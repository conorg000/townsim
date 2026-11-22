"""Three arms forked from one morning of a run: no product (`none`); the product launched, adopted
or not by people's choice (`choice`); and the product launched with a few clients drawn from the
seed alone, who have it from launch day with every system of theirs connected (`drawn`).

The arms differ only in what `fork_arm`'s setup does on the morning they are forked from.
"""

from __future__ import annotations

import math
import random

from townsim.town import connections, gen, obligations, product
from townsim.town.clock import date_text
from townsim.town.connections import SCOPES, client_name
from townsim.town.entities import Connection
from townsim.town.obligations import money
from townsim.town.rundb import RunDB
from townsim.town.runs import fork
from townsim.town.store import Store
from townsim.town.systems import ADULT, can_grant, systems_of_client

ARMS = ("none", "choice", "drawn")
NOTES = ("families", "seed", "deck_weeks", "start")
CAUSE = "routine:arms:drawn"
LIMIT_SHARES = (0.10, 0.25, 0.50)       # a weekly limit, as a share of what the system pays out in an ordinary week
LIMIT_ROUND = 1000                      # rounded up to $10
LIMIT_LEAST = 2000                      # and at least $20


def _adults(store: Store, household: str) -> list[str]:
    return [p for p in store.get("households", household).members
            if store.has("people", p) and store.get("people", p).age >= ADULT]


def _owners(store: Store) -> set[str]:
    return {o.owner for o in store.all("orgs") if o.owner and not o.outside}


def _lets(store: Store, household: str) -> bool:
    members = set(store.get("households", household).members) | {household}
    return any(h.landlord in members for h in store.all("households"))


def _weekly_obligations(store: Store, account: str, kind: str | None = None) -> int:
    """What the account's regular payments come to in a week (a fee every 4 weeks a quarter of it)."""
    return sum(obligations.weekly_cents(o) for o in store.where("obligations", payer=account)
               if o.status == "active" and (kind is None or o.kind == kind))


def _stock_order(store: Store, org: str) -> int:
    """What a shop's standing order costs a week, at its supplier's price today."""
    total = 0
    for f in store.where("firms", org=org):
        supplier, units = (f.order_supplier, f.order_units) if f.order_units else (f.last_supplier, f.last_units)
        total += units * store.get("suppliers", supplier).unit_cents if store.has("suppliers", supplier) else 0
    return total


def weekly_outgoings(store: Store, system_id: str) -> int:
    """What a system pays out in an ordinary week: a shop's stock order for its ordering, its wages
    for its staff and pay, and for anything else the account's weekly payments (a household's with
    its food, a business's with its stock order)."""
    kind, owner = system_id.split(":", 1)
    if store.has("households", owner):
        return gen.weekly_out(store, owner)
    if kind == "ordering":
        return _stock_order(store, owner)
    if kind == "staff":
        return _weekly_obligations(store, owner, "wage")
    return _weekly_obligations(store, owner) + _stock_order(store, owner)


def _limit(store: Store, system_id: str, share: float) -> int:
    cents = math.ceil(weekly_outgoings(store, system_id) * share / LIMIT_ROUND) * LIMIT_ROUND
    return max(LIMIT_LEAST, cents)


def _scope_text(scope: str, cents: int) -> str:
    return {"read": "read only", "limit": f"weekly limit {money(cents)}", "free": "free"}[scope]


def draw_clients(store: Store) -> list[str]:
    """Subscribe the drawn clients to the launched product, drawn only from the seed: two of the
    shops, one household that owns no business (none of the firm's people, the landlord or the
    banker), and the landlord's household. Each has every system connected, each at a scope drawn
    evenly from read only, a weekly limit and free (from read only and free for letters and prices,
    which move no money); each adult of each is told once. Returns the
    subscriptions' ids."""
    rng = random.Random(f"arms:{store.seed}:drawn")
    pr = product.launched(store)
    owners = _owners(store)
    company = set(product.company_people(store, pr.org))
    shops = [o for o in store.all("orgs") if o.kind == "shop" and not o.outside and o.owner]
    picked = [(o.id, o.owner) for o in rng.sample(shops, 2)]
    plain = [h for h in store.all("households")
             if not set(h.members) & (owners | company) and not _lets(store, h.id)
             and not any(store.has("people", p) and store.get("people", p).kind == "banker" for p in h.members)]
    lords = [h for h in store.all("households") if _lets(store, h.id) and h.id not in {c for c, _ in picked}]
    for group in (plain, lords):
        h = rng.choice(group)
        picked.append((h.id, rng.choice(_adults(store, h.id))))
    subs = []
    for client, by in picked:
        product.subscribe(store, by, client, "", CAUSE, tell=False)
        sub = product.subscription_of(store, client)
        inst = store.get("instances", sub.instance)
        made: dict[str, list[str]] = {}
        for system in systems_of_client(store, client):
            scope = rng.choice(("read", "free") if system.kind in connections.MOVES_NO_MONEY else SCOPES)
            share = rng.choice(LIMIT_SHARES)
            cents = _limit(store, system.id, share) if scope == "limit" else 0
            grantor = by if can_grant(store, by, system.id) else next(
                p.id for p in store.all("people") if can_grant(store, p.id, system.id))
            store.add("connections", Connection(store.new_id("cn", CAUSE), inst.id, system.id, grantor, scope, cents,
                                                store.day), CAUSE)
            made.setdefault(grantor, []).append(f"{system.short} ({_scope_text(scope, cents)})")
        fee = store.get("obligations", sub.fee_ob)
        connected = " ".join(f"Connected by {store.get('people', g).name}: {'; '.join(names)}."
                             for g, names in made.items())
        text = (f"{store.get('people', by).name} subscribed {client_name(store, client)} to {pr.name} today ({sub.id}): "
                f"free until {date_text(sub.day + 7 * pr.free_weeks - 1)}, then {money(fee.cents)} {product.period(pr)} from "
                f"{product.account_name(store, client)}. {connected} Asked of it when subscribing: nothing written.")
        for pid in obligations.people_of(store, client):
            obligations.notify(store, pid, text, CAUSE)
        subs.append(sub.id)
    return subs


def _setup(arm: str):
    if arm == "none":
        return None
    if arm == "choice":
        return lambda store: (product.launch(store), None)[1]
    return lambda store: (product.launch(store), draw_clients(store), None)[2]


def fork_arm(db: RunDB, parent: str, at_day: int, new_id: str, arm: str, start: str | None = None) -> Store:
    """Fork `parent` on the morning of `at_day` (a Monday) into an arm, with the parent's notes
    (its start among them). A `start` given must be the parent's."""
    from townsim.town.live import check_start
    if arm not in ARMS:
        raise ValueError(f"arm {arm!r} is not one of {ARMS}")
    if at_day % 7 != 0:
        raise ValueError(f"day {at_day} is not a Monday")
    check_start(db, parent, start)
    store = fork(db, parent, at_day, new_id, setup=_setup(arm))
    for name in NOTES:
        value = db.note(parent, name)
        if value is not None:
            db.put_note(new_id, name, value)
    db.put_note(new_id, "arm", arm)
    return store
