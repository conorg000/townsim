import random
from pathlib import Path

import pytest

import townsim.bribery  # noqa: F401  (registers the production rules before any test saves RULES)
import townsim.market  # noqa: F401  (registers the production rules before any test saves RULES)
import townsim.press  # noqa: F401  (registers the production rules before any test saves RULES)
import townsim.tax  # noqa: F401  (registers the production rules before any test saves RULES)
from townsim.config import TownConfig, build_world, read_config_dict
from townsim.decide import RULES, Decision, Option
from townsim.market import close_firm
from townsim.world import (
    Firm,
    Info,
    Laws,
    Newspaper,
    Person,
    Regulator,
    Treasury,
    World,
)


TOWN_YAML = Path(__file__).resolve().parent.parent / "config" / "town.yaml"


@pytest.fixture(autouse=True)
def _restore_rules():
    """Leave RULES as it was, so rules a test registers do not leak into other tests."""
    saved = dict(RULES)
    yield
    RULES.clear()
    RULES.update(saved)


@pytest.fixture
def rng() -> random.Random:
    return random.Random(0)


@pytest.fixture
def cfg_dict() -> dict:
    """The parsed town.yaml with personas attached; a fresh dict each test, safe to mutate."""
    return read_config_dict(str(TOWN_YAML))


@pytest.fixture
def cfg(cfg_dict) -> TownConfig:
    return TownConfig.model_validate(cfg_dict)


@pytest.fixture
def world(cfg) -> World:
    return build_world(cfg)


@pytest.fixture
def one_firm_world(world) -> World:
    """The config world with every firm except f1 closed."""
    for fid in sorted(world.firms):
        if fid != "f1":
            close_firm(world, fid)
    return world


@pytest.fixture
def fraud_world(world) -> World:
    """The config world one week after f1 hid 10000 of tax at step 13 with heavy concealment."""
    f1 = world.firms["f1"]
    world.step = 14
    f1.cash = 100000
    f1.last_reported_profit = 50000
    world.regulator.budget_per_quarter = 6
    world.regulator.budget_left = 6
    world.regulator.tips = []
    world.infos["info:13:tax_fraud:f1"] = Info(
        id="info:13:tax_fraud:f1",
        step=13,
        kind="tax_fraud",
        subject_id="f1",
        payload={"unpaid": 10000, "concealment": "heavy", "quarter": 1},
        known_by={f1.owner_id},
    )
    return world


@pytest.fixture
def known_fraud_world(fraud_world) -> World:
    """`fraud_world` where f1's first employee also knows of the fraud."""
    first = fraud_world.firms["f1"].employee_ids[0]
    fraud_world.infos["info:13:tax_fraud:f1"].known_by.add(first)
    return fraud_world


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
