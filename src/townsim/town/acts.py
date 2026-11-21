"""Acts: how a person changes the town (design 3.3).

A person's reply is a list of acts, each naming an operation and its arguments. The
reply is data: `apply_act` applies one act in full or not at all and never raises,
whatever the arguments hold. Operations are `OpSpec`s in `REGISTRY`; `ops_core`
registers the first eight when imported.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from townsim.town.store import Store

MAX_CENTS = 100_000_000
_SHOWN = 40
_DOLLARS = re.compile(r"\d{1,3}(,\d{3})+(\.\d*)?|\d+(\.\d*)?|\.\d+", re.ASCII)


@dataclass(frozen=True)
class Act:
    actor: str
    principal: str
    op: Any
    args: Any


@dataclass(frozen=True)
class Outcome:
    ok: bool
    msg: str
    act: Act


@dataclass(frozen=True)
class OpSpec:
    """`check` returns why the act cannot be done (without `Not done:`) or None; it changes
    nothing. `do` makes the change with the cause it is given and returns what the actor is told."""

    name: str
    form: str
    desc: str
    hours: float
    check: Callable[[Store, Act], str | None]
    do: Callable[[Store, Act, str], str]


REGISTRY: dict[str, OpSpec] = {}


def _same(a: OpSpec, b: OpSpec) -> bool:
    """The same spec, even from a reloaded module: same text and same named functions."""
    def where(f: Callable) -> tuple:
        return getattr(f, "__module__", None), getattr(f, "__qualname__", None)
    return ((a.name, a.form, a.desc, a.hours, where(a.check), where(a.do))
            == (b.name, b.form, b.desc, b.hours, where(b.check), where(b.do))
            and "<lambda>" not in (where(a.check)[1] or "") + (where(a.do)[1] or ""))


def register(spec: OpSpec) -> None:
    """Add an operation. Registering the same spec again is harmless; a different one
    under a name already taken raises ValueError."""
    old = REGISTRY.get(spec.name)
    if old is not None and old is not spec and not _same(old, spec):
        raise ValueError(f"an operation called {spec.name!r} is already registered")
    REGISTRY[spec.name] = spec


def _acting_for_themselves(store: Store, act: Act) -> str | None:
    if act.principal != act.actor:
        return "you have no connection that allows you to act for them."
    return None


AUTHORISERS: list[Callable[[Store, Act], str | None]] = [_acting_for_themselves]


def shown(value: Any) -> str:
    """A value from a reply as it is quoted back: a string as written, anything else as
    its repr, cut to 40 characters either way."""
    text = value if isinstance(value, str) else repr(value)
    return text[:_SHOWN]


def _not_done(act: Act, reason: str) -> Outcome:
    return Outcome(False, f"Not done: {reason}", act)


def apply_act(store: Store, act: Act, n: int) -> Outcome:
    """Apply one act in full or not at all. `n` numbers the act within the day's acts."""
    if not isinstance(act.args, dict):
        return _not_done(act, "that operation was not written in a form that can be read.")
    spec = REGISTRY.get(act.op.strip().lower()) if isinstance(act.op, str) else None
    if spec is None:
        return _not_done(act, f"there is no operation called '{shown(act.op)}'.")
    cause = f"op:{act.actor}:{spec.name}:d{store.day}.{n}"
    before = store.mark()
    try:
        for authorise in AUTHORISERS:
            reason = authorise(store, act)
            if reason is not None:
                return _not_done(act, reason)
        person = store.get("people", act.actor)
        if person.hours_left < spec.hours:
            return _not_done(act, "you have no time left this week for that.")
        reason = spec.check(store, act)
        if reason is not None:
            return _not_done(act, reason)
        with store.transaction():
            told = spec.do(store, act, cause)
            store.set("people", act.actor, cause, hours_left=round(person.hours_left - spec.hours, 2))
    except Exception as e:  # a bug in an operation: the act is undone and reported, never raised
        if store.mark() != before:
            store.undo_to(before)
        store.engine_errors.append(f"{cause}: {type(e).__name__}: {str(e)[:200]}")
        return _not_done(act, "that could not be carried out.")
    return Outcome(True, told, act)


def dollars(value: Any) -> Decimal | None:
    """A sum of dollars as written in a reply, exactly: an int, a finite float, or a string
    such as `"$1,308.50"` or `"5."` whose commas only separate thousands. None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip().removeprefix("$").strip()
        return Decimal(text.replace(",", "")) if _DOLLARS.fullmatch(text) else None
    return None


def to_cents(value: Any) -> int | None:
    """Dollars as written in a reply, in cents, rounded half up: `12.5` and `"$1,308.50"` both
    read. None for a bool, a non-number, zero, a negative, or more than 100,000,000 cents."""
    sum_ = dollars(value)
    if sum_ is None or sum_ <= 0 or sum_ > Decimal(MAX_CENTS) / 100:
        return None
    cents = int((sum_ * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return cents if cents > 0 else None


def cut(text: str, n: int = 80) -> str:
    """`text`, or its first `n` characters and `…` when it is longer."""
    return text if len(text) <= n else text[:n] + "\u2026"


def clean_text(value: Any) -> str | None:
    """Text a person wrote, without control characters other than newline and tab; None
    for a non-string."""
    if not isinstance(value, str):
        return None
    return "".join(c for c in value if c in "\n\t" or unicodedata.category(c) != "Cc")


def resolve(store: Store, name: Any) -> str | None:
    """The id of the person, household or org called `name`, ignoring case and surrounding spaces."""
    if not isinstance(name, str):
        return None
    wanted = name.strip().casefold()
    for table in ("people", "households", "orgs"):
        for row in store.all(table):
            if row.name.casefold() == wanted:
                return row.id
    return None


def name_of_who(store: Store, who: str) -> str:
    """The name of a person, household or org."""
    for table in ("people", "households", "orgs"):
        if store.has(table, who):
            return store.get(table, who).name
    return who


def account_of(store: Store, who: str) -> str:
    """A person's household account; a household's or org's own id."""
    if store.has("people", who):
        return store.get("people", who).household
    return who


def forms(store: Store, actor: str) -> list[str]:
    """The written form of every operation, in the order they were registered."""
    return [spec.form for spec in REGISTRY.values()]
