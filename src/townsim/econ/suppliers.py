"""Suppliers outside the town. They differ in price and reliability; prices drift.

Orders are placed at the end of a week at that week's quoted price and arrive at
the start of the next, paid on delivery to `outside:suppliers`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class Supplier:
    id: str
    base_price: int  # cents per unit the price reverts towards
    price: int  # today's quote, public
    p_full: float  # chance an order arrives complete (hidden)
    partial_low: float  # share delivered when it does not (hidden)
    partial_high: float
    drift_sd: float = 0.015  # weekly log random walk (about 5% typical swing either way)
    reversion: float = 0.05  # pull back towards base each week

    def fill(self, ordered: int, rng: random.Random) -> int:
        if ordered <= 0:
            return 0
        if rng.random() < self.p_full:
            return ordered
        share = rng.uniform(self.partial_low, self.partial_high)
        return int(ordered * share)

    def expected_fill(self) -> float:
        return self.p_full + (1 - self.p_full) * (self.partial_low + self.partial_high) / 2

    def drift(self, rng: random.Random) -> None:
        x = math.log(self.price / self.base_price)
        x = (1 - self.reversion) * x + rng.gauss(0.0, self.drift_sd)
        self.price = max(1, round(self.base_price * math.exp(x)))


def default_suppliers() -> list[Supplier]:
    return [
        # A wholesaler's van: about 5% cheaper, but nearly one order in three comes up short.
        Supplier("cheap", base_price=665, price=665, p_full=0.7, partial_low=0.2, partial_high=0.65),
        # A regional distributor: dearer, almost always complete.
        Supplier("reliable", base_price=700, price=700, p_full=0.97, partial_low=0.85, partial_high=0.95),
    ]
