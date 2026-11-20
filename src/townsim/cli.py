"""The `townsim` command: run, fork, replay and report on runs kept in a SQLite log,
and compare deciders on canned probe decisions."""

import json
import random
from typing import Annotated

import typer

from townsim.compare import compare_deciders, load_probes, probe_people
from townsim.config import ConfigError, TownConfig, build_world, load_config
from townsim.decide import ModelUnavailable
from townsim.events import EventLog
from townsim.measures import measures, stability
from townsim.runs import ReplayMismatch, fork as fork_run, replay as replay_run
from townsim.sim import Simulation, make_deciders
from townsim.world import total_money

app = typer.Typer(help="Run the town, branch its runs, check them and report on them.")

Db = Annotated[str, typer.Option("--db", help="SQLite file holding the runs.")]
RunId = Annotated[str, typer.Option("--run-id", help="Name of the run.")]


def _fail(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _decider_rng(seed: int) -> random.Random:
    """The rule deciders' own rng, kept apart from the engine's `Random(seed)`."""
    return random.Random(f"{seed}:deciders")


def _has_run(log: EventLog, run_id: str) -> bool:
    try:
        log.get_run(run_id)
    except KeyError:
        return False
    return True


def _run_config(log: EventLog, run_id: str) -> TownConfig:
    return TownConfig.model_validate(json.loads(log.get_run(run_id)["config_json"]))


def _start_step(log: EventLog, run_id: str) -> int:
    """The step a run begins at: 0, or for a fork the step it was forked at."""
    parent_step = log.get_run(run_id)["parent_step"]
    return 0 if parent_step is None else parent_step


def _advance(sim: Simulation, target_step: int, resume_hint: str) -> None:
    """Run `sim` until its world reaches `target_step`; a model outage aborts with a hint."""
    try:
        sim.run(max(0, target_step - sim.world.step))
    except ModelUnavailable as exc:
        _fail(
            f"run {sim.run_id!r} aborted at step {sim.world.step}: {exc}\n"
            f"Steps before {sim.world.step} are saved. To continue, run:\n  {resume_hint}"
        )
    except ConfigError as exc:
        _fail(
            f"run {sim.run_id!r} stopped at step {sim.world.step}: {exc}\n"
            f"Steps before {sim.world.step} are saved; fix the setting and resume with:\n"
            f"  {resume_hint}"
        )
    typer.echo(f"run {sim.run_id!r} is at step {sim.world.step}")


@app.command()
def run(
    config: Annotated[str, typer.Argument(help="The town file, e.g. config/town.yaml.")],
    steps: Annotated[int, typer.Option("--steps", min=0, help="Steps the run should have.")] = 104,
    seed: Annotated[int, typer.Option("--seed", help="Seed for a new run.")] = 1,
    run_id: RunId = "base",
    db: Db = "runs.sqlite",
    resume: Annotated[
        bool, typer.Option("--resume", help="Continue an existing run up to --steps.")
    ] = False,
) -> None:
    """Start a run of the town, or with --resume continue one from its latest snapshot."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        _fail(f"bad config: {exc}")
    log = EventLog(db)
    hint = (
        f"townsim run {config} --steps {steps} --seed {seed} --run-id {run_id} --db {db} --resume"
    )
    if resume:
        if not _has_run(log, run_id):
            _fail(f"no run {run_id!r} in {db} to resume")
        if log.get_run(run_id)["config_json"] != cfg.model_dump_json():
            _fail(f"{config} differs from the config run {run_id!r} was started with")
        sim = Simulation.resume(log, run_id, {})
        sim.deciders = make_deciders(cfg, _decider_rng(sim.seed), sim.world.people)
        _advance(sim, _start_step(log, run_id) + steps, hint)
        return

    if _has_run(log, run_id):
        _fail(f"run {run_id!r} already exists in {db}; use --resume to continue it")
    world = build_world(cfg)
    deciders = make_deciders(cfg, _decider_rng(seed), world.people)
    log.begin()
    try:
        log.create_run(run_id, seed, cfg.model_dump_json())
        sim = Simulation(world, deciders, log, run_id, seed)
    except BaseException:
        log.rollback()
        raise
    log.commit()
    _advance(sim, steps, hint)


@app.command()
def fork(
    db: Db = "runs.sqlite",
    source: Annotated[str, typer.Option("--from", help="The run to branch from.")] = "base",
    at: Annotated[int, typer.Option("--at", help="Branch from the world at the start of this step.")] = 104,
    run_id: RunId = "f1",
    seed: Annotated[int, typer.Option("--seed", help="Seed for the new run.")] = 2,
    steps: Annotated[int, typer.Option("--steps", min=0, help="Steps to run after the branch.")] = 104,
) -> None:
    """Start a new run from another run's world at the start of step --at."""
    log = EventLog(db)
    if not _has_run(log, source):
        _fail(f"no run {source!r} in {db}")
    if _has_run(log, run_id):
        _fail(f"run {run_id!r} already exists in {db}")
    cfg = _run_config(log, source)
    try:
        sim = fork_run(log, source, at, run_id, seed, {})
    except ValueError as exc:
        _fail(str(exc))
    sim.deciders = make_deciders(cfg, _decider_rng(seed), sim.world.people)
    hint = (
        f"townsim run CONFIG --steps {steps} --run-id {run_id} --db {db} --resume"
        f"  (CONFIG: the town file run {source!r} was started with)"
    )
    _advance(sim, at + steps, hint)


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="The run to replay.")],
    db: Db = "runs.sqlite",
) -> None:
    """Rebuild a run from its recorded choices and check it matches its log exactly."""
    log = EventLog(db)
    if not _has_run(log, run_id):
        _fail(f"no run {run_id!r} in {db}")
    try:
        replay_run(log, run_id)
    except ReplayMismatch as exc:
        _fail(f"replay of {run_id!r} does not match at step {exc.step}: {exc.detail}")
    typer.echo(f"replay of {run_id!r} matches its log")


@app.command()
def report(
    run_id: Annotated[str, typer.Argument(help="The run to report on.")],
    db: Db = "runs.sqlite",
) -> None:
    """Print the run's measures and stability verdict as one JSON object."""
    log = EventLog(db)
    if not _has_run(log, run_id):
        _fail(f"no run {run_id!r} in {db}")
    start_key = _start_step(log, run_id) - 1
    try:
        start_world, _ = log.load_snapshot(run_id, start_key)
    except ValueError as exc:
        _fail(str(exc))
    out = {
        "run_id": run_id,
        "measures": measures(log, run_id),
        "stability": stability(log, run_id, total_money(start_world)),
    }
    typer.echo(json.dumps(out, indent=2))


@app.command("compare-deciders")
def compare_deciders_cmd(
    config: Annotated[str, typer.Argument(help="The town file whose deciders to compare.")],
    deciders: Annotated[
        str, typer.Option("--deciders", help="Comma-separated decider names from the config.")
    ],
    probes: Annotated[
        str, typer.Option("--probes", help="The probe decisions file.")
    ] = "config/probe_decisions.yaml",
    n: Annotated[int, typer.Option("--n", min=1, help="Calls per decider per probe.")] = 50,
    seed: Annotated[int, typer.Option("--seed", help="Seed for the deciders' rng.")] = 1,
) -> None:
    """Put the same probe decisions to each decider and print what each chose as one JSON object."""
    try:
        cfg = load_config(config)
        probe_list = load_probes(probes)
    except ConfigError as exc:
        _fail(f"bad config: {exc}")
    names = [name.strip() for name in deciders.split(",") if name.strip()]
    if not names:
        _fail("--deciders names no decider")
    unknown = [name for name in names if name not in cfg.deciders]
    if unknown:
        known = ", ".join(sorted(cfg.deciders))
        _fail(f"unknown decider(s) {', '.join(unknown)}; the config has: {known}")
    selected = cfg.model_copy(update={"deciders": {name: cfg.deciders[name] for name in names}})
    try:
        built = make_deciders(selected, _decider_rng(seed), probe_people(probe_list))
    except ConfigError as exc:
        _fail(f"bad config: {exc}")
    typer.echo(json.dumps(compare_deciders(built, probe_list, n), indent=2))
