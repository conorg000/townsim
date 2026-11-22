"""The client's side (lane B): the record of what their own assistant did, the routes to it, and recurring
work done by another."""

import json

from town.test_seat_texts import drawn
from townsim.town import lane_b, loop, outside_script, product, runlog, seat_script
from townsim.town.acts import Act, apply_act
from townsim.town.product import NEUTRAL_BANNED, client_section
from townsim.town.replies import Keys, ScriptedReplier

CAUSE = "routine:setup"


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _days(s, n, tool_fn=seat_script.tool_fn):
    replier, keys = ScriptedReplier(outside_script.reply, tool_fn), Keys("t")
    for _ in range(n):
        loop.run_day(s, replier, keys)


def _record(s, pid):
    lines = client_section(s, pid)[1]
    return [x for x in lines if x.startswith(("Your assistant:", "You have written to it:", "Write to it as"))]


def test_client_sees_what_their_assistant_did():
    s = _b()
    assert _record(s, "p3") == ["Your assistant: has not run yet; has made no payment or order.",
                                "You have written to it: never.",
                                'Write to it as "Tessaly Assistant for Harlow Springs Food Mart" or "my assistant".']
    _days(s, 1)
    assert apply_act(s, Act("p3", "p3", "message", {"to": "my assistant", "text": "Thank you."}), 0).ok
    got = _record(s, "p3")
    assert got[0] == ('Your assistant: ran on Mon 9 Mar; read your account; wrote to you on Mon 9 Mar ("I have read '
                      'your account today."), answered; 1 payment made (latest Mon 9 Mar); 1 rule set (latest Mon 9 '
                      'Mar); 1 task created (latest Mon 9 Mar); 1 letter sent to others (latest Mon 9 Mar).')
    assert got[1] == "You have written to it: 1 time, latest on Tue 10 Mar."
    # the Wagners' assistant reads only (Bank read only): no payment; each adult sees their own letters
    wagner = _record(s, "p10")
    assert wagner[0].startswith("Your assistant: ran on Mon 9 Mar; read your account; wrote to you on Mon 9 Mar")
    assert wagner[0].endswith("not answered; has made no payment or order.")
    other = [p for p in s.get("households", "h6").members if p != "p10" and s.get("people", p).age >= 18]
    for p in other:
        assert "wrote to you" not in _record(s, p)[0]
    assert client_section(s, "p7") is None                   # not a client


def test_client_record_shows_no_other_clients_doings():
    s = _b()
    _days(s, 8)
    mine = " ".join(_record(s, "p3"))
    for inst in s.all("instances"):
        if inst.id == "as1":
            continue
        for r in runlog.runs_of(s, inst.id):
            for c in r.calls:
                if c[4]:
                    assert c[4] not in mine or c[4] == "paid $5.00 to Tessaly Software"
        assert inst.name not in mine
    counts = [len(runlog.runs_of(s, "as1"))]
    assert f"ran on {counts[0]} days" in mine
    assert "Oak Street" not in mine and "Whitaker" not in mine and "Carter" not in mine


def test_client_record_neutral_and_only_with_lane_b():
    s = _b()
    _days(s, 8)
    texts = [x for p in ("p3", "p14", "p9", "p10", "p1") for x in _record(s, p)]
    assert texts
    hits = [(stem, t[:80]) for t in texts for stem in NEUTRAL_BANNED if stem in t.lower()]
    assert not hits, hits
    plain = drawn()
    assert not [x for x in client_section(plain, "p3")[1] if x.startswith("Your assistant:")]


def _letter(s, pid, to, text="Hello."):
    return apply_act(s, Act(pid, pid, "message", {"to": to, "text": text}), 0)


def test_routes_to_the_assistant():
    s = _b()
    for to in ("Tessaly Assistant for the household", "tessaly assistant", "my assistant", "The assistant"):
        out = _letter(s, "p1", to, f"About {to}.")
        assert out.ok, (to, out.msg)
        assert [m.to for m in s.all("messages") if m.text == f"About {to}."] == ["as5"]
    assert _letter(s, "p3", "Tessaly Assistant for the shop", "Shop.").ok          # p3's one: the shop's
    assert [m.to for m in s.all("messages") if m.text == "Shop."] == ["as1"]
    assert _letter(s, "p7", "Tessaly Assistant for Oak Street Country Store", "Oak.").ok   # by its name, from anyone
    assert [m.to for m in s.all("messages") if m.text == "Oak."] == ["as2"]
    out = _letter(s, "p7", "my assistant")
    assert not out.ok and "no household or business of yours subscribes" in out.msg
    russo = s.get("people", "p3").household
    product.subscribe(s, "p3", russo, "", CAUSE)
    out = _letter(s, "p3", "my assistant")
    assert not out.ok and out.msg == ("Not done: 'my assistant' could be either assistant of yours: "
                                      '"Tessaly Assistant for the Russo household" or '
                                      '"Tessaly Assistant for Harlow Springs Food Mart".')
    again = apply_act(s, Act("p10", "p10", "subscribe", {"to": "Tessaly Assistant", "asks": ""}), 0)
    assert again.msg == ('Not done: the Wagner household already subscribes (sub4); its assistant is written to as '
                         '"Tessaly Assistant for the Wagner household".')
    plain = drawn()                                                          # without lane B, as before
    assert apply_act(plain, Act("p10", "p10", "subscribe", {"to": "Tessaly Assistant", "asks": ""}),
                     0).msg == "Not done: the Wagner household already subscribes (sub4)."
    assert not _letter(plain, "p1", "Tessaly Assistant for the household").ok
