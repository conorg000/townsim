"""Jobs: offers accepted and declined, wages on Fridays, quitting and dismissal, extra hours,
pay by hand, benefit, stock orders, prices, an owner's own hours and moving money."""

import pytest

from townsim.town import acts, firms, jobs
from townsim.town import obligations as O
from townsim.town.acts import Act, apply_act
from townsim.town.clock import date_text
from townsim.town.entities import Event, FirmRow, Org, Work
from townsim.town.loop import close, routine
from townsim.town.views import view

from .helpers import add_person, add_shop, add_supplier, delivered, set_cash, town_with_shop
from .test_random_play import _check_run, _check_wage_chains

T = "routine:test"


def work_town():
    """The thin town (day 0 a Monday) with Marsh's Stores (org3, Ruth Ashby p01, $2,000) and
    its shop fm1 (60 units for each 38 hours); Craig Pryce (p03) works there 38 hours for $440
    a week, paid on Fridays; two suppliers, Vale Wholesale (sp1, $7.00, always complete) and
    Cheap Van Foods (sp2, $6.65); a regional office that pays benefit; and a second shop,
    Lowe's Grocer (org4, Ann Lowe p05, $1,000)."""
    s = town_with_shop()
    s.add("firms", FirmRow("fm1", "org3", "food", price=1000, quality=0.6, visibility=0.5, reputation=0.7,
                           units_per_staff=60, shelf_weeks=2), T)
    add_supplier(s, "org6", "Vale Wholesale", 700, 1.0, 0.85, 0.95)
    add_supplier(s, "org7", "Cheap Van Foods", 665, 0.7, 0.2, 0.65)
    s.open_account("org8", T, allow_credit=True)
    s.add("orgs", Org("org8", "Wenlow Regional Office", "region", outside=True), T)
    jobs.start(s, "p03", "org3", 44_000, 38, 4, T, since=-7)
    add_person(s, "p05", "Ann Lowe")
    add_shop(s, "org4", "Lowe's Grocer", "p05", 100_000)
    return s


def do(s, actor, op, n=0, **args):
    return apply_act(s, Act(actor, actor, op, args), n)


def refused(s, actor, op, **args):
    before = s.hash()
    out = do(s, actor, op, **args)
    assert not out.ok and s.hash() == before and s.engine_errors == [], out.msg
    return out.msg


def live(s, n):
    """`n` mornings and evenings with nobody acting."""
    for _ in range(n):
        routine(s)
        close(s)


def to_day(s, day):
    live(s, day - s.day)


def paid(s, src, dst):
    """(day, cents) of every transfer from `src` to `dst`."""
    return [(t.t, t.cents) for t in s.ledger.transfers if t.src == src and t.dst == dst]


def told(s, pid):
    return [n.text for n in s.where("notices", to=pid)]


def job_of(s, pid, org):
    return next(j for j in s.where("jobs", person=pid) if j.org == org)


def offer(s, actor, to, weekly, hours, n=0, **more):
    out = do(s, actor, "offer_job", n, to=to, weekly_pay=weekly, hours=hours, **more)
    assert out.ok, out.msg
    return s.all("requests")[-1]


def accept(s, pid, rq, n=1):
    return do(s, pid, "answer", n, request=rq.id, answer="accept")


# offers

def test_an_accepted_offer_makes_a_wage_due_on_fridays():
    s = work_town()
    set_cash(s, "org3", 500_000)
    rq = offer(s, "p01", "Megan Pryce", 440, 38)
    assert (rq.kind, rq.text, rq.cents, rq.first, rq.expires, rq.hours, rq.pay) == \
        ("job", "A job offer", 0, 7, 6, 38.0, 44_000)
    assert rq.summary == ("Marsh's Stores offers Megan Pryce a job: $440.00 a week for 38 hours, paid Fridays, "
                          "from Monday 9 March.")
    live(s, 1)
    out = accept(s, "p04", rq)
    assert out.ok and out.msg == "Accepted. Megan Pryce starts at Marsh's Stores on Monday 9 March."
    assert told(s, "p01")[-1] == ('Megan Pryce accepted your request rq1 ("A job offer"). '
                                  "Megan Pryce starts at Marsh's Stores on Monday 9 March.")
    job = job_of(s, "p04", "org3")
    ob = s.get("obligations", job.wage_ob)
    assert (job.weekly, job.hours, job.since, job.ended) == (44_000, 38.0, 7, -1)
    assert (ob.kind, ob.payer, ob.payee, ob.cents, ob.first, ob.every, ob.ends) == \
        ("wage", "org3", "h2", 44_000, 11, 7, "both")
    assert "employed" not in acts.positions(s, "p04")             # it starts on Monday
    to_day(s, 7)
    assert "employed" in acts.positions(s, "p04")
    live(s, 1)                                      # Monday's hours count the job
    assert s.get("people", "p04").hours_week == 70 - 38
    to_day(s, 19)
    wages = [(t.t, t.cents) for t in s.ledger.transfers if t.src == "org3" and t.cause.startswith("routine:collect:")
             and s.get("dues", t.cause.split(":")[2]).obligation == job.wage_ob]
    assert wages == [(11, 44_000), (18, 44_000)]
    assert s.ledger.total() == 0


def test_a_declined_offer_changes_no_job():
    s = work_town()
    rq = offer(s, "p01", "Megan Pryce", 440, 38, text="We are busy on Saturdays.")
    assert rq.text == "We are busy on Saturdays."
    out = do(s, "p04", "answer", request=rq.id, answer="decline")
    assert out.ok and out.msg == "Declined." and s.where("jobs", person="p04") == []
    assert told(s, "p01") == ['Megan Pryce declined your request rq1 ("We are busy on Saturdays.").']


def test_an_offer_lapses_on_the_sunday_before_the_day_it_names():
    s = work_town()
    live(s, 6)                                     # Sunday: the offer reaches them on Monday
    rq = offer(s, "p01", "Megan Pryce", 440, 38)
    assert (rq.first, rq.expires) == (14, 13) and rq.summary.endswith("from Monday 16 March.")
    to_day(s, 15)
    assert s.get("requests", rq.id).status == "lapsed"


def test_a_new_job_ends_the_old_one_and_the_summary_said_so():
    s = work_town()
    old = job_of(s, "p03", "org3")
    live(s, 2)                                     # Wednesday
    rq = offer(s, "p05", "Craig Pryce", 500, 38)
    assert rq.summary == ("Lowe's Grocer offers Craig Pryce a job: $500.00 a week for 38 hours, paid Fridays, "
                          "from Monday 9 March. Accepting ends your job at Marsh's Stores on Friday 6 March.")
    live(s, 1)
    assert accept(s, "p03", rq).ok
    assert s.get("jobs", old.id).ended == 4
    assert told(s, "p01") == ["Craig Pryce has left Marsh's Stores. Their last pay is on Friday 6 March."]
    to_day(s, 21)
    assert [d for d, _ in paid(s, "org3", "h2")] == [4]
    assert paid(s, "org4", "h2") == [(11, 50_000), (18, 50_000)]


def test_a_new_job_accepted_at_a_weekend_ends_the_old_one_on_the_friday_just_gone():
    s = work_town()
    live(s, 5)                                     # Saturday
    rq = offer(s, "p05", "Craig Pryce", 500, 38)
    assert rq.summary.endswith("from Monday 9 March. Accepting ends your job at Marsh's Stores on Friday 6 March.")
    live(s, 1)                                     # Sunday
    assert accept(s, "p03", rq).ok and s.engine_errors == []
    assert told(s, "p01") == ["Craig Pryce has left Marsh's Stores. Their last pay was on Friday 6 March."]
    to_day(s, 21)
    assert [d for d, _ in paid(s, "org3", "h2")] == [4]


def test_pay_below_the_minimum_is_refused_at_offer_and_at_acceptance(monkeypatch):
    s = work_town()
    assert refused(s, "p01", "offer_job", to="Megan Pryce", weekly_pay=400, hours=38) == \
        "Not done: $400.00 a week for 38 hours is less than $11.00 an hour."
    rq = offer(s, "p01", "Megan Pryce", 440, 38)
    monkeypatch.setattr(jobs, "MIN_HOURLY", 1300)
    assert refused(s, "p04", "answer", request=rq.id, answer="accept") == \
        "Not done: $440.00 a week for 38 hours is less than $13.00 an hour."
    monkeypatch.setattr(jobs, "MIN_HOURLY", 1100)
    s.set("orgs", "org3", T, owner="p05")
    assert refused(s, "p04", "answer", request=rq.id, answer="accept") == \
        "Not done: Ruth Ashby no longer owns Marsh's Stores."


def test_an_offer_is_refused_for_one_who_works_there_or_lacks_the_hours():
    s = work_town()
    assert refused(s, "p01", "offer_job", to="Craig Pryce", weekly_pay=500, hours=38) == \
        "Not done: Craig Pryce already works at Marsh's Stores."
    assert refused(s, "p01", "offer_job", to="Ruth Ashby", weekly_pay=500, hours=38) == "Not done: that is you."
    assert refused(s, "p01", "offer_job", to="Megan Pryce", weekly_pay=500, hours=61) == \
        "Not done: '61' is not a number of hours a week from 4 to 60."
    assert refused(s, "p03", "offer_job", to="Megan Pryce", weekly_pay=500, hours=38) == \
        "Not done: there is no operation called 'offer_job'."
    rq = offer(s, "p05", "Craig Pryce", 700, 60)
    s.add("work", Work("wk1", "p03", "h2", "the books", 12.0), T)        # the job at Marsh's would end
    assert refused(s, "p03", "answer", request=rq.id, answer="accept") == \
        "Not done: your week has 58 hours once your other work is done, and this job is 60."


# shifts

def test_eight_extra_hours_at_440_for_38_pay_9263_once():
    s = work_town()
    out = do(s, "p01", "offer_shifts", to="Craig Pryce", hours=8)
    rq = s.all("requests")[-1]
    assert out.ok and (rq.kind, rq.hours, rq.pay, rq.first, rq.expires) == ("shifts", 8.0, 9_263, 4, 3)
    assert rq.summary == "Marsh's Stores offers Craig Pryce 8 extra hours, paid $92.63 on Friday 6 March."
    live(s, 1)
    out = accept(s, "p03", rq)
    assert out.msg == ("Accepted. Craig Pryce works 8 extra hours at Marsh's Stores this week, "
                       "paid $92.63 on Friday 6 March.")
    assert s.get("people", "p03").hours_left == 70 - 38 - 8 - 0.1
    assert s.get("firms", "fm1").extra_hours == 8 and firms.capacity(s, "fm1") == 60 * 46 // 38
    to_day(s, 20)
    assert sorted(c for d, c in paid(s, "org3", "h2") if d == 4) == [9_263, 44_000]
    assert [c for d, c in paid(s, "org3", "h2") if d > 4] == [44_000, 44_000]
    assert s.get("firms", "fm1").extra_hours == 0


def test_extra_hours_need_the_hours_and_a_job_there():
    s = work_town()
    do(s, "p01", "offer_shifts", to="Craig Pryce", hours=8)
    rq = s.all("requests")[-1]
    s.set("people", "p03", T, hours_left=5.0)
    assert refused(s, "p03", "answer", request=rq.id, answer="accept") == \
        "Not done: you have 4.9 hours left this week, and this is 8."
    assert refused(s, "p01", "offer_shifts", to="Megan Pryce", hours=8) == \
        "Not done: Megan Pryce does not work at Marsh's Stores."
    assert refused(s, "p01", "offer_shifts", to="Craig Pryce", hours=21) == \
        "Not done: '21' is not a number of hours from 1 to 20."


def test_extra_hours_offered_or_accepted_from_a_friday_on_are_paid_the_friday_after():
    s = work_town()
    live(s, 4)                                      # Friday, after the morning's wages
    do(s, "p01", "offer_shifts", to="Craig Pryce", hours=4)
    rq = s.all("requests")[-1]
    assert (rq.first, rq.expires) == (11, 10) and rq.summary.endswith("paid $46.32 on Friday 13 March.")
    out = accept(s, "p03", rq)                     # accepted the same Friday
    assert out.msg.endswith("paid $46.32 on Friday 13 March.")
    to_day(s, 12)
    assert [(d, c) for d, c in paid(s, "org3", "h2") if c == 4_632] == [(11, 4_632)]
    live(s, 1)                                      # Sunday: an offer then is paid on the Friday after
    do(s, "p01", "offer_shifts", 1, to="Craig Pryce", hours=4)
    assert s.all("requests")[-1].first == 18 and s.all("requests")[-1].summary.endswith("on Friday 20 March.")


# leaving

def test_quitting_on_tuesday_is_paid_that_friday_and_never_again():
    s = work_town()
    live(s, 1)
    out = do(s, "p03", "quit")
    assert out.msg == "You have left Marsh's Stores. Your last pay is on Friday 6 March."
    assert told(s, "p01") == ["Craig Pryce has left Marsh's Stores. Their last pay is on Friday 6 March."]
    assert "Marsh's Stores" in view(s, "p03")[0][0]["content"]
    to_day(s, 4)
    assert "employed" in acts.positions(s, "p03")
    to_day(s, 5)
    assert "employed" not in acts.positions(s, "p03")
    assert "Marsh's Stores" not in view(s, "p03")[0][0]["content"]
    to_day(s, 28)
    assert paid(s, "org3", "h2") == [(4, 44_000)]


def test_quitting_at_a_weekend_takes_no_further_wage():
    s = work_town()
    live(s, 5)
    out = do(s, "p03", "quit", **{"from": "Marsh's Stores"})
    assert out.msg == "You have left Marsh's Stores. Your last pay was on Friday 6 March."
    assert "employed" not in acts.positions(s, "p03")
    to_day(s, 28)
    assert paid(s, "org3", "h2") == [(4, 44_000)]


def test_an_owner_cannot_quit_and_a_job_ending_cannot_be_left_twice():
    s = work_town()
    jobs.start(s, "p01", "org3", 30_000, 38, 4, T, since=-7)
    assert refused(s, "p01", "quit") == "Not done: you own Marsh's Stores."
    assert do(s, "p03", "quit").ok
    assert refused(s, "p03", "quit") == "Not done: your job at Marsh's Stores ends on Friday 6 March."
    assert refused(s, "p03", "quit", **{"from": "Lowe's Grocer"}) == "Not done: you have no job at 'Lowe's Grocer'."
    assert refused(s, "p04", "quit") == "Not done: there is no operation called 'quit'."


def test_a_dismissal_ends_after_this_fridays_pay_and_the_view_shows_it():
    s = work_town()
    live(s, 1)
    out = do(s, "p01", "dismiss", who="Craig Pryce", text="Trade is slow.")
    assert out.msg == "Craig Pryce has left Marsh's Stores. Their last pay is on Friday 6 March."
    assert told(s, "p03") == ["Marsh's Stores has ended your job. Your last pay is on Friday 6 March. "
                              'Ruth Ashby wrote: "Trade is slow."']
    assert refused(s, "p01", "dismiss", who="Craig Pryce") == \
        "Not done: Craig Pryce's job at Marsh's Stores ends on Friday 6 March."
    to_day(s, 5)
    assert "employed" not in acts.positions(s, "p03")
    assert "Marsh's Stores" not in view(s, "p03")[0][0]["content"]
    to_day(s, 21)
    assert paid(s, "org3", "h2") == [(4, 44_000)]
    assert refused(s, "p01", "dismiss", who="Megan Pryce") == "Not done: Megan Pryce does not work at Marsh's Stores."


def test_a_job_lost_at_a_weekend_was_paid_on_the_friday_just_gone():
    s = work_town()
    s.set("orgs", "org3", T, outside=True)          # an outside employer, as the event wants
    s.add("events", Event("ev1", 5, "job_lost", "p03", 0, 0), T)
    to_day(s, 6)
    assert told(s, "p03") == ["Marsh's Stores has ended your job. Your last pay was on Friday 6 March."]
    to_day(s, 21)
    assert paid(s, "org3", "h2") == [(4, 44_000)]


# pay

def test_set_staff_pay_takes_effect_after_this_friday():
    s = work_town()
    live(s, 1)
    out = do(s, "p01", "set_staff_pay", who="Craig Pryce", weekly_pay=500)
    assert out.msg == "Marsh's Stores pays Craig Pryce $500.00 a week from Friday 13 March."
    assert told(s, "p03") == ["Marsh's Stores pays you $500.00 a week from Friday 13 March."]
    job = job_of(s, "p03", "org3")
    ob = s.get("obligations", job.wage_ob)                # the one wage, re-priced from that Friday
    assert job.weekly == 50_000 and (ob.cents, ob.next_cents, ob.next_from) == (44_000, 50_000, 11)
    to_day(s, 19)
    assert paid(s, "org3", "h2") == [(4, 44_000), (11, 50_000), (18, 50_000)]
    assert refused(s, "p01", "set_staff_pay", who="Craig Pryce", weekly_pay=300) == \
        "Not done: $300.00 a week for 38 hours is less than $11.00 an hour."
    assert refused(s, "p01", "set_staff_pay", who="Craig Pryce", weekly_pay=500) == \
        "Not done: Craig Pryce is paid $500.00 a week already."


def test_set_staff_pay_at_a_weekend_applies_from_the_friday_after_next():
    s = work_town()
    live(s, 5)
    out = do(s, "p01", "set_staff_pay", who="Craig Pryce", weekly_pay=500)
    assert out.msg.endswith("from Friday 20 March.")
    to_day(s, 19)
    assert paid(s, "org3", "h2") == [(4, 44_000), (11, 44_000), (18, 50_000)]


def test_an_owners_own_draw_may_be_set_to_nothing():
    s = work_town()
    set_cash(s, "org3", 1_000_000)
    jid = jobs.start(s, "p01", "org3", 30_000, 38, 4, T, since=-7)
    out = do(s, "p01", "set_staff_pay", who="Ruth Ashby", weekly_pay=0)
    assert out.msg == "Marsh's Stores pays you $0.00 a week from Friday 13 March."
    wage = s.get("jobs", jid).wage_ob                      # the one wage, re-priced to nothing
    ob = s.get("obligations", wage)
    assert (ob.status, ob.last, ob.next_cents, ob.next_from) == ("active", None, 0, 11) and s.get("jobs", jid).weekly == 0
    to_day(s, 19)
    assert paid(s, "org3", "h1") == [(4, 30_000)]
    assert [d.day for d in s.where("dues", obligation=wage)] == [4]          # an instalment of nothing opens no due
    assert not [u for u in O.upcoming(s, "h1", 21) if u.obligation == wage]
    out = do(s, "p01", "set_staff_pay", who="Ruth Ashby", weekly_pay=200)    # on a Saturday
    assert out.msg == "Marsh's Stores pays you $200.00 a week from Friday 3 April." and out.ok
    assert s.get("jobs", jid).wage_ob == wage
    to_day(s, 40)
    assert paid(s, "org3", "h1") == [(4, 30_000), (32, 20_000), (39, 20_000)]
    assert refused(s, "p01", "set_staff_pay", who="Craig Pryce", weekly_pay=0).startswith("Not done: '0'")


def test_wages_from_an_empty_till_go_to_arrears_and_the_owner_is_told():
    s = work_town()
    set_cash(s, "org3", 0)
    to_day(s, 5)
    [due] = [d for d in s.all("dues") if d.obligation == job_of(s, "p03", "org3").wage_ob]
    assert due.status == "arrears" and paid(s, "org3", "h2") == []
    assert told(s, "p01") == ["A payment of $440.00 due Friday 6 March (wages from Marsh's Stores) could not be taken. "
                              "The Marsh's Stores account held $0.00."]


def test_wages_paid_by_hand_from_the_household_count_toward_the_wage():
    s = work_town()
    set_cash(s, "h1", 100_000)
    live(s, 3)                                      # Thursday
    out = do(s, "p01", "pay", to="Craig Pryce", amount=440)
    assert out.msg == "Paid $440.00 to Craig Pryce. It was counted toward wages from Marsh's Stores due Friday 6 March."
    to_day(s, 12)
    assert paid(s, "org3", "h2") == [(11, 44_000)] and paid(s, "h1", "h2") == [(3, 44_000)]
    assert s.ledger.total() == 0
    _check_run(s)


def test_part_of_a_wage_paid_by_hand_leaves_the_rest_to_friday_and_arrears_come_first():
    s = work_town()
    set_cash(s, "org3", 0)
    to_day(s, 6)                                    # Friday's wage is in arrears
    out = do(s, "p01", "pay", to="Craig Pryce", amount=500)
    assert out.msg == ("Paid $500.00 to Craig Pryce. It was counted toward wages from Marsh's Stores due "
                       "Friday 6 March; wages from Marsh's Stores due Friday 13 March.")
    set_cash(s, "org3", 100_000)
    to_day(s, 12)
    assert paid(s, "org3", "h2") == [(11, 44_000 - 6_000)]
    dues = [d for d in s.all("dues") if d.obligation == job_of(s, "p03", "org3").wage_ob]
    assert [(d.day, d.paid, d.status) for d in dues] == [(4, 44_000, "paid"), (11, 44_000, "paid")]
    _check_run(s)


def test_a_payment_where_something_is_owed_between_the_two_accounts_counts_toward_that():
    s = work_town()
    do(s, "p01", "standing_payment", to="Craig Pryce", amount=20, day="Friday")
    out = do(s, "p01", "pay", 1, to="Craig Pryce", amount=20)
    assert out.msg == ("Paid $20.00 to Craig Pryce. "
                       "It was counted toward standing payment to Craig Pryce due Friday 6 March.")
    to_day(s, 5)
    assert paid(s, "org3", "h2") == [(4, 44_000)]


def test_count_toward_counts_arrears_then_one_instalment_and_moves_nothing():
    s = work_town()
    ob = job_of(s, "p03", "org3").wage_ob
    moved = len(s.ledger.transfers)
    matches = O.count_toward(s, ob, 100_000, T)
    assert [m.cents for m in matches] == [44_000] and len(s.ledger.transfers) == moved
    with pytest.raises(ValueError):
        O.count_toward(s, "ob999", 5, T)
    with pytest.raises(ValueError):
        O.count_toward(s, ob, 0, T)


# benefit

def test_benefit_starts_the_week_after_and_ends_with_a_job():
    s = work_town()
    live(s, 1)
    do(s, "p03", "quit")
    to_day(s, 8)
    [ob] = [o for o in s.all("obligations") if o.memo == "jobseeker's benefit"]
    assert (ob.payer, ob.payee, ob.cents, ob.first, ob.every, ob.ends) == ("org8", "h2", 14_000, 11, 7, "both")
    assert s.get("people", "p03").benefit == ob.id
    assert told(s, "p03")[-1] == "Jobseeker's benefit of $140.00 a week starts on Friday 13 March."
    live(s, 7)                                      # Tuesday 17 March
    rq = offer(s, "p05", "Craig Pryce", 500, 38)
    live(s, 1)
    assert accept(s, "p03", rq).ok
    assert told(s, "p03")[-1] == "Jobseeker's benefit is not paid after Friday 20 March."
    to_day(s, 36)
    assert paid(s, "org8", "h2") == [(11, 14_000), (18, 14_000)]
    assert len([o for o in s.all("obligations") if o.memo == "jobseeker's benefit"]) == 1


def test_benefit_is_for_those_under_pension_age_whose_job_ended():
    s = work_town()
    s.set("people", "p03", T, age=66)
    live(s, 1)
    do(s, "p03", "quit")
    to_day(s, 15)
    assert not [o for o in s.all("obligations") if o.memo == "jobseeker's benefit"]   # nor for Megan, never employed


# shops

def test_an_order_placed_by_saturday_arrives_monday_and_replaces_an_earlier_one():
    s = work_town()
    live(s, 1)
    out = do(s, "p01", "order_stock", supplier="Cheap Van Foods", units=100)
    assert out.msg == "Ordered 100 units from Cheap Van Foods at $6.65 a unit, for Monday 9 March."
    live(s, 4)                                      # Saturday
    out = do(s, "p01", "order_stock", supplier="vale wholesale", units="50")
    assert out.msg == "Ordered 50 units from Vale Wholesale at $7.00 a unit, for Monday 9 March."
    to_day(s, 8)
    assert delivered(s, "fm1") == [50] and paid(s, "org3", "org6") == [(7, 35_000)]
    out = do(s, "p01", "order_stock", supplier="Vale Wholesale", units=0)
    assert out.msg == "Nothing is ordered for Monday 16 March, and nothing is repeated after it."
    to_day(s, 22)
    assert delivered(s, "fm1") == [50]
    assert refused(s, "p01", "order_stock", supplier="Edith Marsh", units=5) == \
        "Not done: 'Edith Marsh' is not a supplier."
    assert refused(s, "p01", "order_stock", supplier="Vale Wholesale", units=5001) == \
        "Not done: '5001' is not a whole number of units from 0 to 5,000."


def test_a_price_takes_effect_at_once():
    s = work_town()
    assert do(s, "p01", "set_price", price="$11.50").msg == "The price at Marsh's Stores is now $11.50 a basket."
    assert s.get("firms", "fm1").price == 1150
    assert refused(s, "p01", "set_price", price=0.99) == "Not done: '0.99' is not a price from $1.00 to $100.00."
    assert refused(s, "p05", "set_price", price=5) == "Not done: Lowe's Grocer is not a shop."


def test_extra_hours_raise_this_weeks_capacity_only():
    s = work_town()
    assert firms.capacity(s, "fm1") == 60
    out = do(s, "p01", "work_extra_hours", hours=19)
    assert out.msg == "You work 19 extra hours at Marsh's Stores this week."
    assert firms.capacity(s, "fm1") == 90 and s.get("people", "p01").hours_left == 51
    s.set("people", "p01", T, hours_left=10.0)
    assert refused(s, "p01", "work_extra_hours", hours=11) == "Not done: you have no time left this week for that."
    to_day(s, 7)
    assert firms.capacity(s, "fm1") == 60


def test_moving_money_between_ones_own_accounts():
    s = work_town()
    out = do(s, "p01", "move_money", **{"from": "Marsh's Stores", "to": "household", "amount": 500})
    assert out.msg == "Moved $500.00 from Marsh's Stores to your household."
    t = s.ledger.transfers[-1]
    assert (t.src, t.dst, t.cents, t.memo, t.cause) == ("org3", "h1", 50_000, "moved between your own accounts",
                                                         "op:p01:move_money:d0.0")
    out = do(s, "p01", "move_money", 1, **{"from": "Ashby", "to": "marsh's stores", "amount": "$1,000"})
    assert out.msg == "Moved $1,000.00 from your household to Marsh's Stores." and s.balance("org3") == 250_000
    assert refused(s, "p01", "move_money", **{"from": "household", "to": "Household", "amount": 5}) == \
        "Not done: that is the same account twice."
    assert refused(s, "p01", "move_money", **{"from": "household", "to": "Marsh's Stores", "amount": 5000}) == \
        "Not done: the account holds $100.00 and this needs $5,000.00."
    assert refused(s, "p01", "move_money", **{"from": "household", "to": "Lowe's Grocer", "amount": 5}) == \
        "Not done: 'Lowe's Grocer' is not an account of yours."
    assert s.ledger.total() == 0


# what people are told

SUMMARY_WORDS, SUMMARY_CHARS = 60, 480


def test_summaries_are_bounded_by_construction():
    s = work_town()
    add_shop(s, "org9", "Third Shop", "p02", 100_000)
    names = {"org3": "Marsh " * 16 + "Mark", "org4": "Lowes " * 16 + "Lowe", "org9": "Third " * 16 + "Thrd"}
    for org, name in names.items():
        s.set("orgs", org, T, name=name[:100])
    s.set("people", "p04", T, name="Megan " + "Q" * 40)
    s.set("orgs", "org9", T, owner="p04")
    jobs.start(s, "p03", "org9", 50_000, 10, 4, T, since=-7)
    rq = offer(s, "p05", "Craig Pryce", 500, 20)
    assert len(rq.summary.split()) <= 60 and len(rq.summary) <= 480
    for name in names.values():
        assert name[:100] not in rq.summary and name[:40].rstrip() + "\u2026" in rq.summary
    from townsim.town.ops_work import _bounded
    assert _bounded("w " * 100).endswith("\u2026") and len(_bounded("w " * 100).split()) <= 60
    assert len(_bounded(("x" * 39 + " ") * 30)) <= 480


def test_summaries_are_short_even_with_the_longest_names():
    s = work_town()
    long = "Abcdefghij " * 9 + "Abcdefghi"                   # a hundred characters
    s.set("orgs", "org4", T, name=long.replace("Abcdefghij", "Orgnamexyz")[:100])
    s.set("orgs", "org3", T, name=long[:100])
    s.set("people", "p03", T, name=long[:100])
    rq = offer(s, "p05", long[:100], 99_999, 60, text="word " * 80)
    rq2 = (do(s, "p01", "offer_shifts", 1, to=long[:100], hours=20), s.all("requests")[-1])[1]
    for summary in (rq.summary, rq2.summary):
        assert len(summary.split()) <= SUMMARY_WORDS and len(summary) <= SUMMARY_CHARS, summary
    assert "Accepting ends your job at" in rq.summary


# what review found: no wage paid twice or after a job ends

def _hamlet():
    import pickle
    global _HAMLET
    try:
        return pickle.loads(_HAMLET)
    except NameError:
        from townsim.town import gen
        _HAMLET = pickle.dumps(gen.generate(1, ["a", "b", "c", "d"]))
        return pickle.loads(_HAMLET)


def _gail(s, since):
    """Wages from Lumb's Grocer to Gail Whiteley's household (h5) from `since`."""
    return [(t.t, t.cents) for t in s.ledger.transfers if t.src == "org10" and t.dst == "h5" and t.t >= since]


def _collected_after_last(s):
    """Collections of a due after its obligation's last day: none may exist."""
    bad = []
    for t in s.ledger.transfers:
        if t.cause.startswith("routine:collect:"):
            due = s.get("dues", t.cause.split(":")[2])
            ob = s.get("obligations", due.obligation)
            if ob.last is not None and due.day > ob.last:
                bad.append((due.id, due.day, ob.last))
    return bad


@pytest.mark.parametrize("act", ["set_staff_pay", "dismiss"])
def test_a_second_payment_by_hand_then_a_change_never_pays_a_friday_twice(act):
    s = _hamlet(); live(s, 10); routine(s)          # Thursday 12 March; Gail Whiteley's wage is $434
    do(s, "p16", "pay", to="Gail Whiteley", amount=434)
    out = do(s, "p16", "pay", 1, to="Gail Whiteley", amount=50)
    assert out.msg == "Paid $50.00 to Gail Whiteley."                 # Friday 20 March is more than a week ahead
    args = dict(who="Gail Whiteley", weekly_pay=500) if act == "set_staff_pay" else dict(who="Gail Whiteley")
    assert do(s, "p16", act, 2, **args).ok
    close(s); live(s, 16)
    assert _gail(s, 10) == ([(18, 50_000), (25, 50_000)] if act == "set_staff_pay" else [])
    assert _collected_after_last(s) == [] and s.ledger.total() == 0
    assert [d.cents for d in s.where("dues", obligation="ob3") if d.day > 11] == \
        ([50_000, 50_000] if act == "set_staff_pay" else [])
    _check_run(s)


def test_a_payment_at_a_weekend_then_a_dismissal_takes_no_further_wage():
    s = _hamlet(); live(s, 12); routine(s)          # Saturday
    assert do(s, "p16", "pay", to="Gail Whiteley", amount=100).msg.endswith("due Friday 20 March.")
    assert do(s, "p16", "dismiss", 1, who="Gail Whiteley").msg.endswith("was on Friday 13 March.")
    close(s); live(s, 14)
    assert _gail(s, 12) == [] and _collected_after_last(s) == []
    _check_run(s)


def test_a_payment_reaching_into_next_week_then_quitting_takes_no_further_wage():
    s = _hamlet(); live(s, 9); routine(s)
    do(s, "p16", "pay", to="Gail Whiteley", amount=534)
    assert do(s, "p9", "quit", 1).msg == "You have left Lumb's Grocer. Your last pay is on Friday 13 March."
    close(s); live(s, 16)
    assert _gail(s, 9) == [] and _collected_after_last(s) == []
    _check_run(s)


def test_nothing_is_counted_toward_an_instalment_after_the_last():
    s = work_town()
    ob = job_of(s, "p03", "org3").wage_ob
    O.end(s, ob, 4, T)
    assert [m.cents for m in O.count_toward(s, ob, 100_000, T)] == [44_000]
    assert O.count_toward(s, ob, 100, T) == []                        # Friday 6 March is paid; nothing after
    assert [d.day for d in s.where("dues", obligation=ob)] == [4]


@pytest.mark.parametrize("start", [7, 9, 11, 12])
def test_a_draw_set_to_nothing_and_back_never_pays_a_friday_twice(start):
    s = _hamlet(); live(s, start)
    assert do(s, "p3", "set_staff_pay", who="Rosa Sowerby", weekly_pay=0).ok
    out = do(s, "p3", "set_staff_pay", 1, who="Rosa Sowerby", weekly_pay=300)
    live(s, 21)
    days = [t.t for t in s.ledger.transfers if t.src == "org11" and t.dst == "h2" and t.t >= start]
    assert len(days) == len(set(days)), days
    first_new = min(t.t for t in s.ledger.transfers if t.src == "org11" and t.dst == "h2" and t.cents == 30_000)
    assert out.msg.startswith(f"Hartcliffe Food Hall pays you $300.00 a week from {date_text(first_new)}. "
                              f"This replaces the change to $0.00 that was to start on {date_text(first_new)}.")
    assert {o.id for o in s.all("obligations") if o.payer == "org11" and o.payee == "h2" and o.kind == "wage"} == \
        {s.get("jobs", next(j.id for j in s.where("jobs", person="p3"))).wage_ob}


def test_pay_changed_twice_in_a_week_pays_each_friday_once():
    s = _hamlet(); live(s, 9)
    do(s, "p3", "set_staff_pay", who="Thea Ormerod", weekly_pay=450)
    do(s, "p3", "set_staff_pay", 1, who="Thea Ormerod", weekly_pay=460)
    do(s, "p3", "set_staff_pay", 2, who="Thea Ormerod", weekly_pay=470)
    live(s, 21)
    wages = [(t.t, t.cents) for t in s.ledger.transfers if t.src == "org11" and t.dst == "h9" and t.t >= 9]
    assert wages == [(11, 39_295), (18, 47_000), (25, 47_000)]


def test_a_second_offer_accepted_ends_the_first_before_it_starts():
    s = _hamlet(); live(s, 8)
    do(s, "p3", "offer_job", to="Wendy Lumb", weekly_pay=440, hours=38)
    r1 = s.all("requests")[-1]
    assert accept(s, "p18", r1).ok
    do(s, "p14", "offer_job", 2, to="Wendy Lumb", weekly_pay=500, hours=38)
    r2 = s.all("requests")[-1]
    assert r2.summary.endswith("Accepting means you do not start at Hartcliffe Food Hall.")
    assert accept(s, "p18", r2, 3).ok
    assert told(s, "p3")[-1] == "Wendy Lumb does not start at Hartcliffe Food Hall on Monday 16 March."
    live(s, 13)
    rows = [(j.org, j.since, j.ended) for j in s.where("jobs", person="p18")]
    assert rows == [("org11", 14, 13), ("org12", 14, -1)]
    assert s.get("people", "p18").hours_week == 70 - 38
    assert [t.cents for t in s.ledger.transfers if t.dst == "h9" and t.src == "org11" and t.cents == 44_000] == []
    assert [t.t for t in s.ledger.transfers if t.dst == "h9" and t.src == "org12"] == [18]


def test_a_job_lost_after_leaving_it_is_passed_over():
    s = _hamlet(); live(s, 8); routine(s)
    assert do(s, "p4", "quit").ok
    close(s)
    s.add("events", Event("ev999", s.day, "job_lost", "p4", 0, 0), T)
    routine(s); close(s)
    assert s.engine_errors == []


def test_a_wage_paid_by_hand_says_so_in_the_payment():
    s = work_town()
    set_cash(s, "h1", 100_000)
    do(s, "p01", "pay", to="Craig Pryce", amount=440)
    assert s.ledger.transfers[-1].memo == \
        "payment to Craig Pryce (counted toward wages from Marsh's Stores due Friday 6 March)"


def test_end_finishes_an_obligation_with_nothing_left_to_pay():
    s = work_town()
    ob = job_of(s, "p03", "org3").wage_ob
    to_day(s, 4); routine(s)                        # Friday's wage is taken
    O.end(s, ob, 4, T)
    assert s.get("obligations", ob).status == "finished"


# the view follows the store

def _who(s, pid):
    from townsim.town import facts
    return facts.who(s, pid)


def test_someone_who_takes_a_job_is_no_longer_out_of_work_in_any_view():
    s = _hamlet()
    assert "You are out of work." in _who(s, "p18") and "Finding work" in _who(s, "p18")
    live(s, 8)
    do(s, "p16", "offer_job", to="Wendy Lumb", weekly_pay=440, hours=38)
    assert accept(s, "p18", s.all("requests")[-1]).ok
    live(s, 7)                                      # Monday 16 March: the job has begun
    who = _who(s, "p18")
    assert "out of work" not in who and "Finding work" not in who and "benefit" not in who
    assert "You work at Lumb's Grocer: 38 hours a week, for $440.00 a week, paid on Fridays." in who
    assert "Wendy Lumb, your child, works for you, works at Lumb's Grocer." in _who(s, "p16")


def test_someone_who_loses_a_job_is_not_told_they_keep_it():
    s = _hamlet()
    [ev] = [e for e in s.all("events") if e.kind == "job_lost"]
    assert "Keeping your job at Brackenfield Foods." in s.get("people", ev.who).worries
    live(s, 22)                                     # past the last pay, and benefit has begun
    who = _who(s, ev.who)
    assert "Brackenfield" not in who
    worries = who.split("What worries you: ")[1].split("\n")[0] if "What worries you: " in who else ""
    assert "Keeping your job" not in worries


def test_a_debt_that_is_settled_is_no_longer_a_worry():
    s = _hamlet()
    assert "Whether Rosa keeps up the $15.00 a week owed to you." in _who(s, "p16")
    [ob] = [o for o in s.all("obligations") if o.kind == "agreement" and o.payee == "h9"]
    s.set("obligations", ob.id, T, status="finished")
    assert "Rosa keeps up" not in _who(s, "p16") and "paying back Martin Lumb" not in _who(s, "p16")


def test_a_household_that_falls_behind_is_shown_so():
    s = _hamlet()
    p = next(p for p in s.all("people") if "More goes out" not in _who(s, p.id) and p.worries
             and s.get("households", p.household).members[0] == p.id)
    O.create(s, kind="bill", payer=p.household, payee="org4", cents=500_000, first=1, every=7, memo="energy",
             ends="service", made_by="routine", cause=T)
    assert "What worries you: More goes out each week" in _who(s, p.id)


# what the second review found: nothing prepaid more than a week ahead

def _wages_in(s, since):
    """(day, payer, cents) of everything paid to Gail Whiteley's household from `since`."""
    return [(t.t, t.src, t.cents) for t in s.ledger.transfers if t.dst == "h5" and t.src in ("org10", "h9")
            and t.t >= since]


def test_three_payments_by_hand_on_a_thursday_then_a_pay_change_pay_each_friday_once():
    s = _hamlet(); live(s, 10); routine(s)          # Thursday 12 March
    outs = [do(s, "p16", "pay", i, to="Gail Whiteley", amount=434).msg for i in range(3)]
    assert outs[0].endswith("due Friday 13 March.") and outs[1:] == ["Paid $434.00 to Gail Whiteley."] * 2
    assert do(s, "p16", "set_staff_pay", 3, who="Gail Whiteley", weekly_pay=500).ok
    close(s); live(s, 20)
    assert [(d, c) for d, src, c in _wages_in(s, 11) if src == "org10"] == [(18, 50_000), (25, 50_000)]
    _check_wage_chains(s); _check_run(s)


def test_two_payments_by_hand_on_a_saturday_then_a_pay_change_pay_each_friday_once():
    s = _hamlet(); live(s, 12); routine(s)          # Saturday 14 March
    outs = [do(s, "p16", "pay", i, to="Gail Whiteley", amount=434).msg for i in range(2)]
    assert outs[0].endswith("due Friday 20 March.") and outs[1] == "Paid $434.00 to Gail Whiteley."
    out = do(s, "p16", "set_staff_pay", 2, who="Gail Whiteley", weekly_pay=500)
    assert out.msg == "Lumb's Grocer pays Gail Whiteley $500.00 a week from Friday 27 March."
    close(s); live(s, 20)
    assert [(d, c) for d, src, c in _wages_in(s, 13) if src == "org10"] == [(25, 50_000), (32, 50_000)]
    _check_wage_chains(s); _check_run(s)


@pytest.mark.parametrize("then", ["dismiss", "set_staff_pay"])
def test_two_requests_for_the_wage_accepted_then_a_change_pay_each_friday_once(then):
    s = _hamlet(); live(s, 10); routine(s)
    for i in range(2):
        assert do(s, "p9", "request", 2 * i, to="Martin Lumb", text="my wage", amount=434, **{"for": "ob3"}).ok
        assert do(s, "p16", "answer", 2 * i + 1, request=s.all("requests")[-1].id, answer="accept").ok
    args = dict(weekly_pay=500) if then == "set_staff_pay" else {}
    assert do(s, "p16", then, 9, who="Gail Whiteley", **args).ok
    close(s); live(s, 20)
    assert {d.cents for d in s.where("dues", obligation="ob3") if d.day > 11} <= {50_000}
    assert (then == "dismiss") == (s.get("obligations", "ob3").last == 11)
    _check_wage_chains(s); _check_run(s)


def test_a_dismissal_after_a_week_paid_ahead_tells_both_and_benefit_waits_for_it():
    s = _hamlet(); live(s, 12); routine(s)          # Saturday 14 March: Friday 20 March paid ahead
    do(s, "p16", "pay", to="Gail Whiteley", amount=434)
    assert do(s, "p16", "dismiss", 1, who="Gail Whiteley").msg.endswith("was on Friday 13 March.")
    kept = "$434.00 already paid toward wages from Lumb's Grocer due Friday 20 March"
    assert told(s, "p16")[-1] == f"{kept} stays with {s.get('households', 'h5').name}."
    assert told(s, "p9")[-2:] == ["Lumb's Grocer has ended your job. Your last pay was on Friday 13 March.",
                                  f"{kept} is yours to keep."]
    [due] = [d for d in s.where("dues", obligation="ob3") if d.day == 18]
    assert (due.status, due.paid) == ("void", 43_400)
    close(s); live(s, 20)
    [benefit] = [o for o in s.all("obligations") if o.memo == "jobseeker's benefit" and o.payee == "h5"]
    assert benefit.first == 25                                         # not Friday 20 March, already paid
    assert [d for d, src, c in _wages_in(s, 13) if src == "org10"] == []
    _check_run(s)


def test_pay_for_a_wage_of_ones_own_shop_from_the_household_counts_toward_it():
    s = _hamlet(); live(s, 10); routine(s)
    out = do(s, "p16", "pay", to="Gail Whiteley", amount=434, **{"for": "ob3"})
    assert out.msg == "Paid $434.00 to Gail Whiteley. It was counted toward wages from Lumb's Grocer due Friday 13 March."
    assert s.ledger.transfers[-1].src == "h9"
    assert do(s, "p9", "pay", 1, to="Dennis Brindle", amount=5, **{"for": "ob3"}).msg.startswith(
        "Not done: you have nothing with the id 'ob3'")


def test_curly_quotation_marks_in_a_note_cannot_close_its_quotation():
    from townsim.town.statement import quoted
    assert quoted("a \u201cb\u201d \u201ec\u201f \u00abd\u00bb \"e\"") == "\"a 'b' 'c' 'd' 'e'\""


# what the third review found: a job has one wage, re-priced

@pytest.mark.parametrize("day,end", [(11, "dismiss"), (12, "dismiss"), (12, "quit"), (13, "quit")])
def test_a_pay_change_from_a_friday_on_then_leaving_takes_no_wage_after_the_last_pay(day, end):
    s = _hamlet(); live(s, day); routine(s)
    assert do(s, "p16", "set_staff_pay", who="Gail Whiteley", weekly_pay=500).ok
    out = do(s, "p16", "dismiss", 1, who="Gail Whiteley") if end == "dismiss" else do(s, "p9", "quit", 1)
    last = s.get("jobs", next(j.id for j in s.where("jobs", person="p9") if j.org == "org10")).ended
    assert out.ok and last in (11, 18)
    close(s); live(s, 30 - day)
    wages = [o for o in s.all("obligations") if o.payer == "org10" and o.payee == "h5" and o.kind == "wage"]
    assert [o.id for o in wages] == ["ob3"]                            # one wage, never a second
    assert [d for d, src, c in _wages_in(s, day) if src == "org10" and d > last] == []
    assert s.engine_errors == []
    _check_run(s)


def test_benefit_after_a_pay_change_and_a_dismissal_at_a_weekend_starts_after_the_last_wage():
    s = _hamlet(); live(s, 12); routine(s)          # Saturday 14 March
    do(s, "p16", "set_staff_pay", who="Gail Whiteley", weekly_pay=500)
    do(s, "p16", "dismiss", 1, who="Gail Whiteley")
    close(s); live(s, 10)
    assert [d for d, src, c in _wages_in(s, 12) if src == "org10"] == []
    benefit = [(t.t, t.cents) for t in s.ledger.transfers if t.dst == "h5" and "benefit" in t.memo]
    assert benefit == [(18, 14_000)]
    assert told(s, "p9")[-1] == "Jobseeker's benefit of $140.00 a week starts on Friday 20 March."


def test_a_stopped_wage_is_not_replaced_and_the_job_left_takes_no_wage_after_the_last_pay():
    s = _hamlet(); live(s, 10); routine(s)          # Thursday 12 March
    assert do(s, "p16", "stop", id="ob3").msg.endswith("recorded as a breach.")
    assert do(s, "p16", "set_staff_pay", 1, who="Gail Whiteley", weekly_pay=600).msg == \
        "Not done: Lumb's Grocer has stopped paying Gail Whiteley's wages."
    close(s); live(s, 7); routine(s)                # Friday 20 March
    assert do(s, "p9", "quit").msg == "You have left Lumb's Grocer. Your last pay is on Friday 20 March."
    close(s); live(s, 20)
    assert s.engine_errors == [] and s.get("obligations", "ob3").last <= 20
    assert not [t for t in s.ledger.transfers if t.src == "org10" and t.dst == "h5" and t.t >= 21
                and t.memo.startswith("wages")]                      # a late fee on what is owed may be taken
    _check_run(s)


def test_pay_changed_many_times_in_a_week_is_one_wage_at_the_last_amount():
    s = _hamlet(); live(s, 10)
    for i, weekly in enumerate((600, 450, 600, 450)):
        assert do(s, "p16", "set_staff_pay", i, who="Gail Whiteley", weekly_pay=weekly).ok
    ob = s.get("obligations", "ob3")
    assert (ob.cents, ob.next_cents, ob.next_from) == (43_400, 45_000, 18)
    live(s, 20)
    assert [(d, c) for d, src, c in _wages_in(s, 10) if src == "org10"] == [(11, 43_400), (18, 45_000), (25, 45_000)]
    ob = s.get("obligations", "ob3")
    assert (ob.cents, ob.next_from) == (45_000, -1)                    # the change folded in once it came
    _check_run(s)


def test_reprice_refuses_what_it_cannot_do():
    s = work_town()
    ob = job_of(s, "p03", "org3").wage_ob
    for bad in ((-1, 11), (50_000, 10), (50_000, 0), (50_000, -3)):
        with pytest.raises(ValueError):
            O.reprice(s, ob, *bad, T)
    O.reprice(s, ob, 50_000, 11, T)
    O.reprice(s, ob, 46_000, 18, T)                                    # replaces the pending change
    assert (s.get("obligations", ob).next_cents, s.get("obligations", ob).next_from) == (46_000, 18)
    assert O.instalment_cents(s.get("obligations", ob), 11) == 44_000
    assert O.instalment_cents(s.get("obligations", ob), 25) == 46_000
    O.end(s, ob, 18, T)
    with pytest.raises(ValueError):
        O.reprice(s, ob, 50_000, 11, T)


def test_a_payment_naming_a_wage_more_than_a_week_away_says_nothing_was_counted():
    s = _hamlet(); live(s, 10); routine(s)          # Thursday: Friday's wage is paid by hand
    do(s, "p16", "pay", to="Gail Whiteley", amount=434, **{"for": "ob3"})
    out = do(s, "p16", "pay", 1, to="Gail Whiteley", amount=100, **{"for": "ob3"})
    assert out.msg == ("Paid $100.00 to Gail Whiteley. Nothing was counted toward wages from Lumb's Grocer: "
                       "the next payment on it is more than a week away.")
    assert do(s, "p9", "request", 2, to="Martin Lumb", text="my wage", amount=50, **{"for": "ob3"}).ok
    out = do(s, "p16", "answer", 3, request=s.all("requests")[-1].id, answer="accept")
    assert out.msg.endswith("Nothing was counted toward wages from Lumb's Grocer: the next payment on it is "
                            "more than a week away.")


def test_for_cannot_name_an_ended_wage():
    s = _hamlet(); live(s, 12); routine(s)          # Saturday: the last pay was Friday 13 March
    assert do(s, "p16", "dismiss", who="Gail Whiteley").ok
    assert do(s, "p16", "pay", 1, to="Gail Whiteley", amount=10, **{"for": "ob3"}).msg == \
        "Not done: you have nothing with the id 'ob3' that is owed to Gail Whiteley."
    assert do(s, "p9", "request", 2, to="Martin Lumb", text="x", amount=10, **{"for": "ob3"}).msg == \
        "Not done: 'ob3' is not something Martin Lumb owes you."


def test_a_request_with_no_text_is_named_without_an_empty_quotation():
    from townsim.town.entities import Request
    s = work_town()
    s.add("requests", Request("rq9", 0, "p03", "p01", "", 0, 14, summary="Craig Pryce asks. More words."), T)
    assert do(s, "p01", "answer", request="rq9", answer="decline").ok
    assert told(s, "p03")[-1] == "Ruth Ashby declined your request rq9. Craig Pryce asks."
    s.add("requests", Request("rq10", 0, "p03", "p01", "  ", 0, 14), T)
    assert do(s, "p01", "answer", 1, request="rq10", answer="decline").ok
    assert told(s, "p03")[-1] == "Ruth Ashby declined your request rq10."


# what the fourth review found: a draw of nothing is a wage of nothing; what is announced is paid

def _draws(s, since):
    """(day, cents) of the owner's draw from Lumb's Grocer to Martin Lumb's household (h9)."""
    return [(t.t, t.cents) for t in s.ledger.transfers if t.src == "org10" and t.dst == "h9" and t.t >= since
            and t.memo.startswith("wages")]


@pytest.mark.parametrize("gap", [0, 1, 3])
def test_a_draw_set_to_nothing_and_back_twice_is_one_wage(gap):
    s = _hamlet(); live(s, 7); routine(s)          # Monday 9 March; Martin Lumb draws $457.90
    for i, weekly in enumerate((0, 450, 0, 450)):
        assert do(s, "p16", "set_staff_pay", i, who="Martin Lumb", weekly_pay=weekly).ok
        if gap and i == 1:
            close(s); live(s, gap - 1); routine(s)
    close(s); live(s, 21)
    wages = [o.id for o in s.all("obligations") if o.payer == "org10" and o.payee == "h9" and o.kind == "wage"
             and o.memo == "wages from Lumb's Grocer"]
    assert wages == ["ob1"] and s.get("jobs", "jb1").wage_ob == "ob1"
    days = [d for d, _ in _draws(s, 7)]
    assert len(days) == len(set(days)), _draws(s, 7)
    assert _draws(s, 7)[:2] == [(11, 45_790), (18, 45_000)]
    _check_run(s)


def test_a_draw_of_nothing_opens_no_due_and_shows_no_line():
    s = _hamlet(); live(s, 7); routine(s)
    do(s, "p16", "set_staff_pay", who="Martin Lumb", weekly_pay=0)
    close(s); live(s, 14); routine(s)               # Monday 23 March
    assert [d.day for d in s.where("dues", obligation="ob1") if d.day > 11] == []
    assert not [u for u in O.upcoming(s, "h9", 21) if u.obligation == "ob1"]
    assert O.instalment_cents(s.get("obligations", "ob1"), 25) == 0
    assert "$0.00  Lumb's Grocer" not in view(s, "p16")[0][1]["content"]
    assert _draws(s, 12) == []


def test_a_second_pay_change_takes_the_place_of_the_first_on_its_day():
    s = _hamlet(); live(s, 10); routine(s)          # Thursday 12 March
    assert do(s, "p16", "set_staff_pay", who="Gail Whiteley", weekly_pay=500).msg == \
        "Lumb's Grocer pays Gail Whiteley $500.00 a week from Friday 20 March."
    close(s); routine(s)                            # Friday 13 March
    out = do(s, "p16", "set_staff_pay", 1, who="Gail Whiteley", weekly_pay=560)
    assert out.msg == ("Lumb's Grocer pays Gail Whiteley $560.00 a week from Friday 20 March. "
                       "This replaces the change to $500.00 that was to start on Friday 20 March.")
    assert told(s, "p9")[-1] == ("Lumb's Grocer pays you $560.00 a week from Friday 20 March. "
                                 "This replaces the change to $500.00 that was to start on Friday 20 March.")
    close(s); live(s, 20)
    assert [(d.day, d.cents, d.status) for d in s.where("dues", obligation="ob3") if d.day >= 11][:3] == \
        [(11, 43_400, "paid"), (18, 56_000, "paid"), (25, 56_000, "paid")]
    _check_run(s)


def test_three_pay_changes_across_a_week_pay_what_was_last_announced_for_each_friday():
    s = _hamlet(); live(s, 10); routine(s)          # Thursday 12 March
    do(s, "p16", "set_staff_pay", who="Gail Whiteley", weekly_pay=500)            # from Friday 20 March
    close(s); live(s, 6); routine(s)                # Thursday 19 March: still pending
    out = do(s, "p16", "set_staff_pay", 1, who="Gail Whiteley", weekly_pay=520)
    assert out.msg.endswith("This replaces the change to $500.00 that was to start on Friday 20 March.")
    close(s); live(s, 1); routine(s)                # Saturday 21 March: the change has come
    out = do(s, "p16", "set_staff_pay", 2, who="Gail Whiteley", weekly_pay=540)
    assert out.msg == "Lumb's Grocer pays Gail Whiteley $540.00 a week from Friday 3 April."
    close(s); live(s, 20)
    got = [(t.t, t.cents) for t in s.ledger.transfers if t.src == "org10" and t.dst == "h5" and t.t >= 11]
    assert got[:4] == [(11, 43_400), (18, 52_000), (25, 52_000), (32, 54_000)]
    _check_run(s)


def test_a_request_for_a_wage_answered_after_it_ends_with_nothing_owed_is_refused():
    s = _hamlet(); live(s, 10); routine(s)
    assert do(s, "p9", "request", to="Martin Lumb", text="my wage", amount=50, **{"for": "ob3"}).ok
    rq = s.all("requests")[-1].id
    close(s); live(s, 1); routine(s)                # Saturday: Gail Whiteley is dismissed; her last pay was Friday
    assert do(s, "p16", "dismiss", 1, who="Gail Whiteley").ok
    before = s.hash()
    out = do(s, "p16", "answer", 2, request=rq, answer="accept")
    assert out.msg == "Not done: that was for wages from Lumb's Grocer, which has ended with nothing owed."
    assert s.hash() == before


def test_extra_shifts_are_paid_at_the_rate_in_force_on_the_day_they_are_offered():
    s = work_town()
    out = do(s, "p01", "set_staff_pay", who="Craig Pryce", weekly_pay=760)        # from a Friday ahead
    assert out.ok and "$760.00 a week from Friday 13 March" in out.msg, out.msg
    out = do(s, "p01", "offer_shifts", 1, to="Craig Pryce", hours=8)
    assert out.ok
    assert s.all("requests")[-1].pay == round(8 * 44_000 / 38)                    # $440 a week for 38 hours
    to_day(s, 13)
    do(s, "p01", "offer_shifts", 2, to="Craig Pryce", hours=8)
    assert s.all("requests")[-1].pay == round(8 * 76_000 / 38)
