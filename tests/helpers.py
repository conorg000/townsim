"""Helpers shared by test modules."""

import random
import re

from townsim.config import FORBIDDEN_WORDS, TownConfig, build_world
from townsim.decide import Choice, Decider, Decision, ModelUnavailable, RuleDecider, ScriptedDecider
from townsim.events import EventLog
from townsim.sim import Simulation, make_deciders

# The shared forbidden words, plus coaching phrases no prompt may use.
FORBIDDEN_PROMPT_WORDS = re.compile(rf"{FORBIDDEN_WORDS.pattern}|you should|ought to", re.IGNORECASE)


def scripted(mapping: dict[str, str]):
    """A decide function that picks `mapping[kind]`, or the default for kinds not in it."""
    return ScriptedDecider(mapping).decide


def assert_prompt_clean(decisions: list[Decision]) -> None:
    """Fail if any situation or option label uses forbidden wording, or if nothing was offered."""
    assert decisions, "no decisions were offered"
    for d in decisions:
        for text in [d.situation, *(o.label for o in d.options)]:
            match = FORBIDDEN_PROMPT_WORDS.search(text)
            assert match is None, f"{d.id}: {match.group()!r} in {text!r}"


def rule_deciders(cfg: TownConfig, seed: int = 0, people=None) -> dict[str, Decider]:
    """The config's deciders, with an rng of their own so they never shift the engine's draws.

    Model deciders, if configured, read from `people`.
    """
    return make_deciders(cfg, random.Random(f"{seed}:deciders"), people)


def make_sim(cfg: TownConfig, db_path, seed: int, deciders=None, run_id: str = "r") -> Simulation:
    """A new run `run_id` of the config's starting world, logged to `db_path`."""
    log = EventLog(str(db_path))
    log.create_run(run_id, seed, cfg.model_dump_json())
    world = build_world(cfg)
    if deciders is None:
        deciders = rule_deciders(cfg, seed, world.people)
    return Simulation(world, deciders, log, run_id, seed)


def run_rule_sim(cfg: TownConfig, db_path, seed: int, steps: int, run_id: str = "r") -> Simulation:
    sim = make_sim(cfg, db_path, seed, run_id=run_id)
    sim.run(steps)
    return sim


def delete_first_decision_at_step(log: EventLog, run_id: str, step: int) -> None:
    """Tamper with the log: remove the first recorded decision of `step` in `run_id`."""
    cursor = log._conn.execute(
        "DELETE FROM events WHERE rowid = (SELECT rowid FROM events"
        " WHERE run_id = ? AND step = ? AND kind = 'decision' ORDER BY seq LIMIT 1)",
        (run_id, step),
    )
    assert cursor.rowcount == 1, f"no decision recorded at step {step} of run {run_id!r}"


def hashes(sim: Simulation) -> list[str]:
    """The state hash logged at each step of `sim`'s run, in step order."""
    return [e.payload["hash"] for _, e in sim.log.events(sim.run_id, kind="state_hash")]


class FailsAtStep:
    """A rule decider until a decision comes at step `n`, then raises `ModelUnavailable`."""

    def __init__(self, n: int, seed: int = 0):
        self.n = n
        self.inner = RuleDecider(random.Random(f"{seed}:deciders"))

    def decide(self, decision: Decision) -> Choice:
        step = int(decision.id.split(":", 1)[0])
        if step == self.n:
            raise ModelUnavailable(f"no answer at step {step}")
        return self.inner.decide(decision)
