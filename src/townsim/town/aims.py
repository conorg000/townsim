"""What is on a person's mind: up to five short lines in their own words, each with the day
it was first held (design 3.3; decision 7).

A reply may carry a new list; it replaces the old one whole. A new aim keeps the age of an
old one when it is at least 0.6 similar to it, so rewording a matter does not make it new.
Rows are slots: a person's rows are reused for their aims and an empty `text` marks one not
in use, so nothing is ever removed and a person never has more than five rows."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from townsim.town.acts import clean_text
from townsim.town.entities import Aim
from townsim.town.store import Store, id_key

__all__ = ["Aim", "MAX_AIMS", "MAX_WORDS", "SIMILAR", "update", "lines", "held"]

MAX_AIMS = 5
MAX_WORDS = 20
SIMILAR = 0.6


def _cleaned(value: Any) -> str:
    """An aim as kept: cleaned, on one line, at most 20 words; "" for anything else."""
    text = clean_text(value)
    return " ".join(text.split()[:MAX_WORDS]) if text else ""


def held(store: Store, pid: str) -> list[Aim]:
    """`pid`'s aims in use, oldest first, then in row order."""
    rows = [a for a in store.where("aims", person=pid) if a.text]
    return sorted(rows, key=lambda a: (a.since, id_key(a.id)))


def update(store: Store, pid: str, aims: Any, cause: str) -> None:
    """Replace `pid`'s aims with `aims`. A non-list changes nothing, and so does a list with
    no usable line in it; the empty list `[]` clears them. Elements that are not text, or are
    blank, are passed over; past five, the rest are dropped. Each keeps the `since` of the
    most similar old aim not already taken, if at least 0.6 similar."""
    if not isinstance(aims, list):
        return
    new = [t for t in (_cleaned(a) for a in aims) if t][:MAX_AIMS]
    if aims and not new:
        return
    old = held(store, pid)
    taken: set[int] = set()
    plan: list[tuple[str, int]] = []
    for text in new:
        best, score = None, SIMILAR
        for i, a in enumerate(old):
            if i in taken:
                continue
            ratio = SequenceMatcher(None, a.text.casefold(), text.casefold()).ratio()
            if ratio >= score and (best is None or ratio > score):
                best, score = i, ratio
        if best is not None:
            taken.add(best)
            plan.append((text, old[best].since))
        else:
            plan.append((text, store.day))
    slots = store.where("aims", person=pid)
    for i, (text, since) in enumerate(plan):
        if i < len(slots):
            row = slots[i]
            changed = {k: v for k, v in (("text", text), ("since", since)) if getattr(row, k) != v}
            if changed:
                store.set("aims", row.id, cause, **changed)
        else:
            store.add("aims", Aim(store.new_id("am", cause), pid, text, since), cause)
    for row in slots[len(plan):]:
        if row.text:
            store.set("aims", row.id, cause, text="")


def lines(store: Store, pid: str) -> list[str]:
    """`for <n> days: <text>` for each aim `pid` holds, oldest first."""
    out = []
    for a in held(store, pid):
        n = store.day - a.since
        out.append(f"for {n} {'day' if n == 1 else 'days'}: {a.text}")
    return out
