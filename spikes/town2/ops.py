"""The one complete list of direct operations, shared by people, the AI and the narrator.

Each entry: name -> (JSON form shown in views, short description, who it applies to).
`who` is "all", "owner", "employed", "landlord", "unemployed" or "client" (has the product).
"""

OPS: dict[str, tuple[str, str, str]] = {
    "read": ('{"op": "read", "what": "household account" | "shop account" | "agreements" | "<a notice or page>"}', "read one's own records or a notice", "all"),
    "message": ('{"op": "message", "to": "<name>", "text": "<what you write>"}', "write to someone", "all"),
    "converse": ('{"op": "converse", "with": "<name>", "opening": "<your first words>"}', "talk with someone now, if they are free (an hour)", "all"),
    "book_appointment": ('{"op": "book_appointment", "with": "<name>", "day": "<e.g. Thursday 12 March>", "about": "<what for>"}', "fix a day to meet someone; the meeting is held that day if both are free", "all"),
    "request": ('{"op": "request", "to": "<name>", "text": "<what you ask for>", "amount": <dollars they would pay you, or 0>}', "ask someone for something; they accept or decline", "all"),
    "answer": ('{"op": "answer", "request": "<id>", "answer": "accept" | "decline", "note": "<optional words>"}', "accept or decline a request, offer or proposed agreement", "all"),
    "pay": ('{"op": "pay", "to": "<name, business or company>", "amount": <dollars>, "note": "<what for>"}', "pay money once", "all"),
    "standing_payment": ('{"op": "standing_payment", "to": "<name or business>", "amount": <dollars>, "day": "<day of the week>", "weeks": <number, or 0 for until cancelled>, "note": "<what for>"}', "set up a weekly payment that goes out by itself", "all"),
    "cancel_standing_payment": ('{"op": "cancel_standing_payment", "id": "<id>"}', "stop a standing payment", "all"),
    "propose_agreement": ('{"op": "propose_agreement", "to": "<name>", "kind": "repayment_plan" | "sale" | "retainer" | "job_terms", "terms": {"amount": <dollars>, "per": "week" | "once", "weeks": <number or 0>, "hours": <hours a week, for job terms>, "what": "<plain words>"}}', "propose a written agreement; it holds once they accept", "all"),
    "move_money": ('{"op": "move_money", "from": "household" | "shop", "to": "shop" | "household", "amount": <dollars>}', "move money between your own accounts", "owner"),
    "repay": ('{"op": "repay", "debt": "<debt id>", "amount": <dollars>}', "repay part or all of a debt", "all"),
    "borrow": ('{"op": "borrow", "amount": <dollars>, "weeks": <number>, "purpose": "<what for>"}', "apply to the bank for a loan", "all"),
    "shop_at": ('{"op": "shop_at", "firm": "<shop name>"}', "choose where the household does its usual shopping", "all"),
    "offer_job": ('{"op": "offer_job", "to": "<name>", "weekly_pay": <dollars>, "hours": <hours a week>, "text": "<optional words>"}', "offer someone a job at your shop", "owner"),
    "offer_shifts": ('{"op": "offer_shifts", "to": "<name>", "hours": <extra hours this week>}', "offer a member of staff extra paid hours this week at their hourly rate", "owner"),
    "accept_job": ('{"op": "accept_job", "offer": "<request id>"}', "accept a job offer", "all"),
    "quit": ('{"op": "quit"}', "leave your job", "employed"),
    "dismiss": ('{"op": "dismiss", "who": "<name>", "text": "<optional words>"}', "dismiss a member of staff", "owner"),
    "set_price": ('{"op": "set_price", "price": <dollars a basket>}', "set your shop's price", "owner"),
    "order_stock": ('{"op": "order_stock", "supplier": "Brook Cash & Carry" | "Dales Wholesale", "units": <number>}', "order stock for Monday", "owner"),
    "set_staff_pay": ('{"op": "set_staff_pay", "who": "<name, or yourself for your draw>", "weekly_pay": <dollars>}', "set staff pay or your own draw", "owner"),
    "work_extra_hours": ('{"op": "work_extra_hours", "hours": <number>}', "put more of your own hours into serving at your shop this week", "owner"),
    "set_rent": ('{"op": "set_rent", "weekly": <dollars>, "text": "<optional words>"}', "set the rent of the house you let, from next week", "landlord"),
    "serve_notice": ('{"op": "serve_notice", "text": "<optional words>"}', "serve notice on a tenant two weeks behind with rent", "landlord"),
    "publish_notice": ('{"op": "publish_notice", "text": "<what it says>"}', "put up a notice everyone in town can see", "all"),
    "sign_up": ('{"op": "sign_up", "for": "myself" | "household" | "shop", "asked": "<what you want it to do, in your own words>", "connect": [{"system": "<one of your systems>", "scope": "read" | "limit" | "free", "limit": <dollars a week, if limit>}]}', "start using Calder, from Calder Systems", "all"),
    "change_access": ('{"op": "change_access", "system": "<system>", "scope": "none" | "read" | "limit" | "free", "limit": <dollars a week>}', "change what your Calder can use", "client"),
    "tell_calder": ('{"op": "tell_calder", "text": "<what you want it to do or know>"}', "tell your Calder something", "client"),
    "leave_calder": ('{"op": "leave_calder"}', "stop using Calder", "client"),
    "set_product_price": ('{"op": "set_product_price", "for": "person" | "shop", "weekly": <dollars>}', "set what Calder costs a week", "company"),
    "write_notes": ('{"op": "write_notes", "text": "<for yourself>"}', "write in your own notes", "all"),
}
ATTEMPT_LINE = 'Anything else you want to do, say it in plain words as {"attempt": "<what you do>"}.'

SYSTEMS = {
    "household_account": "the household bank account",
    "messages": "messages and letters",
    "shop_ordering": "the shop's ordering",
    "shop_till": "the shop's till and prices",
    "shop_payroll": "the shop's staff and pay",
    "shop_books": "the shop's bank account and books",
}

# hours each operation takes from a person's own hours
HOURS = {"converse": 1.0, "book_appointment": 0.0, "message": 0.25, "request": 0.25, "propose_agreement": 0.5, "answer": 0.1,
         "pay": 0.25, "standing_payment": 0.25, "cancel_standing_payment": 0.1, "move_money": 0.1, "repay": 0.25,
         "borrow": 1.0, "shop_at": 0.1, "offer_job": 0.5, "offer_shifts": 0.25, "accept_job": 0.1, "quit": 0.1, "dismiss": 0.5,
         "set_price": 0.1, "order_stock": 0.5, "set_staff_pay": 0.1, "set_rent": 0.25, "serve_notice": 0.5, "publish_notice": 0.5,
         "sign_up": 0.5, "change_access": 0.1, "tell_calder": 0.1, "leave_calder": 0.1, "write_notes": 0.1, "read": 0.1,
         "work_extra_hours": 0.0, "appointment": 1.0, "set_product_price": 0.25}


def lines_for(kinds: set[str]) -> list[str]:
    """The operation lines a person with these kinds sees (kinds include 'all')."""
    return [form for name, (form, desc, who) in OPS.items() if who in kinds]


def narrator_block() -> str:
    return "\n".join(f"- {k}: {v[1]}" for k, v in OPS.items())
