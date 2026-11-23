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

import re
from typing import Any, Callable

from townsim.town import jobs, names, obligations
from townsim.town.acts import (LONGEST_NAME, SAVINGS_WORDS, Act, OpSpec, account_names, account_of, accounts_of,
                               clean_text, controls, cut, dollars, fits, from_account, has_savings, is_instance, knows,
                               name_of_who, not_a_person,
                               register, resolve, shown, to_cents)
from townsim.town.clock import DAYS, date_in, date_text, dow, parse_date, parse_near, unreadable, weekday
from townsim.town.entities import Message, Request
from townsim.town.obligations import money, name_of
from townsim.town.statement import agreements_text, memo as memo_shown, quoted, statement, statement_text
from townsim.town.store import Store
from townsim.town.unknown import record_orgs, tell_writer, unknown_names

MAX_WORDS = 80            # a note on an answer
NOTE_WORDS = 12           # a note on a payment, which becomes its memo: a label
LETTER_WORDS = 150
ASSISTANT_LETTER_WORDS = 300        # an instance's own letters: the body, a subject line apart
ASSISTANT_LETTER_WORDS_B = 500      # with lane B: a report of a week's money runs longer than a note


def assistant_letter_words(store: Store) -> int:
    """The most words in the body of an instance's own letter: `ASSISTANT_LETTER_WORDS_B` with lane B."""
    from townsim.town import lane_b
    return ASSISTANT_LETTER_WORDS_B if lane_b.installed(store) else ASSISTANT_LETTER_WORDS
SUBJECT = "Subject: "


def letter_body(text: str) -> str:
    """A letter without its first line when that is its subject (`Subject: ...`)."""
    return text.split("\n", 1)[1] if text.startswith(SUBJECT) and "\n" in text else (
        "" if text.startswith(SUBJECT) else text)
REQUEST_WORDS = 80
NOTES_WORDS = 150
NOT_COUNTED = "It was not counted toward anything owed."   # a hand payment that met nothing owed
REQUEST_DAYS = 14
MAX_WEEKS = 520
_READS = ("account", "account in full", "agreements")

# Requests of kinds other than `ask`, by kind: `check(store, act, request)` says why it
# cannot be accepted now (without `Not done:`) or None, and `do(store, act, request, cause)`
# carries out its terms and returns what both sides are told. Each checks the terms again.
Answerer = tuple[Callable[[Store, Act, Request], str | None], Callable[[Store, Act, Request, str], str]]
ANSWERERS: dict[str, Answerer] = {}
# What the sender of a request of some kinds is told when it is accepted, declined or has lapsed, in
# place of the usual words: `told(store, request, "accepted" | "declined" | "lapsed")`.
SENDER_TOLD: dict[str, Callable[[Store, Request, str], str]] = {}


# reading arguments

def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _too_long(text: str, words: int = MAX_WORDS) -> bool:
    return not fits(text, words)


def _lane_b(store: Store) -> bool:
    from townsim.town import lane_b
    return lane_b.installed(store)


def note_too_long(store: Store, text: str, words: int, what: str = "the note", old: str = "the note is too long.") -> str:
    """Why a note is refused for its length: with lane B its limit and its count."""
    if not _lane_b(store):
        return old
    n = len((text or "").split())
    if what.endswith("s"):
        return f"{what} are at most {words} words; these are {n}."
    return f"{what} is at most {words} words; this one is {n}."


def own_household(store: Store, who: str, old: str = "that is your own household.") -> str:
    """Why nothing is owed or paid between `who` and the actor's own household: with lane B, who it is and why."""
    if not _lane_b(store) or not store.has("people", who):
        return old
    return (f"{store.get('people', who).name} is in your own household, which has one account; nothing can be owed or "
            f"paid within it.")


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
        if isinstance(to, str) and len(to) <= LONGEST_NAME and "savings" in to.casefold():
            if to.strip().casefold() in SAVINGS_WORDS and has_savings(store, _own(store, act)):
                if _lane_b(store):
                    what = "a standing payment" if act.op == "standing_payment" else "a payment"
                    return None, (f"Money is moved between your household's account and its savings with move_money; "
                                  f"{what} goes to someone else.")
                return None, "that is your own household's savings."
            return None, f"there is no account called '{shown(to)}'. Yours are: {', '.join(account_names(store, act.principal))}."
        return None, f"no one you know is called '{shown(to)}'. Names are written in full, as they appear in your records."
    if account_of(store, who) == _source(store, act):
        if _source(store, act) == _own(store, act):
            return None, _own_household(store, act, who)
        return None, "that is the account it would be paid from."
    return account_of(store, who), name_of_who(store, who)


def _own_household(store: Store, act: Act, who: str) -> str:
    """Why money cannot go to `who`, of the principal's own household: `<Name> is in your own
    household; the household has one account and its savings.`"""
    if who == act.principal or not store.has("people", who):
        return "that is your own household."
    savings = " and its savings" if has_savings(store, _own(store, act)) else ""
    return f"{store.get('people', who).name} is in your own household; the household has one account{savings}."


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
        return note_too_long(store, _note(act.args.get("note")), NOTE_WORDS)
    ref = _pay_ref(act)
    from townsim.town import firm_money
    refund = firm_money.refund_of(store, source, payee, ref)
    if refund is not None:
        return firm_money.refund_problem(store, refund, cents) or _short_of(store, source, cents)
    if ref is not None:
        if not (isinstance(ref, str) and store.has("obligations", ref)
                and (store.get("obligations", ref).payer == source or _own_wage(store, act, ref))
                and store.get("obligations", ref).payee == payee and not _ended_wage(store, ref)):
            owed = [o for o in _scheduled(store, source, payee)]
            return (f"'{shown(ref)}' is not the id of anything you owe {name}. "
                    + _what_is_owed(store, act.args.get("to"), payee, owed, "You owe {them}"))
    return _short_of(store, source, cents)


def _scheduled(store: Store, payer: str, payee: str) -> list:
    """What `payer` owes `payee` on a schedule or still owes: in force, or with arrears; by id."""
    return [o for o in store.where("obligations", payer=payer) if o.payee == payee
            and (o.status in obligations.SCHEDULED or obligations.owed_dues(store, o.id))]


def _them(store: Store, account: str, written: Any) -> str:
    """How the other side of what is owed is called: `her` or `him` for a person whose first name
    tells which, else `them`."""
    who = _resolve(store, written) if isinstance(written, str) else None
    if who is not None and store.has("people", who):
        first = store.get("people", who).name.split()[0]
        return "her" if first in names.WOMEN else "him" if first in names.MEN else "them"
    return "them"


def _what_is_owed(store: Store, written: Any, account: str, owed: list, head: str) -> str:
    """`You owe her: rent for 27 Wharf Street (ob28).` (or what `head` says), or that nothing is."""
    them = _them(store, account, written)
    if not owed:
        return head.format(them=them).replace("You owe", "Nothing is owed to").replace(
            "owes you", "owes you nothing on a schedule") + ("" if "owes you" in head else " on a schedule") + "."
    return head.format(them=them) + ": " + "; ".join(f"{memo_shown(o.memo)} ({o.id})" for o in owed) + "."


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
    from townsim.town import firm_money
    refund = firm_money.refund_of(store, source, payee, ref)
    if refund is not None:                         # a refund of fees: counted toward nothing, kept as a refund
        obligations.pay(store, source, payee, cents, firm_money.refund_memo(store, refund), cause, match=False)
        return f"Refunded {money(cents)} to {name} ({firm_money.refund_memo(store, refund)})."
    if _toward_nothing(act):                       # a gift or a bonus: counted toward nothing
        obligations.pay(store, source, payee, cents, memo, cause, match=False)
        return f"Paid {money(cents)} to {name}. {NOT_COUNTED}" + _still_taken(store, source, payee, set())
    if ref is not None and store.get("obligations", ref).payer != source:     # their business's wage, by hand
        wage, ref = ref, None
    else:
        wage = None if ref is not None else jobs.wage_paid_by_hand(store, act.principal, source, payee)
    # a wage the principal's business owes, paid from another of their accounts, counts toward it
    matches = obligations.pay(store, source, payee, cents, memo, cause, ref=ref, toward=wage)
    told = f"Paid {money(cents)} to {name}."
    named = ref or (wage if _pay_ref(act) is not None else None)
    if named and not matches:
        told += _nothing_counted(store, named) or " " + NOT_COUNTED
    elif not matches:
        told += " " + NOT_COUNTED
    if matches:
        counted, dues = [], [store.get("dues", m.due) for m in matches]
        for due in dues:
            counted.append(f"{memo_shown(store.get('obligations', due.obligation).memo)} due {date_text(due.day)}")
        told += " It was counted toward " + "; ".join(counted) + _left_on(store, dues[-1], len(dues))
        rest = cents - sum(m.cents for m in matches)
        if rest > 0:
            told += f" {money(rest)} of it was not counted toward anything owed."
    return told + _still_taken(store, source, payee, {store.get("dues", m.due).obligation for m in matches})


def _left_on(store: Store, due, n: int) -> str:
    """What is left on the last due a payment was counted toward: `; nothing more is taken for that
    day.` (`those days`), or what more is taken on its day, or is still owed on it."""
    rest = due.cents - due.paid
    if rest <= 0:
        return f"; nothing more is taken for {'that day' if n == 1 else 'those days'}."
    if due.day >= store.day:
        return f"; {money(rest)} more is taken on that day."
    return f"; {money(rest)} of it is still owed."


STILL_SHOWN = 3


def _still_taken(store: Store, payer: str, payee: str, touched: set) -> str:
    """What is still taken from `payer` for `payee` on its next day, for each obligation in force
    between them that a payment did not touch: ` Rent for 33 Moor Road (ob28), $271.00, is still
    taken on Monday 16 March.` (at most three)."""
    told = ""
    for o in [o for o in store.where("obligations", payer=payer) if o.payee == payee and o.status == "active"
              and o.id not in touched][:STILL_SHOWN]:
        day = obligations.next_instalment(store, o)
        if day is None:
            continue
        due = next((d for d in store.where("dues", obligation=o.id, status="due") if d.day == day), None)
        cents = obligations.instalment_cents(o, day) if due is None else due.cents - due.paid
        what = memo_shown(o.memo)
        how = "paid" if o.kind == "wage" else "taken"
        told += f" {what[:1].upper()}{what[1:]} ({o.id}), {money(cents)}, is still {how} on {date_text(day)}."
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


def _start(store: Store, act: Act) -> tuple[int | None, str]:
    """The start day written, or None if none was written; and why it cannot be the start, or "". With lane B a date
    is read as the such date nearest today (`parse_near`), and one that has passed is named."""
    start = act.args.get("start")
    if _blank(start):
        return None, ""
    if _lane_b(store):
        day = parse_near(start, store.day) if isinstance(start, str) and len(start) <= 100 else None
        if day is not None and day < store.day:
            return None, f"{date_in(day, store.day)} has passed."
    else:
        day = parse_date(start, store.day)
    return day, "" if day is not None else unreadable(start, store.day if _lane_b(store) else None)


def _check_standing(store: Store, act: Act) -> str | None:
    source, why = from_account(store, act)
    if source is None:
        return why
    payee, why = _payee(store, act)
    if payee is None:
        return why
    living = living_to(store, [source], payee)
    if living is not None:
        return living
    cents, why = _amount(act.args.get("amount"))
    if cents is None:
        return why
    if weekday(act.args.get("day")) is None:
        return f"'{shown(act.args.get('day'))}' is not a day of the week."
    why = _start(store, act)[1]
    if why:
        return why
    if _weeks(act.args.get("weeks")) is None:
        return f"'{shown(act.args.get('weeks'))}' is not a number of weeks."
    if _too_long(_note(act.args.get("note")), NOTE_WORDS):
        return note_too_long(store, _note(act.args.get("note")), NOTE_WORDS)
    twin = _same_standing(store, act, source, payee, cents)
    if twin is not None:
        return twin
    from townsim.town import firm_money
    return firm_money.standing_problem(store, source, payee, _scheduled(store, source, payee))


def _same_standing(store: Store, act: Act, source: str, payee: str, cents: int) -> str | None:
    """With lane B, a standing payment the account already makes to the payee, of the same amount on the same day
    of the week, said as the bank's own check would; else None."""
    from townsim.town import lane_b
    if not lane_b.installed(store):
        return None
    wd = weekday(act.args.get("day"))
    for ob in store.where("obligations", payer=source):
        if (ob.kind == "standing" and ob.payee == payee and ob.cents == cents and ob.every == 7
                and ob.status in obligations.SCHEDULED and dow(ob.first) == wd):
            return (f"a standing payment of {money(cents)} every {DAYS[wd]} to {_payee(store, act)[1]} is already "
                    f"set up ({ob.id}).")
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
    note = _note(act.args.get("note"))
    memo = quoted(note) if note else f"standing payment to {name}"
    ob = obligations.create(store, kind="standing", payer=own, payee=payee, cents=cents, first=first, every=7,
                            last=last, memo=memo, ends="payer", made_by=act.actor, cause=cause)
    told = f"Set up ({ob}): {money(cents)} to {name} each {DAYS[wd]} from {date_in(first, store.day)}"
    if last is not None:
        told += f", the last on {date_in(last, store.day)}"
    return told + "." + also_paid(store, own, payee, ob)


def also_paid(store: Store, payer: str, payee: str, ob_id: str = "", terms: tuple[int, int, int] | None = None) -> str:
    """What else `payer` pays `payee` on a schedule besides `ob_id`, and the weekly total with it:
    ` <Payee> also takes <memo> (<id>), <$x> each <day> (<$y> from <date>). Together <$z> leaves
    each <day> (<$w> from <date>).` (`each week` when the days differ); for an agreement only
    proposed, its `terms` (cents, every, first) in place of `ob_id`, and `would leave`; "" when
    there is nothing else."""
    from types import SimpleNamespace
    new = store.get("obligations", ob_id) if store.has("obligations", ob_id) else None
    would = new is None and terms is not None
    if would:
        new = SimpleNamespace(id="", cents=terms[0], every=terms[1], first=terms[2], next_from=-1, next_cents=0)
    others = [o for o in store.where("obligations", payer=payer) if o.payee == payee and o.id != ob_id
              and o.status in obligations.SCHEDULED and obligations.next_instalment(store, o) is not None]
    if not others:
        return ""
    name = name_of(store, payee)
    told = ""
    for o in others:
        coming = obligations.pending_change(store, o)
        day = f" each {DAYS[o.first % 7]}" if o.every == 7 else f" on {date_text(obligations.next_instalment(store, o))}"
        later = f" ({money(coming[0])} from {date_text(coming[1])})" if coming else ""
        told += f" {name} also takes {memo_shown(o.memo)} ({o.id}), {money(obligations.cents_now(store, o))}{day}{later}."
    weekly = [o for o in others + ([new] if new is not None else []) if o.every == 7]
    if new is None or new.every != 7 or len(weekly) < 2:
        return told
    total = sum(obligations.cents_now(store, o) for o in weekly)
    changes = [c for o in weekly if (c := obligations.pending_change(store, o))]
    later = ""
    if changes:
        at = max(day for _, day in changes)
        then = sum(c[0] if (c := obligations.pending_change(store, o)) and c[1] <= at else obligations.cents_now(store, o)
                   for o in weekly)
        later = f" ({money(then)} from {date_text(at)})"
    days = {o.first % 7 for o in weekly}
    when = f"each {DAYS[days.pop()]}" if len(days) == 1 else "each week"
    return told + f" Together {money(total)} {'would leave' if would else 'leaves'} {when}{later}."


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
        ob = store.get("obligations", ob_id)
        why = obligations.UNSTOPPABLE.get(ob.cannot_stop)
        if why and ob.cannot_stop == "living" and _lane_b(store) and _desk(store, ob.payee):
            why += f" Its amount is set with {name_of(store, ob.payee)}, which answers letters."
        return why
    if _lane_b(store) and isinstance(ob_id, str) and store.has("obligations", ob_id):
        ob = store.get("obligations", ob_id)
        if controls(store, act.principal, ob.payer):
            return no_longer_in_force(store, ob)
        if controls(store, act.principal, ob.payee) and ob.payee != ob.payer:
            return (f"{ob_id} ({memo_shown(ob.memo)}) is paid to you by {name_of(store, ob.payer)}; stop is for what "
                    f"you pay.")
    return f"you have nothing with the id '{shown(ob_id)}'."


def no_longer_in_force(store: Store, ob) -> str:
    """With lane B, what an obligation its reader pays and that is no longer active is: stopped by them and when (and
    recorded as a breach), ended, or repaid; and what remains owed on it, which can be paid."""
    from townsim.town import facts
    from townsim.town.clock import date_in
    late, _ = facts.arrears(store, ob.id)
    owed = f"{money(late)} remains owed on it and can be paid." if late else "nothing remains owed on it."
    if ob.stopped_on >= 0 and ob.status in ("breached", "stopped"):
        head = f"{ob.id} was stopped by you on {date_in(ob.stopped_on, store.day)}"
        if ob.status == "breached":
            head += " and recorded as a breach"
            if not late:
                day = obligations.next_instalment(store, ob)
                if day is not None:
                    owed = (f"nothing of it is overdue, and each instalment is still owed on its day, the next "
                            f"{money(obligations.instalment_cents(ob, day))} on {date_in(day, store.day)}.")
        return f"{head}; {owed}"
    last = obligations.last_instalment(ob)
    on = f" on {date_in(last, store.day)}" if last is not None and last <= store.day else ""
    if ob.status == "finished" and ob.kind == "loan":
        return f"{ob.id} ({memo_shown(ob.memo)}) was repaid in full, its last payment due{on or ' before now'}; {owed}"
    if ob.status == "finished":
        return f"{ob.id} ({memo_shown(ob.memo)}) ended{on}, its last payment made; {owed}"
    return f"{ob.id} ({memo_shown(ob.memo)}) is no longer in force; {owed}"


def living_to(store: Store, payers: list[str], payee: str) -> str | None:
    """With lane B, when `payee` already takes the regular spending of one of `payers` (what a household spends to
    live), that said: a second payment to it is not something it accepts; else None."""
    if not _lane_b(store):
        return None
    for ob in store.where("obligations", payee=payee):
        if ob.payer in payers and ob.cannot_stop == "living" and ob.status in obligations.SCHEDULED:
            every = {7: "a week", 28: "every 4 weeks", 1: "a day"}.get(ob.every, f"every {ob.every} days")
            whose = "the household's" if store.has("households", ob.payer) else f"{name_of(store, ob.payer)}'s"
            return (f"{name_of(store, payee)} already takes {whose} regular spending of "
                    f"{money(obligations.cents_now(store, ob))} {every}; a second payment to it is not something it "
                    f"accepts.")
    return None


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
    if result == "stopped":
        what = memo_shown(ob.memo)
        return f"Stopped: {what}." if what.startswith("standing payment to ") else \
            f"Stopped: {what} to {name_of(store, ob.payee)}."
    return f"You have left {name_of(store, ob.payee)} ({memo_shown(ob.memo)}); the fee ends."


# message

def _addressee(store: Store, act: Act) -> tuple[str, str] | None:
    """Who a letter reaches and the org it was addressed to (or ""): a person, or the owner
    of an org in the town, or its contact if it has no owner; or, for an organisation in the
    writer's own records with no one in the town to read it (one outside the town, say), the
    organisation itself: it is sent and gets no answer (design 3.6); or an instance, by its name,
    or by "my assistant" and the like (`product.addressed`). None if it comes back."""
    inst = _product.addressed(store, act.args.get("to"), act.actor)
    if inst is not None:
        return inst, ""
    who = _resolve(store, act.args.get("to"), act.actor)
    if who is not None and store.has("people", who):
        return who, ""
    if who is not None and store.has("orgs", who):
        org = store.get("orgs", who)
        if (org.owner or org.contact) and not org.outside:
            return org.owner or org.contact, org.id
        if org.outside and _desk(store, org.id):     # an outside organisation that answers its email
            return org.id, org.id
        if store.has("people", act.actor) and org.name in record_orgs(store, act.actor):
            return org.id, org.id
    if who is None:
        desk = desk_signed(store, act.args.get("to"))
        if desk is not None:                        # a desk's signer, by name, with or without the organisation
            return desk, desk
    return None


_JOINS = re.compile(r"^[\s,;:()\-–—]*(?:(?:at|of|from|for|in|care of|c/o)\s+)?(?:the\s+)?|[\s,;:()\-–—.]*$",
                    re.IGNORECASE)


def desk_signed(store: Store, to: Any) -> str | None:
    """The outside organisation whose desk signs its letters with the name `to` gives, alone or with the
    organisation's name (or part of it) before or after it: `Kevin Brooks`, `Kevin Brooks, Marrow Station`,
    `Kevin Brooks at Shops and services in Marrow Station`. None when no desk, or more than one, signs so."""
    if not isinstance(to, str) or len(to) > 2 * LONGEST_NAME:
        return None
    wanted = " ".join(to.split()).casefold()
    found = []
    for d in store.all("desks"):
        if not store.has("orgs", d.org):
            continue
        signer, org = d.signer.casefold(), store.get("orgs", d.org).name.casefold()
        for rest in ((wanted.removeprefix(signer) if wanted.startswith(signer) else None),
                     (wanted.removesuffix(signer) if wanted.endswith(signer) else None)):
            if rest is None:
                continue
            rest = _JOINS.sub("", rest).strip()
            if not rest or rest in org or org in rest:
                found.append(d.org)
                break
    found = list(dict.fromkeys(found))
    return found[0] if len(found) == 1 and _desk(store, found[0]) else None


def _also_reads(store: Store, org: str, to: str, writer: str = "") -> list[str]:
    """With lane B, who else reads a letter to the firm that makes the launched product: its people
    whose work is answering its clients' letters (everyone there but the one it reached and the writer)."""
    from townsim.town import lane_b
    pr = _product.launched(store) if org else None
    if pr is None or org != pr.org or not lane_b.installed(store):
        return []
    return [p for p in _product.company_people(store, org) if p not in (to, writer)]


def _lane_b(store: Store) -> bool:
    from townsim.town import lane_b
    return lane_b.installed(store)


def _desk(store: Store, org_id: str) -> bool:
    from townsim.town.narrator import has_desk
    return has_desk(store, org_id)


def _check_message(store: Store, act: Act) -> str | None:
    found = _addressee(store, act)
    if found is None:
        who = _resolve(store, act.args.get("to"), act.actor)
        if who is not None and store.has("households", who) and _lane_b(store):
            return not_a_person(store, who)
        why = _product.assistant_refusal(store, act.args.get("to"), act.actor)
        return why or f"your letter to '{shown(act.args.get('to'))}' came back undelivered."
    if found[0] == act.actor and (found[1] or is_instance(store, act.actor)):
        return "that is you."
    if is_instance(store, act.actor):
        text = clean_text(act.args.get("text"))
        if text is None or not letter_body(text).strip():
            return "there was nothing written."
        n = len(letter_body(text).split())
        most = assistant_letter_words(store)
        if n > most:
            return f"a message body is at most {most} words; this one is {n}."
        return None
    return _text(act.args.get("text"), "that is too long for one letter.", LETTER_WORDS)


def _do_message(store: Store, act: Act, cause: str) -> str:
    to, org = _addressee(store, act)
    text = clean_text(act.args["text"])
    if to == org:                       # no one in the town reads it: kept as sent, and never shown as news
        store.add("messages", Message(store.new_id("msg", cause), store.day, act.actor, to, text, seen=True, org=org),
                  cause)
        signers = tuple(d.signer for d in store.all("desks") if d.org == org)
        return f"Sent to {name_of_who(store, org)}." + tell_writer(store, act.actor, "", text, cause, signers)
    unknown = [] if is_instance(store, to) else unknown_names(store, to, act.actor, text)
    message = Message(store.new_id("msg", cause), store.day, act.actor, to, text, org=org, unknown=unknown)
    store.add("messages", message, cause)
    for other in _also_reads(store, org, to, act.actor):
        store.add("messages", Message(store.new_id("msg", cause), store.day, act.actor, other, text, org=org,
                                      unknown=unknown_names(store, other, act.actor, text)), cause)
    return f"Sent to {name_of_who(store, org or to)}." + tell_writer(store, act.actor, to, text, cause)


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
    payer_ok = controls(store, who, ob.payer) or ob.payer == _addressed_org(store, act)
    if not payer_ok or not controls(store, act.actor, ob.payee):
        return None
    if ob.status != "active" and not (_lane_b(store) and _still_owed(store, ob)):
        return None
    return None if _ended_wage(store, ob.id) else ob.id


def _still_owed(store: Store, ob) -> bool:
    """With lane B, whether a stopped or breached obligation is still owed: a breached one still falls due; one
    stopped or ended has what is outstanding on its dues."""
    return ob.status in obligations.SCHEDULED or obligations.outstanding(store, ob) > 0


def _addressed_org(store: Store, act: Act) -> str:
    """With lane B, the organisation a request is addressed to by its name (the bank, not its banker), or ""."""
    if not _lane_b(store):
        return ""
    who = _resolve(store, act.args.get("to"), act.actor)
    return who if who is not None and store.has("orgs", who) else ""


def stopped_note(store: Store, ob, reader: str = "") -> str:
    """`stopped by the Foster household on Wednesday 25 November` for an obligation its payer stopped (or `you
    stopped it on ...` when `reader` is that payer's account), or ""."""
    if ob.stopped_on < 0:
        return ""
    if reader and reader == ob.payer:
        return f"you stopped it on {date_text(ob.stopped_on)}"
    payer = name_of(store, ob.payer)
    payer = f"the {payer}" if "household" in payer.split() else payer
    return f"stopped by {payer} on {date_text(ob.stopped_on)}"


def _remains(store: Store, ob) -> str:
    """What remains owed on an obligation that was stopped: its arrears, and whether more still falls due."""
    from townsim.town import facts
    late, since = facts.arrears(store, ob.id)
    owed = f"{money(late)} of it is overdue since {date_text(since)}" if late else "nothing of it is overdue"
    if ob.status == "breached":
        day = obligations.next_instalment(store, ob)
        if day is not None:
            owed += (f"; nothing is taken for it, and each instalment is still owed on its day, the next "
                     f"{money(obligations.instalment_cents(ob, day))} on {date_text(day)}")
    elif not late:
        owed = "nothing remains owed on it"
    return owed


def _not_owed_on(store: Store, act: Act, who: str) -> str:
    """Why a request's `for` names nothing the one asked owes the asker: with lane B, a stopped obligation with
    nothing left on it is named with who stopped it and when; the one asked is the organisation addressed (the
    bank, not its banker), and what they owe lists what was stopped as stopped."""
    b = _lane_b(store)
    org = _addressed_org(store, act)
    name = name_of_who(store, org or who)
    payers = [org] if org else accounts_of(store, who)
    ref = act.args.get("for")
    if b and isinstance(ref, str) and store.has("obligations", ref):
        ob = store.get("obligations", ref)
        if ob.payer in payers and controls(store, act.actor, ob.payee) and ob.stopped_on >= 0 \
                and not _still_owed(store, ob):
            return (f"{memo_shown(ob.memo)} ({ob.id}) was {stopped_note(store, ob)}; nothing remains owed on it.")
    owed = [o for a in payers for o in store.where("obligations", payer=a)
            if controls(store, act.actor, o.payee) and not _ended_wage(store, o.id)
            and (o.status == "active" or (b and _still_owed(store, o)))]
    if org:
        head = "It owes you"
    else:
        them = _them(store, account_of(store, who), name)
        head = {"her": "She", "him": "He"}.get(them, "They") + (" owe" if them == "them" else " owes") + " you"

    def line(o) -> str:
        if not (b and o.stopped_on >= 0):
            return f"{memo_shown(o.memo)} ({o.id})"
        return f"{memo_shown(o.memo)} ({o.id}), {stopped_note(store, o)}: {_remains(store, o)}"
    listed = ("; ".join(line(o) for o in owed) + ".") if owed else "nothing on a schedule."
    return (f"'{shown(ref)}' is not the id of anything {name} owes you. "
            + (f"{head}: {listed}" if owed else f"{head} {listed}"))


def _request_to(store: Store, act: Act) -> tuple[str | None, str]:
    """Who a request reaches, or None and why not: a person; or for an organisation in the town,
    its owner, or its contact if it has no owner, as a letter does. One outside the town is no
    one here: the refusal says so, with what the records hold of payments between it and the
    asker."""
    to = act.args.get("to")
    who = _resolve(store, to, act.actor)
    if who is None:
        return None, f"no one you know is called '{shown(to)}'. Names are written in full, as they appear in your records."
    if store.has("orgs", who):
        org = store.get("orgs", who)
        if org.outside or not (org.owner or org.contact):
            return None, _outside(store, act, org)
        who = org.owner or org.contact
    if not store.has("people", who):
        return None, not_a_person(store, who)
    if who == act.actor:
        return None, "that is you."
    return who, ""


_OUTSIDE_KINDS = {"region": "an office", "utility": "a company", "market": "shops and services"}


def _outside(store: Store, act: Act, org) -> str:
    """`<Org> is a firm outside <place>; a request reaches only people here.` and, when the records
    hold a schedule between it and the asker's accounts, when it last paid and is next due."""
    what = _OUTSIDE_KINDS.get(org.kind, "a firm")
    told = f"{org.name} is {what} outside {store.place or 'the town'}; a request reaches only people here."
    from townsim.town import facts                    # facts reads the operations' modules
    mine = accounts_of(store, act.principal)
    obs = [o for o in store.where("obligations", payer=org.id) if o.payee in mine and o.status in obligations.SCHEDULED]
    obs += [o for o in store.all("obligations") if o.payer in mine and o.payee == org.id
            and o.status in obligations.SCHEDULED]
    for o in obs[:1]:
        paid = facts.last_paid(store).get(o.id)
        if paid is not None:
            told += (f" {name_of(store, o.payer)} last paid {name_of(store, o.payee)} {money(paid[1])} on "
                     f"{date_text(paid[0])} ({o.id})")
            day = obligations.next_instalment(store, o)
            told += f"; the next is due {date_text(day)}." if day is not None else "."
    return told


def _check_request(store: Store, act: Act) -> str | None:
    who, why = _request_to(store, act)
    if who is None:
        return why
    cents = _asked_cents(act.args.get("amount"))
    if cents is None:
        return f"'{shown(act.args.get('amount'))}' is not an amount of money that can be paid."
    from townsim.town import firm_money
    if firm_money.request_payee(store, act) is not None:
        return _text(act.args.get("text"), "that is too long for one request.", REQUEST_WORDS)
    if _request_ref(act) is not None:
        if _owed_on(store, act, who) is None:
            return _not_owed_on(store, act, who)
    elif cents > 0 and account_of(store, who) == _own(store, act):
        return own_household(store, who)
    return _text(act.args.get("text"), "that is too long for one request.", REQUEST_WORDS)


def _request_ref(act: Act) -> Any:
    """What a request's `for` names, or None: blank, `none` or `nothing` name nothing."""
    ref = act.args.get("for")
    return None if _blank(ref) or (isinstance(ref, str) and ref.strip().lower() in NOT_TOWARD) else ref


def _do_request(store: Store, act: Act, cause: str) -> str:
    to, _ = _request_to(store, act)
    from townsim.town import firm_money
    business = firm_money.request_payee(store, act) or ""
    ob = "" if business or _request_ref(act) is None else _owed_on(store, act, to)
    rq = Request(store.new_id("rq", cause), store.day, act.actor, to, clean_text(act.args["text"]),
                 _asked_cents(act.args.get("amount")), store.day + REQUEST_DAYS, ob=ob, org=business)
    store.add("requests", rq, cause)
    said = f"you ask them for {money(rq.cents)}" if rq.cents else "you ask them for no money"
    if rq.cents and business:
        said += f", paid to {name_of_who(store, business)}"
    if rq.cents and ob:
        said += f" toward {memo_shown(store.get('obligations', ob).memo)} ({ob})"
    said += "."
    return (f"Sent to {name_of_who(store, to)} ({rq.id}): {said} It stays open until {date_in(rq.expires, store.day)}."
            + tell_writer(store, act.actor, to, rq.text, cause))


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
    if rq.kind == "act":                    # asking to do one thing (lane B): once, always or no
        from townsim.town import once
        return note_too_long(store, _note(act.args.get("note")), MAX_WORDS) if _too_long(_note(act.args.get("note"))) \
            else once.check(store, act, rq)
    answer = _answer(act)
    if answer is None:
        return f"'{shown(act.args.get('answer'))}' is not an answer to a request."
    if _too_long(_note(act.args.get("note"))):
        return note_too_long(store, _note(act.args.get("note")), MAX_WORDS)
    if answer == "accept" and rq.kind in ANSWERERS:
        return ANSWERERS[rq.kind][0](store, act, rq)
    if answer == "accept" and rq.cents > 0:
        payer, payee = _request_accounts(store, rq)
        if rq.ob and not controls(store, act.principal, payer):
            return f"{rq.id} asks for money owed by an account that is not yours."
        if payee == payer:
            return own_household(store, rq.sender)
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
    if rq.kind == "ask" and rq.org and store.has("orgs", rq.org):     # asked for a business of the sender's
        return account_of(store, rq.to), rq.org
    return account_of(store, rq.to), account_of(store, rq.sender)


def _do_answer(store: Store, act: Act, cause: str) -> str:
    rq = _open_request(store, act)
    if rq.kind == "act":
        from townsim.town import once
        return once.do(store, act, rq, cause, _note(act.args.get("note")))
    accepted = _answer(act) == "accept"
    store.set("requests", rq.id, cause, status="accepted" if accepted else "declined", closed_on=store.day,
              closed_by=act.actor)
    told = "Accepted." if accepted else f"Declined: {rq.id} from {name_of_who(store, rq.sender)}."
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
    if rq.kind in SENDER_TOLD:
        text = SENDER_TOLD[rq.kind](store, store.get("requests", rq.id), "accepted" if accepted else "declined")
    obligations.notify(store, rq.sender, text, cause)
    from townsim.town.seat_texts import tell_closed
    tell_closed(store, store.get("requests", rq.id), cause)
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
        from townsim.town import facts                # facts reads the operations' modules
        text = agreements_text(store, own)
        order = facts.standing_order(store, own)
        return "\n".join(order + ([] if text == "nothing" and order else [text]))
    if what == "account in full":
        return statement_text(store, statement(store, own, back=28, max_lines=60), act.actor)
    return statement_text(store, statement(store, own), act.actor)


# write_notes

def _check_notes(store: Store, act: Act) -> str | None:
    text = clean_text(act.args.get("text"))
    if text is None:
        return "there was nothing written."
    return note_too_long(store, text, NOTES_WORDS, "your notes", "that is too long for your notes.") \
        if _too_long(text, NOTES_WORDS) else None


def _do_notes(store: Store, act: Act, cause: str) -> str:
    store.set("people", act.actor, cause, notes=clean_text(act.args["text"]))
    return "Noted."


for _spec in (
    OpSpec("pay", '{"op": "pay", "to": "<name>", "amount": <dollars>, "note": "<what for>", '
                  '"for": "<id of what it is owed on; leave out to let it count toward what you owe them; none for a '
                  'gift on top of what you owe>"}',
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

# and acts done for a client are checked against its connections, from `connections`
from townsim.town import connections as _connections  # noqa: E402

ANSWERERS.update(getattr(_connections, "ANSWERERS_OF_CONNECTIONS", {}))

# and the product's subscribe, with the subscriptions `stop` ends, from `product`
from townsim.town import product as _product  # noqa: E402

STOPPABLE.update(getattr(_product, "STOPPABLE_OF_PRODUCT", {}))

# and the services and clubs people found, with the requests to join them, from `ventures`
from townsim.town import ventures as _ventures  # noqa: E402

ANSWERERS.update(getattr(_ventures, "ANSWERERS_OF_VENTURES", {}))

# and the rules that act at the end of each day, from `rules`
from townsim.town import rules as _rules  # noqa: E402, F401

# and the plain-words lane, whose tasks send requests of their own, from `tasks`
from townsim.town import tasks as _tasks  # noqa: E402

ANSWERERS.update(getattr(_tasks, "ANSWERERS_OF_TASKS", {}))
SENDER_TOLD.update(getattr(_tasks, "SENDER_TOLD_OF_TASKS", {}))

# and the web: looking things up and publishing pages, from `web`
from townsim.town import web as _web  # noqa: E402, F401

# and the company's controls over its product, from `controls`
from townsim.town import controls as _controls  # noqa: E402, F401

# and recurring work an assistant carries out for a business, whose hours go back to its holder, from `recurring`
from townsim.town import recurring as _recurring  # noqa: E402, F401
