"""The statement an account holder reads: balance, what came in and went out, what is
coming, and the balance after what goes out (design 3.2). Nothing here changes the store."""

from __future__ import annotations

import re
from bisect import bisect_left
from typing import NamedTuple

from townsim.town.clock import date_in, date_text
from townsim.town.obligations import (OWED, SCHEDULED, Upcoming, last_cents, last_instalment, money, name_of,
                                      cents_now, pending_change, upcoming)
from townsim.town.store import Store


MEMO_WORDS = 12


def cut_words(text: str, n: int | None) -> str:
    """`text` up to its `n`th word, with `…` after it if anything was left out; whitespace
    and line breaks kept as they were."""
    if n is None:
        return text
    found = list(re.finditer(r"\S+", text))
    if len(found) <= n:
        return text
    return text[:found[n - 1].end()] + "\u2026" if n > 0 else "\u2026"


MEMO_CHARS = 100
TOWARD_ITEMS = 3


class Counted(str):
    """A line code writes to count what is not shown (`(<n> more not shown)` and the like).
    The flag goes with the line, so nothing has to recognise one by how it looks."""
_COUNTED = " (counted toward "
_DUE = re.compile(r", due \w+ \d+ \w+$")


_DOUBLE_QUOTES = str.maketrans({q: "'" for q in '"\u201c\u201d\u201e\u201f\u00ab\u00bb'})


def quoted(text: str) -> str:
    """Words a person wrote, made into a memo: on one line, inside double quotation marks,
    with any double quotation mark of theirs, straight or curly, made a single one. Every memo
    a person writes is stored so, which is how what they wrote is told from what code wrote
    wherever it goes: into a payment, an obligation, a late fee on it, a notice or a record."""
    return '"' + " ".join(text.translate(_DOUBLE_QUOTES).split()) + '"'


def label(text: str) -> str:
    """A memo as a label: at most twelve words and 100 characters, on one line; a quotation
    cut short is closed."""
    text = cut_words(" ".join(text.split()), MEMO_WORDS)
    text = text if len(text) <= MEMO_CHARS else text[:MEMO_CHARS].rstrip() + "\u2026"
    return text + '"' if text.count('"') % 2 else text


def _outside(text: str, needle: str) -> list[int]:
    """Where `needle` stands in `text` outside any quotation."""
    found, at = [], text.find(needle)
    while at != -1:
        if text.count('"', 0, at) % 2 == 0:
            found.append(at)
        at = text.find(needle, at + 1)
    return found


def _split(text: str, sep: str) -> list[str]:
    parts, start = [], 0
    for at in _outside(text, sep):
        parts.append(text[start:at])
        start = at + len(sep)
    return parts + [text[start:]]


def _toward(part: str) -> str:
    at = _outside(part, " due ")
    return label(part[:at[-1]]) + part[at[-1]:] if at else label(part)


def memo(text: str) -> str:
    """A memo as shown, on one line, its whitespace collapsed: what a person wrote (stored in
    quotation marks, see `quoted`) and what code wrote around it cut to a label; what code
    wrote after it (`, due <date>`, or ` (counted toward <memo> due <date>; …)`, at most three
    of those with each memo cut the same way, then `and <n> more`) kept. Only code's own
    words outside a quotation are read as code's."""
    text = " ".join(text.split())
    at = _outside(text, _COUNTED)
    if at and text.endswith(")"):
        parts = [_toward(p) for p in _split(text[at[0] + len(_COUNTED):-1], "; ")]
        if len(parts) > TOWARD_ITEMS:
            parts = parts[:TOWARD_ITEMS] + [f"and {len(parts) - TOWARD_ITEMS} more"]
        return label(text[:at[0]]) + _COUNTED + "; ".join(parts) + ")"
    m = _DUE.search(text)
    if m and text.count('"', 0, m.start()) % 2 == 0:
        return label(text[:m.start()]) + m.group(0)
    return label(text)


AGREEMENT_LINES = 40


class Line(NamedTuple):
    """One transfer. `cents` is negative when money left the account; `cause` is the
    transfer's own."""

    day: int
    other: str
    cents: int
    memo: str
    cause: str = ""


MOVED = "moved between your own accounts"


def mover(line: Line) -> str:
    """Who moved money between their own accounts on `line`: the actor its cause names, or ""."""
    if line.memo != MOVED or not line.cause.startswith("op:"):
        return ""
    return line.cause.split(":")[1].split("/")[0]


OPENING = "outside:opening"          # where the generator's opening balances come from (`gen.OPENING`)


def other_and_memo(store: Store, line: Line, reader: str = "") -> str:
    """What a statement line says after its date and sum: the other account's name and the memo;
    for money someone else of the household moved between their own accounts, `<Name> moved
    <$x> from <account>` (or `to`) in their place."""
    if line.other == OPENING:                       # the books began here: no one paid it
        return "balance brought forward"
    who = mover(line)
    if who and reader and who != reader and store.has("people", who):
        way = "from" if line.cents > 0 else "to"
        return f"{store.get('people', who).name} moved {money(abs(line.cents))} {way} {name_of(store, line.other)}"
    return f"{name_of(store, line.other)}  {same_day(memo(line.memo), line.day)}{by_instance(store, line.cause)}"


def by_instance(store: Store, cause: str) -> str:
    """` (by <product>)` for a transfer an instance made, or one collected on an obligation an
    instance set up; else ""."""
    if not store.all("instances"):
        return ""
    who = ""
    if cause.startswith("op:"):
        who = cause.split(":")[1].split("/")[0]
    elif cause.startswith("routine:collect:"):
        due = cause.removeprefix("routine:collect:")
        if store.has("dues", due):
            who = store.get("obligations", store.get("dues", due).obligation).made_by
    if not who or not store.has("instances", who):
        return ""
    inst = store.get("instances", who)
    name = store.get("products", inst.product).name if store.has("products", inst.product) else inst.name
    return f" (by {name})"


class Statement(NamedTuple):
    """`since` and `until` are the first and last days covered; `earlier` is how many
    lines in that span were left out to keep the newest."""

    account: str
    day: int
    balance: int
    lines: list[Line]
    out: list[Upcoming]
    coming_in: list[Upcoming]
    after_out: int
    since: int
    until: int
    earlier: int


def statement(store: Store, account: str, back: int = 14, ahead: int = 7, max_lines: int = 25) -> Statement:
    lines = []
    transfers = store.ledger.transfers                  # in time order, so only the last `back` days are read
    for t in transfers[bisect_left(transfers, store.day - back, key=lambda t: t.t):]:
        if t.t <= store.day and account in (t.src, t.dst):
            if t.src == account:
                lines.append(Line(t.t, t.dst, -t.cents, t.memo, t.cause))
            else:
                lines.append(Line(t.t, t.src, t.cents, t.memo, t.cause))
    earlier = max(0, len(lines) - max_lines)
    ups = upcoming(store, account, ahead)
    out = [u for u in ups if u.out]
    balance = store.balance(account)
    return Statement(account, store.day, balance, lines[earlier:], out, [u for u in ups if not u.out],
                     balance - sum(u.cents for u in out), max(0, store.day - back), store.day + ahead, earlier)


def _overdue(store: Store, u: Upcoming) -> bool:
    return any(d.status in OWED for d in store.where("dues", obligation=u.obligation, day=u.day))


def _coming(store: Store, ups: list[Upcoming]) -> list[str]:
    rows = []
    for u in ups:
        when = f"overdue since {date_text(u.day)}" if _overdue(store, u) else date_text(u.day)
        rows.append(f"  {when}  {money(u.cents)}  {name_of(store, u.other)}  {memo(u.memo)} ({u.obligation})")
    return rows or ["  nothing"]


def same_day(text: str, day: int) -> str:
    """A memo on a line dated `day` without its `, due <day>`, which the line's date already says."""
    tail = f", due {date_text(day)}"
    return text[:-len(tail)] if text.endswith(tail) else text


def line_rows(store: Store, lines: list[Line], reader: str = "") -> list[str]:
    """The rows `statement_text` shows for `lines`, oldest first, as a view shows them."""
    return [f"  {date_text(l.day)}  {money(l.cents)}  {other_and_memo(store, l, reader)}" for l in lines]


def savings_line(store: Store, account: str) -> list[str]:
    """`Savings: <$x>` for a household with savings; nothing for any other account."""
    savings = f"savings:{account}"
    return [f"Savings: {money(store.balance(savings))}"] if store.ledger.has(savings) else []


def statement_text(store: Store, st: Statement, reader: str = "") -> str:
    rows = [f"Balance: {money(st.balance)}", *savings_line(store, st.account), f"In and out since {date_text(st.since)}:"]
    if st.earlier:
        rows.append(f"  ({st.earlier} earlier lines not shown)")
    rows += line_rows(store, st.lines, reader)
    if not st.lines and not st.earlier:
        rows.append("  nothing")
    rows.append(f"Going out by {date_text(st.until)}:")
    rows += _coming(store, st.out)
    rows.append(f"Expected in by {date_text(st.until)}:")
    rows += _coming(store, st.coming_in)
    rows.append(lowest_line(st))
    return "\n".join(rows)


def lowest(st: Statement) -> int:
    """The lowest the balance comes to by the statement's last day, taking what is due in and out
    day by day in order (on a day, what comes in first, as wages are paid first; anything
    overdue going out as today; anything overdue coming in not counted)."""
    moves = [(max(u.day, st.day), 1, -u.cents) for u in st.out]
    moves += [(u.day, 0, u.cents) for u in st.coming_in if u.day >= st.day]
    balance = low = st.balance
    for _, _, cents in sorted(moves):
        balance += cents
        low = min(low, balance)
    return low


def lowest_line(st: Statement) -> str:
    """`Lowest point before <the day after its last>: <$x>`."""
    return f"Lowest point before {date_text(st.until + 1)}: {money(lowest(st))}"


def began(store: Store, ob) -> str:
    """`since Friday 14 March 2025` for one that began before its first instalment here, else
    `first Monday 9 March`."""
    if ob.since is not None and ob.since < ob.first:
        return f"since {date_in(ob.since, store.day)}"
    return f"first {date_text(ob.first)}"


def agreements_text(store: Store, account: str) -> str:
    """Every obligation `account` pays or receives, ordered for its reader: those in force
    (active or breached) that it pays, then those in force that it receives, each by id, then
    the rest by id; at most forty lines, then a line counting the rest. Each memo is cut to a
    label as views show it; a change of amount announced and not yet come is told with its
    new sum and day; a last instalment that differs from the rest is told with its sum;
    one that finished before any instalment fell says so in place of its dates."""
    mine = [ob for ob in store.all("obligations") if account in (ob.payer, ob.payee)]
    live = [ob for ob in mine if ob.status in SCHEDULED]
    order = ([ob for ob in live if ob.payer == account] + [ob for ob in live if ob.payer != account]
             + [ob for ob in mine if ob.status not in SCHEDULED])
    rows = []
    for ob in order:
        party = f"to {name_of(store, ob.payee)}" if ob.payer == account else f"from {name_of(store, ob.payer)}"
        if ob.status == "finished" and not [d for d in store.where("dues", obligation=ob.id) if d.status != "void"]:
            how = "once" if ob.every == 0 else {1: "every day", 7: "every week", 28: "every 4 weeks"}.get(ob.every, f"every {ob.every} days")
            rows.append(f"{ob.id}: {memo(ob.memo)}, {money(ob.cents)} {how} {party}, ended before its first payment, "
                        f"{ob.status}")
            continue
        if ob.every == 0:
            rows.append(f"{ob.id}: {memo(ob.memo)}, {money(ob.cents)} once on {date_text(ob.first)} {party}, "
                        f"{ob.status}")
            continue
        how = {1: "every day", 7: "every week", 28: "every 4 weeks"}.get(ob.every, f"every {ob.every} days")
        end = last_instalment(ob)                    # the day its last instalment falls, not its raw `last`
        until = f", until {date_in(end, store.day)}" if ob.last is not None else ""
        if ob.last is not None and last_cents(ob) != ob.cents:
            until = f", the last, on {date_in(end, store.day)}, {money(last_cents(ob))}"
        coming = pending_change(store, ob)            # a rent or wage re-priced from a day not yet come
        change = f", {money(coming[0])} from {date_text(coming[1])}" if coming else ""
        rows.append(f"{ob.id}: {memo(ob.memo)}, {money(cents_now(store, ob))} {how} {party}{change}, {began(store, ob)}"
                    f"{until}, {ob.status}")
    if len(rows) > AGREEMENT_LINES:
        rows = rows[:AGREEMENT_LINES] + [f"({len(rows) - AGREEMENT_LINES} more not shown)"]
    return "\n".join(rows) or "nothing"
