"""With lane B a run stopped and resumed in mid-week comes out as the straight run: what used a person's hours
that week is a kept record, not read from the change log, which a snapshot does not keep."""

import json

import pytest

from town.test_b_runs import _b_parent
from townsim.town import arms, outside_script, seat_script
from townsim.town.replies import ScriptedReplier, StopRun
from townsim.town.rundb import RunDB
from townsim.town.runs import replay, resume, run

OWNER = "p3"


def _people(key, messages):          # the shop's owner orders, reads, and works extra hours every day
    if key.actor != OWNER or key.purpose != "turn":
        return outside_script.reply(key, messages)
    return json.dumps({"thoughts": "", "aims": [], "acts": [
        {"op": "order_stock", "supplier": "Coldwater Bend Food Supply", "baskets": 200},
        {"op": "read", "what": "account", "from": "Harlow Springs Food Mart"},
        {"op": "work_extra_hours", "hours": 12}]})


def _replier():
    return ScriptedReplier(_people, seat_script.tool_fn)


class _StopOn:
    def __init__(self, inner, day):
        self.inner, self.day, self.done = inner, day, False

    def model_for(self, actor):
        return self.inner.model_for(actor)

    def ask(self, key, messages):
        if key.day == self.day and not self.done:
            self.done = True
            raise StopRun("cap: stopped for the test")
        return self.inner.ask(key, messages)

    def ask_tools(self, key, messages, tools):
        return self.inner.ask_tools(key, messages, tools)


def _end(path, stop):
    with RunDB(_b_parent(path)) as db:
        s = arms.fork_arm(db, "pre", 7, "c", "choice")
        if stop is None:
            end = run(db, "c", s, _replier(), 14)
        else:
            first = run(db, "c", s, _StopOn(_replier(), stop), 14)
            assert first.day == stop
            end = run(db, "c", resume(db, "c"), _replier(), 21 - first.day)
        assert replay(db, "c") == [] and db.errors("c") == []
        return end.day, end.hash()


@pytest.mark.parametrize("stop", [9, 10, 11])
def test_stopped_in_mid_week_and_resumed_equals_the_straight_run(tmp_path, stop):
    straight = _end(str(tmp_path / "a.sqlite"), None)
    assert _end(str(tmp_path / "b.sqlite"), stop) == straight
