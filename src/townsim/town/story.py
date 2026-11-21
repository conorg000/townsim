"""A kept run read back as a plain account in Markdown (`account`): the run, week by week, counts
by family, things to look at, and balances. No model is called; everything comes from what the
run's `RunDB` holds.

Whatever a townsperson wrote (thoughts, letters, requests, notes, talk, the words of an act)
is untrusted: it appears only on `>` lines, with every character that could start a heading,
a table, a link, emphasis or HTML escaped, or inside inline code, without backticks; and it is
cut to a stated length. What the engine told them is shown inside inline code too, since it
may quote their words."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from townsim.town import aims as A
from townsim.town import talk as T
from townsim.town.acts import clean_text, shown
from townsim.town.clock import date_text
from townsim.town.loop import op_written, parse_reply
from townsim.town.obligations import SCHEDULED, money
from townsim.town.ops_core import NOT_COUNTED  # noqa: F401  (what the payer is told; the list reads the change log)
from townsim.town.replies import Reply, refused
from townsim.town.rundb import RunDB
from townsim.town.store import Store, id_key
from townsim.town.unknown import NOT_KNOWN

THOUGHT_WORDS = 60      # what someone wrote to themselves, at most
LETTER_WORDS = 400      # a letter, request, note or other words of an act (the engine allows 150 at most)
SAID_WORDS = 150        # a line said in a talk (the engine keeps 120 at most)
LONGEST_WORD = 60
REASON_CHARS = 300      # why the engine did not do an act
TOLD_CHARS = 160        # what the engine told them of an act carried out
ARGS_CHARS = 120        # the rest of an act, as written
TEXT_CHARS = 120        # a memo, an aim or a name, inside code
TOP_REFUSALS = 5
ERRORS_SHOWN = 10

UNREADABLE = "Their reply could not be read, so nothing in it was done."
REFUSED = "The provider refused to answer."
QUOTED_ARGS = ("text", "note", "opening", "about", "purpose")
QUOTED_PAST = 8         # words past which any other written value is quoted rather than shown inline

KIND_WORDS = {"standing": "standing payment", "agreement": "agreement", "rent": "rent", "wage": "wage",
              "loan": "loan repayment", "bill": "bill", "fee": "fee"}


# --- untrusted text ----------------------------------------------------------------------------

_ESCAPED = set("\\`*_[]<>#|~")


def _escape(line: str) -> str:
    """One line of someone's words with nothing in it able to start Markdown structure."""
    out = "".join("\\" + c if c in _ESCAPED else c for c in line)
    m = re.match(r"(\d+)([.)])", out)
    if m:
        out = m.group(1) + "\\" + out[len(m.group(1)):]
    elif out[:1] in "-+=":
        out = "\\" + out
    return out


def quote(value: Any, words: int, indent: str = "") -> list[str]:
    """Someone's words as `>` lines (begun with `indent`), escaped, every word over 60
    characters cut, and the whole cut to `words` words with a note saying so. Empty for no
    words."""
    text = (clean_text(value if isinstance(value, str) else shown(value)) or "").replace("\t", " ")
    lines: list[list[str]] = []
    left, cut = words, False
    for raw in text.split("\n"):
        ws = [w if len(w) <= LONGEST_WORD else w[:LONGEST_WORD] + "…" for w in raw.split()]
        if len(ws) > left:
            ws, cut = ws[:left], True
        left -= len(ws)
        lines.append(ws)
        if cut:
            break
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    out: list[str] = []
    for ws in lines:
        if not ws:
            if out and out[-1] != indent + ">":
                out.append(indent + ">")
            continue
        out.append(indent + "> " + _escape(" ".join(ws)))
    if cut and out:
        out[-1] += f" … (cut at {words} words)"
    return out


def code(value: Any, n: int = TEXT_CHARS, cell: bool = False) -> str:
    """`value` as inline code on one line: cleaned, backticks made `'`, cut to `n` characters
    with `…`; in a table cell, `|` made `¦` too."""
    text = value if isinstance(value, str) else shown(value)
    text = " ".join((clean_text(text) or "").split()).replace("`", "'")
    if cell:
        text = text.replace("|", "¦")
    if len(text) > n:
        text = text[:n] + "…"
    return f"`{text}`" if text else "(nothing)"


# --- the run as stored -------------------------------------------------------------------------


def _key_parts(key: str) -> tuple[str, int, str, str]:
    """(model, day, actor, purpose) of a full reply key."""
    parts = key.split("|")
    return parts[1], int(parts[2][1:]), parts[3], parts[4].split(":")[0]


def _op(cause: str) -> str:
    """The operation an act's cause names (`op:<actor>:<op>:d<day>.<n>`), or ""."""
    parts = cause.split(":")
    return parts[2] if len(parts) > 3 and parts[0] == "op" else ""


def _pct(n: int, of: int) -> str:
    return f"{n} ({100 * n / of:.1f}%)" if of else f"{n}"


class _Run:
    def __init__(self, db: RunDB, run_id: str) -> None:
        self.db, self.id = db, run_id
        self.seed = db.get_run(run_id)["seed"]
        self.snap_days = db.snapshot_days(run_id)
        self.turn_days = db.turn_days(run_id)
        self._snaps: dict[int, Store] = {}
        self.replies: dict[str, Reply] = dict(db.replies(run_id))
        self.turns = {d: db.turns(run_id, d) for d in self.turn_days}
        kept = set(db.change_days(run_id))
        self.changes = {d: db.changes(run_id, d) if d in kept else [] for d in self.turn_days}
        first = self.snap(self.snap_days[0]) if self.snap_days else Store()
        self.family = {p.id: p.family for p in first.all("people")}
        try:
            noted = json.loads(self.db.note(run_id, "families") or "{}")
        except ValueError:
            noted = {}
        self.models: dict[str, str] = {}
        for name, value in noted.items() if isinstance(noted, dict) else ():
            self.models[str(name)] = str(value.get("model", "")) if isinstance(value, dict) else str(value)
        for key in self.replies:
            model, _, actor, _ = _key_parts(key)
            self.models.setdefault(self.family.get(actor, "") or "?", model)
        self.families = sorted(set(self.models) | {f or "?" for f in self.family.values()})

    def snap(self, day: int) -> Store:
        if day not in self._snaps:
            self._snaps[day] = self.db.load_snapshot(self.id, day)
        return self._snaps[day]

    def morning(self, day: int) -> Store:
        return self.snap(day) if day in self.snap_days else self.after(day)

    def after(self, day: int) -> Store:
        """The state when `day` was done: the next morning's snapshot, or the latest."""
        later = [d for d in self.snap_days if d > day]
        return self.snap(later[0] if later else self.snap_days[-1])

    def fam(self, pid: str) -> str:
        return self.family.get(pid, "") or "?"


def name(s: Store, who: str) -> str:
    """Who an id is, by the state given: a person's or business's name, `the <name> household`."""
    if s.has("people", who):
        return s.get("people", who).name
    if s.has("households", who):
        called = s.get("households", who).name
        return f"the {called}" if "household" in called.split() else f"the {called} household"
    if s.has("orgs", who):
        return s.get("orgs", who).name
    if s.has("suppliers", who):
        return name(s, s.get("suppliers", who).org)
    return who


# --- 1. the run --------------------------------------------------------------------------------


def _the_run(r: _Run) -> list[str]:
    out = ["## The run", ""]
    out.append(f"- Seed: {r.seed}")
    if r.turn_days:
        first, last = r.turn_days[0], r.turn_days[-1]
        out.append(f"- {len(r.turn_days)} days kept, {date_text(first)} to {date_text(last)} "
                   f"({len(r.turn_days) / 7:g} weeks).")
    else:
        out.append("- No days kept.")
    counts = Counter(r.fam(p) for p in r.family)
    spend: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for key, reply in r.replies.items():
        f = r.fam(_key_parts(key)[2])
        spend[f][0] += 1
        spend[f][1] += reply.cost
    out.append("- Families:")
    for f in r.families:
        out.append(f"  - {f}: {code(r.models.get(f, '') or 'unknown', 80)}, plays {counts.get(f, 0)} people; "
                   f"{spend[f][0]} replies, ${spend[f][1]:.4f}")
    stored = r.db.spent(r.id)
    try:
        lost = float(r.db.note(r.id, "lost_spend") or 0)
    except ValueError:
        lost = 0.0
    out.append(f"- Stored spend: ${stored:.4f}")
    out.append(f"- Lost to failed attempts: ${lost:.4f}")
    cap = r.db.note(r.id, "cap")
    if cap is not None:
        out.append(f"- Spending cap: {code(cap, 20)} dollars")
    errors = r.db.errors(r.id)
    out.append(f"- Engine errors: {len(errors)}")
    for day, text in errors[:ERRORS_SHOWN]:
        out.append(f"  - {date_text(day)}: {code(text, REASON_CHARS)}")
    if len(errors) > ERRORS_SHOWN:
        out.append(f"  - and {len(errors) - ERRORS_SHOWN} more")
    out.append(f"- Why it stopped: {_why_stopped(r, cap)}")
    return out


def _why_stopped(r: _Run, cap: str | None) -> str:
    if not r.snap_days:
        return "nothing was kept."
    last = r.snap_days[-1]
    pending = [(k, rep) for k, rep in r.replies.items() if _key_parts(k)[1] == last and last not in r.turns]
    refusal = next(((k, rep) for k, rep in pending if refused(rep)), None)
    if refusal is not None:
        model, _, actor, _ = _key_parts(refusal[0])
        return (f"the provider refused a prompt on {date_text(last)}, asked for "
                f"{name(r.snap(last), actor)} of family {r.fam(actor)} ({code(model, 80)}); that day was not kept.")
    if pending:
        return (f"it stopped part way through {date_text(last)}, after {len(pending)} replies of that day had come "
                f"back; that day was not kept. Why is not kept with the run: a spending cap that the next asks "
                f"would pass, or a provider that could not be reached, are the usual reasons.")
    if not r.turn_days:
        return "it stopped before its first day was done."
    done = f"it stopped at the end of {date_text(r.turn_days[-1])}, with {len(r.turn_days)} days done"
    deck = r.db.note(r.id, "deck_weeks")
    if cap is None:
        return done + "."
    if deck is not None and deck.isdigit() and len(r.turn_days) >= 7 * int(deck):
        return done + ", all the weeks it was made for."
    by_day: Counter = Counter()
    for key, reply in r.replies.items():
        by_day[_key_parts(key)[1]] += reply.cost
    try:
        most, total = float(cap), r.db.spent(r.id) + float(r.db.note(r.id, "lost_spend") or 0)
    except ValueError:
        most, total = 0.0, 0.0
    dearest = max(by_day.values(), default=0.0)
    if most and total + 2 * dearest <= most:
        return done + (f". The cap was not near (${total:.4f} of ${most:.4f} spent, no day above "
                       f"${dearest:.4f}), so it most likely ran the weeks asked for; that number is not kept.")
    return done + (". Why is not kept with the run: either it had run the weeks asked for, or the next day's "
                   "asks would have passed the spending cap.")


# --- 2. week by week ---------------------------------------------------------------------------


def _split_args(args: Any) -> tuple[str, list[tuple[str, Any]]]:
    """An act's arguments as one inline line and the written values to quote beneath it."""
    if not isinstance(args, dict):
        return code(args, ARGS_CHARS) if args not in (None, {}) else "", []
    inline, quoted = [], []
    for k, v in args.items():
        if isinstance(v, str) and (k in QUOTED_ARGS or len(v.split()) > QUOTED_PAST):
            if v.strip():
                quoted.append((k, v))
        else:
            inline.append(f"{shown(k)}: {v if isinstance(v, str) else shown(v)}")
    return (code(", ".join(inline), ARGS_CHARS) if inline else ""), quoted


def act_lines(outcome) -> list[str]:
    """`- ✓` (carried out) or `- ✗` (not done), what was written, and what the engine said;
    then the written words quoted beneath."""
    mark = "✓" if outcome.ok else "✗"
    inline, quoted = _split_args(outcome.act.args)
    head = f"- {mark} {code(op_written(outcome.act.op), 40)}" + (f" {inline}" if inline else "")
    lines = [head + f": {code(outcome.msg, TOLD_CHARS if outcome.ok else REASON_CHARS)}"]
    for k, v in quoted:
        if len(quoted) > 1:
            lines.append(f"  Its {code(k, 20)}:")
        lines += quote(v, LETTER_WORDS, "  ")
    return lines


def _turn_block(r: _Run, turn) -> list[list[str]]:
    reply = r.replies.get(turn.key)
    if reply is not None and refused(reply):
        return [[f"- {REFUSED}"]]
    if turn.invalid:
        return [[f"- {UNREADABLE}"]]
    blocks = []
    parsed = parse_reply(reply.text) if reply is not None else None
    thoughts = parsed.get("thoughts") if parsed else None
    if isinstance(thoughts, str) and thoughts.strip():
        blocks.append(["Wrote to themselves:", ""] + quote(thoughts, THOUGHT_WORDS))
    acts = [line for o in turn.outcomes for line in act_lines(o)]
    if acts:
        blocks.append(acts)
    return blocks


def _talk_blocks(r: _Run, day: int, tk: str, turns: list) -> list[list[str]]:
    s = r.after(day)
    if not s.has("talks", tk):
        return [[f"**{date_text(day)}: a talk** ({tk}, not kept)"]]
    t = s.get("talks", tk)
    a, b = name(s, t.a), name(s, t.b)
    how = {"asked": f"{a} asked to talk", "appointment": f"booked by {a}", "household": "at home"}.get(t.kind, t.kind)
    blocks = [[f"**{date_text(day)}: {a} and {b}** ({how})"]]
    lines = T.lines_of(s, tk)
    if t.kind == "asked" and lines:
        opening, lines = lines[0], lines[1:]
        blocks.append([f"{name(s, opening.speaker)} opened:", ""] + (quote(opening.text, SAID_WORDS) or ["(nothing)"]))
    for turn in turns:
        who = name(s, turn.pid)
        reply = r.replies.get(turn.key)
        if reply is not None and refused(reply):
            blocks.append([f"- {who}: {REFUSED}"])
            continue
        if turn.invalid:
            blocks.append([f"- {who}: {UNREADABLE} The talk ended there."])
            continue
        line = lines.pop(0) if lines else None
        said = quote(line.text, SAID_WORDS) if line is not None else []
        blocks.append(([f"{who} said:", ""] + said) if said else [f"{who} said nothing."])
        acts = [x for o in turn.outcomes for x in act_lines(o)]
        if acts:
            blocks.append(acts)
    return blocks


def _week_by_week(r: _Run) -> list[str]:
    out = ["## Week by week"]
    last = r.turn_days[-1] if r.turn_days else 0
    for week in sorted({d // 7 for d in r.turn_days}):
        days = [d for d in r.turn_days if d // 7 == week]
        out += ["", f"### Week {week + 1}: {date_text(7 * week)} to {date_text(min(7 * week + 6, last))}"]
        mine: dict[str, list] = defaultdict(list)
        talks: dict[tuple[int, str], list] = {}
        for d in days:
            for turn in r.turns[d]:
                if turn.talk:
                    talks.setdefault((d, turn.talk), []).append(turn)
                else:
                    mine[turn.pid].append((d, turn))
        s = r.morning(days[0])
        idle = []
        for pid in sorted(mine, key=id_key):
            shown_days = [(d, _turn_block(r, turn)) for d, turn in mine[pid]]
            if not any(blocks for _, blocks in shown_days):
                idle.append(f"{name(s, pid)} (family {r.fam(pid)})")
                continue
            out += ["", f"#### {name(s, pid)} (family {r.fam(pid)})"]
            for d, blocks in shown_days:
                if not blocks:
                    out += ["", f"**{date_text(d)}**: asked, and did nothing."]
                    continue
                out += ["", f"**{date_text(d)}**"]
                for block in blocks:
                    out += [""] + block
        if idle:
            out += ["", "Asked this week and did nothing: " + "; ".join(idle) + "."]
        if talks:
            out += ["", "#### Talks"]
            for (d, tk), turns in talks.items():
                for block in _talk_blocks(r, d, tk, turns):
                    out += [""] + block
    if not r.turn_days:
        out += ["", "No days were kept."]
    return out


# --- 3. counts by family -----------------------------------------------------------------------


def _counts(r: _Run) -> list[str]:
    fams = r.families
    people = Counter(r.fam(p) for p in r.family)
    asks, refusals, tin, tout, cost = Counter(), Counter(), Counter(), Counter(), defaultdict(float)
    for key, reply in r.replies.items():
        f = r.fam(_key_parts(key)[2])
        asks[f] += 1
        refusals[f] += refused(reply)
        tin[f] += reply.tokens_in
        tout[f] += reply.tokens_out
        cost[f] += reply.cost
    unread, acts, bad = Counter(), Counter(), Counter()
    reasons: dict[str, Counter] = defaultdict(Counter)
    thought_words: dict[str, list[int]] = defaultdict(list)
    for d in r.turn_days:
        for turn in r.turns[d]:
            f = r.fam(turn.pid)
            reply = r.replies.get(turn.key)
            if turn.invalid and not (reply is not None and refused(reply)):
                unread[f] += 1
            for o in turn.outcomes:
                acts[f] += 1
                if not o.ok:
                    bad[f] += 1
                    reasons[f][o.msg] += 1
            if not turn.talk and not turn.invalid and reply is not None:
                parsed = parse_reply(reply.text)
                if parsed is not None and isinstance(parsed.get("thoughts"), str):
                    thought_words[f].append(len(parsed["thoughts"].split()))

    def row(label, model, fs):
        n = sum(asks[f] for f in fs)
        words = [w for f in fs for w in thought_words[f]]
        cells = [label, model, str(sum(people[f] for f in fs)), str(n), _pct(sum(unread[f] for f in fs), n),
                 _pct(sum(refusals[f] for f in fs), n), str(sum(acts[f] for f in fs)),
                 _pct(sum(bad[f] for f in fs), sum(acts[f] for f in fs)),
                 f"{sum(words) / len(words):.1f}" if words else "—",
                 f"{sum(tin[f] for f in fs) / n:,.0f}" if n else "—",
                 f"{sum(tout[f] for f in fs) / n:,.0f}" if n else "—",
                 f"${sum(cost[f] for f in fs):.4f}"]
        return "| " + " | ".join(cells) + " |"
    out = ["## Counts by family", "",
           "Asks are the replies asked for. Unreadable replies and the provider's refusals are counted apart, "
           "each as a share of asks; acts not done as a share of acts. Thought words are the mean words someone "
           "wrote to themselves on a turn of their own; tokens are the mean a reply took in and gave out.", "",
           "| Family | Model | People | Asks | Unreadable | Refusals | Acts | Not done | Thought words "
           "| Tokens in | Tokens out | Dollars |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for f in fams:
        out.append(row(f, code(r.models.get(f, "") or "unknown", 60, cell=True), [f]))
    out.append(row("All", "—", fams))
    out += ["", "### Commonest reasons acts were not done"]
    for f in fams:
        out += ["", f"Family {f}:", ""]
        top = reasons[f].most_common(TOP_REFUSALS)
        out += [f"- {k} × {code(msg, REASON_CHARS)}" for msg, k in top] or ["- None."]
    return out


# --- 4. things to look at ----------------------------------------------------------------------


def _named(s: Store, x: str) -> bool:
    """Whether `x` is someone or something `name` can name in `s`."""
    return any(s.has(t, x) for t in ("people", "households", "orgs", "suppliers"))


class _Day:
    """A kept day's acts carried out and its changes, by cause."""

    def __init__(self, r: _Run, d: int) -> None:
        self.d, self.date = d, date_text(d)
        self.before, self.after = r.morning(d), r.after(d)
        self.changes = r.changes[d]
        self.by_cause: dict[str, list] = defaultdict(list)
        for c in self.changes:
            self.by_cause[c.cause].append(c)
        self.done = [o for t in r.turns[d] for o in t.outcomes if o.ok]

    def who(self, x: str) -> str:
        return name(self.after, x) if _named(self.after, x) else name(self.before, x)

    def row(self, table: str, id: str):
        for s in (self.after, self.before):
            if s.has(table, id):
                return s.get(table, id)
        return None

    def request(self, outcome):
        rq = outcome.act.args.get("request") if isinstance(outcome.act.args, dict) else None
        return self.row("requests", rq) if isinstance(rq, str) else None


def _ob_words(day: _Day, ob) -> str:
    return (f"{ob.id}, {money(ob.cents)} {KIND_WORDS.get(ob.kind, ob.kind)} from {day.who(ob.payer)} "
            f"to {day.who(ob.payee)} ({code(ob.memo, 60)})")


def _stops(day: _Day) -> list[str]:
    out = []
    for o in day.done:
        if _op(o.cause) != "stop":
            continue
        actor = day.who(o.act.actor)
        status = [c for c in day.by_cause[o.cause] if c.table == "obligations" and c.field == "status"]
        if status:
            ob = day.row("obligations", status[0].id)
            out.append(f"{day.date}: {actor} stopped {_ob_words(day, ob)}; it is now {status[0].new}.")
        else:
            first = next((c for c in day.by_cause[o.cause] if c.table not in ("results", "counter")), None)
            what = f"{first.table.rstrip('s')} {first.id}" if first else "something"
            out.append(f"{day.date}: {actor} stopped {what}: {code(o.msg, TOLD_CHARS)}")
    return out


def _toward_nothing(day: _Day) -> list[str]:
    out = []
    for o in day.done:
        if _op(o.cause) != "pay":
            continue
        mine = day.by_cause[o.cause]
        if any(c.table == "dues" for c in mine):
            continue
        for c in mine:
            if c.table == "ledger":
                t = c.new
                out.append(f"{day.date}: {day.who(o.act.actor)} paid {money(t.cents)} from {day.who(t.src)} "
                           f"to {day.who(t.dst)}; it counted toward nothing owed.")
    return out


def _standing_again(day: _Day) -> list[str]:
    out = []
    added_today: list = []
    for c in day.changes:
        if c.table != "obligations" or c.field != "+":
            continue
        ob = c.new
        if _op(c.cause) == "standing_payment":
            already = [x for x in day.before.all("obligations")
                       if x.payer == ob.payer and x.payee == ob.payee and x.status in SCHEDULED]
            already += [x for x in added_today if x.payer == ob.payer and x.payee == ob.payee]
            if already:
                had = "; ".join(f"{x.id} ({KIND_WORDS.get(x.kind, x.kind)}, {money(x.cents)})" for x in already)
                out.append(f"{day.date}: {day.who(c.cause.split(':')[1].split('/')[0])} set up {_ob_words(day, ob)}, "
                           f"who already had from them: {had}.")
        added_today.append(ob)
    return out


_WRITER_NAMES = re.compile(r"([^.]*) (?:is|are) " + re.escape(NOT_KNOWN))


def _unknown_names(day: _Day) -> list[str]:
    out = []
    for o in day.done:
        m = _WRITER_NAMES.search(o.msg)
        if m:
            names = [n for part in m.group(1).split(", ") for n in part.split(" and ") if n.strip()]
            out.append(f"{day.date}: {day.who(o.act.actor)} wrote of {', '.join(code(n, 40) for n in names)} "
                       f"({code(op_written(o.act.op), 40)}), no one they know.")
    for c in day.changes:
        if c.table == "messages" and c.field == "+" and getattr(c.new, "unknown", None):
            msg = c.new
            out.append(f"{day.date}: a letter from {day.who(msg.sender)} to {day.who(msg.to)} named "
                       f"{', '.join(code(n, 40) for n in msg.unknown)}, no one {day.who(msg.to)} knows.")
    return out


def _accepted_money(day: _Day) -> list[str]:
    out = []
    for o in day.done:
        if _op(o.cause) != "answer":
            continue
        mine = day.by_cause[o.cause]
        accepted = [c for c in mine if c.table == "requests" and c.field == "status" and c.new == "accepted"]
        moved = [c.new for c in mine if c.table == "ledger"]
        if accepted and moved:
            rq = day.row("requests", accepted[0].id)
            what = "; ".join(f"{money(t.cents)} from {day.who(t.src)} to {day.who(t.dst)}" for t in moved)
            out.append(f"{day.date}: {day.who(o.act.actor)} accepted {rq.id} from {day.who(rq.sender)}: {what}.")
    return out


OWNER_OPS = ("set_price", "set_staff_pay", "order_stock", "offer_shifts")
_MONEY_FIELDS = ("price", "weekly", "cents", "next_cents", "order_cents", "pay")
_QUIET = ("wage_ob",)       # ids that a change carries along


def _value(day: _Day, field: str, v: Any) -> str:
    if field in _MONEY_FIELDS and isinstance(v, int):
        return money(v)
    if field in ("next_from", "ended", "since", "first", "last") and isinstance(v, int):
        return date_text(v) if v >= 0 else "none"
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return code(v, 40)
    if isinstance(v, (int, float)):
        return f"{v:g}"
    if not v:
        return "none"
    return day.who(v) if _named(day.after, v) else code(v, 40)


def _diffs(day: _Day, cause: str, tables: tuple[str, ...]) -> list[str]:
    out = []
    for c in day.by_cause[cause]:
        if c.table not in tables or c.field in ("+",) + _QUIET or c.old == c.new:
            continue
        row = day.row(c.table, c.id)
        if c.table == "firms" and row is not None:
            label = f"{day.who(row.org)}'s {c.field.replace('_', ' ')}"
        elif c.table == "jobs" and row is not None:
            label = f"{day.who(row.person)}'s job at {day.who(row.org)}, {c.field}"
        elif c.table == "obligations" and row is not None:
            label = f"the {KIND_WORDS.get(row.kind, row.kind)} {c.id}, {c.field.replace('_', ' ')}"
        else:
            label = f"{c.id} {c.field.replace('_', ' ')}"
        out.append(f"{label} {_value(day, c.field, c.old)} → {_value(day, c.field, c.new)}")
    return out


def _order(day: _Day, cause: str) -> list[str]:
    """A standing order placed, as the shop's row now has it, beside what it was."""
    out = []
    for fid in dict.fromkeys(c.id for c in day.by_cause[cause] if c.table == "firms"):
        f = {c.field: c for c in day.by_cause[cause] if c.table == "firms" and c.id == fid}
        row = day.row("firms", fid)

        def now(field):
            return f[field].new if field in f else getattr(row, field, None)

        def was(field):
            return f[field].old if field in f else getattr(row, field, None)
        units, supplier = now("order_units"), now("order_supplier")
        said = (f"{day.who(row.org)} is to have {units} baskets from {day.who(supplier)} at "
                f"{money(now('order_cents') or 0)} a basket" if units else f"{day.who(row.org)} is to have no order")
        if was("order_units"):
            said += f" (it had {was('order_units')} from {day.who(was('order_supplier'))})"
        out.append(said)
    return out


def _owners(day: _Day) -> list[str]:
    out = []
    for o in day.done:
        op = _op(o.cause)
        if op == "order_stock":
            diffs = _order(day, o.cause)
        elif op in OWNER_OPS:
            diffs = _diffs(day, o.cause, ("firms", "jobs", "obligations"))
        if op in OWNER_OPS:
            what = "; ".join(diffs) if diffs else code(o.msg, REASON_CHARS)
            out.append(f"{day.date}: {day.who(o.act.actor)} ({op}): {what}")
    return out


def _lapsed(day: _Day, kind: str) -> list[str]:
    out = []
    for c in day.changes:
        if c.table == "requests" and c.field == "status" and c.new == "lapsed":
            rq = day.row("requests", c.id)
            if rq is not None and rq.kind == kind:
                out.append(f"{day.date}: {rq.id}, from {day.who(rq.sender)} to {day.who(rq.to)}, lapsed unanswered.")
    return out


def _jobs(day: _Day) -> list[str]:
    out = []
    for o in day.done:
        op, actor = _op(o.cause), day.who(o.act.actor)
        mine = day.by_cause[o.cause]
        if op == "offer_job":
            for c in mine:
                if c.table == "requests" and c.field == "+":
                    rq = c.new
                    out.append(f"{day.date}: {actor} offered {day.who(rq.to)} a job at {day.who(rq.org)} ({rq.id}), "
                               f"{money(rq.pay)} a week for {rq.hours:g} hours from {date_text(rq.first)}.")
        elif op == "answer":
            rq = day.request(o)
            if rq is not None and rq.kind == "job":
                said = "accepted" if any(c.table == "jobs" and c.field == "+" for c in mine) else "declined"
                out.append(f"{day.date}: {actor} {said} the job at {day.who(rq.org)} offered by "
                           f"{day.who(rq.sender)} ({rq.id}).")
        elif op in ("quit", "dismiss"):
            for c in mine:
                if c.table == "jobs" and c.field == "ended":
                    job = day.row("jobs", c.id)
                    if op == "quit":
                        out.append(f"{day.date}: {actor} quit their job at {day.who(job.org)}; last day worked "
                                   f"{date_text(c.new)}.")
                    else:
                        out.append(f"{day.date}: {actor} dismissed {day.who(job.person)} from {day.who(job.org)}; "
                                   f"last day worked {date_text(c.new)}.")
    return out + _lapsed(day, "job")


def _loans(day: _Day) -> list[str]:
    out = []
    for o in day.done:
        op, actor = _op(o.cause), day.who(o.act.actor)
        if op == "borrow":
            for c in day.by_cause[o.cause]:
                if c.table == "requests" and c.field == "+":
                    rq = c.new
                    out.append(f"{day.date}: {actor} asked {day.who(rq.payee)} for {money(rq.pay)} over {rq.times} "
                               f"weeks for {day.who(rq.payer)} ({rq.id}); the banker is {day.who(rq.to)}.")
        elif op == "answer":
            rq = day.request(o)
            if rq is not None and rq.kind == "loan":
                said = next((c.new for c in day.by_cause[o.cause] if c.table == "requests" and c.field == "status"),
                            "answered")
                out.append(f"{day.date}: the banker {actor} {said} {rq.id}, {day.who(rq.sender)}'s request for "
                           f"{money(rq.pay)}.")
    return out + _lapsed(day, "loan")


def _rents(day: _Day) -> list[str]:
    out = []
    for cause, mine in day.by_cause.items():
        by = (f"by {day.who(cause.split(':')[1].split('/')[0])} ({_op(cause)})" if cause.startswith("op:")
              else "by an event dealt with the place" if cause.startswith("routine:event")
              else "as was set before, by the day's routine")
        for ob_id in dict.fromkeys(c.id for c in mine if c.table == "obligations"):
            ob = day.row("obligations", ob_id)
            if getattr(ob, "kind", "") != "rent":
                continue
            fields = {c.field: c for c in mine if c.table == "obligations" and c.id == ob_id}
            said = []
            if "cents" in fields and fields["cents"].old != fields["cents"].new:
                said.append(f"is now {money(fields['cents'].new)} a week, was {money(fields['cents'].old)}")
            if "next_cents" in fields and fields["next_cents"].new and "next_from" in fields:
                said.append(f"is to be {money(fields['next_cents'].new)} a week from "
                            f"{date_text(fields['next_from'].new)}")
            if said:
                out.append(f"{day.date}: the rent {day.who(ob.payer)} pays {day.who(ob.payee)} ({ob.id}) "
                           f"{' and '.join(said)}; {by}.")
        for c in mine:
            if c.table == "records" and c.field == "+" and c.new.kind == "notice to quit":
                who = day.who(cause.split(":")[1].split("/")[0]) if cause.startswith("op:") else "its landlord"
                out.append(f"{day.date}: notice to quit was served on {day.who(c.new.about)} by {who}.")
    return out


def _aims(day: _Day) -> list[str]:
    out = []
    people = sorted({c.new.person if c.field == "+" else day.row("aims", c.id).person
                     for c in day.changes if c.table == "aims"}, key=id_key)
    for pid in people:
        was = [a.text for a in A.held(day.before, pid)]
        now = [a.text for a in A.held(day.after, pid)]
        added = [code(t) for t in now if t not in was]
        dropped = [code(t) for t in was if t not in now]
        said = ([f"added {', '.join(added)}"] if added else []) + ([f"dropped {', '.join(dropped)}"] if dropped else [])
        if said:
            out.append(f"{day.date}: {day.who(pid)} {'; '.join(said)}.")
    return out


LOOKS = (("Stops", _stops), ("Payments counted toward nothing", _toward_nothing),
         ("Standing payments to someone already paid", _standing_again),
         ("Names no one knows", _unknown_names), ("Requests accepted that moved money", _accepted_money),
         ("Owners' changes to prices, wages, orders and staff", _owners), ("Jobs", _jobs),
         ("Loans", _loans), ("Rents and notices", _rents), ("Aims", _aims))


def _looks(r: _Run) -> list[str]:
    found: dict[str, list[str]] = {title: [] for title, _ in LOOKS}
    for d in r.turn_days:
        day = _Day(r, d)
        for title, fn in LOOKS:
            found[title] += fn(day)
    out = ["## Things to look at", "", "Each line is read from the change log of the day."]
    for title, _ in LOOKS:
        out += ["", f"### {title}", ""]
        out += [f"- {line}" for line in found[title]] or ["None."]
    return out


# --- 5. balances -------------------------------------------------------------------------------


def _balances(r: _Run) -> list[str]:
    out = ["## Balances", ""]
    if not r.snap_days:
        return out + ["Nothing was kept."]
    first, last = r.snap(r.snap_days[0]), r.snap(r.snap_days[-1])
    out += [f"| Who | {date_text(r.snap_days[0])}, first morning | {date_text(r.snap_days[-1])}, last morning |",
            "|---|---:|---:|"]
    seen: list[tuple[str, str]] = []
    for s in (first, last):
        for h in s.all("households"):
            seen.append(("households", h.id))
        for o in s.all("orgs"):
            if not o.outside:
                seen.append(("orgs", o.id))
    rows = sorted(dict.fromkeys(seen), key=lambda x: (x[0] != "households", id_key(x[1])))

    def bal(s: Store, who: str) -> str:
        return money(s.balance(who)) if s.ledger.has(who) else "—"
    for table, who in rows:
        s = last if last.has(table, who) else first
        label = name(s, who) + ("" if table == "households" else f" ({s.get(table, who).kind})")
        out.append(f"| {label} | {bal(first, who)} | {bal(last, who)} |")
    return out


def account(db: RunDB, run_id: str) -> str:
    """The kept run `run_id` as a Markdown account in five parts: the run, week by week, counts
    by family, things to look at, balances. Raises KeyError for a run not in `db`."""
    r = _Run(db, run_id)
    parts = [[f"# Run {code(run_id, 60)}"], _the_run(r), _week_by_week(r), _counts(r), _looks(r), _balances(r)]
    return "\n\n".join("\n".join(p) for p in parts) + "\n"
