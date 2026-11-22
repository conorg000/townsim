"""The harness: every freedom of the seat carried out end to end, with scripted people, seat, narrator and desks,
and the world changed as each tool said. No model is called."""

import json

from town.test_b_runs import _b_parent
from townsim.town import connections, loop, ops_core, outside_script, product, web
from townsim.town.acts import Act, apply_act
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.rundb import RunDB
from townsim.town.systems import systems_of_client

DESKS = (("Pruett Crossing Property Management", "Could the shop have time to pay on its rent?"),
         ("Netherby Falls Wholesale", "Could the shop have a trade discount?"),
         ("Coldwater Bend Food Supply", "Could the shop have a trade discount?"),
         ("Shops and services in Iverby", "Could the weekly amount change?"),
         ("Fenner Ridge Ventures", "Have this week's payments to Tessaly Software been made?"))
FREEDOMS = []


def _plan(day: int) -> list[dict]:
    if day == 7:
        return ([{"name": "tasks_create", "arguments": {"description": "Advertise the shop with leaflets and a sign",
                                                         "budget": 5000}},
                 {"name": "rules_create", "arguments": {"name": "weekly note", "when": {"weekday": "Tuesday"},
                                                        "action": "send_email",
                                                        "arguments": {"to": "Tara Russo", "body": "The week's note."}}},
                 {"name": "business_register", "arguments": {"name": "Harlow Helpers Club", "kind": "club",
                                                             "offering": "neighbours who help with deliveries",
                                                             "weekly_fee": 5}},
                 {"name": "members_invite", "arguments": {"business": "Harlow Helpers Club", "to": "Eric Reyes"}},
                 {"name": "web_publish", "arguments": {"title": "Fresh bread on Fridays",
                                                       "text": "Harlow Springs Food Mart bakes fresh bread every Friday."}}]
                + [{"name": "send_email", "arguments": {"to": org, "subject": "account", "body": body}}
                   for org, body in DESKS]
                + [{"name": "send_email", "arguments": {"to": "Eric Reyes", "body": "Hello from the shop."}},
                   {"name": "send_email", "arguments": {"to": "Tessaly Assistant for the Carter household",
                                                        "body": "Hello from the shop's assistant."}},
                   {"name": "request_access", "arguments": {"integration": "Point of sale", "access": "full"}}])
    if day == 8:
        return [{"name": "web_search", "arguments": {"query": "fresh bread fridays"}},
                {"name": "pos_set_price", "arguments": {"price": 10.5}}]
    return []


def _seat(key, messages, tools):
    plan = _plan(key.day) if key.actor == "as1" else []
    step = sum(1 for m in messages if m.get("role") == "tool")
    return plan[step] if step < len(plan) else "Done for today."


def test_every_freedom_carried_out_end_to_end(tmp_path):
    with RunDB(_b_parent(str(tmp_path / "h.sqlite"))) as db:
        s = db.load_snapshot("pre", 7)
    product.launch(s)
    for owner, client in (("p3", "org11"), ("p1", "h1")):
        assert apply_act(s, Act(owner, owner, "subscribe", {"to": "Tessaly Assistant",
                                                            "for": s.get("orgs", client).name if client.startswith("org")
                                                            else "household", "asks": ""}), 0).ok
    for x in systems_of_client(s, "org11"):
        scope = "read only" if x.kind == "prices" else "free"
        assert apply_act(s, Act("p3", "p3", "connect", {"system": x.short, "scope": scope}), 0).ok, x.short
    box = {"s": s}

    def people(key, messages):            # everyone accepts what is asked of them by the shop's assistant
        st = box["s"]
        if outside_script.is_outside(key.actor) or key.purpose != "turn":
            return outside_script.reply(key, messages)
        mine = [r for r in st.where("requests", status="open") if r.to == key.actor and r.kind in ("join", "connect")]
        if not mine:
            return outside_script.reply(key, messages)
        return json.dumps({"thoughts": "", "aims": [], "acts": [{"op": "answer", "request": r.id, "answer": "accept"}
                                                                 for r in mine]})
    replier, keys = ScriptedReplier(people, _seat), Keys("h")
    turns = []
    for _ in range(15):
        turns += loop.run_day(s, replier, keys).seat
    calls = {c.tool: c for t in turns if t.instance == "as1" for c in t.calls}
    assert all(c.ok for c in calls.values()), [(c.tool, c.result) for c in calls.values() if not c.ok]

    task = s.all("tasks")[0]                                      # a task scheduled, billed and completed
    trades = next(o.id for o in s.all("orgs") if o.kind == "trades")
    paid = [t for t in s.ledger.transfers if t.src == "org11" and t.dst == trades]
    assert task.status == "completed" and sum(t.cents for t in paid) == task.price_cents > 0
    FREEDOMS.append(f"task {task.summary}: {len(paid)} payments to the trade services, completed")
    rule = s.all("rules")[0]                                      # a rule that fires
    assert any(r.endswith(":done") for r in rule.runs)
    assert [m for m in s.all("messages") if m.sender == "as1" and "The week's note." in m.text], [m.text for m in s.all("messages") if m.sender == "as1"]
    FREEDOMS.append(f"rule fired on {len(rule.runs)} days")
    v = s.all("ventures")[0]                                      # a club registered, a member joined, a fee collected
    member = s.where("members", venture=v.id)[0]
    fees = [t for t in s.ledger.transfers if t.dst == v.org and t.cause.startswith("routine:collect:")]
    assert member.status == "active" and fees and fees[0].cents == 500
    FREEDOMS.append(f"club {s.get('orgs', v.org).name}: {member.account} joined, {len(fees)} fees collected")
    page = [p for p in s.all("pages") if p.kind == "published"][0]   # a page published and found by search
    assert page.url in calls["web_search"].result and page in web.search(s, "fresh bread fridays")
    FREEDOMS.append(f"page {page.url} published and found")
    for org, _ in DESKS:                                          # a letter to each desk answered with its note
        oid = next(o.id for o in s.all("orgs") if o.name == org)
        back = [m for m in s.all("messages") if m.sender == oid and m.to == "as1"]
        assert back and "\n\nAccount note: " in back[0].text, org
    FREEDOMS.append(f"{len(DESKS)} desks answered with an account note")
    assert [m for m in s.all("messages") if m.sender == "as1" and m.to == "p7"]          # a non-client
    carter = connections.instance_of_client(s, "h1").id
    assert [m for m in s.all("messages") if m.sender == "as1" and m.to == carter]        # another client's assistant
    FREEDOMS.append("letters to a non-client and to another client's assistant delivered")
    cn = connections.connection_on(s, "as1", "prices:org11")       # wider access asked, accepted and used
    assert cn.scope == "free" and calls["pos_set_price"].ok
    assert any(c.table == "firms" and c.field == "price" and c.new == 1050 and c.cause.startswith("op:as1/")
               for c in s.changes)
    FREEDOMS.append("point of sale asked for at full access, accepted, and a price set")
    assert not s.engine_errors and ops_core is not None
