"""A firm: cash in the ledger, a price, staff, perishable stock in dated cohorts,
a lagging reputation and a quality."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Cohort:
    week: int  # week delivered
    units: int
    unit_cost: int  # cents paid per unit


@dataclass
class WeekCounters:
    sold: int = 0
    revenue: int = 0
    cogs: int = 0
    turned_away_stock: int = 0
    turned_away_capacity: int = 0
    first_attempts: int = 0
    first_served: int = 0
    spoiled: int = 0
    spoiled_cost: int = 0


@dataclass
class Firm:
    id: str
    need: str
    price: int
    quality: float
    visibility: float
    reputation: float
    staff: int
    units_per_staff: int
    wage: int  # cents per staff per week
    fixed_cost: int  # cents per week (rent, power, rates)
    shelf_life: int = 2  # weeks a cohort can be sold in, counting its delivery week
    carry_loss: float = 0.15  # share of a cohort carried into another week that wilts or goes stale
    cohorts: list[Cohort] = field(default_factory=list)
    employees: list[str] = field(default_factory=list)
    open: bool = True
    arrears_weeks: int = 0  # consecutive weeks it could not pay its bills in full
    arrears_cents: int = 0  # unpaid wages and costs owed
    owed: dict[str, int] = field(default_factory=dict)  # creditor -> cents unpaid
    week: WeekCounters = field(default_factory=WeekCounters)

    # --- stock -----------------------------------------------------------------
    def stock(self) -> int:
        return sum(c.units for c in self.cohorts)

    def stock_value(self) -> int:
        return sum(c.units * c.unit_cost for c in self.cohorts)

    def receive(self, week: int, units: int, unit_cost: int) -> None:
        if units > 0:
            self.cohorts.append(Cohort(week, units, unit_cost))

    def capacity(self) -> int:
        return self.staff * self.units_per_staff

    def perish(self, week: int) -> int:
        """End of `week`: drop cohorts whose last sellable week was this one, and lose
        `carry_loss` of the rest as they go into another week (stale bread, soft fruit)."""
        spoiled = 0
        keep = []
        for c in self.cohorts:
            if c.week + self.shelf_life - 1 <= week:
                lost = c.units
            else:
                lost = round(c.units * self.carry_loss)
            if lost:
                spoiled += lost
                self.week.spoiled_cost += lost * c.unit_cost
                c.units -= lost
            if c.units > 0:
                keep.append(c)
        self.cohorts = keep
        self.week.spoiled += spoiled
        return spoiled

    # --- the Provider protocol used by the demand rule ---------------------------
    def refusal(self) -> str | None:
        if not self.open:
            return "closed"
        if self.week.sold >= self.capacity():
            return "capacity"
        if self.stock() <= 0:
            return "stock"
        return None

    def sell_one(self) -> int:
        oldest = self.cohorts[0]  # oldest first
        oldest.units -= 1
        if oldest.units == 0:
            self.cohorts.pop(0)
        self.week.sold += 1
        self.week.revenue += self.price
        self.week.cogs += oldest.unit_cost
        return oldest.unit_cost

    def note_turned_away(self, reason: str, first_choice: bool) -> None:
        if reason == "capacity":
            self.week.turned_away_capacity += 1
        elif reason == "stock":
            self.week.turned_away_stock += 1

    def note_first_choice(self, served: bool) -> None:
        self.week.first_attempts += 1
        if served:
            self.week.first_served += 1

    def update_reputation(self, smoothing: float = 0.15, fill_weight: float = 0.7) -> float:
        """An exponential average of first-choice fill rate and quality, so it lags."""
        w = self.week
        if not w.first_attempts:
            return 1.0
        fill = w.first_served / w.first_attempts
        target = fill_weight * fill + (1 - fill_weight) * self.quality
        self.reputation = (1 - smoothing) * self.reputation + smoothing * target
        return fill
