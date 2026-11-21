"""A decider played by a language model through LiteLLM.

The person is described in the second person from their name, persona, role and cash,
read from the live `people` mapping when each decision is made, followed by the
messages they received in recent weeks. The model sees the
situation and the options only: never the decision id or kind, the person's traits or
id, or `decision.facts`. The model name comes from configuration.
"""

import json
import time
from collections.abc import Callable

from townsim.decide import Choice, Decision, ModelUnavailable
from townsim.market import _dollars
from townsim.world import Person

REPLY_INSTRUCTION = 'Reply with JSON only: {"choice": "<id>"}'

ROLE_WORDS = {
    "owner": "You own a business.",
    "worker": "You work for a local business.",
    "auditor": "You are the town's tax auditor.",
    "journalist": "You are a reporter at the local paper.",
    "unemployed": "You are out of work.",
}

RETRY_SLEEPS = (1, 4)  # seconds to wait after the first and second failed attempts
DEFAULT_TIMEOUT = 60.0  # seconds a completion call may take before it counts as failed


INBOX_WEEKS_SHOWN = 4  # a person's prompt lists the messages of their last this many weeks


def system_text(person: Person) -> str:
    """The second-person description of `person` that opens every prompt to them."""
    return (
        f"You are {person.name}. {person.persona} {ROLE_WORDS[person.role]} "
        f"You have {_dollars(person.cash)} in cash."
    )


def describe_inbox_item(item: dict) -> str:
    """One inbox item as a line: a message with its sender, or a payment notice."""
    if "amount" in item:
        memo = f": {item['memo']}" if item.get("memo") else ""
        return f"Week {item['step']}: {item['from_desc']} paid you {_dollars(item['amount'])}{memo}"
    if item.get("report"):
        return f"Week {item['step']}, report from {item['from_desc']}: \"{item['text']}\""
    return f"Week {item['step']}, from {item['from_desc']}: \"{item['text']}\""


def inbox_block(person: Person, now: int | None) -> str:
    """The "Recent messages to you" block for the last weeks' inbox, or "" if it is empty.

    `now` is the current week; None (a decision with no week in its id) lists every item.
    """
    items = [
        i
        for i in person.inbox
        if (now is None or i["step"] > now - INBOX_WEEKS_SHOWN)
        and not (i["from_id"] == person.id and not i.get("from_assistant"))
    ]  # leaving out a person's own replies to their AI system, kept in their inbox for it
    if not items:
        return ""
    lines = "\n".join(f"- {describe_inbox_item(i)}" for i in items)
    return f"\n\nRecent messages to you:\n{lines}"


def _decision_step(decision: Decision) -> int | None:
    head = decision.id.split(":", 1)[0]
    return int(head) if head.lstrip("-").isdigit() else None


def render_prompt(person: Person, decision: Decision) -> list[dict]:
    """The system message describing `person` and the user message offering the options."""
    system = system_text(person) + inbox_block(person, _decision_step(decision))
    options = [f"{o.id}: {o.label}" for o in decision.options]
    user = "\n".join([decision.situation, "", *options, "", REPLY_INSTRUCTION])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_choice(text: str | None) -> str:
    """The `choice` string of the first JSON object in `text`, or "" if there is none."""
    if not text:
        return ""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        choice = obj.get("choice") if isinstance(obj, dict) else None
        return choice if isinstance(choice, str) else ""
    return ""


def _litellm_completion(**kwargs):
    import litellm

    litellm.suppress_debug_info = True  # it otherwise prints help text to stdout on errors
    return litellm.completion(**kwargs)


def _litellm_cost(resp) -> float | None:
    import litellm

    try:
        return litellm.completion_cost(completion_response=resp)
    except Exception:
        return None


def _no_cost(resp) -> None:
    return None


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


def _match_option(option_id: str, decision: Decision, meta: dict) -> str:
    """`option_id`, or the one offered id it is plainly short for.

    Some ids carry a prefix ("firm:f5"), and a model sometimes replies with the part after
    it ("f5"). When exactly one offered id ends that way it is taken as meant, and the
    reply as given is kept in `meta["replied"]`.
    """
    offered = [o.id for o in decision.options]
    if not option_id or option_id in offered:
        return option_id
    matches = [o for o in offered if o.endswith(":" + option_id)]
    if len(matches) != 1:
        return option_id
    meta["replied"] = option_id
    return matches[0]


class ModelDecider:
    """Asks `model` to choose for the person making each decision.

    `completion_fn` defaults to `litellm.completion`, and the reply is then priced with
    `litellm.completion_cost`; an injected `completion_fn` has no price, so `cost_usd` is
    None. A reply with no usable choice gives `Choice(option_id="")`, and so does a reply
    whose shape cannot be read (no choices, no message, content that is not text); its
    `meta["raw"]` is then None. Each call is given `timeout` seconds. An exception from
    `completion_fn`, a timeout included, is retried, up to 3 attempts in all, then
    `ModelUnavailable` is raised.
    """

    def __init__(
        self,
        model: str,
        temperature: float | None,
        people: dict[str, Person],
        completion_fn: Callable | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        timeout: float = DEFAULT_TIMEOUT,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        # Optional settings are sent only when set, so a model that rejects one is not sent it.
        optional = {
            "temperature": temperature,
            "reasoning_effort": reasoning_effort,
            "max_tokens": max_tokens,
        }
        self.params = {k: v for k, v in optional.items() if v is not None}
        self.people = people
        if completion_fn is None:
            self.completion_fn, self.cost_fn = _litellm_completion, _litellm_cost
        else:
            self.completion_fn, self.cost_fn = completion_fn, _no_cost
        self.sleep_fn = sleep_fn

    def decide(self, decision: Decision) -> Choice:
        messages = render_prompt(self.people[decision.actor_id], decision)
        text, meta = self._ask(messages)
        return Choice(option_id=_match_option(parse_choice(text), decision, meta), meta=meta)

    def write(self, key: str, person_id: str, messages: list[dict]) -> tuple[str | None, dict]:
        """Free text for `messages`, with the same token and cost meta as a decision."""
        return self._ask(messages)

    def _ask(self, messages: list[dict]) -> tuple[str | None, dict]:
        resp = self._complete(messages)
        text = reply_text(resp)
        usage = getattr(resp, "usage", None)
        meta = {
            "prompt_tokens": token_count(usage, "prompt_tokens"),
            "completion_tokens": token_count(usage, "completion_tokens"),
            "reasoning_tokens": token_count(
                getattr(usage, "completion_tokens_details", None), "reasoning_tokens"
            ),
            "cost_usd": self.cost_fn(resp),
            "raw": text,
            "reasoning": reasoning_text(resp),
            "finish_reason": finish_reason(resp),
        }
        return text, meta

    def _complete(self, messages: list[dict]):
        for attempt, wait in enumerate((*RETRY_SLEEPS, None), start=1):
            try:
                return self.completion_fn(
                    model=self.model,
                    messages=messages,
                    timeout=self.timeout,
                    **self.params,
                )
            except Exception as exc:
                if wait is None:
                    raise ModelUnavailable(
                        f"{self.model}: no answer after {attempt} attempts: {exc}"
                    ) from exc
                self.sleep_fn(wait)
