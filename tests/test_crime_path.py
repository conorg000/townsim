"""The whole crime path, run through `Simulation` and replayed (review I6).

Scripted "crooked" deciders make every owner underreport, workers tip, owners offer
bribes, the auditor take some and refuse others, and journalists publish what they find.
The run covers several quarters. The checks are the engine's promises about such a run:
it replays exactly (also after an abort and resume), money is conserved, the public
record shows one filing per open firm per quarter and nothing hidden, and each fraud is
found at most once.
"""

import json
import random

import pytest

from helpers import make_sim
from townsim.config import TownConfig
from townsim.decide import Choice, Decision, ModelUnavailable, RuleDecider
from townsim.runs import replay
from townsim.sim import Simulation
from townsim.tax import QUARTER_STEPS
from townsim.world import total_money

STEPS = 3 * QUARTER_STEPS + 3  # three tax quarters and the audits after the last one
ABORT_AT = QUARTER_STEPS + 4  # after the first quarter's frauds exist

# Event kinds that record a private act or its private outcome.
HIDDEN_KINDS = {
    "decision",
    "invalid_response",
    "file_tax",
    "underreport_tax",
    "audit_detected",
    "audit_clean",
    "offer_bribe",
    "accept_bribe",
    "tip_auditor",
    "tip_press",
    "investigate",
    "investigation_found",
    "investigation_empty",
}
HIDDEN_ACTIONS = {"underreport_tax", "offer_bribe", "accept_bribe", "tip_auditor", "tip_press",
                  "investigate"}
AUDIT_OUTCOME_KEYS = {"penalty", "unpaid", "taxable", "concealment", "concealment_cost"}


class Crooked:
    """Commits every crime on offer; everything else is left to the baseline rules."""

    def __init__(self, seed: int = 0):
        self.rules = RuleDecider(random.Random(f"{seed}:crooked"))

    def decide(self, decision: Decision) -> Choice:
        kind, actor = decision.kind, decision.actor_id
        if kind == "tax_report":
            return Choice(option_id="hide50_some")
        if kind == "audit_response":
            return Choice(option_id="offer_bribe")
        if kind == "bribe_offer":  # the auditor takes payments in even weeks, refuses in odd
            week = int(decision.id.split(":", 1)[0])
            return Choice(option_id="accept" if week % 2 == 0 else "refuse")
        if kind == "tip":
            return Choice(option_id="tip_auditor" if int(actor[1:]) % 2 else "tip_press")
        if kind == "publish":
            return Choice(option_id="publish")
        return self.rules.decide(decision)


class CrookedUntil(Crooked):
    """`Crooked` until a decision comes at step `n`, then a model outage."""

    def __init__(self, n: int):
        super().__init__()
        self.n = n

    def decide(self, decision: Decision) -> Choice:
        if int(decision.id.split(":", 1)[0]) == self.n:
            raise ModelUnavailable(f"no answer at step {self.n}")
        return super().decide(decision)


@pytest.fixture
def crime_cfg(cfg_dict) -> TownConfig:
    """The shipped town with the values the crime path relies on set here, not read from it."""
    cfg_dict["laws"].update(
        tax_rate=0.2, penalty_multiplier=2.0,
        illegal=["underreport_tax", "offer_bribe", "accept_bribe"],
    )
    cfg_dict["params"]["audit_budget_per_quarter"] = 6
    cfg_dict["people"]["start_cash"] = 500000  # owners can afford the payments they offer
    cfg_dict["assignment"] = {"default": "rule", "by_role": {}, "by_person": {}}
    return TownConfig.model_validate(cfg_dict)


@pytest.fixture
def crooked_run(crime_cfg, tmp_path) -> Simulation:
    sim = make_sim(crime_cfg, tmp_path / "crime.sqlite", seed=11, deciders={"rule": Crooked()})
    sim.run(STEPS)
    return sim


@pytest.fixture
def resumed_run(crime_cfg, tmp_path) -> Simulation:
    sim = make_sim(
        crime_cfg, tmp_path / "resumed.sqlite", seed=11, deciders={"rule": CrookedUntil(ABORT_AT)}
    )
    with pytest.raises(ModelUnavailable):
        sim.run(STEPS)
    assert sim.world.step == ABORT_AT
    resumed = Simulation.resume(sim.log, "r", {"rule": Crooked(seed=1)})
    assert resumed.world.infos, "the resumed world should hold live secrets"
    resumed.run(STEPS - ABORT_AT)
    return resumed


def _events(sim: Simulation):
    return [(step, e) for step, e in sim.log.events(sim.run_id) if e.kind != "run_aborted"]


def _kinds(sim: Simulation) -> list[str]:
    return [e.kind for _, e in _events(sim)]


def _final_world(sim: Simulation):
    return sim.log.load_snapshot(sim.run_id, sim.log.latest_snapshot_step(sim.run_id))[0]


@pytest.mark.parametrize("run", ["crooked_run", "resumed_run"])
def test_the_crime_path_is_exercised(run, request):
    kinds = _kinds(request.getfixturevalue(run))
    for kind in ("underreport_tax", "tip_auditor", "tip_press", "audit", "audit_detected",
                 "offer_bribe", "accept_bribe", "penalty_paid", "investigation_found", "publish"):
        assert kind in kinds, f"no {kind} event in the run"
    assert kinds.count("run_aborted") == 0


def test_a_refused_bribe_is_exercised(crooked_run):
    refused = [
        (step, e) for step, e in _events(crooked_run)
        if e.kind == "decision" and e.payload["decision_kind"] == "bribe_offer"
        and e.payload["option_id"] == "refuse"
    ]
    assert refused, "the auditor never refused a payment"


@pytest.mark.parametrize("run", ["crooked_run", "resumed_run"])
def test_the_crooked_run_replays_exactly(run, request):
    sim = request.getfixturevalue(run)
    assert sim.world.step == STEPS
    replay(sim.log, sim.run_id)


def test_the_resumed_run_logged_its_abort(resumed_run):
    aborted = [s for s, e in resumed_run.log.events("r", kind="run_aborted")]
    assert aborted == [ABORT_AT]


@pytest.mark.parametrize("run", ["crooked_run", "resumed_run"])
def test_money_is_conserved(run, request):
    sim = request.getfixturevalue(run)
    start, _ = sim.log.load_snapshot(sim.run_id, -1)
    expected = total_money(start)
    for step in range(-1, STEPS):
        world, _ = sim.log.load_snapshot(sim.run_id, step)
        assert total_money(world) == expected, f"money changed by step {step}"


@pytest.mark.parametrize("run", ["crooked_run", "resumed_run"])
def test_every_open_firm_has_one_visible_filing_each_quarter(run, request):
    sim = request.getfixturevalue(run)
    events = _events(sim)
    quarters = range(QUARTER_STEPS, STEPS, QUARTER_STEPS)
    assert len(quarters) >= 2
    for q in quarters:
        before, _ = sim.log.load_snapshot(sim.run_id, q - 1)
        after, _ = sim.log.load_snapshot(sim.run_id, q)
        filers = [e.payload["firm_id"] for s, e in events
                  if s == q and e.visible and e.kind == "tax_filed"]
        assert len(filers) == len(set(filers)), f"a firm filed twice in week {q}"
        open_after = {fid for fid, f in after.firms.items() if f.open}
        open_before = {fid for fid, f in before.firms.items() if f.open}
        assert open_after <= set(filers) <= open_before, f"week {q}: filings {filers}"
        assert set(open_before) - set(filers) <= {
            e.payload["firm_id"] for s, e in events if s == q and e.kind == "firm_closed"
        }
    assert not [e for _, e in events if e.visible and e.kind == "tax_filed"
                and any(k in e.payload for k in AUDIT_OUTCOME_KEYS)]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


@pytest.mark.parametrize("run", ["crooked_run", "resumed_run"])
def test_the_public_record_reveals_nothing_hidden(run, request):
    sim = request.getfixturevalue(run)
    world = _final_world(sim)
    published = {iid for iid, i in world.infos.items() if i.payload.get("published")}
    hidden_ids = set(world.infos) - published
    assert hidden_ids, "the run should leave some secrets unpublished"
    for step, e in _events(sim):
        if not e.visible:
            continue
        where = f"step {step}: visible {e.kind}"
        assert e.kind not in HIDDEN_KINDS, where
        if e.kind == "action_rejected":
            assert e.payload["action"] not in HIDDEN_ACTIONS, where
        named = {s for s in _strings(e.payload) if s.startswith("info:")}
        assert not named & hidden_ids, f"{where} names {sorted(named & hidden_ids)}"
        for iid in named:
            # A visible event may name an Info only once it is public: the publication itself.
            assert e.kind == "publish" and iid in published, f"{where} names {iid}"
        if e.kind != "penalty_paid":
            assert not AUDIT_OUTCOME_KEYS & set(e.payload), f"{where}: {sorted(e.payload)}"
        assert "info:" not in json.dumps(e.payload) or e.kind == "publish", where


@pytest.mark.parametrize("run", ["crooked_run", "resumed_run"])
def test_a_penalty_is_public_only_after_a_detection_in_the_same_step(run, request):
    sim = request.getfixturevalue(run)
    by_step: dict[int, list] = {}
    for step, e in _events(sim):
        by_step.setdefault(step, []).append(e)
    for step, events in by_step.items():
        detected = sum(e.kind == "audit_detected" for e in events)
        penalties = sum(e.kind == "penalty_paid" for e in events)
        assert penalties <= detected, f"step {step}: penalty with no detection"


@pytest.mark.parametrize("run", ["crooked_run", "resumed_run"])
def test_each_fraud_is_detected_at_most_once(run, request):
    sim = request.getfixturevalue(run)
    world = _final_world(sim)
    frauds = [i for i in world.infos.values() if i.kind == "tax_fraud"]
    for fraud in frauds:
        assert not (fraud.payload.get("detected") and fraud.payload.get("buried")), fraud.id
    for fid in world.firms:
        detections = sum(
            e.kind == "audit_detected" and e.payload["firm_id"] == fid for _, e in _events(sim)
        )
        closed = sum(
            1 for i in frauds
            if i.subject_id == fid and (i.payload.get("detected") or i.payload.get("buried"))
        )
        # Each detection settles or buries exactly one fraud, which no audit finds again.
        assert detections == closed, f"{fid}: {detections} detections, {closed} frauds closed"
    buried = [i for i in frauds if i.payload.get("buried")]
    assert buried, "no fraud was bribed away"
    assert len([e for _, e in _events(sim) if e.kind == "accept_bribe"]) == len(buried)
