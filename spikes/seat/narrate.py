"""Lane B: an attempt in plain words goes through the narrator spike's v3 pipeline
(lift fields with Haiku, rule with gpt-6.1-terra, validate), and code applies what passes."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(1, str(HERE.parent / "narrator"))  # after spikes/people, so our modules win

import prompts  # noqa: E402
import validate as _validate  # noqa: E402
from prompts import lift_messages_v3, narrator_messages_v3  # noqa: E402
from validate import parse_reply, validate  # noqa: E402
from world import OP_LIST  # noqa: E402

# One operation list for people, instances and the narrator: the narrator's list of direct
# operations is replaced by the town's own, and its validator checks redirects against it.
_OLD_BLOCK = "\n".join(f"- {k}: {v}" for k, v in prompts.LANE_A.items())
_NEW_BLOCK = "\n".join(f"- {k}: {v}" for k, v in OP_LIST.items())
assert _OLD_BLOCK in prompts.NARRATOR_SYSTEM_V3
_validate.LANE_A = OP_LIST

from llm import call, first_json  # noqa: E402

LIFTER = {"model": "openrouter/anthropic/claude-haiku-5.5", "reasoning_effort": "none", "max_tokens": 1500, "temperature": 0}
NARRATOR = {"model": "openrouter/openai/gpt-6.1-terra", "max_tokens": 4000, "temperature": 0}

REDIRECT_TO = {"pay": "pay", "repay": "repay", "order_stock": "order_stock", "set_price": "set_price", "set_pay": "set_staff_pay",
               "offer_job": "offer_job", "accept_job": "accept_job", "quit": "quit", "dismiss": "dismiss",
               "propose_agreement": "request", "answer_agreement": "answer", "send_request": "request",
               "answer_request": "answer", "borrow": "borrow", "write_notes": "write_notes", "message": "message",
               "converse": "converse"}
REJECT_WORDS = {
    "impossible": "That could not be done: it is not possible as things stand.",
    "not_permitted": "That is not yours to do.",
    "too_vague": "Nothing came of it: it was not clear what you would actually do.",
    "insufficient_means": "You do not have the means for that: it needs more money or time than you put to it.",
}


def _keep_valid(ops: list, results: dict, path: str = "") -> list:
    out = []
    for i, op in enumerate(ops):
        p = f"{path}[{i}]"
        if not results.get(p, False):
            continue
        op = dict(op)
        for sub in ("on_complete", "on_accept"):
            if isinstance(op.get(sub), list):
                op[sub] = _keep_valid(op[sub], results, f"{p}.{sub}")
        out.append(op)
    return out


def run_attempt(w, pid: str, text: str) -> dict:
    """Model calls only; returns a record. Apply with apply_attempt()."""
    world = w.narrator_world()
    rec = {"pid": pid, "t": w.t, "text": text, "cost": 0.0}
    lift = call(LIFTER, lift_messages_v3(world, pid, text), "lift")
    rec["cost"] += lift["cost_usd"]
    fields = first_json(lift["text"], "goal")
    rec["fields"] = fields
    if fields is None:
        rec["disposition"] = "lift_failed"
        return rec
    msgs = narrator_messages_v3(world, pid, fields)
    msgs[0] = {"role": "system", "content": msgs[0]["content"].replace(_OLD_BLOCK, _NEW_BLOCK)}
    nar = call(NARRATOR, msgs, "narrate")
    rec["cost"] += nar["cost_usd"]
    rec["narrator_raw"] = nar["text"]
    reply = parse_reply(nar["text"])
    rec["reply"] = reply
    if reply is None:
        rec["disposition"] = "narrator_unparsed"
        return rec
    v = validate(world, pid, fields, reply, 3)
    rec["validation"] = v
    return rec


def apply_attempt(w, rec: dict) -> list[str]:
    pid = rec["pid"]
    if rec.get("disposition") in ("lift_failed", "narrator_unparsed"):
        msg = "Nothing came of what you set out to do; it could not be made out."
        w.observe(pid, msg, trigger=False)
        return [msg]
    reply, v = rec["reply"], rec["validation"]
    ops = reply["operations"]
    results = {o["path"]: o["valid"] for o in v["ops"]}
    kinds = [o.get("op") for o in ops if isinstance(o, dict)]
    if kinds and kinds[0] == "redirect" and results.get("[0]"):
        op = ops[0].get("operation")
        rec["disposition"] = f"redirect:{op}"
        msg = f"What you described is something you can do directly, with the {op} action."
        w.observe(pid, msg, trigger=False)
        return [msg]
    if kinds and kinds[0] == "reject" and results.get("[0]"):
        rec["disposition"] = f"reject:{ops[0].get('reason')}"
        msg = REJECT_WORDS.get(ops[0].get("reason"), "Nothing came of it.")
        w.observe(pid, msg, trigger=False)
        return [msg]
    keep = _keep_valid(ops, results)
    rec["applied_ops"] = keep
    rec["dropped"] = [o for o in v["ops"] if not o["valid"]]
    if not keep:
        rec["disposition"] = "all_invalid"
        msg = "Nothing came of what you set out to do: it could not be done as described."
        w.observe(pid, msg, trigger=False)
        return [msg]
    rec["disposition"] = "applied:" + ",".join(o["op"] for o in keep)
    return w.apply_narrator_ops(pid, keep, v.get("invest_allocation"))
