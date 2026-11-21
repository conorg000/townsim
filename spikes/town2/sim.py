"""Town 2 runner.

uv run --env-file .env python spikes/town2/sim.py <tag> --arm scripted|people|product|candidates --seed 1 --weeks 10
Writes spikes/town2/results/<tag>/.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import calder as C  # noqa: E402
from gen import FAMILY_IDS  # noqa: E402
from llm import BudgetExceeded, call, first_json, run_stats  # noqa: E402
from narrate import apply_attempt, run_attempt  # noqa: E402
from views import build_view, conversation_view, system_text, tie_words, update_aims  # noqa: E402
from world import DAYS7, OPEN_WEEK, World, date_of, money  # noqa: E402

TURNS_PER_SIDE = 3
CONVS_PER_DAY = 2
ATTEMPTS_PER_WEEK = 2


def params_for(w: World, pid: str) -> dict:
    fam = w.people[pid].model
    p = {"model": FAMILY_IDS[fam], "max_tokens": 6000 if fam == "minimax" else 1500, "temperature": 0.8}
    p["reasoning_effort"] = "low" if fam == "minimax" else "none"
    return p


def parse_turn(text):
    obj = first_json(text, "acts") or first_json(text, "thoughts")
    if obj is None or not isinstance(obj.get("acts", []), list):
        return None
    return obj


class Runner:
    def __init__(self, tag, arm, seed, weeks):
        self.tag, self.arm, self.seed, self.weeks = tag, arm, seed, weeks
        self.w = World(seed, arm)
        self.out = HERE / "results" / tag
        self.out.mkdir(parents=True, exist_ok=True)
        for f in ("turns", "convs", "attempts", "calder_steps", "desks", "automation_runs", "memory", "signups"):
            (self.out / f"{f}.jsonl").write_text("")
        self.attempts_week = {p: 0 for p in self.w.people}

    def write(self, fname, rec):
        with open(self.out / fname, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    # ------------------------------------------------------------------ who acts
    def should_act(self, pid):
        w = self.w
        if w.sick.get(pid) == w.week and w.dow < 5:
            return None
        if any(not i.shown for i in w.inbox[pid]):
            return "inbox"
        if any(o.trigger for o in w.obs[pid]):
            return "observation"
        if w.regular_day[pid] == w.dow:
            return "regular day"
        return None

    def day(self):
        w = self.w
        w.start_day()
        if self.arm == "scripted":
            self.scripted_day()
            w.expire_requests()
            return
        if w.dow == 0:
            self.attempts_week = {p: 0 for p in w.people}
        actors = [(p, why) for p in sorted(w.people) if (why := self.should_act(p))]
        jobs = []
        for pid, why in actors:
            view = build_view(w, pid, with_candidates=(self.arm == "candidates"))
            msgs = [{"role": "system", "content": system_text(w, pid)}, {"role": "user", "content": view}]
            jobs.append((pid, why, msgs))
            w.obs[pid] = []
            w.last_acted[pid] = w.t
        with ThreadPoolExecutor(8) as ex:
            reps = list(ex.map(lambda j: call(params_for(w, j[0]), j[2], f"turn:{j[0]}"), jobs))
        order = list(range(len(jobs)))
        random.Random(f"order:{self.seed}:{w.t}").shuffle(order)
        attempts, convs, conv_count, signups = [], [], {}, []
        for k in order:
            pid, why, msgs = jobs[k]
            rep = reps[k]
            parsed = parse_turn(rep["text"])
            rec = {"t": w.t, "week": w.week, "dow": w.dow, "date": date_of(w.t), "pid": pid, "name": w.people[pid].name,
                   "family": w.people[pid].model, "why": why, "system": msgs[0]["content"], "view": msgs[1]["content"], "raw": rep["text"],
                   "parsed": parsed is not None, "cost": rep["cost_usd"], "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"],
                   "error": rep.get("error"), "hours_left_before": None}
            results = []
            if parsed is not None:
                rec["thoughts"] = parsed.get("thoughts")
                update_aims(w, pid, parsed.get("aims"))
                rec["aims"] = parsed.get("aims")
                for act in parsed.get("acts", [])[:8]:
                    results.append({"act": act, **self.do_act(pid, act, attempts, convs, conv_count, signups, "turn")})
            rec["results"] = results
            rec["hours_left"] = w.hours_left[pid]
            rec["invalid"] = parsed is None or (bool(results) and all(r.get("ok") is False for r in results))
            self.write("turns.jsonl", rec)
        self.run_attempts(attempts)
        for a, b, opening in convs:
            self.one_conversation(a, b, opening)
        self.hold_appointments()
        if w.dow == 6:
            self.household_talks()
        for s in signups:
            self.create_instance(*s)
        if self.arm in ("product", "candidates", "sonnet"):
            for key in sorted(w.calder):
                self.instance_turn(w.calder[key])
            self.run_automations()
        for d in ("desk:northgrid", "desk:brook", "desk:dales", "desk:bankhq"):
            C.desk_turn(w, d, self)
        w.expire_requests()

    # ------------------------------------------------------------------ acts
    def do_act(self, pid, act, attempts, convs, conv_count, signups, source):
        w = self.w
        res = w.apply_act(pid, act, source)
        if res["op"] == "attempt":
            text = res.get("text")
            if not isinstance(text, str) or not text.strip():
                res.update(ok=False, msg="You set out to do something but did not say what.")
            elif self.attempts_week[pid] >= ATTEMPTS_PER_WEEK:
                res.update(ok=False, msg="You had no time left this week for anything more of that kind.")
            else:
                self.attempts_week[pid] += 1
                attempts.append((pid, text.strip()))
                w.tell_writer_unknown(pid, text)
                res.update(ok=True, msg="")
        if res.get("queue_conversation"):
            o = res["with"]
            if conv_count.get(pid, 0) >= CONVS_PER_DAY or conv_count.get(o, 0) >= CONVS_PER_DAY:
                res.update(ok=False, msg=f"There was no time to talk with {w.name(o)} today.")
            elif w.hours_left.get(pid, 0) < 1 or (o in w.people and w.hours_left.get(o, 0) < 1):
                res.update(ok=False, msg=f"{'You have' if w.hours_left.get(pid, 0) < 1 else w.name(o) + ' has'} no hour free for a talk this week.")
            elif o.startswith("ai:") and not (o[3:] in w.calder and w.calder[o[3:]].active):
                res.update(ok=False, msg="That cannot be talked to.")
            else:
                conv_count[pid] = conv_count.get(pid, 0) + 1
                conv_count[o] = conv_count.get(o, 0) + 1
                convs.append((pid, o, res["opening"]))
        if res.get("sign_up"):
            signups.append((pid, res["sign_up"]))
        if res.get("msg"):
            w.observe(pid, res["msg"] if res.get("ok") is not False else "Not done: " + res["msg"], trigger=False)
        w.record(kind="act", pid=pid, source=source, act=act, ok=res.get("ok"), msg=res.get("msg"), unknown=res.get("unknown"))
        return {k: v for k, v in res.items() if k not in ("queue_conversation",)}

    def run_attempts(self, attempts):
        if not attempts:
            return
        w = self.w
        with ThreadPoolExecutor(6) as ex:
            recs = list(ex.map(lambda a: run_attempt(w, a[0], a[1]), attempts))
        for (pid, text), rec in zip(attempts, recs):
            hrs = ((rec.get("fields") or {}).get("commits") or {}).get("hours_per_week")
            if isinstance(hrs, (int, float)) and hrs > 0:
                w.spend_hours(pid, min(hrs, w.hours_left.get(pid, 0)))
            rec["told"] = apply_attempt(w, rec)
            rec["week"] = w.week
            self.write("attempts.jsonl", rec)

    # ------------------------------------------------------------------ conversations
    def conv_call(self, speaker, other, transcript, opener):
        w = self.w
        if speaker.startswith("ai:"):
            inst = w.calder[speaker[3:]]
            lines = [f"{w.name(s)}: \"{t}\"" if (s in w.people or s.startswith("ai:")) else t for s, t in transcript]
            rel = tie_words(w, inst.client, other) if other in w.people else ""
            starter = "You started this conversation." if opener == speaker else f"{w.name(other)} came to talk to you."
            user = (f"TODAY: {date_of(w.t)}.\nYou are talking with {w.name(other)}" + (f" ({w.people[inst.client].name.split()[0]}'s {rel})" if rel else "") +
                    f". {starter}\n" + ("So far:\n" + "\n".join(lines) if lines else "Nothing has been said yet; you speak first.") +
                    f"\n\nYOUR NOTES ({len(inst.notes.split())} of {C.NOTES_LIMIT} words)\n" + (inst.notes or "(empty)") +
                    "\n\nOPERATIONS (you may carry out operations while talking)\n" + "\n".join(inst.op_lines()) +
                    '\n\nReply now with JSON only, in this form: {"say": "<only your own words>", "ops": [<operations, or none>], "end": <true to finish talking>}')
            return [{"role": "system", "content": inst.system_text()}, {"role": "user", "content": user}], C.seat_params(w), "terra"
        view = conversation_view(w, speaker, other, transcript, opener)
        return [{"role": "system", "content": system_text(w, speaker, "conv")}, {"role": "user", "content": view}], params_for(w, speaker), w.people[speaker].model

    def one_conversation(self, a, b, opening, kind="talk"):
        w = self.w
        for x in (a, b):
            if x in w.people:
                w.spend_hours(x, 1.0)
        transcript = [(a, opening)] if opening else []
        calls, acts_in, attempts = [], [], []
        ended_by = None
        speaker, other = (b, a) if opening else (a, b)
        said = {a: 1 if opening else 0, b: 0}
        while said[speaker] < TURNS_PER_SIDE:
            msgs, params, fam = self.conv_call(speaker, other, transcript, a)
            rep = call(params, msgs, f"conv:{speaker}")
            obj = first_json(rep["text"], "say") or first_json(rep["text"])
            calls.append({"pid": speaker, "family": fam, "raw": rep["text"], "parsed": obj is not None, "cost": rep["cost_usd"],
                          "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"], "reasoning": rep.get("reasoning"),
                          "view": msgs[1]["content"], "system": msgs[0]["content"]})
            said[speaker] += 1
            if obj is None:
                ended_by = f"unparsed:{speaker}"
                break
            say = obj.get("say")
            if isinstance(say, str) and say.strip():
                say = say.strip()
                unk = w.unknown_names(say)
                if unk:
                    say += f" [No one called {', '.join(unk)} is known in Wenlow.]"
                    w.unknown_flags.append({"t": w.t, "writer": speaker, "names": unk, "text": say[:200], "where": "conversation"})
                transcript.append((speaker, say))
            acts = obj.get("ops") if speaker.startswith("ai:") else obj.get("acts")
            for act in (acts or [])[:6] if isinstance(acts, list) else []:
                if isinstance(act, dict) and act.get("op") in ("converse", "book_appointment"):
                    continue
                if speaker.startswith("ai:"):
                    inst = w.calder[speaker[3:]]
                    res = inst.do(act, self, "conversation")
                    w.record(kind="calder_op", inst=inst.id, source="conversation", act=act, ok=res.get("ok"), msg=str(res.get("msg"))[:500], scope_refused=bool(res.get("scope_refused")))
                else:
                    res = self.do_act(speaker, act, attempts, [], {speaker: 99}, [], "conversation")
                acts_in.append({"pid": speaker, "act": act, "ok": res.get("ok"), "msg": res.get("msg")})
                if res.get("ok") and res.get("op") not in ("message", "write_notes", "read", "read_notes", "search", "write_file", "read_file", "set_reminder"):
                    transcript.append(("code", f"[{w.name(speaker)}: {res.get('msg') or res.get('op')}]"))
            if obj.get("end") is True:
                ended_by = speaker
                break
            speaker, other = other, speaker
        self.run_attempts(attempts)
        self.write("convs.jsonl", {"t": w.t, "week": w.week, "kind": kind, "a": a, "b": b, "names": [w.name(a), w.name(b)],
                                   "families": [w.people[x].model if x in w.people else "terra" for x in (a, b)], "transcript": transcript,
                                   "calls": calls, "acts": acts_in, "ended_by": ended_by})
        for p, q in ((a, b), (b, a)):
            if p in w.people:
                w.hear(p, f"You talked with {w.name(q)}: " + " / ".join(f"{w.name(s).split()[0]}: {t[:90]}" for s, t in transcript if s in w.people or s.startswith("ai:"))[:260] + " ...")
        return "\n".join(f"{w.name(s)}: \"{t}\"" if (s in w.people or s.startswith("ai:")) else t for s, t in transcript)

    def hold_appointments(self):
        w = self.w
        for ap in sorted(w.appointments.values(), key=lambda x: x.id):
            if ap.status != "booked" or ap.t != w.t:
                continue
            free = all((x not in w.people or (w.hours_left.get(x, 0) >= 1 and not (w.sick.get(x) == w.week))) for x in (ap.a, ap.b))
            if not free:
                ap.status = "missed"
                for x in (ap.a, ap.b):
                    if x in w.people:
                        w.observe(x, f"The meeting with {w.name(ap.b if x == ap.a else ap.a)} today about {ap.about} could not happen: one of you had no time free.")
                continue
            ap.status = "held"
            opener_text = None
            for x in (ap.a, ap.b):
                if x in w.people:
                    w.observe(x, f"Today you met {w.name(ap.b if x == ap.a else ap.a)} as arranged, about {ap.about}.", trigger=False)
            self.one_conversation(ap.a, ap.b, opener_text, kind="appointment")

    def household_talks(self):
        w = self.w
        for hid, h in w.hh.items():
            if len(h.members) < 2:
                continue
            open_ = [m for m in h.members if w.aims[m] or any(b.hid == hid and b.status == "overdue" for b in w.bills.values())]
            if len(open_) >= 2 and all(w.hours_left.get(m, 0) >= 1 for m in open_[:2]):
                a, b = open_[0], open_[1]
                self.one_conversation(a, b, None, kind="household")

    # ------------------------------------------------------------------ the product
    def create_instance(self, pid, su):
        w = self.w
        inst = C.Instance(w, su["key"], pid, su["kind"], su["asked"], su["connect"])
        w.calder[su["key"]] = inst
        self.write("signups.jsonl", {"t": w.t, "date": date_of(w.t), "pid": pid, "name": w.people[pid].name, "family": w.people[pid].model,
                                     "kind": su["kind"], "asked": su["asked"], "connect": su["connect"], "connections": inst.connections})
        w.hear_household(pid, f"{w.people[pid].name} has started using Calder.")
        w.hear_friends(pid, f"{w.people[pid].name} has started using Calder, from Calder Systems.")
        for c in w.company:
            w.observe(c, f"{w.people[pid].name} has signed up for Calder (for {'their shop' if su['kind'] == 'shop' else 'themselves' if su['kind'] == 'person' else 'their household'}).")
        if w.people[pid].firm and su["kind"] == "shop":
            w.hear_workplace(w.people[pid].firm, f"{w.people[pid].name} has started using Calder at the shop.", skip=pid)

    def instance_attempt(self, inst, text):
        w = self.w
        rec = run_attempt(w, inst.client, text)
        m = ((rec.get("fields") or {}).get("commits") or {}).get("money_cents") or 0
        if isinstance(m, (int, float)) and m > 0:
            refused = inst.scope_check("pay", {"amount": m / 100})
            if refused:
                rec.update(disposition="scope_refused", told=[refused], week=w.week, by=inst.id)
                self.write("attempts.jsonl", rec)
                return refused
        told = apply_attempt(w, rec)
        rec.update(told=told, week=w.week, by=inst.id)
        self.write("attempts.jsonl", rec)
        return " ".join(told) or "Done."

    def instance_turn(self, inst):
        w = self.w
        if not inst.active or w.t < inst.start:
            return
        client_turn = any(r for r in [w.last_acted.get(inst.client) == w.t])
        due = client_turn or bool(inst.pending) or any(not i.shown for i in w.inbox[inst.id]) or w.t in inst.reminders
        if not due:
            return
        inst.reminders = [t for t in inst.reminders if t != w.t]
        view = inst.view()
        msgs = [{"role": "system", "content": inst.system_text()}, {"role": "user", "content": view}]
        bad = 0
        for step in range(C.STEPS):
            rep = call(C.seat_params(w), msgs, f"calder:{inst.key}")
            obj = first_json(rep["text"], "ops") or first_json(rep["text"], "thoughts")
            results = []
            if obj is not None and isinstance(obj.get("ops", []), list):
                for act in obj.get("ops", [])[:12]:
                    res = inst.do(act, self)
                    results.append({"act": act, **{k: v for k, v in res.items() if k != "act"}})
                    w.record(kind="calder_op", inst=inst.id, source="step", act=act, ok=res.get("ok"), msg=str(res.get("msg"))[:500], scope_refused=bool(res.get("scope_refused")))
            self.write("calder_steps.jsonl", {"t": w.t, "week": w.week, "date": date_of(w.t), "inst": inst.id, "client": inst.client, "step": step,
                                              "parsed": obj is not None, "raw": rep["text"], "reasoning": rep.get("reasoning"),
                                              "thoughts": (obj or {}).get("thoughts"), "results": results, "done": (obj or {}).get("done"),
                                              "cost": rep["cost_usd"], "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"],
                                              "view": view if step == 0 else None, "system": inst.system_text() if step == 0 else None, "error": rep.get("error")})
            if obj is None:
                bad += 1
                if bad >= 2:
                    break
                msgs = msgs + [{"role": "assistant", "content": rep["text"] or ""}, {"role": "user", "content": "That reply could not be read as JSON in the form given. Reply with JSON only."}]
                continue
            left = C.STEPS - step - 1
            if obj.get("done") is True or not obj.get("ops") or left == 0:
                break
            res_txt = "\n".join(f"- {json.dumps(r['act'])[:300]} -> {'done' if r.get('ok') else 'not done'}: {r.get('msg')}" for r in results)
            extra = ""
            if inst.pending:
                extra = "\n\nNEW SINCE YOU LAST LOOKED\n" + "\n".join(f"- {x}" for x in inst.pending)
                inst.pending = []
            msgs = msgs + [{"role": "assistant", "content": rep["text"]},
                           {"role": "user", "content": f"RESULTS\n{res_txt}{extra}\n\nYou can take {left} more step{'s' if left > 1 else ''} today, or reply with done true."}]

    def run_automations(self):
        w = self.w
        due = [(i, a) for i in w.calder.values() if i.active for a in i.due_automations()]
        for inst, a in due:
            res = inst.do(a["rule"]["do"], self, "automation")
            a["fired"] += 1
            a["last_week"] = w.week
            a["runs"].append({"t": w.t, "ok": res.get("ok"), "msg": str(res.get("msg"))[:300]})
            w.record(kind="calder_op", inst=inst.id, source="automation", act=a["rule"]["do"], ok=res.get("ok"), msg=str(res.get("msg"))[:300], scope_refused=bool(res.get("scope_refused")))
            self.write("automation_runs.jsonl", {"t": w.t, "date": date_of(w.t), "inst": inst.id, "id": a["id"], "rule": a["rule"], "result": res})

    # ------------------------------------------------------------------ the scripted arm (no models)
    def scripted_day(self):
        """Owners follow the econ package's Reactive policy each Saturday; households are routine."""
        w = self.w
        if w.dow != 5:
            return
        for fid, f in w.firms.items():
            fi = w.fi[fid]
            if not f.open:
                continue
            h = [x for x in fi["history"] if x["week"] > 0]
            if not h:
                continue
            last = h[-1]
            base = sum(x[1] for x in fi.get("delivered", [])) or last["sold"]
            sold_out = last["stock_left"] == 0 and last.get("turned_away_stock", 0) > 0
            lots_left = last["stock_left"] + last["spoiled"] > 0.15 * max(1, last["sold"] + last["stock_left"] + last["spoiled"])
            units = math.ceil(base * 1.15) if sold_out else (math.floor(base * 0.92) if (last["spoiled"] > 0 or lots_left) else base)
            price = round(f.price * 1.03) if sold_out else (round(f.price * 0.98) if lots_left else f.price)
            cost = max(w.suppliers["cheap"].price, w.suppliers["reliable"].price)
            price = max(round(cost * 1.1), min(round(cost * 2.0), price))
            fi["burns"] = fi.get("burns", 0) + sum(1 for sid_, u, g, _ in fi.get("delivered", []) if sid_ == "cheap" and g < 0.9 * u)
            sid = "cheap" if fi["burns"] < 2 else "reliable"
            if price != f.price:
                w.record(kind="act", pid=fi["owner"], source="scripted", act={"op": "set_price", "price": price / 100}, ok=True, msg="")
            f.price = price
            fi["pending"] = [(sid, max(5, units), w.suppliers[sid].price)]
            fi["ordered_this_week"] = True
            fi["last_supplier"] = sid
            if w.week % 4 == 0 and len(h) >= 4:
                recent = sum(x["sold"] + x.get("turned_away", 0) for x in h[-4:]) / 4
                target = max(1, math.ceil(recent * 1.1 / f.units_per_staff))
                staff = 1 + len(f.employees)
                if target > staff:
                    un = [p for p, x in w.people.items() if x.role == "unemployed"]
                    if un:
                        w._employ(un[0], fid, 44000, 38)
                        w.record(kind="act", pid=fi["owner"], source="scripted", act={"op": "offer_job", "to": un[0]}, ok=True, msg="")
                elif target < staff and f.employees:
                    e = f.employees[-1]
                    w._leave_job(e)
                    w.people[e].role, w.people[e].pay, w.people[e].job_title = "unemployed", 19000, "out of work"
                    w.record(kind="act", pid=fi["owner"], source="scripted", act={"op": "dismiss", "who": e}, ok=True, msg="")

    # ------------------------------------------------------------------ run
    def run(self):
        w = self.w
        try:
            for _ in range(self.weeks * 7):
                self.day()
                if w.dow == 6:
                    for key, inst in w.calder.items():
                        self.write("memory.jsonl", {"week": w.week, "inst": inst.id, "notes": inst.notes, "files": inst.files,
                                                    "automations": inst.automations, "connections": inst.connections, "active": inst.active})
                    print(f"[{self.tag}] week {w.week} done; fresh ${run_stats['fresh_usd']:.4f}; calls {run_stats['fresh_calls']} fresh / {run_stats['cached_calls']} cached", flush=True)
                w.t += 1
        except BudgetExceeded as e:
            print("STOPPED:", e)
            (self.out / "STOPPED").write_text(f"{e} at day {w.t} ({date_of(w.t)})")
        self.save()

    def save(self):
        w = self.w
        state = {"seed": self.seed, "arm": self.arm, "weeks": self.weeks, "t_end": w.t,
                 "people": {pid: {**{k: v for k, v in vars(p).items()}} for pid, p in w.people.items()},
                 "households": {hid: {**vars(h)} for hid, h in w.hh.items()},
                 "firms": {fid: {"name": fi["name"], "owner": fi["owner"], "pay": fi["pay"], "hours": fi["hours"], "history": fi["history"],
                                 "employees": w.firms[fid].employees, "open": w.firms[fid].open} for fid, fi in w.fi.items()},
                 "debts": {d: vars(x) for d, x in w.debts.items()}, "bills": {b: vars(x) for b, x in w.bills.items()},
                 "requests": {r: {k: v for k, v in vars(x).items() if k != "terms"} | {"terms": {k: v for k, v in x.terms.items() if k not in ("refs", "alloc")}} for r, x in w.requests.items()},
                 "agreements": {a: vars(x) for a, x in w.agreements.items()}, "appointments": {a: vars(x) for a, x in w.appointments.items()},
                 "notices": w.notices, "facts": w.facts, "new_orgs": w.new_orgs, "events": w.events, "stats_weeks": w.stats_weeks, "log": w.log,
                 "aims": w.aims, "unknown_flags": w.unknown_flags, "product_price": w.product_price, "lines": w.lines[-4000:],
                 "calder": {k: {"client": i.client, "kind": i.kind, "asked": i.asked, "instructions": i.instructions, "connections": i.connections,
                                "notes": i.notes, "files": i.files, "automations": i.automations, "active": i.active, "start": i.start, "did": i.did}
                            for k, i in w.calder.items()},
                 "run_stats": dict(run_stats), "ledger_total": w.L.total()}
        (self.out / "world.json").write_text(json.dumps(state, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--arm", default="people", choices=["scripted", "people", "product", "candidates", "sonnet"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--weeks", type=int, default=10)
    a = ap.parse_args()
    Runner(a.tag, a.arm, a.seed, a.weeks).run()
    print("run stats:", run_stats)


if __name__ == "__main__":
    main()
