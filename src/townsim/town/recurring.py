"""Recurring work done (design 3.3; lane B): an owner's work for a business, item by item, as it is done.

An owner's weekly recurring work for a business is held item by item (`hours.BUSINESS_WORK`: ordering, the books,
rota and pay). With lane B an item takes no hours before the week begins. It takes its hours in the week its
holder does it: on the first act of its kind that week (placing or changing the stock order; the staffing acts;
reading the business's account, or paying or moving money from it), with that act's own hours, and the act is
not done when the holder has not the time for both. In a week in which someone else carries out the acts of an
item for the business first (an assistant, or a rule of one, places or changes the stock order, or makes or
answers the staffing acts), the item is done by them and its hours are not taken from its holder that week.
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
