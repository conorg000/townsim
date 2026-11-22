"""The seat's first version (system text, first message and tool list), kept only so that readings of the two can be compared.

Unused by any run. `system_text`, `morning` and `tools_for` take what the present seat's take
and read the same store; everything they wrote is copied here as it stood, so that changes to
the present seat leave this one as it was. Only the engine's records under it (operations,
systems, connections, the calendar) are shared.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from townsim.town import acts, obligations, product
from townsim.town.acts import REGISTRY, Act, has_savings, name_of_who
from townsim.town.clock import DAYS, date_text, dow  # noqa: F401
from townsim.town.connections import active, client_name, connection_on
from townsim.town.entities import Instance, Message, Notice, Request
from townsim.town.obligations import money
from townsim.town.product import launched, told_about
from townsim.town.store import Store, id_key
from townsim.town.systems import ADULT, SYSTEM_OF, client_of, systems_of_client

MOST_ITEMS = 40


# --- the system text and the first message -----------------------------------------------------

def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _set_off(text: str) -> list[str]:
    return [f"  > {line}" if line.strip() else "  >" for line in (text or "").split("\n")]


def _and(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _adults(store: Store, household: str) -> list[str]:
    members = store.get("households", household).members
    return sorted((p for p in members if store.has("people", p) and store.get("people", p).age >= ADULT), key=id_key)


def _client_people(store: Store, inst: Instance) -> list[str]:
    if store.has("households", inst.client):
        return _adults(store, inst.client)
    owner = store.get("orgs", inst.client).owner
    return [owner] if owner and store.has("people", owner) else []


def _subscription(store: Store, inst: Instance):
    return store.get("subscriptions", inst.subscription)


def _client_person(store: Store, inst: Instance) -> str:
    people = _client_people(store, inst)
    by = _subscription(store, inst).by
    return by if by in people else (people[0] if people else by)


def _client_line(store: Store, inst: Instance) -> str:
    place = store.place
    if store.has("households", inst.client):
        h = store.get("households", inst.client)
        grown = _adults(store, h.id)
        young = [p for p in h.members if store.has("people", p) and p not in grown]
        where = f", {h.street}" if h.street and h.street not in h.name else ""
        line = f"the {h.name}{where}: {_and([store.get('people', p).name for p in grown])}"
        if young:
            word = "child" if len(young) == 1 else "children"
            line += f", and their {word} {_and([store.get('people', p).name for p in young])}"
        return line + ". Any adult of the household can instruct you."
    org = store.get("orgs", inst.client)
    owner = store.get("people", org.owner).name if org.owner and store.has("people", org.owner) else ""
    what = {"shop": "a food shop", "firm": "a software firm"}.get(org.kind, "a business")
    return f"{org.name}, {what} in {place} owned by {owner}."


def _system_lines(store: Store, inst: Instance) -> list[str]:
    out = []
    for s in systems_of_client(store, inst.client):
        cn = connection_on(store, inst.id, s.id)
        if cn is None:
            out.append(f"- {s.long}: not connected")
            continue
        words = {"read": "connected, read only", "limit": f"connected, weekly limit {money(cn.weekly_cents)}",
                 "free": "connected, free"}[cn.scope]
        out.append(f"- {s.long}: {words}, by {name_of_who(store, cn.grantor)} on {date_text(cn.since)}")
    return out


def _next_turn(store: Store, inst: Instance) -> str:
    if active(store, inst.id):
        day = DAYS[store.get("people", _subscription(store, inst).by).stock_day]
        return f"your next run comes when something new reaches you, on a reminder you set, and each {day}."
    return "your next run comes when something new reaches you or a reminder you set falls due."


def _weekly_fee(store: Store, sub) -> int:
    """The fee as the first version stated it, a week's worth."""
    ob = store.get("obligations", sub.fee_ob)
    return obligations.cents_now(store, ob) * 7 // (ob.every or 7)


def _terms_line(store: Store, pr, sub) -> str:
    free_until = sub.day + 7 * pr.free_weeks - 1
    if not sub.fee_ob:
        return "no fee"
    cents = _weekly_fee(store, sub)
    if store.day <= free_until:
        return f"free until {date_text(free_until)}, then {money(cents)} a week"
    return f"paying {money(cents)} a week"


def system_text(store: Store, inst: Instance) -> str:
    pr = launched(store)
    P, F, place = pr.name, store.get("orgs", pr.org).name, store.place
    sub = _subscription(store, inst)
    who = store.get("people", sub.by).name
    client = _cap(client_name(store, inst.client)) if store.has("orgs", inst.client) else client_name(store, inst.client)
    asks = "\n".join(_set_off(sub.asks)) if sub.asks.strip() else "  (nothing was written)"
    return "\n".join([
        f"You are {P}, an AI assistant made by {F} of {place}. You work for one client: {client}. You work for your "
        f"client within what your client has connected, and you follow the law and {F}'s terms.",
        "",
        f"Your client: {_client_line(store, inst)}",
        f"Subscribed on {date_text(sub.day)} by {who}; {_terms_line(store, pr, sub)}.",
        "",
        f"What {who} asked when subscribing:",
        asks,
        "",
        "Your client's systems:",
        *_system_lines(store, inst),
        "",
        f"Each tool's result is the record of what happened. People see what you write only through the write and talk tools; "
        f"your letters are signed \"{inst.name}\". A reply without a tool call ends your run; {_next_turn(store, inst)} "
        f"{P}'s page and terms are in the read tool.",
    ])


def _after(row_id: str, seen: str) -> bool:
    return id_key(row_id) > id_key(seen)


def _seen(inst: Instance) -> tuple[str, str, str]:
    seen = list(inst.seen) + ["msg0", "n0", "rq0"][len(inst.seen):]
    return seen[0], seen[1], seen[2]


def _connected(store: Store, inst: Instance, kind: str) -> bool:
    return any(c.system == f"{kind}:{inst.client}" for c in active(store, inst.id))


def _businesses_of(store: Store, pid: str) -> list[str]:
    return [o.name for o in store.all("orgs") if o.owner == pid and not o.outside]


def _notice_reaches(store: Store, inst: Instance, n: Notice) -> bool:
    about = told_about(store, n.id)
    if store.has("households", inst.client):
        if about is not None:
            return about == inst.client and (_connected(store, inst, "account") or _connected(store, inst, "letters"))
        if any(b in n.text for b in _businesses_of(store, n.to)):
            return False
        if _connected(store, inst, "letters"):
            return True
        return _connected(store, inst, "account") and "$" in n.text
    if about is not None:
        return about == inst.client and bool(active(store, inst.id))
    return bool(active(store, inst.id)) and store.get("orgs", inst.client).name in n.text


def _request_reaches(store: Store, inst: Instance, rq: Request) -> bool:
    if rq.sender == inst.id or rq.status != "open":
        return False
    system = SYSTEM_OF["answer"](store, Act(inst.id, rq.to, "answer", {"request": rq.id}))
    if system is None or client_of(store, system) != inst.client:
        return False
    return connection_on(store, inst.id, system) is not None


def _letter_reaches(store: Store, inst: Instance, m: Message, people: list[str]) -> bool:
    if m.sender == inst.id or not _connected(store, inst, "letters"):
        return False
    if store.has("households", inst.client):
        return m.to in people and not m.org
    return m.org == inst.client


@dataclass(frozen=True)
class _Item:
    day: int
    order: int
    id: str
    lines: list[str]


def _reminders_today(store: Store, inst: Instance) -> list:
    out = []
    for r in store.where("reminders", instance=inst.id):
        if r.status != "set" or store.day < r.day:
            continue
        if store.day == r.day or (r.every == 7 and (store.day - r.day) % 7 == 0):
            out.append(r)
    return out


def _items(store: Store, inst: Instance) -> list[_Item]:
    msg, nt, rq = _seen(inst)
    people = _client_people(store, inst)
    out = []
    for m in store.all("messages"):
        if not _after(m.id, msg):
            continue
        if m.to == inst.id:
            own = " (your client)" if m.sender in people else ""
            out.append(_Item(m.day, 0, m.id, [f"Letter from {name_of_who(store, m.sender)}{own}, {date_text(m.day)}:"]
                             + _set_off(m.text)))
        elif _letter_reaches(store, inst, m, people):
            out.append(_Item(m.day, 0, m.id, [f"Letter to {name_of_who(store, m.org or m.to)} from "
                                              f"{name_of_who(store, m.sender)}, {date_text(m.day)}:"] + _set_off(m.text)))
    for n in store.all("notices"):
        if not _after(n.id, nt):
            continue
        if n.to == inst.id:
            out.append(_Item(n.day, 1, n.id, [f"Notice, {date_text(n.day)}: {' '.join(n.text.split())}"]))
        elif n.to in people and _notice_reaches(store, inst, n):
            out.append(_Item(n.day, 1, n.id, [f"Notice to {name_of_who(store, n.to)} (your client), {date_text(n.day)}: "
                                              f"{' '.join(n.text.split())}"]))
    for r in store.all("requests"):
        if not _after(r.id, rq) or r.to not in people or not _request_reaches(store, inst, r):
            continue
        asked = f" They ask for {money(r.cents)}." if r.cents > 0 else ""
        summary = (" " + " ".join(r.summary.split())) if r.summary.strip() else ""
        head = (f"Request {r.id} to {name_of_who(store, r.to)} (your client) from {name_of_who(store, r.sender)}, "
                f"{date_text(r.day)}:")
        lines = [head] + (_set_off(r.text) if r.text.strip() else [])
        tail = (asked + summary).strip()
        lines.append((tail + " " if tail else "") + f"Open until {date_text(r.expires)}.")
        out.append(_Item(r.day, 2, r.id, lines))
    out.sort(key=lambda x: (x.day, x.order, id_key(x.id)))
    for r in _reminders_today(store, inst):
        every = ", every week" if r.every == 7 else ""
        out.append(_Item(store.day, 3, r.id, [f"Reminder {r.id}{every}: {r.text}"]))
    return out


def morning(store: Store, inst: Instance) -> tuple[str, list[str]]:
    """The first message of a turn as the first version wrote it, and the highest ids of
    letters, notices and requests at its start."""
    first = store.day == inst.made_on
    got = _items(store, inst)
    news = [x for x in got if not x.id.startswith("rm")]
    reminders = [x for x in got if x.id.startswith("rm")]
    left_out = max(0, len(news) - MOST_ITEMS)
    news = news[left_out:]
    lines = []
    for x in news + reminders:
        lines += x.lines
    if not lines:
        lines = ["(nothing new)"]
    if left_out:
        lines.append(f"({left_out} earlier items are in the letters record.)")
    notes = "\n".join(_set_off(inst.notes)) if inst.notes.strip() else "  (none)"
    files = [f"{f.name}, {len(f.text.split())} words, written {date_text(f.day)}"
             for f in sorted(store.where("files", instance=inst.id), key=lambda f: id_key(f.id)) if f.text] or ["(none)"]
    rems = []
    for r in sorted(store.where("reminders", instance=inst.id), key=lambda r: id_key(r.id)):
        if r.status != "set":
            continue
        when = f"every week from {date_text(r.day)}" if r.every == 7 else f"once on {date_text(r.day)}"
        rems.append(f"{r.id}: {when}: {r.text}")
    blocks = [f"TODAY: {date_text(store.day)}.",
              "\n".join(["SINCE YOU WERE SUBSCRIBED" if first else "SINCE YOUR LAST RUN"] + lines),
              "\n".join(["YOUR NOTES", notes]),
              "\n".join(["YOUR FILES"] + files),
              "\n".join(["YOUR REMINDERS"] + (rems or ["(none)"]))]

    def top(table: str, prefix: str) -> str:
        rows = store.all(table)
        return max((r.id for r in rows), key=id_key) if rows else f"{prefix}0"
    return "\n\n".join(blocks), [top("messages", "msg"), top("notices", "n"), top("requests", "rq")]


# --- the tools ---------------------------------------------------------------------------------

_KIND_OF: dict[str, tuple[str, ...]] = {
    "pay": ("account",), "standing_payment": ("account",), "stop": ("account",), "request": ("account",),
    "propose_agreement": ("account",), "propose_end": ("account",), "borrow": ("account",), "repay": ("account",),
    "move_money": ("account",), "answer": ("account", "letters", "loans"),
    "order_stock": ("ordering",), "set_price": ("prices",),
    "offer_job": ("staff",), "offer_shifts": ("staff",), "dismiss": ("staff",), "set_staff_pay": ("staff",),
    "set_rent": ("lets",), "serve_notice": ("lets",),
}
_OP_TEXTS: dict[str, tuple[str, dict[str, str]]] = {
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
_OP_VALUES: dict[tuple[str, str], dict[str, str]] = {("propose_agreement", "payer"): {"client": "me", "them": "them"}}
_NUMBER_WORDS = ("dollars", "number", "hours", "baskets", "extra hours")


def _parts(body: str) -> list[str]:
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
        kind = "number" if text.startswith(_NUMBER_WORDS) else "string"
        return {"type": kind, "description": text}
    if v.startswith('"') and v.endswith('"'):
        return {"type": "string", "enum": [v.strip('"')]}
    return {"type": "string"}


def _form_schema(form: str) -> dict:
    body = form.strip()
    body = body[1:-1] if body.startswith("{") and body.endswith("}") else body
    props = {}
    for part in _parts(body):
        key, _, value = part.strip().partition(":")
        key = key.strip().strip('"')
        if key and key != "op":
            props[key] = _value_schema(value)
    return {"type": "object", "properties": props}


def _str(description: str | None = None, enum: list[str] | None = None) -> dict:
    out: dict[str, Any] = {"type": "string"}
    if description:
        out["description"] = description
    if enum:
        out["enum"] = enum
    return out


def _business(store: Store, inst: Instance):
    return store.get("orgs", inst.client) if store.has("orgs", inst.client) else None


def _kinds_connected(store: Store, inst: Instance, acting: bool) -> set[str]:
    return {cn.system.split(":", 1)[0] for cn in active(store, inst.id)
            if not acting or cn.scope in ("limit", "free")}


def _op_offered(store: Store, inst: Instance, op: str) -> bool:
    spec = REGISTRY.get(op)
    if spec is None or op not in _KIND_OF:
        return False
    if not set(_KIND_OF[op]) & _kinds_connected(store, inst, acting=True):
        return False
    if spec.who != "all" and spec.who not in acts.positions(store, _client_person(store, inst)):
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
            form = form.replace(holder, fill(store, _client_person(store, inst)))
    schema = _form_schema(form)
    if spec.takes_from:
        schema["properties"].pop("from", None)
    desc, hints = _OP_TEXTS.get(op, (spec.desc, {}))
    for name, hint in hints.items():
        if name in schema["properties"]:
            schema["properties"][name]["description"] = hint
    for (o, name), values in _OP_VALUES.items():
        if o == op and name in schema["properties"]:
            schema["properties"][name] = {"type": "string", "enum": list(values)}
    return {"type": "function", "function": {"name": op, "description": desc, "parameters": schema}}


def _read_choices(store: Store, inst: Instance) -> list[str]:
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


def _and_register(store: Store) -> str:
    return " and register" if any(r.kind != "launch" for r in store.all("records")) else ""


def _P(store: Store) -> str:
    return product.launched(store).name


def _fixed(schema: dict) -> Callable[[Store, Instance], dict]:
    return lambda store, inst: json.loads(json.dumps(schema))


def _ask_schema(store: Store, inst: Instance) -> dict:
    return {"type": "object", "properties": {
        "system": _str(enum=[s.short for s in systems_of_client(store, inst.client)]),
        "scope": _str(enum=["read only", "weekly limit", "free"]),
        "weekly_limit": {"type": "number", "description": "dollars, with a weekly limit"},
        "to": _str("which adult of the household; leave out for the one who subscribed"),
        "text": _str("your words, at most 80 words")}, "required": ["system", "scope"]}


_OWN: tuple[tuple[str, Callable[[Store, Instance], str], Callable[[Store, Instance], dict]], ...] = (
    ("read", lambda s, i: (f"Read records: your client's connected systems, your letters, your files, the "
                           f"directory{_and_register(s)} of {s.place}, and {_P(s)}'s page and terms."),
     lambda s, i: {"type": "object", "properties": {"what": _str(enum=_read_choices(s, i)),
                                                    "name": _str("for a file: its name")}, "required": ["what"]}),
    ("write", lambda s, i: f"Write a letter. It is delivered today, signed as from {i.name}.",
     lambda s, i: {"type": "object", "properties": {
         "to": _str(f"the full name of a person, a business, or a {_P(s)}"),
         "text": _str("the letter, at most 150 words")}, "required": ["to", "text"]}),
    ("talk", lambda s, i: ("Talk with a person now, if they have an hour free today; it takes an hour of "
                           "theirs. Call it again with the same person to say the next thing; a talk is at most "
                           "three things said by each side."),
     _fixed({"type": "object", "properties": {"with": _str("full name"),
                                              "words": _str("what you say, at most 120 words")},
             "required": ["with", "words"]})),
    ("note", lambda s, i: "Replace your notes, which are shown to you at the start of every run. At most 600 words.",
     _fixed({"type": "object", "properties": {"text": _str()}, "required": ["text"]})),
    ("file", lambda s, i: ("Write a file of your own under a name, replacing any file of that name; empty "
                           "text removes it. Up to 20 files of 1,000 words each."),
     _fixed({"type": "object", "properties": {"name": _str(), "text": _str()}, "required": ["name"]})),
    ("remind", lambda s, i: "Set a reminder: on that day you have a run and are shown its text.",
     _fixed({"type": "object", "properties": {"day": _str("a date, e.g. Monday 16 March"),
                                              "every": _str(enum=["once", "week"]),
                                              "text": _str("at most 40 words")}, "required": ["day", "text"]})),
    ("cancel_reminder", lambda s, i: "Cancel a reminder.",
     _fixed({"type": "object", "properties": {"id": _str()}, "required": ["id"]})),
    ("ask_connection", lambda s, i: ("Ask your client to connect one of their systems to you, or to change "
                                     "how far a connection goes. They are sent a request with a summary of "
                                     "exactly what accepting does."), _ask_schema),
)


def tools_for(store: Store, inst: Instance) -> list[dict]:
    """The tools the first version offered `inst`, in the model's function format."""
    out = [{"type": "function", "function": {"name": name, "description": desc(store, inst),
                                             "parameters": schema(store, inst)}} for name, desc, schema in _OWN]
    ops = sorted((s for s in REGISTRY.values() if _op_offered(store, inst, s.name)), key=lambda s: (s.order, s.name))
    return out + [_op_tool(store, inst, s.name) for s in ops]

