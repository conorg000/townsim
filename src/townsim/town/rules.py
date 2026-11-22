"""Rules: things that keep running (design 4.4).

Anyone can set a rule on a system they control or are connected to: on each day its condition on
figures they can read holds, at the end of the day, it does one operation for them. Code runs every
rule in a step of its own after the seat (`run_rules`). Every condition is read from the store as it
stands at the start of that step, before any rule acts, so one rule's act never sets off another
the same day; and each act goes through `apply_act` with the cause `auto:<rule>:...`, so it is
checked, like any act, against the access its installer holds on that day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from townsim.town import acts, obligations
from townsim.town.acts import Act, OpSpec, apply_act, clean_text, dollars, is_instance, register, savings_of, shown
from townsim.town.clock import DAYS, dow, weekday
from townsim.town.entities import Rule
from townsim.town.obligations import money
from townsim.town.store import Store

ACTIONS = ("order_stock", "set_price", "pay", "move_money", "message")
NUMBERS = {"order_stock": ("baskets", "units"), "set_price": ("price",), "pay": ("amount",), "move_money": ("amount",)}
UNITS = ("baskets", "units")
MAX_RULES, MAX_PER_ACTION, KEPT_RUNS, MAX_TIMES, NAME_CHARS = 10, 3, 5, 5, 60
SUSPEND_AFTER = 3
WHY_CHARS = 120


# --- figures --------------------------------------------------------------------------------

def _firm(store: Store, account: str):
    rows = store.where("firms", org=account)
    return rows[0] if rows else None


def _last_till(store: Store, account: str):
    f = _firm(store, account)
    if f is None:
        return None
    tills = [t for t in store.all("tills") if t.firm == f.id]
    return max(tills, key=lambda t: t.week) if tills else None


def _cents(c: int | None) -> Decimal | None:
    return None if c is None else Decimal(c) / 100


def _balance(store: Store, account: str) -> Decimal | None:
    return _cents(store.balance(account)) if store.ledger.has(account) else None


def _savings(store: Store, account: str) -> Decimal | None:
    return _balance(store, savings_of(account)) if store.has("households", account) else None


def _stock(store: Store, account: str) -> Decimal | None:
    f = _firm(store, account)
    return Decimal(sum(f.stock_units)) if f is not None else None


def _till(field: Callable[[Any], int]) -> Callable[[Store, str], Decimal | None]:
    def of(store: Store, account: str) -> Decimal | None:
        if _firm(store, account) is None:
            return None
        t = _last_till(store, account)
        return Decimal(field(t)) if t is not None else Decimal(0)
    return of


def _on_order(store: Store, account: str) -> Decimal | None:
    f = _firm(store, account)
    if f is None:
        return None
    return Decimal(f.order_units if f.order_supplier else f.last_units)


def _price(store: Store, account: str) -> Decimal | None:
    f = _firm(store, account)
    return _cents(f.price) if f is not None else None


def _lowest_other(store: Store, account: str) -> Decimal | None:
    f = _firm(store, account)
    others = [x.price for x in store.all("firms") if f is not None and x.id != f.id and x.need == f.need]
    return _cents(min(others)) if others else None


def _rent_arrears(store: Store, account: str) -> Decimal | None:
    from townsim.town import facts
    if not store.has("households", account):
        return None
    lords = set(store.get("households", account).members) | {account}
    total = 0
    for t in store.all("households"):
        if t.landlord in lords and t.id != account:
            total += sum(facts.arrears(store, o.id)[0] for o in store.where("obligations", payer=t.id)
                         if o.kind == "rent" and o.status in obligations.SCHEDULED)
    return _cents(total)


def _applications(store: Store, account: str) -> Decimal | None:
    if not store.has("households", account):
        return None
    members = set(store.get("households", account).members)
    banks = {o.id for o in store.all("orgs") if o.kind == "bank" and o.contact in members}
    return Decimal(sum(1 for r in store.where("requests", status="open") if r.kind == "loan" and r.payee in banks))


def _members(store: Store, account: str) -> Decimal | None:
    from townsim.town import ventures
    v = ventures.of_org(store, account)
    return Decimal(len(ventures.members(store, v.id))) if v is not None else None


@dataclass(frozen=True)
class Figure:
    kind: str                  # the kind of system it is read from
    money: bool                # in dollars (else a count)
    read: Callable[[Store, str], Decimal | None]


FIGURES: dict[str, Figure] = {
    "balance": Figure("account", True, _balance),
    "savings_balance": Figure("account", True, _savings),
    "stock": Figure("ordering", False, _stock),
    "sold_last_week": Figure("ordering", False, _till(lambda t: t.sold)),
    "turned_away_last_week": Figure("ordering", False, _till(lambda t: t.turned_stock + t.turned_capacity)),
    "spoiled_last_week": Figure("ordering", False, _till(lambda t: t.spoiled)),
    "on_order": Figure("ordering", False, _on_order),
    "price": Figure("prices", True, _price),
    "lowest_other_price": Figure("prices", True, _lowest_other),
    "rent_arrears": Figure("lets", True, _rent_arrears),
    "open_applications": Figure("loans", False, _applications),
    "members": Figure("members", False, _members),
}


def readable(store: Store, who: str) -> list:
    """The systems `who` can read: an instance's connected at any scope; a person's own."""
    from townsim.town import connections, systems
    if is_instance(store, who):
        found = [systems.system(store, c.system) for c in connections.active(store, who)]
        return [s for s in found if s is not None]
    return systems.grantable(store, who) if store.has("people", who) else []


def _reads(store: Store, who: str, name: str, account: str) -> bool:
    """Whether `who` can read the figure `name` of `account`."""
    fig = FIGURES.get(name) if isinstance(name, str) else None
    if fig is None:
        return False
    if name == "savings_balance" and not acts.has_savings(store, account):
        return False
    return any(s.kind == fig.kind and (s.owner == account or fig.kind == "loans") for s in readable(store, who))


def figures_of(store: Store, who: str, account: str | None = None) -> list[str]:
    """The figures `who` can read: of `account`, or of any of the accounts they can read."""
    accounts = [account] if account is not None else list(dict.fromkeys(s.owner for s in readable(store, who)
                                                                          if s.kind != "loans"))
    if account is None and store.has("people", who):
        accounts.append(store.get("people", who).household)
    return [n for n in FIGURES if any(_reads(store, who, n, a) for a in accounts)]


def value_of(store: Store, name: str, account: str) -> Decimal | None:
    return FIGURES[name].read(store, account)


# --- reading what a rule is written as -------------------------------------------------------

def _number(value: Any) -> Decimal | None:
    """A number as written (an int, a finite float, or a string of digits), exactly; or None."""
    return dollars(value)


def _home(store: Store, who: str) -> str:
    return store.get("instances", who).client if is_instance(store, who) else store.get("people", who).household


def _account(store: Store, act: Act, action: str, args: dict, when: dict) -> str:
    """The account a rule's figures are read from: the owner of the system its action uses; for a
    letter, the owner of the first system the installer can read that holds its figure; else the
    installer's client or household."""
    from townsim.town.systems import SYSTEM_OF
    if action in SYSTEM_OF:
        try:
            sid = SYSTEM_OF[action](store, Act(act.actor, act.principal, action, args))
        except Exception:        # noqa: BLE001  (arguments the mapping cannot read)
            sid = None
        if sid:
            return sid.split(":", 1)[1]
    fig = FIGURES.get(when.get("figure")) if isinstance(when.get("figure"), str) else None
    if fig is not None and fig.kind != "loans":
        found = next((s.owner for s in readable(store, act.actor) if s.kind == fig.kind), None)
        if found is not None:
            return found
    return _home(store, act.actor)


def _no_figure(store: Store, who: str, name: Any) -> str:
    if is_instance(store, who):
        return f"'{shown(name)}' is not a figure of a connected integration."
    yours = ", ".join(figures_of(store, who)) or "none"
    return f"'{shown(name)}' is not one of your figures. Yours are: {yours}."


def _check_when(store: Store, who: str, when: Any, account: str) -> str | None:
    if not isinstance(when, dict) or not ({"figure", "weekday"} & set(when)):
        return "a condition is a figure, a weekday, or both."
    if "figure" in when:
        if not _reads(store, who, when.get("figure"), account):
            return _no_figure(store, who, when.get("figure"))
        if when.get("is") not in ("below", "above"):
            return f"'{shown(when.get('is'))}' is not \"below\" or \"above\"."
        if _number(when.get("value")) is None:
            return f"'{shown(when.get('value'))}' is not a number."
    if "weekday" in when and weekday(when.get("weekday")) is None:
        return f"'{shown(when.get('weekday'))}' is not a day of the week."
    return None


def _formula(value: Any) -> bool:
    return isinstance(value, dict)


def _check_do(store: Store, who: str, action: Any, args: Any, account: str) -> str | None:
    if action not in ACTIONS:
        return f"'{shown(action)}' is not one of: {', '.join(ACTIONS)}."
    if not isinstance(args, dict):
        return "the action's arguments were not written in a form that can be read."
    worked = [k for k, v in args.items() if _formula(v)]
    if len(worked) > 1:
        return "at most one argument is worked out from a figure."
    for k in worked:
        f = args[k]
        if k not in NUMBERS.get(action, ()):
            return f"'{shown(k)}' is not a number the action takes."
        if not _reads(store, who, f.get("figure"), account):
            return _no_figure(store, who, f.get("figure"))
        times, plus = _number(f.get("times", 1)), _number(f.get("plus", 0))
        if times is None or not 0 <= times <= MAX_TIMES:
            return f"'times' is a number from 0 to {MAX_TIMES}."
        if plus is None:
            return "'plus' is a number."
    return None


def _do_of(act: Act) -> tuple[Any, dict]:
    do = act.args.get("do")
    if not isinstance(do, dict):
        return None, {}
    op = do.get("op")
    return (op.strip().lower() if isinstance(op, str) else op), {k: v for k, v in do.items() if k != "op"}


def _name(value: Any) -> str | None:
    text = clean_text(value)
    if text is None or not text.strip() or "\n" in text.strip() or len(text.strip()) > NAME_CHARS:
        return None
    return text.strip()


def live(store: Store, who: str) -> list[Rule]:
    """`who`'s rules that are active or suspended, in id order."""
    return [r for r in store.all("rules") if r.installer == who and r.status in ("active", "suspended")]


def _check_set(store: Store, act: Act) -> str | None:
    if _name(act.args.get("name")) is None:
        return f"a rule's name is one line of at most {NAME_CHARS} characters."
    action, args = _do_of(act)
    if action is None:
        return "'do' is one operation, written as in the list."
    when = act.args.get("when") if isinstance(act.args.get("when"), dict) else {}
    account = _account(store, act, action, args, when) if action in ACTIONS else _home(store, act.actor)
    why = _check_do(store, act.actor, action, args, account)
    if why is not None:
        return why
    why = _check_when(store, act.actor, act.args.get("when"), account)
    if why is not None:
        return why
    if store.has("people", act.actor):
        spec = acts.REGISTRY.get(action)
        if spec is None or (spec.who != "all" and spec.who not in acts.positions(store, act.actor)):
            return f"'{action}' is not an operation of yours."
    mine = live(store, act.actor)
    if len(mine) >= MAX_RULES:
        return f"there are {MAX_RULES} active rules, the most kept."
    if sum(1 for r in mine if r.action == action) >= MAX_PER_ACTION:
        return f"there are {MAX_PER_ACTION} active rules that do {action}, the most kept."
    return None


def _do_set(store: Store, act: Act, cause: str) -> str:
    action, args = _do_of(act)
    when = act.args["when"]
    when = {k: when[k] for k in ("figure", "is", "value", "weekday") if k in when}
    account = _account(store, act, action, args, when)
    rid = store.new_id("rl", cause)
    rule = Rule(rid, act.actor, act.principal, account, _name(act.args["name"]), json.dumps(when, sort_keys=True),
                action, json.dumps(args, sort_keys=True), made_on=store.day)
    store.add("rules", rule, cause)
    return f"Set ({rid}): {rule.name}: when {when_words(rule)}, {do_words(store, rule)}; checked at the end of each day."


def _rule_of(store: Store, act: Act) -> Rule | None:
    rid = act.args.get("rule") if isinstance(act.args, dict) else None
    if not isinstance(rid, str) or not store.has("rules", rid):
        return None
    r = store.get("rules", rid)
    return r if r.status != "deleted" else None


def can_delete(store: Store, act: Act, r: Rule) -> bool:
    """The installer, anyone who controls the account it reads, or an instance for its client."""
    if r.installer == act.actor:
        return True
    if is_instance(store, act.actor):
        from townsim.town.systems import client_of
        return client_of(store, f"account:{r.account}") == store.get("instances", act.actor).client
    return store.has("people", act.principal) and acts.controls(store, act.principal, r.account)


def _check_delete(store: Store, act: Act) -> str | None:
    r = _rule_of(store, act)
    if r is None or not can_delete(store, act, r):
        return f"there is no rule with the id '{shown(act.args.get('rule'))}'."
    return None


def _do_delete(store: Store, act: Act, cause: str) -> str:
    r = _rule_of(store, act)
    store.set("rules", r.id, cause, status="deleted")
    return f"Deleted: rule {r.id} ({r.name})."


def _system_of_set(store: Store, act: Act) -> str | None:
    """The system a rule's action uses."""
    from townsim.town.systems import SYSTEM_OF
    action, args = _do_of(act) if isinstance(act.args, dict) else (None, {})
    if action not in SYSTEM_OF:
        return None
    return SYSTEM_OF[action](store, Act(act.actor, act.principal, action, args))


def _system_of_delete(store: Store, act: Act) -> str | None:
    from townsim.town.systems import SYSTEM_OF
    r = _rule_of(store, act)
    if r is None or r.action not in SYSTEM_OF:
        return None
    return SYSTEM_OF[r.action](store, Act(act.actor, act.principal, r.action, json.loads(r.args)))


# --- words ------------------------------------------------------------------------------------

def _figure_words(name: str, value: Any) -> str:
    n = _number(value)
    if n is None:
        return shown(value)
    if FIGURES[name].money:
        return money(int((n * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    return f"{n.normalize():f}"


def when_words(r: Rule) -> str:
    """`stock below 50`, `weekday Sunday`, or both joined by `and`."""
    when = json.loads(r.when)
    parts = []
    if "figure" in when:
        parts.append(f"{when['figure']} {when['is']} {_figure_words(when['figure'], when['value'])}")
    if "weekday" in when:
        parts.append(f"weekday {DAYS[weekday(when['weekday'])]}")
    return " and ".join(parts)


def _arg_words(value: Any) -> str:
    if _formula(value):
        times, plus = _number(value.get("times", 1)), _number(value.get("plus", 0))
        out = f"{value.get('figure')}"
        if times is not None and times != 1:
            out += f" × {times.normalize():f}"
        if plus:
            out += f" + {plus.normalize():f}"
        return out
    return shown(value)


def do_words(store: Store, r: Rule) -> str:
    """What a rule does, in plain words."""
    a = json.loads(r.args)
    if r.action == "pay":
        return f"pay {shown(a.get('to'))} {_money_words(a.get('amount'))}"
    if r.action == "move_money":
        return f"move {_money_words(a.get('amount'))} from {shown(a.get('from'))} to {shown(a.get('to'))}"
    if r.action == "order_stock":
        return f"order {_arg_words(a.get('baskets', a.get('units')))} baskets a week from {shown(a.get('supplier'))}"
    if r.action == "set_price":
        what = f"the weekly fee of {shown(a['for'])}" if a.get("for") else "the price"
        return f"set {what} to {_money_words(a.get('price'))}"
    return f"write to {shown(a.get('to'))}"


def _money_words(value: Any) -> str:
    if _formula(value):
        return _arg_words(value)
    n = _number(value)
    return money(int((n * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))) if n is not None else shown(value)


# --- the day's step ---------------------------------------------------------------------------

def _holds(store: Store, r: Rule) -> bool:
    when = json.loads(r.when)
    if "weekday" in when and weekday(when["weekday"]) != dow(store.day):
        return False
    if "figure" in when:
        have = value_of(store, when["figure"], r.account) if when["figure"] in FIGURES else None
        want = _number(when.get("value"))
        if have is None or want is None:
            return False
        if when.get("is") == "below" and not have < want:
            return False
        if when.get("is") == "above" and not have > want:
            return False
    return True


def _worked(store: Store, r: Rule) -> tuple[dict | None, str]:
    """The rule's arguments with a figure worked out (units to whole, dollars to cents), or None and why."""
    args = json.loads(r.args)
    for k, v in list(args.items()):
        if not _formula(v):
            continue
        have = value_of(store, v.get("figure"), r.account) if v.get("figure") in FIGURES else None
        times, plus = _number(v.get("times", 1)), _number(v.get("plus", 0))
        if have is None or times is None or plus is None:
            return None, f"the figure {shown(v.get('figure'))} cannot be read today."
        n = have * times + plus
        if k in UNITS:
            args[k] = int(n.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        else:
            args[k] = str(n.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return args, ""


def _acting(store: Store, who: str) -> bool:
    if is_instance(store, who):
        return store.get("instances", who).status == "active"
    return store.has("people", who)


def _tell(store: Store, r: Rule, why: str, suspended: bool, cause: str) -> None:
    if is_instance(store, r.installer):
        from townsim.town import seat_tools
        inst = store.get("instances", r.installer)
        text = f"Rule {r.id} did not run: {seat_tools._integration_names(store, inst, why)}"
        if suspended:
            text += f" Rule {r.id} is suspended after three declined runs."
    else:
        text = f"Your rule {r.id} ({r.name}) did not run: {why}"
        if suspended:
            text += f" Your rule {r.id} is stopped after three days on which it did not run."
    obligations.notify(store, r.installer, text, cause)


def run_rules(store: Store, n: int) -> int:
    """The rules step, at the end of the day: every active rule of an installer who can act today
    (a person; an instance that is active), in id order, has its condition and its arguments read
    from the store as it is now, before any rule acts; then each whose condition held is done by its
    installer, as of today (an instance's for the person whose connection it acts under today), with
    the cause `auto:<rule>:...`. A refusal is counted and its installer told; three in a row suspend
    the rule. Returns the next act number."""
    rows = [r for r in store.all("rules") if r.status == "active" and _acting(store, r.installer)
            and not any(x.startswith(f"{store.day}:") for x in r.runs)]          # at most once a day
    due = []
    for r in rows:
        try:
            if _holds(store, r):
                due.append((r.id, *_worked(store, r)))
        except Exception as e:      # noqa: BLE001  (a fault in one rule never stops the day)
            store.engine_errors.append(f"rule:{r.id}:d{store.day}: {type(e).__name__}: {str(e)[:200]}")
    for rid, args, why in due:
        r = store.get("rules", rid)
        cause = f"routine:rules:{rid}"
        ok = False
        if args is not None:
            try:
                act = _act(store, r, args)
            except Exception as e:      # noqa: BLE001
                store.engine_errors.append(f"rule:{rid}:d{store.day}: {type(e).__name__}: {str(e)[:200]}")
                act, why = None, "that could not be carried out."
            if act is not None:
                out = apply_act(store, act, n, via=f"auto:{rid}")
                n += 1
                ok, why = out.ok, out.msg.removeprefix("Not done: ").strip()
        if ok:
            store.set("rules", rid, cause, fails=0, runs=(r.runs + [f"{store.day}:done"])[-KEPT_RUNS:])
            continue
        fails = r.fails + 1
        suspended = fails >= SUSPEND_AFTER
        store.set("rules", rid, cause, fails=fails, runs=(r.runs + [f"{store.day}:declined:{why[:WHY_CHARS]}"])[-KEPT_RUNS:],
                  **({"status": "suspended"} if suspended else {}))
        _tell(store, r, why, suspended, cause)
    return n


def _act(store: Store, r: Rule, args: dict) -> Act:
    """The act a rule does today: a person's own; an instance's for the grantor of its connection
    on the action's system today (its own, for a letter)."""
    if not is_instance(store, r.installer):
        return Act(r.installer, r.installer, r.action, args)
    if r.action == "message":
        return Act(r.installer, r.installer, r.action, args)
    from townsim.town import seat_tools
    from townsim.town.connections import connection_on
    from townsim.town.seat_texts import client_people
    from townsim.town.systems import SYSTEM_OF
    inst = store.get("instances", r.installer)
    args = seat_tools.op_args(store, inst, r.action, args)
    for pid in [r.principal] + [p for p in client_people(store, inst) if p != r.principal]:
        try:
            sid = SYSTEM_OF[r.action](store, Act(inst.id, pid, r.action, args))
        except Exception:        # noqa: BLE001  (arguments the mapping cannot read: the act refuses them)
            sid = None
        cn = connection_on(store, inst.id, sid) if sid else None
        if cn is not None:
            return Act(r.installer, cn.grantor, r.action, args)
    return Act(r.installer, r.principal, r.action, args)


# --- registered ----------------------------------------------------------------------------------

YOUR_FIGURES = "<your figures>"


def _fill_figures(store: Store, pid: str) -> str:
    return acts._alternatives(figures_of(store, pid)) if figures_of(store, pid) else '"<none>"'


def _register() -> None:
    from townsim.town import lane_b, systems
    acts.FILLED[YOUR_FIGURES] = _fill_figures
    systems.SYSTEM_OF.update({"set_rule": _system_of_set, "delete_rule": _system_of_delete})
    acts.GATES.update({"set_rule": lane_b.installed, "delete_rule": lane_b.installed})
    for spec in (
        OpSpec("set_rule", '{"op": "set_rule", "name": "<a name>", "when": {"figure": ' + YOUR_FIGURES
               + ', "is": "below" | "above", "value": <number>} or {"weekday": "<day>"}, "do": {<one of: order_stock, '
               'set_price, pay, move_money, message, written as here>}}',
               "Set a rule that does one operation for you on each day its condition holds, at the end of the day.",
               0.25, _check_set, _do_set, 270),
        OpSpec("delete_rule", '{"op": "delete_rule", "rule": "<id>"}', "Delete one of your rules.", 0.1,
               _check_delete, _do_delete, 271),
    ):
        register(spec)


_register()



# --- what people see ---------------------------------------------------------------------------

SECTION_LINES = 6


def section(store: Store, pid: str) -> tuple[str, list[str]] | None:
    """YOUR RULES: each active or suspended rule on `pid`'s own accounts or set by them, one line."""
    from townsim.town.clock import date_text
    mine = set(acts.accounts_of(store, pid))
    rows = [r for r in store.all("rules") if r.status in ("active", "suspended")
            and (r.installer == pid or r.account in mine)]
    if not rows:
        return None
    lines = []
    for r in rows[:SECTION_LINES]:
        last = r.runs[-1] if r.runs else ""
        if last:
            day, _, rest = last.partition(":")
            ran = f"last ran {date_text(int(day))}: {'done' if rest == 'done' else 'not done'}"
        else:
            ran = "not run yet"
        stopped = "; stopped" if r.status == "suspended" else ""
        lines.append(f"{r.id} {r.name}: when {when_words(r)}, {do_words(store, r)}; set by "
                     f"{acts.name_of_who(store, r.installer)}; {ran}{stopped}")
    if len(rows) > SECTION_LINES:
        lines.append(f"({len(rows) - SECTION_LINES} more)")
    return "YOUR RULES", lines
