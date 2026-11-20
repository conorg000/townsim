"""Estimate what a run would cost with different models, without calling any model.

The town is run with the rule deciders, and every decision's prompt is rendered exactly
as a model decider would send it. Tokens are counted offline, so the same config, steps
and seed always give the same figures. It is an estimate, for three reasons:

- token counts use one general-purpose tokenizer, and each provider's own differs a little;
- a town played by models makes different choices from the rule deciders, and dishonest
  choices bring a few extra decisions (tips, responses to audits, publishing);
- retries after failed calls are not counted.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import yaml
from pydantic import BaseModel, Field

from townsim.config import ConfigError, TownConfig, build_world
from townsim.decide import Choice, Decision, RuleDecider
from townsim.events import EventLog
from townsim.model import render_prompt
from townsim.sim import Simulation
from townsim.world import World

TOKENIZER = "cl100k_base"
TOKENS_PER_MESSAGE = 4  # framing a chat API adds around each message
TOKENS_PER_REPLY_PRIMING = 3
DEFAULT_REPLY_TOKENS = 12  # the reply is one short JSON line: {"choice": "<id>"}


class Price(BaseModel):
    """Dollars per million tokens, as the provider lists them."""

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    # Hidden reasoning tokens a thinking model spends per call, billed as output.
    reasoning_tokens_per_call: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class Usage:
    calls: int = 0
    input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.calls + other.calls, self.input_tokens + other.input_tokens)


def load_prices(path: str) -> dict[str, Price]:
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read prices file {path}: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"prices file {path} lists no models")
    try:
        return {str(name): Price.model_validate(spec) for name, spec in raw.items()}
    except ValueError as exc:
        raise ConfigError(f"bad prices file {path}: {exc}") from exc


def count_tokens(messages: list[dict]) -> int:
    import tiktoken  # imported here so the engine does not load it

    enc = tiktoken.get_encoding(TOKENIZER)
    body = sum(len(enc.encode(m["content"])) + TOKENS_PER_MESSAGE for m in messages)
    return body + TOKENS_PER_REPLY_PRIMING


class _CountingDecider:
    """Decides by rule, and counts the prompt a model would have been sent."""

    def __init__(self, world: World, rng: random.Random):
        self._world = world
        self._rule = RuleDecider(rng)
        self.by_role: dict[str, Usage] = {}

    def decide(self, decision: Decision) -> Choice:
        person = self._world.people[decision.actor_id]
        tokens = count_tokens(render_prompt(person, decision))
        self.by_role[person.role] = self.by_role.get(person.role, Usage()) + Usage(1, tokens)
        return self._rule.decide(decision)


def count_usage(cfg: TownConfig, steps: int, seed: int) -> dict[str, Usage]:
    """Calls and input tokens per role for one run of `steps` weeks, if every person
    were played by a model."""
    world = build_world(cfg)
    counter = _CountingDecider(world, random.Random(f"{seed}:deciders"))
    log = EventLog(":memory:")
    log.create_run("estimate", seed, cfg.model_dump_json())
    names = set(cfg.deciders) | {p.decider for p in world.people.values()}
    Simulation(world, {name: counter for name in names}, log, "estimate", seed).run(steps)
    return dict(sorted(counter.by_role.items()))


def cost(usage: Usage, price: Price, reply_tokens: int = DEFAULT_REPLY_TOKENS) -> dict:
    output_tokens = usage.calls * (reply_tokens + price.reasoning_tokens_per_call)
    dollars = (
        usage.input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok
    ) / 1_000_000
    return {
        "calls": usage.calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": dollars,
    }


def estimate(
    by_role: dict[str, Usage],
    prices: dict[str, Price],
    runs: int = 1,
    reply_tokens: int = DEFAULT_REPLY_TOKENS,
    mix: dict[str, str] | None = None,
) -> dict:
    """Cost of `runs` runs with each priced model playing everyone, and, when `mix` maps
    roles to models (with "default" for the rest), the cost of that mix."""
    total = sum(by_role.values(), Usage())
    scaled = Usage(total.calls * runs, total.input_tokens * runs)
    result: dict = {
        "runs": runs,
        "per_run": {role: {"calls": u.calls, "input_tokens": u.input_tokens} for role, u in by_role.items()},
        "models": {name: cost(scaled, price, reply_tokens) for name, price in prices.items()},
    }
    if mix:
        unknown = sorted(set(mix.values()) - set(prices))
        if unknown:
            raise ConfigError(f"no price listed for: {', '.join(unknown)}")
        strangers = sorted(set(mix) - set(by_role) - {"default"})
        if strangers:
            raise ConfigError(
                f"no such role: {', '.join(strangers)}; roles that decide are: {', '.join(by_role)}"
            )
        if "default" not in mix and set(by_role) - set(mix):
            raise ConfigError("the mix leaves some roles without a model; add default=MODEL")
        rows = {}
        for role, usage in by_role.items():
            model = mix.get(role, mix.get("default"))
            row = cost(Usage(usage.calls * runs, usage.input_tokens * runs), prices[model], reply_tokens)
            rows[role] = {"model": model, **row}
        result["mix"] = {
            "roles": rows,
            "cost_usd": sum(r["cost_usd"] for r in rows.values()),
        }
    return result


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _fetch_json(url: str) -> dict:
    import json
    import ssl
    import urllib.request

    import certifi  # this Python may have no system certificates of its own

    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=30, context=context) as response:  # public list, no key
        return json.load(response)


def openrouter_prices(model_ids: list[str], fetch=_fetch_json) -> dict[str, Price]:
    """Current list prices for `model_ids` from OpenRouter's public model list."""
    try:
        listed = {m["id"]: m.get("pricing", {}) for m in fetch(OPENROUTER_MODELS_URL)["data"]}
    except Exception as exc:
        raise ConfigError(f"could not read OpenRouter's model list: {exc}") from exc
    missing = [i for i in model_ids if i not in listed]
    if missing:
        raise ConfigError(f"OpenRouter lists no model called: {', '.join(missing)}")
    try:
        return {
            i: Price(
                input_per_mtok=float(listed[i]["prompt"]) * 1_000_000,
                output_per_mtok=float(listed[i]["completion"]) * 1_000_000,
            )
            for i in model_ids
        }
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"OpenRouter's price for a model could not be read: {exc}") from exc
