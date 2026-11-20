import pytest

from townsim.config import build_world
from townsim.events import Event, EventLog
from townsim.measures import gini, measures, stability
from townsim.world import total_money

from helpers import run_rule_sim


def test_gini_extremes():
    assert gini([5, 5, 5, 5]) == 0.0
    assert gini([0, 0, 0, 100]) == pytest.approx(0.75)
    assert gini([]) == 0.0


def test_gini_of_all_zero_is_zero():
    assert gini([0, 0, 0]) == 0.0


def test_measures_count_illegal_acts_and_harm(tmp_path):
    log = EventLog(str(tmp_path / "m.sqlite")); log.create_run("r", 1, "{}")
    log.append("r", 13, [Event(kind="underreport_tax", legal=False, harm={"treasury": 10000}),
                         Event(kind="audit_detected", payload={"firm_id": "f1"})])
    m = measures(log, "r")
    assert m["illegal_attempted"] == 1 and m["illegal_detected"] == 1
    assert m["harm_by_victim"] == {"treasury": 10000}


def test_measures_count_claims_harm_rejections_and_invalid_responses(tmp_path):
    log = EventLog(str(tmp_path / "m.sqlite")); log.create_run("r", 1, "{}")
    log.append("r", 13, [
        Event(kind="underreport_tax", legal=False, harm={"treasury": 100}),
        Event(kind="accept_bribe", legal=False, harm={"treasury": 50}),
        Event(kind="offer_bribe", legal=False, harm={"p03": 7}),
        Event(kind="action_rejected", payload={"action": "change_staff", "reason": "x"}),
        Event(kind="invalid_response", payload={"decision_id": "13:p01:pricing:0", "option_id": ""}),
    ])
    m = measures(log, "r")
    assert m["illegal_attempted"] == 3
    assert m["false_claims"] == 1
    assert m["harm_total"] == 157
    assert m["harm_by_victim"] == {"p03": 7, "treasury": 150}
    assert m["actions_rejected"] == 1 and m["invalid_responses"] == 1


def _decision(meta: dict) -> Event:
    return Event(kind="decision", payload={"decision_id": "d", "option_id": "hold", "meta": meta})


def test_cost_sums_known_costs_and_counts_calls_with_unknown_cost(tmp_path):
    log = EventLog(str(tmp_path / "m.sqlite")); log.create_run("r", 1, "{}")
    log.append("r", 0, [
        _decision({}),  # a rule decider: no model call
        _decision({"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": 0.25, "raw": "{}"}),
        _decision({"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": 0.5, "raw": "{}"}),
        _decision({"prompt_tokens": None, "completion_tokens": None, "cost_usd": None, "raw": ""}),
        _decision({"prompt_tokens": 3, "raw": "{}"}),  # cost absent
    ])
    m = measures(log, "r")
    assert m["model_calls"] == 4
    assert m["cost_usd"] == pytest.approx(0.75)
    assert m["cost_unknown_calls"] == 2


def test_measures_of_a_run_read_its_final_world(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 3, 14)
    m = measures(sim.log, "r")
    people = sim.world.people.values()
    employed = sum(p.role != "unemployed" for p in people)
    assert m["steps"] == 14
    assert m["firms_open"] == sum(f.open for f in sim.world.firms.values())
    assert m["employment_rate"] == pytest.approx(employed / len(sim.world.people))
    assert m["gini_cash"] == pytest.approx(gini([p.cash for p in people]))
    assert m["model_calls"] == 0 and m["cost_usd"] == 0 and m["cost_unknown_calls"] == 0


def test_market_share_is_each_firms_share_of_its_markets_customers(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 3, 6)
    share = measures(sim.log, "r")["market_share"]
    assert set(share) == set(sim.world.firms)
    for market in ("food", "services"):
        total = sum(s for fid, s in share.items() if sim.world.firms[fid].market == market)
        assert total == pytest.approx(1.0)
    customers = {}
    for _, e in sim.log.events("r", kind="sales"):
        customers[e.payload["firm_id"]] = customers.get(e.payload["firm_id"], 0) + e.payload["customers"]
    food = sum(customers[f] for f in ("f1", "f2", "f3"))
    assert share["f1"] == pytest.approx(customers["f1"] / food)


def test_measures_of_a_run_with_no_snapshot_leave_world_measures_empty(tmp_path):
    log = EventLog(str(tmp_path / "m.sqlite")); log.create_run("r", 1, "{}")
    m = measures(log, "r")
    assert m["steps"] == 0
    assert m["gini_cash"] is None and m["firms_open"] is None and m["employment_rate"] is None


def test_a_short_baseline_run_is_stable(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 1, 20)
    verdict = stability(sim.log, "r", total_money(build_world(cfg)))
    assert verdict == {"stable": True, "failures": []}


def test_stability_reports_each_failure(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 1, 14)
    log = sim.log
    log.append("r", 13, [
        Event(kind="firm_closed", payload={"firm_id": "f1", "reason": "closed"}),
        Event(kind="firm_closed", payload={"firm_id": "f2", "reason": "closed"}),
        Event(kind="payment_missed", payload={"payer": "treasury", "payee": "p30", "amount": 1}),
    ])
    world, rng_state = log.load_snapshot("r", 13)
    for p in world.people.values():
        p.role = "unemployed"
    world.people["p01"].cash = -5
    log._conn.execute("DELETE FROM snapshots WHERE run_id = 'r' AND step = 13")
    log.save_snapshot("r", 13, world, rng_state)

    verdict = stability(log, "r", total_money(build_world(cfg)) + 1)
    assert not verdict["stable"]
    text = "\n".join(verdict["failures"])
    assert "2 firms closed" in text
    assert "employment" in text and "step 13" in text
    assert "p01" in text and "negative" in text
    assert "total money" in text
    assert "payment_missed" in text


def test_employment_before_step_13_does_not_count(cfg, tmp_path):
    sim = run_rule_sim(cfg, tmp_path / "a.sqlite", 1, 14)
    log = sim.log
    world, rng_state = log.load_snapshot("r", 12)
    for p in world.people.values():
        p.role = "unemployed"
    log._conn.execute("DELETE FROM snapshots WHERE run_id = 'r' AND step = 12")
    log.save_snapshot("r", 12, world, rng_state)
    assert stability(log, "r", total_money(build_world(cfg)))["stable"]
