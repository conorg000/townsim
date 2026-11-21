"""What a person is shown each day they act (design 2.8, 3.3, 4.2, 4.5).

The system message is who they are (`facts.who`) and the form of a reply. The user message
is their day, in sections in a fixed order, each left out when it is empty: the date; what
came of their last acts; their household's account; their business; what is owed; their
letters and notices; requests waiting for their answer; appointments; the people they know;
how their trade works; what is on their mind; their notes; their hours; the operations.

The inbox shows the three most pressing letters and notices in full (closest ties first, a
notice ranking with the household, then oldest) and counts the rest, which stay unseen;
requests waiting are shown three at a time in the same order. A view is at most 1,200 words,
system and user together, where giving way can achieve it. Building a view changes nothing.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import acts, aims, facts
from townsim.town.acts import accounts_of, name_of_who
from townsim.town.clock import date_text
from townsim.town.entities import Message
from townsim.town.obligations import money
from townsim.town.statement import agreements_text, line_rows, statement, statement_text
from townsim.town.store import Store, id_key

MAX_WORDS = 1200
STATEMENT_LINES = 25
IN_FULL = 3
PEOPLE_KEPT = 6

REPLY_FORM = ('Reply with JSON only, in this form:\n'
              '{"thoughts": "<what goes through your mind; no one else sees this>", '
              '"aims": [<up to five short lines: what is on your mind now>], '
              '"acts": [<operations from the list, in order>]}')


def _words(text: str) -> int:
    return len(text.split())


# --- what reached them --------------------------------------------------------------------


def _letter_head(store: Store, m) -> str:
    """Who a letter is from, and the org it was addressed to if it was."""
    head = name_of_who(store, m.sender)
    return f"{head} to {name_of_who(store, m.org)}" if m.org else head


def _ranked(store: Store, pid: str, rows: list, rank: dict[str, int] | None = None) -> list:
    """`rows` (letters, notices or requests) by the sender's tie rank (a notice as the
    household), then day, then id."""
    rank = facts.ranks(store, pid) if rank is None else rank

    def key(row: Any) -> tuple:
        sender = getattr(row, "sender", None)
        r = 0 if sender is None else rank.get(sender, acts.UNKNOWN)
        return r, row.day, id_key(row.id)
    return sorted(rows, key=key)


def _unseen(store: Store, pid: str, rank: dict[str, int] | None = None) -> list:
    rows = store.where("messages", to=pid, seen=False) + store.where("notices", to=pid, seen=False)
    return _ranked(store, pid, rows, rank)


def _counted(rest: list) -> str:
    """`(<n> more: <a> letters, <b> notices)` for the items not shown in full."""
    if not rest:
        return ""
    a = len([r for r in rest if isinstance(r, Message)])
    b = len(rest) - a

    def n(k: int, word: str) -> str:
        return f"{k} {word}" + ("" if k == 1 else "s")
    return f"({len(rest)} more: {n(a, 'letter')}, {n(b, 'notice')})"


def inbox(store: Store, pid: str) -> tuple[list, int, str]:
    """The unseen letters and notices to `pid` shown in full (at most three, most pressing
    first), how many more wait, and the line that counts them ("" when none do)."""
    rows = _unseen(store, pid)
    return rows[:IN_FULL], len(rows[IN_FULL:]), _counted(rows[IN_FULL:])


def _item_text(store: Store, row: Any) -> str:
    if not isinstance(row, Message):
        return row.text
    lines = [f"From {_letter_head(store, row)}: {row.text}"]
    lines += [f"({name} is not someone you know.)" for name in row.unknown]
    return "\n".join(lines)


def _results(store: Store, pid: str) -> list[tuple[str, str]]:
    """The unseen results of `pid`'s acts, oldest first, as (id, text shown): `<op>: <text>`,
    each line after the first indented by two spaces."""
    rows = sorted((r.day, id_key(r.id), r.id, r) for r in store.where("results", actor=pid, seen=False))
    return [(r.id, f"{r.op}: {r.text}".replace("\n", "\n  ")) for _, _, _, r in rows]


def _wakes(result) -> bool:
    """A refusal, or what was read, brings its actor a turn; other results wait for one."""
    return not result.ok or result.op.strip().lower() == "read"


def with_news(store: Store) -> set[str]:
    """Everyone with a message or notice unseen, or a result of theirs that brings a turn.
    (A set for membership only: never iterated.)"""
    found = {row.to for table in ("messages", "notices") for row in store.where(table, seen=False)}
    return found | {r.actor for r in store.where("results", seen=False) if _wakes(r)}


def has_news(store: Store, pid: str) -> bool:
    """Whether `pid` has a message or notice unseen, or a result of theirs that brings a turn."""
    return pid in with_news(store)


def _waiting(store: Store, pid: str, rank: dict[str, int] | None = None) -> tuple[list[tuple[str, str]], int]:
    """The open requests to `pid` shown in full, as (id, text shown), and how many more wait:
    those not yet seen first, so each comes to be shown; then most pressing first."""
    rows = _ranked(store, pid, [rq for rq in store.where("requests", status="open") if rq.to == pid], rank)
    rows = [rq for rq in rows if not rq.seen] + [rq for rq in rows if rq.seen]
    shown = []
    for rq in rows[:IN_FULL]:
        text = f"{rq.id} from {name_of_who(store, rq.sender)}: {rq.text}"
        if rq.cents > 0:
            text += f" They ask you for {money(rq.cents)}."
        if rq.summary:
            text += f"\n  {rq.summary}"
        shown.append((rq.id, text))
    return shown, len(rows[IN_FULL:])


def _hours(h: float) -> str:
    return f"{round(h, 2):g}"


# --- the layout -----------------------------------------------------------------------------


class _Parts(NamedTuple):
    """Everything a user message is made of, built once; `_user` lays out a trimmed choice."""

    results: list[tuple[str, str]]
    business: list[str]           # the business with two, one and no till weeks: [full, ..]
    owed: str
    items: list[Any]              # inbox rows, most pressing first
    item_texts: list[str]
    waiting: list[tuple[str, str]]
    waiting_more: int
    people: list[str]
    knowhow: list[str]
    mind: list[str]
    notes: str
    hours: str
    operations: list[str]


def _owed(store: Store, pid: str) -> str:
    """`agreements_text` for the household, then under its name for each business."""
    accounts = accounts_of(store, pid)
    blocks = []
    household = agreements_text(store, accounts[0])
    if household != "nothing":
        blocks.append(household)
    for org in accounts[1:]:
        text = agreements_text(store, org)
        if text != "nothing":
            blocks.append(f"{name_of_who(store, org)}\n{text}")
    return "\n".join(blocks)


def _parts(store: Store, pid: str) -> _Parts:
    person = store.get("people", pid)
    rank = facts.ranks(store, pid)
    items = _unseen(store, pid, rank)
    waiting, waiting_more = _waiting(store, pid, rank)
    weeks = facts.till_weeks(store, pid)
    business = [facts.business(store, pid, weeks=w) for w in range(weeks, -1, -1)] if weeks else \
        [facts.business(store, pid)]
    return _Parts(_results(store, pid), business, _owed(store, pid), items,
                  [_item_text(store, r) for r in items[:IN_FULL]], waiting, waiting_more,
                  facts.people_lines(store, pid), facts.knowhow(store, pid), aims.lines(store, pid),
                  person.notes if person.notes.strip() else "", _hours(person.hours_left), acts.forms(store, pid))


def _user(store: Store, parts: _Parts, account: str, people: int, business: str, in_full: int) -> str:
    sections = [f"TODAY: {date_text(store.day)}."]
    if parts.results:
        sections.append("WHAT CAME OF YOUR LAST ACTS\n" + "\n".join(text for _, text in parts.results))
    sections.append("YOUR HOUSEHOLD'S ACCOUNT\n" + account)
    if business:
        sections.append("YOUR BUSINESS\n" + business)
    if parts.owed:
        sections.append("WHAT IS OWED\n" + parts.owed)
    if parts.items:
        rows = parts.item_texts[:in_full]
        counted = _counted(parts.items[in_full:])
        sections.append("LETTERS AND NOTICES\n" + "\n".join(rows + ([counted] if counted else [])))
    if parts.waiting:
        more = [f"({parts.waiting_more} more waiting)"] if parts.waiting_more else []
        sections.append("WAITING FOR YOUR ANSWER\n" + "\n".join([t for _, t in parts.waiting] + more))
    if parts.people[:people]:
        sections.append("PEOPLE YOU KNOW\n" + "\n".join(parts.people[:people]))
    if parts.knowhow:
        sections.append("HOW THINGS WORK\n" + "\n".join(parts.knowhow))
    if parts.mind:
        sections.append("ON YOUR MIND\n" + "\n".join(parts.mind))
    if parts.notes:
        sections.append("YOUR NOTES\n" + parts.notes)
    sections.append(f"You have {parts.hours} hours left this week.")
    sections.append("OPERATIONS\n" + "\n".join(parts.operations))
    return "\n\n".join(sections)


def _system(store: Store, pid: str) -> str:
    return f"{facts.who(store, pid)}\n\n{REPLY_FORM}"


def _layout(store: Store, pid: str) -> tuple[str, str, list[str]]:
    """The system and user messages within `MAX_WORDS` together where they can be, and the
    ids of the results, letters, notices and requests shown in full.

    Everything is built once. Sections are joined by whitespace, so what each piece that
    gives way would save is its own words; the trims are worked out from those counts and
    the message laid out once more. What gives way, in order: the household statement's
    oldest lines, down to none (a line counting them appears once any go); the people
    known beyond the six closest; the business's second till week; then the third and
    second letters or notices, which move to the counted line and wait unseen. Results and
    requests never give way."""
    system = _system(store, pid)
    parts = _parts(store, pid)
    st = statement(store, store.get("people", pid).household, ahead=facts.AHEAD, max_lines=STATEMENT_LINES)
    in_full = min(IN_FULL, len(parts.items))
    business = parts.business[0]
    people = len(parts.people)
    text = _user(store, parts, statement_text(store, st), people, business, in_full)
    over = _words(system) + _words(text) - MAX_WORDS
    if over > 0 and st.lines:
        sizes = [_words(row) for row in line_rows(store, st.lines)]
        note = _words(f"({st.earlier + 1} earlier lines not shown)") if st.earlier == 0 else 0
        drop, saved = 0, -note
        while drop < len(sizes) and saved < over:
            saved += sizes[drop]
            drop += 1
        if saved > 0:
            st = st._replace(lines=st.lines[drop:], earlier=st.earlier + drop)
            over -= saved
    if over > 0 and people > PEOPLE_KEPT:
        cut = min(people - PEOPLE_KEPT, _needed([_words(p) for p in reversed(parts.people[PEOPLE_KEPT:])], over))
        over -= sum(_words(p) for p in parts.people[people - cut:people])
        people -= cut
    if over > 0 and len(parts.business) > 2:
        over -= _words(business) - _words(parts.business[1])
        business = parts.business[1]
    while over > 0 and in_full > 1:
        counted_before = _words(_counted(parts.items[in_full:]))
        in_full -= 1
        over -= _words(parts.item_texts[in_full]) - (_words(_counted(parts.items[in_full:])) - counted_before)
    text = _user(store, parts, statement_text(store, st), people, business, in_full)
    ids = [i for i, _ in parts.results] + [r.id for r in parts.items[:in_full]] + [i for i, _ in parts.waiting]
    return system, text, ids


def _needed(sizes: list[int], over: int) -> int:
    """How many of `sizes`, taken in order, it takes to save `over` words (all of them if
    that is not enough)."""
    saved = 0
    for n, size in enumerate(sizes, 1):
        saved += size
        if saved >= over:
            return n
    return len(sizes)


def view(store: Store, pid: str) -> tuple[list[dict], list[str]]:
    """`person_view` and `shown_ids` together, laid out once."""
    system, text, ids = _layout(store, pid)
    return [{"role": "system", "content": system}, {"role": "user", "content": text}], ids


def shown_ids(store: Store, pid: str) -> list[str]:
    """The ids of the results, letters, notices and requests `person_view` shows in full."""
    return _layout(store, pid)[2]


def person_view(store: Store, pid: str) -> list[dict]:
    """A system message and a user message for `pid`, together at most 1,200 words where
    giving way can achieve it."""
    return view(store, pid)[0]


def mark_seen(store: Store, pid: str, ids: list[str], cause: str) -> None:
    """Mark as seen the messages, notices and requests to `pid`, and the results of `pid`'s
    acts, among `ids` that are still unseen."""
    for i in ids:
        for table, owner in (("messages", "to"), ("notices", "to"), ("requests", "to"), ("results", "actor")):
            if store.has(table, i):
                row = store.get(table, i)
                if getattr(row, owner) == pid and not row.seen:
                    store.set(table, i, cause, seen=True)
