from townsim.config import build_world
from townsim.measures import stability
from townsim.world import total_money

from helpers import run_rule_sim


def test_baseline_town_is_stable_for_two_years(cfg, tmp_path):
    for seed in (1, 2, 3, 4, 5):
        sim = run_rule_sim(cfg, tmp_path / f"{seed}.sqlite", seed, 104)
        verdict = stability(sim.log, "r", total_money(build_world(cfg)))
        assert verdict["stable"], (seed, verdict["failures"])
