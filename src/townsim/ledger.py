"""The only way money moves between accounts. Money is never created or destroyed."""

from townsim.world import Firm, Newspaper, Person, Provider, Treasury, World

Account = Person | Firm | Treasury | Newspaper | Provider


class InsufficientFunds(Exception):
    pass


def _account(world: World, account_id: str) -> Account:
    if account_id == "treasury":
        return world.treasury
    if account_id == "newspaper":
        return world.newspaper
    if account_id == "provider" and world.provider is not None:
        return world.provider
    if account_id in world.people:
        return world.people[account_id]
    if account_id in world.firms:
        return world.firms[account_id]
    raise KeyError(f"unknown account: {account_id!r}")


def transfer(world: World, src: str, dst: str, amount: int, reason: str) -> None:
    """Move `amount` cents from `src` to `dst`. State is unchanged if this raises."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError(f"amount must be int cents, got {type(amount).__name__}")
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount} ({reason})")
    source = _account(world, src)
    destination = _account(world, dst)
    if source.cash < amount:
        raise InsufficientFunds(
            f"{src} has {source.cash}, cannot pay {amount} to {dst} ({reason})"
        )
    source.cash -= amount
    destination.cash += amount
