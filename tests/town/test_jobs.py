"""Jobs: offers accepted and declined, wages on Fridays, quitting and dismissal, extra hours,
pay by hand, benefit, stock orders, prices, an owner's own hours and moving money."""

import pytest

from townsim.town import acts, firms, jobs
from townsim.town import obligations as O
from townsim.town.acts import Act, apply_act
from townsim.town.entities import Event, FirmRow, Org, Work
from townsim.town.loop import close, routine
from townsim.town.views import view

from .helpers import add_person, add_shop, add_supplier, delivered, set_cash, town_with_shop
from .test_random_play import _check_run

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
        ("job", "A job offer.", 0, 7, 6, 38.0, 44_000)
    assert rq.summary == ("Marsh's Stores offers Megan Pryce a job: $440.00 a week for 38 hours, paid Fridays, "
                          "from Monday 9 March.")
    live(s, 1)
    out = accept(s, "p04", rq)
    assert out.ok and out.msg == "Accepted. Megan Pryce starts at Marsh's Stores on Monday 9 March."
    assert told(s, "p01")[-1] == ('Megan Pryce accepted your request rq1 ("A job offer."). '
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
    assert out.ok and (rq.kind, rq.hours, rq.pay, rq.expires) == ("shifts", 8.0, 9_263, 6)
    assert rq.summary == "Marsh's Stores offers Craig Pryce 8 extra hours this week, paid $92.63 on Friday."
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
    live(s, 6)
    assert refused(s, "p01", "offer_shifts", to="Craig Pryce", hours=8) == "Not done: this week ends today."


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
    assert job.weekly == 50_000 and s.get("obligations", job.wage_ob).first == 11
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
    jid = jobs.start(s, "p01", "org3", 30_000, 38, 4, T, since=-7)
    out = do(s, "p01", "set_staff_pay", who="Ruth Ashby", weekly_pay=0)
    assert out.msg == "Marsh's Stores pays you $0.00 a week from Friday 13 March."
    assert s.get("jobs", jid).wage_ob == "" and s.get("jobs", jid).weekly == 0
    to_day(s, 19)
    assert paid(s, "org3", "h1") == [(4, 30_000)]
    out = do(s, "p01", "set_staff_pay", who="Ruth Ashby", weekly_pay=200)
    assert out.msg == "Marsh's Stores pays you $200.00 a week from Friday 27 March." and out.ok
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
