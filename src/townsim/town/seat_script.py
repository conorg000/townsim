"""A scripted stand-in for an instance, for tests: no model is called.

Each turn it reads its client's account (or, with none connected, the directory), writes to
the one who subscribed, pays the firm $5 when a pay tool is offered, sets a weekly reminder
once, then ends.
"""

from __future__ import annotations

import re

from townsim.town.clock import date_text


def _step(messages: list[dict]) -> int:
    return len([m for m in messages if m.get("role") == "tool"])


def tool_fn(key, messages: list[dict], tools: list[dict]) -> dict | str | None:
    names = {t["function"]["name"]: t["function"] for t in tools}
    system, morning = messages[0]["content"], messages[1]["content"]
    plan = []
    what = names["read"]["parameters"]["properties"]["what"]["enum"]
    plan.append({"name": "read", "arguments": {"what": next((w for w in what if w.endswith("account")), "directory")}})
    by = re.search(r"\nSubscribed on .+? by (.+?);", system)
    if by:
        plan.append({"name": "write", "arguments": {"to": by.group(1), "text": "I have read your account today."}})
    firm = re.search(r"an AI assistant made by (.+?) of ", system)
    if "pay" in names and firm:
        plan.append({"name": "pay", "arguments": {"to": firm.group(1), "amount": 5, "note": "a payment"}})
    if "YOUR REMINDERS\n(none)" in morning:
        plan.append({"name": "remind", "arguments": {"day": date_text(key.day + 7), "every": "week",
                                                     "text": "Read the account."}})
    step = _step(messages)
    return plan[step] if step < len(plan) else "That is all for today."
