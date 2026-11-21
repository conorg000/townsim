"""The statement an account holder reads: balance, what came in and went out, what is
coming, and the balance after what goes out (design 3.2). Nothing here changes the store."""

from __future__ import annotations

from bisect import bisect_left
from typing import NamedTuple

from townsim.town.clock import date_text
from townsim.town.obligations import OWED, Upcoming, money, name_of, upcoming
from townsim.town.store import Store


class Line(NamedTuple):
    """One transfer. `cents` is negative when money left the account."""

    day: int
    other: str
    cents: int
    memo: str


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
                lines.append(Line(t.t, t.dst, -t.cents, t.memo))
            else:
                lines.append(Line(t.t, t.src, t.cents, t.memo))
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
        rows.append(f"  {when}  {money(u.cents)}  {name_of(store, u.other)}  {u.memo} ({u.obligation})")
    return rows or ["  nothing"]


def line_rows(store: Store, lines: list[Line]) -> list[str]:
    """The rows `statement_text` shows for `lines`, oldest first."""
    return [f"  {date_text(l.day)}  {money(l.cents)}  {name_of(store, l.other)}  {l.memo}" for l in lines]


def statement_text(store: Store, st: Statement) -> str:
    rows = [f"Balance: {money(st.balance)}", f"In and out since {date_text(st.since)}:"]
    if st.earlier:
        rows.append(f"  ({st.earlier} earlier lines not shown)")
    rows += line_rows(store, st.lines)
    if not st.lines and not st.earlier:
        rows.append("  nothing")
    rows.append(f"Going out by {date_text(st.until)}:")
    rows += _coming(store, st.out)
    rows.append(f"Expected in by {date_text(st.until)}:")
    rows += _coming(store, st.coming_in)
    rows.append(f"After what goes out: {money(st.after_out)}")
    return "\n".join(rows)


def agreements_text(store: Store, account: str) -> str:
    """Every obligation `account` pays or receives, any status, in id order."""
    rows = []
    for ob in store.all("obligations"):
        if account not in (ob.payer, ob.payee):
            continue
        party = f"to {name_of(store, ob.payee)}" if ob.payer == account else f"from {name_of(store, ob.payer)}"
        if ob.every == 0:
            rows.append(f"{ob.id}: {ob.memo}, {money(ob.cents)} once on {date_text(ob.first)} {party}, {ob.status}")
            continue
        how = {1: "every day", 7: "every week"}.get(ob.every, f"every {ob.every} days")
        until = f", until {date_text(ob.last)}" if ob.last is not None else ""
        rows.append(f"{ob.id}: {ob.memo}, {money(ob.cents)} {how} {party}, first {date_text(ob.first)}{until}, {ob.status}")
    return "\n".join(rows) or "nothing"
