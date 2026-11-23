"""Faults found by the thirteen-week paid run on seed 3 (`runs/d3.sqlite`, run `d3-choice`), each pinned on a
generated town with lane B. Nothing here calls a model."""

from __future__ import annotations

import functools
import pickle
import re
from datetime import date

import pytest

from townsim.town import clock, connections, gen, lane_b, loop, product, seat_ids
from townsim.town.acts import Act, apply_act

FAMILIES = ["a", "b", "c", "d"]
START = "2026-11-23"                    # the run's own start: thirteen weeks from late November into February
_DATED = re.compile(r"\b(" + "|".join(clock.DAYS) + r") (\d{1,2}) (" + "|".join(clock.MONTHS) + r") (\d{4})\b")


def adults(s, h):
    return [p for p in s.get("households", h).members if s.get("people", p).age >= 18]


def do(s, actor, op, **args):
    return apply_act(s, Act(actor, actor, op, args), 1)


def _days(s, n):
    for _ in range(n):
        loop.routine(s)
        loop.close(s)


@functools.cache
def _late_bytes(seed: int) -> bytes:
    """Seed `seed` begun on 23 November 2026 with lane B, a household subscribed with its account connected to read."""
    s = gen.generate(seed, FAMILIES, start=START)
    lane_b.install(s)
    product.launch(s)
    h = next(x for x in s.all("households") if adults(s, x.id))
    product.subscribe(s, adults(s, h.id)[0], h.id, "", "routine:t", tell=False)
    inst = s.get("instances", product.subscription_of(s, h.id).instance)
    connections.make(s, adults(s, h.id)[0], inst, f"account:{h.id}", "read", 0, "routine:t")
    return pickle.dumps((s, h.id, inst.id))


def late(seed: int):
    return pickle.loads(_late_bytes(seed))


def _real(text: str) -> list[tuple[str, int]]:
    """Every `Monday 28 December 2026` in `text`, each checked to be a day of that year on that weekday."""
    found = []
    for m in _DATED.finditer(text):
        d = date(int(m.group(4)), clock.MONTHS.index(m.group(3)) + 1, int(m.group(2)))
        assert clock.DAYS[d.weekday()] == m.group(1), m.group(0)
        found.append((m.group(0), d.year))
    return found


def _record_days(s) -> set[str]:
    """Each day an obligation's record holds (its first day and its last instalment), written with its year."""
    from townsim.town import obligations
    days = set()
    for ob in s.all("obligations"):
        for d in (ob.first, ob.last, obligations.last_instalment(ob)):
            if d is not None:
                days.add(clock.date_year(d))
    return days


def test_a_date_with_no_year_is_given_the_nearest_such_date():
    s, _, _ = late(3)
    dec28, jan4 = 35, 42                                  # Monday 28 December 2026, Monday 4 January 2027
    assert clock.date_text(dec28) == "Monday 28 December" and clock.date_text(jan4) == "Monday 4 January"
    for today in (30, 34, 35, 38, 41, 44, 49):            # before and after the turn of the year
        assert seat_ids.dated("Monday 28 December", today) == "Monday 28 December 2026", today
        assert seat_ids.dated("Monday 4 January", today) == "Monday 4 January 2027", today
        assert clock.parse_near("28 December", today) == dec28 and clock.parse_near("Monday 4 January", today) == jan4
    assert seat_ids.dated("Monday 23 November", 49) == "Monday 23 November 2026"
    assert seat_ids.dated("Friday 28 December", 44) == "Friday 28 December"      # no such day near: as written
    assert seat_ids.dated("Friday 27 March 2026", 44) == "Friday 27 March 2026"


@pytest.mark.parametrize("seed", [2, 3])
def test_across_the_turn_of_the_year_an_assistant_and_a_person_see_each_date_in_its_own_year(seed):
    from townsim.town import seat_tools as ST, views
    from townsim.town.replies import Keys, ScriptedReplier
    s, home, iid = late(seed)
    who = adults(s, home)[0]
    ctx = ST.Ctx(ScriptedReplier(lambda k, m: "{}", lambda k, m, t: None), Keys("t"))
    seen_years = set()
    for until in (36, 44):                               # Tuesday 29 December 2026, Wednesday 6 January 2027
        _days(s, until - s.day)
        h = s.hash()
        for name in ("bank_get_account", "bank_list_agreements"):
            done = ST.run_tool(s, s.get("instances", iid), name, {}, ctx, 1)
            assert done.ok, done.text
            dates = _real(done.text)
            assert dates, done.text
            seen_years |= {y for _, y in dates}
            bare = [m.group(0) for m in seat_ids._DATE.finditer(done.text)]
            assert not bare, bare                       # every date the assistant reads carries its year
        assert s.hash() == h                            # reading changes nothing
        loop.routine(s)
        text = "\n".join(m["content"] for m in views.view(s, who)[0])
        far = [d for d, y in _real(text) if y not in (2026, 2027)]
        assert set(far) <= _record_days(s), far          # a far year is a record's own: a loan's end, a lease's start
    assert {2026, 2027} <= seen_years
    # a date that has passed is named as the calendar has it, with its year when that is not this year's
    other = next(p.id for p in s.all("people") if p.age >= 18 and p.household != home)
    out = do(s, who, "book_appointment", day="Monday 4 January", **{"with": s.get("people", other).name},
             about="the bills")
    assert out.msg == "Not done: Monday 4 January has passed.", out.msg
    out = do(s, who, "propose_agreement", to=s.get("people", other).name, amount=5, per="week", weeks=4,
             start="Monday 28 December", payer="me", what="the bills")
    assert out.msg == "Not done: Monday 28 December 2026 has passed.", out.msg


@functools.cache
def _town_bytes(seed: int) -> bytes:
    s = gen.generate(seed, FAMILIES)
    lane_b.install(s)
    product.launch(s)
    return pickle.dumps(s)


def town(seed: int):
    return pickle.loads(_town_bytes(seed))


@pytest.mark.parametrize("seed", [2, 3])
def test_a_shop_is_offered_to_connect_once_it_subscribes_and_the_refusal_before_says_how(seed):
    from townsim.town.systems import grantable
    s = town(seed)
    shop = next(o for o in s.all("orgs") if o.kind == "shop" and not o.outside and o.owner
                and s.where("firms", org=o.id) and s.get("people", o.owner).age >= 18)
    owner, P = shop.owner, product.launched(s).name
    assert do(s, owner, "subscribe", to=P, **{"for": "household", "asks": ""}).ok
    mine = {x.short: x for x in grantable(s, owner)}
    shops = [k for k, x in mine.items() if connections.client_of(s, x.id) == shop.id]
    homes = [k for k, x in mine.items() if connections.client_of(s, x.id) != shop.id]
    form = connections._fill_systems(s, owner)
    assert shops and homes and all(f'"{k}"' in form for k in homes) and not any(f'"{k}"' in form for k in shops), form
    out = do(s, owner, "connect", system=shops[0], scope="read only")
    assert out.msg == (f'Not done: {shop.name} has no subscription of its own; a business is subscribed in its own '
                       f'name ({{"op": "subscribe", "to": "{P}", "for": "{shop.name}"}}).'), out.msg
    assert do(s, owner, "subscribe", to=P, **{"for": shop.name, "asks": ""}).ok
    form = connections._fill_systems(s, owner)
    assert all(f'"{k}"' in form for k in shops), form
    assert do(s, owner, "connect", system=shops[0], scope="read only").ok


def test_without_lane_b_the_connect_form_and_refusal_are_as_they_were():
    s = gen.generate(3, FAMILIES)
    product.launch(s)
    shop = next(o for o in s.all("orgs") if o.kind == "shop" and not o.outside and o.owner)
    assert connections.no_subscription(s, shop.id) == f"{shop.name} has no subscription."


def _two_households(s):
    hs = [h for h in s.all("households") if adults(s, h.id)]
    return hs[0], hs[1]


def _view(s, pid):
    from townsim.town import views
    loop.routine(s)
    return "\n".join(m["content"] for m in views.view(s, pid)[0])


@pytest.mark.parametrize("seed", [2, 3])
def test_a_stopped_or_breached_agreement_can_be_named_and_is_told_as_it_stands(seed):
    from townsim.town import obligations
    from townsim.town.clock import date_text
    from townsim.town.obligations import money
    s = town(seed)
    payer, payee = _two_households(s)
    debtor, lender = adults(s, payer.id)[0], adults(s, payee.id)[0]
    ob = obligations.create(s, kind="agreement", payer=payer.id, payee=payee.id, cents=300, first=s.day + 3, every=7,
                            memo="club membership", ends="both", made_by=debtor, cause="routine:t", party=lender)
    _days(s, 7)
    assert do(s, debtor, "stop", id=ob).ok
    on = date_text(s.day)
    _days(s, 15)
    late = sum(d.cents - d.paid for d in obligations.owed_dues(s, ob))
    assert late >= 600
    out = do(s, debtor, "stop", id=ob)
    assert out.msg == (f"Not done: {ob} was stopped by you on {on} and recorded as a breach; {money(late)} remains owed "
                       f"on it and can be paid."), out.msg
    out = do(s, debtor, "propose_end", id=ob)
    assert out.msg.startswith(f"Not done: {ob} was stopped by you on {on} and recorded as a breach;"), out.msg
    out = do(s, lender, "stop", id=ob)
    assert not out.ok and "nothing with the id" not in out.msg and ob in out.msg, out.msg
    text = _view(s, debtor)
    worry = next(x for x in text.splitlines() if x.startswith("What worries you:"))
    assert f"you stopped it on {on}, so nothing is taken for it" in worry, worry
    going = [x for x in text.splitlines() if f"({ob}" in x and x.startswith("  ")]
    assert going and all(f"stopped on {on}, not taken" in x for x in going), going
    worry = next(x for x in _view(s, lender).splitlines() if x.startswith("What worries you:"))
    assert "keeps up the $3.00 a week" not in worry or "stopped" in worry, worry
    assert do(s, debtor, "pay", to=s.get("people", lender).name, amount=9, **{"for": ob}).ok


@pytest.mark.parametrize("seed", [2, 3])
def test_stop_on_a_payment_stopped_ended_or_repaid_and_on_an_ended_subscription_says_what_it_is(seed):
    from townsim.town import obligations
    from townsim.town.clock import date_text
    s = town(seed)
    payer, payee = _two_households(s)
    debtor = adults(s, payer.id)[0]
    standing = obligations.create(s, kind="standing", payer=payer.id, payee=payee.id, cents=500, first=s.day + 3,
                                  every=7, memo="standing payment to a neighbour", ends="payer", made_by=debtor,
                                  cause="routine:t")
    ended = obligations.create(s, kind="agreement", payer=payer.id, payee=payee.id, cents=200, first=s.day + 1,
                               every=7, last=s.day + 8, memo="lessons", ends="both", made_by=debtor,
                               cause="routine:t", party=adults(s, payee.id)[0])
    loan = obligations.create(s, kind="loan", payer=payer.id, payee=payee.id, cents=100, first=s.day + 1, every=7,
                              last=s.day + 1, memo="loan for a bicycle", ends="both", made_by=debtor,
                              cause="routine:t", party=adults(s, payee.id)[0])
    assert do(s, debtor, "stop", id=standing).ok
    on = date_text(s.day)
    _days(s, 10)
    assert s.get("obligations", ended).status == "finished" and s.get("obligations", loan).status == "finished"
    out = do(s, debtor, "stop", id=standing)
    assert out.msg == f"Not done: {standing} was stopped by you on {on}; nothing remains owed on it.", out.msg
    out = do(s, debtor, "stop", id=ended)
    assert out.msg.startswith(f"Not done: {ended} (lessons) ended on ") and "nothing remains owed on it" in out.msg, out.msg
    out = do(s, debtor, "stop", id=loan)
    assert out.msg.startswith(f"Not done: {loan} (loan for a bicycle) was repaid in full") and "nothing remains owed" in out.msg, out.msg
    product.subscribe(s, debtor, payer.id, "", "routine:t", tell=False)
    sub = product.subscription_of(s, payer.id)
    assert do(s, debtor, "stop", id=sub.id).ok
    out = do(s, debtor, "stop", id=sub.id)
    assert out.msg.startswith(f"Not done: {sub.id} ended on {date_text(s.day)}"), out.msg


@pytest.mark.parametrize("seed", [2, 3])
def test_putting_money_by_names_move_money_and_a_rule_may_do_it_each_week(seed):
    from townsim.town import acts, rules
    s = town(seed)
    h = next(x for x in s.all("households") if adults(s, x.id) and acts.has_savings(s, x.id))
    who = adults(s, h.id)[0]
    out = do(s, who, "standing_payment", to="savings", amount=20, day="Friday", start="", weeks=0)
    assert out.msg == ("Not done: Money is moved between your household's account and its savings with move_money; a "
                       "standing payment goes to someone else."), out.msg
    out = do(s, who, "pay", to="savings", amount=20)
    assert out.msg.endswith("with move_money; a payment goes to someone else."), out.msg
    out = do(s, who, "set_rule", name="put by", when={"weekday": clock.DAYS[clock.dow(s.day)]},
             do={"op": "move_money", "from": "household", "to": "savings", "amount": 20})
    assert out.ok, out.msg
    before = s.balance(acts.savings_of(h.id))
    rules.run_rules(s, 1)
    assert s.balance(acts.savings_of(h.id)) - before == 2000


@pytest.mark.parametrize("seed", [2, 3])
def test_a_desk_has_no_page_and_a_letter_naming_its_signer_does_not_call_the_signer_unknown(seed):
    s = town(seed)
    who = next(p.id for p in s.all("people") if p.age >= 18)
    for d in s.all("desks"):
        org = s.get("orgs", d.org).name
        out = do(s, who, "read_page", of=org)
        assert out.msg == f"Not done: {org} has no page; it is written to by letter.", out.msg
        out = do(s, who, "message", to=org, text=f"Dear {d.signer}, please write back.")
        assert out.ok and d.signer not in out.msg and "not anyone you know" not in out.msg, out.msg
