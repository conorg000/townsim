"""Hours: what a person's week leaves them once their job and recurring work are done.

Each Monday a person's hours for the week are `WEEK_HOURS`, less the hours of their job,
less the hours of the recurring work they hold. An ill week has none.
"""

from __future__ import annotations

from townsim.town import jobs
from townsim.town.clock import dow
from townsim.town.entities import Work
from townsim.town.store import Store, id_key

WEEK_HOURS = 70.0
WORK_HOURS = {"household paperwork": 1.5, "ordering": 4.0, "the books": 3.0, "rota and pay": 2.0}

# With lane B an owner's work for a business is itemised: an item takes its hours in the week its holder does it
# (the first act of its kind that week, `recurring`), and none in a week they do not, or someone else did it.
# The sizes are the findings' (why-people-adopt.md, 3B), there an inference [I], not a sourced figure: ordering
# about 3 hours, with reading the week's stock and sales; the books about 3, with going through the accounts;
# rota and pay about 2. (Small shop owners' 50-60 hours a week, which these sit inside, is a recollection the
# findings did not check.)
BUSINESS_WORK = {"ordering": 3.0, "the books": 3.0, "rota and pay": 2.0}


def job_hours(store: Store, pid: str) -> float:
    """The hours a week of the jobs `pid` holds today."""
    return sum(j.hours for j in jobs.held(store, person=pid))


def work_hours(store: Store, pid: str) -> float:
    """The hours a week of the recurring work `pid` holds."""
    return sum(w.hours for w in store.where("work", holder=pid))


def itemised(store: Store, w: Work) -> bool:
    """Whether a piece of recurring work takes its hours only when it is done (lane B's business work)."""
    from townsim.town import lane_b
    return w.what in BUSINESS_WORK and lane_b.installed(store)


def duty_hours(store: Store, pid: str) -> float:
    """The hours a week of `pid`'s caring and second job (lane B's `duties`)."""
    return sum(d.hours for d in store.all("duties") if d.person == pid)


def weekly_hours(store: Store, pid: str) -> float:
    """The hours taken from `pid`'s week before it begins: their jobs, the recurring work that is not itemised,
    and their duties."""
    flat = sum(w.hours for w in store.where("work", holder=pid) if not itemised(store, w))
    return job_hours(store, pid) + flat + duty_hours(store, pid)


def committed_hours(store: Store, pid: str) -> float:
    """The hours of `pid`'s week that their jobs' other work holds: all their recurring work and their duties."""
    return work_hours(store, pid) + duty_hours(store, pid)


def free_hours(store: Store, pid: str) -> float:
    """`pid`'s hours for the week beginning today: never below 0, and 0 if they are ill today."""
    if store.get("people", pid).ill_until >= store.day:
        return 0.0
    return round(max(0.0, WEEK_HOURS - weekly_hours(store, pid)), 2)


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


# --- what a person is told of their own time -------------------------------------------------

_FRACTIONS = {0.25: "¼ hour", 0.5: "½ hour", 0.75: "¾ hour", 1.0: "1 hour"}


def length(h: float) -> str:
    """Hours as said: `¼ hour`, `½ hour`, `1 hour`, `6 minutes`, `2.5 hours`."""
    h = round(h, 2)
    if h in _FRACTIONS:
        return _FRACTIONS[h]
    if h < 1:
        return f"{round(h * 60):g} minutes"
    return f"{h:g} hours"


def ill(store: Store, pid: str) -> bool:
    return store.get("people", pid).ill_until >= store.day


def returns_on(store: Store, pid: str) -> int:
    """The Monday a person's own time comes back: the next one, or after their illness."""
    from townsim.town.jobs import next_monday
    p = store.get("people", pid)
    return next_monday(max(store.day, p.ill_until))


def ill_line(store: Store, pid: str) -> str:
    """`You are ill until <last day>: you are not at work and have no time of your own until
    <the Monday after>.`"""
    from townsim.town.clock import date_text
    p = store.get("people", pid)
    return (f"You are ill until {date_text(p.ill_until)}: you are not at work and have no time of your own until "
            f"{date_text(p.ill_until + 1)}.")


def _cause_op(cause: str) -> str:
    """The operation a cause names (`op:<who>:<op>:d<day>.<n>`), or ""."""
    parts = cause.split(":")
    return parts[2] if len(parts) >= 4 and parts[0] == "op" else ""


def _spent(store: Store, changes: list, pid: str, monday: int) -> dict[str, list]:
    """What took `pid`'s own hours in `changes` from `monday`, newest first: what -> [hours, count]."""
    from townsim.town.clock import DAYS
    spent: dict[str, list] = {}            # what -> [hours, count]; in the order first met
    items = {r.id: store.get("work", r.work).what for r in store.all("returned")
             if r.monday == monday and r.by == pid and store.has("work", r.work)}
    item_of = {c.cause: items[c.id] for c in changes if c.table == "returned" and c.field == "+"
               and c.id in items}
    for c in reversed(changes):
        if c.day < monday:
            break
        if c.table != "people" or c.id != pid or c.field != "hours_left" or not isinstance(c.old, (int, float)) \
                or not isinstance(c.new, (int, float)) or c.new >= c.old \
                or not c.cause.startswith(("op:", "routine:talk:")):
            continue
        gone = round(c.old - c.new, 2)
        op = _cause_op(c.cause)
        if c.cause in item_of:
            key = f"the week's {item_of[c.cause].removeprefix('the ')}"
        elif op == "work_extra_hours":
            shop = next((o.name for o in store.all("orgs") if o.owner == pid and store.where("firms", org=o.id)), "")
            key = f"extra hours worked at {shop} on {DAYS[dow(c.day)]}" if shop else f"extra hours worked on {DAYS[dow(c.day)]}"
        elif op == "answer" and gone > 0.1:
            key = f"extra hours accepted on {DAYS[dow(c.day)]}"
        elif c.cause.startswith("routine:talk:"):
            key = "talks"
        elif op == "message":
            key = "letters"
        else:
            key = "other things done"
        got = spent.setdefault(key, [0.0, 0])
        got[0] += gone
        got[1] += 1
    return spent


def note_spent(store: Store, changes: list) -> None:
    """With lane B, at the end of the day: keep what took each person's own hours today (from the day's changes,
    which a day run from its morning always holds), so that what used the week's hours is a record."""
    from townsim.town.entities import Spent
    today = [c for c in changes if c.day == store.day]
    people = sorted({c.id for c in today if c.table == "people" and c.field == "hours_left"}, key=id_key)
    cause = "routine:spent"
    for pid in people:
        for what, (h, n) in _spent(store, today, pid, store.day - dow(store.day)).items():
            store.add("spent", Spent(store.new_id("ht", cause), pid, store.day, what, round(h, 2), n), cause)


def used(store: Store, pid: str) -> str:
    """What used `pid`'s own time this week: their illness; or the largest three of the extra hours they worked or
    accepted, the talks they had, the letters they wrote and their other acts, each with its hours; or that their
    work takes the week. Without lane B it is read from the change log; with lane B from the kept record of earlier
    days this week (`note_spent`, so it survives a snapshot) and today's changes, which a day always holds."""
    from townsim.town.clock import date_text
    p = store.get("people", pid)
    if p.ill_until >= store.day:
        return f"you are ill until {date_text(p.ill_until)}"
    monday = store.day - dow(store.day)
    from townsim.town import lane_b
    if lane_b.installed(store):
        today = [c for c in store.changes if c.day == store.day]
        spent = _spent(store, today, pid, monday)
        for r in sorted((r for r in store.all("spent") if r.person == pid and monday <= r.day < store.day),
                        key=lambda r: (-r.day, id_key(r.id))):
            got = spent.setdefault(r.what, [0.0, 0])
            got[0] += r.hours
            got[1] += r.n
    else:
        spent = _spent(store, store.changes, pid, monday)
    if not spent:
        if p.hours_week <= 0:
            return f"your work takes all {WEEK_HOURS:g} hours of the week"
        return f"your work leaves {length(p.hours_week)} of the week's {WEEK_HOURS:g} hours"
    top = sorted(spent.items(), key=lambda kv: -kv[1][0])[:3]
    out = []
    for what, (h, n) in top:
        h = round(h, 2)
        if what.startswith("extra hours"):
            out.append(f"{h:g} {what}")
        elif what in ("talks", "letters"):
            out.append(f"{n} {what if n != 1 else what[:-1]}, {length(h)}")
        else:
            out.append(f"{what}, {length(h)}")
    return "; ".join(out)


def no_time(store: Store, pid: str, needed: float, work: str = "") -> str:
    """Why an act that takes `needed` hours cannot be done for want of time (without
    `Not done:`): what is left, what used the rest, and the day it comes back. `work` names the
    week's recurring work the act would do, whose hours `needed` holds."""
    from townsim.town.clock import date_text
    left = store.get("people", pid).hours_left
    back = f" It returns on {date_text(returns_on(store, pid))}."
    if left <= 0:
        return f"you have no time of your own left this week ({used(store, pid)}).{back}"
    takes = f"this, with the week's {work.removeprefix('the ')}, takes" if work else "this takes"
    return (f"you have {round(left, 2):g} hours of your own left this week, and {takes} {length(needed)} "
            f"({used(store, pid)}).{back}")


def week_line(store: Store, pid: str) -> str:
    """With lane B, the reader's week so far as a record: `Your week: Harlow Springs Food Mart 39 hours; caring
    for your mother, who lives in Iverby, 10 hours; household paperwork 1.5 hours; ordering 3 hours; rota and pay
    done by Tessaly Assistant for Harlow Springs Food Mart; of your own, 13.5 hours left.` Work not yet done is
    not named."""
    from townsim.town import recurring
    parts = []
    for j in jobs.held(store, person=pid):
        org = store.get("orgs", j.org).name if store.has("orgs", j.org) else j.org
        parts.append(f"{org} {length(j.hours)}")
    parts += [f"{d.what}, {length(d.hours)}" for d in store.all("duties") if d.person == pid]
    parts += [f"{w.what} {length(w.hours)}" for w in store.where("work", holder=pid) if not itemised(store, w)]
    for what, who, h in recurring.done_this_week(store, pid):
        parts.append(f"{what} done by {who}" if who else f"{what} {length(h)}")
    left = round(store.get("people", pid).hours_left, 2)
    parts.append(f"of your own, {length(left)} left" if left > 0 else "none of your own left")
    return "Your week: " + "; ".join(parts) + "."
