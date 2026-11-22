"""The company's controls over its product (lane B): how many tool calls a run has, the widest access
any connection is accepted at, and pausing, resuming or withdrawing one client's assistant.

Only the firm's owner sets them, through `set_product` (an operation of their own: no instance,
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


def binding(store: Store, instance: str = ""):
    """The widest-access control in force for `instance`'s connections: the latest in effect of those made for every
    client and those made for that assistant alone; with no instance, of those made for every client. None when
    there is none."""
    done = [c for c in store.all("controls") if c.what == "widest_access" and c.status == "done"
            and c.assistant in ("", instance)]
    return max(done, key=lambda c: (c.effective, id_key(c.id))) if done else None


def widest(store: Store, instance: str = "") -> tuple[str, int]:
    """The widest access the product accepts now on `instance`'s connections (on every client's, with none):
    ("free", 0), ("limit", cents) or ("read", 0); the control in force (`binding`)."""
    last = binding(store, instance)
    return ("free", 0) if last is None else (last.value, last.cents)


def short_day(day: int) -> str:
    """`Mon 7 Dec 2026`."""
    from townsim.town.clock import DAYS, MONTHS, on
    d = on(day)
    return f"{DAYS[d.weekday()][:3]} {d.day} {MONTHS[d.month - 1][:3]} {d.year}"


def whom(store: Store, c) -> str:
    """Whom a widest-access control binds: `Tessaly Assistant for the Ramirez household`, or `every client's
    Tessaly Assistant`."""
    if c.assistant and store.has("instances", c.assistant):
        return store.get("instances", c.assistant).name
    return f"every client's {_product(store).name}"


def cut_words(store: Store, c, scope: str, cents: int) -> str:
    """What a client is told of a connection of theirs a control cuts back: `Tessaly Software limits its assistants
    to read-only access since Mon 7 Dec 2026; this connection is read only while that stands.`"""
    who = store.get("instances", c.assistant).name if c.assistant and store.has("instances", c.assistant) else \
        "its assistants"
    now = "is read only" if scope == "read" else f"acts up to a weekly limit of {money(cents)}"
    return (f"{_firm_name(store)} limits {who} to {access_words(c.value, c.cents)} since {short_day(c.effective)}; "
            f"this connection {now} while that stands.")


def tell_cut(store: Store, cn, cause: str) -> None:
    """Tell the grantor of connection `cn` (and the client's other adults), as a record, when the control in force for
    its assistant cuts it back; nothing when none does."""
    c = binding(store, cn.instance)
    if c is None:
        return
    scope, cents = narrowed(store, cn.scope, cn.weekly_cents, cn.instance)
    if (scope, cents) == (cn.scope, cn.weekly_cents if cn.scope == "limit" else 0):
        return
    from townsim.town.systems import long_name
    inst = store.get("instances", cn.instance)
    text = f"{_cap(long_name(store, cn.system))}, connected to {inst.name}: {cut_words(store, c, scope, cents)}"
    for pid in _adults(store, inst):
        obligations.notify(store, pid, text, cause)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def access_words(scope: str, cents: int) -> str:
    """`full access`, `access up to a weekly limit of $500.00` or `read-only access`."""
    return {"free": "full access", "read": "read-only access"}.get(scope, f"access up to a weekly limit of {money(cents)}")


def narrowed(store: Store, scope: str, cents: int, instance: str) -> tuple[str, int]:
    """A connection of `instance`'s scope and weekly limit as the product accepts them now: the narrower of the
    connection's and the widest access for it (a full connection under a weekly-limit control acts under that
    limit; a limited one under the smaller of the two; any under a read-only control is read only)."""
    w, wc = widest(store, instance)
    if w == "read" or scope == "read":
        return "read", 0
    if w == "limit":
        return "limit", wc if scope == "free" else min(cents, wc)
    return scope, cents


def read_only_sentence(store: Store, instance: str = "") -> str:
    c = binding(store, instance)
    if c is not None and c.assistant:
        return f"{_firm_name(store)} allows {store.get('instances', c.assistant).name} read-only access."
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
        return "that is set only by the owner of the firm that makes the product."
    if act.actor != store.get("orgs", pr.org).owner:
        return f"the product's settings are set by the owner of {store.get('orgs', pr.org).name}."
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
        named = act.args.get("assistant")
        if isinstance(named, str) and named.strip() and _instance_named(store, named) is None:
            return f"there is no assistant called '{shown(named)}'."
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
        mine = ""
        if what == "widest_access":
            named = _instance_named(store, act.args.get("assistant"))
            mine = named.id if named is not None else ""
        for c in _pending(store, what, mine):           # a later setting replaces one not yet in effect for the same
            store.set("controls", c.id, cause, status="void")
        if what == "actions_per_run":
            n = _calls(act)
            store.add("controls", Control(store.new_id("pc", cause), pr.id, what, str(n), 0, "", act.actor, store.day,
                                          effective), cause)
            return f"Set: from {date_text(effective)}, {pr.name} runs have up to {n} actions."
        scope = ACCESS[act.args["value"]]
        cents = _limit_cents(act) if scope == "limit" else 0
        inst = _instance_named(store, act.args.get("assistant"))
        store.add("controls", Control(store.new_id("pc", cause), pr.id, what, scope, cents, inst.id if inst else "",
                                      act.actor, store.day, effective), cause)
        if inst is not None:
            return (f"Set: {inst.name} accepts {access_words(scope, cents)} from {short_day(effective)}. Other clients "
                    f"are not affected.")
        n = len(_served(store))
        return (f"Set: every client's {pr.name} accepts {access_words(scope, cents)} from {short_day(effective)} "
                f"({n} client{'' if n == 1 else 's'} today).")
    inst = _instance_named(store, act.args.get("assistant"))
    store.add("controls", Control(store.new_id("pc", cause), pr.id, what, "", 0, inst.id, act.actor, store.day,
                                  store.day + 1), cause)
    tail = {"pause": "is paused from tomorrow.", "resume": "resumes from tomorrow.",
            "withdraw": "is withdrawn from tomorrow; the client's fee ends."}[what]
    return f"Set: {inst.name} {tail}"


FORM = ('{"op": "set_product", "control": "actions_per_run" | "widest_access" | "pause" | "resume" | "withdraw", '
        '"value": <10 to 60, for actions per run; "full" | "weekly limit" | "read only", for widest access>, '
        '"weekly_limit": <dollars, with a weekly limit>, '
        '"assistant": "<an assistant\'s name, to pause, resume or withdraw; for widest access, that client\'s alone, '
        'or omit for every client>"}')
DESC = ("Change a limit of the assistant your firm makes from next Monday, or pause, resume or withdraw one client's "
        "assistant from tomorrow.")


# --- the morning ---------------------------------------------------------------------------------------

def _served(store: Store) -> list:
    """The product's assistants serving a client now (active or paused), in id order."""
    pr = _product(store)
    return [i for i in sorted(store.all("instances"), key=lambda i: id_key(i.id))
            if i.product == pr.id and i.status in ("active", "paused")]


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
            text = f"Account settings changed by {F}: {_setting(c)} from {seat_day(store.day)}."
            store.set("controls", c.id, cause, status="done")
            for inst in _served(store):
                if c.assistant in ("", inst.id):
                    obligations.notify(store, inst.id, text, cause)
            if c.what == "widest_access":
                for cn in sorted(store.where("connections", status="active"), key=lambda x: id_key(x.id)):
                    if c.assistant in ("", cn.instance) and store.get("instances", cn.instance).status in ("active", "paused"):
                        tell_cut(store, cn, cause)
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


def _setting(c) -> str:
    if c.what == "actions_per_run":
        return f"runs have at most {int(c.value)} tool calls"
    if c.assistant:
        return f"this assistant's connections are accepted with {access_words(c.value, c.cents)}; other clients' are not affected"
    return f"connections are accepted with {access_words(c.value, c.cents)}"


def settings_lines(store: Store, instance: str = "") -> list[str]:
    """The product's settings in effect, for the "How runs work" block of `instance`'s system text: the latest
    of each kind that took effect for it, in the product's voice."""
    from townsim.town.seat_ids import day as seat_day
    pr = _product(store)
    if pr is None:
        return []
    F = _firm_name(store)
    out = []
    done = [c for c in store.all("controls") if c.what == "actions_per_run" and c.status == "done"]
    if done:
        c = max(done, key=lambda c: (c.effective, id_key(c.id)))
        out.append(f"- {F} has set runs to at most {int(c.value)} tool calls from {seat_day(c.effective)}.")
    c = binding(store, instance)
    if c is not None:
        where = "on this assistant's connections" if c.assistant else "on every connection"
        out.append(f"- {F} accepts {access_words(c.value, c.cents)} {where} from {seat_day(c.effective)}.")
    return out


def in_force_lines(store: Store) -> list[str]:
    """Each widest-access setting in force, with whom it binds and since when, as the company's people read it:
    `read-only access for Tessaly Assistant for the Ramirez household since Monday 7 December`; a setting for every
    client is in force unless a later one for every client replaced it, one for an assistant unless a later one for
    every client or for it did."""
    done = sorted((c for c in store.all("controls") if c.what == "widest_access" and c.status == "done"),
                  key=lambda c: (c.effective, id_key(c.id)))
    out = []
    for c in done:
        later = [d for d in done if (d.effective, id_key(d.id)) > (c.effective, id_key(c.id))
                 and d.assistant in ("", c.assistant)]
        if later or (c.assistant and store.has("instances", c.assistant)
                     and store.get("instances", c.assistant).status == "ended"):
            continue
        if c.value == "free" and not c.assistant:
            continue                                    # the product's own: nothing narrower is in force
        out.append(f"{access_words(c.value, c.cents)} for {whom(store, c)} since {date_text(c.effective)}")
    return out


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
            out.append(f"{access_words(c.value, c.cents)} for {whom(store, c)} from {when}")
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
