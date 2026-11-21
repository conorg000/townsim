"""Shared set-up for the town tests."""

from townsim.econ.ledger import Transfer
from townsim.town import obligations as O
from townsim.town.entities import Household, Org, Person, Tie
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
