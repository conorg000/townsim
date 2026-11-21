"""The generated hamlet's days: the ordinary events dealt when it was made, applied each on
its day. (A scripted life that plays a generated place is in `hamlet_script`.)

`apply_events` is part of the morning routine. Each event is applied once, on its day, with
the cause `routine:event:<event id>`, and told to those it falls on in a notice that says
what happened and nothing else. An event naming a person, household or supplier that does
not exist is noted in `store.engine_errors` and marked done.
"""

from __future__ import annotations

from townsim.town import jobs, obligations
from townsim.town.clock import date_text, dow, week
from townsim.town.entities import Event, Notice
from townsim.town.obligations import money
from townsim.town.store import Store

SUNDAY = 6
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
    if not held:                 # one who has already left the job it was dealt for has nothing to lose
        return None if store.where("jobs", person=e.who) else "no job at an outside employer for"
    job = held[0]
    last = jobs.last_friday(e.day)        # this week's Friday, or at a weekend the Friday just gone
    jobs.finish(store, job.id, last, cause)
    _tell(store, [e.who], f"{store.get('orgs', job.org).name} has ended your job. "
                          f"Your last pay {jobs.last_pay_text(last, store.day)}.", cause)
    return None


def _adults(store: Store, household: str) -> list[str]:
    return [p for p in store.get("households", household).members if store.get("people", p).age >= 18]


def _repair(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("households", e.who):
        return "no household"
    spending = [o for o in store.where("obligations", payer=e.who) if o.memo == "regular spending"]
    if not spending:                     # nowhere it has ever bought from: nothing to bill
        return None
    due = store.day + REPAIR_DAYS
    obligations.create(store, kind="bill", payer=e.who, payee=spending[-1].payee, cents=e.amount, first=due,
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
        bills = obligations.instalment_days(ob, store.day, e.weeks)
        for day in bills:
            obligations.create(store, kind="bill", payer=ob.payer, payee=ob.payee, cents=extra, first=day,
                               memo="extra energy, cold spell", ends="service", made_by="routine", cause=cause)
        if bills:
            _tell(store, _adults(store, ob.payer),
                  f"A cold spell: energy bills are {e.amount}% higher for {_weeks(len(bills))}.", cause)
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

