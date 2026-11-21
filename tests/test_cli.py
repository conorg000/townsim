import json
import sqlite3

from typer.testing import CliRunner

from townsim.cli import app

runner = CliRunner()


def _run(*args: str):
    return runner.invoke(app, list(args))


def test_town_thin_prints_a_line_a_week_then_replays(tmp_path):
    db = str(tmp_path / "thin.sqlite")
    result = _run("town-thin", "--weeks", "3", "--db", db)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 4 and lines[-1] == "replay ok"
    for n, line in enumerate(lines[:3], start=1):
        week, day, digest, total = line.split("  ")
        assert week == f"week {n}" and day == f"day {7 * n}"
        assert len(digest) == 12 and int(digest, 16) >= 0
        assert total == "ledger total 0"


def test_town_thin_is_the_same_every_time(tmp_path):
    first = _run("town-thin", "--weeks", "2", "--db", str(tmp_path / "a.sqlite"))
    second = _run("town-thin", "--weeks", "2", "--db", str(tmp_path / "b.sqlite"), "--run-id", "other")
    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output


def test_town_thin_defaults_to_eight_weeks(tmp_path):
    result = _run("town-thin", "--db", str(tmp_path / "thin.sqlite"))
    assert result.exit_code == 0
    assert result.output.splitlines()[-2].startswith("week 8  day 56  ")


def test_town_thin_refuses_a_run_id_already_in_the_file(tmp_path):
    db = str(tmp_path / "thin.sqlite")
    assert _run("town-thin", "--weeks", "1", "--db", db).exit_code == 0
    again = _run("town-thin", "--weeks", "1", "--db", db)
    assert again.exit_code == 1
    assert "already has a run called 'thin'" in again.output and "Traceback" not in again.output
    assert _run("town-thin", "--weeks", "1", "--db", db, "--run-id", "t2").exit_code == 0


def test_town_thin_needs_a_db():
    assert _run("town-thin", "--weeks", "1").exit_code != 0


def test_town_replay_checks_a_kept_run(tmp_path):
    db = str(tmp_path / "thin.sqlite")
    _run("town-thin", "--weeks", "2", "--db", db, "--run-id", "t1")
    result = _run("town-replay", "--db", db, "t1")
    assert result.exit_code == 0 and result.output == "replay ok\n"


def test_town_replay_reports_a_mismatch(tmp_path):
    db = str(tmp_path / "thin.sqlite")
    _run("town-thin", "--weeks", "2", "--db", db, "--run-id", "t1")
    with sqlite3.connect(db) as con:          # Craig's day-4 rent payment becomes nothing at all
        key, stored = con.execute("select key, reply_json from replies where key like 't1|%|d4|p03|%'").fetchone()
        reply = json.loads(stored) | {"text": '{"thoughts": "", "acts": []}'}
        con.execute("update replies set reply_json = ? where key = ?", (json.dumps(reply), key))
    result = _run("town-replay", "--db", db, "t1")
    assert result.exit_code == 1
    assert "replay mismatch (state differs) at t1|d5" in result.output and "replay ok" not in result.output


def test_town_replay_of_an_unknown_run_or_file_is_a_plain_error(tmp_path):
    db = str(tmp_path / "thin.sqlite")
    _run("town-thin", "--weeks", "1", "--db", db)
    unknown = _run("town-replay", "--db", db, "nope")
    assert unknown.exit_code == 1 and "has no run called 'nope'" in unknown.output
    missing = _run("town-replay", "--db", str(tmp_path / "missing.sqlite"), "thin")
    assert missing.exit_code == 1 and "no database file" in missing.output
    assert not (tmp_path / "missing.sqlite").exists()
