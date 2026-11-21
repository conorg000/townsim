import importlib

import pytest

from townsim.town import acts, ops_core
from townsim.town.acts import (REGISTRY, Act, OpSpec, account_of, apply_act, clean_text, cut, forms, register, resolve,
                               to_cents)

from town.helpers import thin


@pytest.mark.parametrize("args", [None, [], "pay", {"to": "Megan Pryce"}, {"to": "Megan Pryce", "amount": "lots"},
    {"to": "Megan Pryce", "amount": -5}, {"to": "Megan Pryce", "amount": True}, {"to": ["x"], "amount": 5},
    {"to": "Sanjay", "amount": 5}, {"to": "Megan Pryce", "amount": 10**9}])
def test_junk_is_refused_and_changes_nothing(args):
    s = thin(); before = s.hash()
    out = apply_act(s, Act("p01", "p01", "pay", args), 0)
    assert not out.ok and out.msg.startswith("Not done:") and s.hash() == before and s.engine_errors == []


def test_cut_shortens_only_what_is_too_long():
    assert cut("a" * 80) == "a" * 80 and cut("a" * 81) == "a" * 80 + "\u2026" and cut("abcdef", 3) == "abc\u2026"


def test_unknown_operation_and_acting_for_another_are_refused():
    s = thin(); before = s.hash()
    out = apply_act(s, Act("p01", "p01", "cancel_everything", {}), 0)
    assert out.msg == "Not done: there is no operation called 'cancel_everything'."
    out = apply_act(s, Act("p01", "p03", "pay", {"to": "Ruth Ashby", "amount": 5}), 1)
    assert not out.ok and out.msg == "Not done: you have no connection that allows you to act for them."
    assert s.hash() == before


def test_args_that_are_not_an_object_are_refused_first():
    out = apply_act(thin(), Act("p01", "p01", "no_such_op", ["to", "x"]), 0)
    assert out.msg == "Not done: that operation was not written in a form that can be read."


@pytest.mark.parametrize("op", [None, 7, ["pay"], {"op": "pay"}, "x" * 500])
def test_an_operation_name_that_is_not_a_name_is_refused(op):
    s = thin(); before = s.hash()
    out = apply_act(s, Act("p01", "p01", op, {}), 0)
    assert out.msg.startswith("Not done: there is no operation called '") and len(out.msg) < 100
    assert s.hash() == before and s.engine_errors == []


def test_an_operation_that_breaks_is_undone_and_reported():
    s = thin(); before = s.hash()
    def boom(store, act, cause):
        store.set("people", act.actor, cause, notes="half done"); raise RuntimeError("bug")
    register(OpSpec("boom", "{}", "", 0.0, lambda s, a: None, boom, 90))
    out = apply_act(s, Act("p01", "p01", "boom", {}), 0)
    assert not out.ok and out.msg == "Not done: that could not be carried out."
    assert s.hash() == before and len(s.engine_errors) == 1


def test_a_check_that_breaks_is_reported_not_raised():
    s = thin(); before = s.hash()
    register(OpSpec("bad_check", "{}", "", 0.0, lambda s, a: 1 / 0, lambda s, a, c: "done", 90))
    out = apply_act(s, Act("p01", "p01", "bad_check", {}), 0)
    assert out.msg == "Not done: that could not be carried out." and s.hash() == before
    assert len(s.engine_errors) == 1


def test_operation_names_ignore_case_and_spaces():
    s = thin()
    out = apply_act(s, Act("p01", "p01", " Pay ", {"to": "Megan Pryce", "amount": 1}), 4)
    assert out.ok and s.ledger.transfers[-1].cause == "op:p01:pay:d0.4"
    assert apply_act(s, Act("p01", "p01", "WRITE_NOTES", {"text": "x"}), 5).ok


def test_clean_text_keeps_newlines_and_tabs_only():
    assert clean_text("a\nb\tc\x00d\re\x1bf\x85g\u200bh") == "a\nb\tcdefgh"     # format characters go too
    assert clean_text(5) is None and clean_text(None) is None and clean_text("") == ""


def test_a_done_act_carries_its_cause_and_takes_its_hours():
    s = thin(); seen = []
    def note(store, act, cause):
        seen.append(cause); store.set("people", act.actor, cause, notes="x"); return "Done it."
    register(OpSpec("note", "{}", "", 1.5, lambda s, a: None, note, 90))
    s.advance_day()
    out = apply_act(s, Act("p02", "p02", "note", {}), 3)
    assert out.ok and out.msg == "Done it." and seen == ["op:p02:note:d1.3"]
    assert s.get("people", "p02").hours_left == 68.5
    assert {c.cause for c in s.changes[-2:]} == {"op:p02:note:d1.3"}


def test_hours_are_taken_and_run_out():
    s = thin(); s.set("people", "p01", "routine:test", hours_left=0.3)
    assert apply_act(s, Act("p01", "p01", "message", {"to": "Craig Pryce", "text": "hello"}), 0).ok
    assert s.get("people", "p01").hours_left == pytest.approx(0.05)
    out = apply_act(s, Act("p01", "p01", "message", {"to": "Craig Pryce", "text": "again"}), 1)
    assert out.msg == "Not done: you have no time left this week for that."


def test_to_cents():
    assert [to_cents(x) for x in (12, 12.5, "12", "$1,308.50", " 7.25 ")] == [1200, 1250, 1200, 130850, 725]
    assert [to_cents(x) for x in (0, -1, True, None, "lots", [], 10**7)] == [None] * 7


def test_to_cents_is_exact_and_rounds_half_up():
    assert to_cents(0.29) == 29 and to_cents(1.005) == 101 and to_cents("0.125") == 13
    assert to_cents(1_000_000) == 100_000_000 and to_cents(1_000_000.01) is None
    assert to_cents("$ 5") == 500 and to_cents(".5") == 50


def test_to_cents_reads_commas_only_as_thousands():
    assert [to_cents(x) for x in ("12,50", "1,2,3", "1,00", "1,000", "1,000.50", "5.")] == \
        [None, None, None, 100000, 100050, 500]
    assert [to_cents(x) for x in ("$12,345.6", ",100", "100,", "1,0000", "1,000,000", "1,000.", "1.000,50")] == \
        [1234560, None, None, None, 100_000_000, 100000, None]
    for junk in (float("nan"), float("inf"), "1e3", "5$", "-5", "1.2.3", "", "$", "9" * 5000, 0.004, {}, b"5"):
        assert to_cents(junk) is None


def test_resolve_and_account_of():
    s = thin()
    assert resolve(s, "  ruth ASHBY ") == "p01" and resolve(s, "Pryce") == "h2"
    assert resolve(s, "wenlow lettings") == "org1"
    assert resolve(s, "Sanjay") is None and resolve(s, None) is None and resolve(s, ["Ruth Ashby"]) is None
    assert [account_of(s, w) for w in ("p04", "h1", "org2")] == ["h2", "h1", "org2"]


def test_forms_are_the_eight_in_order():
    names = list(REGISTRY)
    assert names[:8] == ["pay", "standing_payment", "stop", "message", "request", "answer", "read", "write_notes"]
    assert forms(thin(), "p01")[0] == ('{"op": "pay", "to": "<name>", "amount": <dollars>, '
                                       '"note": "<what for>", "for": "<id of what it is owed on, if any>"}')
    assert len(forms(thin(), "p01")) == len([s for s in REGISTRY.values() if s.who == "all"]) == 8


def test_registering_twice_is_harmless_but_a_different_spec_is_refused():
    importlib.reload(ops_core)
    importlib.import_module("townsim.town.ops_core")
    assert list(REGISTRY)[:8] == ["pay", "standing_payment", "stop", "message", "request", "answer",
                                  "read", "write_notes"]
    register(REGISTRY["pay"])
    with pytest.raises(ValueError):
        register(OpSpec("pay", "{}", "", 0.0, lambda s, a: None, lambda s, a, c: "", 10))
    assert acts.REGISTRY["pay"].hours == 0.25


def test_forms_go_by_order_whatever_the_order_of_registering():
    s = thin()
    before = forms(s, "p01")
    specs = list(REGISTRY.values())
    REGISTRY.clear()
    for spec in reversed(specs):
        register(spec)
    assert forms(s, "p01") == before
    register(OpSpec("borrow", "{borrow}", "", 0.0, lambda s, a: None, lambda s, a, c: "", 15))
    register(OpSpec("aardvark", "{aardvark}", "", 0.0, lambda s, a: None, lambda s, a, c: "", 15))
    assert forms(s, "p01")[:4] == [before[0], "{aardvark}", "{borrow}", before[1]]   # ties go by name
    assert [REGISTRY[n].order for n in ("pay", "standing_payment", "write_notes")] == [10, 20, 80]
