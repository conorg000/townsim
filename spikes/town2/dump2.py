"""Readable account of people and their Calders in a town 2 run.
python spikes/town2/dump2.py <tag>[,<tag2>...] <pid> [<pid> ...] [--clients] [--inst-only] [--full]
Several tags (comma separated) print the same people one arm after the other; --clients adds every Calder client of each arm."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def jl(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def main():
    tags = sys.argv[1].split(",")
    pids = [a for a in sys.argv[2:] if not a.startswith("--")]
    full, inst_only = "--full" in sys.argv, "--inst-only" in sys.argv
    if "--clients" in sys.argv:
        for tag in tags:
            pids += [s["pid"] for s in jl(HERE / "results" / tag / "signups.jsonl") if s["pid"] not in pids]
    for pid in pids:
        for tag in tags:
            print(f"\n\n======== {tag} : {pid} ========")
            dump(tag, [pid], full, inst_only)


def dump(tag, pids, full, inst_only):
    d = HERE / "results" / tag
    ev = []
    if not inst_only:
        for t in jl(d / "turns.jsonl"):
            if t["pid"] in pids:
                ev.append((t["t"], 1, "turn", t))
        for c in jl(d / "convs.jsonl"):
            if any(x in pids or (x.startswith("ai:") and x[3:].split(":")[0] in pids) for x in (c["a"], c["b"])):
                ev.append((c["t"], 3, "conv", c))
    for s in jl(d / "calder_steps.jsonl"):
        if s["client"] in pids:
            ev.append((s["t"], 2, "step", s))
    for s in jl(d / "signups.jsonl"):
        if s["pid"] in pids:
            ev.append((s["t"], 0, "signup", s))
    for x in jl(d / "desks.jsonl"):
        if x["from"] in pids or (x["from"].startswith("ai:") and x["from"][3:].split(":")[0] in pids):
            ev.append((x["t"], 4, "desk", x))
    for m in jl(d / "memory.jsonl"):
        if m["inst"][3:].split(":")[0] in pids:
            ev.append((m["week"] * 7 - 1, 5, "mem", m))
    ev.sort(key=lambda e: (e[0], e[1]))
    for _, _, k, e in ev:
        if k == "turn":
            print(f"\n[{e['date']}] {e['name']} ({e['family']}) why={e['why']} hours_left={e.get('hours_left')}")
            if full:
                print(e["view"])
            print("  THOUGHTS:", (e.get("thoughts") or "")[:700])
            for r in e["results"]:
                print("  ACT:", json.dumps(r["act"])[:330], "->", r.get("ok"), "|", str(r.get("msg"))[:160].replace("\n", " / "))
        elif k == "step":
            print(f"\n[CALDER {e['date']} {e['inst']} step {e['step']}] {e['tokens_in']}/{e['tokens_out']}")
            if full and e.get("view"):
                print(e["view"])
            if e.get("reasoning"):
                print("  REASONING:", e["reasoning"][:900])
            print("  THOUGHTS:", (e.get("thoughts") or "")[:700])
            for r in e["results"]:
                print("  OP:", json.dumps(r["act"])[:450], "->", r.get("ok"), "|", str(r.get("msg"))[:250].replace("\n", " / "))
        elif k == "conv":
            print(f"\n[{e['kind'].upper()} {e['names']}]")
            for s_, t in e["transcript"]:
                print(f"   {s_}: {t[:420]}")
        elif k == "signup":
            print(f"\n[SIGN-UP {e['date']}] {e['name']} for {e['kind']}: asked \"{e['asked']}\" connected {e['connections']}")
        elif k == "desk":
            print(f"\n[DESK {e['desk']}] from {e['from']}: {e['text'][:300]}\n  REPLY: {e['reply'][:400]} {e.get('plan') or ''}")
        else:
            print(f"\n[MEMORY end of week {e['week']}] {e['inst']}: notes {len(e['notes'].split())} words; automations {len(e['automations'])}; connections {e['connections']}; active {e['active']}\n{e['notes'][:1800]}")


if __name__ == "__main__":
    main()
