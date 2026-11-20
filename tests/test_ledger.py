import pytest

from townsim.ledger import InsufficientFunds, transfer
from townsim.world import total_money


def test_transfer_moves_money_and_conserves_total(tiny_world):
    before = total_money(tiny_world)
    transfer(tiny_world, "p1", "f1", 500, "purchase")
    assert tiny_world.people["p1"].cash == 500
    assert tiny_world.firms["f1"].cash == 1500
    assert total_money(tiny_world) == before


def test_transfer_rejects_overdraft_without_changing_state(tiny_world):
    with pytest.raises(InsufficientFunds):
        transfer(tiny_world, "p1", "treasury", 1001, "tax")
    assert tiny_world.people["p1"].cash == 1000


def test_transfer_rejects_non_positive_amount(tiny_world):
    with pytest.raises(ValueError):
        transfer(tiny_world, "p1", "f1", 0, "x")
