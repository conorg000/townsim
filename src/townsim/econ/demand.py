"""Households, their needs, and the one rule of demand.

Every offering in town, whenever it was created, states the need it serves, its
price, quality and visibility. Each want a household has is put through the same
rule: score every offering for that need, walk down the ranking buying from the
first that suits (price within willingness to pay and budget, provider able to
serve), else buy out of town if that is worth the hassle, else go without.

The scoring noise is Gumbel, so with no stockouts the choice among the offerings a
household finds acceptable is an exact logit. Only `true_choice_probabilities`
(used by the Oracle, never by an ordinary manager) relies on that.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Protocol

from townsim.econ.ledger import Ledger


@dataclass(frozen=True)
class NeedSpec:
    """Data for one need. Add a need by adding an entry, not code."""

    name: str
    wants_per_person: float  # wants a week for one person, in an average week
    size_exponent: float  # wants scale with household size ** this (economies of scale)
    ref_price: int  # the price households think of as normal, cents
    season_amplitude: float  # +/- share of demand swing over the year
    season_peak_week: int  # week of the year with the highest demand
    outside_price: int  # what it costs out of town, cents
    outside_quality: float
    hassle: int  # cents-equivalent bother of getting it out of town (not paid to anyone)
    wtp_mean: float  # willingness to pay, as a multiple of ref_price, averaged over households
    wtp_sd: float  # spread of that multiple across households (log scale)
    wtp_occasion_sd: float  # how much it varies from one want to the next (log scale)
    budget_factor: float  # weekly budget = expected wants * ref_price * this


NEEDS: dict[str, NeedSpec] = {
    "food": NeedSpec(
        name="food",
        wants_per_person=4.0,
        size_exponent=0.85,
        ref_price=1000,
        season_amplitude=0.22,
        season_peak_week=50,
        outside_price=1150,
        outside_quality=0.6,
        hassle=200,
        wtp_mean=1.12,
        wtp_sd=0.15,
        wtp_occasion_sd=0.12,
        budget_factor=1.5,
    ),
}


@dataclass(frozen=True)
class DemandRule:
    """Weights of the one scoring rule, the same for every offering and household."""

    price: float = 6.0  # utility per unit of -ln(price / ref_price)
    quality: float = 2.0
    reputation: float = 2.5
    visibility: float = 1.0
    habit: float = 0.4  # bonus for where the household last bought this need
    quality_wtp: float = 0.5  # willingness to pay rises by this * (quality - 0.5)
    noise: float = 1.0  # Gumbel scale


@dataclass(frozen=True)
class Offering:
    provider: str
    need: str
    price: int
    quality: float
    visibility: float


class Provider(Protocol):
    id: str
    reputation: float

    def refusal(self) -> str | None:
        """None if it can serve one more unit now, else why not ('stock', 'capacity')."""

    def sell_one(self) -> int:
        """Hand over one unit; return its cost of goods in cents."""

    def note_turned_away(self, reason: str, first_choice: bool) -> None: ...

    def note_first_choice(self, served: bool) -> None: ...


@dataclass
class Household:
    id: str
    size: int
    wtp: dict[str, float]  # need -> multiple of ref_price
    budget: dict[str, int]  # need -> cents a week
    income: int  # cents a week from outside work, pensions and the like
    last_bought: dict[str, str] = field(default_factory=dict)  # need -> provider id

    def expected_wants(self, spec: NeedSpec) -> float:
        return spec.wants_per_person * self.size**spec.size_exponent


def season_factor(spec: NeedSpec, week: int) -> float:
    phase = 2 * math.pi * ((week % 52) - spec.season_peak_week) / 52
    return 1.0 + spec.season_amplitude * math.cos(phase)


def poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    k, p = 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def gumbel(rng: random.Random) -> float:
    u = rng.random()
    while u <= 0.0:
        u = rng.random()
    return -math.log(-math.log(u))


def wtp_cap(
    hh: Household, spec: NeedSpec, rule: DemandRule, quality: float, occasion: float = 1.0
) -> float:
    return hh.wtp[spec.name] * occasion * spec.ref_price * (1 + rule.quality_wtp * (quality - 0.5))


def occasion_factor(spec: NeedSpec, z: float) -> float:
    s = spec.wtp_occasion_sd
    return math.exp(s * z - s * s / 2)


def base_score(
    hh: Household, spec: NeedSpec, rule: DemandRule, off: Offering, reputation: float
) -> float:
    s = (
        rule.quality * off.quality
        - rule.price * math.log(off.price / spec.ref_price)
        + rule.reputation * reputation
        + rule.visibility * off.visibility
    )
    if hh.last_bought.get(spec.name) == off.provider:
        s += rule.habit
    return s


@dataclass
class ShopStats:
    wants: int = 0
    bought_in_town: int = 0
    bought_outside: int = 0
    went_without: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)


def shop(
    ledger: Ledger,
    households: list[Household],
    offerings: list[Offering],
    providers: dict[str, Provider],
    spec: NeedSpec,
    rule: DemandRule,
    week: int,
    demand_mult: float,
    seed: int,
    cause: str = "routine:shopping",
) -> ShopStats:
    """One week of buying for one need, for every household, by the one rule."""
    stats = ShopStats()
    offs = sorted((o for o in offerings if o.need == spec.name), key=lambda o: o.provider)
    mult = season_factor(spec, week) * demand_mult
    # Every household's wants and noise come from its own stream, so one firm's
    # choices do not reshuffle the luck of the whole town (common random numbers).
    queue: list[tuple[Household, float, list[float]]] = []
    n_noise = max(8, len(offs))
    spent: dict[str, int] = {}
    for hh in households:
        rng = random.Random(f"wants:{seed}:{week}:{spec.name}:{hh.id}")
        n = poisson(rng, hh.expected_wants(spec) * mult)
        for _ in range(n):
            occ = occasion_factor(spec, rng.gauss(0.0, 1.0))
            queue.append((hh, occ, [gumbel(rng) * rule.noise for _ in range(n_noise)]))
    random.Random(f"order:{seed}:{week}:{spec.name}").shuffle(queue)
    outside_eff = spec.outside_price + spec.hassle
    for hh, occ, noise in queue:
        stats.wants += 1
        left = hh.budget[spec.name] - spent.get(hh.id, 0)
        cash = ledger.balance(hh.id)
        ranked = []
        for i, off in enumerate(offs):
            prov = providers[off.provider]
            if off.price > wtp_cap(hh, spec, rule, off.quality, occ):
                continue
            if off.price > left or off.price > cash:
                continue
            ranked.append((base_score(hh, spec, rule, off, prov.reputation) + noise[i], i, off))
        ranked.sort(key=lambda t: (-t[0], t[1]))
        bought = False
        for rank, (_, _, off) in enumerate(ranked):
            prov = providers[off.provider]
            why = prov.refusal()
            if why is not None:
                prov.note_turned_away(why, first_choice=rank == 0)
                if rank == 0:
                    prov.note_first_choice(False)
                continue
            if rank == 0:
                prov.note_first_choice(True)
            prov.sell_one()
            ledger.transfer(hh.id, off.provider, off.price, f"{spec.name} x1", cause)
            spent[hh.id] = spent.get(hh.id, 0) + off.price
            hh.last_bought[spec.name] = off.provider
            stats.bought_in_town += 1
            stats.by_provider[off.provider] = stats.by_provider.get(off.provider, 0) + 1
            bought = True
            break
        if bought:
            continue
        cap = wtp_cap(hh, spec, rule, spec.outside_quality, occ)
        if outside_eff <= cap and spec.outside_price <= left and spec.outside_price <= cash:
            ledger.transfer(hh.id, "outside:market", spec.outside_price, f"{spec.name} out of town", cause)
            spent[hh.id] = spent.get(hh.id, 0) + spec.outside_price
            stats.bought_outside += 1
        else:
            stats.went_without += 1
    return stats


def true_choice_probabilities(
    households: list[Household],
    offerings: list[Offering],
    reputations: dict[str, float],
    spec: NeedSpec,
    rule: DemandRule,
) -> dict[str, float]:
    """Expected wants won per provider per unit of town demand multiplier, assuming no
    stockouts. Hidden truth: only the Oracle may be given this."""
    offs = sorted((o for o in offerings if o.need == spec.name), key=lambda o: o.provider)
    out = {o.provider: 0.0 for o in offs}
    # integrate over the occasion-to-occasion spread of willingness to pay
    nodes = [(-2.0201828705, 0.0199532421), (-0.9585724646, 0.3936193232), (0.0, 0.9453087205),
             (0.9585724646, 0.3936193232), (2.0201828705, 0.0199532421)]
    for hh in households:
        w = hh.expected_wants(spec)
        for x, wt in nodes:
            occ = occasion_factor(spec, x * math.sqrt(2))
            share = wt / math.sqrt(math.pi)
            elig = [o for o in offs if o.price <= wtp_cap(hh, spec, rule, o.quality, occ)]
            if not elig:
                continue
            scores = [base_score(hh, spec, rule, o, reputations[o.provider]) / rule.noise for o in elig]
            top = max(scores)
            ws = [math.exp(sc - top) for sc in scores]
            tot = sum(ws)
            for o, e in zip(elig, ws):
                out[o.provider] += w * share * e / tot
    return out
