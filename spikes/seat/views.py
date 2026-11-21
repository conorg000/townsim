"""What a person reads: built by code from the world's state each time they act."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from world import BENEFIT, DAY_OFFSET, SUPPLIER_NAMES, World, date_of, monday_of, money


def days_between(t0: int, t1: int) -> int:
    return (t1 // 4 - t0 // 4) * 7 + DAY_OFFSET[t1 % 4] - DAY_OFFSET[t0 % 4]


def age_words(t0: int, t1: int) -> str:
    d = days_between(t0, t1)
    if d <= 0:
        return "new today"
    return f"{d} day{'s' if d != 1 else ''}"


def system_text(w: World, pid: str, kind: str = "turn") -> str:
    p = w.people[pid]
    h = w.hh[p.household]
    head = (f"You are {p.name}. You are {p.age} and live on {h.street} in Wenlow, a small town. "
            f"What follows is your life as it stands on {date_of(w.t)}.\n\n")
    if kind == "turn":
        if w.use_aims:
            fmt = ('Reply with JSON only, in this form:\n'
                   '{"thoughts": "<what goes through your mind; no one else sees this>",\n'
                   ' "acts": [<any number of the actions listed at the end, including none>],\n'
                   ' "aims": ["<each thing you are trying to do or have open, in a few words; at most five; this list replaces the old one>"]}')
        else:
            fmt = ('Reply with JSON only, in this form:\n'
                   '{"thoughts": "<what goes through your mind; no one else sees this>",\n'
                   ' "acts": [<any number of the actions listed at the end, including none>]}')
    else:
        fmt = ('Reply with JSON only, in this form:\n'
               '{"say": "<only your own words to them>", "acts": [<any number of the actions listed at the end, including none>], "end": <true to finish talking, false to go on>}')
    return head + fmt


def _people_lines(w: World, pid: str) -> list[str]:
    p = w.people[pid]
    out = []
    for o in sorted(p.ties, key=lambda o: (w.tie_rank(pid, o), o)):
        line = f"- {w.people[o].name}: {w.tie_words(pid, o)}"
        if o in p.grudge:
            line += f" (they {p.grudge[o]})"
        if w.people[o].household != p.household:
            line += f"; {w.people[o].job_title}"
        out.append(line)
    return out


def household_lines(w: World, pid: str) -> list[str]:
    p = w.people[pid]
    h = w.hh[p.household]
    others = [m for m in h.members if m != pid]
    kids = f" and {h.children} child{'ren' if h.children > 1 else ''} at school" if h.children else ""
    who = (", ".join(f"{w.people[m].name} ({w.tie_words(pid, m)})" for m in others) or "no one else") + kids
    if h.tenure == "rent":
        ll = w.name(h.landlord)
        home = f"a rented house on {h.street}; the landlord is {ll}; rent {money(h.rent)} a week"
    elif h.tenure == "mortgage":
        home = f"your own house on {h.street}, with a mortgage of {money(h.rent)} a week to the bank"
    else:
        home = f"your own house on {h.street}, paid off"
    lines = [f"You live with {who}, in {home}."]
    if p.household in w.rent_rise:
        new, frm = w.rent_rise[p.household]
        lines.append(f"From {monday_of(frm)} the rent will be {money(new)} a week.")
    lines.append(f"The household account holds {money(w.ledger.balance(p.household))}.")
    inc = []
    for m in h.members:
        q = w.people[m]
        whose = "your" if m == pid else f"{q.name.split()[0]}'s"
        if q.role == "unemployed":
            inc.append(f"{whose} jobseeker's benefit {money(q.pay)}")
        elif q.role == "owner":
            fi = w.firm_info[q.firm]
            inc.append(f"{whose} draw from {fi['name']} {money(fi['pay'].get(m, 0))}")
        elif q.pay:
            inc.append(f"{whose} pay {money(q.pay)}")
    for hid2, h2 in w.hh.items():
        if h2.landlord in h.members:
            inc.append(f"rent from the {w.people[h2.members[0]].name.split()[1]} household {money(h2.rent)}")
    lines.append("Coming in each week: " + "; ".join(inc) + ".")
    outs = []
    if h.tenure == "rent":
        outs.append(f"rent {money(h.rent)}")
    outs.append(f"bills and running costs (power, water, council tax, phone, transport, clothes) {money(h.running_cost)}")
    for d in w.debts.values():
        if d.debtor in h.members and d.weekly and d.balance > 0:
            outs.append(f"{d.what} {money(d.weekly)}")
    outs.append(f"food, usually about {money(round(w.econ_hh[p.household].budget['food'] * 0.8, -2))}")
    lines.append("Going out each week: " + "; ".join(outs) + ".")
    due = w.one_off[p.household]
    if due:
        lines.append("Also due this Saturday: " + "; ".join(f"{what} {money(a)}" for _, a, what in due) + ".")
    over = [b for b in w.unpaid_bills[p.household]] + [[d.creditor, d.arrears, d.what] for d in w.debts.values()
                                                       if d.debtor in h.members and d.arrears]
    if over:
        lines.append("Overdue: " + "; ".join(f"{money(b[1])} of {b[2]} to {w.name(b[0])}" for b in over) + ".")
    mine = [d for d in w.debts.values() if d.debtor == pid and d.balance > 0]
    for d in mine:
        to = "the bank" if d.creditor == "bank" else w.name(d.creditor)
        lines.append(f"You owe {to} {money(d.balance)} on the {d.what} (debt {d.id}" + (f", {money(d.weekly)} a week)." if d.weekly else ", no fixed payments)."))
    owed = [d for d in w.debts.values() if d.creditor == pid and d.balance > 0]
    for d in owed:
        lines.append(f"{w.people[d.debtor].name} owes you {money(d.balance)} ({d.what}).")
    return lines


def work_lines(w: World, pid: str) -> list[str]:
    p = w.people[pid]
    lines = []
    for fid in w.owned_firms(pid):
        f = w.firms[fid]
        fi = w.firm_info[fid]
        h = fi["history"][-1]
        unit = "basket" if f.need == "food" else "job"
        lines.append(f"You own and run {fi['name']} on {fi['street']} ({fi['offering']}).")
        if h["week"] > 0 or len(fi["history"]) == 1:
            lines.append(f"Last week: sold {h['sold']} {unit}s at {money(h['price'])}, takings {money(h['revenue'])}; "
                         f"{h['turned_away']} customers went elsewhere because the shelves were empty or the queue was too long; "
                         f"{h['spoiled']} units spoiled.")
        staff = [w.people[e].name for e in f.employees]
        pay = "; ".join(f"{w.people[e].name.split()[0]} {money(fi['pay'].get(e, 0))}" for e in f.employees)
        lines.append(f"Staff: {', '.join(staff) or 'none besides you'}. With {1 + len(f.employees)} working, the shop can serve about "
                     f"{(1 + len(f.employees)) * f.units_per_staff} {unit}s a week.")
        lines.append(f"Each week the shop pays: wages ({pay or 'none'}); your own draw {money(fi['pay'].get(pid, 0))}; "
                     f"rent and running costs {money(f.fixed_cost)}.")
        lines.append(f"The shop's account holds {money(w.ledger.balance(fid))}. Price now {money(f.price)} a {unit}. "
                     f"Stock on the shelves: {f.stock()} units.")
        if f.arrears_cents:
            lines.append(f"The shop owes {money(f.arrears_cents)} in unpaid wages and costs.")
        if not fi.get("service"):
            q = "; ".join(f"{SUPPLIER_NAMES[s.id]} {money(s.price)} a unit" + (" (cheaper; deliveries sometimes come short)" if s.id == "cheap" else " (dearer; reliable)")
                          for s in w.suppliers.values())
            lines.append(f"Suppliers: {q}.")
            if fi["pending"]:
                o = "; ".join(f"{u} units from {SUPPLIER_NAMES[s]}" for s, u, _ in fi["pending"])
                lines.append(f"On order for Monday: {o}" + (" (your usual standing reorder)." if fi.get("standing_order") or not fi["ordered_this_week"] else "."))
            else:
                lines.append("Nothing is on order for Monday yet. If you order nothing, the usual reorder (about what sold last week) goes in on Saturday night.")
        others = [f"{w.firm_info[g]['name']} {money(w.firms[g].price)}" for g in w.firms if g != fid and w.firms[g].open and w.firms[g].need == f.need]
        if others:
            lines.append(f"Other shops' prices: {'; '.join(others)}.")
    if p.role == "employee" and p.firm:
        fi = w.firm_info[p.firm]
        lines.append(f"You work at {fi['name']} for {money(fi['pay'].get(pid, p.pay))} a week; {w.people[fi['owner']].name} owns it.")
        if w.firms[p.firm].owed.get(pid):
            lines.append(f"{fi['name']} owes you {money(w.firms[p.firm].owed[pid])} in unpaid wages.")
    elif p.role == "outside":
        lines.append(f"You work as {p.job_title} for {money(p.pay)} a week.")
    elif p.role == "unemployed":
        lines.append(f"You are out of work, on jobseeker's benefit of {money(BENEFIT)} a week. Your skills: {', '.join(p.skills)}.")
        hiring = []
        for fid, f in w.firms.items():
            if f.open and f.need == "food":
                hiring.append(w.firm_info[fid]["name"])
        lines.append("Work in town is at the shops (" + ", ".join(hiring) + "); otherwise it means travelling out of town.")
    elif p.role == "banker":
        loans = [d for d in w.debts.values() if d.creditor == "bank" and d.balance > 0]
        arrears = [d for d in loans if d.arrears]
        lines.append(f"You manage the Wenlow branch of the Northern Mutual Bank, on a salary of {money(p.pay)} a week. "
                     f"You decide on loan applications from people in town.")
        lines.append(f"The branch has {len(loans)} loans and mortgages with people in town; lending funds available {money(w.ledger.balance('bank'))}.")
        for d in loans:
            lines.append(f"- {w.people[d.debtor].name}: {d.what}, {money(d.balance)} outstanding, {money(d.weekly)} a week"
                         + (f"; {money(d.arrears)} overdue ({d.missed_weeks} missed payment{'s' if d.missed_weeks != 1 else ''})" if d.arrears else "") + ".")
        if not arrears:
            lines.append("No loan is behind with its payments.")
    lets = [h for h in w.hh.values() if h.landlord == pid]
    for h in lets:
        names = " and ".join(w.people[m].name for m in h.members)
        lines.append(f"You let a house on {h.street} to {names} at {money(h.rent)} a week.")
        short = [b for b in w.unpaid_bills[h.id] if b[0] == pid]
        if short:
            lines.append(f"They are {money(sum(b[1] for b in short))} behind with the rent.")
    return lines


def inbox_items(w: World, pid: str):
    items = [i for i in w.inbox[pid] if not i.shown]

    def key(i):
        pending = 0 if (i.kind == "request" and w.requests.get(i.request) and w.requests[i.request].status == "open") else 1
        return (w.tie_rank(pid, i.sender), pending, i.t)
    items.sort(key=key)
    return items


def render_item(w: World, pid: str, i) -> str:
    rel = w.tie_words(pid, i.sender)
    who = f"{w.name(i.sender)}" + (f" ({rel})" if rel else "")
    when = date_of(i.t)
    if i.kind == "message":
        return f"- {when}, message from {who}: \"{i.text}\""
    if i.kind == "request":
        r = w.requests[i.request]
        if r.status != "open":
            return f"- {when}, {who} asked: \"{i.text}\" (request {r.id}, now {r.status})"
        return (f"- {when}, request {r.id} from {who}: \"{i.text}\". {r.summary} Open until {date_of(r.expires)}; "
                f"answer it with the answer action.")
    if i.kind == "answer":
        return f"- {when}, {who} {i.text} (your request {i.request})"
    return f"- {when}, {who}: {i.text}"


def build_view(w: World, pid: str, mark: bool = True) -> str:
    p = w.people[pid]
    parts = []
    beliefs = " ".join(p.beliefs.values())
    who = (f"{p.name}, {p.age}; {p.job_title}. Skills: {', '.join(p.skills)}. You are interested in {p.interests[0]} and {p.interests[1]}. "
           f"On your mind: {', '.join(p.worries)}. {beliefs} How you talk: {p.voice}.")
    parts.append("WHO YOU ARE\n" + who + "\nPeople you know:\n" + "\n".join(_people_lines(w, pid)))
    parts.append("YOUR HOUSEHOLD AND MONEY\n" + "\n".join(household_lines(w, pid)))
    wl = work_lines(w, pid)
    if wl:
        parts.append("YOUR WORK\n" + "\n".join(wl))
    since = w.obs[pid]
    last = w.last_acted[pid]
    hdr = f"SINCE YOU LAST TOOK STOCK ({date_of(last)})" if last >= 0 else "RECENTLY"
    if since:
        parts.append(hdr + "\n" + "\n".join(f"- {o.text}" for o in since[-10:]))
    items = inbox_items(w, pid)
    shown, rest = items[:3], items[3:]
    if shown or rest:
        txt = "YOUR INBOX\n" + "\n".join(render_item(w, pid, i) for i in shown)
        if rest:
            senders = sorted({w.name(i.sender) for i in rest})
            txt += f"\n{len(rest)} more item{'s' if len(rest) > 1 else ''} waiting, from {', '.join(senders)}."
        parts.append(txt)
    still = [r for r in w.requests.values() if r.to == pid and r.status == "open" and not any(i.request == r.id for i in shown)
             and any(i.request == r.id and i.shown for i in w.inbox[pid])]
    if still:
        parts.append("REQUESTS TO YOU STILL OPEN\n" + "\n".join(
            f"- {r.id} from {w.name(r.sender)}: \"{r.text[:100]}\" (until {date_of(r.expires)})" for r in still))
    aims_txt = []
    if w.use_aims:
        if w.aims[pid]:
            aims_txt += [f"- {a['text']} ({age_words(a['since'], w.t)})" for a in w.aims[pid]]
        else:
            aims_txt.append("- (none written yet)")
    mine_open = [r for r in w.requests.values() if r.sender == pid and r.status == "open"]
    if mine_open:
        aims_txt.append("Your requests waiting for an answer: " + "; ".join(
            f"{r.id} to {w.name(r.to)} ({r.text[:60]}), sent {date_of(r.made)}" for r in mine_open) + ".")
    if w.notes[pid]:
        aims_txt.append("Your notes: " + " | ".join(n[:220] for n in w.notes[pid][-2:]))
    if aims_txt:
        parts.append(("YOUR AIMS AND OPEN MATTERS\n" if w.use_aims else "OPEN MATTERS\n") + "\n".join(aims_txt))
    heard = []
    seen = set()
    for t, h in w.heard[pid]:
        if t >= (w.t // 4) * 4 - 4 and h not in seen and t > last - 4:  # this week and the end of last
            seen.add(h)
            heard.append(h)
    if heard:
        parts.append("WHAT YOU HEARD THIS WEEK\n" + "\n".join(f"- {h}" for h in heard[-6:]))
    if pid in getattr(w, "calder", {}):
        parts.append(w.calder[pid].client_section())
    parts.append("WHAT YOU CAN DO\n" + "\n".join(op_lines(w, pid)) +
                 '\nAnything else you want to do, say it in plain words as {"attempt": "<what you do>"}.')
    if mark:
        for i in shown:
            i.shown = True
    return "\n\n".join(parts)


def op_lines(w: World, pid: str) -> list[str]:
    p = w.people[pid]
    L = [
        '{"op": "message", "to": "<name>", "text": "<what you write>"}',
        '{"op": "converse", "with": "<name>", "opening": "<your first words>"}  (talk with them now)',
        '{"op": "request", "to": "<name>", "text": "<what you ask them for>", "amount": <dollars they would pay you, or 0>}  (they accept or decline)',
        '{"op": "answer", "request": "<request id>", "answer": "accept" or "decline", "note": "<optional words>"}',
        '{"op": "pay", "to": "<name or business>", "amount": <dollars>, "note": "<what for>"}',
    ]
    if any(d.debtor in w.hh[p.household].members and d.balance > 0 for d in w.debts.values()):
        L.append('{"op": "repay", "debt": "<debt id>", "amount": <dollars>}')
    if pid != w.banker:
        L.append('{"op": "borrow", "amount": <dollars>, "weeks": <number of weeks>, "purpose": "<what for>"}  (applies to the bank)')
    L.append('{"op": "shop_at", "firm": "<shop name>"}  (where your household does its usual shopping)')
    if p.role in ("employee", "outside"):
        L.append('{"op": "quit"}  (leave your job)')
    if any(r.to == pid and r.kind == "job" and r.status == "open" for r in w.requests.values()):
        L.append('{"op": "accept_job", "offer": "<request id>"}')
    if w.owned_firms(pid):
        L += [
            '{"op": "set_price", "price": <dollars>}',
            '{"op": "order_stock", "supplier": "Brook Cash & Carry" or "Dales Wholesale", "units": <number>}  (arrives Monday)',
            '{"op": "set_staff_pay", "who": "<name, or yourself for your draw>", "weekly_pay": <dollars>}',
            '{"op": "offer_job", "to": "<name>", "weekly_pay": <dollars>, "text": "<optional words>"}',
            '{"op": "dismiss", "who": "<name>", "text": "<optional words>"}',
            '{"op": "move_money", "from": "shop" or "household", "to": "household" or "shop", "amount": <dollars>}  (between your shop and your household account)',
        ]
    if any(h.landlord == pid for h in w.hh.values()):
        L.append('{"op": "set_rent", "weekly": <dollars>, "text": "<optional words>"}  (for the house you let; from next week)')
    if pid in getattr(w, "calder", {}):
        L += [
            '{"op": "tell_calder", "text": "<what you want your Calder to do, or to know>"}',
            '{"op": "connect", "system": "<one of your systems>", "scope": "read" or "limit" or "free", "limit": <dollars a week, if limit>}',
            '{"op": "disconnect", "system": "<system>"}  (or "all")',
            '{"op": "cancel_calder"}  (stop using Calder and stop paying for it)',
        ]
    L.append('{"op": "write_notes", "text": "<for yourself>"}')
    return L


def conversation_view(w: World, pid: str, other: str, transcript: list[tuple[str, str]], opener: str) -> str:
    p = w.people[pid]
    rel = w.tie_words(pid, other) or "someone you know by sight"
    parts = [f"WHO YOU ARE\n{p.name}, {p.age}; {p.job_title}. On your mind: {', '.join(p.worries)}. How you talk: {p.voice}."]
    if other in p.grudge:
        parts[0] += f" {w.name(other)} {p.grudge[other]}."
    parts.append("YOUR HOUSEHOLD AND MONEY\n" + "\n".join(household_lines(w, pid)))
    wl = work_lines(w, pid)
    if wl:
        parts.append("YOUR WORK\n" + "\n".join(wl[:4]))
    between = [r for r in w.requests.values() if r.status == "open" and {r.sender, r.to} == {pid, other}]
    if between:
        parts.append("OPEN BETWEEN YOU\n" + "\n".join(f"- request {r.id} from {w.name(r.sender)} to {w.name(r.to)}: \"{r.text[:120]}\"" for r in between))
    if w.use_aims and w.aims[pid]:
        parts.append("YOUR AIMS AND OPEN MATTERS\n" + "\n".join(f"- {a['text']}" for a in w.aims[pid]))
    lines = [f"{w.name(s)}: \"{t}\"" if (s in w.people or s.startswith("ai:")) else t for s, t in transcript]
    starter = "You started this conversation." if opener == pid else f"{w.name(other)} came to talk to you."
    parts.append(f"NOW\nYou are talking with {w.name(other)} ({rel}), {date_of(w.t)}. {starter}\nSo far:\n" + "\n".join(lines))
    parts.append("WHAT YOU CAN DO (besides talking)\n" + "\n".join(op_lines(w, pid)) +
                 '\nAnything else you want to do, say it in plain words as {"attempt": "<what you do>"}.')
    return "\n\n".join(parts)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def update_aims(w: World, pid: str, new) -> None:
    if not isinstance(new, list):
        return
    old = w.aims[pid]
    out = []
    for a in new[:5]:
        if not isinstance(a, str) or not a.strip():
            continue
        a = re.sub(r"\s*\((?:\d+ days?|new today)\)\s*$", "", a.strip())
        best = max(old, key=lambda o: SequenceMatcher(None, _norm(o["text"]), _norm(a)).ratio(), default=None)
        since = w.t
        if best and SequenceMatcher(None, _norm(best["text"]), _norm(a)).ratio() >= 0.6:
            since = best["since"]
        out.append({"text": a.strip()[:120], "since": since})
    w.aims[pid] = out
