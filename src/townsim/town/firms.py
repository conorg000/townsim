"""Shops, their suppliers, and shopping by the one rule of demand.

The arithmetic is `townsim.econ`'s: `econ.demand.shop` decides who buys what where,
`econ.firm.Firm` serves, refuses and moves reputation, `econ.suppliers.Supplier` fills
orders and drifts its price. This module builds those objects from the store's rows,
runs them, and writes back what they changed through `Store` methods, so every cent
moved is in the ledger with a cause, and every change can be undone and replayed.

Each day `shop_today` puts the buyers whose shopping day it is through the rule with
their whole week's wants. On Mondays `deliver` brings each shop its order; on Sundays
`close_week` throws away old stock, moves reputations and supplier prices, and records
the week.
"""

from __future__ import annotations

import random

from townsim.econ.demand import NEEDS, DemandRule, Household, Offering, shop
from townsim.econ.firm import Cohort, Firm, WeekCounters
from townsim.econ.suppliers import Supplier
from townsim.town import clock
from townsim.town.entities import Buyer, FirmRow, SupplierRow, TillWeek
from townsim.town.store import Store

NEED = "food"
HOURS_PER_STAFF = 38.0
OUTSIDE_BUYERS = "outside:households"
OUT_OF_TOWN = "outside:market"   # where `econ.demand.shop` pays for what is bought out of town
SHOPPING = "routine:shopping"
CLOSE = "routine:close_week"
_CREDIT = 10**15                 # what an account that allows credit is taken to hold
_PLACES = 6


def staff_hours(store: Store, org: str) -> float:
    """Hours worked at `org`'s shops this week: 38 for each of `FirmRow.staff` (standing in
    for the jobs there until jobs exist), plus the shop's `extra_hours`."""
    return sum(HOURS_PER_STAFF * f.staff + f.extra_hours for f in store.where("firms", org=org))


def capacity(store: Store, firm_id: str) -> int:
    """Units the shop can serve in a week: `units_per_staff` for each 38 hours worked."""
    f = store.get("firms", firm_id)
    return int(f.units_per_staff * staff_hours(store, f.org) // HOURS_PER_STAFF)


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


def _cohorts(row: FirmRow) -> list[Cohort]:
    return [Cohort(w, u, 0) for u, w in zip(row.stock_units, row.stock_weeks) if u > 0]


def _firm(store: Store, row: FirmRow) -> Firm:
    """An `econ.firm.Firm` for the row: its id is the shop's organisation account, and its
    capacity the week's, so its count of the week's sales is checked against it."""
    f = Firm(row.org, row.need, row.price, row.quality, row.visibility, row.reputation,
             staff=1, units_per_staff=capacity(store, row.id), wage=0, fixed_cost=0,
             shelf_life=row.shelf_weeks, cohorts=_cohorts(row))
    f.week = WeekCounters(sold=row.sold, turned_away_stock=row.turned_stock,
                          turned_away_capacity=row.turned_capacity,
                          first_attempts=row.first_served + row.first_refused,
                          first_served=row.first_served)
    return f


def _changed(row: object, **fields: object) -> dict:
    return {k: v for k, v in fields.items() if getattr(row, k) != v}


def _econ_id(b: Buyer) -> str:
    """A background buyer is its own household in econ, so it has its own random stream."""
    return b.id if b.account == OUTSIDE_BUYERS else b.account


def shop_today(store: Store) -> None:
    """Every buyer whose `shop_day` is today, and who has not shopped today, buys its week's
    food by the one rule. One transaction; one transfer per (payer, payee)."""
    today = clock.dow(store.day)
    buyers = [b for b in store.where("buyers", shop_day=today) if b.shopped_on != store.day]
    if not buyers:
        return
    spec = NEEDS[NEED]
    rows = [f for f in store.all("firms") if f.need == NEED]
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
        shop(ledger, households, offerings, providers, spec, DemandRule(), clock.week(store.day), 1.0,
             store.seed, SHOPPING)
        totals: dict[tuple[str, str], list[int]] = {}
        for payer, payee, cents in ledger.sales:
            t = totals.setdefault((payer, payee), [0, 0])
            t[0] += cents
            t[1] += 1
        if any(payee == OUT_OF_TOWN for _, payee in totals) and not store.ledger.has(OUT_OF_TOWN):
            store.open_account(OUT_OF_TOWN, SHOPPING)
        for (payer, payee), (cents, units) in sorted(totals.items()):
            memo = f"{NEED} out of town x{units}" if payee == OUT_OF_TOWN else f"{NEED} x{units}"
            store.transfer(payer, payee, cents, memo, SHOPPING)
        for b, hh in zip(buyers, households):
            store.set("buyers", b.id, SHOPPING, shopped_on=store.day,
                      **_changed(b, last_bought=hh.last_bought.get(NEED, "")))
        for row in rows:
            f = providers[row.org]
            w = f.week
            fields = _changed(row, stock_units=[c.units for c in f.cohorts], stock_weeks=[c.week for c in f.cohorts],
                              sold=w.sold, turned_stock=w.turned_away_stock, turned_capacity=w.turned_away_capacity,
                              first_served=w.first_served, first_refused=w.first_attempts - w.first_served)
            if fields:
                store.set("firms", row.id, SHOPPING, **fields)


# --- suppliers -------------------------------------------------------------------------


def _supplier(row: SupplierRow) -> Supplier:
    return Supplier(row.id, row.base_cents or row.unit_cents, row.unit_cents, row.reliability,
                    row.short_low, row.short_high)


def deliver(store: Store) -> None:
    """Monday: each shop gets the order placed since last Monday, or last week's again,
    as far as the supplier fills it and the shop can pay for it, paid on delivery."""
    if clock.dow(store.day) != 0:
        return
    week = clock.week(store.day)
    with store.transaction():
        for row in store.all("firms"):
            cause = f"routine:delivery:{row.id}"
            if row.order_units > 0 and row.order_supplier:
                sid, units = row.order_supplier, row.order_units
            else:
                sid, units = row.last_supplier, row.last_units
            if not sid or units <= 0 or not store.has("suppliers", sid):
                continue
            sp = store.get("suppliers", sid)
            got = _supplier(sp).fill(units, random.Random(f"fill:{store.seed}:{week}:{row.id}"))
            got = min(got, max(0, store.balance(row.org)) // sp.unit_cents)
            fields: dict = dict(last_supplier=sid, last_units=units, order_units=0)
            if got > 0:
                store.transfer(row.org, sp.org, got * sp.unit_cents,
                               f"{got} units from {store.get('orgs', sp.org).name}", cause)
                fields.update(stock_units=[*row.stock_units, got], stock_weeks=[*row.stock_weeks, week])
            fields = _changed(row, **fields)
            if fields:
                store.set("firms", row.id, cause, **fields)


# --- the end of the week -----------------------------------------------------------------


def close_week(store: Store) -> None:
    """Sunday: old stock is thrown away, reputations and supplier prices move, each shop's
    week is recorded, and its counters and extra hours return to 0."""
    if clock.dow(store.day) != 6:
        return
    week = clock.week(store.day)
    with store.transaction():
        for row in store.all("firms"):
            keep = [(u, w) for u, w in zip(row.stock_units, row.stock_weeks) if week - w < row.shelf_weeks]
            thrown = sum(row.stock_units) - sum(u for u, _ in keep)
            spoiled = row.spoiled + thrown
            f = _firm(store, row)
            f.update_reputation()
            store.add("tills", TillWeek(store.new_id("tw", CLOSE), row.id, week, row.price, row.sold,
                                        row.turned_stock, row.turned_capacity, spoiled,
                                        sum(u for u, _ in keep), store.balance(row.org)), CLOSE)
            fields = _changed(row, stock_units=[u for u, _ in keep], stock_weeks=[w for _, w in keep],
                              reputation=round(f.reputation, _PLACES), sold=0, turned_stock=0,
                              turned_capacity=0, spoiled=0, first_served=0, first_refused=0, extra_hours=0.0)
            if fields:
                store.set("firms", row.id, CLOSE, **fields)
        for sp in store.all("suppliers"):
            model = _supplier(sp)
            model.drift(random.Random(f"drift:{store.seed}:{week}:{sp.id}"))
            fields = _changed(sp, unit_cents=model.price, reliability=round(model.p_full, _PLACES))
            if fields:
                store.set("suppliers", sp.id, CLOSE, **fields)
