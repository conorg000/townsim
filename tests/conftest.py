import random

import pytest

from townsim.world import (
    Firm,
    Laws,
    Newspaper,
    Person,
    Regulator,
    Treasury,
    World,
)


@pytest.fixture
def rng() -> random.Random:
    return random.Random(0)


@pytest.fixture
def tiny_world() -> World:
    owner = Person(
        id="p0",
        name="Olive",
        persona="A careful shop owner.",
        role="owner",
        cash=0,
        employer_id=None,
        ambition=0.5,
        scruples=0.5,
        decider="scripted",
    )
    worker = Person(
        id="p1",
        name="Walt",
        persona="A steady worker.",
        role="worker",
        cash=1000,
        employer_id="f1",
        ambition=0.5,
        scruples=0.5,
        decider="scripted",
    )
    firm = Firm(
        id="f1",
        name="Corner Grocer",
        market="food",
        owner_id="p0",
        cash=1000,
        price=100,
        wage=500,
        quality=0.5,
        employee_ids=["p1"],
    )
    return World(
        people={"p0": owner, "p1": worker},
        firms={"f1": firm},
        treasury=Treasury(cash=0),
        newspaper=Newspaper(cash=0, journalist_ids=[]),
        regulator=Regulator(auditor_id="", budget_per_quarter=0, budget_left=0),
        laws=Laws(
            tax_rate=0.2,
            penalty_multiplier=2.0,
            benefit=6000,
            illegal={"underreport_tax", "offer_bribe", "accept_bribe"},
        ),
    )
