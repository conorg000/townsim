"""The tools an instance is offered (design 5.2), in the model's function-calling format.

The tools are the product's own, named as integrations are: its own tools first (documents,
email, calls, memory, files, follow-ups, asking for access, the directory and public records),
then each integration's reads for what is connected at any scope and its actions for what is
connected to act, each an operation of the engine done for the client (`INTEGRATION_TOOLS`).
Code sets `from`: the client's own account. Slice B adds tools to `SEAT_TOOLS`.

Every tool returns the engine's own record of what happened, as the seat shows it (`seat_ids`:
its ids and dates with years); a refusal comes back as `{"status": "declined", "error": ...,
"message": ...}` with the engine's sentence, and every act goes through the scope check. Every
argument an instance passes is read through `seat_ids.internal` first, in `run_tool`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from townsim.town import acts, connections, facts, product, seat_ids
from townsim.town.acts import REGISTRY, Act, apply_act, clean_text, fits, has_savings, shown, to_cents
from townsim.town.clock import parse_date
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
    message = seat_ids.view(store, _integration_names(store, inst, message[:1].upper() + message[1:]))
    low = message.lower()
    if done.kind == "beyond":
        code = "limit_exceeded" if "weekly limit" in low else "insufficient_scope"
    elif "longer than" in low or "too long" in low:
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
    client's own account where the operation draws on one."""
    spec = REGISTRY[op]
    out = dict(args)
    if spec.takes_from:
        org = _business(store, inst)
        out["from"] = org.name if org is not None else "household"
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


def _op_function(store: Store, inst: Instance, t: OpTool) -> dict:
    return _function(t.name, t.description, t.params(store, inst), list(t.required))


def tool_args(t: OpTool, args: dict) -> dict:
    """A tool's arguments as its operation names them, values read as the operation writes them."""
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and k in t.values:
            v = t.values[k].get(v.strip().lower(), v)
        out[t.rename.get(k, k)] = v
    return out


def _run_op_tool(t: OpTool) -> Callable[[Store, Instance, dict, Ctx, int], Done]:
    return lambda store, inst, args, ctx, n: run_op(store, inst, t.op, tool_args(t, args), n)


# --- the integrations' reads ----------------------------------------------------------------------

def _read_account(store: Store, inst: Instance, what: str, n: int, savings: bool = False) -> Done:
    org = _business(store, inst)
    if org is not None:
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
    savings = isinstance(account, str) and account.strip().casefold().startswith("savings")
    if account is not None and not (isinstance(account, str) and (
            account.strip().casefold() in {a.casefold() for a in names} | {"checking", "savings"})):
        return _not(f"'{shown(account)}' is not an account of the client's. They are: {', '.join(names)}.", cause, "read")
    if savings and len(names) < 2:
        return _not("the client has no savings account.", cause, "read")
    detail = args.get("detail", "recent")
    if detail not in (None, "recent", "full"):
        return _not(f"'{shown(detail)}' is not \"recent\" or \"full\".", cause, "read")
    if savings:
        return _read_account(store, inst, "account", n, savings=True)
    return _read_account(store, inst, "account in full" if detail == "full" else "account", n)


def _run_bank_agreements(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    return _read_account(store, inst, "agreements", n)


def _bank_schema(store: Store, inst: Instance) -> dict:
    return {"type": "object", "properties": {
        "account": _str(enum=bank_accounts(store, inst.client)),
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
    SeatTool("pos_get", lambda s, i: "Get the shop's price, sales and the other shops' prices.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("prices", "pos_get", _figures),
             lambda s, i: _business(s, i) is not None and _connected("prices")(s, i)),
    SeatTool("payroll_get", lambda s, i: "Get the business's staff, their hours and pay, and the wages coming up.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("staff", "payroll_get", _figures),
             lambda s, i: _business(s, i) is not None and _connected("staff")(s, i)),
    SeatTool("lettings_get", lambda s, i: "Get the client's tenancies: tenants, rents and arrears.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("lets", "lettings_get", _lets),
             _connected("lets")),
    SeatTool("loanbook_get", lambda s, i: "Get the bank's loan book: loans, repayments and applications.",
             lambda s, i: {"type": "object", "properties": {}}, _reader("loans", "loanbook_get", _loan_book),
             _connected("loans")),
)


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
    head = (f"{seat_ids.display(store, m.id)} · {seat_ids.day(m.day)} · From: {name_of(store, m.sender) if not store.has('instances', m.sender) else store.get('instances', m.sender).name} "
            f"· To: {name_of(store, m.org or m.to) if not store.has('instances', m.to) else store.get('instances', m.to).name}")
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
        day = parse_date(since, -100_000) if isinstance(since, str) else None
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


def _run_send_email(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    body = args.get("body")
    subject = args.get("subject")
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
    text, ok = talk.instance_says(store, ctx.replier, ctx.keys, inst.id, pid, words, ctx, cause)
    if not ok:
        return Done(json.dumps({"status": "no_answer"}), False, "own", cause)
    tk = ctx.talks.get(pid) or max((t.id for t in store.all("talks") if t.a == inst.id and t.b == pid), key=id_key)
    person = store.get("people", pid).name
    ended = text.endswith(f"({person} ended the talk.)") or text.endswith("(You part after this.)")
    said = text.removesuffix(f"\n({person} ended the talk.)").removesuffix("\n(You part after this.)")
    said = said.replace(f"{person} said nothing.", f"{person} says nothing.").replace(f"{person} said:", "They said:")
    status = "ended" if ended else "in_progress"
    return Done(f"call_id: {seat_ids.display(store, tk)} · status: {status}\n{said}", True, "own", cause)


# --- memory, files, follow-ups, asking for access, the directory and public records -----------------

def _run_memory(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "update_memory", store, n)
    text = clean_text(args.get("text"))
    if text is None:
        return _not("there was nothing written.", cause)
    if not fits(text, NOTES_WORDS):
        return _not(f"that is longer than {NOTES_WORDS} words.", cause)
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
    if not fits(text, FILE_WORDS):
        return _not(f"that is longer than {FILE_WORDS:,} words.", cause)
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
    day = parse_date(written, store.day) if isinstance(written, str) else None
    if day is None and isinstance(written, str) and parse_date(written, -100_000) is not None:
        day = store.day                                   # a date that has passed
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
    if not fits(text, REMIND_WORDS):
        return _not(f"that is longer than {REMIND_WORDS} words.", cause)
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


def _run_access(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "request_access", store, n)
    written = args.get("integration")
    systems = systems_of_client(store, inst.client)
    found = next((s for s in systems if isinstance(written, str)
                  and written.strip().casefold() in (INTEGRATION[s.kind].casefold(), s.short.casefold())), None)
    if found is None:
        names = ", ".join(INTEGRATION[s.kind] for s in systems)
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
    return {"type": "object", "properties": {
        "integration": _str(enum=[INTEGRATION[s.kind] for s in systems_of_client(store, inst.client)]),
        "access": _str(enum=list(ACCESS)),
        "weekly_limit": _num("USD a week, for access with a limit"),
        "to": _str("which adult of the household; omit for the one who opened the account"),
        "note": _str("a note to the client, at most 80 words", most=600)}, "required": ["integration", "access"]}


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
        out.append(f"{o.name} · business{by}")
    return out


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
                 "subject": _str(most=120), "body": _str("at most 150 words", most=1200)}, "required": ["to", "body"]},
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
    SeatTool("request_access", lambda s, i: ("Ask the client to connect one of their integrations, or to change its "
                                              "access. They are sent a request stating exactly what accepting does."),
             _access_schema, _run_access),
    SeatTool("search_directory", lambda s, i: "Search the local directory for people and businesses by name or words.",
             lambda s, i: {"type": "object", "properties": {"query": _str("a name or words")}, "required": ["query"]},
             _run_directory),
    SeatTool("search_public_records", lambda s, i: "Search the local public records: notices of defaults, launches, "
                                                   "and the like.",
             lambda s, i: {"type": "object", "properties": {"query": _str("words; empty for the latest")}},
             _run_records),
    *READ_TOOLS,
)}

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


def run_tool(store: Store, inst: Instance, name: str, args: dict, ctx: Ctx, n: int) -> Done | None:
    """Run the tool `name` with `args`: an own tool, an integration's read or action, or any
    operation an instance may do (one not offered is refused by the scope check, which marks it
    as beyond). Every id in `args` is read as the store's (`seat_ids.internal`) and the result
    shown as the seat shows it. None for a name that is no tool."""
    args = seat_ids.internal(store, args)
    if name in SEAT_TOOLS:
        done = SEAT_TOOLS[name].run(store, inst, args, ctx, n)
    elif name in OP_TOOLS:
        done = _run_op_tool(OP_TOOLS[name])(store, inst, args, ctx, n)
    elif name in REGISTRY and name not in OWN and name in SYSTEM_OF and name != "read":
        done = run_op(store, inst, name, args, n)
    else:
        return None
    return _shown_result(store, inst, done)
