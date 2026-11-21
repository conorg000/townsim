"""The generated hamlet's days: the ordinary events dealt when it was made, applied each on
its day, and a scripted life that plays any generated place without a model.

`apply_events` is part of the morning routine. Each event is applied once, on its day, with
the cause `routine:event:<event id>`, and told to those it falls on in a notice that says
what happened and nothing else. An event naming a person, household or supplier that does
not exist is noted in `store.engine_errors` and marked done.
"""

from __future__ import annotations

import json
import random
import re

from townsim.town import jobs, obligations
from townsim.town.clock import date_text, dow, week
from townsim.town.entities import Event, Notice
from townsim.town.obligations import money
from townsim.town.replies import ReplyKey
from townsim.town.store import Store

FRIDAY, SUNDAY = 4, 6
REPAIR_DAYS = 7
_NUMBERS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _tell(store: Store, people: list[str], text: str, cause: str) -> None:
    for pid in people:
        store.add("notices", Notice(store.new_id("n", cause), store.day, pid, text), cause)


def _weeks(n: int) -> str:
    word = _NUMBERS[n] if 0 <= n < len(_NUMBERS) else str(n)
    return f"{word} week" + ("" if n == 1 else "s")


def _illness(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("people", e.who):
        return "no person"
    fields: dict = {"ill_until": e.day + (SUNDAY - dow(e.day))}
    if dow(e.day) != 0:                  # on a Monday the week's hours, set next, are none
        fields["hours_left"] = 0.0
    store.set("people", e.who, cause, **fields)
    _tell(store, [e.who], "You are ill this week.", cause)
    return None


def _job_lost(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("people", e.who):
        return "no person"
    held = [j for j in jobs.held(store, person=e.who) if store.has("orgs", j.org) and store.get("orgs", j.org).outside]
    if not held:
        return "no job at an outside employer for"
    job = held[0]
    last = e.day + (FRIDAY - dow(e.day)) % 7        # this week's Friday, or the next if it has passed
    jobs.finish(store, job.id, last, cause)
    _tell(store, [e.who], f"{store.get('orgs', job.org).name} has ended your job. "
                          f"Your last pay is on {date_text(last)}.", cause)
    return None


def _adults(store: Store, household: str) -> list[str]:
    return [p for p in store.get("households", household).members if store.get("people", p).age >= 18]


def _repair(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("households", e.who):
        return "no household"
    spending = [o for o in store.where("obligations", payer=e.who) if o.memo == "regular spending"]
    if not spending:
        return "no regular spending for"
    due = store.day + REPAIR_DAYS
    obligations.create(store, kind="bill", payer=e.who, payee=spending[0].payee, cents=e.amount, first=due,
                       memo="repair to the house", ends="service", made_by="routine", cause=cause)
    _tell(store, _adults(store, e.who), f"The house needs a repair costing {money(e.amount)}. "
                                        f"It is due on {date_text(due)}.", cause)
    return None


def _supplier_short(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("suppliers", e.who):
        return "no supplier"
    sp = store.get("suppliers", e.who)
    monday = e.day + (-dow(e.day)) % 7                  # today if a Monday, else the next
    store.set("suppliers", sp.id, cause, short_week=week(monday))
    name = store.get("orgs", sp.org).name if store.has("orgs", sp.org) else sp.org
    when = "this week's delivery" if monday == e.day else f"the delivery on {date_text(monday)}"
    owners: list[str] = []
    for f in store.all("firms"):
        if sp.id in (f.order_supplier, f.last_supplier) and store.has("orgs", f.org):
            owner = store.get("orgs", f.org).owner
            if owner and owner not in owners:
                owners.append(owner)
    _tell(store, owners, f"{name} has said {when} will be short.", cause)
    return None


def _cold_month(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("orgs", e.who):
        return "no utility"
    for ob in store.where("obligations", payee=e.who):
        if ob.kind != "bill" or ob.memo != "energy" or ob.status != "active" or not store.has("households", ob.payer):
            continue
        extra = (ob.cents * e.amount + 50) // 100
        for day in obligations.instalment_days(ob, store.day, e.weeks):
            obligations.create(store, kind="bill", payer=ob.payer, payee=ob.payee, cents=extra, first=day,
                               memo="extra energy, cold spell", ends="service", made_by="routine", cause=cause)
        _tell(store, _adults(store, ob.payer),
              f"A cold spell: energy bills are {e.amount}% higher for {_weeks(e.weeks)}.", cause)
    return None


_KINDS = {"illness": _illness, "job_lost": _job_lost, "repair": _repair, "supplier_short": _supplier_short,
          "cold_month": _cold_month}


def apply_events(store: Store) -> None:
    """Apply each event due today and not yet done, in id order, each in full or not at all."""
    for e in store.all("events"):
        if e.done or e.day != store.day:
            continue
        cause = f"routine:event:{e.id}"
        apply = _KINDS.get(e.kind)
        mark = store.mark()
        try:
            with store.transaction():
                why = f"is of no kind known ({e.kind!r})" if apply is None else apply(store, e, cause)
                if why is not None:
                    raise _Skip(why)
        except _Skip as skip:
            what = skip.args[0]
            store.engine_errors.append(f"event {e.id} d{store.day}: {e.kind} {what} {e.who!r}; not applied"
                                       if apply is not None else f"event {e.id} d{store.day}: {what}; not applied")
        except Exception as ex:          # a bug: undone and noted, never raised out of the morning
            if store.mark() != mark:
                store.undo_to(mark)
            store.engine_errors.append(f"event {e.id} d{store.day}: {type(ex).__name__}: {str(ex)[:200]}")
        store.set("events", e.id, cause, done=True)


class _Skip(Exception):
    """An event that cannot be applied, and why."""


# --- a scripted life --------------------------------------------------------------------

_MONEY = r"-?\$[\d,]+\.\d\d"
_NAME = re.compile(r"^You are (.+?), \d+\.", re.M)
_AT = re.compile(r"\b(?:at|runs) ((?:[A-Z][A-Za-z']*)(?: [A-Z][A-Za-z']*)*)")
_ARREARS = re.compile(rf"^  overdue since [^$]+?  ({_MONEY})  (.+?)  (.+) \((ob\d+)\)$")
_COMING = re.compile(rf"^  [A-Z][a-z]+day \d+ [A-Z][a-z]+  ({_MONEY})  (.+?)  (.+) \((ob\d+)\)$")
_REQUEST = re.compile(rf"^(rq\d+) from (.+?): .*?(?:They ask you for ({_MONEY})\.)?$")
_OWED_TO_ME = re.compile(r"^(ob\d+): (.+?), (\$[\d,]+\.\d\d) a week from ")
_STANDING_NOTE = "towards the week"
_NO_ONE = "Edith Marsh"


def _cents(text: str) -> int:
    sign = -1 if text.startswith("-") else 1
    return sign * int(text.lstrip("-").removeprefix("$").replace(",", "").replace(".", ""))


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


def _sections(user: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for block in user.split("\n\n"):
        lines = block.split("\n")
        if lines and lines[0].isupper():
            out[lines[0]] = lines[1:]
    return out


class _Seen:
    """What a scripted person reads off their view."""

    def __init__(self, messages: list[dict]) -> None:
        system = messages[0].get("content", "") if messages else ""
        user = messages[-1].get("content", "") if messages else ""
        found = _NAME.search(system)
        self.name = found.group(1) if found else ""
        self.sections = _sections(user)
        account = self.sections.get("YOUR HOUSEHOLD'S ACCOUNT", [])
        self.balance = next((_cents(m.group(1)) for line in account
                             if (m := re.match(rf"Balance: ({_MONEY})", line))), 0)
        self.after = next((_cents(m.group(1)) for line in account
                           if (m := re.match(rf"After what goes out: ({_MONEY})", line))), 0)
        out = []                     # the lines under "Going out by"
        for line in account:
            if line.startswith("Going out by"):
                out = [line]
            elif line.startswith("Expected in by"):
                break
            elif out:
                out.append(line)
        self.arrears = [(_cents(m.group(1)), m.group(2), m.group(3), m.group(4))
                        for line in out if (m := _ARREARS.match(line))]
        self.coming = [(_cents(m.group(1)), m.group(2), m.group(3), m.group(4))
                       for line in out if (m := _COMING.match(line))]
        self.people = [line.split(", ", 1) for line in self.sections.get("PEOPLE YOU KNOW", []) if ", " in line]
        self.requests = [(m.group(1), m.group(2), _cents(m.group(3)) if m.group(3) else 0)
                         for line in self.sections.get("WAITING FOR YOUR ANSWER", []) if (m := _REQUEST.match(line))]
        self.owed = self.sections.get("WHAT IS OWED", [])
        business = self.sections.get("YOUR BUSINESS", [])
        self.business = business[0] if business else ""
        mine = {self.business, self.name}
        self.places = [p for p in dict.fromkeys(_AT.findall(system)) if p not in mine and " " in p]
        self.minded = "ON YOUR MIND" in self.sections

    def known(self, *words: str) -> list[str]:
        """The full names of people known whose line has any of `words`."""
        return [name for name, how in self.people if any(w in how for w in words)]


def _reply(aims: list[str] | None, acts: list) -> str:
    body: dict = {"thoughts": ""}
    if aims is not None:
        body["aims"] = aims
    body["acts"] = acts
    return json.dumps(body)


def script(key: ReplyKey, messages: list[dict]) -> str:
    """A scripted life for anyone in a generated place: what they do is decided from the day,
    who they are and the text of their view alone, with `Random(f"script:<day>:<actor>")`
    for variety. They answer what is asked of them, pay toward what is overdue, give and ask
    for help, write to those they know, keep notes and aims, and now and then make a slip."""
    rng = random.Random(f"script:{key.day}:{key.actor}")
    me = _Seen(messages)
    num = int(re.sub(r"\D", "", key.actor) or 0)
    day = key.day
    first = me.name.split()[0] if me.name else ""
    if 15 <= day < 22 and num % 9 == 0:
        return "I'll leave it all for today and see how things look tomorrow."
    if 29 <= day < 36 and num % 8 == 5:
        return _reply(None, [{"op": "write_notes", "text": f"Things to do, list {i}"} for i in range(1, 13)])
    acts: list = []
    close = me.known("partner", "child", "parent", "brother", "sister", "family", "friend")
    friends = me.known("friend")
    for rq, sender, cents in me.requests:
        yes = rng.random() < 0.8 and (cents == 0 or cents * 3 <= me.balance)
        acts.append({"op": "answer", "request": rq, "answer": "accept" if yes else "decline",
                     "note": "Glad to help." if yes else "Not this time, sorry."})
    if me.arrears and me.balance >= 500:
        cents, payee, memo, ob = me.arrears[0]
        acts.append({"op": "pay", "to": payee, "amount": _dollars(min(cents, me.balance // 2)), "for": ob,
                     "note": f"towards the {memo}"})
    spending = [c for c in me.coming if c[2] == "regular spending"]
    if 7 <= day < 14 and spending and me.balance > 100_000 and rng.random() < 0.3:
        acts.append({"op": "pay", "to": spending[0][1], "amount": _dollars(me.balance * 85 // 100),
                     "note": "a new three-piece suite"})
    mine = [line.split(":")[0] for line in me.owed if _STANDING_NOTE in line and " to " in line]
    if mine and day >= 35 and rng.random() < 0.5:
        acts.append({"op": "stop", "id": mine[0]})
    elif not mine and 7 <= day < 28 and close and me.after > 20_000 and rng.random() < 0.2:
        acts.append({"op": "standing_payment", "to": rng.choice(close), "amount": 15, "day": "Friday",
                     "weeks": 0, "note": _STANDING_NOTE})
    roll = rng.random()
    if roll < 0.08 and friends:
        acts.append({"op": "message", "to": rng.choice(friends).split()[0],
                     "text": f"Are you about on Saturday? It has been a while. {first}"})
    elif roll < 0.16 and close:
        acts.append({"op": "message", "to": rng.choice(close), "text": "How are things with you this week?"})
    elif roll < 0.21 and me.places:
        acts.append({"op": "message", "to": rng.choice(me.places), "text": "Will you have eggs in on Thursday?"})
    elif roll < 0.24:
        acts.append({"op": "message", "to": _NO_ONE, "text": "Is there a plot free at the allotments?"})
    roll = rng.random()
    if roll < 0.08 and close:
        acts.append({"op": "request", "to": rng.choice(close), "text": "Could you give me a hand on Saturday morning?",
                     "amount": 0})
    elif roll < 0.25 and friends and (me.after < 0 or me.balance < 30_000):
        acts.append({"op": "request", "to": rng.choice(friends), "text": "Could you lend me $50 until payday?",
                     "amount": 50})
    debtors = me.known("owes you money")
    owed_me = [m for line in me.owed if (m := _OWED_TO_ME.match(line))]
    if debtors and owed_me and rng.random() < 0.15:
        ob, memo, amount = owed_me[0].groups()
        acts.append({"op": "request", "to": debtors[0], "text": f"Could you pay this week's {memo} early?",
                     "amount": _dollars(_cents(amount)), "for": ob})
    staff = me.known("works for you")
    if me.business and staff and rng.random() < 0.12:
        acts.append({"op": "pay", "to": rng.choice(staff), "amount": 20, "note": "a thank-you for the extra effort",
                     "from": me.business})
    roll = rng.random()
    if roll < 0.06:
        acts.append({"op": "read", "what": "account in full"})
    elif roll < 0.10:
        acts.append({"op": "read", "what": "account"})
    if rng.random() < 0.15:
        acts.append({"op": "write_notes", "text": f"Day {day}: the account held ${me.balance / 100:,.2f}."})
    if 22 <= day < 25 and num % 7 == 3:
        acts += [{"op": "pay", "to": 7, "amount": "lots"}, {"op": "message", "to": ["someone"], "text": None},
                 {"amount": 5}]
    aims = None
    if not me.minded:
        aims = ["Keep up with the bills", f"See {close[0].split()[0]} soon" if close else "Get out more"]
    elif rng.random() < 0.1:
        aims = ["Keep up with all the bills", "Put a little by each week" if me.after > 0 else "Spend less on extras"]
    return _reply(aims, acts)
