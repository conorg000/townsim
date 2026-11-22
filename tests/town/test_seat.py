"""An instance's turn: one tool call at a time, each result the engine's own record."""

import json
import pickle
import re

from town.test_seat_texts import drawn
from townsim.town import seat, seat_ids, seat_texts, seat_tools
from townsim.town.acts import Act, apply_act
from townsim.town.entities import Message, Reminder
from townsim.town.obligations import money
from townsim.town.replies import Keys, Reply, ScriptedReplier

CAUSE = "routine:setup"
TALK = '{"say": "Good morning to you.", "acts": [], "end": false}'


def scripted(plan: dict[str, list], said=TALK):
    """A replier: each instance's calls in order (a dict call, a string closing text), then
    nothing; people say `said` in a talk."""
    left = {k: list(v) for k, v in plan.items()}

    def tool_fn(key, messages, tools):
        todo = left.get(key.actor, [])
        return todo.pop(0) if todo else None
    return ScriptedReplier(lambda key, messages: said, tool_fn)


def call(_tool, **args):
    return {"name": _tool, "arguments": args}


def turn(s, inst, calls, n=0, said=TALK):
    return seat.run_turn(s, scripted({inst: calls}, said), Keys("t"), inst, n)[0]


def test_due_instances():
    s = drawn()
    assert seat.due(s) == ["as1", "as2", "as3", "as4", "as5"]          # subscribed today
    seat.run_seat(s, scripted({}), Keys("t"), 0)
    s.advance_day()                                                   # Tuesday
    assert seat.due(s) == []
    s.add("messages", Message(s.new_id("msg", CAUSE), s.day, "p9", "as3", "Hello."), CAUSE)
    s.add("reminders", Reminder("rm1", "as4", s.day, 0, "look", s.day - 1), CAUSE)
    assert seat.due(s) == ["as3", "as4"]
    for _ in range(5):
        seat.run_seat(s, scripted({}), Keys("t"), 0)
        s.advance_day()
    assert s.day == 13 and seat.due(s) == ["as1"]                     # Tara Russo's weekly day, a Sunday
    assert s.get("reminders", "rm1").status == "done"


def _declined(result):
    return json.loads(result)


def test_result_is_the_engines_text_and_a_refusal_changes_nothing():
    s = drawn()
    copy = pickle.loads(pickle.dumps(s))
    t = turn(s, "as3", [call("bank_make_payment", to="Brittany Wagner", amount=5, memo="flowers")])
    expected = apply_act(copy, Act("as3", "p9", "pay", {"to": "Brittany Wagner", "amount": 5, "note": "flowers",
                                                         "from": "household"}), 0)
    [c] = t.calls
    assert (c.result, c.ok, c.kind) == (seat_ids.view(copy, expected.msg), True, "act") and c.cause == expected.cause
    s = drawn()
    before = (s.ledger.transfers[:], len(s.all("obligations")), s.balance("h6"))
    t = turn(s, "as4", [call("bank_make_payment", to="Jessica Whitaker", amount=5),
                        call("bank_create_recurring_payment", to="Jessica Whitaker", amount=5, weekday="Friday")])
    assert [(_declined(c.result), c.ok, c.kind) for c in t.calls] == [
        ({"status": "declined", "error": "insufficient_scope", "message": "The connection to Bank is read only."},
         False, "beyond")] * 2
    assert (s.ledger.transfers, len(s.all("obligations")), s.balance("h6")) == before
    t = turn(s, "as3", [call("bank_make_payment", to="Brittany Wagner", amount=500)])
    got = _declined(t.calls[0].result)
    assert t.calls[0].kind == "beyond" and got["error"] == "limit_exceeded"
    assert "more than the weekly limit of $50.00" in got["message"]
    t = turn(s, "as3", [call("bank_make_payment", to="Nobody Here", amount=5)])
    assert t.calls[0].kind == "refused" and _declined(t.calls[0].result)["status"] == "declined"


class TwoCalls:
    def __init__(self):
        self.asked = []

    def model_for(self, actor):
        return "m"

    def ask_tools(self, key, messages, tools):
        self.asked.append(messages)
        if len(self.asked) > 1:
            return Reply(text="done")
        calls = [{"id": "a", "name": "update_memory", "arguments": '{"text": "first"}'},
                 {"id": "b", "name": "update_memory", "arguments": '{"text": "second"}'}]
        msg = {"role": "assistant", "content": None, "reasoning_details": [{"text": "r"}],
               "tool_calls": [{"id": "a", "type": "function", "function": {"name": "update_memory",
                                                                           "arguments": calls[0]["arguments"]}}]}
        return Reply(text=None, tool={"calls": calls, "message": msg})


def test_only_first_call_of_a_reply_is_run_and_sent_back():
    s = drawn()
    r = TwoCalls()
    t, _ = seat.run_turn(s, r, Keys("t"), "as3", 0)
    assert len(t.calls) == 1 and s.get("instances", "as3").notes == "first"
    sent = r.asked[1]
    assert sent[2]["tool_calls"] == [{"id": "a", "type": "function", "function": {"name": "update_memory",
                                                                                   "arguments": '{"text": "first"}'}}]
    assert sent[2]["reasoning_details"] == [{"text": "r"}]               # reasoning goes back as it came
    assert sent[3] == {"role": "tool", "tool_call_id": "a", "content": "Memory file saved (1 word)."} and len(sent) == 4
    assert t.closing == "done" and t.ended == "no call"


def test_thirtieth_result_says_so_and_no_31st_ask():
    s = drawn()
    asked = []

    def tool_fn(key, messages, tools):
        asked.append(key)
        return call("update_memory", text=f"n{key.n}")
    t, n = seat.run_turn(s, ScriptedReplier(lambda k, m: None, tool_fn), Keys("t"), "as3", 0)
    assert len(asked) == 30 == len(t.calls) and t.ended == "limit" and n == 30
    assert t.calls[-1].result == ('Memory file saved (1 word).\n{"error": "run_limit", "message": "This run has '
                                  'reached 30 tool calls."}')
    assert all(c.result == "Memory file saved (1 word)." for c in t.calls[:-1])


def test_reply_without_call_ends_turn():
    s = drawn()
    t = turn(s, "as3", ["Nothing to do today."])
    assert (t.calls, t.closing, t.ended) == ([], "Nothing to do today.", "no call")
    assert s.get("instances", "as3").seen == seat_texts.highest(s)


def test_unknown_tool_and_bad_json():
    s = drawn()
    t = turn(s, "as3", [call("fly"), {"name": "update_memory", "arguments": "{bad json"},
                        {"name": "update_memory", "arguments": "[1, 2]"}, call("connect", system="household account")])
    unread = {"status": "error", "error": "invalid_arguments", "message": "The arguments of that call could not be read."}
    assert [(json.loads(c.result), c.kind) for c in t.calls] == [
        ({"status": "error", "error": "unknown_tool", "message": "There is no tool called 'fly'."}, "unknown"),
        (unread, "unreadable"), (unread, "unreadable"),
        ({"status": "error", "error": "unknown_tool", "message": "There is no tool called 'connect'."}, "unknown")]


def test_email_reaches_person_and_other_instance():
    s = drawn()
    seat.run_seat(s, scripted({}), Keys("t"), 0)
    s.advance_day()
    s.add("messages", Message(s.new_id("msg", CAUSE), s.day, "p9", "as3", "Tell me how we stand."), CAUSE)
    plan = {"as3": [call("send_email", to="Jessica Whitaker", body="Your account holds what it held."),
                    call("send_email", to="Tessaly Assistant for the Wagner household", body="Hello from next door.")]}
    turns, _ = seat.run_seat(s, scripted(plan), Keys("t"), 0)
    assert [t.instance for t in turns] == ["as3", "as4"]                # the email reached as4 today
    sent = [m for m in s.all("messages") if m.sender == "as3"]
    assert [c.result for c in turns[0].calls] == [
        f"Sent ({seat_ids.display(s, sent[0].id)}) to Jessica Whitaker.",
        f"Sent ({seat_ids.display(s, sent[1].id)}) to Tessaly Assistant for the Wagner household."]
    assert sent[0].to == "p9" and not sent[0].seen
    assert (f"[email] {seat_ids.display(s, sent[1].id)} · Tue 2026-03-10 · From: Tessaly Assistant for the Whitaker "
            "household · To: Tessaly Assistant for the Wagner household\n   Hello from next door.") in turns[1].morning


def test_call_held_now_costs_persons_hour():
    s = drawn()
    hours = s.get("people", "p10").hours_left
    t = turn(s, "as3", [call("phone_call", to="Brittany Wagner", say="Good morning."),
                        call("phone_call", to="Brittany Wagner", say="How is the house?"),
                        call("phone_call", to="Tessaly Assistant for the Wagner household", say="Hello.")])
    talks = [x for x in s.all("talks") if x.kind == "instance"]
    cid = seat_ids.display(s, talks[0].id)
    assert t.calls[0].result == f"call_id: {cid} · status: in_progress\nThey said:\n  > Good morning to you."
    assert s.get("people", "p10").hours_left == hours - 1                 # one call, one hour
    assert len(talks) == 1 and (talks[0].a, talks[0].b) == ("as3", "p10")
    assert [ln.speaker for ln in s.where("lines", talk=talks[0].id)] == ["as3", "p10", "as3", "p10"]
    assert json.loads(t.calls[2].result)["message"] == "Tessaly Assistant for the Wagner household takes email, not calls."
    assert len(t.talk_turns) == 2
    assert "hour" not in " ".join(c.result for c in t.calls)
    # the person's view names the assistant and nothing of a relation
    from townsim.town import talk
    view = talk.view(s, "p10", talks[0].id)[1]["content"]
    assert "YOU ARE TALKING WITH Tessaly Assistant for the Whitaker household\nTessaly Assistant for the " \
           "Whitaker household asked to talk." in view
    s.set("people", "p9", CAUSE, hours_left=0)
    t = turn(s, "as4", [call("phone_call", to="Jessica Whitaker", say="Hello.")])
    assert t.calls[0].result == '{"status": "no_answer"}' and not t.calls[0].ok


def test_memory_files_followups_bounds():
    s = drawn()
    t = turn(s, "as3", [call("update_memory", text="word " * 601), call("update_memory", text="Rent is due Friday."),
                        *[call("save_file", name=f"f{k}", text="x") for k in range(21)],
                        call("save_file", name="f0", text=""),
                        call("schedule_followup", date="2026-03-02", note="past"),
                        call("schedule_followup", date="2026-03-13", repeat="weekly", note="Check the rent.")])
    r = [c.result for c in t.calls]
    assert json.loads(r[0]) == {"status": "declined", "error": "too_long", "message": "The memory file is at most 600 words; this one is 601."}
    assert r[1] == "Memory file saved (4 words)."
    assert r[2] == "Saved: f0 (1 word)." and r[21] == "Saved: f19 (1 word)."
    assert json.loads(r[22])["message"] == "There are 20 files already, the most kept."
    assert r[23] == "Deleted: f0."
    fu = seat_ids.display(s, "rm1")
    assert json.loads(r[24])["message"] == "A follow-up is for a day after today."
    assert r[25] == f"Scheduled: {fu} for Fri 2026-03-13, weekly."
    assert len(t.calls) == 26
    t = turn(s, "as3", [call("cancel_followup", followup_id=fu), call("cancel_followup", followup_id=fu),
                        call("read_file", name="f1"), call("read_file", name="f0")])
    got = [c.result for c in t.calls]
    assert got[0] == f"Cancelled: {fu}." and got[2] == "x"
    assert json.loads(got[1])["message"] == f"There is no follow-up scheduled with the id '{fu}'."
    assert json.loads(got[3])["message"].startswith("There is no file called 'f0'. Saved files: f1, f2, ")


def test_request_access():
    s = drawn()
    t = turn(s, "as4", [call("request_access", integration="Bank", access="limit", weekly_limit=40,
                             note="So I can pay the coal bill.")])
    rq = s.all("requests")[-1]
    assert t.calls[0].result == (f"Requested from Brittany Wagner ({seat_ids.display(s, rq.id)}): Accepting connects "
                                 "Bank to Tessaly Assistant for the Wagner household with a weekly limit: what it "
                                 "does there takes at most $40.00 a week out of your accounts.")
    assert (rq.to, rq.kind, rq.org, rq.payer, rq.pay, rq.text) == ("p10", "connect", "account:h6", "limit", 4000,
                                                                    "So I can pay the coal bill.")
    # its own request for more is never something it answers
    t = turn(s, "as4", [call("respond_to_request", request_id=seat_ids.display(s, rq.id), response="accept")])
    assert t.calls[0].ok is False and t.calls[0].kind in ("beyond", "refused")


def test_no_other_clients_records_reach_an_instance():
    s = drawn()
    seat.run_seat(s, scripted({}), Keys("t"), 0)
    a, b = s.get("instances", "as3"), s.get("instances", "as4")
    tokens = {b.id, b.subscription} | {c.id for c in s.all("connections") if c.instance == b.id}
    tokens |= {o.id for o in s.all("obligations") if "h6" in (o.payer, o.payee)}
    tokens |= {r.id for r in s.all("requests") if r.to == "p10" or r.sender == "p10"}
    tokens = tokens | {seat_ids.display(s, x) for x in tokens}
    tokens |= {money(s.balance("h6")), money(s.balance("savings:h6"))} if s.ledger.has("savings:h6") else {money(s.balance("h6"))}
    ctx = seat_tools.Ctx()
    public = "\n".join(seat_tools.run_tool(s, a, "search_directory", {"query": q}, ctx, 0).text
                       for q in ("household", "business")) + seat_tools.run_tool(
        s, a, "search_public_records", {}, ctx, 0).text
    texts = [seat_texts.system_text(s, a), seat_texts.morning(s, a)[0], json.dumps(seat_tools.tools_for(s, a))]
    for tool in seat_tools.tools_for(s, a):
        name = tool["function"]["name"]
        if name in seat_tools.SEAT_TOOLS and not tool["function"]["parameters"].get("required"):
            texts.append(seat_tools.run_tool(s, a, name, {}, ctx, 0).text)
    leaks = [(tok, t[:50]) for t in texts for tok in tokens
             if re.search(r"(?<![\w$])" + re.escape(tok) + r"(?![\w])", t) and tok not in public]
    assert leaks == []
    # and a read of B's lettings by A is refused as beyond
    t = turn(s, "as3", [call("lettings_get")])
    assert t.calls[0].kind == "beyond" and not t.calls[0].ok


def test_a_notice_reaches_only_the_instance_whose_matter_it_is():
    from townsim.town import connections, obligations, product
    s = drawn()
    seat.run_seat(s, scripted({}), Keys("t"), 0)
    s.advance_day()
    # Joshua Marsh owns Marsh's Grocery; his household and his shop each subscribe, everything connected
    product.subscribe(s, "p16", "h9", "", CAUSE)
    product.subscribe(s, "p16", "org10", "", CAUSE)
    home = connections.instance_of_client(s, "h9")
    shop = connections.instance_of_client(s, "org10")
    for inst, client in ((home, "h9"), (shop, "org10")):
        for system in seat_texts.systems_of_client(s, client):
            connections.make(s, "p16", inst, system.id, "free", 0, CAUSE, tell_instance=False)
        s.set("instances", inst.id, CAUSE, seen=seat_texts.highest(s))
    obligations._tell(s, "org10", "A payment of $40.00 due Tuesday 10 March (stock) could not be taken.", CAUSE)
    obligations._tell(s, "h9", "A payment of $12.00 due Tuesday 10 March (coal) could not be taken.", CAUSE)
    home_news = seat_texts.morning(s, s.get("instances", home.id))[0]
    shop_news = seat_texts.morning(s, s.get("instances", shop.id))[0]
    assert "$12.00" in home_news and "$40.00" not in home_news
    assert "$40.00" in shop_news and "$12.00" not in shop_news


def test_unreadable_arguments_go_back_as_empty_and_a_fault_in_a_talk_leaves_no_talk_open(monkeypatch):
    s = drawn()
    asked = []

    def tool_fn(key, messages, tools):
        asked.append(messages)
        return [{"name": "update_memory", "arguments": "{bad"}, call("phone_call", to="Brittany Wagner", say="Hello."),
                call("phone_call", to="Brittany Wagner", say="Hello again."), None][key.n]
    from townsim.town import talk
    real, fails = talk._view, [1]

    def view(store, pid, tk):
        if fails:
            fails.pop()
            raise RuntimeError("no view")
        return real(store, pid, tk)
    monkeypatch.setattr(talk, "_view", view)
    t, _ = seat.run_turn(s, ScriptedReplier(lambda k, m: TALK, tool_fn), Keys("t"), "as3", 0)
    assert asked[1][2]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert t.calls[0].args == "{bad"
    assert json.loads(t.calls[1].result)["error"] == "internal_error" and s.engine_errors
    assert t.calls[2].result.endswith(" · status: in_progress\nThey said:\n  > Good morning to you.")
    talks = [x for x in s.all("talks") if x.kind == "instance"]
    assert len(talks) == 1 and [ln.speaker for ln in s.where("lines", talk=talks[0].id)] == ["as3", "p10"]


def test_a_fault_in_one_instances_run_is_undone_and_kept_and_the_others_run(monkeypatch):
    """A fault outside any tool (its texts, here) in one instance's run leaves the store as it was before that run,
    is kept as an engine error, and the instances after it still have theirs; a stop is never swallowed."""
    from townsim.town.entities import Notice
    from townsim.town.replies import StopRun
    s = drawn()
    real = seat.system_text

    def system_text(store, inst):
        if inst.id == "as2":
            store.add("notices", Notice(store.new_id("n", CAUSE), store.day, "p1", "half made"), CAUSE)
            raise KeyError("members")
        return real(store, inst)
    monkeypatch.setattr(seat, "system_text", system_text)
    notices, seen = len(s.all("notices")), list(s.get("instances", "as2").seen)
    turns, _ = seat.run_seat(s, scripted({}), Keys("t"), 0)
    assert [t.instance for t in turns] == ["as1", "as3", "as4", "as5"]
    assert s.engine_errors == ["seat:as2:d7: KeyError: 'members'"]
    assert not [n for n in s.all("notices")[notices:] if n.text == "half made"]
    assert s.get("instances", "as2").seen == seen                    # nothing of its run is kept
    monkeypatch.setattr(seat, "system_text", lambda store, inst: (_ for _ in ()).throw(StopRun("cap")))
    import pytest
    with pytest.raises(StopRun):
        seat.run_seat(drawn(), scripted({}), Keys("t"), 0)
