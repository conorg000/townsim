import json
import time
from collections import Counter

import pytest

from townsim.town.entities import Message, Request
from townsim.town.loop import MAX_ACTS, parse_acts, routine, run_day, who_acts
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.thin import thin_script, thin_town
from townsim.town.views import person_view

EMPTY = '{"thoughts": "", "acts": []}'


def run_thin(days, script=thin_script):
    s = thin_town(); keys = Keys("thin"); reports = []
    for _ in range(days):
        reports.append(run_day(s, ScriptedReplier(script), keys))
    return s, reports


@pytest.fixture(scope="module")
def weeks8():
    return run_thin(56)


def eight_weeks():
    return run_thin(56)[0]


def turns(reports, pid):
    return {r.day: t for r in reports for t in r.turns if t.pid == pid}


# parse_acts

@pytest.mark.parametrize("text, acts", [
    ('{"thoughts": "x", "acts": [{"op": "pay"}]}', [{"op": "pay"}]),
    ('Here it is:\n```json\n{"thoughts": "x", "acts": []}\n```', []),
    ('{"note": 1} then {"acts": [1, "two"]} and {"acts": [3]}', [1, "two"]),
    ('{"acts": "not a list"} {"acts": [4]}', [4]),
    ('{"outer": {"acts": [5]}}', None), ('{"a": 1} {"acts": []}', []),
    ('{"a": {"acts": [6]}} {"acts": [7]}', [7]),
    (None, None), ("", None), ("no braces at all", None), ("{not json", None), ('{"acts": {}}', None),
    ("[" * 5000 + "{" * 5000, None), ('{"acts": [' * 3000, None),
])
def test_parse_acts_finds_the_first_object_with_an_acts_list(text, acts):
    assert parse_acts(text) == acts


def test_parse_acts_gives_up_quickly_on_junk():
    started = time.perf_counter()
    assert parse_acts("{" * 1_000_000) is None
    assert parse_acts("{x " * 100_000 + '{"acts": []}') is None              # past the attempts allowed
    assert parse_acts(" " * 20_000 + '{"acts": []}') is None                 # past the characters scanned
    assert time.perf_counter() - started < 0.5


# who acts, and the routine

def test_who_acts_is_new_things_open_requests_and_stock_day():
    s = thin_town()
    assert who_acts(s) == []
    s.add("messages", Message("msg1", 0, "p01", "p04", "Hello."), "routine:t")
    s.add("requests", Request("rq1", 0, "p03", "p02", "Ladder?", 0, 14), "routine:t")
    assert who_acts(s) == ["p02", "p04"]
    for _ in range(4):
        s.advance_day()
    assert who_acts(s) == ["p01", "p02", "p03", "p04"]


def test_routine_resets_hours_on_monday_and_lapses_old_requests():
    s = thin_town()
    text = "Could you look after the children on Saturday, as we have to be out all day? " * 2
    s.add("requests", Request("rq1", 0, "p03", "p01", text, 0, 14), "routine:t")
    s.add("requests", Request("rq2", 0, "p03", "p02", "Ladder?", 0, 15), "routine:t")
    s.set("people", "p02", "routine:t", hours_left=3.5)
    for _ in range(14):
        s.advance_day()
    routine(s)                                              # day 14, a Monday; rq1 is still open today
    assert s.get("people", "p02").hours_left == 70.0 and s.get("requests", "rq1").status == "open"
    assert [c.cause for c in s.changes if c.table == "people" and c.day == 14] == ["routine:week"]
    s.advance_day(); routine(s)
    assert s.get("requests", "rq1").status == "lapsed" and s.get("requests", "rq2").status == "open"
    notice = s.all("notices")[-1]
    assert notice.to == "p03" and notice.text == (
        f"Your request rq1 to Ruth Ashby ({text[:80]}…) was not answered and has lapsed.")
    assert any(c.cause == "routine:lapse:rq1" for c in s.changes)


# a day

def test_everyone_sees_the_morning_and_acts_apply_in_id_order():
    seen = {}

    def capture(key, messages):
        seen[(key.day, key.actor)] = messages[1]["content"]
        return thin_script(key, messages)

    s, reports = run_thin(5, capture)
    friday = reports[4]
    assert [t.pid for t in friday.turns] == ["p01", "p02", "p03"]   # Tom hears his letter came back
    assert all(o.ok for t in friday.turns for o in t.outcomes)
    assert "Balance: $397.00" in seen[(4, "p03")]           # before Ruth's $25, which is applied first
    ruth = next(i for i, t in enumerate(s.ledger.transfers) if t.cause == "op:p01:pay:d4.0")
    craig = next(i for i, t in enumerate(s.ledger.transfers) if t.cause.startswith("op:p03:pay:d4."))
    assert ruth < craig and s.ledger.transfers[ruth].dst == "h2"
    assert s.ledger.transfers[craig].cause == "op:p03:pay:d4.2"   # numbered across the day


def test_eight_weeks_conserve_money_and_collect_nothing_twice(weeks8):
    s, _ = weeks8
    assert s.ledger.total() == 0 and s.engine_errors == [] and s.day == 56
    collected = Counter(t.cause for t in s.ledger.transfers if t.cause.startswith("routine:collect:"))
    assert max(collected.values()) == 1
    assert all(d.paid <= d.cents for d in s.all("dues"))
    # Craig paid Monday 9 March's rent by hand on Friday 6 March: from day 4 to day 10 exactly $185 reached org1 from h2
    assert sum(t.cents for t in s.ledger.transfers if t.src == "h2" and t.dst == "org1" and 4 <= t.t <= 10) == 18_500
    assert any(o.penalty for o in s.all("obligations"))      # the Pryces fall behind, as the figures make them


def test_views_stay_within_1200_words_after_eight_weeks(weeks8):
    s, _ = weeks8
    assert all(len(person_view(s, p.id)[1]["content"].split()) <= 1200 for p in s.all("people"))


def test_the_script_does_what_the_thin_town_needs(weeks8):
    s, reports = weeks8
    standing = s.get("obligations", "ob9")
    assert (standing.kind, standing.payer, standing.payee, standing.cents, standing.status) == (
        "standing", "h1", "h2", 2000, "stopped")
    assert [(d.day, d.status) for d in s.all("dues") if d.obligation == "ob9"] == [(11, "paid"), (18, "paid"), (25, "paid")]
    assert [t.t for t in s.ledger.transfers
            if t.cause.startswith("routine:collect:") and (t.src, t.dst) == ("h1", "h2")] == [11, 18, 25]
    [loan] = [t for t in s.ledger.transfers if t.cause.startswith("op:p01:answer:")]
    assert (loan.cents, loan.memo) == (5000, "request rq1")      # the request is what it is for
    assert not any(c.table == "dues" for c in s.changes if c.cause == loan.cause)
    hand = [t for t in s.ledger.transfers if t.cause.startswith("op:p03:pay:")]
    assert len(hand) == 1 and hand[0].t == 4 and hand[0].dst == "org1"
    rq = s.get("requests", "rq1")
    assert (rq.sender, rq.to, rq.cents, rq.status) == ("p03", "p01", 5000, "accepted")
    tom = turns(reports, "p02")
    assert tom[3].outcomes[0].msg == "Not done: your letter to 'Edith Marsh' came back undelivered."
    assert tom[10].outcomes[0].msg.startswith("Not done:")
    megan = turns(reports, "p04")
    assert megan[5].invalid and megan[5].outcomes == []
    assert len(megan[12].outcomes) == MAX_ACTS and s.get("people", "p04").notes == "Shopping list, version 8"


def test_an_invalid_reply_applies_nothing_and_leaves_the_news_unseen():
    s = thin_town()
    s.add("messages", Message("msg1", 0, "p01", "p04", "Hello."), "routine:t")
    report = run_day(s, ScriptedReplier(lambda key, messages: "not json"), Keys("x"))
    assert report.turns[0].invalid and not s.get("messages", "msg1").seen
    report = run_day(s, ScriptedReplier(lambda key, messages: EMPTY), Keys("x"))
    assert report.turns[0].pid == "p04" and not report.turns[0].invalid and s.get("messages", "msg1").seen


def test_elements_that_are_not_objects_are_refused_one_by_one():
    s = thin_town()
    s.add("messages", Message("msg1", 0, "p01", "p04", "Hello."), "routine:t")
    reply = json.dumps({"acts": ["pay", 3, None, {"op": "write_notes", "text": "kept"}]})
    report = run_day(s, ScriptedReplier(lambda key, messages: reply), Keys("x"))
    assert [o.ok for o in report.turns[0].outcomes] == [False, False, False, True]
    assert s.get("people", "p04").notes == "kept" and s.engine_errors == []


# what came of their acts

def _reply(*acts):
    return json.dumps({"thoughts": "", "acts": list(acts)})


def scripted(by_turn):
    """A script from {(day, actor): reply}; everyone else does nothing."""
    return lambda key, messages: by_turn.get((key.day, key.actor), EMPTY)


def test_each_act_leaves_a_result_with_its_own_cause():
    s = thin_town()
    long_op = "Pay\x07" + "x" * 60
    script = scripted({(4, "p01"): _reply({"op": "pay", "to": "Megan Pryce", "amount": 5},
                                          {"op": long_op, "to": "Megan Pryce"}, 7)})
    s, reports = run_thin(5, script)
    [paid, unknown, junk] = [r for r in s.all("results") if r.actor == "p01"]
    assert (paid.day, paid.op, paid.ok, paid.seen) == (4, "pay", True, False)
    assert paid.text == "Paid $5.00 to Megan Pryce."
    assert unknown.op == ("Pay" + "x" * 60)[:40] and not unknown.ok   # cleaned, then cut to 40 and unknown.text.startswith("Not done:")
    assert junk.op == "(no operation named)" and not junk.ok
    causes = [c.cause for c in s.changes if c.table == "results" and c.field == "+"]
    assert causes == ["op:p01:pay:d4.0", "op:p01:?:d4.1", "op:p01:?:d4.2"]
    assert [i.id for i in s.all("results")] == ["res1", "res2", "res3"]


def test_a_refusal_wakes_its_actor_and_is_shown_after_today():
    seen = {}

    def capture(key, messages):
        seen[(key.day, key.actor)] = messages[1]["content"]
        return thin_script(key, messages)

    s, reports = run_thin(5, capture)
    assert "p02" in [t.pid for t in reports[4].turns]        # Tom's letter came back on Thursday; he acts on Friday
    text = seen[(4, "p02")]
    assert text.startswith("TODAY: Friday 6 March.\n\nWHAT CAME OF YOUR LAST ACTS\n"
                           "message: Not done: your letter to 'Edith Marsh' came back undelivered.\n\n"
                           "YOUR HOUSEHOLD'S ACCOUNT\n")
    [result] = [r for r in s.all("results") if r.actor == "p02"]
    assert result.seen and any(c.cause == "routine:seen:p02" and c.table == "results" for c in s.changes)


def test_a_success_waits_for_another_turn_and_a_read_does_not():
    script = scripted({(0, "p01"): _reply({"op": "write_notes", "text": "x"}),
                       (0, "p03"): _reply({"op": "read", "what": "agreements"})})
    s = thin_town(); keys = Keys("x"); replier = ScriptedReplier(script)
    s.set("people", "p01", "routine:t", stock_day=0); s.set("people", "p03", "routine:t", stock_day=0)
    run_day(s, replier, keys)
    assert who_acts(s) == ["p03"]                             # the read's result wakes Craig; Ruth's notes do not
    text = person_view(s, "p03")[1]["content"]
    assert ("WHAT CAME OF YOUR LAST ACTS\nread: ob6: rent, $185.00 every week to Wenlow Lettings, "
            "first Monday 2 March, active\n  ob7: energy") in text
    run_day(s, replier, keys)
    assert all(r.seen for r in s.all("results") if r.actor == "p03")
    assert not s.get("results", "res1").seen                  # Ruth has not had a turn since
    assert "write_notes: Noted." in person_view(s, "p01")[1]["content"]


def test_an_invalid_reply_leaves_results_unseen():
    s = thin_town(); keys = Keys("x")
    run_day(s, ScriptedReplier(lambda key, messages: EMPTY), keys)
    s.set("people", "p01", "routine:t", stock_day=1)
    run_day(s, ScriptedReplier(lambda key, messages: _reply({"op": "fly"})), keys)
    run_day(s, ScriptedReplier(lambda key, messages: "not json"), keys)
    assert [r.seen for r in s.all("results")] == [False]
    run_day(s, ScriptedReplier(lambda key, messages: EMPTY), keys)
    assert [r.seen for r in s.all("results")] == [True]


def test_results_are_never_held_back_by_the_word_cap():
    s = thin_town()
    for i in range(10):
        s.add("messages", Message(f"msg{i + 1}", 0, "p01", "p03", f"letter {i + 1} " + "word " * 298), "routine:t")
    s.set("people", "p03", "routine:t", stock_day=0)
    reply = _reply({"op": "fly"}, {"op": "read", "what": "account in full"})
    run_day(s, ScriptedReplier(lambda key, messages: reply if key.actor == "p03" and key.day == 0 else "x"), Keys("x"))
    text = person_view(s, "p03")[1]["content"]
    assert "fly: Not done: there is no operation called 'fly'.\nread: Balance: " in text
    assert " more: " in text and " letters, 0 notices)" in text


def test_ids_are_counted_under_the_cause_of_what_made_them(weeks8):
    s, _ = weeks8
    counters = [i for i, c in enumerate(s.changes) if c.table == "counter"]
    assert counters
    for i in counters:                          # each id is counted, then its row added, under one cause
        c, row = s.changes[i], s.changes[i + 1]
        assert row.field == "+" and row.cause == c.cause != "routine:new_id" and row.id == f"{c.id}{c.new}", (c, row)
