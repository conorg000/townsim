"""A larger town runs in good time, and a day does not grow much slower as the run grows."""

import gc
import time

from townsim.town import obligations as O
from townsim.town.entities import Household, Org, Person
from townsim.town.loop import run_day
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.store import Store

EMPTY = '{"thoughts": "", "acts": []}'
SETUP = "routine:setup"


def wide_town(households: int = 20) -> Store:
    """`households` households of two, each with wages, rent, energy and spending like the
    thin town's. Most get by; some fall behind, so arrears, fees and defaults pile up."""
    s = Store()
    s.place = "Wide"
    s.open_account("outside:employers", SETUP, allow_credit=True)
    s.open_account("outside:shops", SETUP)
    for org, name, kind in (("org1", "Lettings", "landlord"), ("org2", "Energy", "utility")):
        s.open_account(org, SETUP)
        s.add("orgs", Org(org, name, kind), SETUP)
    for i in range(1, households + 1):
        h = f"h{i}"
        s.open_account(h, SETUP)
        members = [f"p{2 * i - 1}", f"p{2 * i}"]
        s.add("households", Household(h, f"House {i}", members), SETUP)
        for n, p in enumerate(members):
            s.add("people", Person(p, f"Person {p}", h, 12.0, 12.0, (i + n) % 7), SETUP)
        s.transfer("outside:employers", h, 30_000 + 1_000 * i, "opening", SETUP)
        wage = 31_000 + 2_000 * (i % 5)               # some households fall ever further behind
        setup = dict(every=7, made_by="routine", cause=SETUP)
        O.create(s, kind="wage", payer="outside:employers", payee=h, cents=wage, first=4, memo="wages", ends="both", **setup)
        O.create(s, kind="rent", payer=h, payee="org1", cents=18_500, first=0, memo="rent", ends="both", **setup)
        O.create(s, kind="bill", payer=h, payee="org2", cents=4_800, first=2, memo="energy", ends="service", **setup)
        O.create(s, kind="bill", payer=h, payee="outside:shops", cents=12_000, first=5, memo="spending",
                 ends="service", **setup)
    return s


def week_times(households: int = 20, weeks: int = 21) -> list[float]:
    """Each week's time, with the garbage collector paused, so what the rest of the suite
    holds in memory does not weigh on the later weeks more than the earlier ones."""
    s = wide_town(households)
    replier, keys, times = ScriptedReplier(lambda key, messages: EMPTY), Keys("w"), []
    gc.collect()
    gc.disable()
    try:
        for _ in range(weeks):
            started = time.perf_counter()
            for _ in range(7):
                run_day(s, replier, keys)
            times.append(time.perf_counter() - started)
    finally:
        gc.enable()
    assert s.ledger.total() == 0 and s.engine_errors == []
    assert any(o.penalty for o in s.all("obligations"))      # the arrears machinery has work to do
    return times


def test_twenty_households_over_21_weeks_run_quickly_and_evenly():
    times = week_times()
    assert sum(times) < 3.0, times
    first, last = sum(times[:3]), sum(times[-3:])           # three weeks each, so a fast first week still measures
    assert last <= 5 * first, times
