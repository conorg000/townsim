"""Branching runs from a saved moment, and proving a run reproduces exactly from its log.

Snapshot keys follow `EventLog.save_snapshot`: the snapshot under key N is the world
that step N + 1 starts from, and key -1 is the starting world. So the world as it
stands at the start of step K is the snapshot under key K - 1.
"""

import json
import random

from townsim.config import ConfigError, TownConfig, build_world
from townsim.decide import Choice, Decider, Decision, ReplayDecider
from townsim.events import Event, EventLog, state_hash
from townsim.sim import Simulation
from townsim.world import World


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
    """Answers from the recorded choices and texts, noting which were used in the current step."""

    def __init__(self, recorded: dict[str, str], texts: dict[str, tuple[str | None, dict]]):
        self.inner = ReplayDecider(recorded)
        self.texts = texts
        self.step = 0
        self.used: set[str] = set()
        self.used_texts: set[str] = set()

    def write(self, key: str, person_id: str, messages: list[dict]) -> tuple[str | None, dict]:
        if key not in self.texts:
            raise ReplayMismatch(self.step, f"text {key} was not recorded")
        self.used_texts.add(key)
        text, meta = self.texts[key]
        return text, dict(meta)

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
    """Rebuild `run_id` from its starting snapshot using only its recorded choices and texts.

    Runs in a temporary in-memory log, leaving `log` untouched. Every step from the
    starting snapshot to the latest snapshot is rebuilt and checked against the log:
    the decisions asked for and chosen, every other event, the state hash, and the
    snapshot saved after the step. `ReplayMismatch` names the first step that differs.
    Events of a step that was aborted and rolled back (`run_aborted`) are skipped.

    First the starting snapshot is tied to the run row (see `_check_start`): a root
    run's to `build_world` of its stored config, a fork's to its parent's snapshot,
    and either's rng to `Random(seed)`.
    """
    run = log.get_run(run_id)
    start_key = -1 if run["parent_step"] is None else run["parent_step"] - 1
    try:
        world, rng_state = log.load_snapshot(run_id, start_key)
    except ValueError:
        raise ReplayMismatch(start_key + 1, "the run has no starting snapshot") from None
    _check_start(log, run, start_key, world, rng_state)
    last_step = log.latest_snapshot_step(run_id)
    steps = range(start_key + 1, last_step + 1)
    if not steps:
        raise ReplayMismatch(start_key + 1, "the run has no logged steps to replay")

    logged: dict[int, list[Event]] = {}
    for step, event in log.events(run_id):
        if event.kind != "run_aborted":
            logged.setdefault(step, []).append(event)
    outside = sorted(set(logged) - set(steps))
    if outside:
        raise ReplayMismatch(
            outside[0], f"events logged outside the run's steps {steps.start}..{steps.stop - 1}"
        )

    recorded_by_step = {
        step: {
            e.payload["decision_id"]: e.payload["option_id"]
            for e in events
            if e.kind == "decision"
        }
        for step, events in logged.items()
    }
    recorded = {d: o for by_id in recorded_by_step.values() for d, o in by_id.items()}
    texts_by_step = {
        step: {
            e.payload["key"]: (e.payload["text"], e.payload.get("meta") or {})
            for e in events
            if e.kind == "text"
        }
        for step, events in logged.items()
    }
    texts = {k: t for by_key in texts_by_step.values() for k, t in by_key.items()}

    checker = _CheckedReplay(recorded, texts)
    deciders: dict[str, Decider] = {p.decider: checker for p in world.people.values()}
    deciders.update(
        {p.assistant.decider: checker for p in world.people.values() if p.assistant is not None}
    )
    if world.provider is not None:  # owners may sign up during the run
        deciders[world.provider.decider] = checker
    scratch = EventLog(":memory:")
    scratch.save_snapshot(run_id, start_key, world, rng_state)
    sim = Simulation(world, deciders, scratch, run_id, run["seed"])
    sim.rng.setstate(rng_state)

    for step in steps:
        if sim.world.step != step:
            raise ReplayMismatch(step, f"replay reached step {sim.world.step} instead")
        checker.step, checker.used, checker.used_texts = step, set(), set()
        sim.step()
        unused = sorted(set(recorded_by_step.get(step, {})) - checker.used)
        if unused:
            raise ReplayMismatch(step, f"recorded decision {unused[0]} was not used")
        unused = sorted(set(texts_by_step.get(step, {})) - checker.used_texts)
        if unused:
            raise ReplayMismatch(step, f"recorded text {unused[0]} was not used")
        expected = logged.get(step, [])
        replayed = [e for _, e in scratch.events(run_id, step=step)]
        _check_hash(step, expected, replayed)
        _check_events(step, expected, replayed)
        _check_snapshot(step, log, scratch, run_id)


def _check_start(log: EventLog, run: dict, start_key: int, world: World, rng_state) -> None:
    """The starting snapshot must be the one the run row describes.

    A root run starts from `build_world` of its stored config; a fork starts from its
    parent's snapshot at `parent_step - 1`. Either way the engine rng starts as
    `Random(seed)`.
    """
    step = start_key + 1
    if run["parent_step"] is None:
        try:
            cfg = TownConfig.model_validate(json.loads(run["config_json"]))
        except (ConfigError, ValueError, TypeError) as exc:
            raise ReplayMismatch(step, f"the run's stored config cannot be read: {exc}") from None
        if state_hash(world) != state_hash(build_world(cfg)):
            raise ReplayMismatch(
                step, "the starting snapshot is not the world built from the run's config"
            )
    else:
        try:
            parent_world, _ = log.load_snapshot(run["parent_run_id"], start_key)
        except ValueError:
            raise ReplayMismatch(
                step, f"the parent run has no snapshot at step {start_key} to fork from"
            ) from None
        if state_hash(world) != state_hash(parent_world):
            raise ReplayMismatch(
                step, f"the fork's starting snapshot differs from the parent's at step {start_key}"
            )
    if rng_state != random.Random(run["seed"]).getstate():
        raise ReplayMismatch(step, "the starting rng state is not Random(seed) for the run's seed")


def _check_hash(step: int, expected: list[Event], got: list[Event]) -> None:
    logged_hashes = [e.payload.get("hash") for e in expected if e.kind == "state_hash"]
    if len(logged_hashes) != 1:
        raise ReplayMismatch(step, f"{len(logged_hashes)} state hashes logged, expected 1")
    (replayed_hash,) = [e.payload["hash"] for e in got if e.kind == "state_hash"]
    if logged_hashes[0] != replayed_hash:
        raise ReplayMismatch(step, "state hashes differ")


def _comparable(event: Event) -> dict:
    """The event as replay must reproduce it; a decision's or text's `meta` is the decider's own."""
    data = event.model_dump(mode="json")
    if event.kind in ("decision", "text"):
        data["payload"] = {k: v for k, v in data["payload"].items() if k != "meta"}
    return data


def _check_events(step: int, expected: list[Event], got: list[Event]) -> None:
    """Compare a step's events, leaving out `invalid_response`, which replay never produces."""
    want = [_comparable(e) for e in expected if e.kind != "invalid_response"]
    have = [_comparable(e) for e in got if e.kind != "invalid_response"]
    for i, (w, h) in enumerate(zip(want, have)):
        if w != h:
            raise ReplayMismatch(
                step, f"event {i} differs: logged {w['kind']}, replayed {h['kind']}"
            )
    if len(want) != len(have):
        raise ReplayMismatch(step, f"{len(want)} events logged, {len(have)} replayed")


def _check_snapshot(step: int, log: EventLog, scratch: EventLog, run_id: str) -> None:
    try:
        logged_world, logged_rng = log.load_snapshot(run_id, step)
    except ValueError:
        raise ReplayMismatch(step, "no snapshot logged after this step") from None
    replayed_world, replayed_rng = scratch.load_snapshot(run_id, step)
    if state_hash(logged_world) != state_hash(replayed_world):
        raise ReplayMismatch(step, "logged snapshot world differs from the replayed world")
    if logged_rng != replayed_rng:
        raise ReplayMismatch(step, "logged snapshot rng state differs from the replayed one")
