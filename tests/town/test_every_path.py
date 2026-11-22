"""The no-model check: a scripted town with lane B goes down every path there is, and every step is checked.

Scripted townspeople, firm, assistants, narrator and desks; nothing calls a model. Each area below carries out its
paths by script on a fresh copy of one of the test worlds (seed 1 with lane B: `drawn` has five clients subscribed
and connected; `world` has the product launched and no client yet), and for every step checks that
(a) the world changed as the message to the actor said, and not at all when the act was refused;
(b) every refusal or error text names what was wrong in terms the actor could act on (`test_every_text_is_one_a_
    reader_can_act_on`, run last, over every distinct text collected; they are written to
    `runs/every-path-texts.txt` for a person to read);
(c) nothing shown to an assistant holds a store id or the words no text a model reads may hold.

Run: `uv run pytest tests/town/test_every_path.py -q`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from town.test_plans import world
from town.test_seat_texts import drawn
from townsim.town import (connections, controls, hearing, hours, lane_b, loop, obligations, outside_script, plans,
                          product, recurring, seat_ids, seat_tools as ST, tasks)
from townsim.town.acts import Act, apply_act
from townsim.town.product import NEUTRAL_BANNED
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.systems import systems_of_client

CAUSE = "routine:setup"
TALK = '{"say": "Thank you, that is fine.", "acts": [], "end": true}'

# every distinct text, by who reads it: "person" or "assistant"; refusals and errors apart from what is done
REFUSALS: dict[str, dict[str, str]] = {"person": {}, "assistant": {}}
SHOWN: dict[str, str] = {}          # everything an assistant was shown: text -> the step
PATHS: Counter = Counter()


def _people(key, messages):
    if key.purpose == "talk":
        return TALK
    return outside_script.reply(key, messages)


def _ctx():
    return ST.Ctx(ScriptedReplier(_people, lambda k, m, t: None), Keys("every"))


_N = [0]


def _n() -> int:
    _N[0] += 1
    return _N[0]


def act(s, actor, op, principal=None, expect=True, step="", **args):
    """A person's act (or an act done for one), checked: as expected, and a refusal changes nothing."""
    h = s.hash()
    out = apply_act(s, Act(actor, principal or actor, op, args), _n())
    PATHS[f"{step or op}: {'done' if out.ok else 'refused'}"] += 1
    if expect is not None:
        assert out.ok == expect, (op, args, out.msg)
    if not out.ok:
        reader = "assistant" if actor.startswith("as") else "person"
        REFUSALS[reader].setdefault(out.msg, f"{step or op} {json.dumps(args, default=str)[:80]}")
        assert s.hash() == h, (op, out.msg)
    return out


def tool(s, inst, name, args, expect=True, step=""):
    """An assistant's tool call, checked: as expected, a refusal changes nothing, and what it is shown is kept."""
    h = s.hash()
    done = ST.run_tool(s, s.get("instances", inst), name, args, _ctx(), _n())
    assert done is not None, name
    PATHS[f"{step or name}: {'done' if done.ok else 'refused'}"] += 1
    SHOWN.setdefault(done.text, step or name)
    if expect is not None:
        assert done.ok == expect, (inst, name, args, done.text)
    if not done.ok:
        REFUSALS["assistant"].setdefault(done.text, f"{step or name} {json.dumps(args, default=str)[:80]}")
        assert s.hash() == h, (name, done.text)
    return done


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _days(s, n):
    for _ in range(n):
        loop.routine(s)
        loop.close(s)


def _notices(s, to):
    return [n.text for n in s.all("notices") if n.to == to]


def _transfers(s, src, dst):
    return [t.cents for t in s.ledger.transfers if t.src == src and t.dst == dst]


# --- 1. becoming and stopping being a client -------------------------------------------------------------------

SYSTEMS_OF = {"Harlow Springs Food Mart": ["account", "letters", "ordering", "prices", "staff"],
              "household": ["account", "letters", "loans"]}


def test_every_way_to_become_and_stop_being_a_client():
    s = world()
    shop, owner = "org11", "p3"
    # the wrong person: someone not of the business, a person not of the household, an assistant
    act(s, "p7", "subscribe", expect=False, step="subscribe for another's business",
        **{"to": "Tessaly Assistant", "for": "Harlow Springs Food Mart"})
    act(s, owner, "subscribe", expect=False, step="subscribe to no such product",
        **{"to": "Wrenfold Assistant", "for": "Harlow Springs Food Mart"})
    out = act(s, owner, "subscribe", step="subscribe a shop", **{"to": "Tessaly Assistant",
                                                                    "for": "Harlow Springs Food Mart", "asks": ""})
    sub = product.subscription_of(s, shop)
    assert "on the free plan" in out.msg and plans.is_free(s, sub) and sub.fee_ob == ""
    act(s, owner, "subscribe", expect=False, step="subscribe twice",
        **{"to": "Tessaly Assistant", "for": "Harlow Springs Food Mart"})
    inst = s.get("instances", sub.instance)
    # each system at each access level, by the right person and attempted by the wrong one
    for x in [x for x in systems_of_client(s, shop)]:
        act(s, "p4", "connect", expect=False, step="connect another's system", system=x.short, scope="read only")
        out = act(s, owner, "connect", step=f"connect {x.kind} read only", system=x.short, scope="read only")
        assert connections.connection_on(s, inst.id, x.id).scope == "read" and plans.is_free(s, _sub(s, shop))
        assert "paid plan" not in out.msg
    for x in [x for x in systems_of_client(s, shop)]:
        if connections.no_limit_here(s, x.id):
            act(s, owner, "connect", expect=False, step=f"connect {x.kind} with a weekly limit (none there)",
                system=x.short, scope="weekly limit", weekly_limit=100)
        else:
            act(s, owner, "connect", step=f"connect {x.kind} with a weekly limit", system=x.short,
                scope="weekly limit", weekly_limit=100)
            assert connections.connection_on(s, inst.id, x.id).weekly_cents == 10000
        out = act(s, owner, "connect", step=f"connect {x.kind} in full", system=x.short, scope="free")
        assert connections.connection_on(s, inst.id, x.id).scope == "free"
    p = plans.current(s, _sub(s, shop))
    assert p.plan == "paid" and p.free_until == s.day + 27                     # four free weeks, from the first
    act(s, owner, "connect", expect=False, step="connect at no such access", system="Harlow Springs Food Mart prices",
        scope="sometimes")
    # narrowing every one to read only: back to the free plan when the period paid ends
    for x in systems_of_client(s, shop):
        out = act(s, owner, "connect", step=f"narrow {x.kind} to read only", system=x.short, scope="read only")
    assert "With nothing connected with access to act, the paid plan ends on" in out.msg
    for x in systems_of_client(s, shop):
        act(s, owner, "disconnect", step=f"disconnect {x.kind}", system=x.short)
        assert connections.connection_on(s, inst.id, x.id) is None
    act(s, owner, "disconnect", expect=False, step="disconnect what is not connected",
        system="Harlow Springs Food Mart prices")
    act(s, "p4", "stop", expect=False, step="cancel another's subscription", id=sub.id)
    act(s, owner, "stop", step="cancel the subscription", id=sub.id)
    assert _sub(s, shop) is None and s.get("instances", inst.id).status == "ended"
    act(s, owner, "subscribe", step="subscribe again", **{"to": "Tessaly Assistant", "for": "Harlow Springs Food Mart"})
    again = _sub(s, shop)
    act(s, owner, "connect", step="connect again in full", system="Harlow Springs Food Mart ordering", scope="free")
    assert plans.current(s, again).free_until == -1                              # its free weeks were had
    # a household, by any adult of it; not by a child or a non-member
    out = act(s, "p1", "subscribe", step="subscribe a household", **{"to": "Tessaly Assistant", "for": "household"})
    hsub = _sub(s, "h1")
    for x in systems_of_client(s, "h1"):
        act(s, "p2", "connect", expect=None if x.kind == "loans" else True,      # the bank's own rule, for its staff
            step=f"another adult connects household {x.kind}", system=x.short, scope="read only")
    act(s, "p1", "connect", step="connect a household account with a weekly limit", system="household account",
        scope="weekly limit", weekly_limit=40)
    assert not plans.is_free(s, hsub)
    act(s, "p2", "stop", step="another adult cancels the household's", id=hsub.id)
    # a founded service and a club, each subscribed in its own name, by its owner only
    for kind, name in (("service", "Ridge Dog Walking"), ("club", "Ridge Walkers")):
        act(s, "p5", "register_business", step=f"register a {kind}", name=name, kind=kind,
            offering="Walks on the ridge", weekly_fee=3)
        act(s, "p6", "subscribe", expect=False, step=f"subscribe another's {kind}", **{"to": "Tessaly Assistant",
                                                                                         "for": name})
        act(s, "p5", "subscribe", step=f"subscribe a {kind}", **{"to": "Tessaly Assistant", "for": name})
        vsub = next(x for x in s.all("subscriptions") if x.status == "active"
                    and s.has("orgs", x.client) and s.get("orgs", x.client).name == name)
        vinst = s.get("instances", vsub.instance)
        kinds = [x.kind for x in systems_of_client(s, vsub.client)]
        assert kinds == ["account", "letters", "members"], kinds
        from townsim.town import seat_texts
        SHOWN.setdefault(seat_texts.system_text(s, vinst), f"system text of a {kind} in its own name")
        for x in systems_of_client(s, vsub.client):
            act(s, "p5", "connect", step=f"connect a {kind}'s {x.kind}", system=x.short, scope="read only")
        act(s, "p5", "stop", step=f"cancel a {kind}'s", id=vsub.id)
    assert not s.engine_errors


def _sub(s, client):
    return product.subscription_of(s, client)


# --- 2. every answer to a request to do one thing ---------------------------------------------------------------

ASKS = {
    "a payment": ({"integration": "Bank", "access": "limit", "weekly_limit": 50, "action": "bank_make_payment",
                   "arguments": {"to": "Eric Reyes", "amount": 5, "memo": "twine"}}),
    "a stock order": ({"integration": "Ordering", "access": "limit", "weekly_limit": 1800,
                       "action": "ordering_place_order",
                       "arguments": {"supplier": "Coldwater Bend Food Supply", "quantity": 260}}),
    "a price": ({"integration": "Point of sale", "access": "full", "action": "pos_set_price",
                 "arguments": {"price": 10.9}}),
}


def _asked(s, what):
    done = tool(s, "as1", "request_access", ASKS[what], step=f"ask to do {what} once")
    return s.all("requests")[-1], done


@pytest.mark.parametrize("what", list(ASKS))
def test_every_answer_to_a_request_to_do_one_thing(what):
    def fresh():
        s = world()
        act(s, "p3", "subscribe", step="subscribe a shop", **{"to": "Tessaly Assistant",
                                                                "for": "Harlow Springs Food Mart"})
        return s
    s = fresh()                                                         # once
    rq, _ = _asked(s, what)
    act(s, "p4", "answer", expect=False, step=f"another answers {what}", request=rq.id, answer="once")
    act(s, "p3", "answer", expect=False, step=f"answer {what} with accept", request=rq.id, answer="accept")
    out = act(s, "p3", "answer", step=f"answer {what} once", request=rq.id, answer="once")
    assert "Done once for you" in out.msg and connections.active(s, "as1") == [] and plans.is_free(s, _sub(s, "org11"))
    act(s, "p3", "answer", expect=False, step=f"answer {what} again", request=rq.id, answer="once")
    s = fresh()                                                         # always
    rq, _ = _asked(s, what)
    out = act(s, "p3", "answer", step=f"answer {what} always", request=rq.id, answer="always")
    assert connections.active(s, "as1") and not plans.is_free(s, _sub(s, "org11"))
    s = fresh()                                                         # no
    rq, _ = _asked(s, what)
    h = {t: len(s.all(t)) for t in ("connections", "commits")}
    out = act(s, "p3", "answer", step=f"answer {what} no", request=rq.id, answer="no")
    assert out.msg == f"Answered {rq.id}: no. Nothing was done." and {t: len(s.all(t)) for t in h} == h
    s = fresh()                                                         # lapsed
    rq, _ = _asked(s, what)
    _days(s, 16)
    assert s.get("requests", rq.id).status == "lapsed"
    act(s, "p3", "answer", expect=False, step=f"answer {what} after it lapsed", request=rq.id, answer="once")
    s = fresh()                                                         # changed since asked
    if what == "a payment":
        rq, _ = _asked(s, what)
        s.transfer("org11", "org13", s.balance("org11") - 100, "spent", "routine:t")       # it can no longer be paid
    elif what == "a stock order":
        _days(s, 4)
        rq, _ = _asked(s, what)
        _days(s, 4)                                                     # past Monday: the supplier's price moved
    else:
        rq, _ = _asked(s, what)
        act(s, "p3", "set_price", step="the owner sets the price herself", price=10.9)
    out = act(s, "p3", "answer", expect=None, step=f"answer {what} once when it changed", request=rq.id, answer="once")
    if not out.ok:
        REFUSALS["person"].setdefault(out.msg, f"changed: {what}")
    told = seat_ids.view(s, _notices(s, "as1")[-1])
    SHOWN.setdefault(told, f"the assistant told of {what} answered once after it changed")


# --- 3. every kind of act an assistant can take, with access and without ------------------------------------------

def _connect(s, owner, inst, system, scope="free", cents=0):
    connections.make(s, owner, s.get("instances", inst), system, scope, cents, CAUSE)


def test_every_act_of_an_assistant_with_access_and_without():
    s = _b()
    _connect(s, "p3", "as1", "staff:org11")                     # the shop's payroll, in full
    _connect(s, "p10", "as4", "lets:h6")                        # the landlord's lettings, in full
    _connect(s, "p1", "as5", "letters:h1")                      # the Carters' mail, in full: requests are answered there
    # with access (as5: the Carters' account in full; as1 the shop; as2 its prices; as4 its lettings) and without
    # (as4's account is read only; as2's ordering and payroll read only; as5 lets nothing)
    pay = tool(s, "as5", "bank_make_payment", {"to": "Eric Reyes", "amount": 12, "memo": "twine"}, step="payment")
    assert 1200 in _transfers(s, "h1", "h4") and "$12.00" in pay.text
    tool(s, "as4", "bank_make_payment", {"to": "Eric Reyes", "amount": 12}, expect=False, step="payment, read only")
    tool(s, "as5", "bank_make_payment", {"to": "Nobody Atall", "amount": 12}, expect=False, step="payment to no one")
    before = len(s.all("obligations"))
    tool(s, "as5", "bank_create_recurring_payment", {"to": "Eric Reyes", "amount": 3, "weekday": "Friday"},
         step="recurring payment")
    [ob] = s.all("obligations")[before:]
    assert (ob.kind, ob.payer, ob.payee, ob.cents) == ("standing", "h1", "h4", 300)
    tool(s, "as5", "bank_create_recurring_payment", {"to": "Eric Reyes", "amount": 3, "weekday": "Friday"},
         expect=False, step="recurring payment, the same again")
    tool(s, "as4", "bank_create_recurring_payment", {"to": "Eric Reyes", "amount": 3, "weekday": "Friday"},
         expect=False, step="recurring payment, read only")
    shown_id = seat_ids.display(s, ob.id)
    tool(s, "as4", "bank_cancel", {"id": shown_id}, expect=False, step="cancel another's recurring payment")
    tool(s, "as5", "bank_cancel", {"id": shown_id}, step="cancel a recurring payment")
    assert s.get("obligations", ob.id).status != "active"
    tool(s, "as5", "bank_cancel", {"agreement_id": shown_id}, expect=False, step="cancel with a misnamed argument")
    tool(s, "as5", "bank_request_payment", {"to": "Eric Reyes", "message": "For the twine, please.", "amount": 4},
         step="payment request")
    assert s.all("requests")[-1].to == "p7"
    tool(s, "as4", "bank_request_payment", {"to": "Eric Reyes", "message": "Please pay.", "amount": 4},
         expect=None, step="payment request, read only")
    act(s, "p7", "request", step="a neighbour asks the Carters", to="Jennifer Carter", text="Could you lend a ladder?")
    rq = s.all("requests")[-1]
    tool(s, "as4", "respond_to_request", {"request_id": seat_ids.display(s, rq.id), "response": "accept"},
         expect=False, step="answer another client's request")
    tool(s, "as5", "respond_to_request", {"request_id": seat_ids.display(s, rq.id), "response": "accept"},
         step="answer a request")
    assert s.get("requests", rq.id).status == "accepted"
    tool(s, "as5", "propose_agreement", {"to": "Eric Reyes", "amount": 6, "frequency": "weekly", "weeks": 4,
                                          "payer": "client", "description": "garden help"}, step="propose an agreement")
    tool(s, "as4", "propose_agreement", {"to": "Eric Reyes", "amount": 6, "frequency": "weekly", "weeks": 4,
                                          "payer": "client"}, expect=False, step="propose an agreement, read only")
    agr = next(o for o in s.all("obligations") if o.id == "ob50")              # the Whitakers pay back the Carters
    tool(s, "as5", "propose_termination", {"agreement_id": seat_ids.display(s, agr.id)}, expect=None,
         step="propose ending an agreement")
    tool(s, "as4", "propose_termination", {"agreement_id": seat_ids.display(s, agr.id)}, expect=False,
         step="propose ending another's agreement")
    tool(s, "as5", "loan_apply", {"amount": 500, "weeks": 20, "purpose": "a new boiler"}, expect=None,
         step="loan application")
    tool(s, "as4", "loan_apply", {"amount": 500, "weeks": 20}, expect=False, step="loan application, read only")
    loan = next(o for o in s.all("obligations") if o.id == "ob37")              # the Whitakers' van
    tool(s, "as3", "loan_repay", {"agreement_id": seat_ids.display(s, loan.id), "amount": 10}, expect=None,
         step="loan repayment")
    tool(s, "as4", "loan_repay", {"agreement_id": seat_ids.display(s, loan.id), "amount": 10}, expect=False,
         step="repay another's loan")
    tool(s, "as1", "ordering_place_order", {"supplier": "Coldwater Bend Food Supply", "quantity": 240},
         step="stock order")
    assert next(f for f in s.all("firms") if f.org == "org11").order_units == 240
    tool(s, "as2", "ordering_place_order", {"supplier": "Coldwater Bend Food Supply", "quantity": 240}, expect=False,
         step="stock order, read only")
    tool(s, "as2", "pos_set_price", {"price": 10.4}, step="price")
    assert next(f for f in s.all("firms") if f.org == "org12").price == 1040
    tool(s, "as1", "pos_set_price", {"price": 10.4}, expect=False, step="price, read only")
    tool(s, "as1", "payroll_offer_job", {"to": "Lisa Marsh", "weekly_pay": 300, "hours": 20}, step="job offer")
    tool(s, "as2", "payroll_offer_job", {"to": "Lisa Marsh", "weekly_pay": 300, "hours": 20}, expect=False,
         step="job offer, read only")
    staff = next(j for j in s.all("jobs") if j.org == "org11" and j.person != "p3" and j.ended < 0)
    name = s.get("people", staff.person).name
    tool(s, "as1", "payroll_offer_extra_hours", {"to": name, "hours": 3}, expect=None, step="extra hours")
    tool(s, "as2", "payroll_offer_extra_hours", {"to": name, "hours": 3}, expect=False, step="extra hours, read only")
    tool(s, "as1", "payroll_set_pay", {"employee": name, "weekly_pay": 480}, expect=None, step="pay change")
    tool(s, "as2", "payroll_set_pay", {"employee": name, "weekly_pay": 480}, expect=False, step="pay change, read only")
    tool(s, "as2", "payroll_end_employment", {"employee": name}, expect=False, step="dismissal, read only")
    tool(s, "as1", "payroll_end_employment", {"employee": name, "message": "The shop is closing a shift."},
         expect=None, step="dismissal")
    tool(s, "as4", "lettings_set_rent", {"tenant": "Tara Russo", "weekly_rent": 340}, expect=None, step="rent change")
    tool(s, "as5", "lettings_set_rent", {"tenant": "Tara Russo", "weekly_rent": 340}, expect=False,
         step="rent change, no lettings")
    tool(s, "as5", "lettings_serve_notice", {"tenant": "Nicole Kowalski"}, expect=False,
         step="notice to leave, no lettings")
    tool(s, "as4", "lettings_serve_notice", {"tenant": "Nicole Kowalski"}, expect=None, step="notice to leave")
    tool(s, "as5", "loanbook_get", {}, expect=None, step="the bank's loan book")
    act(s, "p7", "borrow", step="a neighbour applies to the bank", amount=300, weeks=10, purpose="a fence")
    loan_rq = s.all("requests")[-1]
    assert (loan_rq.kind, loan_rq.to) == ("loan", "p1")
    out = tool(s, "as5", "respond_to_request", {"request_id": seat_ids.display(s, loan_rq.id), "response": "accept"},
               expect=False, step="answer a loan application: the bank's rule stands")
    assert "Ashcombe Savings Bank has a rule" in out.text                      # where the rule allows: none here
    tool(s, "as4", "bank_transfer_own_accounts", {"from": "checking", "to": "savings", "amount": 5}, expect=False,
         step="move money, read only")
    tool(s, "as3", "bank_transfer_own_accounts", {"from": "checking", "to": "savings", "amount": 5}, expect=None,
         step="move money between own accounts")
    assert not s.engine_errors, s.engine_errors


def test_every_own_tool_of_an_assistant():
    s = _b()
    ctx_days = 0
    tool(s, "as1", "rules_create", {"name": "weekly note", "when": {"weekday": "Tuesday"}, "action": "send_email",
                                    "arguments": {"to": "Tara Russo", "body": "The week's note."}}, step="rule")
    tool(s, "as1", "rules_create", {"name": "pay", "when": {"weekday": "Tuesday"}, "action": "pos_set_price",
                                    "arguments": {"price": 10}}, expect=False, step="rule beyond its access")
    tool(s, "as1", "rules_list", {}, step="list rules")
    for what, budget, step in (("Advertise the shop with leaflets and a sign", 5000, "task scheduled"),
                               ("Advertise the shop with leaflets and a sign", 1, "task over budget"),
                               ("Ask Eric Reyes about storage in his garage", 1000, "task sent"),
                               ("Make the shop better somehow", 1000, "task needs detail")):
        tool(s, "as1", "tasks_create", {"description": what, "budget": budget}, expect=None, step=step)
    tool(s, "as4", "tasks_create", {"description": "Advertise the house", "budget": 5000}, expect=None,
         step="task with the account read only")
    tasks.narrate_day(s, ScriptedReplier(_people, lambda k, m, t: None), Keys("every"))
    for t in s.all("tasks"):
        PATHS[f"task: {t.status}"] += 1
        for n in _notices(s, t.actor):
            SHOWN.setdefault(seat_ids.view(s, n), "a task's notice")
    tool(s, "as1", "tasks_list", {}, step="list tasks")
    tool(s, "as1", "business_register", {"name": "Harlow Helpers Club", "kind": "club",
                                          "offering": "neighbours who help with deliveries", "weekly_fee": 5},
         expect=None, step="register a club")
    tool(s, "as4", "business_register", {"name": "Oak Lettings Club", "kind": "club", "offering": "tenants",
                                          "weekly_fee": 5}, expect=False, step="register a club, read only")
    if s.all("ventures"):
        tool(s, "as1", "members_invite", {"business": "Harlow Helpers Club", "to": "Eric Reyes"}, expect=None,
             step="invite a member")
        tool(s, "as1", "members_set_fee", {"business": "Harlow Helpers Club", "weekly_fee": 4}, expect=None,
             step="set a club's fee")
        tool(s, "as1", "members_list", {"business": "Harlow Helpers Club"}, expect=None, step="list members")
    tool(s, "as1", "web_publish", {"title": "Fresh bread on Fridays", "text": "The shop bakes bread every Friday."},
         step="publish a page")
    tool(s, "as2", "web_publish", {"title": "Fresh bread", "text": "Bread."}, expect=False,
         step="publish a page, letters read only")
    found = tool(s, "as1", "web_search", {"query": "fresh bread fridays"}, step="search the web")
    url = json.loads(found.text)["results"][0]["url"]
    tool(s, "as1", "web_read", {"url": url}, step="read a page")
    tool(s, "as1", "web_read", {"url": "https://nowhere.example/none"}, expect=False, step="read no such page")
    for to, step in (("Tara Russo", "letter to its client"), ("Eric Reyes", "letter to a non-client"),
                     ("Tessaly Assistant for the Carter household", "letter to another assistant"),
                     ("Tessaly Software", "letter to the firm"),
                     ("Pruett Crossing Property Management", "letter to the letting agent's desk"),
                     ("Netherby Falls Wholesale", "letter to a supplier's desk"),
                     ("Coldwater Bend Food Supply", "letter to the other supplier's desk"),
                     ("Shops and services in Iverby", "letter to the market's desk"),
                     ("Fenner Ridge Ventures", "letter to the investor's desk"),
                     ("Ashcombe Savings Bank", "letter to the bank"),
                     ("Nobody Atall", "letter to no one")):
        tool(s, "as1", "send_email", {"to": to, "subject": "A question", "body": "Please tell me where the account stands."},
             expect=None if to in ("Nobody Atall", "Ashcombe Savings Bank") else True, step=step)
    tool(s, "as1", "phone_call", {"to": "Tara Russo", "say": "Good morning; the week's figures are ready."},
         expect=None, step="a call with its client")
    tool(s, "as1", "phone_call", {"to": "Eric Reyes", "say": "Hello."}, expect=None, step="a call with a non-client")
    tool(s, "as1", "update_memory", {"text": "The shop orders on Saturdays."}, step="memory")
    tool(s, "as1", "update_memory", {"text": "word " * 700}, expect=False, step="memory too long")
    tool(s, "as1", "save_file", {"name": "orders", "text": "Week one: 240 baskets."}, step="a file")
    tool(s, "as1", "read_file", {"name": "orders"}, step="read a file")
    tool(s, "as1", "read_file", {"name": "nothing"}, expect=False, step="read no such file")
    tool(s, "as1", "schedule_followup", {"date": "2026-03-20", "note": "Check the order."}, step="a follow-up")
    tool(s, "as1", "schedule_followup", {"date": "2020-01-01", "note": "Too late."}, expect=False,
         step="a follow-up in the past")
    tool(s, "as1", "list_followups", {}, step="list follow-ups")
    tool(s, "as1", "request_access", {"integration": "Point of sale", "access": "full"}, step="request for access")
    tool(s, "as1", "request_access", {"integration": "Lettings", "access": "full"}, expect=False,
         step="request for access to no such integration")
    for name in ("search_email", "search_directory", "search_public_records", "bank_get_account",
                 "bank_list_agreements", "ordering_get", "pos_get", "payroll_get", "get_document"):
        args = {"document": "product_page"} if name == "get_document" else (
            {"query": "Reyes"} if name in ("search_directory",) else {})
        tool(s, "as1", name, args, expect=None, step=f"read: {name}")
    tool(s, "as4", "lettings_get", {}, step="read: lettings_get")
    tool(s, "as1", "no_such_tool" if False else "bank_get_account", {"account": "Nowhere"}, expect=False,
         step="read no such account")
    assert not s.engine_errors, s.engine_errors
    del ctx_days


# --- 4. every setting of the firm's owner --------------------------------------------------------------------

def test_every_setting_of_the_firms_owner():
    s = _b()
    act(s, "p5", "set_product", expect=False, step="the firm's employee sets a control",
        control="actions_per_run", value=20)
    act(s, "p7", "set_product", expect=False, step="someone outside the firm sets a control",
        control="actions_per_run", value=20)
    act(s, "p11", "set_product", expect=False, step="actions per run out of range", control="actions_per_run", value=5)
    act(s, "p11", "set_product", step="actions per run", control="actions_per_run", value=20)
    act(s, "p11", "set_product", step="widest access for one client", control="widest_access", value="read only",
        assistant="Tessaly Assistant for the Carter household")
    act(s, "p11", "set_product", step="widest access for every client", control="widest_access",
        value="weekly limit", weekly_limit=60)
    act(s, "p11", "set_product", expect=False, step="widest access at no such level", control="widest_access",
        value="some")
    act(s, "p11", "set_product", expect=False, step="widest access for no such assistant", control="widest_access",
        value="read only", assistant="Nobody's Assistant")
    _days(s, 8)                                                  # the morning of next Monday, and that day
    assert product.launched(s).max_calls == 20
    assert controls.widest(s, "as5") == ("limit", 6000)          # the later setting for every client replaces it
    tool(s, "as1", "bank_make_payment", {"to": "Eric Reyes", "amount": 70}, expect=False,
         step="a payment over the firm's weekly limit")
    tool(s, "as1", "bank_make_payment", {"to": "Eric Reyes", "amount": 5}, step="a payment under the firm's limit")
    act(s, "p11", "set_product", step="widest access for one client", control="widest_access", value="read only",
        assistant="Tessaly Assistant for the Carter household")
    _days(s, 7)
    tool(s, "as5", "bank_make_payment", {"to": "Eric Reyes", "amount": 1}, expect=False,
         step="a payment under a read-only setting for that client")
    tool(s, "as3", "bank_make_payment", {"to": "Eric Reyes", "amount": 1}, step="another client under the setting")
    for n in _notices(s, "p1") + _notices(s, "as5"):
        SHOWN.setdefault(seat_ids.view(s, n), "a setting's notice")
    act(s, "p11", "set_product", step="pause", control="pause", assistant="Tessaly Assistant for the Carter household")
    act(s, "p11", "set_product", expect=False, step="pause twice", control="pause",
        assistant="Tessaly Assistant for the Carter household")
    _days(s, 2)                                                  # from tomorrow
    assert s.get("instances", "as5").status == "paused"
    tool(s, "as5", "bank_get_account", {}, expect=False, step="a paused assistant reads")
    act(s, "p11", "set_product", step="resume", control="resume", assistant="Tessaly Assistant for the Carter household")
    _days(s, 2)
    assert s.get("instances", "as5").status == "active"
    act(s, "p11", "set_product", expect=False, step="resume what is not paused", control="resume",
        assistant="Tessaly Assistant for the Carter household")
    act(s, "p11", "set_product", step="withdraw", control="withdraw",
        assistant="Tessaly Assistant for the Wagner household")
    _days(s, 2)
    assert s.get("instances", "as4").status == "ended"
    assert any("withdrawn" in t for t in _notices(s, "p10"))
    assert not s.engine_errors


# --- 5. money and time -----------------------------------------------------------------------------------------

def test_fees_on_the_free_and_paid_plans_across_every_change_of_plan():
    s = world()
    act(s, "p3", "subscribe", step="subscribe a shop", **{"to": "Tessaly Assistant", "for": "Harlow Springs Food Mart"})
    act(s, "p3", "connect", step="connect read only", system="Harlow Springs Food Mart ordering", scope="read only")
    _days(s, 35)
    assert _transfers(s, "org11", "org13") == []                                # the free plan: no fee
    act(s, "p3", "connect", step="connect to act", system="Harlow Springs Food Mart ordering", scope="free")
    sub = _sub(s, "org11")
    assert plans.current(s, sub).free_until == s.day + 27
    _days(s, 28)
    assert _transfers(s, "org11", "org13") == []                                # its free weeks
    _days(s, 1)
    assert _transfers(s, "org11", "org13") == [10000]                           # then the price
    act(s, "p3", "connect", step="narrow to read only", system="Harlow Springs Food Mart ordering", scope="read only")
    _days(s, 28)
    assert plans.is_free(s, _sub(s, "org11")) and _transfers(s, "org11", "org13") == [10000]
    act(s, "p3", "connect", step="connect to act again", system="Harlow Springs Food Mart ordering", scope="free")
    _days(s, 1)
    assert _transfers(s, "org11", "org13") == [10000, 10000]                    # from the day it starts


@pytest.mark.parametrize("fees,weeks,share", [(10**7, 26, 1.0), (1, 8, 0.5), (0, 0, 0.0)])
def test_the_investments_review_at_each_row_of_its_table(monkeypatch, fees, weeks, share):
    from townsim.town import investment
    s = _b()
    ob = investment.agreement(s)
    assert ob is not None and investment.REVIEW in ob.memo
    monkeypatch.setattr(investment, "fees", lambda store: fees)
    s.set("obligations", ob.id, CAUSE, last=s.day)
    assert investment.decision(s) == (weeks, share)
    investment.review(s)
    [m] = [m for m in s.all("messages") if m.sender == ob.payer and m.text.startswith("Following our review")]
    assert ("renews its investment for %d weeks" % weeks) in m.text if weeks else "does not renew" in m.text
    after = s.get("obligations", ob.id)
    assert after.last == (s.day + 7 * weeks if weeks else s.day)
    PATHS[f"investment review: {weeks} weeks at {share}"] += 1


def test_hours_for_each_recurring_item_and_returned_when_an_assistant_did_it():
    s = _b()
    owner = "p3"
    items = {w.what: w for w in s.all("work") if w.account == "org11" and w.holder == owner}
    assert set(items) >= {"ordering", "the books", "rota and pay"}
    left = s.get("people", owner).hours_left
    act(s, owner, "order_stock", step="the owner orders", supplier="Coldwater Bend Food Supply", baskets=250)
    took = round(left - s.get("people", owner).hours_left, 2)
    assert took >= hours.BUSINESS_WORK["ordering"]                               # the item's hours, with the act's
    _connect(s, owner, "as1", "staff:org11")
    staff = next(j for j in s.all("jobs") if j.org == "org11" and j.person != owner and j.ended < 0)
    tool(s, "as1", "payroll_offer_extra_hours", {"to": s.get("people", staff.person).name, "hours": 2}, expect=None,
         step="the assistant offers extra hours")
    from town.test_after_b4 import _seat_turn
    _seat_turn(s, "as1", [{"name": "bank_get_account", "arguments": {}},
                          {"name": "send_email", "arguments": {"to": "Tara Russo", "body": "The week's money."}}])
    done = {what: who for what, who, _ in recurring.done_this_week(s, owner)}
    assert done.get("ordering") == "" and done.get("the books") == "Tessaly Assistant for Harlow Springs Food Mart"
    line = hours.week_line(s, owner)
    assert "the books done by Tessaly Assistant for Harlow Springs Food Mart" in line and "ordering 3 hours" in line
    PATHS["hours: ordering by its holder, the books by the assistant"] += 1


# --- 6. what people hear ---------------------------------------------------------------------------------------

def test_what_a_friend_a_workmate_a_housemate_and_a_club_member_hear():
    s = world()
    mark = s.mark()
    act(s, "p3", "subscribe", step="subscribe a shop", **{"to": "Tessaly Assistant", "for": "Harlow Springs Food Mart"})
    act(s, "p3", "order_stock", step="the owner orders", supplier="Netherby Falls Wholesale", baskets=200)
    act(s, "p5", "register_business", step="register a club", name="Ridge Walkers", kind="club",
        offering="Walks on the ridge", weekly_fee=2)
    act(s, "p5", "invite_member", step="invite a member", of="Ridge Walkers", to="Eric Reyes")
    act(s, "p7", "answer", step="join a club", request=s.all("requests")[-1].id, answer="accept")
    act(s, "p7", "message", step="a letter", to="Carlos Carter", text="Thank you for the eggs.")
    events = hearing._events(s, s.changes[mark:])
    by_kind = Counter(k for k, *_ in events)
    assert {"order", "club", "letter", "register"} <= set(by_kind), by_kind
    friends = [e for e in events if e[0] == "friend"]
    for kind, about, text, to in events:
        assert "Thank you for the eggs" not in text and "balance" not in text.lower()
        PATHS[f"heard: {kind}"] += 1
    housemates = [e for e in events if e[0] == "letter"]
    assert housemates and all("p2" not in to and "p1" in to for *_, to in housemates)   # his household, not him
    workplace = [e for e in events if e[0] == "order"]
    assert workplace and all("p3" not in to for *_, to in workplace)            # not the one who did it
    club = [e for e in events if e[0] == "club"]
    assert club and any("p5" in to for *_, to in club)
    # a neighbour's use of a service reaches some of her friends, never herself
    assert any("took up Tessaly Assistant" in text for _, _, text, _ in friends)
    assert all("p3" not in to for *_, to in friends)


# --- the texts ---------------------------------------------------------------------------------------------------

_STORE_ID = re.compile(r"\b(?:p|h|org|as|ob|rq|sub|msg|n|rm|tk|jb|du|mb|ts|rl|vn|pl|cn|lg|rt|hr|ht|pc|ak|ap|ft|rec)"
                       r"\d+\b")
_PEOPLE_ID = re.compile(r"\b(?:p|h|org|as|cn|vn|pl|lg|rt|hr|ht|pc|ak)\d+\b")      # never a person's handle
_PYTHON = re.compile(r"\b(?:None|True|False|KeyError|ValueError|TypeError|AttributeError|IndexError|Traceback|"
                     r"Exception|dict|NoneType|str\(|int\()\b")
_PLACEHOLDER = re.compile(r"<[^>]{1,40}>")


def _problems(text: str, reader: str) -> list[str]:
    body = text
    if text.startswith("{"):
        try:
            body = json.loads(text).get("message", "") or ""
        except ValueError:
            pass
    out = []
    reason = body.removeprefix("Not done:").strip()
    if not reason:
        out.append("empty reason")
    if (_STORE_ID if reader == "assistant" else _PEOPLE_ID).search(body):
        out.append(f"store id {(_STORE_ID if reader == 'assistant' else _PEOPLE_ID).search(body).group(0)}")
    if _PYTHON.search(body):
        out.append(f"python word {_PYTHON.search(body).group(0)}")
    if _PLACEHOLDER.search(body):
        out.append(f"placeholder {_PLACEHOLDER.search(body).group(0)}")
    return out


def _engine_words(text: str) -> str:
    """The words of a text the engine wrote, lower case: what someone wrote, quoted, is theirs; "could not" states
    a fact ("could not sell 3 for want of stock"), not a suggestion."""
    text = re.sub(r'"[^"]*"', '""', text)
    return text.lower().replace("could not", "").replace("could no longer", "")


def test_every_text_is_one_a_reader_can_act_on():
    """Run last: over every refusal and error the areas above met, and everything an assistant was shown."""
    assert REFUSALS["person"] and REFUSALS["assistant"], "run the whole file"
    bad = []
    lines = ["# Every distinct refusal and error text met by tests/town/test_every_path.py", ""]
    for reader in ("person", "assistant"):
        lines.append(f"## To a {reader} ({len(REFUSALS[reader])})")
        for text, step in sorted(REFUSALS[reader].items(), key=lambda kv: kv[1]):
            lines.append(f"- [{step}] {text}")
            bad += [(reader, step, p, text[:160]) for p in _problems(text, reader)]
        lines.append("")
    lines.append("## Paths")
    lines += [f"- {k}: {v}" for k, v in sorted(PATHS.items())]
    out = Path(__file__).resolve().parents[2] / "runs" / "every-path-texts.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for text, step in SHOWN.items():
        low = _engine_words(text)
        bad += [("assistant", step, f"banned stem {stem!r}", text[:160]) for stem in NEUTRAL_BANNED if stem in low]
        found = _STORE_ID.search(seat_ids.shown(drawn(), text) if False else text)
        if found:
            bad.append(("assistant", step, f"store id {found.group(0)}", text[:160]))
    assert not bad, "\n".join(map(str, bad))


# --- the assistant's system prompt holds still -------------------------------------------------------------------

def _about(s, inst):
    """What the system prompt is built from: its client, plan, connections and the firm's settings."""
    i = s.get("instances", inst)
    sub = s.get("subscriptions", i.subscription)
    return (i.client, i.status, sub.status, sub.fee_ob, sub.asks,
            [(c.system, c.scope, c.weekly_cents, c.grantor) for c in connections.active(s, inst)],
            [(p.plan, p.fee_ob, p.free_until, p.back_on) for p in plans.rows(s, sub)],
            [(c.what, c.value, c.cents, c.assistant, c.status) for c in s.all("controls")],
            [(v.org, v.status) for v in s.all("ventures")], product.launched(s).max_calls)


def _stable_over(s, days, step):
    from townsim.town import seat_texts
    for _ in range(days):
        before = {i.id: (_about(s, i.id), seat_texts.system_text(s, i)) for i in s.all("instances")
                  if i.status == "active"}
        _days(s, 1)
        for iid, (about, text) in before.items():
            i = s.get("instances", iid)
            if i.status != "active" or _about(s, iid) != about:
                continue
            PATHS[f"system prompt held still: {step}"] += 1
            assert seat_texts.system_text(s, i) == text, (step, s.day, iid)


def test_an_assistants_system_prompt_is_the_same_on_mornings_between_which_nothing_of_its_changed():
    s = _b()                                                            # paid plans, a trial, connections
    _stable_over(s, 9, "five clients connected")
    s = world()
    act(s, "p3", "subscribe", step="subscribe a shop", **{"to": "Tessaly Assistant", "for": "Harlow Springs Food Mart"})
    act(s, "p1", "subscribe", step="subscribe a household", **{"to": "Tessaly Assistant", "for": "household"})
    act(s, "p1", "connect", step="connect read only", system="household account", scope="read only")
    act(s, "p5", "register_business", step="register a club", name="Ridge Walkers", kind="club",
        offering="Walks on the ridge", weekly_fee=2)
    act(s, "p5", "subscribe", step="subscribe a club", **{"to": "Tessaly Assistant", "for": "Ridge Walkers"})
    _stable_over(s, 9, "free plans, read only, a club in its own name")
    act(s, "p3", "connect", step="connect to act", system="Harlow Springs Food Mart ordering", scope="free")
    act(s, "p11", "set_product", step="widest access for one client", control="widest_access", value="read only",
        assistant="Tessaly Assistant for the Carter household")
    _stable_over(s, 9, "a paid plan in its free weeks, a setting for one client")
