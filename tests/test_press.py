import random

import pytest

from helpers import assert_prompt_clean, scripted
from townsim.actions import execute
from townsim.decide import RULES, Choice, Decision
from townsim.events import state_hash
from townsim.market import close_firm
from townsim.press import Publish, press_phase, tips_phase
from townsim.tax import tax_phase
from townsim.world import Info, World, total_money


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


def _fraud(w: World) -> Info:
    return w.infos["info:13:tax_fraud:f1"]


def _first_employee(w: World) -> str:
    return w.firms["f1"].employee_ids[0]


def _journalist(w: World) -> str:
    return sorted(w.newspaper.journalist_ids)[0]


# The brief's tests.


def test_heavy_concealment_keeps_employees_in_the_dark(one_firm_world, rng):
    w = one_firm_world; w.step = 13; w.firms["f1"].profit_qtd = 100000
    tax_phase(w, scripted({"tax_report": "hide50_heavy"}), rng)
    info = next(iter(w.infos.values()))
    assert info.known_by == {w.firms["f1"].owner_id}


def test_worker_who_knows_can_tip_the_auditor_once(known_fraud_world, rng):
    w = known_fraud_world                # f1's first employee knows about f1
    tips_phase(w, scripted({"tip": "tip_auditor"}), rng)
    tips_phase(w, scripted({"tip": "tip_auditor"}), rng)
    assert w.regulator.tips == ["f1"]


def test_publishing_tells_everyone_and_hurts_loyalty(known_fraud_world, rng):
    w = known_fraud_world
    w.params["scandal_loyalty_hit"] = 0.3
    for p in w.people.values():
        p.loyalty.pop("f1", None)
    info = next(iter(w.infos.values())); info.known_by.add(w.newspaper.journalist_ids[0])
    press_phase(w, scripted({"investigate": "none", "publish": "publish"}), rng)
    assert info.known_by == set(w.people)
    assert all(p.loyalty["f1"] == pytest.approx(-0.3) for p in w.people.values())
    assert "f1" in w.regulator.tips


def test_investigating_a_clean_firm_finds_nothing(world, rng):
    evs = press_phase(world, scripted({"investigate": "firm:f1"}), rng)
    assert world.infos == {} and not any(e.kind == "publish" for e in evs)


# Employees learning of a fraud.


def _file_fraud(w: World, option: str, rng: random.Random) -> Info:
    w.step = 13
    w.firms["f1"].profit_qtd = 100000
    tax_phase(w, scripted({"tax_report": option}), rng)
    return w.infos["info:13:tax_fraud:f1"]


@pytest.mark.parametrize(
    "option, draw, learns",
    [
        ("hide50_none", 0.49, True),
        ("hide50_none", 0.5, False),
        ("hide50_some", 0.19, True),
        ("hide50_some", 0.2, False),
        ("hide50_heavy", 0.0, False),
    ],
)
def test_each_employee_learns_with_the_concealment_probability(one_firm_world, option, draw, learns):
    w = one_firm_world
    f1 = w.firms["f1"]
    info = _file_fraud(w, option, _Draw(draw))
    expected = {f1.owner_id} | (set(f1.employee_ids) if learns else set())
    assert info.known_by == expected


class _Counting(random.Random):
    """Counts `random()` draws and returns the next of `values` (cycling)."""

    def __init__(self, values: list[float]):
        super().__init__(0)
        self.values = values
        self.calls = 0

    def random(self) -> float:
        v = self.values[self.calls % len(self.values)]
        self.calls += 1
        return v


def test_one_draw_per_employee_in_order(one_firm_world):
    w = one_firm_world
    staff = list(w.firms["f1"].employee_ids)
    rng = _Counting([0.1, 0.9])
    info = _file_fraud(w, "hide50_none", rng)
    assert rng.calls == len(staff)
    assert info.known_by - {w.firms["f1"].owner_id} == set(staff[0::2])


def test_heavy_concealment_makes_no_draws(one_firm_world):
    rng = _Counting([0.0])
    _file_fraud(one_firm_world, "hide50_heavy", rng)
    assert rng.calls == 0


# Tips.


def test_tip_decision_is_offered_once_to_a_worker_who_knows(known_fraud_world, rng):
    w = known_fraud_world
    decide, seen = _recorder()
    evs = tips_phase(w, decide, rng)
    worker = _first_employee(w)
    assert [d.actor_id for d in seen] == [worker]
    d = seen[0]
    assert d.id == f"14:{worker}:tip:0" and d.kind == "tip"
    assert [o.id for o in d.options] == ["silent", "tip_auditor", "tip_press"]
    assert d.default_option_id == "silent"
    assert d.facts["scruples"] == w.people[worker].scruples
    assert evs == [] and w.regulator.tips == [] and w.newspaper.press_tips == []
    assert worker in _fraud(w).decided_by
    tips_phase(w, decide, rng)
    assert len(seen) == 1
    assert_prompt_clean(seen)


def test_tip_situation_states_only_what_the_worker_knows(known_fraud_world, rng):
    w = known_fraud_world
    decide, seen = _recorder()
    tips_phase(w, decide, rng)
    text = seen[0].situation
    assert w.firms["f1"].name in text and "last quarter" in text
    assert "$100.00" not in text      # the unpaid amount is not something the worker knows


def test_tip_press_goes_to_the_newspaper_and_is_hidden(known_fraud_world, rng):
    w = known_fraud_world
    evs = tips_phase(w, scripted({"tip": "tip_press"}), rng)
    assert w.newspaper.press_tips == ["f1"] and w.regulator.tips == []
    assert [e.kind for e in evs] == ["tip_press"] and evs[0].visible is False
    assert evs[0].payload == {"firm_id": "f1", "info_id": "info:13:tax_fraud:f1"}


def test_tip_auditor_event_is_hidden(known_fraud_world, rng):
    evs = tips_phase(known_fraud_world, scripted({"tip": "tip_auditor"}), rng)
    assert [e.kind for e in evs] == ["tip_auditor"] and evs[0].visible is False


def test_tips_never_duplicate_a_firm(known_fraud_world, rng):
    w = known_fraud_world
    w.regulator.tips = ["f1"]
    w.newspaper.press_tips = ["f1"]
    second = w.firms["f1"].employee_ids[1]
    _fraud(w).known_by.add(second)
    tips_phase(w, scripted({"tip": "tip_auditor"}), rng)
    for e in w.firms["f1"].employee_ids[:2]:
        _fraud(w).decided_by.discard(e)
    tips_phase(w, scripted({"tip": "tip_press"}), rng)
    assert w.regulator.tips == ["f1"] and w.newspaper.press_tips == ["f1"]


@pytest.mark.parametrize("flag", ["detected", "published"])
def test_detected_or_published_fraud_is_not_offered_for_tipping(known_fraud_world, rng, flag):
    w = known_fraud_world
    _fraud(w).payload[flag] = True
    decide, seen = _recorder()
    tips_phase(w, decide, rng)
    assert seen == []


def test_only_workers_of_the_firm_are_asked(known_fraud_world, rng):
    w = known_fraud_world
    info = _fraud(w)
    outsider = w.firms["f2"].employee_ids[0]
    info.known_by.update({outsider, w.regulator.auditor_id})
    decide, seen = _recorder()
    tips_phase(w, decide, rng)
    assert [d.actor_id for d in seen] == [_first_employee(w)]   # not the owner, outsider or auditor


def test_workers_are_asked_in_person_id_order(known_fraud_world, rng):
    w = known_fraud_world
    staff = w.firms["f1"].employee_ids
    _fraud(w).known_by.update(staff)
    decide, seen = _recorder()
    tips_phase(w, decide, rng)
    assert [d.actor_id for d in seen] == sorted(staff)


def test_worker_at_a_closed_firm_is_not_asked(known_fraud_world, rng):
    w = known_fraud_world
    close_firm(w, "f1")
    decide, seen = _recorder()
    tips_phase(w, decide, rng)
    assert seen == []


@pytest.mark.parametrize("scruples, expected", [(0.61, "tip_auditor"), (0.6, "silent")])
def test_tip_rule_follows_scruples(known_fraud_world, rng, scruples, expected):
    w = known_fraud_world
    w.people[_first_employee(w)].scruples = scruples
    decide, seen = _recorder()
    tips_phase(w, decide, rng)
    assert RULES["tip"](seen[0], random.Random(0)) == expected


# The newspaper.


def test_each_journalist_gets_an_investigate_decision_in_id_order(world, rng):
    close_firm(world, "f5")
    decide, seen = _recorder()
    press_phase(world, decide, rng)
    journalists = sorted(world.newspaper.journalist_ids)
    assert [d.actor_id for d in seen] == journalists
    d = seen[0]
    assert d.id == f"0:{journalists[0]}:investigate:0" and d.default_option_id == "none"
    assert [o.id for o in d.options] == ["none", "firm:f1", "firm:f2", "firm:f3", "firm:f4"]
    assert_prompt_clean(seen)


def test_investigation_success_reveals_the_fraud(fraud_world):
    w = fraud_world
    j = _journalist(w)
    decide, seen = _recorder({"investigate": "firm:f1"})
    evs = press_phase(w, decide, _Draw(0.14))
    assert j in _fraud(w).known_by
    found = [e for e in evs if e.kind == "investigation_found"]
    assert found[0].payload == {"firm_id": "f1", "info_id": "info:13:tax_fraud:f1"}
    assert found[0].visible is False
    publish = [d for d in seen if d.kind == "publish"]
    assert publish[0].actor_id == j                    # can publish in the same step
    assert [o.id for o in publish[0].options] == ["publish", "hold"]
    assert publish[0].default_option_id == "hold"
    assert_prompt_clean(publish)
    assert not any(e.kind == "publish" for e in evs)   # held by default


@pytest.mark.parametrize("tipped, draw, found", [
    (False, 0.15, False), (True, 0.59, True), (True, 0.6, False),
])
def test_investigation_success_probability(fraud_world, tipped, draw, found):
    w = fraud_world
    if tipped:
        w.newspaper.press_tips = ["f1"]
    evs = press_phase(w, scripted({"investigate": "firm:f1"}), _Draw(draw))
    assert (_journalist(w) in _fraud(w).known_by) is found
    kinds = [e.kind for e in evs]
    assert ("investigation_found" in kinds) is found
    assert ("investigation_empty" in kinds) is not found


def test_investigating_a_clean_firm_emits_empty_and_hidden(world):
    evs = press_phase(world, scripted({"investigate": "firm:f1"}), _Draw(0.0))
    empty = [e for e in evs if e.kind == "investigation_empty"]
    assert len(empty) == 2 and all(e.payload == {"firm_id": "f1"} and not e.visible for e in empty)


@pytest.mark.parametrize("flag", ["detected", "published"])
def test_investigation_does_not_find_a_detected_or_published_fraud(fraud_world, flag):
    w = fraud_world
    _fraud(w).payload[flag] = True
    decide, seen = _recorder({"investigate": "firm:f1", "publish": "publish"})
    evs = press_phase(w, decide, _Draw(0.0))
    assert not any(e.kind == "investigation_found" for e in evs)
    assert not any(d.kind == "publish" for d in seen)


def test_investigation_finds_the_most_recent_undetected_fraud(fraud_world):
    w = fraud_world
    w.infos["info:26:tax_fraud:f1"] = Info(
        id="info:26:tax_fraud:f1", step=26, kind="tax_fraud", subject_id="f1",
        payload={"unpaid": 4000, "concealment": "none", "quarter": 2},
        known_by={w.firms["f1"].owner_id},
    )
    w.step = 27
    evs = press_phase(w, scripted({"investigate": "firm:f1"}), _Draw(0.0))
    found = [e for e in evs if e.kind == "investigation_found"]
    assert found[0].payload["info_id"] == "info:26:tax_fraud:f1"
    assert _journalist(w) not in _fraud(w).known_by


def test_publishing_marks_it_published_clears_the_press_tip_and_is_visible(known_fraud_world, rng):
    w = known_fraud_world
    w.params["scandal_loyalty_hit"] = 0.3
    w.newspaper.press_tips = ["f2", "f1"]
    w.regulator.tips = ["f1"]
    _fraud(w).known_by.add(_journalist(w))
    money = total_money(w)
    evs = press_phase(w, scripted({"publish": "publish"}), rng)
    pub = [e for e in evs if e.kind == "publish"]
    assert len(pub) == 1 and pub[0].visible is True
    assert pub[0].payload == {"info_id": "info:13:tax_fraud:f1", "firm_id": "f1"}
    assert _fraud(w).payload["published"] is True
    assert w.newspaper.press_tips == ["f2"] and w.regulator.tips == ["f1"]
    assert total_money(w) == money


def test_published_story_is_not_offered_again(known_fraud_world, rng):
    w = known_fraud_world
    w.params["scandal_loyalty_hit"] = 0.3
    for j in w.newspaper.journalist_ids:
        _fraud(w).known_by.add(j)
    decide, seen = _recorder({"publish": "publish"})
    press_phase(w, decide, rng)
    assert [d.kind for d in seen].count("publish") == 1
    press_phase(w, decide, rng)
    assert [d.kind for d in seen].count("publish") == 1


def test_loyalty_hit_floors_at_minus_one(known_fraud_world, rng):
    w = known_fraud_world
    w.params["scandal_loyalty_hit"] = 0.3
    first, second = sorted(w.people)[:2]
    w.people[first].loyalty["f1"] = -0.9
    w.people[second].loyalty["f1"] = 0.25
    _fraud(w).known_by.add(_journalist(w))
    press_phase(w, scripted({"publish": "publish"}), rng)
    assert w.people[first].loyalty["f1"] == -1.0
    assert w.people[second].loyalty["f1"] == pytest.approx(-0.05)


def test_publish_is_rejected_for_someone_who_does_not_know(fraud_world, rng):
    w = fraud_world
    evs = execute(w, _journalist(w), Publish(info_id="info:13:tax_fraud:f1"), rng)
    assert evs[0].kind == "action_rejected"
    assert "published" not in _fraud(w).payload


def test_investigate_rule_prefers_a_tipped_firm_else_picks_by_rng(world, rng):
    world.newspaper.press_tips = ["f3"]
    decide, seen = _recorder()
    press_phase(world, decide, rng)
    assert RULES["investigate"](seen[0], random.Random(0)) == "firm:f3"
    world.newspaper.press_tips = []
    decide, seen = _recorder()
    press_phase(world, decide, rng)
    picks = {RULES["investigate"](seen[0], random.Random(s)) for s in range(20)}
    assert picks <= {f"firm:{fid}" for fid in world.firms} and len(picks) > 1


def test_investigate_rule_with_no_open_firms_picks_none(world, rng):
    for fid in sorted(world.firms):
        close_firm(world, fid)
    decide, seen = _recorder()
    press_phase(world, decide, rng)
    assert [o.id for o in seen[0].options] == ["none"]
    assert RULES["investigate"](seen[0], random.Random(0)) == "none"


def test_publish_rule_publishes(known_fraud_world, rng):
    w = known_fraud_world
    _fraud(w).known_by.add(_journalist(w))
    decide, seen = _recorder()
    press_phase(w, decide, rng)
    publish = next(d for d in seen if d.kind == "publish")
    assert RULES["publish"](publish, random.Random(0)) == "publish"


def test_new_fields_are_in_the_state_hash(fraud_world):
    before = state_hash(fraud_world)
    _fraud(fraud_world).decided_by.update({"p09", "p07"})
    after = state_hash(fraud_world)
    assert after != before
    fraud_world.newspaper.press_tips.append("f1")
    assert state_hash(fraud_world) != after
