"""Readable dump of a run: python spikes/people/dump.py <tag> [pid ...] [--views]"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(tag):
    d = HERE / "results" / tag
    turns = [json.loads(l) for l in (d / "turns.jsonl").read_text().splitlines() if l.strip()]
    convs = [json.loads(l) for l in (d / "convs.jsonl").read_text().splitlines() if l.strip()]
    atts = [json.loads(l) for l in (d / "attempts.jsonl").read_text().splitlines() if l.strip()]
    world = json.loads((d / "world.json").read_text()) if (d / "world.json").exists() else None
    for t in turns:  # an act a model wrote as something other than an object
        for r in t["results"]:
            if not isinstance(r["act"], dict):
                r["act"] = {"not_an_object": r["act"]}
    for c in convs:
        for x in c["acts"]:
            if not isinstance(x["act"], dict):
                x["act"] = {"not_an_object": x["act"]}
    return turns, convs, atts, world


def main():
    tag = sys.argv[1]
    pids = [a for a in sys.argv[2:] if not a.startswith("--")]
    views = "--views" in sys.argv
    turns, convs, atts, world = load(tag)
    events = []
    for t in turns:
        events.append((t["t"], 0, "turn", t))
    for c in convs:
        events.append((c["t"], 1, "conv", c))
    for a in atts:
        events.append((a["t"], 2, "att", a))
    events.sort(key=lambda e: (e[0], e[1]))
    for _, _, k, e in events:
        if k == "turn":
            if pids and e["pid"] not in pids:
                continue
            print(f"\n=== {e['date']} | {e['name']} ({e['pid']}, {e['family']}) | why: {e['why']} | parsed {e['parsed']} | invalid {e['invalid']}")
            if views:
                print(e["view"])
                print("--- reply:")
                print(e["raw"])
            else:
                print("THOUGHTS:", e.get("thoughts"))
                for r in e["results"]:
                    print("  ACT:", json.dumps(r["act"])[:400], "->", r["ok"], "|", r.get("msg"))
                print("  AIMS:", e.get("aims"))
        elif k == "conv":
            if pids and e["a"] not in pids and e["b"] not in pids:
                continue
            print(f"\n*** CONVERSATION {e['names'][0]} -> {e['names'][1]} (ended by {e['ended_by']})")
            for s, t in e["transcript"]:
                print(f"   {s}: {t}")
            for a in e["acts"]:
                print("   CONV-ACT:", a["pid"], json.dumps(a["act"])[:300], "->", a["ok"], a.get("msg"))
        else:
            if pids and e["pid"] not in pids:
                continue
            print(f"\n+++ ATTEMPT by {e['pid']}: {e['text']}\n    -> {e.get('disposition')} | told: {e.get('told')}")


if __name__ == "__main__":
    main()
