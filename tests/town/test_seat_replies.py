"""Replies that carry tool calls, and what they may cost: nothing here calls a model."""

import json
import math
from types import SimpleNamespace

import pytest

from townsim.estimate import Price
from townsim.town.families import BILLED_MARGIN, Family, load_seat, seat_worst_case
from townsim.town.modelreplier import ModelReplier
from townsim.town.replies import (Keys, Recording, ReplayMismatch, Replaying, ReplyKey, ScriptedReplier, StopRun,
                                  prompt_hash, tool_prompt_hash)
from townsim.town.rundb import RunDB

SEAT = Family("seat", "maker/seat", {"max_tokens": 4000, "reasoning_effort": "low"})
PEOPLE = {"a": Family("a", "maker/alpha", {"max_tokens": 100}), "b": Family("b", "maker/beta", {"max_tokens": 100})}
PRICES = {m: Price(input_per_mtok=0.10, output_per_mtok=0.50) for m in ("maker/seat", "maker/alpha", "maker/beta")}
TOOLS = [{"type": "function", "function": {"name": "note", "description": "Replace your notes.",
                                           "parameters": {"type": "object", "properties": {"text": {"type": "string"}}}}}]
MESSAGES = [{"role": "system", "content": "You are an assistant."}, {"role": "user", "content": "TODAY: Monday."}]


def _resp(name="note", args='{"text": "x"}', extra=None, text=None, calls=1):
    tc = [SimpleNamespace(id=f"c{i}", type="function", function=SimpleNamespace(name=name, arguments=args))
          for i in range(calls)]
    message = SimpleNamespace(content=text, reasoning_content="thinking", refusal=None, tool_calls=tc,
                              reasoning_details=extra)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
                           usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20))


class Fake:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.resp


def _seat_replier(fake, cap=1.0, spent=0.0):
    return ModelReplier(PEOPLE, PRICES, {"p1": "a"}, fake, cap=cap, spent=lambda: spent, sleep_fn=lambda s: None,
                        seat=SEAT)


def test_recording_stores_tool_reply_and_replaying_serves_it(tmp_path):
    with RunDB(str(tmp_path / "r.sqlite")) as db:
        db.create_run("r", seed=1)
        inner = ScriptedReplier(lambda k, m: "x", lambda k, m, t: {"name": "note", "arguments": {"text": "hi"}})
        key = Keys("r").next("scripted", 7, "as1", "seat")
        got = Recording(inner, db, "r").ask_tools(key, MESSAGES, TOOLS)
        assert got.tool["calls"] == [{"id": "call_0", "name": "note", "arguments": '{"text": "hi"}'}]
        assert got.tool["message"]["tool_calls"][0]["function"]["name"] == "note"
        again = Recording(ScriptedReplier(lambda k, m: None), db, "r").ask_tools(key, MESSAGES, TOOLS)
        assert again == got                                   # served as stored, never asked again
        rp = Replaying(db, "r")
        assert rp.ask_tools(key, MESSAGES, TOOLS) == got and rp.unused() == []
        with pytest.raises(ReplayMismatch) as e:
            Replaying(db, "r").ask_tools(key, MESSAGES, TOOLS + TOOLS)
        assert e.value.why == "prompt changed"


def test_tool_prompt_hash_changes_with_tools():
    assert tool_prompt_hash(MESSAGES, TOOLS) != tool_prompt_hash(MESSAGES, [])
    assert tool_prompt_hash(MESSAGES, TOOLS) != prompt_hash(MESSAGES)
    assert tool_prompt_hash(MESSAGES, TOOLS) == tool_prompt_hash([dict(m) for m in MESSAGES], json.loads(json.dumps(TOOLS)))


def test_model_replier_sends_tools_with_parallel_off():
    fake = Fake(_resp(extra=[{"type": "reasoning.text", "text": "hmm"}], calls=2))
    r = _seat_replier(fake)
    assert r.model_for("as3") == "maker/seat" and r.model_for("p1") == "maker/alpha"
    key = ReplyKey("r", "maker/seat", 7, "as1", "seat", "", 0)
    reply = r.ask_tools(key, MESSAGES, TOOLS)
    [call] = fake.calls
    assert call["tools"] is TOOLS and call["tool_choice"] == "auto" and call["parallel_tool_calls"] is False
    assert call["model"] == "openrouter/maker/seat" and call["max_tokens"] == 4000 and call["reasoning_effort"] == "low"
    assert len(reply.tool["calls"]) == 2                         # both kept, the first alone sent back
    msg = reply.tool["message"]
    assert msg["role"] == "assistant" and len(msg["tool_calls"]) == 1 and msg["tool_calls"][0]["id"] == "c0"
    assert msg["reasoning_details"] == [{"type": "reasoning.text", "text": "hmm"}]
    assert msg["reasoning_content"] == "thinking" and reply.reasoning == "thinking"
    json.dumps(msg)                                              # plain JSON
    # a long argument counts as output in what the call is recorded to cost
    big = _seat_replier(Fake(_resp(args=json.dumps({"text": "w " * 3000})))).ask_tools(key, MESSAGES, TOOLS)
    assert big.cost >= math.ceil(6000 / 3) * 0.50 / 1e6
    # a people's call is unchanged: no tools are sent
    plain = Fake(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}", reasoning_content=None,
                                                                                 refusal=None), finish_reason="stop")]))
    _seat_replier(plain).ask(ReplyKey("r", "maker/alpha", 7, "p1", "turn", "", 0), MESSAGES)
    assert "tools" not in plain.calls[0] and "parallel_tool_calls" not in plain.calls[0]


def test_seat_worst_case_counts_tools_and_calls():
    price = PRICES["maker/seat"]
    base = seat_worst_case(SEAT, price, MESSAGES, [])
    with_tools = seat_worst_case(SEAT, price, MESSAGES, TOOLS)
    assert with_tools - base == pytest.approx(BILLED_MARGIN * math.ceil(len(json.dumps(TOOLS, sort_keys=True)) / 3)
                                              * price.input_per_mtok / 1e6, rel=0.05)
    call = {"role": "assistant", "content": None, "reasoning_details": [{"text": "r" * 3000}],
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": "note", "arguments": "a" * 3000}}]}
    grown = MESSAGES + [call, {"role": "tool", "tool_call_id": "c", "content": "b" * 3000}]
    more = seat_worst_case(SEAT, price, grown, TOOLS) - with_tools
    assert more >= BILLED_MARGIN * (9000 / 3) * price.input_per_mtok / 1e6
    assert base >= BILLED_MARGIN * 4000 * price.output_per_mtok / 1e6
    # the cap is checked against it before every call: one that would pass it is not sent
    fake = Fake(_resp())
    r = _seat_replier(fake, cap=0.003, spent=0.0)
    with pytest.raises(StopRun):
        r.ask_tools(ReplyKey("r", "m", 7, "as1", "seat", "", 0), MESSAGES, TOOLS)
    assert fake.calls == [] and r.stopped.startswith("cap: ")


def test_load_seat_refuses_dear_model_and_other_params(tmp_path):
    prices = tmp_path / "p.yaml"
    prices.write_text("cheap/one:\n  input_per_mtok: 0.1\n  output_per_mtok: 0.5\n"
                      "dear/one:\n  input_per_mtok: 3.0\n  output_per_mtok: 15.0\n")

    def load(text):
        f = tmp_path / "s.yaml"
        f.write_text(text)
        return load_seat(str(f), str(prices))
    got = load("seat:\n  model: cheap/one\n  params: {max_tokens: 4000, reasoning_effort: low}\n")
    assert got.model == "cheap/one" and dict(got.params) == {"max_tokens": 4000, "reasoning_effort": "low"}
    for bad in ("seat:\n  model: dear/one\n  params: {max_tokens: 100}\n",
                "seat:\n  model: cheap/one\n  params: {max_tokens: 100, n: 4}\n",
                "seat:\n  model: cheap/one\n  params: {max_tokens: 9000}\n",
                "seat:\n  model: cheap/one\n  params: {max_tokens: 100}\nother:\n  model: cheap/one\n",
                "people:\n  model: cheap/one\n  params: {max_tokens: 100}\n"):
        with pytest.raises(ValueError):
            load(bad)
    real = load_seat("config/seat.yaml", "config/prices.yaml")
    assert real.model == "anthropic/claude-haiku-5.5" and "temperature" not in real.params
    with pytest.raises(ValueError):                         # a dear seat is refused by the replier too
        ModelReplier(PEOPLE, {**PRICES, "dear/one": Price(input_per_mtok=3.0, output_per_mtok=15.0)}, {}, Fake(None), cap=1.0, spent=lambda: 0.0,
                     seat=Family("seat", "dear/one", {"max_tokens": 100}))


def test_people_keys_unchanged():
    keys = Keys("r")
    assert keys.next("m", 3, "p1", "turn").text() == "r|m|d3|p1|turn:|0"
    assert keys.next("m", 3, "as1", "seat").slot() == "r|d3|as1|seat:|0"
    assert keys.next("m", 3, "as1", "seat").n == 1
