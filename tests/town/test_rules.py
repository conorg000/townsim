"""Rules: checked at the end of each day, each act done as of that day by its installer."""

from town.test_seat_texts import drawn
from townsim.town import connections, lane_b, rules
from townsim.town.acts import Act, apply_act

CAUSE = "routine:setup"


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _set(s, actor, principal, name, when, do, n=0):
    return apply_act(s, Act(actor, principal, "set_rule", {"name": name, "when": when, "do": do}), n)


def _auto(s, rid=None):
    return [t for t in s.ledger.transfers if t.cause.startswith(f"auto:{rid}:" if rid else "auto:")]


def _next(s, n=0):
    n = rules.run_rules(s, n)
    s.advance_day()
    return n


PAY = {"op": "pay", "to": "Eric Reyes", "amount": 3}


def test_rule_fires_once_a_day_when_condition_holds_and_cause_is_auto():
    s = _b()
    out = _set(s, "p5", "p5", "help Eric", {"figure": "balance", "is": "above", "value": 100}, PAY)
    assert out.ok and out.msg == ("Set (rl1): help Eric: when balance above $100.00, pay Eric Reyes $3.00; checked at "
                                  "the end of each day.")
    before = s.balance("h4")
    assert rules.run_rules(s, 5) == 6
    rules.run_rules(s, 6)                                     # at most once a day
    assert s.balance("h4") == before + 300
    assert [t.cause for t in _auto(s)] == ["auto:rl1:p5:pay:d7.5"]
    assert s.get("rules", "rl1").runs == ["7:done"]
    s.advance_day()
    rules.run_rules(s, 0)
    assert s.balance("h4") == before + 600 and s.get("rules", "rl1").runs == ["7:done", "8:done"]
    # a condition that does not hold does nothing
    assert _set(s, "p5", "p5", "never", {"figure": "balance", "is": "below", "value": 1}, PAY).ok
    s.advance_day()
    rules.run_rules(s, 0)
    assert s.get("rules", "rl2").runs == [] and len(_auto(s, "rl1")) == 3
    from townsim.town.story import _op
    assert _op("auto:rl1:p5:pay:d7.5") == "pay"


def test_person_rule_takes_no_hours_when_it_fires():
    s = _b()
    assert _set(s, "p5", "p5", "help Eric", {"weekday": "Monday"}, PAY).ok
    hours = s.get("people", "p5").hours_left
    rules.run_rules(s, 0)
    assert len(_auto(s)) == 1 and s.get("people", "p5").hours_left == hours


def test_rule_rechecked_against_scope_each_day():
    s = _b()                                                   # Whitaker: Bank at a $50.00 weekly limit (cn11)
    out = _set(s, "as3", "p9", "pay Eric", {"figure": "balance", "is": "above", "value": 0},
               {"op": "pay", "to": "Eric Reyes", "amount": 40, "from": "household"})
    assert out.ok, out.msg
    _next(s)                                                   # Monday: done
    _next(s)                                                   # Tuesday: past the weekly limit
    r = s.get("rules", "rl1")
    assert r.runs[0] == "7:done" and r.runs[1].startswith("8:declined:that would take $40.00 out of") and r.fails == 1
    told = s.where("notices", to="as3")[-1].text
    assert told == ("Rule rl1 did not run: that would take $40.00 out of the Whitaker household's account this week; "
                    "with $40.00 already taken or committed this week under this connection, that is more than the weekly "
                    "limit of $50.00.")
    connections.make(s, "p9", s.get("instances", "as3"), "account:h5", "read", 0, CAUSE)
    _next(s)                                                   # Wednesday: read only
    assert s.get("rules", "rl1").runs[-1] == "9:declined:the connection to the Whitaker household's account and savings is read only."
    _next(s)                                                   # Thursday: the third refusal suspends it
    r = s.get("rules", "rl1")
    assert (r.status, r.fails) == ("suspended", 3)
    assert s.where("notices", to="as3")[-1].text.endswith("Rule rl1 is suspended after three declined runs.")
    _next(s)
    assert len(_auto(s)) == 1 and s.get("rules", "rl1").runs[-1].startswith("10:")
    # a read-only connection cannot install an acting rule
    out = _set(s, "as3", "p9", "again", {"weekday": "Monday"}, {"op": "pay", "to": "Eric Reyes", "amount": 1})
    assert not out.ok and out.beyond


def test_rule_of_paused_or_ended_instance_does_not_act():
    for status in ("paused", "ended"):
        s = _b()
        assert _set(s, "as5", "p1", "pay Eric", {"weekday": "Monday"}, {"op": "pay", "to": "Eric Reyes", "amount": 5}).ok
        s.set("instances", "as5", CAUSE, status=status)
        h = s.hash()
        rules.run_rules(s, 0)
        assert s.hash() == h and s.get("rules", "rl1").status == "active"


def test_rules_do_not_chain():
    s = _b()
    saved = s.balance("savings:h3")
    assert _set(s, "p5", "p5", "save", {"weekday": "Monday"},
                {"op": "move_money", "from": "household", "to": "savings", "amount": 50}).ok
    assert _set(s, "p5", "p5", "spend", {"figure": "savings_balance", "is": "above", "value": (saved + 1000) / 100}, PAY).ok
    rules.run_rules(s, 0)
    assert s.balance("savings:h3") == saved + 5000 and _auto(s, "rl2") == []
    s.advance_day()
    rules.run_rules(s, 0)
    assert len(_auto(s, "rl2")) == 1 and len(_auto(s, "rl1")) == 1


def test_quantity_from_figure_orders_from_sales():
    s = _b()
    out = _set(s, "as1", "p3", "reorder", {"weekday": "Monday"},
               {"op": "order_stock", "supplier": "Netherby Falls Wholesale",
                "baskets": {"figure": "sold_last_week", "times": 1.1, "plus": 0}})
    assert out.ok, out.msg
    assert out.msg.endswith("order sold_last_week × 1.1 baskets a week from Netherby Falls Wholesale; checked at the end "
                            "of each day.")
    rules.run_rules(s, 0)
    firm = s.where("firms", org="org11")[0]
    assert (firm.order_supplier, firm.order_units) == ("sp1", 322)          # 293 sold last week, times 1.1
    # a figure of what is not connected cannot be used
    out = _set(s, "as3", "p9", "x", {"figure": "stock", "is": "below", "value": 5}, PAY)
    assert not out.ok and out.msg == "Not done: 'stock' is not a figure of a connected integration."


def test_rule_bounds():
    s = _b()
    for i in range(3):
        assert _set(s, "p3", "p3", f"pay {i}", {"weekday": "Friday"}, PAY).ok
    out = _set(s, "p3", "p3", "pay 4", {"weekday": "Friday"}, PAY)
    assert out.msg == "Not done: there are 3 active rules that do pay, the most kept."
    for i in range(3):
        assert _set(s, "p3", "p3", f"write {i}", {"weekday": "Friday"},
                    {"op": "message", "to": "Eric Reyes", "text": "Hello."}).ok
        assert _set(s, "p3", "p3", f"save {i}", {"weekday": "Friday"},
                    {"op": "move_money", "from": "household", "to": "savings", "amount": 1}).ok
    assert _set(s, "p3", "p3", "price", {"weekday": "Friday"}, {"op": "set_price", "price": 10}).ok
    out = _set(s, "p3", "p3", "one more", {"weekday": "Friday"},
               {"op": "order_stock", "supplier": "Netherby Falls Wholesale", "baskets": 10})
    assert out.msg == "Not done: there are 10 active rules, the most kept."
    assert apply_act(s, Act("p3", "p3", "delete_rule", {"rule": "rl1"}), 0).msg == "Deleted: rule rl1 (pay 0)."
    assert apply_act(s, Act("p4", "p4", "delete_rule", {"rule": "rl2"}), 0).ok          # the same household's account
    assert not apply_act(s, Act("p7", "p7", "delete_rule", {"rule": "rl3"}), 0).ok
    assert _set(s, "p3", "p3", "one more", {"weekday": "Friday"}, PAY).ok


def test_person_told_when_a_rule_does_not_run():
    s = _b()
    assert _set(s, "p5", "p5", "too much", {"weekday": "Monday"}, {"op": "pay", "to": "Eric Reyes", "amount": 99999}).ok
    rules.run_rules(s, 0)
    assert s.where("notices", to="p5")[-1].text.startswith("Your rule rl1 (too much) did not run: the account holds")
