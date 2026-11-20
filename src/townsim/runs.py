"""Branching runs from a saved moment, and proving a run reproduces exactly from its log.

Snapshot keys follow `EventLog.save_snapshot`: the snapshot under key N is the world
that step N + 1 starts from, and key -1 is the starting world. So the world as it
stands at the start of step K is the snapshot under key K - 1.
"""

import random

from townsim.decide import Choice, Decider, Decision, ReplayDecider
from townsim.events import EventLog
from townsim.sim import Simulation


class ReplayMismatch(Exception):
    """A replay of a run did not reproduce what its log records at `step`."""

    def __init__(self, step: int, detail: str):
        super().__init__(f"step {step}: {detail}")
        self.step = step
        self.detail = detail


def fork(
    log: EventLog,
    source_run_id: str,
    at_step: int,
    new_run_id: str,
    seed: int,
    deciders: dict[str, Decider],
) -> Simulation:
    """A new run `new_run_id` starting from the source run's world at the start of `at_step`.

    The fork has its own engine rng, a fresh `Random(seed)`, and saves its starting world
    under its own run id so it can be replayed without the source run's later history.
    """
    world, _ = log.load_snapshot(source_run_id, at_step - 1)
    config_json = log.get_run(source_run_id)["config_json"]
    log.begin()
    try:
        log.create_run(
            new_run_id, seed, config_json, parent_run_id=source_run_id, parent_step=at_step
        )
        log.save_snapshot(new_run_id, at_step - 1, world, random.Random(seed).getstate())
    except BaseException:
        log.rollback()
        raise
    log.commit()
    return Simulation(world, deciders, log, new_run_id, seed)


class _CheckedReplay:
    """Answers from the recorded choices, noting which were used in the current step."""

    def __init__(self, recorded: dict[str, str]):
        self.inner = ReplayDecider(recorded)
        self.step = 0
        self.used: set[str] = set()

    def decide(self, decision: Decision) -> Choice:
        try:
            choice = self.inner.decide(decision)
        except KeyError:
            raise ReplayMismatch(
                self.step, f"decision {decision.id} was not recorded"
            ) from None
        self.used.add(decision.id)
        return choice


def replay(log: EventLog, run_id: str) -> None:
    """Rebuild `run_id` from its starting snapshot using only its recorded choices.

    Runs in a temporary in-memory log, leaving `log` untouched, and raises
    `ReplayMismatch` at the first step whose state hash differs from the logged one,
    that asks for a decision that was not recorded, or that leaves a recorded decision
    unused. Steps that were aborted and rolled back have no state hash and are skipped.
    """
    run = log.get_run(run_id)
    start_key = -1 if run["parent_step"] is None else run["parent_step"] - 1
    world, rng_state = log.load_snapshot(run_id, start_key)

    recorded_by_step: dict[int, dict[str, str]] = {}
    for step, event in log.events(run_id, kind="decision"):
        recorded_by_step.setdefault(step, {})[event.payload["decision_id"]] = event.payload[
            "option_id"
        ]
    recorded = {d: o for by_id in recorded_by_step.values() for d, o in by_id.items()}
    logged_hashes = [(step, e.payload["hash"]) for step, e in log.events(run_id, kind="state_hash")]

    checker = _CheckedReplay(recorded)
    deciders: dict[str, Decider] = {p.decider: checker for p in world.people.values()}
    scratch = EventLog(":memory:")
    scratch.save_snapshot(run_id, start_key, world, rng_state)
    sim = Simulation(world, deciders, scratch, run_id, run["seed"])
    sim.rng.setstate(rng_state)

    for step, expected in logged_hashes:
        if sim.world.step != step:
            raise ReplayMismatch(step, f"replay reached step {sim.world.step} instead")
        checker.step, checker.used = step, set()
        sim.step()
        unused = sorted(set(recorded_by_step.get(step, {})) - checker.used)
        if unused:
            raise ReplayMismatch(step, f"recorded decision {unused[0]} was not used")
        (_, got), = scratch.events(run_id, kind="state_hash", step=step)
        if got.payload["hash"] != expected:
            raise ReplayMismatch(step, "state hashes differ")
