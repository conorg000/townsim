import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from townsim.config import ConfigError, TownConfig, build_world, load_config
from townsim.world import total_money

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def test_build_world_has_thirty_people_in_expected_roles(cfg):
    w = build_world(cfg)
    roles = Counter(p.role for p in w.people.values())
    assert roles == {"owner": 5, "worker": 20, "auditor": 1, "journalist": 2, "unemployed": 2}
    assert all(len(f.employee_ids) == 4 for f in w.firms.values())


def test_unknown_decider_key_is_a_config_error(cfg_dict):
    cfg_dict["assignment"]["default"] = "missing"
    with pytest.raises(ConfigError, match="missing"):
        TownConfig.model_validate(cfg_dict)


def test_more_roles_than_people_is_a_config_error(cfg_dict):
    cfg_dict["people"]["count"] = 10
    with pytest.raises(ConfigError):
        build_world(TownConfig.model_validate(cfg_dict))


def test_assignment_precedence_person_over_role_over_default(cfg_dict):
    cfg_dict["deciders"]["m"] = {"type": "model", "model": "x/y", "temperature": 0.7}
    cfg_dict["assignment"] = {"default": "rule", "by_role": {"owner": "m"}, "by_person": {"p01": "rule"}}
    w = build_world(TownConfig.model_validate(cfg_dict))
    assert w.people["p01"].decider == "rule" and w.people["p02"].decider == "m"


def test_people_ids_roles_and_employers_follow_assignment_order(world):
    assert list(world.people) == [f"p{i:02d}" for i in range(1, 31)]
    owners = [p for p in world.people.values() if p.role == "owner"]
    assert [p.id for p in owners] == ["p01", "p02", "p03", "p04", "p05"]
    for firm in world.firms.values():
        owner = world.people[firm.owner_id]
        assert owner.employer_id == firm.id
        assert all(world.people[e].role == "worker" for e in firm.employee_ids)
        assert all(world.people[e].employer_id == firm.id for e in firm.employee_ids)
    assert world.firms["f1"].employee_ids == ["p06", "p07", "p08", "p09"]
    assert world.regulator.auditor_id == "p26"
    assert world.newspaper.journalist_ids == ["p27", "p28"]
    assert {world.people[i].role for i in ("p29", "p30")} == {"unemployed"}
    assert all(world.people[i].employer_id is None for i in ("p26", "p27", "p28", "p29", "p30"))


def test_world_carries_config_values(cfg, world):
    f1 = world.firms["f1"]
    assert (f1.name, f1.market, f1.quality, f1.price, f1.wage, f1.cash) == (
        "Harbour Bakery", "food", 0.6, 5000, 11000, 400000,
    )
    assert all(p.cash == 100000 for p in world.people.values())
    assert world.treasury.cash == 1000000 and world.newspaper.cash == 200000
    assert world.laws.illegal == {"underreport_tax", "offer_bribe", "accept_bribe"}
    assert world.regulator.budget_per_quarter == world.regulator.budget_left == 6
    assert world.params == cfg.params
    assert total_money(world) == 30 * 100000 + 5 * 400000 + 1000000 + 200000


def test_personas_are_assigned_in_file_order(cfg, world):
    for person, persona in zip(world.people.values(), cfg.personas, strict=False):
        assert (person.name, person.persona) == (persona.name, persona.persona)
        assert (person.ambition, person.scruples) == (persona.ambition, persona.scruples)


def test_build_world_is_deterministic(cfg):
    assert build_world(cfg).model_dump() == build_world(cfg).model_dump()


def test_unknown_decider_type_is_a_config_error(cfg_dict):
    cfg_dict["deciders"]["odd"] = {"type": "oracle"}
    with pytest.raises(ConfigError):
        TownConfig.model_validate(cfg_dict)


def test_unknown_decider_in_by_role_or_by_person_is_a_config_error(cfg_dict):
    cfg_dict["assignment"]["by_role"] = {"worker": "nope_role"}
    with pytest.raises(ConfigError, match="nope_role"):
        TownConfig.model_validate(cfg_dict)
    cfg_dict["assignment"]["by_role"] = {}
    cfg_dict["assignment"]["by_person"] = {"p03": "nope_person"}
    with pytest.raises(ConfigError, match="nope_person"):
        TownConfig.model_validate(cfg_dict)


def test_fewer_personas_than_people_is_a_config_error(cfg_dict):
    cfg_dict["personas"] = cfg_dict["personas"][:29]
    with pytest.raises(ConfigError, match="personas"):
        TownConfig.model_validate(cfg_dict)


def test_load_config_reads_town_and_personas(cfg):
    loaded = load_config(str(CONFIG_DIR / "town.yaml"))
    assert loaded == cfg
    assert len(loaded.personas) == 30


def test_load_config_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "town.yaml"))


def test_load_config_malformed_yaml_is_a_config_error(tmp_path):
    (tmp_path / "town.yaml").write_text("firms: [unclosed\n")
    (tmp_path / "personas.yaml").write_text((CONFIG_DIR / "personas.yaml").read_text())
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "town.yaml"))


def test_load_config_missing_personas_file_is_a_config_error(tmp_path):
    (tmp_path / "town.yaml").write_text((CONFIG_DIR / "town.yaml").read_text())
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "town.yaml"))


def test_load_config_invalid_values_are_a_config_error(tmp_path):
    town = yaml.safe_load((CONFIG_DIR / "town.yaml").read_text())
    town["assignment"]["default"] = "missing"
    (tmp_path / "town.yaml").write_text(yaml.safe_dump(town))
    (tmp_path / "personas.yaml").write_text((CONFIG_DIR / "personas.yaml").read_text())
    with pytest.raises(ConfigError, match="missing"):
        load_config(str(tmp_path / "town.yaml"))


def test_personas_file_has_thirty_distinct_spread_entries(cfg):
    personas = cfg.personas
    assert len(personas) == 30
    assert len({p.name for p in personas}) == 30
    assert len({p.persona for p in personas}) == 30
    for attr in ("ambition", "scruples"):
        values = [getattr(p, attr) for p in personas]
        assert min(values) == pytest.approx(0.1) and max(values) == pytest.approx(0.9)
        assert all(0.1 <= v <= 0.9 for v in values)
    for p in personas:
        assert len(re.findall(r"[.!?](?:\s|$)", p.persona)) == 2, p.persona
        assert not re.search(r"simulat|game|test|experiment", p.persona, re.IGNORECASE), p.persona


# Words tying a person to a trade, workplace or role. Roles and employers change during
# a run and are given to deciders separately, so persona text must not fix them.
# Prefixes match any word starting with them; words match whole words only.
TRADE_AND_ROLE_PREFIXES = [
    "bak", "bread", "loa", "grocer", "deli", "shop", "store", "shel", "repair", "fix",
    "plumb", "electric", "boiler", "handym", "tool", "trade", "carer", "nurs", "client",
    "customer", "owner", "boss", "employ", "worker", "staff", "shift", "wage", "salar",
    "job", "unemploy", "redundan", "firm", "business", "compan", "audit", "inspect",
    "council", "regulat", "journalis", "report", "newspaper", "paper", "print", "stor",
]
TRADE_AND_ROLE_WORDS = ["care", "caring", "counter", "owns", "hired", "fired", "books", "accounts", "press"]


def test_personas_do_not_name_a_firm_trade_or_role(cfg):
    firm_words = {w.lower() for f in cfg.firms for w in re.findall(r"[A-Za-z]+", f.name)}
    firm_words |= {f.market for f in cfg.firms}
    patterns = [rf"\b{re.escape(w)}\b" for w in sorted(firm_words | set(TRADE_AND_ROLE_WORDS))]
    patterns += [rf"\b{re.escape(w)}" for w in TRADE_AND_ROLE_PREFIXES]
    for p in cfg.personas:
        for pat in patterns:
            assert not re.search(pat, p.persona, re.IGNORECASE), (p.name, pat, p.persona)
