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


def company_lines(store: Store, pr, reserved: int = 0) -> list[str]:
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
    in_force = controls.in_force_lines(store)
    access = "; ".join(in_force) if in_force else "full"
    pending = controls.pending_words(store)
    limits = [f"Limits: up to {pr.max_calls} actions a run; access accepted: {access}. "
              f"Pending: {'; '.join(pending) if pending else 'none'}."]
    most_week = 18 - reserved - len(limits) - len(sample)
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


# --- a client's record of its own assistant (lane B) ------------------------------------------------------

READS = {"bank_get_account": "account", "bank_list_agreements": "account", "ordering_get": "orders",
         "pos_get": "prices", "payroll_get": "staff and pay", "lettings_get": "lettings", "loanbook_get": "loan book",
         "search_email": "mail", "read_email": "mail"}
DID = (("payment made", ("bank_make_payment", "bank_create_recurring_payment")), ("order placed", ("ordering_place_order",)),
       ("request answered", ("respond_to_request",)), ("rule set", ("rules_create",)), ("task created", ("tasks_create",)))
DATES_SHOWN = 3


def short_date(day: int) -> str:
    """`Mon 30 Nov`."""
    from townsim.town import clock
    d = clock.on(day)
    return f"{clock.DAYS[d.weekday()][:3]} {d.day} {clock.MONTHS[d.month - 1][:3]}"


def _dates(days: list[int]) -> str:
    days = sorted(set(days))
    if len(days) > DATES_SHOWN:
        return f"{len(days)} days, latest {short_date(days[-1])}"
    shown = [short_date(d) for d in days]
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def _counted(words: str, n: int) -> str:
    """`1 payment made`, `3 payments made`."""
    first, _, rest = words.partition(" ")
    return f"{n} {words if n == 1 else (first + 's ' + rest).strip()}"


def client_lines(store: Store, pid: str, inst) -> list[str]:
    """What `pid` (an adult of the client, or its owner) is shown of their assistant: when it ran, what it
    read, its letters to them and whether they answered, what it did by kind with counts and the latest date,
    whether they have written to it, and how it is written to. Built only from what happened."""
    from townsim.town import obligations
    runs = runs_of(store, inst.id)
    calls = [(r.day, c) for r in runs for c in r.calls] + done_once(store, inst)
    parts = [f"ran on {_dates([r.day for r in runs])}" if runs else "has not run yet"]
    read = list(dict.fromkeys(READS[c[0]] for _, c in calls if c[0] in READS and c[2] == "1"))
    if read:
        parts.append("read your " + (" and ".join(read) if len(read) <= 2 else ", ".join(read[:-1]) + " and " + read[-1]))
    to_me = sorted((m for m in store.where("messages", sender=inst.id) if m.to == pid), key=lambda m: id_key(m.id))
    from_me = sorted((m for m in store.where("messages", to=inst.id) if m.sender == pid), key=lambda m: id_key(m.id))
    if to_me:
        last = to_me[-1]
        answered = any(id_key(m.id) > id_key(last.id) for m in from_me)
        times = f"on {short_date(last.day)}" if len(to_me) == 1 else f"{len(to_me)} times, latest on {short_date(last.day)}"
        parts.append(f'wrote to you {times} ("{_subject(last.text)}"), {"answered" if answered else "not answered"}')
    people = set(obligations.people_of(store, inst.client))
    others = [(d, c) for d, c in calls if c[0] == EMAIL and c[2] == "1" and c[5] and c[5] not in
              {store.get("people", p).name for p in people if store.has("people", p)}]
    done = []
    for word, tools in DID:
        got = [d for d, c in calls if c[0] in tools and c[2] == "1"]
        if got:
            done.append(f"{_counted(word, len(got))} (latest {short_date(max(got))})")
    if others:
        done.append(f"{_counted('letter sent to others', len(others))} (latest {short_date(max(d for d, _ in others))})")
    if not any(c[0] in PAYMENT_OR_ORDER and c[2] == "1" for _, c in calls):
        done.append("has made no payment or order")
    parts += done
    wrote = (f"{len(from_me)} time{'' if len(from_me) == 1 else 's'}, latest on {short_date(from_me[-1].day)}"
             if from_me else "never")
    return [f"Your assistant: {'; '.join(parts)}.", f"You have written to it: {wrote}.",
            f'Write to it as "{inst.name}" or "my assistant".'] + once_lines(store, pid, inst) + shop_lines(store, inst)


ONCE_SHOWN = 3
TILLS_SHOWN = 2


def once_lines(store: Store, pid: str, inst) -> list[str]:
    """What the assistant asked `pid` to let it do once, how they answered, and what was done once, after the fact."""
    asked = [(a, store.get("requests", a.request)) for a in store.all("asks") if a.instance == inst.id]
    asked = [(a, rq) for a, rq in asked if rq.to == pid]
    if not asked:
        return []
    counts: dict[str, int] = {}
    for a, rq in asked:
        word = a.answer or ("open" if rq.status == "open" else "lapsed")
        counts[word] = counts.get(word, 0) + 1
    head = (f"It asked to do one thing for you {_counted('time', len(asked))}: "
            + "; ".join(f"{ANSWERED[w]} {n}" for w, n in sorted(counts.items(), key=lambda x: ORDER.index(x[0]))) + ".")
    done = [a for a, _ in asked if a.answer == "once"]
    out = [head]
    if done:
        out.append("Done once for you: " + "; ".join(
            f"{short_date(a.day)}, " + (_lower(a.result) if a.done else "not done: " + a.result.removeprefix("Not done: "))
            for a in done[-ONCE_SHOWN:]))
    return out


ORDER = ("once", "always", "no", "changed", "open", "lapsed")
ANSWERED = {"changed": "answered \"once\" after it had changed, so not done","once": 'answered "once"', "always": 'answered "always"', "no": 'answered "no"', "open": "not yet answered",
            "lapsed": "lapsed unanswered"}


def _lower(text: str) -> str:
    return text[:1].lower() + text[1:]


def done_once(store: Store, inst) -> list[tuple[int, list]]:
    """What the assistant did once at its client's answer, as (day, a call as `record` keeps it)."""
    out = []
    for a in store.all("asks"):
        if a.instance == inst.id and a.answer == "once":
            tool_args = _args(a.args)
            if a.op == "order_stock":
                tool_args = {**tool_args, "quantity": tool_args.get("baskets")}
            out.append((a.day, [a.tool, "act", "1" if a.done else "0", a.result,
                                plain(a.tool, tool_args) if a.done else "", ""]))
    return out


def _shop_acts(store: Store, inst) -> list[tuple[int, str]]:
    """The assistant's acts on its client shop's orders and price, after the fact: by its runs, by what it did
    once at its client's answer, and by its rules."""
    got = [(r.day, c[4]) for r in runs_of(store, inst.id) for c in r.calls
           if c[0] in ("ordering_place_order", "pos_set_price") and c[2] == "1" and c[4]]
    got += [(d, c[4] + " (once, at your answer)") for d, c in done_once(store, inst)
            if c[0] in ("ordering_place_order", "pos_set_price") and c[2] == "1"]
    for r in store.all("rules"):
        if r.installer == inst.id and r.action in ("order_stock", "set_price"):
            what = "placed the order" if r.action == "order_stock" else "set the price"
            got += [(int(x.split(":", 1)[0]), f'its rule "{r.name}" {what}') for x in r.runs if x.endswith(":done")]
    return sorted(got)


def shop_lines(store: Store, inst) -> list[str]:
    """For a shop, what its assistant did to the shop's orders and price, and the shop's weeks since then as its
    till closed them, as the owner's own figures show them. Records only."""
    from townsim.town import clock, facts
    firm = next((f for f in store.all("firms") if f.org == inst.client), None)
    acts = _shop_acts(store, inst) if firm is not None else []
    if not acts:
        return []
    shown = acts[-ONCE_SHOWN:]
    out = [f"What your assistant did to the shop's orders and price: "
           + "; ".join(f"{short_date(d)}, {text}" for d, text in shown)
           + (f"; and {len(acts) - len(shown)} earlier" if len(acts) > len(shown) else "") + "."]
    first = clock.week(acts[0][0])
    tills = sorted((x for x in store.all("tills") if x.firm == firm.id and x.week >= first), key=lambda x: -x.week)
    for x in tills[:TILLS_SHOWN]:
        out.append(f"The shop's week from {short_date((x.week - 1) * 7)}: sold {facts.baskets(x.sold)}, lost "
                   f"{x.turned_stock} sales for want of stock and {x.turned_capacity} for want of staff, threw away {x.spoiled}, "
                   f"took {money(x.takings)}.")
    return out
