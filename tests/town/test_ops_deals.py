"""Agreements, loans and rent: a proposal accepted is one thing listed for both sides; the
banker lends the bank's money; repaying early shortens a loan from its end; a landlord sets
rent and serves notice. Every argument of every new operation, and the answers to the
requests they make, stand up to junk."""

import functools
import pickle
import re

import pytest

from townsim.town import obligations as O
from townsim.town.acts import REGISTRY, Act, apply_act
from townsim.town.entities import Org
from townsim.town.facts import bank_book, owed_lines
from townsim.town.statement import agreements_text

from .helpers import add_person, set_cash, tie, town_with_shop
from .test_jobs import live, to_day
from .test_ops_core import JUNK

T = "routine:test"
NEW = ["propose_agreement", "propose_end", "borrow", "repay", "set_rent", "serve_notice"]
_ADVICE = re.compile(r"\b(should|must|could|try|please|consider|instead|why not|you may|you can)\b", re.I)


def deals_town():
    """The thin town (day 0 a Monday) with Marsh's Stores (org3, owned by Ruth Ashby p01,
    $2,000); a bank, Wenlow Bank (org4, $50,000), whose banker is Dora Penn (p05, of her own
    household h3); and a landlord, Len Hale (p06, of h4), who lets 4 Mill Lane to the Pryces
    (h2: Craig p03, Megan p04) for $185 a week on Mondays from day 0 (ob6)."""
    s = town_with_shop()
    add_person(s, "p05", "Dora Penn")
    s.set("people", "p05", T, kind="banker", hours_week=70.0, hours_left=70.0)
    s.open_account("org4", T)
    s.add("orgs", Org("org4", "Wenlow Bank", "bank", contact="p05"), T)
    s.transfer("outside:employers", "org4", 5_000_000, "opening", T)
    add_person(s, "p06", "Len Hale")
    s.set("people", "p06", T, kind="landlord", hours_week=70.0, hours_left=70.0)
    rent = next(o for o in s.where("obligations", payer="h2") if o.kind == "rent")
    s.set("obligations", rent.id, T, payee="h4")
    s.set("households", "h2", T, landlord="p06", street="4 Mill Lane")
    for tenant in ("p03", "p04"):
        tie(s, "p06", tenant, "tenant")
        tie(s, tenant, "p06", "landlord")
    return s


def do(s, actor, op, n=0, **args):
    return apply_act(s, Act(actor, actor, op, args), n)


def ok(s, actor, op, n=0, **args):
    out = do(s, actor, op, n, **args)
    assert out.ok, out.msg
    assert s.engine_errors == [] and s.ledger.total() == 0
    return out


def refused(s, actor, op, **args):
    before = s.hash()
    out = do(s, actor, op, **args)
    assert not out.ok and s.hash() == before and s.engine_errors == [], out.msg
    assert out.msg.startswith("Not done: ") and out.msg.endswith(".") and not _ADVICE.search(out.msg), out.msg
    return out.msg


def told(s, pid):
    return [n.text for n in s.where("notices", to=pid)]


SHOPPING = dict(to="Megan Pryce", amount=20, per="week", weeks=4, start="Friday 6 March", pays="me",
                what="towards the shopping")


def agreed(s, n=0):
    """Ruth proposes SHOPPING on day 0 and Megan accepts it; the obligation."""
    ok(s, "p01", "propose_agreement", n, **SHOPPING)
    rq = s.all("requests")[-1]
    ok(s, "p04", "answer", n + 1, request=rq.id, answer="accept")
    return s.all("obligations")[-1]


def lent(s, actor="p03", amount=1000, weeks=26, purpose="a van", n=0):
    ok(s, actor, "borrow", n, amount=amount, weeks=weeks, purpose=purpose)
    rq = s.all("requests")[-1]
    ok(s, "p05", "answer", n + 1, request=rq.id, answer="accept")
    return s.all("obligations")[-1]


# agreements

def test_an_agreement_is_one_thing_listed_for_both_with_its_start_date():
    s = deals_town()
    out = ok(s, "p01", "propose_agreement", **SHOPPING)
    rq = s.all("requests")[-1]
    assert out.msg == f"Sent to Megan Pryce ({rq.id}). It stays open until Monday 16 March."
    summary = ('Ruth Ashby pays Megan Pryce $20.00 every week from Friday 6 March for 4 weeks ($80.00 in all): '
               '"towards the shopping".')
    assert (rq.kind, rq.to, rq.cents, rq.summary) == ("agreement", "p04", 0, summary)
    assert s.where("obligations", kind="agreement") == []
    live(s, 1)
    out = ok(s, "p04", "answer", 1, request=rq.id, answer="accept")
    ob = s.all("obligations")[-1]
    assert out.msg == f"Accepted. Agreed ({ob.id}): {summary}"
    assert told(s, "p01")[-1] == f'Megan Pryce accepted your request {rq.id} ("towards the shopping"). ' \
                                 f"Agreed ({ob.id}): {summary}"
    assert (ob.kind, ob.payer, ob.payee, ob.cents, ob.first, ob.every, ob.last, ob.ends, ob.made_by, ob.memo) == \
        ("agreement", "h1", "h2", 2000, 4, 7, 25, "both", "p01", '"towards the shopping"')
    line = f'{ob.id}: "towards the shopping", $20.00 every week to Pryce, first Friday 6 March, ' \
           f"until Friday 27 March, active"
    assert line in agreements_text(s, "h1").split("\n")
    assert line.replace("to Pryce", "from Ashby") in agreements_text(s, "h2").split("\n")
    assert any(r.startswith(f"{ob.id}: ") for r in owed_lines(s, "h1"))
    assert any(r.startswith(f"{ob.id}: ") for r in owed_lines(s, "h2"))
    set_cash(s, "h1", 100_000)
    to_day(s, 30)
    assert [(t.t, t.cents) for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2"] == \
        [(4, 2000), (11, 2000), (18, 2000), (25, 2000)]
    assert s.get("obligations", ob.id).status == "finished"


def test_a_single_payment_and_one_until_ended_are_worded_so():
    s = deals_town()
    ok(s, "p01", "propose_agreement", **{**SHOPPING, "per": "once", "weeks": 9})
    assert s.all("requests")[-1].summary == ('Ruth Ashby pays Megan Pryce $20.00 once on Friday 6 March: '
                                             '"towards the shopping".')
    ok(s, "p01", "propose_agreement", 1, **{**SHOPPING, "weeks": 0, "pays": "them", "start": None})
    rq = s.all("requests")[-1]
    assert rq.summary == ('Megan Pryce pays Ruth Ashby $20.00 every week from Tuesday 3 March until either side '
                          'ends it: "towards the shopping".')
    ok(s, "p04", "answer", 2, request=rq.id, answer="accept")
    ob = s.all("obligations")[-1]
    assert (ob.payer, ob.payee, ob.first, ob.last) == ("h2", "h1", 1, None)


def test_what_was_only_said_in_a_letter_is_listed_nowhere():
    s = deals_town()
    before = (agreements_text(s, "h1"), agreements_text(s, "h2"), len(s.all("obligations")))
    ok(s, "p01", "message", to="Megan Pryce", text="Forty a week, standing order, from Friday.")
    ok(s, "p04", "message", 1, to="Ruth Ashby", text="Agreed: forty a week from Friday.")
    live(s, 7)
    assert (agreements_text(s, "h1"), agreements_text(s, "h2"), len(s.all("obligations"))) == before
    assert not [o for o in s.all("obligations") if o.kind in ("agreement", "standing")]


def test_terms_are_checked_again_when_accepted():
    s = deals_town()
    ok(s, "p01", "propose_agreement", **{**SHOPPING, "from": "Marsh's Stores"})
    rq = s.all("requests")[-1]
    assert rq.summary.startswith("Marsh's Stores pays Megan Pryce $20.00 every week")
    s.set("orgs", "org3", T, owner="p02")               # Ruth has since left the shop she proposed for
    msg = refused(s, "p04", "answer", request=rq.id, answer="accept")
    assert msg == "Not done: Ruth Ashby no longer owns Marsh's Stores."
    assert s.get("requests", rq.id).status == "open"
    # a start that has passed moves to the next same weekday, and acceptance says so
    s = deals_town()
    ok(s, "p01", "propose_agreement", **SHOPPING)
    rq = s.all("requests")[-1]
    to_day(s, 5)
    out = ok(s, "p04", "answer", request=rq.id, answer="accept")
    ob = s.all("obligations")[-1]
    assert out.msg == (f"Accepted. Agreed ({ob.id}): Ruth Ashby pays Megan Pryce $20.00 every week from "
                       f'Friday 13 March for 4 weeks ($80.00 in all): "towards the shopping". '
                       f"The first date proposed, Friday 6 March, had passed.")
    assert (ob.first, ob.last) == (11, 32)
    # one accepted on its first day starts a week on, as that day's payments have been taken
    s2 = deals_town()
    ok(s2, "p01", "propose_agreement", **SHOPPING)
    to_day(s2, 4)
    out = ok(s2, "p04", "answer", request=s2.all("requests")[-1].id, answer="accept")
    assert out.msg.endswith("from Friday 13 March for 4 weeks ($80.00 in all): \"towards the shopping\". "
                            "The first date proposed, Friday 6 March, is today.")
    # proposals that would start in the past, or today, are refused
    assert refused(s, "p01", "propose_agreement", **{**SHOPPING, "start": "Monday 2 March"}) == \
        "Not done: Monday 2 March has passed."
    assert refused(s, "p01", "propose_agreement", **{**SHOPPING, "start": "Saturday 7 March"}) == \
        "Not done: Saturday 7 March is today."


def test_ending_by_agreement_is_no_breach():
    s = deals_town()
    ob = agreed(s)
    set_cash(s, "h1", 100_000)
    to_day(s, 5)
    out = ok(s, "p04", "propose_end", id=ob.id)
    rq = s.all("requests")[-1]
    summary = f'Megan Pryce asks to end "towards the shopping" ({ob.id}): no payment after Thursday 12 March.'
    assert (rq.kind, rq.to, rq.ob, rq.first, rq.expires, rq.summary) == ("end", "p01", ob.id, 10, 10, summary)
    assert out.msg == f"Sent to Ruth Ashby ({rq.id}). It stays open until Thursday 12 March."
    live(s, 1)
    out = ok(s, "p01", "answer", request=rq.id, answer="accept")
    done = f'Ended ({ob.id}): "towards the shopping", no payment after Thursday 12 March.'
    assert out.msg == f"Accepted. {done}"
    assert told(s, "p04")[-1].endswith(done)
    assert s.get("obligations", ob.id).status == "finished" and s.get("obligations", ob.id).last == 10
    assert s.all("records") == []
    to_day(s, 30)
    assert [t.t for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2"] == [4]
    assert refused(s, "p04", "propose_end", id=ob.id) == f"Not done: you have no agreement in force with the id '{ob.id}'."


# loans

def test_a_loan_of_1000_over_26_weeks_repays_4077_a_week_and_1060_in_all():
    s = deals_town()
    out = ok(s, "p03", "borrow", amount=1000, weeks=26, purpose="a van")
    rq = s.all("requests")[-1]
    assert out.msg == f"Sent to Dora Penn ({rq.id}). It stays open until Sunday 8 March."
    summary = ('Craig Pryce asks the bank for $1,000.00 over 26 weeks, repaying $40.77 a week ($1,060.00 in all) '
               'from Friday 13 March, the last, on Friday 4 September, $40.75: "a van". '
               'They now owe the bank $0.00 on 0 loans, $0.00 of it overdue.')
    assert (rq.kind, rq.to, rq.pay, rq.times, rq.first, rq.summary) == ("loan", "p05", 100_000, 26, 11, summary)
    out = ok(s, "p05", "answer", 1, request=rq.id, answer="accept")
    ob = s.all("obligations")[-1]
    assert (ob.kind, ob.payer, ob.payee, ob.cents, ob.final_cents, ob.first, ob.every, ob.last, ob.ends) == \
        ("loan", "h2", "org4", 4077, 4075, 11, 7, 186, "both")
    assert 4077 * 25 + 4075 == 106_000
    done = (f"Wenlow Bank lends Craig Pryce $1,000.00 ({ob.id}): $40.77 a week from Friday 13 March "
            f"($1,060.00 in all), the last, on Friday 4 September, $40.75.")
    assert out.msg == f"Accepted. {done}"
    assert told(s, "p03")[-1].endswith(done)
    assert any(t.person == "p03" and t.other == "p05" and t.kind == "banker" for t in s.all("ties"))
    line = (f'{ob.id}: Pryce, loan for "a van", $40.77 a week, the last, on Friday 4 September, $40.75')
    assert line in bank_book(s, "p05").split("\n")
    assert f'{ob.id}: loan for "a van", $40.77 a week to Wenlow Bank, the last, on Friday 4 September, $40.75' \
        in owed_lines(s, "h2")
    assert "the last, on Friday 4 September, $40.75, active" in agreements_text(s, "h2")
    # a second loan's request counts the first
    ok(s, "p04", "borrow", 2, amount=50, weeks=4, purpose="shoes")
    assert s.all("requests")[-1].summary.endswith("They now owe the bank $1,060.00 on 1 loan, $0.00 of it overdue.")
    assert [u.cents for u in O.upcoming(s, "h2", 200) if u.obligation == ob.id][-1] == 4075


def test_the_banker_decides_and_the_money_moves_once():
    s = deals_town()
    ok(s, "p03", "borrow", amount=1000, weeks=26, purpose="a van")
    rq = s.all("requests")[-1]
    assert refused(s, "p04", "answer", request=rq.id, answer="accept").startswith("Not done: you have no open request")
    ok(s, "p05", "answer", 1, request=rq.id, answer="decline")
    assert [t for t in s.ledger.transfers if t.src == "org4"] == []
    ok(s, "p03", "borrow", 2, amount=1000, weeks=26, purpose="a van")
    rq = s.all("requests")[-1]
    set_cash(s, "org4", 50_000)
    assert refused(s, "p05", "answer", request=rq.id, answer="accept") == \
        "Not done: Wenlow Bank holds $500.00 and this needs $1,000.00."
    set_cash(s, "org4", 5_000_000)
    ok(s, "p05", "answer", 3, request=rq.id, answer="accept")
    refused(s, "p05", "answer", request=rq.id, answer="accept")
    out = [t for t in s.ledger.transfers if t.src == "org4" and t.dst == "h2"]
    assert [(t.cents, t.memo) for t in out] == [(100_000, 'loan: "a van"')]
    assert refused(s, "p05", "borrow", amount=100, weeks=10, purpose="x") == "Not done: you are the banker."
    assert refused(s, "p03", "borrow", amount=49, weeks=10, purpose="x") == \
        "Not done: '49' is not an amount from $50.00 to $20,000.00."
    assert refused(s, "p03", "borrow", amount=100, weeks=105, purpose="x") == \
        "Not done: '105' is not a whole number of weeks from 4 to 104."


def test_repaying_early_shortens_the_loan_from_its_end():
    s = deals_town()
    ob = lent(s)
    set_cash(s, "h2", 200_000)
    assert refused(s, "p03", "repay", id=ob.id, amount=1060.01) == "Not done: that is more than the $1,060.00 still owed."
    out = ok(s, "p03", "repay", 5, id=ob.id, amount=100)
    after = s.get("obligations", ob.id)
    assert (after.last, after.final_cents, after.status) == (179, 2229, "active")
    assert out.msg == (f'Repaid $100.00 to Wenlow Bank on loan for "a van" ({ob.id}): $960.00 still owed, '
                       f"the last payment, $22.29, on Friday 28 August.")
    assert [(d.day, d.cents, d.paid, d.status) for d in s.where("dues", obligation=ob.id)] == [(11, 4077, 4077, "paid")]
    assert [t.cents for t in s.ledger.transfers if t.src == "h2" and t.dst == "org4"] == [10_000]
    assert f"{ob.id}: Pryce, loan for \"a van\", $40.77 a week, the last, on Friday 28 August, $22.29" in bank_book(s, "p05")
    # the rest in one sum: nothing more is owed
    out = ok(s, "p03", "repay", 6, id=ob.id, amount=960)
    assert out.msg == f'Repaid $960.00 to Wenlow Bank on loan for "a van" ({ob.id}): nothing more is owed on it.'
    assert s.get("obligations", ob.id).status == "finished"
    assert refused(s, "p03", "repay", id=ob.id, amount=1).startswith("Not done: you have no loan or agreement")


def test_two_repayments_running_shorten_the_loan_twice():
    s = deals_town()
    ob = lent(s)
    set_cash(s, "h2", 200_000)
    ok(s, "p03", "repay", id=ob.id, amount=100)
    out = ok(s, "p04", "repay", 1, id=ob.id, amount=100)       # the next two instalments are now paid ahead
    after = s.get("obligations", ob.id)
    assert (after.last, after.final_cents, after.status) == (172, 383, "active")      # 4,077 + 2,229 + 3,694
    assert O.outstanding(s, after) == 86_000
    assert out.msg.endswith("$860.00 still owed, the last payment, $3.83, on Friday 21 August.")
    assert [(d.day, d.status) for d in s.where("dues", obligation=ob.id)] == [(11, "paid"), (18, "paid")]


def test_repaying_counts_toward_arrears_first():
    s = deals_town()
    ob = lent(s)
    set_cash(s, "h2", 0)
    s.set("obligations", next(o.id for o in s.where("obligations", payer="h2") if o.kind == "rent"), T,
          status="finished")
    for o in s.where("obligations", payee="h2"):
        s.set("obligations", o.id, T, status="finished")          # nothing comes in
    to_day(s, 12)
    assert [d.status for d in s.where("dues", obligation=ob.id)] == ["arrears"]
    set_cash(s, "h2", 10_000)
    ok(s, "p03", "repay", id=ob.id, amount=50)
    dues = s.where("dues", obligation=ob.id)
    assert [(d.day, d.paid, d.status) for d in dues] == [(11, 4077, "paid"), (18, 923, "due")]
    assert s.get("obligations", ob.id).last == 186


def test_a_loan_repaid_by_repay_is_not_also_taken_in_full_by_its_schedule():
    s = deals_town()
    ob = lent(s)
    set_cash(s, "h2", 10_000_000)
    ok(s, "p03", "repay", 5, id=ob.id, amount=100)
    to_day(s, 200)
    taken = [t.cents for t in s.ledger.transfers if t.src == "h2" and t.dst == "org4"]
    assert sum(taken) == 106_000 and taken[0] == 10_000 and taken[-1] == 2229 and len(taken) == 1 + 23 + 1
    assert s.get("obligations", ob.id).status == "finished"
    assert all(d.day <= 179 for d in s.where("dues", obligation=ob.id))


# rent

def test_a_rent_change_takes_28_days_and_the_tenant_is_told():
    s = deals_town()
    set_cash(s, "h2", 10_000_000)
    old = next(o for o in s.where("obligations", payer="h2") if o.kind == "rent")
    out = ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=200)
    assert out.msg == "The rent for 4 Mill Lane changes from $185.00 to $200.00 a week from Monday 30 March."
    for pid in ("p03", "p04"):
        assert told(s, pid)[-1] == "Your rent for 4 Mill Lane changes from $185.00 to $200.00 a week from Monday 30 March."
    first = s.all("obligations")[-1]
    assert (first.kind, first.payer, first.payee, first.cents, first.first, first.memo) == \
        ("rent", "h2", "h4", 20_000, 28, old.memo)
    assert s.get("obligations", old.id).last == 27
    live(s, 3)
    ok(s, "p06", "set_rent", tenant="Megan", weekly=210)
    assert told(s, "p04")[-1] == "Your rent for 4 Mill Lane changes from $185.00 to $210.00 a week from Monday 6 April."
    assert s.get("obligations", first.id).status == "finished"
    assert s.get("obligations", old.id).last == 34
    to_day(s, 43)
    assert [(t.t, t.cents) for t in s.ledger.transfers if t.src == "h2" and t.dst == "h4"] == \
        [(0, 18_500), (7, 18_500), (14, 18_500), (21, 18_500), (28, 18_500), (35, 21_000), (42, 21_000)]
    assert s.where("dues", obligation=first.id) == []
    assert refused(s, "p03", "set_rent", tenant="Craig Pryce", weekly=1) == \
        "Not done: there is no operation called 'set_rent'."
    assert refused(s, "p06", "set_rent", tenant="Ruth Ashby", weekly=200) == \
        "Not done: you do not let the house Ruth Ashby lives in."
    assert refused(s, "p06", "set_rent", tenant="Craig Pryce", weekly=19.99) == \
        "Not done: '19.99' is not an amount from $20.00 to $2,000.00."


def _behind(s, days):
    """The Pryces with nothing coming in and nothing in the account, for `days` days."""
    set_cash(s, "h2", 0)
    for o in s.where("obligations", payee="h2"):
        s.set("obligations", o.id, T, status="finished")
    to_day(s, days)


def test_notice_needs_14_days_of_arrears():
    s = deals_town()
    _behind(s, 13)
    assert refused(s, "p06", "serve_notice", tenant="Craig Pryce") == \
        "Not done: no rent for 4 Mill Lane has been unpaid for 14 days."
    live(s, 1)
    out = ok(s, "p06", "serve_notice", tenant="Craig Pryce")
    assert out.msg == "You have served notice on the Pryce household to leave 4 Mill Lane by Monday 13 April."
    rec = s.all("records")[-1]
    assert (rec.kind, rec.about, rec.day, rec.text) == \
        ("notice to quit", "h2", 14, "Len Hale has served notice on the Pryce household to leave 4 Mill Lane by "
                                     "Monday 13 April.")
    for pid in ("p03", "p04"):
        assert told(s, pid)[-1] == "Len Hale has served notice: you are to leave 4 Mill Lane by Monday 13 April."
    live(s, 1)
    assert refused(s, "p06", "serve_notice", tenant="Megan Pryce") == \
        "Not done: notice was served on the Pryce household on Monday 16 March."


def test_summaries_stay_within_their_bounds():
    s = deals_town()
    s.set("people", "p04", T, name="Megan " + "Q" * 94)
    s.set("people", "p01", T, name="Ruth " + "R" * 95)
    s.set("orgs", "org4", T, name="B" * 100)
    ok(s, "p01", "propose_agreement", **{**SHOPPING, "to": "Megan " + "Q" * 94, "what": " ".join(["w" * 7] * 12)})
    ok(s, "p03", "borrow", 1, amount=20000, weeks=104, purpose=" ".join(["v" * 7] * 12))
    for rq in s.all("requests"):
        assert len(rq.summary) <= 480 and len(rq.summary.split()) <= 60, rq.summary


# every argument, against junk

def _agreement(s):
    ok(s, "p01", "propose_agreement", 90, **SHOPPING)
    return {"request": s.all("requests")[-1].id, "answer": "accept", "note": "fine"}


def _end(s):
    ob = agreed(s, 90)
    ok(s, "p04", "propose_end", 92, id=ob.id)
    return {"request": s.all("requests")[-1].id, "answer": "accept", "note": "fine"}


def _loan(s):
    ok(s, "p03", "borrow", 90, amount=1000, weeks=26, purpose="a van")
    return {"request": s.all("requests")[-1].id, "answer": "accept", "note": "fine"}


def _end_args(s):
    return {"id": agreed(s, 90).id}


def _repay_args(s):
    return {"id": lent(s, n=90).id, "amount": 100, "from": "household"}


@functools.cache
def _behind_town() -> bytes:
    s = deals_town()
    _behind(s, 14)
    return pickle.dumps(s)


def behind_town():
    return pickle.loads(_behind_town())


# name -> (actor, operation, the town, valid arguments)
VALID = {
    "propose_agreement": ("p01", "propose_agreement", deals_town, lambda s: {**SHOPPING, "from": "Marsh's Stores"}),
    "propose_end": ("p04", "propose_end", deals_town, _end_args),
    "borrow": ("p01", "borrow", deals_town, lambda s: {"amount": 1000, "weeks": 26, "purpose": "a van",
                                                       "from": "Marsh's Stores"}),
    "repay": ("p03", "repay", deals_town, _repay_args),
    "set_rent": ("p06", "set_rent", deals_town, lambda s: {"tenant": "Craig Pryce", "weekly": 200}),
    "serve_notice": ("p06", "serve_notice", behind_town, lambda s: {"tenant": "Craig Pryce"}),
    "answer an agreement": ("p04", "answer", deals_town, _agreement),
    "answer an end": ("p01", "answer", deals_town, _end),
    "answer a loan": ("p05", "answer", deals_town, _loan),
}
CASES = [(name, key) for name, (_, _, make_town, make) in VALID.items() for key in make(make_town())]


def test_these_are_the_new_operations_in_order():
    assert [n for n in REGISTRY if n in NEW] == NEW
    assert [REGISTRY[n].order for n in NEW] == [180, 190, 200, 210, 220, 230]
    assert [REGISTRY[n].who for n in NEW] == ["all", "all", "all", "all", "landlord", "landlord"]
    assert [REGISTRY[n].takes_from for n in NEW] == [True, False, True, True, False, False]
    assert [REGISTRY[n].hours for n in NEW] == [0.5, 0.25, 1.0, 0.25, 0.25, 0.5]


def test_the_valid_acts_are_valid_and_take_their_hours():
    for name, (actor, op, make_town, make) in VALID.items():
        s = make_town()
        args = make(s)
        before = s.get("people", actor).hours_left
        out = apply_act(s, Act(actor, actor, op, args), 0)
        assert out.ok, (name, out.msg)
        assert s.get("people", actor).hours_left == pytest.approx(before - REGISTRY[op].hours), name
        assert s.ledger.total() == 0 and s.engine_errors == []


@pytest.mark.parametrize("name,key", CASES)
def test_junk_in_every_argument_of_every_new_operation(name, key):
    actor, op, make_town, make = VALID[name]
    for value in JUNK + ["<missing>"]:
        s = make_town()
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
    s = deals_town()
    ob = agreed(s, 50)
    loan = lent(s, n=60)
    bad = [("p01", "propose_agreement", {**SHOPPING, "to": "Ruth Ashby"}),
           ("p01", "propose_agreement", {**SHOPPING, "to": "Tom Ashby"}),
           ("p01", "propose_agreement", {**SHOPPING, "per": "month"}),
           ("p01", "propose_agreement", {**SHOPPING, "weeks": 521}),
           ("p01", "propose_agreement", {**SHOPPING, "start": "31 February"}),
           ("p01", "propose_agreement", {**SHOPPING, "pays": "both"}),
           ("p01", "propose_agreement", {**SHOPPING, "what": ""}),
           ("p01", "propose_agreement", {**SHOPPING, "what": "w " * 13}),
           ("p01", "propose_agreement", {**SHOPPING, "from": "Wenlow Bank"}),
           ("p01", "propose_end", {"id": "ob999"}),
           ("p05", "propose_end", {"id": ob.id}),
           ("p03", "propose_end", {"id": loan.id}),
           ("p03", "borrow", {"amount": 30000, "weeks": 26, "purpose": "a van"}),
           ("p03", "borrow", {"amount": 1000, "weeks": 3, "purpose": "a van"}),
           ("p03", "borrow", {"amount": 1000, "weeks": 26, "purpose": ""}),
           ("p03", "borrow", {"amount": 50, "weeks": 103, "purpose": "a van"}),
           ("p03", "repay", {"id": ob.id, "amount": 5}),
           ("p01", "repay", {"id": loan.id, "amount": 5}),
           ("p03", "repay", {"id": loan.id, "amount": "lots"}),
           ("p03", "repay", {"id": loan.id, "amount": 2000}),
           ("p06", "set_rent", {"tenant": "Craig Pryce", "weekly": 185}),
           ("p06", "set_rent", {"tenant": "Edith Marsh", "weekly": 200}),
           ("p06", "serve_notice", {"tenant": "Craig Pryce"}),
           ("p06", "serve_notice", {"tenant": "Tom Ashby"})]
    for actor, op, args in bad:
        refused(s, actor, op, **args)
    s.set("orgs", "org4", T, contact="")
    assert refused(s, "p03", "borrow", amount=100, weeks=10, purpose="x") == "Not done: the bank has no banker."
    s.set("orgs", "org4", T, kind="lender")
    assert refused(s, "p03", "borrow", amount=100, weeks=10, purpose="x") == "Not done: there is no bank in the town."
