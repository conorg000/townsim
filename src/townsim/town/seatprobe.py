"""The seat probe: each drawn instance's first turn, run `n` times on the seat's model.

Seed 1 (or another) is played for a week on the hamlet's script, the product launched and the
drawn arm's clients made in memory, all without a model; then each drawn instance's first turn
is run on a fresh copy of that morning, `n` times, its people's words in a talk scripted. Each
row says how the turn went: calls, tools used, unreadable arguments and unknown tools, tries
beyond the connections, a provider's refusal, whether reasoning came back and how long, whether
a second call followed a first (the reasoning sent back), the finish reasons, and the cost.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable
from dataclasses import dataclass, field

from townsim.estimate import Price
from townsim.town import arms, gen, hamlet_script, product, seat
from townsim.town.families import Family
from townsim.town.modelreplier import ModelReplier, safe
from townsim.town.replies import Both, Keys, Reply, ScriptedReplier, StopRun, refused

MAX_CAP = 1.0
DAYS = 7


@dataclass
class Row:
    instance: str
    k: int
    calls: int = 0
    tools: list[str] = field(default_factory=list)
    unreadable: int = 0
    unknown: int = 0
    beyond: int = 0
    refused: bool = False
    reasoning: bool = False
    reasoning_chars: int = 0
    second_ok: bool | None = None
    finishes: list[str] = field(default_factory=list)
    cost: float = 0.0
    ended: str = ""
    error: str = ""


class ProbeStopped(Exception):
    def __init__(self, why: str, rows: list[Row]) -> None:
        super().__init__(why)
        self.rows = rows


def morning(seed: int = 1, families: list[str] | None = None):
    """The drawn arm's launch morning, made in memory with no model."""
    from townsim.town.loop import run_day
    s = gen.generate(seed, families or ["a", "b", "c", "d"])
    keys = Keys("probe")
    for _ in range(DAYS):
        run_day(s, ScriptedReplier(hamlet_script.script), keys)
    product.launch(s)
    arms.draw_clients(s)
    return s


class _Tap:
    """The seat's replier, keeping every reply it gives."""

    def __init__(self, inner, spent: list[float]) -> None:
        self.inner = inner
        self.spent = spent
        self.replies: list[Reply] = []

    def model_for(self, actor: str) -> str:
        return self.inner.model_for(actor)

    def ask_tools(self, key, messages, tools) -> Reply:
        reply = self.inner.ask_tools(key, messages, tools)
        self.replies.append(reply)
        self.spent[0] += reply.cost             # the cap sees each call's spend before the next
        if refused(reply):
            raise StopRun(reply.error)
        return reply


def run(store, seat_family: Family, prices: dict[str, Price], completion_fn: Callable, cap: float, n: int = 2,
        cost_fn: Callable | None = None, sleep_fn: Callable[[float], None] | None = None) -> list[Row]:
    """A row for each drawn instance's first turn, `n` times each. Raises `ProbeStopped` (with the
    rows so far) when the cap or a refusal stops it."""
    spent = [0.0]
    extra = {} if sleep_fn is None else {"sleep_fn": sleep_fn}
    model = ModelReplier({}, prices, {}, completion_fn, cap=cap, spent=lambda: spent[0], cost_fn=cost_fn,
                         lost=lambda d: spent.__setitem__(0, spent[0] + d), seat=seat_family, **extra)
    frozen = pickle.dumps(store)
    rows: list[Row] = []
    for inst in [i.id for i in store.all("instances")]:
        for k in range(n):
            s = pickle.loads(frozen)
            tap = _Tap(model, spent)
            replier = Both(ScriptedReplier(hamlet_script.script), tap)
            row = Row(inst, k)
            try:
                t, _ = seat.run_turn(s, replier, Keys(f"probe{k}"), inst, 0)
                row.ended = t.ended
            except StopRun as e:
                row.error = safe(str(e))
                row.refused = any(refused(r) for r in tap.replies)
                _fill(row, tap.replies, None)
                rows.append(row)
                raise ProbeStopped(row.error, rows) from None
            _fill(row, tap.replies, t)
            rows.append(row)
    return rows


def _fill(row: Row, replies: list[Reply], turn) -> None:
    row.cost = sum(r.cost for r in replies)
    row.finishes = [r.finish or "" for r in replies]
    row.reasoning_chars = sum(len(r.reasoning or "") for r in replies)
    row.reasoning = row.reasoning_chars > 0 or any(
        any(k in ((r.tool or {}).get("message") or {}) for k in ("reasoning_details", "thinking_blocks"))
        for r in replies)
    if turn is not None:
        row.calls = len(turn.calls)
        row.tools = [c.tool for c in turn.calls]
        row.unreadable = sum(c.kind == "unreadable" for c in turn.calls)
        row.unknown = sum(c.kind == "unknown" for c in turn.calls)
        row.beyond = sum(c.kind == "beyond" for c in turn.calls)
    if len(replies) >= 2 and (replies[0].tool or {}).get("calls"):
        row.second_ok = replies[1].error is None


def table(rows: list[Row]) -> str:
    lines = ["instance  k  calls  unread  unknown  beyond  reasoning  2nd   ended     cost     tools"]
    for r in rows:
        lines.append(f"{r.instance:<9} {r.k:<2} {r.calls:<6} {r.unreadable:<7} {r.unknown:<8} {r.beyond:<7} "
                     f"{r.reasoning_chars:<10} {str(r.second_ok):<5} {r.ended or r.error[:8]:<9} ${r.cost:.4f}  "
                     f"{', '.join(r.tools)}")
    return "\n".join(lines)
