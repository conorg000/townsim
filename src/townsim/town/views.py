"""What a person is shown each day they act (design 2.8, 4.2): who they are, how their
household's account stands, what is new, what waits for their answer, their notes,
their hours, and the operations. Building a view changes nothing."""

from __future__ import annotations

import townsim.town.ops_core  # noqa: F401  (registers the operations)
from townsim.town import acts
from townsim.town.acts import name_of_who
from townsim.town.clock import date_text
from townsim.town.obligations import money
from townsim.town.statement import statement, statement_text
from townsim.town.store import Store, id_key

MAX_WORDS = 1200
STATEMENT_LINES = 25

REPLY_FORM = ('Reply with JSON only, in this form:\n'
              '{"thoughts": "<what goes through your mind; no one else sees this>", '
              '"acts": [<operations from the list, in order>]}')


def _new(store: Store, pid: str) -> list[tuple[str, str]]:
    """The unseen messages and notices to `pid`, oldest first, as (id, text shown)."""
    items = [(m.day, id_key(m.id), m.id, f"From {name_of_who(store, m.sender)}: {m.text}")
             for m in store.all("messages") if m.to == pid and not m.seen]
    items += [(n.day, id_key(n.id), n.id, n.text) for n in store.all("notices") if n.to == pid and not n.seen]
    return [(i, text) for _, _, i, text in sorted(items)]


def has_news(store: Store, pid: str) -> bool:
    """Whether any message or notice to `pid` is unseen."""
    return any(row.to == pid and not row.seen for table in ("messages", "notices") for row in store.all(table))


def _waiting(store: Store, pid: str) -> list[str]:
    rows = []
    for rq in store.all("requests"):
        if rq.to == pid and rq.status == "open":
            row = f"{rq.id} from {name_of_who(store, rq.sender)}: {rq.text}"
            if rq.cents > 0:
                row += f" They ask you for {money(rq.cents)}."
            rows.append(row)
    return rows


def _hours(h: float) -> str:
    return f"{round(h, 2):g}"


def _user_text(store: Store, pid: str, max_lines: int, new: list[tuple[str, str]], hidden: int) -> tuple[str, int]:
    """The user message with at most `max_lines` statement lines, the news `new` and a line
    counting the `hidden` items held back; and how many statement lines it had."""
    person = store.get("people", pid)
    st = statement(store, person.household, max_lines=max_lines)
    sections = [f"TODAY: {date_text(store.day)}.",
                "YOUR HOUSEHOLD'S ACCOUNT\n" + statement_text(store, st)]
    if new or hidden:
        rows = [text for _, text in new] + ([f"({hidden} more not shown yet)"] if hidden else [])
        sections.append("NEW\n" + "\n".join(rows))
    waiting = _waiting(store, pid)
    if waiting:
        sections.append("WAITING FOR YOUR ANSWER\n" + "\n".join(waiting))
    if person.notes.strip():
        sections.append("YOUR NOTES\n" + person.notes)
    sections.append(f"You have {_hours(person.hours_left)} hours left this week.")
    sections.append("OPERATIONS\n" + "\n".join(acts.forms(store, pid)))
    return "\n\n".join(sections), len(st.lines)


def _layout(store: Store, pid: str) -> tuple[str, list[str]]:
    """The user message within `MAX_WORDS` where it can be, and the ids of the news it shows.

    The oldest statement lines give way first, then the newest news, which waits unseen
    for another day."""
    new = _new(store, pid)
    total = len(new)
    text, lines = _user_text(store, pid, STATEMENT_LINES, new, 0)
    while len(text.split()) > MAX_WORDS and lines > 0:
        text, lines = _user_text(store, pid, lines - 1, new, 0)
    while len(text.split()) > MAX_WORDS and new:
        new = new[:-1]
        text, _ = _user_text(store, pid, 0, new, total - len(new))
    return text, [i for i, _ in new]


def shown_ids(store: Store, pid: str) -> list[str]:
    """The ids of the messages and notices `person_view` shows as new."""
    return _layout(store, pid)[1]


def person_view(store: Store, pid: str) -> list[dict]:
    """A system message and a user message for `pid`, the user message at most 1,200 words
    where dropping statement lines and holding back news can achieve it."""
    person = store.get("people", pid)
    household = store.get("households", person.household)
    system = f"You are {person.name}, of the {household.name} household in {store.place}.\n\n{REPLY_FORM}"
    return [{"role": "system", "content": system}, {"role": "user", "content": _layout(store, pid)[0]}]


def mark_seen(store: Store, pid: str, ids: list[str], cause: str) -> None:
    """Mark as seen the messages and notices to `pid` among `ids` that are still unseen."""
    for i in ids:
        for table in ("messages", "notices"):
            if store.has(table, i):
                row = store.get(table, i)
                if row.to == pid and not row.seen:
                    store.set(table, i, cause, seen=True)
