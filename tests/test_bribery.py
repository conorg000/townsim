import random

import pytest

from helpers import assert_prompt_clean, scripted
from townsim.actions import action_from_dict
from townsim.bribery import AcceptBribe, OfferBribe, resolve_bribe
from townsim.decide import Choice, Decision, RuleDecider
from townsim.market import close_firm
from townsim.tax import audit_phase
from townsim.world import Info, World, total_money

ILLEGAL = {"underreport_tax", "offer_bribe", "accept_bribe"}


class _Draw(random.Random):
    """An rng whose `random()` always returns `value`."""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _recorder(choose: dict[str, str] | None = None):
    """A decide function that records each decision and picks `choose[kind]` or the default."""
    seen: list[Decision] = []
    choose = choose or {}

    def decide(d: Decision) -> Choice:
        seen.append(d)
        return Choice(option_id=choose.get(d.kind, d.default_option_id))

    return decide, seen


@pytest.fixture
def fraud_world(fraud_world) -> World:
    """`fraud_world` with the laws and personal cash these tests rely on set explicitly."""
    w = fraud_world
    w.laws.illegal = set(ILLEGAL)
    w.laws.penalty_multiplier = 2.0
    w.people[w.firms["f1"].owner_id].cash = 50000
    w.people[w.regulator.auditor_id].cash = 1000
    w.newspaper.press_tips = ["f2", "f1"]
    return w


def _fraud(w: World) -> Info:
    return w.infos["info:13:tax_fraud:f1"]


OFFER = {"audit_response": "offer_bribe", "bribe_offer": "accept"}
REFUSE = {"audit_response": "offer_bribe", "bribe_offer": "refuse"}


# The brief's tests.


def test_accepted_bribe_moves_money_to_auditor_and_skips_penalty(fraud_world, rng):
    w = fraud_world; t = w.treasury.cash
    aud = w.people[w.regulator.auditor_id]; a0 = aud.cash
    evs = resolve_bribe(w, "f1", 30000, scripted(OFFER), rng)
    assert aud.cash == a0 + 15000 and w.treasury.cash == t
    assert {e.kind for e in evs if not e.legal} == {"offer_bribe", "accept_bribe"}
    assert any(i.kind == "bribery" for i in w.infos.values())


def test_refused_bribe_adds_half_again_to_the_penalty(fraud_world, rng):
    w = fraud_world; t = w.treasury.cash
    resolve_bribe(w, "f1", 30000, scripted(REFUSE), rng)
    assert w.treasury.cash == t + 45000


def test_owner_who_cannot_afford_the_bribe_pays_the_plain_penalty(fraud_world, rng):
    w = fraud_world; w.people[w.firms["f1"].owner_id].cash = 10; t = w.treasury.cash
    resolve_bribe(w, "f1", 30000, scripted(OFFER), rng)
    assert w.treasury.cash == t + 30000


# Paying the penalty.


def test_default_is_to_pay_the_penalty(fraud_world, rng):
    w = fraud_world; t = w.treasury.cash; owner_cash = w.people[w.firms["f1"].owner_id].cash
    decide, seen = _recorder()
    evs = resolve_bribe(w, "f1", 30000, decide, rng)
    assert [d.kind for d in seen] == ["audit_response"]
    assert [e.kind for e in evs] == ["penalty_paid"]
    assert w.treasury.cash == t + 30000
    assert w.people[w.firms["f1"].owner_id].cash == owner_cash


def test_settled_penalty_marks_the_fraud_detected_and_known_to_the_auditor(fraud_world, rng):
    w = fraud_world
    resolve_bribe(w, "f1", 30000, scripted({}), rng)
    assert _fraud(w).payload["detected"] is True
    assert w.regulator.auditor_id in _fraud(w).known_by


def test_settled_penalty_removes_the_firm_from_press_tips(fraud_world, rng):
    resolve_bribe(fraud_world, "f1", 30000, scripted({}), rng)
    assert fraud_world.newspaper.press_tips == ["f2"]


# Offering a payment.


def test_accepted_bribe_details(fraud_world, rng):
    w = fraud_world
    owner_id, auditor_id = w.firms["f1"].owner_id, w.regulator.auditor_id
    firm_cash = w.firms["f1"].cash
    evs = resolve_bribe(w, "f1", 30000, scripted(OFFER), rng)
    assert [e.kind for e in evs] == ["offer_bribe", "accept_bribe"]
    offer, accept = evs
    assert offer.actor_id == owner_id and offer.visible is False
    assert accept.actor_id == auditor_id and accept.visible is False
    assert accept.harm == {"treasury": 30000}
    assert accept.payload["amount"] == 15000
    assert w.people[owner_id].cash == 50000 - 15000
    assert w.firms["f1"].cash == firm_cash
    info = w.infos["info:14:bribery:f1"]
    assert info.kind == "bribery" and info.subject_id == "f1" and info.step == 14
    assert info.known_by == {owner_id, auditor_id}
    assert accept.payload["info_id"] == info.id


def test_accepted_bribe_leaves_the_fraud_undetected_but_known_to_the_auditor(fraud_world, rng):
    w = fraud_world
    resolve_bribe(w, "f1", 30000, scripted(OFFER), rng)
    assert "detected" not in _fraud(w).payload
    assert w.regulator.auditor_id in _fraud(w).known_by
    assert w.newspaper.press_tips == ["f2", "f1"]


def test_bribery_is_legal_when_the_law_says_so(fraud_world, rng):
    fraud_world.laws.illegal = {"underreport_tax"}
    evs = resolve_bribe(fraud_world, "f1", 30000, scripted(OFFER), rng)
    assert all(e.legal for e in evs)


def test_refused_bribe_settles_and_marks_detected(fraud_world, rng):
    w = fraud_world; owner_cash = w.people[w.firms["f1"].owner_id].cash
    evs = resolve_bribe(w, "f1", 30000, scripted(REFUSE), rng)
    assert [e.kind for e in evs] == ["offer_bribe", "penalty_paid"]
    assert evs[1].payload["penalty"] == 45000
    assert _fraud(w).payload["detected"] is True
    assert w.regulator.auditor_id in _fraud(w).known_by
    assert w.newspaper.press_tips == ["f2"]
    assert w.people[w.firms["f1"].owner_id].cash == owner_cash
    assert not any(i.kind == "bribery" for i in w.infos.values())


def test_unaffordable_offer_is_a_hidden_rejection_and_the_auditor_is_not_asked(fraud_world, rng):
    w = fraud_world; w.people[w.firms["f1"].owner_id].cash = 14999
    decide, seen = _recorder(OFFER)
    evs = resolve_bribe(w, "f1", 30000, decide, rng)
    assert [d.kind for d in seen] == ["audit_response"]
    assert [e.kind for e in evs] == ["action_rejected", "penalty_paid"]
    assert evs[0].visible is False and evs[0].payload["action"] == "offer_bribe"
    assert evs[1].payload["penalty"] == 30000
    assert _fraud(w).payload["detected"] is True
    assert w.newspaper.press_tips == ["f2"]


def test_owner_who_no_longer_owns_an_open_firm_pays_the_plain_penalty(fraud_world, rng):
    w = fraud_world
    close_firm(w, "f1")
    w.firms["f1"].cash = 100000
    t = w.treasury.cash
    decide, seen = _recorder(OFFER)
    evs = resolve_bribe(w, "f1", 30000, decide, rng)
    assert seen == []
    assert [e.kind for e in evs] == ["penalty_paid"]
    assert w.treasury.cash == t + 30000
    assert _fraud(w).payload["detected"] is True


def test_money_is_conserved_on_every_path(fraud_world):
    for script in ({}, OFFER, REFUSE):
        w = fraud_world.model_copy(deep=True)
        before = total_money(w)
        resolve_bribe(w, "f1", 30000, scripted(script), random.Random(0))
        assert total_money(w) == before


# The decisions.


def test_audit_response_decision(fraud_world, rng):
    w = fraud_world; owner_id = w.firms["f1"].owner_id
    decide, seen = _recorder()
    resolve_bribe(w, "f1", 30000, decide, rng)
    (d,) = seen
    assert d.id == f"14:{owner_id}:audit_response:0"
    assert d.actor_id == owner_id and d.kind == "audit_response"
    assert d.default_option_id == "pay_penalty"
    assert [o.id for o in d.options] == ["pay_penalty", "offer_bribe"]
    assert d.options[0].action is None
    assert action_from_dict(d.options[1].action) == OfferBribe(firm_id="f1", amount=15000)
    assert "$300.00" in d.situation and "$150.00" in d.situation
    assert w.firms["f1"].name in d.situation
    assert_prompt_clean(seen)


def test_bribe_offer_decision(fraud_world, rng):
    w = fraud_world; auditor_id = w.regulator.auditor_id
    decide, seen = _recorder({"audit_response": "offer_bribe"})
    resolve_bribe(w, "f1", 30000, decide, rng)
    d = seen[1]
    assert d.id == f"14:{auditor_id}:bribe_offer:0"
    assert d.actor_id == auditor_id and d.kind == "bribe_offer"
    assert d.default_option_id == "refuse"
    assert [o.id for o in d.options] == ["refuse", "accept"]
    assert d.options[0].action is None
    assert action_from_dict(d.options[1].action) == AcceptBribe(
        firm_id="f1", amount=15000, penalty=30000
    )
    assert "$150.00" in d.situation and "$10.00" in d.situation
    assert_prompt_clean(seen)


def test_both_situations_state_the_declined_amount(fraud_world, rng):
    decide, seen = _recorder({"audit_response": "offer_bribe"})
    resolve_bribe(fraud_world, "f1", 30000, decide, rng)
    assert all("$450.00" in d.situation for d in seen) and len(seen) == 2


def _situations(w: World) -> tuple[str, str]:
    decide, seen = _recorder({"audit_response": "offer_bribe"})
    resolve_bribe(w, "f1", 30000, decide, random.Random(0))
    return seen[0].situation, seen[1].situation


def test_situations_state_the_law_as_it_stands(fraud_world):
    illegal = fraud_world.model_copy(deep=True)
    legal = fraud_world.model_copy(deep=True)
    legal.laws.illegal = {"underreport_tax"}
    (owner_illegal, auditor_illegal), (owner_legal, auditor_legal) = (
        _situations(illegal), _situations(legal)
    )
    assert "Offering the auditor a payment is illegal." in owner_illegal
    assert "Offering the auditor a payment is not against the law." in owner_legal
    assert "Accepting a payment from an audited owner is illegal." in auditor_illegal
    assert "Accepting a payment from an audited owner is not against the law." in auditor_legal


def test_unoffered_option_is_an_error(fraud_world, rng):
    with pytest.raises(ValueError):
        resolve_bribe(fraud_world, "f1", 30000, scripted({"audit_response": "nope"}), rng)


def test_baseline_rules_pay_and_refuse(fraud_world, rng):
    w = fraud_world; t = w.treasury.cash
    rules = RuleDecider(random.Random(0))
    seen: list[Decision] = []

    def decide(d: Decision) -> Choice:
        seen.append(d)
        if d.kind == "audit_response":
            return Choice(option_id="offer_bribe")  # force the offer so the auditor's rule runs
        return rules.decide(d)

    resolve_bribe(w, "f1", 30000, decide, rng)
    assert rules.decide(seen[0]).option_id == "pay_penalty"
    assert rules.decide(seen[1]).option_id == "refuse"
    assert w.treasury.cash == t + 45000


# Through the audit phase.


def test_audit_phase_offers_the_owner_a_response_on_detection(fraud_world):
    w = fraud_world; t = w.treasury.cash
    aud = w.people[w.regulator.auditor_id]; a0 = aud.cash
    evs = audit_phase(w, scripted({"audit": "deep:f1", **OFFER}), _Draw(0.0))
    assert [e.kind for e in evs] == ["audit", "audit_detected", "offer_bribe", "accept_bribe"]
    assert aud.cash == a0 + 15000 and w.treasury.cash == t


def test_audit_phase_default_still_settles_the_penalty(fraud_world):
    w = fraud_world; t = w.treasury.cash
    evs = audit_phase(w, scripted({"audit": "deep:f1"}), _Draw(0.0))
    assert [e.kind for e in evs] == ["audit", "audit_detected", "penalty_paid"]
    assert w.treasury.cash == t + 30000


def test_detection_is_hidden_until_a_penalty_is_paid(fraud_world):
    w = fraud_world
    evs = audit_phase(w, scripted({"audit": "deep:f1"}), _Draw(0.0))
    detected = next(e for e in evs if e.kind == "audit_detected")
    assert detected.visible is False
    assert next(e for e in evs if e.kind == "penalty_paid").visible is True


def test_after_an_accepted_bribe_no_visible_event_names_the_detection(fraud_world):
    w = fraud_world
    evs = audit_phase(w, scripted({"audit": "deep:f1", **OFFER}), _Draw(0.0))
    visible = [e for e in evs if e.visible]
    assert [e.kind for e in visible] == ["audit"]
    clean_world = fraud_world.model_copy(deep=True)
    del clean_world.infos["info:13:tax_fraud:f1"]
    clean = audit_phase(clean_world, scripted({"audit": "deep:f1"}), _Draw(0.0))
    assert [e.kind for e in clean] == ["audit", "audit_clean"]
    assert [e.kind for e in clean if e.visible] == [e.kind for e in visible]
    assert [e.payload for e in clean if e.visible] == [e.payload for e in visible]
    assert not any(k in e.payload for e in visible for k in ("penalty", "unpaid", "info_id"))


def test_audit_outcomes_are_private(fraud_world):
    evs = audit_phase(fraud_world, scripted({"audit": "light:f2"}), _Draw(0.0))
    assert [(e.kind, e.visible) for e in evs] == [("audit", True), ("audit_clean", False)]
