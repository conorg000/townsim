"""Wenlow, the thin town: two households of two, a landlord, an energy company, and a
script of replies that exercises the engine over eight weeks without a model."""

from __future__ import annotations

import json

from townsim.town import obligations as O
from townsim.town.entities import Household, Org, Person
from townsim.town.replies import ReplyKey
from townsim.town.store import Store

_SETUP = "routine:setup"


def thin_town() -> Store:
    """The Ashbys (h1: Ruth p01, Tom p02; $600) and the Pryces (h2: Craig p03, Megan p04;
    $300), their landlord org1 and energy company org2, with wages, rent, energy and
    spending on weekly schedules. The Pryces spend $133 a week more than comes in."""
    s = Store()
    s.place = "Wenlow"
    s.seed = 0
    s.open_account("outside:employers", _SETUP, allow_credit=True)
    s.open_account("outside:shops", _SETUP)
    for org, name, kind in (("org1", "Wenlow Lettings", "landlord"), ("org2", "Northgrid Energy", "utility")):
        s.open_account(org, _SETUP)
        s.add("orgs", Org(org, name, kind, owner=""), _SETUP)
    people = {"h1": ("Ashby", [("p01", "Ruth", 4), ("p02", "Tom", 3)]),
              "h2": ("Pryce", [("p03", "Craig", 4), ("p04", "Megan", 5)])}
    for h, (surname, members) in people.items():
        s.open_account(h, _SETUP)
        s.add("households", Household(h, surname, [p for p, _, _ in members]), _SETUP)
        for p, first, stock_day in members:
            s.add("people", Person(p, f"{first} {surname}", h, 12.0, 12.0, stock_day), _SETUP)
    for h, cents in (("h1", 60000), ("h2", 30000)):
        s.transfer("outside:employers", h, cents, "opening", _SETUP)
    setup = dict(every=7, made_by="routine", cause=_SETUP)
    for h, wage, rent in (("h1", 52000, 21000), ("h2", 33000, 18500)):
        O.create(s, kind="wage", payer="outside:employers", payee=h, cents=wage, first=4, memo="wages",
                 ends="both", **setup)
        O.create(s, kind="rent", payer=h, payee="org1", cents=rent, first=0, memo="rent", ends="both", **setup)
        O.create(s, kind="bill", payer=h, payee="org2", cents=4800, first=2, memo="energy", ends="service", **setup)
        O.create(s, kind="bill", payer=h, payee="outside:shops", cents=23000, first=5,
                 memo="food and regular spending", ends="service", **setup)
    return s


def _reply(thoughts: str, *acts: dict) -> str:
    return json.dumps({"thoughts": thoughts, "acts": list(acts)})


# (day, actor) -> reply text. Each is a day the person is sure to act: their stock day,
# or (Ruth on day 12) the day after a request reaches them.
_SCRIPT: dict[tuple[int, str], str] = {
    (3, "p02"): _reply("Edith will know about the allotments.",
                       {"op": "message", "to": "Edith Marsh", "text": "Is there a plot free this spring?"}),
    (4, "p01"): _reply("The Pryces are stretched. A little now and something every week.",
                       {"op": "pay", "to": "Megan Pryce", "amount": 25, "note": "for the school shoes"},
                       {"op": "standing_payment", "to": "Megan Pryce", "amount": 20, "day": "Friday",
                        "weeks": 0, "note": "towards the shopping"}),
    (4, "p03"): _reply("Wages are in. Pay Monday's rent now, before it goes on other things.",
                       {"op": "pay", "to": "Wenlow Lettings", "amount": 185, "note": "rent"}),
    (5, "p04"): "I'll sort the shopping out later.",
    (10, "p02"): _reply("", {"op": "pay", "to": ["Ruth"], "amount": "lots"}),
    (11, "p03"): _reply("Ask Ruth for a little to get us to the end of the month.",
                        {"op": "request", "to": "Ruth Ashby", "amount": 50,
                         "text": "Could you lend us $50 until the end of the month?"}),
    (12, "p01"): _reply("Of course.", {"op": "answer", "request": "rq1", "answer": "accept",
                                       "note": "No hurry to pay it back."}),
    (12, "p04"): _reply("Getting the list right.",
                        *({"op": "write_notes", "text": f"Shopping list, version {i}"} for i in range(1, 13))),
    (25, "p01"): _reply("Three weeks is what I said I could do.", {"op": "stop", "id": "ob9"}),
}

_NOTHING = _reply("")


def thin_script(key: ReplyKey, messages: list[dict]) -> str | None:
    """The thin town's reply for a turn, decided from the day and the actor alone."""
    return _SCRIPT.get((key.day, key.actor), _NOTHING)
