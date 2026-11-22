"""Standard rates and curves for work done in plain words (design 4.3).

Code reads only these: what a kind of work costs and how long it takes at each scale, what putting
money into one attribute of a shop costs and how far it moves it, what a new shop starts as, and the
bounds the outside desks keep to. Every figure is fixed: the same for everyone, in every run. All
money is in cents; weeks are whole.
"""

from __future__ import annotations

import math
import statistics

SCALES = ("small", "medium", "large")

PROJECT: dict[str, dict[str, tuple[int, int]]] = {
    "fit_out": {"small": (400_000, 3), "medium": (1_200_000, 5), "large": (4_000_000, 10)},
    "repair": {"small": (60_000, 1), "medium": (250_000, 2), "large": (800_000, 4)},
    "campaign": {"small": (30_000, 1), "medium": (120_000, 2), "large": (400_000, 4)},
    "petition": {"small": (15_000, 1), "medium": (50_000, 2), "large": (150_000, 3)},
    "event": {"small": (25_000, 1), "medium": (100_000, 2), "large": (350_000, 3)},
    "survey": {"small": (20_000, 1), "medium": (70_000, 2), "large": (200_000, 3)},
    "training": {"small": (40_000, 2), "medium": (150_000, 4), "large": (500_000, 8)},
}

INVEST: dict[str, dict[str, tuple[int, int]]] = {
    "quality": {"small": (150_000, 2), "medium": (500_000, 4), "large": (1_500_000, 8)},
    "visibility": {"small": (30_000, 1), "medium": (120_000, 2), "large": (400_000, 4)},
    "reputation": {"small": (80_000, 2), "medium": (300_000, 4), "large": (900_000, 8)},
    "capacity": {"small": (200_000, 2), "medium": (700_000, 4), "large": (2_000_000, 6)},
}

REGISTRATION = 15_000          # to the regional office

# A new food shop, by the scale of its fit-out: its premises' weekly rent to the letting agency, and
# where it starts. Its price is the median of the place's shop prices (`new_shop_price`).
NEW_SHOP: dict[str, dict] = {
    scale: {"rent_cents": rent, "visibility": vis, "quality": qual, "reputation": 0.40, "units_per_staff": 160,
            "shelf_weeks": 2}
    for scale, rent, vis, qual in (("small", 15_000, 0.30, 0.55), ("medium", 25_000, 0.45, 0.60),
                                   ("large", 35_000, 0.60, 0.65))
}

# attribute -> (cap, cents at which a raise has gone 1 - 1/e of the way to the cap); capacity's cap is a
# multiple of the shop's units_per_staff when it was first invested in
CURVE: dict[str, tuple[float, int]] = {
    "quality": (0.95, 600_000),
    "visibility": (1.0, 150_000),
    "reputation": (0.95, 800_000),
    "capacity": (1.5, 1_000_000),
}
ATTRIBUTES = tuple(CURVE)

VISIBILITY_HALF_WEEKS = 8

# units a week (the average of the last four weeks' deliveries from a supplier) -> the most percent a
# supplier's desk gives
DISCOUNT_TIERS = ((1000, 6), (600, 4), (300, 2))

SPENDING_BAND = (0.90, 1.10)
SPENDING_EVERY_WEEKS = 8

_PLACES = 6


def raise_attribute(level: float, base: float, attribute: str, cents: int) -> float:
    """Where `cents` put into `attribute` takes it from `level`: `level + (top - level) *
    (1 - exp(-cents / k))`, `top` the cap (for capacity, the cap times `base`). Never past the cap,
    never down. Rounded to 6 places."""
    cap, k = CURVE[attribute]
    top = cap * base if attribute == "capacity" else cap
    if cents <= 0 or level >= top:
        return round(level, _PLACES)
    return round(level + (top - level) * (1 - math.exp(-cents / k)), _PLACES)


def decayed(visibility: float, base: float) -> float:
    """A week's fading of visibility above its base: the excess halves every `VISIBILITY_HALF_WEEKS`."""
    if visibility <= base:
        return visibility
    return round(base + (visibility - base) * 0.5 ** (1 / VISIBILITY_HALF_WEEKS), _PLACES)


def most_discount(units_a_week: float) -> int:
    """The most percent of discount a supplier gives on `units_a_week`."""
    return next((pct for units, pct in DISCOUNT_TIERS if units_a_week >= units), 0)


def new_shop_price(store) -> int:
    """A new shop's price: the median of the place's shop prices, rounded to 5 cents."""
    prices = [f.price for f in store.all("firms") if f.price > 0]
    if not prices:
        return 1000
    return int(5 * round(statistics.median(prices) / 5))


def min_wage_cents(store) -> int:
    """The lowest hourly pay of any job in the store, rounded down to 25 cents (worked out once, when
    lane B is installed, for the rulebook page)."""
    rates = [j.weekly / j.hours for j in store.all("jobs") if j.hours > 0 and j.weekly > 0]
    if not rates:
        return 0
    return int(min(rates) // 25 * 25)
