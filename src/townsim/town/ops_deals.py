"""The operations of agreements, loans and rent: proposing an agreement and asking to end
one, borrowing from the bank and repaying early, and a landlord's rent and notice to quit.
Registered when `ops_core` is imported.

An agreement proposed, a loan asked for and an end asked for are requests (kinds
`agreement`, `loan` and `end`) whose terms code writes in their summary and checks again
when they are accepted (`ANSWERERS_OF_DEALS`, which `ops_core.answer` hands them to).
Accepted, an agreement is one obligation of kind `agreement` that both sides made and that
both see listed; a loan is the bank's money paid once into the borrowing account and one
`loan` obligation repaid weekly from the Friday of the following week.

A proposed agreement lapses at the end of the day before it would start (or after fourteen
days), so it starts on the day it names. A request to end an agreement names the last day
anything falls on it: the day before its first instalment from the day after tomorrow on,
and what has already been paid toward the days after it, which stays with the payee. It
stays open until that day, and is refused if what was paid ahead has changed. Accepted, it
changes nothing before that day: what falls by then is still collected. A loan asked
for stays open until the day before its first instalment, which it names.

Requests code writes for a person carry no text of theirs: everything is in the summary,
the words they wrote quoted inside it. Names in a summary are cut to forty characters and
six words, so that a summary is never cut and its quotation can be read back whole.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, NamedTuple

from townsim.town import facts, jobs, obligations
from townsim.town import ops_core as core
from townsim.town import ops_work as work
from townsim.town.acts import (Act, OpSpec, account_of, accounts_of, controls, fits, from_account, name_of_who,
                               register, shown, to_cents)
from townsim.town.clock import date_text, parse_date
from townsim.town.entities import Notice, Org, Record, Request, Tie
from townsim.town.obligations import SCHEDULED, last_cents, money, name_of, outstanding, owed_dues
from townsim.town.statement import memo as memo_shown, quoted
from townsim.town.store import Store, id_key

WHAT_WORDS = 12
MAX_WEEKS = 520
MIN_LOAN, MAX_LOAN = 5_000, 2_000_000
MIN_LOAN_WEEKS, MAX_LOAN_WEEKS = 4, 104
RATE = Fraction(12, 100)                 # a year, on the sum lent, for the weeks it runs
MIN_RENT, MAX_RENT = 2_000, 200_000
RENT_NOTICE_DAYS = 28                    # a change of rent takes effect on the first rent day this far on
ARREARS_DAYS = 14                        # rent unpaid this long before notice can be served
QUIT_DAYS = 28                           # the notice given to leave, and how long before another
ADULT = 18
NAME_WORDS = 6                           # a name in a summary, so that no summary is ever cut
_PER = ("week", "once")
_PAYS = ("me", "them")
_DATE_CHARS = 100


# reading arguments

def _word(value: Any, words: tuple[str, ...]) -> str | None:
    if isinstance(value, str) and value.strip().lower() in words:
        return value.strip().lower()
    return None


def _label(value: Any) -> tuple[str | None, str]:
    """A person's label for what something is for, cleaned; or None and why not."""
    text = core._note(value)
    if not text:
        return None, "there was nothing written for what it is for."
    if not fits(text, WHAT_WORDS):
        return None, "what it is for is longer than twelve words."
    return text, ""


def _start(store: Store, value: Any) -> tuple[int | None, str]:
    """The first day written: the day after tomorrow if none is (so that the request, which
    reaches its addressee tomorrow and lapses the day before it would start, can be answered);
    or None and why not."""
    if core._blank(value):
        return store.day + 2, ""
    readable = isinstance(value, str) and len(value) <= _DATE_CHARS
    day = parse_date(value, store.day) if readable else None
    if day is None:
        gone = parse_date(value, -10 ** 6) if readable else None
        if gone is not None:
            return None, f"{date_text(gone)} has passed."
        return None, f"'{shown(value)}' is not a date that can be read."
    if day == store.day:
        return None, f"{date_text(day)} is today."
    if day == store.day + 1:
        return None, "an agreement can start no sooner than the day after tomorrow."
    return day, ""


def _cut(name: str) -> str:
    """A name as a summary shows it: at most 40 characters and six words, cut with `…`."""
    name = work._cut_name(name)
    words = name.split()
    return name if len(words) <= NAME_WORDS else " ".join(words[:NAME_WORDS]) + "\u2026"


def _quoted_part(summary: str) -> str:
    """The words a person wrote in a summary code wrote, in their quotation marks: the last
    quotation in it. Names are cut so that a summary is never cut, and a quotation of a person's
    words holds no double quotation mark of its own (`quoted`), so it is always whole."""
    end = summary.rfind('"')
    return summary[summary.rfind('"', 0, end):end + 1]


def _side_name(store: Store, account: str, person: str) -> str:
    """The name one side of an agreement goes by: the person's own when it is their
    household's account, else the account's."""
    if store.has("people", person) and store.get("people", person).household == account:
        return store.get("people", person).name
    return name_of(store, account)


def _adults(store: Store, account: str) -> list[str]:
    """Who answers for an account: a household's members of 18 or over, by id; an org's
    owner, or else its contact."""
    if store.has("households", account):
        members = [p for p in store.get("households", account).members if store.get("people", p).age >= ADULT]
        return sorted(members, key=id_key)
    if store.has("orgs", account):
        org = store.get("orgs", account)
        who = org.owner or org.contact
        return [who] if who and store.has("people", who) else []
    return []


def _tell(store: Store, people: list[str], text: str, cause: str) -> None:
    for pid in people:
        store.add("notices", Notice(store.new_id("n", cause), store.day, pid, text), cause)


def _weeks_text(n: int) -> str:
    return f"{n} week" + ("" if n == 1 else "s")


def _not_held(store: Store, pid: str, account: str) -> str:
    if store.has("orgs", account):
        return f"{name_of_who(store, pid)} no longer owns {name_of(store, account)}."
    return f"{name_of_who(store, pid)} no longer holds the {name_of(store, account)} account."


# propose_agreement

class Terms(NamedTuple):
    mine: str             # the proposer's account
    who: str              # the person it is proposed to
    payer: str
    payee: str
    cents: int
    every: int
    first: int
    weeks: int
    what: str


def _agreement_terms(store: Store, act: Act) -> tuple[Terms | None, str]:
    mine, why = from_account(store, act)
    if mine is None:
        return None, why
    who, why = work._person(store, act, "to")
    if who is None:
        return None, why
    other = account_of(store, who)
    if other == mine:
        return None, "that is your own household." if store.has("households", mine) else \
            "that is the account it would be paid from."
    cents, why = core._amount(act.args.get("amount"))
    if cents is None:
        return None, why
    per = _word(act.args.get("per"), _PER)
    if per is None:
        return None, f"'{shown(act.args.get('per'))}' is not \"week\" or \"once\"."
    weeks = 0
    if per == "week":
        weeks = work._whole(act.args.get("weeks"), 0, MAX_WEEKS)
        if weeks is None:
            return None, f"'{shown(act.args.get('weeks'))}' is not a whole number of weeks from 0 to {MAX_WEEKS}."
    first, why = _start(store, act.args.get("start"))
    if first is None:
        return None, why
    pays = _word(act.args.get("pays"), _PAYS)
    if pays is None:
        return None, f"'{shown(act.args.get('pays'))}' is not \"me\" or \"them\"."
    what, why = _label(act.args.get("what"))
    if what is None:
        return None, why
    payer, payee = (mine, other) if pays == "me" else (other, mine)
    return Terms(mine, who, payer, payee, cents, 7 if per == "week" else 0, first, weeks, what), ""


def agreement_summary(payer: str, payee: str, cents: int, every: int, first: int, weeks: int, what: str) -> str:
    """What a proposed agreement says, every name cut to 40 characters and the whole bounded."""
    head = f"{_cut(payer)} pays {_cut(payee)} {money(cents)}"
    if every == 0:
        text = f"{head} once on {date_text(first)}: {quoted(what)}."
    elif weeks == 0:
        text = f"{head} every week from {date_text(first)} until either side ends it: {quoted(what)}."
    else:
        text = (f"{head} every week from {date_text(first)} for {_weeks_text(weeks)} "
                f"({money(cents * weeks)} in all): {quoted(what)}.")
    return work._bounded(text)


def _names(store: Store, mine: str, proposer: str, other_person: str, payer: str, payee: str) -> tuple[str, str]:
    def name(account: str) -> str:
        return _side_name(store, account, proposer if account == mine else other_person)
    return name(payer), name(payee)


def _check_propose(store: Store, act: Act) -> str | None:
    terms, why = _agreement_terms(store, act)
    return why if terms is None else None


def _do_propose(store: Store, act: Act, cause: str) -> str:
    t, _ = _agreement_terms(store, act)
    payer, payee = _names(store, t.mine, act.principal, t.who, t.payer, t.payee)
    summary = agreement_summary(payer, payee, t.cents, t.every, t.first, t.weeks, t.what)
    expires = min(store.day + core.REQUEST_DAYS, t.first - 1)          # it lapses before it would start
    rq = Request(store.new_id("rq", cause), store.day, act.actor, t.who, "", 0, expires, kind="agreement", summary=summary, org=t.mine, every=t.every, first=t.first, times=t.weeks,
                 pay=t.cents, payer=t.payer, payee=t.payee)
    store.add("requests", rq, cause)
    return f"Sent to {store.get('people', t.who).name} ({rq.id}). It stays open until {date_text(rq.expires)}."


def _check_agreement(store: Store, act: Act, rq: Request) -> str | None:
    for account in (rq.payer, rq.payee):
        if not store.ledger.has(account):
            return "an account it names no longer exists."
    if not controls(store, rq.sender, rq.org):
        return _not_held(store, rq.sender, rq.org)
    if account_of(store, act.principal) not in (rq.payer, rq.payee):
        return f"{rq.id} is an agreement with another household."
    return None


def _do_agreement(store: Store, act: Act, rq: Request, cause: str) -> str:
    # the request lapses at the end of the day before its start, so its start is always ahead
    last = rq.first + 7 * (rq.times - 1) if rq.every and rq.times else None
    ob = obligations.create(store, kind="agreement", payer=rq.payer, payee=rq.payee, cents=rq.pay, first=rq.first,
                            every=rq.every, last=last, memo=_quoted_part(rq.summary), ends="both",
                            made_by=rq.sender, cause=cause)
    return f"Agreed ({ob}): {rq.summary}"


# propose_end

def _end_day(store: Store, ob) -> int | None:
    """The last day anything falls on `ob` if it ends by agreement: the day before its first
    instalment from the day after tomorrow on; None if none falls from then on."""
    days = obligations.instalment_days(ob, store.day + 2, 1)
    return days[0] - 1 if days else None


def _ending(store: Store, act: Act) -> tuple[Any, str, str]:
    """(the agreement, the other side's account, who answers for it), or None and why not."""
    ob_id = act.args.get("id")
    none = f"you have no agreement in force with the id '{shown(ob_id)}'."
    if not isinstance(ob_id, str) or not store.has("obligations", ob_id):
        return None, "", none
    ob = store.get("obligations", ob_id)
    mine = [a for a in (ob.payer, ob.payee) if controls(store, act.principal, a)]
    if ob.kind != "agreement" or ob.status != "active" or not mine:
        return None, "", none
    if len(mine) == 2:
        return None, "", f"both sides of {ob.id} are yours."
    other = ob.payee if mine[0] == ob.payer else ob.payer
    if _end_day(store, ob) is None:
        left = obligations.instalment_days(ob, store.day + 1, 1)
        if left:
            return None, "", (f"{ob.id} has one payment left, on {date_text(left[0])}, and that is tomorrow: "
                              f"there is nothing after it to end.")
        return None, "", f"no payment falls on {ob.id} after today."
    people = _adults(store, other)
    if not people:
        return None, "", f"no one answers for {name_of(store, other)}."
    return ob, other, ob.made_by if ob.made_by in people else people[0]


def _check_propose_end(store: Store, act: Act) -> str | None:
    ob, _, why = _ending(store, act)
    return why if ob is None else None


def _paid_ahead(store: Store, ob, last: int) -> tuple[int, list[int]]:
    """(cents, days) already paid toward instalments of `ob` after `last`, which ending it there
    leaves with the payee."""
    dues = sorted((d for d in store.where("dues", obligation=ob.id)
                   if d.day > last and d.status != "void" and d.paid > 0), key=lambda d: d.day)
    return sum(d.paid for d in dues), [d.day for d in dues]


def _kept_text(store: Store, ob, cents: int, days: list[int]) -> str:
    if not cents:
        return ""
    return (f" {money(cents)} already paid toward {work._names([date_text(d) for d in days])} stays with "
            f"{_cut(_account_name(store, ob.payee))}.")


def _account_name(store: Store, account: str) -> str:
    """A household as `the <X> household`; anything else by its name."""
    if store.has("households", account):
        return _household(store.get("households", account))
    return name_of(store, account)


def _do_propose_end(store: Store, act: Act, cause: str) -> str:
    ob, _, to = _ending(store, act)
    last = _end_day(store, ob)
    kept, days = _paid_ahead(store, ob, last)
    summary = work._bounded(f"{_cut(store.get('people', act.actor).name)} asks to end {memo_shown(ob.memo)} "
                            f"({ob.id}): no payment after {date_text(last)}.{_kept_text(store, ob, kept, days)}")
    rq = Request(store.new_id("rq", cause), store.day, act.actor, to, "", 0, last, kind="end",
                 summary=summary, ob=ob.id, first=last, pay=kept)
    store.add("requests", rq, cause)
    return f"Sent to {store.get('people', to).name} ({rq.id}). It stays open until {date_text(rq.expires)}."


def _check_end(store: Store, act: Act, rq: Request) -> str | None:
    ob = store.get("obligations", rq.ob)
    if ob.status != "active":
        return f"{ob.id} is {ob.status}."
    sides = [a for a in (ob.payer, ob.payee) if controls(store, act.principal, a)]
    if len(sides) != 1 or not any(controls(store, rq.sender, a) for a in (ob.payer, ob.payee) if a not in sides):
        return f"{ob.id} is no longer between you and {name_of_who(store, rq.sender)}."
    kept, days = _paid_ahead(store, ob, rq.first)
    if kept != rq.pay:
        now = _kept_text(store, ob, kept, days).strip() or f"Nothing is now paid toward a day after {date_text(rq.first)}."
        return f"what was paid ahead on {ob.id} has changed since {rq.id} was made. {now}"
    return None


def _do_end(store: Store, act: Act, rq: Request, cause: str) -> str:
    ob = store.get("obligations", rq.ob)
    # nothing changes before the stated day: what falls by then is still collected, and the
    # agreement is finished by the ordinary path once its last instalment has been paid
    obligations.end(store, ob.id, rq.first, cause)       # what was paid ahead stays paid, and both are told
    return f"Ended ({ob.id}): {memo_shown(ob.memo)}, no payment after {date_text(rq.first)}."


# borrow

def _bank(store: Store) -> Org | None:
    banks = [o for o in store.all("orgs") if o.kind == "bank" and not o.outside]
    return banks[0] if banks else None


def loan_schedule(cents: int, weeks: int) -> tuple[int, int, int]:
    """(total, weekly, last) for `cents` lent over `weeks`: the total is the sum and 12 per
    cent a year of it for the weeks, rounded; each week a whole part of it, rounded up; the
    last instalment the rest."""
    total = round(cents * (1 + RATE * weeks / 52))
    weekly = -(-total // weeks)
    return total, weekly, total - weekly * (weeks - 1)


class Loan(NamedTuple):
    source: str
    bank: Org
    banker: str
    cents: int
    weeks: int
    purpose: str


def _loan_terms(store: Store, act: Act) -> tuple[Loan | None, str]:
    source, why = from_account(store, act)
    if source is None:
        return None, why
    bank = _bank(store)
    if bank is None:
        return None, "there is no bank in the town."
    if not bank.contact or not store.has("people", bank.contact):
        return None, "the bank has no banker."
    if bank.contact == act.principal:
        return None, "you are the banker."
    cents = to_cents(act.args.get("amount"))
    if cents is None or not MIN_LOAN <= cents <= MAX_LOAN:
        return None, (f"'{shown(act.args.get('amount'))}' is not an amount from {money(MIN_LOAN)} "
                      f"to {money(MAX_LOAN)}.")
    weeks = work._whole(act.args.get("weeks"), MIN_LOAN_WEEKS, MAX_LOAN_WEEKS)
    if weeks is None:
        return None, (f"'{shown(act.args.get('weeks'))}' is not a whole number of weeks from {MIN_LOAN_WEEKS} "
                      f"to {MAX_LOAN_WEEKS}.")
    purpose, why = _label(act.args.get("purpose"))
    if purpose is None:
        return None, why
    total, weekly, final = loan_schedule(cents, weeks)
    if final <= 0:
        return None, f"{money(total)} in {weeks} weekly sums of {money(weekly)} leaves nothing for the last."
    return Loan(source, bank, bank.contact, cents, weeks, purpose), ""


def _loan_first(day: int) -> int:
    """The Friday a loan asked for on `day` is first repaid: of the week after the day after it."""
    return jobs.next_monday(day + 1) + jobs.FRIDAY


def _owing(store: Store, account: str, bank: str) -> tuple[int, int, int]:
    """(still owed, loans, overdue) on the loans in force `account` owes `bank`."""
    loans = [o for o in store.where("obligations", payer=account)
             if o.payee == bank and o.kind == "loan" and o.status in SCHEDULED]
    overdue = sum(d.cents - d.paid for o in loans for d in owed_dues(store, o.id))
    return sum(outstanding(store, o) for o in loans), len(loans), overdue


def _check_borrow(store: Store, act: Act) -> str | None:
    loan, why = _loan_terms(store, act)
    return why if loan is None else None


def _do_borrow(store: Store, act: Act, cause: str) -> str:
    loan, _ = _loan_terms(store, act)
    total, weekly, final = loan_schedule(loan.cents, loan.weeks)
    first = _loan_first(store.day)
    last = first + 7 * (loan.weeks - 1)
    owed, k, overdue = _owing(store, loan.source, loan.bank.id)
    differs = f", the last, on {date_text(last)}, {money(final)}" if final != weekly else ""
    summary = work._bounded(
        f"{_cut(_side_name(store, loan.source, act.principal))} asks the bank for {money(loan.cents)} over "
        f"{loan.weeks} weeks, repaying {money(weekly)} a week ({money(total)} in all) from {date_text(first)}"
        f"{differs}: {quoted(loan.purpose)}. They now owe the bank {money(owed)} on {k} loan{'' if k == 1 else 's'}, "
        f"{money(overdue)} of it overdue.")
    rq = Request(store.new_id("rq", cause), store.day, act.actor, loan.banker, "", 0, first - 1, kind="loan", summary=summary, org=loan.source, first=first,
                 times=loan.weeks, pay=loan.cents, payer=loan.source, payee=loan.bank.id)
    store.add("requests", rq, cause)
    return f"Sent to {store.get('people', loan.banker).name} ({rq.id}). It stays open until {date_text(rq.expires)}."


def _check_loan(store: Store, act: Act, rq: Request) -> str | None:
    if not store.has("orgs", rq.payee) or store.get("orgs", rq.payee).contact != act.principal:
        return f"you are not the banker at {name_of(store, rq.payee)}."
    if not store.ledger.has(rq.payer):
        return "the account it would be lent to no longer exists."
    if not controls(store, rq.sender, rq.payer):
        return _not_held(store, rq.sender, rq.payer)
    held = store.balance(rq.payee)
    if held < rq.pay and not store.ledger.accounts[rq.payee].allow_credit:
        return f"{name_of(store, rq.payee)} holds {money(held)} and this needs {money(rq.pay)}."
    return None


def _do_loan(store: Store, act: Act, rq: Request, cause: str) -> str:
    total, weekly, final = loan_schedule(rq.pay, rq.times)
    last = rq.first + 7 * (rq.times - 1)
    purpose = _quoted_part(rq.summary)
    obligations.pay(store, rq.payee, rq.payer, rq.pay, f"loan: {purpose}", cause, match=False)
    ob = obligations.create(store, kind="loan", payer=rq.payer, payee=rq.payee, cents=weekly, first=rq.first,
                            every=7, last=last, memo=f"loan for {purpose}", ends="both",
                            made_by=rq.sender, cause=cause, final_cents=final if final != weekly else 0)
    banker = act.principal
    if store.has("people", rq.sender) and not any(t.person == rq.sender and t.other == banker and t.kind == "banker"
                                                   for t in store.where("ties", person=rq.sender)):
        store.add("ties", Tie(store.new_id("ti", cause), rq.sender, banker, "banker"), cause)
    head = (f"{name_of(store, rq.payee)} lends {_side_name(store, rq.payer, rq.sender)} {money(rq.pay)} ({ob}): "
            f"{money(weekly)} a week from {date_text(rq.first)}")
    if final != weekly:
        return f"{head} ({money(total)} in all), the last, on {date_text(last)}, {money(final)}."
    return f"{head} to {date_text(last)} ({money(total)} in all)."


# repay

def _repaying(store: Store, act: Act) -> tuple[Any, str]:
    source, why = from_account(store, act)
    if source is None:
        return None, why
    ob_id = act.args.get("id")
    if (isinstance(ob_id, str) and store.has("obligations", ob_id)
            and store.get("obligations", ob_id).kind in ("loan", "agreement")
            and store.get("obligations", ob_id).payer == source
            and outstanding(store, store.get("obligations", ob_id)) > 0):
        return store.get("obligations", ob_id), ""
    return None, f"you have no loan or agreement with the id '{shown(ob_id)}' that this account still owes on."


def _check_repay(store: Store, act: Act) -> str | None:
    ob, why = _repaying(store, act)
    if ob is None:
        return why
    cents, why = core._amount(act.args.get("amount"))
    if cents is None:
        return why
    most = obligations.repayable(store, ob)
    if cents > most:
        if obligations.open_ended(ob) and ob.status in SCHEDULED:
            return f"that is more than the {money(most)} now due on it."
        return f"that is more than the {money(most)} still owed."
    return core._short_of(store, ob.payer, cents)


def _do_repay(store: Store, act: Act, cause: str) -> str:
    ob, _ = _repaying(store, act)
    cents = to_cents(act.args.get("amount"))
    matches = obligations.repay(store, ob.id, cents, cause)
    ob = store.get("obligations", ob.id)
    owed = outstanding(store, ob)
    head = f"Repaid {money(cents)} to {name_of(store, ob.payee)} on {memo_shown(ob.memo)} ({ob.id})"
    payer = _side_name(store, ob.payer, act.principal)
    _tell(store, [p for p in _adults(store, ob.payee) if p != act.actor],
          f"{payer} repaid {money(cents)} on {memo_shown(ob.memo)} ({ob.id}).", cause)
    if obligations.open_ended(ob) and ob.status in SCHEDULED:       # it goes on until ended
        counted = "; ".join(f"{money(m.cents)} toward the payment due {date_text(store.get('dues', m.due).day)}"
                            for m in matches)
        return f"{head}: {counted}. It goes on every week until either side ends it."
    if owed == 0:
        return f"{head}: nothing more is owed on it."
    last = ""
    if ob.status in SCHEDULED and ob.last is not None and obligations._last_instalment(ob) >= store.day:
        last = f", the last payment, {money(last_cents(ob))}, on {date_text(obligations._last_instalment(ob))}"
    return f"{head}: {money(owed)} still owed{last}."


# set_rent and serve_notice

def _tenancy(store: Store, act: Act) -> tuple[Any, str]:
    """The household the tenant named lives in, if the principal lets it; or None and why not."""
    who, why = work._person(store, act, "tenant")
    if who is None:
        return None, why
    person = store.get("people", who)
    h = store.get("households", person.household)
    me = store.get("people", act.principal)
    if h.landlord not in (act.principal, me.household) or h.id == me.household:
        return None, f"you do not let the house {person.name} lives in."
    if person.age < ADULT:
        return None, f"{person.name} is {person.age}."
    return h, ""


def _address(h) -> str:
    return h.street or "the house"


def _household(h) -> str:
    return f"the {h.name.split(' household')[0]} household"


def _rent(store: Store, act: Act, h) -> Any:
    """The one rent `h` pays the principal for its whole let (`facts.rent_of`), or None."""
    mine = accounts_of(store, act.principal)
    found = [o for o in facts.rents(store, h.id) if o.payee in mine and o.every > 0]
    return found[0] if found else None


class RentChange(NamedTuple):
    h: Any
    ob: Any
    cents: int
    day: int                       # the first rent day at the new amount
    pending: tuple[int, int] | None      # (cents, day) of a change announced and not yet come
    kept: list                     # dues already opened at or after the day, which keep their amount


def _rent_change(store: Store, act: Act) -> tuple[RentChange | None, str]:
    """A change of rent re-prices the let's one rent obligation (as a change of pay re-prices a
    wage): from the first rent day at least `RENT_NOTICE_DAYS` on; or, while a change is
    pending, in its place on its day when the new rent is no more than the pending one (taking
    back a rise needs no notice). A rent day whose due is already opened and paid toward in full,
    or by more than the new rent, keeps its amount, and the change starts after it."""
    h, why = _tenancy(store, act)
    if h is None:
        return None, why
    ob = _rent(store, act, h)
    if ob is None:
        return None, f"no rent for {_address(h)} is paid to you."
    cents = to_cents(act.args.get("weekly"))
    if cents is None or not MIN_RENT <= cents <= MAX_RENT:
        return None, (f"'{shown(act.args.get('weekly'))}' is not an amount from {money(MIN_RENT)} "
                      f"to {money(MAX_RENT)}.")
    pending = (ob.next_cents, ob.next_from) if ob.next_from > store.day else None
    if pending is None and cents == ob.cents:
        return None, f"the rent for {_address(h)} is {money(cents)} a week already."
    if pending is not None and cents == pending[0]:
        return None, f"the rent for {_address(h)} is to be {money(cents)} a week from {date_text(pending[1])} already."
    if pending is not None and cents <= pending[0]:
        day = pending[1]
    else:
        day = obligations.next_on_schedule(ob, store.day + RENT_NOTICE_DAYS)
    kept = []
    while (d := next((d for d in store.where("dues", obligation=ob.id, day=day) if d.status != "void"), None)) \
            and (d.paid >= d.cents or d.paid > cents):
        kept.append(d)
        day += ob.every
    if ob.last is not None:
        return None, f"the rent for {_address(h)} ends on {date_text(ob.last)}."
    return RentChange(h, ob, cents, day, pending, kept), ""


def _check_rent(store: Store, act: Act) -> str | None:
    change, why = _rent_change(store, act)
    return why if change is None else None


def _do_rent(store: Store, act: Act, cause: str) -> str:
    c, _ = _rent_change(store, act)
    obligations.reprice(store, c.ob.id, c.cents, c.day, cause)
    if c.cents == c.ob.cents:
        change = f"for {_address(c.h)} stays {money(c.cents)} a week."
    else:
        change = (f"for {_address(c.h)} changes from {money(c.ob.cents)} to {money(c.cents)} a week "
                  f"from {date_text(c.day)}.")
    if c.pending is not None:
        change += f" This replaces the change to {money(c.pending[0])} that was to start on {date_text(c.pending[1])}."
    for d in c.kept:
        change += (f" The rent due {date_text(d.day)} stays {money(d.cents)}: "
                   f"{money(d.paid)} has been paid toward it already.")
    _tell(store, _adults(store, c.h.id), f"Your rent {change}", cause)
    return f"The rent {change}"


def _notice(store: Store, act: Act) -> tuple[Any, str]:
    h, why = _tenancy(store, act)
    if h is None:
        return None, why
    mine = accounts_of(store, act.principal)
    rents = [o for o in store.where("obligations", payer=h.id) if o.kind == "rent" and o.payee in mine]
    if not any(store.day - d.day >= ARREARS_DAYS for o in rents for d in owed_dues(store, o.id)):
        return None, f"no rent for {_address(h)} has been unpaid for {ARREARS_DAYS} days."
    served = [r for r in store.where("records", kind="notice to quit")
              if r.about == h.id and store.day - r.day < QUIT_DAYS]
    if served:
        return None, f"notice was served on {_household(h)} on {date_text(served[-1].day)}."
    return h, ""


def _check_notice(store: Store, act: Act) -> str | None:
    h, why = _notice(store, act)
    return why if h is None else None


def _do_notice(store: Store, act: Act, cause: str) -> str:
    h, _ = _notice(store, act)
    by = date_text(store.day + QUIT_DAYS)
    landlord = store.get("people", act.principal).name
    text = f"{landlord} has served notice on {_household(h)} to leave {_address(h)} by {by}."
    store.add("records", Record(store.new_id("rec", cause), store.day, "notice to quit", h.id, text), cause)
    _tell(store, _adults(store, h.id), f"{landlord} has served notice: you are to leave {_address(h)} by {by}.", cause)
    return f"You have served notice on {_household(h)} to leave {_address(h)} by {by}."


ANSWERERS_OF_DEALS: dict[str, core.Answerer] = {"agreement": (_check_agreement, _do_agreement),
                                                "end": (_check_end, _do_end), "loan": (_check_loan, _do_loan)}
core.ANSWERERS.update(ANSWERERS_OF_DEALS)

for _spec in (
    OpSpec("propose_agreement", '{"op": "propose_agreement", "to": "<name>", "amount": <dollars>, '
                                '"per": "week" | "once", "weeks": <number, or 0 for until ended>, '
                                '"start": "<first date>", "pays": "me" | "them", "what": "<plain words>"}',
           "Propose an agreement to pay or be paid.", 0.5, _check_propose, _do_propose, 180, takes_from=True),
    OpSpec("propose_end", '{"op": "propose_end", "id": "<id of the agreement>"}',
           "Ask the other side to end an agreement.", 0.25, _check_propose_end, _do_propose_end, 190),
    OpSpec("borrow", '{"op": "borrow", "amount": <dollars>, "weeks": <number>, "purpose": "<what for>"}',
           "Ask the bank for a loan.", 1.0, _check_borrow, _do_borrow, 200, takes_from=True),
    OpSpec("repay", '{"op": "repay", "id": "<id of the loan or agreement>", "amount": <dollars>}',
           "Pay off a loan or agreement early, in part or in full.", 0.25, _check_repay, _do_repay, 210,
           takes_from=True),
    OpSpec("set_rent", '{"op": "set_rent", "tenant": "<name>", "weekly": <dollars>}',
           "Change the weekly rent of a house you let.", 0.25, _check_rent, _do_rent, 220, who="landlord"),
    OpSpec("serve_notice", '{"op": "serve_notice", "tenant": "<name>"}',
           "Serve notice to leave on a household that rents from you.", 0.5, _check_notice, _do_notice, 230,
           who="landlord"),
):
    register(_spec)
