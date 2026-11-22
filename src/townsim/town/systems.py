"""Systems: what a client has that can be connected to an instance (design 4.1).

A system is one of a client's accounts, letters, a shop's ordering or prices, a business's
staff and pay, the houses a household lets, or the bank's loan book. Its id is
`<kind>:<owner id>`. `SYSTEM_OF` says, for every operation, which system an act of it uses,
worked out with the act's principal; `OWN` lists what is only ever a person's own doing.
Every registered operation is in exactly one of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from townsim.town import acts
from townsim.town.acts import Act, account_of, controls, from_account, has_savings, is_savings
from townsim.town.store import Store

KINDS = ("account", "letters", "ordering", "prices", "staff", "lets", "loans", "members")
# what each kind of system is called as an integration of the product (the seat's words for it)
INTEGRATION = {"account": "Bank", "letters": "Email", "ordering": "Ordering", "prices": "Point of sale",
               "staff": "Payroll", "lets": "Lettings", "loans": "Loan book", "members": "Members"}
# the systems of a venture, by its kind, listed under its client (`systems_of_client`)
VENTURE_KINDS = {"service": ("account", "letters", "members"), "club": ("account", "letters", "members"),
                 "shop": ("account", "letters", "ordering", "prices", "staff")}
ADULT = 18


@dataclass(frozen=True)
class System:
    id: str
    kind: str
    owner: str
    short: str
    long: str


def _adults(store: Store, household: str) -> list[str]:
    return [p for p in store.get("households", household).members
            if store.has("people", p) and store.get("people", p).age >= ADULT]


def _bank(store: Store, household: str):
    """The bank in the town whose contact is of `household`, or None."""
    members = set(store.get("households", household).members)
    return next((o for o in store.all("orgs") if o.kind == "bank" and not o.outside and o.contact in members), None)


def _lets(store: Store, household: str) -> bool:
    members = set(store.get("households", household).members) | {household}
    return any(h.landlord in members for h in store.all("households"))


def _letter(store: Store, household: str) -> str:
    """Who lets houses for `household`: the member named as landlord, or the household."""
    members = store.get("households", household).members
    lords = {h.landlord for h in store.all("households")}
    who = next((p for p in members if p in lords), None)
    return store.get("people", who).name if who else f"the {store.get('households', household).name}"


def venture_of(store: Store, org: str):
    """The venture whose organisation `org` is, or None."""
    return next((v for v in store.all("ventures") if v.org == org), None)


def own_client(store: Store, v) -> bool:
    """Whether a venture is a client of its own: it holds an active or paused subscription in its own name, so
    its systems are its subscription's, not its founding client's."""
    return any(sub.client == v.org and sub.status == "active" for sub in store.all("subscriptions"))


def ventures_of(store: Store, client: str) -> list:
    """The client's ventures, in id order (every status)."""
    return [v for v in store.all("ventures") if v.client == client] if store.all("ventures") else []


def system(store: Store, system_id: str) -> System | None:
    """The system `system_id` names, if it is one the store has."""
    if not isinstance(system_id, str) or ":" not in system_id:
        return None
    kind, owner = system_id.split(":", 1)
    if kind == "loans":
        if not store.has("orgs", owner) or store.get("orgs", owner).kind != "bank":
            return None
        bank = store.get("orgs", owner)
        contact = bank.contact if store.has("people", bank.contact) else ""
        return System(system_id, kind, owner, "loan book", f"the loan book of {bank.name}") if contact else None
    if store.has("households", owner):
        name = store.get("households", owner).name
        savings = " and savings" if has_savings(store, owner) else ""
        texts = {"account": ("household account", f"the {name}'s account{savings}"),
                 "letters": ("household letters", f"letters to the {name}"),
                 "lets": ("lets", f"the houses {_letter(store, owner)} lets")}
        if kind not in texts or (kind == "lets" and not _lets(store, owner)):
            return None
        return System(system_id, kind, owner, *texts[kind])
    if store.has("orgs", owner):
        org = store.get("orgs", owner)
        if org.outside or not org.owner:
            return None
        if kind == "members":
            v = venture_of(store, owner)
            if v is None or v.kind not in ("service", "club"):
                return None
            return System(system_id, kind, owner, f"{org.name} members", f"the members of {org.name}")
        shop = bool(store.where("firms", org=owner))
        texts = {"account": (f"{org.name} account", f"{org.name}'s account"),
                 "letters": (f"{org.name} letters", f"letters to {org.name}"),
                 "ordering": (f"{org.name} ordering", f"the stock orders of {org.name}"),
                 "prices": (f"{org.name} prices", f"the prices of {org.name}"),
                 "staff": (f"{org.name} staff and pay", f"the staff and pay of {org.name}")}
        if kind not in texts or (kind in ("ordering", "prices") and not shop):
            return None
        return System(system_id, kind, owner, *texts[kind])
    return None


def systems_of_client(store: Store, client: str) -> list[System]:
    """A household's or a business's systems, in `KINDS` order; then, for each of its active
    ventures, the venture's own (`VENTURE_KINDS`). A venture is no client: it has none of its own."""
    own = venture_of(store, client)
    if own is not None:              # a venture is no client, unless it subscribes in its own name
        if not own_client(store, own):
            return []
        return [x for x in (system(store, f"{kind}:{own.org}") for kind in VENTURE_KINDS.get(own.kind, ())) if x]
    found = [system(store, f"{kind}:{client}") for kind in KINDS if kind not in ("loans", "members")]
    if store.has("households", client):
        bank = _bank(store, client)
        found.append(system(store, f"loans:{bank.id}") if bank else None)
    for v in ventures_of(store, client):
        if v.status == "active" and not own_client(store, v):
            found += [system(store, f"{kind}:{v.org}") for kind in VENTURE_KINDS.get(v.kind, ())]
    return [s for s in found if s is not None]


def client_of(store: Store, system_id: str) -> str:
    """The household or business whose system it is: for the loan book, the banker's household;
    for a venture's, the client it was founded for."""
    kind, owner = system_id.split(":", 1)
    if kind == "loans":
        return store.get("people", store.get("orgs", owner).contact).household
    v = venture_of(store, owner)
    if v is None:
        return owner
    return v.org if own_client(store, v) else v.client


def long_name(store: Store, system_id: str) -> str:
    found = system(store, system_id)
    return found.long if found else system_id


def can_grant(store: Store, pid: str, system_id: str) -> bool:
    """Whether `pid` controls the system: any adult of the household for its account and
    letters; the landlord or an adult of the landlord household for its lets; the bank's
    contact for the loan book; the owner for a business's systems."""
    found = system(store, system_id)
    if found is None or not isinstance(pid, str) or not store.has("people", pid):
        return False
    if found.kind == "loans":
        return store.get("orgs", found.owner).contact == pid
    if store.has("orgs", found.owner):
        return store.get("orgs", found.owner).owner == pid
    return pid in _adults(store, found.owner)        # a household's account, letters and lets


def grantable(store: Store, pid: str) -> list[System]:
    """Every system `pid` can connect: their household's, then each business they own."""
    clients = [store.get("people", pid).household] + [o.id for o in store.all("orgs") if o.owner == pid and not o.outside]
    found = [s for c in clients for s in systems_of_client(store, c) if can_grant(store, pid, s.id)]
    return list({s.id: s for s in found}.values())


def system_named(store: Store, pid: str, short: Any) -> System | None:
    """The system of `pid`'s that a short name, as they write it, names (ignoring case)."""
    if not isinstance(short, str):
        return None
    wanted = short.strip().casefold()
    return next((s for s in grantable(store, pid) if s.short.casefold() == wanted), None)


# which system an act uses

def account_system(account: str | None) -> str | None:
    """The account system an account belongs to: a household's savings are its account's."""
    if account is None:
        return None
    if is_savings(account):
        account = acts.household_of_savings(account)
    return f"account:{account}"


def _drawn(store: Store, act: Act) -> str | None:
    """The account the act draws on, as `from_account` reads it."""
    return account_system(from_account(store, act)[0])


def _obligation(store: Store, act: Act, key: str = "id"):
    ob_id = act.args.get(key)
    return store.get("obligations", ob_id) if isinstance(ob_id, str) and store.has("obligations", ob_id) else None


def _stop(store: Store, act: Act) -> str | None:
    """The account that pays what is stopped: an obligation's payer; anything else `stop`
    names (an appointment) is no one's account. A subscription is stopped from the account
    that pays its fee."""
    ob = _obligation(store, act)
    if ob is not None:
        return account_system(ob.payer)
    from townsim.town.ops_core import STOPPABLE
    rid = act.args.get("id")
    for table in STOPPABLE:
        if isinstance(rid, str) and store.has(table, rid):
            row = store.get(table, rid)
            fee = getattr(row, "fee_ob", "")
            if fee and store.has("obligations", fee):
                return account_system(store.get("obligations", fee).payer)
            return account_system(row.client) if hasattr(row, "fee_ob") and hasattr(row, "client") else None
    return _drawn(store, act)


def _repay(store: Store, act: Act) -> str | None:
    ob = _obligation(store, act)
    return account_system(ob.payer) if ob is not None else _drawn(store, act)


def _propose_end(store: Store, act: Act) -> str | None:
    """The client's side of the agreement: the one of its accounts the principal controls."""
    ob = _obligation(store, act)
    if ob is None:
        return _drawn(store, act)
    mine = [a for a in (ob.payer, ob.payee) if controls(store, act.principal, a)]
    return account_system(mine[0]) if mine else _drawn(store, act)


def _request(store: Store, act: Act) -> str | None:
    """A request asks money to be paid to the actor's account (an instance's is its client's)."""
    return account_system(account_of(store, act.actor))


def _move(store: Store, act: Act) -> str | None:
    account, _ = acts.account_named(store, act.principal, act.args.get("from"))
    return account_system(account) if account is not None else _drawn(store, act)


def _answer(store: Store, act: Act) -> str | None:
    """By the request's kind: what pays for a money ask, an agreement, an end and a loan is an
    account; an ask of no money is a letter; a loan asked of the banker is the loan book; a
    job, extra hours and a connection are the client's own to answer."""
    from townsim.town.ops_core import _request_accounts
    rid = act.args.get("request")
    if not isinstance(rid, str) or not store.has("requests", rid):
        return f"letters:{account_of(store, act.principal)}"
    rq = store.get("requests", rid)
    if rq.kind == "ask":
        if rq.cents > 0:
            return account_system(_request_accounts(store, rq)[0])
        return f"letters:{account_of(store, act.principal)}"
    if rq.kind == "agreement":
        return account_system(account_of(store, act.principal))
    if rq.kind == "end":
        ob = store.get("obligations", rq.ob)
        mine = [a for a in (ob.payer, ob.payee) if controls(store, act.principal, a)]
        return account_system(mine[0]) if mine else None
    if rq.kind == "loan":
        if store.has("orgs", rq.payee) and store.get("orgs", rq.payee).contact == act.principal:
            return f"loans:{rq.payee}"
        return account_system(rq.payer)
    if rq.kind == "join":                  # the joining account pays the fee
        return account_system(rq.payer)
    if rq.kind == "task":                  # agreeing to what someone's task asks: a letter's answer
        return f"letters:{account_of(store, act.principal)}"
    return None                        # job, shifts, connect: the client's own


def _business(kind: str) -> Callable[[Store, Act], str | None]:
    """The first organisation by id the principal owns that has a system of `kind`; or, when none
    has, the first they own (whose operations' own checks then say what it is not)."""
    def of(store: Store, act: Act) -> str | None:
        owned = [o for o in store.all("orgs") if o.owner == act.principal and not o.outside]
        having = [o for o in owned if system(store, f"{kind}:{o.id}") is not None]
        return f"{kind}:{(having or owned)[0].id}" if owned else None
    of.__name__ = of.__qualname__ = f"_{kind}_of"
    return of


def _venture_named(store: Store, act: Act, key: str) -> str | None:
    """The members system of the service or club of the principal's that `key` names, or None."""
    from townsim.town import ventures
    v = ventures.named(store, act.principal, act.args.get(key) if isinstance(act.args, dict) else None)
    return f"members:{v.org}" if v is not None else None


def _invite(store: Store, act: Act) -> str | None:
    return _venture_named(store, act, "of")


def _set_price(store: Store, act: Act) -> str | None:
    """A shop's prices; or, with `for`, the members of the service or club it names."""
    if isinstance(act.args, dict) and act.args.get("for") not in (None, ""):
        return _venture_named(store, act, "for")
    return _business("prices")(store, act)


def _lets_of(store: Store, act: Act) -> str | None:
    return f"lets:{store.get('people', act.principal).household}"


SYSTEM_OF: dict[str, Callable[[Store, Act], str | None]] = {
    "pay": _drawn, "standing_payment": _drawn, "stop": _stop, "request": _request,
    "propose_agreement": _drawn, "propose_end": _propose_end, "borrow": _drawn, "repay": _repay,
    "move_money": _move, "read": _drawn, "answer": _answer,
    "order_stock": _business("ordering"), "set_price": _set_price,
    "register_business": _drawn, "invite_member": _invite,
    "offer_job": _business("staff"), "offer_shifts": _business("staff"), "dismiss": _business("staff"),
    "set_staff_pay": _business("staff"),
    "set_rent": _lets_of, "serve_notice": _lets_of,
}

OWN = {"message", "converse", "book_appointment", "write_notes", "work_extra_hours", "quit", "subscribe", "connect",
       "disconnect", "read_page"}
