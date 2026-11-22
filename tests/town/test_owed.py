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
