"""A day in a fixed order (design 4.1): the routine, then everyone's view built from the
morning's state, then every reply, and only then the acts, person by person in id order."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import aims, firms, hamlet, hours, obligations
from townsim.town.acts import Act, Outcome, apply_act, clean_text, cut, name_of_who, shown
from townsim.town.clock import dow
from townsim.town.entities import Notice, Result
from townsim.town.replies import Keys, Replier
from townsim.town.store import Store
from townsim.town.views import mark_seen, view, with_news

MAX_ACTS = 8
SCAN_CHARS = 20_000
SCAN_TRIES = 50


@dataclass(frozen=True)
class Turn:
    pid: str
    key: str
    invalid: bool
    outcomes: list[Outcome]


@dataclass(frozen=True)
class DayReport:
    day: int
    turns: list[Turn]


def _lapse(store: Store) -> None:
    for rq in store.where("requests", status="open"):
        if rq.expires >= store.day:
            continue
        cause = f"routine:lapse:{rq.id}"
        store.set("requests", rq.id, cause, status="lapsed")
        text = f"Your request {rq.id} to {name_of_who(store, rq.to)} ({cut(rq.text)}) was not answered and has lapsed."
        store.add("notices", Notice(store.new_id("n", cause), store.day, rq.sender, text), cause)


def routine(store: Store) -> None:
    """The morning: today's events; on a Monday, a week's free hours and the shops'
    deliveries; old requests lapse; what is owed is opened, aged and collected; then those
    whose shopping day it is shop. Events come first, so that someone who falls ill on a
    Monday has no hours that week."""
    hamlet.apply_events(store)
    hours.monday_hours(store)
    firms.deliver(store)
    _lapse(store)
    obligations.open_dues(store)
    obligations.age_arrears(store)
    obligations.collect(store)
    firms.shop_today(store)


def close(store: Store) -> None:
    """The evening: on a Sunday the shops' week closes; then the day ends."""
    firms.close_week(store)
    store.advance_day()


def who_acts(store: Store) -> list[str]:
    """People with something unseen, an open request to them they have not seen or that is
    on its last day, or their stock day today."""
    waiting = {rq.to for rq in store.where("requests", status="open") if not rq.seen or rq.expires == store.day}
    news = with_news(store)
    return [p.id for p in store.all("people") if p.id in news or p.id in waiting or p.stock_day == dow(store.day)]


def parse_acts(text: str | None) -> list | None:
    """The `acts` list of the first top-level JSON object in `text` that has one, or None.

    Only the first `SCAN_CHARS` characters are read and at most `SCAN_TRIES` decodes are
    tried. An object without an `acts` list is skipped whole. Never raises."""
    reply = parse_reply(text)
    return None if reply is None else reply["acts"]


def parse_reply(text: str | None) -> dict | None:
    """The first top-level JSON object in `text` that has an `acts` list, or None; read as
    `parse_acts` reads it. Never raises."""
    if not isinstance(text, str):
        return None
    text = text[:SCAN_CHARS]
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


def _act(pid: str, element: Any) -> Act:
    if isinstance(element, dict):
        return Act(pid, pid, element.get("op"), {k: v for k, v in element.items() if k != "op"})
    return Act(pid, pid, None, element)


NO_OP = "(no operation named)"
MORE = "(more)"


def _op_written(op: Any) -> str:
    """An act's operation name as its actor wrote it, cut to 40 characters, on one line;
    `(no operation named)` when none was written."""
    written = " ".join((clean_text(shown(op)) or "").split()) if op is not None else ""
    return written or NO_OP


def _tell_result(store: Store, outcome: Outcome) -> None:
    act = outcome.act
    result = Result(store.new_id("res", outcome.cause), store.day, act.actor, _op_written(act.op), outcome.msg,
                    outcome.ok)
    store.add("results", result, outcome.cause)


def _tell_more(store: Store, pid: str, k: int, cause: str) -> None:
    """Tell `pid` that `k` operations past the eighth (`MAX_ACTS`) were not carried out."""
    text = f"Not done: {k} more operations were written than the eight carried out in a day."
    store.add("results", Result(store.new_id("res", cause), store.day, pid, MORE, text, False), cause)


def run_day(store: Store, replier: Replier, keys: Keys) -> DayReport:
    """The routine, then everyone's view, every reply, and each person's aims and acts in
    id order; then the close. A reply's `aims`, when it has them, are taken before its acts.
    Acts past the eighth are not carried out, and the person is told how many."""
    routine(store)
    day = store.day
    shown = [(pid, *view(store, pid)) for pid in who_acts(store)]
    asked = []
    for pid, messages, _ in shown:
        key = keys.next(replier.model_for(pid), day, pid, "turn")
        asked.append((key.text(), replier.ask(key, messages)))
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
            outcome = apply_act(store, _act(pid, element), n)
            _tell_result(store, outcome)
            outcomes.append(outcome)
            n += 1
        if len(elements) > MAX_ACTS:
            _tell_more(store, pid, len(elements) - MAX_ACTS, f"op:{pid}:more:d{day}.{n}")
            n += 1
        mark_seen(store, pid, ids, f"routine:seen:{pid}")
        turns.append(Turn(pid, key, False, outcomes))
    close(store)
    return DayReport(day, turns)
