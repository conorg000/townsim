import functools
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from townsim.town import acts, firms, gen, hours, names, ties
from townsim.town.loop import routine
from townsim.town.obligations import money

FAMILIES = ["a", "b", "c", "d"]
SEED_1_HASH = "057e5d5b4a09c20f437b251e897080e51d99c5fa44ba83eb1e1b57d96638360d"


@functools.lru_cache(maxsize=None)
def place(seed):
    """A generated place, shared by the tests that only read it."""
    return gen.generate(seed, FAMILIES)


@pytest.mark.parametrize("seed", range(1, 31))
def test_every_seed_gives_a_sound_place(seed):
    s = place(seed)
    assert gen.check(s) == [] and s.ledger.total() == 0 and len(s.all("people")) == 18
    assert 7 <= len(s.all("households")) <= 9 and s.day == 0 and s.seed == seed


def test_the_same_seed_gives_the_same_place():
    assert gen.generate(7, FAMILIES).hash() == gen.generate(7, FAMILIES).hash() != gen.generate(8, FAMILIES).hash()


def test_seed_1_is_frozen():
    assert gen.generate(1, FAMILIES).hash() == SEED_1_HASH        # changed only on purpose


def _shops(s):
    return [o for o in s.all("orgs") if o.kind == "shop"]


@pytest.mark.parametrize("seed", (1, 2, 3))
def test_ordinary_inequality(seed):
    s = place(seed)
    landlords = [p for p in s.all("people") if p.kind == "landlord"]
    assert len(landlords) == 1
    assert len([h for h in s.all("households") if h.landlord == landlords[0].id]) >= 3
    staff = {o.id: len([j for j in s.where("jobs", org=o.id) if j.person != o.owner]) for o in _shops(s)}
    assert sorted(staff.values()) == [1, 1, 4] and max(staff.values()) >= 0.4 * sum(staff.values())
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    loans = [o for o in s.where("obligations", payee=bank.id) if o.kind == "loan"]
    assert len(loans) >= 3 and all(o.last is not None for o in loans)
    assert any(s.has("orgs", o.payer) for o in loans)
    assert bank.owner == "" and s.get("people", bank.contact).kind == "banker"
    assert s.balance(bank.id) > 0


def test_families_are_dealt_within_kinds():
    for seed in (1, 2, 3, 4, 5):
        s = place(seed)
        played = Counter(p.family for p in s.all("people"))
        assert set(played) == set(FAMILIES) and min(played.values()) >= 2
        kinds: dict[str, set] = {}
        for p in s.all("people"):
            kinds.setdefault(p.kind, set()).add(p.family)
        for kind, fams in kinds.items():
            if len([p for p in s.all("people") if p.kind == kind]) >= 4:
                assert len(fams) >= 2, kind
    dealt = [tuple(p.family for p in place(seed).all("people")) for seed in (1, 2, 3)]
    assert len(set(dealt)) == 3


def test_the_deck_is_dated_and_fixed_at_generation():
    from townsim.town import hamlet
    weeks = set()
    for seed in (1, 2, 3, 4):
        s = place(seed)
        deck = s.all("events")
        assert deck and all(0 <= e.day < 7 * hamlet.RUN_WEEKS and not e.done for e in deck)
        weeks |= {e.day // 7 for e in deck}
        for e in deck:
            assert e.kind in hamlet.KINDS
            table = {"illness": "people", "job_lost": "people", "hours_cut": "people", "repair": "households",
                     "agency_rent": "households", "supplier_short": "suppliers", "supplier_price": "suppliers",
                     "energy_price": "orgs", "cold_month": "orgs"}[e.kind]
            assert s.has(table, e.who)
            if e.kind in ("job_lost", "hours_cut"):
                assert any(s.get("orgs", j.org).outside for j in s.where("jobs", person=e.who))
            if e.kind == "repair":
                assert e.amount > 0 and e.what in hamlet.REPAIRS
            if e.kind == "agency_rent":
                assert s.get("orgs", s.get("households", e.who).landlord).outside
        dealt = Counter(hamlet.household_of(s, e) for e in deck if hamlet.household_of(s, e))
        assert max(dealt.values()) <= hamlet.EVENTS_A_HOUSEHOLD
        assert gen.check(s) == []
    assert max(weeks) >= 12                                       # the deck spans the run, not its first weeks
    again = gen.generate(2, FAMILIES).all("events")
    assert again == place(2).all("events")
    assert len(gen.generate(2, FAMILIES, weeks=4).all("events")) < len(again)


def test_the_check_refuses_a_deck_that_crowds_a_household():
    from townsim.town import hamlet
    from townsim.town.entities import Event
    s = gen.generate(3, FAMILIES)
    h = s.all("households")[0].id
    for i in range(hamlet.EVENTS_A_HOUSEHOLD + 1):
        s.add("events", Event(f"ev{900 + i}", 10 + i, "repair", h, 10_000, 1, what="roof"), "routine:t")
    assert any("more than 3 events" in x for x in gen.check(s))


def test_nothing_in_a_generated_place_mentions_what_it_is_for():
    text = gen.describe(gen.generate(1, FAMILIES)).lower()
    for word in ("simulat", " ai ", "artificial", "language model", "experiment", "test"):
        assert word not in text
    s = place(1)
    stored = " ".join([*(f"{p.name} {p.job} {p.voice} {' '.join(p.beliefs + p.interests + p.worries)}"
                         for p in s.all("people")),
                       *(o.name for o in s.all("orgs")), *(o.memo for o in s.all("obligations"))]).lower()
    for word in ("simulat", " ai ", "artificial", "language model", "experiment", "test", " game "):
        assert word not in f" {stored} "


def test_a_generated_place_runs_a_week_of_routine_without_error():
    s = gen.generate(3, FAMILIES)
    for _ in range(7):
        routine(s)
        firms.deliver(s); firms.shop_today(s); firms.close_week(s)
        s.advance_day()
        assert s.ledger.total() == 0
    assert s.engine_errors == []
    assert sum(t.sold for t in s.all("tills")) > 0
    assert not [d for d in s.all("dues") if d.status != "paid"]


# what is generated is true of the store

def _dollars(text):
    return re.findall(r"\$[\d,]+\.\d\d", text)


def _figures(s, pid):
    """Every sum of money a person's own rows show: what their household and the orgs they
    own pay and are paid, and their household's weekly totals."""
    p = s.get("people", pid)
    accounts = [p.household] + [o.id for o in s.all("orgs") if o.owner == pid]
    found = set()
    for a in accounts:
        for o in s.where("obligations", payer=a) + s.where("obligations", payee=a):
            found.add(money(o.cents))
    found |= {money(gen.weekly_in(s, p.household)), money(gen.weekly_out(s, p.household))}
    return found


@pytest.mark.parametrize("seed", (1, 2, 3, 4, 5, 6))
def test_every_worry_job_line_and_tie_is_true_of_the_store(seed):
    s = place(seed)
    orgs = {o.name: o for o in s.all("orgs")}
    for p in s.all("people"):
        assert 1 <= len(p.worries) <= 3 and 2 <= len(p.beliefs) <= 4 and 1 <= len(p.interests) <= 3 and p.voice
        for w in p.worries:
            for figure in _dollars(w):
                assert figure in _figures(s, p.id), (p.name, w)
        named = [o for name, o in orgs.items() if name in p.job]
        for o in named:
            assert o.owner == p.id or s.where("jobs", person=p.id) and any(
                j.org == o.id for j in s.where("jobs", person=p.id)), (p.name, p.job)
        if p.kind in ("owner", "staff", "banker", "software", "outside_worker"):
            assert named and s.where("jobs", person=p.id), (p.name, p.job)
    for t in s.all("ties"):
        a, b = s.get("people", t.person), s.get("people", t.other)
        if t.kind == "tenant":
            assert s.get("households", b.household).landlord == a.id
        if t.kind == "landlord":
            assert s.get("households", a.household).landlord == b.id
        if t.kind == "employee":
            assert any(s.get("orgs", j.org).owner == a.id for j in s.where("jobs", person=b.id))
        if t.kind == "employer":
            assert any(s.get("orgs", j.org).owner == b.id for j in s.where("jobs", person=a.id))
        if t.kind == "coworker":
            assert {j.org for j in s.where("jobs", person=a.id)} & {j.org for j in s.where("jobs", person=b.id)}
        if t.kind in ("creditor", "debtor"):
            debtor, creditor = (a, b) if t.kind == "creditor" else (b, a)
            assert [o for o in s.where("obligations", payer=debtor.household)
                    if o.payee == creditor.household and o.kind == "agreement" and o.last is not None]
        if t.kind == "banker":
            assert b.kind == "banker"
            bank = next(o for o in s.all("orgs") if o.kind == "bank")
            assert [o for o in s.where("obligations", payee=bank.id)
                    if o.kind == "loan" and o.payer in [a.household] + [g.id for g in s.all("orgs") if g.owner == a.id]]
        if t.kind in ("partner", "parent", "child", "sibling"):
            assert a.household == b.household
        if t.kind == "partner":
            assert abs(a.age - b.age) <= 8
        if t.kind == "kin":
            assert a.household != b.household
        assert any(u.person == t.other and u.other == t.person for u in s.all("ties")) or t.kind == "banker"


@pytest.mark.parametrize("seed", (1, 2, 3, 4))
def test_positions_follow_the_rows(seed):
    s = place(seed)
    held = {p.id: acts.positions(s, p.id) for p in s.all("people")}
    owners = [p for p, pos in held.items() if "owner" in pos]
    assert len(owners) == 4 and sorted(s.get("people", p).kind for p in owners) == ["owner", "owner", "owner", "software"]
    assert len([p for p, pos in held.items() if "landlord" in pos]) == 1
    assert len([p for p, pos in held.items() if "banker" in pos]) == 1
    assert [p for p, pos in held.items() if "employed" in pos] == sorted({j.person for j in s.all("jobs")},
                                                                       key=lambda i: int(i[1:]))


@pytest.mark.parametrize("seed", (1, 2, 3))
def test_first_names_are_plain_to_everyone_who_knows_them(seed):
    s = place(seed)
    for p in s.all("people"):
        first = [s.get("people", o).name.split()[0] for o in acts.knows(s, p.id)]
        assert len(first) == len(set(first))
        for o in acts.knows(s, p.id):
            assert acts.resolve(s, s.get("people", o).name.split()[0], p.id) == o


def test_check_reports_what_is_unsound():
    s = gen.generate(1, FAMILIES)
    a, b = s.all("people")[:2]
    s.set("people", b.id, "routine:setup", name=f"{a.name.split()[0]} {b.name.split()[1]}")
    s.set("people", a.id, "routine:setup", age=19 if a.age > 40 else 82)
    job = s.all("jobs")[0]
    s.set("jobs", job.id, "routine:setup", weekly=int(job.hours * 1000))
    problems = gen.check(s)
    assert any("first name" in x for x in problems)
    assert any("an hour" in x for x in problems)
    shop = _shops(s)[0]
    s.set("orgs", shop.id, "routine:setup", name=s.all("people")[3].name)
    assert any("named like" in x for x in gen.check(s))


def test_describe_reads_as_a_directory():
    s = place(1)
    text = gen.describe(s)
    assert s.place in text
    for p in s.all("people"):
        assert p.name in text
    for o in s.all("orgs"):
        if not o.outside:
            assert o.name in text
    for e in s.all("events"):
        assert gen.date_text(e.day) in text
    assert 1500 < len(text.split()) < 9000


def test_names_and_families_are_plain_lists():
    assert len(names.FIRST_NAMES) >= 60 and len(set(names.FIRST_NAMES)) == len(names.FIRST_NAMES)
    assert len(names.SURNAMES) >= 40 and len(names.STREETS) >= 20 and len(names.PLACES) >= 12
    assert names.SHOP_WORDS
    config = yaml.safe_load((Path(__file__).parents[2] / "config" / "families.yaml").read_text())
    assert {k: v["model"] for k, v in config.items()} == {
        "haiku": "anthropic/claude-haiku-5.5", "deepseek": "deepseek/deepseek-v4.1-flash-1017",
        "hunyuan": "tencent/hy4-flash", "minimax": "minimax/minimax-m3.1-flash"}
    assert all({k: x for k, x in v["params"].items() if k != "reasoning_effort"} == {"max_tokens": 1200, "temperature": 0.8}
               and v["left_out"] is False for v in config.values())


def test_draw_lists_are_wide_enough():
    assert len(gen.VOICES) >= 12 and len(gen.INTERESTS) >= 30
    for axis, poles in gen.BELIEFS.items():
        assert len(poles) == 2 and all(len(w) >= 4 for w in poles), axis
    assert len(gen.BELIEFS) == 6


@pytest.mark.parametrize("seed", (1, 2, 3))
def test_recurring_work_and_bank_borrowers(seed):
    s = place(seed)
    for h in s.all("households"):
        held = [w for w in s.all("work") if w.account == h.id]
        assert [w.what for w in held] == ["household paperwork"] and held[0].holder in h.members
    for o in _shops(s):
        assert sorted(w.what for w in s.all("work") if w.account == o.id) == ["ordering", "rota and pay", "the books"]
        assert all(w.holder == o.owner for w in s.all("work") if w.account == o.id)
        assert [j.person for j in s.where("jobs", org=o.id)].count(o.owner) == 1
    bank = next(o for o in s.all("orgs") if o.kind == "bank")
    banked = {p.id for p in s.all("people") if "banker" in ties.kinds(s, p.id).get(bank.contact, [])}
    for o in s.where("obligations", payee=bank.id):
        payers = s.get("households", o.payer).members if s.has("households", o.payer) else [s.get("orgs", o.payer).owner]
        assert {p for p in payers if s.get("people", p).age >= 18} <= banked
    for p in s.all("people"):
        assert p.hours_week == p.hours_left == hours.free_hours(s, p.id) > 0


# facts that hold up

def _run(s, days):
    for _ in range(days):
        routine(s); firms.deliver(s); firms.shop_today(s); firms.close_week(s); s.advance_day()


def test_nobody_started_their_present_job_before_sixteen():
    for seed in range(1, 21):
        s = place(seed)
        for j in s.all("jobs"):
            p = s.get("people", j.person)
            assert j.since < 0 and -j.since / 7 <= max(4, (p.age - 16) * 52), (p.name, p.age, j.since)


def test_households_that_worry_about_money_do_run_short():
    worried = short = 0
    for seed in range(1, 21):
        s = gen.generate(seed, FAMILIES)
        start = {h.id: s.balance(h.id) for h in s.all("households")}
        tight = [h.id for h in s.all("households")
                 if any(w.startswith("More goes out") for m in h.members for w in s.get("people", m).worries)]
        for h in tight:
            assert gen.weekly_in(s, h) < gen.weekly_out(s, h)
        _run(s, 28)
        worried += len(tight)
        short += len([h for h in tight if s.balance(h) < start[h]])
        assert s.ledger.total() == 0 and s.engine_errors == []
    assert worried and short >= 0.6 * worried


def test_food_counts_as_what_a_household_can_be_expected_to_spend():
    s = place(1)
    price = sum(f.price for f in s.all("firms")) / 3
    for b in s.all("buyers"):
        if b.account != firms.OUTSIDE_BUYERS:
            spend = gen.food_spend(s, b.account)
            assert spend == gen.expected_food(b.size, price) and spend < b.budget * 2
            obs = sum(o.cents for o in s.where("obligations", payer=b.account) if o.every == 7)
            assert gen.weekly_out(s, b.account) == obs + spend


@pytest.mark.parametrize("seed", range(1, 21))
def test_mortgages_end_by_seventy_five_and_the_landlord_s_is_sized_without_the_rents(seed):
    s = place(seed)
    for h in s.all("households"):
        head = s.get("people", h.members[0])
        mortgage = [o for o in s.where("obligations", payer=h.id) if o.memo.startswith("mortgage")]
        if head.age >= 75:
            assert not mortgage
        for o in mortgage:
            assert (o.last - o.first) // 7 + 1 <= (75 - head.age) * 52
            if any(p.kind == "landlord" for p in map(lambda m: s.get("people", m), h.members)):
                rents = sum(r.cents for r in s.where("obligations", payee=h.id) if r.kind == "rent")
                base = gen.weekly_in(s, h.id) - rents
                assert 0.25 * base - 100 <= o.cents <= 0.35 * base + 100


def test_siblings_living_together_share_a_surname():
    for seed in range(1, 41):
        s = place(seed)
        for t in s.all("ties"):
            if t.kind == "sibling":
                assert s.get("people", t.person).name.split()[-1] == s.get("people", t.other).name.split()[-1]


def test_a_landlord_under_pension_age_lives_on_the_rents_and_pensions_are_each_their_own():
    for seed in range(1, 21):
        s = place(seed)
        landlord = next(p for p in s.all("people") if p.kind == "landlord")
        pensions = [o for o in s.where("obligations", payee=landlord.household) if "pension" in o.memo]
        retired = [p for p in s.get("households", landlord.household).members
                   if s.get("people", p).kind == "retired" or (s.get("people", p) is landlord and landlord.age >= 66)]
        assert len(pensions) == len(retired)
        for p in s.all("people"):
            for w in p.worries:
                if w.startswith("Getting by on your pension"):
                    assert p.kind == "retired" or (p.kind == "landlord" and p.age >= 66)


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _all_strings(k); yield from _all_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_strings(v)


IMPERATIVES = ("never", "do not", "don't", "keep", "always", "try", "make", "be")
UNSAID = ("model", "models", "game", "games", "role", "test", "scenario", "experiment", "machine", "machines",
          "robot", "ai", "artificial", "simulation")


def test_no_wording_is_an_instruction_or_says_what_the_place_is_for():
    lists = [v for m in (gen, names) for k, v in vars(m).items()
             if k.isupper() and isinstance(v, (list, tuple, dict))]
    words = [w for v in lists for w in _all_strings(v)]
    words += [w for seed in (1, 2, 3) for p in place(seed).all("people") for w in p.worries + p.beliefs + [p.voice]]
    assert len(words) > 300
    for w in words:
        low = w.lower()
        assert not any(low == i or low.startswith(i + " ") for i in IMPERATIVES), w
        assert not any(re.search(rf"\b{u}\b", low) for u in UNSAID), w


ADDRESSING = ("you can", "you should", "you may", "you could", "you might", "you need to", "try to", "make sure",
              "remember to", "ought to", "role", "scenario")


def test_wordings_shown_as_a_person_s_own_are_in_the_second_person_and_ask_nothing():
    beliefs = [w for poles in gen.BELIEFS.values() for pole in poles for w in pole]
    worries = [w for seed in range(1, 11) for p in place(seed).all("people") for w in p.worries]
    voices = list(gen.VOICES) + [p.voice for p in place(1).all("people")]
    for w in beliefs + list(gen.INTERESTS) + worries:
        low = f" {w.lower()} "
        assert not any(f" {a}" in low for a in ADDRESSING), w
    for w in beliefs:
        assert not re.search(r"\byou\b", w.lower()), w
    for w in worries + voices:
        assert not re.search(r"\b(their|them|they)\b", w.lower()) or "they are" in w, w
    assert all(v.startswith("You write ") for v in voices)
    text = gen.describe(place(1))
    for p in place(1).all("people"):
        assert f"Worries: {' '.join(p.worries)}" in text


def test_the_number_of_families_changes_nothing_else():
    def without_families(s):
        c = s.canonical()
        for p in c["tables"]["people"].values():
            p["family"] = ""
        return c
    for seed in (1, 2, 5):
        assert without_families(gen.generate(seed, ["a", "b", "c"])) == without_families(gen.generate(seed, FAMILIES))


def test_a_person_named_like_an_organisation_is_reported():
    s = gen.generate(2, FAMILIES)
    p = s.all("people")[0]
    shop = _shops(s)[0]
    s.set("orgs", shop.id, "routine:setup", name=p.name.split()[-1])
    assert any("named like an organisation" in x for x in gen.check(s))


def test_household_headings_are_written_one_way():
    s = place(1)
    text = gen.describe(s)
    for h in s.all("households"):
        assert f"{h.name.split(' household')[0]} household, {h.street}\n" in text
