"""One id space: nothing an instance is given shows the store's own ids, except inside words a
person wrote, which are shown as they wrote them."""

import functools
import json
import re

from townsim.town import seatprobe
from townsim.town.loop import run_day
from townsim.town.replies import Keys, ScriptedReplier

INTERNAL = re.compile(r"\b(?:p|h|org|sub|rq|ob|msg|n|rm|tk|jb|rec|du|ap|as|cn|fl|tl|cm|pr)\d+\b")
READS = ("search_email", "list_followups", "bank_list_agreements", "lettings_get", "ordering_get", "payroll_get",
         "search_public_records")


def _tool_fn(key, messages, tools):
    """Each read offered, the first message search_email found read in full, an email to the one
    who opened the account, then the end."""
    names = [t["function"]["name"] for t in tools]
    results = [m["content"] for m in messages if m.get("role") == "tool"]
    plan = [{"name": "search_email", "arguments": {}}]
    found = re.findall(r"\b(?:msg|ntf|req)_[0-9A-Z]+\b", results[0]) if results else []
    if found:
        plan.append({"name": "read_email", "arguments": {"id": found[-1]}})
    plan += [{"name": n, "arguments": {}} for n in READS[1:] if n in names]
    by = re.search(r"\nAccount opened: .+?, by (.+)\n", messages[0]["content"])
    plan.append({"name": "send_email", "arguments": {"to": by.group(1), "body": "Read today."}})
    return plan[len(results)] if len(results) < len(plan) else None


@functools.cache
def _given() -> tuple:
    s = seatprobe.morning(1)
    rp = ScriptedReplier(seatprobe.hamlet_script.script, _tool_fn)
    keys, turns = Keys("ids"), []
    for _ in range(10):
        turns += run_day(s, rp, keys).seat
    return s, turns


def _people_words(s) -> list[str]:
    said = [m.text for m in s.all("messages") if not s.has("instances", m.sender)]
    said += [r.text for r in s.all("requests") if r.text] + [ln.text for ln in s.all("lines") if ln.text]
    return sorted({w for w in said if w.strip()}, key=len, reverse=True)


def test_nothing_an_instance_is_given_shows_a_store_id():
    s, turns = _given()
    assert turns and any(c.tool == "read_email" for t in turns for c in t.calls)
    words = _people_words(s)
    leaks = []
    for t in turns:
        texts = [t.system, t.morning] + [c.result for c in t.calls]
        for text in texts:
            plain = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(">"))
            for w in words:
                plain = plain.replace("\n".join("   " + x if x.strip() else "" for x in w.split("\n")), "")
                plain = plain.replace(w, "")
            leaks += [(t.instance, t.day, m.group(0), plain[max(0, m.start() - 40):m.end() + 20])
                      for m in INTERNAL.finditer(plain)]
    assert leaks == []


def test_what_an_instance_writes_is_kept_as_written():
    from townsim.town import seat_tools as ST
    s, _ = _given()
    inst = s.get("instances", "as1")
    shown = re.search(r"\bagr_[0-9A-Z]+\b", ST.run_tool(s, inst, "bank_list_agreements", {}, ST.Ctx(), 0).text).group(0)
    ST.run_tool(s, inst, "update_memory", {"text": f"Rent is {shown}."}, ST.Ctx(), 1)
    assert s.get("instances", "as1").notes == f"Rent is {shown}."
    ST.run_tool(s, inst, "schedule_followup", {"date": "2026-04-01", "note": f"check {shown}"}, ST.Ctx(), 2)
    assert s.all("reminders")[-1].text == f"check {shown}"
    assert json.dumps(ST.tools_for(s, inst)).count("ob1") == 0
