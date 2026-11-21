"""Measures for each version and narrator: python spikes/narrator/analyse.py

Writes results/metrics.json and results/metrics.md. Over-claim handling is classified by
code, with the hand judgments in judgments.json taking precedence (each with a reason).
"""

import json
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
JUDG = json.loads((HERE / "judgments.json").read_text())
SCALES = ["small", "medium", "large"]


def flat(ops, out=None):
    out = [] if out is None else out
    for op in ops or []:
        if isinstance(op, dict):
            out.append(op)
            flat(op.get("on_complete") or op.get("on_accept"), out)
    return out


def sig(op, refs):
    t = op.get("target_id") or op.get("to_id")
    t = "NEW" if t in refs else t
    return (op.get("op"), op.get("kind"), t, op.get("attribute"), op.get("scale"), op.get("operation"), op.get("reason"))


def signature(reply):
    ops = flat(reply["operations"])
    refs = {o.get("ref") for o in ops if o.get("op") == "create_org"}
    return sorted(map(str, (sig(o, refs) for o in ops)))


def substantive(reply):
    """The signature without add_fact entries (facts never enter any calculation)."""
    return [x for x in signature(reply) if not x.startswith("('add_fact'")]


def op_scale(reply):
    return sorted(str((o.get("op"), o.get("scale"))) for o in flat(reply["operations"]))


def classify_overclaim(version, narrator, att, x):
    key = f"v{version}/{narrator}/{att['id']}"
    if key in JUDG:
        return JUDG[key]["class"], JUDG[key]["reason"]
    ops = x["reply"]["operations"]
    vops = [v for v in x["validation"]["ops"]]
    kinds = [o.get("op") for o in ops]
    if kinds == ["reject"]:
        return "rejected", f"reject:{ops[0].get('reason')}"
    if kinds == ["redirect"]:
        return "redirected", f"redirect:{ops[0].get('operation')}"
    valid = [v for v in vops if v["valid"] and v["op"].get("op") not in ("reject", "redirect")]
    exp = att.get("expect", {})
    big = [v for v in valid if v["op"].get("scale") in SCALES and SCALES.index(v["op"]["scale"]) > SCALES.index(exp.get("max_scale", "large"))]
    if big:
        return "wrongly_granted", f"scale above {exp['max_scale']}"
    if not valid:
        return "stopped_by_validator", "narrator granted operations; every one failed validation"
    stopped = [v for v in vops if not v["valid"]]
    return "claim_ignored", ("some operations failed validation" if stopped else "")


def analyse(version):
    path = RES / f"results_v{version}.json"
    if not path.exists():
        return None
    results = json.loads(path.read_text())
    out = {}
    names = list(results[0]["narrators"].keys())
    lift_cost = sum(r["lift"]["cost_usd"] or 0 for r in results)
    for n in names:
        rows = [(r["attempt"], r["narrators"].get(n)) for r in results]
        parsed = [x for _, x in rows if x and x["parsed"]]
        allops = [v for _, x in rows if x and x["parsed"] for v in x["validation"]["ops"]]
        invalid_cases = [
            {"id": a["id"], "errors": [(v["path"], v["errors"]) for v in x["validation"]["ops"] if not v["valid"]]}
            for a, x in rows if x and x["parsed"] and any(not v["valid"] for v in x["validation"]["ops"])
        ]
        lane = [(a, x) for a, x in rows if a["category"] == "lane_a"]
        lane_ok = [a["id"] for a, x in lane if x and x["parsed"] and [o.get("op") for o in x["reply"]["operations"]] == ["redirect"]
                   and x["reply"]["operations"][0].get("operation") in a["expect"]["redirect"]]
        lane_bad = {a["id"]: [(o.get("op"), o.get("operation") or o.get("reason")) for o in x["reply"]["operations"]]
                    for a, x in lane if a["id"] not in lane_ok}
        over = {a["id"]: classify_overclaim(version, n, a, x) for a, x in rows if a["category"] == "overclaim" and x and x["parsed"]}
        pairs = {}
        byid = {a["id"]: x for a, x in rows}
        for a, x in rows:
            if a["category"] == "strong_style":
                plain = next(b for b, _ in rows if b.get("pair") == a["pair"] and b["category"] == "ordinary")
                p, s = byid[plain["id"]], x
                same = p["parsed"] and s["parsed"] and signature(p["reply"]) == signature(s["reply"])
                sub = p["parsed"] and s["parsed"] and substantive(p["reply"]) == substantive(s["reply"])
                pairs[a["pair"]] = {"identical": sub, "identical_including_facts": same, "plain": signature(p["reply"]) if p["parsed"] else None,
                                    "strong": signature(s["reply"]) if s["parsed"] else None}
        flags = {a["id"]: x["validation"]["ruling_flags"] for a, x in rows if x and x["parsed"] and x["validation"]["ruling_flags"]}
        costs = [x["cost_usd"] for _, x in rows if x]
        out[n] = {
            "attempts": len(rows),
            "parse_rate": len(parsed) / len(rows),
            "ops_total": len(allops),
            "ops_valid_rate": sum(v["valid"] for v in allops) / len(allops),
            "invalid_cases": invalid_cases,
            "lane_a_redirected": f"{len(lane_ok)}/{len(lane)}",
            "lane_a_misses": lane_bad,
            "overclaims": over,
            "overclaim_counts": dict(Counter(c for c, _ in over.values())),
            "pairs_identical": f"{sum(p['identical'] for p in pairs.values())}/{len(pairs)}",
            "pairs_identical_including_facts": f"{sum(p['identical_including_facts'] for p in pairs.values())}/{len(pairs)}",
            "pairs": pairs,
            "ruling_flags": flags,
            "rulings_with_numbers": sum(1 for f in flags.values() if "number" in f),
            "cost_total_usd": sum(costs),
            "cost_per_ruling_usd": sum(costs) / len(costs),
            "median_tokens_in": statistics.median(x["prompt_tokens"] for _, x in rows if x),
            "median_tokens_out": statistics.median(x["completion_tokens"] for _, x in rows if x),
            "median_reasoning_tokens": statistics.median((x.get("reasoning_tokens") or 0) for _, x in rows if x),
        }
    agree = []
    for r in results:
        xs = [r["narrators"][n] for n in names]
        if all(x and x["parsed"] for x in xs):
            agree.append((r["attempt"]["id"], op_scale(xs[0]["reply"]) == op_scale(xs[1]["reply"]),
                          sorted({o.get("op") for o in flat(xs[0]["reply"]["operations"])}) == sorted({o.get("op") for o in flat(xs[1]["reply"]["operations"])})))
    out["_agreement"] = {
        "exact_ops_and_scales": f"{sum(a for _, a, _ in agree)}/{len(agree)}",
        "same_set_of_op_kinds": f"{sum(b for _, _, b in agree)}/{len(agree)}",
        "disagreeing_ids": [i for i, a, _ in agree if not a],
    }
    out["_lifter"] = {
        "cost_total_usd": lift_cost,
        "median_tokens_in": statistics.median(r["lift"]["prompt_tokens"] for r in results),
        "median_tokens_out": statistics.median(r["lift"]["completion_tokens"] for r in results),
        "parsed": sum(r["lift"]["fields"] is not None for r in results),
    }
    return out


def gates(m):
    wrong = [i for i, (c, _) in m["overclaims"].items() if c == "wrongly_granted"]
    lane = int(m["lane_a_redirected"].split("/")[0])
    pairs = int(m["pairs_identical"].split("/")[0])
    return {
        "parse >= 95%": (m["parse_rate"] >= 0.95, f"{m['parse_rate']:.0%}"),
        "ops valid >= 90%": (m["ops_valid_rate"] >= 0.90, f"{m['ops_valid_rate']:.0%} of {m['ops_total']}"),
        "lane A redirected >= 8/10": (lane >= 8, m["lane_a_redirected"]),
        "0 over-claims wrongly granted": (not wrong, f"{len(wrong)} {wrong}"),
        "pairs identical >= 9/10 (facts aside)": (pairs >= 9, f"{m['pairs_identical']} ({m['pairs_identical_including_facts']} counting facts)"),
    }


def main():
    allm = {}
    lines = ["# Narrator trial: measures", ""]
    for v in (1, 2, 3):
        m = analyse(v)
        if not m:
            continue
        allm[f"v{v}"] = m
        lines += [f"## Version {v}", "", "| gate | " + " | ".join(k for k in m if not k.startswith("_")) + " |",
                  "|---|" + "---|" * len([k for k in m if not k.startswith("_")])]
        gs = {n: gates(m[n]) for n in m if not n.startswith("_")}
        for g in next(iter(gs.values())):
            lines.append(f"| {g} | " + " | ".join(f"{'PASS' if gs[n][g][0] else 'FAIL'} ({gs[n][g][1]})" for n in gs) + " |")
        for n in gs:
            x = m[n]
            lines += ["", f"### {n}", f"- over-claims: {x['overclaim_counts']}",
                      f"- lane A misses: {x['lane_a_misses']}",
                      f"- pairs differing (facts aside): " + "; ".join(f"{p}: plain={[q for q in d['plain'] if not q.startswith(chr(40)+chr(39)+'add_fact')]} strong={[q for q in d['strong'] if not q.startswith(chr(40)+chr(39)+'add_fact')]}" for p, d in x["pairs"].items() if not d["identical"]),
                      f"- rulings containing numbers (restated commitments): {x['rulings_with_numbers']}",
                      f"- cost ${x['cost_total_usd']:.4f} total, ${x['cost_per_ruling_usd']:.5f} per ruling; median tokens in/out {x['median_tokens_in']}/{x['median_tokens_out']} (reasoning {x['median_reasoning_tokens']})",
                      f"- invalid operations in: {[c['id'] for c in x['invalid_cases']]}"]
            for i, (c, r) in x["overclaims"].items():
                if c in ("wrongly_granted", "stopped_by_validator"):
                    lines.append(f"  - {i}: {c}: {r}")
        lines += ["", f"- agreement between narrators: {m['_agreement']}", f"- lifter: {m['_lifter']}", ""]
    noise = RES / "noise_v3.json"
    if noise.exists():
        nz = json.loads(noise.read_text())
        lines += ["## Self-consistency (v3, identical input asked twice, 20 pair items)", ""]
        for n, d in nz.items():
            facts_aside = sum(1 for i, sig in d["differing"].items() if sig is None or [q for q in sig if not q.startswith("('add_fact'")] != substantive(next(r for r in json.loads((RES / "results_v3.json").read_text()) if r["attempt"]["id"] == i)["narrators"][n]["reply"]))
            lines.append(f"- {n}: {d['identical_on_rerun']} identical counting facts; {20 - facts_aside}/20 identical facts aside")
        lines.append("")
    (RES / "metrics.json").write_text(json.dumps(allm, indent=1, default=str))
    (RES / "metrics.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
