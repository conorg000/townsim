"""The things the town's store holds. Every field is a plain value or a list of them.

Households and orgs hold money: each one's ledger account id is its own id.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Person:
    id: str
    name: str
    household: str
    hours_week: float
    hours_left: float
    stock_day: int
    notes: str = ""
    kind: str = "resident"
    age: int = 40
    family: str = ""
    job: str = ""
    voice: str = ""
    beliefs: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    worries: list[str] = field(default_factory=list)
    ill_until: int = -1


@dataclass
class Household:
    id: str
    name: str
    members: list[str]
    street: str = ""
    shop_day: int = 5
    landlord: str = ""   # the person, household or org it rents from


@dataclass
class Org:
    """`owner` is the person who controls its account, if any; `outside` is an
    organisation beyond the town, whose books are only kept for the town's side."""

    id: str
    name: str
    kind: str
    owner: str = ""
    outside: bool = False


@dataclass
class Message:
    """`org` is the organisation a letter was addressed to, delivered to its owner `to`."""

    id: str
    day: int
    sender: str
    to: str
    text: str
    seen: bool = False
    org: str = ""
    unknown: list[str] = field(default_factory=list)


@dataclass
class Request:
    """A request, with or without money. `status` is open, accepted, declined or lapsed.
    `ob` is the obligation the money asked for is owed on, if the request names one.
    `org`, `every`, `first`, `times` and `hours` are the terms of kinds that carry them."""

    id: str
    day: int
    sender: str
    to: str
    text: str
    cents: int
    expires: int
    status: str = "open"
    kind: str = "ask"
    ob: str = ""
    summary: str = ""
    seen: bool = False
    org: str = ""
    every: int = 0
    first: int = -1
    times: int = 0
    hours: float = 0.0


@dataclass
class Tie:
    """How `person` sees `other`: kin, friend, coworker, employer, employee, landlord,
    tenant, creditor, debtor or banker. One row per direction."""

    id: str
    person: str
    other: str
    kind: str


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


@dataclass
class Obligation:
    """A payment due on a schedule: from `first`, every `every` days (0 for once), to `last`.

    `kind` is in `obligations.KIND_ORDER`. `ends` says who can end it: payer, both or
    service. `status` is active, stopped, breached or finished.
    """

    id: str
    kind: str
    payer: str
    payee: str
    cents: int
    first: int
    every: int
    last: int | None
    memo: str
    ends: str
    made_by: str
    made_on: int
    penalty: bool = False
    status: str = "active"


@dataclass
class Due:
    """One instalment of an obligation. `status` is due, paid, arrears or defaulted."""

    id: str
    obligation: str
    day: int
    cents: int
    paid: int = 0
    status: str = "due"
    fee_charged: bool = False
    notice_sent: bool = False


@dataclass
class Result:
    """What came of one act, told to the person who did it. `op` is the operation's name
    as they wrote it, cut and cleaned; `text` is what they are told."""

    id: str
    day: int
    actor: str
    op: str
    text: str
    ok: bool
    seen: bool = False


@dataclass
class FirmRow:
    """A shop: what it offers for `need`, its stock, its standing order and its week so far.

    `org` is the organisation whose account it trades from. Stock is held in cohorts,
    oldest first: `stock_units[i]` units that arrived in week `stock_weeks[i]`. An order
    placed this week is `order_supplier`/`order_units`; `last_supplier`/`last_units` is
    the order Monday's delivery repeats when nobody places one. `sold` to `first_refused`
    count the week so far and return to 0 when it closes, as does `extra_hours`.
    `staff` stands in for the jobs at the shop until jobs exist."""

    id: str
    org: str
    need: str
    price: int
    quality: float
    visibility: float
    reputation: float
    units_per_staff: int
    shelf_weeks: int
    stock_units: list[int] = field(default_factory=list)
    stock_weeks: list[int] = field(default_factory=list)
    order_supplier: str = ""
    order_units: int = 0
    last_supplier: str = ""
    last_units: int = 0
    sold: int = 0
    turned_stock: int = 0
    turned_capacity: int = 0
    spoiled: int = 0
    first_served: int = 0
    first_refused: int = 0
    extra_hours: float = 0.0
    staff: int = 1


@dataclass
class SupplierRow:
    """A supplier beyond the town, paid into the account of `org` (an outside organisation).
    `unit_cents` is today's price, which drifts back towards `base_cents`; `reliability` is
    the chance an order arrives complete, and a short one brings between `short_low` and
    `short_high` of it."""

    id: str
    org: str
    unit_cents: int
    reliability: float
    base_cents: int = 0
    short_low: float = 0.5
    short_high: float = 0.9


@dataclass
class Buyer:
    """Someone who buys food once a week, on `shop_day` (0 for Monday). `account` is a
    household id, or `outside:households` for a background buyer from round about.
    `wtp` is willingness to pay as a multiple of the usual price, `budget` cents a week.
    `last_bought` is the organisation it last bought from; `shopped_on` the last day it
    shopped."""

    id: str
    account: str
    size: int
    wtp: float
    budget: int
    shop_day: int
    last_bought: str = ""
    shopped_on: int = -1


@dataclass
class TillWeek:
    """One shop's week as it closed."""

    id: str
    firm: str
    week: int
    price: int
    sold: int
    turned_stock: int
    turned_capacity: int
    spoiled: int
    stock_left: int
    cash: int


@dataclass
class Work:
    """Recurring work a person holds: `what` (a key of `hours.WORK_HOURS`) for `account`,
    the household or organisation it belongs to, taking `hours` a week."""

    id: str
    holder: str
    account: str
    what: str
    hours: float
