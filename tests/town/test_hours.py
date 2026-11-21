import pytest

from townsim.town import hours
from townsim.town.entities import Job
from townsim.town.acts import Act, apply_act
from townsim.town.loop import routine

from .helpers import thin, town_with_shop


def give_work(s, holder, account, what):
    return hours.give(s, holder, account, what, "routine:setup")


def test_free_hours_are_what_a_job_and_recurring_work_leave():
    s = town_with_shop()
    give_work(s, "p01", "org3", "ordering"); give_work(s, "p01", "org3", "the books")
    give_work(s, "p01", "h1", "household paperwork")
    hours.monday_hours(s)
    p = s.get("people", "p01")
    assert p.hours_left == 61.5 and p.hours_week == 61.5
    assert s.get("people", "p02").hours_left == 70.0


def test_an_ill_week_has_no_hours():
    s = thin()
    s.set("people", "p01", "routine:t", ill_until=s.day + 3)
    hours.monday_hours(s)
    assert s.get("people", "p01").hours_left == 0.0 and s.get("people", "p01").hours_week == 0.0
    assert s.get("people", "p02").hours_left == 70.0
    for _ in range(7):
        s.advance_day()
    hours.monday_hours(s)
    assert s.get("people", "p01").hours_left == 70.0 and s.get("people", "p01").hours_week == 70.0


def test_an_ill_person_recovers_the_monday_after_ill_until():
    s = thin()
    s.set("people", "p01", "routine:t", ill_until=7)   # ill through day 7, the second Monday
    for _ in range(7):
        s.advance_day()
    hours.monday_hours(s)
    assert s.get("people", "p01").hours_left == 0.0
    for _ in range(7):
        s.advance_day()
    hours.monday_hours(s)
    assert s.get("people", "p01").hours_left == 70.0


def test_hours_never_go_below_zero():
    s = town_with_shop()
    for what in ("ordering",) * 20:
        give_work(s, "p01", "org3", what)
    assert hours.free_hours(s, "p01") == 0.0
    hours.monday_hours(s)
    assert s.get("people", "p01").hours_left == 0.0


def test_a_person_with_no_hours_is_refused_plainly():
    s = thin()
    s.set("people", "p01", "routine:t", hours_left=0.0)
    out = apply_act(s, Act("p01", "p01", "write_notes", {"text": "x"}), 0)
    assert not out.ok and out.msg == "Not done: you have no time left this week for that."


def test_job_hours_are_the_hours_of_the_jobs_a_person_holds():
    s = town_with_shop()
    assert hours.job_hours(s, "p01") == 0.0
    for org, h in (("org3", 30.0), ("org1", 8.5)):
        s.add("jobs", Job(s.new_id("jb", "routine:setup"), "p01", org, 40000, h, "", -7), "routine:setup")
    give_work(s, "p01", "org3", "ordering")
    assert hours.job_hours(s, "p01") == 38.5 and hours.job_hours(s, "p02") == 0.0
    assert hours.free_hours(s, "p01") == 27.5


def test_give_refuses_an_unknown_kind_of_work():
    s = thin()
    with pytest.raises(ValueError):
        hours.give(s, "p01", "h1", "gardening", "routine:setup")
    assert s.all("work") == []


def test_give_copies_the_hours_of_the_work():
    s = thin()
    wid = hours.give(s, "p01", "h1", "rota and pay", "routine:setup")
    w = s.get("work", wid)
    assert wid.startswith("wk") and (w.holder, w.account, w.what, w.hours) == ("p01", "h1", "rota and pay", 2.0)


def test_hours_are_reset_only_on_mondays():
    s = thin()
    s.advance_day()   # day 1, a Tuesday
    s.set("people", "p01", "routine:t", hours_left=3.0)
    hours.monday_hours(s)
    assert s.get("people", "p01").hours_left == 3.0
    for _ in range(6):
        s.advance_day()
    routine(s)        # day 7, a Monday
    assert s.get("people", "p01").hours_left == 70.0


def test_monday_hours_twice_changes_nothing_the_second_time():
    s = town_with_shop()
    give_work(s, "p01", "org3", "ordering")
    hours.monday_hours(s)
    before, mark = s.hash(), s.mark()
    hours.monday_hours(s)
    assert s.hash() == before and s.mark() == mark
    assert s.get("people", "p01").hours_left == 66.0
