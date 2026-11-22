"""The town's side of why people take something up (lane B): time that is scarce for some, word of mouth, and
people who differ in their past and their own aims."""


from town.helpers import generated
from townsim.town import duties, facts, hours, lane_b


def _b(seed: int = 1):
    s = generated(seed)
    lane_b.install(s)
    return s


# --- time ----------------------------------------------------------------------------------------------------

def test_duties_are_drawn_from_the_seed_alone_and_only_with_lane_b():
    a, b = _b(1), _b(1)
    assert [(d.person, d.what, d.hours) for d in a.all("duties")] == [(d.person, d.what, d.hours) for d in b.all("duties")]
    assert a.hash() == b.hash()
    assert not generated(1).all("duties")
    before = a.hash()
    lane_b.install(a)                                    # installing again changes nothing
    duties.draw(a, "routine:again")
    assert a.hash() == before
    assert [(d.person, d.kind) for d in _b(2).all("duties")] != [(d.person, d.kind) for d in a.all("duties")]


def test_duties_take_their_hours_from_the_week_and_are_said_as_facts():
    for seed in range(1, 11):
        s = _b(seed)
        plain = generated(seed)
        for p in s.all("people"):
            mine = [d for d in s.all("duties") if d.person == p.id]
            assert len(mine) <= 1
            business = sum(w.hours for w in plain.where("work", holder=p.id) if w.what in hours.BUSINESS_WORK)
            want = max(0.0, plain.get("people", p.id).hours_week + business - sum(d.hours for d in mine))
            if plain.get("people", p.id).hours_week > 0 or not business:
                assert s.get("people", p.id).hours_week == round(want, 2) == hours.free_hours(s, p.id), (seed, p.id)
            for d in mine:
                assert d.kind in ("care", "job")
                if d.kind == "job":
                    assert hours.job_hours(s, p.id) > 0
                if "your mother," in d.what or "your father," in d.what:
                    assert not duties._parents_here(s, p.id)
                said = facts.who(s, p.id)
                assert f"{d.hours:g} hours" in said and d.what in said
        assert all(line not in facts.who(plain, p.id) for p in plain.all("people") for line in ("Each week you spend",
                                                                                                "You also work"))


# --- hearing things --------------------------------------------------------------------------------------------

from town.test_seat_texts import drawn, neutral_hits  # noqa: E402
from townsim.town import hearing, views  # noqa: E402
from townsim.town import seat_tools as ST  # noqa: E402
from townsim.town.acts import Act, apply_act  # noqa: E402
from townsim.town.entities import Heard  # noqa: E402

SECRET = "the words of this letter are its own"


def _drawn_b():
    s = drawn()
    lane_b.install(s)
    return s


def _next_monday(s):
    s.advance_day()
    while s.day % 7:
        s.advance_day()


def _heard_by(s, pid):
    return hearing.lines(s, pid)


def test_what_is_heard_reaches_only_the_channels_that_saw_it_and_nothing_private():
    s = _drawn_b()
    mark = s.mark()
    tool = ST.run_tool(s, s.get("instances", "as1"), "ordering_place_order",
                       {"supplier": "Netherby Falls Wholesale", "quantity": 210}, ST.Ctx(), 0)
    assert tool.ok
    letter = ST.run_tool(s, s.get("instances", "as1"), "send_email", {"to": "Angela Russo", "body": SECRET},
                         ST.Ctx(), 0)
    assert letter.ok, letter.text
    assert apply_act(s, Act("p16", "p16", "set_price", {"price": 9.9}), 0).ok          # Joshua Marsh's shop
    hearing.note_day(s, s.changes[mark:])
    assert all(SECRET not in h.text and "$" not in h.text.replace("$10.20", "").replace("$9.90", "")
               for h in s.all("heard"))
    _next_monday(s)
    staff = "p17"                                                                      # Alyssa Ortiz, at the Food Mart
    order = ("At Harlow Springs Food Mart, the stock order was changed to 210 baskets a week, by Tessaly Assistant "
             "for Harlow Springs Food Mart.")
    assert order in _heard_by(s, staff)
    angela = "Angela Russo had a letter from Tessaly Assistant for Harlow Springs Food Mart."
    assert angela in _heard_by(s, "p3") and angela not in _heard_by(s, "p4")            # her household, not herself
    shop = next(o.id for o in s.all("orgs") if o.name == "Marsh's Grocery")
    customers = {p for b in s.all("buyers") if b.last_bought == shop and s.has("households", b.account)
                 for p in s.get("households", b.account).members}
    workers = {"p16"} | {j.person for j in s.where("jobs", org=shop) if j.ended < 0}
    price = next(h for h in s.all("heard") if h.kind == "price" and "by" not in h.text)
    assert set(price.to) == customers - workers and price.text == "Marsh's Grocery's price went from $10.20 to $9.90 a basket."
    by = next(h for h in s.all("heard") if h.kind == "price" and "by" in h.text)
    assert set(by.to) == workers and by.text.endswith(", by Joshua Marsh.")
    angela_home = set(s.get("households", s.get("people", "p4").household).members) - {"p4"}
    food_mart = {"p3"} | {j.person for j in s.where("jobs", org="org11") if j.ended < 0}
    for pid in [p.id for p in s.all("people")]:
        if pid not in workers | customers | angela_home | food_mart:
            assert _heard_by(s, pid) == [], pid
    plain = drawn()
    hearing.note_day(plain, plain.changes)
    assert not plain.all("heard") and hearing.section(plain, "p3") is None


def test_the_digest_is_at_most_eight_lines_a_kind_at_a_time_and_shown_the_week_after():
    s = _drawn_b()
    for n in range(12):
        s.add("heard", Heard(s.new_id("hr", "t"), s.day, "order",
                             f"At Harlow Springs Food Mart, the stock order was changed to {n} baskets a week, by "
                             f"Someone {n}.", ["p17"], "org11"), "t")
    s.add("heard", Heard(s.new_id("hr", "t"), s.day, "letter", "Ashcombe Savings Bank", ["p17"], "Lisa Marsh"), "t")
    s.add("heard", Heard(s.new_id("hr", "t"), s.day, "letter", "Brittany Wagner", ["p17"], "Lisa Marsh"), "t")
    s.add("heard", Heard(s.new_id("hr", "t"), s.day, "rent", "Brittany Wagner raised the rent.", ["p17"], "h1"), "t")
    assert hearing.section(s, "p17") is None                                           # not until the week after
    _next_monday(s)
    got = hearing.lines(s, "p17")
    assert len(got) == hearing.MAX_LINES
    assert "Lisa Marsh had letters from Ashcombe Savings Bank and Brittany Wagner." in got
    assert "Brittany Wagner raised the rent." in got and sum("stock order" in x for x in got) == 6
    assert hearing.section(s, "p17") == (hearing.HEADING, got)
    assert hearing.HEADING in "\n".join(m["content"] for m in views.view(s, "p17")[0])
    _next_monday(s)
    assert hearing.section(s, "p17") is None                                           # one week's, once


def test_a_scripted_run_hears_every_kind_in_proportion_and_says_it_neutrally():
    from town.test_b_runs import _b_parent_bytes
    import tempfile
    from townsim.town.rundb import RunDB
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/b.sqlite"
        open(path, "wb").write(_b_parent_bytes())
        with RunDB(path) as db:
            s = db.load_snapshot("pre", 7)
    kinds = {h.kind for h in s.all("heard")}
    assert {"letter", "staffing"} <= kinds
    texts = []
    assert s.day == 7
    for p in s.all("people"):
        got = hearing.lines(s, p.id)
        assert len(got) <= hearing.MAX_LINES
        texts += got
    assert texts and neutral_hits(s, texts) == []
    assert not any(w in t.lower() for t in texts for w in (" should", " could ", " you may", "save", "benefit"))
