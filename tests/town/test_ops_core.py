import pytest

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import acts
from townsim.town.acts import Act, apply_act
from townsim.town.entities import Request
from townsim.town.statement import agreements_text, statement, statement_text

from town.helpers import thin


def do(s, actor, op, n=0, **args):
    return apply_act(s, Act(actor, actor, op, args), n)


def refused(s, actor, op, **args):
    """Apply an act that must be refused; check it changed nothing and return the reason."""
    before = s.hash()
    out = do(s, actor, op, **args)
    assert not out.ok and s.hash() == before and s.engine_errors == []
    return out.msg


def ob_id(s, payer, memo):
    return next(o.id for o in s.all("obligations") if o.payer == payer and o.memo == memo)


# pay

def test_pay_moves_money_between_households():
    s = thin()
    out = do(s, "p01", "pay", to="Megan Pryce", amount=25, note="for the lift")
    assert out.ok and out.msg == "Paid $25.00 to Megan Pryce."
    assert (s.balance("h1"), s.balance("h2")) == (57500, 32500)
    assert s.ledger.transfers[-1].memo == "for the lift" and s.ledger.transfers[-1].cause == "op:p01:pay:d0.0"


def test_pay_with_for_counts_toward_that_obligation():
    s = thin(); rent = ob_id(s, "h1", "rent")
    out = do(s, "p01", "pay", to="Wenlow Lettings", amount="$210", note="rent", **{"for": rent})
    assert out.msg == "Paid $210.00 to Wenlow Lettings. It was counted toward rent due Monday 2 March."
    due = next(d for d in s.all("dues") if d.obligation == rent)
    assert (due.paid, due.status) == (21000, "paid")


def test_pay_without_for_still_counts_toward_what_is_owed():
    s = thin()
    out = do(s, "p01", "pay", to="Wenlow Lettings", amount=100)
    assert out.msg == "Paid $100.00 to Wenlow Lettings. It was counted toward rent due Monday 2 March."


def test_pay_for_something_not_owed_to_them_is_refused():
    s = thin(); energy = ob_id(s, "h1", "energy")
    msg = refused(s, "p01", "pay", to="Wenlow Lettings", amount=10, **{"for": energy})
    assert msg == f"Not done: you have nothing with the id '{energy}' that is owed to Wenlow Lettings."
    assert refused(s, "p01", "pay", to="Wenlow Lettings", amount=10, **{"for": "ob999"}).endswith(
        "nothing with the id 'ob999' that is owed to Wenlow Lettings.")


def test_pay_to_no_one_too_much_or_yourself_is_refused():
    s = thin()
    assert refused(s, "p01", "pay", to="Sanjay", amount=5) == "Not done: no one called 'Sanjay' is known in the town."
    assert refused(s, "p01", "pay", to="Megan Pryce", amount=700) == \
        "Not done: the account holds $600.00 and this needs $700.00."
    assert refused(s, "p01", "pay", to="Tom Ashby", amount=5) == "Not done: that is your own household."
    assert refused(s, "p01", "pay", to="ashby", amount=5) == "Not done: that is your own household."
    assert refused(s, "p01", "pay", to="Megan Pryce", amount="lots").startswith("Not done:")


# standing_payment

def test_standing_payment_sets_up_a_weekly_obligation():
    s = thin()
    out = do(s, "p01", "standing_payment", to="Megan Pryce", amount=20, day="Thursday", weeks=3, note="")
    ob = s.all("obligations")[-1]
    assert out.msg == (f"Set up ({ob.id}): $20.00 to Megan Pryce every Thursday, first on Thursday 5 March, "
                       "last on Thursday 19 March.")
    assert (ob.kind, ob.payer, ob.payee, ob.cents, ob.first, ob.every, ob.last, ob.ends, ob.made_by) == \
        ("standing", "h1", "h2", 2000, 3, 7, 17, "payer", "p01")
    assert ob.memo == "standing payment to Megan Pryce"


def test_standing_payment_starts_on_the_weekday_on_or_after_start():
    s = thin()
    out = do(s, "p01", "standing_payment", to="Craig Pryce", amount=5, day="thu", start="Thursday 12 March",
             weeks=0, note="garden")
    ob = s.all("obligations")[-1]
    assert (ob.first, ob.last, ob.memo) == (10, None, "garden")
    assert out.msg == f"Set up ({ob.id}): $5.00 to Craig Pryce every Thursday, first on Thursday 12 March."
    do(s, "p01", "standing_payment", 1, to="Craig Pryce", amount=5, day=" sun. ", start="10 March")
    assert s.all("obligations")[-1].first == 13


def test_standing_payment_without_start_is_never_today():
    s = thin()   # day 0 is a Monday
    do(s, "p01", "standing_payment", to="Craig Pryce", amount=5, day="MONDAY")
    assert s.all("obligations")[-1].first == 7
    do(s, "p01", "standing_payment", 1, to="Craig Pryce", amount=5, day="Monday", start="2 March")
    assert s.all("obligations")[-1].first == 0


def test_a_second_payment_to_someone_already_owed_is_shown_as_a_second():
    s = thin(); rent = ob_id(s, "h1", "rent")
    out = do(s, "p01", "standing_payment", to="Wenlow Lettings", amount=210, day="Friday", weeks=0)
    assert out.msg.endswith(f". This is in addition to rent ({rent}), which already goes to them on Monday 2 March.")
    do(s, "p01", "pay", 1, to="Wenlow Lettings", amount=210)
    out = do(s, "p01", "standing_payment", 2, to="Wenlow Lettings", amount=5, day="Friday")
    assert f"in addition to rent ({rent}), which already goes to them on Monday 9 March." in out.msg


def test_standing_payment_refusals():
    s = thin()
    base = dict(to="Megan Pryce", amount=20, day="Thursday")
    assert refused(s, "p01", "standing_payment", **{**base, "day": "Funday"}) == \
        "Not done: 'Funday' is not a day of the week."
    assert refused(s, "p01", "standing_payment", **{**base, "start": "soon"}) == \
        "Not done: 'soon' is not a date that can be read."
    assert refused(s, "p01", "standing_payment", **{**base, "start": "1 March"}) == \
        "Not done: '1 March' is not a date that can be read."
    for weeks in (-1, 1.5, "two", True, 521):
        assert refused(s, "p01", "standing_payment", **{**base, "weeks": weeks}).startswith("Not done:")
    assert refused(s, "p01", "standing_payment", **{**base, "to": "Sanjay"}) == \
        "Not done: no one called 'Sanjay' is known in the town."
    assert refused(s, "p01", "standing_payment", **{**base, "to": "Tom Ashby"}) == \
        "Not done: that is your own household."
    assert refused(s, "p01", "standing_payment", **{**base, "amount": 0}).startswith("Not done:")


# stop

def test_stop_a_standing_payment():
    s = thin()
    do(s, "p01", "standing_payment", to="Megan Pryce", amount=20, day="Thursday")
    ob = s.all("obligations")[-1].id
    assert do(s, "p02", "stop", 1, id=ob).msg == "Stopped."    # either member of the household
    assert s.get("obligations", ob).status == "stopped"


def test_stop_a_service_leaves_it():
    s = thin(); energy = ob_id(s, "h1", "energy")
    assert do(s, "p01", "stop", id=energy).msg == "You have left; the fee ends."
    assert s.get("obligations", energy).status == "stopped"


def test_stop_on_an_agreement_is_a_breach_and_says_so():
    s = thin(); rent = ob_id(s, "h1", "rent")
    out = do(s, "p01", "stop", id=rent)
    assert out.msg == "That was an agreement with Wenlow Lettings. Stopping it is recorded as a breach."
    assert s.get("obligations", rent).status == "breached"
    assert s.all("records")[-1].kind == "breach"


def test_stop_what_you_do_not_pay_is_refused():
    s = thin()
    for other in (ob_id(s, "h2", "rent"), ob_id(s, "outside:employers", "wages"), "ob999", "", None, 3):
        assert refused(s, "p01", "stop", id=other) == f"Not done: you have nothing with the id '{other}'."
    rent = ob_id(s, "h1", "rent")
    do(s, "p01", "stop", id=rent)
    assert refused(s, "p01", "stop", id=rent) == f"Not done: you have nothing with the id '{rent}'."


# message

def test_message_is_delivered_to_a_person():
    s = thin()
    out = do(s, "p01", "message", to="craig pryce", text="Can you help on Saturday?")
    assert out.msg == "Sent to Craig Pryce."
    m = s.all("messages")[-1]
    assert (m.sender, m.to, m.text, m.day) == ("p01", "p03", "Can you help on Saturday?", 0)


def test_a_letter_to_no_one_comes_back():
    s = thin()
    assert refused(s, "p01", "message", to="Sanjay", text="hello") == \
        "Not done: your letter to 'Sanjay' came back undelivered."
    assert refused(s, "p01", "message", to="Wenlow Lettings", text="hello") == \
        "Not done: your letter to 'Wenlow Lettings' came back undelivered."


def test_control_characters_are_taken_out_of_what_people_write():
    s = thin()
    do(s, "p01", "message", to="Craig Pryce", text="line one\nline\ttwo\x00\x1b[31m\x7f")
    assert s.all("messages")[-1].text == "line one\nline\ttwo[31m"
    do(s, "p01", "write_notes", 1, text="keep\x08 this")
    assert s.get("people", "p01").notes == "keep this"
    do(s, "p01", "pay", 2, to="Megan Pryce", amount=1, note="\x0c\x0c")
    assert s.ledger.transfers[-1].memo == "payment to Megan Pryce"
    assert refused(s, "p01", "message", to="Craig Pryce", text="\x00\x01") == "Not done: there was nothing written."


def test_message_with_nothing_or_too_much_written_is_refused():
    s = thin()
    assert refused(s, "p01", "message", to="Craig Pryce", text="  ") == "Not done: there was nothing written."
    assert refused(s, "p01", "message", to="Craig Pryce") == "Not done: there was nothing written."
    assert refused(s, "p01", "message", to="Craig Pryce", text="word " * 301) == \
        "Not done: that is too long for one letter."
    assert do(s, "p01", "message", to="Craig Pryce", text="word " * 300).ok


# request and answer

def ask(s, amount=40, n=0):
    return do(s, "p03", "request", n, to="Ruth Ashby", text="Could you lend us $40 for the meter?", amount=amount)


def test_request_is_sent_and_stays_open_two_weeks():
    s = thin()
    out = ask(s)
    rq = s.all("requests")[-1]
    assert out.msg == f"Sent to Ruth Ashby ({rq.id}). It stays open until Monday 16 March."
    assert (rq.sender, rq.to, rq.cents, rq.expires, rq.status) == ("p03", "p01", 4000, 14, "open")


def test_request_without_money():
    s = thin()
    for n, amount in enumerate((0, "0", None, "0.00", "$0", 0.0, " $0.0 ")):
        assert ask(s, amount, n).ok and s.all("requests")[-1].cents == 0
    assert do(s, "p03", "request", 9, to="Ruth Ashby", text="Come for tea?").ok


def test_request_without_an_amount():
    s = thin()
    assert do(s, "p03", "request", to="Ruth Ashby", text="Come for tea?").ok and s.all("requests")[-1].cents == 0


def test_request_refusals():
    s = thin()
    assert refused(s, "p03", "request", to="Ruth Ashby", text="lend", amount="lots").startswith("Not done:")
    assert refused(s, "p03", "request", to="Sanjay", text="lend") == \
        "Not done: no one called 'Sanjay' is known in the town."
    assert refused(s, "p03", "request", to="Craig Pryce", text="lend").startswith("Not done:")
    assert refused(s, "p03", "request", to="Wenlow Lettings", text="lend").startswith("Not done:")
    assert refused(s, "p03", "request", to="Megan Pryce", text="lend", amount=5) == \
        "Not done: that is your own household."
    assert refused(s, "p03", "request", to="Ruth Ashby", text="") == "Not done: there was nothing written."
    assert refused(s, "p03", "request", to="Ruth Ashby", text="\x00\x1b") == "Not done: there was nothing written."
    for amount in (-1, "-0", True, "0,0"):
        assert refused(s, "p03", "request", to="Ruth Ashby", text="lend", amount=amount).startswith("Not done:")


def test_accepting_a_request_for_money_moves_it_once():
    s = thin(); ask(s); rq = s.all("requests")[-1].id
    out = do(s, "p01", "answer", 1, request=rq, answer="Accept", note="glad to")
    assert out.ok and out.msg == "Accepted. Paid $40.00 to Craig Pryce."
    assert (s.balance("h1"), s.balance("h2")) == (56000, 34000)
    assert s.get("requests", rq).status == "accepted"
    notice = s.all("notices")[-1]
    assert (notice.to, notice.text) == \
        ("p03", f"Ruth Ashby accepted your request {rq} (Could you lend us $40 for the meter?). They wrote: glad to")
    assert refused(s, "p01", "answer", request=rq, answer="accept") == \
        f"Not done: you have no open request with the id '{rq}'."
    assert (s.balance("h1"), s.balance("h2")) == (56000, 34000)


def test_money_sent_on_accepting_a_request_counts_toward_nothing():
    s = thin()
    assert do(s, "p01", "standing_payment", to="Megan Pryce", amount=20, day="Friday").ok
    ask(s); rq = s.all("requests")[-1].id
    assert do(s, "p01", "answer", 2, request=rq, answer="accept").msg == "Accepted. Paid $40.00 to Craig Pryce."
    standing = s.all("obligations")[-1]
    assert standing.kind == "standing" and s.ledger.transfers[-1].memo == f"request {rq}"
    assert not any(d.obligation == standing.id for d in s.all("dues"))      # Friday's $20 is still to be taken


def test_declining_a_request_tells_the_sender():
    s = thin(); ask(s); rq = s.all("requests")[-1].id
    assert do(s, "p01", "answer", 1, request=rq, answer="decline").msg == "Declined."
    assert s.get("requests", rq).status == "declined" and s.balance("h1") == 60000
    assert s.all("notices")[-1].text == f"Ruth Ashby declined your request {rq} (Could you lend us $40 for the meter?)."


def test_the_notice_cuts_a_long_request_and_cleans_the_note():
    s = thin()
    do(s, "p03", "request", to="Ruth Ashby", text="a" * 100)
    rq = s.all("requests")[-1].id
    do(s, "p01", "answer", 1, request=rq, answer="decline", note="no\x07 thanks\x00")
    assert s.all("notices")[-1].text == f"Ruth Ashby declined your request {rq} ({'a' * 80}\u2026). They wrote: no thanks"


def test_answer_refusals():
    s = thin(); ask(s); rq = s.all("requests")[-1].id
    assert refused(s, "p02", "answer", request=rq, answer="accept") == \
        f"Not done: you have no open request with the id '{rq}'."
    assert refused(s, "p01", "answer", request="rq999", answer="accept") == \
        "Not done: you have no open request with the id 'rq999'."
    assert refused(s, "p01", "answer", request=rq, answer="maybe") == \
        "Not done: 'maybe' is not an answer to a request."
    assert refused(s, "p01", "answer", request=rq, answer=["accept"] * 30) == \
        "Not done: '['accept', 'accept', 'accept', 'accept',' is not an answer to a request."
    ask(s, 700, 1); big = s.all("requests")[-1].id
    assert refused(s, "p01", "answer", request=big, answer="accept") == \
        "Not done: the account holds $600.00 and this needs $700.00."
    for _ in range(15):
        s.advance_day()
    assert refused(s, "p01", "answer", request=rq, answer="decline").startswith("Not done: you have no open request")


# read and write_notes

def test_read_shows_the_account_and_agreements():
    s = thin()
    assert do(s, "p01", "read", what="account").msg == statement_text(s, statement(s, "h1"))
    assert do(s, "p01", "read", what=" Account In Full ").msg == \
        statement_text(s, statement(s, "h1", back=28, max_lines=60))
    assert do(s, "p01", "read", what="agreements").msg == agreements_text(s, "h1")
    assert refused(s, "p01", "read", what="everything") == \
        "Not done: 'everything' is not something that can be read."
    assert do(s, "p01", "read", 1, what="AGREEMENTS").ok


def test_write_notes_replaces_the_notes():
    s = thin()
    assert do(s, "p01", "write_notes", text="rent is Monday").msg == "Noted."
    assert do(s, "p01", "write_notes", 1, text="energy is Wednesday").ok
    assert s.get("people", "p01").notes == "energy is Wednesday"
    assert refused(s, "p01", "write_notes", text="word " * 301).startswith("Not done:")
    assert refused(s, "p01", "write_notes", text=["a list"]).startswith("Not done:")


# junk in every argument of every operation

JUNK = [None, True, False, 0, -1, 2.5, 10**30, float("nan"), float("inf"), "", "   ", "x" * 20000, "word " * 5000,
        [], [1, "a"], {}, {"a": {"b": [None]}}, "ob1", "rq1", "Ruth Ashby", "$-3", "0"]


def _standing(s):
    do(s, "p01", "standing_payment", 90, to="Megan Pryce", amount=20, day="Thursday")
    return {"id": s.all("obligations")[-1].id}


def _asked(s):
    ask(s, 5, 90)
    return {"request": s.all("requests")[-1].id, "answer": "accept", "note": "fine"}


VALID = {
    "pay": lambda s: {"to": "Megan Pryce", "amount": 5, "note": "x", "for": ""},
    "standing_payment": lambda s: {"to": "Megan Pryce", "amount": 5, "day": "Thursday",
                                   "start": "Thursday 12 March", "weeks": 2, "note": "x"},
    "stop": _standing,
    "message": lambda s: {"to": "Craig Pryce", "text": "hi"},
    "request": lambda s: {"to": "Craig Pryce", "text": "lend", "amount": 5},
    "answer": _asked,
    "read": lambda s: {"what": "account"},
    "write_notes": lambda s: {"text": "x"},
}
CASES = [(op, key) for op, make in VALID.items() for key in make(thin())]


def test_the_valid_acts_are_valid():
    for op, make in VALID.items():
        s = thin()
        args = make(s)
        actor = "p01"
        assert apply_act(s, Act(actor, actor, op, args), 0).ok, op


@pytest.mark.parametrize("op,key", CASES)
def test_junk_in_any_argument_never_breaks_anything(op, key):
    for value in JUNK + ["<missing>"]:
        s = thin()
        args = VALID[op](s)
        if value == "<missing>":
            del args[key]
        else:
            args[key] = value
        before = s.hash()
        out = apply_act(s, Act("p01", "p01", op, args), 0)
        assert out.ok or out.msg.startswith("Not done:"), (op, key, value, out.msg)
        if not out.ok:
            assert s.hash() == before and len(out.msg) < 500, (op, key, value)
        assert s.engine_errors == [] and s.ledger.total() == 0, (op, key, value, s.engine_errors)


# acting for someone else

def _ruth_may_act_for_craig():
    acts.AUTHORISERS[:] = [lambda s, a: None if a.actor == a.principal or (a.actor, a.principal) == ("p01", "p03")
                           else "you have no connection that allows you to act for them."]


def test_an_act_for_a_principal_uses_their_account_and_the_actors_hours():
    s = thin(); _ruth_may_act_for_craig()
    out = apply_act(s, Act("p01", "p03", "pay", {"to": "Wenlow Lettings", "amount": 10}), 2)
    assert out.ok and out.cause == "op:p01/p03:pay:d0.2"
    t = s.ledger.transfers[-1]
    assert (t.src, t.dst, t.cents, t.cause) == ("h2", "org1", 1_000, "op:p01/p03:pay:d0.2")
    assert s.get("people", "p01").hours_left == 11.75 and s.get("people", "p03").hours_left == 12.0
    assert apply_act(s, Act("p01", "p01", "pay", {"to": "Megan Pryce", "amount": 1}), 3).cause == "op:p01:pay:d0.3"
    out = apply_act(s, Act("p01", "p04", "pay", {"to": "Wenlow Lettings", "amount": 1}), 4)
    assert out.msg == "Not done: you have no connection that allows you to act for them."


def test_a_principal_s_requests_stops_letters_and_notes():
    s = thin(); _ruth_may_act_for_craig()
    s.add("requests", Request("rq1", 0, "p02", "p03", "Could you spare $5?", 500, 14), "routine:t")
    assert apply_act(s, Act("p01", "p01", "answer", {"request": "rq1", "answer": "accept"}), 0).msg.startswith(
        "Not done: you have no open request")                      # it is Craig's to answer
    out = apply_act(s, Act("p01", "p03", "answer", {"request": "rq1", "answer": "accept"}), 1)
    assert out.ok and s.ledger.transfers[-1][1:4] == ("h2", "h1", 500)
    rent = ob_id(s, "h2", "rent")
    assert apply_act(s, Act("p01", "p01", "stop", {"id": rent}), 2).msg.startswith("Not done:")
    assert apply_act(s, Act("p01", "p03", "stop", {"id": rent}), 3).ok
    assert apply_act(s, Act("p01", "p03", "message", {"to": "Tom Ashby", "text": "Hello."}), 4).ok
    assert s.all("messages")[-1].sender == "p01"
    assert apply_act(s, Act("p01", "p03", "write_notes", {"text": "mine"}), 5).ok
    assert (s.get("people", "p01").notes, s.get("people", "p03").notes) == ("mine", "")
