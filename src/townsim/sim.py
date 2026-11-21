"""The weekly loop: run every phase in order, log the step, and snapshot the world.

A step is one SQLite transaction. If a decider raises `ModelUnavailable`, the step's
events are rolled back, the world and rng are put back as they were when the step
began, a `run_aborted` event is committed on its own and the exception propagates.
"""

import functools
import random

from townsim import adoption, assistant, bribery, market, press, tax  # noqa: F401  (bribery: registers its rules)
from townsim.actions import _load, _save
from townsim.compare import SystemOneDecider
from townsim.config import ConfigError, TownConfig
from townsim.decide import Choice, Decider, Decision, ModelUnavailable, RecordingDecider, RuleDecider
from townsim.events import Event, EventLog, state_hash
from townsim.model import ModelDecider
from townsim.world import Person, World

START_STEP = -1  # the snapshot of the starting world, saved before the first step


def make_deciders(
    cfg: TownConfig, rng: random.Random, people: dict[str, Person] | None = None
) -> dict[str, Decider]:
    """One decider per configured name. Rule deciders draw from `rng`, not the engine's rng.

    Model and System One deciders read each person from `people` at decision time, so
    pass the `world.people` of the world being run (for a resumed or forked run, that
    run's `sim.world.people`); `people` is required when any decider has `type: model`
    or `type: system_one`. A System One decider samples from `rng` and calls TypeSafe
    AI's API; its client is built on its first decision, so a missing `TYPESAFE_API_KEY`
    only fails a run that actually asks that decider.
    """
    deciders: dict[str, Decider] = {}
    for name, spec in cfg.deciders.items():
        if spec.type == "rule":
            deciders[name] = RuleDecider(rng)
        elif spec.type == "model":
            if people is None:
                raise ConfigError(f"decider {name!r}: a model decider needs the world's people")
            deciders[name] = ModelDecider(
                spec.model,
                spec.temperature,
                people,
                timeout=spec.timeout,
                reasoning_effort=spec.reasoning_effort,
                max_tokens=spec.max_tokens,
            )
        elif spec.type == "system_one":
            if people is None:
                raise ConfigError(
                    f"decider {name!r}: a system one decider needs the world's people"
                )
            factory = functools.partial(_system_one_client, name, spec.model)
            deciders[name] = SystemOneDecider(spec.model, people, rng, client_factory=factory)
        else:
            raise ConfigError(f"decider {name!r}: type {spec.type!r} is not available yet")
    return deciders


def _system_one_client(name: str, model: str):
    """TypeSafe AI's client, imported here so the engine never loads the HTTP module."""
    from townsim.typesafe import make_client

    try:
        return make_client(model)
    except ConfigError as exc:
        raise ConfigError(f"decider {name!r}: {exc}") from exc


class Simulation:
    """One run of the town, logged to `log` under `run_id`; the engine rng is `Random(seed)`.

    A new run saves the starting world as the snapshot for step -1. `deciders` is read
    afresh at every step, so a resumed or forked run whose deciders need its own world
    can be given `{}` and then `sim.deciders = make_deciders(cfg, rng, sim.world.people)`.
    """

    def __init__(
        self, world: World, deciders: dict[str, Decider], log: EventLog, run_id: str, seed: int
    ):
        self.world = world
        self.deciders = deciders
        self.log = log
        self.run_id = run_id
        self.seed = seed
        self.rng = random.Random(seed)
        if log.latest_snapshot_step(run_id) is None:
            log.save_snapshot(run_id, START_STEP, world, self.rng.getstate())

    @classmethod
    def resume(cls, log: EventLog, run_id: str, deciders: dict[str, Decider]) -> "Simulation":
        """Continue `run_id` from its latest snapshot."""
        step = log.latest_snapshot_step(run_id)
        if step is None:
            raise ValueError(f"no snapshot to resume run {run_id!r} from")
        world, rng_state = log.load_snapshot(run_id, step)
        sim = cls(world, deciders, log, run_id, log.get_run(run_id)["seed"])
        sim.rng.setstate(rng_state)
        return sim

    def run(self, n_steps: int) -> None:
        for _ in range(n_steps):
            self.step()

    def step(self) -> None:
        """Run one week. On any error the world, rng and log are left as the step found them."""
        step = self.world.step
        saved_world = _save(self.world)
        saved_rng = self.rng.getstate()
        self.log.begin()
        try:
            self._run_phases(step)
        except BaseException as exc:
            self.log.rollback()
            _load(saved_world)
            self.rng.setstate(saved_rng)
            if isinstance(exc, ModelUnavailable):
                aborted = Event(kind="run_aborted", payload={"step": step, "error": str(exc)})
                self.log.append(self.run_id, step, [aborted])
            raise
        self.log.commit()

    def _run_phases(self, step: int) -> None:
        world, rng = self.world, self.rng
        events: list[Event] = []
        recorders = {name: RecordingDecider(d, events) for name, d in self.deciders.items()}
        seen: set[str] = set()

        def decide(decision: Decision) -> Choice:
            if decision.id in seen:
                raise ValueError(f"decision id used twice in step {step}: {decision.id!r}")
            seen.add(decision.id)
            return recorders[world.people[decision.actor_id].decider].decide(decision)

        written: set[str] = set()

        def write(key: str, person_id: str, decider: str, messages: list[dict]):
            """Ask `decider` for free text, and record what it wrote as a hidden `text` event."""
            if key in written:
                raise ValueError(f"text key used twice in step {step}: {key!r}")
            written.add(key)
            fn = getattr(self.deciders[decider], "write", None)
            text, meta = (None, {"no_writer": True}) if fn is None else fn(key, person_id, messages)
            events.append(
                Event(
                    kind="text",
                    actor_id=person_id,
                    payload={"key": key, "person_id": person_id, "text": text, "meta": meta},
                    visible=False,
                )
            )
            return text, meta

        # The assistant's weekly and tax turns share decision-id counters, since both can
        # put a pay cut to the same staff in one week.
        seqs: dict[tuple[str, str], int] = {}
        events += assistant.assistant_phase(world, decide, write, rng, seqs)
        events += market.owners_phase(world, decide, rng)
        events += market.market_phase(world, rng)
        events += market.payroll_phase(world)
        events += adoption.fee_phase(world)
        events += tax.tax_phase(world, decide, rng, write=write, seqs=seqs)
        events += adoption.quarter_phase(world, decide, rng)
        events += press.tips_phase(world, decide, rng)
        events += tax.audit_phase(world, decide, rng)
        events += press.press_phase(world, decide, rng)
        events += assistant.replies_phase(world, write)
        events += market.closure_phase(world)
        assistant.note_week(world, events)
        events.append(Event(kind="state_hash", payload={"hash": state_hash(world)}))
        self.log.append(self.run_id, step, events)
        world.step += 1
        self.log.save_snapshot(self.run_id, step, world, rng.getstate())
