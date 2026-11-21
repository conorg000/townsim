"""A generic ledger: accounts keyed by any string id, integer cents, conserved.

Money is never created or destroyed. Opening balances are moved in from an outside
account that allows credit, so the sum over every account, outside ones included,
is always zero. Every transfer is recorded with a memo and a cause tag (design 3.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class Overdraft(Exception):
    pass


class Transfer(NamedTuple):
    week: int
    src: str
    dst: str
    cents: int
    memo: str
    cause: str


@dataclass
class Account:
    id: str
    balance: int = 0
    allow_credit: bool = False


@dataclass
class Ledger:
    accounts: dict[str, Account] = field(default_factory=dict)
    transfers: list[Transfer] = field(default_factory=list)
    week: int = 0

    def open(self, account_id: str, allow_credit: bool = False) -> Account:
        if account_id in self.accounts:
            raise ValueError(f"account {account_id!r} already exists")
        acct = Account(account_id, 0, allow_credit)
        self.accounts[account_id] = acct
        return acct

    def has(self, account_id: str) -> bool:
        return account_id in self.accounts

    def balance(self, account_id: str) -> int:
        return self.accounts[account_id].balance

    def transfer(self, src: str, dst: str, cents: int, memo: str, cause: str) -> None:
        """Move `cents` from `src` to `dst`. Nothing changes if this raises."""
        if not isinstance(cents, int) or isinstance(cents, bool):
            raise TypeError(f"cents must be int, got {type(cents).__name__}")
        if cents <= 0:
            raise ValueError(f"cents must be positive, got {cents} ({memo})")
        if not cause:
            raise ValueError("every transfer needs a cause tag")
        a = self.accounts[src]
        b = self.accounts[dst]
        if a.balance < cents and not a.allow_credit:
            raise Overdraft(f"{src} has {a.balance}, cannot pay {cents} to {dst} ({memo})")
        a.balance -= cents
        b.balance += cents
        self.transfers.append(Transfer(self.week, src, dst, cents, memo, cause))

    def total(self) -> int:
        return sum(a.balance for a in self.accounts.values())
