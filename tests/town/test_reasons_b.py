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
