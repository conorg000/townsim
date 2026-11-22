"""People who differ (lane B): facts about each person and their past, drawn once when lane B is installed on a
stream of their own (`Random("traits:<seed>")`), so a place without lane B is as it was and one with it is a
function of its seed alone. None of it is about the product, assistants or anything like them.

- A line of their history with earlier technology, placed on the adopter curve; it takes the place of the
  generator's slogans about technology.
- For about a third of people, something of their own they are trying to make, learn or get done, fitting their
  work and age.
- One or two people whose lead they tend to follow, from their ties: an older relative, their employer, a friend.
- For employers, whether they have a rule for staff about outside software and the employer's records; shown to
  staff as a workplace rule and kept by code (`systems.can_grant`): staff cannot connect the employer's records.
"""

from __future__ import annotations

import random

from townsim.town import jobs, ties
from townsim.town.entities import Trait, WorkRule
from townsim.town.store import Store, id_key

# The adopter curve's five places and their shares: innovators 2.5%, early adopters 13.5%, early majority 34%,
# late majority 34%, laggards 16% (Rogers, Diffusion of Innovations, 5th ed., 2003; a convention cut from a
# roughly normal curve, as the findings say, why-people-adopt.md 1.1 and 1.4).
CURVE = (0.025, 0.135, 0.34, 0.34, 0.16)
# What `gen.check_b` accepts for each share over seeds 1-30 (540 people): about twice the sampling spread.
CURVE_RANGES = ((0.005, 0.05), (0.09, 0.18), (0.28, 0.40), (0.28, 0.40), (0.11, 0.21))
# The history lines, one for each place, from the findings (3F), in the past tense and as plain facts: the first
# keeps only its fact ("and you like trying new things before others do" is left out, being a present leaning,
# not a past act); the fourth says how statements were kept, since "pay the rent by hand" would contradict a rent
# that the place takes each week.
HISTORY = (
    "You were among the first in {place} to bank online.",
    "You set up most of your bills to pay themselves years ago.",
    "You took up online banking once most people you know had.",
    "You use the bank's website when you have to, and kept paper statements long after most people stopped.",
    "You have never owned a computer.",
)
# For someone whose work is at a desk with a computer (the bank, the software firm), the last place's line is
# about home, so as not to contradict their work.
AT_A_DESK = ("banker", "software")
LAST_AT_A_DESK = "You have never banked online, and you keep no computer at home."

# About a third of people have something of their own they are trying to make, learn or get done: the findings'
# figure (3F, "for about a third of people"), an inference [I], not a sourced share.
PROJECT_SHARE = 1 / 3
PROJECT_RANGE = (0.25, 0.42)
# Ordinary aims by what someone does and their age (lowest, highest); none is about software or anything like it.
PROJECTS = {
    "owner": (("You have been meaning to start delivering orders to customers who cannot get to the shop.", 19, 82),
              ("You have been meaning to start a Saturday stall for growers from round about.", 19, 82),
              ("You want to have the shop's front repainted and its sign redone this year.", 19, 82),
              ("You are teaching yourself to keep proper accounts for the shop.", 19, 82)),
    "work": (("You are teaching yourself bookkeeping.", 19, 65),
             ("You have been meaning to start a cleaning round.", 19, 60),
             ("You are studying in the evenings for a qualification in accounts.", 19, 50),
             ("You have been meaning to start a five-a-side football team.", 19, 45),
             ("You are saving for a deposit on a house of your own.", 19, 40),
             ("You are learning to cook properly.", 19, 82),
             ("You would like to start a running club.", 19, 60)),
    "home": (("You have been meaning to start a cleaning round.", 19, 60),
             ("You are teaching yourself bookkeeping.", 19, 65),
             ("You would like to start a playgroup for the small children of {place}.", 19, 55),
             ("You are learning to sew well enough to take in alterations.", 19, 82),
             ("You would like to start a running club.", 19, 60)),
    "retired": (("You would like to write up the history of {place}.", 50, 82),
                ("You have been meaning to start a walking club.", 40, 82),
                ("You are putting together your family's history.", 40, 82),
                ("You would like to start a lunch club for older people in {place}.", 50, 82),
                ("You are learning to paint in watercolours.", 19, 82)),
}
_GROUP = {"owner": "owner", "staff": "work", "banker": "work", "software": "work", "outside_worker": "work",
          "landlord": "retired", "retired": "retired", "unemployed": "home", "home": "home"}

# Whose lead someone tends to follow: one person, or two about two times in five (ordinary; the findings give
# no figure). An older relative is someone of their family at least ten years older.
TWO_FOLLOWED = 0.4
OLDER = 10

# How often an employer has a rule that staff may not give outside software access to its records. The findings
# give no share (1.2: employer restrictions were a main barrier in Denmark, Humlum and Vestergaard 2024), so these
# are ordinary figures: a bank keeps its customers' records under duties of confidence and as a matter of course
# forbids it; a larger employer beyond the place often has a policy on outside software; a small shop rarely
# has anything written down. The software firm is left without one: it writes such software.
RULE_CHANCE = {"bank": 0.8, "employer": 0.5, "shop": 0.1}
RULE_RANGE = (0.2, 0.45)
RULE = "staff may not give outside software access to its records"

# Words no person's description may hold (`gen.check_b`), besides the product's name.
NOT_DESCRIBED = ("assistant", " ai ", " ai.", " ai,", "artificial intelligence", "chatbot", "automat")


def _group(kind: str) -> str:
    return _GROUP.get(kind, "home")


def _projects(store: Store, p) -> list[str]:
    return [text.format(place=store.place) for text, lo, hi in PROJECTS[_group(p.kind)] if lo <= p.age <= hi]


def _candidates(store: Store, pid: str) -> list[str]:
    """Whose lead `pid` might follow, from their ties: older relatives, their employer, their friends."""
    me = store.get("people", pid)
    got = []
    for other, kinds in ties.kinds(store, pid).items():
        if not store.has("people", other):
            continue
        age = store.get("people", other).age
        family = any(ties.base(k) in ("parent", "kin", "sibling", "partner") for k in kinds)
        if family and age >= me.age + OLDER or "employer" in kinds or "friend" in kinds:
            got.append(other)
    return sorted(got, key=id_key)


def draw(store: Store, cause: str) -> None:
    """Draw everyone's traits and the employers' rules, and keep them; the generator's technology slogans are
    taken out of everyone's beliefs. Drawing again changes nothing."""
    if store.all("traits"):
        return
    from townsim.town.gen import BELIEFS
    slogans = {b for pole in BELIEFS["technology"] for b in pole}
    rng = random.Random(f"traits:{store.seed}")
    for p in store.all("people"):
        curve, project, which, two, a, b = (rng.random() for _ in range(6))
        place = 0
        while place < len(CURVE) - 1 and curve >= sum(CURVE[:place + 1]):
            place += 1
        options = _projects(store, p)
        mine = options[int(which * len(options))] if options and project < PROJECT_SHARE else ""
        pool = _candidates(store, p.id)
        follows = []
        if pool:
            first = pool[int(a * len(pool))]
            follows = [first]
            rest = [q for q in pool if q != first]
            if rest and two < TWO_FOLLOWED:
                follows.append(rest[int(b * len(rest))])
        history = LAST_AT_A_DESK if place == len(HISTORY) - 1 and p.kind in AT_A_DESK else HISTORY[place]
        store.add("traits", Trait(p.id, p.id, place, history.format(place=store.place), mine,
                                  sorted(follows, key=id_key)), cause)
        if any(x in slogans for x in p.beliefs):
            store.set("people", p.id, cause, beliefs=[x for x in p.beliefs if x not in slogans])
    for org in store.all("orgs"):
        chance = RULE_CHANCE.get(org.kind)
        drawn = rng.random()
        if chance is not None and (not org.outside or org.kind == "employer") and drawn < chance:
            store.add("workrules", WorkRule(org.id, org.id, RULE.format(name=org.name)), cause)


def rule_of(store: Store, org: str) -> WorkRule | None:
    return store.get("workrules", org) if store.has("workrules", org) else None


def rule_against(store: Store, pid: str, org: str) -> str | None:
    """The rule of `org`'s that keeps `pid` (not its owner) from giving outside software access to its records, as
    a sentence; None when there is none."""
    rule = rule_of(store, org)
    if rule is None or (store.has("orgs", org) and store.get("orgs", org).owner == pid):
        return None
    return f"{store.get('orgs', org).name} has a rule: {rule.text}."


def lines(store: Store, pid: str) -> list[str]:
    """What a person's own description says of their past, their own aim, whose lead they follow and their
    workplace's rule; nothing without lane B."""
    if not store.has("traits", pid):
        return []
    t = store.get("traits", pid)
    out = [t.history]
    if t.project:
        out.append(t.project)
    if t.follows:
        names = [store.get("people", q).name for q in t.follows if store.has("people", q)]
        if names:
            out.append(f"You tend to follow the lead of {' and '.join(names)}.")
    for org in dict.fromkeys(j.org for j in jobs.held(store, person=pid)):
        rule = rule_of(store, org)
        if rule is None:
            continue
        name = store.get("orgs", org).name
        if store.get("orgs", org).owner == pid:
            out.append(f"Your rule at {name}: {rule.text}.")
        else:
            out.append(f"A rule at {name}, where you work: {rule.text}.")
    return out
