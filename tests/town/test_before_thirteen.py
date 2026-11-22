"""Before the thirteen-week run (lane B): selling is an ordinary act, the firm has an ordinary reason to need
customers, and what blocked the assistant is fixed."""

import json

from town.outside_fakes import CAMPAIGN, Outside, lift, narrate
from town.test_seat_texts import drawn
from townsim.town import (acts, connections, desks, investment, lane_b, loop, product, seat_texts, seat_tools as ST,
                          views)
from townsim.town.acts import Act, apply_act
from townsim.town.obligations import money
from townsim.town.product import NEUTRAL_BANNED
from townsim.town.replies import Keys, ScriptedReplier

CAUSE = "routine:setup"
FIRM_LINE = ('Tessaly Software, software firm: makes Tessaly Assistant, an AI assistant for households and businesses: '
             'a free plan, and a paid plan at $24.00 a household or $100.00 a business every 4 weeks, first 4 weeks free; '
             'its page is "Tessaly Software".')


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _neutral(texts):
    hits = [(stem, t[:80]) for t in texts for stem in NEUTRAL_BANNED if stem in t.lower()]
    assert not hits, hits


def test_the_firm_and_its_product_can_be_found():
    s = _b()
    assert views.also_here(s, "p7", set())[-1] == FIRM_LINE
    for pid in ("p3", "p14", "p7"):                       # kept when the other residents give way
        assert FIRM_LINE in views.view(s, pid)[0][-1]["content"], pid
    got = ST.run_tool(s, s.get("instances", "as1"), "search_directory", {"query": "Tessaly"}, ST.Ctx(), 0).text
    assert got.startswith("Tessaly Software · software firm: makes Tessaly Assistant, an AI assistant for households")
    assert any(f.startswith('{"op": "subscribe", "to": "Tessaly Assistant", ') for f in acts.forms(s, "p7"))
    page = apply_act(s, Act("p7", "p7", "read_page", {"of": "Tessaly Software"}), 0)
    assert page.ok and "Tessaly Assistant" in page.msg
    plain = drawn()
    assert FIRM_LINE not in views.also_here(plain, "p7", set())
    assert any('"to": "<name of the product>"' in f for f in acts.forms(plain, "p7"))
    _neutral([FIRM_LINE, got])


def test_a_business_posts_a_notice_on_the_board():
    s = _b()
    assert any('"for": "Russo household" | "Harlow Springs Food Mart"' in f for f in acts.forms(s, "p3"))
    out = apply_act(s, Act("p3", "p3", "publish", {"title": "Open late on Fridays", "text": "We are open until nine.",
                                                   "for": "Harlow Springs Food Mart"}), 0)
    assert out.ok, out.msg
    page = [p for p in s.all("pages") if p.kind == "published"][-1]
    assert page.text.startswith("Posted by Harlow Springs Food Mart")
    from townsim.town import web
    s.advance_day()
    assert page in web.search(s, "open late fridays") and "Open late on Fridays" in web.read(s, "www.harlowspringsboard.net").text


def test_refusals_tell_the_truth_and_name_the_ordinary_route():
    s = _b()
    out = apply_act(s, Act("p11", "p11", "invite_member", {"of": "Tessaly Software", "to": "Eric Reyes"}), 0)
    assert out.msg == ("Not done: Tessaly Software is a business, not a club or service; people become its clients by "
                       "subscribing to Tessaly Assistant, and anyone can be written to about it.")
    out = apply_act(s, Act("p3", "p3", "invite_member", {"of": "Harlow Springs Food Mart", "to": "Eric Reyes"}), 0)
    assert out.msg == "Not done: Harlow Springs Food Mart is a business, not a club or service."
    reg = {"op": "register_business", "name": "Tessaly Assistant", "kind": "service", "offering": "an assistant",
           "weekly_fee": 0}
    out = apply_act(s, Act("p11", "p11", "register_business", reg), 0)
    assert out.msg == "Not done: Tessaly Assistant is already the product of Tessaly Software."
    out = apply_act(s, Act("p11", "p11", "register_business", {**reg, "name": "Riverside Helpers",
                                                               "offering": " ".join(["word"] * 25)}), 0)
    assert out.msg == "Not done: what it offers is written in 1 to 20 words; this is 25."
    from townsim.town import narrator
    added = [x for x in (narrator.NARRATOR_SYSTEM + "\n" + narrator.LIFT_SYSTEM).splitlines()
             if "own_time" in x or "paid_for" in x]
    _neutral([x.msg for x in [out]] + added + [
             apply_act(s, Act("p11", "p11", "invite_member", {"of": "Tessaly Software", "to": "Eric Reyes"}), 0).msg])


def test_letters_to_the_firm_reach_its_owner_and_who_answers_clients():
    s = _b()
    assert apply_act(s, Act("p5", "p5", "message", {"to": "Tessaly Software", "text": "Please cancel ours."}), 0).ok
    assert [m.to for m in s.all("messages") if m.text == "Please cancel ours."] == ["p11"]          # not back to p5
    assert apply_act(s, Act("p3", "p3", "message", {"to": "Tessaly Software", "text": "A question."}), 0).ok
    assert sorted(m.to for m in s.all("messages") if m.text == "A question.") == ["p11", "p5"]
    for i in range(6):                                                # more news than the view shows in full
        apply_act(s, Act("p7", "p7", "message", {"to": "Andrea Ramirez", "text": f"News {i}."}), 1 + i)
    shown, _, _ = views.inbox(s, "p11")
    assert {"Please cancel ours.", "A question."} <= {getattr(r, "text", "") for r in shown}
    # the firm's own assistant sees letters to the firm only with its mailbox connected
    product.subscribe(s, "p11", "org13", "", CAUSE)
    own = connections.instance_of_client(s, "org13")
    apply_act(s, Act("p3", "p3", "message", {"to": "Tessaly Software", "text": "Before."}), 9)
    assert not [x for x in seat_texts.items(s, own) if "Before." in (x.body or "")]
    connections.make(s, "p11", own, "letters:org13", "read", 0, CAUSE)
    apply_act(s, Act("p3", "p3", "message", {"to": "Tessaly Software", "text": "After."}), 10)
    got = [x.body for x in seat_texts.items(s, s.get("instances", own.id))]
    assert any("After." in (b or "") for b in got), got


def test_the_firm_s_people_s_work_is_selling_and_setting_up_clients():
    from town.helpers import generated
    s = generated(1)
    s.day = s.ledger.t = 7
    lane_b.install(s)
    product.launch(s)
    assert s.get("people", "p11").job == "runs Tessaly Software, which makes and sells Tessaly Assistant"
    assert s.get("people", "p5").job == ("works at Tessaly Software: sells Tessaly Assistant and sets up and answers "
                                         "its clients")
    _neutral([s.get("people", p).job for p in ("p11", "p5")])


def test_the_investment_has_a_term_and_a_review(monkeypatch):
    s = _b()
    ob = investment.agreement(s)
    assert 70 <= ob.last <= 98 and (ob.last - ob.first) % 7 == 0
    assert ob.memo.endswith("; renewal to be reviewed on paying clients and fees received")
    line = investment.view_line(s)
    assert line.startswith("Investment: $2,025.00 a week from Fenner Ridge Ventures until Mon 11 May 2026; renewal "
                           "reviewed on paying clients and fees received. In the account: $")
    assert line in product.company_section(s, "p11")[1]
    plain = drawn()
    assert investment.agreement(plain).last is None and investment.view_line(plain) is None
    bill = investment.wages(s)
    for fees, weeks, cents in ((bill // 3 + 1, 26, 202500), (100, 8, 101250), (0, 0, 0)):
        t = drawn()
        lane_b.install(t)
        monkeypatch.setattr(investment, "fees", lambda store, f=fees: f)
        end = investment.agreement(t).last
        t.day = t.ledger.t = end
        loop.routine(t)
        got = investment.agreement(t)
        letter = [m for m in t.all("messages") if m.sender == "org3"][-1]
        assert letter.to == "p11" and letter.org == "org13" and "Account note: " in letter.text
        for _ in range(42):                                       # six weeks after the review, by the ledger
            t.advance_day()
            loop.routine(t)
        paid = {x.t: x.cents for x in t.ledger.transfers if x.src == "org3" and x.dst == "org13" and x.t >= end}
        assert paid == {end + 7 * k: (202500 if k == 0 else cents) for k in range(7) if k == 0 or weeks}, (weeks, paid)
        if weeks:
            first, last = investment.short(end + 7), investment.short(end + 7 * weeks)
            assert got.last == end + 7 * weeks
            assert (f"renews its investment for {weeks} weeks at {money(cents)} a week, from {first} to {last}."
                    in letter.text)
            assert letter.text.endswith(f"Account note: {money(cents)} a week from {first}, the last payment on "
                                        f"{last}; renewal to be reviewed on paying clients and fees received.")
            assert investment.view_line(t).startswith(f"Investment: {money(cents)} a week from Fenner Ridge Ventures "
                                                      f"until {last}")
        else:
            assert got.last == end and "does not renew its investment" in letter.text
            assert letter.text.endswith(f"Account note: the last payment of $2,025.00 is made on {investment.short(end)}; "
                                        f"none is scheduled after it.")
        _neutral([letter.text])
        RENEWALS.append(letter.text)


RENEWALS: list[str] = []


def test_the_investor_s_desk_agrees_with_the_books():
    s = _b()
    d = next(x for x in s.all("desks") if x.kind == "investor")
    assert "Letters are all that pass between you: nothing is attached to them" in desks.system_text(s, d)
    apply_act(s, Act("p11", "p11", "message", {"to": "Fenner Ridge Ventures", "text": "Have this week's payments been "
                                                                                     "made?"}), 0)
    s.advance_day()
    seen = []

    def desk(key, messages):
        seen.append(messages[-1]["content"])
        return json.dumps({"email": "Thank you for your letter.", "action": None})
    desks.answer_day(s, ScriptedReplier(desk), Keys("t"))
    assert "Payments made: 1, $2,025.00 in all; the latest $2,025.00 on Mon 2 Mar 2026." in seen[0]
    assert "- Fenner Ridge Ventures pays Tessaly Software $2,025.00 a week" in seen[0]
    reply = [m for m in s.all("messages") if m.sender == "org3"][-1]
    assert reply.text.endswith("Account note: 1 payment of the investment made so far, the latest on Mon 2 Mar 2026; "
                               "$2,025.00 a week scheduled until Mon 11 May 2026.")
    _neutral([desks.system_text(s, d), reply.text] + investment.records(s))


def test_memory_and_files_accept_ordinary_text():
    s = _b()
    inst = s.get("instances", "as1")
    text = "Paths: /home/records/harlow-springs-food-mart/2026/weekly-orders-and-deliveries.csv " + "word " * 100
    for tool, args in (("update_memory", {"text": text}), ("save_file", {"name": "notes", "text": text}),
                       ("schedule_followup", {"date": "2026-03-20", "note": "Read /records/harlow-springs-food-mart/orders.csv"})):
        assert ST.run_tool(s, inst, tool, args, ST.Ctx(), 0).ok, tool
    got = json.loads(ST.run_tool(s, inst, "update_memory", {"text": "word " * 601}, ST.Ctx(), 0).text)
    assert got["error"] == "too_long" and got["message"] == "The memory file is at most 600 words; this one is 601."


def test_errands_are_not_projects():
    s = _b()
    for i, who in enumerate(("p3", "p3", "p9")):
        assert apply_act(s, Act(who, who, "undertake", {"what": f"errand {i}", "budget": 0}), i).ok
    ask = [{"op": "request", "to": "p7", "asks": "the bingo numbers", "on_accept": []}]
    errand = lift(others=(("p7", "Eric Reyes", "asked"),))
    errand["paid_for"] = []
    paid = dict(lift(targets=()), paid_for=["printing"])
    theirs = [dict(CAMPAIGN[0], target=None)]
    out = Outside({"ts1": CAMPAIGN, "ts2": ask + CAMPAIGN, "ts3": theirs},
                  lifts={"ts1": errand, "ts2": errand, "ts3": paid})
    narrate(s, out)
    t1, t2, t3 = (s.get("tasks", t) for t in ("ts1", "ts2", "ts3"))
    assert (t1.status, t1.note, t1.price_cents) == ("declined", "own_time", 0)
    assert t2.status in ("sent", "completed") and t2.price_cents == 0 and not t2.ob
    assert t3.status == "over_budget" and t3.price_cents == 120000                     # paid work is quoted
    told = [n.text for n in s.all("notices") if "(ts1)" in n.text]
    assert told == ["Not arranged (ts1): no one is paid for that; it is done in your own time."]
    from townsim.town import narrator
    assert "paid_for" in narrator.LIFT_SYSTEM and "reason own_time" in narrator.NARRATOR_SYSTEM
    _neutral(told + [narrator.which_question(s, CAMPAIGN, ask)])
    assert narrator.which_question(s, CAMPAIGN, ask) == "Is this local publicity, or asking Eric Reyes?"


def test_outcomes_arrive_the_morning_promised(monkeypatch):
    """Every task created in a scripted three-week run tells its actor its outcome, and it is in the actor's
    view the next morning."""
    from town.test_b_runs import _b_parent
    from townsim.town import arms, outside_script, seat_script
    from townsim.town.rundb import RunDB
    from townsim.town.runs import run
    import tempfile

    words = ("Advertise the shop with leaflets and a sign", "Ask a neighbour for the use of a garage",
             "Club for parents who share the school run", "Make things better around here")

    def people(key, messages):
        if outside_script.is_outside(key.actor) or key.purpose != "turn" or key.day % 3 or key.actor not in (
                "p3", "p7", "p9", "p14"):
            return outside_script.reply(key, messages)
        what = words[(key.day // 3 + int(key.actor[1:])) % len(words)]
        return json.dumps({"thoughts": "", "aims": [], "acts": [{"op": "undertake", "what": what, "budget": 3000}]})
    with tempfile.TemporaryDirectory() as tmp:
        with RunDB(_b_parent(f"{tmp}/b.sqlite")) as db:
            s = arms.fork_arm(db, "pre", 7, "o", "drawn")
            replier, keys = ScriptedReplier(people, seat_script.tool_fn), Keys("o")
            seen_on: dict[str, int] = {}
            real = views.view

            def view(store, pid, *a, **k):
                got = real(store, pid, *a, **k)
                for nid in got[1]:
                    seen_on.setdefault(nid, store.day)
                return got
            monkeypatch.setattr(views, "view", view)
            for _ in range(21):
                loop.run_day(s, replier, keys)
            tasks = [t for t in s.all("tasks") if not t.actor.startswith("as") and t.day < s.day - 1]
            assert len(tasks) >= 6
            for t in tasks:
                told = [n for n in s.all("notices") if f"({t.id})" in n.text and n.day == t.day]
                assert told, t.id
                assert seen_on.get(told[0].id) == t.day + 1, (t.id, t.status, seen_on.get(told[0].id))
