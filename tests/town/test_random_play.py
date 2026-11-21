"""Random play: replies drawn at random, some sensible and some junk, never break the books.

Each seed plays the thin town for six weeks on replies from `random_script`. After every
day the books must balance, no operation may have failed inside the engine, every due
must be paid between nothing and its sum, no account without credit may be below zero,
and every transfer and change must carry a cause. At the end, no due is collected twice
and every paid due received exactly its sum.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict

import pytest

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


def _ids(messages: list[dict], prefix: str) -> list[str]:
    text = " ".join(m.get("content", "") for m in messages)
    return sorted(set(i for i in _IDS.findall(text) if i.startswith(prefix)))


def random_script(rng: random.Random):
    """A reply function for `ScriptedReplier`: for each turn, zero to ten acts from every
    registered operation (and some that are not), with arguments read from the view
    (names, obligation and request ids) or invented (wrong types, unknown names, huge or
    negative sums, missing keys), and now and then text that is not JSON at all.
    All of its randomness comes from `rng`."""

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
        return pick(NAMES, JUNK_NAMES)

    def args(op: str, obs: list[str], rqs: list[str]) -> dict:
        if op == "pay":
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

    def element(obs: list[str], rqs: list[str]):
        roll = rng.random()
        if roll < 0.05:
            return rng.choice([5, "pay", None, [], ["pay", "Ruth Ashby", 5], True])
        if roll < 0.12:
            op = rng.choice(["fly", "", None, 7, "PAY", " Pay ", "write notes", "borrow", ["pay"]])
        else:
            op = rng.choices(list(REGISTRY), [WEIGHTS.get(n, 1) for n in REGISTRY])[0]
        a = args(op.strip().lower() if isinstance(op, str) else "", obs, rqs)
        return {"op": op, **a} if rng.random() > 0.03 else {**a}

    def reply(key, messages: list[dict]) -> str | None:
        roll = rng.random()
        if roll < 0.04:
            return None
        if roll < 0.10:
            return rng.choice(["I'll think about it.", "{", "{\"acts\": ", "[]", "null", "{\"acts\": \"pay\"}",
                               "{\"thoughts\": \"no acts key\"}", "}{", "\x00", "{" * 5000])
        obs, rqs = _ids(messages, "ob"), _ids(messages, "rq")
        acts = [element(obs, rqs) for _ in range(rng.randint(0, 10))]
        body = json.dumps({"thoughts": text() if rng.random() < 0.5 else "", "acts": acts})
        if rng.random() < 0.15:
            body = rng.choice(["Here is my reply: ", "```json\n", "{\"note\": 1} "]) + body + rng.choice(["", "\n```", " {"])
        return body

    return reply


def _check_day(s, mark: int) -> None:
    assert s.ledger.total() == 0
    assert s.engine_errors == []
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
    s = _play(seed)
    _check_run(s)


def test_random_play_does_things():
    """The script is not all refusals: across the seeds, money moves by hand, requests are
    answered, standing payments are set up and stopped."""
    done = Counter()
    for seed in range(5):
        s = _play(seed)
        done.update(c.cause.split(":")[2] for c in s.changes if c.cause.startswith("op:"))
    for op in ("pay", "standing_payment", "stop", "message", "request", "answer", "write_notes"):
        assert done[op] > 0, op


@pytest.mark.parametrize("seed", REPLAYED)
def test_random_runs_replay_exactly(seed, tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        db.create_run("rand", seed=seed)
        final = run(db, "rand", thin_town(), ScriptedReplier(random_script(random.Random(seed))), DAYS)
        assert replay(db, "rand") == []
    assert final.hash() == _play(seed).hash()
