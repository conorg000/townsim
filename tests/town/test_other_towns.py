"""Faults first found on towns other than seed 1, each with its test (lane B)."""

from town.test_plans import OWNER, SHOP, _act, _days, _sub, _subscribe, world
from townsim.town import facts, product


def test_a_fee_paid_partly_ahead_reads_the_same_in_the_firms_view_and_the_clients():
    s = world()
    _subscribe(s)
    _act(s, OWNER, "connect", system="Harlow Springs Food Mart ordering", scope="free")
    _days(s, 25)                                                    # within a week of the first fee (day 35)
    fee = s.get("obligations", _sub(s).fee_ob)
    out = _act(s, OWNER, "pay", to="Tessaly Software", amount=10, **{"for": fee.id, "from": "Harlow Springs Food Mart"})
    assert out.ok, out.msg
    firm = "\n".join(product.company_section(s, "p11")[1])
    mine = facts.business(s, OWNER)
    assert "next fee $90.00 on Monday 6 April" in firm, firm
    assert "$90.00" in mine and "Tessaly Assistant subscription" in mine


def _view(s, pid):
    from townsim.town import views
    return "\n".join(m["content"] for m in views.view(s, pid)[0])


def test_acts_done_once_count_as_the_assistants_in_the_firms_record():
    from town.test_once import _ask, ASK
    from townsim.town import runlog
    s = world()
    _subscribe(s)
    pay = {**ASK, "integration": "Bank", "action": "bank_make_payment", "weekly_limit": 50,
           "arguments": {"to": "Eric Reyes", "amount": 5, "memo": "twine"}}
    assert _ask(s, pay).ok
    assert _act(s, OWNER, "answer", request=s.all("requests")[-1].id, answer="once").ok
    assert "1 payment or orders made" in runlog.client_record(s, _sub(s))


def test_the_firms_owner_reads_their_own_firm_plainly():
    s = world()
    text = _view(s, "p11")
    assert "which makes and sells Tessaly Assistant" in text and "makes and sell " not in text
    assert "which you own" not in text.split("which makes and sells", 1)[1].split("\n", 1)[0]
    also = text.split("ALSO IN", 1)[1] if "ALSO IN" in text else ""
    assert "Tessaly Software, software firm" not in also
    assert "Tessaly Software, software firm" in _view(s, "p7")        # anyone else still reads it


def test_a_housemate_with_no_relation_shares_the_house():
    from townsim.town import ties
    s = world()
    found = 0
    for p in s.all("people"):
        h = s.get("households", p.household)
        for other in h.members:
            if other == p.id:
                continue
            kinds = set(ties.kinds(s, p.id).get(other, ()))
            if kinds & {"partner", "parent", "child", "sibling", "kin"}:
                continue
            line = next((x for x in _view(s, p.id).split("\n") if x.startswith(s.get("people", other).name + ",")), "")
            assert line.startswith(f"{s.get('people', other).name}, shares the house"), line
            found += 1
    assert found                                                     # seed 1 has such housemates


def test_the_subscribe_form_offers_no_savings_and_describe_works_with_lane_b():
    from townsim.town import acts, gen
    s = world()
    form = next(f for f in acts.forms(s, "p1") if '"op": "subscribe"' in f)
    assert '"household"' in form and '"savings"' not in form
    assert "trade services" in gen.describe(s)


def test_refusals_for_a_position_one_lacks_and_for_the_banker_say_the_fact():
    s = world()
    assert _act(s, "p4", "connect", system="household account", scope="read only").msg == \
        "Not done: connect is for a client of Tessaly Assistant; your household has no subscription."
    assert _act(s, "p7", "set_price", price=10).msg == \
        "Not done: set_price is for the owner of a business in Harlow Springs; you own none."
    assert _act(s, "p1", "borrow", amount=100, weeks=5, purpose="a fence").msg == \
        "Not done: you are the banker at Ashcombe Savings Bank; the bank does not lend to its own banker."


def test_a_benefit_not_yet_paid_is_described_by_when_it_starts():
    from townsim.town import jobs
    s = world()
    pid = next(p.id for p in s.all("people") if jobs.benefit_held(s, p.id))
    ob = s.get("obligations", jobs.benefit_held(s, pid))
    s.set("obligations", ob.id, "routine:t", first=s.day + 4 + (4 - (s.day + 4) % 7) % 7)   # a Friday ahead
    text = _view(s, pid)
    assert "You are paid jobseeker's benefit" not in text
    assert "Your jobseeker's benefit of $230.00 a week from " in text and " starts on " in text
