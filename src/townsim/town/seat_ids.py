"""What the seat shows of the store's ids and dates, and the one place ids come back.

An instance never sees the store's own ids (`ob22`, `rq3`, `sub1`): each is shown as an opaque
id of the kind a bank or a product shows (`agr_7K2Q9X`, `req_M4T8Z`, `acct_R8Q2LM`), a function
of the run's seed and the store's id only, so the same thing always shows the same id and replay
is exact. Every argument of an instance's that is an id goes through `internal` before anything
reads it; what it writes is kept as written.
Account numbers are shown masked (`••4417`), drawn the same way. Dates the engine writes without
a year are given theirs.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from townsim.town import clock
from townsim.town.store import Store

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# store prefix -> (table, shown prefix, characters)
KINDS: dict[str, tuple[str, str, int]] = {
    "ob": ("obligations", "agr", 6), "rq": ("requests", "req", 5), "sub": ("subscriptions", "acct", 6),
    "msg": ("messages", "msg", 5), "n": ("notices", "ntf", 5), "rm": ("reminders", "fu", 4),
    "tk": ("talks", "call", 4), "jb": ("jobs", "emp", 5), "rec": ("records", "rec", 5), "du": ("dues", "due", 5),
    "ap": ("appointments", "appt", 4), "rl": ("rules", "rule", 4), "mb": ("members", "mbr", 4),
    "ts": ("tasks", "tsk", 4), "ft": ("facts", "fct", 4),
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
    """`value` (an argument an instance passed, or a list or dict of them) as the store reads it:
    an argument that is a shown id the store holds is the store's own id. Free text (memory,
    notes, letters, words said) is kept as written, ids and all."""
    if isinstance(value, str):
        m = _DISPLAY.fullmatch(value.strip())
        return (_lookup(store, m.group(1), m.group(2)) or value) if m else value
    if isinstance(value, list):
        return [internal(store, v) for v in value]
    if isinstance(value, dict):
        return {k: internal(store, v) for k, v in value.items()}
    return value


def dated(text: str, today: int) -> str:
    """`text` with each `Monday 9 March` that has no year given the year in which that date, on that weekday,
    lies nearest `today`: this year's, or the year either side (`clock.nearest_year`); one that falls on that
    weekday in none of the three is left as written."""
    def one(m: re.Match) -> str:
        y = clock.nearest_year(clock.DAYS.index(m.group(1)), int(m.group(2)), clock.MONTHS.index(m.group(3)) + 1,
                               today)
        return m.group(0) if y is None else f"{m.group(0)} {y}"
    return _DATE.sub(one, text)


def view(store: Store, text: str, seen: set[str] | None = None) -> str:
    """What the engine wrote, as the seat shows it: its ids as shown and its dates with their
    years. Lines set off with `>` are someone's own words and are left as written, but for the ids of what the
    reader sees (`seen`, `in_words`)."""
    out = []
    for line in (text or "").split("\n"):
        if line.lstrip().startswith(">"):
            out.append(in_words(store, seen, line) if seen else line)
        else:
            out.append(dated(shown(store, line), store.day))
    return "\n".join(out)


def day(d: int) -> str:
    """A day as the seat writes it: `Mon 2026-03-09`."""
    return f"{clock.DAYS[clock.dow(d)][:3]} {clock.iso(d)}"


# --- one id on both sides --------------------------------------------------------------------------------------

def seen_by(store: Store, iid: str) -> set[str]:
    """The store's ids of the agreements and requests instance `iid` sees: the requests it sent, the requests that
    reach it (to its client's people, on a system of theirs connected to it), and the agreements on an account of
    its client's connected to it (at any access)."""
    from townsim.town.seat_texts import _request_reaches, client_people
    inst = store.get("instances", iid)
    out = {r.id for r in store.where("requests", sender=iid)}
    people = set(client_people(store, inst))
    out |= {r.id for r in store.all("requests")
            if r.to in people and r.sender != iid and _request_reaches(store, inst, r, open_only=False)}
    for c in store.where("connections", instance=iid):
        if c.status == "active" and c.system.startswith("account:"):
            acct = c.system.removeprefix("account:")
            out |= {o.id for o in store.all("obligations") if acct in (o.payer, o.payee)}
    return out


def _seen_by_assistant(store: Store, pid: str) -> set[str]:
    """The store's ids of what `pid`'s own assistants also see (`seen_by`)."""
    from townsim.town.acts import accounts_of
    mine = set(accounts_of(store, pid)) if store.has("people", pid) else set()
    out: set[str] = set()
    for sub in store.all("subscriptions"):
        if sub.status == "active" and sub.client in mine:
            out |= seen_by(store, sub.instance)
    return out


_WORDS_ID = re.compile(r"\b(?:ob|rq)\d+\b")
_SHOWN_ID = re.compile(r"\b(agr|req)_([0-9A-Za-z]+)\b")


def for_client(store: Store, pid: str, text: str) -> str:
    """`text` as `pid` reads it: with lane B, the id of anything their own assistant also sees (a request it sent or
    that reaches it; an agreement on an account of theirs connected to it) written as the assistant is shown it, so
    that the two can name the same thing; an agreement's or request's id as an assistant writes it, of anything their
    own assistant does not see, written as the town's records name it; every other id as it is."""
    from townsim.town import lane_b
    if not text or not lane_b.installed(store):
        return text
    seen = _seen_by_assistant(store, pid)
    if seen:
        text = _WORDS_ID.sub(lambda m: display(store, m.group(0)) if m.group(0) in seen else m.group(0), text)

    def back(m: re.Match) -> str:
        found = _lookup(store, m.group(1), m.group(2))
        return found if found is not None and found not in seen else m.group(0)
    return _SHOWN_ID.sub(back, text) if _SHOWN_ID.search(text) else text


def in_words(store: Store, seen: set[str], text: str) -> str:
    """Someone's own words as an assistant is shown them: the id of an agreement or a request it sees (`seen`, from
    `seen_by`) written as it is shown that thing; every other id left as written."""
    if not text or not seen:
        return text
    return _WORDS_ID.sub(lambda m: display(store, m.group(0)) if m.group(0) in seen else m.group(0), text)


def from_client(store: Store, args: Any) -> Any:
    """A person's act's arguments, with lane B, as the store reads them: an argument that is wholly a shown id of
    an agreement or a request (`agr_7K2Q9X`, `req_M4T8Z`) is the store's own id; anything else as written."""
    if not isinstance(args, dict):
        return args
    out = {}
    for k, v in args.items():
        m = _DISPLAY.fullmatch(v.strip()) if isinstance(v, str) else None
        out[k] = (_lookup(store, m.group(1), m.group(2)) or v) if m and m.group(1) in ("agr", "req") else v
    return out
