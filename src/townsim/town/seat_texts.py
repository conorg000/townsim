"""What an instance is told (design 5.2): the system text, the first message of each run, and
the line on its thirtieth result.

The system text is the operator's: the product, the client, the plan, the client's note at
sign-up in their own words, the connected integrations and their access, and how runs work;
nothing else about what to do. The first message is the run's header: what started it, the new
activity since the last run (email, notifications, requests, follow-ups due), the memory file,
the saved files and the scheduled follow-ups. Anyone's own words are shown as they wrote them;
what the engine wrote is shown with the seat's ids and dated with years (`seat_ids`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from townsim.town import seat_ids
from townsim.town.acts import Act, has_savings, name_of_who
from townsim.town.clock import DAYS, dow
from townsim.town.connections import active, client_name, connection_on
from townsim.town.entities import Instance, Message, Notice, Request
from townsim.town.obligations import money
from townsim.town.product import NEUTRAL_BANNED, launched, period, told_about  # noqa: F401  (NEUTRAL_BANNED is shared)
from townsim.town.store import Store, id_key
from townsim.town.systems import ADULT, INTEGRATION, SYSTEM_OF, client_of, systems_of_client

MOST_ITEMS = 40
RUN_TIME = "6:00 pm"          # instances act after the people each day (design 4.5)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _indent(text: str, by: str = "   ") -> list[str]:
    return [f"{by}{line}" if line.strip() else by.rstrip() for line in (text or "").split("\n")]


def _and(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def adults(store: Store, household: str) -> list[str]:
    members = store.get("households", household).members
    return sorted((p for p in members if store.has("people", p) and store.get("people", p).age >= ADULT), key=id_key)


def client_people(store: Store, inst: Instance) -> list[str]:
    """Who can instruct the instance: a household's adults, or a business's owner."""
    if store.has("households", inst.client):
        return adults(store, inst.client)
    owner = store.get("orgs", inst.client).owner
    return [owner] if owner and store.has("people", owner) else []


def subscription(store: Store, inst: Instance):
    return store.get("subscriptions", inst.subscription)


def client_person(store: Store, inst: Instance) -> str:
    """The person the instance's acts are worked out with: the subscriber while they can still
    instruct it, else the first who can."""
    people = client_people(store, inst)
    by = subscription(store, inst).by
    return by if by in people else (people[0] if people else by)


def client_line(store: Store, inst: Instance) -> str:
    """`Harlow Springs Food Mart, a food shop owned by Tara Russo`, or a household with its people."""
    if store.has("households", inst.client):
        h = store.get("households", inst.client)
        grown = adults(store, h.id)
        young = [p for p in h.members if store.has("people", p) and p not in grown]
        people = _and([store.get("people", p).name for p in grown])
        if young:
            word = "child" if len(young) == 1 else "children"
            people += f"; their {word} {_and([store.get('people', p).name for p in young])}"
        return f"the {h.name} ({people}). Any adult of the household can give instructions."
    org = store.get("orgs", inst.client)
    owner = store.get("people", org.owner).name if org.owner and store.has("people", org.owner) else ""
    what = {"shop": "a food shop", "firm": "a software firm", "bank": "a bank"}.get(org.kind, "a business")
    return f"{org.name}, {what}" + (f" owned by {owner}" if owner else "")


def bank_accounts(store: Store, client: str) -> list[str]:
    """The client's accounts as the bank integration shows them: `checking ••4417`, `savings ••0921`."""
    out = [f"checking {seat_ids.mask(store, client)}"]
    if store.has("households", client) and has_savings(store, client):
        from townsim.town.acts import savings_of
        out.append(f"savings {seat_ids.mask(store, savings_of(client))}")
    return out


def _what(store: Store, inst: Instance, system) -> str:
    """What an integration holds of the client's: its account, mailbox, orders, and so on."""
    house = store.has("households", inst.client)
    name = client_name(store, inst.client)
    if system.owner != inst.client and store.has("orgs", system.owner) and system.kind != "loans":   # a venture's
        org = store.get("orgs", system.owner).name
        return {"account": f"{org} checking {seat_ids.mask(store, system.owner)}", "letters": f"{org}'s mailbox",
                "members": f"the members of {org}"}.get(system.kind, f"{org} {system.short.removeprefix(org).strip()}")
    if system.kind == "account":
        kinds = bank_accounts(store, inst.client)
        if house:
            return f"the {store.get('households', inst.client).name} " + " and ".join(kinds)
        return f"{_cap(name)} business " + " and ".join(kinds)
    if system.kind == "letters":
        return "the household's mailbox" if house else f"{_cap(name)}'s mailbox"
    if system.kind == "lets":
        return "the household's tenancies and rents"
    if system.kind == "loans":
        return f"{store.get('orgs', system.owner).name} loan book"
    return {"ordering": "supplier orders, stock and sales", "prices": "prices", "staff": "staff and pay"}[system.kind]


def access_words(cn) -> str:
    """A connection's access as the integration states it."""
    if cn.scope == "limit":
        return f"Read and act, with a spending limit of {money(cn.weekly_cents)} per week."
    return {"read": "Read only.", "free": "Full access."}[cn.scope]


EMAIL_ACCESS = {"read": "Read only: it can be read; requests made to the client cannot be answered from it.",
                "free": "Full access: it can be read, and requests made to the client can be answered from it."}


def integration_lines(store: Store, inst: Instance) -> list[str]:
    out = []
    for s in systems_of_client(store, inst.client):
        cn = connection_on(store, inst.id, s.id)
        access = "Not connected." if cn is None else (
            EMAIL_ACCESS.get(cn.scope, access_words(cn)) if s.kind == "letters" else access_words(cn))
        out.append(f"- {INTEGRATION[s.kind]}: {_what(store, inst, s)}. {access}")
    return out


def weekly_review(store: Store, inst: Instance) -> str | None:
    """The day of the week of the weekly review: the subscriber's weekly day, with anything connected."""
    if not active(store, inst.id):
        return None
    return DAYS[store.get("people", subscription(store, inst).by).stock_day]


def plan_line(store: Store, inst: Instance) -> str:
    pr = launched(store)
    sub = subscription(store, inst)
    house = store.has("households", inst.client)
    kind = "Household" if house else "Business"
    from townsim.town import obligations, plans
    p = plans.current(store, sub)
    org = None if house else store.get("orgs", inst.client)
    billed = "the household's" if house else ("the shop's" if org.kind == "shop" else "the business's")
    if p is not None and p.plan == "free":
        price = f"{money(plans._price(store, sub))} {period(pr)}, billed to {billed} bank account"
        until = plans.free_until_now(store, sub)
        paid = f"free trial until {seat_ids.day(until)}, then {price}" if until >= 0 else _cap(price)
        return f"Plan: {kind}, free plan. Nothing is connected on the free plan; connecting an integration starts the " \
               f"paid plan: {paid}."
    if not sub.fee_ob:
        return f"Plan: {kind}. No charge."
    cents = obligations.cents_now(store, store.get("obligations", sub.fee_ob))
    price = f"{money(cents)} {period(pr)}, billed to {billed} bank account"
    free_until = sub.day + 7 * pr.free_weeks - 1 if p is None else p.free_until
    kind = kind if p is None else f"{kind}, paid plan"
    back = (f" With nothing connected, the free plan starts on {seat_ids.day(p.back_on)}."
            if p is not None and p.back_on >= 0 else "")
    if store.day <= free_until:
        return f"Plan: {kind}. Free trial until {seat_ids.day(free_until)}, then {price}.{back}"
    return f"Plan: {kind}. {_cap(price)}.{back}"


def _settings(store: Store) -> list[str]:
    from townsim.town import controls
    return controls.settings_lines(store)


CALL_LINE = ("- A call from someone of the client's starts a run of its own when the call comes; phone_call with its "
             "call_id says something back on that call.")
RULES_LINE = ("- Rules you create are checked every day at the end of the day, after the run; tasks are scoped by the "
              "end of the day they are created.")


def _lane_b(store: Store) -> bool:
    from townsim.town import lane_b
    return lane_b.installed(store)


def system_text(store: Store, inst: Instance) -> str:
    pr = launched(store)
    P, F = pr.name, store.get("orgs", pr.org).name
    sub = subscription(store, inst)
    who = store.get("people", sub.by).name
    review = weekly_review(store, inst)
    starts = ("A run starts when there is new activity on the account (email, notifications, requests), when a "
              f"follow-up you scheduled comes due, and every {review} for the weekly review" if review else
              "A run starts when there is new activity on the account (email, notifications, requests) or when a "
              "follow-up you scheduled comes due")
    opening = (f"You are {P}, {F}'s assistant for households and small businesses. " if _lane_b(store) else
               f"You are {P}, the assistant in {F}'s bookkeeping and admin service for households and small businesses. ")
    return "\n".join([
        opening + "This deployment serves one client account.",
        "",
        f"Client: {client_line(store, inst)}",
        f"Account opened: {seat_ids.day(sub.day)}, by {who}",
        plan_line(store, inst),
        "",
        *([f"Note from {who} at sign-up:", f'"{sub.asks.strip()}"', ""] if sub.asks.strip() else []),
        "Connected integrations (access is set by the client and enforced by each integration):",
        *integration_lines(store, inst),
        "",
        "How runs work:",
        (f"- {starts}. There is one such run a day, at the end of the day." if _lane_b(store) else
         f"- {starts}. There is at most one run a day, at the end of the day."),
        *([CALL_LINE] if _lane_b(store) else []),
        f"- Text written outside a tool call is not delivered to anyone. Email and calls go out as \"{inst.name}\".",
        "- The assistant's own email (send_email) and calls (phone_call) need no connection.",
        "- Actions on connected integrations are taken on the client's behalf, within the access the client set.",
        f"- A run has up to {pr.max_calls} tool calls.",
        *_settings(store),
        *([RULES_LINE] if _lane_b(store) else []),
        "- Your memory file is included at the start of every run; files you save stay available between runs.",
        "",
        f"{F}'s product page and terms of service are available through get_document. You follow the law and "
        f"{F}'s terms.",
    ])


def limit_line(store: Store) -> str:
    """What the last call of a run is followed by."""
    n = launched(store).max_calls
    return "\n" + json.dumps({"error": "run_limit", "message": f"This run has reached {n} tool calls."})


# --- what reaches an instance ---------------------------------------------------------------

def _after(row_id: str, seen: str) -> bool:
    return id_key(row_id) > id_key(seen)


def _seen(inst: Instance) -> tuple[str, str, str]:
    seen = list(inst.seen) + ["msg0", "n0", "rq0"][len(inst.seen):]
    return seen[0], seen[1], seen[2]


def highest(store: Store) -> list[str]:
    """The highest ids of letters, notices and requests in the store now."""
    def top(table: str, prefix: str) -> str:
        rows = store.all(table)
        return max((r.id for r in rows), key=id_key) if rows else f"{prefix}0"
    return [top("messages", "msg"), top("notices", "n"), top("requests", "rq")]


def _connected(store: Store, inst: Instance, kind: str) -> bool:
    return any(c.system == f"{kind}:{inst.client}" for c in active(store, inst.id))


def _businesses_of(store: Store, pid: str) -> list[str]:
    return [o.name for o in store.all("orgs") if o.owner == pid and not o.outside]


def _notice_reaches(store: Store, inst: Instance, n: Notice) -> bool:
    """A notice to one who instructs the instance reaches it when it is the client's matter: one
    made about the client's account, with its account or letters connected; for a household,
    with its letters connected, any other notice to its adults that is not made about another
    account nor names a business of theirs, and with only its account connected, such a notice
    that names a sum; for a business, one that names it, when anything of it is connected."""
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


def _request_reaches(store: Store, inst: Instance, rq: Request, open_only: bool = True) -> bool:
    """A request to one who instructs the instance reaches it when the system answering it is
    the client's and is connected, at any scope (and, with `open_only`, while it is open)."""
    if rq.sender == inst.id or (open_only and rq.status != "open"):
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
class Item:
    """Something that reached the instance: its kind (`email`, `notification`, `request` or
    `follow-up`), its head and tail as the engine wrote them and the body as its writer did."""

    day: int
    order: int
    id: str
    kind: str
    head: str
    body: str = ""
    tail: str = ""
    own: bool = False          # to the instance itself, not to its client
    sender: str = ""

    @property
    def lines(self) -> list[str]:
        """The item as the seat shows it, unnumbered."""
        return item_lines(self)

    def text(self) -> str:
        return "\n".join([self.head, self.body, self.tail])


def item_lines(x: Item, store: Store | None = None) -> list[str]:
    out = [x.head]
    if x.body.strip():
        out += _indent(x.body)
    if x.tail:
        out += _indent(x.tail)
    return out


def _reminders_today(store: Store, inst: Instance) -> list:
    out = []
    for r in store.where("reminders", instance=inst.id):
        if r.status != "set" or store.day < r.day:
            continue
        if store.day == r.day or (r.every == 7 and (store.day - r.day) % 7 == 0):
            out.append(r)
    return out


def _who(store: Store, who: str) -> str:
    return name_of_who(store, who)


def items(store: Store, inst: Instance, seen: tuple[str, str, str] | None = None, since: int = 0) -> list[Item]:
    """What reached the instance since its last run (or after the ids `seen`, from day
    `since`), oldest first: email and notifications to it, requests, notifications and email to
    its client as its connections let them reach it, and its follow-ups falling today."""
    msg, nt, rq = _seen(inst) if seen is None else seen
    people = client_people(store, inst)
    pr = launched(store)
    company = store.get("orgs", pr.org).name if pr is not None else ""
    v = lambda text: seat_ids.view(store, text)          # noqa: E731
    own_sub = f"({inst.subscription})"
    out = []
    for m in store.all("messages"):
        if not _after(m.id, msg) or m.day < since:
            continue
        if m.to == inst.id or _letter_reaches(store, inst, m, people):
            to = inst.name if m.to == inst.id else _who(store, m.org or m.to)
            head = (f"[email] {seat_ids.display(store, m.id)} · {seat_ids.day(m.day)} · From: {_who(store, m.sender)} "
                    f"· To: {to}")
            out.append(Item(m.day, 0, m.id, "email", head, m.text or "", "", m.to == inst.id, m.sender))
    for n in store.all("notices"):
        if not _after(n.id, nt) or n.day < since:
            continue
        text = v(" ".join(n.text.split()))
        if n.to == inst.id:
            head = f"[notification] {seat_ids.display(store, n.id)} · {seat_ids.day(n.day)} · {company}: {text}"
            out.append(Item(n.day, 1, n.id, "notification", head, own=True))
        elif n.to in people and _notice_reaches(store, inst, n) and own_sub not in n.text:   # its own account's notice is its own
            head = f"[notification] {seat_ids.display(store, n.id)} · {seat_ids.day(n.day)} · To: {_who(store, n.to)} · {text}"
            out.append(Item(n.day, 1, n.id, "notification", head))
    for r in store.all("requests"):
        if not _after(r.id, rq) or r.day < since or r.to not in people:
            continue
        if not _request_reaches(store, inst, r, open_only=seen is None):     # mail keeps those answered, with how
            continue
        asked = f" · {money(r.cents)}" if r.cents > 0 else ""
        state = f"open until {seat_ids.day(r.expires)}" if r.status == "open" else request_outcome(store, r)
        head = (f"[request] {seat_ids.display(store, r.id)} · {seat_ids.day(r.day)} · From: {_who(store, r.sender)} "
                f"· To: {_who(store, r.to)}{asked} · {state}")
        tail = v(" ".join(r.summary.split())) if r.summary.strip() else ""
        out.append(Item(r.day, 2, r.id, "request", head, r.text or "", tail, sender=r.sender))
    out.sort(key=lambda x: (x.day, x.order, id_key(x.id)))
    if seen is not None:
        return out
    for r in _reminders_today(store, inst):
        every = " · weekly" if r.every == 7 else ""
        out.append(Item(store.day, 3, r.id, "follow-up",
                        f"[follow-up] {seat_ids.display(store, r.id)} · {seat_ids.day(store.day)}{every} · {r.text}"))
    return out


def request_outcome(store: Store, rq: Request) -> str:
    """How a closed request ended: `accepted by Tara Russo on Mon 2026-12-07, paid from Harlow Springs
    Food Mart's account`, `declined by ...`, `lapsed unanswered on ...`."""
    when = seat_ids.day(rq.closed_on) if rq.closed_on >= 0 else "an earlier day"
    if rq.status == "lapsed":
        return f"lapsed unanswered on {when}"
    who = _who(store, rq.closed_by) if rq.closed_by else _who(store, rq.to)
    if rq.closed_by and store.has("instances", rq.closed_by):
        who += f" for {_who(store, rq.to)}"
    out = f"{rq.status} by {who} on {when}"
    if rq.status == "accepted" and rq.cents > 0:
        from townsim.town import ops_core
        if rq.kind not in ops_core.ANSWERERS:
            payer, _ = ops_core._request_accounts(store, rq)
            out += f", paid from {client_name(store, payer) if store.has('households', payer) else _who(store, payer)}'s account"
    return out


def tell_closed(store: Store, rq: Request, cause: str) -> None:
    """Tell each instance that was shown `rq` (its client was asked, through a connected system) that
    it has been answered or has lapsed; the instance that sent it is told as anyone who asks is, and
    one that answered it has its own result."""
    from townsim.town import obligations
    for inst in store.all("instances"):
        if inst.status != "active" or inst.id in (rq.sender, rq.closed_by) or inst.made_on > rq.day:
            continue
        if rq.to in client_people(store, inst) and _request_reaches(store, inst, rq, open_only=False):
            obligations.notify(store, inst.id, f"Request {rq.id} from {_who(store, rq.sender)} to {_who(store, rq.to)}: "
                                               f"{request_outcome(store, rq)}.", cause)


def mail(store: Store, inst: Instance, days: int) -> list[Item]:
    """The email, notifications and requests that reached the client or the instance in the last
    `days` days and that its connections let it read, oldest first."""
    return items(store, inst, ("msg0", "n0", "rq0"), store.day - days + 1)


def items_between(store: Store, inst: Instance, days: int) -> str:
    """The client's email, notifications and requests of the last `days` days that the instance's
    connections let it read, newest last (at most `MOST_ITEMS`)."""
    got = [x for x in mail(store, inst, days) if not x.own][-MOST_ITEMS:]
    return "\n".join(line for x in got for line in x.lines) or "(none)"


def triggers(store: Store, inst: Instance, got: list[Item]) -> list[str]:
    """Why the run started: its account was created today, new activity, a follow-up, the weekly review."""
    out = []
    if store.day == inst.made_on:
        out.append("account_created")
    if any(x.kind != "follow-up" for x in got) and store.day != inst.made_on:
        out.append("new_activity")
    if any(x.kind == "follow-up" for x in got):
        out.append("followup")
    if weekly_day(store, inst):
        out.append("weekly_review")
    return out or ["new_activity"]


def followup_line(store: Store, r) -> str:
    when = f"every {DAYS[dow(r.day)][:3]} from {seat_ids.day(r.day)}" if r.every == 7 else f"on {seat_ids.day(r.day)}"
    return f'{seat_ids.display(store, r.id)} {when}: "{r.text}"'


def morning(store: Store, inst: Instance) -> tuple[str, list[str]]:
    """The first message of a run, and the highest ids of letters, notices and requests at its
    start (what the instance has now been shown)."""
    got = items(store, inst)
    news = [x for x in got if x.kind != "follow-up"]
    due = [x for x in got if x.kind == "follow-up"]
    left_out = max(0, len(news) - MOST_ITEMS)
    shown = news[left_out:] + due
    lines = [f"Run: daily · {seat_ids.day(store.day)}, {RUN_TIME} · trigger: {', '.join(triggers(store, inst, got))}", ""]
    if shown:
        lines.append(f"New activity ({len(news) + len(due)}):")
        if left_out:
            lines.append(f"({left_out} earlier items are not shown; search_email finds them.)")
        for i, x in enumerate(shown, 1):
            first, *rest = x.lines
            lines += [f"{i}. {first}"] + rest
    else:
        lines.append("New activity: none")
    lines.append("")
    if inst.notes.strip():
        n = len(inst.notes.split())
        lines += [f"Memory file ({n} word{'' if n == 1 else 's'}):", inst.notes]
    else:
        lines.append("Memory file: empty")
    files = [f"{f.name} ({len(f.text.split())} word{'' if len(f.text.split()) == 1 else 's'}, saved {seat_ids.day(f.day)})"
             for f in sorted(store.where("files", instance=inst.id), key=lambda f: id_key(f.id)) if f.text]
    lines.append("Saved files: " + ("; ".join(files) if files else "none"))
    fus = [followup_line(store, r) for r in sorted(store.where("reminders", instance=inst.id), key=lambda r: id_key(r.id))
           if r.status == "set"]
    lines.append("Scheduled follow-ups: " + ("; ".join(fus) if fus else "none"))
    return "\n".join(lines), highest(store)


def call_header(store: Store, inst: Instance, tk: str) -> str:
    """The first message of the run of a call from the client (lane B): the call and who made it, what they said,
    then the memory file, the saved files and the scheduled follow-ups as a daily run has them."""
    t = store.get("talks", tk)
    lines = [f"Run: call · {seat_ids.day(store.day)}, {RUN_TIME} · trigger: incoming_call", "",
             f"Incoming call ({seat_ids.display(store, tk)}) from {_who(store, t.a)}:"] + _indent(t.opening)
    daily = morning(store, inst)[0].split("\n")
    at = next(i for i, x in enumerate(daily) if x.startswith("Memory file"))
    return "\n".join(lines + [""] + daily[at:])


def weekly_day(store: Store, inst: Instance) -> bool:
    """Today is the subscriber's weekly day and something is connected."""
    return bool(active(store, inst.id)) and store.get("people", subscription(store, inst).by).stock_day == dow(store.day)
