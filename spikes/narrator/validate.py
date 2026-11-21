"""Checks every narrator operation against the fixture world and the actor's commitments.

Costs come from fixed tables by kind and scale (the narrator never sees or sets them).
An operation is invalid if its ids do not exist, the actor does not control its target,
an enum is wrong, it spends beyond what was committed or held, a fact holds a number or
an unknown proper name, or it refers to something no operation created.
"""

import json
import re

from prompts import LANE_A, PROJECT_KINDS, PROJECT_KINDS_V2

SCALES = ["small", "medium", "large"]
ORG_KINDS = ["firm", "service", "club"]
ORG_ATTRS = ["quality", "capacity", "reputation", "visibility"]
REJECT_REASONS = ["impossible", "not_permitted", "too_vague", "insufficient_means"]

# Set-up cost in cents by scale, read by code; durations would come from a parallel table.
CREATE_ORG_COST = {"small": 10000, "medium": 200000, "large": 1500000}
PROJECT_COST = {
    "fit_out": (30000, 200000, 1500000),
    "build": (40000, 250000, 2000000),
    "repair": (15000, 100000, 800000),
    "training": (5000, 60000, 400000),
    "campaign": (3000, 40000, 250000),
    "petition": (0, 3000, 30000),
    "event": (5000, 50000, 400000),
    "survey": (0, 5000, 50000),
}

# v2: costs are the lower edge of each scale band (small < $1,000 <= medium < $10,000 <= large);
# work that is mostly time (campaign, petition, event, survey) costs nothing when small or medium.
CREATE_ORG_COST_V2 = {"small": 10000, "medium": 100000, "large": 1000000}
PROJECT_COST_V2 = {
    "fit_out": (20000, 100000, 1000000),
    "build": (20000, 100000, 1000000),
    "repair": (15000, 100000, 1000000),
    "campaign": (0, 0, 100000),
    "petition": (0, 0, 100000),
    "event": (0, 0, 100000),
    "survey": (0, 0, 100000),
}

NUMBER_RE = re.compile(
    r"\d|\$|%|\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|"
    r"hundred|thousand|million|dozen|percent|double[sd]?|triple[sd]?|twice|half)\b",
    re.I,
)
RESULT_RE = re.compile(
    r"\b(will|would|should)\s+(now\s+)?(increase|double|boost|grow|attract|bring|win|succeed|improve|raise|draw|lead|help|make)\b"
    r"|\b(successful(ly)?|guarantee[sd]?|popular|thriving|boosted|increased|more customers|sales)\b",
    re.I,
)
DRAMA_RE = re.compile(
    r"\b(unfortunately|luckily|fortunately|suddenly|sadly|thrilled|disaster|miracul\w*|amazing|"
    r"surpris\w*|delight\w*|bustling|vibrant|beloved|joy\w*|alas|setback|eager\w*|excit\w*)\b",
    re.I,
)
COMMON_CAPS = {
    "I", "The", "A", "An", "It", "Its", "They", "He", "She", "His", "Her", "Their", "This", "That",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Saturdays", "Sundays",
    "English", "West", "African", "St", "Mary's", "Market", "Street", "Road", "Harbour", "Church", "Mill",
    "Lane", "Town", "Council", "Bank", "Savings", "Fixit", "Services", "Corner", "Grocer", "Bakery",
}


def known_name_tokens(world: dict, extra_names: list[str]) -> set[str]:
    toks = set(COMMON_CAPS)
    names = [p["name"] for p in world["people"].values()] + [o["name"] for o in world["orgs"].values()]
    for n in names + extra_names:
        toks.update(re.findall(r"[A-Za-z']+", n))
    return toks


def unknown_proper_names(text: str, allowed: set[str]) -> list[str]:
    out = []
    for sentence in re.split(r"(?<=[.!?;:])\s+", text):
        words = re.findall(r"[A-Za-z][A-Za-z']*", sentence)
        for w in words[1:]:  # first word of a sentence may be capitalised
            base = w[:-2] if w.endswith("'s") else w.rstrip("'")
            if w[0].isupper() and w not in allowed and base not in allowed:
                out.append(w)
    return out


class Ctx:
    def __init__(self, world, actor_id, fields, version=1):
        self.version = version
        self.kinds = PROJECT_KINDS if version == 1 else PROJECT_KINDS_V2  # v3 = v2 here
        self.org_cost = CREATE_ORG_COST if version == 1 else CREATE_ORG_COST_V2
        self.project_cost = PROJECT_COST if version == 1 else PROJECT_COST_V2
        self.invests = []
        self.world = world
        self.actor = actor_id
        commits = (fields or {}).get("commits") or {}
        self.money_committed = _num(commits.get("money_cents"))
        self.hours_committed = _num(commits.get("hours_per_week")) * _num(commits.get("weeks"))
        p = world["people"][actor_id]
        self.controls = set(p["controls"]) | {actor_id}
        self.available = p["cash_cents"] + sum(world["orgs"][c]["cash_cents"] for c in p["controls"])
        self.spent = 0
        self.hours_used = 0
        self.refs = {}  # ref -> name
        self.results = []  # (path, op, errors)

    def exists(self, i):
        return i in self.world["people"] or i in self.world["orgs"] or i in self.refs or (
            self.version >= 2 and i in self.world.get("places", {})) or (
            self.version >= 3 and i in self.world.get("rules", {}))


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


def _spend(ctx, cents, errors):
    ctx.spent += cents
    limit = min(ctx.money_committed, ctx.available)
    if ctx.spent > limit:
        errors.append(f"spending reaches ${ctx.spent/100:,.0f}, beyond what was committed and held (${limit/100:,.0f})")


def check_op(ctx: Ctx, op, path: str, alone_ok: bool):
    errors = []
    if not isinstance(op, dict) or "op" not in op:
        ctx.results.append((path, op, ["not an operation object"]))
        return
    kind = op["op"]
    if kind == "create_org":
        if op.get("kind") not in ORG_KINDS:
            errors.append(f"bad kind {op.get('kind')!r}")
        if op.get("need") not in ctx.world["needs"]:
            errors.append(f"need {op.get('need')!r} not in the need list")
        if op.get("scale") not in SCALES:
            errors.append(f"bad scale {op.get('scale')!r}")
        name = op.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("no name")
        elif any(o["name"].lower() == name.lower() for o in ctx.world["orgs"].values()):
            errors.append("name of an existing organisation")
        if not op.get("offering"):
            errors.append("no offering")
        ref = op.get("ref")
        if ref:
            if ctx.exists(ref):
                errors.append(f"ref {ref!r} already used")
            else:
                ctx.refs[ref] = name or ""
                ctx.controls.add(ref)
        if op.get("scale") in SCALES:
            _spend(ctx, ctx.org_cost[op["scale"]], errors)
    elif kind == "invest":
        t, attr = op.get("target_id"), op.get("attribute")
        if not ctx.exists(t):
            errors.append(f"target {t!r} does not exist")
        elif t not in ctx.controls:
            errors.append(f"actor does not control {t!r}")
        if attr == "skill":
            if t != ctx.actor:
                errors.append("skill may only be invested in the actor themselves")
        elif attr in ORG_ATTRS:
            if t == ctx.actor or t in ctx.world["people"]:
                errors.append(f"{attr} is for organisations, not people")
        else:
            errors.append(f"bad attribute {attr!r}")
        m, h = op.get("money_cents", 0), op.get("hours", 0)
        if ctx.version >= 2:
            ctx.invests.append((op, errors))  # amounts are allocated by code at the end
        elif not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (m, h)):
            errors.append("money_cents and hours must be whole numbers >= 0")
        else:
            _spend(ctx, m, errors)
            ctx.hours_used += h
            if ctx.hours_used > ctx.hours_committed:
                errors.append(f"hours reach {ctx.hours_used}, beyond the {ctx.hours_committed:g} committed")
            if m == 0 and h == 0:
                errors.append("invests nothing")
    elif kind == "project":
        t = op.get("target_id")
        if op.get("kind") not in ctx.kinds:
            errors.append(f"bad project kind {op.get('kind')!r}")
        if op.get("scale") not in SCALES:
            errors.append(f"bad scale {op.get('scale')!r}")
        if not ctx.exists(t):
            errors.append(f"target {t!r} does not exist")
        elif t not in ctx.controls:
            errors.append(f"actor does not control {t!r}")
        if op.get("kind") in ctx.project_cost and op.get("scale") in SCALES:
            _spend(ctx, ctx.project_cost[op["kind"]][SCALES.index(op["scale"])], errors)
        sub = op.get("on_complete", [])
        if not isinstance(sub, list):
            errors.append("on_complete is not a list")
            sub = []
    elif kind == "request":
        to = op.get("to_id")
        if not ctx.exists(to) or to in ctx.refs or to in ctx.world.get("places", {}) or to in ctx.world.get("rules", {}):
            errors.append(f"to_id {to!r} is not an existing person or organisation")
        elif to in ctx.controls:
            errors.append("request to oneself or to something one controls")
        text = op.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append("no text")
        sub = op.get("on_accept", [])
        if not isinstance(sub, list):
            errors.append("on_accept is not a list")
            sub = []
    elif kind == "add_fact":
        subs, text = op.get("subjects"), op.get("text")
        if not isinstance(subs, list) or not subs:
            errors.append("no subjects")
        else:
            for s in subs:
                if not ctx.exists(s):
                    errors.append(f"subject {s!r} does not exist")
        if not isinstance(text, str) or not text.strip():
            errors.append("no text")
        else:
            if NUMBER_RE.search(text):
                errors.append(f"fact contains a number: {NUMBER_RE.search(text).group(0)!r}")
            allowed = known_name_tokens(ctx.world, list(ctx.refs.values()))
            unk = unknown_proper_names(text, allowed)
            if unk:
                errors.append(f"fact names unknown entities: {unk}")
    elif kind == "redirect":
        if op.get("operation") not in LANE_A:
            errors.append(f"unknown direct operation {op.get('operation')!r}")
        if not alone_ok:
            errors.append("redirect must be the only operation")
    elif kind == "reject":
        if op.get("reason") not in REJECT_REASONS:
            errors.append(f"bad reason {op.get('reason')!r}")
        if not alone_ok:
            errors.append("reject must be the only operation")
    else:
        errors.append(f"unknown operation {kind!r}")
    ctx.results.append((path, op, errors))
    granted = set()
    if kind == "request" and ctx.version >= 3:  # what the answerer controls or owns, once they agree
        to = op.get("to_id")
        w = ctx.world
        granted = {to} if to in w["orgs"] else set()
        if to in w["people"]:
            granted |= set(w["people"][to]["controls"])
        granted |= {pid for pid, pl in w.get("places", {}).items() if pl["owner"] in granted | {to}}
        granted -= ctx.controls
        ctx.controls |= granted
    if kind in ("project", "request"):
        for j, s in enumerate(sub):
            check_op(ctx, s, f"{path}.{'on_complete' if kind == 'project' else 'on_accept'}[{j}]", False)
    ctx.controls -= granted


def parse_reply(text: str | None):
    """The first JSON object with an "operations" list, or None."""
    if not text:
        return None
    dec = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = dec.raw_decode(text, start)
            if isinstance(obj, dict) and isinstance(obj.get("operations"), list) and isinstance(obj.get("ruling"), str):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def validate(world: dict, actor_id: str, fields: dict, reply: dict, version: int = 1) -> dict:
    ctx = Ctx(world, actor_id, fields, version)
    ops = reply["operations"]
    for i, op in enumerate(ops):
        check_op(ctx, op, f"[{i}]", len(ops) == 1)
    if ctx.invests:  # v2: what the other entries leave of the commitment is split across invests
        left = max(0, min(ctx.money_committed, ctx.available) - ctx.spent)
        for op, errors in ctx.invests:
            if left == 0 and ctx.hours_committed == 0:
                errors.append("nothing committed is left to invest")
        alloc = {"money_cents_each": left // len(ctx.invests), "hours_each": ctx.hours_committed / len(ctx.invests)}
    else:
        alloc = None
    ruling = reply["ruling"]
    allowed = known_name_tokens(world, list(ctx.refs.values()))
    flags = {
        "number": [m.group(0) for m in NUMBER_RE.finditer(ruling)],
        "result": [m.group(0) for m in RESULT_RE.finditer(ruling)],
        "drama": [m.group(0) for m in DRAMA_RE.finditer(ruling)],
        "unknown_names": unknown_proper_names(ruling, allowed),
    }
    return {
        "ops": [{"path": p, "op": o, "errors": e, "valid": not e} for p, o, e in ctx.results],
        "spent_cents": ctx.spent,
        "invest_allocation": alloc,
        "money_committed_cents": ctx.money_committed,
        "hours_committed": ctx.hours_committed,
        "ruling_flags": {k: v for k, v in flags.items() if v},
    }
