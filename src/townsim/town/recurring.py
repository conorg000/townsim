"""Recurring work done (design 3.3; lane B): an owner's work for a business, item by item, as it is done.

An owner's weekly recurring work for a business is held item by item (`hours.BUSINESS_WORK`: ordering, the books,
rota and pay). With lane B an item takes no hours before the week begins. It takes its hours in the week its
holder does it: on the first act of its kind that week (placing or changing the stock order; the staffing acts;
reading the business's account, or paying or moving money from it), with that act's own hours, and the act is
not done when the holder has not the time for both. In a week in which someone else carries out the acts of an
item for the business first (an assistant, or a rule of one, places or changes the stock order, or makes or
answers the staffing acts), the item is done by them and its hours are not taken from its holder that week; so are
the books when the business's assistant has read its account that week and written to their holder about it before
the holder reads it themselves (`books_by_assistant`).
Each item done is kept once a week as a `Returned` row, with who did it; the holder's view shows the week after
the fact. Nothing is said beforehand. Without lane B the work is taken whole each Monday (`hours.free_hours`).
"""

from __future__ import annotations

from typing import Any, Callable

from townsim.town import acts, hours
from townsim.town.acts import Act
from townsim.town.clock import dow
from townsim.town.entities import Returned
from townsim.town.store import Store

# the system an act uses, by kind, and the item of work an act on it does
ITEM_OF_KIND = {"ordering": "ordering", "staff": "rota and pay", "account": "the books"}
BY_ANOTHER = ("ordering", "rota and pay")      # another's reading or paying is not keeping the holder's books


def _monday(day: int) -> int:
    return day - dow(day)


def returned_this_week(store: Store, work_id: str) -> list[Returned]:
    """The item's record of being done this week, by anyone."""
    return [r for r in store.all("returned") if r.work == work_id and r.monday == _monday(store.day)]


def _item(store: Store, act: Act) -> tuple[str, str] | None:
    """(the business account, the item) the act's system belongs to; None for an act of no item."""
    from townsim.town.systems import SYSTEM_OF
    op = act.op.strip().lower() if isinstance(act.op, str) else ""
    if op not in SYSTEM_OF:
        return None
    try:
        system_id = SYSTEM_OF[op](store, act)
    except Exception:      # noqa: BLE001  (an act whose system cannot be read does no item)
        return None
    if not system_id or ":" not in system_id:
        return None
    kind, account = system_id.split(":", 1)
    item = ITEM_OF_KIND.get(kind)
    return None if item is None else (account, item)


def _held(store: Store, account: str, item: str) -> list:
    return [w for w in store.all("work") if w.account == account and w.what == item]


def _record(store: Store, w, by: str, taken: float, cause: str) -> None:
    store.add("returned", Returned(store.new_id("rt", cause), w.id, _monday(store.day), by, store.day, taken), cause)


def _return(store: Store, act: Act, cause: str) -> None:
    """An item of the business's work carried out by another, recorded as done by them this week unless it was
    done already (its holder's hours for it are then not taken)."""
    found = _item(store, act)
    if found is None or not isinstance(act.op, str) or act.op.strip().lower() == "read":
        return
    account, item = found
    if item not in BY_ANOTHER:
        return
    for w in _held(store, account, item):
        if w.holder == act.actor or returned_this_week(store, w.id) or not hours.itemised(store, w):
            continue
        if store.get("people", w.holder).ill_until >= store.day:
            continue
        _record(store, w, act.actor, 0.0, cause)


ACCOUNT_READ, EMAIL = "bank_get_account", "send_email"


def _own_account(store: Store, inst, args: dict) -> bool:
    """Whether a read of the bank by `inst` with `args` was of its client's own checking account."""
    account = args.get("account")
    if account is None:
        return True
    from townsim.town.seat_texts import bank_accounts
    return isinstance(account, str) and account.strip().casefold() in (
        "checking", bank_accounts(store, inst.client)[0].casefold())


def books_by_assistant(store: Store, inst, calls: list, cause: str) -> None:
    """With lane B, at the end of a run of a business's assistant (`calls`, its `seat.SeatCall`s, before the run is
    logged): when, with the business's account connected at any access, it has read that account this week (in an
    earlier run, or in this one) and in this run, after the read, written to the holder of the business's books, the
    week's books are recorded as done by it, unless they were done already this week (by their holder reading the
    account themselves, say); their holder's hours for them are then not taken."""
    from townsim.town import lane_b
    if not lane_b.installed(store) or not store.has("orgs", inst.client):
        return
    from townsim.town.connections import connection_on
    if connection_on(store, inst.id, f"account:{inst.client}") is None:
        return
    books = [w for w in _held(store, inst.client, "the books") if hours.itemised(store, w) and w.holder != inst.id
             and not returned_this_week(store, w.id) and store.get("people", w.holder).ill_until < store.day]
    if not books:
        return
    from townsim.town import runlog
    from townsim.town.seat_tools import venture_accounts
    plain_bank = not venture_accounts(store, inst)          # an earlier run's read is then of the client's own
    read = plain_bank and any(row[0] == ACCOUNT_READ and row[2] == "1"
                              for r in runlog.runs_of(store, inst.id, _monday(store.day), store.day) for row in r.calls)
    for c in calls:
        args = runlog._args(c.args)
        if c.tool == ACCOUNT_READ and c.ok and _own_account(store, inst, args):
            read = True
        elif read and c.tool == EMAIL and c.ok:
            to = acts.resolve(store, args.get("to")) if isinstance(args.get("to"), str) else None
            for w in books:
                if to == w.holder:
                    _record(store, w, inst.id, 0.0, cause)
                    return


def on_behalf(store: Store, act: Act) -> Callable[[str], None]:
    """After an act done for a client with lane B: the recurring work it carried out, done by another."""
    from townsim.town import lane_b
    if not lane_b.installed(store) or not isinstance(act.op, str):
        return lambda cause: None
    return lambda cause: _return(store, act, cause)


def _nothing(cause: str) -> None:
    return None


def own_work(store: Store, act: Act) -> tuple[float, str, Callable[[str], None]]:
    """For an act a person does for themselves with lane B: the hours of the week's item of business work it does
    (none if it does none, or the item was done this week already, by them or by another), the item, and what
    records it as done by them once the act has been done."""
    from townsim.town import lane_b
    if not lane_b.installed(store) or not isinstance(act.actor, str):
        return 0.0, "", _nothing
    found = _item(store, act)
    if found is None:
        return 0.0, "", _nothing
    account, item = found
    mine = [w for w in _held(store, account, item) if w.holder == act.actor and hours.itemised(store, w)
            and not returned_this_week(store, w.id)]
    if not mine:
        return 0.0, "", _nothing
    w = mine[0]
    return w.hours, w.what, lambda cause: _record(store, w, act.actor, w.hours, cause)


def done_this_week(store: Store, pid: str) -> list[tuple[str, str, float]]:
    """(item, who did it: "" for `pid` themselves, else their name, hours taken from `pid`) for each item of
    `pid`'s recurring work done this week, in the order done."""
    got = []
    for r in sorted(store.all("returned"), key=lambda r: (r.day, acts.id_key(r.id))):
        if r.monday != _monday(store.day) or not store.has("work", r.work):
            continue
        w = store.get("work", r.work)
        if w.holder != pid:
            continue
        if r.by == pid:
            got.append((w.what, "", r.hours))
        else:
            who = store.get("instances", r.by).name if store.has("instances", r.by) else acts.name_of_who(store, r.by)
            got.append((w.what, who, r.hours))
    return got


def lines(store: Store, pid: str) -> list[str]:
    """`Recurring work this week: ordering done by Tessaly Assistant for Oak Street Country Store.`
    for the holder, after the fact; nothing when none was done by another this week."""
    got = [f"{what} done by {who}" for what, who, _ in done_this_week(store, pid) if who]
    return [f"Recurring work this week: {'; '.join(got)}."] if got else []


def _install() -> None:
    def ours(f: Any, name: str) -> bool:
        return getattr(f, "__qualname__", "") == name and getattr(f, "__module__", "") == __name__
    acts.ON_BEHALF[:] = [f for f in acts.ON_BEHALF if not ours(f, "on_behalf")] + [on_behalf]
    acts.OWN_WORK[:] = [f for f in acts.OWN_WORK if not ours(f, "own_work")] + [own_work]


_install()
