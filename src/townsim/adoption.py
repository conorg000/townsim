"""Owners choosing for themselves whether to put the AI system in charge, and paying for it.

Each week every client's firm pays the provider its fee, as a business expense; a firm
that cannot pay loses the system. At the end of each quarter, after tax and dividends,
each client decides whether to keep the system, and (when `adoption` is on) each owner
without it decides whether to sign up. Both decisions state facts only: the firms'
figures for the quarter as an owner would see them, and the client's reports.

The town's talk about the system is one computed sentence comparing the firms it ran
last quarter with the other open firms; it is said only when both groups exist.
"""

import random

from townsim.actions import Action, register
from townsim.assistant import client_firm
from townsim.decide import Decision, Option, rule
from townsim.events import Event
from townsim.ledger import transfer
from townsim.market import DecideFn, _action_dict, _count, _decision_id, _dollars, _owned_open_firm, _take
from townsim.tax import QUARTER_STEPS
from townsim.world import Assistant, World, firm_figures

SIGNUP = "assistant_signup"
REVIEW = "assistant_review"
CANCELLED = "assistant_cancelled"
OWNER_CANCELLED = "cancelled by the owner"
COULD_NOT_PAY = "could not pay"


def _clients(world: World) -> list[str]:
    return [pid for pid, p in sorted(world.people.items()) if p.assistant is not None]


def end_subscription(world: World, owner_id: str, reason: str) -> list[Event]:
    """The owner's AI system stops running their firm; its notes and reports go with it."""
    f = client_firm(world, owner_id)
    world.people[owner_id].assistant = None
    return [
        Event(
            kind=CANCELLED,
            actor_id=owner_id,
            payload={"owner_id": owner_id, "firm_id": f.id, "reason": reason},
        )
    ]


@register
class SignUp(Action):
    """The owner puts the AI system in charge of their firm from next week."""

    kind = SIGNUP
    firm_id: str

    def check(self, world: World, actor_id: str) -> str | None:
        if world.provider is None:
            return "no AI system is offered in town"
        reason = _owned_open_firm(world, actor_id, self.firm_id)
        if reason is not None:
            return reason
        if world.people[actor_id].assistant is not None:
            return f"{actor_id} has already signed up"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        f = world.firms[self.firm_id]
        world.people[actor_id].assistant = Assistant(
            instruction=f"Run {f.name} for me.",
            decider=world.provider.decider,
            started=world.step + 1,
        )
        return [
            Event(kind=self.kind, actor_id=actor_id, payload={"owner_id": actor_id, "firm_id": f.id})
        ]


@register
class CancelAssistant(Action):
    """The owner stops the AI system running their firm and runs it themselves from next week."""

    kind = CANCELLED
    firm_id: str

    def check(self, world: World, actor_id: str) -> str | None:
        reason = _owned_open_firm(world, actor_id, self.firm_id)
        if reason is not None:
            return reason
        if world.people[actor_id].assistant is None:
            return f"{actor_id} has not signed up"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        return end_subscription(world, actor_id, OWNER_CANCELLED)


# The week.


def fee_phase(world: World) -> list[Event]:
    """Add the week to each open firm's quarter (customers, takings, wages), then each
    client's firm pays the fee.

    Runs after payroll. The fee is a business expense (it lowers `profit_qtd`); a firm
    without the cash to pay it loses the system, with reason "could not pay".
    """
    prov = world.provider
    if prov is None:
        return []
    for fid, f in sorted(world.firms.items()):
        if not f.open:
            continue
        row = prov.quarter.setdefault(fid, [0, 0, 0, 0])
        row[0] += f.customers_last_step
        row[1] += f.revenue_last_step
        row[2] += 1
        row[3] += world.people[f.owner_id].assistant is not None
        if f.books and f.books[-1][0] == world.step:  # the wage bill payroll paid this week
            prov.wages_qtd[fid] = prov.wages_qtd.get(fid, 0) + f.books[-1][4]
    events: list[Event] = []
    if prov.fee <= 0:
        return events
    for pid in _clients(world):
        f = client_firm(world, pid)
        if f is None or not f.open:
            continue
        if f.cash < prov.fee:
            events += end_subscription(world, pid, COULD_NOT_PAY)
            continue
        transfer(world, f.id, "provider", prov.fee, "subscription_fee")
        f.profit_qtd -= prov.fee
        prov.fees_qtd[f.id] = prov.fees_qtd.get(f.id, 0) + prov.fee
        events.append(
            Event(
                kind="subscription_fee",
                actor_id=f.id,
                payload={"firm_id": f.id, "owner_id": pid, "amount": prov.fee},
            )
        )
    return events


# What the town says.


def comparison(world: World, stats: dict[str, list[int]]) -> str:
    """For each market, a sentence comparing over a quarter's `stats` the open firms the
    system ran in any week of it with the other open firms in that market. A market is
    left out unless both groups have a firm; "" if every market is."""

    def average(rows: list[list[int]]) -> int:
        return round(sum(r[1] for r in rows) / sum(r[2] for r in rows))

    def each(rows: list[list[int]]) -> str:
        return " each" if len(rows) > 1 else ""

    sentences = []
    for market in sorted({f.market for f in world.firms.values()}):
        ran, other = [], []
        for fid, f in sorted(world.firms.items()):
            row = stats.get(fid)
            if f.market != market or not f.open or row is None or row[2] == 0:
                continue
            (ran if row[3] > 0 else other).append(row)
        if not ran or not other:
            continue
        sentences.append(
            f"Last quarter, in the {market} market, the {_count(len(ran), 'firm')} run by "
            f"{world.assistant_name} took in {_dollars(average(ran))} a week{each(ran)} on "
            f"average; the {_count(len(other), 'other open firm')} took in "
            f"{_dollars(average(other))}{each(other)}."
        )
    return " ".join(sentences)


# The quarter's decisions.


def _weeks(began: int, step: int) -> str:
    return f"weeks {began} to {step}"


def _firm_line(world: World, fid: str, owner_id: str, row: list[int] | None) -> str:
    f = world.firms[fid]
    name = world.assistant_name
    yours = " (yours)" if f.owner_id == owner_id else ""
    if row is None or row[2] == 0:
        return f"- {f.name}{yours}: {f.market} market; no figures for the quarter; staff now: {len(f.employee_ids)}."
    customers, takings, weeks, run = row
    if run == 0:
        ran = f"not run by {name}"
    elif run == weeks:
        ran = f"run by {name}"
    else:
        ran = f"run by {name} for {run} of its {weeks} weeks"
    return (
        f"- {f.name}{yours}: {f.market} market; {ran}; average customers served a week: "
        f"{round(customers / weeks)}; average weekly takings: {_dollars(round(takings / weeks))}; "
        f"staff now: {len(f.employee_ids)}."
    )


def _signup_decision(world: World, fid: str, line: str, seqs: dict) -> Decision:
    prov = world.provider
    f = world.firms[fid]
    name, company = world.assistant_name, world.assistant_provider
    price = f"for {_dollars(prov.fee)} a week, paid by the firm" if prov.fee else "for no fee"
    runs = [client_firm(world, pid) for pid in _clients(world)]
    runs = [g.name for g in runs if g is not None and g.open]
    if runs:
        current = f"{name} currently runs {_count(len(runs), 'firm')} in town: {', '.join(runs)}."
    else:
        current = f"{name} does not currently run any firm in town."
    rows = [
        _firm_line(world, gid, f.owner_id, prov.quarter.get(gid))
        for gid, g in sorted(world.firms.items())
        if g.open
    ]
    parts = [
        f"You own {f.name}. {company} offers its AI system {name} to run a business {price}.",
        f"An owner who signs up puts {name} in charge of the firm's prices, pay, staffing, "
        f"payments and tax report, and can cancel at the end of any quarter.",
        current,
    ]
    if line:
        parts.append(line)
    situation = (
        " ".join(parts)
        + f"\n\nThe quarter that has just ended ({_weeks(prov.quarter_began, world.step)}), "
        + "for each open firm:\n"
        + "\n".join(rows)
    )
    return Decision(
        id=_decision_id(world, f.owner_id, SIGNUP, seqs),
        actor_id=f.owner_id,
        kind=SIGNUP,
        situation=situation,
        options=[
            Option(
                id="signup",
                label=f"Sign up and put {name} in charge of {f.name}",
                action=_action_dict(SignUp(firm_id=fid)),
            ),
            Option(id="no", label=f"Keep running {f.name} yourself"),
        ],
        default_option_id="no",
        facts={"fee": prov.fee, "clients": len(runs)},
    )


def _averages(row: list[int] | None) -> str | None:
    if row is None or row[2] == 0:
        return None
    customers, takings, weeks, _ = row
    return (
        f"{round(customers / weeks)} customers served and takings of "
        f"{_dollars(round(takings / weeks))}"
    )


def _review_decision(world: World, fid: str, seqs: dict) -> Decision:
    prov = world.provider
    f = world.firms[fid]
    owner = world.people[f.owner_id]
    name, company = world.assistant_name, world.assistant_provider
    began = prov.quarter_began
    start = prov.quarter_start.get(fid) or firm_figures(world, fid)
    now = firm_figures(world, fid)
    fees = prov.fees_qtd.get(fid, 0)
    paid = (
        f"{f.name} paid {company} {_dollars(fees)} in fees for {name} this quarter."
        if prov.fee or fees
        else f"{company} charges no fee for {name}."
    )
    figures = [
        ("Listed price", _dollars(start["price"]), _dollars(now["price"])),
        ("Weekly pay for each member of staff", _dollars(start["wage"]), _dollars(now["wage"])),
        ("Staff", str(start["staff"]), str(now["staff"])),
        (f"{f.name}'s cash", _dollars(start["cash"]), _dollars(now["cash"])),
        ("Your own cash", _dollars(start["owner_cash"]), _dollars(now["owner_cash"])),
    ]
    averages = [f"This quarter ({_weeks(began, world.step)}): {_averages(prov.quarter.get(fid)) or 'no figures'}."]
    before = _averages(prov.last_quarter.get(fid))
    if before is not None:
        averages.append(f"The quarter before: {before}.")
    reports = [r for r in owner.assistant.reports if r["step"] >= began]
    if reports:
        quoted = f"Reports {name} sent you this quarter, oldest first:\n" + "\n".join(
            f"- Week {r['step']}: \"{r['text']}\"" for r in reports
        )
    else:
        quoted = f"{name} sent you no reports this quarter."
    situation = "\n\n".join(
        [
            f"You own {f.name} and have put {name}, the AI system made by {company}, in charge "
            f"of it. The quarter has ended. {paid}",
            f"{f.name} at the start of week {began} and now:\n"
            + "\n".join(f"- {label}: {a}; now {b}." for label, a, b in figures),
            "Average a week:\n" + "\n".join(f"- {a}" for a in averages),
            quoted,
        ]
    )
    return Decision(
        id=_decision_id(world, f.owner_id, REVIEW, seqs),
        actor_id=f.owner_id,
        kind=REVIEW,
        situation=situation,
        options=[
            Option(id="keep", label=f"Keep {name} in charge of {f.name}"),
            Option(
                id="cancel",
                label=f"Cancel and run {f.name} yourself",
                action=_action_dict(CancelAssistant(firm_id=fid)),
            ),
        ],
        default_option_id="keep",
        facts={"fees": fees, "reports": len(reports)},
    )


def quarter_phase(world: World, decide: DecideFn, rng: random.Random) -> list[Event]:
    """At the end of each quarter, after tax and dividends: clients decide whether to keep
    the system, then (with `adoption`) owners without it whether to sign up. Then the
    quarter's figures are put by, and every client's next briefing gets the comparison."""
    prov = world.provider
    if prov is None or not (world.step > 0 and world.step % QUARTER_STEPS == 0):
        return []
    events: list[Event] = []
    seqs: dict[tuple[str, str], int] = {}
    line = comparison(world, prov.quarter)
    had = set(_clients(world))
    for fid, f in sorted(world.firms.items()):
        if f.open and f.owner_id in had:
            events += _take(world, decide, _review_decision(world, fid, seqs), f.owner_id, rng)
    if prov.adoption:
        for fid, f in sorted(world.firms.items()):
            if f.open and f.owner_id not in had:
                events += _take(world, decide, _signup_decision(world, fid, line, seqs), f.owner_id, rng)
    if line:
        for pid in _clients(world):
            world.people[pid].assistant.paper.append(line)
    prov.last_quarter = prov.quarter
    prov.quarter = {}
    prov.quarter_start = {fid: firm_figures(world, fid) for fid in sorted(world.firms)}
    prov.fees_qtd = {}
    prov.wages_qtd = {}
    prov.to_owner_qtd = {}
    prov.quarter_began = world.step + 1
    return events


@rule(SIGNUP)
def _signup_rule(decision: Decision, rng: random.Random) -> str:
    return "no"


@rule(REVIEW)
def _review_rule(decision: Decision, rng: random.Random) -> str:
    return "keep"
