"""The first eight operations: paying, standing payments, stopping, letters, requests and
their answers, reading the account, and notes. Registered when this module is imported,
together with the operations of work in `ops_work` and of agreements, loans and rent in
`ops_deals`, whose requests `answer` hands to the checks and changes in `ANSWERERS`.

Each `check` refuses anything its arguments do not support, so each `do` only acts.
Arguments come from a reply and may be any JSON value.

An act is carried out for its principal: money moves from an account the principal
controls (their household's unless a `from` names a business they own), and requests
answered are those made to the principal. Letters and requests are sent by the actor,
and notes are the actor's own. Names are resolved as the actor knows people.
"""

from __future__ import annotations

from typing import Any, Callable

from townsim.town import jobs, obligations
from townsim.town.acts import (LONGEST_NAME, Act, OpSpec, account_of, clean_text, controls, cut, dollars, fits,
                               from_account, knows, name_of_who, register, resolve, shown, to_cents)
from townsim.town.clock import DAYS, date_in, date_text, dow, parse_date, unreadable, weekday
from townsim.town.entities import Message, Notice, Request
from townsim.town.obligations import money, name_of
from townsim.town.statement import agreements_text, memo as memo_shown, quoted, statement, statement_text
from townsim.town.store import Store
from townsim.town.unknown import record_orgs, unknown_names, writer_text

MAX_WORDS = 80            # a note on an answer
NOTE_WORDS = 12           # a note on a payment, which becomes its memo: a label
LETTER_WORDS = 150
REQUEST_WORDS = 80
NOTES_WORDS = 150
REQUEST_DAYS = 14
MAX_WEEKS = 520
_READS = ("account", "account in full", "agreements")

# Requests of kinds other than `ask`, by kind: `check(store, act, request)` says why it
# cannot be accepted now (without `Not done:`) or None, and `do(store, act, request, cause)`
# carries out its terms and returns what both sides are told. Each checks the terms again.
Answerer = tuple[Callable[[Store, Act, Request], str | None], Callable[[Store, Act, Request, str], str]]
ANSWERERS: dict[str, Answerer] = {}


# reading arguments

def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _too_long(text: str, words: int = MAX_WORDS) -> bool:
    return not fits(text, words)


def _resolve(store: Store, name: Any, actor: str | None = None) -> str | None:
    """`acts.resolve`, except that a name longer than any name can be is no one."""
    if isinstance(name, str) and len(name) > LONGEST_NAME:
        return None
    return resolve(store, name, actor)


def _own(store: Store, act: Act) -> str:
    """The principal's household account."""
    return account_of(store, act.principal)


def _source(store: Store, act: Act) -> str:
    """The account the act draws on; its check has made sure there is one."""
    return from_account(store, act)[0]


def _payee(store: Store, act: Act) -> tuple[str | None, str]:
    """The account `to` names, or None and why not. The act's `from` must already read."""
    to = act.args.get("to")
    who = _resolve(store, to, act.actor)
    if who is None:
        return None, f"no one you know is called '{shown(to)}'. Names are written in full, as they appear in your records."
    if account_of(store, who) == _source(store, act):
        if _source(store, act) == _own(store, act):
            return None, "that is your own household."
        return None, "that is the account it would be paid from."
    return account_of(store, who), name_of_who(store, who)


def _amount(value: Any) -> tuple[int | None, str]:
    cents = to_cents(value)
    if cents is None:
        return None, f"'{shown(value)}' is not an amount of money that can be paid."
    return cents, ""


def _note(value: Any) -> str:
    """An optional note, cleaned: any non-string counts as none."""
    return (clean_text(value) or "").strip()


def _short_of(store: Store, account: str, cents: int) -> str | None:
    acct = store.ledger.accounts[account]
    if acct.balance < cents and not acct.allow_credit:
        return f"the account holds {money(acct.balance)} and this needs {money(cents)}."
    return None


def _person_to(store: Store, act: Act) -> str | None:
    who = _resolve(store, act.args.get("to"), act.actor)
    return who if who is not None and store.has("people", who) else None


def _text(value: Any, too_long: str, words: int) -> str | None:
    """Why written text cannot be sent, or None. The text checked is the cleaned text."""
    text = clean_text(value)
    if text is None or not text.strip():
        return "there was nothing written."
    if _too_long(text, words):
        return too_long
    return None


# pay

NOT_TOWARD = ("none", "nothing")          # `for` a payment that is not toward anything owed


def _pay_ref(act: Act) -> Any:
    ref = act.args.get("for")
    return None if _blank(ref) or _toward_nothing(act) else ref


def _toward_nothing(act: Act) -> bool:
    ref = act.args.get("for")
    return isinstance(ref, str) and ref.strip().lower() in NOT_TOWARD


def _check_pay(store: Store, act: Act) -> str | None:
    source, why = from_account(store, act)
    if source is None:
        return why
    payee, name = _payee(store, act)
    if payee is None:
        return name
    cents, why = _amount(act.args.get("amount"))
    if cents is None:
        return why
    if _too_long(_note(act.args.get("note")), NOTE_WORDS):
        return "the note is too long."
    ref = _pay_ref(act)
    if ref is not None:
        if not (isinstance(ref, str) and store.has("obligations", ref)
                and (store.get("obligations", ref).payer == source or _own_wage(store, act, ref))
                and store.get("obligations", ref).payee == payee and not _ended_wage(store, ref)):
            return f"you have nothing with the id '{shown(ref)}' that is owed to {name}."
    return _short_of(store, source, cents)


def _ended_wage(store: Store, ob_id: str) -> bool:
    """Whether `ob_id` is a wage that has ended or been stopped, with nothing of it owed."""
    ob = store.get("obligations", ob_id)
    ended = ob.status not in obligations.SCHEDULED or (ob.last is not None and ob.last < store.day)
    return ob.kind == "wage" and ended and not obligations.owed_dues(store, ob_id)


def _nothing_counted(store: Store, ob_id: str) -> str:
    """What the payer is told when a payment naming `ob_id` counted toward none of it: because
    it has ended, nothing more falls due on it, or its next payment is more than a week away;
    "" otherwise."""
    ob = store.get("obligations", ob_id)
    what = f" Nothing was counted toward {memo_shown(ob.memo)}"
    if ob.status not in obligations.SCHEDULED or (ob.last is not None and ob.last < store.day):
        return f"{what}: it has ended."
    day = obligations.next_instalment(store, ob)
    if day is None:
        return f"{what}: nothing more falls due on it."
    if day <= store.day + obligations.AHEAD_DAYS:
        return ""
    return f"{what}: the next payment on it is more than a week away."


def _own_wage(store: Store, act: Act, ob_id: str) -> bool:
    """Whether `ob_id` is a wage owed by a business in the town the principal owns: one they
    may pay by hand from another account of theirs."""
    ob = store.get("obligations", ob_id)
    return (ob.kind == "wage" and store.has("orgs", ob.payer) and store.get("orgs", ob.payer).owner == act.principal
            and not store.get("orgs", ob.payer).outside)


def _do_pay(store: Store, act: Act, cause: str) -> str:
    payee, name = _payee(store, act)
    cents = to_cents(act.args.get("amount"))
    note = _note(act.args.get("note"))
    memo = quoted(note) if note else f"payment to {name}"
    source, ref = _source(store, act), _pay_ref(act)
    if _toward_nothing(act):                       # a gift or a bonus: counted toward nothing
        obligations.pay(store, source, payee, cents, memo, cause, match=False)
        return f"Paid {money(cents)} to {name}. It was not counted toward anything owed."
    if ref is not None and store.get("obligations", ref).payer != source:     # their business's wage, by hand
        wage, ref = ref, None
    else:
        wage = None if ref is not None else jobs.wage_paid_by_hand(store, act.principal, source, payee)
    # a wage the principal's business owes, paid from another of their accounts, counts toward it
    matches = obligations.pay(store, source, payee, cents, memo, cause, ref=ref, toward=wage)
    told = f"Paid {money(cents)} to {name}."
    named = ref or (wage if _pay_ref(act) is not None else None)
    if named and not matches:
        told += _nothing_counted(store, named) or " It was not counted toward anything owed."
    elif not matches:
        told += " It was not counted toward anything owed."
    if matches:
        counted = []
        for m in matches:
            due = store.get("dues", m.due)
            counted.append(f"{memo_shown(store.get('obligations', due.obligation).memo)} due {date_text(due.day)}")
        told += " It was counted toward " + "; ".join(counted) + "."
    return told


# standing_payment

def _weeks(value: Any) -> int | None:
    if value is None:
        return 0
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_WEEKS:
        return None
    return value


def _start(store: Store, act: Act) -> tuple[int | None, bool]:
    """The start day written, or None if none was written; and whether it could be read."""
    start = act.args.get("start")
    if _blank(start):
        return None, True
    day = parse_date(start, store.day)
    return day, day is not None


def _check_standing(store: Store, act: Act) -> str | None:
    source, why = from_account(store, act)
    if source is None:
        return why
    payee, why = _payee(store, act)
    if payee is None:
        return why
    cents, why = _amount(act.args.get("amount"))
    if cents is None:
        return why
    if weekday(act.args.get("day")) is None:
        return f"'{shown(act.args.get('day'))}' is not a day of the week."
    if not _start(store, act)[1]:
        return unreadable(act.args.get('start'))
    if _weeks(act.args.get("weeks")) is None:
        return f"'{shown(act.args.get('weeks'))}' is not a number of weeks."
    if _too_long(_note(act.args.get("note")), NOTE_WORDS):
        return "the note is too long."
    return None


def _do_standing(store: Store, act: Act, cause: str) -> str:
    own = _source(store, act)
    payee, name = _payee(store, act)
    cents = to_cents(act.args.get("amount"))
    wd = weekday(act.args["day"])
    start, _ = _start(store, act)
    if start is None:
        first = store.day + (wd - dow(store.day) - 1) % 7 + 1
    else:
        first = start + (wd - dow(start)) % 7
    weeks = _weeks(act.args.get("weeks"))
    last = first + 7 * (weeks - 1) if weeks > 0 else None
    already = [o for o in store.all("obligations") if o.payer == own and o.payee == payee and o.status == "active"]
    note = _note(act.args.get("note"))
    memo = quoted(note) if note else f"standing payment to {name}"
    ob = obligations.create(store, kind="standing", payer=own, payee=payee, cents=cents, first=first, every=7,
                            last=last, memo=memo, ends="payer", made_by=act.actor, cause=cause)
    told = f"Set up ({ob}): {money(cents)} to {name} every {DAYS[wd]}, first on {date_text(first)}"
    if last is not None:
        told += f", last on {date_text(last)}"
    told += "."
    for other in already:
        when = obligations.next_instalment(store, other)
        if when is not None:
            told += (f" This is in addition to {memo_shown(other.memo)} ({other.id}), which already goes to them "
                     f"on {date_text(when)}.")
            break
    return told


# stop

# Other kinds of row `stop` may name, by table: `(check, do)` as an operation's, given an
# act whose `id` is a row of that table.
STOPPABLE: dict[str, tuple[Callable[[Store, Act], str | None], Callable[[Store, Act, str], str]]] = {}


def _stoppable(store: Store, act: Act) -> tuple | None:
    ob_id = act.args.get("id")
    return next((pair for table, pair in STOPPABLE.items() if isinstance(ob_id, str) and store.has(table, ob_id)),
                None)


def _check_stop(store: Store, act: Act) -> str | None:
    other = _stoppable(store, act)
    if other is not None:
        return other[0](store, act)
    ob_id = act.args.get("id")
    if (isinstance(ob_id, str) and store.has("obligations", ob_id)
            and controls(store, act.principal, store.get("obligations", ob_id).payer)
            and store.get("obligations", ob_id).status == "active"):
        return obligations.UNSTOPPABLE.get(store.get("obligations", ob_id).cannot_stop)
    return f"you have nothing with the id '{shown(ob_id)}'."


def _do_stop(store: Store, act: Act, cause: str) -> str:
    other = _stoppable(store, act)
    if other is not None:
        return other[1](store, act, cause)
    ob = store.get("obligations", act.args["id"])
    result = obligations.stop(store, ob.id, ob.payer, cause)
    if result == "breach" and ob.kind == "rent":
        return (f"That was your rent to {name_of(store, ob.payee)}. Stopping it is recorded as a breach. The rent "
                f"stays owed each week and is no longer taken from your account.")
    if result == "breach":
        return f"That was an agreement with {name_of(store, ob.payee)}. Stopping it is recorded as a breach."
    return "Stopped." if result == "stopped" else "You have left; the fee ends."


# message

def _addressee(store: Store, act: Act) -> tuple[str, str] | None:
    """Who a letter reaches and the org it was addressed to (or ""): a person, or the owner
    of an org in the town, or its contact if it has no owner; or, for an organisation in the
    writer's own records with no one in the town to read it (one outside the town, say), the
    organisation itself: it is sent and gets no answer (design 3.6). None if it comes back."""
    who = _resolve(store, act.args.get("to"), act.actor)
    if who is not None and store.has("people", who):
        return who, ""
    if who is not None and store.has("orgs", who):
        org = store.get("orgs", who)
        if (org.owner or org.contact) and not org.outside:
            return org.owner or org.contact, org.id
        if store.has("people", act.actor) and org.name in record_orgs(store, act.actor):
            return org.id, org.id
    return None


def _check_message(store: Store, act: Act) -> str | None:
    found = _addressee(store, act)
    if found is None:
        return f"your letter to '{shown(act.args.get('to'))}' came back undelivered."
    if found[1] and found[0] == act.actor:
        return "that is you."
    return _text(act.args.get("text"), "that is too long for one letter.", LETTER_WORDS)


def _do_message(store: Store, act: Act, cause: str) -> str:
    to, org = _addressee(store, act)
    text = clean_text(act.args["text"])
    if to == org:                       # no one in the town reads it: kept as sent, and never shown as news
        store.add("messages", Message(store.new_id("msg", cause), store.day, act.actor, to, text, seen=True, org=org),
                  cause)
        return f"Sent to {name_of_who(store, org)}." + writer_text(unknown_names(store, act.actor, "", text))
    message = Message(store.new_id("msg", cause), store.day, act.actor, to, text, org=org,
                      unknown=unknown_names(store, to, act.actor, text))
    store.add("messages", message, cause)
    return f"Sent to {name_of_who(store, org or to)}." + writer_text(unknown_names(store, act.actor, to, text))


# request

def _asked_cents(value: Any) -> int | None:
    """Nothing, or any amount that reads as zero, asks no money: 0. Otherwise the amount in
    cents, or None if it cannot be read."""
    if value is None or dollars(value) == 0:
        return 0
    return to_cents(value)


def _owed_on(store: Store, act: Act, who: str) -> str | None:
    """The obligation `for` names, if it is active, paid by an account `who` controls and owed
    to one the requester controls; else None."""
    ob_id = act.args.get("for")
    if not isinstance(ob_id, str) or not store.has("obligations", ob_id):
        return None
    ob = store.get("obligations", ob_id)
    if ob.status != "active" or not controls(store, who, ob.payer) or not controls(store, act.actor, ob.payee):
        return None
    return None if _ended_wage(store, ob.id) else ob.id


def _check_request(store: Store, act: Act) -> str | None:
    to = act.args.get("to")
    who = _resolve(store, to, act.actor)
    if who is None:
        return f"no one you know is called '{shown(to)}'. Names are written in full, as they appear in your records."
    if not store.has("people", who):
        return f"{name_of_who(store, who)} is not a person."
    if who == act.actor:
        return "that is you."
    cents = _asked_cents(act.args.get("amount"))
    if cents is None:
        return f"'{shown(act.args.get('amount'))}' is not an amount of money that can be paid."
    if not _blank(act.args.get("for")):
        if _owed_on(store, act, who) is None:
            return f"'{shown(act.args.get('for'))}' is not something {name_of_who(store, who)} owes you."
    elif cents > 0 and account_of(store, who) == _own(store, act):
        return "that is your own household."
    return _text(act.args.get("text"), "that is too long for one request.", REQUEST_WORDS)


def _do_request(store: Store, act: Act, cause: str) -> str:
    to = _person_to(store, act)
    ob = "" if _blank(act.args.get("for")) else _owed_on(store, act, to)
    rq = Request(store.new_id("rq", cause), store.day, act.actor, to, clean_text(act.args["text"]),
                 _asked_cents(act.args.get("amount")), store.day + REQUEST_DAYS, ob=ob)
    store.add("requests", rq, cause)
    said = f"you ask them for {money(rq.cents)}" if rq.cents else "you ask them for no money"
    if rq.cents and ob:
        said += f" toward {memo_shown(store.get('obligations', ob).memo)} ({ob})"
    said += "."
    return (f"Sent to {name_of_who(store, to)} ({rq.id}): {said} It stays open until {date_in(rq.expires, store.day)}."
            + writer_text(unknown_names(store, act.actor, to, rq.text)))


# answer

def _open_request(store: Store, act: Act) -> Request | None:
    rq_id = act.args.get("request")
    if not isinstance(rq_id, str) or not store.has("requests", rq_id):
        return None
    rq = store.get("requests", rq_id)
    if rq.to != act.principal or rq.status != "open" or store.day > rq.expires:
        return None
    return rq


def _answer(act: Act) -> str | None:
    answer = act.args.get("answer")
    if isinstance(answer, str) and answer.strip().lower() in ("accept", "decline"):
        return answer.strip().lower()
    return None


def _check_answer(store: Store, act: Act) -> str | None:
    rq = _open_request(store, act)
    if rq is None:
        return f"you have no open request with the id '{shown(act.args.get('request'))}'."
    answer = _answer(act)
    if answer is None:
        return f"'{shown(act.args.get('answer'))}' is not an answer to a request."
    if _too_long(_note(act.args.get("note"))):
        return "the note is too long."
    if answer == "accept" and rq.kind in ANSWERERS:
        return ANSWERERS[rq.kind][0](store, act, rq)
    if answer == "accept" and rq.cents > 0:
        payer, payee = _request_accounts(store, rq)
        if rq.ob and not controls(store, act.principal, payer):
            return f"{rq.id} asks for money owed by an account that is not yours."
        if payee == payer:
            return "that is your own household."
        owed = _owed_after_end(store, rq)
        if owed == 0:
            return (f"that was for {memo_shown(store.get('obligations', rq.ob).memo)}, which has ended with "
                    f"nothing owed.")
        return _short_of(store, payer, rq.cents if owed is None else min(rq.cents, owed))
    return None


def _owed_after_end(store: Store, rq: Request) -> int | None:
    """What is still owed on the obligation a request names when that obligation has ended or
    been stopped (its arrears); None when it names none, or one still in force."""
    if not rq.ob:
        return None
    ob = store.get("obligations", rq.ob)
    if ob.status in obligations.SCHEDULED and (ob.last is None or ob.last >= store.day):
        return None
    return obligations.outstanding(store, ob)


def _request_accounts(store: Store, rq: Request) -> tuple[str, str]:
    """Who pays and who is paid when a request for money is accepted: the obligation's payer
    and payee when it names one, else the two people's households."""
    if rq.ob:
        ob = store.get("obligations", rq.ob)
        return ob.payer, ob.payee
    return account_of(store, rq.to), account_of(store, rq.sender)


def _do_answer(store: Store, act: Act, cause: str) -> str:
    rq = _open_request(store, act)
    accepted = _answer(act) == "accept"
    store.set("requests", rq.id, cause, status="accepted" if accepted else "declined")
    told = "Accepted." if accepted else "Declined."
    done = ANSWERERS[rq.kind][1](store, act, rq, cause) if accepted and rq.kind in ANSWERERS else ""
    if done:
        told += " " + done
    elif accepted and rq.cents > 0:
        payer, payee = _request_accounts(store, rq)
        owed = _owed_after_end(store, rq)              # ended: only toward what is still owed on it
        cents = rq.cents if owed is None else min(rq.cents, owed)
        if rq.ob:
            matches = obligations.pay(store, payer, payee, cents, f"request {rq.id}", cause, ref=rq.ob)
        else:
            matches = obligations.pay(store, payer, payee, cents, f"request {rq.id}", cause, match="arrears")
        paid = rq.sender if payee == account_of(store, rq.sender) else payee
        if owed is not None:
            told += (f" Paid {money(cents)} to {name_of_who(store, paid)} toward what is still owed on "
                     f"{memo_shown(store.get('obligations', rq.ob).memo)}")
            told += f"; the {money(rq.cents - cents)} more asked was not taken." if cents < rq.cents else "."
        else:
            told += f" Paid {money(cents)} to {name_of_who(store, paid)}."
            if rq.ob and not matches:
                told += _nothing_counted(store, rq.ob)
    text = f"{name_of_who(store, act.actor)} {'accepted' if accepted else 'declined'} your request {rq.id}"
    if rq.text.strip():
        text += f" ({quoted(cut(rq.text))})."
    else:                       # nothing written: named by the first sentence of its summary, if any,
        first = rq.summary.split(". ")[0].rstrip(".") if rq.summary.strip() else ""
        if done and rq.summary.strip() and rq.summary in done:      # unless what was done tells it whole
            first = ""
        text += "." + (f" {first}." if first else "")
    if done:
        text += " " + done
    note = _note(act.args.get("note"))
    if note:
        text += f" They wrote: {quoted(note)}"
    store.add("notices", Notice(store.new_id("n", cause), store.day, rq.sender, text), cause)
    return told


# read

def _what(act: Act) -> str | None:
    what = act.args.get("what")
    if isinstance(what, str) and what.strip().lower() in _READS:
        return what.strip().lower()
    return None


def _check_read(store: Store, act: Act) -> str | None:
    source, why = from_account(store, act)
    if source is None:
        return why
    return None if _what(act) is not None else f"'{shown(act.args.get('what'))}' is not something that can be read."


def _do_read(store: Store, act: Act, cause: str) -> str:
    own = _source(store, act)
    what = _what(act)
    if what == "agreements":
        return agreements_text(store, own)
    if what == "account in full":
        return statement_text(store, statement(store, own, back=28, max_lines=60))
    return statement_text(store, statement(store, own))


# write_notes

def _check_notes(store: Store, act: Act) -> str | None:
    text = clean_text(act.args.get("text"))
    if text is None:
        return "there was nothing written."
    return "that is too long for your notes." if _too_long(text, NOTES_WORDS) else None


def _do_notes(store: Store, act: Act, cause: str) -> str:
    store.set("people", act.actor, cause, notes=clean_text(act.args["text"]))
    return "Noted."


for _spec in (
    OpSpec("pay", '{"op": "pay", "to": "<name>", "amount": <dollars>, "note": "<what for>", '
                  '"for": "<id of what it is owed on, if any; none for a payment that is not toward anything owed>"}',
           "Pay money once.", 0.25, _check_pay, _do_pay, 10, takes_from=True),
    OpSpec("standing_payment", '{"op": "standing_payment", "to": "<name>", "amount": <dollars>, '
                               '"day": "<day of the week>", "start": "<first date, e.g. Monday 13 April>", '
                               '"weeks": <number, or 0 for until stopped>, "note": "<what for>"}',
           "Set up a weekly payment that goes out by itself.", 0.25,
           _check_standing, _do_standing, 20, takes_from=True),
    OpSpec("stop", '{"op": "stop", "id": "<id of the payment or agreement>"}',
           "Stop a payment or agreement.", 0.1, _check_stop, _do_stop, 30),
    OpSpec("message", '{"op": "message", "to": "<name>", "text": "<what you write>"}',
           "Write a letter to a person in the town.", 0.25, _check_message, _do_message, 40),
    OpSpec("request", '{"op": "request", "to": "<name>", "text": "<what you ask for>", '
                      '"amount": <dollars they would pay you, or 0>, "for": "<id of what they owe you, if any>"}',
           "Ask a person for something, with or without money.", 0.25, _check_request, _do_request, 50),
    OpSpec("answer", '{"op": "answer", "request": "<id>", "answer": "accept" | "decline", '
                     '"note": "<optional words>"}',
           "Accept or decline a request made to you.", 0.1, _check_answer, _do_answer, 60),
    OpSpec("read", '{"op": "read", "what": "account" | "account in full" | "agreements"}',
           "Read an account or what is owed.", 0.1, _check_read, _do_read, 70, takes_from=True),
    OpSpec("write_notes", '{"op": "write_notes", "text": "<for yourself>"}',
           "Replace your own notes.", 0.1, _check_notes, _do_notes, 80),
):
    register(_spec)


# the operations of work register themselves, and their kinds of request, from `ops_work`;
# after a reload of this module the kinds are registered again from there
from townsim.town import ops_work as _ops_work  # noqa: E402

ANSWERERS.update(getattr(_ops_work, "ANSWERERS_OF_WORK", {}))

# and so do the operations of agreements, loans and rent, from `ops_deals`
from townsim.town import ops_deals as _ops_deals  # noqa: E402

ANSWERERS.update(getattr(_ops_deals, "ANSWERERS_OF_DEALS", {}))

# and so do the operations of talking, from `ops_talk`, with the appointments `stop` cancels
from townsim.town import ops_talk as _ops_talk  # noqa: E402

STOPPABLE.update(getattr(_ops_talk, "STOPPABLE_OF_TALK", {}))
