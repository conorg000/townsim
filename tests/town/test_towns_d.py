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

