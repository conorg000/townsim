import random

import pytest

from townsim.decide import ModelUnavailable
from townsim.events import state_hash
from townsim.runs import ReplayMismatch, fork, replay
from townsim.sim import Simulation, make_deciders

from helpers import (
    FailsAtStep,
    delete_first_decision_at_step,
    make_sim,
    rule_deciders,
    run_rule_sim,
)


def test_replay_of_an_untouched_run_passes(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    replay(sim.log, "r")


def test_replay_detects_a_tampered_decision(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    delete_first_decision_at_step(sim.log, "r", 12)
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 12 and "12:" in e.value.detail


def test_forks_share_history_and_diverge_by_seed(cfg, tmp_path):
    base = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 26)
    f1 = fork(base.log, "r", 26, "f1", seed=1, deciders=rule_deciders(cfg)); f1.run(26)
    f2 = fork(base.log, "r", 26, "f2", seed=2, deciders=rule_deciders(cfg)); f2.run(26)
    assert f1.world.step == f2.world.step == 52
    assert state_hash(f1.world) != state_hash(f2.world)
    replay(base.log, "f1")


def test_fork_at_a_step_with_no_snapshot_is_an_error(cfg, tmp_path):
    base = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 5)
    with pytest.raises(ValueError, match="snapshot"):
        fork(base.log, "r", 99, "f1", 1, rule_deciders(cfg))


def test_fork_records_parent_and_starts_from_the_parent_world(cfg, tmp_path):
    base = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 10)
    parent_world, _ = base.log.load_snapshot("r", 5)
    f = fork(base.log, "r", 6, "f", seed=3, deciders=rule_deciders(cfg))
    row = base.log.get_run("f")
    assert row["parent_run_id"] == "r" and row["parent_step"] == 6
    assert row["config_json"] == base.log.get_run("r")["config_json"]
    assert f.world.step == 6 and state_hash(f.world) == state_hash(parent_world)


def test_fork_at_step_zero_starts_from_the_starting_world(cfg, tmp_path):
    base = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 3)
    start, _ = base.log.load_snapshot("r", -1)
    f = fork(base.log, "r", 0, "f", seed=7, deciders=rule_deciders(cfg))
    assert f.world.step == 0 and state_hash(f.world) == state_hash(start)
    f.run(5)
    replay(base.log, "f")


def test_forks_with_the_same_seed_and_deciders_match(cfg, tmp_path):
    base = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 10)
    a = fork(base.log, "r", 10, "a", seed=4, deciders=rule_deciders(cfg, 9)); a.run(10)
    b = fork(base.log, "r", 10, "b", seed=4, deciders=rule_deciders(cfg, 9)); b.run(10)
    assert state_hash(a.world) == state_hash(b.world)


def test_replay_detects_a_changed_hash(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 10)
    sim.log._conn.execute(
        "UPDATE events SET json = json_set(json, '$.payload.hash', 'x')"
        " WHERE run_id = 'r' AND step = 4 AND kind = 'state_hash'"
    )
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 4 and "hash" in e.value.detail


def test_replay_detects_an_unused_recorded_decision(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 10)
    (_, decision), *_ = sim.log.events("r", kind="decision", step=3)
    extra = decision.model_copy(deep=True)
    extra.payload["decision_id"] = "3:nobody:pricing:9"
    sim.log.append("r", 3, [extra])
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 3 and "3:nobody:pricing:9" in e.value.detail


def test_replay_does_not_change_the_log(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 10)
    before = sim.log.events("r")
    replay(sim.log, "r")
    assert sim.log.events("r") == before and sim.log.latest_snapshot_step("r") == 9


def test_replay_skips_a_step_that_was_aborted_and_rerun(cfg, tmp_path):
    sim = make_sim(cfg, tmp_path / "a.sqlite", seed=7, deciders={"rule": FailsAtStep(5)})
    with pytest.raises(ModelUnavailable):
        sim.run(10)
    resumed = Simulation.resume(sim.log, "r", make_deciders(cfg, random.Random(0)))
    resumed.run(5)
    assert [e.kind for _, e in sim.log.events("r", kind="run_aborted")] == ["run_aborted"]
    replay(sim.log, "r")


def _tamper(log, sql: str) -> None:
    assert log._conn.execute(sql).rowcount >= 1, "the tampering changed nothing"


def test_replay_detects_a_deleted_hash(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    _tamper(sim.log, "DELETE FROM events WHERE run_id = 'r' AND step = 29 AND kind = 'state_hash'")
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 29 and "hash" in e.value.detail


def test_replay_detects_that_every_hash_was_deleted(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    _tamper(sim.log, "DELETE FROM events WHERE run_id = 'r' AND kind = 'state_hash'")
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 0 and "hash" in e.value.detail


def test_replay_detects_deleted_trailing_steps(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    _tamper(sim.log, "DELETE FROM events WHERE run_id = 'r' AND step >= 20")
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 20


def test_replay_detects_events_logged_past_the_last_snapshot(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    _tamper(sim.log, "DELETE FROM snapshots WHERE run_id = 'r' AND step >= 20")
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 20 and "outside" in e.value.detail


def test_replay_detects_an_edited_snapshot(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    _tamper(
        sim.log,
        "UPDATE snapshots SET world_json = json_set(world_json, '$.treasury.cash', 1)"
        " WHERE run_id = 'r' AND step = 15",
    )
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 15 and "snapshot" in e.value.detail


def test_replay_detects_an_edited_rng_state_in_a_snapshot(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    _tamper(
        sim.log,
        "UPDATE snapshots SET rng_json = json_set(rng_json, '$[1][0]', 12345)"
        " WHERE run_id = 'r' AND step = 15",
    )
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 15 and "rng" in e.value.detail


def test_replay_detects_an_edited_non_decision_event(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 30)
    _tamper(
        sim.log,
        "UPDATE events SET json = json_set(json, '$.harm.edited', 1)"
        " WHERE run_id = 'r' AND step = 10 AND kind NOT IN ('decision', 'state_hash')",
    )
    with pytest.raises(ReplayMismatch) as e:
        replay(sim.log, "r")
    assert e.value.step == 10 and "event" in e.value.detail


def test_replay_of_a_run_with_no_steps_is_a_mismatch(cfg, tmp_path):
    base = run_rule_sim(cfg, tmp_path / "a.sqlite", 7, 5)
    fork(base.log, "r", 5, "f", seed=1, deciders=rule_deciders(cfg))
    with pytest.raises(ReplayMismatch) as e:
        replay(base.log, "f")
    assert e.value.step == 5 and "no logged steps" in e.value.detail
