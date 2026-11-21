"""Random play: replies drawn at random, some sensible and some junk, never break the books.

Each seed plays the thin town for six weeks on replies from `random_script`. After every
day the books must balance, no operation may have failed inside the engine, every due
must be paid between nothing and its sum, no account without credit may be below zero,
and every transfer and change must carry a cause. At the end, no due is collected twice
and every paid due received exactly its sum.

Payments are steered toward what the view shows as owed often enough that payments by
hand are counted toward dues in most seeds, and across the seeds together there must be
a part-payment by hand, a late fee and a default, so the checks cannot go quiet.
"""

from __future__ import annotations

import functools
import json
import random
import re
from collections import Counter, defaultdict

import pytest

from townsim.town import firms, gen
from townsim.town.acts import REGISTRY
from townsim.town.loop import run_day
from townsim.town.obligations import SCHEDULED
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.rundb import RunDB
from townsim.town.runs import replay, run
from townsim.town.thin import thin_town

DAYS = 42
SEEDS = range(40)
REPLAYED = range(5)

NAMES = ["Ruth Ashby", "Tom Ashby", "Craig Pryce", "Megan Pryce", "Wenlow Lettings", "Northgrid Energy"]
JUNK_NAMES = ["Edith Marsh", "Shops", "Employers", "Ashby", "Pryce", " megan pryce ", "RUTH ASHBY", "", None, 7,
              ["Ruth Ashby"], {"name": "Tom Ashby"}, "Ruth", "p01", "h2", "org1", "outside:shops"]
# How often each operation is drawn: stopping is rarer, or rent and wages soon end and
# the town goes quiet; paying is commoner, so households run short and fall into arrears.
WEIGHTS = {"pay": 5, "standing_payment": 2, "stop": 0.5, "message": 1, "request": 2, "answer": 3, "read": 1,
           "write_notes": 1, "converse": 0.5, "book_appointment": 0.3}
# Operations past the first eight are for positions no one holds in the thin town; there they
# are not drawn (so its play is as it was), and the hamlet draws them from each view.
RARE = 0
_IDS = re.compile(r"\b(?:ob|rq|ap)\d+\b")


_OWED = re.compile(r"^  .+?  \$[\d,]+\.\d\d  (.+?)  .* \((ob\d+)\)$")


def _ids(messages: list[dict], prefix: str) -> list[str]:
    text = " ".join(m.get("content", "") for m in messages)
    return sorted(set(i for i in _IDS.findall(text) if i.startswith(prefix)))


def _owed(messages: list[dict], names: list[str] = NAMES) -> list[tuple[str, str, str]]:
    """(name, obligation id, amount) for each line under "Going out by" in the view whose
    payee is someone a payment can name."""
    found, going_out = [], False
    for line in messages[-1].get("content", "").split("\n"):
        if line.startswith("Going out by") or line.startswith("Expected in by"):
            going_out = line.startswith("Going out by")
        elif going_out and (m := _OWED.match(line)) and m.group(1) in names:
            found.append((m.group(1), m.group(2), line.split("  ")[2]))
    return found


_ROW = re.compile(r"^  (?:overdue since )?[A-Z][a-z]+day \d+ [A-Z][a-z]+  -?\$[\d,]+\.\d\d  (.+?)  ")
_OP = re.compile(r'^\{"op": "(\w+)"')


def view_names(messages: list[dict]) -> list[str]:
    """The names a generated place's view shows: the people known, the business, and every
    household and organisation money goes to or comes from."""
    lines = messages[-1].get("content", "").split("\n")
    found: list[str] = []
    section = ""
    for i, line in enumerate(lines):
        if line.isupper() and line.strip():
            section = line
        elif section == "PEOPLE YOU KNOW" and ", " in line:
            found.append(line.split(", ")[0])
        elif section == "YOUR BUSINESS" and lines[i - 1] == section:
            found.append(line)
        elif m := _ROW.match(line):
            found.append(m.group(1))
    return list(dict.fromkeys(found))


_SUPPLIER = re.compile(r"\d+ baskets from (.+)$")


def view_suppliers(messages: list[dict]) -> list[str]:
    """The suppliers a view names, in the order it names them."""
    return list(dict.fromkeys(m.group(1) for line in messages[-1].get("content", "").split("\n")
                              if line.startswith(("On order", "Nothing ordered")) and (m := _SUPPLIER.search(line))))


def view_debts(messages: list[dict]) -> list[str]:
    """The people a view shows the reader owing money or owed money by."""
    lines = messages[-1].get("content", "").split("\n")
    found, section = [], ""
    for line in lines:
        if line.isupper() and line.strip():
            section = line
        elif section == "PEOPLE YOU KNOW" and ", " in line and ("owes you money" in line or "you owe them" in line):
            found.append(line.split(", ")[0])
    return found


_STAFF = re.compile(r"^  (.+?), [\d.]+ hours, \$[\d,]+\.\d\d a week$")


def view_business(messages: list[dict]) -> tuple[list[str], list[str]]:
    """The businesses a view shows the reader owning, and the staff it lists for them."""
    lines = messages[-1].get("content", "").split("\n")
    names = [lines[i + 1] for i, line in enumerate(lines[:-1]) if line == "YOUR BUSINESS"]
    staff = [m.group(1) for line in lines if (m := _STAFF.match(line))]
    return names, staff


def view_ops(messages: list[dict]) -> list[str]:
    """The operations the view offers, in order."""
    return [m.group(1) for line in messages[-1].get("content", "").split("\n") if (m := _OP.match(line))]


def random_script(rng: random.Random, from_view: bool = False):
    """A reply function for `ScriptedReplier`: for each turn, zero to ten acts from every
    registered operation (and some that are not), with arguments read from the view
    (names, obligation and request ids) or invented (wrong types, unknown names, huge or
    negative sums, missing keys), and now and then text that is not JSON at all.
    All of its randomness comes from `rng`. With `from_view`, the names and the operations
    it chooses among are read from each view, as in a generated place."""
    seen = {"names": NAMES, "ops": list(REGISTRY), "suppliers": ["Vale Wholesale"], "business": [], "staff": [],
            "debts": []}

    def pick(sensible: list, junk: list, p: float = 0.7):
        return rng.choice(sensible) if sensible and rng.random() < p else rng.choice(junk)

    def amount():
        return pick([rng.randint(1, 300), rng.randint(1, 30000) / 100, rng.randint(200, 700), f"${rng.randint(1, 2000):,}.50",
                     str(rng.randint(1, 99))],
                    [0, -5, -0.01, 0.001, 10 ** 12, 1e300, float("nan"), float("inf"), "lots", "1,00", "",
                     None, True, [20], {"dollars": 5}, "$-3", "12.345", 99999999.99])

    def text():
        return pick(["Can you help?", "Thank you.", "About the rent.", "Shopping list: bread, milk",
                     "Á café\twith a tab\nand a line"],
                    ["", "   ", None, 12, ["words"], "x" * 3001, "word " * 301, "bell\x07\x00here", "‮"])

    def name():
        return pick(seen["names"], JUNK_NAMES)

    def hours(sensible: list, junk: list):
        return pick(sensible, junk + [None, "lots", True, float("nan"), -1, 10 ** 9, [8], "8 hours"])

    def account():
        return pick(["household", " Household ", *seen["business"], *seen["business"]] if rng.random() < 0.8
                    else seen["names"], JUNK_NAMES, 0.8)

    def worker():
        return pick(seen["staff"], [name()], 0.6)

    aps: list[str] = []

    def args(op: str, obs: list[str], rqs: list[str], owed: list[tuple[str, str, str]]) -> dict:
        if op == "pay" and owed and rng.random() < 0.5:        # toward something owed, in full or in part
            to, ob, sum_ = rng.choice(owed)
            whole = int(sum_.removeprefix("$").replace(",", "").split(".")[0])
            a = {"to": to, "amount": rng.choice([sum_, sum_, max(1, whole // 2), rng.randint(1, 40)]), "note": text()}
            if rng.random() < 0.6:
                a["for"] = ob
        elif op == "pay":
            a = {"to": name(), "amount": amount(), "note": text(), "for": pick(obs, ["ob999", "", None, 4, "rq1"], 0.3)}
        elif op == "standing_payment":
            a = {"to": name(), "amount": amount(),
                 "day": pick(["Friday", "mon", " Sunday. ", "Wednesday"], ["Funday", "", None, 3, "Fri day"]),
                 "start": pick(["", "Monday 13 April", "next Friday", "Thursday", "1 May 2026"],
                               ["31 February", "2 March 2025", "yesterday", 7, "Monday 1 January"], 0.6),
                 "weeks": pick([0, 1, 3, 2.0, None], [-1, 2.5, "3", 10 ** 6, True, 521]), "note": text()}
        elif op == "stop":
            a = {"id": pick(obs + aps, ["ob999", "ob0", "", None, 9, "OB1", "rq1", "ap99"])}
        elif op == "converse":
            a = {"with": name(), "opening": text()}
        elif op == "book_appointment":
            a = {"with": name(), "about": text(),
                 "day": pick(["Friday", "Monday 13 April", "1 May", " thursday "],
                             ["2 March", "Monday 1 June", "", None, 3, "tomorrow"], 0.6)}
        elif op in ("message", "request"):
            a = {"to": name(), "text": text()}
            if op == "request":
                a["amount"] = pick([0, None, rng.randint(1, 200), "0.00"], [amount()], 0.5)
        elif op == "answer":
            a = {"request": pick(rqs, ["rq999", "", None, 1, "ob1"]),
                 "answer": pick(["accept", "decline", " ACCEPT ", "Decline"], ["maybe", "", None, True, "yes"]),
                 "note": text()}
        elif op == "offer_job":
            a = {"to": name(), "weekly_pay": pick([amount(), rng.randint(300, 900)], [amount()]),
                 "hours": hours([38, 20, 4, 60, 12.5, "30"], [0, 3.9, 61]), "text": text()}
        elif op == "offer_shifts":
            a = {"to": worker(), "hours": hours([1, 6, 8, 20, "4"], [0, 21, 0.5])}
        elif op == "dismiss":
            a = {"who": worker(), "text": text()}
        elif op == "set_staff_pay":
            a = {"who": worker(), "weekly_pay": pick([rng.randint(450, 900), 0, "0", amount()], [amount()])}
        elif op == "set_price":
            a = {"price": pick([rng.randint(100, 2000) / 100, "10.50", 1, 100, "$9"], [0, 0.99, 100.01, "free", None])}
        elif op == "order_stock":
            a = {"supplier": pick(seen["suppliers"], JUNK_NAMES),
                 "units": hours([0, 50, 300, 5000, "120", 80.0], [-1, 5001, 2.5, "0.5"])}
        elif op == "work_extra_hours":
            a = {"hours": hours([1, 5, 30, "2", 7.5], [0, 31, 0.99])}
        elif op == "move_money":
            a = {"from": account(), "to": account(), "amount": amount()}
        elif op == "quit":
            a = {"from": pick(seen["names"], JUNK_NAMES, 0.3)} if rng.random() < 0.5 else {}
        elif from_view and op == "propose_agreement":
            a = {"to": name(), "amount": amount(), "per": pick(["week", "once", " Week "], ["month", "", None, 7]),
                 "weeks": pick([0, 1, 4, 52, "6"], [-1, 521, 2.5, None, True]),
                 "start": pick(["", "Friday", "Monday 13 April", "1 May"], ["2 March", "31 February", 7, None], 0.6),
                 "pays": pick(["me", "them", " Them "], ["you", "", None, 1]), "what": text()}
            if rng.random() < 0.3:
                a["from"] = account()
        elif from_view and op == "propose_end":
            a = {"id": pick(obs, ["ob999", "", None, 9, "rq1"])}
        elif from_view and op == "borrow":
            a = {"amount": pick([rng.randint(50, 3000), 20000, "1,500", "$400"], [49, 20001, amount()]),
                 "weeks": hours([4, 26, 52, 104, "10"], [3, 105, 2.5]), "purpose": text()}
            if rng.random() < 0.3:
                a["from"] = account()
        elif from_view and op == "repay":
            a = {"id": pick(obs, ["ob999", "", None, 9, "rq1"]), "amount": amount()}
            if rng.random() < 0.3:
                a["from"] = account()
        elif from_view and op == "set_rent":
            a = {"tenant": name(), "weekly": pick([rng.randint(20, 600), "250", 2000], [19.99, 2000.01, amount()])}
        elif from_view and op == "serve_notice":
            a = {"tenant": name()}
        elif op == "read":
            a = {"what": pick(["account", "account in full", "agreements", " Account "],
                              ["everything", "", None, ["account"], 1])}
        else:  # write_notes, and any operation registered later
            a = {"text": text()}
        for k in list(a):                     # missing keys
            if rng.random() < 0.1:
                del a[k]
        if rng.random() < 0.1:                # keys no form has
            a[rng.choice(["extra", "op2", "amount", "to"])] = rng.choice([1, "x", None])
        return a

    def element(obs: list[str], rqs: list[str], owed: list[tuple[str, str, str]]):
        roll = rng.random()
        if roll < 0.05:
            return rng.choice([5, "pay", None, [], ["pay", "Ruth Ashby", 5], True])
        if roll < 0.12:
            op = rng.choice(["fly", "", None, 7, "PAY", " Pay ", "write notes", "borrow", ["pay"]])
        else:
            op = rng.choices(seen["ops"], [WEIGHTS.get(n, 1 if from_view else RARE) for n in seen["ops"]])[0]
        a = args(op.strip().lower() if isinstance(op, str) else "", obs, rqs, owed)
        return {"op": op, **a} if rng.random() > 0.03 else {**a}

    def reply(key, messages: list[dict]) -> str | None:
        roll = rng.random()
        if roll < 0.04:
            return None
        if roll < 0.10:
            return rng.choice(["I'll think about it.", "{", "{\"acts\": ", "[]", "null", "{\"acts\": \"pay\"}",
                               "{\"thoughts\": \"no acts key\"}", "}{", "\x00", "{" * 5000])
        if from_view:
            seen["names"] = view_names(messages) or NAMES
            seen["ops"] = view_ops(messages) or list(REGISTRY)
            seen["suppliers"] = view_suppliers(messages) or ["Vale Wholesale"]
            seen["business"], seen["staff"] = view_business(messages)
            seen["debts"] = view_debts(messages)
        obs, rqs, owed = _ids(messages, "ob"), _ids(messages, "rq"), _owed(messages, seen["names"])
        aps[:] = _ids(messages, "ap")
        if key.purpose == "talk":                  # a turn in a talk: words, acts, an end, or junk
            if rng.random() < 0.08:
                return rng.choice([None, "Well...", "{\"acts\": []}", "{", "{\"say\": ", "[]"])
            body = {"say": pick(["Yes.", "I'm not sure.", "Fine.\nRuth Ashby: I agree", text(), "word " * 130],
                                [None, 7, ["x"], "", "x" * 3000])}
            if rng.random() < 0.8:
                body["acts"] = [element(obs, rqs, owed) for _ in range(rng.randint(0, 6))] if rng.random() < 0.9 \
                    else rng.choice(["pay", None, 5, {"op": "pay"}])
            if rng.random() < 0.7:
                body["end"] = rng.choice([True, False, "true", 1, None]) if rng.random() < 0.5 else False
            return json.dumps(body)
        acts = [element(obs, rqs, owed) for _ in range(rng.randint(0, 10))]
        if from_view and seen["debts"] and rng.random() < 0.3:     # until ended, between people who owe already
            acts.append({"op": "propose_agreement", "to": rng.choice(seen["debts"]), "amount": rng.randint(5, 40),
                         "per": "week", "weeks": 0, "pays": rng.choice(["me", "them"]), "what": "a little each week"})
        if from_view and owed and rng.random() < 0.3:              # paid toward ahead, then stopped
            to, ob, sum_ = rng.choice(owed)
            acts += [{"op": "pay", "to": to, "amount": rng.choice([1, 5, sum_]), "for": ob}, {"op": "stop", "id": ob}]
        if from_view and seen["business"]:     # an owner's draw to nothing and back, and staff pay changed
            me = re.search(r"^You are (.+?), \d+\.", messages[0].get("content", ""), re.M)
            if me and rng.random() < 0.4:
                pair = [{"op": "set_staff_pay", "who": me.group(1), "weekly_pay": 0},
                        {"op": "set_staff_pay", "who": me.group(1), "weekly_pay": rng.choice([300, 450, 600])}]
                acts += pair * rng.choice([1, 1, 2])
            others = [n for n in seen["staff"] if not me or n != me.group(1)]
            if others and rng.random() < 0.5:
                acts.append({"op": "set_staff_pay", "who": rng.choice(others), "weekly_pay": rng.randint(450, 800)})
        body = json.dumps({"thoughts": text() if rng.random() < 0.5 else "", "acts": acts})
        if rng.random() < 0.15:
            body = rng.choice(["Here is my reply: ", "```json\n", "{\"note\": 1} "]) + body + rng.choice(["", "\n```", " {"])
        return body

    return reply


def _check_day(s, mark: int) -> None:
    """The books balance and every change has a cause; and of talking: nobody has more than two
    talks held in a day, nobody's hours go below zero, and every line belongs to a held talk."""
    assert s.ledger.total() == 0
    held = Counter(p for t in s.all("talks") if t.status == "held" for p in {(t.a, t.day), (t.b, t.day)})
    assert not held or max(held.values()) <= 2, held.most_common(1)
    assert all(p.hours_left >= 0 for p in s.all("people")), [(p.id, p.hours_left) for p in s.all("people")]
    assert all(s.has("talks", x.talk) and s.get("talks", x.talk).status == "held" for x in s.all("lines"))
    assert s.engine_errors == []
    for d in s.all("dues"):
        assert 0 <= d.paid <= d.cents, d
    for a in s.ledger.accounts.values():
        assert a.allow_credit or a.balance >= 0, a
    assert all(t.cause for t in s.ledger.transfers)
    assert all(c.cause for c in s.changes[mark:])


def _owed_in_all(record) -> int | None:
    """What an obligation was for in all when it was made: its instalments to its last, the
    last at its own sum; None for one that runs until ended."""
    if record.every == 0:
        return record.cents
    if record.last is None:
        return None
    n = (record.last - record.first) // record.every + 1
    return record.cents * (n - 1) + (record.final_cents or record.cents)


def _received_within_what_was_owed(s) -> None:
    """For every loan or agreement, what its payee has received on it (collected, counted by
    hand, and repaid early) is never more than it was for in all when it was made."""
    made = {c.id: c.new for c in s.changes if c.table == "obligations" and c.field == "+"}
    on: dict[str, str] = {}                                  # due id -> obligation id
    for d in s.all("dues"):
        on[d.id] = d.obligation
    repaid: dict[str, set[str]] = defaultdict(set)          # obligation -> causes of repay acts on it
    by_hand: dict[str, int] = defaultdict(int)
    for c in s.changes:
        ob = c.id if c.table == "obligations" else on.get(c.id) if c.table == "dues" else None
        if ob is None:
            continue
        if ":repay:" in c.cause:
            repaid[ob].add(c.cause)
        elif c.table == "dues" and c.field == "paid" and c.cause.startswith("op:"):
            by_hand[ob] += c.new - c.old
    sent = {t.cause: t.cents for t in s.ledger.transfers if ":repay:" in t.cause}
    collected: dict[str, int] = defaultdict(int)
    for t in s.ledger.transfers:
        if t.cause.startswith("routine:collect:"):
            collected[on[t.cause.removeprefix("routine:collect:")]] += t.cents
    for ob in s.all("obligations"):
        if ob.kind not in ("loan", "agreement"):
            continue
        total = _owed_in_all(made[ob.id])
        got = collected[ob.id] + by_hand[ob.id] + sum(sent[c] for c in repaid[ob.id])
        assert total is None or got <= total, (ob, got, total)


def _wage_chains(s) -> dict[str, set[str]]:
    """Each job's wage obligations, old and new, as its row named them over the run."""
    chains: dict[str, set[str]] = defaultdict(set)
    for c in s.changes:
        if c.table == "jobs" and c.field == "+" and c.new.wage_ob:
            chains[c.id].add(c.new.wage_ob)
        elif c.table == "jobs" and c.field == "wage_ob" and c.new:
            chains[c.id].add(c.new)
    return chains


def _check_wage_chains(s) -> None:
    """For each job: at most one of its wage obligations is live on any day (their spans, from
    the first instalment to the last, do not overlap), and no wage is taken for it after its
    last pay day."""
    for job, obs in _wage_chains(s).items():
        spans = []
        for ob in (s.get("obligations", o) for o in sorted(obs)):
            days = [d.day for d in s.where("dues", obligation=ob.id) if d.status != "void"]
            if ob.status in SCHEDULED:
                end = ob.last if ob.last is not None else 10 ** 9
            else:                             # finished or stopped: it ran to its last due, if any
                end = max(days, default=ob.first - 1)
            if end >= ob.first:
                spans.append((ob.first, end, ob.id))
        spans.sort()
        for (_, end, a), (start, _, b) in zip(spans, spans[1:]):
            assert start > end, (job, a, b, spans)
        ended = s.get("jobs", job).ended
        if ended != -1:
            for t in s.ledger.transfers:
                if t.cause.startswith("routine:collect:"):
                    due = s.get("dues", t.cause.removeprefix("routine:collect:"))
                    assert not (due.obligation in obs and due.day > ended), (job, ended, due)
    _check_announced(s, set().union(*_wage_chains(s).values()) if _wage_chains(s) else set())


def _check_announced(s, wages: set[str]) -> None:
    """For every wage of a job, each Friday's due is for the amount last announced for that
    Friday when it was taken: the amount it was made with from its first day, or a change of
    amount (`reprice`: `next_cents` from `next_from`) set since, whichever came last."""
    events: dict[str, list[tuple[int, int]]] = defaultdict(list)     # ob -> [(from day, cents)], in order made
    pending_cents: dict[str, int] = {}
    for c in s.changes:
        if c.table == "obligations" and c.id in wages:
            if c.field == "+":
                events[c.id].append((c.new.first, c.new.cents))
            elif c.field == "next_cents":
                pending_cents[c.id] = c.new
            elif c.field == "next_from" and c.new != -1:
                events[c.id].append((c.new, pending_cents.get(c.id, 0)))
        elif c.table == "ledger" and c.cause.startswith("routine:collect:"):
            due = s.get("dues", c.cause.removeprefix("routine:collect:"))
            if due.obligation in wages:
                said = [cents for start, cents in events[due.obligation] if start <= due.day]
                assert said and said[-1] == due.cents, (due, events[due.obligation])


def _check_shops_day(s, mark: int) -> None:
    """Of the day's shopping: each shop's units sold times its price is its takings; no stock is
    ever below nothing."""
    price = {}
    for c in s.changes[mark:]:                        # each shop's price as the day began
        if c.table == "firms" and c.field == "price":
            price.setdefault(c.id, c.old)
    for f in s.all("firms"):
        price.setdefault(f.id, f.price)
        assert all(u >= 0 for u in f.stock_units), f
    sold: Counter = Counter()
    took: Counter = Counter()
    for c in s.changes[mark:]:
        if c.table == "firms" and c.field == "price":
            price[c.id] = c.new
        elif c.table == "firms" and c.cause == "routine:shopping" and c.field in ("sold", "takings"):
            (sold if c.field == "sold" else took)[c.id] += (c.new - c.old) * (price[c.id] if c.field == "sold" else 1)
    assert sold == took, (sold, took)


def _check_rent_days(s) -> None:
    """Every rent day is opened once and none is skipped: from its first day to yesterday (or
    to its last day, or the day it was stopped), one due that is not void on each."""
    for ob in s.all("obligations"):
        if ob.kind != "rent" or ob.every == 0:
            continue
        days = Counter(d.day for d in s.where("dues", obligation=ob.id) if d.status != "void")
        assert not days or max(days.values()) == 1, (ob, days)
        end = s.day - 1
        if ob.last is not None:
            end = min(end, ob.last)
        if ob.status == "stopped":
            end = min(end, ob.stopped_on)
        assert all(days[d] == 1 for d in range(ob.first, end + 1, ob.every)), (ob, sorted(days))


def _check_benefit_and_wage(s) -> None:
    """No one is paid benefit for a week in which a wage of theirs was paid."""
    benefit: dict[str, set[str]] = defaultdict(set)
    for c in s.changes:
        if c.table == "people" and c.field == "benefit" and c.new:
            benefit[c.id].add(c.new)
    for p in s.all("people"):
        if p.benefit:
            benefit[p.id].add(p.benefit)
    for pid, obs in benefit.items():
        paid = {d.day // 7 for o in obs for d in s.where("dues", obligation=o) if d.paid > 0 and d.status != "void"}
        wages = {d.day // 7 for j in s.where("jobs", person=pid) if s.has("obligations", j.wage_ob)
                 for d in s.where("dues", obligation=j.wage_ob) if d.paid > 0 and d.status != "void"}
        assert not paid & wages, (pid, sorted(paid & wages))


def _check_run(s) -> None:
    _check_wage_chains(s)
    _received_within_what_was_owed(s)
    _check_rent_days(s)
    _check_benefit_and_wage(s)
    for job, obs in _wage_chains(s).items():                 # one wage for a job's life, its id unchanged
        assert len(obs) == 1, (job, obs)
    deliveries = Counter((t.cause, t.t // 7) for t in s.ledger.transfers if t.cause.startswith("routine:delivery:"))
    assert not deliveries or max(deliveries.values()) == 1, deliveries.most_common(1)
    collects = Counter(t.cause for t in s.ledger.transfers if t.cause.startswith("routine:collect:"))
    assert not collects or max(collects.values()) == 1, collects.most_common(1)
    for cause in collects:                 # nothing is taken for a void due, a day after an obligation's last,
        due = s.get("dues", cause.removeprefix("routine:collect:"))      # or a day after it was stopped
        ob = s.get("obligations", due.obligation)
        assert due.status != "void", due
        assert ob.last is None or due.day <= ob.last, (due, ob.last)
        assert ob.status != "stopped" or due.day <= ob.stopped_on, (due, ob.stopped_on)

    # The strong form: what reached the payee for each paid due is exactly its sum. A
    # payment by hand counted toward a due cannot be told from the ledger alone (its memo
    # names the obligation and date, not the due), so the change log is read instead: each
    # `paid` change on a due under an `op:` cause is part of that act's one transfer, and
    # the due's collect transfer (if any) brings it to its sum.
    by_hand: dict[str, int] = defaultdict(int)       # due id -> cents counted by hand
    per_cause: dict[str, int] = defaultdict(int)     # op cause -> cents it counted toward dues
    for c in s.changes:
        if c.table == "dues" and c.field == "paid" and c.cause.startswith("op:"):
            by_hand[c.id] += c.new - c.old
            per_cause[c.cause] += c.new - c.old
    sent = {t.cause: t for t in s.ledger.transfers if t.cause.startswith("op:")}
    for cause, cents in per_cause.items():
        assert cause in sent and sent[cause].cents >= cents, cause
    collected = {t.cause.removeprefix("routine:collect:"): t for t in s.ledger.transfers
                 if t.cause.startswith("routine:collect:")}
    for d in s.all("dues"):
        ob = s.get("obligations", d.obligation)
        t = collected.get(d.id)
        if t is not None:
            assert (t.src, t.dst) == (ob.payer, ob.payee)
        if d.status == "paid":
            assert by_hand[d.id] + (t.cents if t else 0) == d.cents, d
        else:
            assert t is None and by_hand[d.id] == d.paid, d


def _hand_matched(s) -> list[tuple[str, int, int]]:
    """(due id, cents counted by hand, cents the due is for) for each payment by hand counted toward a due."""
    return [(c.id, c.new - c.old, s.get("dues", c.id).cents) for c in s.changes
            if c.table == "dues" and c.field == "paid" and c.cause.startswith("op:")]


@functools.cache
def _played(seed: int):
    """The store after `_play(seed)`, played once for all the tests that read it. Do not change it."""
    return _play(seed)


def _play(seed: int):
    rng = random.Random(seed)
    s = thin_town()
    replier = ScriptedReplier(random_script(rng))
    keys = Keys("r")
    for _ in range(DAYS):
        mark = s.mark()
        run_day(s, replier, keys)
        _check_day(s, mark)
    return s


@pytest.mark.parametrize("seed", SEEDS)
def test_random_acts_never_break_the_books(seed):
    _check_run(_played(seed))


def test_random_play_counts_payments_toward_what_is_owed():
    """Across the seeds: payments by hand are counted toward dues in most of them, and
    there is at least one part-payment by hand, one late fee and one default."""
    stores = [_played(seed) for seed in SEEDS]
    matched = [_hand_matched(s) for s in stores]
    assert sum(1 for m in matched if m) > len(stores) // 2, [len(m) for m in matched]
    assert any(cents < due_cents for m in matched for _, cents, due_cents in m)          # part of a due
    assert any(o.penalty for s in stores for o in s.all("obligations"))                     # a late fee
    assert any(c.table == "dues" and c.field == "status" and c.new == "defaulted"
               for s in stores for c in s.changes)                                          # a default


def test_random_play_does_things():
    """The script is not all refusals: across the seeds, money moves by hand, requests are
    answered, standing payments are set up and stopped."""
    done = Counter()
    for seed in range(5):
        s = _played(seed)
        done.update(c.cause.split(":")[2] for c in s.changes
                    if c.cause.startswith("op:") and c.table not in ("results", "counter"))   # what was done, not told
    for op in ("pay", "standing_payment", "stop", "message", "request", "answer", "write_notes"):
        assert done[op] > 0, op


@pytest.mark.parametrize("seed", REPLAYED)
def test_random_runs_replay_exactly(seed, tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        db.create_run("rand", seed=seed)
        final = run(db, "rand", thin_town(), ScriptedReplier(random_script(random.Random(seed))), DAYS)
        assert replay(db, "rand") == []
    assert final.hash() == _played(seed).hash()


HAMLET_DAYS = 28
HAMLET_SEEDS = range(12)
WORK = ("offer_job", "offer_shifts", "dismiss", "set_staff_pay", "set_price", "order_stock", "work_extra_hours",
        "move_money", "quit")


@functools.cache
def _hamlet_played(seed: int):
    """A generated place played for four weeks on `random_script` reading each view, checked
    after every day; with the operations done and the largest view seen. Played once for all
    the tests that read it. Do not change what it returns."""
    s = gen.generate(seed + 1, ["a", "b", "c", "d"])
    rng = random.Random(f"hamlet:{seed}")
    inner = random_script(rng, from_view=True)
    biggest = [0]

    def reply(key, messages):
        biggest[0] = max(biggest[0], sum(len(m["content"].split()) for m in messages))
        return inner(key, messages)

    replier = ScriptedReplier(reply)
    keys = Keys("h")
    done: Counter = Counter()
    real = firms.shop_today

    def shop_today(store):                     # no shop sells more in a week than its staff can serve
        caps = {f.id: firms.capacity(store, f.id) for f in store.all("firms")}
        before = {f.id: f.sold for f in store.all("firms")}
        real(store)
        for f in store.all("firms"):
            assert f.sold == before[f.id] or f.sold <= caps[f.id], (store.day, f.id, f.sold, caps[f.id])
    firms.shop_today = shop_today
    try:
        for _ in range(HAMLET_DAYS):
            mark = s.mark()
            report = run_day(s, replier, keys)
            _check_day(s, mark)
            _check_shops_day(s, mark)
            done.update(o.act.op for t in report.turns for o in t.outcomes if o.ok and isinstance(o.act.op, str))
    finally:
        firms.shop_today = real
    return s, done, biggest[0]


@pytest.mark.parametrize("seed", HAMLET_SEEDS)
def test_random_play_in_the_hamlet_never_breaks_the_books(seed):
    """The same invariants on a generated place for four weeks, the script reading real names
    and ids, and choosing among the operations, from each view; no view reaches 2,000 words."""
    s, _, biggest = _hamlet_played(seed)
    _check_run(s)
    assert biggest < 2000
    assert any(c.cause.startswith("op:") and c.table == "ledger" for c in s.changes)


def test_random_play_in_the_hamlet_does_the_work_of_shops():
    """Across the hamlet seeds, every operation of work was carried out at least once."""
    done = sum((_hamlet_played(seed)[1] for seed in HAMLET_SEEDS), Counter())
    for op in WORK:
        assert done[op] > 0, op


DEALS = ("propose_agreement", "propose_end", "borrow", "repay", "set_rent")


def test_random_play_in_the_hamlet_makes_agreements_loans_and_rents():
    """Across the hamlet seeds, each operation of agreements, loans and rent was carried out at
    least once, and a loan was granted; notice to quit needs rent two weeks unpaid, which four
    weeks of random play need not bring about, so it is left to its own tests."""
    done = sum((_hamlet_played(seed)[1] for seed in HAMLET_SEEDS), Counter())
    for op in DEALS:
        assert done[op] > 0, op
    assert any(r.kind == "loan" and r.status == "accepted" for seed in HAMLET_SEEDS
               for r in _hamlet_played(seed)[0].all("requests"))


def test_random_play_talks():
    """Across the seeds, in the thin town and the hamlet, talks of every kind are held, with
    acts made in them, and appointments are booked and cancelled."""
    for stores in ([_played(seed) for seed in range(10)], [_hamlet_played(seed)[0] for seed in range(4)]):
        kinds = Counter(t.kind for s in stores for t in s.all("talks") if t.status == "held")
        assert kinds["asked"] and kinds["household"], kinds
        assert any(x.results for s in stores for x in s.all("lines"))
        assert any(a.status in ("held", "cancelled") for s in stores for a in s.all("appointments"))


def test_random_play_in_the_hamlet_agrees_until_ended_between_debtors_and_stops_what_was_paid_ahead():
    """Across the hamlet seeds an agreement until ended is made between people who already owe
    each other, and an obligation paid toward ahead is then stopped."""
    stores = [_hamlet_played(seed)[0] for seed in HAMLET_SEEDS]
    debts = [{(o.payer, o.payee) for o in s.all("obligations") if o.memo.startswith("paying back")} for s in stores]
    assert any(o.kind == "agreement" and o.last is None and o.every and o.made_by != "routine"
               and ((o.payer, o.payee) in d or (o.payee, o.payer) in d)
               for s, d in zip(stores, debts) for o in s.all("obligations"))
    assert any(d.status == "void" and d.paid > 0 and s.get("obligations", d.obligation).status == "stopped"
               for s in stores for d in s.all("dues"))
