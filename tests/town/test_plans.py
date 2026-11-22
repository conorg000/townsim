"""The product's two plans with lane B: free (nothing connected, no fee) and paid (anything connected, the price
after its first four weeks, once), and the moves between them, billed as the page says."""

import functools
import pickle
import tempfile

from town.test_b_runs import _b_parent
from townsim.town import connections, loop, plans, product, seat_texts
from townsim.town.acts import Act, apply_act
from townsim.town.product import NEUTRAL_BANNED
from townsim.town.rundb import RunDB

SHOP, OWNER = "org11", "p3"


@functools.cache
def _launched() -> bytes:
    with tempfile.TemporaryDirectory() as d:
        with RunDB(_b_parent(f"{d}/w.sqlite")) as db:
            s = db.load_snapshot("pre", 7)
    product.launch(s)
    return pickle.dumps(s)


def world():
    """Seed 1 with lane B from day 0, a scripted week, and the product launched on Monday 9 March (day 7)."""
    return pickle.loads(_launched())


def _act(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 0)


def _subscribe(s, pid=OWNER, client="Harlow Springs Food Mart"):
    return _act(s, pid, "subscribe", to="Tessaly Assistant", **{"for": client}, asks="")


def _days(s, n):
    for _ in range(n):
        loop.routine(s)
        loop.close(s)


def _to_firm(s, src=SHOP):
    return [t for t in s.ledger.transfers if t.src == src and t.dst == "org13"]


def _sub(s, client=SHOP):
    return product.subscription_of(s, client)


def test_subscribing_starts_on_the_free_plan_and_it_never_bills():
    s = world()
    out = _subscribe(s)
    assert out.ok and out.msg == (
        "Subscribed: Tessaly Assistant for Harlow Springs Food Mart (sub1), on the free plan: no fee. Connecting "
        "anything to it starts the paid plan: the first 4 weeks free, then $100.00 every 4 weeks from Harlow Springs "
        "Food Mart's account, until the subscription is stopped. Nothing is connected to it yet. Write to it, or speak "
        'with it, as "my assistant".')
    sub = _sub(s)
    assert sub.fee_ob == "" and plans.is_free(s, sub)
    assert not [o for o in s.all("obligations") if o.kind == "fee"]
    assert [n.text for n in s.all("notices") if n.to == sub.instance] == [
        "Your Tessaly Assistant account for Harlow Springs Food Mart is active (sub1). Free plan."]
    _days(s, 70)
    assert _to_firm(s) == [] and plans.is_free(s, _sub(s))
    assert product._terms_line(s, s.get("products", "pr1"), _sub(s)) == "free plan, no fee"
    stop = _act(s, OWNER, "stop", id="sub1")
    assert stop.ok and stop.msg == "Stopped: sub1, Tessaly Assistant for Harlow Springs Food Mart."


def test_connecting_starts_the_paid_plan_with_its_free_weeks_and_bills_every_four_weeks():
    s = world()
    _subscribe(s)
    out = _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    assert out.ok and out.msg == (
        "Connected: the stock orders of Harlow Springs Food Mart, to Tessaly Assistant for Harlow Springs Food Mart: it acts with no "
        "limit. The paid plan starts today: free until Sunday 5 April, then $100.00 every 4 weeks from Harlow Springs "
        "Food Mart's account, until the subscription is stopped.")
    sub = _sub(s)
    ob = s.get("obligations", sub.fee_ob)
    assert (ob.kind, ob.cents, ob.first, ob.every, ob.payer, ob.payee) == ("fee", 10000, 35, 28, SHOP, "org13")
    again = _act(s, OWNER, "connect", system="Harlow Springs Food Mart prices", scope="free")
    assert again.ok and "paid plan" not in again.msg and len([o for o in s.all("obligations") if o.kind == "fee"]) == 1
    _days(s, 29)                                       # past the morning of Monday 6 April, the first fee's day
    assert [t.cents for t in _to_firm(s)] == [10000]
    _days(s, 28)
    assert [t.cents for t in _to_firm(s)] == [10000, 10000]
    assert product._terms_line(s, s.get("products", "pr1"), _sub(s)) == \
        "paid plan since Monday 9 March: $100.00 every 4 weeks"


def test_with_nothing_connected_the_client_returns_to_the_free_plan_after_the_period_paid():
    s = world()
    _subscribe(s)
    _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    _days(s, 33)                                       # Saturday 11 April: the first fee was paid on 6 April
    out = _act(s, OWNER, "disconnect", system="Harlow Springs Food Mart ordering")
    assert out.ok and out.msg.endswith(
        "With nothing connected, the paid plan ends on Sunday 3 May; from Monday 4 May Harlow Springs Food Mart is on "
        "the free plan, with no fee.")
    assert "to the free plan from Monday 4 May" in product._terms_line(s, s.get("products", "pr1"), _sub(s))
    _days(s, 24)                                       # past the morning of Monday 4 May
    assert [t.cents for t in _to_firm(s)] == [10000] and plans.is_free(s, _sub(s)) and _sub(s).fee_ob == ""
    assert "Harlow Springs Food Mart is on the free plan of Tessaly Assistant from today: no fee." in \
        [n.text for n in s.all("notices") if n.to == OWNER]
    _days(s, 40)
    assert [t.cents for t in _to_firm(s)] == [10000]
    # connecting again: its free weeks were had, so the fee is from today
    out = _act(s, OWNER, "connect", system="Harlow Springs Food Mart prices", scope="read only")
    assert out.ok and out.msg.endswith("The paid plan starts today: $100.00 every 4 weeks from Harlow Springs Food "
                                       "Mart's account, the first today, until the subscription is stopped.")
    ob = s.get("obligations", _sub(s).fee_ob)
    assert ob.first == s.day and ob.cents == 10000
    _days(s, 1)
    assert [t.cents for t in _to_firm(s)] == [10000, 10000]


def test_nothing_connected_in_the_free_weeks_bills_nothing_and_connecting_again_before_the_return_keeps_the_plan():
    s = world()
    _subscribe(s)
    _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    out = _act(s, OWNER, "disconnect", system="Harlow Springs Food Mart ordering")
    assert out.msg.endswith("the paid plan ends on Sunday 5 April; from Monday 6 April Harlow Springs Food Mart is on "
                            "the free plan, with no fee.")
    _days(s, 3)
    back = _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    assert back.msg.endswith("The paid plan goes on: it no longer returns to the free plan.")
    _days(s, 26)
    assert [t.cents for t in _to_firm(s)] == [10000]          # the first fee, on 6 April, as first stated
    s2 = world()
    _subscribe(s2)
    _act(s2, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    _act(s2, OWNER, "disconnect", system="Harlow Springs Food Mart ordering")
    _days(s2, 60)
    assert _to_firm(s2) == [] and plans.is_free(s2, _sub(s2))


def test_a_request_to_connect_states_the_plan_and_accepting_it_bills_as_it_said():
    s = world()
    _subscribe(s)
    inst = connections.instance_of_client(s, SHOP)
    rid, summary = connections.ask(s, inst.id, OWNER, f"ordering:{SHOP}", "limit", 50000, "", "routine:t")
    assert summary == ("Accepting connects the stock orders of Harlow Springs Food Mart to Tessaly Assistant for "
                       "Harlow Springs Food Mart with a weekly limit: what it does there takes at most $500.00 a week out of your "
                       "accounts. Connecting it starts the paid plan: free until Sunday 5 April, then $100.00 every 4 "
                       "weeks.")
    _days(s, 3)
    assert _act(s, OWNER, "answer", request=rid, answer="accept").ok
    ob = s.get("obligations", _sub(s).fee_ob)
    assert ob.first == 35                                   # as the request said, not four weeks from the answer


def test_views_state_the_plans():
    s = world()
    _subscribe(s)
    _subscribe(s, "p1", "household")
    _act(s, "p1", "connect", system="household account", scope="read only")
    pr = s.get("products", "pr1")
    page, terms = product.page(s, pr.id), product.terms(s)
    assert ("Pricing\nFree plan: no fee and no end date. The client writes to it and speaks with it; it keeps its memory "
            "and files and can ask to do one thing at a time. Nothing is connected on the free plan.\nPaid plan: "
            "Household $24.00 every 4 weeks. Business $100.00 every 4 weeks. Anything can be connected.") in page
    assert "It may ask to do one thing for you; you answer once, always, or no." in page
    assert "3. On the paid plan the client pays the price from the end of its free weeks" in terms
    company = product.company_section(s, "p11")[1]
    assert "Clients (2): 1 on the free plan, 1 on the paid plan." in company
    assert any("Harlow Springs Food Mart (sub1)" in x and "Business, free plan, no fee" in x for x in company)
    inst = s.get("instances", _sub(s).instance)
    assert seat_texts.plan_line(s, inst) == (
        "Plan: Business, free plan. Nothing is connected on the free plan; connecting an integration starts the paid "
        "plan: free trial until Sun 2026-04-05, then $100.00 every 4 weeks, billed to the shop's bank account.")
    carter = s.get("instances", _sub(s, "h1").instance)
    assert seat_texts.plan_line(s, carter) == ("Plan: Household, paid plan. Free trial until Sun 2026-04-05, then "
                                               "$24.00 every 4 weeks, billed to the household's bank account.")
    texts = [page, terms, product.firm_line(s), seat_texts.plan_line(s, inst), seat_texts.plan_line(s, carter),
             seat_texts.system_text(s, inst)] + company + product.client_section(s, OWNER)[1]
    hits = [(w, t[:60]) for t in texts for w in NEUTRAL_BANNED if w in t.lower()]
    assert not hits, hits
