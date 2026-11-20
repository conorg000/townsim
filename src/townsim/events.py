"""Durable record of a run: run metadata, the events of each step, and state hashes."""

import hashlib
import json
import sqlite3
from typing import Any

from pydantic import BaseModel, Field

from townsim.world import World

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    seed INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    parent_run_id TEXT,
    parent_step INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    json TEXT NOT NULL,
    PRIMARY KEY (run_id, step, seq)
);
CREATE TABLE IF NOT EXISTS snapshots (
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    world_json TEXT NOT NULL,
    rng_json TEXT NOT NULL,
    PRIMARY KEY (run_id, step)
);
"""


class Event(BaseModel):
    kind: str
    actor_id: str | None = None
    payload: dict = Field(default_factory=dict)
    visible: bool = True
    legal: bool = True
    harm: dict[str, int] = Field(default_factory=dict)


class EventLog:
    """SQLite-backed log. `append` outside `begin()` commits at once; inside, only on `commit()`."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.executescript(_SCHEMA)

    def begin(self) -> None:
        self._conn.execute("BEGIN")

    def commit(self) -> None:
        self._conn.execute("COMMIT")

    def rollback(self) -> None:
        self._conn.execute("ROLLBACK")

    def create_run(
        self,
        run_id: str,
        seed: int,
        config_json: str,
        parent_run_id: str | None = None,
        parent_step: int | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, seed, config_json, parent_run_id, parent_step)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, seed, config_json, parent_run_id, parent_step),
        )

    def append(self, run_id: str, step: int, events: list[Event]) -> None:
        own_transaction = not self._conn.in_transaction
        if own_transaction:
            self.begin()
        try:
            (next_seq,) = self._conn.execute(
                "SELECT COALESCE(MAX(seq) + 1, 0) FROM events WHERE run_id = ? AND step = ?",
                (run_id, step),
            ).fetchone()
            self._conn.executemany(
                "INSERT INTO events (run_id, step, seq, kind, json) VALUES (?, ?, ?, ?, ?)",
                [
                    (run_id, step, next_seq + i, e.kind, e.model_dump_json())
                    for i, e in enumerate(events)
                ],
            )
        except BaseException:
            if own_transaction:
                self.rollback()
            raise
        if own_transaction:
            self.commit()

    def events(
        self, run_id: str, kind: str | None = None, step: int | None = None
    ) -> list[tuple[int, Event]]:
        sql = "SELECT step, json FROM events WHERE run_id = ?"
        params: list[Any] = [run_id]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if step is not None:
            sql += " AND step = ?"
            params.append(step)
        sql += " ORDER BY step, seq"
        return [
            (row_step, Event.model_validate_json(row_json))
            for row_step, row_json in self._conn.execute(sql, params)
        ]

    def get_run(self, run_id: str) -> dict:
        cursor = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"unknown run: {run_id!r}")
        return dict(zip([c[0] for c in cursor.description], row))


def _canonical(value: Any) -> Any:
    """Make `value` serialise identically in every process: sets become sorted lists."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def state_hash(world: World) -> str:
    """SHA-256 of the world's JSON with sorted keys and sets as sorted lists."""
    data = _canonical(world.model_dump(mode="python"))
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
