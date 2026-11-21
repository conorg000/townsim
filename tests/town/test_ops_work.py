"""The operations of work against junk: every argument of every one of them, and the answers
to the requests they make, never break anything; refusals are plain facts; hours are taken
only when an act is done."""

import re

import pytest

from townsim.town.acts import REGISTRY, Act, apply_act

from .test_jobs import work_town
from .test_ops_core import JUNK

NEW = ["offer_job", "offer_shifts", "dismiss", "set_staff_pay", "set_price", "order_stock", "work_extra_hours",
       "move_money", "quit"]


def _job_offer(s):
    apply_act(s, Act("p01", "p01", "offer_job", {"to": "Megan Pryce", "weekly_pay": 440, "hours": 38}), 90)
    return {"request": s.all("requests")[-1].id, "answer": "accept", "note": "fine"}


def _shifts_offer(s):
    apply_act(s, Act("p01", "p01", "offer_shifts", {"to": "Craig Pryce", "hours": 8}), 90)
    return {"request": s.all("requests")[-1].id, "answer": "accept", "note": "fine"}


# name -> (actor, operation, valid arguments)
VALID = {
    "offer_job": ("p01", "offer_job", lambda s: {"to": "Megan Pryce", "weekly_pay": 440, "hours": 38, "text": "x"}),
    "offer_shifts": ("p01", "offer_shifts", lambda s: {"to": "Craig Pryce", "hours": 8}),
    "dismiss": ("p01", "dismiss", lambda s: {"who": "Craig Pryce", "text": "x"}),
    "set_staff_pay": ("p01", "set_staff_pay", lambda s: {"who": "Craig Pryce", "weekly_pay": 500}),
    "set_price": ("p01", "set_price", lambda s: {"price": 11}),
    "order_stock": ("p01", "order_stock", lambda s: {"supplier": "Vale Wholesale", "units": 100}),
    "work_extra_hours": ("p01", "work_extra_hours", lambda s: {"hours": 5}),
    "move_money": ("p01", "move_money", lambda s: {"from": "Marsh's Stores", "to": "household", "amount": 50}),
    "quit": ("p03", "quit", lambda s: {"from": "Marsh's Stores"}),
    "answer a job offer": ("p04", "answer", _job_offer),
    "answer an offer of hours": ("p03", "answer", _shifts_offer),
}
CASES = [(name, key) for name, (_, _, make) in VALID.items() for key in make(work_town())]
_ADVICE = re.compile(r"\b(should|must|could|try|please|consider|instead|why not|you may|you can)\b", re.I)


def test_these_are_the_new_operations_in_order():
    assert [n for n in REGISTRY if n in NEW] == NEW
    assert [REGISTRY[n].order for n in NEW] == [90, 100, 110, 120, 130, 140, 150, 160, 170]


def test_the_valid_acts_are_valid_and_take_their_hours():
    for name, (actor, op, make) in VALID.items():
        s = work_town()
        args = make(s)
        before = s.get("people", actor).hours_left
        out = apply_act(s, Act(actor, actor, op, args), 0)
        assert out.ok, (name, out.msg)
        taken = 5 if op == "work_extra_hours" else REGISTRY[op].hours
        taken += 8 if name == "answer an offer of hours" else 0        # the hours themselves
        assert s.get("people", actor).hours_left == pytest.approx(before - taken), name
        assert s.ledger.total() == 0 and s.engine_errors == []


@pytest.mark.parametrize("name,key", CASES)
def test_junk_in_every_argument_of_every_new_operation(name, key):
    actor, op, make = VALID[name]
    for value in JUNK + ["<missing>"]:
        s = work_town()
        args = make(s)
        if value == "<missing>":
            del args[key]
        else:
            args[key] = value
        before, hours = s.hash(), s.get("people", actor).hours_left
        out = apply_act(s, Act(actor, actor, op, args), 0)
        assert out.ok or out.msg.startswith("Not done:"), (name, key, value, out.msg)
        if not out.ok:
            assert s.hash() == before and s.get("people", actor).hours_left == hours, (name, key, value)
            assert len(out.msg) < 500 and out.msg.endswith(".") and not _ADVICE.search(out.msg), out.msg
        assert s.engine_errors == [] and s.ledger.total() == 0, (name, key, value, s.engine_errors)


def test_every_refusal_states_a_fact():
    """A refusal for each way each operation can be refused starts `Not done:` and says what
    is so, never what to do."""
    s = work_town()
    bad = [("p01", "offer_job", {"to": "Megan Pryce", "weekly_pay": 100, "hours": 38}),
           ("p01", "offer_job", {"to": "Edith Marsh", "weekly_pay": 500, "hours": 38}),
           ("p01", "offer_job", {"to": "Megan Pryce", "weekly_pay": 500, "hours": 3}),
           ("p01", "offer_job", {"to": "Megan Pryce", "weekly_pay": 500, "hours": 38, "text": "w " * 81}),
           ("p01", "offer_shifts", {"to": "Megan Pryce", "hours": 8}),
           ("p01", "offer_shifts", {"to": "Craig Pryce", "hours": 0}),
           ("p01", "dismiss", {"who": "Megan Pryce"}),
           ("p01", "dismiss", {"who": "Ruth Ashby"}),
           ("p01", "set_staff_pay", {"who": "Craig Pryce", "weekly_pay": 10}),
           ("p01", "set_staff_pay", {"who": "Megan Pryce", "weekly_pay": 500}),
           ("p01", "set_price", {"price": 101}),
           ("p05", "set_price", {"price": 10}),
           ("p01", "order_stock", {"supplier": "Marsh's Stores", "units": 10}),
           ("p01", "order_stock", {"supplier": "Vale Wholesale", "units": 1.5}),
           ("p01", "work_extra_hours", {"hours": 31}),
           ("p01", "move_money", {"from": "household", "to": "household", "amount": 5}),
           ("p01", "move_money", {"from": "Marsh's Stores", "to": "household", "amount": 1_000_000}),
           ("p01", "move_money", {"from": "Craig Pryce", "to": "household", "amount": 5}),
           ("p03", "quit", {"from": "Lowe's Grocer"}),
           ("p04", "quit", {})]
    for actor, op, args in bad:
        before = s.hash()
        out = apply_act(s, Act(actor, actor, op, args), 0)
        assert not out.ok and s.hash() == before, (op, args, out.msg)
        assert out.msg.startswith("Not done: ") and out.msg.endswith(".") and not _ADVICE.search(out.msg), out.msg
