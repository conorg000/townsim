"""The narrator probe: 26 fixed statements filed by the lifter and the narrator on seed 1 with lane B.

Seed 1 (or another) is played for a week on the hamlet's script without a model, and lane B installed on the
morning of day 7. Each statement of `tests/data/attempts_b.jsonl` is undertaken by its named actor on a fresh
copy of that morning and narrated (one lift, two filings) through the replier. Each row records whether the
lift parsed, whether each filing is valid, whether they agree, their signatures, the status code gave, the
quote, what was filed, and the cost. The gates are read, not enforced. This is a probe of the setup.
"""

from __future__ import annotations

import json
import pickle
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from townsim.town import gen, hamlet_script, lane_b, narrator, tasks
from townsim.town.acts import Act, apply_act
from townsim.town.replies import Keys, Reply, ScriptedReplier, StopRun, refused
from townsim.town.store import id_key

MAX_CAP = 1.0
DAYS = 7
BUDGET = 50_000                  # dollars each statement may spend: enough that the quote decides
ATTEMPTS = Path(__file__).resolve().parents[3] / "tests" / "data" / "attempts_b.jsonl"
GATES = {"parse": 0.95, "valid": 0.90, "agreement": 0.70, "pairs": 5, "lane_a": 3, "to_reader": 4, "over_claim": 4}


@dataclass
class Row:
    id: str
    group: str
    actor: str
    text: str
    pair: int | None = None
    form: str | None = None
    parsed: bool = False
    valid: list[bool] = field(default_factory=list)
    agreed: bool = False
    signatures: list[str] = field(default_factory=list)
    status: str = ""
    note: str = ""
    price_cents: int = 0
    entries: list = field(default_factory=list)
    followed: bool = False
    granted: bool = False
    cost: float = 0.0
    error: str = ""
    rulings: list[str] = field(default_factory=list)     # each filing as the narrator wrote it
    why: list[str] = field(default_factory=list)         # why code refused each filing ("" for a valid one)


class ProbeStopped(Exception):
    def __init__(self, why: str, rows: list[Row]) -> None:
        super().__init__(why)
        self.rows = rows


def morning(seed: int = 1, families: list[str] | None = None, start: str | None = None):
    """Day 7 of the seed after a week of the hamlet's script, with lane B installed that morning."""
    from townsim.town.loop import run_day
    s = gen.generate(seed, families or ["a", "b", "c", "d"], start=start)
    keys = Keys("probe")
    for _ in range(DAYS):
        run_day(s, ScriptedReplier(hamlet_script.script), keys)
    lane_b.install(s)
    return s


def roles(store) -> dict[str, str]:
    """The names a statement's placeholders take, and the actors' ids: the first shop and its owner, another
    adult of a household that owns nothing, and the letting agency."""
    from townsim.town.systems import ADULT
    shop = next(o for o in sorted(store.all("orgs"), key=lambda o: id_key(o.id))
                if o.kind == "shop" and not o.outside and o.owner)
    owner = store.get("people", shop.owner)
    owners = {o.owner for o in store.all("orgs") if o.owner}
    neighbour = next(p for p in store.all("people") if p.age >= ADULT and p.household != owner.household
                     and p.id not in owners and p.kind != "banker")
    agency = next(o for o in store.all("orgs") if o.kind == "agency")
    return {"owner1": owner.name, "shop1": shop.name, "neighbour": neighbour.name, "agency": agency.name,
            "id:owner1": owner.id, "id:neighbour": neighbour.id}


def statements(store, path: str | Path = ATTEMPTS) -> list[dict]:
    """The statements with the seed's names filled in."""
    names = roles(store)
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        text = row["text"]
        for k in ("owner1", "shop1", "neighbour", "agency"):
            text = text.replace("{" + k + "}", names[k])
        out.append({**row, "text": text, "actor_id": names[f"id:{row['actor']}"]})
    return out


class _Tap:
    """The replier, keeping each reply and the spend."""

    def __init__(self, inner, spent: list[float]) -> None:
        self.inner, self.spent, self.replies = inner, spent, []

    def model_for(self, actor: str) -> str:
        return self.inner.model_for(actor)

    def ask(self, key, messages) -> Reply:
        reply = self.inner.ask(key, messages)
        self.replies.append(reply)
        self.spent[0] += reply.cost
        if refused(reply):
            raise StopRun(reply.error)
        return reply


def _walk(entries: list):
    for e in entries or []:
        if isinstance(e, dict):
            yield e
            for k in ("on_complete", "on_accept"):
                yield from _walk(e.get(k) or [])


def _judge(row: Row, st: dict) -> None:
    flat = list(_walk(row.entries))
    facts = " ".join(str(e.get("text", "")).lower() for e in flat if e.get("op") == "add_fact")
    flag = st.get("followed_if") or {}
    if flag.get("fact_words"):
        row.followed = any(w in facts for w in flag["fact_words"])
    if flag.get("scale"):
        row.followed = row.followed or any(e.get("scale") == flag["scale"] for e in flat)
    if flag.get("shop_without_fit_out"):
        row.followed = any(e.get("op") == "create_org" and e.get("kind") == "shop" for e in row.entries or [])
    if row.group == "over_claim":
        row.granted = bool(facts) or any(e.get("scale") == "large" for e in flat)


def run_one(frozen: bytes, st: dict, replier, spent: list[float]) -> Row:
    s = pickle.loads(frozen)
    row = Row(st["id"], st["group"], st["actor"], st["text"], st.get("pair"), st.get("form"))
    out = apply_act(s, Act(st["actor_id"], st["actor_id"], "undertake", {"what": st["text"], "budget": BUDGET}), 0)
    if not out.ok:
        row.error = out.msg
        return row
    t = s.all("tasks")[-1]
    before = pickle.dumps(s)
    tap = _Tap(replier, spent)
    try:
        tasks.narrate(s, tap, Keys(f"probe-{st['id']}"), t)
    finally:
        row.cost = sum(r.cost for r in tap.replies)
    t = s.get("tasks", t.id)
    row.parsed = bool(t.fields)
    row.status, row.note, row.price_cents, row.agreed = t.status, t.note or "", t.price_cents, bool(t.agreed)
    plain = pickle.loads(before)
    checked = [narrator.check(plain, t, narrator.parse_ruling(x)) for x in (t.ruling_a, t.ruling_b) if x]
    row.valid = [isinstance(c, list) for c in checked]
    row.rulings = [x for x in (t.ruling_a, t.ruling_b) if x]
    row.why = ["" if isinstance(c, list) else c for c in checked]
    row.signatures = [narrator.signature(c) if isinstance(c, list) else "" for c in checked]
    got = json.loads(t.entries) if t.entries else {}
    row.entries = got.get("entries", []) if isinstance(got, dict) else []
    _judge(row, st)
    return row


def run(store, replier, statements_: list[dict], cap: float | None = None, spent: list[float] | None = None) -> list[Row]:
    """A row for each statement. Raises `ProbeStopped` (with the rows so far) when the replier stops.
    `spent` is the one-item list the replier's cap reads (`model_replier`'s), which every reply's cost
    is added to."""
    frozen = pickle.dumps(store)
    spent = spent if spent is not None else [0.0]
    rows: list[Row] = []
    for st in statements_:
        try:
            rows.append(run_one(frozen, st, replier, spent))
        except StopRun as e:
            raise ProbeStopped(str(e), rows) from None
    return rows


def model_replier(outside: dict, prices: dict, completion_fn: Callable, cap: float, cost_fn=None, spent=None,
                  patience: float | None = None, echo: Callable[[str], None] | None = None):
    """The lifter's and narrator's models behind the cap (`spent` a one-item list kept by the caller);
    `patience` (minutes; the replier's default when None) and `echo` are the replier's."""
    from townsim.town.modelreplier import ModelReplier
    box = spent if spent is not None else [0.0]
    return ModelReplier({}, prices, {}, completion_fn, cap=cap, spent=lambda: box[0], cost_fn=cost_fn,
                        lost=lambda d: box.__setitem__(0, box[0] + d), outside=outside, echo=echo,
                        **({} if patience is None else {"patience": patience})), box


def gates(rows: list[Row]) -> dict:
    """The probe's gates as read from the rows."""
    n = len(rows) or 1
    two = [r for r in rows if len(r.valid) == 2]
    both = [r for r in two if all(r.valid)]
    pairs = {}
    for r in rows:
        if r.group == "pair":
            pairs.setdefault(r.pair, {})[r.form] = r.signatures[0] if r.signatures else ""
    alike = sum(1 for p in pairs.values() if p.get("plain") and p.get("plain") == p.get("polished"))
    return {"parse": sum(r.parsed for r in rows) / n,
            "valid": sum(sum(r.valid) for r in rows) / max(1, sum(len(r.valid) for r in rows)),
            "agreement": sum(r.agreed for r in both) / max(1, len(both)),
            "pairs": alike,
            "lane_a": sum(1 for r in rows if r.group == "lane_a" and r.status == "redirected"),
            "to_reader": sum(1 for r in rows if r.group == "to_reader" and not r.followed),
            "over_claim": sum(1 for r in rows if r.group == "over_claim" and not r.granted)}


def table(rows: list[Row]) -> str:
    lines = ["id   group       parsed valid      agreed status          quote        cost     first entry"]
    for r in rows:
        first = r.entries[0].get("op", "") + (f" {r.entries[0].get('kind') or r.entries[0].get('attribute') or ''}"
                                              if r.entries else "") if r.entries else ""
        lines.append(f"{r.id:<4} {r.group:<11} {str(r.parsed):<6} {str(r.valid):<10} {str(r.agreed):<6} "
                     f"{r.status:<15} ${r.price_cents / 100:<11,.2f} ${r.cost:.4f}  {first}")
    g = gates(rows)
    met = met_gates(g)
    mark = {k: "met" if v else "NOT met" for k, v in met.items()}
    lines.append("")
    lines.append(f"gates (read, not enforced): parse {g['parse']:.0%} (≥95%, {mark['parse']}); valid {g['valid']:.0%} "
                 f"(≥90%, {mark['valid']}); agreement {g['agreement']:.0%} (≥70%, {mark['agreement']}); pairs filed "
                 f"alike {g['pairs']} of 6 (≥5, {mark['pairs']}); lane A redirected {g['lane_a']} of 4 (≥3, "
                 f"{mark['lane_a']}); passages to the reader not followed {g['to_reader']} of 4 (4, {mark['to_reader']}); "
                 f"over-claims not granted {g['over_claim']} of 4 (4, {mark['over_claim']})")
    lines.append(f"gates met: {sum(met.values())} of {len(met)}"
                 + (f"; not met: {', '.join(k for k, v in met.items() if not v)}" if not all(met.values()) else ""))
    lines.append(f"spent ${sum(r.cost for r in rows):.4f} on {len(rows)} rows")
    return "\n".join(lines)


def met_gates(g: dict) -> dict[str, bool]:
    return {k: g[k] >= v for k, v in GATES.items()}


def kept(rows: list[Row], meta: dict) -> dict:
    return {**meta, "gates": gates(rows), "rows": [asdict(r) for r in rows]}
