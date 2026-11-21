"""What a person is shown each day they act (design 2.8, 4.2): who they are, what came of
their last acts, how their household's account stands, what is new, what waits for
their answer, their notes, their hours, and the operations. Building a view changes
nothing."""

from __future__ import annotations

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import acts
from townsim.town.acts import name_of_who
from townsim.town.clock import date_text
from townsim.town.obligations import money
from townsim.town.statement import line_rows, statement, statement_text
from townsim.town.store import Store, id_key

MAX_WORDS = 1200
STATEMENT_LINES = 25

REPLY_FORM = ('Reply with JSON only, in this form:\n'
              '{"thoughts": "<what goes through your mind; no one else sees this>", '
              '"acts": [<operations from the list, in order>]}')


def _letter_head(store: Store, m) -> str:
    """Who a letter is from, and the org it was addressed to if it was."""
    head = name_of_who(store, m.sender)
    return f"{head} to {name_of_who(store, m.org)}" if m.org else head


def _new(store: Store, pid: str) -> list[tuple[str, str]]:
    """The unseen messages and notices to `pid`, oldest first, as (id, text shown)."""
    items = [(m.day, id_key(m.id), m.id, f"From {_letter_head(store, m)}: {m.text}")
             for m in store.where("messages", to=pid, seen=False)]
    items += [(n.day, id_key(n.id), n.id, n.text) for n in store.where("notices", to=pid, seen=False)]
    return [(i, text) for _, _, i, text in sorted(items)]


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


def _waiting(store: Store, pid: str) -> list[tuple[str, str]]:
    """The open requests to `pid`, in id order, as (id, text shown)."""
    rows = []
    for rq in store.where("requests", status="open"):
        if rq.to == pid:
            row = f"{rq.id} from {name_of_who(store, rq.sender)}: {rq.text}"
            if rq.cents > 0:
                row += f" They ask you for {money(rq.cents)}."
            rows.append((rq.id, row))
    return rows


def _hours(h: float) -> str:
    return f"{round(h, 2):g}"


def _user_text(store: Store, pid: str, account: str, results: list[tuple[str, str]], new: list[tuple[str, str]],
               hidden: int) -> str:
    """The user message with the `results`, the `account` text, the news `new` and a line
    counting the `hidden` items held back."""
    person = store.get("people", pid)
    sections = [f"TODAY: {date_text(store.day)}."]
    if results:
        sections.append("WHAT CAME OF YOUR LAST ACTS\n" + "\n".join(text for _, text in results))
    sections.append("YOUR HOUSEHOLD'S ACCOUNT\n" + account)
    if new or hidden:
        rows = [text for _, text in new] + ([f"({hidden} more not shown yet)"] if hidden else [])
        sections.append("NEW\n" + "\n".join(rows))
    waiting = _waiting(store, pid)
    if waiting:
        sections.append("WAITING FOR YOUR ANSWER\n" + "\n".join(text for _, text in waiting))
    if person.notes.strip():
        sections.append("YOUR NOTES\n" + person.notes)
    sections.append(f"You have {_hours(person.hours_left)} hours left this week.")
    sections.append("OPERATIONS\n" + "\n".join(acts.forms(store, pid)))
    return "\n\n".join(sections)


def _words(text: str) -> int:
    return len(text.split())


def _layout(store: Store, pid: str) -> tuple[str, list[str]]:
    """The user message within `MAX_WORDS` where it can be, and the ids of the results and
    news it shows.

    Results are always shown. The oldest statement lines give way first, then the newest
    news, which waits unseen for another day. The statement is built once: rows are
    joined by whitespace, so dropping a line saves exactly its own words, less the five
    of the "(n earlier lines not shown)" line that appears once any are dropped."""
    results = _results(store, pid)
    new = _new(store, pid)
    st = statement(store, store.get("people", pid).household, max_lines=STATEMENT_LINES)
    text = _user_text(store, pid, statement_text(store, st), results, new, 0)
    over = _words(text) - MAX_WORDS
    if over > 0 and st.lines:
        sizes = [_words(row) for row in line_rows(store, st.lines)]
        note = _words(f"({st.earlier + 1} earlier lines not shown)") if st.earlier == 0 else 0
        drop, saved = 0, -note
        while drop < len(sizes) and saved < over:
            saved += sizes[drop]
            drop += 1
        st = st._replace(lines=st.lines[drop:], earlier=st.earlier + drop)
        text = _user_text(store, pid, statement_text(store, st), results, new, 0)
    if _words(text) > MAX_WORDS and new:
        account = statement_text(store, st)
        total = len(new)
        while _words(text) > MAX_WORDS and new:
            new = new[:-1]
            text = _user_text(store, pid, account, results, new, total - len(new))
    return text, [i for i, _ in results] + [i for i, _ in new] + [i for i, _ in _waiting(store, pid)]


def _system(store: Store, pid: str) -> str:
    person = store.get("people", pid)
    household = store.get("households", person.household)
    return f"You are {person.name}, of the {household.name} household in {store.place}.\n\n{REPLY_FORM}"


def view(store: Store, pid: str) -> tuple[list[dict], list[str]]:
    """`person_view` and `shown_ids` together, laid out once."""
    text, ids = _layout(store, pid)
    return [{"role": "system", "content": _system(store, pid)}, {"role": "user", "content": text}], ids


def shown_ids(store: Store, pid: str) -> list[str]:
    """The ids of the results, messages, notices and requests `person_view` shows."""
    return _layout(store, pid)[1]


def person_view(store: Store, pid: str) -> list[dict]:
    """A system message and a user message for `pid`, the user message at most 1,200 words
    where dropping statement lines and holding back news can achieve it."""
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
