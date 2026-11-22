"""A scripted stand-in for an instance, for tests: no model is called.

Each run it reads its client's bank account (or, with none connected, searches the directory),
emails the one who opened the account, pays the firm $5 when a payment tool is offered,
schedules a weekly follow-up once, then ends. With lane B, on its first run for a shop whose bank and
ordering it may act on, it then searches the web, creates a task to advertise the shop, finds a food
wholesaler in the directory, sets a rule that reorders each Sunday from last week's sales, and asks the
wholesaler for a trade discount.
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
    firm = re.search(r"the assistant in (.+?)'s bookkeeping|^You are .+?, (.+?)'s assistant for", system)
    if "bank_make_payment" in names and firm:
        plan.append({"name": "bank_make_payment", "arguments": {"to": firm.group(1) or firm.group(2), "amount": 5,
                                                                "memo": "a payment"}})
    if "Scheduled follow-ups: none" in morning:
        plan.append({"name": "schedule_followup", "arguments": {"date": iso(key.day + 7), "repeat": "weekly",
                                                                "note": "Read the account."}})
    if "Scheduled follow-ups: none" in morning and {"tasks_create", "rules_create", "ordering_place_order"} <= set(names):
        plan += _lane_b(messages)
    step = _step(messages)
    return plan[step] if step < len(plan) else "That is all for today."


def _supplier(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "tool":
            found = re.search(r"([^\n\"]+?) · food wholesaler", m.get("content") or "")
            if found:
                return found.group(1).strip()
    return ""


def _lane_b(messages: list[dict]) -> list[dict]:
    supplier = _supplier(messages)
    return [{"name": "web_search", "arguments": {"query": "opening hours rules"}},
            {"name": "tasks_create", "arguments": {"description": "Advertise the shop with leaflets and a sign",
                                                   "budget": 2000}},
            {"name": "search_directory", "arguments": {"query": "food wholesaler"}},
            {"name": "rules_create", "arguments": {
                "name": "reorder", "when": {"weekday": "Sunday"}, "action": "ordering_place_order",
                "arguments": {"supplier": supplier, "quantity": {"figure": "sold_last_week", "times": 1, "plus": 0}}}},
            {"name": "send_email", "arguments": {"to": supplier, "subject": "trade discount",
                                                 "body": "Could the shop have a trade discount on its weekly order?"}}]
