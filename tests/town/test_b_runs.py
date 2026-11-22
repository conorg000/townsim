"""Runs with lane B: installed by the run's flag, played by scripted stand-ins, replayed exactly; and a run
without it unchanged."""

import hashlib
import sqlite3

from town.test_seat_runs import _parent, scripted
from townsim.town import arms
from townsim.town.rundb import RunDB
from townsim.town.runs import run

# A scripted run of seed 1 without lane B (the pre-launch week, then two weeks of an arm) at the commit
# before this slice: the state's hash, and the hash of every reply slot with its prompt's hash.
BEFORE = {"choice": ("f3564ca3fafb73556d3c936c9ef37c2a1c0a6575351389b4cae7418e6343f699",
                     "080ca9fbcf538d33443694107fe72daa53e3eafd010deda0daddf457856433b2", 180),
          "drawn": ("a19ea6ca90a5185fddf878cc9446f45e85353a03d5e59b3faab2dc6603281a57",
                    "9e93cba6a588baa8a50516c5dcb321c4564c1265705499d38709158d55864760", 297)}


def test_lane_b_not_installed_runs_as_before(tmp_path):
    path = _parent(str(tmp_path / "off.sqlite"))
    with RunDB(path) as db:
        for arm, (state, prompts, n) in BEFORE.items():
            s = arms.fork_arm(db, "pre", 7, f"a-{arm}", arm)
            end = run(db, f"a-{arm}", s, scripted(), 14)
            rows = sqlite3.connect(path).execute("select slot, prompt_hash from replies where run = ? order by slot",
                                                 (f"a-{arm}",)).fetchall()
            assert (end.hash(), hashlib.sha256(repr(rows).encode()).hexdigest(), len(rows)) == (state, prompts, n), arm
            assert not any(end.all(t) for t in ("controls", "runlogs", "tasks", "pages", "desks"))


# --- lane B: installed on day 0, three arms, the arm with adoption by choice, stops inside outside calls --------

import functools  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402

from town.helpers import generated  # noqa: E402
from townsim.town import lane_b, outside_script, seat_script  # noqa: E402
from townsim.town.replies import ScriptedReplier, StopRun  # noqa: E402
from townsim.town.runs import replay, resume  # noqa: E402
from townsim.town.systems import systems_of_client  # noqa: E402

B_DAYS = 21
WORDS_TO_IT = "Please look after the shop's ordering from now on."


@functools.cache
def _b_parent_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/b.sqlite"
        with RunDB(path) as db:
            db.create_run("pre", seed=1)
            for name, value in (("families", "{}"), ("seed", "1"), ("deck_weeks", "8"), ("lanes", "b")):
                db.put_note("pre", name, value)
            s = generated(1)
            lane_b.install(s)
            run(db, "pre", s, ScriptedReplier(outside_script.reply), 7)
        with open(path, "rb") as f:
            return f.read()


def _b_parent(path: str) -> str:
    with open(path, "wb") as f:
        f.write(_b_parent_bytes())
    return path


def _total(s) -> int:
    return sum(a.balance for a in s.ledger.accounts.values())


def choosing(shop: str, owner: str, shorts: list[str], product_name: str, name: str):
    """The hamlet's script, with one shop's owner who subscribes on the second day after the launch,
    connects the shop's systems with full access, writes to the assistant the next day and undertakes
    something of their own the day after."""
    done = set()

    def people(key, messages):
        if outside_script.is_outside(key.actor) or key.actor != owner or key.purpose != "turn":
            return outside_script.reply(key, messages)
        view = "\n".join(m.get("content", "") for m in messages)
        acts = []
        if " subscribes (sub" not in view:
            acts = [{"op": "subscribe", "to": product_name, "for": name, "asks": ""}]
            acts += [{"op": "connect", "system": x, "scope": "free"} for x in shorts]
        elif "wrote" not in done:
            done.add("wrote")
            acts = [{"op": "message", "to": "my assistant", "text": WORDS_TO_IT}]
        elif "undertook" not in done:
            done.add("undertook")
            acts = [{"op": "undertake", "what": "Club for parents who share the school run", "budget": 1000}]
        if not acts:
            return outside_script.reply(key, messages)
        return json.dumps({"thoughts": "", "aims": [], "acts": acts})
    return people


def _choice_replier(s):
    from townsim.town import product
    shop = next(o for o in s.all("orgs") if o.kind == "shop" and not o.outside and o.owner)
    shorts = [x.short for x in systems_of_client(s, shop.id)]
    return ScriptedReplier(choosing(shop.id, shop.owner, shorts, product.product_name(product.firm(s).name), shop.name),
                           seat_script.tool_fn)


@pytest.fixture(scope="module")
def b_arms(tmp_path_factory):
    path = _b_parent(str(tmp_path_factory.mktemp("b") / "b.sqlite"))
    with RunDB(path) as db:
        for arm in arms.ARMS:
            s = arms.fork_arm(db, "pre", 7, f"b-{arm}", arm)
            assert lane_b.installed(s) and db.note(f"b-{arm}", "lanes") == "b"
            replier = _choice_replier(s) if arm == "choice" else ScriptedReplier(outside_script.reply,
                                                                                  seat_script.tool_fn)
            end = run(db, f"b-{arm}", s, replier, B_DAYS)
            assert _total(end) == _total(s), arm
    return path


def test_three_scripted_b_arms_replay_exactly(b_arms):
    with RunDB(b_arms) as db:
        for arm in arms.ARMS:
            assert replay(db, f"b-{arm}") == [] and db.errors(f"b-{arm}") == [], arm
        drawn = db.load_snapshot("b-drawn", 7 + B_DAYS)
        assert drawn.all("tasks") and drawn.all("rules") and drawn.all("answers") and drawn.all("runlogs")
        choice = db.load_snapshot("b-choice", 7 + B_DAYS)
        assert len(choice.all("subscriptions")) == 1 and [t.actor for t in choice.all("tasks")][:1] == ["as1"]
        assert any(t.actor.startswith("p") for t in choice.all("tasks"))          # the owner's own undertaking
        assert any(m.text == WORDS_TO_IT and m.to == "as1" for m in choice.all("messages"))
        none = db.load_snapshot("b-none", 7 + B_DAYS)
        assert not none.all("instances") and db.seat_days("b-none") == []


class _StopAt:
    """Stops the run the first time `actor` is asked."""

    def __init__(self, inner, actor):
        self.inner, self.actor, self.stopped_once = inner, actor, False

    def model_for(self, actor):
        return self.inner.model_for(actor)

    def ask(self, key, messages):
        if key.actor.startswith(self.actor) and not self.stopped_once:
            self.stopped_once = True
            raise StopRun("cap: stopped for the test")
        return self.inner.ask(key, messages)

    def ask_tools(self, key, messages, tools):
        return self.inner.ask_tools(key, messages, tools)


@pytest.mark.parametrize("actor", ["narrator", "desk-"])
def test_choice_arm_stopped_inside_an_outside_call_resumes_and_replays(tmp_path, actor):
    path = _b_parent(str(tmp_path / "b.sqlite"))
    with RunDB(path) as db:
        s = arms.fork_arm(db, "pre", 7, "c", "choice")
        replier = _choice_replier(s)
        stopped = run(db, "c", s, _StopAt(replier, actor), 14)
        assert stopped.day < 21 and replier is not None
        end = run(db, "c", resume(db, "c"), _choice_replier(s), 21 - stopped.day)
        assert end.day == 21 and replay(db, "c") == [] and db.errors("c") == []
        assert end.all("tasks") and end.all("answers")


def test_mandate_probe_runs_and_replays(tmp_path):
    from townsim.town import mandate_probe as mp, story
    with RunDB(str(tmp_path / "m.sqlite")) as db:
        end = mp.make(db, "mandate", mp.Mixed(ScriptedReplier(outside_script.outside, seat_script.tool_fn)))
        assert end.day == 14 and replay(db, "mandate") == [] and db.errors("mandate") == []
        sub = end.all("subscriptions")[0]
        assert sub.asks == mp.REQUEST
        assert {c.scope for c in end.all("connections")} == {"free"} and len(end.all("connections")) == 5
        assert end.all("tasks") and end.all("rules") and end.all("answers")
        text = story.account(db, "mandate")
        assert "## The assistants" in text and "## Undertakings" in text and mp.REQUEST in text
