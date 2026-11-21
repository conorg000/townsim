"""Jobs: a person working hours a week for an organisation, paid a weekly wage.

A job is a `Job` row and its wage, an obligation of kind `wage` from the employer's account
into the person's household account, ended only by both. A job that ends keeps its row:
`ended` is the last day worked, and from the day after it the job no longer counts (see
`current`). A job agreed to start later counts from its `since`.

Wages are paid on Fridays. A job that ends in the week is paid for the last time on that
week's Friday; one that ends at a weekend was paid for the last time on the Friday just
gone (`last_friday`). A person under pension age whose job has ended and who holds no
other is paid jobseeker's benefit from the Friday of the following week (`pay_benefit`).
"""

from __future__ import annotations

from townsim.town import obligations
from townsim.town.clock import date_text, dow
from townsim.town.entities import Job
from townsim.town.obligations import SCHEDULED, money
from townsim.town.store import Store, id_key

MIN_HOURLY = 1100            # cents an hour: the least a job may pay anyone but its owner
BENEFIT_WEEKLY = 23_000      # cents a week of jobseeker's benefit, one rate for everyone
BENEFIT_MEMO = "jobseeker's benefit"
PENSION_AGE = 66
FRIDAY = 4


def current(job: Job, day: int) -> bool:
    """Whether `job` is held on `day`: it has begun (`since`) and has not ended, or ends on
    or after that day."""
    return job.since <= day and (job.ended == -1 or job.ended >= day)


def last_friday(day: int) -> int:
    """The Friday of `day`'s week: this week's, or at a weekend the Friday just gone."""
    return day - dow(day) + FRIDAY


def pay_friday(day: int) -> int:
    """The Friday a wage agreed on `day` is first paid: this week's from Monday to Thursday;
    from Friday on (when that morning's wages have been taken), the following one."""
    return day + FRIDAY - dow(day) + (7 if dow(day) >= FRIDAY else 0)


def next_monday(day: int) -> int:
    """The Monday after `day`."""
    return day + 7 - dow(day)


def last_pay_text(day: int, today: int) -> str:
    """`is on <date>`, or `was on <date>` for a day already gone."""
    return f"{'was' if day < today else 'is'} on {date_text(day)}"


def tell(store: Store, pid: str, text: str, cause: str) -> None:
    obligations.notify(store, pid, text, cause)


def held(store: Store, **where: str) -> list[Job]:
    """The jobs matching `where` (as `Store.where`) held today, in id order."""
    return [j for j in store.where("jobs", **where) if current(j, store.day)]


def start(store: Store, pid: str, org: str, weekly: int, hours: float, first_pay_day: int, cause: str,
          since: int | None = None) -> str:
    """Give `pid` a job at `org` for `hours` a week and `weekly` cents, paid every seven days
    from `first_pay_day`, held since `since` (today unless given). Returns the job's id.
    Raises ValueError, changing nothing, for an unknown person or organisation or a pay day
    before today."""
    if not store.has("people", pid):
        raise ValueError(f"no person {pid!r}")
    if not store.has("orgs", org):
        raise ValueError(f"no organisation {org!r}")
    with store.transaction():
        ob = obligations.create(store, kind="wage", payer=org, payee=store.get("people", pid).household,
                                cents=weekly, first=first_pay_day, every=7,
                                memo=f"wages from {store.get('orgs', org).name}", ends="both", made_by="routine",
                                cause=cause)
        jid = store.new_id("jb", cause)
        store.add("jobs", Job(jid, pid, org, weekly, float(hours), ob, store.day if since is None else since), cause)
    return jid


def finish(store: Store, job_id: str, last_pay_day: int, cause: str) -> None:
    """End a job: its wage is paid for the last time on `last_pay_day`, which is the last day
    worked. A `last_pay_day` already gone this week (a Friday, at the weekend) means no wage
    falls from today. Raises ValueError, changing nothing, for an unknown or already ended
    job or a `last_pay_day` more than six days before today."""
    if not isinstance(job_id, str) or not store.has("jobs", job_id):
        raise ValueError(f"no job {job_id!r}")
    job = store.get("jobs", job_id)
    if job.ended != -1:
        raise ValueError(f"{job_id} already ended on day {job.ended}")
    if not isinstance(last_pay_day, int) or isinstance(last_pay_day, bool) or last_pay_day < store.day - 6:
        raise ValueError(f"last_pay_day must be an int >= {store.day - 6}, got {last_pay_day!r}")
    last = max(last_pay_day, store.day)
    with store.transaction():
        if store.has("obligations", job.wage_ob):      # a job has one wage for its whole life
            obligations.end(store, job.wage_ob, last, cause)
        store.set("jobs", job_id, cause, ended=last_pay_day)


def benefit_held(store: Store, pid: str) -> str | None:
    """The person's jobseeker's benefit, if one still falls due from today; else None."""
    ob_id = store.get("people", pid).benefit
    if not ob_id or not store.has("obligations", ob_id):
        return None
    ob = store.get("obligations", ob_id)
    if ob.status not in SCHEDULED or (ob.last is not None and ob.last < store.day):
        return None
    return ob.id


def end_benefit(store: Store, pid: str, last_day: int, cause: str) -> None:
    """End the person's benefit after `last_day` (today, if that has gone), and tell them."""
    ob_id = benefit_held(store, pid)
    if ob_id is None:
        return
    obligations.end(store, ob_id, max(last_day, store.day), cause)
    tell(store, pid, f"Jobseeker's benefit is not paid after {date_text(last_day)}.", cause)


def _region(store: Store) -> str | None:
    found = [o.id for o in store.all("orgs") if o.kind == "region"]
    return found[0] if found else None


def pay_benefit(store: Store) -> None:
    """Monday: everyone under pension age whose job has ended, who holds no job (nor one
    agreed to start) and has no benefit, is paid `BENEFIT_WEEKLY` a week by the regional
    office into their household, from the Friday of the week after their last pay (or after
    the last week paid in full ahead by hand)."""
    if dow(store.day) != 0:
        return
    region = _region(store)
    if region is None:
        return
    for p in store.all("people"):
        rows = store.where("jobs", person=p.id)
        if p.age >= PENSION_AGE or not rows or benefit_held(store, p.id) is not None:
            continue
        if any(j.ended == -1 or j.ended >= store.day for j in rows):
            continue
        last = max(j.ended for j in rows)
        for j in rows:                   # never a Friday on which a wage of the job is still due or was paid
            if store.has("obligations", j.wage_ob):
                wage = store.get("obligations", j.wage_ob)
                if wage.status in SCHEDULED and wage.last is not None:
                    last = max(last, wage.last)
                last = max([last] + [d.day for d in store.where("dues", obligation=wage.id)
                                     if d.paid > 0 or d.status in ("due",) + obligations.OWED])
        first = max(last - dow(last) + 7 + FRIDAY, pay_friday(store.day))
        cause = f"routine:benefit:{p.id}"
        ob = obligations.create(store, kind="wage", payer=region, payee=p.household, cents=BENEFIT_WEEKLY,
                                first=first, every=7, memo=BENEFIT_MEMO, ends="both", made_by="routine",
                                cause=cause)
        store.set("people", p.id, cause, benefit=ob)
        tell(store, p.id, f"Jobseeker's benefit of {money(BENEFIT_WEEKLY)} a week starts on {date_text(first)}.",
             cause)


def wage_paid_by_hand(store: Store, owner: str, source: str, payee: str) -> str | None:
    """The wage a payment from `source` to the household `payee` counts toward when paid by
    `owner` by hand: when nothing is owed between the two accounts themselves, the first (by
    id) wage still owed to `payee` by an organisation in the town `owner` owns; else None."""
    if not store.has("households", payee):
        return None
    for o in store.where("obligations", payer=source):
        if o.payee == payee and (o.status in SCHEDULED or obligations.owed_dues(store, o.id)):
            return None
    owned = {o.id for o in store.all("orgs") if o.owner == owner and not o.outside and o.id != source}
    wages = [o for o in store.where("obligations", payee=payee) if o.kind == "wage" and o.payer in owned
             and (o.status in SCHEDULED or obligations.owed_dues(store, o.id))]
    return min(wages, key=lambda o: id_key(o.id)).id if wages else None
