"""A probe of the model families: each family is shown the same few views from a scripted
hamlet, a few times over, and what it answers is scored, without running the town on it.

`cases` runs a seed's hamlet on its scripted life and keeps four views chosen by code, each
with a copy of the store as it was when the view was built. `run` asks every family each case
`n` times through a `ModelReplier` behind a spending cap, and scores each reply: whether it
could be read, how many acts it wrote, and how many of those the engine carried out when
applied to a fresh copy of the case's store (the case's own copy is never touched). `table`
sums the rows up a family at a line.

The messages are exactly the views (`views.view`, `talk.view`); nothing is added. The
completion function is an argument; nothing here imports a provider's client or reads a key.
"""

from __future__ import annotations

import copy
import pickle
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from townsim.estimate import Price
from townsim.town import gen, hamlet_script, loop, talk
from townsim.town.acts import apply_act
from townsim.town.clock import dow
from townsim.town.families import Family
from townsim.town.modelreplier import ModelReplier
from townsim.town.obligations import upcoming
from townsim.town.replies import Keys, Reply, ReplyKey, StopRun, refused
from townsim.town.store import Store

MAX_CAP = 1.0      # dollars a probe may be capped at, at most
DAYS = 10          # scripted days before the cases are chosen
TALK_AFTER = 3     # the talk case is the first talk held after this day
TALK_DAYS = 28     # days run at most looking for a talk
DUE_AHEAD = 3      # days ahead a payment counts as due for the case short of money


@dataclass
class Case:
    label: str
    pid: str
    kind: Literal["turn", "talk"]
    messages: list[dict]
    store: Store
    day: int = 0
    ref: str = ""


@dataclass
class Row:
    family: str
    label: str
    got_text: bool
    readable: bool
    acts: int
    acts_ok: int
    refusals: list[str]
    provider_refused: bool
    tokens_in: int
    tokens_out: int
    cost: float
    seconds: float
    text: str | None = None
    reasoning: str | None = None


class ProbeStopped(StopRun):
    """The probe stopped (the cap, a refusal, a model with no answer); `rows` holds the rows
    made before it stopped, the stopping reply's included."""

    def __init__(self, why: str, rows: list[Row]) -> None:
        super().__init__(why)
        self.rows = rows


# --- the cases ---------------------------------------------------------------------------


@dataclass
class _Seen:
    day: int
    pid: str
    purpose: str
    ref: str
    turn: int
    messages: list[dict]
    store: bytes


def _shortfall(store: Store, pid: str) -> int:
    """Cents by which what the person's household owes now or within `DUE_AHEAD` days passes
    its balance (0 or less when covered)."""
    h = store.get("people", pid).household
    out = sum(u.cents for u in upcoming(store, h, DUE_AHEAD) if u.out)
    return out - store.balance(h) if out else -1


def _scripted(seed: int, family_names: list[str]) -> tuple[list[_Seen], int]:
    """Every ask of the seed's hamlet on its script for `DAYS` days, and on until a talk is
    held after day `TALK_AFTER` (at most `TALK_DAYS` days); with the days run."""
    s = gen.generate(seed, list(family_names))
    seen: list[_Seen] = []
    talk_turns: dict[str, int] = {}

    def script(key: ReplyKey, messages: list[dict]) -> str:
        turn = 0
        if key.purpose == "talk":
            turn = talk_turns.get(key.ref, 0)
            talk_turns[key.ref] = turn + 1
        seen.append(_Seen(key.day, key.actor, key.purpose, key.ref, turn, copy.deepcopy(messages),
                          pickle.dumps(s)))
        return hamlet_script.script(key, messages)

    from townsim.town.replies import ScriptedReplier
    replier, keys = ScriptedReplier(script), Keys("probe")
    days = 0
    while days < DAYS or (days < TALK_DAYS and not any(
            x.purpose == "talk" and x.day > TALK_AFTER and x.turn == 1 for x in seen)):
        loop.run_day(s, replier, keys)
        days += 1
    return seen, days


def cases(seed: int, family_names: list[str]) -> list[Case]:
    """Four views from the seed's hamlet run on its script: a shop owner on their stock day;
    a person whose household owes, now or within three days, more than its balance (else the
    person with the lowest balance); a person with an open request to answer; and the first
    talk held after day 3, at its second turn. The first three are from the first ten days,
    the latest that fits, and go to different people where they can; the talk is looked for
    up to day 28. Raises ValueError if a case cannot be found."""
    seen, _ = _scripted(seed, family_names)
    turns = [x for x in seen if x.purpose == "turn" and x.day < DAYS]
    chosen: list[tuple[str, _Seen]] = []

    def take(label: str, fits: list[_Seen], key=None) -> None:
        if not fits:
            raise ValueError(f"seed {seed}: no view fits the case {label!r}")
        used = {x.pid for _, x in chosen}
        fresh = [x for x in fits if x.pid not in used] or fits
        chosen.append((label, max(fresh, key=key) if key else fresh[-1]))

    def shop_owner(x: _Seen) -> bool:
        st = pickle.loads(x.store)
        return (st.get("people", x.pid).stock_day == dow(x.day)
                and any(o.kind == "shop" and o.owner == x.pid for o in st.all("orgs")))

    take("stock day", [x for x in turns if shop_owner(x)])
    short = [(x, _shortfall(pickle.loads(x.store), x.pid)) for x in turns]
    if any(f > 0 for _, f in short):
        take("short of money", [x for x, f in short if f > 0], key=lambda x: (_shortfall(pickle.loads(x.store), x.pid), x.day))
    else:
        def balance(x: _Seen) -> int:
            st = pickle.loads(x.store)
            return st.balance(st.get("people", x.pid).household)
        take("short of money", turns, key=lambda x: (-balance(x), x.day))
    take("request to answer", [x for x in turns if any(
        rq.to == x.pid for rq in pickle.loads(x.store).where("requests", status="open"))])
    talks = [x for x in seen if x.purpose == "talk" and x.day > TALK_AFTER and x.turn == 1]
    if not talks:
        raise ValueError(f"seed {seed}: no talk held after day {TALK_AFTER} in {TALK_DAYS} days")
    chosen.append(("talk", talks[0]))
    return [Case(label, x.pid, "talk" if x.purpose == "talk" else "turn", x.messages, pickle.loads(x.store),
                 x.day, x.ref) for label, x in chosen]


# --- asking and scoring ------------------------------------------------------------------


def score(case: Case, text: str | None) -> tuple[bool, int, int, list[str]]:
    """Whether `text` can be read as a reply to the case, how many acts it wrote, how many of
    those the engine carried out, and the messages of those it refused, in order, applied to
    a fresh copy of the case's store as a day applies them (at most eight acts a turn, four in
    a talk; one past the limit counts as not carried out)."""
    if case.kind == "talk":
        parsed = talk.parse_say(text)
        most = talk.MAX_ACTS
    else:
        parsed = loop.parse_reply(text)
        most = loop.MAX_ACTS
    if parsed is None:
        return False, 0, 0, []
    elements = parsed["acts"]
    store = pickle.loads(pickle.dumps(case.store))
    ok, refusals = 0, []
    for n, element in enumerate(elements[:most]):
        try:
            outcome = (talk._apply(store, case.pid, element, n) if case.kind == "talk"
                       else apply_act(store, loop.act_of(case.pid, element), n))
        except Exception as e:      # noqa: BLE001  (an engine fault is a refusal here)
            refusals.append(f"engine fault: {type(e).__name__}: {e}")
            continue
        if outcome.ok:
            ok += 1
        else:
            refusals.append(outcome.msg)
    return True, len(elements), ok, refusals


def run(cases: list[Case], families: dict[str, Family], prices: dict[str, Price], completion_fn: Callable,
        cap: float, n: int = 3, cost_fn: Callable | None = None,
        sleep_fn: Callable[[float], None] = time.sleep) -> list[Row]:
    """One row per family x case x repeat, family by family in the families' order, each
    family's calls asked as one batch. Spend for the cap is the rows' costs so far plus what
    failed attempts lost. Raises `ProbeStopped` (a `StopRun`) at the cap, on a provider's
    refusal or when a model gives no answer, carrying the rows so far."""
    if not (isinstance(cap, (int, float)) and 0 < cap <= MAX_CAP):
        raise ValueError(f"the cap must be above $0 and at most ${MAX_CAP:g}, got {cap!r}")
    rows: list[Row] = []
    lost: list[float] = []
    seconds: dict[int, float] = {}
    lock = threading.Lock()

    def timed(**kwargs):
        t = time.monotonic()
        try:
            return completion_fn(**kwargs)
        finally:
            with lock:
                seconds[id(kwargs["messages"])] = seconds.get(id(kwargs["messages"]), 0.0) + time.monotonic() - t

    def spent() -> float:
        return sum(r.cost for r in rows) + sum(lost)

    for name, family in families.items():
        replier = ModelReplier({name: family}, prices, {c.pid: name for c in cases}, timed, cap=cap, spent=spent,
                               cost_fn=cost_fn, sleep_fn=sleep_fn, lost=lost.append)
        asked = [(c, ReplyKey("probe", family.model, c.day, c.pid, c.kind, c.ref, i), list(c.messages))
                 for c in cases for i in range(n)]
        got = replier.ask_many([(k, m) for _, k, m in asked])
        stop = None
        for (c, _, messages), reply in zip(asked, got):
            if isinstance(reply, StopRun):
                stop = stop or str(reply)
                continue
            rows.append(_row(name, c, reply, seconds.get(id(messages), 0.0)))
            if refused(reply):
                stop = stop or reply.error
        if stop is not None:
            raise ProbeStopped(replier.stopped or stop, rows)
    return rows


def _row(family: str, case: Case, reply: Reply, seconds: float) -> Row:
    readable, acts, ok, refusals = score(case, reply.text)
    return Row(family, case.label, bool(isinstance(reply.text, str) and reply.text.strip()), readable, acts, ok,
               refusals, refused(reply), reply.tokens_in, reply.tokens_out, reply.cost, round(seconds, 3),
               reply.text, reply.reasoning)


# --- the table -------------------------------------------------------------------------------


def _pct(a: float, b: float) -> str:
    return f"{100 * a / b:.0f}%" if b else "-"


def _cut(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def table(rows: list[Row]) -> str:
    """Per family, in the order the rows have them: calls, readable %, acts per reply, acts
    carried out %, provider refusals, mean tokens in and out, dollars and mean seconds; then
    the three commonest messages of acts the engine refused."""
    order = list(dict.fromkeys(r.family for r in rows))
    head = f"{'family':<12} {'calls':>5} {'readable':>8} {'acts/reply':>10} {'acts done':>9} {'refused':>7} " \
           f"{'tok in':>7} {'tok out':>7} {'dollars':>9} {'secs':>6}"
    out = [head]
    for name in order:
        rs = [r for r in rows if r.family == name]
        k = len(rs)
        acts, ok = sum(r.acts for r in rs), sum(r.acts_ok for r in rs)
        out.append(f"{name:<12} {k:>5} {_pct(sum(r.readable for r in rs), k):>8} {acts / k:>10.1f} "
                   f"{_pct(ok, acts):>9} {sum(r.provider_refused for r in rs):>7} "
                   f"{sum(r.tokens_in for r in rs) / k:>7.0f} {sum(r.tokens_out for r in rs) / k:>7.0f} "
                   f"{sum(r.cost for r in rs):>9.4f} {sum(r.seconds for r in rs) / k:>6.1f}")
        for msg, times in Counter(m for r in rs for m in r.refusals).most_common(3):
            out.append(f"    {times} x {_cut(msg)}")
    return "\n".join(out)
