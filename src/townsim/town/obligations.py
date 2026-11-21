"""One schedule for everything owed (design 3.2).

Every payment due on a schedule is an `Obligation`; each instalment of it is a `Due`
row, opened once by `ensure_due`. `collect` is the only code that takes money for a
due, and it takes it in one transfer or not at all. Unpaid dues age through a ladder:
a late fee at 7 days, a notice at 14, default at 28. A defaulted due is still owed:
collect keeps trying it and a payment by hand counts toward it (design 3.2: what cannot
be paid does not vanish). A default never changes the obligation's status.

Only `stop` on an agreement breaches it: the payer withholding payment. A `breached`
obligation keeps its schedule: its dues open on their days, but they are not taken
automatically. Each goes to arrears on its
day, climbs the same ladder, and can be paid by hand.
"""

from __future__ import annotations

from typing import Iterator, Literal, NamedTuple

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

OWED = ("arrears", "defaulted")      # still owed after their day
SCHEDULED = ("active", "breached")   # obligations whose instalments still fall due


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
    ob = Obligation(store.new_id("ob", cause), kind, payer, payee, cents, first, every, last, memo, ends,
                    made_by, store.day, penalty)
    store.add("obligations", ob, cause)
    if falls_on(ob, store.day):
        ensure_due(store, ob.id, store.day, cause)
    return ob.id


def falls_on(ob: Obligation, day: int) -> bool:
    """Whether an instalment of an active or breached obligation falls on `day`."""
    if ob.status not in SCHEDULED or day < ob.first or (ob.last is not None and day > ob.last):
        return False
    return day == ob.first or (ob.every > 0 and (day - ob.first) % ob.every == 0)


def _last_instalment(ob: Obligation) -> int | None:
    if ob.every == 0:
        return ob.first
    if ob.last is None:
        return None
    return ob.first + (ob.last - ob.first) // ob.every * ob.every


def _find_due(store: Store, ob_id: str, day: int) -> Due | None:
    found = store.where("dues", obligation=ob_id, day=day)
    return found[0] if found else None


def owed_dues(store: Store, ob_id: str) -> list[Due]:
    """The obligation's dues still owed after their day (arrears and defaulted), in id order."""
    return sorted((d for s in OWED for d in store.where("dues", obligation=ob_id, status=s)), key=lambda d: id_key(d.id))


def ensure_due(store: Store, ob_id: str, day: int, cause: str) -> Due:
    """The one row for this obligation's instalment on `day`, created if absent."""
    due = _find_due(store, ob_id, day)
    if due is None:
        due = Due(store.new_id("du", cause), ob_id, day, store.get("obligations", ob_id).cents)
        store.add("dues", due, cause)
    return due


def open_dues(store: Store) -> None:
    for ob in store.all("obligations"):
        if falls_on(ob, store.day):
            ensure_due(store, ob.id, store.day, "routine:due")


def people_of(store: Store, account: str) -> list[str]:
    """A household's members; an org's owner, if it has one; `[]` for anything else."""
    if store.has("households", account):
        return list(store.get("households", account).members)
    if store.has("orgs", account) and store.get("orgs", account).owner:
        return [store.get("orgs", account).owner]
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
        store.add("notices", Notice(store.new_id("n", cause), store.day, person, text), cause)


def _shown(text: str) -> str:
    from townsim.town.statement import memo   # the one way a memo is shown (statement reads this module)
    return memo(text)


FEE_ON = "late fee on "


def _payment(ob: Obligation, day: int, cents: int) -> str:
    """A due as a notice or record names it, in code's words first: `a payment of <$x> due
    <date> (<memo>)`, or for a late fee `a late fee of <$x> on a payment due <date> (<memo>)`."""
    if ob.penalty and ob.memo.startswith(FEE_ON):
        on = ob.memo[len(FEE_ON):]
        what, sep, when = on.rpartition(" due ")
        if sep:
            return f"a late fee of {money(cents)} on a payment due {when} ({_shown(what)})"
    return f"a payment of {money(cents)} due {date_text(day)} ({_shown(ob.memo)})"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def age_arrears(store: Store) -> None:
    """Charge the late fee, send the two-weeks notice, and default, each once, on dues in arrears."""
    for due in store.where("dues", status="arrears"):
        ob = store.get("obligations", due.obligation)
        age = store.day - due.day
        when = date_text(due.day)
        if age >= FEE_AFTER_DAYS and not due.fee_charged and not ob.penalty:
            cause = f"routine:late-fee:{due.id}"
            fee = create(store, kind="bill", payer=ob.payer, payee=ob.payee, cents=LATE_FEE_CENTS,
                         first=store.day, memo=f"{FEE_ON}{ob.memo} due {when}", ends="both",
                         made_by="routine", cause=cause, penalty=True)
            store.set("dues", due.id, cause, fee_charged=True)
            _tell(store, ob.payer, f"A late fee of {money(LATE_FEE_CENTS)} has been added to "
                                   f"{_payment(ob, due.day, due.cents - due.paid)}, which has not been paid.", cause)
        if age >= NOTICE_AFTER_DAYS and not due.notice_sent:
            cause = f"routine:notice:{due.id}"
            store.set("dues", due.id, cause, notice_sent=True)
            _tell(store, ob.payer, f"{_cap(_payment(ob, due.day, due.cents - due.paid))} is now two weeks overdue.",
                  cause)
        if age >= DEFAULT_AFTER_DAYS:
            cause = f"routine:default:{due.id}"
            store.set("dues", due.id, cause, status="defaulted")
            text = f"{name_of(store, ob.payer)} defaulted on {_payment(ob, due.day, due.cents - due.paid)}."
            store.add("records", Record(store.new_id("rec", cause), store.day, "default", ob.payer, text), cause)


def _tell_not_taken(store: Store, ob: Obligation, due: Due, remainder: int, cause: str) -> None:
    text = f"{_cap(_payment(ob, due.day, remainder))} could not be taken."
    if ob.status != "breached":
        text += f" The {name_of(store, ob.payer)} account held {money(store.balance(ob.payer))}."
    _tell(store, ob.payer, text, cause)


def _collect_order(store: Store, due: Due) -> tuple:
    kind = store.get("obligations", due.obligation).kind
    return (kind != "wage", due.day, KIND_ORDER.index(kind), id_key(due.id))


def collect(store: Store) -> None:
    """Take every instalment owed up to today in full, or leave it owed. A due that goes to
    arrears for the first time is told to the payer's people, and so is one taken after it
    went to arrears. A breached obligation's dues are not taken: they go to arrears on their day.

    Wages go first so they land before anything is taken; then oldest first.
    """
    waiting = [d for status in ("due",) + OWED for d in store.where("dues", status=status) if d.day <= store.day]
    for due in sorted(waiting, key=lambda d: _collect_order(store, d)):
        ob = store.get("obligations", due.obligation)
        cause = f"routine:collect:{due.id}"
        remainder = due.cents - due.paid
        if remainder < 0:
            store.engine_errors.append(f"{due.id} has paid {due.paid} of {due.cents} cents")
        account = store.ledger.accounts[ob.payer]
        if remainder > 0 and (ob.status == "breached" or (account.balance < remainder and not account.allow_credit)):
            if due.status == "due":
                store.set("dues", due.id, cause, status="arrears")
                _tell_not_taken(store, ob, due, remainder, cause)
            continue
        if remainder > 0:
            memo = ob.memo if ob.penalty else f"{ob.memo}, due {date_text(due.day)}"
            store.transfer(ob.payer, ob.payee, remainder, memo, cause)
            store.set("dues", due.id, cause, paid=due.cents)
            if due.status in OWED:
                _tell(store, ob.payer, f"{_cap(_payment(ob, due.day, remainder))} was taken on "
                                       f"{date_text(store.day)}.", cause)
        store.set("dues", due.id, cause, status="paid")
        if ob.status == "active" and due.day == _last_instalment(ob):
            store.set("obligations", ob.id, cause, status="finished")


def upcoming(store: Store, account: str, ahead: int = 7) -> list[Upcoming]:
    """What `account` pays or is paid from today to `today + ahead`, plus all arrears,
    soonest first, each with what remains. Changes nothing."""
    obs = {o.id: o for o in sorted(store.where("obligations", payer=account) + store.where("obligations", payee=account),
                                   key=lambda o: id_key(o.id))}
    end = store.day + ahead
    found: dict[tuple[str, int], int] = {}
    for ob in obs.values():
        for due in owed_dues(store, ob.id) + store.where("dues", obligation=ob.id, status="due"):
            if due.status in OWED or due.day <= end:
                found[(ob.id, due.day)] = due.cents - due.paid
        if ob.status in SCHEDULED:
            for day in range(store.day, end + 1):
                if falls_on(ob, day) and _find_due(store, ob.id, day) is None:
                    found[(ob.id, day)] = ob.cents
    rows = []
    for (ob_id, day), cents in found.items():
        ob = obs[ob_id]
        out = ob.payer == account
        rows.append(Upcoming(day, ob_id, ob.payee if out else ob.payer, cents, ob.memo, out))
    rows.sort(key=lambda u: (u.day, KIND_ORDER.index(obs[u.obligation].kind), id_key(u.obligation)))
    return rows


def _instalment_days(ob: Obligation, today: int) -> Iterator[int]:
    """The days from `today` on which an instalment of an active or breached obligation falls, in order."""
    if ob.status not in SCHEDULED:
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
    open_days = [d.day for d in store.where("dues", obligation=ob.id, status="due")
                 if d.day >= store.day and d.paid < d.cents]
    best = min(open_days) if open_days else None
    for day in _instalment_days(ob, store.day):
        if best is not None and day >= best:
            break
        if _find_due(store, ob.id, day) is None:
            return day
    return best


def pay(store: Store, payer: str, payee: str, cents: int, memo: str, cause: str,
        ref: str | None = None, match: bool | Literal["arrears"] = True) -> list[Match]:
    """Pay `cents` from `payer` to `payee` in one transfer and count it toward what the payer
    owes the payee: arrears oldest first, then the single next instalment. With `ref`, only
    that obligation. What is left over is an ordinary payment. With `match="arrears"` it
    counts toward arrears only, never toward an instalment not yet owed. With `match=False`
    it is only the transfer: no due is touched and nothing is returned. `ref` may only be
    given with `match=True`. Changes nothing if it raises."""
    if match not in (True, False, "arrears"):
        raise ValueError(f"match must be True, False or 'arrears', got {match!r}")
    if ref is not None and match is not True:
        raise ValueError(f"ref {ref!r} names what to count toward, but match is {match!r}")
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
    obs = [o for o in store.where("obligations", payer=payer) if o.payee == payee]
    if ref is not None:
        obs = [o for o in obs if o.id == ref]
        if not obs:
            raise ValueError(f"{ref!r} is not an obligation from {payer} to {payee}")
    plan = _plan(store, obs, cents, match)
    if plan:
        memo += " (counted toward " + "; ".join(f"{ob.memo} due {date_text(day)}" for ob, day, _ in plan) + ")"
    with store.transaction():
        store.transfer(payer, payee, cents, memo, cause)
        return _count(store, plan, cause)


def _plan(store: Store, obs: list[Obligation], cents: int,
          match: bool | Literal["arrears"]) -> list[tuple[Obligation, int, int]]:
    """(obligation, day, cents counted) for `cents` paid toward `obs`: arrears oldest first,
    then, unless `match` is `arrears`, the single next instalment. Changes nothing."""
    by_id = {o.id: o for o in obs}
    plan: list[tuple[Obligation, int, int]] = []
    left = cents
    arrears = [d for ob_id in by_id for d in owed_dues(store, ob_id)]
    arrears.sort(key=lambda d: (d.day, KIND_ORDER.index(by_id[d.obligation].kind), id_key(d.obligation)))
    for due in arrears:
        take = min(left, due.cents - due.paid)
        if take > 0:
            plan.append((by_id[due.obligation], due.day, take))
            left -= take
    if left > 0 and match is True:
        nexts = [(day, KIND_ORDER.index(o.kind), id_key(o.id), o) for o in obs
                 if (day := next_instalment(store, o)) is not None]
        if nexts:
            day, _, _, ob = min(nexts, key=lambda n: n[:3])
            due = _find_due(store, ob.id, day)
            owed = ob.cents if due is None else due.cents - due.paid
            plan.append((ob, day, min(left, owed)))
    return plan


def _count(store: Store, plan: list[tuple[Obligation, int, int]], cause: str) -> list[Match]:
    """Mark each planned part as paid on its due, opening the due if need be."""
    matches = []
    for ob, day, take in plan:
        due = ensure_due(store, ob.id, day, cause)
        store.set("dues", due.id, cause, paid=due.paid + take)
        if due.paid >= due.cents:
            store.set("dues", due.id, cause, status="paid")
            if ob.status == "active" and due.day == _last_instalment(ob):
                store.set("obligations", ob.id, cause, status="finished")
        matches.append(Match(due.id, take))
    return matches


def count_toward(store: Store, ob_id: str, cents: int, cause: str) -> list[Match]:
    """Count `cents` already paid by other means toward the obligation `ob_id`, as `pay`
    would count a payment toward it: arrears oldest first, then the single next instalment,
    nothing beyond. No money moves here; the caller has moved it, in a transfer with the
    same cause. Raises ValueError, changing nothing, for an unknown obligation or cents not
    a positive int."""
    if not isinstance(ob_id, str) or not store.has("obligations", ob_id):
        raise ValueError(f"no obligation {ob_id!r}")
    if not _is_int(cents) or cents <= 0:
        raise ValueError(f"cents must be a positive int, got {cents!r}")
    plan = _plan(store, [store.get("obligations", ob_id)], cents, True)
    with store.transaction():
        return _count(store, plan, cause)


def stop(store: Store, ob_id: str, by: str, cause: str) -> str:
    """End an active obligation for its payer `by`. Dues already opened still stand.

    Returns `stopped` (the payer may end it), `left` (leaving a service) or `breach`
    (an agreement both made: it is breached and a record says so; it still falls due,
    but nothing is taken automatically). Raises ValueError,
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
            text = (f"{name_of(store, ob.payer)} stopped paying an agreement with {name_of(store, ob.payee)} "
                    f"({_shown(ob.memo)}).")
            store.add("records", Record(store.new_id("rec", cause), store.day, "breach", ob.payer, text), cause)
            return "breach"
        store.set("obligations", ob_id, cause, status="stopped")
        return "stopped" if ob.ends == "payer" else "left"


def instalment_days(ob: Obligation, today: int, n: int) -> list[int]:
    """The first `n` days from `today` on which an instalment of an active or breached
    obligation falls, in order (fewer if it ends first)."""
    out: list[int] = []
    for day in _instalment_days(ob, today):
        if len(out) >= n:
            break
        out.append(day)
    return out


def end(store: Store, ob_id: str, last_day: int, cause: str) -> None:
    """End a weekly or other repeating obligation after `last_day`: no instalment falls after
    it. Dues already opened stand. A once-only obligation, one no longer active or breached,
    or one already ending on or before `last_day` is left as it is. An obligation with
    nothing owed and no instalment left from today is finished. Raises ValueError, changing
    nothing, for an unknown id or a `last_day` before today."""
    if not isinstance(ob_id, str) or not store.has("obligations", ob_id):
        raise ValueError(f"no obligation {ob_id!r}")
    if not _is_int(last_day) or last_day < store.day:
        raise ValueError(f"last_day must be an int >= today ({store.day}), got {last_day!r}")
    ob = store.get("obligations", ob_id)
    if ob.every == 0 or ob.status not in SCHEDULED or (ob.last is not None and ob.last <= last_day):
        return
    with store.transaction():
        if last_day < ob.first:          # no instalment ever falls
            store.set("obligations", ob_id, cause, status="finished")
            return
        store.set("obligations", ob_id, cause, last=last_day)
        unpaid = [d for d in store.where("dues", obligation=ob_id) if d.status != "paid"]
        if not unpaid and not instalment_days(ob, store.day, 1):
            store.set("obligations", ob_id, cause, status="finished")
