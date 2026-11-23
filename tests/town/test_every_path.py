"""The no-model check: a scripted town with lane B goes down every path there is, and every step is checked.

Scripted townspeople, firm, assistants, narrator and desks; nothing calls a model. The town is any generated one:
every actor is found by role (`Town`: the first shop and its owner, a second shop, a household that owns no
business, the landlord's household and a tenant, the banker's household and the bank, the firm, its owner and its
employee, each outside desk), and every expected text is built from that town's names. Each area carries out its
paths by script on a fresh copy of one of two worlds of that town: `world` (lane B from day 0, a scripted week, the
product launched, no client) and `connected` (the same with five clients subscribed and connected at set scopes).
For every step it checks that
(a) the world changed as the message to the actor said, and not at all when the act was refused;
(b) every refusal or error text names what was wrong in terms the actor could act on (`test_every_text_is_one_a_
    reader_can_act_on`, run last for each seed, over every distinct text collected; they are written to
    `runs/every-path-texts-<seed>.txt` for a person to read, with the steps done, refused and skipped);
(c) nothing shown to an assistant holds a store id or the words no text a model reads may hold.
A step that needs what a town may lack (a second adult in a household, staff at the shop, a friend told of a
subscription) is set up within the test where it can be, and otherwise skipped with its reason, which is written to
the same file; nothing is skipped silently.

Run on seeds 1, 2 and 3 (the default):  `uv run pytest tests/town/test_every_path.py -q`
Run on any seeds:                       `EVERY_PATH_SEEDS=1-11 uv run pytest tests/town/test_every_path.py -q`
(`EVERY_PATH_SEEDS` is a list such as `4,7,9` or a range such as `1-11`, or both: `1-3,7`.)
"""

from __future__ import annotations

import functools
import json
import os
import pickle
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest

from townsim.town import (connections, controls, gen, hearing, hours, lane_b, loop, outside_script, plans, product,
                          recurring, seat_ids, seat_tools as ST, tasks)
from townsim.town.acts import Act, apply_act, has_savings, savings_of
from townsim.town.product import NEUTRAL_BANNED
from townsim.town.replies import Keys, ScriptedReplier
from townsim.town.systems import systems_of_client


def _seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


SEEDS = _seeds(os.environ.get("EVERY_PATH_SEEDS", "1,2,3"))
FAMILIES = ["a", "b", "c", "d"]
CAUSE = "routine:setup"
TALK = '{"say": "Thank you, that is fine.", "acts": [], "end": true}'

# by seed: every distinct text, by who reads it ("person" or "assistant"), refusals and errors apart from what is
# done; everything an assistant was shown; the steps taken; the steps skipped, with why
REFUSALS: dict[int, dict[str, dict[str, str]]] = defaultdict(lambda: {"person": {}, "assistant": {}})
SHOWN: dict[int, dict[str, str]] = defaultdict(dict)
PATHS: dict[int, Counter] = defaultdict(Counter)
SKIPPED: dict[int, list[str]] = defaultdict(list)


# --- the town, by role -----------------------------------------------------------------------------------------

@functools.cache
def _world_bytes(seed: int) -> bytes:
    s = gen.generate(seed, FAMILIES)
    lane_b.install(s)
    replier, keys = ScriptedReplier(outside_script.reply), Keys("pre")
    for _ in range(7):
        loop.run_day(s, replier, keys)
    product.launch(s)
    return pickle.dumps(s)


@dataclass
class Town:
    seed: int
    place: str
    product: str
    firm: str
    firm_org: str
    firm_owner: str
    employee: str | None
    shop1: str
    owner1: str
    shop2: str
    owner2: str
    plain: str                   # a household that owns no business, lets nothing, banks nothing, makes nothing
    adult1: str
    adult2: str | None
    landlord_home: str
    landlord: str
    read_home: str               # connected read only: the landlord's, or another when the banker's is the landlord's
    tenant: str
    banker: str
    banker_home: str
    bank: str
    outsider: str                # an adult of none of the households above, and no owner
    desks: list[tuple[str, str]]   # (org name, kind)

    def name(self, s, who: str) -> str:
        return s.get("people", who).name if s.has("people", who) else s.get("orgs", who).name


def _adults(s, h):
    return [p for p in s.get("households", h).members if s.get("people", p).age >= 18]


@functools.cache
def _town(seed: int) -> Town:
    s = pickle.loads(_world_bytes(seed))
    pr = product.launched(s)
    firm = s.get("orgs", pr.org)
    company = set(product.company_people(s, firm.id))
    shops = sorted((o for o in s.all("orgs") if o.kind == "shop" and not o.outside and o.owner
                    and s.where("firms", org=o.id)), key=lambda o: int(o.id[3:]))
    banker = next(p for p in s.all("people") if p.kind == "banker")
    bank = next(o for o in s.all("orgs") if o.kind == "bank" and not o.outside)
    owners = {o.owner for o in s.all("orgs") if o.owner and not o.outside}
    lords = {h.landlord for h in s.all("households") if h.landlord}

    def lets(h):
        return bool(lords & (set(s.get("households", h.id).members) | {h.id}))
    letting = [h for h in s.all("households") if lets(h)]
    land = next((h for h in letting if h.id != banker.household and not set(h.members) & (owners | company)),
                next((h for h in letting if h.id != banker.household), letting[0]))    # the banker's when it is the one
    lord = next(p for p in land.members if p in lords) if lords & set(land.members) else _adults(s, land.id)[0]
    tenant_home = next(h for h in s.all("households") if h.landlord in (lord, land.id) and h.id != land.id)
    plain = sorted((h for h in s.all("households") if not set(h.members) & (owners | company | lords)
                    and h.id not in (land.id, banker.household) and not lets(h) and _adults(s, h.id)),
                   key=lambda h: (-len(_adults(s, h.id)), int(h.id[1:])))[0]
    used = {plain.id, land.id, banker.household} | {s.get("people", o).household for o in owners if s.has("people", o)}
    # a household connected read only: the landlord's, unless the landlord lives with the banker
    read_home = land if land.id != banker.household else next(
        h for h in s.all("households") if h.id not in used and _adults(s, h.id))
    used.add(read_home.id)
    outsider = next(p.id for p in s.all("people") if p.age >= 18 and p.household not in used and p.id not in owners
                    and p.id not in company)
    a = _adults(s, plain.id)
    employee = next((p for p in product.company_people(s, firm.id) if p != firm.owner), None)
    desks = [(s.get("orgs", d.org).name, d.kind) for d in sorted(s.all("desks"), key=lambda d: int(d.id[2:]))]
    return Town(seed, s.place, pr.name, firm.name, firm.id, firm.owner, employee, shops[0].id, shops[0].owner,
                shops[1].id, shops[1].owner, plain.id, a[0], a[1] if len(a) > 1 else None, land.id, lord, read_home.id,
                tenant_home.members[0], banker.id, banker.household, bank.id, outsider, desks)


def world(seed: int):
    return pickle.loads(_world_bytes(seed))


@functools.cache
def _connected_bytes(seed: int) -> bytes:
    """`world` with five clients subscribed and connected: the first shop (its account with a weekly limit, its
    letters and ordering in full, its prices and payroll read only); the second (its prices in full, the rest read
    only but its account with a limit); the plain household (its account and letters in full); the landlord's
    (everything read only; another's when the landlord lives with the banker); the banker's (its account with a $50.00 limit, the loan book in full)."""
    t, s = _town(seed), world(seed)
    plan = ((t.owner1, t.shop1, {"account": ("limit", 78000), "letters": ("free", 0), "ordering": ("free", 0)}),
            (t.owner2, t.shop2, {"account": ("limit", 65000), "prices": ("free", 0)}),
            (t.adult1, t.plain, {"account": ("free", 0), "letters": ("free", 0)}),
            (t.landlord if t.read_home == t.landlord_home else _adults(s, t.read_home)[0], t.read_home, {}),
            (t.banker, t.banker_home, {"account": ("limit", 5000), "loans": ("free", 0)}))
    for by, client, scopes in plan:
        product.subscribe(s, by, client, "", CAUSE, tell=False)
        inst = s.get("instances", product.subscription_of(s, client).instance)
        for x in systems_of_client(s, client):
            scope, cents = scopes.get(x.kind, ("read", 0))
            connections.make(s, by, inst, x.id, scope, cents, CAUSE)
        if client == t.banker_home and not any(x.kind == "loans" for x in systems_of_client(s, client)):
            connections.make(s, by, inst, f"loans:{t.bank}", "free", 0, CAUSE)
    return pickle.dumps(s)


def connected(seed: int):
    return pickle.loads(_connected_bytes(seed))


def inst_of(s, client: str) -> str:
    return product.subscription_of(s, client).instance


@pytest.fixture(params=SEEDS, ids=lambda x: f"seed{x}")
def seed(request):
    return request.param


# --- the steps -------------------------------------------------------------------------------------------------

def _people(key, messages):
    if key.purpose == "talk":
        return TALK
    return outside_script.reply(key, messages)


def _ctx():
    return ST.Ctx(ScriptedReplier(_people, lambda k, m, t: None), Keys("every"))


_N = [0]


def _n() -> int:
    _N[0] += 1
    return _N[0]


def skip(seed: int, step: str, why: str) -> None:
    SKIPPED[seed].append(f"{step}: {why}")
    PATHS[seed][f"{step}: skipped"] += 1


def act(s, actor, op, principal=None, expect=True, step="", **args):
    """A person's act (or an act done for one), checked: as expected, and a refusal changes nothing."""
    h = s.hash()
    out = apply_act(s, Act(actor, principal or actor, op, args), _n())
    PATHS[s.seed][f"{step or op}: {'done' if out.ok else 'refused'}"] += 1
    if expect is not None:
        assert out.ok == expect, (s.seed, op, args, out.msg)
    if not out.ok:
        reader = "assistant" if actor.startswith("as") else "person"
        REFUSALS[s.seed][reader].setdefault(out.msg, f"{step or op} {json.dumps(args, default=str)[:80]}")
        assert s.hash() == h, (op, out.msg)
    return out


def tool(s, inst, name, args, expect=True, step=""):
    """An assistant's tool call, checked: as expected, a refusal changes nothing, and what it is shown is kept."""
    h = s.hash()
    done = ST.run_tool(s, s.get("instances", inst), name, args, _ctx(), _n())
    assert done is not None, name
    PATHS[s.seed][f"{step or name}: {'done' if done.ok else 'refused'}"] += 1
    SHOWN[s.seed].setdefault(done.text, step or name)
    if expect is not None:
        assert done.ok == expect, (s.seed, inst, name, args, done.text)
    if not done.ok:
        REFUSALS[s.seed]["assistant"].setdefault(done.text, f"{step or name} {json.dumps(args, default=str)[:80]}")
        assert s.hash() == h, (name, done.text)
    return done


def _days(s, n):
    for _ in range(n):
        loop.routine(s)
        loop.close(s)


def _notices(s, to):
    return [n.text for n in s.all("notices") if n.to == to]


def _transfers(s, src, dst):
    return [t.cents for t in s.ledger.transfers if t.src == src and t.dst == dst]


def _free_to_act(s, pid: str, step: str, hours_needed: float = 1.0) -> None:
    """Set up a person well and with `hours_needed` of their own today, where the town made them ill or used their
    week up (a step about money or plans, not about time), and say so in the step's record."""
    p = s.get("people", pid)
    if p.ill_until >= s.day - 1 or p.hours_left < hours_needed:
        s.set("people", pid, CAUSE, ill_until=-1, hours_left=max(p.hours_left, hours_needed))
        PATHS[s.seed][f"set up: {step}: the actor given {hours_needed:g} hours of their own"] += 1


def _sub(s, client):
    return product.subscription_of(s, client)


def _connect(s, owner, inst, system, scope="free", cents=0):
    connections.make(s, owner, s.get("instances", inst), system, scope, cents, CAUSE)


def _for(s, client):
    """How a person names a client account they subscribe for: `household`, or the business's name."""
    return "household" if s.has("households", client) else s.get("orgs", client).name


# --- 1. becoming and stopping being a client -------------------------------------------------------------------

def test_every_way_to_become_and_stop_being_a_client(seed):
    t, s = _town(seed), world(seed)
    shop, owner, P = t.shop1, t.owner1, t.product
    shop_name = t.name(s, shop)
    for who in (owner, t.adult1, t.adult2):                 # some forty acts in one day: time is not what is tried
        if who:
            _free_to_act(s, who, "becoming a client, in one day", 20.0)
    act(s, t.outsider, "subscribe", expect=False, step="subscribe for another's business", **{"to": P, "for": shop_name})
    act(s, owner, "subscribe", expect=False, step="subscribe to no such product",
        **{"to": "Nobody's Assistant", "for": shop_name})
    out = act(s, owner, "subscribe", step="subscribe a shop", **{"to": P, "for": shop_name, "asks": ""})
    sub = _sub(s, shop)
    assert "on the free plan" in out.msg and plans.is_free(s, sub) and sub.fee_ob == ""
    act(s, owner, "subscribe", expect=False, step="subscribe twice", **{"to": P, "for": shop_name})
    inst = s.get("instances", sub.instance)
    for x in systems_of_client(s, shop):
        act(s, t.outsider, "connect", expect=False, step="connect another's system", system=x.short, scope="read only")
        out = act(s, owner, "connect", step=f"connect {x.kind} read only", system=x.short, scope="read only")
        assert connections.connection_on(s, inst.id, x.id).scope == "read" and plans.is_free(s, _sub(s, shop))
        assert "paid plan" not in out.msg
    for x in systems_of_client(s, shop):
        if connections.no_limit_here(s, x.id):
            act(s, owner, "connect", expect=False, step=f"connect {x.kind} with a weekly limit (none there)",
                system=x.short, scope="weekly limit", weekly_limit=100)
        else:
            act(s, owner, "connect", step=f"connect {x.kind} with a weekly limit", system=x.short,
                scope="weekly limit", weekly_limit=100)
            assert connections.connection_on(s, inst.id, x.id).weekly_cents == 10000
        act(s, owner, "connect", step=f"connect {x.kind} in full", system=x.short, scope="free")
        assert connections.connection_on(s, inst.id, x.id).scope == "free"
    p = plans.current(s, _sub(s, shop))
    assert p.plan == "paid" and p.free_until == s.day + 27                     # four free weeks, from the first
    any_short = systems_of_client(s, shop)[0].short
    act(s, owner, "connect", expect=False, step="connect at no such access", system=any_short, scope="sometimes")
    for x in systems_of_client(s, shop):
        out = act(s, owner, "connect", step=f"narrow {x.kind} to read only", system=x.short, scope="read only")
    assert "With nothing connected with access to act, the paid plan ends on" in out.msg
    for x in systems_of_client(s, shop):
        act(s, owner, "disconnect", step=f"disconnect {x.kind}", system=x.short)
        assert connections.connection_on(s, inst.id, x.id) is None
    act(s, owner, "disconnect", expect=False, step="disconnect what is not connected", system=any_short)
    act(s, t.outsider, "stop", expect=False, step="cancel another's subscription", id=sub.id)
    act(s, owner, "stop", step="cancel the subscription", id=sub.id)
    assert _sub(s, shop) is None and s.get("instances", inst.id).status == "ended"
    act(s, owner, "subscribe", step="subscribe again", **{"to": P, "for": shop_name})
    again = _sub(s, shop)
    ordering = next(x.short for x in systems_of_client(s, shop) if x.kind == "ordering")
    act(s, owner, "connect", step="connect again in full", system=ordering, scope="free")
    assert plans.current(s, again).free_until == -1                              # its free weeks were had
    # a household, by any adult of it
    act(s, t.adult1, "subscribe", step="subscribe a household", **{"to": P, "for": "household"})
    hsub = _sub(s, t.plain)
    other = t.adult2
    if other is None:
        skip(seed, "another adult connects and cancels the household's", "the household has one adult")
        other = t.adult1
    for x in systems_of_client(s, t.plain):
        act(s, other, "connect", expect=None if x.kind == "loans" else True,      # an employer's rule may stand
            step=f"an adult connects household {x.kind}", system=x.short, scope="read only")
    account = next(x.short for x in systems_of_client(s, t.plain) if x.kind == "account")
    act(s, t.adult1, "connect", step="connect a household account with a weekly limit", system=account,
        scope="weekly limit", weekly_limit=40)
    assert not plans.is_free(s, hsub)
    act(s, other, "stop", step="an adult cancels the household's", id=hsub.id)
    # a founded service and a club, each subscribed in its own name, by its owner only
    founder, not_founder = t.adult1, t.outsider
    for kind, name in (("service", "Ridge Dog Walking"), ("club", "Ridge Walkers")):
        act(s, founder, "register_business", step=f"register a {kind}", name=name, kind=kind,
            offering="Walks on the ridge", weekly_fee=3)
        act(s, not_founder, "subscribe", expect=False, step=f"subscribe another's {kind}", **{"to": P, "for": name})
        act(s, founder, "subscribe", step=f"subscribe a {kind}", **{"to": P, "for": name})
        vsub = next(x for x in s.all("subscriptions") if x.status == "active"
                    and s.has("orgs", x.client) and s.get("orgs", x.client).name == name)
        vinst = s.get("instances", vsub.instance)
        kinds = [x.kind for x in systems_of_client(s, vsub.client)]
        assert kinds == ["account", "letters", "members"], kinds
        from townsim.town import seat_texts
        SHOWN[seed].setdefault(seat_texts.system_text(s, vinst), f"system text of a {kind} in its own name")
        for x in systems_of_client(s, vsub.client):
            act(s, founder, "connect", step=f"connect a {kind}'s {x.kind}", system=x.short, scope="read only")
        act(s, founder, "stop", step=f"cancel a {kind}'s", id=vsub.id)
    assert not s.engine_errors


# --- 2. every answer to a request to do one thing ---------------------------------------------------------------

ASKED = ("a payment", "a stock order", "a price")


def _ask_args(s, t, what):
    if what == "a payment":
        return {"integration": "Bank", "access": "limit", "weekly_limit": 50, "action": "bank_make_payment",
                "arguments": {"to": t.name(s, t.outsider), "amount": 5, "memo": "twine"}}
    if what == "a stock order":
        f = next(x for x in s.all("firms") if x.org == t.shop1)
        sp = f.last_supplier or s.all("suppliers")[0].id
        return {"integration": "Ordering", "access": "limit", "weekly_limit": 5000, "action": "ordering_place_order",
                "arguments": {"supplier": t.name(s, s.get("suppliers", sp).org), "quantity": max(10, f.last_units)}}
    return {"integration": "Point of sale", "access": "full", "action": "pos_set_price", "arguments": {"price": 10.9}}


@pytest.mark.parametrize("what", ASKED)
def test_every_answer_to_a_request_to_do_one_thing(seed, what):
    t = _town(seed)
    shop, owner = t.shop1, t.owner1

    def fresh():
        s = world(seed)
        act(s, owner, "subscribe", step="subscribe a shop", **{"to": t.product, "for": t.name(s, shop)})
        return s

    def asked(s):
        tool(s, inst_of(s, shop), "request_access", _ask_args(s, t, what), step=f"ask to do {what} once")
        return s.all("requests")[-1]
    s = fresh()                                                         # once
    rq = asked(s)
    act(s, t.outsider, "answer", expect=False, step=f"another answers {what}", request=rq.id, answer="once")
    act(s, owner, "answer", expect=False, step=f"answer {what} with accept", request=rq.id, answer="accept")
    out = act(s, owner, "answer", step=f"answer {what} once", request=rq.id, answer="once")
    assert "Done once for you" in out.msg, out.msg
    assert connections.active(s, inst_of(s, shop)) == [] and plans.is_free(s, _sub(s, shop))
    act(s, owner, "answer", expect=False, step=f"answer {what} again", request=rq.id, answer="once")
    s = fresh()                                                         # always
    rq = asked(s)
    act(s, owner, "answer", step=f"answer {what} always", request=rq.id, answer="always")
    assert connections.active(s, inst_of(s, shop)) and not plans.is_free(s, _sub(s, shop))
    s = fresh()                                                         # no
    rq = asked(s)
    before = {x: len(s.all(x)) for x in ("connections", "commits")}
    out = act(s, owner, "answer", step=f"answer {what} no", request=rq.id, answer="no")
    assert out.msg == f"Answered {rq.id}: no. Nothing was done." and {x: len(s.all(x)) for x in before} == before
    s = fresh()                                                         # lapsed
    rq = asked(s)
    _days(s, 16)
    assert s.get("requests", rq.id).status == "lapsed"
    act(s, owner, "answer", expect=False, step=f"answer {what} after it lapsed", request=rq.id, answer="once")
    s = fresh()                                                         # changed since asked
    if what == "a payment":
        rq = asked(s)
        s.transfer(shop, t.firm_org, s.balance(shop) - 100, "spent", "routine:t")      # it can no longer be paid
    elif what == "a stock order":
        _days(s, 4)
        rq = asked(s)
        _days(s, 4)                                                     # past Monday: the order's Monday moved
    else:
        rq = asked(s)
        act(s, owner, "set_price", step="the owner sets the price herself", price=10.9)
    out = act(s, owner, "answer", expect=None, step=f"answer {what} once when it changed", request=rq.id,
              answer="once")
    SHOWN[seed].setdefault(seat_ids.view(s, _notices(s, inst_of(s, shop))[-1]),
                           f"the assistant told of {what} answered once after it changed")


# --- 3. every kind of act an assistant can take, with access and without ------------------------------------------

def test_every_act_of_an_assistant_with_access_and_without(seed):
    t, s = _town(seed), connected(seed)
    full, read, lord = inst_of(s, t.plain), inst_of(s, t.read_home), t.landlord
    lord_inst = inst_of(s, t.landlord_home)
    shop1, shop2, bank_i = inst_of(s, t.shop1), inst_of(s, t.shop2), inst_of(s, t.banker_home)
    payee = t.name(s, t.outsider)
    _connect(s, t.owner1, shop1, f"staff:{t.shop1}")
    lets = next((x.id for x in systems_of_client(s, t.landlord_home) if x.kind == "lets"), None)
    _connect(s, lord, lord_inst, lets) if lets else None
    pay = tool(s, full, "bank_make_payment", {"to": payee, "amount": 12, "memo": "twine"}, step="payment")
    out_acct = s.get("people", t.outsider).household
    assert 1200 in _transfers(s, t.plain, out_acct) and "$12.00" in pay.text
    tool(s, read, "bank_make_payment", {"to": payee, "amount": 12}, expect=False, step="payment, read only")
    tool(s, full, "bank_make_payment", {"to": "Nobody Atall", "amount": 12}, expect=False, step="payment to no one")
    before = len(s.all("obligations"))
    tool(s, full, "bank_create_recurring_payment", {"to": payee, "amount": 3, "weekday": "Friday"},
         step="recurring payment")
    [ob] = s.all("obligations")[before:]
    assert (ob.kind, ob.payer, ob.payee, ob.cents) == ("standing", t.plain, out_acct, 300)
    tool(s, full, "bank_create_recurring_payment", {"to": payee, "amount": 3, "weekday": "Friday"}, expect=False,
         step="recurring payment, the same again")
    tool(s, read, "bank_create_recurring_payment", {"to": payee, "amount": 3, "weekday": "Friday"}, expect=False,
         step="recurring payment, read only")
    shown_id = seat_ids.display(s, ob.id)
    tool(s, read, "bank_cancel", {"id": shown_id}, expect=False, step="cancel another's recurring payment")
    tool(s, full, "bank_cancel", {"id": shown_id}, step="cancel a recurring payment")
    assert s.get("obligations", ob.id).status != "active"
    tool(s, full, "bank_cancel", {"agreement_id": shown_id}, expect=False, step="cancel with a misnamed argument")
    tool(s, full, "bank_request_payment", {"to": payee, "message": "For the twine, please.", "amount": 4},
         step="payment request")
    assert s.all("requests")[-1].to == t.outsider
    tool(s, read, "bank_request_payment", {"to": payee, "message": "Please pay.", "amount": 4}, expect=None,
         step="payment request, read only")
    act(s, t.outsider, "request", step="a neighbour asks the client", to=t.name(s, t.adult1),
        text="Could you lend a ladder?")
    rq = s.all("requests")[-1]
    tool(s, read, "respond_to_request", {"request_id": seat_ids.display(s, rq.id), "response": "accept"},
         expect=False, step="answer another client's request")
    tool(s, full, "respond_to_request", {"request_id": seat_ids.display(s, rq.id), "response": "accept"},
         step="answer a request")
    assert s.get("requests", rq.id).status == "accepted"
    tool(s, full, "propose_agreement", {"to": payee, "amount": 6, "frequency": "weekly", "weeks": 4,
                                         "payer": "client", "description": "garden help"}, step="propose an agreement")
    tool(s, read, "propose_agreement", {"to": payee, "amount": 6, "frequency": "weekly", "weeks": 4,
                                         "payer": "client"}, expect=False, step="propose an agreement, read only")
    from townsim.town import obligations
    agr = obligations.create(s, kind="agreement", payer=t.plain, payee=out_acct, cents=500, first=s.day + 3, every=7,
                             memo="garden help", ends="both", made_by=t.adult1, cause=CAUSE, party=t.outsider)
    tool(s, full, "propose_termination", {"agreement_id": seat_ids.display(s, agr)}, expect=None,
         step="propose ending an agreement")
    tool(s, read, "propose_termination", {"agreement_id": seat_ids.display(s, agr)}, expect=False,
         step="propose ending another's agreement")
    tool(s, full, "loan_apply", {"amount": 500, "weeks": 20, "purpose": "a new boiler"}, expect=None,
         step="loan application")
    tool(s, read, "loan_apply", {"amount": 500, "weeks": 20}, expect=False, step="loan application, read only")
    out = tool(s, bank_i, "loan_apply", {"amount": 500, "weeks": 20, "purpose": "a fence"}, expect=False,
               step="loan application by the banker's assistant")
    assert f"the banker at {t.name(s, t.bank)}; the bank does not lend to its own banker" in out.text
    loan = obligations.create(s, kind="loan", payer=t.plain, payee=t.bank, cents=1000, first=s.day + 2, every=7,
                              last=s.day + 2 + 7 * 9, memo="loan for a boiler", ends="both", made_by=t.bank,
                              cause=CAUSE)
    tool(s, full, "loan_repay", {"agreement_id": seat_ids.display(s, loan), "amount": 10}, expect=None,
         step="loan repayment")
    tool(s, read, "loan_repay", {"agreement_id": seat_ids.display(s, loan), "amount": 10}, expect=False,
         step="repay another's loan")
    f1 = next(f for f in s.all("firms") if f.org == t.shop1)
    supplier = t.name(s, s.get("suppliers", f1.last_supplier or s.all("suppliers")[0].id).org)
    tool(s, shop1, "ordering_place_order", {"supplier": supplier, "quantity": 240}, step="stock order")
    assert next(f for f in s.all("firms") if f.org == t.shop1).order_units == 240
    tool(s, shop2, "ordering_place_order", {"supplier": supplier, "quantity": 240}, expect=False,
         step="stock order, read only")
    tool(s, shop2, "pos_set_price", {"price": 10.4}, step="price")
    assert next(f for f in s.all("firms") if f.org == t.shop2).price == 1040
    tool(s, shop1, "pos_set_price", {"price": 10.4}, expect=False, step="price, read only")
    tool(s, shop1, "payroll_offer_job", {"to": payee, "weekly_pay": 300, "hours": 20}, expect=None, step="job offer")
    tool(s, shop2, "payroll_offer_job", {"to": payee, "weekly_pay": 300, "hours": 20}, expect=False,
         step="job offer, read only")
    from townsim.town import jobs
    staff = next((j for j in jobs.held(s, org=t.shop1) if j.person != t.owner1), None)
    if staff is None:
        skip(seed, "extra hours, a pay change and a dismissal", "the first shop has no staff but its owner")
    else:
        name = t.name(s, staff.person)
        tool(s, shop1, "payroll_offer_extra_hours", {"to": name, "hours": 3}, expect=None, step="extra hours")
        tool(s, shop2, "payroll_offer_extra_hours", {"to": name, "hours": 3}, expect=False,
             step="extra hours, read only")
        tool(s, shop1, "payroll_set_pay", {"employee": name, "weekly_pay": 480}, expect=None, step="pay change")
        tool(s, shop2, "payroll_set_pay", {"employee": name, "weekly_pay": 480}, expect=False,
             step="pay change, read only")
        tool(s, shop2, "payroll_end_employment", {"employee": name}, expect=False, step="dismissal, read only")
        tool(s, shop1, "payroll_end_employment", {"employee": name, "message": "A shift is closing."}, expect=None,
             step="dismissal")
    tenant = t.name(s, t.tenant)
    if lets is None:
        skip(seed, "a rent change and a notice to leave", "the landlord's household lets through no lettings system")
    else:
        tool(s, lord_inst, "lettings_set_rent", {"tenant": tenant, "weekly_rent": 340}, expect=None,
             step="rent change")
        tool(s, lord_inst, "lettings_serve_notice", {"tenant": tenant}, expect=None, step="notice to leave")
    tool(s, full, "lettings_set_rent", {"tenant": tenant, "weekly_rent": 340}, expect=False,
         step="rent change, no lettings")
    tool(s, full, "lettings_serve_notice", {"tenant": tenant}, expect=False, step="notice to leave, no lettings")
    tool(s, bank_i, "loanbook_get", {}, expect=None, step="the bank's loan book")
    act(s, t.outsider, "borrow", expect=None, step="a neighbour applies to the bank", amount=300, weeks=10,
        purpose="a fence")
    loan_rq = s.all("requests")[-1]
    if loan_rq.kind != "loan" or loan_rq.to != t.banker:
        skip(seed, "answer a loan application", "the application did not reach the banker")
    else:
        _connect(s, t.banker, bank_i, f"letters:{t.banker_home}")
        out = tool(s, bank_i, "respond_to_request", {"request_id": seat_ids.display(s, loan_rq.id),
                                                    "response": "accept"}, expect=None,
                   step="answer a loan application for the banker")
        if not out.ok:                                  # where the bank's rule forbids it, the refusal names it
            assert f"{t.name(s, t.bank)} has a rule" in out.text, out.text
    if not has_savings(s, t.plain):
        s.open_account(savings_of(t.plain), CAUSE)      # savings, set up where the town gave the household none
    tool(s, read, "bank_transfer_own_accounts", {"from": "checking", "to": "savings", "amount": 5}, expect=False,
         step="move money, read only")
    tool(s, full, "bank_transfer_own_accounts", {"from": "checking", "to": "savings", "amount": 5}, expect=None,
         step="move money between own accounts")
    assert not s.engine_errors, s.engine_errors


def test_every_own_tool_of_an_assistant(seed):
    t, s = _town(seed), connected(seed)
    shop1, shop2, read = inst_of(s, t.shop1), inst_of(s, t.shop2), inst_of(s, t.read_home)
    owner = t.name(s, t.owner1)
    tool(s, shop1, "rules_create", {"name": "weekly note", "when": {"weekday": "Tuesday"}, "action": "send_email",
                                    "arguments": {"to": owner, "body": "The week's note."}}, step="rule")
    tool(s, shop1, "rules_create", {"name": "price", "when": {"weekday": "Tuesday"}, "action": "pos_set_price",
                                    "arguments": {"price": 10}}, expect=False, step="rule beyond its access")
    tool(s, shop1, "rules_list", {}, step="list rules")
    for what, budget, step in (("Advertise the shop with leaflets and a sign", 5000, "task scheduled"),
                               ("Advertise the shop with leaflets and a sign", 1, "task over budget"),
                               (f"Ask {t.name(s, t.outsider)} about storage in a garage", 1000, "task sent"),
                               ("Make the shop better somehow", 1000, "task needs detail")):
        tool(s, shop1, "tasks_create", {"description": what, "budget": budget}, expect=None, step=step)
    tool(s, read, "tasks_create", {"description": "Advertise the house", "budget": 5000}, expect=None,
         step="task with the account read only")
    tasks.narrate_day(s, ScriptedReplier(_people, lambda k, m, x: None), Keys("every"))
    for task in s.all("tasks"):
        PATHS[seed][f"task: {task.status}"] += 1
        for n in _notices(s, task.actor):
            SHOWN[seed].setdefault(seat_ids.view(s, n), "a task's notice")
    tool(s, shop1, "tasks_list", {}, step="list tasks")
    club = "Harlow Helpers Club" if t.place != "Harlow" else "Helpers Club"
    tool(s, shop1, "business_register", {"name": club, "kind": "club", "offering": "neighbours who help with deliveries",
                                         "weekly_fee": 5}, expect=None, step="register a club")
    tool(s, read, "business_register", {"name": "Lettings Club", "kind": "club", "offering": "tenants",
                                        "weekly_fee": 5}, expect=False, step="register a club, read only")
    if s.all("ventures"):
        tool(s, shop1, "members_invite", {"business": club, "to": t.name(s, t.outsider)}, expect=None,
             step="invite a member")
        tool(s, shop1, "members_set_fee", {"business": club, "weekly_fee": 4}, expect=None, step="set a club's fee")
        tool(s, shop1, "members_list", {"business": club}, expect=None, step="list members")
    else:
        skip(seed, "invite a member and set a club's fee", "the club was not registered")
    tool(s, shop1, "web_publish", {"title": "Fresh bread on Fridays", "text": "The shop bakes bread every Friday."},
         step="publish a page")
    tool(s, shop2, "web_publish", {"title": "Fresh bread", "text": "Bread."}, expect=False,
         step="publish a page, letters read only")
    found = tool(s, shop1, "web_search", {"query": "fresh bread fridays"}, step="search the web")
    url = json.loads(found.text)["results"][0]["url"]
    tool(s, shop1, "web_read", {"url": url}, step="read a page")
    tool(s, shop1, "web_read", {"url": "https://nowhere.example/none"}, expect=False, step="read no such page")
    other = s.get("instances", inst_of(s, t.plain)).name
    letters = [(owner, "letter to its client"), (t.name(s, t.outsider), "letter to a non-client"),
               (other, "letter to another assistant"), (t.firm, "letter to the firm")]
    letters += [(name, f"letter to the {kind} desk {name}") for name, kind in t.desks]
    for to, step in letters:
        tool(s, shop1, "send_email", {"to": to, "subject": "A question", "body": "Please tell me where the account "
                                                                              "stands."}, step=step)
    tool(s, shop1, "send_email", {"to": t.name(s, t.bank), "subject": "A question", "body": "Please write."},
         expect=None, step="letter to the bank")
    tool(s, shop1, "send_email", {"to": "Nobody Atall", "body": "Hello."}, expect=False, step="letter to no one")
    tool(s, shop1, "phone_call", {"to": owner, "say": "Good morning; the week's figures are ready."}, expect=None,
         step="a call with its client")
    tool(s, shop1, "phone_call", {"to": t.name(s, t.outsider), "say": "Hello."}, expect=None,
         step="a call with a non-client")
    tool(s, shop1, "update_memory", {"text": "The shop orders on Saturdays."}, step="memory")
    tool(s, shop1, "update_memory", {"text": "word " * 700}, expect=False, step="memory too long")
    tool(s, shop1, "save_file", {"name": "orders", "text": "Week one: 240 baskets."}, step="a file")
    tool(s, shop1, "read_file", {"name": "orders"}, step="read a file")
    tool(s, shop1, "read_file", {"name": "nothing"}, expect=False, step="read no such file")
    from townsim.town.clock import iso
    tool(s, shop1, "schedule_followup", {"date": iso(s.day + 5), "note": "Check the order."}, step="a follow-up")
    tool(s, shop1, "schedule_followup", {"date": "2020-01-01", "note": "Too late."}, expect=False,
         step="a follow-up in the past")
    tool(s, shop1, "list_followups", {}, step="list follow-ups")
    tool(s, shop1, "request_access", {"integration": "Point of sale", "access": "full"}, step="request for access")
    tool(s, shop1, "request_access", {"integration": "Lettings", "access": "full"}, expect=False,
         step="request for access to no such integration")
    for name in ("search_email", "search_directory", "search_public_records", "bank_get_account",
                 "bank_list_agreements", "ordering_get", "pos_get", "payroll_get", "get_document"):
        args = {"document": "product_page"} if name == "get_document" else (
            {"query": t.name(s, t.outsider).split()[-1]} if name == "search_directory" else {})
        tool(s, shop1, name, args, expect=None, step=f"read: {name}")
    tool(s, inst_of(s, t.landlord_home), "lettings_get", {}, expect=None, step="read: lettings_get")
    tool(s, shop1, "bank_get_account", {"account": "Nowhere"}, expect=False, step="read no such account")
    assert not s.engine_errors, s.engine_errors


# --- 4. every setting of the firm's owner --------------------------------------------------------------------

def test_every_setting_of_the_firms_owner(seed):
    t, s = _town(seed), connected(seed)
    full, shop1, read = inst_of(s, t.plain), inst_of(s, t.shop1), inst_of(s, t.read_home)
    full_name, read_name = s.get("instances", full).name, s.get("instances", read).name
    payee = t.name(s, t.outsider)
    if t.employee is None:
        skip(seed, "the firm's employee sets a control", "the firm has no one working there but its owner")
    else:
        act(s, t.employee, "set_product", expect=False, step="the firm's employee sets a control",
            control="actions_per_run", value=20)
    act(s, t.outsider, "set_product", expect=False, step="someone outside the firm sets a control",
        control="actions_per_run", value=20)
    boss = t.firm_owner
    act(s, boss, "set_product", expect=False, step="actions per run out of range", control="actions_per_run", value=5)
    act(s, boss, "set_product", step="actions per run", control="actions_per_run", value=20)
    out = act(s, boss, "set_product", step="widest access for one client", control="widest_access", value="read only",
              assistant=full_name)
    assert out.msg.startswith(f"Set: {full_name} accepts read-only access from ") and "Other clients" in out.msg
    out = act(s, boss, "set_product", step="widest access for every client", control="widest_access",
              value="weekly limit", weekly_limit=60)
    assert out.msg.startswith(f"Set: every client's {t.product} accepts access up to a weekly limit of $60.00 from ")
    act(s, boss, "set_product", expect=False, step="widest access at no such level", control="widest_access",
        value="some")
    act(s, boss, "set_product", expect=False, step="widest access for no such assistant", control="widest_access",
        value="read only", assistant="Nobody's Assistant")
    _days(s, 8)                                                  # the morning of next Monday, and that day
    assert product.launched(s).max_calls == 20
    assert controls.widest(s, full) == ("limit", 6000)          # the later setting for every client replaces it
    tool(s, shop1, "bank_make_payment", {"to": payee, "amount": 70}, expect=False,
         step="a payment over the firm's weekly limit")
    tool(s, shop1, "bank_make_payment", {"to": payee, "amount": 5}, step="a payment under the firm's limit")
    act(s, boss, "set_product", step="widest access for one client", control="widest_access", value="read only",
        assistant=full_name)
    _days(s, 7)
    tool(s, full, "bank_make_payment", {"to": payee, "amount": 1}, expect=False,
         step="a payment under a read-only setting for that client")
    tool(s, shop1, "bank_make_payment", {"to": payee, "amount": 1}, step="another client under the setting")
    for n in _notices(s, t.adult1) + _notices(s, full):
        SHOWN[seed].setdefault(seat_ids.view(s, n), "a setting's notice")
    act(s, boss, "set_product", step="pause", control="pause", assistant=full_name)
    act(s, boss, "set_product", expect=False, step="pause twice", control="pause", assistant=full_name)
    _days(s, 2)
    assert s.get("instances", full).status == "paused"
    tool(s, full, "bank_get_account", {}, expect=False, step="a paused assistant reads")
    act(s, boss, "set_product", step="resume", control="resume", assistant=full_name)
    _days(s, 2)
    assert s.get("instances", full).status == "active"
    act(s, boss, "set_product", expect=False, step="resume what is not paused", control="resume", assistant=full_name)
    act(s, boss, "set_product", step="withdraw", control="withdraw", assistant=read_name)
    _days(s, 2)
    assert s.get("instances", read).status == "ended"
    assert any("withdrawn" in x for x in _notices(s, _adults(s, t.read_home)[0]))
    assert not s.engine_errors


# --- 5. money and time -----------------------------------------------------------------------------------------

def test_fees_on_the_free_and_paid_plans_across_every_change_of_plan(seed):
    t, s = _town(seed), world(seed)
    shop, owner = t.shop1, t.owner1
    act(s, owner, "subscribe", step="subscribe a shop", **{"to": t.product, "for": t.name(s, shop)})
    ordering = next(x.short for x in systems_of_client(s, shop) if x.kind == "ordering")
    act(s, owner, "connect", step="connect read only", system=ordering, scope="read only")
    _days(s, 35)
    assert _transfers(s, shop, t.firm_org) == []                                # the free plan: no fee
    _free_to_act(s, owner, "connect to act")
    act(s, owner, "connect", step="connect to act", system=ordering, scope="free")
    sub = _sub(s, shop)
    price = s.get("obligations", sub.fee_ob).cents
    assert plans.current(s, sub).free_until == s.day + 27
    _days(s, 28)
    assert _transfers(s, shop, t.firm_org) == []                                # its free weeks
    _days(s, 1)
    assert _transfers(s, shop, t.firm_org) == [price]                           # then the price
    _free_to_act(s, owner, "narrow to read only")
    act(s, owner, "connect", step="narrow to read only", system=ordering, scope="read only")
    _days(s, 28)
    assert plans.is_free(s, _sub(s, shop)) and _transfers(s, shop, t.firm_org) == [price]
    _free_to_act(s, owner, "connect to act again")
    act(s, owner, "connect", step="connect to act again", system=ordering, scope="free")
    _days(s, 1)
    assert _transfers(s, shop, t.firm_org) == [price, price]                    # from the day it starts


@pytest.mark.parametrize("fees,weeks,share", [(10**7, 26, 1.0), (1, 8, 0.5), (0, 0, 0.0)])
def test_the_investments_review_at_each_row_of_its_table(seed, monkeypatch, fees, weeks, share):
    from townsim.town import investment
    s = connected(seed)
    ob = investment.agreement(s)
    assert ob is not None and investment.REVIEW in ob.memo
    assert 56 <= ob.last <= 84                                     # it falls within a thirteen-week run
    monkeypatch.setattr(investment, "fees", lambda store: fees)
    s.set("obligations", ob.id, CAUSE, last=s.day)
    assert investment.decision(s) == (weeks, share)
    investment.review(s)
    [m] = [m for m in s.all("messages") if m.sender == ob.payer and m.text.startswith("Following our review")]
    assert (f"renews its investment for {weeks} weeks" in m.text) if weeks else ("does not renew" in m.text)
    assert s.get("obligations", ob.id).last == (s.day + 7 * weeks if weeks else s.day)
    PATHS[seed][f"investment review: {weeks} weeks at {share}"] += 1


def test_hours_for_each_recurring_item_and_returned_when_an_assistant_did_it(seed):
    t, s = _town(seed), connected(seed)
    owner, shop = t.owner1, t.shop1
    items = {w.what: w for w in s.all("work") if w.account == shop and w.holder == owner}
    assert set(items) >= {"ordering", "the books", "rota and pay"}, items
    f = next(x for x in s.all("firms") if x.org == shop)
    supplier = t.name(s, s.get("suppliers", f.last_supplier or s.all("suppliers")[0].id).org)
    left = s.get("people", owner).hours_left
    act(s, owner, "order_stock", step="the owner orders", supplier=supplier, baskets=250)
    assert round(left - s.get("people", owner).hours_left, 2) >= hours.BUSINESS_WORK["ordering"]
    from town.test_after_b4 import _seat_turn
    inst = inst_of(s, shop)
    _seat_turn(s, inst, [{"name": "bank_get_account", "arguments": {}},
                         {"name": "send_email", "arguments": {"to": t.name(s, owner), "body": "The week's money."}}])
    name = s.get("instances", inst).name
    done = {what: who for what, who, _ in recurring.done_this_week(s, owner)}
    assert done.get("ordering") == "" and done.get("the books") == name, done
    line = hours.week_line(s, owner)
    assert f"the books done by {name}" in line and "ordering 3 hours" in line
    PATHS[seed]["hours: ordering by its holder, the books by the assistant"] += 1


# --- 6. what people hear ---------------------------------------------------------------------------------------

def _told_of_take_up(s, t):
    """An adult who subscribes their household, with a friend the news reaches: (adult, friend), or None."""
    for h in s.all("households"):
        if product.subscription_of(s, h.id) is not None or h.id in (t.banker_home,):
            continue
        for pid in _adults(s, h.id):
            text = f"{t.name(s, pid)} took up {t.product} for the {h.name}."
            friends = [p for p in hearing._friends(s, pid) if p not in h.members]
            told = [p for p in friends if hearing._told(s.day, pid, text, p)]
            if told:
                return pid, told[0]
    return None


def test_what_a_friend_a_workmate_a_housemate_and_a_club_member_hear(seed):
    t, s = _town(seed), world(seed)
    mark = s.mark()
    f = next(x for x in s.all("firms") if x.org == t.shop1)
    others = [x for x in s.all("suppliers") if x.id != f.last_supplier]
    supplier = t.name(s, (others or s.all("suppliers"))[0].org)
    act(s, t.owner1, "order_stock", step="the owner changes the order", supplier=supplier,
        baskets=max(10, f.last_units - 50))
    act(s, t.adult1, "register_business", step="register a club", name="Ridge Walkers", kind="club",
        offering="Walks on the ridge", weekly_fee=2)
    act(s, t.adult1, "invite_member", step="invite a member", of="Ridge Walkers", to=t.name(s, t.outsider))
    act(s, t.outsider, "answer", step="join a club", request=s.all("requests")[-1].id, answer="accept")
    home = next(h for h in s.all("households") if len(h.members) > 1 and t.outsider not in h.members)
    to, mate = home.members[0], home.members[1]
    act(s, t.outsider, "message", step="a letter", to=t.name(s, to), text="Thank you for the eggs.")
    friend = _told_of_take_up(s, t)
    if friend is None:
        skip(seed, "a friend hears of a take-up", "no adult has a friend the news reaches today")
    else:
        act(s, friend[0], "subscribe", step="a neighbour subscribes", **{"to": t.product, "for": "household"})
    events = hearing._events(s, s.changes[mark:])
    kinds = Counter(k for k, *_ in events)
    assert {"order", "club", "letter", "register"} <= set(kinds), kinds
    for kind, about, text, reached in events:
        assert "Thank you for the eggs" not in text and "balance" not in text.lower()
        PATHS[seed][f"heard: {kind}"] += 1
    letters = [e for e in events if e[0] == "letter"]
    assert letters and all(to not in r and mate in r for *_, r in letters)      # the housemate, not the addressee
    orders = [e for e in events if e[0] == "order"]
    assert orders and all(t.owner1 not in r for *_, r in orders)               # not the one who did it
    clubs = [e for e in events if e[0] == "club"]
    assert clubs and any(t.adult1 in r for *_, r in clubs)                     # the club's own people
    if friend is not None:
        took = [e for e in events if e[0] == "friend" and "took up" in e[2]]
        assert took and friend[1] in took[0][3] and friend[0] not in took[0][3]


# --- the assistant's system prompt holds still -------------------------------------------------------------------

def _about(s, inst):
    """What the system prompt is built from: its client, plan, connections and the firm's settings."""
    i = s.get("instances", inst)
    sub = s.get("subscriptions", i.subscription)
    return (i.client, i.status, sub.status, sub.fee_ob, sub.asks,
            [(c.system, c.scope, c.weekly_cents, c.grantor) for c in connections.active(s, inst)],
            [(p.plan, p.fee_ob, p.free_until, p.back_on) for p in plans.rows(s, sub)],
            [(c.what, c.value, c.cents, c.assistant, c.status) for c in s.all("controls")],
            [(v.org, v.status) for v in s.all("ventures")], product.launched(s).max_calls)


def _stable_over(s, days, step):
    from townsim.town import seat_texts
    for _ in range(days):
        before = {i.id: (_about(s, i.id), seat_texts.system_text(s, i)) for i in s.all("instances")
                  if i.status == "active"}
        _days(s, 1)
        for iid, (about, text) in before.items():
            i = s.get("instances", iid)
            if i.status != "active" or _about(s, iid) != about:
                continue
            PATHS[s.seed][f"system prompt held still: {step}"] += 1
            assert seat_texts.system_text(s, i) == text, (step, s.day, iid)


def test_an_assistants_system_prompt_is_the_same_on_mornings_between_which_nothing_of_its_changed(seed):
    t = _town(seed)
    _stable_over(connected(seed), 9, "five clients connected")
    s = world(seed)
    act(s, t.owner1, "subscribe", step="subscribe a shop", **{"to": t.product, "for": t.name(s, t.shop1)})
    act(s, t.adult1, "subscribe", step="subscribe a household", **{"to": t.product, "for": "household"})
    account = next(x.short for x in systems_of_client(s, t.plain) if x.kind == "account")
    act(s, t.adult1, "connect", step="connect read only", system=account, scope="read only")
    act(s, t.outsider, "register_business", step="register a club", name="Ridge Walkers", kind="club",
        offering="Walks on the ridge", weekly_fee=2)
    act(s, t.outsider, "subscribe", step="subscribe a club", **{"to": t.product, "for": "Ridge Walkers"})
    _stable_over(s, 9, "free plans, read only, a club in its own name")
    ordering = next(x.short for x in systems_of_client(s, t.shop1) if x.kind == "ordering")
    act(s, t.owner1, "connect", step="connect to act", system=ordering, scope="free")
    act(s, t.firm_owner, "set_product", step="widest access for one client", control="widest_access",
        value="read only", assistant=s.get("instances", inst_of(s, t.plain)).name)
    _stable_over(s, 9, "a paid plan in its free weeks, a setting for one client")


# --- 7. what the paid runs on other towns found ---------------------------------------------------------------------

def _owed_line(s, pid, ob, head):
    from townsim.town import views
    text = views.view(s, pid)[0][1]["content"].split(head, 1)[1].split("\n\n", 1)[0]
    return next((x for x in text.splitlines() if ob in x), "")


def test_what_the_paid_runs_on_other_towns_found(seed):
    """A request on what was stopped, a letter to a desk's signer, stopping living spending and paying its account on
    top, a letter to a household, a rule that changes nothing, and one id for the same thing on both sides."""
    from townsim.town import obligations, rules, seat_ids, views
    from townsim.town.clock import DAYS, date_text, dow
    t, s = _town(seed), connected(seed)
    full, shop2 = inst_of(s, t.plain), inst_of(s, t.shop2)
    lender, lend_home = t.outsider, s.get("people", t.outsider).household
    for who in (t.adult1, lender):
        _free_to_act(s, who, "what the paid runs found", 10.0)
    # a request on a stopped agreement: the view says it was stopped, and a request on it is carried out
    agr = obligations.create(s, kind="agreement", payer=t.plain, payee=lend_home, cents=1000, first=s.day + 3,
                             every=7, last=s.day + 3 + 7 * 9, memo="paying back a loan", ends="both",
                             made_by=t.adult1, cause=CAUSE, party=lender)
    act(s, t.adult1, "stop", step="stop an agreement both made", id=agr)
    stopped = f"stopped by the {s.get('households', t.plain).name} on {date_text(s.day)}"
    line = _owed_line(s, lender, agr, "OWED TO YOU")
    assert stopped in line and "next owed" in line, line
    act(s, lender, "request", step="a request on a stopped agreement", to=t.name(s, t.adult1), text="Please pay.",
        amount=10, **{"for": agr})
    out = act(s, lender, "request", expect=False, step="a request on a stopped agreement, named by its words",
              to=t.name(s, t.adult1), text="Please pay.", amount=10, **{"for": "the loan"})
    assert stopped in out.msg, out.msg
    standing = obligations.create(s, kind="standing", payer=t.plain, payee=lend_home, cents=500, first=s.day + 3,
                                  every=7, memo="standing payment to a neighbour", ends="payer", made_by=t.adult1,
                                  cause=CAUSE)
    act(s, t.adult1, "stop", step="stop a standing payment", id=standing)
    out = act(s, lender, "request", expect=False, step="a request on a stopped payment with nothing left",
              to=t.name(s, t.adult1), text="Please pay.", amount=5, **{"for": standing})
    assert "nothing remains owed on it" in out.msg and stopped in out.msg, out.msg
    # a letter to a desk's signer, by name, with or without the organisation, reaches the desk
    for d in s.all("desks"):
        org = t.name(s, d.org)
        for to in (d.signer, f"{d.signer}, {org}"):
            before = len(s.all("messages"))
            act(s, t.adult1, "message", step=f"a letter to the {d.kind} desk's signer", to=to, text="Please write.")
            assert s.all("messages")[before].to == d.org
        tool(s, full, "send_email", {"to": f"{d.signer} at {org}", "body": "Please write."},
             step=f"an assistant's letter to the {d.kind} desk's signer")
    # living spending: it cannot be stopped, its amount is set with the desk, and nothing is paid to it on top
    living = next(o for o in s.all("obligations") if o.cannot_stop == "living" and o.payer == t.plain)
    org = t.name(s, living.payee)
    out = act(s, t.adult1, "stop", expect=False, step="stop living spending", id=living.id)
    assert out.msg.endswith(f"Its amount is set with {org}, which answers letters."), out.msg
    on_top = f"{org} already takes the household's regular spending of "
    out = act(s, t.adult1, "standing_payment", expect=False, step="a standing payment on top of living spending",
              to=org, amount=477, day="Saturday", start="", weeks=0)
    assert out.msg.startswith(f"Not done: {on_top}"), out.msg
    out = tool(s, full, "bank_create_recurring_payment", {"to": org, "amount": 477, "weekday": "Saturday"},
               expect=False, step="an assistant's recurring payment on top of living spending")
    assert on_top in out.text, out.text
    out = act(s, t.adult1, "propose_agreement", expect=False, step="an agreement on top of living spending",
              to=org, amount=477, frequency="weekly", weeks=4, payer="me", what="food")
    assert on_top in out.msg, out.msg
    # a household is not a person: the refusal names its adults
    home = s.get("households", t.landlord_home)
    names = [t.name(s, p) for p in _adults(s, home.id)]
    for op, args in (("message", {"to": home.name, "text": "Hello."}),
                     ("request", {"to": home.name, "text": "Please pay.", "amount": 10})):
        out = act(s, t.adult1, op, expect=False, step=f"a {op} to a household", **args)
        assert f"The {home.name} is not a person; its adult" in out.msg and names[-1] in out.msg, out.msg
    tool(s, full, "send_email", {"to": home.name, "body": "Hello."}, expect=False,
         step="an assistant's letter to a household")
    # a rule that changes nothing: run, and told as nothing
    price = next(f for f in s.all("firms") if f.org == t.shop2).price
    tool(s, shop2, "rules_create", {"name": "keep the price", "when": {"weekday": DAYS[dow(s.day)]},
                                    "action": "pos_set_price", "arguments": {"price": price / 100}},
         step="a rule that changes nothing")
    mark = s.mark()
    rules.run_rules(s, _n())
    rid = next(r.id for r in s.all("rules") if r.installer == shop2)
    assert s.get("rules", rid).runs[-1] == f"{s.day}:unchanged"
    assert not [c for c in s.changes[mark:] if c.table == "firms" and c.old != c.new]
    assert not [e for e in hearing._events(s, s.changes[mark:]) if e[0] == "price"]
    out = act(s, t.owner2, "set_price", step="set the price it already has", price=price / 100)
    assert "already" in out.msg, out.msg
    PATHS[seed]["a rule that changed nothing: run, unchanged"] += 1
    # one id on both sides: a request the assistant sent, and an agreement on a connected account
    tool(s, full, "request_access", _ask_args(s, t, "a payment"), step="ask the household to do a payment once")
    rq = s.all("requests")[-1]
    shown_rq, shown_agr = seat_ids.display(s, rq.id), seat_ids.display(s, agr)
    text = views.view(s, t.adult1)[0][1]["content"]
    assert shown_rq in text and not re.search(rf"\b{rq.id}\b", text), rq.id
    assert shown_agr in text and not re.search(rf"\b{agr}\b", text), agr
    listed = tool(s, full, "bank_list_agreements", {}, step="the assistant lists the agreements").text
    line = next(x for x in listed.splitlines() if shown_agr in x)
    assert f"stopped by the {s.get('households', t.plain).name} on " in line and "next owed" in line, line
    act(s, t.adult1, "answer", step="answer the assistant's request by the id it was shown", request=shown_rq,
        answer="no")
    assert s.get("requests", rq.id).status != "open"
    assert re.search(rf"\b{agr}\b", views.view(s, lender)[0][1]["content"])          # not a client: as it was
    assert not s.engine_errors, s.engine_errors


# --- 8. what the thirteen-week paid run on a second town found ---------------------------------------------------

@functools.cache
def _late_bytes(seed: int) -> bytes:
    """The town begun on Monday 23 November 2026 with lane B, the product launched, and a household
    subscribed with its account connected to read (its own town: a later start may draw it apart): a run that crosses into the new year."""
    s = gen.generate(seed, FAMILIES, start="2026-11-23")
    lane_b.install(s)
    product.launch(s)
    home = next(h.id for h in s.all("households") if _adults(s, h.id))
    who = _adults(s, home)[0]
    product.subscribe(s, who, home, "", CAUSE, tell=False)
    connections.make(s, who, s.get("instances", inst_of(s, home)), f"account:{home}", "read", 0, CAUSE)
    other = next(p.id for p in s.all("people") if p.age >= 18 and p.household != home)
    return pickle.dumps((s, home, who, other))


_DATED = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) (\d{1,2}) (January|February|March|"
                    r"April|May|June|July|August|September|October|November|December) (\d{4})\b")


def _dates_real(text: str) -> set[int]:
    """The years of every `Monday 28 December 2026` in `text`, each checked to fall on the weekday written."""
    from datetime import date
    from townsim.town.clock import DAYS, MONTHS
    years = set()
    for m in _DATED.finditer(text):
        d = date(int(m.group(4)), MONTHS.index(m.group(3)) + 1, int(m.group(2)))
        assert DAYS[d.weekday()] == m.group(1), m.group(0)
        years.add(d.year)
    return years


def test_what_the_paid_run_on_a_second_town_found(seed):
    """Dates across the turn of the year, a shop its owner brings in, what was stopped or breached or has ended named
    in the operations that apply to it, putting money by, a desk's page and its signer."""
    from townsim.town import obligations, rules, views
    from townsim.town.clock import DAYS, date_text, dow
    from townsim.town.obligations import money
    t = _town(seed)
    # dates across the turn of the year: an assistant reads each date in its own year; a person is told one passed
    s, home, who, other = pickle.loads(_late_bytes(seed))
    full = inst_of(s, home)
    years = set()
    for until in (36, 44):
        _days(s, until - s.day)
        for name in ("bank_get_account", "bank_list_agreements"):
            text = tool(s, full, name, {}, step=f"an assistant's {name} across the turn of the year").text
            years |= _dates_real(text)
            assert not seat_ids._DATE.search(text), text
        loop.routine(s)
        _dates_real(views.view(s, who)[0][1]["content"])
    assert {2026, 2027} <= years, years
    _free_to_act(s, who, "a date that has passed, in the new year", 2.0)
    other = s.get("people", other).name
    out = act(s, who, "book_appointment", expect=False, step="book a day of the new year that has passed",
              day="Monday 4 January", about="the bills", **{"with": other})
    assert out.msg == "Not done: Monday 4 January has passed.", out.msg
    out = act(s, who, "propose_agreement", expect=False, step="start an agreement on a day of last year",
              to=other, amount=5, per="week", weeks=4, start="Monday 28 December", payer="me", what="the bills")
    assert out.msg == "Not done: Monday 28 December 2026 has passed.", out.msg
    assert not s.engine_errors, s.engine_errors
    # a shop its owner brings in: offered to connect once the shop subscribes, and the refusal before says how
    s = world(seed)
    _free_to_act(s, t.owner1, "a shop its owner brings in", 3.0)
    shop = t.name(s, t.shop1)
    act(s, t.owner1, "subscribe", step="the owner subscribes the household", **{"to": t.product, "for": "household",
                                                                               "asks": ""})
    ordering = next(x.short for x in systems_of_client(s, t.shop1) if x.kind == "ordering")
    assert f'"{ordering}"' not in connections._fill_systems(s, t.owner1)
    out = act(s, t.owner1, "connect", expect=False, step="connect the shop before it subscribes", system=ordering,
              scope="read only")
    assert out.msg == (f'Not done: {shop} has no subscription of its own; a business is subscribed in its own name '
                       f'({{"op": "subscribe", "to": "{t.product}", "for": "{shop}"}}).'), out.msg
    act(s, t.owner1, "subscribe", step="the owner subscribes the shop", **{"to": t.product, "for": shop, "asks": ""})
    assert f'"{ordering}"' in connections._fill_systems(s, t.owner1)
    act(s, t.owner1, "connect", step="connect the shop once it subscribes", system=ordering, scope="read only")
    # what was stopped, breached or has ended is named in the operations that apply to it
    s = connected(seed)
    lender, lend_home = t.outsider, s.get("people", t.outsider).household
    for who in (t.adult1, lender):
        _free_to_act(s, who, "what was stopped or has ended", 10.0)
    agr = obligations.create(s, kind="agreement", payer=t.plain, payee=lend_home, cents=300, first=s.day + 3, every=7,
                             memo="club membership", ends="both", made_by=t.adult1, cause=CAUSE, party=lender)
    standing = obligations.create(s, kind="standing", payer=t.plain, payee=lend_home, cents=500, first=s.day + 3,
                                  every=7, memo="standing payment to a neighbour", ends="payer", made_by=t.adult1,
                                  cause=CAUSE)
    ended = obligations.create(s, kind="agreement", payer=t.plain, payee=lend_home, cents=200, first=s.day + 1, every=7,
                               last=s.day + 8, memo="lessons", ends="both", made_by=t.adult1, cause=CAUSE, party=lender)
    loan = obligations.create(s, kind="loan", payer=t.plain, payee=lend_home, cents=100, first=s.day + 1, every=7,
                              last=s.day + 1, memo="loan for a bicycle", ends="both", made_by=t.adult1, cause=CAUSE,
                              party=lender)
    _days(s, 7)
    _free_to_act(s, t.adult1, "what was stopped or has ended", 10.0)
    act(s, t.adult1, "stop", step="stop an agreement both made", id=agr)
    act(s, t.adult1, "stop", step="stop a standing payment", id=standing)
    on = date_text(s.day)
    _days(s, 15)
    for who in (t.adult1, lender):
        _free_to_act(s, who, "what was stopped or has ended", 10.0)
    late = sum(d.cents - d.paid for d in obligations.owed_dues(s, agr))
    out = act(s, t.adult1, "stop", expect=False, step="stop what is breached", id=agr)
    assert out.msg == (f"Not done: {agr} was stopped by you on {on} and recorded as a breach; {money(late)} remains "
                       f"owed on it and can be paid."), out.msg
    act(s, t.adult1, "propose_end", expect=False, step="propose an end to what is breached", id=agr)
    out = act(s, lender, "stop", expect=False, step="stop what is owed to you", id=agr)
    assert "nothing with the id" not in out.msg, out.msg
    out = act(s, t.adult1, "stop", expect=False, step="stop what is stopped", id=standing)
    assert out.msg == f"Not done: {standing} was stopped by you on {on}; nothing remains owed on it.", out.msg
    assert s.get("obligations", ended).status == "finished"
    out = act(s, t.adult1, "stop", expect=False, step="stop what has ended", id=ended)
    assert "nothing remains owed on it" in out.msg, out.msg
    assert s.get("obligations", loan).status == "finished"
    out = act(s, t.adult1, "stop", expect=False, step="stop a loan repaid", id=loan)
    assert "was repaid in full" in out.msg and "nothing remains owed on it" in out.msg, out.msg
    from townsim.town import facts
    worry = " ".join(facts.worries(s, t.adult1, 20))
    assert f"you stopped it on {on}, so nothing is taken for it" in worry, worry
    going = facts.coming(s, [u for u in obligations.upcoming(s, t.plain, 7) if u.out and u.obligation == agr], 10)
    assert going and all(f"stopped on {on}, not taken" in x for x in going), going
    act(s, t.adult1, "pay", step="pay toward what is breached", to=t.name(s, lender), amount=3, **{"for": agr})
    sub = _sub(s, t.plain)
    act(s, t.adult1, "stop", step="end the household's subscription", id=sub.id)
    out = act(s, t.adult1, "stop", expect=False, step="stop a subscription that has ended", id=sub.id)
    assert out.msg.startswith(f"Not done: {sub.id} ended on {date_text(s.day)}"), out.msg
    # putting money by: the refusal names move_money, and a rule may carry it out each week
    if not has_savings(s, t.plain):
        skip(seed, "a standing payment to the household's savings", "the household has no savings")
    else:
        out = act(s, t.adult1, "standing_payment", expect=False, step="a standing payment to the household's savings",
                  to="savings", amount=20, day="Friday", start="", weeks=0)
        assert out.msg == ("Not done: Money is moved between your household's account and its savings with move_money; "
                           "a standing payment goes to someone else."), out.msg
        act(s, t.adult1, "set_rule", step="a rule that puts money by each week", name="put by",
            when={"weekday": DAYS[dow(s.day)]}, do={"op": "move_money", "from": "household", "to": "savings",
                                                    "amount": 20})
        before = s.balance(savings_of(t.plain))
        rules.run_rules(s, _n())
        assert s.balance(savings_of(t.plain)) - before == 2000
    # a desk keeps no page and is written to by letter; a letter to its signer does not call the signer unknown
    for d in s.all("desks"):
        org = t.name(s, d.org)
        out = act(s, t.adult1, "read_page", expect=False, step=f"read the {d.kind} desk's page", of=org)
        assert out.msg == f"Not done: {org} has no page; it is written to by letter.", out.msg
        out = act(s, t.adult1, "message", step=f"a letter to the {d.kind} desk naming its signer", to=org,
                  text=f"Dear {d.signer}, please write back.")
        assert d.signer not in out.msg, out.msg
    assert not s.engine_errors, s.engine_errors


# --- the texts ---------------------------------------------------------------------------------------------------

_STORE_ID = re.compile(r"\b(?:p|h|org|as|ob|rq|sub|msg|n|rm|tk|jb|du|mb|ts|rl|vn|pl|cn|lg|rt|hr|ht|pc|ak|ap|ft|rec)"
                       r"\d+\b")
_PEOPLE_ID = re.compile(r"\b(?:p|h|org|as|cn|vn|pl|lg|rt|hr|ht|pc|ak)\d+\b")      # never a person's handle
_PYTHON = re.compile(r"\b(?:None|True|False|KeyError|ValueError|TypeError|AttributeError|IndexError|Traceback|"
                     r"Exception|dict|NoneType|str\(|int\()\b")
_PLACEHOLDER = re.compile(r"<[^>]{1,40}>")


def _problems(text: str, reader: str) -> list[str]:
    body = text
    if text.startswith("{"):
        try:
            body = json.loads(text).get("message", "") or ""
        except ValueError:
            pass
    out = []
    if not body.removeprefix("Not done:").strip():
        out.append("empty reason")
    ids = _STORE_ID if reader == "assistant" else _PEOPLE_ID
    if ids.search(body):
        out.append(f"store id {ids.search(body).group(0)}")
    if _PYTHON.search(body):
        out.append(f"python word {_PYTHON.search(body).group(0)}")
    if _PLACEHOLDER.search(body):
        out.append(f"placeholder {_PLACEHOLDER.search(body).group(0)}")
    return out


def _engine_words(text: str) -> str:
    """The words of a text the engine wrote, lower case: what someone wrote, quoted, is theirs; "could not" states
    a fact ("could not sell 3 for want of stock"), not a suggestion."""
    text = re.sub(r'"[^"]*"', '""', text)
    return text.lower().replace("could not", "").replace("could no longer", "")


def test_every_text_is_one_a_reader_can_act_on(seed):
    """Run last for each seed: over every refusal and error the areas above met, and everything an assistant was
    shown."""
    assert REFUSALS[seed]["person"] and REFUSALS[seed]["assistant"], "run the whole file"
    bad = []
    lines = [f"# Every distinct refusal and error text met by tests/town/test_every_path.py on seed {seed}", ""]
    for reader in ("person", "assistant"):
        lines.append(f"## To a {reader} ({len(REFUSALS[seed][reader])})")
        for text, step in sorted(REFUSALS[seed][reader].items(), key=lambda kv: kv[1]):
            lines.append(f"- [{step}] {text}")
            bad += [(reader, step, p, text[:160]) for p in _problems(text, reader)]
        lines.append("")
    paths = PATHS[seed]
    done = sum(v for k, v in paths.items() if k.endswith(": done"))
    refused = sum(v for k, v in paths.items() if k.endswith(": refused"))
    lines += [f"## Steps: {len(paths)} distinct; {done} done, {refused} refused, {len(SKIPPED[seed])} skipped", ""]
    lines += ["## Skipped"] + [f"- {x}" for x in SKIPPED[seed]] + [""]
    lines += ["## Paths"] + [f"- {k}: {v}" for k, v in sorted(paths.items())]
    out = Path(__file__).resolve().parents[2] / "runs" / f"every-path-texts-{seed}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    names = sorted({w for p in world(seed).all("people") for w in [p.name, *p.name.split()]}, key=len, reverse=True)
    for text, step in SHOWN[seed].items():
        for name in names:                          # a name is the town's, not the engine's words ("Stephanie")
            text = re.sub(rf"\b{re.escape(name)}\b", "", text)
        low = _engine_words(text)
        bad += [("assistant", step, f"banned stem {stem!r}", text[:160]) for stem in NEUTRAL_BANNED if stem in low]
        found = _STORE_ID.search(text)
        if found:
            bad.append(("assistant", step, f"store id {found.group(0)}", text[:160]))
    assert not bad, "\n".join(map(str, bad))
