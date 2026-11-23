"""Faults found by the second thirteen-week paid run on seed 3 (`runs/e3.sqlite`, run `e3-choice`), each pinned on a
generated town with lane B. Nothing here calls a model."""

from __future__ import annotations

import re

import pytest

from townsim.town import clock
from .test_towns_d import _days, adults, do, late

FEB5, FEB12 = 74, 81                     # Friday 5 and Friday 12 February 2027 on a town begun 23 November 2026


def _other(s, home):
    return next(p for p in s.all("people") if p.age >= 18 and p.household != home)


@pytest.mark.parametrize("seed", [2, 3])
def test_every_operation_reads_a_date_with_no_year_as_the_nearest_such_date(seed):
    from townsim.town import seat_tools as ST
    from townsim.town.replies import Keys, ScriptedReplier
    s, home, iid = late(seed)
    who, other = adults(s, home)[0], _other(s, home)
    ctx = ST.Ctx(ScriptedReplier(lambda k, m: "{}", lambda k, m, t: None), Keys("t"))
    # before the turn of the year: a January date is next January's, on whatever weekday is written beside it
    _days(s, 35 - s.day)                                             # Monday 28 December 2026
    for written in ("Monday 4 January", "Sunday 4 January", "4 January"):
        assert clock.parse_near(written, s.day) == 42, written
    out = do(s, who, "book_appointment", day="Sunday 4 January", about="the bills", **{"with": other.name})
    assert out.ok and "Monday 4 January" in out.msg, out.msg
    # after it: a date a week ahead is this year's though the weekday written is last year's
    _days(s, FEB5 - s.day)
    assert clock.date_year(s.day) == "Friday 5 February 2027"
    out = do(s, who, "book_appointment", day="Thursday 12 February", about="the bills", **{"with": other.name})
    assert out.ok and "Friday 12 February" in out.msg, out.msg
    assert s.all("appointments")[-1].day == FEB12
    out = do(s, who, "standing_payment", to=other.name, amount=5, day="Friday", start="Thursday 12 February", weeks=2)
    assert out.ok and "from Friday 12 February" in out.msg, out.msg
    out = do(s, who, "propose_agreement", to=other.name, amount=5, per="week", weeks=4, start="Thursday 12 February",
             payer="me", what="the bills")
    assert out.ok and "from Friday 12 February 2027" in out.msg, out.msg
    # a date that has passed is named so by each, with its year when that is not this year's
    for op, args in (("book_appointment", {"day": "Thursday 4 February", "about": "the bills", "with": other.name}),
                     ("standing_payment", {"to": other.name, "amount": 5, "day": "Friday",
                                           "start": "Thursday 4 February", "weeks": 2}),
                     ("propose_agreement", {"to": other.name, "amount": 5, "per": "week", "weeks": 4,
                                            "start": "Thursday 4 February", "payer": "me", "what": "the bills"})):
        out = do(s, who, op, **args)
        assert out.msg == "Not done: Thursday 4 February has passed.", (op, out.msg)
    out = do(s, who, "propose_agreement", to=other.name, amount=5, per="week", weeks=4, start="28 December",
             payer="me", what="the bills")
    assert out.msg == "Not done: Monday 28 December 2026 has passed.", out.msg
    # an assistant's follow-up and email search read dates the same way
    inst = s.get("instances", iid)
    done = ST.run_tool(s, inst, "schedule_followup", {"date": "Thursday 12 February", "note": "the bills"}, ctx, 1)
    assert done.ok, done.text
    assert s.all("reminders")[-1].day == FEB12
    done = ST.run_tool(s, inst, "schedule_followup", {"date": "Thursday 4 February", "note": "the bills"}, ctx, 2)
    assert not done.ok and "A follow-up is for a day after today." in done.text, done.text
    assert ST.run_tool(s, inst, "search_email", {"since": "Thursday 4 February"}, ctx, 3).ok


_EXAMPLE = re.compile(r"(?:e\.g\. |Write it like )(" + "|".join(clock.DAYS) + r") (\d{1,2}) (" + "|".join(clock.MONTHS)
                      + r")\b")


def examples_shown(text: str, today: int) -> list[int]:
    """Each example date in `text` (`e.g. Monday 30 November`, `Write it like ...`), checked to be a coming day near
    `today` with its right weekday; the days it names."""
    found = []
    for m in _EXAMPLE.finditer(text):
        day = clock.nearest_date(int(m.group(2)), clock.MONTHS.index(m.group(3)) + 1, today)
        assert clock.DAYS[clock.dow(day)] == m.group(1), (m.group(0), clock.date_year(today))
        assert today < day <= today + 14, (m.group(0), clock.date_year(today))
        found.append(day)
    return found


@pytest.mark.parametrize("seed", [2, 3])
def test_an_example_date_is_a_coming_day_near_the_day_it_is_shown_on(seed):
    from townsim.town import loop, views
    s, home, _ = late(seed)
    who, other = adults(s, home)[0], _other(s, home)
    for until in (0, 35, 40, 60, 74):                    # November, the turn of the year, January, February
        _days(s, until - s.day)
        loop.routine(s)
        text = "\n".join(m["content"] for m in views.view(s, who)[0])
        assert len(examples_shown(text, s.day)) >= 2, text
        for op, args in (("book_appointment", {"day": "soon", "about": "the bills", "with": other.name}),
                         ("standing_payment", {"to": other.name, "amount": 5, "day": "Friday", "start": "soon"}),
                         ("propose_agreement", {"to": other.name, "amount": 5, "per": "week", "weeks": 4,
                                                "start": "soon", "payer": "me", "what": "the bills"})):
            out = do(s, who, op, **args)
            assert "Write it like" in out.msg and len(examples_shown(out.msg, s.day)) == 1, out.msg


def test_without_lane_b_the_example_dates_are_as_they_were():
    from townsim.town import gen, loop, views
    s = gen.generate(3, ["a", "b", "c", "d"])
    loop.routine(s)
    who = next(p.id for p in s.all("people") if p.age >= 18)
    text = "\n".join(m["content"] for m in views.view(s, who)[0])
    assert "e.g. Monday 13 April" in text and "e.g. Thursday 12 March" in text
    assert clock.unreadable("soon") == "'soon' is not a date that can be read. Write it like Monday 16 March."

