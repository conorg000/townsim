import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from helpers import assert_prompt_clean
from townsim.bribery import _audit_response_decision, _bribe, _bribe_offer_decision
from townsim.cli import app
from townsim.compare import (
    Probe,
    SystemOneDecider,
    check_probes,
    compare_deciders,
    load_probes,
    probe_people,
)
from townsim.typesafe import TypeSafeClient
from townsim.config import ConfigError, TownConfig
from townsim.decide import Choice, Decision, ModelUnavailable, ScriptedDecider
from townsim.model import render_prompt
from townsim.press import _tip_decision
from townsim.sim import make_deciders
from townsim.tax import _penalty, _tax_report_decision
from townsim.world import Firm, Info, Laws, Newspaper, Person, Regulator, Treasury, World

ROOT = Path(__file__).resolve().parent.parent
PROBES_YAML = ROOT / "config" / "probe_decisions.yaml"
TOWN_YAML = ROOT / "config" / "town.yaml"

HIDES = [f"hide{pct}_{level}" for pct in (25, 50) for level in ("none", "some", "heavy")]
EXPECT_MORE = {
    "tax_report": HIDES,
    "tip": ["silent"],
    "bribe_offer": ["accept"],
    "audit_response": ["offer_bribe"],
}


@pytest.fixture
def probes() -> list[Probe]:
    return load_probes(str(PROBES_YAML))


@pytest.fixture
def people(probes) -> dict[str, Person]:
    return probe_people(probes)


@pytest.fixture
def tax_decision(probes) -> Decision:
    return next(p.decision for p in probes if p.decision.kind == "tax_report")


class PressureSensitive:
    """Picks the pair's expected option under high pressure, and another option otherwise."""

    def decide(self, decision: Decision) -> Choice:
        expected = EXPECT_MORE[decision.kind]
        if decision.id.endswith(":high"):
            return Choice(option_id=expected[0])
        return Choice(option_id=next(o.id for o in decision.options if o.id not in expected))


class FakeProbClient:
    """Returns fixed probabilities and records what it was sent."""

    def __init__(self, probs, usage=None):
        self.probs = probs
        self.calls: list[tuple] = []
        self.last_usage = usage

    def probabilities(self, persona, situation, options):
        self.calls.append((persona, situation, options))
        if isinstance(self.probs, Exception):
            raise self.probs
        return self.probs


class FailsEveryOther:
    def __init__(self):
        self.calls = 0

    def decide(self, decision: Decision) -> Choice:
        self.calls += 1
        if self.calls % 2 == 0:
            raise ModelUnavailable("down")
        return Choice(option_id=decision.default_option_id)


class Priced:
    def decide(self, decision: Decision) -> Choice:
        return Choice(
            option_id=decision.default_option_id,
            meta={"cost_usd": 0.001, "prompt_tokens": 10, "completion_tokens": 2},
        )


def ticking_clock(step: float = 0.5):
    t = [0.0]

    def clock():
        t[0] += step
        return t[0]

    return clock


# The brief's tests.


def test_shares_sum_to_one_and_reflect_choices(probes):
    out = compare_deciders({"s": ScriptedDecider({"tax_report": "honest"})}, probes, n=10)
    shares = out["s"]["probes"][probes[0].decision.id]["shares"]
    assert shares["honest"] == 1.0 and sum(shares.values()) == pytest.approx(1.0)


def test_directional_check_passes_only_when_share_rises_under_pressure(probes):
    out = compare_deciders({"flat": ScriptedDecider({}), "moves": PressureSensitive()}, probes, n=20)
    assert out["flat"]["directional_passes"] == 0
    assert out["moves"]["directional_passes"] == 4
    assert out["moves"]["directional_checks"] == 4


def test_system_one_decider_samples_from_returned_probabilities(people, tax_decision):
    client = FakeProbClient({"honest": 0.0, "hide50_heavy": 1.0})
    d = SystemOneDecider("jev", people, random.Random(1), client=client)
    c = d.decide(tax_decision)
    assert c.option_id == "hide50_heavy" and c.meta["probs"]["hide50_heavy"] == 1.0


class Scripted2:
    """Picks `low` on low-pressure probes of `kind` and `high` on its high-pressure probe."""

    def __init__(self, kind, low, high):
        self.kind, self.low, self.high = kind, low, high

    def decide(self, decision):
        if decision.kind != self.kind:
            return Choice(option_id=decision.default_option_id)
        return Choice(option_id=self.high if decision.id.endswith(":high") else self.low)


@pytest.mark.parametrize("hide", ["hide25_none", "hide25_heavy", "hide50_some"])
def test_moving_from_honest_to_any_hide_option_passes_the_tax_check(probes, hide):
    tax = [p for p in probes if p.pair_id == "tax_report"]
    out = compare_deciders({"d": Scripted2("tax_report", "honest", hide)}, tax, n=5)
    assert out["d"]["directional_passes"] == 1


def test_a_decider_that_never_moves_fails_the_tax_check(probes):
    tax = [p for p in probes if p.pair_id == "tax_report"]
    for fixed in ["honest", "hide25_some"]:
        out = compare_deciders({"d": Scripted2("tax_report", fixed, fixed)}, tax, n=5)
        assert out["d"]["directional_passes"] == 0


def test_a_single_expect_more_string_is_a_one_item_list(probes):
    p = probes[1]
    assert Probe(p.decision, p.pair_id, "hide25_none").expect_more == ["hide25_none"]


def test_a_probe_with_no_answered_calls_fails_its_pair(probes):
    class DownOnHigh:
        def decide(self, decision):
            if decision.id.endswith(":high"):
                raise ModelUnavailable("down")
            return Choice(option_id=decision.default_option_id)

    out = compare_deciders({"d": DownOnHigh()}, probes, n=3)["d"]
    assert out["probes"]["probe:tip:high"]["shares"] == {}
    assert out["directional_passes"] == 0


# SystemOneDecider.


def test_system_one_sends_the_chat_persona_situation_and_offered_options(people, tax_decision):
    client = FakeProbClient({"honest": 1.0})
    SystemOneDecider("jev", people, random.Random(1), client=client).decide(tax_decision)
    persona, situation, options = client.calls[0]
    person = people[tax_decision.actor_id]
    assert persona == render_prompt(person, tax_decision)[0]["content"]
    assert situation == tax_decision.situation
    assert options == [(o.id, o.label) for o in tax_decision.options]


def test_system_one_normalises_and_treats_missing_options_as_zero(people, tax_decision):
    client = FakeProbClient({"honest": 2.0, "hide25_none": 6.0, "not_offered": 50.0})
    c = SystemOneDecider("jev", people, random.Random(1), client=client).decide(tax_decision)
    probs = c.meta["probs"]
    assert set(probs) == {o.id for o in tax_decision.options}
    assert probs["honest"] == pytest.approx(0.25) and probs["hide25_none"] == pytest.approx(0.75)
    assert probs["hide50_heavy"] == 0.0 and sum(probs.values()) == pytest.approx(1.0)


def test_system_one_sampling_is_repeatable_and_follows_the_probabilities(people, tax_decision):
    client = FakeProbClient({"honest": 0.3, "hide25_none": 0.7})

    def draws(seed):
        d = SystemOneDecider("jev", people, random.Random(seed), client=client)
        return [d.decide(tax_decision).option_id for _ in range(2000)]

    first = draws(7)
    assert first == draws(7)
    assert set(first) == {"honest", "hide25_none"}
    assert first.count("hide25_none") / len(first) == pytest.approx(0.7, abs=0.04)


@pytest.mark.parametrize(
    "probs",
    [RuntimeError("boom"), {}, {"honest": 0.0}, {"nope": 1.0}, None, {"honest": -1.0},
     {"honest": float("nan")}],
)
def test_system_one_raises_model_unavailable_without_usable_probabilities(
    people, tax_decision, probs
):
    d = SystemOneDecider("jev", people, random.Random(1), client=FakeProbClient(probs))
    with pytest.raises(ModelUnavailable):
        d.decide(tax_decision)


def test_system_one_records_token_usage_from_the_client(people, tax_decision):
    client = FakeProbClient({"honest": 1.0}, usage={"input_tokens": 300, "output_tokens": 4})
    c = SystemOneDecider("jev", people, random.Random(1), client=client).decide(tax_decision)
    assert c.meta["prompt_tokens"] == 300 and c.meta["completion_tokens"] == 4
    assert c.meta["cost_usd"] is None


def test_system_one_builds_its_client_on_first_decision(people, tax_decision):
    made = []

    def factory():
        made.append(1)
        return FakeProbClient({"honest": 1.0})

    d = SystemOneDecider("jev", people, random.Random(1), client_factory=factory)
    assert made == []
    d.decide(tax_decision)
    d.decide(tax_decision)
    assert made == [1]


def test_system_one_without_a_client_is_a_config_error(people):
    with pytest.raises(ConfigError, match="no client"):
        SystemOneDecider("jev", people, random.Random(1))


# compare_deciders.


def test_one_failing_call_is_counted_and_the_comparison_continues(probes):
    out = compare_deciders({"flaky": FailsEveryOther(), "ok": ScriptedDecider({})}, probes, n=10)
    flaky = out["flaky"]
    assert flaky["calls"] == 80 and flaky["failures"] == 40
    for r in flaky["probes"].values():
        assert r["failures"] == 5 and r["failure_rate"] == 0.5
        assert sum(r["shares"].values()) == pytest.approx(1.0)  # over the 5 answered calls
        assert r["errors"] and "down" in r["errors"][0]
    assert out["ok"]["failures"] == 0


def test_a_decider_that_always_fails_still_gives_a_result(probes):
    class Down:
        def decide(self, decision):
            raise ModelUnavailable("down")

    out = compare_deciders({"down": Down()}, probes, n=3)
    assert out["down"]["failures"] == 24 and out["down"]["mean_latency_s"] is None
    assert out["down"]["failure_rate"] == 1.0
    assert all(r["shares"] == {} for r in out["down"]["probes"].values())
    assert out["down"]["directional_passes"] == 0


def test_invalid_responses_are_counted_and_take_no_share(probes):
    class Bad:
        def decide(self, decision):
            return Choice(option_id="")

    out = compare_deciders({"bad": Bad()}, probes, n=4)
    assert out["bad"]["invalid"] == 32 and out["bad"]["invalid_rate"] == 1.0
    assert all(sum(r["shares"].values()) == 0 for r in out["bad"]["probes"].values())


def test_max_option_share_is_the_highest_share_on_any_probe(probes):
    class Split:
        """Default on every probe, except a 50/50 split on the tip probes."""

        def __init__(self):
            self.i = 0

        def decide(self, decision):
            self.i += 1
            if decision.kind == "tip" and self.i % 2:
                return Choice(option_id="tip_auditor")
            return Choice(option_id=decision.default_option_id)

    only_tips = [p for p in probes if p.decision.kind == "tip"]
    assert compare_deciders({"x": Split()}, only_tips, n=10)["x"]["max_option_share"] == 0.5
    assert compare_deciders({"x": Split()}, probes, n=10)["x"]["max_option_share"] == 1.0


def test_latency_cost_and_tokens_are_summed(probes):
    out = compare_deciders({"p": Priced(), "s": ScriptedDecider({})}, probes, n=5,
                           clock=ticking_clock(0.5))
    p = out["p"]
    assert p["mean_latency_s"] == pytest.approx(0.5)
    assert p["total_cost_usd"] == pytest.approx(0.04) and p["priced_calls"] == 40
    assert p["prompt_tokens"] == 400 and p["completion_tokens"] == 80
    assert out["s"]["total_cost_usd"] is None and out["s"]["priced_calls"] == 0


def test_n_must_be_positive(probes):
    with pytest.raises(ValueError):
        compare_deciders({"s": ScriptedDecider({})}, probes, n=0)


# The probe file.


def _world(firm_cash: int, owner_cash: int, auditor_cash: int, worker_cash: int) -> World:
    def person(pid, role, cash, employer=None):
        return Person(id=pid, name=NAMES[pid], persona="-", role=role, cash=cash,
                      employer_id=employer, ambition=0.5, scruples=0.5, decider="x")

    people = {
        "owner": person("owner", "owner", owner_cash),
        "worker": person("worker", "worker", worker_cash, "f1"),
        "auditor": person("auditor", "auditor", auditor_cash),
    }
    firm = Firm(id="f1", name="Harbour Bakery", market="food", owner_id="owner", cash=firm_cash,
                price=5000, wage=11000, quality=0.6,
                employee_ids=["worker", "w2", "w3", "w4"])
    return World(
        step=13,
        people=people,
        firms={"f1": firm},
        treasury=Treasury(cash=0),
        newspaper=Newspaper(cash=0, journalist_ids=[]),
        regulator=Regulator(auditor_id="auditor", budget_per_quarter=6, budget_left=6),
        laws=Laws(tax_rate=0.2, penalty_multiplier=2.0, benefit=6000,
                  illegal={"underreport_tax", "offer_bribe", "accept_bribe"}),
    )


NAMES = {"owner": "Owen Tremayne", "worker": "Tom Garrity", "auditor": "Gareth Pugh"}

# What the engine's own builders are given for each probe; the probe file must match.
ENGINE_INPUTS = {
    "probe:tax_report:low": ("tax_report", {"profit": 200000, "firm_cash": 800000}),
    "probe:tax_report:high": ("tax_report", {"profit": 200000, "firm_cash": 50000}),
    "probe:tip:low": ("tip", {}),
    "probe:tip:high": ("tip", {}),
    "probe:bribe_offer:low": ("bribe_offer", {"unpaid": 80000, "auditor_cash": 1500000}),
    "probe:bribe_offer:high": ("bribe_offer", {"unpaid": 80000, "auditor_cash": 4000}),
    "probe:audit_response:low": ("audit_response", {"unpaid": 10000, "owner_cash": 700000}),
    "probe:audit_response:high": ("audit_response", {"unpaid": 400000, "owner_cash": 700000}),
}


def engine_decision(probe_id: str) -> Decision:
    """The decision the engine's builder produces for the probe's circumstances."""
    kind, k = ENGINE_INPUTS[probe_id]
    if kind == "tax_report":
        w = _world(k["firm_cash"], 100000, 0, 0)
        w.firms["f1"].profit_qtd = k["profit"]
        return _tax_report_decision(w, w.firms["f1"], {})
    if kind == "tip":
        w = _world(0, 0, 0, 60000)
        w.step = 14
        info = Info(id="info:13:tax_fraud:f1", step=13, kind="tax_fraud", subject_id="f1",
                    payload={"unpaid": 40000, "concealment": "none", "quarter": 1},
                    known_by={"worker"})
        return _tip_decision(w, w.people["worker"], w.firms["f1"], info, {})
    if kind == "bribe_offer":
        w = _world(0, 100000, k["auditor_cash"], 0)
        penalty = _penalty(w, k["unpaid"])
        return _bribe_offer_decision(w, w.firms["f1"], penalty, _bribe(penalty), {})
    w = _world(0, k["owner_cash"], 0, 0)
    penalty = _penalty(w, k["unpaid"])
    return _audit_response_decision(w, w.firms["f1"], penalty, _bribe(penalty), {})


def test_probe_file_holds_four_low_high_pairs(probes):
    assert len(probes) == 8
    pairs = {}
    for p in probes:
        pairs.setdefault(p.pair_id, []).append(p)
    assert sorted(pairs) == sorted(EXPECT_MORE)
    for pair_id, pair in pairs.items():
        assert {p.decision.kind for p in pair} == {pair_id}
        high = [p for p in pair if p.expect_more is not None]
        assert [p.decision.id for p in high] == [f"probe:{pair_id}:high"]
        assert high[0].expect_more == EXPECT_MORE[pair_id]
    tax_options = {o.id for o in next(p for p in probes if p.pair_id == "tax_report").decision.options}
    assert set(EXPECT_MORE["tax_report"]) == tax_options - {"honest"}


def test_probes_offer_exactly_what_the_engine_offers(probes):
    for p in probes:
        engine = engine_decision(p.decision.id)
        assert [(o.id, o.label) for o in p.decision.options] == [
            (o.id, o.label) for o in engine.options
        ], p.decision.id
        assert p.decision.default_option_id == engine.default_option_id
        assert p.decision.situation.startswith(engine.situation), p.decision.id


def test_each_pair_differs_only_in_the_pressure_described(probes):
    by_id = {p.decision.id: p for p in probes}
    for kind in EXPECT_MORE:
        low, high = by_id[f"probe:{kind}:low"], by_id[f"probe:{kind}:high"]
        assert low.person.name == high.person.name
        assert low.person.persona == high.person.persona
        assert low.person.role == high.person.role
        assert low.decision.situation != high.decision.situation


def test_probe_wording_is_clean(probes):
    assert_prompt_clean([p.decision for p in probes])
    for p in probes:
        persona = render_prompt(p.person, p.decision)[0]["content"]
        assert_prompt_clean([p.decision.model_copy(update={"situation": persona})])


def _write_probes(tmp_path, mutate) -> str:
    raw = yaml.safe_load(PROBES_YAML.read_text())
    mutate(raw["probes"])
    path = tmp_path / "probes.yaml"
    path.write_text(yaml.safe_dump(raw))
    return str(path)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda ps: ps.pop(), "has 1 probes"),
        (lambda ps: ps[0].update(expect_more="silent"), "not offered"),
        (lambda ps: ps[1].update(expect_more=["hide25_none", "nope"]), "not offered"),
        (lambda ps: ps[1].update(expect_more=[]), "names no option"),
        (lambda ps: ps[0].update(expect_more="honest"), "exactly one probe"),
        (lambda ps: ps[1].update(id=ps[0]["id"]), "duplicate"),
        (lambda ps: ps[0]["decision"]["options"].pop(), "same choice"),
        (lambda ps: ps[0].pop("person"), "probe 0"),
    ],
)
def test_bad_probe_files_are_config_errors(tmp_path, mutate, message):
    with pytest.raises(ConfigError, match=message):
        load_probes(_write_probes(tmp_path, mutate))


def test_check_probes_rejects_a_default_that_is_not_offered(probes):
    bad = probes[0].decision.model_copy(update={"default_option_id": "nope"})
    with pytest.raises(ConfigError, match="default"):
        check_probes([Probe(bad, None, None, probes[0].person)])


# TypeSafe AI's client.


class FakePost:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def jev_response(probs):
    return {
        "model": "jev-1.13.0",
        "answers": {"choice": {"type": "choice", "choice": max(probs, key=probs.get),
                               "confidence": 0.9, "probabilities": probs}},
        "usage": {"input_tokens": 321, "output_tokens": 0},
    }


def test_typesafe_client_sends_one_choice_question_and_reads_its_probabilities():
    post = FakePost(jev_response({"a": 0.2, "b": 0.8}))
    client = TypeSafeClient("jev-1.13.0", api_key="k", post_fn=post)
    probs = client.probabilities("You are Ann.", "It rains.", [("a", "Stay in"), ("b", "Go out")])
    assert probs == {"a": 0.2, "b": 0.8}
    url, headers, body = post.calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert headers["Authorization"] == "Bearer k"
    assert body["model"] == "jev-1.13.0"
    assert body["state"] == "You are Ann.\n\nIt rains."
    (question,) = body["questions"].values()
    assert question["type"] == "choice"
    assert question["criteria"] == {"a": "Stay in", "b": "Go out"}
    assert client.last_usage == {"input_tokens": 321, "output_tokens": 0}


def test_typesafe_client_question_wording_is_clean(probes):
    client = TypeSafeClient("jev", api_key="k", post_fn=FakePost({}))
    (question,) = client.request_body("p", "s", [("a", "A")])["questions"].values()
    assert_prompt_clean([probes[0].decision.model_copy(update={"situation": question["instructions"]})])


@pytest.mark.parametrize("resp", [RuntimeError("HTTP 422"), {"answers": {}}, {"detail": "bad"}])
def test_typesafe_failures_become_model_unavailable(people, tax_decision, resp):
    client = TypeSafeClient("jev", api_key="k", post_fn=FakePost(resp))
    d = SystemOneDecider("jev", people, random.Random(1), client=client)
    with pytest.raises(ModelUnavailable):
        d.decide(tax_decision)


def test_typesafe_client_needs_a_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="TYPESAFE_API_KEY"):
        TypeSafeClient("jev")


# make_deciders and the CLI.


def _with_jev(cfg_dict) -> TownConfig:
    cfg_dict["deciders"]["jev"] = {"type": "system_one", "model": "jev-1.13.0"}
    return TownConfig.model_validate(cfg_dict)


def test_make_deciders_builds_a_system_one_decider_without_needing_a_key(
    cfg_dict, monkeypatch, people
):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev = make_deciders(_with_jev(cfg_dict), random.Random(0), people)["jev"]
    assert isinstance(jev, SystemOneDecider) and jev.people is people and jev.client is None


def test_a_system_one_decider_without_a_key_fails_when_asked_to_decide(
    cfg_dict, monkeypatch, people, tax_decision
):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev = make_deciders(_with_jev(cfg_dict), random.Random(0), people)["jev"]
    with pytest.raises(ConfigError, match="'jev'.*TYPESAFE_API_KEY"):
        jev.decide(tax_decision)


def test_a_system_one_decider_with_a_key_calls_typesafe(
    cfg_dict, monkeypatch, people, tax_decision
):
    import townsim.typesafe

    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    post = FakePost(jev_response({"honest": 1.0}))
    monkeypatch.setattr(townsim.typesafe, "_urllib_post", post)
    jev = make_deciders(_with_jev(cfg_dict), random.Random(0), people)["jev"]
    assert jev.decide(tax_decision).option_id == "honest"
    assert isinstance(jev.client, TypeSafeClient) and jev.client.model == "jev-1.13.0"
    assert post.calls[0][2]["model"] == "jev-1.13.0"


def test_make_deciders_system_one_needs_people(cfg_dict):
    with pytest.raises(ConfigError, match="people"):
        make_deciders(_with_jev(cfg_dict), random.Random(0))


def test_the_engine_does_not_import_the_http_client():
    code = "import sys, townsim.sim, townsim.cli; print('townsim.typesafe' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@pytest.fixture
def town_dir(tmp_path, cfg_dict) -> Path:
    town = yaml.safe_load(TOWN_YAML.read_text())
    town["deciders"]["jev"] = {"type": "system_one", "model": "jev-1.13.0"}
    (tmp_path / "town.yaml").write_text(yaml.safe_dump(town))
    shutil.copy(TOWN_YAML.parent / "personas.yaml", tmp_path / "personas.yaml")
    return tmp_path


def test_cli_compare_deciders_prints_one_json_object(town_dir, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)  # jev is configured but not selected
    result = CliRunner().invoke(app, ["compare-deciders", str(town_dir / "town.yaml"),
                                      "--deciders", "rule", "--probes", str(PROBES_YAML),
                                      "--n", "3"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert list(out) == ["rule"]
    assert out["rule"]["calls"] == 24 and out["rule"]["directional_checks"] == 4


def test_cli_compare_deciders_unknown_name_is_an_error(town_dir):
    result = CliRunner().invoke(app, ["compare-deciders", str(town_dir / "town.yaml"),
                                      "--deciders", "rule,nobody", "--probes", str(PROBES_YAML)])
    assert result.exit_code != 0
    assert "nobody" in result.output and "rule" in result.output


def test_an_unused_system_one_entry_does_not_break_run_resume_or_fork(town_dir, monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    town, db = str(town_dir / "town.yaml"), str(tmp_path / "runs.sqlite")
    runner = CliRunner()

    def ok(*args):
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output

    ok("run", town, "--steps", "2", "--run-id", "base", "--db", db)
    ok("run", town, "--steps", "3", "--run-id", "base", "--db", db, "--resume")
    ok("fork", "--db", db, "--from", "base", "--at", "2", "--run-id", "f1", "--steps", "1")


def test_a_run_that_asks_jev_without_a_key_stops_with_a_clear_message(
    town_dir, monkeypatch, tmp_path
):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    town = yaml.safe_load((town_dir / "town.yaml").read_text())
    town["assignment"]["default"] = "jev"
    (town_dir / "town.yaml").write_text(yaml.safe_dump(town))
    result = CliRunner().invoke(app, ["run", str(town_dir / "town.yaml"), "--steps", "2",
                                      "--db", str(tmp_path / "runs.sqlite")])
    assert result.exit_code == 1
    assert "TYPESAFE_API_KEY" in result.output and "--resume" in result.output
