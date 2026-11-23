"""Faults found by the paid runs on seeds 2 and 3 (towns the setup had not run on), each pinned on a generated town with lane B.
Nothing here calls a model."""

from __future__ import annotations

import functools
import pickle

import pytest

from townsim.town import gen, lane_b, product
from townsim.town.acts import Act, apply_act

FAMILIES = ["a", "b", "c", "d"]


@functools.cache
def _bytes(seed: int) -> bytes:
    s = gen.generate(seed, FAMILIES)
    lane_b.install(s)
    product.launch(s)
    return pickle.dumps(s)


def town(seed: int):
    return pickle.loads(_bytes(seed))


def adults(s, h):
    return [p for p in s.get("households", h).members if s.get("people", p).age >= 18]


def do(s, actor, op, **args):
    return apply_act(s, Act(actor, actor, op, args), 1)


def a_writer(s):
    return next(p.id for p in s.all("people") if p.age >= 18)


@pytest.mark.parametrize("seed", [2, 3])
def test_a_letter_to_a_desks_signer_reaches_the_desk(seed):
    s = town(seed)
    who = a_writer(s)
    for d in s.all("desks"):
        org = s.get("orgs", d.org).name
        for to in (d.signer, f"{d.signer}, {org}", f"{d.signer} at {org}", f"{org}, {d.signer}"):
            before = len(s.all("messages"))
            out = do(s, who, "message", to=to, text="Please confirm the figures.")
            assert out.ok, (to, out.msg)
            [m] = s.all("messages")[before:]
            assert m.to == d.org and m.org == d.org, (to, m)


@pytest.mark.parametrize("seed", [2, 3])
def test_a_household_is_not_a_person_and_its_adults_are_named(seed):
    s = town(seed)
    h = next(h for h in s.all("households") if len(adults(s, h.id)) >= 2)
    who = next(p.id for p in s.all("people") if p.age >= 18 and p.household != h.id)
    names = [s.get("people", p).name for p in adults(s, h.id)]
    want = f"The {h.name} is not a person; its adults are {', '.join(names[:-1])} and {names[-1]}."
    for op, args in (("message", {"to": h.name, "text": "Hello."}),
                     ("request", {"to": h.name, "text": "Please pay.", "amount": 10})):
        out = do(s, who, op, **args)
        assert not out.ok and out.msg == f"Not done: {want}", (op, out.msg)
