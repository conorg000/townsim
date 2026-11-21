"""The `townsim` command: the small economy's skill test, and the thin town run and
replayed from a SQLite file. No command calls a model."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Iterator

import typer

app = typer.Typer(help="Run the thin town or a generated hamlet and replay it, or run the small economy's skill test.")

_FAMILIES = str(Path(__file__).resolve().parents[2] / "config" / "families.yaml")

Db = Annotated[str, typer.Option("--db", help="SQLite file holding the runs.")]


def _fail(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


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


@contextmanager
def _plainly(db: str) -> Iterator[None]:
    """A file that is not a database, a place a file cannot be made, or a bad run or
    snapshot, as one plain line and exit code 1."""
    try:
        yield
    except sqlite3.Error as e:
        _fail(f"cannot use {db} as a run database: {e}")
    except KeyError as e:
        _fail(f"{db} has nothing kept for {e.args[0]!r}" if e.args else f"{db} is missing something it needs")
    except ValueError as e:
        _fail(str(e))


def _replay(db, run_id: str) -> None:
    from townsim.town.replies import ReplayMismatch
    from townsim.town.runs import replay

    try:
        pending = replay(db, run_id)
    except ReplayMismatch as e:
        _fail(str(e))
    typer.echo("replay ok")
    if pending:
        typer.echo(f"{len(pending)} stored replies are for a day the run did not finish")


@app.command("town-thin")
def town_thin_cmd(
    db: Annotated[str, typer.Option("--db", help="SQLite file to keep the run in.")],
    weeks: Annotated[int, typer.Option("--weeks", min=1, help="Weeks to run.")] = 8,
    run_id: Annotated[str, typer.Option("--run-id", help="Name of the run.")] = "thin",
) -> None:
    """Run the thin town on its script of replies, then replay it. No model is called.
    Exits 1 if the engine reported any errors."""
    from townsim.town.thin import thin_script, thin_town

    _run_weeks(db, run_id, 0, weeks, thin_town, thin_script)


def _run_weeks(db: str, run_id: str, seed: int, weeks: int, make_store, script) -> None:
    """Run a town week by week on a script of replies, print a line a week and the engine
    errors, then replay it. Exits 1 on engine errors or a run id already in the file."""
    from townsim.town.replies import ScriptedReplier
    from townsim.town.rundb import RunDB
    from townsim.town.runs import run

    with _plainly(db), RunDB(db) as rdb:
        try:
            rdb.get_run(run_id)
        except KeyError:
            rdb.create_run(run_id, seed=seed)
        else:
            _fail(f"{db} already has a run called {run_id!r}")
        store = make_store()
        replier = ScriptedReplier(script)
        for n in range(1, weeks + 1):
            store = run(rdb, run_id, store, replier, 7)
            typer.echo(f"week {n}  day {store.day}  {store.hash()[:12]}  ledger total {store.ledger.total()}")
        errors = rdb.errors(run_id)
        typer.echo(f"engine errors: {len(errors)}")
        for day, text in errors[:10]:
            typer.echo(f"  day {day}: {text}")
        _replay(rdb, run_id)
        if errors:
            raise typer.Exit(code=1)


@app.command("town-hamlet")
def town_hamlet_cmd(
    db: Annotated[str, typer.Option("--db", help="SQLite file to keep the run in.")],
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    weeks: Annotated[int, typer.Option("--weeks", min=1, help="Weeks to run.")] = 8,
    run_id: Annotated[str, typer.Option("--run-id", help="Name of the run.")] = "hamlet",
    families: Annotated[str, typer.Option("--families", help="YAML file of the model families.")] = _FAMILIES,
) -> None:
    """Generate the hamlet for a seed and run it on a scripted life, then replay it. No model
    is called. Exits 1 if the engine reported any errors."""
    from townsim.town import families as fam
    from townsim.town import gen, hamlet

    try:
        names = fam.names(families)
    except (OSError, ValueError) as e:
        _fail(f"cannot read the families from {families}: {e}")
    _run_weeks(db, run_id, seed, weeks, lambda: gen.generate(seed, names), hamlet.script)


@app.command("town-replay")
def town_replay_cmd(
    db: Db,
    run_id: Annotated[str, typer.Argument(help="Name of the run.")],
) -> None:
    """Run a kept run again from its stored replies and check every morning's state."""
    from townsim.town.rundb import RunDB

    if not Path(db).is_file():
        _fail(f"no database file {db}")
    with _plainly(db), RunDB(db) as rdb:
        try:
            rdb.get_run(run_id)
        except KeyError:
            _fail(f"{db} has no run called {run_id!r}")
        if not rdb.snapshot_days(run_id):
            _fail(f"run {run_id!r} in {db} has no days kept")
        _replay(rdb, run_id)
