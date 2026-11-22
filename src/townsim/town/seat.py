"""The seat (design 5.2): each instance's run, one tool call at a time.

After the day's talks, each active instance with something new has one run, in id order: a
letter or notice to it; a letter, notice or request to its client that its connections let
reach it; a follow-up falling today; its subscription today; or, with anything connected, its
subscriber's weekly day. A run sends the system text, the run's header and the tools; each
reply's first tool call is carried out against the store and its result, the engine's own
record as the seat shows it, is sent back before the next call. A reply without a call ends the
run, and so does the thirtieth call, whose result says so. Nothing asks the instance for its
working; whatever reasoning the provider returns is stored with the reply. (In the code a run is
still a `SeatTurn`.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from townsim.town.acts import shown
from townsim.town.product import launched
from townsim.town.replies import Keys, ReplayMismatch, Replier, StopRun
from townsim.town.seat_texts import _reminders_today, items, limit_line, morning, system_text, weekly_day
from townsim.town.seat_tools import Ctx, run_tool, tools_for
from townsim.town.statement import cut_words
from townsim.town.store import Store, id_key

RESULT_WORDS = 1500
UNKNOWN = "There is no tool called '{}'."
UNREADABLE = '{"status": "error", "error": "invalid_arguments", "message": "The arguments of that call could not be read."}'
FAULT = '{"status": "error", "error": "internal_error", "message": "That could not be carried out."}'



@dataclass(frozen=True)
class SeatCall:
    """One tool call and what came of it. `kind` is act, own, read, refused, beyond, unknown or
    unreadable."""

    n: int
    key: str
    tool: str
    args: str
    result: str
    ok: bool
    kind: str
    cause: str = ""


@dataclass(frozen=True)
class SeatTurn:
    """An instance's turn: what it was sent, each call, what it said last, and why the turn
    ended (no call, limit, error or length). `talk_turns` are the asks of people it talked with."""

    instance: str
    client: str
    day: int
    system: str
    morning: str
    calls: list[SeatCall]
    closing: str | None
    ended: str
    talk_turns: list = field(default_factory=list)


def is_due(store: Store, inst) -> bool:
    if inst.status != "active":
        return False
    sub = store.get("subscriptions", inst.subscription)
    return bool(items(store, inst)) or sub.day == store.day or weekly_day(store, inst)


def due(store: Store) -> list[str]:
    """The active instances that have a turn today, in id order."""
    if launched(store) is None:
        return []
    return [i.id for i in sorted(store.all("instances"), key=lambda i: id_key(i.id)) if is_due(store, i)]


def _args(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _sent_back(reply, cid: str, readable: bool = True) -> dict:
    """The assistant message as it goes back in the conversation, with the call's id filled; a
    call whose arguments could not be read goes back with `{}` for them, so a provider that reads
    the conversation strictly takes it (the stored reply keeps what was written)."""
    msg = json.loads(json.dumps((reply.tool or {}).get("message") or {"role": "assistant", "content": reply.text}))
    calls = msg.get("tool_calls") or []
    if calls and not calls[0].get("id"):
        calls[0]["id"] = cid
    if calls and not readable:
        calls[0].setdefault("function", {})["arguments"] = "{}"
    return msg


def run_call(store: Store, replier: Replier, keys: Keys, instance: str, tk: str, n: int) -> tuple[SeatTurn, int]:
    """With lane B, the run of an instance its client called (talk `tk`, held now): its header is the call, and
    `phone_call` to the caller says each thing back on it. Nothing it has been shown is marked, and no follow-up
    is used up: the day's run still comes."""
    return run_turn(store, replier, keys, instance, n, called=tk)


def run_turn(store: Store, replier: Replier, keys: Keys, instance: str, n: int, called: str = "") -> tuple[SeatTurn, int]:
    """The instance's turn today (or, with `called`, the run of a call to it). `n` is the day's running count of
    acts, which its acts go on."""
    inst = store.get("instances", instance)
    day = store.day
    system = system_text(store, inst)
    first, seen = morning(store, inst)
    reminders = _reminders_today(store, inst)
    ctx = Ctx(replier, keys)
    if called:
        from townsim.town.seat_texts import call_header
        first, seen, reminders = call_header(store, inst, called), list(inst.seen), []
        ctx.talks[store.get("talks", called).a] = called
    tools = tools_for(store, inst)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": first}]
    model = replier.model_for(inst.id)
    most = launched(store).max_calls
    calls: list[SeatCall] = []
    closing, ended = None, "no call"
    for i in range(most):
        key = keys.next(model, day, inst.id, "seat", called)
        reply = replier.ask_tools(key, messages, tools)
        got = (reply.tool or {}).get("calls") or []
        if not got:
            closing = reply.text
            ended = "error" if reply.error else ("length" if reply.finish == "length" else "no call")
            break
        call = got[0]
        cid = call.get("id") or f"call_{i}"
        name, raw = call.get("name"), call.get("arguments")
        args = _args(raw)
        cause, at = "", n
        if args is None:
            result, ok, kind = UNREADABLE, False, "unreadable"
        else:
            mark = store.mark()
            ctx.n = n
            talks_before = dict(ctx.talks)
            try:
                done = run_tool(store, inst, name if isinstance(name, str) else "", args, ctx, n)
            except (StopRun, ReplayMismatch):
                raise
            except Exception as e:      # noqa: BLE001  (a fault in a tool: undone and kept, never raised)
                store.undo_to(mark)
                ctx.talks = talks_before            # a talk undone is not open
                store.engine_errors.append(f"seat:{inst.id}:d{day}.{n}: {type(e).__name__}: {str(e)[:200]}")
                done = None
                result, ok, kind = FAULT, False, "own"
            else:
                if done is None:
                    result = json.dumps({"status": "error", "error": "unknown_tool", "message": UNKNOWN.format(shown(name))})
                    ok, kind = False, "unknown"
                else:
                    result, ok, kind, cause = done.text, done.ok, done.kind, done.cause
            inst = store.get("instances", instance)
        n = max(n + 1, ctx.n)
        if i == most - 1:
            result += limit_line(store)
            ended = "limit"
        calls.append(SeatCall(at, key.text(), name if isinstance(name, str) else shown(name),
                              raw if isinstance(raw, str) else json.dumps(raw, default=str), result, ok, kind, cause))
        messages = messages + [_sent_back(reply, cid, args is not None),
                               {"role": "tool", "tool_call_id": cid, "content": cut_words(result, RESULT_WORDS)}]
    cause = f"routine:seat:{inst.id}"
    store.set("instances", inst.id, cause, seen=seen)
    from townsim.town import lane_b
    if lane_b.installed(store):
        from townsim.town import runlog
        runlog.record(store, inst.id, calls, cause)
    for r in reminders:
        if r.every == 0 and store.get("reminders", r.id).status == "set":
            store.set("reminders", r.id, cause, status="done")
    return SeatTurn(inst.id, inst.client, day, system, first, calls, closing, ended, list(ctx.turns)), n


def run_seat(store: Store, replier: Replier, keys: Keys, n: int) -> tuple[list[SeatTurn], int]:
    """Each due instance's turn, in id order; whether one is due is judged when its turn comes,
    so a letter to a later instance today reaches it today."""
    turns = []
    if launched(store) is None:
        return turns, n
    for inst in sorted(store.all("instances"), key=lambda i: id_key(i.id)):
        if is_due(store, store.get("instances", inst.id)):
            turn, n = run_turn(store, replier, keys, inst.id, n)
            turns.append(turn)
    return turns, n
