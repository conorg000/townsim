"""Runs kept in a `RunDB`: run (recording every reply), resume, replay exactly, fork."""

from __future__ import annotations

from typing import Callable

from townsim.town.loop import run_day
from townsim.town.replies import Keys, Recording, Replaying, Replier, ReplayMismatch, StopRun
from townsim.town.rundb import RunDB
from townsim.town.store import Store


def run(db: RunDB, run_id: str, store: Store, replier: Replier, days: int) -> Store:
    """Run `days` days from `store.day`, keeping each morning's snapshot, each day's
    changes, turns and engine errors, and every reply. On `StopRun`, the day it was raised in is not kept: the
    store returned is that morning's snapshot, loaded fresh.

    Raises ValueError, writing nothing, if the run already has a snapshot for the day it
    is about to run and that snapshot's state differs from `store`'s."""
    kept = db.snapshot_hash(run_id, store.day)
    if kept is not None and kept != store.hash():
        raise ValueError(f"run {run_id!r} already has a different state for day {store.day}")
    recording = Recording(replier, db, run_id)
    keys = Keys(run_id)
    for _ in range(days):
        day = store.day
        db.save_snapshot(run_id, day, store)
        mark, errors = store.mark(), len(store.engine_errors)
        try:
            report = run_day(store, recording, keys)
        except StopRun:
            return db.load_snapshot(run_id, day)
        db.put_changes(run_id, day, store.changes[mark:])
        db.put_turns(run_id, day, report.turns)
        if report.seat:
            db.put_seat_turns(run_id, day, report.seat)
        db.put_errors(run_id, day, store.engine_errors[errors:])
    db.save_snapshot(run_id, store.day, store)
    return store


def resume(db: RunDB, run_id: str) -> Store:
    """The run's latest snapshot."""
    days = db.snapshot_days(run_id)
    if not days:
        raise KeyError(run_id)
    return db.load_snapshot(run_id, days[-1])


def replay(db: RunDB, run_id: str) -> list[str]:
    """Run every recorded day again from the earliest snapshot with only stored replies,
    and return the keys of replies stored for the latest snapshot's day, which a stopped
    run has not completed (usually none).

    Raises `ReplayMismatch` if a morning's state differs from its snapshot, a reply is
    missing or its prompt changed, or a stored reply for a completed day is never asked
    for. Writes nothing."""
    days = db.snapshot_days(run_id)
    if not days:
        raise KeyError(run_id)
    replier = Replaying(db, run_id)
    keys = Keys(run_id)
    store = db.load_snapshot(run_id, days[0])
    for i, day in enumerate(days):
        if store.day != day or store.hash() != db.snapshot_hash(run_id, day):
            raise ReplayMismatch(key=f"{run_id}|d{day}", why="state differs")
        if i < len(days) - 1:
            run_day(store, replier, keys)
    pending_day = f"d{days[-1]}"
    left = replier.unused()
    pending = [k for k in left if k.split("|")[2] == pending_day]
    unused = [k for k in left if k.split("|")[2] != pending_day]
    if unused:
        raise ReplayMismatch(key=unused[0], why="unused")
    return pending


def fork(db: RunDB, parent: str, at_day: int, new_id: str, seed: int | None = None,
         setup: Callable[[Store], None] | None = None) -> Store:
    """A new run whose first snapshot is the parent's for `at_day`, changed by `setup` first
    when one is given (an arm's launch, say)."""
    store = db.load_snapshot(parent, at_day)
    if setup is not None:
        setup(store)
    db.create_run(new_id, seed if seed is not None else db.get_run(parent)["seed"], parent, at_day)
    db.save_snapshot(new_id, at_day, store)
    return db.load_snapshot(new_id, at_day)
