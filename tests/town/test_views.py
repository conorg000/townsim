import re

from townsim.town.entities import Message, Notice, Request, Result
from townsim.town.thin import thin_town
from townsim.town.views import mark_seen, person_view, shown_ids

_WORDS = re.compile(r"\b(test|model|game|consider)\b|you may|you should|you can|\bsimulat", re.IGNORECASE)


def test_a_view_changes_nothing_and_says_nothing_about_what_to_do():
    s = thin_town(); before = s.hash(); msgs = person_view(s, "p03")
    assert s.hash() == before and msgs[0]["role"] == "system" and "Craig Pryce" in msgs[0]["content"]
    assert msgs[0]["content"].startswith("You are Craig Pryce, 40.\nYou live in Wenlow; your household is the "
                                         "Pryce household.\nThe house is rented from Wenlow Lettings for $185.00 "
                                         "a week, paid on Mondays.\nMegan Pryce, lives with you.\n")
    assert 'Reply with JSON only, in this form:\n{"thoughts": ' in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    text = msgs[1]["content"]
    assert text.startswith("TODAY: Monday 2 March.") and '{"op": "pay"' in text
    for content in (text, msgs[0]["content"]):
        assert _WORDS.search(content) is None, _WORDS.search(content)


def test_sections_come_in_order_and_empty_ones_are_left_out():
    s = thin_town()
    text = person_view(s, "p03")[1]["content"]
    assert "LETTERS AND NOTICES" not in text and "WAITING FOR YOUR ANSWER" not in text and "YOUR NOTES" not in text
    s.add("notices", Notice("n1", 0, "p03", "A notice."), "routine:t")
    s.add("messages", Message("msg1", 0, "p01", "p03", "Hello Craig."), "routine:t")
    s.add("messages", Message("msg2", 0, "p01", "p04", "Not for Craig."), "routine:t")
    s.add("requests", Request("rq1", 0, "p01", "p03", "Lend me a ladder", 0, 14), "routine:t")
    s.add("requests", Request("rq2", 0, "p02", "p03", "Could you spare some?", 5000, 14), "routine:t")
    s.set("people", "p03", "routine:t", notes="Ring the landlord.", hours_left=11.75)
    s.add("results", Result("res2", 0, "p03", "read", "line one\nline two", True), "routine:t")
    s.add("results", Result("res1", 0, "p03", "pay", "Not done: no.", False), "routine:t")
    s.add("results", Result("res3", 0, "p04", "pay", "Not Craig's.", False), "routine:t")
    text = person_view(s, "p03")[1]["content"]
    assert "WHAT CAME OF YOUR LAST ACTS\npay: Not done: no.\nread: line one\n  line two\n\n" in text
    assert "Not Craig's." not in text
    heads = ["TODAY: Monday 2 March.", "WHAT CAME OF YOUR LAST ACTS", "YOUR HOUSEHOLD'S ACCOUNT",
             "LETTERS AND NOTICES", "WAITING FOR YOUR ANSWER", "PEOPLE YOU KNOW", "YOUR NOTES",
             "You have 11.75 hours left this week.", "OPERATIONS"]
    lines = text.split("\n")
    assert [lines.index(h) for h in heads] == sorted(lines.index(h) for h in heads)
    assert "LETTERS AND NOTICES\nA notice.\nFrom Ruth Ashby: Hello Craig.\n\n" in text and "Not for Craig." not in text
    assert "rq1 from Ruth Ashby: Lend me a ladder\nrq2 from Tom Ashby: Could you spare some? They ask you for $50.00." in text
    assert "YOUR NOTES\nRing the landlord." in text and "Balance: $300.00" in text
    assert shown_ids(s, "p03") == ["res1", "res2", "n1", "msg1", "rq1", "rq2"]
    mark_seen(s, "p03", shown_ids(s, "p03"), "routine:seen:p03")
    assert [r.seen for r in s.all("results")] == [True, True, False]


def test_whole_hours_are_shown_without_a_fraction():
    assert "You have 70 hours left this week." in person_view(thin_town(), "p01")[1]["content"]


def test_mark_seen_marks_only_what_it_is_given():
    s = thin_town()
    s.add("notices", Notice("n1", 0, "p03", "Shown."), "routine:t")
    ids = shown_ids(s, "p03")
    s.add("messages", Message("msg1", 0, "p01", "p03", "Arrived later."), "routine:t")
    mark_seen(s, "p03", ids, "routine:seen:p03")
    assert s.get("notices", "n1").seen and not s.get("messages", "msg1").seen
    assert s.changes[-1].cause == "routine:seen:p03"
    assert shown_ids(s, "p03") == ["msg1"]


def test_when_the_news_is_too_long_the_rest_waits_for_another_day():
    s = thin_town()
    for i in range(6):
        s.add("messages", Message(f"msg{i + 1}", 0, "p01", "p03", f"letter {i + 1} " + "word " * 298), "routine:t")
    unseen = [f"msg{i + 1}" for i in range(6)]
    text = person_view(s, "p03")[1]["content"]
    shown = shown_ids(s, "p03")
    assert len(text.split()) <= 1200 and 0 < len(shown) < 6 and shown == unseen[:len(shown)]
    assert f"({6 - len(shown)} more: {6 - len(shown)} letters, 0 notices)" in text.split("\n")
    assert f"letter {len(shown)} " in text and f"letter {len(shown) + 1} " not in text
    mark_seen(s, "p03", shown, "routine:seen:p03")
    s.advance_day()
    later = shown_ids(s, "p03")
    assert later and later == unseen[len(shown):len(shown) + len(later)]
    assert f"letter {len(shown) + 1} " in person_view(s, "p03")[1]["content"]


def test_a_long_statement_loses_its_oldest_lines_to_stay_within_1200_words():
    s = thin_town()
    for i in range(25):
        s.transfer("outside:employers", "h2", 100, f"line {i} " + "word " * 60, "routine:t")
    text = person_view(s, "p03")[1]["content"]
    assert len(text.split()) <= 1200 and "earlier lines not shown)" in text
    assert "line 24 " in text and "line 0 " not in text


# --- the generated hamlet -------------------------------------------------------------

import functools  # noqa: E402

import pytest  # noqa: E402
from town.helpers import tie  # noqa: E402

from townsim.town import acts, aims, facts, firms, gen, names, views  # noqa: E402
from townsim.town import obligations as O  # noqa: E402
from townsim.town.acts import Act, apply_act, knows, tie_rank  # noqa: E402
from townsim.town.loop import routine  # noqa: E402
from townsim.town.clock import date_text  # noqa: E402
from townsim.town.obligations import money  # noqa: E402

FAMILIES = ["a", "b", "c", "d"]
BANNED = ("you should", "you may", "you can", "you could", "you might", "you need to", "consider", "try to",
          "it would be", "remember to", "make sure", "be sure to", "don't forget", "it is wise", "ought to",
          "simulat", "language model", "experiment", "scenario", "role", "game")
_BANNED = re.compile("|".join(r"\b" + re.escape(p) + ("" if p == "simulat" else r"\b") for p in BANNED), re.I)


@functools.lru_cache(maxsize=None)
def _place(seed):
    return gen.generate(seed, FAMILIES)


def place(seed):
    """A fresh copy of a generated place, so a test may change it."""
    import copy
    return copy.deepcopy(_place(seed))


def run_routine(s, days):
    for _ in range(days):
        routine(s)
        firms.deliver(s); firms.shop_today(s); firms.close_week(s)
        s.advance_day()


def words(msgs):
    return sum(len(m["content"].split()) for m in msgs)


def section(text, head):
    """The lines under `head` in a user message, up to the next blank line; None if absent."""
    parts = text.split("\n\n")
    for p in parts:
        lines = p.split("\n")
        if lines[0] == head:
            return lines[1:]
    return None


HEADS = ("WHAT CAME OF YOUR LAST ACTS", "YOUR HOUSEHOLD'S ACCOUNT", "THE BANK'S BOOK", "YOUR BUSINESS",
         "WHAT IS OWED",
         "LETTERS AND NOTICES", "WAITING FOR YOUR ANSWER", "APPOINTMENTS", "PEOPLE YOU KNOW", "HOW THINGS WORK",
         "ON YOUR MIND", "YOUR NOTES", "OPERATIONS")


def heads(text):
    return [p.split("\n")[0] for p in text.split("\n\n") if p.split("\n")[0] in HEADS]


def send(s, sender, to, text):
    out = apply_act(s, Act(sender, sender, "message", {"to": s.get("people", to).name, "text": text}), 0)
    assert out.ok, out.msg
    return s.all("messages")[-1].id


def ask(s, sender, to, text):
    out = apply_act(s, Act(sender, sender, "request", {"to": s.get("people", to).name, "text": text}), 0)
    assert out.ok, out.msg
    return s.all("requests")[-1].id


def tied(s, pid, kind):
    return [t.other for t in s.all("ties") if t.person == pid and t.kind == kind]


def someone_with_a_partner_a_friend_and_a_landlord(s):
    """A person, their partner, a friend who is not their landlord, their landlord who is not
    their friend, and two people they do not know."""
    for p in s.all("people"):
        partner, friend, landlord = tied(s, p.id, "partner"), tied(s, p.id, "friend"), tied(s, p.id, "landlord")
        friend = [f for f in friend if tie_rank(s, p.id, f) == 2]
        landlord = [lord for lord in landlord if tie_rank(s, p.id, lord) == 4]
        strangers = [q.id for q in s.all("people") if q.id != p.id and q.id not in knows(s, p.id)]
        if partner and friend and landlord and len(strangers) >= 2:
            return p.id, partner[0], friend[0], landlord[0], strangers[0], strangers[1]
    raise AssertionError("no such person")


def test_the_inbox_shows_the_three_most_pressing_and_counts_the_rest():
    s = place(1)
    pid, partner, friend, landlord, stranger, stranger2 = someone_with_a_partner_a_friend_and_a_landlord(s)
    send(s, landlord, pid, "about the gutter"); send(s, stranger, pid, "hello"); send(s, friend, pid, "Saturday?")
    send(s, partner, pid, "the bill"); send(s, stranger2, pid, "a question")
    shown, more, line = views.inbox(s, pid)
    assert [x.sender for x in shown] == [partner, friend, landlord] and more == 2
    assert line == "(2 more: 2 letters, 0 notices)"
    text = views.person_view(s, pid)[1]["content"]
    letters = section(text, "LETTERS AND NOTICES")
    assert letters[0] == f"From {s.get('people', partner).name}: the bill" and letters[-1] == line
    assert "hello" not in text and "a question" not in text


def test_a_notice_ranks_with_the_household_and_one_is_counted_as_a_notice():
    s = place(1)
    pid, partner, friend, landlord, stranger, _ = someone_with_a_partner_a_friend_and_a_landlord(s)
    send(s, stranger, pid, "hello"); send(s, friend, pid, "Saturday?"); send(s, landlord, pid, "gutter")
    s.add("notices", Notice(s.new_id("n", "routine:t"), 0, pid, "A notice."), "routine:t")
    shown, more, line = views.inbox(s, pid)
    assert [getattr(x, "sender", "notice") for x in shown] == ["notice", friend, landlord]
    assert line == "(1 more: 1 letter, 0 notices)"
    send(s, partner, pid, "the bill")
    assert views.inbox(s, pid)[2] == "(2 more: 2 letters, 0 notices)"
    s.add("notices", Notice(s.new_id("n", "routine:t"), 0, pid, "Another."), "routine:t")
    s.add("notices", Notice(s.new_id("n", "routine:t"), 0, pid, "And another."), "routine:t")
    assert views.inbox(s, pid)[2] == "(4 more: 3 letters, 1 notice)"


def test_only_what_was_shown_in_full_is_marked_seen():
    s = place(1)
    pid, partner, friend, landlord, stranger, stranger2 = someone_with_a_partner_a_friend_and_a_landlord(s)
    sent = [send(s, who, pid, f"letter {i}") for i, who in enumerate((stranger, stranger2, friend, landlord, partner))]
    asked = [ask(s, who, pid, f"request {i}") for i, who in enumerate((stranger, stranger2, friend, landlord, partner))]
    msgs, ids = views.view(s, pid)
    assert ids == views.shown_ids(s, pid)
    assert ids == [sent[4], sent[2], sent[3], asked[4], asked[2], asked[3]]
    waiting = section(msgs[1]["content"], "WAITING FOR YOUR ANSWER")
    assert waiting[-1] == "(2 more requests waiting)" and len(waiting) == 4
    views.mark_seen(s, pid, ids, f"routine:seen:{pid}")
    assert [s.get("messages", m).seen for m in sent] == [False, False, True, True, True]
    assert [s.get("requests", r).seen for r in asked] == [False, False, True, True, True]
    assert views.has_news(s, pid)                                 # what was only counted still wakes them
    assert views.inbox(s, pid)[0] == [s.get("messages", sent[0]), s.get("messages", sent[1])]
    assert views.shown_ids(s, pid) == [sent[0], sent[1], asked[0], asked[1], asked[4]]   # what has not been seen first


def test_a_request_shows_its_summary():
    s = thin_town()
    s.add("requests", Request("rq1", 0, "p01", "p03", "Work for me on Saturdays", 0, 14,
                              summary="Ruth Ashby asks you to work 4 hours on Saturdays for $48.00."), "routine:t")
    waiting = section(person_view(s, "p03")[1]["content"], "WAITING FOR YOUR ANSWER")
    assert waiting == ["rq1 from Ruth Ashby: Work for me on Saturdays",
                       "  Ruth Ashby asks you to work 4 hours on Saturdays for $48.00."]


def _unused_first_name(s):
    taken = {p.name.split()[0] for p in s.all("people")}
    return next(n for n in names.FIRST_NAMES if n not in taken)


def test_a_name_the_reader_does_not_know_is_noted_under_the_letter():
    s = place(1)
    pid, partner, friend, landlord, stranger, _ = someone_with_a_partner_a_friend_and_a_landlord(s)
    invented = f"{_unused_first_name(s)} Holroyd"
    reader, sender = s.get("people", pid), s.get("people", friend)
    stranger_name = s.get("people", stranger).name
    text = (f"Dear {reader.name}, {invented} says {s.get('people', partner).name} and {stranger_name} "
            f"owe money. Ask {invented} yourself. Yours, {sender.name}")
    mid = send(s, friend, pid, text)
    expected = [invented] + ([stranger_name] if stranger_name.split()[0] in names.FIRST_NAMES else [])
    assert s.get("messages", mid).unknown == expected
    letters = section(person_view(s, pid)[1]["content"], "LETTERS AND NOTICES")
    assert letters[0] == f"From {sender.name}: {text}"
    assert letters[1:1 + len(expected)] == [f"({n} is not someone you know.)" for n in expected]


def test_a_name_known_to_the_reader_or_not_a_first_name_is_not_noted():
    s = thin_town()
    send(s, "p01", "p03", "Megan Pryce and Tom Ashby send their love. Wenlow Lettings wrote. Edith Marsh too.")
    assert s.all("messages")[-1].unknown == []                 # Edith is not a first name the place uses
    send(s, "p01", "p03", "Ask Ada Lovell about Ruth Ashby.")
    assert s.all("messages")[-1].unknown == ["Ada Lovell"]


def _who_lines_without_the_persons_own_words(s, pid, text):
    p = s.get("people", pid)
    own = list(p.beliefs) + list(p.worries)
    text = text.rstrip()
    voice = text.split("\n")[-1]                           # the voice line, said to them
    return [line for line in text.split("\n") if line != voice and not any(o and o in line for o in own)]


@pytest.mark.parametrize("seed", (1, 2, 3, 4, 5))
def test_every_view_states_facts_only(seed):
    s = place(seed)
    for when in (0, 14):
        if when:
            run_routine(s, 14)
        for p in s.all("people"):
            system, user = (m["content"] for m in views.person_view(s, p.id))
            assert system.endswith(views.REPLY_FORM)
            text = "\n".join(_who_lines_without_the_persons_own_words(s, p.id, system.removesuffix(views.REPLY_FORM))
                             + [user])
            found = _BANNED.search(text)
            assert found is None, (p.name, found.group(0), text[max(0, found.start() - 80):found.end() + 80])


@pytest.mark.parametrize("seed", (1, 2, 3, 4, 5))
def test_every_view_is_about_1200_words_on_day_0_and_after_two_weeks(seed):
    s = place(seed)
    assert all(words(views.person_view(s, p.id)) <= 1200 for p in s.all("people"))
    run_routine(s, 14)
    assert s.all("tills")
    assert all(words(views.person_view(s, p.id)) <= 1300 for p in s.all("people"))


@pytest.mark.parametrize("seed", (1, 2, 3))
def test_after_three_weeks_with_nobody_acting_every_view_is_at_most_1300_words(seed):
    s = place(seed)
    run_routine(s, 21)
    counts = {p.name: words(views.person_view(s, p.id)) for p in s.all("people")}
    assert max(counts.values()) <= 1300, counts


def test_the_system_message_is_who_they_are_then_the_reply_form():
    s = place(1)
    for p in s.all("people"):
        system = views.person_view(s, p.id)[0]["content"]
        who = facts.who(s, p.id)
        assert system == who + "\n\n" + views.REPLY_FORM
        assert who.startswith(f"You are {p.name}, {p.age}.") and len(who.split()) <= 300
        assert who.split("\n")[-1].startswith("You write ") if p.voice.startswith("Writes ") else p.voice in who
        for b in p.beliefs + p.worries:
            assert b in who


def test_who_says_where_they_live_how_the_house_is_held_and_what_they_earn():
    s = place(1)
    for p in s.all("people"):
        who = facts.who(s, p.id)
        h = s.get("households", p.household)
        assert h.street in who
        for m in h.members:
            if m != p.id:
                assert s.get("people", m).name in who
        rent = [o for o in s.where("obligations", payer=h.id) if o.kind == "rent"]
        if rent:
            assert f"rented from {acts.name_of_who(s, h.landlord)} for {money(rent[0].cents)} a week" in who
        for j in s.where("jobs", person=p.id):
            assert f"{j.hours:g} hours a week" in who and money(j.weekly) in who


@pytest.mark.parametrize("seed", (1, 2))
def test_every_dollar_figure_in_who_is_in_their_rows(seed):
    s = place(seed)
    for p in s.all("people")[:5] + [q for q in s.all("people") if q.kind in ("owner", "banker", "landlord")]:
        accounts = acts.accounts_of(s, p.id)
        rows = {money(o.cents) for a in accounts for o in s.where("obligations", payer=a) + s.where("obligations", payee=a)}
        rows |= {money(j.weekly) for o in accounts for j in s.where("jobs", org=o)}
        rows |= {money(j.weekly) for j in s.where("jobs", person=p.id)} | {money(s.balance(a)) for a in accounts}
        for figure in re.findall(r"\$[\d,]+\.\d\d", "\n".join(_who_lines_without_the_persons_own_words(
                s, p.id, facts.who(s, p.id)))):
            assert figure in rows, (p.name, figure)


def _kind(s, kind):
    return [p for p in s.all("people") if p.kind == kind]


def _owes(s, pid):
    """Whether `pid` has anything for `WHAT IS OWED`: by its rule, from the rows."""
    for a in acts.accounts_of(s, pid):
        for o in s.where("obligations", payer=a) + s.where("obligations", payee=a):
            if (o.kind in ("loan", "agreement", "standing") and o.status in ("active", "breached")) or \
                    any(d.status in ("arrears", "defaulted") for d in s.where("dues", obligation=o.id)):
                return True
    return False


def test_each_position_sees_the_sections_it_implies_and_no_others():
    s = place(1)
    owner, staff, banker, landlord, retired = (_kind(s, k)[0] for k in ("owner", "staff", "banker", "landlord", "retired"))
    expect = {owner.id: ["YOUR HOUSEHOLD'S ACCOUNT", "YOUR BUSINESS", "WHAT IS OWED", "PEOPLE YOU KNOW",
                         "HOW THINGS WORK", "OPERATIONS"],
              staff.id: ["YOUR HOUSEHOLD'S ACCOUNT", "WHAT IS OWED", "PEOPLE YOU KNOW", "OPERATIONS"],
              retired.id: ["YOUR HOUSEHOLD'S ACCOUNT", "WHAT IS OWED", "PEOPLE YOU KNOW", "OPERATIONS"],
              banker.id: ["YOUR HOUSEHOLD'S ACCOUNT", "THE BANK'S BOOK", "WHAT IS OWED", "PEOPLE YOU KNOW",
                          "HOW THINGS WORK", "OPERATIONS"],
              landlord.id: ["YOUR HOUSEHOLD'S ACCOUNT", "WHAT IS OWED", "PEOPLE YOU KNOW", "HOW THINGS WORK",
                            "OPERATIONS"]}
    for pid, want in expect.items():
        want = [h for h in want if h != "WHAT IS OWED" or _owes(s, pid)]
        assert heads(person_view(s, pid)[1]["content"]) == want, s.get("people", pid).kind
    aims.update(s, staff.id, ["Ask for more hours"], "op:x:aims:d0.0")
    s.set("people", staff.id, "routine:t", notes="Saturday shift.")
    send(s, owner.id, staff.id, "Can you do Saturday?")
    assert heads(person_view(s, staff.id)[1]["content"]) == [h for h in (
        "YOUR HOUSEHOLD'S ACCOUNT", "WHAT IS OWED", "LETTERS AND NOTICES", "PEOPLE YOU KNOW", "ON YOUR MIND",
        "YOUR NOTES", "OPERATIONS") if h != "WHAT IS OWED" or _owes(s, staff.id)]
    assert section(person_view(s, staff.id)[1]["content"], "ON YOUR MIND") == ["for 0 days: Ask for more hours"]


def test_the_business_is_shown_as_a_till_prints_it():
    s = place(1)
    owner = _kind(s, "owner")[0]
    org = next(o for o in s.all("orgs") if o.owner == owner.id)
    f = s.where("firms", org=org.id)[0]
    text = facts.business(s, owner.id)
    lines = text.split("\n")
    assert lines[:3] == [org.name, f"Account: {money(s.balance(org.id))}", f"Price: {money(f.price)} a basket"]
    assert lines[3] == f"On the shelf: {sum(f.stock_units)} units ({sum(f.stock_units)} from this week, 0 from last week)"
    sup = s.get("orgs", s.get("suppliers", f.last_supplier).org).name
    assert lines[4] == f"Nothing ordered; last week's order was {f.last_units} units from {sup}"
    staff = [f"  {s.get('people', j.person).name}, {j.hours:g} hours, {money(j.weekly)} a week"
             for j in s.where("jobs", org=org.id)]
    assert lines[5:6 + len(staff)] == ["Staff:"] + staff
    assert "Last week:" not in text and f"Going out by {date_text(6)}:" in text
    firms.place_order(s, f.id, f.last_supplier, 120, "op:x:order:d0.0")
    assert facts.business(s, owner.id).split("\n")[4] == f"On order for Monday: 120 units from {sup}"
    run_routine(s, 14)
    text = facts.business(s, owner.id)
    [last, before] = sorted((t for t in s.all("tills") if t.firm == f.id), key=lambda t: -t.week)[:2]
    assert (f"Last week: sold {last.sold}, turned away {last.turned_stock} for want of stock and "
            f"{last.turned_capacity} for want of staff, threw away {last.spoiled}, took {money(last.takings)}") in text
    assert f"The week before: sold {before.sold}," in text


def _no_figures(lines):
    return [re.sub(r"\$[\d,]+\.\d\d|\d+", "#", line) for line in lines]


def test_owners_see_the_same_knowhow_word_for_word():
    s = place(1)
    seen = [section(person_view(s, p.id)[1]["content"], "HOW THINGS WORK") for p in _kind(s, "owner")]
    assert 4 <= len(seen[0]) <= 6 and all(_no_figures(x) == _no_figures(seen[0]) for x in seen)
    for p, lines in zip(_kind(s, "owner"), seen):
        f = s.where("firms", org=next(o.id for o in s.all("orgs") if o.owner == p.id))[0]
        assert any(str(f.units_per_staff) in line for line in lines)
    banker = section(person_view(s, _kind(s, "banker")[0].id)[1]["content"], "HOW THINGS WORK")
    landlord = section(person_view(s, _kind(s, "landlord")[0].id)[1]["content"], "HOW THINGS WORK")
    assert 3 <= len(banker) <= 4 and 2 <= len(landlord) <= 3
    assert banker == facts.KNOWHOW["banker"] and landlord == facts.KNOWHOW["landlord"]
    assert set(facts.KNOWHOW) == {"owner", "banker", "landlord"}
    for lines in facts.KNOWHOW.values():
        for line in lines:
            assert _BANNED.search(line) is None and " if you " not in f" {line.lower()} "


def test_people_you_know_are_closest_first_at_most_twelve():
    s = place(1)
    for p in s.all("people"):
        rows = section(person_view(s, p.id)[1]["content"], "PEOPLE YOU KNOW")
        known = knows(s, p.id)
        assert len(rows) == min(12, len(known))
        order = sorted(known, key=lambda o: (tie_rank(s, p.id, o), acts.id_key(o)))[:12]
        assert [r.split(",")[0] for r in rows] == [s.get("people", o).name for o in order]
        for r, o in zip(rows, order):
            if tie_rank(s, p.id, o) == 0 and not tied(s, p.id, "partner") + tied(s, p.id, "child") \
                    + tied(s, p.id, "parent") + tied(s, p.id, "sibling"):
                assert r.endswith("lives with you")
    clara = next(p for p in s.all("people") if tied(s, p.id, "partner") and tied(s, p.id, "child"))
    rows = section(person_view(s, clara.id)[1]["content"], "PEOPLE YOU KNOW")
    assert f"{s.get('people', tied(s, clara.id, 'partner')[0]).name}, your partner" in rows
    assert f"{s.get('people', tied(s, clara.id, 'child')[0]).name}, your child" in rows


def test_a_view_changes_nothing():
    for seed in (1, 2):
        s = place(seed)
        before, n = s.hash(), len(s.changes)
        for p in s.all("people"):
            views.view(s, p.id); views.shown_ids(s, p.id); facts.who(s, p.id)
        assert s.hash() == before and len(s.changes) == n


def _result(s, pid, op, **args):
    """Do an act for `pid` and keep its result as the loop does."""
    out = apply_act(s, Act(pid, pid, op, args), len(s.all("results")))
    s.add("results", Result(s.new_id("res", out.cause), s.day, pid, op, out.msg, out.ok), out.cause)
    return out


def _worst(seed=1):
    """An owner after two till weeks with the worst the operations allow: 150-word letters
    from five people, 150-word notes, five 20-word aims, three 80-word requests with
    summaries, eight results of which two read the account in full, and twelve ties."""
    s = place(seed)
    run_routine(s, 14)
    owner = max(_kind(s, "owner"), key=lambda p: len(s.where("jobs", org=next(
        o.id for o in s.all("orgs") if o.owner == p.id))))
    others = [p.id for p in s.all("people") if p.id != owner.id]
    for o in others[:12]:
        if tie_rank(s, owner.id, o) > 2:
            tie(s, owner.id, o, "friend")
    letters = [send(s, o, owner.id, f"letter {i} " + "word " * 148) for i, o in enumerate(others[:5])]
    asked = [ask(s, o, owner.id, f"request {i} " + "please " * 78) for i, o in enumerate(others[5:8])]
    for rq in asked:
        s.set("requests", rq, "routine:t", summary="They ask you to work four hours on Saturdays for $48.00 a week, "
                                                  "from Saturday 21 March, for eight weeks.")
    assert _result(s, owner.id, "write_notes", text="note " * 150).ok
    aims.update(s, owner.id, [f"aim {i} " + "thing " * 19 for i in range(5)], "op:x:aims:d14.0")
    for i in range(6):
        if i in (1, 4):
            _result(s, owner.id, "read", what="account in full")
        else:
            _result(s, owner.id, "pay", to="Nobody Here", amount=1, note="word " * 60)
    _result(s, owner.id, "read", what="agreements")
    return s, owner.id, letters, asked


def test_the_view_holds_its_length_with_the_worst_the_operations_allow():
    s, owner, letters, asked = _worst()
    v = views.layout(s, owner)
    total = len(v.system.split()) + len(v.user.split())
    assert 1200 < total < 2000
    assert v.steps == list("abcdefg")                         # every step of giving way, in order
    shown = section(v.user, "LETTERS AND NOTICES")
    assert len(shown[0].split()) <= 104 and shown[0].endswith("\u2026") and shown[-1].startswith("(4 more:")
    waiting = section(v.user, "WAITING FOR YOUR ANSWER")
    assert waiting[-1] == "(2 more requests waiting)" and waiting[0].split(": ", 1)[1].count("please") == 60 - 2
    views.mark_seen(s, owner, v.ids, f"routine:seen:{owner}")
    assert [s.get("messages", m).seen for m in letters].count(True) == 1
    assert [s.get("requests", r).seen for r in asked].count(True) == 1
    assert views.has_news(s, owner)                           # what was cut from view waits unseen


@pytest.mark.parametrize("seed", (1, 2, 3))
def test_no_one_s_worst_case_reaches_2000_words(seed):
    s, owner, _, _ = _worst(seed)
    assert words(views.person_view(s, owner)) < 2000


def test_at_most_six_results_are_shown_and_only_the_latest_read_in_full():
    s = place(1)
    pid = _kind(s, "owner")[0].id
    for i in range(8):
        _result(s, pid, "read" if i in (2, 6) else "pay", what="account in full", to="Nobody Here", amount=1)
    rows = section(person_view(s, pid)[1]["content"], "WHAT CAME OF YOUR LAST ACTS")
    assert rows[0] == "(2 earlier results not shown)"
    assert [r.split(":")[0] for r in rows[1:] if not r.startswith("  ")] == ["read", "pay", "pay", "pay", "read", "pay"]
    assert rows[1] == "read: shown before" and rows[-1].startswith("pay: Not done:")
    reads = [r.id for r in s.all("results") if r.op == "read"]
    ids = views.shown_ids(s, pid)
    assert [r.id for r in s.all("results")][:2] == ids[6:8] and reads[1] in ids[:6]
    views.mark_seen(s, pid, ids, "routine:seen:x")
    assert all(r.seen for r in s.all("results"))
    _result(s, pid, "read", what="account in full"); _result(s, pid, "read", what="agreements")
    rows = section(person_view(s, pid)[1]["content"], "WHAT CAME OF YOUR LAST ACTS")
    assert rows[0] == "read: shown before" and rows[1].startswith("read: ob")
    _result(s, pid, "pay", to="Nobody Here", amount=1, note="word " * 70)
    assert len(section(person_view(s, pid)[1]["content"], "WHAT CAME OF YOUR LAST ACTS")[-1].split()) <= 40


def test_what_is_owed_lists_loans_agreements_standing_payments_and_arrears():
    s = place(1)
    for p in s.all("people"):
        for a in acts.accounts_of(s, p.id):
            for line in facts.owed_lines(s, a):
                ob = s.get("obligations", line.split(":")[0])
                assert ob.kind in ("loan", "agreement", "standing") and ob.status in ("active", "breached")
                other = ob.payee if ob.payer == a else ob.payer
                assert money(ob.cents) + " a week " in line and O.name_of(s, other) in line
                assert (", until " in line) == (ob.last is not None)
                assert ob.last is None or line.endswith(f", until {facts.when(ob.last)}")
    hh = s.all("households")[0].id
    rent = next(o for o in s.where("obligations", payer=hh) if o.kind == "rent")
    s.transfer(hh, "outside:opening", s.balance(hh), "spent", "routine:t")
    run_routine(s, 1)
    line = next(x for x in facts.owed_lines(s, hh) if x.startswith(rent.id + ":"))
    assert line.endswith(f", {money(rent.cents)} overdue")
    for i in range(9):
        O.create(s, kind="agreement", payer=hh, payee="outside:opening", cents=100 + i, first=s.day + 3, every=0,
                 memo=f"thing {i}", ends="both", made_by="routine", cause="routine:t")
    lines = facts.owed_lines(s, hh)
    assert len(lines) == 9 and re.fullmatch(r"\(\d+ more not shown\)", lines[-1])
    assert any(re.search(r", \$1\.0\d once on \w+ \d+ \w+ to Opening$", x) for x in lines[:8])


def test_the_banker_sees_the_bank_s_book():
    s = place(1)
    banker = _kind(s, "banker")[0]
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    hh = next(o.payer for o in s.where("obligations", payee=bank.id) if o.kind == "loan" and s.has("households", o.payer))
    s.transfer(hh, "outside:opening", s.balance(hh), "spent", "routine:t")
    run_routine(s, 1)
    book = section(person_view(s, banker.id)[1]["content"], "THE BANK'S BOOK")
    assert book[:2] == [bank.name, f"Account: {money(s.balance(bank.id))}"]
    loans = [o for o in s.where("obligations", payee=bank.id) if o.kind == "loan" and o.status == "active"]
    assert [line.split(":")[0] for line in book[2:]] == [o.id for o in loans][:12]
    late = 0
    for line, ob in zip(book[2:], loans):
        assert line.startswith(f"{ob.id}: {acts.name_of_who(s, ob.payer)}, {ob.memo}, {money(ob.cents)} a week")
        dues = [d for d in s.where("dues", obligation=ob.id) if d.status in ("arrears", "defaulted")]
        if dues:
            late += 1
            owed = sum(d.cents - d.paid for d in dues)
            assert line.endswith(f", {money(owed)} overdue since {date_text(min(d.day for d in dues))}")
        else:
            assert "overdue" not in line
    assert late >= 1
    for p in s.all("people"):
        if p.kind != "banker":
            assert "THE BANK'S BOOK" not in person_view(s, p.id)[1]["content"]


def test_owner_knowhow_states_the_real_rules():
    s = place(1)
    owner = _kind(s, "owner")[0]
    f = s.where("firms", org=next(o.id for o in s.all("orgs") if o.owner == owner.id))[0]
    lines = facts.knowhow(s, owner.id)
    assert lines[0] == ("Stock ordered before Monday arrives on Monday and is paid for on arrival; with no new order, "
                        "last week's order comes again.")
    from townsim.econ.firm import Firm
    carry = round(100 * Firm.__dataclass_fields__["carry_loss"].default)
    assert lines[1] == (f"Of what is left on the shelf at the end of a week, about {carry} in every 100 units go off; "
                        f"anything {f.shelf_weeks} weeks old is thrown away.")


def test_who_says_what_the_household_does_once():
    s = place(1)
    for p in s.all("people"):
        who = facts.who(s, p.id)
        for m in s.get("households", p.household).members:
            q = s.get("people", m)
            if m != p.id and q.job:
                line = next(x for x in who.split("\n") if x.startswith(q.name + ", "))
                assert q.job in line or q.job.split()[0] not in line or line.endswith(f"is {q.job}.")
        for j in s.where("jobs", person=p.id):
            org = s.get("orgs", j.org).name
            assert len([x for x in who.split("\n") if x.startswith("You ") and org in x and "Also" not in x]) == 1


def test_the_sender_of_a_request_shown_counts_as_known():
    s = place(1)
    pid, _, _, _, stranger, _ = someone_with_a_partner_a_friend_and_a_landlord(s)
    friend_of_stranger = s.get("people", stranger).name
    rq = ask(s, stranger, pid, "Could you help?")
    s.set("requests", rq, "routine:t", seen=True)
    third = next(q for q in s.all("people") if q.id not in knows(s, pid) and q.id not in (pid, stranger))
    send(s, third.id, pid, f"{friend_of_stranger} asked about you.")
    assert friend_of_stranger not in s.all("messages")[-1].unknown
