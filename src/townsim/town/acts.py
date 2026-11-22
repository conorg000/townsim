"""Acts: how a person changes the town (design 3.3).

A person's reply is a list of acts, each naming an operation and its arguments. The
reply is data: `apply_act` applies one act in full or not at all and never raises,
whatever the arguments hold. Operations are `OpSpec`s in `REGISTRY`; `ops_core`
registers the first eight when imported.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from townsim.town import jobs, ties
from townsim.town.store import TABLES, Store, id_key

MAX_CENTS = 100_000_000
_SHOWN = 40
_DOLLARS = re.compile(r"\d{1,3}(,\d{3})+(\.\d*)?|\d+(\.\d*)?|\.\d+", re.ASCII)


@dataclass(frozen=True)
class Act:
    actor: str
    principal: str
    op: Any
    args: Any


@dataclass(frozen=True)
class Outcome:
    """`cause` is the act's own cause tag, which every change it made carries. `beyond` is
    true when an authoriser refused the act: it went past what the actor may do for its
    principal."""

    ok: bool
    msg: str
    act: Act
    cause: str = ""
    beyond: bool = False


@dataclass(frozen=True)
class OpSpec:
    """`check` returns why the act cannot be done (without `Not done:`) or None; it changes
    nothing. `do` makes the change with the cause it is given and returns what the actor is
    told. `order` places the operation's form in the view: by order, then by name. `who`
    is the position it is shown to (see `positions`). `takes_from` says it draws on an
    account a `from` names (see `from_account`). `hours_of`, when given, gives the hours
    an act takes in place of `hours`."""

    name: str
    form: str
    desc: str
    hours: float
    check: Callable[[Store, Act], str | None]
    do: Callable[[Store, Act, str], str]
    order: int
    who: str = "all"
    takes_from: bool = False
    hours_of: Callable[[Store, Act], float] | None = None


REGISTRY: dict[str, OpSpec] = {}

# Operations that only some places have (lane B's): each name, and what says whether a store has it.
# One a store does not have is shown to no one and is no operation there.
GATES: dict[str, Callable[[Store], bool]] = {}


def available(store: Store, name: str) -> bool:
    gate = GATES.get(name)
    return gate is None or bool(gate(store))


def _same(a: OpSpec, b: OpSpec) -> bool:
    """The same spec, even from a reloaded module: same text and same named functions."""
    def where(f: Callable | None) -> tuple:
        return getattr(f, "__module__", None), getattr(f, "__qualname__", None)
    def text(s: OpSpec) -> tuple:
        return (s.name, s.form, s.desc, s.hours, s.order, s.who, s.takes_from,
                where(s.check), where(s.do), where(s.hours_of))
    named = (where(a.check)[1] or "") + (where(a.do)[1] or "") + (where(a.hours_of)[1] or "")
    return text(a) == text(b) and "<lambda>" not in named


def register(spec: OpSpec) -> None:
    """Add an operation. Registering the same spec again is harmless; a different one
    under a name already taken raises ValueError."""
    old = REGISTRY.get(spec.name)
    if old is not None and old is not spec and not _same(old, spec):
        raise ValueError(f"an operation called {spec.name!r} is already registered")
    REGISTRY[spec.name] = spec


def _acting_for_themselves(store: Store, act: Act) -> str | None:
    if act.principal != act.actor:
        return "you have no connection that allows you to act for them."
    return None


AUTHORISERS: list[Callable[[Store, Act], str | None]] = [_acting_for_themselves]   # `connections` puts its own first

# For an act done for someone else, each of these is called before it is done and returns
# what to call with the act's cause once it has been done (`connections.on_behalf`).
ON_BEHALF: list[Callable[[Store, Act], Callable[[str], None]]] = []

# For an act a person does for themselves (not by a rule), each of these is called before it is done and returns
# the hours of recurring work the act does besides its own, what that work is called ("" for none), and what to
# call with the act's cause once it has been done (`recurring.own_work`).
OWN_WORK: list[Callable[[Store, Act], tuple[float, str, Callable[[str], None]]]] = []


def shown(value: Any) -> str:
    """A value from a reply as it is quoted back: a string as written, anything else as
    its repr, cleaned of control and format characters with line breaks as spaces, and cut
    to 40 characters either way."""
    text = value if isinstance(value, str) else repr(value)
    text = "".join(" " if c in "\n\r\t" else c for c in text[:4 * _SHOWN]
                   if c in "\n\r\t" or unicodedata.category(c) not in ("Cc", "Cf", "Cs"))
    return text[:_SHOWN]


def _not_done(act: Act, reason: str, cause: str) -> Outcome:
    return Outcome(False, f"Not done: {reason}", act, cause)


def apply_act(store: Store, act: Act, n: int, via: str = "") -> Outcome:
    """Apply one act in full or not at all. `n` numbers the act within the day's acts.

    The outcome carries the act's cause tag, `op:<actor>:<op>:d<day>.<n>`, or
    `op:<actor>/<principal>:...` for an act done for someone else; for an act naming no
    operation, the tag has `?` in place of the operation's name. Hours are the actor's. An act
    done by something its actor set up (`via`: `auto:<rule>` for a rule, `narr:<task>` for a
    task) carries `<via>:<actor>...:<op>:d<day>.<n>`; one done by a rule takes no hours."""
    spec = REGISTRY.get(act.op.strip().lower()) if isinstance(act.op, str) else None
    if spec is not None and not available(store, spec.name):
        spec = None
    person = isinstance(act.actor, str) and store.has("people", act.actor)
    held = _positions_or_none(store, act.actor if person else act.principal)
    outside = spec is not None and spec.who != "all" and spec.who not in held
    if outside and person:
        spec = None   # an operation outside the actor's positions is one they do not have
    who = act.actor if act.principal == act.actor else f"{act.actor}/{act.principal}"
    cause = f"{via or 'op'}:{who}:{spec.name if spec is not None else '?'}:d{store.day}.{n}"
    if not isinstance(act.args, dict):
        return _not_done(act, "that operation was not written in a form that can be read.", cause)
    if spec is None:
        return _not_done(act, f"there is no operation called '{shown(act.op)}'.", cause)
    before = store.mark()
    try:
        for authorise in AUTHORISERS:
            reason = authorise(store, act)
            if reason is not None:
                return Outcome(False, f"Not done: {reason}", act, cause, beyond=True)
        if outside:                    # done for a client, outside the positions of the one it is done for
            return _not_done(act, f"there is no operation called '{shown(act.op)}'.", cause)
        hours = 0.0
        own: list[Callable[[str], None]] = []
        if person and not via.startswith("auto:"):     # an instance's acts, and a rule's, take no one's hours
            hours = spec.hours if spec.hours_of is None else spec.hours_of(store, act)
            work = ""
            if act.actor == act.principal:
                for f in OWN_WORK:
                    more, called, then = f(store, act)
                    hours, work, own = hours + more, called or work, own + [then]
            if store.get("people", act.actor).hours_left < hours:
                from townsim.town.hours import no_time          # hours reads the jobs this module reads
                return _not_done(act, no_time(store, act.actor, hours, work), cause)
        reason = spec.check(store, act)
        if reason is not None:
            return _not_done(act, reason, cause)
        after = [f(store, act) for f in ON_BEHALF] if act.actor != act.principal else own
        with store.transaction():
            told = spec.do(store, act, cause)
            if person:
                left = store.get("people", act.actor).hours_left
                store.set("people", act.actor, cause, hours_left=round(left - hours, 2))
            for f in after:
                f(cause)
    except Exception as e:  # a bug in an operation: the act is undone and reported, never raised
        if store.mark() != before:
            store.undo_to(before)
        store.engine_errors.append(f"{cause}: {type(e).__name__}: {str(e)[:200]}")
        return _not_done(act, "that could not be carried out.", cause)
    return Outcome(True, told, act, cause)


_VIA = ("auto", "narr", "once")


def _cause_parts(cause: str) -> tuple[str, str]:
    """(who, op) an act's cause names: `op:<who>:<op>:...`, or `auto:<rule>:<who>:<op>:...` and
    `narr:<task>:<who>:<op>:...`; ("", "") for any other cause."""
    parts = cause.split(":")
    if parts[0] == "op" and len(parts) > 3:
        return parts[1], parts[2]
    if parts[0] in _VIA and len(parts) > 4:
        return parts[2], parts[3]
    return "", ""


def op_of_cause(cause: str) -> str:
    """The operation an act's cause names, or ""."""
    return _cause_parts(cause)[1]


def actor_of_cause(cause: str) -> str:
    """The actor an act's cause names (not its principal), or ""."""
    return _cause_parts(cause)[0].split("/")[0]


def _positions_or_none(store: Store, pid: Any) -> list[str]:
    """`positions`, or none for an actor who is not a person."""
    return positions(store, pid) if isinstance(pid, str) and store.has("people", pid) else []


def dollars(value: Any) -> Decimal | None:
    """A sum of dollars as written in a reply, exactly: an int, a finite float, or a string
    such as `"$1,308.50"` or `"5."` whose commas only separate thousands. None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip().removeprefix("$").strip()
        return Decimal(text.replace(",", "")) if _DOLLARS.fullmatch(text) else None
    return None


def to_cents(value: Any) -> int | None:
    """Dollars as written in a reply, in cents, rounded half up: `12.5` and `"$1,308.50"` both
    read. None for a bool, a non-number, zero, a negative, or more than 100,000,000 cents."""
    sum_ = dollars(value)
    if sum_ is None or sum_ <= 0 or sum_ > Decimal(MAX_CENTS) / 100:
        return None
    cents = int((sum_ * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return cents if cents > 0 else None


def cut(text: str, n: int = 80) -> str:
    """`text`, or its first `n` characters and `…` when it is longer."""
    return text if len(text) <= n else text[:n] + "\u2026"


_BREAKS = {ord(c): "\n" for c in "\r\v\f\x85\x1c\x1d\x1e\u2028\u2029"}


def clean_text(value: Any) -> str | None:
    """Text a person wrote, without control characters other than newline and tab, and
    without format, private-use, unassigned or lone surrogate characters (which could not be
    encoded to send); every other way of breaking a line (a carriage return alone, a vertical
    tab, a form feed, U+0085, the separators U+001C to U+001E and the line and paragraph
    separators U+2028 and U+2029) becomes a newline, so no words run together and no line
    hides behind another. None for a non-string."""
    if not isinstance(value, str):
        return None
    value = value.replace("\r\n", "\n").translate(_BREAKS)
    return "".join(c for c in value if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf", "Co", "Cn", "Cs"))


LONGEST_WORD = 40
CHARS_A_WORD = 8
LONGEST_NAME = 100


def fits(text: str, words: int) -> bool:
    """Whether text a person wrote is within a limit of `words`: at most that many
    whitespace-separated words, at most eight characters a word in all, and no word longer
    than 40 characters."""
    found = text.split()
    return (len(found) <= words and len(text) <= CHARS_A_WORD * words
            and all(len(w) <= LONGEST_WORD for w in found))


def resolve(store: Store, name: Any, actor: str | None = None) -> str | None:
    """The id of the person, household or org called `name`, ignoring case and surrounding
    spaces; or, given an `actor`, of the one resident whose first name it is (or failing that,
    the one person they know whose first name it is)."""
    if not isinstance(name, str):
        return None
    wanted = name.strip().casefold()
    for table in ("people", "households", "orgs"):
        for row in store.all(table):
            if row.name.casefold() == wanted:
                return row.id
    if actor is None or not wanted or not store.has("people", actor):
        return None
    residents = [p.id for p in store.all("people") if p.id != actor and p.name.casefold().split()[:1] == [wanted]]
    if len(residents) == 1:
        return residents[0]
    bearers = [p for p in knows(store, actor) if store.get("people", p).name.casefold().split()[:1] == [wanted]]
    if len(bearers) == 1:
        return bearers[0]
    found = _by_title(store, wanted, actor) if not bearers else []
    found = found or ([] if bearers else _by_role(store, wanted, actor))
    return found[0] if len(found) == 1 else None


_TITLE = re.compile(r"(?:mr|mrs|ms|miss|dr)\.?\s+([^\s]+)")
_ROLES = {"landlord": ("landlord", "the landlord", "my landlord", "landlady", "the landlady", "my landlady"),
          "employer": ("employer", "my employer", "the employer", "my boss", "the boss"),
          "bank": ("bank", "the bank", "my bank"),
          "banker": ("banker", "the banker", "my banker")}


def _by_title(store: Store, wanted: str, actor: str) -> list[str]:
    """The people `actor` knows whose surname a title and a surname (`Mrs Varley`) names."""
    m = _TITLE.fullmatch(wanted)
    if not m:
        return []
    return [p for p in knows(store, actor) if store.get("people", p).name.casefold().split()[-1:] == [m.group(1)]]


def _by_role(store: Store, wanted: str, actor: str) -> list[str]:
    """Who a role names for `actor` (`the landlord`, `my employer`, `the bank`, `the banker`):
    every one they have that tie with, so that it resolves only when there is one."""
    role = next((r for r, words in _ROLES.items() if wanted in words), None)
    person = store.get("people", actor)
    if role == "landlord":
        lord = store.get("households", person.household).landlord
        return [lord] if lord else []
    if role == "employer":
        found = []
        for j in jobs.held(store, person=actor):
            if store.has("orgs", j.org):
                org = store.get("orgs", j.org)
                if org.owner != actor:
                    who = org.owner if org.owner and store.has("people", org.owner) else org.id
                    if who not in found:
                        found.append(who)
        return found
    banks = [o for o in store.all("orgs") if o.kind == "bank" and not o.outside]
    if role == "bank":
        return [o.id for o in banks]
    if role == "banker":
        return [o.contact for o in banks if o.contact and store.has("people", o.contact)]
    return []


def knows(store: Store, pid: str) -> list[str]:
    """Everyone `pid` knows, in id order: their household, everyone tied to them either way
    today (`ties`), everyone they have written to or had a letter from, and everyone they have
    talked with (a talk held is the record of it)."""
    found = set(store.get("households", store.get("people", pid).household).members)
    found |= ties.tied(store, pid)
    for m in store.all("messages"):
        if m.sender == pid:
            found.add(m.to)
        elif m.to == pid:
            found.add(m.sender)
    for t in _rows(store, "talks"):
        if t.status == "held" and pid in (t.a, t.b):
            found.add(t.b if t.a == pid else t.a)
    return sorted((p for p in found if p != pid and store.has("people", p)), key=id_key)


_TIE_RANKS = {"kin": 1, "partner": 1, "parent": 1, "child": 1, "sibling": 1, "friend": 2,
              "employer": 3, "employee": 3, "coworker": 3,
              "landlord": 4, "tenant": 4, "creditor": 4, "debtor": 4, "banker": 4, "borrower": 4}
UNKNOWN = 6
TIE_RANKS = _TIE_RANKS     # how close each kind of tie is, for those who rank many at once


def _rows(store: Store, table: str) -> list:
    """Every row of `table`, or none if this store has no such table."""
    return store.all(table) if table in TABLES else []


def tie_rank(store: Store, pid: str, other: str) -> int:
    """How close `other` is to `pid`: 0 household, 1 kin (a partner, parent, child or sibling
    in another house is kin), 2 friend, 3 employer, employee or
    coworker, 4 landlord, tenant, creditor, debtor or banker, 5 anyone else known, 6 unknown.
    For an organisation: 3 if they work there or own it, 4 if it is their household's
    landlord or a bank, else 6."""
    person = store.get("people", pid)
    if store.has("orgs", other):
        org = store.get("orgs", other)
        if org.owner == pid or any(j.person == pid and j.org == other for j in _rows(store, "jobs")):
            return 3
        if store.get("households", person.household).landlord == other or org.kind == "bank":
            return 4
        return UNKNOWN
    if store.has("people", other) and store.get("people", other).household == person.household:
        return 0
    ranks = [_TIE_RANKS.get(ties.base(k), 5) for k in ties.kinds(store, pid).get(other, [])]
    if ranks:
        return min(ranks)
    return 5 if other in knows(store, pid) else UNKNOWN


def name_of_who(store: Store, who: str) -> str:
    """The name of a person, household, org or instance."""
    for table in ("people", "households", "orgs", "instances"):
        if store.has(table, who):
            return store.get(table, who).name
    return who


def actor_name(store: Store, who: str) -> str:
    """The name of whoever did an act: a person's name, or an instance's."""
    return name_of_who(store, who)


def is_instance(store: Store, who: object) -> bool:
    """Whether `who` is the id of an instance (of a product, working for a client)."""
    return isinstance(who, str) and store.has("instances", who)


def account_of(store: Store, who: str) -> str:
    """A person's household account; an instance's, its client's; a household's or org's own id."""
    if store.has("people", who):
        return store.get("people", who).household
    if is_instance(store, who):
        return store.get("instances", who).client
    return who


def accounts_of(store: Store, pid: str) -> list[str]:
    """The accounts `pid` controls: their household's, then every org in the town they own,
    in id order. An org outside the town is not theirs to draw on, whoever its owner. An
    instance's are its client's account alone."""
    if is_instance(store, pid):
        return [store.get("instances", pid).client]
    return [store.get("people", pid).household] + [o.id for o in store.all("orgs") if o.owner == pid and not o.outside]


def controls(store: Store, pid: str, account: str) -> bool:
    return account in accounts_of(store, pid)


SAVINGS = "savings"


def savings_of(household: str) -> str:
    """The id of a household's savings account at the town's bank (`savings:<household>`),
    which nothing owed is ever taken from."""
    return f"{SAVINGS}:{household}"


def has_savings(store: Store, household: str) -> bool:
    return store.ledger.has(savings_of(household))


def is_savings(account: str) -> bool:
    return account.startswith(SAVINGS + ":")


def household_of_savings(account: str) -> str:
    return account.removeprefix(SAVINGS + ":")


HOUSEHOLD_WORDS = ("household", "my household", "the household", "home")
SAVINGS_WORDS = ("savings", "my savings", "the savings", "our savings", "household savings", "savings account")
BUSINESS_WORDS = ("business", "shop", "my shop", "the shop", "my business", "the business")


def account_names(store: Store, principal: str) -> list[str]:
    """What `principal`'s own accounts are called: `household`, `savings` when the household has
    savings, and each business they own in the town by its name."""
    household = store.get("people", principal).household
    return (["household"] + ([SAVINGS] if has_savings(store, household) else [])
            + [o.name for o in store.all("orgs") if o.owner == principal and not o.outside])


def account_named(store: Store, principal: str, value: Any, savings: bool = True) -> tuple[str | None, str]:
    """The account of `principal`'s that `value` names, and how it is called; or None and why
    not. `household` (or the household's name) is their household; `savings` the household's
    savings (unless `savings` is false); a business they own in the town by its name, or by
    `business`, `shop` and the like when they own exactly one."""
    household = store.get("people", principal).household
    owned = [o for o in store.all("orgs") if o.owner == principal and not o.outside]
    if isinstance(value, str) and len(value) <= LONGEST_NAME:
        wanted = value.strip().casefold()
        if wanted in HOUSEHOLD_WORDS or wanted == store.get("households", household).name.casefold():
            return household, "your household"
        if wanted in SAVINGS_WORDS and has_savings(store, household):
            if not savings:
                return None, "money leaves savings only by a move to another of your own accounts."
            return savings_of(household), "your savings"
        for org in owned:
            if org.name.casefold() == wanted:
                return org.id, org.name
        if wanted in BUSINESS_WORDS and len(owned) == 1:
            return owned[0].id, owned[0].name
    yours = ", ".join(account_names(store, principal))
    return None, f"there is no account called '{shown(value)}'. Yours are: {yours}."


def from_account(store: Store, act: Act) -> tuple[str | None, str | None]:
    """The account the act draws on, or None and why not: no `from` is the principal's
    household; otherwise the account it names (`account_named`). Only reading may name the
    household's savings: nothing is paid from them."""
    value = act.args.get("from") if isinstance(act.args, dict) else None
    household = store.get("people", act.principal).household
    if value is None or (isinstance(value, str) and not value.strip()):
        return household, None
    reading = isinstance(act.op, str) and act.op.strip().lower() == "read"
    account, why = account_named(store, act.principal, value, savings=reading)
    return (account, None) if account is not None else (None, why)


POSITIONS = ("all", "owner", "employed", "landlord", "banker", "client", "market", "company")
YOUR_ACCOUNTS = "<your accounts>"          # in a form: the reader's own accounts, by name (`forms`)


def _alternatives(names: list[str]) -> str:
    """`"a" | "b"`: names as a form offers them, each in double quotation marks."""
    return " | ".join('"' + n.replace('"', "'") + '"' for n in names)


def positions(store: Store, pid: str) -> list[str]:
    """What `pid` is in the town, in the order of `POSITIONS`: everyone is `all`; `owner` of
    an org in the town; `employed` in a job held today; `landlord` to a household, themselves or
    through their household; `banker`; `client`, an adult of a household, or the owner of a
    business, that has an active subscription (an instance); `market` once a product is launched; and
    `company`, the launched product's firm's owner and anyone working there today."""
    person = store.get("people", pid)
    found = ["all"]
    if any(o.owner == pid and not o.outside for o in store.all("orgs")):
        found.append("owner")
    if any(j.person == pid and jobs.current(j, store.day) for j in _rows(store, "jobs")):
        found.append("employed")
    if any(h.landlord in (pid, person.household) for h in store.all("households")):
        found.append("landlord")
    if person.kind == "banker":
        found.append("banker")
    owned = {o.id for o in store.all("orgs") if o.owner == pid and not o.outside}
    adult = person.age >= 18
    if any(i.status == "active" and ((adult and i.client == person.household) or i.client in owned)
           for i in _rows(store, "instances")):
        found.append("client")
    launched = _rows(store, "products")
    if launched:
        found.append("market")
        org = launched[0].org
        if any(o.id == org and o.owner == pid for o in store.all("orgs")) or any(
                j.person == pid and j.org == org and jobs.current(j, store.day) for j in _rows(store, "jobs")):
            found.append("company")
    return found


# Parts of forms that are filled with the reader's own few things: each part, and what gives
# the text in its place for a reader (`forms`). Registered with the operations they belong to.
FILLED: dict[str, Callable[[Store, str], str]] = {}


def forms(store: Store, actor: str) -> list[str]:
    """The written form of every operation open to the actor's positions, by `order` and
    then name, with the reader's own things in place of a placeholder that has a few known
    values for them: `<your accounts>` is their accounts by name (`"household" | "savings" |
    "Lumb's Grocer"`), and each placeholder in `FILLED` its values. For an owner, an
    operation that takes a `from` shows it, naming their accounts it may draw on; reading
    shows it to everyone with savings, which it may name too."""
    held = positions(store, actor)
    owner = "owner" in held
    names = account_names(store, actor)
    drawn = [n for n in names if n != SAVINGS]
    shown_forms = []
    for spec in sorted(REGISTRY.values(), key=lambda spec: (spec.order, spec.name)):
        if spec.who not in held or not available(store, spec.name):
            continue
        form = spec.form.replace(YOUR_ACCOUNTS, _alternatives(names))
        for holder, fill in FILLED.items():
            if holder in form:
                form = form.replace(holder, fill(store, actor))
        if spec.takes_from and form.endswith("}"):
            reads = spec.name == "read"
            some = names if reads else drawn
            if owner or (reads and len(some) > 1):
                form = form[:-1] + ', "from": ' + _alternatives(some) + "}"
        shown_forms.append(form)
    return shown_forms
