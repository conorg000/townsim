import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from townsim.town.acts import OpSpec, register
from townsim.town.replies import Reply, ReplayMismatch, ScriptedReplier, StopRun
from townsim.town.rundb import RunDB
from townsim.town.runs import fork, replay, resume, run
from townsim.town.thin import thin_script, thin_town

EMPTY = '{"thoughts": "", "acts": []}'


def stop_on(day, actor):
    """The thin script, except that it raises StopRun the first time `actor` is asked on `day`."""
    stopped = []

    def script(key, messages):
        if (key.day, key.actor) == (day, actor) and not stopped:
            stopped.append(key)
            raise StopRun("stop here")
        return thin_script(key, messages)
    return script


def stop_sooner(key, messages):
    """The thin script, except that Ruth stops her standing payment on Friday 20 March, a week early."""
    if (key.day, key.actor) == (18, "p01"):
        return '{"thoughts": "", "acts": [{"op": "stop", "id": "ob9"}]}'
    return thin_script(key, messages)


@pytest.fixture(scope="module")
def eight_week_db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("runs") / "r1.sqlite")
    with RunDB(path) as db:
        db.create_run("r1", seed=1)
        final = run(db, "r1", thin_town(), ScriptedReplier(thin_script), 56)
    return path, final.hash()


def copy_of(src, tmp_path, name):
    path = str(tmp_path / name)
    shutil.copy(src, path)
    return path


def test_a_run_replays_exactly(eight_week_db):
    path, final = eight_week_db
    with RunDB(path) as db:
        replay(db, "r1")                                        # no error
        assert db.snapshot_hash("r1", 56) == final and db.snapshot_days("r1") == list(range(57))
        assert db.change_days("r1") == list(range(56))
        assert any(c.cause == "op:p03:pay:d4.2" for c in db.changes("r1", 4))
        assert all(c.day == 4 for c in db.changes("r1", 4))


def test_replay_writes_nothing(eight_week_db, tmp_path):
    path = copy_of(eight_week_db[0], tmp_path, "c.sqlite")
    with open(path, "rb") as f:
        before = f.read()
    with RunDB(path) as db:
        replay(db, "r1")
    with open(path, "rb") as f:
        assert f.read() == before


def test_replay_notices_a_changed_view_a_changed_state_and_an_unused_reply(eight_week_db, tmp_path):
    src = eight_week_db[0]

    prompt = copy_of(src, tmp_path, "prompt.sqlite")
    with sqlite3.connect(prompt) as c:
        c.execute("update replies set prompt_hash = 'x' where key = 'r1|scripted|d11|p03|turn:|0'")
    with RunDB(prompt) as db, pytest.raises(ReplayMismatch) as e:
        replay(db, "r1")
    assert e.value.why == "prompt changed" and "d11" in str(e.value) and "p03" in str(e.value)

    state = copy_of(src, tmp_path, "state.sqlite")
    with sqlite3.connect(state) as c:
        c.execute("update snapshots set hash = 'x' where run = 'r1' and day = 20")
    with RunDB(state) as db, pytest.raises(ReplayMismatch) as e:
        replay(db, "r1")
    assert e.value.why == "state differs" and str(e.value) == "replay mismatch (state differs) at r1|d20"

    unused = copy_of(src, tmp_path, "unused.sqlite")
    with RunDB(unused) as db:
        db.put_reply("r1", "r1|scripted|d30|p02|turn:|5", "r1|d30|p02|turn:|5", "x", Reply(text=EMPTY))
        with pytest.raises(ReplayMismatch) as e:
            replay(db, "r1")
    assert e.value.why == "unused" and "d30" in str(e.value)


class TwoModels:
    """The thin script, with Ruth played by one model and everyone else by another."""

    def __init__(self, fn=thin_script):
        self.fn = fn

    def model_for(self, actor):
        return "scripted-a" if actor == "p01" else "scripted-b"

    def ask(self, key, messages):
        return Reply(text=self.fn(key, messages))


def test_people_played_by_different_models_record_resume_and_replay(tmp_path):
    with RunDB(str(tmp_path / "two.sqlite")) as db, RunDB(str(tmp_path / "one.sqlite")) as one:
        db.create_run("t", seed=1); one.create_run("t", seed=1)
        assert run(db, "t", thin_town(), TwoModels(stop_on(day=11, actor="p03")), 21).day == 11
        end = run(db, "t", resume(db, "t"), TwoModels(), 10)
        assert end.day == 21 and replay(db, "t") == []
        models = {k.split("|")[3]: k.split("|")[1] for k in db.reply_keys("t")}
        assert models["p01"] == "scripted-a" and {models[p] for p in ("p02", "p03", "p04")} == {"scripted-b"}
        assert end.hash() == run(one, "t", thin_town(), ScriptedReplier(thin_script), 21).hash()
        with pytest.raises(ReplayMismatch) as e:                    # going on with the models changed is refused
            run(db, "t", db.load_snapshot("t", 11), ScriptedReplier(thin_script), 1)
        assert e.value.why == "model changed"


def test_each_day_keeps_its_turns(eight_week_db):
    with RunDB(eight_week_db[0]) as db:
        assert db.turn_days("r1") == list(range(56))
        [tom] = [t for t in db.turns("r1", 3) if t.pid == "p02"]
        assert tom.key == "r1|scripted|d3|p02|turn:|0" and not tom.invalid
        assert [o.msg for o in tom.outcomes] == ["Not done: your letter to 'Edith Marsh' came back undelivered."]
        assert db.turns("r1", 0) == []
        with pytest.raises(KeyError):
            db.turns("r1", 56)


def test_a_run_stopped_mid_day_resumes_to_the_same_end(tmp_path):
    with RunDB(str(tmp_path / "a.sqlite")) as db_a, RunDB(str(tmp_path / "b.sqlite")) as db_b:
        db_a.create_run("a", seed=1); db_b.create_run("b", seed=1)
        straight = run(db_a, "a", thin_town(), ScriptedReplier(thin_script), 21).hash()
        # Friday 13 March: Ruth (p01) and Tom (p02, told his payment was refused) are asked and
        # their replies stored before Craig's ask stops the run
        stopper = ScriptedReplier(stop_on(day=11, actor="p03"))
        back = run(db_b, "b", thin_town(), stopper, 21)
        assert back.day == 11 and back.hash() == db_b.snapshot_hash("b", 11)
        assert db_b.change_days("b") == db_b.turn_days("b") == list(range(11))
        assert "b|scripted|d11|p01|turn:|0" in db_b.reply_keys("b")
        assert replay(db_b, "b") == ["b|scripted|d11|p01|turn:|0", "b|scripted|d11|p02|turn:|0"]  # its day is pending
        assert run(db_b, "b", resume(db_b, "b"), stopper, 21 - 11).hash() == straight
        assert replay(db_b, "b") == []
        assert sorted(db_b.reply_keys("b")) == sorted("b" + k[1:] for k in db_a.reply_keys("a"))


def test_a_run_refuses_to_overwrite_a_different_history(tmp_path):
    with RunDB(str(tmp_path / "o.sqlite")) as db:
        db.create_run("o", seed=1)
        run(db, "o", thin_town(), ScriptedReplier(thin_script), 5)
        other = thin_town(); other.transfer("outside:employers", "h1", 100, "extra", "routine:t")
        keys, hash0, changes0 = db.reply_keys("o"), db.snapshot_hash("o", 0), db.changes("o", 0)
        with pytest.raises(ValueError, match=r"'o'.*day 0"):
            run(db, "o", other, ScriptedReplier(thin_script), 2)
        assert (db.reply_keys("o"), db.snapshot_hash("o", 0), db.changes("o", 0)) == (keys, hash0, changes0)
        assert run(db, "o", resume(db, "o"), ScriptedReplier(thin_script), 1).day == 6   # the same history is fine


def test_a_fork_shares_the_past_and_has_its_own_future(eight_week_db, tmp_path):
    with RunDB(copy_of(eight_week_db[0], tmp_path, "f.sqlite")) as db:
        child = fork(db, "r1", 14, "c")
        assert child.hash() == db.snapshot_hash("r1", 14) == db.snapshot_hash("c", 14)
        assert db.get_run("c") == {"id": "c", "seed": 1, "parent": "r1", "parent_day": 14, "schema": 2}
        end = run(db, "c", child, ScriptedReplier(stop_sooner), 7)
        assert end.day == 21 and end.hash() != db.snapshot_hash("r1", 21)
        assert end.get("obligations", "ob9").status == "stopped"
        assert db.load_snapshot("r1", 21).get("obligations", "ob9").status == "active"
        replay(db, "r1")
        replay(db, "c")
        assert fork(db, "r1", 0, "d", seed=7).hash() == db.snapshot_hash("r1", 0) and db.get_run("d")["seed"] == 7
        with pytest.raises(KeyError):
            fork(db, "r1", 99, "e")
        with pytest.raises(KeyError):
            db.get_run("e")


_PROGRAM = """
import sys
from townsim.town.replies import ScriptedReplier
from townsim.town.rundb import RunDB
from townsim.town.runs import run
from townsim.town.thin import thin_script, thin_town
with RunDB(sys.argv[1]) as db:
    db.create_run("p", seed=1)
    print(run(db, "p", thin_town(), ScriptedReplier(thin_script), 14).hash())
"""


def test_two_processes_reach_the_same_hash(tmp_path):
    hashes = []
    for seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", _PROGRAM, str(tmp_path / f"{seed}.sqlite")],
                             env=env, capture_output=True, text=True, check=True)
        hashes.append(out.stdout.strip())
    assert len(hashes[0]) == 64 and hashes[0] == hashes[1]


def _breaks(store, act, cause):
    raise RuntimeError("a bug")


def test_engine_errors_are_kept_with_their_day(tmp_path):
    register(OpSpec("break", "{}", "", 0.0, lambda s, a: None, _breaks, 90))

    def script(key, messages):
        if (key.day, key.actor) in ((3, "p02"), (5, "p04")):
            return '{"acts": [{"op": "break"}, {"op": "break"}]}'
        if (key.day, key.actor) == (11, "p03"):
            raise StopRun("stop")
        return thin_script(key, messages)

    with RunDB(str(tmp_path / "e.sqlite")) as db:
        db.create_run("e", seed=1)
        assert db.errors("e") == []
        run(db, "e", thin_town(), ScriptedReplier(script), 12)
        errors = db.errors("e")
        assert [day for day, _ in errors] == [3, 3, 5, 5]
        assert errors[0][1].startswith("op:p02:break:d3.0: RuntimeError: a bug")
        assert 11 not in db.change_days("e")


def test_a_view_that_raises_skips_that_person_that_day_and_the_run_goes_on(tmp_path, monkeypatch):
    """A view that raises for one person on one day leaves the store as it was, is kept as an
    engine error, and that person is not asked that day; the run goes on and replays exactly."""
    from townsim.town import views
    from townsim.town.entities import Notice
    real = views.view

    def view(store, pid):
        if (store.day, pid) == (4, "p03"):
            store.add("notices", Notice(store.new_id("n", "routine:test"), store.day, pid, "half made"), "routine:test")
            raise TypeError("a view went wrong")
        return real(store, pid)
    monkeypatch.setattr(views, "view", view)
    asked = []

    def script(key, messages):
        asked.append((key.day, key.actor))
        return thin_script(key, messages)
    path = str(tmp_path / "v.sqlite")
    with RunDB(path) as db:
        db.create_run("v", seed=1)
        final = run(db, "v", thin_town(), ScriptedReplier(script), 8)
        assert db.errors("v") == [(4, "view:p03:d4: TypeError: a view went wrong")]
        assert (4, "p03") not in asked and (4, "p01") in asked and final.day == 8
        assert not any(n.text == "half made" for n in final.all("notices"))
        assert not any(c.cause == "routine:test" for c in final.changes)
        assert replay(db, "v") == []


# a day's turns asked as one batch

from townsim.town import families as _families, gen as _gen, hamlet_script as _hamlet_script  # noqa: E402

SCRIPTED_HASH = "c1fe0861a584ea6e47d015d8d5335798ecdb277ab91b7e27506a97c0ce5a13b0"   # changed only on purpose


def _hamlet():
    return _gen.generate(1, _families.names("config/families.yaml"))


class BatchScript:
    """The hamlet's script behind `ask_many`. With `stop`, the pair at index `stop[1]` of the
    first batch on day `stop[0]` is a `StopRun` (every other pair in it is answered); with
    `die`, that batch raises SystemExit instead, as a killed process would. Records each
    batch's keys."""

    def __init__(self, stop=None, die=None):
        self.stop, self.die = stop, die
        self.batches = []

    def model_for(self, actor):
        return "scripted"

    def ask(self, key, messages):
        return Reply(text=_hamlet_script.script(key, messages))

    def ask_many(self, pairs):
        self.batches.append([k for k, _ in pairs])
        day = pairs[0][0].day if pairs else None
        if self.die is not None and day == self.die:
            self.die = None
            raise SystemExit("killed")
        out = [Reply(text=_hamlet_script.script(k, m)) for k, m in pairs]
        if self.stop is not None and day == self.stop[0]:
            out[self.stop[1]] = StopRun("stop here")
            self.stop = None
        return out


def _same_days(db, other, run_id, days):
    for d in range(days + 1):
        assert db.snapshot_hash(run_id, d) == other.snapshot_hash(run_id, d), d
    assert db.change_days(run_id) == db.turn_days(run_id) == list(range(days))
    for d in range(days):
        assert db.changes(run_id, d) == other.changes(run_id, d), d
        assert db.turns(run_id, d) == other.turns(run_id, d), d
    assert db.errors(run_id) == other.errors(run_id)
    assert db.reply_keys(run_id) and sorted(db.reply_keys(run_id)) == sorted(other.reply_keys(run_id))


def test_run_stopped_mid_batch_resumes_to_the_same_state(tmp_path):
    days = 14
    with RunDB(str(tmp_path / "s.sqlite")) as straight_db, RunDB(str(tmp_path / "b.sqlite")) as db:
        straight_db.create_run("h", seed=1); db.create_run("h", seed=1)
        straight = run(straight_db, "h", _hamlet(), BatchScript(), days)
        stopper = BatchScript(stop=(9, 2))
        back = run(db, "h", _hamlet(), stopper, days)
        assert back.day == 9
        [first9] = [b for b in stopper.batches if b[0].day == 9]
        assert len(first9) >= 3
        stored9 = [k for k in db.reply_keys("h") if k.split("|")[2] == "d9"]
        assert stored9 == [k.text() for i, k in enumerate(first9) if i != 2]    # the rest of the batch is kept
        going_on = BatchScript()
        end = run(db, "h", resume(db, "h"), going_on, days - 9)
        assert [b for b in going_on.batches if b[0].day == 9][0] == [first9[2]]   # only the one not stored
        assert end.hash() == straight.hash() and replay(db, "h") == []
        _same_days(db, straight_db, "h", days)


def test_a_process_killed_mid_batch_asks_again_only_what_was_not_stored(tmp_path):
    days = 12
    with RunDB(str(tmp_path / "s.sqlite")) as straight_db, RunDB(str(tmp_path / "k.sqlite")) as db:
        straight_db.create_run("h", seed=1); db.create_run("h", seed=1)
        straight = run(straight_db, "h", _hamlet(), BatchScript(), days)
        with pytest.raises(SystemExit):
            run(db, "h", _hamlet(), BatchScript(die=9), days)
        stored = set(db.reply_keys("h"))
        assert stored and all(int(k.split("|")[2][1:]) < 9 for k in stored)
        going_on = BatchScript()
        end = run(db, "h", resume(db, "h"), going_on, days - 9)
        assert not {k.text() for b in going_on.batches for k in b} & stored
        assert end.hash() == straight.hash() and replay(db, "h") == []
        _same_days(db, straight_db, "h", days)


def test_scripted_hashes_unchanged(tmp_path):
    with RunDB(str(tmp_path / "p.sqlite")) as db:
        db.create_run("h", seed=1)
        final = run(db, "h", _hamlet(), ScriptedReplier(_hamlet_script.script), 56)
    assert final.day == 56 and final.hash() == SCRIPTED_HASH
