"""One SQLite file holding runs, their stored replies and their morning snapshots."""

from __future__ import annotations

import dataclasses
import json
import pickle
import sqlite3

from townsim.town.replies import Reply
from townsim.town.store import Store

_SCHEMA = """
create table if not exists runs (
    id text primary key, seed integer not null, parent text, parent_day integer
);
create table if not exists replies (
    seq integer primary key autoincrement,
    run text not null, key text not null, prompt_hash text not null, reply_json text not null,
    unique (run, key)
);
create table if not exists snapshots (
    run text not null, day integer not null, hash text not null, blob blob not null,
    primary key (run, day)
);
"""


class RunDB:
    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # runs

    def create_run(self, run_id: str, seed: int, parent: str | None = None, parent_day: int | None = None) -> None:
        try:
            self._db.execute("insert into runs values (?, ?, ?, ?)", (run_id, seed, parent, parent_day))
        except sqlite3.IntegrityError:
            raise ValueError(f"run {run_id!r} already exists") from None
        self._db.commit()

    def get_run(self, run_id: str) -> dict:
        row = self._db.execute("select id, seed, parent, parent_day from runs where id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(zip(("id", "seed", "parent", "parent_day"), row))

    # replies

    def put_reply(self, run_id: str, key: str, prompt_hash: str, reply: Reply) -> None:
        """Store a reply. A key is written once."""
        try:
            self._db.execute(
                "insert into replies (run, key, prompt_hash, reply_json) values (?, ?, ?, ?)",
                (run_id, key, prompt_hash, json.dumps(dataclasses.asdict(reply), sort_keys=True)),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"run {run_id!r} already has a reply at {key!r}") from None
        self._db.commit()

    def get_reply(self, run_id: str, key: str) -> tuple[str, Reply] | None:
        row = self._db.execute(
            "select prompt_hash, reply_json from replies where run = ? and key = ?", (run_id, key)
        ).fetchone()
        if row is None:
            return None
        return row[0], Reply(**json.loads(row[1]))

    def reply_keys(self, run_id: str) -> list[str]:
        """The run's reply keys in the order they were stored."""
        return [r[0] for r in self._db.execute("select key from replies where run = ? order by seq", (run_id,))]

    # snapshots

    def save_snapshot(self, run_id: str, day: int, store: Store) -> None:
        """Keep the store as it stands this morning, replacing any snapshot for that day."""
        self._db.execute(
            "insert or replace into snapshots values (?, ?, ?, ?)",
            (run_id, day, store.hash(), pickle.dumps(store)),
        )
        self._db.commit()

    def load_snapshot(self, run_id: str, day: int) -> Store:
        """A new `Store` each time, rebuilt from the snapshot."""
        row = self._db.execute("select blob from snapshots where run = ? and day = ?", (run_id, day)).fetchone()
        if row is None:
            raise KeyError((run_id, day))
        return pickle.loads(row[0])

    def snapshot_hash(self, run_id: str, day: int) -> str | None:
        row = self._db.execute("select hash from snapshots where run = ? and day = ?", (run_id, day)).fetchone()
        return None if row is None else row[0]

    def snapshot_days(self, run_id: str) -> list[int]:
        return [r[0] for r in self._db.execute("select day from snapshots where run = ? order by day", (run_id,))]
