import pytest

from townsim.town import obligations as O
from townsim.town.store import Store

from town.helpers import SETUP, run_days, town


def only_due(s: Store):
    dues = s.all("dues")
    assert len(dues) == 1
    return dues[0]


def first_due(s: Store):
    return s.all("dues")[0]


def status_of(s: Store, memo: str) -> str:
    [ob] = [o for o in s.all("obligations") if o.memo == memo]
    [due] = [d for d in s.all("dues") if d.obligation == ob.id]
    return due.status


def test_an_instalment_is_collected_once_on_its_day():
    s = town(h1=60_000)                  # the brief had 50,000, which cannot cover three rents of 17,500
    O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=17_500, first=0, every=7, memo="rent", ends="both", **SETUP)
    run_days(s, 15)
    paid = [t for t in s.ledger.transfers if t.dst == "outside:lettings"]
    assert [t.t for t in paid] == [0, 7, 14] and all(t.cents == 17_500 for t in paid)
    assert len({t.cause for t in paid}) == 3 and s.ledger.total() == 0
    assert paid[1].memo == "rent, due Monday 9 March" and paid[1].cause == "routine:collect:du2"


def test_what_cannot_be_paid_becomes_arrears_and_is_never_part_paid():
    s = town(h1=10_000)
    O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=17_500, first=0, memo="rent", ends="both", **SETUP)
    run_days(s, 1)
    assert s.balance("h1") == 10_000 and only_due(s).status == "arrears"
    s.transfer("outside:employers", "h1", 20_000, "wages", "routine:test"); run_days(s, 1)
    assert only_due(s).status == "paid" and s.balance("h1") == 12_500


def test_the_ladder_fee_at_7_notice_at_14_default_at_28():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    run_days(s, 8)
    fees = [o for o in s.all("obligations") if o.penalty]
    assert len(fees) == 1 and fees[0].cents == 2_500 and "late fee on energy due Monday 2 March" == fees[0].memo
    assert fees[0].ends == "both"                                        # the parent is "service"
    assert [d.day for d in s.all("dues") if d.obligation == fees[0].id] == [7]
    assert [(n.to, n.text) for n in s.all("notices")] == [
        (p, "A late fee of $25.00 has been added to energy, due Monday 2 March, which has not been paid.")
        for p in ("p1", "p2")]
    run_days(s, 7)
    two_weeks = [n for n in s.all("notices") if "two weeks" in n.text]
    assert len(two_weeks) == len(O.people_of(s, "h1")) == 2
    assert two_weeks[0].text == "energy, $48.00, due Monday 2 March, is now two weeks overdue."
    run_days(s, 14)
    assert first_due(s).status == "defaulted" and [r.kind for r in s.all("records")] == ["default"]
    [rec] = s.all("records")
    assert rec.about == "h1" and rec.text == "Ashby defaulted on energy, $48.00, due Monday 2 March."
    assert len([o for o in s.all("obligations") if o.penalty]) == 1      # no fee on a fee, no second fee
    assert s.balance("h1") == 0 and s.ledger.total() == 0


def test_a_defaulted_due_is_not_retried():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    run_days(s, 29)
    s.transfer("outside:employers", "h1", 100_000, "wages", "routine:test"); run_days(s, 1)
    assert first_due(s).status == "defaulted"
    assert [t.memo for t in s.ledger.transfers if t.src == "h1"] == ["late fee on energy due Monday 2 March"]


def test_wages_land_before_anything_is_taken_and_older_debts_go_first():
    s = town(h1=0)
    O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=10_000, first=0, memo="rent", ends="both", **SETUP)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=10_000, first=2, memo="energy", ends="service", **SETUP)
    O.create(s, kind="wage", payer="outside:employers", payee="h1", cents=15_000, first=4, memo="wages", ends="both", **SETUP)
    run_days(s, 5)
    assert s.balance("h1") == 5_000 and status_of(s, "rent") == "paid" and status_of(s, "energy") == "arrears"


def test_upcoming_lists_what_remains_with_dates():
    s = town(h1=0)
    rent = O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=17_500, first=7, every=7, memo="rent", ends="both", **SETUP)
    O.create(s, kind="wage", payer="outside:employers", payee="h1", cents=30_000, first=3, memo="wages", ends="both", **SETUP)
    O.create(s, kind="bill", payer="h2", payee="outside:northgrid", cents=4_800, first=1, memo="not ours", ends="service", **SETUP)
    due = O.ensure_due(s, rent, 7, "routine:test")
    s.set("dues", due.id, "routine:test", paid=5_000)
    before, logged = s.hash(), len(s.changes)
    ups = O.upcoming(s, "h1")
    assert s.hash() == before and len(s.changes) == logged
    assert [(u.day, u.memo, u.cents, u.out, u.other) for u in ups] == [
        (3, "wages", 30_000, False, "outside:employers"),
        (7, "rent", 12_500, True, "outside:lettings"),
    ]
    assert ups[1].obligation == rent
    assert [u.day for u in O.upcoming(s, "h1", ahead=14)] == [3, 7, 14]


def test_upcoming_includes_arrears_whatever_their_day():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    run_days(s, 3)
    ups = O.upcoming(s, "h1")
    assert [(u.day, u.memo, u.cents) for u in ups] == [(0, "energy", 4_800)]
    assert O.upcoming(s, "h2") == []


def test_a_stopped_obligation_opens_no_new_dues_but_an_open_due_still_stands():
    s = town(h1=0)
    ob = O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=10_000, first=0, every=7, memo="rent", ends="both", **SETUP)
    run_days(s, 1)
    s.set("obligations", ob, "routine:test", status="stopped")
    s.transfer("outside:employers", "h1", 50_000, "wages", "routine:test")
    run_days(s, 14)
    assert only_due(s).status == "paid" and s.balance("h1") == 40_000
    assert not O.falls_on(s.get("obligations", ob), 7)


def test_a_once_only_obligation_finishes_when_paid():
    s = town(h1=50_000)
    ob = O.create(s, kind="fee", payer="h1", payee="outside:lettings", cents=1_000, first=2, memo="key", ends="payer", **SETUP)
    run_days(s, 2)
    assert s.get("obligations", ob).status == "active"
    run_days(s, 1)
    assert s.get("obligations", ob).status == "finished" and only_due(s).status == "paid"


def test_an_obligation_with_a_last_day_finishes_on_its_last_instalment():
    s = town(h1=50_000)
    ob = O.create(s, kind="loan", payer="h1", payee="outside:lettings", cents=1_000, first=0, every=7, last=15, memo="loan", ends="both", **SETUP)
    run_days(s, 14)
    assert s.get("obligations", ob).status == "active"
    run_days(s, 10)
    assert s.get("obligations", ob).status == "finished"
    assert [d.day for d in s.all("dues")] == [0, 7, 14]


def test_falls_on_follows_the_schedule():
    s = town()
    ob = s.get("obligations", O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=100, first=3, every=7, last=17, memo="x", ends="service", **SETUP))
    assert [d for d in range(30) if O.falls_on(ob, d)] == [3, 10, 17]
    once = s.get("obligations", O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=100, first=3, memo="y", ends="service", **SETUP))
    assert [d for d in range(30) if O.falls_on(once, d)] == [3]


def test_ensure_due_never_makes_a_second_row():
    s = town()
    ob = O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=100, first=0, memo="x", ends="service", **SETUP)
    a = O.ensure_due(s, ob, 0, "routine:due")
    s.set("obligations", ob, "routine:test", status="finished")
    b = O.ensure_due(s, ob, 0, "routine:due")
    O.open_dues(s)
    assert a is b and len(s.all("dues")) == 1 and a.id == "du1" and a.cents == 100


BAD = [
    dict(kind="gift"), dict(ends="never"), dict(cents=0), dict(cents=-5), dict(cents=10.0), dict(cents=True),
    dict(every=-1), dict(first=-1), dict(first=5, last=4), dict(payer="h9"), dict(payee="outside:nowhere"),
    dict(payee="h1"),
]


@pytest.mark.parametrize("bad", BAD)
def test_create_refuses_bad_arguments_and_changes_nothing(bad):
    s = town()
    before, logged = s.hash(), len(s.changes)
    args = dict(kind="bill", payer="h1", payee="outside:northgrid", cents=100, first=0, every=0, last=None,
                memo="x", ends="service", **SETUP) | bad
    with pytest.raises(ValueError):
        O.create(s, **args)
    assert s.hash() == before and len(s.changes) == logged


def test_money():
    assert [O.money(c) for c in (0, 5, 2_500, 130_850, 123_456_789)] == [
        "$0.00", "$0.05", "$25.00", "$1,308.50", "$1,234,567.89"]


def test_people_of():
    s = town()
    assert O.people_of(s, "h1") == ["p1", "p2"] and O.people_of(s, "outside:lettings") == []


def test_money_is_conserved_and_nothing_is_collected_twice_over_sixty_days():
    s = town(h1=20_000, h2=5_000)
    O.create(s, kind="wage", payer="outside:employers", payee="h1", cents=40_000, first=4, every=14, memo="wages", ends="both", **SETUP)
    O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=30_000, first=0, every=7, memo="rent", ends="both", **SETUP)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=6_000, first=1, every=7, memo="energy", ends="service", **SETUP)
    O.create(s, kind="loan", payer="h1", payee="h2", cents=2_000, first=0, every=3, last=40, memo="loan", ends="both", **SETUP)
    O.create(s, kind="standing", payer="h2", payee="h1", cents=1_500, first=2, every=5, memo="share", ends="payer", **SETUP)
    for _ in range(60):
        run_days(s, 1)
        assert s.ledger.total() == 0
        assert all(a.allow_credit or a.balance >= 0 for a in s.ledger.accounts.values())
    collected = [t.cause for t in s.ledger.transfers if t.cause.startswith("routine:collect:")]
    assert len(collected) == len(set(collected))
    dues = {d.id: d for d in s.all("dues")}
    for t in s.ledger.transfers:
        if t.cause.startswith("routine:collect:"):
            assert t.cents == dues[t.cause.removeprefix("routine:collect:")].cents
    statuses = {d.status for d in dues.values()}
    assert "paid" in statuses and statuses & {"arrears", "defaulted"}      # too little money for everything
    assert len({(d.obligation, d.day) for d in dues.values()}) == len(dues)


def test_an_obligation_made_after_open_dues_still_falls_due_today():
    s = town(h1=10_000)
    run_days(s, 3)
    O.open_dues(s); O.age_arrears(s)
    ob = O.create(s, kind="fee", payer="h1", payee="outside:lettings", cents=1_000, first=3, memo="key", ends="payer", **SETUP)
    assert [(d.obligation, d.day) for d in s.all("dues")] == [(ob, 3)]
    assert s.changes[-1].cause == "routine:setup"
    O.open_dues(s)
    assert len(s.all("dues")) == 1
    O.collect(s)
    assert only_due(s).status == "paid" and s.balance("h1") == 9_000


def test_an_obligation_cannot_start_before_today():
    s = town()
    run_days(s, 3)
    before, logged = s.hash(), len(s.changes)
    with pytest.raises(ValueError):
        O.create(s, kind="fee", payer="h1", payee="outside:lettings", cents=1_000, first=2, memo="key", ends="payer", **SETUP)
    assert s.hash() == before and len(s.changes) == logged


def test_a_due_paid_beyond_its_amount_is_an_engine_fault_and_is_marked_paid():
    s = town(h1=10_000)
    ob = O.create(s, kind="fee", payer="h1", payee="outside:lettings", cents=1_000, first=0, memo="key", ends="payer", **SETUP)
    due = only_due(s)
    s.set("dues", due.id, "routine:test", paid=1_500)
    O.collect(s)
    assert due.status == "paid" and due.paid == 1_500 and s.balance("h1") == 10_000
    assert len(s.engine_errors) == 1 and due.id in s.engine_errors[0]
    assert s.get("obligations", ob).status == "finished"


def test_defaulting_on_the_final_instalment_breaches_the_obligation():
    s = town(h1=0)
    once = O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    ending = O.create(s, kind="loan", payer="h1", payee="outside:lettings", cents=1_000, first=0, every=7, last=3, memo="loan", ends="both", **SETUP)
    running = O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=10_000, first=0, every=7, memo="rent", ends="both", **SETUP)
    run_days(s, 29)
    assert [s.get("obligations", o).status for o in (once, ending, running)] == ["breached", "breached", "active"]
    assert any(c.table == "obligations" and c.field == "status" and c.new == "breached"
               and c.cause.startswith("routine:default:du") for c in s.changes)


def test_collect_twice_in_a_day_takes_each_due_once():
    s = town(h1=50_000)
    O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=17_500, first=0, memo="rent", ends="both", **SETUP)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", **SETUP)
    O.collect(s); O.collect(s)
    assert len([t for t in s.ledger.transfers if t.src == "h1"]) == 2 and s.balance("h1") == 27_700


def test_dues_are_taken_in_numeric_id_order_and_an_exact_balance_pays():
    s = town(h1=1_000, h2=100_000)
    for i in range(8):
        O.create(s, kind="bill", payer="h2", payee="outside:northgrid", cents=100, first=0, memo=f"h2 bill {i}", ends="service", **SETUP)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=1_000, first=0, memo="nine", ends="service", **SETUP)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=1_000, first=0, memo="ten", ends="service", **SETUP)
    O.collect(s)
    assert [(d.id, d.status) for d in s.all("dues")][-2:] == [("du9", "paid"), ("du10", "arrears")]
    assert s.balance("h1") == 0
