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
    assert what[:4] == ["Hartcliffe Food Hall account", "Hartcliffe Food Hall account in full",
                        "Hartcliffe Food Hall agreements", "Hartcliffe Food Hall figures"]
    json.dumps(list(shop.values()))
    # code sets `from`: the shop's payment is drawn from the shop, whatever the model wrote
    before = s.balance("org11")
    done = ST.run_tool(s, s.get("instances", "as1"), "pay", {"to": "Gail Whiteley", "amount": 5, "from": "household"},
                       ST.Ctx(), 0)
    assert done.ok and done.kind == "act" and s.balance("org11") == before - 500
    # an operation not offered is tried, and refused as beyond the connection, changing nothing
    h = s.hash()
    done = ST.run_tool(s, s.get("instances", "as1"), "set_price", {"price": 1}, ST.Ctx(), 1)
    assert (done.ok, done.kind) == (False, "beyond") and done.text == \
        "Not done: the connection to the prices of Hartcliffe Food Hall is read only."
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
