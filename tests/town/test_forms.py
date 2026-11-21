"""Forms a weak model fills in wrong: dates, numbers as text, accounts, employers, names by
title or role, what a payment counts toward, and the order's form."""

import functools
import pickle

import pytest

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import gen, jobs
from townsim.town.acts import Act, apply_act, forms, resolve
from townsim.town.clock import parse_date

FAMILIES = ["a", "b", "c", "d"]


@functools.cache
def _frozen() -> bytes:
    return pickle.dumps(gen.generate(1, FAMILIES))


def place():
    return pickle.loads(_frozen())


def do(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 0)


@pytest.mark.parametrize("text, day", [("today", 3), ("tomorrow", 4), ("Thursday", 10), ("16 March", 14),
                                       ("March 16", 14), ("2026-03-16", 14), ("Monday 16 March", 14),
                                       ("16/03/2026", None), ("next week sometime", None), ("2026-02-30", None)])
def test_dates_are_read_in_the_ways_people_write_them(text, day):
    assert parse_date(text, 3) == day


def _owner(s):
    org = next(o for o in s.all("orgs") if o.kind == "shop")
    return org.owner, org


def test_a_date_that_cannot_be_read_is_refused_with_the_form():
    s = place()
    pid, _ = _owner(s)
    friend = next(p for p in s.all("people") if p.id != pid and p.household != s.get("people", pid).household)
    out = do(s, pid, "propose_agreement", to=friend.name, amount=5, per="week", weeks=2, payer="me",
             start="next week sometime", what="eggs")
    assert out.msg == "Not done: 'next week sometime' is not a date that can be read. Write it like Monday 16 March."
    out = do(s, pid, "book_appointment", **{"with": friend.name, "day": "16/03/2026", "about": "eggs"})
    assert out.msg == "Not done: '16/03/2026' is not a date that can be read. Write it like Monday 16 March."
    assert do(s, pid, "book_appointment", **{"with": friend.name, "day": "tomorrow", "about": "eggs"}).ok
    assert do(s, pid, "standing_payment", to=friend.name, amount=5, day="Friday", start="2026-03-20", weeks="4").ok


def test_whole_numbers_written_as_text_are_read():
    s = place()
    pid, org = _owner(s)
    friend = next(p for p in s.all("people") if p.household != s.get("people", pid).household)
    assert do(s, pid, "propose_agreement", to=friend.name, amount="5", per="week", weeks="4", payer="me", what="eggs").ok
    sup = s.get("orgs", s.all("suppliers")[0].org).name
    assert do(s, pid, "order_stock", supplier=sup, baskets="40").ok
    assert do(s, pid, "borrow", amount="900", weeks="10", purpose="a van").ok


@pytest.mark.parametrize("word", ["business", "shop", "my shop", "the shop", "my business"])
def test_the_business_by_any_plain_name(word):
    s = place()
    pid, org = _owner(s)
    out = do(s, pid, "move_money", **{"from": word, "to": "household", "amount": 1})
    assert out.ok and out.msg == f"Moved $1.00 from {org.name} to your household."
    friend = next(p for p in s.all("people") if p.household != s.get("people", pid).household)
    out = do(s, pid, "pay", to=friend.name, amount=1, **{"from": word})
    assert out.ok and s.ledger.transfers[-1].src == org.id


def test_an_account_that_is_not_theirs_is_refused_with_those_that_are():
    s = place()
    pid, org = _owner(s)
    out = do(s, pid, "move_money", **{"from": "savings", "to": "household", "amount": 1})
    assert out.msg == f"Not done: 'savings' is not an account of yours. Yours are: household, {org.name}."


def test_quit_names_the_employer_and_from_still_reads():
    s = place()
    worker = next(p for p in s.all("people") if p.kind == "staff")
    job = jobs.held(s, person=worker.id)[0]
    form = next(f for f in forms(s, worker.id) if '"op": "quit"' in f)
    assert '"employer": ' in form and '"from"' not in form
    out = do(s, worker.id, "quit", employer=s.get("orgs", job.org).name)
    assert out.ok, out.msg
    s2 = place()
    assert do(s2, worker.id, "quit", **{"from": s2.get("orgs", job.org).name}).ok


def test_a_title_and_surname_or_a_role_resolves_when_one_fits():
    s = place()
    tenant = next(p for p in s.all("people") if s.has("people", s.get("households", p.household).landlord))
    lord = s.get("households", tenant.household).landlord
    assert resolve(s, "the landlord", tenant.id) == lord == resolve(s, "my landlord", tenant.id)
    surname = s.get("people", lord).name.split()[-1]
    assert resolve(s, f"Mrs {surname}", tenant.id) == lord
    worker = next(p for p in s.all("people") if p.kind == "staff")
    owner = s.get("orgs", jobs.held(s, person=worker.id)[0].org).owner
    assert resolve(s, "my employer", worker.id) == owner
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    assert resolve(s, "the bank", worker.id) == bank.id and resolve(s, "the banker", worker.id) == bank.contact
    out = do(s, worker.id, "pay", to="Mrs Nobodyhere", amount=1)
    assert out.msg == ("Not done: no one you know is called 'Mrs Nobodyhere'. Names are written in full, as they "
                       "appear in your records.")


def test_a_payment_for_none_counts_toward_nothing_and_every_payment_says_what_it_counted_toward():
    s = place()
    pid, org = _owner(s)
    staff = next(j for j in jobs.held(s, org=org.id) if j.person != pid)
    name = s.get("people", staff.person).name
    out = do(s, pid, "pay", to=name, amount=20, note="a bonus", **{"from": org.name, "for": "none"})
    assert out.msg == f"Paid $20.00 to {name}. It was not counted toward anything owed."
    assert all(d.paid == 0 for d in s.where("dues", obligation=staff.wage_ob))
    out = do(s, pid, "pay", to=name, amount=20, note="wages", **{"from": org.name})
    assert "It was counted toward wages from" in out.msg
    form = next(f for f in forms(s, pid) if '"op": "pay"' in f)
    assert '"for": "<id of what it is owed on, if any; none for a payment that is not toward anything owed>"' in form
    order = next(f for f in forms(s, pid) if '"op": "order_stock"' in f)
    assert '"baskets": <baskets a week; 0 to end the order>' in order
