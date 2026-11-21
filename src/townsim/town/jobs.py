"""Jobs: a person working hours a week for an organisation, paid a weekly wage.

A job is a `Job` row and its wage, an obligation of kind `wage` from the employer's account
into the person's household account, ended only by both. A job that ends keeps its row:
`ended` is the last day worked, and from the day after it the job no longer counts (see
`current`)."""

from __future__ import annotations

from townsim.town import obligations
from townsim.town.entities import Job
from townsim.town.store import Store


def current(job: Job, day: int) -> bool:
    """Whether `job` is held on `day`: it has not ended, or ends on or after that day."""
    return job.ended == -1 or job.ended >= day


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
    worked. Raises ValueError, changing nothing, for an unknown or already ended job or a
    `last_pay_day` before today."""
    if not isinstance(job_id, str) or not store.has("jobs", job_id):
        raise ValueError(f"no job {job_id!r}")
    job = store.get("jobs", job_id)
    if job.ended != -1:
        raise ValueError(f"{job_id} already ended on day {job.ended}")
    with store.transaction():
        if store.has("obligations", job.wage_ob):
            obligations.end(store, job.wage_ob, last_pay_day, cause)
        elif not isinstance(last_pay_day, int) or last_pay_day < store.day:
            raise ValueError(f"last_pay_day must be an int >= today ({store.day}), got {last_pay_day!r}")
        store.set("jobs", job_id, cause, ended=last_pay_day)
