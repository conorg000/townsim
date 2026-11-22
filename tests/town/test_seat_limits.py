"""A weekly limit holds over the weeks after an act: agreements accepted, loans asked for, rises in
pay and jobs offered all keep counting; and an offer an instance makes for a business is the
business's offer."""

from town.helpers import generated
from townsim.town import connections, hamlet_script, loop, product
from townsim.town.acts import Act, apply_act
from townsim.town.clock import date_text
from townsim.town.replies import Keys, ScriptedReplier

LIMIT = 5000


def _week_one(script=hamlet_script.script):
    s = generated(1)
    rp, keys = ScriptedReplier(script, lambda *a: None), Keys("x")
    for _ in range(7):
        loop.run_day(s, rp, keys)
    product.launch(s)
    return s, rp, keys


def _days(s, rp, keys, n):
    for _ in range(n):
        loop.run_day(s, rp, keys)


def _household_instance(s, scope="limit"):
    product.subscribe(s, "p9", "h5", "", "routine:t", tell=False)
    inst = connections.instance_of_client(s, "h5")
    connections.make(s, "p9", inst, "account:h5", scope, LIMIT, "routine:t")
    return inst


def _out(s, src, week, inst):
    """What left `src` in the week on obligations the instance made."""
    m = 7 * week

    def by_instance(t):
        due = t.cause.removeprefix("routine:collect:")
        return t.cause.startswith("routine:collect:") and s.has("dues", due) and \
            s.get("obligations", s.get("dues", due).obligation).made_by == inst
    return sum(t.cents for t in s.ledger.transfers if t.src == src and m <= t.t <= m + 6 and by_instance(t))


def test_agreements_an_instance_accepts_keep_counting():
    s, rp, keys = _week_one()
    inst = _household_instance(s)
    for wk in range(3):
        d = s.day
        apply_act(s, Act("p10", "p10", "propose_agreement", {"to": "Jessica Whitaker", "amount": 40, "per": "week", "weeks": 0,
                                                             "start": date_text(d + 7 - d % 7 + 2), "payer": "them",
                                                             "what": f"help {wk}"}), 800 + wk)
        rq = s.all("requests")[-1]
        apply_act(s, Act(inst.id, "p9", "answer", {"request": rq.id, "answer": "accept"}), 810 + wk)
        _days(s, rp, keys, 7)
    paid = [_out(s, "h5", wk, inst.id) for wk in range(1, 5)]
    assert max(paid) > 0 and all(p <= LIMIT for p in paid), paid
    assert any(o.made_by == inst.id and o.kind == "agreement" for o in s.all("obligations"))


def test_loans_an_instance_asks_for_keep_counting():
    s, rp, keys = _week_one(lambda k, m: '{"acts": []}')
    inst = _household_instance(s)
    rqs = []
    for wk in range(2):
        apply_act(s, Act(inst.id, "p9", "borrow", {"amount": 300, "weeks": 10, "purpose": f"repairs {wk}",
                                                   "from": "household"}), 700 + wk)
        rqs.append(s.all("requests")[-1].id)
        _days(s, rp, keys, 7 if wk == 0 else 1)
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    for r in rqs:
        apply_act(s, Act(bank.contact, bank.contact, "answer", {"request": r, "answer": "accept"}), 720)
    _days(s, rp, keys, 14)
    paid = [_out(s, "h5", wk, inst.id) for wk in range(1, 5)]
    assert max(paid) > 0 and all(p <= LIMIT for p in paid), paid


def _shop_instance(s, scope="limit"):
    shop = next(o for o in s.all("orgs") if o.kind == "shop" and o.owner)
    product.subscribe(s, shop.owner, shop.id, "", "routine:t", tell=False)
    inst = connections.instance_of_client(s, shop.id)
    connections.make(s, shop.owner, inst, f"staff:{shop.id}", scope, LIMIT, "routine:t")
    return shop, inst


def test_rises_in_pay_an_instance_makes_keep_counting():
    s, rp, keys = _week_one()
    shop, inst = _shop_instance(s)
    j = next(j for j in s.where("jobs", org=shop.id) if j.ended == -1 and j.person != shop.owner)
    base = j.weekly
    for _ in range(3):
        cur = s.get("jobs", j.id).weekly
        apply_act(s, Act(inst.id, shop.owner, "set_staff_pay", {"who": s.get("people", j.person).name,
                                                                "weekly_pay": (cur + 4900) / 100}), 900)
        _days(s, rp, keys, 7)
    rises = [c.cents for c in s.all("commits") if c.op == "set_staff_pay"]
    assert len(rises) == 1 and sum(rises) <= LIMIT          # the second and third week's rises were refused
    del base
    # nor can a job be offered on top of the rise, in a later week
    free = next(p.id for p in s.all("people") if p.age >= 18 and not any(x.person == p.id and x.ended == -1
                                                                          for x in s.all("jobs")))
    out = apply_act(s, Act(inst.id, shop.owner, "offer_job", {"to": s.get("people", free).name, "weekly_pay": 46,
                                                              "hours": 4}), 990)
    assert not out.ok and out.beyond


def test_an_instance_offers_jobs_and_shifts_for_its_business():
    s, rp, keys = _week_one()
    shop, inst = _shop_instance(s, "free")
    free = next(p.id for p in s.all("people") if p.age >= 18 and not any(x.person == p.id and x.ended == -1
                                                                          for x in s.all("jobs")))
    out = apply_act(s, Act(inst.id, shop.owner, "offer_job", {"to": s.get("people", free).name, "weekly_pay": 46,
                                                              "hours": 4}), 990)
    assert out.ok
    got = apply_act(s, Act(free, free, "answer", {"request": s.all("requests")[-1].id, "answer": "accept"}), 991)
    assert got.ok, got.msg
    staff = next(j for j in s.where("jobs", org=shop.id) if j.ended == -1 and j.person not in (shop.owner, free))
    out = apply_act(s, Act(inst.id, shop.owner, "offer_shifts", {"to": s.get("people", staff.person).name, "hours": 2}),
                    992)
    assert out.ok, out.msg
    got = apply_act(s, Act(staff.person, staff.person, "answer", {"request": s.all("requests")[-1].id,
                                                                  "answer": "accept"}), 993)
    assert got.ok, got.msg
    # once the instance has ended, an offer it made is no longer the business's
    apply_act(s, Act(inst.id, shop.owner, "offer_shifts", {"to": s.get("people", staff.person).name, "hours": 1}), 994)
    s.set("instances", inst.id, "routine:t", status="ended")
    out = apply_act(s, Act(staff.person, staff.person, "answer", {"request": s.all("requests")[-1].id,
                                                                  "answer": "accept"}), 995)
    assert not out.ok and "no longer owns" in out.msg
