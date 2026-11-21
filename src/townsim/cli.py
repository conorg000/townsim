"""The `townsim` command: the small economy's skill test, and the thin town run and
replayed from a SQLite file. No command calls a model."""

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Run the thin town and replay it, or run the small economy's skill test.")

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
    """Run the thin town on its script of replies, then replay it. No model is called."""
    from townsim.town.replies import ScriptedReplier
    from townsim.town.rundb import RunDB
    from townsim.town.runs import run
    from townsim.town.thin import thin_script, thin_town

    with RunDB(db) as rdb:
        try:
            rdb.create_run(run_id, seed=0)
        except ValueError:
            _fail(f"{db} already has a run called {run_id!r}")
        store = thin_town()
        replier = ScriptedReplier(thin_script)
        for n in range(1, weeks + 1):
            store = run(rdb, run_id, store, replier, 7)
            typer.echo(f"week {n}  day {store.day}  {store.hash()[:12]}  ledger total {store.ledger.total()}")
        _replay(rdb, run_id)


@app.command("town-replay")
def town_replay_cmd(
    db: Db,
    run_id: Annotated[str, typer.Argument(help="Name of the run.")],
) -> None:
    """Run a kept run again from its stored replies and check every morning's state."""
    from townsim.town.rundb import RunDB

    if not Path(db).is_file():
        _fail(f"no database file {db}")
    with RunDB(db) as rdb:
        try:
            rdb.get_run(run_id)
        except KeyError:
            _fail(f"{db} has no run called {run_id!r}")
        if not rdb.snapshot_days(run_id):
            _fail(f"run {run_id!r} in {db} has no days kept")
        _replay(rdb, run_id)
