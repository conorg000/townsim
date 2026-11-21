"""Runs the people trial.

uv run --env-file .env python spikes/people/sim.py <tag> --seed 1 --weeks 4 [--people 3] [--no-aims]
Writes spikes/people/results/<tag>/: turns.jsonl, convs.jsonl, attempts.jsonl, world_log.json, stats.json.
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
from world import World, date_of  # noqa: E402

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
    def __init__(self, tag: str, seed: int, weeks: int, people: int | None, use_aims: bool):
        self.tag, self.seed, self.weeks = tag, seed, weeks
        self.w = World(seed, use_aims)
        ids = sorted(self.w.people)
        self.active = set(ids[:people]) if people else set(ids)
        self.out = HERE / "results" / tag
        self.out.mkdir(parents=True, exist_ok=True)
        for f in ("turns.jsonl", "convs.jsonl", "attempts.jsonl"):
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
            if o not in self.active:
                res.update(ok=False, msg=f"{w.people[o].name} could not be found to talk to.")
            elif conv_count.get(pid, 0) >= CONVS_PER_ROUND or conv_count.get(o, 0) >= CONVS_PER_ROUND:
                res.update(ok=False, msg=f"There was no time to talk with {w.people[o].name} today.")
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
        w = self.w
        # conversations run in the order opened; each is a strict alternation of model calls
        for a, b, opening in convs:
            transcript = [(a, opening)]
            calls = []
            ended_by = None
            acts_in = []
            attempts = []
            speaker, other = b, a
            said = {a: 1, b: 0}
            while said[speaker] < TURNS_PER_SIDE:
                view = conversation_view(w, speaker, other, transcript, a)
                msgs = [{"role": "system", "content": system_text(w, speaker, "conv")}, {"role": "user", "content": view}]
                rep = call(params_for(w, speaker), msgs, f"conv:{speaker}")
                obj = first_json(rep["text"], "say") or first_json(rep["text"])
                calls.append({"pid": speaker, "family": w.people[speaker].model, "raw": rep["text"], "parsed": obj is not None,
                              "cost": rep["cost_usd"], "tokens_in": rep["prompt_tokens"], "tokens_out": rep["completion_tokens"],
                              "view": view, "system": msgs[0]["content"]})
                said[speaker] += 1
                if obj is None:
                    ended_by = f"unparsed:{speaker}"
                    break
                say = obj.get("say")
                if isinstance(say, str) and say.strip():
                    transcript.append((speaker, say.strip()))
                for act in (obj.get("acts") or [])[:6] if isinstance(obj.get("acts"), list) else []:
                    if isinstance(act, dict) and act.get("op") == "converse":
                        continue
                    res = self.do_act(speaker, act, attempts, [], {speaker: 99}, "conversation")
                    acts_in.append({"pid": speaker, "act": act, "ok": res["ok"], "msg": res.get("msg")})
                    if res["ok"] and res["op"] not in ("message", "write_notes"):
                        transcript.append(("code", f"[{w.people[speaker].name}: {self._act_words(act, res)}]"))
                if obj.get("end") is True:
                    ended_by = speaker
                    break
                speaker, other = other, speaker
            self.run_attempts(attempts)
            rec = {"t": w.t, "week": w.week, "round": w.rnd, "a": a, "b": b, "names": [w.people[a].name, w.people[b].name],
                   "families": [w.people[a].model, w.people[b].model], "transcript": transcript, "calls": calls,
                   "acts": acts_in, "attempts": [x[1] for x in attempts], "ended_by": ended_by}
            self.write("convs.jsonl", rec)
            for p, q in ((a, b), (b, a)):
                w.observe(p, f"You talked with {w.people[q].name} ({date_of(w.t)}).", trigger=False)
                w.heard[p].append((w.t, f"You talked with {w.people[q].name}: " + " / ".join(
                    f"{w.people[s].name.split()[0]}: {t[:90]}" for s, t in transcript if s in w.people)[:260] + " ..."))

    def _act_words(self, act, res) -> str:
        if res["op"] == "request":
            return f"sends a request {res.get('request')}: \"{act.get('text', '')[:100]}\""
        if res["op"] == "attempt":
            return f"sets out to: {act.get('attempt', '')[:100]}"
        return res.get("msg") or res["op"]

    def run(self) -> None:
        w = self.w
        try:
            for _ in range(self.weeks):
                w.start_week()
                for r in range(4):
                    self.round()
                    if r == 3:
                        w.end_week()
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
            "ledger_total": w.ledger.total(),
        }
        (self.out / "world.json").write_text(json.dumps(state, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--people", type=int, default=None)
    ap.add_argument("--no-aims", action="store_true")
    a = ap.parse_args()
    Runner(a.tag, a.seed, a.weeks, a.people, not a.no_aims).run()
    print(f"run stats: {run_stats}")


if __name__ == "__main__":
    main()
