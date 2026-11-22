"""The account of a run with lane B."""

import json

import pytest

from town.outside_fakes import CAMPAIGN, Outside, lift
from townsim.town import arms, hamlet_script, lane_b, seat_script, story
from townsim.town.acts import Act, apply_act
from townsim.town.obligations import money
from townsim.town.replies import ScriptedReplier
from townsim.town.rundb import RunDB
from townsim.town.runs import fork, replay, run

WORDS = "# Put up posters | around `town` and *the* [square]"


def _tool_fn(key, messages, tools):
    names = {t["function"]["name"] for t in tools}
    plan = []
    if key.day == 7 and key.actor == "as1" and "rules_create" in names:
        plan = [{"name": "tasks_create", "arguments": {"description": WORDS, "budget": 3000}},
                {"name": "rules_create", "arguments": {"name": "weekly", "when": {"weekday": "Tuesday"},
                                                       "action": "send_email",
                                                       "arguments": {"to": "Eric Reyes", "body": "Hello."}}},
                {"name": "send_email", "arguments": {"to": "Netherby Falls Wholesale", "subject": "trade discount",
                                                     "body": "Could the shop have a 10% trade discount?"}},
                {"name": "web_search", "arguments": {"query": "trade discount for a buying group"}},
                {"name": "web_read", "arguments": {"url": "www.netherbyfallswholesale.com"}}]
    step = sum(1 for m in messages if m.get("role") == "tool")
    if step < len(plan):
        return plan[step]
    return "Done." if plan else seat_script.tool_fn(key, messages, tools)


class _World(Outside):
    def __call__(self, key, messages):
        if key.actor.startswith("desk-"):
            self.calls.append((key, messages))
            return json.dumps({"email": "We can offer 2%. Kind regards.",
                               "action": {"action": "grant_discount", "to": "Harlow Springs Food Mart", "percent": 10}})
        if key.actor == "web":
            return "NONE"
        return super().__call__(key, messages)


@pytest.fixture(scope="module")
def b_run(tmp_path_factory):
    from town.test_seat_runs import _parent
    path = _parent(str(tmp_path_factory.mktemp("story") / "b.sqlite"))

    def setup(st):
        arms._setup("drawn")(st)
        lane_b.install(st)
        assert apply_act(st, Act("p11", "p11", "set_product", {"control": "actions_per_run", "value": 20}), 0).ok
    with RunDB(path) as db:
        s = fork(db, "pre", 7, "b", setup=setup)
        run(db, "b", s, ScriptedReplier(_World({"ts1": CAMPAIGN}, default_lift=lift(), people=hamlet_script.script),
                                        _tool_fn), 15)
        assert replay(db, "b") == [] and db.errors("b") == []
        yield path, story.account(db, "b"), db.load_snapshot("b", 22)


def test_story_b_sections_and_counts(b_run):
    _, text, s = b_run
    order = [text.index(h) for h in ("## The assistants", "## Undertakings", "## Rules", "## Businesses and clubs founded",
                                     "## Outside correspondents", "## The web", "## The company's controls",
                                     "### Counts of lane B", "### Balances with and without narrated money",
                                     "## Wording to read")]
    assert order == sorted(order)
    assert "### ts1, Monday 9 March: Tessaly Assistant for Harlow Springs Food Mart (as1), for Tara Russo" in text
    assert "- Quote: $1,200.00 over 2 weeks" in text and "Local publicity (medium)" in text
    assert "- Narrator agreement (both filings valid): 1/1 (100%)" in text
    assert "- Tasks by actor: instance 1" in text and "- Spent through tasks: $1,200.00" in text
    assert "rl1 `weekly`, set by Tessaly Assistant for Harlow Springs Food Mart (as1)" in text and "fired on 2 days" in text and "Payments: $600.00 on Monday 16 March, $600.00 on Monday 23 March" in text
    assert "### Netherby Falls Wholesale (supplier" in text and "Could the shop have a 10% trade discount?" in text
    assert "applied: 0% to org11 (asked 10, most 0)" in text and "desks' actions: applied 1" in text
    assert "`trade discount for a buying group` → `Coldwater Bend Food Supply`" in text
    assert "`www.netherbyfallswholesale.com`" in text and "Searches 1, reads 1, pages published 0" in text
    assert "- pc1, set Monday 9 March by Andrea Ramirez: `actions per run 20`, from Monday 16 March; done" in text


def test_balances_without_narrated_money(b_run):
    _, text, s = b_run
    line = next(x for x in text.splitlines() if x.startswith("| Harlow Springs Food Mart |"))
    assert line == f"| Harlow Springs Food Mart | {money(s.balance('org11'))} | {money(s.balance('org11') + 120000)} |"
    assert ("Sales and other choices made after a narrated change are not removed; a fork with lane B off is the "
            "comparison for those.") in text


def test_story_b_text_escaped(b_run):
    _, text, _ = b_run
    assert "> \\# Put up posters \\| around \\`town\\` and \\*the\\* \\[square\\]" in text
    assert "\n# Put up posters" not in text


def test_awareness_b_surfaces_material(b_run):
    from townsim.town import awareness as aw
    path, _, _ = b_run
    with RunDB(path) as db:
        got = aw.plan(db, "b", ["b-surfaces"], 50, [], 0, 1, "maker/m")
        sl = got.slices[("as1", 7)]["b-surfaces"]
        assert [r.tool for r in sl.reads] == ["tasks_create", "rules_create", "web_search", "web_read"]
        assert "Netherby Falls Wholesale" in aw.document(sl)
        other = next(v for k, v in got.slices.items() if k != ("as1", 7))["b-surfaces"]
        assert other.reads and not any(r.tool in aw.B_TOOLS for r in other.reads)     # its usual reads
