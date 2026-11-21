"""Shared set-up for the town tests."""

from townsim.econ.ledger import Transfer
from townsim.town import obligations as O
from townsim.town.entities import Household, Org, Person
from townsim.town.store import Store

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
    """Wenlow: the Ashbys (h1: Ruth p01, Tom p02; $600) and the Pryces (h2: Craig p03,
    Megan p04; $300), their landlord org1 and energy company org2, with wages, rent,
    energy and spending on weekly schedules. Task 7 moves this into the package."""
    s = Store()
    s.place = "Wenlow"
    s.open_account("outside:employers", "routine:setup", allow_credit=True)
    s.open_account("outside:shops", "routine:setup")
    for org, name, kind in (("org1", "Wenlow Lettings", "landlord"), ("org2", "Northgrid Energy", "utility")):
        s.open_account(org, "routine:setup")
        s.add("orgs", Org(org, name, kind), "routine:setup")
    people = {"h1": ("Ashby", [("p01", "Ruth", 4), ("p02", "Tom", 3)]),
              "h2": ("Pryce", [("p03", "Craig", 4), ("p04", "Megan", 5)])}
    for h, (surname, members) in people.items():
        s.open_account(h, "routine:setup")
        s.add("households", Household(h, surname, [p for p, _, _ in members]), "routine:setup")
        for p, first, stock_day in members:
            s.add("people", Person(p, f"{first} {surname}", h, 12.0, 12.0, stock_day), "routine:setup")
    for h, cents in (("h1", 60000), ("h2", 30000)):
        s.transfer("outside:employers", h, cents, "opening", "routine:setup")
    for h, wage, rent in (("h1", 52000, 21000), ("h2", 33000, 18500)):
        O.create(s, kind="wage", payer="outside:employers", payee=h, cents=wage, first=4, every=7,
                 memo="wages", ends="both", **SETUP)
        O.create(s, kind="rent", payer=h, payee="org1", cents=rent, first=0, every=7,
                 memo="rent", ends="both", **SETUP)
        O.create(s, kind="bill", payer=h, payee="org2", cents=4800, first=2, every=7,
                 memo="energy", ends="service", **SETUP)
        O.create(s, kind="bill", payer=h, payee="outside:shops", cents=23000, first=5, every=7,
                 memo="food and regular spending", ends="service", **SETUP)
    return s
