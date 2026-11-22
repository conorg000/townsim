"""Instances, connections and acting for a client (design 4.1)."""

from town.helpers import generated
from townsim.town import acts
from townsim.town.entities import Instance

CAUSE = "routine:setup"


def _instance(s, client: str = "h6", iid: str = "as1") -> str:
    name = f"Kestrel Assistant for the {s.get('households', client).name}" if s.has("households", client) else \
        f"Kestrel Assistant for {s.get('orgs', client).name}"
    s.add("instances", Instance(iid, "pr1", client, "sub1", name, made_on=s.day), CAUSE)
    return iid


def test_instance_rows_round_trip_and_hash_unchanged_without_them():
    s = generated(1)
    before = s.hash()
    assert "instances" not in s.canonical()["tables"]
    mark = s.mark()
    _instance(s)
    assert s.hash() != before and s.get("instances", "as1").seen == []
    s.undo_to(mark)
    assert s.hash() == before


def test_name_of_who_knows_instances():
    s = generated(1)
    _instance(s)
    assert acts.name_of_who(s, "as1") == "Kestrel Assistant for the Varley household"
    assert acts.is_instance(s, "as1") and not acts.is_instance(s, "p10") and not acts.is_instance(s, 7)
