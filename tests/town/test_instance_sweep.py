"""Every operation an instance can be connected for, done by an instance, raises no engine error;
and no act is carried out for a client beyond what was connected, by any operation."""

import functools
import pickle
import random

from town.helpers import generated
from townsim.town import hamlet_script, lane_b, ops_core  # noqa: F401  (registers the operations)
from townsim.town.acts import Act, actor_name, apply_act
from townsim.town.connections import commitment, used
from townsim.town.entities import Connection, Instance, Request
from townsim.town.loop import run_day
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.systems import SYSTEM_OF, systems_of_client

CAUSE = "routine:setup"
# each kind of client, and who acts as its principal
CLIENTS = (("h6", "p10"), ("h1", "p1"), ("h9", "p16"), ("org10", "p16"), ("org13", "p11"))


@functools.cache
def _week() -> bytes:
    s = generated(1)
    keys = Keys("sweep")
    for _ in range(7):
        run_day(s, ScriptedReplier(hamlet_script.script), keys)
    return pickle.dumps(s)


def week() -> "Store":  # noqa: F821
    return pickle.loads(_week())


def _instances(s, scope_of=lambda system: ("free", 0)) -> dict[str, str]:
    """An instance for each client, holding every system of its client; its id by client."""
    made = {}
    for n, (client, grantor) in enumerate(CLIENTS, 1):
        iid = f"as{n}"
        s.add("instances", Instance(iid, "pr1", client, f"sub{n}", f"Wrenfold Assistant for {client}"), CAUSE)
        for system in systems_of_client(s, client):
            scope, cents = scope_of(system.id)
            s.add("connections", Connection(s.new_id("cn", CAUSE), iid, system.id, grantor, scope, cents, s.day), CAUSE)
        made[client] = iid
    return made


def _args(s, client: str, principal: str) -> dict[str, list[dict]]:
    """Argument sets for each operation, for a client."""
    biz = s.get("orgs", client).name if s.has("orgs", client) else None
    src = {"from": biz} if biz else {}
    ob = next((o.id for o in s.all("obligations") if o.payer == client and o.status == "active"), "ob1")
    agreement = next((o.id for o in s.all("obligations") if o.kind == "agreement" and o.status == "active"), "ob1")
    rqs = [r.id for r in s.all("requests") if r.to == principal and r.status == "open"]
    staff = next((s.get("people", j.person).name for j in s.all("jobs")
                  if s.get("orgs", j.org).owner == principal and j.person != principal and j.ended == -1), "Tara Russo")
    tenant = next((s.get("people", h.members[0]).name for h in s.all("households") if h.landlord == principal),
                  "Tara Russo")
    return {
        "pay": [{"to": "Tara Russo", "amount": 3, **src}, {"to": "Ashcombe Savings Bank", "amount": 2,
                                                              "for": ob, **src}],
        "standing_payment": [{"to": "Jessica Whitaker", "amount": 4, "day": "Friday", "weeks": 2, **src}],
        "stop": [{"id": ob}, {"id": "ap1"}],
        "request": [{"to": "Jessica Whitaker", "text": "Could you pay for the eggs?", "amount": 5},
                    {"to": "Jessica Whitaker", "text": "Will you come by?"}],
        "propose_agreement": [{"to": "Jessica Whitaker", "amount": 6, "per": "week", "weeks": 3, "start": "Monday 30 March",
                               "payer": "me", "what": "the garden", **src}],
        "propose_end": [{"id": agreement}],
        "borrow": [{"amount": 500, "weeks": 10, "purpose": "a van", **src}],
        "repay": [{"id": ob, "amount": 1, **src}],
        "move_money": [{"from": biz or "household", "to": "savings" if not biz else "household", "amount": 1}],
        "read": [{"what": w, **src} for w in ("account", "account in full", "agreements")],
        "answer": [{"request": r, "answer": a} for r in rqs for a in ("decline", "accept")],
        "order_stock": [{"supplier": "Netherby Falls Wholesale", "baskets": 3}],
        "set_price": [{"price": 9.5}, {"for": f"Club {client}", "price": 3}],
        "offer_job": [{"to": "Lisa Marsh", "weekly_pay": 300, "hours": 20}],
        "offer_shifts": [{"to": staff, "hours": 2}],
        "dismiss": [{"who": staff}],
        "set_staff_pay": [{"who": staff, "weekly_pay": 400}],
        "set_rent": [{"tenant": tenant, "weekly": 200}],
        "serve_notice": [{"tenant": tenant}],
        "register_business": [{"name": f"Club {client}", "kind": "club", "offering": "walks", "weekly_fee": 2, **src}],
        "invite_member": [{"of": f"Club {client}", "to": "Jessica Whitaker"}, {"of": f"Club {client}", "to": "Lisa Marsh"}],
        "set_rule": [{"name": "rent", "when": {"weekday": "Sunday"}, "do": {"op": "pay", "to": "Tara Russo", "amount": 2, **src}},
                     {"name": "stock", "when": {"figure": "stock", "is": "below", "value": 900},
                      "do": {"op": "order_stock", "supplier": "Netherby Falls Wholesale",
                             "baskets": {"figure": "sold_last_week", "times": 1.1, "plus": 0}}},
                     {"name": "save", "when": {"figure": "balance", "is": "above", "value": 1},
                      "do": {"op": "move_money", "from": biz or "household", "to": "savings" if not biz else "household",
                             "amount": {"figure": "balance", "times": 0.01, "plus": 0}}}],
        "delete_rule": [{"rule": "rl1"}, {"rule": "rl2"}],
    }


def _requests_to(s, principal: str) -> None:
    """A money ask and an ask of nothing to the principal, from someone else."""
    other = "p9" if principal != "p9" else "p13"
    for cents in (500, 0):
        s.add("requests", Request(s.new_id("rq", CAUSE), s.day, other, principal, "Please.", cents, s.day + 14), CAUSE)


def test_every_connectable_operation_by_an_instance_raises_no_engine_error():
    s = week()
    lane_b.install(s, CAUSE)
    made = _instances(s)
    done, n = 0, 0
    for client, principal in CLIENTS:
        _requests_to(s, principal)
        sets = _args(s, client, principal)
        assert set(sets) == set(SYSTEM_OF)
        for op in SYSTEM_OF:
            for args in sets[op] + [{}]:
                n += 1
                out = apply_act(s, Act(made[client], principal, op, args), n)
                assert not s.engine_errors, (client, op, args, s.engine_errors)
                done += out.ok
    assert done > 40
    assert actor_name(s, "as1") == "Wrenfold Assistant for h6" and actor_name(s, "p10") == "Brittany Wagner"


def test_no_act_is_carried_out_beyond_what_was_connected():
    """Random acts by instances whose systems are connected at random scopes and limits: every
    act on a system not connected, or connected read only, changes nothing; under a weekly limit
    what is committed this week never passes the limit."""
    rng = random.Random("sweep:scopes")
    s = week()
    lane_b.install(s, CAUSE)
    scopes = {}

    def scope_of(system):
        scope = rng.choice(("none", "read", "limit", "free"))
        scopes[system] = scope
        return (scope, rng.choice((500, 2000, 10_000))) if scope != "none" else ("read", 0)
    made = _instances(s, scope_of)
    for cn in s.all("connections"):            # "none": no connection at all
        if scopes[cn.system] == "none":
            s.set("connections", cn.id, CAUSE, status="ended")
    for client, principal in CLIENTS:
        _requests_to(s, principal)
    n = 0
    for _ in range(3):
        for client, principal in CLIENTS:
            sets = _args(s, client, principal)
            for _ in range(25):
                op = rng.choice(sorted(SYSTEM_OF))
                args = rng.choice(sets[op] or [{}])
                if "amount" in args and rng.random() < 0.5:
                    args = {**args, "amount": rng.choice((1, 20, 90, 400))}
                act = Act(made[client], principal, op, args)
                system = SYSTEM_OF[op](s, act)
                now = next((c for c in s.all("connections") if c.instance == made[client] and c.system == system
                            and c.status == "active"), None)       # a venture's, connected when it was founded
                scope = scopes.get(system, now.scope if now is not None else "none")
                cents = commitment(s, act)
                before, mark = s.hash(), s.mark()
                n += 1
                out = apply_act(s, act, n)
                if scope == "none" or (scope == "read" and op != "read"):
                    assert not out.ok and out.beyond and s.hash() == before, (op, args, out.msg)
                if out.ok and scope == "limit" and cents > 0:
                    cn = next(c for c in s.all("connections") if c.system == system and c.status == "active")
                    assert used(s, cn, s.day) <= cn.weekly_cents, (op, args)
                    assert any(c.table == "commits" for c in s.changes[mark:])
        assert not s.engine_errors
        for _ in range(3):
            run_day(s, ScriptedReplier(hamlet_script.script), Keys("sweep"))
