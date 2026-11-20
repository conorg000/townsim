import pytest

from helpers import assert_prompt_clean, run_rule_sim
from townsim.actions import execute
from townsim.config import TownConfig, build_world
from townsim.decide import Choice, Decision
from townsim.market import ChangeStaff, QuitJob, SetWage, owners_phase, payroll_phase
from townsim.world import total_money


def _play(script: dict[str, str]):
    """A decide function that records each decision and takes the scripted option per kind."""
    seen: list[Decision] = []

    def decide(d: Decision) -> Choice:
        seen.append(d)
        return Choice(option_id=script.get(d.kind, d.default_option_id))

    return decide, seen


@pytest.fixture
def review_world(world):
    world.step = int(world.params["wage_review_weeks"])
    return world


def test_wages_are_only_reviewed_on_review_weeks(world, rng):
    decide, seen = _play({})
    world.step = 0
    owners_phase(world, decide, rng)
    world.step = int(world.params["wage_review_weeks"]) + 1
    owners_phase(world, decide, rng)
    assert not [d for d in seen if d.kind == "wages"]
    world.step = int(world.params["wage_review_weeks"])
    owners_phase(world, decide, rng)
    assert len([d for d in seen if d.kind == "wages"]) == sum(f.open for f in world.firms.values())


def test_no_review_when_the_town_sets_no_review_weeks(world, rng):
    world.params["wage_review_weeks"] = 0
    world.params["regulars_review_weeks"] = 0
    world.step = 4
    decide, seen = _play({})
    owners_phase(world, decide, rng)
    assert {d.kind for d in seen} == {"pricing", "staffing"}


def test_a_cut_lowers_what_payroll_pays_each_member_of_staff(review_world, rng):
    world = review_world
    old = world.firms["f1"].wage
    pid = world.firms["f1"].employee_ids[0]
    decide, _ = _play({"wages": "cut10"})
    events = owners_phase(world, decide, rng)
    new = round(old * 0.9)
    assert world.firms["f1"].wage == new
    cut = next(e for e in events if e.kind == "set_wage" and e.payload["firm_id"] == "f1")
    assert cut.payload == {"firm_id": "f1", "old_wage": old, "new_wage": new, "staff": 4}
    assert cut.visible and cut.legal
    before, total = world.people[pid].cash, total_money(world)
    payroll_phase(world)
    assert world.people[pid].cash - before >= new - int(world.params["subscription"])
    assert world.people[pid].cash - before < old
    assert total_money(world) == total


def test_a_raise_and_a_hold(review_world, rng):
    world = review_world
    old = world.firms["f1"].wage
    owners_phase(world, _play({"wages": "raise5"})[0], rng)
    assert world.firms["f1"].wage == round(old * 1.05)
    events = owners_phase(world, _play({})[0], rng)
    assert not [e for e in events if e.kind == "set_wage"]


def test_staff_whose_pay_is_cut_are_each_asked_and_may_leave(review_world, rng):
    world = review_world
    staff = sorted(world.firms["f1"].employee_ids)
    decide, seen = _play({"wages": "cut10", "pay_cut": "quit"})
    events = owners_phase(world, decide, rng)
    asked = [d.actor_id for d in seen if d.kind == "pay_cut" and "Harbour Bakery" in d.situation]
    assert asked == staff
    assert world.firms["f1"].employee_ids == []
    assert all(world.people[p].role == "unemployed" and world.people[p].employer_id is None for p in staff)
    assert len([e for e in events if e.kind == "quit_job" and e.payload["firm_id"] == "f1"]) == 4


def test_nobody_is_asked_about_a_raise_or_a_hold(review_world, rng):
    decide, seen = _play({"wages": "raise10"})
    owners_phase(review_world, decide, rng)
    assert not [d for d in seen if d.kind == "pay_cut"]


def test_the_minimum_wage_is_a_floor_and_options_at_the_floor_are_not_offered(review_world, rng):
    world = review_world
    world.laws.min_wage = world.firms["f1"].wage
    decide, seen = _play({})
    owners_phase(world, decide, rng)
    f1 = next(d for d in seen if d.kind == "wages" and d.actor_id == world.firms["f1"].owner_id)
    assert [o.id for o in f1.options] == ["hold", "raise5", "raise10"]
    assert "minimum wage" in f1.situation
    events = execute(world, world.firms["f1"].owner_id, SetWage(firm_id="f1", factor=0.5), rng)
    assert events[0].payload["new_wage"] == world.laws.min_wage


def test_only_the_owner_can_set_the_wage_and_only_a_worker_can_quit(world, rng):
    worker = world.firms["f1"].employee_ids[0]
    owner = world.firms["f1"].owner_id
    assert execute(world, worker, SetWage(firm_id="f1", factor=0.9), rng)[0].kind == "action_rejected"
    assert execute(world, owner, QuitJob(), rng)[0].kind == "action_rejected"


def test_nobody_takes_a_job_that_pays_less_than_the_benefit(world, rng):
    world.firms["f1"].wage = world.laws.benefit - 1
    events = execute(world, world.firms["f1"].owner_id, ChangeStaff(firm_id="f1", delta=1), rng)
    assert events[0].kind == "action_rejected" and "benefit" in events[0].payload["reason"]


def test_by_rule_staff_stay_unless_pay_falls_below_the_benefit(review_world, rng):
    from townsim.decide import RuleDecider

    world = review_world
    rule = RuleDecider(rng)

    def decide(d):
        if d.kind == "pay_cut":
            return rule.decide(d)
        return Choice(option_id="cut10" if d.kind == "wages" else d.default_option_id)

    owners_phase(world, decide, rng)
    assert len(world.firms["f1"].employee_ids) == 4
    world.firms["f1"].wage = world.laws.benefit + 100
    owners_phase(world, decide, rng)
    assert world.firms["f1"].employee_ids == []


def test_wage_and_pay_cut_prompts_are_clean_and_state_the_alternatives(review_world, rng):
    decide, seen = _play({"wages": "cut5"})
    owners_phase(review_world, decide, rng)
    assert_prompt_clean(seen)
    wages = next(d for d in seen if d.kind == "wages")
    assert "out of work" in wages.situation and "$60.00 a week" in wages.situation
    assert "$110.00 to each member of staff" in wages.situation
    cut = next(d for d in seen if d.kind == "pay_cut")
    assert "from $110.00 to $104.50 a week" in cut.situation


def test_the_rule_town_never_changes_wages(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "r.sqlite", seed=1, steps=13)
    assert not [e for _, e in sim.log.events("r", kind="set_wage")]
    assert {f.wage for f in sim.world.firms.values()} == {11000}
    kinds = {e.payload["decision_kind"] for _, e in sim.log.events("r", kind="decision")}
    assert "wages" in kinds


def test_measures_report_wages_cuts_quits_and_cash_by_role(cfg, tmp_path):
    from helpers import make_sim
    from townsim.decide import RuleDecider
    from townsim.measures import measures
    import random

    rule = RuleDecider(random.Random(0))

    class CutsOnce:
        def decide(self, d):
            if d.kind == "wages" and d.id.startswith("4:p01:"):
                return Choice(option_id="cut10")
            if d.kind == "pay_cut" and d.actor_id == "p06":
                return Choice(option_id="quit")
            return rule.decide(d)

    sim = make_sim(cfg, tmp_path / "m.sqlite", seed=1, deciders={"rule": CutsOnce()})
    sim.run(6)
    m = measures(sim.log, "r")
    assert m["wage_cuts"] == 1 and m["wage_raises"] == 0 and m["quits"] == 1
    assert m["wage_by_firm"]["f1"] == 9900 and m["wage_by_firm"]["f2"] == 11000
    assert set(m["median_cash_by_role"]) >= {"owner", "worker"}
