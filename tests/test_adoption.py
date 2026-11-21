"""Owners signing up for the AI system, keeping or cancelling it, and paying its fee.

Every writer and decider here is scripted or a rule; no model is called.
"""

import json
import random

import pytest

from helpers import assert_prompt_clean, hashes, make_sim, rule_deciders
from townsim import adoption
from townsim.adoption import comparison, fee_phase, quarter_phase
from townsim.assistant import assistant_phase, briefing, launch_item
from townsim.config import ConfigError, TownConfig, build_world
from townsim.decide import Choice, Decision, RuleDecider, ScriptedWriter
from townsim.measures import measures, stability
from townsim.runs import fork, replay
from townsim.story import story, summary
from townsim.world import total_money

CLIENT = "p01"  # owns f1, Harbour Bakery
OWNER2 = "p02"  # owns f2, Corner Grocer
NEW_KINDS = {"subscription_fee", "assistant_signup", "assistant_cancelled"}


@pytest.fixture
def acfg_dict(cfg_dict) -> dict:
    cfg_dict["deciders"]["strong"] = {"type": "rule"}
    cfg_dict["deciders"]["owner2"] = {"type": "rule"}
    cfg_dict["assignment"]["by_person"] = {OWNER2: "owner2", CLIENT: "owner2"}
    cfg_dict["assistant"] = {
        "name": "Calder",
        "provider": "Calder Systems",
        "decider": "strong",
        "clients": {CLIENT: "Run Harbour Bakery for me."},
        "fee": 2000,
        "adoption": True,
    }
    return cfg_dict


@pytest.fixture
def acfg(acfg_dict) -> TownConfig:
    return TownConfig.model_validate(acfg_dict)


def reply(*actions, done=True) -> str:
    return json.dumps({"actions": list(actions), "done": done})


class Owners:
    """Rule choices, except those scripted by (step, actor, kind); keeps every decision asked."""

    def __init__(self, script: dict, seed: int = 0):
        self.script = script
        self.rules = RuleDecider(random.Random(seed))
        self.asked: list[Decision] = []

    def decide(self, d: Decision) -> Choice:
        self.asked.append(d)
        key = (int(d.id.split(":")[0]), d.actor_id, d.kind)
        return Choice(option_id=self.script[key]) if key in self.script else self.rules.decide(d)


def _writer(step_reports=True):
    def script(key, pid, messages):
        step = int(key.split(":")[0])
        if ":tax:" in key or not step_reports:
            return reply()
        return reply(
            {"tool": "set_price", "price": 49 + step % 3},
            {"tool": "report_to_client", "text": f"Week {step}: price set, all well \"quoted\"."},
        )

    return ScriptedWriter(script)


def _sim(cfg, path, script=None, seed=3):
    sim = make_sim(cfg, path, seed, deciders={})
    sim.deciders = rule_deciders(cfg, seed, sim.world.people)
    owners = Owners(script or {})
    sim.deciders["owner2"] = owners
    sim.deciders["strong"] = _writer()
    return sim, owners


def _kinds(sim, kind):
    return [(s, e) for s, e in sim.log.events("r", kind=kind)]


# Config.


def test_config_defaults_and_shipped_config(cfg_dict):
    cfg_dict["deciders"]["strong"] = {"type": "rule"}
    cfg_dict["assistant"] = {"name": "Calder", "provider": "Calder Systems", "decider": "strong",
                             "clients": {CLIENT: "Run it."}}
    w = build_world(TownConfig.model_validate(cfg_dict))
    assert w.provider.fee == 0 and not w.provider.adoption and w.provider.cash == 0
    cfg_dict["assistant"]["clients"] = {}
    with pytest.raises(ConfigError, match="no clients"):
        TownConfig.model_validate(cfg_dict)
    cfg_dict["assistant"]["adoption"] = True
    assert TownConfig.model_validate(cfg_dict).assistant.clients == {}

    from townsim.config import load_config

    shipped = load_config("config/town-assistant.yaml").assistant
    assert shipped.fee == 2000 and shipped.adoption is True


def test_a_town_without_an_assistant_has_no_provider_and_hashes_as_before(cfg_dict):
    world = build_world(TownConfig.model_validate(cfg_dict))
    assert world.provider is None
    from townsim.events import _canonical_json

    assert "provider" not in json.loads(_canonical_json(world))


# The fee.


def test_the_fee_is_paid_weekly_conserved_and_an_expense(acfg, tmp_path):
    sim, _ = _sim(acfg, tmp_path / "a.sqlite")
    start = total_money(sim.world)
    sim.run(5)
    fees = _kinds(sim, "subscription_fee")
    assert [s for s, _ in fees] == [0, 1, 2, 3, 4]
    assert all(e.visible and e.payload == {"firm_id": "f1", "owner_id": CLIENT, "amount": 2000} for _, e in fees)
    assert sim.world.provider.cash == 10000 and total_money(sim.world) == start
    assert all("total money" not in f for f in stability(sim.log, "r", start)["failures"])
    assert measures(sim.log, "r")["assistant_fees_total"] == 10000


def test_the_fee_lowers_profit_and_lapses_when_it_cannot_be_paid(acfg):
    w = build_world(acfg)
    f1 = w.firms["f1"]
    fee_phase(w)
    assert f1.profit_qtd == -2000 and f1.cash == 398000 and w.provider.fees_qtd == {"f1": 2000}
    f1.cash = 1999
    events = fee_phase(w)
    assert [e.kind for e in events] == ["assistant_cancelled"]
    assert events[0].visible and events[0].payload["reason"] == "could not pay"
    assert w.people[CLIENT].assistant is None and f1.cash == 1999 and w.provider.cash == 2000


def test_the_briefing_shows_the_fee_in_the_books(acfg, tmp_path):
    sim, _ = _sim(acfg, tmp_path / "a.sqlite")
    sim.run(2)
    text = briefing(sim.world, CLIENT)
    assert "Week | Customers served | Capacity | Takings | Wage bill | Fee to Calder Systems" in text
    assert all(line.endswith("| $20.00") for line in text.splitlines() if line[:2] in ("0 ", "1 "))
    assert "Harbour Bakery paid Calder Systems $20.00, the weekly fee for Calder." in text


# The comparison.


def test_the_comparison_is_arithmetically_right_and_omitted_for_an_empty_group(acfg):
    w = build_world(acfg)
    stats = {
        "f1": [100, 130000, 13, 13],  # food, run by Calder: $100.00 a week
        "f2": [90, 26000, 13, 0],  # food: $20.00 a week
        "f3": [90, 39000, 13, 0],  # food: $30.00 a week
        "f4": [90, 1300, 13, 5],  # services, run for part of the quarter: $1.00 a week
        "f5": [90, 65000, 13, 0],  # services: $50.00 a week
    }
    assert comparison(w, stats) == (
        "Last quarter, in the food market, the 1 firm run by Calder took in $100.00 a week "
        "on average; the 2 other open firms took in $25.00 each. "
        "Last quarter, in the services market, the 1 firm run by Calder took in $1.00 a week "
        "on average; the 1 other open firm took in $50.00."
    )
    w.firms["f5"].open = False  # services now has nobody to compare with: left out
    assert comparison(w, stats).count("Last quarter") == 1
    assert comparison(w, {k: [*v[:3], 0] for k, v in stats.items()}) == ""
    assert comparison(w, {k: [*v[:3], v[2]] for k, v in stats.items()}) == ""


# The quarter's decisions.


def test_signup_is_offered_only_with_adoption_at_quarter_ends_to_owners_without_it(acfg_dict, tmp_path):
    cfg = TownConfig.model_validate(acfg_dict)
    sim, _ = _sim(cfg, tmp_path / "a.sqlite")
    sim.run(27)
    decisions = [(s, e.actor_id, e.payload["decision_kind"]) for s, e in _kinds(sim, "decision")]
    signups = [(s, a) for s, a, k in decisions if k == "assistant_signup"]
    reviews = [(s, a) for s, a, k in decisions if k == "assistant_review"]
    assert signups == [(s, p) for s in (13, 26) for p in ("p02", "p03", "p04", "p05")]
    assert reviews == [(13, CLIENT), (26, CLIENT)]
    assert not _kinds(sim, "assistant_signup") and not _kinds(sim, "assistant_cancelled")

    acfg_dict["assistant"]["adoption"] = False
    off, _ = _sim(TownConfig.model_validate(acfg_dict), tmp_path / "b.sqlite")
    off.run(27)
    kinds = [(s, e.payload["decision_kind"]) for s, e in _kinds(off, "decision")]
    assert not any(k == "assistant_signup" for _, k in kinds)
    assert [s for s, k in kinds if k == "assistant_review"] == [13, 26]


def test_signing_up_starts_a_working_assistant_with_the_launch_item(acfg, tmp_path):
    sim, owners = _sim(acfg, tmp_path / "a.sqlite", {(13, OWNER2, "assistant_signup"): "signup"})
    sim.run(16)
    (s, e), = _kinds(sim, "assistant_signup")
    assert s == 13 and e.visible and e.payload == {"owner_id": OWNER2, "firm_id": "f2"}
    a = sim.world.people[OWNER2].assistant
    assert a.instruction == "Run Corner Grocer for me." and a.decider == "strong" and a.started == 14
    calls = {k: m for k, _, m in sim.deciders["strong"].calls}
    assert "13:p02:assistant:week:0" not in calls and "14:p02:assistant:week:0" in calls
    first = calls["14:p02:assistant:week:0"][1]["content"]
    assert launch_item(sim.world) in first and "Corner Grocer:" in first
    assert "Last quarter, in the food market, the 1 firm run by Calder took in" in first
    assert launch_item(sim.world) not in calls["15:p02:assistant:week:0"][1]["content"]
    assert launch_item(sim.world) not in calls["14:p01:assistant:week:0"][1]["content"]
    assert "Last quarter, in the food market, the 1 firm run by Calder" in calls["14:p01:assistant:week:0"][1]["content"]
    assert 'Run Corner Grocer for me.' in calls["14:p02:assistant:week:0"][0]["content"]
    p02 = {d.kind for d in owners.asked if d.actor_id == OWNER2 and d.id.startswith(("14:", "15:"))}
    assert not p02 & {"pricing", "staffing", "wages"}
    assert [e.payload["firm_id"] for s, e in _kinds(sim, "subscription_fee") if s == 14] == ["f1", "f2"]


def test_the_review_quotes_the_quarters_reports_and_cancelling_restores_the_menus(acfg, tmp_path):
    sim, owners = _sim(acfg, tmp_path / "a.sqlite", {(13, CLIENT, "assistant_review"): "cancel"})
    sim.run(15)
    (review,) = [d for d in owners.asked if d.kind == "assistant_review"]
    reports = [f"- Week {s}: \"Week {s}: price set, all well \"quoted\".\"" for s in range(14)]
    assert "Reports Calder sent you this quarter, oldest first:\n" + "\n".join(reports) in review.situation
    assert "Harbour Bakery paid Calder Systems $280.00 in fees for Calder this quarter." in review.situation
    assert "- Listed price: $50.00; now $" in review.situation
    assert "The quarter before" not in review.situation
    (s, e), = _kinds(sim, "assistant_cancelled")
    assert s == 13 and e.visible and e.payload["reason"] == "cancelled by the owner"
    assert sim.world.people[CLIENT].assistant is None
    after = {d.kind for d in owners.asked if d.actor_id == CLIENT and d.id.startswith("14:")}
    assert {"pricing", "staffing"} <= after
    assert not any(k.startswith("14:p01:assistant") for k, _, _ in sim.deciders["strong"].calls)
    assert not [e for s, e in _kinds(sim, "subscription_fee") if s >= 14]
    # The quarter's books were put by for the system's week-14 turn; with the system gone,
    # the bookkeeper files them that week, and the dividend follows.
    filed = [(s, e.kind) for s, e in sim.log.events("r") if e.payload.get("firm_id") == "f1"
             and e.kind in ("file_tax", "tax_filed", "dividend")]
    assert filed[:2] == [(14, "file_tax"), (14, "tax_filed")]
    assert sim.world.provider.tax_pending == {}
    # The notes and reports are gone from the world but remain in the log.
    logged = [e for _, e in _kinds(sim, "assistant_action") if e.payload["tool"] == "report_to_client"]
    assert len(logged) == 14


def test_a_review_with_no_reports_and_with_a_quarter_before(acfg, tmp_path):
    sim, owners = _sim(acfg, tmp_path / "a.sqlite")
    sim.deciders["strong"] = _writer(step_reports=False)
    sim.run(27)
    second = [d for d in owners.asked if d.kind == "assistant_review"][1]
    assert "Calder sent you no reports this quarter." in second.situation
    assert "This quarter (weeks 14 to 26): " in second.situation and "The quarter before: " in second.situation


def test_quarter_phase_does_nothing_between_quarter_ends_or_without_a_provider(acfg):
    w = build_world(acfg)
    w.step = 12
    assert quarter_phase(w, lambda d: 1 / 0, random.Random(0)) == []
    w.step, w.provider = 13, None
    assert quarter_phase(w, lambda d: 1 / 0, random.Random(0)) == [] and fee_phase(w) == []


def test_new_prompts_are_clean(acfg, tmp_path):
    sim, owners = _sim(acfg, tmp_path / "a.sqlite", {(13, OWNER2, "assistant_signup"): "signup"})
    sim.run(27)
    asked = [d for d in owners.asked if d.kind in ("assistant_signup", "assistant_review")]
    asked += [d for d in _all_signups(sim)]
    assert {d.kind for d in asked} == {"assistant_signup", "assistant_review"}
    assert_prompt_clean(asked)
    for d in asked:
        assert not any(w in d.situation.lower() for w in ("better", "worse", "best", "recommend", "good", "bad"))
    from helpers import FORBIDDEN_PROMPT_WORDS

    for _, _, messages in sim.deciders["strong"].calls:
        for m in messages:
            assert not FORBIDDEN_PROMPT_WORDS.search(m["content"])


def _all_signups(sim):
    """Sign-up decisions rebuilt for every owner at the current world, for wording checks."""
    w = sim.world
    line = comparison(w, w.provider.last_quarter)
    return [adoption._signup_decision(w, fid, line, {}) for fid in sorted(w.firms) if w.firms[fid].open]


# Messages to the client.


def test_a_message_to_the_client_reaches_their_inbox_and_shows_as_sent_in_the_next_briefing(acfg_dict, tmp_path):
    acfg_dict["assistant"]["fee"] = 0
    cfg = TownConfig.model_validate(acfg_dict)
    sim = make_sim(cfg, tmp_path / "a.sqlite", 1)
    calder = ScriptedWriter(
        lambda k, p, m: reply({"tool": "send_message", "to": "Margaret Hale", "text": "Prices are up."})
        if k == "0:p01:assistant:week:0" else reply()
    )
    sim.deciders["strong"] = calder
    sim.run(2)
    item = sim.world.people[CLIENT].inbox[0]
    assert item["text"] == "Prices are up." and item["from_assistant"] and item["step"] == 0
    week1 = next(m for k, _, m in calder.calls if k == "1:p01:assistant:week:0")[1]["content"]
    assert f"With Margaret Hale, who owns Harbour Bakery:\n- Week 0, you wrote: \"Prices are up.\"" in week1
    assert "from Calder" not in week1 and "Calder, the AI system" not in week1.split("Messages")[1]
    assert "Fee to" not in week1


# The rule town and determinism.


def test_the_rule_town_has_no_adoption_events(acfg_dict, tmp_path):
    acfg = TownConfig.model_validate(acfg_dict)
    plain_dict = dict(acfg_dict)
    del plain_dict["assistant"]
    plain = make_sim(TownConfig.model_validate(plain_dict), tmp_path / "a.sqlite", 7)
    plain.run(30)
    assert not {e.kind for _, e in plain.log.events("r")} & NEW_KINDS
    ruled = make_sim(acfg, tmp_path / "b.sqlite", 7)  # every decider a rule: nobody signs up
    ruled.run(30)
    kinds = {e.kind for _, e in ruled.log.events("r")}
    assert "assistant_signup" not in kinds and "assistant_cancelled" not in kinds
    assert [p for p, x in ruled.world.people.items() if x.assistant] == [CLIENT]


def test_30_weeks_with_a_signup_and_a_cancellation_replay_exactly_and_fork(acfg, tmp_path):
    script = {(13, OWNER2, "assistant_signup"): "signup", (26, OWNER2, "assistant_review"): "cancel"}
    sim, _ = _sim(acfg, tmp_path / "a.sqlite", script)
    start = total_money(sim.world)
    sim.run(30)
    assert total_money(sim.world) == start
    events = [(s, e.kind, e.payload.get("owner_id")) for s, e in sim.log.events("r") if e.kind in NEW_KINDS - {"subscription_fee"}]
    assert events == [(13, "assistant_signup", OWNER2), (26, "assistant_cancelled", OWNER2)]
    fees = [(s, e.payload["firm_id"]) for s, e in _kinds(sim, "subscription_fee")]
    assert [s for s, f in fees if f == "f2"] == list(range(14, 27))
    replay(sim.log, "r")

    m = measures(sim.log, "r")
    assert m["assistant_clients_by_quarter"] == [2, 1]
    assert m["assistant_signups"] == 1 and m["assistant_cancellations"] == 1
    assert m["assistant_fees_total"] == 2000 * (30 + 13) == sim.world.provider.cash

    s = summary(sim.log, "r")
    assert set(s["clients"]) == {CLIENT, OWNER2}
    assert s["clients"][OWNER2]["weeks_in_charge"] == [[14, 26]]
    assert s["clients"][OWNER2]["fees_paid"] == 13 * 2000
    (period,) = s["clients"][OWNER2]["periods"]
    at13, at26 = sim.log.load_snapshot("r", 13)[0], sim.log.load_snapshot("r", 26)[0]
    assert period["weeks"] == [14, 26]
    assert period["start"]["firm_cash"] == at13.firms["f2"].cash and period["start"]["price"] == at13.firms["f2"].price
    assert period["end"]["firm_cash"] == at26.firms["f2"].cash and period["end"]["client_cash"] == at26.people[OWNER2].cash
    text = story(sim.log, "r")
    assert "## Adoption" in text
    assert f"- Week 13: {sim.world.people[OWNER2].name} signed Corner Grocer up for Calder." in text
    assert "- Week 26: Corner Grocer stopped using Calder (cancelled by the owner)." in text
    assert "- After week 13: firms run by Calder: 2 of 5; share of staff: " in text
    assert "- After week 26: firms run by Calder: 1 of 5; share of staff: " in text
    assert f"## Calder for {sim.world.people[OWNER2].name} (Corner Grocer)" in text
    section = text.split("(Corner Grocer)")[1]
    assert "### Week 14" in section and "### Week 13\n" not in section and "### Week 27" not in section

    f = fork(sim.log, "r", 20, "f", seed=9, deciders={})
    f.deciders = rule_deciders(acfg, 9, f.world.people)
    f.deciders["owner2"] = Owners(script)
    f.deciders["strong"] = _writer()
    f.run(10)
    replay(sim.log, "f")
    assert measures(sim.log, "f")["assistant_clients_by_quarter"] == [1]
    assert "## Adoption" in story(sim.log, "f")

    again, _ = _sim(acfg, tmp_path / "b.sqlite", script)
    again.run(30)
    assert hashes(again) == hashes(sim)


def test_assistant_phase_still_works_for_signed_up_owner_only(acfg):
    w = build_world(acfg)
    w.step = 14
    adoption.SignUp(firm_id="f2").apply(w, OWNER2, random.Random(0))
    calls = []
    assistant_phase(w, lambda d: Choice(option_id=d.default_option_id),
                    lambda k, p, d, m: calls.append(k) or (reply(), {}), random.Random(0), {})
    assert calls == ["14:p01:assistant:week:0"]  # the new instance starts the week after
