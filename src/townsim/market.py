"""The town's fixed economic rules: weekly buying, payroll, and firm closures."""

import random

from townsim.events import Event
from townsim.ledger import transfer
from townsim.world import World

MARKETS = ("food", "services")


def market_phase(world: World, rng: random.Random) -> list[Event]:
    """Each person buys at most one unit in each market from the best firm they can use."""
    p = world.params
    capacity_per_employee = int(p["units_per_employee"])
    quality_weight = p["quality_weight"]
    price_weight = p["price_weight"]
    noise = p["choice_noise"]
    loyalty_gain = p["loyalty_gain"]
    loyalty_cap = p["loyalty_cap"]

    sold = {fid: 0 for fid in world.firms}
    events: list[Event] = []
    for market in MARKETS:
        firm_ids = sorted(
            fid for fid, f in world.firms.items() if f.open and f.market == market
        )
        if not firm_ids:
            continue
        mean_price = sum(world.firms[fid].price for fid in firm_ids) / len(firm_ids)
        buyers = sorted(world.people)
        rng.shuffle(buyers)
        for pid in buyers:
            loyalty = world.people[pid].loyalty
            scores = {}
            for fid in firm_ids:
                f = world.firms[fid]
                relative_price = f.price / mean_price if mean_price > 0 else 0.0
                scores[fid] = (
                    quality_weight * f.quality
                    - price_weight * relative_price
                    + loyalty.get(fid, 0)
                    + rng.uniform(-noise, noise)
                )
            for fid in sorted(firm_ids, key=lambda fid: (-scores[fid], fid)):
                f = world.firms[fid]
                capacity = capacity_per_employee * len(f.employee_ids)
                if sold[fid] >= capacity or f.price > world.people[pid].cash:
                    continue
                if f.price > 0:
                    transfer(world, pid, fid, f.price, f"purchase:{market}")
                world.firms[fid].profit_qtd += world.firms[fid].price
                sold[fid] += 1
                person = world.people[pid]
                person.loyalty[fid] = min(loyalty_cap, person.loyalty.get(fid, 0) + loyalty_gain)
                break
        for fid in firm_ids:
            f = world.firms[fid]
            events.append(
                Event(
                    kind="sales",
                    actor_id=fid,
                    payload={"firm_id": fid, "customers": sold[fid], "revenue": sold[fid] * f.price},
                )
            )
    for fid in world.firms:
        world.firms[fid].customers_last_step = sold[fid]
    return events


def payroll_phase(world: World) -> list[Event]:
    """Firms pay wages (or close), then public salaries, benefits, and subscriptions."""
    salary = int(world.params["public_salary"])
    subscription = int(world.params["subscription"])
    events: list[Event] = []

    for fid in sorted(world.firms):
        f = world.firms[fid]
        if not f.open:
            continue
        staff = list(f.employee_ids)
        if f.cash < f.wage * len(staff):
            events += _close_firm(world, fid, "unpaid_wages")
            continue
        for pid in staff:
            if f.wage > 0:
                transfer(world, fid, pid, f.wage, "wage")
            world.firms[fid].profit_qtd -= world.firms[fid].wage

    auditor_id = world.regulator.auditor_id
    if auditor_id in world.people:
        events += _public_payment(world, "treasury", auditor_id, salary, "public_salary")
    unemployed = sorted(pid for pid, p in world.people.items() if p.role == "unemployed")
    for pid in unemployed:
        events += _public_payment(world, "treasury", pid, world.laws.benefit, "benefit")
    for pid in world.newspaper.journalist_ids:
        events += _public_payment(world, "newspaper", pid, salary, "public_salary")

    if subscription > 0:
        for pid in sorted(world.people):
            if world.people[pid].cash >= subscription:
                transfer(world, pid, "newspaper", subscription, "subscription")
    return events


def closure_phase(world: World) -> list[Event]:
    """Close every open firm that has no employees and cannot cover one week's wage."""
    events: list[Event] = []
    for fid in sorted(world.firms):
        f = world.firms[fid]
        if f.open and not f.employee_ids and f.cash < f.wage:
            events += _close_firm(world, fid, "no_staff_no_cash")
    return events


def close_firm(world: World, firm_id: str) -> list[Event]:
    """Close a firm: staff and owner become unemployed, its remaining cash goes to the owner."""
    return _close_firm(world, firm_id, "closed")


def _close_firm(world: World, firm_id: str, reason: str) -> list[Event]:
    f = world.firms[firm_id]
    if not f.open:
        return []
    for pid in f.employee_ids:
        person = world.people[pid]
        person.role = "unemployed"
        person.employer_id = None
    owner = world.people[f.owner_id]
    owner.role = "unemployed"
    owner.employer_id = None
    if f.cash > 0:
        transfer(world, firm_id, f.owner_id, f.cash, "firm_closed")
    f = world.firms[firm_id]
    f.open = False
    f.employee_ids = []
    f.customers_last_step = 0
    return [
        Event(kind="firm_closed", actor_id=firm_id, payload={"firm_id": firm_id, "reason": reason})
    ]


def _public_payment(world: World, payer: str, payee: str, amount: int, reason: str) -> list[Event]:
    if amount <= 0:
        return []
    payer_cash = world.treasury.cash if payer == "treasury" else world.newspaper.cash
    if payer_cash < amount:
        return [
            Event(
                kind="payment_missed",
                actor_id=payer,
                payload={"payer": payer, "payee": payee, "amount": amount},
            )
        ]
    transfer(world, payer, payee, amount, reason)
    return []
