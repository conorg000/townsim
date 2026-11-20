import random

import pytest

from helpers import assert_prompt_clean, scripted
from townsim.actions import action_from_dict, execute
from townsim.decide import RULES, Choice, Decision, RuleDecider
from townsim.events import state_hash
from townsim.market import close_firm
from townsim.tax import Audit, FileTax, audit_phase, settle_penalty, tax_phase
from townsim.world import Info, World, total_money


def _recorder(choose=None):
    """A decide function that records each decision and picks `choose(d)` or the default."""
    seen: list[Decision] = []

    def decide(d: Decision) -> Choice:
        seen.append(d)
        return Choice(option_id=choose(d) if choose else d.default_option_id)

    return decide, seen


class _Draw(random.Random):
    """An rng whose `random()` always returns `value`."""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _quarter(w: World, profit: int = 100000, cash: int = 400000) -> World:
    """Put `w` at the end of the first quarter with f1 at `profit` and `cash`."""
    w.step = 13
    f1 = w.firms["f1"]
    f1.profit_qtd, f1.cash, f1.wage = profit, cash, 1000
    w.laws.tax_rate = 0.2
    w.laws.penalty_multiplier = 2.0
    w.params["dividend_share"] = 0.5
    w.params["reserve_weeks"] = 4.0
    return w


def _fraud(w: World) -> Info:
    return w.infos["info:13:tax_fraud:f1"]


# The brief's tests.


def test_honest_report_pays_full_tax(world, rng):
    world.step = 13; world.firms["f1"].profit_qtd = 100000
    world.laws.tax_rate = 0.2; world.firms["f1"].cash = 400000
    t = world.treasury.cash
    tax_phase(world, scripted({"tax_report": "honest"}), rng)
    assert world.treasury.cash - t >= 20000   # f1's 20% plus the other firms


def test_underreporting_hides_tax_creates_secret_and_is_illegal(one_firm_world, rng):
    w = one_firm_world; w.step = 13; w.firms["f1"].profit_qtd = 100000
    w.laws.tax_rate = 0.2; w.firms["f1"].cash = 400000
    evs = tax_phase(w, scripted({"tax_report": "hide50_heavy"}), rng)
    ev = next(e for e in evs if e.kind == "underreport_tax")
    assert ev.legal is False and ev.visible is False and ev.harm == {"treasury": 10000}
    info = next(i for i in w.infos.values() if i.kind == "tax_fraud")
    assert info.known_by == {w.firms["f1"].owner_id}


def test_underreporting_is_legal_when_the_law_says_so(one_firm_world, rng):
    w = one_firm_world; w.step = 13; w.firms["f1"].profit_qtd = 100000
    w.laws.illegal.discard("underreport_tax")
    evs = tax_phase(w, scripted({"tax_report": "hide25_none"}), rng)
    assert all(e.legal for e in evs)


def test_deep_audit_with_tip_always_detects_and_charges_penalty(fraud_world, rng):
    w = fraud_world                      # f1 hid 10000; with no concealment a tipped deep audit is certain
    _fraud(w).payload["concealment"] = "none"
    w.regulator.tips.append("f1")
    evs = audit_phase(w, scripted({"audit": "deep:f1"}), rng)
    assert any(e.kind == "audit_detected" for e in evs)
    assert w.regulator.budget_left == 3


def test_audit_option_list_respects_budget(fraud_world, rng):
    fraud_world.regulator.budget_left = 1
    seen = []
    audit_phase(fraud_world, lambda d: (seen.append(d), Choice(option_id="none"))[1], rng)
    assert not any(o.id.startswith("deep:") for o in seen[0].options)
    assert_prompt_clean(seen)


def test_penalty_larger_than_cash_takes_only_what_is_there(fraud_world):
    fraud_world.firms["f1"].cash = 50
    settle_penalty(fraud_world, "f1", 30000)
    assert fraud_world.firms["f1"].cash == 0


# When the tax phase runs.


@pytest.mark.parametrize("step", [0, 1, 12, 14, 25])
def test_tax_phase_runs_only_at_the_end_of_a_quarter(world, rng, step):
    world.step = step
    world.firms["f1"].profit_qtd = 100000
    before = state_hash(world)
    decide, seen = _recorder()
    assert tax_phase(world, decide, rng) == []
    assert seen == []
    assert state_hash(world) == before


@pytest.mark.parametrize("step", [13, 26, 104])
def test_tax_phase_runs_every_thirteenth_step(world, rng, step):
    world.step = step
    decide, seen = _recorder()
    tax_phase(world, decide, rng)
    assert len(seen) == len(world.firms)


# The tax_report decision.


def test_each_open_firm_owner_gets_one_tax_report_in_firm_id_order(world, rng):
    _quarter(world)
    close_firm(world, "f2")
    decide, seen = _recorder()
    tax_phase(world, decide, rng)
    expected = [world.firms[fid].owner_id for fid in sorted(world.firms) if fid != "f2"]
    assert [d.actor_id for d in seen] == expected
    assert all(d.kind == "tax_report" for d in seen)
    assert [d.id for d in seen] == [f"13:{d.actor_id}:tax_report:0" for d in seen]


def test_tax_report_options_default_and_actions(world, rng):
    _quarter(world)
    decide, seen = _recorder()
    tax_phase(world, decide, rng)
    d = seen[0]
    assert [o.id for o in d.options] == [
        "honest",
        "hide25_none", "hide25_some", "hide25_heavy",
        "hide50_none", "hide50_some", "hide50_heavy",
    ]
    assert d.default_option_id == "honest"
    actions = {o.id: action_from_dict(o.action) for o in d.options}
    assert actions["honest"] == FileTax(firm_id="f1", hide_share=0.0, concealment="none")
    assert actions["honest"].kind == "file_tax"
    assert actions["hide25_some"] == FileTax(firm_id="f1", hide_share=0.25, concealment="some")
    assert actions["hide50_heavy"] == FileTax(firm_id="f1", hide_share=0.5, concealment="heavy")
    assert actions["hide50_heavy"].kind == "underreport_tax"


def test_tax_report_facts(world, rng):
    _quarter(world, profit=100000, cash=250000)
    world.regulator.budget_per_quarter = 6
    owner = world.people[world.firms["f1"].owner_id]
    owner.scruples = 0.35
    decide, seen = _recorder()
    tax_phase(world, decide, rng)
    assert seen[0].facts == {
        "taxable": 100000, "cash": 250000, "audit_budget": 6, "owner_scruples": 0.35,
    }


def test_taxable_profit_is_never_negative(world, rng):
    _quarter(world, profit=-5000)
    decide, seen = _recorder()
    evs = tax_phase(world, decide, rng)
    assert seen[0].facts["taxable"] == 0
    ev = next(e for e in evs if e.kind == "file_tax" and e.payload["firm_id"] == "f1")
    assert ev.payload["tax_paid"] == 0
    assert world.firms["f1"].last_reported_profit == 0


def test_tax_report_prompts_are_clean_and_state_the_amounts(world, rng):
    _quarter(world, profit=100000)
    decide, seen = _recorder()
    tax_phase(world, decide, rng)
    assert_prompt_clean(seen)
    d = seen[0]
    assert world.firms["f1"].name in d.situation
    assert "$1,000.00" in d.situation                     # taxable profit
    labels = {o.id: o.label for o in d.options}
    assert "$200.00" in labels["honest"]                  # full tax
    assert "$500.00" in labels["hide50_heavy"]            # reported profit
    assert "$100.00" in labels["hide50_heavy"]            # tax paid
    assert "$15.00" in labels["hide50_heavy"]             # concealment cost: 15% of 100.00


def test_situation_states_whether_underreporting_is_illegal(world, rng):
    _quarter(world)
    decide, seen = _recorder()
    tax_phase(world.model_copy(deep=True), decide, rng)
    world.laws.illegal.discard("underreport_tax")
    tax_phase(world, decide, rng)
    assert "illegal" in seen[0].situation
    assert "illegal" not in seen[-1].situation


# Filing.


def test_honest_filing_pays_tax_and_records_reported_profit(one_firm_world, rng):
    w = _quarter(one_firm_world, profit=100000)
    t = w.treasury.cash
    evs = tax_phase(w, scripted({}), rng)
    ev = next(e for e in evs if e.kind == "file_tax")
    assert ev.legal and ev.visible and ev.harm == {}
    assert ev.payload["tax_due"] == 20000 and ev.payload["tax_paid"] == 20000
    assert w.treasury.cash == t + 20000
    assert w.firms["f1"].last_reported_profit == 100000
    assert not any(i.kind == "tax_fraud" for i in w.infos.values())


@pytest.mark.parametrize(
    "option, reported, paid, unpaid, cost",
    [
        ("hide25_none", 75000, 15000, 5000, 0),
        ("hide25_some", 75000, 15000, 5000, 250),
        ("hide25_heavy", 75000, 15000, 5000, 750),
        ("hide50_none", 50000, 10000, 10000, 0),
        ("hide50_some", 50000, 10000, 10000, 500),
        ("hide50_heavy", 50000, 10000, 10000, 1500),
    ],
)
def test_underreporting_amounts(one_firm_world, rng, option, reported, paid, unpaid, cost):
    w = _quarter(one_firm_world, profit=100000)
    t = w.treasury.cash
    evs = tax_phase(w, scripted({"tax_report": option}), rng)
    ev = next(e for e in evs if e.kind == "underreport_tax")
    assert ev.payload["reported"] == reported
    assert ev.payload["tax_paid"] == paid
    assert ev.payload["unpaid"] == unpaid
    assert ev.payload["concealment_cost"] == cost
    assert ev.harm == {"treasury": unpaid}
    assert w.treasury.cash == t + paid + cost
    assert w.firms["f1"].last_reported_profit == reported


def test_underreporting_creates_a_secret_with_a_deterministic_id(one_firm_world, rng):
    w = _quarter(one_firm_world, profit=100000)
    w.step = 26
    tax_phase(w, scripted({"tax_report": "hide25_some"}), rng)
    info = w.infos["info:26:tax_fraud:f1"]
    assert info.kind == "tax_fraud" and info.subject_id == "f1" and info.step == 26
    assert info.payload == {"unpaid": 5000, "concealment": "some", "quarter": 2}
    assert info.known_by == {w.firms["f1"].owner_id}


def test_firm_that_cannot_afford_the_tax_pays_what_it_has(one_firm_world, rng):
    w = _quarter(one_firm_world, profit=100000, cash=7000)
    t = w.treasury.cash
    evs = tax_phase(w, scripted({}), rng)
    ev = next(e for e in evs if e.kind == "file_tax")
    assert ev.payload["tax_due"] == 20000 and ev.payload["tax_paid"] == 7000
    assert w.firms["f1"].cash == 0
    assert w.treasury.cash == t + 7000


def test_rejected_underreport_files_honestly_and_keeps_the_rejection(one_firm_world, rng):
    # Cash covers the 10000 tax on the reported half but not the 1500 concealment cost.
    w = _quarter(one_firm_world, profit=100000, cash=11000)
    evs = tax_phase(w, scripted({"tax_report": "hide50_heavy"}), rng)
    kinds = [e.kind for e in evs]
    assert kinds.index("action_rejected") < kinds.index("file_tax")
    rejected = evs[kinds.index("action_rejected")]
    assert rejected.payload["action"] == "underreport_tax"
    assert rejected.visible is False
    honest = evs[kinds.index("file_tax")]
    assert honest.payload["tax_due"] == 20000 and honest.payload["tax_paid"] == 11000
    assert "underreport_tax" not in kinds
    assert not any(i.kind == "tax_fraud" for i in w.infos.values())


def test_underreporting_with_nothing_to_hide_is_rejected(one_firm_world, rng):
    w = _quarter(one_firm_world, profit=0)
    evs = tax_phase(w, scripted({"tax_report": "hide50_none"}), rng)
    kinds = [e.kind for e in evs]
    assert "action_rejected" in kinds and "file_tax" in kinds
    assert not w.infos


def test_only_the_owner_of_an_open_firm_may_file(world, rng):
    other = world.firms["f2"].owner_id
    (ev,) = execute(world, other, FileTax(firm_id="f1", hide_share=0.0, concealment="none"), rng)
    assert ev.kind == "action_rejected"


# After filing: dividend, profit and budget reset.


def test_dividend_profit_reset_and_budget_reset(one_firm_world, rng):
    w = _quarter(one_firm_world, profit=100000, cash=400000)
    w.regulator.budget_per_quarter = 6
    w.regulator.budget_left = 1
    owner = w.people[w.firms["f1"].owner_id]
    o = owner.cash
    bill = w.firms["f1"].wage * len(w.firms["f1"].employee_ids)
    evs = tax_phase(w, scripted({}), rng)
    # After 20000 tax the firm keeps four weeks of wages and pays out half of the rest.
    dividend = round(0.5 * (380000 - 4 * bill))
    assert owner.cash == o + dividend
    assert w.firms["f1"].cash == 380000 - dividend
    div = next(e for e in evs if e.kind == "dividend")
    assert div.payload == {"firm_id": "f1", "owner_id": owner.id, "amount": dividend}
    assert w.firms["f1"].profit_qtd == 0
    assert w.regulator.budget_left == 6


def test_fractional_reserve_weeks_are_not_truncated(one_firm_world, rng):
    w = _quarter(one_firm_world, profit=0, cash=400000)
    w.params["reserve_weeks"] = 2.5
    f1 = w.firms["f1"]
    f1.wage = 1001
    worker = w.people[sorted(set(w.people) - {f1.owner_id})[0]]
    worker.role, worker.employer_id = "worker", "f1"
    f1.employee_ids = [worker.id]
    o = w.people[f1.owner_id].cash
    tax_phase(w, scripted({}), rng)
    # 2.5 weeks of a 1001 wage bill is a 2502.5 reserve; truncating to 2 weeks would give 2002.
    assert w.people[f1.owner_id].cash == o + round(0.5 * (400000 - 2502.5))


def test_no_dividend_when_cash_is_below_the_reserve(one_firm_world, rng):
    w = _quarter(one_firm_world, profit=0)
    w.firms["f1"].cash = 4 * w.firms["f1"].wage * len(w.firms["f1"].employee_ids)
    o = w.people[w.firms["f1"].owner_id].cash
    evs = tax_phase(w, scripted({}), rng)
    assert w.people[w.firms["f1"].owner_id].cash == o
    assert not any(e.kind == "dividend" for e in evs)


def test_tax_phase_conserves_money_and_is_deterministic(world, rng):
    _quarter(world)
    other = world.model_copy(deep=True)
    before = total_money(world)
    script = scripted({"tax_report": "hide50_heavy"})
    tax_phase(world, script, rng)
    tax_phase(other, script, random.Random(0))
    assert total_money(world) == before
    assert state_hash(world) == state_hash(other)


# The audit decision.


def test_auditor_gets_one_audit_decision(fraud_world, rng):
    decide, seen = _recorder()
    assert audit_phase(fraud_world, decide, rng) == []
    (d,) = seen
    assert d.actor_id == fraud_world.regulator.auditor_id
    assert d.kind == "audit"
    assert d.id == f"14:{d.actor_id}:audit:0"
    assert d.default_option_id == "none"


def test_audit_options_for_each_open_firm(fraud_world, rng):
    close_firm(fraud_world, "f3")
    decide, seen = _recorder()
    audit_phase(fraud_world, decide, rng)
    open_ids = [fid for fid in sorted(fraud_world.firms) if fid != "f3"]
    expected = ["none"]
    for fid in open_ids:
        expected += [f"light:{fid}", f"deep:{fid}"]
    assert [o.id for o in seen[0].options] == expected
    actions = {o.id: o.action for o in seen[0].options}
    assert actions["none"] is None
    assert action_from_dict(actions["deep:f1"]) == Audit(firm_id="f1", depth="deep")


def test_audit_with_no_budget_offers_only_none(fraud_world, rng):
    fraud_world.regulator.budget_left = 0
    decide, seen = _recorder()
    audit_phase(fraud_world, decide, rng)
    assert [o.id for o in seen[0].options] == ["none"]


def test_audit_facts_and_situation_show_public_information_only(fraud_world, rng):
    w = fraud_world
    w.regulator.tips.append("f2")
    w.firms["f1"].profit_qtd = 7777777
    w.firms["f1"].customers_last_step = 9
    w.firms["f1"].price = 4321
    w.firms["f2"].last_reported_profit = None
    decide, seen = _recorder()
    audit_phase(w, decide, rng)
    d = seen[0]
    assert d.facts["budget_left"] == 6
    assert d.facts["firms"]["f1"] == {
        "last_reported_profit": 50000, "customers_last_step": 9, "price": 4321, "tipped": False,
    }
    assert d.facts["firms"]["f2"]["tipped"] is True
    assert d.facts["firms"]["f2"]["last_reported_profit"] is None
    assert "$500.00" in d.situation and "$43.21" in d.situation
    assert "77,777.77" not in d.situation
    assert "7777777" not in str(d.facts)
    assert_prompt_clean(seen)


def test_no_auditor_means_no_audit(fraud_world, rng):
    fraud_world.regulator.auditor_id = ""
    decide, seen = _recorder()
    assert audit_phase(fraud_world, decide, rng) == []
    assert seen == []


# Detection.


@pytest.mark.parametrize(
    "depth, tipped, concealment, p",
    [
        ("light", False, "none", 0.3),
        ("light", False, "some", 0.18),
        ("light", False, "heavy", 0.09),
        ("deep", False, "none", 0.9),
        ("deep", False, "heavy", 0.27),
        ("light", True, "none", 0.6),
        ("light", True, "heavy", 0.18),
        ("deep", True, "none", 1.0),
        ("deep", True, "some", 0.6),
        ("deep", True, "heavy", 0.3),
    ],
)
def test_detection_probability(fraud_world, depth, tipped, concealment, p):
    _fraud(fraud_world).payload["concealment"] = concealment
    if tipped:
        fraud_world.regulator.tips.append("f1")
    for draw, found in [(p - 0.001, True), (p + 0.001, False)]:
        w = fraud_world.model_copy(deep=True)
        evs = audit_phase(w, scripted({"audit": f"{depth}:f1"}), _Draw(draw))
        kinds = [e.kind for e in evs]
        assert ("audit_detected" in kinds) is found, (draw, kinds)
        assert ("audit_clean" in kinds) is not found


def test_tipped_deep_audit_against_heavy_concealment_detects_at_point_three(fraud_world):
    assert _fraud(fraud_world).payload["concealment"] == "heavy"
    fraud_world.regulator.tips.append("f1")
    below, above = fraud_world.model_copy(deep=True), fraud_world
    evs = audit_phase(below, scripted({"audit": "deep:f1"}), _Draw(0.29))
    assert any(e.kind == "audit_detected" for e in evs)
    evs = audit_phase(above, scripted({"audit": "deep:f1"}), _Draw(0.31))
    assert any(e.kind == "audit_clean" for e in evs)
    assert not any(e.kind == "audit_detected" for e in evs)


def test_detection_removes_the_firm_from_tips(fraud_world):
    fraud_world.regulator.tips = ["f1", "f2", "f1"]
    audit_phase(fraud_world, scripted({"audit": "light:f1"}), _Draw(0.0))
    assert fraud_world.regulator.tips == ["f2"]


def test_tips_persist_when_nothing_is_detected(fraud_world):
    fraud_world.regulator.tips = ["f1", "f2"]
    audit_phase(fraud_world, scripted({"audit": "light:f1"}), _Draw(0.99))
    audit_phase(fraud_world, scripted({"audit": "light:f2"}), _Draw(0.0))
    assert fraud_world.regulator.tips == ["f1", "f2"]


def test_detection_charges_the_penalty_and_marks_the_secret(fraud_world):
    w = fraud_world
    t, c = w.treasury.cash, w.firms["f1"].cash
    evs = audit_phase(w, scripted({"audit": "deep:f1"}), _Draw(0.0))
    kinds = [e.kind for e in evs]
    assert kinds[0] == "audit"
    detected = evs[kinds.index("audit_detected")]
    assert detected.payload == {"firm_id": "f1", "unpaid": 10000, "penalty": 30000}
    assert w.treasury.cash == t + 30000 and w.firms["f1"].cash == c - 30000
    info = _fraud(w)
    assert info.payload["detected"] is True
    assert w.regulator.auditor_id in info.known_by


def test_clean_audit_when_there_is_no_fraud(fraud_world, rng):
    evs = audit_phase(fraud_world, scripted({"audit": "deep:f2"}), _Draw(0.0))
    clean = next(e for e in evs if e.kind == "audit_clean")
    assert clean.payload == {"firm_id": "f2", "depth": "deep"}
    assert fraud_world.regulator.budget_left == 3


def test_failed_detection_looks_the_same_as_no_fraud(fraud_world):
    a = fraud_world.model_copy(deep=True)
    evs_fraud = audit_phase(fraud_world, scripted({"audit": "light:f1"}), _Draw(0.99))
    evs_none = audit_phase(a, scripted({"audit": "light:f2"}), _Draw(0.99))
    clean_fraud = next(e for e in evs_fraud if e.kind == "audit_clean")
    clean_none = next(e for e in evs_none if e.kind == "audit_clean")
    assert set(clean_fraud.payload) == set(clean_none.payload) == {"firm_id", "depth"}
    assert "detected" not in _fraud(fraud_world).payload
    assert fraud_world.regulator.auditor_id not in _fraud(fraud_world).known_by


def test_detected_fraud_cannot_be_found_again(fraud_world):
    fraud_world.regulator.tips.append("f1")
    audit_phase(fraud_world, scripted({"audit": "light:f1"}), _Draw(0.0))
    evs = audit_phase(fraud_world, scripted({"audit": "light:f1"}), _Draw(0.0))
    assert any(e.kind == "audit_clean" for e in evs)


def test_only_the_most_recent_undetected_fraud_can_be_found(fraud_world):
    w = fraud_world
    w.infos["info:26:tax_fraud:f1"] = Info(
        id="info:26:tax_fraud:f1", step=26, kind="tax_fraud", subject_id="f1",
        payload={"unpaid": 4000, "concealment": "none", "quarter": 2},
        known_by={w.firms["f1"].owner_id},
    )
    w.step = 27
    evs = audit_phase(w, scripted({"audit": "light:f1"}), _Draw(0.0))
    detected = next(e for e in evs if e.kind == "audit_detected")
    assert detected.payload["unpaid"] == 4000
    assert w.infos["info:26:tax_fraud:f1"].payload["detected"] is True
    assert "detected" not in _fraud(w).payload


def test_audit_beyond_the_budget_is_rejected(fraud_world, rng):
    fraud_world.regulator.budget_left = 2
    before = state_hash(fraud_world)
    (ev,) = execute(fraud_world, fraud_world.regulator.auditor_id, Audit(firm_id="f1", depth="deep"), rng)
    assert ev.kind == "action_rejected" and ev.payload["action"] == "audit"
    assert state_hash(fraud_world) == before


def test_only_the_auditor_may_audit_an_open_firm(fraud_world, rng):
    owner = fraud_world.firms["f1"].owner_id
    (ev,) = execute(fraud_world, owner, Audit(firm_id="f1", depth="light"), rng)
    assert ev.kind == "action_rejected"
    close_firm(fraud_world, "f2")
    (ev,) = execute(fraud_world, fraud_world.regulator.auditor_id, Audit(firm_id="f2", depth="light"), rng)
    assert ev.kind == "action_rejected"


def test_audit_phase_conserves_money_and_is_deterministic(fraud_world, rng):
    other = fraud_world.model_copy(deep=True)
    before = total_money(fraud_world)
    audit_phase(fraud_world, scripted({"audit": "light:f1"}), rng)
    audit_phase(other, scripted({"audit": "light:f1"}), random.Random(0))
    assert total_money(fraud_world) == before
    assert state_hash(fraud_world) == state_hash(other)


# settle_penalty.


def test_settle_penalty_with_zero_cash_moves_nothing(fraud_world):
    fraud_world.firms["f1"].cash = 0
    t = fraud_world.treasury.cash
    settle_penalty(fraud_world, "f1", 30000)
    assert fraud_world.firms["f1"].cash == 0 and fraud_world.treasury.cash == t
    assert _fraud(fraud_world).payload["detected"] is True


def test_settle_penalty_reports_what_was_paid(fraud_world):
    fraud_world.firms["f1"].cash = 50
    t = fraud_world.treasury.cash
    (ev,) = settle_penalty(fraud_world, "f1", 30000)
    assert ev.payload == {"firm_id": "f1", "penalty": 30000, "paid": 50}
    assert fraud_world.treasury.cash == t + 50
    assert fraud_world.regulator.auditor_id in _fraud(fraud_world).known_by


# Baseline rules.


def _rule(decision: Decision) -> str:
    return RuleDecider(random.Random(0)).decide(decision).option_id


def _audit_decision(w: World) -> Decision:
    decide, seen = _recorder()
    audit_phase(w.model_copy(deep=True), decide, random.Random(0))
    return seen[0]


def test_tax_and_audit_rules_are_registered():
    assert "tax_report" in RULES and "audit" in RULES


def test_tax_report_rule_files_honestly(world, rng):
    _quarter(world)
    decide, seen = _recorder()
    tax_phase(world, decide, rng)
    assert {_rule(d) for d in seen} == {"honest"}


def _set_reports(w: World, ratios: dict[str, int]) -> None:
    """Give each open firm price 1000 and 1 customer, so its ratio is reported / 13000."""
    for fid, f in w.firms.items():
        f.price, f.customers_last_step = 1000, 1
        f.last_reported_profit = ratios.get(fid)


def test_audit_rule_deep_audits_a_tipped_firm(fraud_world):
    _set_reports(fraud_world, {"f1": 900, "f2": 100})
    fraud_world.regulator.tips.append("f1")
    assert _rule(_audit_decision(fraud_world)) == "deep:f1"


def test_audit_rule_light_audits_the_lowest_ratio_when_deep_is_unaffordable(fraud_world):
    _set_reports(fraud_world, {"f1": 900, "f2": 300, "f3": 100})
    fraud_world.firms["f3"].customers_last_step = 0   # ratio 100 / max(1, 0) = 100
    fraud_world.firms["f2"].customers_last_step = 10  # ratio 300 / 130000
    fraud_world.regulator.tips.append("f1")
    fraud_world.regulator.budget_left = 2
    assert _rule(_audit_decision(fraud_world)) == "light:f2"


def test_audit_rule_skips_firms_that_have_never_reported(fraud_world):
    _set_reports(fraud_world, {"f4": 500})
    assert _rule(_audit_decision(fraud_world)) == "light:f4"
    _set_reports(fraud_world, {})
    assert _rule(_audit_decision(fraud_world)) == "none"


def test_audit_rule_with_no_budget_picks_none(fraud_world):
    _set_reports(fraud_world, {"f1": 100})
    fraud_world.regulator.tips.append("f1")
    fraud_world.regulator.budget_left = 0
    assert _rule(_audit_decision(fraud_world)) == "none"
