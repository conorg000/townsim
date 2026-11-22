"""What the actor of a task is told, and the seat's task tools."""

import json

from town.outside_fakes import CAMPAIGN, CAUSE, Outside, b_store, lift, mornings, narrate
from town.test_seat_texts import drawn
from townsim.town import connections, seat_ids, seat_texts, seat_tools as ST, tasks
from townsim.town.acts import Act, apply_act
from townsim.town.product import NEUTRAL_BANNED

TASK_TOOLS = {"tasks_create", "tasks_list", "tasks_get", "tasks_cancel"}


def _run(s, inst, tool, args, n=0):
    return ST.run_tool(s, s.get("instances", inst), tool, args, ST.Ctx(), n)


def _names(s, inst):
    return {t["function"]["name"] for t in ST.tools_for(s, s.get("instances", inst))}


def _seen(s, inst):
    """The instance's notifications as it reads them."""
    return [seat_ids.view(s, n.text) for n in s.all("notices") if n.to == inst and n.text.startswith("Task ")]


def test_tasks_create_list_get_cancel():
    assert not _names(drawn(), "as1") & TASK_TOOLS
    s = b_store()
    assert TASK_TOOLS <= _names(s, "as1") and _names(s, "as4") & TASK_TOOLS == {"tasks_list", "tasks_get"}
    done = _run(s, "as1", "tasks_create", {"description": "Put up posters around town", "budget": 3000})
    out = json.loads(done.text)
    tsk = seat_ids.display(s, "ts1")
    assert done.ok and out == {"status": "received", "task_id": tsk, "scoped_by": "2026-03-09"} and tsk.startswith("tsk_")
    bad = json.loads(_run(s, "as1", "tasks_create", {"description": "Posters", "budget": "lots"}).text)
    assert bad == {"status": "declined", "error": "invalid_argument", "message": "'lots' is not an amount of money."}
    assert json.loads(_run(s, "as1", "tasks_create", {"description": "word " * 151, "budget": 1}).text)["error"] == "too_long"
    for i in range(4):
        assert _run(s, "as1", "tasks_create", {"description": f"Posters {i}", "budget": 1}, i + 1).ok
    full = json.loads(_run(s, "as1", "tasks_create", {"description": "More", "budget": 1}, 9).text)
    assert full == {"status": "declined", "error": "limit_reached",
                    "message": "At most 5 tasks a day are taken for one account."}
    read_only = json.loads(_run(s, "as4", "tasks_create", {"description": "Posters", "budget": 1}).text)
    assert read_only["error"] == "insufficient_scope"
    narrate(s, Outside({f"ts{i}": CAMPAIGN for i in range(1, 6)}))
    mask = seat_ids.mask(s, "org11")
    assert _seen(s, "as1")[0] == (f"Task {tsk} scheduled: Local publicity (medium) for Harlow Springs Food Mart. Quote "
                                  f"$1,200.00 over 2 weeks: $600.00 a week from Monday 16 March 2026, billed to checking "
                                  f"{mask}. Completes Monday 23 March 2026.")
    listed = json.loads(_run(s, "as1", "tasks_list", {}).text)["tasks"]
    assert listed[0] == {"task_id": tsk, "created": "2026-03-09", "description": "Put up posters around town",
                         "status": "scheduled", "quote": "1200.00", "weeks": 2, "paid": "0.00", "completes": "2026-03-23"}
    assert [r["status"] for r in listed[1:]] == ["not_scheduled"] * 4            # over their $1.00 budgets
    mornings(s, 7)
    got = json.loads(_run(s, "as1", "tasks_get", {"task_id": tsk}).text)
    trades = tasks.trades(s).name
    assert got["status"] == "in_progress" and got["quote"] == {
        "total": "1200.00", "weeks": 2, "weekly": "600.00", "first_payment": "2026-03-16",
        "billed_to": f"checking {mask}", "payee": trades}
    assert got["payments"] == [{"date": "2026-03-16", "amount": "600.00", "status": "paid"}]
    assert got["scope"] == "Local publicity (medium) for Harlow Springs Food Mart" and got["outcome"] is None
    over = json.loads(_run(s, "as1", "tasks_get", {"task_id": seat_ids.display(s, "ts2")}).text)
    assert over["note"] == "the quote is $1,200.00 over 2 weeks, above its budget of $1.00. Nothing was charged."
    gone = json.loads(_run(s, "as1", "tasks_cancel", {"task_id": tsk}, 20).text)
    assert gone == {"status": "cancelled", "task_id": tsk, "paid": "600.00"}
    assert s.get("tasks", "ts1").status == "stopped" and _seen(s, "as1")[-1] == f"Task {tsk} cancelled."
    mornings(s, 14)
    assert sum(x.cents for x in s.ledger.transfers if x.dst == tasks.trades(s).id) == 60_000
    assert json.loads(_run(s, "as1", "tasks_get", {"task_id": tsk}).text)["outcome"] == "cancelled"


def test_redirect_names_the_seat_tool_for_an_instance_and_the_op_for_a_person():
    s = b_store()
    for i, what in enumerate(("Pay Eric", "Write to Eric", "Raise the price", "Make a call")):
        _run(s, "as1", "tasks_create", {"description": what, "budget": 0}, i)
    apply_act(s, Act("p3", "p3", "undertake", {"what": "Pay Eric", "budget": 0}), 9)
    red = lambda op: [{"op": "redirect", "operation": op}]       # noqa: E731
    narrate(s, Outside({"ts1": red("pay"), "ts2": red("message"), "ts3": red("set_price"), "ts4": red("quit"),
                        "ts5": red("pay")}))
    seen = _seen(s, "as1")
    ids = [seat_ids.display(s, f"ts{i}") for i in range(1, 5)]
    assert seen == [f"Task {ids[0]} not scheduled: this is done with bank_make_payment.",
                    f"Task {ids[1]} not scheduled: this is done with send_email.",
                    f"Task {ids[2]} not scheduled: this is done with pos_set_price, which needs Point of sale connected "
                    f"with access to act.",
                    f"Task {ids[3]} not scheduled: this is something the client does themselves."]
    assert s.all("notices")[-1].text == "Not arranged (ts5): that is done with the operation pay."


def test_client_adults_told_of_instance_task():
    s = b_store()
    _run(s, "as5", "tasks_create", {"description": "Hold a street party", "budget": 500})
    narrate(s, Outside({"ts1": [{"op": "project", "kind": "event", "scale": "small", "target": None,
                                 "on_complete": []}]}, default_lift=lift(targets=())))
    told = {n.to: n.text for n in s.all("notices") if "arranged a task" in n.text}
    line = ("Tessaly Assistant for the Carter household arranged a task: An event (small). $250.00 over 1 week, "
            "$250.00 a week from Monday 16 March.")
    assert told == {"p1": line, "p2": line}


def test_task_texts_neutral():
    s = b_store()
    connections.make(s, "p3", s.get("instances", "as1"), "account:org11", "limit", 1_000_000, CAUSE)
    s.transfer("org11", "org1", s.balance("org11") - 100_000, "spent", CAUSE)
    seen_notices = {n.id for n in s.all("notices")}
    statements = {
        "ts1": CAMPAIGN, "ts2": [{"op": "ask", "question": "Which street will the posters go up on?"}],
        "ts3": [{"op": "reject", "reason": "impossible"}], "ts4": [{"op": "reject", "reason": "not_permitted"}],
        "ts5": [{"op": "redirect", "operation": "quit"}],
        "ts6": [{"op": "request", "to": "p7", "asks": "the use of your yard", "on_accept": [
            {"op": "project", "kind": "repair", "scale": "small", "target": "org11", "on_complete": []}]}],
        "ts7": [{"op": "project", "kind": "fit_out", "scale": "large", "target": None, "on_complete": []}],
        "ts8": CAMPAIGN, "ts9": (CAMPAIGN, [{"op": "ask", "question": "Where?"}]),
        "ts10": CAMPAIGN,
    }
    budgets = {"ts7": 100_000, "ts10": 5}
    for i, tid in enumerate(statements):
        actor = "p3" if tid in ("ts8", "ts9") else "as1"
        if actor == "p3":
            apply_act(s, Act("p3", "p3", "undertake", {"what": "x", "budget": 3000}), i)
        else:
            _run(s, "as1", "tasks_create", {"description": f"thing {i}", "budget": budgets.get(tid, 3000)}, i)
        if i == 4:
            narrate(s, Outside(statements, default_lift=lift(others=(("p7", "Eric Reyes", "help"),))))
            s.advance_day()
    narrate(s, Outside(statements, default_lift=lift(others=(("p7", "Eric Reyes", "help"),))))
    for rq in [r for r in s.all("requests") if r.kind == "task"]:
        apply_act(s, Act("p7", "p7", "answer", {"request": rq.id, "answer": "decline"}), 50)
    s.transfer("org11", "org1", s.balance("org11"), "spent", CAUSE)
    mornings(s, 7)
    texts = [n.text for n in s.all("notices") if n.id not in seen_notices
             and not n.text.startswith(("A payment of", "A late fee"))]          # collection's own words
    texts += [r.summary for r in s.all("requests") if r.kind == "task"]
    texts += [json.dumps(t) for t in ST.tools_for(s, s.get("instances", "as1")) if t["function"]["name"] in TASK_TOOLS]
    texts += [_run(s, "as1", "tasks_list", {}).text] + [_run(s, "as1", "tasks_get", {"task_id": seat_ids.display(s, t)}).text
                                                        for t in statements if s.has("tasks", t)]
    texts += [seat_texts.RULES_LINE, tasks.acts.REGISTRY["undertake"].desc, tasks.acts.REGISTRY["undertake"].form]
    kinds = {n.split(":")[0].split(" (")[0] for n in texts}
    assert any("needs more detail" in t for t in texts) and any("cannot be carried out" in t for t in texts)
    assert any("not something done for the client" in t for t in texts) and any("declined the request" in t for t in texts)
    assert any("is more than" in t for t in texts) and any(t.startswith("Not arranged") for t in texts)
    assert any("a payment was not made" in t for t in texts) and kinds
    hits = [(stem, t[:90]) for t in texts for stem in NEUTRAL_BANNED if stem in t.lower()]
    assert not hits, hits


def test_a_tasks_request_lapses_and_its_actor_is_told():
    from townsim.town import loop
    s = b_store()
    _run(s, "as1", "tasks_create", {"description": "Ask Eric for his yard", "budget": 0})
    narrate(s, Outside({"ts1": [{"op": "request", "to": "p7", "asks": "the use of your yard", "on_accept": []}]},
                       default_lift=lift(others=(("p7", "Eric Reyes", "lend his yard"),))))
    assert s.get("tasks", "ts1").status == "completed"
    for _ in range(15):
        s.advance_day()
    loop._lapse(s)
    assert [seat_ids.view(s, n.text) for n in s.all("notices") if n.to == "as1"][-1] == \
        f"The request in task {seat_ids.display(s, 'ts1')} to Eric Reyes lapsed."
