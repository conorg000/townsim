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
_REQUEST = re.compile(rf"^(rq\d+) from (.+?): .*?(?:They ask you for ({_MONEY})\.)?$")
_OWED_TO_ME = re.compile(r"^(ob\d+): (.+?), (\$[\d,]+\.\d\d) a week from ")
_STANDING_NOTE = "towards the week"
_NO_ONE = "Edith Marsh"


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
        self.requests = [(m.group(1), m.group(2), _cents(m.group(3)) if m.group(3) else 0)
                         for line in self.sections.get("WAITING FOR YOUR ANSWER", []) if (m := _REQUEST.match(line))]
        self.owed = self.sections.get("WHAT IS OWED", [])
        business = self.sections.get("YOUR BUSINESS", [])
        self.business = business[0] if business else ""
        mine = {self.business, self.name}
        self.places = [p for p in dict.fromkeys(_AT.findall(system)) if p not in mine and " " in p]
        self.minded = "ON YOUR MIND" in self.sections

    def known(self, *words: str) -> list[str]:
        """The full names of people known whose line has any of `words`."""
        return [name for name, how in self.people if any(w in how for w in words)]


def _reply(aims: list[str] | None, acts: list) -> str:
    body: dict = {"thoughts": ""}
    if aims is not None:
        body["aims"] = aims
    body["acts"] = acts
    return json.dumps(body)


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
    roll = rng.random()
    if roll < 0.06:
        acts.append({"op": "read", "what": "account in full"})
    elif roll < 0.10:
        acts.append({"op": "read", "what": "account"})
    if rng.random() < 0.15:
        acts.append({"op": "write_notes", "text": f"Day {day}: the account held ${me.balance / 100:,.2f}."})
    if 22 <= day < 25 and num % 7 == 3:
        acts += [{"op": "pay", "to": 7, "amount": "lots"}, {"op": "message", "to": ["someone"], "text": None},
                 {"amount": 5}]
    aims = None
    if not me.minded:
        aims = ["Keep up with the bills", f"See {close[0].split()[0]} soon" if close else "Get out more"]
    elif rng.random() < 0.1:
        aims = ["Keep up with all the bills", "Put a little by each week" if me.after > 0 else "Spend less on extras"]
    return _reply(aims, acts)
