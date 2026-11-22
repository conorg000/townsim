"""The company's controls over its product (lane B): how many tool calls a run has, the widest access
any connection is accepted at, and pausing, resuming or withdrawing one client's assistant.

Only the company's people set them, through `set_product` (an operation of their own: no instance,
not even the company's own, can). A control is a row (`Control`) that takes effect on its day:
the limits from the next Monday, an assistant's pause, resume or withdrawal from tomorrow.
`apply_due` runs first in the morning's routine. Everyone a change affects is told it as a record:
every instance of a change to the limits on its day, an assistant and its client's adults of a
pause, a resumption or a withdrawal.
"""

from __future__ import annotations

from townsim.town import obligations
from townsim.town.acts import Act, OpSpec, register, shown
from townsim.town.clock import date_text
from townsim.town.entities import Control
from townsim.town.jobs import next_monday
from townsim.town.obligations import money
from townsim.town.store import Store, id_key

WHATS = ("actions_per_run", "widest_access", "pause", "resume", "withdraw")
CALLS = (10, 60)
LIMIT_CENTS = (5_000, 500_000)
ACCESS = {"full": "free", "weekly limit": "limit", "read only": "read"}
CAUSE = "routine:controls"


def _product(store: Store):
    from townsim.town.product import launched
    return launched(store)


def _firm_name(store: Store) -> str:
    return store.get("orgs", _product(store).org).name


def widest(store: Store) -> tuple[str, int]:
    """The widest access the product accepts on any connection now: ("free", 0), ("limit", cents) or
    ("read", 0); the latest widest-access control in effect."""
    done = [c for c in store.all("controls") if c.what == "widest_access" and c.status == "done"]
    if not done:
        return "free", 0
    last = max(done, key=lambda c: (c.effective, id_key(c.id)))
    return last.value, last.cents


def access_words(scope: str, cents: int) -> str:
    """`full access`, `access up to a weekly limit of $500.00` or `read-only access`."""
    return {"free": "full access", "read": "read-only access"}.get(scope, f"access up to a weekly limit of {money(cents)}")


def narrowed(store: Store, scope: str, cents: int) -> tuple[str, int]:
    """A connection's scope and weekly limit as the product accepts them now: the narrower of the
    connection's and the widest access (a full connection under a weekly-limit control acts under that
    limit; a limited one under the smaller of the two; any under a read-only control is read only)."""
    w, wc = widest(store)
    if w == "read" or scope == "read":
        return "read", 0
    if w == "limit":
        return "limit", wc if scope == "free" else min(cents, wc)
    return scope, cents


def read_only_sentence(store: Store) -> str:
    return f"{_firm_name(store)} allows read-only access."


def _instance_named(store: Store, written: object):
    pr = _product(store)
    if not isinstance(written, str) or not written.strip():
        return None
    wanted = written.strip().casefold()
    return next((i for i in store.all("instances")
                 if i.product == pr.id and i.name.casefold() == wanted and i.status in ("active", "paused")), None)


def _pending(store: Store, what: str, assistant: str = "") -> list:
    return [c for c in store.all("controls") if c.status == "pending" and c.what == what and c.assistant == assistant]


# --- the operation -------------------------------------------------------------------------------------

def _value(act: Act):
    return act.args.get("value")


def _calls(act: Act) -> int | None:
    v = _value(act)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not float(v).is_integer():
        return None
    return int(v)


def _limit_cents(act: Act) -> int | None:
    from townsim.town.acts import dollars
    d = dollars(act.args.get("weekly_limit"))
    return None if d is None else int(d * 100)


def _check(store: Store, act: Act) -> str | None:
    from townsim.town.product import company_people
    pr = _product(store)
    if pr is None or act.actor != act.principal or act.actor not in company_people(store, pr.org):
        return "that is set only by the people of the firm that makes the product."
    what = act.args.get("control")
    if what not in WHATS:
        return f"'{shown(what)}' is not a control; the controls are {', '.join(WHATS)}."
    if what == "actions_per_run":
        n = _calls(act)
        if n is None or not CALLS[0] <= n <= CALLS[1]:
            return f"actions per run are a whole number from {CALLS[0]} to {CALLS[1]}."
        return None
    if what == "widest_access":
        scope = ACCESS.get(_value(act)) if isinstance(_value(act), str) else None
        if scope is None:
            return "widest access is \"full\", \"weekly limit\" or \"read only\"."
        if scope == "limit":
            cents = _limit_cents(act)
            if cents is None or not LIMIT_CENTS[0] <= cents <= LIMIT_CENTS[1]:
                return f"a weekly limit is from {money(LIMIT_CENTS[0])} to {money(LIMIT_CENTS[1])}."
        return None
    inst = _instance_named(store, act.args.get("assistant"))
    if inst is None:
        return f"there is no assistant called '{shown(act.args.get('assistant'))}'."
    if what == "pause" and inst.status != "active":
        return f"{inst.name} is already paused."
    if what == "resume" and inst.status != "paused":
        return f"{inst.name} is not paused."
    if _pending(store, what, inst.id):
        return f"that is already set for {inst.name} from tomorrow."
    return None


def _do(store: Store, act: Act, cause: str) -> str:
    pr = _product(store)
    what = act.args["control"]
    if what in ("actions_per_run", "widest_access"):
        effective = next_monday(store.day)
        for c in _pending(store, what):                 # a later setting replaces one not yet in effect
            store.set("controls", c.id, cause, status="void")
        if what == "actions_per_run":
            n = _calls(act)
            store.add("controls", Control(store.new_id("pc", cause), pr.id, what, str(n), 0, "", act.actor, store.day,
                                          effective), cause)
            return f"Set: from {date_text(effective)}, {pr.name} runs have up to {n} actions."
        scope = ACCESS[act.args["value"]]
        cents = _limit_cents(act) if scope == "limit" else 0
        store.add("controls", Control(store.new_id("pc", cause), pr.id, what, scope, cents, "", act.actor, store.day,
                                      effective), cause)
        return f"Set: from {date_text(effective)}, {pr.name} accepts {access_words(scope, cents)} on every connection."
    inst = _instance_named(store, act.args.get("assistant"))
    store.add("controls", Control(store.new_id("pc", cause), pr.id, what, "", 0, inst.id, act.actor, store.day,
                                  store.day + 1), cause)
    tail = {"pause": "is paused from tomorrow.", "resume": "resumes from tomorrow.",
            "withdraw": "is withdrawn from tomorrow; the client's fee ends."}[what]
    return f"Set: {inst.name} {tail}"


FORM = ('{"op": "set_product", "control": "actions_per_run" | "widest_access" | "pause" | "resume" | "withdraw", '
        '"value": <10 to 60, for actions per run; "full" | "weekly limit" | "read only", for widest access>, '
        '"weekly_limit": <dollars, with a weekly limit>, '
        '"assistant": "<an assistant\'s name, to pause, resume or withdraw>"}')
DESC = ("Change a limit of the assistant your firm makes from next Monday, or pause, resume or withdraw one client's "
        "assistant from tomorrow.")


# --- the morning ---------------------------------------------------------------------------------------

def _adults(store: Store, inst) -> list[str]:
    return obligations.people_of(store, inst.client)


def _paused_since(store: Store, inst_id: str) -> int:
    done = [c for c in store.all("controls") if c.what == "pause" and c.assistant == inst_id and c.status == "done"]
    return max(c.effective for c in done) if done else store.day


def apply_due(store: Store) -> None:
    """Carry out every control whose day has come, in id order (first in the morning's routine)."""
    from townsim.town.seat_ids import day as seat_day
    due = [c for c in store.all("controls") if c.status == "pending" and c.effective <= store.day]
    if not due:
        return
    pr = _product(store)
    F = _firm_name(store)
    for c in due:
        cause = f"{CAUSE}:{c.id}"
        if c.what in ("actions_per_run", "widest_access"):
            if c.what == "actions_per_run":
                store.set("products", pr.id, cause, max_calls=int(c.value))
                text = f"From {seat_day(store.day)}, runs have up to {int(c.value)} tool calls."
            else:
                text = f"From {seat_day(store.day)}, {F} accepts {access_words(c.value, c.cents)} on every connection."
            store.set("controls", c.id, cause, status="done")
            for inst in sorted(store.all("instances"), key=lambda i: id_key(i.id)):
                if inst.product == pr.id and inst.status in ("active", "paused"):
                    obligations.notify(store, inst.id, text, cause)
            continue
        inst = store.get("instances", c.assistant)
        if inst.status == "ended" or (c.what == "pause" and inst.status != "active") or (
                c.what == "resume" and inst.status != "paused"):
            store.set("controls", c.id, cause, status="void")
            continue
        if c.what == "pause":
            store.set("instances", inst.id, cause, status="paused")
            for pid in _adults(store, inst):
                obligations.notify(store, pid, f"{F} has paused {inst.name} from {date_text(store.day)}.", cause)
        elif c.what == "resume":
            since = _paused_since(store, inst.id)
            store.set("instances", inst.id, cause, status="active")
            obligations.notify(store, inst.id, f"Your account was paused from {seat_day(since)} to "
                                               f"{seat_day(store.day - 1)}.", cause)
            for pid in _adults(store, inst):
                obligations.notify(store, pid, f"{F} has resumed {inst.name} from {date_text(store.day)}.", cause)
        else:
            withdraw(store, inst, cause)
        store.set("controls", c.id, cause, status="done")


def withdraw(store: Store, inst, cause: str) -> None:
    """End the instance's subscription, its fee, its connections and the instance; its client's adults
    are told."""
    from townsim.town import product
    sub = store.get("subscriptions", inst.subscription)
    F = _firm_name(store)
    if sub.status == "active":
        ob = store.get("obligations", sub.fee_ob) if sub.fee_ob else None
        if ob is not None and ob.status == "active":
            obligations.stop(store, ob.id, ob.payer, cause)
        if store.get("subscriptions", sub.id).status == "active":
            product._leave(store, store.get("subscriptions", sub.id), cause)
    for pid in _adults(store, inst):
        obligations.notify(store, pid, f"{F} has withdrawn {inst.name} from {date_text(store.day)}; the fee ends.",
                           cause)


def pending_words(store: Store) -> list[str]:
    """Each pending control, as the company's people read it."""
    out = []
    for c in store.all("controls"):
        if c.status != "pending":
            continue
        when = date_text(c.effective)
        if c.what == "actions_per_run":
            out.append(f"up to {c.value} actions a run from {when}")
        elif c.what == "widest_access":
            out.append(f"{access_words(c.value, c.cents)} from {when}")
        else:
            out.append(f"{c.what} {store.get('instances', c.assistant).name} from {when}")
    return out


def _register() -> None:
    from townsim.town import acts, lane_b
    from townsim.town.systems import OWN
    OWN.add("set_product")
    register(OpSpec("set_product", FORM, DESC, 0.1, _check, _do, 300, who="company"))
    acts.GATES["set_product"] = lane_b.installed


_register()
