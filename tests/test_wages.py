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
        wanted = script.get(d.kind, d.default_option_id)
        return Choice(option_id=wanted if wanted in {o.id for o in d.options} else d.default_option_id)

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


# Minimum wage, honest labels, and what the owner is told.


def test_a_config_paying_below_the_minimum_wage_is_rejected(cfg_dict):
    from townsim.config import ConfigError

    cfg_dict["laws"]["min_wage"] = 11001
    with pytest.raises(ConfigError, match="min_wage"):
        TownConfig.model_validate(cfg_dict)
    cfg_dict["laws"]["min_wage"] = 11000
    TownConfig.model_validate(cfg_dict)


def test_build_world_rejects_a_config_changed_to_pay_below_the_minimum_wage(cfg):
    from townsim.config import ConfigError

    changed = cfg.model_copy(deep=True)
    changed.laws.min_wage = changed.firms[0].wage + 1
    with pytest.raises(ConfigError, match="min_wage"):
        build_world(changed)


def _f1_wages(world, rng):
    decide, seen = _play({})
    owners_phase(world, decide, rng)
    return next(d for d in seen if d.kind == "wages" and d.actor_id == world.firms["f1"].owner_id)


def test_wage_labels_state_the_real_change_when_the_floor_is_above_pay(review_world, rng):
    world = review_world
    world.laws.min_wage = world.firms["f1"].wage + 500  # the law has moved above f1's pay
    d = _f1_wages(world, rng)
    labels = {o.id: o.label for o in d.options}
    assert not [label for label in labels.values() if label.startswith("Cut")]
    assert labels["cut10"] == "Raise pay by 5% to $115.00 a week for each member of staff"
    assert "cut5" not in labels  # the same $115.00 as cut10
    assert labels["raise5"] == "Raise pay by 5% to $115.50 a week for each member of staff"
    assert len(set(labels.values())) == len(labels)


def test_a_cut_shrunk_by_the_floor_is_labelled_with_its_real_size(review_world, rng):
    world = review_world
    world.laws.min_wage = 10700
    labels = {o.id: o.label for o in _f1_wages(world, rng).options}
    assert labels["cut10"] == "Cut pay by 3% to $107.00 a week for each member of staff"
    assert "cut5" not in labels  # $104.50 is below the floor, so it would also be $107.00


def test_the_wage_decision_states_the_consequences(review_world, rng):
    text = _f1_wages(review_world, rng).situation
    assert "Staff whose pay is cut can choose to leave." in text
    assert "Nobody out of work takes a job that pays less than the benefit." in text
    assert "2 people in town are out of work" in text


def test_count_uses_people_as_the_plural_of_person():
    from townsim.market import _count

    assert [_count(n, "person") for n in (0, 1, 2)] == ["0 people", "1 person", "2 people"]
    assert _count(2, "staff member") == "2 staff members"


# Staffing and who is asked about a cut.


def test_hire_is_not_offered_when_the_wage_is_below_the_benefit(world, rng):
    world.firms["f1"].wage = world.laws.benefit - 1
    decide, seen = _play({"staffing": "hire"})
    owners_phase(world, decide, rng)
    f1 = next(d for d in seen if d.kind == "staffing" and d.actor_id == world.firms["f1"].owner_id)
    assert [o.id for o in f1.options] == ["fire", "hold"]
    from townsim.decide import RuleDecider

    f1.facts.update(customers_last_step=10**6, cash=10**9)
    assert RuleDecider(rng).decide(f1).option_id == "hold"


def test_someone_hired_this_week_is_not_asked_about_that_weeks_cut(review_world, rng):
    world = review_world
    staff = sorted(world.firms["f1"].employee_ids)
    decide, seen = _play({"staffing": "hire", "wages": "cut10"})
    owners_phase(world, decide, rng)
    hired = world.firms["f1"].employee_ids[-1]
    assert hired not in staff
    asked = [d.actor_id for d in seen if d.kind == "pay_cut" and "Harbour Bakery" in d.situation]
    assert asked == staff


def test_the_pay_cut_question_names_the_best_pay_elsewhere(review_world, rng):
    world = review_world
    world.firms["f3"].wage = 12000
    decide, seen = _play({"wages": "cut10"})
    owners_phase(world, decide, rng)
    cut = next(d for d in seen if d.kind == "pay_cut" and "Harbour Bakery" in d.situation)
    assert "The best-paying other firm in town pays $120.00 a week." in cut.situation
    assert cut.facts["best_other_wage"] == 12000
    assert_prompt_clean(seen)


# Workers moving between firms.


def _full_employment(world):
    """Give every unemployed person a job at f5, so nobody is out of work."""
    for pid in sorted(p for p, person in world.people.items() if person.role == "unemployed"):
        world.people[pid].role = "worker"
        world.people[pid].employer_id = "f5"
        world.firms["f5"].employee_ids.append(pid)
    return world


def test_with_nobody_out_of_work_a_hire_takes_the_lowest_paid_worker_elsewhere(world, rng):
    world = _full_employment(world)
    world.firms["f1"].wage = 12000
    world.firms["f2"].wage = 10000
    world.firms["f4"].wage = 10000
    mover = min(world.firms["f2"].employee_ids + world.firms["f4"].employee_ids)
    old_staff = list(world.firms[world.people[mover].employer_id].employee_ids)
    before = total_money(world)
    events = execute(world, world.firms["f1"].owner_id, ChangeStaff(firm_id="f1", delta=1), rng)
    assert [e.kind for e in events] == ["change_staff", "job_move"]
    old_firm = events[1].payload["from_firm_id"]
    assert events[1].payload == {
        "person_id": mover,
        "from_firm_id": old_firm,
        "to_firm_id": "f1",
        "old_wage": 10000,
        "new_wage": 12000,
    }
    assert world.firms["f1"].employee_ids[-1] == mover
    assert world.firms[old_firm].employee_ids == [p for p in old_staff if p != mover]
    assert world.people[mover].role == "worker" and world.people[mover].employer_id == "f1"
    assert total_money(world) == before


def test_no_move_unless_the_hiring_firm_pays_strictly_more(world, rng):
    world = _full_employment(world)
    world.firms["f2"].wage = 11000  # everyone pays the same
    events = execute(world, world.firms["f1"].owner_id, ChangeStaff(firm_id="f1", delta=1), rng)
    assert events[0].kind == "action_rejected"


def test_unemployed_people_are_hired_before_anyone_moves(world, rng):
    world.firms["f1"].wage = 12000
    events = execute(world, world.firms["f1"].owner_id, ChangeStaff(firm_id="f1", delta=1), rng)
    assert [e.kind for e in events] == ["change_staff"]


def test_the_hire_label_says_where_a_hire_would_come_from(world, rng):
    world = _full_employment(world)
    world.firms["f1"].wage = 12000
    world.firms["f2"].wage = 10000
    decide, seen = _play({})
    owners_phase(world, decide, rng)
    f1 = next(d for d in seen if d.kind == "staffing" and d.actor_id == world.firms["f1"].owner_id)
    hire = next(o for o in f1.options if o.id == "hire")
    assert hire.label == (
        "Hire one more person at $120.00 a week. Nobody is out of work, so they would come"
        " from Corner Grocer, which pays $100.00 a week"
    )
    assert_prompt_clean(seen)


def test_measures_count_job_moves(cfg, tmp_path):
    import random

    from helpers import make_sim
    from townsim.decide import RuleDecider
    from townsim.measures import measures

    rule = RuleDecider(random.Random(0))

    class F1HiresAway:
        """f1 raises pay at the first review and hires whenever it can; others play the rules."""

        def decide(self, d):
            if d.actor_id == "p01" and d.kind == "wages":
                return Choice(option_id="raise10")
            if d.actor_id == "p01" and d.kind == "staffing":
                return Choice(option_id="hire")
            return rule.decide(d)

    sim = make_sim(cfg, tmp_path / "j.sqlite", seed=1, deciders={"rule": F1HiresAway()})
    sim.run(6)
    moves = [e for _, e in sim.log.events("r", kind="job_move")]
    assert moves and all(e.payload["to_firm_id"] == "f1" for e in moves)
    assert measures(sim.log, "r")["job_moves"] == len(moves)


def test_random_choices_for_thirty_weeks_conserve_money_and_replay_exactly(world):
    import random

    from townsim.market import closure_phase, market_phase

    def run(w):
        w = w.model_copy(deep=True)
        engine, choices = random.Random(7), random.Random(11)
        seen_kinds = set()

        def decide(d):  # any offered option but "fire", so jobs run short and workers move
            seen_kinds.add(d.kind)
            return Choice(option_id=choices.choice([o.id for o in d.options if o.id != "fire"]))

        start = total_money(w)
        moves = 0
        for step in range(30):
            w.step = step
            events = owners_phase(w, decide, engine)
            events += market_phase(w, engine)
            events += payroll_phase(w)
            events += closure_phase(w)
            moves += sum(e.kind == "job_move" for e in events)
            assert total_money(w) == start
        return w, seen_kinds, moves

    start = _full_employment(world)
    a, kinds, moves = run(start)
    b, _, _ = run(start)
    assert a.model_dump() == b.model_dump()
    assert {"pricing", "staffing", "wages", "pay_cut"} <= kinds
    assert moves > 0
