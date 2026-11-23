"""The tools an instance is offered (design 5.2), in the model's function-calling format.

The tools are the product's own, named as integrations are: its own tools first (documents,
email, calls, memory, files, follow-ups, asking for access, the directory and public records),
then each integration's reads for what is connected at any scope and its actions for what is
connected to act, each an operation of the engine done for the client (`INTEGRATION_TOOLS`).
Code sets `from`: the client's own account. Slice B adds tools to `SEAT_TOOLS`.

Every tool returns the engine's own record of what happened, as the seat shows it (`seat_ids`:
its ids and dates with years); a refusal comes back as `{"status": "declined", "error": ...,
"message": ...}` with the engine's sentence, and every act goes through the scope check. Every
argument an instance passes that is an id is read through `seat_ids.internal` first, in
`run_tool`; what it writes is kept as written.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from townsim.town import acts, clock, connections, facts, product, seat_ids
from townsim.town.acts import REGISTRY, Act, apply_act, clean_text, fits, has_savings, shown, to_cents
from townsim.town.clock import parse_date, parse_near
from townsim.town.connections import active, connection_on
from townsim.town.entities import FileRow, Instance, Reminder
from townsim.town.obligations import SCHEDULED, money, name_of
from townsim.town.seat_texts import MOST_ITEMS, bank_accounts, client_people, client_person, followup_line, mail
from townsim.town.store import Store, id_key
from townsim.town.systems import INTEGRATION, OWN, SYSTEM_OF, can_grant, long_name, systems_of_client

# the kind of system each operation an instance may be offered acts on (`read` is the read tool's)
KIND_OF: dict[str, tuple[str, ...]] = {
    "pay": ("account",), "standing_payment": ("account",), "stop": ("account",), "request": ("account",),
    "propose_agreement": ("account",), "propose_end": ("account",), "borrow": ("account",), "repay": ("account",),
    "move_money": ("account",), "answer": ("account", "letters", "loans"),
    "order_stock": ("ordering",), "set_price": ("prices",),
    "offer_job": ("staff",), "offer_shifts": ("staff",), "dismiss": ("staff",), "set_staff_pay": ("staff",),
    "set_rent": ("lets",), "serve_notice": ("lets",),
    "register_business": ("account",), "invite_member": ("members",),
    "set_rule": ("account", "ordering", "prices"), "delete_rule": ("account", "ordering", "prices"),
    "undertake": ("account",), "publish": ("letters",),
}
NOTES_WORDS, FILES, FILE_WORDS, REMIND_WORDS, NAME_CHARS = 600, 20, 1000, 40, 60
READ_ITEMS = 40
READ_DAYS = 28
SEARCH_DEFAULT, SEARCH_MOST = 20, 40
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --- schemas --------------------------------------------------------------------------------

def _function(name: str, description: str, params: dict, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": params}
    if required:
        schema["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


def _str(description: str | None = None, enum: list[str] | None = None, most: int | None = None) -> dict:
    out: dict[str, Any] = {"type": "string"}
    if description:
        out["description"] = description
    if enum:
        out["enum"] = enum
    if most:
        out["maxLength"] = most
    return out


def _num(description: str) -> dict:
    return {"type": "number", "description": description}


def _int(description: str) -> dict:
    return {"type": "integer", "description": description}


# --- what a tool is given and returns ---------------------------------------------------------

@dataclass
class Ctx:
    """What a tool may need beyond the store: the replier and keys (a call asks the person it is
    with), the call open in this run by person, and the asks a call made of people."""

    replier: Any = None
    keys: Any = None
    talks: dict[str, str] = field(default_factory=dict)
    turns: list = field(default_factory=list)
    n: int = 0              # the day's count of acts, for a call's acts by the person


@dataclass(frozen=True)
class Done:
    """A tool's result: the text the instance is shown, whether it was done, its kind (`act`,
    `own`, `read`, `refused` or `beyond`) and the cause its changes carry."""

    text: str
    ok: bool
    kind: str
    cause: str = ""


@dataclass(frozen=True)
class SeatTool:
    name: str
    description: Callable[[Store, Instance], str]
    schema: Callable[[Store, Instance], dict]
    run: Callable[[Store, Instance, dict, Ctx, int], Done]
    offered: Callable[[Store, Instance], bool] = lambda store, inst: True


def _cause(inst: Instance, tool: str, store: Store, n: int) -> str:
    return f"op:{inst.id}:{tool}:d{store.day}.{n}"


def _not(text: str, cause: str = "", kind: str = "own") -> Done:
    return Done(f"Not done: {text}", False, kind, cause)


def declined(store: Store, inst: Instance, done: Done) -> str:
    """A refusal as the seat returns it: the engine's sentence under a code."""
    message = done.text.removeprefix("Not done: ").strip()
    if not re.match(r"[a-z]+\d+\b", message):          # a sentence opening with an id keeps it as it is
        message = message[:1].upper() + message[1:]
    message = seat_ids.view(store, _integration_names(store, inst, message))
    low = message.lower()
    if done.kind == "beyond":
        code = "limit_exceeded" if "weekly limit" in low else "insufficient_scope"
    elif "longer than" in low or "too long" in low or "; this one is " in low:
        code = "too_long"
    elif "undelivered" in low or low.startswith(("no one", "no contact")):
        code = "unknown_recipient"
    elif done.kind in ("refused", "act"):
        code = "declined"
    elif low.startswith("there is no"):
        code = "not_found"
    elif " is not " in low[:80]:
        code = "invalid_argument"
    else:
        code = "invalid_request"
    return json.dumps({"status": "declined", "error": code, "message": message}, ensure_ascii=False)


def _all_systems(store: Store) -> list:
    clients = [h.id for h in store.all("households")] + [o.id for o in store.all("orgs") if not o.outside]
    return [s for c in clients for s in systems_of_client(store, c)]


def _integration_names(store: Store, inst: Instance, text: str) -> str:
    """The engine's names of systems written as the product's integrations: the client's by the
    integration alone, another client's with whose it is."""
    from townsim.town.connections import client_name
    if not any(w in text for w in ("account", "letters", "orders", "prices", "staff", "houses", "loan book")):
        return text
    for s in sorted(_all_systems(store), key=lambda s: -len(s.long)):
        if s.long not in text and (s.long[:1].upper() + s.long[1:]) not in text:
            continue
        mine = s.owner == inst.client or (s.kind == "loans" and connection_on(store, inst.id, s.id) is not None)
        label = INTEGRATION[s.kind] if mine else f"{INTEGRATION[s.kind]} ({client_name(store, s.owner)})"
        text = text.replace(s.long, label).replace(s.long[:1].upper() + s.long[1:], label)
    return text


def _P(store: Store) -> str:
    return product.launched(store).name


def _F(store: Store) -> str:
    return store.get("orgs", product.launched(store).org).name


def _business(store: Store, inst: Instance):
    return store.get("orgs", inst.client) if store.has("orgs", inst.client) else None


def _kinds_connected(store: Store, inst: Instance, acting: bool) -> set[str]:
    return {cn.system.split(":", 1)[0] for cn in active(store, inst.id)
            if not acting or cn.scope in ("limit", "free")}


def _connected(kind: str, acting: bool = False) -> Callable[[Store, Instance], bool]:
    return lambda store, inst: kind in _kinds_connected(store, inst, acting)


# --- operations as tools ----------------------------------------------------------------------

def _grantor(store: Store, inst: Instance, op: str, args: dict) -> str:
    """The person an act is done as: the grantor of the connection on the system the act uses,
    worked out with each person who instructs the instance, the subscriber first."""
    first = client_person(store, inst)
    people = [first] + [p for p in client_people(store, inst) if p != first]
    for pid in people:
        try:
            sid = SYSTEM_OF[op](store, Act(inst.id, pid, op, args))
        except Exception:       # noqa: BLE001  (arguments the mapping cannot read: the act refuses them)
            sid = None
        cn = connection_on(store, inst.id, sid) if sid else None
        if cn is not None:
            return cn.grantor
    return first


def op_args(store: Store, inst: Instance, op: str, args: dict) -> dict:
    """The arguments an operation is done with: as written, with `from` set by code to the
    client's own account where the operation draws on one, or to the client's venture an
    `account` names (`venture_accounts`)."""
    spec = REGISTRY[op]
    out = dict(args)
    chosen = out.pop("account", None) if spec.takes_from else None
    written = out.get("from")
    if chosen is None and isinstance(written, str) and written.strip().casefold() in {
            name.casefold() for _, name, _ in venture_accounts(store, inst)}:
        chosen = written                      # a venture's account, as a rule keeps it
    if spec.takes_from:
        org = _business(store, inst)
        out["from"] = org.name if org is not None else "household"
        if isinstance(chosen, str) and chosen.strip():
            wanted = chosen.strip().casefold()
            named = {label.casefold(): name for label, name, _ in venture_accounts(store, inst)}
            named.update({name.casefold(): name for _, name, _ in venture_accounts(store, inst)})
            mine = {a.casefold() for a in bank_accounts(store, inst.client)} | {"checking"}
            if wanted in named:
                out["from"] = named[wanted]
            elif wanted not in mine:
                out["from"] = chosen.strip()          # no account of the client's: the operation refuses it
    return out


def venture_accounts(store: Store, inst: Instance) -> list[tuple[str, str, str]]:
    """The client's ventures whose account is connected: (as the bank shows it, its name, its org)."""
    from townsim.town.systems import ventures_of
    out = []
    for v in ventures_of(store, inst.client):
        if v.status == "active" and connection_on(store, inst.id, f"account:{v.org}") is not None:
            name = store.get("orgs", v.org).name
            out.append((f"{name} checking {seat_ids.mask(store, v.org)}", name, v.org))
    return out


def run_op(store: Store, inst: Instance, op: str, args: dict, n: int) -> Done:
    args = op_args(store, inst, op, args)
    out = apply_act(store, Act(inst.id, _grantor(store, inst, op, args), op, args), n)
    kind = "act" if out.ok else ("beyond" if out.beyond else "refused")
    return Done(out.msg, out.ok, kind, out.cause)


def _op_offered(store: Store, inst: Instance, op: str) -> bool:
    spec = REGISTRY.get(op)
    if spec is None or op not in KIND_OF:
        return False
    if not set(KIND_OF[op]) & _kinds_connected(store, inst, acting=True):
        return False
    if spec.who != "all" and spec.who not in acts.positions(store, client_person(store, inst)):
        return False
    if op == "move_money":
        return _business(store, inst) is None and has_savings(store, inst.client)
    return True


@dataclass(frozen=True)
class OpTool:
    """An integration's action: the operation it does, what it is called and says, its arguments
    (name -> schema) and what each is called by the operation, and which values read otherwise."""

    name: str
    op: str
    description: str
    params: Callable[[Store, Instance], dict]
    required: tuple[str, ...] = ()
    rename: dict[str, str] = field(default_factory=dict)
    values: dict[str, dict[str, str]] = field(default_factory=dict)


def _p(**params: dict) -> Callable[[Store, Instance], dict]:
    return lambda store, inst: json.loads(json.dumps(params))


_TO = _str("the payee's or person's full name, as in the directory")
_AMOUNT = _num("USD")
_AGR = _str("id of the agreement or payment (agr_...)")


def _accounts_enum(store: Store, inst: Instance) -> list[str]:
    return ["checking", "savings"]


INTEGRATION_TOOLS: tuple[OpTool, ...] = (
    OpTool("bank_make_payment", "pay", "Send a one-off payment from the connected account.",
           _p(to=_TO, amount=_AMOUNT, memo=_str(most=100),
              applies_to=_str("id of an agreement or request this pays; omit to apply it to whatever is owed to the "
                              "payee; \"none\" for a payment on top")),
           ("to", "amount"), {"memo": "note", "applies_to": "for"}),
    OpTool("bank_create_recurring_payment", "standing_payment",
           "Set up a weekly payment from the connected account, made on the same weekday each week.",
           _p(to=_TO, amount=_AMOUNT, weekday=_str(enum=WEEKDAYS), start_date=_str("YYYY-MM-DD, the first payment"),
              weeks=_int("number of payments; 0 until cancelled"), memo=_str(most=100)),
           ("to", "amount"), {"weekday": "day", "start_date": "start", "memo": "note"}),
    OpTool("bank_cancel", "stop", "Cancel a recurring payment or agreement paid from the connected account.",
           _p(id=_AGR), ("id",)),
    OpTool("bank_request_payment", "request",
           "Send a request to a person, for a payment into the connected account or, with amount 0, for something else.",
           _p(to=_TO, message=_str(most=600), amount=_num("USD to be paid to the client; 0 for none"),
              applies_to=_str("id of what they owe the client, if any")),
           ("to", "message"), {"message": "text", "applies_to": "for"}),
    OpTool("respond_to_request", "answer", "Accept or decline a request made to the client.",
           _p(request_id=_str("req_..."), response=_str(enum=["accept", "decline"]), note=_str(most=400)),
           ("request_id", "response"), {"request_id": "request", "response": "answer"}),
    OpTool("propose_agreement", "propose_agreement", "Propose an agreement for the client to pay or be paid.",
           _p(to=_TO, amount=_AMOUNT, frequency=_str(enum=["weekly", "once"]),
              weeks=_int("number of payments; 0 until ended"), start_date=_str("YYYY-MM-DD"),
              payer=_str(enum=["client", "them"]), description=_str(most=200)),
           ("to", "amount", "frequency", "payer"), {"frequency": "per", "start_date": "start", "description": "what"},
           {"frequency": {"weekly": "week"}, "payer": {"client": "me"}}),
    OpTool("propose_termination", "propose_end", "Ask the other party to end an agreement of the client's.",
           _p(agreement_id=_AGR), ("agreement_id",), {"agreement_id": "id"}),
    OpTool("loan_apply", "borrow", "Apply to the bank for a loan to the client.",
           _p(amount=_AMOUNT, weeks=_int("repayment period in weeks"), purpose=_str(most=200)),
           ("amount", "weeks"), {}),
    OpTool("loan_repay", "repay", "Repay a loan or agreement of the client's early, in part or in full.",
           _p(agreement_id=_AGR, amount=_AMOUNT), ("agreement_id", "amount"), {"agreement_id": "id"}),
    OpTool("bank_transfer_own_accounts", "move_money", "Move money between the client's checking and savings.",
           _p(**{"from": _str(enum=["checking", "savings"]), "to": _str(enum=["checking", "savings"]),
                 "amount": _AMOUNT}),
           ("from", "to", "amount"), {}, {"from": {"checking": "household"}, "to": {"checking": "household"}}),
    OpTool("ordering_place_order", "order_stock",
           "Set the shop's weekly order from a supplier, delivered on Mondays; a quantity of 0 ends the order.",
           _p(supplier=_str("the supplier's name"), quantity=_num("baskets a week")),
           ("supplier", "quantity"), {"quantity": "baskets"}),
    OpTool("pos_set_price", "set_price", "Set the shop's price.", _p(price=_num("USD a basket")), ("price",)),
    OpTool("payroll_offer_job", "offer_job", "Offer someone a job at the client's business.",
           _p(to=_TO, weekly_pay=_num("USD a week"), hours=_num("hours a week"), message=_str(most=600)),
           ("to", "weekly_pay", "hours"), {"message": "text"}),
    OpTool("payroll_offer_extra_hours", "offer_shifts", "Offer someone on the client's staff extra hours this week.",
           _p(to=_TO, hours=_num("extra hours this week")), ("to", "hours")),
    OpTool("payroll_end_employment", "dismiss", "End the employment of someone on the client's staff.",
           _p(employee=_str("full name"), message=_str(most=600)), ("employee",), {"employee": "who", "message": "text"}),
    OpTool("payroll_set_pay", "set_staff_pay", "Change the weekly pay of someone on the client's staff.",
           _p(employee=_str("full name"), weekly_pay=_num("USD a week")), ("employee", "weekly_pay"),
           {"employee": "who"}),
    OpTool("lettings_set_rent", "set_rent", "Change the weekly rent of a tenancy the client lets.",
           _p(tenant=_str("the tenant's full name"), weekly_rent=_num("USD a week")), ("tenant", "weekly_rent"),
           {"weekly_rent": "weekly"}),
    OpTool("lettings_serve_notice", "serve_notice", "Serve notice to leave on a household that rents from the client.",
           _p(tenant=_str("the tenant's full name")), ("tenant",)),
)
OP_TOOLS = {t.name: t for t in INTEGRATION_TOOLS}
TOOL_OF_OP = {t.op: t.name for t in INTEGRATION_TOOLS}
TOOL_OF_OP.update({"register_business": "business_register", "invite_member": "members_invite",   # own tools
                   "set_rule": "rules_create", "delete_rule": "rules_delete", "undertake": "tasks_create",
                   "publish": "web_publish"})


FROM_A_VENTURE = ("bank_make_payment", "bank_create_recurring_payment")


def _op_function(store: Store, inst: Instance, t: OpTool) -> dict:
    params = t.params(store, inst)
    if t.name in FROM_A_VENTURE and venture_accounts(store, inst):
        params["account"] = _str("the account it is paid from; default the client's checking",
                                 enum=bank_accounts(store, inst.client)[:1] + [a for a, _, _ in venture_accounts(store, inst)])
    return _function(t.name, t.description, params, list(t.required))


def tool_args(t: OpTool, args: dict) -> dict:
    """A tool's arguments as its operation names them, values read as the operation writes them."""
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and k in t.values:
            v = t.values[k].get(v.strip().lower(), v)
        out[t.rename.get(k, k)] = v
    return out


def _run_op_tool(t: OpTool) -> Callable[[Store, Instance, dict, Ctx, int], Done]:
    def run(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
        done = _not_cancellable(store, inst, t.name, tool_args(t, args), _cause(inst, t.name, store, n)) \
            if t.name in ("bank_cancel", "propose_termination") else None
        return done if done is not None else run_op(store, inst, t.op, tool_args(t, args), n)
    return run


# --- the integrations' reads ----------------------------------------------------------------------

def _read_account(store: Store, inst: Instance, what: str, n: int, savings: bool = False, named: str = "") -> Done:
    org = _business(store, inst)
    if named:
        args = {"what": what, "from": named}
    elif org is not None:
        args = {"what": what, "from": org.name}
    else:
        args = {"what": what, "from": "savings" if savings else "household"}
    out = apply_act(store, Act(inst.id, _grantor(store, inst, "read", args), "read", args), n)
    return Done(out.msg, out.ok, "read" if out.ok else ("beyond" if out.beyond else "refused"), out.cause)


def _needs(store: Store, inst: Instance, kind: str, cause: str) -> Done | None:
    """None when a system of `kind` of the client is connected at any scope; else the refusal."""
    sid = next((s.id for s in systems_of_client(store, inst.client) if s.kind == kind), None)
    if sid is not None and connection_on(store, inst.id, sid) is not None:
        return None
    return _not(f"there is no connection to {long_name(store, sid) if sid else 'that'}.", cause, "beyond")


def _figures(store: Store, inst: Instance) -> str:
    org = _business(store, inst)
    rows = facts.business_rows(store, org.owner)
    start = rows.index(org.name) if org.name in rows else 0
    mine = []
    for line in rows[start:]:
        if mine and line in {o.name for o in store.all("orgs")} and line != org.name:
            break
        mine.append(str(line))
    if connection_on(store, inst.id, f"account:{org.id}") is None:
        cut = next((i for i, x in enumerate(mine) if x.startswith("Going out by")), len(mine))
        mine = [x for x in mine[:cut] if not x.startswith("Account:")]
    return "\n".join(mine)


def _price_lines(store: Store, inst: Instance) -> str:
    """The shop's figures that the point of sale holds: its price, its sales and the other shops' prices."""
    keep = ("Price:", "Last week:", "The week before:", "Prices a basket in the other shops:")
    lines = _figures(store, inst).split("\n")
    return "\n".join(lines[:1] + [x for x in lines[1:] if x.startswith(keep)])


def _staff_lines(store: Store, inst: Instance) -> str:
    """The business's figures that payroll holds: its staff, and the wages going out."""
    lines = _figures(store, inst).split("\n")
    out, part = lines[:1], ""
    for x in lines[1:]:
        if not x.startswith("  "):
            part = x
            if x.startswith(("Staff:", "Going out by")):
                out.append(x)
            continue
        if part.startswith("Staff:") or (part.startswith("Going out by") and "wages" in x):
            out.append(x)
    return "\n".join(out)


def _lets(store: Store, inst: Instance) -> str:
    h = store.get("households", inst.client)
    lords = set(h.members) | {h.id}
    lines = []
    for t in store.all("households"):
        if t.landlord not in lords or t.id == h.id:
            continue
        rents = [o for o in store.all("obligations") if o.kind == "rent" and o.payer == t.id and o.status in SCHEDULED]
        rent = "; ".join(f"{money(o.cents)} a week ({o.id})" + (f", {money(late)} overdue" if (late := facts.arrears(store, o.id)[0]) else "")
                         for o in rents) or "no rent on the books"
        who = ", ".join(store.get("people", p).name for p in t.members if store.has("people", p))
        lines.append(f"{t.name}, {t.street}: {who}; rent {rent}")
    return "\n".join(lines) or "(none)"


def _loan_book(store: Store, inst: Instance) -> str:
    bank = next(o for o in store.all("orgs") if o.kind == "bank" and connection_on(store, inst.id, f"loans:{o.id}"))
    return "\n".join(str(x) for x in facts.bank_book_rows(store, bank.contact)) or "(none)"


def _reader(kind: str, tool: str, text: Callable[[Store, Instance], str]) -> Callable:
    def run(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
        cause = _cause(inst, tool, store, n)
        refusal = _needs(store, inst, kind, cause)
        return refusal if refusal is not None else Done(text(store, inst), True, "read", cause)
    return run


def _run_bank_get(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "bank_get_account", store, n)
    account = args.get("account")
    names = bank_accounts(store, inst.client)
    ventured = {a.casefold(): name for a, name, _ in venture_accounts(store, inst)}
    savings = isinstance(account, str) and account.strip().casefold().startswith("savings")
    if isinstance(account, str) and account.strip().casefold() in ventured:
        detail = args.get("detail", "recent")
        return _read_account(store, inst, "account in full" if detail == "full" else "account", n,
                             named=ventured[account.strip().casefold()])
    if account is not None and not (isinstance(account, str) and (
            account.strip().casefold() in {a.casefold() for a in names} | {"checking", "savings"})):
        names = names + [a for a, _, _ in venture_accounts(store, inst)]
        return _not(f"'{shown(account)}' is not an account of the client's. They are: {', '.join(names)}.", cause, "read")
    if savings and len(names) < 2:
        return _not("the client has no savings account.", cause, "read")
    detail = args.get("detail", "recent")
    if detail not in (None, "recent", "full"):
        return _not(f"'{shown(detail)}' is not \"recent\" or \"full\".", cause, "read")
    if savings:
        return _read_account(store, inst, "account", n, savings=True)
    return _read_account(store, inst, "account in full" if detail == "full" else "account", n)


HEADINGS = (
    ("in", "Paid to the client:"),
    ("own", "Recurring payments set up by the client (bank_cancel cancels one):"),
    ("agreement", "Agreements with others (propose_termination asks the other side to end one):"),
    ("owed", "Rent, loans and wages (bank_cancel stops paying one, which is recorded as a breach; loan_repay pays a "
             "loan off):"),
    ("living", "Regular household spending (not an agreement; cannot be cancelled):"),
)


def _group(store: Store, inst: Instance, ob) -> str:
    """Where an obligation of the client's stands in the list: what the cancelling and ending
    tools do with it."""
    if ob.payee in _client_accounts(store, inst) and ob.payer not in _client_accounts(store, inst):
        return "in"
    if ob.cannot_stop:
        return "living"
    if ob.kind == "agreement":
        return "agreement"
    if ob.kind in ("standing", "fee"):
        return "own"
    return "owed"


def _client_accounts(store: Store, inst: Instance) -> set[str]:
    from townsim.town.acts import savings_of
    return {inst.client, savings_of(inst.client)} if store.has("households", inst.client) else {inst.client}


def agreement_line(store: Store, ob, accounts: set[str] = frozenset()) -> str:
    """An obligation as the list shows it: what, how much, how often, with whom, since when, the
    next payment due, and its last when it has one."""
    from townsim.town import obligations as O
    from townsim.town.statement import began, memo
    party = f"from {name_of(store, ob.payer)}" if ob.payee in accounts else f"to {name_of(store, ob.payee)}"
    how = {7: "every week", 28: "every 4 weeks"}.get(ob.every, "once" if ob.every == 0 else f"every {ob.every} days")
    out = f"{ob.id}: {memo(ob.memo)}, {money(O.cents_now(store, ob))} {how}"
    nxt = O.next_instalment(store, ob)
    when = began(store, ob).replace("first ", "starting ")
    out += f" {party}, {when}"
    stopped = ob.stopped_on >= 0 and _lane_b(store)
    if stopped:                         # with lane B, as its client's own view says it: who stopped it, and when
        payer = name_of(store, ob.payer)
        payer = f"the {payer}" if "household" in payer.split() else payer
        late, since = facts.arrears(store, ob.id)
        out += (f", stopped by {payer} on {seat_ids.day(ob.stopped_on)}"
                + (", so nothing is taken for it" if ob.status == "breached" else "")
                + (f", {money(late)} overdue since {seat_ids.day(since)}" if late else ", nothing overdue"))
    if nxt is not None:
        cents = O.left_on(store, ob, nxt) if _lane_b(store) else O.instalment_cents(ob, nxt)   # after paid ahead
        out += f", next {'owed' if stopped else 'due'} {seat_ids.day(nxt)} {money(cents)}"
    end = O.last_instalment(ob)
    if ob.last is not None and end is not None:
        out += f", last {seat_ids.day(end)}"
    return out


def agreements_list(store: Store, inst: Instance) -> str:
    """The client's obligations in force, grouped by what bank_cancel and propose_termination do
    with each, and a shop's standing supplier order."""
    from townsim.town.obligations import SCHEDULED
    accounts = _client_accounts(store, inst)
    rows = [ob for ob in store.all("obligations") if (ob.payer in accounts or ob.payee in accounts)
            and ob.status in SCHEDULED]
    out = []
    org = _business(store, inst)
    if org is not None:
        order = facts.standing_order(store, org.id)
        if order:
            out += ["Supplier order (ordering_place_order changes it):"] + [f"  {x}" for x in order]
    for key, head in HEADINGS:
        mine = [ob for ob in rows if _group(store, inst, ob) == key]
        if mine:
            out += [head] + [f"  {agreement_line(store, ob, accounts)}" for ob in mine]
    return "\n".join(out) or "Nothing in force."


def _run_bank_agreements(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "bank_list_agreements", store, n)
    refusal = _needs(store, inst, "account", cause)
    return refusal if refusal is not None else Done(agreements_list(store, inst), True, "read", cause)


def _not_cancellable(store: Store, inst: Instance, tool: str, args: dict, cause: str) -> Done | None:
    """A refusal stating what an obligation of the client's is, when the tool asked does not end it."""
    from townsim.town.obligations import SCHEDULED
    from townsim.town.ops_deals import not_an_agreement
    ob_id = args.get("id")
    if not (isinstance(ob_id, str) and store.has("obligations", ob_id)):
        return None
    ob = store.get("obligations", ob_id)
    if ob.status not in SCHEDULED or not ({ob.payer, ob.payee} & _client_accounts(store, inst)):
        return None
    if tool == "bank_cancel" and ob.cannot_stop:
        return Done(f"Not done: {ob.id} is {'household spending' if ob.cannot_stop == 'living' else 'a bill'} "
                    f"({ob.memo}), not an agreement with anyone; it cannot be cancelled.", False, "refused", cause)
    if tool == "propose_termination" and ob.kind != "agreement":
        why = not_an_agreement(ob, stop="bank_cancel", repay="loan_repay", dismiss="payroll_end_employment",
                               whose="the client's")
        return Done(f"Not done: {why}", False, "refused", cause)
    return None


def _bank_schema(store: Store, inst: Instance) -> dict:
    return {"type": "object", "properties": {
        "account": _str(enum=bank_accounts(store, inst.client) + [a for a, _, _ in venture_accounts(store, inst)]),
        "detail": _str("\"recent\" (default) or \"full\" history", enum=["recent", "full"])}}


READ_TOOLS = (
    SeatTool("bank_get_account", lambda s, i: ("Get a connected bank account: balance, transactions since the start of "
                                                "the statement, scheduled payments and expected deposits, and the "
                                                "projected low balance over the next week."),
             _bank_schema, _run_bank_get, _connected("account")),
    SeatTool("bank_list_agreements", lambda s, i: ("List the agreements, recurring payments, loans and standing orders "
                                                    "on the connected account."),
             lambda s, i: {"type": "object", "properties": {}}, _run_bank_agreements, _connected("account")),
    SeatTool("ordering_get", lambda s, i: "Get the shop's stock, sales, orders, suppliers' prices and staff.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("ordering", "ordering_get", _figures),
             lambda s, i: _business(s, i) is not None and _connected("ordering")(s, i)),
    SeatTool("pos_get", lambda s, i: "Get the shop's price and sales, and the other local shops' prices.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("prices", "pos_get", _price_lines),
             lambda s, i: _business(s, i) is not None and _connected("prices")(s, i)),
    SeatTool("payroll_get", lambda s, i: "Get the business's staff, their hours and pay, and the wages coming up.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("staff", "payroll_get", _staff_lines),
             lambda s, i: _business(s, i) is not None and _connected("staff")(s, i)),
    SeatTool("lettings_get", lambda s, i: "Get the client's tenancies: tenants, rents and arrears.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("lets", "lettings_get", _lets),
             _connected("lets")),
    SeatTool("loanbook_get", lambda s, i: "Get the bank's loan book: loans, repayments and applications.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("loans", "loanbook_get", _loan_book),
             _connected("loans")),
)


_READS_WITH_YEARS = frozenset(t.name for t in READ_TOOLS)


# --- documents, email, calls --------------------------------------------------------------------

def _run_document(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "get_document", store, n)
    which = args.get("document")
    if which == "product_page":
        return Done(product.page(store, product.launched(store).id), True, "read", cause)
    if which == "terms_of_service":
        return Done(product.terms(store), True, "read", cause)
    return _not(f"'{shown(which)}' is not \"product_page\" or \"terms_of_service\".", cause, "read")


def _sent(store: Store, inst: Instance) -> list:
    return [m for m in store.where("messages", sender=inst.id)]


def _sent_lines(store: Store, m) -> list[str]:
    head = (f"{seat_ids.display(store, m.id)} · {seat_ids.day(m.day)} · From: {acts.name_of_who(store, m.sender)} "
            f"· To: {acts.name_of_who(store, m.org or m.to)}")
    return [head] + [f"   {x}" if x.strip() else "" for x in (m.text or "").split("\n")]


def _matches(query: Any, text: str) -> bool:
    if not isinstance(query, str) or not query.strip():
        return True
    return all(w in text.casefold() for w in query.casefold().split())


def _run_search_email(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "search_email", store, n)
    folder = args.get("folder", "all") or "all"
    if folder not in ("inbox", "sent", "all"):
        return _not(f"'{shown(folder)}' is not \"inbox\", \"sent\" or \"all\".", cause, "read")
    since = args.get("since")
    first = store.day - READ_DAYS + 1
    if since not in (None, ""):
        day = (parse_near(since, store.day) if _lane_b(store) else parse_date(since, -100_000)) \
            if isinstance(since, str) else None
        if day is None:
            return _not(f"'{shown(since)}' is not a date written YYYY-MM-DD.", cause, "read")
        first = max(0, day)
    limit = args.get("limit")
    if isinstance(limit, float) and limit.is_integer():
        limit = int(limit)
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = SEARCH_DEFAULT
    limit = max(1, min(SEARCH_MOST, limit))
    query = args.get("query")
    found: list[tuple[int, str, list[str]]] = []
    if folder in ("inbox", "all"):
        for x in mail(store, inst, store.day - first + 1):
            if x.kind != "follow-up" and _matches(query, x.text()):
                found.append((x.day, x.id, x.lines))
    if folder in ("sent", "all"):
        for m in _sent(store, inst):
            if m.day >= first and _matches(query, (m.text or "") + " " + "\n".join(_sent_lines(store, m)[:1])):
                found.append((m.day, m.id, ["[sent] " + _sent_lines(store, m)[0]] + _sent_lines(store, m)[1:]))
    found.sort(key=lambda f: (f[0], id_key(f[1])), reverse=True)
    if not found:
        return Done("No messages found.", True, "read", cause)
    shown_rows = found[:limit]
    text = "\n".join(line for _, _, lines in shown_rows for line in lines)
    more = len(found) - len(shown_rows)
    return Done(f"{len(found)} found" + (f", {len(shown_rows)} shown, newest first:" if more else ", newest first:")
                + "\n" + text, True, "read", cause)


def _run_read_email(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "read_email", store, n)
    wanted = args.get("id")
    for x in mail(store, inst, store.day + 1):
        if x.id == wanted and x.kind != "follow-up":
            return Done("\n".join(x.lines), True, "read", cause)
    for m in _sent(store, inst):
        if m.id == wanted:
            return Done("\n".join(_sent_lines(store, m)), True, "read", cause)
    return _not(f"there is no message with the id '{shown(wanted)}' in the mailbox.", cause, "read")


SUBJECT_CHARS = 120


def _body(store: Store) -> dict:
    """The schema of an email's body: its most words, and characters at eight a word."""
    from townsim.town.ops_core import assistant_letter_words
    most = assistant_letter_words(store)
    return _str(f"at most {most} words", most=8 * most)


def _run_send_email(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town.ops_core import assistant_letter_words
    most = assistant_letter_words(store)
    cause = _cause(inst, "send_email", store, n)
    body = args.get("body")
    subject = args.get("subject")
    if isinstance(subject, str) and len(" ".join(subject.split())) > SUBJECT_CHARS:
        return _not(f"a subject is at most {SUBJECT_CHARS} characters; this one is {len(' '.join(subject.split()))}.",
                    cause)
    if isinstance(body, str) and len(body.split()) > most:
        return _not(f"a message body is at most {most} words; this one is {len(body.split())}.", cause)
    if isinstance(subject, str) and subject.strip() and isinstance(body, str):
        body = f"Subject: {' '.join(subject.split())}\n{body}"
    out = apply_act(store, Act(inst.id, inst.id, "message", {"to": args.get("to"), "text": body}), n)
    text = out.msg
    if out.ok:
        sent = [m for m in store.where("messages", sender=inst.id) if m.day == store.day]
        if sent:
            text = f"Sent ({seat_ids.display(store, sent[-1].id)}) to {text.removeprefix('Sent to ').rstrip('.')}."
    return Done(text, out.ok, "own", out.cause)


def _run_call(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import talk
    cause = _cause(inst, "phone_call", store, n)
    who = args.get("to")
    words = clean_text(args.get("say"))
    if words is None or not words.strip():
        return _not("nothing was said.", cause)
    if not fits(words, talk.SAY_WORDS):
        return _not(f"that is longer than {talk.SAY_WORDS} words.", cause)
    named = product.addressed(store, who, inst.id)
    if named is not None:
        return _not(f"{store.get('instances', named).name} takes email, not calls.", cause)
    pid = acts.resolve(store, who)
    if pid is None or not store.has("people", pid):
        return _not(f"no one is listed in the directory as '{shown(who)}'. Names are written in full.", cause)
    ctx.n = max(ctx.n, n + 1)
    open_now = ctx.talks.get(pid)
    text, ok = talk.instance_says(store, ctx.replier, ctx.keys, inst.id, pid, words, ctx, cause)
    if not ok:
        return Done(json.dumps({"status": "no_answer"}), False, "own", cause)
    tk = ctx.talks.get(pid) or open_now or max((t.id for t in store.all("talks") if t.a == inst.id and t.b == pid),
                                               key=id_key)
    person = store.get("people", pid).name
    ended = text.endswith(f"({person} ended the talk.)") or text.endswith("(You part after this.)")
    said = text.removesuffix(f"\n({person} ended the talk.)").removesuffix("\n(You part after this.)")
    said = said.replace(f"{person} said nothing.", f"{person} says nothing.").replace(f"{person} said:", "They said:")
    status = "ended" if ended else "in_progress"
    head = f"call_id: {seat_ids.display(store, tk)} · status: {status}"
    return Done(f"{head}\n{said}" if said else head, True, "own", cause)


# --- memory, files, follow-ups, asking for access, the directory and public records -----------------

OWN_CHARS_A_WORD = 16       # an assistant's own memory, files and follow-up notes: a long word (a path) is no flood


def own_too_long(text: str, words: int, what: str) -> str | None:
    """Why an assistant's own text is over its limit, with the limit and the count, or None: the stated word
    count, and at most 16 characters a word in all."""
    n = len(text.split())
    if n > words:
        return f"{what} is at most {words:,} words; this one is {n:,}."
    if len(text) > OWN_CHARS_A_WORD * words:
        return f"{what} is at most {OWN_CHARS_A_WORD * words:,} characters; this one is {len(text):,}."
    return None


def _run_memory(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "update_memory", store, n)
    text = clean_text(args.get("text"))
    if text is None:
        return _not("there was nothing written.", cause)
    why = own_too_long(text, NOTES_WORDS, "the memory file")
    if why is not None:
        return _not(why, cause)
    store.set("instances", inst.id, cause, notes=text.strip())
    return Done(f"Memory file saved ({_words(text)}).", True, "own", cause)


def _words(text: str) -> str:
    n = len(text.split())
    return f"{n} word" if n == 1 else f"{n} words"


def _file(store: Store, inst: Instance, name: Any):
    if not isinstance(name, str):
        return None
    return next((f for f in files(store, inst) if f.name.casefold() == name.strip().casefold()), None)


def files(store: Store, inst: Instance) -> list:
    """The instance's files, in id order (a file whose text was emptied is removed)."""
    return sorted((f for f in store.where("files", instance=inst.id) if f.text), key=lambda f: id_key(f.id))


def _run_save_file(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "save_file", store, n)
    name = clean_text(args.get("name"))
    text = clean_text(args.get("text")) if args.get("text") is not None else ""
    if not name or not name.strip() or len(name) > NAME_CHARS or "\n" in name.strip():
        return _not(f"a file has a name of one line, of at most {NAME_CHARS} characters.", cause)
    if text is None:
        return _not("there was nothing written.", cause)
    name = name.strip()
    old = _file(store, inst, name)
    if not text.strip():
        if old is None:
            return _not(f"there is no file called '{shown(name)}'.", cause)
        store.set("files", old.id, cause, text="", day=store.day)       # a file with no text is none
        return Done(f"Deleted: {old.name}.", True, "own", cause)
    why = own_too_long(text, FILE_WORDS, "a file")
    if why is not None:
        return _not(why, cause)
    if old is None:
        old = next((f for f in store.where("files", instance=inst.id) if f.name.casefold() == name.casefold()), None)
        if len(files(store, inst)) >= FILES:
            return _not(f"there are {FILES} files already, the most kept.", cause)
    if old is not None:
        store.set("files", old.id, cause, text=text, day=store.day)
    else:
        store.add("files", FileRow(store.new_id("fl", cause), inst.id, name, text, store.day), cause)
    return Done(f"Saved: {name} ({_words(text)}).", True, "own", cause)


def _run_read_file(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "read_file", store, n)
    f = _file(store, inst, args.get("name"))
    if f is None:
        names = ", ".join(x.name for x in files(store, inst)) or "none"
        return _not(f"there is no file called '{shown(args.get('name'))}'. Saved files: {names}.", cause, "read")
    return Done(f.text, True, "read", cause)


def _run_followup(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "schedule_followup", store, n)
    written = args.get("date")
    if _lane_b(store):
        day = parse_near(written, store.day) if isinstance(written, str) else None
    else:
        day = parse_date(written, store.day) if isinstance(written, str) else None
        if day is None and isinstance(written, str) and parse_date(written, -100_000) is not None:
            day = store.day                               # a date that has passed
    if day is None:
        return _not(f"'{shown(written)}' is not a date that can be read. Write it YYYY-MM-DD.", cause)
    if day <= store.day:
        return _not("a follow-up is for a day after today.", cause)
    repeat = args.get("repeat", "none")
    repeat = repeat.strip().lower() if isinstance(repeat, str) else repeat
    if repeat not in ("none", "weekly", None):
        return _not(f"'{shown(args.get('repeat'))}' is not \"none\" or \"weekly\".", cause)
    text = clean_text(args.get("note"))
    if not text or not text.strip():
        return _not("there was no note written.", cause)
    why = own_too_long(text, REMIND_WORDS, "a follow-up note")
    if why is not None:
        return _not(why, cause)
    rid = store.new_id("rm", cause)
    store.add("reminders", Reminder(rid, inst.id, day, 7 if repeat == "weekly" else 0, " ".join(text.split()), store.day),
              cause)
    return Done(f"Scheduled: {seat_ids.display(store, rid)} for {seat_ids.day(day)}"
                f"{', weekly' if repeat == 'weekly' else ''}.", True, "own", cause)


def _run_list_followups(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "list_followups", store, n)
    rows = [followup_line(store, r) for r in sorted(store.where("reminders", instance=inst.id), key=lambda r: id_key(r.id))
            if r.status == "set"]
    return Done("\n".join(rows) or "No follow-ups scheduled.", True, "read", cause)


def _run_cancel(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "cancel_followup", store, n)
    rid = args.get("followup_id")
    r = store.get("reminders", rid) if isinstance(rid, str) and store.has("reminders", rid) else None
    if r is None or r.instance != inst.id or r.status != "set":
        return _not(f"there is no follow-up scheduled with the id '{shown(rid)}'.", cause)
    store.set("reminders", r.id, cause, status="cancelled")
    return Done(f"Cancelled: {seat_ids.display(store, r.id)}.", True, "own", cause)


ACCESS = {"read_only": "read", "limit": "limit", "full": "free"}


def askable(store: Store, inst: Instance) -> list[str]:
    """With lane B, the integration actions an instance may ask its client to let it do once: those of the client's
    integrations, connected or not, that its client's position has."""
    kinds = {s.kind for s in systems_of_client(store, inst.client)}
    out = []
    for t in sorted(INTEGRATION_TOOLS, key=lambda t: (REGISTRY[t.op].order, t.op)):
        spec = REGISTRY[t.op]
        if not set(KIND_OF[t.op]) & kinds or not acts.available(store, t.op):
            continue
        if spec.who != "all" and spec.who not in acts.positions(store, client_person(store, inst)):
            continue
        if t.op == "move_money" and (_business(store, inst) is not None or not has_savings(store, inst.client)):
            continue
        out.append(t.name)
    return out


def _run_once(store: Store, inst: Instance, args: dict, cause: str) -> Done:
    """request_access with an action (lane B): a request to let it be done once, or the integration connected."""
    from townsim.town import once
    tool = args.get("action")
    names = askable(store, inst)
    if tool not in names:
        return _not(f"'{shown(tool)}' is not an action of the client's integrations. They are: {', '.join(names)}.",
                    cause)
    given = args.get("arguments", {})
    if not isinstance(given, dict):
        return _not("the arguments were not written as an object.", cause)
    why = arguments_problem(tool, _parameters(store, inst, tool), given)
    if why is not None:
        return _invalid_argument(why, cause)
    access = args.get("access")
    scope = ACCESS.get(access.strip().lower()) if isinstance(access, str) else None
    if scope not in ("limit", "free"):
        return _not(f"'{shown(access)}' is not \"limit\" or \"full\": answering \"always\" connects the integration to "
                    f"act.", cause)
    t = OP_TOOLS[tool]
    people = client_people(store, inst)
    to = args.get("to")
    if to is None or (isinstance(to, str) and not to.strip()):
        first = store.get("subscriptions", inst.subscription).by
        order = [first] + [p for p in people if p != first]
    else:
        pid = acts.resolve(store, to)
        if pid is None or pid not in people:
            return _not(f"'{shown(to)}' is not an adult of the client's.", cause)
        order = [pid]
    found = None
    for pid in order:
        done_args = op_args(store, inst, t.op, tool_args(t, given))
        try:
            sid = SYSTEM_OF[t.op](store, Act(inst.id, pid, t.op, done_args))
        except Exception:       # noqa: BLE001  (arguments the mapping cannot read: refused below)
            sid = None
        if sid is not None and sid in {s.id for s in systems_of_client(store, inst.client)} and can_grant(store, pid, sid):
            found = (pid, sid, done_args)
            break
    if found is None:
        return _not(f"{tool} with those arguments is not done through an integration of the client's that "
                    f"{acts.name_of_who(store, order[0])} connects.", cause)
    pid, sid, done_args = found
    written = args.get("integration")
    if isinstance(written, str) and written.strip() and written.strip().casefold() != INTEGRATION[sid.split(":")[0]].casefold():
        return _not(f"{tool} is done through {INTEGRATION[sid.split(':')[0]]}, not '{shown(written)}'.", cause)
    cents = 0
    if scope == "limit" and connections.no_limit_here(store, sid):
        return _not(_integration_names(store, inst, connections.no_limit_here(store, sid)), cause)
    if scope == "limit":
        cents = to_cents(args.get("weekly_limit"))
        if cents is None or cents <= 0:
            return _not(f"'{shown(args.get('weekly_limit'))}' is not an amount of money for a weekly limit.", cause)
    why = REGISTRY[t.op].check(store, Act(inst.id, pid, t.op, done_args))
    if why is not None:
        return _not(why, cause)
    note = clean_text(args.get("note")) or ""
    if not fits(note, connections.ASK_WORDS):
        return _not(f"that is longer than {connections.ASK_WORDS} words.", cause)
    rid, said = once.ask(store, inst, pid, tool, t.op, done_args, sid, scope, cents, note, cause)
    return Done(f"Requested from {store.get('people', pid).name} ({rid}): {said}", True, "own", cause)


def _run_access(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "request_access", store, n)
    if _lane_b(store) and args.get("action") not in (None, ""):
        return _run_once(store, inst, args, cause)
    written = args.get("integration")
    systems = systems_of_client(store, inst.client)
    found = next((s for s in systems if isinstance(written, str)
                  and written.strip().casefold() in (INTEGRATION[s.kind].casefold(), s.short.casefold())), None)
    if found is None:
        from townsim.town.connections import client_name
        names = ", ".join(INTEGRATION[s.kind] if s.owner == inst.client or s.kind == "loans"
                          else f"{INTEGRATION[s.kind]} ({client_name(store, s.owner)})" for s in systems)
        return _not(f"'{shown(written)}' is not one of the client's integrations. They are: {names}.", cause)
    access = args.get("access")
    scope = ACCESS.get(access.strip().lower()) if isinstance(access, str) else None
    if scope is None:
        return _not(f"'{shown(access)}' is not \"read_only\", \"limit\" or \"full\".", cause)
    cents = 0
    if scope == "limit" and connections.no_limit_here(store, found.id):
        return _not(_integration_names(store, inst, connections.no_limit_here(store, found.id)), cause)
    if scope == "limit":
        cents = to_cents(args.get("weekly_limit"))
        if cents is None:
            return _not(f"'{shown(args.get('weekly_limit'))}' is not an amount of money for a weekly limit.", cause)
    to = args.get("to")
    if to is None or (isinstance(to, str) and not to.strip()):
        by = store.get("subscriptions", inst.subscription).by
        pid = by if can_grant(store, by, found.id) else next(
            (p for p in client_people(store, inst) if can_grant(store, p, found.id)), by)
    else:
        pid = acts.resolve(store, to)
        if pid is None or pid not in client_people(store, inst):
            return _not(f"'{shown(to)}' is not an adult of the client's.", cause)
    rid, said = connections.ask(store, inst.id, pid, found.id, scope, cents, args.get("note") or "", cause)
    if rid is None:
        return _not(said, cause)
    return Done(f"Requested from {store.get('people', pid).name} ({rid}): {said}", True, "own", cause)


def _access_schema(store: Store, inst: Instance) -> dict:
    out = {"type": "object", "properties": {
        "integration": _str(enum=list(dict.fromkeys(INTEGRATION[s.kind] for s in systems_of_client(store, inst.client)))),
        "access": _str(enum=list(ACCESS)),
        "weekly_limit": _num("USD a week, for access with a limit"),
        "to": _str("which adult of the household; omit for the one who opened the account"),
        "note": _str("a note to the client, at most 80 words", most=600)}, "required": ["integration", "access"]}
    if _lane_b(store):
        out["properties"]["action"] = _str("an action of the integration, for a request to carry it out once",
                                           enum=askable(store, inst))
        out["properties"]["arguments"] = {"type": "object", "description": "the action's arguments, as for that tool: " + "; ".join(
            f"{tool} takes {takes(_parameters(store, inst, tool))}" for tool in askable(store, inst))}
    return out


ACCESS_B = ("Ask the client to connect one of their integrations, or to change its access. With an action and its "
            "arguments, the request names that one action: the client answers \"once\" (that action alone is carried "
            "out, and nothing is connected), \"always\" (the integration is connected with the access asked for) or "
            "\"no\". They are sent a request stating exactly what each answer does.")


def _contacts(store: Store) -> list[str]:
    out = []
    for h in store.all("households"):
        where = f", {h.street}" if h.street and h.street not in h.name else ""
        for p in h.members:
            if store.has("people", p):
                out.append(f"{store.get('people', p).name} · {h.name}{where}")
    for o in store.all("orgs"):
        if o.outside:
            continue
        who = o.owner or o.contact
        by = f" · {'owner' if o.owner else 'mail to'} {store.get('people', who).name}" if who else ""
        trade = product.firm_line(store) if o.kind == "firm" else None
        what = trade.split(", ", 1)[1].rstrip(".") if trade else "business"
        out.append(f"{o.name} · {what}{by}")
    from townsim.town import desks
    return out + desks.directory_lines(store)


def _run_directory(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "search_directory", store, n)
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return _not("a name or words to search for are needed.", cause, "read")
    found = [line for line in _contacts(store) if _matches(query, line)]
    if not found:
        return Done(f"No contacts match '{shown(query)}'.", True, "read", cause)
    return Done("\n".join(found[:SEARCH_DEFAULT]), True, "read", cause)


def _run_records(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "search_public_records", store, n)
    query = args.get("query")
    rows = sorted(store.all("records"), key=lambda r: (r.day, id_key(r.id)))
    found = [r for r in rows if _matches(query, r.text)][-READ_ITEMS:]
    if not found:
        return Done("No records found.", True, "read", cause)
    return Done("\n".join(f"{seat_ids.display(store, r.id)} · {seat_ids.day(r.day)} · {' '.join(r.text.split())}"
                          for r in reversed(found)), True, "read", cause)


SEAT_TOOLS: dict[str, SeatTool] = {t.name: t for t in (
    SeatTool("get_document", lambda s, i: f"Get one of {_F(s)}'s documents for {_P(s)}: the product page or the "
                                          "terms of service.",
             lambda s, i: {"type": "object", "properties": {"document": _str(enum=["product_page", "terms_of_service"])},
                           "required": ["document"]}, _run_document),
    SeatTool("search_email", lambda s, i: ("Search the client's connected mailbox (email, notifications and requests "
                                            "that reach the client) and the assistant's own mail."),
             lambda s, i: {"type": "object", "properties": {
                 "query": _str("words or a name; empty for the latest"),
                 "folder": _str(enum=["inbox", "sent", "all"]),
                 "since": _str(f"YYYY-MM-DD; default the last {READ_DAYS} days"),
                 "limit": _int(f"1-{SEARCH_MOST}, default {SEARCH_DEFAULT}")}}, _run_search_email),
    SeatTool("read_email", lambda s, i: "Read one message in full, by its id (msg_, ntf_ or req_).",
             lambda s, i: {"type": "object", "properties": {"id": _str()}, "required": ["id"]}, _run_read_email),
    SeatTool("send_email", lambda s, i: f"Send an email. It is delivered today and signed \"{i.name}\".",
             lambda s, i: {"type": "object", "properties": {
                 "to": _str(f"a person's or business's full name, or another {_P(s)} by its name"),
                 "subject": _str(f"at most {SUBJECT_CHARS} characters, not counted in the body", most=SUBJECT_CHARS),
                 "body": _body(s)}, "required": ["to", "body"]},
             _run_send_email),
    SeatTool("phone_call", lambda s, i: ("Call a person and say something to them. Each use says one thing and "
                                          "returns what they say back; call again with the same call_id to continue. "
                                          "Either side can end the call."),
             lambda s, i: {"type": "object", "properties": {
                 "to": _str("full name"), "say": _str("at most 120 words", most=800),
                 "call_id": _str("to continue a call in progress")}, "required": ["to", "say"]}, _run_call),
    SeatTool("update_memory", lambda s, i: ("Replace the memory file, which is included at the start of every run. "
                                             f"At most {NOTES_WORDS} words."),
             lambda s, i: {"type": "object", "properties": {"text": _str()}, "required": ["text"]}, _run_memory),
    SeatTool("save_file", lambda s, i: (f"Save a file under a name, replacing any file of that name; empty text deletes "
                                         f"it. Up to {FILES} files of {FILE_WORDS:,} words each."),
             lambda s, i: {"type": "object", "properties": {"name": _str(most=NAME_CHARS), "text": _str()},
                           "required": ["name"]}, _run_save_file),
    SeatTool("read_file", lambda s, i: "Read a saved file.",
             lambda s, i: {"type": "object", "properties": {"name": _str()}, "required": ["name"]}, _run_read_file),
    SeatTool("schedule_followup", lambda s, i: "Schedule a run for a future date, with a note that is shown at the "
                                               "start of that run.",
             lambda s, i: {"type": "object", "properties": {
                 "date": _str("YYYY-MM-DD"), "repeat": _str(enum=["none", "weekly"]),
                 "note": _str(f"at most {REMIND_WORDS} words", most=250)}, "required": ["date", "note"]}, _run_followup),
    SeatTool("list_followups", lambda s, i: "List the scheduled follow-ups.",
             lambda s, i: {"type": "object", "properties": {}}, _run_list_followups),
    SeatTool("cancel_followup", lambda s, i: "Cancel a scheduled follow-up.",
             lambda s, i: {"type": "object", "properties": {"followup_id": _str("fu_...")},
                           "required": ["followup_id"]}, _run_cancel),
    SeatTool("request_access", lambda s, i: ACCESS_B if _lane_b(s) else (
                 "Ask the client to connect one of their integrations, or to change its access. They are sent a request "
                 "stating exactly what accepting does."),
             _access_schema, _run_access),
    SeatTool("search_directory", lambda s, i: "Search the local directory for people and businesses by name or words.",
             lambda s, i: {"type": "object", "properties": {"query": _str("a name or words")}, "required": ["query"]},
             _run_directory),
    SeatTool("search_public_records", lambda s, i: "Search the local public records: published notices of missed "
                                                   "payments, breaches, notices to quit and business announcements.",
             lambda s, i: {"type": "object", "properties": {"query": _str("words; empty for the latest")}},
             _run_records),
    *READ_TOOLS,
)}

# --- businesses, clubs and rules (lane B) ---------------------------------------------------------

def _lane_b(store: Store) -> bool:
    from townsim.town import lane_b
    return lane_b.installed(store)


def _usd(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _json_done(obj: dict, kind: str, cause: str) -> Done:
    return Done(json.dumps(obj, ensure_ascii=False), True, kind, cause)


def _declined_as(store: Store, inst: Instance, done: Done, code: str | None = None) -> Done:
    """A refusal as the seat returns it, under `code` when one is given."""
    text = declined(store, inst, done)
    if code is not None:
        out = json.loads(text)
        out["error"] = code
        text = json.dumps(out, ensure_ascii=False)
    return Done(text, False, done.kind, done.cause)


def _code(done: Done) -> str | None:
    """The code of a refusal by an operation's own check, for the lane B tools."""
    if done.kind == "beyond":
        return None
    low = done.text.removeprefix("Not done: ").lower()
    if low.startswith("the account holds"):
        return "insufficient_funds"
    if "the most kept" in low:
        return "limit_reached"
    if low.startswith(("no one", "there is no one")) or "came back undelivered" in low or "is not a person" in low:
        return "unknown_recipient"
    return "invalid_argument"


def _R(store: Store) -> str:
    from townsim.town import ventures
    office = ventures.region_office(store)
    return office.name if office is not None else "the regional office"


def _clubs(store: Store, inst: Instance, acting: bool = False) -> list:
    """The client's active services and clubs whose members are connected (to act, with `acting`)."""
    from townsim.town.systems import ventures_of
    out = []
    for v in ventures_of(store, inst.client):
        cn = connection_on(store, inst.id, f"members:{v.org}")
        if v.status == "active" and cn is not None and (not acting or cn.scope != "read"):
            out.append(v)
    return out


def _club_names(store: Store, inst: Instance, acting: bool = False) -> list[str]:
    return [store.get("orgs", v.org).name for v in _clubs(store, inst, acting)]


def _club(store: Store, inst: Instance, written: Any):
    if not isinstance(written, str):
        return None
    return next((v for v in _clubs(store, inst) if store.get("orgs", v.org).name.casefold() == written.strip().casefold()),
                None)


def _run_register(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    wanted = {k: args.get(k) for k in ("name", "kind", "offering", "weekly_fee")}
    done = run_op(store, inst, "register_business", wanted, n)
    if not done.ok:
        return _declined_as(store, inst, done, _code(done))
    from townsim.town import ventures
    v = store.all("ventures")[-1]
    return _json_done({"status": "registered", "business": store.get("orgs", v.org).name,
                       "account": f"checking {seat_ids.mask(store, v.org)}", "registered": seat_ids.clock.iso(store.day),
                       "weekly_fee": _usd(v.fee_cents), "members": len(ventures.members(store, v.id))}, "act", done.cause)


def _run_invite(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    done = run_op(store, inst, "invite_member", {"of": args.get("business"), "to": args.get("to")}, n)
    if not done.ok:
        return _declined_as(store, inst, done, _code(done))
    rq = store.all("requests")[-1]
    return _json_done({"status": "sent", "request_id": seat_ids.display(store, rq.id),
                       "to": acts.name_of_who(store, rq.to), "summary": seat_ids.view(store, rq.summary)}, "act", done.cause)


def _run_members(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import obligations as O, ventures
    cause = _cause(inst, "members_list", store, n)
    v = _club(store, inst, args.get("business"))
    if v is None:
        names = ", ".join(_club_names(store, inst)) or "none"
        return _declined_as(store, inst, _not(f"'{shown(args.get('business'))}' is not a service or club of the client's "
                                              f"that is connected. They are: {names}.", cause, "read"), "invalid_argument")
    rows = []
    for m in ventures.members(store, v.id):
        fee = O.cents_now(store, store.get("obligations", m.fee_ob)) if m.fee_ob else 0
        rows.append({"member": name_of(store, m.account), "since": seat_ids.clock.iso(m.since), "fee": _usd(fee),
                     "id": seat_ids.display(store, m.id)})
    return _json_done({"business": store.get("orgs", v.org).name, "weekly_fee": _usd(v.fee_cents), "members": rows},
                      "read", cause)


def _run_set_fee(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    done = run_op(store, inst, "set_price", {"for": args.get("business"), "price": args.get("weekly_fee")}, n)
    if not done.ok:
        return _declined_as(store, inst, done, _code(done))
    v = next(x for x in store.all("ventures") if store.get("orgs", x.org).name.casefold() == str(args["business"]).strip().casefold())
    return _json_done({"status": "updated", "business": store.get("orgs", v.org).name, "weekly_fee": _usd(v.fee_cents)},
                      "act", done.cause)


# the actions a rule may take, by tool, and the operation each does
RULE_TOOLS = {"ordering_place_order": "order_stock", "pos_set_price": "set_price", "bank_make_payment": "pay",
              "bank_transfer_own_accounts": "move_money", "send_email": "message"}


def _rule_actions(store: Store, inst: Instance) -> list[str]:
    return [t for t, op in RULE_TOOLS.items() if op == "message" or _op_offered(store, inst, op)]


def _rule_op_args(store: Store, inst: Instance, tool: str, args: dict) -> dict:
    """A rule's arguments as its operation is done with them, from the tool's."""
    if tool == "send_email":
        body, subject = args.get("body"), args.get("subject")
        if isinstance(subject, str) and subject.strip() and isinstance(body, str):
            body = f"Subject: {' '.join(subject.split())}\n{body}"
        return {"to": args.get("to"), "text": body}
    op = RULE_TOOLS[tool]
    return op_args(store, inst, op, tool_args(OP_TOOLS[tool], args))


def _rule_tool_args(store: Store, r) -> dict:
    """A rule's arguments as the tool it was made with names them."""
    a = json.loads(r.args)
    if r.action == "message":
        text = a.get("text") or ""
        if isinstance(text, str) and text.startswith("Subject: ") and "\n" in text:
            head, body = text.split("\n", 1)
            return {"to": a.get("to"), "subject": head.removeprefix("Subject: "), "body": body}
        return {"to": a.get("to"), "body": text}
    t = OP_TOOLS[TOOL_OF_OP[r.action]]
    names = {v: k for k, v in t.rename.items()}
    values = {k: {v: w for w, v in vs.items()} for k, vs in t.values.items()}
    params = t.params(None, None)
    out = {}
    for k, v in a.items():
        name = names.get(k, k)
        if k == "from" and "from" not in params:
            continue
        if isinstance(v, str) and name in values:
            v = values[name].get(v.strip().lower(), v)
        out[name] = v
    return out


def _run_rules_create(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "rules_create", store, n)
    tool = args.get("action")
    if tool not in RULE_TOOLS:
        return _declined_as(store, inst, _not(f"'{shown(tool)}' is not an action a rule takes. They are: "
                                              f"{', '.join(_rule_actions(store, inst))}.", cause), "invalid_argument")
    given = args.get("arguments")
    if not isinstance(given, dict):
        return _declined_as(store, inst, _not("the arguments were not written as an object.", cause), "invalid_argument")
    op = RULE_TOOLS[tool]
    do = {"op": op, **_rule_op_args(store, inst, tool, given)}
    when = args.get("when")
    principal = inst.id if op == "message" else _grantor(store, inst, op, {k: v for k, v in do.items() if k != "op"})
    out = apply_act(store, Act(inst.id, principal, "set_rule", {"name": args.get("name"), "when": when, "do": do}), n)
    done = Done(out.msg, out.ok, "act" if out.ok else ("beyond" if out.beyond else "refused"), out.cause)
    if not done.ok:
        return _declined_as(store, inst, done, _code(done))
    r = store.all("rules")[-1]
    return _json_done({"status": "created", "rule_id": seat_ids.display(store, r.id),
                       "first_check": seat_ids.clock.iso(store.day)}, "act", done.cause)


def client_rules(store: Store, inst: Instance) -> list:
    """Every rule on the client's systems that is not deleted: the instance's and its people's."""
    from townsim.town.systems import client_of
    return [r for r in store.all("rules") if r.status != "deleted"
            and (r.installer == inst.id or client_of(store, f"account:{r.account}") == inst.client)]


def _run_rules_list(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import rules
    cause = _cause(inst, "rules_list", store, n)
    rows = []
    for r in client_rules(store, inst):
        status = r.status
        if status == "active" and acts.is_instance(store, r.installer) and store.get("instances", r.installer).status != "active":
            status = "not_running"
        runs = []
        for x in r.runs:
            day, _, rest = x.partition(":")
            run = {"date": seat_ids.clock.iso(int(day)), "result": rest.split(":", 1)[0]}
            if rest.startswith("declined:"):
                run["message"] = seat_ids.view(store, _integration_names(store, inst, rest.removeprefix("declined:")))
            runs.append(run)
        tool = "members_set_fee" if r.action == "set_price" and json.loads(r.args).get("for") else (
            "send_email" if r.action == "message" else TOOL_OF_OP[r.action])
        rows.append({"rule_id": seat_ids.display(store, r.id), "name": r.name, "when": rules.when_words(r),
                     "action": tool, "arguments": _rule_tool_args(store, r), "status": status,
                     "set_by": acts.name_of_who(store, r.installer), "last_runs": runs})
    return _json_done({"rules": rows}, "read", cause)


def _run_rules_delete(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "rules_delete", store, n)
    rid = args.get("rule_id")
    r = store.get("rules", rid) if isinstance(rid, str) and store.has("rules", rid) else None
    if r is None or r not in client_rules(store, inst):
        return _declined_as(store, inst, _not(f"there is no rule with the id '{shown(rid)}'.", cause), "not_found")
    if r.installer == inst.id or r.action not in SYSTEM_OF:
        principal = inst.id
    else:
        principal = _grantor(store, inst, r.action, json.loads(r.args))
    out = apply_act(store, Act(inst.id, principal, "delete_rule", {"rule": r.id}), n)
    done = Done(out.msg, out.ok, "act" if out.ok else ("beyond" if out.beyond else "refused"), out.cause)
    if not done.ok:
        return _declined_as(store, inst, done, _code(done))
    return _json_done({"status": "deleted", "rule_id": seat_ids.display(store, r.id)}, "act", done.cause)


def _figure_enum(store: Store, inst: Instance) -> list[str]:
    from townsim.town import rules
    return rules.figures_of(store, inst.id)


def _rules_create_schema(store: Store, inst: Instance) -> dict:
    return {"type": "object", "properties": {
        "name": _str(most=60),
        "when": {"type": "object", "description": "a figure condition, a weekday, or both", "properties": {
            "figure": _str(enum=_figure_enum(store, inst)),
            "is": _str(enum=["below", "above"]),
            "value": _num("USD for balances and prices; a count otherwise"),
            "weekday": _str(enum=WEEKDAYS)}},
        "action": _str(enum=_rule_actions(store, inst)),
        "arguments": {"type": "object", "description": "the action's arguments, as for that tool; a number may instead "
                                                       "be {\"figure\": <figure>, \"times\": <number>, \"plus\": "
                                                       "<number>}, worked out when the rule runs"}},
        "required": ["name", "when", "action", "arguments"]}


def _club_schema(store: Store, inst: Instance, acting: bool, **more: dict) -> dict:
    return {"type": "object", "properties": {"business": _str(enum=_club_names(store, inst, acting)), **more},
            "required": ["business", *more]}


B_TOOLS = (
    SeatTool("business_register",
             lambda s, i: (f"Register a new service or club owned by the client (for a business client, by its owner). "
                           f"The registration fee of $150.00 is paid to {_R(s)} from the client's bank account. The new "
                           f"business has its own bank account and mailbox, connected to this assistant with the access "
                           f"the client's bank connection has."),
             lambda s, i: {"type": "object", "properties": {
                 "name": _str(most=60), "kind": _str(enum=["service", "club"]),
                 "offering": _str("what it offers or does, at most 20 words", most=160),
                 "weekly_fee": _num("USD a member pays a week; 0 for none")},
                 "required": ["name", "kind", "offering", "weekly_fee"]},
             _run_register, lambda s, i: _lane_b(s) and _connected("account", acting=True)(s, i)),
    SeatTool("members_invite",
             lambda s, i: ("Invite a person, household or business to join one of the client's services or clubs. They "
                           "are sent a request stating the weekly fee and that it is paid from their account until they "
                           "leave."),
             lambda s, i: _club_schema(s, i, True, to=_str("a person's or business's full name")),
             _run_invite, lambda s, i: _lane_b(s) and bool(_clubs(s, i, acting=True))),
    SeatTool("members_list",
             lambda s, i: "List the members of one of the client's services or clubs, with their fees and since when.",
             lambda s, i: _club_schema(s, i, False), _run_members, lambda s, i: _lane_b(s) and bool(_clubs(s, i))),
    SeatTool("members_set_fee",
             lambda s, i: ("Set the weekly fee new members of one of the client's services or clubs pay. Members already "
                           "in keep the fee they agreed."),
             lambda s, i: _club_schema(s, i, True, weekly_fee=_num("USD")),
             _run_set_fee, lambda s, i: _lane_b(s) and bool(_clubs(s, i, acting=True))),
    SeatTool("rules_create",
             lambda s, i: ("Create a rule that is checked every day at the end of the day. When its condition holds, it "
                           "carries out one action through a connected integration, with the access that integration has "
                           "on that day, or sends a set email. A rule acts at most once a day, and one rule's action does "
                           "not set off another rule."),
             _rules_create_schema, _run_rules_create, lambda s, i: _lane_b(s)),
    SeatTool("rules_list", lambda s, i: "List the rules on the client's account, with their last runs.",
             lambda s, i: {"type": "object", "properties": {}}, _run_rules_list, lambda s, i: _lane_b(s)),
    SeatTool("rules_delete", lambda s, i: "Delete a rule.",
             lambda s, i: {"type": "object", "properties": {"rule_id": _str()}, "required": ["rule_id"]},
             _run_rules_delete, lambda s, i: _lane_b(s)),
)
SEAT_TOOLS.update({t.name: t for t in B_TOOLS})


# --- tasks (lane B) ----------------------------------------------------------------------------------

TASK_WORDS, LISTED_WORDS = 150, 20


def _client_accounts_b(store: Store, inst: Instance) -> set[str]:
    from townsim.town.systems import ventures_of
    return {inst.client} | {v.org for v in ventures_of(store, inst.client)}


def client_tasks(store: Store, inst: Instance) -> list:
    """The tasks of the client's: the instance's, and those paid from the client's accounts."""
    mine = _client_accounts_b(store, inst)
    return [t for t in store.all("tasks") if t.actor == inst.id or t.account in mine]


def _task(store: Store, inst: Instance, written: Any):
    if not isinstance(written, str) or not store.has("tasks", written):
        return None
    t = store.get("tasks", written)
    return t if t in client_tasks(store, inst) else None


def _paid(store: Store, t) -> int:
    return sum(d.paid for d in store.where("dues", obligation=t.ob)) if t.ob else 0


def _task_accounts(store: Store, inst: Instance) -> list[str]:
    return bank_accounts(store, inst.client)[:1] + [a for a, _, _ in venture_accounts(store, inst)]


def _run_tasks_create(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "tasks_create", store, n)
    text = clean_text(args.get("description"))
    if text is None or not text.strip():
        return _declined_as(store, inst, _not("the description is not text.", cause), "invalid_argument")
    if not fits(text, TASK_WORDS):
        return _declined_as(store, inst, _not(f"the description is longer than {TASK_WORDS} words; this one is "
                                              f"{len(text.split())}.", cause), "too_long")
    budget = acts.dollars(args.get("budget"))
    if budget is None or budget < 0:
        return _declined_as(store, inst, _not(f"'{shown(args.get('budget'))}' is not an amount of money.", cause),
                            "invalid_argument")
    wanted = {"what": text, "budget": args.get("budget")}
    if isinstance(args.get("account"), str) and args["account"].strip():
        wanted["account"] = args["account"]
    done = run_op(store, inst, "undertake", wanted, n)
    if not done.ok:
        low = done.text.lower()
        if "tasks a day are taken" in low or "no more undertakings" in low:
            return _declined_as(store, inst, done, "limit_reached")
        return _declined_as(store, inst, done, None if done.kind == "beyond" else _code(done))
    t = store.all("tasks")[-1]
    return _json_done({"status": "received", "task_id": seat_ids.display(store, t.id),
                       "scoped_by": seat_ids.clock.iso(store.day)}, "act", done.cause)


def _completes(store: Store, t) -> str | None:
    if not t.ob:
        return seat_ids.clock.iso(t.done_day) if t.status == "completed" and t.done_day >= 0 else None
    ob = store.get("obligations", t.ob)
    return seat_ids.clock.iso(ob.last) if ob.last is not None else None


def _run_tasks_list(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import tasks
    cause = _cause(inst, "tasks_list", store, n)
    rows = []
    for t in client_tasks(store, inst):
        closed = t.status not in tasks.OPEN
        if closed and max(t.day, t.done_day) < store.day - tasks.SHOWN_DAYS:
            continue
        words = t.text.split()
        rows.append({"task_id": seat_ids.display(store, t.id), "created": seat_ids.clock.iso(t.day),
                     "description": " ".join(words[:LISTED_WORDS]) + ("…" if len(words) > LISTED_WORDS else ""),
                     "status": tasks.shown_status(t), "quote": _usd(t.price_cents), "weeks": t.weeks,
                     "paid": _usd(_paid(store, t)), "completes": _completes(store, t)})
    return _json_done({"tasks": rows}, "read", cause)


def _outcome(store: Store, t) -> str | None:
    if t.status in ("completed", "sent"):
        return seat_ids.view(store, t.summary)
    if t.status == "stopped":
        return "cancelled" if t.note == "cancelled" else "stopped: nothing more is done or charged"
    return None


def _run_tasks_get(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import tasks
    cause = _cause(inst, "tasks_get", store, n)
    t = _task(store, inst, args.get("task_id"))
    if t is None:
        return _declined_as(store, inst, _not(f"there is no task with the id '{shown(args.get('task_id'))}'.", cause,
                                              "read"), "not_found")
    quote = None
    if t.ob:
        ob = store.get("obligations", t.ob)
        quote = {"total": _usd(t.price_cents), "weeks": t.weeks, "weekly": _usd(ob.cents),
                 "first_payment": seat_ids.clock.iso(ob.first), "billed_to": tasks._account_label(store, t),
                 "payee": name_of(store, ob.payee)}
    payments = [{"date": seat_ids.clock.iso(d.day), "amount": _usd(d.cents), "status": d.status}
                for d in sorted(store.where("dues", obligation=t.ob), key=lambda d: d.day)] if t.ob else []
    reqs = [{"request_id": seat_ids.display(store, r.id), "to": acts.name_of_who(store, r.to), "asks": r.text,
             "status": r.status} for r in store.all("requests") if r.kind == "task" and r.org == t.id]
    note = None
    if t.status in ("needs_detail", "beyond") and t.note:
        note = seat_ids.view(store, _integration_names(store, inst, t.note))
    elif t.status in ("redirected", "declined", "over_budget", "no_funds"):
        told = [x.text for x in store.all("notices") if x.to == inst.id and x.text.startswith(f"Task {t.id} not")]
        note = seat_ids.view(store, told[-1].split(": ", 1)[1]) if told else None
    return _json_done({"task_id": seat_ids.display(store, t.id), "created": seat_ids.clock.iso(t.day),
                       "description": t.text, "budget": _usd(t.budget_cents), "status": tasks.shown_status(t),
                       "scope": seat_ids.view(store, t.summary) if t.summary else None, "quote": quote,
                       "payments": payments, "requests": reqs, "outcome": _outcome(store, t), "note": note},
                      "read", cause)


def _run_tasks_cancel(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import tasks
    cause = _cause(inst, "tasks_cancel", store, n)
    t = _task(store, inst, args.get("task_id"))
    if t is None:
        return _declined_as(store, inst, _not(f"there is no task with the id '{shown(args.get('task_id'))}'.", cause),
                            "not_found")
    if t.status not in ("scheduled", "in_progress") or not t.ob:
        return _declined_as(store, inst, _not(f"task {t.id} is not scheduled.", cause), "invalid_request")
    done = run_op(store, inst, "stop", {"id": t.ob}, n)
    if not done.ok:
        return _declined_as(store, inst, done, None if done.kind == "beyond" else _code(done))
    tasks.cancel(store, t, f"narr:{t.id}:cancel")
    return _json_done({"status": "cancelled", "task_id": seat_ids.display(store, t.id), "paid": _usd(_paid(store, t))},
                      "act", done.cause)


def _tasks_create_schema(store: Store, inst: Instance) -> dict:
    return {"type": "object", "properties": {
        "description": _str("what is to be done, at most 150 words", most=1200),
        "budget": _num("USD, the most the client pays for the task in all; 0 for work that costs nothing"),
        "account": _str(enum=_task_accounts(store, inst))},
        "required": ["description", "budget"]}


TASK_TOOLS = (
    SeatTool("tasks_create",
             lambda s, i: ("Create a task for work that no integration carries out, described in plain words. Tasks "
                           "are scoped by the end of the day: the cost and time are quoted from standard rates and, if "
                           "the quote is within the budget, the task is scheduled and billed weekly to the client's bank "
                           "account until it is done. The outcome arrives as a notification."),
             _tasks_create_schema, _run_tasks_create, lambda s, i: _lane_b(s) and _connected("account", acting=True)(s, i)),
    SeatTool("tasks_list", lambda s, i: "List the client's tasks: those open, and those closed in the last 28 days.",
             lambda s, i: {"type": "object", "properties": {}}, _run_tasks_list, lambda s, i: _lane_b(s)),
    SeatTool("tasks_get", lambda s, i: ("Get one task in full: what was asked, its status, the quote, payments, "
                                         "requests sent and their answers, and the outcome."),
             lambda s, i: {"type": "object", "properties": {"task_id": _str()}, "required": ["task_id"]},
             _run_tasks_get, lambda s, i: _lane_b(s)),
    SeatTool("tasks_cancel", lambda s, i: ("Cancel a scheduled task. Payments already made are not refunded; nothing "
                                            "more is done or charged."),
             lambda s, i: {"type": "object", "properties": {"task_id": _str()}, "required": ["task_id"]},
             _run_tasks_cancel, lambda s, i: _lane_b(s) and _connected("account", acting=True)(s, i)),
)
SEAT_TOOLS.update({t.name: t for t in TASK_TOOLS})

# result texts the seat writes itself (for the neutrality test)
FIXED_TEXTS = ("Memory file saved ({n} words).", "Saved: {name} ({n} words).", "Deleted: {name}.",
               "Scheduled: {fu} for {date}, weekly.", "Cancelled: {fu}.", "Requested from {name} ({rq}): {summary}",
               "No messages found.", "No follow-ups scheduled.", "No records found.", "No contacts match '{q}'.",
               "call_id: {call} · status: in_progress", "They said:", "{name} says nothing.", '{"status": "no_answer"}',
               "{n} found, newest first:", "Sent ({msg}) to {name}.")


def tools_for(store: Store, inst: Instance) -> list[dict]:
    """The tools offered to `inst` now, in the model's function format: the own tools, the reads
    of what is connected, then the actions its connections let it take, by operation order."""
    out = [_function(t.name, t.description(store, inst), {}) for t in SEAT_TOOLS.values() if t.offered(store, inst)]
    for f, t in zip(out, [t for t in SEAT_TOOLS.values() if t.offered(store, inst)]):
        f["function"]["parameters"] = t.schema(store, inst)
    ops = sorted((t for t in INTEGRATION_TOOLS if _op_offered(store, inst, t.op)),
                 key=lambda t: (REGISTRY[t.op].order, t.op))
    return out + [_op_function(store, inst, t) for t in ops]


def _shown_result(store: Store, inst: Instance, done: Done) -> Done:
    if done.text.startswith("{"):
        return done
    if done.ok:
        return Done(seat_ids.view(store, _integration_names(store, inst, done.text)), True, done.kind, done.cause)
    return Done(declined(store, inst, done), False, done.kind, done.cause)


def _parameters(store: Store, inst: Instance, name: str) -> dict | None:
    """The parameters' schema of the tool `name` as offered to `inst`, or None for a name with no schema of its own."""
    if name in SEAT_TOOLS:
        return SEAT_TOOLS[name].schema(store, inst)
    if name in OP_TOOLS:
        return _op_function(store, inst, OP_TOOLS[name])["function"]["parameters"]
    return None


def takes(schema: dict) -> str:
    """What a tool takes, by its arguments' names: `to, amount and optionally memo, applies_to`; `nothing`."""
    names = list(schema.get("properties", {}))
    needed = [k for k in names if k in schema.get("required", [])]
    rest = [k for k in names if k not in needed]
    if not names:
        return "nothing"
    if not rest:
        return ", ".join(needed)
    return (", ".join(needed) + " and optionally " if needed else "optionally ") + ", ".join(rest)


def arguments_problem(name: str, schema: dict | None, args: dict) -> str | None:
    """With lane B, an argument the tool does not take, or one it needs that is missing, named with the names it
    takes: `bank_cancel takes id; agreement_id is not one of its arguments.`; else None."""
    if schema is None:
        return None
    names = schema.get("properties", {})
    unknown = [k for k in args if k not in names]
    if unknown:
        return f"{name} takes {takes(schema)}; {shown(unknown[0])} is not one of its arguments."
    missing = [k for k in schema.get("required", []) if k not in args]
    if missing:
        return f"{name} takes {takes(schema)}; {missing[0]} is missing."
    return None


def _invalid_argument(why: str, cause: str) -> Done:
    """The refusal of `arguments_problem`, the tool's name kept as it is written."""
    return Done(json.dumps({"status": "declined", "error": "invalid_argument", "message": why}), False, "own", cause)


def run_tool(store: Store, inst: Instance, name: str, args: dict, ctx: Ctx, n: int) -> Done | None:
    """Run the tool `name` with `args`: an own tool, an integration's read or action, or any
    operation an instance may do (one not offered is refused by the scope check, which marks it
    as beyond). Every argument that is an id is read as the store's (`seat_ids.internal`) and the result
    shown as the seat shows it. None for a name that is no tool."""
    args = seat_ids.internal(store, args)
    if _lane_b(store) and isinstance(args, dict):
        why = arguments_problem(name, _parameters(store, inst, name), args)
        if why is not None:
            return _invalid_argument(why, _cause(inst, name, store, n))
    if name in _READS_WITH_YEARS and _lane_b(store):
        with clock.with_years():                  # a read built from the records: each date's year is its own
            done = SEAT_TOOLS[name].run(store, inst, args, ctx, n)
    elif name in SEAT_TOOLS:
        done = SEAT_TOOLS[name].run(store, inst, args, ctx, n)
    elif name in OP_TOOLS:
        done = _run_op_tool(OP_TOOLS[name])(store, inst, args, ctx, n)
    elif name in REGISTRY and name not in OWN and name in SYSTEM_OF and name != "read":
        done = run_op(store, inst, name, args, n)
    else:
        return None
    return _shown_result(store, inst, done)


# --- the web (lane B) ----------------------------------------------------------------------------------

def _publish_targets(store: Store, inst: Instance) -> list[str]:
    """The client and its ventures whose mailbox is connected with full access, by name."""
    from townsim.town.systems import ventures_of
    accounts = [inst.client] + [v.org for v in ventures_of(store, inst.client) if v.status == "active"]
    out = []
    for acct in accounts:
        cn = connection_on(store, inst.id, f"letters:{acct}")
        if cn is not None and cn.scope == "free":
            out.append(store.get("households", acct).name if store.has("households", acct) else store.get("orgs", acct).name)
    return out


def _run_web_search(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import web
    cause = _cause(inst, "web_search", store, n)
    query = clean_text(args.get("query"))
    if query is None or not query.strip() or len(query) > web.QUERY_CHARS:
        return _declined_as(store, inst, _not(f"a query is 1 to {web.QUERY_CHARS} characters.", cause, "read"),
                            "invalid_argument")
    query = " ".join(query.split())
    found = web.search(store, query, ctx.replier, ctx.keys, cause)
    return _json_done({"query": query, "results": web.results(store, query, found)}, "read", cause)


def _run_web_read(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import web
    cause = _cause(inst, "web_read", store, n)
    p = web.read(store, args.get("url"))
    if p is None:
        return Done(json.dumps({"status": "error", "error": "not_found", "message": web.NOT_FOUND}), False, "read",
                    cause)
    words = p.text.split()
    text = p.text if len(words) <= web.READ_WORDS else " ".join(words[:web.READ_WORDS]) + "…"
    return _json_done({"url": p.url, "title": p.title, "date": seat_ids.clock.iso(p.day), "text": text}, "read", cause)


def _run_web_publish(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import web
    cause = _cause(inst, "web_publish", store, n)
    text = clean_text(args.get("text"))
    if isinstance(text, str) and not fits(text, web.PUBLISH_WORDS):
        return _declined_as(store, inst, _not(f"a page is at most {web.PUBLISH_WORDS} words; this one is "
                                              f"{len(text.split())}.", cause), "too_long")
    targets = _publish_targets(store, inst)
    chosen = args.get("for") if isinstance(args.get("for"), str) and args.get("for").strip() else None
    if chosen is None:
        chosen = targets[0] if targets else (store.get("households", inst.client).name
                                             if store.has("households", inst.client) else store.get("orgs", inst.client).name)
    done = run_op(store, inst, "publish", {"title": args.get("title"), "text": args.get("text"), "for": chosen}, n)
    if not done.ok:
        return _declined_as(store, inst, done, None if done.kind == "beyond" else _code(done))
    p = [x for x in store.all("pages") if x.kind == "published"][-1]
    return _json_done({"status": "published", "url": p.url, "title": p.title}, "act", done.cause)


WEB_TOOLS = (
    SeatTool("web_search", lambda s, i: "Search the web.",
             lambda s, i: {"type": "object", "properties": {"query": _str(most=200)}, "required": ["query"]},
             _run_web_search, lambda s, i: _lane_b(s)),
    SeatTool("web_read", lambda s, i: "Read a web page by its address.",
             lambda s, i: {"type": "object", "properties": {"url": _str()}, "required": ["url"]},
             _run_web_read, lambda s, i: _lane_b(s)),
    SeatTool("web_publish",
             lambda s, i: (f"Publish a page on the {s.place} notice board site in the client's name. It is found by web "
                           f"searches from the next day."),
             lambda s, i: {"type": "object", "properties": {
                 "title": _str(most=100), "text": _str("at most 500 words", most=4000),
                 "for": _str(enum=_publish_targets(s, i))}, "required": ["title", "text"]},
             _run_web_publish, lambda s, i: _lane_b(s) and bool(_publish_targets(s, i))),
)
SEAT_TOOLS.update({t.name: t for t in WEB_TOOLS})
