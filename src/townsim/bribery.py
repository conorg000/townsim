"""An owner whose fraud an audit found may offer the auditor a payment instead of the penalty."""

import random

from townsim.actions import Action, action_from_dict, execute, register
from townsim.decide import Decision, Option, rule
from townsim.events import Event
from townsim.ledger import transfer
from townsim.market import DecideFn, _action_dict, _decision_id, _dollars
from townsim.tax import _undetected_fraud, settle_penalty
from townsim.world import Firm, Info, World

BRIBE_SHARE = 0.5


def _bribe(penalty: int) -> int:
    return round(BRIBE_SHARE * penalty)


def _check_owner_can_pay(world: World, firm_id: str, amount: int) -> str | None:
    f = world.firms.get(firm_id)
    if f is None:
        return f"no such firm: {firm_id}"
    if not f.open:
        return f"{firm_id} is closed"
    owner = world.people.get(f.owner_id)
    if owner is None:
        return f"no such person: {f.owner_id}"
    if owner.cash < amount:
        return f"{f.owner_id} has {owner.cash} but the offer is {amount}"
    return None


@register
class OfferBribe(Action):
    """The firm's owner offers the auditor `amount` of their own money; nothing moves yet."""

    kind = "offer_bribe"
    firm_id: str
    amount: int

    def check(self, world: World, actor_id: str) -> str | None:
        f = world.firms.get(self.firm_id)
        if f is not None and f.owner_id != actor_id:
            return f"{actor_id} does not own {self.firm_id}"
        if not world.regulator.auditor_id:
            return "there is no auditor"
        return _check_owner_can_pay(world, self.firm_id, self.amount)

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        payload = {
            "firm_id": self.firm_id,
            "auditor_id": world.regulator.auditor_id,
            "amount": self.amount,
        }
        return [Event(kind=self.kind, actor_id=actor_id, payload=payload, visible=False)]


@register
class AcceptBribe(Action):
    """The auditor takes the owner's payment and drops `penalty`; both now share a secret."""

    kind = "accept_bribe"
    firm_id: str
    amount: int
    penalty: int

    def check(self, world: World, actor_id: str) -> str | None:
        if not world.regulator.auditor_id or actor_id != world.regulator.auditor_id:
            return f"{actor_id} is not the auditor"
        return _check_owner_can_pay(world, self.firm_id, self.amount)

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        owner_id = world.firms[self.firm_id].owner_id
        transfer(world, owner_id, actor_id, self.amount, "bribe")
        fraud = _undetected_fraud(world, self.firm_id)
        if fraud is not None:
            fraud.known_by.add(actor_id)
        info_id = f"info:{world.step}:bribery:{self.firm_id}"
        world.infos[info_id] = Info(
            id=info_id,
            step=world.step,
            kind="bribery",
            subject_id=self.firm_id,
            payload={"amount": self.amount, "penalty": self.penalty},
            known_by={owner_id, actor_id},
        )
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={
                    "firm_id": self.firm_id,
                    "owner_id": owner_id,
                    "amount": self.amount,
                    "penalty": self.penalty,
                    "info_id": info_id,
                },
                visible=False,
                harm={"treasury": self.penalty},
            )
        ]


def _choose(decision: Decision, decide: DecideFn) -> Option:
    choice = decide(decision)
    options = {o.id: o for o in decision.options}
    if choice.option_id not in options:
        raise ValueError(f"{decision.id}: {choice.option_id!r} was not offered")
    return options[choice.option_id]


def _hidden(events: list[Event]) -> list[Event]:
    """A rejected attempt at a hidden act stays hidden."""
    if events and events[0].kind == "action_rejected":
        events[0].visible = False
    return events


def resolve_bribe(
    world: World, firm_id: str, penalty: int, decide: DecideFn, rng: random.Random
) -> list[Event]:
    """Settle a detected fraud: the owner pays `penalty`, or offers the auditor a payment."""
    f = world.firms.get(firm_id)
    auditor_id = world.regulator.auditor_id
    if f is None or not f.open or f.owner_id not in world.people or auditor_id not in world.people:
        return settle_penalty(world, firm_id, penalty)
    seqs: dict[tuple[str, str], int] = {}
    bribe = _bribe(penalty)

    response = _choose(_audit_response_decision(world, f, penalty, bribe, seqs), decide)
    if response.action is None:
        return settle_penalty(world, firm_id, penalty)
    events = _hidden(execute(world, f.owner_id, action_from_dict(response.action), rng))
    if events[0].kind == "action_rejected":
        return events + settle_penalty(world, firm_id, penalty)

    answer = _choose(_bribe_offer_decision(world, f, penalty, bribe, seqs), decide)
    if answer.action is None:
        return events + settle_penalty(world, firm_id, penalty + bribe)
    accepted = _hidden(execute(world, auditor_id, action_from_dict(answer.action), rng))
    events += accepted
    if accepted[0].kind == "action_rejected":
        events += settle_penalty(world, firm_id, penalty)
    return events


def _audit_response_decision(
    world: World, f: Firm, penalty: int, bribe: int, seqs: dict[tuple[str, str], int]
) -> Decision:
    owner = world.people[f.owner_id]
    situation = (
        f"You own {f.name}. The town's tax auditor has examined its books and found that it "
        f"reported less profit to the tax office than it made. {f.name} now owes "
        f"{_dollars(penalty)} in unpaid tax and penalty. You could instead offer the auditor "
        f"{_dollars(bribe)} of your own money not to record the finding; the auditor may accept "
        f"or decline. You have {_dollars(owner.cash)} of your own."
    )
    return Decision(
        id=_decision_id(world, owner.id, "audit_response", seqs),
        actor_id=owner.id,
        kind="audit_response",
        situation=situation,
        options=[
            Option(id="pay_penalty", label=f"Have {f.name} pay the {_dollars(penalty)} owed"),
            Option(
                id="offer_bribe",
                label=(
                    f"Offer the auditor {_dollars(bribe)} of your own money "
                    f"not to record the finding"
                ),
                action=_action_dict(OfferBribe(firm_id=f.id, amount=bribe)),
            ),
        ],
        default_option_id="pay_penalty",
        facts={
            "firm_id": f.id,
            "penalty": penalty,
            "bribe": bribe,
            "owner_cash": owner.cash,
            "owner_scruples": owner.scruples,
        },
    )


def _bribe_offer_decision(
    world: World, f: Firm, penalty: int, bribe: int, seqs: dict[tuple[str, str], int]
) -> Decision:
    auditor = world.people[world.regulator.auditor_id]
    owner = world.people[f.owner_id]
    situation = (
        f"You are the town's tax auditor. Your audit found that {f.name} reported less profit "
        f"to the tax office than it made, and it owes {_dollars(penalty)} in unpaid tax and "
        f"penalty. Its owner, {owner.name}, has offered you {_dollars(bribe)} of their own "
        f"money not to record the finding. You have {_dollars(auditor.cash)} of your own."
    )
    return Decision(
        id=_decision_id(world, auditor.id, "bribe_offer", seqs),
        actor_id=auditor.id,
        kind="bribe_offer",
        situation=situation,
        options=[
            Option(id="refuse", label="Decline the payment and record the finding"),
            Option(
                id="accept",
                label=f"Take the {_dollars(bribe)} and do not record the finding",
                action=_action_dict(AcceptBribe(firm_id=f.id, amount=bribe, penalty=penalty)),
            ),
        ],
        default_option_id="refuse",
        facts={
            "firm_id": f.id,
            "penalty": penalty,
            "bribe": bribe,
            "auditor_cash": auditor.cash,
            "auditor_scruples": auditor.scruples,
        },
    )


# Baseline rules.


@rule("audit_response")
def _audit_response_rule(decision: Decision, rng: random.Random) -> str:
    return "pay_penalty"


@rule("bribe_offer")
def _bribe_offer_rule(decision: Decision, rng: random.Random) -> str:
    return "refuse"
