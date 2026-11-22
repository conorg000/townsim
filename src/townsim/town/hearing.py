"""Hearing things (design 3.4; lane B): what reached each person last week through the channels a village has.

At the end of each day code reads that day's changes and keeps, as `Heard` rows, what the members of a channel
saw for themselves, each with the people it reached:
- one's household: a letter a member had, by whom it was sent (never what it said); a job a member took or left;
  a member's illness; the rent of the house raised or lowered;
- one's workplace (the owner and everyone working there): someone taking a job there or leaving it, extra hours
  offered and by whom, the stock order changed and by whom, the price changed and by whom, someone ill;
- the clubs and services one belongs to: its fee changed, a member joining or leaving;
- the shops one bought at (the shop one's household last bought from): its price changed; and, for those who
  shop there on that day, the day it ran out of baskets or had too few staff to serve everyone;
- public records: a service, club or shop registered, a notice posted on the notice board;
- one's friends (the ties of that kind): of a friend's public changes of circumstance, a job taken or left, a club
  or service joined or left, a subscription to a service taken up or stopped (the product's among them), and for a
  business of theirs its supplier or its price changed; each reaches each friend at `FRIEND_SHARE`, drawn from what
  it is and who they are, and none reaches one who heard it through another channel.
At a workplace, a stock order changed is told again when the books show what came of it: on the Sunday that closes
the week its delivery came, with how the shop's baskets lasted that week.
Things an assistant did are among these as anyone would see them: a rota or an order made by it at one's work, a
letter from it to someone at home, a notice posted through it. Nothing private is kept: no balance, pay, loan
terms or the words of a letter.

Each Monday a person is shown what reached them in the week before (`section`), at most `MAX_LINES` lines in the
past tense; when there is more, the lines are taken from each kind in turn, so that no kind fills the digest.
Without lane B nothing is kept or shown.
"""

from __future__ import annotations

from collections import defaultdict

from townsim.town import acts, jobs
from townsim.town.clock import DAYS, date_text, dow
from townsim.town.entities import Heard
from townsim.town.obligations import money
from townsim.town.store import Store, id_key

MAX_LINES = 8
MOST_OF_A_KIND = 3
HEADING = "WHAT YOU HEARD LAST WEEK"
# the order in which kinds are taken when a week has more than MAX_LINES lines
KINDS = ("letter", "job", "friend", "ill", "rent", "rota", "order", "price", "shelf", "staffing", "club",
         "register", "notice")
# people tell their friends some but not all of their news: each public change of a friend's reaches each friend
# about half the time
FRIEND_SHARE = 0.5


def _name(store: Store, who: str) -> str:
    return acts.name_of_who(store, who)


def _household(store: Store, pid: str) -> list[str]:
    if not store.has("people", pid):
        return []
    return list(store.get("households", store.get("people", pid).household).members)


def _members(store: Store, account: str) -> list[str]:
    """Everyone in a household, or a business's owner."""
    if store.has("households", account):
        return list(store.get("households", account).members)
    if store.has("orgs", account) and store.get("orgs", account).owner:
        return [store.get("orgs", account).owner]
    return []


def _workplace(store: Store, org: str) -> list[str]:
    """The owner of `org` and everyone working there today."""
    got = [j.person for j in jobs.held(store, org=org)]
    if store.has("orgs", org) and store.get("orgs", org).owner:
        got.append(store.get("orgs", org).owner)
    return got


def _customers(store: Store, org: str) -> list[str]:
    """Everyone in the households whose shopping was last done at `org`."""
    return [p for b in store.all("buyers") if b.last_bought == org and store.has("households", b.account)
            for p in store.get("households", b.account).members]


def _shoppers_today(store: Store, org: str) -> list[str]:
    """Everyone in the households that shop on today's day and last bought at `org`: those who found its shelves
    and its tills as they were today."""
    today = dow(store.day)
    return [p for b in store.all("buyers") if b.last_bought == org and b.shop_day == today
            and store.has("households", b.account) for p in store.get("households", b.account).members]


def _venture_people(store: Store, venture) -> list[str]:
    got = [venture.founder]
    for m in store.all("members"):
        if m.venture == venture.id and m.status == "active":
            got += _members(store, m.account)
    return got


def _by(store: Store, cause: str) -> str:
    """`, by Tara Russo`, `, by Tessaly Assistant for Harlow Springs Food Mart`, `, by a rule set by …`; "" for
    the place's own routine."""
    actor = acts.actor_of_cause(cause)
    if not actor:
        return ""
    who = _name(store, actor)
    return f", by a rule set by {who}" if cause.startswith("auto:") else f", by {who}"


def _did(cause: str) -> str:
    """The person who did an act by hand (not by a rule): they know it already."""
    return "" if cause.startswith("auto:") else acts.actor_of_cause(cause)


def _others(people: list[str], *known: str) -> list[str]:
    return [p for p in people if p not in known]


def _friends(store: Store, pid: str) -> list[str]:
    """Those who count `pid` a friend, in id order."""
    return sorted({t.person for t in store.all("ties") if t.other == pid and t.kind == "friend"}, key=id_key)


def _told(day: int, subject: str, text: str, friend: str) -> bool:
    """Whether `friend` is told this piece of `subject`'s news: a fixed share, drawn from what it is and who they are."""
    import hashlib
    h = hashlib.sha256(f"{day}|{subject}|{text}|{friend}".encode()).hexdigest()
    return int(h[:8], 16) / 0x100000000 < FRIEND_SHARE


def _supplier(store: Store, sid: str) -> str:
    return _name(store, store.get("suppliers", sid).org) if sid and store.has("suppliers", sid) else ""


def _order_text(store: Store, f, changes: list) -> str:
    """What one act's `changes` did to a shop's stock order: `the order was cut to 400 baskets a week`, `raised to`,
    `set at`, with ` from Coldwater Bend Food Supply` when the supplier changed; `the order was moved to …`; or `the
    stock order was ended`."""
    if not f.order_units:
        return "the stock order was ended"
    name = _supplier(store, f.order_supplier)
    if _moved(f, changes) and name:
        how = _how(f, changes)
        return f"the order was moved to {name}" + ("" if how == "kept at" else f" and {how} {f.order_units} baskets a week")
    return f"the order was {_how(f, changes)} {f.order_units} baskets a week"


def _moved(f, changes: list) -> bool:
    """Whether one act's changes moved the shop's order to another supplier than the one it had."""
    c = next((c for c in changes if c.field == "order_supplier"), None)
    return c is not None and f.order_supplier != (c.old or f.last_supplier)


def _how(f, changes: list) -> str:
    """`cut to`, `raised to` or `kept at`: the order's baskets against what it was."""
    c = next((c for c in changes if c.field == "order_units"), None)
    before = c.old if c is not None and isinstance(c.old, int) and c.old else f.last_units
    return "cut to" if f.order_units < before else "raised to" if f.order_units > before else "kept at"


def _lasted(till) -> str:
    """How a shop's baskets lasted its week, as its books show it."""
    if till.turned_stock:
        return "the shop ran out of baskets before the week was out"
    if till.stock_left:
        return "the shop had baskets left on Sunday"
    return "the shop sold every basket"


def _shop(store: Store, fid: str) -> str:
    return store.get("firms", fid).org if store.has("firms", fid) else ""


def _events(store: Store, changes: list) -> list[tuple[str, str, str, list[str]]]:
    """(kind, what it is about, text, who it reached) for each thing seen today, in the order it happened."""
    day = DAYS[dow(store.day)]
    out: list[tuple[str, str, str, list[str]]] = []
    seen: set[tuple] = set()

    def add(kind: str, about: str, text: str, to: list[str], once: tuple | None = None) -> None:
        if once is not None:
            if once in seen:
                return
            seen.add(once)
        if to:
            out.append((kind, about, text, to))

    def friends(subject: str, text: str, reached: list[str]) -> None:
        """`subject`'s public news, to the friends it reaches who did not hear it otherwise."""
        if not store.has("people", subject):
            return
        to = [p for p in _friends(store, subject) if p != subject and p not in reached
              and _told(store.day, subject, text, p)]
        add("friend", subject, text, to, once=("friend", subject, text))

    for c in changes:
        if c.table == "messages" and c.field == "+":
            m = c.new
            if not store.has("people", m.to):
                continue
            home = [p for p in _household(store, m.to) if p != m.to]
            if m.sender in home:
                continue
            add("letter", _name(store, m.to), _name(store, m.sender), home)
        elif c.table == "jobs" and c.field == "+":
            j = c.new
            if not store.has("orgs", j.org) or not store.has("people", j.person):
                continue
            start = f", from {date_text(j.since)}" if j.since > store.day else ""
            text = f"{_name(store, j.person)} took a job at {_name(store, j.org)}{start}."
            to = [p for p in _workplace(store, j.org) + _household(store, j.person) if p != j.person]
            add("job", j.org, text, to)
            friends(j.person, text, to)
        elif c.table == "jobs" and c.field == "ended" and c.new is not None and c.new >= 0 and store.has("jobs", c.id):
            j = store.get("jobs", c.id)
            if not store.has("orgs", j.org) or not store.has("people", j.person):
                continue
            op = acts.op_of_cause(c.cause)
            how = "was dismissed from" if op == "dismiss" else "lost their job at" if "job_lost" in c.cause \
                or c.cause.startswith("routine:event") else "left"
            text = f"{_name(store, j.person)} {how} {_name(store, j.org)}."
            to = [p for p in _workplace(store, j.org) + _household(store, j.person) if p != j.person]
            if ("ended", j.id) not in seen:
                friends(j.person, text, to)
            add("job", j.org, text, to, once=("ended", j.id))
        elif c.table == "people" and c.field == "ill_until" and isinstance(c.new, int) and c.new >= store.day \
                and (c.old is None or c.new > c.old):
            pid = c.id
            work = [x for j in jobs.held(store, person=pid) for x in _workplace(store, j.org)]
            add("ill", pid, f"{_name(store, pid)} was ill from {day}, until {date_text(c.new)}.",
                [p for p in _household(store, pid) + work if p != pid], once=("ill", pid))
        elif c.table == "requests" and c.field == "+" and c.new.kind == "shifts" and store.has("orgs", c.new.org):
            rq = c.new
            add("rota", rq.org, f"At {_name(store, rq.org)}, {_name(store, rq.to)} was offered {rq.hours:g} extra hours"
                                f"{_by(store, c.cause)}.", _others(_workplace(store, rq.org), rq.to, _did(c.cause)))
        elif c.table == "firms" and c.field in ("order_units", "order_supplier") and not c.cause.startswith("routine"):
            f = store.get("firms", c.id)
            if ("order", c.cause) in seen:
                continue
            to = _others(_workplace(store, f.org), _did(c.cause))
            mine = [x for x in changes if x.cause == c.cause and x.table == "firms" and x.id == c.id]
            add("order", f.org, f"At {_name(store, f.org)} {_order_text(store, f, mine)}{_by(store, c.cause)}.", to,
                once=("order", c.cause))
            if _moved(f, mine) and f.order_units and store.has("orgs", f.org):
                friends(store.get("orgs", f.org).owner, f"{_name(store, f.org)} changed its supplier to "
                                                        f"{_supplier(store, f.order_supplier)}.", to)
        elif c.table == "firms" and c.field == "price" and isinstance(c.old, int) and isinstance(c.new, int):
            f = store.get("firms", c.id)
            text = f"{_name(store, f.org)}'s price went from {money(c.old)} to {money(c.new)} a basket"
            add("price", f.org, f"{text}{_by(store, c.cause)}.", _others(_workplace(store, f.org), _did(c.cause)))
            customers = [p for p in _customers(store, f.org) if p not in _workplace(store, f.org)]
            add("price", f.org, f"{text}.", customers)
            if store.has("orgs", f.org) and store.get("orgs", f.org).owner:
                friends(store.get("orgs", f.org).owner, f"{text}.", _workplace(store, f.org) + customers)
        elif c.table == "firms" and c.field in ("turned_stock", "turned_capacity") and isinstance(c.new, int) \
                and isinstance(c.old, int) and c.new > c.old and c.cause.startswith("routine"):
            org = _shop(store, c.id)
            to = _shoppers_today(store, org) + _workplace(store, org)
            if c.field == "turned_stock":
                add("shelf", org, f"{_name(store, org)} had no baskets left on {day}.", to, once=("stock", org))
            else:
                add("staffing", org, f"{_name(store, org)} had too few staff on {day} to serve everyone who came "
                                     f"in.", to, once=("staff", org))
        elif c.table == "obligations" and c.field == "next_from" and isinstance(c.new, int) and c.new > store.day:
            ob = store.get("obligations", c.id)
            if ob.kind != "rent" or not store.has("households", ob.payer):
                continue
            h = store.get("households", ob.payer)
            if ob.next_cents == ob.cents:
                continue
            how = "raised" if ob.next_cents > ob.cents else "lowered"
            lord = _members(store, ob.payee) if store.has("households", ob.payee) else []
            who = _name(store, h.landlord) if h.landlord else _name(store, ob.payee)
            add("rent", h.id, f"{who} {how} the rent at {h.street or 'the house'} from {date_text(ob.next_from)}.",
                list(h.members) + lord, once=("rent", ob.id))
        elif c.table == "ventures" and c.field == "fee_cents" and store.has("ventures", c.id):
            v = store.get("ventures", c.id)
            fee = f"{money(c.new)} a week" if c.new else "nothing"
            add("club", v.id, f"{_name(store, v.org)}'s fee for new members went to {fee}.", _venture_people(store, v))
        elif c.table == "members" and c.field == "+" and store.has("ventures", c.new.venture):
            v = store.get("ventures", c.new.venture)
            to = _venture_people(store, v)
            add("club", v.id, f"{_name(store, c.new.account)} joined {_name(store, v.org)}.", to)
            friends(c.new.person, f"{_name(store, c.new.person)} joined {_name(store, v.org)}.", to)
        elif c.table == "members" and c.field == "status" and c.new == "left" and store.has("members", c.id):
            m = store.get("members", c.id)
            if store.has("ventures", m.venture):
                v = store.get("ventures", m.venture)
                to = _venture_people(store, v) + _members(store, m.account)
                add("club", v.id, f"{_name(store, m.account)} left {_name(store, v.org)}.", to)
                friends(m.person, f"{_name(store, m.person)} left {_name(store, v.org)}.", to)
        elif c.table == "subscriptions" and c.field == "+" and store.has("products", c.new.product):
            sub = c.new
            pr = store.get("products", sub.product)
            friends(sub.by, f"{_name(store, sub.by)} took up {pr.name} for {_client(store, sub.client)}.",
                    _members(store, sub.client))
        elif c.table == "subscriptions" and c.field == "status" and c.new == "ended" and store.has("subscriptions", c.id):
            sub = store.get("subscriptions", c.id)
            if store.has("products", sub.product):
                pr = store.get("products", sub.product)
                friends(sub.by, f"{_name(store, sub.by)}'s {pr.name} for {_client(store, sub.client)} was stopped.",
                        _members(store, sub.client))
        elif c.table == "tills" and c.field == "+" and store.has("firms", c.new.firm):
            till, f = c.new, store.get("firms", c.new.firm)
            monday = store.day - dow(store.day)
            placed = [h for h in sorted(store.all("heard"), key=lambda h: (h.day, id_key(h.id)))
                      if h.kind == "order" and h.about == f.org and monday - 7 <= h.day < monday and "; " not in h.text]
            if placed:                                  # the last placed last week is the one delivered this week
                add("order", f.org, f"{placed[-1].text[:-1]}; {_lasted(till)}.", list(placed[-1].to))
        elif c.table == "ventures" and c.field == "+":
            v = c.new
            what = f"a {v.kind}" + (f" ({acts.cut(v.offering, 60)})" if v.offering else "")
            add("register", v.id, f"{_name(store, v.org)}, {what}, was registered by {_name(store, v.founder)}.",
                [p.id for p in store.all("people")])
        elif c.table == "pages" and c.field == "+" and c.new.kind == "published":
            pg = c.new
            signed = pg.text.split("\n", 1)[0]
            add("notice", pg.id, f'A notice was put up on the {store.place} notice board, "{acts.cut(pg.title, 60)}" '
                                 f'({signed[:1].lower()}{signed[1:]}).', [p.id for p in store.all("people")])
    return out


def _client(store: Store, client: str) -> str:
    """`the Carter household`, or a business's name."""
    from townsim.town.connections import client_name
    return client_name(store, client)


def note_day(store: Store, changes: list) -> None:
    """At the end of the day, with lane B: keep what reached whom in the day's `changes` (those since the morning
    began: a run's morning is a snapshot, which keeps no changes, so nothing before it is read)."""
    from townsim.town import lane_b
    if not lane_b.installed(store):
        return
    cause = f"routine:heard:d{store.day}"
    for kind, about, text, to in _events(store, [c for c in changes if c.day == store.day]):
        people = sorted({p for p in to if store.has("people", p)}, key=id_key)
        if people:
            store.add("heard", Heard(store.new_id("hr", cause), store.day, kind, text, people, about), cause)


def _week_rows(store: Store, pid: str) -> list:
    monday = store.day - dow(store.day)
    return [h for h in store.all("heard") if monday - 7 <= h.day < monday and pid in h.to]


def lines(store: Store, pid: str) -> list[str]:
    """What reached `pid` last week, at most `MAX_LINES` lines and `MOST_OF_A_KIND` of any kind: letters to one
    person merged into one line, a stock order or a shop's shelves and staff once each; when there are more, a line
    of each kind in turn."""
    rows = _week_rows(store, pid)
    by_kind: dict[str, list[tuple[int, str]]] = defaultdict(list)
    letters: dict[str, list] = {}
    kept: dict[tuple, tuple[int, str]] = {}
    for h in rows:
        if h.kind == "letter":
            got = letters.setdefault(h.about, [h.day, []])
            if h.text not in got[1]:
                got[1].append(h.text)
        elif h.kind == "order":
            kept[("order", h.about, h.text.rsplit(", by ", 1)[-1])] = (h.day, h.text)   # the week's last, by each
        elif h.kind in ("shelf", "staffing"):
            kept.setdefault((h.kind, h.about), (h.day, h.text))                    # the first day, once
        else:
            by_kind[h.kind].append((h.day, h.text))
    for who, (day, senders) in letters.items():
        many = len(senders) > 1
        by_kind["letter"].append((day, f"{who} had {'letters' if many else 'a letter'} from {_and(senders)}."))
    for (kind, *_), row in kept.items():
        by_kind[kind].append(row)
    for k in by_kind:
        by_kind[k].sort(key=lambda r: r[0])
    chosen: list[tuple[int, str]] = []
    queues = [list(by_kind[k])[:MOST_OF_A_KIND] for k in KINDS if by_kind.get(k)]
    while queues and len(chosen) < MAX_LINES:
        for q in list(queues):
            if len(chosen) >= MAX_LINES:
                break
            chosen.append(q.pop(0))
            if not q:
                queues.remove(q)
    return [text for _, text in sorted(chosen, key=lambda r: r[0])]


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def section(store: Store, pid: str) -> tuple[str, list[str]] | None:
    """The digest as a section of a person's view, from Monday to Sunday of the week after the one it tells."""
    from townsim.town import lane_b
    if not lane_b.installed(store) or not store.has("people", pid):
        return None
    got = lines(store, pid)
    return (HEADING, got) if got else None
