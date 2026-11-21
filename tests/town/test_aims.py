from townsim.town import aims
from townsim.town.thin import thin_town

from town.helpers import run_days


def test_aims_keep_their_age_when_they_are_the_same_matter():
    s = thin_town(); aims.update(s, "p03", ["Find the $185 for Monday's rent"], "op:p03:aims:d0.0"); run_days(s, 9)
    aims.update(s, "p03", ["Find $185 for the rent on Monday", "Ask Megan about the car"], "op:p03:aims:d9.0")
    assert aims.lines(s, "p03") == ["for 9 days: Find $185 for the rent on Monday", "for 0 days: Ask Megan about the car"]


def test_an_aim_not_matched_by_a_new_one_is_dropped_and_the_list_is_replaced_whole():
    s = thin_town()
    aims.update(s, "p03", ["Pay the rent", "Mend the gate"], "op:p03:aims:d0.0")
    run_days(s, 3)
    aims.update(s, "p03", ["Ask Tom about the allotment"], "op:p03:aims:d3.0")
    assert aims.lines(s, "p03") == ["for 0 days: Ask Tom about the allotment"]
    assert aims.lines(s, "p04") == []
    assert all(c.cause == "op:p03:aims:d3.0" for c in s.changes[-2:])
    aims.update(s, "p03", [], "op:p03:aims:d3.1")
    assert aims.lines(s, "p03") == []


def test_aims_are_listed_oldest_first_and_one_day_is_one_day():
    s = thin_town()
    aims.update(s, "p01", ["Write to the council about the lamp"], "op:p01:aims:d0.0")
    run_days(s, 1)
    aims.update(s, "p01", ["Buy seed potatoes", "Write to the council about the lamp"], "op:p01:aims:d1.0")
    assert aims.lines(s, "p01") == ["for 1 day: Write to the council about the lamp", "for 0 days: Buy seed potatoes"]


def test_the_same_list_again_changes_nothing():
    s = thin_town()
    aims.update(s, "p01", ["Buy seed potatoes"], "op:p01:aims:d0.0")
    before, n = s.hash(), len(s.changes)
    aims.update(s, "p01", ["Buy seed potatoes"], "op:p01:aims:d0.1")
    assert s.hash() == before and len(s.changes) == n


def test_aims_survive_junk():
    s = thin_town()
    aims.update(s, "p03", ["Pay the rent"], "op:p03:aims:d0.0")
    before = s.hash()
    for junk in (None, "Pay the rent", 7, {"a": 1}):
        aims.update(s, "p03", junk, "op:p03:aims:d0.1")
        assert s.hash() == before
    aims.update(s, "p03", [1, 2.5, None, ["x"], {"y": 1}, "   "], "op:p03:aims:d0.2")
    assert s.hash() == before                                   # nothing usable: unchanged
    aims.update(s, "p03", [f"aim {i}" for i in range(6)], "op:p03:aims:d0.3")
    assert aims.lines(s, "p03") == [f"for 0 days: aim {i}" for i in range(5)]
    aims.update(s, "p03", ["word " * 500, "a\x00b\x07 c"], "op:p03:aims:d0.4")
    [long, clean] = aims.lines(s, "p03")
    assert len(long.removeprefix("for 0 days: ").split()) == 20 and clean == "for 0 days: ab c"
    assert len(s.all("aims")) == 5                             # rows are reused, never more than five a person
