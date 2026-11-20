"""The town's fixed economic rules: weekly buying, payroll, firm closures, and owners' choices."""

import random
from collections.abc import Callable

from townsim.actions import Action, action_from_dict, execute, register
from townsim.decide import Choice, Decision, Option, rule
from townsim.events import Event
from townsim.ledger import transfer
from townsim.world import Firm, World

MARKETS = ("food", "services")

DecideFn = Callable[[Decision], Choice]

MIN_PRICE = 100
PRICE_FACTORS = {"down10": 0.90, "down5": 0.95, "hold": 1.0, "up5": 1.05, "up10": 1.10}


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


# Owners' pricing and staffing choices.


def _owned_open_firm(world: World, actor_id: str, firm_id: str) -> str | None:
    f = world.firms.get(firm_id)
    if f is None:
        return f"no such firm: {firm_id}"
    if not f.open:
        return f"{firm_id} is closed"
    if f.owner_id != actor_id:
        return f"{actor_id} does not own {firm_id}"
    return None


def _new_price(price: int, factor: float) -> int:
    return max(MIN_PRICE, round(price * factor))


@register
class SetPrice(Action):
    kind = "set_price"
    firm_id: str
    factor: float

    def check(self, world: World, actor_id: str) -> str | None:
        if self.factor <= 0:
            return f"price factor must be positive, got {self.factor}"
        return _owned_open_firm(world, actor_id, self.firm_id)

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        f = world.firms[self.firm_id]
        old_price = f.price
        f.price = _new_price(old_price, self.factor)
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={"firm_id": self.firm_id, "old_price": old_price, "new_price": f.price},
            )
        ]


@register
class ChangeStaff(Action):
    """`delta=+1` hires one unemployed person chosen by `rng`; `-1` lets the most recent hire go."""

    kind = "change_staff"
    firm_id: str
    delta: int

    def check(self, world: World, actor_id: str) -> str | None:
        if self.delta not in (1, -1):
            return f"staff can change by one person at a time, got {self.delta}"
        reason = _owned_open_firm(world, actor_id, self.firm_id)
        if reason is not None:
            return reason
        if self.delta == 1 and not _unemployed_ids(world):
            return "nobody is looking for work"
        if self.delta == -1 and not world.firms[self.firm_id].employee_ids:
            return f"{self.firm_id} has no staff to let go"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        f = world.firms[self.firm_id]
        if self.delta == 1:
            pid = rng.choice(_unemployed_ids(world))
            f.employee_ids.append(pid)
            person = world.people[pid]
            person.role = "worker"
            person.employer_id = self.firm_id
        else:
            pid = f.employee_ids.pop()
            person = world.people[pid]
            person.role = "unemployed"
            person.employer_id = None
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={"firm_id": self.firm_id, "delta": self.delta, "person_id": pid},
            )
        ]


def _unemployed_ids(world: World) -> list[str]:
    return sorted(pid for pid, p in world.people.items() if p.role == "unemployed")


def owners_phase(world: World, decide: DecideFn, rng: random.Random) -> list[Event]:
    """Each open firm's owner, in firm id order, makes a pricing then a staffing decision."""
    events: list[Event] = []
    seqs: dict[tuple[str, str], int] = {}
    for fid in sorted(world.firms):
        for build in (_pricing_decision, _staffing_decision):
            f = world.firms[fid]
            if not f.open:
                break
            decision = build(world, f, seqs)
            choice = decide(decision)
            options = {o.id: o for o in decision.options}
            if choice.option_id not in options:
                raise ValueError(f"{decision.id}: {choice.option_id!r} was not offered")
            action = options[choice.option_id].action
            if action is not None:
                events += execute(world, f.owner_id, action_from_dict(action), rng)
    return events


def _action_dict(action: Action) -> dict:
    """The action as `action_from_dict` reads it back."""
    return {"kind": action.kind, **action.model_dump()}


def _decision_id(world: World, actor_id: str, kind: str, seqs: dict[tuple[str, str], int]) -> str:
    seq = seqs.get((actor_id, kind), 0)
    seqs[(actor_id, kind)] = seq + 1
    return f"{world.step}:{actor_id}:{kind}:{seq}"


def _firm_facts(world: World, f: Firm) -> dict:
    competitors = [
        g.price for g in world.firms.values() if g.open and g.market == f.market and g.id != f.id
    ]
    staff = len(f.employee_ids)
    return {
        "price": f.price,
        "mean_market_price": round(sum(competitors) / len(competitors)) if competitors else None,
        "customers_last_step": f.customers_last_step,
        "capacity": int(world.params["units_per_employee"]) * staff,
        "cash": f.cash,
        "weekly_wage_bill": f.wage * staff,
        "staff": staff,
    }


def _dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{part:02d}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _describe_firm(f: Firm, facts: dict) -> str:
    mean = facts["mean_market_price"]
    if mean is None:
        market = f"No other open firm sells in the {f.market} market."
    else:
        market = f"Other open firms in the {f.market} market charge {_dollars(mean)} on average."
    return (
        f"You own {f.name}, which sells in the {f.market} market. "
        f"Your price is {_dollars(facts['price'])}. {market} "
        f"Last week you served {_count(facts['customers_last_step'], 'customer')}; "
        f"with {_count(facts['staff'], 'staff member')} you could have served up to "
        f"{facts['capacity']}. "
        f"Your firm has {_dollars(facts['cash'])} in cash and pays "
        f"{_dollars(facts['weekly_wage_bill'])} a week in wages."
    )


def _pricing_decision(world: World, f: Firm, seqs: dict[tuple[str, str], int]) -> Decision:
    facts = _firm_facts(world, f)
    options = []
    for oid, factor in PRICE_FACTORS.items():
        if oid == "hold":
            options.append(Option(id=oid, label=f"Keep your price at {_dollars(f.price)}"))
            continue
        verb = "Lower" if factor < 1 else "Raise"
        pct = round(abs(factor - 1) * 100)
        label = f"{verb} your price by {pct}% to {_dollars(_new_price(f.price, factor))}"
        action = _action_dict(SetPrice(firm_id=f.id, factor=factor))
        options.append(Option(id=oid, label=label, action=action))
    return Decision(
        id=_decision_id(world, f.owner_id, "pricing", seqs),
        actor_id=f.owner_id,
        kind="pricing",
        situation=_describe_firm(f, facts) + " You can change your price for this week.",
        options=options,
        default_option_id="hold",
        facts=facts,
    )


def _staffing_decision(world: World, f: Firm, seqs: dict[tuple[str, str], int]) -> Decision:
    facts = _firm_facts(world, f)
    return Decision(
        id=_decision_id(world, f.owner_id, "staffing", seqs),
        actor_id=f.owner_id,
        kind="staffing",
        situation=_describe_firm(f, facts) + " You can change the size of your staff this week.",
        options=[
            Option(
                id="hire",
                label=f"Hire one more person at {_dollars(f.wage)} a week",
                action=_action_dict(ChangeStaff(firm_id=f.id, delta=1)),
            ),
            Option(
                id="fire",
                label="Let your most recent hire go",
                action=_action_dict(ChangeStaff(firm_id=f.id, delta=-1)),
            ),
            Option(id="hold", label="Keep your staff as it is"),
        ],
        default_option_id="hold",
        facts=facts,
    )


@rule("pricing")
def _pricing_rule(decision: Decision, rng: random.Random) -> str:
    customers = decision.facts["customers_last_step"]
    capacity = decision.facts["capacity"]
    if capacity == 0:
        return "hold"  # no staff, so no sales to read demand from: 0 >= 0 is not "full"
    if customers < 0.7 * capacity:
        return "down5"
    if customers >= capacity:
        return "up5"
    return "hold"


@rule("staffing")
def _staffing_rule(decision: Decision, rng: random.Random) -> str:
    facts = decision.facts
    customers, capacity = facts["customers_last_step"], facts["capacity"]
    if customers >= capacity and facts["cash"] > 8 * facts["weekly_wage_bill"]:
        return "hire"
    if customers < 0.5 * capacity and facts["staff"] > 1:
        return "fire"
    return "hold"
