"""One SQLite file holding runs, their stored replies and their morning snapshots."""

from __future__ import annotations

import copy
import pickle
import sqlite3

from townsim.town.replies import Reply, decode_reply, encode_reply
from townsim.town.store import Store

_SCHEMA = """
create table if not exists runs (
    id text primary key, seed integer not null, parent text, parent_day integer
);
create table if not exists replies (
    seq integer primary key autoincrement,
    run text not null, key text not null, slot text not null, prompt_hash text not null, reply_json text not null,
    unique (run, slot)
);
create table if not exists snapshots (
    run text not null, day integer not null, hash text not null, blob blob not null,
    primary key (run, day)
);
create table if not exists changes (
    run text not null, day integer not null, blob blob not null,
    primary key (run, day)
);
create table if not exists errors (
    run text not null, day integer not null, n integer not null, text text not null,
    primary key (run, day, n)
);
create table if not exists turns (
    run text not null, day integer not null, blob blob not null,
    primary key (run, day)
);
"""


class RunDB:
    """Every write commits, or rolls back if it fails, so no write lock is left held."""

    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path)
        with self._db:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> RunDB:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _write(self, sql: str, params: tuple) -> None:
        with self._db:
            self._db.execute(sql, params)

    # runs

    def create_run(self, run_id: str, seed: int, parent: str | None = None, parent_day: int | None = None) -> None:
        try:
            self._write("insert into runs values (?, ?, ?, ?)", (run_id, seed, parent, parent_day))
        except sqlite3.IntegrityError:
            raise ValueError(f"run {run_id!r} already exists") from None

    def get_run(self, run_id: str) -> dict:
        row = self._db.execute("select id, seed, parent, parent_day from runs where id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(zip(("id", "seed", "parent", "parent_day"), row))

    # replies

    def put_reply(self, run_id: str, key: str, slot: str, prompt_hash: str, reply: Reply) -> None:
        """Store a reply under its full key and its slot (the key without the model). A
        slot is written once."""
        try:
            self._write(
                "insert into replies (run, key, slot, prompt_hash, reply_json) values (?, ?, ?, ?, ?)",
                (run_id, key, slot, prompt_hash, encode_reply(reply)),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"run {run_id!r} already has a reply at {slot!r}") from None

    def get_reply(self, run_id: str, slot: str) -> tuple[str, str, Reply] | None:
        """The full key, prompt hash and reply stored at `slot`, or None."""
        row = self._db.execute(
            "select key, prompt_hash, reply_json from replies where run = ? and slot = ?", (run_id, slot)
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], decode_reply(row[2])

    def reply_keys(self, run_id: str) -> list[str]:
        """The run's full reply keys in the order they were stored."""
        return [k for k, _ in self.reply_entries(run_id)]

    def reply_entries(self, run_id: str) -> list[tuple[str, str]]:
        """The run's (full key, slot) pairs in the order they were stored."""
        return [(r[0], r[1]) for r in
                self._db.execute("select key, slot from replies where run = ? order by seq", (run_id,))]

    # snapshots

    def save_snapshot(self, run_id: str, day: int, store: Store) -> None:
        """Keep the store's state as it stands this morning, replacing any snapshot for that day.

        Only state is kept: the snapshot has an empty change log and no engine errors
        (history is kept by `put_changes`). The live store is not altered.
        """
        state = copy.copy(store)
        state.changes = []
        state.engine_errors = []
        self._write("insert or replace into snapshots values (?, ?, ?, ?)", (run_id, day, store.hash(), pickle.dumps(state)))

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

    # history

    def put_changes(self, run_id: str, day: int, changes: list) -> None:
        """Keep a day's change log, replacing any already kept for that day."""
        self._write("insert or replace into changes values (?, ?, ?)", (run_id, day, pickle.dumps(list(changes))))

    def changes(self, run_id: str, day: int) -> list:
        row = self._db.execute("select blob from changes where run = ? and day = ?", (run_id, day)).fetchone()
        if row is None:
            raise KeyError((run_id, day))
        return pickle.loads(row[0])

    def change_days(self, run_id: str) -> list[int]:
        return [r[0] for r in self._db.execute("select day from changes where run = ? order by day", (run_id,))]

    def put_turns(self, run_id: str, day: int, turns: list) -> None:
        """Keep a day's turns (each person's reply key and what came of their acts),
        replacing any already kept for that day."""
        self._write("insert or replace into turns values (?, ?, ?)", (run_id, day, pickle.dumps(list(turns))))

    def turns(self, run_id: str, day: int) -> list:
        row = self._db.execute("select blob from turns where run = ? and day = ?", (run_id, day)).fetchone()
        if row is None:
            raise KeyError((run_id, day))
        return pickle.loads(row[0])

    def turn_days(self, run_id: str) -> list[int]:
        return [r[0] for r in self._db.execute("select day from turns where run = ? order by day", (run_id,))]

    def put_errors(self, run_id: str, day: int, errors: list[str]) -> None:
        """Keep the engine errors a day raised, replacing any already kept for that day."""
        with self._db:
            self._db.execute("delete from errors where run = ? and day = ?", (run_id, day))
            self._db.executemany("insert into errors values (?, ?, ?, ?)",
                                 [(run_id, day, n, text) for n, text in enumerate(errors)])

    def errors(self, run_id: str) -> list[tuple[int, str]]:
        """Every engine error kept for the run, as (day, text), in order."""
        return [(r[0], r[1]) for r in
                self._db.execute("select day, text from errors where run = ? order by day, n", (run_id,))]
