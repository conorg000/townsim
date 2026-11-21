"""The model replier, against a fake completion function: nothing here calls a model."""

import threading
import time
from types import SimpleNamespace

import pytest

from townsim.estimate import Price
from townsim.town.families import Family, worst_case
from townsim.town.modelreplier import ModelReplier
from townsim.town.replies import Reply, ReplyKey, StopRun

FAMILIES = {
    "a": Family("a", "maker/alpha", {"max_tokens": 100, "temperature": 0.8, "reasoning_effort": "none"}),
    "b": Family("b", "maker/beta", {"max_tokens": 200}),
}
PRICES = {"maker/alpha": Price(input_per_mtok=0.10, output_per_mtok=0.50),
          "maker/beta": Price(input_per_mtok=0.20, output_per_mtok=1.00)}
PEOPLE = [f"p{i:02d}" for i in range(1, 19)]
FAMILY_OF = {p: ("a" if i % 2 else "b") for i, p in enumerate(PEOPLE)}
MESSAGES = [{"role": "system", "content": "You are Ruth."}, {"role": "user", "content": "Today is Monday."}]


def response(text="ok", reasoning=None, finish="stop", usage=(10, 5), refusal=None):
    message = SimpleNamespace(content=text, reasoning_content=reasoning, refusal=refusal)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])
    if usage is not None:
        resp.usage = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
    return resp


class Fake:
    """A completion function: records each call and answers with `answer(model, messages)`."""

    def __init__(self, answer=lambda model, messages: response()):
        self.answer = answer
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
        return self.answer(kwargs["model"], kwargs["messages"])


def key(actor="p01", day=3, run="r"):
    return ReplyKey(run, "m", day, actor, "turn", "", 0)


def replier(fake, cap=10.0, spent=0.0, cost_fn=None, **kw):
    return ModelReplier(FAMILIES, PRICES, FAMILY_OF, fake, cap=cap, spent=lambda: spent, cost_fn=cost_fn,
                        sleep_fn=lambda s: None, **kw)


def test_routes_each_person_to_their_family():
    fake = Fake()
    r = replier(fake)
    assert r.model_for("p01") == "maker/beta" and r.model_for("p02") == "maker/alpha"
    r.ask(key("p01"), MESSAGES)
    r.ask(key("p02"), MESSAGES)
    assert [c["model"] for c in fake.calls] == ["openrouter/maker/beta", "openrouter/maker/alpha"]
    with pytest.raises(KeyError):
        r.model_for("p99")


def test_passes_messages_and_params_unchanged():
    fake = Fake()
    messages = [{"role": "system", "content": "  odd  spacing\n"}, {"role": "user", "content": "{\"acts\": []}"}]
    before = [dict(m) for m in messages]
    replier(fake).ask(key("p02"), messages)
    [call] = fake.calls
    assert call["messages"] is messages and messages == before
    assert {k: v for k, v in call.items() if k not in ("model", "messages", "timeout")} == FAMILIES["a"].params
    assert set(call) == {"model", "messages", "timeout", *FAMILIES["a"].params}


def test_reply_fields():
    fake = Fake(lambda m, msgs: response("hello", reasoning="hmm", finish="length", usage=(123, 45)))
    reply = replier(fake, cost_fn=lambda resp: 0.0042).ask(key(), MESSAGES)
    assert reply == Reply(text="hello", reasoning="hmm", finish="length", tokens_in=123, tokens_out=45,
                          cost=0.0042, error=None)


def _raises(resp):
    raise RuntimeError("no price known")


@pytest.mark.parametrize("cost_fn", [_raises, lambda resp: 0, lambda resp: -1.0, lambda resp: "0.1",
                                     lambda resp: float("nan"), lambda resp: True, None])
def test_cost_falls_back_to_prices(cost_fn):
    fake = Fake(lambda m, msgs: response("hi", usage=(1000, 200)))
    reply = replier(fake, cost_fn=cost_fn).ask(key("p02"), MESSAGES)     # family a: 0.10 / 0.50
    assert reply.cost == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)


@pytest.mark.parametrize("usage", [None, ("many", None), (None, 3.5), (True, False)])
def test_cost_falls_back_to_characters_when_counts_are_missing(usage):
    fake = Fake(lambda m, msgs: response("x" * 30, usage=usage))
    reply = replier(fake, cost_fn=lambda resp: 0).ask(key("p02"), MESSAGES)
    chars_in = sum(len(m["content"]) for m in MESSAGES)
    assert reply.tokens_in == -(-chars_in // 3) and reply.tokens_out == 10
    assert reply.cost > 0
    assert reply.cost == pytest.approx((reply.tokens_in * 0.10 + 10 * 0.50) / 1e6)


def test_a_reply_object_without_choices_still_costs_something():
    fake = Fake(lambda m, msgs: object())
    reply = replier(fake).ask(key("p02"), MESSAGES)
    assert reply.text is None and reply.error is None and reply.cost > 0


def test_cap_stops_before_sending():
    fake = Fake()
    cap = 1.0
    hair = worst_case(FAMILIES["a"], PRICES["maker/alpha"], MESSAGES) / 2
    r = replier(fake, cap=cap, spent=cap - hair)
    with pytest.raises(StopRun):
        r.ask(key("p02"), MESSAGES)
    assert fake.calls == [] and r.stopped.startswith("cap:")


def test_exactly_at_the_cap_is_allowed():
    fake = Fake()
    worst = worst_case(FAMILIES["a"], PRICES["maker/alpha"], MESSAGES)
    r = ModelReplier(FAMILIES, PRICES, FAMILY_OF, fake, cap=0.5 + worst, spent=lambda: 0.5)
    r.ask(key("p02"), MESSAGES)
    assert len(fake.calls) == 1 and r.stopped is None


def test_unavailable_stops_and_returns_nothing():
    def down(model, messages):
        raise TimeoutError("took too long")
    fake = Fake(down)
    r = replier(fake)
    with pytest.raises(StopRun):
        r.ask(key("p02"), MESSAGES)
    assert len(fake.calls) == 3 and r.stopped.startswith("unavailable: maker/alpha:")


def test_refusal_is_returned_then_everything_stops():
    fake = Fake(lambda m, msgs: response(None, finish="content_filter"))
    r = replier(fake)
    reply = r.ask(key("p02"), MESSAGES)
    assert reply.error.startswith("refused:") and reply.cost > 0 and reply.finish == "content_filter"
    assert r.stopped.startswith("refused: maker/alpha:")
    with pytest.raises(StopRun):
        r.ask(key("p03"), MESSAGES)
    assert all(isinstance(x, StopRun) for x in r.ask_many([(key("p04"), MESSAGES), (key("p05"), MESSAGES)]))
    assert len(fake.calls) == 1


def test_a_cut_off_filter_with_text_is_not_a_refusal():
    fake = Fake(lambda m, msgs: response("partial answer", finish="content_filter"))
    r = replier(fake)
    assert r.ask(key("p02"), MESSAGES).error is None and r.stopped is None


def test_refusal_field_on_message_counts():
    fake = Fake(lambda m, msgs: response("", finish="stop", refusal="I can't help with that."))
    r = replier(fake)
    reply = r.ask(key("p01"), MESSAGES)
    assert reply.error.startswith("refused: maker/beta:") and "I can't help with that." in reply.error
    with pytest.raises(StopRun):
        r.ask(key("p02"), MESSAGES)


def test_ask_many_keeps_order_and_isolates_failures():
    def answer(model, messages):
        who = messages[-1]["content"]
        if who == "p05":
            raise ConnectionError("reset")
        if who == "p11":
            return response(None, finish="content_filter")
        time.sleep(0.01)
        return response(f"reply for {who}")
    fake = Fake(answer)
    r = replier(fake)
    pairs = [(key(p), [{"role": "user", "content": p}]) for p in PEOPLE]
    got = r.ask_many(pairs)
    assert len(got) == 18
    assert isinstance(got[4], StopRun)
    assert isinstance(got[10], Reply) and got[10].error.startswith("refused:")
    others = [g for i, g in enumerate(got) if i not in (4, 10)]
    assert [g.text for g in others] == [f"reply for {p}" for i, p in enumerate(PEOPLE) if i not in (4, 10)]
    assert all(g.error is None and g.cost > 0 for g in others)
    assert r.stopped.startswith("unavailable:") or r.stopped.startswith("refused:")
    assert len(fake.calls) == 17 + 3


def test_ask_many_runs_calls_side_by_side():
    gate = threading.Barrier(4, timeout=5)

    def answer(model, messages):
        gate.wait()                     # only returns once four calls are in flight together
        return response()
    got = replier(Fake(answer), workers=4).ask_many([(key(p), MESSAGES) for p in PEOPLE[:4]])
    assert all(isinstance(g, Reply) for g in got)


def test_a_hung_call_does_not_hold_the_others():
    release = threading.Event()

    def answer(model, messages):
        if messages[-1]["content"] == "p03":
            release.wait(10)
        return response(messages[-1]["content"])
    r = replier(Fake(answer), deadline=0.3)
    try:
        got = r.ask_many([(key(p), [{"role": "user", "content": p}]) for p in PEOPLE])
    finally:
        release.set()
    assert isinstance(got[2], StopRun) and r.stopped.startswith("unavailable: ")
    assert [g.text for i, g in enumerate(got) if i != 2] == [p for i, p in enumerate(PEOPLE) if i != 2]


def test_ask_many_checks_the_cap_for_the_whole_batch():
    fake = Fake()
    pairs = [(key(p), MESSAGES) for p in PEOPLE[:4]]
    worsts = [worst_case(FAMILIES[FAMILY_OF[p]], PRICES[FAMILIES[FAMILY_OF[p]].model], MESSAGES) for p in PEOPLE[:4]]
    r = replier(fake, cap=1.0, spent=1.0 - sum(worsts) + 1e-9)   # each call fits alone; the four do not
    got = r.ask_many(pairs)
    assert all(isinstance(g, StopRun) for g in got) and fake.calls == [] and r.stopped.startswith("cap:")
    r = replier(fake, cap=1.0, spent=1.0 - sum(worsts))
    assert all(isinstance(g, Reply) for g in r.ask_many(pairs)) and len(fake.calls) == 4


def test_ask_many_of_nothing_sends_nothing():
    fake = Fake()
    assert replier(fake).ask_many([]) == [] and fake.calls == []


@pytest.mark.parametrize("text", [
    "I think I will go to the shop today.",
    "```json\n{\"acts\": []}\n```",
    "{\"acts\": []}{\"acts\": [{\"op\": \"pay\"}]}",
    "Let me see.\n{\"acts\": [{\"op\": \"pay\", \"to\": \"p02\"}]}",
    "{\"acts\": [",
    "",
])
def test_unreadable_text_is_just_text(text):
    fake = Fake(lambda m, msgs: response(text))
    r = replier(fake)
    assert r.ask(key(), MESSAGES).text == text
    assert r.ask_many([(key("p02"), MESSAGES)])[0].text == text
    assert r.stopped is None


def test_a_family_above_the_ceiling_is_refused_by_the_replier_too():
    dear = {"maker/alpha": Price(input_per_mtok=0.5, output_per_mtok=0.5), "maker/beta": PRICES["maker/beta"]}
    with pytest.raises(ValueError, match="maker/alpha"):
        ModelReplier(FAMILIES, dear, FAMILY_OF, Fake(), cap=1.0, spent=lambda: 0.0)
    with pytest.raises(ValueError, match="maker/alpha"):
        ModelReplier(FAMILIES, {"maker/beta": PRICES["maker/beta"]}, FAMILY_OF, Fake(), cap=1.0, spent=lambda: 0.0)


def test_a_refusal_later_in_a_batch_than_a_failure_still_stops_everything():
    def answer(model, messages):
        if messages[-1]["content"] == "p01":
            raise ConnectionError("reset")
        if messages[-1]["content"] == "p02":
            return response(None, refusal="No.")
        return response()
    fake = Fake(answer)
    r = replier(fake)
    got = r.ask_many([(key(p), [{"role": "user", "content": p}]) for p in PEOPLE[:3]])
    assert isinstance(got[0], StopRun) and got[1].error.startswith("refused:") and got[2].error is None
    assert r.stopped.startswith("unavailable:")
    calls = len(fake.calls)
    with pytest.raises(StopRun):
        r.ask(key("p03"), MESSAGES)
    assert len(fake.calls) == calls


def test_the_replier_reads_no_key_and_imports_no_client():
    import townsim.town.modelreplier as mod
    source = open(mod.__file__, encoding="utf-8").read()
    assert "environ" not in source and "getenv" not in source and "import litellm" not in source
