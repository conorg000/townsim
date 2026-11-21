"""The `townsim` command: the small economy's skill test, and the thin town run and
replayed from a SQLite file. Only `town-live` and `town-probe` call models, behind a cap."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Iterator

import typer

app = typer.Typer(help="Run the thin town or a generated hamlet and replay it, or run the small economy's skill test.")

_ROOT = Path(__file__).resolve().parents[2]
_FAMILIES = str(_ROOT / "config" / "families.yaml")

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
    errors, then replay it. Exits 1 on engine errors or a run id already in the file. The
    town is made before the run is, so a town that cannot be made leaves no run behind."""
    from townsim.town.replies import ScriptedReplier
    from townsim.town.rundb import RunDB
    from townsim.town.runs import run

    try:
        store = make_store()
    except (ValueError, RuntimeError) as e:
        _fail(f"cannot make the town: {e}")
    with _plainly(db), RunDB(db) as rdb:
        try:
            rdb.get_run(run_id)
        except KeyError:
            rdb.create_run(run_id, seed=seed)
        else:
            _fail(f"{db} already has a run called {run_id!r}")
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
    families: Annotated[str, typer.Option("--families", help="YAML file of the model families.",
                                          show_default="config/families.yaml")] = _FAMILIES,
) -> None:
    """Generate the hamlet for a seed and run it on a scripted life, then replay it. No model
    is called. Exits 1 if the engine reported any errors."""
    from townsim.town import families as fam
    from townsim.town import gen, hamlet_script

    try:
        names = fam.names(families)
    except (OSError, ValueError) as e:
        _fail(f"cannot read the families from {families}: {e}")
    _run_weeks(db, run_id, seed, weeks, lambda: gen.generate(seed, names), hamlet_script.script)


_PRICES = str(_ROOT / "config" / "prices.yaml")
_KEY = "OPENROUTER_API_KEY"


def _need_key(command: str) -> None:
    """Fail in one line if the key is not in the environment; its value is never read."""
    if _KEY not in os.environ:
        _fail(f"no {_KEY} in the environment; run with: uv run --env-file .env townsim {command} …")


def _cap(cap: float, most: float) -> None:
    if not (cap > 0 and cap <= most):
        _fail(f"the cap must be above $0 and at most ${most:g}, got ${cap:g}")


def _shown(path: str) -> str:
    """A path inside the repository as relative to its root; any other as given."""
    try:
        return str(Path(path).resolve().relative_to(_ROOT))
    except ValueError:
        return path


def _load_families(families: str, prices: str):
    """The families and the prices, or one plain line and exit code 1."""
    from townsim.estimate import load_prices
    from townsim.town import families as fam

    try:
        return fam.load(families, prices), load_prices(prices)
    except (OSError, ValueError) as e:
        _fail(f"cannot read the families from {_shown(families)} and {_shown(prices)}: {e}")


def _litellm():
    """LiteLLM's completion function and a cost function over its replies, loaded only here."""
    import litellm

    litellm.drop_params = True
    litellm.suppress_debug_info = True
    return litellm.completion, lambda resp: litellm.completion_cost(completion_response=resp)


@app.command("town-live")
def town_live_cmd(
    db: Annotated[str, typer.Option("--db", help="SQLite file to keep the run in.")],
    weeks: Annotated[int, typer.Option("--weeks", min=1, help="Weeks to run in all, from day 0.")],
    cap: Annotated[float, typer.Option("--cap", help="Dollars the run may spend in all, at most 5.")],
    run_id: Annotated[str, typer.Option("--run-id", help="Name of the run.")] = "live",
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    families: Annotated[str, typer.Option("--families", help="YAML file of the model families.",
                                          show_default="config/families.yaml")] = _FAMILIES,
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
) -> None:
    """Run the hamlet with its people played by language models, behind a spending cap, then
    replay it. A run already in the file is resumed to the same total of weeks. CALLS MODELS
    AND SPENDS MONEY. Exits 1 on engine errors, a replay mismatch, or a stop other than the cap."""
    from townsim.town import live
    from townsim.town.replies import ReplayMismatch
    from townsim.town.rundb import RunDB

    _cap(cap, live.MAX_CAP)
    _need_key("town-live")
    fams, price_list = _load_families(families, prices)
    completion_fn, cost_fn = _litellm()
    with _plainly(db), RunDB(db) as rdb:
        try:
            got = live.play(rdb, run_id, seed, weeks, fams, price_list, cap, completion_fn, cost_fn=cost_fn,
                            echo=typer.echo)
        except ReplayMismatch as e:
            _fail(f"the run cannot go on: {e}")
        for name, n, dollars in live.spend_by_family(rdb, run_id):
            typer.echo(f"  {name}  {n} replies  ${dollars:.4f}")
        typer.echo(f"stored spend: ${rdb.spent(run_id):.4f}")
        typer.echo(f"lost to failed attempts: ${live.lost_spend(rdb, run_id):.4f}")
        errors = rdb.errors(run_id)
        typer.echo(f"engine errors: {len(errors)}")
        for day, text in errors[:10]:
            typer.echo(f"  day {day}: {text}")
        _replay(rdb, run_id)
        if got.stopped and got.stopped.startswith("cap: "):
            typer.echo(f"stopped by the cap of ${cap:g} after {got.days_done} days; run again with a larger cap to go on")
        if errors or (got.stopped and not got.stopped.startswith("cap: ")):
            raise typer.Exit(code=1)


_PROBE_OUT = "runs/probe.json"


@app.command("town-probe")
def town_probe_cmd(
    cap: Annotated[float, typer.Option("--cap", help="Dollars the probe may spend in all, at most 1.")],
    families: Annotated[str, typer.Option("--families", help="YAML file of the model families.",
                                          show_default="config/families.yaml")] = _FAMILIES,
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    n: Annotated[int, typer.Option("--n", min=1, help="Times each family is asked each case.")] = 3,
    out: Annotated[str, typer.Option("--out", help="JSON file the rows and replies are written to.")] = _PROBE_OUT,
) -> None:
    """Show every family the same four views of a scripted hamlet, n times each, behind a
    spending cap; write the rows and replies to --out and print a table. CALLS MODELS AND
    SPENDS MONEY. Exits 1 if it stopped other than at the cap."""
    import dataclasses

    from townsim.town import probe

    _cap(cap, probe.MAX_CAP)
    _need_key("town-probe")
    fams, price_list = _load_families(families, prices)
    try:
        cs = probe.cases(seed, list(fams))
    except (ValueError, RuntimeError) as e:
        _fail(f"cannot make the cases: {e}")
    completion_fn, cost_fn = _litellm()
    stopped = None
    try:
        rows = probe.run(cs, fams, price_list, completion_fn, cap, n=n, cost_fn=cost_fn)
    except probe.ProbeStopped as e:
        rows, stopped = e.rows, str(e)
    kept = {"seed": seed, "n": n, "cap": cap, "stopped": stopped,
            "families": {name: f.model for name, f in fams.items()},
            "cases": [{"label": c.label, "pid": c.pid, "kind": c.kind, "day": c.day} for c in cs],
            "rows": [dataclasses.asdict(r) for r in rows]}
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(kept, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        _fail(f"cannot write {out}: {e.strerror or e}")
    typer.echo(probe.table(rows) if rows else "no replies")
    typer.echo(f"spent ${sum(r.cost for r in rows):.4f}; rows written to {out}")
    if stopped:
        typer.echo(f"stopped: {stopped}")
        if not stopped.startswith("cap: "):
            raise typer.Exit(code=1)


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
