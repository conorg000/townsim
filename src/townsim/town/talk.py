"""Talking: conversations held the same day, appointments and the household's Sunday talk.

After everyone has acted, the day's talks are held (`todays`, then `hold` for each): today's
appointments by id, then the talks asked for with `converse` by id, then on a Sunday each
household's talk when something is open between its two adults with the lowest ids. A talk
is held only if both people have an hour left (the asker's was taken when they asked) and
neither has had two talks held today; it then takes an hour from each who has not yet given
one. `hold` returns what it asked (`Held`), and the day's running count of acts goes on
through it.

A talk is at most three turns a side. An asked talk's opening is the asker's first turn and
the other speaks next; any other talk begins with an ask of `a`. Each turn is one ask with key
purpose `talk` and ref the talk's id, shown `view`, and answered `{"say": ..., "acts": [...],
"end": true}`. What is said is cleaned and bounded (`said`) and kept as a `Line`; at most four
acts a turn are applied at once, in the day's running count, and what came of them is shown to
the speaker in their next turn's view (and, after their last turn, in their next ordinary
view). A reply that cannot be read ends the talk there.

Everything a speaker sees of what anyone said or wrote is set off from what code wrote: a
turn is `<Full name> said:` and then the words on one line begun `  > `; a letter is
`From <name> on <date>:` and its lines begun `  > `; a request as in the ordinary view.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from townsim.town import acts, facts, loop, unknown, views
from townsim.town.acts import LONGEST_WORD, Outcome, accounts_of, apply_act, clean_text, name_of_who
from townsim.town.clock import date_text, dow, week
from townsim.town.entities import Line, Notice, Result, Talk
from townsim.town.obligations import SCHEDULED, money, name_of
from townsim.town.replies import Keys, Replier
from townsim.town.statement import Counted, statement
from townsim.town.store import Store, id_key

TURNS = 3                   # turns a side
SAY_WORDS = 120
SAY_CHARS = 960
MAX_ACTS = 4
TALKS_A_DAY = 2
TALK_HOURS = 1.0
SUNDAY = 6
NOT_IN_TALK = ("converse", "book_appointment")
SILENT = "(says nothing)"
MAX_WORDS = 900
LETTERS = 3
LETTER_CUT = 60
REQUESTS = 3
OWED_LINES = 8
OWED_KEPT = 4
MONEY_DAYS = 7
MONEY_LINES = 8
MONEY_KEPT = 4
SCAN_CHARS = 20_000
SCAN_TRIES = 50
LAST_TURN = "You part after this."          # shown on a speaker's last turn only

REPLY_FORM = ('Reply with JSON only, in this form:\n'
              '{"say": "<your words>", "acts": [<operations from the list, in order>], '
              '"end": <true to end the talk>}')


# --- what is said ----------------------------------------------------------------------------


def said(value: Any, other: str) -> str:
    """What a speaker said, as it is kept: cleaned; cut at the first line that begins with
    `other`'s full or first name and a colon, so nobody writes the other's part; on one line;
    every word over 40 characters cut to 40; then cut to 120 words and 960 characters with
    `…`. Empty when nothing is left."""
    text = clean_text(value) or ""
    full = other.strip().casefold()
    names = [n for n in (full, full.split()[0] if full.split() else "") if n]
    kept = []
    for line in text.split("\n"):
        head = line.strip().casefold()
        if any(head.startswith(n) and head[len(n):].lstrip().startswith(":") for n in names):
            break
        kept.append(line)
    words = [w[:LONGEST_WORD] for w in " ".join(kept).split()]
    text = " ".join(words[:SAY_WORDS]) + ("…" if len(words) > SAY_WORDS else "")
    if len(text) > SAY_CHARS:
        head = text[:SAY_CHARS]
        space = head.rfind(" ")
        text = (head[:space] if space > 0 else head).rstrip() + "…"
    return text


def first_object(text: Any, key: str) -> dict | None:
    """The first top-level JSON object in `text` that has `key` (with, for `acts`, a list),
    reading at most `SCAN_CHARS` characters and trying at most `SCAN_TRIES` decodes. An
    object without it is skipped whole. Never raises."""
    if not isinstance(text, str):
        return None
    text = text[:SCAN_CHARS]
    decoder = json.JSONDecoder()
    at = text.find("{")
    for _ in range(SCAN_TRIES):
        if at == -1:
            return None
        try:
            value, end = decoder.raw_decode(text, at)
        except (ValueError, RecursionError):
            at = text.find("{", at + 1)
            continue
        if isinstance(value, dict) and key in value and (key != "acts" or isinstance(value[key], list)):
            return value
        at = text.find("{", end)
    return None


def parse_say(text: Any) -> dict | None:
    """A talk reply: the first top-level JSON object with a `say`, or None if there is none.
    `acts` is a list when the reply has one as a list, else empty; `end` is true only when it
    is the JSON value `true`."""
    found = first_object(text, "say")
    if found is None:
        return None
    elements = found.get("acts")
    return {"say": found["say"], "acts": elements if isinstance(elements, list) else [],
            "end": found.get("end") is True}


# --- which talks are held today ------------------------------------------------------------


def adults(store: Store, household: str) -> list[str]:
    """The household's adults, by id."""
    members = store.get("households", household).members
    return sorted((p for p in members if store.has("people", p) and store.get("people", p).age >= 18), key=id_key)


def _names_first(text: str, first: str) -> bool:
    return bool(first) and re.search(r"(?<!\w)" + re.escape(first.casefold()) + r"(?!\w)", text.casefold()) is not None


def household_pair(store: Store, household: str) -> tuple[str, str] | None:
    """The first pair of the household's adults, in id order, between whom something is
    open: an unseen letter from one to the other, a due of the household's in arrears, or an
    aim of one's that names the other's first name. None if there is no such pair."""
    grown = adults(store, household)
    for i, a in enumerate(grown):
        for b in grown[i + 1:]:
            if _open_between(store, household, a, b):
                return a, b
    return None


def _open_between(store: Store, household: str, a: str, b: str) -> bool:
    letter = any(m.sender in (a, b) and m.to in (a, b) and m.sender != m.to
                 for m in store.where("messages", seen=False))
    arrears = any(store.get("obligations", d.obligation).payer == household for d in store.where("dues", status="arrears"))
    named = any(_names_first(x.text, store.get("people", other).name.split()[0])
                for me, other in ((a, b), (b, a)) for x in store.where("aims", person=me) if x.text)
    return letter or arrears or named


def todays(store: Store) -> list[str]:
    """What may be held today, in order: today's booked appointments by id; the talks asked
    for today by id; on a Sunday, the households whose adults have something open between
    them and have not talked this week, by id."""
    found = [ap.id for ap in store.where("appointments", day=store.day) if ap.status == "booked"]
    found += [t.id for t in store.where("talks", day=store.day) if t.kind == "asked" and t.status == "waiting"]
    if dow(store.day) == SUNDAY:
        done = {t.ref for t in store.all("talks") if t.kind == "household" and week(t.day) == week(store.day)}
        found += [h.id for h in store.all("households") if h.id not in done and household_pair(store, h.id)]
    return found


def held_today(store: Store, pid: str) -> int:
    return len([t for t in store.where("talks", day=store.day) if t.status == "held" and pid in (t.a, t.b)])


def _free(store: Store, pid: str, hour: bool = True) -> bool:
    """Whether `pid` can talk now: an hour left (unless it was taken already) and fewer than
    two talks held today."""
    return (not hour or store.get("people", pid).hours_left >= TALK_HOURS) and held_today(store, pid) < TALKS_A_DAY


def _why_not(store: Store, pid: str, who: str) -> str:
    """Why `pid` (called `who`) cannot talk now: no hour left, or two talks already today."""
    if store.get("people", pid).hours_left < TALK_HOURS:
        return f"{who} had no time"
    return f"{who} had already had two talks that day"


def _tell(store: Store, pid: str, text: str, cause: str) -> None:
    store.add("notices", Notice(store.new_id("n", cause), store.day, pid, text), cause)


def _take_hour(store: Store, pid: str, cause: str) -> None:
    left = store.get("people", pid).hours_left
    store.set("people", pid, cause, hours_left=round(left - TALK_HOURS, 2))


# --- holding a talk --------------------------------------------------------------------------


@dataclass
class Held:
    """What came of `hold`: the talk held (or ""), each turn asked, and the next act number."""

    talk: str
    turns: list = field(default_factory=list)
    n: int = 0


def hold(store: Store, replier: Replier, keys: Keys, ref: str, n: int = 0) -> Held:
    """Hold what `ref` names (an appointment, an asked talk or a household) if both people can
    talk; else record why not. `n` is the day's running count of acts, which the talk's acts
    continue. If a speaker's view cannot be built, the talk is not held today and nothing of
    it is kept: the store is as it was before, and an engine error is kept."""
    mark = store.mark()
    try:
        return _hold(store, replier, keys, ref, n)
    except _NoView as e:
        store.undo_to(mark)
        store.engine_errors.append(e.args[0])
        return Held("", [], n)


class _NoView(Exception):
    """A speaker's view in a talk could not be built: the error as kept."""


def _hold(store: Store, replier: Replier, keys: Keys, ref: str, n: int) -> Held:
    cause = f"routine:talk:{ref}"
    if store.has("appointments", ref):
        ap = store.get("appointments", ref)
        lacking = [p for p in (ap.a, ap.b) if not _free(store, p)]
        if lacking:
            store.set("appointments", ap.id, cause, status="missed")
            for me, other in ((ap.a, ap.b), (ap.b, ap.a)):
                why = " and ".join(_why_not(store, p, "you" if p == me else store.get("people", p).name)
                                   for p in lacking)
                _tell(store, me, f"The talk with {store.get('people', other).name} on {date_text(ap.day)} did not "
                                 f"happen: {why}.", cause)
            return Held("", [], n)
        store.set("appointments", ap.id, cause, status="held")
        tk = store.new_id("tk", cause)
        store.add("talks", Talk(tk, store.day, ap.a, ap.b, "appointment", "", "held", ref=ap.id), cause)
        for p in (ap.a, ap.b):
            _take_hour(store, p, cause)
    elif store.has("talks", ref):
        t = store.get("talks", ref)
        tk = t.id
        if not _free(store, t.b) or not _free(store, t.a, hour=False):
            store.set("talks", tk, cause, status="not held")
            left = store.get("people", t.a).hours_left
            store.set("people", t.a, cause, hours_left=round(left + TALK_HOURS, 2))
            other = store.get("people", t.b).name
            text = (f"{other} had no time to talk today." if not _free(store, t.b)
                    else f"You had already talked twice today; the talk with {other} was not held.")
            _tell(store, t.a, text, cause)
            return Held("", [], n)
        store.set("talks", tk, cause, status="held")
        _take_hour(store, t.b, cause)
    elif store.has("households", ref):
        pair = household_pair(store, ref)
        if pair is None or not all(_free(store, p) for p in pair):
            return Held("", [], n)
        tk = store.new_id("tk", cause)
        store.add("talks", Talk(tk, store.day, pair[0], pair[1], "household", "", "held", ref=ref), cause)
        for p in pair:
            _take_hour(store, p, cause)
    else:
        store.engine_errors.append(f"talk {ref!r} d{store.day}: nothing to hold")
        return Held("", [], n)
    return _converse(store, replier, keys, tk, n)


def _line(store: Store, tk: str, speaker: str, text: str, results: list[str], cause: str) -> None:
    k = len(store.where("lines", talk=tk))
    store.add("lines", Line(store.new_id("ln", cause), tk, k, speaker, text, results), cause)


def _converse(store: Store, replier: Replier, keys: Keys, tk: str, n: int) -> Held:
    t = store.get("talks", tk)
    day, cause = store.day, f"routine:talk:{tk}"
    spoken = {t.a: 0, t.b: 0}
    speaker = t.a
    if t.kind == "asked":
        _line(store, tk, t.a, t.opening, [], cause)
        spoken[t.a], speaker = 1, t.b
    held = Held(tk, [], n)
    while spoken[speaker] < TURNS:
        other = t.b if speaker == t.a else t.a
        try:
            messages, ids = _view(store, speaker, tk)
        except Exception as e:      # noqa: BLE001  (any fault in building a view)
            raise _NoView(f"view:{speaker}:d{day}: {type(e).__name__}: {e}") from e
        key = keys.next(replier.model_for(speaker), day, speaker, "talk", tk)
        reply = replier.ask(key, messages)
        parsed = parse_say(reply.text)
        if parsed is None:
            held.turns.append(loop.Turn(speaker, key.text(), True, [], tk))
            break
        text = said(parsed["say"], store.get("people", other).name)
        outcomes, results = [], []
        elements = parsed["acts"]
        for element in elements[:MAX_ACTS]:
            outcome = _apply(store, speaker, element, held.n)
            results.append(_tell_result(store, outcome))
            outcomes.append(outcome)
            held.n += 1
        if len(elements) > MAX_ACTS:
            c = f"op:{speaker}:more:d{day}.{held.n}"
            more = f"Not done: {len(elements) - MAX_ACTS} more operations were written than the four carried out in a turn."
            rid = store.new_id("res", c)
            store.add("results", Result(rid, day, speaker, loop.MORE, more, False), c)
            results.append(rid)
            held.n += 1
        views.mark_seen(store, speaker, ids, f"routine:seen:{speaker}")
        _line(store, tk, speaker, text, results, cause)
        held.turns.append(loop.Turn(speaker, key.text(), False, outcomes, tk))
        spoken[speaker] += 1
        if parsed["end"]:
            break
        speaker = other
    store.set("talks", tk, cause, unseen=[t.a, t.b])
    return held


def _apply(store: Store, pid: str, element: Any, n: int) -> Outcome:
    act = loop.act_of(pid, element)
    name = act.op.strip().lower() if isinstance(act.op, str) else None
    if name in NOT_IN_TALK and isinstance(act.args, dict):
        cause = f"op:{pid}:{name}:d{store.day}.{n}"
        return Outcome(False, "Not done: that cannot be done in the middle of a talk.", act, cause)
    return apply_act(store, act, n)


def _tell_result(store: Store, outcome: Outcome) -> str:
    act = outcome.act
    rid = store.new_id("res", outcome.cause)
    store.add("results", Result(rid, store.day, act.actor, loop.op_written(act.op), outcome.msg, outcome.ok),
              outcome.cause)
    return rid


# --- what a speaker is shown -------------------------------------------------------------


def turn_lines(store: Store, line: Line, words: int | None = None, reader: str = "") -> list[str]:
    """`<Full name> said:` and what they said on one line begun `  > `, cut to `words` words
    when given; `  (says nothing)` when they said nothing. For a `reader`, a line beneath for
    each name in what is shown of it that is no one they know."""
    head = f"{name_of_who(store, line.speaker)} said:"
    if not line.text:
        return [head, f"  {SILENT}"]
    text = facts.cut_words(line.text, words)
    flagged = unknown.flags(store, reader, line.speaker, text) if reader else []
    return [head, "  > " + text] + flagged


def lines_of(store: Store, tk: str) -> list[Line]:
    return sorted(store.where("lines", talk=tk), key=lambda x: x.n)


def _relation(store: Store, pid: str, other: str) -> str:
    for o, _, words in facts.people_known(store, pid):
        if o == other:
            return words
    return "someone you do not know"


def _how(store: Store, pid: str, t: Talk) -> str:
    """How the talk came about, in one line."""
    name = "You" if t.a == pid else store.get("people", t.a).name
    if t.kind == "asked":
        return f"{name} asked to talk."
    if t.kind == "appointment" and store.has("appointments", t.ref):
        ap = store.get("appointments", t.ref)
        return f"{name} booked this talk on {date_text(ap.made_on)}, about {ap.about}."
    return f"At home, on {date_text(t.day)}."


def _between_obligations(store: Store, pid: str, other: str, most: int) -> list[str]:
    """What either side's accounts owe the other's, one line each as `WHAT IS OWED` writes
    them: what is in force, and anything with arrears; at most `most`, then a counted line."""
    a, b = _sides(store, pid, other)
    rows = []
    for ob in store.all("obligations"):
        if not ((ob.payer in a and ob.payee in b) or (ob.payer in b and ob.payee in a)):
            continue
        late, _ = facts.arrears(store, ob.id)
        if not late and ob.status not in SCHEDULED:
            continue
        party = f"to {name_of(store, ob.payee)}" if ob.payer in a else f"from {name_of(store, ob.payer)}"
        if ob.every == 0:
            how = f"once on {facts.when(ob.first)} {party}"
        else:
            every = {1: "a day", 7: "a week"}.get(ob.every, f"every {ob.every} days")
            how = f"{every} {party}" + facts.until(ob)
        rows.append(f"{ob.id}: {facts.memo(ob.memo)}, {money(ob.cents)} {how}"
                    + (f", {money(late)} overdue" if late else ""))
    if len(rows) > most:
        rows = rows[:most] + [Counted(f"({len(rows) - most} more not shown)")]
    return rows


def _between_requests(store: Store, pid: str, other: str) -> list:
    pair = {pid, other}
    rows = [rq for rq in store.where("requests", status="open") if {rq.sender, rq.to} == pair]
    return sorted(rows, key=lambda rq: (rq.day, id_key(rq.id)))


def _between_letters(store: Store, pid: str, other: str) -> list:
    pair = {pid, other}
    rows = [m for m in store.all("messages") if {m.sender, m.to} == pair and m.sender != m.to]
    return sorted(rows, key=lambda m: (m.day, id_key(m.id)))[-LETTERS:]


def _letter_lines(store: Store, pid: str, m, words: int | None) -> list[str]:
    """A letter between them, from the viewer's side: `From you to <name> on <date>:` for one
    they wrote, `From <name> on <date>:` for one they had; then its lines begun `  > `."""
    if m.sender == pid:
        head = f"From you to {name_of_who(store, m.to)} on {date_text(m.day)}:"
    else:
        head = f"From {name_of_who(store, m.sender)} on {date_text(m.day)}:"
    return [head] + views._quoted(facts.cut_words(m.text, words))


def _request_lines(store: Store, pid: str, rq) -> list[str]:
    """A request between them, from the viewer's side: one made to them as the ordinary view
    shows it; one they made as `<id> from you to <name>:`, its words begun `  > `, then
    `You ask them for <$x>.` and what code wrote of it."""
    if rq.sender != pid:
        return views._request_lines(store, rq)
    lines = [f"{rq.id} from you to {name_of_who(store, rq.to)}:"]
    if rq.text.strip():
        lines += views._quoted(rq.text)
        if rq.cents > 0:
            lines.append(f"You ask them for {money(rq.cents)}.")
    return lines + ([" ".join(rq.summary.split())] if rq.summary.strip() else [])


def _sides(store: Store, pid: str, other: str) -> tuple[list[str], list[str]]:
    """The accounts only `pid` controls, and those only `other` controls."""
    mine, theirs = accounts_of(store, pid), accounts_of(store, other)
    return [x for x in mine if x not in theirs], [x for x in theirs if x not in mine]


def _money_between(store: Store, pid: str, other: str) -> list:
    """The ledger's transfers in the last seven days, today's included, between an account
    only `pid` controls and one only `other` controls, oldest first."""
    a, b = _sides(store, pid, other)
    since = store.day - (MONEY_DAYS - 1)
    found = []
    for x in reversed(store.ledger.transfers):
        if x.t < since:
            break
        if (x.src in a and x.dst in b) or (x.src in b and x.dst in a):
            found.append(x)
    return found[::-1]


def _money_lines(store: Store, rows: list, most: int) -> list:
    """`Money between you in the last seven days:` and a line for each of the newest `most`
    transfers, newest last, after a line counting those left out."""
    if not rows:
        return []
    out = ["Money between you in the last seven days:"]
    if len(rows) > most:
        out.append(Counted(f"({len(rows) - most} earlier not shown)"))
    for x in rows[len(rows) - most:] if most else []:
        out.append(f"  {date_text(x.t)}  {money(x.cents)}  from {name_of(store, x.src)} to {name_of(store, x.dst)}  "
                   f"{facts.memo(x.memo)}")
    return out


class _Choice(NamedTuple):
    money: int = MONEY_LINES
    letter_words: int | None = None
    letters: int = LETTERS
    owed: int = OWED_LINES
    requests: int = REQUESTS


def _results(store: Store, pid: str, tk: str) -> list:
    """The unseen results of `pid`'s acts in this talk, oldest first."""
    ids = [r for line in lines_of(store, tk) if line.speaker == pid for r in line.results]
    rows = [store.get("results", r) for r in ids if store.has("results", r)]
    return [r for r in rows if not r.seen]


def _forms(store: Store, pid: str) -> list[str]:
    return [f for f in acts.forms(store, pid) if not any(f'"op": "{n}"' in f for n in NOT_IN_TALK)]


def _user(store: Store, pid: str, tk: str, c: _Choice) -> str:
    t = store.get("talks", tk)
    other = t.b if t.a == pid else t.a
    name = store.get("people", other).name
    blocks = [f"TODAY: {date_text(store.day)}.",
              "\n".join([f"YOU ARE TALKING WITH {name}", _relation(store, pid, other), _how(store, pid, t)])]
    between = list(_between_obligations(store, pid, other, c.owed))
    requests = _between_requests(store, pid, other)
    for rq in requests[:c.requests]:
        between += _request_lines(store, pid, rq)
    between += _money_lines(store, _money_between(store, pid, other), c.money)
    if len(requests) > c.requests:
        between.append(Counted(f"({len(requests) - c.requests} more requests between you not shown)"))
    letters = _between_letters(store, pid, other)
    for m in letters[len(letters) - c.letters:] if c.letters else []:
        between += _letter_lines(store, pid, m, c.letter_words)
    if letters and c.letters < len(letters):
        k = len(letters) - c.letters
        between.append(Counted(f"({k} more letter{'' if k == 1 else 's'} between you not shown)"))
    if between:
        blocks.append("\n".join(["BETWEEN YOU"] + [str(x) for x in between]))
    lines = lines_of(store, tk)
    spoke = len([x for x in lines if x.speaker == pid])
    if lines:
        blocks.append("\n".join(["THE TALK SO FAR"] + [x for line in lines for x in turn_lines(store, line, reader=pid)]))
    if spoke == TURNS - 1:
        blocks.append(LAST_TURN)
    shown = _results(store, pid, tk)
    if shown:
        reads = [r.id for r in shown if views._is_read(r)]
        latest = reads[-1] if reads else None
        blocks.append("\n".join(["WHAT CAME OF IT"] + [views._result_text(r, latest, views.READ_CUT) for r in shown]))
    st = statement(store, store.get("people", pid).household, ahead=facts.AHEAD)
    account = [f"Balance: {money(st.balance)}", f"Going out by {date_text(st.until)}:"]
    account += [str(x) for x in facts.coming(store, st.out, facts.GOING_OUT)]
    blocks.append("\n".join(["YOUR HOUSEHOLD'S ACCOUNT"] + account))
    blocks.append(f"You have {views._hours(store.get('people', pid).hours_left)} hours left this week.")
    blocks.append("\n".join(["OPERATIONS"] + _forms(store, pid)))
    return "\n\n".join(blocks)


class TalkLayout(NamedTuple):
    system: str
    user: str
    ids: list[str]
    steps: list[str]


def layout(store: Store, pid: str, tk: str) -> TalkLayout:
    """The view `pid` is shown for their turn in talk `tk`: at most 900 words on ordinary
    input and never over 2,000 words or 16,000 characters. While it is over 900 words, each
    only as far as needed: the money between them gives way to its four newest transfers
    (`money`); the letters between them are cut to 60 words each (`letters`),
    then left out, oldest first (`no letters`); what is owed between them gives way to four
    lines (`owed`); and the requests between them beyond the first, then that one too, move
    to the counted line (`requests`). The talk itself is never cut: it is at most six turns
    of 120 words and 960 characters."""
    system = f"{facts.who(store, pid)}\n\n{REPLY_FORM}"
    base = len(system.split())
    steps: list[str] = []
    c = _Choice()

    def over(choice: _Choice) -> bool:
        user = _user(store, pid, tk, choice)
        return base + len(user.split()) > MAX_WORDS or len(system) + len(user) > views.CHAR_CEILING

    t = store.get("talks", tk)
    other = t.b if t.a == pid else t.a
    letters = len(_between_letters(store, pid, other))
    if over(c) and len(_money_between(store, pid, other)) > MONEY_KEPT:
        c = c._replace(money=MONEY_KEPT)
        steps.append("money")
    if over(c) and letters:
        c = c._replace(letter_words=LETTER_CUT)
        steps.append("letters")
        if over(c):
            steps.append("no letters")
            while over(c) and c.letters > 0:
                c = c._replace(letters=c.letters - 1)
    if over(c) and len(_between_obligations(store, pid, other, OWED_LINES)) > OWED_KEPT + 1:
        c = c._replace(owed=OWED_KEPT)
        steps.append("owed")
    if over(c) and _between_requests(store, pid, other):
        steps.append("requests")
        while over(c) and c.requests > 0:
            c = c._replace(requests=c.requests - 1)
    ids = [r.id for r in _results(store, pid, tk)]
    return TalkLayout(system, _user(store, pid, tk, c), ids, steps)


def _view(store: Store, pid: str, tk: str) -> tuple[list[dict], list[str]]:
    v = layout(store, pid, tk)
    return [{"role": "system", "content": v.system}, {"role": "user", "content": v.user}], v.ids


def view(store: Store, pid: str, talk_id: str) -> list[dict]:
    """A system message and a user message for `pid`'s turn in talk `talk_id`: see `layout`.
    Building it changes nothing."""
    return _view(store, pid, talk_id)[0]


# --- what is shown afterwards ------------------------------------------------------------


def unseen_talks(store: Store, pid: str) -> list[Talk]:
    """The talks `pid` took part in that they have not been shown since, oldest first."""
    rows = [t for t in store.all("talks") if t.status == "held" and pid in t.unseen]
    return sorted(rows, key=lambda t: (t.day, id_key(t.id)))


def summary_lines(store: Store, pid: str, t: Talk, words: int) -> list[str]:
    """A talk as the ordinary view shows it: `With <full name> on <date>:`, then each turn
    cut to `words` words."""
    other = t.b if t.a == pid else t.a
    out = [f"With {store.get('people', other).name} on {date_text(t.day)}:"]
    for line in lines_of(store, t.id):
        out += turn_lines(store, line, words, pid)
    return out

