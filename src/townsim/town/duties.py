"""Caring and second jobs (lane B): hours some people's weeks give to someone they look after, or to a second
job beyond the place, drawn once when lane B is installed on a stream of their own (`Random("duties:<seed>")`),
so a place without lane B is as it was and one with it is a function of its seed alone.

The shares are ordinary figures; the findings (why-people-adopt.md, 3B and departure 11) say only that some
people care for a parent or a child or hold a second job, and give no rates.
"""

from __future__ import annotations

import random

from townsim.town import hours
from townsim.town.entities import Duty
from townsim.town.store import Store

# About one adult in eight gives unpaid care to someone each week: between the 2021 census of England and
# Wales (about 9% of people) and the US figure for adults caring for a relative or friend (about 21%, AARP
# and NAC 2020). Both recalled, not checked this session.
CARE_SHARE = 0.12
CARE_AGES = (25, 79)
# Hours of care a week: most carers give under 20 (the same census: about half of carers give 19 hours or
# fewer; the US survey's mean is about 24). Recalled, not checked.
CARE_HOURS = (5, 20)
# About one employed person in twenty holds a second job (US BLS multiple jobholders, about 5% of the
# employed in 2023-24). Recalled, not checked.
SECOND_JOB_SHARE = 0.05
# A second job is mostly a few evening or weekend shifts: 8 to 16 hours a week (ordinary; no figure found).
SECOND_JOB_HOURS = (8, 16)

# Whom someone looks after, by their age: a parent or a parent-in-law beyond the place for those of working age,
# a child of the family after school, or for the old a brother or a neighbour.
CARED_FOR = {
    (25, 44): ("caring for your mother, who lives in {near}", "caring for your father, who lives in {near}",
               "looking after your sister's children after school"),
    (45, 64): ("caring for your mother, who lives in {near}", "caring for your father, who lives in {near}",
               "caring for your mother-in-law, who lives in {near}", "looking after your grandchildren after school"),
    (65, 79): ("looking after your grandchildren after school", "caring for your brother, who lives in {near}",
               "looking in on a housebound neighbour in {near}"),
}
SECOND_JOBS = ("evening shifts behind the bar of a pub in {near}", "weekend shifts at a garden centre in {near}",
               "cleaning offices in {near} in the evenings", "delivering takeaway meals in {near} at weekends")


def _near(store: Store) -> str:
    market = next((o for o in store.all("orgs") if o.kind == "market"), None)
    return market.name.removeprefix("Shops and services in ") if market is not None else "town"


def _parents_here(store: Store, pid: str) -> bool:
    """Whether someone of `pid`'s family who could be their parent lives in the place."""
    from townsim.town import ties
    age = store.get("people", pid).age
    return any(k in ("parent", "kin") and store.get("people", other).age >= age + 16
               for other, kinds in ties.kinds(store, pid).items() for k in kinds)


def draw(store: Store, cause: str) -> None:
    """Draw each person's duties (at most one) and keep them; drawing again changes nothing."""
    if store.all("duties"):
        return
    rng = random.Random(f"duties:{store.seed}")
    near = _near(store)
    for p in store.all("people"):
        care, job = rng.random(), rng.random()         # both drawn for everyone, so one draw does not move another
        h_care, h_job = rng.randint(*CARE_HOURS), rng.randint(*SECOND_JOB_HOURS)
        pick, pick_job = rng.random(), rng.random()
        if CARE_AGES[0] <= p.age <= CARE_AGES[1] and care < CARE_SHARE:
            band = next(b for b in CARED_FOR if b[0] <= p.age <= b[1])
            options = [o for o in CARED_FOR[band] if not ("your mother" in o or "your father" in o)
                       or not _parents_here(store, p.id)]
            what = options[int(pick * len(options))].format(near=near)
            store.add("duties", Duty(p.id, p.id, "care", what, float(h_care)), cause)
        elif hours.job_hours(store, p.id) > 0 and p.age < 66 and job < SECOND_JOB_SHARE:
            what = SECOND_JOBS[int(pick_job * len(SECOND_JOBS))].format(near=near)
            store.add("duties", Duty(p.id, p.id, "job", what, float(h_job)), cause)


def line(store: Store, pid: str) -> list[str]:
    """`Each week you spend 12 hours caring for your mother, who lives in Iverby.` for the person's own
    description; nothing for someone with no duties."""
    return [f"Each week you spend {d.hours:g} hours {d.what}." if d.kind == "care" else
            f"You also work {d.hours:g} hours a week: {d.what}." for d in store.all("duties") if d.person == pid]
