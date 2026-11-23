"""A `Replier` played by language models, one family per person, behind a spending cap.

The messages a person is sent go to their family's model exactly as given, with the family's
parameters and nothing else. Before any call or batch, the spend already stored for the run
plus the worst case of what is about to be sent must not pass the cap; if it would, nothing
is sent and the run stops. A model that gives no answer after its retries stops the run and
yields no reply. A refused prompt yields a reply (its `error` begins `refused:`), which the
caller stores; after it, this replier sends nothing more.

The completion function is an argument (LiteLLM's `completion` in live use), so nothing here
imports a provider's client or reads a key.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from townsim.estimate import Price
from townsim.model import (RETRY_SLEEPS, ModelUnavailable, assistant_message, complete, finish_reason,
                           reasoning_text, reply_text, token_count, tool_calls)
from townsim.town.families import (TOKENS_PER_MESSAGE, Family, check_family, input_tokens, seat_input_tokens,
                                   seat_worst_case, worst_case)
from townsim.town.replies import Reply, ReplyKey, StopRun, is_instance_id

ATTEMPTS = len(RETRY_SLEEPS) + 1
OUTSIDE_ROLES = ("lifter", "narrator", "web", "desk")   # the attempts `model.complete` makes at one call


SAFE_CHARS = 300    # the most of a provider's error text that is kept or shown
_SECRET = [
    (re.compile(r"sk-[\w-]{8,}", re.I), "[removed]"),
    (re.compile(r"Bearer\s+\S+", re.I), "[removed]"),
    (re.compile(r"(Authorization:|api_key=|x-api-key:|api-key:)\s*\S+", re.I), r"\1 [removed]"),
]


def safe(text: str) -> str:
    """Text from a provider or its client, fit to store or show: anything shaped like a key
    or a credential header's value is `[removed]`, as is the value of any environment variable
    named `*_API_KEY` or `*_TOKEN` (of 8 or more characters) wherever it appears; whitespace
    collapsed; cut to `SAFE_CHARS` characters."""
    text = str(text)
    for name, value in os.environ.items():
        if name.endswith(("_API_KEY", "_TOKEN")) and len(value) >= 8:
            text = text.replace(value, "[removed]")
    for pattern, by in _SECRET:
        text = pattern.sub(by, text)
    text = " ".join(text.split())
    return text if len(text) <= SAFE_CHARS else text[:SAFE_CHARS - 1] + "…"


def _usage_count(resp, name: str) -> int | None:
    try:
        usage = getattr(resp, "usage", None)
    except Exception:       # noqa: BLE001  (an odd reply object)
        return None
    if usage is None:
        return None
    n = token_count(usage, name)
    return n if n is not None and n >= 0 else None


def _refusal(resp) -> str | None:
    """The provider's refusal text on the reply's message, when it gave a non-empty one."""
    try:
        value = getattr(resp.choices[0].message, "refusal", None)
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    return value if isinstance(value, str) and value.strip() else None


OVERRUN = 2.0       # a reply that cost more than this many times its worst case stops the run


class ModelReplier:
    """Asks each person's family's model through `completion_fn`.

    `family_of` maps a person's id to a family name; `cost_fn(response)` gives a call's
    dollars (LiteLLM's `completion_cost(completion_response=...)` in live use). A reply's
    recorded cost is the larger of that and the listed price of its tokens, each count taken
    as at least what can be seen (the messages' characters / 3 plus 4 a message in, the
    reply's text and reasoning characters / 3 out), so neither a cost function nor usage that
    under-reports can hide spend; the reply's `tokens_in`/`tokens_out` keep what the provider
    reported (0 when it reported nothing). `lost(dollars)` is told the worst case of
    every attempt that raised (a provider may bill an attempt the client gave up on), once
    per attempt, on the calling thread after a batch's calls have all finished, in the pairs'
    order; a raising `lost` stops this replier (`unavailable: lost-spend callback failed`)
    after the batch is returned whole. An answer that cannot be read counts as one lost
    attempt. `spent()` gives the dollars already spent on the run: what its stored replies cost
    plus everything `lost` was given. A reply that cost more than the worst case of its attempt is
    kept and counted as any is; one that cost more than twice it (`OVERRUN`) stops this replier once
    its batch is returned (`cap: a reply cost ...`), since the cap's reckoning no longer holds; a
    reply billed a little over its worst case (rounding, a provider's own count) does not. Text from the provider or its client is passed through `safe`
    before it is kept in an error or `stopped`. `stopped` says why this replier last stopped a run:
    `cap: ...`, `refused: <model>: ...` or `unavailable: <model>: ...`.

    Raises ValueError if a family sets a parameter `families.check_params` does not allow,
    names an unpriced model or one above the price ceiling, or if `family_of` deals a person
    a family that is not among `families`.
    """

    def __init__(self, families: dict[str, Family], prices: dict[str, Price], family_of: dict[str, str],
                 completion_fn: Callable, cap: float, spent: Callable[[], float], cost_fn: Callable | None = None,
                 workers: int = 8, sleep_fn: Callable[[float], None] = time.sleep,
                 lost: Callable[[float], None] | None = None, seat: Family | None = None,
                 outside: dict[str, Family] | None = None) -> None:
        families = {n: Family(f.name, f.model, f.params) for n, f in families.items()}   # read-only copies
        for family in families.values():
            check_family(family, prices)
        if seat is not None:
            seat = Family(seat.name, seat.model, seat.params)
            check_family(seat, prices)
        self.seat = seat
        self.outside = {n: Family(f.name, f.model, f.params) for n, f in (outside or {}).items()}
        for family in self.outside.values():
            check_family(family, prices)
        for person, name in family_of.items():
            if name not in families:
                raise ValueError(f"person {person!r} is dealt family {name!r}, which is not among the families loaded")
        self.families = families
        self.prices = prices
        self.family_of = family_of
        self.completion_fn = completion_fn
        self.cap = cap
        self.spent = spent
        self.cost_fn = cost_fn
        self.workers = max(1, int(workers))
        self.sleep_fn = sleep_fn
        self.lost = lost
        self.stopped: str | None = None
        self._halted = False     # a refusal, an overbilled reply or a failed lost-spend callback: nothing more is sent

    def _family(self, actor: str) -> Family:
        """The family that plays `actor`: a person's; the seat for an instance; the outside families
        for the lifter, the narrator, the web's writer and each outside desk (`desk-<org>`)."""
        role = "desk" if isinstance(actor, str) and actor.startswith("desk-") else actor
        if role in OUTSIDE_ROLES:
            if role not in self.outside:
                raise StopRun(f"unavailable: this replier has no family for {role}")
            return self.outside[role]
        if is_instance_id(actor) and actor not in self.family_of:
            if self.seat is None:
                raise ValueError(f"{actor} is an instance and this replier has no seat")
            return self.seat
        return self.families[self.family_of[actor]]

    def model_for(self, actor: str) -> str:
        return self._family(actor).model

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply:
        [got] = self.ask_many([(key, messages)])
        if isinstance(got, StopRun):
            raise got
        return got

    def ask_many(self, pairs: list[tuple[ReplyKey, list[dict]]]) -> list[Reply | StopRun]:
        """One element per pair, in order: a `Reply`, or a `StopRun` for a call that was not
        made or gave no answer. Never raises for one call's failure.

        Before anything is sent, the spend so far plus the worst case of every attempt that
        may be made (`ATTEMPTS` per call) must not pass the cap, or nothing is sent. The
        batch waits for all its calls (each bounded by `model.complete`'s timeout and
        retries). Replies are stored by the caller only once the batch returns, so a process
        killed mid-batch may lose up to one batch of replies that were paid for; they are
        asked again on resume."""
        fams = [self._family(key.actor) for key, _ in pairs]
        if self._halted:
            return [StopRun(self.stopped) for _ in pairs]
        if not pairs:
            return []
        worsts = [worst_case(f, self.prices[f.model], m) for f, (_, m) in zip(fams, pairs)]
        calls = [lambda counted, f=f, m=m: self._call(counted, f, m) for f, (_, m) in zip(fams, pairs)]
        return self._run(fams, worsts, calls)

    def ask_tools(self, key: ReplyKey, messages: list[dict], tools: list[dict]) -> Reply:
        """One call with tools to the seat's model, behind the cap as any call is, its worst case
        counting the tools and everything on the messages (`families.seat_worst_case`). It sends
        the tools with `tool_choice="auto"` and `parallel_tool_calls=False`, and the seat's
        parameters. Raises `StopRun` when the call is not made or gives no answer."""
        if self.seat is None:
            raise StopRun("unavailable: this replier has no seat")
        seat = self.seat
        if self._halted:
            raise StopRun(self.stopped)
        worst = seat_worst_case(seat, self.prices[seat.model], messages, tools)
        [got] = self._run([seat], [worst], [lambda counted: self._call_tools(counted, seat, messages, tools)])
        if isinstance(got, StopRun):
            raise got
        return got

    def _run(self, fams: list[Family], worsts: list[float], calls: list[Callable]) -> list[Reply | StopRun]:
        already = float(self.spent())
        worst = ATTEMPTS * sum(worsts)
        if not already + worst <= self.cap:
            self.stopped = (f"cap: ${already:.4f} spent and up to ${worst:.4f} more for {len(calls)} "
                            f"call{'s' if len(calls) != 1 else ''} would pass the cap of ${self.cap:.4f}")
            return [StopRun(self.stopped) for _ in calls]
        failed = [0] * len(calls)

        def one(i: int) -> Reply | StopRun:
            family = fams[i]

            def counted(**kwargs):
                try:
                    return self.completion_fn(**kwargs)
                except BaseException:
                    failed[i] += 1
                    raise
            try:
                return calls[i](counted)
            except Exception as e:      # noqa: BLE001  (an answer that could not be read)
                failed[i] += 1          # it was answered, so perhaps billed: one attempt lost
                return StopRun(safe(f"unavailable: {family.model}: {type(e).__name__}: {e}"))

        with ThreadPoolExecutor(max_workers=min(self.workers, len(calls))) as pool:
            results = list(pool.map(one, range(len(calls))))
        why = []
        for got in results:
            if isinstance(got, Reply) and got.error and got.error.startswith("refused:"):
                self._halted = True
                why.append(got.error)
            elif isinstance(got, StopRun):
                why.append(str(got))
        over = [(got.cost, w, f.model) for got, w, f in zip(results, worsts, fams)
                if isinstance(got, Reply) and got.cost > OVERRUN * w]
        if over:
            self._halted = True
            why.append("cap: a reply cost ${:.4f}, more than its worst case ${:.4f} ({})".format(*over[0]))
        if why:
            self.stopped = why[0]
        if self.lost is not None:
            broken = None
            for n, w in zip(failed, worsts):
                for _ in range(n):
                    try:
                        self.lost(w)
                    except Exception as e:      # noqa: BLE001  (the run cannot keep its spend)
                        broken = broken or safe(f"unavailable: lost-spend callback failed: {type(e).__name__}: {e}")
            if broken is not None:
                self._halted = True
                self.stopped = broken
        return results

    def _call_tools(self, completion_fn: Callable, family: Family, messages: list[dict],
                    tools: list[dict]) -> Reply | StopRun:
        try:
            resp = complete(completion_fn, model="openrouter/" + family.model, messages=messages, sleep_fn=self.sleep_fn,
                            tools=tools, tool_choice="auto", parallel_tool_calls=False, **family.params)
        except ModelUnavailable as e:
            return StopRun(safe(f"unavailable: {family.model}: {e}"))
        return self._reply(family, messages, resp, tools)

    def _call(self, completion_fn: Callable, family: Family, messages: list[dict]) -> Reply | StopRun:
        try:
            resp = complete(completion_fn, model="openrouter/" + family.model, messages=messages,
                            sleep_fn=self.sleep_fn, **family.params)
        except ModelUnavailable as e:
            return StopRun(safe(f"unavailable: {family.model}: {e}"))
        return self._reply(family, messages, resp)

    def _reply(self, family: Family, messages: list[dict], resp, tools: list[dict] | None = None) -> Reply:
        text, reasoning, finish = reply_text(resp), reasoning_text(resp), finish_reason(resp)
        tokens_in = _usage_count(resp, "prompt_tokens") or 0      # as the provider reported them
        tokens_out = _usage_count(resp, "completion_tokens") or 0
        # the cost counts at least what can be seen, whatever the provider reported
        calls = tool_calls(resp) if tools is not None else []
        if tools is None:
            seen_in = input_tokens(messages) + TOKENS_PER_MESSAGE * len(messages)
        else:
            seen_in = seat_input_tokens(messages, tools)
        called = sum(len(c["name"]) + len(c["arguments"]) for c in calls)
        seen_out = math.ceil((len(text or "") + len(reasoning or "") + called) / 3)
        price = self.prices[family.model]
        cost = (max(tokens_in, seen_in) * price.input_per_mtok
                + max(tokens_out, seen_out) * price.output_per_mtok) / 1_000_000
        if self.cost_fn is not None:
            try:
                c = self.cost_fn(resp)
            except Exception:       # noqa: BLE001  (no price known to the cost function)
                c = None
            if isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c):
                cost = max(cost, float(c))
        error = None
        refusal = _refusal(resp)
        if refusal is not None:
            error = safe(f"refused: {family.model}: {refusal}")
        elif finish == "content_filter" and not (text or "").strip():
            error = f"refused: {family.model}: the provider's content filter gave no reply"
        tool = None
        if tools is not None:
            tool = {"calls": calls, "message": assistant_message(resp)} if calls else None
        return Reply(text=text, tool=tool, reasoning=reasoning, finish=finish, tokens_in=tokens_in,
                     tokens_out=tokens_out, cost=cost, error=error)
