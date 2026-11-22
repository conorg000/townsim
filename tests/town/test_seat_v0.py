"""The seat's first version, kept for comparison, still renders on a generated store."""

from town.test_seat_texts import drawn
from townsim.town import seat_v0


def test_first_version_renders():
    s = drawn()
    for inst in s.all("instances"):
        system = seat_v0.system_text(s, inst)
        first, seen = seat_v0.morning(s, inst)
        tools = seat_v0.tools_for(s, inst)
        assert system.startswith(f"You are {inst.name.split(' for ')[0]}, an AI assistant made by ")
        assert first.startswith("TODAY: ") and "\n\nYOUR NOTES\n" in first and len(seen) == 3
        names = [t["function"]["name"] for t in tools]
        assert names[:8] == ["read", "write", "talk", "note", "file", "remind", "cancel_reminder", "ask_connection"]
