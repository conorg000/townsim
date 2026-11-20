"""World state: the people, firms and institutions of the town."""

from typing import Literal

from pydantic import BaseModel, Field


class Person(BaseModel):
    id: str
    name: str
    persona: str
    role: Literal["owner", "worker", "auditor", "journalist", "unemployed"]
    cash: int
    employer_id: str | None
    ambition: float
    scruples: float
    decider: str
    loyalty: dict[str, float] = Field(default_factory=dict)


class Firm(BaseModel):
    id: str
    name: str
    market: Literal["food", "services"]
    owner_id: str
    cash: int
    price: int
    wage: int
    quality: float
    employee_ids: list[str]
    open: bool = True
    profit_qtd: int = 0
    customers_last_step: int = 0
    last_reported_profit: int | None = None


class Treasury(BaseModel):
    cash: int


class Newspaper(BaseModel):
    cash: int
    journalist_ids: list[str]
    press_tips: list[str] = Field(default_factory=list)


class Regulator(BaseModel):
    auditor_id: str
    budget_per_quarter: int
    budget_left: int
    tips: list[str] = Field(default_factory=list)


class Laws(BaseModel):
    tax_rate: float
    penalty_multiplier: float
    benefit: int
    illegal: set[str]


class Info(BaseModel):
    id: str
    step: int
    kind: str
    subject_id: str
    payload: dict
    known_by: set[str]
    decided_by: set[str] = Field(default_factory=set)


class World(BaseModel):
    step: int = 0
    people: dict[str, Person]
    firms: dict[str, Firm]
    treasury: Treasury
    newspaper: Newspaper
    regulator: Regulator
    laws: Laws
    infos: dict[str, Info] = Field(default_factory=dict)
    params: dict[str, float] = Field(default_factory=dict)


def total_money(world: World) -> int:
    """Total cash held by people, firms, the treasury and the newspaper."""
    return (
        sum(p.cash for p in world.people.values())
        + sum(f.cash for f in world.firms.values())
        + world.treasury.cash
        + world.newspaper.cash
    )
