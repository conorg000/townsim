"""Connecting a system, narrowing it, taking it back, and an instance asking for one."""

from town.helpers import generated
from townsim.town import ops_core  # noqa: F401  (registers the operations)
from townsim.town.acts import Act, apply_act, forms, positions
from townsim.town.connections import active, ask
from townsim.town.entities import Instance

CAUSE = "routine:setup"


def _lumbs():
    """Seed 1 with the Lumb household (Martin p16, Thea p17, Wendy p18) subscribed."""
    s = generated(1)
    s.add("instances", Instance("as1", "pr1", "h9", "sub1", "Kestrel Assistant for the Lumb household"), CAUSE)
    return s


def _do(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 1)


def _notices(s, to):
    return [n.text for n in s.all("notices") if n.to == to]


def test_connect_and_narrow_and_disconnect_lists_what_stays():
    s = _lumbs()
    out = _do(s, "p16", "connect", system="household account", scope="free")
    assert out.msg == ("Connected: the Lumb household's account and savings, to Kestrel Assistant for the Lumb "
                       "household: it acts with no limit.")
    assert out.msg in _notices(s, "p17") and out.msg in _notices(s, "p18") and not _notices(s, "p16")
    assert _notices(s, "as1") == ["Martin Lumb connected the Lumb household's account and savings: it acts with no limit."]
    assert apply_act(s, Act("as1", "p16", "standing_payment", {"to": "Gail Whiteley", "amount": 12, "day": "Friday"}),
                     2).ok
    out = _do(s, "p17", "connect", system="Household Account", scope="weekly limit", weekly_limit="$40")
    assert out.ok and out.msg.endswith(": it acts, taking at most $40.00 out of your accounts in any week.")
    [cn] = active(s, "as1")
    assert (cn.scope, cn.weekly_cents, cn.grantor) == ("limit", 4000, "p17")
    assert [c.status for c in s.all("connections")] == ["ended", "active"]
    out = _do(s, "p18", "disconnect", system="household account")
    ob = s.all("obligations")[-1]
    assert out.msg == f"Disconnected: the Lumb household's account and savings. What it set up stays yours: {ob.id}: " \
                      f"standing payment to Gail Whiteley, $12.00 each Friday."
    assert active(s, "as1") == [] and s.get("obligations", ob.id).status == "active"
    assert _notices(s, "as1")[-1] == "Wendy Lumb took back the connection to the Lumb household's account and savings."
    assert _do(s, "p18", "disconnect", system="household account").msg == "Not done: household account is not connected."


def test_only_who_controls_a_system_connects_it():
    s = _lumbs()
    s.add("instances", Instance("as2", "pr1", "org10", "sub2", "Kestrel Assistant for Lumb's Grocer"), CAUSE)
    assert _do(s, "p16", "connect", system="Lumb's Grocer ordering", scope="read only").ok
    out = _do(s, "p17", "connect", system="Lumb's Grocer ordering", scope="free")
    assert out.msg.startswith("Not done: 'Lumb's Grocer ordering' is not one of your systems. Yours are: household")
    assert _do(s, "p16", "connect", system="household letters", scope="everything").msg == \
        "Not done: 'everything' is not \"read only\", \"weekly limit\" or \"free\"."
    assert _do(s, "p16", "connect", system="household letters", scope="weekly limit").msg.startswith("Not done: 'None'")
    out = apply_act(s, Act("as1", "p16", "connect", {"system": "household account", "scope": "free"}), 2)
    assert out.beyond and active(s, "as1") == []


def test_connection_request_accepted_makes_connection_by_acceptor():
    s = _lumbs()
    rq, summary = ask(s, "as1", "p17", "account:h9", "limit", 2500, "So I can pay the milk.", CAUSE)
    assert summary == ("Accepting connects the Lumb household's account and savings to Kestrel Assistant for the Lumb "
                       "household with a weekly limit: what it does there takes at most $25.00 a week out of your "
                       "accounts.")
    r = s.get("requests", rq)
    assert (r.kind, r.sender, r.to, r.org, r.payer, r.pay, r.expires) == ("connect", "as1", "p17", "account:h9",
                                                                         "limit", 2500, 14)
    assert ask(s, "as1", "p17", "ordering:org10", "free", 0, "", CAUSE)[0] is None
    assert ask(s, "as1", "p3", "account:h9", "free", 0, "", CAUSE)[0] is None
    out = apply_act(s, Act("as1", "p17", "answer", {"request": rq, "answer": "accept"}), 1)
    assert out.beyond and active(s, "as1") == []
    out = _do(s, "p17", "answer", request=rq, answer="accept")
    assert out.ok, out.msg
    [cn] = active(s, "as1")
    assert (cn.system, cn.grantor, cn.scope, cn.weekly_cents) == ("account:h9", "p17", "limit", 2500)
    rq2, _ = ask(s, "as1", "p18", "letters:h9", "read", 0, "", CAUSE)
    s.set("instances", "as1", CAUSE, status="ended")
    assert _do(s, "p18", "answer", request=rq2, answer="accept").msg.startswith("Not done: the Lumb household has no")


def test_connect_forms_show_only_the_readers_systems():
    s = _lumbs()
    s.add("instances", Instance("as2", "pr1", "org10", "sub2", "Kestrel Assistant for Lumb's Grocer"), CAUSE)
    martin = [f for f in forms(s, "p16") if '"op": "connect"' in f]
    assert martin == ['{"op": "connect", "system": "household account" | "household letters" | "Lumb\'s Grocer account" '
                      '| "Lumb\'s Grocer letters" | "Lumb\'s Grocer ordering" | "Lumb\'s Grocer prices" | "Lumb\'s '
                      'Grocer staff and pay", "scope": "read only" | "weekly limit" | "free", "weekly_limit": '
                      '<dollars, with a weekly limit>}']
    thea = [f for f in forms(s, "p17") if '"op": "connect"' in f or '"op": "disconnect"' in f]
    assert thea[0].startswith('{"op": "connect", "system": "household account" | "household letters", "scope"')
    assert thea[1] == '{"op": "disconnect", "system": "<none connected>"}'
    _do(s, "p17", "connect", system="household letters", scope="read only")
    assert '{"op": "disconnect", "system": "household letters"}' in forms(s, "p18")


def test_connect_hidden_without_subscription():
    s = generated(1)
    assert "client" not in positions(s, "p16")
    assert not [f for f in forms(s, "p16") if "connect" in f]
    assert _do(s, "p16", "connect", system="household account", scope="free").msg == \
        "Not done: there is no operation called 'connect'."
    s = _lumbs()
    assert "client" in positions(s, "p16") and "client" in positions(s, "p18") and "client" not in positions(s, "p10")
