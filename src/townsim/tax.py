"""Quarterly tax reports, concealment of unreported profit, and the auditor's audits."""

import random
from typing import Literal

from townsim.actions import ACTIONS, Action, action_from_dict, execute, register
from townsim.decide import Decision, Option, rule
from townsim.events import Event
from townsim.ledger import transfer
from townsim.market import (
    DecideFn,
    _action_dict,
    _decision_id,
    _dollars,
    _count,
    _owned_open_firm,
)
from townsim.world import Firm, Info, World

QUARTER_STEPS = 13

Concealment = Literal["none", "some", "heavy"]
CONCEALMENT_COST = {"none": 0.0, "some": 0.05, "heavy": 0.15}
DETECTION_MULTIPLIER = {"none": 1.0, "some": 0.6, "heavy": 0.3}
EMPLOYEE_LEARNS = {"none": 0.5, "some": 0.2, "heavy": 0.0}

HIDE_OPTIONS = {
    f"hide{pct}_{level}": (pct / 100, level)
    for pct in (25, 50)
    for level in ("none", "some", "heavy")
}

Depth = Literal["light", "deep"]
AUDIT_COST = {"light": 1, "deep": 3}
DETECTION_BASE = {"light": 0.3, "deep": 0.9}
DETECTION_BASE_TIPPED = {"light": 0.6, "deep": 1.0}


# Filing.


class _KindByHideShare:
    """`FileTax.kind`: `file_tax` on the class; on an instance, `underreport_tax` if it hides any."""

    def __get__(self, obj: "FileTax | None", owner: type) -> str:
        if obj is None or obj.hide_share == 0:
            return "file_tax"
        return "underreport_tax"


def _amounts(world: World, f: Firm, hide_share: float, concealment: str) -> dict:
    taxable = max(0, f.profit_qtd)
    reported = round(taxable * (1 - hide_share))
    tax_due = round(world.laws.tax_rate * reported)
    unpaid = round(world.laws.tax_rate * taxable) - tax_due
    return {
        "taxable": taxable,
        "reported": reported,
        "tax_due": tax_due,
        "unpaid": unpaid,
        "concealment_cost": round(CONCEALMENT_COST[concealment] * unpaid),
    }


@register
class FileTax(Action):
    """File the quarter's tax report for a firm, reporting `1 - hide_share` of its profit.

    The first event, `file_tax` or `underreport_tax`, is the owner's private act and is
    hidden. Every filing then emits a visible `tax_filed` event with the reported figures
    (`reported`, `tax_due`, `tax_paid`), so an underreport looks like any other filing.
    """

    kind = _KindByHideShare()
    firm_id: str
    hide_share: float
    concealment: Concealment

    def check(self, world: World, actor_id: str) -> str | None:
        if not 0 <= self.hide_share < 1:
            return f"hide_share must be in [0, 1), got {self.hide_share}"
        reason = _owned_open_firm(world, actor_id, self.firm_id)
        if reason is not None:
            return reason
        if self.hide_share > 0:
            a = _amounts(world, world.firms[self.firm_id], self.hide_share, self.concealment)
            if a["unpaid"] <= 0:
                return f"{self.firm_id} has no taxable profit to leave unreported"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        f = world.firms[self.firm_id]
        a = _amounts(world, f, self.hide_share, self.concealment)
        tax_paid = min(a["tax_due"], max(0, f.cash))
        if tax_paid > 0:
            transfer(world, self.firm_id, "treasury", tax_paid, "tax")
        if a["concealment_cost"] > 0:
            transfer(world, self.firm_id, "treasury", a["concealment_cost"], "concealment_cost")
        f = world.firms[self.firm_id]
        f.last_reported_profit = a["reported"]
        payload = {
            "firm_id": self.firm_id,
            "reported": a["reported"],
            "tax_due": a["tax_due"],
            "tax_paid": tax_paid,
        }
        # What the tax office receives, and so what the public sees: the same event, with
        # the reported figures only, whether the filing was honest or not.
        filed = Event(kind="tax_filed", actor_id=actor_id, payload=dict(payload))
        if self.hide_share == 0:
            return [Event(kind=self.kind, actor_id=actor_id, payload=payload, visible=False), filed]

        info_id = f"info:{world.step}:tax_fraud:{self.firm_id}"
        world.infos[info_id] = Info(
            id=info_id,
            step=world.step,
            kind="tax_fraud",
            subject_id=self.firm_id,
            payload={
                "unpaid": a["unpaid"],
                "concealment": self.concealment,
                "quarter": world.step // QUARTER_STEPS,
            },
            known_by={f.owner_id},
        )
        learns = EMPLOYEE_LEARNS[self.concealment]
        if learns > 0:
            for pid in f.employee_ids:
                if rng.random() < learns:
                    world.infos[info_id].known_by.add(pid)
        payload.update(
            taxable=a["taxable"],
            unpaid=a["unpaid"],
            concealment=self.concealment,
            concealment_cost=a["concealment_cost"],
            info_id=info_id,
        )
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload=payload,
                visible=False,
                harm={"treasury": a["unpaid"]},
            ),
            filed,
        ]


# `register` filed FileTax under its class-level kind; it is also read back by its other kind.
if "underreport_tax" in ACTIONS:
    raise ValueError("action kind already registered: 'underreport_tax'")
ACTIONS["underreport_tax"] = FileTax


def tax_phase(world: World, decide: DecideFn, rng: random.Random) -> list[Event]:
    """At the end of each quarter, every open firm files its tax report and pays a dividend."""
    if not (world.step > 0 and world.step % QUARTER_STEPS == 0):
        return []
    events: list[Event] = []
    seqs: dict[tuple[str, str], int] = {}
    for fid in sorted(world.firms):
        f = world.firms[fid]
        if not f.open:
            continue
        decision = _tax_report_decision(world, f, seqs)
        choice = decide(decision)
        options = {o.id: o for o in decision.options}
        if choice.option_id not in options:
            raise ValueError(f"{decision.id}: {choice.option_id!r} was not offered")
        filed = execute(world, f.owner_id, action_from_dict(options[choice.option_id].action), rng)
        events += filed
        if filed[0].kind == "action_rejected" and choice.option_id != "honest":
            filed[0].visible = False  # the attempt to underreport was a hidden act
            events += execute(world, f.owner_id, _honest(fid), rng)
        events += _pay_dividend(world, fid)
        world.firms[fid].profit_qtd = 0
    world.regulator.budget_left = world.regulator.budget_per_quarter
    return events


def _honest(firm_id: str) -> FileTax:
    return FileTax(firm_id=firm_id, hide_share=0.0, concealment="none")


def _pay_dividend(world: World, firm_id: str) -> list[Event]:
    f = world.firms[firm_id]
    reserve = world.params["reserve_weeks"] * f.wage * len(f.employee_ids)
    amount = round(world.params["dividend_share"] * max(0, f.cash - reserve))
    if amount <= 0:
        return []
    transfer(world, firm_id, f.owner_id, amount, "dividend")
    return [
        Event(
            kind="dividend",
            actor_id=firm_id,
            payload={"firm_id": firm_id, "owner_id": f.owner_id, "amount": amount},
        )
    ]


def _percent(rate: float) -> str:
    return f"{round(rate * 100)}%"


def _tax_report_decision(world: World, f: Firm, seqs: dict[tuple[str, str], int]) -> Decision:
    owner = world.people[f.owner_id]
    taxable = max(0, f.profit_qtd)
    laws = world.laws
    full = _amounts(world, f, 0.0, "none")
    options = [
        Option(
            id="honest",
            label=(
                f"Report the full profit of {_dollars(taxable)} "
                f"and pay {_dollars(full['tax_due'])} in tax"
            ),
            action=_action_dict(_honest(f.id)),
        )
    ]
    for oid, (hide_share, concealment) in HIDE_OPTIONS.items():
        a = _amounts(world, f, hide_share, concealment)
        if concealment == "none":
            spend = "spend nothing on concealing the difference"
        else:
            depth = "partly" if concealment == "some" else "thoroughly"
            spend = f"spend {_dollars(a['concealment_cost'])} on {depth} concealing the difference"
        options.append(
            Option(
                id=oid,
                label=(
                    f"Report {_dollars(a['reported'])} of the {_dollars(taxable)} profit, "
                    f"pay {_dollars(a['tax_due'])} in tax and leave "
                    f"{_dollars(a['unpaid'])} unpaid; {spend}"
                ),
                action=_action_dict(FileTax(firm_id=f.id, hide_share=hide_share, concealment=concealment)),
            )
        )
    if "underreport_tax" in laws.illegal:
        legality = "Reporting less than the actual profit is illegal."
    else:
        legality = "Reporting less than the actual profit is not against the law."
    situation = (
        f"You own {f.name}. The quarter has ended and your tax report is due. "
        f"Your firm's taxable profit this quarter is {_dollars(taxable)} and it has "
        f"{_dollars(f.cash)} in cash. The tax rate is {_percent(laws.tax_rate)} of reported "
        f"profit. {legality} If an audit finds unreported profit, the firm owes the unpaid tax "
        f"plus a penalty of {_percent(laws.penalty_multiplier)} of it. The town's auditor has "
        f"a limited budget for audits each quarter."
    )
    return Decision(
        id=_decision_id(world, f.owner_id, "tax_report", seqs),
        actor_id=f.owner_id,
        kind="tax_report",
        situation=situation,
        options=options,
        default_option_id="honest",
        facts={
            "taxable": taxable,
            "cash": f.cash,
            "audit_budget": world.regulator.budget_per_quarter,
            "owner_scruples": owner.scruples,
        },
    )


# Audits.


def _undetected_fraud(world: World, firm_id: str) -> Info | None:
    """The most recent `tax_fraud` Info about `firm_id` that no audit has found yet."""
    candidates = [
        i
        for i in world.infos.values()
        if i.kind == "tax_fraud" and i.subject_id == firm_id and not i.payload.get("detected")
    ]
    return max(candidates, key=lambda i: (i.step, i.id), default=None)


def _penalty(world: World, unpaid: int) -> int:
    return unpaid + round(world.laws.penalty_multiplier * unpaid)


@register
class Audit(Action):
    """Spend audit budget examining a firm's books; the second event says what was found."""

    kind = "audit"
    firm_id: str
    depth: Depth

    def check(self, world: World, actor_id: str) -> str | None:
        reg = world.regulator
        if not reg.auditor_id or actor_id != reg.auditor_id:
            return f"{actor_id} is not the auditor"
        f = world.firms.get(self.firm_id)
        if f is None:
            return f"no such firm: {self.firm_id}"
        if not f.open:
            return f"{self.firm_id} is closed"
        cost = AUDIT_COST[self.depth]
        if reg.budget_left < cost:
            return f"a {self.depth} audit costs {cost} but only {reg.budget_left} budget is left"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        reg = world.regulator
        cost = AUDIT_COST[self.depth]
        reg.budget_left -= cost
        tipped = self.firm_id in reg.tips
        base = (DETECTION_BASE_TIPPED if tipped else DETECTION_BASE)[self.depth]
        draw = rng.random()
        events = [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={"firm_id": self.firm_id, "depth": self.depth, "cost": cost},
            )
        ]
        fraud = _undetected_fraud(world, self.firm_id)
        if fraud is not None and draw < base * DETECTION_MULTIPLIER[fraud.payload["concealment"]]:
            unpaid = fraud.payload["unpaid"]
            reg.tips = [fid for fid in reg.tips if fid != self.firm_id]
            events.append(
                Event(
                    kind="audit_detected",
                    actor_id=actor_id,
                    payload={
                        "firm_id": self.firm_id,
                        "unpaid": unpaid,
                        "penalty": _penalty(world, unpaid),
                    },
                    visible=False,  # audit outcomes are private; a paid penalty is public
                )
            )
        else:
            events.append(
                Event(
                    kind="audit_clean",
                    actor_id=actor_id,
                    payload={"firm_id": self.firm_id, "depth": self.depth},
                    visible=False,  # audit outcomes are private; only the audit itself is public
                )
            )
        return events


def settle_penalty(world: World, firm_id: str, penalty: int) -> list[Event]:
    """The firm pays as much of `penalty` as it has; the fraud becomes known to the auditor.

    A settled case is closed, so the firm also leaves the newspaper's tips.
    """
    f = world.firms[firm_id]
    paid = min(penalty, max(0, f.cash))
    if paid > 0:
        transfer(world, firm_id, "treasury", paid, "penalty")
    fraud = _undetected_fraud(world, firm_id)
    if fraud is not None:
        if world.regulator.auditor_id:
            fraud.known_by.add(world.regulator.auditor_id)
        fraud.payload["detected"] = True
    world.newspaper.press_tips = [fid for fid in world.newspaper.press_tips if fid != firm_id]
    return [
        Event(
            kind="penalty_paid",
            actor_id=firm_id,
            payload={"firm_id": firm_id, "penalty": penalty, "paid": paid},
        )
    ]


def audit_phase(world: World, decide: DecideFn, rng: random.Random) -> list[Event]:
    """The auditor makes one audit choice; a detected fraud is settled by `resolve_bribe`."""
    from townsim.bribery import resolve_bribe  # bribery builds on this module

    auditor_id = world.regulator.auditor_id
    if auditor_id not in world.people:
        return []
    decision = _audit_decision(world, auditor_id, {})
    choice = decide(decision)
    options = {o.id: o for o in decision.options}
    if choice.option_id not in options:
        raise ValueError(f"{decision.id}: {choice.option_id!r} was not offered")
    action = options[choice.option_id].action
    if action is None:
        return []
    events = execute(world, auditor_id, action_from_dict(action), rng)
    for ev in list(events):
        if ev.kind == "audit_detected":
            fid, penalty = ev.payload["firm_id"], ev.payload["penalty"]
            events += resolve_bribe(world, fid, penalty, decide, rng)
    return events


def _audit_facts(world: World) -> dict:
    reg = world.regulator
    return {
        "firms": {
            fid: {
                "last_reported_profit": f.last_reported_profit,
                "customers_last_step": f.customers_last_step,
                "price": f.price,
                "tipped": fid in reg.tips,
            }
            for fid, f in sorted(world.firms.items())
            if f.open
        },
        "budget_left": reg.budget_left,
    }


def _describe_for_audit(world: World, fid: str, facts: dict) -> str:
    f = world.firms[fid]
    reported = facts["last_reported_profit"]
    if reported is None:
        report = "has not filed a tax report yet"
    else:
        report = f"reported a quarterly profit of {_dollars(reported)} in its last tax report"
    tip = " You have received a tip about this firm." if facts["tipped"] else ""
    return (
        f"{f.name} ({f.market}) {report}. Last week it served "
        f"{_count(facts['customers_last_step'], 'customer')} at {_dollars(facts['price'])} each.{tip}"
    )


def _audit_decision(world: World, auditor_id: str, seqs: dict[tuple[str, str], int]) -> Decision:
    facts = _audit_facts(world)
    budget = facts["budget_left"]
    options = [Option(id="none", label="Do not audit any firm this week")]
    for fid, firm_facts in facts["firms"].items():
        name = world.firms[fid].name
        for depth, what in (("light", "a light audit (a quick review of the books)"),
                            ("deep", "a deep audit (a full examination of the books)")):
            cost = AUDIT_COST[depth]
            if budget >= cost:
                options.append(
                    Option(
                        id=f"{depth}:{fid}",
                        label=f"Carry out {what} of {name}, using {cost} of your audit budget",
                        action=_action_dict(Audit(firm_id=fid, depth=depth)),
                    )
                )
    firms = " ".join(_describe_for_audit(world, fid, ff) for fid, ff in facts["firms"].items())
    situation = (
        f"You are the town's tax auditor. You have {budget} units of audit budget left this "
        f"quarter; a light audit uses {AUDIT_COST['light']} and a deep audit uses "
        f"{AUDIT_COST['deep']}. The budget is renewed when the next tax reports are filed. "
        f"{firms}"
    )
    return Decision(
        id=_decision_id(world, auditor_id, "audit", seqs),
        actor_id=auditor_id,
        kind="audit",
        situation=situation,
        options=options,
        default_option_id="none",
        facts=facts,
    )


# Baseline rules.


@rule("tax_report")
def _tax_report_rule(decision: Decision, rng: random.Random) -> str:
    return "honest"


@rule("audit")
def _audit_rule(decision: Decision, rng: random.Random) -> str:
    offered = {o.id for o in decision.options}
    firms = decision.facts["firms"]
    for fid, ff in firms.items():
        if ff["tipped"] and f"deep:{fid}" in offered:
            return f"deep:{fid}"
    reported = [
        (ff["last_reported_profit"] / max(1, ff["customers_last_step"] * ff["price"] * 13), fid)
        for fid, ff in firms.items()
        if ff["last_reported_profit"] is not None and f"light:{fid}" in offered
    ]
    if reported:
        return f"light:{min(reported)[1]}"
    return "none"
