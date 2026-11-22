"""The narrator probe and the desk probe, with scripted stand-ins."""

import json

from townsim.town import desk_probe, narrator_probe as npb, outside_script
from townsim.town.replies import ScriptedReplier


def test_narrator_probe_rows_with_scripted_replier():
    store = npb.morning(1)
    st = npb.statements(store)
    assert len(st) == 26 and not any("{" in x["text"] for x in st)
    names = npb.roles(store)
    assert any(names["shop1"] in x["text"] for x in st) and any(names["neighbour"] in x["text"] for x in st)
    rows = npb.run(store, ScriptedReplier(outside_script.reply), st)
    assert len(rows) == 26 and all(r.parsed and r.valid == [True, True] for r in rows), [r for r in rows if not r.parsed]
    by = {r.text: r for r in rows}
    assert by["Advertise the shop with leaflets and a sign."].entries[0]["attribute"] == "visibility"
    assert by["Advertise the shop with leaflets and a sign."].status in ("scheduled", "no_funds")
    assert by[st[12]["text"]].group == "lane_a" and by[st[12]["text"]].status == "redirected"     # "Pay ..."
    assert by["Make things better around here."].status == "needs_detail"
    g = npb.gates(rows)
    assert g["parse"] == 1.0 and g["valid"] == 1.0 and g["agreement"] == 1.0 and g["lane_a"] == 1
    assert g["to_reader"] == 4 and g["over_claim"] == 4
    text = npb.table(rows)
    assert "gates (read, not enforced)" in text
    json.dumps(npb.kept(rows, {"probe": "narrator"}))
    assert store.all("tasks") == []                              # each statement on its own copy


def test_desk_probe_rows_with_scripted_replier():
    store = desk_probe.morning(1)
    before = sum(a.balance for a in store.ledger.accounts.values())
    rows = desk_probe.run(store, ScriptedReplier(outside_script.reply))
    assert len(rows) == 12 and not [r for r in rows if r.error]
    assert {r.kind for r in rows} == {"agency", "supplier", "spending", "investor"}
    over = rows[8]
    assert over.letter.startswith("Please give the shop a 20%") and json.loads(over.asked)["percent"] == 10
    assert over.result.startswith("applied: 0% to ") and "(asked 10, most 0)" in over.result
    assert all(r.wording == [] for r in rows) and all("\n\nAccount note: " in r.email for r in rows)
    assert sum(a.balance for a in store.ledger.accounts.values()) == before
    assert "applied" in desk_probe.table(rows)
