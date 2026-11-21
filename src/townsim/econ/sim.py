"""The weekly loop of the small economy. No model is called anywhere here.

Each week: deliveries arrive and are paid for; households receive their outside
income and shop by the one rule (season and seeded shocks move how much they want);
wages and fixed costs are paid, or fall into arrears; stock ages and perishes;
reputations update; each manager sees only its own firm's results and public
prices, and sets next week's price, orders and staff; supplier prices drift.

Deterministic given a seed. Every random draw comes from a stream named by what it
is for, so changing one firm's decisions does not reshuffle the town's luck.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from townsim.econ.demand import (
    NEEDS,
    DemandRule,
    Household,
    NeedSpec,
    Offering,
    season_factor,
    shop,
    true_choice_probabilities,
)
from townsim.econ.firm import Firm
from townsim.econ.ledger import Ledger
from townsim.econ.suppliers import Supplier, default_suppliers

OUTSIDE = (
    "outside:capital",  # where opening balances came from
    "outside:suppliers",
    "outside:market",  # shops out of town
    "outside:employers",  # households' income from outside the town's firms
    "outside:landlords",  # firms' rent and running costs
)


@dataclass(frozen=True)
class ShockKind:
    name: str
    mult: float
    weeks: int
    weight: float


SHOCKS: tuple[ShockKind, ...] = (
    ShockKind("cold snap", 1.3, 2, 1.0),  # people stock up and stay local
    ShockKind("market day in the next town", 0.75, 1, 1.0),
    ShockKind("fete", 1.2, 1, 0.6),
)


@dataclass(frozen=True)
class EconConfig:
    n_households: int = 20
    n_firms: int = 3
    need: str = "food"
    shelf_life: int = 2
    carry_loss: float = 0.15
    units_per_staff: int = 30
    wage: int = 4000
    fixed_cost: int = 3500
    start_cash: int = 250_000  # about four weeks of takings: ordinary working capital
    start_price: int = 1000
    start_staff: int = 2
    start_stock: int = 45
    qualities: tuple[float, ...] = (0.6, 0.57, 0.63)
    visibility: float = 0.5
    start_reputation: float = 0.75
    close_after_arrears: int = 4
    shock_rate: float = 0.07  # chance a week starts a shock
    max_staff: int = 6
    horizon: int = 260  # weeks of shock schedule drawn up front


# --- what a manager sees ------------------------------------------------------------


@dataclass(frozen=True)
class WeekRecord:
    """One week of a firm's own books, plus the prices anyone could see."""

    week: int
    price: int
    staff: int
    capacity: int
    ordered: tuple[tuple[str, int], ...]  # supplier, units: the order that was due this week
    delivered: tuple[tuple[str, int], ...]
    paid_unit: tuple[tuple[str, int], ...]
    sold: int
    revenue: int
    turned_away_stock: int  # walk-ins who found the shelves empty
    turned_away_capacity: int  # walk-ins who gave up on the queue
    spoiled: int
    leftover: int  # units still fresh at the end of the week
    cash: int
    arrears_cents: int
    competitor_prices: tuple[int, ...]
    supplier_quotes: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Observation:
    """Everything a manager is shown. Plain values only; no handle on the world."""

    firm_id: str
    week: int  # the week just ended
    price: int
    quality: float
    staff: int
    units_per_staff: int
    wage: int
    fixed_cost: int
    shelf_life: int
    cash: int
    stock_fresh: int  # units that can still be sold next week
    stock_unit_cost: int
    supplier_quotes: tuple[tuple[str, int], ...]
    competitor_prices: tuple[int, ...]
    history: tuple[WeekRecord, ...]  # own books, oldest first, including before the trial


@dataclass(frozen=True)
class Decision:
    price: int
    orders: tuple[tuple[str, int], ...]
    staff: int


class Manager(Protocol):
    name: str

    def decide(self, obs: Observation) -> Decision: ...


@dataclass
class Truth:
    """Hidden parameters, handed only to a manager that declares `uses_truth`."""

    demand_mean: Callable[[int], float]  # price next week -> expected walk-ins won, no stockouts
    supplier_fill: dict[str, tuple[float, float]]  # p_full, mean partial share
    upcoming_mult: tuple[float, ...]  # true demand multipliers for the next few weeks


# --- the economy --------------------------------------------------------------------


@dataclass
class Economy:
    seed: int
    cfg: EconConfig
    spec: NeedSpec
    rule: DemandRule
    ledger: Ledger
    households: list[Household]
    firms: dict[str, Firm]
    suppliers: dict[str, Supplier]
    shocks: list[tuple[int, str, float]]  # start week, name, mult
    week: int = 0
    pending: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)
    history: dict[str, list[WeekRecord]] = field(default_factory=dict)
    shop_log: list[Any] = field(default_factory=list)

    # --- construction ---------------------------------------------------------------
    @classmethod
    def build(cls, seed: int, cfg: EconConfig | None = None, rule: DemandRule | None = None) -> "Economy":
        cfg = cfg or EconConfig()
        rule = rule or DemandRule()
        spec = NEEDS[cfg.need]
        rng = random.Random(f"town:{seed}")
        ledger = Ledger()
        for acct in OUTSIDE:
            ledger.open(acct, allow_credit=True)
        households = []
        for i in range(cfg.n_households):
            size = rng.choices([1, 2, 3, 4, 5], weights=[28, 34, 16, 14, 8])[0]
            wtp = spec.wtp_mean * math.exp(rng.gauss(0, spec.wtp_sd) - spec.wtp_sd**2 / 2)
            hh = Household(f"hh:{i + 1:02d}", size, {spec.name: wtp}, {}, 0)
            budget = round(hh.expected_wants(spec) * spec.ref_price * spec.budget_factor)
            hh.budget[spec.name] = budget
            hh.income = round(budget * rng.uniform(1.3, 2.0))
            ledger.open(hh.id)
            ledger.transfer("outside:capital", hh.id, hh.income * 2, "savings at start", "setup")
            households.append(hh)
        firms = {}
        for j in range(cfg.n_firms):
            fid = f"firm:{j + 1}"
            q = cfg.qualities[j % len(cfg.qualities)]
            f = Firm(
                fid, spec.name, cfg.start_price, q, cfg.visibility, cfg.start_reputation,
                cfg.start_staff, cfg.units_per_staff, cfg.wage, cfg.fixed_cost, cfg.shelf_life,
                cfg.carry_loss,
            )
            f.receive(-1, cfg.start_stock, 680)
            ledger.open(fid)
            ledger.transfer("outside:capital", fid, cfg.start_cash, "owner's capital", "setup")
            firms[fid] = f
        sup = {s.id: s for s in default_suppliers()}
        shocks = []
        srng = random.Random(f"shocks:{seed}")
        for w in range(cfg.horizon):
            if srng.random() < cfg.shock_rate:
                kind = srng.choices(SHOCKS, weights=[k.weight for k in SHOCKS])[0]
                for d in range(kind.weeks):
                    shocks.append((w + d, kind.name, kind.mult))
        econ = cls(seed, cfg, spec, rule, ledger, households, firms, sup, shocks)
        for f in firms.values():
            econ._set_staff(f, cfg.start_staff)
            econ.history[f.id] = []
            econ.pending[f.id] = []
        return econ

    # --- helpers --------------------------------------------------------------------
    def demand_mult(self, week: int) -> float:
        m = 1.0
        for w, _, mult in self.shocks:
            if w == week:
                m *= mult
        return m

    def offerings(self) -> list[Offering]:
        offs = [
            Offering(f.id, f.need, f.price, f.quality, f.visibility)
            for f in self.firms.values()
            if f.open
        ]
        return offs

    def add_firm(self, firm: Firm, cash: int) -> None:
        """Open a new provider mid-run. The demand rule needs nothing new to serve it."""
        self.ledger.open(firm.id)
        self.ledger.transfer("outside:capital", firm.id, cash, "owner's capital", "op:found")
        self.firms[firm.id] = firm
        self.history[firm.id] = []
        self.pending[firm.id] = []
        self._set_staff(firm, firm.staff)

    def _set_staff(self, f: Firm, n: int) -> None:
        n = max(1, min(self.cfg.max_staff, n))
        employed = {e for g in self.firms.values() for e in g.employees}
        while len(f.employees) > n:
            f.employees.pop()
        for hh in self.households:
            if len(f.employees) >= n:
                break
            if hh.id not in employed:
                f.employees.append(hh.id)
                employed.add(hh.id)
        f.staff = len(f.employees)

    def stock_unit_cost(self, f: Firm) -> int:
        s = f.stock()
        return round(f.stock_value() / s) if s else 0

    def observe(self, fid: str) -> Observation:
        f = self.firms[fid]
        return Observation(
            firm_id=fid,
            week=self.week,
            price=f.price,
            quality=f.quality,
            staff=f.staff,
            units_per_staff=f.units_per_staff,
            wage=f.wage,
            fixed_cost=f.fixed_cost,
            shelf_life=f.shelf_life,
            cash=self.ledger.balance(fid),
            stock_fresh=f.stock(),
            stock_unit_cost=self.stock_unit_cost(f),
            supplier_quotes=tuple(sorted((s.id, s.price) for s in self.suppliers.values())),
            competitor_prices=tuple(g.price for g in self.firms.values() if g.id != fid and g.open),
            history=tuple(self.history[fid]),
        )

    def truth_for(self, fid: str) -> Truth:
        nxt = self.week + 1
        mult = season_factor(self.spec, nxt) * self.demand_mult(nxt)
        reps = {f.id: f.reputation for f in self.firms.values()}
        base_offs = [o for o in self.offerings() if o.provider != fid]
        f = self.firms[fid]

        def demand_mean(price: int) -> float:
            offs = base_offs + [Offering(fid, f.need, price, f.quality, f.visibility)]
            probs = true_choice_probabilities(self.households, offs, reps, self.spec, self.rule)
            return probs[fid] * mult

        fill = {
            s.id: (s.p_full, (s.partial_low + s.partial_high) / 2) for s in self.suppliers.values()
        }
        upcoming = tuple(
            season_factor(self.spec, nxt + k) * self.demand_mult(nxt + k) for k in range(4)
        )
        return Truth(demand_mean, fill, upcoming)

    def profit_basis(self, fid: str) -> int:
        """Cash plus stock at cost, less what it owes."""
        f = self.firms[fid]
        return self.ledger.balance(fid) + f.stock_value() - f.arrears_cents

    # --- the week -------------------------------------------------------------------
    def step(self, managers: dict[str, Manager]) -> None:
        w = self.week
        self.ledger.t = w
        cause = "routine"
        # 1. deliveries arrive and are paid on delivery
        deliveries: dict[str, tuple[list, list, list]] = {}
        for fid, f in self.firms.items():
            ordered, delivered, paid = [], [], []
            for sid, units, unit_price in self.pending.get(fid, []):
                ordered.append((sid, units))
                got = 0
                if f.open:
                    rng = random.Random(f"deliver:{self.seed}:{w}:{fid}:{sid}")
                    got = self.suppliers[sid].fill(units, rng)
                    affordable = max(0, self.ledger.balance(fid)) // unit_price
                    got = min(got, affordable)
                if got > 0:
                    self.ledger.transfer(fid, "outside:suppliers", got * unit_price,
                                         f"{got} units from {sid}", f"{cause}:delivery")
                    f.receive(w, got, unit_price)
                delivered.append((sid, got))
                paid.append((sid, unit_price))
            self.pending[fid] = []
            deliveries[fid] = (ordered, delivered, paid)
            f.week = type(f.week)()
        # 2. households get their income and shop
        for hh in self.households:
            self.ledger.transfer("outside:employers", hh.id, hh.income, "income", f"{cause}:income")
        stats = shop(
            self.ledger, self.households, self.offerings(),
            self.firms,
            self.spec, self.rule, w, self.demand_mult(w), self.seed, f"{cause}:shopping",
        )
        self.shop_log.append(stats)
        # 3. wages and fixed costs, or arrears
        for f in self.firms.values():
            if not f.open:
                continue
            due: dict[str, int] = dict(f.owed)  # unpaid bills carried from earlier weeks
            for e in f.employees:
                due[e] = due.get(e, 0) + f.wage
            due["outside:landlords"] = due.get("outside:landlords", 0) + f.fixed_cost
            f.owed = {}
            for dst in sorted(due):
                pay = min(due[dst], max(0, self.ledger.balance(f.id)))
                if pay > 0:
                    kind = "costs" if dst == "outside:landlords" else "wages"
                    self.ledger.transfer(f.id, dst, pay, kind, f"{cause}:{kind}")
                if due[dst] > pay:
                    f.owed[dst] = due[dst] - pay
            f.arrears_cents = sum(f.owed.values())
            f.arrears_weeks = f.arrears_weeks + 1 if f.arrears_cents > 0 else 0
        # 4. stock ages and perishes; 5. reputation
        for f in self.firms.values():
            f.perish(w)
            if f.open:
                f.update_reputation()
        # record the week for each firm
        for fid, f in self.firms.items():
            ordered, delivered, paid = deliveries[fid]
            self.history[fid].append(WeekRecord(
                week=w, price=f.price, staff=f.staff, capacity=f.capacity(),
                ordered=tuple(ordered), delivered=tuple(delivered), paid_unit=tuple(paid),
                sold=f.week.sold, revenue=f.week.revenue,
                turned_away_stock=f.week.turned_away_stock,
                turned_away_capacity=f.week.turned_away_capacity,
                spoiled=f.week.spoiled, leftover=f.stock(), cash=self.ledger.balance(fid),
                arrears_cents=f.arrears_cents,
                competitor_prices=tuple(g.price for g in self.firms.values() if g.id != fid and g.open),
                supplier_quotes=tuple(sorted((s.id, s.price) for s in self.suppliers.values())),
            ))
        # closures
        for f in self.firms.values():
            if f.open and f.arrears_weeks >= self.cfg.close_after_arrears:
                f.open = False
                f.employees = []
                f.staff = 0
        # 6. managers decide next week
        decisions = {}
        for fid, f in self.firms.items():
            if not f.open or fid not in managers:
                continue
            m = managers[fid]
            obs = self.observe(fid)
            if getattr(m, "uses_truth", False):
                decisions[fid] = m.decide(obs, self.truth_for(fid))  # type: ignore[call-arg]
            else:
                decisions[fid] = m.decide(obs)
        for fid, d in decisions.items():
            self._apply(self.firms[fid], d)
        # 7. supplier prices drift
        for s in sorted(self.suppliers.values(), key=lambda s: s.id):
            s.drift(random.Random(f"drift:{self.seed}:{w}:{s.id}"))
        self.week += 1

    def _apply(self, f: Firm, d: Decision) -> None:
        f.price = max(1, min(5 * self.spec.ref_price, int(d.price)))
        orders = []
        for sid, units in d.orders:
            if sid in self.suppliers and units > 0:
                orders.append((sid, int(units), self.suppliers[sid].price))
        self.pending[f.id] = orders
        target = int(d.staff)
        if target > f.staff:
            self._set_staff(f, f.staff + 1)  # hiring takes time: one at a time
        elif target < f.staff:
            self._set_staff(f, f.staff - 1)

    def run(self, managers: dict[str, Manager], weeks: int) -> None:
        for _ in range(weeks):
            self.step(managers)
