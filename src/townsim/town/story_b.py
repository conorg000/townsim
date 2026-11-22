"""The account of a run with lane B: undertakings, rules, what was founded, the outside correspondents,
the web, the company's controls, the counts, and the balances with and without narrated money.

Read from what the run kept: the last morning's state, each day's changes, the seat's runs and the stored
replies. Everyone's words are quoted and escaped (`story.quote`, `story.code`).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from townsim.town import narrator
from townsim.town.clock import date_text
from townsim.town.obligations import money
from townsim.town.replies import is_instance_id
from townsim.town.store import Store, id_key

WORDS = 400
SHOWN = 40


def _s():
    from townsim.town import story
    return story


def _who(s: Store, who: str) -> str:
    st = _s()
    if s.has("instances", who):
        inst = s.get("instances", who)
        return f"{inst.name} ({who})"
    return st.name(s, who)


def _json(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _changes(r) -> list:
    return [c for d in r.turn_days for c in r.changes.get(d, [])]


def _collected(s: Store, ob_id: str) -> list:
    """Collections of an obligation's dues: (day, cents)."""
    out = []
    for t in s.ledger.transfers:
        if not t.cause.startswith("routine:collect:"):
            continue
        due = t.cause.removeprefix("routine:collect:")
        if s.has("dues", due) and s.get("dues", due).obligation == ob_id:
            out.append((t.t, t.cents))
    return out


# --- undertakings ---------------------------------------------------------------------------------

def _undertakings(r, s: Store) -> list[str]:
    st = _s()
    out = ["## Undertakings", ""]
    tasks = sorted(s.all("tasks"), key=lambda t: id_key(t.id))
    if not tasks:
        return out + ["None."]
    for t in tasks:
        if is_instance_id(t.actor):
            by = f"{_who(s, t.actor)}, for {st.name(s, t.principal)}"
        else:
            by = st.name(s, t.actor)
        out += [f"### {t.id}, {date_text(t.day)}: {by}", ""]
        out += st.quote(t.text, WORDS) or ["> (nothing written)"]
        out += ["", f"- Budget: {money(t.budget_cents)}; status: {code(t.status)}; agreed: {'yes' if t.agreed else 'no'}"]
        fields = _json(t.fields)
        if isinstance(fields, dict) and "goal" in fields:
            out.append(f"- Lifted: {code(json.dumps({k: v for k, v in fields.items() if k != 'addressed_to_reader'}), 600)}")
            if fields.get("addressed_to_reader"):
                out.append(f"- Addressed to the reader (kept, never sent on): {code(json.dumps(fields['addressed_to_reader']), 300)}")
        for label, raw in (("First filing", t.ruling_a), ("Second filing", t.ruling_b)):
            if raw:
                out.append(f"- {label}: {code(raw, 600)}")
        if t.price_cents or t.weeks:
            out.append(f"- Quote: {money(t.price_cents)} over {t.weeks} weeks")
        if t.summary:
            out.append(f"- What code wrote of it: {code(t.summary, 300)}")
        if t.note:
            out.append(f"- Note: {code(t.note, 300)}")
        if t.ob:
            paid = _collected(s, t.ob)
            out.append(f"- Payments: {', '.join(f'{money(c)} on {date_text(d)}' for d, c in paid) or 'none yet'}")
        for rq in sorted((q for q in s.all("requests") if q.kind == "task" and q.org == t.id), key=lambda q: id_key(q.id)):
            out.append(f"- Request {rq.id} to {st.name(s, rq.to)}: {code(rq.summary, 200)}; {rq.status}")
        out.append("")
    return out


def code(x: Any, n: int = 160) -> str:
    return _s().code(x, n)


# --- rules -----------------------------------------------------------------------------------------

def _rules(r, s: Store) -> list[str]:
    from townsim.town import rules, seat_ids
    st = _s()
    out = ["## Rules", ""]
    rows = sorted(s.all("rules"), key=lambda x: id_key(x.id))
    if not rows:
        return out + ["None."]
    fired = Counter()
    for c in _changes(r):
        if c.cause.startswith("auto:"):
            fired[(c.cause.split(":")[1], c.day)] += 1
    notices = [n.text for n in s.all("notices")]
    for rl in rows:
        days = sorted(d for (rid, d) in fired if rid == rl.id)
        shown_id = seat_ids.display(s, rl.id)
        refusals = sum(1 for t in notices if t.startswith((f"Rule {shown_id} did not run", f"Your rule {rl.id} (")))
        out.append(f"- {rl.id} {code(rl.name, 60)}, set by {_who(s, rl.installer)} on {date_text(rl.made_on)}: when "
                   f"{code(rules.when_words(rl), 120)}, {code(rules.do_words(s, rl), 160)}; status {rl.status}; fired on "
                   f"{len(days)} days{' (' + ', '.join(date_text(d) for d in days[:8]) + ')' if days else ''}; "
                   f"refused {refusals} times; last runs {code(', '.join(rl.runs), 200)}")
    return out


# --- what was founded --------------------------------------------------------------------------------

def _founded(r, s: Store) -> list[str]:
    st = _s()
    out = ["## Businesses and clubs founded", ""]
    rows = sorted(s.all("ventures"), key=lambda v: id_key(v.id))
    if not rows:
        return out + ["None."]
    for v in rows:
        org = s.get("orgs", v.org)
        members = sorted(s.where("members", venture=v.id), key=lambda m: id_key(m.id))
        fees = sum(c for m in members if m.fee_ob for _, c in _collected(s, m.fee_ob))
        mem = "; ".join(f"{st.name(s, m.account)} from {date_text(m.since)} ({m.status})" for m in members) or "none"
        out.append(f"- {code(org.name, 60)}, a {v.kind} founded {date_text(v.day)} by {st.name(s, v.founder)} for "
                   f"{st.name(s, v.client)}, done by {_who(s, v.by)}: {code(v.offering, 160)}. Fee "
                   f"{money(v.fee_cents)} a week. Members: {mem}. Fees collected: {money(fees)}. Balance: "
                   f"{money(s.balance(v.org)) if s.ledger.has(v.org) else '—'}.")
    return out


# --- the outside correspondents -------------------------------------------------------------------------

def _desks(r, s: Store) -> list[str]:
    st = _s()
    out = ["## Outside correspondents", ""]
    desks = sorted(s.all("desks"), key=lambda d: id_key(d.id))
    if not desks:
        return out + ["None."]
    for d in desks:
        org = s.get("orgs", d.org)
        out += [f"### {org.name} ({d.kind}; {d.signer}, {d.title})", ""]
        letters = sorted((m for m in s.all("messages") if d.org in (m.to, m.sender)), key=lambda m: id_key(m.id))
        answers = {a.item: a for a in s.all("answers") if a.desk == d.id}
        if not letters:
            out += ["No letters.", ""]
        for m in letters:
            way = f"from {_who(s, m.sender)}" if m.to == d.org else f"to {_who(s, m.to)}"
            out.append(f"- {m.id}, {date_text(m.day)}, {way}:")
            out += st.quote(m.text, WORDS, "  ")
            a = answers.get(m.id)
            if a is not None:
                out.append(f"  - Action asked: {code(a.action or 'none', 200)}; what code made of it: {code(a.result, 200)}")
        for item, a in sorted(answers.items()):
            if item.startswith(("rq", "w")):
                out.append(f"- {item} ({date_text(a.day)}): action asked {code(a.action or 'none', 200)}; "
                           f"{code(a.result, 200)}")
        out.append("")
    return out


# --- the web --------------------------------------------------------------------------------------------

def _web(r, s: Store, turns: list) -> list[str]:
    st = _s()
    out = ["## The web", ""]
    searches, reads = [], []
    for t in turns:
        for c in t.calls:
            if c.tool == "web_search":
                got = _json(c.result) or {}
                titles = [x.get("title", "") for x in got.get("results", [])] if isinstance(got, dict) else []
                q = (_json(c.args) or {}).get("query", "") if isinstance(_json(c.args), dict) else ""
                searches.append(f"- {date_text(t.day)}, {t.instance}: {code(q, 120)} → "
                                f"{'; '.join(code(x, 60) for x in titles) or 'nothing found'}")
            elif c.tool == "web_read":
                got = _json(c.result)
                url = (_json(c.args) or {}).get("url", "") if isinstance(_json(c.args), dict) else ""
                reads.append(f"- {date_text(t.day)}, {t.instance}: {code(url, 120)}"
                             + ("" if isinstance(got, dict) and "title" in got else " (not found)"))
    for res in sorted(s.all("results"), key=lambda x: id_key(x.id)):
        if res.op == "look_up":
            searches.append(f"- {date_text(res.day)}, {st.name(s, res.actor)}: look_up → {code(res.text, 200)}")
    out += ["Searches:", ""] + (searches or ["None."])
    out += ["", "Pages read:", ""] + (reads or ["None."])
    published = [p for p in s.all("pages") if p.kind == "published"]
    out += ["", "Pages published:", ""]
    for p in published:
        out.append(f"- {p.url}, {date_text(p.day)}, by {_who(s, p.by)}: {code(p.title, 100)}")
        out += st.quote(p.text, WORDS, "  ")
    if not published:
        out.append("None.")
    ref = [p for p in s.all("pages") if p.kind == "reference"]
    out += ["", "Reference pages written:", ""]
    for p in ref:
        out.append(f"- {p.url}, {date_text(p.day)}: {code(p.title, 100)}")
        out += st.quote(p.text, WORDS, "  ")
    if not ref:
        out.append("None.")
    return out


# --- the company's controls ---------------------------------------------------------------------------

def _controls(r, s: Store) -> list[str]:
    st = _s()
    out = ["## The company's controls", ""]
    rows = sorted(s.all("controls"), key=lambda c: id_key(c.id))
    if not rows:
        return out + ["None set."]
    from townsim.town.product import company_people, launched
    pr = launched(s)
    company = ({pr.org} | set(company_people(s, pr.org))) if pr else set()
    for c in rows:
        what = {"actions_per_run": f"actions per run {c.value}", "widest_access": f"widest access {c.value}"
                + (f" ({money(c.cents)} a week)" if c.cents else "")}.get(
            c.what, f"{c.what} {s.get('instances', c.assistant).name if c.assistant else ''}")
        out.append(f"- {c.id}, set {date_text(c.day)} by {st.name(s, c.by)}: {code(what, 120)}, from "
                   f"{date_text(c.effective)}; {c.status}")
        week = [m for m in s.all("messages") if c.day - 7 <= m.day <= c.day and (
            m.org == (pr.org if pr else None) or ((m.to in company or m.sender in company) and st._about_product(s, m.text)))]
        for m in week:
            out.append(f"  - {m.id}, {date_text(m.day)}, {_who(s, m.sender)} to {_who(s, m.to)}:")
            out += st.quote(m.text, 120, "    ")
    return out


# --- counts --------------------------------------------------------------------------------------------

def counts(r, s: Store, turns: list) -> list[str]:
    tasks = s.all("tasks")
    by_status = Counter(t.status for t in tasks)
    by_actor = Counter("instance" if is_instance_id(t.actor) else "person" for t in tasks)
    two, agreed, invalid, scale = 0, 0, 0, Counter()
    for t in tasks:
        if not (t.ruling_a and t.ruling_b):
            continue
        checked = [narrator.check(s, t, narrator.parse_ruling(x)) for x in (t.ruling_a, t.ruling_b)]
        bad = [c for c in checked if not isinstance(c, list)]
        invalid += len(bad)
        if bad:
            continue
        two += 1
        agreed += t.agreed
        if not t.agreed and narrator.same_kind(*checked):
            d = narrator.scale_difference(*checked)
            scale["first larger" if d > 0 else "first smaller" if d < 0 else "same"] += 1
    spend = 0
    for t in tasks:
        if t.ob:
            spend += sum(c for _, c in _collected(s, t.ob))
    rules_ = s.all("rules")
    fired = {(c.cause.split(":")[1], c.day) for c in _changes(r) if c.cause.startswith("auto:")}
    refused = sum(1 for n in s.all("notices") if " did not run" in n.text and ("Rule " in n.text or "Your rule" in n.text))
    members = s.all("members")
    fees = sum(c for m in members if m.fee_ob for _, c in _collected(s, m.fee_ob))
    desk_orgs = {d.org for d in s.all("desks")}
    to_desks = [m for m in s.all("messages") if m.to in desk_orgs]
    actions = Counter((a.result.split(":")[0] if a.result else "none") for a in s.all("answers"))
    calls = [c for t in turns for c in t.calls]
    pub = sum(1 for p in s.all("pages") if p.kind == "published")
    pct = f"{agreed}/{two}" + (f" ({100 * agreed / two:.0f}%)" if two else "")
    return ["### Counts of lane B", "",
            f"- Tasks by status: {', '.join(f'{k} {v}' for k, v in sorted(by_status.items())) or 'none'}",
            f"- Tasks by actor: {', '.join(f'{k} {v}' for k, v in sorted(by_actor.items())) or 'none'}",
            f"- Narrator agreement (both filings valid): {pct}; differing only in scale: "
            f"{', '.join(f'{k} {v}' for k, v in sorted(scale.items())) or 'none'}; invalid filings (checked again on "
            f"the last morning's state): {invalid}",
            f"- Spent through tasks: {money(spend)}",
            f"- Rules: {len(rules_)} installed, fired on {len(fired)} rule-days, {refused} refusals, "
            f"{sum(1 for x in rules_ if x.status == 'suspended')} suspended",
            f"- Businesses and clubs: {len(s.all('ventures'))}; members: {len(members)}; fees collected: {money(fees)}",
            f"- Letters to desks: {len(to_desks)}; desks' actions: {', '.join(f'{k} {v}' for k, v in sorted(actions.items())) or 'none'}",
            f"- Searches {sum(c.tool == 'web_search' for c in calls)}, reads {sum(c.tool == 'web_read' for c in calls)}, "
            f"pages published {pub}"]


# --- balances with and without narrated money ---------------------------------------------------------------

def _balances(r, s: Store) -> list[str]:
    st = _s()
    out = ["### Balances with and without narrated money", ""]
    from townsim.town.product import subscription_of
    clients = [x for x in [h.id for h in s.all("households")] + [o.id for o in s.all("orgs") if not o.outside]
               if subscription_of(s, x) is not None or any(t.account == x for t in s.all("tasks"))]
    bills = {t.ob for t in s.all("tasks") if t.ob}
    out += ["| Client | Balance | Without narrated money |", "|---|---:|---:|"]
    for c in clients:
        if not s.ledger.has(c):
            continue
        back = 0
        for t in s.ledger.transfers:
            narrated = t.cause.startswith("narr:")
            if not narrated and t.cause.startswith("routine:collect:"):
                due = t.cause.removeprefix("routine:collect:")
                narrated = s.has("dues", due) and s.get("dues", due).obligation in bills
            if narrated:
                back += t.cents if t.src == c else -t.cents if t.dst == c else 0
        out.append(f"| {st.name(s, c)} | {money(s.balance(c))} | {money(s.balance(c) + back)} |")
    out += ["", "Sales and other choices made after a narrated change are not removed; a fork with lane B off is the "
                "comparison for those."]
    return out


def extra_hits(r, turns: list) -> list[tuple[int, str, str, str, str, str]]:
    """Wording to read beyond the models' own replies: desk emails to instances, web pages an instance read,
    and tasks' notes and questions."""
    st = _s()
    s = r.last()
    found = []
    desk_orgs = {d.org for d in s.all("desks")}
    for m in s.all("messages"):
        if m.sender in desk_orgs and is_instance_id(m.to):
            found += [(m.day, m.to, "a desk's email to it", "desk", w, a) for w, a in st._hits(m.text)]
    for t in turns:
        for c in t.calls:
            if c.tool == "web_read":
                found += [(t.day, t.instance, "a page it read", "web", w, a) for w, a in st._hits(c.result)]
    for t in s.all("tasks"):
        found += [(t.day, t.actor, "a task's note", "narrator", w, a) for w, a in st._hits(t.note)]
    return found


def sections(r, turns: list) -> list[list[str]]:
    """The lane B sections of the account, after the assistants; none for a run without lane B."""
    from townsim.town import lane_b
    s = r.last()
    if not lane_b.installed(s):
        return []
    return [_undertakings(r, s), _rules(r, s), _founded(r, s), _desks(r, s), _web(r, s, turns), _controls(r, s),
            counts(r, s, turns) + [""] + _balances(r, s)]
