"""A day in a fixed order (design 4.1): the routine, then everyone's view built from the
morning's state, then every reply, and only then the acts, person by person in id order."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import obligations
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
    """The morning: hours back on a Monday, old requests lapse, then what is owed is
    opened, aged and collected."""
    if dow(store.day) == 0:
        for p in store.all("people"):
            if p.hours_left != p.hours_week:
                store.set("people", p.id, "routine:week", hours_left=p.hours_week)
    _lapse(store)
    obligations.open_dues(store)
    obligations.age_arrears(store)
    obligations.collect(store)


def who_acts(store: Store) -> list[str]:
    """People with something unseen, an open request to them, or their stock day today."""
    waiting = {rq.to for rq in store.where("requests", status="open")}
    news = with_news(store)
    return [p.id for p in store.all("people") if p.id in news or p.id in waiting or p.stock_day == dow(store.day)]


def parse_acts(text: str | None) -> list | None:
    """The `acts` list of the first top-level JSON object in `text` that has one, or None.

    Only the first `SCAN_CHARS` characters are read and at most `SCAN_TRIES` decodes are
    tried. An object without an `acts` list is skipped whole. Never raises."""
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
            return value["acts"]
        at = text.find("{", end)
    return None


def _act(pid: str, element: Any) -> Act:
    if isinstance(element, dict):
        return Act(pid, pid, element.get("op"), {k: v for k, v in element.items() if k != "op"})
    return Act(pid, pid, None, element)


def _op_written(op: Any) -> str:
    """An act's operation name as its actor wrote it, cut to 40 characters, on one line."""
    return " ".join((clean_text(shown(op)) or "").split())


def _tell_result(store: Store, outcome: Outcome) -> None:
    act = outcome.act
    result = Result(store.new_id("res", outcome.cause), store.day, act.actor, _op_written(act.op), outcome.msg,
                    outcome.ok)
    store.add("results", result, outcome.cause)


def run_day(store: Store, replier: Replier, keys: Keys) -> DayReport:
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
        elements = parse_acts(reply.text)
        if elements is None:
            turns.append(Turn(pid, key, True, []))
            continue
        outcomes = []
        for element in elements[:MAX_ACTS]:
            outcome = apply_act(store, _act(pid, element), n)
            _tell_result(store, outcome)
            outcomes.append(outcome)
            n += 1
        mark_seen(store, pid, ids, f"routine:seen:{pid}")
        turns.append(Turn(pid, key, False, outcomes))
    store.advance_day()
    return DayReport(day, turns)
