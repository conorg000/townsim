import random

import pytest

from townsim.decide import Decision, Option
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


@pytest.fixture
def pricing_decision() -> Decision:
    return Decision(
        id="0:p0:pricing:0",
        actor_id="p0",
        kind="pricing",
        situation="Your shop sells bread at 100 cents a loaf. Choose a new price.",
        options=[
            Option(id="down10", label="Lower the price by 10%"),
            Option(id="down5", label="Lower the price by 5%"),
            Option(id="hold", label="Keep the price"),
            Option(id="up5", label="Raise the price by 5%"),
            Option(id="up10", label="Raise the price by 10%"),
        ],
        default_option_id="hold",
        facts={"price": 100},
    )


@pytest.fixture
def unknown_kind_decision() -> Decision:
    return Decision(
        id="0:p0:no_such_kind:0",
        actor_id="p0",
        kind="no_such_kind",
        situation="Choose one.",
        options=[Option(id="a", label="A"), Option(id="b", label="B")],
        default_option_id="b",
    )
