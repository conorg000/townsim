"""Compare deciders on the same canned decisions, and the System One decider.

A probe is one decision put to a decider `n` times. Two probes sharing a `pair_id`
describe the same choice under low and high pressure; the high-pressure probe names, in
`expect_more`, the option whose share should be higher there than in the low one.

A System One model returns a probability for each offered option instead of text.
`SystemOneDecider` samples one option from those probabilities with its own rng.
"""

import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from townsim.config import ConfigError
from townsim.decide import Choice, Decider, Decision, ModelUnavailable, Option
from townsim.model import render_prompt
from townsim.world import Person


# Probes.


@dataclass(frozen=True)
class Probe:
    decision: Decision
    pair_id: str | None
    expect_more: str | None
    person: Person | None = None


def load_probes(path: str) -> list[Probe]:
    """Read and check the probes in `path`; see `config/probe_decisions.yaml`."""
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"malformed YAML in {path}: {e}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("probes"), list):
        raise ConfigError(f"{path}: expected a mapping with a list under 'probes'")
    probes = []
    for i, entry in enumerate(raw["probes"]):
        try:
            probes.append(_probe(entry))
        except (KeyError, TypeError, ValueError) as e:
            raise ConfigError(f"{path}: probe {i}: {e}") from e
    check_probes(probes)
    return probes


def _probe(entry: dict) -> Probe:
    pid = entry["id"]
    p = entry["person"]
    person = Person(
        id=f"{pid}:person",
        name=p["name"],
        persona=p["persona"],
        role=p["role"],
        cash=p["cash"],
        employer_id=None,
        ambition=0.5,
        scruples=0.5,
        decider="probe",
    )
    d = entry["decision"]
    decision = Decision(
        id=pid,
        actor_id=person.id,
        kind=d["kind"],
        situation=d["situation"],
        options=[Option(id=o["id"], label=o["label"]) for o in d["options"]],
        default_option_id=d["default_option_id"],
    )
    return Probe(decision, entry.get("pair"), entry.get("expect_more"), person)


def check_probes(probes: list[Probe]) -> None:
    """Raise `ConfigError` unless ids are unique and every pair is a well-formed low/high pair."""
    ids = [p.decision.id for p in probes]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"duplicate probe ids: {ids}")
    for pr in probes:
        offered = [o.id for o in pr.decision.options]
        if pr.decision.default_option_id not in offered:
            raise ConfigError(f"{pr.decision.id}: default option is not offered")
        if pr.expect_more is not None and pr.expect_more not in offered:
            raise ConfigError(f"{pr.decision.id}: expect_more {pr.expect_more!r} is not offered")
    for pair_id, pair in _pairs(probes).items():
        if len(pair) != 2:
            raise ConfigError(f"pair {pair_id!r} has {len(pair)} probes, not 2")
        if sum(p.expect_more is not None for p in pair) != 1:
            raise ConfigError(
                f"pair {pair_id!r}: exactly one probe, the high-pressure one, names expect_more"
            )
        a, b = pair
        if a.decision.kind != b.decision.kind or [o.id for o in a.decision.options] != [
            o.id for o in b.decision.options
        ]:
            raise ConfigError(f"pair {pair_id!r}: both probes must offer the same choice")


def _pairs(probes: list[Probe]) -> dict[str, list[Probe]]:
    pairs: dict[str, list[Probe]] = {}
    for p in probes:
        if p.pair_id is not None:
            pairs.setdefault(p.pair_id, []).append(p)
    return pairs


def probe_people(probes: list[Probe]) -> dict[str, Person]:
    """The person making each probe's decision, keyed by the decision's `actor_id`."""
    return {p.decision.actor_id: p.person for p in probes if p.person is not None}


# Comparison.


def compare_deciders(
    deciders: dict[str, Decider],
    probes: list[Probe],
    n: int,
    clock: Callable[[], float] = time.perf_counter,
) -> dict:
    """Put each probe to each decider `n` times and summarise what each one chose.

    Per probe: `shares` (each offered option's share of the `n` calls), `invalid` (calls
    answered with an option not offered), `failures` (calls that raised), mean latency
    in seconds over answered calls, and summed `cost_usd` (None if no call was priced).
    Per decider: the same totals, `directional_passes` (pairs whose `expect_more` share
    is strictly higher in the high-pressure probe) out of `directional_checks`, and
    `max_option_share` (the highest share any single option takes on any probe).
    A decider that raises on one call has that call counted as a failure; the rest go on.
    """
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}")
    return {name: _compare_one(d, probes, n, clock) for name, d in deciders.items()}


def _compare_one(decider: Decider, probes: list[Probe], n: int, clock) -> dict:
    per_probe = {p.decision.id: _run_probe(decider, p, n, clock) for p in probes}
    results = list(per_probe.values())
    calls = n * len(probes)
    answered = calls - sum(r["failures"] for r in results)
    latency = sum(r["_latency_total"] for r in results)
    priced = sum(r["priced_calls"] for r in results)
    tokens = [r["_tokens"] for r in results]
    for r in results:
        del r["_latency_total"], r["_tokens"]

    pairs = _pairs(probes)
    passes = 0
    for pair in pairs.values():
        high = next(p for p in pair if p.expect_more is not None)
        low = next(p for p in pair if p is not high)
        option = high.expect_more
        if (
            per_probe[high.decision.id]["shares"][option]
            > per_probe[low.decision.id]["shares"][option]
        ):
            passes += 1

    return {
        "probes": per_probe,
        "calls": calls,
        "invalid": sum(r["invalid"] for r in results),
        "failures": calls - answered,
        "mean_latency_s": latency / answered if answered else None,
        "total_cost_usd": (
            sum(r["cost_usd"] for r in results if r["cost_usd"] is not None) if priced else None
        ),
        "priced_calls": priced,
        "prompt_tokens": sum(t[0] for t in tokens),
        "completion_tokens": sum(t[1] for t in tokens),
        "directional_passes": passes,
        "directional_checks": len(pairs),
        "max_option_share": max(
            (max(r["shares"].values(), default=0.0) for r in results), default=0.0
        ),
    }


def _run_probe(decider: Decider, probe: Probe, n: int, clock) -> dict:
    d = probe.decision
    counts = {o.id: 0 for o in d.options}
    invalid = failures = priced = prompt_tokens = completion_tokens = 0
    latency = cost = 0.0
    errors: list[str] = []
    for _ in range(n):
        start = clock()
        try:
            choice = decider.decide(d)
        except Exception as exc:
            failures += 1
            if len(errors) < 3:
                errors.append(f"{type(exc).__name__}: {exc}")
            continue
        latency += clock() - start
        if choice.option_id in counts:
            counts[choice.option_id] += 1
        else:
            invalid += 1
        c = choice.meta.get("cost_usd")
        if c is not None:
            priced += 1
            cost += c
        prompt_tokens += choice.meta.get("prompt_tokens") or 0
        completion_tokens += choice.meta.get("completion_tokens") or 0
    answered = n - failures
    return {
        "kind": d.kind,
        "pair_id": probe.pair_id,
        "expect_more": probe.expect_more,
        "shares": {oid: k / n for oid, k in counts.items()},
        "invalid": invalid,
        "failures": failures,
        "errors": errors,
        "mean_latency_s": latency / answered if answered else None,
        "cost_usd": cost if priced else None,
        "priced_calls": priced,
        "_latency_total": latency,
        "_tokens": (prompt_tokens, completion_tokens),
    }


# The System One decider.


class ProbabilityClient(Protocol):
    def probabilities(
        self, persona: str, situation: str, options: list[tuple[str, str]]
    ) -> dict[str, float]: ...


class SystemOneDecider:
    """Asks a System One model for a probability per offered option and samples one with `rng`.

    The persona sent is the same description of the person a chat model gets. The
    returned probabilities are normalised over the offered options (missing ones count as
    0, unknown ids are dropped) and stored in `Choice.meta["probs"]`. If the client raises
    or gives nothing usable, `ModelUnavailable` is raised.
    """

    def __init__(
        self,
        model: str,
        people: dict[str, Person],
        rng: random.Random,
        client: ProbabilityClient | None = None,
    ):
        if client is None:
            raise ConfigError(f"system one model {model!r}: no client is configured")
        self.model = model
        self.people = people
        self.rng = rng
        self.client = client

    def decide(self, decision: Decision) -> Choice:
        persona = render_prompt(self.people[decision.actor_id], decision)[0]["content"]
        options = [(o.id, o.label) for o in decision.options]
        try:
            raw = self.client.probabilities(persona, decision.situation, options)
        except Exception as exc:
            raise ModelUnavailable(f"{self.model}: {exc}") from exc
        probs = _normalise(raw, [oid for oid, _ in options])
        if probs is None:
            raise ModelUnavailable(f"{self.model}: no usable probabilities in {raw!r}")
        r = self.rng.random()
        chosen = options[-1][0]
        acc = 0.0
        for oid, _ in options:
            acc += probs[oid]
            if r < acc:
                chosen = oid
                break
        usage = getattr(self.client, "last_usage", None) or {}
        meta = {
            "probs": probs,
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "cost_usd": None,
        }
        return Choice(option_id=chosen, meta=meta)


def _normalise(raw, offered: list[str]) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    probs = {}
    for oid in offered:
        v = raw.get(oid, 0.0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            v = 0.0
        probs[oid] = float(v)
    total = sum(probs.values())
    if total <= 0:
        return None
    return {oid: v / total for oid, v in probs.items()}


# TypeSafe AI's System One API.

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_KEY_ENV = "TYPESAFE_API_KEY"
QUESTION_ID = "choice"
QUESTION = "Which of these do you do?"


def _urllib_post(url: str, headers: dict[str, str], body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


class TypeSafeClient:
    """A `ProbabilityClient` for TypeSafe AI's `POST /v1/systemone` (one `choice` question).

    Written from the public docs at docs.typesafe.ai (API reference and the Choice
    primitive); not yet tried against the live service. The state is the persona and
    the situation; the criteria map each option id to its label. The key is read from
    the `TYPESAFE_API_KEY` environment variable. Token usage of the last call is kept
    in `last_usage`.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        post_fn: Callable[[str, dict[str, str], dict, float], dict] = _urllib_post,
        timeout: float = 30.0,
    ):
        api_key = api_key if api_key is not None else os.environ.get(TYPESAFE_KEY_ENV)
        if not api_key:
            raise ConfigError(
                f"system one model {model!r}: set {TYPESAFE_KEY_ENV} to use TypeSafe AI's API"
            )
        self.model = model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.post_fn = post_fn
        self.timeout = timeout
        self.last_usage: dict | None = None

    def request_body(self, persona: str, situation: str, options: list[tuple[str, str]]) -> dict:
        return {
            "model": self.model,
            "state": f"{persona}\n\n{situation}",
            "questions": {
                QUESTION_ID: {
                    "type": "choice",
                    "instructions": QUESTION,
                    "criteria": {oid: label for oid, label in options},
                }
            },
        }

    def probabilities(
        self, persona: str, situation: str, options: list[tuple[str, str]]
    ) -> dict[str, float]:
        self.last_usage = None
        body = self.request_body(persona, situation, options)
        resp = self.post_fn(TYPESAFE_URL, self._headers, body, self.timeout)
        self.last_usage = resp.get("usage")
        answer = resp["answers"][QUESTION_ID]
        return dict(answer["probabilities"])
