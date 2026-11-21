"""Runs kept in a `RunDB`: run (recording every reply), resume, replay exactly, fork."""

from __future__ import annotations

from townsim.town.loop import run_day
from townsim.town.replies import Keys, Recording, Replaying, Replier, ReplayMismatch, StopRun
from townsim.town.rundb import RunDB
from townsim.town.store import Store


def run(db: RunDB, run_id: str, store: Store, replier: Replier, days: int) -> Store:
    """Run `days` days from `store.day`, keeping each morning's snapshot, each day's
    changes and every reply. On `StopRun`, the day it was raised in is not kept: the
    store returned is that morning's snapshot, loaded fresh."""
    recording = Recording(replier, db, run_id)
    keys = Keys(run_id)
    for _ in range(days):
        day = store.day
        db.save_snapshot(run_id, day, store)
        mark = store.mark()
        try:
            run_day(store, recording, keys)
        except StopRun:
            return db.load_snapshot(run_id, day)
        db.put_changes(run_id, day, store.changes[mark:])
    db.save_snapshot(run_id, store.day, store)
    return store


def resume(db: RunDB, run_id: str) -> Store:
    """The run's latest snapshot."""
    days = db.snapshot_days(run_id)
    if not days:
        raise KeyError(run_id)
    return db.load_snapshot(run_id, days[-1])


def _model(db: RunDB, run_id: str) -> str:
    models = list(dict.fromkeys(k.split("|")[1] for k in db.reply_keys(run_id)))
    if len(models) > 1:
        raise ValueError(f"run {run_id!r} has replies from more than one model: {models}")
    return models[0] if models else "scripted"


def replay(db: RunDB, run_id: str) -> None:
    """Run every recorded day again from the earliest snapshot with only stored replies.

    Raises `ReplayMismatch` if a morning's state differs from its snapshot, a reply is
    missing or its prompt changed, or a stored reply is never asked for. Writes nothing."""
    days = db.snapshot_days(run_id)
    if not days:
        raise KeyError(run_id)
    replier = Replaying(db, run_id, _model(db, run_id))
    keys = Keys(run_id)
    store = db.load_snapshot(run_id, days[0])
    for i, day in enumerate(days):
        if store.day != day or store.hash() != db.snapshot_hash(run_id, day):
            raise ReplayMismatch(key=f"{run_id}|d{day}", why="state differs")
        if i < len(days) - 1:
            run_day(store, replier, keys)
    unused = replier.unused()
    if unused:
        raise ReplayMismatch(key=unused[0], why="unused")


def fork(db: RunDB, parent: str, at_day: int, new_id: str, seed: int | None = None) -> Store:
    """A new run whose first snapshot is the parent's for `at_day`."""
    store = db.load_snapshot(parent, at_day)
    db.create_run(new_id, seed if seed is not None else db.get_run(parent)["seed"], parent, at_day)
    db.save_snapshot(new_id, at_day, store)
    return db.load_snapshot(new_id, at_day)
