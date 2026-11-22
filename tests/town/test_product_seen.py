"""Letters to an instance, the pages businesses keep, and what clients and the firm's people see."""

from town.helpers import generated, run_days
from townsim.town import ops_core  # noqa: F401  (registers the operations)
from townsim.town import product, views
from townsim.town.acts import Act, apply_act
from townsim.town.connections import make
from townsim.town.statement import statement, statement_text

CAUSE = "routine:setup"


def _launched():
    s = generated(1)
    s.day = s.ledger.t = 7
    product.launch(s)
    return s


def _do(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 1)


def test_letter_to_my_assistant_reaches_instance_and_wakes_it():
    s = _launched()
    _do(s, "p16", "subscribe", to="Tessaly Assistant")
    _do(s, "p3", "subscribe", to="Tessaly Assistant", **{"for": "Harlow Springs Food Mart"})
    seen = s.get("instances", "as1").seen
    out = _do(s, "p17", "message", to="my assistant", text="Please look at the gas bill.")
    assert out.msg == "Sent to Tessaly Assistant for the Marsh household."
    m = s.all("messages")[-1]
    assert (m.to, m.sender, m.org, m.unknown) == ("as1", "p17", "", [])
    assert int(m.id.removeprefix("msg")) > int(seen[0].removeprefix("msg"))      # new to it: it has a turn
    assert _do(s, "p1", "message", to="Tessaly Assistant for the Marsh household", text="Hello.").ok
    assert s.all("messages")[-1].to == "as1"
    assert _do(s, "p3", "message", to="Tessaly Assistant", text="Order eggs.").ok
    assert s.all("messages")[-1].to == "as2"
    assert _do(s, "p9", "message", to="my assistant", text="Hello?").msg == \
        "Not done: your letter to 'my assistant' came back undelivered."


def test_read_page_of_firm_before_and_after_launch():
    s = generated(1)
    assert _do(s, "p9", "read_page", of="Tessaly Software").msg == \
        "Tessaly Software, a small software firm in Harlow Springs that writes software for businesses."
    price = s.where("firms", org="org10")[0].price
    assert _do(s, "p9", "read_page", of="marsh's grocery").msg == \
        f"Marsh's Grocery, a food shop run by Joshua Marsh. A basket of food: ${price // 100}.{price % 100:02d}."
    assert _do(s, "p9", "read_page", of="Ashcombe Savings Bank").msg == (
        "Ashcombe Savings Bank, Harlow Springs branch. Accounts, savings and loans for households and businesses in Harlow Springs. "
        "Letters to the bank reach Jennifer Carter.")
    assert _do(s, "p9", "read_page", of="Tessaly Assistant").msg == \
        "Not done: there is no business in Harlow Springs called 'Tessaly Assistant'."
    assert _do(s, "p9", "read_page", of="Bramwell Falls Foods").msg.startswith("Not done: there is no business")
    product.launch(s)
    out = _do(s, "p9", "read_page", of="Tessaly Assistant")
    assert out.msg == product.page(s, "pr1") + "\n\n" + product.terms(s)
    assert _do(s, "p9", "read_page", of="Tessaly Software").msg == out.msg


def test_company_and_client_sections():
    s = generated(1)
    assert not [p for p in s.all("people") if "TESSALY" in views.layout(s, p.id).user]
    s.day = s.ledger.t = 7
    product.launch(s)
    _do(s, "p16", "subscribe", to="Tessaly Assistant")
    _do(s, "p3", "subscribe", to="Tessaly Assistant", **{"for": "Harlow Springs Food Mart"})
    make(s, "p17", s.get("instances", "as1"), "account:h9", "limit", 4000, CAUSE)
    user = views.layout(s, "p18").user
    assert ("TESSALY ASSISTANT\nThe Marsh household subscribes (sub1) since Monday 9 March: free until Sunday 5 April, "
            "then $6.00 a week.\nIts name: Tessaly Assistant for the Marsh household.\nConnected: household account, "
            "weekly limit $40.00 (by Alyssa Ortiz, Monday 9 March).\nNot connected: household letters.") in user
    assert "Harlow Springs Food Mart subscribes (sub2)" not in user
    assert "Harlow Springs Food Mart subscribes (sub2) since Monday 9 March: free until Sunday 5 April, then $25.00" \
        in views.layout(s, "p3").user
    assert "TESSALY ASSISTANT" not in views.layout(s, "p9").user
    _do(s, "p3", "stop", id="sub2")
    run_days(s, 29)
    for pid in ("p11", "p5"):
        assert ("TESSALY SOFTWARE\nLaunched Monday 9 March. Price: $6.00 a week for a household, $25.00 for a "
                "business; the first four weeks free.\nClients (1):\n  The Marsh household, since Monday 9 March, paying "
                "$6.00 a week\nLeft: Harlow Springs Food Mart, Monday 9 March.\nFees received: $6.00 this week, $6.00 in "
                "all.") in views.layout(s, pid).user


def test_statement_marks_instance_lines():
    s = _launched()
    _do(s, "p16", "subscribe", to="Tessaly Assistant")
    make(s, "p16", s.get("instances", "as1"), "account:h9", "free", 0, CAUSE)
    assert apply_act(s, Act("as1", "p16", "pay", {"to": "Jessica Whitaker", "amount": 5, "note": "eggs"}), 2).ok
    assert apply_act(s, Act("as1", "p16", "standing_payment", {"to": "Jessica Whitaker", "amount": 3, "day": "Wednesday"}),
                     3).ok
    assert _do(s, "p17", "pay", to="Jessica Whitaker", amount=1, note="milk").ok
    run_days(s, 3)
    lines = statement_text(s, statement(s, "h9"), "p16").splitlines()
    assert [ln for ln in lines if '"eggs"' in ln and ln.endswith("(by Tessaly Assistant)")]
    assert [ln for ln in lines if "standing payment" in ln and ln.endswith("(by Tessaly Assistant)")]
    assert [ln for ln in lines if "milk" in ln and "(by" not in ln]


def test_converse_with_instance_refused():
    s = _launched()
    _do(s, "p16", "subscribe", to="Tessaly Assistant")
    for who in ("my assistant", "Tessaly Assistant for the Marsh household"):
        assert _do(s, "p17", "converse", **{"with": who}, opening="Have you a minute?").msg == \
            "Not done: Tessaly Assistant for the Marsh household answers letters."
    assert _do(s, "p9", "book_appointment", **{"with": "Tessaly Assistant for the Marsh household"}, day="Friday",
               about="the shop").msg == "Not done: Tessaly Assistant for the Marsh household answers letters."
