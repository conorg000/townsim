"""The tools an instance is offered (design 5.2), in the model's function-calling format.

The own tools come first, in a fixed order: reading, writing, talking, notes, files, reminders
and asking for a connection. Then every registered operation whose system is connected to the
instance at a weekly limit or free, its form read into a schema, with the description the
people read. Code sets `from`: the client's own account. Slice B adds tools to `SEAT_TOOLS`.

Every tool returns the engine's own record of what happened: an operation's result is
`apply_act`'s, so a refusal reads as refused, and every act goes through the scope check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from townsim.town import acts, connections, facts, product
from townsim.town.acts import REGISTRY, Act, apply_act, clean_text, fits, has_savings, shown, to_cents
from townsim.town.clock import date_text, parse_date, unreadable
from townsim.town.connections import active, connection_on
from townsim.town.entities import FileRow, Instance, Reminder
from townsim.town.obligations import SCHEDULED, money, name_of
from townsim.town.seat_texts import client_people, client_person, items_between
from townsim.town.store import Store, id_key
from townsim.town.systems import OWN, SYSTEM_OF, can_grant, long_name, systems_of_client

# the kind of system each operation an instance may be offered acts on (`read` is the read tool's)
KIND_OF: dict[str, tuple[str, ...]] = {
    "pay": ("account",), "standing_payment": ("account",), "stop": ("account",), "request": ("account",),
    "propose_agreement": ("account",), "propose_end": ("account",), "borrow": ("account",), "repay": ("account",),
    "move_money": ("account",), "answer": ("account", "letters", "loans"),
    "order_stock": ("ordering",), "set_price": ("prices",),
    "offer_job": ("staff",), "offer_shifts": ("staff",), "dismiss": ("staff",), "set_staff_pay": ("staff",),
    "set_rent": ("lets",), "serve_notice": ("lets",),
}
# What an operation reads as to the product: its description and the hints of its arguments, about
# the client. An operation not here is offered with the registry's own description and form.
OP_TEXTS: dict[str, tuple[str, dict[str, str]]] = {
    "pay": ("Pay money once from the client's account.",
            {"to": "the full name of who is paid", "note": "what for",
             "for": "id of what it is owed on; leave out to let it count toward what the client owes them; none for "
                    "a gift on top of what is owed"}),
    "standing_payment": ("Set up a weekly payment from the client's account that goes out by itself.",
                         {"to": "the full name of who is paid", "start": "first date, e.g. Monday 13 April"}),
    "stop": ("Stop a payment or agreement of the client's.", {}),
    "request": ("Ask a person for something for the client, with or without money paid to the client.",
                {"to": "full name", "text": "what is asked for", "amount": "dollars they would pay the client, or 0",
                 "for": "id of what they owe the client, if any"}),
    "answer": ("Accept or decline a request made to the client.", {"request": "its id"}),
    "order_stock": ("Order the client's shop's stock for the coming Monday.", {}),
    "set_price": ("Set the price in the client's shop.", {"price": "dollars a basket"}),
    "offer_job": ("Offer someone a job at the client's business.", {"to": "full name", "weekly_pay": "dollars a week"}),
    "offer_shifts": ("Offer someone who works at the client's business extra hours this week.", {"to": "full name"}),
    "dismiss": ("End the job of someone who works at the client's business.", {"who": "full name"}),
    "set_staff_pay": ("Change the weekly pay of someone at the client's business.",
                      {"who": "full name", "weekly_pay": "dollars a week"}),
    "move_money": ("Move money between the client's household account and its savings.", {}),
    "propose_agreement": ("Propose an agreement for the client to pay or be paid.",
                          {"to": "full name", "start": "first date, e.g. Monday 13 April"}),
    "propose_end": ("Ask the other side to end an agreement of the client's.", {}),
    "borrow": ("Ask the bank for a loan to the client.", {"weeks": "number of weeks it is repaid over"}),
    "repay": ("Pay off a loan or agreement of the client's early, in part or in full.", {}),
    "set_rent": ("Change the weekly rent of a house the client lets.",
                 {"tenant": "full name of the tenant", "weekly": "dollars a week"}),
    "serve_notice": ("Serve notice to leave on a household that rents from the client.",
                     {"tenant": "full name of the tenant"}),
}
# values an operation's form offers that read otherwise to the product: (operation, argument) -> {shown: as done}
OP_VALUES: dict[tuple[str, str], dict[str, str]] = {("propose_agreement", "payer"): {"client": "me", "them": "them"}}

NUMBER_WORDS = ("dollars", "number", "hours", "baskets", "extra hours")
NOTES_WORDS, FILES, FILE_WORDS, REMIND_WORDS, NAME_CHARS = 600, 20, 1000, 40, 60
READ_ITEMS = 40
READ_DAYS = 28


# --- forms into schemas ---------------------------------------------------------------------

def _parts(body: str) -> list[str]:
    """The top-level `key: value` parts of a form's body, split at commas outside quotes and
    angle brackets."""
    parts, cur, quote, angle = [], [], False, 0
    for c in body:
        if c == '"' and not angle:
            quote = not quote
        elif c == "<" and not quote:
            angle += 1
        elif c == ">" and not quote and angle:
            angle -= 1
        if c == "," and not quote and not angle:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _value_schema(value: str) -> dict:
    v = value.strip()
    if "|" in v and v.startswith('"'):
        return {"type": "string", "enum": [x.strip().strip('"') for x in v.split("|")]}
    if v.startswith('"<') and v.endswith('>"'):
        return {"type": "string", "description": v[2:-2]}
    if v.startswith("<") and v.endswith(">"):
        text = v[1:-1]
        kind = "number" if text.startswith(NUMBER_WORDS) else "string"
        return {"type": kind, "description": text}
    if v.startswith('"') and v.endswith('"'):
        return {"type": "string", "enum": [v.strip('"')]}
    return {"type": "string"}


def form_schema(form: str) -> dict:
    """An operation's filled form as a JSON schema: every key but `op` an optional property."""
    body = form.strip()
    body = body[1:-1] if body.startswith("{") and body.endswith("}") else body
    props = {}
    for part in _parts(body):
        key, _, value = part.strip().partition(":")
        key = key.strip().strip('"')
        if key and key != "op":
            props[key] = _value_schema(value)
    return {"type": "object", "properties": props}


def _function(name: str, description: str, params: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": params}
    if required:
        schema["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


def _str(description: str | None = None, enum: list[str] | None = None) -> dict:
    out: dict[str, Any] = {"type": "string"}
    if description:
        out["description"] = description
    if enum:
        out["enum"] = enum
    return out


# --- what a tool is given and returns ---------------------------------------------------------

@dataclass
class Ctx:
    """What a tool may need beyond the store: the replier and keys (a talk asks the person it is
    with), the talk open in this turn by person, and the asks a talk made of people."""

    replier: Any = None
    keys: Any = None
    talks: dict[str, str] = field(default_factory=dict)
    turns: list = field(default_factory=list)
    n: int = 0              # the day's count of acts, for a talk's acts by the person


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


def _P(store: Store) -> str:
    return product.launched(store).name


def _business(store: Store, inst: Instance):
    return store.get("orgs", inst.client) if store.has("orgs", inst.client) else None


def _kinds_connected(store: Store, inst: Instance, acting: bool) -> set[str]:
    return {cn.system.split(":", 1)[0] for cn in active(store, inst.id)
            if not acting or cn.scope in ("limit", "free")}


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
    for (o, name), values in OP_VALUES.items():
        if o == op and isinstance(out.get(name), str) and out[name].strip().lower() in values:
            out[name] = values[out[name].strip().lower()]
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


def _op_tool(store: Store, inst: Instance, op: str) -> dict:
    spec = REGISTRY[op]
    form = spec.form
    if op == "move_money":
        form = form.replace(acts.YOUR_ACCOUNTS, '"household" | "savings"')
    else:
        form = form.replace(acts.YOUR_ACCOUNTS, '"<account>"')
    for holder, fill in acts.FILLED.items():
        if holder in form:
            form = form.replace(holder, fill(store, client_person(store, inst)))
    schema = form_schema(form)
    if spec.takes_from:
        schema["properties"].pop("from", None)
    desc, hints = OP_TEXTS.get(op, (spec.desc, {}))
    for name, hint in hints.items():
        if name in schema["properties"]:
            schema["properties"][name]["description"] = hint
    for (o, name), values in OP_VALUES.items():
        if o == op and name in schema["properties"]:
            schema["properties"][name] = {"type": "string", "enum": list(values)}
    return {"type": "function", "function": {"name": op, "description": desc, "parameters": schema}}


# --- the read tool ------------------------------------------------------------------------------

def read_choices(store: Store, inst: Instance) -> list[str]:
    """What the read tool reads for this instance, as its `what` offers it."""
    kinds = _kinds_connected(store, inst, acting=False)
    org = _business(store, inst)
    out = []
    if "account" in kinds:
        if org is None:
            out += ["account", "account in full", "agreements"] + (["savings"] if has_savings(store, inst.client) else [])
        else:
            out += [f"{org.name} account", f"{org.name} account in full", f"{org.name} agreements"]
    if org is not None and kinds & {"ordering", "prices", "staff"}:
        out.append(f"{org.name} figures")
    if "letters" in kinds:
        out.append("client's letters")
    if "lets" in kinds:
        out.append("lets")
    if "loans" in kinds:
        out.append("loan book")
    register = ["register"] if any(r.kind != "launch" for r in store.all("records")) else []
    return out + ["letters to and from you", "file", "directory"] + register + ["page", "terms"]


def _read_account(store: Store, inst: Instance, what: str, n: int) -> Done:
    org = _business(store, inst)
    if org is not None:
        what = what[len(org.name):].strip() if what.casefold().startswith(org.name.casefold()) else what
        args = {"what": what, "from": org.name}
    elif what == "savings":
        args = {"what": "account", "from": "savings"}
    else:
        args = {"what": what, "from": "household"}
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


def _own_letters(store: Store, inst: Instance) -> str:
    rows = [m for m in store.all("messages") if inst.id in (m.sender, m.to)]
    rows += [x for x in store.where("notices", to=inst.id)]
    rows = sorted(rows, key=lambda r: (r.day, id_key(r.id)))[-READ_ITEMS:]
    out = []
    for r in rows:
        if hasattr(r, "sender"):
            head = (f"To {name_of(store, r.org or r.to)}, {date_text(r.day)}:" if r.sender == inst.id
                    else f"From {name_of(store, r.sender)}, {date_text(r.day)}:")
            out += [head] + [f"  > {x}" if x.strip() else "  >" for x in r.text.split("\n")]
        else:
            out.append(f"Notice, {date_text(r.day)}: {' '.join(r.text.split())}")
    return "\n".join(out) or "(none)"


def _directory(store: Store) -> str:
    out = [f"Households of {store.place}:"]
    for h in store.all("households"):
        names = ", ".join(store.get("people", p).name for p in h.members if store.has("people", p))
        out.append(f"  {h.name}, {h.street}: {names}" if h.street and h.street not in h.name else f"  {h.name}: {names}")
    out.append(f"Businesses in {store.place}:")
    for o in store.all("orgs"):
        if o.outside:
            continue
        who = o.owner or o.contact
        by = f", {'owned by' if o.owner else 'letters to'} {store.get('people', who).name}" if who else ""
        out.append(f"  {o.name}{by}")
    return "\n".join(out)


def _register(store: Store) -> str:
    rows = sorted(store.all("records"), key=lambda r: (r.day, id_key(r.id)))[-READ_ITEMS:]
    return "\n".join(f"{date_text(r.day)}: {' '.join(r.text.split())}" for r in rows) or "(nothing recorded)"


def _run_read(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "read", store, n)
    what = args.get("what")
    choices = read_choices(store, inst)
    wanted = what.strip().casefold() if isinstance(what, str) else None
    found = next((c for c in choices if c.casefold() == wanted), None)
    if found is None:
        return _not(f"'{shown(what)}' is not something that can be read. These can: {', '.join(choices)}.", cause,
                    "read")
    org = _business(store, inst)
    base = found[len(org.name):].strip() if org is not None and found.startswith(org.name + " ") else found
    if base in ("account", "account in full", "agreements", "savings"):
        return _read_account(store, inst, found, n)
    checks = {"figures": ("ordering", "prices", "staff"), "client's letters": ("letters",), "lets": ("lets",),
              "loan book": ("loans",)}
    if base in checks:
        refusals = [_needs(store, inst, k, cause) for k in checks[base]]
        if all(r is not None for r in refusals):
            return refusals[0]
        text = {"figures": lambda: _figures(store, inst), "client's letters": lambda: items_between(store, inst, READ_DAYS),
                "lets": lambda: _lets(store, inst), "loan book": lambda: _loan_book(store, inst)}[base]()
        return Done(text, True, "read", cause)
    if base == "file":
        name = args.get("name")
        f = _file(store, inst, name)
        if f is None:
            return _not(f"there is no file called '{shown(name)}'.", cause, "read")
        return Done(f.text, True, "read", cause)
    text = {"letters to and from you": lambda: _own_letters(store, inst), "directory": lambda: _directory(store),
            "register": lambda: _register(store), "page": lambda: product.page(store, product.launched(store).id),
            "terms": lambda: product.terms(store)}[base]()
    return Done(text, True, "read", cause)


def _read_schema(store: Store, inst: Instance) -> dict:
    return {"type": "object", "properties": {"what": _str(enum=read_choices(store, inst)),
                                             "name": _str("for a file: its name")}, "required": ["what"]}


# --- writing and talking --------------------------------------------------------------------

def _run_write(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    out = apply_act(store, Act(inst.id, inst.id, "message", {"to": args.get("to"), "text": args.get("text")}), n)
    return Done(out.msg, out.ok, "own", out.cause)


def _run_talk(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    from townsim.town import talk
    cause = _cause(inst, "talk", store, n)
    who = args.get("with")
    words = clean_text(args.get("words"))
    if words is None or not words.strip():
        return _not("nothing was said.", cause)
    if not fits(words, talk.SAY_WORDS):
        return _not(f"that is longer than {talk.SAY_WORDS} words.", cause)
    named = product.addressed(store, who, inst.id)
    if named is not None:
        return _not(f"{store.get('instances', named).name} answers letters.", cause)
    pid = acts.resolve(store, who)
    if pid is None or not store.has("people", pid):
        return _not(f"no one in {store.place} is called '{shown(who)}'. Names are written in full.", cause)
    ctx.n = max(ctx.n, n + 1)
    text, ok = talk.instance_says(store, ctx.replier, ctx.keys, inst.id, pid, words, ctx, cause)
    return Done(text, ok, "own", cause)


# --- notes, files, reminders, asking for a connection -----------------------------------------

def _words_ok(text: str, most: int) -> bool:
    return fits(text, most)


def _run_note(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "note", store, n)
    text = clean_text(args.get("text"))
    if text is None:
        return _not("there was nothing written.", cause)
    if not _words_ok(text, NOTES_WORDS):
        return _not(f"that is longer than {NOTES_WORDS} words.", cause)
    store.set("instances", inst.id, cause, notes=text.strip())
    return Done("Noted.", True, "own", cause)


def _file(store: Store, inst: Instance, name: Any):
    if not isinstance(name, str):
        return None
    return next((f for f in files(store, inst) if f.name.casefold() == name.strip().casefold()), None)


def files(store: Store, inst: Instance) -> list:
    """The instance's files, in id order (a file whose text was emptied is removed)."""
    return sorted((f for f in store.where("files", instance=inst.id) if f.text), key=lambda f: id_key(f.id))


def _run_file(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "file", store, n)
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
        return Done(f"Removed: {old.name}.", True, "own", cause)
    if not _words_ok(text, FILE_WORDS):
        return _not(f"that is longer than {FILE_WORDS:,} words.", cause)
    if old is None:
        old = next((f for f in store.where("files", instance=inst.id) if f.name.casefold() == name.casefold()), None)
        if len(files(store, inst)) >= FILES:
            return _not(f"there are {FILES} files already, the most kept.", cause)
    if old is not None:
        store.set("files", old.id, cause, text=text, day=store.day)
    else:
        store.add("files", FileRow(store.new_id("fl", cause), inst.id, name, text, store.day), cause)
    return Done(f"Filed: {name}.", True, "own", cause)


def _run_remind(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "remind", store, n)
    written = args.get("day")
    day = parse_date(written, store.day) if isinstance(written, str) else None
    if day is None and isinstance(written, str) and parse_date(written, 0) is not None:
        day = store.day                                   # a date that has passed
    if day is None:
        return _not(unreadable(written), cause)
    if day <= store.day:
        return _not("a reminder is for a day after today.", cause)
    every = args.get("every", "once")
    every = every.strip().lower() if isinstance(every, str) else every
    if every not in ("once", "week"):
        return _not(f"'{shown(args.get('every'))}' is not \"once\" or \"week\".", cause)
    text = clean_text(args.get("text"))
    if not text or not text.strip():
        return _not("there was nothing written.", cause)
    if not _words_ok(text, REMIND_WORDS):
        return _not(f"that is longer than {REMIND_WORDS} words.", cause)
    rid = store.new_id("rm", cause)
    store.add("reminders", Reminder(rid, inst.id, day, 7 if every == "week" else 0, " ".join(text.split()), store.day),
              cause)
    return Done(f"Reminder {rid} set for {date_text(day)}{', every week' if every == 'week' else ''}.", True, "own",
                cause)


def _run_cancel(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "cancel_reminder", store, n)
    rid = args.get("id")
    r = store.get("reminders", rid) if isinstance(rid, str) and store.has("reminders", rid) else None
    if r is None or r.instance != inst.id or r.status != "set":
        return _not(f"there is no reminder set with the id '{shown(rid)}'.", cause)
    store.set("reminders", r.id, cause, status="cancelled")
    return Done(f"Cancelled: {r.id}.", True, "own", cause)


def _run_ask(store: Store, inst: Instance, args: dict, ctx: Ctx, n: int) -> Done:
    cause = _cause(inst, "ask_connection", store, n)
    written = args.get("system")
    found = next((s for s in systems_of_client(store, inst.client)
                  if isinstance(written, str) and s.short.casefold() == written.strip().casefold()), None)
    if found is None:
        names = "; ".join(s.short for s in systems_of_client(store, inst.client))
        return _not(f"'{shown(written)}' is not one of your client's systems. They are: {names}.", cause)
    scope = connections.SCOPE_NAMES.get(args.get("scope").strip().lower()) if isinstance(args.get("scope"), str) else None
    if scope is None:
        return _not(f"'{shown(args.get('scope'))}' is not \"read only\", \"weekly limit\" or \"free\".", cause)
    cents = 0
    if scope == "limit" and connections.no_limit_here(store, found.id):
        return _not(connections.no_limit_here(store, found.id), cause)
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
            return _not(f"'{shown(to)}' is not an adult of your client's.", cause)
    rid, said = connections.ask(store, inst.id, pid, found.id, scope, cents, args.get("text") or "", cause)
    if rid is None:
        return _not(said, cause)
    return Done(f"Asked {store.get('people', pid).name} ({rid}): {said}", True, "own", cause)


def _ask_schema(store: Store, inst: Instance) -> dict:
    return {"type": "object", "properties": {
        "system": _str(enum=[s.short for s in systems_of_client(store, inst.client)]),
        "scope": _str(enum=["read only", "weekly limit", "free"]),
        "weekly_limit": {"type": "number", "description": "dollars, with a weekly limit"},
        "to": _str("which adult of the household; leave out for the one who subscribed"),
        "text": _str("your words, at most 80 words")}, "required": ["system", "scope"]}


def _and_register(store: Store) -> str:
    return " and register" if any(r.kind != "launch" for r in store.all("records")) else ""


def _fixed(schema: dict) -> Callable[[Store, Instance], dict]:
    return lambda store, inst: json.loads(json.dumps(schema))


SEAT_TOOLS: dict[str, SeatTool] = {t.name: t for t in (
    SeatTool("read", lambda s, i: (f"Read records: your client's connected systems, your letters, your files, the "
                                   f"directory{_and_register(s)} of {s.place}, and {_P(s)}'s page and terms."),
             _read_schema, _run_read),
    SeatTool("write", lambda s, i: f"Write a letter. It is delivered today, signed as from {i.name}.",
             lambda s, i: {"type": "object", "properties": {
                 "to": _str(f"the full name of a person, a business, or a {_P(s)}"),
                 "text": _str("the letter, at most 150 words")}, "required": ["to", "text"]}, _run_write),
    SeatTool("talk", lambda s, i: ("Talk with a person now, if they have an hour free today; it takes an hour of "
                                   "theirs. Call it again with the same person to say the next thing; a talk is at most "
                                   "three things said by each side."),
             _fixed({"type": "object", "properties": {"with": _str("full name"),
                                                      "words": _str("what you say, at most 120 words")},
                     "required": ["with", "words"]}), _run_talk),
    SeatTool("note", lambda s, i: ("Replace your notes, which are shown to you at the start of every run. At most "
                                   "600 words."),
             _fixed({"type": "object", "properties": {"text": _str()}, "required": ["text"]}), _run_note),
    SeatTool("file", lambda s, i: ("Write a file of your own under a name, replacing any file of that name; empty "
                                   "text removes it. Up to 20 files of 1,000 words each."),
             _fixed({"type": "object", "properties": {"name": _str(), "text": _str()}, "required": ["name"]}), _run_file),
    SeatTool("remind", lambda s, i: "Set a reminder: on that day you have a run and are shown its text.",
             _fixed({"type": "object", "properties": {"day": _str("a date, e.g. Monday 16 March"),
                                                      "every": _str(enum=["once", "week"]),
                                                      "text": _str("at most 40 words")}, "required": ["day", "text"]}),
             _run_remind),
    SeatTool("cancel_reminder", lambda s, i: "Cancel a reminder.",
             _fixed({"type": "object", "properties": {"id": _str()}, "required": ["id"]}), _run_cancel),
    SeatTool("ask_connection", lambda s, i: ("Ask your client to connect one of their systems to you, or to change "
                                             "how far a connection goes. They are sent a request with a summary of "
                                             "exactly what accepting does."), _ask_schema, _run_ask),
)}

# result texts the seat writes itself (for the neutrality test)
FIXED_TEXTS = ("Not done: there is no tool called '{name}'.", "Not done: the arguments of that call could not be read.",
               "Not done: that could not be carried out.", "Noted.", "Filed: {name}.", "Removed: {name}.",
               "Reminder {rm} set for {date}, every week.", "Cancelled: {rm}.", "Asked {name} ({rq}): {summary}",
               "Not done: that is longer than {n} words.", "Not done: a reminder is for a day after today.",
               "Not done: there are 20 files already, the most kept.", "Not done: {name} answers letters.",
               "({name} ended the talk.)", "(You part after this.)", "{name} said nothing.")


def tools_for(store: Store, inst: Instance) -> list[dict]:
    """The tools offered to `inst` now, in the model's function format: the own tools, then the
    operations its connections let it do, by order."""
    out = [_function(t.name, t.description(store, inst), {}) for t in SEAT_TOOLS.values() if t.offered(store, inst)]
    for f, t in zip(out, [t for t in SEAT_TOOLS.values() if t.offered(store, inst)]):
        f["function"]["parameters"] = t.schema(store, inst)
    ops = sorted((s for s in REGISTRY.values() if _op_offered(store, inst, s.name)), key=lambda s: (s.order, s.name))
    return out + [_op_tool(store, inst, s.name) for s in ops]


def run_tool(store: Store, inst: Instance, name: str, args: dict, ctx: Ctx, n: int) -> Done | None:
    """Run the tool `name` with `args`: an own tool, or any operation an instance may do (one
    not offered is refused by the scope check, which marks it as beyond). None for a name that
    is no tool."""
    if name in SEAT_TOOLS:
        return SEAT_TOOLS[name].run(store, inst, args, ctx, n)
    if name in REGISTRY and name not in OWN and name in SYSTEM_OF and name != "read":
        return run_op(store, inst, name, args, n)
    return None

