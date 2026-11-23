"""Every distinct refusal and error text that people and assistants received in kept runs, reduced to a shape so
that like texts group: names, ids, sums, dates, counts and quoted words become placeholders. Calls no model.

`town-refusals` prints the shapes, most frequent first, each with its count, who received it and one full
example; a shape that the no-model check (`tests/town/test_every_path.py`) also met, as it wrote them to
`runs/every-path-texts-<seed>.txt`, is marked `[checked]`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from townsim.town.store import Store

WEEKDAYS = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DAY_WORDS = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_DATE = re.compile(rf"\b(?:(?:{WEEKDAYS}|{_DAY_WORDS})\.?,? )?\d{{1,2}} (?:{MONTHS})(?: \d{{4}})?\b"
                   rf"|\b(?:{_DAY_WORDS} )?\d{{4}}-\d{{2}}-\d{{2}}\b|\b(?:{WEEKDAYS})\b")
_SUM = re.compile(r"-?\$-?[\d,]+(?:\.\d+)?")
_DISPLAY_ID = re.compile(r"\b[a-z]{2,5}_[A-Z0-9]{3,}\b")
_STORE_ID = re.compile(r"\b(?:p|h|org|as|ob|rq|sub|msg|n|rm|tk|jb|du|mb|ts|rl|vn|pl|cn|lg|rt|hr|ht|pc|ak|ap|ft|rec|"
                       r"dk|pr|sp|in|sy)\d+\b")
_CARD = re.compile(r"••\d+")
_QUOTED = re.compile(r"(?<![A-Za-z])'[^']{1,200}'(?![A-Za-z])|\"[^\"]{1,200}\"")
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)*\b")
_SPACE = re.compile(r"\s+")


def names_of(store: Store) -> list[str]:
    """Every name a text may hold of this town, longest first: people (whole, first and family names), households,
    organisations, assistants, products, desks' signers and the place."""
    out: set[str] = {store.place} if getattr(store, "place", "") else set()
    for p in store.all("people"):
        out.add(p.name)
        out.update(w for w in p.name.split() if len(w) > 1)
    for table in ("households", "orgs", "instances", "products"):
        for row in store.all(table):
            name = getattr(row, "name", "")
            if name:
                out.add(name)
    for d in store.all("desks"):
        out.add(d.signer)
        out.update(w for w in d.signer.split() if len(w) > 1)
    return sorted((n for n in out if n and n[0].isupper()), key=lambda n: (-len(n), n))


def shape(text: str, names: list[str]) -> str:
    """The text with what differs between like texts made placeholders, on one line."""
    body = text
    if body.startswith("{"):
        try:
            got = json.loads(body)
            body = got.get("message") or got.get("error") or body
        except (ValueError, AttributeError):
            pass
    body = _SPACE.sub(" ", body).strip()
    body = _QUOTED.sub("'<text>'", body)
    if names:
        body = re.sub(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")(?:'s)?(?![\w])",
                      lambda m: "<name>'s" if m.group(0).endswith("'s") else "<name>", body)
    body = _DISPLAY_ID.sub("<id>", body)
    body = _STORE_ID.sub("<id>", body)
    body = _CARD.sub("<card>", body)
    body = _DATE.sub("<date>", body)
    body = _SUM.sub("$<sum>", body)
    body = _NUMBER.sub("<n>", body)
    body = re.sub(r"<name>(?: <name>)+", "<name>", body)
    body = re.sub(r"<date>(?: <date>)+", "<date>", body)
    return body


@dataclass
class Shape:
    shape: str
    count: int = 0
    readers: Counter = field(default_factory=Counter)
    example: str = ""
    where: str = ""
    checked: bool = False


def received(db, run_id: str) -> list[tuple[str, str, str]]:
    """(reader, text, where) for every refusal and error the run's people and assistants received, in order: each
    act of a person's that was not done, each tool call of an assistant's that was declined or failed, and each act
    in a talk that was not done."""
    out = []
    for day in db.turn_days(run_id):
        for t in db.turns(run_id, day):
            for o in t.outcomes:
                if not o.ok:
                    out.append(("assistant" if t.pid.startswith("as") else "person", o.msg, f"{run_id} day {day} {t.pid}"))
    for day in db.seat_days(run_id):
        for st in db.seat_turns(run_id, day):
            for c in st.calls:
                if not c.ok:
                    out.append(("assistant", c.result, f"{run_id} day {day} {st.instance} {c.tool}"))
            for t in getattr(st, "talk_turns", []) or []:
                for o in getattr(t, "outcomes", []):
                    if not o.ok:
                        out.append(("person", o.msg, f"{run_id} day {day} {t.pid} (talk)"))
    return out


def every_path_shapes(folder: Path) -> set[str]:
    """The shapes of the texts the no-model check met, from the files it wrote, each seed's names taken from that
    seed's town as the check generates it."""
    from townsim.town import gen, lane_b, product
    out: set[str] = set()
    for path in sorted(folder.glob("every-path-texts-*.txt")):
        try:
            seed = int(path.stem.rsplit("-", 1)[1])
        except ValueError:
            continue
        s = gen.generate(seed, ["a", "b", "c", "d"])
        lane_b.install(s)
        product.launch(s)
        names = names_of(s)
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"- \[.*?\] (.*)$", line)
            if m:
                out.add(shape(m.group(1), names))
    return out


def gather(sources: list[tuple[object, str]], checked: set[str]) -> list[Shape]:
    """The shapes of every text received in `sources` ((db, run id) pairs), most frequent first."""
    from townsim.town.rundb import NotKept
    shapes: dict[str, Shape] = {}
    for db, run_id in sources:
        days = db.snapshot_days(run_id)
        names: list[str] = []
        if days:
            try:
                names = names_of(db.load_snapshot(run_id, days[-1]))
            except (NotKept, Exception):        # noqa: BLE001  (a snapshot this code cannot read: no names)
                names = []
        for reader, text, where in received(db, run_id):
            key = shape(text, names)
            got = shapes.setdefault(key, Shape(key, example=text, where=where, checked=key in checked))
            got.count += 1
            got.readers[reader] += 1
    return sorted(shapes.values(), key=lambda x: (-x.count, x.shape))


def lines(found: list[Shape]) -> list[str]:
    total = sum(x.count for x in found)
    out = [f"{len(found)} distinct texts, {total} received; [checked]: also met by the no-model check", ""]
    for x in found:
        who = ", ".join(f"{r} {n}" for r, n in sorted(x.readers.items()))
        mark = " [checked]" if x.checked else ""
        out.append(f"{x.count:5d}  ({who}){mark}  {x.shape}")
        example = _SPACE.sub(" ", x.example).strip()
        out.append(f"       e.g. {x.where}: {example[:400]}")
    return out
