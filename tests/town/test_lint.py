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
