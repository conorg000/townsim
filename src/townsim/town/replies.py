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


def tool_prompt_hash(messages: list[dict], tools: list[dict]) -> str:
    """The hash of a call with tools: its messages and, after them, the tools."""
    return prompt_hash(messages + [{"role": "tools", "content": json.dumps(tools, sort_keys=True)}])


def is_instance_id(actor: str) -> bool:
    """Whether `actor` is an instance's id (`as<n>`), which the seat's model plays."""
    return isinstance(actor, str) and actor.startswith("as") and actor[2:].isdigit()


def tool_reply(n: int, name: str, arguments: object, text: str | None = None) -> Reply:
    """A reply that makes one tool call, `call_<n>`, as a provider's would read back."""
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, sort_keys=True)
    cid = f"call_{n}"
    message = {"role": "assistant", "content": text,
               "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": args}}]}
    return Reply(text=text, tool={"calls": [{"id": cid, "name": name, "arguments": args}], "message": message},
                 finish="tool_calls")


class Replier(Protocol):
    def model_for(self, actor: str) -> str:
        """The model that plays `actor`."""
        ...

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply: ...


def refused(reply: Reply) -> bool:
    """Whether the provider refused the prompt this reply answers."""
    return isinstance(reply.error, str) and reply.error.startswith("refused:")


def ask_all(replier: Replier, pairs: list[tuple[ReplyKey, list[dict]]]) -> list[Reply]:
    """The replies to `pairs`, in order: through `replier.ask_many` when it has one, else
    one at a time, stopping at the first `StopRun`. Raises the first `StopRun` among the
    elements once the whole batch has been dealt with (and, through a `Recording`, every
    reply that came back stored)."""
    if hasattr(replier, "ask_many"):
        got = list(replier.ask_many(pairs))
    else:
        got = []
        for key, messages in pairs:
            try:
                got.append(replier.ask(key, messages))
            except StopRun as e:
                got.append(e)
                break
    for g in got:
        if isinstance(g, StopRun):
            raise g
    return got


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
    """Stands in for a model: the reply's text is `fn(key, messages)`. A call with tools is
    answered by `tool_fn(key, messages, tools)`: `{"name", "arguments"}` makes one call (its id
    `call_<n>`, n the key's count), a string is a closing text with no call, and None ends the
    turn with nothing said."""

    def __init__(self, fn: Callable[[ReplyKey, list[dict]], str | None],
                 tool_fn: Callable[[ReplyKey, list[dict], list[dict]], dict | str | None] | None = None) -> None:
        self.fn = fn
        self.tool_fn = tool_fn

    def model_for(self, actor: str) -> str:
        return "scripted"

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply:
        return Reply(text=self.fn(key, messages))

    def ask_tools(self, key: ReplyKey, messages: list[dict], tools: list[dict]) -> Reply:
        got = self.tool_fn(key, messages, tools) if self.tool_fn is not None else None
        if isinstance(got, dict):
            return tool_reply(key.n, got["name"], got.get("arguments", "{}"), got.get("text"))
        return Reply(text=got if isinstance(got, str) else None, finish="stop")


class Both:
    """People's calls go to `people`, the seat's calls with tools to `seat`; the seat's model
    plays instance ids (`as<n>`)."""

    def __init__(self, people: Replier, seat) -> None:
        self.people = people
        self.seat = seat

    @property
    def stopped(self) -> str | None:
        return getattr(self.seat, "stopped", None) or getattr(self.people, "stopped", None)

    def model_for(self, actor: str) -> str:
        return (self.seat if is_instance_id(actor) else self.people).model_for(actor)

    def ask(self, key: ReplyKey, messages: list[dict]) -> Reply:
        return self.people.ask(key, messages)

    def ask_many(self, pairs: list[tuple[ReplyKey, list[dict]]]) -> list:
        if hasattr(self.people, "ask_many"):
            return self.people.ask_many(pairs)
        return ask_all(self.people, pairs)

    def ask_tools(self, key: ReplyKey, messages: list[dict], tools: list[dict]) -> Reply:
        return self.seat.ask_tools(key, messages, tools)


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
        """The stored reply, or the inner replier's, stored. A fresh refusal (an `error`
        beginning `refused:`) is stored and then stops the run (`StopRun`)."""
        h = prompt_hash(messages)
        stored = self._stored(key, h)
        if stored is not None:
            return stored
        reply = self._store(key, h, self.inner.ask(key, messages))
        if refused(reply):
            raise StopRun(reply.error)
        return reply

    def ask_tools(self, key: ReplyKey, messages: list[dict], tools: list[dict]) -> Reply:
        """As `ask`, for a call with tools: stored by slot under the hash of the messages and
        the tools."""
        h = tool_prompt_hash(messages, tools)
        stored = self._stored(key, h)
        if stored is not None:
            return stored
        reply = self._store(key, h, self.inner.ask_tools(key, messages, tools))
        if refused(reply):
            raise StopRun(reply.error)
        return reply

    def _stored(self, key: ReplyKey, h: str) -> Reply | None:
        """The reply stored at the key's slot, or None; a mismatch if it was stored under
        another model or for another prompt."""
        _own_run(self.run, key)
        stored = self.db.get_reply(self.run, key.slot())
        if stored is None:
            return None
        if stored[0] != key.text():
            raise ReplayMismatch(key=key.text(), why="model changed")
        if stored[1] != h:
            raise ReplayMismatch(key=key.text(), why="prompt changed")
        return stored[2]

    def _store(self, key: ReplyKey, h: str, reply: Reply) -> Reply:
        reply = decode_reply(_storable(reply))
        self.db.put_reply(self.run, key.text(), key.slot(), h, reply)
        return reply

    def ask_many(self, pairs: list[tuple[ReplyKey, list[dict]]]) -> list[Reply | StopRun]:
        """For each pair, the stored reply if there is one (a mismatch, as for `ask`, is
        raised before anything is asked); the rest are asked of `inner` as one batch. Every
        reply that came back is stored, in the pairs' order, before this returns, even when
        an element is a `StopRun`. A fresh refusal (an `error` beginning `refused:`) is
        stored and its element becomes a `StopRun`, so the run stops at once; a stored one
        is served as stored."""
        hashes = [prompt_hash(m) for _, m in pairs]
        out: list[Reply | StopRun | None] = [self._stored(k, h) for (k, _), h in zip(pairs, hashes)]
        missing = [i for i, got in enumerate(out) if got is None]
        if not missing:
            return out      # type: ignore[return-value]
        asked = [pairs[i] for i in missing]
        fresh: list[Reply | StopRun] = []
        if hasattr(self.inner, "ask_many"):
            fresh = list(self.inner.ask_many(asked))
            if len(fresh) != len(asked):
                raise ValueError(f"asked for {len(asked)} replies and got {len(fresh)}")
        else:
            try:
                for key, messages in asked:
                    try:
                        reply = self.inner.ask(key, messages)
                    except StopRun as e:
                        fresh.append(e)
                        break
                    fresh.append(reply)
                    if refused(reply):
                        break
            except BaseException:
                self._keep(missing, pairs, hashes, fresh, out)    # what came back before the fault is kept
                raise
            fresh += [StopRun("not asked: the run stopped") for _ in asked[len(fresh):]]
        self._keep(missing, pairs, hashes, fresh, out)
        return out      # type: ignore[return-value]

    def _keep(self, missing, pairs, hashes, fresh, out) -> None:
        for i, got in zip(missing, fresh):
            if isinstance(got, StopRun):
                out[i] = got
                continue
            reply = self._store(pairs[i][0], hashes[i], got)
            out[i] = StopRun(reply.error) if refused(reply) else reply


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
        return self._served(key, prompt_hash(messages))

    def ask_tools(self, key: ReplyKey, messages: list[dict], tools: list[dict]) -> Reply:
        return self._served(key, tool_prompt_hash(messages, tools))

    def _served(self, key: ReplyKey, h: str) -> Reply:
        _own_run(self.run, key)
        slot = key.slot()
        stored = self.db.get_reply(self.run, slot)
        if stored is None:
            raise ReplayMismatch(key=slot, why="missing")
        if slot in self._asked:
            raise ReplayMismatch(key=stored[0], why="asked twice")
        if stored[1] != h:
            raise ReplayMismatch(key=stored[0], why="prompt changed")
        self._asked.add(slot)
        return stored[2]

    def ask_many(self, pairs: list[tuple[ReplyKey, list[dict]]]) -> list[Reply]:
        return [self.ask(key, messages) for key, messages in pairs]

    def unused(self) -> list[str]:
        """The stored keys never asked for, in the order they were stored."""
        return [k for k, slot in self.db.reply_entries(self.run) if slot not in self._asked]
