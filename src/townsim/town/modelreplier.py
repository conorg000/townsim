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
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from townsim.estimate import Price
from townsim.model import RETRY_SLEEPS, ModelUnavailable, complete, finish_reason, reasoning_text, reply_text, token_count
from townsim.town.families import Family, check_family, input_tokens, worst_case
from townsim.town.replies import Reply, ReplyKey, StopRun

ATTEMPTS = len(RETRY_SLEEPS) + 1   # the attempts `model.complete` makes at one call


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


class ModelReplier:
    """Asks each person's family's model through `completion_fn`.

    `family_of` maps a person's id to a family name; `cost_fn(response)` gives a call's
    dollars (LiteLLM's `completion_cost(completion_response=...)` in live use). A reply's
    recorded cost is the larger of that and the listed price of its tokens, so a cost
    function that under-reports cannot hide spend. `lost(dollars)` is told the worst case of
    every attempt that raised (a provider may bill an attempt the client gave up on), once
    per attempt, on the calling thread after a batch's calls have all finished, in the pairs'
    order. `spent()` gives the dollars already spent on the run: what its stored replies cost
    plus everything `lost` was given. `stopped` says why this replier last stopped a run:
    `cap: ...`, `refused: <model>: ...` or `unavailable: <model>: ...`.

    Raises ValueError if a family sets a parameter `families.check_params` does not allow,
    names an unpriced model or one above the price ceiling, or if `family_of` deals a person
    a family that is not among `families`.
    """

    def __init__(self, families: dict[str, Family], prices: dict[str, Price], family_of: dict[str, str],
                 completion_fn: Callable, cap: float, spent: Callable[[], float], cost_fn: Callable | None = None,
                 workers: int = 8, sleep_fn: Callable[[float], None] = time.sleep,
                 lost: Callable[[float], None] | None = None) -> None:
        for family in families.values():
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
        self._refused = False

    def _family(self, actor: str) -> Family:
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
        if self._refused:
            return [StopRun(self.stopped) for _ in pairs]
        if not pairs:
            return []
        worsts = [worst_case(f, self.prices[f.model], m) for f, (_, m) in zip(fams, pairs)]
        already = float(self.spent())
        worst = ATTEMPTS * sum(worsts)
        if not already + worst <= self.cap:
            self.stopped = (f"cap: ${already:.4f} spent and up to ${worst:.4f} more for {len(pairs)} "
                            f"call{'s' if len(pairs) != 1 else ''} would pass the cap of ${self.cap:.4f}")
            return [StopRun(self.stopped) for _ in pairs]
        failed = [0] * len(pairs)

        def one(i: int) -> Reply | StopRun:
            family, messages = fams[i], pairs[i][1]

            def counted(**kwargs):
                try:
                    return self.completion_fn(**kwargs)
                except BaseException:
                    failed[i] += 1
                    raise
            try:
                return self._call(counted, family, messages)
            except Exception as e:      # noqa: BLE001  (a fault reading one reply)
                return StopRun(f"unavailable: {family.model}: {type(e).__name__}: {e}")

        with ThreadPoolExecutor(max_workers=min(self.workers, len(pairs))) as pool:
            results = list(pool.map(one, range(len(pairs))))
        if self.lost is not None:
            for n, w in zip(failed, worsts):
                for _ in range(n):
                    self.lost(w)
        why = []
        for got in results:
            if isinstance(got, Reply) and got.error and got.error.startswith("refused:"):
                self._refused = True
                why.append(got.error)
            elif isinstance(got, StopRun):
                why.append(str(got))
        if why:
            self.stopped = why[0]
        return results

    def _call(self, completion_fn: Callable, family: Family, messages: list[dict]) -> Reply | StopRun:
        try:
            resp = complete(completion_fn, model="openrouter/" + family.model, messages=messages,
                            sleep_fn=self.sleep_fn, **family.params)
        except ModelUnavailable as e:
            return StopRun(f"unavailable: {family.model}: {e}")
        return self._reply(family, messages, resp)

    def _reply(self, family: Family, messages: list[dict], resp) -> Reply:
        text, reasoning, finish = reply_text(resp), reasoning_text(resp), finish_reason(resp)
        tokens_in = _usage_count(resp, "prompt_tokens")
        tokens_out = _usage_count(resp, "completion_tokens")
        if tokens_in is None:
            tokens_in = input_tokens(messages)
        if tokens_out is None:
            tokens_out = math.ceil((len(text or "") + len(reasoning or "")) / 3)
        price = self.prices[family.model]
        cost = (tokens_in * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1_000_000
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
            error = f"refused: {family.model}: {' '.join(refusal.split())}"
        elif finish == "content_filter" and not (text or "").strip():
            error = f"refused: {family.model}: the provider's content filter gave no reply"
        return Reply(text=text, reasoning=reasoning, finish=finish, tokens_in=tokens_in, tokens_out=tokens_out,
                     cost=cost, error=error)
