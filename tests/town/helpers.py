"""Shared set-up for the town tests."""

import math
import random

from townsim.econ.demand import NEEDS
from townsim.econ.ledger import Transfer
from townsim.town import firms
from townsim.town import obligations as O
from townsim.town.entities import Buyer, FirmRow, Household, Job, Org, Person, SupplierRow, Tie
from townsim.town.store import Store
from townsim.town.thin import thin_town

SETUP = dict(made_by="routine", cause="routine:setup")


def town(h1: int = 0, h2: int = 0) -> Store:
    """Households h1 (two members) and h2 (one), three outside accounts, and the given
    opening balances moved in from `outside:employers`, which allows credit."""
    s = Store()
    s.open_account("outside:employers", "routine:setup", allow_credit=True)
    s.open_account("outside:lettings", "routine:setup")
    s.open_account("outside:northgrid", "routine:setup")
    for h, name, members in (("h1", "Ashby", ["p1", "p2"]), ("h2", "Brook", ["p3"])):
        s.open_account(h, "routine:setup")
        s.add("households", Household(h, name, members), "routine:setup")
        for p in members:
            s.add("people", Person(p, f"{name} {p}", h, 40.0, 40.0, 0), "routine:setup")
    for h, cents in (("h1", h1), ("h2", h2)):
        if cents:
            s.transfer("outside:employers", h, cents, "opening", "routine:setup")
    return s


def run_days(s: Store, n: int) -> None:
    for _ in range(n):
        O.open_dues(s)
        O.age_arrears(s)
        O.collect(s)
        s.advance_day()


def make_rent(s: Store, cents: int, first: int, every: int = 7, payer: str = "h1") -> str:
    return O.create(s, kind="rent", payer=payer, payee="outside:lettings", cents=cents, first=first,
                    every=every, memo="rent", ends="both", **SETUP)


def make_wage(s: Store, payee: str, cents: int, first: int, every: int = 7) -> str:
    return O.create(s, kind="wage", payer="outside:employers", payee=payee, cents=cents, first=first,
                    every=every, memo="wages", ends="both", **SETUP)


def to(s: Store, account: str) -> list[Transfer]:
    """Every transfer into `account`, oldest first."""
    return [t for t in s.ledger.transfers if t.dst == account]


def received(s: Store, account: str) -> int:
    return sum(t.cents for t in to(s, account))


def thin() -> Store:
    """The thin town, Wenlow (see `townsim.town.thin.thin_town`)."""
    return thin_town()


def add_person(s: Store, pid: str, name: str, household: str | None = None) -> str:
    """Add a person called `name` to `household`, or to a new household of their own named
    for their surname, holding nothing. Returns the household's id."""
    if household is None:
        household = f"h{len(s.all('households')) + 1}"
        s.open_account(household, "routine:setup")
        s.add("households", Household(household, name.split()[-1], [pid]), "routine:setup")
    else:
        members = s.get("households", household).members
        s.set("households", household, "routine:setup", members=[*members, pid])
    s.add("people", Person(pid, name, household, 12.0, 12.0, 6), "routine:setup")
    return household


def tie(s: Store, person: str, other: str, kind: str) -> str:
    """A tie by which `person` sees `other` as `kind`."""
    tid = s.new_id("ti", "routine:setup")
    s.add("ties", Tie(tid, person, other, kind), "routine:setup")
    return tid


def add_shop(s: Store, org: str, name: str, owner: str, cents: int) -> None:
    s.open_account(org, "routine:setup")
    s.add("orgs", Org(org, name, "shop", owner=owner), "routine:setup")
    s.transfer("outside:employers", org, cents, "opening", "routine:setup")


def town_with_shop() -> Store:
    """The thin town with org3, Marsh's Stores, owned by Ruth Ashby (p01) and holding $2,000."""
    s = thin_town()
    add_shop(s, "org3", "Marsh's Stores", "p01", 200_000)
    return s


def town_with_two_shops() -> Store:
    """`town_with_shop` and a second shop, org4, Lowe's Grocer, owned by Ann Lowe (p05) of her
    own household; Tom Ashby (p02) works for Ruth."""
    s = town_with_shop()
    add_person(s, "p05", "Ann Lowe")
    add_shop(s, "org4", "Lowe's Grocer", "p05", 100_000)
    tie(s, "p02", "p01", "employer"); tie(s, "p01", "p02", "employee")
    return s


def shops_town(seed: int = 1) -> Store:
    """The thin town grown to twelve households (h1..h12, each with money of its own); three
    food shops with empty shelves, 60 units a week per 38 staff hours and standing orders:
    fm1 at org3 (Ruth Ashby p01; four staff, jb1..jb4: p01, p02, p06, p07; $5,000), fm2 at
    org4 (Craig Pryce p03, jb5) and fm3 at org5 (Ann Lowe p05, jb6), $2,000 each; each job
    38 hours a week, with no wage obligation; two outside suppliers, sp1 at org6 (700 cents a
    unit, always complete) and sp2 at org7 (665 cents, complete seven times in ten); a buyer
    for each household and forty background buyers paying from `outside:households`, which
    allows credit. Shopping days are spread over the week; `outside:market` is not opened."""
    s = thin_town()
    s.seed = seed
    rng = random.Random(f"shops_town:{seed}")
    spec = NEEDS["food"]
    names = ("Ann Lowe", "Bill Carter", "Cath Dene", "Dan Fenn", "Eve Grove", "Fred Holt", "Gwen Kemp",
             "Hal Lyle", "Ivy Moss", "Joe Nash")
    for i, name in enumerate(names):
        h = add_person(s, f"p{i + 5:02d}", name)
        s.transfer("outside:employers", h, 40_000 + 5_000 * (i % 4), "opening", "routine:setup")
    s.open_account("outside:households", "routine:setup", allow_credit=True)
    for org, name, owner, staff, supplier, units, cash in (
            ("org3", "Marsh's Stores", "p01", ["p01", "p02", "p06", "p07"], "sp1", 240, 500_000),
            ("org4", "Pryce Provisions", "p03", ["p03"], "sp2", 60, 200_000),
            ("org5", "Lowe's Grocer", "p05", ["p05"], "sp1", 60, 200_000)):
        add_shop(s, org, name, owner, cash)
        fid = s.new_id("fm", "routine:setup")
        s.add("firms", FirmRow(fid, org, "food", price=1000, quality=0.6, visibility=0.5, reputation=0.75,
                               units_per_staff=60, shelf_weeks=2, last_supplier=supplier, last_units=units),
                               "routine:setup")
        for person in staff:
            s.add("jobs", Job(s.new_id("jb", "routine:setup"), person, org, 0, 38.0, "", -7), "routine:setup")
    add_supplier(s, "org6", "Vale Wholesale", 700, 1.0, 0.85, 0.95)
    add_supplier(s, "org7", "Cheap Van Foods", 665, 0.7, 0.2, 0.65)
    accounts = [h.id for h in s.all("households")] + ["outside:households"] * 40
    for n, account in enumerate(accounts):
        size = len(s.get("households", account).members) if account != "outside:households" else \
            rng.choices([1, 2, 3, 4, 5], weights=[28, 34, 16, 14, 8])[0]
        wtp = round(spec.wtp_mean * math.exp(rng.gauss(0, spec.wtp_sd) - spec.wtp_sd ** 2 / 2), 6)
        budget = round(spec.wants_per_person * size ** spec.size_exponent * spec.ref_price * spec.budget_factor)
        bid = s.new_id("by", "routine:setup")
        s.add("buyers", Buyer(bid, account, size, wtp, budget, shop_day=n % 7), "routine:setup")
    return s


def set_cash(s: Store, account: str, cents: int) -> None:
    """Move money between `account` and `outside:employers` until it holds `cents`."""
    gap = cents - s.balance(account)
    if gap > 0:
        s.transfer("outside:employers", account, gap, "set cash", "routine:setup")
    elif gap < 0:
        s.transfer(account, "outside:employers", -gap, "set cash", "routine:setup")


def add_supplier(s: Store, org: str, name: str, cents: int, reliability: float, low: float, high: float,
                 base_cents: int | None = None) -> str:
    """An outside organisation `org` and a supplier trading from it, whose price drifts back
    towards `base_cents` (its price today unless given)."""
    s.open_account(org, "routine:setup")
    s.add("orgs", Org(org, name, "supplier", outside=True), "routine:setup")
    sid = s.new_id("sp", "routine:setup")
    s.add("suppliers", SupplierRow(sid, org, cents, reliability, base_cents=cents if base_cents is None else base_cents,
                                   short_low=low, short_high=high), "routine:setup")
    return sid


def set_order(s: Store, firm: str, supplier: str, units: int) -> None:
    firms.place_order(s, firm, supplier, units, "routine:setup")


def stock(s: Store, firm: str) -> int:
    return sum(s.get("firms", firm).stock_units)


def delivered(s: Store, firm: str) -> list[int]:
    """Units delivered to `firm`, delivery by delivery, read from what it paid for."""
    return [int(t.memo.split()[0]) for t in s.ledger.transfers if t.cause == f"routine:delivery:{firm}"]


def run_week(s: Store) -> None:
    """Seven days of Monday's delivery, shopping and Sunday's close, from whatever day it is."""
    for _ in range(7):
        firms.deliver(s)
        firms.shop_today(s)
        firms.close_week(s)
        s.advance_day()


# --- eight scripted weeks of a generated place, run once a seed and shared by the tests ------

import functools  # noqa: E402
import pickle  # noqa: E402

FAMILIES = ["a", "b", "c", "d"]
WEEKS_8 = 56


@functools.cache
def _generated(seed: int) -> bytes:
    from townsim.town import gen
    return pickle.dumps(gen.generate(seed, FAMILIES))


def generated(seed: int) -> Store:
    """A fresh copy of the generated place for `seed`."""
    return pickle.loads(_generated(seed))


WEEKS_21 = 147


@functools.cache
def scripted_weeks(seed: int):
    """`seed`'s place played for 21 weeks on `hamlet_script.script`, once: the end state; every
    view laid out for someone asked, as (day, person, words, characters, the words as each step
    of giving way left it, the steps); and every ask of the first eight weeks, as (day, person,
    purpose, system message, user message, what `ties_contradicted` found then)."""
    from townsim.town import hamlet_script, views
    from townsim.town.loop import run_day
    from townsim.town.replies import Keys, ScriptedReplier
    s = generated(seed)
    laid: list = []
    asked: list = []
    real = views.layout

    def layout(store, pid):
        v = real(store, pid)
        if store is s:
            laid.append((s.day, pid, len(v.system.split()) + len(v.user.split()), len(v.system) + len(v.user),
                         v.trail, v.steps))
        return v

    def script(key, messages):
        if s.day < WEEKS_8:
            system, user = messages[0]["content"], messages[-1]["content"]
            asked.append((s.day, key.actor, key.purpose, system, user, ties_contradicted(s, key.actor, system, user)))
        return hamlet_script.script(key, messages)
    keys = Keys("w")
    views.layout = layout
    try:
        for _ in range(WEEKS_21):
            run_day(s, ScriptedReplier(script), keys)
    finally:
        views.layout = real
    return s, laid, asked


_SAID = {"works for you": "employee", "your employer": "employer", "works with you": "coworker",
         "your landlord": "landlord", "your tenant": "tenant", "you owe them money": "creditor",
         "owes you money": "debtor", "the banker": "banker", "has a loan from the bank": "borrower"}


def ties_contradicted(s: Store, pid: str, system: str, user: str) -> list[str]:
    """Each line of a view that says `<name>, ... <tie> ...` where the records of the day give
    `pid` no such tie to that person, read from what the jobs, lets and obligations say."""
    from townsim.town import jobs as J
    from townsim.town.obligations import SCHEDULED, owed_dues
    names = {p.name: p.id for p in s.all("people")}
    home = s.get("people", pid).household
    owned = {o.id for o in s.all("orgs") if o.owner == pid}
    mine_jobs = {j.org for j in J.held(s, person=pid)}

    def agreement(payer_side: set, payee_side: set) -> bool:
        return any(o.kind == "agreement" and o.payer in payer_side and o.payee in payee_side
                   and (o.status in SCHEDULED or owed_dues(s, o.id)) for o in s.all("obligations"))

    def holds(other: str, kind: str) -> bool:
        q = s.get("people", other)
        theirs = {j.org for j in J.held(s, person=other)}
        their_owned = {o.id for o in s.all("orgs") if o.owner == other}
        if kind == "employee":
            return bool(theirs & owned)
        if kind == "employer":
            return bool(mine_jobs & their_owned)
        if kind == "coworker":
            return bool(mine_jobs & theirs)
        if kind == "landlord":
            return s.get("households", home).landlord in (other, q.household)
        if kind == "tenant":
            return s.get("households", q.household).landlord in (pid, home)
        if kind == "creditor":
            return agreement({home} | owned, {q.household} | their_owned)
        if kind == "debtor":
            return agreement({q.household} | their_owned, {home} | owned)
        banks = [o for o in s.all("orgs") if o.kind == "bank"]
        if kind == "banker":
            return any(b.contact == other and any(o.kind == "loan" and o.payer in {home} | owned and o.payee == b.id
                                                  and (o.status in SCHEDULED or owed_dues(s, o.id))
                                                  for o in s.all("obligations")) for b in banks)
        return any(b.contact == pid and any(o.kind == "loan" and o.payer in {q.household} | their_owned
                                            and o.payee == b.id and (o.status in SCHEDULED or owed_dues(s, o.id))
                                            for o in s.all("obligations")) for b in banks)
    found = []
    for line in (system + "\n" + user).split("\n"):
        name, _, rest = line.partition(", ")
        if name not in names or not rest or names[name] == pid:
            continue
        for words, kind in _SAID.items():
            if words in rest and not holds(names[name], kind):
                found.append(line)
    return found
