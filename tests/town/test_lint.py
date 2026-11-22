"""A lint over everything people are shown in eight scripted weeks of seeds 1 to 3: the lines
code wrote state the records of the day, carry their years where a summary needs them, never
advise, and name what a shop sells one way."""

import re

import pytest

from .helpers import scripted_weeks

BANNED = re.compile(r"\byou (may|can|could|should)\b|\bconsider\b|\bremember to\b|\bit is wise\b|\bdon't forget\b"
                    r"|\bat most three times\b|\bcan be changed at any time\b|\bsimulat|\bgames?\b|\bplayers?\b"
                    r"|\bturns?\b", re.IGNORECASE)
SELLS = re.compile(r"\bunits?\b|\bcustomers?\b", re.IGNORECASE)
DATE = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) \d{1,2} [A-Z][a-z]+\b(?! \d{4})")
SUMMARY = re.compile(r" (would )?pays? .+ \$[\d,.]+ (a week for \d+ weeks? from|a week from .+ until either side|once on)"
                     r"| asks the bank for | asks to end |^Your rent for |"
                     r"rent for .+ changes from | lends .+ \(ob\d+\)|^Agreed \(ob|Ended \(ob")
OWN = ("ON YOUR MIND", "YOUR NOTES")


def code_lines(user: str, system: str) -> list[str]:
    """The lines of a view code wrote: not a person's words set off with `  > `, not their own
    notes and aims, and with the quoted memos people wrote taken out."""
    out = []
    for block in [system] + user.split("\n\n"):
        lines = block.split("\n")
        if lines and lines[0] in OWN:
            continue
        for line in lines:
            if line.lstrip().startswith(">"):
                continue
            out.append(re.sub(r'"[^"]*"', '""', line))
    return out


@pytest.mark.parametrize("seed", (1, 2, 3))
def test_what_people_are_shown_states_the_records(seed):
    _, _, asked = scripted_weeks(seed)
    assert asked
    for day, pid, purpose, system, user, ties in asked:
        assert ties == [], (seed, day, pid, ties)
        lines = code_lines(user, system)
        for line in lines:
            found = BANNED.search(line)
            assert found is None, (seed, day, pid, line)
            assert SELLS.search(line) is None, (seed, day, pid, line)
            if SUMMARY.search(line):
                summary = line.split(" It stays open until ")[0]
                assert DATE.search(summary) is None, (seed, day, pid, line)
        every = user.split("\n")
        for i, line in enumerate(every):
            if re.fullmatch(r"\s*\(\d+ earlier lines not shown\)", line):
                assert i + 1 < len(every) and re.match(r"\s+[A-Z][a-z]+day \d", every[i + 1]), (seed, day, pid, line)


@pytest.mark.parametrize("seed", (1, 2, 3))
def test_an_owner_is_shown_the_suppliers_and_the_other_shops_prices_almost_every_day(seed):
    s, _, asked = scripted_weeks(seed)
    owners = {o.owner for o in s.all("orgs") if o.kind == "shop"}
    views = [user for _, pid, purpose, _, user, _ in asked if purpose == "turn" and pid in owners]
    assert views
    both = [u for u in views if "Suppliers' prices a basket today: " in u and "Prices a basket in the other shops: " in u]
    assert len(both) >= 0.9 * len(views), (seed, len(both), len(views))


@pytest.mark.parametrize("seed", (1, 2, 3, 4, 5))
def test_over_21_weeks_no_step_of_giving_way_adds_words_and_no_view_is_over_1600(seed):
    _, laid, _ = scripted_weeks(seed)
    assert len({d for d, *_ in laid}) > 100
    for day, pid, words, chars, trail, steps in laid:
        assert all(b <= a for a, b in zip(trail, trail[1:])), (seed, day, pid, steps, trail)
        assert words <= trail[-1] and words <= 1600, (seed, day, pid, words, steps)
        assert words < 2000 and chars <= 16000


ALSO_LINE = re.compile(r"[A-Z][\w'-]+(?: [A-Z][\w'-]+)+(?:, \d+ [A-Z][\w ]+)?(?:, (?:runs|works at) [^$]+)?")
OWED_LINE = re.compile(r"ob\d+: .+, \$[\d,]+\.\d\d (?:a week|a day|every \d+ days|once on [^;]+) (?:to|from) .+"
                       r"; (?:last paid [^;]+|nothing paid yet)(?:; \$[\d,]+\.\d\d overdue since [^;]+)?(?:; next due .+)?")


@pytest.mark.parametrize("seed", (1, 3))
def test_the_new_sections_are_records_of_the_day(seed):
    _, _, asked = scripted_weeks(seed)
    seen = {"also": 0, "owed": 0, "savings": 0, "time": 0}
    for day, pid, purpose, system, user, _ in asked:
        if purpose != "turn":
            continue
        for block in user.split("\n\n"):
            lines = block.split("\n")
            if lines[0].startswith("ALSO IN "):
                for x in lines[1:]:
                    assert ALSO_LINE.fullmatch(x) and not re.search(r"benefit|out of work|\$", x), (seed, day, pid, x)
                    seen["also"] += 1
            elif lines[0] in ("YOU OWE", "OWED TO YOU"):
                for x in lines[1:]:
                    if x.endswith(":") or x.startswith("("):
                        continue
                    assert OWED_LINE.fullmatch(re.sub(r'"[^"]*"', '""', x)), (seed, day, pid, x)
                    seen["owed"] += 1
        for x in user.split("\n"):
            if x.startswith("Savings: "):
                assert re.fullmatch(r"Savings: \$[\d,]+\.\d\d", x), x
                seen["savings"] += 1
            if x.startswith("Time of your own"):
                assert re.fullmatch(r"Time of your own left this week, outside your work: [\d.]+ hours?\.", x), x
                seen["time"] += 1
    assert all(seen.values()), seen
