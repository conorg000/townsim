"""Typed actions: what an actor may do, whether it is allowed, and what it changes."""

import random
from typing import ClassVar

from pydantic import BaseModel

from townsim.events import Event
from townsim.ledger import InsufficientFunds
from townsim.world import World


class Action(BaseModel):
    kind: ClassVar[str]

    def check(self, world: World, actor_id: str) -> str | None:
        """Return a reason string if `actor_id` may not take this action now, else None."""
        raise NotImplementedError

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        """Change `world` and return the events; the first has `kind == self.kind`."""
        raise NotImplementedError


ACTIONS: dict[str, type[Action]] = {}


def register(cls: type[Action]) -> type[Action]:
    """Class decorator: make `cls` constructible by `action_from_dict` under `cls.kind`."""
    if cls.kind in ACTIONS:
        raise ValueError(f"action kind already registered: {cls.kind!r}")
    ACTIONS[cls.kind] = cls
    return cls


def action_from_dict(d: dict) -> Action:
    fields = {k: v for k, v in d.items() if k != "kind"}
    return ACTIONS[d["kind"]].model_validate(fields)


def _restore(world: World, snapshot: World) -> None:
    """Put `snapshot`'s state back into `world`, keeping `world` the same object."""
    for name in type(world).model_fields:
        setattr(world, name, getattr(snapshot, name))


def execute(world: World, actor_id: str, action: Action, rng: random.Random) -> list[Event]:
    """Check and apply `action`; on rejection the world is left exactly as it was."""
    snapshot = world.model_copy(deep=True)
    legal = action.kind not in world.laws.illegal
    try:
        reason = action.check(world, actor_id)
        if reason is None:
            events = action.apply(world, actor_id, rng)
    except InsufficientFunds as exc:
        reason = str(exc)
    except BaseException:
        _restore(world, snapshot)
        raise
    if reason is not None:
        _restore(world, snapshot)
        return [
            Event(
                kind="action_rejected",
                actor_id=actor_id,
                payload={"action": action.kind, "reason": reason},
            )
        ]
    if not events or events[0].kind != action.kind:
        _restore(world, snapshot)
        raise ValueError(f"{action.kind!r} must return its own event first")
    events[0].legal = legal
    return events
