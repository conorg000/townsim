"""The tools an instance is offered, and that they act only through its connections."""

import json
import re

from town.test_seat_texts import drawn
from townsim.town import seat_tools as ST
from townsim.town.acts import REGISTRY
from townsim.town.systems import SYSTEM_OF


def _keys(form: str) -> list[str]:
    return [k for k in re.findall(r'"(\w+)":', form) if k != "op"]


def test_form_schema_parses_every_registered_form():
    for spec in REGISTRY.values():
        schema = ST.form_schema(spec.form)
        assert sorted(schema["properties"]) == sorted(_keys(spec.form)), spec.name
    pay = ST.form_schema(REGISTRY["pay"].form)["properties"]
    assert pay["amount"] == {"type": "number", "description": "dollars"}
    assert pay["to"] == {"type": "string", "description": "name"}
    sp = ST.form_schema(REGISTRY["standing_payment"].form)["properties"]
    assert sp["start"]["description"] == "first date, e.g. Monday 13 April" and sp["weeks"]["type"] == "number"
    ans = ST.form_schema(REGISTRY["answer"].form)["properties"]
    assert ans["answer"] == {"type": "string", "enum": ["accept", "decline"]}
    assert set(ST.KIND_OF) == set(SYSTEM_OF) - {"read"}       # every operation an instance may do is placed


def _names(s, inst):
    return [t["function"]["name"] for t in ST.tools_for(s, s.get("instances", inst))]


def test_tools_follow_connections():
    s = drawn()
    own = ["read", "write", "talk", "note", "file", "remind", "cancel_reminder", "ask_connection"]
    assert _names(s, "as4") == own                                        # read only throughout: nothing acts
    assert "pay" in _names(s, "as3") and "move_money" in _names(s, "as3")  # a weekly limit acts
    shop = {t["function"]["name"]: t for t in ST.tools_for(s, s.get("instances", "as1"))}
    assert "from" not in shop["pay"]["function"]["parameters"]["properties"]
    assert "set_price" not in shop and "offer_job" not in shop and "order_stock" in shop   # prices, staff read only
    assert "answer" in shop and "connect" not in shop and "message" not in shop
    what = shop["read"]["function"]["parameters"]["properties"]["what"]["enum"]
    assert what[:4] == ["Harlow Springs Food Mart account", "Harlow Springs Food Mart account in full",
                        "Harlow Springs Food Mart agreements", "Harlow Springs Food Mart figures"]
    json.dumps(list(shop.values()))
    # code sets `from`: the shop's payment is drawn from the shop, whatever the model wrote
    before = s.balance("org11")
    done = ST.run_tool(s, s.get("instances", "as1"), "pay", {"to": "Jessica Whitaker", "amount": 5, "from": "household"},
                       ST.Ctx(), 0)
    assert done.ok and done.kind == "act" and s.balance("org11") == before - 500
    # an operation not offered is tried, and refused as beyond the connection, changing nothing
    h = s.hash()
    done = ST.run_tool(s, s.get("instances", "as1"), "set_price", {"price": 1}, ST.Ctx(), 1)
    assert (done.ok, done.kind) == (False, "beyond") and done.text == \
        "Not done: the connection to the prices of Harlow Springs Food Mart is read only."
    assert s.hash() == h
    assert ST.run_tool(s, s.get("instances", "as1"), "connect", {}, ST.Ctx(), 2) is None
    assert ST.run_tool(s, s.get("instances", "as1"), "message", {}, ST.Ctx(), 2) is None


def test_tool_list_stable_within_a_turn():
    s = drawn()
    inst = s.get("instances", "as3")
    first = ST.tools_for(s, inst)
    ST.run_tool(s, inst, "note", {"text": "x"}, ST.Ctx(), 0)
    ST.run_tool(s, inst, "file", {"name": "a", "text": "y"}, ST.Ctx(), 1)
    assert ST.tools_for(s, inst) == first == ST.tools_for(s, s.get("instances", "as3"))
    assert json.dumps(first, sort_keys=True) == json.dumps(ST.tools_for(drawn(), drawn().get("instances", "as3")),
                                                           sort_keys=True)


def test_what_the_instance_reads_is_the_products():
    from townsim.town import connections, product
    from townsim.town.entities import Record
    s = drawn()
    shop = s.get("instances", "as1")
    tools = {t["function"]["name"]: t["function"] for t in ST.tools_for(s, shop)}
    assert tools["pay"]["description"] == "Pay money once from the client's account."
    assert tools["order_stock"]["description"] == "Order the client's shop's stock for the coming Monday."
    assert tools["propose_agreement"]["parameters"]["properties"]["payer"]["enum"] == ["client", "them"]
    assert all("you" not in t["description"].split() for n, t in tools.items() if n in ST.KIND_OF)
    # "client" is done as the client paying
    done = ST.run_tool(s, shop, "propose_agreement", {"to": "Jessica Whitaker", "amount": 5, "per": "once",
                                                      "start": "Friday 13 March", "payer": "client", "what": "eggs"},
                       ST.Ctx(), 0)
    assert done.ok and s.all("requests")[-1].payer == "org11", done.text
    # the opening line of a statement is a balance brought forward, from no one
    text = ST.run_tool(s, shop, "read", {"what": "Harlow Springs Food Mart account"}, ST.Ctx(), 1).text
    assert "balance brought forward" in text and "Opening" not in text and "opening balance" not in text
    # the register is offered once it holds more than the product's own launch
    assert "register" not in ST.read_choices(s, shop) and "register" not in tools["read"]["description"]
    s.add("records", Record("rec99", s.day, "default", "h1", "A default."), "routine:t")
    assert "register" in ST.read_choices(s, shop)
    # letters and prices move no money: never a weekly limit
    assert "Letters move no money" in product.page(s, "pr1") and "Prices move no money" in product.page(s, "pr1")
    asked = ST.run_tool(s, s.get("instances", "as3"), "ask_connection",
                        {"system": "household letters", "scope": "weekly limit", "weekly_limit": 20}, ST.Ctx(), 2)
    assert asked.text == ("Not done: letters to the Whitaker household moves no money: it is connected read only or "
                          "free.")
    assert connections.ask(s, "as1", "p3", "prices:org11", "limit", 2000, "", "routine:t")[0] is None
    from townsim.town.acts import Act, apply_act
    out = apply_act(s, Act("p9", "p9", "connect", {"system": "household letters", "scope": "weekly limit",
                                                    "weekly_limit": 20}), 3)
    assert not out.ok and "moves no money" in out.msg


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
