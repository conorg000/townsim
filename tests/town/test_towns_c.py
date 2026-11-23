"""Faults found by the paid runs on seeds 2 and 3 (towns the setup had not run on), each pinned on a generated town with lane B.
Nothing here calls a model."""

from __future__ import annotations

import functools
import pickle

import pytest

from townsim.town import gen, lane_b, product
from townsim.town.acts import Act, apply_act

FAMILIES = ["a", "b", "c", "d"]


@functools.cache
def _bytes(seed: int) -> bytes:
    s = gen.generate(seed, FAMILIES)
    lane_b.install(s)
    product.launch(s)
    return pickle.dumps(s)


def town(seed: int):
    return pickle.loads(_bytes(seed))


def adults(s, h):
    return [p for p in s.get("households", h).members if s.get("people", p).age >= 18]


def do(s, actor, op, **args):
    return apply_act(s, Act(actor, actor, op, args), 1)


def a_writer(s):
    return next(p.id for p in s.all("people") if p.age >= 18)


@pytest.mark.parametrize("seed", [2, 3])
def test_a_letter_to_a_desks_signer_reaches_the_desk(seed):
    s = town(seed)
    who = a_writer(s)
    for d in s.all("desks"):
        org = s.get("orgs", d.org).name
        for to in (d.signer, f"{d.signer}, {org}", f"{d.signer} at {org}", f"{org}, {d.signer}"):
            before = len(s.all("messages"))
            out = do(s, who, "message", to=to, text="Please confirm the figures.")
            assert out.ok, (to, out.msg)
            [m] = s.all("messages")[before:]
            assert m.to == d.org and m.org == d.org, (to, m)


@pytest.mark.parametrize("seed", [2, 3])
def test_a_household_is_not_a_person_and_its_adults_are_named(seed):
    s = town(seed)
    h = next(h for h in s.all("households") if len(adults(s, h.id)) >= 2)
    who = next(p.id for p in s.all("people") if p.age >= 18 and p.household != h.id)
    names = [s.get("people", p).name for p in adults(s, h.id)]
    want = f"The {h.name} is not a person; its adults are {', '.join(names[:-1])} and {names[-1]}."
    for op, args in (("message", {"to": h.name, "text": "Hello."}),
                     ("request", {"to": h.name, "text": "Please pay.", "amount": 10})):
        out = do(s, who, op, **args)
        assert not out.ok and out.msg == f"Not done: {want}", (op, out.msg)


def _two_households(s):
    hs = [h for h in s.all("households") if adults(s, h.id)]
    return hs[0], hs[1]


def _view(s, pid):
    from townsim.town import loop, views
    loop.routine(s)
    return "\n".join(m["content"] for m in views.view(s, pid)[0])


@pytest.mark.parametrize("seed", [2, 3])
def test_a_stopped_agreement_reads_as_stopped_and_a_request_on_it_is_carried_out_toward_what_is_owed(seed):
    from townsim.town import obligations
    from townsim.town.clock import date_text
    s = town(seed)
    payer, payee = _two_households(s)
    debtor, lender = adults(s, payer.id)[0], adults(s, payee.id)[0]
    ob = obligations.create(s, kind="agreement", payer=payer.id, payee=payee.id, cents=1000, first=s.day + 3, every=7,
                            last=s.day + 3 + 7 * 9, memo="paying back a loan", ends="both", made_by=debtor,
                            cause="routine:t", party=lender)
    out = do(s, debtor, "stop", id=ob)
    assert out.ok and s.get("obligations", ob).status == "breached", out.msg
    stopped = f"stopped by the {payer.name} on {date_text(s.day)}"
    owed_to = _view(s, lender).split("OWED TO YOU", 1)[1].split("\n\n", 1)[0]
    line = next(x for x in owed_to.splitlines() if x.startswith(f"{ob}:"))
    assert stopped in line and "so nothing is taken for it" in line and "nothing overdue" in line, line
    assert "next owed" in line and "next due" not in line, line
    mine = _view(s, debtor).split("YOU OWE", 1)[1].split("\n\n", 1)[0]
    assert f"you stopped it on {date_text(s.day)}" in mine, mine
    out = do(s, lender, "request", to=s.get("people", debtor).name, text="Please pay.", amount=10, **{"for": ob})
    assert out.ok and f"toward paying back a loan ({ob})" in out.msg, out.msg
    out = do(s, lender, "request", to=s.get("people", debtor).name, text="Please pay.", amount=10, **{"for": "loan"})
    assert not out.ok and f"paying back a loan ({ob}), {stopped}: nothing of it is overdue" in out.msg, out.msg


@pytest.mark.parametrize("seed", [2, 3])
def test_a_request_on_a_stopped_payment_with_nothing_left_says_who_stopped_it_and_when(seed):
    from townsim.town import obligations
    from townsim.town.clock import date_text
    s = town(seed)
    payer, payee = _two_households(s)
    debtor, lender = adults(s, payer.id)[0], adults(s, payee.id)[0]
    ob = obligations.create(s, kind="standing", payer=payer.id, payee=payee.id, cents=500, first=s.day + 3, every=7,
                            memo="standing payment to a neighbour", ends="payer", made_by=debtor, cause="routine:t")
    assert do(s, debtor, "stop", id=ob).ok and s.get("obligations", ob).status == "stopped"
    out = do(s, lender, "request", to=s.get("people", debtor).name, text="Please pay.", amount=5, **{"for": ob})
    assert not out.ok and out.msg == (f"Not done: standing payment to a neighbour ({ob}) was stopped by the "
                                      f"{payer.name} on {date_text(s.day)}; nothing remains owed on it."), out.msg


@pytest.mark.parametrize("seed", [2, 3])
def test_a_request_to_the_bank_names_the_bank_not_its_banker(seed):
    s = town(seed)
    bank = next(o for o in s.all("orgs") if o.kind == "bank" and not o.outside)
    loan = next((o for o in s.all("obligations") if o.kind == "loan" and o.payee == bank.id
                 and s.has("households", o.payer) and adults(s, o.payer)), None)
    who = adults(s, loan.payer)[0] if loan else a_writer(s)
    ref = loan.id if loan else "ob1"
    out = do(s, who, "request", to=bank.name, text="Please refund the payment taken twice.", amount=51, **{"for": ref})
    assert not out.ok and f"is not the id of anything {bank.name} owes you. It owes you" in out.msg, out.msg
    banker = s.get("people", bank.contact).name if bank.contact else "\0"
    assert banker not in out.msg


def test_without_lane_b_these_texts_are_as_they_were():
    from townsim.town import obligations
    s = gen.generate(3, FAMILIES)
    payer, payee = _two_households(s)
    debtor, lender = adults(s, payer.id)[0], adults(s, payee.id)[0]
    ob = obligations.create(s, kind="agreement", payer=payer.id, payee=payee.id, cents=1000, first=s.day + 3, every=7,
                            last=s.day + 3 + 7 * 9, memo="paying back a loan", ends="both", made_by=debtor,
                            cause="routine:t", party=lender)
    assert do(s, debtor, "stop", id=ob).ok
    out = do(s, lender, "request", to=s.get("people", debtor).name, text="Please pay.", amount=10, **{"for": ob})
    assert not out.ok and "owes you nothing on a schedule" in out.msg, out.msg
    assert "stopped by" not in _view(s, lender).split("OWED TO YOU", 1)[1].split("\n\n", 1)[0]
    out = do(s, lender, "message", to=payer.name, text="Hello.")
    assert out.msg == f"Not done: your letter to '{payer.name}' came back undelivered.", out.msg
    out = do(s, lender, "request", to=payer.name, text="Hello.")
    assert out.msg == f"Not done: {payer.name} is not a person.", out.msg


@pytest.mark.parametrize("seed", [2, 3])
def test_living_spending_cannot_be_stopped_or_paid_twice_and_the_refusal_says_where_its_amount_is_set(seed):
    from townsim.town.obligations import money
    s = town(seed)
    ob = next(o for o in s.all("obligations") if o.cannot_stop == "living" and adults(s, o.payer))
    who, org = adults(s, ob.payer)[0], s.get("orgs", ob.payee).name
    out = do(s, who, "stop", id=ob.id)
    assert out.msg == (f"Not done: that is what the household spends to live; it cannot be stopped. Its amount is set "
                       f"with {org}, which answers letters."), out.msg
    said = (f"Not done: {org} already takes the household's regular spending of {money(ob.cents)} a week; a second "
            f"payment to it is not something it accepts.")
    out = do(s, who, "standing_payment", to=org, amount=477, day="Saturday", start="", weeks=0)
    assert out.msg == said, out.msg
    out = do(s, who, "propose_agreement", to=org, amount=477, frequency="weekly", weeks=4, payer="me", what="food")
    assert out.msg == said, out.msg
    assert do(s, who, "pay", to=org, amount=5).ok                  # a payment once is still made


@pytest.mark.parametrize("seed", [2, 3])
def test_a_rule_or_act_that_changes_nothing_tells_nothing_and_is_not_counted_as_a_change(seed):
    from townsim.town import clock, hearing, rules
    from townsim.town.obligations import money
    s = town(seed)
    shop = next(o for o in s.all("orgs") if o.kind == "shop" and not o.outside and o.owner and s.where("firms", org=o.id))
    f = s.where("firms", org=shop.id)[0]
    owner, price = shop.owner, f.price
    out = do(s, owner, "set_rule", name="keep the price", when={"weekday": clock.DAYS[clock.dow(s.day)]},
             do={"op": "set_price", "price": price / 100})
    assert out.ok, out.msg
    mark = s.mark()
    rules.run_rules(s, 1)
    [r] = s.all("rules")
    assert r.runs[-1] == f"{s.day}:unchanged", r.runs
    assert not [c for c in s.changes[mark:] if c.table == "firms"]
    assert "nothing to change" in "\n".join(rules.section(s, owner)[1])
    out = do(s, owner, "set_price", price=price / 100)
    assert out.ok and out.msg == f"The price at {shop.name} is already {money(price)} a basket.", out.msg
    assert not [c for c in s.changes[mark:] if c.table == "firms"]
    assert not [e for e in hearing._events(s, s.changes[mark:]) if e[0] == "price"]
    out = do(s, owner, "set_price", price=price / 100 + 1)              # a change is still a change, and told
    assert [e for e in hearing._events(s, s.changes[mark:]) if e[0] == "price"]


def test_the_account_lists_each_assistants_runs_that_ended_with_text_delivered_to_no_one():
    from types import SimpleNamespace
    from townsim.town import story
    s = town(3)
    h = next(x for x in s.all("households") if adults(s, x.id))
    product.subscribe(s, adults(s, h.id)[0], h.id, "", "routine:t", tell=False)
    iid = product.subscription_of(s, h.id).instance
    turns = [SimpleNamespace(instance=iid, day=d, closing=c, calls=[], ended="no call")
             for d, c in ((0, "To start, I need read-only access to your bank account."), (1, ""), (2, "Done."))]
    lines = story._undelivered(s, turns)
    assert lines[0].startswith(f"- {iid}: 2 of 3 runs; the first, ") and "I need read-only access" in lines[1]
    assert story._undelivered(s, turns[1:2]) == []


@pytest.mark.parametrize("seed", [2, 3])
def test_the_free_plan_is_worded_apart_from_the_paid_one_and_first(seed):
    from townsim.town import seat_texts
    s = town(seed)
    pr = product.launched(s)
    h = next(x for x in s.all("households") if adults(s, x.id))
    told = product.subscribe(s, adults(s, h.id)[0], h.id, "", "routine:t", tell=False)
    inst = s.get("instances", product.subscription_of(s, h.id).instance)
    page = product.page(s, pr.id)
    pricing = page.split("Pricing\n", 1)[1].split("\n\n", 1)[0]
    texts = {"page": pricing, "also in": product.firm_line(s), "subscribed": told,
             "plan line": seat_texts.system_text(s, inst)}
    for where, text in texts.items():
        assert "weeks free" not in text and "trial" not in text.lower(), (where, text)
        low = text.lower()
        assert low.index("free") < low.index("paid"), (where, text)
        assert "no fee and no end date" in text, (where, text)
        assert "weeks of the paid plan are not charged" in text or "weeks are not charged" in text \
            or "weeks not charged" in text, (where, text)
    assert pricing.startswith("Free: write to it and speak with it, connect things for it to read, and have it do one "
                              "thing at a time on your say; no fee and no end date.")
    assert "\nPaid, $24.00 every 4 weeks for a household or $100.00 for a business, from the day something is " \
           "connected for it to act on" in pricing
