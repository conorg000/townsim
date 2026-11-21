"""Town configuration: load it from YAML, check it, and build the starting world."""

import re
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from townsim.world import Assistant, Firm, Laws, Newspaper, Person, Regulator, Treasury, World

Role = Literal["owner", "worker", "auditor", "journalist", "unemployed"]
ROLES: frozenset[str] = frozenset(get_args(Role))


class ConfigError(ValueError):
    """The town configuration is missing, malformed or inconsistent."""


# Words that would say or imply the world is constructed. No prompt may use them; config
# load rejects persona text that does, and the tests check every prompt against them.
FORBIDDEN_WORDS = re.compile(
    r"(?i)simulat|experiment|\btest(?:s|ed|ing|er|ers)?\b|\bgam(?:e|es|ed|ing|er|ers)\b"
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirmConfig(_Strict):
    id: str
    name: str
    market: Literal["food", "services"]
    quality: float
    price: int
    wage: int
    staff: int = Field(ge=0)
    cash: int


class PeopleConfig(_Strict):
    count: int = Field(ge=0)
    auditors: int = Field(ge=0)
    journalists: int = Field(ge=0)
    start_cash: int


class LawsConfig(_Strict):
    tax_rate: float
    penalty_multiplier: float
    benefit: int
    illegal: list[str]
    min_wage: int = Field(default=0, ge=0)


class RuleDeciderConfig(_Strict):
    type: Literal["rule"]


class ModelDeciderConfig(_Strict):
    type: Literal["model"]
    model: str
    # Left out of the call when None: some models reject or ignore a temperature.
    temperature: float | None = None
    # Sent only when set, e.g. "none", "low", "high". Leave unset for the model's default.
    reasoning_effort: str | None = None
    # Upper bound on tokens billed per reply, hidden reasoning included. Unset means no cap.
    max_tokens: int | None = Field(default=None, gt=0)
    timeout: float = Field(default=60.0, gt=0)  # seconds per completion call


class SystemOneDeciderConfig(_Strict):
    type: Literal["system_one"]
    model: str


DeciderConfig = Annotated[
    RuleDeciderConfig | ModelDeciderConfig | SystemOneDeciderConfig,
    Field(discriminator="type"),
]


class AssignmentConfig(_Strict):
    default: str
    by_role: dict[Role, str] = Field(default_factory=dict)
    by_person: dict[str, str] = Field(default_factory=dict)


class AssistantConfig(_Strict):
    """An AI system offered to the town's businesses, and the owners who signed up for it.

    `clients` maps each client (the owner of a firm) to the instruction they gave it;
    each client gets a separate instance. `decider` names the configured decider that
    plays it. Each weekly turn has up to `max_rounds` model calls and `max_actions` tool uses.
    """

    name: str
    provider: str
    decider: str
    clients: dict[str, str]
    max_rounds: int = Field(default=3, ge=1)
    max_actions: int = Field(default=12, ge=1)


class PersonaConfig(_Strict):
    name: str
    persona: str
    ambition: float = Field(ge=0.0, le=1.0)
    scruples: float = Field(ge=0.0, le=1.0)


class TownConfig(_Strict):
    """The whole town. Validation failures raise `ConfigError`."""

    firms: list[FirmConfig]
    people: PeopleConfig
    treasury_cash: int
    newspaper_cash: int
    laws: LawsConfig
    params: dict[str, float]
    deciders: dict[str, DeciderConfig]
    assignment: AssignmentConfig
    assistant: AssistantConfig | None = None
    personas: list[PersonaConfig]

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "TownConfig":
        try:
            return super().model_validate(obj, **kwargs)
        except ValidationError as e:
            raise ConfigError(str(e)) from e

    def __init__(self, **data: Any):
        try:
            super().__init__(**data)
        except ValidationError as e:
            raise ConfigError(str(e)) from e

    @model_validator(mode="after")
    def _check_consistency(self) -> "TownConfig":
        firm_ids = [f.id for f in self.firms]
        if len(set(firm_ids)) != len(firm_ids):
            raise ValueError(f"duplicate firm ids: {firm_ids}")
        below = _below_min_wage(self)
        if below:
            raise ValueError(below)

        needed = sum(1 + f.staff for f in self.firms) + self.people.auditors + self.people.journalists
        if needed > self.people.count:
            raise ValueError(
                f"roles need {needed} people but people.count is {self.people.count}"
            )
        if len(self.personas) < self.people.count:
            raise ValueError(
                f"only {len(self.personas)} personas for {self.people.count} people"
            )
        for i, p in enumerate(self.personas):
            for field, text in (("name", p.name), ("persona", p.persona)):
                match = FORBIDDEN_WORDS.search(text)
                if match:
                    raise ValueError(
                        f"personas[{i}].{field} uses the forbidden word {match.group()!r}: {text!r}"
                    )
        if self.people.auditors > 1:
            raise ValueError("the regulator has one auditor; people.auditors must be 0 or 1")

        known = set(self.deciders)
        a = self.assignment
        used = [("assignment.default", a.default)]
        used += [(f"assignment.by_role.{r}", k) for r, k in a.by_role.items()]
        used += [(f"assignment.by_person.{p}", k) for p, k in a.by_person.items()]
        for where, key in used:
            if key not in known:
                raise ValueError(f"{where}: unknown decider {key!r} (known: {sorted(known)})")

        person_ids = set(_person_ids(self.people.count))
        for pid in a.by_person:
            if pid not in person_ids:
                raise ValueError(f"assignment.by_person: unknown person id {pid!r}")
        if self.assistant is not None:
            _check_assistant(self.assistant, known, _person_ids(self.people.count)[: len(self.firms)])
        return self


def _check_assistant(spec: AssistantConfig, deciders: set[str], owner_ids: list[str]) -> None:
    """The assistant's decider must exist and every client must own a firm."""
    if spec.decider not in deciders:
        raise ValueError(f"assistant.decider: unknown decider {spec.decider!r} (known: {sorted(deciders)})")
    if not spec.clients:
        raise ValueError("assistant.clients: no clients")
    for where, text in (("name", spec.name), ("provider", spec.provider)):
        if not text.strip():
            raise ValueError(f"assistant.{where} is empty")
    for pid, instruction in spec.clients.items():
        if pid not in owner_ids:
            raise ValueError(f"assistant.clients: {pid!r} does not own a firm (owners: {owner_ids})")
        if not instruction.strip():
            raise ValueError(f"assistant.clients.{pid}: the instruction is empty")
    for where, text in (
        ("name", spec.name),
        ("provider", spec.provider),
        *((f"clients.{pid}", t) for pid, t in spec.clients.items()),
    ):
        match = FORBIDDEN_WORDS.search(text)
        if match:
            raise ValueError(f"assistant.{where} uses the forbidden word {match.group()!r}: {text!r}")


def _below_min_wage(cfg: TownConfig) -> str | None:
    """Why the config breaks its own minimum wage, or None if every firm pays at least it."""
    low = [f"{f.id} ({f.wage})" for f in cfg.firms if f.wage < cfg.laws.min_wage]
    if not low:
        return None
    return f"firm wage below laws.min_wage ({cfg.laws.min_wage}): {', '.join(low)}"


def _person_ids(count: int) -> list[str]:
    return [f"p{i:02d}" for i in range(1, count + 1)]


def read_config_dict(path: str) -> dict:
    """Parse the town file at `path` and attach `personas.yaml` from the same directory."""
    town_path = Path(path)
    town = _read_yaml(town_path)
    if not isinstance(town, dict):
        raise ConfigError(f"{town_path}: expected a mapping at the top level")
    personas = _read_yaml(town_path.parent / "personas.yaml")
    if not isinstance(personas, list):
        raise ConfigError(f"{town_path.parent / 'personas.yaml'}: expected a list of personas")
    town["personas"] = personas
    return town


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text()
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"malformed YAML in {path}: {e}") from e


def load_config(path: str) -> TownConfig:
    """Load and validate the town file at `path` and its sibling `personas.yaml`."""
    return TownConfig.model_validate(read_config_dict(path))


def build_world(cfg: TownConfig) -> World:
    """Build the starting world.

    Ids are handed out in order: one owner per firm, then each firm's staff, then
    auditors, then journalists; everyone left over is unemployed. Personas follow
    the same order.
    """
    below = _below_min_wage(cfg)  # a config changed after validation is checked again here
    if below:
        raise ConfigError(below)
    ids = _person_ids(cfg.people.count)
    slots: list[tuple[Role, str | None]] = []
    for f in cfg.firms:
        slots.append(("owner", f.id))
    for f in cfg.firms:
        slots += [("worker", f.id)] * f.staff
    slots += [("auditor", None)] * cfg.people.auditors
    slots += [("journalist", None)] * cfg.people.journalists
    if len(slots) > len(ids):
        raise ConfigError(f"roles need {len(slots)} people but people.count is {len(ids)}")
    slots += [("unemployed", None)] * (len(ids) - len(slots))

    a = cfg.assignment
    people: dict[str, Person] = {}
    for pid, (role, employer_id), persona in zip(ids, slots, cfg.personas, strict=False):
        people[pid] = Person(
            id=pid,
            name=persona.name,
            persona=persona.persona,
            role=role,
            cash=cfg.people.start_cash,
            employer_id=employer_id,
            ambition=persona.ambition,
            scruples=persona.scruples,
            decider=a.by_person.get(pid, a.by_role.get(role, a.default)),
        )
    if cfg.assistant is not None:
        for pid, instruction in cfg.assistant.clients.items():
            people[pid].assistant = Assistant(
                instruction=instruction.strip(), decider=cfg.assistant.decider
            )

    firms: dict[str, Firm] = {}
    for f in cfg.firms:
        firms[f.id] = Firm(
            id=f.id,
            name=f.name,
            market=f.market,
            owner_id=next(p.id for p in people.values() if p.role == "owner" and p.employer_id == f.id),
            cash=f.cash,
            price=f.price,
            wage=f.wage,
            quality=f.quality,
            employee_ids=[p.id for p in people.values() if p.role == "worker" and p.employer_id == f.id],
        )

    auditors = [p.id for p in people.values() if p.role == "auditor"]
    budget = int(cfg.params.get("audit_budget_per_quarter", 0))
    return World(
        people=people,
        firms=firms,
        treasury=Treasury(cash=cfg.treasury_cash),
        newspaper=Newspaper(
            cash=cfg.newspaper_cash,
            journalist_ids=[p.id for p in people.values() if p.role == "journalist"],
        ),
        regulator=Regulator(
            auditor_id=auditors[0] if auditors else "",
            budget_per_quarter=budget,
            budget_left=budget,
        ),
        laws=Laws(
            tax_rate=cfg.laws.tax_rate,
            penalty_multiplier=cfg.laws.penalty_multiplier,
            benefit=cfg.laws.benefit,
            illegal=set(cfg.laws.illegal),
            min_wage=cfg.laws.min_wage,
        ),
        params=dict(cfg.params),
        **_assistant_settings(cfg),
    )


def _assistant_settings(cfg: TownConfig) -> dict:
    if cfg.assistant is None:
        return {}
    a = cfg.assistant
    return {
        "assistant_name": a.name,
        "assistant_provider": a.provider,
        "assistant_max_rounds": a.max_rounds,
        "assistant_max_actions": a.max_actions,
    }
