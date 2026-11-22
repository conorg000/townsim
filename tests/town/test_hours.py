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
    assert not out.ok and out.msg == ("Not done: you have no time of your own left this week (your work leaves 70 "
                                      "hours of the week's 70 hours). It returns on Monday 9 March.")


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


# what a person is told of their own time

def test_the_view_says_the_time_is_their_own_and_what_things_take_from_the_operations():
    from townsim.town import acts
    from townsim.town.views import person_view
    s = town_with_shop()
    owner = person_view(s, "p01")[1]["content"].split("\n")
    other = person_view(s, "p03")[1]["content"].split("\n")
    at = owner.index("Time of your own left this week, outside your work: 70 hours.")
    letter, talk, offer = (hours.length(acts.REGISTRY[op].hours) for op in ("message", "converse", "offer_job"))
    assert owner[at + 1] == (f"A letter takes {letter}, a talk {talk}, a request ¼ hour, a payment ¼ hour, "
                             f"a job offer {offer}, an offer of extra hours ¼ hour.")
    assert letter == "¼ hour" and talk == "1 hour" and offer == "½ hour"
    assert other[other.index("Time of your own left this week, outside your work: 70 hours.") + 1] == \
        "A letter takes ¼ hour, a talk 1 hour, a request ¼ hour, a payment ¼ hour."


def test_an_ill_person_is_told_until_when_and_nothing_reads_as_a_change_to_their_hours():
    from townsim.town.views import person_view
    s = town_with_shop()
    s.set("people", "p01", "routine:t", ill_until=6, hours_left=0.0)
    user = person_view(s, "p01")[1]["content"]
    assert ("You are ill until Sunday 8 March: you are not at work and have no time of your own until Monday 9 March."
            in user.split("\n"))
    assert "Time of your own" not in user and "hours left" not in user
    out = apply_act(s, Act("p01", "p01", "message", {"to": "Tom Ashby", "text": "hello"}), 0)
    assert out.msg == ("Not done: you have no time of your own left this week (you are ill until Sunday 8 March). "
                       "It returns on Monday 9 March.")


def test_the_refusal_says_what_used_the_time_and_when_it_returns():
    from townsim.town import ops_core  # noqa: F401
    s = town_with_shop()
    s.add("firms", __import__("townsim.town.entities", fromlist=["FirmRow"]).FirmRow(
        "fm1", "org3", "food", 1000, 0.6, 0.5, 0.5, 60, 2), "routine:t")
    for _ in range(3):
        s.advance_day()
    s.set("people", "p01", "routine:t", hours_left=27.0)
    assert apply_act(s, Act("p01", "p01", "work_extra_hours", {"hours": 25.9}), 0).ok
    for k in range(3):
        assert apply_act(s, Act("p01", "p01", "message", {"to": "Tom Ashby", "text": f"note {k}"}), 1 + k).ok
    out = apply_act(s, Act("p01", "p01", "converse", {"with": "Tom Ashby", "opening": "A word?"}), 5)
    assert out.msg == ("Not done: you have 0.35 hours of your own left this week, and this takes 1 hour (25.9 extra "
                       "hours worked at Marsh's Stores on Thursday; 3 letters, ¾ hour). It returns on Monday "
                       "9 March.")
