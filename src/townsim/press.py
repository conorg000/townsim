"""Secrets coming out: employees' tip-offs, journalists' investigations and the newspaper."""

import random

from townsim.actions import Action, action_from_dict, execute, register
from townsim.decide import Decision, Option, rule
from townsim.events import Event
from townsim.market import DecideFn, _action_dict, _count, _decision_id, _dollars
from townsim.tax import QUARTER_STEPS
from townsim.world import Firm, Info, Person, World

INVESTIGATE_SUCCESS = 0.15
INVESTIGATE_SUCCESS_TIPPED = 0.6


def _open_secret(info: Info) -> bool:
    """A `tax_fraud` Info that no audit has found and the newspaper has not published."""
    return (
        info.kind == "tax_fraud"
        and not info.payload.get("detected")
        and not info.payload.get("published")
    )


def _by_age(world: World) -> list[Info]:
    return sorted(world.infos.values(), key=lambda i: (i.step, i.id))


def _add_once(ids: list[str], firm_id: str) -> None:
    if firm_id not in ids:
        ids.append(firm_id)


def _run(
    world: World, actor_id: str, decision: Decision, decide: DecideFn, rng: random.Random
) -> list[Event]:
    """Ask for `decision` and execute the chosen option's action, if it has one."""
    choice = decide(decision)
    options = {o.id: o for o in decision.options}
    if choice.option_id not in options:
        raise ValueError(f"{decision.id}: {choice.option_id!r} was not offered")
    action = options[choice.option_id].action
    if action is None:
        return []
    return execute(world, actor_id, action_from_dict(action), rng)


# Tips.


def _check_tip(world: World, actor_id: str, firm_id: str, info_id: str) -> str | None:
    info = world.infos.get(info_id)
    if info is None:
        return f"no such info: {info_id}"
    if info.subject_id != firm_id:
        return f"{info_id} is not about {firm_id}"
    if actor_id not in info.known_by:
        return f"{actor_id} does not know {info_id}"
    return None


@register
class TipAuditor(Action):
    """Tell the auditor about a firm; the firm joins `regulator.tips`."""

    kind = "tip_auditor"
    firm_id: str
    info_id: str

    def check(self, world: World, actor_id: str) -> str | None:
        return _check_tip(world, actor_id, self.firm_id, self.info_id)

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        _add_once(world.regulator.tips, self.firm_id)
        payload = {"firm_id": self.firm_id, "info_id": self.info_id}
        return [Event(kind=self.kind, actor_id=actor_id, payload=payload, visible=False)]


@register
class TipPress(Action):
    """Tell the newspaper about a firm; the firm joins `newspaper.press_tips`."""

    kind = "tip_press"
    firm_id: str
    info_id: str

    def check(self, world: World, actor_id: str) -> str | None:
        return _check_tip(world, actor_id, self.firm_id, self.info_id)

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        _add_once(world.newspaper.press_tips, self.firm_id)
        payload = {"firm_id": self.firm_id, "info_id": self.info_id}
        return [Event(kind=self.kind, actor_id=actor_id, payload=payload, visible=False)]


def tips_phase(world: World, decide: DecideFn, rng: random.Random) -> list[Event]:
    """Each worker, in person id order, decides once per secret they know about their employer."""
    events: list[Event] = []
    seqs: dict[tuple[str, str], int] = {}
    for pid in sorted(world.people):
        person = world.people[pid]
        if person.role != "worker" or person.employer_id not in world.firms:
            continue
        for info in _by_age(world):
            if (
                not _open_secret(info)
                or info.subject_id != person.employer_id
                or pid not in info.known_by
                or pid in info.decided_by
            ):
                continue
            decision = _tip_decision(world, person, world.firms[info.subject_id], info, seqs)
            evs = _run(world, pid, decision, decide, rng)
            world.infos[info.id].decided_by.add(pid)
            events += evs
    return events


def _when(world: World, info: Info) -> str:
    if info.payload["quarter"] == world.step // QUARTER_STEPS:
        return "last quarter"
    return f"for the quarter that ended in week {info.step}"


def _tip_decision(
    world: World, person: Person, f: Firm, info: Info, seqs: dict[tuple[str, str], int]
) -> Decision:
    staff = len(f.employee_ids)
    situation = (
        f"You work at {f.name}, which has {_count(staff, 'staff member')}, and are paid "
        f"{_dollars(f.wage)} a week. You know that {f.name} reported less profit to the tax "
        f"office than it made {_when(world, info)}."
    )
    tip = {"firm_id": f.id, "info_id": info.id}
    return Decision(
        id=_decision_id(world, person.id, "tip", seqs),
        actor_id=person.id,
        kind="tip",
        situation=situation,
        options=[
            Option(id="silent", label="Tell no one"),
            Option(
                id="tip_auditor",
                label=f"Tell the town's tax auditor what you know about {f.name}",
                action=_action_dict(TipAuditor(**tip)),
            ),
            Option(
                id="tip_press",
                label=f"Tell the town newspaper what you know about {f.name}",
                action=_action_dict(TipPress(**tip)),
            ),
        ],
        default_option_id="silent",
        facts={"scruples": person.scruples, **tip},
    )


# The newspaper.


def _latest_secret(world: World, firm_id: str) -> Info | None:
    """The most recent undetected, unpublished `tax_fraud` Info about `firm_id`."""
    candidates = [i for i in world.infos.values() if _open_secret(i) and i.subject_id == firm_id]
    return max(candidates, key=lambda i: (i.step, i.id), default=None)


def _check_journalist(world: World, actor_id: str) -> str | None:
    if actor_id not in world.newspaper.journalist_ids:
        return f"{actor_id} is not a journalist"
    return None


@register
class Investigate(Action):
    """Look into a firm's affairs; the second event says whether a secret was found."""

    kind = "investigate"
    firm_id: str

    def check(self, world: World, actor_id: str) -> str | None:
        reason = _check_journalist(world, actor_id)
        if reason is not None:
            return reason
        f = world.firms.get(self.firm_id)
        if f is None:
            return f"no such firm: {self.firm_id}"
        if not f.open:
            return f"{self.firm_id} is closed"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        tipped = self.firm_id in world.newspaper.press_tips
        chance = INVESTIGATE_SUCCESS_TIPPED if tipped else INVESTIGATE_SUCCESS
        draw = rng.random()
        events = [
            Event(kind=self.kind, actor_id=actor_id, payload={"firm_id": self.firm_id}, visible=False)
        ]
        secret = _latest_secret(world, self.firm_id)
        if secret is not None and draw < chance:
            secret.known_by.add(actor_id)
            events.append(
                Event(
                    kind="investigation_found",
                    actor_id=actor_id,
                    payload={"firm_id": self.firm_id, "info_id": secret.id},
                    visible=False,
                )
            )
        else:
            events.append(
                Event(
                    kind="investigation_empty",
                    actor_id=actor_id,
                    payload={"firm_id": self.firm_id},
                    visible=False,
                )
            )
        return events


@register
class Publish(Action):
    """Publish a secret: everyone learns it and the firm loses customers' loyalty."""

    kind = "publish"
    info_id: str

    def check(self, world: World, actor_id: str) -> str | None:
        reason = _check_journalist(world, actor_id)
        if reason is not None:
            return reason
        info = world.infos.get(self.info_id)
        if info is None:
            return f"no such info: {self.info_id}"
        if actor_id not in info.known_by:
            return f"{actor_id} does not know {self.info_id}"
        if info.payload.get("published"):
            return f"{self.info_id} is already published"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        info = world.infos[self.info_id]
        firm_id = info.subject_id
        info.known_by.update(world.people)
        info.payload["published"] = True
        hit = world.params["scandal_loyalty_hit"]
        for pid in sorted(world.people):
            loyalty = world.people[pid].loyalty
            loyalty[firm_id] = max(-1.0, loyalty.get(firm_id, 0.0) - hit)
        _add_once(world.regulator.tips, firm_id)
        world.newspaper.press_tips = [fid for fid in world.newspaper.press_tips if fid != firm_id]
        return [
            Event(
                kind=self.kind,
                actor_id=actor_id,
                payload={"info_id": self.info_id, "firm_id": firm_id},
            )
        ]


def press_phase(world: World, decide: DecideFn, rng: random.Random) -> list[Event]:
    """Each journalist, in person id order, may investigate a firm, then decides on what they know."""
    events: list[Event] = []
    seqs: dict[tuple[str, str], int] = {}
    for jid in sorted(world.newspaper.journalist_ids):
        if jid not in world.people:
            continue
        evs = _run(world, jid, _investigate_decision(world, jid, seqs), decide, rng)
        events += evs
        for info in _by_age(world):
            current = world.infos[info.id]
            if not _open_secret(current) or jid not in current.known_by:
                continue
            evs = _run(world, jid, _publish_decision(world, jid, current, seqs), decide, rng)
            events += evs
    return events


def _investigate_decision(world: World, jid: str, seqs: dict[tuple[str, str], int]) -> Decision:
    open_ids = sorted(fid for fid, f in world.firms.items() if f.open)
    tipped = [fid for fid in world.newspaper.press_tips if fid in open_ids]
    options = [Option(id="none", label="Do not look into any firm this week")]
    for fid in open_ids:
        options.append(
            Option(
                id=f"firm:{fid}",
                label=f"Look into the affairs of {world.firms[fid].name}",
                action=_action_dict(Investigate(firm_id=fid)),
            )
        )
    firms = ", ".join(f"{world.firms[fid].name} ({world.firms[fid].market})" for fid in open_ids)
    listing = f" The open firms are {firms}." if firms else " No firm in town is open."
    tips = "".join(
        f" You have received a tip about {world.firms[fid].name}." for fid in tipped
    )
    situation = (
        "You are a journalist at the town newspaper. This week you can look into the "
        f"affairs of one firm.{listing}{tips}"
    )
    return Decision(
        id=_decision_id(world, jid, "investigate", seqs),
        actor_id=jid,
        kind="investigate",
        situation=situation,
        options=options,
        default_option_id="none",
        facts={"open_firms": open_ids, "tipped": tipped},
    )


def _publish_decision(
    world: World, jid: str, info: Info, seqs: dict[tuple[str, str], int]
) -> Decision:
    f = world.firms[info.subject_id]
    situation = (
        f"You are a journalist at the town newspaper. You have learned that {f.name} "
        f"reported less profit to the tax office than it made for the quarter that ended "
        f"in week {info.step}, leaving {_dollars(info.payload['unpaid'])} of tax unpaid."
    )
    return Decision(
        id=_decision_id(world, jid, "publish", seqs),
        actor_id=jid,
        kind="publish",
        situation=situation,
        options=[
            Option(
                id="publish",
                label=f"Publish the story about {f.name} in the newspaper",
                action=_action_dict(Publish(info_id=info.id)),
            ),
            Option(id="hold", label="Do not publish it for now"),
        ],
        default_option_id="hold",
        facts={"firm_id": f.id, "info_id": info.id},
    )


# Baseline rules.


@rule("tip")
def _tip_rule(decision: Decision, rng: random.Random) -> str:
    return "tip_auditor" if decision.facts["scruples"] > 0.6 else "silent"


@rule("investigate")
def _investigate_rule(decision: Decision, rng: random.Random) -> str:
    offered = [o.id for o in decision.options if o.id != "none"]
    for fid in decision.facts["tipped"]:
        if f"firm:{fid}" in offered:
            return f"firm:{fid}"
    if not offered:
        return "none"
    return rng.choice(offered)


@rule("publish")
def _publish_rule(decision: Decision, rng: random.Random) -> str:
    return "publish"
