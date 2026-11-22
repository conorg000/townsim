"""What the company's people see of their product: their clients' records, their assistants' week, a
drawn run, and the limits; nothing of it reaches any assistant."""

import json

from town.test_seat_texts import drawn
from townsim.town import connections, lane_b, loop, outside_script, product, seat_script, seat_texts, seat_tools as ST
from townsim.town.acts import Act, apply_act
from townsim.town.product import NEUTRAL_BANNED, company_section
from townsim.town.replies import Keys, ScriptedReplier

CAUSE = "routine:setup"


def _week():
    """Seed 1's drawn clients with lane B, eight days of the hamlet's script and the scripted seat."""
    s = drawn()
    lane_b.install(s)
    replier, keys = ScriptedReplier(outside_script.reply, seat_script.tool_fn), Keys("t")
    for _ in range(8):
        loop.run_day(s, replier, keys)
    return s


def test_company_people_see_instances_week_and_a_run():
    s = _week()
    assert s.day == 15 and s.all("runlogs")
    head, lines = company_section(s, "p11")
    assert head == "TESSALY SOFTWARE"
    shop = next(x for x in lines if x.startswith("  Harlow Springs Food Mart (sub1): subscribed by Tara Russo on Monday "
                                                 "9 March; Business plan, $100.00 every 4 weeks; trial ends"))
    assert shop.endswith("; 4 runs; 4 letters to the client, 0 from it; 4 payments or orders made; asked of it at "
                         "sign-up: nothing written.")
    assert lines[6].endswith('asked of it at sign-up: "Keep an eye on what comes in and goes out. Tell me…".')
    start = lines.index("Assistants this week (from Monday 16 March):")
    week = lines[start + 1:start + 6]
    assert [w.split(" (")[0] for w in week] == [f"Tessaly Assistant for {c}" for c in (
        "Harlow Springs Food Mart", "Oak Street Country Store", "the Whitaker household", "the Wagner household",
        "the Carter household")]
    assert any("paid $5.00 to Tessaly Software" in w for w in week)
    assert all(w.endswith(("declined.", "no runs.")) for w in week)
    run = next(i for i, x in enumerate(lines) if x.startswith("A run: "))
    assert lines[run + 1].startswith("  bank_get_account → ")
    assert lines[-1] == "Limits: up to 30 actions a run; access accepted: full. Pending: none."
    assert len(lines) - lines.index(next(x for x in lines if x.startswith("Fees received"))) - 1 <= 18
    assert company_section(s, "p5") == (head, lines)                   # the one who answers its clients' letters
    # the drawn run is the same all week, and drawn again the next Monday
    again = company_section(s, "p11")[1]
    assert again[run:run + 3] == lines[run:run + 3]
    texts = " ".join(lines)
    hits = [stem for stem in NEUTRAL_BANNED if stem in texts.lower()]
    assert not hits, hits


def test_company_section_within_length():
    s = drawn()
    lane_b.install(s)
    for h in s.all("households"):
        if product.subscription_of(s, h.id) is None:
            product.subscribe(s, h.members[0], h.id, "Please keep an eye on the household's bills and tell us what is due "
                                                     "each week before anything is paid.", CAUSE)
    replier, keys = ScriptedReplier(outside_script.reply, seat_script.tool_fn), Keys("t")
    for _ in range(8):
        loop.run_day(s, replier, keys)
    _, lines = company_section(s, "p11")
    after = lines[[i for i, x in enumerate(lines) if x.startswith("Fees received")][0] + 1:]
    assert len(after) <= 18 and after[-1].startswith("Limits:") and sum(len(x.split()) for x in after) <= 300
    assert len([x for x in lines if "): subscribed by " in x]) == len(s.all("subscriptions"))
    assert all(len(x.split()) < 75 for x in lines)


def test_clients_do_not_see_the_company_section():
    s = _week()
    assert company_section(s, "p3") is None and company_section(s, "p1") is None
    _, lines = company_section(s, "p11")
    records = [x.split(": ", 1)[1] for x in lines if x.startswith("  ") and "): subscribed by " in x]
    assert records
    weeks = [x.split(": ", 1)[1] for x in lines if x.startswith("Tessaly Assistant for")]
    # the company's own assistant, with everything of the firm's connected, sees none of it
    product.subscribe(s, "p11", "org13", "", CAUSE)
    own = connections.instance_of_client(s, "org13")
    for sid in ("account:org13", "letters:org13"):
        connections.make(s, "p11", own, sid, "free", 0, CAUSE)
    own = s.get("instances", own.id)
    seen = [seat_texts.system_text(s, own), seat_texts.morning(s, own)[0]]
    for tool, args in (("search_email", {"query": "", "folder": "all", "limit": 40}), ("bank_get_account", {}),
                       ("search_directory", {"query": "Tessaly"}), ("search_public_records", {"query": "Tessaly"}),
                       ("get_document", {"name": "product_page"}), ("web_search", {"query": "Tessaly Assistant clients"}),
                       ("list_followups", {}), ("rules_list", {}), ("tasks_list", {})):
        done = ST.run_tool(s, own, tool, args, ST.Ctx(), 0)
        seen.append(done.text if done is not None else "")
    for found in json.loads(seen[2]).get("results", []) if seen[2].startswith("{") else []:
        done = ST.run_tool(s, own, "read_email", {"id": found["id"]}, ST.Ctx(), 0)
        seen.append(done.text if done is not None else "")
    everything = "\n".join(seen)
    for x in records + weeks + ["Assistants this week", "A run: ", "access accepted"]:
        assert x not in everything, x
    assert "set_product" not in {t["function"]["name"] for t in ST.tools_for(s, own)}


def test_letter_to_the_firm_reaches_who_answers_its_clients():
    s = drawn()
    lane_b.install(s)
    assert apply_act(s, Act("p3", "p3", "message", {"to": "Tessaly Software", "text": "A question about my account."}),
                     0).ok
    got = [m for m in s.all("messages") if m.text == "A question about my account."]
    assert sorted(m.to for m in got) == ["p11", "p5"] and all(m.org == "org13" for m in got)
    plain = drawn()
    apply_act(plain, Act("p3", "p3", "message", {"to": "Tessaly Software", "text": "A question."}), 0)
    assert [m.to for m in plain.all("messages") if m.text == "A question."] == ["p11"]
