"""Scripted stand-ins for the lifter and the narrator, and a store with lane B, for the tasks' tests."""

import json

from town.test_seat_texts import drawn
from townsim.town import lane_b, obligations, tasks
from townsim.town.replies import Keys, ScriptedReplier

CAUSE = "routine:setup"


def b_store():
    s = drawn()
    lane_b.install(s)
    return s


def lift(goal="advertise the shop", targets=(("org11", "Harlow Springs Food Mart"),), others=(), claims=(),
         addressed=()):
    return {"goal": goal, "steps": [goal], "targets": [{"id": i, "name": n} for i, n in targets],
            "others_involved": [{"id": i, "name": n, "part": p} for i, n, p in others], "claims": list(claims),
            "addressed_to_reader": list(addressed)}


class Outside:
    """Answers the lifter with `lifts[task id]` (or `default_lift`) and the narrator with
    `rulings[task id]`: a list of entries for both filings, or a pair (first, second). Keeps every call."""

    def __init__(self, rulings=None, lifts=None, default_lift=None, people=None):
        self.rulings = rulings or {}
        self.lifts = lifts or {}
        self.default_lift = default_lift or lift()
        self.people = people
        self.calls = []

    def __call__(self, key, messages):
        self.calls.append((key, messages))
        if key.actor == "lifter":
            got = self.lifts.get(key.ref, self.default_lift)
            return got if isinstance(got, str) else json.dumps(got)
        if key.actor == "narrator":
            r = self.rulings[key.ref]
            r = r[key.n] if isinstance(r, tuple) else r
            return r if isinstance(r, str) else json.dumps({"entries": r})
        return self.people(key, messages) if self.people else None

    def of(self, actor):
        return [m for k, m in self.calls if k.actor == actor]


def narrate(s, outside):
    return tasks.narrate_day(s, ScriptedReplier(outside), Keys("t"))


def mornings(s, n):
    """`n` mornings of collection and completion, nothing else of the day."""
    for _ in range(n):
        s.advance_day()
        obligations.open_dues(s)
        obligations.age_arrears(s)
        obligations.collect(s)
        tasks.complete_due(s)


CAMPAIGN = [{"op": "project", "kind": "campaign", "scale": "medium", "target": "org11", "on_complete": []}]
