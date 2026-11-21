"""Scripted managers for one firm. Each sees only an `Observation` of its own books
and public prices; only the Oracle is also handed the hidden truth.

- Naive: a fixed markup, reorders what it sold, the cheap supplier, staff unchanged.
- Reactive: a sensible hill-climber that nudges order and price from last week.
- Skilled: forecasts demand (season, trend, own price response), orders by the
  newsvendor ratio with observed supplier reliability, prices near its estimated
  profit maximum, and sizes staff to the forecast.
- Oracle: the Skilled machinery fed true demand and true reliability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

from townsim.econ.sim import Decision, Observation, Truth, WeekRecord

_N = NormalDist()
CHEAP, RELIABLE = "cheap", "reliable"


def _quote(obs: Observation, sid: str) -> int:
    return dict(obs.supplier_quotes)[sid]


def _demand_seen(r: WeekRecord) -> int:
    """Walk-ins: those served plus those turned away (the lost sales a shop can see)."""
    return r.sold + r.turned_away_stock + r.turned_away_capacity


def _ordered_total(r: WeekRecord) -> int:
    return sum(u for _, u in r.ordered)


# --- Naive ------------------------------------------------------------------------


@dataclass
class Naive:
    """Keeps the markup it was already charging, reorders last week's sales plus a
    little, buys from the cheap supplier, never changes staff.

    "Sales" counts the customers it had to turn away for want of stock: a shopkeeper
    who sold out knows people asked. Without that, reordering exactly what was sold
    ratchets down after every short delivery until the shop stocks a tenth of what
    is asked for, which no real shopkeeper would do."""

    name: str = "naive"
    buffer: float = 1.1
    markup: float | None = None  # fixed at the first decision from the shop's own books

    def decide(self, obs: Observation) -> Decision:
        last = obs.history[-1]
        if self.markup is None:
            self.markup = inherited_markup(obs)
        price = round(_quote(obs, CHEAP) * self.markup)
        units = max(1, math.ceil((last.sold + last.turned_away_stock) * self.buffer))
        return Decision(price, ((CHEAP, units),), obs.staff)


def inherited_markup(obs: Observation) -> float:
    """Price over unit cost paid, from the most recent delivery in the books."""
    for r in reversed(obs.history):
        paid = [(u, c) for (_, u), (_, c) in zip(r.delivered, r.paid_unit) if u > 0]
        if paid:
            cost = sum(u * c for u, c in paid) / sum(u for u, _ in paid)
            return obs.price / cost
    return 1.4


# --- Reactive ---------------------------------------------------------------------


@dataclass
class Reactive:
    name: str = "reactive"
    burn_memory: int = 26  # weeks it remembers short deliveries

    def decide(self, obs: Observation) -> Decision:
        h = obs.history
        last = h[-1]
        available = last.sold + last.leftover + last.spoiled
        base = _ordered_total(last) or last.sold
        sold_out = last.leftover == 0 and last.turned_away_stock > 0
        lots_left = last.leftover + last.spoiled > 0.15 * max(1, available)
        if sold_out:
            units = math.ceil(base * 1.15)
        elif last.spoiled > 0 or lots_left:
            units = math.floor(base * 0.92)
        else:
            units = base
        units = max(5, units)
        # price: up when it sells out, down when stock is left over
        price = obs.price
        if sold_out:
            price = round(price * 1.03)
        elif lots_left:
            price = round(price * 0.98)
        cost = max(_quote(obs, CHEAP), _quote(obs, RELIABLE))
        price = max(round(cost * 1.1), min(round(cost * 2.0), price))
        # supplier: the cheap one unless it has let us down twice lately
        burns = 0
        for r in h[-self.burn_memory:]:
            for (sid, o), (_, d) in zip(r.ordered, r.delivered):
                if sid == CHEAP and o > 0 and d < 0.9 * o:
                    burns += 1
        sid = RELIABLE if burns >= 2 else CHEAP
        # staff: every four weeks, move one towards what recent sales need
        staff = obs.staff
        if obs.week % 4 == 0 and len(h) >= 4:
            recent = sum(r.sold for r in h[-4:]) / 4
            target = max(1, math.ceil(recent * 1.1 / obs.units_per_staff))
            staff += (target > staff) - (target < staff)
        return Decision(price, ((sid, units),), staff)


# --- tools shared by Skilled and Oracle ---------------------------------------------


def _loss(z: float) -> float:
    """E[(Z - z)+] for a standard normal Z."""
    return _N.pdf(z) - z * (1 - _N.cdf(z))


def exp_min(mu: float, sd: float, m: float) -> float:
    """E[min(D, m)] for D ~ Normal(mu, sd), m >= 0."""
    if m <= 0:
        return 0.0
    if sd <= 1e-9:
        return min(mu, m)
    return max(0.0, mu - sd * _loss((m - mu) / sd))


@dataclass(frozen=True)
class Plan:
    price: int
    orders: tuple[tuple[str, int], ...]
    staff: int
    value: float


def evaluate(
    price: float, mu: float, sd: float, carry: int, orders: dict[str, int],
    quotes: dict[str, int], fills: dict[str, tuple[float, float]], staff: int, k: int,
    wage: int, spoil_share: float, goodwill: float,
) -> float:
    """Expected one-week value of a plan, over full/short delivery outcomes."""
    scen = [(1.0, carry, 0.0)]
    for sid, q in orders.items():
        if q <= 0:
            continue
        pf, part = fills[sid]
        nxt = []
        for p, a, cost in scen:
            nxt.append((p * pf, a + q, cost + q * quotes[sid]))
            got = q * part
            nxt.append((p * (1 - pf), a + got, cost + got * quotes[sid]))
        scen = nxt
    cap = staff * k
    c_unit = min(quotes.values())
    total = 0.0
    for p, avail, cost in scen:
        m = min(avail, cap)
        sales = exp_min(mu, sd, m)
        short = max(0.0, mu - sales)
        left = max(0.0, avail - sales)
        total += p * (price * sales - cost + (1 - spoil_share) * c_unit * left - goodwill * short)
    return total - wage * staff


def best_plan(
    obs: Observation, mu_at, sd_at, prices: list[int], fills: dict[str, tuple[float, float]],
    spoil_share: float, use_suppliers: bool = True, use_staffing: bool = True,
) -> Plan:
    quotes = dict(obs.supplier_quotes)
    k, wage = obs.units_per_staff, obs.wage
    carry = obs.stock_fresh
    sids = [CHEAP, RELIABLE] if use_suppliers else [CHEAP]
    eff = {s: quotes[s] / (fills[s][0] + (1 - fills[s][0]) * fills[s][1]) for s in sids}
    c = min(eff.values())

    def target(price: float, mu: float, sd: float) -> float:
        under = price - c + 0.5 * (price - c)  # lost margin plus goodwill
        over = c * spoil_share
        cr = min(0.98, max(0.02, under / (under + over)))
        return mu + sd * _N.inv_cdf(cr)

    def staff_for(units: float) -> int:
        return max(1, min(6, math.ceil(units / k)))

    # 1. price: compare candidates at their own newsvendor stock, ample staff
    best_p, best_v = prices[0], -1e18
    for p in prices:
        mu, sd = mu_at(p), sd_at(p)
        q = max(0.0, target(p, mu, sd) - carry)
        n = staff_for(target(p, mu, sd)) if use_staffing else obs.staff
        sid = min(eff, key=eff.get)
        v = evaluate(p, mu, sd, carry, {sid: round(q / (fills[sid][0] + (1 - fills[sid][0]) * fills[sid][1]))},
                     quotes, fills, n, k, wage, spoil_share, 0.5 * (p - c))
        if v > best_v:
            best_p, best_v = p, v
    p = best_p
    mu, sd = mu_at(p), sd_at(p)
    g = 0.5 * (p - c)
    need = max(0.0, target(p, mu, sd) - carry)
    # 2. orders: split between suppliers and pad for short deliveries
    best = None
    splits = [0.0, 0.25, 0.5, 0.75, 1.0] if use_suppliers else [1.0]
    n0 = staff_for(target(p, mu, sd)) if use_staffing else obs.staff
    for s in splits:
        for pad in (1.0, 1.1, 1.25):
            o = {CHEAP: round(need * s * pad)}
            if use_suppliers:
                o[RELIABLE] = round(need * (1 - s) * pad)
            v = evaluate(p, mu, sd, carry, o, quotes, fills, n0, k, wage, spoil_share, g)
            if best is None or v > best[0]:
                best = (v, o)
    _, orders = best
    # 3. staff for that stock
    staff = obs.staff
    if use_staffing:
        bv = None
        for n in range(1, 7):
            v = evaluate(p, mu, sd, carry, orders, quotes, fills, n, k, wage, spoil_share, g)
            if bv is None or v > bv:
                bv, staff = v, n
    return Plan(int(p), tuple(sorted((s, q) for s, q in orders.items() if q > 0)), staff, best[0])


def _spoil_share(h: tuple[WeekRecord, ...]) -> float:
    """Of what was left at the end of a week, how much spoiled by the end of the next."""
    left = sum(r.leftover for r in h[-13:-1])
    spoiled = sum(r.spoiled for r in h[-12:])
    if left <= 0:
        return 0.5
    return min(1.0, max(0.1, spoiled / left))


def observed_fills(h: tuple[WeekRecord, ...]) -> dict[str, tuple[float, float]]:
    """Each supplier's record from our own deliveries, shrunk towards a trade prior."""
    out = {}
    for sid in (CHEAP, RELIABLE):
        n = full = 0
        part_sum, part_n = 0.0, 0
        for r in h:
            for (s, o), (_, d) in zip(r.ordered, r.delivered):
                if s != sid or o <= 0:
                    continue
                n += 1
                if d >= o:
                    full += 1
                else:
                    part_sum += d / o
                    part_n += 1
        pf = (full + 0.85 * 4) / (n + 4)
        part = (part_sum + 0.6 * 2) / (part_n + 2)
        out[sid] = (pf, part)
    return out


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(m[r][i]))
        m[i], m[piv] = m[piv], m[i]
        if abs(m[i][i]) < 1e-12:
            m[i][i] = 1e-12
        for r in range(n):
            if r != i:
                f = m[r][i] / m[i][i]
                for c in range(i, n + 1):
                    m[r][c] -= f * m[i][c]
    return [m[i][n] / m[i][i] for i in range(n)]


@dataclass
class DemandModel:
    coef: list[float]  # const, cos, sin, log relative price, trend
    resid_level: float
    sd_log: float

    def log_mu(self, week: int, rel_price: float, t: float) -> float:
        ang = 2 * math.pi * (week % 52) / 52
        c = self.coef
        return c[0] + c[1] * math.cos(ang) + c[2] * math.sin(ang) + c[3] * math.log(rel_price) + c[4] * t

    @property
    def elasticity(self) -> float:
        return min(8.0, max(1.2, -self.coef[3]))


def fit_demand(h: tuple[WeekRecord, ...], prior_elasticity: float = 3.0) -> DemandModel:
    """Weighted least squares on the shop's own books: log walk-ins against the time of
    year, its price relative to the others' and a trend; ridge-shrunk towards priors."""
    T = h[-1].week
    rows, ys, ws = [], [], []
    for r in h:
        d = _demand_seen(r)
        if d <= 0 or not r.competitor_prices:
            continue
        pc = sum(r.competitor_prices) / len(r.competitor_prices)
        ang = 2 * math.pi * (r.week % 52) / 52
        rows.append([1.0, math.cos(ang), math.sin(ang), math.log(r.price / pc), (r.week - T) / 52])
        ys.append(math.log(d))
        ws.append(0.5 ** ((T - r.week) / 52))
    prior = [0.0, 0.0, 0.0, -prior_elasticity, 0.0]
    strength = [0.0, 2.0, 2.0, 0.02, 4.0]  # in units of squared-feature-weight
    a = [[0.0] * 5 for _ in range(5)]
    b = [0.0] * 5
    for x, y, w in zip(rows, ys, ws):
        for i in range(5):
            b[i] += w * x[i] * y
            for j in range(5):
                a[i][j] += w * x[i] * x[j]
    for i in range(5):
        a[i][i] += strength[i] + 1e-6
        b[i] += strength[i] * prior[i]
    coef = _solve(a, b)
    model = DemandModel(coef, 0.0, 0.15)
    # recent level: smoothed residual; spread: residual sd
    res = [y - sum(c * xi for c, xi in zip(coef, x)) for x, y in zip(rows, ys)]
    lvl = 0.0
    for e in res[-8:]:
        lvl = 0.6 * lvl + 0.4 * e
    tail = res[-26:]
    if len(tail) >= 4:
        mean = sum(tail) / len(tail)
        model.sd_log = max(0.06, math.sqrt(sum((e - mean) ** 2 for e in tail) / (len(tail) - 1)))
    model.resid_level = lvl
    return model


# --- Skilled ----------------------------------------------------------------------


@dataclass
class Skilled:
    name: str = "skilled"
    forecasting: bool = True
    pricing: bool = True
    suppliers: bool = True
    staffing: bool = True
    max_step: float = 0.06  # most it moves price in a week
    markup: float | None = None  # with pricing off: the inherited markup, as Naive
    def decide(self, obs: Observation) -> Decision:
        h = obs.history
        model = fit_demand(h)
        pc = sum(obs.competitor_prices) / len(obs.competitor_prices) if obs.competitor_prices else obs.price
        nxt = obs.week + 1
        if self.forecasting:
            def mu_at(p: float) -> float:
                return math.exp(model.log_mu(nxt, p / pc, 1 / 52) + 0.7 * model.resid_level)
            sd_log = model.sd_log
        else:  # level from the last four weeks only, no season or trend
            recent = [_demand_seen(r) for r in h[-4:]]
            lvl = sum(recent) / len(recent)
            e = model.elasticity

            def mu_at(p: float) -> float:
                return lvl * (p / obs.price) ** (-e)
            spread = [_demand_seen(r) for r in h[-8:]]
            m = sum(spread) / len(spread)
            sd_log = max(0.06, math.sqrt(sum((x - m) ** 2 for x in spread) / max(1, len(spread) - 1)) / max(1, m))

        def sd_at(p: float) -> float:
            mu = mu_at(p)
            return max(math.sqrt(max(mu, 1.0)), mu * sd_log)

        if self.pricing:
            lo, hi = obs.price * (1 - self.max_step), obs.price * (1 + self.max_step)
            prices = sorted({round(min(hi, max(lo, pc * f))) for f in [0.85 + 0.015 * i for i in range(21)]}
                            | {obs.price})
        else:
            if self.markup is None:
                self.markup = inherited_markup(obs)
            prices = [round(min(dict(obs.supplier_quotes).values()) * self.markup)]
        fills = observed_fills(h)
        plan = best_plan(obs, mu_at, sd_at, prices, fills, _spoil_share(h),
                         use_suppliers=self.suppliers, use_staffing=self.staffing)
        orders = plan.orders if plan.orders else ((CHEAP, 0),)
        return Decision(plan.price, orders, plan.staff)


# --- Oracle -----------------------------------------------------------------------


@dataclass
class Oracle:
    name: str = "oracle"
    uses_truth: bool = True

    def decide(self, obs: Observation, truth: Truth) -> Decision:
        pc = sum(obs.competitor_prices) / len(obs.competitor_prices) if obs.competitor_prices else obs.price
        cache: dict[int, float] = {}

        def mu_at(p: float) -> float:
            key = round(p)
            if key not in cache:
                cache[key] = truth.demand_mean(key)
            return cache[key]

        def sd_at(p: float) -> float:
            return math.sqrt(max(1.0, mu_at(p)))

        prices = sorted({round(pc * (0.8 + 0.02 * i)) for i in range(26)})
        plan = best_plan(obs, mu_at, sd_at, prices, truth.supplier_fill, _spoil_share(obs.history))
        orders = plan.orders if plan.orders else ((CHEAP, 0),)
        return Decision(plan.price, orders, plan.staff)


POLICIES = {"naive": Naive, "reactive": Reactive, "skilled": Skilled, "oracle": Oracle}


def make_policy(name: str, **flags) -> object:
    return POLICIES[name](**flags)
