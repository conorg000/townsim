"""A generated hamlet: eighteen people, their houses, three food shops, a bank, a landlord, a
small software firm, jobs, debts, ties, beliefs, worries and a dated deck of ordinary events.

`generate(seed, families)` composes a place from one random stream,
`Random(f"gen:{seed}:{attempt}")`, writes it into a `Store` at day 0, and keeps it if
`check` finds it structurally sound; otherwise it draws again with the next attempt. A place
is made once and frozen by its seed: nothing here looks at how a run of it goes.

Money is drawn from ordinary shares. Each household's income comes first (wages for hours
at an hourly rate, a pension or benefit, rents); its outgoings are shares of that income
(housing 25-35%, food 12-18%, energy 4-7%, and other regular spending for the rest less a
margin), with debts on top for those drawn to have them. The shops' prices, quality and
standing orders start near `econ`'s reference figures, and their suppliers are econ's two.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from townsim.econ.demand import NEEDS, DemandRule, Offering, true_choice_probabilities
from townsim.econ.demand import Household as EconHousehold
from townsim.econ.suppliers import default_suppliers
from townsim.town import acts, firms, hours, names
from townsim.town import obligations as O
from townsim.town.clock import DAY_ZERO, date_text
from townsim.town.entities import Buyer, Event, FirmRow, Household, Job, Org, Person, SupplierRow, Tie
from townsim.town.obligations import money
from townsim.town.store import Store, id_key

SETUP = "routine:setup"
MAX_ATTEMPTS = 200
PEOPLE = 18
OPENING = "outside:opening"      # where opening balances come from
MONDAY, WEDNESDAY, FRIDAY, SATURDAY = 0, 2, 4, 5
MIN_WAGE = 1100                  # cents an hour

# Who is there: the kinds of person and how many of each; the other two are drawn from REST.
KINDS = (("owner", 3), ("staff", 6), ("banker", 1), ("landlord", 1), ("software", 2), ("outside_worker", 3))
REST = ("retired", "unemployed", "home")
REST_WEIGHTS = (5, 3, 2)
SHOP_STAFF = (4, 1, 1)           # staff besides the owner, largest shop first
LETS = 3
IN_TOWN = 0.78                   # share of a buyer's food wants within willingness to pay near the usual price

AGES = {"owner": (28, 64), "staff": (19, 64), "banker": (27, 62), "landlord": (52, 79), "software": (23, 58),
        "outside_worker": (19, 63), "retired": (66, 82), "unemployed": (19, 64), "home": (21, 64)}
PENSION_AGE = 66

# Bands every draw uses: (low, high). Cents unless said otherwise.
HOURLY = {"owner": (1200, 1600), "staff": (1150, 1400), "banker": (1700, 2400), "software_owner": (2200, 3200),
          "software": (1800, 2800), "outside_worker": (1300, 2200)}
JOB_HOURS = {"owner": (38, 48), "staff": (16, 38), "banker": (35, 40), "software_owner": (38, 45),
             "software": (35, 40), "outside_worker": (30, 40)}
PENSION = (28_000, 42_000)
BENEFIT = (23_000, 32_000)
HOUSING, FOOD, ENERGY = (0.25, 0.35), (0.12, 0.18), (0.04, 0.07)
MORTGAGE_END_AGE = 75
LANDLORD_JOB_CHANCE = 0.5
LANDLORD_JOB_HOURS = (12, 24)
MARGIN = (-0.08, 0.12)           # what a household's regular outgoings leave of its income
PERSONAL_LOAN_CHANCE = 0.3
PERSONAL_LOAN_SHARE = (0.03, 0.07)
OPENING_WEEKS = (1.0, 4.0)
PRICE = (0.99, 1.11)             # times econ's reference price
QUALITY = (0.52, 0.68)
VISIBILITY = {"large": (0.6, 0.85), "small": (0.3, 0.55)}
REPUTATION = (0.45, 0.55)
UNITS_PER_38_HOURS = (135, 185)
SHOP_FIXED = {"large": (15_000, 35_000), "small": (8_000, 18_000)}
SHOP_LOAN = (4_000, 9_000)
INVESTOR_OVER_WAGES = (1.02, 1.15)

LOAN_PURPOSES = ("car loan", "loan for a new kitchen", "loan for a van", "loan for a new boiler",
                 "loan for the roof")

VOICES = [
    "You write short, plain sentences.", "You write at length and wander off the point.",
    "You write politely and a little formally.", "You write in a hurry, with few full stops.",
    "You write warmly and ask after people.", "You write bluntly and get to the point.",
    "You write carefully, checking figures twice.", "You write with a dry sense of humour.",
    "You write in lowercase, like a text message.", "You write as little as possible.",
    "You write cheerfully, with exclamation marks.", "You write anxiously, apologising often.",
    "You write in a businesslike way, with lists.", "You write the way you talk, with local sayings.",
]

INTERESTS = [
    "gardening", "fishing", "football", "cricket", "darts", "crosswords", "baking", "knitting", "hill walking",
    "birdwatching", "local history", "cycling", "crime novels", "the pub quiz", "the church choir", "bowls",
    "toy trains", "cooking", "the allotment", "walking the dog", "family history", "jigsaw puzzles",
    "woodwork", "watercolours", "photography", "horse racing", "snooker", "bingo", "running", "swimming",
    "ballroom dancing", "cards", "dominoes", "fixing up old cars", "chess", "sewing", "old films", "country music",
    "keeping hens",
]

# Axis -> (one pole's wordings, the other's).
BELIEFS: dict[str, tuple[list[str], list[str]]] = {
    "institutions": (
        ["Banks and councils mostly do right by people.", "The people in charge generally know what they are doing.",
         "The council can usually be trusted.", "Most officials do their jobs honestly."],
        ["The council looks after its own.", "Banks are out for themselves.", "Official letters are best read twice.",
         "Those in charge rarely tell the whole story."]),
    "change": (
        ["Things should change faster round here.", "New ways are usually better ways.",
         "A place that stands still goes backwards.", "It does people good to try new things."],
        ["Things were better done the old way.", "Change is mostly for the worse.", "Things that work are best left alone.",
         "The village was a better place twenty years ago."]),
    "technology": (
        ["New gadgets make life easier.", "Computers save more time than they waste.",
         "Most new inventions are worth having.", "Phones and computers have made life better."],
        ["Gadgets always break just when they are needed.", "Computers make more work than they save.",
         "Nothing beats a proper conversation over the counter.", "Too much is done on screens now."]),
    "religion": (
        ["Church is the best part of the week.", "Faith matters a great deal.", "A prayer never did anyone harm.",
         "God has a plan for everyone."],
        ["Religion has never meant much.", "Church is for weddings and funerals.", "There is no God.",
         "Religion causes more trouble than it is worth."]),
    "politics": (
        ["The rich should pay more tax.", "Workers need strong unions.", "Public services should be properly paid for.",
         "Big companies have too much say."],
        ["Taxes are too high.", "People should stand on their own feet.", "The less government the better.",
         "Businesses know better than politicians."]),
    "risk": (
        ["Nothing ventured, nothing gained.", "A good chance is worth a gamble.", "Better to try and fail than never try.",
         "Fortune favours the bold."],
        ["Better safe than sorry.", "Borrowing more than can be paid back is asking for trouble.", "It pays to keep something put by for a rainy day.",
         "Putting all your eggs in one basket is foolish."]),
}

EMPLOYERS = (
    ("{p} Haulage", ["drives for {e}", "loads lorries at {e}", "works in the office at {e}", "fixes lorries at {e}"]),
    ("{p} Foods", ["works on the packing line at {e}", "drives a forklift at {e}", "works in the office at {e}",
                   "cleans the equipment at {e}"]),
    ("{p} General Hospital", ["works as a porter at {e}", "works in the kitchens at {e}", "nurses at {e}",
                              "works on reception at {e}"]),
    ("{p} Quarry", ["drives a dumper at {e}", "works in the weighbridge office at {e}", "maintains the plant at {e}",
                    "works on the crusher at {e}"]),
)
STAFF_LINES = ("works on the till at {s}", "stacks shelves at {s}", "serves behind the counter at {s}",
               "works at {s}")
HOME_LINES = ("keeps house", "looks after the home")


def when(day: int) -> str:
    """A day as written, with its year when it is not 2026: `Monday 26 April 2027`."""
    year = (DAY_ZERO + timedelta(days=day)).year
    return date_text(day) + ("" if year == DAY_ZERO.year else f" {year}")


def weekly_in(store: Store, account: str) -> int:
    """Cents a week paid into `account` by its weekly obligations."""
    return sum(o.cents for o in store.where("obligations", payee=account) if o.every == 7 and o.status == "active")


def expected_food(size: int, price: float) -> int:
    """Cents a week a household of `size` can be expected to spend on food at `price` a unit:
    its expected wants (as `econ` has them) that fall within willingness to pay (`IN_TOWN`)."""
    spec = NEEDS["food"]
    return round(spec.wants_per_person * size ** spec.size_exponent * price * IN_TOWN)


def food_spend(store: Store, account: str) -> int:
    """What a household can be expected to spend on food a week at the shops' average price.
    Its `Buyer.budget` is the most it will spend, not what it spends."""
    prices = [f.price for f in store.all("firms") if f.need == firms.NEED]
    if not prices:
        return 0
    price = sum(prices) / len(prices)
    return sum(expected_food(b.size, price) for b in store.all("buyers") if b.account == account)


def weekly_out(store: Store, account: str) -> int:
    """Cents a week a household pays out by its weekly obligations, and its expected food spending."""
    out = sum(o.cents for o in store.where("obligations", payer=account) if o.every == 7 and o.status == "active")
    return out + food_spend(store, account)


class _Redraw(Exception):
    """This attempt cannot be completed; draw again."""


@dataclass
class _P:
    """A person while the place is being drawn."""

    kind: str
    age: int = 0
    first: str = ""
    surname: str = ""
    relation: str = ""          # head, partner, child, sibling, or "" when living alone
    id: str = ""


@dataclass
class _H:
    members: list[_P]
    form: str
    surname: str = ""
    address: str = ""
    id: str = ""
    tenure: str = ""            # landlord, agency, mortgage or outright
    shares: dict = field(default_factory=dict)


def _r100(x: float) -> int:
    """Cents rounded to whole dollars, at least $1."""
    return max(100, int(round(x / 100)) * 100)


def _u(rng: random.Random, band: tuple[float, float]) -> float:
    return rng.uniform(*band)


# --- the people and their houses ---------------------------------------------------------


def _households(rng: random.Random) -> list[_H]:
    kinds = [k for k, n in KINDS for _ in range(n)] + rng.choices(REST, weights=REST_WEIGHTS, k=PEOPLE - 16)
    rng.shuffle(kinds)
    n = rng.randint(7, 9)
    sizes = [1] * n
    for _ in range(PEOPLE - n):
        sizes[rng.choice([i for i in range(n) if sizes[i] < 4])] += 1
    house = [i for i, size in enumerate(sizes) for _ in range(size)]
    soft = [i for i, k in enumerate(kinds) if k == "software"]
    if house[soft[0]] == house[soft[1]]:       # the two at the software firm live apart
        swap = rng.choice([i for i in range(PEOPLE) if house[i] != house[soft[1]]])
        kinds[soft[1]], kinds[swap] = kinds[swap], kinds[soft[1]]
    out, at = [], 0
    for size in sizes:
        members = [_P(k) for k in kinds[at:at + size]]
        at += size
        if all(m.kind == "home" for m in members):
            for m in members:
                m.kind = "unemployed"
        # elders first, so that a household's parents come from the kinds that can be older
        members.sort(key=lambda m: (-AGES[m.kind][0], -AGES[m.kind][1]))
        if size == 1:
            forms = ["single"]
        elif size == 2:
            forms = [rng.choices(("couple", "parent and child", "siblings"), weights=(70, 15, 15))[0]]
        else:
            forms = [rng.choices(("family", "single parent", "siblings"), weights=(75, 15, 10))[0]]
        h = _H(members, forms[0])
        for form in forms + ["couple", "family", "parent and child", "single parent", "siblings", "housemates"]:
            if _form_fits(h.members, form):
                h.form = form
                break
        out.append(h)
    return out


_GAP = {"couple": 8, "siblings": 12}
_CHILD_AGES = (19, 42)


def _parents(form: str) -> int:
    return 1 if form in ("parent and child", "single parent") else 2


def _form_fits(m: list[_P], form: str) -> bool:
    """Whether members of these kinds can be ages that make them this form of household."""
    if form in ("single", "housemates"):
        return form == "single" or len(m) > 1
    if form in _GAP:
        if form == "couple" and len(m) != 2:
            return False
        lo = max(AGES[x.kind][0] for x in m)
        hi = min(AGES[x.kind][1] for x in m)
        return lo - hi <= _GAP[form]
    k = _parents(form)
    if (form in ("parent and child",)) != (len(m) == 2) or len(m) <= k:
        return False
    child_lo = max(max(AGES[c.kind][0], _CHILD_AGES[0]) for c in m[k:])
    if any(max(AGES[c.kind][0], _CHILD_AGES[0]) > min(AGES[c.kind][1], _CHILD_AGES[1]) for c in m[k:]):
        return False
    parents = m[:k]
    if any(AGES[x.kind][1] < child_lo + 20 for x in parents):
        return False
    lo = max(max(AGES[x.kind][0], child_lo + 20) for x in parents)
    hi = min(AGES[x.kind][1] for x in parents)
    return k == 1 or lo - hi <= 8


def _ages(rng: random.Random, h: _H) -> None:
    """Draw ages that make the household what its form says, within each kind's ages."""
    m = h.members
    if h.form in ("single", "housemates"):
        for x in m:
            x.age, x.relation = rng.randint(*AGES[x.kind]), ("" if h.form == "single" else "housemate")
        m[0].relation = "" if h.form == "single" else "head"
        return
    if h.form in _GAP:
        gap = _GAP[h.form]
        lo = max([AGES[m[0].kind][0]] + [AGES[x.kind][0] - gap for x in m[1:]])
        hi = min([AGES[m[0].kind][1]] + [AGES[x.kind][1] + gap for x in m[1:]])
        m[0].age, m[0].relation = rng.randint(lo, hi), "head"
        for x in m[1:]:
            x.age = rng.randint(max(AGES[x.kind][0], m[0].age - gap), min(AGES[x.kind][1], m[0].age + gap))
            x.relation = "partner" if h.form == "couple" else "sibling"
        return
    k = _parents(h.form)
    parents, children = m[:k], m[k:]
    top = min(AGES[x.kind][1] for x in parents) - 20
    for c in children:
        c.age = rng.randint(max(AGES[c.kind][0], _CHILD_AGES[0]), max(max(AGES[c.kind][0], _CHILD_AGES[0]),
                                                                       min(AGES[c.kind][1], _CHILD_AGES[1], top)))
        c.relation = "child"
    eldest = max(c.age for c in children)
    lo = max([AGES[m[0].kind][0], eldest + 20] + [AGES[x.kind][0] - 8 for x in parents[1:]])
    hi = min([AGES[m[0].kind][1]] + [AGES[x.kind][1] + 8 for x in parents[1:]])
    if lo > hi:
        raise _Redraw
    m[0].age, m[0].relation = rng.randint(lo, hi), "head"
    if k == 2:
        m[1].age = rng.randint(max(AGES[m[1].kind][0], eldest + 20, m[0].age - 8), min(AGES[m[1].kind][1], m[0].age + 8))
        m[1].relation = "partner"


def _kin(rng: random.Random, hs: list[_H]) -> list[tuple[_P, _P, str]]:
    """A parent and adult child in different houses, and two siblings in different houses
    when there are two of an age; the younger takes the elder's surname (applied in `_names`)."""
    where = {id(p): i for i, h in enumerate(hs) for p in h.members}
    people = [p for h in hs for p in h.members]
    pairs = [(a, b) for a in people for b in people if where[id(a)] != where[id(b)]
             and b.relation != "child" and a.relation != "child" and 20 <= a.age - b.age <= 42]
    if not pairs:
        raise _Redraw
    parent, child = rng.choice(pairs)
    out = [(parent, child, "parent")]
    used = {where[id(parent)], where[id(child)]}
    sibs = [(a, b) for a in people for b in people if where[id(a)] not in used and where[id(b)] not in used
            and where[id(a)] != where[id(b)] and 0 <= a.age - b.age <= 12 and a is not b
            and "child" not in (a.relation, b.relation)]
    if sibs:
        a, b = rng.choice(sibs)
        out.append((a, b, "sibling"))
    return out


def _names(rng: random.Random, hs: list[_H], kin: list[tuple[_P, _P, str]]) -> None:
    surnames = rng.sample(names.SURNAMES, len(hs) + PEOPLE)
    spare = surnames[len(hs):]
    for h, s in zip(hs, surnames):
        h.surname = s
        for p in h.members:
            p.surname = s
        for p in h.members:
            if (p.relation == "partner" and rng.random() < 0.15) or p.relation == "housemate":
                p.surname = spare.pop()
    where = {id(p): h for h in hs for p in h.members}
    for elder, younger, _ in kin:
        # only a household's head brings a relative's surname into a house, and the household's
        # members who had the head's name take it with them; anyone else keeps theirs, as married
        # relatives do
        h = where[id(younger)]
        if younger.relation in ("head", "") and where[id(elder)] is not h:
            old = h.surname
            h.surname = elder.surname
            for p in h.members:
                if p.surname == old:
                    p.surname = elder.surname
    for p, first in zip([p for h in hs for p in h.members], rng.sample(names.FIRST_NAMES, PEOPLE)):
        p.first = first
    streets = rng.sample(names.STREETS, rng.randint(3, 5))
    numbers = {s: rng.sample(range(1, 40), len(hs)) for s in streets}
    for i, h in enumerate(hs):
        street = streets[i % len(streets)]
        h.address = f"{numbers[street][i]} {street}"


# --- writing rows -----------------------------------------------------------------------


def _org(store: Store, name: str, kind: str, outside: bool = False, owner: str = "", contact: str = "",
         credit: bool = False) -> str:
    oid = store.new_id("org", SETUP)
    store.open_account(oid, SETUP, allow_credit=credit)
    store.add("orgs", Org(oid, name, kind, owner=owner, outside=outside, contact=contact), SETUP)
    return oid


def _weekly(store: Store, kind: str, payer: str, payee: str, cents: int, first: int, memo: str, ends: str,
            last: int | None = None) -> str:
    return O.create(store, kind=kind, payer=payer, payee=payee, cents=cents, first=first, every=7, last=last,
                    memo=memo, ends=ends, made_by="routine", cause=SETUP)


def _employ(store: Store, person: str, org: str, hours_a_week: float, weekly: int, since: int,
            cause: str = SETUP) -> str:
    """Give `person` a job at `org`: a `Job` row and its weekly wage, paid by the employer's
    account into the person's household account on Fridays from the first Friday on, ended
    only by both. Returns the job's id."""
    employer = store.get("orgs", org)
    first = store.day + (FRIDAY - store.day) % 7
    ob = O.create(store, kind="wage", payer=org, payee=store.get("people", person).household, cents=weekly,
                  first=first, every=7, memo=f"wages from {employer.name}", ends="both", made_by="routine",
                  cause=cause)
    jid = store.new_id("jb", cause)
    store.add("jobs", Job(jid, person, org, weekly, float(hours_a_week), ob, since), cause)
    return jid


def _tie(store: Store, a: str, b: str, kind: str, back: str | None) -> None:
    store.add("ties", Tie(store.new_id("ti", SETUP), a, b, kind), SETUP)
    if back is not None:
        store.add("ties", Tie(store.new_id("ti", SETUP), b, a, back), SETUP)


def _econ_households(store: Store) -> list[EconHousehold]:
    return [EconHousehold(b.account if b.account != firms.OUTSIDE_BUYERS else b.id, b.size, {"food": b.wtp},
                          {"food": b.budget}, 0) for b in store.all("buyers")]


def expected_units(store: Store) -> dict[str, float]:
    """Each shop's expected weekly units at its present price, quality, visibility and
    reputation: `econ.demand.true_choice_probabilities` over every buyer in an average week,
    up to what its hours can serve. Wants a full shop turns away go to the others in
    proportion to what they win (as the rule's logit does when a choice is taken away), up
    to what they can serve. Keyed by firm id."""
    rows = [f for f in store.all("firms") if f.need == firms.NEED]
    offers = [Offering(f.org, f.need, f.price, f.quality, f.visibility) for f in rows]
    won = true_choice_probabilities(_econ_households(store), offers, {f.org: f.reputation for f in rows},
                                    NEEDS[firms.NEED], DemandRule())
    cap = {f.id: float(firms.capacity(store, f.id)) for f in rows}
    share = {f.id: won[f.org] for f in rows}
    units = {f.id: min(share[f.id], cap[f.id]) for f in rows}
    spill = sum(share[f] - units[f] for f in units)
    while spill > 1e-9:
        open_ = [f for f in units if units[f] < cap[f] and share[f] > 0]
        weight = sum(share[f] for f in open_)
        if not open_ or weight <= 0:
            break
        spilt, spill = spill, 0.0
        for f in open_:
            more = spilt * share[f] / weight
            room = cap[f] - units[f]
            units[f] += min(more, room)
            spill += max(0.0, more - room)
    return units


def _mean_wants() -> float:
    """Expected food wants a week of a background buyer of econ's sizes."""
    spec = NEEDS["food"]
    sizes, weights = (1, 2, 3, 4, 5), (28, 34, 16, 14, 8)
    return sum(w * spec.wants_per_person * n ** spec.size_exponent for n, w in zip(sizes, weights)) / sum(weights)


def background_buyers(capacity: int) -> int:
    """How many households round about shop in the hamlet: enough for the wants of all of
    them that fall in town (`IN_TOWN` of them, the share within willingness to pay at the
    usual price) to fill the three shops' weekly capacity."""
    return round(capacity / (_mean_wants() * IN_TOWN))


def _shop_costs(store: Store, f: FirmRow, units: float) -> tuple[int, int, int]:
    """Weekly wages, cost of `units` from its supplier, and fixed costs of a shop."""
    wages = sum(j.weekly for j in store.where("jobs", org=f.org))
    unit = store.get("suppliers", f.last_supplier).unit_cents if store.has("suppliers", f.last_supplier) else 0
    fixed = sum(o.cents for o in store.where("obligations", payer=f.org)
                if o.every == 7 and o.kind in ("rent", "bill") and o.status == "active")
    return wages, round(units * unit), fixed


def _build(seed: int, families: list[str], rng: random.Random, attempt: int = 0) -> Store:
    s = Store()
    s.seed = seed
    s.place = rng.choice(names.PLACES)
    near = rng.sample([p for p in names.PLACES if p != s.place], 7)
    s.open_account(OPENING, SETUP, allow_credit=True)
    s.open_account(firms.OUTSIDE_BUYERS, SETUP, allow_credit=True)

    # people and houses
    hs = _households(rng)
    for h in hs:
        _ages(rng, h)
    kin = _kin(rng, hs)
    _names(rng, hs, kin)

    # organisations beyond the hamlet
    emp_name, emp_lines = rng.choice(EMPLOYERS)
    employer = _org(s, emp_name.format(p=near[0]), "employer", outside=True, credit=True)
    region = _org(s, f"{near[1]} Regional Office", "region", outside=True, credit=True)
    investor = _org(s, f"{near[2]} Ventures", "investor", outside=True, credit=True)
    utility = _org(s, f"{rng.choice(names.ENERGY_WORDS)} Energy", "utility", outside=True)
    agency = _org(s, f"{near[3]} Lettings", "agency", outside=True)
    market = _org(s, f"Shops and services in {near[4]}", "market", outside=True)
    sup_orgs = [_org(s, f"{near[5]} Wholesale", "supplier", outside=True),
                _org(s, f"{near[6]} Cash and Carry", "supplier", outside=True)]

    # households and people
    people: list[_P] = []
    taken = set()
    for h in hs:
        h.id = s.new_id("h", SETUP)
        name = f"{h.surname} household"
        if name in taken:
            name = f"{h.surname} household at {h.address}"
        taken.add(name)
        s.open_account(h.id, SETUP)
        for p in h.members:
            p.id = s.new_id("p", SETUP)
            people.append(p)
        s.add("households", Household(h.id, name, [p.id for p in h.members], street=h.address), SETUP)
        for p in h.members:
            s.add("people", Person(p.id, f"{p.first} {p.surname}", h.id, hours.WEEK_HOURS, hours.WEEK_HOURS, 0,
                                   kind=p.kind, age=p.age), SETUP)
    home_of = {p.id: h for h in hs for p in h.members}
    by_kind = {k: [p for p in people if p.kind == k] for k in AGES}

    # the bank, the shops and the software firm
    banker = by_kind["banker"][0]
    bank = _org(s, f"{s.place} and District Bank", "bank", contact=banker.id)
    owners = list(by_kind["owner"])
    rng.shuffle(owners)
    shop_words = rng.sample(names.SHOP_WORDS, 3)
    shop_names, stems = [], []
    for o, word in zip(owners, shop_words):
        choices = [f"{o.surname}'s", home_of[o.id].address.split(" ", 1)[1], s.place]
        rng.shuffle(choices)
        stem = next((c for c in choices if c not in stems), None)
        if stem is None:
            raise _Redraw
        stems.append(stem)
        shop_names.append(f"{stem} {word}")
    shops = [_org(s, name, "shop", owner=o.id) for o, name in zip(owners, shop_names)]
    soft = list(by_kind["software"])
    rng.shuffle(soft)
    soft_owner, soft_worker = soft
    if home_of[soft_owner.id] is home_of[soft_worker.id]:
        raise _Redraw
    firm = _org(s, f"{rng.choice(names.FIRM_WORDS)} Software", "firm", owner=soft_owner.id)
    econ_sups = {sp.id: sp for sp in default_suppliers()}
    sup_rows = []
    for org, key in zip(sup_orgs, ("reliable", "cheap")):
        sp = econ_sups[key]
        sid = s.new_id("sp", SETUP)
        s.add("suppliers", SupplierRow(sid, org, sp.price, sp.p_full, sp.base_price, sp.partial_low, sp.partial_high),
              SETUP)
        sup_rows.append(sid)

    # jobs: who works at which shop (largest first), never for a shop their own household owns
    staff = list(by_kind["staff"])
    rng.shuffle(staff)
    staff.sort(key=lambda p: all(home_of[o.id] is not home_of[p.id] for o in owners))   # the constrained first
    crews: list[list[_P]] = [[] for _ in shops]
    for p in staff:
        open_ = [i for i in range(3) if len(crews[i]) < SHOP_STAFF[i] and home_of[owners[i].id] is not home_of[p.id]]
        if not open_:
            raise _Redraw
        crews[min(open_, key=lambda i: (len(crews[i]) >= SHOP_STAFF[i], i))].append(p)
    if [len(c) for c in crews] != list(SHOP_STAFF):
        raise _Redraw

    def job(p: _P, org: str, band: str, hours_band: tuple[int, int] | None = None) -> None:
        h = rng.randint(*(hours_band or JOB_HOURS[band]))
        rate = rng.randrange(HOURLY[band][0], HOURLY[band][1] + 1, 5)
        weeks = rng.randint(4, max(4, min(600, (p.age - 16) * 52)))    # nobody started before 16
        _employ(s, p.id, org, h, int(round(h * rate)), -7 * weeks)

    for i, shop in enumerate(shops):
        job(owners[i], shop, "owner")
        for p in crews[i]:
            job(p, shop, "staff")
    job(banker, bank, "banker")
    job(soft_owner, firm, "software_owner")
    job(soft_worker, firm, "software")
    for p in by_kind["outside_worker"]:
        job(p, employer, "outside_worker")

    # pensions and benefit; a landlord under pension age lives on the rents, and some work part time
    landlord = by_kind["landlord"][0]
    pension_of: dict[str, str] = {}
    for p in people:
        if p.kind == "retired" or (p.kind == "landlord" and p.age >= PENSION_AGE):
            pension_of[p.id] = _weekly(s, "wage", region, home_of[p.id].id, rng.randrange(*PENSION, 100), FRIDAY,
                                       "state pension", "service")
        elif p.kind == "landlord" and rng.random() < LANDLORD_JOB_CHANCE:
            job(p, employer, "outside_worker", LANDLORD_JOB_HOURS)
        elif p.kind == "unemployed":
            _weekly(s, "wage", region, home_of[p.id].id, rng.randrange(*BENEFIT, 100), FRIDAY, "jobseeker's benefit",
                    "service")

    # the shops' rows, rent and running costs, a loan, and the owner's recurring work
    spec = NEEDS["food"]
    fids = []
    for i, shop in enumerate(shops):
        size = "large" if i == 0 else "small"
        fid = s.new_id("fm", SETUP)
        s.add("firms", FirmRow(fid, shop, firms.NEED, price=int(round(spec.ref_price * _u(rng, PRICE) / 5)) * 5,
                               quality=round(_u(rng, QUALITY), 6), visibility=round(_u(rng, VISIBILITY[size]), 6),
                               reputation=round(_u(rng, REPUTATION), 6),
                               units_per_staff=rng.randint(*UNITS_PER_38_HOURS), shelf_weeks=2,
                               last_supplier=rng.choice(sup_rows), delivered_week=1), SETUP)
        fids.append(fid)
        _weekly(s, "rent", shop, agency, rng.randrange(*SHOP_FIXED[size], 100), MONDAY,
                "shop rent and running costs", "both")
        for what in ("ordering", "the books", "rota and pay"):
            hours.give(s, owners[i].id, shop, what, SETUP)
    borrower = rng.randrange(3)
    _weekly(s, "loan", shops[borrower], bank, rng.randrange(*SHOP_LOAN, 100), MONDAY,
            "loan for the shop fittings", "both", last=7 * (rng.randint(30, 200) - 1))

    # how each house is held
    lhome = home_of[landlord.id]
    bhome = home_of[banker.id]
    others = [h for h in hs if h is not lhome]
    tenants = rng.sample(others, LETS)
    for h in hs:
        if h is lhome:
            h.tenure = "outright" if h is bhome else "mortgage"
        elif h in tenants:
            h.tenure = "landlord"
        elif h is bhome:
            h.tenure = "agency"
        else:
            h.tenure = rng.choice(("agency", "mortgage"))
        if h.tenure == "mortgage" and h.members[0].age >= MORTGAGE_END_AGE:
            h.tenure = "outright"          # paid off by 75
        h.shares = dict(housing=_u(rng, HOUSING), food=_u(rng, FOOD), energy=_u(rng, ENERGY), margin=_u(rng, MARGIN))
    price = sum(s.get("firms", f).price for f in fids) / len(fids)
    for h in sorted(hs, key=lambda h: h is lhome):        # the landlord's rents come in first
        income = weekly_in(s, h.id)
        rents = sum(o.cents for o in s.where("obligations", payee=h.id) if o.kind == "rent")
        if h.tenure == "mortgage" and income - rents <= 0:
            h.tenure = "outright"          # nothing but the rents coming in: the house is the landlord's own
        housing = _r100(h.shares["housing"] * (income - rents)) if h.tenure != "outright" else 0
        if h.tenure == "landlord":
            _weekly(s, "rent", h.id, lhome.id, housing, MONDAY, f"rent for {h.address}", "both")
            s.set("households", h.id, SETUP, landlord=landlord.id)
        elif h.tenure == "agency":
            _weekly(s, "rent", h.id, agency, housing, MONDAY, f"rent for {h.address}", "both")
            s.set("households", h.id, SETUP, landlord=agency)
        elif h.tenure == "mortgage":
            left = min(1300, (MORTGAGE_END_AGE - h.members[0].age) * 52)
            _weekly(s, "loan", h.id, bank, housing, MONDAY, f"mortgage on {h.address}", "both",
                    last=7 * (rng.randint(min(150, left), left) - 1))
        energy = _r100(h.shares["energy"] * income)
        _weekly(s, "bill", h.id, utility, energy, WEDNESDAY, "energy", "service")
        food = expected_food(len(h.members), price)
        # what regular outgoings leave of income is the drawn margin: the rest of it goes on regular spending
        rest = income * (1 - h.shares["margin"]) - housing - energy - food
        _weekly(s, "bill", h.id, market, _r100(rest), SATURDAY, "regular spending", "payer")
        if h is not bhome and rng.random() < PERSONAL_LOAN_CHANCE:
            _weekly(s, "loan", h.id, bank, _r100(_u(rng, PERSONAL_LOAN_SHARE) * income), MONDAY,
                    rng.choice(LOAN_PURPOSES), "both", last=7 * (rng.randint(20, 150) - 1))
    for h in hs:
        hours.give(s, rng.choice(h.members).id, h.id, "household paperwork", SETUP)

    # ties: households, kin, work, landlord and tenant, then friends
    for h in hs:
        head = h.members[0]
        for p in h.members[1:]:
            if p.relation == "partner":
                _tie(s, head.id, p.id, "partner", "partner")
            elif p.relation == "sibling":
                _tie(s, head.id, p.id, "sibling", "sibling")
            elif p.relation == "child":
                for parent in h.members:
                    if parent.relation in ("head", "partner"):
                        _tie(s, parent.id, p.id, "child", "parent")
        kids = [p for p in h.members if p.relation == "child"]
        for a in kids:
            for b in kids:
                if id_key(a.id) < id_key(b.id):
                    _tie(s, a.id, b.id, "sibling", "sibling")
    for a, b, _ in kin:
        _tie(s, a.id, b.id, "kin", "kin")
    for i, shop in enumerate(shops):
        for p in crews[i]:
            _tie(s, p.id, owners[i].id, "employer", "employee")
        for a in crews[i]:
            for b in crews[i]:
                if id_key(a.id) < id_key(b.id):
                    _tie(s, a.id, b.id, "coworker", "coworker")
    _tie(s, soft_worker.id, soft_owner.id, "employer", "employee")
    outs = by_kind["outside_worker"]
    for a in outs:
        for b in outs:
            if id_key(a.id) < id_key(b.id):
                _tie(s, a.id, b.id, "coworker", "coworker")
    for h in tenants:
        for p in h.members:
            _tie(s, p.id, landlord.id, "landlord", "tenant")
    friends: dict[str, list[str]] = {p.id: [] for p in people}
    kin_of: dict[str, list[str]] = {p.id: [] for p in people}
    for a, b, _ in kin:
        kin_of[a.id].append(b.id)
        kin_of[b.id].append(a.id)
    order = list(people)
    rng.shuffle(order)
    for p in order:
        want = rng.randint(1, 3)
        pool = [q for q in order if home_of[q.id] is not home_of[p.id] and q.id not in friends[p.id]
                and len(friends[q.id]) < 3 and q.id not in kin_of[p.id]]
        rng.shuffle(pool)
        for q in pool:
            if len(friends[p.id]) >= want:
                break
            friends[p.id].append(q.id)
            friends[q.id].append(p.id)
            _tie(s, p.id, q.id, "friend", "friend")
    if any(not f for f in friends.values()):
        raise _Redraw

    # each borrower from the bank sees the banker as their banker
    borrowers: list[str] = []
    for o in s.where("obligations", payee=bank):
        if o.kind == "loan":
            who = s.get("households", o.payer).members if s.has("households", o.payer) else [s.get("orgs", o.payer).owner]
            borrowers += [m for m in who if m not in borrowers]
    for pid in borrowers:
        _tie(s, pid, banker.id, "banker", None)

    # one or two private debts between residents who know each other
    pairs = []
    for t in s.all("ties"):
        if t.kind in ("kin", "friend") and id_key(t.person) < id_key(t.other):
            pairs.append((t.person, t.other))
    rng.shuffle(pairs)
    n_debts, made = rng.randint(1, 2), 0
    in_debt: set[str] = set()                 # households already lending or owing
    for a, b in pairs:
        if made >= n_debts:
            break
        creditor, debtor = (a, b) if rng.random() < 0.5 else (b, a)
        ch, dh = s.get("people", creditor).household, s.get("people", debtor).household
        if dh in in_debt or ch in in_debt:
            continue
        weeks = rng.randint(4, 20)
        cents = rng.randrange(1000, 4001, 500)
        first = rng.choice((MONDAY, FRIDAY, SATURDAY))
        _weekly(s, "agreement", dh, ch, cents, first, f"paying back {s.get('people', creditor).name}", "both",
                last=first + 7 * (weeks - 1))
        _tie(s, debtor, creditor, "creditor", "debtor")
        in_debt |= {dh, ch}
        made += 1

    # food: a buyer for each household, and the background buyers round about
    spec = NEEDS["food"]
    days = rng.sample(range(7), 7) + rng.sample(range(7), 7)
    for h, day in zip(hs, days):
        wtp = round(spec.wtp_mean * math.exp(rng.gauss(0, spec.wtp_sd) - spec.wtp_sd ** 2 / 2), 6)
        budget = _r100(h.shares["food"] * weekly_in(s, h.id))
        s.add("buyers", Buyer(s.new_id("by", SETUP), h.id, len(h.members), wtp, budget, day), SETUP)
        s.set("households", h.id, SETUP, shop_day=day)
    for n in range(background_buyers(sum(firms.capacity(s, f) for f in fids))):
        size = rng.choices([1, 2, 3, 4, 5], weights=[28, 34, 16, 14, 8])[0]
        wtp = round(spec.wtp_mean * math.exp(rng.gauss(0, spec.wtp_sd) - spec.wtp_sd ** 2 / 2), 6)
        budget = round(spec.wants_per_person * size ** spec.size_exponent * spec.ref_price * spec.budget_factor)
        s.add("buyers", Buyer(s.new_id("by", SETUP), firms.OUTSIDE_BUYERS, size, wtp, budget, n % 7), SETUP)

    units = expected_units(s)
    for fid, shop in zip(fids, shops):
        f = s.get("firms", fid)
        u = max(1, int(round(units[fid])))
        s.set("firms", fid, SETUP, last_units=u, stock_units=[u], stock_weeks=[1])
        wages, goods, fixed = _shop_costs(s, f, u)
        s.transfer(OPENING, shop, _r100((wages + goods + fixed) * _u(rng, (2.0, 4.0))), "opening balance", SETUP)

    # the software firm's funding, the bank's money, households' savings
    soft_wages = sum(j.weekly for j in s.where("jobs", org=firm))
    _weekly(s, "agreement", investor, firm, _r100(soft_wages * _u(rng, INVESTOR_OVER_WAGES)), MONDAY,
            f"investment from {s.get('orgs', investor).name}", "both")
    s.transfer(OPENING, firm, _r100(soft_wages * _u(rng, (2.0, 6.0))), "opening balance", SETUP)
    s.transfer(OPENING, bank, rng.randrange(8_000_000, 20_000_001, 10_000), "opening balance", SETUP)
    for h in hs:
        s.transfer(OPENING, h.id, _r100(weekly_out(s, h.id) * _u(rng, OPENING_WEEKS)), "opening balance", SETUP)

    _describe_people(s, rng, hs, people, home_of, dict(shops=shops, fids=fids, owners=owners, crews=crews,
                     bank=bank, firm=firm, employer=employer, emp_lines=emp_lines, investor=investor,
                     landlord=landlord, pension_of=pension_of))
    _deal(s, random.Random(f"gen:{seed}:{attempt}:families"), families)
    _deck(s, rng, hs, sup_rows, utility)
    for p in s.all("people"):
        free = hours.free_hours(s, p.id)
        s.set("people", p.id, SETUP, hours_week=free, hours_left=free)
    return s


# --- what people are like -----------------------------------------------------------------


def _describe_people(s: Store, rng: random.Random, hs: list[_H], people: list[_P], home_of: dict,
                     w: dict) -> None:
    shops, owners, crews = w["shops"], w["owners"], w["crews"]
    shop_name = {o: s.get("orgs", o).name for o in shops}
    bank_name, firm_name, emp_name = (s.get("orgs", w[k]).name for k in ("bank", "firm", "employer"))
    smallest = min(w["fids"], key=lambda f: (s.get("firms", f).last_units, id_key(f)))
    smallest_org = s.get("firms", smallest).org
    stock_days = [i % 7 for i in range(PEOPLE)]
    rng.shuffle(stock_days)
    emp_lines = rng.sample(w["emp_lines"], 3)
    outs = [p for p in people if p.kind == "outside_worker"]
    pensions = sorted(o.cents for o in s.all("obligations") if o.memo == "state pension")
    for p, day in zip(people, stock_days):
        h = home_of[p.id]
        jobs_ = s.where("jobs", person=p.id)
        # what they do
        if p.kind == "owner":
            line = f"runs {shop_name[shops[owners.index(p)]]}"
        elif p.kind == "staff":
            line = rng.choice(STAFF_LINES).format(s=shop_name[jobs_[0].org])
        elif p.kind == "banker":
            line = f"works at {bank_name}"
        elif p.kind == "software":
            line = (f"runs {firm_name}, a small software firm that writes software for businesses"
                    if s.get("orgs", w["firm"]).owner == p.id else f"writes software at {firm_name}")
        elif p.kind == "outside_worker":
            line = emp_lines[outs.index(p)].format(e=emp_name)
        elif p.kind == "landlord":
            line = "lets three houses" + (" and is retired" if p.age >= PENSION_AGE else
                                          f" and works part time at {emp_name}" if jobs_ else "")
        elif p.kind == "retired":
            line = "retired"
        elif p.kind == "unemployed":
            line = "out of work"
        else:
            line = rng.choice(HOME_LINES)
        # what they worry about, from their rows
        worries = []
        inn, out = weekly_in(s, h.id), weekly_out(s, h.id)
        if inn < out:
            worries.append(f"More goes out each week ({money(out)}) than comes in ({money(inn)}).")
        for t in s.all("ties"):
            if t.person == p.id and t.kind in ("creditor", "debtor"):
                other = s.get("people", t.other)
                payer, payee = (h.id, other.household) if t.kind == "creditor" else (other.household, h.id)
                o = next(o for o in s.where("obligations", payer=payer) if o.payee == payee and o.kind == "agreement")
                worries.append(f"Paying back {other.name.split()[0]}: {money(o.cents)} a week until {when(o.last)}."
                               if t.kind == "creditor" else
                               f"Whether {other.name.split()[0]} keeps up the {money(o.cents)} a week owed to you.")
        for o in s.where("obligations", payer=h.id):
            if o.kind == "loan" and not o.memo.startswith("mortgage") and p.relation in ("head", "partner", ""):
                worries.append(f"The {o.memo} costs {money(o.cents)} a week until {when(o.last)}.")
        if p.kind == "owner":
            shop = shops[owners.index(p)]
            if shop == smallest_org:
                worries.append(f"Whether enough custom comes into {shop_name[shop]}, the smallest of the three shops.")
            for o in s.where("obligations", payer=shop):
                if o.kind == "loan":
                    worries.append(f"The {o.memo} costs {shop_name[shop]} {money(o.cents)} a week.")
                elif o.kind == "rent":
                    worries.append(f"Whether {shop_name[shop]} takes enough to pay its rent and running costs "
                                   f"of {money(o.cents)} a week.")
        if p.kind == "staff" and jobs_[0].hours < 30:
            worries.append(f"You only get {jobs_[0].hours:g} hours a week at {shop_name[jobs_[0].org]}.")
        if p.kind == "unemployed":
            amount = next(o.cents for o in s.where("obligations", payee=h.id) if o.memo == "jobseeker's benefit")
            worries.append(f"Finding work; your benefit is {money(amount)} a week.")
        if p.id in w["pension_of"]:
            mine = s.get("obligations", w["pension_of"][p.id]).cents
            if mine <= pensions[len(pensions) // 2]:
                worries.append(f"Getting by on your pension of {money(mine)} a week.")
        if s.get("households", h.id).landlord and h.tenure == "landlord":
            rent = next(o for o in s.where("obligations", payer=h.id) if o.kind == "rent")
            worries.append(f"Your rent of {money(rent.cents)} a week to {s.get('people', w['landlord'].id).name}.")
        if h.tenure == "mortgage" and p.relation in ("head", "partner", ""):
            m = next(o for o in s.where("obligations", payer=h.id) if o.memo.startswith("mortgage"))
            worries.append(f"Your mortgage of {money(m.cents)} a week.")
        if p.kind == "landlord":
            worries.append("Keeping the three houses you let in good repair.")
        if p.kind == "banker":
            n = len([o for o in s.where("obligations", payee=w["bank"]) if o.kind == "loan"])
            worries.append(f"Whether the {n} loans on the books at {bank_name} are kept up.")
        if p.kind == "software":
            inv = next(o for o in s.where("obligations", payer=w["investor"]) if o.payee == w["firm"])
            if s.get("orgs", w["firm"]).owner == p.id:
                worries.append(f"Whether {s.get('orgs', w['investor']).name} keeps paying {firm_name} "
                               f"{money(inv.cents)} a week.")
            else:
                worries.append(f"Whether {firm_name} keeps its funding.")
        if p.kind == "outside_worker":
            worries.append(f"Keeping your job at {emp_name}.")
        if p.kind == "home":
            earners = [q for q in h.members if s.where("jobs", person=q.id)]
            if earners:
                worries.append(f"Getting by on what {earners[0].first} brings in.")
        if p.kind == "staff" and not any(x.startswith("You only") for x in worries):
            worries.append(f"Keeping your hours at {shop_name[jobs_[0].org]}.")
        if not worries:
            worries.append("Keeping up with the bills.")
        n = rng.randint(1, 3)
        axes = rng.sample(sorted(BELIEFS), rng.randint(2, 4))
        beliefs = [rng.choice(BELIEFS[a][rng.randrange(2)]) for a in axes]
        s.set("people", p.id, SETUP, job=line, stock_day=day, voice=rng.choice(VOICES), beliefs=beliefs,
              interests=rng.sample(INTERESTS, rng.randint(1, 3)), worries=worries[:n])


def _deal(s: Store, rng: random.Random, families: list[str]) -> None:
    """Deal model families at random within each kind of person, round the families in turn,
    so each kind of four or more has at least two and every family plays its share."""
    offset = 0
    groups: dict[str, list[str]] = {}
    for p in s.all("people"):
        groups.setdefault(p.kind, []).append(p.id)
    order = list(families)
    rng.shuffle(order)
    for kind in sorted(groups):
        ids = list(groups[kind])
        rng.shuffle(ids)
        for i, pid in enumerate(ids):
            s.set("people", pid, SETUP, family=order[(i + offset) % len(order)])
        offset += len(ids)


def _deck(s: Store, rng: random.Random, hs: list[_H], sup_rows: list[str], utility: str) -> None:
    """Four to seven ordinary events over the first twelve weeks."""
    n = rng.randint(4, 7)
    kinds = ["illness", "job_lost", "repair", "supplier_short", "cold_month"]
    rng.shuffle(kinds)
    kinds = kinds[:min(n, 5)] + [rng.choice(("illness", "repair")) for _ in range(n - 5)]
    ill = rng.sample([p.id for p in s.all("people")], n)
    events = []
    for k, who_ill in zip(kinds, ill):
        day = rng.randint(7, 83)
        if k == "illness":
            events.append((day, k, who_ill, 0, 1))
        elif k == "job_lost":
            events.append((day, k, rng.choice([p.id for p in s.all("people") if p.kind == "outside_worker"]), 0, 0))
        elif k == "repair":
            events.append((day, k, rng.choice(hs).id, rng.randrange(15_000, 90_001, 500), 0))
        elif k == "supplier_short":
            events.append((day, k, rng.choice(sup_rows), 0, 1))
        else:
            events.append((day, k, utility, 30, 4))
    for day, k, who, amount, weeks in sorted(events):
        s.add("events", Event(s.new_id("ev", SETUP), day, k, who, amount, weeks), SETUP)


# --- generate and check -------------------------------------------------------------------


def generate_counted(seed: int, families: list[str]) -> tuple[Store, int]:
    """The place for `seed` and the number of attempts it took."""
    if len(families) < 2 or len(set(families)) != len(families):
        raise ValueError(f"families must be two or more distinct names, got {families!r}")
    for attempt in range(MAX_ATTEMPTS):
        rng = random.Random(f"gen:{seed}:{attempt}")
        try:
            s = _build(seed, list(families), rng, attempt)
        except _Redraw:
            continue
        if not check(s):
            return s, attempt + 1
    raise RuntimeError(f"no sound place for seed {seed} in {MAX_ATTEMPTS} attempts")


def generate(seed: int, families: list[str]) -> Store:
    """The place for `seed`, at day 0, played by `families`."""
    return generate_counted(seed, families)[0]


def check(s: Store) -> list[str]:
    """What is structurally unsound about a place; empty when it is sound."""
    bad: list[str] = []
    people = s.all("people")
    households = s.all("households")
    orgs = s.all("orgs")
    # everyone in exactly one household
    count: dict[str, int] = {}
    for h in households:
        for m in h.members:
            count[m] = count.get(m, 0) + 1
    for p in people:
        if count.get(p.id, 0) != 1 or p.id not in s.get("households", p.household).members:
            bad.append(f"{p.name} is not in exactly one household")
        if not 19 <= p.age <= 82:
            bad.append(f"{p.name} is {p.age}")
    if len(people) != PEOPLE or not 7 <= len(households) <= 9:
        bad.append(f"{len(people)} people in {len(households)} households")
    for t in s.all("ties"):
        if t.kind == "partner" and abs(s.get("people", t.person).age - s.get("people", t.other).age) > 8:
            bad.append(f"partners {t.person} and {t.other} are more than eight years apart")
    # jobs
    for j in s.all("jobs"):
        ob = s.get("obligations", j.wage_ob) if s.has("obligations", j.wage_ob) else None
        if j.hours <= 0 or j.weekly < MIN_WAGE * j.hours:
            bad.append(f"{j.id} pays {money(j.weekly)} for {j.hours:g} hours, under {money(MIN_WAGE)} an hour")
        if ob is None or ob.cents != j.weekly or ob.payer != j.org or ob.kind != "wage":
            bad.append(f"{j.id}'s wage obligation does not match it")
        org = s.get("orgs", j.org)
        if org.owner and org.owner != j.person and \
                s.get("people", org.owner).household == s.get("people", j.person).household:
            bad.append(f"{j.person} works for {org.name}, which their household owns")
    # the landlord, the shops, the bank
    landlords = [p for p in people if p.kind == "landlord"]
    if len(landlords) != 1 or len([h for h in households if h.landlord == landlords[0].id]) < LETS:
        bad.append("there is not one landlord letting three houses")
    shops = [o for o in orgs if o.kind == "shop"]
    staff = {o.id: len([j for j in s.where("jobs", org=o.id) if j.person != o.owner]) for o in shops}
    if not staff or max(staff.values()) < 0.4 * sum(staff.values()):
        bad.append("the largest shop employs under 40% of the shops' staff")
    banks = [o for o in orgs if o.kind == "bank"]
    loans = [o for b in banks for o in s.where("obligations", payee=b.id) if o.kind == "loan" and o.status == "active"]
    if len(banks) != 1 or len(loans) < 3 or not any(s.has("orgs", o.payer) for o in loans):
        bad.append("the bank does not hold three loans, one of them to a firm")
    needs = Counter(f.need for f in s.all("firms"))
    if not needs or max(needs.values()) < 2:
        bad.append("no two shops serve the same need")
    kinds = {t.kind for t in s.all("ties") if s.has("people", t.person) and s.has("people", t.other)}
    if not {"tenant", "landlord"} <= kinds or not {"creditor", "debtor"} <= kinds:
        bad.append("there is no tenant and landlord pair, or no creditor and debtor pair, among residents")
    for p in people:
        if p.kind == "banker":
            mine = [p.household] + [o.id for o in orgs if o.owner == p.id]
            if any(o.payer in mine for b in banks for o in s.where("obligations", payee=b.id)):
                bad.append(f"the banker {p.name} owes the bank")
    # names
    full = Counter(p.name.casefold() for p in people)
    if max(full.values(), default=0) > 1:
        bad.append("two people share a full name")
    org_names = {o.name.casefold() for o in orgs}
    for p in people:
        if any(n.casefold() in org_names for n in (p.name, p.name.split()[0], p.name.split()[-1])):
            bad.append(f"{p.name} is named like an organisation")
        firsts = Counter(s.get("people", q).name.split()[0].casefold() for q in acts.knows(s, p.id))
        twice = sorted(n for n, c in firsts.items() if c > 1)
        if twice:
            bad.append(f"{p.name} knows two people with the first name {twice[0]}")
    # money
    units = expected_units(s) if s.all("firms") else {}
    for f in s.all("firms"):
        u = units[f.id]
        wages, goods, fixed = _shop_costs(s, f, u)
        cost = wages + goods + fixed
        takings = u * f.price
        if cost <= 0 or not 0.75 <= takings / cost <= 1.25:
            bad.append(f"{f.id} would take {money(round(takings))} a week against costs of {money(cost)}")
    below = 0
    for h in households:
        inn, out = weekly_in(s, h.id), weekly_out(s, h.id)
        if out <= 0 or not 0.85 <= inn / out <= 1.30:
            bad.append(f"{h.name} has {money(inn)} a week in and {money(out)} out")
        elif inn < out:
            below += 1
    if below < 2:
        bad.append(f"only {below} households have less coming in than going out")
    # families
    fams = Counter(p.family for p in people)
    if "" in fams or (fams and min(fams.values()) < 2):
        bad.append("a family plays fewer than two people")
    by_kind: dict[str, list] = {}
    for p in people:
        by_kind.setdefault(p.kind, []).append(p.family)
    for kind, fs in sorted(by_kind.items()):
        if len(fs) >= 4 and len(set(fs)) < 2:
            bad.append(f"every {kind} is played by one family")
    if s.ledger.total() != 0:
        bad.append(f"the ledger totals {s.ledger.total()}")
    return bad


# --- a page to read -----------------------------------------------------------------------


def _who(s: Store, account: str) -> str:
    return acts.name_of_who(s, account)


def describe(s: Store) -> str:
    """A few pages of a local directory and ledger: the place, its households, its
    organisations, who knows whom, and what is due to happen."""
    L: list[str] = []
    people, households = s.all("people"), s.all("households")
    inside = [o for o in s.all("orgs") if not o.outside]
    L += [s.place, "=" * len(s.place), "",
          f"{len(people)} people in {len(households)} households. Three food shops, "
          f"{_names_of(o for o in inside if o.kind == 'bank')}, and {_names_of(o for o in inside if o.kind == 'firm')}, "
          "a small software firm that writes software for businesses. "
          f"The ledger starts on {date_text(0)}.", ""]
    L += ["Households", "----------", ""]
    for h in households:
        L.append(f"{h.name.split(' household')[0]} household, {h.street}")
        rel = {t.other: t.kind for t in s.all("ties") if t.person == h.members[0] and t.kind in _HOUSEHOLD_TIES}
        for i, m in enumerate(h.members):
            p = s.get("people", m)
            head = s.get('people', h.members[0]).name.split()[0]
            how = "" if i == 0 else f", {rel[m]} of {head}" if m in rel else f", shares the house with {head}"
            L.append(f"  {p.name}, {p.age}{how}: {p.job}.")
        if h.landlord and s.has("people", h.landlord):
            L.append(f"  Rents from {s.get('people', h.landlord).name}.")
        elif h.landlord:
            L.append(f"  Rents from {_who(s, h.landlord)}.")
        elif any(o.memo.startswith("mortgage") for o in s.where("obligations", payer=h.id)):
            L.append("  Owns the house with a mortgage.")
        else:
            L.append("  Owns the house outright.")
        ins = [o for o in s.where("obligations", payee=h.id) if o.every == 7]
        outs = [o for o in s.where("obligations", payer=h.id) if o.every == 7]
        came = [(f"from {_who(s, o.payer)}, {o.memo}" if o.kind == "agreement" else o.memo)
                + f" {money(o.cents)} ({_day(o.first)})" for o in ins]
        L.append("  In each week: " + "; ".join(came) + ".")
        food = sum(b.budget for b in s.all("buyers") if b.account == h.id)
        day = next((b.shop_day for b in s.all("buyers") if b.account == h.id), 0)
        went = [f"{o.memo} to {_who(s, o.payee)} {money(o.cents)} ({_day(o.first)})" for o in outs]
        L.append("  Out each week: " + "; ".join(went) + f"; food, about {money(food_spend(s, h.id))} "
                 f"(at most {money(food)}), bought on {_dayname(day)}s.")
        L.append(f"  In all {money(weekly_in(s, h.id))} in and {money(weekly_out(s, h.id))} out a week. "
                 f"Holds {money(s.balance(h.id))}.")
        owes = [o for o in outs if o.last is not None]
        for o in owes:
            n = (o.last - o.first) // 7 + 1
            L.append(f"  Owes {_who(s, o.payee)}: {o.memo}, {money(o.cents)} a week, {n} payments left, "
                     f"the last on {when(o.last)} ({money(n * o.cents)} in all).")
        for w in [w for w in s.all("work") if w.account == h.id]:
            L.append(f"  {s.get('people', w.holder).name} does the {w.what}.")
        L.append("")
    L += ["Organisations", "-------------", ""]
    for o in inside:
        L.append(f"{o.name} ({_KIND_WORD[o.kind]})")
        if o.owner:
            L.append(f"  Owned by {s.get('people', o.owner).name}.")
        if o.contact:
            L.append(f"  Letters to {s.get('people', o.contact).name}.")
        for j in s.where("jobs", org=o.id):
            L.append(f"  {s.get('people', j.person).name}: {j.hours:g} hours a week for {money(j.weekly)}, "
                     f"paid on Fridays, for {_since(j.since)}.")
        for f in s.where("firms", org=o.id):
            sup = s.get("suppliers", f.last_supplier)
            L.append(f"  Sells food at {money(f.price)} a unit; {sum(f.stock_units)} units in stock; "
                     f"a standing order of {f.last_units} units a week from {_who(s, sup.org)} at "
                     f"{money(sup.unit_cents)} a unit, delivered on Mondays; can serve {firms.capacity(s, f.id)} "
                     f"units a week.")
        for w in s.all("work"):
            if w.account == o.id:
                L.append(f"  {s.get('people', w.holder).name} does the {w.what.removeprefix('the ')} "
                         f"({w.hours:g} hours a week).")
        for ob in s.where("obligations", payer=o.id):
            if ob.kind != "wage":
                last = f", the last on {when(ob.last)}" if ob.last is not None else ""
                L.append(f"  Pays {_who(s, ob.payee)} {money(ob.cents)} a week for {ob.memo}{last}.")
        for ob in s.where("obligations", payee=o.id):
            last = f", the last on {when(ob.last)}" if ob.last is not None else ""
            L.append(f"  Is paid {money(ob.cents)} a week by {_who(s, ob.payer)}: {ob.memo}{last}.")
        L.append(f"  Holds {money(s.balance(o.id))}.")
        L.append("")
    outside = [o for o in s.all("orgs") if o.outside]
    L.append("Beyond the hamlet: " + "; ".join(f"{o.name} ({_KIND_WORD[o.kind]})" for o in outside) + ".")
    L.append(f"About {len([b for b in s.all('buyers') if b.account == firms.OUTSIDE_BUYERS])} households "
             "round about also shop in the hamlet's shops.")
    L.append("")
    L += ["People", "------", ""]
    for p in people:
        known: dict[str, list[str]] = {}
        for t in s.all("ties"):
            if t.person == p.id:
                known.setdefault(_TIE_WORD.get(t.kind, t.kind), []).append(s.get("people", t.other).name)
        L.append(f"{p.name}, {p.age}. {p.voice} Thinks: {' '.join(p.beliefs)} Likes {_and(p.interests)}.")
        L.append(f"  Worries: {' '.join(p.worries)}")
        L.append("  Knows: " + "; ".join(f"{k} {_and(v)}" for k, v in known.items()) + ".")
        L.append("")
    L += ["What is due to happen", "---------------------", ""]
    for e in s.all("events"):
        L.append(f"{date_text(e.day)}: {_event_text(s, e)}")
    return "\n".join(L) + "\n"


_HOUSEHOLD_TIES = ("partner", "parent", "child", "sibling")
_KIND_WORD = {"shop": "food shop", "bank": "bank", "firm": "software firm", "employer": "employer",
              "supplier": "food wholesaler", "agency": "letting agent", "utility": "energy supplier",
              "region": "pensions and benefits", "investor": "investor", "market": "shops and services"}
_TIE_WORD = {"partner": "partner", "parent": "parent", "child": "child", "sibling": "brother or sister",
             "kin": "family in another house", "friend": "friends with", "coworker": "works with",
             "employer": "works for", "employee": "employs", "landlord": "rents from", "tenant": "lets to",
             "creditor": "owes money to", "debtor": "is owed money by", "banker": "banks with"}


def _names_of(orgs) -> str:
    return _and([o.name for o in orgs])


def _and(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _dayname(d: int) -> str:
    return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[d % 7]


def _day(first: int) -> str:
    return _dayname(first) + "s"


def _since(day: int) -> str:
    weeks = -day // 7
    return f"{weeks // 52} years" if weeks >= 104 else f"{weeks} weeks" if weeks < 52 else "a year or so"


def _event_text(s: Store, e: Event) -> str:
    if e.kind == "illness":
        return f"{_who(s, e.who)} falls ill for a week."
    if e.kind == "job_lost":
        job = s.where("jobs", person=e.who)
        where = _who(s, job[0].org) if job else "work"
        return f"{_who(s, e.who)} loses the job at {where}."
    if e.kind == "repair":
        return f"The {_who(s, e.who)} needs a repair costing {money(e.amount)}."
    if e.kind == "supplier_short":
        return f"{_who(s, s.get('suppliers', e.who).org)} is short of stock for a week."
    return f"A cold spell: {_who(s, e.who)} bills go up by {e.amount}% for {e.weeks} weeks."
