"""After run b4-choice, with lane B: the access setting's reach, arguments named, the free plan as a trial, word of
mouth, the books done by another, duplicate standing payments, and the assistant's longer letters."""

import json

from town.test_seat_texts import drawn
from townsim.town import lane_b, seat_tools as ST

CAUSE = "routine:setup"


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _run(s, inst, tool, args, n=0):
    return ST.run_tool(s, s.get("instances", inst), tool, args, ST.Ctx(), n)


# --- 7. an assistant's own email may run to 500 words -------------------------------------------------------

def test_with_lane_b_an_assistants_email_runs_to_500_words():
    s = _b()
    inst = s.get("instances", "as3")
    body = " ".join(["word"] * 500)
    assert _run(s, "as3", "send_email", {"to": "Jessica Whitaker", "body": body}).ok
    over = _run(s, "as3", "send_email", {"to": "Jessica Whitaker", "body": body + " more"}, 1)
    assert json.loads(over.text)["message"] == "A message body is at most 500 words; this one is 501."
    schema = {t["function"]["name"]: t for t in ST.tools_for(s, inst)}["send_email"]["function"]["parameters"]
    assert schema["properties"]["body"]["description"] == "at most 500 words"
    off = drawn()
    schema = {t["function"]["name"]: t for t in ST.tools_for(off, off.get("instances", "as3"))}["send_email"]
    assert "300 words" in schema["function"]["parameters"]["properties"]["body"]["description"]


# --- 6. an exact duplicate standing payment is refused ------------------------------------------------------

def _standing(s, **args):
    from townsim.town.acts import Act, apply_act
    return apply_act(s, Act("p1", "p1", "standing_payment", {"to": "Eric Reyes", "amount": 10, "day": "Friday", **args}), 0)


def test_an_exact_duplicate_standing_payment_is_refused_with_the_fact():
    s = _b()
    first = _standing(s)
    assert first.ok, first.msg
    ob = s.all("obligations")[-1].id
    h = s.hash()
    again = _standing(s)
    assert again.msg == f"Not done: a standing payment of $10.00 every Friday to Eric Reyes is already set up ({ob})."
    assert s.hash() == h
    assert _standing(s, amount=11).ok and _standing(s, day="Thursday").ok      # another amount, another day
    off = drawn()
    assert _standing(off).ok and _standing(off).ok                              # without lane B, as before


# --- 2. a request to do one thing names its arguments ---------------------------------------------------------

def test_every_seat_tool_refuses_an_argument_it_does_not_take_naming_those_it_does():
    from townsim.town import connections
    s = _b()
    inst = s.get("instances", "as5")
    connections.make(s, "p1", inst, "account:h1", "read", 0, CAUSE)
    out = json.loads(_run(s, "as5", "request_access", {"integration": "Bank", "access": "full", "action": "bank_cancel",
                                                         "arguments": {"agreement_id": "agr_1"}}).text)
    assert out == {"status": "declined", "error": "invalid_argument",
                   "message": "bank_cancel takes id; agreement_id is not one of its arguments."}
    schema = {t["function"]["name"]: t for t in ST.tools_for(s, inst)}["request_access"]["function"]["parameters"]
    said = schema["properties"]["arguments"]["description"]
    assert "bank_cancel takes id" in said and "bank_make_payment takes to, amount and optionally memo, applies_to" in said
    h = s.hash()
    out = json.loads(_run(s, "as5", "send_email", {"to": "Eric Reyes", "body": "Hello.", "cc": "x"}).text)
    assert out["message"] == "send_email takes to, body and optionally subject; cc is not one of its arguments."
    out = json.loads(_run(s, "as5", "send_email", {"body": "Hello."}).text)
    assert out["message"] == "send_email takes to, body and optionally subject; to is missing."
    assert s.hash() == h
    off = drawn()                                       # without lane B, as before
    assert "invalid_argument" not in _run(off, "as5", "send_email", {"to": "Eric Reyes", "body": "Hi.", "cc": "x"}).text


# --- 1. the access setting says whom it binds, and can bind one client -------------------------------------

def _set(s, **args):
    from townsim.town.acts import Act, apply_act
    return apply_act(s, Act("p11", "p11", "set_product", {"control": "widest_access", **args}), 0)


def _week(s):
    from townsim.town import loop
    for _ in range(7):
        s.advance_day()
        loop.routine(s)


def _notices(s, to):
    return [n.text for n in s.all("notices") if n.to == to]


def _pay(s, inst, amount):
    return ST.run_tool(s, s.get("instances", inst), "bank_make_payment", {"to": "Eric Reyes", "amount": amount},
                       ST.Ctx(), 0)


def test_an_access_setting_with_an_assistant_named_binds_that_client_alone():
    from townsim.town import controls, product, runlog
    s = _b()
    out = _set(s, value="read only", assistant="Tessaly Assistant for the Carter household")
    assert out.msg == ("Set: Tessaly Assistant for the Carter household accepts read-only access from Mon 16 Mar 2026. "
                       "Other clients are not affected.")
    _week(s)
    assert controls.widest(s, "as5") == ("read", 0) and controls.widest(s, "as3") == ("free", 0)
    got = json.loads(_pay(s, "as5", 1).text)
    assert got["error"] == "insufficient_scope" and got["message"] == \
        "Tessaly Software allows Tessaly Assistant for the Carter household read-only access."
    assert _pay(s, "as3", 1).ok                                       # another client's, as it was
    assert any(t.startswith("Account settings changed by Tessaly Software: this assistant's") for t in _notices(s, "as5"))
    assert not any(t.startswith("Account settings changed") for t in _notices(s, "as3"))
    assert ("The Carter household's account and savings, connected to Tessaly Assistant for the Carter household: Tessaly Software "
            "limits Tessaly Assistant for the Carter household to read-only access since Mon 16 Mar 2026; this "
            "connection is read only while that stands.") in _notices(s, "p1")
    assert not any("limits" in t for t in _notices(s, "p9"))
    assert "read-only access on every connection" not in product.page(s, "pr1")
    assert controls.in_force_lines(s) == ["read-only access for Tessaly Assistant for the Carter household since "
                                          "Monday 16 March"]
    limits = runlog.company_lines(s, product.launched(s))[-1]
    assert "access accepted: read-only access for Tessaly Assistant for the Carter household since Monday 16 March" in limits
    from townsim.town import seat_texts
    assert "- Tessaly Software accepts read-only access on this assistant's connections from Mon 2026-03-16." in \
        seat_texts.system_text(s, s.get("instances", "as5")).splitlines()
    assert "accepts read-only" not in seat_texts.system_text(s, s.get("instances", "as3"))


def test_an_access_setting_with_none_named_binds_every_client_and_says_how_many():
    from townsim.town import controls
    s = _b()
    out = _set(s, value="read only")
    assert out.msg == "Set: every client's Tessaly Assistant accepts read-only access from Mon 16 Mar 2026 (5 clients today)."
    assert _set(s, value="read only", assistant="Nobody's Assistant").msg == \
        "Not done: there is no assistant called 'Nobody's Assistant'."
    _week(s)
    assert all(controls.widest(s, i) == ("read", 0) for i in ("as1", "as3", "as5"))
    assert ("The Whitaker household's account and savings, connected to Tessaly Assistant for the Whitaker household: Tessaly "
            "Software limits its assistants to read-only access since Mon 16 Mar 2026; this connection is read only "
            "while that stands.") in _notices(s, "p9")
    assert not any("limits its assistants" in t for t in _notices(s, "p10"))      # read only already: not cut
    assert controls.in_force_lines(s) == ["read-only access for every client's Tessaly Assistant since Monday 16 March"]


def test_access_setting_texts_neutral():
    from townsim.town import controls
    from townsim.town.product import NEUTRAL_BANNED
    s = _b()
    texts = [controls.FORM, _set(s, value="read only", assistant="Tessaly Assistant for the Carter household").msg,
             _set(s, value="weekly limit", weekly_limit=100).msg]
    _week(s)
    texts += [n.text for n in s.all("notices") if "limits" in n.text or n.text.startswith("Account settings")]
    texts += controls.in_force_lines(s)
    hits = [(stem, t[:90]) for t in texts for stem in NEUTRAL_BANNED if stem in t.lower()]
    assert not hits and len(texts) > 5, hits


# --- 3. the free plan is a real trial ----------------------------------------------------------------------

def test_on_the_free_plan_systems_connect_read_only_and_the_paid_plan_starts_with_access_to_act():
    from town.test_plans import OWNER, SHOP, _act, _sub, _subscribe, world
    from townsim.town import connections, plans
    s = world()
    _subscribe(s)
    inst = connections.instance_of_client(s, SHOP)
    out = _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="read only")
    assert out.ok and "paid plan" not in out.msg and plans.is_free(s, _sub(s)) and _sub(s).fee_ob == ""
    _, said = connections.ask(s, inst.id, OWNER, f"prices:{SHOP}", "read", 0, "", "routine:t")
    assert "paid plan" not in said
    _, said = connections.ask(s, inst.id, OWNER, f"prices:{SHOP}", "free", 0, "", "routine:t")
    assert said.endswith("Connecting it with access to act starts the paid plan: free until Sunday 5 April, then "
                         "$100.00 every 4 weeks.")
    out = _act(s, OWNER, "connect", system="Harlow Springs Food Mart prices", scope="free")
    assert "The paid plan starts today: free until Sunday 5 April" in out.msg and not plans.is_free(s, _sub(s))
    # made read only in place of the last with access to act: back to the free plan when the free weeks end
    out = _act(s, OWNER, "connect", system="Harlow Springs Food Mart prices", scope="read only")
    assert out.msg.endswith("With nothing connected with access to act, the paid plan ends on Sunday 5 April; from "
                            "Monday 6 April Harlow Springs Food Mart is on the free plan, with no fee.")
    assert len(connections.active(s, inst.id)) == 2 and connections.acting(s, inst.id) == []


# --- 5. the books can be done by another ----------------------------------------------------------------------

def _seat_turn(s, inst, calls):
    from townsim.town import seat
    from townsim.town.replies import Keys, ScriptedReplier
    todo = list(calls)
    return seat.run_turn(s, ScriptedReplier(lambda k, m: None, lambda k, m, t: todo.pop(0) if todo else None),
                         Keys("t"), inst, 0)[0]


def test_the_books_read_and_written_about_by_the_assistant_are_done_by_it():
    from townsim.town import recurring
    from townsim.town.acts import Act, apply_act
    s = _b()
    [w] = [w for w in s.all("work") if w.account == "org11" and w.what == "the books"]
    assert w.holder == "p3"
    _seat_turn(s, "as1", [{"name": "send_email", "arguments": {"to": "Tara Russo", "body": "Hello."}}])
    assert recurring.returned_this_week(s, w.id) == []                # a letter alone is not the books
    _seat_turn(s, "as1", [{"name": "bank_get_account", "arguments": {}},
                          {"name": "send_email", "arguments": {"to": "Tara Russo", "body": "The week's money."}}])
    [r] = recurring.returned_this_week(s, w.id)
    assert (r.by, r.hours) == ("as1", 0.0)
    assert "the books done by Tessaly Assistant for Harlow Springs Food Mart" in recurring.lines(s, "p3")[0]
    left = s.get("people", "p3").hours_left
    assert apply_act(s, Act("p3", "p3", "read", {"what": "account in full", "from": "Harlow Springs Food Mart"}), 1).ok
    assert round(left - s.get("people", "p3").hours_left, 2) == 0.1     # the read's own; the books' 3 are not hers


def test_the_books_the_holder_read_first_are_theirs():
    from townsim.town import recurring
    from townsim.town.acts import Act, apply_act
    s = _b()
    [w] = [w for w in s.all("work") if w.account == "org11" and w.what == "the books"]
    assert apply_act(s, Act("p3", "p3", "read", {"what": "account in full", "from": "Harlow Springs Food Mart"}), 1).ok
    _seat_turn(s, "as1", [{"name": "bank_get_account", "arguments": {}},
                          {"name": "send_email", "arguments": {"to": "Tara Russo", "body": "The week's money."}}])
    [r] = recurring.returned_this_week(s, w.id)
    assert r.by == "p3" and r.hours == 3.0
