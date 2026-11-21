import re

from townsim.town.entities import Message, Notice, Request
from townsim.town.thin import thin_town
from townsim.town.views import mark_seen, person_view, shown_ids

_WORDS = re.compile(r"\b(test|model|game|consider)\b|you may|you should|you can|\bsimulat", re.IGNORECASE)


def test_a_view_changes_nothing_and_says_nothing_about_what_to_do():
    s = thin_town(); before = s.hash(); msgs = person_view(s, "p03")
    assert s.hash() == before and msgs[0]["role"] == "system" and "Craig Pryce" in msgs[0]["content"]
    assert msgs[0]["content"].startswith("You are Craig Pryce, of the Pryce household in Wenlow.")
    assert 'Reply with JSON only, in this form:\n{"thoughts": ' in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    text = msgs[1]["content"]
    assert text.startswith("TODAY: Monday 2 March.") and '{"op": "pay"' in text
    for content in (text, msgs[0]["content"]):
        assert _WORDS.search(content) is None, _WORDS.search(content)


def test_sections_come_in_order_and_empty_ones_are_left_out():
    s = thin_town()
    text = person_view(s, "p03")[1]["content"]
    assert "NEW" not in text.split("\n") and "WAITING FOR YOUR ANSWER" not in text and "YOUR NOTES" not in text
    s.add("notices", Notice("n1", 0, "p03", "A notice."), "routine:t")
    s.add("messages", Message("msg1", 0, "p01", "p03", "Hello Craig."), "routine:t")
    s.add("messages", Message("msg2", 0, "p01", "p04", "Not for Craig."), "routine:t")
    s.add("requests", Request("rq1", 0, "p01", "p03", "Lend me a ladder", 0, 14), "routine:t")
    s.add("requests", Request("rq2", 0, "p02", "p03", "Could you spare some?", 5000, 14), "routine:t")
    s.set("people", "p03", "routine:t", notes="Ring the landlord.", hours_left=11.75)
    text = person_view(s, "p03")[1]["content"]
    heads = ["TODAY: Monday 2 March.", "YOUR HOUSEHOLD'S ACCOUNT", "NEW", "WAITING FOR YOUR ANSWER", "YOUR NOTES",
             "You have 11.75 hours left this week.", "OPERATIONS"]
    lines = text.split("\n")
    assert [lines.index(h) for h in heads] == sorted(lines.index(h) for h in heads)
    assert "From Ruth Ashby: Hello Craig.\nA notice." in text and "Not for Craig." not in text
    assert "rq1 from Ruth Ashby: Lend me a ladder\nrq2 from Tom Ashby: Could you spare some? They ask you for $50.00." in text
    assert "YOUR NOTES\nRing the landlord." in text and "Balance: $300.00" in text
    assert shown_ids(s, "p03") == ["msg1", "n1"]


def test_whole_hours_are_shown_without_a_fraction():
    assert "You have 12 hours left this week." in person_view(thin_town(), "p01")[1]["content"]


def test_mark_seen_marks_only_what_it_is_given():
    s = thin_town()
    s.add("notices", Notice("n1", 0, "p03", "Shown."), "routine:t")
    ids = shown_ids(s, "p03")
    s.add("messages", Message("msg1", 0, "p01", "p03", "Arrived later."), "routine:t")
    mark_seen(s, "p03", ids, "routine:seen:p03")
    assert s.get("notices", "n1").seen and not s.get("messages", "msg1").seen
    assert s.changes[-1].cause == "routine:seen:p03"
    assert shown_ids(s, "p03") == ["msg1"]


def test_a_long_statement_loses_its_oldest_lines_to_stay_within_1200_words():
    s = thin_town()
    for i in range(25):
        s.transfer("outside:employers", "h2", 100, f"line {i} " + "word " * 60, "routine:t")
    text = person_view(s, "p03")[1]["content"]
    assert len(text.split()) <= 1200 and "earlier lines not shown)" in text
    assert "line 24 " in text and "line 0 " not in text
