"""Compact readable dump of results.json for review: python spikes/narrator/dump.py [ids]"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
def short(op):
    o = dict(op)
    k = o.pop("op")
    sub = o.pop("on_complete", None) or o.pop("on_accept", None)
    s = f"{k}({', '.join(f'{a}={v}' for a, v in o.items())})"
    if sub:
        s += " -> [" + "; ".join(short(x) for x in sub) + "]"
    return s



def main():
    results = json.loads((HERE / "results" / (sys.argv[2] if len(sys.argv) > 2 else "results.json")).read_text())
    only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1] != "all" else None

    for r in results:
        a = r["attempt"]
        if only and a["id"] not in only:
            continue
        f = r["lift"]["fields"] or {}
        print(f"== {a['id']} {a['category']} {a.get('pair','')} actor={a['actor']} commits={f.get('commits')} claims={f.get('claims')} addr={f.get('addressed_to_reader')}")
        for n, x in r["narrators"].items():
            if not x["parsed"]:
                print(f"  {n}: UNPARSED {str(x['raw'])[:200]}")
                continue
            print(f"  {n}: {x['reply']['ruling']}")
            for op in x["reply"]["operations"]:
                print(f"     {short(op)}")
            bad = [(v['path'], v['errors']) for v in x["validation"]["ops"] if v["errors"]]
            if bad:
                print(f"     INVALID: {bad}")
            if x["validation"]["ruling_flags"]:
                print(f"     FLAGS: {x['validation']['ruling_flags']}")



if __name__ == "__main__":
    main()
