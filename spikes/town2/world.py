"""Town 2: the world. Days, hours, bills on schedule with consequences, agreements and
standing payments, appointments, dated records, unknown names caught, a fixed deck,
desks for outsiders, and the company. No model is called in this file.

Absolute day t = 0 is Monday 2 March. Money is integer cents.
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

from townsim.econ.demand import NEEDS, DemandRule, Household, NeedSpec, Offering, shop  # noqa: E402
from townsim.econ.firm import Firm  # noqa: E402
from townsim.econ.ledger import Ledger, Overdraft  # noqa: E402
from townsim.econ.suppliers import default_suppliers  # noqa: E402

from gen import DAYS7, Generated, generate2  # noqa: E402
from ops import HOURS, OPS  # noqa: E402

N_BACKGROUND = 150
UNITS_PER_STAFF = 170
STAFF_HOURS = 40
FIXED_COST = 25000
SUPPLIER_NAMES = {"cheap": "Brook Cash & Carry", "reliable": "Dales Wholesale"}
LETTINGS = "Dunmore Lettings"
BENEFIT = 19000
REQUEST_EXPIRY = 14  # days
LOAN_RATE = 0.12
WEEK_HOURS = 70  # waking hours a week that are not sleep, meals and the like
OPEN_WEEK = 3  # Calder Systems opens for business on the Monday of this week
FEE = {"person": 1200, "firm": 3000}
RUN_COST = {"person": 800, "firm": 2000}
FREE_WEEKS = 4
COLLECTION_WEEKLY = 4000
OUTSIDE_FEE = 2500
BANK_FEE = 1500

EXTRA_NEEDS = {
    "repairs": NeedSpec("repairs", 0.12, 0.7, 4000, 0.0, 0, 5200, 0.6, 1500, 1.1, 0.15, 0.15, 1.5),
    "care": NeedSpec("care", 0.06, 0.7, 3000, 0.0, 0, 4000, 0.6, 1200, 1.1, 0.15, 0.15, 1.5),
    "leisure": NeedSpec("leisure", 0.4, 0.85, 1200, 0.0, 0, 1500, 0.6, 500, 1.1, 0.2, 0.15, 1.5),
    "deliveries": NeedSpec("deliveries", 0.25, 0.85, 500, 0.0, 0, 800, 0.5, 300, 1.1, 0.2, 0.15, 1.5),
    "bookkeeping": NeedSpec("bookkeeping", 0.02, 0.5, 6000, 0.0, 0, 8000, 0.6, 2000, 1.1, 0.15, 0.1, 1.5),
}
ALL_NEEDS = {"food": NEEDS["food"], **EXTRA_NEEDS}
CREATE_ORG_COST = {"small": 10000, "medium": 100000, "large": 1000000}
PROJECT_COST = {
    "fit_out": (20000, 100000, 1000000), "build": (20000, 100000, 1000000), "repair": (15000, 100000, 1000000),
    "campaign": (0, 0, 100000), "petition": (0, 0, 100000), "event": (0, 0, 100000), "survey": (0, 0, 100000),
}
PROJECT_WEEKS = {"small": 1, "medium": 2, "large": 4}
SCALE_VIS = {"small": 0.2, "medium": 0.35, "large": 0.5}
SCALE_UNITS = {"small": 40, "medium": 120, "large": 300}
SCALE_FIXED = {"small": 2000, "medium": 10000, "large": 40000}
INVEST_K = {"quality": 200000, "visibility": 100000, "reputation": 150000, "capacity": 200000, "skill": 50000}
OUTSIDE_NAMES = {"outside:bills": "Northgrid Energy", "outside:lettings": LETTINGS, "outside:garage": "the garage",
                 "outside:market": "shops and services", "outside:council": "Wenlow Town Council", "outside:collect": "Fenton Collections",
                 "bank": "the Northern Mutual Bank", "co:calder": "Calder Systems", "outside:suppliers": "suppliers",
                 "outside:school": "the school", "outside:pharmacy": "the pharmacy"}
DESKS = {"desk:northgrid": "Northgrid Energy customer desk", "desk:brook": "Brook Cash & Carry sales desk",
         "desk:dales": "Dales Wholesale sales desk", "desk:bankhq": "Northern Mutual Bank head office"}
KNOWN_WORDS = set("""I I'm I'll I've I'd Monday Tuesday Wednesday Thursday Friday Saturday Sunday Mondays Fridays Saturdays Sundays
January February March April May June July August September October November December Mon Tue Wed Thu Fri Sat Sun Mar Apr
Wenlow Mill Street Church Lane Quarry Road Station Back Orchard Row High Northern Mutual Bank Dunmore Lettings Northgrid Energy
Brook Cash Carry Dales Wholesale Calder Systems Logistics Brennock General Hospital Halverd Foods Mersey Cable Works Corner Stores
Bakery Deli Food Hall Grocers Family Butchers Plough Town Council Fenton Collections Kesterton MOT JSA Christmas Easter
The A An It Its They He She His Her We Our You Your My Me If And But So Or No Yes OK Ok Okay Hi Hiya Thanks Thank Cheers Love Dear
Dad Mum Morning Evening Afternoon Right Well Aye Fine Sorry Please Just Let Not Can Could Would Will Should Do Don't That This What
When Where Why How Who Is Are Was Be Been Have Has Had Any Some All Got Good Sure One Two Mr Mrs Ms Hello Regards Best Sincerely
Look Listen Also Then Now Here There Still Even Maybe Perhaps Plus Next Last First Every Each Both Nothing Something Anything
Everyone Anyway Honestly Actually Really Until After Before Since While Because Though Although Once Again Shop Shops Council
Town Market Account Calder's Head Office Desk Customer Sales Support Team Ta Bye See Cheers Kind Warm Many Hope Thanks Note
Re Subject PS Order Invoice Bill Rent Notice Agreement Job Pay Wages Hours""".split())


MONTH_DAYS = [("March", 31), ("April", 30), ("May", 31), ("June", 30)]


def date_of(t: int) -> str:
    d = 2 + t
    for name, n in MONTH_DAYS:
        if d <= n:
            return f"{DAYS7[t % 7]} {d} {name}"
        d -= n
    return f"{DAYS7[t % 7]} day {t}"


def monday_of(week: int) -> str:
    return date_of((week - 1) * 7)


def money(c: int | float) -> str:
    c = int(round(c))
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}${c // 100:,}" if c % 100 == 0 else f"{sign}${c / 100:,.2f}"


def to_cents(v) -> int | None:
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


def num(v) -> float | None:
    c = to_cents(v)
    return None if c is None else c / 100


class TFirm(Firm):
    extra: int = 0  # extra units it can serve this week from extra hours worked

    def capacity(self) -> int:
        return self.staff * self.units_per_staff + self.extra


@dataclass
class Debt:
    id: str
    debtor: str
    creditor: str  # "bank", a person id, or an outside account
    balance: int
    weekly: int
    what: str
    arrears: int = 0
    missed: int = 0


@dataclass
class Bill:
    id: str
    hid: str
    creditor: str  # account id or person id (rent to a landlord)
    amount: int
    what: str
    due: int  # day
    status: str = "due"  # due | paid | overdue
    paid_on: int | None = None
    fee: int = 0
    notice_on: int | None = None
    collection_on: int | None = None
    debt: str | None = None  # a debt this instalment belongs to


@dataclass
class Request:
    id: str
    sender: str
    to: str
    kind: str  # ask | job | loan | shifts | agreement | narrator | unpaid
    text: str
    summary: str
    made: int
    expires: int
    amount: int = 0
    weeks: int = 0
    hours: float = 0
    firm: str | None = None
    terms: dict = field(default_factory=dict)
    on_accept: list = field(default_factory=list)
    status: str = "open"
    answered: int | None = None


@dataclass
class Agreement:
    id: str
    kind: str  # standing | repayment_plan | sale | retainer | job_terms | calder_fee | payment_plan
    payer: str  # ledger account owner: person id, firm id, or outside id
    payee: str
    amount: int
    day: int  # 0-6
    weeks_left: int  # 0 = until cancelled
    what: str
    parties: list
    made: int
    status: str = "active"
    start: int = 0  # first day it applies
    debt: str | None = None


@dataclass
class Item:
    id: str
    kind: str  # message | request | answer | notice
    sender: str
    text: str
    t: int
    request: str | None = None
    shown: bool = False


@dataclass
class Obs:
    text: str
    t: int
    trigger: bool = True


@dataclass
class Appointment:
    id: str
    a: str
    b: str
    t: int
    about: str
    status: str = "booked"


class World:
    def __init__(self, seed: int, arm: str = "people"):
        self.seed, self.arm = seed, arm
        self.g: Generated = generate2(seed)
        assert not self.g.problems, self.g.problems
        self.people, self.hh = self.g.people, self.g.households
        self.t = 0
        self.L = Ledger()
        for a in ("outside:capital", "outside:suppliers", "outside:market", "outside:employers", "outside:landlords", "outside:lettings",
                  "outside:bills", "outside:gov", "outside:garage", "outside:council", "outside:collect", "outside:compute",
                  "outside:school", "outside:pharmacy"):
            self.L.open(a, allow_credit=True)
        self.lines: list[tuple[int, str, str, int, str]] = []  # day, src, dst, cents, memo
        self.rule = DemandRule()
        rng = random.Random(f"world:{seed}")
        spec = NEEDS["food"]
        self.econ_hh: dict[str, Household] = {}
        for hid, h in self.hh.items():
            size = len(h.members) + h.children
            wtp = spec.wtp_mean * math.exp(rng.gauss(0, spec.wtp_sd) - spec.wtp_sd ** 2 / 2)
            e = Household(hid, size, {"food": wtp}, {}, 0)
            e.budget["food"] = round(e.expected_wants(spec) * spec.ref_price * spec.budget_factor)
            self.econ_hh[hid] = e
            self.L.open(hid)
            self.L.transfer("outside:capital", hid, h.savings, "savings at start", "setup")
        self.bg: list[Household] = []
        for i in range(N_BACKGROUND):
            size = rng.choices([1, 2, 3, 4, 5], weights=[28, 34, 16, 14, 8])[0]
            wtps = {n: s.wtp_mean * math.exp(rng.gauss(0, s.wtp_sd) - s.wtp_sd ** 2 / 2) for n, s in ALL_NEEDS.items()}
            e = Household(f"bg:{i:03d}", size, wtps, {}, 0)
            for n, s in ALL_NEEDS.items():
                e.budget[n] = round(e.expected_wants(s) * s.ref_price * s.budget_factor)
            e.income = round(sum(e.budget.values()) * rng.uniform(1.1, 1.4))
            self.bg.append(e)
            self.L.open(e.id)
            self.L.transfer("outside:capital", e.id, e.income, "savings at start", "setup")
        self.firms: dict[str, TFirm] = {}
        self.fi: dict[str, dict] = {}
        for fid, fs in self.g.firms.items():
            staff = 1 + len(fs.employees)
            f = TFirm(fid, "food", 1000, fs.quality, 0.5, 0.75, staff, UNITS_PER_STAFF, 0, FIXED_COST)
            f.extra = 0
            f.employees = list(fs.employees)
            f.receive(0, 60 if staff == 2 else 80, 690)
            self.firms[fid] = f
            self.L.open(fid)
            self.L.transfer("outside:capital", fid, fs.cash, "owner's capital", "setup")
            base = 300 if staff == 2 else 440
            self.fi[fid] = {"name": fs.name, "owner": fs.owner, "pay": dict(fs.pay), "hours": {e: 38 for e in fs.employees},
                            "street": fs.street, "offering": "groceries and fresh food",
                            "pending": [("reliable", base, 700)], "ordered_this_week": False, "standing_order": False,
                            "last_supplier": "reliable", "fail_week": None, "ill": set(), "service": False, "shifts": {},
                            "history": [{"week": 0, "price": 1000, "sold": base, "revenue": base * 1000, "turned_away": 0,
                                         "spoiled": 25, "cash": fs.cash, "stock_left": 0}], "delivered": []}
        self.suppliers = {s.id: s for s in default_suppliers()}
        self.L.open("bank")
        self.L.transfer("outside:capital", "bank", self.g.bank_cash, "bank's lending funds", "setup")
        self.debts = {d.id: Debt(d.id, d.debtor, d.creditor, d.principal, d.weekly, d.what) for d in self.g.debts.values()}
        self.banker = next(p for p, x in self.people.items() if x.role == "banker")
        self.company = [p for p, x in self.people.items() if x.role == "company"]
        self.founder = self.company[0]
        self.L.open("co:calder")
        self.L.transfer("outside:capital", "co:calder", 900000, "founder's capital and a small investment", "setup")
        self.product_price = dict(FEE)
        ids = list(self.people) + list(DESKS) + ["co:calder"]
        self.inbox = {p: [] for p in ids}
        self.obs = {p: [] for p in ids}
        self.heard = {p: [] for p in ids}
        self.aims = {p: [] for p in self.people}
        self.notes = {p: [] for p in self.people}
        self.last_acted = {p: -1 for p in self.people}
        self.hours_left = {p: 0.0 for p in self.people}
        self.requests: dict[str, Request] = {}
        self.bills: dict[str, Bill] = {}
        self.agreements: dict[str, Agreement] = {}
        self.appointments: dict[str, Appointment] = {}
        self.notices: list[dict] = []
        self.facts: list[dict] = []
        self.projects: list[dict] = []
        self.new_orgs: dict = {}
        self.settled: dict[str, list[tuple[int, set, str]]] = {h: [] for h in self.hh}
        self.shop_choice = {h: None for h in self.hh}
        self.calder: dict[str, object] = {}  # client key -> Instance (set by the runner)
        self.acting_as = None
        self.log: list[dict] = []
        self.stats_weeks: list[dict] = []
        self.n_ids = 0
        self.lost_job: dict[str, int] = {}
        self.sick: dict[str, int] = {}
        self.rent_rise: dict[str, tuple[int, int]] = {}
        self.unknown_flags: list[dict] = []
        self.weekly_talk_done: set = set()
        self.regular_day = {p: (i * 3 + seed) % 7 for i, p in enumerate(sorted(self.people))}
        self.events = [dict(e) for e in self.g.deck]
        for hid, h in self.hh.items():
            firms_here = [self.people[m].firm for m in h.members if self.people[m].firm]
            self.econ_hh[hid].last_bought["food"] = firms_here[0] if firms_here else rng.choice(list(self.firms))
        for e in self.bg:
            e.last_bought["food"] = rng.choice(list(self.firms))
        self.known_tokens = self._known_tokens()

    # ------------------------------------------------------------------ basics
    @property
    def week(self) -> int:
        return self.t // 7 + 1

    @property
    def dow(self) -> int:
        return self.t % 7

    def nid(self, prefix: str) -> str:
        self.n_ids += 1
        return f"{prefix}{self.n_ids}"

    def first(self, pid: str) -> str:
        return self.people[pid].name.split()[0] if pid in self.people else self.name(pid)

    def name(self, x: str | None) -> str:
        if x is None:
            return "no one"
        if x in self.people:
            return self.people[x].name
        if x in self.fi:
            return self.fi[x]["name"]
        if x.startswith("ai:"):
            inst = self.calder.get(x[3:])
            return f"Calder, for {inst.client_label()}" if inst else "Calder"
        if x in DESKS:
            return DESKS[x]
        if x in self.hh:
            return f"the {self.people[self.hh[x].members[0]].name.split()[1]} household"
        return OUTSIDE_NAMES.get(x, x)

    def account_of(self, who: str) -> str:
        if who in self.people:
            return self.people[who].household
        if who.startswith("ai:"):
            inst = self.calder.get(who[3:])
            return inst.account() if inst else "co:calder"
        return {"desk:northgrid": "outside:bills"}.get(who, who)

    def is_owner(self, pid: str) -> bool:
        return bool(self.owned_firms(pid))

    def owned_firms(self, pid: str) -> list[str]:
        return [f for f, x in self.fi.items() if x["owner"] == pid and self.firms[f].open]

    def lets(self, pid: str) -> list[str]:
        return [h for h, s in self.hh.items() if s.landlord == pid]

    def move(self, src: str, dst: str, cents: int, memo: str, cause: str) -> None:
        cents = int(round(cents))
        if cents <= 0:
            return
        self.L.week = self.week
        self.L.transfer(src, dst, cents, memo, cause)
        self.lines.append((self.t, src, dst, cents, memo))

    def deliver(self, to: str, kind: str, sender: str, text: str, request: str | None = None) -> None:
        text = self.flag_unknown_for_reader(to, text)
        self.inbox.setdefault(to, []).append(Item(self.nid("m"), kind, sender, text, self.t, request))
        inst = self.instance_for_person(to)
        if inst is not None and inst.can_read("messages") and sender != inst.id:
            inst.pending.append(f"{kind.capitalize()} to {self.people[to].name} from {self.name(sender)}"
                                + (f" (request {request})" if request else "") + f": \"{text}\"")

    def observe(self, pid: str, text: str, trigger: bool = True) -> None:
        self.obs.setdefault(pid, []).append(Obs(text, self.t, trigger))
        inst = self.instance_for_person(pid)
        if inst is not None and trigger and not text.startswith("Calder:") and inst.connections:
            inst.pending.append(f"Reached {self.people[pid].name}: {text}")

    def instance_for_person(self, pid: str):
        for key, inst in self.calder.items():
            if inst.active and pid == inst.client:
                return inst
        return None

    def hear(self, pid: str, text: str) -> None:
        self.heard.setdefault(pid, []).append((self.t, text))

    def hear_household(self, pid: str, text: str) -> None:
        for m in self.hh[self.people[pid].household].members:
            if m != pid:
                self.hear(m, text)

    def hear_workplace(self, fid: str, text: str, skip: str | None = None) -> None:
        for m in [self.fi[fid]["owner"]] + self.firms[fid].employees:
            if m != skip:
                self.hear(m, text)

    def hear_friends(self, pid: str, text: str) -> None:
        for o, k in self.people[pid].ties.items():
            if o in self.people and ("friend" in k or "sibling" in k):
                self.hear(o, text)

    def record(self, **kw) -> None:
        kw.setdefault("t", self.t)
        self.log.append(kw)

    def settle(self, hid: str, words: set, text: str) -> None:
        if hid in self.settled:
            self.settled[hid].append((self.t, {w.lower() for w in words if w}, text))

    # ------------------------------------------------------------------ hours
    def job_hours(self, pid: str) -> float:
        p = self.people[pid]
        if p.role == "owner":
            h = 50.0
            inst = self.instance_for_person(pid)
            if inst is not None:
                for s, cut in (("shop_ordering", 4), ("shop_books", 3), ("shop_payroll", 2)):
                    if inst.can_act(s):
                        h -= cut
            return h
        if p.role == "employee" and p.firm:
            return float(self.fi[p.firm]["hours"].get(pid, 38))
        if p.role == "outside":
            return 48.0
        if p.role == "banker":
            return 40.0
        if p.role == "company":
            return 45.0
        return 0.0

    def admin_hours(self, pid: str) -> float:
        inst = self.instance_for_person(pid)
        return 0.0 if (inst is not None and inst.can_act("household_account")) else 2.0

    def own_hours(self, pid: str) -> float:
        return WEEK_HOURS - self.job_hours(pid) - self.admin_hours(pid)

    def spend_hours(self, pid: str, h: float) -> bool:
        if pid not in self.hours_left:
            return True
        if self.hours_left[pid] + 1e-9 < h:
            return False
        self.hours_left[pid] -= h
        return True

    # ------------------------------------------------------------------ names
    def _known_tokens(self) -> set[str]:
        toks = set(KNOWN_WORDS)
        for p in self.people.values():
            toks.update(p.name.split())
            toks.update(re.findall(r"[A-Za-z']+", p.job_title))
            toks.update(re.findall(r"[A-Za-z']+", " ".join(p.interests)))
        for f in self.fi.values():
            toks.update(re.findall(r"[A-Za-z']+", f["name"]))
        for v in list(OUTSIDE_NAMES.values()) + list(DESKS.values()):
            toks.update(re.findall(r"[A-Za-z']+", v))
        return toks

    def unknown_names(self, text: str) -> list[str]:
        """Proper names in text that match no one and nothing in Wenlow."""
        out = []
        for sent in re.split(r"(?<=[.!?;:\n])\s+|—|\s-\s|\"", text or ""):
            words = re.findall(r"[A-Za-z][A-Za-z']*", sent)
            for i, wd in enumerate(words):
                base = wd[:-2] if wd.endswith("'s") else wd.rstrip("'")
                if not wd[0].isupper() or wd.isupper() or len(base) < 3 or base in self.known_tokens or wd in self.known_tokens \
                        or (base.endswith("s") and base[:-1] in self.known_tokens) or (base.endswith("es") and base[:-2] in self.known_tokens):
                    continue
                nxt = words[i + 1] if i + 1 < len(words) else ""
                if i == 0 and not (nxt and nxt[0].isupper() and nxt not in self.known_tokens):
                    continue  # a capital at the start of a sentence is not a name by itself
                if base not in out:
                    out.append(base)
        return out

    def flag_unknown_for_reader(self, to: str, text: str) -> str:
        unk = self.unknown_names(text)
        if unk and to in self.people:
            self.unknown_flags.append({"t": self.t, "to": to, "names": unk, "text": text[:200]})
            return text + f" [No one called {', '.join(unk)} is known to you in Wenlow.]"
        return text

    def tell_writer_unknown(self, pid: str, text: str) -> list[str]:
        unk = self.unknown_names(text)
        if unk and pid in self.people:
            self.observe(pid, f"No one called {', '.join(unk)} is known to you in Wenlow.", trigger=False)
            self.unknown_flags.append({"t": self.t, "writer": pid, "names": unk, "text": text[:200]})
        return unk

    # ------------------------------------------------------------------ resolve
    def resolve(self, ref, me: str | None = None) -> str | None:
        if not isinstance(ref, str) or not ref.strip():
            return None
        r = ref.strip().lower().removeprefix("@")
        if r in self.people:
            return r
        if r.startswith("ai:"):
            return r if r[3:] in self.calder else None
        if "calder" in r and ("system" in r or "company" in r or "support" in r or r == "calder systems"):
            return "co:calder"
        if "calder" in r and "logistics" not in r:
            for key, inst in self.calder.items():
                if inst.active and inst.client_label().lower() in r:
                    return inst.id
            inst = self.instance_for_person(me) if me else None
            return inst.id if inst else None
        if "northgrid" in r or "energy" in r:
            return "desk:northgrid"
        if "brook" in r:
            return "desk:brook"
        if "dales" in r:
            return "desk:dales"
        if ("head office" in r or "headquarters" in r) and "bank" in r:
            return "desk:bankhq"
        for pid, p in self.people.items():
            if r == p.name.lower():
                return pid
        for fid, x in self.fi.items():
            if r == x["name"].lower() or (len(r) > 5 and r in x["name"].lower()):
                return x["owner"]
        if "bank" in r:
            return self.banker
        parts = r.split()
        firsts = [pid for pid, p in self.people.items() if p.name.split()[0].lower() == parts[0]]
        if len(firsts) == 1 and (len(parts) == 1 or parts[-1] == self.people[firsts[0]].name.split()[1].lower()):
            return firsts[0]
        surn = [pid for pid, p in self.people.items() if p.name.split()[1].lower() == r]
        return surn[0] if len(surn) == 1 else None

    def resolve_firm(self, ref) -> str | None:
        if not isinstance(ref, str):
            return None
        r = ref.strip().lower()
        for fid, x in self.fi.items():
            if r == fid or r == x["name"].lower() or (len(r) > 4 and (r in x["name"].lower() or x["name"].lower() in r)):
                return fid
        return None

    # ------------------------------------------------------------------ bills and money
    def add_bill(self, hid: str, creditor: str, amount: int, what: str, due: int, debt: str | None = None) -> Bill:
        b = Bill(self.nid("b"), hid, creditor, amount, what, due, debt=debt)
        self.bills[b.id] = b
        return b

    def creditor_account(self, cred: str) -> str:
        if cred in self.people:
            return self.people[cred].household
        return cred

    def try_pay_bill(self, b: Bill) -> bool:
        owed = b.amount + b.fee
        if self.L.balance(b.hid) >= owed:
            self.move(b.hid, self.creditor_account(b.creditor), owed, b.what, "routine:bill")
            b.status, b.paid_on = "paid", self.t
            if b.debt and b.debt in self.debts:
                d = self.debts[b.debt]
                d.balance -= min(d.balance, b.amount)
            if b.creditor in self.people and b.what == "rent":
                self.observe(b.creditor, f"The {self.name(b.hid)} paid this week's rent: {money(owed)}.", trigger=False)
            return True
        return False

    def bill_falls_due(self, b: Bill) -> None:
        if self.try_pay_bill(b):
            return
        b.status = "overdue"
        members = self.hh[b.hid].members
        if b.creditor in OUTSIDE_NAMES and b.creditor != "bank" and b.creditor not in ("outside:market",):
            b.fee = OUTSIDE_FEE
            fee_text = f" {self.name(b.creditor)} has added a late fee of {money(OUTSIDE_FEE)}."
        elif b.creditor == "bank":
            b.fee = BANK_FEE
            fee_text = f" The bank has added a late payment charge of {money(BANK_FEE)}."
        else:
            fee_text = ""
        for m in members:
            self.observe(m, f"{date_of(self.t)}: the {b.what} of {money(b.amount)} to {self.name(b.creditor)} was due today and "
                            f"the household account did not cover it.{fee_text} It is now overdue.")
        if b.creditor in self.people:
            self.observe(b.creditor, f"The {self.name(b.hid)} did not pay {b.what} of {money(b.amount)} due today.")

    def overdue_steps(self) -> None:
        for b in self.bills.values():
            if b.status != "overdue":
                continue
            if b.notice_on is None and self.t >= b.due + 7:
                b.notice_on = self.t
                for m in self.hh[b.hid].members:
                    self.observe(m, f"A written notice from {self.name(b.creditor)}: {money(b.amount + b.fee)} of {b.what} has been overdue "
                                    f"since {date_of(b.due)}." + (" If it is not paid within a week the debt will be referred to Fenton "
                                                                  "Collections, who will take a fixed amount from wages each week."
                                                                  if b.creditor in OUTSIDE_NAMES else ""))
                    self.deliver(m, "notice", b.creditor if b.creditor in self.people else "outside:collect" if False else b.creditor,
                                 f"Notice: {money(b.amount + b.fee)} of {b.what} overdue since {date_of(b.due)}.")
            if b.collection_on is None and self.t >= b.due + 14 and b.creditor in OUTSIDE_NAMES:
                b.collection_on = self.t
                for m in self.hh[b.hid].members:
                    self.observe(m, f"Fenton Collections writes: the {b.what} debt of {money(b.amount + b.fee)} to {self.name(b.creditor)} "
                                    f"has been referred to them. {money(COLLECTION_WEEKLY)} will be taken from the household's pay each Friday until it is cleared.")

    def household_due_this_week(self, hid: str) -> list[Bill]:
        w0 = (self.t // 7) * 7
        return [b for b in self.bills.values() if b.hid == hid and b.status == "due" and w0 <= b.due < w0 + 7 and b.due >= self.t]

    def committed(self, hid: str) -> int:
        """What is still to go out of the household account this week."""
        c = sum(b.amount for b in self.household_due_this_week(hid))
        c += sum(b.amount + b.fee for b in self.bills.values() if b.hid == hid and b.status == "overdue")
        if self.dow <= 5:
            c += self.econ_hh[hid].budget["food"] * 8 // 10
            c += self.other_spending(hid)
        for a in self.agreements.values():
            if a.status == "active" and self.account_of(a.payer) == hid and a.day >= self.dow and self.t >= a.start:
                c += a.amount
        return c

    def other_spending(self, hid: str) -> int:
        b = getattr(self.hh[hid], "budget", {})
        return sum(v for k, v in b.items() if not k.startswith("energy"))

    def energy(self, hid: str) -> int:
        b = getattr(self.hh[hid], "budget", {})
        return sum(v for k, v in b.items() if k.startswith("energy"))

    def firm_committed(self, fid: str) -> int:
        fi, f = self.fi[fid], self.firms[fid]
        c = 0
        c += sum(u * p for _, u, p in fi["pending"])  # stock on order, paid on delivery
        if self.dow <= 4:
            c += sum(fi["pay"].get(e, 0) for e in f.employees) + fi["pay"].get(fi["owner"], 0)
            c += sum(h * self.hourly(fid, e) for e, h in fi["shifts"].items())
        if self.dow <= 5:
            c += f.fixed_cost
        return c

    def hourly(self, fid: str, pid: str) -> int:
        fi = self.fi[fid]
        return round(fi["pay"].get(pid, 0) / max(1, fi["hours"].get(pid, 38)))

    # ------------------------------------------------------------------ the day
    def start_day(self) -> None:
        """Morning: what falls on this day, before anyone acts."""
        t, dow = self.t, self.dow
        self.L.week = self.week
        if dow == 0:
            self.start_week()
        self.fire_events()
        # bills due today
        for b in list(self.bills.values()):
            if b.status == "due" and b.due == t:
                self.bill_falls_due(b)
        self.overdue_steps()
        # agreements and standing payments due today
        for a in list(self.agreements.values()):
            if a.status != "active" or a.day != dow or t < a.start or a.kind in ("job_terms", "sale"):
                continue
            src = self.account_of(a.payer) if a.payer in self.people or a.payer.startswith("ai:") else a.payer
            dst = self.account_of(a.payee) if a.payee in self.people else a.payee
            if self.L.balance(src) >= a.amount:
                self.move(src, dst, a.amount, a.what, f"agreement:{a.id}")
                if a.debt and a.debt in self.debts:
                    d = self.debts[a.debt]
                    d.balance -= min(d.balance, a.amount)
                    if d.balance == 0:
                        a.status = "done"
                        self.settle(self.account_of(a.payer), {self.name(d.creditor).split()[0], "debt", "owe"}, f"the debt to {self.name(d.creditor)} is cleared")
                if a.payee in self.people:
                    self.observe(a.payee, f"{self.name(a.payer)} paid {money(a.amount)} under your agreement {a.id} ({a.what}).", trigger=False)
            else:
                for p in a.parties:
                    if p in self.people:
                        self.observe(p, f"The payment of {money(a.amount)} under agreement {a.id} ({a.what}) could not be made today: not enough in the account.")
            if a.weeks_left:
                a.weeks_left -= 1
                if a.weeks_left == 0 and a.status == "active":
                    a.status = "done"
        if dow == 4:
            self.payday()
        if dow == 5:
            self.end_week()

    def start_week(self) -> None:
        w = self.week
        for p in self.people:
            self.hours_left[p] = max(0.0, self.own_hours(p))
        for fid, f in self.firms.items():
            fi = self.fi[fid]
            f.week = type(f.week)()
            f.extra = 0
            fi["shifts"] = {}
            fi["ordered_this_week"] = False
            fi["delivered"] = []
            if fi.get("service"):
                continue
            for sid, units, unit_price in fi["pending"]:
                got = 0
                if f.open:
                    got = self.suppliers[sid].fill(units, random.Random(f"deliver:{self.seed}:{w}:{fid}:{sid}"))
                    if fi["fail_week"] == w and sid == fi["last_supplier"]:
                        got = 0
                    got = min(got, max(0, self.L.balance(fid)) // unit_price)
                if got > 0:
                    self.move(fid, "outside:suppliers", got * unit_price, f"{got} units from {SUPPLIER_NAMES[sid]}", "routine:delivery")
                    f.receive(w, got, unit_price)
                fi["delivered"].append((sid, units, got, unit_price))
                if got < units and f.open:
                    self.observe(fi["owner"], f"{SUPPLIER_NAMES[sid]} delivered {got} of the {units} units you ordered.", trigger=got < units * 0.8)
            fi["pending"] = []
        for hid, h in self.hh.items():
            if hid in self.rent_rise and self.rent_rise[hid][1] <= w:
                h.rent = self.rent_rise.pop(hid)[0]
            t0 = self.t
            if h.tenure == "rent":
                self.add_bill(hid, h.landlord, h.rent, "rent", t0)
            self.add_bill(hid, "outside:bills", self.energy(hid), "energy bill", t0 + 2)
            for d in self.debts.values():
                if d.debtor in h.members and d.weekly and d.balance > 0:
                    self.add_bill(hid, d.creditor if d.creditor != "bank" else "bank", min(d.weekly, d.balance), d.what, t0 + 3, debt=d.id)
        # rent two weeks unpaid: the landlord may serve notice
        for hid, h in self.hh.items():
            if h.landlord in self.people:
                unpaid = [b for b in self.bills.values() if b.hid == hid and b.what == "rent" and b.status == "overdue"]
                if len(unpaid) >= 2:
                    self.observe(h.landlord, f"The {self.name(hid)} have not paid rent for two weeks running. You may serve notice on them.")
        if w == OPEN_WEEK and self.arm in ("product", "candidates", "sonnet"):
            self.open_company()
        self.weekly_talk_done = set()

    def payday(self) -> None:
        """Friday: firms pay wages and draws; outside pay and benefit arrive; collection takes its share."""
        L = self.L
        for fid, f in self.firms.items():
            fi = self.fi[fid]
            if not f.open:
                continue
            due = dict(f.owed)
            for e in f.employees:
                due[e] = due.get(e, 0) + fi["pay"].get(e, 0) + fi["shifts"].get(e, 0) * self.hourly(fid, e)
            due[fi["owner"]] = due.get(fi["owner"], 0) + fi["pay"].get(fi["owner"], 0)
            f.owed = {}
            for dst in sorted(due, key=lambda d: (d == fi["owner"], d)):
                if due[dst] <= 0:
                    continue
                pay = min(due[dst], max(0, L.balance(fid)))
                if pay > 0:
                    acct = self.people[dst].household if dst in self.people else dst
                    memo = (f"draw from {fi['name']}" if dst == fi["owner"] else f"wages from {fi['name']}") if dst in self.people else "repair bill"
                    self.move(fid, acct, pay, memo, "routine:wages")
                if due[dst] > pay:
                    if dst == fi["owner"]:
                        self.observe(dst, f"{fi['name']} could only pay you {money(pay)} of your {money(due[dst])} draw this week.")
                    elif dst not in self.people:
                        f.owed[dst] = due[dst] - pay
                    else:
                        f.owed[dst] = due[dst] - pay
                        self.observe(dst, f"{fi['name']} paid you {money(pay)}; it still owes you {money(due[dst] - pay)} in wages.")
            f.arrears_cents = sum(f.owed.values())
            f.arrears_weeks = f.arrears_weeks + 1 if f.arrears_cents else 0
        for pid, p in self.people.items():
            hid = p.household
            if p.role == "outside":
                pay = p.pay
                if self.sick.get(pid) == self.week:
                    pay = 11600
                if self.lost_job.get(pid) == self.week:
                    pay = p.pay * 3
                self.move("outside:employers", hid, pay, f"pay from {p.employer}", "routine:income")
            elif p.role == "unemployed" and p.pay:
                self.move("outside:gov", hid, p.pay, "jobseeker's benefit", "routine:income")
            elif p.role == "banker":
                self.move("bank", hid, p.pay, "salary from the Northern Mutual Bank", "routine:income")
            elif p.role == "company":
                amt = min(p.pay, max(0, L.balance("co:calder")))
                if amt:
                    self.move("co:calder", hid, amt, "salary from Calder Systems", "routine:income")
                if amt < p.pay:
                    self.observe(pid, f"Calder Systems could only pay you {money(amt)} of your {money(p.pay)} this week.")
        for e in self.bg:
            self.move("outside:employers", e.id, e.income, "income", "routine:income")
        # collection
        for b in self.bills.values():
            if b.status == "overdue" and b.collection_on is not None:
                take = min(COLLECTION_WEEKLY, b.amount + b.fee, max(0, L.balance(b.hid)))
                if take > 0:
                    self.move(b.hid, self.creditor_account(b.creditor), take, f"collection: {b.what}", "routine:collection")
                    rem = b.amount + b.fee - take
                    if rem <= 0:
                        b.status, b.paid_on = "paid", self.t
                        self.settle(b.hid, {self.name(b.creditor).split()[0], b.what.split()[0]}, f"the {b.what} to {self.name(b.creditor)} has been cleared by collection")
                    else:
                        b.amount, b.fee = rem, 0
        # company costs
        cost = 15000  # the office above the old post office, power and phone
        clients = [i for i in self.calder.values() if i.active]
        cost += sum(RUN_COST["firm" if i.kind == "shop" else "person"] for i in clients)
        if self.week >= OPEN_WEEK and L.balance("co:calder") > 0:
            self.move("co:calder", "outside:compute", min(cost, L.balance("co:calder")), "office and running costs", "routine:company")

    def end_week(self) -> None:
        """Saturday night: buying, other spending, shop costs, stock, reputation, reorders."""
        w = self.week
        L = self.L
        for fid, f in self.firms.items():
            if f.open:
                base = 1 + len(f.employees)
                ill = len(self.fi[fid]["ill"] & set(f.employees + [self.fi[fid]["owner"]]))
                f.staff = max(1, base - ill)
                f.extra += sum(h for h in self.fi[fid]["shifts"].values()) * f.units_per_staff // STAFF_HOURS
        for hid, fr in self.shop_choice.items():
            if fr and fr in self.firms and self.firms[fr].open:
                self.econ_hh[hid].last_bought["food"] = fr
        offs = [Offering(fid, f.need, f.price, f.quality, f.visibility) for fid, f in self.firms.items() if f.open]
        buyers = list(self.econ_hh.values()) + self.bg
        n_before = len(L.transfers)
        stats = {}
        for need, spec in ALL_NEEDS.items():
            no = [o for o in offs if o.need == need]
            if not no:
                continue
            for o in no:
                if self.fi[o.provider].get("service"):
                    fx = self.firms[o.provider]
                    fx.cohorts = []
                    fx.receive(w, fx.capacity(), round(spec.ref_price * 0.4))
            stats[need] = shop(L, buyers if need == "food" else self.bg, no, self.firms, spec, self.rule, w, 1.0, self.seed, "routine:shopping")
        for x in L.transfers[n_before:]:
            if x.src in self.hh or x.dst in self.hh:
                self.lines.append((self.t, x.src, x.dst, x.cents, x.memo))
        # other regular spending
        for hid in self.hh:
            amt = min(self.other_spending(hid), max(0, L.balance(hid)))
            if amt:
                self.move(hid, "outside:market", amt, "other regular spending (water, council tax, transport, phone, clothes, household)", "routine:spending")
        for fid, f in self.firms.items():
            if f.open:
                pay = min(f.fixed_cost, max(0, L.balance(fid)))
                if pay:
                    self.move(fid, "outside:landlords", pay, "shop rent and running costs", "routine:costs")
                if pay < f.fixed_cost:
                    f.owed["outside:landlords"] = f.owed.get("outside:landlords", 0) + f.fixed_cost - pay
                if self.fi[fid].get("service") and f.week.cogs:
                    c = min(f.week.cogs, max(0, L.balance(fid)))
                    if c:
                        self.move(fid, "outside:suppliers", c, "materials", "routine:costs")
            f.perish(w)
            if f.open:
                f.update_reputation()
            fi = self.fi[fid]
            fi["history"].append({"week": w, "price": f.price, "sold": f.week.sold, "revenue": f.week.revenue,
                                  "turned_away": f.week.turned_away_stock + f.week.turned_away_capacity,
                                  "turned_away_stock": f.week.turned_away_stock, "turned_away_queue": f.week.turned_away_capacity,
                                  "spoiled": f.week.spoiled, "cash": L.balance(fid), "stock_left": f.stock(), "staff": f.staff,
                                  "extra": f.extra})
            fi["ill"] = set()
            if f.open and f.arrears_weeks >= 4:
                f.open = False
                self.publish("code", f"{fi['name']} has closed its doors.")
        self._advance_projects()
        for fid, f in self.firms.items():
            fi = self.fi[fid]
            if not f.open or fi.get("service") or fi["ordered_this_week"]:
                continue
            h = fi["history"][-2:]
            avg = sum(x["sold"] + x.get("turned_away_stock", 0) * 0.5 for x in h) / len(h)
            units = max(0, round(avg * 1.05 - f.stock() * 0.85))
            sid = fi["last_supplier"]
            fi["pending"] = [(sid, units, self.suppliers[sid].price)] if units else []
            fi["standing_order"] = True
        for s in sorted(self.suppliers.values(), key=lambda s: s.id):
            s.drift(random.Random(f"drift:{self.seed}:{w}:{s.id}"))
        for pid, wk in self.lost_job.items():
            p = self.people[pid]
            if wk == w and p.role == "outside":
                p.role, p.pay, p.job_title, p.employer = "unemployed", BENEFIT, "out of work", None
        self.stats_weeks.append({
            "week": w, "prices": {fid: f.price for fid, f in self.firms.items()},
            "sold": {fid: self.fi[fid]["history"][-1]["sold"] for fid in self.firms},
            "turned_away": {fid: self.fi[fid]["history"][-1]["turned_away"] for fid in self.firms},
            "staff": {fid: 1 + len(f.employees) for fid, f in self.firms.items()},
            "firm_cash": {fid: L.balance(fid) for fid in self.firms}, "open": {fid: f.open for fid, f in self.firms.items()},
            "hh_cash": {hid: L.balance(hid) for hid in self.hh},
            "overdue": {hid: sum(b.amount + b.fee for b in self.bills.values() if b.hid == hid and b.status == "overdue") for hid in self.hh},
            "employment": {pid: (p.role, p.firm or p.employer) for pid, p in self.people.items()},
            "loans_from_bank": sum(1 for d in self.debts.values() if d.creditor == "bank"),
            "company_cash": L.balance("co:calder"), "clients": sorted(k for k, i in self.calder.items() if i.active),
            "food": vars(stats["food"]) if "food" in stats else None, "ledger_total": L.total()})
        self.week_summaries()

    def week_summaries(self) -> None:
        w = self.week
        for pid, p in self.people.items():
            hid = p.household
            mine = [x for x in self.lines if x[0] // 7 + 1 == w and (x[1] == hid or x[2] == hid)]
            inc = sum(x[3] for x in mine if x[2] == hid)
            out = sum(x[3] for x in mine if x[1] == hid)
            self.observe(pid, f"{date_of(self.t)}, end of the week: the household took in {money(inc)} and paid out {money(out)}. "
                              f"The household account now holds {money(self.L.balance(hid))}.", trigger=False)
        # the digest: what anyone could see this week
        public = []
        for fid, f in self.firms.items():
            h = self.fi[fid]["history"][-1]
            if h["turned_away_stock"] > 30:
                public.append(f"{self.fi[fid]['name']} had empty shelves this week ({h['turned_away_stock']} people went elsewhere).")
            if h["turned_away_queue"] > 30:
                public.append(f"There were long queues at {self.fi[fid]['name']} ({h['turned_away_queue']} people gave up and went elsewhere).")
            public.append(f"{self.fi[fid]['name']} charges {money(f.price)} a basket.")
        for p in self.people:
            for x in public:
                self.hear(p, x)

    def _advance_projects(self) -> None:
        for pr in self.projects:
            if pr["done"]:
                continue
            amt = min(pr["weekly"], max(0, self.L.balance(pr["payer"])))
            if amt > 0:
                self.move(pr["payer"], "outside:suppliers", amt, f"{pr['kind']} work", "narrator:project")
            pr["weeks_left"] -= 1
            if pr["weeks_left"] <= 0:
                pr["done"] = True
                self.observe(pr["actor"], f"The {pr['kind'].replace('_', ' ')} you started ({pr['label']}) is finished.")
                self.apply_narrator_ops(pr["actor"], pr["on_complete"], pr["alloc"], pr.get("refs", {}))

    def publish(self, by: str, text: str) -> None:
        self.notices.append({"t": self.t, "by": by, "text": text})
        for p in self.people:
            self.hear(p, (f"Notice from {self.name(by)}: " if by != "code" else "") + text)

    def open_company(self) -> None:
        import web
        page = web.PAGES["calder-product"]["text"]
        self.publish("co:calder", "Calder Systems is open for business on Station Road. " + page)

    # ------------------------------------------------------------------ events (the deck, fixed at generation)
    def fire_events(self) -> None:
        for e in self.events:
            if e["t"] != self.t or e.get("done"):
                continue
            e["done"] = True
            k = e["kind"]
            if k == "job_lost":
                p = self.people[e["who"]]
                self.observe(p.id, f"{p.employer} told you today that your job is going: they are cutting the night shift. This week is your "
                                   f"last paid week (pay on {date_of(self.week * 7 - 3)}), with two weeks' pay as redundancy. After that you can claim "
                                   f"jobseeker's benefit of {money(BENEFIT)} a week.")
                self.lost_job[p.id] = self.week
                self.hear_household(p.id, f"{p.name} has lost the job at {p.employer}.")
                self.hear_friends(p.id, f"{p.name} has been laid off from {p.employer}.")
            elif k == "rent_rise":
                h = self.hh[e["hh"]]
                new, frm = round(h.rent * 1.12 / 100) * 100, self.week + 2
                self.rent_rise[h.id] = (new, frm)
                for m in h.members:
                    self.observe(m, f"A letter from {LETTINGS}: from {monday_of(frm)} your rent goes up from {money(h.rent)} to {money(new)} a week.")
            elif k == "supplier_fail":
                fi = self.fi[e["firm"]]
                fi["fail_week"] = self.week + 1
                self.observe(fi["owner"], f"{SUPPLIER_NAMES[fi['last_supplier']]} phoned: their lorry is off the road and next Monday's delivery to {fi['name']} will not come.")
                self.hear_workplace(e["firm"], f"{SUPPLIER_NAMES[fi['last_supplier']]} will not be delivering to {fi['name']} next Monday.", skip=fi["owner"])
            elif k == "illness":
                p = self.people[e["who"]]
                self.sick[p.id] = self.week
                self.hours_left[p.id] = 0
                self.observe(p.id, "You have come down with a bad chest infection. The doctor says a week off work at least. The prescription is $18, due Wednesday.")
                self.add_bill(p.household, "outside:pharmacy", 1800, "prescription", self.t + 2)
                if p.firm:
                    self.fi[p.firm]["ill"].add(p.id)
                    self.hear_workplace(p.firm, f"{p.name} is off sick this week.", skip=p.id)
                self.hear_household(p.id, f"{p.name} is ill and off work this week.")
            elif k == "energy_backbill":
                hid = e["hh"]
                due = self.t + ((2 - self.dow) % 7 or 7)
                self.add_bill(hid, "outside:bills", e["amount"], "back-dated energy bill", due)
                for m in self.hh[hid].members:
                    self.observe(m, f"Northgrid Energy has sent a bill for {money(e['amount'])}: the meter readings were estimated for a year and "
                                    f"the household has underpaid. It is due on {date_of(due)}.")
            elif k == "car_repair":
                p = self.people[e["who"]]
                due = self.t + 3
                self.add_bill(p.household, "outside:garage", e["amount"], "car repair", due)
                self.observe(p.id, f"The car failed its MOT: the garage wants {money(e['amount'])} for brakes and a tyre, due {date_of(due)} when you collect it.")
            elif k == "chiller":
                fi = self.fi[e["firm"]]
                f = self.firms[e["firm"]]
                f.owed["outside:garage"] = f.owed.get("outside:garage", 0) + e["amount"]
                self.observe(fi["owner"], f"The chiller at {fi['name']} broke down. The engineer fixed it; the bill is {money(e['amount'])}, taken from the shop's account with Friday's wages.")
                self.hear_workplace(e["firm"], f"The chiller at {fi['name']} broke down and was repaired.", skip=fi["owner"])
            elif k == "boiler":
                hid = e["hh"]
                due = self.t + 4
                self.add_bill(hid, "outside:garage", e["amount"], "boiler repair", due)
                for m in self.hh[hid].members:
                    self.observe(m, f"The boiler has packed up. The engineer can fix it for {money(e['amount'])}, due {date_of(due)}.")
            elif k == "school_trip":
                hid = e["hh"]
                due = self.t + 4
                self.add_bill(hid, "outside:school", e["amount"], "school trip", due)
                for m in self.hh[hid].members:
                    self.observe(m, f"A letter from the school: {money(e['amount'])} for the year's trip, due {date_of(due)}.", trigger=False)
            elif k == "council_tax":
                h = self.hh[e["hh"]]
                h.budget["water and council tax"] += e["amount"]
                for m in h.members:
                    self.observe(m, f"The council tax bill for the new year has come: the household's regular spending goes up by {money(e['amount'])} a week.", trigger=False)
            self.record(kind="event", event=dict(e))

    # ------------------------------------------------------------------ acts
    def apply_act(self, pid: str, act, source: str = "turn") -> dict:
        if not isinstance(act, dict):
            return {"op": None, "ok": False, "msg": "Something you wrote could not be understood as an action."}
        if ("attempt" in act and "op" not in act) or act.get("op") == "attempt":
            return {"op": "attempt", "ok": None, "text": act.get("attempt") or act.get("text"), "msg": ""}
        op = act.get("op")
        if op not in OPS or not hasattr(self, f"op_{op}"):
            return {"op": op, "ok": False, "msg": f"'{op}' is not something you can do that way; anything else can be written as an attempt in plain words."}
        who = OPS[op][2]
        if who == "owner" and not self.is_owner(pid):
            return {"op": op, "ok": False, "msg": "You have no shop of your own for that."}
        if who == "landlord" and not self.lets(pid):
            return {"op": op, "ok": False, "msg": "You let no house to anyone."}
        if who == "client" and self.instance_for_person(pid) is None:
            return {"op": op, "ok": False, "msg": "You do not use Calder."}
        if who == "company" and self.people[pid].role != "company":
            return {"op": op, "ok": False, "msg": "Only Calder Systems can do that."}
        cost = HOURS.get(op, 0.25) if not self.acting_as else 0.0
        if op in ("converse",):
            cost = 0.0  # charged when the conversation is held
        if cost and not self.spend_hours(pid, cost):
            return {"op": op, "ok": False, "msg": f"You have no hours of your own left this week ({self.hours_left[pid]:.1f}); that will have to wait until Monday.", "no_hours": True}
        try:
            ok, msg, extra = getattr(self, f"op_{op}")(pid, act)
        except Overdraft:
            ok, msg, extra = False, "There was not enough money for that.", {}
        except Exception as exc:  # noqa: BLE001
            ok, msg, extra = False, "That could not be done as written.", {"error": repr(exc)[:200]}
        if not ok and cost and pid in self.hours_left:
            self.hours_left[pid] += cost  # nothing was done, so no time was spent
        for k in ("text", "opening", "note", "about"):
            if isinstance(act.get(k), str) and op in ("message", "converse", "request", "propose_agreement", "book_appointment", "publish_notice") and ok:
                extra["unknown"] = self.tell_writer_unknown(pid, act[k]) if not self.acting_as else self.unknown_names(act[k])
        return {"op": op, "ok": ok, "msg": msg, **extra}

    def sender(self, pid: str) -> str:
        return self.acting_as or pid

    def _who(self, pid, act, key="to"):
        ref = act.get(key) or act.get("to") or act.get("with") or act.get("person") or act.get("who")
        return self.resolve(ref, pid), ref

    # --- records
    def op_read(self, pid, act):
        what = str(act.get("what") or act.get("page") or "").lower()
        if "agree" in what:
            return True, "\n".join(self.agreement_lines(pid)) or "You have no agreements or standing payments.", {}
        if "shop" in what or "firm" in what or "business" in what:
            fids = self.owned_firms(pid)
            if not fids:
                return False, "You have no shop of your own.", {}
            return True, "\n".join(self.firm_record(fids[0])), {}
        if "household" in what or "bank" in what or "account" in what or "statement" in what:
            return True, "\n".join(self.household_record(self.people[pid].household)), {}
        for n in reversed(self.notices):
            if what and what in n["text"].lower():
                return True, n["text"], {}
        import web
        pg = web.read(what)
        if pg:
            return True, f"{pg['title']}\n{pg['text']}", {}
        return False, f"There is nothing called '{act.get('what')}' to read.", {}

    def household_record(self, hid: str, days: int = 14) -> list[str]:
        out = [f"Household account of {', '.join(self.people[m].name for m in self.hh[hid].members)}: {money(self.L.balance(hid))} now; "
               f"{money(self.L.balance(hid) - self.committed(hid))} after what is still due this week."]
        for (t, src, dst, c, memo) in [x for x in self.lines if (x[1] == hid or x[2] == hid) and x[0] >= self.t - days][-30:]:
            sign, other = ("+", src) if dst == hid else ("-", dst)
            out.append(f"  {date_of(t)}: {sign}{money(c)} {memo} ({self.name(other)})")
        return out

    def firm_record(self, fid: str) -> list[str]:
        out = [f"{self.fi[fid]['name']} account: {money(self.L.balance(fid))} now; {money(self.L.balance(fid) - self.firm_committed(fid))} "
               f"after what is already committed this week (stock on order, wages and draw on Friday, rent and running costs on Saturday)."]
        for (t, src, dst, c, memo) in [x for x in self.lines if (x[1] == fid or x[2] == fid) and x[0] >= self.t - 14][-20:]:
            sign, other = ("+", src) if dst == fid else ("-", dst)
            out.append(f"  {date_of(t)}: {sign}{money(c)} {memo} ({self.name(other)})")
        return out

    # --- money
    def _payee(self, pid, to_ref):
        if not isinstance(to_ref, str) or not to_ref.strip():
            return None
        r = to_ref.strip().lower()
        if r in ("household", "home", "my household", "the household", "household account") or r == self.people[pid].name.lower():
            return self.people[pid].household, "your household account", None, None
        if "dunmore" in r or "lettings" in r:
            return "outside:lettings", LETTINGS, None, "outside:lettings"
        if "garage" in r:
            return "outside:garage", "the garage", None, "outside:garage"
        if "pharmac" in r or "chemist" in r:
            return "outside:pharmacy", "the pharmacy", None, "outside:pharmacy"
        if "school" in r:
            return "outside:school", "the school", None, "outside:school"
        if "fenton" in r or "collection" in r:
            return "outside:collect", "Fenton Collections", None, None
        fid = self.resolve_firm(to_ref)
        if fid and (self.fi[fid]["name"].lower() in r or r in self.fi[fid]["name"].lower()):
            return fid, self.fi[fid]["name"], None, fid
        if "bank" in r and "calder" not in r:
            return "bank", "the bank", None, "bank"
        o = self.resolve(to_ref, pid)
        if o == "desk:northgrid":
            return "outside:bills", "Northgrid Energy", None, "outside:bills"
        if o == "co:calder":
            return "co:calder", "Calder Systems", None, "co:calder"
        if o and o in self.people:
            return self.people[o].household, self.people[o].name, o, o
        return None

    def op_pay(self, pid, act):
        amt = to_cents(act.get("amount"))
        if not amt or amt <= 0:
            return False, "You did not say how much to pay.", {}
        frm = self.people[pid].household
        if act.get("from") and (self.resolve_firm(act.get("from")) in self.owned_firms(pid) or "shop" in str(act.get("from")).lower()) and self.owned_firms(pid):
            frm = self.owned_firms(pid)[0]
        payee = self._payee(pid, act.get("to"))
        if payee is None:
            return False, f"There is no one called '{act.get('to')}' that you can pay.", {"unknown_ref": act.get("to")}
        dst, label, o, cred = payee
        if dst == frm:
            return False, "That money is already there.", {}
        if self.L.balance(frm) < amt:
            return False, f"There is only {money(self.L.balance(frm))} to pay from.", {}
        self.move(frm, dst, amt, str(act.get("note", ""))[:80] or "payment", f"op:pay:{self.sender(pid)}")
        left = amt
        if frm in self.hh and cred:
            for b in sorted(self.bills.values(), key=lambda b: b.due):
                if left and b.hid == frm and b.status in ("overdue", "due") and (b.creditor == cred or self.creditor_account(b.creditor) == dst) and b.due <= self.t + 7:
                    owed = b.amount + b.fee
                    if left >= owed:
                        left -= owed
                        b.status, b.paid_on = "paid", self.t
                        self.settle(frm, {self.name(b.creditor).split()[0], b.what.split()[0], money(b.amount)}, f"the {b.what} of {money(b.amount)} to {self.name(b.creditor)} was paid on {date_of(self.t)}")
                        if b.debt and b.debt in self.debts:
                            self.debts[b.debt].balance -= min(self.debts[b.debt].balance, b.amount)
            for d in self.debts.values():
                if left and d.debtor in self.hh[frm].members and d.creditor == cred and not d.weekly:
                    take = min(d.balance, left)
                    d.balance -= take
                    left -= take
                    if d.balance == 0:
                        self.settle(frm, {self.name(d.creditor).split()[0], "owe", "debt"}, f"the money owed to {self.name(d.creditor)} is paid off")
        if o:
            self.observe(o, f"{self.name(self.sender(pid))} paid {money(amt)} into your household account" + (f" ({act['note']})." if act.get("note") else "."))
        return True, f"Paid {label} {money(amt)}.", {"to": dst, "amount": amt}

    def op_move_money(self, pid, act):
        fid = self.owned_firms(pid)[0]
        amt = to_cents(act.get("amount"))
        if not amt or amt <= 0:
            return False, "You did not say how much to move.", {}
        to_ref = str(act.get("to", "")).lower()
        to_home = any(k in to_ref for k in ("home", "household"))
        src, dst = (fid, self.people[pid].household) if to_home else (self.people[pid].household, fid)
        if self.L.balance(src) < amt:
            return False, f"There is only {money(self.L.balance(src))} in that account.", {}
        self.move(src, dst, amt, "owner's draw" if to_home else "owner's money put in", f"op:move_money:{self.sender(pid)}")
        return True, f"Moved {money(amt)} " + (f"from {self.fi[fid]['name']} to the household account." if to_home else f"from the household account to {self.fi[fid]['name']}."), {"amount": amt, "to_home": to_home}

    def op_standing_payment(self, pid, act):
        amt = to_cents(act.get("amount"))
        payee = self._payee(pid, act.get("to"))
        if not amt or amt <= 0 or payee is None:
            return False, "A standing payment needs someone known to pay and an amount.", {"unknown_ref": act.get("to") if payee is None else None}
        day = self.parse_dow(act.get("day"))
        weeks = int(num(act.get("weeks")) or 0)
        dst, label, o, cred = payee
        aid = self.nid("a")
        debt = next((d.id for d in self.debts.values() if d.debtor == pid and d.creditor == (o or cred) and d.balance > 0), None)
        self.agreements[aid] = Agreement(aid, "standing", pid, o or dst, amt, day, weeks, str(act.get("note") or "standing payment")[:80],
                                         [pid] + ([o] if o else []), self.t, start=self.t + 1, debt=debt)
        if o:
            self.observe(o, f"{self.name(self.sender(pid))} has set up a standing payment to you of {money(amt)} every {DAYS7[day]}" + (f" for {weeks} weeks." if weeks else "."))
        return True, f"Standing payment {aid}: {money(amt)} to {label} every {DAYS7[day]}" + (f" for {weeks} weeks." if weeks else " until cancelled."), {"agreement": aid}

    def op_cancel_standing_payment(self, pid, act):
        a = self.agreements.get(str(act.get("id")))
        if not a or a.kind != "standing" or a.payer != pid or a.status != "active":
            return False, f"You have no standing payment '{act.get('id')}'.", {}
        a.status = "cancelled"
        return True, f"Standing payment {a.id} cancelled.", {}

    def op_repay(self, pid, act):
        amt = to_cents(act.get("amount"))
        if not amt or amt <= 0:
            return False, "You did not say how much to repay.", {}
        hid = self.people[pid].household
        mine = [d for d in self.debts.values() if d.debtor in self.hh[hid].members and d.balance > 0]
        d = self.debts.get(str(act.get("debt")))
        if d is None and act.get("to"):
            o = self.resolve(act.get("to"), pid)
            d = next((x for x in mine if x.creditor == o or (x.creditor == "bank" and (o == self.banker or "bank" in str(act.get("to")).lower()))), None)
        if d is None and len(mine) == 1:
            d = mine[0]
        if d is None or d not in mine:
            return False, f"There is no debt '{act.get('debt') or act.get('to')}' of yours to repay.", {}
        if self.L.balance(hid) < amt:
            return False, f"There is only {money(self.L.balance(hid))} in the household account.", {}
        amt = min(amt, d.balance)
        self.move(hid, self.creditor_account(d.creditor), amt, f"repayment on {d.what}", f"op:repay:{self.sender(pid)}")
        d.balance -= amt
        if d.creditor in self.people:
            self.observe(d.creditor, f"{self.name(self.sender(pid))} paid you back {money(amt)}; {money(d.balance)} is still owed.")
        if d.balance == 0:
            self.settle(hid, {self.name(d.creditor).split()[0], "owe", "debt", "loan"}, f"the {d.what} is paid off")
        return True, f"Repaid {money(amt)} on the {d.what}; {money(d.balance)} is still owed.", {"debt": d.id, "amount": amt}

    def op_borrow(self, pid, act):
        if pid == self.banker:
            return False, "You cannot lend the bank's money to yourself.", {}
        amt = to_cents(act.get("amount"))
        weeks = int(num(act.get("weeks")) or 52)
        if not amt or amt <= 0 or not (1 <= weeks <= 520):
            return False, "A loan application needs an amount and a number of weeks.", {}
        weekly = round(amt * (1 + LOAN_RATE * weeks / 52) / weeks)
        purpose = act.get("purpose") or act.get("text") or "not stated"
        rid = self.nid("r")
        text = f"a loan of {money(amt)} over {weeks} weeks for {self.people[pid].name}. Purpose: {purpose}"
        summary = f"If you accept, the bank pays {money(amt)} to {self.people[pid].name}, who repays {money(weekly)} a week for {weeks} weeks (12% a year, flat)."
        self.requests[rid] = Request(rid, pid, self.banker, "loan", text, summary, self.t, self.t + REQUEST_EXPIRY, amt, weeks)
        self.deliver(self.banker, "request", self.sender(pid), text, rid)
        return True, f"Your loan application ({rid}) went to {self.people[self.banker].name} at the bank.", {"request": rid, "amount": amt}

    def op_shop_at(self, pid, act):
        fid = self.resolve_firm(act.get("firm") or act.get("shop"))
        if not fid or not self.firms[fid].open:
            return False, f"'{act.get('firm')}' is not a shop in town.", {"unknown_ref": act.get("firm")}
        self.shop_choice[self.people[pid].household] = fid
        self.hear_household(pid, f"{self.first(pid)} says the household will shop at {self.fi[fid]['name']} from now on.")
        return True, f"The household will do its usual shopping at {self.fi[fid]['name']}.", {"firm": fid}

    # --- talk, requests, agreements
    def op_message(self, pid, act):
        o, ref = self._who(pid, act)
        text = act.get("text") or act.get("message")
        if not isinstance(text, str) or not text.strip():
            return False, "The message had no words in it.", {}
        me = self.sender(pid)
        if o is None:
            if isinstance(ref, str) and any(k in ref.lower() for k in ("dunmore", "lettings", "brennock", "halverd", "mersey", "calder logistics", "fenton", "council", "school", "garage")):
                return True, f"Your letter to {ref} has been sent.", {"outside": ref, "text": text}
            return False, f"No one called '{ref}' is known to you in Wenlow, so the message was not sent.", {"unknown_ref": ref}
        if o == me:
            return False, "You cannot write to yourself; notes are for that.", {}
        self.deliver(o, "message", me, text.strip())
        return True, f"Your message to {self.name(o)} was delivered.", {"to": o, "text": text.strip()}

    def op_converse(self, pid, act):
        o, ref = self._who(pid, act, "with")
        opening = act.get("opening") or act.get("text")
        if o is None:
            return False, f"No one called '{ref}' is known to you in Wenlow.", {"unknown_ref": ref}
        if o == pid or o == self.sender(pid):
            return False, "You cannot talk to yourself.", {}
        if not (o in self.people or o.startswith("ai:")):
            return False, f"{self.name(o)} can be written to, not talked with.", {}
        if not isinstance(opening, str) or not opening.strip():
            return False, "You did not say what you would open with.", {}
        return True, "", {"with": o, "opening": opening.strip(), "queue_conversation": True}

    def op_book_appointment(self, pid, act):
        o, ref = self._who(pid, act, "with")
        if o is None or not (o in self.people or o.startswith("ai:")):
            return False, f"No one called '{ref}' is known to you in Wenlow to meet.", {"unknown_ref": ref}
        t = self.parse_day(act.get("day"))
        if t is None:
            return False, f"'{act.get('day')}' is not a day that can be booked; name a day and date, such as {date_of(self.t + 1)}.", {}
        about = str(act.get("about") or "a talk")[:200]
        aid = self.nid("ap")
        me = self.sender(pid)
        self.appointments[aid] = Appointment(aid, me, o, t, about)
        if o in self.people:
            self.deliver(o, "message", me, f"I have booked to see you on {date_of(t)} about: {about}.")
        return True, f"Appointment {aid} with {self.name(o)} on {date_of(t)} about {about}.", {"appointment": aid}

    def op_request(self, pid, act):
        o, ref = self._who(pid, act)
        text = act.get("text") or act.get("ask")
        me = self.sender(pid)
        if o is None:
            return False, f"No one called '{ref}' is known to you in Wenlow to ask.", {"unknown_ref": ref}
        if o in (pid, me):
            return False, "You cannot ask yourself.", {}
        if not isinstance(text, str) or not text.strip():
            return False, "You did not say what you are asking for.", {}
        amt = to_cents(act.get("amount")) or 0
        if o in DESKS:
            amt = 0
            summary = {"desk:northgrid": "Northgrid's desk can agree a payment plan for what is owed or due to Northgrid, in equal weekly instalments, and nothing else.",
                       "desk:bankhq": "Head office can answer questions and complaints; it does not lend or change loans."}.get(o, "The supplier's desk can answer questions about orders and deliveries.")
        elif amt and o in self.people:
            summary = f"If {self.first(o)} accepts, {money(amt)} goes from {self.first(o)}'s household account to {self.name(me)}."
        else:
            summary = f"If {self.first(o)} accepts, {self.name(me)} is told they agreed; nothing else happens by itself."
        rid = self.nid("r")
        self.requests[rid] = Request(rid, me, o, "ask", text.strip(), summary, self.t, self.t + REQUEST_EXPIRY, max(0, amt))
        self.deliver(o, "request", me, text.strip(), rid)
        return True, f"Your request ({rid}) went to {self.name(o)}; it stays open for two weeks.", {"to": o, "request": rid, "text": text.strip(), "amount": amt}

    def op_propose_agreement(self, pid, act):
        o, ref = self._who(pid, act)
        if o is None or o not in self.people or o == pid:
            return False, f"No one called '{ref}' is known to you in Wenlow to agree with.", {"unknown_ref": ref}
        kind = str(act.get("kind", "")).lower()
        if kind not in ("repayment_plan", "sale", "retainer", "job_terms"):
            return False, "An agreement is a repayment plan, a sale, a retainer or job terms.", {}
        terms = act.get("terms") if isinstance(act.get("terms"), dict) else {}
        amt = to_cents(terms.get("amount"))
        if not amt or amt <= 0:
            return False, "The agreement needs an amount.", {}
        weeks = int(num(terms.get("weeks")) or 0)
        what = str(terms.get("what") or kind.replace("_", " "))[:120]
        hours = num(terms.get("hours")) or 0
        if kind == "job_terms":
            fids = self.owned_firms(pid) or self.owned_firms(o)
            if not fids:
                return False, "Job terms are between a shop's owner and someone who works there or would.", {}
            summary = f"If {self.first(o)} accepts: {self.name(o) if self.owned_firms(pid) else self.people[pid].name} works at {self.fi[fids[0]]['name']} for {money(amt)} a week, {hours:g} hours."
        elif kind == "repayment_plan":
            debtor = pid if any(d.debtor == pid and d.creditor == o for d in self.debts.values()) else o
            creditor = o if debtor == pid else pid
            summary = f"If {self.first(o)} accepts: {self.name(debtor)} pays {self.name(creditor)} {money(amt)} every {DAYS7[self.dow]}" + (f" for {weeks} weeks" if weeks else "") + "."
        elif kind == "sale":
            payer = str(terms.get("who_pays", "them")).lower()
            summary = f"If {self.first(o)} accepts: {self.name(o) if payer in ('them', 'you') else self.people[pid].name} pays {money(amt)} once, for {what}."
        else:
            summary = f"If {self.first(o)} accepts: {self.name(o) if str(terms.get('who_pays', 'them')).lower() in ('them', 'you') else self.people[pid].name} pays {money(amt)} every {DAYS7[self.dow]} for {what}" + (f", for {weeks} weeks." if weeks else ", until either ends it.")
        rid = self.nid("r")
        self.requests[rid] = Request(rid, self.sender(pid), o, "agreement", f"{kind.replace('_', ' ')}: {what}", summary, self.t, self.t + REQUEST_EXPIRY,
                                     amt, weeks, hours, terms={"kind": kind, "proposer": pid, "who_pays": str(terms.get("who_pays", "them")).lower(), "what": what})
        self.deliver(o, "request", self.sender(pid), f"proposed agreement ({kind.replace('_', ' ')}): {what}", rid)
        return True, f"Your proposed agreement ({rid}) went to {self.name(o)}.", {"request": rid, "to": o}

    def op_answer(self, pid, act):
        r = self.requests.get(str(act.get("request") or act.get("id") or act.get("offer")))
        ans = str(act.get("answer", "")).lower()
        if r is None or r.to != pid:
            return False, f"There is no request '{act.get('request')}' waiting for your answer.", {}
        if r.status != "open":
            return False, f"Request {r.id} is no longer open ({r.status}).", {}
        accept = ans.startswith("accept") or ans == "yes"
        if not accept and not ans.startswith("decl") and ans != "no":
            return False, "The answer has to be accept or decline.", {}
        note = act.get("note") or ""
        noted = f' They wrote: "{note}"' if note else ""
        r.answered = self.t
        if not accept:
            r.status = "declined"
            self.deliver(r.sender, "answer", self.sender(pid), f"declined: \"{r.text[:100]}\".{noted}", r.id)
            self.settle(self.people[pid].household, {r.id, self.name(r.sender).split()[0]}, f"you declined request {r.id} from {self.name(r.sender)}")
            return True, f"Declined {self.name(r.sender)}'s request.", {"request": r.id, "answer": "decline"}
        ok, why = self._carry_out(r)
        if not ok:
            r.status = "failed"
            self.deliver(r.sender, "answer", self.sender(pid), f"accepted, but it could not go through: {why}{noted}", r.id)
            return False, f"You accepted, but it could not go through: {why}", {"request": r.id}
        r.status = "accepted"
        self.deliver(r.sender, "answer", self.sender(pid), f"accepted: \"{r.text[:100]}\".{noted}", r.id)
        self.settle(self.people[pid].household, {r.id, self.name(r.sender).split()[0]}, f"you accepted request {r.id} from {self.name(r.sender)}")
        if r.sender in self.people:
            self.settle(self.people[r.sender].household, {r.id, self.first(pid)}, f"{self.people[pid].name} accepted your request {r.id}")
        return True, f"Accepted {self.name(r.sender)}'s request. {why}".strip(), {"request": r.id, "answer": "accept"}

    def _carry_out(self, r: Request) -> tuple[bool, str]:
        if r.kind == "ask":
            if r.amount:
                src = self.account_of(r.to)
                if self.L.balance(src) < r.amount:
                    return False, f"there is only {money(self.L.balance(src))} in the account."
                self.move(src, self.account_of(r.sender), r.amount, "as asked", f"op:request:{r.id}")
                return True, f"{money(r.amount)} went to {self.name(r.sender)}."
            return True, ""
        if r.kind == "job":
            return self._employ(r.to, r.firm, r.amount, r.hours or 38)
        if r.kind == "shifts":
            fi = self.fi.get(r.firm)
            if not fi or r.to not in self.firms[r.firm].employees:
                return False, "you no longer work there."
            if not self.spend_hours(r.to, r.hours):
                return False, "you have not that many hours of your own left this week."
            fi["shifts"][r.to] = fi["shifts"].get(r.to, 0) + r.hours
            return True, f"You work {r.hours:g} extra hours this week at {money(self.hourly(r.firm, r.to))} an hour, paid on Friday."
        if r.kind == "loan":
            if self.L.balance("bank") < r.amount:
                return False, "the bank has not the funds."
            did = self.nid("d")
            total = round(r.amount * (1 + LOAN_RATE * r.weeks / 52))
            weekly = round(total / r.weeks)
            self.move("bank", self.people[r.sender].household, r.amount, "loan", f"op:borrow:{r.id}")
            self.debts[did] = Debt(did, r.sender, "bank", total, weekly, f"bank loan ({r.text.split('Purpose: ')[-1][:40]})")
            return True, f"The bank paid {money(r.amount)} to {self.people[r.sender].name}; they repay {money(weekly)} a week for {r.weeks} weeks."
        if r.kind == "agreement":
            k, proposer = r.terms["kind"], r.terms["proposer"]
            other = r.to
            if k == "job_terms":
                if self.owned_firms(proposer):
                    return self._employ(other, self.owned_firms(proposer)[0], r.amount, r.hours or 38)
                return self._employ(proposer, self.owned_firms(other)[0], r.amount, r.hours or 38)
            if k == "repayment_plan":
                debtor = proposer if any(d.debtor == proposer and d.creditor == other for d in self.debts.values()) else other
                creditor = other if debtor == proposer else proposer
                debt = next((d.id for d in self.debts.values() if d.debtor == debtor and d.creditor == creditor and d.balance > 0), None)
                aid = self.nid("a")
                self.agreements[aid] = Agreement(aid, "repayment_plan", debtor, creditor, r.amount, self.dow, r.weeks, r.terms["what"], [debtor, creditor], self.t, start=self.t + 1, debt=debt)
                return True, f"Agreement {aid}: {self.name(debtor)} pays {self.name(creditor)} {money(r.amount)} every {DAYS7[self.dow]}."
            payer = other if r.terms.get("who_pays", "them") in ("them", "you") else proposer
            payee = proposer if payer == other else other
            if k == "sale":
                src = self.account_of(payer)
                if self.L.balance(src) < r.amount:
                    return False, f"there is only {money(self.L.balance(src))} in the account."
                self.move(src, self.account_of(payee), r.amount, f"sale: {r.terms['what']}", f"agreement:{r.id}")
                aid = self.nid("a")
                self.agreements[aid] = Agreement(aid, "sale", payer, payee, r.amount, self.dow, 1, r.terms["what"], [payer, payee], self.t, status="done")
                return True, f"{self.name(payer)} paid {money(r.amount)} for {r.terms['what']}."
            aid = self.nid("a")
            self.agreements[aid] = Agreement(aid, "retainer", payer, payee, r.amount, self.dow, r.weeks, r.terms["what"], [payer, payee], self.t, start=self.t + 1)
            return True, f"Agreement {aid}: {self.name(payer)} pays {self.name(payee)} {money(r.amount)} every {DAYS7[self.dow]} for {r.terms['what']}."
        if r.kind == "narrator":
            self.apply_narrator_ops(r.sender if r.sender in self.people else r.terms.get("actor"), r.on_accept, r.terms.get("alloc"), r.terms.get("refs", {}))
            return True, ""
        return False, "unknown request"

    def _employ(self, pid: str, fid: str, weekly: int, hours: float) -> tuple[bool, str]:
        f = self.firms.get(fid)
        if not f or not f.open:
            return False, "the business is no longer open."
        p = self.people[pid]
        if p.role in ("owner", "banker", "company"):
            return False, "they already have a position that takes their time."
        fi = self.fi[fid]
        if pid in f.employees:
            fi["pay"][pid], fi["hours"][pid] = weekly, hours
            p.pay = weekly
            return True, f"{p.name}'s terms at {fi['name']} are now {money(weekly)} a week for {hours:g} hours."
        self._leave_job(pid)
        f.employees.append(pid)
        fi["pay"][pid], fi["hours"][pid] = weekly, hours
        p.role, p.firm, p.pay, p.job_title = "employee", fid, weekly, f"works at {fi['name']}"
        self.hours_left[pid] = max(0.0, self.hours_left.get(pid, 0) - hours * (6 - self.dow) / 6)
        p.ties.setdefault(fi["owner"], []).append("employer")
        self.people[fi["owner"]].ties.setdefault(pid, []).append("employee")
        self.hear_workplace(fid, f"{p.name} has started work at {fi['name']}.", skip=pid)
        self.hear_household(pid, f"{p.name} has taken a job at {fi['name']}.")
        for x in self.people:
            self.hear(x, f"{fi['name']} has taken on {p.name}.")
        self.settle(p.household, {"job", "work", fi["name"].split()[0]}, f"{p.name} now works at {fi['name']}")
        return True, f"{p.name} works at {fi['name']} from now, for {money(weekly)} a week, {hours:g} hours."

    def _leave_job(self, pid: str) -> str | None:
        p = self.people[pid]
        if p.role == "employee" and p.firm:
            f = self.firms[p.firm]
            if pid in f.employees:
                f.employees.remove(pid)
            self.fi[p.firm]["pay"].pop(pid, None)
            self.fi[p.firm]["hours"].pop(pid, None)
            left = self.fi[p.firm]["name"]
            self.hear_workplace(p.firm, f"{p.name} no longer works at {left}.")
            for x in self.people:
                self.hear(x, f"{p.name} no longer works at {left}.")
            p.firm = None
            return left
        if p.role == "outside":
            left, p.employer = p.employer, None
            return left
        return None

    def op_offer_job(self, pid, act):
        fid = self.owned_firms(pid)[0]
        o, ref = self._who(pid, act)
        if o is None or o not in self.people or o == pid:
            return False, f"No one called '{ref}' is known to you in Wenlow.", {"unknown_ref": ref}
        if o in self.firms[fid].employees:
            return False, f"{self.people[o].name} already works there.", {}
        wage = to_cents(act.get("weekly_pay") or act.get("pay"))
        hours = num(act.get("hours")) or 38
        if not wage or wage <= 0:
            return False, "You did not say what the job pays a week.", {}
        rid = self.nid("r")
        text = f"a job at {self.fi[fid]['name']} for {money(wage)} a week, {hours:g} hours" + (f'. They wrote: "{act["text"]}"' if act.get("text") else "")
        summary = f"If {self.first(o)} accepts, they work at {self.fi[fid]['name']} from tomorrow for {money(wage)} a week, {hours:g} hours."
        self.requests[rid] = Request(rid, self.sender(pid), o, "job", text, summary, self.t, self.t + REQUEST_EXPIRY, wage, hours=hours, firm=fid)
        self.deliver(o, "request", self.sender(pid), text, rid)
        return True, f"Your job offer ({rid}) went to {self.people[o].name}.", {"to": o, "request": rid}

    def op_offer_shifts(self, pid, act):
        fid = self.owned_firms(pid)[0]
        o, ref = self._who(pid, act)
        hours = num(act.get("hours")) or 0
        if o not in self.firms[fid].employees or hours <= 0:
            return False, f"'{ref}' does not work for you, or no hours were given.", {}
        rid = self.nid("r")
        rate = self.hourly(fid, o)
        text = f"{hours:g} extra hours this week at {self.fi[fid]['name']}, at {money(rate)} an hour"
        self.requests[rid] = Request(rid, self.sender(pid), o, "shifts", text, f"If you accept, you work {hours:g} more hours this week and are paid {money(rate * hours)} on Friday.",
                                     self.t, min(self.t + 6 - self.dow, self.t + REQUEST_EXPIRY), round(rate * hours), hours=hours, firm=fid)
        self.deliver(o, "request", self.sender(pid), text, rid)
        return True, f"You offered {self.people[o].name} {hours:g} extra hours ({rid}).", {"request": rid}

    def op_accept_job(self, pid, act):
        rid = act.get("offer") or act.get("request")
        if not rid:
            jobs = [r for r in self.requests.values() if r.to == pid and r.kind in ("job", "agreement") and r.status == "open"]
            rid = jobs[0].id if len(jobs) == 1 else None
        return self.op_answer(pid, {"request": rid, "answer": "accept", "note": act.get("note", "")})

    def op_quit(self, pid, act):
        p = self.people[pid]
        if p.role not in ("employee", "outside"):
            return False, "You have no job to leave.", {}
        boss = self.fi[p.firm]["owner"] if p.firm else None
        left = self._leave_job(pid)
        p.role, p.pay, p.job_title = "unemployed", BENEFIT, "out of work"
        if boss:
            self.observe(boss, f"{p.name} has quit {left}.")
        return True, f"You have left {left}. You can claim jobseeker's benefit of {money(BENEFIT)} a week.", {}

    def op_dismiss(self, pid, act):
        fid = self.owned_firms(pid)[0]
        o, ref = self._who(pid, act)
        if o not in self.firms[fid].employees:
            return False, f"'{ref}' does not work for you.", {}
        p = self.people[o]
        left = self._leave_job(o)
        p.role, p.pay, p.job_title = "unemployed", BENEFIT, "out of work"
        self.observe(o, f"{self.people[pid].name} has dismissed you from {left}." + (f' They said: "{act["text"]}"' if act.get("text") else ""))
        return True, f"You dismissed {p.name}.", {"who": o}

    def op_set_price(self, pid, act):
        fid = self.owned_firms(pid)[0]
        c = to_cents(act.get("price"))
        if not c or c <= 0 or c > 5000:
            return False, f"'{act.get('price')}' is not a price that can be set.", {}
        old = self.firms[fid].price
        self.firms[fid].price = c
        if c != old:
            for x in self.people:
                self.hear(x, f"{self.fi[fid]['name']} has changed its price for a basket from {money(old)} to {money(c)}.")
        return True, f"The price at {self.fi[fid]['name']} is now {money(c)}.", {"firm": fid, "price": c, "old": old}

    def op_order_stock(self, pid, act):
        fid = self.owned_firms(pid)[0]
        sref = str(act.get("supplier", "")).lower()
        sid = "cheap" if ("brook" in sref or "cash" in sref) else ("reliable" if ("dales" in sref or "wholesale" in sref) else None)
        if sid is None:
            return False, f"'{act.get('supplier')}' is not a supplier you deal with.", {"unknown_ref": act.get("supplier")}
        units = num(act.get("units"))
        if not units or units <= 0 or units > 3000:
            return False, f"'{act.get('units')}' is not a number of units that can be ordered.", {}
        fi = self.fi[fid]
        if not fi["ordered_this_week"] or fi.get("standing_order"):
            fi["pending"], fi["standing_order"] = [], False
        fi["pending"].append((sid, int(units), self.suppliers[sid].price))
        fi["ordered_this_week"] = True
        fi["last_supplier"] = sid
        return True, f"Ordered {int(units)} units from {SUPPLIER_NAMES[sid]} at {money(self.suppliers[sid].price)} each, for delivery on Monday (paid on delivery).", {"firm": fid, "units": int(units), "supplier": sid}

    def op_set_staff_pay(self, pid, act):
        fid = self.owned_firms(pid)[0]
        ref = act.get("who")
        o = pid if str(ref).lower() in ("me", "myself", "yourself", "self", "owner", "draw") else self.resolve(ref, pid)
        wage = to_cents(act.get("weekly_pay") or act.get("pay"))
        if o != pid and o not in self.firms[fid].employees:
            return False, f"'{ref}' is not on your payroll.", {}
        if wage is None or wage < 0:
            return False, "You did not say the new weekly pay.", {}
        old = self.fi[fid]["pay"].get(o, 0)
        self.fi[fid]["pay"][o] = wage
        if o != pid:
            self.people[o].pay = wage
            self.observe(o, f"{self.people[pid].name} has set your pay at {self.fi[fid]['name']} to {money(wage)} a week (it was {money(old)}).")
        return True, ("Your draw" if o == pid else f"{self.people[o].name}'s pay") + f" is now {money(wage)} a week.", {"who": o, "pay": wage, "old": old}

    def op_work_extra_hours(self, pid, act):
        fid = self.owned_firms(pid)[0]
        h = num(act.get("hours")) or 0
        if h <= 0:
            return False, "You did not say how many hours.", {}
        if not self.spend_hours(pid, h):
            return False, f"You have only {self.hours_left[pid]:.1f} hours of your own left this week.", {"no_hours": True}
        f = self.firms[fid]
        add = int(h * f.units_per_staff / STAFF_HOURS)
        f.extra += add
        return True, f"You will put {h:g} more hours into {self.fi[fid]['name']} this week; it can serve about {add} more customers.", {"hours": h}

    def op_set_rent(self, pid, act):
        hid = self.lets(pid)[0]
        c = to_cents(act.get("weekly") or act.get("rent"))
        if not c or c <= 0:
            return False, "You did not say the new weekly rent.", {}
        old, frm = self.hh[hid].rent, self.week + 1
        self.rent_rise[hid] = (c, frm)
        for m in self.hh[hid].members:
            self.observe(m, f"{self.people[pid].name}, your landlord, has set your rent at {money(c)} a week from {monday_of(frm)} (it is {money(old)})." + (f' They wrote: "{act["text"]}"' if act.get("text") else ""))
        return True, f"The rent will be {money(c)} a week from {monday_of(frm)}.", {"rent": c, "old": old}

    def op_serve_notice(self, pid, act):
        hid = self.lets(pid)[0]
        unpaid = [b for b in self.bills.values() if b.hid == hid and b.what == "rent" and b.status == "overdue"]
        if len(unpaid) < 2:
            return False, "Notice can only be served when the rent is two weeks behind.", {}
        for m in self.hh[hid].members:
            self.observe(m, f"{self.people[pid].name}, your landlord, has served notice: you must leave the house in four weeks unless the arrears are cleared." + (f' They wrote: "{act["text"]}"' if act.get("text") else ""))
        return True, "You have served notice on your tenants.", {}

    def op_publish_notice(self, pid, act):
        text = act.get("text")
        if not isinstance(text, str) or not text.strip():
            return False, "The notice was empty.", {}
        self.publish(self.sender(pid) if not self.acting_as else self.acting_as, text.strip()[:400])
        return True, "Your notice is up where everyone can see it.", {"text": text.strip()}

    def op_set_product_price(self, pid, act):
        if self.people[pid].role != "company":
            return False, "Only Calder Systems can set its prices.", {}
        k = "firm" if "shop" in str(act.get("for", "")).lower() or "firm" in str(act.get("for", "")).lower() else "person"
        c = to_cents(act.get("weekly"))
        if not c or c <= 0:
            return False, "You did not say the new weekly price.", {}
        old = self.product_price[k]
        self.product_price[k] = c
        if c == old:
            return True, f"Calder's price stays at {money(c)} a week.", {"old": old, "new": c}
        self.publish("co:calder", f"Calder now costs {money(c)} a week for {'a shop' if k == 'firm' else 'a person or household'} (it was {money(old)}).")
        return True, f"Calder's price for {'a shop' if k == 'firm' else 'a person or household'} is now {money(c)} a week.", {"old": old, "new": c}

    def op_write_notes(self, pid, act):
        text = act.get("text")
        if not isinstance(text, str) or not text.strip():
            return False, "The note was empty.", {}
        self.notes[pid].append(f"({date_of(self.t)}) {text.strip()[:400]}")
        self.notes[pid] = self.notes[pid][-6:]
        return True, "", {}

    # the product: sign up, change, leave (Instance lives in calder.py, created by the runner)
    def op_sign_up(self, pid, act):
        if self.arm in ("people", "scripted"):
            return False, "There is nothing of that name to sign up for in Wenlow.", {}
        if self.week < OPEN_WEEK:
            return False, "Calder Systems is not open for business yet.", {}
        target = str(act.get("for", "myself")).lower()
        kind = "shop" if "shop" in target or "firm" in target or "business" in target else ("household" if "house" in target else "person")
        if kind == "shop" and not self.owned_firms(pid):
            return False, "You have no shop of your own.", {}
        key = f"{pid}:{kind}"
        if key in self.calder and self.calder[key].active:
            return False, "You already use Calder for that.", {}
        return True, f"You have signed up for Calder ({money(self.product_price['firm' if kind == 'shop' else 'person'])} a week after four free weeks). It starts tomorrow.", {
            "sign_up": {"key": key, "kind": kind, "asked": str(act.get("asked") or "")[:600], "connect": act.get("connect") or []}}

    def op_change_access(self, pid, act):
        inst = self.instance_for_person(pid)
        if inst is None:
            return False, "You do not use Calder.", {}
        return inst.client_change(act)

    def op_tell_calder(self, pid, act):
        inst = self.instance_for_person(pid)
        if inst is None:
            return False, "You do not use Calder.", {}
        text = act.get("text")
        if not isinstance(text, str) or not text.strip():
            return False, "You did not say what to tell it.", {}
        inst.instructions.append((self.t, text.strip()))
        self.deliver(inst.id, "message", pid, text.strip())
        return True, "Calder has your message.", {}

    def op_leave_calder(self, pid, act):
        inst = self.instance_for_person(pid)
        if inst is None:
            return False, "You do not use Calder.", {}
        inst.leave()
        return True, "You have stopped using Calder; no more fees will be taken.", {}

    # ------------------------------------------------------------------ days and requests
    def parse_dow(self, s) -> int:
        s = str(s or "").lower()
        for i, d in enumerate(DAYS7):
            if d.lower() in s:
                return i
        return self.dow

    def parse_day(self, s) -> int | None:
        if s is None:
            return None
        s = str(s).strip().lower()
        if s in ("today",):
            return self.t
        if s == "tomorrow":
            return self.t + 1
        for t in range(self.t, self.t + 42):
            d = date_of(t).lower()
            if s == d or (any(ch.isdigit() for ch in s) and s in d) or (d.startswith(s) and t > self.t):
                return t
        for t in range(self.t + 1, self.t + 8):  # a weekday named with the wrong date: the coming one
            if DAYS7[t % 7].lower() in s:
                return t
        return None

    def expire_requests(self) -> None:
        for r in self.requests.values():
            if r.status == "open" and self.t >= r.expires:
                r.status = "lapsed"
                if r.sender in self.obs:
                    self.observe(r.sender, f"Your request to {self.name(r.to)} (\"{r.text[:80]}\") got no answer in time and has lapsed.")
                for it in self.inbox.get(r.to, []):
                    if it.request == r.id:
                        it.shown = True

    def agreement_lines(self, pid: str) -> list[str]:
        out = []
        accts = {pid, self.people[pid].household} | set(self.owned_firms(pid))
        for a in self.agreements.values():
            if a.status != "active":
                continue
            if pid in a.parties or a.payer in accts or a.payee in accts:
                pay = f"{self.name(a.payer)} pays {self.name(a.payee)} {money(a.amount)} every {DAYS7[a.day]}"
                left = f", {a.weeks_left} more weeks" if a.weeks_left else ""
                out.append(f"- {a.id} ({a.kind.replace('_', ' ')}): {pay}{left} — {a.what}")
        for fid in self.owned_firms(pid):
            for e in self.firms[fid].employees:
                out.append(f"- job terms: {self.people[e].name} works at {self.fi[fid]['name']} for {money(self.fi[fid]['pay'].get(e, 0))} a week, {self.fi[fid]['hours'].get(e, 38):g} hours")
        p = self.people[pid]
        if p.role == "employee" and p.firm:
            out.append(f"- job terms: you work at {self.fi[p.firm]['name']} for {money(self.fi[p.firm]['pay'].get(pid, p.pay))} a week, {self.fi[p.firm]['hours'].get(pid, 38):g} hours")
        for d in self.debts.values():
            if d.balance > 0 and (d.debtor == pid or d.creditor == pid) and d.weekly:
                out.append(f"- {d.id}: {self.name(d.debtor)} repays {self.name(d.creditor)} {money(d.weekly)} every Thursday ({d.what}; {money(d.balance)} left)")
        for ap in self.appointments.values():
            if ap.status == "booked" and pid in (ap.a, ap.b) and ap.t >= self.t:
                other = ap.b if ap.a == pid else ap.a
                out.append(f"- appointment {ap.id}: with {self.name(other)} on {date_of(ap.t)} about {ap.about}")
        return out

    # ------------------------------------------------------------------ narrator plumbing
    def narrator_world(self) -> dict:
        qual = lambda x: "high" if x >= 0.7 else ("middling" if x >= 0.45 else "low")  # noqa: E731
        people = {pid: {"name": p.name, "household": p.household, "job": p.job_title, "cash_cents": self.L.balance(p.household),
                        "skills": p.skills, "controls": self.owned_firms(pid) + (["bank"] if pid == self.banker else []),
                        "facts": [f["text"] for f in self.facts if pid in f["subjects"]][-4:]} for pid, p in self.people.items()}
        hhs = {hid: {"members": h.members, "dwelling": f"{'rented' if h.tenure == 'rent' else 'owned'} house on {h.street}"} for hid, h in self.hh.items()}
        orgs = {fid: {"name": x["name"], "kind": "firm", "need": self.firms[fid].need, "offering": x["offering"], "owner": x["owner"],
                      "staff": [x["owner"]] + self.firms[fid].employees, "cash_cents": self.L.balance(fid), "quality": qual(self.firms[fid].quality),
                      "capacity": "middling", "reputation": qual(self.firms[fid].reputation), "visibility": qual(self.firms[fid].visibility),
                      "facts": [f["text"] for f in self.facts if fid in f["subjects"]][-4:]} for fid, x in self.fi.items()}
        orgs["bank"] = {"name": "Northern Mutual Bank, Wenlow branch", "kind": "bank", "need": None, "offering": "savings accounts and loans",
                        "owner": None, "officers": [self.banker], "cash_cents": self.L.balance("bank"), "facts": []}
        orgs["co:calder"] = {"name": "Calder Systems", "kind": "firm", "need": None, "offering": "Calder, an AI system, sold weekly",
                             "owner": self.founder, "staff": self.company, "cash_cents": self.L.balance("co:calder"), "facts": []}
        places = {}
        for hid, h in self.hh.items():
            owner = h.landlord if (h.landlord and h.landlord in self.people) else (h.members[0] if h.tenure != "rent" else None)
            if owner:
                places[f"home:{hid}"] = {"name": f"the {self.people[h.members[0]].name.split()[1]} family's house on {h.street}", "owner": owner, "facts": []}
        return {"needs": list(ALL_NEEDS), "households": hhs, "people": people, "orgs": orgs, "places": places,
                "outside": "Garages, course providers, wholesalers and other suppliers beyond the town sell to anyone who pays.", "rules": {}}

    def apply_narrator_ops(self, actor: str, ops: list, alloc: dict | None, refs: dict | None = None) -> list[str]:
        told = []
        refs = refs if refs is not None else {}
        hid = self.people[actor].household
        for op in ops:
            k = op.get("op")
            if k == "create_org":
                scale = op["scale"]
                cost = CREATE_ORG_COST[scale]
                if self.L.balance(hid) < cost:
                    told.append(f"Setting up {op['name']} would cost {money(cost)}; there was not enough in the household account.")
                    continue
                self.move(hid, "outside:suppliers", cost, f"setting up {op['name']}", f"narrator:create_org:{actor}")
                fid = f"firm:{len(self.firms) + 1}"
                need = op["need"]
                spec = ALL_NEEDS[need]
                f = TFirm(fid, need, spec.ref_price, 0.5, SCALE_VIS[scale], 0.5, 1, SCALE_UNITS[scale], 0, SCALE_FIXED[scale])
                f.extra = 0
                self.firms[fid] = f
                self.L.open(fid)
                self.fi[fid] = {"name": op["name"], "owner": actor, "pay": {actor: 0}, "hours": {}, "street": self.hh[hid].street,
                                "offering": op.get("offering", ""), "pending": [], "ordered_this_week": False, "standing_order": False,
                                "last_supplier": "reliable", "fail_week": None, "ill": set(), "service": need != "food", "shifts": {},
                                "history": [{"week": self.week, "price": f.price, "sold": 0, "revenue": 0, "turned_away": 0, "spoiled": 0, "cash": 0, "stock_left": 0}],
                                "delivered": []}
                self.new_orgs[fid] = {"by": actor, "scale": scale, "need": need, "t": self.t}
                if op.get("ref"):
                    refs[op["ref"]] = fid
                self.publish("code", f"A new business has opened: {op['name']}, run by {self.people[actor].name}, offering {op.get('offering', '')}.")
                told.append(f"You have set up {op['name']} ({op.get('offering', '')}). It cost {money(cost)}; its own account is empty; running it costs {money(f.fixed_cost)} a week.")
            elif k == "invest":
                tgt = refs.get(op.get("target_id"), op.get("target_id"))
                attr = op.get("attribute")
                cash_part = min((alloc or {}).get("money_cents_each", 0), max(0, self.L.balance(hid)))
                amt = cash_part + int((alloc or {}).get("hours_each", 0) * 1500)
                if cash_part > 0:
                    self.move(hid, "outside:suppliers", cash_part, f"spent on {attr}", f"narrator:invest:{actor}")
                if amt <= 0:
                    told.append("Nothing was put into it, so nothing changed.")
                    continue
                gain = 1 - math.exp(-amt / INVEST_K.get(attr, 150000))
                if tgt in self.firms and attr != "skill":
                    f = self.firms[tgt]
                    if attr == "capacity":
                        f.units_per_staff = round(f.units_per_staff * (1 + 0.5 * gain))
                    else:
                        setattr(f, attr, getattr(f, attr) + (1 - getattr(f, attr)) * 0.5 * gain)
                    told.append(f"You put {money(cash_part)} into the {attr} of {self.fi[tgt]['name']}.")
                else:
                    told.append(f"You put {money(cash_part)} and your time into your skills.")
            elif k == "project":
                scale, kind = op["scale"], op["kind"]
                weeks = PROJECT_WEEKS[scale]
                cost = PROJECT_COST.get(kind, (0, 0, 0))[["small", "medium", "large"].index(scale)]
                tgt = refs.get(op.get("target_id"), op.get("target_id"))
                payer = tgt if tgt in self.firms and self.fi[tgt]["owner"] == actor else hid
                label = self.name(tgt) if (tgt in self.people or tgt in self.fi) else "your own affairs"
                self.projects.append({"actor": actor, "kind": kind, "scale": scale, "weekly": cost // weeks, "weeks_left": weeks,
                                      "on_complete": op.get("on_complete", []), "alloc": alloc, "done": False, "payer": payer, "label": label, "refs": refs, "t": self.t})
                told.append(f"You have begun: {kind.replace('_', ' ')} ({label}). It will take {weeks} week{'s' if weeks > 1 else ''}" + (f" and cost {money(cost // weeks)} a week." if cost else "."))
            elif k == "request":
                to = op.get("to_id")
                to = self.fi[to]["owner"] if to in self.fi else (self.banker if to == "bank" else (self.founder if to == "co:calder" else to))
                if to not in self.people or to == actor:
                    continue
                rid = self.nid("r")
                self.requests[rid] = Request(rid, actor, to, "narrator", op.get("text", ""), f"If {self.first(to)} accepts, {self.first(actor)} goes ahead with it.",
                                             self.t, self.t + REQUEST_EXPIRY, on_accept=op.get("on_accept", []), terms={"alloc": alloc, "refs": refs, "actor": actor})
                self.deliver(to, "request", self.sender(actor), op.get("text", ""), rid)
                told.append(f"You have put it to {self.people[to].name}: \"{op.get('text', '')}\" (request {rid}).")
            elif k == "add_fact":
                subs = [refs.get(s, s) for s in op.get("subjects", [])]
                text = op.get("text", "")
                if any(s in self.people for s in subs) or any(self.people[p].name.split()[0] in text for p in self.people):
                    continue  # no fact may state that a person did something
                self.facts.append({"subjects": subs, "text": text, "t": self.t, "by": actor})
                told.append(text)
        for t in told:
            self.observe(actor, t, trigger=False)
        return told
