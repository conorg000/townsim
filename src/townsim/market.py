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
MIN_WAGE = 100
WAGE_FACTORS = {"cut10": 0.90, "cut5": 0.95, "hold": 1.0, "raise5": 1.05, "raise10": 1.10}


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
        world.firms[fid].revenue_last_step = sold[fid] * world.firms[fid].price
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
        f = world.firms[fid]
        week = [f.price, f.customers_last_step, f.revenue_last_step, f.wage * len(staff)]
        f.recent_weeks = (f.recent_weeks + [week])[-RECENT_WEEKS_KEPT:]
        capacity = int(world.params["units_per_employee"]) * len(staff)
        book = [world.step, f.customers_last_step, capacity, f.revenue_last_step, f.wage * len(staff)]
        f.books = (f.books + [book])[-BOOKS_KEPT:]

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
    """Change the firm's price by `factor`, or, when `price` is given, to exactly `price`."""

    kind = "set_price"
    firm_id: str
    factor: float = 1.0
    price: int | None = None

    def check(self, world: World, actor_id: str) -> str | None:
        if self.price is not None:
            if self.price < MIN_PRICE:
                return f"a price must be at least {_dollars(MIN_PRICE)}"
        elif self.factor <= 0:
            return f"price factor must be positive, got {self.factor}"
        return _owned_open_firm(world, actor_id, self.firm_id)

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        f = world.firms[self.firm_id]
        old_price = f.price
        f.price = self.price if self.price is not None else _new_price(old_price, self.factor)
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={"firm_id": self.firm_id, "old_price": old_price, "new_price": f.price},
            )
        ]


@register
class ChangeStaff(Action):
    """`delta=+1` hires one person; `-1` lets the most recent hire go.

    A hire is an unemployed person chosen by `rng`. If nobody is out of work, it is
    the worker at the lowest-paying other firm (ties by wage, then person id), and only
    if this firm pays strictly more; that worker moves and a `job_move` event follows.
    """

    kind = "change_staff"
    firm_id: str
    delta: int

    def check(self, world: World, actor_id: str) -> str | None:
        if self.delta not in (1, -1):
            return f"staff can change by one person at a time, got {self.delta}"
        reason = _owned_open_firm(world, actor_id, self.firm_id)
        if reason is not None:
            return reason
        if self.delta == 1 and not _unemployed_ids(world) and _mover_id(world, self.firm_id) is None:
            return "nobody is looking for work, and no other firm pays its staff less"
        if self.delta == 1 and world.firms[self.firm_id].wage < world.laws.benefit:
            return "nobody out of work will take a job that pays less than the benefit"
        if self.delta == -1 and not world.firms[self.firm_id].employee_ids:
            return f"{self.firm_id} has no staff to let go"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        f = world.firms[self.firm_id]
        moved: list[Event] = []
        if self.delta == 1:
            unemployed = _unemployed_ids(world)
            if unemployed:
                pid = rng.choice(unemployed)
            else:
                pid = _mover_id(world, self.firm_id)
                old = world.firms[world.people[pid].employer_id]
                old.employee_ids.remove(pid)
                moved.append(
                    Event(
                        kind="job_move",
                        actor_id=pid,
                        payload={
                            "person_id": pid,
                            "from_firm_id": old.id,
                            "to_firm_id": self.firm_id,
                            "old_wage": old.wage,
                            "new_wage": f.wage,
                        },
                    )
                )
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
        ] + moved


def _mover_id(world: World, firm_id: str) -> str | None:
    """The worker a hire by `firm_id` would take when nobody is out of work, if any.

    That is the worker at the lowest-paying other open firm (ties by wage, then person
    id), and only when `firm_id` pays them strictly more than they now earn.
    """
    candidates = [
        (g.wage, pid)
        for g in world.firms.values()
        if g.open and g.id != firm_id
        for pid in g.employee_ids
        if world.people[pid].role == "worker"
    ]
    if not candidates:
        return None
    low_wage, pid = min(candidates)
    return pid if world.firms[firm_id].wage > low_wage else None


def wage_floor(world: World) -> int:
    """The lowest weekly wage a firm may set: the legal minimum, if the town has one."""
    return max(MIN_WAGE, world.laws.min_wage)


def _new_wage(world: World, wage: int, factor: float) -> int:
    return max(wage_floor(world), round(wage * factor))


@register
class SetWage(Action):
    """Change what the firm pays each member of staff a week, from this week's payroll."""

    kind = "set_wage"
    firm_id: str
    factor: float = 1.0
    # When given, the new weekly wage exactly; it may not be below `wage_floor`.
    wage: int | None = None

    def check(self, world: World, actor_id: str) -> str | None:
        if self.wage is not None:
            if self.wage < wage_floor(world):
                if world.laws.min_wage > MIN_WAGE:
                    return f"the law sets a minimum wage of {_dollars(world.laws.min_wage)} a week"
                return f"weekly pay must be at least {_dollars(MIN_WAGE)}"
        elif self.factor <= 0:
            return f"wage factor must be positive, got {self.factor}"
        return _owned_open_firm(world, actor_id, self.firm_id)

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        f = world.firms[self.firm_id]
        old_wage = f.wage
        f.wage = self.wage if self.wage is not None else _new_wage(world, old_wage, self.factor)
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={
                    "firm_id": self.firm_id,
                    "old_wage": old_wage,
                    "new_wage": f.wage,
                    "staff": len(f.employee_ids),
                },
            )
        ]


@register
class QuitJob(Action):
    """A worker leaves their employer and is out of work."""

    kind = "quit_job"

    def check(self, world: World, actor_id: str) -> str | None:
        person = world.people.get(actor_id)
        if person is None:
            return f"no such person: {actor_id}"
        f = world.firms.get(person.employer_id or "")
        if person.role != "worker" or f is None or actor_id not in f.employee_ids:
            return f"{actor_id} has no job to leave"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        person = world.people[actor_id]
        f = world.firms[person.employer_id]
        f.employee_ids.remove(actor_id)
        person.role = "unemployed"
        person.employer_id = None
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={"firm_id": f.id, "person_id": actor_id, "wage": f.wage},
            )
        ]


def _unemployed_ids(world: World) -> list[str]:
    return sorted(pid for pid, p in world.people.items() if p.role == "unemployed")


def _is_wage_review_week(world: World) -> bool:
    every = int(world.params.get("wage_review_weeks", 0))
    return every > 0 and world.step > 0 and world.step % every == 0


def _take(world, decide, decision, actor_id, rng) -> list[Event]:
    choice = decide(decision)
    options = {o.id: o for o in decision.options}
    if choice.option_id not in options:
        raise ValueError(f"{decision.id}: {choice.option_id!r} was not offered")
    action = options[choice.option_id].action
    if action is None:
        return []
    return execute(world, actor_id, action_from_dict(action), rng)


def owners_phase(world: World, decide: DecideFn, rng: random.Random) -> list[Event]:
    """Each open firm's owner, in firm id order, makes a pricing then a staffing decision.

    Every `wage_review_weeks` weeks the owner also decides what to pay staff. When pay
    is cut, each member of that firm's staff who was there before this week's staffing
    decision, in id order, decides whether to stay. An owner who has put an AI system
    in charge of their firm is asked none of these: the system runs it instead.
    """
    events: list[Event] = []
    seqs: dict[tuple[str, str], int] = {}
    builders = [_pricing_decision, _staffing_decision]
    if _is_wage_review_week(world):
        builders.append(_wage_decision)
    for fid in sorted(world.firms):
        if world.people[world.firms[fid].owner_id].assistant is not None:
            continue
        staff_before = set(world.firms[fid].employee_ids)  # pricing does not change staff
        for build in builders:
            f = world.firms[fid]
            if not f.open:
                break
            old_wage = f.wage
            events += _take(world, decide, build(world, f, seqs), f.owner_id, rng)
            events += pay_cut_reactions(world, decide, rng, fid, old_wage, staff_before, seqs)
    return events


def pay_cut_reactions(
    world: World,
    decide: DecideFn,
    rng: random.Random,
    firm_id: str,
    old_wage: int,
    staff_before: set[str],
    seqs: dict[tuple[str, str], int],
) -> list[Event]:
    """If the firm now pays less than `old_wage`, each of its staff who were in
    `staff_before`, in id order, decides whether to stay."""
    events: list[Event] = []
    f = world.firms[firm_id]
    if not f.open or f.wage >= old_wage:
        return events
    for pid in sorted(p for p in f.employee_ids if p in staff_before):
        decision = _pay_cut_decision(world, world.firms[firm_id], pid, old_wage, seqs)
        events += _take(world, decide, decision, pid, rng)
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
        "wage": f.wage,
        "staff": staff,
        "recent_weeks": [list(w) for w in f.recent_weeks],
    }


RECENT_WEEKS_KEPT = 4
BOOKS_KEPT = 8


def _describe_recent_weeks(weeks: list[list[int]]) -> str:
    """The firm's own books for its last weeks, most recent first, as plain figures."""
    if not weeks:
        return ""
    names = ["Last week", "The week before", "Three weeks ago", "Four weeks ago"]
    lines = []
    for name, (price, customers, takings, wages) in zip(names, reversed(weeks)):
        left = takings - wages
        result = f"{_dollars(left)} left after wages" if left >= 0 else f"{_dollars(-left)} short of wages"
        lines.append(
            f"{name}: price {_dollars(price)}, {_count(customers, 'customer')}, "
            f"takings {_dollars(takings)}, wages {_dollars(wages)}, {result}."
        )
    return " Your books for recent weeks. " + " ".join(lines)


def _dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{part:02d}"


_PLURALS = {"person": "people"}


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {_PLURALS.get(noun, noun + 's')}"


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
        f"{_dollars(facts['weekly_wage_bill'])} a week in wages"
        f"{_each(facts)}."
        f"{_describe_recent_weeks(facts['recent_weeks'])}"
    )


def _each(facts: dict) -> str:
    return f" ({_dollars(facts['wage'])} to each member of staff)" if facts["staff"] else ""


def _labour_market(world: World, firm_id: str) -> dict:
    """What anyone in town can see about work: who is out of work and what others pay."""
    others = [
        g.wage for g in world.firms.values() if g.open and g.id != firm_id and g.employee_ids
    ]
    return {
        "out_of_work": len(_unemployed_ids(world)),
        "benefit": world.laws.benefit,
        "mean_other_wage": round(sum(others) / len(others)) if others else None,
        "min_wage": world.laws.min_wage,
    }


def _describe_labour_market(lm: dict) -> str:
    n = lm["out_of_work"]
    people = "Nobody in town is" if n == 0 else f"{_count(n, 'person')} in town {'is' if n == 1 else 'are'}"
    text = (
        f"{people} out of work; the town pays people out of work "
        f"{_dollars(lm['benefit'])} a week."
    )
    if lm["mean_other_wage"] is not None:
        text += f" Other firms in town pay their staff {_dollars(lm['mean_other_wage'])} a week on average."
    if lm["min_wage"] > 0:
        text += f" The law sets a minimum wage of {_dollars(lm['min_wage'])} a week."
    return text


def _wage_decision(world: World, f: Firm, seqs: dict[tuple[str, str], int]) -> Decision:
    facts = {**_firm_facts(world, f), **_labour_market(world, f.id)}
    options = []
    offered: set[int] = set()
    for oid, factor in WAGE_FACTORS.items():
        new = _new_wage(world, f.wage, factor)
        if oid == "hold":
            options.append(Option(id=oid, label=f"Keep pay at {_dollars(f.wage)} a week"))
            continue
        if new == f.wage or new in offered:
            continue  # no change, or the same new wage as an option already offered
        offered.add(new)
        # The floor can turn a cut into a raise, or shrink it: label the real change.
        verb = "Cut" if new < f.wage else "Raise"
        pct = round(abs(new - f.wage) * 100 / f.wage) if f.wage > 0 else 0
        by = f" by {pct}%" if pct > 0 else ""
        label = f"{verb} pay{by} to {_dollars(new)} a week for each member of staff"
        options.append(
            Option(id=oid, label=label, action=_action_dict(SetWage(firm_id=f.id, factor=factor)))
        )
    return Decision(
        id=_decision_id(world, f.owner_id, "wages", seqs),
        actor_id=f.owner_id,
        kind="wages",
        situation=(
            _describe_firm(f, facts)
            + " "
            + _describe_labour_market(facts)
            + " Staff whose pay is cut can choose to leave. Nobody out of work takes a job"
            " that pays less than the benefit. When nobody is out of work, a firm that hires"
            " takes on a worker from the lowest-paying other firm, if it pays that worker more."
            " You can change what you pay your staff, starting this week."
        ),
        options=options,
        default_option_id="hold",
        facts=facts,
    )


def _pay_cut_decision(
    world: World, f: Firm, pid: str, old_wage: int, seqs: dict[tuple[str, str], int]
) -> Decision:
    lm = _labour_market(world, f.id)
    owner = world.people[f.owner_id].name
    best_other = max((g.wage for g in world.firms.values() if g.open and g.id != f.id), default=None)
    elsewhere = ""
    if best_other is not None and best_other > f.wage:
        elsewhere = f" The best-paying other firm in town pays {_dollars(best_other)} a week."
    return Decision(
        id=_decision_id(world, pid, "pay_cut", seqs),
        actor_id=pid,
        kind="pay_cut",
        situation=(
            f"You work at {f.name}. {owner}, who owns it, has cut your pay from "
            f"{_dollars(old_wage)} to {_dollars(f.wage)} a week, starting this week. "
            + _describe_labour_market(lm)
            + elsewhere
        ),
        options=[
            Option(id="stay", label=f"Keep working at {f.name} for {_dollars(f.wage)} a week"),
            Option(id="quit", label="Leave your job", action=_action_dict(QuitJob())),
        ],
        default_option_id="stay",
        facts={"wage": f.wage, "old_wage": old_wage, "best_other_wage": best_other, **lm},
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
    options = []
    if f.wage >= world.laws.benefit:  # below the benefit, nobody out of work would take it
        label = f"Hire one more person at {_dollars(f.wage)} a week"
        mover = None if _unemployed_ids(world) else _mover_id(world, f.id)
        if mover is not None:
            source = world.firms[world.people[mover].employer_id]
            label += (
                f". Nobody is out of work, so they would come from {source.name},"
                f" which pays {_dollars(source.wage)} a week"
            )
        options.append(
            Option(id="hire", label=label, action=_action_dict(ChangeStaff(firm_id=f.id, delta=1)))
        )
    return Decision(
        id=_decision_id(world, f.owner_id, "staffing", seqs),
        actor_id=f.owner_id,
        kind="staffing",
        situation=_describe_firm(f, facts) + " You can change the size of your staff this week.",
        options=options
        + [
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
    can_hire = any(o.id == "hire" for o in decision.options)
    if can_hire and customers >= capacity and facts["cash"] > 8 * facts["weekly_wage_bill"]:
        return "hire"
    if customers < 0.5 * capacity and facts["staff"] > 1:
        return "fire"
    return "hold"


@rule("wages")
def _wages_rule(decision: Decision, rng: random.Random) -> str:
    return "hold"


@rule("pay_cut")
def _pay_cut_rule(decision: Decision, rng: random.Random) -> str:
    # Better pay elsewhere is not a job offer: leave only when the benefit pays more.
    return "quit" if decision.facts["wage"] < decision.facts["benefit"] else "stay"
