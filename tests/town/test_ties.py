"""Ties to an employer, staff, a landlord or tenant and those owed or owing are read from the
records as they stand on the day; family and friendship stay as made."""

import functools
import pickle

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import gen, ties
from townsim.town import obligations as O
from townsim.town.acts import Act, apply_act, knows
from townsim.town.facts import people_known, who
from townsim.town.loop import close, routine

FAMILIES = ["a", "b", "c", "d"]


@functools.cache
def _frozen(seed: int) -> bytes:
    return pickle.dumps(gen.generate(seed, FAMILIES))


def place(seed: int = 1):
    return pickle.loads(_frozen(seed))


def words_for(s, pid, other):
    return next((w for o, _, w in people_known(s, pid) if o == other), None)


def live(s, n):
    for _ in range(n):
        routine(s)
        close(s)


def test_after_someone_leaves_a_shop_neither_is_shown_the_other_as_staff_or_employer():
    s = place(1)
    shop = next(o for o in s.all("orgs") if o.kind == "shop" and len(s.where("jobs", org=o.id)) > 2)
    staff = next(j.person for j in s.where("jobs", org=shop.id) if j.person != shop.owner)
    assert "your employer" in words_for(s, staff, shop.owner)
    assert "works for you" in words_for(s, shop.owner, staff)
    out = apply_act(s, Act(shop.owner, shop.owner, "dismiss", {"who": s.get("people", staff).name}), 0)
    assert out.ok, out.msg
    live(s, 7)
    mine, theirs = words_for(s, staff, shop.owner), words_for(s, shop.owner, staff)
    assert mine is None or "your employer" not in mine
    assert "works for you" not in theirs and theirs.startswith("used to work for you until Friday 6 March")
    staff_lines = [line for line in who(s, shop.owner).split("\n") if line.startswith("Also working at")]
    assert all(s.get("people", staff).name not in line for line in staff_lines)
    for other in [j.person for j in s.where("jobs", org=shop.id) if j.person not in (staff, shop.owner)]:
        assert "works with you" not in (words_for(s, staff, other) or "")


def test_a_debt_paid_off_is_no_longer_a_tie():
    s = place(1)
    ob = next(o for o in s.all("obligations") if o.kind == "agreement" and s.has("households", o.payer))
    debtor = ob.made_by
    creditor = next(p for p in s.get("households", ob.payee).members if s.get("people", p).age >= 18)
    assert "creditor" in ties.kinds(s, debtor)[creditor] and "debtor" in ties.kinds(s, creditor)[debtor]
    O.repay(s, ob.id, O.outstanding(s, ob), "routine:test") if s.balance(ob.payer) >= O.outstanding(s, ob) else \
        s.set("obligations", ob.id, "routine:test", status="finished")
    assert "creditor" not in ties.kinds(s, debtor).get(creditor, [])
    assert "debtor" not in ties.kinds(s, creditor).get(debtor, [])


def test_a_loan_makes_the_banker_a_tie_and_its_end_unmakes_it():
    s = place(1)
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    loan = next(o for o in s.where("obligations", payee=bank.id) if o.kind == "loan" and s.has("households", o.payer))
    borrower = s.get("households", loan.payer).members[0]
    assert "banker" in ties.kinds(s, borrower)[bank.contact]
    assert "has a loan from the bank" in words_for(s, bank.contact, borrower)
    for o in s.where("obligations", payer=loan.payer):
        if o.payee == bank.id:
            s.set("obligations", o.id, "routine:test", status="finished")
    assert "banker" not in ties.kinds(s, borrower).get(bank.contact, [])


def test_siblings_are_brothers_or_sisters_and_never_both():
    seen = 0
    for seed in range(1, 13):
        s = place(seed)
        for p in s.all("people"):
            for other, _, words in people_known(s, p.id):
                assert "brother or sister" not in words
                if "sibling" in ties.kinds(s, p.id).get(other, []):
                    seen += 1
                    first = s.get("people", other).name.split()[0]
                    from townsim.town import names
                    expected = "your sister" if first in names.WOMEN else "your brother"
                    assert expected in words, (words, first)
            assert "brother or sister" not in who(s, p.id)
    assert seen


def test_family_and_friends_stay_as_made_and_everyone_tied_is_known():
    s = place(1)
    for p in s.all("people"):
        found = ties.kinds(s, p.id)
        for t in s.all("ties"):
            if t.person == p.id:
                assert t.kind in ties.PERSONAL and t.kind in found[t.other]
        assert set(found) <= set(knows(s, p.id))
