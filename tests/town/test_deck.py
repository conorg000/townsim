"""The place over the whole run: repairs to rented houses are the landlord's, an agency raises
rents as a landlord here does, prices change, hours are cut, and what is settled is told."""

import functools
import pickle

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import gen, hamlet, jobs
from townsim.town import obligations as O
from townsim.town.acts import Act, apply_act
from townsim.town.entities import Event
from townsim.town.loop import close, routine

from .helpers import SETUP, run_days, town

FAMILIES = ["a", "b", "c", "d"]
T = "routine:test"


@functools.cache
def _frozen() -> bytes:
    return pickle.dumps(gen.generate(1, FAMILIES))


def place():
    s = pickle.loads(_frozen())
    for e in s.all("events"):
        s.set("events", e.id, T, done=True)
    return s


def apply(s, kind, who, amount=0, weeks=1, what=""):
    s.add("events", Event("ev99", s.day, kind, who, amount, weeks, what=what), T)
    mark = s.mark()
    hamlet.apply_events(s)
    assert s.engine_errors == []
    return [s.get("notices", c.id) for c in s.changes[mark:] if c.table == "notices" and c.field == "+"]


def test_a_repair_to_a_house_let_by_a_landlord_here_is_billed_to_the_landlord():
    s = place()
    h = next(h for h in s.all("households") if s.has("people", h.landlord))
    lord = s.get("people", h.landlord)
    told = apply(s, "repair", h.id, 40_000, what="boiler")
    bill = s.all("obligations")[-1]
    assert (bill.payer, bill.cents, bill.kind, bill.cannot_stop) == (lord.household, 40_000, "bill", "work")
    assert bill.memo == f"repair to the boiler at {h.street}"
    mine = {n.to: n.text for n in told}
    tenant = next(p for p in h.members if s.get("people", p).age >= 18)
    assert mine[tenant] == f"The boiler was repaired. The bill, $400.00, went to {lord.name}."
    assert mine[lord.id] == f"The boiler at {h.street} needs a repair costing $400.00. It is due on Monday 9 March."
    assert not [o for o in s.where("obligations", payer=h.id) if o.memo.startswith("repair")]


def test_a_repair_to_a_house_let_by_an_agency_is_the_agency_s_and_the_tenants_are_told():
    s = place()
    h = next(h for h in s.all("households") if s.has("orgs", h.landlord))
    before = len(s.all("obligations"))
    told = apply(s, "repair", h.id, 40_000, what="roof")
    assert len(s.all("obligations")) == before
    assert {n.text for n in told} == {f"{s.get('orgs', h.landlord).name} had the roof repaired."}


def test_an_agency_raises_a_rent_with_four_weeks_notice_in_a_landlord_s_words():
    s = place()
    h = next(h for h in s.all("households") if s.has("orgs", h.landlord))
    rent = next(o for o in s.where("obligations", payer=h.id) if o.kind == "rent")
    old = rent.cents
    told = apply(s, "agency_rent", h.id, 5)
    new = round(old * 1.05 / 100) * 100
    day = O.next_on_schedule(rent, s.day + 28)
    ob = s.get("obligations", rent.id)
    assert (ob.next_cents, ob.next_from) == (new, day) and day - s.day >= 28
    from townsim.town.clock import date_year
    text = (f"Your rent for {h.street} changes from ${old / 100:,.2f} to ${new / 100:,.2f} a week from "
            f"{date_year(day)}.")
    assert {n.text for n in told} == {text}
    assert len([o for o in s.where("obligations", payer=h.id) if o.kind == "rent"]) == 1


def test_a_supplier_changes_its_price_and_its_buyers_are_told():
    s = place()
    sp = s.all("suppliers")[0]
    unit, base = sp.unit_cents, sp.base_cents
    told = apply(s, "supplier_price", sp.id, 10)
    now = s.get("suppliers", sp.id)
    assert now.unit_cents == round(unit * 1.1) and now.base_cents == round(base * 1.1)
    assert all("has changed its price from" in n.text for n in told)


def test_the_energy_price_changes_with_four_weeks_notice():
    s = place()
    utility = next(o for o in s.all("orgs") if o.kind == "utility")
    bills = [(o.id, o.cents) for o in s.where("obligations", payee=utility.id) if o.memo == "energy"]
    told = apply(s, "energy_price", utility.id, -10)
    for ob, cents in bills:
        now = s.get("obligations", ob)
        assert now.next_cents == round(cents * 0.9) and now.next_from - s.day >= 28 and now.cents == cents
    assert told and all("is changing its prices: your energy bill is" in n.text for n in told)


def test_an_outside_employer_cuts_hours_and_pay_from_next_week():
    s = place()
    worker = next(p for p in s.all("people") if p.kind == "outside_worker")
    job = jobs.held(s, person=worker.id)[0]
    hours, weekly = job.hours, job.weekly
    told = apply(s, "hours_cut", worker.id, 25)
    now = s.get("jobs", job.id)
    assert now.hours == round(hours * 0.75) and now.weekly == round(weekly * now.hours / hours)
    wage = s.get("obligations", job.wage_ob)
    assert wage.next_cents == now.weekly and wage.next_from == jobs.next_monday(s.day) + 4
    assert [n.text for n in told][0].startswith(f"{s.get('orgs', job.org).name} has cut your hours to ")


def test_what_is_settled_is_told_to_the_payer_s_people():
    s = town(h1=0)
    rent = O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=10_000, first=0, every=7, memo="rent",
                    ends="both", **SETUP)
    run_days(s, 1)                                               # Monday's rent goes to arrears
    s.transfer("outside:employers", "h1", 5_000, "odd job", T)
    O.pay(s, "h1", "outside:lettings", 5_000, "part", "op:p1:pay:d1.0", ref=rent)
    assert not [n for n in s.all("notices") if n.text == "Rent is up to date."]
    s.transfer("outside:employers", "h1", 5_000, "odd job", T)
    O.pay(s, "h1", "outside:lettings", 5_000, "rest", "op:p1:pay:d1.1", ref=rent)
    told = [n for n in s.all("notices") if n.text == "Rent is up to date."]
    assert sorted(n.to for n in told) == ["p1", "p2"] and {n.id for n in told} <= {
        c.id for c in s.changes if c.cause == "op:p1:pay:d1.1"}
    loan = O.create(s, kind="loan", payer="h1", payee="outside:lettings", cents=1_000, first=2, every=7, last=9,
                    memo="loan for a van", ends="both", **SETUP)
    s.transfer("outside:employers", "h1", 5_000, "odd job", T)
    run_days(s, 10)
    assert [n.text for n in s.all("notices") if n.text.endswith("is paid in full.")] == ["Loan for a van is paid in full."] * 2
    assert s.get("obligations", loan).status == "finished"
