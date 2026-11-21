"""One schedule for everything owed (design 3.2).

Every payment due on a schedule is an `Obligation`; each instalment of it is a `Due`
row, opened once by `ensure_due`. `collect` is the only code that takes money for a
due, and it takes it in one transfer or not at all. Unpaid dues age through a ladder:
a late fee at 7 days, a notice at 14, default at 28.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple

from townsim.econ.ledger import Overdraft

from townsim.town.clock import date_text
from townsim.town.entities import Due, Notice, Obligation, Record
from townsim.town.store import Store, id_key

KIND_ORDER = ("wage", "rent", "loan", "bill", "agreement", "standing", "fee")
ENDS = ("payer", "both", "service")

LATE_FEE_CENTS = 2500
FEE_AFTER_DAYS = 7
NOTICE_AFTER_DAYS = 14
DEFAULT_AFTER_DAYS = 28


class Match(NamedTuple):
    """Part of a payment by hand counted toward one due."""

    due: str
    cents: int


class Upcoming(NamedTuple):
    day: int
    obligation: str
    other: str
    cents: int
    memo: str
    out: bool


def money(cents: int) -> str:
    """`130850` gives `$1,308.50`."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{part:02d}"


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def create(store: Store, *, kind: str, payer: str, payee: str, cents: int, first: int, every: int = 0,
           last: int | None = None, memo: str, ends: str, made_by: str, cause: str,
           penalty: bool = False) -> str:
    """Add an obligation and return its id. Raises ValueError, changing nothing, on bad arguments.

    `first` may not be before today. If an instalment falls today its due is opened here,
    so an obligation made after that day's `open_dues` is still collected today.
    """
    if kind not in KIND_ORDER:
        raise ValueError(f"kind {kind!r} is not one of {KIND_ORDER}")
    if ends not in ENDS:
        raise ValueError(f"ends {ends!r} is not one of {ENDS}")
    if not _is_int(cents) or cents <= 0:
        raise ValueError(f"cents must be a positive int, got {cents!r}")
    if not _is_int(every) or every < 0:
        raise ValueError(f"every must be an int >= 0, got {every!r}")
    if not _is_int(first) or first < store.day:
        raise ValueError(f"first must be an int >= today ({store.day}), got {first!r}")
    if last is not None and (not _is_int(last) or last < first):
        raise ValueError(f"last must be None or an int >= first, got {last!r}")
    for account in (payer, payee):
        if not store.ledger.has(account):
            raise ValueError(f"no ledger account {account!r}")
    if payer == payee:
        raise ValueError(f"payer and payee are both {payer!r}")
    ob = Obligation(store.new_id("ob"), kind, payer, payee, cents, first, every, last, memo, ends,
                    made_by, store.day, penalty)
    store.add("obligations", ob, cause)
    if falls_on(ob, store.day):
        ensure_due(store, ob.id, store.day, cause)
    return ob.id


def falls_on(ob: Obligation, day: int) -> bool:
    """Whether an instalment of an active obligation falls on `day`."""
    if ob.status != "active" or day < ob.first or (ob.last is not None and day > ob.last):
        return False
    return day == ob.first or (ob.every > 0 and (day - ob.first) % ob.every == 0)


def _last_instalment(ob: Obligation) -> int | None:
    if ob.every == 0:
        return ob.first
    if ob.last is None:
        return None
    return ob.first + (ob.last - ob.first) // ob.every * ob.every


def _find_due(store: Store, ob_id: str, day: int) -> Due | None:
    for due in store.all("dues"):
        if due.obligation == ob_id and due.day == day:
            return due
    return None


def ensure_due(store: Store, ob_id: str, day: int, cause: str) -> Due:
    """The one row for this obligation's instalment on `day`, created if absent."""
    due = _find_due(store, ob_id, day)
    if due is None:
        due = Due(store.new_id("du"), ob_id, day, store.get("obligations", ob_id).cents)
        store.add("dues", due, cause)
    return due


def open_dues(store: Store) -> None:
    for ob in store.all("obligations"):
        if falls_on(ob, store.day):
            ensure_due(store, ob.id, store.day, "routine:due")


def people_of(store: Store, account: str) -> list[str]:
    """A household's members; `[]` for anything else."""
    if store.has("households", account):
        return list(store.get("households", account).members)
    return []


def name_of(store: Store, account: str) -> str:
    """A household's or org's name; `outside:lettings` gives `Lettings`; anything else, its id."""
    for table in ("households", "orgs"):
        if store.has(table, account):
            return store.get(table, account).name
    if account.startswith("outside:"):
        rest = account.removeprefix("outside:")
        return rest[:1].upper() + rest[1:]
    return account


def _tell(store: Store, account: str, text: str, cause: str) -> None:
    for person in people_of(store, account):
        store.add("notices", Notice(store.new_id("n"), store.day, person, text), cause)


def age_arrears(store: Store) -> None:
    """Charge the late fee, send the two-weeks notice, and default, each once, on dues in arrears."""
    for due in [d for d in store.all("dues") if d.status == "arrears"]:
        ob = store.get("obligations", due.obligation)
        age = store.day - due.day
        when = date_text(due.day)
        if age >= FEE_AFTER_DAYS and not due.fee_charged and not ob.penalty:
            cause = f"routine:late-fee:{due.id}"
            fee = create(store, kind="bill", payer=ob.payer, payee=ob.payee, cents=LATE_FEE_CENTS,
                         first=store.day, memo=f"late fee on {ob.memo} due {when}", ends="both",
                         made_by="routine", cause=cause, penalty=True)
            store.set("dues", due.id, cause, fee_charged=True)
            _tell(store, ob.payer, f"A late fee of {money(LATE_FEE_CENTS)} has been added to {ob.memo}, "
                                   f"due {when}, which has not been paid.", cause)
        if age >= NOTICE_AFTER_DAYS and not due.notice_sent:
            cause = f"routine:notice:{due.id}"
            store.set("dues", due.id, cause, notice_sent=True)
            _tell(store, ob.payer, f"{ob.memo}, {money(due.cents - due.paid)}, due {when}, "
                                   f"is now two weeks overdue.", cause)
        if age >= DEFAULT_AFTER_DAYS:
            cause = f"routine:default:{due.id}"
            store.set("dues", due.id, cause, status="defaulted")
            text = f"{name_of(store, ob.payer)} defaulted on {ob.memo}, {money(due.cents - due.paid)}, due {when}."
            store.add("records", Record(store.new_id("rec"), store.day, "default", ob.payer, text), cause)
            if ob.status == "active" and due.day == _last_instalment(ob):
                store.set("obligations", ob.id, cause, status="breached")


def _collect_order(store: Store, due: Due) -> tuple:
    kind = store.get("obligations", due.obligation).kind
    return (kind != "wage", due.day, KIND_ORDER.index(kind), id_key(due.id))


def collect(store: Store) -> None:
    """Take every due or arrears instalment up to today in full, or mark it arrears.

    Wages go first so they land before anything is taken; then oldest first.
    """
    waiting = [d for d in store.all("dues") if d.status in ("due", "arrears") and d.day <= store.day]
    for due in sorted(waiting, key=lambda d: _collect_order(store, d)):
        ob = store.get("obligations", due.obligation)
        cause = f"routine:collect:{due.id}"
        remainder = due.cents - due.paid
        if remainder < 0:
            store.engine_errors.append(f"{due.id} has paid {due.paid} of {due.cents} cents")
        account = store.ledger.accounts[ob.payer]
        if remainder > 0 and (account.balance < remainder and not account.allow_credit):
            if due.status != "arrears":
                store.set("dues", due.id, cause, status="arrears")
            continue
        if remainder > 0:
            memo = ob.memo if ob.penalty else f"{ob.memo}, due {date_text(due.day)}"
            store.transfer(ob.payer, ob.payee, remainder, memo, cause)
            store.set("dues", due.id, cause, paid=due.cents)
        store.set("dues", due.id, cause, status="paid")
        if ob.status == "active" and due.day == _last_instalment(ob):
            store.set("obligations", ob.id, cause, status="finished")


def upcoming(store: Store, account: str, ahead: int = 7) -> list[Upcoming]:
    """What `account` pays or is paid from today to `today + ahead`, plus all arrears,
    soonest first, each with what remains. Changes nothing."""
    obs = {o.id: o for o in store.all("obligations") if account in (o.payer, o.payee)}
    end = store.day + ahead
    found: dict[tuple[str, int], int] = {}
    opened: set[tuple[str, int]] = set()
    for due in store.all("dues"):
        if due.obligation not in obs:
            continue
        opened.add((due.obligation, due.day))
        if due.status == "arrears" or (due.status == "due" and due.day <= end):
            found[(due.obligation, due.day)] = due.cents - due.paid
    for ob in obs.values():
        for day in range(store.day, end + 1):
            if falls_on(ob, day) and (ob.id, day) not in opened:
                found[(ob.id, day)] = ob.cents
    rows = []
    for (ob_id, day), cents in found.items():
        ob = obs[ob_id]
        out = ob.payer == account
        rows.append(Upcoming(day, ob_id, ob.payee if out else ob.payer, cents, ob.memo, out))
    rows.sort(key=lambda u: (u.day, KIND_ORDER.index(obs[u.obligation].kind), id_key(u.obligation)))
    return rows


def _instalment_days(ob: Obligation, today: int) -> Iterator[int]:
    """The days from `today` on which an instalment of an active obligation falls, in order."""
    if ob.status != "active":
        return
    if ob.every == 0:
        if ob.first >= today:
            yield ob.first
        return
    day = ob.first if today <= ob.first else ob.first + -(-(today - ob.first) // ob.every) * ob.every
    while ob.last is None or day <= ob.last:
        yield day
        day += ob.every


def next_instalment(store: Store, ob: Obligation) -> int | None:
    """The earliest instalment of `ob` from today on not yet fully paid: an open due, or
    else the first day it falls with no due opened yet."""
    open_days = [d.day for d in store.all("dues")
                 if d.obligation == ob.id and d.status == "due" and d.day >= store.day and d.paid < d.cents]
    best = min(open_days) if open_days else None
    for day in _instalment_days(ob, store.day):
        if best is not None and day >= best:
            break
        if _find_due(store, ob.id, day) is None:
            return day
    return best


def pay(store: Store, payer: str, payee: str, cents: int, memo: str, cause: str,
        ref: str | None = None, match: bool = True) -> list[Match]:
    """Pay `cents` from `payer` to `payee` in one transfer and count it toward what the payer
    owes the payee: arrears oldest first, then the single next instalment. With `ref`, only
    that obligation. What is left over is an ordinary payment. With `match=False` it is only
    the transfer: no due is touched and nothing is returned. Changes nothing if it raises."""
    if not _is_int(cents) or cents <= 0:
        raise ValueError(f"cents must be a positive int, got {cents!r}")
    for account in (payer, payee):
        if not store.ledger.has(account):
            raise ValueError(f"no ledger account {account!r}")
    if payer == payee:
        raise ValueError(f"payer and payee are both {payer!r}")
    account = store.ledger.accounts[payer]
    if account.balance < cents and not account.allow_credit:
        raise Overdraft(f"{payer} has {account.balance}, cannot pay {cents} to {payee} ({memo})")
    if not match:
        store.transfer(payer, payee, cents, memo, cause)
        return []
    obs = [o for o in store.all("obligations") if o.payer == payer and o.payee == payee]
    if ref is not None:
        obs = [o for o in obs if o.id == ref]
        if not obs:
            raise ValueError(f"{ref!r} is not an obligation from {payer} to {payee}")
    by_id = {o.id: o for o in obs}

    plan: list[tuple[Obligation, int, int]] = []  # (obligation, day, cents counted)
    left = cents
    arrears = [d for d in store.all("dues") if d.obligation in by_id and d.status == "arrears"]
    arrears.sort(key=lambda d: (d.day, KIND_ORDER.index(by_id[d.obligation].kind), id_key(d.obligation)))
    for due in arrears:
        take = min(left, due.cents - due.paid)
        if take > 0:
            plan.append((by_id[due.obligation], due.day, take))
            left -= take
    if left > 0:
        nexts = [(day, KIND_ORDER.index(o.kind), id_key(o.id), o) for o in obs
                 if (day := next_instalment(store, o)) is not None]
        if nexts:
            day, _, _, ob = min(nexts, key=lambda n: n[:3])
            due = _find_due(store, ob.id, day)
            owed = ob.cents if due is None else due.cents - due.paid
            plan.append((ob, day, min(left, owed)))

    if plan:
        memo += " (counted toward " + "; ".join(f"{ob.memo} due {date_text(day)}" for ob, day, _ in plan) + ")"
    matches = []
    with store.transaction():
        store.transfer(payer, payee, cents, memo, cause)
        for ob, day, take in plan:
            due = ensure_due(store, ob.id, day, cause)
            store.set("dues", due.id, cause, paid=due.paid + take)
            if due.paid >= due.cents:
                store.set("dues", due.id, cause, status="paid")
                if ob.status == "active" and due.day == _last_instalment(ob):
                    store.set("obligations", ob.id, cause, status="finished")
            matches.append(Match(due.id, take))
    return matches


def stop(store: Store, ob_id: str, by: str, cause: str) -> str:
    """End an active obligation for its payer `by`. Dues already opened still stand.

    Returns `stopped` (the payer may end it), `left` (leaving a service) or `breach`
    (an agreement both made: it is breached and a record says so). Raises ValueError,
    changing nothing, if `by` is not the payer or the obligation is not active.
    """
    if not store.has("obligations", ob_id):
        raise ValueError(f"no obligation {ob_id!r}")
    ob = store.get("obligations", ob_id)
    if by != ob.payer:
        raise ValueError(f"{by!r} does not pay {ob_id}")
    if ob.status != "active":
        raise ValueError(f"{ob_id} is {ob.status}, not active")
    with store.transaction():
        if ob.ends == "both":
            store.set("obligations", ob_id, cause, status="breached")
            text = f"{name_of(store, ob.payer)} stopped paying {ob.memo}, an agreement with {name_of(store, ob.payee)}."
            store.add("records", Record(store.new_id("rec"), store.day, "breach", ob.payer, text), cause)
            return "breach"
        store.set("obligations", ob_id, cause, status="stopped")
        return "stopped" if ob.ends == "payer" else "left"
