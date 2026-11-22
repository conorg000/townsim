"""The product's two plans (lane B): free, and paid.

A subscription taken out with lane B starts on the free plan: no fee and no end date; nothing is connected
on it. Connecting anything (`connections.make`, whatever asks for it) moves the client to the paid plan from
that day: the product's price every `fee_days`, its first `free_weeks` weeks free the first time a client is on
it. When nothing is connected any more, the client returns to the free plan at the end of the period already
paid (`nothing_connected`, then `apply_due` on that morning); connecting again before then keeps the paid plan
as it was. Each change is a `Plan` row; a subscription without rows (the firm's own, or one taken out before
lane B) is outside the plans.
"""

from __future__ import annotations

from townsim.town import obligations
from townsim.town.clock import date_text
from townsim.town.entities import Plan
from townsim.town.obligations import money
from townsim.town.store import Store, id_key


def rows(store: Store, sub) -> list[Plan]:
    return sorted((p for p in store.all("plans") if p.subscription == sub.id), key=lambda p: id_key(p.id))


def current(store: Store, sub) -> Plan | None:
    """The subscription's plan now, or None for one outside the plans."""
    got = rows(store, sub)
    return got[-1] if got else None


def is_free(store: Store, sub) -> bool:
    p = current(store, sub)
    return p is not None and p.plan == "free"


def _product(store: Store, sub):
    return store.get("products", sub.product)


def _price(store: Store, sub) -> int:
    from townsim.town import product
    return product.price_of(store, _product(store, sub), sub.client)


def _period(store: Store, sub) -> str:
    from townsim.town import product
    return product.period(_product(store, sub))


def trial_used(store: Store, sub) -> bool:
    return any(p.plan == "paid" for p in rows(store, sub))


def start_free(store: Store, sub, cause: str) -> None:
    store.add("plans", Plan(store.new_id("pl", cause), sub.id, store.day, "free"), cause)


def free_until_now(store: Store, sub) -> int:
    """The last free day the paid plan would have if it started today (-1 when its free weeks were had)."""
    return -1 if trial_used(store, sub) else store.day + 7 * _product(store, sub).free_weeks - 1


def terms(store: Store, sub, free_until: int) -> str:
    """`free until Sunday 5 April, then $100.00 every 4 weeks`, or `$100.00 every 4 weeks from the day it starts`."""
    price = f"{money(_price(store, sub))} {_period(store, sub)}"
    return f"free until {date_text(free_until)}, then {price}" if free_until >= 0 else f"{price} from the day it starts"


def starts_sentence(store: Store, sub, free_until: int) -> str:
    """What a request that would connect something says of the plan, while the client is on the free plan."""
    if not is_free(store, sub):
        return ""
    return f"Connecting it starts the paid plan: {terms(store, sub, free_until)}."


def start_paid(store: Store, sub, by: str, cause: str, free_until: int | None = None) -> str:
    """Move the client to the paid plan today, if it is on the free plan, or keep it there if it was to return
    to the free plan: the fee from the day after its free weeks (`free_until`, as a request stated it, or four
    weeks from today), or from today when its free weeks were had. What the client is told, or ""."""
    p = current(store, sub)
    if p is None:
        return ""
    if p.plan == "paid":
        if p.back_on >= 0:                      # connected again before the return: the fee goes on as it was
            store.set("plans", p.id, cause, back_on=-1)
            if p.fee_ob and store.has("obligations", p.fee_ob):
                store.set("obligations", p.fee_ob, cause, last=None, status="active")
            return "The paid plan goes on: it no longer returns to the free plan."
        return ""
    from townsim.town import product
    pr = _product(store, sub)
    if trial_used(store, sub):
        free_until = -1
    elif free_until is None or free_until < store.day:
        free_until = store.day + 7 * pr.free_weeks - 1
    first = free_until + 1 if free_until >= 0 else store.day
    ob = obligations.create(store, kind="fee", payer=sub.client, payee=pr.org, cents=_price(store, sub), first=first,
                            every=pr.fee_days, memo=f"{pr.name} subscription", ends="service", made_by=by, cause=cause)
    store.set("subscriptions", sub.id, cause, fee_ob=ob)
    store.add("plans", Plan(store.new_id("pl", cause), sub.id, store.day, "paid", ob, free_until), cause)
    return (f"The paid plan starts today: {terms(store, sub, free_until)} from {product.account_name(store, sub.client)}, "
            f"until the subscription is stopped.")


def nothing_connected(store: Store, sub, cause: str) -> str:
    """With nothing connected any more, the paid plan ends at the end of the period already paid (its free weeks,
    or the 4 weeks the last fee paid for) and the client returns to the free plan the day after. What they are
    told, or ""."""
    p = current(store, sub)
    if p is None or p.plan != "paid" or p.back_on >= 0 or not p.fee_ob:
        return ""
    ob = store.get("obligations", p.fee_ob)
    nxt = obligations.instalment_days(ob, store.day + 1, 1) if ob.status in obligations.SCHEDULED else []
    back = nxt[0] if nxt else store.day + 1
    store.set("plans", p.id, cause, back_on=back)
    if nxt:
        obligations.end(store, ob.id, back - 1, cause)
    return (f"With nothing connected, the paid plan ends on {date_text(back - 1)}; from {date_text(back)} "
            f"{_client(store, sub)} is on the free plan, with no fee.")


def _client(store: Store, sub) -> str:
    from townsim.town.connections import client_name
    return client_name(store, sub.client)


def apply_due(store: Store) -> None:
    """The morning: each client whose paid plan ended yesterday returns to the free plan."""
    for p in list(store.all("plans")):
        if p.plan != "paid" or p.back_on < 0 or p.back_on > store.day:
            continue
        sub = store.get("subscriptions", p.subscription)
        if sub.status != "active" or current(store, sub).id != p.id:
            continue
        cause = f"routine:plan:{sub.id}"
        store.set("subscriptions", sub.id, cause, fee_ob="")
        store.add("plans", Plan(store.new_id("pl", cause), sub.id, store.day, "free"), cause)
        for pid in obligations.people_of(store, sub.client):
            obligations.notify(store, pid, f"{_cap(_client(store, sub))} is on the free plan of "
                                           f"{_product(store, sub).name} from today: no fee.", cause)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def fee_obligations(store: Store, sub) -> list[str]:
    """Every fee obligation the subscription has had: its plans' and its own."""
    out = [p.fee_ob for p in rows(store, sub) if p.fee_ob]
    if sub.fee_ob and sub.fee_ob not in out:
        out.append(sub.fee_ob)
    return out


def words(store: Store, sub) -> str | None:
    """The plan as the client's and the firm's views state it: `free plan, no fee`; `paid plan since Monday 9
    March: free until Sunday 5 April, then $100.00 every 4 weeks`; `paid plan since ...: $100.00 every 4 weeks`;
    with `, to the free plan from <date>` when it returns. None outside the plans."""
    p = current(store, sub)
    if p is None:
        return None
    if p.plan == "free":
        return "free plan, no fee"
    price = f"{money(obligations.cents_now(store, store.get('obligations', p.fee_ob)))} {_period(store, sub)}"
    out = f"paid plan since {date_text(p.day)}: " + (
        f"free until {date_text(p.free_until)}, then {price}" if p.free_until >= store.day else price)
    if p.back_on >= 0:
        out += f"; to the free plan from {date_text(p.back_on)}"
    return out


def counts(store: Store, subs: list) -> tuple[int, int]:
    """(on the free plan, on the paid plan) among the active subscriptions within the plans."""
    free = paid = 0
    for s in subs:
        p = current(store, s) if s.status == "active" else None
        if p is not None:
            free += p.plan == "free"
            paid += p.plan == "paid"
    return free, paid
