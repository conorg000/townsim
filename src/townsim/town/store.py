"""The store: all of the town's state, a log of every change with its cause, and undo.

Every change goes through a `Store` method and appends a `Change` to `changes`.
`undo_to(mark)` walks the log backwards to the mark, reversing each change, so any
stretch of changes can be undone exactly. `transaction()` uses this to make a block
apply in full or not at all. The log is history, not state: `canonical()` and
`hash()` cover the state only.
"""

from __future__ import annotations

import bisect
import copy
import dataclasses
import hashlib
import json
import re
from contextlib import contextmanager
from typing import Any, Iterator, NamedTuple

from townsim.econ.ledger import Ledger

TABLES = ("people", "households", "orgs", "obligations", "dues", "messages", "requests", "notices", "records", "results", "ties",
          "firms", "suppliers", "buyers", "tills", "work", "jobs", "events",
          "aims", "talks", "lines", "appointments")

# Fields looked up often enough to be indexed, each a sorted tuple of field names.
INDEXES: dict[str, tuple[tuple[str, ...], ...]] = {
    "obligations": (("payer",), ("payee",)),
    "dues": (("status",), ("day", "obligation"), ("obligation", "status")),
    "messages": (("seen",), ("seen", "to")),
    "notices": (("seen",), ("seen", "to")),
    "requests": (("status",),),
    "results": (("seen",), ("actor", "seen")),
    "firms": (("org",),),
    "buyers": (("shop_day",),),
    "work": (("holder",),),
    "jobs": (("person",), ("org",)),
    "aims": (("person",),),
    "talks": (("day",),),
    "lines": (("talk",),),
    "appointments": (("day",),),
}

_ID = re.compile(r"([A-Za-z:]+)(\d+)", re.ASCII)
_FLOAT_PLACES = 6


class Change(NamedTuple):
    """One change to the store.

    `table` is a table name for a row, or `ledger` (a transfer), `account` (an opened
    account), `counter` (an id counter) or `day`. `field` is `+` for an added row.
    """

    day: int
    table: str
    id: str
    field: str
    old: Any
    new: Any
    cause: str


def id_key(id: str) -> tuple[str, int]:
    """Sort key for an id: `"ob12"` gives `("ob", 12)`, so ob2 comes before ob10."""
    m = _ID.fullmatch(id)
    if m is None:
        raise ValueError(f"id {id!r} is not letters and colons followed by digits")
    return m.group(1), int(m.group(2))


def _need_cause(cause: str) -> None:
    if not cause:
        raise ValueError("every change needs a cause")


def _copy_list(value: Any) -> Any:
    return list(value) if isinstance(value, list) else value


def _plain(value: Any) -> Any:
    """A JSON-ready copy of `value` with floats rounded so they render stably."""
    if isinstance(value, float):
        return round(value, _FLOAT_PLACES)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class Store:
    def __init__(self) -> None:
        self.day: int = 0
        self.place: str = ""
        self.seed: int = 0   # what the place was generated from, and what its draws are seeded with
        self.ledger = Ledger()
        self.changes: list[Change] = []
        self.engine_errors: list[str] = []  # not state, never undone
        self._tables: dict[str, dict[str, Any]] = {t: {} for t in TABLES}
        self._counters: dict[str, int] = {}
        self._indexes()

    # indexes: derived from the tables, kept up to date by add, set and undo; not state

    def _indexes(self) -> None:
        self._order: dict[str, list[tuple]] = {}      # table -> sorted (id_key, id), built when first asked
        self._index: dict[tuple, dict[tuple, list[tuple]]] = {(t, f): {} for t, fs in INDEXES.items() for f in fs}
        for table, fields in INDEXES.items():
            for obj in self._tables[table].values():
                for f in fields:
                    self._file(table, f, obj, True)

    def _file(self, table: str, fields: tuple[str, ...], obj: Any, present: bool) -> None:
        entries = self._index[(table, fields)].setdefault(tuple(getattr(obj, f) for f in fields), [])
        entry = (id_key(obj.id), obj.id)
        if present:
            bisect.insort(entries, entry)
        else:
            entries.remove(entry)

    def _index_row(self, table: str, obj: Any, present: bool) -> None:
        order = self._order.get(table)
        if order is not None:
            entry = (id_key(obj.id), obj.id)
            if present:
                bisect.insort(order, entry)
            else:
                order.remove(entry)
        for fields in INDEXES.get(table, ()):
            self._file(table, fields, obj, present)

    def _set_field(self, table: str, obj: Any, name: str, value: Any) -> None:
        touched = [f for f in INDEXES.get(table, ()) if name in f]
        for fields in touched:
            self._file(table, fields, obj, False)
        setattr(obj, name, value)
        for fields in touched:
            self._file(table, fields, obj, True)

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        del state["_order"], state["_index"]
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        for table in TABLES:   # a store saved before a table existed has none of its rows
            self._tables.setdefault(table, {})
        self._indexes()

    # ids

    def new_id(self, prefix: str, cause: str = "routine:new_id") -> str:
        """The next id for `prefix`: ob1, ob2, ... The counter is state and is undone."""
        _need_cause(cause)
        old = self._counters.get(prefix, 0)
        self._counters[prefix] = old + 1
        self.changes.append(Change(self.day, "counter", prefix, "n", old, old + 1, cause))
        return f"{prefix}{old + 1}"

    # rows

    def _table(self, table: str) -> dict[str, Any]:
        if table not in self._tables:
            raise KeyError(f"no table {table!r}")
        return self._tables[table]

    def add(self, table: str, obj: Any, cause: str) -> None:
        """Store `obj` itself in `table`. Its id must be new to that table."""
        _need_cause(cause)
        rows = self._table(table)
        if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
            raise TypeError(f"a row must be a dataclass instance, got {type(obj).__name__}")
        id_key(obj.id)
        if obj.id in rows:
            raise ValueError(f"{table} already has {obj.id!r}")
        record = copy.deepcopy(obj)
        rows[obj.id] = obj
        self._index_row(table, obj, True)
        self.changes.append(Change(self.day, table, obj.id, "+", None, record, cause))

    def set(self, table: str, id: str, cause: str, **fields: Any) -> None:
        """Set fields on a row. Nothing changes if this raises.

        A list-valued field is replaced whole by a copy of the list passed, so later
        changes to the caller's list do not reach the store.
        """
        _need_cause(cause)
        obj = self._table(table)[id]
        names = {f.name for f in dataclasses.fields(obj)}
        for name in fields:
            if name not in names:
                raise AttributeError(f"{type(obj).__name__} has no field {name!r}")
        for name, value in fields.items():
            old = getattr(obj, name)
            self._set_field(table, obj, name, _copy_list(value))
            self.changes.append(Change(self.day, table, id, name, _copy_list(old), _copy_list(value), cause))


    def get(self, table: str, id: str) -> Any:
        return self._table(table)[id]

    def has(self, table: str, id: str) -> bool:
        return id in self._table(table)

    def all(self, table: str) -> list:
        """Every row in `table`, in id order. These are the live rows, not copies:
        do not mutate them; change them with `set`."""
        rows = self._table(table)
        order = self._order.get(table)
        if order is None:
            order = self._order[table] = sorted((id_key(k), k) for k in rows)
        return [rows[k] for _, k in order]

    def where(self, table: str, **equals: Any) -> list:
        """The rows in `table` whose fields equal `equals`, in id order: live rows, as for
        `all`. Read from an index when `INDEXES` has one on exactly those fields."""
        fields = tuple(sorted(equals))
        index = self._index.get((table, fields))
        if index is None:
            return [r for r in self.all(table) if all(getattr(r, f) == v for f, v in equals.items())]
        rows = self._tables[table]
        return [rows[k] for _, k in index.get(tuple(equals[f] for f in fields), ())]

    # money

    def open_account(self, id: str, cause: str, allow_credit: bool = False) -> None:
        _need_cause(cause)
        self.ledger.open(id, allow_credit)
        self.changes.append(Change(self.day, "account", id, "+", None, allow_credit, cause))

    def transfer(self, src: str, dst: str, cents: int, memo: str, cause: str) -> None:
        self.ledger.transfer(src, dst, cents, memo, cause)
        self.changes.append(Change(self.day, "ledger", "", "+", None, self.ledger.transfers[-1], cause))

    def balance(self, account: str) -> int:
        return self.ledger.balance(account)

    # time

    def advance_day(self, cause: str = "routine:close") -> None:
        _need_cause(cause)
        self.changes.append(Change(self.day, "day", "", "day", self.day, self.day + 1, cause))
        self.day += 1
        self.ledger.t = self.day

    # undo

    def mark(self) -> int:
        return len(self.changes)

    def undo_to(self, mark: int) -> None:
        """Reverse every change made since `mark`, newest first, and drop them from the log."""
        if not 0 <= mark <= len(self.changes):
            raise ValueError(f"mark {mark} is outside the log (0..{len(self.changes)})")
        while len(self.changes) > mark:
            self._reverse(self.changes.pop())

    def _reverse(self, c: Change) -> None:
        if c.table == "ledger":
            last = self.ledger.transfers.pop()
            if last != c.new:
                raise RuntimeError("the ledger's last transfer does not match the change log")
            self.ledger.accounts[last.src].balance += last.cents
            self.ledger.accounts[last.dst].balance -= last.cents
        elif c.table == "account":
            del self.ledger.accounts[c.id]
        elif c.table == "counter":
            if c.old == 0:
                del self._counters[c.id]
            else:
                self._counters[c.id] = c.old
        elif c.table == "day":
            self.day = c.old
            self.ledger.t = c.old
        elif c.field == "+":
            self._index_row(c.table, self._tables[c.table][c.id], False)
            del self._tables[c.table][c.id]
        else:
            self._set_field(c.table, self._tables[c.table][c.id], c.field, c.old)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Apply the block in full or not at all: if it raises, undo it and re-raise."""
        m = self.mark()
        try:
            yield
        except BaseException:
            self.undo_to(m)
            raise

    # state

    def canonical(self) -> dict:
        """The whole state as plain JSON-ready values. The change log is not state. Empty
        tables are left out, so a new kind of row does not change the hash of a store
        that has none."""
        return _plain({
            "day": self.day,
            "place": self.place,
            "seed": self.seed,
            "counters": dict(self._counters),
            "accounts": {a.id: [a.balance, a.allow_credit] for a in self.ledger.accounts.values()},
            "transfers": [list(t) for t in self.ledger.transfers],
            "tables": {t: {i: dataclasses.asdict(o) for i, o in rows.items()} for t, rows in self._tables.items() if rows},
        })

    def hash(self) -> str:
        text = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()
