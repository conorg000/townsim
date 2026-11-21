"""The generated hamlet's days: the ordinary events dealt when it was made, applied each on
its day. (A scripted life that plays a generated place is in `hamlet_script`.)

`deal` draws the deck when the place is made: for every week of the run, each person,
household, outside worker, supplier and the place itself has the chance its kind of event has
in a week (the base rates below), drawn from a stream seeded with the place's seed alone, so
nothing in it is aimed at anyone. No household has more than three events.

`apply_events` is part of the morning routine. Each event is applied once, on its day, with
the cause `routine:event:<event id>`, and told to those it falls on in a notice that says
what happened and nothing else. An event naming a person, household or supplier that does
not exist is noted in `store.engine_errors` and marked done.
"""

from __future__ import annotations

import random

from townsim.town import jobs, obligations
from townsim.town.clock import DAY_ZERO, date_text, dow, week
from townsim.town.entities import Event, Notice
from townsim.town.obligations import money
from townsim.town.store import Store

# Base rates: the chance a week, for each one it can fall on.
ILLNESS = 0.025          # a person: about 1.3 weeks off sick a year, near the share of working time lost to sickness
JOB_LOST = 0.003         # an outside worker: about one in seven a year loses a job at an outside employer
HOURS_CUT = 0.004        # an outside worker: short-time working, a little more common than losing the job
REPAIR = 0.012           # a household: a repair costing over $150 about every year and a half
SUPPLIER_SHORT = 0.04    # a supplier: says a week's delivery will be short about twice a year
SUPPLIER_PRICE = 0.03    # a supplier: changes its price list about one and a half times a year
ENERGY_PRICE = 1 / 13    # the energy supplier: its prices are reviewed each quarter
AGENCY_RENT = 0.02       # a household renting from an agency: about one rise a year
COLD_MONTH = 0.05        # the place, in a week of November to March: a cold spell now and then
EVENTS_A_HOUSEHOLD = 3   # the most events a household is dealt
RUN_WEEKS = 21           # the weeks a deck is dealt for, unless the place is made for another length
COLD_MONTHS = (11, 12, 1, 2, 3)

REPAIRS = ("boiler", "roof", "back door", "windows", "drains", "wiring", "gutters", "chimney")
_PRICE_RANGE = (-8, 12)            # percent a supplier's price changes by, never 0
_ENERGY_RANGE = (-10, 15)          # percent energy prices change by, never 0
_RENT_RANGE = (3, 8)               # percent an agency raises a rent by
_CUT_RANGE = (15, 35)              # percent of their hours an outside employer cuts
_REPAIR_CENTS = (15_000, 90_000)
NOTICE_DAYS = 28                   # an energy price changes on the first bill day this far on

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


def _outside_job(store: Store, e: Event) -> tuple:
    """The job at an outside employer the event falls on, or None and why (None for one who has
    left, or is leaving, the job it was dealt for: they have nothing to lose)."""
    if not store.has("people", e.who):
        return None, "no person"
    held = [j for j in jobs.held(store, person=e.who) if store.has("orgs", j.org) and store.get("orgs", j.org).outside
            and j.ended == -1]
    if held:
        return held[0], None
    return None, None if store.where("jobs", person=e.who) else "no job at an outside employer for"


def _job_lost(store: Store, e: Event, cause: str) -> str | None:
    job, why = _outside_job(store, e)
    if job is None:
        return why
    last = jobs.last_friday(e.day)        # this week's Friday, or at a weekend the Friday just gone
    jobs.finish(store, job.id, last, cause)
    _tell(store, [e.who], f"{store.get('orgs', job.org).name} has ended your job. "
                          f"Your last pay {jobs.last_pay_text(last, store.day)}.", cause)
    return None


def _hours_cut(store: Store, e: Event, cause: str) -> str | None:
    """An outside employer cuts a job's hours by `amount` percent from next Monday, and its pay in
    proportion from that week's Friday."""
    job, why = _outside_job(store, e)
    if job is None:
        return why
    if not store.has("obligations", job.wage_ob):
        return "no wage for the job of"
    wage = store.get("obligations", job.wage_ob)
    hours = max(4.0, round(job.hours * (100 - e.amount) / 100))
    if hours >= job.hours or wage.status != "active" or wage.last is not None:
        return None
    monday = jobs.next_monday(store.day)
    friday = obligations.next_on_schedule(wage, monday + jobs.FRIDAY)
    weekly = round(obligations.cents_now(store, wage) * hours / job.hours)
    obligations.reprice(store, wage.id, weekly, friday, cause)
    store.set("jobs", job.id, cause, hours=float(hours), weekly=weekly)
    _tell(store, [e.who], f"{store.get('orgs', job.org).name} has cut your hours to {hours:g} a week from "
                          f"{date_text(monday)}. Your pay is {money(weekly)} a week from {date_text(friday)}.", cause)
    return None


def _adults(store: Store, household: str) -> list[str]:
    return [p for p in store.get("households", household).members if store.get("people", p).age >= 18]


def _repair(store: Store, e: Event, cause: str) -> str | None:
    """A repair to a house: billed to the household that owns it, or for a rented house to its
    landlord here, the tenants told it was done and who was billed; a letting agency outside the
    town has it done, and the tenants are told so."""
    if not store.has("households", e.who):
        return "no household"
    h = store.get("households", e.who)
    thing = e.what or "house"
    lord = h.landlord
    if lord and store.has("orgs", lord):
        _tell(store, _adults(store, h.id), f"{store.get('orgs', lord).name} had the {thing} repaired.", cause)
        return None
    payer = store.get("people", lord).household if lord and store.has("people", lord) else \
        lord if lord and store.has("households", lord) else h.id
    spending = [o for o in store.where("obligations", payer=payer) if o.memo == "regular spending"] or \
        [o for o in store.where("obligations", payer=h.id) if o.memo == "regular spending"]
    if not spending:                     # nowhere it has ever bought from: nothing to bill
        return None
    due = store.day + REPAIR_DAYS
    where = h.street or "the house"
    memo = "repair to the house" if payer == h.id else f"repair to the {thing} at {where}"
    obligations.create(store, kind="bill", payer=payer, payee=spending[-1].payee, cents=e.amount, first=due,
                       memo=memo, ends="both", made_by="routine", cause=cause, cannot_stop="work")
    if payer == h.id:
        _tell(store, _adults(store, h.id), f"The {thing if e.what else 'house'} needs a repair costing "
                                           f"{money(e.amount)}. It is due on {date_text(due)}.", cause)
        return None
    _tell(store, _adults(store, payer), f"The {thing} at {where} needs a repair costing {money(e.amount)}. "
                                        f"It is due on {date_text(due)}.", cause)
    name = store.get("people", lord).name if store.has("people", lord) else store.get("households", payer).name
    _tell(store, _adults(store, h.id), f"The {thing} was repaired. The bill, {money(e.amount)}, went to {name}.",
          cause)
    return None


def _buyers_of(store: Store, sid: str) -> list[str]:
    owners: list[str] = []
    for f in store.all("firms"):
        if sid in (f.order_supplier, f.last_supplier) and store.has("orgs", f.org):
            owner = store.get("orgs", f.org).owner
            if owner and owner not in owners:
                owners.append(owner)
    return owners


def _supplier_short(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("suppliers", e.who):
        return "no supplier"
    sp = store.get("suppliers", e.who)
    monday = e.day + (-dow(e.day)) % 7                  # today if a Monday, else the next
    store.set("suppliers", sp.id, cause, short_week=week(monday))
    name = store.get("orgs", sp.org).name if store.has("orgs", sp.org) else sp.org
    when = "this week's delivery" if monday == e.day else f"the delivery on {date_text(monday)}"
    _tell(store, _buyers_of(store, sp.id), f"{name} has said {when} will be short.", cause)
    return None


def _supplier_price(store: Store, e: Event, cause: str) -> str | None:
    """A supplier changes its price list by `amount` percent: today's price and the price it
    drifts back towards."""
    if not store.has("suppliers", e.who):
        return "no supplier"
    sp = store.get("suppliers", e.who)
    price = max(1, round(sp.unit_cents * (100 + e.amount) / 100))
    store.set("suppliers", sp.id, cause, unit_cents=price, base_cents=max(1, round(sp.base_cents * (100 + e.amount) / 100)))
    name = store.get("orgs", sp.org).name if store.has("orgs", sp.org) else sp.org
    _tell(store, _buyers_of(store, sp.id), f"{name} has changed its price from {money(sp.unit_cents)} to "
                                           f"{money(price)} a basket.", cause)
    return None


def _energy_price(store: Store, e: Event, cause: str) -> str | None:
    """The energy supplier changes its prices by `amount` percent, on each household's first
    bill day at least four weeks on, the household told."""
    if not store.has("orgs", e.who):
        return "no utility"
    name = store.get("orgs", e.who).name
    for ob in store.where("obligations", payee=e.who):
        if ob.kind != "bill" or ob.memo != "energy" or ob.status != "active" or ob.every == 0 or ob.last is not None \
                or not store.has("households", ob.payer):
            continue
        day = obligations.next_on_schedule(ob, store.day + NOTICE_DAYS)
        now = obligations.pending_change(store, ob)[0] if obligations.pending_change(store, ob) else ob.cents
        cents = max(100, round(now * (100 + e.amount) / 100))
        if any(d.day >= day and d.status != "void" and (d.status == "paid" or d.paid > cents)
               for d in store.where("dues", obligation=ob.id)):
            continue
        obligations.reprice(store, ob.id, cents, day, cause)
        _tell(store, _adults(store, ob.payer), f"{name} is changing its prices: your energy bill is "
                                               f"{money(cents)} a week from {date_text(day)}.", cause)
    return None


def _agency_rent(store: Store, e: Event, cause: str) -> str | None:
    """A letting agency outside the town raises the household's rent by `amount` percent, by the
    rule a landlord here follows (`ops_deals.change_of_rent`), the tenants told in its words."""
    from townsim.town import facts, ops_deals          # the operations read this module's routine
    if not store.has("households", e.who):
        return "no household"
    h = store.get("households", e.who)
    rents = [o for o in facts.rents(store, h.id) if store.has("orgs", o.payee) and store.get("orgs", o.payee).outside
             and o.every > 0]
    if not rents:
        return None                      # no longer renting from an agency: nothing to raise
    ob = rents[0]
    base = obligations.pending_change(store, ob)[0] if obligations.pending_change(store, ob) else ob.cents
    change, _ = ops_deals.change_of_rent(store, h, ob, round(base * (100 + e.amount) / 100 / 100) * 100)
    if change is not None:
        ops_deals.make_rent_change(store, change, cause)
    return None


def _cold_month(store: Store, e: Event, cause: str) -> str | None:
    if not store.has("orgs", e.who):
        return "no utility"
    for ob in store.where("obligations", payee=e.who):
        if ob.kind != "bill" or ob.memo != "energy" or ob.status != "active" or not store.has("households", ob.payer):
            continue
        bills = obligations.instalment_days(ob, store.day, e.weeks)
        for day in bills:
            extra = (obligations.instalment_cents(ob, day) * e.amount + 50) // 100
            obligations.create(store, kind="bill", payer=ob.payer, payee=ob.payee, cents=extra, first=day,
                               memo="extra energy, cold spell", ends="service", made_by="routine", cause=cause,
                               cannot_stop="energy")
        if bills:
            _tell(store, _adults(store, ob.payer),
                  f"A cold spell: energy bills are {e.amount}% higher for {_weeks(len(bills))}.", cause)
    return None


_APPLY = {"illness": _illness, "job_lost": _job_lost, "hours_cut": _hours_cut, "repair": _repair,
          "supplier_short": _supplier_short, "supplier_price": _supplier_price, "energy_price": _energy_price,
          "agency_rent": _agency_rent, "cold_month": _cold_month}
KINDS = tuple(_APPLY)


def apply_events(store: Store) -> None:
    """Apply each event due today and not yet done, in id order, each in full or not at all."""
    for e in store.all("events"):
        if e.done or e.day != store.day:
            continue
        cause = f"routine:event:{e.id}"
        apply = _APPLY.get(e.kind)
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


# --- the deck -------------------------------------------------------------------------------


def _signed(rng: random.Random, low: int, high: int) -> int:
    return rng.choice([x for x in range(low, high + 1) if x != 0])


def _cold(day: int) -> bool:
    from datetime import timedelta
    return (DAY_ZERO + timedelta(days=day)).month in COLD_MONTHS


def chances(store: Store) -> list[tuple[str, str, str, float]]:
    """Everything the deck draws for each week, in a fixed order: (kind, who, household or "",
    the chance a week)."""
    out = []
    people = store.all("people")
    for p in people:
        out.append(("illness", p.id, p.household, ILLNESS))
    for p in people:
        if any(store.get("orgs", j.org).outside for j in store.where("jobs", person=p.id) if store.has("orgs", j.org)):
            out += [("job_lost", p.id, p.household, JOB_LOST), ("hours_cut", p.id, p.household, HOURS_CUT)]
    for h in store.all("households"):
        out.append(("repair", h.id, h.id, REPAIR))
        if h.landlord and store.has("orgs", h.landlord) and store.get("orgs", h.landlord).outside:
            out.append(("agency_rent", h.id, h.id, AGENCY_RENT))
    for sp in store.all("suppliers"):
        out += [("supplier_short", sp.id, "", SUPPLIER_SHORT), ("supplier_price", sp.id, "", SUPPLIER_PRICE)]
    for org in store.all("orgs"):
        if org.kind == "utility":
            out += [("energy_price", org.id, "", ENERGY_PRICE), ("cold_month", org.id, "", COLD_MONTH)]
    return out


def deal(store: Store, rng: random.Random, weeks: int = RUN_WEEKS) -> list[Event]:
    """The deck for `weeks` weeks from today: each week, each chance drawn once, in order; a
    household that has three events is dealt no more. Returns the events, by day."""
    dealt: dict[str, int] = {}
    found = []
    for w in range(weeks):
        for kind, who, household, chance in chances(store):
            hit, day = rng.random(), store.day + 7 * w + rng.randrange(7)
            if kind == "cold_month" and not _cold(store.day + 7 * w):
                continue
            if hit >= chance or (household and dealt.get(household, 0) >= EVENTS_A_HOUSEHOLD):
                continue
            if household:
                dealt[household] = dealt.get(household, 0) + 1
            amount, length, what = 0, 1, ""
            if kind == "repair":
                amount, what = rng.randrange(_REPAIR_CENTS[0], _REPAIR_CENTS[1] + 1, 500), rng.choice(REPAIRS)
            elif kind == "hours_cut":
                amount = rng.randrange(_CUT_RANGE[0], _CUT_RANGE[1] + 1, 5)
            elif kind == "supplier_price":
                amount = _signed(rng, *_PRICE_RANGE)
            elif kind == "energy_price":
                amount = _signed(rng, *_ENERGY_RANGE)
            elif kind == "agency_rent":
                amount = rng.randint(*_RENT_RANGE)
            elif kind == "cold_month":
                amount, length = 30, 4
            elif kind in ("job_lost",):
                length = 0
            found.append((day, kind, who, amount, length, what))
    return [Event("", day, kind, who, amount, length, what=what) for day, kind, who, amount, length, what in sorted(found)]


def household_of(store: Store, e: Event) -> str:
    """The household an event falls on, or "" for one that falls on no household."""
    if store.has("people", e.who):
        return store.get("people", e.who).household
    return e.who if store.has("households", e.who) else ""
