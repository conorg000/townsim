"""The first eight operations: paying, standing payments, stopping, letters, requests and
their answers, reading the account, and notes. Registered when this module is imported.

Each `check` refuses anything its arguments do not support, so each `do` only acts.
Arguments come from a reply and may be any JSON value.

An act is carried out for its principal: money moves from an account the principal
controls (their household's unless a `from` names a business they own), and requests
answered are those made to the principal. Letters and requests are sent by the actor,
and notes are the actor's own. Names are resolved as the actor knows people.
"""

from __future__ import annotations

import re
from typing import Any

from townsim.town import obligations
from townsim.town.acts import (LONGEST_NAME, Act, OpSpec, account_of, clean_text, controls, cut, dollars, fits,
                               from_account, knows, name_of_who, register, resolve, shown, to_cents)
from townsim.town.clock import DAYS, date_text, dow, parse_date, weekday
from townsim.town.entities import Message, Notice, Request
from townsim.town.names import FIRST_NAMES
from townsim.town.obligations import money, name_of
from townsim.town.statement import agreements_text, statement, statement_text
from townsim.town.store import Store

MAX_WORDS = 80            # a note on an answer
NOTE_WORDS = 12           # a note on a payment, which becomes its memo: a label
LETTER_WORDS = 150
REQUEST_WORDS = 80
NOTES_WORDS = 150
REQUEST_DAYS = 14
MAX_WEEKS = 520
_READS = ("account", "account in full", "agreements")


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
        return None, f"no one called '{shown(to)}' is known in the town."
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

def _pay_ref(act: Act) -> Any:
    ref = act.args.get("for")
    return None if _blank(ref) else ref


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
                and store.get("obligations", ref).payer == source and store.get("obligations", ref).payee == payee):
            return f"you have nothing with the id '{shown(ref)}' that is owed to {name}."
    return _short_of(store, source, cents)


def _do_pay(store: Store, act: Act, cause: str) -> str:
    payee, name = _payee(store, act)
    cents = to_cents(act.args.get("amount"))
    memo = _note(act.args.get("note")) or f"payment to {name}"
    matches = obligations.pay(store, _source(store, act), payee, cents, memo, cause, ref=_pay_ref(act))
    told = f"Paid {money(cents)} to {name}."
    if matches:
        counted = []
        for m in matches:
            due = store.get("dues", m.due)
            counted.append(f"{store.get('obligations', due.obligation).memo} due {date_text(due.day)}")
        told += " It was counted toward " + "; ".join(counted) + "."
    return told


# standing_payment

def _weeks(value: Any) -> int | None:
    if value is None:
        return 0
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
        return f"'{shown(act.args.get('start'))}' is not a date that can be read."
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
    memo = _note(act.args.get("note")) or f"standing payment to {name}"
    ob = obligations.create(store, kind="standing", payer=own, payee=payee, cents=cents, first=first, every=7,
                            last=last, memo=memo, ends="payer", made_by=act.actor, cause=cause)
    told = f"Set up ({ob}): {money(cents)} to {name} every {DAYS[wd]}, first on {date_text(first)}"
    if last is not None:
        told += f", last on {date_text(last)}"
    told += "."
    for other in already:
        when = obligations.next_instalment(store, other)
        if when is not None:
            told += (f" This is in addition to {other.memo} ({other.id}), which already goes to them "
                     f"on {date_text(when)}.")
            break
    return told


# stop

def _check_stop(store: Store, act: Act) -> str | None:
    ob_id = act.args.get("id")
    if (isinstance(ob_id, str) and store.has("obligations", ob_id)
            and controls(store, act.principal, store.get("obligations", ob_id).payer)
            and store.get("obligations", ob_id).status == "active"):
        return None
    return f"you have nothing with the id '{shown(ob_id)}'."


def _do_stop(store: Store, act: Act, cause: str) -> str:
    ob = store.get("obligations", act.args["id"])
    result = obligations.stop(store, ob.id, ob.payer, cause)
    if result == "breach":
        return f"That was an agreement with {name_of(store, ob.payee)}. Stopping it is recorded as a breach."
    return "Stopped." if result == "stopped" else "You have left; the fee ends."


# message

def _addressee(store: Store, act: Act) -> tuple[str, str] | None:
    """Who a letter reaches and the org it was addressed to (or ""): a person, or the owner
    of an org in the town, or its contact if it has no owner. None if it comes back."""
    who = _resolve(store, act.args.get("to"), act.actor)
    if who is not None and store.has("people", who):
        return who, ""
    if who is not None and store.has("orgs", who):
        org = store.get("orgs", who)
        if (org.owner or org.contact) and not org.outside:
            return org.owner or org.contact, org.id
    return None


def _check_message(store: Store, act: Act) -> str | None:
    found = _addressee(store, act)
    if found is None:
        return f"your letter to '{shown(act.args.get('to'))}' came back undelivered."
    if found[1] and found[0] == act.actor:
        return "that is you."
    return _text(act.args.get("text"), "that is too long for one letter.", LETTER_WORDS)


_PAIR = re.compile(r"(?=\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b)")
_FIRST = frozenset(FIRST_NAMES)


def unknown_names(store: Store, reader: str, sender: str, text: str) -> list[str]:
    """The names in `text` that are no one `reader` knows, in the order they first appear:
    every two capitalised words in a row whose first is a first name people here bear and
    which are not the full name of the reader, the sender, anyone the reader knows or anyone
    whose request the reader has been shown."""
    shown = [rq.sender for rq in store.all("requests") if rq.to == reader and rq.seen]
    known = {store.get("people", p).name.casefold() for p in [reader, *knows(store, reader), *shown]
             if store.has("people", p)}
    if store.has("people", sender):
        known.add(store.get("people", sender).name.casefold())
    found: list[str] = []
    for m in _PAIR.finditer(text):
        name = f"{m.group(1)} {m.group(2)}"
        if m.group(1) in _FIRST and name.casefold() not in known and name not in found:
            found.append(name)
    return found


def _do_message(store: Store, act: Act, cause: str) -> str:
    to, org = _addressee(store, act)
    text = clean_text(act.args["text"])
    message = Message(store.new_id("msg", cause), store.day, act.actor, to, text, org=org,
                      unknown=unknown_names(store, to, act.actor, text))
    store.add("messages", message, cause)
    return f"Sent to {name_of_who(store, org or to)}."


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
    return ob.id


def _check_request(store: Store, act: Act) -> str | None:
    to = act.args.get("to")
    who = _resolve(store, to, act.actor)
    if who is None:
        return f"no one called '{shown(to)}' is known in the town."
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
    return f"Sent to {name_of_who(store, to)} ({rq.id}). It stays open until {date_text(rq.expires)}."


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
    if answer == "accept" and rq.cents > 0:
        payer, payee = _request_accounts(store, rq)
        if rq.ob and not controls(store, act.principal, payer):
            return f"{rq.id} asks for money owed by an account that is not yours."
        if payee == payer:
            return "that is your own household."
        return _short_of(store, payer, rq.cents)
    return None


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
    if accepted and rq.cents > 0:
        payer, payee = _request_accounts(store, rq)
        if rq.ob:
            obligations.pay(store, payer, payee, rq.cents, f"request {rq.id}", cause, ref=rq.ob)
        else:
            obligations.pay(store, payer, payee, rq.cents, f"request {rq.id}", cause, match="arrears")
        paid = rq.sender if payee == account_of(store, rq.sender) else payee
        told += f" Paid {money(rq.cents)} to {name_of_who(store, paid)}."
    asked = cut(rq.text)
    text = (f"{name_of_who(store, act.actor)} {'accepted' if accepted else 'declined'} "
            f"your request {rq.id} ({asked}).")
    note = _note(act.args.get("note"))
    if note:
        text += f" They wrote: {note}"
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
                  '"for": "<id of what it is owed on, if any>"}',
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
