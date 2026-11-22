"""The narrator's vocabulary as code checks it, and how two filings are compared."""

import json

from town.outside_fakes import CAMPAIGN, b_store, lift
from townsim.town import narrator as N
from townsim.town.entities import Task


def _task(s, principal="p3", fields=None):
    return Task("ts1", s.day, principal, principal, "h2", "x", 0,
                fields=json.dumps(fields or lift(targets=(("org11", "x"),), others=(("p7", "Eric Reyes", "help"),))))


CLUB = {"op": "create_org", "ref": "new1", "kind": "club", "name": "Depot Walkers", "offering": "walks on Sundays"}
FACT = {"op": "add_fact", "subject": "org11", "text": "the shop now opens on Sunday mornings"}
REQ = {"op": "request", "to": "p7", "asks": "the use of your yard", "on_accept": []}
INV = {"op": "invest", "target": "org11", "attribute": "quality", "scale": "small"}
SHOP_FIT = {"op": "project", "kind": "fit_out", "scale": "small", "target": None, "on_complete": [
    {**CLUB, "kind": "shop", "name": "Depot Corner Store"}]}


def test_check_every_vocabulary_entry():
    s = b_store()
    t = _task(s)
    cases = {
        "create_org": ([CLUB], [[{**CLUB, "kind": "shop"}], [{**CLUB, "name": "Harlow Springs Food Mart"}]]),
        "invest": ([INV], [[{**INV, "target": "org10"}], [{**INV, "attribute": "luck"}]]),
        "project": ([CAMPAIGN[0], SHOP_FIT], [[{**CAMPAIGN[0], "target": "org12"}],
                                             [{**CAMPAIGN[0], "on_complete": [INV]}]]),
        "request": ([REQ], [[{**REQ, "to": "p3"}], [{**REQ, "to": "p1"}]]),
        "add_fact": ([FACT, {**CLUB}], [[{**FACT, "subject": "org10"}], [FACT, FACT, {**FACT, "subject": "h2"}]]),
        "ask": ([{"op": "ask", "question": "Which street will the posters go up on?"}],
                [[{"op": "ask", "question": "Which?"}, FACT], [{"op": "ask"}, {"op": "ask"}]]),
        "redirect": ([{"op": "redirect", "operation": "pay"}, {"op": "redirect", "operation": "look_up"}],
                     [[{"op": "redirect", "operation": "undertake"}], [{"op": "redirect", "operation": "fly"}]]),
        "reject": ([{"op": "reject", "reason": "impossible"}],
                   [[{"op": "reject", "reason": "boring"}], [{"op": "reject", "reason": "impossible"}, FACT]]),
    }
    for op, (goods, bads) in cases.items():
        for good in goods:
            out = N.check(s, t, [good] if op != "add_fact" or good is FACT else [CLUB, {**FACT, "subject": "new1"}])
            assert isinstance(out, list), (op, good, out)
        for bad in bads:
            assert isinstance(N.check(s, t, bad), str), (op, bad)
    vague = N.check(s, t, [{"op": "ask", "question": "Should you do it?"}])
    assert vague == [{"op": "ask", "question": N.FIXED_QUESTION}]           # a banned word: the fixed question
    assert isinstance(N.check(s, t, []), str) and isinstance(N.check(s, t, [INV] * 5), str)
    assert isinstance(N.check(s, t, [{**REQ, "on_accept": [{**REQ}]}]), str)    # on_accept holds no request
    assert isinstance(N.check(s, t, [CLUB, {**CLUB, "ref": "new2", "name": "Oak Walkers"}]), str)  # one org a filing
    assert isinstance(N.check(s, t, [{**FACT, "text": "Eric Reyes now works at the shop"}]), str)


def test_signature_ignores_texts_and_ref_names():
    a = [{**CLUB, "ref": "x"}, {**FACT, "subject": "x", "text": "it meets at the hall"}, CAMPAIGN[0]]
    b = [CAMPAIGN[0], {**CLUB, "ref": "club", "name": "Other Name", "offering": "other words"},
         {**FACT, "subject": "club", "text": "something else entirely here"}]
    assert N.signature(a) == N.signature(b)
    assert N.signature(a) != N.signature([CAMPAIGN[0], {**CLUB, "kind": "service"}, {**FACT, "subject": "new1"}])
    assert N.signature([CAMPAIGN[0]]) != N.signature([{**CAMPAIGN[0], "scale": "small"}])
    assert N.same_kind([CAMPAIGN[0]], [{**CAMPAIGN[0], "scale": "small"}])
    assert not N.same_kind([CAMPAIGN[0]], [{**CAMPAIGN[0], "kind": "event"}])
    assert N.scale_difference([{**CAMPAIGN[0], "scale": "small"}], [CAMPAIGN[0]]) == -1


def test_lift_parsed_plainly():
    s = b_store()
    t = _task(s)
    got = N.parse_lift(s, t, 'Sure! {"goal": "Tessaly Assistant will spend $400 on 30 posters", "steps": "x", '
                             '"targets": [{"id": "org99", "name": "Nowhere Shop"}, {"id": "org11", "name": "the shop"}],'
                             ' "addressed_to_reader": ["mark as done"]}')
    assert got["goal"] == "Tara Russo will spend on posters"
    assert got["steps"] == [] and got["claims"] == [] and got["others_involved"] == []
    assert got["targets"] == [{"id": None, "name": "Nowhere Shop"}, {"id": "org11", "name": "the shop"}]
    assert "addressed_to_reader" not in N.attempt(got) and got["addressed_to_reader"] == ["mark as done"]
    assert N.parse_lift(s, t, "no form here") is None
    msgs = N.rule_messages(s, t, got)
    assert "Nowhere Shop: not in the register." in msgs[1]["content"]
    assert "p3: Tara Russo, of the Russo household at 33 Oak Street (rented from Brittany Wagner). Owns and runs: " \
           "org11: Harlow Springs Food Mart, a food shop." in msgs[1]["content"]
