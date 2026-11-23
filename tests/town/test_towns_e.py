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


@pytest.mark.parametrize("seed", [2, 3])
def test_the_one_who_is_paid_is_told_what_a_stopped_agreement_is_as_the_payer_is(seed):
    from townsim.town import facts, obligations
    from townsim.town.clock import date_text
    from townsim.town.obligations import money
    from .test_towns_d import _two_households, _view, town
    s = town(seed)
    payer, payee = _two_households(s)
    debtor, lender = adults(s, payer.id)[0], adults(s, payee.id)[0]
    ob = obligations.create(s, kind="agreement", payer=payer.id, payee=payee.id, cents=300, first=s.day + 3, every=7,
                            memo="club membership", ends="both", made_by=debtor, cause="routine:t", party=lender)
    _days(s, 7)
    assert do(s, debtor, "stop", id=ob).ok
    on = date_text(s.day)
    _days(s, 15)
    late = sum(d.cents - d.paid for d in obligations.owed_dues(s, ob))
    head = (f"{ob} (club membership), paid to you by the {s.get('households', payer.id).name}, was stopped "
            f"by them on {on} and recorded as a breach; {money(late)} of it remains owed to you.")
    text = _view(s, lender)
    assert ob in text, text                                 # the id the payee's own view lists
    for op in ("propose_end", "stop"):
        out = do(s, lender, op, id=ob)
        assert not out.ok and out.msg.startswith(f"Not done: {head}"), (op, out.msg)
    assert do(s, lender, "stop", id=ob).msg.endswith(" Stop is for what you pay.")
    worry = " ".join(facts.worries(s, lender, 20))
    assert f"stopped by the {s.get('households', payer.id).name} on {on}" in worry, worry
    out = do(s, lender, "request", to=s.get("people", debtor).name, text="what is owed on the club", amount=3,
             **{"for": ob})
    assert out.ok, out.msg
    # an agreement that ended, from the side it was paid to
    ended = obligations.create(s, kind="agreement", payer=payer.id, payee=payee.id, cents=200, first=s.day + 1,
                               every=7, last=s.day + 8, memo="lessons", ends="both", made_by=debtor,
                               cause="routine:t", party=lender)
    _days(s, 10)
    assert s.get("obligations", ended).status == "finished"
    out = do(s, lender, "propose_end", id=ended)
    assert out.msg.startswith(f"Not done: {ended} (lessons), paid to you by the ") and " ended on " in out.msg, out.msg


@pytest.mark.parametrize("seed", [2, 3])
def test_subscribing_again_says_what_can_and_cannot_be_changed_and_the_letter_it_names_reaches_the_assistant(seed):
    from townsim.town import product
    s, home, iid = late(seed)
    who = adults(s, home)[0]
    sub = product.subscription_of(s, home)
    asks = sub.asks
    out = do(s, who, "subscribe", to=product.launched(s).name, **{"for": "household", "asks": "Warn me before rent."})
    inst = s.get("instances", iid)
    assert out.msg == (f'Not done: {product.client_name(s, home)} already subscribes ({sub.id}); its assistant is '
                       f'written to as "{inst.name}". {product.AFTER_SUBSCRIBING}'), out.msg
    assert product.subscription_of(s, home).asks == asks
    out = do(s, who, "message", to="my assistant", text="Warn me before rent.")
    assert out.ok and [m.to for m in s.all("messages") if m.text == "Warn me before rent."] == [iid], out.msg


@pytest.mark.parametrize("seed", [2, 3])
def test_an_id_in_a_clients_words_is_the_assistants_and_in_the_assistants_the_clients(seed):
    from townsim.town import obligations, seat_ids, seat_texts, seat_tools as ST, views, loop
    from townsim.town.replies import Keys, ScriptedReplier
    s, home, iid = late(seed)
    who = adults(s, home)[0]
    others = [h.id for h in s.all("households") if h.id != home and adults(s, h.id)]
    mine = obligations.create(s, kind="agreement", payer=home, payee=others[0], cents=300, first=s.day + 3, every=7,
                              memo="club membership", ends="both", made_by=who, cause="routine:t",
                              party=adults(s, others[0])[0])
    theirs = obligations.create(s, kind="agreement", payer=others[0], payee=others[1], cents=300, first=s.day + 3,
                                every=7, memo="lessons", ends="both", made_by=adults(s, others[0])[0],
                                cause="routine:t", party=adults(s, others[1])[0])
    asked = do(s, adults(s, others[0])[0], "request", to=s.get("people", who).name, text="the club", amount=3,
               **{"for": mine})
    assert asked.ok, asked.msg
    rq = s.all("requests")[-1].id
    inst = s.get("instances", iid)
    sees = seat_ids.seen_by(s, iid)
    assert {mine, rq} <= sees and theirs not in sees
    # the client's own view names what the assistant sees as the assistant does, and the rest as the records do
    loop.routine(s)
    text = views.view(s, who)[0][1]["content"]
    assert seat_ids.display(s, mine) in text and seat_ids.display(s, rq) in text and f"({mine}" not in text
    # the client's words to the assistant: what it sees in its form, the rest as written
    words = f"About {mine}, {rq} and {theirs}."
    assert do(s, who, "message", to="my assistant", text=words).ok
    shown = "\n".join(x.body for x in seat_texts.items(s, inst) if x.kind == "email" and "About" in x.body)
    assert shown == f"About {seat_ids.display(s, mine)}, {seat_ids.display(s, rq)} and {theirs}.", shown
    # the assistant's words to the client: what it sees in the client's form (its own), the rest as the records name it
    ctx = ST.Ctx(ScriptedReplier(lambda k, m: "{}", lambda k, m, t: None), Keys("t"))
    body = f"On {seat_ids.display(s, mine)} and {seat_ids.display(s, theirs)}."
    done = ST.run_tool(s, inst, "send_email", {"to": s.get("people", who).name, "body": body}, ctx, 1)
    assert done.ok, done.text
    _days(s, 1)
    loop.routine(s)
    text = views.view(s, who)[0][1]["content"]
    assert f"On {seat_ids.display(s, mine)} and {theirs}." in text, text


@pytest.mark.parametrize("seed", [2, 3])
def test_the_account_states_the_free_weeks_the_records_hold_from_the_day_the_paid_plan_started(seed):
    from townsim.town import connections, obligations, product, story
    s, home, iid = late(seed)
    who = adults(s, home)[0]
    _days(s, 20)
    sub = product.subscription_of(s, home)
    connections.make(s, who, s.get("instances", iid), f"account:{home}", "limit", 10000, "routine:t")
    ob = s.get("obligations", product.subscription_of(s, home).fee_ob)
    assert ob.first == s.day + 28 and sub.day == 0            # the paid plan began 20 days after subscribing
    head = "\n".join(story._instance_head(None, s, s.get("instances", iid)))
    fee = f"{obligations.money(ob.cents)} every 4 weeks"
    assert f"free until {clock.date_text(ob.first - 1)}, then {fee}" in head, head
