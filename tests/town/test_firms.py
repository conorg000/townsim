"""Shops, suppliers and shopping, through the one rule of demand in `townsim.econ`."""

import os
import random
import subprocess
import sys

from townsim.econ.firm import Firm
from townsim.econ.suppliers import Supplier
from townsim.town import firms

from town.helpers import delivered, run_week, set_cash, set_order, shops_town, stock

S = "routine:setup"


def shopping(s):
    return [t for t in s.ledger.transfers if t.cause == "routine:shopping"]


def stocked(s, units=500):
    """`shops_town` after Monday's delivery, with `units` more on every shelf."""
    s = s if s is not None else shops_town()
    firms.deliver(s)
    for f in s.all("firms"):
        s.set("firms", f.id, S, stock_units=[*f.stock_units, units], stock_weeks=[*f.stock_weeks, 1])
    return s


def test_shopping_conserves_money_and_logs_every_move():
    s = shops_town(seed=3); firms.deliver(s); before = len(s.ledger.transfers)
    for _ in range(7): firms.shop_today(s); s.advance_day()
    assert s.ledger.total() == 0 and len(s.ledger.transfers) > before
    assert all(t.cause == "routine:shopping" and t.memo.startswith("food x") or
               t.cause == "routine:shopping" and t.memo.startswith("food out of town x")
               for t in s.ledger.transfers[before:])
    assert any(t.memo.startswith("food x") for t in s.ledger.transfers[before:])


def test_money_taken_matches_units_sold_at_the_price():
    s = stocked(shops_town())
    for _ in range(7): firms.shop_today(s); s.advance_day()
    for f in s.all("firms"):
        taken = sum(t.cents for t in shopping(s) if t.dst == f.org)
        units = sum(int(t.memo.split("x")[-1]) for t in shopping(s) if t.dst == f.org)
        assert f.sold > 0 and units == f.sold and taken == f.sold * f.price


def test_shopping_can_be_undone_exactly():
    s = stocked(shops_town()); before = s.hash(); m = s.mark()
    firms.shop_today(s)
    assert s.hash() != before
    s.undo_to(m)
    assert s.hash() == before


def test_shopping_twice_in_a_day_serves_nobody_twice():
    s = stocked(shops_town()); firms.shop_today(s)
    today = [b for b in s.all("buyers") if b.shop_day == 0]
    assert today and all(b.shopped_on == 0 for b in today)
    m = s.mark(); h = s.hash()
    firms.shop_today(s)
    assert s.mark() == m and s.hash() == h


def test_only_todays_buyers_shop():
    s = stocked(shops_town()); s.advance_day()   # Tuesday
    firms.shop_today(s)
    assert {b.shop_day for b in s.all("buyers") if b.shopped_on == 1} == {1}
    assert all(b.shopped_on == -1 for b in s.all("buyers") if b.shop_day != 1)
    paid = {t.src for t in shopping(s)}
    tuesday = {b.account for b in s.all("buyers") if b.shop_day == 1}
    assert paid and paid <= tuesday


def test_a_days_shopping_makes_one_transfer_per_payer_and_payee():
    s = stocked(shops_town()); units = transfers = 0
    for _ in range(7):
        n = len(s.ledger.transfers); firms.shop_today(s); s.advance_day()
        day = s.ledger.transfers[n:]
        pairs = {(t.src, t.dst) for t in day}
        assert len(day) == len(pairs) <= len({t.src for t in day}) * len({t.dst for t in day})
        assert [(t.src, t.dst) for t in day] == sorted(pairs)
        units += sum(int(t.memo.split("x")[-1]) for t in day); transfers += len(day)
    assert units > 3 * transfers   # many units, few transfers


def test_with_every_shelf_empty_food_is_bought_out_of_town():
    s = shops_town(); assert not s.ledger.has("outside:market")
    for _ in range(7): firms.shop_today(s); s.advance_day()
    out = shopping(s)
    assert out and all(t.dst == "outside:market" and t.memo.startswith("food out of town x") for t in out)
    assert sum(t.cents for t in out) == 1150 * sum(int(t.memo.split("x")[-1]) for t in out)
    assert s.ledger.accounts["outside:market"].allow_credit is False and s.ledger.total() == 0


def test_background_buyers_pay_from_outside_households():
    s = stocked(shops_town()); firms.shop_today(s)
    assert any(t.src == "outside:households" for t in shopping(s))
    assert all(t.src == "outside:households" or s.has("households", t.src) for t in shopping(s))


def test_a_buyer_remembers_where_it_last_bought():
    s = stocked(shops_town()); firms.shop_today(s)
    orgs = {f.org for f in s.all("firms")}
    bought = [b.last_bought for b in s.all("buyers") if b.shopped_on == 0 and b.last_bought]
    assert bought and set(bought) <= orgs


_WEEK = """
from tests.town.helpers import run_week, shops_town
from townsim.town import firms
s = shops_town(seed=5)
firms.deliver(s)
run_week(s)
firms.deliver(s)
print(s.hash())
"""


def test_shopping_is_the_same_whatever_the_hash_seed():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    hashes = []
    for seed in ("0", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", _WEEK], cwd=root, env=env,
                             capture_output=True, text=True, check=True)
        hashes.append(out.stdout.strip())
    assert hashes[0] == hashes[1] and len(hashes[0]) == 64


def test_an_empty_shelf_turns_customers_away():
    s = stocked(shops_town())
    s.set("firms", "fm1", S, stock_units=[], stock_weeks=[])
    n = len(s.ledger.transfers)
    for _ in range(6): firms.shop_today(s); s.advance_day()
    f = s.get("firms", "fm1")
    assert f.sold == 0 and f.turned_stock > 0 and f.first_refused > 0
    assert not [t for t in s.ledger.transfers[n:] if t.dst == "org3"]
    assert sum(g.sold for g in s.all("firms")) > 0


def test_capacity_is_counted_over_the_week():
    s = stocked(shops_town(), units=1000)
    s.set("firms", "fm1", S, units_per_staff=100, staff=1)
    for f in ("fm2", "fm3"): s.set("firms", f, S, stock_units=[], stock_weeks=[])
    assert firms.staff_hours(s, "org3") == 38.0 and firms.capacity(s, "fm1") == 100
    for _ in range(6): firms.shop_today(s); s.advance_day()
    f = s.get("firms", "fm1")
    assert f.sold == 100 and f.turned_capacity > 0
    assert sum(t.cents for t in shopping(s) if t.dst == "org3") == 100 * f.price


def test_extra_hours_add_to_capacity_for_the_week_only():
    s = shops_town()
    s.set("firms", "fm1", S, units_per_staff=100, staff=1, extra_hours=19.0)
    assert firms.staff_hours(s, "org3") == 57.0 and firms.capacity(s, "fm1") == 150
    for _ in range(6): s.advance_day()
    firms.close_week(s)
    assert s.get("firms", "fm1").extra_hours == 0.0 and firms.capacity(s, "fm1") == 100


def test_nobody_ordering_repeats_last_week_exactly():
    s = shops_town(); set_order(s, "fm1", "sp1", 300); firms.deliver(s); run_week(s); firms.deliver(s)
    assert s.get("suppliers", "sp1").reliability == 1.0
    assert delivered(s, "fm1") == [300, 300]
    f = s.get("firms", "fm1")
    assert (f.last_supplier, f.last_units, f.order_units) == ("sp1", 300, 0)


def test_a_delivery_pays_the_supplier_and_adds_a_cohort():
    s = shops_town(); firms.deliver(s)
    f = s.get("firms", "fm1")
    assert f.stock_units == [240] and f.stock_weeks == [1]
    [t] = [t for t in s.ledger.transfers if t.cause == "routine:delivery:fm1"]
    assert (t.src, t.dst, t.cents, t.memo) == ("org3", "org6", 240 * 700, "240 units from Vale Wholesale")
    assert s.ledger.total() == 0


def test_a_short_delivery_follows_the_supplier_fill():
    s = shops_town(seed=2); firms.deliver(s)
    sp = s.get("suppliers", "sp2")
    want = Supplier("sp2", sp.base_cents, sp.unit_cents, sp.reliability, sp.short_low, sp.short_high).fill(
        60, random.Random("fill:2:1:fm2"))
    assert stock(s, "fm2") == want


def test_a_delivery_is_cut_to_what_the_shop_can_pay_for():
    s = shops_town(); set_cash(s, "org3", 100_000); set_order(s, "fm1", "sp1", 300)      # $1,000 at $7.00 a unit
    firms.deliver(s); assert stock(s, "fm1") == 142 and s.balance("org3") == 600


def test_deliveries_come_on_mondays_and_the_week_closes_on_sundays():
    s = shops_town(); s.advance_day(); m = s.mark()
    firms.deliver(s); firms.close_week(s)
    assert s.mark() == m


def test_old_stock_is_thrown_away_and_the_week_is_recorded():
    s = shops_town()
    s.set("firms", "fm1", S, stock_units=[5, 7], stock_weeks=[-1, 1], sold=9, turned_stock=2,
          turned_capacity=3, spoiled=1, first_served=4, first_refused=1, extra_hours=5.0)
    for _ in range(6): s.advance_day()
    firms.close_week(s)
    f = s.get("firms", "fm1")
    assert f.stock_units == [7] and f.stock_weeks == [1]
    assert (f.sold, f.turned_stock, f.turned_capacity, f.spoiled, f.first_served, f.first_refused,
            f.extra_hours) == (0, 0, 0, 0, 0, 0, 0.0)
    [tw] = [t for t in s.all("tills") if t.firm == "fm1"]
    assert (tw.week, tw.price, tw.sold, tw.turned_stock, tw.turned_capacity, tw.spoiled, tw.stock_left,
            tw.cash) == (1, 1000, 9, 2, 3, 6, 7, s.balance("org3"))
    assert len(s.all("tills")) == 3


def test_reputation_moves_as_the_econ_firm_moves_it():
    s = shops_town()
    s.set("firms", "fm1", S, reputation=0.5, quality=0.7, first_served=3, first_refused=1)
    model = Firm("x", "food", 1000, 0.7, 0.5, 0.5, 1, 40, 0, 0)
    model.week.first_attempts, model.week.first_served = 4, 3
    model.update_reputation()
    for _ in range(6): s.advance_day()
    firms.close_week(s)
    assert s.get("firms", "fm1").reputation == round(model.reputation, 6) != 0.5
    assert s.get("firms", "fm2").reputation == 0.75   # nobody tried it first: unchanged


def test_supplier_prices_drift_as_the_econ_supplier_drifts():
    s = shops_town(seed=4)
    for _ in range(6): s.advance_day()
    sp = s.get("suppliers", "sp2")
    model = Supplier("sp2", sp.base_cents, sp.unit_cents, sp.reliability, sp.short_low, sp.short_high)
    model.drift(random.Random("drift:4:1:sp2"))
    firms.close_week(s)
    assert s.get("suppliers", "sp2").unit_cents == model.price


def test_a_week_of_trade_can_be_undone_exactly():
    s = shops_town(); before = s.hash(); m = s.mark()
    firms.deliver(s); run_week(s); firms.deliver(s)
    assert s.ledger.total() == 0 and s.all("tills")
    s.undo_to(m)
    assert s.hash() == before
