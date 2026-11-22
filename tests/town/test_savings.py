"""Putting money by: every household has savings at the town's bank, which only a move between
one's own accounts reaches and nothing owed is ever taken from."""

import functools
import pickle

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import gen, obligations as O
from townsim.town.acts import Act, apply_act, forms, has_savings, savings_of
from townsim.town.entities import Request
from townsim.town.views import person_view

from .helpers import generated, scripted_weeks


@functools.cache
def _frozen() -> bytes:
    return pickle.dumps(generated(1))


def place():
    return pickle.loads(_frozen())


def do(s, pid, op, **args):
    return apply_act(s, Act(pid, pid, op, args), 0)


def couple(s):
    """A household of two adults neither of whom owns a business, and the two."""
    for h in s.all("households"):
        adults = [p for p in h.members if s.get("people", p).age >= 18
                  and not any(o.owner == p for o in s.all("orgs"))]
        if len(adults) >= 2 and len(h.members) == len(adults):
            return h, adults[0], adults[1]
    raise AssertionError("no couple")


def test_every_household_has_savings_some_start_with_money_and_check_asserts_it():
    held = []
    for seed in (1, 2, 3, 4, 5):
        s = generated(seed)
        assert all(has_savings(s, h.id) for h in s.all("households"))
        held += [s.balance(savings_of(h.id)) for h in s.all("households")]
    assert any(c > 0 for c in held) and held.count(0) > len(held) / 4
    s = generated(1)
    h = next(h for h in s.all("households") if not s.balance(savings_of(h.id)))
    del s.ledger.accounts[savings_of(h.id)]
    assert gen.check(s) == [f"{h.name} has no savings account"]


def test_a_move_to_savings_shows_on_the_shared_statement_and_in_both_views():
    s = place()
    h, me, partner = couple(s)
    before = s.balance(savings_of(h.id))
    out = do(s, me, "move_money", **{"from": "household", "to": "savings", "amount": 50})
    assert out.ok and out.msg == f"Moved $50.00 from your household to your savings. Your savings hold {O.money(before + 5000)}."
    mine, theirs = (person_view(s, p)[1]["content"].split("\n") for p in (me, partner))
    assert f"Savings: {O.money(before + 5000)}" in mine and f"Savings: {O.money(before + 5000)}" in theirs
    assert any(x.endswith("-$50.00  savings  moved between your own accounts") for x in mine)
    name = s.get("people", me).name
    assert any(x.endswith(f"-$50.00  {name} moved $50.00 to savings") for x in theirs)
    back = do(s, partner, "move_money", **{"from": "savings", "to": "household", "amount": 20})
    assert back.ok and s.balance(savings_of(h.id)) == before + 3000


def test_savings_pay_nothing_and_are_never_collected_from():
    s = place()
    h, me, _ = couple(s)
    if not s.balance(savings_of(h.id)):
        s.transfer(gen.OPENING, savings_of(h.id), 500_000, "opening balance", "routine:t")
    friend = next(p.name for p in s.all("people") if p.household != h.id)
    assert do(s, me, "pay", to=friend, amount=5, **{"from": "savings"}).msg == \
        "Not done: money leaves savings only by a move to another of your own accounts."
    assert do(s, me, "read", what="account", **{"from": "savings"}).ok
    s.transfer(h.id, gen.OPENING, s.balance(h.id), "spent", "routine:t")
    held = s.balance(savings_of(h.id))
    for _ in range(8):
        from townsim.town.loop import routine
        routine(s)
        s.advance_day()
    assert s.balance(savings_of(h.id)) == held
    told = [n.text for n in s.where("notices", to=me) if "could not be taken" in n.text]
    assert told and all(t.endswith(f"Its savings held {O.money(held)}.") for t in told)


def test_refusals_name_the_accounts_that_exist():
    s = place()
    h, me, partner = couple(s)
    surname = s.get("people", me).name.split()[-1]
    named = f"{surname} household savings"
    assert do(s, me, "pay", to=named, amount=5).msg == \
        f"Not done: there is no account called '{named}'. Yours are: household, savings."
    assert do(s, me, "move_money", **{"from": "household", "to": named, "amount": 5}).msg == \
        f"Not done: there is no account called '{named}'. Yours are: household, savings."
    assert do(s, me, "pay", to=s.get("people", partner).name, amount=5).msg == (
        f"Not done: {s.get('people', partner).name} is in your own household; the household has one account and "
        f"its savings.")
    assert do(s, me, "pay", to="savings", amount=5).msg == "Not done: that is your own household's savings."


def test_the_forms_name_savings_and_the_business_and_the_banker_sees_savings():
    s = place()
    h, me, _ = couple(s)
    mine = forms(s, me)
    assert '{"op": "move_money", "from": "household" | "savings", "to": "household" | "savings", "amount": <dollars>}' \
        in mine
    assert any(f.startswith('{"op": "read"') and f.endswith('"from": "household" | "savings"}') for f in mine)
    assert not any(f.startswith('{"op": "pay"') and '"from"' in f for f in mine)
    owner = next(o for o in s.all("orgs") if o.kind == "shop")
    assert any(f.startswith('{"op": "move_money"') and f'"household" | "savings" | "{owner.name}"' in f
               for f in forms(s, owner.owner))
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    s.add("requests", Request("rq90", 0, me, bank.contact, "", 0, 9, kind="loan", summary="asks the bank",
                              payer=h.id, payee=bank.id, pay=10_000, first=11, times=4), "routine:t")
    user = person_view(s, bank.contact)[1]["content"]
    assert f"account holds {O.money(s.balance(h.id))}, and its savings {O.money(s.balance(savings_of(h.id)))}." in user


def test_the_scripted_weeks_use_savings():
    s, _, _ = scripted_weeks(1)
    moved = [x for x in s.ledger.transfers if ":move_money:" in x.cause and "savings:" in (x.src + x.dst)]
    assert moved
