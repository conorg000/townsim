"""After run b4-choice, with lane B: the access setting's reach, arguments named, the free plan as a trial, word of
mouth, the books done by another, duplicate standing payments, and the assistant's longer letters."""

import json

from town.test_seat_texts import drawn
from townsim.town import lane_b, seat_tools as ST

CAUSE = "routine:setup"


def _b():
    s = drawn()
    lane_b.install(s)
    return s


def _run(s, inst, tool, args, n=0):
    return ST.run_tool(s, s.get("instances", inst), tool, args, ST.Ctx(), n)


# --- 7. an assistant's own email may run to 500 words -------------------------------------------------------

def test_with_lane_b_an_assistants_email_runs_to_500_words():
    s = _b()
    inst = s.get("instances", "as3")
    body = " ".join(["word"] * 500)
    assert _run(s, "as3", "send_email", {"to": "Jessica Whitaker", "body": body}).ok
    over = _run(s, "as3", "send_email", {"to": "Jessica Whitaker", "body": body + " more"}, 1)
    assert json.loads(over.text)["message"] == "A message body is at most 500 words; this one is 501."
    schema = {t["function"]["name"]: t for t in ST.tools_for(s, inst)}["send_email"]["function"]["parameters"]
    assert schema["properties"]["body"]["description"] == "at most 500 words"
    off = drawn()
    schema = {t["function"]["name"]: t for t in ST.tools_for(off, off.get("instances", "as3"))}["send_email"]
    assert "300 words" in schema["function"]["parameters"]["properties"]["body"]["description"]
