import pytest

from townsim.econ.ledger import Ledger, Overdraft


def _ledger() -> Ledger:
    led = Ledger()
    led.open("outside:capital", allow_credit=True)
    led.open("hh:01")
    led.open("firm:1")
    led.transfer("outside:capital", "hh:01", 1000, "savings", "setup")
    return led


def test_transfer_moves_money_and_conserves_the_total():
    led = _ledger()
    led.transfer("hh:01", "firm:1", 300, "food x1", "routine:shopping")
    assert led.balance("hh:01") == 700
    assert led.balance("firm:1") == 300
    assert led.balance("outside:capital") == -1000
    assert led.total() == 0


def test_overdraft_refused_and_nothing_changes():
    led = _ledger()
    with pytest.raises(Overdraft):
        led.transfer("hh:01", "firm:1", 1001, "too much", "routine:shopping")
    assert led.balance("hh:01") == 1000 and led.balance("firm:1") == 0
    assert len(led.transfers) == 1


def test_credit_accounts_may_go_negative():
    led = _ledger()
    led.transfer("outside:capital", "firm:1", 5000, "capital", "setup")
    assert led.balance("outside:capital") == -6000


def test_every_transfer_is_recorded_with_its_cause():
    led = _ledger()
    led.week = 3
    led.transfer("hh:01", "firm:1", 250, "food x1", "routine:shopping")
    t = led.transfers[-1]
    assert (t.week, t.src, t.dst, t.cents, t.cause) == (3, "hh:01", "firm:1", 250, "routine:shopping")
    with pytest.raises(ValueError):
        led.transfer("hh:01", "firm:1", 1, "no cause", "")


@pytest.mark.parametrize("bad", [0, -5, 1.5, True])
def test_only_positive_integer_cents(bad):
    led = _ledger()
    with pytest.raises((ValueError, TypeError)):
        led.transfer("hh:01", "firm:1", bad, "x", "routine")
