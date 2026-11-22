"""Names in what people write that are no one the reader knows: flagged to the reader, and to
the writer in the result of what they sent; ordinary sentences never flagged."""

import functools
import pickle

import pytest

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import gen, names, unknown
from townsim.town.acts import Act, apply_act, knows
from townsim.town.views import person_view

FAMILIES = ["a", "b", "c", "d"]


@functools.cache
def _frozen() -> bytes:
    return pickle.dumps(gen.generate(1, FAMILIES))


def place():
    return pickle.loads(_frozen())


def _reader(s):
    """A person who works in a shop, and someone they know who writes to them."""
    pid = next(p.id for p in s.all("people") if p.kind == "staff")
    home = s.get("people", pid).household
    writer = next(q for q in knows(s, pid) if s.get("people", q).household != home)
    return pid, writer


def _flags(s, pid, writer, text):
    return unknown.unknown_names(s, pid, writer, text)


def test_the_list_of_given_names_is_broad_and_holds_the_generator_s_own():
    assert len(unknown.GIVEN) >= 400
    assert set(names.FIRST_NAMES) <= unknown.GIVEN
    assert not unknown.GIVEN & set(names.SURNAMES)
    assert not unknown.GIVEN & {"January", "March", "April", "May", "June", "July", "August", "Monday", "Friday"}


def _ordinary(s):
    shops = [o.name for o in s.all("orgs") if o.kind == "shop"]
    bank = next(o.name for o in s.all("orgs") if o.kind == "bank")
    return [
        "Good Morning to you.", "Dear Sir, I write about the rent.", "Thank You for the eggs.",
        "See you on Monday.", "It was a cold March this year.", "On Friday 13 March the van came late.",
        "Happy New Year from all of us.", f"{s.place} is quiet this week.", f"I was in {s.place} on Tuesday.",
        *[f"{shop} has bread in again." for shop in shops], f"I spoke to {bank} about it.",
        "Will you be about on Saturday?", "Grace before meals, as Mother always says.", "Frank words were said.",
        "Hope you are well.", "Bill paid on Friday, all of it.", "Kind Regards and Best Wishes.",
        "Many Thanks, as always.", "Yours Sincerely.", "The Bank will want it back.", "I went to the Post Office.",
        "The Town Council meets on Wednesday.", "We had a lovely Christmas Day.", "Mark the date in the diary.",
        "Merry Christmas to you both.", "Is the shop open on Sunday?", "I will see you in April.",
        "I go to Church Street on Thursdays.", "It is Monday 9 March already.", "OK, I will.",
        "The Lettings people wrote.", "Ruby red apples are best.", "Dawn came early.",
        "The August Bank Holiday is coming.", "I live on Victoria Road.", "We walked up to St Mary's church on Sunday.",
        "Queen Victoria would not stand for this.", "Customer Services said they would call back.",
        "The Food Bank on the corner is open.", "We had a Victoria sponge for tea.", "St Patrick's Day was fun.",
    ]


def _invented():
    return [
        "Edith Marsh", "Sarah Kemp", "Mr Grant", "Mrs Varga", "Dr. Ellison", "Miss Tamworth", "Ms Ogden",
        "Ask Tom", "Harold Bentley", "Agnes Price", "Brian Coates", "Doris Hale", "Graham Pike", "Norma Jessop",
        "Kemp Lettings", "Grimsdale Council", "Northfield Bank", "Holm Services Ltd", "Barker Agency",
        "Cotton Company", "Ridley Limited", "Fenwick Co", "Eastgate Office", "Ernestine Fisk", "Hector Lyle",
        "Quentin Hardcastle", "Phoebe Oakes", "Rhoda Mellor", "Wilfred Pratt", "Imogen Swales", "Theodore",
        "Mrs Edith Marsh",
    ]


def test_ordinary_sentences_are_never_flagged():
    s = place()
    pid, writer = _reader(s)
    ordinary = _ordinary(s)
    assert len(ordinary) >= 30
    for text in ordinary:
        assert _flags(s, pid, writer, text) == [], text


def test_invented_names_and_bodies_are_flagged():
    s = place()
    pid, writer = _reader(s)
    invented = _invented()
    assert len(invented) >= 30
    for name in invented:
        found = _flags(s, pid, writer, f"You could ask {name} about it.")
        assert len(found) == 1, (name, found)
        assert found[0].replace("Dr.", "Dr") in name.replace("Dr.", "Dr") or name == "Ask Tom", (name, found)
    assert _flags(s, pid, writer, "Ask Tom about it.") == ["Tom"]
    assert _flags(s, pid, writer, "Ruby Tranter sends her love.") == ["Ruby Tranter"]      # a word-name opening one
    assert _flags(s, pid, writer, "Mr Grant and Sarah Kemp said so; Sarah Kemp again.") == ["Mr Grant", "Sarah Kemp"]


def test_known_people_and_the_reader_s_own_records_are_not_flagged():
    s = place()
    pid, writer = _reader(s)
    me = s.get("people", pid)
    friend = s.get("people", writer)
    employer = next(s.get("orgs", j.org).name for j in s.where("jobs", person=pid))
    outside = [o.name for o in s.all("orgs") if o.outside]
    text = (f"{friend.name} and {friend.name.split()[0]} said {employer} is fine. Mr {friend.name.split()[-1]} "
            f"agreed, and so did {me.name}.")
    assert _flags(s, pid, writer, text) == []
    # an organisation outside the place that is in no record of theirs is no one they know
    stranger_orgs = [n for n in outside if n not in unknown.record_orgs(s, pid)]
    assert all(_flags(s, pid, writer, f"The {n} wrote.") for n in stranger_orgs if n.split()[-1] in unknown.ORG_WORDS)


def _do(s, actor, op, **args):
    return apply_act(s, Act(actor, actor, op, args), 0)


def test_the_writer_is_told_and_the_reader_sees_it_under_a_letter_request_and_appointment():
    s = place()
    pid, writer = _reader(s)
    reader = s.get("people", pid)
    out = _do(s, writer, "message", to=reader.name, text="Edith Marsh says hello.")
    assert out.ok and out.msg == f"Sent to {reader.name}. Edith Marsh is not anyone you know."
    out = _do(s, writer, "request", to=reader.name, text="Could Sarah Kemp and Mr Grant help?", amount=0)
    assert out.ok and out.msg.endswith("Sarah Kemp and Mr Grant are not anyone you know.")
    out = _do(s, writer, "book_appointment", **{"with": reader.name, "day": "Friday", "about": "what Edith Marsh said"})
    assert out.ok and out.msg.endswith("on Friday 6 March.")           # told of Edith once this week already
    user = person_view(s, pid)[1]["content"]
    assert user.count("(Edith Marsh is not someone you know.)") >= 2
    assert "(Sarah Kemp is not someone you know.)" in user and "(Mr Grant is not someone you know.)" in user


def test_an_agreement_s_words_and_a_talk_s_opening_are_checked():
    s = place()
    pid, writer = _reader(s)
    reader = s.get("people", pid)
    out = _do(s, writer, "propose_agreement", to=reader.name, amount=5, per="week", weeks=2, payer="me",
              what="for what Edith Marsh asked")
    assert out.ok and out.msg.endswith("Edith Marsh is not anyone you know."), out.msg
    assert "(Edith Marsh is not someone you know.)" in person_view(s, pid)[1]["content"]
    out = _do(s, writer, "converse", **{"with": reader.name, "opening": "Did Harold Bentley call?"})
    assert out.ok and out.msg.endswith("Harold Bentley is not anyone you know.")


@pytest.mark.parametrize("text", ["", "   ", "\x00", "a" * 5000, "Mr", "Mr.", "Bank", "Dear Mr . Grant"])
def test_odd_text_never_raises(text):
    s = place()
    pid, writer = _reader(s)
    _flags(s, pid, writer, text)


def test_every_resident_is_known_by_name_and_a_writer_is_told_of_a_name_once_a_week():
    s = place()
    pid, writer = _reader(s)
    reader = s.get("people", pid)
    strangers = [p for p in s.all("people") if p.id not in (pid, writer)
                 and p.id not in __import__("townsim.town.acts", fromlist=["knows"]).knows(s, writer)]
    assert strangers                                   # someone the writer has no tie with
    other = strangers[0]
    out = _do(s, writer, "message", to=reader.name, text=f"{other.name} and {other.name.split()[0]} send their love.")
    assert out.ok and out.msg == f"Sent to {reader.name}."
    assert _do(s, writer, "message", to=other.name.split()[0], text="Hello there.").ok      # one resident bears it
    for k in range(2):
        out = _do(s, writer, "message", to=reader.name, text=f"Sarah Kemp says hello, {k}.")
        assert out.msg == f"Sent to {reader.name}." + (" Sarah Kemp is not anyone you know." if k == 0 else "")
    for _ in range(7):
        s.advance_day()
    out = _do(s, writer, "message", to=reader.name, text="Sarah Kemp says hello again.")
    assert out.msg.endswith("Sarah Kemp is not anyone you know.")
