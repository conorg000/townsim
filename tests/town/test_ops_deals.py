"""Agreements, loans and rent: a proposal accepted is one thing listed for both sides; the
banker lends the bank's money; repaying early shortens a loan from its end; a landlord sets
rent and serves notice. Every argument of every new operation, and the answers to the
requests they make, stand up to junk."""

import functools
import pickle
import random
import re

import pytest

from townsim.town import obligations as O
from townsim.town.acts import REGISTRY, Act, apply_act
from townsim.town.entities import Org
from townsim.town.facts import bank_book, owed_lines, who
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


def agreed(s, n=0, **terms):
    """Ruth proposes SHOPPING (with `terms` in place of its own) on day 0 and Megan accepts it;
    the obligation."""
    ok(s, "p01", "propose_agreement", n, **{**SHOPPING, **terms})
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
    assert out.msg == f"Sent to Megan Pryce ({rq.id}). It stays open until Thursday 5 March."
    summary = ('Ruth Ashby pays Megan Pryce $20.00 every week from Friday 6 March for 4 weeks ($80.00 in all): '
               '"towards the shopping".')
    assert (rq.kind, rq.to, rq.cents, rq.text, rq.summary) == ("agreement", "p04", 0, "", summary)
    assert s.where("obligations", kind="agreement") == []
    live(s, 1)
    out = ok(s, "p04", "answer", 1, request=rq.id, answer="accept")
    ob = s.all("obligations")[-1]
    assert out.msg == f"Accepted. Agreed ({ob.id}): {summary}"
    assert told(s, "p01")[-1].endswith(f"Agreed ({ob.id}): {summary}")
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
    assert rq.summary == ('Megan Pryce pays Ruth Ashby $20.00 every week from Wednesday 4 March until either side '
                          'ends it: "towards the shopping".')            # no start written: the day after tomorrow
    live(s, 1)
    ok(s, "p04", "answer", 2, request=rq.id, answer="accept")
    ob = s.all("obligations")[-1]
    assert (ob.payer, ob.payee, ob.first, ob.last) == ("h2", "h1", 2, None)


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
    # proposals that would start in the past, or today, are refused
    to_day(s, 5)
    assert refused(s, "p01", "propose_agreement", **{**SHOPPING, "start": "Monday 2 March"}) == \
        "Not done: Monday 2 March has passed."
    assert refused(s, "p01", "propose_agreement", **{**SHOPPING, "start": "Saturday 7 March"}) == \
        "Not done: Saturday 7 March is today."
    assert refused(s, "p01", "propose_agreement", **{**SHOPPING, "start": "Sunday 8 March"}) == \
        "Not done: an agreement can start no sooner than the day after tomorrow."
    out = ok(s, "p01", "propose_agreement", **{**SHOPPING, "start": "Monday 9 March"})
    assert out.msg.endswith("It stays open until Sunday 8 March.")


def test_an_agreement_lapses_before_it_would_start():
    s = deals_town()
    ok(s, "p01", "propose_agreement", **SHOPPING)
    rq = s.all("requests")[-1]
    to_day(s, 3)                                                 # its last day: Thursday, the day before it starts
    out = ok(s, "p04", "answer", request=rq.id, answer="accept")
    ob = s.all("obligations")[-1]
    assert out.msg == f"Accepted. Agreed ({ob.id}): {rq.summary}" and (ob.first, ob.last) == (4, 25)
    s = deals_town()
    ok(s, "p01", "propose_agreement", **SHOPPING)
    rq = s.all("requests")[-1]
    to_day(s, 4)
    assert refused(s, "p04", "answer", request=rq.id, answer="accept").startswith("Not done: you have no open request")
    live(s, 1)
    assert s.get("requests", rq.id).status == "lapsed"
    assert s.where("obligations", kind="agreement") == []
    # fourteen days at most, for a start further off
    s = deals_town()
    ok(s, "p01", "propose_agreement", **{**SHOPPING, "start": "Friday 3 April"})
    assert s.all("requests")[-1].expires == 14


def test_ending_says_what_was_already_paid_toward_the_days_after():
    s = deals_town()
    ob = agreed(s)
    live(s, 2)                                                   # Wednesday
    ok(s, "p01", "repay", id=ob.id, amount=5)                    # toward Friday's instalment
    assert [(d.day, d.paid, d.status) for d in s.where("dues", obligation=ob.id)] == [(4, 500, "due")]
    ok(s, "p01", "propose_end", 1, id=ob.id)
    rq = s.all("requests")[-1]
    assert (rq.to, rq.first, rq.pay, rq.text) == ("p03", 3, 500, "")
    assert rq.summary == (f'Ruth Ashby asks to end "towards the shopping" ({ob.id}): no payment after Thursday '
                          f"5 March. $5.00 already paid toward Friday 6 March stays with the Pryce household.")
    ok(s, "p01", "repay", 2, id=ob.id, amount=5)                 # the figure changes before it is answered
    assert refused(s, "p03", "answer", request=rq.id, answer="accept") == (
        f"Not done: what was paid ahead on {ob.id} has changed since {rq.id} was made. $10.00 already paid "
        f"toward Friday 6 March stays with the Pryce household.")
    ok(s, "p01", "propose_end", 3, id=ob.id)                     # asked again, with the new figure
    rq = s.all("requests")[-1]
    live(s, 1)
    ok(s, "p03", "answer", request=rq.id, answer="accept")
    assert s.get("obligations", ob.id).status == "finished"
    assert [(d.day, d.paid, d.status) for d in s.where("dues", obligation=ob.id)] == [(4, 1000, "void")]
    assert '$10.00 already paid toward "towards the shopping" due Friday 6 March stays with Pryce.' in told(s, "p01")
    assert '$10.00 already paid toward "towards the shopping" due Friday 6 March is yours to keep.' in told(s, "p04")
    to_day(s, 30)
    assert [t.cents for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2"] == [500, 500]
    assert refused(s, "p01", "repay", id=ob.id, amount=1).startswith("Not done: you have no loan or agreement")


def test_ending_a_single_payment_already_paid_toward():
    s = deals_town()
    ob = agreed(s, **{"per": "once"})
    ok(s, "p01", "repay", 5, id=ob.id, amount=5)
    ok(s, "p01", "propose_end", 6, id=ob.id)
    rq = s.all("requests")[-1]
    assert rq.summary.endswith("no payment after Thursday 5 March. $5.00 already paid toward Friday 6 March stays "
                               "with the Pryce household.")
    ok(s, "p03", "answer", 7, request=rq.id, answer="accept")
    assert s.get("obligations", ob.id).status == "finished"
    assert [(d.paid, d.status) for d in s.where("dues", obligation=ob.id)] == [(500, "void")]
    assert told(s, "p04")[-1].endswith("is yours to keep.")
    to_day(s, 10)
    assert [t.cents for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2"] == [500]
    # one due tomorrow has nothing after it to end
    s = deals_town()
    ok(s, "p01", "propose_agreement", **{**SHOPPING, "per": "once", "start": "Wednesday 4 March"})
    ok(s, "p04", "answer", 1, request=s.all("requests")[-1].id, answer="accept")
    once = s.all("obligations")[-1]
    live(s, 1)
    assert refused(s, "p01", "propose_end", id=once.id) == (
        f"Not done: {once.id} has one payment left, on Wednesday 4 March, and that is tomorrow: there is nothing "
        f"after it to end.")


def test_repaying_an_agreement_pays_it_ahead_and_never_past_its_end():
    s = deals_town()
    ob = agreed(s)
    set_cash(s, "h1", 100_000)
    assert refused(s, "p01", "repay", id=ob.id, amount=80.01) == "Not done: that is more than the $80.00 still owed."
    ok(s, "p01", "repay", 5, id=ob.id, amount=50)
    assert [(d.day, d.paid, d.status) for d in s.where("dues", obligation=ob.id)] == \
        [(4, 2000, "paid"), (11, 2000, "paid"), (18, 1000, "due")]
    assert s.get("obligations", ob.id).last == 25
    ok(s, "p01", "repay", 6, id=ob.id, amount=30)
    assert s.get("obligations", ob.id).status == "finished"
    to_day(s, 30)
    assert sum(t.cents for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2") == 8000
    assert all(d.day <= 25 and d.status == "paid" for d in s.where("dues", obligation=ob.id))


def test_a_breach_paid_off_in_full_is_finished():
    s = deals_town()
    ob = agreed(s, **{"weeks": 2})
    ok(s, "p01", "stop", 5, id=ob.id)
    assert s.get("obligations", ob.id).status == "breached"
    to_day(s, 12)
    assert [d.status for d in s.where("dues", obligation=ob.id)] == ["arrears", "arrears"]
    set_cash(s, "h1", 100_000)
    ok(s, "p01", "pay", to="Megan Pryce", amount=40, **{"for": ob.id})
    assert s.get("obligations", ob.id).status == "finished"


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
    assert out.msg == f"Sent to Dora Penn ({rq.id}). It stays open until Thursday 12 March."
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


def test_a_loan_asked_for_at_the_weekend_names_its_first_friday_and_stays_open_until_the_day_before():
    s = deals_town()
    to_day(s, 5)                                                 # Saturday 7 March
    ok(s, "p03", "borrow", amount=1000, weeks=26, purpose="a van")
    rq = s.all("requests")[-1]
    assert (rq.first, rq.expires, rq.text) == (11, 10, "")
    assert "from Friday 13 March" in rq.summary
    to_day(s, 10)
    ok(s, "p05", "answer", request=rq.id, answer="accept")
    assert s.all("obligations")[-1].first == 11


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

def _rent_paid(s, since=0):
    """(day, cents) of what the Pryces paid their landlord from day `since`, late fees aside."""
    return [(t.t, t.cents) for t in s.ledger.transfers
            if t.src == "h2" and t.dst == "h4" and t.t >= since and not t.memo.startswith("late fee")]


def test_a_rent_change_takes_28_days_and_the_tenant_is_told():
    s = deals_town()
    set_cash(s, "h2", 10_000_000)
    rent = next(o for o in s.where("obligations", payer="h2") if o.kind == "rent")
    n = len(s.all("obligations"))
    out = ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=200)
    assert out.msg == "The rent for 4 Mill Lane changes from $185.00 to $200.00 a week from Monday 30 March."
    for pid in ("p03", "p04"):
        assert told(s, pid)[-1] == "Your rent for 4 Mill Lane changes from $185.00 to $200.00 a week from Monday 30 March."
    ob = s.get("obligations", rent.id)                     # one rent for the let, re-priced
    assert len(s.all("obligations")) == n and (ob.cents, ob.next_cents, ob.next_from, ob.last) == (18_500, 20_000, 28, None)
    assert f"{rent.id}: rent, $185.00 every week to Hale, $200.00 from Monday 30 March, first" in agreements_text(s, "h2")
    live(s, 3)
    # (a change made on day 22, after a rent day paid at the old amount, is made the same way)
    s2 = pickle.loads(pickle.dumps(s))
    to_day(s2, 22)
    assert refused(s2, "p06", "set_rent", tenant="Craig Pryce", weekly=200) == \
        "Not done: the rent for 4 Mill Lane is to be $200.00 a week from Monday 30 March already."
    out = ok(s2, "p06", "set_rent", tenant="Craig Pryce", weekly=220)
    assert out.msg == ("The rent for 4 Mill Lane changes from $185.00 to $220.00 a week from Monday 27 April. "
                       "This replaces the change to $200.00 that was to start on Monday 30 March.")
    to_day(s2, 57)
    assert _rent_paid(s2, 21) == [(21, 18_500), (28, 18_500), (35, 18_500), (42, 18_500), (49, 18_500), (56, 22_000)]
    out = ok(s, "p06", "set_rent", tenant="Megan", weekly=210)      # more than the change pending: 28 days from today
    assert told(s, "p04")[-1] == ("Your rent for 4 Mill Lane changes from $185.00 to $210.00 a week from Monday 6 April. "
                                  "This replaces the change to $200.00 that was to start on Monday 30 March.")
    to_day(s, 43)
    assert _rent_paid(s) == [(0, 18_500), (7, 18_500), (14, 18_500), (21, 18_500), (28, 18_500), (35, 21_000),
                             (42, 21_000)]
    assert [o.id for o in s.where("obligations", payer="h2") if o.kind == "rent"] == [rent.id]
    assert refused(s, "p03", "set_rent", tenant="Craig Pryce", weekly=1) == \
        "Not done: there is no operation called 'set_rent'."
    assert refused(s, "p06", "set_rent", tenant="Ruth Ashby", weekly=200) == \
        "Not done: you do not let the house Ruth Ashby lives in."
    assert refused(s, "p06", "set_rent", tenant="Craig Pryce", weekly=19.99) == \
        "Not done: '19.99' is not an amount from $20.00 to $2,000.00."


def test_taking_back_a_rise_needs_no_notice():
    s = deals_town()
    set_cash(s, "h2", 10_000_000)
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=200)                  # from Monday 30 March
    to_day(s, 10)
    out = ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=190)
    assert out.msg == ("The rent for 4 Mill Lane changes from $185.00 to $190.00 a week from Monday 30 March. "
                       "This replaces the change to $200.00 that was to start on Monday 30 March.")
    out = ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=185)
    assert out.msg == ("The rent for 4 Mill Lane stays $185.00 a week. "
                       "This replaces the change to $190.00 that was to start on Monday 30 March.")
    assert told(s, "p03")[-1] == "Your " + out.msg.removeprefix("The ")
    to_day(s, 36)
    assert _rent_paid(s) == [(d, 18_500) for d in (0, 7, 14, 21, 28, 35)]


def test_a_rent_changed_twice_with_a_rent_day_paid_by_hand_is_taken_once_a_day():
    """The reviewer's sequence: changes on days 0, 3, 22 and 29, the tenant paying the day-28
    rent by hand on day 22; every rent day is taken once, at the amount last told for it."""
    s = deals_town()
    set_cash(s, "h2", 10_000_000)
    rent = next(o for o in s.where("obligations", payer="h2") if o.kind == "rent")
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=200)                  # from day 28
    to_day(s, 3)
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=210)                  # more: from day 35
    to_day(s, 22)
    out = ok(s, "p03", "pay", to="Len Hale", amount=185, **{"for": rent.id})
    assert out.msg == "Paid $185.00 to Len Hale. It was counted toward rent due Monday 30 March."
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=220)                  # more: from day 56
    to_day(s, 29)
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=230)                  # more: from day 63
    to_day(s, 80)
    dues = sorted((d.day, d.cents, d.paid, d.status) for d in s.where("dues", obligation=rent.id))
    assert [d[0] for d in dues] == list(range(0, 78, 7)) and all(d[3] == "paid" and d[1] == d[2] for d in dues)
    assert [d[1] for d in dues] == [18_500] * 9 + [23_000] * 3
    assert _rent_paid(s) == [(d, 18_500) for d in (0, 7, 14, 21)] + [(22, 18_500)] + \
        [(d, 18_500) for d in (35, 42, 49, 56)] + [(d, 23_000) for d in (63, 70, 77)]
    assert [o.id for o in s.where("obligations", payer="h2") if o.kind == "rent"] == [rent.id]


def test_a_rent_paid_ahead_at_a_coming_amount_and_then_changed_is_taken_once():
    """The reviewer's second sequence: the tenant pays the day-28 rent at its new amount on day
    22, and the landlord changes it again: day 28 is paid once, at the amount paid toward it."""
    s = deals_town()
    set_cash(s, "h2", 10_000_000)
    rent = next(o for o in s.where("obligations", payer="h2") if o.kind == "rent")
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=200)
    to_day(s, 22)
    ok(s, "p03", "pay", to="Len Hale", amount=200)
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=210)
    to_day(s, 64)
    assert _rent_paid(s, 21) == [(21, 18_500), (22, 20_000)] + [(d, 20_000) for d in (35, 42, 49)] + \
        [(d, 21_000) for d in (56, 63)]
    assert sorted(d.day for d in s.where("dues", obligation=rent.id)) == list(range(0, 64, 7))


def test_a_change_of_rent_leaves_a_breached_rent_breached_at_the_new_price():
    s = deals_town()
    set_cash(s, "h2", 10_000_000)
    rent = next(o for o in s.where("obligations", payer="h2") if o.kind == "rent")
    assert do(s, "p03", "stop", id=rent.id).ok
    to_day(s, 8)
    out = ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=200)
    assert out.msg == "The rent for 4 Mill Lane changes from $185.00 to $200.00 a week from Monday 13 April."
    to_day(s, 50)
    assert s.get("obligations", rent.id).status == "breached"
    assert _rent_paid(s, 1) == []                                     # nothing taken: it is withheld
    owed = sorted((d.day, d.cents, d.status) for d in s.where("dues", obligation=rent.id))
    assert [d[0] for d in owed] == list(range(0, 50, 7))
    assert [d[1] for d in owed] == [18_500] * 6 + [20_000] * 2 and all(d[2] != "paid" for d in owed[1:])


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


# what people are told of their lives stays true

def test_who_tells_the_rent_in_force_and_a_change_coming_on_both_sides():
    s = deals_town()
    set_cash(s, "h2", 10_000_000)
    rented = "The house is rented from Len Hale for $185.00 a week, paid on Mondays."
    assert rented in who(s, "p03").split("\n")
    assert "You let 4 Mill Lane to the Pryce household for $185.00 a week." in who(s, "p06").split("\n")
    ok(s, "p06", "set_rent", tenant="Craig Pryce", weekly=200)
    for day in (0, 27):
        to_day(s, day)
        assert f"{rented} The rent changes to $200.00 a week from Monday 30 March." in who(s, "p03").split("\n")
        assert ("You let 4 Mill Lane to the Pryce household for $185.00 a week; it changes to $200.00 from "
                "Monday 30 March.") in who(s, "p06").split("\n")
        assert "rent" not in " ".join(line for line in who(s, "p03").split("\n") if line.startswith("Your household owes"))
    to_day(s, 28)
    assert "The house is rented from Len Hale for $200.00 a week, paid on Mondays." in who(s, "p03").split("\n")
    assert "You let 4 Mill Lane to the Pryce household for $200.00 a week." in who(s, "p06").split("\n")
    assert "changes" not in who(s, "p03") + who(s, "p06")


def test_a_last_payment_is_the_day_its_last_instalment_falls():
    s = deals_town()
    ob = agreed(s)
    O.end(s, ob.id, 20, T)                                       # a Sunday: the last instalment is Friday 20 March
    assert s.get("obligations", ob.id).last == 20
    assert "until Friday 20 March, active" in agreements_text(s, "h1")
    assert any(r.endswith("until Friday 20 March") for r in owed_lines(s, "h1"))
    assert "the last payment on Friday 20 March." in who(s, "p04")
    assert "Sunday 22 March" not in agreements_text(s, "h1") + " ".join(owed_lines(s, "h1")) + who(s, "p04")
    loan = lent(s, n=5)
    O.end(s, loan.id, 30, T)
    assert f"{loan.id}: Pryce, loan for \"a van\", $40.77 a week, until Friday 27 March" in bank_book(s, "p05")


def test_who_is_at_most_300_words_for_everyone():
    """On seeds 1 to 5 on day 0, and on seed 1 after eight weeks of the scripted life."""
    from townsim.town import gen, hamlet_script
    from townsim.town.loop import run_day
    from townsim.town.replies import Keys, ScriptedReplier
    stores = [gen.generate(seed, ["a", "b", "c", "d"]) for seed in range(1, 6)]
    s = gen.generate(1, ["a", "b", "c", "d"])
    keys = Keys("w")
    for _ in range(56):
        run_day(s, ScriptedReplier(hamlet_script.script), keys)
    for s in stores + [s]:
        for p in s.all("people"):
            assert len(who(s, p.id).split()) <= 300, (s.seed, s.day, p.id, len(who(s, p.id).split()))


def test_repaying_an_agreement_until_ended_never_finishes_it():
    s = deals_town()
    set_cash(s, "h1", 100_000)
    ob = agreed(s, weeks=0)                                      # Fridays from Friday 6 March, until ended
    out = ok(s, "p01", "repay", 2, id=ob.id, amount=20)
    assert out.msg == (f'Repaid $20.00 to Pryce on "towards the shopping" ({ob.id}): $20.00 toward the payment due '
                       f"Friday 6 March. It goes on every week until either side ends it.")
    assert s.get("obligations", ob.id).status == "active"
    assert refused(s, "p01", "repay", id=ob.id, amount=1) == "Not done: that is more than the $0.00 now due on it."
    to_day(s, 2)
    assert refused(s, "p01", "repay", id=ob.id, amount=20.01) == "Not done: that is more than the $0.00 now due on it."
    to_day(s, 5)                                                 # Saturday: next Friday is within the week
    assert refused(s, "p01", "repay", id=ob.id, amount=20.01) == "Not done: that is more than the $20.00 now due on it."
    ok(s, "p01", "repay", id=ob.id, amount=5)
    to_day(s, 19)
    assert [(t.t, t.cents) for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2"] == \
        [(0, 2000), (5, 500), (11, 1500), (18, 2000)]
    assert s.get("obligations", ob.id).status == "active"


def test_an_end_accepted_on_the_day_it_is_asked_changes_nothing_before_its_date():
    s = deals_town()
    set_cash(s, "h1", 100_000)
    ob = agreed(s)
    to_day(s, 3)                                                 # Thursday 5 March
    ok(s, "p01", "propose_end", id=ob.id)
    rq = s.all("requests")[-1]
    assert rq.first == 10 and rq.summary.endswith("no payment after Thursday 12 March.")
    ok(s, "p03", "answer", 1, request=rq.id, answer="accept")
    after = s.get("obligations", ob.id)
    assert (after.status, after.last) == ("active", 10)
    out = ok(s, "p01", "repay", 2, id=ob.id, amount=20)          # Friday's instalment, ahead: no engine error
    assert out.msg.endswith("nothing more is owed on it.") and s.get("obligations", ob.id).status == "finished"
    to_day(s, 30)
    assert [(t.t, t.cents) for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2"] == [(3, 2000)]
    # without the repayment, Friday's instalment is collected and then it is finished
    s = deals_town()
    set_cash(s, "h1", 100_000)
    ob = agreed(s)
    to_day(s, 3)
    ok(s, "p01", "propose_end", id=ob.id)
    ok(s, "p03", "answer", 1, request=s.all("requests")[-1].id, answer="accept")
    assert refused(s, "p01", "repay", id=ob.id, amount=20.01) == "Not done: that is more than the $20.00 still owed."
    to_day(s, 30)
    assert [(t.t, t.cents) for t in s.ledger.transfers if t.src == "h1" and t.dst == "h2"] == [(4, 2000)]
    assert s.get("obligations", ob.id).status == "finished"


# random sequences of agreements

def _schedule_total(ob) -> int:
    if ob.every == 0:
        return ob.cents
    n = (O.last_instalment(ob) - ob.first) // ob.every + 1
    return ob.cents * (n - 1) + O.last_cents(ob)


def _check_agreements(s, made: dict, ended: set) -> None:
    assert s.engine_errors == [] and s.ledger.total() == 0
    for ob_id, kind in made.items():
        ob = s.get("obligations", ob_id)
        dues = s.where("dues", obligation=ob_id)
        for d in dues:
            assert 0 <= d.paid <= d.cents, d
            if ob.last is not None:
                assert d.day <= ob.last or d.status == "void", (d, ob)
            if d.status == "void" and d.paid:
                kept = f"{O.money(d.paid)} already paid toward"
                assert any(kept in n.text and O.date_text(d.day) in n.text for n in s.where("notices", to="p01")), d
                assert any(kept in n.text and "is yours to keep" in n.text for n in s.where("notices", to="p04")), d
        received = sum(d.paid for d in dues if d.status != "void")
        if ob.status == "finished" and ob_id in ended and all(d.status == "void" for d in dues):
            assert ob.first > max((r.first for r in s.where("requests", kind="end", ob=ob_id)
                                   if r.status == "accepted"), default=ob.first)       # ended before it began
        elif kind == "until ended" and ob.last is None and ob.status not in O.SCHEDULED:
            raise AssertionError(("an agreement until ended was finished", ob))
        elif kind == "until ended" and ob.last is None:
            gone = s.day - 1                         # the checks fall before today's morning has run
            upto = [d for d in dues if d.day <= gone]
            still = sum(d.cents - d.paid for d in upto if d.status in ("due",) + O.OWED)
            days = [d for d in range(ob.first, gone + 1) if (d - ob.first) % 7 == 0]
            assert sum(d.paid for d in upto) + still == len(days) * ob.cents, (ob, dues)
            ahead = [d for d in dues if d.day > gone]
            assert all(d.day <= s.day + 7 for d in ahead) and len(ahead) <= 1, ahead
        else:
            owed = O.outstanding(s, ob) if ob.status in O.SCHEDULED else \
                sum(d.cents - d.paid for d in dues if d.status in ("due",) + O.OWED)
            assert received + owed == _schedule_total(ob), (ob, received, owed, dues)


@pytest.mark.parametrize("seed", range(30))
def test_random_sequences_of_agreements_keep_their_sums(seed):
    rng = random.Random(f"agreements:{seed}")
    s = deals_town()
    set_cash(s, "h1", 30_000)
    made: dict[str, str] = {}
    ended: set[str] = set()
    n = 0
    for step in range(100):
        roll = rng.random()
        n += 1
        if roll < 0.12 and len(made) < 4:
            kind = rng.choice(["weekly", "once", "until ended"])
            terms = {"per": "once"} if kind == "once" else {"weeks": 0} if kind == "until ended" else \
                {"weeks": rng.randint(1, 6)}
            out = do(s, "p01", "propose_agreement", n, **{**SHOPPING, **terms, "start": None,
                                                          "amount": rng.choice([10, 20, 35])})
            if out.ok and rng.random() < 0.9:
                if rng.random() < 0.5:
                    live(s, 1)
                if do(s, "p04", "answer", n + 1, request=s.all("requests")[-1].id, answer="accept").ok:
                    made[s.all("obligations")[-1].id] = kind
        elif roll < 0.35 and made:
            ob = rng.choice(sorted(made))
            do(s, "p01", "repay", n, id=ob, amount=rng.choice([1, 5, 10, 20, 35, 70, 500]))
        elif roll < 0.45 and made:
            ob = rng.choice(sorted(made))
            do(s, "p01", "pay", n, to="Megan Pryce", amount=rng.choice([5, 20, 40]), **{"for": ob})
        elif roll < 0.55 and made:
            ob = rng.choice(sorted(made))
            asker = rng.choice(["p01", "p04"])
            if do(s, asker, "propose_end", n, id=ob).ok:
                rq = s.all("requests")[-1]
                if rng.random() < 0.5:
                    live(s, rng.choice([1, 2]))
                if rng.random() < 0.5:
                    do(s, "p01", "repay", n + 1, id=ob, amount=5)        # the figure may change first
                if do(s, rq.to, "answer", n + 2, request=rq.id, answer="accept").ok:
                    ended.add(ob)
        elif roll < 0.62:
            set_cash(s, "h1", rng.choice([0, 0, 2_000, 50_000]))        # arrears now and then
        else:
            live(s, rng.choice([1, 1, 2, 3]))
        _check_agreements(s, made, ended)
    assert made


def test_an_agreement_until_ended_between_debtor_and_creditor_is_told_without_an_end():
    from townsim.town.facts import worries
    s = deals_town()
    tie(s, "p01", "p04", "creditor")
    tie(s, "p04", "p01", "debtor")
    agreed(s, weeks=0)
    assert "Paying back Megan: $20.00 a week until either side ends it." in worries(s, "p01", 20)
    assert "Whether Ruth keeps up the $20.00 a week owed to you." in worries(s, "p04", 20)
    for pid in ("p01", "p04"):
        who(s, pid)
