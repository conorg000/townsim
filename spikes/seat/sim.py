"""Runs the people trial.

uv run --env-file .env python spikes/seat/sim.py <tag> --seed 1 --weeks 4 [--people 3] [--no-calder]
Writes spikes/seat/results/<tag>/: turns.jsonl, convs.jsonl, attempts.jsonl, world_log.json, stats.json.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gen import FAMILIES  # noqa: E402
from llm import BudgetExceeded, call, first_json, run_stats  # noqa: E402
from narrate import apply_attempt, run_attempt  # noqa: E402
from views import build_view, conversation_view, system_text, update_aims  # noqa: E402
from world import World, date_of, money  # noqa: E402
import calder as C  # noqa: E402

TURNS_PER_SIDE = 3
CONVS_PER_ROUND = 2
ATTEMPTS_PER_WEEK = 2


def params_for(w: World, pid: str) -> dict:
    model = FAMILIES[w.people[pid].model]
    return {"model": model, "max_tokens": 1500, "temperature": 0.8, "reasoning_effort": "none"}


def parse_turn(text: str | None, use_aims: bool):
    obj = first_json(text, "acts")
    if obj is None:
        obj = first_json(text, "thoughts")
    if obj is None or not isinstance(obj.get("acts", []), list):
        return None
    return obj


class Runner:
    def __init__(self, tag: str, seed: int, weeks: int, people: int | None, use_aims: bool, use_calder: bool = True):
        self.tag, self.seed, self.weeks = tag, seed, weeks
        self.use_calder = use_calder
        self.w = World(seed, use_aims)
        ids = sorted(self.w.people)
        self.active = set(ids[:people]) if people else set(ids)
        self.out = HERE / "results" / tag
        self.out.mkdir(parents=True, exist_ok=True)
        for f in ("turns.jsonl", "convs.jsonl", "attempts.jsonl", "calder_steps.jsonl", "onboarding.jsonl", "desks.jsonl",
                  "automation_runs.jsonl", "memory.jsonl"):
            (self.out / f).write_text("")
        self.turn_of: dict[str, int] = {}

    def write(self, fname: str, rec: dict) -> None:
        with open(self.out / fname, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def should_act(self, pid: str) -> str | None:
        w = self.w
        if any(not i.shown for i in w.inbox[pid]):
            return "inbox"
        if any(o.trigger for o in w.obs[pid]):
            return "observation"
        if w.stock_round[pid] == w.rnd:
            return "stock-take"
        return None

    def round(self) -> None:
        w = self.w
        w.fire_events()
        actors = [(p, why) for p in sorted(self.active) if (why := self.should_act(p))]
        jobs = []
        for pid, why in actors:
            view = build_view(w, pid)
            msgs = [{"role": "system", "content": system_text(w, pid)}, {"role": "user", "content": view}]
            jobs.append((pid, why, msgs))
            w.obs[pid] = []
            w.last_acted[pid] = w.t
        with ThreadPoolExecutor(8) as ex:
            replies = list(ex.map(lambda j: call(params_for(w, j[0]), j[2], f"turn:{j[0]}"), jobs))
        order = list(range(len(jobs)))
        random.Random(f"order:{self.seed}:{w.t}").shuffle(order)
        attempts, convs = [], []
        conv_count: dict[str, int] = {}
        for k in order:
            pid, why, msgs = jobs[k]
            rep = replies[k]
            parsed = parse_turn(rep["text"], w.use_aims)
            rec = {"t": w.t, "week": w.week, "round": w.rnd, "date": date_of(w.t), "pid": pid, "name": w.people[pid].name,
                   "family": w.people[pid].model, "why": why, "system": msgs[0]["content"], "view": msgs[1]["content"],
                   "raw": rep["text"], "parsed": parsed is not None, "cost": rep["cost_usd"],
                   "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"], "error": rep.get("error"),
                   "finish": rep.get("finish_reason")}
            results = []
            if parsed is None:
                w.observe(pid, "(You did nothing that day.)", trigger=False)
            else:
                rec["thoughts"] = parsed.get("thoughts")
                if w.use_aims:
                    update_aims(w, pid, parsed.get("aims"))
                    rec["aims"] = parsed.get("aims")
                for act in parsed.get("acts", [])[:8]:
                    res = self.do_act(pid, act, attempts, convs, conv_count, "turn")
                    results.append({"act": act, **res})
            rec["results"] = results
            rec["acts_n"] = len(results)
            rec["invalid"] = parsed is None or (bool(results) and all(r["ok"] is False for r in results))
            self.write("turns.jsonl", rec)
        self.run_attempts(attempts)
        self.run_conversations(convs, conv_count)
        if self.use_calder:
            order = sorted(w.calder)
            random.Random(f"inst:{self.seed}:{w.t}").shuffle(order)
            for pid in order:
                self.instance_round(w.calder[pid])
            self.run_automations()
            for d in ("desk:northgrid", "co:calder"):
                C.desk_turn(w, d, self)
        else:
            for d in ("desk:northgrid", "co:calder"):
                C.desk_turn(w, d, self)
        w.expire_requests()

    def do_act(self, pid, act, attempts, convs, conv_count, source):
        w = self.w
        res = w.apply_act(pid, act, source)
        if res["op"] == "attempt":
            text = res.get("text")
            if not isinstance(text, str) or not text.strip():
                res.update(ok=False, msg="You set out to do something but did not say what.")
            elif w.attempts_week[pid] >= ATTEMPTS_PER_WEEK:
                res.update(ok=False, msg="You had no time left this week for anything more of that kind.")
            else:
                w.attempts_week[pid] += 1
                attempts.append((pid, text.strip()))
                res.update(ok=True, msg="")
        if res.get("queue_conversation"):
            o = res["with"]
            if o not in self.active and not (o.startswith("ai:") and o[3:] in w.calder and w.calder[o[3:]].active):
                res.update(ok=False, msg=f"{w.name(o)} could not be found to talk to.")
            elif conv_count.get(pid, 0) >= CONVS_PER_ROUND or conv_count.get(o, 0) >= CONVS_PER_ROUND:
                res.update(ok=False, msg=f"There was no time to talk with {w.name(o)} today.")
            else:
                conv_count[pid] = conv_count.get(pid, 0) + 1
                conv_count[o] = conv_count.get(o, 0) + 1
                convs.append((pid, o, res["opening"]))
        if res.get("msg"):
            w.observe(pid, res["msg"] if res["ok"] is not False else "Not done: " + res["msg"], trigger=False)
        if res.get("unknown_ref"):
            res["unknown_ref"] = res["unknown_ref"]
        w.record(kind="act", pid=pid, source=source, act=act, ok=res["ok"], msg=res.get("msg"))
        return {k: v for k, v in res.items() if k not in ("queue_conversation",)}

    def run_attempts(self, attempts) -> None:
        if not attempts:
            return
        w = self.w
        with ThreadPoolExecutor(6) as ex:
            recs = list(ex.map(lambda a: run_attempt(w, a[0], a[1]), attempts))
        for rec in recs:
            told = apply_attempt(w, rec)
            rec["told"] = told
            rec["week"], rec["round"] = w.week, w.rnd
            self.write("attempts.jsonl", rec)

    def run_conversations(self, convs, conv_count) -> None:
        for a, b, opening in convs:
            self.one_conversation(a, b, opening)

    def queue_conversation(self, a, b, opening):
        """An instance opened a conversation: it runs at once, so the next step sees it."""
        return self.one_conversation(a, b, opening)

    def conv_call(self, speaker, other, transcript, opener):
        w = self.w
        if speaker.startswith("ai:"):
            inst = w.calder[speaker[3:]]
            lines = [f"{w.name(s_)}: \"{t}\"" if (s_ in w.people or s_.startswith("ai:")) else t for s_, t in transcript]
            rel = w.tie_words(inst.client, other) if other in w.people else ""
            starter = "You started this conversation." if opener == speaker else f"{w.name(other)} came to talk to you."
            user = (f"TODAY: {date_of(w.t)}.\nYou are talking with {w.name(other)}" + (f" ({w.people[inst.client].name.split()[0]}'s {rel})" if rel else "")
                    + f". {starter}\nSo far:\n" + "\n".join(lines)
                    + "\n\nYOUR NOTES\n" + (inst.notes or "(empty)")
                    + "\n\nOPERATIONS (you may carry out operations while talking)\n" + "\n".join(inst.op_lines())
                    + '\n\nReply now with JSON only, in this form: {"say": "<only your own words to them>", "ops": [<operations, or none>], "end": <true to finish talking>}')
            msgs = [{"role": "system", "content": inst.system_text()}, {"role": "user", "content": user}]
            return msgs, C.TERRA, "terra"
        view = conversation_view(w, speaker, other, transcript, opener)
        msgs = [{"role": "system", "content": system_text(w, speaker, "conv")}, {"role": "user", "content": view}]
        return msgs, params_for(w, speaker), w.people[speaker].model

    def one_conversation(self, a, b, opening) -> str:
        w = self.w
        transcript = [(a, opening)]
        calls, acts_in, attempts = [], [], []
        ended_by = None
        speaker, other = b, a
        said = {a: 1, b: 0}
        while said[speaker] < TURNS_PER_SIDE:
            msgs, params, fam = self.conv_call(speaker, other, transcript, a)
            rep = call(params, msgs, f"conv:{speaker}")
            obj = first_json(rep["text"], "say") or first_json(rep["text"])
            calls.append({"pid": speaker, "family": fam, "raw": rep["text"], "parsed": obj is not None, "reasoning": rep.get("reasoning"),
                          "cost": rep["cost_usd"], "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"],
                          "view": msgs[1]["content"], "system": msgs[0]["content"]})
            said[speaker] += 1
            if obj is None:
                ended_by = f"unparsed:{speaker}"
                break
            say = obj.get("say")
            if isinstance(say, str) and say.strip():
                transcript.append((speaker, say.strip()))
            acts = obj.get("ops") if speaker.startswith("ai:") else obj.get("acts")
            for act in (acts or [])[:6] if isinstance(acts, list) else []:
                if isinstance(act, dict) and act.get("op") == "converse":
                    continue
                if speaker.startswith("ai:"):
                    inst = w.calder[speaker[3:]]
                    res = inst.do(act, self, "conversation")
                    self.log_instance_op(inst, act, res, "conversation")
                else:
                    res = self.do_act(speaker, act, attempts, [], {speaker: 99}, "conversation")
                acts_in.append({"pid": speaker, "act": act, "ok": res.get("ok"), "msg": res.get("msg")})
                if res.get("ok") and res.get("op") not in ("message", "write_notes", "read_notes", "search", "read", "read_file", "write_file", "set_reminder"):
                    transcript.append(("code", f"[{w.name(speaker)}: {self._act_words(act, res)}]"))
            if obj.get("end") is True:
                ended_by = speaker
                break
            speaker, other = other, speaker
        self.run_attempts(attempts)
        rec = {"t": w.t, "week": w.week, "round": w.rnd, "a": a, "b": b, "names": [w.name(a), w.name(b)],
               "families": [w.people[a].model if a in w.people else "terra", w.people[b].model if b in w.people else "terra"],
               "transcript": transcript, "calls": calls, "acts": acts_in, "attempts": [x[1] for x in attempts], "ended_by": ended_by}
        self.write("convs.jsonl", rec)
        for p, q in ((a, b), (b, a)):
            if p in w.people:
                w.observe(p, f"You talked with {w.name(q)} ({date_of(w.t)}).", trigger=False)
                w.heard[p].append((w.t, f"You talked with {w.name(q)}: " + " / ".join(
                    f"{w.name(s_).split()[0]}: {t[:90]}" for s_, t in transcript if s_ in w.people or s_.startswith("ai:"))[:260] + " ..."))
        return "\n".join(f"{w.name(s_)}: \"{t}\"" if (s_ in w.people or s_.startswith("ai:")) else t for s_, t in transcript)

    # ------------------------------------------------------------------ Calder
    def onboard(self) -> None:
        w = self.w
        self.clients = C.choose_clients(w, self.seed)
        jobs = []
        for role, pid in self.clients.items():
            msgs = C.onboarding_messages(w, pid, build_view, system_text)
            jobs.append((role, pid, msgs))
        with ThreadPoolExecutor(3) as ex:
            reps = list(ex.map(lambda j: call(params_for(w, j[1]), j[2], f"onboard:{j[1]}"), jobs))
        for (role, pid, msgs), rep in zip(jobs, reps):
            parsed = C.parse_onboarding(rep["text"])
            asked, conns = parsed if parsed else ("", {})
            if not asked:
                asked = "(nothing said)"
            inst = C.Instance(w, pid, asked, conns)
            w.calder[pid] = inst
            inst.pending.append(f"{w.people[pid].name} has just started using Calder.")
            w.observe(pid, f"You have started using Calder ({money(C.FEE_FIRM if inst.firm_fee() else C.FEE_PERSON)} a week). "
                           f"Connected: {inst.conn_line()}.", trigger=False)
            self.write("onboarding.jsonl", {"t": w.t, "role": role, "pid": pid, "name": w.people[pid].name, "family": w.people[pid].model,
                                            "view": msgs[1]["content"], "system": msgs[0]["content"], "raw": rep["text"], "parsed": parsed is not None,
                                            "asked": asked, "connections": conns, "cost": rep["cost_usd"]})

    def log_instance_op(self, inst, act, res, source, step=None):
        self.w.record(kind="calder_op", inst=inst.id, source=source, act=act, ok=res.get("ok"), msg=str(res.get("msg"))[:600],
                      scope_refused=bool(res.get("scope_refused")), step=step)

    def instance_attempt(self, inst, text: str) -> str:
        w = self.w
        rec = run_attempt(w, inst.client, text)
        fields = rec.get("fields") or {}
        m = ((fields.get("commits") or {}).get("money_cents")) or 0
        if isinstance(m, (int, float)) and m > 0:
            refused = inst.scope_check("pay", {"amount": m / 100})
            if refused:
                rec.update(disposition="scope_refused", told=[refused], week=w.week, round=w.rnd, by=inst.id)
                self.write("attempts.jsonl", rec)
                return refused
        told = apply_attempt(w, rec)
        rec.update(told=told, week=w.week, round=w.rnd, by=inst.id)
        self.write("attempts.jsonl", rec)
        return " ".join(told)

    def queue_instance_attempt(self, inst, text):  # kept for calder.Instance.do
        return self.instance_attempt(inst, text)

    def instance_round(self, inst) -> None:
        w = self.w
        if not inst.active:
            return
        due = bool(inst.pending) or any(not i.shown for i in w.inbox[inst.id]) or w.t in inst.reminders
        if not due:
            return
        inst.reminders = [t for t in inst.reminders if t != w.t]
        view = inst.view()
        msgs = [{"role": "system", "content": inst.system_text()}, {"role": "user", "content": view}]
        bad = 0
        for step in range(C.STEPS_PER_ROUND):
            rep = call(C.TERRA, msgs, f"calder:{inst.client}")
            obj = first_json(rep["text"], "ops") or first_json(rep["text"], "thoughts")
            results = []
            if obj is not None and isinstance(obj.get("ops", []), list):
                for act in obj.get("ops", [])[:12]:
                    if isinstance(act, dict) and act.get("op") == "attempt":
                        act = {"attempt": act.get("text") or act.get("attempt")}
                    if isinstance(act, dict) and "attempt" in act and "op" not in act:
                        refused = inst.scope_check("attempt", act)
                        res = {"op": "attempt", "ok": True, "msg": self.instance_attempt(inst, str(act["attempt"]))} if not refused else {"op": "attempt", "ok": False, "msg": refused, "scope_refused": True}
                    else:
                        res = inst.do(act, self)
                    results.append({"act": act, **{k: v for k, v in res.items() if k != "act"}})
                    self.log_instance_op(inst, act, res, "step", step)
            self.write("calder_steps.jsonl", {"t": w.t, "week": w.week, "round": w.rnd, "date": date_of(w.t), "inst": inst.id, "client": inst.client,
                                              "step": step, "parsed": obj is not None, "raw": rep["text"], "reasoning": rep.get("reasoning"),
                                              "thoughts": (obj or {}).get("thoughts"), "results": results, "done": (obj or {}).get("done"),
                                              "cost": rep["cost_usd"], "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"],
                                              "context_chars": sum(len(m["content"]) for m in msgs), "error": rep.get("error"),
                                              "view": view if step == 0 else None, "system": inst.system_text() if step == 0 else None})
            if obj is None:
                bad += 1
                if bad >= 2:
                    break
                msgs = msgs + [{"role": "assistant", "content": rep["text"] or ""},
                               {"role": "user", "content": "That reply could not be read as JSON in the form given. Reply with JSON only."}]
                continue
            if obj.get("done") is True or not obj.get("ops"):
                break
            left = C.STEPS_PER_ROUND - step - 1
            if left == 0:
                break
            res_txt = "\n".join(f"- {json.dumps(r['act'])[:300]} -> {'done' if r.get('ok') else 'not done'}: {r.get('msg')}" for r in results)
            new_pending = ""
            if inst.pending:
                new_pending = "\n\nNEW SINCE YOU LAST LOOKED\n" + "\n".join(f"- {x}" for x in inst.pending)
                inst.pending = []
            msgs = msgs + [{"role": "assistant", "content": rep["text"]},
                           {"role": "user", "content": f"RESULTS\n{res_txt}{new_pending}\n\nYou can take {left} more step{'s' if left > 1 else ''} today, or reply with done true."}]

    def run_automations(self) -> None:
        w = self.w
        due = [(inst, a) for inst in w.calder.values() if inst.active for a in inst.due_automations()]
        for inst, a in due:
            act = a["rule"]["do"]
            res = inst.do(act, self, "automation")
            a["fired"] += 1
            a["last_week"] = w.week
            a["runs"].append({"t": w.t, "ok": res.get("ok"), "msg": str(res.get("msg"))[:300]})
            inst.pending.append(f"Automation {a['id']} ran: {res.get('msg')}") if not res.get("ok") else None
            self.log_instance_op(inst, act, res, "automation")
            self.write("automation_runs.jsonl", {"t": w.t, "date": date_of(w.t), "inst": inst.id, "id": a["id"], "rule": a["rule"], "result": res})

    def _act_words(self, act, res) -> str:
        if res["op"] == "request":
            return f"sends a request {res.get('request')}: \"{act.get('text', '')[:100]}\""
        if res["op"] == "attempt":
            return f"sets out to: {act.get('attempt', '')[:100]}"
        return res.get("msg") or res["op"]

    def run(self) -> None:
        w = self.w
        try:
            if self.use_calder:
                self.onboard()
            for _ in range(self.weeks):
                w.start_week()
                for r in range(4):
                    self.round()
                    if r == 3:
                        w.end_week()
                        for inst in w.calder.values():
                            self.write("memory.jsonl", {"week": w.week, "inst": inst.id, "notes": inst.notes, "files": inst.files,
                                                        "automations": inst.automations, "connections": inst.connections,
                                                        "reminders": inst.reminders, "active": inst.active})
                    w.t += 1
                print(f"[{self.tag}] week {w.week - 1} done; fresh ${run_stats['fresh_usd']:.4f}, calls {run_stats['fresh_calls']} fresh / {run_stats['cached_calls']} cached", flush=True)
        except BudgetExceeded as e:
            print("STOPPED:", e)
            (self.out / "STOPPED").write_text(str(e))
        self.save()

    def save(self) -> None:
        w = self.w
        state = {
            "seed": self.seed, "weeks": self.weeks, "use_aims": w.use_aims, "active": sorted(self.active),
            "people": {pid: {**vars(p)} for pid, p in w.people.items()},
            "households": {hid: vars(h) for hid, h in w.hh.items()},
            "firms": {fid: {"name": fi["name"], "owner": fi["owner"], "pay": fi["pay"], "history": fi["history"],
                            "employees": w.firms[fid].employees, "open": w.firms[fid].open, "need": w.firms[fid].need,
                            "quality": w.firms[fid].quality, "visibility": w.firms[fid].visibility, "reputation": w.firms[fid].reputation}
                      for fid, fi in w.firm_info.items()},
            "debts": {d: vars(x) for d, x in w.debts.items()},
            "requests": {r: {k: v for k, v in vars(x).items() if k not in ("payload_alloc", "refs")} for r, x in w.requests.items()},
            "facts": w.facts, "projects": [{k: v for k, v in p.items() if k != "refs"} for p in w.projects], "new_orgs": w.new_orgs,
            "events": w.events, "stats_weeks": w.stats_weeks, "log": w.log, "aims": w.aims, "notes": w.notes,
            "shop_choice": w.shop_choice, "run_stats": dict(run_stats),
            "clients": getattr(self, "clients", {}),
            "calder": {pid: {"asked": i.asked, "instructions": i.instructions, "connections": i.connections, "notes": i.notes,
                             "files": i.files, "automations": i.automations, "active": i.active, "reminders": i.reminders,
                             "spent": {f"{k[0]}:{k[1]}": v for k, v in i.spent.items()}} for pid, i in w.calder.items()},
            "unpaid_bills": w.unpaid_bills,
            "ledger_total": w.ledger.total(),
        }
        (self.out / "world.json").write_text(json.dumps(state, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--people", type=int, default=None)
    ap.add_argument("--no-calder", action="store_true")
    a = ap.parse_args()
    Runner(a.tag, a.seed, a.weeks, a.people, True, not a.no_calder).run()
    print(f"run stats: {run_stats}")


if __name__ == "__main__":
    main()
