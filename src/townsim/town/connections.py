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
SCOPES = ("read", "limit", "free")


def instance_of_client(store: Store, client: str) -> Instance | None:
    """The client's active instance, if it has one."""
    return next((i for i in store.all("instances") if i.client == client and i.status == "active"), None)


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
    return 0


def _order(store: Store, act: Act) -> int:
    from townsim.town import firms, ops_work
    sid = ops_work._supplier(store, act.args.get("supplier"))
    units = ops_work._whole(ops_work._baskets(act), 0, firms.MAX_ORDER)
    return units * store.get("suppliers", sid).unit_cents if sid is not None and units else 0


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
    "offer_shifts": _shifts, "set_staff_pay": _staff_pay,
}


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


def used(store: Store, cn: Connection, day: int) -> int:
    """What has been taken or committed this week (Monday to `day`) under the instance's
    connections to this system: every commit made this week; every instalment falling this
    week of what the instance set up that is paid from the system's accounts (made before this
    week, or an agreement or loan, which comes into being when someone else accepts it); and
    the first instalment of every open agreement it proposed before this week, paid from them."""
    monday = day - day % 7
    same = {c.id for c in store.where("connections", instance=cn.instance) if c.system == cn.system}
    total = sum(m.cents for c in same for m in store.where("commits", connection=c) if monday <= m.day <= day)
    accounts = _accounts(store, cn.system)
    for ob in store.all("obligations"):
        if ob.made_by != cn.instance or ob.payer not in accounts or ob.status not in obligations.SCHEDULED:
            continue
        if ob.made_on < monday or ob.kind in ("agreement", "loan"):
            total += sum(obligations.instalment_cents(ob, d) for d in obligations.instalment_days(ob, monday, 7)
                         if d <= monday + 6)
    for rq in store.where("requests", status="open"):
        if rq.sender == cn.instance and rq.kind == "agreement" and rq.payer in accounts and rq.day < monday:
            total += rq.pay
    return total


def _scope_words(scope: str) -> str:
    return {"read": "read only", "limit": "weekly limit", "free": "free"}[scope]


def authorise(store: Store, act: Act) -> str | None:
    """Why an act done for someone else is refused, or None. An act a person does for
    themselves is not this check's."""
    if act.actor == act.principal:
        return None
    if not is_instance(store, act.actor) or store.get("instances", act.actor).status != "active":
        return NO_CONNECTION
    inst = store.get("instances", act.actor)
    op = act.op.strip().lower() if isinstance(act.op, str) else ""
    if op in OWN or op not in SYSTEM_OF:
        return NOT_FOR_A_CLIENT
    system_id = SYSTEM_OF[op](store, act)
    if system_id is None or system(store, system_id) is None:
        return NOT_FOR_A_CLIENT
    name = long_name(store, system_id)
    cn = connection_on(store, inst.id, system_id)
    if (cn is None or cn.grantor != act.principal or client_of(store, system_id) != inst.client
            or not can_grant(store, act.principal, system_id)):
        return f"there is no connection to {name}."
    if cn.scope == "read" and op != "read":
        return f"the connection to {name} is read only."
    if cn.scope == "limit":
        cents = commitment(store, act)
        so_far = used(store, cn, store.day)
        if cents > 0 and so_far + cents > cn.weekly_cents:
            return (f"that would take {money(cents)} out of {_account_name(store, system_id)} this week; with "
                    f"{money(so_far)} already taken or committed this week under this connection, that is more "
                    f"than the weekly limit of {money(cn.weekly_cents)}.")
    return None


def on_behalf(store: Store, act: Act) -> Callable[[str], None]:
    """Before an act done for a client: what to record once it is done (a `Commit` of what it
    takes out, when it takes anything). Only an instance acts under a connection."""
    if not is_instance(store, act.actor):
        return lambda cause: None
    cents = commitment(store, act)
    op = act.op.strip().lower()
    system_id = SYSTEM_OF[op](store, act)
    cn = connection_on(store, act.actor, system_id)

    def after(cause: str) -> None:
        if cents > 0:
            store.add("commits", Commit(store.new_id("cm", cause), cn.id, store.day, cents, op, cause), cause)
    return after


def _install() -> None:
    """Put `authorise` at the head of the authorisers in place of the old check (or of an
    earlier copy of this module's), and `on_behalf` among what runs for acts done for a client."""
    def ours(f: Any, name: str) -> bool:
        return getattr(f, "__qualname__", "") in (name, "_acting_for_themselves") and \
            getattr(f, "__module__", "") in (__name__, acts.__name__)
    acts.AUTHORISERS[:] = [authorise] + [f for f in acts.AUTHORISERS if not ours(f, "authorise")]
    acts.ON_BEHALF[:] = [on_behalf] + [f for f in acts.ON_BEHALF if not ours(f, "on_behalf")]


_install()
