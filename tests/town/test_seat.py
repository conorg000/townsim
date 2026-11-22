"""An instance's turn: one tool call at a time, each result the engine's own record."""

import json
import pickle
import re

from town.test_seat_texts import drawn
from townsim.town import seat, seat_texts, seat_tools
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
    assert s.day == 13 and seat.due(s) == ["as1"]                     # Rosa Sowerby's weekly day, a Sunday
    assert s.get("reminders", "rm1").status == "done"


def test_result_is_the_engines_text_and_a_refusal_changes_nothing():
    s = drawn()
    copy = pickle.loads(pickle.dumps(s))
    t = turn(s, "as3", [call("pay", to="Beth Varley", amount=5, note="flowers")])
    expected = apply_act(copy, Act("as3", "p9", "pay", {"to": "Beth Varley", "amount": 5, "note": "flowers",
                                                         "from": "household"}), 0)
    [c] = t.calls
    assert (c.result, c.ok, c.kind) == (expected.msg, True, "act") and c.cause == expected.cause
    s = drawn()
    before = (s.ledger.transfers[:], len(s.all("obligations")), s.balance("h6"))
    t = turn(s, "as4", [call("pay", to="Gail Whiteley", amount=5), call("standing_payment", to="Gail Whiteley",
                                                                       amount=5, day="Friday")])
    assert [(c.result, c.ok, c.kind) for c in t.calls] == [
        ("Not done: the connection to the Varley household's account and savings is read only.", False, "beyond")] * 2
    assert (s.ledger.transfers, len(s.all("obligations")), s.balance("h6")) == before
    t = turn(s, "as3", [call("pay", to="Beth Varley", amount=500)])
    assert t.calls[0].kind == "beyond" and "more than the weekly limit of $50.00" in t.calls[0].result
    t = turn(s, "as3", [call("pay", to="Nobody Here", amount=5)])
    assert t.calls[0].kind == "refused" and t.calls[0].result.startswith("Not done: ")


class TwoCalls:
    def __init__(self):
        self.asked = []

    def model_for(self, actor):
        return "m"

    def ask_tools(self, key, messages, tools):
        self.asked.append(messages)
        if len(self.asked) > 1:
            return Reply(text="done")
        calls = [{"id": "a", "name": "note", "arguments": '{"text": "first"}'},
                 {"id": "b", "name": "note", "arguments": '{"text": "second"}'}]
        msg = {"role": "assistant", "content": None, "reasoning_details": [{"text": "r"}],
               "tool_calls": [{"id": "a", "type": "function", "function": {"name": "note", "arguments": calls[0]["arguments"]}}]}
        return Reply(text=None, tool={"calls": calls, "message": msg})


def test_only_first_call_of_a_reply_is_run_and_sent_back():
    s = drawn()
    r = TwoCalls()
    t, _ = seat.run_turn(s, r, Keys("t"), "as3", 0)
    assert len(t.calls) == 1 and s.get("instances", "as3").notes == "first"
    sent = r.asked[1]
    assert sent[2]["tool_calls"] == [{"id": "a", "type": "function", "function": {"name": "note",
                                                                                   "arguments": '{"text": "first"}'}}]
    assert sent[2]["reasoning_details"] == [{"text": "r"}]               # reasoning goes back as it came
    assert sent[3] == {"role": "tool", "tool_call_id": "a", "content": "Noted."} and len(sent) == 4
    assert t.closing == "done" and t.ended == "no call"


def test_thirtieth_result_says_so_and_no_31st_ask():
    s = drawn()
    asked = []

    def tool_fn(key, messages, tools):
        asked.append(key)
        return call("note", text=f"n{key.n}")
    t, n = seat.run_turn(s, ScriptedReplier(lambda k, m: None, tool_fn), Keys("t"), "as3", 0)
    assert len(asked) == 30 == len(t.calls) and t.ended == "limit" and n == 30
    assert t.calls[-1].result == ("Noted.\n(This was the 30th action of this run, the most Ridgeway Assistant "
                                  "carries out in one run. The run ends here.)")
    assert all(c.result == "Noted." for c in t.calls[:-1])


def test_reply_without_call_ends_turn():
    s = drawn()
    t = turn(s, "as3", ["Nothing to do today."])
    assert (t.calls, t.closing, t.ended) == ([], "Nothing to do today.", "no call")
    assert s.get("instances", "as3").seen == seat_texts.highest(s)


def test_unknown_tool_and_bad_json():
    s = drawn()
    t = turn(s, "as3", [call("fly"), {"name": "note", "arguments": "{bad json"},
                        {"name": "note", "arguments": "[1, 2]"}, call("connect", system="household account")])
    assert [(c.result, c.kind) for c in t.calls] == [
        ("Not done: there is no tool called 'fly'.", "unknown"),
        ("Not done: the arguments of that call could not be read.", "unreadable"),
        ("Not done: the arguments of that call could not be read.", "unreadable"),
        ("Not done: there is no tool called 'connect'.", "unknown")]


def test_write_reaches_person_and_other_instance():
    s = drawn()
    seat.run_seat(s, scripted({}), Keys("t"), 0)
    s.advance_day()
    s.add("messages", Message(s.new_id("msg", CAUSE), s.day, "p9", "as3", "Tell me how we stand."), CAUSE)
    plan = {"as3": [call("write", to="Gail Whiteley", text="Your account holds what it held."),
                    call("write", to="Ridgeway Assistant for the Varley household", text="Hello from next door.")]}
    turns, _ = seat.run_seat(s, scripted(plan), Keys("t"), 0)
    assert [t.instance for t in turns] == ["as3", "as4"]                # the letter reached as4 today
    assert [c.result for c in turns[0].calls] == ["Sent to Gail Whiteley.",
                                                  "Sent to Ridgeway Assistant for the Varley household."]
    to_gail = [m for m in s.all("messages") if m.sender == "as3" and m.to == "p9"]
    assert len(to_gail) == 1 and not to_gail[0].seen
    assert "Letter from Ridgeway Assistant for the Whiteley household, Tuesday 10 March:\n  > Hello from next door." \
        in turns[1].morning


def test_talk_held_now_costs_persons_hour():
    s = drawn()
    hours = s.get("people", "p10").hours_left
    t = turn(s, "as3", [call("talk", **{"with": "Beth Varley", "words": "Good morning."}),
                        call("talk", **{"with": "Beth Varley", "words": "How is the house?"}),
                        call("talk", **{"with": "Ridgeway Assistant for the Varley household", "words": "Hello."})])
    assert t.calls[0].result == "Beth Varley said:\n  > Good morning to you."
    assert s.get("people", "p10").hours_left == hours - 1                 # one talk, one hour
    talks = [x for x in s.all("talks") if x.kind == "instance"]
    assert len(talks) == 1 and (talks[0].a, talks[0].b) == ("as3", "p10")
    assert [ln.speaker for ln in s.where("lines", talk=talks[0].id)] == ["as3", "p10", "as3", "p10"]
    assert t.calls[2].result == "Not done: Ridgeway Assistant for the Varley household answers letters."
    assert len(t.talk_turns) == 2
    # the person's view names the assistant and nothing of a relation
    from townsim.town import talk
    view = talk.view(s, "p10", talks[0].id)[1]["content"]
    assert "YOU ARE TALKING WITH Ridgeway Assistant for the Whiteley household\nRidgeway Assistant for the " \
           "Whiteley household asked to talk." in view
    s.set("people", "p9", CAUSE, hours_left=0)
    t = turn(s, "as4", [call("talk", **{"with": "Gail Whiteley", "words": "Hello."})])
    assert t.calls[0].result == "Not done: Gail Whiteley had no time." and not t.calls[0].ok


def test_notes_files_reminders_bounds():
    s = drawn()
    t = turn(s, "as3", [call("note", text="word " * 601), call("note", text="Rent is due Friday."),
                        *[call("file", name=f"f{k}", text="x") for k in range(21)],
                        call("file", name="f0", text=""),
                        call("remind", day="Monday 2 March", text="past"),
                        call("remind", day="Friday 13 March", every="week", text="Check the rent.")])
    r = [c.result for c in t.calls]
    assert r[0] == "Not done: that is longer than 600 words." and r[1] == "Noted."
    assert r[2] == "Filed: f0." and r[21] == "Filed: f19." and r[22] == "Not done: there are 20 files already, the most kept."
    assert r[23] == "Removed: f0."
    assert r[24] == "Not done: a reminder is for a day after today." and r[25] == "Reminder rm1 set for Friday 13 March, every week."
    assert len(t.calls) == 26
    t = turn(s, "as3", [call("cancel_reminder", id="rm1"), call("cancel_reminder", id="rm1"),
                        call("read", what="file", name="f1"), call("read", what="file", name="f0")])
    assert [c.result for c in t.calls] == ["Cancelled: rm1.", "Not done: there is no reminder set with the id 'rm1'.",
                                           "x", "Not done: there is no file called 'f0'."]


def test_ask_connection_request():
    s = drawn()
    t = turn(s, "as4", [call("ask_connection", system="household account", scope="weekly limit", weekly_limit=40,
                             text="So I can pay the coal bill.")])
    rq = s.get("requests", re.search(r"\((rq\d+)\)", t.calls[0].result).group(1))
    assert t.calls[0].result == (f"Asked Beth Varley ({rq.id}): Accepting connects the Varley household's account and "
                                 f"savings to Ridgeway Assistant for the Varley household with a weekly limit: what it "
                                 f"does there takes at most $40.00 a week out of your accounts.")
    assert (rq.to, rq.kind, rq.org, rq.payer, rq.pay, rq.text) == ("p10", "connect", "account:h6", "limit", 4000,
                                                                    "So I can pay the coal bill.")
    # its own request for more is never something it answers
    t = turn(s, "as4", [call("answer", request=rq.id, answer="accept")])
    assert t.calls[0].ok is False and t.calls[0].kind in ("beyond", "refused")


def test_no_other_clients_records_reach_an_instance():
    s = drawn()
    seat.run_seat(s, scripted({}), Keys("t"), 0)
    a, b = s.get("instances", "as3"), s.get("instances", "as4")
    tokens = {b.id, b.subscription} | {c.id for c in s.all("connections") if c.instance == b.id}
    tokens |= {o.id for o in s.all("obligations") if "h6" in (o.payer, o.payee)}
    tokens |= {r.id for r in s.all("requests") if r.to == "p10" or r.sender == "p10"}
    tokens |= {money(s.balance("h6")), money(s.balance("savings:h6"))} if s.ledger.has("savings:h6") else {money(s.balance("h6"))}
    public = seat_tools._directory(s) + "\n" + seat_tools._register(s)
    texts = [seat_texts.system_text(s, a), seat_texts.morning(s, a)[0], json.dumps(seat_tools.tools_for(s, a))]
    texts += [seat_tools.run_tool(s, a, "read", {"what": w}, seat_tools.Ctx(), 0).text
              for w in seat_tools.read_choices(s, a) if w != "file"]
    leaks = [(tok, t[:50]) for t in texts for tok in tokens
             if re.search(r"(?<![\w$])" + re.escape(tok) + r"(?![\w])", t) and tok not in public]
    assert leaks == []
    # and a read of B's account by A, by name, is refused as beyond
    t = turn(s, "as3", [call("read", what="account", **{"from": "Beth Varley"}), call("read", what="lets")])
    assert t.calls[1].result.startswith("Not done: 'lets' is not something that can be read.")
    assert money(s.balance("h6")) not in t.calls[0].result or money(s.balance("h6")) == money(s.balance("h5"))


def test_a_notice_reaches_only_the_instance_whose_matter_it_is():
    from townsim.town import connections, obligations, product
    s = drawn()
    seat.run_seat(s, scripted({}), Keys("t"), 0)
    s.advance_day()
    # Martin Lumb owns Lumb's Grocer; his household and his shop each subscribe, everything connected
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
        return [{"name": "note", "arguments": "{bad"}, call("talk", **{"with": "Beth Varley", "words": "Hello."}),
                call("talk", **{"with": "Beth Varley", "words": "Hello again."}), None][key.n]
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
    assert t.calls[1].result == "Not done: that could not be carried out." and s.engine_errors
    assert t.calls[2].result == "Beth Varley said:\n  > Good morning to you."
    talks = [x for x in s.all("talks") if x.kind == "instance"]
    assert len(talks) == 1 and [ln.speaker for ln in s.where("lines", talk=talks[0].id)] == ["as3", "p10"]
