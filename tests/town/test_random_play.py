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

from townsim.town import gen
from townsim.town.acts import REGISTRY
from townsim.town.loop import run_day
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
           "write_notes": 1}
_IDS = re.compile(r"\b(?:ob|rq)\d+\b")


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
    seen = {"names": NAMES, "ops": list(REGISTRY)}

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
            a = {"id": pick(obs, ["ob999", "ob0", "", None, 9, "OB1", "rq1"])}
        elif op in ("message", "request"):
            a = {"to": name(), "text": text()}
            if op == "request":
                a["amount"] = pick([0, None, rng.randint(1, 200), "0.00"], [amount()], 0.5)
        elif op == "answer":
            a = {"request": pick(rqs, ["rq999", "", None, 1, "ob1"]),
                 "answer": pick(["accept", "decline", " ACCEPT ", "Decline"], ["maybe", "", None, True, "yes"]),
                 "note": text()}
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
            op = rng.choices(seen["ops"], [WEIGHTS.get(n, 1) for n in seen["ops"]])[0]
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
        obs, rqs, owed = _ids(messages, "ob"), _ids(messages, "rq"), _owed(messages, seen["names"])
        acts = [element(obs, rqs, owed) for _ in range(rng.randint(0, 10))]
        body = json.dumps({"thoughts": text() if rng.random() < 0.5 else "", "acts": acts})
        if rng.random() < 0.15:
            body = rng.choice(["Here is my reply: ", "```json\n", "{\"note\": 1} "]) + body + rng.choice(["", "\n```", " {"])
        return body

    return reply


# A repair to a house whose regular spending has been stopped (which a payer may do) has no
# one to be paid to: the engine notes it and passes over the event, as it is meant to.
_EXPECTED = re.compile(r"^event ev\d+ d\d+: repair no regular spending in force for 'h\d+'; not applied$")


def _check_day(s, mark: int) -> None:
    assert s.ledger.total() == 0
    assert [e for e in s.engine_errors if not _EXPECTED.match(e)] == []
    for d in s.all("dues"):
        assert 0 <= d.paid <= d.cents, d
    for a in s.ledger.accounts.values():
        assert a.allow_credit or a.balance >= 0, a
    assert all(t.cause for t in s.ledger.transfers)
    assert all(c.cause for c in s.changes[mark:])


def _check_run(s) -> None:
    collects = Counter(t.cause for t in s.ledger.transfers if t.cause.startswith("routine:collect:"))
    assert not collects or max(collects.values()) == 1, collects.most_common(1)

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
    assert final.hash() == _play(seed).hash()


HAMLET_DAYS = 28
HAMLET_SEEDS = range(20)


@pytest.mark.parametrize("seed", HAMLET_SEEDS)
def test_random_play_in_the_hamlet_never_breaks_the_books(seed):
    """The same invariants on a generated place for four weeks, the script reading real names
    and ids, and choosing among the operations, from each view; no view reaches 2,000 words."""
    s = gen.generate(seed + 1, ["a", "b", "c", "d"])
    rng = random.Random(f"hamlet:{seed}")
    inner = random_script(rng, from_view=True)
    biggest = [0]

    def reply(key, messages):
        biggest[0] = max(biggest[0], sum(len(m["content"].split()) for m in messages))
        return inner(key, messages)

    replier = ScriptedReplier(reply)
    keys = Keys("h")
    for _ in range(HAMLET_DAYS):
        mark = s.mark()
        run_day(s, replier, keys)
        _check_day(s, mark)
    _check_run(s)
    assert biggest[0] < 2000
    assert any(c.cause.startswith("op:") and c.table == "ledger" for c in s.changes)
