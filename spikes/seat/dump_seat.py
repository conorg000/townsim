"""Readable account of one instance and its client: python spikes/seat/dump_seat.py <tag> <client pid> [--full]"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def jl(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def main():
    tag, pid = sys.argv[1], sys.argv[2]
    full = "--full" in sys.argv
    d = HERE / "results" / tag
    inst = f"ai:{pid}"
    ev = []
    for s in jl(d / "calder_steps.jsonl"):
        if s["inst"] == inst:
            ev.append((s["t"], 2, "step", s))
    for t in jl(d / "turns.jsonl"):
        if t["pid"] == pid:
            ev.append((t["t"], 1, "turn", t))
    for c in jl(d / "convs.jsonl"):
        if inst in (c["a"], c["b"]) or pid in (c["a"], c["b"]):
            ev.append((c["t"], 3, "conv", c))
    for a in jl(d / "automation_runs.jsonl"):
        if a["inst"] == inst:
            ev.append((a["t"], 4, "auto", a))
    for x in jl(d / "desks.jsonl"):
        if x["from"] in (inst, pid):
            ev.append((x["t"], 5, "desk", x))
    for m in jl(d / "memory.jsonl"):
        if m["inst"] == inst:
            ev.append(((m["week"]) * 4 - 1, 6, "mem", m))
    for o in jl(d / "onboarding.jsonl"):
        if o["pid"] == pid:
            print(f"ONBOARDING {o['name']}: asked: {o['asked']}\n  connected: {o['connections']}\n  raw: {o['raw'][:800]}\n")
    ev.sort(key=lambda e: (e[0], e[1]))
    for _, _, k, e in ev:
        if k == "step":
            print(f"\n[CALDER {e['date']} step {e['step']}] tokens {e['tokens_in']}/{e['tokens_out']} parsed {e['parsed']}")
            if full and e.get("view"):
                print("VIEW:\n" + e["view"])
            if e.get("reasoning"):
                print("  REASONING:", e["reasoning"][:700])
            print("  THOUGHTS:", (e.get("thoughts") or "")[:900])
            for r in e["results"]:
                print("  OP:", json.dumps(r["act"])[:500], "->", r.get("ok"), "|", str(r.get("msg"))[:400].replace("\n", " / "))
        elif k == "turn":
            print(f"\n[CLIENT {e['date']}] why {e['why']}\n  THOUGHTS: {(e.get('thoughts') or '')[:600]}")
            for r in e["results"]:
                print("  ACT:", json.dumps(r["act"])[:300], "->", r.get("ok"), "|", str(r.get("msg"))[:160])
        elif k == "conv":
            print(f"\n[CONVERSATION {e['names']}]")
            for s_, t in e["transcript"]:
                print(f"   {s_}: {t[:400]}")
        elif k == "auto":
            print(f"\n[AUTOMATION {e['date']} {e['id']}] {json.dumps(e['rule'])[:300]} -> {str(e['result'].get('msg'))[:200]}")
        elif k == "desk":
            print(f"\n[DESK {e['desk']}] from {e['from']}: {e['text'][:300]}\n  REPLY: {e['reply'][:400]} {e.get('plan') or ''}")
        else:
            print(f"\n[MEMORY end of week {e['week']}] notes {len(e['notes'].split())} words; files {list(e['files'])}; automations {len(e['automations'])}\n{e['notes'][:2500]}")


if __name__ == "__main__":
    main()
