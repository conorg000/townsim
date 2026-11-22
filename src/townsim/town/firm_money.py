"""The firm's money with its clients (lane B): what it asks of a client is the firm's, a standing payment to
a client it owes nothing is refused with the fact, and a fee can be refunded.

`client_label` names a client so that two households of one surname cannot be confused. `refund_of` reads a
`pay` whose `for` names a subscription: from the firm's account to that subscription's client, at most what
the firm received in its fees less what it refunded, kept with a memo that reads as a refund on both sides.
"""

from __future__ import annotations

from typing import Any

from townsim.town.obligations import money
from townsim.town.store import Store

REFUND = "refund of {P} fee ({sub})"


def _lane(store: Store):
    from townsim.town import lane_b, product
    return product.launched(store) if lane_b.installed(store) else None


def client_label(store: Store, client: str) -> str:
    """`the Whitaker household at 35 Oak Street`, or a business's name."""
    if store.has("households", client):
        h = store.get("households", client)
        called = h.name if "household" in h.name.split() else f"{h.name} household"
        street = f" at {h.street}" if h.street and h.street not in h.name else ""
        return f"the {called}{street}"
    return store.get("orgs", client).name if store.has("orgs", client) else client


def is_client(store: Store, account: str) -> bool:
    return any(s.client == account for s in store.all("subscriptions"))


def firm_of(store: Store, account: str) -> bool:
    pr = _lane(store)
    return pr is not None and account == pr.org


def fees_from(store: Store, sub) -> int:
    """What the firm received in fees on a subscription."""
    from townsim.town import plans
    obs = set(plans.fee_obligations(store, sub))
    if not obs:
        return 0
    total = 0
    for t in store.ledger.transfers:
        if t.cause.startswith("routine:collect:"):
            due = t.cause.removeprefix("routine:collect:")
            if store.has("dues", due) and store.get("dues", due).obligation in obs:
                total += t.cents
    return total


def refunded(store: Store, sub) -> int:
    pr = _lane(store)
    memo = REFUND.format(P=pr.name, sub=sub.id) if pr else None
    return sum(t.cents for t in store.ledger.transfers if t.memo == memo)


def refund_of(store: Store, source: str, payee: str, ref: Any):
    """The subscription a `pay` from the firm's account refunds, when `for` names one of the payee's; else None."""
    pr = _lane(store)
    if pr is None or source != pr.org or not isinstance(ref, str) or not store.has("subscriptions", ref.strip()):
        return None
    sub = store.get("subscriptions", ref.strip())
    return sub if sub.client == payee and sub.product == pr.id else None


def refund_problem(store: Store, sub, cents: int) -> str | None:
    room = fees_from(store, sub) - refunded(store, sub)
    if cents > room:
        return (f"{client_label(store, sub.client)} has paid {money(fees_from(store, sub))} in fees on {sub.id}, "
                f"{money(refunded(store, sub))} of it refunded; a refund is at most {money(max(0, room))}.")
    return None


def refund_memo(store: Store, sub) -> str:
    return REFUND.format(P=_lane(store).name, sub=sub.id)


def standing_problem(store: Store, source: str, payee: str, owed: list) -> str | None:
    """A standing payment from the firm's account to a client it owes nothing is refused, with the fact."""
    if not firm_of(store, source) or not is_client(store, payee) or owed:
        return None
    return (f"{store.get('orgs', source).name} owes {client_label(store, payee)} nothing; a payment to them is made "
            f"with pay.")


def request_payee(store: Store, act) -> str | None:
    """The account money asked for in a request goes to, when it is a business of the asker's: the one `for`
    names; or the firm, when its owner asks one of its clients' people and names nothing."""
    from townsim.town.acts import account_of, controls
    pr = _lane(store)
    if pr is None:
        return None
    written = act.args.get("for")
    if isinstance(written, str) and written.strip():
        wanted = written.strip().casefold()
        org = next((o for o in store.all("orgs") if o.name.casefold() == wanted and not o.outside), None)
        return org.id if org is not None and controls(store, act.principal, org.id) else None
    firm = store.get("orgs", pr.org)
    if act.principal != firm.owner:
        return None
    from townsim.town.ops_core import _request_to
    who, _ = _request_to(store, act)
    if who is None:
        return None
    asked = {account_of(store, who)} | {o.id for o in store.all("orgs") if o.owner == who}
    return pr.org if any(is_client(store, a) for a in asked) else None
