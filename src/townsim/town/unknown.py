"""Names in what a person wrote that are no one its reader knows (design 3.4, 11).

A reader knows the people of their household, everyone tied to them, everyone they have
written to or had a letter from, and anyone whose request they have been shown; and the
organisations in their own records: those of the place, their employers, their landlord,
the bank, the suppliers of a shop they own, and anyone they pay or are paid by on a
schedule. What is flagged, once each, in the order it first appears:

- a title (`Mr`, `Mrs`, `Ms`, `Miss`, `Dr`, with or without a full stop) and a capitalised
  word, unless someone the reader knows bears that surname (or a title and a full name the
  reader knows);
- a given name from `given_names.txt` and a capitalised word, unless that is the full name
  of someone the reader knows;
- capitalised words ending in an organisation's word (`Council`, `Bank`, `Lettings`, ...),
  unless they are the name of an organisation in the reader's records;
- a given name standing alone that no one the reader knows bears, unless it is also an
  ordinary word (`Will`, `Grace`, `Frank`, ...).

Names the reader knows are taken out of the text before it is read, so a known name is
never read as part of another. Nothing here changes the store, and nothing says whether
anyone so named exists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from townsim.town.acts import knows
from townsim.town.clock import DAYS, MONTHS
from townsim.town.store import Store
from townsim.town.ties import kept

GIVEN = frozenset((Path(__file__).with_name("given_names.txt")).read_text().split())
TITLES = frozenset({"Mr", "Mrs", "Ms", "Miss", "Dr"})
ORG_WORDS = frozenset({"Council", "Bank", "Lettings", "Ltd", "Limited", "Agency", "Office", "Services", "Company",
                       "Co"})
# Given names that are also ordinary words: never flagged alone, and not at the start of a sentence.
AMBIGUOUS = frozenset({
    "Will", "Mark", "Bill", "Rose", "Grace", "Hope", "Joy", "Faith", "Frank", "Jack", "Pat", "Sue", "Penny", "Dawn",
    "Art", "Guy", "Ray", "Don", "Earl", "Grant", "Miles", "Max", "Dean", "Glen", "Dale", "Cliff", "Heath", "Brook",
    "Holly", "Ivy", "Ruby", "Pearl", "Amber", "Iris", "Violet", "Olive", "Lily", "Hazel", "Sandy", "Rich", "Rob",
    "Nick", "Bob", "Gene", "Carol", "Victor", "Norman", "Sally", "Basil", "Lee", "Robin", "Rowan", "Ross", "Bonnie",
    "Felicity", "Constance", "Prudence", "Patience", "Florence", "Lewis", "Scott", "Stewart", "Murray", "Terry",
    "Harry", "Pierce", "Chase", "Hunter", "Wade", "Lance", "Kim", "Lynn", "Polly", "Nell", "Mona", "Eve", "Una",
    "Victoria",
})
# Words naming a place: a given name before one is a street's or a building's name, not a person's.
PLACE_WORDS = frozenset({"Road", "Street", "Lane", "Avenue", "Close", "Square", "Row", "Hill", "Park", "Way", "Drive",
                         "Court", "Place", "Gardens", "Terrace", "Crescent", "Walk", "Station", "Church", "Chapel",
                         "School", "Hall", "House", "Cottage", "Farm", "Green"})
# Words before a given name that make it a saint's, a sovereign's or a title's, not a person here.
NOT_PEOPLE_BEFORE = frozenset({"St", "Saint", "Queen", "King", "Prince", "Princess", "Lord", "Lady"})
# Capitalised words that are never the second half of a person's name.
NOT_SURNAMES = frozenset(set(DAYS) | set(MONTHS) | {
    "I", "Road", "Street", "Lane", "Close", "Row", "Hill", "Side", "End", "Head", "Square", "Avenue", "Park", "Way",
    "Drive", "Court", "Place", "Gardens", "Terrace", "Green", "Crescent", "Walk", "Day", "Night", "Morning",
    "Evening", "Christmas", "Easter", "Sunday", "Church", "Chapel", "School", "Hall", "House", "Cottage", "Farm"})
# Words that open a sentence or a phrase before a name, and are no part of it.
LEADING = frozenset({
    "The", "A", "An", "Ask", "Dear", "Tell", "From", "To", "At", "With", "Write", "Call", "See", "Try", "Our",
    "My", "Your", "His", "Her", "Their", "This", "That", "And", "But", "Or", "If", "So", "Yes", "No", "Hello", "Hi",
    "Thanks", "Thank", "Please", "Old", "New", "Even", "Only", "Then", "When", "As", "By", "For", "In", "On", "Of",
    "Not", "Did", "Does", "Do", "Has", "Have", "Is", "Was", "Like", "Both", "Some", "Any", "Every", "Its"})
# The generic words before an organisation's word that make a common noun of it, not a name.
GENERIC = frozenset({"Post", "Head", "Town", "Parish", "County", "District", "Social", "Local", "Main", "Central",
                     "Booking", "Ticket", "Home", "Village", "Sorting", "Box", "Building", "Lost", "Property",
                     "Housing", "Council", "Benefits", "Tax", "Savings", "Food", "Customer", "Health"})
NOT_BODIES_AFTER = frozenset({"Holiday", "Holidays"})        # `Bank Holiday` is a day, not a body

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*|\S")
_MASK = "\x00"
NOT_KNOWN = "not anyone you know."   # how the writer is told of names they do not know
MOST = 3                            # names told of a piece of writing at most


class Known(NamedTuple):
    """What one reader knows: full names, first names and surnames of people (casefolded),
    and the names of organisations, longest first."""

    full: frozenset[str]
    firsts: frozenset[str]
    surnames: frozenset[str]
    names: tuple[str, ...]          # every known name, to be taken out of a text before it is read


def record_orgs(store: Store, reader: str) -> list[str]:
    """The organisations in `reader`'s own records."""
    p = store.get("people", reader)
    accounts = {p.household} | {o.id for o in store.all("orgs") if o.owner == reader}
    found = {o.id for o in store.all("orgs") if not o.outside}
    found |= {j.org for j in store.where("jobs", person=reader)}
    h = store.get("households", p.household)
    if h.landlord:
        found.add(h.landlord)
    for table in ("obligations",):
        for ob in store.all(table):
            if ob.payer in accounts:
                found.add(ob.payee)
            elif ob.payee in accounts:
                found.add(ob.payer)
    if any(o.owner == reader and store.where("firms", org=o.id) for o in store.all("orgs")):
        found |= {sp.org for sp in store.all("suppliers")}
    return [store.get("orgs", o).name for o in found if store.has("orgs", o)]


def known(store: Store, reader: str, also: tuple[str, ...] = ()) -> Known:
    """What `reader` knows, with the people `also` names (the writer, say) counted as known."""
    shown = [rq.sender for rq in store.all("requests") if rq.to == reader and rq.seen]
    people = [store.get("people", q).name for q in dict.fromkeys([reader, *also, *knows(store, reader), *shown])
              if store.has("people", q)]
    full = frozenset(n.casefold() for n in people)
    firsts = frozenset(n.split()[0].casefold() for n in people if n.split())
    surnames = frozenset(n.split()[-1].casefold() for n in people if len(n.split()) > 1)
    names = sorted(set(people) | set(record_orgs(store, reader)), key=lambda n: (-len(n), n))
    return Known(full, firsts, surnames, tuple(n for n in names if n.strip()))


def _base(word: str) -> str:
    """A word without a possessive `'s`."""
    for tail in ("'s", "’s"):
        if word.endswith(tail) and len(word) > len(tail) + 1:
            return word[:-len(tail)]
    return word


def _cap(word: str) -> bool:
    """A capitalised word: a capital, then at least one small letter."""
    return len(word) >= 2 and word[0].isupper() and any(c.islower() for c in word[1:]) and word[0].isalpha()


def _masked(text: str, k: Known) -> str:
    """`text` with every known name in it taken out."""
    if not k.names:
        return text
    pattern = re.compile(r"(?<![\w])(?:" + "|".join(re.escape(n) for n in k.names) + r")(?![\w])", re.IGNORECASE)
    return pattern.sub(_MASK, text)


def names_in(k: Known, text: str) -> list[str]:
    """The names in `text` that are no one the reader of `k` knows, in the order they appear."""
    masked = _masked(text, k)
    found = [(m.group(0), m.start()) for m in _TOKEN.finditer(masked)]
    words = [w for w, _ in found]
    n = len(words)
    used = [False] * n
    named: list[tuple[int, str]] = []

    def sentence_start(i: int) -> bool:
        before = masked[:found[i][1]].rstrip()
        return not before or before[-1] in '.!?:;"“(\n'

    for i, w in enumerate(words):                       # saints, sovereigns and lords
        if w.rstrip(".") in NOT_PEOPLE_BEFORE and i + 1 < n and _base(words[i + 1]) in GIVEN:
            used[i] = used[i + 1] = True
    for i, w in enumerate(words):                       # organisations
        if w not in ORG_WORDS or i == 0 or not _cap(words[i - 1]) or (i + 1 < n and words[i + 1] in ORG_WORDS):
            continue
        if i + 1 < n and words[i + 1] in NOT_BODIES_AFTER:
            for x in range(i - 1, i + 2):
                used[x] = True
            continue
        j = i - 1
        while j > 0 and _cap(words[j - 1]) and i - (j - 1) <= 3:
            j -= 1
        while j < i and (words[j] in LEADING or words[j] in TITLES):
            j += 1
        if j < i and not all(_base(words[x]) in GENERIC for x in range(j, i)):
            named.append((j, " ".join(_base(words[x]) for x in range(j, i)) + f" {w}"))
            for x in range(j, i + 1):
                used[x] = True
    for i, w in enumerate(words):                       # a title and a name
        if used[i] or _base(w).rstrip(".") not in TITLES or not w[0].isupper():
            continue
        title = _base(w).rstrip(".")
        at = i + 1
        if at < n and words[at] == ".":
            at += 1
        caps = []
        while at < n and len(caps) < 2 and _cap(words[at]) and not used[at] and words[at] not in NOT_SURNAMES:
            caps.append(at)
            at += 1
        if not caps:
            continue
        if len(caps) == 2 and _base(words[caps[0]]) in GIVEN:
            name = f"{_base(words[caps[0]])} {_base(words[caps[1]])}"
            unknown = name.casefold() not in k.full
        else:
            caps = caps[:1]
            name = _base(words[caps[0]])
            unknown = name.casefold() not in k.surnames and name.casefold() not in k.firsts
        if unknown:
            named.append((i, f"{title} {name}"))
        for x in [i, *caps]:
            used[x] = True
    for i, w in enumerate(words):                       # a given name and a surname
        first = _base(w)
        if used[i] or not _cap(w) or first not in GIVEN or i + 1 >= n:
            continue
        second = words[i + 1]
        if _base(second) in PLACE_WORDS:                # a street or a building named for someone
            used[i] = used[i + 1] = True
            continue
        if not _cap(second) or used[i + 1] or _base(second) in NOT_SURNAMES:
            continue
        if first in AMBIGUOUS and sentence_start(i) and second in LEADING:
            continue
        name = f"{first} {_base(second)}"
        if name.casefold() not in k.full:
            named.append((i, name))
        used[i] = used[i + 1] = True
    for i, w in enumerate(words):                       # a given name alone
        first = _base(w)
        if used[i] or not _cap(w) or first not in GIVEN or first in AMBIGUOUS or first.casefold() in k.firsts:
            continue
        named.append((i, first))
    out: list[str] = []
    for _, name in sorted(named):
        if name not in out:
            out.append(name)
    return out


def with_person(k: Known, name: str) -> Known:
    """`k` with the person called `name` counted as known too."""
    if not name.strip() or name.casefold() in k.full:
        return k
    parts = name.split()
    names = tuple(sorted(set(k.names) | {name}, key=lambda n: (-len(n), n)))
    return Known(k.full | {name.casefold()}, k.firsts | {parts[0].casefold()},
                 k.surnames | ({parts[-1].casefold()} if len(parts) > 1 else set()), names)


def _known_now(store: Store, reader: str) -> Known:
    """`known`, kept while the store is unchanged (a view is built without changing it)."""
    return kept(store, ("known", reader), lambda: known(store, reader))


def unknown_names(store: Store, reader: str, writer: str, text: str) -> list[str]:
    """The names in `text`, written by `writer`, that are no one `reader` knows."""
    if not text or not text.strip():
        return []
    k = _known_now(store, reader)
    if store.has("people", writer):
        k = with_person(k, store.get("people", writer).name)
    return names_in(k, text)


def flags(store: Store, reader: str, writer: str, text: str) -> list[str]:
    """The lines a reader is shown beneath `text`, written by `writer` (`reader_lines`)."""
    return reader_lines(unknown_names(store, reader, writer, text))


def reader_lines(names: list[str]) -> list[str]:
    """What the reader is told beneath a piece of writing: a line for each of at most three of
    `names`, then a line counting the rest."""
    lines = [f"({n} is not someone you know.)" for n in names[:MOST]]
    if len(names) > MOST:
        lines.append(f"({len(names) - MOST} more names in it are not people you know.)")
    return lines


def writer_text(names: list[str]) -> str:
    """What the writer is told in the result of what they sent: ` <Name> is not anyone you
    know.` for each of at most three of `names` (and how many more), or ""."""
    if not names:
        return ""
    head = names[:MOST]
    text = " " + (f"{head[0]} is" if len(head) == 1 else ", ".join(head[:-1]) + f" and {head[-1]} are")
    text += " " + NOT_KNOWN
    if len(names) > MOST:
        text += f" Nor are {len(names) - MOST} more names in it."
    return text


def present(names: list[str], text: str) -> list[str]:
    """Those of `names` that stand in `text` as whole words."""
    return [n for n in names if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text)]
