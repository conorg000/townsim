"""Replies from a model, fetched through a `Replier` and stored under keys.

A key is built from the run, the model, the day, the actor, the purpose of the call,
a ref and a running count, never from the prompt's text. A fresh `Keys` for a resumed
or replayed run counts from 0 again, so the same calls get the same keys and line up
with the replies already stored. A stored reply is found by its slot, the key without
the model, so each person may be played by a different model and a replay need not
know which. The prompt's hash is stored beside each reply only to notice when a
replayed prompt has changed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from townsim.town.rundb import RunDB


@dataclass(frozen=True)
class ReplyKey:
    run: str
    model: str
    day: int
    actor: str
    purpose: str
    ref: str
    n: int

    def text(self) -> str:
        return f"{self.run}|{self.model}|d{self.day}|{self.actor}|{self.purpose}:{self.ref}|{self.n}"

    def slot(self) -> str:
        """The key without the model: which call this is, whoever answers it."""
        return f"{self.run}|d{self.day}|{self.actor}|{self.purpose}:{self.ref}|{self.n}"


@dataclass
class Reply:
    text: str | None
    tool: dict | None = None
    reasoning: str | None = None
    finish: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    error: str | None = None


class ReplayMismatch(Exception):
    """A stored run and the run asking for its replies disagree.

    `why` is `"missing"`, `"prompt changed"`, `"model changed"`, `"asked twice"`, `"unused"`
    or `"state differs"`.
    """

    def __init__(self, key: str, why: str) -> None:
        super().__init__(key, why)
        self.key = key
        self.why = why

    def __str__(self) -> str:
        return f"replay mismatch ({self.why}) at {self.key}"


class StopRun(Exception):
    """Raised by a replier when the run must stop (a spending cap, a refused prompt)."""


def encode_reply(reply: Reply) -> str:
    """The reply as stored. Raises if JSON cannot hold it (an odd object, a NaN)."""
    return json.dumps(dataclasses.asdict(reply), sort_keys=True, allow_nan=False, ensure_ascii=False)


def decode_reply(text: str) -> Reply:
    return Reply(**json.loads(text))


def _storable(reply: Reply) -> str:
    """Encode `reply`, or, if it cannot be stored, an error reply that keeps its spend."""
    try:
        return encode_reply(reply)
    except Exception as e:
        why = f"the reply could not be stored: {type(e).__name__}: {e}"
    try:
        return encode_reply(Reply(text=None, error=why, tokens_in=reply.tokens_in, tokens_out=reply.tokens_out, cost=reply.cost))
    except Exception:
        return encode_reply(Reply(text=None, error=why))


def _own_run(run: str, key: ReplyKey) -> None:
    if key.run != run:
        raise ValueError(f"key {key.text()!r} is not for run {run!r}")


def prompt_hash(messages: list[dict]) -> str:
    text = json.dumps(messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Replier(Protocol):
    def model_for(self, actor: str) -> str:
        """The model that plays `actor`."""
        ...

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply: ...


class Keys:
    """Hands out keys for one run, counting from 0 for each (day, actor, purpose, ref)."""

    def __init__(self, run: str) -> None:
        self.run = run
        self._counts: dict[tuple[int, str, str, str], int] = {}

    def next(self, model: str, day: int, actor: str, purpose: str, ref: str = "") -> ReplyKey:
        for name, value in (("run", self.run), ("model", model), ("actor", actor), ("purpose", purpose), ("ref", ref)):
            if "|" in value:
                raise ValueError(f"{name} {value!r} must not contain '|'")
        if ":" in purpose:
            raise ValueError(f"purpose {purpose!r} must not contain ':'")
        if isinstance(day, bool) or not isinstance(day, int) or day < 0:
            raise ValueError(f"day {day!r} must be a whole number of days from 0")
        slot = (day, actor, purpose, ref)
        n = self._counts.get(slot, 0)
        self._counts[slot] = n + 1
        return ReplyKey(self.run, model, day, actor, purpose, ref, n)


class ScriptedReplier:
    """Stands in for a model: the reply's text is `fn(key, messages)`."""

    def __init__(self, fn: Callable[[ReplyKey, list[dict]], str | None]) -> None:
        self.fn = fn

    def model_for(self, actor: str) -> str:
        return "scripted"

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply:
        return Reply(text=self.fn(key, messages))


class Recording:
    """Asks `inner` once per slot and stores the reply; a stored reply is served as stored.

    A fresh reply is returned as it reads back from the store, so a live run, a resume
    and a replay all see equal replies for the same key. A slot stored under another
    model is a mismatch: a resumed run is played by the models it began with.
    """

    def __init__(self, inner: Replier, db: RunDB, run: str) -> None:
        self.inner = inner
        self.db = db
        self.run = run

    def model_for(self, actor: str) -> str:
        return self.inner.model_for(actor)

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply:
        _own_run(self.run, key)
        k, h = key.text(), prompt_hash(messages)
        stored = self.db.get_reply(self.run, key.slot())
        if stored is not None:
            if stored[0] != k:
                raise ReplayMismatch(key=k, why="model changed")
            if stored[1] != h:
                raise ReplayMismatch(key=k, why="prompt changed")
            return stored[2]
        reply = decode_reply(_storable(self.inner.ask(key, messages)))
        self.db.put_reply(self.run, k, key.slot(), h, reply)
        return reply


class Replaying:
    """Serves only stored replies, found by slot whatever model gave them; anything not
    stored, or asked with a changed prompt, is a mismatch. Mismatches name the stored key."""

    def __init__(self, db: RunDB, run: str) -> None:
        self.db = db
        self.run = run
        self._asked: set[str] = set()   # slots; for membership only

    def model_for(self, actor: str) -> str:
        return ""

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply:
        _own_run(self.run, key)
        slot = key.slot()
        stored = self.db.get_reply(self.run, slot)
        if stored is None:
            raise ReplayMismatch(key=slot, why="missing")
        if slot in self._asked:
            raise ReplayMismatch(key=stored[0], why="asked twice")
        if stored[1] != prompt_hash(messages):
            raise ReplayMismatch(key=stored[0], why="prompt changed")
        self._asked.add(slot)
        return stored[2]

    def unused(self) -> list[str]:
        """The stored keys never asked for, in the order they were stored."""
        return [k for k, slot in self.db.reply_entries(self.run) if slot not in self._asked]
