"""The generator: 12 people in about 6 households, composed from parts with a seed,
then checked for coherence. Everything here is state; text is rendered from it later.

Money is integer cents throughout.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

HAIKU = "openrouter/anthropic/claude-haiku-5.5"
DEEPSEEK = "openrouter/deepseek/deepseek-v4.1-flash-1017"
FAMILIES = {"haiku": HAIKU, "deepseek": DEEPSEEK}

TOWN = "Wenlow"
STREETS = ["Mill Street", "Church Lane", "Quarry Road", "Station Road", "Back Lane", "Orchard Row", "High Street"]

FIRST_F = ["Maureen", "Ruth", "Jo", "Carol", "Priya", "Denise", "Sian", "Lorna", "Abby", "Tess", "Nadia", "Gwen", "Hazel", "Megan"]
FIRST_M = ["Dave", "Ian", "Gary", "Tomasz", "Keith", "Owen", "Rob", "Malcolm", "Sunil", "Lewis", "Pete", "Craig", "Alan", "Ross"]
SURNAMES = ["Kerr", "Pryce", "Holt", "Nowak", "Bevan", "Lister", "Daly", "Marsh", "Shah", "Fenwick", "Royle", "Tully", "Ashby", "Corrie"]

FIRM_NAMES = ["{s}'s Grocers", "Wenlow Bakery & Deli", "The Corner Stores", "{s} Family Butchers", "Quarry Road Food Hall"]
OUT_EMPLOYERS = [
    ("Calder Logistics depot", "warehouse operative", 61000),
    ("Brennock General Hospital", "porter", 58000),
    ("Halverd Foods plant", "line supervisor", 66000),
    ("Mersey Cable Works", "machine setter", 64000),
]
INTERESTS = ["the allotment", "football", "the chapel choir", "fishing", "darts at the Plough", "baking",
             "local history", "the brass band", "walking the fells", "quizzes", "the bowls club", "knitting",
             "cars", "the parish council", "crosswords"]
VOICES = [
    "plain and short; you don't waste words",
    "chatty; you run on and ask after people",
    "careful and polite; you choose words like you're writing a letter",
    "dry; you make jokes to get past awkward things",
    "blunt; you say what you think and expect the same",
    "warm; you use first names and say thank you a lot",
    "worried-sounding; you hedge and apologise",
    "brisk and businesslike; figures first",
]
GRUDGE_REASONS = [
    "told people at the Plough that your family doesn't pay its way",
    "got the job you applied for two years ago and you think it was a favour",
    "complained to the council about your hedge and the van parked outside",
    "never paid back a loan of $300 from years ago, then pretended not to remember",
    "left a bad review of a family business in the parish newsletter",
    "fell out with you over a boundary fence",
]
SKILLS = {
    "owner": [["buying stock", "keeping books"], ["baking", "serving customers"], ["butchery", "ordering"], ["running a till", "stock control"]],
    "employee": [["serving customers"], ["shelf stacking", "driving"], ["baking"], ["cleaning", "deliveries"]],
    "outside": [["forklift driving"], ["machine work"], ["lifting and carrying"], ["supervising"]],
    "unemployed": [["painting and decorating"], ["care work"], ["cooking"], ["admin and typing"], ["gardening"]],
    "banker": [["lending", "accounts"]],
}


@dataclass
class Person:
    id: str
    name: str
    sex: str
    age: int
    household: str
    role: str  # owner | employee | outside | unemployed | banker
    model: str = ""
    firm: str | None = None  # firm owned or worked at
    employer: str | None = None  # outside employer name
    job_title: str = ""
    pay: int = 0  # weekly pay in cents (wage, outside pay, benefit, or banker's salary)
    skills: list[str] = field(default_factory=list)
    ties: dict[str, list[str]] = field(default_factory=dict)  # other id -> kinds
    grudge: dict[str, str] = field(default_factory=dict)  # other id -> reason
    beliefs: dict[str, str] = field(default_factory=dict)
    interests: list[str] = field(default_factory=list)
    worries: list[str] = field(default_factory=list)
    voice: str = ""
    health: str = "well"


@dataclass
class HouseholdSpec:
    id: str
    members: list[str]
    children: int
    street: str
    tenure: str  # own | mortgage | rent
    landlord: str | None  # person id, or "outside:lettings"
    rent: int  # weekly rent or mortgage payment, cents
    running_cost: int  # weekly bills (power, water, council tax, phone), cents
    savings: int
    owns_let: str | None = None  # household id this one lets to
    house_value: int = 0


@dataclass
class DebtSpec:
    id: str
    debtor: str  # person id
    creditor: str  # "bank" or person id or "outside:card"
    principal: int
    weekly: int
    weeks_left: int
    what: str


@dataclass
class FirmSpec:
    id: str
    name: str
    owner: str
    employees: list[str]
    pay: dict[str, int]  # person -> weekly cents (owner's own draw included)
    quality: float
    cash: int
    street: str


@dataclass
class Generated:
    seed: int
    people: dict[str, Person]
    households: dict[str, HouseholdSpec]
    firms: dict[str, FirmSpec]
    debts: dict[str, DebtSpec]
    bank_cash: int
    problems: list[str]


def _name(rng, sex, used):
    pool = FIRST_F if sex == "f" else FIRST_M
    for _ in range(100):
        n = rng.choice(pool)
        if n not in used:
            used.add(n)
            return n
    raise RuntimeError("names exhausted")


def generate(seed: int) -> Generated:
    rng = random.Random(f"people:{seed}")
    used_first: set[str] = set()
    surnames = rng.sample(SURNAMES, 8)
    # --- households: sizes of adults that sum to 12 over 6 households ---------------
    sizes = rng.choice([[1, 2, 2, 2, 2, 3], [2, 2, 2, 2, 2, 2], [1, 1, 2, 2, 3, 3], [1, 2, 2, 2, 2, 3]])
    rng.shuffle(sizes)
    roles = ["owner"] * 3 + ["banker"] + ["employee"] * 4 + ["outside"] * 2 + ["unemployed"] * 2
    # deal roles so no household is entirely out of work and the banker lives with no owner
    for _ in range(500):
        rng.shuffle(roles)
        groups, i = [], 0
        for s in sizes:
            groups.append(roles[i:i + s])
            i += s
        ok = all(any(r != "unemployed" for r in g) for g in groups)
        ok = ok and not any("banker" in g and "owner" in g for g in groups)
        ok = ok and sum(1 for g in groups if "owner" in g) == 3
        if ok:
            break
    people: dict[str, Person] = {}
    households: dict[str, HouseholdSpec] = {}
    pid = 0
    for h, g in enumerate(groups):
        hid = f"h{h + 1}"
        surname = surnames[h]
        members = []
        for k, role in enumerate(g):
            pid += 1
            sex = "f" if (k == 0) == (rng.random() < 0.5) else "m"
            if len(g) == 3 and k == 2:  # an adult son or daughter
                age = rng.randint(19, 26)
            elif len(g) == 3:
                age = rng.randint(46, 62)
            elif k == 1:  # a partner close in age to the first adult
                age = max(22, min(66, people[f"p{pid - 1:02d}"].age + rng.randint(-6, 6)))
            else:
                age = rng.randint(24, 64)
            p = Person(f"p{pid:02d}", f"{_name(rng, sex, used_first)} {surname}", sex, age, hid, role)
            people[p.id] = p
            members.append(p.id)
        children = rng.choice([0, 0, 1, 2]) if len(g) == 2 else (rng.choice([0, 1]) if len(g) == 1 else 0)
        households[hid] = HouseholdSpec(hid, members, children, rng.choice(STREETS), "rent", None, 0, 0, 0)
    for hid, hh in households.items():  # partners and parents are kin
        for a in hh.members:
            for b in hh.members:
                if a != b:
                    kind = "partner" if len(hh.members) == 2 or (hh.members.index(a) < 2 and hh.members.index(b) < 2) else (
                        "child" if hh.members.index(b) == 2 else "parent")
                    people[a].ties.setdefault(b, []).append(kind)
    # --- work ----------------------------------------------------------------------
    owners = [p for p in people.values() if p.role == "owner"]
    employees = [p for p in people.values() if p.role == "employee"]
    firm_names = rng.sample(FIRM_NAMES, 3)
    firms: dict[str, FirmSpec] = {}
    qualities = [0.6, 0.57, 0.63]
    for j, o in enumerate(owners):
        fid = f"firm:{j + 1}"
        name = firm_names[j].format(s=o.name.split()[1])
        firms[fid] = FirmSpec(fid, name, o.id, [], {o.id: rng.choice([32000, 35000, 38000])}, qualities[j],
                              rng.randint(600, 900) * 1000, rng.choice(STREETS))
        o.firm, o.job_title = fid, f"owner of {name}"
        o.pay = firms[fid].pay[o.id]
        o.skills = rng.choice(SKILLS["owner"])
    # four employees over three firms; no one works for a firm owned in their own household unless unavoidable
    order = list(firms)
    for k, e in enumerate(employees):
        cands = [f for f in order if people[firms[f].owner].household != e.household] or order
        cands.sort(key=lambda f: len(firms[f].employees))
        fid = cands[0]
        firms[fid].employees.append(e.id)
        wage = rng.choice([42000, 44000, 46000, 48000])
        firms[fid].pay[e.id] = wage
        e.firm, e.pay, e.job_title = fid, wage, f"works at {firms[fid].name}"
        e.skills = rng.choice(SKILLS["employee"])
        e.ties.setdefault(firms[fid].owner, []).append("employer")
        people[firms[fid].owner].ties.setdefault(e.id, []).append("employee")
    outs = rng.sample(OUT_EMPLOYERS, 2)
    for p, (emp, title, pay) in zip([p for p in people.values() if p.role == "outside"], outs):
        p.employer, p.job_title, p.pay = emp, f"{title} at {emp}, out of town", pay
        p.skills = rng.choice(SKILLS["outside"])
    for p in people.values():
        if p.role == "unemployed":
            p.pay, p.job_title = 19000, "out of work"  # jobseeker's benefit
            p.skills = rng.choice(SKILLS["unemployed"])
        if p.role == "banker":
            p.pay, p.job_title = 72000, "manager of the Wenlow branch of the Northern Mutual Bank"
            p.skills = SKILLS["banker"][0]
    # coworkers know each other
    for f in firms.values():
        staff = [f.owner] + f.employees
        for a in staff:
            for b in staff:
                if a != b and "employer" not in people[a].ties.get(b, []) and "employee" not in people[a].ties.get(b, []):
                    people[a].ties.setdefault(b, []).append("coworker")
    # --- homes, money and debts --------------------------------------------------------
    debts: dict[str, DebtSpec] = {}
    hids = list(households)
    rng.shuffle(hids)
    banker_h = next(h for h in hids if any(people[m].role == "banker" for m in households[h].members))
    # one owner household (or the banker's) owns a second house and lets it to another household
    landlord_h = rng.choice([h for h in hids if any(people[m].role in ("owner", "banker") for m in households[h].members)])
    tenure = {}
    tenure[landlord_h] = "mortgage"
    rest = [h for h in hids if h != landlord_h]
    tenant_h = rng.choice([h for h in rest if not any(people[m].role == "banker" for m in households[h].members)])
    tenure[tenant_h] = "rent_person"
    rest.remove(tenant_h)
    kinds = ["own", "mortgage", "rent_outside", "rent_outside"]
    rng.shuffle(kinds)
    for h, k in zip(rest, kinds):
        tenure[h] = k
    dn = 0
    for hid, hh in households.items():
        adults = len(hh.members)
        size = adults + hh.children
        income = sum(people[m].pay for m in hh.members)
        # bills and running costs (power, water, council tax, phone, transport, clothes): most of what is left
        hh.running_cost = round(max(11000 + 3600 * size, income * rng.uniform(0.36, 0.5)) / 100) * 100
        hh.savings = round(income * rng.choice([0.3, 0.8, 1.5, 3.0]) / 1000) * 1000
        t = tenure[hid]
        primary = hh.members[0]
        if t == "own":
            hh.tenure, hh.rent = "own", 0
            hh.savings += 300000
        elif t == "mortgage":
            hh.tenure, hh.landlord = "mortgage", "bank"
            hh.rent = rng.choice([17000, 19000, 21000, 24000])
            dn += 1
            debts[f"d{dn}"] = DebtSpec(f"d{dn}", primary, "bank", hh.rent * 52 * 15, hh.rent, 52 * 15, "mortgage on the house")
        elif t == "rent_outside":
            hh.tenure, hh.landlord = "rent", "outside:lettings"
            hh.rent = rng.choice([16500, 18000, 19500, 21000])
        else:
            hh.tenure = "rent"
            landlord = next(m for m in households[landlord_h].members if people[m].role in ("owner", "banker"))
            hh.landlord = landlord
            hh.rent = rng.choice([17500, 19000, 20500])
            households[landlord_h].owns_let = hid
            for m in hh.members:
                people[m].ties.setdefault(landlord, []).append("landlord")
                people[landlord].ties.setdefault(m, []).append("tenant")
        hh.house_value = 0
    # bank lends: a car or business loan to two people other than the banker
    banker = next(p for p in people.values() if p.role == "banker")
    owner_b = rng.choice(owners)
    others_b = rng.sample([p for p in people.values() if p.role not in ("banker",) and p.id != owner_b.id and p.age > 21], 2)
    borrowers = [others_b[0], others_b[1], owner_b]
    whats = [("loan for the car", 400000, 52 * 2), ("loan to consolidate card debts", 600000, 52 * 3),
             ("business loan for the shop's chiller", 900000, 52 * 3)]
    for p, (what, principal, weeks) in zip(borrowers, whats):
        dn += 1
        weekly = round(principal * 1.12 / weeks / 100) * 100
        debts[f"d{dn}"] = DebtSpec(f"d{dn}", p.id, "bank", principal, weekly, weeks, what)
        p.ties.setdefault(banker.id, []).append("your bank manager")
    # a private debt between two people in different households
    a, b = rng.sample([p for p in people.values()], 2)
    while a.household == b.household:
        a, b = rng.sample([p for p in people.values()], 2)
    dn += 1
    amt = rng.choice([80000, 120000, 150000])
    debts[f"d{dn}"] = DebtSpec(f"d{dn}", a.id, b.id, amt, 0, 0, "money borrowed privately, to be paid back when possible")
    a.ties.setdefault(b.id, []).append("you owe them money")
    b.ties.setdefault(a.id, []).append("they owe you money")
    # --- friends, kin across households, a rivalry and a grudge -------------------------
    plist = list(people.values())
    sib_a, sib_b = rng.sample(plist, 2)
    while sib_a.household == sib_b.household:
        sib_a, sib_b = rng.sample(plist, 2)
    sib_a.ties.setdefault(sib_b.id, []).append("sibling")
    sib_b.ties.setdefault(sib_a.id, []).append("sibling")
    for p in plist:
        others = [q for q in plist if q.household != p.household and q.id != p.id]
        for q in rng.sample(others, 2):
            if "friend" not in p.ties.get(q.id, []) and rng.random() < 0.6:
                p.ties.setdefault(q.id, []).append("friend")
                q.ties.setdefault(p.id, []).append("friend")
    o1, o2 = rng.sample(owners, 2)
    o1.ties.setdefault(o2.id, []).append("rival trader")
    o2.ties.setdefault(o1.id, []).append("rival trader")
    for _ in range(2):
        g1, g2 = rng.sample(plist, 2)
        while g1.household == g2.household or "friend" in g1.ties.get(g2.id, []):
            g1, g2 = rng.sample(plist, 2)
        reasons = [r for r in GRUDGE_REASONS
                   if not ("family business" in r and not any(people[m].role == "owner" for m in households[g1.household].members))
                   and not ("got the job" in r and g2.role in ("unemployed",))]
        g1.grudge[g2.id] = rng.choice(reasons)
        g1.ties.setdefault(g2.id, []).append("you hold a grudge")
    # --- beliefs, interests, worries, voice ---------------------------------------------
    for p in plist:
        p.beliefs = {
            "institutions": rng.choice(["You trust banks and the council to be fair, mostly.",
                                        "You think banks and the council look after their own.",
                                        "You have no strong view of banks or the council."]),
            "change": rng.choice(["You like things the way they have always been done.",
                                  "You like trying new ways of doing things.",
                                  "You go along with change once others have tried it."]),
            "risk": rng.choice(["You are careful with money and hate owing anything.",
                                "You will take a chance if the odds look fair.",
                                "You have taken big chances before and would again."]),
        }
        p.interests = rng.sample(INTERESTS, 2)
        p.voice = rng.choice(VOICES)
    # models: half and half, alternating within each role so both families get every kind of life
    by_role: dict[str, list[Person]] = {}
    for p in plist:
        by_role.setdefault(p.role, []).append(p)
    flip = rng.random() < 0.5
    k = 0
    for role in ["owner", "employee", "outside", "unemployed", "banker"]:
        for p in by_role.get(role, []):
            p.model = "haiku" if (k % 2 == 0) != flip else "deepseek"
            k += 1
    gen = Generated(seed, people, households, firms, debts, 25_000_000, [])
    derive_worries(gen)
    gen.problems = check(gen)
    return gen


def derive_worries(g: Generated) -> None:
    for p in g.people.values():
        hh = g.households[p.household]
        w = []
        my_debts = [d for d in g.debts.values() if d.debtor == p.id]
        income = sum(g.people[m].pay for m in hh.members)
        outgo = hh.rent + hh.running_cost + sum(d.weekly for d in g.debts.values() if g.people[d.debtor].household == hh.id)
        if outgo + 11000 * (len(hh.members) + hh.children) * 0.6 > income * 0.9:
            w.append("money is tight from week to week")
        if any(d.creditor == "bank" and d.what != "mortgage on the house" for d in my_debts):
            w.append("the bank loan")
        if any(d.creditor not in ("bank",) and d.creditor.startswith("p") for d in my_debts):
            w.append(f"the money you owe {g.people[my_debts[-1].creditor].name.split()[0]}")
        if p.role == "unemployed":
            w.append("finding work")
        if p.role == "owner":
            w.append("whether the shop is paying its way")
        if p.role == "employee":
            w.append("keeping your hours")
        if hh.tenure == "rent":
            w.append("the rent going up")
        if hh.children:
            w.append("the children's costs")
        if p.role == "banker":
            w.append("loans going bad on your books")
        if p.age > 55:
            w.append("your health")
        p.worries = w[:3] if w else ["nothing much, for now"]


def check(g: Generated) -> list[str]:
    """Coherence checks. Returns a list of problems (empty if fine)."""
    probs = []
    people, hhs = g.people, g.households
    if len(people) != 12:
        probs.append("not 12 people")
    roles = [p.role for p in people.values()]
    if roles.count("owner") != 3 or roles.count("banker") != 1:
        probs.append("need 3 owners and 1 banker")
    for f in g.firms.values():
        if not f.employees:
            probs.append(f"{f.name} has no staff")
    for p in people.values():
        if p.household not in hhs or p.id not in hhs[p.household].members:
            probs.append(f"{p.id} household mismatch")
        if p.role in ("owner", "employee") and not p.firm:
            probs.append(f"{p.id} has no firm")
        if p.role != "unemployed" and p.pay <= 0:
            probs.append(f"{p.id} has no pay")
    for h in hhs.values():
        if h.tenure == "rent" and not h.landlord:
            probs.append(f"{h.id} rents from no one")
        if h.landlord and h.landlord.startswith("p") and people[h.landlord].household == h.id:
            probs.append(f"{h.id} rents from itself")
        income = sum(people[m].pay for m in h.members)
        if income <= 0:
            probs.append(f"{h.id} has no income")
    landlords = {h.landlord for h in hhs.values() if h.landlord and h.landlord.startswith("p")}
    if not landlords:
        probs.append("no landlord/tenant link")
    if not any(d.creditor == "bank" and d.what != "mortgage on the house" for d in g.debts.values()):
        probs.append("no bank debtor")
    if not any(d.creditor.startswith("p") for d in g.debts.values()):
        probs.append("no private debt")
    if not any(p.grudge for p in people.values()):
        probs.append("no grudge")
    fam = [p.model for p in people.values()]
    if fam.count("haiku") != 6:
        probs.append("families not half and half")
    return probs


if __name__ == "__main__":
    import sys

    g = generate(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
    for p in g.people.values():
        print(p.id, p.name, p.age, p.role, p.job_title, p.household, p.model, p.pay, p.ties, p.grudge, p.worries)
    for h in g.households.values():
        print(h)
    for f in g.firms.values():
        print(f)
    for d in g.debts.values():
        print(d)
    print("problems:", g.problems)


# ======================================================================================
# Town 2: company people, household budgets from ordinary spending shares, a deck fixed
# at generation, and model families dealt at random within each kind of person.
# ======================================================================================
FAMILY_IDS = {
    "haiku": "openrouter/anthropic/claude-haiku-5.5",
    "deepseek": "openrouter/deepseek/deepseek-v4.1-flash-1017",
    "hy4": "openrouter/tencent/hy4-flash",
    "minimax": "openrouter/minimax/minimax-m3.1-flash",
}
DAYS7 = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def budget_lines(g: Generated, hid: str) -> dict[str, int]:
    """Weekly spending by category (cents), from ordinary shares for size and income."""
    h = g.households[hid]
    size = len(h.members) + h.children
    adults = len(h.members)
    income = sum(g.people[m].pay for m in h.members)
    commuters = sum(1 for m in h.members if g.people[m].role == "outside")
    r = lambda x: int(round(x / 100) * 100)  # noqa: E731
    return {
        "energy (Northgrid Energy, Wednesdays)": r(max(3000 + 900 * size, 0.045 * income)),
        "water and council tax": r(0.05 * income + 400 * size),
        "transport": r(3000 * commuters + 0.06 * income),
        "phone and internet": 1500,
        "clothing": r(max(700 * size, 0.04 * income)),
        "household goods and repairs": r(0.05 * income),
        "recreation, eating out and other": r(max(900 * size, 0.19 * income)),
    }


def generate2(seed: int) -> Generated:
    g = generate(seed)
    rng = random.Random(f"town2:{seed}")
    used = {p.name.split()[0] for p in g.people.values()}
    sur_used = {p.name.split()[1] for p in g.people.values()}
    # --- Calder Systems: a founder and someone who does sales and support -------------
    company = []
    for k, (title, pay) in enumerate([("founder of Calder Systems, which makes and sells Calder, an AI system", 52000),
                                      ("sales and support at Calder Systems", 42000)]):
        sex = rng.choice("fm")
        first = _name(rng, sex, used)
        surname = rng.choice([s for s in SURNAMES if s not in sur_used])
        sur_used.add(surname)
        pid = f"p{13 + k}"
        hid = f"h{7 + k}"
        p = Person(pid, f"{first} {surname}", sex, rng.randint(27, 45), hid, "company", job_title=title, pay=pay)
        p.skills = [["software", "selling"], ["selling", "customer support"]][k]
        p.employer = "Calder Systems"
        g.people[pid] = p
        g.households[hid] = HouseholdSpec(hid, [pid], 0, "Station Road", "rent", "outside:lettings",
                                          rng.choice([17000, 18500]), 0, rng.choice([150000, 300000]))
        company.append(p)
    a, b = company
    a.ties[b.id] = ["works with you"]
    b.ties[a.id] = ["works with you"]
    for p in company:
        for q in rng.sample([x for x in g.people.values() if x.role != "company"], 2):
            p.ties.setdefault(q.id, []).append("friend")
            q.ties.setdefault(p.id, []).append("friend")
        p.beliefs = {"institutions": rng.choice(["You trust banks and the council to be fair, mostly.", "You have no strong view of banks or the council."]),
                     "change": "You like trying new ways of doing things.",
                     "risk": rng.choice(["You will take a chance if the odds look fair.", "You have taken big chances before and would again."])}
        p.interests = rng.sample(INTERESTS, 2)
        p.voice = rng.choice(VOICES)
        p.worries = ["whether Calder Systems will find customers before the money runs out"]
    # --- budgets ------------------------------------------------------------------------
    for hid, h in g.households.items():
        lines = budget_lines(g, hid)
        h.running_cost = sum(lines.values())
        h.budget = lines  # type: ignore[attr-defined]
    derive_worries(g)
    for p in company:
        p.worries = ["whether Calder Systems will find customers before the money runs out"]
    # --- the deck, fixed now: who and which day ------------------------------------------
    d = random.Random(f"deck2:{seed}")
    P = g.people
    day = lambda w0, w1, dow=None: (d.randint(w0, w1) - 1) * 7 + (dow if dow is not None else d.randint(0, 5))  # noqa: E731
    town = [p for p in P.values() if p.role != "company"]
    deck = [
        {"kind": "job_lost", "who": d.choice([p.id for p in town if p.role == "outside"]), "t": day(2, 4)},
        {"kind": "rent_rise", "hh": d.choice([h for h, s in g.households.items() if s.landlord == "outside:lettings" and h <= "h6"]), "t": day(2, 2)},
        {"kind": "supplier_fail", "firm": d.choice(sorted(g.firms)), "t": day(3, 5, 2)},
        {"kind": "illness", "who": d.choice([p.id for p in town if p.role in ("employee", "outside", "owner")]), "t": day(3, 8, 0)},
        {"kind": "energy_backbill", "hh": d.choice(sorted(h for h in g.households if h <= "h6")), "t": day(2, 4, 0), "amount": 64000},
        {"kind": "car_repair", "who": d.choice([p.id for p in town if p.role != "unemployed"]), "t": day(1, 6), "amount": 46000},
        {"kind": "chiller", "firm": d.choice(sorted(g.firms)), "t": day(2, 6), "amount": 65000},
        {"kind": "boiler", "hh": d.choice(sorted(h for h in g.households if h <= "h6")), "t": day(5, 9), "amount": 38000},
        {"kind": "school_trip", "hh": d.choice([h for h, s in g.households.items() if s.children] or ["h1"]), "t": day(2, 8), "amount": 7500},
        {"kind": "council_tax", "hh": d.choice(sorted(g.households)), "t": day(1, 3, 0), "amount": 900},
    ]
    g.deck = sorted(deck, key=lambda e: e["t"])  # type: ignore[attr-defined]
    # --- families, at random within each kind of person -----------------------------------
    def kind(p):
        if p.role == "owner":
            return "owner"
        if p.role in ("banker", "company"):
            return "office"
        if any(x.debtor == p.id for x in g.debts.values()) or p.role == "unemployed":
            return "in_debt_or_out_of_work"
        return "employee"
    fr = random.Random(f"families:{seed}")
    groups: dict[str, list[Person]] = {}
    for p in sorted(g.people.values(), key=lambda p: p.id):
        groups.setdefault(kind(p), []).append(p)
    fams = list(FAMILY_IDS)
    offset = 0
    for k in sorted(groups):
        ps = groups[k]
        fr.shuffle(ps)
        order = fams[:]
        fr.shuffle(order)
        for i, p in enumerate(ps):
            p.model = order[(i + offset) % len(order)]
            p.kind = k  # type: ignore[attr-defined]
        offset += len(ps)
    g.problems = check2(g)
    return g


def check2(g: Generated) -> list[str]:
    probs = [x for x in check(g) if x not in ("not 12 people", "families not half and half")]
    if len(g.people) != 14:
        probs.append("not 14 people")
    fam = [p.model for p in g.people.values()]
    for f in FAMILY_IDS:
        if fam.count(f) < 2:
            probs.append(f"family {f} plays fewer than two people")
    return probs


def describe(g: Generated) -> str:
    out = []
    for hid, h in g.households.items():
        income = sum(g.people[m].pay for m in h.members) + sum(h2.rent for h2 in g.households.values() if h2.landlord in h.members)
        debt_w = sum(d.weekly for d in g.debts.values() if d.debtor in h.members)
        food = 0
        out_w = h.rent + h.running_cost + debt_w
        out.append(f"{hid}: {', '.join(g.people[m].name + ' (' + g.people[m].role + ', ' + g.people[m].model + ')' for m in h.members)}; "
                   f"children {h.children}; {h.tenure} {h.rent // 100}; budget {h.running_cost // 100}; debts {debt_w // 100}/wk; "
                   f"income {income // 100}; left before food {(income - out_w) // 100}; savings {h.savings // 100}")
    return "\n".join(out)
