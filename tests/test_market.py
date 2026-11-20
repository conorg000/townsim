import random

from townsim.events import state_hash
from townsim.market import close_firm, closure_phase, market_phase, payroll_phase
from townsim.world import total_money


def test_market_conserves_money_and_respects_capacity(world, rng):
    before = total_money(world)
    market_phase(world, rng)
    assert total_money(world) == before
    for f in world.firms.values():
        assert f.customers_last_step <= 4 * len(f.employee_ids)


def test_cheaper_firm_wins_more_customers_all_else_equal(world, rng):
    for f in world.firms.values(): f.quality = 0.5
    world.firms["f1"].price, world.firms["f2"].price, world.firms["f3"].price = 3000, 5000, 5000
    market_phase(world, rng)
    assert world.firms["f1"].customers_last_step > world.firms["f2"].customers_last_step


def test_firm_that_cannot_pay_wages_closes_cleanly(world):
    world.firms["f1"].cash = 100
    evs = payroll_phase(world)
    f = world.firms["f1"]
    assert f.open is False and f.employee_ids == [] and f.cash == 0
    assert world.people[f.owner_id].role == "unemployed"
    assert "firm_closed" in [e.kind for e in evs]


def test_market_with_no_open_firms_in_a_market_does_not_crash(world, rng):
    for fid in ("f4", "f5"): close_firm(world, fid)
    market_phase(world, rng)


def test_firm_with_no_staff_sells_nothing(world, rng):
    world.firms["f1"].employee_ids = []
    market_phase(world, rng)
    assert world.firms["f1"].customers_last_step == 0


# --- beyond the brief ---


def test_market_records_revenue_as_profit_and_emits_one_sales_event_per_open_firm(world, rng):
    close_firm(world, "f5")
    cash_before = {fid: f.cash for fid, f in world.firms.items()}
    evs = market_phase(world, rng)
    sales = [e for e in evs if e.kind == "sales"]
    assert sorted(e.payload["firm_id"] for e in sales) == ["f1", "f2", "f3", "f4"]
    for e in sales:
        f = world.firms[e.payload["firm_id"]]
        assert e.payload["customers"] == f.customers_last_step
        assert e.payload["revenue"] == f.customers_last_step * f.price
        assert f.cash - cash_before[f.id] == e.payload["revenue"]
        assert f.profit_qtd == e.payload["revenue"]
    assert world.firms["f5"].customers_last_step == 0


def test_buyers_who_cannot_afford_any_price_buy_nothing(world, rng):
    for p in world.people.values():
        p.cash = 10
    evs = market_phase(world, rng)
    assert all(e.payload["customers"] == 0 for e in evs if e.kind == "sales")


def test_loyalty_rises_on_purchase_and_is_capped(world, rng):
    for _ in range(30):
        for p in world.people.values():
            p.cash = 100000
        market_phase(world, rng)
    loyalties = [v for p in world.people.values() for v in p.loyalty.values()]
    assert loyalties and max(loyalties) <= 0.3 + 1e-9
    assert any(v > 0 for v in loyalties)


def test_market_is_deterministic_for_a_seed(cfg):
    from townsim.config import build_world

    a, b = build_world(cfg), build_world(cfg)
    market_phase(a, random.Random(7))
    market_phase(b, random.Random(7))
    assert state_hash(a) == state_hash(b)


def test_payroll_pays_wages_salaries_benefits_and_subscriptions(world):
    before = total_money(world)
    worker = world.firms["f1"].employee_ids[0]
    unemployed = [p.id for p in world.people.values() if p.role == "unemployed"]
    journalist = world.newspaper.journalist_ids[0]
    auditor = world.regulator.auditor_id
    start = {pid: p.cash for pid, p in world.people.items()}
    evs = payroll_phase(world)
    assert total_money(world) == before
    assert world.people[worker].cash == start[worker] + 11000 - 700
    assert world.people[auditor].cash == start[auditor] + 10000 - 700
    assert world.people[journalist].cash == start[journalist] + 10000 - 700
    for pid in unemployed:
        assert world.people[pid].cash == start[pid] + 6000 - 700
    assert world.firms["f1"].profit_qtd == -4 * 11000
    assert not [e for e in evs if e.kind in ("firm_closed", "payment_missed")]


def test_firm_closure_moves_all_staff_and_owner_to_unemployed(world):
    staff = list(world.firms["f2"].employee_ids)
    owner = world.firms["f2"].owner_id
    owner_cash = world.people[owner].cash
    evs = close_firm(world, "f2")
    assert [e.kind for e in evs] == ["firm_closed"]
    assert evs[0].payload["firm_id"] == "f2"
    for pid in staff + [owner]:
        assert world.people[pid].role == "unemployed"
        assert world.people[pid].employer_id is None
    assert world.people[owner].cash == owner_cash + 400000
    assert world.firms["f2"].cash == 0
    assert close_firm(world, "f2") == []


def test_broke_treasury_and_newspaper_skip_payments_and_log_them(world):
    world.treasury.cash = 0
    world.newspaper.cash = 0
    before = total_money(world)
    evs = payroll_phase(world)
    assert total_money(world) == before
    missed = [e.payload for e in evs if e.kind == "payment_missed"]
    payers = {m["payer"] for m in missed}
    assert payers == {"treasury", "newspaper"}
    assert {"payer": "treasury", "payee": world.regulator.auditor_id, "amount": 10000} in missed


def test_person_who_cannot_afford_subscription_skips_it(world):
    pid = world.firms["f1"].owner_id
    world.people[pid].cash = 0
    payroll_phase(world)
    assert world.people[pid].cash == 0


def test_closure_phase_closes_only_empty_broke_firms(world):
    world.firms["f1"].employee_ids = []
    world.firms["f1"].cash = 5000
    world.firms["f2"].employee_ids = []
    world.firms["f3"].cash = 0
    evs = closure_phase(world)
    assert [e.payload["firm_id"] for e in evs if e.kind == "firm_closed"] == ["f1"]
    assert world.firms["f1"].open is False
    assert world.firms["f2"].open and world.firms["f3"].open
