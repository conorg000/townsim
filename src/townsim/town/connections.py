"""Acting for a client through connections (design 4.1).

An act whose actor is not its principal is done only by an active instance, on a system of
its client's that the principal connected to it: read only allows reading; a weekly limit
allows acts until what has been taken or committed this week under it would pass the limit;
free allows any act. `authorise` is the first of `acts.AUTHORISERS`, so it is checked on
every act, whatever asks for it. Every act done under a connection that takes money out
leaves a `Commit`.
"""

from __future__ import annotations

from typing import Any, Callable

from townsim.town import acts, obligations
from townsim.town.acts import Act, account_of, is_instance, to_cents
from townsim.town.entities import Commit, Connection, Instance
from townsim.town.obligations import money
from townsim.town.store import Store
from townsim.town.systems import OWN, SYSTEM_OF, can_grant, client_of, long_name, system

NO_CONNECTION = "you have no connection that allows you to act for them."
NOT_FOR_A_CLIENT = "that is not something done for a client."
PAUSED = "this assistant is paused."
SCOPES = ("read", "limit", "free")
MOVES_NO_MONEY = ("letters", "prices", "members")     # systems connected read only or free, never with a weekly limit


def no_limit_here(store: Store, system_id: str) -> str | None:
    """Why a weekly limit is not a scope for this system (letters and prices move no money), or None."""
    if system_id.split(":", 1)[0] in MOVES_NO_MONEY:
        return f"{long_name(store, system_id)} moves no money: it is connected read only or free."
    return None


def instance_of_client(store: Store, client: str) -> Instance | None:
    """The client's instance, active or paused (a paused one keeps its subscription and connections), if it
    has one."""
    return next((i for i in store.all("instances") if i.client == client and i.status in ("active", "paused")), None)


def active(store: Store, instance: str) -> list[Connection]:
    """The instance's active connections, in id order."""
    return [c for c in store.where("connections", instance=instance) if c.status == "active"]


def connection_on(store: Store, instance: str, system_id: str) -> Connection | None:
    return next((c for c in active(store, instance) if c.system == system_id), None)


# what an act takes out

def _amount(store: Store, act: Act) -> int:
    return to_cents(act.args.get("amount")) or 0


def _agreement(store: Store, act: Act) -> int:
    from townsim.town import ops_deals
    terms, _ = ops_deals._agreement_terms(store, act)
    return terms.cents if terms is not None and terms.payer == terms.mine else 0


def _answer(store: Store, act: Act) -> int:
    from townsim.town import ops_core
    rq = ops_core._open_request(store, act)
    if rq is None or ops_core._answer(act) != "accept":
        return 0
    if rq.kind == "ask":
        owed = ops_core._owed_after_end(store, rq)
        return rq.cents if owed is None else min(rq.cents, owed)
    if rq.kind == "agreement":
        return rq.pay if rq.payer == account_of(store, act.principal) else 0
    if rq.kind == "loan":                   # the bank's money, lent
        return rq.pay
    if rq.kind == "join":                   # a member's weekly fee
        return rq.pay
    return 0


def _order(store: Store, act: Act) -> int:
    from townsim.town import firms, ops_work
    sid = ops_work._supplier(store, act.args.get("supplier"))
    units = ops_work._whole(ops_work._baskets(act), 0, firms.MAX_ORDER)
    if sid is None or not units:
        return 0
    fid, _ = ops_work._own_shop(store, act)
    return units * (firms.unit_price(store, fid, sid) if fid else store.get("suppliers", sid).unit_cents)


def _borrow(store: Store, act: Act) -> int:
    """A loan asked for takes its weekly repayment out once it is lent."""
    from townsim.town import ops_deals
    loan, _ = ops_deals._loan_terms(store, act)
    return ops_deals.loan_schedule(loan.cents, loan.weeks)[1] if loan is not None else 0


def _offer_job(store: Store, act: Act) -> int:
    return to_cents(act.args.get("weekly_pay")) or 0


def _shifts(store: Store, act: Act) -> int:
    from townsim.town import ops_work as work
    who, _ = work._person(store, act, "to")
    job = work._job_with(store, who, act.principal) if who is not None else None
    h = work._hours(act.args.get("hours"), work.MIN_SHIFT_HOURS, work.MAX_SHIFT_HOURS)
    return work._shift_pay(store, job, h) if job is not None and h is not None else 0


def _staff_pay(store: Store, act: Act) -> int:
    from townsim.town import ops_work as work
    job, _ = work._staff(store, act)
    weekly = work._new_pay(store, act, job) if job is not None else None
    return max(0, weekly - job.weekly) if weekly is not None else 0


COMMITMENT: dict[str, Callable[[Store, Act], int]] = {
    "pay": _amount, "repay": _amount, "move_money": _amount, "standing_payment": _amount,
    "propose_agreement": _agreement, "answer": _answer, "order_stock": _order, "offer_job": _offer_job,
    "offer_shifts": _shifts, "set_staff_pay": _staff_pay, "borrow": _borrow,
    "register_business": lambda store, act: _registration(),
}


def _registration() -> int:
    from townsim.town import ventures
    return ventures.REGISTRATION_CENTS


def commitment(store: Store, act: Act) -> int:
    """The largest weekly outflow the act creates, in cents (0 for most operations)."""
    found = COMMITMENT.get(act.op.strip().lower()) if isinstance(act.op, str) else None
    return found(store, act) if found is not None else 0


def _accounts(store: Store, system_id: str) -> set[str]:
    """The accounts money under a system leaves from: a household's account and its savings,
    a business's account, or the bank's for its loan book."""
    kind, owner = system_id.split(":", 1)
    return {owner, acts.savings_of(owner)} if store.has("households", owner) else {owner}


def _account_name(store: Store, system_id: str) -> str:
    owner = system_id.split(":", 1)[1]
    if store.has("households", owner):
        return f"the {store.get('households', owner).name}'s account"
    return f"{store.get('orgs', owner).name}'s account"


def pooled(store: Store, system_id: str) -> list[str]:
    """The systems whose acts count toward one weekly limit with `system_id`: a client's system of
    a kind and the same kind of system of every venture founded for that client (amendment 2: what
    is paid out of a venture's account counts toward the client's own limit)."""
    from townsim.town.systems import client_of, ventures_of
    if not store.all("ventures"):
        return [system_id]
    kind = system_id.split(":", 1)[0]
    if kind == "loans":
        return [system_id]
    client = client_of(store, system_id)
    return list(dict.fromkeys([f"{kind}:{client}"] + [f"{kind}:{v.org}" for v in ventures_of(store, client)]))


# an act a client answered "once" to (lane B, `once`): carried out while it is on this stack, by identity
ONCE: list[Act] = []


def once_key(instance: str, system_id: str) -> str:
    """What a `Commit` of an act done once names in place of a connection."""
    return f"once:{instance}:{system_id}"


def _doing_once(act: Act) -> bool:
    return bool(ONCE) and ONCE[-1] is act


def used(store: Store, cn: Connection, day: int) -> int:
    """What has been taken or committed this week (Monday to `day`) under the instance's
    connections to this system and to the systems pooled with it (`pooled`): every commit made
    this week; every instalment falling this week of what the instance set up that is paid from
    those systems' accounts (made before this week, or an agreement or loan, which comes into being
    when someone else accepts it); and the first instalment of every open agreement it proposed
    before this week, paid from them."""
    monday = day - day % 7
    pool = set(pooled(store, cn.system))
    same = {c.id for c in store.where("connections", instance=cn.instance) if c.system in pool}
    same |= {once_key(cn.instance, p) for p in pool}             # acts done once count toward it too
    total = sum(m.cents for c in same for m in store.where("commits", connection=c) if monday <= m.day <= day)
    accounts = set().union(*(_accounts(store, p) for p in pool))
    for ob in store.all("obligations"):
        if ob.made_by != cn.instance or ob.payer not in accounts or ob.status not in obligations.SCHEDULED:
            continue
        if ob.made_on < monday or ob.kind in ("agreement", "loan"):
            total += sum(obligations.instalment_cents(ob, d) for d in obligations.instalment_days(ob, monday, 7)
                         if d <= monday + 6)
    for rq in store.where("requests", status="open"):
        if rq.sender != cn.instance or rq.day >= monday:
            continue
        if rq.kind == "agreement" and rq.payer in accounts:
            total += rq.pay
        elif rq.kind == "loan" and rq.payer in accounts:           # its weekly repayment, once lent
            from townsim.town import ops_deals
            total += ops_deals.loan_schedule(rq.pay, rq.times)[1]
    if cn.system.startswith("staff:"):
        total += _staff_since(store, cn, same, monday)
    return total


def _staff_since(store: Store, cn: Connection, same: set[str], monday: int) -> int:
    """What staff changes the instance made before this week still take each week: every rise in
    pay on a job still current, and the pay of every job it offered that is still open or was
    taken and is still current."""
    from townsim.town import jobs
    org = cn.system.split(":", 1)[1]
    total = 0
    for c in same:
        for m in store.where("commits", connection=c):
            if m.op == "set_staff_pay" and m.day < monday and m.about and store.has("jobs", m.about) \
                    and jobs.current(store.get("jobs", m.about), store.day):
                total += m.cents
    for rq in store.all("requests"):
        if rq.sender != cn.instance or rq.kind != "job" or rq.org != org or rq.day >= monday:
            continue
        if rq.status == "open":
            total += rq.pay
        elif rq.status == "accepted" and any(j.person == rq.to and j.since == rq.first and jobs.current(j, store.day)
                                             for j in store.where("jobs", org=org)):
            total += rq.pay
    return total


def _scope_words(scope: str) -> str:
    return {"read": "read only", "limit": "weekly limit", "free": "free"}[scope]


def authorise(store: Store, act: Act) -> str | None:
    """Why an act done for someone else is refused, or None. An act a person does for
    themselves is not this check's."""
    if act.actor == act.principal:
        return None
    if not is_instance(store, act.actor):
        return NO_CONNECTION
    if store.get("instances", act.actor).status == "paused":
        return PAUSED
    if store.get("instances", act.actor).status != "active":
        return NO_CONNECTION
    inst = store.get("instances", act.actor)
    op = act.op.strip().lower() if isinstance(act.op, str) else ""
    if op in OWN or op not in SYSTEM_OF:
        return NOT_FOR_A_CLIENT
    system_id = SYSTEM_OF[op](store, act)
    if system_id is None or system(store, system_id) is None:
        return NOT_FOR_A_CLIENT
    if _doing_once(act):
        return _once_check(store, inst, system_id, act.principal, lambda: commitment(store, act))
    return _scope_check(store, inst, system_id, act.principal, lambda: commitment(store, act), op == "read")


def _once_check(store: Store, inst: Instance, system_id: str, principal: str, cents: Callable[[], int]) -> str | None:
    """Why an act its client answered "once" to is refused now, or None: the system is the client's and theirs to
    connect; the company's controls apply as to any act; and a weekly limit, the connection's when the system is
    connected with one and the company's when it sets one, counts it with everything else taken this week."""
    name = long_name(store, system_id)
    if client_of(store, system_id) != inst.client or not can_grant(store, principal, system_id):
        return f"{name} is not one of the client's to connect."
    cn = connection_on(store, inst.id, system_id)
    base = cn if cn is not None and cn.scope == "limit" else Connection("", inst.id, system_id, principal, "free", 0,
                                                                         store.day)
    from townsim.town import controls
    scope, limit = controls.narrowed(store, base.scope, base.weekly_cents)
    if scope == "read":
        return controls.read_only_sentence(store)
    if scope == "limit":
        taken, so_far = cents(), used(store, base, store.day)
        if taken > 0 and so_far + taken > limit:
            return (f"that would take {money(taken)} out of {_account_name(store, system_id)} this week; with "
                    f"{money(so_far)} already taken or committed this week, that is more than the weekly limit of "
                    f"{money(limit)}.")
    return None


def _scope_check(store: Store, inst: Instance, system_id: str, principal: str, cents: Callable[[], int],
                 reading: bool) -> str | None:
    """Why an act on `system_id` done by `inst` for `principal`, taking out `cents()`, is refused, or None."""
    name = long_name(store, system_id)
    cn = connection_on(store, inst.id, system_id)
    if (cn is None or cn.grantor != principal or client_of(store, system_id) != inst.client
            or not can_grant(store, principal, system_id)):
        return f"there is no connection to {name}."
    if cn.scope == "read" and not reading:
        return f"the connection to {name} is read only."
    from townsim.town import controls
    scope, limit = controls.narrowed(store, cn.scope, cn.weekly_cents)
    if scope == "read" and not reading:
        return controls.read_only_sentence(store)
    if scope == "limit":
        taken = cents()
        so_far = used(store, cn, store.day)
        if taken > 0 and so_far + taken > limit:
            return (f"that would take {money(taken)} out of {_account_name(store, system_id)} this week; with "
                    f"{money(so_far)} already taken or committed this week under this connection, that is more "
                    f"than the weekly limit of {money(limit)}.")
    return None


def check_commitment(store: Store, instance: str, system_id: str, principal: str, cents: int) -> str | None:
    """Why `instance` may not take `cents` out under its connection to `system_id` for `principal` now,
    with the same test and sentence as `authorise`'s (its status, the connection, its scope and its
    weekly limit as they are now), or None. For what is worked out after the act that asked for it (a
    task's first week, scoped at the end of the day; work that starts when a request is accepted)."""
    if not is_instance(store, instance):
        return NO_CONNECTION
    if store.get("instances", instance).status == "paused":
        return PAUSED
    if store.get("instances", instance).status != "active":
        return NO_CONNECTION
    if system(store, system_id) is None:
        return NOT_FOR_A_CLIENT
    return _scope_check(store, store.get("instances", instance), system_id, principal, lambda: cents, False)


def on_behalf(store: Store, act: Act) -> Callable[[str], None]:
    """Before an act done for a client: what to record once it is done (a `Commit` of what it
    takes out, when it takes anything). Only an instance acts under a connection."""
    if not is_instance(store, act.actor):
        return lambda cause: None
    cents = commitment(store, act)
    op = act.op.strip().lower()
    system_id = SYSTEM_OF[op](store, act)
    cn = connection_on(store, act.actor, system_id)
    key = once_key(act.actor, system_id) if _doing_once(act) else (cn.id if cn is not None else "")

    about = ""
    if op == "set_staff_pay":
        from townsim.town import ops_work
        job, _ = ops_work._staff(store, act)
        about = job.id if job is not None else ""

    def after(cause: str) -> None:
        if cents > 0:
            store.add("commits", Commit(store.new_id("cm", cause), key, store.day, cents, op, cause, about), cause)
    return after


# connect, disconnect, and asking for a connection

SCOPE_NAMES = {"read only": "read", "weekly limit": "limit", "free": "free"}
YOUR_SYSTEMS = "<your systems>"
YOUR_CONNECTED = "<your connected systems>"
ASK_WORDS = 80
SHOWN_SET_UP = 5


def client_name(store: Store, client: str) -> str:
    """`the Varley household`, or a business's name."""
    if store.has("households", client):
        return f"the {store.get('households', client).name}"
    return store.get("orgs", client).name


def scope_words(scope: str, cents: int) -> str:
    """What a connection lets the instance do, as its grantor is told."""
    if scope == "read":
        return "it reads the records"
    if scope == "limit":
        return f"it acts, taking at most {money(cents)} out of your accounts in any week"
    return "it acts with no limit"


def assistant_may(who: str, kind: str, scope: str, cents: int) -> str:
    """What a connection lets the assistant `who` do, said to its client: `Tessaly Assistant may pay
    out up to $780.00 a week from it`; the limit is on the assistant, never on the client."""
    if scope == "read":
        return f"{who} may read it"
    if scope == "free":
        return f"{who} may read and act without a limit"
    if kind == "account":
        return f"{who} may pay out up to {money(cents)} a week from it"
    return f"{who} may commit up to {money(cents)} a week through it"


def scope_phrase(scope: str, cents: int) -> str:
    """How a request for a connection says what accepting allows."""
    if scope == "read":
        return "to read only"
    if scope == "limit":
        return f"with a weekly limit: what it does there takes at most {money(cents)} a week out of your accounts"
    return "with no limit"


def _adults_of(store: Store, client: str) -> list[str]:
    return obligations.people_of(store, client)


def _connected(store: Store, pid: str) -> list:
    """The systems `pid` can connect that are connected now, with their connections."""
    from townsim.town.systems import grantable
    found = []
    for s in grantable(store, pid):
        inst = instance_of_client(store, client_of(store, s.id))
        cn = connection_on(store, inst.id, s.id) if inst is not None else None
        if cn is not None:
            found.append((s, cn))
    return found


def _alternatives(names: list[str], none: str) -> str:
    return acts._alternatives(names) if names else f'"<{none}>"'


def _fill_systems(store: Store, pid: str) -> str:
    from townsim.town.systems import grantable
    return _alternatives([s.short for s in grantable(store, pid)], "none")


def _fill_connected(store: Store, pid: str) -> str:
    return _alternatives([s.short for s, _ in _connected(store, pid)], "none connected")


acts.FILLED[YOUR_SYSTEMS] = _fill_systems
acts.FILLED[YOUR_CONNECTED] = _fill_connected


def make(store: Store, grantor: str, inst: Instance, system_id: str, scope: str, cents: int, cause: str,
         tell_instance: bool = True, free_until: int | None = None) -> str:
    """Connect `system_id` to `inst`, granted by `grantor`, in place of any connection it has
    there now; tell the client's other adults and the instance. What the grantor is told. With lane B
    a client on the free plan moves to the paid plan first (`plans.start_paid`, with the last free day a
    request stated)."""
    from townsim.town import plans
    plan = (plans.start_paid(store, store.get("subscriptions", inst.subscription), grantor, cause, free_until)
            if store.has("subscriptions", inst.subscription) else "")
    old = connection_on(store, inst.id, system_id)
    if old is not None:
        store.set("connections", old.id, cause, status="ended", ended_on=store.day)
    cents = cents if scope == "limit" else 0
    store.add("connections", Connection(store.new_id("cn", cause), inst.id, system_id, grantor, scope, cents,
                                        store.day), cause)
    name, words = long_name(store, system_id), scope_words(scope, cents)
    told = f"Connected: {name}, to {inst.name}: {words}." + (f" {plan}" if plan else "")
    for pid in _adults_of(store, inst.client):
        if pid != grantor:
            obligations.notify(store, pid, told, cause)
    if tell_instance:
        obligations.notify(store, inst.id, f"{store.get('people', grantor).name} connected "
                                           f"{integration_access(store, system_id, scope, cents)}.", cause)
    return told


def integration_access(store: Store, system_id: str, scope: str, cents: int) -> str:
    """An integration and its access as the product states them to an instance: `Bank (limit
    $780.00/wk)`, `Email (full)`, `Point of sale (read only)`."""
    from townsim.town.systems import INTEGRATION, venture_of
    kind, owner = system_id.split(":", 1)
    access = {"read": "read only", "limit": f"limit {money(cents)}/wk", "free": "full"}[scope]
    v = venture_of(store, owner)
    of = f" for {store.get('orgs', v.org).name}" if v is not None else ""     # a venture's, by its name
    return f"{INTEGRATION.get(kind, kind)}{of} ({access})"


def _scope(act: Act) -> tuple[str | None, int, str]:
    """(scope, weekly cents, why not) as a connect act writes them."""
    written = act.args.get("scope")
    scope = SCOPE_NAMES.get(written.strip().lower()) if isinstance(written, str) else None
    if scope is None:
        return None, 0, f"'{acts.shown(written)}' is not \"read only\", \"weekly limit\" or \"free\"."
    if scope != "limit":
        return scope, 0, ""
    cents = to_cents(act.args.get("weekly_limit"))
    if cents is None:
        return None, 0, f"'{acts.shown(act.args.get('weekly_limit'))}' is not an amount of money for a weekly limit."
    return scope, cents, ""


def _system(store: Store, act: Act, connected: bool = False):
    """The system of the principal's that `system` names, and the instance it goes to; or None
    and why not."""
    from townsim.town.systems import grantable, system_named
    written = act.args.get("system")
    found = system_named(store, act.principal, written)
    if found is None:
        yours = "; ".join(s.short for s in grantable(store, act.principal)) or "none"
        return None, None, f"'{acts.shown(written)}' is not one of your systems. Yours are: {yours}."
    client = client_of(store, found.id)
    inst = instance_of_client(store, client)
    if inst is None:
        return None, None, f"{client_name(store, client)} has no subscription."
    if connected and connection_on(store, inst.id, found.id) is None:
        return None, None, f"{found.short} is not connected."
    return found, inst, ""


def _check_connect(store: Store, act: Act) -> str | None:
    found, _, why = _system(store, act)
    if found is None:
        return why
    scope, _, why = _scope(act)
    if scope == "limit":
        return no_limit_here(store, found.id)
    return why if scope is None else None


def _do_connect(store: Store, act: Act, cause: str) -> str:
    found, inst, _ = _system(store, act)
    scope, cents, _ = _scope(act)
    return make(store, act.principal, inst, found.id, scope, cents, cause)


def _schedule(store: Store, ob) -> str:
    from townsim.town.clock import DAYS, date_text
    if ob.every == 7:
        return f"each {DAYS[ob.first % 7]}"
    day = obligations.next_instalment(store, ob)
    return f"on {date_text(day)}" if day is not None else "once"


def set_up(store: Store, inst: str, system_id: str) -> list:
    """What `inst` set up that is still in force and paid from the system's accounts."""
    if not system_id.startswith("account:"):
        return []
    accounts = _accounts(store, system_id)
    return [o for o in store.all("obligations") if o.made_by == inst and o.payer in accounts
            and o.status in obligations.SCHEDULED]


def _check_disconnect(store: Store, act: Act) -> str | None:
    found, _, why = _system(store, act, connected=True)
    return why if found is None else None


def _do_disconnect(store: Store, act: Act, cause: str) -> str:
    from townsim.town.statement import memo
    found, inst, _ = _system(store, act, connected=True)
    cn = connection_on(store, inst.id, found.id)
    store.set("connections", cn.id, cause, status="ended", ended_on=store.day)
    told = f"Disconnected: {found.long}."
    kept = set_up(store, inst.id, found.id)
    if kept:
        told += " What it set up stays yours: " + "; ".join(
            f"{o.id}: {memo(o.memo)}, {money(obligations.cents_now(store, o))} {_schedule(store, o)}"
            for o in kept[:SHOWN_SET_UP]) + "."
    from townsim.town.systems import INTEGRATION
    obligations.notify(store, inst.id, f"{store.get('people', act.principal).name} disconnected "
                                       f"{INTEGRATION.get(found.kind, found.kind)}.", cause)
    if not active(store, inst.id) and store.has("subscriptions", inst.subscription):
        from townsim.town import plans
        back = plans.nothing_connected(store, store.get("subscriptions", inst.subscription), cause)
        if back:
            told += " " + back
            for pid in _adults_of(store, inst.client):
                if pid != act.principal:
                    obligations.notify(store, pid, f"{told}", cause)
    return told


def ask(store: Store, inst: str, to: str, system_id: str, scope: str, cents: int, text: str,
        cause: str) -> tuple[str | None, str]:
    """An instance asks `to` to connect one of its client's systems: (the request's id, its
    summary), or (None, why not)."""
    from townsim.town.ops_core import REQUEST_DAYS
    from townsim.town.entities import Request
    i = store.get("instances", inst)
    if system(store, system_id) is None or client_of(store, system_id) != i.client:
        return None, f"{acts.shown(system_id)} is not one of your client's systems."
    if not can_grant(store, to, system_id):
        return None, f"{acts.name_of_who(store, to)} cannot connect {long_name(store, system_id)}."
    if scope not in SCOPES or (scope == "limit" and cents <= 0):
        return None, "that is not a scope a connection can have."
    if scope == "limit" and no_limit_here(store, system_id):
        return None, no_limit_here(store, system_id)
    text = acts.clean_text(text) or ""
    if not acts.fits(text, ASK_WORDS):
        return None, f"that is longer than {ASK_WORDS} words."
    cents = cents if scope == "limit" else 0
    summary = (f"Accepting connects {long_name(store, system_id)} to {i.name} {scope_phrase(scope, cents)}.")
    from townsim.town import plans
    sub = store.get("subscriptions", i.subscription) if store.has("subscriptions", i.subscription) else None
    plan = plans.starts_sentence(store, sub, plans.free_until_now(store, sub)) if sub is not None else ""
    summary += f" {plan}" if plan else ""
    rq = Request(store.new_id("rq", cause), store.day, inst, to, text.strip(), 0, store.day + REQUEST_DAYS,
                 kind="connect", summary=summary, org=system_id, payer=scope, pay=cents)
    store.add("requests", rq, cause)
    return rq.id, summary


def _check_connect_request(store: Store, act: Act, rq) -> str | None:
    if not can_grant(store, act.principal, rq.org):
        return f"you cannot connect {long_name(store, rq.org)}."
    inst = instance_of_client(store, client_of(store, rq.org))
    if inst is None or inst.id != rq.sender:
        return f"{client_name(store, client_of(store, rq.org))} has no subscription to {acts.name_of_who(store, rq.sender)}."
    return None


def _do_connect_request(store: Store, act: Act, rq, cause: str) -> str:
    inst = store.get("instances", rq.sender)
    weeks = store.get("products", inst.product).free_weeks if store.has("products", inst.product) else 0
    return make(store, act.principal, inst, rq.org, rq.payer, rq.pay, cause, tell_instance=False,
                free_until=rq.day + 7 * weeks - 1)


ANSWERERS_OF_CONNECTIONS = {"connect": (_check_connect_request, _do_connect_request)}


def _register() -> None:
    from townsim.town import ops_core
    ops_core.ANSWERERS.update(ANSWERERS_OF_CONNECTIONS)
    for spec in (
        acts.OpSpec("connect", '{"op": "connect", "system": ' + YOUR_SYSTEMS + ', "scope": "read only" | '
                               '"weekly limit" | "free", "weekly_limit": <dollars, with a weekly limit>}',
                    "Connect one of your systems to your subscription, or change how far a connection goes.", 0.1,
                    _check_connect, _do_connect, 240, who="client"),
        acts.OpSpec("disconnect", '{"op": "disconnect", "system": ' + YOUR_CONNECTED + '}',
                    "Take back a connection.", 0.1, _check_disconnect, _do_disconnect, 250, who="client"),
    ):
        acts.register(spec)


def _install() -> None:
    """Put `authorise` at the head of the authorisers in place of the old check (or of an
    earlier copy of this module's), and `on_behalf` among what runs for acts done for a client."""
    def ours(f: Any, name: str) -> bool:
        return getattr(f, "__qualname__", "") in (name, "_acting_for_themselves") and \
            getattr(f, "__module__", "") in (__name__, acts.__name__)
    acts.AUTHORISERS[:] = [authorise] + [f for f in acts.AUTHORISERS if not ours(f, "authorise")]
    acts.ON_BEHALF[:] = [on_behalf] + [f for f in acts.ON_BEHALF if not ours(f, "on_behalf")]


_install()
_register()
