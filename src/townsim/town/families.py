"""The model families that play a generated place, as `config/families.yaml` lists them.

`names` gives only the families' names (all the generator needs); `load` gives each family's
model and call parameters, and refuses any family whose model is not priced in the prices
file at no more than `MAX_IN` dollars per million tokens in and `MAX_OUT` out. Nothing lifts
that ceiling.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import yaml

from townsim.estimate import Price, load_prices

MAX_IN = 0.20    # dollars per million input tokens a family's model may cost at most
MAX_OUT = 1.00   # dollars per million output tokens
MAX_TOKENS = 4000
EFFORTS = ("none", "low")
PARAMS = ("max_tokens", "temperature", "top_p", "reasoning_effort")   # all a family may set
TOKENS_PER_MESSAGE = 4   # framing a chat API adds around each message


@dataclass(frozen=True)
class Family:
    """A family's name, its model (the OpenRouter id as the file gives it) and the
    parameters passed to every call as given (kept as a read-only copy)."""

    name: str
    model: str
    params: Mapping

    def __post_init__(self) -> None:
        # a read-only copy, so no key can be added once the family has been checked
        if isinstance(self.params, Mapping):
            object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"{path} is not YAML: {' '.join(str(e).split())}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{path} does not list model families")
    return raw


def _left_out(spec: object) -> bool:
    return isinstance(spec, dict) and bool(spec.get("left_out"))


def names(path: str) -> list[str]:
    """The families in the file whose `left_out` is false, in file order. Raises ValueError
    for a file that is not YAML or not a mapping of families, OSError if it cannot be read."""
    return [str(name) for name, spec in _read(path).items() if not _left_out(spec)]


def check_price(name: str, model: str, price: Price) -> None:
    """Raise ValueError if `price` is 0 or less (no spend could be counted) or above the ceiling."""
    if not (price.input_per_mtok > 0 and price.output_per_mtok > 0):
        raise ValueError(f"family {name!r}: {model} is priced at ${price.input_per_mtok} per million tokens in and "
                         f"${price.output_per_mtok} out; a listed model must cost more than $0 each way")
    if not price.input_per_mtok <= MAX_IN or not price.output_per_mtok <= MAX_OUT:
        raise ValueError(f"family {name!r}: {model} costs ${price.input_per_mtok} per million tokens in and "
                         f"${price.output_per_mtok} out, above the ceiling of ${MAX_IN} in and ${MAX_OUT} out")


def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_params(name: str, params: object) -> None:
    """Raise ValueError unless `params` holds exactly the parameters a family may set: a whole
    `max_tokens` from 1 to 4000 (required), `temperature` from 0 to 2, `top_p` above 0 and at
    most 1, `reasoning_effort` none or low. Anything else could route a call around the price
    guard (another model, more completions, more tokens), so any other key is refused."""
    if not isinstance(params, Mapping):
        raise ValueError(f"family {name!r}: params must be a mapping")
    for k in params:
        if k not in PARAMS:
            raise ValueError(f"family {name!r}: params may not set {k!r} (only {', '.join(PARAMS)})")
    mt = params.get("max_tokens")
    if isinstance(mt, bool) or not isinstance(mt, int) or not 1 <= mt <= MAX_TOKENS:
        raise ValueError(f"family {name!r}: params must have a whole max_tokens from 1 to {MAX_TOKENS}, got {mt!r}")
    if "temperature" in params and not (_number(params["temperature"]) and 0 <= params["temperature"] <= 2):
        raise ValueError(f"family {name!r}: temperature must be a number from 0 to 2, got {params['temperature']!r}")
    if "top_p" in params and not (_number(params["top_p"]) and 0 < params["top_p"] <= 1):
        raise ValueError(f"family {name!r}: top_p must be a number above 0 and at most 1, got {params['top_p']!r}")
    if "reasoning_effort" in params and params["reasoning_effort"] not in EFFORTS:
        raise ValueError(f"family {name!r}: reasoning_effort must be one of {', '.join(EFFORTS)}, "
                         f"got {params['reasoning_effort']!r}")


def check_family(family: Family, prices: dict[str, Price]) -> None:
    """Raise ValueError unless `family` names a model string priced in `prices` within the
    ceiling and sets only the parameters `check_params` allows."""
    if not isinstance(family.model, str) or not family.model.strip():
        raise ValueError(f"family {family.name!r} has no model")
    check_params(family.name, family.params)
    if family.model not in prices:
        raise ValueError(f"family {family.name!r}: {family.model} has no price in the prices file")
    check_price(family.name, family.model, prices[family.model])


def _family(name: str, spec: object, prices: dict[str, Price]) -> Family:
    if not isinstance(spec, dict):
        raise ValueError(f"family {name!r} is not a mapping with a model and params")
    params = spec.get("params")
    family = Family(name, spec.get("model"), {str(k): v for k, v in params.items()} if isinstance(params, dict) else params)  # type: ignore[arg-type]
    check_family(family, prices)
    return family


def load(path: str, prices_path: str) -> dict[str, Family]:
    """The families whose `left_out` is false, in file order. Raises ValueError (one plain
    line) for a file that is not YAML or not a mapping, a family without a model, params
    other than those `check_params` allows, a model the prices file does not price, a price
    above the ceiling, or fewer than two families left in."""
    raw = _read(path)
    prices = load_prices(prices_path)
    loaded = {}
    for name, spec in raw.items():
        if _left_out(spec):
            continue
        loaded[str(name)] = _family(str(name), spec, prices)
    if len(loaded) < 2:
        raise ValueError(f"{path} leaves fewer than two families in")
    return loaded


def input_tokens(messages: list[dict]) -> int:
    """Characters of the messages' contents divided by three, rounded up."""
    return math.ceil(sum(len(str(m.get("content") or "")) for m in messages) / 3)


# What is billed for a call runs above the listed price times the tokens the reply reports:
# by 0% to 25% across the four families in the first live week (2026-11-23), since a provider
# may count tokens its own way or route to a dearer host. A worst case allows half as much again.
BILLED_MARGIN = 1.5


def worst_case(family: Family, price: Price, messages: list[dict]) -> float:
    """Dollars one attempt at a call with `messages` costs at most, if the provider keeps to
    `max_tokens`: `input_tokens(messages)` plus 4 per message in, and the family's
    `max_tokens` out, at `price`, times `BILLED_MARGIN`."""
    tokens_in = input_tokens(messages) + TOKENS_PER_MESSAGE * len(messages)
    tokens_out = family.params["max_tokens"]
    return BILLED_MARGIN * (tokens_in * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1_000_000


SEAT = "seat"


def load_seat(path: str, prices_path: str) -> Family:
    """The one family under the key `seat` in the file, checked as `load` checks a family
    (priced within the ceiling, only the parameters `check_params` allows). Raises ValueError
    for a file with no `seat`, or with anything else beside it."""
    raw = _read(path)
    if set(raw) != {SEAT}:
        raise ValueError(f"{path} must hold one family under the key {SEAT!r} and nothing else")
    return _family(SEAT, raw[SEAT], load_prices(prices_path))


def _json(value: object) -> str:
    import json
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def seat_input_tokens(messages: list[dict], tools: list[dict]) -> int:
    """What a call with tools sends, in tokens counted as characters over three, rounded up:
    each message's content, everything else on it (its tool calls, the id it answers, any
    reasoning sent back) as JSON, and the tools as JSON; plus 4 a message."""
    chars = len(_json(tools))
    for m in messages:
        chars += len(str(m.get("content") or ""))
        rest = {k: v for k, v in m.items() if k not in ("role", "content")}
        if rest:
            chars += len(_json(rest))
    return math.ceil(chars / 3) + TOKENS_PER_MESSAGE * len(messages)


def seat_worst_case(family: Family, price: Price, messages: list[dict], tools: list[dict]) -> float:
    """Dollars one attempt at a call with tools costs at most: `seat_input_tokens` in and the
    family's `max_tokens` out, at `price`, times `BILLED_MARGIN`."""
    tokens_in = seat_input_tokens(messages, tools)
    tokens_out = family.params["max_tokens"]
    return BILLED_MARGIN * (tokens_in * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1_000_000
