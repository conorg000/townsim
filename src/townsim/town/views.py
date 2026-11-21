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

import re
from typing import Any, NamedTuple

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import acts, aims, facts
from townsim.town.acts import accounts_of, name_of_who
from townsim.town.clock import date_text
from townsim.town.entities import Message
from townsim.town.obligations import money
from townsim.town.statement import line_rows, statement, statement_text
from townsim.town.store import Store, id_key

MAX_WORDS = 1200
STATEMENT_LINES = 25
IN_FULL = 3
PEOPLE_KEPT = 6
RESULTS_SHOWN = 6
RESULT_WORDS = 40
READ_WORDS = 250
READ_CUT = 120
LETTER_CUT = 100
REQUEST_CUT = 60

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


def _item_text(store: Store, row: Any, words: int | None = None) -> str:
    if not isinstance(row, Message):
        return row.text
    lines = [f"From {_letter_head(store, row)}: {_cut(row.text, words)}"]
    lines += [f"({name} is not someone you know.)" for name in row.unknown]
    return "\n".join(lines)


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


def _cut(text: str, n: int | None) -> str:
    """`text` up to its `n`th word, with `…` after it if anything was left out; whitespace
    and line breaks kept as they were."""
    if n is None:
        return text
    found = list(re.finditer(r"\S+", text))
    if len(found) <= n:
        return text
    return text[:found[n - 1].end()] + "\u2026" if n > 0 else "\u2026"


def _is_read(result) -> bool:
    return result.ok and result.op.strip().lower() == "read"


class _Results(NamedTuple):
    """The results shown (id, row), newest six oldest first; the ids of older ones passed
    over; and which shown one is the latest `read`."""

    shown: list
    passed: list[str]
    latest_read: str | None


def _results(store: Store, pid: str) -> _Results:
    rows = [r for _, _, r in sorted((r.day, id_key(r.id), r) for r in store.where("results", actor=pid, seen=False))]
    shown, passed = rows[-RESULTS_SHOWN:], [r.id for r in rows[:-RESULTS_SHOWN]]
    reads = [r.id for r in shown if _is_read(r)]
    return _Results(shown, passed, reads[-1] if reads else None)


def _result_text(r, latest_read: str | None, read_words: int) -> str:
    """`<op>: <text>`, each line after the first indented; cut to 40 words, or for the latest
    read to `read_words`; an older read is `<op>: shown before`."""
    if _is_read(r) and r.id != latest_read:
        return f"{r.op}: shown before"
    words = read_words if r.id == latest_read else RESULT_WORDS
    return _cut(f"{r.op}: {r.text}", words).replace("\n", "\n  ")


def _waiting(store: Store, pid: str, rank: dict[str, int] | None = None) -> list:
    """The open requests to `pid`: those not yet seen first, so each comes to be shown; then
    most pressing first."""
    rows = _ranked(store, pid, [rq for rq in store.where("requests", status="open") if rq.to == pid], rank)
    return [rq for rq in rows if not rq.seen] + [rq for rq in rows if rq.seen]


def _request_text(store: Store, rq, words: int | None) -> str:
    text = f"{rq.id} from {name_of_who(store, rq.sender)}: {_cut(rq.text, words)}"
    if rq.cents > 0:
        text += f" They ask you for {money(rq.cents)}."
    if rq.summary:
        text += f"\n  {rq.summary}"
    return text


def _hours(h: float) -> str:
    return f"{round(h, 2):g}"


# --- the layout -----------------------------------------------------------------------------


class _Parts(NamedTuple):
    """Everything a user message is made of, built once; `_user` lays out a trimmed choice."""

    results: _Results
    business: list[str]           # the business with two, one and no till weeks: [full, ..]
    book: str
    owed: str
    items: list[Any]              # inbox rows, most pressing first
    waiting: list                 # open requests, in the order they are shown
    people: list[str]
    knowhow: list[str]
    mind: list[str]
    notes: str
    hours: str
    operations: list[str]


class _Choice(NamedTuple):
    """How far each part has given way."""

    statement: Any
    people: int
    business: str
    in_full: int
    requests: int
    letter_words: int | None = None
    request_words: int | None = None
    read_words: int = READ_WORDS


class Layout(NamedTuple):
    """A view laid out: its two messages, the ids shown in full (and results passed over),
    and the steps of giving way that were taken, in order, as letters `a` to `g`."""

    system: str
    user: str
    ids: list[str]
    steps: list[str]


def _owed(store: Store, pid: str) -> str:
    """`facts.owed_lines` for the household, then under its name for each business."""
    accounts = accounts_of(store, pid)
    blocks = facts.owed_lines(store, accounts[0])
    for org in accounts[1:]:
        lines = facts.owed_lines(store, org)
        if lines:
            blocks += [name_of_who(store, org)] + lines
    return "\n".join(blocks)


def _parts(store: Store, pid: str) -> _Parts:
    person = store.get("people", pid)
    rank = facts.ranks(store, pid)
    weeks = facts.till_weeks(store, pid)
    business = [facts.business(store, pid, weeks=w) for w in range(weeks, -1, -1)] if weeks else \
        [facts.business(store, pid)]
    return _Parts(_results(store, pid), business, facts.bank_book(store, pid), _owed(store, pid),
                  _unseen(store, pid, rank), _waiting(store, pid, rank),
                  facts.people_lines(store, pid), facts.knowhow(store, pid), aims.lines(store, pid),
                  person.notes if person.notes.strip() else "", _hours(person.hours_left), acts.forms(store, pid))


def _user(store: Store, parts: _Parts, c: _Choice) -> str:
    sections = [f"TODAY: {date_text(store.day)}."]
    res = parts.results
    if res.shown:
        rows = [f"({len(res.passed)} earlier results not shown)"] if res.passed else []
        rows += [_result_text(r, res.latest_read, c.read_words) for r in res.shown]
        sections.append("WHAT CAME OF YOUR LAST ACTS\n" + "\n".join(rows))
    sections.append("YOUR HOUSEHOLD'S ACCOUNT\n" + statement_text(store, c.statement))
    if parts.book:
        sections.append("THE BANK'S BOOK\n" + parts.book)
    if c.business:
        sections.append("YOUR BUSINESS\n" + c.business)
    if parts.owed:
        sections.append("WHAT IS OWED\n" + parts.owed)
    if parts.items:
        rows = [_item_text(store, r, c.letter_words if i == 0 else None) for i, r in enumerate(parts.items[:c.in_full])]
        counted = _counted(parts.items[c.in_full:])
        sections.append("LETTERS AND NOTICES\n" + "\n".join(rows + ([counted] if counted else [])))
    if parts.waiting:
        rows = [_request_text(store, rq, c.request_words if i == 0 else None)
                for i, rq in enumerate(parts.waiting[:c.requests])]
        more = len(parts.waiting) - c.requests
        rows += [f"({more} more requests waiting)"] if more else []
        sections.append("WAITING FOR YOUR ANSWER\n" + "\n".join(rows))
    if parts.people[:c.people]:
        sections.append("PEOPLE YOU KNOW\n" + "\n".join(parts.people[:c.people]))
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


def layout(store: Store, pid: str) -> Layout:
    """The view of `pid`, about 1,200 words together on an ordinary day and never near 2,000.

    Everything is built once, then laid out again only as each step of giving way needs.
    While the whole is over 1,200 words, and each only as far as needed: (a) the household
    statement's in-and-out lines, oldest first, down to none (a line counting them appears);
    (b) the people known, down to the six closest; (c) the business's second till week;
    (d) the third and second letters or notices move to the counted line and wait unseen;
    (e) the third and second requests move to a counted line and wait unseen; (f) the one
    letter left is cut to 100 words and the one request left to 60; (g) the latest `read`
    result is cut to 120 words. What is written is already limited where it is made (a letter
    150 words, a request 80, notes 150, an aim 20), so what is left stays under 2,000."""
    system = _system(store, pid)
    parts = _parts(store, pid)
    st = statement(store, store.get("people", pid).household, ahead=facts.AHEAD, max_lines=STATEMENT_LINES)
    c = _Choice(st, len(parts.people), parts.business[0], min(IN_FULL, len(parts.items)),
                min(IN_FULL, len(parts.waiting)))
    base = _words(system)
    steps: list[str] = []

    def over(choice: _Choice) -> int:
        return base + _words(_user(store, parts, choice)) - MAX_WORDS

    left = over(c)
    if left > 0 and st.lines:                                                     # (a)
        sizes = [_words(row) for row in line_rows(store, st.lines)]
        note = _words(f"({st.earlier + 1} earlier lines not shown)") if st.earlier == 0 else 0
        drop, saved = 0, -note
        while drop < len(sizes) and saved < left:
            saved += sizes[drop]
            drop += 1
        if saved > 0:
            c = c._replace(statement=st._replace(lines=st.lines[drop:], earlier=st.earlier + drop))
            steps.append("a")
            left = over(c)
    if left > 0 and c.people > PEOPLE_KEPT:                                       # (b)
        sizes = [_words(p) for p in reversed(parts.people[PEOPLE_KEPT:])]
        cut = _needed(sizes, left)
        c = c._replace(people=c.people - cut)
        steps.append("b")
        left = over(c)
    if left > 0 and len(parts.business) > 2:                                      # (c)
        c = c._replace(business=parts.business[1])
        steps.append("c")
        left = over(c)
    for field in ("in_full", "requests"):                                        # (d), (e)
        if left > 0 and getattr(c, field) > 1:
            steps.append("d" if field == "in_full" else "e")
            while left > 0 and getattr(c, field) > 1:
                c = c._replace(**{field: getattr(c, field) - 1})
                left = over(c)
    if left > 0 and (c.in_full and isinstance(parts.items[0], Message) or c.requests):   # (f)
        c = c._replace(letter_words=LETTER_CUT, request_words=REQUEST_CUT)
        steps.append("f")
        left = over(c)
    if left > 0 and parts.results.latest_read is not None:                       # (g)
        c = c._replace(read_words=READ_CUT)
        steps.append("g")
    user = _user(store, parts, c)
    ids = ([r.id for r in parts.results.shown] + parts.results.passed + [r.id for r in parts.items[:c.in_full]]
           + [rq.id for rq in parts.waiting[:c.requests]])
    return Layout(system, user, ids, steps)


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
    v = layout(store, pid)
    return [{"role": "system", "content": v.system}, {"role": "user", "content": v.user}], v.ids


def shown_ids(store: Store, pid: str) -> list[str]:
    """The ids of the results, letters, notices and requests `person_view` shows in full,
    and of the results too old to be shown, which are passed over."""
    return layout(store, pid).ids


def person_view(store: Store, pid: str) -> list[dict]:
    """A system message and a user message for `pid`: see `layout`."""
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
