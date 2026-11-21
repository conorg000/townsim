import dataclasses
import sqlite3

import pytest

from townsim.town.replies import (
    Keys,
    Recording,
    Replaying,
    ReplayMismatch,
    Reply,
    ReplyKey,
    ScriptedReplier,
    StopRun,
    prompt_hash,
)
from townsim.town.rundb import RunDB
from townsim.town.store import Change, Store

MSGS = [{"role": "user", "content": "view"}]


@pytest.fixture
def db(tmp_path):
    d = RunDB(str(tmp_path / "x.sqlite"))
    d.create_run("r1", seed=1)
    yield d
    d.close()


def test_keys_count_per_day_actor_purpose_and_ref():
    k = Keys("r1")
    a = k.next("scripted", 3, "p01", "turn"); b = k.next("scripted", 3, "p01", "turn"); c = k.next("scripted", 3, "p01", "conv", "c7")
    assert (a.n, b.n, c.n) == (0, 1, 0) and a.text() == "r1|scripted|d3|p01|turn:|0"
    assert c.text() == "r1|scripted|d3|p01|conv:c7|0"
    assert b == ReplyKey("r1", "scripted", 3, "p01", "turn", "", 1)
    assert k.next("scripted", 4, "p01", "turn").n == 0
    assert k.next("scripted", 3, "p02", "turn").n == 0


def test_a_fresh_keys_starts_every_count_again():
    first = [Keys("r1").next("scripted", 0, "p01", "turn") for _ in range(2)]
    assert first[0] == first[1] and first[0].n == 0


@pytest.mark.parametrize("field,value", [
    ("model", "a|b"), ("actor", "p|1"), ("purpose", "tu|rn"), ("purpose", "tu:rn"), ("ref", "c|7"),
])
def test_keys_refuse_parts_that_would_make_the_text_ambiguous(field, value):
    args = {"model": "scripted", "day": 0, "actor": "p01", "purpose": "turn", "ref": ""}
    args[field] = value
    with pytest.raises(ValueError):
        Keys("r1").next(**args)


def test_keys_refuse_a_run_with_a_bar():
    with pytest.raises(ValueError):
        Keys("r|1").next("scripted", 0, "p01", "turn")


def test_prompt_hash_depends_on_content_not_dict_order():
    assert prompt_hash([{"role": "user", "content": "x"}]) == prompt_hash([{"content": "x", "role": "user"}])
    assert prompt_hash([{"role": "user", "content": "x"}]) != prompt_hash([{"role": "user", "content": "y"}])
    assert len(prompt_hash(MSGS)) == 64


def test_scripted_replier_returns_the_function_text():
    r = ScriptedReplier(lambda key, msgs: f"{key.actor}:{msgs[0]['content']}")
    assert r.model == "scripted"
    assert r.ask(Keys("r1").next("scripted", 0, "p01", "turn"), MSGS) == Reply(text="p01:view")


def test_recording_stores_once_and_serves_a_resume_without_asking(tmp_path):
    db = RunDB(str(tmp_path / "x.sqlite")); db.create_run("r1", seed=1); calls = []
    inner = ScriptedReplier(lambda key, msgs: calls.append(key) or "hello")
    rec = Recording(inner, db, "r1"); key = Keys("r1").next("scripted", 0, "p01", "turn"); msgs = [{"role": "user", "content": "view"}]
    assert rec.ask(key, msgs).text == "hello" and rec.ask(key, msgs).text == "hello" and len(calls) == 1
    with pytest.raises(ReplayMismatch) as e: rec.ask(key, [{"role": "user", "content": "a different view"}])
    assert e.value.why == "prompt changed" and "d0|p01" in e.value.key
    db.close()


def test_recording_model_is_the_inner_model(db):
    assert Recording(ScriptedReplier(lambda k, m: "x"), db, "r1").model == "scripted"


def test_recording_serves_a_resume_from_a_second_handle_on_the_file(tmp_path):
    path = str(tmp_path / "x.sqlite")
    first = RunDB(path); first.create_run("r1", seed=1)
    Recording(ScriptedReplier(lambda k, m: "hello"), first, "r1").ask(Keys("r1").next("scripted", 0, "p01", "turn"), MSGS)
    second = RunDB(path)

    def never(k, m):
        raise AssertionError("a stored reply must not be asked for again")

    assert Recording(ScriptedReplier(never), second, "r1").ask(Keys("r1").next("scripted", 0, "p01", "turn"), MSGS).text == "hello"
    first.close(); second.close()


def test_recording_stores_nothing_when_the_inner_raises(db):
    def stop(k, m):
        raise StopRun("spending cap")

    key = Keys("r1").next("scripted", 0, "p01", "turn")
    with pytest.raises(StopRun):
        Recording(ScriptedReplier(stop), db, "r1").ask(key, MSGS)
    assert db.reply_keys("r1") == []
    assert Recording(ScriptedReplier(lambda k, m: "later"), db, "r1").ask(key, MSGS).text == "later"


def test_recording_serves_a_stored_reply_whole(db):
    whole = Reply(text=None, tool={"name": "pay", "args": {"cents": 5}}, reasoning="r", finish="tool", tokens_in=3, tokens_out=4, cost=0.5, error="e")
    key = Keys("r1").next("scripted", 0, "p01", "turn")
    db.put_reply("r1", key.text(), prompt_hash(MSGS), whole)
    assert Recording(ScriptedReplier(lambda k, m: "fresh"), db, "r1").ask(key, MSGS) == whole


def test_replaying_fails_on_a_missing_key_and_reports_unused_ones(db):
    keys = Keys("r1")
    k0, k1, k2 = (keys.next("scripted", 0, a, "turn") for a in ("p02", "p01", "p03"))
    rec = Recording(ScriptedReplier(lambda k, m: k.actor), db, "r1")
    for k in (k0, k1, k2):
        rec.ask(k, MSGS)

    rep = Replaying(db, "r1", "scripted")
    assert rep.model == "scripted"
    assert rep.unused() == [k0.text(), k1.text(), k2.text()]
    assert rep.ask(k1, MSGS).text == "p01"
    assert rep.unused() == [k0.text(), k2.text()]

    missing = Keys("r1").next("scripted", 9, "p01", "turn")
    with pytest.raises(ReplayMismatch) as e:
        rep.ask(missing, MSGS)
    assert e.value.why == "missing" and e.value.key == missing.text()
    assert str(e.value) == f"replay mismatch (missing) at {missing.text()}"

    with pytest.raises(ReplayMismatch) as e:
        rep.ask(k0, [{"role": "user", "content": "other"}])
    assert e.value.why == "prompt changed" and e.value.key == k0.text()


def test_a_reply_is_stored_whole(db):
    full = Reply(text="hi", tool={"name": "pay", "args": {"to": "p02", "cents": 120}}, reasoning="because", finish="stop", tokens_in=11, tokens_out=7, cost=0.0042, error=None)
    failed = Reply(text=None, error="timeout")
    db.put_reply("r1", "k1", "h1", full)
    db.put_reply("r1", "k2", "h2", failed)
    assert db.get_reply("r1", "k1") == ("h1", full)
    assert db.get_reply("r1", "k2") == ("h2", failed)
    assert db.get_reply("r1", "k3") is None
    assert db.reply_keys("r1") == ["k1", "k2"]


def test_a_reply_key_is_written_once(db):
    db.put_reply("r1", "k1", "h1", Reply(text="a"))
    with pytest.raises(ValueError):
        db.put_reply("r1", "k1", "h1", Reply(text="b"))
    assert db.get_reply("r1", "k1") == ("h1", Reply(text="a"))


def test_replies_are_kept_per_run(db):
    db.create_run("r2", seed=2)
    db.put_reply("r1", "k", "h", Reply(text="one"))
    db.put_reply("r2", "k", "h", Reply(text="two"))
    assert db.get_reply("r2", "k")[1].text == "two" and db.reply_keys("r1") == ["k"]


def test_runs_are_created_once_and_read_back(db):
    db.create_run("r2", seed=7, parent="r1", parent_day=3)
    assert db.get_run("r1") == {"id": "r1", "seed": 1, "parent": None, "parent_day": None}
    assert db.get_run("r2") == {"id": "r2", "seed": 7, "parent": "r1", "parent_day": 3}
    with pytest.raises(ValueError):
        db.create_run("r1", seed=9)
    with pytest.raises(KeyError):
        db.get_run("nope")


def _town() -> Store:
    s = Store()
    s.place = "hamlet"
    s.open_account("bank", "routine:setup", allow_credit=True)
    s.open_account("p01", "routine:setup")
    s.transfer("bank", "p01", 500, "wage", "routine:setup")
    s.advance_day()
    return s


def test_snapshots_round_trip_and_keep_their_hash(db):
    s = _town()
    db.save_snapshot("r1", 1, s)
    loaded = db.load_snapshot("r1", 1)
    assert loaded.hash() == db.snapshot_hash("r1", 1) == s.hash()
    assert loaded is not s and db.load_snapshot("r1", 1) is not loaded
    assert db.snapshot_hash("r1", 2) is None
    with pytest.raises(KeyError):
        db.load_snapshot("r1", 2)


def test_a_loaded_snapshot_can_change_and_be_undone(db):
    db.save_snapshot("r1", 1, _town())
    loaded = db.load_snapshot("r1", 1)
    m = loaded.mark()
    loaded.transfer("p01", "bank", 200, "rent", "routine:rent")
    loaded.new_id("ob")
    loaded.advance_day()
    assert loaded.hash() != db.snapshot_hash("r1", 1)
    loaded.undo_to(m)
    assert loaded.hash() == db.snapshot_hash("r1", 1)
    assert db.load_snapshot("r1", 1).balance("p01") == 500


def test_a_snapshot_for_a_day_is_replaced_and_days_are_listed(db):
    s = _town()
    db.save_snapshot("r1", 2, s)
    db.save_snapshot("r1", 0, Store())
    s.transfer("p01", "bank", 100, "rent", "routine:rent")
    db.save_snapshot("r1", 2, s)
    assert db.snapshot_hash("r1", 2) == s.hash()
    assert db.load_snapshot("r1", 2).balance("p01") == 400
    assert db.snapshot_days("r1") == [0, 2]
    assert db.snapshot_days("r2") == []


# review fixes


def _key(run="r1", day=0, actor="p01"):
    return Keys(run).next("scripted", day, actor, "turn")


class _Fixed:
    model = "scripted"

    def __init__(self, reply):
        self.reply = reply

    def ask(self, key, messages):
        return self.reply


def test_live_resumed_and_replayed_replies_are_equal(tmp_path):
    path = str(tmp_path / "x.sqlite")
    reply = Reply(text="ok", tool={"name": "pay", "args": {"to": ("p02", "p03")}}, tokens_in=2, cost=0.1)
    with RunDB(path) as db:
        db.create_run("r1", seed=1)
        live = Recording(_Fixed(reply), db, "r1").ask(_key(), MSGS)
    with RunDB(path) as db:
        resumed = Recording(_Fixed(Reply(text="other")), db, "r1").ask(_key(), MSGS)
        replayed = Replaying(db, "r1", "scripted").ask(_key(), MSGS)
    assert live == resumed == replayed
    assert live.tool == {"name": "pay", "args": {"to": ["p02", "p03"]}}


def test_a_reply_that_cannot_be_stored_becomes_a_stored_error(db):
    bad = Reply(text="ok", tool={"thing": object()}, tokens_in=5, tokens_out=6, cost=0.25)
    live = Recording(_Fixed(bad), db, "r1").ask(_key(), MSGS)
    assert live.text is None and live.tool is None
    assert live.error.startswith("the reply could not be stored: ")
    assert (live.tokens_in, live.tokens_out, live.cost) == (5, 6, 0.25)
    assert db.get_reply("r1", _key().text()) == (prompt_hash(MSGS), live)


def test_a_reply_with_a_nan_cost_still_stores_an_error(db):
    live = Recording(_Fixed(Reply(text="ok", cost=float("nan"))), db, "r1").ask(_key(), MSGS)
    assert live.text is None and live.error.startswith("the reply could not be stored: ")
    assert db.get_reply("r1", _key().text())[1] == live


def test_a_refused_write_does_not_lock_the_file_for_another_handle(tmp_path):
    path = str(tmp_path / "x.sqlite")
    with RunDB(path) as a, RunDB(path) as b:
        a.create_run("r1", seed=1)
        a.put_reply("r1", "k", "h", Reply(text="x"))
        with pytest.raises(ValueError):
            a.create_run("r1", seed=1)
        b.create_run("r2", seed=2)
        with pytest.raises(ValueError):
            a.put_reply("r1", "k", "h", Reply(text="y"))
        b.put_reply("r2", "k", "h", Reply(text="z"))
        assert a.get_reply("r2", "k")[1].text == "z"


@pytest.mark.parametrize("day", [-1, True, 1.0, "1"])
def test_keys_refuse_a_day_that_is_not_a_whole_count(day):
    with pytest.raises(ValueError):
        Keys("r1").next("scripted", day, "p01", "turn")


def test_repliers_refuse_a_key_from_another_run(db):
    with pytest.raises(ValueError):
        Recording(ScriptedReplier(lambda k, m: "x"), db, "r1").ask(_key(run="r2"), MSGS)
    with pytest.raises(ValueError):
        Replaying(db, "r1", "scripted").ask(_key(run="r2"), MSGS)
    assert db.reply_keys("r1") == []


def test_replaying_refuses_a_key_asked_twice(db):
    Recording(ScriptedReplier(lambda k, m: "x"), db, "r1").ask(_key(), MSGS)
    rep = Replaying(db, "r1", "scripted")
    rep.ask(_key(), MSGS)
    with pytest.raises(ReplayMismatch) as e:
        rep.ask(_key(), MSGS)
    assert e.value.why == "asked twice" and e.value.key == _key().text()


def test_a_snapshot_holds_state_not_history(db):
    s = _town()
    s.engine_errors.append("boom")
    n = len(s.changes)
    db.save_snapshot("r1", 1, s)
    assert len(s.changes) == n and s.engine_errors == ["boom"]
    loaded = db.load_snapshot("r1", 1)
    assert loaded.changes == [] and loaded.engine_errors == []
    assert loaded.hash() == s.hash() == db.snapshot_hash("r1", 1)


@dataclasses.dataclass
class _Row:
    id: str
    name: str


def test_changes_are_kept_per_day(db):
    day1 = [Change(1, "people", "p1", "+", None, _Row("p1", "Ann"), "routine:setup"),
            Change(1, "people", "p1", "name", "Ann", "Anne", "op:p1:rename:d1.0")]
    db.put_changes("r1", 1, day1)
    db.put_changes("r1", 0, [])
    assert db.changes("r1", 1) == day1 and isinstance(db.changes("r1", 1)[0], Change)
    db.put_changes("r1", 1, day1[:1])
    assert db.changes("r1", 1) == day1[:1]
    assert db.change_days("r1") == [0, 1]
    with pytest.raises(KeyError):
        db.changes("r1", 2)


def test_rundb_closes_when_used_as_a_context_manager(tmp_path):
    with RunDB(str(tmp_path / "x.sqlite")) as db:
        db.create_run("r1", seed=1)
    with pytest.raises(sqlite3.ProgrammingError):
        db.get_run("r1")
