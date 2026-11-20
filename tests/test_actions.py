import random

import pytest

from townsim.actions import ACTIONS, Action, action_from_dict, execute, register
from townsim.events import Event, state_hash
from townsim.ledger import transfer
from townsim.world import World


class Pay(Action):
    """Test-only action: move cash from the actor to another account."""

    kind = "pay"
    to: str
    amount: int

    def check(self, world: World, actor_id: str) -> str | None:
        if self.amount <= 0:
            return "amount must be positive"
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        transfer(world, actor_id, self.to, self.amount, "pay")
        return [
            Event(kind="pay", actor_id=actor_id, payload={"to": self.to, "amount": self.amount}),
            Event(kind="paid", actor_id=self.to, payload={"amount": self.amount}),
        ]


class PayThenCrash(Action):
    """Test-only action: mutates the world, then fails with a non-ledger error."""

    kind = "pay_then_crash"

    def check(self, world: World, actor_id: str) -> str | None:
        return None

    def apply(self, world: World, actor_id: str, rng: random.Random) -> list[Event]:
        transfer(world, actor_id, "f1", 100, "crash")
        world.laws.illegal.add("pay")
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def test_actions_registered():
    """Register the test-only actions for each test, and leave ACTIONS as it was."""
    saved = dict(ACTIONS)
    register(Pay)
    register(PayThenCrash)
    yield
    ACTIONS.clear()
    ACTIONS.update(saved)


def test_execute_applies_and_marks_legality(tiny_world, rng):
    evs = execute(tiny_world, "p1", Pay(to="f1", amount=100), rng)
    assert tiny_world.people["p1"].cash == 900 and evs[0].legal is True
    tiny_world.laws.illegal.add("pay")
    assert execute(tiny_world, "p1", Pay(to="f1", amount=100), rng)[0].legal is False


def test_execute_rejects_overdraft_and_leaves_state_untouched(tiny_world, rng):
    h = state_hash(tiny_world)
    evs = execute(tiny_world, "p1", Pay(to="f1", amount=5000), rng)
    assert evs[0].kind == "action_rejected" and state_hash(tiny_world) == h


def test_action_from_dict_round_trips():
    assert action_from_dict({"kind": "pay", "to": "f1", "amount": 3}) == Pay(to="f1", amount=3)


def test_overdraft_rejection_event_names_action_and_reason(tiny_world, rng):
    (ev,) = execute(tiny_world, "p1", Pay(to="f1", amount=5000), rng)
    assert ev.actor_id == "p1"
    assert ev.payload["action"] == "pay"
    assert "cannot pay 5000" in ev.payload["reason"]


def test_check_reason_rejects_without_applying(tiny_world, rng):
    h = state_hash(tiny_world)
    evs = execute(tiny_world, "p1", Pay(to="f1", amount=0), rng)
    assert evs == [
        Event(
            kind="action_rejected",
            actor_id="p1",
            payload={"action": "pay", "reason": "amount must be positive"},
        )
    ]
    assert state_hash(tiny_world) == h


def test_rejection_restores_the_callers_world_object_in_place(tiny_world, rng):
    @register
    class OverdraftAfterPaying(Action):
        kind = "overdraft_after_paying"

        def check(self, world, actor_id):
            return None

        def apply(self, world, actor_id, rng):
            transfer(world, actor_id, "f1", 100, "first")
            world.step += 1
            transfer(world, actor_id, "f1", 5000, "second")
            return []

    before = tiny_world.model_copy(deep=True)
    evs = execute(tiny_world, "p1", OverdraftAfterPaying(), rng)
    assert evs[0].kind == "action_rejected"
    assert tiny_world == before
    assert tiny_world.people["p1"].cash == 1000 and tiny_world.firms["f1"].cash == 1000


def test_other_exceptions_restore_state_and_propagate(tiny_world, rng):
    before = tiny_world.model_copy(deep=True)
    with pytest.raises(RuntimeError, match="boom"):
        execute(tiny_world, "p1", PayThenCrash(), rng)
    assert tiny_world == before


def test_further_events_keep_legal_true(tiny_world, rng):
    tiny_world.laws.illegal.add("pay")
    evs = execute(tiny_world, "p1", Pay(to="f1", amount=100), rng)
    assert [(e.kind, e.legal) for e in evs] == [("pay", False), ("paid", True)]


def test_legality_is_read_at_execution_time(tiny_world, rng):
    tiny_world.laws.illegal.add("pay")
    assert execute(tiny_world, "p1", Pay(to="f1", amount=1), rng)[0].legal is False
    tiny_world.laws.illegal.discard("pay")
    assert execute(tiny_world, "p1", Pay(to="f1", amount=1), rng)[0].legal is True


def test_register_returns_class_and_rejects_duplicate_kind():
    class Wave(Action):
        kind = "wave"

    assert register(Wave) is Wave and ACTIONS["wave"] is Wave

    class OtherPay(Pay):
        kind = "pay"

    with pytest.raises(ValueError, match="pay"):
        register(OtherPay)
    assert ACTIONS["pay"] is Pay


def test_action_from_dict_unknown_kind_raises():
    with pytest.raises(KeyError):
        action_from_dict({"kind": "no_such_action"})


def test_legality_uses_laws_as_they_were_when_the_action_began(tiny_world, rng):
    @register
    class LegaliseSelf(Action):
        kind = "legalise_self"

        def check(self, world, actor_id):
            return None

        def apply(self, world, actor_id, rng):
            world.laws.illegal.discard(self.kind)
            return [Event(kind=self.kind, actor_id=actor_id)]

    tiny_world.laws.illegal.add("legalise_self")
    assert execute(tiny_world, "p1", LegaliseSelf(), rng)[0].legal is False


def test_apply_must_return_its_own_event_first(tiny_world, rng):
    @register
    class WrongFirstEvent(Action):
        kind = "wrong_first_event"

        def check(self, world, actor_id):
            return None

        def apply(self, world, actor_id, rng):
            transfer(world, actor_id, "f1", 100, "wrong")
            return [Event(kind="something_else")]

    before = tiny_world.model_copy(deep=True)
    with pytest.raises(ValueError, match="wrong_first_event"):
        execute(tiny_world, "p1", WrongFirstEvent(), rng)
    assert tiny_world == before
