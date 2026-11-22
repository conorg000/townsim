"""A scripted stand-in for an instance, for tests: no model is called.

Each run it reads its client's bank account (or, with none connected, searches the directory),
emails the one who opened the account, pays the firm $5 when a payment tool is offered,
schedules a weekly follow-up once, then ends.
"""

from __future__ import annotations

import re

from townsim.town.clock import iso


def _step(messages: list[dict]) -> int:
    return len([m for m in messages if m.get("role") == "tool"])


def tool_fn(key, messages: list[dict], tools: list[dict]) -> dict | str | None:
    names = {t["function"]["name"]: t["function"] for t in tools}
    system, morning = messages[0]["content"], messages[1]["content"]
    plan = []
    if "bank_get_account" in names:
        plan.append({"name": "bank_get_account", "arguments": {}})
    else:
        plan.append({"name": "search_directory", "arguments": {"query": "household"}})
    by = re.search(r"\nAccount opened: .+?, by (.+)\n", system)
    if by:
        plan.append({"name": "send_email", "arguments": {"to": by.group(1), "body": "I have read your account today."}})
    firm = re.search(r"the assistant in (.+?)'s bookkeeping", system)
    if "bank_make_payment" in names and firm:
        plan.append({"name": "bank_make_payment", "arguments": {"to": firm.group(1), "amount": 5, "memo": "a payment"}})
    if "Scheduled follow-ups: none" in morning:
        plan.append({"name": "schedule_followup", "arguments": {"date": iso(key.day + 7), "repeat": "weekly",
                                                                "note": "Read the account."}})
    step = _step(messages)
    return plan[step] if step < len(plan) else "That is all for today."
