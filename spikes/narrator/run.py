"""Runs every attempt through lift -> narrate -> validate, for each narrator model.

uv run --env-file .env python spikes/narrator/run.py
Replies are cached on disk keyed by a hash of (model, messages, params): reruns are free.
Stops (raises) if total spend passes the budget.
"""

import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from prompts import lift_messages, lift_messages_v2, lift_messages_v3, narrator_messages, narrator_messages_v2, narrator_messages_v3  # noqa: E402
import os  # noqa: E402

VERSION = int(os.environ.get('NARRATOR_VERSION', '1'))
from townsim.model import reply_text, token_count  # noqa: E402  (read-only helpers)
from validate import parse_reply, validate  # noqa: E402

BUDGET_USD = 2.50
LIFTER = {"model": "openrouter/anthropic/claude-haiku-5.5", "reasoning_effort": "none", "max_tokens": 1500, "temperature": 0}
NARRATORS = {
    "haiku": {"model": "openrouter/anthropic/claude-haiku-5.5", "max_tokens": 3000, "temperature": 0},
    "terra": {"model": "openrouter/openai/gpt-6.1-terra", "max_tokens": 4000, "temperature": 0},
}
CACHE = HERE / "cache"
RESULTS = HERE / "results"
PRICES = yaml.safe_load((HERE.parents[1] / "config" / "prices.yaml").read_text())

_lock = threading.Lock()
spend = {"total": 0.0, "fresh": 0.0}


def _price(model: str, pin: int, pout: int) -> float:
    p = PRICES.get(model.removeprefix("openrouter/"), {})
    return (pin * p.get("input_per_mtok", 0) + pout * p.get("output_per_mtok", 0)) / 1e6


def call(params: dict, messages: list[dict]) -> dict:
    key = hashlib.sha256(json.dumps([params, messages], sort_keys=True).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        rec = json.loads(path.read_text())
        with _lock:
            spend["total"] += rec["cost_usd"]
        return rec
    with _lock:
        if spend["total"] > BUDGET_USD:
            raise RuntimeError(f"budget passed: ${spend['total']:.3f}")
    import litellm

    litellm.suppress_debug_info = True
    litellm.drop_params = True
    resp = None
    for attempt in range(3):
        try:
            resp = litellm.completion(messages=messages, timeout=120, **params)
            break
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
    if resp is None:
        return {"text": None, "error": err, "cost_usd": 0.0, "prompt_tokens": None, "completion_tokens": None}
    usage = getattr(resp, "usage", None)
    pin, pout = token_count(usage, "prompt_tokens") or 0, token_count(usage, "completion_tokens") or 0
    try:
        cost = litellm.completion_cost(completion_response=resp)
    except Exception:  # noqa: BLE001
        cost = None
    if not cost:
        cost = _price(params["model"], pin, pout)
    rec = {
        "text": reply_text(resp),
        "cost_usd": cost,
        "prompt_tokens": pin,
        "completion_tokens": pout,
        "reasoning_tokens": token_count(getattr(usage, "completion_tokens_details", None), "reasoning_tokens"),
        "finish_reason": getattr(resp.choices[0], "finish_reason", None),
    }
    path.write_text(json.dumps(rec, indent=1))
    with _lock:
        spend["total"] += cost
        spend["fresh"] += cost
        if spend["total"] > BUDGET_USD:
            raise RuntimeError(f"budget passed: ${spend['total']:.3f}")
    return rec


def parse_fields(text):
    if not text:
        return None
    dec = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = dec.raw_decode(text, start)
            if isinstance(obj, dict) and "goal" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def run_one(world, att, narrators):
    lift = call(LIFTER, {1: lift_messages, 2: lift_messages_v2, 3: lift_messages_v3}[VERSION](world, att["actor"], att["text"]))
    fields = parse_fields(lift["text"])
    out = {"attempt": att, "lift": {"raw": lift["text"], "fields": fields, "cost_usd": lift["cost_usd"],
                                    "prompt_tokens": lift["prompt_tokens"], "completion_tokens": lift["completion_tokens"]}}
    if fields is None:
        out["narrators"] = {}
        return out
    msgs = {1: narrator_messages, 2: narrator_messages_v2, 3: narrator_messages_v3}[VERSION](world, att["actor"], fields)
    out["narrator_user_message"] = msgs[1]["content"]
    res = {}
    for name in narrators:
        rec = call(NARRATORS[name], msgs)
        reply = parse_reply(rec["text"])
        res[name] = {
            "raw": rec["text"],
            "parsed": reply is not None,
            "reply": reply,
            "validation": validate(world, att["actor"], fields, reply, VERSION) if reply else None,
            "cost_usd": rec["cost_usd"],
            "prompt_tokens": rec["prompt_tokens"],
            "completion_tokens": rec["completion_tokens"],
            "reasoning_tokens": rec.get("reasoning_tokens"),
            "error": rec.get("error"),
        }
    out["narrators"] = res
    return out


def main():
    narrators = sys.argv[1].split(",") if len(sys.argv) > 1 else list(NARRATORS)
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    world = json.loads((HERE / "world.json").read_text())
    atts = [json.loads(l) for l in (HERE / "attempts.jsonl").read_text().splitlines() if l.strip()]
    if only:
        atts = [a for a in atts if a["id"] in only]
    CACHE.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda a: run_one(world, a, narrators), atts))
    if not only:
        (RESULTS / f"results_v{VERSION}.json").write_text(json.dumps(results, indent=1))
    else:
        for r in results:
            print(json.dumps({k: r[k] for k in ("lift", "narrators")}, indent=1)[:6000])
    print(f"total spend (incl. cached): ${spend['total']:.4f}; fresh this run: ${spend['fresh']:.4f}")


if __name__ == "__main__":
    main()
