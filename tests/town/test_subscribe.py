"""Subscribing, the fee, and leaving."""

from town.helpers import generated, received, run_days, to
from townsim.town import ops_core  # noqa: F401  (registers the operations)
from townsim.town import product
from townsim.town.acts import Act, apply_act, forms, positions
from townsim.town.connections import active, instance_of_client, make


def _launched(day: int = 7):
    """Seed 1 on `day` (a Monday), with Tessaly Assistant launched."""
    s = generated(1)
    s.day = day
    s.ledger.t = day
    product.launch(s)
    return s


def _do(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 1)


def test_subscribe_makes_instance_and_four_free_weeks():
    s = _launched()
    out = _do(s, "p16", "subscribe", to="Tessaly Assistant", **{"for": "household"}, asks="Keep the bills in order.")
    assert out.msg == ("Subscribed: Tessaly Assistant for the Marsh household (sub1). Free until Sunday 5 April; then "
                       "$6.00 a week from the Marsh household's account, until the subscription is stopped. Nothing is "
                       "connected to it yet.")
    sub = s.get("subscriptions", "sub1")
    inst = s.get("instances", sub.instance)
    fee = s.get("obligations", sub.fee_ob)
    assert (sub.client, sub.by, sub.day, sub.asks, sub.status) == ("h9", "p16", 7, "Keep the bills in order.", "active")
    assert (inst.id, inst.client, inst.subscription, inst.name, inst.made_on) == \
        ("as1", "h9", "sub1", "Tessaly Assistant for the Marsh household", 7)
    assert (fee.kind, fee.payer, fee.payee, fee.cents, fee.first, fee.every, fee.ends, fee.made_by, fee.memo) == \
        ("fee", "h9", "org13", 600, 35, 7, "service", "p16", "Tessaly Assistant subscription")
    told = [n for n in s.all("notices") if n.text == out.msg]
    assert [n.to for n in told] == ["p17", "p18"]
    assert [n.text for n in s.all("notices") if n.to == "as1"] == ["Joshua Marsh subscribed the Marsh household today."]
    assert "client" in positions(s, "p17") and "client" not in positions(s, "p1")
    biz = _do(s, "p16", "subscribe", to="tessaly assistant", **{"for": "Marsh's Grocery"})
    assert biz.ok and "$25.00 a week from Marsh's Grocery's account" in biz.msg
    assert s.get("subscriptions", "sub2").asks == ""
    assert _do(s, "p16", "subscribe", to="Wrenfold Assistant").msg == \
        "Not done: there is no product called 'Wrenfold Assistant'. Sold in Harlow Springs: Tessaly Assistant (Tessaly Software)."
    assert _do(s, "p11", "subscribe", to="Tessaly Assistant", **{"for": "savings"}).msg == \
        "Not done: a subscription is for a household or a business."
    own = _do(s, "p11", "subscribe", to="Tessaly Assistant", **{"for": "Tessaly Software"})
    assert own.ok and "Tessaly Software makes it, and pays no fee." in own.msg     # the firm is its own client
    sub = next(x for x in s.all("subscriptions") if x.client == "org13")
    assert sub.fee_ob == "" and s.get("instances", sub.instance).status == "active"
    assert _do(s, "p11", "stop", id=sub.id).msg == f"Stopped: {sub.id}, Tessaly Assistant for Tessaly Software."
    assert s.get("instances", sub.instance).status == "ended"
    assert _do(s, "p9", "subscribe", to="Tessaly Assistant", asks="word " * 151).msg == \
        "Not done: that is longer than 150 words."


def test_fee_first_collected_on_day_28_into_firm():
    s = _launched()
    _do(s, "p16", "subscribe", to="Tessaly Assistant")

    def fees():
        return [(t.cents, t.memo) for t in to(s, "org13") if t.src == "h9"]
    run_days(s, 28)
    assert s.day == 35 and fees() == []
    run_days(s, 1)
    assert fees() == [(600, "Tessaly Assistant subscription, due Monday 6 April")]
    run_days(s, 7)
    assert len(fees()) == 2


def test_one_subscription_per_client_any_adult():
    s = _launched()
    assert _do(s, "p17", "subscribe", to="Tessaly Assistant").ok
    assert _do(s, "p18", "subscribe", to="Tessaly Assistant").msg == \
        "Not done: the Marsh household already subscribes (sub1)."
    assert len(s.all("instances")) == 1 and len([o for o in s.all("obligations") if o.kind == "fee"]) == 1


def test_stop_subscription_or_fee_leaves_and_ends_connections():
    s = _launched()
    _do(s, "p16", "subscribe", to="Tessaly Assistant")
    _do(s, "p3", "subscribe", to="Tessaly Assistant", **{"for": "Harlow Springs Food Mart"})
    inst = instance_of_client(s, "h9")
    make(s, "p16", inst, "account:h9", "free", 0, "routine:setup")
    make(s, "p17", inst, "letters:h9", "read", 0, "routine:setup")
    assert _do(s, "p9", "stop", id="sub1").msg == "Not done: you have nothing with the id 'sub1'."
    out = _do(s, "p18", "stop", id="sub1")
    assert out.msg == "You have left Tessaly Software (Tessaly Assistant subscription); the fee ends."
    assert s.get("subscriptions", "sub1").status == "ended" and s.get("subscriptions", "sub1").ended_on == 7
    assert s.get("instances", "as1").status == "ended" and active(s, "as1") == []
    assert all(c.ended_on == 7 for c in s.all("connections"))
    assert s.get("obligations", s.get("subscriptions", "sub1").fee_ob).status == "stopped"
    assert "client" not in positions(s, "p16") or instance_of_client(s, "h9") is None
    assert _do(s, "p18", "stop", id="sub1").msg == "Not done: sub1 has ended."
    fee = s.get("subscriptions", "sub2").fee_ob
    assert _do(s, "p3", "stop", id=fee).msg == \
        "You have left Tessaly Software (Tessaly Assistant subscription); the fee ends."
    assert s.get("subscriptions", "sub2").status == "ended" and s.get("instances", "as2").status == "ended"
    assert _do(s, "p16", "subscribe", to="Tessaly Assistant").ok       # a client who left may subscribe again
    run_days(s, 40)
    assert not [d for d in s.all("dues") if d.obligation == fee] and not [t for t in to(s, "org13") if t.src == "h2"]


def test_subscribe_hidden_before_launch():
    s = generated(1)
    assert not [f for f in forms(s, "p16") if '"subscribe"' in f]
    assert _do(s, "p16", "subscribe", to="Tessaly Assistant").msg == "Not done: there is no operation called 'subscribe'."
    s = _launched()
    [form] = [f for f in forms(s, "p16") if '"subscribe"' in f]
    assert form == ('{"op": "subscribe", "to": "<name of the product>", "for": "household" | "savings" | '
                    '"Marsh\'s Grocery", "asks": "<what you want it to do, in your own words>"}')
