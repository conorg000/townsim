"""Hours: what a person's week leaves them once their job and recurring work are done.

Each Monday a person's hours for the week are `WEEK_HOURS`, less the hours of their job,
less the hours of the recurring work they hold. An ill week has none.
"""

from __future__ import annotations

from townsim.town import jobs
from townsim.town.clock import dow
from townsim.town.entities import Work
from townsim.town.store import Store

WEEK_HOURS = 70.0
WORK_HOURS = {"household paperwork": 1.5, "ordering": 4.0, "the books": 3.0, "rota and pay": 2.0}


def job_hours(store: Store, pid: str) -> float:
    """The hours a week of the jobs `pid` holds today."""
    return sum(j.hours for j in jobs.held(store, person=pid))


def work_hours(store: Store, pid: str) -> float:
    """The hours a week of the recurring work `pid` holds."""
    return sum(w.hours for w in store.where("work", holder=pid))


def free_hours(store: Store, pid: str) -> float:
    """`pid`'s hours for the week beginning today: never below 0, and 0 if they are ill today."""
    if store.get("people", pid).ill_until >= store.day:
        return 0.0
    return round(max(0.0, WEEK_HOURS - job_hours(store, pid) - work_hours(store, pid)), 2)


def give(store: Store, holder: str, account: str, what: str, cause: str) -> str:
    """Give `holder` the recurring work `what` for `account`. Returns the work's id."""
    if what not in WORK_HOURS:
        raise ValueError(f"no kind of work called {what!r}")
    wid = store.new_id("wk", cause)
    store.add("work", Work(wid, holder, account, what, WORK_HOURS[what]), cause)
    return wid


def monday_hours(store: Store) -> None:
    """On a Monday, set everyone's hours for the week and hours left to their free hours."""
    if dow(store.day) != 0:
        return
    for p in store.all("people"):
        free = free_hours(store, p.id)
        changed = {k: free for k in ("hours_week", "hours_left") if getattr(p, k) != free}
        if changed:
            store.set("people", p.id, "routine:week", **changed)
