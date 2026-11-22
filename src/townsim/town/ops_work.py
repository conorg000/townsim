"""The operations of work: offering jobs and extra hours, quitting and dismissing, pay,
prices, stock orders, an owner's own extra hours, and moving money between one's own
accounts. Registered when `ops_core` is imported.

An owner's business is the first organisation in the town they own, by id; its shop, the
first shop row it trades through. A job offer and an offer of extra hours are requests
(kinds `job` and `shifts`) whose terms are written by code in their summary and checked
again when they are accepted (`ANSWERERS_OF_WORK`, which `ops_core.answer` hands them to).

Wages are paid on Fridays. A job offered starts on the Monday after the day it reaches the
person, and the offer stays open until the Sunday before it, so the date it names holds.
A job that ends is paid for the last time on that week's Friday, or at a weekend was paid
for the last time on the Friday just gone. Changes of pay take effect after this week's
Friday (from Friday to Sunday, after the next); what is agreed from a Friday on is first
paid the following Friday, and every message names the date.
"""

from __future__ import annotations

import math
import re
from typing import Any

from townsim.town import firms, hours, jobs, obligations, statement
from townsim.town import ops_core as core
from townsim.town.acts import (FILLED, REGISTRY, YOUR_ACCOUNTS, Act, OpSpec, _alternatives, account_named, actor_name, dollars, fits,
                               is_savings, name_of_who, register, shown, to_cents)
from townsim.town.clock import date_in, date_text
from townsim.town.entities import Job, Org, Request
from townsim.town.obligations import money
from townsim.town.statement import quoted
from townsim.town.store import Store

TEXT_WORDS = 80
MIN_JOB_HOURS, MAX_JOB_HOURS = 4, 60
MIN_SHIFT_HOURS, MAX_SHIFT_HOURS = 1, 20
MIN_EXTRA_HOURS, MAX_EXTRA_HOURS = 1, 30
MIN_PRICE, MAX_PRICE = 100, 10_000
WORKING_AGE = 16
MOVED = statement.MOVED
JOB_TEXT = "A job offer"
SHIFTS_TEXT = "An offer of extra hours"
SUMMARY_WORDS, SUMMARY_CHARS, SUMMARY_NAME = 60, 480, 40
_SUPPLIER_PART = '"supplier": "<name>"'
_EMPLOYER_PART = ', "employer": "<employer, if you have more than one job>"'
_NUMBER = re.compile(r"\d{1,6}(\.\d{1,6})?", re.ASCII)


# reading arguments

def _number(value: Any) -> Any:
    """A number written as a plain string (`"38"`, `"7.5"`) read as one; anything else as it is."""
    if isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        return float(value.strip())
    return value


def _hours(value: Any, low: float, high: float) -> float | None:
    """A number of hours from `low` to `high`, to two places; None for anything else."""
    value = _number(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not low <= value <= high:
        return None
    return round(float(value), 2)


def _h(h: float) -> str:
    return f"{h:g}"


def _whole(value: Any, low: int, high: int) -> int | None:
    """A whole number from `low` to `high`; a float with nothing after the point counts."""
    value = _number(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _text_problem(value: Any) -> str | None:
    """Why optional words cannot be sent, or None."""
    text = core._note(value)
    return None if fits(text, TEXT_WORDS) else "the text is too long."


def _person(store: Store, act: Act, key: str) -> tuple[str | None, str]:
    """The person `key` names, or None and why not."""
    value = act.args.get(key)
    who = core._resolve(store, value, act.actor)
    if who is None:
        return None, f"no one you know is called '{shown(value)}'. Names are written in full, as they appear in your records."
    if not store.has("people", who):
        return None, f"{name_of_who(store, who)} is not a person."
    if who == act.principal:
        return None, "that is you."
    return who, ""


def _owned(store: Store, pid: str) -> list[Org]:
    return [o for o in store.all("orgs") if o.owner == pid and not o.outside]


def _business(store: Store, pid: str) -> Org | None:
    owned = _owned(store, pid)
    return owned[0] if owned else None


def _shop(store: Store, org: str) -> str | None:
    rows = store.where("firms", org=org)
    return rows[0].id if rows else None


def _has_job_at(store: Store, pid: str, org: str) -> bool:
    """Whether `pid` holds a job at `org`, or has one there agreed to start."""
    return any(j.org == org and (j.ended == -1 or j.ended >= store.day) for j in store.where("jobs", person=pid))


def _job_with(store: Store, pid: str, owner: str) -> Job | None:
    """The first job `pid` holds at an organisation `owner` owns."""
    owned = {o.id for o in _owned(store, owner)}
    held = [j for j in jobs.held(store, person=pid) if j.org in owned]
    return held[0] if held else None


def _coming(store: Store, pid: str) -> list[Job]:
    """The jobs `pid` holds today or has agreed to start, not already ending, in id order."""
    return [j for j in store.where("jobs", person=pid) if j.ended == -1]


def _elsewhere(store: Store, pid: str, org: str) -> list[Job]:
    """The jobs `pid` holds or has agreed to start, at organisations other than `org` that they
    do not own, and that are not already ending: those a new job ends."""
    return [j for j in _coming(store, pid) if j.org != org and store.get("orgs", j.org).owner != pid]


def _cut_name(name: str) -> str:
    return name if len(name) <= SUMMARY_NAME else name[:SUMMARY_NAME].rstrip() + "\u2026"


def _bounded(text: str) -> str:
    """A summary at most 60 words and 480 characters, cut at a word with `…` if it is longer."""
    words = text.split(" ")
    if len(words) <= SUMMARY_WORDS and len(text) <= SUMMARY_CHARS:
        return text
    out = ""
    for w in words[:SUMMARY_WORDS - 1]:
        if len(out) + len(w) + 2 > SUMMARY_CHARS:
            break
        out = f"{out} {w}" if out else w
    return out + "\u2026"


def sent(store: Store, rq: Request, said: str) -> str:
    """What the sender of a request is told: who it went to, what the other will read of it
    (`said`, as code wrote it), and the day it lapses."""
    return f"Sent to {name_of_who(store, rq.to)} ({rq.id}): {said} It stays open until {date_in(rq.expires, store.day)}."


def _ending(store: Store, job: Job) -> str | None:
    """Why a job already ending cannot be ended or changed, or None."""
    if job.ended == -1:
        return None
    return (f"{store.get('people', job.person).name}'s job at {_org_name(store, job.org)} ends on "
            f"{date_text(job.ended)}.")


def _org_name(store: Store, org: str) -> str:
    return store.get("orgs", org).name


def _names(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _too_little(weekly: int, h: float) -> str | None:
    if weekly < round(jobs.MIN_HOURLY * h):
        return (f"{money(weekly)} a week for {_h(h)} hours is less than {money(jobs.MIN_HOURLY)} an hour.")
    return None


def _owner_of(store: Store, org: str) -> list[str]:
    owner = store.get("orgs", org).owner
    return [owner] if owner else []


def _left(store: Store, job: Job, last: int, cause: str) -> str:
    """End `job` with its last pay on `last` and tell its owner; return what they are told."""
    jobs.finish(store, job.id, last, cause)
    name = store.get("people", job.person).name
    text = f"{name} has left {_org_name(store, job.org)}. Their last pay {jobs.last_pay_text(last, store.day)}."
    for owner in _owner_of(store, job.org):
        if owner != job.person:
            jobs.tell(store, owner, text, cause)
    return text


def _not_started(store: Store, job: Job, cause: str) -> None:
    """A job agreed and not yet begun ends before its first day; nothing is paid on it; its
    owner is told."""
    jobs.finish(store, job.id, job.since - 1, cause)
    text = (f"{store.get('people', job.person).name} does not start at {_org_name(store, job.org)} "
            f"on {date_text(job.since)}.")
    for owner in _owner_of(store, job.org):
        jobs.tell(store, owner, text, cause)


# offer_job

def _job_terms(store: Store, act: Act) -> tuple[str | None, int | None, float | None, str]:
    """(org, weekly cents, hours, why not) for an offer of a job."""
    org = _business(store, act.principal)
    if org is None:
        return None, None, None, "you own no business in the town."
    weekly = to_cents(act.args.get("weekly_pay"))
    if weekly is None:
        return None, None, None, f"'{shown(act.args.get('weekly_pay'))}' is not an amount of money that can be paid."
    h = _hours(act.args.get("hours"), MIN_JOB_HOURS, MAX_JOB_HOURS)
    if h is None:
        return None, None, None, (f"'{shown(act.args.get('hours'))}' is not a number of hours a week from "
                                  f"{MIN_JOB_HOURS} to {MAX_JOB_HOURS}.")
    return org.id, weekly, h, ""


def _check_offer_job(store: Store, act: Act) -> str | None:
    who, why = _person(store, act, "to")
    if who is None:
        return why
    org, weekly, h, why = _job_terms(store, act)
    if org is None:
        return why
    person = store.get("people", who)
    if person.age < WORKING_AGE:
        return f"{person.name} is {person.age}."
    if _has_job_at(store, who, org):
        return f"{person.name} already works at {_org_name(store, org)}."
    return _too_little(weekly, h) or _text_problem(act.args.get("text"))


def _start_monday(day: int) -> int:
    """The Monday a job offered on `day` starts: the Monday after the day it reaches them."""
    return jobs.next_monday(day + 1)


def job_summary(store: Store, org: str, who: str, weekly: int, h: float, monday: int) -> str:
    """What an offer of a job says: its terms, the jobs elsewhere accepting it ends and those
    agreed elsewhere it means are not started; every name cut to 40 characters."""
    text = (f"{_cut_name(_org_name(store, org))} offers {_cut_name(store.get('people', who).name)} a job: "
            f"{money(weekly)} a week for {_h(h)} hours, paid Fridays, from {date_text(monday)}.")
    ending = _elsewhere(store, who, org)
    held = [_cut_name(_org_name(store, j.org)) for j in ending if j.since <= store.day]
    agreed = [_cut_name(_org_name(store, j.org)) for j in ending if j.since > store.day]
    if held:
        plural = "s" if len(held) > 1 else ""
        text += f" Accepting ends your job{plural} at {_names(held)} on {date_text(monday - 3)}."
    if agreed:
        text += f" Accepting means you do not start at {_names(agreed)}."
    return _bounded(text)


def _do_offer_job(store: Store, act: Act, cause: str) -> str:
    who, _ = _person(store, act, "to")
    org, weekly, h, _ = _job_terms(store, act)
    monday = _start_monday(store.day)
    rq = Request(store.new_id("rq", cause), store.day, act.actor, who, core._note(act.args.get("text")) or JOB_TEXT,
                 0, monday - 1, kind="job", summary=job_summary(store, org, who, weekly, h, monday),
                 org=org, first=monday, hours=h, pay=weekly)
    store.add("requests", rq, cause)
    return sent(store, rq, rq.summary)


def _offered_by_business(store: Store, rq: Request, org) -> bool:
    """An offer is the business's while its sender owns it, or is the business's own active
    instance: an offer an instance made for a business is the business's offer."""
    if org.outside:
        return False
    if org.owner == rq.sender:
        return True
    if store.has("instances", rq.sender):
        inst = store.get("instances", rq.sender)
        return inst.status == "active" and inst.client == org.id
    return False


def _check_job(store: Store, act: Act, rq: Request) -> str | None:
    org = store.get("orgs", rq.org)
    if not _offered_by_business(store, rq, org):
        return f"{name_of_who(store, rq.sender)} no longer owns {org.name}."
    why = _too_little(rq.pay, rq.hours)
    if why is not None:
        return why
    pid = act.principal
    if _has_job_at(store, pid, rq.org):
        return f"you already work at {org.name}."
    ending = {j.id for j in _elsewhere(store, pid, rq.org)}
    kept = sum(j.hours for j in _coming(store, pid) if j.id not in ending)
    free = hours.WEEK_HOURS - hours.work_hours(store, pid) - kept
    if free < rq.hours:
        return (f"your week has {_h(round(max(free, 0.0), 2))} hours once your other work is done, "
                f"and this job is {_h(rq.hours)}.")
    return None


def _do_job(store: Store, act: Act, rq: Request, cause: str) -> str:
    pid = act.principal
    monday = rq.first
    for job in _elsewhere(store, pid, rq.org):
        if job.since > store.day:          # agreed and not yet started: it never starts
            _not_started(store, job, cause)
        else:
            _left(store, job, monday - 3, cause)
    jobs.start(store, pid, rq.org, rq.pay, rq.hours, monday + jobs.FRIDAY, cause, since=monday)
    jobs.end_benefit(store, pid, monday - 3, cause)
    return f"{store.get('people', pid).name} starts at {_org_name(store, rq.org)} on {date_text(monday)}."


# offer_shifts

def _check_offer_shifts(store: Store, act: Act) -> str | None:
    who, why = _person(store, act, "to")
    if who is None:
        return why
    if _business(store, act.principal) is None:
        return "you own no business in the town."
    job = _job_with(store, who, act.principal)
    if job is None:
        return f"{store.get('people', who).name} does not work at {_business(store, act.principal).name}."
    h = _hours(act.args.get("hours"), MIN_SHIFT_HOURS, MAX_SHIFT_HOURS)
    if h is None:
        return (f"'{shown(act.args.get('hours'))}' is not a number of hours from {MIN_SHIFT_HOURS} "
                f"to {MAX_SHIFT_HOURS}.")
    if _shift_pay(store, job, h) <= 0:
        return (f"{store.get('people', who).name} is paid {money(_weekly_now(store, job))} a week at "
                f"{_org_name(store, job.org)}.")
    return None


def _weekly_now(store: Store, job: Job) -> int:
    """The weekly pay in force today: the wage's amount as of today, not a change announced
    for a Friday to come (which `job.weekly` already holds)."""
    if store.has("obligations", job.wage_ob):
        return obligations.cents_now(store, store.get("obligations", job.wage_ob))
    return job.weekly


def _shift_pay(store: Store, job: Job, h: float) -> int:
    """Extra hours are paid at the hourly rate in force on the day they are offered."""
    return round(h * _weekly_now(store, job) / job.hours) if job.hours > 0 else 0


def _do_offer_shifts(store: Store, act: Act, cause: str) -> str:
    who, _ = _person(store, act, "to")
    job = _job_with(store, who, act.principal)
    h = _hours(act.args.get("hours"), MIN_SHIFT_HOURS, MAX_SHIFT_HOURS)
    pay = _shift_pay(store, job, h)
    org = _org_name(store, job.org)
    friday = jobs.pay_friday(store.day + 1)        # what is accepted from tomorrow is paid then
    summary = _bounded(f"{_cut_name(org)} offers {_cut_name(store.get('people', who).name)} {_h(h)} extra hours, "
                       f"paid {money(pay)} on {date_text(friday)}.")
    rq = Request(store.new_id("rq", cause), store.day, act.actor, who, SHIFTS_TEXT, 0,
                 friday - 1, kind="shifts", summary=summary, org=job.org, first=friday, hours=h, pay=pay)
    store.add("requests", rq, cause)
    from townsim.town import facts                    # facts reads this module's operations
    had, cents, ids = facts.extra_accepted(store, job.org, who)
    already = (f" {store.get('people', who).name} has accepted {had:g} extra hours this week already, {money(cents)} "
               f"({', '.join(ids)}).") if ids else ""
    return sent(store, rq, summary) + already


def _check_shifts(store: Store, act: Act, rq: Request) -> str | None:
    org = store.get("orgs", rq.org)
    if not _offered_by_business(store, rq, org):
        return f"{name_of_who(store, rq.sender)} no longer owns {org.name}."
    if not jobs.held(store, person=act.principal, org=rq.org):
        return f"you no longer work at {org.name}."
    left = store.get("people", act.principal).hours_left
    if act.actor == act.principal:              # answering takes its own time too
        left -= REGISTRY["answer"].hours
    if left < rq.hours:
        return f"you have {_h(round(max(left, 0.0), 2))} hours of your own left this week, and this is {_h(rq.hours)} hours."
    return None


def _do_shifts(store: Store, act: Act, rq: Request, cause: str) -> str:
    person = store.get("people", act.principal)
    store.set("people", act.principal, cause, hours_left=round(person.hours_left - rq.hours, 2))
    fid = _shop(store, rq.org)
    if fid is not None:
        store.set("firms", fid, cause, extra_hours=round(store.get("firms", fid).extra_hours + rq.hours, 2))
    friday = rq.first
    org = _org_name(store, rq.org)
    worker = store.get("people", act.principal)
    obligations.create(store, kind="wage", payer=rq.org, payee=worker.household, cents=rq.pay, first=friday,
                       memo=f"extra hours at {org}", ends="both", made_by="routine", cause=cause)
    return (f"{worker.name} works {_h(rq.hours)} extra hours at {org} this week, "
            f"paid {money(rq.pay)} on {date_text(friday)}.")


# quit

def _quitting(store: Store, act: Act) -> tuple[Job | None, str]:
    """The job `quit` leaves, or None and why not."""
    pid = act.principal
    held = jobs.held(store, person=pid)
    value = act.args.get("employer", act.args.get("from"))      # `from`, its older name, means the same
    if not core._blank(value):
        who = core._resolve(store, value, act.actor)
        mine = [j for j in held if j.org == who]
        if not mine:
            return None, f"you have no job at '{shown(value)}'."
        held = mine
    leaving = [j for j in held if store.get("orgs", j.org).owner != pid]
    if not leaving:
        if held:
            return None, f"you own {_org_name(store, held[0].org)}."
        return None, "you have no job."
    lasting = [j for j in leaving if j.ended == -1]
    if not lasting:
        return None, f"your job at {_org_name(store, leaving[0].org)} ends on {date_text(leaving[0].ended)}."
    leaving = lasting
    if len(leaving) > 1:
        return None, f"you have jobs at {_names([_org_name(store, j.org) for j in leaving])}."
    return leaving[0], ""


def _check_quit(store: Store, act: Act) -> str | None:
    job, why = _quitting(store, act)
    return why if job is None else None


def _do_quit(store: Store, act: Act, cause: str) -> str:
    job, _ = _quitting(store, act)
    last = jobs.last_friday(store.day)
    _left(store, job, last, cause)
    return f"You have left {_org_name(store, job.org)}. Your last pay {jobs.last_pay_text(last, store.day)}."


# dismiss

def _check_dismiss(store: Store, act: Act) -> str | None:
    who, why = _person(store, act, "who")
    if who is None:
        return why
    if _business(store, act.principal) is None:
        return "you own no business in the town."
    job = _job_with(store, who, act.principal)
    if job is None:
        return f"{store.get('people', who).name} does not work at {_business(store, act.principal).name}."
    return _ending(store, job) or _text_problem(act.args.get("text"))


def _do_dismiss(store: Store, act: Act, cause: str) -> str:
    who, _ = _person(store, act, "who")
    job = _job_with(store, who, act.principal)
    last = jobs.last_friday(store.day)
    org = _org_name(store, job.org)
    paid = jobs.last_pay_text(last, store.day)
    text = f"{org} has ended your job. Your last pay {paid}."
    note = core._note(act.args.get("text"))
    if note:
        text += f" {actor_name(store, act.actor)} wrote: {quoted(note)}"
    jobs.tell(store, who, text, cause)          # told first; then what follows from the end
    jobs.finish(store, job.id, last, cause)
    return f"{store.get('people', who).name} has left {org}. Their last pay {paid}."


# set_staff_pay

def _staff(store: Store, act: Act) -> tuple[Job | None, str]:
    """The job whose pay is set: someone's at the principal's business, or their own."""
    value = act.args.get("who")
    who = core._resolve(store, value, act.actor)
    if who is None:
        return None, f"no one you know is called '{shown(value)}'. Names are written in full, as they appear in your records."
    if not store.has("people", who):
        return None, f"{name_of_who(store, who)} is not a person."
    if _business(store, act.principal) is None:
        return None, "you own no business in the town."
    job = _job_with(store, who, act.principal)
    if job is None:
        return None, f"{store.get('people', who).name} does not work at {_business(store, act.principal).name}."
    return job, ""


def _new_pay(store: Store, act: Act, job: Job) -> int | None:
    value = act.args.get("weekly_pay")
    if job.person == act.principal and dollars(value) == 0:
        return 0
    return to_cents(value)


def _check_staff_pay(store: Store, act: Act) -> str | None:
    job, why = _staff(store, act)
    if job is None:
        return why
    why = _ending(store, job)
    if why is not None:
        return why
    weekly = _new_pay(store, act, job)
    if weekly is None:
        return f"'{shown(act.args.get('weekly_pay'))}' is not an amount of money that can be paid."
    name = store.get("people", job.person).name
    if store.has("obligations", job.wage_ob):
        wage = store.get("obligations", job.wage_ob)
        if wage.status != "active" or wage.last is not None:
            return f"{_org_name(store, job.org)} has stopped paying {name}'s wages."
    if weekly == job.weekly:
        return f"{name} is paid {money(weekly)} a week already."
    if job.person != act.principal:
        return _too_little(weekly, job.hours)
    return None


def _do_staff_pay(store: Store, act: Act, cause: str) -> str:
    """A job has one wage obligation for its whole life, re-priced (to nothing, for a draw of
    nothing) from the Friday after the next payable one, past any Friday already paid by hand;
    or, while a change is pending, in its place on its day. Only a job with no wage yet is
    given one, from the next payable Friday."""
    job, _ = _staff(store, act)
    weekly = _new_pay(store, act, job)
    friday = jobs.pay_friday(store.day)
    org = _org_name(store, job.org)
    worker = store.get("people", job.person)
    replaced = ""
    if store.has("obligations", job.wage_ob):
        wage = store.get("obligations", job.wage_ob)
        if wage.next_from > store.day:                 # a change announced and not yet come
            start = wage.next_from
            replaced = (f" This replaces the change to {money(wage.next_cents)} that was to start on "
                        f"{date_text(start)}.")
        else:
            start = obligations.next_on_schedule(wage, friday + 1)
        while any(d.day == start and d.status != "void" and d.paid > 0       # a Friday paid toward keeps its pay
                  for d in store.where("dues", obligation=wage.id)):
            start += wage.every
        obligations.reprice(store, wage.id, weekly, start, cause)
        ob = wage.id
    else:
        start = friday
        ob = obligations.create(store, kind="wage", payer=job.org, payee=worker.household, cents=weekly, first=start,
                                every=7, memo=f"wages from {org}", ends="both", made_by="routine", cause=cause)
    store.set("jobs", job.id, cause, weekly=weekly, wage_ob=ob)
    paid = obligations.how_paid(store, store.get("obligations", ob), False)
    if job.person == act.principal:
        return f"{org} pays you {money(weekly)} a week from {date_text(start)} ({ob}).{replaced} {paid}"
    jobs.tell(store, job.person, f"{org} pays you {money(weekly)} a week from {date_text(start)} ({ob}).{replaced} "
                                 f"{paid}", cause)
    return f"{org} pays {worker.name} {money(weekly)} a week from {date_text(start)}.{replaced}"


# set_price

def _own_shop(store: Store, act: Act) -> tuple[str | None, str]:
    org = _business(store, act.principal)
    if org is None:
        return None, "you own no business in the town."
    fid = _shop(store, org.id)
    if fid is None:
        return None, f"{org.name} is not a shop."
    return fid, ""


def _price(value: Any) -> int | None:
    cents = to_cents(value)
    return cents if cents is not None and MIN_PRICE <= cents <= MAX_PRICE else None


def _for_venture(act: Act) -> bool:
    return act.args.get("for") not in (None, "")


def _check_price(store: Store, act: Act) -> str | None:
    if _for_venture(act):
        from townsim.town import ventures
        return ventures.check_fee(store, act)
    fid, why = _own_shop(store, act)
    if fid is None:
        return why
    if _price(act.args.get("price")) is None:
        return (f"'{shown(act.args.get('price'))}' is not a price from {money(MIN_PRICE)} "
                f"to {money(MAX_PRICE)}.")
    return None


def _do_price(store: Store, act: Act, cause: str) -> str:
    if _for_venture(act):
        from townsim.town import ventures
        return ventures.do_fee(store, act, cause)
    fid, _ = _own_shop(store, act)
    cents = _price(act.args.get("price"))
    store.set("firms", fid, cause, price=cents)
    return f"The price at {_org_name(store, store.get('firms', fid).org)} is now {money(cents)} a basket."


# order_stock

def _supplier(store: Store, value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > core.LONGEST_NAME:
        return None
    wanted = value.strip().casefold()
    for sp in store.all("suppliers"):
        if store.has("orgs", sp.org) and store.get("orgs", sp.org).name.casefold() == wanted:
            return sp.id
    return None


def _baskets(act: Act) -> Any:
    """The baskets an order names (`units`, the older name, means the same)."""
    return act.args.get("baskets", act.args.get("units"))


def _check_order(store: Store, act: Act) -> str | None:
    fid, why = _own_shop(store, act)
    if fid is None:
        return why
    if _supplier(store, act.args.get("supplier")) is None:
        names = _names(sorted(_org_name(store, sp.org) for sp in store.all("suppliers") if store.has("orgs", sp.org)))
        yours = f" Yours are {names}." if store.all("suppliers") else ""
        return f"'{shown(act.args.get('supplier'))}' is not a supplier.{yours}"
    if _whole(_baskets(act), 0, firms.MAX_ORDER) is None:
        return f"'{shown(_baskets(act))}' is not a whole number of baskets from 0 to {firms.MAX_ORDER:,}."
    return None


def _do_order(store: Store, act: Act, cause: str) -> str:
    fid, _ = _own_shop(store, act)
    sid = _supplier(store, act.args.get("supplier"))
    units = _whole(_baskets(act), 0, firms.MAX_ORDER)
    firms.place_order(store, fid, sid, units, cause)
    monday = jobs.next_monday(store.day)
    if units == 0:
        return f"Nothing is ordered for {date_text(monday)}, and nothing is repeated after it."
    sp = store.get("suppliers", sid)
    return (f"Ordered {units:,} baskets from {_org_name(store, sp.org)} at {money(firms.unit_price(store, fid, sid))} a basket, "
            f"for {date_text(monday)}.")


# work_extra_hours

def _extra(act: Act) -> float | None:
    return _hours(act.args.get("hours"), MIN_EXTRA_HOURS, MAX_EXTRA_HOURS) if isinstance(act.args, dict) else None


def _extra_hours_of(store: Store, act: Act) -> float:
    """The hours the act takes: those it names, or none if it names none that can be worked."""
    return _extra(act) or 0.0


def _check_extra(store: Store, act: Act) -> str | None:
    fid, why = _own_shop(store, act)
    if fid is None:
        return why
    if _extra(act) is None:
        return (f"'{shown(act.args.get('hours'))}' is not a number of hours from {MIN_EXTRA_HOURS} "
                f"to {MAX_EXTRA_HOURS}.")
    return None


def _do_extra(store: Store, act: Act, cause: str) -> str:
    fid, _ = _own_shop(store, act)
    h = _extra(act)
    store.set("firms", fid, cause, extra_hours=round(store.get("firms", fid).extra_hours + h, 2))
    return f"You work {_h(h)} extra hours at {_org_name(store, store.get('firms', fid).org)} this week."


# move_money

def _own_account(store: Store, act: Act, key: str) -> tuple[str | None, str]:
    """The account `key` names (`acts.account_named`), or None and why not."""
    return account_named(store, act.principal, act.args.get(key))


def _check_move(store: Store, act: Act) -> str | None:
    source, why = _own_account(store, act, "from")
    if source is None:
        return why
    dest, why = _own_account(store, act, "to")
    if dest is None:
        return why
    if source == dest:
        return "that is the same account twice."
    cents, why = core._amount(act.args.get("amount"))
    if cents is None:
        return why
    return core._short_of(store, source, cents)


def _do_move(store: Store, act: Act, cause: str) -> str:
    source, src_name = _own_account(store, act, "from")
    dest, dst_name = _own_account(store, act, "to")
    cents = to_cents(act.args.get("amount"))
    obligations.pay(store, source, dest, cents, MOVED, cause, match=False)
    told = f"Moved {money(cents)} from {src_name} to {dst_name}."
    savings = [a for a in (source, dest) if is_savings(a)]
    if savings:
        told += f" Your savings hold {money(store.balance(savings[0]))}."
    return told


def _suppliers(store: Store, pid: str) -> str:
    names = sorted(_org_name(store, sp.org) for sp in store.all("suppliers") if store.has("orgs", sp.org))
    return '"supplier": ' + (_alternatives(names) if names else '"<name>"')


def _employers(store: Store, pid: str) -> str:
    """A job's employer as `quit` names it: only when there is more than one job it could leave."""
    names = [_org_name(store, j.org) for j in jobs.held(store, person=pid)
             if store.has("orgs", j.org) and store.get("orgs", j.org).owner != pid and j.ended == -1]
    names = list(dict.fromkeys(names))
    return ', "employer": ' + _alternatives(names) if len(names) > 1 else ""


FILLED[_SUPPLIER_PART] = _suppliers
FILLED[_EMPLOYER_PART] = _employers


ANSWERERS_OF_WORK: dict[str, core.Answerer] = {"job": (_check_job, _do_job), "shifts": (_check_shifts, _do_shifts)}
core.ANSWERERS.update(ANSWERERS_OF_WORK)

for _spec in (
    OpSpec("offer_job", '{"op": "offer_job", "to": "<name>", "weekly_pay": <dollars>, "hours": <hours a week>, '
                        '"text": "<optional words>"}',
           "Offer someone a job at your business.", 0.5, _check_offer_job, _do_offer_job, 90, who="owner"),
    OpSpec("offer_shifts", '{"op": "offer_shifts", "to": "<name>", "hours": <extra hours this week>}',
           "Offer someone who works for you extra hours this week.", 0.25, _check_offer_shifts, _do_offer_shifts,
           100, who="owner"),
    OpSpec("dismiss", '{"op": "dismiss", "who": "<name>", "text": "<optional words>"}',
           "End the job of someone who works for you.", 0.5, _check_dismiss, _do_dismiss, 110, who="owner"),
    OpSpec("set_staff_pay", '{"op": "set_staff_pay", "who": "<name>", "weekly_pay": <dollars>}',
           "Change the weekly pay of someone at your business, or your own.", 0.1, _check_staff_pay,
           _do_staff_pay, 120, who="owner"),
    OpSpec("set_price", '{"op": "set_price", "price": <dollars>' + "<for one of your services or clubs>" + '}',
           "Change your shop's price.", 0.1, _check_price, _do_price, 130, who="owner"),
    OpSpec("order_stock", '{"op": "order_stock", ' + _SUPPLIER_PART + ', "baskets": <baskets a week; 0 to end the order>}',
           "Order stock for Monday.", 0.5, _check_order, _do_order, 140, who="owner"),
    OpSpec("work_extra_hours", '{"op": "work_extra_hours", "hours": <number>}',
           "Work extra hours in your shop this week.", 0.0, _check_extra, _do_extra, 150, who="owner",
           hours_of=_extra_hours_of),
    OpSpec("move_money", '{"op": "move_money", "from": ' + YOUR_ACCOUNTS + ', "to": ' + YOUR_ACCOUNTS
           + ', "amount": <dollars>}',
           "Move money between your own accounts.", 0.1, _check_move, _do_move, 160),
    OpSpec("quit", '{"op": "quit"' + _EMPLOYER_PART + '}',
           "Leave a job.", 0.1, _check_quit, _do_quit, 170, who="employed"),
):
    register(_spec)
