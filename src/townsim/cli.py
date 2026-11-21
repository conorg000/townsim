"""The `townsim` command: run, fork, replay and report on runs kept in a SQLite log,
and compare deciders on canned probe decisions."""

import json
import os
import random
from pathlib import Path
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


def _open_existing(db: str) -> EventLog:
    """The log in `db`, which must already exist: a mistyped path is not a new, empty log."""
    if not Path(db).is_file():
        _fail(f"no database file {db}")
    return EventLog(db)


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
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Seed for a new run (default 1); with --resume, if given, "
                     "it must be the run's own seed."),
    ] = None,
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
    if resume:
        log = _open_existing(db)
        if not _has_run(log, run_id):
            _fail(f"no run {run_id!r} in {db} to resume")
        stored_seed = log.get_run(run_id)["seed"]
        if seed is not None and seed != stored_seed:
            _fail(f"--seed {seed} differs from the seed {stored_seed} run {run_id!r} was started with")
        seed = stored_seed
    else:
        seed = 1 if seed is None else seed
        log = EventLog(db)
    hint = (
        f"townsim run {config} --steps {steps} --seed {seed} --run-id {run_id} --db {db} --resume"
    )
    if resume:
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
    log = _open_existing(db)
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
def story(
    run_id: Annotated[str, typer.Argument(help="The run to read.")],
    db: Db = "runs.sqlite",
    out: Annotated[str | None, typer.Option("--out", help="Write the account to this file.")] = None,
) -> None:
    """Print what each AI system did in a run, week by week, with a few numbers."""
    from townsim.story import story as tell

    log = _open_existing(db)
    if not _has_run(log, run_id):
        _fail(f"no run {run_id!r} in {db}")
    text = tell(log, run_id)
    if out:
        with open(out, "w") as f:
            f.write(text)
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="The run to replay.")],
    db: Db = "runs.sqlite",
) -> None:
    """Rebuild a run from its recorded choices and check it matches its log exactly."""
    log = _open_existing(db)
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
    log = _open_existing(db)
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
    _check_system_one_keys(selected)
    try:
        people = probe_people(probe_list)
        built = make_deciders(selected, _decider_rng(seed), people)
    except ConfigError as exc:
        _fail(f"bad config: {exc}")
    typer.echo(json.dumps(compare_deciders(built, probe_list, n), indent=2))


def _check_system_one_keys(cfg: TownConfig) -> None:
    """Fail before any call if a selected System One decider has no API key to call with."""
    system_one = [name for name, spec in cfg.deciders.items() if spec.type == "system_one"]
    if not system_one:
        return
    from townsim.typesafe import TYPESAFE_KEY_ENV  # loaded only when a System One decider is used

    if not os.environ.get(TYPESAFE_KEY_ENV):
        _fail(
            f"decider(s) {', '.join(system_one)} call TypeSafe AI's API: "
            f"set {TYPESAFE_KEY_ENV} first; no calls were made"
        )


@app.command("estimate")
def estimate_cmd(
    config: Annotated[str, typer.Argument(help="The town file to estimate.")],
    prices: Annotated[
        str, typer.Option("--prices", help="Model prices file, dollars per million tokens.")
    ] = "config/prices.yaml",
    steps: Annotated[int, typer.Option("--steps", min=1, help="Weeks per run.")] = 104,
    runs: Annotated[int, typer.Option("--runs", min=1, help="Number of runs to price.")] = 1,
    seed: Annotated[int, typer.Option("--seed", help="Seed for the town used to count prompts.")] = 1,
    mix: Annotated[
        str,
        typer.Option(
            "--mix",
            help="Price a mix of models by role, e.g. owner=big,default=small.",
        ),
    ] = "",
    openrouter: Annotated[
        str,
        typer.Option(
            "--openrouter",
            help="Comma-separated OpenRouter model ids to price from its live list, instead of --prices.",
        ),
    ] = "",
    fee_pct: Annotated[
        float,
        typer.Option("--fee-pct", min=0, help="Percentage added on top, e.g. a credit purchase fee."),
    ] = 0.0,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of tables.")] = False,
) -> None:
    """Estimate what runs would cost with each priced model, without calling any model."""
    from townsim.estimate import count_usage, estimate, load_prices, openrouter_prices

    try:
        cfg = load_config(config)
        ids = [i.strip() for i in openrouter.split(",") if i.strip()]
        price_list = openrouter_prices(ids) if ids else load_prices(prices)
        mix_map = _parse_mix(mix)
        result = estimate(count_usage(cfg, steps, seed), price_list, runs=runs, mix=mix_map)
    except ConfigError as exc:
        _fail(str(exc))
    factor = 1 + fee_pct / 100
    result["fee_pct"] = fee_pct
    for row in result["models"].values():
        row["cost_usd"] *= factor
    if "mix" in result:
        result["mix"]["cost_usd"] *= factor
        for row in result["mix"]["roles"].values():
            row["cost_usd"] *= factor
    if cfg.assistant is not None:
        result["note"] = (
            f"Not counted: the calls of {cfg.assistant.name}, the AI system running "
            f"{len(cfg.assistant.clients)} firm(s), and townspeople's replies to its messages."
        )
    if as_json:
        typer.echo(json.dumps(result, indent=2))
        return
    source = "OpenRouter's live list" if ids else prices
    fee = f", plus a {fee_pct:g}% fee" if fee_pct else ""
    typer.echo(f"{runs} run(s) of {steps} weeks, seed {seed}. Estimate only; no model was called.")
    if "note" in result:
        typer.echo(result["note"])
    typer.echo(f"Prices from {source}{fee}.\n")
    typer.echo("Per run, by who is deciding:")
    for role, u in result["per_run"].items():
        typer.echo(f"  {role:<12}{u['calls']:>8,} calls{u['input_tokens']:>12,} input tokens")
    typer.echo("\nIf one model played everyone:")
    typer.echo(f"  {'model':<28}{'calls':>9}{'input tok':>13}{'output tok':>12}{'cost':>12}")
    for name, row in sorted(result["models"].items(), key=lambda kv: kv[1]["cost_usd"]):
        typer.echo(
            f"  {name:<28}{row['calls']:>9,}{row['input_tokens']:>13,}"
            f"{row['output_tokens']:>12,}{_dollars(row['cost_usd']):>12}"
        )
    if "mix" in result:
        typer.echo("\nWith the mix you gave:")
        for role, row in result["mix"]["roles"].items():
            typer.echo(f"  {role:<12}{row['model']:<28}{_dollars(row['cost_usd']):>12}")
        typer.echo(f"  {'total':<40}{_dollars(result['mix']['cost_usd']):>12}")


@app.command("skill")
def skill_cmd(
    seeds: Annotated[int, typer.Option("--seeds", min=1, help="How many seeds to run.")] = 10,
    first_seed: Annotated[int, typer.Option("--first-seed", help="The first seed.")] = 1,
    weeks: Annotated[int, typer.Option("--weeks", min=1, help="Weeks in each trial.")] = 52,
    levers: Annotated[
        bool, typer.Option("--levers/--no-levers", help="Also rerun Skilled with each skill off.")
    ] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")] = False,
) -> None:
    """Plan step 0, "Does skill pay?": scripted managers in the small economy. No model is called."""
    from townsim.econ.skilltest import experiment, format_table

    result = experiment(list(range(first_seed, first_seed + seeds)), weeks, levers)
    typer.echo(json.dumps(result, indent=2) if as_json else format_table(result))


def _parse_mix(text: str) -> dict[str, str] | None:
    if not text.strip():
        return None
    out: dict[str, str] = {}
    for part in text.split(","):
        role, sep, model = part.partition("=")
        if not sep or not role.strip() or not model.strip():
            raise ConfigError(f"--mix needs role=model pairs separated by commas, got {part!r}")
        out[role.strip()] = model.strip()
    return out


def _dollars(amount: float) -> str:
    return f"${amount:,.2f}" if amount >= 1 else f"${amount:,.4f}"
