import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from townsim.cli import app
from townsim.decide import ModelUnavailable, RuleDecider
from townsim.events import EventLog
from townsim.runs import replay

TOWN_YAML = Path(__file__).resolve().parent.parent / "config" / "town.yaml"

runner = CliRunner()


def _run(*args: str):
    return runner.invoke(app, list(args))


@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "runs.sqlite")


def _base(db: str, steps: int = 6, run_id: str = "base"):
    return _run("run", str(TOWN_YAML), "--steps", str(steps), "--seed", "1",
                "--run-id", run_id, "--db", db)


def test_run_logs_the_requested_steps_and_replays(db):
    result = _base(db)
    assert result.exit_code == 0, result.output
    log = EventLog(db)
    assert log.latest_snapshot_step("base") == 5
    assert log.get_run("base")["seed"] == 1
    replay(log, "base")


def test_run_on_an_existing_run_id_is_an_error_not_an_overwrite(db):
    assert _base(db).exit_code == 0
    before = len(EventLog(db).events("base"))
    result = _base(db)
    assert result.exit_code != 0
    assert "already exists" in result.output and "--resume" in result.output
    assert len(EventLog(db).events("base")) == before


def test_resume_continues_a_run_to_its_requested_length(db):
    assert _base(db, steps=4).exit_code == 0
    result = _run("run", str(TOWN_YAML), "--steps", "7", "--seed", "1",
                  "--run-id", "base", "--db", db, "--resume")
    assert result.exit_code == 0, result.output
    log = EventLog(db)
    assert log.latest_snapshot_step("base") == 6
    replay(log, "base")


def test_resume_of_an_unknown_run_is_an_error(db):
    result = _run("run", str(TOWN_YAML), "--steps", "3", "--seed", "1",
                  "--run-id", "nope", "--db", db, "--resume")
    assert result.exit_code != 0 and "nope" in result.output


def test_a_model_outage_exits_non_zero_and_says_how_to_resume(db, monkeypatch):
    def flaky(self, decision):
        if decision.id.startswith("3:"):
            raise ModelUnavailable("no answer")
        return original(self, decision)

    original = RuleDecider.decide
    monkeypatch.setattr(RuleDecider, "decide", flaky)
    result = _base(db)
    assert result.exit_code != 0
    assert "step 3" in result.output and "--resume" in result.output
    assert EventLog(db).latest_snapshot_step("base") == 2

    monkeypatch.setattr(RuleDecider, "decide", original)
    result = _run("run", str(TOWN_YAML), "--steps", "6", "--seed", "1",
                  "--run-id", "base", "--db", db, "--resume")
    assert result.exit_code == 0, result.output
    assert EventLog(db).latest_snapshot_step("base") == 5


def test_fork_starts_a_new_run_from_the_source_and_replays(db):
    assert _base(db).exit_code == 0
    result = _run("fork", "--db", db, "--from", "base", "--at", "6", "--run-id", "f1",
                  "--seed", "2", "--steps", "3")
    assert result.exit_code == 0, result.output
    log = EventLog(db)
    run = log.get_run("f1")
    assert run["parent_run_id"] == "base" and run["parent_step"] == 6 and run["seed"] == 2
    assert log.latest_snapshot_step("f1") == 8
    assert _run("replay", "--db", db, "f1").exit_code == 0


def test_fork_onto_an_existing_run_id_is_an_error(db):
    assert _base(db).exit_code == 0
    result = _run("fork", "--db", db, "--from", "base", "--at", "3", "--run-id", "base",
                  "--seed", "2", "--steps", "1")
    assert result.exit_code != 0 and "already exists" in result.output


def test_replay_exits_zero_on_success_and_non_zero_with_the_step_on_mismatch(db):
    assert _base(db).exit_code == 0
    assert _run("replay", "--db", db, "base").exit_code == 0

    log = EventLog(db)
    log._conn.execute(
        "DELETE FROM events WHERE rowid = (SELECT rowid FROM events"
        " WHERE run_id = 'base' AND step = 4 AND kind = 'decision' ORDER BY seq LIMIT 1)"
    )
    result = _run("replay", "--db", db, "base")
    assert result.exit_code != 0
    assert "step 4" in result.output


def test_replay_of_an_unknown_run_is_an_error(db):
    result = _run("replay", "--db", db, "nope")
    assert result.exit_code != 0 and "nope" in result.output


def test_report_prints_one_json_object_with_measures_and_stability(db):
    assert _base(db).exit_code == 0
    result = _run("report", "--db", db, "base")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["run_id"] == "base"
    assert report["measures"]["steps"] == 6
    assert report["stability"] == {"stable": True, "failures": []}
