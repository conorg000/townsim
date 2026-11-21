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
        db.put_reply("r1", "r1|scripted|d30|p02|turn:|5", "x", Reply(text=EMPTY))
        with pytest.raises(ReplayMismatch) as e:
            replay(db, "r1")
    assert e.value.why == "unused" and "d30" in str(e.value)


def test_replay_refuses_replies_from_more_than_one_model(eight_week_db, tmp_path):
    with RunDB(copy_of(eight_week_db[0], tmp_path, "m.sqlite")) as db:
        db.put_reply("r1", "r1|other|d3|p02|turn:|0", "x", Reply(text=EMPTY))
        with pytest.raises(ValueError):
            replay(db, "r1")


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
        assert db.get_run("c") == {"id": "c", "seed": 1, "parent": "r1", "parent_day": 14}
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
    register(OpSpec("break", "{}", "", 0.0, lambda s, a: None, _breaks))

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
