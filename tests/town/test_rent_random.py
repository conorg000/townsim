"""Landlord and tenant at random: a let has one rent, re-priced however often it changes.

On each seed the landlord of the deals town changes the rent at random, any number of times,
while the tenants pay by hand (toward the rent and not), run out of money and pay their
arrears, stop the rent, and are served notice, for twelve weeks. Then: no rent day is taken
twice or skipped; what each rent day is for is what both sides were last told for that day;
money counted toward a rent day is never taken again for it; a breached rent is never
collected automatically; and the books hold as everywhere else.
"""

from __future__ import annotations

import random
import re

import pytest

from townsim.town.acts import Act, apply_act
from townsim.town.clock import parse_date
from townsim.town.loop import close, routine

from .helpers import set_cash
from .test_ops_deals import deals_town
from .test_random_play import _check_run

DAYS = 84
SEEDS = range(12)
_CHANGE = re.compile(r"changes from \$([\d,.]+) to \$([\d,.]+) a week from (\w+ \d+ \w+)\.")
_STAYS = re.compile(r"stays \$([\d,.]+) a week\. This replaces")
_KEPT = re.compile(r"The rent due (\w+ \d+ \w+) stays \$([\d,.]+):")


def _cents(text: str) -> int:
    return round(float(text.replace(",", "")) * 100)


def _play(seed: int):
    rng = random.Random(f"rent:{seed}")
    s = deals_town()
    set_cash(s, "h2", 200_000)
    rent = next(o for o in s.where("obligations", payer="h2") if o.kind == "rent")
    told: dict[int, int] = {}                  # rent day -> cents both sides were last told it is for
    told_from: list[tuple[int, int]] = [(0, rent.cents)]
    n = 0

    def act(actor, op, **args):
        nonlocal n
        n += 1
        return apply_act(s, Act(actor, actor, op, args), n)

    def amount_for(day: int) -> int:
        if day in told:
            return told[day]
        return [c for start, c in told_from if start <= day][-1]

    for _ in range(DAYS):
        routine(s)
        for _ in range(rng.choice([0, 1, 1, 2, 3])):
            roll = rng.random()
            if roll < 0.3:
                out = act("p06", "set_rent", tenant=rng.choice(["Craig Pryce", "Megan Pryce"]),
                          weekly=rng.choice([150, 170, 185, 190, 200, 210, 240, 185.5]))
                if out.ok:
                    if m := _CHANGE.search(out.msg):      # the present rent until the day, the new from it
                        day = parse_date(m.group(3), s.day)
                        told_from += [(s.day, _cents(m.group(1))), (day, _cents(m.group(2)))]
                    else:
                        m = _STAYS.search(out.msg)
                        told_from.append((s.day, _cents(m.group(1))))
                    for d in [d for d in told if d >= s.day]:
                        del told[d]
                    for m in _KEPT.finditer(out.msg):
                        told[parse_date(m.group(1), s.day)] = _cents(m.group(2))
            elif roll < 0.6:
                args = {"to": "Len Hale", "amount": rng.choice([185, 200, 100, 50, 18.5, 240, 400])}
                if rng.random() < 0.6:
                    args["for"] = rent.id
                act(rng.choice(["p03", "p04"]), "pay", **args)
            elif roll < 0.7:
                owed = sum(d.cents - d.paid for d in s.where("dues", obligation=rent.id)
                           if d.status in ("arrears", "defaulted"))
                if owed:
                    act("p03", "pay", to="Len Hale", amount=owed / 100, **{"for": rent.id})
            elif roll < 0.8:
                set_cash(s, "h2", rng.choice([0, 0, 10_000, 300_000]))
            elif roll < 0.85:
                act("p03", "stop", id=rent.id)
            else:
                act("p06", "serve_notice", tenant="Craig Pryce")
            assert s.engine_errors == [] and s.ledger.total() == 0
        close(s)
    return s, rent.id, amount_for


@pytest.mark.parametrize("seed", SEEDS)
def test_a_let_has_one_rent_and_every_rent_day_is_taken_once_at_what_was_told(seed):
    s, rent, amount_for = _play(seed)
    _check_run(s)
    assert [o.id for o in s.all("obligations") if o.kind == "rent" and o.payer == "h2"] == [rent]
    ob = s.get("obligations", rent)
    dues = sorted((d for d in s.where("dues", obligation=rent) if d.status != "void"), key=lambda d: d.day)
    assert [d.day for d in dues if d.day < DAYS] == list(range(0, DAYS, 7))      # none skipped, none twice
    assert [d.day for d in dues if d.day >= DAYS] in ([], [DAYS])                  # one paid toward ahead
    for d in dues:
        assert d.cents == amount_for(d.day), (d, amount_for(d.day))
    for t in s.ledger.transfers:                                         # a breached rent is never taken
        if t.cause.startswith("routine:collect:"):
            due = s.get("dues", t.cause.removeprefix("routine:collect:"))
            if due.obligation == rent and ob.stopped_on != -1:
                assert t.t <= ob.stopped_on, (t, ob.stopped_on)


def test_the_random_landlord_does_everything():
    """Across the seeds the rent is changed, raised and lowered, paid by hand, stopped and
    paid off in arrears, so the checks cannot go quiet."""
    outs = [_play(seed)[0] for seed in SEEDS]
    causes = [c.cause for s in outs for c in s.changes if c.cause.startswith("op:")]
    for op in ("set_rent", "pay", "stop"):
        assert any(f":{op}:" in c for c in causes), op
    assert any(o.status == "breached" for s in outs for o in s.all("obligations") if o.kind == "rent")
    assert any(c.table == "obligations" and c.field == "next_from" and c.new != -1 for s in outs for c in s.changes)
