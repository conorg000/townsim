"""Plan step 0, "Does skill pay?": put each scripted manager in charge of firm 1 for a
year, with the two rival firms run by Reactive, over several seeds, and compare.

Each seed first runs a year with every firm under Reactive, so that every trial
starts from the same going concern: same cash, stock, staff, reputation, prices and
a year of books. The trial year's profit is the change in cash plus stock at cost,
less anything owed.
"""

from __future__ import annotations

import pickle
from dataclasses import asdict, dataclass
from functools import lru_cache

from townsim.econ.policies import Reactive, make_policy
from townsim.econ.sim import EconConfig, Economy

FIRM = "firm:1"
LEVERS = ("forecasting", "pricing", "suppliers", "staffing")


@lru_cache(maxsize=64)
def _burned_in(seed: int, weeks: int) -> bytes:
    econ = Economy.build(seed, EconConfig())
    econ.run({fid: Reactive() for fid in econ.firms}, weeks)
    return pickle.dumps(econ)


def start_state(seed: int, burn_in: int = 52) -> Economy:
    return pickle.loads(_burned_in(seed, burn_in))


@dataclass(frozen=True)
class TrialResult:
    seed: int
    policy: str
    profit: int  # cents
    fill_rate: float
    spoilage: float  # share of units delivered that spoiled
    reputation: float
    survived: bool


def run_trial(seed: int, policy: str, weeks: int = 52, **flags) -> TrialResult:
    econ = start_state(seed)
    managers = {fid: Reactive() for fid in econ.firms}
    managers[FIRM] = make_policy(policy, **flags)
    start = econ.profit_basis(FIRM)
    first = len(econ.history[FIRM])
    econ.run(managers, weeks)
    recs = econ.history[FIRM][first:]
    walkins = sum(r.sold + r.turned_away_stock + r.turned_away_capacity for r in recs)
    delivered = sum(u for r in recs for _, u in r.delivered)
    f = econ.firms[FIRM]
    return TrialResult(
        seed=seed,
        policy=policy,
        profit=econ.profit_basis(FIRM) - start,
        fill_rate=sum(r.sold for r in recs) / walkins if walkins else 0.0,
        spoilage=sum(r.spoiled for r in recs) / delivered if delivered else 0.0,
        reputation=f.reputation,
        survived=f.open,
    )


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _ratio(a: float, b: float) -> float | None:
    return a / b if b > 0 else None


def experiment(seeds: list[int], weeks: int = 52, levers: bool = True) -> dict:
    results: dict[str, list[TrialResult]] = {}
    for name in ("naive", "reactive", "skilled", "oracle"):
        results[name] = [run_trial(s, name, weeks) for s in seeds]
    mean = {k: _mean(r.profit for r in v) for k, v in results.items()}
    won = sum(s.profit > r.profit for s, r in zip(results["skilled"], results["reactive"]))
    won_naive = sum(s.profit > r.profit for s, r in zip(results["skilled"], results["naive"]))
    # The gate is whether skill pays: on the mean, the skilled manager must clearly beat
    # both ordinary ones, with room above it. Naive against reactive is reported but is
    # not a gate: over many seeds the two are a coin flip, because reacting to noise costs
    # about as much as it gains. Skilled against naive seed by seed is also reported
    # only: over 40 seeds skilled earned about twice as much but lost about one seed in
    # four, so a single year does not always tell them apart.
    ordinary = max(mean["reactive"], mean["naive"])
    checks = {
        "skilled >= 1.25 x the better ordinary manager (mean)": mean["skilled"] >= 1.25 * ordinary,
        "skilled ahead of reactive in >= 80% of seeds": won >= 0.8 * len(seeds),
        "oracle > skilled (mean)": mean["oracle"] > mean["skilled"],
    }
    out = {
        "seeds": seeds,
        "weeks": weeks,
        "policies": {
            k: {
                "mean_profit": mean[k],
                "mean_fill_rate": _mean(r.fill_rate for r in v),
                "mean_spoilage": _mean(r.spoilage for r in v),
                "mean_reputation": _mean(r.reputation for r in v),
                "survived": sum(r.survived for r in v),
                "per_seed": [asdict(r) for r in v],
            }
            for k, v in results.items()
        },
        "ratios": {
            "skilled/reactive": _ratio(mean["skilled"], mean["reactive"]),
            "reactive/naive": _ratio(mean["reactive"], mean["naive"]),
            "oracle/skilled": _ratio(mean["oracle"], mean["skilled"]),
        },
        "skilled_seeds_won": won,
        "skilled_seeds_won_vs_naive": won_naive,
        "checks": checks,
        "pass": all(checks.values()),
    }
    if levers:
        att = {}
        for lever in LEVERS:
            rs = [run_trial(s, "skilled", weeks, **{lever: False}) for s in seeds]
            m = _mean(r.profit for r in rs)
            att[lever] = {
                "mean_profit": m,
                "share_of_full": _ratio(m, mean["skilled"]),
                "share_of_gap_lost": (mean["skilled"] - m) / (mean["skilled"] - mean["reactive"])
                if mean["skilled"] != mean["reactive"] else None,
            }
        out["levers_disabled"] = att
    return out


def _money(cents: float) -> str:
    return f"${cents / 100:,.0f}"


def _fmt_ratio(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def format_table(res: dict) -> str:
    lines = [f"Does skill pay? {len(res['seeds'])} seeds, {res['weeks']} weeks; firm 1 run by each "
             "policy, two rivals run by reactive. No model was called.", ""]
    lines.append(f"{'policy':<10}{'mean profit':>13}{'fill':>7}{'spoil':>7}{'rep':>6}{'alive':>7}")
    for k, v in res["policies"].items():
        lines.append(
            f"{k:<10}{_money(v['mean_profit']):>13}{v['mean_fill_rate']:>7.2f}"
            f"{v['mean_spoilage']:>7.2f}{v['mean_reputation']:>6.2f}"
            f"{v['survived']:>4}/{len(res['seeds'])}"
        )
    lines += ["", "Per seed (profit):", "seed  " + "".join(f"{k:>11}" for k in res["policies"])]
    for i, s in enumerate(res["seeds"]):
        lines.append(f"{s:<6}" + "".join(
            f"{_money(v['per_seed'][i]['profit']):>11}" for v in res["policies"].values()))
    lines.append("")
    for k, v in res["ratios"].items():
        lines.append(f"{k:<18}{_fmt_ratio(v)}")
    lines.append(f"skilled beat reactive in {res['skilled_seeds_won']}/{len(res['seeds'])} seeds")
    lines.append(f"skilled beat naive in {res['skilled_seeds_won_vs_naive']}/{len(res['seeds'])} seeds")
    if "levers_disabled" in res:
        lines += ["", "Skilled with one skill turned off:",
                  f"{'off':<13}{'mean profit':>13}{'of full':>9}{'gap lost':>10}"]
        for k, v in res["levers_disabled"].items():
            lines.append(f"{k:<13}{_money(v['mean_profit']):>13}{_fmt_ratio(v['share_of_full']):>9}"
                         f"{_fmt_ratio(v['share_of_gap_lost']):>10}")
    lines.append("")
    for k, ok in res["checks"].items():
        lines.append(f"  [{'ok' if ok else 'no'}] {k}")
    lines.append(f"Gate: {'PASS' if res['pass'] else 'FAIL'}")
    return "\n".join(lines)
