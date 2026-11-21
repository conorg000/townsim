"""World state: the people, firms and institutions of the town."""

from typing import Literal

from pydantic import BaseModel, Field


class Assistant(BaseModel):
    """An AI system a firm's owner has put in charge of their business.

    `reports` holds {step, text} items written for the client. `since` and `paper` are
    lines gathered at the end of each week for the next briefing, and cleared by it.
    """

    instruction: str
    decider: str
    notes: str = ""
    reports: list[dict] = Field(default_factory=list)
    since: list[str] = Field(default_factory=list)
    paper: list[str] = Field(default_factory=list)
    # The week of this instance's first turn: 0 for a client named in the config, the week
    # after the sign-up for an owner who signed up during the run.
    started: int = 0


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
    # Set when this person owns a firm and has put an AI system in charge of it.
    assistant: Assistant | None = None
    # Messages {step, from_id, from_assistant, from_desc, text} and payment notices
    # {step, from_id, from_desc, amount, memo}, oldest first; kept for 8 weeks.
    inbox: list[dict] = Field(default_factory=list)


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
    # The firm's own books for its last few weeks, oldest first: one
    # [price, customers, takings, wages paid] entry per week. The owner can see these.
    recent_weeks: list[list[int]] = Field(default_factory=list)
    last_reported_profit: int | None = None
    # The firm's books for its last 8 weeks, oldest first: one
    # [step, customers, capacity, takings, wage bill] entry per week.
    books: list[list[int]] = Field(default_factory=list)


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


class Provider(BaseModel):
    """The company that offers the AI system: its account, its terms, and the firms' quarter.

    `quarter` and `last_quarter` hold, for each firm, [customers served, takings, weeks
    open, weeks run by the AI system] over the current and the previous quarter.
    `quarter_start` holds each firm's figures (see `firm_figures`) when the current quarter
    began, and `fees_qtd` what each firm has paid in fees this quarter.
    """

    cash: int = 0
    decider: str = ""
    fee: int = 0  # cents a week, paid by each client's firm
    adoption: bool = False  # whether owners are offered the system each quarter
    quarter_began: int = 0
    quarter: dict[str, list[int]] = Field(default_factory=dict)
    last_quarter: dict[str, list[int]] = Field(default_factory=dict)
    quarter_start: dict[str, dict[str, int]] = Field(default_factory=dict)
    fees_qtd: dict[str, int] = Field(default_factory=dict)


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
    # The AI system some owners have put in charge of their firms; empty when there is none.
    assistant_name: str = ""
    assistant_provider: str = ""
    assistant_max_rounds: int = 0
    assistant_max_actions: int = 0
    # The AI system's provider; None when the town has no AI system. Left out of the
    # state hash when None, so a town without one hashes as it always has.
    provider: Provider | None = None


def firm_figures(world: World, firm_id: str) -> dict[str, int]:
    """What an owner sees of their firm at a moment: price, pay, staff, its cash and their own."""
    f = world.firms[firm_id]
    return {
        "price": f.price,
        "wage": f.wage,
        "staff": len(f.employee_ids),
        "cash": f.cash,
        "owner_cash": world.people[f.owner_id].cash,
    }


def total_money(world: World) -> int:
    """Total cash held by people, firms, the treasury, the newspaper and the AI's provider."""
    return (
        sum(p.cash for p in world.people.values())
        + sum(f.cash for f in world.firms.values())
        + world.treasury.cash
        + world.newspaper.cash
        + (world.provider.cash if world.provider is not None else 0)
    )
