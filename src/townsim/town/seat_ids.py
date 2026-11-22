"""What the seat shows of the store's ids and dates, and the one place ids come back.

An instance never sees the store's own ids (`ob22`, `rq3`, `sub1`): each is shown as an opaque
id of the kind a bank or a product shows (`agr_7K2Q9X`, `req_M4T8Z`, `acct_R8Q2LM`), a function
of the run's seed and the store's id only, so the same thing always shows the same id and replay
is exact. Every id an instance passes back goes through `internal` before anything reads it.
Account numbers are shown masked (`••4417`), drawn the same way. Dates the engine writes without
a year are given theirs.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from townsim.town import clock
from townsim.town.store import Store

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# store prefix -> (table, shown prefix, characters)
KINDS: dict[str, tuple[str, str, int]] = {
    "ob": ("obligations", "agr", 6), "rq": ("requests", "req", 5), "sub": ("subscriptions", "acct", 6),
    "msg": ("messages", "msg", 5), "n": ("notices", "ntf", 5), "rm": ("reminders", "fu", 4),
    "tk": ("talks", "call", 4), "jb": ("jobs", "emp", 5), "rec": ("records", "rec", 5), "du": ("dues", "due", 5),
    "ap": ("appointments", "appt", 4),
}
SHOWN = {v[1]: k for k, v in KINDS.items()}
_INTERNAL = re.compile(r"\b(" + "|".join(sorted(KINDS, key=len, reverse=True)) + r")(\d+)\b")
_DISPLAY = re.compile(r"\b(" + "|".join(sorted(SHOWN, key=len, reverse=True)) + r")_([0-9A-Za-z]+)\b")
_DATE = re.compile(r"\b(" + "|".join(clock.DAYS) + r") (\d{1,2}) (" + "|".join(clock.MONTHS) + r")\b(?! \d{4})")


def _code(seed: int, key: str, n: int) -> str:
    digest = hashlib.sha256(f"seat:{seed}:{key}".encode()).digest()
    return "".join(ALPHABET[b % len(ALPHABET)] for b in digest[:n])


def display(store: Store, internal: str) -> str:
    """The id an instance is shown for the store's `internal` id (itself when it is no kind shown)."""
    m = re.fullmatch(r"([a-z]+)(\d+)", internal)
    if m is None or m.group(1) not in KINDS:
        return internal
    _, prefix, n = KINDS[m.group(1)]
    return f"{prefix}_{_code(store.seed, internal, n)}"


def mask(store: Store, account: str) -> str:
    """An account number as shown, its last four digits: `••4417`."""
    digest = hashlib.sha256(f"seat-account:{store.seed}:{account}".encode()).digest()
    return "••" + "".join(str(b % 10) for b in digest[:4])


def shown(store: Store, text: str) -> str:
    """`text` with every store id of a kind shown, that is in the store, written as shown."""
    def one(m: re.Match) -> str:
        table = KINDS[m.group(1)][0]
        return display(store, m.group(0)) if store.has(table, m.group(0)) else m.group(0)
    return _INTERNAL.sub(one, text)


def _lookup(store: Store, prefix: str, code: str) -> str | None:
    kind = SHOWN[prefix]
    table = KINDS[kind][0]
    wanted = f"{prefix}_{code.upper()}"
    found = [r.id for r in store.all(table) if display(store, r.id) == wanted]
    return found[0] if found else None


def internal(store: Store, value: Any) -> Any:
    """`value` (an argument an instance passed: text, or a list or dict of them) with every shown
    id the store holds written as the store's own. Anything else is left as it is."""
    if isinstance(value, str):
        return _DISPLAY.sub(lambda m: _lookup(store, m.group(1), m.group(2)) or m.group(0), value)
    if isinstance(value, list):
        return [internal(store, v) for v in value]
    if isinstance(value, dict):
        return {k: internal(store, v) for k, v in value.items()}
    return value


def _year_of(weekday: int, day: int, month: int, near: int) -> int | None:
    for y in (near, near + 1, near - 1):
        try:
            if date(y, month, day).weekday() == weekday:
                return y
        except ValueError:
            continue
    return None


def dated(text: str, today: int) -> str:
    """`text` with each `Monday 9 March` that has no year given the year whose such day is that
    weekday, nearest the year of `today`."""
    near = clock.on(today).year

    def one(m: re.Match) -> str:
        y = _year_of(clock.DAYS.index(m.group(1)), int(m.group(2)), clock.MONTHS.index(m.group(3)) + 1, near)
        return m.group(0) if y is None else f"{m.group(0)} {y}"
    return _DATE.sub(one, text)


def view(store: Store, text: str) -> str:
    """What the engine wrote, as the seat shows it: its ids as shown and its dates with their
    years. Lines set off with `>` are someone's own words and are left as written."""
    out = []
    for line in (text or "").split("\n"):
        out.append(line if line.lstrip().startswith(">") else dated(shown(store, line), store.day))
    return "\n".join(out)


def day(d: int) -> str:
    """A day as the seat writes it: `Mon 2026-03-09`."""
    return f"{clock.DAYS[clock.dow(d)][:3]} {clock.iso(d)}"
