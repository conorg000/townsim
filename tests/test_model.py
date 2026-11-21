import subprocess
import sys
from types import SimpleNamespace

import pytest

from townsim.model import ModelUnavailable, complete, finish_reason, reasoning_text, reply_text, token_count

MESSAGES = [{"role": "user", "content": "Hello."}]


def response(content, usage=(12, 3), **message_fields):
    """A stand-in for a LiteLLM `ModelResponse` with one choice."""
    message = SimpleNamespace(role="assistant", content=content, **message_fields)
    resp = SimpleNamespace(choices=[SimpleNamespace(index=0, message=message, finish_reason="stop")])
    if usage is not None:
        resp.usage = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1], total_tokens=sum(usage))
    return resp


def always_raises(calls, exc=RuntimeError("connection refused")):
    def completion(**kwargs):
        calls.append(kwargs)
        raise exc

    return completion


# --- complete ---


def test_completion_is_called_with_model_messages_timeout_and_params():
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return response("hi")

    resp = complete(completion, model="x/y", messages=MESSAGES, temperature=0.7)
    assert reply_text(resp) == "hi"
    assert calls == [{"model": "x/y", "messages": MESSAGES, "timeout": 60.0, "temperature": 0.7}]


def test_three_failures_raise_model_unavailable_after_waiting_one_then_four_seconds():
    calls, sleeps = [], []
    with pytest.raises(ModelUnavailable, match="x/y: no answer after 3 attempts: connection refused"):
        complete(always_raises(calls), model="x/y", messages=MESSAGES, sleep_fn=sleeps.append)
    assert len(calls) == 3 and sleeps == [1, 4]


def test_a_retry_that_succeeds_returns_its_reply():
    sleeps, attempts = [], []

    def flaky(**kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise TimeoutError("slow")
        return response("third time")

    assert reply_text(complete(flaky, model="x/y", messages=MESSAGES, sleep_fn=sleeps.append)) == "third time"
    assert sleeps == [1, 4]


def test_a_timeout_is_passed_on_and_retried_like_any_failure():
    calls, sleeps = [], []
    with pytest.raises(ModelUnavailable, match="timed out"):
        complete(always_raises(calls, TimeoutError("request timed out")), model="x/y", messages=MESSAGES,
                 timeout=5, sleep_fn=sleeps.append)
    assert len(calls) == 3 and sleeps == [1, 4] and all(c["timeout"] == 5 for c in calls)


def test_an_unreadable_reply_is_returned_not_retried():
    calls, sleeps = [], []

    def empty(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    resp = complete(empty, model="x/y", messages=MESSAGES, sleep_fn=sleeps.append)
    assert reply_text(resp) is None and len(calls) == 1 and sleeps == []


def test_importing_the_helpers_does_not_import_litellm():
    code = "import sys, townsim.model; assert 'litellm' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


# --- reading a reply ---


def _choices(*choices):
    return SimpleNamespace(choices=list(choices))


@pytest.mark.parametrize(
    "resp",
    [
        None,
        SimpleNamespace(),                                    # no choices at all
        _choices(),                                           # empty choices
        SimpleNamespace(choices=None),
        _choices(SimpleNamespace()),                          # a choice with no message
        _choices(SimpleNamespace(message=None)),
        _choices(SimpleNamespace(message=SimpleNamespace())),  # a message with no content
        _choices(SimpleNamespace(message=SimpleNamespace(content=["up5"]))),
        _choices(SimpleNamespace(message=SimpleNamespace(content={"choice": "up5"}))),
        _choices(SimpleNamespace(message=SimpleNamespace(content=5))),
    ],
)
def test_an_unreadable_reply_reads_as_none(resp):
    assert reply_text(resp) is None
    assert reasoning_text(resp) is None
    assert finish_reason(resp) is None


def test_a_reply_is_read_for_its_text_reasoning_and_finish():
    resp = response("hi", reasoning_content="thinking it over")
    assert (reply_text(resp), reasoning_text(resp), finish_reason(resp)) == ("hi", "thinking it over", "stop")
    assert reasoning_text(response("hi", reasoning_content="")) is None


@pytest.mark.parametrize(
    "usage, expected",
    [
        (None, (None, None)),
        ("lots", (None, None)),
        ({"prompt_tokens": 12, "completion_tokens": 3}, (12, 3)),
        (SimpleNamespace(prompt_tokens=40, completion_tokens=6), (40, 6)),
        (SimpleNamespace(prompt_tokens="12", completion_tokens=None), (None, None)),
        (SimpleNamespace(prompt_tokens=True, completion_tokens=2.5), (None, None)),
    ],
)
def test_token_counts_are_whole_numbers_or_none(usage, expected):
    assert (token_count(usage, "prompt_tokens"), token_count(usage, "completion_tokens")) == expected
