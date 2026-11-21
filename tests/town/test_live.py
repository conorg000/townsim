"""A live run of the hamlet against a fake completion function: nothing here calls a model."""

import json
import threading
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from townsim.cli import app
from townsim.estimate import Price
from townsim.town import live
from townsim.town.families import Family
from townsim.town.rundb import RunDB
from townsim.town.runs import replay

FAMILIES = {
    "a": Family("a", "maker/alpha", {"max_tokens": 100, "temperature": 0.8}),
    "b": Family("b", "maker/beta", {"max_tokens": 100}),
    "c": Family("c", "maker/gamma", {"max_tokens": 100}),
    "d": Family("d", "maker/delta", {"max_tokens": 100}),
}
PRICES = {f.model: Price(input_per_mtok=0.10, output_per_mtok=0.50) for f in FAMILIES.values()}
TURN = '{"thoughts": "", "acts": []}'
TALK = '{"say": "Fine.", "acts": [], "end": true}'
COST = 0.001


def response(text, refusal=None):
    message = SimpleNamespace(content=text, reasoning_content=None, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class Fake:
    """A completion function answering an empty turn, or the talk form in a talk; it counts its
    calls, and refuses the `refuse_at`-th call (1-based) when that is set."""

    def __init__(self, refuse_at=None):
        self.calls = []
        self.refuse_at = refuse_at
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            n = len(self.calls)
        if n == self.refuse_at:
            return response(None, refusal="I can't help with that.")
        talking = "YOU ARE TALKING WITH" in kwargs["messages"][-1]["content"]
        return response(TALK if talking else TURN)


def play(db, fake, cap=5.0, weeks=2, seed=1, families=FAMILIES, echo=None):
    lines = []
    got = live.play(db, "live", seed, weeks, families, PRICES, cap, fake, cost_fn=lambda resp: COST,
                    echo=lines.append if echo is None else echo)
    return got, lines


def test_play_two_weeks_and_replay(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake()
        got, lines = play(db, fake)
        assert got.days_done == 14 and got.stopped is None and got.engine_errors == 0
        assert len(lines) == 2 and lines[0].startswith("week 1  Monday 2 March  ") and "week 2  Monday 9 March" in lines[1]
        assert json.loads(db.note("live", "families")) == {n: f.model for n, f in FAMILIES.items()}
        assert db.note("live", "seed") == "1" and float(db.note("live", "cap")) == 5.0
        assert replay(db, "live") == []
        assert len(fake.calls) == len(db.replies("live")) > 0
        assert got.spent == pytest.approx(COST * len(fake.calls)) == pytest.approx(db.spent("live"))
        assert {c["model"] for c in fake.calls} <= {"openrouter/" + f.model for f in FAMILIES.values()}


def test_play_stops_at_the_cap_and_resumes(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake()
        first, lines = play(db, fake, cap=0.012)
        assert first.stopped is not None and first.stopped.startswith("cap: ") and first.days_done < 7
        assert lines[-1].startswith(f"stopped on day {first.days_done}: cap: ")
        assert replay(db, "live") is not None                   # what was done replays
        before = len(fake.calls)
        second, _ = play(db, fake, cap=5.0)
        assert second.days_done == 14 and second.stopped is None
        assert len(fake.calls) > before
        assert len(fake.calls) == len(db.replies("live"))      # no slot asked twice
        assert db.spent("live") == pytest.approx(COST * len(fake.calls)) == pytest.approx(second.spent)
        assert replay(db, "live") == []


def test_lost_spend_counts_toward_the_cap(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        db.create_run("live", seed=1)
        db.put_note("live", "families", json.dumps({n: f.model for n, f in FAMILIES.items()}))
        db.put_note("live", "seed", "1")
        db.put_note("live", "lost_spend", "4.999")
        from townsim.town import gen
        db.save_snapshot("live", 0, gen.generate(1, list(FAMILIES), weeks=21))
        fake = Fake()
        got, _ = play(db, fake, cap=5.0)
        assert got.stopped.startswith("cap: $4.9990 spent") and fake.calls == []


def test_resume_with_other_seed_or_families_is_refused(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake()
        play(db, fake, weeks=1)
        n = len(fake.calls)
        with pytest.raises(ValueError, match="seed"):
            play(db, fake, seed=2)
        moved = {**FAMILIES, "c": Family("c", "maker/alpha", {"max_tokens": 100})}
        with pytest.raises(ValueError, match="family 'c'"):
            play(db, fake, families=moved)
        assert len(fake.calls) == n


def test_refusal_stops_the_run_and_is_stored(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake(refuse_at=3)
        got, lines = play(db, fake)
        assert got.stopped.startswith("refused: ") and got.days_done < 14
        assert any(r.error and r.error.startswith("refused: ") for _, r in db.replies("live"))
        assert len(fake.calls) == len(db.replies("live"))
        assert lines[-1].startswith("stopped on day")
        replay(db, "live")


def test_cli_requires_cap_and_key(tmp_path, monkeypatch):
    runner = CliRunner()
    path = tmp_path / "r.sqlite"
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    got = runner.invoke(app, ["town-live", "--db", str(path), "--weeks", "1"])
    assert got.exit_code == 2                                  # usage error: no --cap
    got = runner.invoke(app, ["town-live", "--db", str(path), "--weeks", "1", "--cap", "9"])
    assert got.exit_code == 1 and "cap" in got.output and "Traceback" not in got.output
    assert "not-a-real-key" not in got.output
    monkeypatch.delenv("OPENROUTER_API_KEY")
    got = runner.invoke(app, ["town-live", "--db", str(path), "--weeks", "1", "--cap", "1"])
    assert got.exit_code == 1
    assert got.output.strip() == ("no OPENROUTER_API_KEY in the environment; run with: "
                                  "uv run --env-file .env townsim town-live …")
    assert not path.exists()


def test_cli_runs_on_a_fake(tmp_path, monkeypatch):
    """The command's printing and replay, with LiteLLM (never imported here) swapped for the fake."""
    import townsim.cli as cli
    fake = Fake()
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(cli, "_litellm", lambda: (fake, lambda resp: COST))
    families = tmp_path / "families.yaml"
    families.write_text("".join(f"{n}:\n  model: {f.model}\n  params: {{max_tokens: 100}}\n" for n, f in FAMILIES.items()))
    prices = tmp_path / "prices.yaml"
    prices.write_text("".join(f"{m}:\n  input_per_mtok: 0.1\n  output_per_mtok: 0.5\n" for m in PRICES))
    got = CliRunner().invoke(app, ["town-live", "--db", str(tmp_path / "r.sqlite"), "--weeks", "1", "--cap", "1",
                                   "--families", str(families), "--prices", str(prices)])
    assert got.exit_code == 0, got.output
    assert "week 1  Monday 2 March" in got.output and "replay ok" in got.output
    assert "engine errors: 0" in got.output and "lost to failed attempts: $0.0000" in got.output
    assert all(f"  {n}  " in got.output for n in FAMILIES)
    assert "not-a-real-key" not in got.output and len(fake.calls) > 0
