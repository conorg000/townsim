"""The things the town's store holds. Every field is a plain value or a list of them.

Households and orgs hold money: each one's ledger account id is its own id.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Person:
    id: str
    name: str
    household: str
    hours_week: float
    hours_left: float
    stock_day: int
    notes: str = ""


@dataclass
class Household:
    id: str
    name: str
    members: list[str]


@dataclass
class Org:
    id: str
    name: str
    kind: str


@dataclass
class Message:
    id: str
    day: int
    sender: str
    to: str
    text: str
    seen: bool = False


@dataclass
class Request:
    """A request for money. `status` is open, accepted, declined or lapsed."""

    id: str
    day: int
    sender: str
    to: str
    text: str
    cents: int
    expires: int
    status: str = "open"


@dataclass
class Notice:
    """Something code tells a person."""

    id: str
    day: int
    to: str
    text: str
    seen: bool = False


@dataclass
class Record:
    """An entry on the public register."""

    id: str
    day: int
    kind: str
    about: str
    text: str
