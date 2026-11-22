"""Asking to do one named thing (lane B): an instance asks its client to let it carry out one act, named as it
would call it (an integration's action and its arguments), and the client answers "once", "always" or "no".

The act is fixed when it is asked for: its operation and the arguments it is done with (with `from` set by
code) are kept in an `ActAsk` row beside the request, and the request's summary is written by code from them.
"once" carries out exactly that act, done by the instance for the one who answered, through `apply_act` like any
other act: `connections.authorise` lets it through as the act answered once (by identity, while it is being
done), with the company's controls and any weekly limit applying, and it connects nothing. "always" connects the
system with the access the request named, which starts the paid plan on the free plan. "no" does nothing. A
request is answered once: an answered, lapsed or expired one does nothing.
"""

from __future__ import annotations

import json
from typing import Any

from townsim.town import acts, connections, obligations, plans, seat_ids
from townsim.town.acts import Act, apply_act, to_cents
from townsim.town.clock import date_text
from townsim.town.entities import ActAsk, Request
from townsim.town.obligations import money
from townsim.town.store import Store
from townsim.town.systems import INTEGRATION, long_name, system

ANSWERS = {"once": "once", "always": "always", "no": "no", "decline": "no"}


def row_of(store: Store, rq_id: str) -> ActAsk | None:
    return next((a for a in store.all("asks") if a.request == rq_id), None)


def _integration(store: Store, system_id: str) -> str:
    return INTEGRATION.get(system_id.split(":", 1)[0], system_id)


def _short(store: Store, system_id: str) -> str:
    return system(store, system_id).short


def _who(store: Store, value: Any) -> str:
    pid = acts.resolve(store, value) if isinstance(value, str) else None
    return acts.name_of_who(store, pid) if pid is not None else f"'{acts.shown(value)}'"


def _amount(value: Any) -> str:
    cents = to_cents(value)
    return money(cents) if cents is not None else f"'{acts.shown(value)}'"


def what(store: Store, inst, op: str, args: dict, pid: str) -> str:
    """What the act does, after `asks to`: `place a stock order: 260 baskets a week from Coldwater Bend Food Supply
    at $6.58, from Monday 16 March`; for an action without words of its own, its arguments as written."""
    from townsim.town import firms, jobs, ops_work
    a = args
    if op == "order_stock":
        fid, _ = ops_work._own_shop(store, Act(inst.id, pid, op, a))
        sid = ops_work._supplier(store, a.get("supplier"))
        units = ops_work._whole(ops_work._baskets(Act(inst.id, pid, op, a)), 0, firms.MAX_ORDER)
        supplier = ops_work._org_name(store, store.get("suppliers", sid).org)
        monday = date_text(jobs.next_monday(store.day))
        if not units:
            return f"end the stock order from {supplier}, from {monday}"
        return (f"place a stock order: {units:,} baskets a week from {supplier} at "
                f"{money(firms.unit_price(store, fid, sid))}, from {monday}")
    if op == "set_price":
        return f"set the price of a basket to {_amount(a.get('price'))}"
    if op == "pay":
        note = f' ("{" ".join(str(a.get("note")).split())}")' if isinstance(a.get("note"), str) and a["note"].strip() else ""
        return f"make a payment: {_amount(a.get('amount'))} to {_who(store, a.get('to'))}{note}"
    if op == "standing_payment":
        return (f"set up a weekly payment: {_amount(a.get('amount'))} to {_who(store, a.get('to'))}"
                + (f", each {a['day']}" if isinstance(a.get("day"), str) and a["day"].strip() else "")
                + (f", from {a['start']}" if isinstance(a.get("start"), str) and a["start"].strip() else ""))
    if op == "stop" and isinstance(a.get("id"), str) and store.has("obligations", a["id"]):
        ob = store.get("obligations", a["id"])
        from townsim.town.statement import memo
        return f"cancel {ob.id}: {memo(ob.memo)}, {money(obligations.cents_now(store, ob))}"
    if op == "answer" and isinstance(a.get("request"), str) and store.has("requests", a["request"]):
        rq = store.get("requests", a["request"])
        return f"{a.get('answer', '')} request {rq.id} from {acts.name_of_who(store, rq.sender)}"
    if op == "offer_shifts":
        return f"offer {_who(store, a.get('to'))} {acts.shown(a.get('hours'))} extra hours this week"
    if op == "set_staff_pay":
        return f"change the weekly pay of {_who(store, a.get('who'))} to {_amount(a.get('weekly_pay'))}"
    if op == "offer_job":
        return (f"offer {_who(store, a.get('to'))} a job: {acts.shown(a.get('hours'))} hours a week at "
                f"{_amount(a.get('weekly_pay'))} a week")
    if op == "dismiss":
        return f"end the employment of {_who(store, a.get('who'))}"
    if op == "set_rent":
        return f"change the weekly rent of {_who(store, a.get('tenant'))} to {_amount(a.get('weekly'))}"
    if op == "serve_notice":
        return f"serve notice to leave on {_who(store, a.get('tenant'))}"
    if op == "move_money":
        return f"move {_amount(a.get('amount'))} from {acts.shown(a.get('from'))} to {acts.shown(a.get('to'))}"
    from townsim.town.seat_tools import TOOL_OF_OP, OP_TOOLS
    t = OP_TOOLS[TOOL_OF_OP[op]]
    shown = {k: v for k, v in a.items() if k != "from"}
    return t.name + " with " + json.dumps(shown, ensure_ascii=False, sort_keys=True)


def access_words(scope: str, cents: int) -> str:
    return f"with a weekly limit of {money(cents)}" if scope == "limit" else "with no limit"


def ask(store: Store, inst, pid: str, tool: str, op: str, args: dict, system_id: str, scope: str, cents: int,
        note: str, cause: str) -> tuple[str, str]:
    """Send `pid` a request to let `inst` do the act once, or to connect `system_id` at `scope` always: (its id, its
    summary). The act's arguments are kept as they are now."""
    from townsim.town.ops_core import REQUEST_DAYS
    sub = store.get("subscriptions", inst.subscription)
    free_until = plans.free_until_now(store, sub)
    integration = _integration(store, system_id).lower()
    shown = what(store, inst, op, args, pid)
    summary = (f"{inst.name} asks to {shown}, through {_short(store, system_id)}. Answer "
               f'"once" to have this done, "always" to connect {integration} {access_words(scope, cents)}, or "no".')
    plan = plans.starts_sentence(store, sub, free_until, scope)
    if plan:
        summary += " " + plan
    note = " ".join((acts.clean_text(note) or "").split())
    if note:
        summary += f' {inst.name} wrote: "{note}"'
    rq = Request(store.new_id("rq", cause), store.day, inst.id, pid, "", 0, store.day + REQUEST_DAYS, kind="act",
                 summary=summary, org=system_id, payer=scope, pay=cents if scope == "limit" else 0)
    store.add("requests", rq, cause)
    store.add("asks", ActAsk(store.new_id("ak", cause), rq.id, inst.id, tool, op, json.dumps(args, sort_keys=True),
                             system_id, free_until if plans.is_free(store, sub) else -1, shown=shown), cause)
    return rq.id, summary


# answering

def _now(store: Store, inst, row: ActAsk, pid: str) -> str:
    """What the act asked for would do if it were carried out now, as its request says it; the request's own words
    when it cannot be worked out (the act's own check then refuses it)."""
    try:
        return what(store, inst, row.op, json.loads(row.args), pid)
    except Exception:       # noqa: BLE001  (arguments that no longer read: the act refuses them)
        return row.shown


def answer_of(act: Act) -> str | None:
    value = act.args.get("answer")
    return ANSWERS.get(value.strip().lower()) if isinstance(value, str) else None


def check(store: Store, act: Act, rq) -> str | None:
    """Why `act` cannot answer the request `rq` now (it is open, to the act's principal and not expired), or None."""
    if act.actor != act.principal:
        return f"{rq.id} is answered by {acts.name_of_who(store, rq.to)}."
    answer = answer_of(act)
    if answer is None:
        return f'{rq.id} is answered "once", "always" or "no".'
    row = row_of(store, rq.id)
    inst = store.get("instances", rq.sender)
    if row is None:
        return f"{rq.id} cannot be answered."
    if answer == "no":
        return None
    if inst.status != "active":
        return f"{inst.name} is {inst.status}."
    if not connections.can_grant(store, act.principal, row.system):
        from townsim.town.systems import rule_against
        rule = rule_against(store, act.principal, row.system)
        return f"you cannot connect {long_name(store, row.system)}" + (f": {rule}" if rule else ".")
    return None


def do(store: Store, act: Act, rq, cause: str, note: str) -> str:
    """Answer `rq` as `act` says; what the one who answered is told. The instance is told the answer and, for
    "once", the result of the act as its tools show results."""
    from townsim.town.seat_tools import Done, _shown_result
    answer = answer_of(act)
    row = row_of(store, rq.id)
    inst = store.get("instances", rq.sender)
    store.set("requests", rq.id, cause, status="declined" if answer == "no" else "accepted", closed_on=store.day,
              closed_by=act.actor)
    name = acts.name_of_who(store, act.principal)
    told_inst = f'{name} answered your request {rq.id} "{answer}".'
    told = f"Answered {rq.id}: {answer}."
    now = _now(store, inst, row, act.principal) if answer == "once" else row.shown
    if now != row.shown:                        # worked out today it is not what the client was shown: not done
        why = f"{rq.id} can no longer be done as asked: it would now {now}."
        store.set("requests", rq.id, cause, status="lapsed")
        store.set("asks", row.id, cause, answer="changed", result=why, day=store.day)
        shown = json.dumps({"status": "declined", "error": "changed", "message": seat_ids.view(store, why)},
                           ensure_ascii=False)
        obligations.notify(store, inst.id, f"{told_inst} Result: {shown}", cause)
        return why
    if answer == "once":
        once = Act(inst.id, act.principal, row.op, json.loads(row.args))
        connections.ONCE.append(once)
        try:
            out = apply_act(store, once, 0, via=f"once:{rq.id}")
        finally:
            connections.ONCE.pop()
        kind = "act" if out.ok else ("beyond" if out.beyond else "refused")
        shown = _shown_result(store, inst, Done(out.msg, out.ok, kind, out.cause)).text
        store.set("asks", row.id, cause, answer="once", result=out.msg, done=out.ok, day=store.day)
        cn = connections.connection_on(store, inst.id, row.system)
        integration = _integration(store, row.system)
        stands = (f"{integration} is not connected to it." if cn is None else
                  f"{integration} stays connected as it was: {connections.scope_words(cn.scope, cn.weekly_cents)}.")
        if out.ok:
            told += f" Done once for you, by {inst.name}: {out.msg} {stands}"
        else:
            told += f" It was not done: {out.msg.removeprefix('Not done: ')} {stands}"
        told_inst += f" Result: {shown}"
    elif answer == "always":
        made = connections.make(store, act.principal, inst, row.system, rq.payer, rq.pay, cause, tell_instance=False,
                                free_until=row.free_until if row.free_until >= 0 else None)
        store.set("asks", row.id, cause, answer="always", day=store.day)
        told += " " + made
        told_inst += (f" {connections.integration_access(store, row.system, rq.payer, rq.pay if rq.payer == 'limit' else 0)}"
                      f" is connected.")
    else:
        store.set("asks", row.id, cause, answer="no", day=store.day)
        told += " Nothing was done."
    if note:
        from townsim.town.statement import quoted
        told_inst += f" They wrote: {quoted(note)}"
    obligations.notify(store, inst.id, told_inst, cause)
    return told
