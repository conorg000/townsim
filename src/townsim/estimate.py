"""Prices of models and the arithmetic of what calls cost, without calling any model.

Token counts use one general-purpose tokenizer; each provider's own differs a little,
so any figure from here is an estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml
from pydantic import BaseModel, Field

TOKENIZER = "cl100k_base"
TOKENS_PER_MESSAGE = 4  # framing a chat API adds around each message
TOKENS_PER_REPLY_PRIMING = 3
DEFAULT_REPLY_TOKENS = 12


class Price(BaseModel):
    """Dollars per million tokens, as the provider lists them."""

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    # Hidden reasoning tokens a thinking model spends per call, billed as output.
    reasoning_tokens_per_call: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class Usage:
    """Calls made and the input tokens they sent."""

    calls: int = 0
    input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.calls + other.calls, self.input_tokens + other.input_tokens)


def load_prices(path: str) -> dict[str, Price]:
    """The prices in a YAML file of `model: {input_per_mtok, output_per_mtok}`.
    Raises ValueError if the file cannot be read or lists no valid prices."""
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read prices file {path}: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"prices file {path} lists no models")
    try:
        return {str(name): Price.model_validate(spec) for name, spec in raw.items()}
    except ValueError as exc:
        raise ValueError(f"bad prices file {path}: {exc}") from exc


def count_tokens(messages: list[dict]) -> int:
    """The input tokens a chat call with `messages` sends, counted offline."""
    import tiktoken  # imported here so nothing else loads it

    enc = tiktoken.get_encoding(TOKENIZER)
    body = sum(len(enc.encode(m["content"])) + TOKENS_PER_MESSAGE for m in messages)
    return body + TOKENS_PER_REPLY_PRIMING


def cost(usage: Usage, price: Price, reply_tokens: int = DEFAULT_REPLY_TOKENS) -> dict:
    """Calls, tokens in and out, and dollars for `usage` at `price`, with `reply_tokens`
    of output per call plus any reasoning tokens the price lists."""
    output_tokens = usage.calls * (reply_tokens + price.reasoning_tokens_per_call)
    dollars = (usage.input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1_000_000
    return {
        "calls": usage.calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": dollars,
    }
