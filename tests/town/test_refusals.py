"""`town-refusals`: every distinct refusal and error text of kept runs, grouped by shape. Calls no model."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from townsim.cli import app
from townsim.town import gen, lane_b, refusals
from townsim.town.acts import Act, Outcome
from townsim.town.loop import Turn
from townsim.town.rundb import RunDB
from townsim.town.seat import SeatCall, SeatTurn


def _kept(path: str):
    s = gen.generate(3, ["a", "b", "c", "d"])
    lane_b.install(s)
    a, b = [p for p in s.all("people") if p.age >= 18][:2]
    h = s.get("households", b.household).name
    texts = [f"Not done: your letter to '{b.name}' came back undelivered.",
             f"Not done: your letter to '{a.name}' came back undelivered.",
             f"Not done: {h} is not a person.",
             "Not done: the account holds $276.80 and this needs $335.00.",
             "Not done: the account holds $1,276.80 and this needs $2,335.00."]
    with RunDB(path) as db:
        db.create_run("r", seed=3)
        db.save_snapshot("r", 0, s)
        db.put_turns("r", 0, [Turn(a.id, "k", False, [Outcome(False, t, Act(a.id, a.id, "message", {}))
                                                         for t in texts] + [Outcome(True, "Sent.", Act(a.id, a.id, "x", {}))])])
        msg = json.dumps({"status": "declined", "error": "unknown_recipient",
                          "message": f"Your letter to '{b.name}' came back undelivered."})
        db.put_seat_turns("r", 0, [SeatTurn("as1", h, 0, "", "", [SeatCall(1, "k", "send_email", "{}", msg, False,
                                                                           "refused")], None, "no call")])
    return s, texts


def test_like_texts_group_by_shape_with_names_ids_sums_and_dates_as_placeholders(tmp_path):
    path = str(tmp_path / "r.sqlite")
    s, texts = _kept(path)
    with RunDB(path) as db:
        found = refusals.gather([(db, "r")], set())
    by = {x.shape: x for x in found}
    letter = by["Not done: your letter to '<text>' came back undelivered."]
    assert letter.count == 2 and letter.readers == {"person": 2}
    assert by["Your letter to '<text>' came back undelivered."].readers == {"assistant": 1}
    assert by["Not done: the account holds $<sum> and this needs $<sum>."].count == 2
    assert by["Not done: <name> is not a person."].count == 1
    assert found[0].count == 2                                           # most frequent first
    assert refusals.shape("Not done: it returns on Monday 14 December (ob52).", []) == \
        "Not done: it returns on <date> (<id>)."


def test_the_command_prints_counts_and_examples_and_marks_what_the_check_met(tmp_path, monkeypatch):
    path = str(tmp_path / "r.sqlite")
    s, texts = _kept(path)
    folder = tmp_path / "runs"
    folder.mkdir()
    (folder / "every-path-texts-3.txt").write_text(f"## To a person (1)\n- [a step] {texts[2]}\n", encoding="utf-8")
    assert refusals.every_path_shapes(folder) == {"Not done: <name> is not a person."}
    import townsim.cli as cli
    monkeypatch.setattr(cli, "_ROOT", tmp_path)
    out = CliRunner().invoke(app, ["town-refusals", "--db", path, "--run", "r"])
    assert out.exit_code == 0, out.output
    lines = out.output.splitlines()
    first = next(i for i, x in enumerate(lines) if "came back undelivered" in x)
    assert lines[first].split()[0] == "2" and "(person 2)" in lines[first]
    assert lines[first + 1].strip().startswith("e.g. r day 0 ")
    assert any("[checked]" in x and "is not a person" in x for x in lines)
    bad = CliRunner().invoke(app, ["town-refusals", "--db", str(tmp_path / "none.sqlite")])
    assert bad.exit_code == 1
