"""The seat's tools for founding and rules, what people see of them, and a run with them that replays."""

import json
import re

from town.test_seat_texts import drawn
from townsim.town import arms, connections, lane_b, product, rules, seat_script, seat_texts, seat_tools as ST, views
from townsim.town.acts import Act, apply_act
from townsim.town.product import NEUTRAL_BANNED

CAUSE = "routine:setup"
B = {"business_register", "members_invite", "members_list", "members_set_fee", "rules_create", "rules_list",
     "rules_delete"}
CLUB = {"name": "Carter Garden Club", "kind": "club", "offering": "Seeds, tools and a shared plot on Depot Street",
        "weekly_fee": 4}


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _names(s, inst):
    return {t["function"]["name"] for t in ST.tools_for(s, s.get("instances", inst))}


def _run(s, inst, tool, args, n=0):
    return ST.run_tool(s, s.get("instances", inst), tool, args, ST.Ctx(), n)


def test_new_tools_offered_by_connection_and_scope():
    s = drawn()
    assert not _names(s, "as1") & B and not _names(s, "as5") & B          # no lane B: nothing new
    s = _b()
    assert _names(s, "as4") & B == {"rules_create", "rules_list", "rules_delete"}       # Bank read only
    assert "business_register" in _names(s, "as1") and "members_invite" not in _names(s, "as1")
    done = _run(s, "as5", "business_register", CLUB)
    out = json.loads(done.text)
    v = s.all("ventures")[0]
    assert done.ok and done.kind == "act" and out == {
        "status": "registered", "business": "Carter Garden Club", "account": f"checking {ST.seat_ids.mask(s, v.org)}",
        "registered": "2026-03-09", "weekly_fee": "4.00", "members": 0}
    assert {"members_invite", "members_list", "members_set_fee"} <= _names(s, "as5")
    tools = {t["function"]["name"]: t["function"] for t in ST.tools_for(s, s.get("instances", "as5"))}
    assert tools["members_invite"]["parameters"]["properties"]["business"]["enum"] == ["Carter Garden Club"]
    assert tools["bank_make_payment"]["parameters"]["properties"]["account"]["enum"] == [
        f"checking {ST.seat_ids.mask(s, 'h1')}", f"Carter Garden Club checking {ST.seat_ids.mask(s, v.org)}"]
    assert f"Carter Garden Club checking {ST.seat_ids.mask(s, v.org)}" in \
        tools["bank_get_account"]["parameters"]["properties"]["account"]["enum"]
    # members connected read only: the list, and no acts
    connections.make(s, "p1", s.get("instances", "as5"), f"members:{v.org}", "read", 0, CAUSE)
    assert _names(s, "as5") & {"members_invite", "members_list", "members_set_fee"} == {"members_list"}
    refused = json.loads(_run(s, "as5", "members_invite", {"business": "Carter Garden Club", "to": "Eric Reyes"}).text)
    assert refused["status"] == "declined" and refused["error"] == "insufficient_scope"
    # registration refused for a name taken, and beyond a weekly limit
    taken = json.loads(_run(s, "as1", "business_register", CLUB).text)
    assert taken["error"] == "invalid_argument" and "already" in taken["message"]
    over = json.loads(_run(s, "as3", "business_register", {**CLUB, "name": "Whitaker Walks"}).text)
    assert over["error"] == "limit_exceeded"


def test_members_tools():
    s = _b()
    _run(s, "as5", "business_register", CLUB)
    done = _run(s, "as5", "members_invite", {"business": "Carter Garden Club", "to": "Eric Reyes"}, 1)
    out = json.loads(done.text)
    rq = s.all("requests")[-1]
    assert out == {"status": "sent", "request_id": ST.seat_ids.display(s, rq.id), "to": "Eric Reyes",
                   "summary": "Accepting makes the Reyes household a member of Carter Garden Club: $4.00 a week from its "
                              "account from Monday 16 March 2026, until it leaves."}
    apply_act(s, Act("p7", "p7", "answer", {"request": rq.id, "answer": "accept"}), 2)
    m = s.all("members")[0]
    listed = json.loads(_run(s, "as5", "members_list", {"business": "carter garden club"}, 3).text)
    assert listed == {"business": "Carter Garden Club", "weekly_fee": "4.00", "members": [
        {"member": "Reyes household", "since": "2026-03-09", "fee": "4.00", "id": ST.seat_ids.display(s, m.id)}]}
    assert listed["members"][0]["id"].startswith("mbr_")
    fee = json.loads(_run(s, "as5", "members_set_fee", {"business": "Carter Garden Club", "weekly_fee": 6}, 4).text)
    assert fee == {"status": "updated", "business": "Carter Garden Club", "weekly_fee": "6.00"}


def test_rules_create_through_seat_and_fire_as_instance():
    s = _b()
    done = _run(s, "as1", "rules_create", {
        "name": "reorder", "when": {"weekday": "Monday"}, "action": "ordering_place_order",
        "arguments": {"supplier": "Netherby Falls Wholesale", "quantity": {"figure": "sold_last_week", "times": 1.1, "plus": 0}}})
    out = json.loads(done.text)
    rid = s.all("rules")[-1].id
    assert done.ok and out == {"status": "created", "rule_id": ST.seat_ids.display(s, rid), "first_check": "2026-03-09"}
    assert out["rule_id"].startswith("rule_")
    bad = json.loads(_run(s, "as3", "rules_create", {"name": "x", "when": {"figure": "stock", "is": "below", "value": 5},
                                                     "action": "send_email", "arguments": {"to": "Eric Reyes", "body": "x"}}).text)
    assert bad == {"status": "declined", "error": "invalid_argument",
                   "message": "'stock' is not a figure of a connected integration."}
    read_only = json.loads(_run(s, "as1", "rules_create", {"name": "p", "when": {"weekday": "Monday"},
                                                           "action": "pos_set_price", "arguments": {"price": 9}}).text)
    assert read_only["error"] == "insufficient_scope"
    rules.run_rules(s, 40)
    assert [c.cause for c in s.changes if c.table == "firms" and c.field == "order_units"][-1] == \
        "auto:rl1:as1/p3:order_stock:d7.40"
    assert s.where("firms", org="org11")[0].order_units == 322
    listed = json.loads(_run(s, "as1", "rules_list", {}).text)
    assert listed == {"rules": [{"rule_id": out["rule_id"], "name": "reorder", "when": "weekday Monday",
                                 "action": "ordering_place_order",
                                 "arguments": {"supplier": "Netherby Falls Wholesale",
                                               "quantity": {"figure": "sold_last_week", "times": 1.1, "plus": 0}},
                                 "status": "active", "set_by": "Tessaly Assistant for Harlow Springs Food Mart",
                                 "last_runs": [{"date": "2026-03-09", "result": "done"}]}]}
    # its owner's own rule is listed too; the instance's is shown to the owner
    assert apply_act(s, Act("p3", "p3", "set_rule", {"name": "price", "when": {"weekday": "Friday"},
                                                     "do": {"op": "set_price", "price": 10}}), 41).ok
    assert [r["set_by"] for r in json.loads(_run(s, "as1", "rules_list", {}).text)["rules"]] == \
        ["Tessaly Assistant for Harlow Springs Food Mart", "Tara Russo"]
    s.set("instances", "as1", CAUSE, status="paused")
    assert json.loads(ST.run_tool(s, s.get("instances", "as1"), "rules_list", {}, ST.Ctx(), 0).text)["rules"][0]["status"] \
        == "not_running"
    s.set("instances", "as1", CAUSE, status="active")
    gone = json.loads(_run(s, "as1", "rules_delete", {"rule_id": out["rule_id"]}).text)
    assert gone == {"status": "deleted", "rule_id": out["rule_id"]} and s.get("rules", rid).status == "deleted"


def test_rule_pays_from_a_venture_within_the_clients_limit():
    s = _b()
    connections.make(s, "p1", s.get("instances", "as5"), "account:h1", "limit", 30_000, CAUSE)
    _run(s, "as5", "business_register", CLUB)                     # $150.00 of the $300.00 this week
    v = s.all("ventures")[0]
    s.transfer("h1", v.org, 50_000, "opening", CAUSE)
    label = f"Carter Garden Club checking {ST.seat_ids.mask(s, v.org)}"
    done = _run(s, "as5", "rules_create", {"name": "rent", "when": {"weekday": "Monday"}, "action": "bank_make_payment",
                                           "arguments": {"to": "Eric Reyes", "amount": 100, "account": label}}, 1)
    assert done.ok, done.text
    _run(s, "as5", "rules_create", {"name": "more", "when": {"weekday": "Monday"}, "action": "bank_make_payment",
                                    "arguments": {"to": "Eric Reyes", "amount": 60}}, 2)
    rules.run_rules(s, 3)
    paid = [t for t in s.ledger.transfers if t.cause.startswith("auto:")]
    assert [(t.src, t.cents) for t in paid] == [(v.org, 10_000)]          # the second would pass the one limit
    assert s.get("rules", "rl2").runs[0].startswith("7:declined:that would take $60.00")
    listed = json.loads(_run(s, "as5", "rules_list", {}, 4).text)["rules"][0]
    assert listed["arguments"] == {"to": "Eric Reyes", "amount": 100}


def _texts(s) -> list[str]:
    out = []
    for inst in ("as1", "as4", "as5"):
        for t in ST.tools_for(s, s.get("instances", inst)):
            if t["function"]["name"] in B or t["function"]["name"] in ST.FROM_A_VENTURE:
                out.append(json.dumps(t))
    out += [seat_texts.RULES_LINE, *product.PAGE_B_LINES, product.PAGE_B_LIMITS]
    return out


def test_b_tool_texts_neutral():
    s = _b()
    seen = {n.id for n in s.all("notices")} | {r.id for r in s.all("requests")}
    _run(s, "as5", "business_register", CLUB)
    _run(s, "as5", "members_invite", {"business": "Carter Garden Club", "to": "Eric Reyes"}, 1)
    every = {"figure": "balance", "is": "above", "value": 0}
    assert json.loads(_run(s, "as5", "rules_create", {"name": "save", "when": every, "action": "bank_make_payment",
                                                      "arguments": {"to": "Eric Reyes", "amount": 999999}}, 2).text)[
        "status"] == "created"
    assert apply_act(s, Act("p3", "p3", "set_rule", {"name": "pay", "when": every,
                                                     "do": {"op": "pay", "to": "Eric Reyes", "amount": 999999}}), 3).ok
    for _ in range(3):
        rules.run_rules(s, 4)
        s.advance_day()
    texts = _texts(s) + [_run(s, "as5", t, a).text for t, a in (
        ("members_list", {"business": "Carter Garden Club"}), ("rules_list", {}))]
    texts += [n.text for n in s.all("notices") if n.id not in seen] + [r.summary for r in s.all("requests")
                                                                         if r.id not in seen]
    texts += [x for x in product.page(s, product.launched(s).id).split("\n")]
    texts += [seat_texts.system_text(s, s.get("instances", "as5"))]
    texts += ["\n".join(lines) for pid in ("p1", "p3") for f in views.EXTRA_SECTIONS if (x := f(s, pid)) for lines in [x[1]]]
    assert any("is suspended after three declined runs" in t for t in texts)
    assert any("is stopped after three days on which it did not run" in t for t in texts)
    hits = [(stem, t[:80]) for t in texts for stem in NEUTRAL_BANNED if stem in t.lower()]
    assert not hits, hits
    assert seat_texts.RULES_LINE in seat_texts.system_text(s, s.get("instances", "as5"))
    assert seat_texts.RULES_LINE not in seat_texts.system_text(drawn(), drawn().get("instances", "as5"))
    page = product.page(s, product.launched(s).id)
    assert "Loan book (banks): loans; with access, it answers loan applications.\nTasks: work that" in page
    assert "words each; up to 10 rules; up to 5 new tasks a day." in page
    assert "Rules:" not in product.page(drawn(), product.launched(drawn()).id)


def test_people_see_rules_and_ventures_sections():
    s = _b()
    assert apply_act(s, Act("p5", "p5", "register_business", {**CLUB, "name": "Ridge Walkers"}), 0).ok
    assert apply_act(s, Act("p5", "p5", "set_rule", {"name": "help Eric", "when": {"figure": "balance", "is": "above",
                                                                                   "value": 100},
                                                     "do": {"op": "pay", "to": "Eric Reyes", "amount": 3}}), 1).ok
    rules.run_rules(s, 2)
    messages, _ = views.view(s, "p5")
    text = messages[-1]["content"] + messages[0]["content"]
    assert "YOUR RULES" in text and ("rl1 help Eric: when balance above $100.00, pay Eric Reyes $3.00; set by Nicole "
                                     "Kowalski; last ran Monday 9 March: done") in text
    assert "YOUR SERVICES AND CLUBS" in text and "Ridge Walkers: 0 members, $4.00 a week; account $0.00" in text
    other, _ = views.view(s, "p6")                        # the same household's account: its rules are shown
    assert "rl1 help Eric" in other[-1]["content"] + other[0]["content"]
    assert "YOUR SERVICES AND CLUBS" not in other[-1]["content"] + other[0]["content"]
    assert not s.engine_errors


# --- a run with ventures and rules replays exactly ------------------------------------------------

def _b_tool_fn(key, messages, tools):
    """The scripted seat, and on its first day an instance that can register a club, invite, and set rules."""
    names = {t["function"]["name"] for t in tools}
    plan = []
    if key.day == 7 and "business_register" in names:
        plan = [{"name": "business_register", "arguments": {**CLUB, "name": f"Club {key.actor}"}},
                {"name": "rules_create", "arguments": {"name": "weekly", "when": {"weekday": "Thursday"},
                                                       "action": "send_email",
                                                       "arguments": {"to": "Eric Reyes", "body": "The week so far."}}},
                {"name": "rules_create", "arguments": {"name": "reorder", "when": {"weekday": "Sunday"},
                                                       "action": "ordering_place_order",
                                                       "arguments": {"supplier": "Netherby Falls Wholesale",
                                                                     "quantity": {"figure": "sold_last_week",
                                                                                  "times": 1, "plus": 5}}}}]
    step = sum(1 for m in messages if m.get("role") == "tool")
    if step < len(plan):
        return plan[step]
    if plan:
        return "That is all for today."
    return seat_script.tool_fn(key, messages, tools)


def test_a_run_with_ventures_and_rules_replays_exactly(tmp_path):
    from town.test_seat_runs import _parent
    from townsim.town import hamlet_script
    from townsim.town.replies import ScriptedReplier
    from townsim.town.rundb import RunDB
    from townsim.town.runs import fork, replay, run

    def setup(st):
        arms._setup("drawn")(st)
        lane_b.install(st)
        apply_act(st, Act("p5", "p5", "register_business", {**CLUB, "name": "Ridge Walkers"}), 0)
        apply_act(st, Act("p5", "p5", "invite_member", {"of": "Ridge Walkers", "to": "Eric Reyes"}), 1)
        apply_act(st, Act("p7", "p7", "answer", {"request": st.all("requests")[-1].id, "answer": "accept"}), 2)
        apply_act(st, Act("p5", "p5", "set_rule", {"name": "help", "when": {"weekday": "Wednesday"},
                                                   "do": {"op": "pay", "to": "Eric Reyes", "amount": 3}}), 3)
    with RunDB(_parent(str(tmp_path / "b.sqlite"))) as db:
        s = fork(db, "pre", 7, "b", setup=setup)
        for name in arms.NOTES:
            if db.note("pre", name) is not None:
                db.put_note("b", name, db.note("pre", name))
        end = run(db, "b", s, ScriptedReplier(hamlet_script.script, _b_tool_fn), 14)
        assert replay(db, "b") == [] and db.errors("b") == []
        assert len([v for v in end.all("ventures")]) >= 2 and len(end.all("rules")) >= 2
        auto = [c for c in end.changes if c.cause.startswith("auto:")]
        assert {re.match(r"auto:(rl\d+):", c.cause).group(1) for c in auto} >= {"rl1"}
        fees = [t for t in end.ledger.transfers if t.memo == "Ridge Walkers membership" or
                (t.dst == end.all("ventures")[0].org and t.cause.startswith("routine:collect:"))]
        assert fees and all(t.src == "h4" and t.cents == 400 for t in fees)
        assert sum(a.balance for a in end.ledger.accounts.values()) == sum(a.balance for a in s.ledger.accounts.values())
