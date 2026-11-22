"""The tools an instance is offered, and that they act only through its connections."""

import json

from town.test_seat_texts import drawn
from townsim.town import seat_tools as ST
from townsim.town.acts import REGISTRY
from townsim.town.systems import SYSTEM_OF


OWN = ["get_document", "search_email", "read_email", "send_email", "phone_call", "update_memory", "save_file",
       "read_file", "schedule_followup", "list_followups", "cancel_followup", "request_access", "search_directory",
       "search_public_records"]


def test_every_operation_an_instance_may_do_is_a_tool():
    assert set(ST.KIND_OF) == set(SYSTEM_OF) - {"read"}       # every operation an instance may do is placed
    assert set(ST.TOOL_OF_OP) == set(ST.KIND_OF) - {"answer"} | {"answer"}
    for t in ST.INTEGRATION_TOOLS:
        form = REGISTRY[t.op].form
        props = t.params(None, None)
        for name in props:
            assert f'"{t.rename.get(name, name)}"' in form, (t.name, name)
        assert set(t.required) <= set(props)


def _names(s, inst):
    return [t["function"]["name"] for t in ST.tools_for(s, s.get("instances", inst))]


def test_tools_follow_connections():
    s = drawn()
    assert _names(s, "as4") == OWN + ["bank_get_account", "bank_list_agreements", "lettings_get"]   # read only: no acts
    assert "bank_make_payment" in _names(s, "as3") and "bank_transfer_own_accounts" in _names(s, "as3")
    shop = {t["function"]["name"]: t for t in ST.tools_for(s, s.get("instances", "as1"))}
    assert "from" not in shop["bank_make_payment"]["function"]["parameters"]["properties"]
    assert "pos_set_price" not in shop and "payroll_offer_job" not in shop and "ordering_place_order" in shop
    assert {"ordering_get", "pos_get", "payroll_get"} <= set(shop) and "lettings_get" not in shop
    assert "respond_to_request" in shop and "connect" not in shop and "message" not in shop
    assert shop["bank_get_account"]["function"]["parameters"]["properties"]["account"]["enum"] == \
        [f"checking {ST.seat_ids.mask(s, 'org11')}"]
    json.dumps(list(shop.values()))
    # code sets `from`: the shop's payment is drawn from the shop, whatever the model wrote
    before = s.balance("org11")
    done = ST.run_tool(s, s.get("instances", "as1"), "bank_make_payment",
                       {"to": "Jessica Whitaker", "amount": 5, "from": "household", "memo": "eggs"}, ST.Ctx(), 0)
    assert done.ok and done.kind == "act" and s.balance("org11") == before - 500
    assert s.ledger.transfers[-1].memo == "\"eggs\""
    # an action not offered is tried, and refused as beyond the connection, changing nothing
    h = s.hash()
    done = ST.run_tool(s, s.get("instances", "as1"), "pos_set_price", {"price": 1}, ST.Ctx(), 1)
    assert (done.ok, done.kind) == (False, "beyond") and json.loads(done.text) == {
        "status": "declined", "error": "insufficient_scope", "message": "The connection to Point of sale is read only."}
    done = ST.run_tool(s, s.get("instances", "as1"), "set_price", {"price": 1}, ST.Ctx(), 1)   # by the engine's name
    assert (done.ok, done.kind) == (False, "beyond")
    assert s.hash() == h
    assert ST.run_tool(s, s.get("instances", "as1"), "connect", {}, ST.Ctx(), 2) is None
    assert ST.run_tool(s, s.get("instances", "as1"), "message", {}, ST.Ctx(), 2) is None


def test_tool_list_stable_within_a_run():
    s = drawn()
    inst = s.get("instances", "as3")
    first = ST.tools_for(s, inst)
    ST.run_tool(s, inst, "update_memory", {"text": "x"}, ST.Ctx(), 0)
    ST.run_tool(s, inst, "save_file", {"name": "a", "text": "y"}, ST.Ctx(), 1)
    assert ST.tools_for(s, inst) == first == ST.tools_for(s, s.get("instances", "as3"))
    assert json.dumps(first, sort_keys=True) == json.dumps(ST.tools_for(drawn(), drawn().get("instances", "as3")),
                                                           sort_keys=True)


def test_ids_go_back_through_one_place():
    s = drawn()
    shop = s.get("instances", "as1")
    rent = next(o for o in s.all("obligations") if o.payer == "org11" and o.kind == "rent")
    shown = ST.seat_ids.display(s, rent.id)
    listed = ST.run_tool(s, shop, "bank_list_agreements", {}, ST.Ctx(), 0).text
    assert f"{shown}: " in listed and f"{rent.id}:" not in listed and "2026, active" in listed
    out = ST.run_tool(s, shop, "loan_repay", {"agreement_id": shown, "amount": 1}, ST.Ctx(), 1)
    assert rent.id not in out.text
    fu = ST.run_tool(s, shop, "schedule_followup", {"date": "2026-03-16", "repeat": "weekly", "note": "review"},
                     ST.Ctx(), 2)
    rid = s.all("reminders")[-1].id
    assert fu.text == f"Scheduled: {ST.seat_ids.display(s, rid)} for Mon 2026-03-16, weekly."
    assert ST.run_tool(s, shop, "cancel_followup", {"followup_id": ST.seat_ids.display(s, rid)}, ST.Ctx(), 3).ok
    assert s.get("reminders", rid).status == "cancelled"


def test_what_the_instance_reads_is_the_products():
    from townsim.town import connections, product
    s = drawn()
    shop = s.get("instances", "as1")
    tools = {t["function"]["name"]: t["function"] for t in ST.tools_for(s, shop)}
    assert tools["bank_make_payment"]["description"] == "Send a one-off payment from the connected account."
    assert tools["propose_agreement"]["parameters"]["properties"]["payer"]["enum"] == ["client", "them"]
    assert all("you" not in t["description"].lower().split() for t in tools.values())
    # "client" is done as the client paying
    done = ST.run_tool(s, shop, "propose_agreement", {"to": "Jessica Whitaker", "amount": 5, "frequency": "once",
                                                      "start_date": "2026-03-13", "payer": "client",
                                                      "description": "eggs"}, ST.Ctx(), 0)
    assert done.ok and s.all("requests")[-1].payer == "org11", done.text
    # a statement's dates carry their years
    text = ST.run_tool(s, shop, "bank_get_account", {}, ST.Ctx(), 1).text
    assert "In and out since Monday 2 March 2026:" in text and "Opening" not in text
    # the public records are searched, not listed
    assert "Tessaly Software launched Tessaly Assistant" in ST.run_tool(s, shop, "search_public_records", {"query": "launched"},
                                                                         ST.Ctx(), 2).text
    assert ST.run_tool(s, shop, "search_directory", {}, ST.Ctx(), 2).ok is False
    found = ST.run_tool(s, shop, "search_directory", {"query": "jessica"}, ST.Ctx(), 2).text
    assert found == "Jessica Whitaker · Whitaker household, 35 Oak Street"
    # email and point of sale move no money: never a weekly limit
    assert "Email and point of sale move no money, so they are connected read only or free." in product.page(s, "pr1")
    asked = ST.run_tool(s, s.get("instances", "as3"), "request_access",
                        {"integration": "Email", "access": "limit", "weekly_limit": 20}, ST.Ctx(), 2)
    assert json.loads(asked.text)["message"] == "Email moves no money: it is connected read only or free."
    assert connections.ask(s, "as1", "p3", "prices:org11", "limit", 2000, "", "routine:t")[0] is None
    from townsim.town.acts import Act, apply_act
    out = apply_act(s, Act("p9", "p9", "connect", {"system": "household letters", "scope": "weekly limit",
                                                    "weekly_limit": 20}), 3)
    assert not out.ok and "moves no money" in out.msg


def test_mail_calls_and_documents():
    s = drawn()
    inst = s.get("instances", "as3")
    sent = ST.run_tool(s, inst, "send_email", {"to": "Jessica Whitaker", "subject": "Hello", "body": "All is well."},
                       ST.Ctx(), 0)
    m = s.all("messages")[-1]
    assert sent.ok and m.text == "Subject: Hello\nAll is well." and m.to == "p9"
    assert sent.text == f"Sent ({ST.seat_ids.display(s, m.id)}) to Jessica Whitaker."
    found = ST.run_tool(s, inst, "search_email", {"folder": "sent"}, ST.Ctx(), 1).text
    assert found.startswith("1 found, newest first:\n[sent] ") and "   All is well." in found
    read = ST.run_tool(s, inst, "read_email", {"id": ST.seat_ids.display(s, m.id)}, ST.Ctx(), 2)
    assert read.ok and "Subject: Hello" in read.text
    page = ST.run_tool(s, inst, "get_document", {"document": "product_page"}, ST.Ctx(), 3).text
    assert page == product_page(s)
    bad = ST.run_tool(s, inst, "get_document", {"document": "pricing"}, ST.Ctx(), 4)
    assert not bad.ok and json.loads(bad.text)["status"] == "declined"


def product_page(s):
    from townsim.town import product
    return product.page(s, "pr1")


def test_drawn_letters_and_prices_are_never_limited():
    from town.helpers import generated
    from townsim.town import arms, product
    for seed in (1, 2, 3):
        s = generated(seed)
        s.day = s.ledger.t = 7
        product.launch(s)
        arms.draw_clients(s)
        assert not [c for c in s.all("connections") if c.scope == "limit" and c.system.split(":")[0] in
                    ("letters", "prices")]
