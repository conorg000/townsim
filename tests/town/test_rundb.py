"""What a run spent, its stored replies with their keys, and the notes kept with it; and a
run played by the model replier, on a fake completion function, kept, stopped and replayed."""

import sqlite3
import threading
from types import SimpleNamespace

import pytest

from townsim.estimate import Price
from townsim.town import families as fam
from townsim.town import gen, hamlet_script
from townsim.town.families import Family
from townsim.town.modelreplier import ModelReplier
from townsim.town.replies import Keys, Recording, Reply, ScriptedReplier, prompt_hash
from townsim.town.rundb import SCHEMA, RunDB
from townsim.town.runs import replay, resume, run

MSGS = [{"role": "user", "content": "view"}]


@pytest.fixture
def db(tmp_path):
    d = RunDB(str(tmp_path / "x.sqlite"))
    d.create_run("r1", seed=1)
    d.create_run("r2", seed=1)
    yield d
    d.close()


def _put(db, run, actor, reply, day=0):
    key = Keys(run).next("m", day, actor, "turn")
    db.put_reply(run, key.text(), key.slot(), prompt_hash(MSGS), reply)
    return key.text()


def test_spent_sums_stored_costs(db):
    assert db.spent("r1") == 0.0 and isinstance(db.spent("r1"), float)
    _put(db, "r1", "p01", Reply(text="a", cost=0.25))
    _put(db, "r1", "p02", Reply(text=None, cost=0.125, error="refused: m: no"))
    _put(db, "r1", "p03", Reply(text="free"))
    _put(db, "r2", "p01", Reply(text="other run", cost=4.0))
    assert db.spent("r1") == pytest.approx(0.375) and db.spent("r2") == pytest.approx(4.0)
    assert db.spent("nobody") == 0.0


def test_replies_in_stored_order(db):
    first = _put(db, "r1", "p02", Reply(text="b", cost=0.1))
    second = _put(db, "r1", "p01", Reply(text="a", tokens_in=3))
    _put(db, "r2", "p01", Reply(text="elsewhere"))
    assert db.replies("r1") == [(first, Reply(text="b", cost=0.1)), (second, Reply(text="a", tokens_in=3))]
    assert db.replies("nobody") == []


def test_notes_round_trip_and_replace(db):
    assert db.note("r1", "families") is None
    db.put_note("r1", "families", "haiku: anthropic/claude-haiku-5.5")
    db.put_note("r2", "families", "other")
    db.put_note("r1", "stopped", "cap: $1.00")
    assert db.note("r1", "families") == "haiku: anthropic/claude-haiku-5.5"
    db.put_note("r1", "families", "replaced")
    assert (db.note("r1", "families"), db.note("r2", "families"), db.note("r1", "stopped")) == (
        "replaced", "other", "cap: $1.00")


def test_old_file_gains_notes_table(tmp_path):
    path = str(tmp_path / "old.sqlite")
    with sqlite3.connect(path) as c:                    # a file as runs were kept before notes
        c.execute("create table runs (id text primary key, seed integer not null, parent text, "
                  "parent_day integer, schema integer not null)")
        c.execute("insert into runs values ('old', 1, null, null, ?)", (SCHEMA,))
    with RunDB(path) as old:
        assert old.note("old", "x") is None
        old.put_note("old", "x", "kept")
    with RunDB(path) as old:
        assert old.note("old", "x") == "kept" and old.get_run("old")["schema"] == SCHEMA
    assert SCHEMA == 2


# a run played by the model replier, on a fake completion function

PRICES = {"cheap/a": Price(input_per_mtok=0.10, output_per_mtok=0.50),
          "cheap/b": Price(input_per_mtok=0.05, output_per_mtok=0.20)}


def _families(names):
    return {n: Family(n, ("cheap/a", "cheap/b")[i % 2], {"max_tokens": 300}) for i, n in enumerate(names)}


def _fake():
    """A completion function answering every prompt with an empty turn. Set `refuse_next` and
    the next call it answers is refused."""
    lock = threading.Lock()

    def completion(**kw):
        text = kw["messages"][-1]["content"]
        message = SimpleNamespace(content='{"acts": []}', reasoning_content=None, refusal=None)
        with lock:
            completion.calls.append(kw)
            if completion.refuse_next:
                completion.refuse_next = False
                message.content, message.refusal = None, "I can't help with that."
        usage = SimpleNamespace(prompt_tokens=len(text) // 4, completion_tokens=6)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)
    completion.calls = []
    completion.refuse_next = False
    return completion


def _model_run(db, run_id, days, completion, cap=5.0, store=None):
    names = fam.names("config/families.yaml")
    s = store if store is not None else gen.generate(1, names)
    family_of = {p.id: p.family for p in s.all("people")}
    replier = ModelReplier(_families(names), PRICES, family_of, completion, cap=cap,
                           spent=lambda: db.spent(run_id), sleep_fn=lambda t: None)
    return run(db, run_id, s, replier, days), replier


def test_a_model_run_keeps_its_spend_and_replays_exactly(tmp_path):
    with RunDB(str(tmp_path / "m.sqlite")) as db:
        db.create_run("m", seed=1)
        completion = _fake()
        end, replier = _model_run(db, "m", 10, completion)
        assert end.day == 10 and replier.stopped is None
        assert len(completion.calls) == len(db.replies("m")) > 0
        assert all(r.cost > 0 for _, r in db.replies("m"))
        assert db.spent("m") == pytest.approx(sum(r.cost for _, r in db.replies("m")))
        models = {k.split("|")[1] for k, _ in db.replies("m")}
        assert models == {"cheap/a", "cheap/b"}
        assert {c["model"] for c in completion.calls} == {"openrouter/cheap/a", "openrouter/cheap/b"}
        assert replay(db, "m") == []


def test_a_model_run_stops_at_its_cap_and_goes_on_under_a_higher_one(tmp_path):
    with RunDB(str(tmp_path / "c.sqlite")) as db:
        db.create_run("c", seed=1)
        completion = _fake()
        end, replier = _model_run(db, "c", 10, completion, cap=0.0005)
        assert end.day < 10 and replier.stopped.startswith("cap:")
        assert db.spent("c") <= 0.0005
        asked = len(completion.calls)
        end, replier = _model_run(db, "c", 10 - end.day, completion, cap=5.0, store=resume(db, "c"))
        assert end.day == 10 and replier.stopped is None
        assert len(completion.calls) > asked
        assert len(completion.calls) == len(db.replies("c"))         # every call kept once; none asked twice
        assert replay(db, "c") == []


def test_a_refusal_in_a_batch_keeps_the_whole_batch_and_stops_the_run(tmp_path):
    with RunDB(str(tmp_path / "f.sqlite")) as db:
        db.create_run("f", seed=1)
        straight_completion = _fake()
        with RunDB(str(tmp_path / "s.sqlite")) as other:
            other.create_run("f", seed=1)
            _model_run(other, "f", 3, straight_completion)
            day2 = [k for k, _ in other.replies("f") if k.split("|")[2] == "d2" and "|turn:|" in k]
        assert len(day2) >= 2
        completion = _fake()
        end, replier = _model_run(db, "f", 2, completion)
        assert end.day == 2
        completion.refuse_next = True                   # the first call on day 2 is refused
        end, replier = _model_run(db, "f", 1, completion, store=resume(db, "f"))
        assert end.day == 2 and replier.stopped.startswith("refused:")
        kept2 = [(k, r) for k, r in db.replies("f") if k.split("|")[2] == "d2"]
        assert [k for k, _ in kept2] == day2                    # every reply of the batch is kept
        assert sum(1 for _, r in kept2 if r.error and r.error.startswith("refused:")) == 1
        assert all(r.cost > 0 for _, r in kept2)
        assert replay(db, "f") == [k for k, _ in kept2]         # the stopped day is pending


def test_the_scripted_replier_is_unaffected_by_notes(tmp_path):
    with RunDB(str(tmp_path / "n.sqlite")) as db:
        db.create_run("n", seed=1)
        db.put_note("n", "families", "scripted")
        run(db, "n", gen.generate(1, fam.names("config/families.yaml")), ScriptedReplier(hamlet_script.script), 3)
        assert replay(db, "n") == [] and db.spent("n") == 0.0


# providers that overspend

MAXT = 300


class _Billing:
    """A provider that bills every attempt it is sent, answered or not, at the listed prices for
    the tokens it says it used: `out_mult` times `max_tokens` out, and the characters / 3 in.
    It raises `fail_first` times for each prompt before answering. `report` is the cost
    function the replier is given (by default the true bill)."""

    def __init__(self, out_mult=1.0, fail_first=0, report=None):
        self.out_mult, self.fail_first = out_mult, fail_first
        self.report = report or (lambda resp: resp._cost)
        self.billed = 0.0
        self.tries = {}
        self.lock = threading.Lock()

    def __call__(self, **kw):
        price = PRICES[kw["model"].split("/", 1)[1]]
        chars = sum(len(m["content"]) for m in kw["messages"])
        tin, tout = -(-chars // 3), int(MAXT * self.out_mult)
        cost = (tin * price.input_per_mtok + tout * price.output_per_mtok) / 1e6
        with self.lock:
            self.billed += cost
            n = self.tries[id(kw["messages"])] = self.tries.get(id(kw["messages"]), 0) + 1
        if n <= self.fail_first:
            raise TimeoutError("the client gave up; the provider still bills")
        message = SimpleNamespace(content='{"acts": []}', reasoning_content=None, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=tin, completion_tokens=tout), _cost=cost)


class _Watched(ModelReplier):
    """Records, for every batch sent, what the check allowed for it (`ATTEMPTS` x its worst
    case) and what the provider billed while it ran."""

    def __init__(self, provider, *args, **kw):
        super().__init__(*args, **kw)
        self.provider, self.batches = provider, []

    def ask_many(self, pairs):
        from townsim.town.modelreplier import ATTEMPTS
        before = self.provider.billed
        got = super().ask_many(pairs)
        if self.provider.billed > before:
            fams = [self.families[self.family_of[k.actor]] for k, _ in pairs]
            allowed = ATTEMPTS * sum(fam.worst_case(f, PRICES[f.model], m) for f, (_, m) in zip(fams, pairs))
            self.batches.append((self.provider.billed - before, allowed))
        return got


@pytest.mark.parametrize("name,provider", [
    ("ten times max_tokens out", lambda: _Billing(out_mult=10)),
    ("cost function reports 1e-9", lambda: _Billing(report=lambda resp: 1e-9)),
    ("raises twice before each answer", lambda: _Billing(fail_first=2)),
    ("all three", lambda: _Billing(out_mult=10, fail_first=2, report=lambda resp: 1e-9)),
])
def test_a_provider_that_overspends_is_held_to_the_cap(tmp_path, name, provider):
    """The bound: a batch is sent only when spend so far (stored costs plus lost attempts) plus
    `ATTEMPTS` x the batch's worst case is within the cap C, so after any batch
        stored + lost <= C + (what that batch billed - ATTEMPTS x its worst case),
    and the spend recorded never passes C by more than the largest such excess of one batch.
    The run stops at the cap."""
    cap = 0.05
    p = provider()
    lost = []
    with RunDB(str(tmp_path / "o.sqlite")) as db:
        db.create_run("o", seed=1)
        names = fam.names("config/families.yaml")
        s = gen.generate(1, names)
        families = {n: Family(n, ("cheap/a", "cheap/b")[i % 2], {"max_tokens": MAXT}) for i, n in enumerate(names)}
        r = _Watched(p, families, PRICES, {q.id: q.family for q in s.all("people")}, p, cap=cap,
                     spent=lambda: db.spent("o") + sum(lost), cost_fn=p.report, sleep_fn=lambda t: None,
                     lost=lost.append)
        end = run(db, "o", s, r, 120)
        recorded = db.spent("o") + sum(lost)
        assert end.day < 120 and r.stopped.startswith("cap:")
        if getattr(p, "out_mult", 1) > 1:                            # an overbilled reply stops it after its batch
            assert len(r.batches) == 1 and r.stopped.startswith("cap: a reply cost $")
        else:
            assert len(r.batches) >= 3                               # several batches were sent
        excess = max(0.0, max(billed - allowed for billed, allowed in r.batches))
        assert recorded <= cap + excess + 1e-12
        print(f"{name}: billed {p.billed / cap:.2f} x the cap, recorded {recorded / cap:.2f} x")


def test_a_provider_reporting_nothing_is_still_charged_and_stopped(tmp_path):
    """Usage 0/0 and a cost function saying 0: each reply is charged at least the listed price
    of what can be seen (the messages' characters / 3 plus 4 a message in, the reply's characters
    / 3 out), so stored spend grows and the run stops; it never passes C by more than one batch
    (ATTEMPTS x that batch's worst case, at most)."""
    cap = 0.01

    def completion(**kw):
        message = SimpleNamespace(content='{"acts": []}', reasoning_content=None, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))
    with RunDB(str(tmp_path / "z.sqlite")) as db:
        db.create_run("z", seed=1)
        names = fam.names("config/families.yaml")
        s = gen.generate(1, names)
        r = _Watched(_Billing(), _families(names), PRICES, {q.id: q.family for q in s.all("people")}, completion,
                     cap=cap, spent=lambda: db.spent("z"), cost_fn=lambda resp: 0.0, sleep_fn=lambda t: None)
        end = run(db, "z", s, r, 400)
        assert end.day < 400 and r.stopped.startswith("cap:")
        assert all(rep.cost > 0 and (rep.tokens_in, rep.tokens_out) == (0, 0) for _, rep in db.replies("z"))
        assert 0 < db.spent("z") <= cap
