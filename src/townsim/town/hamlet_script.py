"""A scripted life for any generated place, standing in for a model: used by tests and the
`town-hamlet` command only, never by the engine."""

from __future__ import annotations

import json
import random
import re

from townsim.town.replies import ReplyKey

_MONEY = r"-?\$[\d,]+\.\d\d"
_NAME = re.compile(r"^You are (.+?), \d+\.", re.M)
_AT = re.compile(r"\b(?:at|runs) ((?:[A-Z][A-Za-z']*)(?: [A-Z][A-Za-z']*)*)")
_ARREARS = re.compile(rf"^  overdue since [^$]+?  ({_MONEY})  (.+?)  (.+) \((ob\d+)\)$")
_COMING = re.compile(rf"^  [A-Z][a-z]+day \d+ [A-Z][a-z]+  ({_MONEY})  (.+?)  (.+) \((ob\d+)\)$")
_REQUEST = re.compile(r"^(rq\d+) from (.+):$")
_ASKED = re.compile(rf"^They ask you for ({_MONEY})\.$")
_OWED_TO_ME = re.compile(r"^(ob\d+): (.+?), (\$[\d,]+\.\d\d) a week from ")
_STANDING_NOTE = "towards the week"
_NO_ONE = "Edith Marsh"
_FULL = r"[A-Z][\w'-]+ [A-Z][\w'-]+"
_IDLE = re.compile(rf"^({_FULL}), [^.]*?(?:is out of work|has no work at present)\.$", re.M)
_WORKS = re.compile(rf"^({_FULL}), [^,\n]+, works ", re.M)
_MY_PAY = re.compile(rf"^You work [^\n]*?, for ({_MONEY}) a week", re.M)
_HOURS_LEFT = re.compile(r"^You have ([\d.]+) hours left this week\.$", re.M)
_PRICE = re.compile(rf"^Price: ({_MONEY}) a basket$")
_ORDER = re.compile(r"(\d+) units from (.+)$")
_STAFF = re.compile(rf"^  ({_FULL}), [\d.]+ hours, ({_MONEY}) a week$")
_OFFERED = re.compile(rf" offers .+ a job: ({_MONEY}) a week")
_EXTRA = re.compile(r" offers .+ ([\d.]+) extra hours, paid ")
_LOAN_ASKED = re.compile(rf" asks the bank for ({_MONEY}) over ")
_END_ASKED = re.compile(r" asks to end .+ \(ob\d+\): no payment after ")
_AGREEMENT = re.compile(rf" pays .+ {_MONEY} (once on|every week from) ")
_GARDEN = "help with the garden"
_BOILER = "a new boiler"
_OWED_LINE = re.compile(rf"^(ob\d+): (.+?), ({_MONEY}) a week (to|from) ")


def _cents(text: str) -> int:
    sign = -1 if text.startswith("-") else 1
    return sign * int(text.lstrip("-").removeprefix("$").replace(",", "").replace(".", ""))


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


def _sections(user: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for block in user.split("\n\n"):
        lines = block.split("\n")
        if lines and lines[0].isupper():
            out[lines[0]] = lines[1:]
    return out


class _Seen:
    """What a scripted person reads off their view."""

    def __init__(self, messages: list[dict]) -> None:
        system = messages[0].get("content", "") if messages else ""
        user = messages[-1].get("content", "") if messages else ""
        found = _NAME.search(system)
        self.name = found.group(1) if found else ""
        self.sections = _sections(user)
        account = self.sections.get("YOUR HOUSEHOLD'S ACCOUNT", [])
        self.balance = next((_cents(m.group(1)) for line in account
                             if (m := re.match(rf"Balance: ({_MONEY})", line))), 0)
        self.after = next((_cents(m.group(1)) for line in account
                           if (m := re.match(rf"After what goes out: ({_MONEY})", line))), 0)
        out = []                     # the lines under "Going out by"
        for line in account:
            if line.startswith("Going out by"):
                out = [line]
            elif line.startswith("Expected in by"):
                break
            elif out:
                out.append(line)
        self.arrears = [(_cents(m.group(1)), m.group(2), m.group(3), m.group(4))
                        for line in out if (m := _ARREARS.match(line))]
        self.coming = [(_cents(m.group(1)), m.group(2), m.group(3), m.group(4))
                       for line in out if (m := _COMING.match(line))]
        self.people = [line.split(", ", 1) for line in self.sections.get("PEOPLE YOU KNOW", []) if ", " in line]
        self.requests = []           # (id, from, cents asked): each request's head, then what it asks for
        self.summaries: dict[str, str] = {}      # request id -> the offer code wrote of it, if any
        for line in self.sections.get("WAITING FOR YOUR ANSWER", []):
            if m := _REQUEST.match(line):
                self.requests.append((m.group(1), m.group(2), 0))
            elif (m := _ASKED.match(line)) and self.requests:
                self.requests[-1] = self.requests[-1][:2] + (_cents(m.group(1)),)
            elif self.requests and not line.startswith(("  ", "(")):
                self.summaries[self.requests[-1][0]] = line
        self.owed = self.sections.get("WHAT IS OWED", [])
        business = self.sections.get("YOUR BUSINESS", [])
        self.business = business[0] if business else ""
        self.price = next((_cents(m.group(1)) for line in business if (m := _PRICE.match(line))), None)
        self.order = next(((int(m.group(1)), m.group(2)) for line in business
                           if line.startswith(("On order", "Nothing ordered")) and (m := _ORDER.search(line))), None)
        self.staff = [(m.group(1), _cents(m.group(2))) for line in business if (m := _STAFF.match(line))]
        self.idle = _IDLE.findall(system)
        self.house_workers = _WORKS.findall(system)
        found = _MY_PAY.search(system)
        self.my_pay = _cents(found.group(1)) if found else None
        found = _HOURS_LEFT.search(user)
        self.hours_left = float(found.group(1)) if found else 0.0
        mine = {self.business, self.name}
        self.places = [p for p in dict.fromkeys(_AT.findall(system)) if p not in mine and " " in p]
        self.minded = "ON YOUR MIND" in self.sections
        self.banker = "THE BANK'S BOOK" in self.sections
        self.notes = "\n".join(self.sections.get("YOUR NOTES", []))

    def known(self, *words: str) -> list[str]:
        """The full names of people known whose line has any of `words`."""
        return [name for name, how in self.people if any(w in how for w in words)]


def _reply(aims: list[str] | None, acts: list) -> str:
    body: dict = {"thoughts": ""}
    if aims is not None:
        body["aims"] = aims
    body["acts"] = acts
    return json.dumps(body)


def _done_mark(day: int) -> str:
    return f"Business seen to in week {day // 7 + 1}."


def _work(me: _Seen, day: int, num: int) -> list[dict]:
    """What an owner does with their business, week by week, and an employee's leaving; with
    a slip now and then that is refused."""
    week, wd = day // 7 + 1, day % 7
    acts: list[dict] = []
    if _done_mark(day) in me.notes:          # this week's business is done
        return acts
    if not me.business:
        if week == 5 and num % 9 == 6 and me.my_pay is not None:
            acts.append({"op": "quit"})
        return acts
    staff = [(name, pay) for name, pay in me.staff if name != me.name]
    friends = [f for f in me.known("friend") if f not in dict(staff)]
    if week == 2:
        if me.idle:
            acts += [{"op": "offer_job", "to": me.idle[0], "weekly_pay": 100, "hours": 20},
                     {"op": "offer_job", "to": me.idle[0], "weekly_pay": 440, "hours": 38,
                      "text": "There is a place on the till if you want it."}]
        if me.price is not None:
            acts.append({"op": "set_price", "price": _dollars(me.price + 20)})
            if num % 3 == 2:
                acts.append({"op": "set_price", "price": 0.5})
        if me.order is not None:
            units, supplier = me.order
            acts.append({"op": "order_stock", "supplier": supplier, "units": units})
            if num % 4 == 0:
                acts.append({"op": "order_stock", "supplier": supplier, "units": 6000})
    elif week == 3:
        if staff:
            acts.append({"op": "offer_shifts", "to": staff[0][0], "hours": 6})
        if friends and num % 2 == 0:
            acts.append({"op": "offer_shifts", "to": friends[0], "hours": 6})
        if num % 7 == 0 and me.house_workers:
            acts.append({"op": "offer_job", "to": me.house_workers[0], "weekly_pay": 253, "hours": 22})
    elif week == 4:
        if staff:
            name, pay = staff[0]
            if num % 2 == 0:
                acts.append({"op": "set_staff_pay", "who": name, "weekly_pay": 50})
            acts.append({"op": "set_staff_pay", "who": name, "weekly_pay": _dollars(pay + 1000)})
            if wd <= 3 and me.balance > 2 * staff[-1][1]:          # this week's wage, by hand, before Friday
                acts.append({"op": "pay", "to": staff[-1][0], "amount": _dollars(staff[-1][1]), "note": "wages"})
    elif week == 5:
        if me.price is not None:
            acts.append({"op": "work_extra_hours", "hours": 40 if num % 2 == 1 else 6})
            if num % 2 == 1:
                acts.append({"op": "work_extra_hours", "hours": 6})
        if num % 8 == 0:
            acts.append({"op": "quit"})
    elif week == 6:
        if num % 3 == 0 and staff:
            acts.append({"op": "dismiss", "who": staff[0][0], "text": "Trade has been slow."})
            if friends:
                acts.append({"op": "dismiss", "who": friends[0]})
        acts.append({"op": "move_money", "from": me.business, "to": "household", "amount": 100})
        if num % 2 == 0:
            acts.append({"op": "move_money", "from": "household", "to": "household", "amount": 100})
    elif week == 7 and me.balance > 30_000:
        acts.append({"op": "move_money", "from": "household", "to": me.business, "amount": 100})
    elif week == 8 and num % 7 == 0 and me.order is not None:
        acts.append({"op": "order_stock", "supplier": me.order[1], "units": 0})
    return acts


def _deals_mark(day: int) -> str:
    return f"Dealings seen to in week {day // 7 + 1}."


def _owed(me: _Seen, memo: str, way: str) -> list[tuple[str, int]]:
    """(id, weekly cents) of what is owed whose memo is `memo` and that goes `way` (`to` or `from`)."""
    return [(m.group(1), _cents(m.group(3))) for line in me.owed
            if (m := _OWED_LINE.match(line)) and m.group(2).endswith(f'"{memo}"') and m.group(4) == way]


def _deals(me: _Seen, day: int, num: int) -> list[dict]:
    """Agreements, loans and rent, week by week: an agreement proposed and later ended by
    agreement, one turned down; a loan asked for and granted, one turned down, and part of it
    repaid early; a landlord's change of rent and notice; with a slip in each that is refused."""
    week = day // 7 + 1
    acts: list[dict] = []
    if _deals_mark(day) in me.notes:
        return acts
    friends = me.known("friend")
    tenants = me.known("your tenant")
    if week == 2 and num % 6 == 2 and friends:
        acts += [{"op": "propose_agreement", "to": friends[0], "amount": 10, "per": "week", "weeks": 6,
                  "pays": "me", "what": _GARDEN},
                 {"op": "propose_agreement", "to": friends[-1], "amount": 25, "per": "once", "start": "Saturday",
                  "pays": "them", "what": "a share of the coal"},
                 {"op": "propose_agreement", "to": friends[0], "amount": 10, "per": "week", "weeks": 2,
                  "start": "Monday 2 March", "pays": "me", "what": _GARDEN}]
    if week == 2 and tenants:
        acts += [{"op": "set_rent", "tenant": tenants[0], "weekly": 340},
                 {"op": "set_rent", "tenant": tenants[0], "weekly": 5}]
    if week == 3 and num % 6 == 4:
        if me.business:
            acts.append({"op": "borrow", "amount": 9000, "weeks": 52, "purpose": "a second van",
                         "from": me.business})
        else:
            acts.append({"op": "borrow", "amount": 1500, "weeks": 26, "purpose": _BOILER})
        acts.append({"op": "borrow", "amount": 50000, "weeks": 26, "purpose": _BOILER})
    if week >= 3 and tenants:                 # notice on one tenant a week, in turn
        acts.append({"op": "serve_notice", "tenant": tenants[week % len(tenants)]})
    if week == 5 and num % 6 == 2:
        for ob, _ in _owed(me, _GARDEN, "to")[:1]:
            acts += [{"op": "propose_end", "id": ob}, {"op": "propose_end", "id": "ob1"}]
    if week == 5 and num % 6 == 4:
        for ob, weekly in _owed(me, _BOILER, "to")[:1]:
            if me.balance > 4 * weekly:
                acts += [{"op": "repay", "id": ob, "amount": _dollars(3 * weekly + 500)},
                         {"op": "repay", "id": ob, "amount": 99999}]
    return acts


def script(key: ReplyKey, messages: list[dict]) -> str:
    """A scripted life for anyone in a generated place: what they do is decided from the day,
    who they are and the text of their view alone, with `Random(f"script:<day>:<actor>")`
    for variety. They answer what is asked of them, pay toward what is overdue, give and ask
    for help, write to those they know, keep notes and aims, and now and then make a slip."""
    rng = random.Random(f"script:{key.day}:{key.actor}")
    me = _Seen(messages)
    num = int(re.sub(r"\D", "", key.actor) or 0)
    day = key.day
    first = me.name.split()[0] if me.name else ""
    if 15 <= day < 22 and num % 9 == 0:
        return "I'll leave it all for today and see how things look tomorrow."
    if 29 <= day < 36 and num % 8 == 5:
        return _reply(None, [{"op": "write_notes", "text": f"Things to do, list {i}"} for i in range(1, 13)])
    acts: list = []
    close = me.known("partner", "child", "parent", "brother", "sister", "family", "friend")
    friends = me.known("friend")
    for rq, sender, cents in me.requests:
        summary = me.summaries.get(rq, "")
        offered, extra = _OFFERED.search(summary), _EXTRA.search(summary)
        loan, agreement = _LOAN_ASKED.search(summary), _AGREEMENT.search(summary)
        if offered:            # a job: taken if it pays more than the one they have
            yes = me.my_pay is None or _cents(offered.group(1)) > me.my_pay
        elif extra:            # extra hours: taken if the week has room for them
            yes = me.hours_left >= float(extra.group(1)) + 1
        elif loan:             # the banker lends up to $5,000
            yes = _cents(loan.group(1)) <= 500_000
        elif _END_ASKED.search(summary):
            yes = True
        elif agreement:        # an agreement: a weekly one is taken, a single payment is not
            yes = agreement.group(1) != "once on"
        else:
            yes = rng.random() < 0.8 and (cents == 0 or cents * 3 <= me.balance)
        acts.append({"op": "answer", "request": rq, "answer": "accept" if yes else "decline",
                     "note": "Glad to help." if yes else "Not this time, sorry."})
    if me.arrears and me.balance >= 500:
        cents, payee, memo, ob = me.arrears[0]
        acts.append({"op": "pay", "to": payee, "amount": _dollars(min(cents, me.balance // 2)), "for": ob,
                     "note": f"towards the {memo}"})
    spending = [c for c in me.coming if c[2] == "regular spending"]
    if 7 <= day < 14 and spending and me.balance > 100_000 and rng.random() < 0.3:
        acts.append({"op": "pay", "to": spending[0][1], "amount": _dollars(me.balance * 85 // 100),
                     "note": "a new three-piece suite"})
    mine = [line.split(":")[0] for line in me.owed if _STANDING_NOTE in line and " to " in line]
    if mine and day >= 35 and rng.random() < 0.5:
        acts.append({"op": "stop", "id": mine[0]})
    elif not mine and 7 <= day < 28 and close and me.after > 20_000 and rng.random() < 0.2:
        acts.append({"op": "standing_payment", "to": rng.choice(close), "amount": 15, "day": "Friday",
                     "weeks": 0, "note": _STANDING_NOTE})
    roll = rng.random()
    if roll < 0.08 and friends:
        acts.append({"op": "message", "to": rng.choice(friends).split()[0],
                     "text": f"Are you about on Saturday? It has been a while. {first}"})
    elif roll < 0.16 and close:
        acts.append({"op": "message", "to": rng.choice(close), "text": "How are things with you this week?"})
    elif roll < 0.21 and me.places:
        acts.append({"op": "message", "to": rng.choice(me.places), "text": "Will you have eggs in on Thursday?"})
    elif roll < 0.24:
        acts.append({"op": "message", "to": _NO_ONE, "text": "Is there a plot free at the allotments?"})
    roll = rng.random()
    if roll < 0.08 and close:
        acts.append({"op": "request", "to": rng.choice(close), "text": "Could you give me a hand on Saturday morning?",
                     "amount": 0})
    elif roll < 0.25 and friends and (me.after < 0 or me.balance < 30_000):
        acts.append({"op": "request", "to": rng.choice(friends), "text": "Could you lend me $50 until payday?",
                     "amount": 50})
    debtors = me.known("owes you money")
    owed_me = [m for line in me.owed if (m := _OWED_TO_ME.match(line))]
    if debtors and owed_me and rng.random() < 0.15:
        ob, memo, amount = owed_me[0].groups()
        acts.append({"op": "request", "to": debtors[0], "text": f"Could you pay this week's {memo} early?",
                     "amount": _dollars(_cents(amount)), "for": ob})
    staff = me.known("works for you")
    if me.business and staff and rng.random() < 0.12:
        acts.append({"op": "pay", "to": rng.choice(staff), "amount": 20, "note": "a thank-you for the extra effort",
                     "from": me.business})
    work = _work(me, day, num)
    acts += work
    deals = _deals(me, day, num)
    acts += deals
    mark = _done_mark(day) if work or _done_mark(day) in me.notes else ""
    if deals or _deals_mark(day) in me.notes:
        mark = f"{mark} {_deals_mark(day)}".strip()
    roll = rng.random()
    if roll < 0.06:
        acts.append({"op": "read", "what": "account in full"})
    elif roll < 0.10:
        acts.append({"op": "read", "what": "account"})
    notes = None
    if rng.random() < 0.15:
        notes = {"op": "write_notes", "text": f"Day {day}: the account held ${me.balance / 100:,.2f}. {mark}".strip()}
    elif work or deals:
        notes = {"op": "write_notes", "text": mark}
    if notes is not None and deals:           # noted before the dealings, so that it is among the eight done
        at = len(acts) - len(deals) - (1 if roll < 0.10 else 0)
        acts.insert(at, notes)
    elif notes is not None:
        acts.append(notes)
    if 22 <= day < 25 and num % 7 == 3:
        acts += [{"op": "pay", "to": 7, "amount": "lots"}, {"op": "message", "to": ["someone"], "text": None},
                 {"amount": 5}]
    aims = None
    if not me.minded:
        aims = ["Keep up with the bills", f"See {close[0].split()[0]} soon" if close else "Get out more"]
    elif rng.random() < 0.1:
        aims = ["Keep up with all the bills", "Put a little by each week" if me.after > 0 else "Spend less on extras"]
    return _reply(aims, acts)
