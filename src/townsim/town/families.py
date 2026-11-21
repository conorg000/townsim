"""The model families that play a generated place, as `config/families.yaml` lists them.

`names` gives only the families' names (all the generator needs); `load` gives each family's
model and call parameters, and refuses any family whose model is not priced in the prices
file at no more than `MAX_IN` dollars per million tokens in and `MAX_OUT` out. Nothing lifts
that ceiling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import yaml

from townsim.estimate import Price, load_prices

MAX_IN = 0.20    # dollars per million input tokens a family's model may cost at most
MAX_OUT = 1.00   # dollars per million output tokens
MAX_TOKENS = 4000
EFFORTS = ("none", "low")


@dataclass(frozen=True)
class Family:
    """A family's name, its model (the OpenRouter id as the file gives it) and the
    parameters passed to every call as given."""

    name: str
    model: str
    params: dict


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
    """Raise ValueError if `price` is above the ceiling."""
    if not price.input_per_mtok <= MAX_IN or not price.output_per_mtok <= MAX_OUT:
        raise ValueError(f"family {name!r}: {model} costs ${price.input_per_mtok} per million tokens in and "
                         f"${price.output_per_mtok} out, above the ceiling of ${MAX_IN} in and ${MAX_OUT} out")


def _family(name: str, spec: object, prices: dict[str, Price]) -> Family:
    if not isinstance(spec, dict):
        raise ValueError(f"family {name!r} is not a mapping with a model and params")
    model = spec.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"family {name!r} has no model")
    params = spec.get("params")
    if not isinstance(params, dict):
        raise ValueError(f"family {name!r}: params must be a mapping")
    params = {str(k): v for k, v in params.items()}
    mt = params.get("max_tokens")
    if isinstance(mt, bool) or not isinstance(mt, int) or not 1 <= mt <= MAX_TOKENS:
        raise ValueError(f"family {name!r}: params must have a whole max_tokens from 1 to {MAX_TOKENS}, got {mt!r}")
    if "reasoning_effort" in params and params["reasoning_effort"] not in EFFORTS:
        raise ValueError(f"family {name!r}: reasoning_effort must be one of {', '.join(EFFORTS)}, "
                         f"got {params['reasoning_effort']!r}")
    if model not in prices:
        raise ValueError(f"family {name!r}: {model} has no price in the prices file")
    check_price(name, model, prices[model])
    return Family(name, model, params)


def load(path: str, prices_path: str) -> dict[str, Family]:
    """The families whose `left_out` is false, in file order. Raises ValueError (one plain
    line) for a file that is not YAML or not a mapping, a family without a model, params
    without a whole `max_tokens` from 1 to 4000 (or with a `reasoning_effort` other than
    none or low), a model the prices file does not price, a price above the ceiling, or
    fewer than two families left in."""
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


def worst_case(family: Family, price: Price, messages: list[dict]) -> float:
    """Dollars a call with `messages` could cost at most: `input_tokens(messages)` in and the
    family's `max_tokens` out, at `price`."""
    tokens_out = family.params["max_tokens"]
    return (input_tokens(messages) * price.input_per_mtok + tokens_out * price.output_per_mtok) / 1_000_000
