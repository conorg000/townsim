"""The world for the people trial: the step 0 economy (ledger, demand rule, firms,
suppliers) with generated households as buyers, generated people owning and staffing
the three food firms, a bank, rent, wages and bills, a seeded event deck, and code that
validates and applies every act. No model is called in this file.
"""

from __future__ import annotations

import math
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from townsim.econ.demand import NEEDS, DemandRule, Household, NeedSpec, shop  # noqa: E402
from townsim.econ.firm import Firm  # noqa: E402
from townsim.econ.ledger import Ledger, Overdraft  # noqa: E402
from townsim.econ.suppliers import default_suppliers  # noqa: E402

from gen import TOWN, Generated, generate  # noqa: E402

DAYS = ["Monday", "Wednesday", "Friday", "Saturday"]
N_BACKGROUND = 150  # households in the rest of the town, buyers run by code only
UNITS_PER_STAFF = 170
FIXED_COST = 25000
SUPPLIER_NAMES = {"cheap": "Brook Cash & Carry", "reliable": "Dales Wholesale"}
LETTINGS = "Dunmore Lettings"
BENEFIT = 19000
REQUEST_EXPIRY_ROUNDS = 8  # two weeks
LOAN_RATE = 0.12  # a year, flat

# needs that a new organisation can serve besides food (data only, as the econ package intends)
EXTRA_NEEDS = {
    "repairs": NeedSpec("repairs", 0.12, 0.7, 4000, 0.0, 0, 5200, 0.6, 1500, 1.1, 0.15, 0.15, 1.5),
    "care": NeedSpec("care", 0.06, 0.7, 3000, 0.0, 0, 4000, 0.6, 1200, 1.1, 0.15, 0.15, 1.5),
    "leisure": NeedSpec("leisure", 0.4, 0.85, 1200, 0.0, 0, 1500, 0.6, 500, 1.1, 0.2, 0.15, 1.5),
    "deliveries": NeedSpec("deliveries", 0.25, 0.85, 500, 0.0, 0, 800, 0.5, 300, 1.1, 0.2, 0.15, 1.5),
    "bookkeeping": NeedSpec("bookkeeping", 0.02, 0.5, 6000, 0.0, 0, 8000, 0.6, 2000, 1.1, 0.15, 0.1, 1.5),
}
ALL_NEEDS = {"food": NEEDS["food"], **EXTRA_NEEDS}
CREATE_ORG_COST = {"small": 10000, "medium": 100000, "large": 1000000}  # = narrator spike v2/v3 table
PROJECT_COST = {
    "fit_out": (20000, 100000, 1000000), "build": (20000, 100000, 1000000), "repair": (15000, 100000, 1000000),
    "campaign": (0, 0, 100000), "petition": (0, 0, 100000), "event": (0, 0, 100000), "survey": (0, 0, 100000),
}
PROJECT_WEEKS = {"small": 1, "medium": 2, "large": 4}
SCALE_VIS = {"small": 0.2, "medium": 0.35, "large": 0.5}
SCALE_UNITS = {"small": 40, "medium": 120, "large": 300}
SCALE_FIXED = {"small": 2000, "medium": 10000, "large": 40000}
INVEST_K = {"quality": 200000, "visibility": 100000, "reputation": 150000, "capacity": 200000, "skill": 50000}

OPS_ALL = ["pay", "message", "converse", "request", "answer", "offer_job", "accept_job", "quit", "dismiss",
           "set_price", "order_stock", "set_staff_pay", "set_rent", "borrow", "repay", "shop_at", "write_notes"]


MONTH_START = 2  # Monday 2 March is the Monday of the first week
DAY_OFFSET = [0, 2, 4, 5]


def date_of(t: int) -> str:
    """Absolute round -> 'Wednesday 4 March'."""
    day = MONTH_START + (t // 4) * 7 + DAY_OFFSET[t % 4]
    month = "March"
    if day > 31:
        day -= 31
        month = "April"
    return f"{DAYS[t % 4]} {day} {month}"


def monday_of(week: int) -> str:
    return date_of((week - 1) * 4)


def saturday_of(week: int) -> str:
    return date_of((week - 1) * 4 + 3)


def money(c: int | float) -> str:
    c = int(round(c))
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}${c // 100:,}" if c % 100 == 0 else f"{sign}${c / 100:,.2f}"


def to_cents(v) -> int | None:
    """Dollars as given by a person (number or "$1,200.50") to cents, or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v * 100))
    if isinstance(v, str):
        s = v.replace("$", "").replace(",", "").strip()
        try:
            return int(round(float(s) * 100))
        except ValueError:
            return None
    return None


@dataclass
class Debt:
    id: str
    debtor: str
    creditor: str  # "bank" or a person id
    balance: int
    weekly: int
    what: str
    arrears: int = 0
    missed_weeks: int = 0


@dataclass
class Request:
    id: str
    sender: str
    to: str
    kind: str  # ask | job | loan | narrator
    text: str
    summary: str
    made: int  # absolute round
    expires: int
    amount: int = 0  # ask: what the receiver would pay the sender; job: weekly pay; loan: principal
    weeks: int = 0  # loan length
    firm: str | None = None
    on_accept: list = field(default_factory=list)
    status: str = "open"  # open | accepted | declined | lapsed | failed
    answered: int | None = None


@dataclass
class Item:
    """Something that reached a person from another person."""
    id: str
    kind: str  # message | request | answer | conversation
    sender: str
    text: str
    t: int
    request: str | None = None
    shown: bool = False


@dataclass
class Obs:
    text: str
    t: int
    trigger: bool = True  # does it make the person act next round?


class World:
    def __init__(self, seed: int, use_aims: bool = True):
        self.seed = seed
        self.use_aims = use_aims
        self.g: Generated = generate(seed)
        assert not self.g.problems, self.g.problems
        self.people = self.g.people
        self.hh = self.g.households
        self.t = 0  # absolute round: week = t // 4 + 1
        self.ledger = Ledger()
        for a in ("outside:capital", "outside:suppliers", "outside:market", "outside:employers", "outside:landlords",
                  "outside:lettings", "outside:bills", "outside:gov", "outside:garage"):
            self.ledger.open(a, allow_credit=True)
        self.rule = DemandRule()
        rng = random.Random(f"world:{seed}")
        # played households
        self.econ_hh: dict[str, Household] = {}
        spec = NEEDS["food"]
        for hid, h in self.hh.items():
            size = len(h.members) + h.children
            wtp = spec.wtp_mean * math.exp(rng.gauss(0, spec.wtp_sd) - spec.wtp_sd ** 2 / 2)
            e = Household(hid, size, {"food": wtp}, {}, 0)
            e.budget["food"] = round(e.expected_wants(spec) * spec.ref_price * spec.budget_factor)
            self.econ_hh[hid] = e
            self.ledger.open(hid)
            self.ledger.transfer("outside:capital", hid, h.savings, "savings at start", "setup")
        # the rest of the town: background households, code only
        self.bg: list[Household] = []
        for i in range(N_BACKGROUND):
            size = rng.choices([1, 2, 3, 4, 5], weights=[28, 34, 16, 14, 8])[0]
            wtps = {n: s.wtp_mean * math.exp(rng.gauss(0, s.wtp_sd) - s.wtp_sd ** 2 / 2) for n, s in ALL_NEEDS.items()}
            e = Household(f"bg:{i:03d}", size, wtps, {}, 0)
            for n, s in ALL_NEEDS.items():
                e.budget[n] = round(e.expected_wants(s) * s.ref_price * s.budget_factor)
            e.income = round(sum(e.budget.values()) * rng.uniform(1.1, 1.4))
            self.bg.append(e)
            self.ledger.open(e.id)
            self.ledger.transfer("outside:capital", e.id, e.income, "savings at start", "setup")
        # firms
        self.firms: dict[str, Firm] = {}
        self.firm_info: dict[str, dict] = {}
        for fid, fs in self.g.firms.items():
            staff = 1 + len(fs.employees)
            f = Firm(fid, "food", 1000, fs.quality, 0.5, 0.75, staff, UNITS_PER_STAFF, 0, FIXED_COST)
            f.employees = list(fs.employees)
            f.receive(0, 60 if staff == 2 else 80, 690)
            self.firms[fid] = f
            self.ledger.open(fid)
            self.ledger.transfer("outside:capital", fid, fs.cash, "owner's capital", "setup")
            self.firm_info[fid] = {"name": fs.name, "owner": fs.owner, "pay": dict(fs.pay), "street": fs.street,
                                   "offering": "groceries and fresh food", "pending": [], "ordered_this_week": False,
                                   "last_supplier": "reliable", "history": [], "price_set_by": None,
                                   "fail_supplier_week": None, "ill": set(), "service": False}
        self.suppliers = {s.id: s for s in default_suppliers()}
        # seed last week's history so owners have figures from the start
        for fid, f in self.firms.items():
            base = 300 if f.staff == 2 else 440
            self.firm_info[fid]["history"].append({"week": 0, "price": 1000, "sold": base, "revenue": base * 1000,
                                                   "turned_away": 0, "spoiled": 25, "cash": self.ledger.balance(fid),
                                                   "wages": sum(self.firm_info[fid]["pay"].values()), "fixed": FIXED_COST})
            self.firm_info[fid]["pending"] = [("reliable", base, self.suppliers["reliable"].price)]
        # the bank
        self.ledger.open("bank")
        self.ledger.transfer("outside:capital", "bank", self.g.bank_cash, "bank's lending funds", "setup")
        self.debts: dict[str, Debt] = {d.id: Debt(d.id, d.debtor, d.creditor, d.principal, d.weekly, d.what)
                                       for d in self.g.debts.values()}
        self.banker = next(p.id for p in self.people.values() if p.role == "banker")
        # people's runtime state
        self.inbox: dict[str, list[Item]] = {p: [] for p in self.people}
        self.obs: dict[str, list[Obs]] = {p: [] for p in self.people}
        self.heard: dict[str, list[tuple[int, str]]] = {p: [] for p in self.people}
        self.aims: dict[str, list[dict]] = {p: [] for p in self.people}
        self.notes: dict[str, list[str]] = {p: [] for p in self.people}
        self.last_acted: dict[str, int] = {p: -1 for p in self.people}
        self.attempts_week: dict[str, int] = {p: 0 for p in self.people}
        self.requests: dict[str, Request] = {}
        self.facts: list[dict] = []
        self.projects: list[dict] = []
        self.new_orgs: dict[str, dict] = {}
        self.shop_choice: dict[str, str | None] = {h: None for h in self.hh}
        self.events = self._deck(rng)
        self.log: list[dict] = []
        self.unpaid_bills: dict[str, list[list]] = {h: [] for h in self.hh}  # [creditor, cents, what]
        self.one_off: dict[str, list[tuple[str, int, str]]] = {h: [] for h in self.hh}  # bills due this week
        self.firm_one_off: dict[str, list[tuple[str, int, str]]] = {f: [] for f in self.firms}
        self.stock_round = {pid: i % 4 for i, pid in enumerate(sorted(self.people, key=lambda x: rng.random()))}
        self.n_ids = 0
        self.lost_job: dict[str, int] = {}
        self.sick: dict[str, int] = {}
        self.rent_rise: dict[str, tuple[int, int]] = {}  # hid -> (new rent, from week)
        self.stats_weeks: list[dict] = []
        # pay start-of-trial habit: households shop where most of their members work, else at random
        for hid, h in self.hh.items():
            firms_here = [self.people[m].firm for m in h.members if self.people[m].firm]
            self.econ_hh[hid].last_bought["food"] = firms_here[0] if firms_here else rng.choice(list(self.firms))
        for e in self.bg:
            e.last_bought["food"] = rng.choice(list(self.firms))

    # ---------------------------------------------------------------- helpers ----------
    @property
    def week(self) -> int:
        return self.t // 4 + 1

    @property
    def rnd(self) -> int:
        return self.t % 4

    def when(self, t: int | None = None) -> str:
        t = self.t if t is None else t
        return date_of(t)

    def nid(self, prefix: str) -> str:
        self.n_ids += 1
        return f"{prefix}{self.n_ids}"

    def first(self, pid: str) -> str:
        return self.people[pid].name.split()[0]

    def name(self, pid: str) -> str:
        if pid in self.people:
            return self.people[pid].name
        if pid in self.firm_info:
            return self.firm_info[pid]["name"]
        return {"bank": "the Northern Mutual Bank", "outside:lettings": LETTINGS}.get(pid, pid)

    def cash(self, pid: str) -> int:
        return self.ledger.balance(self.people[pid].household)

    def tie_rank(self, me: str, other: str) -> int:
        """0 closest: household; 1 kin; 2 friend; 3 work, landlord, creditor; 4 anyone else."""
        if other not in self.people:
            return 5
        if self.people[other].household == self.people[me].household:
            return 0
        kinds = self.people[me].ties.get(other, [])
        if "sibling" in kinds:
            return 1
        if "friend" in kinds:
            return 2
        if kinds:
            return 3
        return 4

    def tie_words(self, me: str, other: str) -> str:
        kinds = [k for k in self.people[me].ties.get(other, [])]
        words = {"partner": "your partner", "child": "your son" if self.people[other].sex == "m" else "your daughter",
                 "parent": "your father" if self.people[other].sex == "m" else "your mother",
                 "sibling": "your brother" if self.people[other].sex == "m" else "your sister",
                 "employer": "your boss", "employee": "works for you", "coworker": "works with you",
                 "landlord": "your landlord", "tenant": "rents your house", "friend": "a friend",
                 "rival trader": "runs a rival shop", "your bank manager": "your bank manager",
                 "you owe them money": "you owe them money", "they owe you money": "owes you money",
                 "you hold a grudge": "you have a grudge against them"}
        return ", ".join(words.get(k, k) for k in kinds)

    def resolve(self, ref) -> str | None:
        """A person id from an id, full name, first name, surname (if unique) or firm name (its owner)."""
        if not isinstance(ref, str) or not ref.strip():
            return None
        r = ref.strip().lower().removeprefix("@")
        for pid, p in self.people.items():
            if r in (pid, p.name.lower()):
                return pid
        for fid, fi in {**self.firm_info}.items():
            if r in (fid, fi["name"].lower()) or (len(r) > 5 and r in fi["name"].lower()):
                return fi["owner"]
        if "bank" in r:
            return self.banker
        firsts = [pid for pid, p in self.people.items() if p.name.split()[0].lower() == r.split()[0]]
        if len(firsts) == 1:
            last = r.split()[-1]
            if len(r.split()) == 1 or last == self.people[firsts[0]].name.split()[1].lower():
                return firsts[0]
        surn = [pid for pid, p in self.people.items() if p.name.split()[1].lower() == r]
        if len(surn) == 1:
            return surn[0]
        return None

    def resolve_firm(self, ref) -> str | None:
        if not isinstance(ref, str):
            return None
        r = ref.strip().lower()
        for fid, fi in self.firm_info.items():
            if r == fid or r == fi["name"].lower() or (len(r) > 4 and (r in fi["name"].lower() or fi["name"].lower() in r)):
                return fid
        pid = self.resolve(ref)
        if pid and self.people[pid].role == "owner":
            return self.people[pid].firm
        return None

    def owned_firms(self, pid: str) -> list[str]:
        return [f for f, fi in self.firm_info.items() if fi["owner"] == pid and self.firms[f].open]

    def deliver(self, to: str, kind: str, sender: str, text: str, request: str | None = None) -> None:
        self.inbox[to].append(Item(self.nid("m"), kind, sender, text, self.t, request))

    def observe(self, pid: str, text: str, trigger: bool = True) -> None:
        self.obs[pid].append(Obs(text, self.t, trigger))

    def hear(self, pid: str, text: str) -> None:
        self.heard[pid].append((self.t, text))

    def hear_household(self, pid: str, text: str) -> None:
        for m in self.hh[self.people[pid].household].members:
            if m != pid:
                self.hear(m, text)

    def hear_workplace(self, fid: str, text: str, skip: str | None = None) -> None:
        fi = self.firm_info[fid]
        for m in [fi["owner"]] + self.firms[fid].employees:
            if m != skip and m in self.people:
                self.hear(m, text)

    def publish(self, text: str) -> None:
        for p in self.people:
            self.hear(p, text)

    def record(self, **kw) -> None:
        kw.setdefault("t", self.t)
        self.log.append(kw)

    # ---------------------------------------------------------------- event deck ----------
    def _deck(self, rng: random.Random) -> list[dict]:
        P = self.people
        r = random.Random(f"deck:{self.seed}")
        ev = []
        outs = [p for p in P.values() if p.role == "outside"]
        lost = r.choice(outs)
        ev.append({"week": r.choice([1, 2]), "kind": "job_lost", "who": lost.id})
        tenants_out = [h for h, s in self.hh.items() if s.landlord == "outside:lettings"]
        if tenants_out:
            ev.append({"week": r.choice([1, 2]), "kind": "rent_rise", "hh": r.choice(tenants_out)})
        ev.append({"week": r.choice([2, 3]), "kind": "supplier_fail", "firm": r.choice(list(self.g.firms))})
        ill = r.choice([p for p in P.values() if p.role in ("employee", "outside", "owner")])
        ev.append({"week": r.choice([2, 3, 4]), "kind": "illness", "who": ill.id})
        # someone falls into arrears: a back-dated energy bill on the household with least to spare
        ev.append({"week": r.choice([1, 2]), "kind": "arrears_bill", "hh": None})
        # ordinary extras
        extra = [
            {"kind": "car_repair", "who": r.choice([p.id for p in P.values() if p.role != "unemployed"])},
            {"kind": "chiller", "firm": r.choice(list(self.g.firms))},
            {"kind": "school_trip", "hh": r.choice([h for h, s in self.hh.items() if s.children] or list(self.hh))},
            {"kind": "council_tax", "hh": r.choice(list(self.hh))},
        ]
        for e in r.sample(extra, 3):
            e["week"] = r.choice([1, 2, 3, 4])
            ev.append(e)
        for e in ev:
            e["round"] = r.choice([0, 0, 1, 2])
        return ev

    def fire_events(self) -> None:
        for e in self.events:
            if e["week"] != self.week or e["round"] != self.rnd or e.get("done"):
                continue
            e["done"] = True
            k = e["kind"]
            if k == "job_lost":
                p = self.people[e["who"]]
                emp = p.employer
                self.observe(p.id, f"{emp} told you today that your job is going: they are cutting the night shift. "
                                   f"This week is your last paid week (pay on {saturday_of(self.week)}); you will get two weeks' pay as redundancy with it. "
                                   f"After that you can claim jobseeker's benefit of {money(BENEFIT)} a week.")
                self.lost_job[p.id] = self.week
                self.hear_household(p.id, f"{p.name} has lost the job at {emp}.")
                for o, kinds in p.ties.items():
                    if "friend" in kinds or "sibling" in kinds:
                        self.hear(o, f"{p.name} has been laid off from {emp}.")
            elif k == "rent_rise":
                h = self.hh[e["hh"]]
                new = round(h.rent * 1.12 / 100) * 100
                frm = self.week + 2
                self.rent_rise[h.id] = (new, frm)
                e["new"], e["from"] = new, frm
                for m in h.members:
                    self.observe(m, f"A letter from {LETTINGS}: from {monday_of(frm)} your rent goes up from "
                                    f"{money(h.rent)} to {money(new)} a week.")
            elif k == "supplier_fail":
                fi = self.firm_info[e["firm"]]
                fi["fail_supplier_week"] = self.week + 1 if self.rnd > 0 else self.week
                e["applies_week"] = fi["fail_supplier_week"]
                sup = SUPPLIER_NAMES[fi["last_supplier"]]
                when = "this week's" if fi["fail_supplier_week"] == self.week else "next week's"
                self.observe(fi["owner"], f"{sup} phoned: their lorry is off the road and {when} delivery to "
                                          f"{fi['name']} will not come.")
                self.hear_workplace(e["firm"], f"{sup} will not be delivering to {fi['name']} this time.", skip=fi["owner"])
            elif k == "illness":
                p = self.people[e["who"]]
                self.sick[p.id] = self.week
                self.observe(p.id, "You have come down with a bad chest infection. The doctor says a week off work at least. "
                                   "The prescription cost $18.")
                self.one_off[p.household].append(("outside:bills", 1800, "prescription"))
                if p.firm:
                    self.firm_info[p.firm]["ill"].add(p.id)
                    self.hear_workplace(p.firm, f"{p.name} is off sick this week.", skip=p.id)
                self.hear_household(p.id, f"{p.name} is ill and off work this week.")
            elif k == "arrears_bill":
                def spare(h):
                    s = self.hh[h]
                    inc = sum(self.people[m].pay for m in s.members)
                    out = s.rent + s.running_cost + self.econ_hh[h].budget["food"] + sum(d.weekly for d in self.debts.values() if self.people.get(d.debtor) and self.people[d.debtor].household == h)
                    return self.ledger.balance(h) + inc - out
                hid = min(self.hh, key=spare)
                amt = max(50000, round((spare(hid) + 80000) / 1000) * 1000)
                e["hh"], e["amount"] = hid, amt
                self.one_off[hid].append(("outside:bills", amt, "back-dated energy bill"))
                for m in self.hh[hid].members:
                    self.observe(m, f"Northgrid Energy has sent a bill for {money(amt)}: they say the meter readings were "
                                    f"estimated for a year and you have underpaid. It will be taken from the household account on Saturday.")
            elif k == "car_repair":
                p = self.people[e["who"]]
                e["amount"] = 46000
                self.one_off[p.household].append(("outside:garage", 46000, "car repair"))
                self.observe(p.id, "The car failed its MOT: the garage wants $460 for brakes and a tyre, which will come out of the household account on Saturday when you collect it.")
            elif k == "chiller":
                fi = self.firm_info[e["firm"]]
                self.firm_one_off[e["firm"]].append(("outside:garage", 65000, "chiller repair"))
                self.observe(fi["owner"], f"The chiller at {fi['name']} broke down overnight. The engineer fixed it; the bill is $650 and will be taken from the shop\'s account on Saturday.")
                self.hear_workplace(e["firm"], f"The chiller at {fi['name']} broke down and had to be repaired.", skip=fi["owner"])
            elif k == "school_trip":
                h = self.hh[e["hh"]]
                self.one_off[h.id].append(("outside:bills", 7500, "school trip"))
                for m in h.members:
                    self.observe(m, "A letter from the school: $75 for the year's trip, taken from the household account on Saturday.", trigger=False)
            elif k == "council_tax":
                h = self.hh[e["hh"]]
                h.running_cost += 900
                for m in h.members:
                    self.observe(m, f"The council tax bill for the new year has come: your household bills go up by $9 a week, to {money(h.running_cost)}.", trigger=False)
            self.record(kind="event", event=dict(e))

    # ---------------------------------------------------------------- week routine ----------
    def start_week(self) -> None:
        """Monday before anyone acts: deliveries arrive and are paid for."""
        w = self.week
        self.ledger.week = w
        for pid in self.people:
            self.attempts_week[pid] = 0
        for fid, f in self.firms.items():
            fi = self.firm_info[fid]
            f.week = type(f.week)()
            fi["ordered_this_week"] = False
            fi["delivered"] = []
            if fi.get("service"):
                continue
            for sid, units, unit_price in fi["pending"]:
                got = 0
                if f.open:
                    got = self.suppliers[sid].fill(units, random.Random(f"deliver:{self.seed}:{w}:{fid}:{sid}"))
                    if fi["fail_supplier_week"] == w and sid == fi["last_supplier"]:
                        got = 0
                    got = min(got, max(0, self.ledger.balance(fid)) // unit_price)
                if got > 0:
                    self.ledger.transfer(fid, "outside:suppliers", got * unit_price, f"{got} units from {sid}", "routine:delivery")
                    f.receive(w, got, unit_price)
                fi["delivered"].append((sid, units, got, unit_price))
                if got < units and f.open:
                    self.observe(fi["owner"], f"{SUPPLIER_NAMES[sid]} delivered {got} of the {units} units you ordered.",
                                 trigger=got < units * 0.8)
            fi["pending"] = []

    def end_week(self) -> None:
        """Saturday night after the last round: buying, pay, rent, bills, loans, stock, reputation."""
        w = self.week
        L = self.ledger
        # capacity this week (sick staff do not work)
        for fid, f in self.firms.items():
            if f.open:
                f.staff = 1 + len(f.employees) - len(self.firm_info[fid]["ill"] & set(f.employees + [self.firm_info[fid]["owner"]]))
                f.staff = max(1, f.staff)
        # people's income
        for pid, p in self.people.items():
            hid = p.household
            if p.role == "outside":
                pay = p.pay
                if self.sick.get(pid) == w:
                    pay = 11600  # statutory sick pay
                if self.lost_job.get(pid) == w:
                    pay = p.pay * 3
                L.transfer("outside:employers", hid, pay, f"pay from {p.employer}", "routine:income")
            elif p.role == "unemployed" and p.pay:
                L.transfer("outside:gov", hid, p.pay, "jobseeker's benefit", "routine:income")
            elif p.role == "banker":
                L.transfer("bank", hid, p.pay, "salary", "routine:income")
        for e in self.bg:
            L.transfer("outside:employers", e.id, e.income, "income", "routine:income")
        # households keep shopping where they chose
        for hid, f in self.shop_choice.items():
            if f and self.firms.get(f) and self.firms[f].open:
                self.econ_hh[hid].last_bought["food"] = f
        # shopping: food for everyone, other needs for the background town where providers exist
        from townsim.econ.demand import Offering
        offs = [Offering(fid, f.need, f.price, f.quality, f.visibility) for fid, f in self.firms.items() if f.open]
        buyers = list(self.econ_hh.values()) + self.bg
        stats = {}
        before = {fid: f.week.sold for fid, f in self.firms.items()}
        for need, spec in ALL_NEEDS.items():
            needs_offs = [o for o in offs if o.need == need]
            if not needs_offs:
                continue
            for o in needs_offs:  # services restock to capacity each week
                f = self.firms[o.provider]
                if self.firm_info[o.provider].get("service"):
                    f.cohorts = []
                    f.receive(w, f.capacity(), round(spec.ref_price * 0.4))
            group = buyers if need == "food" else self.bg
            stats[need] = shop(L, group, needs_offs, self.firms, spec, self.rule, w, 1.0, self.seed, "routine:shopping")
        for fid, f in self.firms.items():
            if self.firm_info[fid].get("service") and f.week.sold:
                cost = f.week.cogs
                if cost > 0:
                    L.transfer(fid, "outside:suppliers", min(cost, max(1, L.balance(fid))), "materials", "routine:costs") if L.balance(fid) > 0 else None
        # firms pay wages, owner's draw and running costs, or fall behind
        for fid, f in self.firms.items():
            fi = self.firm_info[fid]
            if not f.open:
                continue
            due = dict(f.owed)
            for e in f.employees:
                due[e] = due.get(e, 0) + fi["pay"].get(e, 0)
            due[fi["owner"]] = due.get(fi["owner"], 0) + fi["pay"].get(fi["owner"], 0)
            due["outside:landlords"] = due.get("outside:landlords", 0) + f.fixed_cost
            for cred, amt, what in self.firm_one_off[fid]:
                due[cred] = due.get(cred, 0) + amt
            self.firm_one_off[fid] = []
            f.owed = {}
            # employees and outside bills first, the owner's draw last
            order = sorted(due, key=lambda d: (d == fi["owner"], d))
            for dst in order:
                if due[dst] <= 0:
                    continue
                pay = min(due[dst], max(0, L.balance(fid)))
                acct = self.people[dst].household if dst in self.people else dst
                if pay > 0:
                    L.transfer(fid, acct, pay, "wages" if dst in self.people else "costs", "routine:wages")
                if due[dst] > pay:
                    if dst == fi["owner"]:
                        self.observe(dst, f"{fi['name']} could only pay you {money(pay)} of your {money(due[dst])} this week.")
                    else:
                        f.owed[dst] = due[dst] - pay
                        if dst in self.people:
                            self.observe(dst, f"{fi['name']} paid you {money(pay)}; it still owes you {money(due[dst] - pay)} in wages.")
            f.arrears_cents = sum(f.owed.values())
            f.arrears_weeks = f.arrears_weeks + 1 if f.arrears_cents else 0
        # households: rent or mortgage, bills, loans, one-off bills, then arrears
        for hid, h in self.hh.items():
            if hid in self.rent_rise and self.rent_rise[hid][1] <= w:
                h.rent = self.rent_rise.pop(hid)[0]
            dues: list[list] = [list(x) for x in self.unpaid_bills[hid]]
            self.unpaid_bills[hid] = []
            if h.tenure == "rent":
                dues.append([h.landlord, h.rent, "rent"])
            dues.append(["outside:bills", h.running_cost, "household bills"])
            for cred, amt, what in self.one_off[hid]:
                dues.append([cred, amt, what])
            self.one_off[hid] = []
            for d in self.debts.values():
                if d.debtor in self.people and self.people[d.debtor].household == hid and d.weekly and d.balance > 0:
                    dues.append([f"debt:{d.id}", min(d.weekly, d.balance) + d.arrears, d.what])
                    d.arrears = 0
            for cred, amt, what in dues:
                if amt <= 0:
                    continue
                pay = min(amt, max(0, L.balance(hid)))
                if cred.startswith("debt:"):
                    d = self.debts[cred[5:]]
                    dst = "bank" if d.creditor == "bank" else self.people[d.creditor].household
                else:
                    dst = self.hh[self.people[cred].household].id if cred in self.people else cred
                if pay > 0:
                    L.transfer(hid, dst, pay, what, "routine:bills")
                    if cred.startswith("debt:"):
                        d.balance -= min(pay, d.balance)
                    if cred in self.people:
                        self.observe(cred, f"{self.name(h.members[0])}'s household paid the rent: {money(pay)}.", trigger=False)
                if amt > pay:
                    short = amt - pay
                    if cred.startswith("debt:"):
                        d.arrears += short
                        d.missed_weeks += 1
                        self.observe(self.banker if d.creditor == "bank" else d.creditor,
                                     f"{self.name(d.debtor)} missed {money(short)} of this week's payment on the {d.what}.")
                    else:
                        self.unpaid_bills[hid].append([cred, short, what])
                        if cred in self.people:
                            self.observe(cred, f"{self.name(h.members[0])}'s household is {money(short)} short on this week's rent.")
                    for m in h.members:
                        self.observe(m, f"There was not enough in the household account to pay all of the {what} "
                                        f"({money(amt)}): {money(short)} is now overdue to {self.name(cred if not cred.startswith('debt:') else d.creditor)}.")
        # stock ages; reputations move; projects advance
        for fid, f in self.firms.items():
            f.perish(w)
            if f.open:
                f.update_reputation()
            fi = self.firm_info[fid]
            fi["history"].append({"week": w, "price": f.price, "sold": f.week.sold, "revenue": f.week.revenue,
                                  "turned_away": f.week.turned_away_stock + f.week.turned_away_capacity,
                                  "spoiled": f.week.spoiled, "cash": L.balance(fid),
                                  "wages": sum(fi["pay"].get(e, 0) for e in f.employees) + fi["pay"].get(fi["owner"], 0),
                                  "fixed": f.fixed_cost, "stock_left": f.stock()})
            fi["ill"] = set()
            if f.open and f.arrears_weeks >= 4:
                f.open = False
                self.publish(f"{fi['name']} has closed its doors.")
        self._advance_projects()
        # standing reorder for firms whose owner ordered nothing this week
        for fid, f in self.firms.items():
            fi = self.firm_info[fid]
            if not f.open or fi.get("service") or fi["ordered_this_week"]:
                continue
            h = fi["history"][-2:]
            avg = sum(x["sold"] + x.get("turned_away", 0) * 0.5 for x in h) / len(h)
            units = max(0, round(avg * 1.05 - f.stock() * 0.85))
            sid = fi["last_supplier"]
            fi["pending"] = [(sid, units, self.suppliers[sid].price)] if units else []
            fi["standing_order"] = True
        for s in sorted(self.suppliers.values(), key=lambda s: s.id):
            s.drift(random.Random(f"drift:{self.seed}:{w}:{s.id}"))
        # outside job ends after the last paid week
        for pid, wk in self.lost_job.items():
            p = self.people[pid]
            if wk == w and p.role == "outside":
                p.role, p.pay, p.job_title, p.employer = "unemployed", BENEFIT, "out of work", None
        # the week's figures for the record
        self.stats_weeks.append({
            "week": w,
            "prices": {fid: f.price for fid, f in self.firms.items()},
            "sold": {fid: self.firm_info[fid]["history"][-1]["sold"] for fid in self.firms},
            "firm_cash": {fid: L.balance(fid) for fid in self.firms},
            "open": {fid: f.open for fid, f in self.firms.items()},
            "hh_cash": {hid: L.balance(hid) for hid in self.hh},
            "arrears": {hid: sum(x[1] for x in self.unpaid_bills[hid]) + sum(d.arrears for d in self.debts.values() if self.people.get(d.debtor) and self.people[d.debtor].household == hid) for hid in self.hh},
            "employment": {pid: (p.role, p.firm or p.employer) for pid, p in self.people.items()},
            "food_stats": vars(stats["food"]) if "food" in stats else None,
            "ledger_total": L.total(),
        })
        self.week_summaries()

    def week_summaries(self) -> None:
        """What each person learns on Saturday night, as plain observations (no trigger)."""
        w = self.week
        for pid, p in self.people.items():
            hid = p.household
            moves = [x for x in self.ledger.transfers if x.week == w and (x.dst == hid or x.src == hid) and x.cause != "setup"]
            inc = sum(x.cents for x in moves if x.dst == hid and x.cause in ("routine:income", "routine:wages"))
            out = sum(x.cents for x in moves if x.src == hid and x.cause in ("routine:bills", "routine:shopping"))
            food = sum(x.cents for x in moves if x.src == hid and x.cause == "routine:shopping")
            self.observe(pid, f"{saturday_of(w)}, end of the week: the household took in {money(inc)} in pay this week and paid out "
                              f"{money(out)} in rent, bills, loans and food ({money(food)} of it on food). "
                              f"The household account now holds {money(self.ledger.balance(hid))}.", trigger=False)
        for fid, f in self.firms.items():
            fi = self.firm_info[fid]
            h = fi["history"][-1]
            busy = "busy" if h["sold"] > (fi["history"][-2]["sold"] if len(fi["history"]) > 1 else h["sold"]) * 1.05 else (
                "quiet" if h["sold"] < (fi["history"][-2]["sold"] if len(fi["history"]) > 1 else h["sold"]) * 0.95 else "about as usual")
            for e in f.employees:
                self.hear(e, f"{fi['name']} was {busy} this week.")

    def _advance_projects(self) -> None:
        for pr in self.projects:
            if pr["done"]:
                continue
            payer = pr["payer"]
            amt = min(pr["weekly"], max(0, self.ledger.balance(payer)))
            if amt > 0:
                self.ledger.transfer(payer, "outside:suppliers", amt, f"{pr['kind']} work", "narrator:project")
            pr["weeks_left"] -= 1
            if pr["weeks_left"] <= 0:
                pr["done"] = True
                self.observe(pr["actor"], f"The {pr['kind'].replace('_', ' ')} you started ({pr['label']}) is finished.")
                self.apply_narrator_ops(pr["actor"], pr["on_complete"], pr["alloc"], pr.get("refs", {}))

    # ---------------------------------------------------------------- acts ----------
    def apply_act(self, pid: str, act, source: str = "turn") -> dict:
        """Validate and carry out one act. Returns {"op", "ok", "msg", ...}; msg is what the person is told."""
        if not isinstance(act, dict):
            return {"op": None, "ok": False, "msg": "Something you wrote could not be understood as an action."}
        if "attempt" in act and "op" not in act:
            return {"op": "attempt", "ok": None, "text": act.get("attempt"), "msg": ""}
        op = act.get("op")
        fn = getattr(self, f"op_{op}", None) if isinstance(op, str) else None
        if fn is None or op not in OPS_ALL:
            return {"op": op, "ok": False, "msg": f"'{op}' is not something you can do that way; anything else can be written as an attempt in plain words."}
        try:
            ok, msg, extra = fn(pid, act)
        except Overdraft:
            ok, msg, extra = False, "There was not enough money for that.", {}
        res = {"op": op, "ok": ok, "msg": msg, **extra}
        return res

    def _who(self, pid, act, key="to"):
        ref = act.get(key) or act.get("to") or act.get("with") or act.get("person") or act.get("who")
        o = self.resolve(ref)
        return o, ref

    def op_pay(self, pid, act):
        amt = to_cents(act.get("amount"))
        to_ref = act.get("to")
        if amt is None or amt <= 0:
            return False, "You did not say how much to pay.", {}
        frm = self.people[pid].household
        if act.get("from") and self.resolve_firm(act.get("from")) in self.owned_firms(pid):
            frm = self.resolve_firm(act.get("from"))
        dst_firm = self.resolve_firm(to_ref) if isinstance(to_ref, str) and self.resolve_firm(to_ref) and (self.firm_info[self.resolve_firm(to_ref)]["name"].lower() in to_ref.lower() or to_ref.startswith("firm:")) else None
        o = None if dst_firm else self.resolve(to_ref)
        if isinstance(to_ref, str) and ("bank" in to_ref.lower()):
            dst, label = "bank", "the bank"
        elif dst_firm:
            dst, label = dst_firm, self.firm_info[dst_firm]["name"]
        elif o:
            dst, label = self.people[o].household, self.people[o].name
        elif isinstance(to_ref, str) and "dunmore" in to_ref.lower():
            dst, label = "outside:lettings", LETTINGS
        elif isinstance(to_ref, str) and ("northgrid" in to_ref.lower() or "energy" in to_ref.lower()):
            dst, label = "outside:bills", "Northgrid Energy"
        else:
            return False, f"There is no one called '{to_ref}' that you can pay.", {"unknown_ref": to_ref}
        if dst == frm:
            return False, "That money is already there.", {}
        if self.ledger.balance(frm) < amt:
            return False, f"You tried to pay {label} {money(amt)}, but there is only {money(self.ledger.balance(frm))} to pay from.", {}
        self.ledger.transfer(frm, dst, amt, str(act.get("note", ""))[:80] or "payment", f"op:pay:{pid}")
        # paying overdue bills to this creditor clears them
        if frm == self.people[pid].household:
            left = amt
            cred_key = o if o else dst
            for b in self.unpaid_bills[frm]:
                if left and (b[0] == cred_key or (o and b[0] == o)):
                    take = min(b[1], left)
                    b[1] -= take
                    left -= take
            self.unpaid_bills[frm] = [b for b in self.unpaid_bills[frm] if b[1] > 0]
            if dst == "bank" or o:
                for d in self.debts.values():
                    if left and d.debtor in self.hh[frm].members and ((dst == "bank" and d.creditor == "bank") or d.creditor == o):
                        take = min(d.arrears, left)
                        d.arrears -= take
                        left -= take
        if o:
            note = f" ({act['note']})" if act.get("note") else ""
            self.observe(o, f"{self.people[pid].name} paid {money(amt)} into your household account{note}.")
        return True, f"You paid {label} {money(amt)}.", {"to": dst, "amount": amt}

    def op_message(self, pid, act):
        o, ref = self._who(pid, act)
        text = act.get("text") or act.get("message") or act.get("body")
        if not isinstance(text, str) or not text.strip():
            return False, "The message had no words in it.", {}
        if o is None:
            if isinstance(ref, str) and any(k in ref.lower() for k in ("dunmore", "lettings", "northgrid", "energy", "calder", "brennock", "halverd", "mersey", "dales", "brook")):
                return True, f"Your letter to {ref} has been sent.", {"outside": ref, "text": text}
            return False, f"You know of no one called '{ref}', so the message was not sent.", {"unknown_ref": ref}
        if o == pid:
            return False, "You cannot send a message to yourself; notes are for that.", {}
        self.deliver(o, "message", pid, text.strip())
        return True, f"Your message to {self.people[o].name} was delivered.", {"to": o, "text": text.strip()}

    def op_converse(self, pid, act):
        o, ref = self._who(pid, act, "with")
        opening = act.get("opening") or act.get("text") or act.get("say")
        if o is None:
            return False, f"You know of no one called '{ref}' to talk to.", {"unknown_ref": ref}
        if o == pid:
            return False, "You cannot talk to yourself.", {}
        if not isinstance(opening, str) or not opening.strip():
            return False, "You did not say what you would open with.", {}
        return True, "", {"with": o, "opening": opening.strip(), "queue_conversation": True}

    def op_request(self, pid, act):
        o, ref = self._who(pid, act)
        text = act.get("text") or act.get("ask") or act.get("what")
        if o is None:
            return False, f"You know of no one called '{ref}' to ask.", {"unknown_ref": ref}
        if o == pid:
            return False, "You cannot ask yourself.", {}
        if not isinstance(text, str) or not text.strip():
            return False, "You did not say what you are asking for.", {}
        amt = to_cents(act.get("amount")) if act.get("amount") not in (None, "", 0) else 0
        if amt is None or amt < 0:
            amt = 0
        summary = (f"If {self.first(o)} accepts, {money(amt)} goes from {self.first(o)}'s household account to {self.people[pid].name}'s."
                   if amt else f"If {self.first(o)} accepts, {self.people[pid].name} is told they agreed; nothing else happens by itself.")
        rid = self.nid("r")
        self.requests[rid] = Request(rid, pid, o, "ask", text.strip(), summary, self.t, self.t + REQUEST_EXPIRY_ROUNDS, amt)
        self.deliver(o, "request", pid, text.strip(), rid)
        return True, f"Your request ({rid}) went to {self.people[o].name}; it stays open for two weeks.", {"to": o, "request": rid, "text": text.strip(), "amount": amt}

    def op_answer(self, pid, act):
        rid = act.get("request") or act.get("id") or act.get("offer")
        ans = str(act.get("answer", "")).lower()
        r = self.requests.get(rid) if isinstance(rid, str) else None
        if r is None or r.to != pid:
            return False, f"There is no request '{rid}' waiting for your answer.", {}
        if r.status != "open":
            return False, f"Request {rid} is no longer open ({r.status}).", {}
        if ans not in ("accept", "decline", "yes", "no", "accepted", "declined"):
            return False, f"Your answer to {rid} has to be accept or decline.", {}
        note = act.get("note") or ""
        accept = ans in ("accept", "yes", "accepted")
        r.answered = self.t
        sender = r.sender
        noted = f' They wrote: "{note}"' if note else ""
        if not accept:
            r.status = "declined"
            self.deliver(sender, "answer", pid, f"declined your request (\"{r.text[:80]}\").{noted}", rid)
            return True, f"You declined {self.first(sender)}'s request.", {"request": rid, "answer": "decline"}
        ok, why = self._carry_out(r)
        if not ok:
            r.status = "failed"
            self.deliver(sender, "answer", pid, f"accepted your request, but it could not go through: {why}{noted}", rid)
            return False, f"You accepted, but it could not go through: {why}", {"request": rid}
        r.status = "accepted"
        self.deliver(sender, "answer", pid, f"accepted your request: \"{r.text[:120]}\".{noted}", rid)
        return True, f"You accepted {self.first(sender)}'s request. {why}".strip(), {"request": rid, "answer": "accept"}

    def _carry_out(self, r: Request) -> tuple[bool, str]:
        L = self.ledger
        if r.kind == "ask":
            if r.amount:
                hid = self.people[r.to].household
                if L.balance(hid) < r.amount:
                    return False, f"there is only {money(L.balance(hid))} in your household account."
                L.transfer(hid, self.people[r.sender].household, r.amount, "as asked", f"op:request:{r.id}")
                return True, f"{money(r.amount)} went to {self.people[r.sender].name}."
            return True, ""
        if r.kind == "job":
            f = self.firms.get(r.firm)
            if not f or not f.open:
                return False, "the business is no longer open."
            p = self.people[r.to]
            if p.role == "owner" or p.role == "banker":
                return False, "you already run something."
            self._leave_job(p.id, quietly=False)
            f.employees.append(p.id)
            fi = self.firm_info[r.firm]
            fi["pay"][p.id] = r.amount
            f.staff = 1 + len(f.employees)
            p.role, p.firm, p.pay, p.job_title = "employee", r.firm, r.amount, f"works at {fi['name']}"
            p.ties.setdefault(fi["owner"], []).append("employer")
            self.people[fi["owner"]].ties.setdefault(p.id, []).append("employee")
            self.hear_workplace(r.firm, f"{p.name} has started work at {fi['name']}.", skip=p.id)
            self.hear_household(p.id, f"{p.name} has taken a job at {fi['name']}.")
            return True, f"You now work at {fi['name']} for {money(r.amount)} a week, starting now."
        if r.kind == "loan":
            if L.balance("bank") < r.amount:
                return False, "the bank has not the funds."
            did = self.nid("d")
            weekly = round(r.amount * (1 + LOAN_RATE * r.weeks / 52) / r.weeks)
            L.transfer("bank", self.people[r.sender].household, r.amount, "loan", f"op:borrow:{r.id}")
            self.debts[did] = Debt(did, r.sender, "bank", round(r.amount * (1 + LOAN_RATE * r.weeks / 52)), weekly, f"bank loan ({r.text[:40]})")
            return True, f"The bank paid {money(r.amount)} to {self.people[r.sender].name}; they repay {money(weekly)} a week for {r.weeks} weeks."
        if r.kind == "narrator":
            self.apply_narrator_ops(r.sender, r.on_accept, r.payload_alloc if hasattr(r, "payload_alloc") else None, getattr(r, "refs", {}))
            return True, ""
        return False, "unknown request"

    def _leave_job(self, pid: str, quietly: bool = True) -> str | None:
        p = self.people[pid]
        if p.role == "employee" and p.firm:
            f = self.firms[p.firm]
            if pid in f.employees:
                f.employees.remove(pid)
            f.staff = 1 + len(f.employees)
            fi = self.firm_info[p.firm]
            fi["pay"].pop(pid, None)
            left = fi["name"]
            self.hear_workplace(p.firm, f"{p.name} no longer works at {left}.")
            p.firm = None
            return left
        if p.role == "outside":
            left = p.employer
            p.employer = None
            return left
        return None

    def op_offer_job(self, pid, act):
        firms = self.owned_firms(pid)
        if not firms:
            return False, "You have no business to offer a job at.", {}
        o, ref = self._who(pid, act)
        if o is None:
            return False, f"You know of no one called '{ref}'.", {"unknown_ref": ref}
        fid = self.resolve_firm(act.get("firm")) if act.get("firm") else firms[0]
        if fid not in firms:
            fid = firms[0]
        if o in self.firms[fid].employees or o == pid:
            return False, f"{self.people[o].name} already works there.", {}
        wage = to_cents(act.get("weekly_pay") or act.get("pay") or act.get("wage"))
        if not wage or wage <= 0:
            return False, "You did not say what the job pays a week.", {}
        fi = self.firm_info[fid]
        rid = self.nid("r")
        text = f"a job at {fi['name']} for {money(wage)} a week"
        if act.get("text"):
            text += f'. They wrote: "{act["text"]}"'
        summary = f"If {self.first(o)} accepts, they work at {fi['name']} from now for {money(wage)} a week" + (
            ", leaving their present job." if self.people[o].role in ("employee", "outside") else ".")
        self.requests[rid] = Request(rid, pid, o, "job", text, summary, self.t, self.t + REQUEST_EXPIRY_ROUNDS, wage, firm=fid)
        self.deliver(o, "request", pid, text, rid)
        return True, f"Your job offer ({rid}) went to {self.people[o].name}.", {"to": o, "request": rid}

    def op_accept_job(self, pid, act):
        rid = act.get("offer") or act.get("request") or act.get("id")
        if not rid:
            jobs = [r for r in self.requests.values() if r.to == pid and r.kind == "job" and r.status == "open"]
            if len(jobs) == 1:
                rid = jobs[0].id
        return self.op_answer(pid, {"request": rid, "answer": "accept", "note": act.get("note", "")})

    def op_quit(self, pid, act):
        p = self.people[pid]
        if p.role not in ("employee", "outside"):
            return False, "You have no job to leave.", {}
        boss = self.firm_info[p.firm]["owner"] if p.firm else None
        left = self._leave_job(pid)
        p.role, p.pay, p.job_title = "unemployed", BENEFIT, "out of work"
        if boss:
            self.observe(boss, f"{p.name} has quit {left}.")
        self.hear_household(pid, f"{p.name} has left the job at {left}.")
        return True, f"You have left {left}. You can claim jobseeker's benefit of {money(BENEFIT)} a week.", {}

    def op_dismiss(self, pid, act):
        firms = self.owned_firms(pid)
        o, ref = self._who(pid, act)
        if not firms:
            return False, "You employ no one.", {}
        if o is None or not any(o in self.firms[f].employees for f in firms):
            return False, f"'{ref}' does not work for you.", {"unknown_ref": ref if o is None else None}
        p = self.people[o]
        left = self._leave_job(o)
        p.role, p.pay, p.job_title = "unemployed", BENEFIT, "out of work"
        self.observe(o, f"{self.people[pid].name} has dismissed you from {left}." + (f' They said: "{act["text"]}"' if act.get("text") else ""))
        self.hear_household(o, f"{p.name} has been let go from {left}.")
        return True, f"You dismissed {p.name}.", {"who": o}

    def op_set_price(self, pid, act):
        firms = self.owned_firms(pid)
        if not firms:
            return False, "You have no business whose prices you set.", {}
        fid = self.resolve_firm(act.get("firm")) if act.get("firm") else firms[0]
        if fid not in firms:
            fid = firms[0]
        c = to_cents(act.get("price"))
        if not c or c <= 0 or c > 5000 * (ALL_NEEDS[self.firms[fid].need].ref_price // 1000 or 1):
            return False, f"'{act.get('price')}' is not a price that can be set.", {}
        old = self.firms[fid].price
        self.firms[fid].price = c
        if c != old:
            self.publish(f"{self.firm_info[fid]['name']} has changed its price for a basket from {money(old)} to {money(c)}.")
        return True, f"The price at {self.firm_info[fid]['name']} is now {money(c)}" + (" (unchanged)." if c == old else "."), {"firm": fid, "price": c, "old": old}

    def op_order_stock(self, pid, act):
        firms = [f for f in self.owned_firms(pid) if not self.firm_info[f].get("service")]
        if not firms:
            return False, "You have no shop to order stock for.", {}
        fid = self.resolve_firm(act.get("firm")) if act.get("firm") else firms[0]
        if fid not in firms:
            fid = firms[0]
        sref = str(act.get("supplier", "")).lower()
        sid = "cheap" if ("brook" in sref or "cheap" in sref or "cash" in sref) else ("reliable" if ("dales" in sref or "reliable" in sref or "wholesale" in sref) else None)
        if sid is None:
            return False, f"'{act.get('supplier')}' is not a supplier you deal with (Brook Cash & Carry or Dales Wholesale).", {"unknown_ref": act.get("supplier")}
        units = act.get("units") or act.get("quantity")
        if isinstance(units, str) and units.strip().isdigit():
            units = int(units)
        if not isinstance(units, (int, float)) or isinstance(units, bool) or units <= 0 or units > 3000:
            return False, f"'{units}' is not a number of units that can be ordered.", {}
        fi = self.firm_info[fid]
        if fi["ordered_this_week"] is False or fi.get("standing_order"):
            fi["pending"] = []
            fi["standing_order"] = False
        fi["pending"].append((sid, int(units), self.suppliers[sid].price))
        fi["ordered_this_week"] = True
        fi["last_supplier"] = sid
        return True, f"Ordered {int(units)} units from {SUPPLIER_NAMES[sid]} at {money(self.suppliers[sid].price)} each, for delivery on Monday.", {"firm": fid, "units": int(units), "supplier": sid}

    def op_set_staff_pay(self, pid, act):
        firms = self.owned_firms(pid)
        if not firms:
            return False, "You have no staff whose pay you set.", {}
        o, ref = self._who(pid, act, "who")
        wage = to_cents(act.get("weekly_pay") or act.get("pay") or act.get("wage"))
        fid = next((f for f in firms if o == pid or o in self.firms[f].employees), None)
        if fid is None or o is None:
            return False, f"'{ref}' is not on your payroll.", {"unknown_ref": ref if o is None else None}
        if wage is None or wage < 0:
            return False, "You did not say the new weekly pay.", {}
        old = self.firm_info[fid]["pay"].get(o, 0)
        self.firm_info[fid]["pay"][o] = wage
        if o != pid:
            self.people[o].pay = wage
            self.observe(o, f"{self.people[pid].name} has set your pay at {self.firm_info[fid]['name']} to {money(wage)} a week (it was {money(old)}).")
        return True, f"{'Your own draw' if o == pid else self.people[o].name + chr(39) + 's pay'} at {self.firm_info[fid]['name']} is now {money(wage)} a week.", {"who": o, "pay": wage, "old": old}

    def op_set_rent(self, pid, act):
        lets = [h for h, s in self.hh.items() if s.landlord == pid]
        if not lets:
            return False, "You let no house to anyone.", {}
        hid = lets[0]
        c = to_cents(act.get("weekly") or act.get("rent") or act.get("amount"))
        if not c or c <= 0:
            return False, "You did not say the new weekly rent.", {}
        old = self.hh[hid].rent
        frm = self.week + 1
        self.rent_rise[hid] = (c, frm)
        for m in self.hh[hid].members:
            self.observe(m, f"{self.people[pid].name}, your landlord, has set your rent at {money(c)} a week from {monday_of(frm)} (it is {money(old)})." +
                         (f' They wrote: "{act["text"]}"' if act.get("text") else ""))
        return True, f"The rent for the house you let will be {money(c)} a week from {monday_of(frm)}.", {"rent": c, "old": old}

    def op_borrow(self, pid, act):
        if pid == self.banker:
            return False, "You cannot lend the bank's money to yourself.", {}
        amt = to_cents(act.get("amount"))
        weeks = act.get("weeks") or 52
        if isinstance(weeks, str) and weeks.isdigit():
            weeks = int(weeks)
        if not amt or amt <= 0:
            return False, "You did not say how much you want to borrow.", {}
        if not isinstance(weeks, (int, float)) or weeks < 1 or weeks > 520:
            return False, "The loan length has to be a number of weeks.", {}
        weeks = int(weeks)
        weekly = round(amt * (1 + LOAN_RATE * weeks / 52) / weeks)
        purpose = act.get("purpose") or act.get("text") or "not stated"
        rid = self.nid("r")
        text = f"a loan of {money(amt)} over {weeks} weeks. Purpose: {purpose}"
        summary = (f"If you accept, the bank pays {money(amt)} to {self.people[pid].name}, who repays {money(weekly)} a week "
                   f"for {weeks} weeks (12% a year, flat).")
        self.requests[rid] = Request(rid, pid, self.banker, "loan", text, summary, self.t, self.t + REQUEST_EXPIRY_ROUNDS, amt, weeks)
        self.deliver(self.banker, "request", pid, text, rid)
        return True, f"Your loan application ({rid}) went to {self.people[self.banker].name} at the bank.", {"request": rid, "amount": amt}

    def op_repay(self, pid, act):
        amt = to_cents(act.get("amount"))
        if not amt or amt <= 0:
            return False, "You did not say how much to repay.", {}
        mine = [d for d in self.debts.values() if d.debtor in self.hh[self.people[pid].household].members and d.balance > 0]
        did = act.get("debt") or act.get("loan")
        d = self.debts.get(did) if isinstance(did, str) else None
        if d is None and act.get("to"):
            o = self.resolve(act.get("to"))
            cands = [x for x in mine if (x.creditor == o) or (x.creditor == "bank" and (o == self.banker or "bank" in str(act.get("to")).lower()))]
            cands.sort(key=lambda x: (x.what.startswith("mortgage"), x.balance))
            d = cands[0] if cands else None
        if d is None and len(mine) == 1:
            d = mine[0]
        if d is None or d not in mine:
            return False, f"There is no debt '{did or act.get('to')}' of yours to repay.", {}
        hid = self.people[pid].household
        if self.ledger.balance(hid) < amt:
            return False, f"There is only {money(self.ledger.balance(hid))} in the household account.", {}
        amt = min(amt, d.balance + d.arrears)
        dst = "bank" if d.creditor == "bank" else self.people[d.creditor].household
        self.ledger.transfer(hid, dst, amt, f"repayment on {d.what}", f"op:repay:{pid}")
        take = min(d.arrears, amt)
        d.arrears -= take
        d.balance -= min(d.balance, amt - take)
        if d.creditor != "bank":
            self.observe(d.creditor, f"{self.people[pid].name} paid you back {money(amt)}; {money(d.balance)} is still owed.")
        else:
            self.observe(self.banker, f"{self.people[pid].name} made an extra repayment of {money(amt)} on the {d.what}.", trigger=False)
        return True, f"You repaid {money(amt)} on the {d.what}; {money(d.balance)} is still owed.", {"debt": d.id, "amount": amt}

    def op_shop_at(self, pid, act):
        fid = self.resolve_firm(act.get("firm") or act.get("shop") or act.get("to"))
        if not fid or not self.firms[fid].open:
            return False, f"'{act.get('firm') or act.get('shop')}' is not a shop in town.", {"unknown_ref": act.get("firm") or act.get("shop")}
        hid = self.people[pid].household
        self.shop_choice[hid] = fid
        self.hear_household(pid, f"{self.first(pid)} says the household will do its shopping at {self.firm_info[fid]['name']} from now on.")
        return True, f"Your household will do its usual shopping at {self.firm_info[fid]['name']}.", {"firm": fid}

    def op_write_notes(self, pid, act):
        text = act.get("text") or act.get("notes") or act.get("note")
        if not isinstance(text, str) or not text.strip():
            return False, "The note was empty.", {}
        self.notes[pid].append(f"({self.when()}) {text.strip()[:400]}")
        self.notes[pid] = self.notes[pid][-6:]
        return True, "", {}

    # ---------------------------------------------------------------- expiry ----------
    def expire_requests(self) -> None:
        for r in self.requests.values():
            if r.status == "open" and self.t >= r.expires:
                r.status = "lapsed"
                self.observe(r.sender, f"Your request to {self.people[r.to].name} (\"{r.text[:80]}\") got no answer in two weeks and has lapsed.")
                for it in self.inbox[r.to]:
                    if it.request == r.id:
                        it.shown = True

    # ---------------------------------------------------------------- narrator plumbing ----------
    def narrator_world(self) -> dict:
        """The record in the shape the narrator spike's prompts and validator read."""
        qual = lambda x: "high" if x >= 0.7 else ("middling" if x >= 0.45 else "low")  # noqa: E731
        people = {}
        for pid, p in self.people.items():
            facts = [f["text"] for f in self.facts if pid in f["subjects"]][-4:]
            if p.role == "unemployed":
                facts.append("out of work")
            people[pid] = {"name": p.name, "household": p.household, "job": p.job_title, "cash_cents": self.cash(pid),
                           "skills": p.skills, "controls": self.owned_firms(pid) + (["bank"] if pid == self.banker else []),
                           "facts": facts}
        hhs = {hid: {"members": h.members, "dwelling": f"{'rented' if h.tenure == 'rent' else 'owned'} house on {h.street}"}
               for hid, h in self.hh.items()}
        orgs = {}
        for fid, f in self.firms.items():
            fi = self.firm_info[fid]
            orgs[fid] = {"name": fi["name"], "kind": "firm", "need": f.need, "offering": fi["offering"], "owner": fi["owner"],
                         "staff": [fi["owner"]] + f.employees, "cash_cents": self.ledger.balance(fid),
                         "quality": qual(f.quality), "capacity": "middling", "reputation": qual(f.reputation),
                         "visibility": qual(f.visibility),
                         "facts": [x["text"] for x in self.facts if fid in x["subjects"]][-4:] + ([] if f.open else ["closed"])}
        orgs["bank"] = {"name": "Northern Mutual Bank, Wenlow branch", "kind": "bank", "need": None, "offering": "savings accounts and loans",
                        "owner": None, "officers": [self.banker], "cash_cents": self.ledger.balance("bank"), "facts": []}
        places = {}
        for hid, h in self.hh.items():
            owner = h.landlord if (h.landlord and h.landlord.startswith("p")) else (h.members[0] if h.tenure != "rent" else None)
            if owner:
                places[f"home:{hid}"] = {"name": f"the {self.people[h.members[0]].name.split()[1]} family's house on {h.street}",
                                         "owner": owner, "facts": ["a terraced house with a back yard"]}
        return {"needs": list(ALL_NEEDS), "households": hhs, "people": people, "orgs": orgs, "places": places,
                "outside": "Garages, course providers, wholesalers and other suppliers beyond the town sell to anyone who pays; they are not named in the record.",
                "rules": {}}

    def apply_narrator_ops(self, actor: str, ops: list, alloc: dict | None, refs: dict | None = None) -> list[str]:
        """Apply validated narrator operations minimally. Returns observations given to the actor."""
        told = []
        refs = refs if refs is not None else {}
        hid = self.people[actor].household
        for op in ops:
            k = op.get("op")
            if k == "create_org":
                scale = op["scale"]
                cost = CREATE_ORG_COST[scale]
                payer = hid
                if self.ledger.balance(payer) < cost:
                    told.append(f"Setting up {op['name']} would cost {money(cost)}; there was not enough in the household account.")
                    continue
                self.ledger.transfer(payer, "outside:suppliers", cost, f"setting up {op['name']}", f"narrator:create_org:{actor}")
                fid = f"firm:{len(self.firms) + 1}"
                need = op["need"]
                spec = ALL_NEEDS[need]
                f = Firm(fid, need, spec.ref_price, 0.5, SCALE_VIS[scale], 0.5, 1, SCALE_UNITS[scale], 0, SCALE_FIXED[scale])
                self.firms[fid] = f
                self.ledger.open(fid)
                self.firm_info[fid] = {"name": op["name"], "owner": actor, "pay": {actor: 0}, "street": self.hh[hid].street,
                                       "offering": op.get("offering", ""), "pending": [], "ordered_this_week": False,
                                       "last_supplier": "reliable", "history": [{"week": self.week, "price": f.price, "sold": 0, "revenue": 0,
                                                                                 "turned_away": 0, "spoiled": 0, "cash": 0, "wages": 0, "fixed": f.fixed_cost}],
                                       "price_set_by": None, "fail_supplier_week": None, "ill": set(), "service": need != "food"}
                self.firm_one_off[fid] = []
                self.new_orgs[fid] = {"by": actor, "scale": scale, "need": need, "t": self.t}
                if op.get("ref"):
                    refs[op["ref"]] = fid
                self.publish(f"A new business has opened: {op['name']}, run by {self.people[actor].name}, offering {op.get('offering', '')}.")
                told.append(f"You have set up {op['name']} ({op.get('offering', '')}). Setting it up cost {money(cost)}. "
                            f"It has its own account, which is empty; it sells at {money(f.price)} a time to begin with, and running it costs {money(f.fixed_cost)} a week.")
            elif k == "invest":
                tgt = refs.get(op.get("target_id"), op.get("target_id"))
                attr = op.get("attribute")
                amt = (alloc or {}).get("money_cents_each", 0) + int((alloc or {}).get("hours_each", 0) * 1500)
                cash_part = min((alloc or {}).get("money_cents_each", 0), max(0, self.ledger.balance(hid)))
                if cash_part > 0:
                    self.ledger.transfer(hid, "outside:suppliers", cash_part, f"spent on {attr}", f"narrator:invest:{actor}")
                if amt <= 0:
                    told.append("Nothing was put into it, so nothing changed.")
                    continue
                gain = 1 - math.exp(-amt / INVEST_K.get(attr, 150000))
                if attr == "skill":
                    self.facts.append({"subjects": [actor], "text": "has been working on their skills", "t": self.t})
                    told.append(f"You put {money(cash_part)} and your time into improving your skills.")
                elif tgt in self.firms:
                    f = self.firms[tgt]
                    if attr == "capacity":
                        f.units_per_staff = round(f.units_per_staff * (1 + 0.5 * gain))
                    else:
                        cur = getattr(f, attr)
                        setattr(f, attr, cur + (1 - cur) * 0.5 * gain)
                    told.append(f"You put {money(cash_part)}{' and your time' if (alloc or {}).get('hours_each') else ''} into the {attr} of {self.firm_info[tgt]['name']}.")
            elif k == "project":
                scale, kind = op["scale"], op["kind"]
                weeks = PROJECT_WEEKS[scale]
                cost = PROJECT_COST.get(kind, (0, 0, 0))[["small", "medium", "large"].index(scale)]
                tgt = refs.get(op.get("target_id"), op.get("target_id"))
                payer = tgt if tgt in self.firms and self.firm_info[tgt]["owner"] == actor and not str(tgt).startswith("p") else hid
                label = self.name(tgt) if tgt in self.people or tgt in self.firms else "your own affairs"
                self.projects.append({"actor": actor, "kind": kind, "scale": scale, "weekly": cost // weeks, "weeks_left": weeks,
                                      "on_complete": op.get("on_complete", []), "alloc": alloc, "done": False, "payer": payer,
                                      "label": label, "refs": refs, "t": self.t})
                told.append(f"You have begun: {kind.replace('_', ' ')} ({label}). It will take {weeks} week{'s' if weeks > 1 else ''}"
                            + (f" and cost {money(cost // weeks)} a week." if cost else "."))
            elif k == "request":
                to = op.get("to_id")
                if to in self.firm_info:
                    to = self.firm_info[to]["owner"]
                if to == "bank":
                    to = self.banker
                if to not in self.people or to == actor:
                    continue
                rid = self.nid("r")
                r = Request(rid, actor, to, "narrator", op.get("text", ""),
                            f"If {self.first(to)} accepts, {self.first(actor)} goes ahead with it.", self.t, self.t + REQUEST_EXPIRY_ROUNDS,
                            on_accept=op.get("on_accept", []))
                r.payload_alloc = alloc  # type: ignore[attr-defined]
                r.refs = refs  # type: ignore[attr-defined]
                self.requests[rid] = r
                self.deliver(to, "request", actor, op.get("text", ""), rid)
                told.append(f"You have put it to {self.people[to].name}: \"{op.get('text', '')}\" (request {rid}).")
            elif k == "add_fact":
                subs = [refs.get(s, s) for s in op.get("subjects", [])]
                self.facts.append({"subjects": subs, "text": op.get("text", ""), "t": self.t, "by": actor})
                concerned = set()
                for s in subs:
                    if s in self.people:
                        concerned.add(s)
                    elif s in self.firm_info:
                        concerned.add(self.firm_info[s]["owner"])
                        concerned.update(self.firms[s].employees)
                for c in concerned - {actor}:
                    self.hear(c, f"{self.name(subs[0]) if subs else ''}: {op.get('text', '')}".strip(": "))
                told.append(f"{op.get('text', '')}")
        for t in told:
            self.observe(actor, t, trigger=False)
        return told
