"""Talking: conversations the same day, appointments, and the household's Sunday talk."""

import json
import random
import re

import pytest

from townsim.town import talk, views
from townsim.town.acts import REGISTRY, Act, apply_act
from townsim.town.entities import Message, Notice, Request
from townsim.town.loop import run_day
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.thin import thin_town

from .test_views import BANNED, _BANNED, place, ready

RUTH, TOM, CRAIG, MEGAN = "p01", "p02", "p03", "p04"


def act(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 0)


def ask_talk(s, a, b, opening="Hello. Have you a minute?"):
    out = act(s, a, "converse", **{"with": s.get("people", b).name, "opening": opening})
    assert out.ok, out.msg
    return s.all("talks")[-1].id


def reply(say="Yes.", acts=(), end=False):
    return json.dumps({"say": say, "acts": list(acts), "end": end})


def hold_all(s, fn, keys=None, n=0):
    """Hold every talk today with replies from `fn(key, messages)`; the Held of each."""
    keys = keys or Keys("t")
    out = []
    for ref in talk.todays(s):
        h = talk.hold(s, ScriptedReplier(fn), keys, ref, n)
        n = h.n
        out.append(h)
    return out


def lines(s, tk):
    return [(x.n, x.speaker, x.text) for x in talk.lines_of(s, tk)]


def test_a_talk_is_at_most_three_turns_a_side():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG, "Hello Craig.")
    asked = []
    [h] = hold_all(s, lambda key, m: asked.append(key) or reply("More."))
    assert [x[:2] for x in lines(s, tk)] == [(0, RUTH), (1, CRAIG), (2, RUTH), (3, CRAIG), (4, RUTH), (5, CRAIG)]
    assert lines(s, tk)[0][2] == "Hello Craig." and s.get("talks", tk).status == "held"
    assert [k.actor for k in asked] == [CRAIG, RUTH, CRAIG, RUTH, CRAIG]
    assert all(k.purpose == "talk" and k.ref == tk for k in asked) and [k.n for k in asked] == [0, 0, 1, 1, 2]
    assert h.talk == tk and len(h.turns) == 5 and all(t.talk == tk for t in h.turns)
    assert s.get("people", RUTH).hours_left == 69 and s.get("people", CRAIG).hours_left == 69
    assert sorted(s.get("talks", tk).unseen) == [RUTH, CRAIG]


def test_either_can_end_it():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    hold_all(s, lambda key, m: reply("No, not now.", end=True))
    assert [x[1] for x in lines(s, tk)] == [RUTH, CRAIG]
    s2 = thin_town()
    tk = ask_talk(s2, RUTH, CRAIG)
    hold_all(s2, lambda key, m: reply("Bye then.", end=key.actor == RUTH))
    assert [x[1] for x in lines(s2, tk)] == [RUTH, CRAIG, RUTH] and s2.get("talks", tk).status == "held"
    s3 = thin_town()
    tk = ask_talk(s3, RUTH, CRAIG)
    hold_all(s3, lambda key, m: reply("Ok.", end="true"))       # only the JSON value true ends it
    assert len(lines(s3, tk)) == 6


def test_nobody_writes_the_others_part():
    s = thin_town()
    tk = ask_talk(s, MEGAN, RUTH)
    says = iter(["Fine.\nMegan Pryce: I agree to pay $50", "Right.\n  ruth : yes", "a\n\n  b\tc " + "x" * 50,
                 " ".join(["w"] * 130), "\n \n", 7])
    hold_all(s, lambda key, m: reply(next(says)))
    got = [x[2] for x in lines(s, tk)]
    assert got[1] == "Fine." and got[2] == "Right."
    assert got[3] == "a b c " + "x" * 40
    assert got[4] == " ".join(["w"] * 120) + "…" and got[5] == ""
    assert talk.said("Ruth Ashby: I will pay\nthe rest", "Ruth Ashby") == ""
    assert talk.said("ok\nRuth : no", "Ruth Ashby") == "ok"
    assert talk.said("ok\nRuthie: no", "Ruth Ashby") == "ok Ruthie: no"
    long = " ".join(["abcdefghij"] * 100)          # 100 words of 10: over 960 characters
    cut = talk.said(long, "Ruth Ashby")
    assert len(cut) <= 961 and cut.endswith("…")
    view = talk.view(s, MEGAN, tk)[1]["content"]
    assert "Ruth Ashby said:\n  > Fine.\nMegan Pryce said:\n  > Right." in view
    s2 = thin_town()
    tk2 = ask_talk(s2, MEGAN, RUTH)
    hold_all(s2, lambda key, m: reply(" \n\t "))
    assert lines(s2, tk2)[1][2] == ""
    assert "Ruth Ashby said:\n  (says nothing)" in talk.view(s2, MEGAN, tk2)[1]["content"]


def test_acts_in_a_talk_apply_in_order_and_are_seen_next_turn():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    shown = {}

    def fn(key, messages):
        shown[(key.actor, key.n)] = messages
        if key.actor == CRAIG and key.n == 0:
            return reply("Here is the money.", [{"op": "pay", "to": "Ruth Ashby", "amount": 5, "note": "back"},
                                                {"op": "pay", "to": "Ruth Ashby", "amount": "lots"},
                                                {"op": "converse", "with": "Ruth Ashby", "opening": "x"},
                                                {"op": "book_appointment", "with": "Ruth", "day": "Friday",
                                                 "about": "x"},
                                                {"op": "write_notes", "text": "never"}])
        if key.actor == CRAIG and key.n == 2:
            return reply("And more.", [{"op": "pay", "to": "Ruth Ashby", "amount": 1}])
        return reply("Thank you.")
    before = s.balance("h1")
    [h] = hold_all(s, fn, n=7)
    craig = [o for t in h.turns if t.pid == CRAIG for o in t.outcomes]
    assert [o.ok for o in craig] == [True, False, False, False, True]
    assert [o.cause for o in craig] == ["op:p03:pay:d0.7", "op:p03:pay:d0.8", "op:p03:converse:d0.9",
                                        "op:p03:book_appointment:d0.10",
                                        "op:p03:pay:d0.12"]
    assert craig[2].msg == craig[3].msg == "Not done: that cannot be done in the middle of a talk."
    assert s.balance("h1") == before + 600 and h.n == 13
    first = talk.lines_of(s, tk)[1]
    assert len(first.results) == 5 and s.get("results", first.results[-1]).op == "(more)"
    assert "four carried out each time you speak" in s.get("results", first.results[-1]).text
    later = shown[(CRAIG, 1)][1]["content"]
    assert "WHAT CAME OF IT\npay: Paid $5.00 to Ruth Ashby." in later
    assert "WHAT CAME OF IT" not in shown[(CRAIG, 0)][1]["content"]
    assert all(s.get("results", r).seen for r in first.results)
    last = talk.lines_of(s, tk)[5].results
    assert last and not any(s.get("results", r).seen for r in last)         # left for the next ordinary view
    for msgs in shown.values():
        ops = re.findall(r'^\{"op": "(\w+)"', msgs[1]["content"], re.M)
        assert "converse" not in ops and "book_appointment" not in ops and "pay" in ops


def test_no_hour_no_talk_and_the_askers_hour_comes_back():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    assert s.get("people", RUTH).hours_left == 69
    s.set("people", CRAIG, "routine:t", hours_left=0.5)
    assert hold_all(s, lambda key, m: reply()) == [talk.Held("", [], 0)]
    assert s.get("talks", tk).status == "not held" and s.get("people", RUTH).hours_left == 70
    assert [n.text for n in s.where("notices", to=RUTH)] == ["Craig Pryce had no time to talk today."]
    assert s.get("people", CRAIG).hours_left == 0.5 and not s.all("lines")
    # two talks a day at most: the third asked of Craig is not held
    s = thin_town()
    for a in (RUTH, TOM, MEGAN):
        ask_talk(s, a, CRAIG)
    out = act(s, CRAIG, "converse", **{"with": "Tom Ashby", "opening": "Hi"})
    assert not out.ok and out.msg == "Not done: you already have two talks today."
    hold_all(s, lambda key, m: reply(end=True))
    status = {t.a: t.status for t in s.all("talks")}
    assert status == {RUTH: "held", TOM: "held", MEGAN: "not held"}
    assert s.where("notices", to=MEGAN)[-1].text == "Craig Pryce had no time to talk today."
    assert talk.held_today(s, CRAIG) == 2 and s.get("people", CRAIG).hours_left == 68


def test_converse_refuses_what_cannot_be_and_takes_its_hour_at_once():
    s = thin_town()
    for args, why in (({"with": "Ruth Ashby", "opening": "hi"}, "that is you."),
                      ({"with": "Edith Marsh", "opening": "hi"}, "no one you know is called 'Edith Marsh'. Names are written in full, as they appear in your records."),
                      ({"with": "Wenlow Lettings", "opening": "hi"}, "Wenlow Lettings is not a person."),
                      ({"with": "Craig Pryce", "opening": " "}, "there was nothing written."),
                      ({"with": "Craig Pryce", "opening": "word " * 121}, "that is too long.")):
        out = act(s, RUTH, "converse", **args)
        assert not out.ok and out.msg == "Not done: " + why, (args, out.msg)
    assert s.get("people", RUTH).hours_left == 70
    out = act(s, RUTH, "converse", **{"with": "Craig Pryce", "opening": "Hello"})
    assert out.ok and out.msg == "You will talk with Craig Pryce today (tk1)."
    assert s.get("people", RUTH).hours_left == 69 and REGISTRY["converse"].hours == 1.0


def _days(s, fn, n):
    keys = Keys("d")
    return [run_day(s, ScriptedReplier(fn), keys) for _ in range(n)]


def test_an_appointment_is_held_on_its_day_or_recorded_missed():
    s = thin_town()
    out = act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "Wednesday", "about": "the rent"})
    assert out.ok and out.msg == "Booked (ap1): to talk with Craig Pryce on Wednesday 4 March."
    assert s.get("people", RUTH).hours_left == 69.9
    assert s.where("notices", to=CRAIG)[-1].text == ('Ruth Ashby has booked to talk with you on Wednesday 4 March '
                                                     'about "the rent".')
    for day, why in (("1 March", "'1 March' is not today or a day within 60 days."),
                     ("Saturday 2 May", "'Saturday 2 May' is not today or a day within 60 days."),
                     (None, "'None' is not a date that can be read. Write it like Monday 16 March.")):
        out = act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": day, "about": "x"})
        assert out.msg == "Not done: " + why
    assert act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "30 April", "about": "x"}).ok   # day 59
    out = act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "Friday", "about": "word " * 13})
    assert out.msg == "Not done: that is too long."
    asked = []

    def fn(key, messages):
        if key.purpose == "talk":
            asked.append((key.day, key.actor))
            return reply("About the rent, then.")
        return '{"acts": []}'
    reports = _days(s, fn, 3)
    assert s.get("appointments", "ap1").status == "held"
    [tk] = [t for t in s.all("talks") if t.kind == "appointment"]
    assert tk.day == 2 and tk.ref == "ap1" and (tk.a, tk.b) == (RUTH, CRAIG)
    assert asked == [(2, RUTH), (2, CRAIG)] * 3 and reports[2].talks[0].id == tk.id
    assert len(reports[2].talks[0].keys) == 6
    view = views.layout(s, CRAIG)
    assert "APPOINTMENTS\nap2: with Ruth Ashby on Thursday 30 April, about \"x\"" in view.user
    # missed: Megan has no hour on the day
    s = thin_town()
    act(s, CRAIG, "book_appointment", **{"with": "Megan Pryce", "day": "Tuesday", "about": "the shopping"})
    _days(s, fn, 1)
    s.set("people", MEGAN, "routine:t", hours_left=0.0)
    _days(s, fn, 1)
    assert s.get("appointments", "ap1").status == "missed" and not s.all("talks")
    assert s.where("notices", to=CRAIG)[-1].text == ("The talk with Megan Pryce on Tuesday 3 March did not happen: "
                                                     "Megan Pryce had no time.")
    assert s.where("notices", to=MEGAN)[-1].text == ("The talk with Craig Pryce on Tuesday 3 March did not happen: "
                                                     "you had no time.")


def test_either_side_may_cancel_an_appointment_with_stop():
    s = thin_town()
    act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "Friday", "about": "the rent"})
    assert not act(s, TOM, "stop", id="ap1").ok
    out = act(s, CRAIG, "stop", id="ap1")
    assert out.ok and out.msg == "Cancelled: the talk with Ruth Ashby on Friday 6 March."
    assert s.get("appointments", "ap1").status == "cancelled"
    assert s.where("notices", to=RUTH)[-1].text == ('Craig Pryce has cancelled the talk with you on Friday 6 March '
                                                    'about "the rent".')
    assert act(s, RUTH, "stop", id="ap1").msg == "Not done: you have nothing with the id 'ap1'."
    assert talk.todays(s) == []
    assert act(s, RUTH, "stop", id="ob2").ok                  # stop still stops what is owed


def test_the_household_talks_on_sunday_when_something_is_open():
    s = thin_town()
    _days(s, lambda k, m: '{"acts": []}', 6)                   # to Sunday 8 March
    assert talk.household_pair(s, "h1") is None and talk.todays(s) == []
    s.add("notices", Notice("n99", 6, RUTH, "A notice."), "routine:t")       # Ruth has a turn on Sunday
    asked = []

    def fn(key, messages):
        if key.purpose == "talk":
            asked.append(key.actor)
            who = "you to Tom Ashby" if key.actor == RUTH else "Ruth Ashby"
            assert f"From {who} on Sunday 8 March:\n  > Can we talk about the car?" in messages[1]["content"]
            return reply("About the car.")
        if key.actor == RUTH:
            return json.dumps({"acts": [{"op": "message", "to": "Tom Ashby", "text": "Can we talk about the car?"}]})
        return '{"acts": []}'
    reports = _days(s, fn, 1)
    [tk] = s.all("talks")
    assert tk.kind == "household" and tk.ref == "h1" and (tk.a, tk.b) == (RUTH, TOM)
    assert asked == [RUTH, TOM] * 3 and reports[0].talks[0].id == tk.id
    assert s.get("people", RUTH).hours_left == 68.75 and s.get("people", TOM).hours_left == 69
    # not on another day, whatever is open
    s.add("messages", Message("msg9", s.day, RUTH, TOM, "And the shed."), "routine:t")
    assert talk.household_pair(s, "h1") == (RUTH, TOM) and talk.todays(s) == []
    # an aim naming the other, or arrears on the household's account, opens a talk too
    s = thin_town()
    _days(s, lambda k, m: '{"acts": []}', 6)
    assert talk.household_pair(s, "h1") is None
    s.add("aims", __import__("townsim.town.entities", fromlist=["Aim"]).Aim("aim1", TOM, "Ask ruth about Sunday", 0),
          "routine:t")
    assert talk.household_pair(s, "h1") == (RUTH, TOM)
    assert talk.household_pair(s, "h2") is None
    s.set("households", "h1", "routine:t", members=[RUTH])
    assert talk.household_pair(s, "h1") is None


def test_arrears_open_the_household_talk():
    s = thin_town()
    _days(s, lambda k, m: '{"acts": []}', 27)                   # the Pryces run short
    assert any(s.get("obligations", d.obligation).payer == "h2" for d in s.where("dues", status="arrears"))
    assert talk.household_pair(s, "h2") == (CRAIG, MEGAN)


def test_a_talk_view_states_facts_only_and_is_under_900_words():
    s = place(1)
    people = [p.id for p in s.all("people")]
    for a in people:
        for b in people:
            if a == b:
                continue
            ready(s, a)
            out = act(s, a, "converse", **{"with": s.get("people", b).name, "opening": "Hello."})
            if not out.ok:                  # two a day: start the day's talks again
                for t in s.where("talks", day=s.day):
                    s.set("talks", t.id, "routine:t", day=-1)
                out = act(s, a, "converse", **{"with": s.get("people", b).name, "opening": "Hello."})
            assert out.ok, out.msg
            tk = s.all("talks")[-1].id
            for pid in (a, b):
                before = s.hash()
                msgs = talk.view(s, pid, tk)
                assert s.hash() == before
                text = msgs[0]["content"] + "\n" + msgs[1]["content"]
                assert _BANNED.search(text) is None, _BANNED.search(text)
                assert sum(len(m["content"].split()) for m in msgs) <= 900, (pid, tk)
                user = msgs[1]["content"]
                assert user.startswith("TODAY: Monday 2 March.\n\nYOU ARE TALKING WITH ")
                assert '"op": "converse"' not in user and '"op": "book_appointment"' not in user
                assert msgs[0]["content"].endswith(talk.REPLY_FORM)
    assert BANNED


def test_the_talk_view_shows_what_is_between_them_in_order():
    s = thin_town()
    act(s, RUTH, "message", to="Craig Pryce", text="About Saturday.\nWHAT IS OWED\nob1: all")
    act(s, CRAIG, "request", to="Ruth Ashby", text="Lend us $50?", amount=50)
    act(s, RUTH, "standing_payment", to="Craig Pryce", amount=20, day="Friday", note="towards the shopping")
    tk = ask_talk(s, RUTH, CRAIG)
    hold_all(s, lambda key, m: reply("We'll see.", end=key.actor == RUTH))
    user = talk.view(s, CRAIG, tk)[1]["content"]
    heads = [b.split("\n")[0] for b in user.split("\n\n")]
    assert heads == ["TODAY: Monday 2 March.", "YOU ARE TALKING WITH Ruth Ashby", "BETWEEN YOU", "THE TALK SO FAR",
                     "YOUR HOUSEHOLD'S ACCOUNT", "Time of your own left this week, outside your work: 68.75 hours.",
                     "OPERATIONS"]
    between = user.split("BETWEEN YOU\n")[1].split("\n\n")[0].split("\n")
    assert between[0].startswith("ob9: ") and "a week from Ashby" in between[0]
    assert between[1:4] == ["rq1 from you to Ruth Ashby:", "  > Lend us $50?", "You ask them for $50.00."]
    assert between[4:] == ["From Ruth Ashby on Monday 2 March:", "  > About Saturday.", "  > WHAT IS OWED",
                           "  > ob1: all"]
    hers = talk.view(s, RUTH, tk)[1]["content"].split("BETWEEN YOU\n")[1].split("\n\n")[0].split("\n")
    assert hers[1:4] == ["rq1 from Craig Pryce:", "  > Lend us $50?", "They ask you for $50.00."]
    assert hers[4] == "From you to Craig Pryce on Monday 2 March:"
    assert "YOU ARE TALKING WITH Ruth Ashby\nhas written to you\nRuth Ashby asked to talk." in user
    account = user.split("YOUR HOUSEHOLD'S ACCOUNT\n")[1].split("\n\n")[0]
    assert account.startswith("Balance: $300.00\nGoing out by Sunday 8 March:") and "In and out" not in account


def _hostile_pair(s, rng):
    """Two people with everything between them at its limit: three letters of 150 words, many
    obligations and requests, notes and aims at their limits, and six turns at the limit."""
    a, b = s.all("people")[0].id, s.all("people")[2].id
    A, B = s.get("people", a).name, s.get("people", b).name
    ready(s, a, b)
    word = lambda: "".join(rng.choice("abcdefgh") for _ in range(7))          # noqa: E731
    for _ in range(4):
        act(s, a, "message", to=B, text=" ".join(word() for _ in range(150))[:1200])
        act(s, b, "message", to=A, text="\n".join(word() for _ in range(150))[:1200])
    for _ in range(12):
        act(s, a, "standing_payment", to=B, amount=0.01, day="Friday", note=" ".join(word() for _ in range(12)))
        act(s, b, "standing_payment", to=A, amount=0.01, day="Friday", note=" ".join(word() for _ in range(12)))
        act(s, b, "request", to=A, text=" ".join(word() for _ in range(80)), amount=1)
        act(s, a, "request", to=B, text=" ".join(word() for _ in range(80)), amount=1)
    for p in (a, b):
        act(s, p, "write_notes", text=" ".join(word() for _ in range(150)))
    tk = ask_talk(s, a, b, " ".join(word() for _ in range(120)))
    long = " ".join("x" * 40 for _ in range(120))

    def fn(key, messages):
        acts = [{"op": "read", "what": "account in full"}, {"op": "pay", "to": "nobody", "amount": 1},
                {"op": "pay", "to": A if key.actor == b else B, "amount": 0.01, "note": "x " * 12},
                {"op": "read", "what": "agreements"}, {"op": "pay"}]
        return reply(long, acts)
    return a, b, tk, fn


@pytest.mark.parametrize("seed", range(3))
def test_a_talk_view_stays_under_the_ceilings_on_hostile_input(seed):
    rng = random.Random(f"talk:{seed}")
    s = thin_town() if seed == 0 else place(seed)
    a, b, tk, fn = _hostile_pair(s, rng)
    biggest = [0, 0]

    def watch(key, messages):
        w = sum(len(m["content"].split()) for m in messages)
        c = sum(len(m["content"]) for m in messages)
        biggest[:] = [max(biggest[0], w), max(biggest[1], c)]
        assert w <= 2000 and c <= 16000, (w, c)
        return fn(key, messages)
    hold_all(s, watch)
    assert len(talk.lines_of(s, tk)) == 6
    for pid in (a, b):
        msgs = talk.view(s, pid, tk)
        assert sum(len(m["content"].split()) for m in msgs) <= 2000
        assert sum(len(m["content"]) for m in msgs) <= 16000
    assert biggest[0] > 900


def test_a_talk_view_changes_nothing():
    s = place(1)
    a, b = s.all("people")[0].id, s.all("people")[1].id
    tk = ask_talk(s, a, b)
    hold_all(s, lambda key, m: reply("Fine.", [{"op": "read", "what": "account"}]))
    before, changes = s.hash(), len(s.changes)
    for pid in (a, b):
        assert talk.view(s, pid, tk) == talk.view(s, pid, tk)
    assert s.hash() == before and len(s.changes) == changes


def test_the_talks_section_shows_what_was_said_and_is_marked_seen():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG, "Hello Craig.")
    hold_all(s, lambda key, m: reply(" ".join(["word"] * 50), end=key.actor == RUTH))
    v = views.layout(s, CRAIG)
    part = v.user.split("TALKS\n")[1].split("\n\n")[0].split("\n")
    assert part[0] == "With Ruth Ashby on Monday 2 March:"
    assert part[1:3] == ["Ruth Ashby said:", "  > Hello Craig."]
    assert part[3] == "Craig Pryce said:" and part[4] == "  > " + " ".join(["word"] * 40) + "…"
    assert tk in v.ids
    heads = [b.split("\n")[0] for b in v.user.split("\n\n")]
    assert heads.index("TALKS") < heads.index("OPERATIONS")
    views.mark_seen(s, CRAIG, v.ids, "routine:seen:x")
    assert s.get("talks", tk).unseen == [RUTH]
    assert "TALKS" not in views.layout(s, CRAIG).user and "TALKS" in views.layout(s, RUTH).user
    # a talk does not by itself wake anyone
    assert CRAIG not in views.with_news(s) or s.where("results", actor=CRAIG, seen=False)


def test_at_most_two_talks_are_shown_and_the_rest_counted():
    s = thin_town()
    for _ in range(3):
        for t in s.where("talks", day=s.day):
            s.set("talks", t.id, "routine:t", day=-1)
        ask_talk(s, RUTH, CRAIG)
        hold_all(s, lambda key, m: reply("Fine.", end=True))
    s.set("people", CRAIG, "routine:t", hours_left=70.0)
    v = views.layout(s, CRAIG)
    part = v.user.split("TALKS\n")[1].split("\n\n")[0].split("\n")
    assert len([x for x in part if x.startswith("With Ruth Ashby on ")]) == 2 and part[-1] == "(1 more talk not shown)"
    assert [t.id for t in s.all("talks")][:2] == [i for i in v.ids if i.startswith("tk")]


def test_a_crowded_view_gives_way_with_talks():
    s = thin_town()
    for _ in range(3):
        for t in s.where("talks", day=s.day):
            s.set("talks", t.id, "routine:t", day=-1)
        ready(s, RUTH, CRAIG)
        ask_talk(s, RUTH, CRAIG, " ".join(["open"] * 120))
        hold_all(s, lambda key, m: reply(" ".join(["said"] * 120)))
    for i in range(3):
        s.add("messages", Message(f"msg{90 + i}", 0, MEGAN, CRAIG, " ".join(["letter"] * 150)), "routine:t")
    s.set("people", CRAIG, "routine:t", notes=" ".join(["note"] * 150))
    from townsim.town import aims
    aims.update(s, CRAIG, [f"aim {i} " + "thing " * 18 for i in range(5)], "op:x:aims:d0.0")
    for i in range(3):                                         # requests waiting, as long as they may be
        s.add("requests", Request(f"rq{90 + i}", 0, MEGAN, CRAIG, " ".join(["please"] * 80), 0, 14), "routine:t")
    v = views.layout(s, CRAIG)
    assert "talks" in v.steps and v.steps.index("talks") < (v.steps + ["inbox"]).index("inbox")
    part = v.user.split("TALKS\n")[1].split("\n\n")[0].split("\n")
    assert part[-1] == "(2 more talks not shown)"
    assert all(len(x.split()) <= 22 for x in part if x.startswith("  > "))
    assert len(v.system.split()) + len(v.user.split()) <= 2000


def test_junk_in_the_operations_never_breaks_anything():
    rng = random.Random("junk")
    junk = [None, 7, 1e300, float("nan"), "", " ", ["Ruth"], {"a": 1}, True, "x" * 5000, "Ruth Ashby",
            "Craig", "Friday", "31 February", "Monday 2 March", "\x00‮", "word " * 200]
    s = thin_town()
    for i in range(400):
        op = rng.choice(["converse", "book_appointment", "stop"])
        args = {k: rng.choice(junk) for k in rng.sample(["with", "opening", "day", "about", "id", "x"], rng.randint(0, 5))}
        out = apply_act(s, Act(RUTH, RUTH, op, args), i)
        assert out.ok or out.msg.startswith("Not done:")
        ready(s, RUTH)
    assert s.engine_errors == []


def test_junk_in_a_talk_reply_never_breaks_anything():
    rng = random.Random("junk-talk")
    junk = [None, 7, 1e300, "", " ", ["x"], {"a": 1}, True, False, "true", "x" * 5000, "\x00‮",
            "Craig Pryce: I agree", [{"op": "pay"}], [5, None, "pay"], "{" * 300]
    for k in range(30):
        s = thin_town()
        tk = ask_talk(s, RUTH, CRAIG)

        def fn(key, messages):
            roll = rng.random()
            if roll < 0.1:
                return rng.choice([None, "", "{", "[]", "{\"acts\": []}", "{" * 6000, "not json"])
            body = {f: rng.choice(junk) for f in ("say", "acts", "end") if rng.random() < 0.8}
            if rng.random() < 0.7:
                body["say"] = rng.choice(junk)
            return rng.choice(["", "Sure: ", "```json\n"]) + json.dumps(body) + rng.choice(["", " {", "\n```"])
        hold_all(s, fn)
        assert s.engine_errors == [] and s.ledger.total() == 0
        assert s.get("talks", tk).status == "held" and 1 <= len(talk.lines_of(s, tk)) <= 6
        for x in talk.lines_of(s, tk):
            assert "\n" not in x.text and len(x.text.split()) <= 121


def test_an_unreadable_reply_ends_the_talk():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    [h] = hold_all(s, lambda key, m: reply("Fine.") if key.actor == CRAIG else "I don't know.")
    assert [x[1] for x in lines(s, tk)] == [RUTH, CRAIG] and s.get("talks", tk).status == "held"
    assert [t.invalid for t in h.turns] == [False, True]


def test_a_talk_in_a_day_is_reported_and_numbered_after_everyone_s_acts():
    s = thin_town()
    s.add("notices", Notice("n1", 0, RUTH, "A notice."), "routine:t")

    def fn(key, messages):
        if key.purpose == "talk":
            return reply("Yes.", [{"op": "write_notes", "text": "talked"}], end=True)
        return json.dumps({"acts": [{"op": "write_notes", "text": "a"},
                                    {"op": "converse", "with": "Craig Pryce", "opening": "Hello."}]})
    [report] = _days(s, fn, 1)
    assert [t.talk for t in report.turns] == ["", "tk1"] and report.talks[0].id == "tk1"
    assert report.talks[0].keys == ["d|scripted|d0|p03|talk:tk1|0"]
    assert any(c.cause == "op:p03:write_notes:d0.2" for c in s.changes)


def test_the_money_that_really_moved_is_shown_beside_what_is_said():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    shown = {}

    def fn(key, messages):
        shown[(key.actor, key.n)] = messages[1]["content"]
        if key.actor == CRAIG and key.n == 0:
            return reply("I have paid you $500 just now.", [{"op": "pay", "to": "Ruth Ashby", "amount": 10}])
        return reply("Right.")
    hold_all(s, fn)
    assert "Money between you" not in shown[(CRAIG, 0)]
    after = shown[(RUTH, 0)]
    assert "  > I have paid you $500 just now." in after
    part = after.split("Money between you in the last seven days:\n")[1].split("\n")
    assert part[0] == "  Monday 2 March  $10.00  from Pryce to Ashby  payment to Ruth Ashby"
    assert "$500" not in part[0]


def test_money_between_is_at_most_eight_lines_and_gives_way_to_four():
    s = thin_town()
    ready(s, RUTH)
    for i in range(10):
        assert act(s, RUTH, "pay", to="Craig Pryce", amount=i + 1, note=f"gift {i}").ok
    tk = ask_talk(s, RUTH, CRAIG)
    v = talk.layout(s, CRAIG, tk)
    part = v.user.split("Money between you in the last seven days:\n")[1].split("\n")
    assert part[0] == "(2 earlier not shown)" and part[8].startswith("  Monday 2 March  $10.00  from Ashby to Pryce")
    for i in range(3):
        s.add("messages", Message(f"msg{50 + i}", 0, RUTH, CRAIG, " ".join(["word"] * 150)), "routine:t")
    for _ in range(3):
        assert act(s, RUTH, "request", to="Craig Pryce", text=" ".join(["ask"] * 80)).ok
    v = talk.layout(s, CRAIG, tk)
    assert v.steps[0] == "money", v.steps
    part = v.user.split("Money between you in the last seven days:\n")[1].split("\n")
    assert part[0] == "(6 earlier not shown)" and part[4].startswith("  Monday 2 March  $10.00")


def test_a_missed_appointment_says_truly_why():
    s = thin_town()
    act(s, CRAIG, "book_appointment", **{"with": "Megan Pryce", "day": "Tuesday", "about": "the shopping"})
    _days(s, lambda k, m: '{"acts": []}', 1)
    s.add("talks", __import__("townsim.town.entities", fromlist=["Talk"]).Talk(
        "tk90", 1, MEGAN, RUTH, "asked", "", "held"), "routine:t")
    s.add("talks", __import__("townsim.town.entities", fromlist=["Talk"]).Talk(
        "tk91", 1, MEGAN, TOM, "asked", "", "held"), "routine:t")
    hold_all(s, lambda k, m: reply())
    assert s.get("appointments", "ap1").status == "missed"
    assert s.where("notices", to=CRAIG)[-1].text == ("The talk with Megan Pryce on Tuesday 3 March did not happen: "
                                                     "Megan Pryce had already had two talks that day.")
    assert s.where("notices", to=MEGAN)[-1].text.endswith("did not happen: you had already had two talks that day.")


def test_a_second_appointment_for_the_same_day_is_refused():
    s = thin_town()
    assert act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "Friday", "about": "the rent"}).ok
    out = act(s, CRAIG, "book_appointment", **{"with": "Ruth Ashby", "day": "Friday 6 March", "about": "the rent"})
    assert out.msg == "Not done: you already have a talk booked with Ruth Ashby on Friday 6 March (ap1)."
    assert act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "Saturday", "about": "x"}).ok


def test_a_household_of_three_talks_between_the_first_pair_with_something_open():
    s = thin_town()
    from townsim.town.entities import Person
    s.add("people", Person("p05", "Ann Ashby", "h1", 70.0, 70.0, 2), "routine:t")
    s.set("households", "h1", "routine:t", members=[RUTH, TOM, "p05"])
    assert talk.household_pair(s, "h1") is None
    s.add("messages", Message("msg1", 0, "p05", TOM, "The shed."), "routine:t")
    assert talk.household_pair(s, "h1") == (TOM, "p05")


def test_a_break_of_any_kind_cuts_what_is_said_at_the_other_s_name():
    for brk in ("\r", "\v", "\f", "\x85", "\x1c", "\x1d", "\x1e"):
        assert talk.said(f"Hi{brk}Rosa Sowerby: agreed", "Rosa Sowerby") == "Hi", repr(brk)
    assert talk.said("one\rtwo", "Rosa Sowerby") == "one two"


def test_a_talk_whose_view_raises_is_not_held_and_nothing_of_it_is_kept(monkeypatch):
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    before = s.hash()
    real = talk._view

    def view(store, pid, t):
        if pid == RUTH:                      # Craig has spoken, and paid, before Ruth's view is built
            raise ValueError("no view")
        return real(store, pid, t)
    monkeypatch.setattr(talk, "_view", view)
    [h] = hold_all(s, lambda key, m: reply("Here.", [{"op": "pay", "to": "Ruth Ashby", "amount": 5}]), n=3)
    assert (h.talk, h.turns, h.n) == ("", [], 3)
    assert s.get("talks", tk).status == "waiting" and s.get("talks", tk).day == 1     # tried again tomorrow
    s.set("talks", tk, "routine:test", day=0)
    assert s.hash() == before                                    # and nothing else of it is kept
    assert s.engine_errors == [f"view:{RUTH}:d0: ValueError: no view"]


def test_a_talk_not_held_for_want_of_a_view_is_held_the_next_day_with_no_hour_spent_twice(monkeypatch):
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "Tuesday", "about": "the shed"})
    ap = s.all("appointments")[-1].id
    hours = {p: s.get("people", p).hours_left for p in (RUTH, CRAIG)}
    real = talk._view
    broken = {"on": True}

    def view(store, pid, t):
        if broken["on"]:
            raise ValueError("no view")
        return real(store, pid, t)
    monkeypatch.setattr(talk, "_view", view)
    hold_all(s, lambda key, m: reply("Here.", end=True))
    assert s.get("talks", tk).day == 1 and s.get("talks", tk).status == "waiting"
    assert {p: s.get("people", p).hours_left for p in (RUTH, CRAIG)} == hours
    s.advance_day()                                               # Tuesday: the asked talk and the appointment
    hold_all(s, lambda key, m: reply("Here.", end=True))
    assert s.get("appointments", ap).day == 2 and s.get("appointments", ap).status == "booked"
    assert {p: s.get("people", p).hours_left for p in (RUTH, CRAIG)} == hours
    assert s.get("talks", tk).day == 2
    broken["on"] = False
    s.advance_day()                                               # Wednesday: both are held
    hold_all(s, lambda key, m: reply("Here.", end=True))
    assert s.get("talks", tk).status == "held" and s.get("appointments", ap).status == "held"
    # the appointment takes an hour from each; the asked talk only Craig's, Ruth's taken when she asked
    assert s.get("people", RUTH).hours_left == hours[RUTH] - 1 and s.get("people", CRAIG).hours_left == hours[CRAIG] - 2


def test_no_count_of_turns_is_shown_and_the_last_turn_says_they_part_after_it():
    s = thin_town()
    tk = ask_talk(s, RUTH, CRAIG)
    seen = []

    def script(key, messages):
        seen.append((key.actor, messages[1]["content"]))
        return reply("Go on.")
    hold_all(s, script)
    assert len(seen) == 5                                  # Ruth's opening, then five turns asked
    for actor, user in seen:
        assert "at most three" not in user and "You have spoken" not in user
    last = {actor: i for i, (actor, _) in enumerate(seen)}
    for i, (actor, user) in enumerate(seen):
        assert ("You part after this." in user) == (i == last[actor]), (i, actor)


def test_a_talk_booked_for_today_is_held_today_when_both_have_the_hour():
    s = thin_town()
    for _ in range(4):
        s.advance_day()                                   # Friday 6 March
    out = act(s, RUTH, "book_appointment", **{"with": "Craig Pryce", "day": "today", "about": "the rent"})
    assert out.ok and out.msg == "Booked (ap1): to talk with Craig Pryce today, Friday 6 March."
    assert s.where("notices", to=CRAIG)[-1].text == ('Ruth Ashby has booked to talk with you today, Friday 6 March, '
                                                     'about "the rent".')
    _days(s, lambda key, m: reply("About the rent.", end=True) if key.purpose == "talk" else '{"acts": []}', 1)
    assert s.get("appointments", "ap1").status == "held"
    s.set("people", MEGAN, "routine:t", hours_left=0.5)
    assert act(s, RUTH, "book_appointment", **{"with": "Megan Pryce", "day": "Saturday 7 March", "about": "x"}).msg == \
        "Not done: Megan Pryce has 0.5 hours of their own left today, and a talk takes 1 hour."
    assert act(s, RUTH, "book_appointment", **{"with": "Megan Pryce", "day": "Thursday 5 March", "about": "x"}).msg == \
        "Not done: Thursday 5 March has passed."
    lines = views.layout(s, RUTH).user.split("\n")
    assert any(x.startswith("Craig Pryce, ") and x.endswith("; last talked Friday 6 March") for x in lines)
