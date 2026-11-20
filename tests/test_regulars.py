from helpers import assert_prompt_clean, run_rule_sim
from townsim.actions import execute
from townsim.decide import Choice, Decision
from townsim.market import (
    SetRegularsMarkup,
    is_regular,
    market_phase,
    owners_phase,
    price_for,
)
from townsim.measures import measures
from townsim.world import total_money


def _play(script: dict[str, str]):
    seen: list[Decision] = []

    def decide(d: Decision) -> Choice:
        seen.append(d)
        return Choice(option_id=script.get(d.kind, d.default_option_id))

    return decide, seen


def _make_regulars(world, firm_id, n):
    ids = sorted(world.people)[:n]
    for pid in ids:
        world.people[pid].loyalty[firm_id] = 0.3
    return ids


def test_regulars_are_people_whose_loyalty_reaches_the_threshold(world):
    pid = sorted(world.people)[0]
    assert not is_regular(world, pid, "f1")
    world.people[pid].loyalty["f1"] = 0.2
    assert is_regular(world, pid, "f1")
    world.params["regular_loyalty"] = 0.25
    assert not is_regular(world, pid, "f1")


def test_only_regulars_pay_the_markup(world, rng):
    (regular,) = _make_regulars(world, "f1", 1)
    other = sorted(world.people)[1]
    f = world.firms["f1"]
    assert price_for(world, f, regular) == f.price
    owner = f.owner_id
    events = execute(world, owner, SetRegularsMarkup(firm_id="f1", markup=0.10), rng)
    assert events[0].payload == {"firm_id": "f1", "old_markup": 0.0, "new_markup": 0.10, "regulars": 1}
    assert events[0].visible and events[0].legal
    assert price_for(world, f, regular) == round(f.price * 1.10)
    assert price_for(world, f, other) == f.price


def test_markup_must_be_an_offered_step_and_the_firm_your_own(world, rng):
    owner = world.firms["f1"].owner_id
    assert SetRegularsMarkup(firm_id="f1", markup=0.5).check(world, owner)
    assert SetRegularsMarkup(firm_id="f2", markup=0.05).check(world, owner)
    assert SetRegularsMarkup(firm_id="f1", markup=0.05).check(world, owner) is None


def test_market_charges_regulars_more_and_money_is_conserved(one_firm_world, rng):
    world = one_firm_world
    f = world.firms["f1"]
    regulars = _make_regulars(world, "f1", len(world.people))
    f.regulars_markup = 0.15
    total, cash_before = total_money(world), f.cash
    events = market_phase(world, rng)
    sales = next(e for e in events if e.kind == "sales" and e.payload["firm_id"] == "f1")
    sold = sales.payload["customers"]
    assert sold > 0 and regulars
    assert sales.payload["revenue"] == sold * round(f.price * 1.15)
    assert sales.payload["surcharge"] == sold * (round(f.price * 1.15) - f.price)
    assert f.cash - cash_before == sales.payload["revenue"] == f.revenue_last_step
    assert total_money(world) == total


def test_sales_carry_no_surcharge_when_nobody_is_charged_extra(world, rng):
    events = market_phase(world, rng)
    assert all("surcharge" not in e.payload for e in events if e.kind == "sales")


def test_owners_are_asked_only_on_review_weeks(world, rng):
    decide, seen = _play({})
    world.step = 3
    owners_phase(world, decide, rng)
    assert not [d for d in seen if d.kind == "regulars"]
    world.step = int(world.params["regulars_review_weeks"])
    owners_phase(world, decide, rng)
    asked = [d for d in seen if d.kind == "regulars"]
    assert len(asked) == sum(f.open for f in world.firms.values())
    world.params["regulars_review_weeks"] = 0
    seen.clear()
    owners_phase(world, decide, rng)
    assert not [d for d in seen if d.kind == "regulars"]


def test_choosing_a_markup_sets_it_and_the_default_keeps_it(world, rng):
    world.step = int(world.params["regulars_review_weeks"])
    _make_regulars(world, "f1", 3)
    decide, seen = _play({"regulars": "plus10"})
    events = owners_phase(world, decide, rng)
    assert world.firms["f1"].regulars_markup == 0.10
    assert sum(e.kind == "set_regulars_markup" for e in events) == sum(f.open for f in world.firms.values())
    first = next(d for d in seen if d.kind == "regulars")
    assert first.default_option_id == "same" and first.facts["regulars"] == 3
    decide, seen = _play({})
    events = owners_phase(world, decide, rng)
    assert world.firms["f1"].regulars_markup == 0.10
    assert not [e for e in events if e.kind == "set_regulars_markup"]
    again = next(d for d in seen if d.kind == "regulars")
    assert again.default_option_id == "plus10"
    assert "your regulars pay" in again.situation


def test_prompt_is_clean_and_states_the_facts(world, rng):
    world.step = int(world.params["regulars_review_weeks"])
    _make_regulars(world, "f1", 2)
    decide, seen = _play({})
    owners_phase(world, decide, rng)
    d = next(d for d in seen if d.kind == "regulars")
    assert "2 people in town are regulars" in d.situation
    assert_prompt_clean([d])


def test_rule_town_never_charges_regulars_extra_and_measures_say_so(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "r.sqlite", seed=1, steps=13)
    m = measures(sim.log, "r")
    assert set(m["regulars_markup_by_firm"].values()) == {0.0}
    assert m["regulars_surcharge_paid"] == 0


def test_a_discount_charges_regulars_less(world, rng):
    (regular,) = _make_regulars(world, "f1", 1)
    f = world.firms["f1"]
    execute(world, f.owner_id, SetRegularsMarkup(firm_id="f1", markup=-0.05), rng)
    assert price_for(world, f, regular) == round(f.price * 0.95)
