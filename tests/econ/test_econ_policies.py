"""Managers see only an Observation; the gate ordering holds on a quick run."""

import dataclasses
import inspect
import json

from typer.testing import CliRunner

from townsim.cli import app
from townsim.econ.policies import Naive, Oracle, Reactive, Skilled
from townsim.econ.sim import Decision, Observation, Truth, WeekRecord
from townsim.econ.skilltest import experiment, start_state

PLAIN = (int, float, str, bool, type(None))


def _plain(x) -> bool:
    if isinstance(x, PLAIN):
        return True
    if isinstance(x, tuple):
        return all(_plain(v) for v in x)
    if isinstance(x, (Observation, WeekRecord)):
        return all(_plain(getattr(x, f.name)) for f in dataclasses.fields(x))
    return False


HIDDEN = {"reputation", "wtp", "p_full", "partial_low", "partial_high", "shocks", "households",
          "demand_mult", "season_amplitude", "rule"}


def test_observation_holds_only_plain_values_and_no_hidden_parameters():
    econ = start_state(1)
    obs = econ.observe("firm:1")
    assert _plain(obs)
    names = {f.name for f in dataclasses.fields(Observation)} | {f.name for f in dataclasses.fields(WeekRecord)}
    assert not names & HIDDEN


class _Spy:
    def __init__(self, inner):
        self.inner, self.name, self.calls = inner, inner.name, []

    def decide(self, *args):
        self.calls.append(args)
        return self.inner.decide(*args)


def test_ordinary_managers_are_handed_only_an_observation():
    for cls in (Naive, Reactive, Skilled):
        assert list(inspect.signature(cls.decide).parameters) == ["self", "obs"]
        assert not getattr(cls(), "uses_truth", False)
    econ = start_state(1)
    spies = {fid: _Spy(Reactive()) for fid in econ.firms}
    spies["firm:1"] = _Spy(Skilled())
    econ.run(spies, 3)
    for spy in spies.values():
        assert spy.calls and all(len(a) == 1 and type(a[0]) is Observation for a in spy.calls)


def test_only_the_oracle_is_given_the_truth():
    econ = start_state(1)
    spy = _Spy(Oracle())
    spy.uses_truth = True
    econ.run({"firm:1": spy, "firm:2": Reactive(), "firm:3": Reactive()}, 2)
    assert all(isinstance(a[0], Observation) and isinstance(a[1], Truth) for a in spy.calls)


def test_decisions_are_plain():
    econ = start_state(2)
    obs = econ.observe("firm:1")
    for m in (Naive(), Reactive(), Skilled()):
        d = m.decide(obs)
        assert isinstance(d, Decision) and d.price > 0 and d.staff >= 1
        assert all(isinstance(u, int) and u >= 0 for _, u in d.orders)


def test_gate_ordering_holds_on_a_quick_run():
    res = experiment([1, 2, 3, 4], levers=False)
    m = {k: v["mean_profit"] for k, v in res["policies"].items()}
    assert m["skilled"] >= 1.25 * max(m["reactive"], m["naive"])
    assert res["skilled_seeds_won"] >= 3
    assert m["oracle"] > m["skilled"]
    assert res["pass"] and all(res["checks"].values())


def test_skill_command_prints_json():
    out = CliRunner().invoke(app, ["skill", "--seeds", "2", "--no-levers", "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert set(data["policies"]) == {"naive", "reactive", "skilled", "oracle"}
    assert "pass" in data and "ratios" in data
    table = CliRunner().invoke(app, ["skill", "--seeds", "1", "--weeks", "4", "--no-levers"])
    assert table.exit_code == 0 and "Gate:" in table.output
