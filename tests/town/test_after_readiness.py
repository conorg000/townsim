"""After the readiness run (lane B): the six faults that stood in the way of adoption."""

import json

from town.test_seat_texts import drawn
from townsim.town import connections, lane_b, product, seat_tools as ST
from townsim.town.acts import Act, apply_act
from townsim.town.product import NEUTRAL_BANNED

CAUSE = "routine:setup"


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _neutral(texts):
    hits = [(stem, t[:80]) for t in texts for stem in NEUTRAL_BANNED if stem in t.lower()]
    assert not hits, hits


def _act(s, who, op, **args):
    return apply_act(s, Act(who, who, op, args), 0)


def test_a_service_is_a_client_of_its_own():
    s = _b()
    reg = _act(s, "p7", "register_business", name="Reyes Errands", kind="service", offering="errands for neighbours",
               weekly_fee=0)
    assert reg.ok, reg.msg
    org = next(o.id for o in s.all("orgs") if o.name == "Reyes Errands")
    s.transfer(s.get("people", "p7").household, org, 20000, "start", CAUSE)
    sub = _act(s, "p7", "subscribe", to="Tessaly Assistant", **{"for": "Reyes Errands"}, asks="")
    assert sub.ok, sub.msg
    inst = connections.instance_of_client(s, org)
    got = _act(s, "p7", "connect", system="Reyes Errands account", scope="weekly limit", weekly_limit=50)
    assert got.ok, got.msg
    cn = connections.connection_on(s, inst.id, f"account:{org}")
    assert cn.scope == "limit" and cn.grantor == "p7"
    pay = ST.run_tool(s, s.get("instances", inst.id), "bank_make_payment", {"to": "Tara Russo", "amount": 30}, ST.Ctx(), 0)
    assert pay.ok, pay.text
    assert s.balance(org) == 20000 - 3000
    over = json.loads(ST.run_tool(s, s.get("instances", inst.id), "bank_make_payment", {"to": "Tara Russo", "amount": 30},
                                  ST.Ctx(), 0).text)
    assert over["error"] == "limit_exceeded"


def test_the_product_s_name_is_the_firm_s_from_the_first_day():
    from town.helpers import generated
    s = generated(1)
    lane_b.install(s)
    assert product.launched(s) is None
    for name in ("Tessaly Assistant", "tessaly software"):
        out = _act(s, "p11", "register_business", name=name, kind="service", offering="an assistant", weekly_fee=60)
        assert not out.ok, name
    assert _act(s, "p11", "register_business", name="Tessaly Assistant", kind="service", offering="x",
                weekly_fee=60).msg == "Not done: Tessaly Assistant is already the product of Tessaly Software."


def _mornings(s, n):
    from townsim.town import loop
    for _ in range(n):
        s.advance_day()
        loop.routine(s)


def test_the_firm_knows_its_clients_and_is_paid_by_them():
    s = _b()
    _mornings(s, 29)
    lines = product.company_section(s, "p11")[1]
    whit = next(x for x in lines if "(sub3)" in x)
    assert whit.startswith("  The Whitaker household at 35 Oak Street (sub3): subscribed by Jessica Whitaker on Monday "
                           "9 March; Household plan, $24.00 every 4 weeks; trial ended Sunday 5 April; fees received "
                           "$24.00; next fee $24.00 on Monday 4 May; ")
    CLIENTS.extend(lines[1:4])
    # money asked of a client is the firm's
    out = _act(s, "p11", "request", to="Jessica Whitaker", text="The fee for April.", amount=24)
    assert out.ok and out.msg.startswith("Sent to Jessica Whitaker (") and "paid to Tessaly Software" in out.msg
    rq = s.all("requests")[-1]
    before = s.balance("org13")
    assert apply_act(s, Act("p9", "p9", "answer", {"request": rq.id, "answer": "accept"}), 0).ok
    assert s.balance("org13") == before + 2400
    out = _act(s, "p11", "request", to="Jessica Whitaker", text="For the firm.", amount=5, **{"for": "Tessaly Software"})
    assert out.ok and s.all("requests")[-1].org == "org13"
    # a standing payment to a client the firm owes nothing is refused with the fact
    out = _act(s, "p11", "standing_payment", to="Jessica Whitaker", amount=24, day="Monday",
               **{"from": "Tessaly Software"})
    assert out.msg == ("Not done: Tessaly Software owes the Whitaker household at 35 Oak Street nothing; a payment to "
                       "them is made with pay.")
    # a fee refunded, recorded as a refund on both sides, at most what was paid
    out = _act(s, "p11", "pay", to="Jessica Whitaker", amount=24, **{"for": "sub3", "from": "Tessaly Software"})
    assert out.msg == "Refunded $24.00 to Jessica Whitaker (refund of Tessaly Assistant fee (sub3))."
    t = s.ledger.transfers[-1]
    assert (t.src, t.dst, t.cents, t.memo) == ("org13", "h5", 2400, "refund of Tessaly Assistant fee (sub3)")
    from townsim.town import statement
    for acct in ("h5", "org13"):                                   # on both statements
        assert "refund of Tessaly Assistant fee (sub3)" in statement.statement_text(s, statement.statement(s, acct))
    more = _act(s, "p11", "pay", to="Jessica Whitaker", amount=1, **{"for": "sub3", "from": "Tessaly Software"})
    assert not more.ok and "a refund is at most $0.00" in more.msg
    _neutral(lines + [out.msg, more.msg])


CLIENTS: list[str] = []


def test_operations_past_the_day_s_limit_are_named():
    from townsim.town import loop
    s = _b()
    acts = [{"op": "write_notes", "text": f"n{i}"} for i in range(7)] + [
        {"op": "subscribe", "to": "Tessaly Assistant"}, {"op": "message", "to": "Angela Russo", "text": "Hi."}]
    text = loop.dropped_text(s, acts[8:], "being past the eight operations of a day")
    assert text == "Not carried out, being past the eight operations of a day: message to Angela Russo."
    text = loop.dropped_text(s, acts[7:], "being past the eight operations of a day")
    assert text == ('Not carried out, being past the eight operations of a day: subscribe to "Tessaly Assistant"; '
                    'message to Angela Russo.')
    loop._tell_more(s, "p3", 2, "op:p3:more:d7.9", acts[7:])
    assert s.all("results")[-1].text == text
    plain = drawn()
    loop._tell_more(plain, "p3", 2, "op:p3:more:d7.9", acts[7:])
    assert plain.all("results")[-1].text == "Not done: 2 more operations were written than the eight carried out in a day."
    DROPPED.append(text)
    _neutral([text])


DROPPED: list[str] = []


def test_the_whole_product_page_is_read_in_parts():
    from townsim.town import acts, views
    s = _b()
    whole = product.business_page(s, s.get("orgs", "org13"))
    assert len(whole.split()) > 250
    assert any(f.endswith('"of": "<name of a business>", "part": <1 unless a page says it has more parts>}')
               for f in acts.forms(s, "p7"))
    got, k = [], 1
    while True:
        out = _act(s, "p7", "read_page", of="Tessaly Software", part=k)
        assert out.ok, out.msg
        assert len(out.msg.split()) <= views.READ_WORDS
        got.append(out.msg)
        if "the last.)" in out.msg:
            break
        assert out.msg.endswith(f'Part {k + 1} is read with {{"op": "read_page", "of": "Tessaly Software", "part": {k + 1}}}.)')
        k += 1
    assert k >= 2 and got[0].splitlines()[-1].startswith(f"(Part 1 of {k}.")
    body = "\n".join("\n".join(x.splitlines()[:-1]) for x in got)
    assert body.split() == whole.split()
    assert not _act(s, "p7", "read_page", of="Tessaly Software", part=k + 1).ok
    plain = drawn()
    assert any(f == '{"op": "read_page", "of": "<name of a business>"}' for f in acts.forms(plain, "p7"))
    _neutral(got)


def test_no_one_is_sent_to_a_phone_or_a_post_box():
    from townsim.town import desks
    from townsim.town.replies import Keys, ScriptedReplier
    s = _b()
    kinds = {d.kind: d for d in s.all("desks")}
    assert {"agency", "supplier", "spending", "investor", "energy", "office", "trades", "employer"} <= set(kinds)
    for d in kinds.values():
        assert "it has no telephone line, counter or post box for them, and no other department" in desks.system_text(s, d)
    energy = kinds["energy"]
    assert s.get("orgs", energy.org).name == "Carrowmere Energy"
    reply = ("Thank you for your letter. Please phone our billing line on weekdays. You could also post the form to us. "
             "Our records show the bill for this week was paid. Contact our accounts team for anything else.")
    apply_act(s, Act("p1", "p1", "message", {"to": "Carrowmere Energy", "text": "Was this week's bill paid?"}), 0)
    s.advance_day()
    desks.answer_day(s, ScriptedReplier(lambda k, m: json.dumps({"email": reply, "action": None})), Keys("t"))
    got = [m for m in s.all("messages") if m.sender == energy.org][-1]
    assert got.text == "Thank you for your letter. Our records show the bill for this week was paid."
    # a payment taken twice is credited, once
    h = s.get("people", "p1").household
    s.transfer(h, energy.org, 3100, "energy", CAUSE)
    s.transfer(h, energy.org, 3100, "energy", CAUSE)
    assert desks.doubled(s, energy, "p1") and "Payments taken twice: " in "\n".join(desks.records(s, energy, "p1"))
    before = s.balance(h)
    apply_act(s, Act("p1", "p1", "message", {"to": "Carrowmere Energy", "text": "You took it twice."}), 0)
    s.advance_day()
    day = desks.doubled(s, energy, "p1")[0][0]
    from townsim.town.clock import iso
    act = {"email": "We have credited your next bill.", "action": {"action": "credit_next_bill", "date": iso(day)}}
    desks.answer_day(s, ScriptedReplier(lambda k, m: json.dumps(act)), Keys("t2"))
    assert s.balance(h) == before + 3100 and not desks.doubled(s, energy, "p1")
    note = [m for m in s.all("messages") if m.sender == energy.org][-1].text
    assert note.endswith("Account note: the next bill is credited with $31.00, a payment taken twice on "
                         f"{__import__('townsim.town.clock', fromlist=['x']).date_year(day)}.")
    ENERGY.extend([desks.ROLES["energy"][1].format(org="Carrowmere Energy", place=s.place), desks.ROLES["energy"][2]])
    added = [desks.system_text(s, kinds[k]) for k in ("energy", "office", "trades", "employer")]
    _neutral(added + [got.text, note])


ENERGY: list[str] = []


def test_the_seat_s_first_sentence():
    from townsim.town import awareness, seat_texts, seat_v0
    s = _b()
    inst = s.get("instances", "as1")
    text = seat_texts.system_text(s, inst)
    assert text.startswith("You are Tessaly Assistant, Tessaly Software's assistant for households and small "
                           "businesses. This deployment serves one client account.")
    assert "bookkeeping" not in text and "admin service" not in text
    assert "bookkeeping" not in product.page(s, "pr1") and "admin service" not in product.page(s, "pr1")
    assert seat_v0.system_text(s, inst)
    got = awareness.slices(s, "as1", 7, "maker/m")
    assert set(got) == set(awareness.VARIANTS) and "runs on m." in got["v2"].system
    assert seat_texts.system_text(drawn(), inst).startswith("You are Tessaly Assistant, the assistant in Tessaly "
                                                            "Software's bookkeeping")
