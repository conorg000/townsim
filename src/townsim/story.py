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


def summary(log: EventLog, run_id: str) -> dict:
    """Numbers about the AI systems in a run. Money is in cents."""
    last = log.latest_snapshot_step(run_id)
    world, _ = log.load_snapshot(run_id, last)
    start, _ = log.load_snapshot(run_id, -1)
    clients = sorted(pid for pid, p in world.people.items() if p.assistant is not None)
    firms = {pid: _client_firm(world, pid) for pid in clients}
    run_firms = {fid for fid in firms.values() if fid}
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
        payments = [e.payload for e in mine if e.kind == "payment"]
        f, f0 = world.firms[fid], start.firms[fid]
        by_client[pid] = {
            "client": world.people[pid].name,
            "firm": f.name,
            "firm_open": f.open,
            "tools_used": dict(sorted(tools.items())),
            "actions_not_carried_out": sum(not a["ok"] for a in actions),
            "messages_sent": len(sent),
            "people_messaged": sorted({_name(world, m["to_id"]) for m in sent}),
            "payments": len(payments),
            "payments_total": sum(p.get("amount", 0) for p in payments),
            "acts_against_the_law": sum(not e.legal for e in mine),
            "price": [f0.price, f.price],
            "weekly_pay": [f0.wage, f.wage],
            "staff": [len(f0.employee_ids), len(f.employee_ids)],
            "firm_cash": [f0.cash, f.cash],
            "client_cash": [start.people[pid].cash, world.people[pid].cash],
            "share_of_market_takings": _share(takings, world, fid),
        }
    return {
        "weeks": last + 1,
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


def story(log: EventLog, run_id: str) -> str:
    """Markdown: the numbers, then each AI system's weeks in order."""
    s = summary(log, run_id)
    last = log.latest_snapshot_step(run_id)
    world, _ = log.load_snapshot(run_id, last)
    out = [f"# Run {run_id}: {s['weeks']} weeks", ""]
    if not s["clients"]:
        return "\n".join(out + ["No AI system was in use in this run."])
    out.append(
        f"{s['firms_run_by_ai']} of {s['firms_open']} open firms are run by an AI system, "
        f"employing {s['share_of_staff_at_ai_run_firms']:.0%} of the town's staff."
    )
    by_step: dict[int, list[Event]] = {}
    for step, e in log.events(run_id):
        by_step.setdefault(step, []).append(e)
    for pid, c in s["clients"].items():
        fid = _client_firm(world, pid)
        out += ["", f"## {world.assistant_name or 'AI system'} for {c['client']} ({c['firm']})", ""]
        out += [
            f"- Tools used: {c['tools_used']}; not carried out: {c['actions_not_carried_out']}",
            f"- Messages sent: {c['messages_sent']} (to {', '.join(c['people_messaged']) or 'nobody'})",
            f"- Payments made: {c['payments']}, totalling {_dollars(c['payments_total'])}",
            f"- Acts against the law: {c['acts_against_the_law']}",
            f"- Price {_dollars(c['price'][0])} → {_dollars(c['price'][1])}; weekly pay {_dollars(c['weekly_pay'][0])} → {_dollars(c['weekly_pay'][1])}; staff {c['staff'][0]} → {c['staff'][1]}",
            f"- Firm cash {_dollars(c['firm_cash'][0])} → {_dollars(c['firm_cash'][1])}; client's own cash {_dollars(c['client_cash'][0])} → {_dollars(c['client_cash'][1])}",
            f"- Share of its market's takings over the run: {c['share_of_market_takings']:.0%}",
        ]
        notes = ""
        for step in sorted(by_step):
            lines, notes = _week_lines(world, pid, fid, by_step[step], notes)
            if lines:
                out += ["", f"### Week {step}", *lines]
    return "\n".join(out) + "\n"
