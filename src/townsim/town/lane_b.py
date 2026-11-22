"""Lane B: what Slice B adds to a place, installed on day 0 of a run that is started with it.

`installed` gates every new operation's form, every new seat tool and text, and every new step: a
store without lane B runs, reads and hashes as it did before. `install` adds the trade services
organisation whose presence is the mark; the later parts of lane B add what else they install here.
"""

from __future__ import annotations

from townsim.town.entities import Org
from townsim.town.store import Store

KIND = "trades"


def installed(store: Store) -> bool:
    """Whether lane B is installed: the trade services organisation exists."""
    return any(o.kind == KIND and o.outside for o in store.all("orgs"))


def install(store: Store, cause: str = "routine:lane-b") -> str:
    """Install lane B: the trade services organisation beyond the place, with an account, named for
    the regional office's area; the web's prepared pages (`web.install`); and the outside desks with
    each household's usual spending (`desks.install`); the investment's term; and people's time: an owner's work
for a business itemised, and caring and second jobs drawn (`_time`). Returns its id; installing again changes
nothing."""
    found = next((o for o in store.all("orgs") if o.kind == KIND and o.outside), None)
    if found is not None:
        return found.id
    from townsim.town import hours
    before = {p.id: hours.free_hours(store, p.id) for p in store.all("people")}
    region = next((o for o in store.all("orgs") if o.kind == "region"), None)
    area = region.name.removesuffix(" Regional Office").strip() if region is not None else ""
    oid = store.new_id("org", cause)
    while store.has("orgs", oid) or store.ledger.has(oid):
        oid = store.new_id("org", cause)
    store.open_account(oid, cause)
    store.add("orgs", Org(oid, f"{area} Trade Services".strip(), KIND, outside=True), cause)
    from townsim.town import desks, web
    web.install(store, cause)
    desks.install(store, cause)
    from townsim.town import investment
    investment.term(store, cause)
    _time(store, before, cause)
    return oid


def _time(store: Store, before: dict[str, float], cause: str) -> None:
    """An owner's work for a business is itemised at its sizes (`hours.BUSINESS_WORK`, `recurring`), some people
    have caring or a second job (`duties`), and everyone's week so far moves by what that changes."""
    from townsim.town import duties, hours
    for w in store.all("work"):
        size = hours.BUSINESS_WORK.get(w.what)
        if size is not None and w.hours != size:
            store.set("work", w.id, cause, hours=size)
    duties.draw(store, cause)
    for p in store.all("people"):
        moved = round(hours.free_hours(store, p.id) - before[p.id], 2)
        if moved:
            store.set("people", p.id, cause, hours_week=round(max(0.0, p.hours_week + moved), 2),
                      hours_left=round(max(0.0, p.hours_left + moved), 2))
