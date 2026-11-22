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
    assert set(by.to) == workers - {"p16"} and by.text.endswith(", by Joshua Marsh.")       # not to who did it
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


# --- people who differ ---------------------------------------------------------------------------------------

from townsim.town import acts, connections, gen, product, traits, ties  # noqa: E402
from townsim.town.product import NEUTRAL_BANNED  # noqa: E402


def _stores_1_to_30():
    out = []
    for seed in range(1, 31):
        out.append(_b(seed))
    return out


def test_traits_keep_their_shares_over_seeds_1_to_30_and_no_description_names_the_product():
    stores = _stores_1_to_30()
    assert gen.check_b(stores) == []
    shares = gen.shares_b(stores)
    assert abs(shares["project"] - traits.PROJECT_SHARE) < 0.08
    for s in stores[:5]:
        for p in s.all("people"):
            said = facts.who(s, p.id)
            low = said.casefold()
            assert "assistant" not in low and "tessaly assistant" not in low and " ai " not in f" {low} "
            assert s.get("traits", p.id).history in said
    broken = _b(1)
    broken.set("traits", "p1", "t", project="You would like an assistant for the shop.")
    assert any("description says 'assistant'" in x for x in gen.check_b([broken] + stores[1:]))


def test_traits_are_a_function_of_the_seed_and_only_with_lane_b():
    a, b = _b(3), _b(3)
    assert [(t.person, t.curve, t.project, t.follows) for t in a.all("traits")] == \
        [(t.person, t.curve, t.project, t.follows) for t in b.all("traits")]
    plain = generated(3)
    assert not plain.all("traits") and not plain.all("workrules")
    slogans = {x for pole in gen.BELIEFS["technology"] for x in pole}
    assert any(x in slogans for p in plain.all("people") for x in p.beliefs)              # the generator's, as before
    assert not any(x in slogans for p in a.all("people") for x in p.beliefs)               # replaced by the history
    for p in a.all("people"):
        t = a.get("traits", p.id)
        assert t.history in [h.format(place=a.place) for h in traits.HISTORY] + [traits.LAST_AT_A_DESK]
        assert t.curve == ([h.format(place=a.place) for h in traits.HISTORY].index(t.history)
                           if t.history != traits.LAST_AT_A_DESK else 4)
        for q in t.follows:
            assert q in ties.kinds(a, p.id)
    texts = [h for h in traits.HISTORY] + [traits.LAST_AT_A_DESK, traits.RULE]
    texts += [x for group in traits.PROJECTS.values() for x, _, _ in group]
    texts += [x for band in duties.CARED_FOR.values() for x in band] + list(duties.SECOND_JOBS)
    for x in texts:
        low = x.casefold()
        assert not [w for w in NEUTRAL_BANNED if w in low], x
        assert not any(w in low for w in ("assistant", "software", " ai", "computer program")) or x == traits.RULE \
            or x.startswith("You have never owned") or "computer at home" in x, x


def test_a_workplace_rule_is_told_to_staff_and_kept_by_code():
    s = _drawn_b()
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    assert s.has("workrules", bank.id)                                         # seed 1's bank has one
    said = facts.who(s, "p1")
    assert f"A rule at {bank.name}, where you work: staff may not give outside software access to its records." in said
    loans = f"loans:{bank.id}"
    from townsim.town import systems
    assert not systems.can_grant(s, "p1", loans)
    short = systems.system(s, loans).short
    out = apply_act(s, Act("p1", "p1", "connect", {"system": short, "scope": "read only"}), 0)
    assert out.msg == (f"Not done: {short} cannot be connected: {bank.name} has a rule: staff may not give outside "
                       "software access to its records.")
    inst = connections.instance_of_client(s, s.get("people", "p1").household)
    got, why = connections.ask(s, inst.id, "p1", loans, "read", 0, "", "t")
    assert got is None and why.endswith(f": {bank.name} has a rule: staff may not give outside software access to "
                                        "its records.")
    assert systems.can_grant(s, "p1", f"account:{s.get('people', 'p1').household}")   # her household's are hers
    plain = drawn()
    assert systems.can_grant(plain, "p1", loans)                                      # without lane B, as before


def test_the_answer_form_lists_every_answer_the_open_requests_take():
    from town.test_once import _asked
    from town.test_plans import OWNER, world
    s = world()
    rq = _asked(s)
    form = next(f for f in acts.forms(s, OWNER) if f.startswith('{"op": "answer"'))
    assert form == '{"op": "answer", "request": "<id>", "answer": "once" | "always" | "no", "note": "<optional words>"}'
    other = apply_act(s, Act("p1", "p1", "request", {"to": "Tara Russo", "text": "A hand on Saturday?", "amount": 0}), 0)
    assert other.ok, other.msg
    form = next(f for f in acts.forms(s, OWNER) if f.startswith('{"op": "answer"'))
    assert form == ('{"op": "answer", "request": "<id>", "answer": "accept" | "decline" | "once" | "always" | "no" '
                    f'("once" | "always" | "no" for {rq.id}), "note": "<optional words>"}}')
    plain = drawn()
    assert '"answer": "accept" | "decline"' in next(f for f in acts.forms(plain, "p3") if f.startswith('{"op": "answer"'))


def test_after_the_launch_only_the_firms_own_people_have_the_product_in_their_description(tmp_path):
    from town.test_b_runs import _b_parent
    from townsim.town import arms
    from townsim.town.rundb import RunDB
    with RunDB(_b_parent(str(tmp_path / "l.sqlite"))) as db:
        s = arms.fork_arm(db, "pre", 7, "c", "choice")
    pr = product.launched(s)
    assert pr is not None
    firm_people = set(product.company_people(s, pr.org))
    for p in s.all("people"):
        low = f" {facts.who(s, p.id).casefold()} "
        words = [w for w in traits.NOT_DESCRIBED + (pr.name.casefold(),) if w in low]
        if p.id in firm_people:
            assert set(words) <= {"assistant", pr.name.casefold()}, (p.name, words)    # what their own work is
            assert "assistant" not in low.replace(pr.name.casefold(), "")
        else:
            assert words == [], (p.name, words)
    assert "Andrea Ramirez, your sister, runs Tessaly Software, paid " in facts.who(s, "p12")
