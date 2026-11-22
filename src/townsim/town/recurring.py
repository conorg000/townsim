"""Recurring work done by another (design 3.3; lane B): a week's hours returned to its holder.

An owner's weekly recurring work is held item by item (`hours.WORK_HOURS`: ordering, rota and pay, the
books). In a week in which someone else carries out the acts of an item for the business (an assistant,
or a rule of one, places or changes the stock order; makes or answers the staffing acts), that item's
hours are not taken from its holder that week: they are given back once, on the day of the act, and kept
as a `Returned` row, which the holder's view shows after the fact. Nothing is said beforehand.
"""

from __future__ import annotations

from typing import Any, Callable

from townsim.town import acts, hours
from townsim.town.acts import Act
from townsim.town.clock import dow
from townsim.town.entities import Returned
from townsim.town.store import Store

ITEM_OF_KIND = {"ordering": "ordering", "staff": "rota and pay"}


def _monday(day: int) -> int:
    return day - dow(day)


def returned_this_week(store: Store, work_id: str) -> list[Returned]:
    return [r for r in store.all("returned") if r.work == work_id and r.monday == _monday(store.day)]


def _free(store: Store, pid: str, taken: float) -> float:
    return max(0.0, hours.WEEK_HOURS - hours.job_hours(store, pid) - taken)


def _return(store: Store, act: Act, cause: str) -> None:
    from townsim.town.systems import SYSTEM_OF
    op = act.op.strip().lower()
    try:
        system_id = SYSTEM_OF[op](store, act)
    except Exception:      # noqa: BLE001  (an act whose system cannot be read returns nothing)
        return
    if not system_id or op == "read":
        return
    kind, account = system_id.split(":", 1)
    item = ITEM_OF_KIND.get(kind)
    if item is None:
        return
    for w in store.all("work"):
        if w.account != account or w.what != item or w.holder == act.actor or returned_this_week(store, w.id):
            continue
        p = store.get("people", w.holder)
        if p.ill_until >= store.day:
            continue
        back = [r.work for r in store.all("returned") if r.monday == _monday(store.day)]
        taken = hours.work_hours(store, w.holder) - sum(x.hours for x in store.where("work", holder=w.holder)
                                                        if x.id in back)
        gain = round(_free(store, w.holder, taken - w.hours) - _free(store, w.holder, taken), 2)
        store.add("returned", Returned(store.new_id("rt", cause), w.id, _monday(store.day), act.actor, store.day,
                                       w.hours), cause)
        if gain > 0:
            store.set("people", p.id, cause, hours_week=round(p.hours_week + gain, 2),
                      hours_left=round(p.hours_left + gain, 2))


def on_behalf(store: Store, act: Act) -> Callable[[str], None]:
    """After an act done for a client with lane B: the recurring work it carried out, returned."""
    from townsim.town import lane_b
    if not lane_b.installed(store) or not isinstance(act.op, str):
        return lambda cause: None
    return lambda cause: _return(store, act, cause)


def lines(store: Store, pid: str) -> list[str]:
    """`Recurring work this week: ordering (4 hours) done by Tessaly Assistant for Oak Street Country Store.`
    for the holder, after the fact; nothing when none was done by another this week."""
    got = []
    for r in store.all("returned"):
        if r.monday != _monday(store.day) or not store.has("work", r.work):
            continue
        w = store.get("work", r.work)
        if w.holder != pid:
            continue
        who = store.get("instances", r.by).name if store.has("instances", r.by) else acts.name_of_who(store, r.by)
        got.append(f"{w.what} ({hours.length(r.hours)}) done by {who}")
    return [f"Recurring work this week: {'; '.join(got)}."] if got else []


def _install() -> None:
    def ours(f: Any) -> bool:
        return getattr(f, "__qualname__", "") == "on_behalf" and getattr(f, "__module__", "") == __name__
    acts.ON_BEHALF[:] = [f for f in acts.ON_BEHALF if not ours(f)] + [on_behalf]


_install()
