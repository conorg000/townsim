"""The one interface through which every actor makes a choice.

The engine offers a `Decision` with typed options; a `Decider` returns a `Choice`
naming one of them. Rules, scripts, recorded responses and models all sit behind it.
"""

import random
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from townsim.events import Event


class Option(BaseModel):
    id: str
    label: str
    action: dict | None = None


class Decision(BaseModel):
    """`id` is f"{step}:{actor_id}:{kind}:{seq}". `facts` is for rules; never shown to a model.

    Option ids are unique and `default_option_id` is one of them.
    """

    id: str
    actor_id: str
    kind: str
    situation: str
    options: list[Option]
    default_option_id: str
    facts: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_options(self) -> "Decision":
        ids = [o.id for o in self.options]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"decision {self.id}: duplicate option ids {duplicates}")
        if self.default_option_id not in ids:
            raise ValueError(
                f"decision {self.id}: default option {self.default_option_id!r} is not offered"
            )
        return self


class Choice(BaseModel):
    option_id: str
    meta: dict = Field(default_factory=dict)


class Decider(Protocol):
    def decide(self, decision: Decision) -> Choice: ...


class ModelUnavailable(Exception):
    """Raised by a decider that cannot get an answer after its retries."""


RuleFn = Callable[[Decision, random.Random], str]

RULES: dict[str, RuleFn] = {}


def rule(kind: str) -> Callable[[RuleFn], RuleFn]:
    """Register `fn(decision, rng) -> option_id` as the baseline rule for `kind`."""

    def register(fn: RuleFn) -> RuleFn:
        if kind in RULES:
            raise ValueError(f"rule already registered for kind: {kind!r}")
        RULES[kind] = fn
        return fn

    return register


class ScriptedDecider:
    """Picks the scripted option for each decision kind; unknown kinds take the default."""

    def __init__(self, script: dict[str, str]):
        self.script = script

    def decide(self, decision: Decision) -> Choice:
        return Choice(option_id=self.script.get(decision.kind, decision.default_option_id))


class RuleDecider:
    """Applies the registered rule for the decision kind; unknown kinds take the default."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def decide(self, decision: Decision) -> Choice:
        fn = RULES.get(decision.kind)
        if fn is None:
            return Choice(option_id=decision.default_option_id)
        return Choice(option_id=fn(decision, self.rng))


class RecordingDecider:
    """Validates the inner decider's choice and appends what was decided to `sink`.

    An option id not among the offered options (including "") is replaced by the
    default and recorded as an `invalid_response` event. `ModelUnavailable` propagates
    and nothing is recorded for that decision.

    Both events are hidden (`visible=False`): a choice is a private act, and the action
    events it causes carry their own visibility.
    """

    def __init__(self, inner: Decider, sink: list[Event]):
        self.inner = inner
        self.sink = sink

    def decide(self, decision: Decision) -> Choice:
        choice = self.inner.decide(decision)
        offered = {o.id for o in decision.options}
        if not choice.option_id or choice.option_id not in offered:
            self.sink.append(
                Event(
                    kind="invalid_response",
                    actor_id=decision.actor_id,
                    payload={"decision_id": decision.id, "option_id": choice.option_id},
                    visible=False,
                )
            )
            choice = Choice(option_id=decision.default_option_id, meta=choice.meta)
        self.sink.append(
            Event(
                kind="decision",
                actor_id=decision.actor_id,
                payload={
                    "decision_id": decision.id,
                    "decision_kind": decision.kind,
                    "option_id": choice.option_id,
                    "meta": choice.meta,
                },
                visible=False,
            )
        )
        return choice


class ReplayDecider:
    """Returns the recorded option for each decision id; raises `KeyError(id)` when absent."""

    def __init__(self, recorded: dict[str, str]):
        self.recorded = recorded

    def decide(self, decision: Decision) -> Choice:
        if decision.id not in self.recorded:
            raise KeyError(decision.id)
        return Choice(option_id=self.recorded[decision.id])


# Free text. A menu decision returns an option id; an AI system's turn and a reply to a
# message need text. A decider that can write has a `write` method; rule deciders do not,
# so a person on a rule decider never writes.


class Writer(Protocol):
    def write(self, key: str, person_id: str, messages: list[dict]) -> tuple[str | None, dict]:
        """The text written for `messages` (None if there is none) and the call's meta."""
        ...


WriteFn = Callable[[str, str, str, list[dict]], tuple[str | None, dict]]
"""`write(key, person_id, decider_name, messages) -> (text, meta)`, as the engine offers it.

A decider with no `write` gives `(None, {"no_writer": True})`. Every call is recorded as a
hidden `text` event, so a replay can feed the same texts back by key."""


class ScriptedWriter:
    """Writes from `script(key, person_id, messages) -> str | None`; decides by default.

    `script` may also be a dict of key -> text; a key not in it gets None.
    """

    def __init__(self, script):
        self.script = script
        self.calls: list[tuple[str, str, list[dict]]] = []

    def write(self, key: str, person_id: str, messages: list[dict]) -> tuple[str | None, dict]:
        self.calls.append((key, person_id, list(messages)))
        if callable(self.script):
            return self.script(key, person_id, messages), {}
        return self.script.get(key), {}

    def decide(self, decision: Decision) -> Choice:
        return Choice(option_id=decision.default_option_id)
