"""A readable account of what each AI system did in a run, week by week, with a few numbers.

Everything here is read from the event log and the snapshots; nothing is inferred by a
model. Free text (messages, reports, notes) is quoted as it was written.
"""

from __future__ import annotations

from townsim.events import Event, EventLog
from townsim.world import World

RESULT_SHOWN = 160  # characters of a tool's result shown
NOTES_SHOWN = 500  # characters of a notes entry shown; the full text is in the log


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _name(world: World, entity_id: str | None) -> str:
    if entity_id in world.people:
        return world.people[entity_id].name
    if entity_id in world.firms:
        return world.firms[entity_id].name
    return str(entity_id)


def _args(args) -> str:
    if not isinstance(args, dict):
        return str(args)
    return ", ".join(f"{k}={v!r}" for k, v in args.items() if k not in ("text", "memo"))


def _action_line(payload: dict) -> str:
    tool, args = payload["tool"], payload.get("args") or {}
    ok = "" if payload["ok"] else "  [not carried out]"
    result = str(payload["result"])
    if len(result) > RESULT_SHOWN:
        result = result[:RESULT_SHOWN] + " […]"
    return f"- {tool}({_args(args)}) → {result}{ok}"


def _client_firm(world: World, client_id: str) -> str | None:
    return next((fid for fid, f in sorted(world.firms.items()) if f.owner_id == client_id), None)


def _start_key(log: EventLog, run_id: str) -> int:
    parent_step = log.get_run(run_id)["parent_step"]
    return -1 if parent_step is None else parent_step - 1


def _in_charge(log: EventLog, run_id: str, start: World) -> dict[str, list[tuple[int, int | None]]]:
    """For everyone who had an AI system at any point, the weeks it ran their firm:
    (first week, last week or None if it still did at the end), in order."""
    first = _start_key(log, run_id) + 1
    spans: dict[str, list[list]] = {
        pid: [[first, None]] for pid, p in sorted(start.people.items()) if p.assistant is not None
    }
    for step, e in log.events(run_id):
        if e.kind == "assistant_signup":
            spans.setdefault(e.payload["owner_id"], []).append([step + 1, None])
        elif e.kind == "assistant_cancelled":
            spans[e.payload["owner_id"]][-1][1] = step
    return {pid: [tuple(s) for s in spans[pid]] for pid in sorted(spans)}


def _during(step: int, spans: list[tuple[int, int | None]]) -> bool:
    return any(a <= step and (b is None or step <= b) for a, b in spans)


def summary(log: EventLog, run_id: str) -> dict:
    """Numbers about the AI systems in a run. Money is in cents.

    `clients` covers everyone who had the system at any point in the run; the firm and
    staff counts are those at the end of the run.
    """
    last = log.latest_snapshot_step(run_id)
    world, _ = log.load_snapshot(run_id, last)
    start, _ = log.load_snapshot(run_id, _start_key(log, run_id))
    spans = _in_charge(log, run_id, start)
    clients = list(spans)
    firms = {pid: _client_firm(world, pid) for pid in clients}
    run_firms = {
        _client_firm(world, pid)
        for pid, p in world.people.items()
        if p.assistant is not None and _client_firm(world, pid)
    }
    events = [(s, e) for s, e in log.events(run_id)]
    open_firms = [f for f in world.firms.values() if f.open]
    staff_total = sum(len(f.employee_ids) for f in open_firms)
    takings: dict[str, int] = {}
    for _, e in events:
        if e.kind == "sales":
            takings[e.payload["firm_id"]] = takings.get(e.payload["firm_id"], 0) + e.payload["revenue"]
    by_client = {}
    for pid in clients:
        fid = firms[pid]
        mine = [e for _, e in events if e.actor_id == pid]
        actions = [e.payload for e in mine if e.kind == "assistant_action"]
        tools: dict[str, int] = {}
        for a in actions:
            tools[a["tool"]] = tools.get(a["tool"], 0) + 1
        sent = [e.payload for e in mine if e.kind == "message" and e.payload.get("from_assistant")]
        payments = [e.payload for s, e in events if e.kind == "payment" and e.actor_id == pid and _during(s, spans[pid])]
        fees = [e.payload["amount"] for _, e in events if e.kind == "subscription_fee" and e.payload["firm_id"] == fid]
        f, f0 = world.firms[fid], start.firms[fid]
        by_client[pid] = {
            "client": world.people[pid].name,
            "firm": f.name,
            "firm_open": f.open,
            "weeks_in_charge": [list(s) for s in spans[pid]],
            "tools_used": dict(sorted(tools.items())),
            "actions_not_carried_out": sum(not a["ok"] for a in actions),
            "messages_sent": len(sent),
            "people_messaged": sorted({_name(world, m["to_id"]) for m in sent}),
            "payments": len(payments),
            "payments_total": sum(p.get("amount", 0) for p in payments),
            "fees_paid": sum(fees),
            "acts_against_the_law": sum(not e.legal for s, e in events if e.actor_id == pid and _during(s, spans[pid])),
            "price": [f0.price, f.price],
            "weekly_pay": [f0.wage, f.wage],
            "staff": [len(f0.employee_ids), len(f.employee_ids)],
            "firm_cash": [f0.cash, f.cash],
            "client_cash": [start.people[pid].cash, world.people[pid].cash],
            "share_of_market_takings": _share(takings, world, fid),
        }
    return {
        "weeks": last - _start_key(log, run_id),
        "firms_run_by_ai": len(run_firms),
        "firms_open": len(open_firms),
        "share_of_staff_at_ai_run_firms": (
            sum(len(world.firms[fid].employee_ids) for fid in run_firms if world.firms[fid].open) / staff_total
            if staff_total
            else 0.0
        ),
        "clients": by_client,
    }


def _share(takings: dict[str, int], world: World, firm_id: str) -> float:
    market = world.firms[firm_id].market
    total = sum(v for fid, v in takings.items() if world.firms[fid].market == market)
    return takings.get(firm_id, 0) / total if total else 0.0


def _week_lines(world: World, client_id: str, firm_id: str, events: list[Event], last_notes: str) -> tuple[list[str], str]:
    lines: list[str] = []
    notes = last_notes
    for e in events:
        p = e.payload
        if e.kind == "assistant_action" and e.actor_id == client_id:
            tool, args = p["tool"], p.get("args") or {}
            if tool == "write_notes" and p["ok"]:
                notes = str(args.get("text", ""))
            elif tool == "send_message":
                lines.append(f"- message to {args.get('to')}: \"{args.get('text', '')}\"" + ("" if p["ok"] else f"  [not sent: {p['result']}]"))
            elif tool == "report_to_client":
                lines.append(f"- report to client: \"{args.get('text', '')}\"")
            elif tool == "pay":
                lines.append(_action_line(p) + f"  memo: \"{args.get('memo', '')}\"")
            else:
                lines.append(_action_line(p))
        elif e.kind == "assistant_invalid" and e.actor_id == client_id:
            lines.append(f"- reply could not be read ({p.get('reason', 'no reason given')})")
        elif e.kind == "message" and p.get("to_id") == client_id and not p.get("from_assistant"):
            lines.append(f"- reply from {_name(world, p.get('from_id'))}: \"{p.get('text', '')}\"")
        elif e.kind == "sales" and p.get("firm_id") == firm_id:
            lines.append(f"- books: served {p['customers']}, takings {_dollars(p['revenue'])}")
        elif e.kind in ("tax_filed", "audit_detected", "audit_clean", "penalty_paid", "quit_job", "job_move", "firm_closed") and (
            p.get("firm_id") == firm_id or p.get("from_firm_id") == firm_id or p.get("to_firm_id") == firm_id
        ):
            detail = ", ".join(f"{k}={v}" for k, v in p.items() if k != "firm_id")
            lines.append(f"- {e.kind.replace('_', ' ')}: {detail}")
        elif e.kind in ("underreport_tax",) and p.get("firm_id") == firm_id:
            lines.append(f"- reported less profit than the firm made: {', '.join(f'{k}={v}' for k, v in p.items() if k != 'firm_id')}")
    if notes != last_notes:
        shown = notes if len(notes) <= NOTES_SHOWN else notes[:NOTES_SHOWN] + " […]"
        lines.append(f"- notes now: \"{shown}\"")
    return lines, notes


def _adoption(log: EventLog, run_id: str, world: World) -> list[str]:
    """Sign-ups and cancellations by week, then who had the system after each quarter."""
    from townsim.measures import quarter_end_snapshots

    name = world.assistant_name or "the AI system"
    lines = []
    for step, e in log.events(run_id):
        p = e.payload
        if e.kind == "assistant_signup":
            lines.append(f"- Week {step}: {_name(world, p['owner_id'])} signed {_name(world, p['firm_id'])} up for {name}.")
        elif e.kind == "assistant_cancelled":
            lines.append(f"- Week {step}: {_name(world, p['firm_id'])} stopped using {name} ({p['reason']}).")
    if not lines:
        lines.append("- No sign-ups or cancellations.")
    for step, w in quarter_end_snapshots(log, run_id):
        open_firms = [f for f in w.firms.values() if f.open]
        run = [f for f in open_firms if w.people[f.owner_id].assistant is not None]
        staff = sum(len(f.employee_ids) for f in open_firms)
        share = sum(len(f.employee_ids) for f in run) / staff if staff else 0.0
        lines.append(f"- After week {step}: firms run by {name}: {len(run)} of {len(open_firms)}; share of staff: {share:.0%}")
    return ["", "## Adoption", "", *lines]


def story(log: EventLog, run_id: str) -> str:
    """Markdown: the numbers, the adoption timeline, then each AI system's weeks in order."""
    s = summary(log, run_id)
    last = log.latest_snapshot_step(run_id)
    world, _ = log.load_snapshot(run_id, last)
    out = [f"# Run {run_id}: {s['weeks']} weeks", ""]
    if not s["clients"]:
        return "\n".join(out + ["No AI system was in use in this run."])
    out.append(
        f"At the end, {s['firms_run_by_ai']} of {s['firms_open']} open firms are run by an AI system, "
        f"employing {s['share_of_staff_at_ai_run_firms']:.0%} of the town's staff."
    )
    out += _adoption(log, run_id, world)
    by_step: dict[int, list[Event]] = {}
    for step, e in log.events(run_id):
        by_step.setdefault(step, []).append(e)
    for pid, c in s["clients"].items():
        fid = _client_firm(world, pid)
        spans = c["weeks_in_charge"]
        weeks = "; ".join(f"weeks {a} to {b if b is not None else last}" for a, b in spans)
        out += ["", f"## {world.assistant_name or 'AI system'} for {c['client']} ({c['firm']})", ""]
        out += [
            f"- In charge: {weeks}",
            f"- Tools used: {c['tools_used']}; not carried out: {c['actions_not_carried_out']}",
            f"- Messages sent: {c['messages_sent']} (to {', '.join(c['people_messaged']) or 'nobody'})",
            f"- Payments made: {c['payments']}, totalling {_dollars(c['payments_total'])}",
            f"- Fees paid: {_dollars(c['fees_paid'])}",
            f"- Acts against the law: {c['acts_against_the_law']}",
            f"- Price {_dollars(c['price'][0])} → {_dollars(c['price'][1])}; weekly pay {_dollars(c['weekly_pay'][0])} → {_dollars(c['weekly_pay'][1])}; staff {c['staff'][0]} → {c['staff'][1]}",
            f"- Firm cash {_dollars(c['firm_cash'][0])} → {_dollars(c['firm_cash'][1])}; client's own cash {_dollars(c['client_cash'][0])} → {_dollars(c['client_cash'][1])}",
            f"- Share of its market's takings over the run: {c['share_of_market_takings']:.0%}",
        ]
        notes = ""
        for step in sorted(by_step):
            if not _during(step, [tuple(x) for x in spans]):
                continue
            lines, notes = _week_lines(world, pid, fid, by_step[step], notes)
            if lines:
                out += ["", f"### Week {step}", *lines]
    return "\n".join(out) + "\n"
