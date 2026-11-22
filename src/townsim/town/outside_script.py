"""Scripted stand-ins for what is beyond the people and the seat, for tests and scripted runs: no model is
called.

The lifter names the first register entry the statement names; the narrator files by the goal's first word
("Advertise" a medium visibility investment in the actor's shop, "Open" a small fit-out with a shop on
completion, "Club" a club, "Ask" a request to the first person named, "Pay" a redirect to pay, anything else a
question), the second filing the same unless the statement says "differ"; desks answer with a short email and,
for a supplier asked for a discount, a grant of 10% (which code clamps); the web's writer gives NONE for news and
a page of about 150 words otherwise. Everyone else is the hamlet's script (`reply`).
"""

from __future__ import annotations

import json
import re

from townsim.town import hamlet_script

OUTSIDE = ("lifter", "narrator", "web")


def is_outside(actor: str) -> bool:
    return actor in OUTSIDE or actor.startswith("desk-")


def _user(messages: list[dict]) -> str:
    return next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")


def _lift(text: str) -> str:
    statement = re.search(r"<statement>\n(.*)\n</statement>", text, re.S)
    words = statement.group(1).strip() if statement else ""
    register = re.findall(r"^(\w+\d+): (.+?) \(([^)]*)\)$", text, re.M)
    named = sorted(((words.find(name), rid, name, kind) for rid, name, kind in register if name and name in words),
                   key=lambda x: x[0])
    targets, others = [], []
    for _, rid, name, kind in named[:1]:
        (others if kind in ("person", "household") or "outside" in kind else targets).append((rid, name))
    first = " ".join(words.split()[:12])
    return json.dumps({"goal": first, "steps": [first],
                       "others_involved": [{"id": i, "name": n, "part": "asked"} for i, n in others],
                       "targets": [{"id": i, "name": n} for i, n in targets], "claims": [],
                       "addressed_to_reader": []})


def _goal(text: str) -> str:
    m = re.search(r'"goal": "([^"]*)"', text)
    return m.group(1) if m else ""


def _rule(text: str, n: int) -> str:
    goal = _goal(text)
    first = (goal.split() or [""])[0].strip(".,").capitalize()
    actor = re.search(r"ACTOR:\n(\w+\d+): ", text)
    shop = re.search(r"Owns and runs: (?:.*?)(org\d+): [^,]+, a food shop", text)
    block = text.split("NAMED:", 1)[-1].split("OPERATIONS:", 1)[0]
    named = re.search(r"^(p\d+): ", block, re.M)
    if first == "Advertise" and shop:
        entries = [{"op": "invest", "target": shop.group(1), "attribute": "visibility", "scale": "medium"}]
    elif first == "Open":
        entries = [{"op": "project", "kind": "fit_out", "scale": "small", "target": actor.group(1) if actor else None,
                    "on_complete": [{"op": "create_org", "ref": "new1", "kind": "shop", "name": "The Corner Pantry",
                                     "offering": "everyday food"}]}]
    elif first == "Club":
        entries = [{"op": "create_org", "ref": "new1", "kind": "club", "name": "The School Run Club",
                    "offering": "parents sharing the school run"}]
    elif first == "Ask" and named:
        entries = [{"op": "request", "to": named.group(1), "asks": "the use of a garage for storage", "on_accept": []}]
    elif first == "Pay":
        entries = [{"op": "redirect", "operation": "pay"}]
    else:
        entries = [{"op": "ask", "question": "What exactly will be done, and with whom?"}]
    if n == 1 and "differ" in goal:
        entries = [{"op": "ask", "question": "Which part comes first, and who does it?"}]
    return json.dumps({"entries": entries})


def _desk(actor: str, text: str) -> str:
    action = None
    shop = re.search(r"^- (.+?) \(a (?:shop|buying group)\)", text, re.M)
    email = re.search(r"<email>\n(.*)\n</email>", text, re.S)
    if shop and email and "discount" in email.group(1).lower():
        action = {"action": "grant_discount", "to": shop.group(1), "percent": 10}
    return json.dumps({"email": "Thank you for your email. We have looked at your account and written back below.",
                       "action": action})


def _web(text: str) -> str:
    subject = text.removeprefix("Subject: ").strip()
    if "news" in subject.lower():
        return "NONE"
    body = ("This page sets out the subject in general terms, as a reference book would. It describes what the "
            "subject is, how it is usually arranged, and the plain points that most accounts of it share. ") * 4
    words = body.split()[:150]
    return f"{subject[:1].upper()}{subject[1:]}: an overview\n" + " ".join(words)


def outside(key, messages: list[dict]) -> str | None:
    """The scripted reply of the lifter, the narrator, a desk or the web's writer; None for anyone else."""
    text = _user(messages)
    if key.actor == "lifter":
        return _lift(text)
    if key.actor == "narrator":
        return _rule(text, key.n)
    if key.actor == "web":
        return _web(text)
    if key.actor.startswith("desk-"):
        return _desk(key.actor, text)
    return None


def reply(key, messages: list[dict]) -> str | None:
    """`outside` for what is beyond the people, the hamlet's script for everyone else."""
    if is_outside(key.actor):
        return outside(key, messages)
    return hamlet_script.script(key, messages)
