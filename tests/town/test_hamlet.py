"""The generated hamlet lives: a day's routine with events, deliveries, collections and
shopping; people on scripted replies; the week's close; eight weeks that conserve money
and replay exactly."""

import functools
import os
import pickle
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from townsim.town import acts, firms, gen, hamlet, hamlet_script, hours, jobs
from townsim.town import obligations as O
from townsim.town.entities import Event
from townsim.town.loop import routine, run_day
from townsim.town.replies import Keys, ScriptedReplier, StopRun
from townsim.town.rundb import RunDB
from townsim.town.runs import replay, resume, run
from townsim.town.views import view

from .helpers import make_rent, run_days, town
from .test_random_play import _check_run

FAMILIES = ["a", "b", "c", "d"]
DAYS = 56
ORDINARY_WORDS = 1500       # the most a view may be on an ordinary day of the scripted weeks
T = "routine:test"


@functools.cache
def _frozen(seed: int) -> bytes:
    return pickle.dumps(gen.generate(seed, FAMILIES))


def place(seed: int = 1):
    """A fresh copy of the generated place for `seed`."""
    return pickle.loads(_frozen(seed))


def quiet(key, messages):
    return '{"acts": []}'


def days(s, n, script=quiet):
    keys = Keys("q")
    return [run_day(s, ScriptedReplier(script), keys) for _ in range(n)]


def words(messages):
    return sum(len(m["content"].split()) for m in messages)


@functools.cache
def _eight_weeks_run():
    """Seed 1 played for eight weeks on `hamlet_script.script` as a kept run (`h1`, in a
    database of its own), with every day's report, the size of every person's view on every
    morning (after the routine) and the time the days took. Played once for this module."""
    import tempfile
    import townsim.town.runs as runs_module
    s = place(1)
    sizes: dict[tuple[int, str], int] = {}
    reports = []

    def script(key, messages):
        if not any(d == s.day for d, _ in sizes):
            for p in s.all("people"):
                sizes[(s.day, p.id)] = words(view(s, p.id)[0])
        return hamlet_script.script(key, messages)

    def kept(store, replier, keys):
        report = run_day(store, replier, keys)
        reports.append(report)
        return report

    db = RunDB(str(Path(tempfile.mkdtemp()) / "h.sqlite"))
    db.create_run("h1", seed=1)
    started = time.perf_counter()
    runs_module.run_day, before = kept, runs_module.run_day
    try:
        final = run(db, "h1", s, ScriptedReplier(script), DAYS)
    finally:
        runs_module.run_day = before
    return final, reports, sizes, time.perf_counter() - started, db


def eight_weeks():
    return _eight_weeks_run()[:4]


@pytest.fixture(scope="module", autouse=True)
def _let_go():
    """The eight weeks are kept for this module's tests only, so later timings do not carry them."""
    yield
    if _eight_weeks_run.cache_info().currsize:
        _eight_weeks_run()[4].close()
    _eight_weeks_run.cache_clear()
    _frozen.cache_clear()


def done_ops(s):
    return Counter(c.cause.split(":")[2] for c in s.changes
                   if c.cause.startswith("op:") and c.table not in ("results", "counter"))


# eight weeks

def test_eight_weeks_in_the_hamlet_conserve_and_replay():
    final, _, _, took, db = _eight_weeks_run()
    assert final.ledger.total() == 0 and db.errors("h1") == [] and replay(db, "h1") == []
    assert took < 15 and final.day == DAYS


def test_nothing_is_collected_twice_and_no_view_is_over_length():
    s, _, sizes, _ = eight_weeks()
    assert s.engine_errors == []
    _check_run(s)                                    # one collect a due; by hand plus collected is its sum
    assert len(sizes) == DAYS * 18
    assert max(sizes.values()) <= ORDINARY_WORDS, max(sizes.items(), key=lambda kv: kv[1])
    for d in s.all("dues"):
        assert 0 <= d.paid <= d.cents


def _largest_views(seed):
    """The largest of everyone's views on each morning of eight scripted weeks of `seed`."""
    s = place(seed)
    sizes = {}

    def script(key, messages):
        if s.day not in {d for d, _ in sizes}:
            for p in s.all("people"):
                sizes[(s.day, p.id)] = words(view(s, p.id)[0])
        return hamlet_script.script(key, messages)
    keys = Keys("v")
    for _ in range(DAYS):
        run_day(s, ScriptedReplier(script), keys)
    return max(sizes.items(), key=lambda kv: kv[1])


@pytest.mark.parametrize("seed", [2, 3])
def test_no_ordinary_view_is_over_1500_words_on_other_seeds(seed):
    where, most = _largest_views(seed)
    assert most <= ORDINARY_WORDS, (where, most)


def test_the_script_lives_a_full_life():
    s, reports, _, _ = eight_weeks()
    results = s.all("results")
    ok = [(r.op, r.text) for r in results if r.ok]
    done = done_ops(s)
    for op in ("pay", "standing_payment", "stop", "message", "request", "answer", "read", "write_notes", "aims"):
        assert done[op] > 0, op
    # a payment by hand toward something overdue, naming it with `for`
    hand = [c for c in s.changes if c.table == "dues" and c.field == "paid" and c.cause.startswith("op:")
            and any(old.cause.startswith("routine:collect:") and old.id == c.id and old.field == "status"
                    and old.new == "arrears" for old in s.changes)]
    assert hand
    acted = [o for r in reports for t in r.turns for o in t.outcomes]
    assert any(o.ok and o.act.op == "pay" and "for" in o.act.args for o in acted)
    # a standing payment to someone known, set up and later stopped
    standing = [o for o in s.all("obligations") if o.kind == "standing"]
    assert any(o.status == "stopped" for o in standing)
    assert all(s.has("households", o.payee) for o in standing)
    # letters by first name, by full name, to a shop, and to no one
    letters = [o for o in acted if o.act.op == "message"]
    sent = [o.act.args["to"] for o in letters if o.ok]
    assert any(isinstance(t, str) and " " not in t for t in sent)                    # a first name alone
    assert any(acts.resolve(s, t) and s.has("people", acts.resolve(s, t)) for t in sent if isinstance(t, str))
    assert any(s.get("orgs", m.org).kind == "shop" for m in s.all("messages") if m.org)
    assert any(o.act.args.get("to") == "Edith Marsh" and o.msg.endswith("came back undelivered.") for o in letters)
    # requests with and without money, one naming what it is for, answered both ways
    rqs = s.all("requests")
    assert any(r.cents > 0 for r in rqs) and any(r.cents == 0 for r in rqs) and any(r.ob for r in rqs)
    assert {"accepted", "declined"} <= {r.status for r in rqs}
    assert any(text.startswith("Balance:") and "In and out since" in text for op, text in ok if op == "read")
    assert any(o.ok and o.act.op == "read" and o.act.args.get("what") == "account in full" for o in acted)
    assert any(p.notes for p in s.all("people"))
    # an owner pays from the business
    owned = {o.id for o in s.all("orgs") if o.owner}
    assert any(t.src in owned and t.cause.startswith("op:") for t in s.ledger.transfers)
    # aims that persist and change
    aims_changes = [c for c in s.changes if c.table == "aims"]
    assert any(a.since < s.day - 28 for a in s.all("aims") if a.text)                # kept for weeks
    assert any(c.field == "text" for c in aims_changes)                              # reworded or replaced
    # a reply that is not JSON, one with junk, one with twelve operations
    turns = [t for r in reports for t in r.turns]
    assert any(t.invalid for t in turns)
    assert any(o.msg == "Not done: there is no operation called 'None'." or o.act.args.get("to") == 7
               for o in acted if isinstance(o.act.args, dict))
    assert any(r.op == "(no operation named)" for r in results)
    assert any(r.op == "(more)" and r.text == "Not done: 4 more operations were written than the eight carried out in a day."
               for r in results)
    # the levers of work: each done and each refused
    work = ("offer_job", "offer_shifts", "dismiss", "set_staff_pay", "set_price", "order_stock", "work_extra_hours",
            "move_money", "quit")
    for op in work:
        assert any(o.ok and o.act.op == op for o in acted), op
        assert any(not o.ok and o.act.op == op and o.msg.startswith("Not done:") for o in acted), op
    offers = [r for r in rqs if r.kind == "job"]
    assert {"accepted", "declined"} <= {r.status for r in offers}
    assert any(r.kind == "shifts" and r.status == "accepted" for r in rqs)
    assert any(o.memo.startswith("extra hours at") and o.status == "finished" for o in s.all("obligations"))
    assert any(j.ended != -1 and s.has("orgs", j.org) and not s.get("orgs", j.org).outside for j in s.all("jobs"))
    assert any(o.memo == "jobseeker's benefit" and o.ends == "both" for o in s.all("obligations"))   # paid on its own
    assert any(c.table == "firms" and c.field == "price" and c.cause.startswith("op:") for c in s.changes)
    ordered = [o.act.args.get("baskets") for o in acted if o.ok and o.act.op == "order_stock"]
    assert 0 in ordered and any(u for u in ordered)
    moved = [o.act.args.get("from") for o in acted if o.ok and o.act.op == "move_money"]
    assert "household" in moved and any(m != "household" for m in moved)
    assert any(o.ok and o.act.op == "pay" and "counted toward wages from" in o.msg and "from" not in o.act.args
               for o in acted)                                                    # a wage paid by hand
    # agreements, loans and rent: each done and each refused (no tenant falls two weeks behind
    # in these eight weeks, so notice to quit is only refused here; its own tests serve it)
    for op in ("propose_agreement", "propose_end", "borrow", "repay", "set_rent", "serve_notice"):
        assert op == "serve_notice" or any(o.ok and o.act.op == op for o in acted), op
        assert any(not o.ok and o.act.op == op and o.msg.startswith("Not done:") for o in acted), op
    for kind in ("agreement", "loan"):
        assert {"accepted", "declined"} <= {r.status for r in rqs if r.kind == kind}, kind
    ended = [s.get("obligations", r.ob) for r in rqs if r.kind == "end" and r.status == "accepted"]
    assert ended and all(o.kind == "agreement" and o.status == "finished" for o in ended)
    assert not any(r.kind == "breach" and any(o.memo in r.text for o in ended) for r in s.all("records"))
    assert any(c.table == "obligations" and c.field == "last" and ":repay:" in c.cause for c in s.changes)
    changed = {c.id: c.day for c in s.changes if c.table == "obligations" and c.field == "next_from"
               and ":set_rent:" in c.cause}                     # a let's one rent, re-priced
    assert changed and all(s.get("obligations", o).kind == "rent" for o in changed)
    assert any(d.status == "paid" and d.day > day + 27 for o, day in changed.items()
               for d in s.where("dues", obligation=o))


def test_shops_have_customers_and_tills_every_week():
    s, _, _, _ = eight_weeks()
    for f in s.all("firms"):
        tills = [t for t in s.all("tills") if t.firm == f.id]
        assert [t.week for t in tills] == list(range(1, 9))
        assert sum(1 for t in tills if t.sold > 0) >= 6, f.id
        assert all(t.cash == t.cash for t in tills)


def test_a_run_stopped_mid_day_and_resumed_reaches_the_same_end(tmp_path):
    """From the kept eight weeks' snapshot of day 30: a run stopped in the middle of that day
    and resumed reaches the same end, and replays."""
    def stopping(key, messages):
        if key.day == 30 and key.actor == "p3":
            raise StopRun
        return hamlet_script.script(key, messages)

    kept = _eight_weeks_run()[4]
    with RunDB(str(tmp_path / "h.sqlite")) as db:
        db.create_run("h", seed=1)
        stopped = run(db, "h", kept.load_snapshot("h1", 30), ScriptedReplier(stopping), DAYS - 30)
        assert stopped.day == 30 and db.snapshot_days("h") == [30]
        final = run(db, "h", resume(db, "h"), ScriptedReplier(hamlet_script.script), DAYS - 30)
        assert replay(db, "h") == []
    assert final.hash() == eight_weeks()[0].hash()


def test_eight_weeks_with_talking_conserve_and_replay():
    """The script talks: conversations asked for and held, one not held for want of time, one
    ended early and one run to three turns a side; a payment and an agreement made inside a
    talk and the agreement accepted there by the other; appointments booked and held, missed
    and cancelled; the household's Sunday talk; and a reply in a talk that cannot be read. The
    run conserves money and replays (see the test above)."""
    from townsim.town import talk
    s, reports, _, _ = eight_weeks()
    talks = s.all("talks")
    asked = [t for t in talks if t.kind == "asked"]
    assert len([t for t in asked if t.status == "held"]) >= 5
    assert any(t.status == "not held" for t in asked)
    assert any(n.text.endswith(" had no time to talk today.") for n in s.all("notices"))
    held = [t for t in talks if t.status == "held"]
    sizes = {t.id: len(talk.lines_of(s, t.id)) for t in held}
    assert 6 in sizes.values() and any(n < 6 for t, n in sizes.items() if s.get("talks", t).kind == "asked")
    in_talks = [o for r in reports for t in r.turns if t.talk for o in t.outcomes]
    assert any(o.ok and o.act.op == "pay" for o in in_talks)
    proposed = {o.msg.split("(")[1].split(")")[0] for o in in_talks if o.ok and o.act.op == "propose_agreement"}
    accepted = {o.act.args["request"] for o in in_talks if o.ok and o.act.op == "answer"}
    assert proposed & accepted
    assert any(s.get("requests", rq).status == "accepted" for rq in proposed & accepted)
    statuses = {a.status for a in s.all("appointments")}
    assert {"held", "missed", "cancelled"} <= statuses
    assert any(t.kind == "appointment" for t in held) and any(t.kind == "household" for t in held)
    assert any(t.invalid for r in reports for t in r.turns if t.talk)
    for r in reports:                                         # two a day at most, and each reported
        assert [h.id for h in r.talks] == [t.id for t in held if t.day == r.day]
    assert all(p.hours_left >= 0 for p in s.all("people"))


def test_a_talk_view_is_under_900_words_for_every_pair_after_eight_weeks():
    from townsim.town import talk
    from townsim.town.acts import Act, apply_act
    for s in (place(1), pickle.loads(pickle.dumps(eight_weeks()[0]))):
        people = [p.id for p in s.all("people")]
        for a in people:
            for b in people:
                if a == b:
                    continue
                for t in s.where("talks", day=s.day):
                    s.set("talks", t.id, T, day=-1)
                s.set("people", a, T, hours_left=60.0)
                out = apply_act(s, Act(a, a, "converse", {"with": s.get("people", b).name, "opening": "Hello."}), 0)
                assert out.ok, out.msg
                tk = s.all("talks")[-1].id
                for pid in (a, b):
                    assert words(talk.view(s, pid, tk)) <= 900, (s.day, pid, tk)


def test_a_run_stopped_mid_talk_resumes_to_the_same_state(tmp_path):
    """From the kept eight weeks: a run stopped at the second speaker's second turn of a talk
    goes back to that morning, and resumed reaches the same end, and replays."""
    from townsim.town import talk
    final, _, _, _ = eight_weeks()
    tk = next(t for t in final.all("talks") if t.status == "held" and len(talk.lines_of(final, t.id)) == 6)
    second = talk.lines_of(final, tk.id)[1].speaker
    stopped_at = []

    def stopping(key, messages):
        if (key.purpose, key.ref, key.actor, key.n) == ("talk", tk.id, second, 1) and not stopped_at:
            stopped_at.append(key)
            raise StopRun
        return hamlet_script.script(key, messages)

    kept = _eight_weeks_run()[4]
    with RunDB(str(tmp_path / "t.sqlite")) as db:
        db.create_run("h", seed=1)
        back = run(db, "h", kept.load_snapshot("h1", tk.day), ScriptedReplier(stopping), DAYS - tk.day)
        assert stopped_at and back.day == tk.day and back.hash() == kept.snapshot_hash("h1", tk.day)
        assert any(f"|talk:{tk.id}|" in k for k in db.reply_keys("h"))
        pending = replay(db, "h")
        assert pending and any(f"|talk:{tk.id}|" in k for k in pending)
        end = run(db, "h", resume(db, "h"), ScriptedReplier(stopping), DAYS - tk.day)
        assert replay(db, "h") == []
    assert end.hash() == final.hash()


_HASH = """
from townsim.town import gen, hamlet_script
from townsim.town.loop import run_day
from townsim.town.replies import Keys, ScriptedReplier
s = gen.generate(1, ["a", "b", "c", "d"]); k = Keys("q")
for _ in range(56):
    run_day(s, ScriptedReplier(hamlet_script.script), k)
print(s.hash())
"""


def test_two_processes_with_different_hash_seeds_reach_the_same_end():
    root = Path(__file__).resolve().parents[2]
    procs = [subprocess.Popen([sys.executable, "-c", _HASH], cwd=root, stdout=subprocess.PIPE, text=True,
                              env={**os.environ, "PYTHONHASHSEED": seed}) for seed in ("1", "2")]
    outs = [p.communicate(timeout=60)[0].strip() for p in procs]
    assert outs[0] == outs[1] == eight_weeks()[0].hash()


# the morning's order, and the close

def test_the_thin_town_has_no_events_deliveries_shopping_or_tills():
    from townsim.town.thin import thin_script, thin_town
    s = thin_town()
    days(s, 14, thin_script)
    assert not [c for c in s.changes if c.cause.startswith(("routine:event", "routine:delivery", "routine:shopping",
                                                             "routine:close_week"))]


# events

def at(day, seed=1):
    s = place(seed)
    while s.day < day:
        s.advance_day()
    return s


def event(s, kind, who, amount=0, weeks=1, eid="ev99"):
    s.add("events", Event(eid, s.day, kind, who, amount, weeks), T)
    mark = s.mark()
    hamlet.apply_events(s)
    changed = [c for c in s.changes[mark:]]
    assert all(c.cause == f"routine:event:{eid}" for c in changed)
    assert s.get("events", eid).done
    return changed


def touched(changes, table):
    return {c.id for c in changes if c.table == table and c.field != "+"} | \
        {c.id for c in changes if c.table == table and c.field == "+"}


def notices(s, changes):
    return [(s.get("notices", c.id).to, s.get("notices", c.id).text) for c in changes
            if c.table == "notices" and c.field == "+"]


def test_an_illness_on_a_monday_leaves_no_hours_that_week():
    s = at(14)
    changes = event(s, "illness", "p2")
    assert notices(s, changes) == [("p2", "You are ill this week.")]
    assert touched(changes, "people") == {"p2"} and s.get("people", "p2").ill_until == 20
    assert {c.table for c in changes} <= {"people", "notices", "counter", "events"}
    hours.monday_hours(s)
    p = s.get("people", "p2")
    assert p.hours_week == 0 and p.hours_left == 0
    for _ in range(7):
        s.advance_day()
    hours.monday_hours(s)
    assert s.get("people", "p2").hours_left == hours.free_hours(s, "p2") > 0


def test_an_illness_mid_week_takes_the_hours_left_at_once():
    s = at(17)
    week_hours = s.get("people", "p2").hours_week
    changes = event(s, "illness", "p2")
    p = s.get("people", "p2")
    assert (p.ill_until, p.hours_left, p.hours_week) == (20, 0, week_hours)
    assert notices(s, changes) == [("p2", "You are ill this week.")]


def test_a_job_lost_ends_after_this_fridays_pay():
    s = place(1)
    [ev] = [e for e in s.all("events") if e.kind == "job_lost"]
    assert (ev.day, ev.who) == (16, "p4")
    job = jobs.held(s, person="p4")[0]
    before = hours.free_hours(s, "p4")
    days(s, 16)
    mark = s.mark()
    days(s, 1)
    told = [n for n in s.all("notices") if n.to == "p4" and n.day == 16]
    assert [n.text for n in told] == ["Brackenfield Foods has ended your job. Your last pay is on Friday 20 March."]
    assert {c.id for c in s.changes[mark:] if c.cause == f"routine:event:{ev.id}" and c.table == "notices"} == \
        {n.id for n in told}
    assert s.get("jobs", job.id).ended == 18 and s.get("obligations", job.wage_ob).last == 18
    days(s, 1)
    assert s.day == 18 and "employed" in acts.positions(s, "p4")        # Friday 20 March, the last day
    days(s, 1)
    assert "employed" not in acts.positions(s, "p4")                    # Saturday
    days(s, 3)                                                          # through Monday 23 March
    routine_hours = s.get("people", "p4").hours_week
    assert routine_hours == hours.free_hours(s, "p4") > before
    days(s, 14)
    wages = [t for t in s.ledger.transfers if t.src == job.org and t.dst == "h2"]
    assert [t.t for t in wages] == [4, 11, 18]
    assert s.get("obligations", job.wage_ob).status == "finished"


def test_a_repair_is_a_bill_due_in_a_week():
    s = at(14)
    changes = event(s, "repair", "h6", amount=55_500)
    [ob] = [s.get("obligations", c.id) for c in changes if c.table == "obligations" and c.field == "+"]
    market = [o.payee for o in s.where("obligations", payer="h6") if o.memo == "regular spending"][0]
    assert (ob.kind, ob.payer, ob.payee, ob.cents, ob.first, ob.every, ob.memo, ob.ends, ob.cannot_stop) == (
        "bill", "h6", market, 55_500, 21, 0, "repair to the house", "both", "work")
    assert notices(s, changes) == [("p10", "The house needs a repair costing $555.00. It is due on Monday 23 March.")]


def test_a_supplier_short_halves_that_weeks_delivery_only():
    with_short, without = place(1), place(1)
    [ev] = [e for e in with_short.all("events") if e.kind == "supplier_short"]
    assert (ev.day, ev.who) == (23, "sp2")
    without.set("events", ev.id, T, done=True)
    days(with_short, 42); days(without, 42)
    assert with_short.get("suppliers", "sp2").short_week == 5
    told = [n for n in with_short.all("notices") if n.day == 23]
    shops = [f for f in with_short.all("firms") if f.last_supplier == "sp2"]
    assert shops
    owners = [with_short.get("orgs", f.org).owner for f in shops]
    assert [(n.to, n.text) for n in told] == [
        (o, "Coldbeck Cash and Carry has said the delivery on Monday 30 March will be short.") for o in owners]

    def by_week(s, firm):
        return {t.t // 7 + 1: int(t.memo.split()[0]) for t in s.ledger.transfers
                if t.cause == f"routine:delivery:{firm}"}
    for f in with_short.all("firms"):
        a, b = by_week(with_short, f.id), by_week(without, f.id)
        for wk in b:
            assert a[wk] == (b[wk] // 2 if wk == 5 and f.last_supplier == "sp2" else b[wk]), (f.id, wk)


def test_a_cold_month_adds_four_weeks_of_energy_bills():
    s = at(28)
    [ev] = [e for e in s.all("events") if e.kind == "cold_month"]
    assert ev.day == 28
    mark = s.mark()
    hamlet.apply_events(s)
    changes = s.changes[mark:]
    extra = [s.get("obligations", c.id) for c in changes if c.table == "obligations" and c.field == "+"]
    energy = [o for o in s.where("obligations", payee=ev.who) if o.memo == "energy"]
    assert len(extra) == 4 * len(energy) == 4 * len(s.all("households"))
    for o in energy:
        mine = [x for x in extra if x.payer == o.payer]
        assert [x.first for x in mine] == [30, 37, 44, 51]
        assert all((x.kind, x.payee, x.cents, x.every, x.memo) ==
                   ("bill", o.payee, round(o.cents * 0.3), 0, "extra energy, cold spell") for x in mine)
    told = notices(s, changes)
    assert sorted(told) == sorted((p, "A cold spell: energy bills are 30% higher for four weeks.")
                                  for h in s.all("households") for p in h.members)


def test_an_event_naming_no_one_is_an_engine_error():
    s = at(14)
    changes = event(s, "illness", "p99")
    assert [c.table for c in changes] == ["events"]
    assert s.engine_errors == ["event ev99 d14: illness no person 'p99'; not applied"]


def test_events_are_applied_once_on_their_day():
    s = place(1)
    days(s, DAYS)
    applied = Counter(c.cause for c in s.changes if c.cause.startswith("routine:event:") and c.table == "events")
    assert sorted(applied) == [f"routine:event:{e.id}" for e in s.all("events") if e.day < DAYS]
    assert all(e.done == (e.day < DAYS) for e in s.all("events"))
    assert all(c.day == s.get("events", c.cause.split(":")[2]).day for c in s.changes
               if c.cause.startswith("routine:event:"))


# arrears taken

def test_arrears_taken_are_told():
    s = town(h1=0)
    make_rent(s, 10_000, 0)
    run_days(s, 1)
    s.transfer("outside:employers", "h1", 20_000, "wages", T)
    run_days(s, 1)
    cause = [t.cause for t in s.ledger.transfers if t.dst == "outside:lettings"][0]
    told = [(n.to, n.text) for n in s.all("notices") if any(c.cause == cause and c.id == n.id for c in s.changes)
            and "taken on" in n.text]
    assert told == [(p, "A payment of $100.00 due Monday 2 March (rent) was taken on Tuesday 3 March.")
                    for p in ("p1", "p2")]


def test_a_due_taken_on_its_day_is_not_told():
    s = town(h1=50_000)
    make_rent(s, 10_000, 0)
    run_days(s, 1)
    assert not s.all("notices")


# the loop

def test_aims_are_taken_before_acts_and_too_many_acts_are_told():
    s = place(1)
    reply = ('{"thoughts": "", "aims": ["Keep the shop going"], "acts": ['
             + ", ".join(['{"op": "write_notes", "text": "x"}'] * 10) + ', {"to": "x"}]}')

    def script(key, messages):
        return reply if key.actor == "p16" else '{"acts": []}'
    days(s, 3, script)                      # Martin Lumb's stock day is Wednesday
    [aim] = [a for a in s.all("aims") if a.person == "p16"]
    assert (aim.text, aim.since) == ("Keep the shop going", 2)
    causes = [c.cause for c in s.changes if c.cause.startswith("op:p16:") and c.day == 2]
    assert causes[0] == "op:p16:aims:d2.0" and "op:p16:write_notes:d2.1" in causes
    more = [r for r in s.all("results") if r.actor == "p16" and r.op == "(more)"]
    assert [(r.text, r.ok) for r in more] == [
        ("Not done: 3 more operations were written than the eight carried out in a day.", False)]


def test_a_reply_without_acts_applies_no_aims():
    s = place(1)
    days(s, 3, lambda key, m: '{"aims": ["one"]}')
    assert not [a for a in s.all("aims") if a.text]


# ending what is owed, and jobs

def test_end_stops_instalments_after_the_last_day():
    s = town(h1=100_000)
    ob = make_rent(s, 10_000, 0)
    run_days(s, 3)
    O.end(s, ob, 7, T)
    assert s.get("obligations", ob).last == 7
    run_days(s, 20)
    assert [d.day for d in s.all("dues")] == [0, 7] and s.get("obligations", ob).status == "finished"


def test_end_leaves_once_only_and_ended_obligations_and_refuses_bad_calls():
    s = town(h1=100_000)
    once = O.create(s, kind="bill", payer="h1", payee="outside:lettings", cents=500, first=3, memo="x",
                    ends="service", made_by="routine", cause=T)
    ob = make_rent(s, 10_000, 0)
    O.end(s, ob, 14, T)
    mark = s.mark()
    O.end(s, once, 5, T); O.end(s, ob, 21, T)
    assert s.mark() == mark
    for bad, day in (("ob99", 5), (ob, -1)):
        with pytest.raises(ValueError):
            O.end(s, bad, day, T)
    assert s.mark() == mark


def test_end_before_the_first_instalment_finishes_it():
    s = town(h1=100_000)
    ob = make_rent(s, 10_000, 10)
    O.end(s, ob, 5, T)
    run_days(s, 20)
    assert s.all("dues") == [] and s.get("obligations", ob).status == "finished"


def test_a_job_started_and_finished():
    s = place(1)
    hh = s.get("people", "p18").household
    jid = jobs.start(s, "p18", "org13", 30_000, 20, 4, T)
    job = s.get("jobs", jid)
    ob = s.get("obligations", job.wage_ob)
    assert (job.person, job.org, job.weekly, job.hours, job.since, job.ended) == ("p18", "org13", 30_000, 20.0, 0, -1)
    assert (ob.kind, ob.payer, ob.payee, ob.first, ob.every, ob.memo, ob.ends) == (
        "wage", "org13", hh, 4, 7, "wages from Ridgeway Software", "both")
    assert "employed" in acts.positions(s, "p18") and hours.job_hours(s, "p18") == 20
    assert firms.staff_hours(s, "org13") == sum(j.hours for j in s.where("jobs", org="org13"))
    jobs.finish(s, jid, 4, T)
    assert s.get("jobs", jid).ended == 4 and s.get("obligations", job.wage_ob).last == 4
    for bad in (jid, "jb99"):
        with pytest.raises(ValueError):
            jobs.finish(s, bad, 11, T)
    for _ in range(5):
        s.advance_day()
    assert "employed" not in acts.positions(s, "p18") and hours.job_hours(s, "p18") == 0
    assert jid not in [j.id for j in jobs.held(s, org="org13")]
    with pytest.raises(ValueError):
        jobs.start(s, "p99", "org13", 100, 1, 11, T)


# random play's sibling: see tests/town/test_random_play.py


def test_a_late_fee_is_told_by_its_memo_alone():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, every=0, memo="energy",
             ends="service", made_by="routine", cause=T)
    run_days(s, 8)                                           # the fee is added on day 7 and cannot be taken
    s.transfer("outside:employers", "h1", 20_000, "wages", T)
    run_days(s, 1)
    texts = [n.text for n in s.all("notices") if n.to == "p1"]
    assert ("A late fee of $25.00 on a payment due Monday 2 March (energy) could not be taken. "
            "The Ashby account held $0.00.") in texts
    assert texts[-2:] == ["A payment of $48.00 due Monday 2 March (energy) was taken on Tuesday 10 March.",
                          "A late fee of $25.00 on a payment due Monday 2 March (energy) was taken on Tuesday 10 March."]


def test_a_repair_is_billed_after_regular_spending_has_stopped():
    s = at(14)
    [ob] = [o for o in s.where("obligations", payer="h6") if o.memo == "regular spending"]
    s.set("obligations", ob.id, T, status="stopped")          # as though it could be stopped
    changes = event(s, "repair", "h6", amount=10_000)
    [bill] = [s.get("obligations", c.id) for c in changes if c.table == "obligations" and c.field == "+"]
    assert (bill.payee, bill.cents, bill.memo, bill.first) == (ob.payee, 10_000, "repair to the house", 21)
    assert s.engine_errors == []


def test_a_repair_to_a_house_that_never_bought_anything_is_passed_over():
    s = at(14)
    for o in s.where("obligations", payer="h6"):
        if o.memo == "regular spending":
            s.set("obligations", o.id, T, memo="other spending")
    changes = event(s, "repair", "h6", amount=10_000)
    assert [c.table for c in changes] == ["events"] and s.engine_errors == []


def test_a_cold_spell_near_an_energy_bills_end_tells_the_weeks_it_has():
    s = at(28)
    [ev] = [e for e in s.all("events") if e.kind == "cold_month"]
    energy = [o for o in s.where("obligations", payee=ev.who) if o.memo == "energy"]
    O.end(s, energy[0].id, 37, T)                            # two more energy days, 30 and 37
    mark = s.mark()
    hamlet.apply_events(s)
    told = {(n.to, n.text) for c in s.changes[mark:] if c.table == "notices" and c.field == "+"
            for n in [s.get("notices", c.id)]}
    first = s.get("households", energy[0].payer).members
    assert {t for p, t in told if p in first} == {"A cold spell: energy bills are 30% higher for two weeks."}
    assert {t for p, t in told if p not in first} == {"A cold spell: energy bills are 30% higher for four weeks."}



# what a household cannot stop

def _stop(s, pid, ob_id, n=0):
    return acts.apply_act(s, acts.Act(pid, pid, "stop", {"id": ob_id}), n)


def test_a_household_cannot_stop_what_it_costs_to_live_or_a_bill_for_work_done():
    s = place()
    routine(s)
    spending, energy = (next(o.id for o in s.where("obligations", payer="h2") if o.memo == m)
                        for m in ("regular spending", "energy"))
    before = s.hash()
    assert _stop(s, "p3", spending).msg == "Not done: that is what the household spends to live; it cannot be stopped."
    assert _stop(s, "p3", energy).msg == "Not done: the house's energy cannot be stopped while you live there."
    event(s, "repair", "h2", amount=10_000)
    bill = s.all("obligations")[-1]
    assert bill.memo == "repair to the house" and bill.every == 0
    before = s.hash()
    assert _stop(s, "p3", bill.id).msg == "Not done: that is a bill for work already done."
    assert s.hash() == before
    event(s, "cold_month", next(o.id for o in s.all("orgs") if o.kind == "utility"), amount=20, weeks=2, eid="ev98")
    extra = next(o for o in s.where("obligations", payer="h2") if o.memo == "extra energy, cold spell")
    assert _stop(s, "p3", extra.id).msg == "Not done: the house's energy cannot be stopped while you live there."
    for ob in (spending, energy, bill.id):
        with pytest.raises(ValueError):
            O.stop(s, ob, "h2", T)


def test_stopping_rent_is_withholding_it_and_says_so():
    s = place()
    days(s, 1)                                                   # stopped on Tuesday 3 March
    rent = next(o for o in s.where("obligations", payer="h1") if o.kind == "rent")          # to the agency
    out = _stop(s, "p1", rent.id)
    agency = O.name_of(s, rent.payee)
    assert out.msg == (f"That was your rent to {agency}. Stopping it is recorded as a breach. The rent stays owed "
                       f"each week and is no longer taken from your account.")
    assert s.all("records")[-1].text == f"Brindle household stopped paying the rent to {agency} (rent for 8 Back Lane)."
    days(s, 7)
    told = [n.text for n in s.where("notices", to="p1") if "was not taken" in n.text]
    assert told == [f"Rent for 8 Back Lane, $258.00, due Monday 9 March, was not taken: you stopped it on "
                    f"Tuesday 3 March. It is owed."]
    assert not any("agreement" in n.text for n in s.where("notices", to="p1"))


def test_an_outside_landlord_serves_notice_when_the_rent_defaults():
    s = place()
    days(s, 1)                                                   # stopped on Tuesday 3 March
    rent = next(o for o in s.where("obligations", payer="h1") if o.kind == "rent")
    agency = O.name_of(s, rent.payee)
    _stop(s, "p1", rent.id)
    days(s, 35)                                                  # the day-7 rent defaults on day 35
    recs = [r for r in s.all("records") if r.kind == "notice to quit"]
    assert [(r.day, r.about, r.text) for r in recs] == \
        [(35, "h1", f"{agency} has served notice on the Brindle household to leave 8 Back Lane by Monday 4 May.")]
    for pid in ("p1", "p2"):
        assert f"{agency} has served notice: you are to leave 8 Back Lane by Monday 4 May." in \
            [n.text for n in s.where("notices", to=pid)]
    assert any(c.cause == f"routine:notice:{rent.id}" for c in s.changes)
    days(s, 14)                                                  # more defaults, but notice stands: once
    assert len([r for r in s.all("records") if r.kind == "notice to quit"]) == 1
