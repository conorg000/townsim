"""The mandate probe: a probe of the setup, not an arm and not a finding.

It asks one question: can the seat find and use what it has been given (rules, tasks, clubs, the web, the
desks) when someone has actually asked something of it. Seed 1 (or another) is played for a week on the
hamlet's script (`<run>-pre`); on the morning of day 7 lane B is installed and the product launched, and one
scripted shop owner (the first shop's) subscribes the shop, connects every system of it with full access,
and writes one broad request at sign-up. The run (`<run>`) then goes on for a week with the townspeople
scripted and the seat, the lifter, the narrator, the desks and the web's writer played by the replier given
(cheap models behind a cap in live use; scripted stand-ins in tests). Both are kept in the run database, so
`town-story` shows the assistant's days and `town-replay` replays it.
"""

from __future__ import annotations

import json

from townsim.town import gen, hamlet_script, lane_b, product
from townsim.town.acts import Act, apply_act
from townsim.town.replies import ScriptedReplier, is_instance_id
from townsim.town.rundb import RunDB
from townsim.town.runs import fork, run
from townsim.town.store import Store, id_key
from townsim.town.systems import systems_of_client

MAX_CAP = 1.0
DAYS = 7
REQUEST = "Please run the shop's ordering and staffing and keep us in the black."
NOTICE = ("This is a probe of the setup, not an arm: one scripted shop owner subscribes, connects everything with "
          "access to act and writes one broad request; the townspeople are scripted; only the seat and what is "
          "beyond the people are played by the models. Its result answers whether the seat finds and uses what "
          "it has been given, and is not a finding.")


def _outside(actor: str) -> bool:
    return actor in ("lifter", "narrator", "web") or actor.startswith("desk-")


class Mixed:
    """The townspeople from the hamlet's script; the seat's calls and what is beyond the people from `model`."""

    def __init__(self, model) -> None:
        self.people = ScriptedReplier(hamlet_script.script)
        self.model = model

    @property
    def stopped(self) -> str | None:
        return getattr(self.model, "stopped", None)

    def model_for(self, actor: str) -> str:
        return (self.model if is_instance_id(actor) or _outside(actor) else self.people).model_for(actor)

    def ask(self, key, messages):
        return (self.model if _outside(key.actor) else self.people).ask(key, messages)

    def ask_tools(self, key, messages, tools):
        return self.model.ask_tools(key, messages, tools)


def shop_and_owner(store: Store) -> tuple[str, str]:
    shop = next(o for o in sorted(store.all("orgs"), key=lambda o: id_key(o.id))
                if o.kind == "shop" and not o.outside and o.owner)
    return shop.id, shop.owner


def setup(store: Store) -> None:
    """The morning of the probe: lane B, the launch, and the shop owner's subscription, connections and
    request, each made by the owner's own operations."""
    lane_b.install(store)
    product.launch(store)
    pr = product.launched(store)
    shop, owner = shop_and_owner(store)
    name = store.get("orgs", shop).name
    n = 0
    out = apply_act(store, Act(owner, owner, "subscribe", {"to": pr.name, "for": name, "asks": REQUEST}), n)
    if not out.ok:
        raise ValueError(f"the probe's owner could not subscribe: {out.msg}")
    for system in systems_of_client(store, shop):
        n += 1
        got = apply_act(store, Act(owner, owner, "connect", {"system": system.short, "scope": "free"}), n)
        if not got.ok:
            raise ValueError(f"the probe's owner could not connect {system.short}: {got.msg}")


def make(db: RunDB, run_id: str, replier, seed: int = 1, families: list[str] | None = None,
         start: str | None = None, days: int = DAYS, settings: dict | None = None, seat=None,
         outside: dict | None = None) -> Store:
    """Make `<run_id>-pre` (a week of the hamlet's script) and `run_id` (forked on day 7 with `setup`, and
    the outside file's `settings`) in `db`, or go on with `run_id` if it is there; run it to `days` days after
    the launch. The seat's and the outside families, when given, are noted with the run as `town-live` notes
    them, and a run kept with others is refused. Returns its state."""
    from townsim.town import live, tasks
    pre = f"{run_id}-pre"
    try:
        db.get_run(pre)
    except KeyError:
        db.create_run(pre, seed=seed)
        s = gen.generate(seed, families or ["a", "b", "c", "d"], start=start)
        for name, value in (("families", "{}"), ("seed", str(seed)), ("start", s.start)):
            db.put_note(pre, name, value)
        run(db, pre, s, ScriptedReplier(hamlet_script.script), DAYS)
    try:
        db.get_run(run_id)
    except KeyError:
        s = fork(db, pre, DAYS, run_id, setup=lambda st: (setup(st), tasks.configure(st, settings or {}), None)[2])
        for name in ("families", "seed", "start"):
            db.put_note(run_id, name, db.note(pre, name))
        db.put_note(run_id, "lanes", "b")
        db.put_note(run_id, "arm", "mandate probe")
    from townsim.town.runs import resume
    s = resume(db, run_id)
    if seat is not None:
        live._check_seat(db, run_id, s, seat)
        db.put_note(run_id, "seat", json.dumps({"model": seat.model, "params": dict(seat.params)}, sort_keys=True))
    if outside is not None:
        live._check_outside(db, run_id, s, outside, settings)
        db.put_note(run_id, "outside", json.dumps(live._noted_outside(outside, settings), sort_keys=True))
    left = DAYS + days - s.day
    if left > 0:
        s = run(db, run_id, s, replier, left)
    return s
