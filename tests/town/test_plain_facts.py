"""What an owner, a banker and a household plainly know (the final review's section 6)."""

import functools
import pickle
import re

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import gen, jobs, views
from townsim.town import obligations as O
from townsim.town.acts import Act, apply_act
from townsim.town.clock import date_year
from townsim.town.facts import business, who
from townsim.town.loop import close, routine
from townsim.town.ops_deals import loan_schedule

FAMILIES = ["a", "b", "c", "d"]


@functools.cache
def _frozen(seed: int) -> bytes:
    return pickle.dumps(gen.generate(seed, FAMILIES))


def place(seed: int = 1):
    return pickle.loads(_frozen(seed))


def live(s, n):
    for _ in range(n):
        routine(s)
        close(s)


def _do(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 0)


def _owner(s):
    return next(o for o in s.all("orgs") if o.kind == "shop")


def test_an_owner_sees_the_last_delivery_the_suppliers_last_week_s_money_and_the_other_shops_prices():
    s = place(1)
    live(s, 16)                                       # Mondays' deliveries and a whole week of trade closed
    org = _owner(s)
    text = business(s, org.owner)
    f = s.where("firms", org=org.id)[0]
    delivery = [x for x in s.ledger.transfers if x.cause == f"routine:delivery:{f.id}"][-1]
    got = int(delivery.memo.split()[0])
    assert re.search(rf"Last delivery: Monday 16 March, {got} baskets at \$[\d.]+ a basket from ", text), text
    for sp in s.all("suppliers"):
        assert s.get("orgs", sp.org).name in text.split("Suppliers' prices a basket today: ")[1].split("\n")[0]
    week = text.split("Last week's money: ")[1].split("\n")[0]
    assert week.startswith("took $") and "paid out" in week and "stock $" in week
    rivals = text.split("Prices a basket in the other shops: ")[1].split("\n")[0]
    for other in s.all("firms"):
        if other.org != org.id:
            assert s.get("orgs", other.org).name in rivals
    assert " units" not in text and "customer" not in text


def test_a_supplier_that_is_not_one_is_refused_with_the_names_of_those_that_are():
    s = place(1)
    org = _owner(s)
    out = _do(s, org.owner, "order_stock", supplier="Coldbeckton", baskets=10)
    names = sorted(s.get("orgs", sp.org).name for sp in s.all("suppliers"))
    assert out.msg == f"Not done: 'Coldbeckton' is not a supplier. Yours are {names[0]} and {names[1]}."
    assert _do(s, org.owner, "order_stock", supplier=names[0], units=10).ok         # the older name still reads


def test_the_banker_sees_the_applicant_s_account_and_four_weeks_of_money():
    s = place(1)
    live(s, 10)
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    pid = next(p.id for p in s.all("people") if p.id != bank.contact and p.age >= 18)
    assert _do(s, pid, "borrow", amount=500, weeks=10, purpose="a new boiler").ok
    rq = s.all("requests")[-1]
    s.set("requests", rq.id, "routine:t", seen=False)
    text = views.person_view(s, bank.contact)[1]["content"]
    h = s.get("people", pid).household
    assert f"The {s.get('households', h).name} account holds ${s.balance(h) / 100:,.2f}." in text
    assert "has come into it and" in text and "They owe the bank nothing." in text


def test_a_household_member_sees_what_the_others_are_paid_and_when_the_household_shops():
    s = place(1)
    for p in s.all("people"):
        lines = who(s, p.id).split("\n")
        h = s.get("households", p.household)
        day = next(b.shop_day for b in s.all("buyers") if b.account == h.id)
        assert f"Your household buys its food on {('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')[day]}s." in lines
        for other in h.members:
            if other == p.id:
                continue
            line = next(x for x in lines if x.startswith(s.get("people", other).name + ", "))
            for j in jobs.held(s, person=other):
                assert f"paid ${j.weekly / 100:,.2f} a week by {s.get('orgs', j.org).name}" in line, line


def test_loan_summaries_carry_the_year_and_the_right_last_day():
    s = place(1)
    pid = next(p.id for p in s.all("people") if p.kind == "staff")
    out = _do(s, pid, "borrow", amount=3000, weeks=52, purpose="a van")
    rq = s.all("requests")[-1]
    total, weekly, final = loan_schedule(300_000, 52)
    last = rq.first + 7 * 51
    assert O.instalment_days(O.Obligation("x", "loan", "a", "b", weekly, rq.first, 7, last, "", "both", "", 0),
                             rq.first, 52)[-1] == last
    assert f"from {date_year(rq.first)}" in rq.summary and date_year(last) in rq.summary
    assert date_year(last).endswith("2027") and out.ok


def test_benefit_is_one_rate_and_a_pension_is_a_state_pension():
    assert jobs.BENEFIT_WEEKLY == 23_000
    for seed in (1, 2, 3, 4, 5):
        s = place(seed)
        for o in s.all("obligations"):
            if o.memo == jobs.BENEFIT_MEMO:
                assert o.cents == jobs.BENEFIT_WEEKLY
        for p in s.all("people"):
            assert "your pension" not in who(s, p.id)


def test_a_wage_not_yet_begun_is_not_counted_as_coming_in():
    s = place(1)
    h = s.all("households")[0].id
    before = gen.weekly_in(s, h)
    O.create(s, kind="wage", payer="outside:opening", payee=h, cents=50_000, first=s.day + 18, every=7,
             memo="wages from later", ends="both", made_by="routine", cause="routine:t")
    assert gen.weekly_in(s, h) == before
    live(s, 14)
    assert gen.weekly_in(s, h) == before + 50_000 or gen.weekly_in(s, h) >= 50_000


def test_know_how_does_not_say_what_may_be_done():
    from townsim.town.facts import KNOWHOW
    assert not any("at any time" in line for lines in KNOWHOW.values() for line in lines)
