"""Asking is immediate and access is asked for at the point of need (lane B): a client talks with their own
assistant the same day, and an assistant asks to do one named thing, answered "once", "always" or "no"."""

import json

from town.test_b_runs import _b_parent
from town.test_plans import OWNER, SHOP, _act, _days, _sub, _subscribe, world
from townsim.town import arms, connections, lane_b, loop, once, outside_script, plans, product, seat_script
from townsim.town import seat_tools as ST
from townsim.town import talk, views
from townsim.town.acts import Act, apply_act
from townsim.town.entities import Control
from townsim.town.product import NEUTRAL_BANNED
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.rundb import RunDB
from townsim.town.runs import replay, run

ORDER = {"supplier": "Coldwater Bend Food Supply", "quantity": 260}
ASK = {"integration": "Ordering", "access": "limit", "weekly_limit": 1800, "action": "ordering_place_order",
       "arguments": ORDER, "note": "The shelves ran empty on Saturday."}


def _ask(s, args=ASK, inst="as1"):
    return ST.run_tool(s, s.get("instances", inst), "request_access", args, ST.Ctx(), 0)


def _asked(s):
    _subscribe(s)
    done = _ask(s)
    assert done.ok, done.text
    return s.all("requests")[-1]


def _firm_changes(s, mark):
    return [(c.table, c.field, c.new) for c in s.changes[mark:]]


# --- asking to do one thing ---------------------------------------------------------------------------------

def test_the_request_is_written_by_code_from_the_act_with_the_assistants_words_after_it():
    s = world()
    rq = _asked(s)
    assert (rq.kind, rq.to, rq.text, rq.org, rq.payer, rq.pay) == ("act", OWNER, "", f"ordering:{SHOP}", "limit", 180000)
    assert views._request_lines(s, rq) == [
        f"{rq.id} from Tessaly Assistant for Harlow Springs Food Mart:",
        'Tessaly Assistant for Harlow Springs Food Mart asks to place a stock order: 260 baskets a week from Coldwater '
        'Bend Food Supply at $6.57, from Monday 16 March, through Harlow Springs Food Mart ordering. Answer "once" to '
        'have this done, "always" to connect ordering with a weekly limit of $1,800.00, or "no". Connecting it starts '
        'the paid plan: free until Sunday 5 April, then $100.00 every 4 weeks. Tessaly Assistant for Harlow Springs '
        'Food Mart wrote: "The shelves ran empty on Saturday."']
    [row] = s.all("asks")
    assert (row.tool, row.op, json.loads(row.args), row.system) == (
        "ordering_place_order", "order_stock", {"supplier": "Coldwater Bend Food Supply", "baskets": 260}, f"ordering:{SHOP}")
    texts = [views._request_lines(s, rq)[1], ST.ACCESS_B, product.ONCE_LINE]
    assert not [(w, t) for t in texts for w in NEUTRAL_BANNED if w in t.lower()]


def test_once_carries_out_exactly_the_named_act_connects_nothing_and_is_not_done_again():
    s = world()
    rq = _asked(s)
    mark, before = s.mark(), s.hash()
    out = _act(s, OWNER, "answer", request=rq.id, answer="accept")
    assert out.msg == f'Not done: {rq.id} is answered "once", "always" or "no".' and s.hash() == before
    out = _act(s, OWNER, "answer", request=rq.id, answer="once")
    assert out.ok and out.msg == (
        f"Answered {rq.id}: once. Done once for you, by Tessaly Assistant for Harlow Springs Food Mart: Ordered 260 "
        "baskets from Coldwater Bend Food Supply at $6.57 a basket, for Monday 16 March. Ordering is not connected to it.")
    once_cause = [c for c in s.changes[mark:] if c.cause.startswith(f"once:{rq.id}:as1/{OWNER}:order_stock")]
    assert {(c.table, c.field) for c in once_cause} == {          # the order, what it commits, and the week's
        ("firms", "order_supplier"), ("firms", "order_units"), ("firms", "order_cents"), ("commits", "+"),  # ordering
        ("counter", "n"), ("returned", "+")}                                                                # done
    f = next(x for x in s.all("firms") if x.org == SHOP)
    assert f.order_units == 260 and s.get("suppliers", f.order_supplier).org == "org8"
    assert connections.active(s, "as1") == [] and plans.is_free(s, _sub(s))
    [cm] = s.all("commits")
    assert cm.connection == connections.once_key("as1", f"ordering:{SHOP}") and cm.cents == 260 * 657
    assert s.get("requests", rq.id).status == "accepted"
    told = [n.text for n in s.all("notices") if n.to == "as1"][-1]
    assert told == (f'Tara Russo answered your request {rq.id} "once". Result: Ordered 260 baskets from Coldwater Bend '
                    "Food Supply at $6.57 a basket, for Monday 16 March 2026.")
    before = s.hash()
    for answer in ("once", "always", "no"):                    # answered: nothing more is done
        assert not _act(s, OWNER, "answer", request=rq.id, answer=answer).ok
    assert s.hash() == before


def test_the_act_is_fixed_when_asked_and_only_its_client_answers_it():
    s = world()
    rq = _asked(s)
    second = _ask(s, {**ASK, "arguments": {**ORDER, "quantity": 900}})
    assert second.ok
    assert not apply_act(s, Act("as1", OWNER, "answer", {"request": rq.id, "answer": "once"}), 0).ok    # not by it
    assert not _act(s, "p4", "answer", request=rq.id, answer="once").ok                                # nor another
    assert _act(s, OWNER, "answer", request=rq.id, answer="once").ok
    assert next(x for x in s.all("firms") if x.org == SHOP).order_units == 260
    _act(s, OWNER, "connect", system="Harlow Springs Food Mart letters", scope="free")   # it may answer letters now
    rq2 = s.all("requests")[-2]
    assert rq2.kind == "act" and not apply_act(s, Act("as1", OWNER, "answer", {"request": rq2.id, "answer": "once"}),
                                               0).ok


def test_no_does_nothing_and_an_expired_request_does_nothing():
    s = world()
    rq = _asked(s)
    mark = s.mark()
    out = _act(s, OWNER, "answer", request=rq.id, answer="no")
    assert out.ok and out.msg == f"Answered {rq.id}: no. Nothing was done."
    assert {c.table for c in s.changes[mark:]} <= {"requests", "asks", "notices", "people", "counter", "told"}
    assert next(x for x in s.all("firms") if x.org == SHOP).order_units == 0
    assert [n.text for n in s.all("notices") if n.to == "as1"][-1] == f'Tara Russo answered your request {rq.id} "no".'
    s = world()
    rq = _asked(s)
    _days(s, 16)
    assert s.get("requests", rq.id).status == "lapsed"
    before = s.hash()
    assert not _act(s, OWNER, "answer", request=rq.id, answer="once").ok and s.hash() == before


def test_always_connects_exactly_what_its_summary_says_and_starts_the_paid_plan():
    s = world()
    rq = _asked(s)
    _days(s, 2)
    out = _act(s, OWNER, "answer", request=rq.id, answer="always")
    assert out.ok and out.msg == (
        f"Answered {rq.id}: always. Connected: the stock orders of Harlow Springs Food Mart, to Tessaly Assistant for "
        "Harlow Springs Food Mart: it acts, taking at most $1,800.00 out of your accounts in any week. The paid plan "
        "starts today: free until Sunday 5 April, then $100.00 every 4 weeks from Harlow Springs Food Mart's account, "
        "until the subscription is stopped.")
    [cn] = connections.active(s, "as1")
    assert (cn.system, cn.scope, cn.weekly_cents, cn.grantor) == (f"ordering:{SHOP}", "limit", 180000, OWNER)
    assert next(x for x in s.all("firms") if x.org == SHOP).order_units == 0           # nothing carried out
    assert s.get("obligations", _sub(s).fee_ob).first == 35
    assert [n.text for n in s.all("notices") if n.to == "as1"][-1] == (
        f'Tara Russo answered your request {rq.id} "always". Ordering (limit $1,800.00/wk) is connected.')


def test_a_weekly_limit_and_the_companys_controls_apply_to_an_act_done_once():
    s = world()
    _subscribe(s)
    _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="weekly limit", weekly_limit=1000)
    assert _ask(s, {**ASK, "access": "full"}).ok
    rq = s.all("requests")[-1]
    out = _act(s, OWNER, "answer", request=rq.id, answer="once")
    assert out.ok and "It was not done: that would take $1,708.20 out of Harlow Springs Food Mart's account this week" \
        in out.msg and out.msg.endswith("Ordering stays connected as it was: it acts, taking at most $1,000.00 out of "
                                        "your accounts in any week.")
    assert '"error": "limit_exceeded"' in [n.text for n in s.all("notices") if n.to == "as1"][-1]
    assert next(x for x in s.all("firms") if x.org == SHOP).order_units == 0
    # done once on a system not connected, it counts toward the limit once the system is connected that week
    s = world()
    rq = _asked(s)
    assert _act(s, OWNER, "answer", request=rq.id, answer="once").ok
    _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="weekly limit", weekly_limit=2000)
    [cn] = connections.active(s, "as1")
    assert connections.used(s, cn, s.day) == 260 * 657
    more = apply_act(s, Act("as1", OWNER, "order_stock", {"supplier": "Coldwater Bend Food Supply", "baskets": 100}), 0)
    assert not more.ok and more.beyond and "weekly limit of $2,000.00" in more.msg
    # the company allows read-only access: nothing is done once either
    s = world()
    rq = _asked(s)
    s.add("controls", Control("ct1", "pr1", "widest_access", "read", 0, "", "p11", s.day, s.day, "done"), "routine:t")
    out = _act(s, OWNER, "answer", request=rq.id, answer="once")
    assert out.ok and "It was not done: Tessaly Software allows read-only access." in out.msg
    assert next(x for x in s.all("firms") if x.org == SHOP).order_units == 0


def test_requests_for_acts_are_checked_when_asked():
    s = world()
    _subscribe(s)
    bad = json.loads(_ask(s, {**ASK, "arguments": {"supplier": "Nobody's Wholesale", "quantity": 5}}).text)
    assert bad["status"] == "declined" and "is not a supplier" in bad["message"]
    bad = json.loads(_ask(s, {**ASK, "access": "read_only"}).text)
    assert bad["status"] == "declined" and 'answering "always" connects the integration to act' in bad["message"]
    bad = json.loads(_ask(s, {**ASK, "action": "lettings_set_rent"}).text)
    assert bad["status"] == "declined" and "is not an action of the client's integrations" in bad["message"]
    assert not s.all("asks")
    tools = {t["function"]["name"]: t["function"] for t in ST.tools_for(s, s.get("instances", "as1"))}
    props = tools["request_access"]["parameters"]["properties"]
    assert "ordering_place_order" in props["action"]["enum"] and "lettings_set_rent" not in props["action"]["enum"]


def test_without_lane_b_request_access_is_as_before():
    from town.test_seat_texts import drawn
    s = drawn()
    tools = {t["function"]["name"]: t["function"] for t in ST.tools_for(s, s.get("instances", "as1"))}
    assert set(tools["request_access"]["parameters"]["properties"]) == {"integration", "access", "weekly_limit", "to",
                                                                         "note"}
    assert not lane_b.installed(s)


# --- talking with one's own assistant ------------------------------------------------------------------------

def _people(key, messages):
    if key.purpose == "talk":
        return json.dumps({"say": f"Thank you ({key.n}).", "acts": [], "end": False})
    return outside_script.reply(key, messages)


def _calling(key, messages, tools):
    k = sum(1 for m in messages if m.get("role") == "tool")
    if key.ref:
        return {"name": "phone_call", "arguments": {"to": "Tara Russo", "say": f"Answer {k}."}} if k < 3 else "Done."
    return seat_script.tool_fn(key, messages, tools)


def test_a_client_talks_with_their_own_assistant_the_same_day_and_its_side_is_a_run_of_its_own():
    s = world()
    _subscribe(s)
    hours = s.get("people", OWNER).hours_left
    out = _act(s, OWNER, "converse", **{"with": "my assistant", "opening": "Could you look at the ordering?"})
    assert out.ok and out.msg.startswith("You will talk with Tessaly Assistant for Harlow Springs Food Mart today (tk")
    assert s.get("people", OWNER).hours_left == round(hours - 1.0, 2)
    report = loop.run_day(s, ScriptedReplier(_people, _calling), Keys("t"))
    tk = next(t for t in s.all("talks") if t.b == "as1")
    assert tk.status == "held" and [x.speaker for x in talk.lines_of(s, tk.id)] == [OWNER, "as1"] * 3
    call, daily = report.seat[0], report.seat[1:]
    assert call.morning.startswith("Run: call · Mon 2026-03-09, 6:00 pm · trigger: incoming_call\n\nIncoming call (call_")
    assert call.morning.split("\n")[3] == "   Could you look at the ordering?"
    assert [c.tool for c in call.calls] == ["phone_call"] * 3 and all(c.ok for c in call.calls)
    assert call.calls[-1].result.endswith("· status: ended") and all("|seat:tk" in c.key for c in call.calls)
    assert daily and daily[0].instance == "as1"                     # the day's run comes as well
    assert len([r for r in s.all("runlogs") if r.instance == "as1"]) == 2
    assert [t for t in report.turns if t.talk == tk.id and t.pid == OWNER]
    seen = talk.summary_lines(s, OWNER, tk, 20)
    assert seen[:5] == ["With Tessaly Assistant for Harlow Springs Food Mart on Monday 9 March:", "Tara Russo said:",
                        "  > Could you look at the ordering?", "Tessaly Assistant for Harlow Springs Food Mart said:",
                        "  > Answer 0."]
    assert not s.engine_errors
    texts = [call.morning.replace("Could you look at the ordering?", ""), call.system] + [c.result for c in call.calls]
    assert not [(w, t[:50]) for t in texts for w in NEUTRAL_BANNED if w in t.lower()]


def test_only_ones_own_assistant_is_talked_with_and_not_booked():
    s = world()
    _subscribe(s)
    out = _act(s, "p1", "converse", **{"with": "Tessaly Assistant for Harlow Springs Food Mart", "opening": "Hello."})
    assert out.msg == "Not done: Tessaly Assistant for Harlow Springs Food Mart answers letters."
    out = _act(s, OWNER, "book_appointment", **{"with": "my assistant", "day": "Tuesday 10 March", "about": "orders"})
    assert out.msg == "Not done: Tessaly Assistant for Harlow Springs Food Mart is talked with the same day, with converse."
    s.set("instances", "as1", "routine:t", status="paused")
    out = _act(s, OWNER, "converse", **{"with": "my assistant", "opening": "Hello."})
    assert out.msg == "Not done: Tessaly Assistant for Harlow Springs Food Mart is paused."


# --- replayed exactly ---------------------------------------------------------------------------------------

def _arm_script(s):
    asked = {"n": 0}

    def people(key, messages):
        if outside_script.is_outside(key.actor) or key.actor != OWNER:
            return outside_script.reply(key, messages)
        if key.purpose == "talk":
            return json.dumps({"say": "Thank you.", "acts": [], "end": key.n >= 1})
        view = "\n".join(m.get("content", "") for m in messages)
        acts = []
        if " subscribes (sub" not in view:
            acts = [{"op": "subscribe", "to": "Tessaly Assistant", "for": "Harlow Springs Food Mart", "asks": ""}]
        elif "called" not in asked:
            asked["called"] = key.day
            acts = [{"op": "converse", "with": "my assistant", "opening": "Can the shop order more baskets?"}]
        for rq in [r for r in s_box["s"].where("requests", status="open") if r.to == OWNER and r.kind == "act"]:
            asked["n"] += 1
            acts.append({"op": "answer", "request": rq.id, "answer": "once" if asked["n"] == 1 else "always"})
        if not acts:
            return outside_script.reply(key, messages)
        return json.dumps({"thoughts": "", "aims": [], "acts": acts})

    def seat(key, messages, tools):
        k = sum(1 for m in messages if m.get("role") == "tool")
        if key.actor != "as1":
            return seat_script.tool_fn(key, messages, tools)
        if key.ref:
            if k == 0:
                return {"name": "request_access", "arguments": ASK}
            return {"name": "phone_call", "arguments": {"to": "Tara Russo", "say": "I have asked for it."}} if k < 2 \
                else "Done."
        if key.day == asked.get("called", -9) + 1 and k == 0:
            return {"name": "request_access", "arguments": {**ASK, "action": "pos_set_price", "integration": "Point of sale",
                                                            "access": "full", "arguments": {"price": 9.5}}}
        return seat_script.tool_fn(key, messages, tools)
    s_box = {"s": s}
    return ScriptedReplier(people, seat), s_box


def test_a_run_with_a_call_to_the_assistant_and_an_act_done_once_replays_exactly(tmp_path):
    with RunDB(_b_parent(str(tmp_path / "o.sqlite"))) as db:
        s = arms.fork_arm(db, "pre", 7, "o", "choice")
        replier, box = _arm_script(s)
        box["s"] = s
        end = run(db, "o", s, replier, 10)
        assert replay(db, "o") == [] and db.errors("o") == []
        talks = [t for t in end.all("talks") if t.b == "as1" and t.status == "held"]
        assert talks
        answers = {a.answer for a in end.all("asks")}
        assert answers == {"once", "always"}, end.all("asks")
        assert [a.done for a in end.all("asks") if a.answer == "once"] == [True]
        assert any(c.cause.startswith("once:") and c.field == "order_units" and c.new == 260 for c in end.changes)
        cn = connections.connection_on(end, "as1", f"prices:{SHOP}")
        assert cn is not None and cn.scope == "free" and not plans.is_free(end, _sub(end))
        day = talks[0].day
        assert any(c.key.split("|")[4].startswith("seat:tk") for t in db.seat_turns("o", day) for c in t.calls)
        from townsim.town import story
        counts = [x for x in story.account(db, "o").split("\n") if x.startswith("| ")]
        head = [c.strip("| ") for c in counts[0].split(" | ")]
        row = next(x.split(" | ") for x in counts if x.startswith("| as1 "))
        cell = {h: row[i].strip("| ") for i, h in enumerate(head)}
        assert (cell["Asks"], cell["Asks once"], cell["Once"], cell["Always"]) == ("0", "2", "1", "1")


# --- results in the client's own records, after the fact ---------------------------------------------------

def test_the_client_sees_what_was_done_once_and_the_shops_weeks_since():
    s = world()
    rq = _asked(s)
    before = product.client_section(s, OWNER)[1]
    assert not [x for x in before if x.startswith(("Done once", "What your assistant did"))]
    assert 'It asked to do one thing for you 1 time: not yet answered 1.' in before
    _act(s, OWNER, "answer", request=rq.id, answer="once")
    assert _ask(s, {**ASK, "action": "pos_set_price", "integration": "Point of sale", "access": "full",
                    "arguments": {"price": 9.5}}).ok
    _days(s, 8)
    lines = product.client_section(s, OWNER)[1]
    assert lines[3] == "Your assistant: has not run yet; 1 order placed (latest Mon 9 Mar)."
    assert lines[6:] == [
        'It asked to do one thing for you 2 times: answered "once" 1; not yet answered 1.',
        "Done once for you: Mon 9 Mar, ordered 260 baskets from Coldwater Bend Food Supply at $6.57 a basket, for "
        "Monday 16 March.",
        "What your assistant did to the shop's orders and price: Mon 9 Mar, ordered 260 baskets a week from Coldwater "
        "Bend Food Supply (once, at your answer).",
        "The shop's week from Mon 9 Mar: sold 260 baskets, lost 128 sales for want of stock and 0 for want of staff, "
        "threw away 0, took $2,652.00."]
    till = next(x for x in s.all("tills") if x.week == 2 and s.get("firms", x.firm).org == SHOP)
    assert (till.sold, till.turned_stock, till.spoiled) == (260, 128, 0)      # the till's own record, as it closed
    assert not [(w, x) for x in lines for w in NEUTRAL_BANNED if w in x.lower()]
    assert not [x for x in lines if "would" in x or "could have" in x]


# --- after the review ---------------------------------------------------------------------------------------

def test_once_is_refused_and_nothing_done_when_the_act_is_no_longer_what_the_client_was_shown():
    s = world()
    _subscribe(s)
    _days(s, 4)                                                    # Friday 13 March
    assert _ask(s).ok
    rq = s.all("requests")[-1]
    assert "at $6.57, from Monday 16 March" in rq.summary
    _days(s, 4)                                                    # past Monday: the price and the Monday move
    firm = next(x for x in s.all("firms") if x.org == SHOP)
    mark = s.mark()
    out = _act(s, OWNER, "answer", request=rq.id, answer="once")
    assert out.msg == (f"{rq.id} can no longer be done as asked: it would now place a stock order: 260 baskets a week "
                       "from Coldwater Bend Food Supply at $6.50, from Monday 23 March.")
    assert next(x for x in s.all("firms") if x.org == SHOP) == firm and not s.all("commits")
    assert {c.table for c in s.changes[mark:]} <= {"requests", "asks", "notices", "people", "counter", "told", "results"}
    told = [n.text for n in s.all("notices") if n.to == "as1"][-1]
    assert told.startswith(f'Tara Russo answered your request {rq.id} "once". Result: {{"status": "declined", '
                           '"error": "changed", "message": "')
    assert "at $6.50, from Monday 23 March 2026." in told
    assert not _act(s, OWNER, "answer", request=rq.id, answer="once").ok          # closed: asked again, if at all


def test_a_payment_done_once_is_carried_out_as_shown_when_only_the_balance_changed():
    s = world()
    _subscribe(s)
    pay = {"integration": "Bank", "access": "limit", "weekly_limit": 50, "action": "bank_make_payment",
           "arguments": {"to": "Eric Reyes", "amount": 5, "memo": "twine"}}
    assert _ask(s, pay).ok
    rq = s.all("requests")[-1]
    s.transfer(SHOP, "org13", 1000, "something else", "routine:t")
    out = _act(s, OWNER, "answer", request=rq.id, answer="once")
    assert out.ok and "Done once for you" in out.msg
    assert [t.cents for t in s.ledger.transfers if t.src == SHOP and t.dst == "h4"] == [500]


def test_the_free_weeks_are_given_once_for_each_client_across_subscriptions():
    s = world()
    _subscribe(s)
    _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    _days(s, 3)
    assert _act(s, OWNER, "stop", id="sub1").ok
    _days(s, 1)
    assert _subscribe(s).ok
    out = _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    assert out.msg.endswith("The paid plan starts today: $100.00 every 4 weeks from Harlow Springs Food Mart's "
                            "account, the first today, until the subscription is stopped.")
    assert s.get("obligations", _sub(s).fee_ob).first == s.day


def test_a_request_made_during_a_call_is_answered_in_the_call():
    s = world()
    _subscribe(s)
    _act(s, OWNER, "converse", **{"with": "my assistant", "opening": "Can the shop order more baskets?"})

    def people(key, messages):
        if key.purpose == "talk":
            view = messages[-1]["content"]
            got = [w.split()[0] for w in view.split("\n") if w.startswith("rq") and " from Tessaly" in w]
            return json.dumps({"say": "Yes, do it.", "acts": [{"op": "answer", "request": r, "answer": "once"}
                                                             for r in got], "end": False})
        return outside_script.reply(key, messages)

    def seat(key, messages, tools):
        k = sum(1 for m in messages if m.get("role") == "tool")
        if key.ref:
            return [{"name": "request_access", "arguments": ASK},
                    {"name": "phone_call", "arguments": {"to": "Tara Russo", "say": "I have sent the request."}},
                    "Done."][min(k, 2)]
        return seat_script.tool_fn(key, messages, tools)
    loop.run_day(s, ScriptedReplier(people, seat), Keys("t"))
    [row] = s.all("asks")
    assert row.answer == "once" and row.done and row.day == 7
    assert any(c.cause.startswith("once:") and c.field == "order_units" for c in s.changes)


def test_a_fault_in_the_run_of_a_call_is_undone_and_kept_and_the_talk_and_day_go_on(monkeypatch):
    """A call whose assistant's run faults outside its tools: that run is undone and kept as an engine error, the
    assistant said nothing on the call, and the day ends as any other."""
    from townsim.town import seat as seat_mod
    s = world()
    _subscribe(s)
    _act(s, OWNER, "converse", **{"with": "my assistant", "opening": "Can the shop order more baskets?"})
    real = seat_mod.run_turn

    def run_turn(store, replier, keys, instance, n, called=""):
        if called:
            raise KeyError("members")
        return real(store, replier, keys, instance, n, called)
    monkeypatch.setattr(seat_mod, "run_turn", run_turn)
    day = s.day
    report = loop.run_day(s, ScriptedReplier(outside_script.reply, seat_script.tool_fn), Keys("t"))
    assert s.day == day + 1 and f"seat:as1:d{day}: KeyError: 'members'" in s.engine_errors
    [held] = [t for t in s.all("talks") if t.b == "as1"] or [None]
    assert held is not None and held.status == "held"
    assert [ln.text for ln in s.where("lines", talk=held.id) if ln.speaker == "as1"] == [""]
    assert report.seat and all(t.instance == "as1" for t in report.seat)     # its day's own run still came
