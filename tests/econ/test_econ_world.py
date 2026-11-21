"""The small economy: money, determinism, stock, suppliers, demand."""

import dataclasses

from townsim.econ.demand import NEEDS, shop
from townsim.econ.firm import Firm
from townsim.econ.policies import Naive, Reactive
from townsim.econ.sim import OUTSIDE, EconConfig, Economy
from townsim.econ.skilltest import start_state


def _run(seed: int, weeks: int = 52) -> Economy:
    econ = Economy.build(seed)
    econ.run({fid: Reactive() for fid in econ.firms}, weeks)
    return econ


def test_money_conserved_over_a_year_including_outside_accounts():
    econ = Economy.build(3)
    managers = {fid: Reactive() for fid in econ.firms}
    managers["firm:2"] = Naive()
    assert econ.ledger.total() == 0
    for _ in range(52):
        econ.step(managers)
        assert econ.ledger.total() == 0
    for acct in OUTSIDE:
        assert econ.ledger.has(acct)
    moved = {t.cause for t in econ.ledger.transfers}
    assert {"routine:delivery", "routine:shopping", "routine:wages", "routine:costs"} <= moved
    assert econ.ledger.balance("outside:suppliers") > 0


def test_same_seed_same_world_different_seed_different_world():
    a, b, c = _run(5, 20), _run(5, 20), _run(6, 20)
    assert a.history == b.history
    assert [t for t in a.ledger.transfers] == [t for t in b.ledger.transfers]
    assert a.history != c.history


def test_stock_perishes_on_schedule():
    f = Firm("firm:x", "food", 1000, 0.6, 0.5, 0.7, 1, 30, 0, 0, shelf_life=2, carry_loss=0.0)
    f.receive(5, 10, 600)
    assert f.perish(5) == 0 and f.stock() == 10  # still good next week
    assert f.perish(6) == 10 and f.stock() == 0  # gone at the end of its second week
    g = Firm("firm:y", "food", 1000, 0.6, 0.5, 0.7, 1, 30, 0, 0, shelf_life=2, carry_loss=0.2)
    g.receive(5, 10, 600)
    assert g.perish(5) == 2 and g.stock() == 8  # some wilts on the way into another week
    assert g.perish(6) == 8
    h = Firm("firm:z", "food", 1000, 0.6, 0.5, 0.7, 1, 30, 0, 0, shelf_life=3, carry_loss=0.0)
    h.receive(0, 4, 600)
    assert h.perish(0) == 0 and h.perish(1) == 0 and h.perish(2) == 4


def test_oldest_stock_sells_first():
    f = Firm("firm:x", "food", 1000, 0.6, 0.5, 0.7, 1, 30, 0, 0)
    f.receive(1, 2, 600)
    f.receive(2, 2, 700)
    assert [f.sell_one(), f.sell_one(), f.sell_one()] == [600, 600, 700]


def test_partial_deliveries_happen_and_are_paid_for_what_arrived():
    econ = start_state(1)
    managers = {fid: Reactive() for fid in econ.firms}
    managers["firm:1"] = Naive()  # always the cheap supplier
    first = len(econ.history["firm:1"])
    econ.run(managers, 30)
    shorts = 0
    for r in econ.history["firm:1"][first:]:
        for (sid, o), (_, d), (_, price) in zip(r.ordered, r.delivered, r.paid_unit):
            paid = sum(t.cents for t in econ.ledger.transfers
                       if t.week == r.week and t.src == "firm:1" and t.dst == "outside:suppliers")
            assert paid == d * price
            if d < o:
                shorts += 1
    assert shorts >= 3


def _shop_once(price: int, quality: float, seed: int = 2) -> tuple[int, int]:
    econ = Economy.build(seed)
    for f in econ.firms.values():
        f.price = price
        f.quality = quality
        f.cohorts.clear()
        f.receive(0, 10_000, 600)
        f.staff, f.units_per_staff = 1, 10_000
    s = shop(econ.ledger, econ.households, econ.offerings(), econ.firms, econ.spec, econ.rule,
             10, 1.0, seed)
    return s.bought_in_town, s.bought_in_town + s.bought_outside


def test_town_demand_falls_when_all_prices_rise():
    low = _shop_once(900, 0.6)
    mid = _shop_once(1050, 0.6)
    high = _shop_once(1200, 0.6)
    assert low[0] > mid[0] > high[0]
    assert low[1] > high[1]  # not just moved out of town: some go without


def test_town_demand_rises_with_quality():
    assert _shop_once(1100, 0.8)[0] > _shop_once(1100, 0.6)[0] > _shop_once(1100, 0.3)[0]


def test_stockout_at_first_choice_lowers_reputation():
    econ = start_state(2)
    empty = econ.firms["firm:1"]
    empty.cohorts.clear()
    econ.pending["firm:1"] = []  # and nothing on order
    before = {fid: f.reputation for fid, f in econ.firms.items()}
    managers = {fid: Reactive() for fid in econ.firms if fid != "firm:1"}
    econ.step(managers)  # firm 1 has no stock and no manager ordering
    rec = econ.history["firm:1"][-1]
    assert rec.sold == 0 and rec.turned_away_stock > 0
    assert empty.reputation < before["firm:1"] - 0.05


def test_seasonality_is_visible_in_sales():
    econ = _run(4, 104)
    peak = NEEDS["food"].season_peak_week
    near = [s.wants for w, s in enumerate(econ.shop_log) if min((w - peak) % 52, (peak - w) % 52) <= 4]
    far = [s.wants for w, s in enumerate(econ.shop_log) if min((w - peak) % 52, (peak - w) % 52) >= 22]
    assert sum(near) / len(near) > 1.25 * sum(far) / len(far)
    near_w = {w for w in range(104) if min((w - peak) % 52, (peak - w) % 52) <= 4}
    far_w = {w for w in range(104) if min((w - peak) % 52, (peak - w) % 52) >= 22}

    def sold_per_week(ws):
        return sum(r.sold for fid in econ.firms for r in econ.history[fid] if r.week in ws) / len(ws)

    assert sold_per_week(near_w) > 1.15 * sold_per_week(far_w)


def _newcomer(econ: Economy, fid: str, price: int, quality: float) -> Firm:
    f = Firm(fid, "food", price, quality, 0.5, 0.75, 2, 30, 4000, 3000)
    f.receive(econ.week, 200, 650)
    econ.add_firm(f, 100_000)
    return f


def test_an_offering_created_mid_run_wins_or_loses_buyers_by_the_same_rule():
    econ = start_state(3)
    managers = {fid: Reactive() for fid in econ.firms}
    econ.run(managers, 10)
    good = _newcomer(econ, "firm:new-good", 950, 0.7)
    dear = _newcomer(econ, "firm:new-dear", 2500, 0.7)
    total = econ.ledger.total()
    econ.step(managers)  # nobody manages the newcomers; they simply sit on the shelf
    assert econ.ledger.total() == total
    assert econ.history["firm:new-good"][-1].sold > 10
    assert econ.history["firm:new-dear"][-1].sold == 0
    assert good.reputation > 0 and dear.reputation == 0.75


def test_config_can_shorten_shelf_life():
    econ = Economy.build(1, dataclasses.replace(EconConfig(), shelf_life=1, carry_loss=0.0))
    f = econ.firms["firm:1"]
    f.receive(0, 1000, 600)
    econ.step({})
    assert econ.history["firm:1"][-1].leftover == 0
