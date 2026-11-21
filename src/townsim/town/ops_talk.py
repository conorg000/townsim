"""The operations of talking: asking to talk today (`converse`) and booking a talk for a later
day (`book_appointment`). Registered when `ops_core` is imported; `stop` naming an
appointment cancels it (`STOPPABLE_OF_TALK`, which `ops_core.stop` hands such ids to).

The talks themselves are held after everyone has acted (`talk`)."""

from __future__ import annotations

from typing import Any

from townsim.town import ops_core as core
from townsim.town import talk
from townsim.town.acts import Act, OpSpec, clean_text, fits, register, shown
from townsim.town.clock import date_text, parse_date, unreadable
from townsim.town.entities import Appointment, Notice, Talk
from townsim.town.statement import quoted
from townsim.town.store import Store
from townsim.town.unknown import reader_lines, unknown_names, writer_text

OPENING_WORDS = 120          # as much as a turn in a talk (`talk.SAY_WORDS`)
ABOUT_WORDS = 12
AHEAD_DAYS = 60


def _with(store: Store, act: Act) -> tuple[str | None, str]:
    """The person `with` names, or None and why not."""
    value = act.args.get("with")
    who = core._resolve(store, value, act.actor)
    if who is None:
        return None, f"no one you know is called '{shown(value)}'. Names are written in full, as they appear in your records."
    if not store.has("people", who):
        return None, f"{store.get('orgs', who).name if store.has('orgs', who) else shown(value)} is not a person."
    if who == act.actor:
        return None, "that is you."
    return who, ""


def _words(value: Any, words: int) -> tuple[str | None, str]:
    text = clean_text(value)
    if text is None or not text.strip():
        return None, "there was nothing written."
    if not fits(text, words):
        return None, "that is too long."
    return text, ""


# converse

def _check_converse(store: Store, act: Act) -> str | None:
    who, why = _with(store, act)
    if who is None:
        return why
    text, why = _words(act.args.get("opening"), OPENING_WORDS)
    if text is None:
        return why
    today = [t for t in store.where("talks", day=store.day)
             if act.actor in (t.a, t.b) and t.status in ("waiting", "held")]
    if len(today) >= talk.TALKS_A_DAY:
        return "you already have two talks today."
    return None


def _do_converse(store: Store, act: Act, cause: str) -> str:
    who, _ = _with(store, act)
    name = store.get("people", who).name
    tk = store.new_id("tk", cause)
    opening = talk.said(act.args["opening"], name)
    store.add("talks", Talk(tk, store.day, act.actor, who, "asked", opening, "waiting"), cause)
    return f"You will talk with {name} today ({tk})." + writer_text(unknown_names(store, act.actor, who, opening))


# book_appointment

def _when(store: Store, act: Act) -> tuple[int | None, str]:
    value = act.args.get("day")
    day = parse_date(value, store.day) if isinstance(value, str) and len(value) <= 100 else None
    if day is None and (not isinstance(value, str) or parse_date(value, -10 ** 6) is None):
        return None, unreadable(value)
    if day is None or not store.day < day <= store.day + AHEAD_DAYS:
        return None, f"'{shown(value)}' is not a day after today and within 60 days."
    return day, ""


def _check_book(store: Store, act: Act) -> str | None:
    who, why = _with(store, act)
    if who is None:
        return why
    day, why = _when(store, act)
    if day is None:
        return why
    pair = {act.actor, who}
    for ap in store.where("appointments", day=day):
        if ap.status == "booked" and {ap.a, ap.b} == pair:
            return (f"you already have a talk booked with {store.get('people', who).name} on {date_text(day)} "
                    f"({ap.id}).")
    text, why = _words(act.args.get("about"), ABOUT_WORDS)
    return why if text is None else None


def _do_book(store: Store, act: Act, cause: str) -> str:
    who, _ = _with(store, act)
    day, _ = _when(store, act)
    about = quoted(clean_text(act.args["about"]))
    ap = store.new_id("ap", cause)
    store.add("appointments", Appointment(ap, day, act.actor, who, about, store.day), cause)
    me, other = store.get("people", act.actor).name, store.get("people", who).name
    text = f"{me} has booked to talk with you on {date_text(day)} about {about}."
    text = " ".join([text, *reader_lines(unknown_names(store, who, act.actor, about))])
    store.add("notices", Notice(store.new_id("n", cause), store.day, who, text), cause)
    return f"Booked ({ap}): to talk with {other} on {date_text(day)}." + writer_text(
        unknown_names(store, act.actor, who, about))


# stop, naming an appointment

def _check_cancel(store: Store, act: Act) -> str | None:
    ap = store.get("appointments", act.args["id"])
    if act.principal not in (ap.a, ap.b) or ap.status != "booked" or ap.day < store.day:
        return f"you have nothing with the id '{shown(ap.id)}'."
    return None


def _do_cancel(store: Store, act: Act, cause: str) -> str:
    ap = store.get("appointments", act.args["id"])
    store.set("appointments", ap.id, cause, status="cancelled")
    other = ap.b if act.principal == ap.a else ap.a
    me = store.get("people", act.principal).name
    text = f"{me} has cancelled the talk with you on {date_text(ap.day)} about {ap.about}."
    store.add("notices", Notice(store.new_id("n", cause), store.day, other, text), cause)
    return f"Cancelled: the talk with {store.get('people', other).name} on {date_text(ap.day)}."


STOPPABLE_OF_TALK = {"appointments": (_check_cancel, _do_cancel)}

for _spec in (
    OpSpec("converse", '{"op": "converse", "with": "<name>", "opening": "<your first words>"}',
           "Talk with someone today.", 1.0, _check_converse, _do_converse, 240),
    OpSpec("book_appointment", '{"op": "book_appointment", "with": "<name>", "day": "<e.g. Thursday 12 March>", '
                               '"about": "<what for>"}',
           "Book a talk with someone on a later day.", 0.1, _check_book, _do_book, 250),
):
    register(_spec)
