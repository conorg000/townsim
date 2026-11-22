"""The investor's agreement with the firm (lane B): a term, and a review on paying clients and fees received.

The generated investment in the software firm is weekly with no end. With lane B it reads as such agreements
do: weekly until a Monday drawn by seed between 10 and 14 weeks after day 0 (`term`, at install), "renewal to
be reviewed on paying clients and fees received". On that morning, before anything is collected, the review
applies a fixed table (`review`, first in the routine): fees received over the last four weeks against the
firm's weekly wage bill; at or above a third, renewed for 26 weeks at the same amount; below that and above
nothing, renewed for 8 weeks at half; nothing, not renewed. The investor tells the firm's owner in a letter
with an account note. Nothing else about the firm or its sales changes.
"""

from __future__ import annotations

import random

from townsim.town import obligations
from townsim.town.clock import DAYS, MONTHS, on
from townsim.town.entities import Message
from townsim.town.obligations import money
from townsim.town.store import Store

TERM_WEEKS = (10, 14)
RENEWED = ((3, 26, 1.0), (0, 8, 0.5))        # fees at or above wages / n (n=3; n=0: above nothing): weeks, share
REVIEW = "renewal to be reviewed on paying clients and fees received"
CAUSE = "routine:investment"


def short(day: int) -> str:
    """`Mon 15 Feb 2027`."""
    d = on(day)
    return f"{DAYS[d.weekday()][:3]} {d.day} {MONTHS[d.month - 1][:3]} {d.year}"


def _firm(store: Store):
    return next((o for o in store.all("orgs") if o.kind == "firm" and not o.outside), None)


def agreement(store: Store):
    """The investor's weekly agreement with the firm, or None."""
    firm = _firm(store)
    investors = {o.id for o in store.all("orgs") if o.kind == "investor"}
    if firm is None:
        return None
    return next((o for o in sorted(store.all("obligations"), key=lambda o: o.made_on)
                 if o.payee == firm.id and o.payer in investors and o.kind == "agreement"), None)


def term(store: Store, cause: str) -> None:
    """Give the agreement its term and its review (at install, once)."""
    ob = agreement(store)
    if ob is None or ob.last is not None or REVIEW in ob.memo:
        return
    days = [d for d in range(ob.first, 7 * TERM_WEEKS[1] + 1, ob.every or 7)
            if 7 * TERM_WEEKS[0] <= d <= 7 * TERM_WEEKS[1]]
    if not days:
        return
    last = random.Random(f"investment:{store.seed}").choice(days)
    store.set("obligations", ob.id, cause, last=last, memo=f"{ob.memo}; {REVIEW}")


def wages(store: Store) -> int:
    firm = _firm(store)
    return sum(obligations.weekly_cents(o) for o in store.where("obligations", payer=firm.id)
               if o.kind == "wage" and o.status == "active") if firm else 0


def fees(store: Store) -> int:
    from townsim.town import product
    pr = product.launched(store)
    return product.fees_received(store, pr, store.day - 28) if pr else 0


def decision(store: Store) -> tuple[int, float]:
    """Weeks renewed and the share of the amount, by the table."""
    got, bill = fees(store), wages(store)
    if got > 0 and 3 * got >= bill:
        return RENEWED[0][1], RENEWED[0][2]
    if got > 0:
        return RENEWED[1][1], RENEWED[1][2]
    return 0, 0.0


def review(store: Store) -> None:
    """On the agreement's last day, before collection: renew or not by the table, and tell the firm's owner."""
    ob = agreement(store)
    if ob is None or ob.last != store.day or REVIEW not in ob.memo or ob.status != "active":
        return
    if any(m.sender == ob.payer and m.day == store.day and m.text.startswith("Following our review") for m in
           store.where("messages", sender=ob.payer)):
        return
    firm = _firm(store)
    investor = store.get("orgs", ob.payer)
    weeks, share = decision(store)
    cents = obligations.cents_now(store, ob)
    ends = ob.last                                   # the term reviewed: its last payment is made today as agreed
    if weeks:
        new = int(round(cents * share))
        first, last = ends + 7, ends + 7 * weeks     # the renewal: from the first payment after today
        store.set("obligations", ob.id, CAUSE, last=last)
        if new != cents:
            store.set("obligations", ob.id, CAUSE, next_cents=new, next_from=first)
        body = (f"Following our review of {firm.name}'s paying clients and fees received, {investor.name} renews its "
                f"investment for {weeks} weeks at {money(new)} a week, from {short(first)} to {short(last)}.")
        note = (f"Account note: {money(new)} a week from {short(first)}, the last payment on {short(last)}; "
                f"{REVIEW}.")
    else:
        body = (f"Following our review of {firm.name}'s paying clients and fees received, {investor.name} does not "
                f"renew its investment.")
        note = f"Account note: the last payment of {money(cents)} is made on {short(ends)}; none is scheduled after it."
    from townsim.town import desks
    d = desks.desk_of(store, investor.id)
    signed = f"\n\n{d.signer}, {investor.name}" if d else ""
    store.add("messages", Message(store.new_id("msg", CAUSE), store.day, investor.id, firm.owner,
                                  f"{body}{signed}\n\n{note}", org=firm.id), CAUSE)


def view_line(store: Store) -> str | None:
    """What the firm's people see of the investment and the money it holds."""
    ob = agreement(store)
    firm = _firm(store)
    if ob is None or REVIEW not in ob.memo:
        return None
    investor = store.get("orgs", ob.payer).name
    held = f"In the account: {money(store.balance(firm.id))}; wages {money(wages(store))} a week."
    if ob.status == "active" and (ob.last is None or ob.last >= store.day):
        return (f"Investment: {money(obligations.cents_now(store, ob))} a week from {investor} until {short(ob.last)}; "
                f"renewal reviewed on paying clients and fees received. {held}")
    return f"Investment: from {investor}, ended {short(ob.last)}, not renewed. {held}"


def records(store: Store) -> list[str]:
    """The investor desk's records of the agreement and its review."""
    ob = agreement(store)
    if ob is None:
        return []
    paid = [t for t in store.ledger.transfers if t.src == ob.payer and t.dst == ob.payee]
    out = [f"Investment agreement: {money(obligations.cents_now(store, ob))} a week to "
           f"{store.get('orgs', ob.payee).name}, paid on {DAYS[on(ob.first).weekday()]}s"
           + (f", until {short(ob.last)}" if ob.last is not None else "") + f"; {ob.memo}.",
           f"Payments made: {len(paid)}, {money(sum(t.cents for t in paid))} in all"
           + (f"; the latest {money(paid[-1].cents)} on {short(paid[-1].t)}." if paid else ".")]
    if REVIEW in ob.memo:
        out.append("The review's table: fees received over the last four weeks at or above a third of the weekly "
                   "wage bill, renewed for 26 weeks at the same amount; below that and above nothing, renewed for 8 "
                   "weeks at half; nothing, not renewed. Now: fees received over the last four weeks "
                   f"{money(fees(store))}; wages {money(wages(store))} a week.")
    return out


def payments_note(store: Store) -> str | None:
    """The investor's account note on any reply: what has been paid and what is scheduled."""
    ob = agreement(store)
    if ob is None:
        return None
    paid = [t for t in store.ledger.transfers if t.src == ob.payer and t.dst == ob.payee]
    latest = f", the latest on {short(paid[-1].t)}" if paid else ""
    until = f" until {short(ob.last)}" if ob.last is not None else ""
    return (f"Account note: {len(paid)} payment{'' if len(paid) == 1 else 's'} of the investment made so far{latest}; "
            f"{money(obligations.cents_now(store, ob))} a week scheduled{until}.")
