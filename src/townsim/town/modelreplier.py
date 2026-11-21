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
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from townsim.estimate import Price
from townsim.model import ModelUnavailable, complete, finish_reason, reasoning_text, reply_text, token_count
from townsim.town.families import Family, check_price, input_tokens, worst_case
from townsim.town.replies import Reply, ReplyKey, StopRun

DEADLINE = 300.0   # seconds a call may take, retries included, before it counts as no answer
_POLL = 0.05


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

    `family_of` maps a person's id to a family name; `spent()` gives the dollars already
    stored for the run; `cost_fn(response)` gives a call's dollars (LiteLLM's
    `completion_cost(completion_response=...)` in live use), the listed prices standing in
    when it raises or gives no positive number. `stopped` says why this replier last
    stopped a run: `cap: ...`, `refused: <model>: ...` or `unavailable: <model>: ...`.
    """

    def __init__(self, families: dict[str, Family], prices: dict[str, Price], family_of: dict[str, str],
                 completion_fn: Callable, cap: float, spent: Callable[[], float], cost_fn: Callable | None = None,
                 workers: int = 8, sleep_fn: Callable[[float], None] = time.sleep, deadline: float = DEADLINE) -> None:
        for name, family in families.items():
            if family.model not in prices:
                raise ValueError(f"family {name!r}: {family.model} has no price")
            check_price(name, family.model, prices[family.model])
        self.families = families
        self.prices = prices
        self.family_of = family_of
        self.completion_fn = completion_fn
        self.cap = cap
        self.spent = spent
        self.cost_fn = cost_fn
        self.workers = max(1, int(workers))
        self.sleep_fn = sleep_fn
        self.deadline = deadline
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
        made or gave no answer. Never raises for one call's failure."""
        fams = [self._family(key.actor) for key, _ in pairs]
        if self._refused:
            return [StopRun(self.stopped) for _ in pairs]
        if not pairs:
            return []
        already = float(self.spent())
        worst = sum(worst_case(f, self.prices[f.model], m) for f, (_, m) in zip(fams, pairs))
        if not already + worst <= self.cap:
            self.stopped = (f"cap: ${already:.4f} spent and up to ${worst:.4f} more for {len(pairs)} "
                            f"call{'s' if len(pairs) != 1 else ''} would pass the cap of ${self.cap:.4f}")
            return [StopRun(self.stopped) for _ in pairs]
        results = self._send([(f, m) for f, (_, m) in zip(fams, pairs)])
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

    def _send(self, calls: list[tuple[Family, list[dict]]]) -> list[Reply | StopRun]:
        results: list[Reply | StopRun | None] = [None] * len(calls)
        started: dict[int, float] = {}

        def one(i: int) -> Reply | StopRun:
            started[i] = time.monotonic()
            family, messages = calls[i]
            return self._call(family, messages)

        pool = ThreadPoolExecutor(max_workers=min(self.workers, len(calls)))
        try:
            futures = {pool.submit(one, i): i for i in range(len(calls))}
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=_POLL, return_when=FIRST_COMPLETED)
                for f in done:
                    i = futures[f]
                    try:
                        results[i] = f.result()
                    except Exception as e:      # noqa: BLE001  (a fault reading one reply)
                        results[i] = StopRun(f"unavailable: {calls[i][0].model}: {type(e).__name__}: {e}")
                now = time.monotonic()
                for f in list(pending):
                    i = futures[f]
                    if i in started and now - started[i] > self.deadline:
                        pending.discard(f)
                        results[i] = StopRun(f"unavailable: {calls[i][0].model}: no answer in {self.deadline:g}s")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return results   # type: ignore[return-value]

    def _call(self, family: Family, messages: list[dict]) -> Reply | StopRun:
        try:
            resp = complete(self.completion_fn, model="openrouter/" + family.model, messages=messages,
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
            if isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c > 0:
                cost = float(c)
        error = None
        refusal = _refusal(resp)
        if refusal is not None:
            error = f"refused: {family.model}: {' '.join(refusal.split())}"
        elif finish == "content_filter" and not (text or "").strip():
            error = f"refused: {family.model}: the provider's content filter gave no reply"
        return Reply(text=text, reasoning=reasoning, finish=finish, tokens_in=tokens_in, tokens_out=tokens_out,
                     cost=cost, error=error)
