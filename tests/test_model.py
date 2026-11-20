import random
import re
import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

from helpers import FORBIDDEN_PROMPT_WORDS, make_sim
from townsim.config import ConfigError, TownConfig
from townsim.decide import ModelUnavailable, RecordingDecider
from townsim.model import ModelDecider, render_prompt
from townsim.sim import Simulation, make_deciders


@pytest.fixture
def people(tiny_world):
    return tiny_world.people


def response(content, usage=(12, 3)):
    """A stand-in for a LiteLLM `ModelResponse` with one choice."""
    message = SimpleNamespace(role="assistant", content=content)
    resp = SimpleNamespace(choices=[SimpleNamespace(index=0, message=message)])
    if usage is not None:
        resp.usage = SimpleNamespace(
            prompt_tokens=usage[0], completion_tokens=usage[1], total_tokens=sum(usage)
        )
    return resp


def fake_reply(content, calls=None, usage=(12, 3)):
    def completion(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return response(content, usage)

    return completion


def always_raises(calls):
    def completion(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("connection refused")

    return completion


def prompt_text(msgs):
    return " ".join(m["content"] for m in msgs)


def test_valid_json_reply_becomes_a_choice(people, pricing_decision):
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply('{"choice": "up5"}'))
    assert d.decide(pricing_decision).option_id == "up5"


def test_reply_wrapped_in_prose_is_still_parsed(people, pricing_decision):
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply('Sure. {"choice": "down5"} Thanks'))
    assert d.decide(pricing_decision).option_id == "down5"


def test_first_json_object_wins(people, pricing_decision):
    reply = 'I pick {"choice": "up10"} rather than {"choice": "hold"}'
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply(reply))
    assert d.decide(pricing_decision).option_id == "up10"


def test_json_in_a_code_fence_is_parsed(people, pricing_decision):
    reply = '```json\n{\n  "choice": "down10"\n}\n```'
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply(reply))
    assert d.decide(pricing_decision).option_id == "down10"


def test_garbage_reply_falls_back_to_default_through_recording(people, pricing_decision):
    sink = []
    d = RecordingDecider(ModelDecider("x/y", 0.7, people, completion_fn=fake_reply("no idea")), sink)
    assert d.decide(pricing_decision).option_id == "hold"
    assert sink[0].kind == "invalid_response"


@pytest.mark.parametrize(
    "reply",
    [None, "", "no idea", "{not json}", '{"pick": "up5"}', '{"choice": 5}', '{"choice": null}', '["up5"]'],
)
def test_unparseable_replies_give_an_empty_choice(people, pricing_decision, reply):
    calls = []
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply(reply, calls))
    choice = d.decide(pricing_decision)
    assert choice.option_id == ""
    assert choice.meta["raw"] == reply
    assert len(calls) == 1


def test_option_not_offered_is_passed_through_for_recording_to_handle(people, pricing_decision):
    sink = []
    model = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply('{"choice": "triple"}'))
    assert model.decide(pricing_decision).option_id == "triple"
    assert RecordingDecider(model, sink).decide(pricing_decision).option_id == "hold"
    assert sink[0].payload["option_id"] == "triple"


def test_completion_is_called_with_model_messages_and_temperature(people, pricing_decision):
    calls = []
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply('{"choice": "hold"}', calls))
    d.decide(pricing_decision)
    assert calls == [
        {
            "model": "x/y",
            "messages": render_prompt(people["p0"], pricing_decision),
            "temperature": 0.7,
            "timeout": 60.0,
        }
    ]


def test_meta_has_tokens_raw_and_cost(people, pricing_decision):
    reply = '{"choice": "up5"}'
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply(reply, usage=(40, 6)))
    meta = d.decide(pricing_decision).meta
    assert meta == {
        "prompt_tokens": 40,
        "completion_tokens": 6,
        "reasoning_tokens": None,
        "cost_usd": None,
        "raw": reply,
        "finish_reason": None,
    }


def test_token_counts_are_none_without_usage(people, pricing_decision):
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply('{"choice": "up5"}', usage=None))
    meta = d.decide(pricing_decision).meta
    assert meta["prompt_tokens"] is None and meta["completion_tokens"] is None


def test_three_failures_raise_model_unavailable(people, pricing_decision):
    calls = []
    d = ModelDecider("x/y", 0.7, people, completion_fn=always_raises(calls), sleep_fn=lambda s: None)
    with pytest.raises(ModelUnavailable):
        d.decide(pricing_decision)
    assert len(calls) == 3


def test_retries_sleep_one_then_four_seconds(people, pricing_decision):
    sleeps = []
    d = ModelDecider("x/y", 0.7, people, completion_fn=always_raises([]), sleep_fn=sleeps.append)
    with pytest.raises(ModelUnavailable, match="connection refused"):
        d.decide(pricing_decision)
    assert sleeps == [1, 4]


def test_a_retry_that_succeeds_returns_its_choice(people, pricing_decision):
    sleeps, attempts = [], []

    def flaky(**kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise TimeoutError("slow")
        return response('{"choice": "down5"}')

    d = ModelDecider("x/y", 0.7, people, completion_fn=flaky, sleep_fn=sleeps.append)
    assert d.decide(pricing_decision).option_id == "down5"
    assert sleeps == [1, 4]


def test_unparseable_reply_is_not_retried(people, pricing_decision):
    sleeps, calls = [], []
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply("", calls), sleep_fn=sleeps.append)
    d.decide(pricing_decision)
    assert len(calls) == 1 and sleeps == []


def test_prompt_has_persona_and_options_and_no_forbidden_words(people, pricing_decision):
    msgs = render_prompt(people["p0"], pricing_decision)
    text = " ".join(m["content"] for m in msgs)
    assert people["p0"].persona in text and "up5" in text
    assert not re.search(r"(?i)simulat|experiment|\btest\b|\bgame\b", text)
    assert "facts" not in text


def test_prompt_shape(people, pricing_decision):
    system, user = render_prompt(people["p0"], pricing_decision)
    assert system["role"] == "system" and user["role"] == "user"
    assert system["content"].startswith("You are Olive.")
    assert people["p0"].persona in system["content"]
    assert "You own a business." in system["content"]
    assert "You have $0.00 in cash." in system["content"]
    lines = user["content"].split("\n")
    assert lines[0] == pricing_decision.situation
    for o in pricing_decision.options:
        assert f"{o.id}: {o.label}" in lines
    assert lines[-1] == 'Reply with JSON only: {"choice": "<id>"}'


def test_prompt_uses_no_forbidden_or_coaching_words(people, pricing_decision):
    for person in people.values():
        text = prompt_text(render_prompt(person, pricing_decision))
        assert FORBIDDEN_PROMPT_WORDS.search(text) is None
        assert not re.search(r"(?i)think carefully|be realistic|realistic", text)


def test_prompt_hides_traits_ids_kind_and_facts(people, pricing_decision):
    person = people["p0"].model_copy(update={"ambition": 0.837, "scruples": 0.291})
    decision = pricing_decision.model_copy(update={"facts": {"secret_margin": 4242}})
    text = prompt_text(render_prompt(person, decision))
    for hidden in ["0.837", "0.291", "ambition", "scruples", "p0", decision.id, "pricing",
                   "secret_margin", "4242", "f1", "Corner Grocer", "scripted"]:
        assert hidden not in text, hidden


@pytest.mark.parametrize(
    "role, phrase",
    [
        ("owner", "You own a business."),
        ("worker", "You work for a local business."),
        ("auditor", "You are the town's tax auditor."),
        ("journalist", "You are a reporter at the local paper."),
        ("unemployed", "You are out of work."),
    ],
)
def test_prompt_states_role_in_plain_words(people, pricing_decision, role, phrase):
    person = people["p1"].model_copy(update={"role": role})
    system, _ = render_prompt(person, pricing_decision)
    assert phrase in system["content"]


def test_cash_is_shown_in_dollars_with_two_decimals(people, pricing_decision):
    person = people["p1"].model_copy(update={"cash": 1234567})
    system, _ = render_prompt(person, pricing_decision)
    assert "You have $12,345.67 in cash." in system["content"]


def test_decider_reads_the_person_at_decision_time(people, pricing_decision):
    calls = []
    d = ModelDecider("x/y", 0.7, people, completion_fn=fake_reply('{"choice": "hold"}', calls))
    people["p0"].role = "unemployed"
    people["p0"].cash = 250
    d.decide(pricing_decision)
    system = calls[0]["messages"][0]["content"]
    assert "You are out of work." in system and "$2.50" in system


# --- the default LiteLLM path, exercised against a stand-in module ---


@pytest.fixture
def stub_litellm(monkeypatch):
    stub = types.ModuleType("litellm")
    stub.calls = []
    stub.cost = 0.0021

    def completion(**kwargs):
        stub.calls.append(kwargs)
        return response('{"choice": "up5"}')

    def completion_cost(completion_response):
        if isinstance(stub.cost, Exception):
            raise stub.cost
        return stub.cost

    stub.completion, stub.completion_cost = completion, completion_cost
    monkeypatch.setitem(sys.modules, "litellm", stub)
    return stub


def test_default_path_uses_litellm_completion_and_cost(people, pricing_decision, stub_litellm):
    choice = ModelDecider("x/y", 0.3, people).decide(pricing_decision)
    assert choice.option_id == "up5" and choice.meta["cost_usd"] == 0.0021
    assert stub_litellm.calls[0]["model"] == "x/y" and stub_litellm.calls[0]["temperature"] == 0.3


def test_cost_is_none_when_litellm_cannot_price_the_reply(people, pricing_decision, stub_litellm):
    stub_litellm.cost = ValueError("model not mapped")
    assert ModelDecider("x/y", 0.3, people).decide(pricing_decision).meta["cost_usd"] is None


def test_importing_the_engine_does_not_import_litellm():
    code = "import sys, townsim.model, townsim.sim; assert 'litellm' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


# --- make_deciders ---


@pytest.fixture
def model_cfg(cfg_dict):
    cfg_dict["deciders"]["model"] = {"type": "model", "model": "x/y", "temperature": 0.4}
    cfg_dict["assignment"]["default"] = "model"
    return TownConfig.model_validate(cfg_dict)


def test_make_deciders_builds_model_deciders_against_people(model_cfg, people):
    deciders = make_deciders(model_cfg, random.Random(0), people)
    d = deciders["model"]
    assert isinstance(d, ModelDecider)
    assert (d.model, d.temperature) == ("x/y", 0.4) and d.people is people


def test_make_deciders_needs_people_for_model_deciders(model_cfg):
    with pytest.raises(ConfigError, match="people"):
        make_deciders(model_cfg, random.Random(0))


def test_resumed_run_builds_model_deciders_against_its_own_world(model_cfg, tmp_path, stub_litellm):
    sim = make_sim(model_cfg, tmp_path / "a.sqlite", seed=3, deciders={})
    sim.deciders = make_deciders(model_cfg, random.Random(0), sim.world.people)
    sim.run(1)
    assert stub_litellm.calls
    resumed = Simulation.resume(sim.log, "r", {})
    resumed.deciders = make_deciders(model_cfg, random.Random(0), resumed.world.people)
    assert resumed.deciders["model"].people is resumed.world.people
    resumed.run(1)
    assert resumed.world.step == 2


def test_prompts_from_a_model_run_use_no_forbidden_words(model_cfg, tmp_path, stub_litellm):
    sim = make_sim(model_cfg, tmp_path / "a.sqlite", seed=5)
    sim.run(13)
    texts = [prompt_text(c["messages"]) for c in stub_litellm.calls]
    assert texts, "no model was asked anything"
    for text in texts:
        assert FORBIDDEN_PROMPT_WORDS.search(text) is None, text
        assert not re.search(r"(?i)ambition|scruples|\bp\d\d\b", text), text


# --- timeouts (review I3) ---


class Timeout(Exception):
    """Stands in for LiteLLM's request timeout error."""


def times_out(calls):
    def completion(**kwargs):
        calls.append(kwargs)
        raise Timeout(f"request timed out after {kwargs['timeout']} seconds")

    return completion


def test_completion_is_given_the_configured_timeout(people, pricing_decision):
    calls = []
    d = ModelDecider(
        "x/y", 0.7, people, completion_fn=fake_reply('{"choice": "hold"}', calls), timeout=12.5
    )
    d.decide(pricing_decision)
    assert calls[0]["timeout"] == 12.5


def test_a_timeout_is_retried_then_raises_model_unavailable(people, pricing_decision):
    calls, sleeps = [], []
    d = ModelDecider(
        "x/y", 0.7, people, completion_fn=times_out(calls), sleep_fn=sleeps.append, timeout=5
    )
    with pytest.raises(ModelUnavailable, match="timed out"):
        d.decide(pricing_decision)
    assert len(calls) == 3 and sleeps == [1, 4]
    assert all(c["timeout"] == 5 for c in calls)


def test_model_decider_config_timeout_defaults_to_sixty_seconds(model_cfg):
    assert model_cfg.deciders["model"].timeout == 60.0


@pytest.mark.parametrize("bad", [0, -1])
def test_model_decider_config_timeout_must_be_positive(cfg_dict, bad):
    cfg_dict["deciders"]["model"] = {"type": "model", "model": "x/y", "temperature": 0.4, "timeout": bad}
    with pytest.raises(ConfigError):
        TownConfig.model_validate(cfg_dict)


def test_make_deciders_passes_the_configured_timeout(cfg_dict, people):
    cfg_dict["deciders"]["model"] = {"type": "model", "model": "x/y", "temperature": 0.4, "timeout": 9}
    cfg = TownConfig.model_validate(cfg_dict)
    assert make_deciders(cfg, random.Random(0), people)["model"].timeout == 9


def test_a_model_timeout_aborts_the_step_and_logs_run_aborted(model_cfg, tmp_path):
    from townsim.events import state_hash

    calls = []
    sim = make_sim(model_cfg, tmp_path / "a.sqlite", seed=3, deciders={})
    sim.deciders = {
        "model": ModelDecider(
            "x/y", 0.4, sim.world.people, completion_fn=times_out(calls), sleep_fn=lambda s: None
        )
    }
    before = state_hash(sim.world)
    with pytest.raises(ModelUnavailable):
        sim.step()
    assert len(calls) == 3 and state_hash(sim.world) == before and sim.world.step == 0
    assert [e.kind for _, e in sim.log.events("r", step=0)] == ["run_aborted"]


# --- badly shaped replies (review I4) ---


def returns(resp, calls=None):
    def completion(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return resp

    return completion


def _choices(*choices):
    return SimpleNamespace(choices=list(choices))


@pytest.mark.parametrize(
    "resp",
    [
        None,
        SimpleNamespace(),                                    # no choices at all
        _choices(),                                           # empty choices
        SimpleNamespace(choices=None),
        _choices(SimpleNamespace()),                          # a choice with no message
        _choices(SimpleNamespace(message=None)),
        _choices(SimpleNamespace(message=SimpleNamespace())),  # a message with no content
        _choices(SimpleNamespace(message=SimpleNamespace(content=["up5"]))),
        _choices(SimpleNamespace(message=SimpleNamespace(content={"choice": "up5"}))),
        _choices(SimpleNamespace(message=SimpleNamespace(content=5))),
    ],
)
def test_an_unreadable_reply_is_an_invalid_response_not_an_exception(people, pricing_decision, resp):
    calls, sink = [], []
    model = ModelDecider("x/y", 0.7, people, completion_fn=returns(resp, calls))
    choice = model.decide(pricing_decision)
    assert choice.option_id == "" and choice.meta["raw"] is None and len(calls) == 1
    recorded = RecordingDecider(model, sink).decide(pricing_decision)
    assert recorded.option_id == "hold"
    assert [e.kind for e in sink] == ["invalid_response", "decision"]
    for e in sink:
        e.model_dump_json()  # the record of it can be logged


@pytest.mark.parametrize(
    "usage",
    [
        None,
        "lots",
        {"prompt_tokens": 12, "completion_tokens": 3},
        SimpleNamespace(prompt_tokens="12", completion_tokens=None),
        SimpleNamespace(prompt_tokens=True, completion_tokens=2.5),
    ],
)
def test_malformed_usage_gives_no_token_counts(people, pricing_decision, usage):
    resp = response('{"choice": "up5"}', usage=None)
    resp.usage = usage
    choice = ModelDecider("x/y", 0.7, people, completion_fn=returns(resp)).decide(pricing_decision)
    assert choice.option_id == "up5"
    if isinstance(usage, dict):
        assert (choice.meta["prompt_tokens"], choice.meta["completion_tokens"]) == (12, 3)
    else:
        assert choice.meta["prompt_tokens"] is None and choice.meta["completion_tokens"] is None


def test_unset_optional_settings_are_left_out_of_the_call(people, pricing_decision):
    calls = []

    def fn(**kwargs):
        calls.append(kwargs)
        return fake_reply('{"choice": "up5"}')(**kwargs)

    ModelDecider("x/y", None, people, completion_fn=fn).decide(pricing_decision)
    assert set(calls[0]) == {"model", "messages", "timeout"}


def test_reasoning_effort_and_max_tokens_are_sent_when_set(people, pricing_decision):
    calls = []

    def fn(**kwargs):
        calls.append(kwargs)
        return fake_reply('{"choice": "up5"}')(**kwargs)

    ModelDecider(
        "x/y", None, people, completion_fn=fn, reasoning_effort="none", max_tokens=200
    ).decide(pricing_decision)
    assert calls[0]["reasoning_effort"] == "none" and calls[0]["max_tokens"] == 200
    assert "temperature" not in calls[0]


def test_reply_naming_the_part_after_a_prefix_is_taken_as_the_one_matching_option(people):
    from townsim.decide import Decision, Option

    d = Decision(
        id="0:p0:investigate:0", actor_id="p0", kind="investigate", situation="s",
        options=[Option(id="none", label="n"), Option(id="firm:f4", label="a"), Option(id="firm:f5", label="b")],
        default_option_id="none",
    )
    choice = ModelDecider("x/y", None, people, completion_fn=fake_reply('{"choice": "f5"}')).decide(d)
    assert choice.option_id == "firm:f5" and choice.meta["replied"] == "f5"


def test_ambiguous_short_reply_is_left_alone(people):
    from townsim.decide import Decision, Option

    d = Decision(
        id="0:p0:audit:0", actor_id="p0", kind="audit", situation="s",
        options=[Option(id="none", label="n"), Option(id="light:f1", label="a"), Option(id="deep:f1", label="b")],
        default_option_id="none",
    )
    choice = ModelDecider("x/y", None, people, completion_fn=fake_reply('{"choice": "f1"}')).decide(d)
    assert choice.option_id == "f1" and "replied" not in choice.meta
