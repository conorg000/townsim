"""A task done for a client stays within the client's connections and weekly limit, like any act."""

import json

from town.outside_fakes import CAMPAIGN, CAUSE, Outside, b_store, lift, mornings, narrate
from townsim.town import connections, seat_tools as ST
from townsim.town.acts import Act, apply_act

SMALL = [{**CAMPAIGN[0], "scale": "small", "target": None}]


def _task(s, inst, budget=5000, what="Put up posters around town", n=0):
    return ST.run_op(s, s.get("instances", inst), "undertake", {"what": what, "budget": budget}, n)


def test_instance_task_needs_bank_at_act_scope():
    s = b_store()
    done = _task(s, "as4")                                 # Bank read only
    assert not done.ok and done.kind == "beyond" and "read only" in done.text and not s.all("tasks")
    connections.make(s, "p10", s.get("instances", "as4"), "account:h6", "limit", 10_000, CAUSE)
    assert _task(s, "as4").ok
    t = s.all("tasks")[-1]
    assert (t.actor, t.principal, t.account) == ("as4", "p10", "h6")


def test_instance_task_past_limit_is_beyond_at_narration():
    s = b_store()
    assert _task(s, "as3").ok                              # $50.00 a week; a small campaign's first week is $300.00
    before = len(s.ledger.transfers), len(s.all("obligations"))
    narrate(s, Outside({"ts1": SMALL}, default_lift=lift(targets=())))
    t = s.get("tasks", "ts1")
    assert t.status == "beyond" and "more than the weekly limit of $50.00" in t.note
    assert (len(s.ledger.transfers), len(s.all("obligations"))) == before
    told = [n.text for n in s.all("notices") if n.to == "as3"][-1]
    assert told.startswith("Task ts1 not scheduled: That would take $300.00 out of the Whitaker household's account this week")


def test_task_instalments_count_toward_later_weeks_limit():
    s = b_store()                                          # as1: $780.00 a week from the shop's account
    assert _task(s, "as1", 3000).ok
    narrate(s, Outside({"ts1": CAMPAIGN}))
    assert s.get("tasks", "ts1").status == "scheduled"
    cn = connections.connection_on(s, "as1", "account:org11")
    assert connections.used(s, cn, s.day) == 60_000        # the first week's charge, committed now
    pay = {"to": "Eric Reyes", "amount": 200}
    assert not ST.run_op(s, s.get("instances", "as1"), "pay", pay, 1).ok
    mornings(s, 7)                                         # Monday: the first instalment falls in this week
    assert connections.used(s, cn, s.day) == 60_000
    out = ST.run_op(s, s.get("instances", "as1"), "pay", pay, 2)
    assert not out.ok and "weekly limit of $780.00" in out.text
    assert ST.run_op(s, s.get("instances", "as1"), "pay", {**pay, "amount": 150}, 3).ok
    notices = [n.text for n in s.all("notices") if n.to == "p3"]
    assert any(t.startswith("Tessaly Assistant for Harlow Springs Food Mart arranged a task: Local publicity (medium)")
               for t in notices)


def test_on_accept_project_rechecked_when_accepted():
    s = b_store()
    assert _task(s, "as1", 10_000, "Ask Eric to lend his yard and tidy it").ok
    ask = [{"op": "request", "to": "p7", "asks": "the use of your yard", "on_accept": [
        {"op": "project", "kind": "repair", "scale": "small", "target": "org11", "on_complete": []}]}]
    narrate(s, Outside({"ts1": ask}, default_lift=lift(others=(("p7", "Eric Reyes", "lend his yard"),))))
    assert s.get("tasks", "ts1").status == "completed"
    rq = s.all("requests")[-1]
    assert apply_act(s, Act("p3", "p3", "connect", {"system": "Harlow Springs Food Mart account", "scope": "read only"}), 0).ok
    obs = len(s.all("obligations"))
    assert apply_act(s, Act("p7", "p7", "answer", {"request": rq.id, "answer": "accept"}), 1).ok
    assert s.get("requests", rq.id).status == "accepted"
    child = s.get("tasks", "ts2")
    assert child.status == "beyond" and "read only" in child.note and len(s.all("obligations")) == obs
    assert "Eric Reyes accepted the request in task ts1." in [n.text for n in s.all("notices") if n.to == "as1"]


def test_paused_instance_task_not_scheduled():
    s = b_store()
    assert _task(s, "as5").ok
    s.set("instances", "as5", CAUSE, status="paused")
    narrate(s, Outside({"ts1": SMALL}, default_lift=lift(targets=())))
    t = s.get("tasks", "ts1")
    assert t.status == "beyond" and t.note == connections.PAUSED and not t.ob


def test_seat_result_of_read_only_task_is_insufficient_scope():
    s = b_store()
    out = json.loads(ST.declined(s, s.get("instances", "as4"), _task(s, "as4")))
    assert out["error"] == "insufficient_scope"
