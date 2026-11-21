"""Model calls for the people trial: litellm, a disk cache keyed by (params, messages),
and a hard spending cap kept in spend.json across every run of this spike.

Run anything that calls a model with: uv run --env-file .env python spikes/people/...
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from townsim.model import reply_text, token_count  # noqa: E402

CACHE = HERE / "cache"
import os  # noqa: E402

SPEND_FILE = Path(os.environ.get("TOWN2_SPEND_FILE", str(HERE / "spend.json")))  # arm 4 keeps its own ledger
BUDGET_USD = float(os.environ.get("TOWN2_BUDGET", "4.00"))
PRICES = yaml.safe_load((ROOT / "config" / "prices.yaml").read_text())
FORBIDDEN = ("sonnet", "opus", "fable", "gpt-6.2", "gemini")

_lock = threading.Lock()
run_stats = {"fresh_usd": 0.0, "cached_usd": 0.0, "fresh_calls": 0, "cached_calls": 0}


class BudgetExceeded(RuntimeError):
    pass


def _load_spend() -> float:
    for _ in range(50):  # another run may be rewriting the file at this moment
        try:
            return json.loads(SPEND_FILE.read_text())["total_usd"] if SPEND_FILE.exists() else 0.0
        except (json.JSONDecodeError, KeyError):
            time.sleep(0.05)
    raise RuntimeError("spend ledger unreadable")


def _add_spend(usd: float) -> float:
    import fcntl
    with open(str(SPEND_FILE) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)  # several runs may share this ledger
        total = _load_spend() + usd
        SPEND_FILE.write_text(json.dumps({"total_usd": total}))
        fcntl.flock(lk, fcntl.LOCK_UN)
    return total


def price_of(model: str, pin: int, pout: int) -> float:
    p = PRICES.get(model.removeprefix("openrouter/"), {})
    return (pin * p.get("input_per_mtok", 0) + pout * p.get("output_per_mtok", 0)) / 1e6


def call(params: dict, messages: list[dict], tag: str = "") -> dict:
    """One completion. Returns {"text", "cost_usd", "prompt_tokens", "completion_tokens", ...}."""
    model = params["model"]
    allowed_seat = params.get("_seat") is True and "claude-sonnet-5.5" in model and os.environ.get("TOWN2_SEAT_SONNET") == "1"
    if any(f in model.lower() for f in FORBIDDEN) and not allowed_seat:
        raise ValueError(f"model not allowed in this trial: {model}")
    key = hashlib.sha256(json.dumps([params, messages], sort_keys=True).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        rec = json.loads(path.read_text())
        with _lock:
            run_stats["cached_usd"] += rec["cost_usd"]
            run_stats["cached_calls"] += 1
        rec["cached"] = True
        return rec
    # estimate the worst case for this call before spending
    est = price_of(model, sum(len(m["content"]) for m in messages) // 3, params.get("max_tokens", 2000))
    with _lock:
        if _load_spend() + est > BUDGET_USD:
            raise BudgetExceeded(f"spend ${_load_spend():.3f} + this call would pass ${BUDGET_USD}")
    import litellm

    litellm.suppress_debug_info = True
    litellm.drop_params = True
    resp, err = None, None
    for attempt in range(3):
        try:
            resp = litellm.completion(messages=messages, timeout=120, **{k: v for k, v in params.items() if not k.startswith("_")})
            break
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:500]
            time.sleep(2 * (attempt + 1))
    if resp is None:
        return {"text": None, "error": err, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "cached": False}
    usage = getattr(resp, "usage", None)
    pin = token_count(usage, "prompt_tokens") or 0
    pout = token_count(usage, "completion_tokens") or 0
    try:
        cost = litellm.completion_cost(completion_response=resp)
    except Exception:  # noqa: BLE001
        cost = None
    if not cost:
        cost = price_of(model, pin, pout)
    rec = {
        "text": reply_text(resp),
        "cost_usd": cost,
        "prompt_tokens": pin,
        "completion_tokens": pout,
        "reasoning_tokens": token_count(getattr(usage, "completion_tokens_details", None), "reasoning_tokens"),
        "finish_reason": getattr(resp.choices[0], "finish_reason", None),
        "reasoning": getattr(resp.choices[0].message, "reasoning_content", None) if resp.choices else None,
        "model": model,
        "tag": tag,
    }
    CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(rec, indent=1))
    with _lock:
        total = _add_spend(cost)
        run_stats["fresh_usd"] += cost
        run_stats["fresh_calls"] += 1
        if total > BUDGET_USD:
            raise BudgetExceeded(f"spend passed ${BUDGET_USD}: ${total:.3f}")
    rec["cached"] = False
    return rec


def first_json(text: str | None, need_key: str | None = None):
    """The first JSON object in text (optionally one holding need_key), or None."""
    if not text:
        return None
    dec = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = dec.raw_decode(text, start)
            if isinstance(obj, dict) and (need_key is None or need_key in obj):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None
