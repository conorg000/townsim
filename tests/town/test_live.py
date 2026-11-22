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
    "a": Family("a", "maker/alpha", {"max_tokens": 4000, "temperature": 0.8}),
    "b": Family("b", "maker/beta", {"max_tokens": 4000}),
    "c": Family("c", "maker/gamma", {"max_tokens": 4000}),
    "d": Family("d", "maker/delta", {"max_tokens": 4000}),
}
PRICES = {f.model: Price(input_per_mtok=0.10, output_per_mtok=0.50) for f in FAMILIES.values()}
TURN = '{"thoughts": "", "acts": []}'
TALK = '{"say": "Fine.", "acts": [], "end": true}'
COST = 0.001
CAP_IN_WEEK_1 = 0.03     # enough for a day or more of the hamlet, not a week


def response(text, refusal=None):
    message = SimpleNamespace(content=text, reasoning_content=None, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class Fake:
    """A completion function answering an empty turn, or the talk form in a talk; it counts its
    calls, refuses the `refuse_at`-th call (1-based) when that is set, and raises `raises`
    (an exception) on every call when that is set."""

    def __init__(self, refuse_at=None, raises=None, refusal="I can't help with that."):
        self.calls = []
        self.refuse_at = refuse_at
        self.raises = raises
        self.refusal = refusal
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            n = len(self.calls)
        if self.raises is not None:
            raise self.raises
        if n == self.refuse_at:
            return response(None, refusal=self.refusal)
        talking = "YOU ARE TALKING WITH" in kwargs["messages"][-1]["content"]
        return response(TALK if talking else TURN)


def play(db, fake, cap=5.0, weeks=2, seed=1, families=FAMILIES, echo=None, cost=COST, prices=None, **kw):
    lines = []
    got = live.play(db, "live", seed, weeks, families, prices or PRICES, cap, fake, cost_fn=lambda resp: cost,
                    echo=lines.append if echo is None else echo, sleep_fn=lambda s: None, **kw)
    return got, lines


NOTED = {n: {"model": f.model, "params": dict(f.params)} for n, f in FAMILIES.items()}


def test_play_two_weeks_and_replay(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake()
        got, lines = play(db, fake)
        assert got.days_done == 14 and got.stopped is None and got.engine_errors == 0
        assert len(lines) == 2 and lines[0].startswith("week 1  Monday 2 March  ") and "week 2  Monday 9 March" in lines[1]
        assert json.loads(db.note("live", "families")) == NOTED
        assert db.note("live", "seed") == "1" and float(db.note("live", "cap")) == 5.0
        assert replay(db, "live") == []
        assert len(fake.calls) == len(db.replies("live")) > 0
        assert got.spent == pytest.approx(COST * len(fake.calls)) == pytest.approx(db.spent("live"))
        assert {c["model"] for c in fake.calls} <= {"openrouter/" + f.model for f in FAMILIES.values()}


def test_play_stops_at_the_cap_and_resumes(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake()
        first, lines = play(db, fake, cap=CAP_IN_WEEK_1)
        assert first.stopped is not None and first.stopped.startswith("cap: ") and 1 <= first.days_done < 7
        assert lines[-1].startswith(f"stopped on day {first.days_done}: cap: ")
        assert db.spent("live") + live.lost_spend(db, "live") <= CAP_IN_WEEK_1
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
        db.put_note("live", "families", json.dumps(NOTED))
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
        moved = {**FAMILIES, "c": Family("c", "maker/alpha", {"max_tokens": 4000})}
        with pytest.raises(ValueError, match="family 'c' as maker/gamma; .* now has it as maker/alpha"):
            play(db, fake, families=moved)
        removed = {k: v for k, v in FAMILIES.items() if k != "d"}
        with pytest.raises(ValueError, match="family 'd' as maker/delta; .* now has it as absent"):
            play(db, fake, families=removed)
        renamed = {**removed, "e": Family("e", "maker/delta", {"max_tokens": 4000})}
        with pytest.raises(ValueError, match="family 'd'"):
            play(db, fake, families=renamed)
        added = {**FAMILIES, "e": Family("e", "maker/delta", {"max_tokens": 4000})}
        with pytest.raises(ValueError, match="family 'e' as absent"):
            play(db, fake, families=added)
        hotter = {**FAMILIES, "a": Family("a", "maker/alpha", {"max_tokens": 4000, "temperature": 1.2})}
        with pytest.raises(ValueError, match="family 'a' with temperature 0.8; .* now has 1.2"):
            play(db, fake, families=hotter)
        assert len(fake.calls) == n


def test_a_resume_past_a_stored_refusal_needs_leave(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake(refuse_at=3)
        got, _ = play(db, fake)
        assert got.stopped.startswith("refused: ")
        n = len(fake.calls)
        with pytest.raises(ValueError, match=r"refused on day \d+: p\w+ played by maker/\w+; .*--past-refusal"):
            play(db, fake)
        assert len(fake.calls) == n
        again, lines = play(db, fake, past_refusal=True)
        assert lines[0].startswith("warning: run 'live' was refused on day ") and again.days_done == 14
        assert len(fake.calls) == len(db.replies("live"))
        assert replay(db, "live") == []


def test_a_second_process_on_the_run_is_refused(tmp_path):
    import fcntl
    import os
    path = str(tmp_path / "r.sqlite")
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)        # another holder of the lock
    try:
        with RunDB(path) as db:
            fake = Fake()
            with pytest.raises(ValueError, match="^another process is running live in r.sqlite$"):
                play(db, fake)
            assert fake.calls == [] and db.reply_keys("live") == []
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    with RunDB(path) as db, RunDB(path) as other:
        got, _ = play(db, Fake(), weeks=1)                   # the lock is free once released
        assert got.days_done == 7
        got, _ = play(other, Fake(), weeks=1)
        assert got.days_done == 7


def test_an_overbilled_reply_stops_after_one_batch(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake()
        worst = 0.0025                                       # above any one attempt's worst case here
        got, _ = play(db, fake, cost=10 * worst)
        assert got.stopped.startswith("cap: a reply cost $0.0250, more than its worst case $")
        first_batch = {r for r in db.reply_keys("live") if "|d0|" in r and "|turn:" in r}
        assert len(fake.calls) == len(db.replies("live")) == len(first_batch) > 0


def test_a_key_in_an_error_is_never_shown_or_kept(tmp_path, monkeypatch):
    marker = "sk-or-v1-SECRETVALUE"
    monkeypatch.setenv("OPENROUTER_API_KEY", marker)
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = Fake(raises=RuntimeError(f"401 Unauthorized: Authorization: Bearer {marker}"))
        got, lines = play(db, fake)
        assert got.stopped.startswith("unavailable: ") and "SECRET" not in got.stopped
        assert all("SECRET" not in ln for ln in lines)
    with RunDB(str(tmp_path / "s.sqlite")) as db:
        got, lines = play(db, Fake(refuse_at=1, refusal=f"no, key {marker} is wrong"))
        [reply] = [r for _, r in db.replies("live") if r.error]
        assert reply.error.startswith("refused: ") and "SECRET" not in reply.error and "SECRET" not in got.stopped


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
    for typed in ("9", "nan", "inf", "-1", "0", "5.0000001", "lots"):
        got = runner.invoke(app, ["town-live", "--db", str(path), "--weeks", "1", "--cap", typed])
        assert got.exit_code == 1 and "Traceback" not in got.output
        assert got.output.strip() == f"the cap must be a number of dollars above 0 and at most 5, got {typed}"
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
    families.write_text("".join(f"{n}:\n  model: {f.model}\n  params: {{max_tokens: 4000}}\n" for n, f in FAMILIES.items()))
    prices = tmp_path / "prices.yaml"
    prices.write_text("".join(f"{m}:\n  input_per_mtok: 0.1\n  output_per_mtok: 0.5\n" for m in PRICES))
    got = CliRunner().invoke(app, ["town-live", "--db", str(tmp_path / "r.sqlite"), "--weeks", "1", "--cap", "1",
                                   "--families", str(families), "--prices", str(prices)])
    assert got.exit_code == 0, got.output
    assert "week 1  Monday 2 March" in got.output and "replay ok" in got.output
    assert "engine errors: 0" in got.output and "lost to failed attempts: $0.0000" in got.output
    assert all(f"  {n}  " in got.output for n in FAMILIES)
    assert "not-a-real-key" not in got.output and len(fake.calls) > 0


def test_cli_never_shows_a_key_from_an_error(tmp_path, monkeypatch):
    import townsim.cli as cli
    marker = "sk-or-v1-SECRETVALUE"
    monkeypatch.setenv("OPENROUTER_API_KEY", marker)
    fake = Fake(raises=RuntimeError(f"401 Unauthorized: Authorization: Bearer {marker}"))
    monkeypatch.setattr(cli, "_litellm", lambda: (fake, None))
    monkeypatch.setattr("townsim.model.RETRY_SLEEPS", (0, 0))     # no waiting between attempts
    families = tmp_path / "families.yaml"
    families.write_text("".join(f"{n}:\n  model: {f.model}\n  params: {{max_tokens: 4000}}\n" for n, f in FAMILIES.items()))
    prices = tmp_path / "prices.yaml"
    prices.write_text("".join(f"{m}:\n  input_per_mtok: 0.1\n  output_per_mtok: 0.5\n" for m in PRICES))
    got = CliRunner().invoke(app, ["town-live", "--db", str(tmp_path / "r.sqlite"), "--weeks", "1", "--cap", "1",
                                   "--families", str(families), "--prices", str(prices)])
    assert got.exit_code == 1 and "stopped on day 0: unavailable: " in got.output
    assert "SECRET" not in got.output and fake.calls


# --- the seat -------------------------------------------------------------------------------

SEAT = Family("seat", "maker/seat", {"max_tokens": 4000, "reasoning_effort": "low"})
SEAT_PRICES = {**PRICES, "maker/seat": Price(input_per_mtok=0.10, output_per_mtok=0.50)}


def tool_response(name=None, args="{}", text=None, reasoning="I look at what is connected."):
    calls = [SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name=name, arguments=args))] if name \
        else None
    message = SimpleNamespace(content=text, reasoning_content=reasoning, refusal=None, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if name else "stop")],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


class SeatFake(Fake):
    """`Fake` for people; with tools, the seat reads the directory once, then ends its turn."""

    def __call__(self, **kwargs):
        if "tools" not in kwargs:
            return super().__call__(**kwargs)
        with self.lock:
            self.calls.append(kwargs)
        if any(m.get("role") == "tool" for m in kwargs["messages"]):
            return tool_response(text="That is all.")
        return tool_response("read", '{"what": "directory"}')


def test_play_with_seat_notes_and_resume_refuses_other_seat(tmp_path):
    from townsim.town import arms
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = SeatFake()
        got, _ = play(db, fake, weeks=1, prices=SEAT_PRICES, seat=SEAT)
        assert got.days_done == 7 and json.loads(db.note("live", "seat")) == {"model": "maker/seat",
                                                                               "params": dict(SEAT.params)}
        other = Family("seat", "maker/seat", {"max_tokens": 2000})
        with pytest.raises(ValueError, match="seat"):
            play(db, fake, weeks=2, prices=SEAT_PRICES, seat=other)
        with pytest.raises(ValueError, match="seat"):
            play(db, fake, weeks=2, prices=SEAT_PRICES)
        s = arms.fork_arm(db, "live", 7, "drawn", "drawn")
        assert s.all("instances")
        with pytest.raises(ValueError, match="need a seat"):
            live.play(db, "drawn", 1, 2, FAMILIES, SEAT_PRICES, 5.0, fake, echo=lambda x: None)
        got = live.play(db, "drawn", 1, 2, FAMILIES, SEAT_PRICES, 5.0, fake, cost_fn=lambda r: COST,
                        echo=lambda x: None, sleep_fn=lambda s: None, seat=SEAT)
        assert got.days_done == 14 and got.stopped is None and got.engine_errors == 0
        seat_calls = [c for c in fake.calls if "tools" in c]
        assert seat_calls and all(c["parallel_tool_calls"] is False and c["model"] == "openrouter/maker/seat"
                                  for c in seat_calls)
        stored = [(k, r) for k, r in db.replies("drawn") if "|seat:" in k]
        assert stored[0][1].reasoning == "I look at what is connected." and stored[0][1].tool["calls"][0]["name"] == "read"
        assert replay(db, "drawn") == []
        assert ("seat", len(stored), pytest.approx(COST * len(stored))) in live.spend_by_family(db, "drawn")


def test_seat_probe_rows_with_fake_completion(tmp_path):
    from townsim.town import seatprobe
    store = seatprobe.morning(1)
    fake = SeatFake()
    rows = seatprobe.run(store, SEAT, SEAT_PRICES, fake, cap=0.5, n=1, cost_fn=lambda r: COST, sleep_fn=lambda s: None)
    assert [r.instance for r in rows] == ["as1", "as2", "as3", "as4"]
    for r in rows:
        assert (r.calls, r.tools, r.unreadable, r.ended, r.second_ok, r.refused) == (1, ["read"], 0, "no call", True, False)
        assert r.reasoning and r.reasoning_chars == 2 * len("I look at what is connected.")
        assert r.finishes == ["tool_calls", "stop"] and r.cost == pytest.approx(2 * COST)
    assert "as1" in seatprobe.table(rows)
    with pytest.raises(seatprobe.ProbeStopped) as e:
        seatprobe.run(store, SEAT, SEAT_PRICES, SeatFake(), cap=0.0005, n=1, sleep_fn=lambda s: None)
    assert str(e.value).startswith("cap: ")
