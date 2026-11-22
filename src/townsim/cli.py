"""The `townsim` command: the small economy's skill test, and the thin town run and
replayed from a SQLite file, and forked into arms. Only `town-live`, `town-probe`, `town-seat-probe` and
`town-awareness` call models, behind a cap."""

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
Start = Annotated[str | None, typer.Option("--start", help="Day 0 of a new place, a Monday written YYYY-MM-DD "
                                                            "(Monday 2 March 2026 when not given).")]


def _fail(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _start(typed: str | None) -> str | None:
    """The start as ISO text, or one plain line and exit code 1 unless it is a Monday."""
    from townsim.town.clock import parse_start

    if typed is None:
        return None
    try:
        return parse_start(typed).isoformat()
    except ValueError as e:
        _fail(str(e))


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
            rdb.put_note(run_id, "start", store.start)
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
    start: Start = None,
) -> None:
    """Generate the hamlet for a seed and run it on a scripted life, then replay it. No model
    is called. Exits 1 if the engine reported any errors."""
    from townsim.town import families as fam
    from townsim.town import gen, hamlet_script

    day0 = _start(start)
    try:
        names = fam.names(families)
    except (OSError, ValueError) as e:
        _fail(f"cannot read the families from {families}: {e}")
    _run_weeks(db, run_id, seed, weeks, lambda: gen.generate(seed, names, start=day0), hamlet_script.script)


_PRICES = str(_ROOT / "config" / "prices.yaml")
_KEY = "OPENROUTER_API_KEY"


def _need_key(command: str) -> None:
    """Fail in one line if the key is not in the environment; its value is never read."""
    if _KEY not in os.environ:
        _fail(f"no {_KEY} in the environment; run with: uv run --env-file .env townsim {command} …")


def _cap(typed: str, most: float) -> float:
    """The cap as dollars, or one plain line, showing it as typed, and exit code 1 unless it
    is a number above 0 and at most `most`."""
    try:
        cap = float(typed)
    except ValueError:
        cap = float("nan")
    if not (cap > 0 and cap <= most):
        _fail(f"the cap must be a number of dollars above 0 and at most {most:g}, got {typed}")
    return cap


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


def _load_seat(seat: str, prices: str):
    """The seat's family, or one plain line and exit code 1."""
    from townsim.town import families as fam

    try:
        return fam.load_seat(seat, prices)
    except (OSError, ValueError) as e:
        _fail(f"cannot read the seat from {_shown(seat)} and {_shown(prices)}: {e}")


def _load_outside(path: str, prices: str):
    """The outside families and the file's settings, or one plain line and exit code 1."""
    from townsim.town import families as fam
    try:
        return fam.load_outside(path, prices)
    except (OSError, ValueError) as e:
        _fail(f"cannot read the outside families from {_shown(path)} and {_shown(prices)}: {e}")


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
    cap: Annotated[str, typer.Option("--cap", help="Dollars the run may spend in all, at most 5.")],
    run_id: Annotated[str, typer.Option("--run-id", help="Name of the run.")] = "live",
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    families: Annotated[str, typer.Option("--families", help="YAML file of the model families.",
                                          show_default="config/families.yaml")] = _FAMILIES,
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    past_refusal: Annotated[bool, typer.Option("--past-refusal", help="Resume a run past a stored refusal.")] = False,
    seat: Annotated[str | None, typer.Option("--seat", help="YAML file of the seat's model, for a run with the "
                                                            "product launched (config/seat.yaml).")] = None,
    start: Start = None,
    outside: Annotated[str | None, typer.Option("--outside", help="YAML file of the lifter's, narrator's, desks' and "
                                                                  "web writer's models, for a run with lane B "
                                                                  "(config/outside.yaml).")] = None,
    lanes: Annotated[str | None, typer.Option("--lanes", help="Install lane B on day 0 of a new run (b): tasks, "
                                                              "rules, clubs, the web, the desks and the company's "
                                                              "controls. Needs --outside.")] = None,
) -> None:
    """Run the hamlet with its people played by language models, behind a spending cap, then
    replay it. A run already in the file is resumed to the same total of weeks. CALLS MODELS
    AND SPENDS MONEY. Exits 1 on engine errors, a replay mismatch, or a stop other than the cap."""
    from townsim.town import live
    from townsim.town.replies import ReplayMismatch
    from townsim.town.rundb import RunDB

    dollars = _cap(cap, live.MAX_CAP)
    day0 = _start(start)
    _need_key("town-live")
    fams, price_list = _load_families(families, prices)
    seat_family = _load_seat(seat, prices) if seat else None
    outside_families, settings = _load_outside(outside, prices) if outside else (None, None)
    completion_fn, cost_fn = _litellm()
    with _plainly(db), RunDB(db) as rdb:
        try:
            got = live.play(rdb, run_id, seed, weeks, fams, price_list, dollars, completion_fn, cost_fn=cost_fn,
                            past_refusal=past_refusal, echo=typer.echo, seat=seat_family, start=day0,
                            outside=outside_families, settings=settings, lanes=lanes)
        except ReplayMismatch as e:
            _fail(f"the run cannot go on: {e}")
        for name, n, spent in live.spend_by_family(rdb, run_id):
            typer.echo(f"  {name}  {n} replies  ${spent:.4f}")
        typer.echo(f"stored spend: ${rdb.spent(run_id):.4f}")
        typer.echo(f"lost to failed attempts: ${live.lost_spend(rdb, run_id):.4f}")
        errors = rdb.errors(run_id)
        typer.echo(f"engine errors: {len(errors)}")
        for day, text in errors[:10]:
            typer.echo(f"  day {day}: {text}")
        _replay(rdb, run_id)
        if got.stopped and got.stopped.startswith("cap: "):
            typer.echo(f"stopped by the cap of ${dollars:g} after {got.days_done} days; run again with a larger cap to go on")
        if errors or (got.stopped and not got.stopped.startswith("cap: ")):
            raise typer.Exit(code=1)


_PROBE_OUT = "runs/probe.json"


@app.command("town-probe")
def town_probe_cmd(
    cap: Annotated[str, typer.Option("--cap", help="Dollars the probe may spend in all, at most 1.")],
    families: Annotated[str, typer.Option("--families", help="YAML file of the model families.",
                                          show_default="config/families.yaml")] = _FAMILIES,
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    n: Annotated[int, typer.Option("--n", min=1, help="Times each family is asked each case.")] = 3,
    out: Annotated[str, typer.Option("--out", help="JSON file the rows and replies are written to.")] = _PROBE_OUT,
    start: Start = None,
) -> None:
    """Show every family the same four views of a scripted hamlet, n times each, behind a
    spending cap; write the rows and replies to --out and print a table. CALLS MODELS AND
    SPENDS MONEY. Exits 1 if it stopped other than at the cap."""
    import dataclasses

    from townsim.town import probe

    dollars = _cap(cap, probe.MAX_CAP)
    day0 = _start(start)
    _need_key("town-probe")
    fams, price_list = _load_families(families, prices)
    try:
        cs = probe.cases(seed, list(fams), echo=typer.echo, start=day0)
    except (ValueError, RuntimeError) as e:
        _fail(f"cannot make the cases: {e}")
    completion_fn, cost_fn = _litellm()
    stopped = None
    try:
        rows = probe.run(cs, fams, price_list, completion_fn, dollars, n=n, cost_fn=cost_fn)
    except probe.ProbeStopped as e:
        rows, stopped = e.rows, str(e)
    kept = {"seed": seed, "start": day0, "n": n, "cap": dollars, "stopped": stopped,
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


_SEAT_PROBE_OUT = "runs/seat-probe.json"


@app.command("town-seat-probe")
def town_seat_probe_cmd(
    cap: Annotated[str, typer.Option("--cap", help="Dollars the probe may spend in all, at most 1.")],
    seat: Annotated[str, typer.Option("--seat", help="YAML file of the seat's model.")],
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    families: Annotated[str, typer.Option("--families", help="YAML file of the model families (their names only).",
                                          show_default="config/families.yaml")] = _FAMILIES,
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    n: Annotated[int, typer.Option("--n", min=1, help="Times each drawn instance's first turn is run.")] = 2,
    out: Annotated[str, typer.Option("--out", help="JSON file the rows are written to.")] = _SEAT_PROBE_OUT,
    start: Start = None,
) -> None:
    """Run each drawn instance's first turn n times on the seat's model, after a week of the
    hamlet's script and the launch made in memory, behind a spending cap; write the rows to --out
    and print a table. CALLS A MODEL AND SPENDS MONEY. Exits 1 if it stopped other than at the cap."""
    import dataclasses

    from townsim.town import families as fam, seatprobe

    dollars = _cap(cap, seatprobe.MAX_CAP)
    day0 = _start(start)
    _need_key("town-seat-probe")
    seat_family = _load_seat(seat, prices)
    from townsim.estimate import load_prices
    price_list = load_prices(prices)
    try:
        store = seatprobe.morning(seed, fam.names(families), start=day0)
    except (OSError, ValueError) as e:
        _fail(f"cannot make the morning: {e}")
    completion_fn, cost_fn = _litellm()
    stopped = None
    try:
        rows = seatprobe.run(store, seat_family, price_list, completion_fn, dollars, n=n, cost_fn=cost_fn)
    except seatprobe.ProbeStopped as e:
        rows, stopped = e.rows, str(e)
    kept = {"seed": seed, "start": day0, "n": n, "cap": dollars, "stopped": stopped, "seat": seat_family.model,
            "rows": [dataclasses.asdict(r) for r in rows]}
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(kept, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        _fail(f"cannot write {out}: {e.strerror or e}")
    typer.echo(seatprobe.table(rows) if rows else "no turns")
    typer.echo(f"spent ${sum(r.cost for r in rows):.4f}; rows written to {out}")
    if stopped:
        typer.echo(f"stopped: {stopped}")
        if not stopped.startswith("cap: "):
            raise typer.Exit(code=1)


_AWARENESS_OUT = "runs/awareness.json"


@app.command("town-awareness")
def town_awareness_cmd(
    db: Db,
    run_id: Annotated[str, typer.Option("--run", help="The kept run whose instances' mornings are read.")],
    cap: Annotated[str, typer.Option("--cap", help="Dollars the reading may spend in all, at most 2.")],
    reader: Annotated[str, typer.Option("--reader", help="YAML file of the reader's model (a seat file).")],
    variants: Annotated[str, typer.Option("--variants", help="Variants read, comma-separated.")] = "v0,v1,v2,v3,k_plus",
    n: Annotated[int, typer.Option("--slices", min=0, help="Mornings sampled for the reader.")] = 24,
    pairs: Annotated[str, typer.Option("--pairs", help="Pairs of variants compared, comma-separated.")] = "v1:v0,v2:v1",
    pair_n: Annotated[int, typer.Option("--pair-slices", min=0, help="Mornings compared in each pair.")] = 20,
    seed: Annotated[int, typer.Option("--seed", help="Seed of the sample.")] = 1,
    out: Annotated[str, typer.Option("--out", help="JSON file every reply is kept in; a rerun asks only for "
                                                   "what it lacks.")] = _AWARENESS_OUT,
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    estimate: Annotated[bool, typer.Option("--estimate", help="Print the calls and their worst case, send nothing.")] = False,
    material: Annotated[str | None, typer.Option("--material", help="Read only this variant (b-surfaces: the morning "
                                                                    "with the day's results from the web, tasks and "
                                                                    "rules), with no pairs.")] = None,
) -> None:
    """Show sampled mornings of a kept run's instances, rebuilt in each variant of the seat, to a
    fresh reader model: three fixed questions per morning, a judge of the first answer, and
    pairwise "which is real" in both orders; print one table. CALLS A MODEL AND SPENDS MONEY."""
    from townsim.estimate import load_prices
    from townsim.town import awareness as aw
    from townsim.town.rundb import RunDB

    dollars = _cap(cap, aw.MAX_CAP)
    family = aw.reader_family(_load_seat(reader, prices))
    price_list = load_prices(prices)
    wanted = [v.strip() for v in variants.split(",") if v.strip()]
    if material is not None:
        wanted, pairs = [material.strip()], ""
    if not Path(db).is_file():
        _fail(f"no database file {db}")
    with _plainly(db), RunDB(db) as rdb:
        rdb.get_run(run_id)
        noted = json.loads(rdb.note(run_id, "seat") or "null")
        model = noted["model"] if noted else family.model
        plan = aw.plan(rdb, run_id, wanted, n, aw.parse_pairs(pairs), pair_n, seed, model)
    meta = {"db": Path(db).name, "run": run_id, "seed": seed, "seat_model": model,
            "reader": {"model": family.model, "params": dict(family.params)}}
    with _plainly(out):
        kept = aw.Kept(out, meta)
        if estimate:
            why = aw.run(plan, kept, family, price_list, None, dollars, estimate_only=True, echo=typer.echo)
            if why:
                _fail(f"stopped: {why}")
            return
        _need_key("town-awareness")
        completion_fn, cost_fn = _litellm()
        stopped = aw.run(plan, kept, family, price_list, completion_fn, dollars, cost_fn=cost_fn, echo=typer.echo)
    typer.echo(aw.table(plan, kept))
    typer.echo(f"replies kept in {out}")
    if stopped:
        typer.echo(f"stopped: {stopped}")
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


@app.command("town-arms")
def town_arms_cmd(
    db: Db,
    parent: Annotated[str, typer.Option("--parent", help="The run to fork from.")],
    at_day: Annotated[int, typer.Option("--at-day", help="The day (a Monday) whose morning the arms start from.")],
    prefix: Annotated[str, typer.Option("--prefix", help="The arms are <prefix>-none, <prefix>-choice and <prefix>-drawn.")],
    start: Annotated[str | None, typer.Option("--start", help="The parent's day 0 (YYYY-MM-DD, a Monday); the arms "
                                                              "are refused if the parent began on another.")] = None,
) -> None:
    """Fork a kept run on one morning into three arms: no product; the product launched; the
    product launched with clients drawn from the seed. The arms keep the parent's start. No
    model is called."""
    from townsim.town import arms
    from townsim.town.rundb import RunDB

    day0 = _start(start)
    if not Path(db).is_file():
        _fail(f"no database file {db}")
    with _plainly(db), RunDB(db) as rdb:
        rdb.get_run(parent)
        if rdb.snapshot_hash(parent, at_day) is None:
            _fail(f"run {parent!r} in {db} has no morning kept for day {at_day}")
        for arm in arms.ARMS:
            store = arms.fork_arm(rdb, parent, at_day, f"{prefix}-{arm}", arm, start=day0)
            subs = ", ".join(f"{s.id} {s.client}" for s in store.all("subscriptions"))
            typer.echo(f"{prefix}-{arm}: day {at_day}" + (f"; drawn: {subs}" if subs else ""))


@app.command("town-story")
def town_story_cmd(
    run_id: Annotated[str, typer.Argument(help="Name of the run.")],
    db: Db,
    out: Annotated[str | None, typer.Option("--out", help="Markdown file the account is written to.",
                                            show_default="runs/<run-id>-story.md")] = None,
) -> None:
    """Write a kept run as a plain account a person can read: the run, week by week, counts by
    family, things to look at and balances. No model is called. Prints the file written."""
    from townsim.town import story
    from townsim.town.rundb import RunDB

    if not Path(db).is_file():
        _fail(f"no database file {db}")
    if out is None:
        if not run_id or any(c in run_id for c in "/\\") or run_id.startswith("."):
            _fail(f"run id {run_id!r} cannot name a file; give --out")
        out = str(Path("runs") / f"{run_id}-story.md")
    with _plainly(db), RunDB(db) as rdb:
        try:
            rdb.get_run(run_id)
        except KeyError:
            _fail(f"{db} has no run called {run_id!r}")
        text = story.account(rdb, run_id)
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    except OSError as e:
        _fail(f"cannot write {out}: {e.strerror or e}")
    typer.echo(os.path.relpath(out))


_OUTSIDE = str(_ROOT / "config" / "outside.yaml")


def _write_json(out: str, kept: dict) -> None:
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(kept, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        _fail(f"cannot write {out}: {e.strerror or e}")


@app.command("town-narrator-probe")
def town_narrator_probe_cmd(
    cap: Annotated[str, typer.Option("--cap", help="Dollars the probe may spend in all, at most 1.")],
    out: Annotated[str, typer.Option("--out", help="JSON file the rows are written to.")] = "runs/narrator-probe.json",
    outside: Annotated[str, typer.Option("--outside", help="YAML file of the lifter's and narrator's models.",
                                         show_default="config/outside.yaml")] = _OUTSIDE,
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    start: Start = None,
) -> None:
    """A probe of the setup: file the 26 statements of tests/data/attempts_b.jsonl by their named actors on
    seed 1 with lane B installed at day 7, each with one lift and two filings, behind a spending cap; write the
    rows and the gates (read, not enforced) to --out and print a table. CALLS MODELS AND SPENDS MONEY."""
    from townsim.estimate import load_prices
    from townsim.town import narrator_probe as npb

    dollars = _cap(cap, npb.MAX_CAP)
    day0 = _start(start)
    _need_key("town-narrator-probe")
    fams, settings = _load_outside(outside, prices)
    store = npb.morning(seed, start=day0)
    from townsim.town import tasks
    tasks.configure(store, settings)
    completion_fn, cost_fn = _litellm()
    replier, spent = npb.model_replier(fams, load_prices(prices), completion_fn, dollars, cost_fn)
    stopped = None
    try:
        rows = npb.run(store, replier, npb.statements(store))
    except npb.ProbeStopped as e:
        rows, stopped = e.rows, str(e)
    _write_json(out, npb.kept(rows, {"probe": "narrator", "seed": seed, "start": day0, "cap": dollars,
                                     "stopped": stopped, "models": {k: f.model for k, f in fams.items()}}))
    typer.echo("A probe of the setup: the lifter and narrator on fixed statements.")
    typer.echo(npb.table(rows) if rows else "no rows")
    typer.echo(f"spent ${spent[0]:.4f}; rows written to {out}")
    if stopped:
        typer.echo(f"stopped: {stopped}")
        if not stopped.startswith("cap: "):
            raise typer.Exit(code=1)


@app.command("town-desk-probe")
def town_desk_probe_cmd(
    cap: Annotated[str, typer.Option("--cap", help="Dollars the probe may spend in all, at most 0.5.")],
    out: Annotated[str, typer.Option("--out", help="JSON file the rows are written to.")] = "runs/desk-probe.json",
    outside: Annotated[str, typer.Option("--outside", help="YAML file of the desks' model.",
                                         show_default="config/outside.yaml")] = _OUTSIDE,
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    start: Start = None,
) -> None:
    """A probe of the setup: twelve letters to the outside desks on seed 1 with lane B (two per kind of desk,
    four asking more than its table allows), answered by the desks' model behind a spending cap; write the
    actions asked and applied after clamping and the wording found to --out. CALLS MODELS AND SPENDS MONEY."""
    from townsim.estimate import load_prices
    from townsim.town import desk_probe as dpb, narrator_probe as npb

    dollars = _cap(cap, dpb.MAX_CAP)
    day0 = _start(start)
    _need_key("town-desk-probe")
    fams, _ = _load_outside(outside, prices)
    store = dpb.morning(seed, start=day0)
    completion_fn, cost_fn = _litellm()
    replier, spent = npb.model_replier(fams, load_prices(prices), completion_fn, dollars, cost_fn)
    stopped = None
    try:
        rows = dpb.run(store, replier)
    except dpb.ProbeStopped as e:
        rows, stopped = e.rows, str(e)
    _write_json(out, dpb.kept(rows, {"probe": "desk", "seed": seed, "start": day0, "cap": dollars, "stopped": stopped,
                                     "model": fams["desk"].model}))
    typer.echo("A probe of the setup: the outside desks on fixed letters.")
    typer.echo(dpb.table(rows) if rows else "no rows")
    typer.echo(f"spent ${spent[0]:.4f}; rows written to {out}")
    if stopped:
        typer.echo(f"stopped: {stopped}")
        if not stopped.startswith("cap: "):
            raise typer.Exit(code=1)


@app.command("town-mandate-probe")
def town_mandate_probe_cmd(
    db: Db,
    cap: Annotated[str, typer.Option("--cap", help="Dollars the probe may spend in all, at most 1.")],
    seat: Annotated[str, typer.Option("--seat", help="YAML file of the seat's model.")],
    outside: Annotated[str, typer.Option("--outside", help="YAML file of the lifter's, narrator's, desks' and web "
                                                           "writer's models.", show_default="config/outside.yaml")] = _OUTSIDE,
    run_id: Annotated[str, typer.Option("--run-id", help="Name of the run (its pre-launch week is <run-id>-pre).")] = "mandate",
    prices: Annotated[str, typer.Option("--prices", help="YAML file of model prices.",
                                        show_default="config/prices.yaml")] = _PRICES,
    seed: Annotated[int, typer.Option("--seed", help="The seed the hamlet is generated from.")] = 1,
    start: Start = None,
) -> None:
    """A probe of the setup, not an arm: one scripted shop owner subscribes, connects everything with access to
    act and writes one broad request; the seat and what is beyond the people run for a week on their models
    with scripted townspeople and lane B, behind a spending cap; the run is kept in --db for town-story and
    replayed. CALLS MODELS AND SPENDS MONEY."""
    from townsim.estimate import load_prices
    from townsim.town import live, mandate_probe as mp
    from townsim.town.modelreplier import ModelReplier
    from townsim.town.replies import ReplayMismatch
    from townsim.town.rundb import RunDB

    dollars = _cap(cap, mp.MAX_CAP)
    day0 = _start(start)
    _need_key("town-mandate-probe")
    seat_family = _load_seat(seat, prices)
    fams, settings = _load_outside(outside, prices)
    completion_fn, cost_fn = _litellm()
    typer.echo(mp.NOTICE)
    with _plainly(db), RunDB(db) as rdb:
        def lost(amount: float) -> None:
            rdb.put_note(run_id, "lost_spend", repr(live.lost_spend(rdb, run_id) + float(amount)))
        model = ModelReplier({}, load_prices(prices), {}, completion_fn, cap=dollars,
                             spent=lambda: live.spent(rdb, run_id), cost_fn=cost_fn, lost=lost, seat=seat_family,
                             outside=fams)
        try:
            store = mp.make(rdb, run_id, mp.Mixed(model), seed=seed, start=day0, settings=settings,
                            seat=seat_family, outside=fams)
        except ReplayMismatch as e:
            _fail(f"the probe cannot go on: {e}")
        typer.echo(f"{run_id}: day {store.day}; spent ${live.spent(rdb, run_id):.4f}")
        if model.stopped:
            typer.echo(f"stopped: {model.stopped}")
        _replay(rdb, run_id)
        typer.echo(f"the assistant's days: townsim town-story {run_id} --db {db}")
