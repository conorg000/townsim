"""Who a person is tied to, as the records stand today (design 3.3, 4.2).

Family and friendship are kept as rows (`Tie`), as the place was made. Every other tie is
read from the records on the day it is asked about, so it follows what has happened:

- `employer`, `employee` and `coworker` from the jobs held today: the owner of where one
  works is their employer, those who work there for an owner are their employees, and
  others who work in the same place are coworkers;
- `landlord` and `tenant` from who lets the house a household lives in;
- `creditor` and `debtor` from agreements in force between two accounts, or with something
  still owed on them: the adults of a household, or the owner of a business, on each side;
- `banker` from a loan from the bank in force, and `borrower` the other way;
- `former_employee` and `former_employer`, with the last day worked, from a job that ended
  (and no job held there today).

Nothing here changes the store.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable

from townsim.town import jobs
from townsim.town.obligations import SCHEDULED, owed_dues
from townsim.town.store import Store

PERSONAL = ("partner", "parent", "child", "sibling", "kin", "friend")
ADULT = 18


def _people_of(store: Store, account: str) -> list[str]:
    """Who stands for an account in a tie: a household's adults, an organisation's owner."""
    if store.has("households", account):
        return [p for p in store.get("households", account).members
                if store.has("people", p) and store.get("people", p).age >= ADULT]
    if store.has("orgs", account):
        owner = store.get("orgs", account).owner
        return [owner] if owner and store.has("people", owner) else []
    return []


def _in_force(store: Store, ob) -> bool:
    return ob.status in SCHEDULED or bool(owed_dues(store, ob.id))


_KEPT: "weakref.WeakKeyDictionary[Store, dict]" = weakref.WeakKeyDictionary()


def kept(store: Store, key: tuple, build: Callable[[], Any]) -> Any:
    """What `build()` gives, kept while the store does not change (reading changes nothing,
    so a view built many times over reads the same records). Callers do not change it."""
    last = store.changes[-1] if store.changes else None
    state = (store.mark(), id(last), last and (last.cause, last.table, last.id, last.field))
    mine = _KEPT.get(store)
    if mine is None or mine[0] != state:
        mine = (state, {})
        _KEPT[store] = mine
    if key not in mine[1]:
        mine[1][key] = build()
    return mine[1][key]


def kinds(store: Store, pid: str) -> dict[str, list[str]]:
    """How `pid` sees each person tied to them today: their own family and friendship rows,
    then the ties read from the records, each kind once, in the order found."""
    return kept(store, ("kinds", pid), lambda: _kinds(store, pid))


def _kinds(store: Store, pid: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}

    def add(other: str, kind: str) -> None:
        if other and other != pid and store.has("people", other):
            got = out.setdefault(other, [])
            if kind not in got:
                got.append(kind)

    for t in store.all("ties"):
        if t.person == pid and t.kind in PERSONAL:
            add(t.other, t.kind)
    person = store.get("people", pid)
    home = person.household
    held = jobs.held(store, person=pid)
    for j in held:
        if not store.has("orgs", j.org):
            continue
        owner = store.get("orgs", j.org).owner
        if owner and owner != pid:
            add(owner, "employer")
        if owner == pid:
            continue
        for other in jobs.held(store, org=j.org):
            if other.person != owner:
                add(other.person, "coworker")
    owned = [o.id for o in store.all("orgs") if o.owner == pid]
    for org in owned:
        for other in jobs.held(store, org=org):
            add(other.person, "employee")
    now = {(j.person, j.org) for org in owned for j in jobs.held(store, org=org)}
    for org in owned:
        for j in store.where("jobs", org=org):
            if j.person != pid and 0 <= j.ended < store.day and (j.person, org) not in now:
                add(j.person, f"former_employee:{j.ended}")
    working = {j.org for j in held}
    for j in store.where("jobs", person=pid):
        if 0 <= j.ended < store.day and j.org not in working and store.has("orgs", j.org):
            add(store.get("orgs", j.org).owner, f"former_employer:{j.ended}")
    for h in store.all("households"):
        if h.landlord in (pid, home) and h.id != home:
            for p in h.members:
                add(p, "tenant")
    lord = store.get("households", home).landlord
    if lord:
        for p in ([lord] if store.has("people", lord) else
                  store.get("households", lord).members if store.has("households", lord) else []):
            add(p, "landlord")
    mine = {home, *owned}
    for account in mine:
        for ob in store.where("obligations", payer=account):
            if ob.kind == "agreement" and ob.payee not in mine and _in_force(store, ob):
                for p in _people_of(store, ob.payee):
                    add(p, "creditor")
            elif ob.kind == "loan" and store.has("orgs", ob.payee) and store.get("orgs", ob.payee).kind == "bank" \
                    and _in_force(store, ob):
                add(store.get("orgs", ob.payee).contact, "banker")
        for ob in store.where("obligations", payee=account):
            if ob.kind == "agreement" and ob.payer not in mine and _in_force(store, ob):
                for p in _people_of(store, ob.payer):
                    add(p, "debtor")
    for bank in store.all("orgs"):
        if bank.kind == "bank" and bank.contact == pid:
            for ob in store.where("obligations", payee=bank.id):
                if ob.kind == "loan" and _in_force(store, ob):
                    for p in _people_of(store, ob.payer):
                        add(p, "borrower")
    return out


def theirs(store: Store, pid: str) -> dict[str, list[str]]:
    """The family and friendship rows others hold toward `pid`, by who holds them."""
    return kept(store, ("theirs", pid), lambda: _theirs(store, pid))


def _theirs(store: Store, pid: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for t in store.all("ties"):
        if t.other == pid and t.kind in PERSONAL and t.person != pid:
            out.setdefault(t.person, []).append(t.kind)
    return out


def tied(store: Store, pid: str) -> set[str]:
    """Everyone tied to `pid` either way today. (A set for membership only: never iterated.)"""
    return set(kinds(store, pid)) | set(theirs(store, pid))


def base(kind: str) -> str:
    """A kind without the day a former tie ended: `former_employee:40` gives `former_employee`."""
    return kind.split(":", 1)[0]
