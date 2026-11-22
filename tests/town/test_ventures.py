"""Services and clubs: founding one, its members and their fees, and whose systems it is."""

from town.test_seat_texts import drawn
from townsim.town import acts, connections, lane_b, obligations, seat_texts, systems, ventures
from townsim.town.acts import Act, apply_act

CAUSE = "routine:setup"
CLUB = {"name": "Harlow Walkers", "kind": "club", "offering": "Weekly walks on the ridge with a packed lunch",
        "weekly_fee": 5}


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _total(s) -> int:
    return sum(a.balance for a in s.ledger.accounts.values())


def _register(s, actor="p5", principal=None, **args):
    return apply_act(s, Act(actor, principal or actor, "register_business", {**CLUB, **args}), 0)


def test_register_business_pays_fee_and_opens_account():
    s = _b()
    total, before, office, hours = _total(s), s.balance("h3"), s.balance("org2"), s.get("people", "p5").hours_left
    out = _register(s)
    assert out.ok, out.msg
    assert out.msg == ("Registered: Harlow Walkers, a club owned by you, with its own account (no money in it yet). "
                       "Members pay $5.00 a week.")
    v = s.all("ventures")[0]
    org = s.get("orgs", v.org)
    assert (org.name, org.kind, org.owner, v.client, v.founder, v.fee_cents, v.by) == \
        ("Harlow Walkers", "club", "p5", "h3", "p5", 500, "p5")
    assert s.balance(v.org) == 0 and s.balance("h3") == before - 15000 and s.balance("org2") == office + 15000
    assert _total(s) == total
    assert s.all("records")[-1].text == "Harlow Walkers was registered by Nicole Kowalski as a club on Monday 9 March."
    assert s.get("people", "p5").hours_left == hours - 1.0
    # the same name again, a bad kind, and a fee too high are refused, changing nothing
    h = s.hash()
    for args in ({}, {"name": "harlow walkers"}, {"name": "x", "kind": "club"}, {"name": "Ridge Riders", "kind": "shop"},
                 {"name": "Ridge Riders", "weekly_fee": 501}, {"name": "Ridge Riders", "offering": " ".join(["w"] * 21)}):
        assert not _register(s, "p6", **args).ok
    assert s.hash() == h


def test_without_lane_b_there_is_no_such_operation():
    s = drawn()
    h = s.hash()
    out = _register(s)
    assert not out.ok and out.msg == "Not done: there is no operation called 'register_business'."
    assert s.hash() == h
    assert not [f for f in acts.forms(s, "p5") if "register_business" in f]
    assert [f for f in acts.forms(_b(), "p5") if "register_business" in f]


def _with_club(fee=5):
    s = _b()
    assert _register(s, weekly_fee=fee).ok
    return s, s.all("ventures")[0]


def test_join_request_accepted_makes_member_and_fee():
    s, v = _with_club()
    out = apply_act(s, Act("p5", "p5", "invite_member", {"of": "harlow walkers", "to": "Eric Reyes"}), 1)
    assert out.ok, out.msg
    rq = s.all("requests")[-1]
    assert (rq.kind, rq.to, rq.org, rq.payer, rq.pay, rq.cents) == ("join", "p7", v.org, "h4", 500, 0)
    assert rq.summary == ("Accepting makes the Reyes household a member of Harlow Walkers: $5.00 a week from its "
                          "account from Monday 16 March, until it leaves.")
    again = apply_act(s, Act("p3", "p3", "invite_member", {"of": "Harlow Walkers", "to": "Eric Reyes"}), 2)
    assert again.msg == "Not done: 'Harlow Walkers' is not a service or club of yours."
    total = _total(s)
    out = apply_act(s, Act("p7", "p7", "answer", {"request": rq.id, "answer": "accept"}), 3)
    assert out.ok and out.msg == ("Accepted. The Reyes household is a member of Harlow Walkers: $5.00 a week from "
                                  "Monday 16 March, until it leaves.")
    m = s.all("members")[0]
    ob = s.get("obligations", m.fee_ob)
    assert (m.account, m.person, ob.payer, ob.payee, ob.cents, ob.first, ob.every, ob.ends, ob.kind) == \
        ("h4", "p7", "h4", v.org, 500, 14, 7, "service", "fee")
    assert _total(s) == total
    twice = apply_act(s, Act("p5", "p5", "invite_member", {"of": "Harlow Walkers", "to": "Jason Reyes"}), 4)
    assert twice.msg == "Not done: the Reyes household is already a member of Harlow Walkers."
    # a new fee is for members who join from now
    out = apply_act(s, Act("p5", "p5", "set_price", {"for": "Harlow Walkers", "price": 7.5}), 5)
    assert out.msg == ("Members who join Harlow Walkers from now pay $7.50 a week; members already in pay what they "
                       "agreed.")
    assert s.get("ventures", v.id).fee_cents == 750 and s.get("obligations", m.fee_ob).cents == 500


def test_stopping_fee_ends_membership():
    s, v = _with_club()
    apply_act(s, Act("p5", "p5", "invite_member", {"of": "Harlow Walkers", "to": "Eric Reyes"}), 1)
    apply_act(s, Act("p7", "p7", "answer", {"request": s.all("requests")[-1].id, "answer": "accept"}), 2)
    m = s.all("members")[0]
    out = apply_act(s, Act("p8", "p8", "stop", {"id": m.fee_ob}), 3)
    assert out.ok and out.msg == "You have left Harlow Walkers (Harlow Walkers membership); the fee ends."
    assert s.get("members", m.id).status == "left"
    assert s.all("notices")[-1].to == "p5" and s.all("notices")[-1].text == "The Reyes household has left Harlow Walkers."


def test_instance_founds_venture_and_is_connected_at_account_scope():
    s = _b()
    inst = s.get("instances", "as5")
    connections.make(s, "p1", inst, "account:h1", "limit", 30_000, CAUSE)
    told = len(s.where("notices", to="p2"))
    out = apply_act(s, Act("as5", "p1", "register_business", {**CLUB, "from": "household"}), 0)
    assert out.ok, out.msg
    v = s.all("ventures")[0]
    assert (v.client, v.founder, v.by, s.get("orgs", v.org).owner) == ("h1", "p1", "as5", "p1")
    made = {c.system: (c.grantor, c.scope, c.weekly_cents) for c in connections.active(s, "as5")}
    assert made[f"account:{v.org}"] == ("p1", "limit", 30_000)
    assert made[f"letters:{v.org}"] == ("p1", "free", 0) and made[f"members:{v.org}"] == ("p1", "free", 0)
    news = [n.text for n in s.where("notices", to="p2")][told:]
    assert "Connected: Harlow Walkers's account, to Tessaly Assistant for the Carter household: it acts, taking at " \
           "most $300.00 out of your accounts in any week." in news
    # what it pays out of the venture's account counts toward the same weekly limit as the client's own
    s.transfer("h1", v.org, 20_000, "opening", CAUSE)
    pay = apply_act(s, Act("as5", "p1", "pay", {"to": "Eric Reyes", "amount": 100, "from": "Harlow Walkers"}), 1)
    assert pay.ok, pay.msg
    over = apply_act(s, Act("as5", "p1", "pay", {"to": "Eric Reyes", "amount": 60, "from": "household"}), 2)
    assert over.beyond and "with $250.00 already taken or committed this week" in over.msg
    over = apply_act(s, Act("as5", "p1", "pay", {"to": "Eric Reyes", "amount": 60, "from": "Harlow Walkers"}), 3)
    assert over.beyond
    assert apply_act(s, Act("as5", "p1", "pay", {"to": "Eric Reyes", "amount": 50, "from": "Harlow Walkers"}), 4).ok


def test_registration_counts_toward_the_limit():
    s = _b()
    h = s.hash()
    out = apply_act(s, Act("as3", "p9", "register_business", {**CLUB, "from": "household"}), 0)   # a $50.00 limit
    assert out.beyond and "weekly limit of $50.00" in out.msg and s.hash() == h


def test_venture_systems_belong_to_founding_client():
    s, v = _with_club()
    ids = [x.id for x in systems.systems_of_client(s, "h3")]
    assert ids == ["account:h3", "letters:h3", f"account:{v.org}", f"letters:{v.org}", f"members:{v.org}"]
    assert systems.systems_of_client(s, v.org) == []
    assert {systems.client_of(s, f"{k}:{v.org}") for k in ("account", "letters", "members")} == {"h3"}
    assert systems.can_grant(s, "p5", f"members:{v.org}") and not systems.can_grant(s, "p6", f"account:{v.org}")
    assert systems.system(s, f"members:{v.org}").long == "the members of Harlow Walkers"
    assert [x.id for x in systems.grantable(s, "p6")] == ["account:h3", "letters:h3"]
    assert len({x.id for x in systems.grantable(s, "p5")}) == len(systems.grantable(s, "p5")) == 5
    assert systems.system(s, "members:org11") is None


def test_founder_of_club_ops_refuse_cleanly():
    s, v = _with_club()
    assert "owner" in acts.positions(s, "p5")
    out = apply_act(s, Act("p5", "p5", "order_stock", {"supplier": "Netherby Falls Wholesale", "baskets": 10}), 1)
    assert not out.ok and out.msg == "Not done: Harlow Walkers is not a shop." and not s.engine_errors
    out = apply_act(s, Act("p5", "p5", "set_price", {"price": 9}), 2)
    assert not out.ok and not s.engine_errors
    # a shop owner who founds a club still orders for the shop
    assert _register(s, "p3", principal="p3", name="Ridge Riders", **{"from": "household"}).ok
    sid = systems.SYSTEM_OF["order_stock"](s, Act("as1", "p3", "order_stock", {}))
    assert sid == "ordering:org11"
    from townsim.town import product
    assert product.business_page(s, s.get("orgs", v.org)) == (
        f"Harlow Walkers, a club in {s.place} run by Nicole Kowalski: Weekly walks on the ridge with a packed lunch. "
        "Weekly fee: $5.00. Members: 0.")
    assert obligations.name_of(s, v.org) == "Harlow Walkers"


def _own_client(kind):
    """A venture of `kind` founded for the Kowalski household and subscribed to in its own name: its instance."""
    s = _b()
    name = f"Ridge {kind.title()}"
    oid = ventures.found(s, "p5", "h3", name, kind, "Walks on the ridge", 0, "p5", CAUSE)
    assert apply_act(s, Act("p5", "p5", "subscribe", {"to": "Tessaly Assistant", "for": name}), 0).ok
    sub = next(x for x in s.all("subscriptions") if x.client == oid)
    return s, s.get("instances", sub.instance), name


def test_a_venture_subscribed_in_its_own_name_has_its_integrations_written():
    """Day 68 of b4-choice: a club's founder subscribed for the club itself, and the instance's members, a system
    of its own client's, had no words (KeyError 'members' out of the day)."""
    for kind, kinds in (("club", ["account", "letters", "members"]), ("service", ["account", "letters", "members"]),
                        ("shop", ["account", "letters", "staff"])):
        s, inst, name = _own_client(kind)
        assert [x.kind for x in systems.systems_of_client(s, inst.client)] == kinds
        lines = seat_texts.integration_lines(s, inst)
        assert len(lines) == len(kinds) and lines[1] == f"- Email: {name}'s mailbox. Not connected."
        if kind != "shop":
            assert lines[2] == f"- Members: the members of {name}. Not connected."
        assert f"Client: {name}" in seat_texts.system_text(s, inst)


def test_every_kind_of_system_of_the_client_itself_has_words():
    """The words for a system are worked out by its kind: none of `systems.KINDS` is missing, whoever's it is."""
    s, inst, name = _own_client("club")
    for kind in systems.KINDS:
        owner = "org9" if kind == "loans" else inst.client   # the bank
        assert seat_texts._what(s, inst, systems.System(f"{kind}:{owner}", kind, owner, "", ""))
