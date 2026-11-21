"""Shops, their suppliers, and shopping by the one rule of demand.

The arithmetic is `townsim.econ`'s: `econ.demand.shop` decides who buys what where,
`econ.firm.Firm` serves, refuses, perishes stock and moves reputation,
`econ.suppliers.Supplier` fills orders and drifts its price. This module builds those
objects from the store's rows, runs them, and writes back what they changed through
`Store` methods, so every cent moved is in the ledger with a cause, and every change can
be undone and replayed.

Each day `shop_today` puts the buyers whose shopping day it is through the rule with
their whole week's wants. On Mondays `deliver` brings each shop its order, once; on
Sundays `close_week` spoils stock as econ does, moves reputations and supplier prices,
and records the week. Orders are placed through `place_order` only. None of the three
daily functions raises: a row they cannot use is skipped with a line in
`store.engine_errors`, and the rest of the call goes ahead.
"""

from __future__ import annotations

import random

from townsim.econ.demand import NEEDS, DemandRule, Household, Offering, shop
from townsim.econ.firm import Cohort, Firm, WeekCounters
from townsim.econ.suppliers import Supplier
from townsim.town import clock, jobs
from townsim.town.entities import Buyer, FirmRow, SupplierRow, TillWeek
from townsim.town.store import Store

NEED = "food"
HOURS_PER_STAFF = 38.0
MAX_ORDER = 5000
OUTSIDE_BUYERS = "outside:households"
OUT_OF_TOWN = "outside:market"   # where `econ.demand.shop` pays for what is bought out of town
SHOPPING = "routine:shopping"
CLOSE = "routine:close_week"
_CREDIT = 10**15                 # what an account that allows credit is taken to hold
_PLACES = 6


def staff_hours(store: Store, org: str) -> float:
    """Hours worked at `org` this week: the hours of the jobs there held today, plus its
    shops' `extra_hours`."""
    worked = sum(j.hours for j in jobs.held(store, org=org))
    return worked + sum(f.extra_hours for f in store.where("firms", org=org))


def capacity(store: Store, firm_id: str) -> int:
    """Units the shop can serve in a week: `units_per_staff` for each 38 hours worked."""
    f = store.get("firms", firm_id)
    return int(f.units_per_staff * staff_hours(store, f.org) // HOURS_PER_STAFF)


def _error(store: Store, what: str, text: str) -> None:
    store.engine_errors.append(f"{what} d{store.day}: {text}")


def _changed(row: object, **fields: object) -> dict:
    return {k: v for k, v in fields.items() if getattr(row, k) != v}


# --- orders ----------------------------------------------------------------------------


def place_order(store: Store, firm_id: str, supplier_id: str, units: int, cause: str) -> None:
    """Order `units` from `supplier_id` for Monday at the supplier's price now, replacing any
    order already placed this week. `units == 0` orders nothing, now or repeated later, until
    an order is placed again. Raises ValueError for an unknown firm or supplier, or units
    outside 0..5000; nothing changes then."""
    if not store.has("firms", firm_id):
        raise ValueError(f"no firm {firm_id!r}")
    if not isinstance(units, int) or isinstance(units, bool) or not 0 <= units <= MAX_ORDER:
        raise ValueError(f"units must be a whole number from 0 to {MAX_ORDER}, got {units!r}")
    if units == 0:
        store.set("firms", firm_id, cause, order_supplier="", order_units=0, order_cents=0, last_units=0)
        return
    if not store.has("suppliers", supplier_id):
        raise ValueError(f"no supplier {supplier_id!r}")
    store.set("firms", firm_id, cause, order_supplier=supplier_id, order_units=units,
              order_cents=store.get("suppliers", supplier_id).unit_cents)


# --- the adapter ---------------------------------------------------------------------


class _Ledger:
    """What `econ.demand.shop` sees as a ledger. `balance` is the store's balance for the
    account behind an econ id, less what this call has already taken from it; `transfer`
    only records, to be paid into the store afterwards in one transfer per pair."""

    def __init__(self, store: Store, accounts: dict[str, str]) -> None:
        self.store = store
        self.accounts = accounts           # econ id -> store account
        self.moved: dict[str, int] = {}    # store account -> cents moved out (+) or in (-)
        self.sales: list[tuple[str, str, int]] = []   # payer account, payee account, cents

    def _account(self, econ_id: str) -> str:
        return self.accounts.get(econ_id, econ_id)

    def balance(self, econ_id: str) -> int:
        account = self._account(econ_id)
        if self.store.ledger.accounts[account].allow_credit:
            return _CREDIT
        return self.store.balance(account) - self.moved.get(account, 0)

    def transfer(self, src: str, dst: str, cents: int, memo: str, cause: str) -> None:
        payer, payee = self._account(src), self._account(dst)
        self.moved[payer] = self.moved.get(payer, 0) + cents
        self.moved[payee] = self.moved.get(payee, 0) - cents
        self.sales.append((payer, payee, cents))


def _firm(store: Store, row: FirmRow) -> Firm:
    """An `econ.firm.Firm` for the row: its id is the shop's organisation account, and its
    capacity the week's, so its count of the week's sales is checked against it. It keeps
    econ's own `carry_loss`."""
    f = Firm(row.org, row.need, row.price, row.quality, row.visibility, row.reputation,
             staff=1, units_per_staff=capacity(store, row.id), wage=0, fixed_cost=0,
             shelf_life=row.shelf_weeks,
             cohorts=[Cohort(w, u, 0) for u, w in zip(row.stock_units, row.stock_weeks) if u > 0])
    f.week = WeekCounters(sold=row.sold, turned_away_stock=row.turned_stock,
                          turned_away_capacity=row.turned_capacity,
                          first_attempts=row.first_served + row.first_refused,
                          first_served=row.first_served)
    return f


def _econ_id(b: Buyer) -> str:
    """A background buyer is its own household in econ, so it has its own random stream."""
    return b.id if b.account == OUTSIDE_BUYERS else b.account


def _buyers_today(store: Store) -> list[Buyer]:
    """Today's buyers who have not shopped this week, less any that cannot shop (noted)."""
    week = clock.week(store.day)
    first: dict[str, str] = {}          # household account -> its first buyer, by id
    for b in store.all("buyers"):
        if b.account != OUTSIDE_BUYERS:
            first.setdefault(b.account, b.id)
    out = []
    for b in store.where("buyers", shop_day=clock.dow(store.day)):
        if b.shopped_week == week:
            continue
        if not store.ledger.has(b.account):
            _error(store, "shop_today", f"buyer {b.id} pays from {b.account!r}, which is no account; skipped")
        elif b.account != OUTSIDE_BUYERS and first[b.account] != b.id:
            _error(store, "shop_today", f"buyer {b.id} is a second buyer for {b.account}; skipped")
        elif not isinstance(b.size, int) or b.size < 1 or b.budget < 0 or b.wtp < 0:
            _error(store, "shop_today", f"buyer {b.id} has size {b.size}, budget {b.budget}, wtp {b.wtp}; skipped")
        else:
            out.append(b)
    return out


def _shops(store: Store) -> list[FirmRow]:
    """Food shops that can trade, one per organisation (the first by id), others noted."""
    out, orgs = [], set()
    for f in store.all("firms"):
        if f.need != NEED:
            continue
        if f.org in orgs:
            _error(store, "shop_today", f"{f.id} is a second {NEED} shop for {f.org}; skipped")
        elif not store.ledger.has(f.org):
            _error(store, "shop_today", f"{f.id} trades from {f.org!r}, which is no account; skipped")
        elif f.price <= 0:
            _error(store, "shop_today", f"{f.id} has price {f.price}; skipped")
        else:
            out.append(f)
        orgs.add(f.org)
    return out


def shop_today(store: Store) -> None:
    """Every buyer whose `shop_day` is today, and who has not shopped this week, buys its
    week's food by the one rule. One transaction; one transfer per (payer, payee)."""
    buyers = _buyers_today(store)
    if not buyers:
        return
    rows = _shops(store)
    week = clock.week(store.day)
    with store.transaction():
        households, accounts = [], {}
        for b in buyers:
            hid = _econ_id(b)
            accounts[hid] = b.account
            households.append(Household(hid, b.size, {NEED: b.wtp}, {NEED: b.budget}, 0,
                                        {NEED: b.last_bought} if b.last_bought else {}))
        providers = {f.org: _firm(store, f) for f in rows}
        offerings = [Offering(f.org, f.need, f.price, f.quality, f.visibility) for f in rows]
        ledger = _Ledger(store, accounts)
        shop(ledger, households, offerings, providers, NEEDS[NEED], DemandRule(), week, 1.0,
             store.seed, SHOPPING)
        totals: dict[tuple[str, str], list[int]] = {}
        for payer, payee, cents in ledger.sales:
            t = totals.setdefault((payer, payee), [0, 0])
            t[0] += cents
            t[1] += 1
        if any(payee == OUT_OF_TOWN for _, payee in totals) and not store.ledger.has(OUT_OF_TOWN):
            store.open_account(OUT_OF_TOWN, SHOPPING)
        for (payer, payee), (cents, units) in sorted(totals.items()):
            basket = "basket" if units == 1 else "baskets"
            memo = f"{units} {basket} of {NEED} out of town" if payee == OUT_OF_TOWN else f"{units} {basket} of {NEED}"
            store.transfer(payer, payee, cents, memo, SHOPPING)
        for b, hh in zip(buyers, households):
            store.set("buyers", b.id, SHOPPING, shopped_week=week,
                      **_changed(b, last_bought=hh.last_bought.get(NEED, "")))
        for row in rows:
            f = providers[row.org]
            w = f.week
            taken = sum(c for (_, payee), (c, _) in totals.items() if payee == row.org)
            fields = _changed(row, stock_units=[c.units for c in f.cohorts], stock_weeks=[c.week for c in f.cohorts],
                              sold=w.sold, turned_stock=w.turned_away_stock, turned_capacity=w.turned_away_capacity,
                              first_served=w.first_served, first_refused=w.first_attempts - w.first_served,
                              takings=row.takings + taken)
            if fields:
                store.set("firms", row.id, SHOPPING, **fields)


# --- suppliers -------------------------------------------------------------------------


def _supplier(row: SupplierRow) -> Supplier:
    return Supplier(row.id, row.base_cents, row.unit_cents, row.reliability, row.short_low, row.short_high)


def _order(store: Store, row: FirmRow) -> tuple[str, int, int] | None:
    """The supplier, units and unit price of Monday's delivery to `row`, or None."""
    if row.order_units > 0:
        if store.has("suppliers", row.order_supplier):
            return row.order_supplier, row.order_units, row.order_cents
        _error(store, "deliver", f"{row.id} ordered from {row.order_supplier!r}, which is no supplier; "
                                 "last week's order repeated")
    if row.last_units <= 0 or not row.last_supplier:
        return None
    if not store.has("suppliers", row.last_supplier):
        _error(store, "deliver", f"{row.id} repeats an order from {row.last_supplier!r}, which is no supplier; skipped")
        return None
    return row.last_supplier, row.last_units, store.get("suppliers", row.last_supplier).unit_cents


def deliver(store: Store) -> None:
    """Monday, once a week: each shop gets the order placed since last Monday at the price it
    was placed at, or last week's again at today's price, as far as the supplier fills it
    (half of that in a week it has said it will be short) and the shop can pay for it, paid
    on delivery."""
    if clock.dow(store.day) != 0:
        return
    week = clock.week(store.day)
    with store.transaction():
        for row in store.all("firms"):
            if row.delivered_week == week:
                continue
            cause = f"routine:delivery:{row.id}"
            fields: dict = dict(delivered_week=week, order_supplier="", order_units=0, order_cents=0)
            order = _order(store, row)
            if order is not None:
                sid, units, cents = order
                sp = store.get("suppliers", sid)
                if cents <= 0:
                    _error(store, "deliver", f"{row.id}: {sid} is priced at {cents} cents; skipped")
                elif not store.ledger.has(row.org) or not store.ledger.has(sp.org):
                    _error(store, "deliver", f"{row.id}: no account for {row.org} or {sp.org}; skipped")
                else:
                    got = _supplier(sp).fill(units, random.Random(f"fill:{store.seed}:{week}:{row.id}"))
                    if sp.short_week == week:
                        got //= 2
                    got = min(got, max(0, store.balance(row.org)) // cents)
                    fields.update(last_supplier=sid, last_units=units)
                    if got > 0:
                        name = store.get("orgs", sp.org).name if store.has("orgs", sp.org) else sp.org
                        store.transfer(row.org, sp.org, got * cents, f"{got} baskets from {name}", cause)
                        fields.update(stock_units=[*row.stock_units, got], stock_weeks=[*row.stock_weeks, week],
                                      delivered=row.delivered + got)
            fields = _changed(row, **fields)
            if fields:
                store.set("firms", row.id, cause, **fields)


# --- the end of the week -----------------------------------------------------------------


def close_week(store: Store) -> None:
    """Sunday: stock perishes as `econ.firm.Firm.perish` has it, reputations and supplier
    prices move, each shop's week is recorded, and its counters and extra hours return to 0."""
    if clock.dow(store.day) != 6:
        return
    week = clock.week(store.day)
    with store.transaction():
        for row in store.all("firms"):
            f = _firm(store, row)
            spoiled = row.spoiled + f.perish(week)
            f.update_reputation()
            stock_left = f.stock()
            store.add("tills", TillWeek(store.new_id("tw", CLOSE), row.id, week, row.price, row.sold,
                                        row.turned_stock, row.turned_capacity, spoiled, stock_left,
                                        store.balance(row.org) if store.ledger.has(row.org) else 0,
                                        row.delivered, row.takings), CLOSE)
            fields = _changed(row, stock_units=[c.units for c in f.cohorts], stock_weeks=[c.week for c in f.cohorts],
                              reputation=round(f.reputation, _PLACES), sold=0, turned_stock=0,
                              turned_capacity=0, spoiled=0, first_served=0, first_refused=0, extra_hours=0.0,
                              delivered=0, takings=0)
            if fields:
                store.set("firms", row.id, CLOSE, **fields)
        for sp in store.all("suppliers"):
            if sp.base_cents <= 0 or sp.unit_cents <= 0:
                _error(store, "close_week", f"{sp.id} has price {sp.unit_cents} and base {sp.base_cents}; no drift")
                continue
            model = _supplier(sp)
            model.drift(random.Random(f"drift:{store.seed}:{week}:{sp.id}"))
            fields = _changed(sp, unit_cents=model.price, reliability=round(model.p_full, _PLACES))
            if fields:
                store.set("suppliers", sp.id, CLOSE, **fields)
