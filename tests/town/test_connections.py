"""Instances, connections and acting for a client (design 4.1)."""

from town.helpers import generated
from townsim.town import acts
from townsim.town.entities import Instance

CAUSE = "routine:setup"


def _instance(s, client: str = "h6", iid: str = "as1") -> str:
    name = f"Kestrel Assistant for the {s.get('households', client).name}" if s.has("households", client) else \
        f"Kestrel Assistant for {s.get('orgs', client).name}"
    s.add("instances", Instance(iid, "pr1", client, "sub1", name, made_on=s.day), CAUSE)
    return iid


def test_instance_rows_round_trip_and_hash_unchanged_without_them():
    s = generated(1)
    before = s.hash()
    assert "instances" not in s.canonical()["tables"]
    mark = s.mark()
    _instance(s)
    assert s.hash() != before and s.get("instances", "as1").seen == []
    s.undo_to(mark)
    assert s.hash() == before


def test_name_of_who_knows_instances():
    s = generated(1)
    _instance(s)
    assert acts.name_of_who(s, "as1") == "Kestrel Assistant for the Varley household"
    assert acts.is_instance(s, "as1") and not acts.is_instance(s, "p10") and not acts.is_instance(s, 7)


# acting for a client

from town.helpers import run_days  # noqa: E402
from townsim.town import ops_core  # noqa: E402,F401  (registers the operations and the check)
from townsim.town.acts import Act, apply_act  # noqa: E402
from townsim.town.connections import used  # noqa: E402
from townsim.town.entities import Connection  # noqa: E402


def _connect(s, iid: str, system: str, grantor: str, scope: str, limit: int = 0) -> str:
    cid = s.new_id("cn", CAUSE)
    s.add("connections", Connection(cid, iid, system, grantor, scope, limit, s.day), CAUSE)
    return cid


def _varley(scope: str = "free", limit: int = 0):
    s = generated(1)
    _instance(s)
    _connect(s, "as1", "account:h6", "p10", scope, limit)
    return s


def _pay(s, dollars, principal="p10", **more):
    return apply_act(s, Act("as1", principal, "pay", {"to": "Rosa Sowerby", "amount": dollars, **more}), 1)


def test_instance_act_with_no_connection_is_not_done_and_changes_nothing():
    s = generated(1)
    _instance(s)
    before = s.hash()
    out = _pay(s, 5)
    assert not out.ok and out.beyond and out.msg == "Not done: there is no connection to the Varley household's account and savings."
    assert s.hash() == before
    out = apply_act(s, Act("as1", "p10", "message", {"to": "Rosa Sowerby", "text": "hello"}), 2)
    assert out.beyond and out.msg == "Not done: that is not something done for a client."
    out = apply_act(s, Act("p3", "p10", "pay", {"to": "Rosa Sowerby", "amount": 5}), 3)
    assert out.beyond and out.msg == "Not done: you have no connection that allows you to act for them."
    assert s.hash() == before


def test_read_only_allows_read_and_refuses_pay():
    s = _varley("read")
    assert apply_act(s, Act("as1", "p10", "read", {"what": "account"}), 1).ok
    before = s.hash()
    out = _pay(s, 5)
    assert out.beyond and out.msg == "Not done: the connection to the Varley household's account and savings is read only."
    out = apply_act(s, Act("as1", "p10", "standing_payment", {"to": "Rosa Sowerby", "amount": 5, "day": "Friday"}), 2)
    assert out.beyond and s.hash() == before


def test_weekly_limit_counts_payments_and_later_collections():
    s = _varley("limit", 5000)
    out = apply_act(s, Act("as1", "p10", "standing_payment",
                           {"to": "Rosa Sowerby", "amount": 30, "day": "Wednesday"}), 1)
    assert out.ok, out.msg
    out = _pay(s, 25)
    assert out.beyond and out.msg == ("Not done: that would take $25.00 out of the Varley household's account this week; "
                                      "with $30.00 already taken or committed this week under this connection, that "
                                      "is more than the weekly limit of $50.00.")
    run_days(s, 7)
    cn = s.get("connections", "cn1")
    assert used(s, cn, s.day) == 3000
    assert _pay(s, 25).beyond
    out = _pay(s, 15)
    assert out.ok and not out.beyond, out.msg
    assert used(s, cn, s.day) == 4500 and [c.cents for c in s.all("commits")] == [3000, 1500]
    assert _pay(s, 6).beyond and _pay(s, 5).ok


def test_limit_counts_an_open_agreement_proposal():
    s = _varley("limit", 5000)
    out = apply_act(s, Act("as1", "p10", "propose_agreement",
                           {"to": "Rosa Sowerby", "amount": 40, "per": "week", "weeks": 4, "start": "Monday 30 March",
                            "payer": "me", "what": "help in the garden"}), 1)
    assert out.ok, out.msg
    assert _pay(s, 15).beyond and _pay(s, 10).ok
    run_days(s, 7)
    assert [r.status for r in s.all("requests") if r.sender == "as1"] == ["open"]
    assert used(s, s.get("connections", "cn1"), s.day) == 4000
    assert _pay(s, 15).beyond and _pay(s, 10).ok


def test_principal_is_grantor_and_cause_names_instance():
    s = _varley()
    out = _pay(s, 5)
    assert out.ok and out.cause == "op:as1/p10:pay:d0.1"
    assert any(t.cause == out.cause and t.src == "h6" for t in s.ledger.transfers)
    s.set("connections", "cn1", CAUSE, grantor="p3")
    before = s.hash()
    out = _pay(s, 5)
    assert out.beyond and out.msg == "Not done: there is no connection to the Varley household's account and savings."
    assert s.hash() == before


def test_instance_acts_take_no_hours():
    s = _varley()
    left = s.get("people", "p10").hours_left
    assert _pay(s, 5).ok and s.get("people", "p10").hours_left == left


def test_household_instance_cannot_draw_on_owners_shop():
    s = generated(1)
    _instance(s, "h9")
    _connect(s, "as1", "account:h9", "p16", "free")
    before = s.hash()
    out = _pay(s, 5, principal="p16", **{"from": "Lumb's Grocer"})
    assert out.beyond and out.msg == "Not done: there is no connection to Lumb's Grocer's account."
    out = apply_act(s, Act("as1", "p16", "set_price", {"price": 9}), 2)
    assert out.beyond and out.msg == "Not done: there is no connection to the prices of Lumb's Grocer."
    assert s.hash() == before
    assert _pay(s, 5, principal="p16").ok
