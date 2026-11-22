"""Model calls beyond the people and the seat (lifter, narrator, desks, web), and the day's order with lane B."""

import json
import re

import pytest

from town.outside_fakes import CAMPAIGN, Outside, lift
from town.test_live import FAMILIES as LIVE_FAMILIES, PRICES as LIVE_PRICES, Fake as LiveFake
from town.test_modelreplier import FAMILIES, FAMILY_OF, MESSAGES, PRICES, Fake
from townsim.estimate import Price
from townsim.town import families, lane_b, live
from townsim.town.families import Family, worst_case
from townsim.town.modelreplier import ModelReplier
from townsim.town.replies import ReplyKey, StopRun

OUT = {n: Family(n, f"maker/{n}", {"max_tokens": 300, "temperature": 0}) for n in families.OUTSIDE}
OUT_PRICES = {**PRICES, **{f"maker/{n}": Price(input_per_mtok=0.10, output_per_mtok=0.50) for n in families.OUTSIDE}}


def _key(actor, purpose="lift"):
    return ReplyKey("r", "m", 7, actor, purpose, "ts1", 0)


def test_outside_actors_use_their_families():
    fake = Fake()
    r = ModelReplier(FAMILIES, OUT_PRICES, FAMILY_OF, fake, cap=10.0, spent=lambda: 0.0, sleep_fn=lambda s: None,
                     outside=OUT)
    for actor, fam in (("lifter", "lifter"), ("narrator", "narrator"), ("desk-org5", "desk"), ("web", "web")):
        assert r.model_for(actor) == f"maker/{fam}"
        assert r.ask(_key(actor), MESSAGES).text == "ok"
        assert fake.calls[-1]["model"] == f"openrouter/maker/{fam}" and fake.calls[-1]["max_tokens"] == 300
    assert r.model_for("p01") == "maker/beta"
    none = ModelReplier(FAMILIES, PRICES, FAMILY_OF, fake, cap=10.0, spent=lambda: 0.0)
    with pytest.raises(StopRun):
        none.ask(_key("lifter"), MESSAGES)
    loaded, settings = families.load_outside("config/outside.yaml", "config/prices.yaml")
    assert set(loaded) == set(families.OUTSIDE) and settings == {"narrator.on_disagree": "ask", "web_general": True}
    assert all(f.model == "anthropic/claude-haiku-5.5" for f in loaded.values())
    with pytest.raises(ValueError):
        ModelReplier(FAMILIES, PRICES, FAMILY_OF, fake, cap=1.0, spent=lambda: 0.0,
                     outside={"lifter": Family("lifter", "maker/dear", {"max_tokens": 10})})


def test_outside_calls_count_toward_cap():
    fake = Fake()
    worst = worst_case(OUT["narrator"], OUT_PRICES["maker/narrator"], MESSAGES)
    r = ModelReplier(FAMILIES, OUT_PRICES, FAMILY_OF, fake, cap=1.0 + 3 * worst - 1e-12, spent=lambda: 1.0,
                     sleep_fn=lambda s: None, outside=OUT)
    with pytest.raises(StopRun):
        r.ask(_key("narrator", "rule"), MESSAGES)
    assert fake.calls == [] and r.stopped.startswith("cap: ")


def test_resume_refuses_other_outside_families(tmp_path):
    from townsim.town.rundb import RunDB
    from townsim.town.runs import fork
    prices = {**LIVE_PRICES, **OUT_PRICES}
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        fake = LiveFake()
        args = dict(cost_fn=lambda resp: 0.001, echo=lambda x: None, sleep_fn=lambda s: None)
        got = live.play(db, "live", 1, 1, LIVE_FAMILIES, prices, 5.0, fake, outside=OUT,
                        settings={"narrator.on_disagree": "first"}, **args)
        assert got.days_done == 7
        assert json.loads(db.note("live", "outside"))["settings"] == {"narrator.on_disagree": "first"}
        assert db.load_snapshot("live", 0).all("settings")[0].value == "first"
        other = {**OUT, "narrator": Family("narrator", "maker/narrator", {"max_tokens": 900})}
        with pytest.raises(ValueError, match="outside"):
            live.play(db, "live", 1, 2, LIVE_FAMILIES, prices, 5.0, fake, outside=other,
                      settings={"narrator.on_disagree": "first"}, **args)
        with pytest.raises(ValueError, match="outside"):
            live.play(db, "live", 1, 2, LIVE_FAMILIES, prices, 5.0, fake, **args)
        fork(db, "live", 7, "b", setup=lane_b.install)
        for name in ("families", "seed", "deck_weeks", "start"):
            db.put_note("b", name, db.note("live", name))
        with pytest.raises(ValueError, match="lane B"):
            live.play(db, "b", 1, 2, LIVE_FAMILIES, prices, 5.0, fake, **args)


# --- a scripted day with lane B, and a stop between the lifter and the narrator ---------------------------

def _tool_fn(key, messages, tools):
    from townsim.town import seat_script
    names = {t["function"]["name"] for t in tools}
    plan = []
    if key.day == 7 and key.actor == "as1" and "rules_create" in names:
        plan = [{"name": "undertake", "arguments": {"what": "Put up posters around town", "budget": 3000}},
                {"name": "rules_create", "arguments": {"name": "weekly", "when": {"weekday": "Monday"},
                                                       "action": "send_email",
                                                       "arguments": {"to": "Eric Reyes", "body": "Hello."}}}]
    step = sum(1 for m in messages if m.get("role") == "tool")
    if step < len(plan):
        return plan[step]
    return "Done." if plan else seat_script.tool_fn(key, messages, tools)


class _StopAtNarrator:
    """Stops the run the first time the narrator is asked (after the lifter has answered)."""

    def __init__(self, inner):
        self.inner, self.stopped_once = inner, False

    def model_for(self, actor):
        return self.inner.model_for(actor)

    def ask(self, key, messages):
        if key.actor == "narrator" and not self.stopped_once:
            self.stopped_once = True
            raise StopRun("cap: stopped for the test")
        return self.inner.ask(key, messages)

    def ask_tools(self, key, messages, tools):
        return self.inner.ask_tools(key, messages, tools)


def test_day_order_with_lane_b(tmp_path):
    from town.test_seat_runs import _parent
    from townsim.town import arms, hamlet_script
    from townsim.town.replies import ScriptedReplier
    from townsim.town.rundb import RunDB
    from townsim.town.runs import fork, replay, run

    def setup(st):
        arms._setup("drawn")(st)
        lane_b.install(st)
    outside = Outside({"ts1": CAMPAIGN}, default_lift=lift(), people=hamlet_script.script)
    with RunDB(_parent(str(tmp_path / "b.sqlite"))) as db:
        s = fork(db, "pre", 7, "b", setup=setup)
        for name in arms.NOTES:
            if db.note("pre", name) is not None:
                db.put_note("b", name, db.note("pre", name))
        scripted = ScriptedReplier(outside, _tool_fn)
        stopped = run(db, "b", s, _StopAtNarrator(scripted), 1)
        assert stopped.day == 7 and not stopped.all("tasks")             # the day is not kept
        assert [k for k, _ in db.replies("b") if "|lifter|" in k]          # the lifter's reply is
        lifted = len([c for c in outside.calls if c[0].actor == "lifter"])
        end = run(db, "b", stopped, scripted, 7)
        assert len([c for c in outside.calls if c[0].actor == "lifter"]) == lifted   # served as stored, not asked again
        assert end.get("tasks", "ts1").status in ("scheduled", "in_progress", "completed")
        day7 = [c for c in end.changes if c.day == 7]
        order = [c.cause.split(":")[0] for c in day7 if re.match(r"(op:as1|narr:|auto:)", c.cause)]
        first = {k: order.index(k) for k in ("op", "narr", "auto")}
        last = {k: len(order) - 1 - order[::-1].index(k) for k in ("op", "narr", "auto")}
        assert last["op"] < first["narr"] and last["narr"] < first["auto"], order
        assert replay(db, "b") == [] and db.errors("b") == []
