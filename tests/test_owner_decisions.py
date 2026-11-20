import random

import pytest

from helpers import assert_prompt_clean, scripted
from townsim.actions import action_from_dict, execute
from townsim.decide import RULES, Choice, Decision, RuleDecider
from townsim.events import state_hash
from townsim.market import ChangeStaff, SetPrice, close_firm, owners_phase
from townsim.world import World, total_money


def _recorder(choose=None):
    """A decide function that records each decision and picks `choose(d)` or the default."""
    seen: list[Decision] = []

    def decide(d: Decision) -> Choice:
        seen.append(d)
        return Choice(option_id=choose(d) if choose else d.default_option_id)

    return decide, seen


def _unemployed(world: World) -> list[str]:
    return sorted(pid for pid, p in world.people.items() if p.role == "unemployed")


def _capacity(world: World, fid: str) -> int:
    return int(world.params["units_per_employee"]) * len(world.firms[fid].employee_ids)


# The brief's tests, with expected values derived from the world.


def test_owner_price_choice_changes_price(world, rng):
    old_price = world.firms["f1"].price
    owners_phase(world, scripted({"pricing": "up10", "staffing": "hold"}), rng)
    assert world.firms["f1"].price == max(100, round(old_price * 1.10))


def test_firing_with_no_staff_is_rejected_not_crashed(world, rng):
    world.firms["f1"].employee_ids = []
    evs = owners_phase(world, scripted({"pricing": "hold", "staffing": "fire"}), rng)
    assert any(e.kind == "action_rejected" and e.payload["action"] == "change_staff" for e in evs)


def test_hiring_moves_an_unemployed_person_into_the_firm(world, rng):
    staff_before = len(world.firms["f1"].employee_ids)
    owners_phase(world, scripted({"pricing": "hold", "staffing": "hire"}), rng)
    assert len(world.firms["f1"].employee_ids) == staff_before + 1


def test_situation_text_contains_no_forbidden_words(world, rng):
    seen = []
    owners_phase(world, lambda d: (seen.append(d), Choice(option_id=d.default_option_id))[1], rng)
    assert_prompt_clean(seen)


# Decisions offered.


def test_each_open_firm_owner_gets_pricing_then_staffing_in_firm_id_order(world, rng):
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    expected = []
    for fid in sorted(world.firms):
        owner = world.firms[fid].owner_id
        expected += [(owner, "pricing"), (owner, "staffing")]
    assert [(d.actor_id, d.kind) for d in seen] == expected


def test_decision_ids_follow_step_actor_kind_seq(world, rng):
    world.step = 7
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    assert [d.id for d in seen] == [f"7:{d.actor_id}:{d.kind}:0" for d in seen]
    assert len({d.id for d in seen}) == len(seen)


def test_pricing_and_staffing_options_and_defaults(world, rng):
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    pricing, staffing = seen[0], seen[1]
    assert [o.id for o in pricing.options] == ["down10", "down5", "hold", "up5", "up10"]
    assert pricing.default_option_id == "hold"
    assert [o.id for o in staffing.options] == ["hire", "fire", "hold"]
    assert staffing.default_option_id == "hold"


def test_option_actions_are_dicts_that_rebuild_into_actions(world, rng):
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    pricing, staffing = seen[0], seen[1]
    factors = {o.id: o.action for o in pricing.options}
    assert factors["hold"] is None
    for oid, factor in [("down10", 0.90), ("down5", 0.95), ("up5", 1.05), ("up10", 1.10)]:
        assert action_from_dict(factors[oid]) == SetPrice(firm_id="f1", factor=factor)
    deltas = {o.id: o.action for o in staffing.options}
    assert deltas["hold"] is None
    assert action_from_dict(deltas["hire"]) == ChangeStaff(firm_id="f1", delta=1)
    assert action_from_dict(deltas["fire"]) == ChangeStaff(firm_id="f1", delta=-1)


def test_facts_describe_the_owners_firm_and_market(world, rng):
    f = world.firms["f1"]
    f.customers_last_step = 3
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    competitors = [
        g.price for g in world.firms.values() if g.open and g.market == f.market and g.id != f.id
    ]
    expected = {
        "price": f.price,
        "mean_market_price": round(sum(competitors) / len(competitors)),
        "customers_last_step": 3,
        "capacity": _capacity(world, "f1"),
        "cash": f.cash,
        "weekly_wage_bill": f.wage * len(f.employee_ids),
            "wage": f.wage,
        "staff": len(f.employee_ids),
        "recent_weeks": [],
    }
    assert seen[0].facts == expected
    assert seen[1].facts == expected


def test_staffing_decision_sees_the_price_just_chosen(world, rng):
    old_price = world.firms["f1"].price
    decide, seen = _recorder(lambda d: "up10" if d.kind == "pricing" else "hold")
    owners_phase(world, decide, rng)
    assert seen[1].facts["price"] == max(100, round(old_price * 1.10))


def test_situation_states_the_firms_numbers_in_dollars(world, rng):
    f = world.firms["f1"]
    f.price, f.cash = 4567, 123456
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    situation = seen[0].situation
    assert "$45.67" in situation
    assert "$1,234.56" in situation
    assert f"${f.wage * len(f.employee_ids) / 100:,.2f}" in situation
    assert f.name in situation


def test_situation_with_no_competitors_says_so(world, rng):
    for fid, f in world.firms.items():
        if fid != "f1" and f.market == world.firms["f1"].market:
            close_firm(world, fid)
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    assert seen[0].facts["mean_market_price"] is None
    assert "no other open firm" in seen[0].situation.lower()


def test_closed_firms_are_skipped(world, rng):
    close_firm(world, "f2")
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    assert world.firms["f2"].owner_id not in {d.actor_id for d in seen}


def test_owner_whose_firm_closes_mid_phase_gets_no_further_decisions(world, rng):
    owner = world.firms["f1"].owner_id

    def decide(d: Decision) -> Choice:
        seen.append(d)
        if d.actor_id == owner and d.kind == "pricing":
            close_firm(world, "f1")
        return Choice(option_id=d.default_option_id)

    seen: list[Decision] = []
    owners_phase(world, decide, rng)
    assert [(d.actor_id, d.kind) for d in seen if d.actor_id == owner] == [(owner, "pricing")]


def test_choice_not_offered_is_an_error(world, rng):
    with pytest.raises(ValueError, match="nonsense"):
        owners_phase(world, lambda d: Choice(option_id="nonsense"), rng)


# Effects of the choices.


def test_hold_everywhere_changes_nothing(world, rng):
    before = state_hash(world)
    evs = owners_phase(world, scripted({}), rng)
    assert evs == []
    assert state_hash(world) == before


def test_price_has_a_floor_of_one_dollar(world, rng):
    world.firms["f1"].price = 105
    owners_phase(world, scripted({"pricing": "down10"}), rng)
    assert world.firms["f1"].price == 100


def test_set_price_event_reports_old_and_new_price(world, rng):
    old_price = world.firms["f1"].price
    evs = owners_phase(world, scripted({"pricing": "down5"}), rng)
    new_price = max(100, round(old_price * 0.95))
    first = next(e for e in evs if e.kind == "set_price")
    assert first.actor_id == world.firms["f1"].owner_id
    assert first.payload == {"firm_id": "f1", "old_price": old_price, "new_price": new_price}


def test_hired_person_was_unemployed_and_now_works_for_the_firm(world, rng):
    unemployed_before = _unemployed(world)
    owners_phase(world, scripted({"staffing": "hire"}), rng)
    hired = world.firms["f1"].employee_ids[-1]
    assert hired in unemployed_before
    assert world.people[hired].role == "worker"
    assert world.people[hired].employer_id == "f1"


def test_hiring_with_nobody_unemployed_is_rejected_and_changes_nothing(world, rng):
    for pid in _unemployed(world):
        world.people[pid].role = "journalist"
    before = state_hash(world)
    evs = owners_phase(world, scripted({"staffing": "hire"}), rng)
    rejected = [e for e in evs if e.kind == "action_rejected"]
    assert len(rejected) == len(world.firms)
    assert all(e.payload["action"] == "change_staff" and e.payload["reason"] for e in rejected)
    assert state_hash(world) == before


def test_firing_lets_the_most_recent_hire_go(world, rng):
    staff = list(world.firms["f1"].employee_ids)
    owners_phase(world, scripted({"staffing": "fire"}), rng)
    assert world.firms["f1"].employee_ids == staff[:-1]
    let_go = world.people[staff[-1]]
    assert let_go.role == "unemployed"
    assert let_go.employer_id is None


def test_fire_after_hire_lets_the_new_hire_go(world, rng):
    staff = list(world.firms["f1"].employee_ids)
    owner = world.firms["f1"].owner_id
    execute(world, owner, ChangeStaff(firm_id="f1", delta=1), rng)
    hired = world.firms["f1"].employee_ids[-1]
    execute(world, owner, ChangeStaff(firm_id="f1", delta=-1), rng)
    assert world.firms["f1"].employee_ids == staff
    assert world.people[hired].role == "unemployed"


def test_only_the_owner_of_an_open_firm_may_act_for_it(world, rng):
    other_owner = world.firms["f2"].owner_id
    for action in [SetPrice(firm_id="f1", factor=1.1), ChangeStaff(firm_id="f1", delta=1)]:
        (ev,) = execute(world, other_owner, action, rng)
        assert ev.kind == "action_rejected"
    owner = world.firms["f1"].owner_id
    close_firm(world, "f1")
    (ev,) = execute(world, owner, SetPrice(firm_id="f1", factor=1.1), rng)
    assert ev.kind == "action_rejected"


def test_change_staff_rejects_deltas_other_than_one_step(world, rng):
    owner = world.firms["f1"].owner_id
    (ev,) = execute(world, owner, ChangeStaff(firm_id="f1", delta=2), rng)
    assert ev.kind == "action_rejected"


def test_owners_phase_conserves_money_and_is_deterministic(world, rng):
    other = world.model_copy(deep=True)
    before = total_money(world)
    script = scripted({"pricing": "up5", "staffing": "hire"})
    owners_phase(world, script, rng)
    owners_phase(other, script, random.Random(0))
    assert total_money(world) == before
    assert state_hash(world) == state_hash(other)


# Baseline rules.


def _rule_choice(world: World, kind: str, rng: random.Random) -> str:
    decide, seen = _recorder()
    owners_phase(world.model_copy(deep=True), decide, rng)
    decision = next(d for d in seen if d.actor_id == world.firms["f1"].owner_id and d.kind == kind)
    return RuleDecider(rng).decide(decision).option_id


def test_pricing_and_staffing_rules_are_registered():
    assert "pricing" in RULES
    assert "staffing" in RULES


def _set_capacity_to_ten(world: World) -> None:
    world.params["units_per_employee"] = 5.0
    world.firms["f1"].employee_ids = world.firms["f1"].employee_ids[:2]


@pytest.mark.parametrize(
    "customers, expected",
    [(0, "down5"), (6, "down5"), (7, "hold"), (9, "hold"), (10, "up5"), (11, "up5")],
)
def test_pricing_rule(world, rng, customers, expected):
    _set_capacity_to_ten(world)
    world.firms["f1"].customers_last_step = customers
    assert _rule_choice(world, "pricing", rng) == expected


def test_pricing_rule_holds_for_a_firm_with_no_staff(world, rng):
    # With no capacity, 0 customers would count as "full" and ratchet the price up weekly.
    world.firms["f1"].employee_ids = []
    world.firms["f1"].customers_last_step = 0
    assert _rule_choice(world, "pricing", rng) == "hold"


def test_a_firm_with_no_staff_keeps_its_price_week_after_week(world, rng):
    f = world.firms["f1"]
    f.employee_ids = []
    price = f.price
    rules = RuleDecider(rng)

    def decide(d):  # the pricing rule decides; nobody is hired, so the firm stays unstaffed
        return Choice(option_id="hold") if d.kind == "staffing" else rules.decide(d)

    for _ in range(5):
        f.customers_last_step = 0
        owners_phase(world, decide, rng)
    assert world.firms["f1"].employee_ids == []
    assert world.firms["f1"].price == price


def test_staffing_rule_hires_when_full_and_cash_covers_eight_weeks_of_wages(world, rng):
    f = world.firms["f1"]
    f.customers_last_step = _capacity(world, "f1")
    bill = f.wage * len(f.employee_ids)
    f.cash = 8 * bill + 1
    assert _rule_choice(world, "staffing", rng) == "hire"
    f.cash = 8 * bill
    assert _rule_choice(world, "staffing", rng) == "hold"


def test_staffing_rule_fires_when_under_half_full_with_more_than_one_staff(world, rng):
    f = world.firms["f1"]
    cap = _capacity(world, "f1")
    f.customers_last_step = (cap - 1) // 2
    assert len(f.employee_ids) > 1
    assert _rule_choice(world, "staffing", rng) == "fire"
    f.employee_ids = f.employee_ids[:1]
    f.customers_last_step = 0
    assert _rule_choice(world, "staffing", rng) == "hold"


def test_staffing_rule_holds_in_between(world, rng):
    f = world.firms["f1"]
    cap = _capacity(world, "f1")
    f.customers_last_step = cap - 1
    f.cash = 10 * f.wage * len(f.employee_ids)
    assert _rule_choice(world, "staffing", rng) == "hold"


def test_owner_sees_the_firms_books_for_recent_weeks_most_recent_first(world, rng):
    from townsim.market import market_phase, payroll_phase

    for _ in range(6):
        market_phase(world, rng)
        payroll_phase(world)
    f = world.firms["f1"]
    assert len(f.recent_weeks) == 4
    price, customers, takings, wages = f.recent_weeks[-1]
    assert takings == price * customers and wages == f.wage * len(f.employee_ids)
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    text = seen[0].situation
    assert "Your books for recent weeks." in text
    assert text.index("Last week:") < text.index("The week before:") < text.index("Four weeks ago:")
    assert_prompt_clean(seen)


def test_a_week_short_of_wages_is_stated_as_a_shortfall(world, rng):
    f = world.firms["f1"]
    f.recent_weeks = [[1000, 2, 2000, 5000]]
    decide, seen = _recorder()
    owners_phase(world, decide, rng)
    assert "$30.00 short of wages" in seen[0].situation
