"""Compare deciders on the same canned decisions, and the System One decider.

A probe is one decision put to a decider `n` times. Two probes sharing a `pair_id`
describe the same choice under low and high pressure; the high-pressure probe names, in
`expect_more`, the options whose combined share should be higher there than in the low one.

A System One model returns a probability for each offered option instead of text.
`SystemOneDecider` samples one option from those probabilities with its own rng. The
HTTP client for TypeSafe AI's API lives in `townsim.typesafe` (its API shape comes from
summaries of the vendor's docs and is unverified; see that module) and is loaded only
when first needed.
"""

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from townsim.config import ConfigError
from townsim.decide import Choice, Decider, Decision, ModelUnavailable, Option
from townsim.model import render_prompt, token_count
from townsim.world import Person


# Probes.


@dataclass(frozen=True)
class Probe:
    """`expect_more` lists the options of the pressured choice; a single string is one option."""

    decision: Decision
    pair_id: str | None
    expect_more: list[str] | None
    person: Person | None = None

    def __post_init__(self):
        if isinstance(self.expect_more, str):
            object.__setattr__(self, "expect_more", [self.expect_more])
        elif self.expect_more is not None:
            object.__setattr__(self, "expect_more", list(self.expect_more))


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
        if pr.expect_more is not None:
            if not pr.expect_more:
                raise ConfigError(f"{pr.decision.id}: expect_more names no option")
            missing = [o for o in pr.expect_more if o not in offered]
            if missing:
                raise ConfigError(f"{pr.decision.id}: expect_more {missing!r} is not offered")
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

    Per probe: `shares` (each offered option's share of the calls that were answered,
    i.e. did not raise; empty if none was), `invalid` (answers naming an option not
    offered, which take no share), `failures` (calls that raised) and their rates over
    the `n` calls, mean latency in seconds over answered calls, and summed `cost_usd`
    (None if no call was priced). Per decider: the same totals, `directional_passes`
    out of `directional_checks`, and `max_option_share` (the highest share any single
    option takes on any probe). A pair passes when the summed share of its
    `expect_more` options is strictly higher in the high-pressure probe; a pair with
    empty shares on either probe fails. A call that raises is counted as a failure for
    that decider and the comparison goes on.
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
        hi, lo = per_probe[high.decision.id]["shares"], per_probe[low.decision.id]["shares"]
        if hi and lo and sum(hi[o] for o in high.expect_more) > sum(
            lo[o] for o in high.expect_more
        ):
            passes += 1

    return {
        "probes": per_probe,
        "calls": calls,
        "invalid": sum(r["invalid"] for r in results),
        "failures": calls - answered,
        "invalid_rate": sum(r["invalid"] for r in results) / calls if calls else 0.0,
        "failure_rate": (calls - answered) / calls if calls else 0.0,
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
    invalid = truncated = failures = priced = prompt_tokens = completion_tokens = 0
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
            # A reply cut off at the token cap is not a choice; count it apart so a model
            # that ran out of room while deliberating is not mistaken for one that refused.
            truncated += choice.meta.get("finish_reason") == "length"
        c = choice.meta.get("cost_usd")
        if c is not None:
            priced += 1
            cost += c
        prompt_tokens += choice.meta.get("prompt_tokens") or 0
        completion_tokens += choice.meta.get("completion_tokens") or 0
    answered = n - failures
    chose = sum(counts.values())
    return {
        "kind": d.kind,
        "pair_id": probe.pair_id,
        "expect_more": probe.expect_more,
        # Shares are of the replies that named an offered option, so they sum to 1.
        "shares": {oid: k / chose for oid, k in counts.items()} if chose else {},
        "chose": chose,
        "invalid": invalid,
        "truncated": truncated,
        "failures": failures,
        "invalid_rate": invalid / n,
        "failure_rate": failures / n,
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

    Give either `client` or `client_factory`; the factory is called on the first
    decision, so a client that cannot be built (say, a missing key) only fails when this
    decider is actually asked to decide, with the factory's `ConfigError`.
    """

    def __init__(
        self,
        model: str,
        people: dict[str, Person],
        rng: random.Random,
        client: ProbabilityClient | None = None,
        client_factory: Callable[[], ProbabilityClient] | None = None,
    ):
        if client is None and client_factory is None:
            raise ConfigError(f"system one model {model!r}: no client is configured")
        self.model = model
        self.people = people
        self.rng = rng
        self.client = client
        self.client_factory = client_factory

    def decide(self, decision: Decision) -> Choice:
        if self.client is None:
            self.client = self.client_factory()
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
        usage = getattr(self.client, "last_usage", None)
        if not isinstance(usage, dict):
            usage = {}
        meta = {
            "probs": probs,
            "prompt_tokens": token_count(usage, "input_tokens"),
            "completion_tokens": token_count(usage, "output_tokens"),
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
