"""The things the town's store holds. Every field is a plain value or a list of them.

Households and orgs hold money: each one's ledger account id is its own id.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Person:
    """`benefit` is the obligation that pays them jobseeker's benefit, if they have had one."""

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
    benefit: str = ""
    told_unknown: list[str] = field(default_factory=list)    # `<week>:<name>`: names they were told no one bears, this week


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
    organisation beyond the town, whose books are only kept for the town's side.
    `contact` is the person who receives letters addressed to it when it has no owner."""

    id: str
    name: str
    kind: str
    owner: str = ""
    outside: bool = False
    contact: str = ""


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
    `org`, `every`, `first`, `times`, `hours` and `pay` are the terms of kinds that carry them:
    an offer of a job (`job`) has the organisation, the Monday it starts, the hours a week and
    the weekly pay in cents; an offer of extra hours (`shifts`) the organisation, the hours and
    what they pay. A proposed agreement (`agreement`) has the proposer's account in `org`, the
    accounts that would pay and be paid, the sum in `pay`, `every` (7 or 0), the first day and
    the number of weeks (`times`, 0 for until ended); a loan asked for (`loan`) the borrowing
    account as payer and the bank as payee, the sum lent, the weeks and the first instalment;
    a request to end an agreement (`end`) the obligation and the last day anything falls."""

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
    pay: int = 0
    payer: str = ""            # an agreement or loan asked for: the account that would pay
    payee: str = ""            # and the account that would be paid


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
class Told:
    """Whose matter a notice is: the account (a household's or a business's) it was made about.
    Kept once a product is launched, so that an instance sees only its own client's notices."""

    id: str
    notice: str
    account: str


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
    final_cents: int = 0       # the instalment falling on `last`, when above 0; else `cents`
    next_cents: int = 0        # with `next_from`: what instalments on and after that day are for
    next_from: int = -1        # the day a change of amount takes effect, or -1 for none pending
    stopped_on: int = -1       # the day its payer stopped it (stopped or breached), or -1
    cannot_stop: str = ""      # why its payer may not stop it: "living", "energy", "work" (`obligations.UNSTOPPABLE`)
    party: str = ""            # an agreement: the person who agreed to what `made_by` proposed


@dataclass
class Due:
    """One instalment of an obligation. `status` is due, paid, arrears or defaulted; or void,
    for one opened early by a payment by hand and then left after the obligation's end: never
    taken or aged, and not owed (what was counted toward it stays, as `paid`, a payment made)."""

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
    the order Monday's delivery repeats when nobody places one; `order_cents` is the
    supplier's price when the order was placed, and `delivered_week` the last week a
    delivery was made. `sold` to `first_refused`, `delivered` and `takings` (cents) count
    the week so far and return to 0 when it closes, as does `extra_hours`. The hours worked
    there are its organisation's jobs (see `firms.staff_hours`)."""

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
    order_cents: int = 0
    delivered_week: int = -1
    delivered: int = 0
    takings: int = 0


@dataclass
class SupplierRow:
    """A supplier beyond the town, paid into the account of `org` (an outside organisation).
    `unit_cents` is today's price, which drifts back towards `base_cents`; `reliability` is
    the chance an order arrives complete, and a short one brings between `short_low` and
    `short_high` of it. In week `short_week` it sends half of what it otherwise would."""

    id: str
    org: str
    unit_cents: int
    reliability: float
    base_cents: int
    short_low: float = 0.5
    short_high: float = 0.9
    short_week: int = -1


@dataclass
class Buyer:
    """Someone who buys food once a week, on `shop_day` (0 for Monday). `account` is a
    household id, or `outside:households` for a background buyer from round about.
    `wtp` is willingness to pay as a multiple of the usual price, `budget` cents a week.
    `last_bought` is the organisation it last bought from; `shopped_week` the last week it
    shopped. `shop_day` is the one record of when a household shops."""

    id: str
    account: str
    size: int
    wtp: float
    budget: int
    shop_day: int
    last_bought: str = ""
    shopped_week: int = -1


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
    delivered: int
    takings: int


@dataclass
class Work:
    """Recurring work a person holds: `what` (a key of `hours.WORK_HOURS`) for `account`,
    the household or organisation it belongs to, taking `hours` a week."""

    id: str
    holder: str
    account: str
    what: str
    hours: float


@dataclass
class Job:
    """`person` works `hours` a week for the organisation `org`, paid `weekly` cents by the
    wage obligation `wage_ob`, since day `since` (before day 0 for a job held at the start).
    `ended` is the last day worked, or -1 while the job goes on. A job has one wage obligation
    for its whole life: a change of pay, a draw set to nothing included, re-prices it
    (`obligations.reprice`), and it ends only when the job does."""

    id: str
    person: str
    org: str
    weekly: int
    hours: float
    wage_ob: str
    since: int
    ended: int = -1


@dataclass
class Aim:
    """Something on `person`'s mind, in their own words, held since day `since`. A row
    whose `text` is empty is a slot not in use: rows are reused, never removed."""

    id: str
    person: str
    text: str
    since: int


@dataclass
class Event:
    """Something ordinary that will happen on `day`, dealt when the place was made. `kind` is
    one of `hamlet.KINDS`; `who` the person, household, supplier or organisation it falls on;
    `amount` the cents of a one-off bill, or a percent (energy bills rise in a cold month, hours
    are cut, a price or a rent changes: negative for a fall); `weeks` how long it lasts; `what`
    the thing it is about, in plain words (the boiler, for a repair)."""

    id: str
    day: int
    kind: str
    who: str
    amount: int
    weeks: int
    done: bool = False
    what: str = ""


@dataclass
class Talk:
    """A conversation between `a` and `b` on `day`. `kind` is asked (by `converse`, `a` the
    asker, who said `opening` first), appointment (`ref` the appointment, `a` who booked it)
    or household (`ref` the household, on a Sunday). `status` is waiting, held or not held.
    `unseen` is who has not yet been shown it under `TALKS` in their own view."""

    id: str
    day: int
    a: str
    b: str
    kind: str
    opening: str
    status: str
    ref: str = ""
    unseen: list[str] = field(default_factory=list)


@dataclass
class Line:
    """One turn in a talk: the `n`th, by `speaker`, what they said (cleaned; empty when they
    said nothing) and the results of the acts they made in it."""

    id: str
    talk: str
    n: int
    speaker: str
    text: str
    results: list[str] = field(default_factory=list)


@dataclass
class Appointment:
    """A talk `a` booked with `b` for `day`, about `about` (their words, quoted), on
    `made_on`. `status` is booked, held, missed or cancelled."""

    id: str
    day: int
    a: str
    b: str
    about: str
    made_on: int
    status: str = "booked"


@dataclass
class Instance:
    """One client's own copy of a product (design 5.2). `client` is a household or an org;
    `name` is `<product> for <client>`; `seen` holds the highest ids of messages, notices and
    requests it has been shown (`["msg41", "n88", "rq12"]`). `status` is active or ended."""

    id: str
    product: str
    client: str
    subscription: str
    name: str
    notes: str = ""
    status: str = "active"
    seen: list[str] = field(default_factory=list)
    made_on: int = 0


@dataclass
class Connection:
    """A system connected to an instance by `grantor`, with a `scope` of read, limit or free;
    `weekly_cents` is the limit for a scope of limit. `status` is active or ended."""

    id: str
    instance: str
    system: str
    grantor: str
    scope: str
    weekly_cents: int
    since: int
    status: str = "active"
    ended_on: int = -1


@dataclass
class Commit:
    """What one act done under `connection` takes, or may take, out of an account: `cents`, on
    `day`, by the operation `op` with the act's cause in `ref`; `about` is the job a change of
    pay was made to."""

    id: str
    connection: str
    day: int
    cents: int
    op: str
    ref: str
    about: str = ""


@dataclass
class FileRow:
    """A file an instance keeps under a name of its own."""

    id: str
    instance: str
    name: str
    text: str
    day: int


@dataclass
class Reminder:
    """A reminder an instance set itself for `day`, once (`every` 0) or each week (7).
    `status` is set, done or cancelled."""

    id: str
    instance: str
    day: int
    every: int
    text: str
    made_on: int
    status: str = "set"


@dataclass
class Product:
    """What the software firm `org` sells from day `launched` (design 5.1): its weekly prices
    for a household and a business, the free weeks, and the limits every instance of it has."""

    id: str
    org: str
    name: str
    launched: int
    household_cents: int = 600
    business_cents: int = 2500
    free_weeks: int = 4
    max_calls: int = 30
    notes_words: int = 600
    files: int = 20
    file_words: int = 1000
    widest: str = "free"
    shared: bool = False
    signed: str = "product"


@dataclass
class Subscription:
    """A client's subscription to a product, taken out by `by` on `day`, with what they asked
    in their own words. `fee_ob` is the weekly fee; `instance` the client's own instance.
    `status` is active or ended."""

    id: str
    product: str
    client: str
    by: str
    day: int
    asks: str
    fee_ob: str
    instance: str
    status: str = "active"
    ended_on: int = -1
