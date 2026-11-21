import pytest

from townsim.econ.ledger import Overdraft
from townsim.town import obligations as O

from town.helpers import SETUP, make_rent, received, run_days, to, town


def unchanged(s):
    return s.hash(), len(s.changes)


def test_rent_paid_by_hand_is_not_taken_again():
    s = town(h1=50_000); rent = make_rent(s, cents=17_500, first=7, every=7)
    run_days(s, 5)
    m = O.pay(s, "h1", "outside:lettings", 17_500, "rent early", "op:p1:pay:d5.0")
    assert [x.cents for x in m] == [17_500]
    assert s.get("dues", m[0].due).obligation == rent
    run_days(s, 4)                                   # through day 8
    assert received(s, "outside:lettings") == 17_500  # once
    [paid] = to(s, "outside:lettings")
    assert paid.memo == "rent early (counted toward rent due Monday 9 March)" and paid.cause == "op:p1:pay:d5.0"


def test_part_paid_by_hand_leaves_the_rest_to_the_schedule():
    s = town(h1=50_000); make_rent(s, cents=17_500, first=7, every=7); run_days(s, 5)
    O.pay(s, "h1", "outside:lettings", 5_000, "toward rent", "op:p1:pay:d5.0"); run_days(s, 4)
    assert received(s, "outside:lettings") == 17_500 and [t.cents for t in to(s, "outside:lettings")] == [5_000, 12_500]


def test_a_surplus_is_an_ordinary_payment_and_never_a_hidden_credit():
    s = town(h1=90_000); make_rent(s, cents=17_500, first=7, every=7); run_days(s, 5)
    m = O.pay(s, "h1", "outside:lettings", 40_000, "a lot", "op:p1:pay:d5.0")
    assert sum(x.cents for x in m) == 17_500
    run_days(s, 11)                                  # through day 15: the day-14 instalment is still taken in full
    assert received(s, "outside:lettings") == 40_000 + 17_500


def test_arrears_are_matched_before_the_next_instalment():
    s = town(h1=0); make_rent(s, cents=17_500, first=0, every=7); run_days(s, 1)
    [late] = s.all("dues"); assert late.status == "arrears"
    s.transfer("outside:employers", "h1", 20_000, "wages", "routine:test")
    m = O.pay(s, "h1", "outside:lettings", 20_000, "catching up", "op:p1:pay:d1.0")
    assert [x.cents for x in m] == [17_500, 2_500] and m[0].due == late.id
    assert late.status == "paid" and late.paid == 17_500
    nxt = s.get("dues", m[1].due); assert (nxt.day, nxt.paid, nxt.status) == (7, 2_500, "due")
    assert to(s, "outside:lettings")[-1].memo == (
        "catching up (counted toward rent due Monday 2 March; rent due Monday 9 March)")
    s.transfer("outside:employers", "h1", 15_000, "wages", "routine:test"); run_days(s, 7)
    assert [t.cents for t in to(s, "outside:lettings")] == [20_000, 15_000] and nxt.status == "paid"


def test_a_payment_to_someone_who_is_owed_nothing_matches_nothing():
    s = town(h1=50_000); assert O.pay(s, "h1", "h2", 2_000, "a gift", "op:p1:pay:d0.0") == []
    assert to(s, "h2")[-1].memo == "a gift"


def test_money_owed_the_other_way_is_not_counted():
    s = town(h1=50_000, h2=50_000)
    O.create(s, kind="loan", payer="h2", payee="h1", cents=2_000, first=3, memo="loan", ends="both", **SETUP)
    assert O.pay(s, "h1", "h2", 2_000, "a gift", "op:p1:pay:d0.0") == [] and s.all("dues") == []


def test_only_one_future_instalment_and_ties_go_by_kind():
    s = town(h1=90_000)
    O.create(s, kind="bill", payer="h1", payee="outside:lettings", cents=4_000, first=7, every=7, memo="water", ends="service", **SETUP)
    rent = make_rent(s, cents=17_500, first=7, every=7)
    m = O.pay(s, "h1", "outside:lettings", 30_000, "both", "op:p1:pay:d0.0")
    assert [(s.get("dues", x.due).obligation, x.cents) for x in m] == [(rent, 17_500)]
    m = O.pay(s, "h1", "outside:lettings", 1_000, "again", "op:p1:pay:d0.1")
    assert [(s.get("dues", x.due).obligation, s.get("dues", x.due).day) for x in m] == [("ob1", 7)]


def test_a_second_payment_moves_on_to_the_instalment_after():
    s = town(h1=50_000); make_rent(s, cents=17_500, first=7, every=7)
    O.pay(s, "h1", "outside:lettings", 17_500, "one", "op:p1:pay:d0.0")
    m = O.pay(s, "h1", "outside:lettings", 17_500, "two", "op:p1:pay:d0.1")
    assert s.get("dues", m[0].due).day == 14


def test_ref_counts_toward_that_obligation_only():
    s = town(h1=50_000)
    make_rent(s, cents=17_500, first=7, every=7)
    water = O.create(s, kind="bill", payer="h1", payee="outside:lettings", cents=4_000, first=3, every=7, memo="water", ends="service", **SETUP)
    m = O.pay(s, "h1", "outside:lettings", 4_000, "water", "op:p1:pay:d0.0", ref=water)
    assert [(s.get("dues", x.due).obligation, s.get("dues", x.due).day) for x in m] == [(water, 3)]


def test_a_bad_ref_changes_nothing():
    s = town(h1=50_000, h2=50_000)
    theirs = make_rent(s, cents=17_500, first=7, payer="h2")
    before = unchanged(s)
    for ref in ("ob99", theirs):
        with pytest.raises(ValueError):
            O.pay(s, "h1", "outside:lettings", 1_000, "x", "op:p1:pay:d0.0", ref=ref)
    assert unchanged(s) == before


def test_a_payment_that_cannot_be_covered_changes_nothing():
    s = town(h1=1_000); make_rent(s, cents=17_500, first=7)
    before = unchanged(s)
    with pytest.raises(Overdraft):
        O.pay(s, "h1", "outside:lettings", 1_001, "x", "op:p1:pay:d0.0")
    with pytest.raises(ValueError):
        O.pay(s, "h1", "h1", 100, "x", "op:p1:pay:d0.0")             # no payment to oneself
    for bad in (0, -5, 10.0, True):
        with pytest.raises(ValueError):
            O.pay(s, "h1", "outside:lettings", bad, "x", "op:p1:pay:d0.0")
    assert unchanged(s) == before


def test_a_defaulted_due_is_never_counted_toward():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    run_days(s, 29)
    s.transfer("outside:employers", "h1", 10_000, "wages", "routine:test")
    m = O.pay(s, "h1", "outside:northgrid", 4_800, "energy", "op:p1:pay:d29.0")
    energy, fee = s.all("dues")
    assert energy.status == "defaulted" and energy.paid == 0
    assert m == [O.Match(fee.id, 2_500)] and fee.status == "paid"     # only the late fee, still in arrears


def test_paying_a_once_only_obligation_by_hand_finishes_it():
    s = town(h1=50_000)
    ob = O.create(s, kind="fee", payer="h1", payee="outside:lettings", cents=1_000, first=2, memo="key", ends="payer", **SETUP)
    O.pay(s, "h1", "outside:lettings", 1_000, "key", "op:p1:pay:d0.0")
    assert s.get("obligations", ob).status == "finished"
    run_days(s, 5); assert received(s, "outside:lettings") == 1_000


def test_stop_says_what_happened():
    s = town(h1=50_000); s.open_account("outside:club", "routine:setup")
    sp = O.create(s, kind="standing", payer="h1", payee="h2", cents=2_000, first=2, every=7, memo="to Megan", ends="payer", made_by="p1", cause="op:p1:standing_payment:d0.0")
    fee = O.create(s, kind="fee", payer="h1", payee="outside:club", cents=500, first=1, every=7, memo="club", ends="service", made_by="p1", cause="routine:setup")
    plan = O.create(s, kind="agreement", payer="h1", payee="h2", cents=2_000, first=3, every=7, last=31, memo="repaying the $800", ends="both", made_by="p1", cause="routine:setup")
    assert O.stop(s, sp, "h1", "op:p1:stop:d0.1") == "stopped" and O.stop(s, fee, "h1", "op:p1:stop:d0.2") == "left"
    assert O.stop(s, plan, "h1", "op:p1:stop:d0.3") == "breach" and s.all("records")[-1].kind == "breach"
    rec = s.all("records")[-1]
    assert rec.about == "h1" and rec.text == "Ashby stopped paying repaying the $800, an agreement with Brook."
    assert [s.get("obligations", o).status for o in (sp, fee, plan)] == ["stopped", "stopped", "breached"]
    assert s.changes[-1].cause == "op:p1:stop:d0.3"
    with pytest.raises(ValueError): O.stop(s, sp, "h1", "op:p1:stop:d0.4")     # already stopped
    run_days(s, 10); assert to(s, "h2") == [] and to(s, "outside:club") == []


def test_stop_refuses_anyone_but_the_payer_and_changes_nothing():
    s = town(h1=50_000)
    ob = make_rent(s, cents=17_500, first=7)
    before = unchanged(s)
    for by, ob_id in (("h2", ob), ("outside:lettings", ob), ("h1", "ob99")):
        with pytest.raises(ValueError):
            O.stop(s, ob_id, by, "op:p3:stop:d0.0")
    assert unchanged(s) == before


def test_a_due_already_open_still_stands_after_stop():
    s = town(h1=50_000)
    ob = make_rent(s, cents=17_500, first=0)            # falls today, so its due is open
    assert O.stop(s, ob, "h1", "op:p1:stop:d0.0") == "breach"
    run_days(s, 15)
    assert [t.t for t in to(s, "outside:lettings")] == [0]


def test_name_of():
    s = town()
    assert [O.name_of(s, a) for a in ("h1", "outside:lettings", "p1")] == ["Ashby", "Lettings", "p1"]


def _rent_on_its_day(paid_by_hand):
    """Rent of 17,500 every week from day 7; `paid_by_hand(s)` runs on day 7, then the
    days run through day 15. Returns the store and the days lettings was paid."""
    s = town(h1=50_000); make_rent(s, cents=17_500, first=7)
    run_days(s, 7)
    paid_by_hand(s)
    s.advance_day(); run_days(s, 8)
    assert s.ledger.total() == 0
    return s, [t.t for t in to(s, "outside:lettings")]


def test_paying_on_the_due_day_before_it_is_opened():
    def act(s):
        m = O.pay(s, "h1", "outside:lettings", 17_500, "rent", "op:p1:pay:d7.0")
        assert [s.get("dues", x.due).day for x in m] == [7]
        O.open_dues(s); O.age_arrears(s); O.collect(s)
    s, days = _rent_on_its_day(act)
    assert days == [7, 14] and received(s, "outside:lettings") == 2 * 17_500


def test_paying_on_the_due_day_after_it_is_opened_but_before_it_is_collected():
    def act(s):
        O.open_dues(s)
        m = O.pay(s, "h1", "outside:lettings", 17_500, "rent", "op:p1:pay:d7.0")
        assert [s.get("dues", x.due).day for x in m] == [7] and len(s.all("dues")) == 1
        O.age_arrears(s); O.collect(s)
    s, days = _rent_on_its_day(act)
    assert days == [7, 14] and received(s, "outside:lettings") == 2 * 17_500


def test_paying_on_the_due_day_after_it_was_collected_counts_toward_the_next():
    def act(s):
        O.open_dues(s); O.age_arrears(s); O.collect(s)
        m = O.pay(s, "h1", "outside:lettings", 17_500, "rent", "op:p1:pay:d7.0")
        assert [s.get("dues", x.due).day for x in m] == [14]
    s, days = _rent_on_its_day(act)
    assert days == [7, 7] and received(s, "outside:lettings") == 2 * 17_500
