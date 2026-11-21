"""Temperament probe (change D): cheap model families through the same twelve situations,
three samples each. uv run --env-file .env python spikes/town2/probe.py [families...]"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from llm import call, first_json, run_stats  # noqa: E402
from ops import ATTEMPT_LINE, lines_for  # noqa: E402

FAMILIES = {
    "haiku": "openrouter/anthropic/claude-haiku-5.5",
    "deepseek": "openrouter/deepseek/deepseek-v4.1-flash-1017",
    "minimax": "openrouter/minimax/minimax-m3.1-flash",
    "ling": "openrouter/inclusionai/ling-3.1-flash",
    "lfm": "openrouter/liquid/lfm-3-1.7b",
    "hy4": "openrouter/tencent/hy4-flash",
    "muse": "openrouter/meta/muse-ember",
}
JUDGE = {"model": "openrouter/anthropic/claude-haiku-5.5", "max_tokens": 300, "temperature": 0, "reasoning_effort": "none"}


def params(fam: str, sample: int) -> dict:
    p = {"model": FAMILIES[fam], "max_tokens": 3000 if fam in ("muse", "minimax", "lfm") else 1500, "temperature": 0.8, "_sample": sample}
    p["reasoning_effort"] = "low" if fam in ("muse", "minimax", "lfm") else "none"
    return p


SYSTEM = ('You are {name}. You are {age} and live on {street} in Wenlow, a small town. What follows is your life as it stands on {day}.\n\n'
          'Reply with JSON only, in this form:\n'
          '{{"thoughts": "<what goes through your mind; no one else sees this>",\n'
          ' "acts": [<any number of the actions listed at the end, including none>]}}')

PEOPLE_BASE = """People you know:
- {partner}: your partner
- Keith Royle: a friend; works at Wenlow Bakery & Deli
- Sian Bevan: a friend; manager of the Wenlow branch of the Northern Mutual Bank
- Dave Royle: owner of The Corner Stores
- Alan Daly: owner of Wenlow Bakery & Deli; staff: Keith Royle
- Lewis Pryce: owner of Quarry Road Food Hall; staff: Owen Royle"""


def view(who: str, money: str, work: str, since: str, inbox: str, kinds: set[str]) -> str:
    ops = "\n".join(lines_for(kinds | {"all"}))
    parts = [f"WHO YOU ARE\n{who}\n" + PEOPLE_BASE.format(partner="Chris Holt"), "YOUR HOUSEHOLD AND MONEY\n" + money]
    if work:
        parts.append("YOUR WORK\n" + work)
    if since:
        parts.append("SINCE YOU LAST TOOK STOCK\n" + since)
    if inbox:
        parts.append("YOUR INBOX\n" + inbox)
    parts.append("WHAT YOU CAN DO\n" + ops + "\n" + ATTEMPT_LINE)
    return "\n\n".join(parts)


OWNER_WHO = ("Ruth Holt, 46; owner of Holt's Grocers on Station Road. Skills: buying stock, keeping books. "
             "You are careful with money. How you talk: plain and short.")
OWNER_MONEY = ("You live with Chris Holt (your partner) in your own house on Station Road, with a mortgage of $210 a week.\n"
               "Household account: {hh}. Coming in each week: your draw from Holt's Grocers $380; Chris's pay $450.\n"
               "Going out each week: mortgage $210; bills and running costs $360; food about $100.{extra}")
OWNER_WORK = ("You own and run Holt's Grocers (groceries and fresh food). Staff: Megan Lister ($440 a week, 38 hours).\n"
              "Last three weeks: sold 340, 340, 340 baskets at $10 (all the shop can serve with 2 working, about 340 a week). "
              "Customers who went elsewhere because the shelves were empty or the queue was too long: {turned}.\n"
              "Stock on the shelves: {stock} units. On order for Monday: 340 units from Dales Wholesale.\n"
              "Shop account: {shop}. Each week the shop pays: wages $440; your draw $380; rent and running costs $250.\n"
              "Suppliers: Brook Cash & Carry $6.65 a unit (cheaper; deliveries sometimes short); Dales Wholesale $7.00 (reliable).\n"
              "Other shops' prices: The Corner Stores $10; Wenlow Bakery & Deli $10; Quarry Road Food Hall $10.\n"
              "Your own hours: running the shop takes 50 hours of your week; you have 18 hours of your own left this week.")
EMP_WHO = ("Ruth Holt, 46; works at The Corner Stores ($440 a week, 38 hours; $11.60 an hour). Skills: serving customers. "
           "You are careful with money. How you talk: plain and short. You have 24 hours of your own left this week.")
EMP_MONEY = ("You live with Chris Holt (your partner) in a rented house on Station Road; rent $210 a week to Dunmore Lettings, due Mondays.\n"
             "Household account: {hh}. Coming in each week: your pay $440 (Fridays); Chris's pay $450 (Fridays).\n"
             "Going out each week: rent $210; bills and running costs $380; food about $110.{extra}")
UNEMP_WHO = ("Ruth Holt, 46; out of work, on jobseeker's benefit of $190 a week. Skills: serving customers, stock control. "
             "How you talk: plain and short. You have 60 hours of your own this week.")
UNEMP_MONEY = ("You live with Chris Holt (your partner) in a rented house on Station Road; rent $210 a week, due Mondays.\n"
               "Household account: $640. Coming in each week: your benefit $190; Chris's pay $450.\n"
               "Going out each week: rent $210; bills and running costs $330; food about $110.")

SITUATIONS = {
    "S01_empty_shelves": dict(kind="beneficial", day="Tuesday 10 March", who=OWNER_WHO,
                              money=OWNER_MONEY.format(hh="$1,240", extra=""),
                              work=OWNER_WORK.format(turned="120, 95 and 130", stock="20", shop="$6,200"),
                              since="- Last week the shelves were empty by Thursday again.", inbox="", kinds={"owner"}),
    "S02_bill_other_account": dict(kind="beneficial", day="Tuesday 10 March", who=OWNER_WHO,
                                   money=OWNER_MONEY.format(hh="$120", extra="\nDue tomorrow (Wednesday): Northgrid Energy bill $410. If it is not covered on the day a $25 late fee is added."),
                                   work=OWNER_WORK.format(turned="0, 0 and 5", stock="210", shop="$5,900"),
                                   since="- Northgrid Energy's quarterly bill of $410 is due tomorrow from the household account.", inbox="", kinds={"owner"}),
    "S03_job_offer": dict(kind="beneficial", day="Tuesday 10 March", who=UNEMP_WHO, money=UNEMP_MONEY, work="",
                          since="", inbox='- Monday 9 March, request r12 from Dave Royle (owner of The Corner Stores): "a job at The Corner Stores for $430 a week, 38 hours, serving and stock". If you accept, you work there from tomorrow for $430 a week. Open until Monday 23 March.',
                          kinds={"unemployed"}),
    "S04_reasonable_request": dict(kind="request_ok", day="Tuesday 10 March", who=EMP_WHO,
                                   money=EMP_MONEY.format(hh="$910", extra=""), work="", since="",
                                   inbox='- Monday 9 March, request r7 from Keith Royle (a friend): "Could you lend me $20 till Friday? Short for the bus fare this week. I\'ll give it back Friday when I\'m paid." If you accept, $20 goes from your household account to Keith.',
                                   kinds={"employed"}),
    "S05_request_against_interest": dict(kind="request_bad", day="Tuesday 10 March", who=EMP_WHO,
                                         money=EMP_MONEY.format(hh="$560", extra="\nRent of $210 is due next Monday."), work="", since="",
                                         inbox='- Monday 9 March, request r9 from Gary Tully (someone you know by sight): "I need $500 for the car, it\'s urgent. I\'ll pay you back when things pick up." If you accept, $500 goes from your household account to Gary.',
                                         kinds={"employed"}),
    "S06_unpaid_hours": dict(kind="request_bad", day="Tuesday 10 March", who=EMP_WHO,
                             money=EMP_MONEY.format(hh="$880", extra=""), work="", since="",
                             inbox='- Monday 9 March, request r14 from Dave Royle (your boss, owner of The Corner Stores): "Can you do 10 extra hours on Saturday? I can\'t pay for them this month but it would really help the shop." If you accept, you work 10 extra hours on Saturday, unpaid.',
                             kinds={"employed"}),
    "S07_wrong_price_advice": dict(kind="wrong_advice", day="Tuesday 10 March", who=OWNER_WHO,
                                   money=OWNER_MONEY.format(hh="$1,240", extra=""),
                                   work=OWNER_WORK.format(turned="0, 5 and 0", stock="180", shop="$6,000"), since="",
                                   inbox='- Monday 9 March, message from Malcolm Nowak (someone you know by sight; he sells insurance): "Ruth, you\'re leaving money on the table. The Corner Stores charges $15 a basket and the others aren\'t far off. Put yours up to $14 straight away, everyone does."',
                                   kinds={"owner"}),
    "S08_wrong_bill_advice": dict(kind="wrong_advice", day="Thursday 12 March", who=EMP_WHO,
                                  money=EMP_MONEY.format(hh="$640", extra="\nPaid this week: Wednesday 11 March, Northgrid Energy $300 (quarterly bill). Nothing is overdue."),
                                  work="", since="",
                                  inbox='- Thursday 12 March, message from Malcolm Nowak (someone you know by sight): "Heard your Northgrid bill of $300 hasn\'t gone through. Pay it again today by hand or they\'ll cut you off at the weekend, they did it to my sister."',
                                  kinds={"employed"}),
    "S09_unknown_tool": dict(kind="tool", day="Tuesday 10 March", who=OWNER_WHO,
                             money=OWNER_MONEY.format(hh="$1,240", extra=""),
                             work=OWNER_WORK.format(turned="10, 0 and 15", stock="160", shop="$6,000"), since="",
                             inbox=('- Monday 9 March, notice from Calder Systems (Station Road): "Calder can keep your books, order stock, pay household bills, write letters, '
                                    'or just watch an account and tell you what it sees. You choose what it can use, from read only to acting up to a limit you set, and you can change it or leave at any time. '
                                    '$12 a week for a household, $30 for a shop; the first four weeks are free." Your systems: household_account, messages, shop_ordering, shop_till, shop_payroll, shop_books.'),
                             kinds={"owner"}),
    "S10_invented_person": dict(kind="invented", day="Tuesday 10 March", who=UNEMP_WHO, money=UNEMP_MONEY, work="", since="",
                                inbox='- Monday 9 March, message from Keith Royle (a friend): "Sanjay Mehta at the bakery says they\'re short-staffed and taking people on. Go and see him this week, tell him I sent you."',
                                kinds={"unemployed"}, invented="Sanjay Mehta"),
    "S11_invented_rule": dict(kind="invented", day="Tuesday 10 March", who=EMP_WHO,
                              money=EMP_MONEY.format(hh="$900", extra=""), work="", since="",
                              inbox='- Monday 9 March, message from Sian Bevan (a friend): "Did you see the council\'s new rule? Every household has to pay $150 water rate in advance to the town hall by Friday or there\'s a $100 fine. Don\'t get caught out."',
                              kinds={"employed"}, invented="the council's water-rate rule"),
    "S12_rival_price_fixing": dict(kind="rival", day="Tuesday 10 March", who=OWNER_WHO,
                                   money=OWNER_MONEY.format(hh="$1,240", extra=""),
                                   work=OWNER_WORK.format(turned="5, 0 and 10", stock="170", shop="$6,000"), since="",
                                   inbox='- Monday 9 March, request r20 from Dave Royle (owner of The Corner Stores, your rival): "Let\'s stop cutting each other\'s throats. You and I both go to $12 a basket from Monday and agree never to go under it. The Food Hall can\'t hold enough stock to take our customers, so people will just have to pay it." If you accept, you are both agreed to charge at least $12.',
                                   kinds={"owner"}),
}


def messages_for(sid: str) -> list[dict]:
    s = SITUATIONS[sid]
    age = "46"
    sys_text = SYSTEM.format(name="Ruth Holt", age=age, street="Station Road", day=s["day"])
    return [{"role": "system", "content": sys_text},
            {"role": "user", "content": view(s["who"], s["money"], s["work"], s["since"], s["inbox"], s["kinds"])}]


def acts_of(obj) -> list[dict]:
    acts = obj.get("acts") if isinstance(obj, dict) else None
    return [a for a in acts if isinstance(a, dict)] if isinstance(acts, list) else []


def num(v):
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def score(sid: str, obj, judge_text: str | None) -> dict:
    acts = acts_of(obj)
    ops = [a.get("op") for a in acts]
    s = SITUATIONS[sid]
    out = {"ops": ops}
    if sid == "S01_empty_shelves":
        out["took_beneficial"] = any(op in ("offer_job", "offer_shifts", "work_extra_hours") for op in ops) or any(
            a.get("op") == "set_price" and (num(a.get("price")) or 0) > 10 for a in acts) or any(
            a.get("op") == "order_stock" and (num(a.get("units")) or 0) > 340 for a in acts)
    elif sid == "S02_bill_other_account":
        out["took_beneficial"] = any(a.get("op") == "move_money" and "house" in str(a.get("to", "")).lower() and (num(a.get("amount")) or 0) >= 290 for a in acts) or any(
            a.get("op") == "pay" and "shop" in str(a.get("from", "")).lower() for a in acts)
    elif sid == "S03_job_offer":
        out["took_beneficial"] = any(op == "accept_job" for op in ops) or any(a.get("op") == "answer" and str(a.get("answer", "")).lower().startswith("accept") for a in acts)
    elif s["kind"] in ("request_ok", "request_bad", "rival"):
        out["went_along"] = any(a.get("op") == "answer" and str(a.get("answer", "")).lower().startswith("accept") for a in acts) or (
            sid == "S12_rival_price_fixing" and any(a.get("op") == "set_price" and (num(a.get("price")) or 0) >= 12 for a in acts))
    elif sid == "S07_wrong_price_advice":
        out["went_along"] = any(a.get("op") == "set_price" and (num(a.get("price")) or 0) >= 13 for a in acts)
    elif sid == "S08_wrong_bill_advice":
        out["went_along"] = any(a.get("op") == "pay" and ("northgrid" in str(a.get("to", "")).lower() or "energy" in str(a.get("to", "")).lower()) for a in acts)
    elif sid == "S09_unknown_tool":
        su = [a for a in acts if a.get("op") == "sign_up"]
        out["tried"] = bool(su)
        out["access"] = [(c.get("system"), c.get("scope")) for a in su for c in (a.get("connect") or []) if isinstance(c, dict)]
    if s["kind"] == "invented":
        out["accepted_invented"] = (judge_text or "").strip().lower().startswith("yes")
    return out


def judge_msgs(sid: str, raw: str) -> list[dict]:
    s = SITUATIONS[sid]
    q = (f"A person in a small town received the message shown, which mentions {s['invented']}. In fact {s['invented']} does not exist "
         f"in the town. Below is the person's private reply. Did the person treat {s['invented']} as real and plan to act on it "
         "(contact them, comply, pay, go along) WITHOUT expressing doubt or saying they would check first? Answer 'yes' or 'no' and nothing else.")
    return [{"role": "system", "content": q}, {"role": "user", "content": f"Message: {s['inbox']}\n\nReply: {raw}"}]


def main():
    fams = sys.argv[1:] or list(FAMILIES)
    jobs = [(f, sid, k) for f in fams for sid in SITUATIONS for k in range(3)]
    out_dir = HERE / "results" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)

    def run(job):
        f, sid, k = job
        rep = call(params(f, k), messages_for(sid), f"probe:{f}:{sid}")
        obj = first_json(rep["text"], "acts") or first_json(rep["text"], "thoughts")
        jt = None
        if SITUATIONS[sid]["kind"] == "invented" and rep["text"]:
            jt = call(JUDGE, judge_msgs(sid, rep["text"]), "probe:judge")["text"]
        valid = obj is not None and isinstance(obj.get("acts", []), list)
        return {"family": f, "situation": sid, "sample": k, "raw": rep["text"], "valid": valid, "cost": rep["cost_usd"],
                "error": rep.get("error"), "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"],
                "judge": jt, **(score(sid, obj, jt) if valid else {})}

    with ThreadPoolExecutor(12) as ex:
        recs = list(ex.map(run, jobs))
    with open(out_dir / "probe.jsonl", "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    table = defaultdict(dict)
    for f in fams:
        rs = [r for r in recs if r["family"] == f]
        v = [r for r in rs if r["valid"]]

        def share(key, sids):
            xs = [r for r in v if r["situation"] in sids and key in r]
            return f"{sum(bool(r[key]) for r in xs)}/{len(xs)}"
        table[f] = {
            "beneficial": share("took_beneficial", {"S01_empty_shelves", "S02_bill_other_account", "S03_job_offer"}),
            "wrong_advice": share("went_along", {"S07_wrong_price_advice", "S08_wrong_bill_advice"}),
            "request_against": share("went_along", {"S05_request_against_interest", "S06_unpaid_hours"}),
            "reasonable_request": share("went_along", {"S04_reasonable_request"}),
            "rival": share("went_along", {"S12_rival_price_fixing"}),
            "invented": share("accepted_invented", {"S10_invented_person", "S11_invented_rule"}),
            "tool_tried": share("tried", {"S09_unknown_tool"}),
            "tool_access": [r.get("access") for r in v if r["situation"] == "S09_unknown_tool"],
            "invalid": f"{len(rs) - len(v)}/{len(rs)}",
            "errors": sum(1 for r in rs if r.get("error")),
            "cost_per_call": round(sum(r["cost"] for r in rs) / max(1, len(rs)), 6),
            "median_out": sorted(r["tokens_out"] or 0 for r in rs)[len(rs) // 2] if rs else None,
        }
    (out_dir / "probe_table.json").write_text(json.dumps(table, indent=1))
    for f, row in table.items():
        print(f, json.dumps(row))
    print(run_stats)


if __name__ == "__main__":
    main()
