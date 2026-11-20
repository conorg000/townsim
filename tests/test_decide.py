import pytest

from townsim.decide import (
    RULES,
    Choice,
    Decision,
    ModelUnavailable,
    Option,
    RecordingDecider,
    ReplayDecider,
    RuleDecider,
    ScriptedDecider,
    rule,
)


# Rules registered in these tests use kinds no production code registers.
PROBE_KIND = "probe_kind"


class Unavailable:
    def decide(self, decision: Decision) -> Choice:
        raise ModelUnavailable("no answer")


class WithMeta:
    def __init__(self, option_id: str):
        self.option_id = option_id

    def decide(self, decision: Decision) -> Choice:
        return Choice(option_id=self.option_id, meta={"tokens": 7})


def test_recording_decider_substitutes_default_for_unknown_option(pricing_decision):
    sink = []
    d = RecordingDecider(ScriptedDecider({"pricing": "nonsense"}), sink)
    assert d.decide(pricing_decision).option_id == "hold"
    assert [e.kind for e in sink] == ["invalid_response", "decision"]


def test_replay_decider_returns_recorded_choice_and_raises_when_missing(pricing_decision):
    r = ReplayDecider({"0:p0:pricing:0": "up5"})
    assert r.decide(pricing_decision).option_id == "up5"
    with pytest.raises(KeyError):
        r.decide(pricing_decision.model_copy(update={"id": "1:p0:pricing:0"}))


def test_replay_decider_key_error_names_the_decision_id(pricing_decision):
    with pytest.raises(KeyError) as excinfo:
        ReplayDecider({}).decide(pricing_decision)
    assert excinfo.value.args == ("0:p0:pricing:0",)


def test_rule_decider_uses_registered_rule_else_default(rng, unknown_kind_decision):
    assert (
        RuleDecider(rng).decide(unknown_kind_decision).option_id
        == unknown_kind_decision.default_option_id
    )


def test_rule_decider_calls_registered_rule_with_decision_and_rng(rng, pricing_decision):
    seen = []
    decision = pricing_decision.model_copy(update={"kind": PROBE_KIND})

    @rule(PROBE_KIND)
    def probe(d, r):
        seen.append((d, r))
        return "up10" if d.facts["price"] < 200 else "down10"

    assert RuleDecider(rng).decide(decision).option_id == "up10"
    assert seen == [(decision, rng)]


def test_rule_decorator_returns_function_and_registers_it():
    def fn(decision, r):
        return "a"

    assert rule("some_kind")(fn) is fn
    assert RULES["some_kind"] is fn


def test_rule_registered_twice_for_same_kind_is_an_error():
    @rule(PROBE_KIND)
    def first(decision, r):
        return "hold"

    with pytest.raises(ValueError, match=PROBE_KIND):

        @rule(PROBE_KIND)
        def second(decision, r):
            return "up5"

    assert RULES[PROBE_KIND] is first


def test_rule_registration_does_not_leak_between_tests():
    assert PROBE_KIND not in RULES
    assert "some_kind" not in RULES


def test_scripted_decider_uses_script_else_default(pricing_decision, unknown_kind_decision):
    d = ScriptedDecider({"pricing": "down5"})
    assert d.decide(pricing_decision).option_id == "down5"
    assert d.decide(unknown_kind_decision).option_id == "b"


def test_recording_decider_records_valid_choice(pricing_decision):
    sink = []
    choice = RecordingDecider(WithMeta("up5"), sink).decide(pricing_decision)
    assert choice.option_id == "up5"
    assert len(sink) == 1
    (event,) = sink
    assert event.kind == "decision"
    assert event.actor_id == "p0"
    assert event.payload == {
        "decision_id": "0:p0:pricing:0",
        "decision_kind": "pricing",
        "option_id": "up5",
        "meta": {"tokens": 7},
    }


def test_recording_decider_invalid_response_payload(pricing_decision):
    sink = []
    RecordingDecider(WithMeta("nonsense"), sink).decide(pricing_decision)
    invalid, decision = sink
    assert invalid.actor_id == "p0"
    assert invalid.payload == {"decision_id": "0:p0:pricing:0", "option_id": "nonsense"}
    assert decision.payload["option_id"] == "hold"


def test_recording_decider_treats_empty_option_id_as_invalid(pricing_decision):
    sink = []
    choice = RecordingDecider(WithMeta(""), sink).decide(pricing_decision)
    assert choice.option_id == "hold"
    assert [e.kind for e in sink] == ["invalid_response", "decision"]
    assert sink[0].payload["option_id"] == ""


def test_recording_decider_lets_model_unavailable_propagate_and_records_nothing(
    pricing_decision,
):
    sink = []
    with pytest.raises(ModelUnavailable):
        RecordingDecider(Unavailable(), sink).decide(pricing_decision)
    assert sink == []


def test_recorded_decisions_replay_to_the_same_choices(pricing_decision, unknown_kind_decision):
    sink = []
    recorder = RecordingDecider(ScriptedDecider({"pricing": "up10"}), sink)
    decisions = [pricing_decision, unknown_kind_decision]
    originals = [recorder.decide(d).option_id for d in decisions]
    recorded = {
        e.payload["decision_id"]: e.payload["option_id"] for e in sink if e.kind == "decision"
    }
    replay = ReplayDecider(recorded)
    assert [replay.decide(d).option_id for d in decisions] == originals


def test_option_and_choice_defaults():
    assert Option(id="x", label="X").action is None
    assert Choice(option_id="x").meta == {}
    assert Choice(option_id="x").meta is not Choice(option_id="y").meta


def test_recorded_decisions_and_invalid_responses_are_hidden(pricing_decision):
    # A choice is a private act; the action events it causes carry their own visibility.
    sink = []
    RecordingDecider(WithMeta("nonsense"), sink).decide(pricing_decision)
    RecordingDecider(WithMeta("up5"), sink).decide(pricing_decision)
    assert [e.kind for e in sink] == ["invalid_response", "decision", "decision"]
    assert all(e.visible is False for e in sink)


def _options(*ids):
    return [Option(id=i, label=i.upper()) for i in ids]


def test_a_decision_must_offer_its_default():
    with pytest.raises(ValueError, match="default"):
        Decision(id="0:p0:k:0", actor_id="p0", kind="k", situation="Choose.",
                 options=_options("a", "b"), default_option_id="c")


def test_a_decision_must_not_offer_the_same_option_id_twice():
    with pytest.raises(ValueError, match="duplicate"):
        Decision(id="0:p0:k:0", actor_id="p0", kind="k", situation="Choose.",
                 options=_options("a", "b", "a"), default_option_id="a")
