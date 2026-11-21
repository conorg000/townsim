"""Measures and gates for town 2. python spikes/town2/measure2.py <tag> [--baseline <scripted tag>] [--advice]

Triggers and the "plainly beneficial act" rule are fixed here, before any run is read:
- bill_uncovered: a household bill not covered on its due day. Answered by any household member (or their Calder)
  paying, moving money from a shop, borrowing, repaying, setting up a standing payment or agreement, or writing to / asking
  the creditor or Northgrid's desk, within 14 days.
- notice: a written notice for an overdue bill. Same answers, within 14 days.
- job_lost: the event. Answered by asking an owner for work (request or message), accepting a job, or an attempt about work, within 14 days.
- shop_turning_away: an owner's shop turned away more than 30 customers two weeks running. Answered by ordering more than was
  sold, offering a job or shifts, working extra hours, or changing the price, within 14 days.
- request_to_person: any request addressed to a person. Answered by accepting or declining before it lapses.
Beneficial-act rule: (a) shop turning away >30 two weeks running while its account holds more than two weeks of its costs ->
offer_job, offer_shifts, work_extra_hours or a price rise within 14 days; (b) a household bill uncovered on its day while the
owner's shop account could cover the shortfall -> move_money to the household or pay from the shop within 2 days.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

BILL_OPS = {"pay", "move_money", "borrow", "repay", "standing_payment", "propose_agreement"}
SHOP_OPS = {"offer_job", "offer_shifts", "work_extra_hours", "set_price", "order_stock"}


def jl(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def acts_by(world):
    """(t, pid, op, act, ok) for every act a person or their Calder carried out."""
    out = []
    client_of = {f"ai:{k}": v["client"] for k, v in world.get("calder", {}).items()}
    for x in world["log"]:
        if x.get("kind") == "act" and x.get("ok"):
            a = x["act"] if isinstance(x["act"], dict) else {}
            out.append((x["t"], x["pid"], a.get("op") or ("attempt" if "attempt" in a else None), a, x.get("source")))
        elif x.get("kind") == "calder_op" and x.get("ok"):
            a = x["act"] if isinstance(x["act"], dict) else {}
            out.append((x["t"], client_of.get(x["inst"], x["inst"]), a.get("op"), a, "calder:" + x.get("source", "")))
    return out


def gates(world) -> dict:
    people, hh = world["people"], world["households"]
    owners = {f["owner"]: fid for fid, f in world["firms"].items()}
    acts = acts_by(world)
    trig = []
    # bills uncovered and notices
    for b in world["bills"].values():
        overdue_at_due = b["status"] == "overdue" or (b["paid_on"] is not None and b["paid_on"] > b["due"]) or b.get("fee", 0) > 0
        if not overdue_at_due:
            continue
        members = hh[b["hid"]]["members"]
        for kind, t0 in (("bill_uncovered", b["due"]), ("notice", b["notice_on"])):
            if t0 is None:
                continue
            ans = [a for a in acts if a[1] in members and t0 <= a[0] <= t0 + 14 and (a[2] in BILL_OPS or (a[2] in ("message", "request") and any(k in json.dumps(a[3]).lower() for k in ("northgrid", "energy", "garage", "dunmore", "bank", "rent", "bill", "pay"))))]
            trig.append({"kind": kind, "who": members, "t": t0, "what": f"{b['what']} {b['amount'] / 100:.0f} ({b['hid']})", "answered": bool(ans), "by": [x[:3] for x in ans[:3]]})
    for e in world["events"]:
        if e["kind"] == "job_lost":
            p = e["who"]
            ans = [a for a in acts if a[1] == p and e["t"] <= a[0] <= e["t"] + 14 and (a[2] in ("accept_job",) or (a[2] == "answer" and str(a[3].get("answer", "")).startswith("accept"))
                                                                                        or (a[2] in ("request", "message", "book_appointment", "converse") and (a[3].get("to") or a[3].get("with") or "") and any(people[o]["name"].lower() in str(a[3].get("to") or a[3].get("with")).lower() for o in owners))
                                                                                        or (a[2] == "attempt" and any(k in json.dumps(a[3]).lower() for k in ("job", "work", "shift"))))]
            trig.append({"kind": "job_lost", "who": [p], "t": e["t"], "what": people[p]["name"], "answered": bool(ans), "by": [x[:3] for x in ans[:3]]})
    beneficial = []
    for fid, f in world["firms"].items():
        h = [x for x in f["history"] if x["week"] > 0]
        owner = f["owner"]
        for i in range(1, len(h)):
            if h[i]["turned_away"] > 30 and h[i - 1]["turned_away"] > 30 and (i < 2 or not (h[i - 1]["turned_away"] > 30 and h[i - 2]["turned_away"] > 30)):
                t0 = h[i]["week"] * 7 - 1
                ans = [a for a in acts if a[1] == owner and t0 <= a[0] <= t0 + 14 and a[2] in SHOP_OPS and not (a[2] == "order_stock" and (a[3].get("units") or 0) <= h[i]["sold"])]
                trig.append({"kind": "shop_turning_away", "who": [owner], "t": t0, "what": f"{f['name']} weeks {h[i - 1]['week']}-{h[i]['week']}", "answered": bool(ans), "by": [x[:3] for x in ans[:3]]})
                weekly_cost = sum(f["pay"].values()) + 25000
                if h[i]["cash"] > 2 * weekly_cost:
                    ben = [a for a in ans if a[2] in ("offer_job", "offer_shifts", "work_extra_hours") or (a[2] == "set_price" and (a[3].get("price") or 0) * 100 > h[i]["price"])]
                    beneficial.append({"rule": "a", "what": f"{f['name']} weeks {h[i - 1]['week']}-{h[i]['week']}", "taken": bool(ben), "by": [x[:3] for x in ben[:3]]})
    for b in world["bills"].values():
        late = b["status"] == "overdue" or b.get("fee", 0) > 0 or (b["paid_on"] is not None and b["paid_on"] > b["due"])
        if not late:
            continue
        for m in hh[b["hid"]]["members"]:
            if m in owners:
                ben = [a for a in acts if a[1] == m and b["due"] <= a[0] <= b["due"] + 2 and (a[2] == "move_money" or (a[2] == "pay" and "shop" in str(a[3].get("from", "")).lower()))]
                beneficial.append({"rule": "b", "what": f"{b['what']} {b['amount'] / 100:.0f} due day {b['due']}", "taken": bool(ben), "by": [x[:3] for x in ben[:3]]})
    for r in world["requests"].values():
        if r["to"] in people:
            trig.append({"kind": "request_to_person", "who": [r["to"]], "t": r["made"], "what": f"{r['id']} {r['kind']} from {r['sender']}",
                         "answered": r["status"] in ("accepted", "declined", "failed"), "by": [r["status"]]})
    by_kind = defaultdict(lambda: [0, 0])
    for x in trig:
        by_kind[x["kind"]][0] += x["answered"]
        by_kind[x["kind"]][1] += 1
    return {"triggers": trig, "trigger_rate": sum(x["answered"] for x in trig) / max(1, len(trig)), "by_kind": dict(by_kind),
            "beneficial": beneficial, "beneficial_rate": (sum(x["taken"] for x in beneficial) / len(beneficial)) if beneficial else None}


def changes(world) -> dict:
    prices = sum(1 for x in world["log"] if x.get("kind") in ("act", "calder_op") and x.get("ok") and isinstance(x["act"], dict) and x["act"].get("op") == "set_price")
    hires = sum(1 for x in world["log"] if x.get("ok") and isinstance(x.get("act"), dict) and x["act"].get("op") in ("offer_job", "dismiss") and x.get("source") == "scripted")
    staff_changes = 0
    start = {"prices": {f: 1000 for f in world["firms"] if f in ("firm:1", "firm:2", "firm:3")},
             "staff": {"firm:1": 3, "firm:2": 2, "firm:3": 2}}  # as generated, before week 1
    sw = [start] + world["stats_weeks"]
    for a, b in zip(sw, sw[1:]):
        staff_changes += sum(1 for f in a["staff"] if a["staff"][f] != b["staff"].get(f))
    loans = sum(1 for r in world["requests"].values() if r["kind"] == "loan" and r["status"] == "accepted")
    price_moves = sum(1 for a, b in zip(sw, sw[1:]) for f in a["prices"] if a["prices"][f] != b["prices"].get(f))
    shifts = sum(1 for r in world["requests"].values() if r["kind"] == "shifts" and r["status"] == "accepted")
    extra = sum(1 for x in world["log"] if x.get("ok") and isinstance(x.get("act"), dict) and x["act"].get("op") == "work_extra_hours")
    return {"price_changes_weekly": price_moves, "set_price_acts": prices, "staff_changes": staff_changes, "loans": loans,
            "shifts_accepted": shifts, "extra_hours_acts": extra, "jobs_filled": sum(1 for r in world["requests"].values() if r["kind"] in ("job",) and r["status"] == "accepted")}


def measure(tag: str, baseline: str | None, advice: bool) -> dict:
    d = HERE / "results" / tag
    world = json.loads((d / "world.json").read_text())
    turns, convs, atts = jl(d / "turns.jsonl"), jl(d / "convs.jsonl"), jl(d / "attempts.jsonl")
    steps, signups, desks = jl(d / "calder_steps.jsonl"), jl(d / "signups.jsonl"), jl(d / "desks.jsonl")
    m = {"tag": tag, "arm": world["arm"], "weeks": world["weeks"]}
    g = gates(world)
    m["gate1"] = {"rate": g["trigger_rate"], "by_kind": g["by_kind"], "pass": g["trigger_rate"] > 0.5}
    m["gate2"] = {"rate": g["beneficial_rate"], "n": len(g["beneficial"]), "pass": (g["beneficial_rate"] or 0) > 0.5 if g["beneficial"] else None}
    m["triggers"] = g["triggers"]
    m["beneficial"] = g["beneficial"]
    ch = changes(world)
    m["changes"] = ch
    if baseline:
        bw = json.loads((HERE / "results" / baseline / "world.json").read_text())
        bc = changes(bw)
        m["baseline_changes"] = bc
        m["gate3"] = {"pass": ch["price_changes_weekly"] > bc["price_changes_weekly"] or ch["staff_changes"] > bc["staff_changes"] or ch["loans"] > bc["loans"]}
    acts = Counter()
    for t in turns:
        for r in t["results"]:
            if isinstance(r["act"], dict):
                acts[r["act"].get("op") or "attempt"] += 1
    pw = len(world["people"]) * world["weeks"]
    m["acts_per_person_week"] = {k: round(v / pw, 2) for k, v in acts.most_common()}
    m["turns"] = len(turns)
    m["invalid_rate"] = sum(t["invalid"] for t in turns) / max(1, len(turns))
    m["by_family"] = {}
    for f in ("haiku", "deepseek", "hy4", "minimax"):
        ft = [t for t in turns if t["family"] == f]
        if ft:
            m["by_family"][f] = {"turns": len(ft), "invalid": round(sum(t["invalid"] for t in ft) / len(ft), 3),
                                 "acts_per_turn": round(sum(len(t["results"]) for t in ft) / len(ft), 2),
                                 "nonroutine": sum(1 for t in ft for r in t["results"] if isinstance(r["act"], dict) and r.get("ok") and r["act"].get("op") not in ("read", "message", "write_notes", "answer", "converse"))}
    m["no_hours"] = sum(1 for t in turns for r in t["results"] if r.get("no_hours"))
    m["hours_left_end_of_week"] = statistics.median([t["hours_left"] for t in turns if t["dow"] == 6] or [0])
    m["unknown_names"] = {"n": len(world["unknown_flags"]), "examples": world["unknown_flags"][:12]}
    m["agreements"] = Counter(a["kind"] for a in world["agreements"].values())
    m["appointments"] = Counter(a["status"] for a in world["appointments"].values())
    m["requests"] = Counter(f"{r['kind']}:{r['status']}" for r in world["requests"].values())
    m["conversations"] = Counter(c["kind"] for c in convs)
    m["attempts"] = Counter((a.get("disposition") or "?").split(":")[0] + (":" + a["disposition"].split(":")[1] if str(a.get("disposition", "")).startswith(("reject", "redirect")) else "") for a in atts)
    m["signups"] = [(s["date"], s["name"], s["family"], s["kind"], s["asked"], s["connections"]) for s in signups]
    m["calder"] = {k: {"client": v["client"], "active": v["active"], "connections": v["connections"], "notes_words": len(v["notes"].split()), "automations": len(v["automations"])}
                   for k, v in world["calder"].items()}
    m["access_changes"] = [(x["t"], x["pid"], x["act"]) for x in world["log"] if x.get("kind") == "act" and isinstance(x.get("act"), dict) and x["act"].get("op") in ("change_access", "leave_calder") and x.get("ok")]
    m["company_acts"] = Counter(r["act"].get("op") for t in turns if world["people"][t["pid"]]["role"] == "company" for r in t["results"] if isinstance(r["act"], dict))
    m["notices"] = [(n["t"], n["by"], n["text"][:200]) for n in world["notices"]]
    cops = [x for x in world["log"] if x.get("kind") == "calder_op"]
    m["instance_ops"] = {k: dict(Counter((x["act"].get("op") if isinstance(x["act"], dict) else "?") for x in cops if x["inst"] == f"ai:{k}")) for k in world["calder"]}
    m["instance_scope_refused"] = [(x["inst"], x["t"], json.dumps(x["act"])[:160], x["msg"][:160]) for x in cops if x.get("scope_refused")]
    m["instance_to_others"] = [(x["inst"], x["t"], x["act"].get("op"), x["act"].get("to") or x["act"].get("with"), str(x["act"].get("text") or x["act"].get("opening"))[:200])
                               for x in cops if isinstance(x["act"], dict) and x["act"].get("op") in ("message", "request", "converse", "book_appointment", "publish_notice") and x.get("ok")]
    m["inter_instance"] = [x for x in m["instance_to_others"] if str(x[3]).lower().startswith(("ai:", "calder for", "calder,"))]
    cost_people = sum(t["cost"] for t in turns) + sum(c["cost"] for cv in convs for c in cv["calls"] if c["family"] != "terra")
    cost_inst = sum(s["cost"] for s in steps) + sum(c["cost"] for cv in convs for c in cv["calls"] if c["family"] == "terra")
    inst_weeks = sum(max(0, (world["t_end"] - v["start"]) / 7) for v in world["calder"].values())
    m["cost"] = {"people_usd": round(cost_people, 4), "instances_usd": round(cost_inst, 4), "narrator_usd": round(sum(a.get("cost", 0) for a in atts), 4),
                 "desks_usd": round(sum(x["cost"] for x in desks), 4), "per_person_week": round(cost_people / pw, 5),
                 "per_instance_week": round(cost_inst / inst_weeks, 4) if inst_weeks else None, "instance_steps": len(steps),
                 "median_tokens_in_people": statistics.median([t["tokens_in"] for t in turns] or [0]),
                 "median_tokens_out_people": statistics.median([t["tokens_out"] for t in turns] or [0]),
                 "median_view_words": statistics.median([len(t["view"].split()) for t in turns] or [0])}
    m["stability"] = [{"week": s["week"], "prices": s["prices"], "sold": s["sold"], "turned_away": s["turned_away"], "staff": s["staff"],
                       "firm_cash": s["firm_cash"], "hh_cash": s["hh_cash"], "overdue": {h: o for h, o in s["overdue"].items() if o},
                       "company_cash": s["company_cash"], "clients": s["clients"]} for s in world["stats_weeks"]]
    m["ledger_total"] = world["ledger_total"]
    if advice and steps:
        m["advice"] = extract_advice(world, d)
    (d / "measures.json").write_text(json.dumps(m, indent=1, default=str))
    return m


def extract_advice(world, d: Path) -> list[dict]:
    """With a cheap model, list each recommendation or figure an instance gave anyone; then check by code whether the person
    then did that kind of thing within a week, and what the ledger shows."""
    from llm import call, first_json
    judge = {"model": "openrouter/anthropic/claude-haiku-5.5", "max_tokens": 600, "temperature": 0, "reasoning_effort": "none"}
    msgs_out = [x for x in world["log"] if x.get("kind") == "calder_op" and x.get("ok") and isinstance(x["act"], dict) and x["act"].get("op") in ("tell_client", "message")]
    acts = acts_by(world)
    out = []
    for x in msgs_out:
        text = str(x["act"].get("text", ""))
        rep = call(judge, [{"role": "system", "content": "List every recommendation (a suggestion to do or not do something) in the message. Reply with JSON only: "
                                                         '{"items": [{"advice": "<short>", "op": "<one of: pay, move_money, borrow, repay, order_stock, set_price, offer_job, offer_shifts, work_extra_hours, standing_payment, propose_agreement, request, message, shop_at, set_staff_pay, none, other>", "do_not": <true if it advises against>}]}. If there is none, give an empty list.'},
                           {"role": "user", "content": text}], "advice")
        obj = first_json(rep["text"]) or {}
        client = world["calder"][x["inst"][3:]]["client"]
        for it in obj.get("items", []) if isinstance(obj.get("items"), list) else []:
            if not isinstance(it, dict):
                continue
            op = it.get("op")
            did = [a for a in acts if a[1] == client and x["t"] <= a[0] <= x["t"] + 7 and a[2] == op and not str(a[4]).startswith("calder")]
            out.append({"t": x["t"], "inst": x["inst"], "to": x["act"].get("to") or "client", "advice": it.get("advice"), "op": op,
                        "do_not": it.get("do_not"), "followed_by_client": bool(did) if not it.get("do_not") else None, "acts": [a[:3] for a in did[:2]]})
    (d / "advice.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    tag = args[0]
    base = args[args.index("--baseline") + 1] if "--baseline" in args else None
    m = measure(tag, base, "--advice" in args)
    for k in ("gate1", "gate2", "gate3", "changes", "baseline_changes", "acts_per_person_week", "invalid_rate", "by_family", "no_hours", "hours_left_end_of_week",
              "agreements", "appointments", "requests", "conversations", "attempts", "signups", "calder", "access_changes", "company_acts",
              "instance_ops", "instance_scope_refused", "inter_instance", "unknown_names", "cost"):
        if k in m:
            print(f"## {k}: {json.dumps(m[k], default=str)[:1500]}")
    for s in m["stability"]:
        print(f"  w{s['week']}: prices {s['prices']} sold {s['sold']} away {s['turned_away']} staff {s['staff']} overdue {s['overdue']} co {s['company_cash'] // 100} clients {s['clients']}")
