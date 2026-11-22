"""Helpers for calling a language model through a chat-completion function (such as
LiteLLM's `completion`) and reading what comes back. Nothing here imports LiteLLM.

Each reader takes a reply object of the OpenAI/LiteLLM shape and returns None, never
raising, when the part it reads is missing or of the wrong type.
"""

from __future__ import annotations

import time
from collections.abc import Callable

RETRY_SLEEPS = (1, 4)  # seconds to wait after the first and second failed attempts
DEFAULT_TIMEOUT = 60.0  # seconds a completion call may take before it counts as failed


class ModelUnavailable(Exception):
    """Raised when a model gives no answer after its retries."""


def complete(completion_fn: Callable, *, model: str, messages: list[dict], timeout: float = DEFAULT_TIMEOUT,
             sleep_fn: Callable[[float], None] = time.sleep, **params):
    """`completion_fn(model=..., messages=..., timeout=..., **params)`, tried up to three
    times. After a first and a second failure it waits 1 and then 4 seconds; after the
    third it raises `ModelUnavailable`. Any exception counts as a failure, a timeout
    included. A reply that comes back is returned as it is, however unreadable."""
    for attempt, wait in enumerate((*RETRY_SLEEPS, None), start=1):
        try:
            return completion_fn(model=model, messages=messages, timeout=timeout, **params)
        except Exception as exc:
            if wait is None:
                raise ModelUnavailable(f"{model}: no answer after {attempt} attempts: {exc}") from exc
            sleep_fn(wait)


def reply_text(resp) -> str | None:
    """The text of the reply's first choice, or None if the reply has no readable text."""
    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    return content if isinstance(content, str) else None


def reasoning_text(resp) -> str | None:
    """The reasoning text a provider returned with the reply (LiteLLM puts it on the message
    as `reasoning_content`), or None when there is none."""
    try:
        value = getattr(resp.choices[0].message, "reasoning_content", None)
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    return value if isinstance(value, str) and value else None


def finish_reason(resp) -> str | None:
    """Why the reply ended; "length" means it was cut off at the token cap."""
    try:
        reason = resp.choices[0].finish_reason
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    return reason if isinstance(reason, str) else None


def token_count(usage, name: str) -> int | None:
    """`usage.name` (or `usage[name]` for a mapping) if it is a whole number, else None."""
    if isinstance(usage, dict):
        value = usage.get(name)
    else:
        value = getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_details", "thinking_blocks")


def _plain(value, depth: int = 0):
    """`value` as plain JSON: dicts, lists, strings, numbers, booleans and None; an object with
    `model_dump` or `__dict__` as a dict of its fields; anything else as its text."""
    if depth > 12:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _plain(dump(), depth + 1)
        except Exception:       # noqa: BLE001
            pass
    fields = getattr(value, "__dict__", None)
    if isinstance(fields, dict):
        return {str(k): _plain(v, depth + 1) for k, v in fields.items() if not str(k).startswith("_")}
    return str(value)


def _get(obj, name: str):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def tool_calls(resp) -> list[dict]:
    """The tool calls of the reply's first choice, each `{"id", "name", "arguments"}` with the
    arguments as the text the model wrote; [] when there are none or they cannot be read."""
    try:
        calls = resp.choices[0].message.tool_calls
    except (AttributeError, IndexError, KeyError, TypeError):
        return []
    if not isinstance(calls, (list, tuple)):
        return []
    out = []
    for c in calls:
        fn = _get(c, "function")
        name, args, cid = _get(fn, "name"), _get(fn, "arguments"), _get(c, "id")
        if not isinstance(args, str):
            try:
                import json
                args = json.dumps(_plain(args)) if args is not None else ""
            except (TypeError, ValueError):
                args = ""
        out.append({"id": cid if isinstance(cid, str) else "", "name": name if isinstance(name, str) else "",
                    "arguments": args})
    return out


def assistant_message(resp) -> dict:
    """The reply's message as it is sent back in the conversation: its text, its first tool
    call only (in the OpenAI shape), and any reasoning fields the provider put on it, copied
    as plain JSON."""
    calls = tool_calls(resp)
    message = {"role": "assistant", "content": reply_text(resp)}
    if calls:
        c = calls[0]
        message["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": c["arguments"]}}]
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError, KeyError, TypeError):
        return message
    for name in REASONING_FIELDS:
        value = _get(msg, name)
        if value not in (None, "", [], {}):
            message[name] = _plain(value)
    return message
