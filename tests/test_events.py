import os
import subprocess
import sys

from townsim.events import Event, EventLog, state_hash


def test_events_round_trip_in_order(tmp_path):
    log = EventLog(str(tmp_path / "r.sqlite"))
    log.create_run("a", seed=1, config_json="{}")
    log.append("a", 0, [Event(kind="x"), Event(kind="y", legal=False, harm={"treasury": 5})])
    got = log.events("a")
    assert [e.kind for _, e in got] == ["x", "y"]
    assert got[1][1].harm == {"treasury": 5}
    assert [e.kind for _, e in log.events("a", kind="y")] == ["y"]


def test_rollback_discards_uncommitted_events(tmp_path):
    log = EventLog(str(tmp_path / "r.sqlite")); log.create_run("a", 1, "{}")
    log.begin(); log.append("a", 0, [Event(kind="x")]); log.rollback()
    assert log.events("a") == []


def test_state_hash_is_stable_and_sensitive(tiny_world):
    h = state_hash(tiny_world)
    assert h == state_hash(tiny_world.model_copy(deep=True))
    tiny_world.people["p1"].cash += 1
    assert state_hash(tiny_world) != h


def test_commit_makes_events_durable(tmp_path):
    path = str(tmp_path / "r.sqlite")
    log = EventLog(path); log.create_run("a", 1, "{}")
    log.begin(); log.append("a", 0, [Event(kind="x")]); log.commit()
    assert [e.kind for _, e in EventLog(path).events("a")] == ["x"]


def test_append_outside_transaction_commits_immediately(tmp_path):
    path = str(tmp_path / "r.sqlite")
    log = EventLog(path); log.create_run("a", 1, "{}")
    log.append("a", 0, [Event(kind="x")])
    assert [e.kind for _, e in EventLog(path).events("a")] == ["x"]


def test_uncommitted_events_are_not_visible_to_other_connections(tmp_path):
    path = str(tmp_path / "r.sqlite")
    log = EventLog(path); log.create_run("a", 1, "{}")
    log.begin(); log.append("a", 0, [Event(kind="x")])
    assert EventLog(path).events("a") == []
    log.commit()


def test_events_filter_by_step_and_keep_order_across_appends(tmp_path):
    log = EventLog(str(tmp_path / "r.sqlite")); log.create_run("a", 1, "{}")
    log.append("a", 0, [Event(kind="x")])
    log.append("a", 1, [Event(kind="y", actor_id="p1", payload={"n": 1})])
    log.append("a", 1, [Event(kind="z", visible=False)])
    assert [(s, e.kind) for s, e in log.events("a")] == [(0, "x"), (1, "y"), (1, "z")]
    assert [e.kind for _, e in log.events("a", step=1)] == ["y", "z"]
    assert [e.kind for _, e in log.events("a", kind="z", step=1)] == ["z"]
    got = log.events("a", step=1)
    assert got[0][1] == Event(kind="y", actor_id="p1", payload={"n": 1})
    assert got[1][1].visible is False


def test_get_run_returns_run_row(tmp_path):
    log = EventLog(str(tmp_path / "r.sqlite"))
    log.create_run("a", 1, "{}")
    log.create_run("b", 2, '{"k": 1}', parent_run_id="a", parent_step=7)
    assert log.get_run("a") == {
        "run_id": "a", "seed": 1, "config_json": "{}",
        "parent_run_id": None, "parent_step": None,
    }
    assert log.get_run("b") == {
        "run_id": "b", "seed": 2, "config_json": '{"k": 1}',
        "parent_run_id": "a", "parent_step": 7,
    }


def test_state_hash_ignores_set_insertion_order(tiny_world):
    other = tiny_world.model_copy(deep=True)
    other.laws.illegal = set(sorted(tiny_world.laws.illegal, reverse=True))
    assert state_hash(other) == state_hash(tiny_world)


_HASH_SCRIPT = """
import sys
from townsim.events import state_hash
from townsim.world import World
print(state_hash(World.model_validate_json(sys.stdin.read())))
"""


def test_state_hash_is_identical_across_processes(tiny_world):
    # Each child rebuilds the world's sets under a different PYTHONHASHSEED,
    # so their iteration order differs between processes.
    world_json = tiny_world.model_dump_json()
    hashes = set()
    for seed in ("1", "2", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", _HASH_SCRIPT],
            input=world_json, env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, check=True,
        )
        hashes.add(out.stdout.strip())
    assert hashes == {state_hash(tiny_world)}
