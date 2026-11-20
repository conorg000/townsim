"""Typed actions: what an actor may do, whether it is allowed, and what it changes."""

import copy
import random
from typing import Any, ClassVar

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


def _save(value: Any) -> tuple:
    """Record `value` and its contents, keeping a reference to each model and container."""
    if isinstance(value, BaseModel):
        fields = {name: _save(getattr(value, name)) for name in type(value).model_fields}
        return ("model", value, fields)
    if isinstance(value, dict):
        return ("dict", value, [(k, _save(v)) for k, v in value.items()])
    if isinstance(value, list):
        return ("list", value, [_save(v) for v in value])
    if isinstance(value, set):
        return ("set", value, list(value))
    return ("value", None, copy.deepcopy(value))


def _load(saved: tuple) -> Any:
    """Put each recorded object back to its recorded contents and return it."""
    kind, obj, data = saved
    if kind == "model":
        for name, child in data.items():
            setattr(obj, name, _load(child))
    elif kind == "dict":
        items = {k: _load(child) for k, child in data}
        obj.clear()
        obj.update(items)
    elif kind == "list":
        obj[:] = [_load(child) for child in data]
    elif kind == "set":
        obj.clear()
        obj.update(data)
    else:
        obj = data
    return obj


def _rejected(action: Action, actor_id: str, reason: str) -> list[Event]:
    return [
        Event(
            kind="action_rejected",
            actor_id=actor_id,
            payload={"action": action.kind, "reason": reason},
        )
    ]


def execute(world: World, actor_id: str, action: Action, rng: random.Random) -> list[Event]:
    """Check and apply `action`; on rejection the world is left exactly as it was.

    Restoring is in place: every model and container that existed before the call
    is put back where it was, with its old contents, so references callers hold
    into the world stay attached to it.
    """
    snapshot = _save(world)
    legal = action.kind not in world.laws.illegal
    try:
        reason = action.check(world, actor_id)
    except BaseException:
        _load(snapshot)
        raise
    if reason is not None:
        return _rejected(action, actor_id, reason)
    try:
        events = action.apply(world, actor_id, rng)
    except InsufficientFunds as exc:
        _load(snapshot)
        return _rejected(action, actor_id, str(exc))
    except BaseException:
        _load(snapshot)
        raise
    if not events or events[0].kind != action.kind:
        _load(snapshot)
        raise ValueError(f"{action.kind!r} must return its own event first")
    events[0].legal = legal
    return events
