"""What you owe and what is owed to you, each line with whom, when it was last paid and when it
is next due; an owner's week of money; money a partner moved told as theirs."""

import functools
import pickle

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import facts
from townsim.town.acts import Act, apply_act
from townsim.town.loop import routine
from townsim.town.obligations import money
from townsim.town.views import person_view

from .helpers import generated


@functools.cache
def _frozen() -> bytes:
    return pickle.dumps(generated(1))


def place():
    return pickle.loads(_frozen())


def section(text, head):
    for part in text.split("\n\n"):
        lines = part.split("\n")
        if lines[0] == head:
            return lines[1:]
    return None


def days(s, n):
    for _ in range(n):
        routine(s)
        s.advance_day()


def test_a_debt_between_households_reads_from_each_side_with_who_agreed_it():
    s = place()
    debt = next(o for o in s.all("obligations") if o.kind == "agreement" and s.has("households", o.payer)
                and s.has("households", o.payee))
    debtor, creditor = s.get("people", debt.made_by), s.get("people", debt.party)
    days(s, 9)
    routine(s)
    owe = section(person_view(s, debtor.id)[1]["content"], "YOU OWE")
    owed = section(person_view(s, creditor.id)[1]["content"], "OWED TO YOU")
    mine = next(x for x in owe if x.startswith(f"{debt.id}: "))
    theirs = next(x for x in owed if x.startswith(f"{debt.id}: "))
    home = lambda p: s.get("households", p.household).name
    assert f"{money(debt.cents)} a week to {creditor.name} ({home(creditor)})" in mine
    assert f"{money(debt.cents)} a week from {debtor.name} ({home(debtor)})" in theirs
    paid = [d for d in s.where("dues", obligation=debt.id, status="paid")]
    nxt = facts.next_instalment(s, debt)
    for line in (mine, theirs):
        assert f"; next due {facts.date_text(nxt)}" in line and f" until {facts.ends_on(debt)};" in line
        assert ("; last paid " + facts.date_text(max(d.day for d in paid))) in line if paid else "; nothing paid yet" in line


def test_an_owner_sees_each_account_under_its_own_heading_and_what_the_business_expects_in():
    s = place()
    firm = next(o for o in s.all("orgs") if o.kind == "firm")
    days(s, 9)
    routine(s)
    user = person_view(s, firm.owner)[1]["content"]
    owed = section(user, "OWED TO YOU")
    assert f"{firm.name}:" in owed
    investment = owed[owed.index(f"{firm.name}:") + 1]
    assert investment.startswith("ob") and " a week from " in investment and "; last paid " in investment
    business = section(user, "YOUR BUSINESS")
    at = next(i for i, x in enumerate(business) if x.startswith("Expected in by "))
    assert "investment from" in business[at + 1]
    owe = section(user, "YOU OWE")
    assert owe[0] == "Your household:"


def test_last_weeks_money_counts_only_what_moved_that_week():
    s = place()
    shop = next(o for o in s.all("orgs") if o.kind == "shop")
    days(s, 9)
    routine(s)
    line = next(x for x in section(person_view(s, shop.owner)[1]["content"], "YOUR BUSINESS")
                if x.startswith("Last week's money: "))
    took = sum(x.cents for x in s.ledger.transfers if x.dst == shop.id and x.cause == "routine:shopping" and x.t < 7)
    out = sum(x.cents for x in s.ledger.transfers if x.src == shop.id and x.t < 7)
    assert line.startswith(f"Last week's money: took {money(took)}, paid out {money(out)} (")
    assert line.endswith(f"{money(abs(took - out))} {'over' if took >= out else 'short'}")
    assert "opening" not in line and "more came in" not in line


def test_money_a_partner_moved_from_their_business_is_told_as_theirs():
    s = place()
    firm = next(o for o in s.all("orgs") if o.kind == "firm")
    owner = s.get("people", firm.owner)
    other = next(p for p in s.get("households", owner.household).members if p != owner.id)
    out = apply_act(s, Act(owner.id, owner.id, "move_money", {"from": firm.name, "to": "household", "amount": 1000}), 0)
    assert out.ok
    theirs = section(person_view(s, other)[1]["content"], "YOUR HOUSEHOLD'S ACCOUNT")
    mine = section(person_view(s, owner.id)[1]["content"], "YOUR HOUSEHOLD'S ACCOUNT")
    assert any(x.endswith(f"$1,000.00  {owner.name} moved $1,000.00 from {firm.name}") for x in theirs)
    assert any(x.endswith(f"$1,000.00  {firm.name}  moved between your own accounts") for x in mine)


# a change to rent says what will be taken, and a second payment to the same payee says the totals

def _agency_tenant(s):
    from townsim.town import facts as F
    for h in s.all("households"):
        rent = [o for o in F.rents(s, h.id) if s.has("orgs", o.payee) and s.get("orgs", o.payee).outside]
        if rent:
            adult = next(p for p in h.members if s.get("people", p).age >= 18)
            return h, rent[0], adult
    raise AssertionError("no agency tenant")


def test_a_second_standing_payment_to_the_landlord_is_told_the_totals_and_owed_lines_say_as_well_as():
    from townsim.town import ops_deals
    from townsim.town.clock import DAYS, date_text, date_year
    s = place()
    h, rent, adult = _agency_tenant(s)
    new = rent.cents + 3_500
    change, _ = ops_deals.change_of_rent(s, h, rent, new)
    ops_deals.make_rent_change(s, change, "routine:t")
    agency = s.get("orgs", rent.payee).name
    notice = [n.text for n in s.where("notices", to=adult)][-1]
    assert notice == (f"Your rent for {h.street} ({rent.id}) changes from {money(rent.cents)} to {money(new)} a week "
                      f"from {date_year(change.day)}. It is taken from the household account each Monday, as now.")
    out = apply_act(s, Act(adult, adult, "standing_payment", {"to": agency, "amount": new / 100, "day": "Monday",
                                                              "weeks": 0, "note": "rent"}), 0)
    ob = s.all("obligations")[-1]
    first = ob.first
    assert out.msg == (f"Set up ({ob.id}): {money(new)} to {agency} each Monday from {date_text(first)}. "
                       f"{agency} also takes rent for {h.street} ({rent.id}), {money(rent.cents)} each Monday "
                       f"({money(new)} from {date_text(change.day)}). Together {money(rent.cents + new)} leaves each "
                       f"Monday ({money(2 * new)} from {date_text(change.day)}).")
    owe = section(person_view(s, adult)[1]["content"], "YOU OWE")
    assert any(x.startswith(f'{ob.id}: "rent", {money(new)} a week to {agency} (as well as {rent.id}) until stopped')
               for x in owe)
    assert DAYS[first % 7] == "Monday"


def test_a_proposed_agreement_to_someone_already_paid_says_the_totals_it_would_make():
    s = place()
    debt = next(o for o in s.all("obligations") if o.kind == "agreement" and s.has("households", o.payer)
                and s.has("households", o.payee))
    debtor, creditor = s.get("people", debt.made_by), s.get("people", debt.party)
    out = apply_act(s, Act(debtor.id, debtor.id, "propose_agreement", {
        "to": creditor.name, "amount": 7, "per": "week", "weeks": 4, "payer": "me", "what": "eggs"}), 0)
    assert out.ok, out.msg
    payee = s.get("households", creditor.household).name
    assert f" {payee} also takes paying back {creditor.name} ({debt.id}), {money(debt.cents)} each " in out.msg
    assert f"Together {money(debt.cents + 700)} would leave each " in out.msg


def test_a_change_of_pay_names_the_wage_and_how_it_is_paid():
    s = place()
    shop = next(o for o in s.all("orgs") if o.kind == "shop")
    from townsim.town import jobs
    staff = next(j for j in jobs.held(s, org=shop.id) if j.person != shop.owner)
    name = s.get("people", staff.person).name
    out = apply_act(s, Act(shop.owner, shop.owner, "set_staff_pay", {"who": name, "weekly_pay": staff.weekly / 100 + 10}),
                    0)
    assert out.ok
    told = [n.text for n in s.where("notices", to=staff.person)][-1]
    assert told.endswith(f"({staff.wage_ob}). It is paid into the household account each Friday, as now.")
