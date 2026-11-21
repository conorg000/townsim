"""The model replier, against a fake completion function: nothing here calls a model."""

import threading
import time
from types import SimpleNamespace

import pytest

from townsim.estimate import Price
from townsim.town.families import Family, worst_case
from townsim.town.modelreplier import ModelReplier
from townsim.town.replies import Reply, ReplyKey, StopRun, encode_reply

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
                                     lambda resp: float("nan"), lambda resp: True, None, lambda resp: 1e-9])
def test_cost_falls_back_to_prices(cost_fn):
    fake = Fake(lambda m, msgs: response("hi", usage=(1000, 200)))
    reply = replier(fake, cost_fn=cost_fn).ask(key("p02"), MESSAGES)     # family a: 0.10 / 0.50
    assert reply.cost == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)


@pytest.mark.parametrize("usage", [None, ("many", None), (None, 3.5), (True, False)])
def test_cost_falls_back_to_characters_when_counts_are_missing(usage):
    fake = Fake(lambda m, msgs: response("x" * 30, usage=usage))
    reply = replier(fake, cost_fn=lambda resp: 0).ask(key("p02"), MESSAGES)
    chars_in = sum(len(m["content"]) for m in MESSAGES)
    assert reply.tokens_in == 0 and reply.tokens_out == 0           # what the provider said: nothing
    floor_in = -(-chars_in // 3) + 4 * len(MESSAGES)
    assert reply.cost == pytest.approx((floor_in * 0.10 + 10 * 0.50) / 1e6) and reply.cost > 0


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
    r = ModelReplier(FAMILIES, PRICES, FAMILY_OF, fake, cap=0.5 + 3 * worst, spent=lambda: 0.5)
    r.ask(key("p02"), MESSAGES)
    assert len(fake.calls) == 1 and r.stopped is None
    r = ModelReplier(FAMILIES, PRICES, FAMILY_OF, fake, cap=0.5 + 3 * worst - 1e-12, spent=lambda: 0.5)
    with pytest.raises(StopRun):                    # every attempt's worst case counts, not one
        r.ask(key("p02"), MESSAGES)
    assert len(fake.calls) == 1


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
    assert r.stopped == str(got[4]) and r.stopped.startswith("unavailable: maker/beta: ")   # the first in order
    assert len(fake.calls) == 17 + 3


def test_ask_many_runs_calls_side_by_side():
    gate = threading.Barrier(4, timeout=5)

    def answer(model, messages):
        gate.wait()                     # only returns once four calls are in flight together
        return response()
    got = replier(Fake(answer), workers=4).ask_many([(key(p), MESSAGES) for p in PEOPLE[:4]])
    assert all(isinstance(g, Reply) for g in got)


def test_a_call_that_raises_does_not_lose_the_others():
    def answer(model, messages):
        if messages[-1]["content"] == "p03":
            raise ConnectionError("reset")
        time.sleep(0.01)
        return response(messages[-1]["content"])
    r = replier(Fake(answer))
    got = r.ask_many([(key(p), [{"role": "user", "content": p}]) for p in PEOPLE])
    assert isinstance(got[2], StopRun)
    assert [g.text for i, g in enumerate(got) if i != 2] == [p for i, p in enumerate(PEOPLE) if i != 2]


def test_ask_many_checks_the_cap_for_the_whole_batch():
    fake = Fake()
    pairs = [(key(p), MESSAGES) for p in PEOPLE[:4]]
    worsts = [worst_case(FAMILIES[FAMILY_OF[p]], PRICES[FAMILIES[FAMILY_OF[p]].model], MESSAGES) for p in PEOPLE[:4]]
    r = replier(fake, cap=1.0, spent=1.0 - 3 * sum(worsts) + 1e-9)   # each call fits alone; the four do not
    got = r.ask_many(pairs)
    assert all(isinstance(g, StopRun) for g in got) and fake.calls == [] and r.stopped.startswith("cap:")
    r = replier(fake, cap=1.0, spent=1.0 - 3 * sum(worsts))
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


def test_a_key_in_the_environment_never_reaches_a_call_or_a_reply(monkeypatch):
    marker = "sk-or-MARKER-7f3a9c"
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, marker)
    fake = Fake(lambda m, msgs: response("fine"))
    r = replier(fake, cost_fn=lambda resp: 1e-6)
    replies = [r.ask(key("p01"), MESSAGES)] + r.ask_many([(key(p), MESSAGES) for p in PEOPLE[:4]])
    assert fake.calls and marker not in repr(fake.calls)
    assert all(marker not in encode_reply(x) for x in replies)
    assert marker not in repr(vars(r))


@pytest.mark.parametrize("extra", [{"n": 5}, {"fallbacks": ["x/y"]}, {"api_base": "http://x"}, {"model": "x/y"},
                                   {"extra_body": {"models": ["x/y"]}}, {"timeout": 900}])
def test_the_replier_refuses_a_hand_built_family_with_other_params(extra):
    bad = dict(FAMILIES, a=Family("a", "maker/alpha", {"max_tokens": 100, **extra}))
    with pytest.raises(ValueError, match=f"'a'.*{next(iter(extra))}"):
        ModelReplier(bad, PRICES, FAMILY_OF, Fake(), cap=1.0, spent=lambda: 0.0)


def test_a_person_dealt_a_family_not_loaded_fails_at_construction():
    with pytest.raises(ValueError, match=r"'p07'.*'minimax'"):
        ModelReplier(FAMILIES, PRICES, dict(FAMILY_OF, p07="minimax"), Fake(), cap=1.0, spent=lambda: 0.0)


def test_a_cost_function_that_under_reports_cannot_hide_spend():
    fake = Fake(lambda m, msgs: response("hi", usage=(1000, 200)))
    listed = (1000 * 0.10 + 200 * 0.50) / 1e6
    assert replier(fake, cost_fn=lambda resp: 1e-9).ask(key("p02"), MESSAGES).cost == pytest.approx(listed)
    assert replier(fake, cost_fn=lambda resp: 0.5).ask(key("p02"), MESSAGES).cost == 0.5


def test_each_attempt_that_raised_is_told_to_lost_in_order_after_the_batch():
    tries = {}
    lock = threading.Lock()
    caller = threading.get_ident()
    told = []

    def answer(model, messages):
        who = messages[-1]["content"]
        with lock:
            tries[who] = tries.get(who, 0) + 1
            n = tries[who]
        if who == "p02" and n == 1:
            raise TimeoutError("once")
        if who == "p04":
            raise TimeoutError("always")
        return response(who)

    def lost(dollars):
        told.append((dollars, threading.get_ident()))
    pairs = [(key(p), [{"role": "user", "content": p}]) for p in PEOPLE[:5]]
    r = replier(Fake(answer), lost=lost)
    got = r.ask_many(pairs)
    assert isinstance(got[3], StopRun) and all(isinstance(g, Reply) for i, g in enumerate(got) if i != 3)
    w = [worst_case(FAMILIES[FAMILY_OF[k.actor]], PRICES[FAMILIES[FAMILY_OF[k.actor]].model], m) for k, m in pairs]
    assert [d for d, _ in told] == [w[1], w[3], w[3], w[3]]          # p02 once, p04 three times, in order
    assert {t for _, t in told} == {caller}


def test_counts_below_what_can_be_seen_are_floored_for_the_cost_only():
    fake = Fake(lambda m, msgs: response("x" * 30, reasoning="y" * 30, usage=(0, 0)))
    reply = replier(fake, cost_fn=lambda resp: 0).ask(key("p02"), MESSAGES)
    floor_in = -(-sum(len(m["content"]) for m in MESSAGES) // 3) + 4 * len(MESSAGES)
    assert (reply.tokens_in, reply.tokens_out) == (0, 0)
    assert reply.cost == pytest.approx((floor_in * 0.10 + 20 * 0.50) / 1e6)
    fake = Fake(lambda m, msgs: response("x" * 30, usage=(5000, 900)))   # above the floors: as reported
    reply = replier(fake).ask(key("p02"), MESSAGES)
    assert (reply.tokens_in, reply.tokens_out) == (5000, 900)
    assert reply.cost == pytest.approx((5000 * 0.10 + 900 * 0.50) / 1e6)


class _Exploding:
    """A reply whose `choices` raises when read."""

    @property
    def choices(self):
        raise RuntimeError("body unreadable")


def test_an_answer_that_cannot_be_read_counts_as_one_lost_attempt():
    told = []

    def answer(model, messages):
        return _Exploding() if messages[-1]["content"] == "p02" else response("fine")
    pairs = [(key(p), [{"role": "user", "content": p}]) for p in PEOPLE[:3]]
    r = replier(Fake(answer), lost=told.append)
    got = r.ask_many(pairs)
    assert isinstance(got[1], StopRun) and str(got[1]).startswith("unavailable: maker/alpha:")
    assert [g.text for i, g in enumerate(got) if i != 1] == ["fine", "fine"]
    assert told == [worst_case(FAMILIES["a"], PRICES["maker/alpha"], pairs[1][1])]


def test_a_lost_callback_that_raises_keeps_the_batch_and_stops_everything_after():
    def answer(model, messages):
        if messages[-1]["content"] == "p02":
            raise TimeoutError("gave up")
        return response("fine")

    def lost(dollars):
        raise OSError("disk full")
    fake = Fake(answer)
    r = replier(fake, lost=lost)
    got = r.ask_many([(key(p), [{"role": "user", "content": p}]) for p in PEOPLE[:3]])
    assert got[0].text == got[2].text == "fine" and isinstance(got[1], StopRun)
    assert r.stopped.startswith("unavailable: lost-spend callback failed")
    calls = len(fake.calls)
    with pytest.raises(StopRun):
        r.ask(key("p04"), MESSAGES)
    assert all(isinstance(g, StopRun) for g in r.ask_many([(key("p05"), MESSAGES)]))
    assert len(fake.calls) == calls and r.stopped.startswith("unavailable: lost-spend callback failed")


def test_a_key_added_to_params_after_the_check_never_reaches_the_call():
    given = {"max_tokens": 100}
    fams = dict(FAMILIES, a=Family("a", "maker/alpha", given))
    fake = Fake()
    r = ModelReplier(fams, PRICES, FAMILY_OF, fake, cap=1.0, spent=lambda: 0.0)
    given["n"] = 50
    with pytest.raises(TypeError):
        fams["a"].params["fallbacks"] = ["x/y"]
    with pytest.raises(TypeError):
        r.families["a"].params["n"] = 50
    r.ask(key("p02"), MESSAGES)
    assert set(fake.calls[0]) == {"model", "messages", "timeout", "max_tokens"}


@pytest.mark.parametrize("raw, kept", [
    ("bad key sk-or-v1-abcdef123456 given", "bad key [removed] given"),
    ("header Bearer abc.def-ghi here", "header [removed] here"),
    ("Authorization: Bearer xyz123", "Authorization: [removed]"),
    ("authorization:secretvalue rest", "authorization: [removed] rest"),
    ("url?api_key=abcdef&x=1", "url?api_key= [removed]"),
    ("API-KEY: abcdef0123", "API-KEY: [removed]"),
    ("x-api-key: abcdef0123 more", "x-api-key: [removed] more"),
    ("many   spaces\n\tand lines", "many spaces and lines"),
])
def test_safe_removes_what_looks_like_a_key(raw, kept):
    from townsim.town.modelreplier import safe
    assert safe(raw) == kept


def test_safe_removes_a_key_in_the_environment_and_cuts(monkeypatch):
    from townsim.town.modelreplier import SAFE_CHARS, safe
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "plainvalue42")
    monkeypatch.setenv("GITHUB_TOKEN", "tokenvalue99")
    monkeypatch.setenv("SHORT_TOKEN", "abc")
    assert safe("got plainvalue42 and tokenvalue99 and abc") == "got [removed] and [removed] and abc"
    long = safe("x" * 1000)
    assert len(long) == SAFE_CHARS and long.endswith("\u2026")


def test_a_key_in_a_provider_error_is_not_kept(monkeypatch):
    marker = "sk-or-v1-SECRETVALUE"
    monkeypatch.setenv("OPENROUTER_API_KEY", marker)

    def fail(model, msgs):
        raise RuntimeError(f"401 Unauthorized: Authorization: Bearer {marker}")
    r = replier(Fake(fail))
    with pytest.raises(StopRun) as got:
        r.ask(key(), MESSAGES)
    assert "SECRET" not in str(got.value) and "SECRET" not in r.stopped
    refusing = replier(Fake(lambda m, msgs: response(None, refusal=f"Bad credentials {marker}")))
    reply = refusing.ask(key(), MESSAGES)
    assert reply.error.startswith("refused: ") and "SECRET" not in reply.error


def test_a_reply_costing_more_than_its_worst_case_stops_after_its_batch():
    fake = Fake(lambda m, msgs: response("ok", usage=(10, 10 * 100 * 10)))      # ten times max_tokens and more
    r = replier(fake)
    pairs = [(key(p), MESSAGES) for p in PEOPLE[:4]]
    first = r.ask_many(pairs)
    assert all(isinstance(x, Reply) for x in first) and len(fake.calls) == 4
    assert r.stopped.startswith("cap: a reply cost $") and "more than its worst case $" in r.stopped
    second = r.ask_many(pairs)
    assert all(isinstance(x, StopRun) for x in second) and len(fake.calls) == 4
