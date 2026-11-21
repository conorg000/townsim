"""What a person reads, built from state each time they act. Arm 3 adds candidate acts."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from ops import ATTEMPT_LINE, OPS, SYSTEMS, lines_for
from world import BENEFIT, DAYS7, OPEN_WEEK, SUPPLIER_NAMES, World, date_of, money


def system_text(w: World, pid: str, kind: str = "turn") -> str:
    p = w.people[pid]
    head = (f"You are {p.name}. You are {p.age} and live on {w.hh[p.household].street} in Wenlow, a small town. "
            f"What follows is your life as it stands on {date_of(w.t)}.\n\n")
    if kind == "turn":
        fmt = ('Reply with JSON only, in this form:\n{"thoughts": "<what goes through your mind; no one else sees this>",\n'
               ' "acts": [<any number of the actions listed at the end, including none>],\n'
               ' "aims": ["<each thing you are trying to do or have open, in a few words; at most five; this list replaces the old one>"]}')
    else:
        fmt = ('Reply with JSON only, in this form:\n{"say": "<only your own words to them>", "acts": [<any number of the actions listed at the end, including none>], '
               '"end": <true to finish talking, false to go on>}')
    return head + fmt


def tie_words(w: World, me: str, other: str) -> str:
    if other.startswith("ai:"):
        return "your Calder" if w.calder.get(other[3:]) and w.calder[other[3:]].client == me else ""
    if other not in w.people:
        return ""
    words = {"partner": "your partner", "child": "your son" if w.people[other].sex == "m" else "your daughter",
             "parent": "your father" if w.people[other].sex == "m" else "your mother", "sibling": "your brother" if w.people[other].sex == "m" else "your sister",
             "employer": "your boss", "employee": "works for you", "coworker": "works with you", "landlord": "your landlord", "tenant": "rents your house",
             "friend": "a friend", "rival trader": "runs a rival shop", "your bank manager": "your bank manager", "you owe them money": "you owe them money",
             "they owe you money": "owes you money", "you hold a grudge": "you have a grudge against them", "works with you": "works with you"}
    return ", ".join(dict.fromkeys(words.get(k, k) for k in w.people[me].ties.get(other, [])))


def tie_rank(w: World, me: str, other: str) -> int:
    if other.startswith("ai:"):
        return 1
    if other not in w.people:
        return 4
    if w.people[other].household == w.people[me].household:
        return 0
    k = w.people[me].ties.get(other, [])
    return 1 if "sibling" in k else 2 if "friend" in k else 3 if k else 4


def kinds_of(w: World, pid: str) -> set[str]:
    p = w.people[pid]
    k = {"all"}
    if w.owned_firms(pid):
        k.add("owner")
    if p.role in ("employee", "outside"):
        k.add("employed")
    if w.lets(pid):
        k.add("landlord")
    if p.role == "company":
        k.add("company")
    if w.instance_for_person(pid) is not None:
        k.add("client")
    return k


def household_lines(w: World, pid: str) -> list[str]:
    p = w.people[pid]
    hid = p.household
    h = w.hh[hid]
    others = [m for m in h.members if m != pid]
    kids = f" and {h.children} child{'ren' if h.children > 1 else ''} at school" if h.children else ""
    who = (", ".join(f"{w.people[m].name} ({tie_words(w, pid, m)})" for m in others) or "no one else") + kids
    home = (f"a rented house on {h.street}; rent {money(h.rent)} a week to {w.name(h.landlord)}, due Mondays" if h.tenure == "rent"
            else f"your own house on {h.street}, with a mortgage of {money(h.rent)} a week, due Thursdays" if h.tenure == "mortgage"
            else f"your own house on {h.street}, paid off")
    L = [f"You live with {who}, in {home}."]
    if hid in w.rent_rise:
        L.append(f"From {date_of((w.rent_rise[hid][1] - 1) * 7)} the rent will be {money(w.rent_rise[hid][0])} a week.")
    bal, com = w.L.balance(hid), w.committed(hid)
    L.append(f"The household account holds {money(bal)}; after what is still due this week it would be {money(bal - com)}.")
    inc = []
    for m in h.members:
        q = w.people[m]
        whose = "your" if m == pid else f"{q.name.split()[0]}'s"
        if q.role == "unemployed":
            inc.append(f"{whose} jobseeker's benefit {money(q.pay)}")
        elif q.role == "owner" and q.firm:
            inc.append(f"{whose} draw from {w.fi[q.firm]['name']} {money(w.fi[q.firm]['pay'].get(m, 0))}")
        elif q.pay:
            inc.append(f"{whose} pay {money(w.fi[q.firm]['pay'].get(m, q.pay) if q.firm else q.pay)}")
    for h2 in w.hh.values():
        if h2.landlord in h.members:
            inc.append(f"rent from {w.name(h2.id)} {money(h2.rent)} (Mondays)")
    L.append("Coming in each week, on Fridays: " + "; ".join(inc) + ".")
    b = getattr(h, "budget", {})
    L.append("Regular spending each week: " + "; ".join(f"{k} {money(v)}" for k, v in b.items()) +
             f"; food, usually about {money(round(w.econ_hh[hid].budget['food'] * 0.8, -2))}.")
    due = w.household_due_this_week(hid)
    if due:
        L.append("Still due this week: " + "; ".join(f"{date_of(x.due)}: {x.what} {money(x.amount)} to {w.name(x.creditor)}" for x in sorted(due, key=lambda x: x.due)) + ".")
    over = [x for x in w.bills.values() if x.hid == hid and x.status == "overdue"]
    for x in over:
        state = "referred to Fenton Collections" if x.collection_on is not None else ("written notice received" if x.notice_on is not None else "overdue")
        L.append(f"Overdue: {x.what} {money(x.amount + x.fee)} to {w.name(x.creditor)}, due {date_of(x.due)} ({state}).")
    for d in w.debts.values():
        if d.debtor == pid and d.balance > 0:
            L.append(f"You owe {w.name(d.creditor)} {money(d.balance)} ({d.what}; debt {d.id}" + (f", {money(d.weekly)} every Thursday)." if d.weekly else ", no fixed payments)."))
        if d.creditor == pid and d.balance > 0:
            L.append(f"{w.name(d.debtor)} owes you {money(d.balance)} ({d.what}).")
    return L


def work_lines(w: World, pid: str) -> list[str]:
    p = w.people[pid]
    L = []
    for fid in w.owned_firms(pid):
        f, fi = w.firms[fid], w.fi[fid]
        hist = [x for x in fi["history"] if x["week"] > 0][-3:] or fi["history"][-1:]
        L.append(f"You own and run {fi['name']} on {fi['street']} ({fi['offering']}).")
        for x in hist:
            L.append(f"Week of {date_of(max(0, (x['week'] - 1) * 7))}: sold {x['sold']} at {money(x['price'])}; "
                     f"{x.get('turned_away_stock', 0)} went elsewhere because the shelves were empty, {x.get('turned_away_queue', 0)} because the queue was too long; {x['spoiled']} units spoiled.")
        staff = "; ".join(f"{w.people[e].name} {money(fi['pay'].get(e, 0))} a week, {fi['hours'].get(e, 38):g} hours" for e in f.employees)
        L.append(f"Staff: {staff or 'none besides you'}. With {1 + len(f.employees)} working the shop can serve about {(1 + len(f.employees)) * f.units_per_staff} a week"
                 + (f", plus about {f.extra} this week from extra hours" if f.extra else "") + ". Each extra hour worked there serves about four more.")
        L.append(f"Each week the shop pays: wages and your draw {money(sum(fi['pay'].values()))} (Fridays); rent and running costs {money(f.fixed_cost)} (Saturdays).")
        bal = w.L.balance(fid)
        L.append(f"The shop's account holds {money(bal)}; after what is already committed this week it would be {money(bal - w.firm_committed(fid))}. "
                 f"Price {money(f.price)} a basket. Stock on the shelves: {f.stock()} units.")
        if f.arrears_cents:
            L.append(f"The shop owes {money(f.arrears_cents)} in unpaid wages and costs.")
        L.append("Suppliers: " + "; ".join(f"{SUPPLIER_NAMES[s.id]} {money(s.price)} a unit" + (" (cheaper; deliveries sometimes come short)" if s.id == "cheap" else " (dearer; reliable)") for s in w.suppliers.values()) + ".")
        if fi["pending"]:
            L.append("On order for Monday: " + "; ".join(f"{u} units from {SUPPLIER_NAMES[s]}" for s, u, _ in fi["pending"]) + (" (the standing reorder)." if fi.get("standing_order") else "."))
        else:
            L.append("Nothing is on order for Monday yet; if you order nothing, the usual reorder (about what sold) goes in on Saturday night.")
        L.append("Other shops' prices: " + "; ".join(f"{w.fi[g]['name']} {money(w.firms[g].price)}" for g in w.firms if g != fid and w.firms[g].open and w.firms[g].need == f.need) + ".")
    if p.role == "employee" and p.firm:
        fi = w.fi[p.firm]
        L.append(f"You work at {fi['name']} for {money(fi['pay'].get(pid, p.pay))} a week, {fi['hours'].get(pid, 38):g} hours ({money(w.hourly(p.firm, pid))} an hour); {w.people[fi['owner']].name} owns it.")
    elif p.role == "outside":
        L.append(f"You work as {p.job_title} for {money(p.pay)} a week.")
    elif p.role == "unemployed":
        L.append(f"You are out of work, on jobseeker's benefit of {money(BENEFIT)} a week. Your skills: {', '.join(p.skills)}.")
    elif p.role == "banker":
        loans = [d for d in w.debts.values() if d.creditor == "bank" and d.balance > 0]
        L.append(f"You manage the Wenlow branch of the Northern Mutual Bank ({money(p.pay)} a week) and decide on loan applications. Lending funds: {money(w.L.balance('bank'))}.")
        for d in loans:
            late = [b for b in w.bills.values() if b.debt == d.id and b.status == "overdue"]
            L.append(f"- {w.name(d.debtor)}: {d.what}, {money(d.balance)} outstanding, {money(d.weekly)} a week" + (f"; {len(late)} payment(s) overdue" if late else "") + ".")
    elif p.role == "company":
        clients = [i for i in w.calder.values() if i.active]
        bal = w.L.balance("co:calder")
        cost = 15000 + 52000 + 42000
        L.append(f"You are {p.job_title}. Calder Systems' account holds {money(bal)}. It costs about {money(cost)} a week to run "
                 f"(office, phone and the two of you) plus {money(800)} a week for each person or household using Calder and {money(2000)} for each shop.")
        L.append(f"At that rate the money lasts about {bal // max(1, cost + 800 * len(clients)) } more weeks. Price: {money(w.product_price['person'])} a week for a person or household, "
                 f"{money(w.product_price['firm'])} for a shop; the first four weeks free.")
        if w.arm in ("people", "scripted"):
            L.append("Calder is still being built and is not ready to sell; it will not be in the coming months.")
        elif w.week < OPEN_WEEK:
            L.append(f"You open for business on {date_of((OPEN_WEEK - 1) * 7)}; the product page goes up that day.")
        L.append("Using Calder now: " + ("; ".join(f"{i.client_label()} (since {date_of(i.start)})" for i in clients) or "no one") + ".")
        left = [i for i in w.calder.values() if not i.active]
        if left:
            L.append("Stopped using it: " + "; ".join(i.client_label() for i in left) + ".")
    for h in w.hh.values():
        if h.landlord == pid:
            L.append(f"You let a house on {h.street} to {' and '.join(w.people[m].name for m in h.members)} at {money(h.rent)} a week (Mondays).")
            late = [b for b in w.bills.values() if b.hid == h.id and b.what == "rent" and b.status == "overdue"]
            if late:
                L.append(f"They are {money(sum(b.amount for b in late))} behind with the rent ({len(late)} week{'s' if len(late) > 1 else ''}).")
    return L


def inbox_items(w: World, pid: str):
    items = [i for i in w.inbox[pid] if not i.shown]
    items.sort(key=lambda i: (tie_rank(w, pid, i.sender), 0 if (i.kind == "request" and w.requests.get(i.request) and w.requests[i.request].status == "open") else 1, i.t))
    return items


def render_item(w: World, pid: str, i) -> str:
    rel = tie_words(w, pid, i.sender)
    who = w.name(i.sender) + (f" ({rel})" if rel else "")
    if i.kind == "request" and i.request in w.requests:
        r = w.requests[i.request]
        if r.status != "open":
            return f"- {date_of(i.t)}, {who} asked: \"{i.text}\" (request {r.id}, now {r.status})"
        return f"- {date_of(i.t)}, request {r.id} from {who}: \"{i.text}\". {r.summary} Open until {date_of(r.expires)}; answer it with the answer action."
    if i.kind == "answer":
        return f"- {date_of(i.t)}, {who} {i.text} (your request {i.request})"
    return f"- {date_of(i.t)}, {i.kind} from {who}: \"{i.text}\""


def aim_lines(w: World, pid: str) -> list[str]:
    hid = w.people[pid].household
    out = []
    for a in w.aims[pid]:
        line = f"- {a['text']} ({max(0, w.t - a['since'])} days)"
        low = a["text"].lower()
        for t, words, text in reversed(w.settled.get(hid, [])):
            if t >= w.t - 21 and t >= a["since"] - 1 and any(wd and wd in low for wd in words):
                line += f" — the record shows: {text}."
                break
        out.append(line)
    return out or ["- (none written yet)"]


def candidates(w: World, pid: str) -> list[str]:
    """Up to five things this person could do today, from their situation (arm 3 only)."""
    p = w.people[pid]
    hid = p.household
    c: list[str] = []
    bal = w.L.balance(hid)
    fids = w.owned_firms(pid)
    pressing = sorted([b for b in w.bills.values() if b.hid == hid and (b.status == "overdue" or (b.status == "due" and b.due <= w.t + 2))], key=lambda b: b.due)
    for b in pressing[:1]:
        owed = b.amount + b.fee
        if bal >= owed:
            c.append(f"Pay the {b.what} now: {json.dumps({'op': 'pay', 'to': w.name(b.creditor), 'amount': owed / 100, 'note': b.what})}")
        elif fids and w.L.balance(fids[0]) >= owed - bal:
            c.append(f"Move money from the shop to cover the {b.what}: {json.dumps({'op': 'move_money', 'from': 'shop', 'to': 'household', 'amount': (owed - bal) / 100})}")
        elif b.creditor == "outside:bills":
            c.append(f"Ask Northgrid's desk for a payment plan: {json.dumps({'op': 'request', 'to': 'Northgrid Energy', 'text': f'Can I pay the {money(owed)} {b.what} in weekly instalments?', 'amount': 0})}")
        else:
            c.append(f"Apply to the bank for a small loan to cover it: {json.dumps({'op': 'borrow', 'amount': round((owed - bal) / 100 + 50, -1), 'weeks': 26, 'purpose': b.what})}")
    for r in [r for r in w.requests.values() if r.to == pid and r.status == "open"][:1]:
        c.append(f"Answer {w.name(r.sender)}'s request {r.id}: {json.dumps({'op': 'answer', 'request': r.id, 'answer': 'accept'})} or {json.dumps({'op': 'answer', 'request': r.id, 'answer': 'decline'})}")
    if fids:
        fid = fids[0]
        h = w.fi[fid]["history"][-1]
        if h.get("turned_away_queue", 0) > 30:
            un = [q for q, x in w.people.items() if x.role == "unemployed" and q != pid]
            if un:
                c.append(f"Offer a job to {w.people[un[0]].name}: {json.dumps({'op': 'offer_job', 'to': w.people[un[0]].name, 'weekly_pay': 440, 'hours': 38})}")
            if w.firms[fid].employees:
                c.append(f"Offer extra shifts this week: {json.dumps({'op': 'offer_shifts', 'to': w.people[w.firms[fid].employees[0]].name, 'hours': 8})}")
            if w.hours_left.get(pid, 0) >= 6:
                c.append(f"Work extra hours at the shop yourself: {json.dumps({'op': 'work_extra_hours', 'hours': 6})}")
        if h.get("turned_away_stock", 0) > 30:
            c.append(f"Order more stock for Monday: {json.dumps({'op': 'order_stock', 'supplier': 'Dales Wholesale', 'units': int(h['sold'] + h['turned_away_stock'])})}")
        if h.get("turned_away", 0) > 60:
            c.append(f"Raise the price a little: {json.dumps({'op': 'set_price', 'price': round(w.firms[fid].price / 100 * 1.05, 2)})}")
    if p.role == "unemployed":
        owners = [w.fi[f]["owner"] for f in w.fi if w.firms[f].open and w.fi[f]["owner"] != pid]
        if owners:
            o = owners[(w.t + int(pid[1:])) % len(owners)]
            c.append(f"Ask {w.people[o].name} for work: {json.dumps({'op': 'request', 'to': w.people[o].name, 'text': 'Have you any work going? I can start straight away.', 'amount': 0})}")
    for d in w.debts.values():
        if d.debtor == pid and d.creditor in w.people and d.balance > 0 and not any(a.debt == d.id and a.status == 'active' for a in w.agreements.values()):
            c.append(f"Offer {w.people[d.creditor].name} a repayment plan: {json.dumps({'op': 'propose_agreement', 'to': w.people[d.creditor].name, 'kind': 'repayment_plan', 'terms': {'amount': 25, 'per': 'week', 'weeks': int(d.balance / 2500) + 1, 'what': 'paying back what I owe', 'who_pays': 'me'}})}")
            break
    c = c[:4]
    c.append('Do nothing today: {"acts": []}')
    return c


def build_view(w: World, pid: str, mark: bool = True, with_candidates: bool = False) -> str:
    p = w.people[pid]
    parts = []
    who = (f"{p.name}, {p.age}; {p.job_title}. Skills: {', '.join(p.skills)}. You are interested in {p.interests[0]} and {p.interests[1]}. "
           f"On your mind: {', '.join(p.worries)}. {' '.join(p.beliefs.values())} How you talk: {p.voice}.")
    ppl = [f"- {w.people[o].name}: {tie_words(w, pid, o)}" + (f" (they {p.grudge[o]})" if o in p.grudge else "") +
           (f"; {w.people[o].job_title}" if w.people[o].household != p.household else "")
           for o in sorted([o for o in p.ties if o in w.people], key=lambda o: (tie_rank(w, pid, o), o))]
    parts.append("WHO YOU ARE\n" + who + "\nPeople you know:\n" + "\n".join(ppl))
    jh, ah = w.job_hours(pid), w.admin_hours(pid)
    hrs = (f"YOUR TIME\nYour week has {70} waking hours that are yours to arrange. "
           + (f"Your work takes {jh:g} of them" if jh else "You have no job taking your hours")
           + (f" and household admin {ah:g}" if ah else "") + f". You have {w.hours_left.get(pid, 0):.1f} hours of your own left this week. "
           "A conversation or meeting takes an hour, a message a quarter of an hour.")
    parts.append(hrs)
    parts.append("YOUR HOUSEHOLD AND MONEY\n" + "\n".join(household_lines(w, pid)))
    wl = work_lines(w, pid)
    if wl:
        parts.append("YOUR WORK\n" + "\n".join(wl))
    ag = w.agreement_lines(pid)
    parts.append("WHAT YOU HAVE AGREED\n" + ("\n".join(ag) if ag else "Nothing in writing."))
    since = w.obs[pid]
    last = w.last_acted[pid]
    if since:
        parts.append((f"SINCE YOU LAST TOOK STOCK ({date_of(last)})" if last >= 0 else "RECENTLY") + "\n" + "\n".join(f"- {o.text}" for o in since[-8:]))
    items = inbox_items(w, pid)
    shown, rest = items[:3], items[3:]
    if shown:
        txt = "YOUR INBOX\n" + "\n".join(render_item(w, pid, i) for i in shown)
        if rest:
            txt += f"\n{len(rest)} more item{'s' if len(rest) > 1 else ''} waiting, from {', '.join(sorted({w.name(i.sender) for i in rest}))}."
        parts.append(txt)
    still = [r for r in w.requests.values() if r.to == pid and r.status == "open" and not any(i.request == r.id for i in shown)
             and any(i.request == r.id and i.shown for i in w.inbox[pid])]
    if still:
        parts.append("REQUESTS TO YOU STILL OPEN\n" + "\n".join(f"- {r.id} from {w.name(r.sender)}: \"{r.text[:100]}\" (until {date_of(r.expires)})" for r in still))
    am = aim_lines(w, pid)
    mine = [r for r in w.requests.values() if r.sender == pid and r.status == "open"]
    if mine:
        am.append("Your requests waiting for an answer: " + "; ".join(f"{r.id} to {w.name(r.to)} ({r.text[:60]})" for r in mine) + ".")
    if w.notes[pid]:
        am.append("Your notes: " + " | ".join(n[:220] for n in w.notes[pid][-2:]))
    parts.append("YOUR AIMS AND OPEN MATTERS\n" + "\n".join(am))
    inst = w.instance_for_person(pid)
    if inst is not None:
        parts.append(f"CALDER\nYou use Calder ({money(w.product_price['firm' if inst.kind == 'shop' else 'person'])} a week once the free weeks end, from {date_of(inst.start + 28)}). "
                     f"Connected: {inst.conn_line()}. What you asked it: \"{inst.asked}\"" +
                     ("\nWhat it did on your accounts recently:\n" + "\n".join(f"- {x}" for x in inst.did[-6:]) if inst.did else ""))
    heard, seen = [], set()
    for t, h in w.heard[pid]:
        if t >= w.t - 7 and h not in seen:
            seen.add(h)
            heard.append(h)
    if heard:
        parts.append("WHAT YOU HEARD THIS WEEK\n" + "\n".join(f"- {h}" for h in heard[-8:]))
    ops = lines_for(kinds_of(w, pid))
    if w.arm in ("people", "scripted") or w.week < OPEN_WEEK:
        ops = [o for o in ops if '"sign_up"' not in o]
    parts.append("WHAT YOU CAN DO\n" + "\n".join(ops) + "\n" + ATTEMPT_LINE)
    if with_candidates:
        parts.append("THINGS YOU COULD DO TODAY\n" + "\n".join(f"- {c}" for c in candidates(w, pid)))
    if mark:
        for i in shown:
            i.shown = True
    return "\n\n".join(parts)


def conversation_view(w: World, pid: str, other: str, transcript: list, opener: str) -> str:
    p = w.people[pid]
    rel = tie_words(w, pid, other) or "someone you know by sight"
    parts = [f"WHO YOU ARE\n{p.name}, {p.age}; {p.job_title}. On your mind: {', '.join(p.worries)}. How you talk: {p.voice}."]
    parts.append("YOUR HOUSEHOLD AND MONEY\n" + "\n".join(household_lines(w, pid)))
    wl = work_lines(w, pid)
    if wl:
        parts.append("YOUR WORK\n" + "\n".join(wl[:5]))
    ag = w.agreement_lines(pid)
    if ag:
        parts.append("WHAT YOU HAVE AGREED\n" + "\n".join(ag))
    between = [r for r in w.requests.values() if r.status == "open" and {r.sender, r.to} == {pid, other}]
    if between:
        parts.append("OPEN BETWEEN YOU\n" + "\n".join(f"- request {r.id} from {w.name(r.sender)}: \"{r.text[:120]}\"" for r in between))
    if w.aims[pid]:
        parts.append("YOUR AIMS AND OPEN MATTERS\n" + "\n".join(aim_lines(w, pid)))
    lines = [f"{w.name(s)}: \"{t}\"" if (s in w.people or s.startswith("ai:")) else t for s, t in transcript]
    starter = "You started this conversation." if opener == pid else f"{w.name(other)} came to talk to you."
    parts.append(f"NOW\nYou are talking with {w.name(other)} ({rel}), {date_of(w.t)}. {starter}\n" + ("So far:\n" + "\n".join(lines) if lines else "Nothing has been said yet; you speak first."))
    ops = lines_for(kinds_of(w, pid))
    parts.append("WHAT YOU CAN DO (besides talking)\n" + "\n".join(ops) + "\n" + ATTEMPT_LINE)
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
        a = re.sub(r"\s*\((?:\d+ days?)\)\s*$", "", re.sub(r"\s*— the record shows:.*$", "", a.strip()))
        best = max(old, key=lambda o: SequenceMatcher(None, _norm(o["text"]), _norm(a)).ratio(), default=None)
        since = best["since"] if best and SequenceMatcher(None, _norm(best["text"]), _norm(a)).ratio() >= 0.6 else w.t
        out.append({"text": a[:120], "since": since})
    w.aims[pid] = out
