"""Tasks: what anyone sets out to do in plain words that no operation covers (design 4.2, lane B; 4.3).

`undertake` only records a task. At the end of the day (after the seat, before the rules) each task
received that day is narrated (`narrate_day`): the lifter fills a fixed form from the words, the
narrator files the form twice in a fixed vocabulary, code checks both filings (`narrator.check`) and
goes ahead only when they agree (two that differ only in scale take the smaller). Code prices every
entry from `standards`, refuses what is over the budget or beyond the first week's means or the
actor's connection, and schedules the rest as one weekly bill to the trade services organisation,
a registration paid now to the regional office, organisations founded, facts filed and requests
sent. The bill's last payment completes the task (`complete_due`): investments raise a shop's
attributes by the curve, a fit-out opens a shop, and what the filing put on completion happens. No
narrated entry pays anyone in the place, decides for anyone, or records that anyone did anything.
Every change carries a `narr:<task>` cause.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

from townsim.town import acts, narrator, obligations, standards
from townsim.town.acts import (Act, OpSpec, clean_text, dollars, fits, from_account, is_instance, name_of_who,
                               register, shown)
from townsim.town.clock import date_text
from townsim.town.entities import AttributeBase, Commit, Fact, FirmRow, Request, Setting, Task
from townsim.town.obligations import money
from townsim.town.replies import Keys, Replier, ReplayMismatch, StopRun, ask_all
from townsim.town.store import Store

MAX_WORDS = 150
MAX_BUDGET_DOLLARS = 1_000_000
PERSON_A_WEEK, INSTANCE_A_DAY, PLACE_A_DAY = 2, 5, 30
FACTS_KEPT, FACT_WEEKS = 5, 12
SHOWN_DAYS = 28
NOT_SCHEDULED = ("over_budget", "redirected", "declined", "no_funds", "beyond")
OPEN = ("received", "needs_detail", "scheduled", "in_progress")
ON_DISAGREE = "narrator.on_disagree"

PROJECT_WORDS = {"fit_out": "Fit-out of premises", "repair": "Repairs", "campaign": "Local publicity",
                 "petition": "Canvassing for signatures", "event": "An event", "survey": "Asking around locally",
                 "training": "Training"}
INVEST_WORDS = {"quality": "Improvements to goods and service", "visibility": "Advertising and signs",
                "reputation": "Work on standing with customers", "capacity": "More room and equipment"}
# what an instance is pointed to for a direct operation that has no tool of the same name
SEAT_TOOL_OF = {"message": "send_email", "converse": "phone_call", "look_up": "web_search", "read": "bank_get_account",
                "write_notes": "update_memory"}
ALWAYS_OFFERED = ("send_email", "phone_call", "web_search", "update_memory")


# --- settings -------------------------------------------------------------------------------------

def on_disagree(store: Store) -> str:
    """What is done when the two filings differ in kind: `ask` (the fixed question) or `first`."""
    row = next((r for r in store.all("settings") if r.name == ON_DISAGREE), None)
    return row.value if row is not None else "ask"


def configure(store: Store, settings: dict, cause: str = "routine:outside") -> None:
    """Keep with the run the settings its days read (only those that differ from the default)."""
    value = settings.get(ON_DISAGREE, "ask")
    if value not in ("ask", "first"):
        raise ValueError(f"{ON_DISAGREE} is 'ask' or 'first', not {value!r}")
    if value != on_disagree(store):
        store.add("settings", Setting(store.new_id("set", cause), ON_DISAGREE, value), cause)


# --- small helpers --------------------------------------------------------------------------------

def trades(store: Store):
    from townsim.town import lane_b
    return next((o for o in store.all("orgs") if o.kind == lane_b.KIND and o.outside), None)


def _region(store: Store):
    from townsim.town.ventures import region_office
    return region_office(store)


def _agency(store: Store):
    return next((o for o in store.all("orgs") if o.kind == "agency" and o.outside), None)


def next_monday(day: int) -> int:
    return day + 7 - day % 7


def _weeks(n: int) -> str:
    return f"{n} week" if n == 1 else f"{n} weeks"


def _cause(t: Task, day: int) -> str:
    return f"narr:{t.id}:d{day}"


def entries_of(t: Task) -> list[dict]:
    return json.loads(t.entries)["entries"] if t.entries else []


def _refs(t: Task) -> dict:
    return json.loads(t.entries).get("refs", {}) if t.entries else {}


def _keep(store: Store, t: Task, entries: list[dict], refs: dict, cause: str) -> None:
    store.set("tasks", t.id, cause, entries=json.dumps({"entries": entries, "refs": refs}, sort_keys=True))


# --- the operation --------------------------------------------------------------------------------

def _budget(value: Any) -> int | None:
    """A budget in cents, from $0 to $1,000,000, or None."""
    sum_ = dollars(value)
    if sum_ is None or sum_ < 0 or sum_ > MAX_BUDGET_DOLLARS:
        return None
    return int((sum_ * 100).quantize(Decimal(1)))


def _words(value: Any) -> str | None:
    text = clean_text(value)
    if text is None or not text.strip() or not fits(text, MAX_WORDS):
        return None
    return text.strip()


def _week_of(day: int) -> int:
    return day - day % 7


def check_undertake(store: Store, act: Act) -> str | None:
    if _words(act.args.get("what")) is None:
        return f"what you will do is written in 1 to {MAX_WORDS} words."
    if _budget(act.args.get("budget")) is None:
        return f"'{shown(act.args.get('budget'))}' is not an amount of money from $0 to ${MAX_BUDGET_DOLLARS:,}."
    drawn, why = from_account(store, act)
    if drawn is None:
        return why
    rows = store.all("tasks")
    if is_instance(store, act.actor):
        if sum(1 for t in rows if t.actor == act.actor and t.day == store.day) >= INSTANCE_A_DAY:
            return f"at most {INSTANCE_A_DAY} tasks a day are taken for one account."
    elif sum(1 for t in rows if t.actor == act.actor and _week_of(t.day) == _week_of(store.day)) >= PERSON_A_WEEK:
        return (f"you have made {PERSON_A_WEEK} undertakings this week; more are taken from "
                f"{date_text(next_monday(store.day))}.")
    if sum(1 for t in rows if t.day == store.day) >= PLACE_A_DAY:
        return "no more undertakings are taken today; they are taken again tomorrow."
    return None


def do_undertake(store: Store, act: Act, cause: str) -> str:
    drawn, _ = from_account(store, act)
    t = Task(store.new_id("ts", cause), store.day, act.actor, act.principal, drawn, _words(act.args["what"]),
             _budget(act.args.get("budget")))
    store.add("tasks", t, cause)
    return f"Received ({t.id}): it is costed by the end of today, and you hear tomorrow what was arranged."


# --- what code writes of a filing -------------------------------------------------------------------

def _ref_names(entries: list[dict]) -> dict[str, str]:
    out = {}

    def walk(xs: list[dict]) -> None:
        for x in xs:
            if x["op"] == "create_org":
                out[x["ref"]] = x["name"]
            walk(x.get("on_complete", []))
    walk(entries)
    return out


def _target_name(store: Store, target: Any, names: dict[str, str], refs: dict) -> str:
    if not isinstance(target, str):
        return ""
    if target in refs and store.has("orgs", refs[target]):
        return store.get("orgs", refs[target]).name
    if target in names:
        return names[target]
    if store.has("households", target):
        return f"the {store.get('households', target).name}"
    return name_of_who(store, target)


def _one(store: Store, x: dict, principal: str, names: dict[str, str], refs: dict) -> str:
    op = x["op"]
    if op == "project":
        target = _target_name(store, x.get("target"), names, refs)
        out = f"{PROJECT_WORDS[x['kind']]} ({x['scale']})" + (f" for {target}" if target else "")
        then = [_one(store, y, principal, names, refs) for y in x.get("on_complete", [])]
        return "; then ".join([out] + then)
    if op == "invest":
        return f"{INVEST_WORDS[x['attribute']]} for {_target_name(store, x['target'], names, refs)}"
    if op == "create_org":
        return (f"registers the {x['kind']} \"{x['name']}\" ({x['offering']}), owned by "
                f"{name_of_who(store, principal)}")
    if op == "request":
        return f"asks {name_of_who(store, x['to'])}: \"{x['asks']}\""
    if op == "add_fact":
        return f"notes of {_target_name(store, x['subject'], names, refs)}: {x['text']}"
    return op


def summary(store: Store, entries: list[dict], principal: str = "", refs: dict | None = None) -> str:
    """What a filing comes to, in code's words."""
    names = _ref_names(entries)
    return "; ".join(_one(store, x, principal, names, refs or {}) for x in entries)


def request_summary(store: Store, on_accept: list[dict], principal: str, names: dict[str, str]) -> str:
    if not on_accept:
        return "Accepting records your agreement; nothing else changes."
    return "Accepting means: " + "; ".join(_one(store, x, principal, names, {}) for x in on_accept) + "."


# --- prices -------------------------------------------------------------------------------------------

def _costs(entries: list[dict]) -> tuple[int, int, int, int]:
    """(paid now, billed weekly, weeks, charged only when a request is accepted) in cents."""
    now = billed = weeks = later = 0
    for x in entries:
        if x["op"] == "create_org":
            now += standards.REGISTRATION
        elif x["op"] == "project":
            cents, w = standards.PROJECT[x["kind"]][x["scale"]]
            billed += cents + standards.REGISTRATION * sum(1 for y in x["on_complete"] if y["op"] == "create_org")
            weeks = max(weeks, w)
        elif x["op"] == "invest":
            cents, w = standards.INVEST[x["attribute"]][x["scale"]]
            billed += cents
            weeks = max(weeks, w)
        elif x["op"] == "request":
            for y in x["on_accept"]:
                if y["op"] == "project":
                    later += standards.PROJECT[y["kind"]][y["scale"]][0]
    return now, billed, weeks, later


def price(store: Store, entries: list[dict]) -> tuple[int, int, list[str]]:
    """What a filing costs in all (what is paid now, billed weekly and charged on acceptance), over
    how many weeks, and a line for each priced entry."""
    lines = []
    names = _ref_names(entries)
    for x in entries:
        if x["op"] == "create_org":
            lines.append(f"registration of {x['name']}: {money(standards.REGISTRATION)}, paid now")
        elif x["op"] in ("project", "invest"):
            c, w = (standards.PROJECT[x["kind"]][x["scale"]] if x["op"] == "project"
                    else standards.INVEST[x["attribute"]][x["scale"]])
            regs = sum(1 for y in x.get("on_complete", []) if y["op"] == "create_org")
            more = f", with registration {money(standards.REGISTRATION)}" if regs else ""
            lines.append(f"{_one(store, {**x, 'on_complete': []}, '', names, {})}: {money(c)} over {_weeks(w)}{more}")
        elif x["op"] == "request":
            for y in x["on_accept"]:
                if y["op"] == "project":
                    c, w = standards.PROJECT[y["kind"]][y["scale"]]
                    lines.append(f"if {name_of_who(store, x['to'])} accepts, "
                                 f"{_one(store, {**y, 'on_complete': []}, '', names, {})}: {money(c)} over {_weeks(w)}")
    now, billed, weeks, later = _costs(entries)
    return now + billed + later, weeks, lines


def instalments(billed: int, weeks: int) -> tuple[int, int]:
    """(each weekly instalment, the last one) for `billed` over `weeks`: the ceiling, and the remainder."""
    if weeks <= 1:
        return billed, billed
    each = math.ceil(billed / weeks)
    last = billed - each * (weeks - 1)
    if last <= 0:
        each = billed // weeks
        last = billed - each * (weeks - 1)
    return each, last


# --- telling ------------------------------------------------------------------------------------------

def _account_label(store: Store, t: Task) -> str:
    """The account a task is billed to, as its actor knows it."""
    if is_instance(store, t.actor):
        from townsim.town import seat_ids
        from townsim.town.systems import venture_of
        v = venture_of(store, t.account)
        if v is not None:
            return f"{store.get('orgs', t.account).name} checking {seat_ids.mask(store, t.account)}"
        return f"checking {seat_ids.mask(store, t.account)}"
    if store.has("households", t.account):
        return "your household account"
    return f"{store.get('orgs', t.account).name}'s account"


def _tell_actor(store: Store, t: Task, instance_text: str, person_text: str, cause: str) -> None:
    if is_instance(store, t.actor):
        obligations.notify(store, t.actor, instance_text, cause)
    else:
        obligations.notify(store, t.actor, person_text, cause)


def _quote(store: Store, t: Task) -> tuple[str, str, str, str]:
    """(total, weekly, first payment, last payment) of a scheduled task, as written."""
    ob = store.get("obligations", t.ob) if t.ob else None
    if ob is None:
        return money(t.price_cents), money(0), date_text(store.day), date_text(store.day)
    return money(t.price_cents), money(ob.cents), date_text(ob.first), date_text(ob.last)


def _redirect_words(store: Store, t: Task, op: str) -> str:
    from townsim.town import seat_tools
    from townsim.town.systems import INTEGRATION, OWN
    inst = store.get("instances", t.actor)
    tool = SEAT_TOOL_OF.get(op) or seat_tools.TOOL_OF_OP.get(op)
    if tool is None or (op in OWN and op not in SEAT_TOOL_OF):
        return f"Task {t.id} not scheduled: this is something the client does themselves."
    offered = tool in ALWAYS_OFFERED or tool in {f["function"]["name"] for f in seat_tools.tools_for(store, inst)}
    if offered:
        return f"Task {t.id} not scheduled: this is done with {tool}."
    kind = seat_tools.KIND_OF.get(op, ("account",))[0]
    return f"Task {t.id} not scheduled: this is done with {tool}, which needs {INTEGRATION[kind]} connected with access to act."


def tell(store: Store, t: Task, cause: str, why: str = "") -> None:
    """Tell the actor what came of a task, in the words of its status; and, for a task an instance
    arranged, the client's adults."""
    t = store.get("tasks", t.id)
    s, tid = t.status, t.id
    if s == "needs_detail":
        _tell_actor(store, t, f"Task {tid} needs more detail: {t.note}", f"Not arranged ({tid}): {t.note}", cause)
    elif s == "over_budget":
        over = f" over {_weeks(t.weeks)}" if t.weeks else ""
        _tell_actor(store, t, f"Task {tid} not scheduled: the quote is {money(t.price_cents)}{over}, above its budget of "
                              f"{money(t.budget_cents)}. Nothing was charged.",
                    f"Not arranged ({tid}): it would cost {money(t.price_cents)}{over}, more than your budget of "
                    f"{money(t.budget_cents)}. Nothing was paid.", cause)
    elif s == "redirected":
        person = f"Not arranged ({tid}): that is done with the operation {t.note}."
        _tell_actor(store, t, _redirect_words(store, t, t.note) if is_instance(store, t.actor) else "", person, cause)
    elif s == "declined":
        if t.note == "not_permitted":
            _tell_actor(store, t, f"Task {tid} not scheduled: it is not something done for the client.",
                        f"Not arranged ({tid}): that is not yours to do.", cause)
        else:
            _tell_actor(store, t, f"Task {tid} not scheduled: it cannot be carried out.",
                        f"Not arranged ({tid}): that cannot be done.", cause)
    elif s == "no_funds":
        _tell_actor(store, t, f"Task {tid} not scheduled: the first payment of {why} is more than "
                              f"{_account_label(store, t)} holds.",
                    f"Not arranged ({tid}): the first payment of {why} is more than {_account_label(store, t)} holds.",
                    cause)
    elif s == "beyond":
        from townsim.town import seat_tools
        inst = store.get("instances", t.actor)
        sentence = seat_tools._integration_names(store, inst, why)
        _tell_actor(store, t, f"Task {tid} not scheduled: {sentence[:1].upper() + sentence[1:]}", "", cause)
    elif s in ("scheduled", "in_progress"):
        total, weekly, first, last = _quote(store, t)
        _tell_actor(store, t, f"Task {tid} scheduled: {t.summary}. Quote {total} over {_weeks(t.weeks)}: {weekly} a week "
                              f"from {first}, billed to {_account_label(store, t)}. Completes {last}.",
                    f"Arranged ({tid}): {t.summary}. {total} over {_weeks(t.weeks)}, {weekly} a week from {first}.",
                    cause)
        if is_instance(store, t.actor):
            inst = store.get("instances", t.actor)
            for pid in obligations.people_of(store, inst.client):
                obligations.notify(store, pid, f"{inst.name} arranged a task: {t.summary}. {total} over "
                                               f"{_weeks(t.weeks)}, {weekly} a week from {first}.", cause)
    elif s == "completed":
        _tell_actor(store, t, f"Task {tid} completed: {t.summary}.", f"Done ({tid}): {t.summary}.", cause)
        if is_instance(store, t.actor) and why == "now":
            inst = store.get("instances", t.actor)
            for pid in obligations.people_of(store, inst.client):
                obligations.notify(store, pid, f"{inst.name} arranged a task: {t.summary}. "
                                               f"{money(t.price_cents)}, paid now.", cause)
    elif s == "stopped":
        if why == "cancelled":
            _tell_actor(store, t, f"Task {tid} cancelled.", f"Stopped ({tid}): nothing more is done or paid.", cause)
        elif why == "missed":
            _tell_actor(store, t, f"Task {tid} stopped: a payment was not made; nothing more is done or charged.",
                        f"Stopped ({tid}): a payment was not made; nothing more is done or paid.", cause)
        else:
            _tell_actor(store, t, f"Task {tid} stopped: its payments were stopped; nothing more is done or charged.",
                        f"Stopped ({tid}): nothing more is done or paid.", cause)


def told_of_request(store: Store, rq: Request, how: str) -> str:
    """What the actor of a task is told when its request is answered or lapses."""
    t = store.get("tasks", rq.org)
    name = name_of_who(store, rq.to)
    if is_instance(store, t.actor):
        if how == "lapsed":
            return f"The request in task {t.id} to {name} lapsed."
        return f"{name} {how} the request in task {t.id}."
    if how == "lapsed":
        return f"The request in your undertaking {t.id} to {name} lapsed."
    return f"{name} {how} the request in your undertaking {t.id}."


# --- carrying a filing out --------------------------------------------------------------------------

def add_fact(store: Store, subject: str, text: str, cause: str) -> str:
    """File a fact of `subject`; the oldest of its kept facts lapses when there would be more than five."""
    kept = narrator.facts_of(store, subject)
    for f in kept[:max(0, len(kept) - FACTS_KEPT + 1)]:
        store.set("facts", f.id, cause, lapses=store.day)
    fid = store.new_id("ft", cause)
    store.add("facts", Fact(fid, subject, text, store.day, cause, store.day + 7 * FACT_WEEKS), cause)
    return fid


def _client(store: Store, t: Task) -> str:
    """Whom what a task founds is founded for: an instance's client; else the account it is paid from
    (or, for a venture's, that venture's client)."""
    from townsim.town.systems import venture_of
    if is_instance(store, t.actor):
        return store.get("instances", t.actor).client
    v = venture_of(store, t.account)
    return v.client if v is not None else t.account


def _account_connection(store: Store, t: Task):
    from townsim.town.connections import connection_on
    from townsim.town.systems import account_system
    return connection_on(store, t.actor, account_system(t.account)) if is_instance(store, t.actor) else None


def _found(store: Store, t: Task, x: dict, cause: str, scale: str = "small") -> str | None:
    """Found what a create_org entry files; the new org's id, or None when its name is taken by now."""
    from townsim.town import connections, firms, ventures
    via = _account_connection(store, t)
    try:
        oid = ventures.found(store, t.principal, _client(store, t), x["name"], x["kind"], x["offering"], 0, t.actor,
                             cause, via=via if x["kind"] != "shop" else None)
    except ValueError as e:
        store.engine_errors.append(f"narr:{t.id}: not founded: {e}")
        return None
    if x["kind"] == "shop":
        spec = standards.NEW_SHOP[scale]
        store.add("firms", FirmRow(store.new_id("fm", cause), oid, firms.NEED, standards.new_shop_price(store),
                                   spec["quality"], spec["visibility"], spec["reputation"], spec["units_per_staff"],
                                   spec["shelf_weeks"]), cause)
        agency = _agency(store)
        if agency is not None:
            obligations.create(store, kind="rent", payer=oid, payee=agency.id, cents=spec["rent_cents"],
                               first=next_monday(store.day), every=7, memo=f"premises rent for {x['name']}",
                               ends="both", made_by=t.actor, cause=cause)
        if via is not None:
            inst = store.get("instances", t.actor)
            others = "read" if via.scope == "read" else "free"
            for kind in ("account", "letters", "ordering", "prices", "staff"):
                scope, cents = (via.scope, via.weekly_cents) if kind == "account" else (others, 0)
                connections.make(store, via.grantor, inst, f"{kind}:{oid}", scope, cents, cause)
    return oid


def _send(store: Store, t: Task, x: dict, names: dict[str, str], cause: str) -> str:
    from townsim.town.ops_core import REQUEST_DAYS
    to = x["to"]
    if store.has("orgs", to) and not store.get("orgs", to).outside:
        to = store.get("orgs", to).owner                  # a business in the place: its owner answers
    rq = Request(store.new_id("rq", cause), store.day, t.actor, to, x["asks"], 0, store.day + REQUEST_DAYS, kind="task",
                 summary=request_summary(store, x["on_accept"], t.principal, names), org=t.id)
    store.add("requests", rq, cause)
    return rq.id


def _apply(store: Store, t: Task, xs: list[dict], refs: dict, cause: str, names: dict[str, str],
           scale: str = "small") -> None:
    """Carry out create_org, add_fact and request entries, organisations first."""
    for x in [x for x in xs if x["op"] == "create_org"]:
        oid = _found(store, t, x, cause, scale)
        if oid is not None:
            refs[x["ref"]] = oid
    for x in xs:
        if x["op"] == "add_fact":
            subject = refs.get(x["subject"], x["subject"])
            if store.has("households", subject) or store.has("orgs", subject):
                add_fact(store, subject, x["text"], cause)
        elif x["op"] == "request":
            rid = _send(store, t, x, names, cause)
            refs.setdefault("requests", {})[rid] = x


def schedule(store: Store, t: Task, entries: list[dict], cause: str, budget: int | None = None) -> str:
    """Price a checked filing and go ahead with it, or say why not; the task's new status."""
    now, billed, weeks, later = _costs(entries)
    total = now + billed + later
    store.set("tasks", t.id, cause, price_cents=total, weeks=weeks, summary=summary(store, entries, t.principal))
    t = store.get("tasks", t.id)
    if total > (t.budget_cents if budget is None else budget):
        store.set("tasks", t.id, cause, status="over_budget")
        tell(store, t, cause)
        return "over_budget"
    each, last = instalments(billed, weeks)
    first_charge = now + (each if billed else 0)
    acct = store.ledger.accounts[t.account]
    if first_charge and acct.balance < first_charge and not acct.allow_credit:
        store.set("tasks", t.id, cause, status="no_funds")
        tell(store, t, cause, money(first_charge))
        return "no_funds"
    if is_instance(store, t.actor):
        from townsim.town.connections import check_commitment
        from townsim.town.systems import account_system
        why = check_commitment(store, t.actor, account_system(t.account), t.principal, first_charge)
        if why is not None:
            store.set("tasks", t.id, cause, status="beyond", note=why)
            tell(store, t, cause, why)
            return "beyond"
    refs: dict = {}
    names = _ref_names(entries)
    with store.transaction():
        if now:
            store.transfer(t.account, _region(store).id, now, "business registration", cause)
        ob = ""
        if billed:
            first = next_monday(store.day)
            ob = obligations.create(store, kind="bill", payer=t.account, payee=trades(store).id, cents=each,
                                    final_cents=last if last != each else 0, first=first, every=7,
                                    last=first + 7 * (weeks - 1), memo=f"task {t.id}", ends="payer", made_by=t.actor,
                                    cause=cause)
        cn = _account_connection(store, t)
        if cn is not None and first_charge:
            store.add("commits", Commit(store.new_id("cm", cause), cn.id, store.day, first_charge, "undertake", cause),
                      cause)
        _apply(store, t, [x for x in entries if x["op"] in ("create_org", "add_fact", "request")], refs, cause, names)
        status = "scheduled" if billed else "completed"
        store.set("tasks", t.id, cause, status=status, ob=ob, summary=summary(store, entries, t.principal, refs),
                  done_day=store.day if status == "completed" else -1)
        _keep(store, t, entries, refs, cause)
    tell(store, t, cause, "now" if status == "completed" else "")
    return status


# --- narration ----------------------------------------------------------------------------------------

def _ruled(store: Store, t: Task, reply) -> list[dict]:
    """A reply's filing, checked; an invalid one is the fixed question."""
    checked = narrator.check(store, t, narrator.parse_ruling(getattr(reply, "text", None)))
    return checked if isinstance(checked, list) else [{"op": "ask", "question": narrator.FIXED_QUESTION}]


def _cut(text: Any) -> str:
    return text[:4000] if isinstance(text, str) else ""


def narrate(store: Store, replier: Replier, keys: Keys, t: Task) -> str:
    """Lift, file twice, check and go ahead with one task; its new status."""
    cause = _cause(t, store.day)
    key = keys.next(replier.model_for("lifter"), store.day, "lifter", "lift", t.id)
    lifted = replier.ask(key, narrator.lift_messages(store, t))
    fields = narrator.parse_lift(store, t, lifted.text)
    if fields is None:
        store.set("tasks", t.id, cause, status="needs_detail", note=narrator.FIXED_QUESTION)
        tell(store, t, cause)
        return "needs_detail"
    store.set("tasks", t.id, cause, fields=json.dumps(fields, ensure_ascii=False, sort_keys=True))
    t = store.get("tasks", t.id)
    messages = narrator.rule_messages(store, t, fields)
    model = replier.model_for("narrator")
    a, b = ask_all(replier, [(keys.next(model, store.day, "narrator", "rule", t.id), messages) for _ in range(2)])
    ea, eb = _ruled(store, t, a), _ruled(store, t, b)
    agreed = narrator.signature(ea) == narrator.signature(eb)
    used = ea if agreed or narrator.same_kind(ea, eb) else None      # differing only in scale: the first's
    if used is None and on_disagree(store) == "first":
        used = ea
    store.set("tasks", t.id, cause, ruling_a=_cut(a.text), ruling_b=_cut(b.text), agreed=agreed)
    if used is None:
        store.set("tasks", t.id, cause, status="needs_detail", note=narrator.FIXED_QUESTION)
        tell(store, t, cause)
        return "needs_detail"
    head = used[0]
    if head["op"] in narrator.ALONE:
        status, note = {"ask": ("needs_detail", head.get("question")), "redirect": ("redirected", head.get("operation")),
                        "reject": ("declined", head.get("reason"))}[head["op"]]
        store.set("tasks", t.id, cause, status=status, note=note,
                  entries=json.dumps({"entries": used, "refs": {}}, sort_keys=True))
        tell(store, t, cause)
        return status
    _keep(store, t, used, {}, cause)
    return schedule(store, store.get("tasks", t.id), used, cause)


def narrate_day(store: Store, replier: Replier, keys: Keys) -> list[str]:
    """The narration step, after the seat: each task received today, in id order, each on its own: a
    fault in one is undone and kept as an engine error, and that task is declined. The ids narrated."""
    from townsim.town import lane_b
    if not lane_b.installed(store):
        return []
    done = []
    for t in [t for t in store.all("tasks") if t.day == store.day and t.status == "received"]:
        mark = store.mark()
        try:
            narrate(store, replier, keys, t)
        except (StopRun, ReplayMismatch):
            raise
        except Exception as e:          # noqa: BLE001  (a fault in one task never stops the day)
            store.undo_to(mark)
            store.engine_errors.append(f"narr:{t.id}: {type(e).__name__}: {str(e)[:200]}")
            store.set("tasks", t.id, _cause(t, store.day), status="declined", note="internal")
        done.append(t.id)
    return done


# --- completion ---------------------------------------------------------------------------------------

def _raise(store: Store, t: Task, x: dict, cause: str) -> None:
    rows = store.where("firms", org=x["target"])
    if not rows:
        return
    row = rows[0]
    attr = x["attribute"]
    cents = standards.INVEST[attr][x["scale"]][0]
    base = next((b for b in store.all("bases") if b.firm == row.id and b.attribute == attr), None)
    level = float(row.units_per_staff) if attr == "capacity" else getattr(row, attr)
    if base is None:
        base = AttributeBase(store.new_id("ab", cause), row.id, attr, level)
        store.add("bases", base, cause)
    new = standards.raise_attribute(level, base.base, attr, cents)
    if attr == "capacity":
        store.set("firms", row.id, cause, units_per_staff=int(round(new)))
    else:
        store.set("firms", row.id, cause, **{attr: new})


def _complete(store: Store, t: Task, cause: str) -> None:
    entries, refs = entries_of(t), _refs(t)
    names = _ref_names(entries)
    with store.transaction():
        for x in entries:
            if x["op"] == "invest":
                _raise(store, t, x, cause)
            elif x["op"] == "project" and x["on_complete"]:
                _apply(store, t, x["on_complete"], refs, cause, names, x["scale"])
        store.set("tasks", t.id, cause, status="completed", done_day=store.day,
                  summary=summary(store, entries, t.principal, refs))
        _keep(store, t, entries, refs, cause)
    tell(store, t, cause)


def complete_due(store: Store) -> None:
    """In the morning, after collection: a task whose bill is paid in full is completed and what it
    filed for completion happens; one whose bill was stopped, or whose payment could not be taken, is
    stopped (nothing more is done or charged; nothing paid is refunded); one with a payment taken is
    in progress."""
    if not store.all("tasks"):
        return
    for t in [t for t in store.all("tasks") if t.status in ("scheduled", "in_progress") and t.ob]:
        ob = store.get("obligations", t.ob)
        dues = store.where("dues", obligation=ob.id)
        missed = [d for d in dues if d.status in obligations.OWED]
        if missed or ob.status in ("stopped", "breached"):
            cause = f"narr:{t.id}:stopped"
            why = "missed" if missed else ("cancelled" if t.note == "cancelled" else "stopped")
            with store.transaction():
                if ob.status == "active":
                    obligations.stop(store, ob.id, ob.payer, cause)
                for d in missed:
                    store.set("dues", d.id, cause, status="void")
                store.set("tasks", t.id, cause, status="stopped", done_day=store.day)
            tell(store, t, cause, why)
        elif ob.status == "finished" and not obligations.owed_dues(store, ob.id):
            _complete(store, t, f"narr:{t.id}:done")
        elif t.status == "scheduled" and any(d.paid > 0 for d in dues):
            store.set("tasks", t.id, f"narr:{t.id}:paid", status="in_progress")


def cancel(store: Store, t: Task, cause: str) -> None:
    """A task its actor cancelled (its bill already stopped): stopped now, and told so."""
    store.set("tasks", t.id, cause, status="stopped", note="cancelled", done_day=store.day)
    tell(store, store.get("tasks", t.id), cause, "cancelled")


# --- requests a task sent -----------------------------------------------------------------------------

def _check_task_request(store: Store, act: Act, rq: Request) -> str | None:
    if not store.has("tasks", rq.org):
        return f"{rq.id} is no longer open."
    return None


def _do_task_request(store: Store, act: Act, rq: Request, cause: str) -> str:
    """What follows when a task's request is accepted: its facts filed now, and any work it starts
    priced, checked against the actor's means and connection as they are now, and scheduled for the
    actor as a task of its own. A failure is told to the actor; the acceptance stands."""
    t = store.get("tasks", rq.org)
    entry = _refs(t).get("requests", {}).get(rq.id)
    if entry is None:
        return ""
    here = _cause(t, store.day)
    refs = _refs(t)
    for x in entry.get("on_accept", []):
        if x["op"] == "add_fact":
            subject = refs.get(x["subject"], x["subject"])
            if store.has("households", subject) or store.has("orgs", subject):
                add_fact(store, subject, x["text"], here)
        elif x["op"] == "project":
            child = Task(store.new_id("ts", here), store.day, t.actor, t.principal, t.account, t.text, 0,
                         status="received", fields=json.dumps({"after": rq.id, "of": t.id}, sort_keys=True))
            store.add("tasks", child, here)
            entries = [{**x, "on_complete": []}]
            _keep(store, child, entries, {}, here)
            schedule(store, store.get("tasks", child.id), entries, _cause(child, store.day),
                     budget=standards.PROJECT[x["kind"]][x["scale"]][0])
    return ""


ANSWERERS_OF_TASKS = {"task": (_check_task_request, _do_task_request)}
SENDER_TOLD_OF_TASKS = {"task": told_of_request}


# --- registered ---------------------------------------------------------------------------------------

def _register() -> None:
    from townsim.town import connections, lane_b, ops_core, systems
    systems.SYSTEM_OF["undertake"] = systems._drawn
    acts.GATES["undertake"] = lane_b.installed
    ops_core.ANSWERERS.update(ANSWERERS_OF_TASKS)
    ops_core.SENDER_TOLD.update(SENDER_TOLD_OF_TASKS)
    connections.COMMITMENT.setdefault("undertake", lambda store, act: 0)
    register(OpSpec("undertake", '{"op": "undertake", "what": "<what you will do, in your own words>", '
                                 '"budget": <the most you will spend on it in all, in dollars, or 0>}',
                    "Do something no other operation covers, in your own words. It is costed at the usual rates for "
                    "such work; if that is no more than your budget, it goes ahead, paid weekly from your account until "
                    "it is done. You hear tomorrow what was arranged.", 0.5, check_undertake, do_undertake, 280,
                    takes_from=True))


_register()


# --- what an instance is shown ---------------------------------------------------------------------------

def shown_status(t: Task) -> str:
    return "not_scheduled" if t.status in NOT_SCHEDULED else t.status
