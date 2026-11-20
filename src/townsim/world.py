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
    # Set when this person has handed their decisions to an AI agent: the standing
    # instruction they gave it. The agent acts in their name with their powers.
    agent_goal: str | None = None


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
    revenue_last_step: int = 0
    # Share added to the listed price for regulars (people whose loyalty to the firm is high).
    regulars_markup: float = 0.0
    # The firm's own books for its last few weeks, oldest first: one
    # [price, customers, takings, wages paid] entry per week. The owner can see these.
    recent_weeks: list[list[int]] = Field(default_factory=list)
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
    # The lowest weekly wage a firm may set, in cents; 0 means the town has no minimum.
    min_wage: int = 0


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
