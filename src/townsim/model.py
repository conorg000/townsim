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
