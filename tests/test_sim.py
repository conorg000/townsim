import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from townsim import market
from townsim.config import build_world
from townsim.decide import Choice, Decision, ModelUnavailable, Option, ReplayDecider
from townsim.events import EventLog, state_hash
from townsim.sim import Simulation, make_deciders
from townsim.world import total_money

from helpers import FailsAtStep, hashes, make_sim, rule_deciders, run_rule_sim


def test_same_seed_gives_identical_hashes(cfg, tmp_path):
    a = run_rule_sim(cfg, tmp_path / "a.sqlite", seed=7, steps=30)
    b = run_rule_sim(cfg, tmp_path / "b.sqlite", seed=7, steps=30)
    assert hashes(a) == hashes(b) and len(hashes(a)) == 30


_RUN_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from townsim.config import load_config
from helpers import hashes, run_rule_sim
sim = run_rule_sim(load_config(sys.argv[2]), sys.argv[3], seed=7, steps=30)
print(" ".join(hashes(sim)))
"""


def test_hashes_do_not_depend_on_the_process_hash_seed(cfg, tmp_path):
    tests_dir = Path(__file__).resolve().parent
    town = tests_dir.parent / "config" / "town.yaml"
    outputs = set()
    for hash_seed in ("1", "2"):
        out = subprocess.run(
            [sys.executable, "-c", _RUN_SCRIPT, str(tests_dir), str(town), str(tmp_path / f"{hash_seed}.sqlite")],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True, text=True, check=True,
        )
        outputs.add(out.stdout.strip())
    assert outputs == {" ".join(hashes(run_rule_sim(cfg, tmp_path / "here.sqlite", 7, 30)))}


def test_different_seed_gives_different_hashes(cfg, tmp_path):
    assert hashes(run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)) != hashes(
        run_rule_sim(cfg, tmp_path / "b.sqlite", 8, 30)
    )


def test_money_is_conserved_over_a_run(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    assert total_money(sim.world) == total_money(build_world(cfg))


def test_model_failure_rolls_back_the_step_and_resume_continues(cfg, tmp_path):
    sim = make_sim(cfg, tmp_path / "a.sqlite", seed=7, deciders={"rule": FailsAtStep(5)})
    with pytest.raises(ModelUnavailable):
        sim.run(10)
    assert sim.world.step == 5
    assert [e.kind for _, e in sim.log.events("r", step=5)] == ["run_aborted"]
    resumed = Simulation.resume(sim.log, "r", make_deciders(cfg, random.Random(0)))
    resumed.run(5)
    assert resumed.world.step == 10


def test_abort_restores_the_same_world_object_and_rng(cfg, tmp_path):
    sim = make_sim(cfg, tmp_path / "a.sqlite", seed=7, deciders={"rule": FailsAtStep(5)})
    world, f1 = sim.world, sim.world.firms["f1"]
    with pytest.raises(ModelUnavailable) as exc:
        sim.run(10)
    saved_world, saved_rng = sim.log.load_snapshot("r", 4)
    assert sim.world is world and sim.world.firms["f1"] is f1
    assert state_hash(sim.world) == state_hash(saved_world)
    assert sim.rng.getstate() == saved_rng
    (_, aborted), = sim.log.events("r", kind="run_aborted")
    assert aborted.payload == {"step": 5, "error": str(exc.value)}
    assert sim.log.latest_snapshot_step("r") == 4


def test_every_step_logs_its_hash_and_a_snapshot_of_the_world_after_it(cfg, tmp_path):
    sim = make_sim(cfg, tmp_path / "a.sqlite", seed=7)
    for n in range(3):
        sim.step()
        world, rng_state = sim.log.load_snapshot("r", n)
        assert world.step == n + 1
        assert state_hash(world) == state_hash(sim.world)
        assert rng_state == sim.rng.getstate()
    logged = [(step, e.payload["hash"]) for step, e in sim.log.events("r", kind="state_hash")]
    assert [step for step, _ in logged] == [0, 1, 2]
    for step, h in logged:
        world, _ = sim.log.load_snapshot("r", step)
        world.step -= 1  # the hash is taken before the step counter moves on
        assert state_hash(world) == h


def test_starting_world_is_saved_before_the_first_step(cfg, tmp_path):
    sim = make_sim(cfg, tmp_path / "a.sqlite", seed=7)
    assert sim.log.latest_snapshot_step("r") == -1
    world, rng_state = sim.log.load_snapshot("r", -1)
    assert state_hash(world) == state_hash(build_world(cfg))
    assert rng_state == random.Random(7).getstate()


def test_snapshots_round_trip_after_a_run(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    world, rng_state = sim.log.load_snapshot("r", 29)
    assert world == sim.world and state_hash(world) == state_hash(sim.world)
    restored = random.Random()
    restored.setstate(rng_state)
    assert restored.random() == sim.rng.random()


def test_snapshot_json_is_canonical(cfg, tmp_path):
    log = EventLog(str(tmp_path / "a.sqlite"))
    a, b = build_world(cfg), build_world(cfg)
    a.laws.illegal = {"x", "y", "z"}
    b.laws.illegal = {"z", "x", "y"}
    log.save_snapshot("a", 0, a, random.Random(1).getstate())
    log.save_snapshot("b", 0, b, random.Random(1).getstate())
    rows = log._conn.execute("SELECT world_json, rng_json FROM snapshots ORDER BY run_id").fetchall()
    assert rows[0] == rows[1]
    assert '"illegal":["x","y","z"]' in rows[0][0]


def test_missing_snapshot_raises(tmp_path):
    log = EventLog(str(tmp_path / "a.sqlite"))
    assert log.latest_snapshot_step("r") is None
    with pytest.raises(ValueError, match="snapshot"):
        log.load_snapshot("r", 0)


def test_resume_continues_from_the_latest_snapshot(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 4)
    resumed = Simulation.resume(sim.log, "r", rule_deciders(cfg, 7))
    assert resumed.world == sim.world and resumed.rng.getstate() == sim.rng.getstate()
    resumed.run(2)
    assert resumed.world.step == 6
    assert [s for s, _ in sim.log.events("r", kind="state_hash")] == list(range(6))


def test_a_run_rebuilds_from_its_log_alone(cfg, tmp_path):
    original = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    recorded = {
        e.payload["decision_id"]: e.payload["option_id"]
        for _, e in original.log.events("r", kind="decision")
    }
    world, rng_state = original.log.load_snapshot("r", -1)
    log = EventLog(str(tmp_path / "b.sqlite"))
    log.create_run("r", 7, "{}")
    replay = Simulation(world, {"rule": ReplayDecider(recorded)}, log, "r", 7)
    replay.rng.setstate(rng_state)
    replay.run(30)
    assert hashes(replay) == hashes(original)


class _Counting:
    def __init__(self, inner):
        self.inner = inner
        self.seen: list[str] = []

    def decide(self, decision):
        self.seen.append(decision.id)
        return self.inner.decide(decision)


def test_every_decision_is_recorded_once_with_a_unique_id(cfg, tmp_path):
    counting = _Counting(rule_deciders(cfg, 7)["rule"])
    sim = make_sim(cfg, tmp_path / "a.sqlite", 7, deciders={"rule": counting})
    sim.run(30)
    recorded = [e.payload["decision_id"] for _, e in sim.log.events("r", kind="decision")]
    assert recorded == counting.seen
    assert recorded and len(set(recorded)) == len(recorded)


class _Bad:
    def decide(self, decision):
        return Choice(option_id="not-offered")


def test_invalid_choice_takes_the_default_and_is_recorded_in_the_step(cfg, tmp_path):
    sim = make_sim(cfg, tmp_path / "a.sqlite", 7, deciders={"rule": _Bad()})
    sim.step()
    kinds = [e.kind for _, e in sim.log.events("r", step=0)]
    assert "invalid_response" in kinds and "decision" in kinds
    assert kinds[-1] == "state_hash"


def test_duplicate_decision_id_in_a_step_is_an_error(cfg, tmp_path, monkeypatch):
    def twice(world, decide, rng):
        d = Decision(
            id=f"{world.step}:p01:same:0",
            actor_id="p01",
            kind="same",
            situation="Choose one.",
            options=[Option(id="a", label="A")],
            default_option_id="a",
        )
        decide(d)
        decide(d)
        return []

    monkeypatch.setattr(market, "owners_phase", twice)
    sim = make_sim(cfg, tmp_path / "a.sqlite", 7)
    start = state_hash(sim.world)
    with pytest.raises(ValueError, match="0:p01:same:0"):
        sim.step()
    assert sim.world.step == 0 and state_hash(sim.world) == start
    assert sim.log.events("r") == []


def test_make_deciders_builds_rule_deciders(cfg):
    deciders = make_deciders(cfg, random.Random(0))
    assert set(deciders) == {"rule"}


