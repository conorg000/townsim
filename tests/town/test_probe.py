"""The probe of the model families, against a fake completion function: nothing here calls a model."""

import functools
import json
import pickle
import threading
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from townsim.cli import app
from townsim.estimate import Price
from townsim.town import probe
from townsim.town.families import Family
from townsim.town.replies import StopRun

NAMES = ["a", "b", "c", "d"]
FAMILIES = {
    "a": Family("a", "maker/alpha", {"max_tokens": 4000}),
    "b": Family("b", "maker/beta", {"max_tokens": 4000}),
}
PRICES = {f.model: Price(input_per_mtok=0.10, output_per_mtok=0.50) for f in FAMILIES.values()}
COST = 0.001


@functools.cache
def _cases(seed):
    return pickle.dumps(probe.cases(seed, NAMES))


def cases(seed=1):
    return pickle.loads(_cases(seed))


def response(text, refusal=None):
    message = SimpleNamespace(content=text, reasoning_content="thinking", refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))


class Fake:
    """Answers with `answer(model, messages)`; counts its calls."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
        return self.answer(kwargs["model"], kwargs["messages"])


def talking(messages):
    return "YOU ARE TALKING WITH" in messages[-1]["content"]


def note_reply(model, messages):
    """A good reply: one note written, which the engine carries out."""
    note = {"op": "write_notes", "text": "Check the rent."}
    if talking(messages):
        return response(json.dumps({"say": "Fine.", "acts": [note], "end": True}))
    return response(json.dumps({"thoughts": "", "acts": [note]}))


def run(cs, answer, n=2, cap=1.0):
    fake = Fake(answer)
    return probe.run(cs, FAMILIES, PRICES, fake, cap, n=n, cost_fn=lambda resp: COST, sleep_fn=lambda s: None), fake


def test_cases_are_four_and_stable():
    first, second = probe.cases(1, NAMES), probe.cases(1, NAMES)
    assert [c.label for c in first] == ["stock day", "short of money", "request to answer", "talk"]
    assert [c.kind for c in first] == ["turn", "turn", "turn", "talk"]
    assert [c.messages for c in first] == [c.messages for c in second]
    assert [c.pid for c in first] == [c.pid for c in second]
    assert [c.store.hash() for c in first] == [c.store.hash() for c in second]
    for c in first:
        assert [m["role"] for m in c.messages] == ["system", "user"]
    assert "YOU ARE TALKING WITH" in first[3].messages[-1]["content"] and first[3].day > 3
    shop = first[0]
    assert shop.store.get("people", shop.pid).stock_day == shop.day % 7
    asker = first[2]
    assert any(rq.to == asker.pid for rq in asker.store.where("requests", status="open"))


def test_run_scores_a_good_reply():
    cs = cases()
    rows, fake = run(cs, note_reply)
    assert len(rows) == len(FAMILIES) * len(cs) * 2 == len(fake.calls)
    assert [(r.family, r.label) for r in rows[:2]] == [("a", "stock day"), ("a", "stock day")]
    for r in rows:
        assert r.got_text and r.readable and r.acts == 1 and r.acts_ok == 1 and r.refusals == []
        assert not r.provider_refused and r.tokens_in == 100 and r.tokens_out == 20
        assert r.cost == pytest.approx(COST) and r.seconds >= 0 and r.reasoning == "thinking"
    for call, c in zip(fake.calls[:len(cs) * 2], [c for c in cs for _ in range(2)]):
        assert call["messages"] == c.messages and call["model"] == "openrouter/maker/alpha"


def test_run_scores_unreadable_and_refused_acts():
    cs = cases()[:1]
    other = next(p.name for p in cs[0].store.all("people") if p.id != cs[0].pid)

    def answer(model, messages):
        if model.endswith("alpha"):
            return response("I think I will restock the shelves today.")
        return response(json.dumps({"acts": [{"op": "book_appointment", "with": other, "day": "the 45th of Nevuary"}]}))
    rows, _ = run(cs, answer, n=1)
    prose, bad = rows
    assert prose.family == "a" and prose.got_text and not prose.readable and prose.acts == 0
    assert bad.family == "b" and bad.readable and bad.acts == 1 and bad.acts_ok == 0
    assert len(bad.refusals) == 1 and bad.refusals[0].startswith("Not done")


def test_applying_acts_does_not_touch_the_case_store():
    cs = cases()
    before = [c.store.hash() for c in cs]
    rows, _ = run(cs, note_reply, n=1)
    assert all(r.acts_ok == 1 for r in rows)
    assert [c.store.hash() for c in cs] == before
    again, _ = run(cs, note_reply, n=1)
    assert [(r.acts, r.acts_ok, r.refusals) for r in again] == [(r.acts, r.acts_ok, r.refusals) for r in rows]


def test_run_stops_on_provider_refusal():
    cs = cases()

    def answer(model, messages):
        if model.endswith("beta"):
            return response(None, refusal="I can't help with that.")
        return note_reply(model, messages)
    fake = Fake(answer)
    with pytest.raises(StopRun) as got:
        probe.run(cs, FAMILIES, PRICES, fake, 1.0, n=1, cost_fn=lambda r: COST, sleep_fn=lambda s: None)
    rows = got.value.rows
    assert str(got.value).startswith("refused: maker/beta")
    assert [r.family for r in rows] == ["a"] * len(cs) + ["b"] * len(cs)
    assert all(r.provider_refused for r in rows if r.family == "b")


def test_run_stops_at_the_cap():
    cs = cases()
    fake = Fake(note_reply)
    with pytest.raises(StopRun) as got:
        probe.run(cs, FAMILIES, PRICES, fake, 0.0001, n=1, sleep_fn=lambda s: None)
    assert str(got.value).startswith("cap: ") and fake.calls == [] and got.value.rows == []
    with pytest.raises(ValueError):
        probe.run(cs, FAMILIES, PRICES, fake, 2.0)


def test_table_lists_every_family():
    rows, _ = run(cases(), note_reply, n=1)
    rows[0].refusals = ["Not done: no such person."]
    text = probe.table(rows)
    lines = text.splitlines()
    assert lines[0].split()[:3] == ["family", "calls", "readable"]
    assert [ln.split()[0] for ln in lines if not ln.startswith(" ")][1:] == ["a", "b"]
    assert "1 x Not done: no such person." in text and "100%" in text


def test_cli_writes_rows_and_prints_the_table(tmp_path, monkeypatch):
    import townsim.cli as cli
    fake = Fake(note_reply)
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setattr(cli, "_litellm", lambda: (fake, lambda resp: COST))
    families = tmp_path / "families.yaml"
    families.write_text("".join(f"{n}:\n  model: {f.model}\n  params: {{max_tokens: 4000}}\n" for n, f in FAMILIES.items()))
    prices = tmp_path / "prices.yaml"
    prices.write_text("".join(f"{m}:\n  input_per_mtok: 0.1\n  output_per_mtok: 0.5\n" for m in PRICES))
    out = tmp_path / "probe" / "probe.json"
    runner = CliRunner()
    base = ["town-probe", "--families", str(families), "--prices", str(prices), "--out", str(out), "--n", "1"]
    assert runner.invoke(app, base).exit_code == 2                     # no --cap
    got = runner.invoke(app, [*base, "--cap", "2"])
    assert got.exit_code == 1 and "cap" in got.output and fake.calls == []
    got = runner.invoke(app, [*base, "--cap", "0.5"])
    assert got.exit_code == 0, got.output
    assert "family" in got.output and "not-a-real-key" not in got.output
    kept = json.loads(out.read_text())
    assert len(kept["rows"]) == 8 and kept["rows"][0]["text"] and kept["rows"][0]["reasoning"] == "thinking"
    assert kept["stopped"] is None and [c["label"] for c in kept["cases"]][-1] == "talk"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    got = runner.invoke(app, [*base, "--cap", "0.5"])
    assert got.exit_code == 1 and got.output.strip().startswith("no OPENROUTER_API_KEY in the environment")


def test_table_fails_a_family_that_writes_no_acts():
    cs = cases()

    def answer(model, messages):
        if model.endswith("beta"):
            if talking(messages):
                return response(json.dumps({"say": "Fine.", "acts": [], "end": True}))
            return response('{"acts": []}')
        return note_reply(model, messages)
    rows, _ = run(cs, answer, n=1)
    lines = probe.table(rows).splitlines()
    assert lines[0].split()[-1] == "passes" and "with act" in lines[0]
    a, b = [ln for ln in lines if ln.split()[0] in ("a", "b")]
    assert a.split()[-1] == "yes" and "4/4 100%" in a
    assert b.split()[-1] == "no" and " 0/0 " in b + " " and b.split()[3] == "0%"
    assert probe.passes([r for r in rows if r.family == "a"]) and not probe.passes([r for r in rows if r.family == "b"])


def test_scoring_keeps_the_rules_of_a_day_and_a_talk():
    cs = cases()
    turn, chat = cs[0], cs[3]
    notes = [{"op": "write_notes", "text": f"note {i}"} for i in range(10)]
    readable, acts, ok, refusals = probe.score(turn, json.dumps({"aims": ["Keep the shop going"], "acts": notes}))
    assert (readable, acts, ok) == (True, 10, 8)
    assert refusals == ["Not done: 2 more operations were written than the eight carried out in a day."]
    fenced = "Here it is:\n```json\n" + json.dumps({"acts": notes[:1]}) + "\n```"
    assert probe.score(turn, fenced)[:3] == (True, 1, 1)
    other = next(p.name for p in chat.store.all("people") if p.id != chat.pid)
    said = json.dumps({"say": "Let's talk later.", "acts": [{"op": "converse", "with": other, "opening": "Hello"}]})
    readable, acts, ok, refusals = probe.score(chat, said)
    assert (readable, acts, ok) == (True, 1, 0)
    assert refusals == ["Not done: that cannot be done in the middle of a talk."]
    readable, acts, ok, refusals = probe.score(chat, json.dumps({"say": "Hm.", "acts": notes[:6]}))
    assert (acts, ok) == (6, 4) and refusals[-1].startswith("Not done: 2 more operations were written than the four")


def test_a_case_that_fits_no_one_is_left_out(monkeypatch):
    lines = []
    monkeypatch.setattr(probe, "_shop_owner", lambda x: False)
    got = probe.cases(1, NAMES, echo=lines.append)
    assert [c.label for c in got] == ["short of money", "request to answer", "talk"]
    assert lines == ["seed 1: no view fits the case 'stock day'; it is left out"]
    monkeypatch.setattr(probe, "_has_request", lambda x: False)
    with pytest.raises(ValueError, match="only 2 of the four cases"):
        probe.cases(1, NAMES, echo=lines.append)


def _cli_files(tmp_path):
    families = tmp_path / "families.yaml"
    families.write_text("".join(f"{n}:\n  model: {f.model}\n  params: {{max_tokens: 4000}}\n" for n, f in FAMILIES.items()))
    prices = tmp_path / "prices.yaml"
    prices.write_text("".join(f"{m}:\n  input_per_mtok: 0.1\n  output_per_mtok: 0.5\n" for m in PRICES))
    return str(families), str(prices)


def test_cli_writes_the_rows_when_the_probe_stops(tmp_path, monkeypatch):
    import townsim.cli as cli
    marker = "sk-or-v1-SECRETVALUE"
    monkeypatch.setenv("OPENROUTER_API_KEY", marker)

    def answer(model, messages):
        if model.endswith("beta"):
            raise RuntimeError(f"401 Unauthorized: Authorization: Bearer {marker}")
        return note_reply(model, messages)
    fake = Fake(answer)
    monkeypatch.setattr(cli, "_litellm", lambda: (fake, None))
    monkeypatch.setattr("townsim.model.RETRY_SLEEPS", (0, 0))     # no waiting between attempts
    families, prices = _cli_files(tmp_path)
    out = tmp_path / "o.json"
    got = CliRunner().invoke(app, ["town-probe", "--families", families, "--prices", prices, "--out", str(out),
                                   "--n", "1", "--cap", "0.5", "--patience", "0"])
    assert got.exit_code == 1 and "stopped: unavailable: maker/beta" in got.output
    assert "SECRET" not in got.output and "SECRET" not in out.read_text()
    kept = json.loads(out.read_text())
    assert [r["family"] for r in kept["rows"]] == ["a"] * 4 and kept["stopped"].startswith("unavailable: ")

    refusing = Fake(lambda model, messages: response(None, refusal="no") if model.endswith("beta")
                    else note_reply(model, messages))
    monkeypatch.setattr(cli, "_litellm", lambda: (refusing, None))
    got = CliRunner().invoke(app, ["town-probe", "--families", families, "--prices", prices, "--out", str(out),
                                   "--n", "1", "--cap", "0.5"])
    kept = json.loads(out.read_text())
    assert got.exit_code == 1 and [r["family"] for r in kept["rows"]] == ["a"] * 4 + ["b"] * 4
    assert kept["stopped"].startswith("refused: maker/beta") and all(r["provider_refused"] for r in kept["rows"][4:])
