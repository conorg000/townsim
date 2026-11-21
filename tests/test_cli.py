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
    assert len(lines) == 5 and lines[-2:] == ["engine errors: 0", "replay ok"]
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
    assert result.output.splitlines()[-3].startswith("week 8  day 56  ")


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


def test_town_thin_fails_when_the_engine_reports_errors(tmp_path, monkeypatch):
    from townsim.town import acts, thin

    def breaks(store, act, cause):
        raise RuntimeError("a bug")

    monkeypatch.setitem(acts.REGISTRY, "break", acts.OpSpec("break", "{}", "", 0.0, lambda s, a: None, breaks, 90))
    monkeypatch.setattr(thin, "thin_script", lambda key, messages: '{"acts": [{"op": "break"}]}')
    result = _run("town-thin", "--weeks", "1", "--db", str(tmp_path / "e.sqlite"))
    assert result.exit_code == 1
    assert "engine errors: " in result.output and "engine errors: 0" not in result.output
    assert "Traceback" not in result.output


def _one_plain_line(result):
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output and isinstance(result.exception, (SystemExit, type(None)))
    assert len(result.output.strip().splitlines()) == 1, result.output
    return result.output.strip()


def test_town_commands_report_a_bad_file_or_run_in_one_plain_line(tmp_path):
    junk = tmp_path / "junk.sqlite"
    junk.write_text("this is not a database " * 100)
    assert "junk.sqlite" in _one_plain_line(_run("town-thin", "--weeks", "1", "--db", str(junk)))
    assert "junk.sqlite" in _one_plain_line(_run("town-replay", "--db", str(junk), "thin"))
    nowhere = str(tmp_path / "no" / "such" / "dir" / "t.sqlite")
    assert "t.sqlite" in _one_plain_line(_run("town-thin", "--weeks", "1", "--db", nowhere))
    db = str(tmp_path / "t.sqlite")
    for bad in ("a|b", ""):
        assert "run id" in _one_plain_line(_run("town-thin", "--weeks", "1", "--db", db, "--run-id", bad))


def test_town_replay_of_a_run_kept_under_another_schema_is_a_plain_error(tmp_path):
    db = str(tmp_path / "thin.sqlite")
    _run("town-thin", "--weeks", "1", "--db", db, "--run-id", "t1")
    with sqlite3.connect(db) as con:
        con.execute("update runs set schema = 0")
    assert "schema 0" in _one_plain_line(_run("town-replay", "--db", db, "t1"))


def test_town_hamlet_runs_eight_weeks_then_replays(tmp_path):
    db = str(tmp_path / "h.sqlite")
    result = _run("town-hamlet", "--seed", "1", "--weeks", "8", "--db", db, "--run-id", "hamlet")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 10 and lines[-2:] == ["engine errors: 0", "replay ok"]
    for n, line in enumerate(lines[:8], start=1):
        week, day, digest, total = line.split("  ")
        assert (week, day, total) == (f"week {n}", f"day {7 * n}", "ledger total 0") and len(digest) == 12
    again = _run("town-hamlet", "--seed", "1", "--weeks", "1", "--db", db, "--run-id", "hamlet")
    assert again.exit_code == 1 and "already has a run called 'hamlet'" in again.output


def test_town_hamlet_reads_the_families_left_in(tmp_path):
    from townsim.town import families

    path = tmp_path / "f.yaml"
    path.write_text("one:\n  left_out: false\ntwo:\n  left_out: true\nthree:\n  left_out: false\n"
                    "four: {model: x, left_out: false}\n")
    assert families.names(str(path)) == ["one", "three", "four"]
    assert families.names("config/families.yaml")[:2] == ["haiku", "deepseek"]
    a = _run("town-hamlet", "--weeks", "1", "--db", str(tmp_path / "a.sqlite"), "--families", str(path))
    assert a.exit_code == 0, a.output
    b = _run("town-hamlet", "--weeks", "1", "--db", str(tmp_path / "c.sqlite"))
    assert a.output.splitlines()[0] != b.output.splitlines()[0]              # other players were dealt
    bad = _run("town-hamlet", "--weeks", "1", "--db", str(tmp_path / "b.sqlite"), "--families", str(tmp_path / "no"))
    assert bad.exit_code == 1 and "cannot read the families" in bad.output
