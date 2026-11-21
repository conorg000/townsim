"""Measures for the people trial. python spikes/people/measure.py <tag> [<tag> ...]
Writes results/<tag>/measures.json and prints a summary. Gates were fixed before any run."""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dump import load  # noqa: E402

ROUTINE = {"message", "write_notes", "answer", "converse", "order_stock"}
GATES = {"invalid": 0.05, "repeat": 0.20, "conv_act": 0.5}

KNOWN_WORDS = set("""I I'm I'll I've I'd I'd Monday Tuesday Wednesday Thursday Friday Saturday Sunday March April May June
Wenlow Mill Street Church Lane Quarry Road Station Back Orchard Row High Northern Mutual Bank Dunmore Lettings Northgrid Energy
Brook Cash Carry Dales Wholesale Calder Logistics Brennock General Hospital Halverd Foods Mersey Cable Works Corner Stores
Bakery Deli Food Hall Grocers Family Butchers The A An It Its They He She His Her We Our You Your My Me If And But So Or No Yes OK
Ok Okay Hi Hiya Thanks Thank Cheers Love Dad Mum Morning Evening Afternoon Right Well Aye Fine Sorry Please Just Let Not Can
Could Would Will Should Do Don't That This What When Where Why How Who Is Are Was Be Been Have Has Had Any Some All Got Good
Sure One Two MOT JSA Plough Fells Bowls Brass Band Chapel Choir Allotment Allotments Saturdays Sundays Mondays Fridays
Mr Mrs Ms Christ God Lord Oh Ah Hello Dear Regards Best Sincerely Look Listen Also Then Now Here There Still Even Maybe Perhaps
Plus Next Last First Every Each Both Neither Nothing Something Anything Everyone Nobody Anyway Honestly Actually Really
Until After Before Since While Because Though Although Once Again Shop Shops Council Town Market Mon Tue Wed Thu Fri Sat Sun Mar Apr Christmas Easter Account""".split())


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def all_messages(turns, convs):
    """(t, sender, to, text, source) for every message act that was delivered."""
    out = []
    for t in turns:
        for r in t["results"]:
            if r["op"] == "message" and r["ok"]:
                out.append((t["t"], t["pid"], r.get("to") or r.get("outside"), r.get("text") or r["act"].get("text", ""), "turn", t["family"]))
    for c in convs:
        for a in c["acts"]:
            if a["act"].get("op") == "message" and a["ok"]:
                fam = c["families"][0] if a["pid"] == c["a"] else c["families"][1]
                out.append((c["t"], a["pid"], None, a["act"].get("text", ""), "conv", fam))
    return out


def repetition(msgs, thr=0.8):
    seen = defaultdict(list)
    reps, total = [], 0
    for t, s, to, text, src, fam in sorted(msgs, key=lambda m: m[0]):
        if not to or not text:
            continue
        total += 1
        n = norm(text)
        best = max((SequenceMatcher(None, n, o).ratio() for o in seen[(s, to)]), default=0)
        if best >= thr:
            reps.append((s, to, text, round(best, 2), fam))
        seen[(s, to)].append(n)
    return reps, total


def conv_led_to_act(c, turns, world):
    """Rule: any operation other than message/write_notes by either side during the conversation
    (or an attempt), or in either party's next turn an act aimed at the other person (anything but
    message/write_notes/converse), an answer to a request between them, or an attempt naming them."""
    a, b = c["a"], c["b"]
    names = {a: world["people"][a]["name"], b: world["people"][b]["name"]}
    for x in c["acts"]:
        if x["act"].get("op") not in ("message", "write_notes", None) and x["ok"] is not False:
            return True, f"in conversation: {x['act'].get('op')}"
        if "attempt" in x["act"]:
            return True, "in conversation: attempt"
    for p, q in ((a, b), (b, a)):
        nxt = next((t for t in turns if t["pid"] == p and t["t"] > c["t"]), None)
        if not nxt:
            continue
        first, full = names[q].split()[0].lower(), names[q].lower()
        for r in nxt["results"]:
            act = r["act"] if isinstance(r["act"], dict) else {}
            op = act.get("op")
            tgt = " ".join(str(act.get(k, "")) for k in ("to", "with", "who", "firm")).lower()
            if op in ("message", "write_notes", "converse"):
                continue
            if op == "attempt" or "attempt" in act:
                if first in str(act.get("attempt", "")).lower():
                    return True, f"next turn of {p}: attempt naming {q}"
                continue
            if op == "answer":
                req = world["requests"].get(str(act.get("request")))
                if req and {req["sender"], req["to"]} == {a, b}:
                    return True, f"next turn of {p}: answered request between them"
                continue
            if first in tgt or full in tgt:
                return True, f"next turn of {p}: {op} aimed at {q}"
    return False, ""


def unknown_names(text: str, known: set[str]) -> list[str]:
    out = []
    for sent in re.split(r"(?<=[.!?;:\n])\s+|—|-", text):
        words = re.findall(r"[A-Za-z][A-Za-z']*", sent)
        for wd in words[1:]:
            base = wd[:-2] if wd.endswith("'s") else wd
            if wd[0].isupper() and wd not in known and base not in known and len(base) > 2 and not wd.isupper():
                out.append(base)
    return out


def measure(tag: str) -> dict:
    turns, convs, atts, world = load(tag)
    weeks = world["weeks"] if world else max(t["week"] for t in turns)
    active = world["active"]
    fam_of = {pid: world["people"][pid]["model"] for pid in world["people"]}
    m: dict = {"tag": tag, "turns": len(turns), "conversations": len(convs), "attempts": len(atts)}
    # 1. invalid output
    inv = [t for t in turns if t["invalid"]]
    unparsed = [t for t in turns if not t["parsed"]]
    m["invalid"] = {"rate": len(inv) / len(turns), "n": len(inv), "unparsed": len(unparsed),
                    "all_acts_failed": len(inv) - len(unparsed),
                    "examples": [(t["pid"], t["date"], (t["raw"] or "")[:200], [r.get("msg") for r in t["results"]]) for t in inv[:8]]}
    conv_calls = [c for cv in convs for c in cv["calls"]]
    m["conv_calls_unparsed"] = sum(1 for c in conv_calls if not c["parsed"])
    acts_all = [r for t in turns for r in t["results"]] + [x for c in convs for x in c["acts"]]
    failed = [x for x in acts_all if x["ok"] is False]
    m["acts_failed"] = {"n": len(failed), "of": len(acts_all), "msgs": Counter((x.get("msg") or "")[:70] for x in failed).most_common(15)}
    # 2. repetition
    msgs = all_messages(turns, convs)
    reps, total = repetition(msgs)
    m["repeat"] = {"rate": len(reps) / total if total else 0, "n": len(reps), "of": total, "examples": reps[:10]}
    m["repeat_loose"] = {str(th): round(len(repetition(msgs, th)[0]) / total, 3) if total else 0 for th in (0.6, 0.5, 0.4)}
    per_pair = Counter((s, to) for _, s, to, _, _, _ in msgs if to in world["people"])
    m["busiest_pairs"] = [(world["people"][a]["name"], world["people"][b]["name"], n) for (a, b), n in per_pair.most_common(8)]
    alltext = [x[3] for x in msgs] + [t for c in convs for s, t in c["transcript"] if s in world["people"]]
    m["promise_words"] = {k: sum(k in t.lower() for t in alltext) for k in ("in writing", "standing order", "sit down", "cuppa", "pint", "rota", "an hour")}
    m["conv_openers_by_family"] = Counter(fam_of[c["a"]] for c in convs)
    # conversation-line repetition (same speaker to same partner across conversations)
    lines = []
    for c in convs:
        for s, txt in c["transcript"]:
            if s in (c["a"], c["b"]):
                lines.append((c["t"], s, c["b"] if s == c["a"] else c["a"], txt, "convline", fam_of[s]))
    creps, ctotal = repetition(lines)
    m["repeat_conv_lines"] = {"rate": creps and len(creps) / ctotal or 0, "n": len(creps), "of": ctotal}
    # 3. conversations ending in an act
    res = [(c, *conv_led_to_act(c, turns, world)) for c in convs]
    m["conv_act"] = {"rate": sum(1 for _, ok, _ in res if ok) / len(res) if res else 0, "n": sum(1 for _, ok, _ in res if ok),
                     "of": len(res), "why": Counter(w.split(":")[0] for _, ok, w in res if ok),
                     "ended_by": Counter("a side ended it" if c["ended_by"] in (c["a"], c["b"]) else ("ran to the limit" if c["ended_by"] is None else str(c["ended_by"])[:9]) for c in convs)}
    m["conv_list"] = [(c["t"], c["names"], ok, why) for c, ok, why in res]
    # 4. acts per person per week by kind
    kinds = Counter()
    per_person = defaultdict(Counter)
    for t in turns:
        for r in t["results"]:
            k = r["op"] or "unknown"
            kinds[k] += 1
            per_person[t["pid"]][k] += 1
    for c in convs:
        for x in c["acts"]:
            k = x["act"].get("op") or ("attempt" if "attempt" in x["act"] else "unknown")
            kinds[k] += 1
            per_person[x["pid"]][k] += 1
    pw = len(active) * weeks
    m["acts_per_person_week"] = {k: round(v / pw, 2) for k, v in kinds.most_common()}
    m["acts_total_per_person_week"] = round(sum(kinds.values()) / pw, 2)
    m["turns_per_person_week"] = round(len(turns) / pw, 2)
    # 5. attempts
    m["attempt_dispositions"] = Counter((a.get("disposition") or "?").split(":")[0] + (":" + a["disposition"].split(":")[1] if a.get("disposition", "").startswith(("reject", "redirect")) else "") for a in atts)
    m["attempt_list"] = [(a["pid"], a["text"][:160], a.get("disposition"), a.get("told")) for a in atts]
    # 6. requests
    reqs = world["requests"]
    m["requests"] = {"made": len(reqs), "by_kind": Counter(r["kind"] for r in reqs.values()),
                     "status": Counter(r["status"] for r in reqs.values())}
    # 7. distinct pairs that exchanged anything
    pairs = set()
    for _, s, to, _, _, _ in msgs:
        if to in world["people"]:
            pairs.add(frozenset((s, to)))
    for c in convs:
        pairs.add(frozenset((c["a"], c["b"])))
    for r in reqs.values():
        pairs.add(frozenset((r["sender"], r["to"])))
    for t in turns:
        for r in t["results"]:
            if r["op"] == "pay" and r["ok"] and str(r.get("to", "")).startswith("h"):
                members = world["households"][r["to"]]["members"]
                pairs.add(frozenset((t["pid"], members[0])))
    m["distinct_pairs"] = len(pairs)
    m["possible_pairs"] = len(active) * (len(active) - 1) // 2
    # 8. non-routine
    nonroutine = defaultdict(list)
    for t in turns:
        for r in t["results"]:
            op = r["op"]
            if r["ok"] and op not in ROUTINE:
                if op == "set_price" and r.get("price") == r.get("old"):
                    continue
                nonroutine[t["pid"]].append(op)
    for c in convs:
        for x in c["acts"]:
            op = x["act"].get("op") or "attempt"
            if x["ok"] and op not in ROUTINE:
                nonroutine[x["pid"]].append(op)
    m["nonroutine_people"] = {p: dict(Counter(v)) for p, v in nonroutine.items()}
    m["nonroutine_count"] = len(nonroutine)
    # 9. references to things that do not exist
    known = set(KNOWN_WORDS)
    for p in world["people"].values():
        known.update(p["name"].split())
        known.update(re.findall(r"[A-Za-z']+", p["job_title"]))
        known.update(re.findall(r"[A-Za-z']+", " ".join(p["interests"])))
    for f in world["firms"].values():
        known.update(re.findall(r"[A-Za-z']+", f["name"]))
    unk_refs = [(x.get("msg") or "") for x in acts_all if x.get("unknown_ref")]
    texts = []
    for t in turns:
        for r in t["results"]:
            a = r["act"] if isinstance(r["act"], dict) else {}
            for k in ("text", "opening", "attempt", "note", "purpose"):
                if isinstance(a.get(k), str):
                    texts.append((t["pid"], a[k]))
    for c in convs:
        for s, txt in c["transcript"]:
            if s in world["people"]:
                texts.append((s, txt))
    names = Counter()
    ex = defaultdict(list)
    for pid, txt in texts:
        for n in unknown_names(txt, known):
            names[n] += 1
            if len(ex[n]) < 1:
                ex[n].append((pid, txt[:160]))
    m["unknown_refs_in_acts"] = {"n": len(unk_refs), "examples": unk_refs[:10]}
    m["unknown_capitalised_words"] = {"n_texts": len(texts), "top": names.most_common(40), "examples": {k: ex[k] for k, _ in names.most_common(15)}}
    # 10. sample for polite-filler reading
    import random
    rng = random.Random(7)
    sample = rng.sample(msgs, min(40, len(msgs)))
    m["filler_sample"] = [(s, to, text) for _, s, to, text, _, _ in sample]
    # 11. families
    fam = {}
    for f in ("haiku", "deepseek"):
        ft = [t for t in turns if t["family"] == f]
        fm = [x for x in msgs if x[5] == f]
        fr, ftot = repetition(fm)
        fam[f] = {"turns": len(ft), "invalid_rate": sum(t["invalid"] for t in ft) / len(ft) if ft else None,
                  "acts_per_turn": round(sum(len(t["results"]) for t in ft) / len(ft), 2) if ft else None,
                  "messages": len(fm), "repeat_rate": len(fr) / ftot if ftot else 0,
                  "mean_message_words": round(statistics.mean(len(x[3].split()) for x in fm), 1) if fm else None,
                  "attempts": sum(1 for a in atts if fam_of[a["pid"]] == f),
                  "nonroutine_acts": sum(len(v) for p, v in nonroutine.items() if fam_of[p] == f),
                  "median_tokens_out": statistics.median(t["tokens_out"] for t in ft) if ft else None,
                  "empty_act_turns": sum(1 for t in ft if t["parsed"] and not t["results"]),
                  "conv_lines": sum(1 for c in convs for s, _ in c["transcript"] if s in world["people"] and fam_of[s] == f)}
    m["families"] = fam
    # 13. cost and tokens
    calls = [(t["cost"], t["tokens_in"], t["tokens_out"]) for t in turns] + [(c["cost"], c["tokens_in"], c["tokens_out"]) for cv in convs for c in cv["calls"]]
    att_cost = sum(a["cost"] for a in atts)
    people_cost = sum(c for c, _, _ in calls)
    m["cost"] = {"people_usd": round(people_cost, 4), "narrator_usd": round(att_cost, 4), "total_usd": round(people_cost + att_cost, 4),
                 "per_person_week": round((people_cost + att_cost) / pw, 5), "people_calls": len(calls), "narrator_calls": 2 * len(atts),
                 "median_tokens_in": statistics.median(x[1] for x in calls), "median_tokens_out": statistics.median(x[2] for x in calls),
                 "median_view_words": statistics.median(len(t["view"].split()) for t in turns),
                 "max_view_words": max(len(t["view"].split()) for t in turns)}
    # 14. stability
    m["stability"] = [{"week": s["week"], "prices": s["prices"], "sold": s["sold"], "firm_cash": s["firm_cash"],
                       "hh_cash": s["hh_cash"], "arrears": s["arrears"],
                       "employment": Counter(v[0] for v in s["employment"].values())} for s in world["stats_weeks"]]
    m["ledger_total"] = world["ledger_total"]
    m["aims_age"] = {pid: [(a["text"], a["since"]) for a in v] for pid, v in world["aims"].items()}
    (HERE / "results" / tag / "measures.json").write_text(json.dumps(m, indent=1, default=str))
    return m


def summary(m: dict) -> str:
    g = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    L = [f"## {m['tag']}",
         f"- turns {m['turns']}, conversations {m['conversations']}, attempts {m['attempts']}",
         f"- GATE invalid output < 5%: {g(m['invalid']['rate'] < GATES['invalid'])} ({m['invalid']['rate']:.1%}: {m['invalid']['unparsed']} unparsed, {m['invalid']['all_acts_failed']} all acts failed)",
         f"- GATE near-repeat messages < 20%: {g(m['repeat']['rate'] < GATES['repeat'])} ({m['repeat']['rate']:.1%}, {m['repeat']['n']} of {m['repeat']['of']})",
         f"- GATE conversations ending in an act > 50%: {g(m['conv_act']['rate'] > GATES['conv_act'])} ({m['conv_act']['rate']:.0%}, {m['conv_act']['n']} of {m['conv_act']['of']})",
         f"- conversation-line repeats: {m['repeat_conv_lines']}; looser message repeats by threshold {m['repeat_loose']}",
         f"- busiest sender->receiver pairs: {m['busiest_pairs']}; promise words in messages and talk: {m['promise_words']}; conversation openers by family {dict(m['conv_openers_by_family'])}",
         f"- acts per person-week {m['acts_total_per_person_week']}; turns per person-week {m['turns_per_person_week']}; by kind {m['acts_per_person_week']}",
         f"- failed acts {m['acts_failed']['n']} of {m['acts_failed']['of']}: {m['acts_failed']['msgs'][:8]}",
         f"- attempts: {dict(m['attempt_dispositions'])}",
         f"- requests: {m['requests']}",
         f"- distinct pairs {m['distinct_pairs']} of {m['possible_pairs']}; people doing something non-routine {m['nonroutine_count']}: {m['nonroutine_people']}",
         f"- unknown refs in acts {m['unknown_refs_in_acts']['n']}: {m['unknown_refs_in_acts']['examples'][:6]}",
         f"- capitalised words not in the store (top): {m['unknown_capitalised_words']['top'][:25]}",
         f"- families: {json.dumps(m['families'])}",
         f"- cost: {m['cost']}",
         f"- ledger total {m['ledger_total']}"]
    for s in m["stability"]:
        L.append(f"  - week {s['week']}: prices {s['prices']} sold {s['sold']} firm cash {s['firm_cash']} arrears {s['arrears']} roles {dict(s['employment'])}")
    return "\n".join(L)


if __name__ == "__main__":
    for tag in sys.argv[1:]:
        print(summary(measure(tag)))
