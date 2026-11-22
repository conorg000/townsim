"""A day in a fixed order (design 4.1): the routine, then everyone's view built from the
morning's state, then every reply, and only then the acts, person by person in id order."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import aims, firms, hamlet, hours, jobs, mend, obligations, talk
from townsim.town.acts import Act, Outcome, apply_act, clean_text, cut, name_of_who, shown
from townsim.town.clock import dow
from townsim.town.entities import Result
from townsim.town.replies import Keys, Replier, ask_all
from townsim.town.statement import quoted
from townsim.town.store import Store
from townsim.town import views

MAX_ACTS = 8
SCAN_CHARS = 20_000
SCAN_TRIES = 50


@dataclass(frozen=True)
class Turn:
    """One ask and what came of it: a person's turn, or (with `talk`) a turn in that talk."""

    pid: str
    key: str
    invalid: bool
    outcomes: list[Outcome]
    talk: str = ""


@dataclass(frozen=True)
class TalkHeld:
    """A talk held today and the keys of the asks made in it, in order."""

    id: str
    keys: list[str]


@dataclass(frozen=True)
class DayReport:
    """A day's turns: everyone's, then each talk's, in the order asked; the talks held; and
    the instances' turns, after the talks."""

    day: int
    turns: list[Turn]
    talks: list[TalkHeld] = field(default_factory=list)
    seat: list = field(default_factory=list)


def _lapse(store: Store) -> None:
    for rq in store.where("requests", status="open"):
        if rq.expires >= store.day:
            continue
        cause = f"routine:lapse:{rq.id}"
        store.set("requests", rq.id, cause, status="lapsed", closed_on=store.day)
        from townsim.town.ops_core import SENDER_TOLD
        text = SENDER_TOLD[rq.kind](store, rq, "lapsed") if rq.kind in SENDER_TOLD else _lapsed(store, rq)
        obligations.notify(store, rq.sender, text, cause)
        from townsim.town.seat_texts import tell_closed
        tell_closed(store, store.get("requests", rq.id), cause)


def _lapsed(store: Store, rq) -> str:
    """What the sender of a request that has lapsed is told: its words quoted; or, for one code
    made for them with no words of theirs, the first sentence of what code wrote of it."""
    name = name_of_who(store, rq.to)
    if rq.text.strip():
        return f"Your request {rq.id} to {name} ({quoted(cut(rq.text))}) was not answered and has lapsed."
    text = f"Your request {rq.id} to {name} was not answered and has lapsed."
    summary = " ".join(rq.summary.split())
    if summary:
        cut_at = summary.find(". ")
        first = summary if cut_at == -1 else summary[:cut_at + 1]
        text += " " + (first if first.endswith((".", "\u2026")) else first + ".")
    return text


def routine(store: Store) -> None:
    """The morning: today's events; on a Monday, benefit for those whose job has ended, a
    week's free hours and the shops' deliveries; old requests lapse; what is owed is opened,
    aged and collected; then those whose shopping day it is shop. Events come first, so that
    someone who falls ill on a Monday has no hours that week."""
    from townsim.town import controls, investment
    controls.apply_due(store)
    from townsim.town import plans
    plans.apply_due(store)
    investment.review(store)
    hamlet.apply_events(store)
    jobs.pay_benefit(store)
    hours.monday_hours(store)
    firms.deliver(store)
    _lapse(store)
    obligations.open_dues(store)
    obligations.age_arrears(store)
    obligations.collect(store)
    from townsim.town import tasks
    tasks.complete_due(store)
    firms.shop_today(store)


def close(store: Store, since: int | None = None) -> None:
    """The evening: on a Sunday the shops' week closes (and every fourth Sunday the suppliers review their
    trade discounts); with lane B what reached whom in the day's
    changes since the morning (`since`, a mark) is kept (`hearing`); then the day ends."""
    from townsim.town import desks
    desks.review_discounts(store)
    firms.close_week(store)
    if since is not None:
        from townsim.town import hearing
        hearing.note_day(store, store.changes[since:])
        from townsim.town import hours, lane_b
        if lane_b.installed(store):
            hours.note_spent(store, store.changes[since:])
    store.advance_day()


def who_acts(store: Store) -> list[str]:
    """People with something unseen, an open request to them they have not seen or that is
    on its last day, or their stock day today."""
    waiting = {rq.to for rq in store.where("requests", status="open") if not rq.seen or rq.expires == store.day}
    news = views.with_news(store)
    return [p.id for p in store.all("people") if p.id in news or p.id in waiting or p.stock_day == dow(store.day)]


def parse_acts(text: str | None) -> list | None:
    """The `acts` list of the first top-level JSON object in `text` that has one, or None.

    Only the first `SCAN_CHARS` characters are read and at most `SCAN_TRIES` decodes are
    tried. An object without an `acts` list is skipped whole. Never raises."""
    reply = parse_reply(text)
    return None if reply is None else reply["acts"]


def parse_reply(text: str | None) -> dict | None:
    """The first top-level JSON object in `text` that has an `acts` list, or None; read as
    `parse_acts` reads it, and if none can be read, read again with its slips mended where
    that is unambiguous (`mend.mended`). Never raises."""
    if not isinstance(text, str):
        return None
    text = text[:SCAN_CHARS]
    found = _first_reply(text)
    if found is None:
        again = mend.mended(text)
        if again != text:
            found = _first_reply(again)
    return found


def _first_reply(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    at = text.find("{")
    for _ in range(SCAN_TRIES):
        if at == -1:
            return None
        try:
            value, end = decoder.raw_decode(text, at)
        except (ValueError, RecursionError):
            at = text.find("{", at + 1)
            continue
        if isinstance(value, dict) and isinstance(value.get("acts"), list):
            return value
        at = text.find("{", end)
    return None


def act_of(pid: str, element: Any) -> Act:
    """The act a reply's element names, for `pid` acting for themselves."""
    if isinstance(element, dict):
        return Act(pid, pid, element.get("op"), {k: v for k, v in element.items() if k != "op"})
    return Act(pid, pid, None, element)


NO_OP = "(no operation named)"
MORE = "(more)"


def op_written(op: Any) -> str:
    """An act's operation name as its actor wrote it, cut to 40 characters, on one line;
    `(no operation named)` when none was written."""
    written = " ".join((clean_text(shown(op)) or "").split()) if op is not None else ""
    return written or NO_OP


def _tell_result(store: Store, outcome: Outcome) -> None:
    act = outcome.act
    result = Result(store.new_id("res", outcome.cause), store.day, act.actor, op_written(act.op), outcome.msg,
                    outcome.ok)
    store.add("results", result, outcome.cause)


def _tell_more(store: Store, pid: str, k: int, cause: str, dropped: list | None = None) -> None:
    """Tell `pid` that `k` operations past the eighth (`MAX_ACTS`) were not carried out; with lane B, each by
    name (`dropped_text`)."""
    text = f"Not done: {k} more operations were written than the eight carried out in a day."
    from townsim.town import lane_b
    if dropped and lane_b.installed(store):
        text = dropped_text(store, dropped, "being past the eight operations of a day")
    store.add("results", Result(store.new_id("res", cause), store.day, pid, MORE, text, False), cause)


def _first_argument(store: Store, element: Any) -> str:
    """`subscribe to "Tessaly Assistant"`, `message to Angela Russo`: an operation and its first argument (a
    name in the place unquoted, anything else quoted)."""
    if not isinstance(element, dict):
        return "an operation that could not be read"
    op = op_written(element.get("op")) or "an operation"
    rest = [(k, v) for k, v in element.items() if k != "op"]
    if not rest:
        return op
    k, v = rest[0]
    text = " ".join(str(v).split())[:60] if isinstance(v, (str, int, float)) else "…"
    from townsim.town.acts import resolve
    named = isinstance(v, str) and resolve(store, v) is not None
    return f"{op} {k} {text}" if named else f'{op} {k} "{text}"'


def dropped_text(store: Store, dropped: list, why: str) -> str:
    """`Not carried out, being past the eight operations of a day: subscribe to "Tessaly Assistant"; message to
    Angela Russo.`"""
    return f"Not carried out, {why}: " + "; ".join(_first_argument(store, e) for e in dropped) + "."


def guarded_view(store: Store, pid: str, build: Any) -> Any:
    """What `build()` returns; or, if it raises, None, with the store left as it was and an
    engine error kept (`view:<pid>:d<day>: <type>: <message>`): one person's view that cannot
    be built never stops the day."""
    mark = store.mark()
    try:
        return build()
    except Exception as e:          # noqa: BLE001  (any fault in building a view)
        store.undo_to(mark)
        store.engine_errors.append(f"view:{pid}:d{store.day}: {type(e).__name__}: {e}")
        return None


def run_day(store: Store, replier: Replier, keys: Keys) -> DayReport:
    """The routine, then everyone's view, every reply (asked as one batch, `ask_all`), and
    each person's aims and acts in id order; then the day's talks (`talk.todays`, each held in turn, its acts numbered on
    from everyone's); then each due instance's turn (`seat.run_seat`), which alone calls `ask_tools`; then the rules (`rules.run_rules`); then the close. A reply's `aims`, when it has them, are taken before
    its acts. Acts past the eighth are not carried out, and the person is told how many. A
person whose view cannot be built is not asked that day (`guarded_view`)."""
    morning = store.mark()
    routine(store)
    from townsim.town import desks
    desks.answer_day(store, replier, keys)
    day = store.day
    shown = []
    for pid in who_acts(store):
        built = guarded_view(store, pid, lambda: views.view(store, pid))
        if built is not None:
            shown.append((pid, *built))
    pairs = [(keys.next(replier.model_for(pid), day, pid, "turn"), messages) for pid, messages, _ in shown]
    asked = [(key.text(), reply) for (key, _), reply in zip(pairs, ask_all(replier, pairs))]
    turns = []
    n = 0
    for (pid, _, ids), (key, reply) in zip(shown, asked):
        parsed = parse_reply(reply.text)
        if parsed is None:
            turns.append(Turn(pid, key, True, []))
            continue
        elements = parsed["acts"]
        if "aims" in parsed:
            aims.update(store, pid, parsed["aims"], f"op:{pid}:aims:d{day}.{n}")
            n += 1
        outcomes = []
        for element in elements[:MAX_ACTS]:
            outcome = apply_act(store, act_of(pid, element), n)
            _tell_result(store, outcome)
            outcomes.append(outcome)
            n += 1
        if len(elements) > MAX_ACTS:
            _tell_more(store, pid, len(elements) - MAX_ACTS, f"op:{pid}:more:d{day}.{n}", elements[MAX_ACTS:])
            n += 1
        views.mark_seen(store, pid, ids, f"routine:seen:{pid}")
        turns.append(Turn(pid, key, False, outcomes))
    held, called = [], []
    for ref in talk.todays(store):
        h = talk.hold(store, replier, keys, ref, n)
        n = h.n
        turns += h.turns
        called += h.seat
        if h.talk:
            held.append(TalkHeld(h.talk, [t.key for t in h.turns]))
    from townsim.town import seat              # the seat reads the operations' modules, which read this one
    seated, n = seat.run_seat(store, replier, keys, n)
    for st in seated:
        turns += st.talk_turns
    seated = called + seated                   # a call's turns came with its talk
    from townsim.town import rules, tasks
    tasks.narrate_day(store, replier, keys)
    n = rules.run_rules(store, n)
    close(store, morning)
    return DayReport(day, turns, held, seated)
