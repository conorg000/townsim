"""What a person is shown each day they act (design 2.8, 3.3, 4.2, 4.5).

The system message is who they are (`facts.who`) and the form of a reply. The user message
is their day, in sections in a fixed order, each left out when it is empty: the date; what
came of their last acts; their household's account; their business; what is owed; their
letters and notices; the talks they have had since they were last shown them; requests
waiting for their answer; the talks booked with them; the people they know;
how their trade works; what is on their mind; their notes; their hours; the operations.

The inbox shows the three most pressing letters and notices in full (closest ties first, a
notice ranking with the household, then oldest) and counts the rest, which stay unseen;
requests waiting are shown three at a time in the same order. A view is about 1,200 words on
an ordinary day and never over 2,000, system and user together (see `layout`). Building a
view changes nothing.

What a person wrote is always set off from what code wrote: a letter's or a request's text
stands under its own `From <name>:` or `<id> from <name>:` line with every line of it begun by
`  > `; their own notes and aims are indented; memos and quoted values are one line, cut to a
label; a notice is one line; a turn in a talk stands under its own `<name> said:` line, on one
line begun `  > `, under `With <name> on <date>:`. So a line at the left margin that begins with
a heading, `From `, `With `, `<id> from `, `<name> said:` or `(` and a digit was written by code.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import acts, aims, facts, talk, unknown
from townsim.town.acts import CHARS_A_WORD, accounts_of, name_of_who
from townsim.town.clock import date_text
from townsim.town.entities import Message
from townsim.town.obligations import money
from townsim.town.facts import cut_words as _cut
from townsim.town.statement import Counted, statement
from townsim.town.store import Store, id_key

MAX_WORDS = 1200
CEILING = 2000
CHAR_CEILING = 16000
STATEMENT_LINES = 25
IN_FULL = 3
TALKS_SHOWN = 2
TALK_WORDS = 40
TALK_CUT = 20
APPOINTMENTS_SHOWN = 5
PEOPLE_KEPT = 6
RESULTS_SHOWN = 9                  # a turn's eight acts and the line about any more
RESULT_WORDS = 80                  # room for a proposal's result, which repeats what the other reads
NOT_ALL = "(not everything is shown today)"
READ_WORDS = 250
READ_CUT = 120

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


def _quoted(text: str) -> list[str]:
    """What a person wrote, each of its lines set off with `  > ` (a blank one as `  >`), so
    that none of it can stand at the margin where the view's own lines stand."""
    return [f"  > {line}" if line.strip() else "  >" for line in text.split("\n")]


def _item_lines(store: Store, row: Any) -> list[str]:
    """A notice, on one line; or a letter: `From <name>:` on its own, then the letter, each
    line set off with `  > `, and beneath it a line for each name in it that the reader does
    not know (at most three, then a line counting the rest)."""
    if not isinstance(row, Message):
        return [" ".join(row.text.split())]
    lines = [f"From {_letter_head(store, row)}:"] + _quoted(row.text)
    return lines + unknown.reader_lines(unknown.present(row.unknown, row.text))


def _item_text(store: Store, row: Any) -> str:
    return "\n".join(_item_lines(store, row))


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


def _is_read(result) -> bool:
    return result.ok and result.op.strip().lower() == "read"


class _Results(NamedTuple):
    """The results shown, the newest nine oldest first; the ids of older ones passed over;
    and which shown one is the latest `read`."""

    shown: list
    passed: list[str]
    latest_read: str | None


def _results(store: Store, pid: str) -> _Results:
    rows = [r for _, _, r in sorted((r.day, id_key(r.id), r) for r in store.where("results", actor=pid, seen=False))]
    shown, passed = rows[-RESULTS_SHOWN:], [r.id for r in rows[:-RESULTS_SHOWN]]
    reads = [r.id for r in shown if _is_read(r)]
    return _Results(shown, passed, reads[-1] if reads else None)


def _fit(text: str, words: int, chars: int) -> str:
    """`text` cut to `words` words and then to `chars` characters on a word boundary, with
    `…` where it was cut."""
    text = _cut(text, words)
    if len(text) <= chars:
        return text
    head = text[:chars]
    space = max(head.rfind(" "), head.rfind("\n"))
    return (head[:space] if space > 0 else head).rstrip() + "\u2026"


def _result_text(r, latest_read: str | None, read_words: int) -> str:
    """`<op>: <text>`, each line after the first indented: cut to 80 words and 640
    characters; the latest read to `read_words` words and eight characters a word; an
    older read in the same batch is not shown."""
    if _is_read(r) and r.id != latest_read:
        return f"{r.op}: not shown, a later read follows"
    words = read_words if r.id == latest_read else RESULT_WORDS
    op = r.op if r.op.strip().lower() in acts.REGISTRY or r.op == "(no operation named)" else f"'{r.op}'"
    if _is_read(r):                                                # a listing code wrote: its lines stay
        return _fit(f"{op}: {r.text}".replace("\n", "\n  "), words, CHARS_A_WORD * words)
    return _fit(" ".join(f"{op}: {r.text}".split()), words, CHARS_A_WORD * words)


def _waiting(store: Store, pid: str, rank: dict[str, int] | None = None) -> list:
    """The open requests to `pid`: those not yet seen first, so each comes to be shown; then
    most pressing first."""
    rows = _ranked(store, pid, [rq for rq in store.where("requests", status="open") if rq.to == pid], rank)
    return [rq for rq in rows if not rq.seen] + [rq for rq in rows if rq.seen]


def _request_lines(store: Store, rq) -> list[str]:
    """`<id> from <name>:` on its own, the request set off as a letter is, then what code
    writes of it: the sum asked for and the summary. A request code made for its sender (an
    offer, a proposed agreement, a loan asked for) has no text of theirs: its summary alone
    follows the header."""
    head = f"{rq.id} from {name_of_who(store, rq.sender)}:"
    summary = [" ".join(rq.summary.split())] if rq.summary.strip() else []
    if not rq.text.strip():
        return [head] + summary + unknown.flags(store, rq.to, rq.sender, request_words(rq))
    lines = [head] + _quoted(rq.text)
    if rq.cents > 0:
        lines.append(f"They ask you for {money(rq.cents)}.")
    return lines + unknown.flags(store, rq.to, rq.sender, rq.text) + summary


def request_words(rq) -> str:
    """What the sender of a request wrote in it: its text, or the words quoted in the summary
    code wrote of an agreement or a loan asked for."""
    if rq.text.strip() or rq.kind not in ("agreement", "loan"):
        return rq.text
    end = rq.summary.rfind('"')
    return rq.summary[rq.summary.rfind('"', 0, end) + 1:end] if end > 0 else ""


def _hours(h: float) -> str:
    return f"{round(h, 2):g}"


def _appointments(store: Store, pid: str) -> list[str]:
    """`<id>: with <name> on <date>, about <"label">` for each talk booked with `pid` from
    today on, soonest first: at most five, then a line counting the rest."""
    rows = sorted((ap for ap in store.all("appointments")
                   if ap.status == "booked" and pid in (ap.a, ap.b) and ap.day >= store.day),
                  key=lambda ap: (ap.day, id_key(ap.id)))
    out = []
    for ap in rows[:APPOINTMENTS_SHOWN]:
        other = ap.b if ap.a == pid else ap.a
        out.append(f"{ap.id}: with {name_of_who(store, other)} on {date_text(ap.day)}, about {ap.about}")
        out += unknown.flags(store, pid, ap.a, ap.about)
    if len(rows) > APPOINTMENTS_SHOWN:
        out.append(Counted(f"({len(rows) - APPOINTMENTS_SHOWN} more not shown)"))
    return out


def _talks_counted(k: int) -> str:
    return f"({k} more talk{'' if k == 1 else 's'} not shown)"


# --- the layout -----------------------------------------------------------------------------


class _Parts(NamedTuple):
    """Everything a user message is made of, built once."""

    results: _Results
    business: list[list[str]]     # the business's rows with two, one and no till weeks: [full, ..]
    book: list[str]
    accounts: list[str]
    items: list[Any]              # inbox rows, most pressing first
    talks: list                   # talks held since they were last shown them, oldest first
    waiting: list                 # open requests, in the order they are shown
    appointments: list[str]
    people: list[str]
    knowhow: list[str]
    mind: list[str]
    notes: str
    hours: str
    operations: list[str]
    pid: str = ""


class _Choice(NamedTuple):
    """How far each part has given way."""

    statement: Any
    people: int
    business: list[str]
    owed: int
    in_full: int
    requests: int
    read_words: int = READ_WORDS
    talks: int = TALKS_SHOWN
    talk_words: int = TALK_WORDS


class _Sec(NamedTuple):
    """A section: its heading (None for a line on its own), its lines each with the id of the
    item it belongs to (or None), how many leading lines may never go, and whether it may lose
    lines at all."""

    head: str | None
    lines: list[tuple[str, str | None]]
    keep: int = 0
    fixed: bool = False

    def text(self) -> str:
        return "\n".join(([self.head] if self.head else []) + [t for t, _ in self.lines])


class Layout(NamedTuple):
    """A view laid out: its two messages, the ids shown in full (and results passed over),
    and the steps of giving way that were taken, in order: `statement`, `people`, `till`,
    `owed`, `talks`, `inbox`, `requests`, `read`, and `guard` for the last guard."""

    system: str
    user: str
    ids: list[str]
    steps: list[str]


def _owed(store: Store, accounts: list[str], most: int) -> list[str]:
    """`facts.owed_lines` for the household, then under its name for each business."""
    lines = facts.owed_lines(store, accounts[0], most)
    for org in accounts[1:]:
        more = facts.owed_lines(store, org, most)
        if more:
            lines += [name_of_who(store, org)] + more
    return lines


def _parts(store: Store, pid: str) -> _Parts:
    person = store.get("people", pid)
    rank = facts.ranks(store, pid)
    weeks = facts.till_weeks(store, pid)
    business = [facts.business_rows(store, pid, weeks=w) for w in range(weeks, -1, -1)] if weeks else \
        [facts.business_rows(store, pid)]
    return _Parts(_results(store, pid), business, facts.bank_book_rows(store, pid), accounts_of(store, pid),
                  _unseen(store, pid, rank), talk.unseen_talks(store, pid), _waiting(store, pid, rank),
                  _appointments(store, pid),
                  facts.people_lines(store, pid), facts.knowhow(store, pid), aims.lines(store, pid),
                  person.notes if person.notes.strip() else "", _hours(person.hours_left), acts.forms(store, pid), pid)


COUNTED = "#counted"               # the owner of a line that counts what is not shown
MARK = "#mark"                     # the owner of the guard's own line


def _rows(lines: list[str]) -> list[tuple[str, str | None]]:
    """Lines of a section, each flagged as counted where code made it so (`Counted`)."""
    return [(str(x), COUNTED if isinstance(x, Counted) else None) for x in lines]


def _plain(text: str) -> list[tuple[str, str | None]]:
    return [(line, None) for line in text.split("\n")] if text else []


def _own(text: str) -> list[tuple[str, str | None]]:
    """The person's own words, every line indented by two spaces."""
    return [(f"  {line}" if line.strip() else "", None) for line in text.split("\n")]


def _sections(store: Store, parts: _Parts, c: _Choice) -> list[_Sec]:
    out = [_Sec(None, [(f"TODAY: {date_text(store.day)}.", None)], fixed=True)]
    res = parts.results
    if res.shown:
        rows = [(f"({len(res.passed)} earlier results not shown)", COUNTED)] if res.passed else []
        rows += _plain("\n".join(_result_text(r, res.latest_read, c.read_words) for r in res.shown))
        out.append(_Sec("WHAT CAME OF YOUR LAST ACTS", rows, fixed=True))
    out.append(_Sec("YOUR HOUSEHOLD'S ACCOUNT", _rows(facts.statement_rows(store, c.statement))))
    if parts.book:
        out.append(_Sec("THE BANK'S BOOK", _rows(parts.book)))
    if c.business:
        out.append(_Sec("YOUR BUSINESS", _rows(c.business)))
    owed = _owed(store, parts.accounts, c.owed)
    if owed:
        out.append(_Sec("WHAT IS OWED", _rows(owed)))
    if parts.items:
        rows = [(line, r.id) for i, r in enumerate(parts.items[:c.in_full])
                for line in _item_lines(store, r)]
        counted = _counted(parts.items[c.in_full:])
        out.append(_Sec("LETTERS AND NOTICES", rows + ([(counted, COUNTED)] if counted else [])))
    if parts.talks:
        rows = [(line, t.id) for t in parts.talks[:c.talks] for line in talk.summary_lines(store, parts.pid, t, c.talk_words)]
        more = len(parts.talks) - c.talks
        out.append(_Sec("TALKS", rows + ([(_talks_counted(more), COUNTED)] if more > 0 else [])))
    if parts.waiting:
        rows = [(line, rq.id) for rq in parts.waiting[:c.requests] for line in _request_lines(store, rq)]
        more = len(parts.waiting) - c.requests
        keep = len(_request_lines(store, parts.waiting[0]))
        out.append(_Sec("WAITING FOR YOUR ANSWER",
                        rows + ([(f"({more} more requests waiting)", COUNTED)] if more else []), keep=keep))
    if parts.appointments:
        out.append(_Sec("APPOINTMENTS", _rows(parts.appointments)))
    if parts.people[:c.people]:
        out.append(_Sec("PEOPLE YOU KNOW", _plain("\n".join(parts.people[:c.people]))))
    if parts.knowhow:
        out.append(_Sec("HOW THINGS WORK", _plain("\n".join(parts.knowhow))))
    if parts.mind:
        out.append(_Sec("ON YOUR MIND", _own("\n".join(parts.mind))))
    if parts.notes:
        out.append(_Sec("YOUR NOTES", _own(parts.notes)))
    out.append(_Sec(None, [(f"You have {parts.hours} hours left this week.", None)], fixed=True))
    out.append(_Sec("OPERATIONS", _plain("\n".join(parts.operations)), fixed=True))
    return out


def _render(secs: list[_Sec]) -> str:
    return "\n\n".join(s.text() for s in secs)


def _guard(secs: list[_Sec], words: int, chars: int) -> tuple[list[_Sec], set[str], bool]:
    """While the user message is `words` words or more, or more than `chars` characters,
    take whole lines (a whole letter, notice or request at a time) from the end of the
    longest section that may lose them, and end it with `(not everything is shown today)`.
    A counted line, and the lines a section must keep, never go. Returns the sections, the
    ids of what was taken, and whether anything was."""
    secs = list(secs)
    taken: set[str] = set()
    fired = False

    def removable(s: _Sec) -> list[int]:
        out = []
        for k, (_, owner) in enumerate(s.lines):
            if owner in (MARK, COUNTED) or k < s.keep:
                continue
            if owner is not None and any(o == owner for _, o in s.lines[:s.keep]):
                continue
            out.append(k)
        return out

    while True:
        text = _render(secs)
        by_chars = len(text) > chars
        if not by_chars and _words(text) < words:
            break
        open_ = [i for i, s in enumerate(secs) if not s.fixed and removable(s)]
        if not open_:
            break
        size = (lambda k: len(secs[k].text())) if by_chars else (lambda k: _words(secs[k].text()))
        i = max(open_, key=lambda k: (size(k), -k))
        s = secs[i]
        k = removable(s)[-1]
        owner = s.lines[k][1]
        if owner is None:
            lines = s.lines[:k] + s.lines[k + 1:]
        else:
            lines = [ln for ln in s.lines if ln[1] != owner]
            taken.add(owner)
        lines = [ln for ln in lines if ln[1] != MARK] + [(NOT_ALL, MARK)]
        secs[i] = s._replace(lines=lines)
        fired = True
    return secs, taken, fired


def layout(store: Store, pid: str) -> Layout:
    """The view of `pid`: about 1,200 words on an ordinary day, never over 2,000.

    What anyone writes is limited where it is written, in words and in characters
    (`acts.fits`: a letter 150 words, a request 80, notes 150, an answer's note 80, a
    payment's note 12; an aim 20 words and 160 characters), and everything a view shows from
    the store is bounded: what people wrote in memos is cut to labels of 12 words and 100
    characters; results are cut to 80 words and 640 characters (the latest read to 250 words
    and 2,000 characters), and counted past nine; upcoming payments are counted past ten
    going out and six coming in; what is owed past eight lines an account; names not known past three
    a letter. Everything is built once, then laid out again only as each step of giving way
    needs. While the whole is over 1,200 words, and each only as far as needed: the household
    statement's in-and-out lines, oldest first, down to none (`statement`); the people known,
    down to the six closest (`people`); the business's second till week (`till`); what is
    owed, down to four lines an account (`owed`); the second talk, which moves to the counted
    line and waits unseen, and then each turn of the first cut to 20 words in place of 40
    (`talks`); the third and second letters or notices,
    which move to the counted line and wait unseen (`inbox`); the third and second requests,
    likewise (`requests`); the latest read result cut to 120 words (`read`). Letters and
    requests are never cut: what is shown is shown whole. If the whole is still 2,000 words or
    more, or more than 16,000 characters, the last guard (`guard`) takes whole lines from the
    end of the longest section that may lose them until it is under both, and ends that
    section with `(not everything is shown today)`; the date, the results, the first
    request, the hours, the operations and every counted line never go, and a letter, notice
    or request it takes is not counted as shown and waits unseen."""
    system = _system(store, pid)
    parts = _parts(store, pid)
    st = statement(store, store.get("people", pid).household, ahead=facts.AHEAD, max_lines=STATEMENT_LINES)
    c = _Choice(st, len(parts.people), parts.business[0], facts.OWED_LINES, min(IN_FULL, len(parts.items)),
                min(IN_FULL, len(parts.waiting)))
    base = _words(system)
    steps: list[str] = []

    def over(choice: _Choice) -> int:
        return base + _words(_render(_sections(store, parts, choice))) - MAX_WORDS

    left = over(c)
    if left > 0 and st.lines:
        sizes = [_words(row) for row in facts.transfer_rows(store, st.lines)]
        note = _words(f"({st.earlier + 1} earlier lines not shown)") if st.earlier == 0 else 0
        drop, saved = 0, -note
        while drop < len(sizes) and saved < left:
            saved += sizes[drop]
            drop += 1
        if saved > 0:
            c = c._replace(statement=st._replace(lines=st.lines[drop:], earlier=st.earlier + drop))
            steps.append("statement")
            left = over(c)
    if left > 0 and c.people > PEOPLE_KEPT:
        c = c._replace(people=c.people - _needed([_words(p) for p in reversed(parts.people[PEOPLE_KEPT:])], left))
        steps.append("people")
        left = over(c)
    if left > 0 and len(parts.business) > 2:
        c = c._replace(business=parts.business[1])
        steps.append("till")
        left = over(c)
    if left > 0 and _owed(store, parts.accounts, facts.OWED_KEPT) != _owed(store, parts.accounts, c.owed):
        c = c._replace(owed=facts.OWED_KEPT)
        steps.append("owed")
        left = over(c)
    if left > 0 and len(parts.talks) > 1:
        c = c._replace(talks=1)
        steps.append("talks")
        left = over(c)
    if left > 0 and parts.talks:
        c = c._replace(talk_words=TALK_CUT)
        if "talks" not in steps:
            steps.append("talks")
        left = over(c)
    for field, step in (("in_full", "inbox"), ("requests", "requests")):
        if left > 0 and getattr(c, field) > 1:
            steps.append(step)
            while left > 0 and getattr(c, field) > 1:
                c = c._replace(**{field: getattr(c, field) - 1})
                left = over(c)
    if left > 0 and parts.results.latest_read is not None:
        c = c._replace(read_words=READ_CUT)
        steps.append("read")
        left = over(c)
    secs = _sections(store, parts, c)
    secs, taken, fired = _guard(secs, CEILING - base, CHAR_CEILING - len(system) - 0)
    if fired:
        steps.append("guard")
    ids = ([r.id for r in parts.results.shown] + parts.results.passed
           + [r.id for r in parts.items[:c.in_full] if r.id not in taken]
           + [rq.id for rq in parts.waiting[:c.requests] if rq.id not in taken]
           + [t.id for t in parts.talks[:c.talks] if t.id not in taken])
    return Layout(system, _render(secs), ids, steps)


def _needed(sizes: list[int], over: int) -> int:
    """How many of `sizes`, taken in order, it takes to save `over` words (all of them if
    that is not enough)."""
    saved = 0
    for n, size in enumerate(sizes, 1):
        saved += size
        if saved >= over:
            return n
    return len(sizes)


def _system(store: Store, pid: str) -> str:
    return f"{facts.who(store, pid)}\n\n{REPLY_FORM}"


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
    """Mark as seen the messages, notices and requests to `pid`, the results of `pid`'s acts
    and the talks `pid` took part in, among `ids` that are still unseen."""
    for i in ids:
        if store.has("talks", i) and pid in store.get("talks", i).unseen:
            store.set("talks", i, cause, unseen=[p for p in store.get("talks", i).unseen if p != pid])
            continue
        for table, owner in (("messages", "to"), ("notices", "to"), ("requests", "to"), ("results", "actor")):
            if store.has(table, i):
                row = store.get(table, i)
                if getattr(row, owner) == pid and not row.seen:
                    store.set(table, i, cause, seen=True)
