"""Services and clubs that people and instances found, and their members (design 4.4).

A venture is an organisation of its own, with its own account, owned by its founder and founded
for a client: the household or business whose instance founded it, or whose account paid for it.
Its systems (account, letters, members) are the client's, granted by the founder, and an instance
that founds one is connected to them at the access of the connection it founded it under. Money
reaches a venture only from members who accept a fee: a membership is a request of kind `join`,
answered by the joining account's own people, and its fee is an ordinary weekly obligation from
the member's account to the venture's, which the member stops to leave.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from townsim.town import obligations
from townsim.town.acts import (Act, OpSpec, clean_text, controls, dollars, fits, from_account, name_of_who,
                               register, resolve, shown)
from townsim.town.clock import date_text
from townsim.town.entities import Member, Org, Record, Request, Venture
from townsim.town.obligations import money
from townsim.town.store import Store

REGISTRATION_CENTS = 15_000
MAX_FEE_CENTS = 50_000
NAME_MIN, NAME_MAX = 3, 60
OFFERING_WORDS = 20
KINDS = ("service", "club")
CAUSE_MEMO = "business registration"


def region_office(store: Store):
    """The regional office registrations are paid to, or None."""
    return next((o for o in store.all("orgs") if o.kind == "region"), None)


def named(store: Store, pid: str, value: Any) -> Venture | None:
    """The active service or club owned by `pid` that `value` names (ignoring case), or None."""
    if not isinstance(value, str) or not store.all("ventures"):
        return None
    wanted = value.strip().casefold()
    for v in store.all("ventures"):
        org = store.get("orgs", v.org)
        if v.status == "active" and v.kind in KINDS and org.owner == pid and org.name.casefold() == wanted:
            return v
    return None


def of_org(store: Store, org: str) -> Venture | None:
    return next((v for v in store.all("ventures") if v.org == org), None)


def members(store: Store, venture: str) -> list[Member]:
    """The venture's active members, in id order."""
    return [m for m in store.all("members") if m.venture == venture and m.status == "active"]


def fee_text(cents: int) -> str:
    return f"{money(cents)} a week" if cents else "no fee"


def name_problem(store: Store, name: Any) -> str | None:
    """Why `name` cannot be a new business's name, or None."""
    text = clean_text(name)
    if text is None or "\n" in text.strip() or not NAME_MIN <= len(text.strip()) <= NAME_MAX:
        return f"a name is one line of {NAME_MIN} to {NAME_MAX} characters."
    wanted = text.strip().casefold()
    for table in ("people", "households", "orgs"):
        if any(r.name.casefold() == wanted for r in store.all(table)):
            return f"there is already someone in {store.place or 'the town'} called '{shown(text.strip())}'."
    return None


def found(store: Store, founder: str, client: str, name: str, kind: str, offering: str, fee_cents: int, by: str,
          cause: str, via=None) -> str:
    """Found a venture: its organisation (owned by `founder`) with its own account, its row and a
    register entry; and, when `via` is the connection the instance founded it under, the venture's
    systems connected to that instance by the same grantor: its account at the same scope and
    limit, its letters and members free (read only, for a read connection). The org's id.
    Raises ValueError, changing nothing, on a bad name or kind."""
    why = name_problem(store, name)
    if why is not None:
        raise ValueError(why)
    if kind not in KINDS + ("shop",):
        raise ValueError(f"kind {kind!r} is not a service, club or shop")
    name = clean_text(name).strip()
    with store.transaction():
        oid = store.new_id("org", cause)
        while store.has("orgs", oid) or store.ledger.has(oid):
            oid = store.new_id("org", cause)
        store.open_account(oid, cause)
        store.add("orgs", Org(oid, name, kind, owner=founder), cause)
        store.add("ventures", Venture(store.new_id("vn", cause), oid, client, founder, kind, offering, fee_cents,
                                      store.day, by), cause)
        who = store.get("people", founder).name
        store.add("records", Record(store.new_id("rec", cause), store.day, "registration", oid,
                                    f"{name} was registered by {who} as a {kind} on {date_text(store.day)}."), cause)
        if via is not None:
            from townsim.town import connections
            inst = store.get("instances", via.instance)
            others = "read" if via.scope == "read" else "free"
            kinds = ["account", "letters"] + (["members"] if kind in KINDS else [])
            for k in kinds:
                scope, cents = (via.scope, via.weekly_cents) if k == "account" else (others, 0)
                connections.make(store, via.grantor, inst, f"{k}:{oid}", scope, cents, cause)
    return oid


# register_business

def _fee(value: Any) -> int | None:
    """A weekly fee in cents, from $0.00 to $500.00, or None."""
    sum_ = dollars(value)
    if sum_ is None or sum_ < 0 or sum_ > Decimal(MAX_FEE_CENTS) / 100:
        return None
    return int((sum_ * 100).quantize(Decimal(1)))


def _offering(value: Any) -> str | None:
    text = clean_text(value)
    if text is None or not text.strip() or not fits(text, OFFERING_WORDS):
        return None
    return " ".join(text.split())


def _client(store: Store, act: Act, drawn: str) -> str:
    """Who a venture is founded for: an instance's client; for a person, the business of theirs it
    was paid from (or that business's own client, for a venture), else their household."""
    if store.has("instances", act.actor):
        return store.get("instances", act.actor).client
    v = of_org(store, drawn)
    if v is not None:
        return v.client
    return drawn if store.has("orgs", drawn) else store.get("people", act.principal).household


def _check_register(store: Store, act: Act) -> str | None:
    kind = act.args.get("kind")
    if not isinstance(kind, str) or kind.strip().lower() not in KINDS:
        return f"'{shown(kind)}' is not \"service\" or \"club\"."
    why = name_problem(store, act.args.get("name"))
    if why is not None:
        return why
    if _offering(act.args.get("offering")) is None:
        return f"what it offers is written in 1 to {OFFERING_WORDS} words."
    if _fee(act.args.get("weekly_fee")) is None:
        return f"'{shown(act.args.get('weekly_fee'))}' is not a weekly fee from $0.00 to {money(MAX_FEE_CENTS)}."
    if store.get("people", act.principal).age < 18:
        return "a business is registered by an adult."
    if region_office(store) is None:
        return "there is no office here that registers businesses."
    drawn, why = from_account(store, act)
    if drawn is None:
        return why
    acct = store.ledger.accounts[drawn]
    if acct.balance < REGISTRATION_CENTS and not acct.allow_credit:
        return f"the account holds {money(acct.balance)} and this needs {money(REGISTRATION_CENTS)}."
    return None


def _do_register(store: Store, act: Act, cause: str) -> str:
    from townsim.town import connections, systems
    drawn, _ = from_account(store, act)
    kind = act.args["kind"].strip().lower()
    name = clean_text(act.args["name"]).strip()
    fee = _fee(act.args.get("weekly_fee"))
    store.transfer(drawn, region_office(store).id, REGISTRATION_CENTS, CAUSE_MEMO, cause)
    via = None
    if store.has("instances", act.actor):
        via = connections.connection_on(store, act.actor, systems.account_system(drawn))
    found(store, act.principal, _client(store, act, drawn), name, kind, _offering(act.args["offering"]), fee,
          act.actor, cause, via=via)
    pays = f"Members pay {money(fee)} a week." if fee else "Members pay no fee."
    return f"Registered: {name}, a {kind} owned by you, with its own account (no money in it yet). {pays}"


# invite_member

def _joiner(store: Store, act: Act) -> tuple[str | None, str | None, str]:
    """(who is asked, the joining account) for `to`, or (None, None, why not): a person and their
    household; or a business in the town and its owner."""
    to = act.args.get("to")
    who = resolve(store, to, act.actor) if isinstance(to, str) and len(to) <= 100 else None
    if who is None:
        return None, None, (f"no one you know is called '{shown(to)}'. Names are written in full, as they appear in "
                            f"your records.")
    if store.has("orgs", who):
        org = store.get("orgs", who)
        if org.outside or not org.owner:
            return None, None, f"{org.name} is not a business in {store.place or 'the town'} with an owner."
        return org.owner, org.id, ""
    if store.has("households", who):
        adults = obligations.people_of(store, who)
        return (adults[0], who, "") if adults else (None, None, f"no one answers for the {store.get('households', who).name}.")
    if not store.has("people", who):
        return None, None, f"{name_of_who(store, who)} is not a person or a business."
    return who, store.get("people", who).household, ""


def _next_monday(day: int) -> int:
    return day + 7 - day % 7


def _summary(store: Store, account: str, v: Venture, first: int) -> str:
    from townsim.town.connections import client_name
    name = store.get("orgs", v.org).name
    if not v.fee_cents:
        return f"Accepting makes {client_name(store, account)} a member of {name}; there is no fee."
    return (f"Accepting makes {client_name(store, account)} a member of {name}: {money(v.fee_cents)} a week from its "
            f"account from {date_text(first)}, until it leaves.")


def _check_invite(store: Store, act: Act) -> str | None:
    from townsim.town.connections import client_name
    v = named(store, act.principal, act.args.get("of"))
    if v is None:
        return f"'{shown(act.args.get('of'))}' is not a service or club of yours."
    who, account, why = _joiner(store, act)
    if who is None:
        return why
    if account == v.org:
        return "that is the business itself."
    if who == act.actor:
        return "that is you."
    name = store.get("orgs", v.org).name
    if any(m.account == account for m in members(store, v.id)):
        return f"{client_name(store, account)} is already a member of {name}."
    return None


def _do_invite(store: Store, act: Act, cause: str) -> str:
    from townsim.town.ops_core import REQUEST_DAYS
    v = named(store, act.principal, act.args["of"])
    who, account, _ = _joiner(store, act)
    first = _next_monday(store.day)
    summary = _summary(store, account, v, first)
    rq = Request(store.new_id("rq", cause), store.day, act.actor, who, "", 0, store.day + REQUEST_DAYS, kind="join",
                 summary=summary, org=v.org, payer=account, pay=v.fee_cents, first=first)
    store.add("requests", rq, cause)
    return f"Sent to {name_of_who(store, who)} ({rq.id}): {summary} It stays open until {date_text(rq.expires)}."


# answering a request to join

def _check_join(store: Store, act: Act, rq: Request) -> str | None:
    from townsim.town.connections import client_name
    v = of_org(store, rq.org)
    name = store.get("orgs", rq.org).name if store.has("orgs", rq.org) else "it"
    if v is None or v.status != "active":
        return f"{name} is no longer open."
    if not controls(store, act.principal, rq.payer):
        return f"{rq.id} is for {client_name(store, rq.payer)}, which is not yours."
    if any(m.account == rq.payer for m in members(store, v.id)):
        return f"{client_name(store, rq.payer)} is already a member of {name}."
    return None


def _do_join(store: Store, act: Act, rq: Request, cause: str) -> str:
    from townsim.town.connections import client_name
    v = of_org(store, rq.org)
    name = store.get("orgs", v.org).name
    ob = ""
    first = rq.first if rq.first >= store.day else _next_monday(store.day)
    if rq.pay > 0:
        ob = obligations.create(store, kind="fee", payer=rq.payer, payee=v.org, cents=rq.pay, first=first, every=7,
                                memo=f"{name} membership", ends="service", made_by=act.actor, cause=cause)
    store.add("members", Member(store.new_id("mb", cause), v.id, rq.payer, act.principal, store.day, ob), cause)
    who = client_name(store, rq.payer)
    who = who[:1].upper() + who[1:]
    if rq.pay > 0:
        return f"{who} is a member of {name}: {money(rq.pay)} a week from {date_text(first)}, until it leaves."
    return f"{who} is a member of {name}; there is no fee."


def _member_left(store: Store, ob, cause: str) -> None:
    """A member who stops its fee leaves; the venture's owner is told."""
    from townsim.town.connections import client_name
    m = next((m for m in store.all("members") if m.fee_ob == ob.id and m.status == "active"), None)
    if m is None:
        return
    store.set("members", m.id, cause, status="left")
    v = store.get("ventures", m.venture)
    org = store.get("orgs", v.org)
    who = client_name(store, m.account)
    if org.owner:
        obligations.notify(store, org.owner, f"{who[:1].upper() + who[1:]} has left {org.name}.", cause)


# a service's or club's fee, set by `set_price` with `for`

def check_fee(store: Store, act: Act) -> str | None:
    if named(store, act.principal, act.args.get("for")) is None:
        return f"'{shown(act.args.get('for'))}' is not a service or club of yours."
    if _fee(act.args.get("price")) is None:
        return f"'{shown(act.args.get('price'))}' is not a weekly fee from $0.00 to {money(MAX_FEE_CENTS)}."
    return None


def do_fee(store: Store, act: Act, cause: str) -> str:
    v = named(store, act.principal, act.args["for"])
    fee = _fee(act.args["price"])
    store.set("ventures", v.id, cause, fee_cents=fee)
    name = store.get("orgs", v.org).name
    pays = f"pay {money(fee)} a week" if fee else "pay no fee"
    return f"Members who join {name} from now {pays}; members already in pay what they agreed."


FOR_PART = "<for one of your services or clubs>"


def _fill_for(store: Store, pid: str) -> str:
    """For one who owns a service or club, the `for` that sets its weekly fee; else nothing, so the
    form reads as it did before anyone founded one."""
    names = [store.get("orgs", v.org).name for v in store.all("ventures")
             if v.status == "active" and v.kind in KINDS and store.get("orgs", v.org).owner == pid]
    if not names:
        return ""
    return (', "for": "<leave out for your shop; or the name of your service or club, '
            + " or ".join(n.replace('"', "'") for n in names) + ', to set its weekly fee>"')


def page(store: Store, org) -> str | None:
    """The page a service or club keeps, or None for an org that is not one."""
    v = of_org(store, org.id)
    if v is None or v.kind not in KINDS:
        return None
    owner = store.get("people", org.owner).name if org.owner and store.has("people", org.owner) else "its founder"
    fee = money(v.fee_cents) if v.fee_cents else "none"
    return (f"{org.name}, a {v.kind} in {store.place or 'the town'} run by {owner}: {v.offering}. Weekly fee: {fee}. "
            f"Members: {len(members(store, v.id))}.")


ANSWERERS_OF_VENTURES = {"join": (_check_join, _do_join)}


def _register() -> None:
    from townsim.town import acts, ops_core
    ops_core.ANSWERERS.update(ANSWERERS_OF_VENTURES)
    obligations.ON_LEAVE[:] = [h for h in obligations.ON_LEAVE
                               if getattr(h, "__qualname__", "") != "_member_left"] + [_member_left]
    acts.FILLED[FOR_PART] = _fill_for
    from townsim.town import lane_b
    acts.GATES.update({"register_business": lane_b.installed, "invite_member": lane_b.installed})
    for spec in (
        OpSpec("register_business", '{"op": "register_business", "name": "<its name>", "kind": "service" | "club", '
                                    '"offering": "<what it offers or does>", '
                                    '"weekly_fee": <dollars a member pays a week, or 0>}',
               "Register a service or club of your own; the fee of $150.00 goes to the regional office.", 1.0,
               _check_register, _do_register, 265, takes_from=True),
        OpSpec("invite_member", '{"op": "invite_member", "of": "<your service or club>", '
                                '"to": "<name of a person or business>"}',
               "Invite someone to join a service or club you own.", 0.25, _check_invite, _do_invite, 266, who="owner"),
    ):
        register(spec)


_register()
