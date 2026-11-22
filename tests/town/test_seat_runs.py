"""The day's order with instances, their records, and exact replay of every arm."""

import functools
import tempfile

import pytest

from town.helpers import generated
from townsim.town import arms, hamlet_script, loop, product, seat, seat_script, talk
from townsim.town.entities import Appointment
from townsim.town.replies import Keys, ScriptedReplier, StopRun
from townsim.town.rundb import RunDB
from townsim.town.runs import replay, resume, run

ARM_DAYS = 21


def scripted():
    return ScriptedReplier(hamlet_script.script, seat_script.tool_fn)


@functools.cache
def _parent_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/parent.sqlite"
        with RunDB(path) as db:
            db.create_run("pre", seed=1)
            for name, value in (("families", "{}"), ("seed", "1"), ("deck_weeks", "8")):
                db.put_note("pre", name, value)
            run(db, "pre", generated(1), ScriptedReplier(hamlet_script.script), 7)
        with open(path, "rb") as f:
            return f.read()


def _parent(path: str) -> str:
    """A run file at `path` holding the pre-launch week of seed 1 on the hamlet's script ("pre")."""
    with open(path, "wb") as f:
        f.write(_parent_bytes())
    return path


@pytest.fixture(scope="module")
def arms_db(tmp_path_factory):
    """The pre-launch week of seed 1 on the hamlet's script, forked on Monday 9 March into the three
    arms, each run a week with the scripted instance."""
    tmp = tmp_path_factory.mktemp("arms")
    path = _parent(f"{tmp}/arms.sqlite")
    with RunDB(path) as db:
        for arm in arms.ARMS:
            s = arms.fork_arm(db, "pre", 7, f"a-{arm}", arm)
            run(db, f"a-{arm}", s, scripted(), ARM_DAYS)
    return path


def test_three_scripted_arms_replay_exactly(arms_db):
    with RunDB(arms_db) as db:
        for arm in arms.ARMS:
            assert replay(db, f"a-{arm}") == [], arm
        drawn = [t for d in db.seat_days("a-drawn") for t in db.seat_turns("a-drawn", d)]
        assert {t.instance for t in drawn} == {"as1", "as2", "as3", "as4"}
        assert all(t.calls and t.calls[0].tool == "bank_get_account" for t in drawn)
        kinds = {c.kind for t in drawn for c in t.calls}
        assert {"read", "own", "act"} <= kinds
        assert any(c.tool == "bank_make_payment" for t in drawn for c in t.calls)
        assert not any(c.tool == "bank_make_payment" for t in drawn if t.instance == "as4" for c in t.calls)   # read only
        assert db.seat_days("a-none") == [] and db.errors("a-drawn") == []
        stored = [k for k, _ in db.replies("a-drawn") if "|seat:" in k]
        assert len(stored) == sum(len(t.calls) + (t.ended != "limit") for t in drawn)


def test_seat_acts_after_talks(monkeypatch):
    order = []
    real_hold, real_seat = talk.hold, seat.run_seat
    monkeypatch.setattr(talk, "hold", lambda *a, **k: (order.append("talk"), real_hold(*a, **k))[1])
    monkeypatch.setattr(seat, "run_seat", lambda *a, **k: (order.append("seat"), real_seat(*a, **k))[1])
    s = generated(1)
    s.day = s.ledger.t = 7
    product.launch(s)
    arms.draw_clients(s)
    s.add("appointments", Appointment("ap1", s.day, "p9", "p10", "the rent", s.day), "routine:setup")
    report = loop.run_day(s, scripted(), Keys("t"))
    assert order[-1] == "seat" and "talk" in order and order.index("seat") > max(i for i, x in enumerate(order) if x == "talk")
    assert [t.instance for t in report.seat] == ["as1", "as2", "as3", "as4"]


class StopsAt:
    """The scripted instance, raising StopRun at the `at`-th call with tools (1-based) once."""

    def __init__(self, at):
        self.at, self.n = at, 0
        self.inner = scripted()

    def model_for(self, actor):
        return "scripted"

    def ask(self, key, messages):
        return self.inner.ask(key, messages)

    def ask_tools(self, key, messages, tools):
        self.n += 1
        if self.n == self.at:
            raise StopRun("stop here")
        return self.inner.ask_tools(key, messages, tools)


def test_stoprun_in_seat_returns_to_morning(tmp_path):
    with RunDB(_parent(str(tmp_path / "s.sqlite"))) as db:
        s = arms.fork_arm(db, "pre", 7, "a", "drawn")
        morning = s.hash()
        got = run(db, "a", s, StopsAt(8), 2)              # stopped at the third call of as2's turn on day 7
        assert got.day == 7 and got.hash() == morning and db.seat_days("a") == []
        kept = [k for k, _ in db.replies("a") if "|seat:" in k]
        assert len(kept) == 7                             # the calls made before the stop are kept
        final = run(db, "a", resume(db, "a"), scripted(), 2)
        assert final.day == 9 and replay(db, "a") == []
    with RunDB(_parent(str(tmp_path / "t.sqlite"))) as db:
        s = arms.fork_arm(db, "pre", 7, "a", "drawn")
        assert run(db, "a", s, scripted(), 2).hash() == final.hash()     # as if it had never stopped


class NoSeat:
    def model_for(self, actor):
        return "scripted"

    def ask(self, key, messages):
        return scripted().ask(key, messages)

    def ask_many(self, pairs):
        return [self.ask(k, m) for k, m in pairs]

    def ask_tools(self, key, messages, tools):
        raise AssertionError("the seat was asked")


def test_none_arm_never_asks_the_seat(tmp_path):
    with RunDB(_parent(str(tmp_path / "n.sqlite"))) as db:
        s = arms.fork_arm(db, "pre", 7, "n", "none")
        assert run(db, "n", s, NoSeat(), 3).day == 10
        c = arms.fork_arm(db, "pre", 7, "c", "choice")
        run(db, "c", c, NoSeat(), 1)                      # launched, and no one subscribed yet: no turn
