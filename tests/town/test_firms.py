"""Shops, suppliers and shopping, through the one rule of demand in `townsim.econ`."""

import os
import random
import subprocess
import sys

from townsim.econ.demand import NEEDS, DemandRule, Household, Offering, shop
from townsim.econ.firm import Cohort, Firm
from townsim.econ.ledger import Ledger
from townsim.econ.suppliers import Supplier
from townsim.town import clock, firms
from townsim.town.entities import Buyer, FirmRow

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
    assert today and all(b.shopped_week == 1 for b in today)
    m = s.mark(); h = s.hash()
    firms.shop_today(s)
    assert s.mark() == m and s.hash() == h


def test_a_buyer_shops_once_a_week_whatever_its_shop_day_becomes():
    s = stocked(shops_town()); firms.shop_today(s)
    monday = [b.id for b in s.all("buyers") if b.shop_day == 0]
    for b in monday: s.set("buyers", b, S, shop_day=2)
    s.advance_day(); s.advance_day()
    n = len(s.ledger.transfers); firms.shop_today(s)
    assert {t.src for t in s.ledger.transfers[n:]}.isdisjoint(
        {s.get("buyers", b).account for b in monday if s.get("buyers", b).account != "outside:households"})
    assert all(s.get("buyers", b).shopped_week == 1 for b in monday)
    s.set("buyers", monday[0], S, shopped_week=0)   # but a buyer that has not shopped this week does
    firms.shop_today(s)
    assert s.get("buyers", monday[0]).shopped_week == 1


def test_only_todays_buyers_shop():
    s = stocked(shops_town()); s.advance_day()   # Tuesday
    firms.shop_today(s)
    assert {b.shop_day for b in s.all("buyers") if b.shopped_week == 1} == {1}
    assert all(b.shopped_week == -1 for b in s.all("buyers") if b.shop_day != 1)
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
    bought = [b.last_bought for b in s.all("buyers") if b.shopped_week == 1 and b.last_bought]
    assert bought and set(bought) <= orgs


_WEEK = """
from tests.town.helpers import run_week, shops_town
from townsim.town import clock, firms
from townsim.town.entities import Buyer, FirmRow
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
    s.set("firms", "fm1", S, units_per_staff=100)
    for jb in ("jb2", "jb3", "jb4"): s.set("jobs", jb, S, hours=0.0)
    for f in ("fm2", "fm3"): s.set("firms", f, S, stock_units=[], stock_weeks=[])
    assert firms.staff_hours(s, "org3") == 38.0 and firms.capacity(s, "fm1") == 100
    for _ in range(6): firms.shop_today(s); s.advance_day()
    f = s.get("firms", "fm1")
    assert f.sold == 100 and f.turned_capacity > 0
    assert sum(t.cents for t in shopping(s) if t.dst == "org3") == 100 * f.price


def test_extra_hours_add_to_capacity_for_the_week_only():
    s = shops_town()
    s.set("firms", "fm1", S, units_per_staff=100, extra_hours=19.0)
    for jb in ("jb2", "jb3", "jb4"): s.set("jobs", jb, S, hours=0.0)
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
    assert (t.src, t.dst, t.cents, t.memo) == ("org3", "org6", 240 * 700, "240 baskets from Vale Wholesale")
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


def test_old_stock_perishes_as_in_the_econ_firm_and_the_week_is_recorded():
    s = shops_town()
    s.set("firms", "fm1", S, stock_units=[5, 20], stock_weeks=[0, 1], sold=9, turned_stock=2,
          turned_capacity=3, spoiled=1, first_served=4, first_refused=1, extra_hours=5.0, delivered=20,
          takings=9000)
    model = Firm("x", "food", 1000, 0.6, 0.5, 0.75, 1, 60, 0, 0, shelf_life=2,
                 cohorts=[Cohort(0, 5, 0), Cohort(1, 20, 0)])
    assert model.perish(1) == 8          # the week-0 cohort is past its last week; 15% of the rest wilts
    for _ in range(6): s.advance_day()
    firms.close_week(s)
    f = s.get("firms", "fm1")
    assert f.stock_units == [c.units for c in model.cohorts] == [17] and f.stock_weeks == [1]
    assert (f.sold, f.turned_stock, f.turned_capacity, f.spoiled, f.first_served, f.first_refused,
            f.extra_hours, f.delivered, f.takings) == (0, 0, 0, 0, 0, 0, 0.0, 0, 0)
    [tw] = [t for t in s.all("tills") if t.firm == "fm1"]
    assert (tw.week, tw.price, tw.sold, tw.turned_stock, tw.turned_capacity, tw.spoiled, tw.stock_left,
            tw.cash, tw.delivered, tw.takings) == (1, 1000, 9, 2, 3, 9, 17, s.balance("org3"), 20, 9000)
    assert len(s.all("tills")) == 3


def test_a_week_of_trade_is_recorded_in_the_till():
    s = shops_town(); run_week(s)
    for f in s.all("firms"):
        [tw] = [t for t in s.all("tills") if t.firm == f.id]
        assert tw.takings == sum(t.cents for t in shopping(s) if t.dst == f.org) == tw.sold * tw.price > 0
        assert tw.delivered == sum(delivered(s, f.id)) > 0


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


def test_deliver_delivers_once_a_week_however_often_it_is_called():
    s = shops_town(); firms.deliver(s); m = s.mark()
    firms.deliver(s)
    assert s.mark() == m and delivered(s, "fm1") == [240]
    assert {f.delivered_week for f in s.all("firms")} == {1}


def test_an_order_is_placed_through_one_door():
    s = shops_town(); m = s.mark()
    for sup, units in (("sp9", 10), ("sp1", -1), ("sp1", 5001), ("sp1", 2.5), ("sp1", True)):
        try:
            firms.place_order(s, "fm1", sup, units, "routine:t")
        except ValueError:
            pass
        else:
            raise AssertionError((sup, units))
    assert s.mark() == m
    firms.place_order(s, "fm1", "sp2", 50, "routine:t")
    firms.place_order(s, "fm1", "sp1", 100, "routine:t")       # replaces the first
    f = s.get("firms", "fm1")
    assert (f.order_supplier, f.order_units, f.order_cents) == ("sp1", 100, 700)


def test_a_placed_order_is_paid_at_the_price_it_was_placed_at():
    s = shops_town(); set_order(s, "fm1", "sp1", 100)
    s.set("suppliers", "sp1", S, unit_cents=800)
    firms.deliver(s)
    [t] = [t for t in s.ledger.transfers if t.cause == "routine:delivery:fm1"]
    assert t.cents == 100 * 700
    f = s.get("firms", "fm1")
    assert (f.order_supplier, f.order_units, f.order_cents, f.last_supplier, f.last_units) == ("", 0, 0, "sp1", 100)


def test_a_repeated_order_is_paid_at_the_price_of_the_day():
    s = shops_town(); s.set("suppliers", "sp1", S, unit_cents=750)
    firms.deliver(s)
    [t] = [t for t in s.ledger.transfers if t.cause == "routine:delivery:fm1"]
    assert t.cents == 240 * 750


def test_ordering_nothing_stops_deliveries_until_an_order_is_placed():
    s = shops_town(); set_order(s, "fm1", "sp1", 0)
    run_week(s); firms.deliver(s); run_week(s)
    assert delivered(s, "fm1") == [] and s.get("firms", "fm1").last_units == 0
    set_order(s, "fm1", "sp1", 30); run_week(s)
    assert delivered(s, "fm1") == [30]


def test_an_order_from_no_supplier_repeats_last_weeks_and_is_cleared():
    s = shops_town(); s.set("firms", "fm1", S, order_supplier="sp9", order_units=50, order_cents=700)
    firms.deliver(s)
    f = s.get("firms", "fm1")
    assert delivered(s, "fm1") == [240] and (f.order_supplier, f.order_units) == ("", 0)
    assert any("sp9" in e for e in s.engine_errors)


def test_a_supplier_priced_at_nothing_is_skipped_and_the_rest_delivered():
    s = shops_town(); s.set("suppliers", "sp2", S, unit_cents=0)
    firms.deliver(s)
    assert delivered(s, "fm2") == [] and delivered(s, "fm1") == [240] and delivered(s, "fm3") == [60]
    assert any("fm2" in e and "sp2" in e for e in s.engine_errors) and s.ledger.total() == 0


def test_a_buyer_with_no_account_is_skipped_and_the_rest_shop():
    s = stocked(shops_town())
    s.add("buyers", Buyer("by99", "h99", 2, 1.1, 10_000, shop_day=0), S)
    firms.shop_today(s)
    assert any("by99" in e for e in s.engine_errors)
    assert s.get("buyers", "by99").shopped_week == -1 and shopping(s)


def test_a_second_buyer_for_a_household_is_skipped():
    s = stocked(shops_town())
    s.add("buyers", Buyer("by99", "h1", 2, 1.1, 10_000, shop_day=0), S)
    firms.shop_today(s)
    assert any("by99" in e and "h1" in e for e in s.engine_errors)
    assert s.get("buyers", "by99").shopped_week == -1


def test_a_second_food_shop_on_one_organisation_is_skipped():
    s = stocked(shops_town())
    s.add("firms", FirmRow("fm9", "org3", "food", 900, 0.9, 0.9, 0.9, 60, 2, stock_units=[100], stock_weeks=[1]), S)
    units = sum(sum(f.stock_units) for f in s.all("firms"))
    firms.shop_today(s)
    assert any("fm9" in e and "org3" in e for e in s.engine_errors)
    fm9 = s.get("firms", "fm9")
    assert fm9.stock_units == [100] and fm9.sold == 0
    sold = sum(f.sold for f in s.all("firms"))
    assert sum(sum(f.stock_units) for f in s.all("firms")) == units - sold and s.ledger.total() == 0


def _econ_twin(s):
    """The direct econ call equivalent to one `shop_today` for every buyer: households,
    firms and a real ledger built from the rows."""
    L = Ledger(); hh = []
    for b in s.all("buyers"):
        hid = b.id if b.account == "outside:households" else b.account
        L.open(hid, allow_credit=b.account == "outside:households")
        L.accounts[hid].balance = 10**15 if b.account == "outside:households" else s.balance(hid)
        hh.append(Household(hid, b.size, {"food": b.wtp}, {"food": b.budget}, 0,
                            {"food": b.last_bought} if b.last_bought else {}))
    provs = {}
    for f in s.all("firms"):
        L.open(f.org); L.accounts[f.org].balance = s.balance(f.org)
        provs[f.org] = Firm(f.org, "food", f.price, f.quality, f.visibility, f.reputation, staff=1,
                            units_per_staff=firms.capacity(s, f.id), wage=0, fixed_cost=0, shelf_life=f.shelf_weeks,
                            cohorts=[Cohort(w, u, 0) for u, w in zip(f.stock_units, f.stock_weeks)])
    L.open("outside:market")
    offs = [Offering(f.org, f.need, f.price, f.quality, f.visibility) for f in s.all("firms")]
    shop(L, hh, offs, provs, NEEDS["food"], DemandRule(), clock.week(s.day), 1.0, s.seed, "routine:shopping")
    return L, hh, provs


def test_shopping_in_town_is_exactly_the_econ_rule():
    for seed in (1, 2, 3, 7):
        s = shops_town(seed=seed); firms.deliver(s)
        for i, b in enumerate(s.all("buyers")):
            s.set("buyers", b.id, S, shop_day=0)
            if i % 5 == 0: s.set("buyers", b.id, S, last_bought="org4")
        homes = [b.account for b in s.all("buyers") if b.account != "outside:households"]
        for account, cents in zip(homes, (1000, 2500, 999)): set_cash(s, account, cents)
        before = {a: s.balance(a) for a in s.ledger.accounts}
        L, hh, provs = _econ_twin(s)
        firms.shop_today(s)
        for f in s.all("firms"):
            e, p = provs[f.org].week, provs[f.org]
            assert (f.sold, f.turned_stock, f.turned_capacity, f.first_served, f.first_refused,
                    s.balance(f.org) - before[f.org], sum(f.stock_units)) == \
                (e.sold, e.turned_away_stock, e.turned_away_capacity, e.first_served,
                 e.first_attempts - e.first_served, L.balance(f.org) - before[f.org], p.stock()), (seed, f.id)
        for b, h in zip(s.all("buyers"), hh):
            if b.account != "outside:households":
                assert s.balance(b.account) - before[b.account] == L.balance(h.id) - before[b.account]
            assert b.last_bought == h.last_bought.get("food", ""), (seed, b.id)
        market = s.balance("outside:market") if s.ledger.has("outside:market") else 0
        assert market == L.balance("outside:market") and sum(f.sold for f in s.all("firms")) > 0
