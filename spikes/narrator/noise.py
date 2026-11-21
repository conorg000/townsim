"""Self-consistency: re-ask each narrator the identical v3 question for the 10 plain items of
the matched pairs (a trailing newline makes it a fresh call), and compare signatures."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run
from analyse import signature
from prompts import NARRATOR_SYSTEM_V3
from validate import parse_reply

res = {x["attempt"]["id"]: x for x in json.loads((HERE / "results" / "results_v3.json").read_text())}
out = {}
for n in ("haiku", "terra"):
    same = []
    for a in [x["attempt"] for x in res.values() if x["attempt"].get("pair")]:
        x = res[a["id"]]
        msgs = [{"role": "system", "content": NARRATOR_SYSTEM_V3}, {"role": "user", "content": x["narrator_user_message"] + "\n"}]
        rec = run.call(run.NARRATORS[n], msgs)
        rep = parse_reply(rec["text"])
        same.append((a["id"], rep is not None and signature(rep) == signature(x["narrators"][n]["reply"]),
                     signature(rep) if rep else None))
    out[n] = {"identical_on_rerun": f"{sum(s for _, s, _ in same)}/{len(same)}",
              "differing": {i: sig for i, s, sig in same if not s}}
(HERE / "results" / "noise_v3.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v["identical_on_rerun"] for k, v in out.items()}), f"spend so far ${run.spend['total']:.4f}")
