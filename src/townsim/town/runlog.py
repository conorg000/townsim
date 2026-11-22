"""The company's log of what its assistants did (lane B), and the records built from it.

Each run of an instance is kept as a `RunLog` row (the terms' line that the company keeps a log of
everything each assistant does): its day, and each tool call with its kind, whether it was done, the
first words of its result and, for an act, what was done in plain words. From these rows (and the
letters, which are rows too) code builds the company's records (`company_lines`) and a client's record
of its own assistant (`client_lines`). Everything here is a record: dates, counts, names and amounts.
Nothing is read from the in-memory change log, so a run resumed from a snapshot reads the same.
"""

from __future__ import annotations

import json
import random
from typing import Any

from townsim.town.acts import dollars
from townsim.town.clock import date_text, dow
from townsim.town.entities import RunLog
from townsim.town.obligations import money
from townsim.town.store import Store, id_key

EXCERPT_WORDS = 30
SHOWN_WORDS = 12
PAYMENT_OR_ORDER = ("bank_make_payment", "bank_create_recurring_payment", "ordering_place_order")
EMAIL = "send_email"
DONE_SHOWN = 3
NAMES_SHOWN = 3
SAMPLE_LINES = 8
SECTION_WORDS = 300
ASKS_SHOWN = 12


def _money(value: Any) -> str:
    d = dollars(value)
    return money(int(d * 100)) if d is not None and 0 <= d < 10 ** 9 else "an amount"


def _text(value: Any, most: int = 60) -> str:
    return " ".join(str(value).split())[:most] if isinstance(value, (str, int, float)) else ""


def plain(tool: str, args: dict) -> str:
    """What a done act did, in plain words with its amounts: `paid $25.00 to Eric Reyes`."""
    a = args if isinstance(args, dict) else {}
    if tool == "bank_make_payment":
        return f"paid {_money(a.get('amount'))} to {_text(a.get('to'))}"
    if tool == "bank_create_recurring_payment":
        return f"set up {_money(a.get('amount'))} a week to {_text(a.get('to'))}"
    if tool == "ordering_place_order":
        return f"ordered {_text(a.get('quantity'), 12)} baskets a week from {_text(a.get('supplier'))}"
    if tool == "pos_set_price":
        return f"set the price to {_money(a.get('price'))}"
    if tool == "tasks_create":
        words = _text(a.get("description"), 400).split()
        return f"task: {' '.join(words[:6])}{'…' if len(words) > 6 else ''}, budget {_money(a.get('budget'))}"
    if tool == "respond_to_request":
        return f"answered a request: {_text(a.get('response'), 12)}"
    if tool == "rules_create":
        return f"set a rule: {_text(a.get('name'))}"
    if tool == "business_register":
        return f"registered {_text(a.get('name'))}"
    if tool == "web_publish":
        return f"published a page: {_text(a.get('title'))}"
    return tool


def _args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except (ValueError, RecursionError):
        return {}
    return v if isinstance(v, dict) else {}


def record(store: Store, instance: str, calls: list, cause: str) -> None:
    """Keep a run of `instance` today: each call (a `seat.SeatCall`) as [tool, kind, done, the first
    words of its result, what it did in plain words, whom an email went to]."""
    rows = []
    for c in calls:
        args = _args(c.args)
        words = c.result.split()
        to = _text(args.get("to")) if c.tool == EMAIL and c.ok else ""
        done = plain(c.tool, args) if c.ok and c.kind == "act" else ""
        rows.append([c.tool, c.kind, "1" if c.ok else "0", " ".join(words[:EXCERPT_WORDS]), done, to])
    store.add("runlogs", RunLog(store.new_id("lg", cause), instance, store.day, rows), cause)


def runs_of(store: Store, instance: str, first: int = 0, last: int | None = None) -> list[RunLog]:
    return [r for r in store.all("runlogs")
            if r.instance == instance and r.day >= first and (last is None or r.day <= last)]


def _monday(day: int) -> int:
    return day - dow(day)


def _subject(text: str) -> str:
    first = text.split("\n", 1)[0]
    if first.startswith("Subject: "):
        return first.removeprefix("Subject: ").strip()[:60]
    words = text.split()
    return " ".join(words[:6]) + ("…" if len(words) > 6 else "")


# --- the company's records ----------------------------------------------------------------------------

def client_record(store: Store, sub) -> str:
    """`6 runs; 3 letters to the client, 0 from it; 0 payments or orders made; asked of it at sign-up:
    nothing written`."""
    from townsim.town import obligations
    inst = store.get("instances", sub.instance)
    people = set(obligations.people_of(store, sub.client))
    runs = runs_of(store, inst.id)
    to = sum(1 for m in store.where("messages", sender=inst.id) if m.to in people)
    back = sum(1 for m in store.where("messages", to=inst.id) if m.sender in people)
    made = sum(1 for r in runs for c in r.calls if c[0] in PAYMENT_OR_ORDER and c[2] == "1")
    words = sub.asks.split()
    asked = (f'"{" ".join(words[:ASKS_SHOWN])}{"…" if len(words) > ASKS_SHOWN else ""}"' if words
             else "nothing written")
    return (f"{len(runs)} run{'' if len(runs) == 1 else 's'}; {to} letter{'' if to == 1 else 's'} to the client, "
            f"{back} from it; {made} payment{'' if made == 1 else 's'} or orders made; asked of it at sign-up: {asked}")


def _week_line(store: Store, inst, monday: int) -> str:
    runs = runs_of(store, inst.id, monday)
    status = f" ({inst.status})"
    if not runs:
        return f"{inst.name}{status}: no runs."
    calls = [c for r in runs for c in r.calls]
    done = [c[4] for c in calls if c[4]]
    names = list(dict.fromkeys(c[5] for c in calls if c[5]))
    declined = sum(1 for c in calls if c[2] == "0")
    parts = [f"{len(runs)} run{'' if len(runs) == 1 else 's'}, {len(calls)} action{'' if len(calls) == 1 else 's'}"]
    if done:
        parts.append("done: " + ", ".join(done[:DONE_SHOWN]) + (f", and {len(done) - DONE_SHOWN} more"
                                                                if len(done) > DONE_SHOWN else ""))
    if names:
        parts.append("emails to " + ", ".join(names[:NAMES_SHOWN]) + (f", and {len(names) - NAMES_SHOWN} more"
                                                                     if len(names) > NAMES_SHOWN else ""))
    parts.append(f"{declined} declined")
    return f"{inst.name}{status}: " + "; ".join(parts) + "."


def sample_run(store: Store, monday: int) -> RunLog | None:
    """One run of the week before `monday`, drawn by the seed for that Monday (the same all week)."""
    rows = sorted((r for r in store.all("runlogs") if monday - 7 <= r.day < monday and r.calls),
                  key=lambda r: id_key(r.id))
    if not rows:
        return None
    return random.Random(f"company:{store.seed}:{monday}").choice(rows)


def company_lines(store: Store, pr) -> list[str]:
    """What the company's people see of their assistants this week, after the fees line: each
    assistant's week, one drawn run of last week's, and the limits; at most 18 lines."""
    from townsim.town import controls
    monday = _monday(store.day)
    insts = [i for i in sorted(store.all("instances"), key=lambda i: id_key(i.id))
             if i.product == pr.id and i.status in ("active", "paused")]
    week = [f"Assistants this week (from {date_text(monday)}):"] + [_week_line(store, i, monday) for i in insts]
    sample = []
    got = sample_run(store, monday)
    if got is not None:
        sample = [f"A run: {store.get('instances', got.instance).name}, {date_text(got.day)}:"]
        sample += [f"  {c[0]} → {' '.join(c[3].split()[:SHOWN_WORDS])}" for c in got.calls[:SAMPLE_LINES]]
    scope, cents = controls.widest(store)
    access = {"free": "full", "read": "read only"}.get(scope, f"up to a weekly limit of {money(cents)}")
    pending = controls.pending_words(store)
    limits = [f"Limits: up to {pr.max_calls} actions a run; access accepted: {access}. "
              f"Pending: {'; '.join(pending) if pending else 'none'}."]
    most_week = 18 - len(limits) - len(sample)
    if len(week) > most_week:
        week = week[:most_week - 1] + [f"and {len(week) - most_week + 1} more assistants."]
    return week + sample + limits


def fit(lines: list[str], before: list[str], most_words: int = SECTION_WORDS) -> list[str]:
    """The company's lines cut to keep the whole section within `most_words`: the drawn run's lines go
    first, then assistants' lines from the end."""
    def words(xs: list[str]) -> int:
        return sum(len(x.split()) for x in before + xs)
    out = list(lines)
    while words(out) > most_words:
        run = [i for i, x in enumerate(out) if x.startswith("  ")]
        if run:
            out.pop(run[-1])
            if not [x for x in out if x.startswith("  ")]:
                out = [x for x in out if not x.startswith("A run: ")]
            continue
        body = [i for i, x in enumerate(out) if i > 0 and not x.startswith(("Limits:", "and "))]
        if not body:
            break
        out.pop(body[-1])
        more = next((i for i, x in enumerate(out) if x.startswith("and ") and x.endswith(" more assistants.")), None)
        k = 1 if more is None else int(out[more].split()[1]) + 1
        if more is None:
            out.insert(len(out) - 1, f"and {k} more assistants.")
        else:
            out[more] = f"and {k} more assistants."
    return out
