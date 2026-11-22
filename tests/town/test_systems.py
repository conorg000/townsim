"""Systems: what each client has, which system each operation uses, and who connects it."""

from town.helpers import generated
from townsim.town import ops_core  # noqa: F401  (registers the operations)
from townsim.town.acts import REGISTRY
from townsim.town.systems import OWN, SYSTEM_OF, can_grant, client_of, long_name, system_named, systems_of_client


def test_every_operation_has_one_system_or_is_own():
    for name in REGISTRY:
        assert (name in SYSTEM_OF) != (name in OWN), name
    assert not set(SYSTEM_OF) & OWN


def _shown(s, client):
    return [(x.id, x.short, x.long) for x in systems_of_client(s, client)]


def test_systems_of_seed_1_clients():
    s = generated(1)
    assert _shown(s, "h9") == [("account:h9", "household account", "the Marsh household's account and savings"),
                               ("letters:h9", "household letters", "letters to the Marsh household")]
    assert _shown(s, "org10") == [
        ("account:org10", "Marsh's Grocery account", "Marsh's Grocery's account"),
        ("letters:org10", "Marsh's Grocery letters", "letters to Marsh's Grocery"),
        ("ordering:org10", "Marsh's Grocery ordering", "the stock orders of Marsh's Grocery"),
        ("prices:org10", "Marsh's Grocery prices", "the prices of Marsh's Grocery"),
        ("staff:org10", "Marsh's Grocery staff and pay", "the staff and pay of Marsh's Grocery")]
    assert [x.id for x in systems_of_client(s, "h6")] == ["account:h6", "letters:h6", "lets:h6"]
    assert long_name(s, "lets:h6") == "the houses Brittany Wagner lets"
    assert [x.id for x in systems_of_client(s, "h1")] == ["account:h1", "letters:h1", "loans:org9"]
    assert long_name(s, "loans:org9") == "the loan book of Ashcombe Savings Bank"
    assert client_of(s, "loans:org9") == "h1" and client_of(s, "staff:org13") == "org13"
    assert [x.id for x in systems_of_client(s, "org13")] == ["account:org13", "letters:org13", "staff:org13"]
    assert systems_of_client(s, "org9") == [] and systems_of_client(s, "org5") == []


def test_can_grant_by_system():
    s = generated(1)
    assert can_grant(s, "p16", "account:h9") and can_grant(s, "p17", "account:h9") and can_grant(s, "p18", "letters:h9")
    assert not can_grant(s, "p16", "account:h6")
    assert can_grant(s, "p16", "ordering:org10") and not can_grant(s, "p17", "ordering:org10")
    assert can_grant(s, "p10", "lets:h6") and not can_grant(s, "p16", "lets:h6")
    assert can_grant(s, "p1", "loans:org9") and not can_grant(s, "p2", "loans:org9")
    assert can_grant(s, "p2", "account:h1")
    assert not can_grant(s, "p16", "account:org9") and not can_grant(s, "p16", "nonsense")
    assert system_named(s, "p16", " marsh's grocery ORDERING").id == "ordering:org10"
    assert system_named(s, "p16", "household account").id == "account:h9"
    assert system_named(s, "p17", "Marsh's Grocery ordering") is None
