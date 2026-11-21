import os
import subprocess
import sys

import pytest

from townsim.econ.ledger import Overdraft
from townsim.town.entities import Household, Message, Notice, Person
from townsim.town.store import TABLES, Store, id_key


def small_store() -> Store:
    s = Store()
    s.open_account("outside:x", "routine:setup", allow_credit=True)
    for h, p, name in (("h1", "p1", "Ashby"), ("h2", "p2", "Brook")):
        s.open_account(h, "routine:setup")
        s.add("households", Household(h, name, [p]), "routine:setup")
        s.add("people", Person(p, f"{name} One", h, 40.0, 40.0, 0), "routine:setup")
        s.transfer("outside:x", h, 10_000, "opening", "routine:setup")
    return s


def test_every_change_is_logged_with_its_cause():
    s = Store(); s.open_account("h1", "routine:setup"); s.open_account("outside:x", "routine:setup", allow_credit=True)
    s.add("households", Household("h1", "Ashby", ["p1"]), "routine:setup")
    s.set("households", "h1", "op:p1:x:d0.0", name="Ashby-Lee")
    s.transfer("outside:x", "h1", 500, "opening", "routine:setup")
    assert all(c.cause for c in s.changes)
    assert s.changes[-1].table == "ledger" and s.get("households", "h1").name == "Ashby-Lee"
    with pytest.raises(ValueError): s.set("households", "h1", "", name="x")
    with pytest.raises(KeyError): s.set("households", "nope", "routine:x", name="x")
    with pytest.raises(AttributeError): s.set("households", "h1", "routine:x", colour="red")


def test_undo_restores_the_exact_state():
    s = small_store(); before = s.hash(); m = s.mark()
    s.add("messages", Message(s.new_id("m"), 0, "p1", "p2", "hello"), "op:p1:message:d0.0")
    s.transfer("h1", "outside:x", 100, "x", "op:p1:pay:d0.1"); s.set("people", "p1", "routine:x", hours_left=1.0); s.advance_day()
    s.undo_to(m)
    assert s.hash() == before and s.ledger.total() == 0 and s.new_id("m") == "m1" and s.day == 0


def test_transaction_undoes_on_error_and_reraises():
    s = small_store(); before = s.hash()
    with pytest.raises(Overdraft):
        with s.transaction():
            s.set("people", "p1", "routine:x", notes="changed")
            s.transfer("h1", "outside:x", 10**9, "too much", "op:p1:pay:d0.0")
    assert s.hash() == before


def test_hash_is_stable_and_ignores_history():
    a, b = small_store(), small_store()
    assert a.hash() == b.hash()
    m = a.mark(); a.set("people", "p1", "routine:x", notes="n"); a.undo_to(m)
    assert a.hash() == b.hash()          # the change log is history, not state


def test_all_is_in_id_order_not_text_order():
    s = Store()
    for i in range(1, 12): s.add("notices", Notice(s.new_id("n"), 0, "p1", str(i)), "routine:x")
    assert [n.id for n in s.all("notices")][-2:] == ["n10", "n11"]


def test_a_new_store_is_empty():
    s = Store()
    assert s.day == 0 and s.place == "" and s.changes == [] and s.engine_errors == []
    assert all(s.all(t) == [] for t in TABLES) and s.ledger.accounts == {}


def test_id_key_splits_prefix_and_number():
    assert id_key("ob12") == ("ob", 12) and id_key("outside:p3") == ("outside:p", 3)
    with pytest.raises(ValueError): id_key("12")
    with pytest.raises(ValueError): id_key("ob")


def test_add_rejects_a_duplicate_id_an_empty_cause_and_an_unknown_table():
    s = small_store()
    with pytest.raises(ValueError): s.add("households", Household("h1", "Again", []), "routine:x")
    with pytest.raises(ValueError): s.add("notices", Notice("n1", 0, "p1", "x"), "")
    with pytest.raises(KeyError): s.add("nope", Notice("n1", 0, "p1", "x"), "routine:x")
    s.add("notices", Notice("h1", 0, "p1", "ids are unique within a table"), "routine:x")
    assert s.has("notices", "h1") and s.has("households", "h1") and not s.has("notices", "n1")


def test_a_failed_set_changes_nothing():
    s = small_store(); before = s.hash(); n = len(s.changes)
    with pytest.raises(AttributeError): s.set("people", "p1", "routine:x", notes="n", colour="red")
    assert s.hash() == before and len(s.changes) == n


def test_the_add_change_records_a_copy_not_the_live_object():
    s = Store(); s.add("people", Person("p1", "A", "h1", 40.0, 40.0, 0), "routine:x")
    s.set("people", "p1", "routine:x", name="B")
    assert s.changes[0].new.name == "A" and s.get("people", "p1").name == "B"


def test_undo_removes_an_opened_account_and_a_transfer_record():
    s = small_store(); m = s.mark(); n_transfers = len(s.ledger.transfers)
    s.open_account("o1", "routine:x")
    s.transfer("h1", "o1", 250, "rent", "op:p1:pay:d0.0")
    assert s.balance("o1") == 250 and s.balance("h1") == 9_750
    s.undo_to(m)
    assert not s.ledger.has("o1") and s.balance("h1") == 10_000
    assert len(s.ledger.transfers) == n_transfers and s.ledger.total() == 0


def test_undo_rewinds_id_counters():
    s = Store(); s.new_id("ob"); m = s.mark()
    assert s.new_id("ob") == "ob2" and s.new_id("ob") == "ob3"
    s.undo_to(m)
    assert s.new_id("ob") == "ob2"


def test_advance_day_moves_the_ledger_clock_and_is_undone():
    s = small_store(); m = s.mark()
    s.advance_day(); s.transfer("h1", "h2", 1, "x", "op:p1:pay:d1.0")
    assert s.day == 1 and s.ledger.t == 1 and s.ledger.transfers[-1].t == 1
    assert s.changes[0].cause and any(c.table == "day" for c in s.changes)
    s.undo_to(m)
    assert s.day == 0 and s.ledger.t == 0


def test_list_fields_are_replaced_whole_and_undo_restores_the_old_list():
    s = small_store(); before = s.hash(); m = s.mark()
    old = s.get("households", "h1").members
    s.set("households", "h1", "routine:x", members=old + ["p3"])
    assert old == ["p1"] and s.get("households", "h1").members == ["p1", "p3"]
    s.undo_to(m)
    assert s.get("households", "h1").members == ["p1"] and s.hash() == before


def test_engine_errors_are_not_state_and_are_not_undone():
    s = small_store(); before = s.hash(); m = s.mark()
    s.engine_errors.append("boom")
    assert s.hash() == before
    s.undo_to(m)
    assert s.engine_errors == ["boom"]


def test_floats_render_stably_in_canonical():
    a, b = small_store(), small_store()
    a.set("people", "p1", "routine:x", hours_left=0.1 + 0.2)
    b.set("people", "p1", "routine:x", hours_left=0.3)
    assert a.hash() == b.hash()


def test_a_transaction_that_succeeds_keeps_its_changes():
    s = small_store()
    with s.transaction():
        s.set("people", "p1", "routine:x", notes="kept")
    assert s.get("people", "p1").notes == "kept"


_HASH_SCRIPT = """
from tests.town.test_store import small_store
s = small_store()
s.set("people", "p2", "routine:x", hours_left=12.5, notes="x")
print(s.hash())
"""


def test_hash_is_the_same_in_separate_processes_with_different_hash_seeds():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    hashes = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", _HASH_SCRIPT], cwd=root, env=env,
                             capture_output=True, text=True, check=True)
        hashes.append(out.stdout.strip())
    assert hashes[0] == hashes[1] and len(hashes[0]) == 64


def test_a_failed_add_changes_nothing_and_logs_nothing():
    s = small_store(); before = s.hash(); n = len(s.changes)
    with pytest.raises(TypeError): s.add("notices", {"id": "n1"}, "routine:x")
    with pytest.raises(ValueError): s.add("notices", Notice("n٣", 0, "p1", "x"), "routine:x")
    assert s.hash() == before and len(s.changes) == n and not s.has("notices", "n1")


def test_set_copies_the_list_it_is_given():
    s = small_store()
    members = ["p1", "p3"]
    s.set("households", "h1", "routine:x", members=members)
    members.append("p4")
    assert s.get("households", "h1").members == ["p1", "p3"] and s.changes[-1].new == ["p1", "p3"]


def test_the_add_record_does_not_share_lists_with_the_row():
    s = Store(); s.add("households", Household("h1", "A", ["p1"]), "routine:x")
    s.get("households", "h1").members.append("p2")
    assert s.changes[0].new.members == ["p1"]


def test_all_orders_ids_that_tie_on_id_key_the_same_way_every_time():
    a, b = Store(), Store()
    for s, ids in ((a, ("p01", "p1")), (b, ("p1", "p01"))):
        for i in ids: s.add("notices", Notice(i, 0, "p1", i), "routine:x")
    assert [n.id for n in a.all("notices")] == [n.id for n in b.all("notices")] == ["p01", "p1"]


def test_id_key_rejects_non_ascii_digits():
    with pytest.raises(ValueError): id_key("p٣")


def test_undo_raises_if_the_ledger_disagrees_with_the_log():
    s = small_store(); m = s.mark()
    s.transfer("h1", "h2", 5, "x", "op:p1:pay:d0.0")
    s.ledger.transfers[-1] = s.ledger.transfers[-1]._replace(cents=6)
    with pytest.raises(RuntimeError): s.undo_to(m)
