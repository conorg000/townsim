"""What a person knows of their own life, as facts read from their rows (design 2.8, 3.3).

`who` is the account of a person that opens their view, in the second person: who they
are, where and with whom they live and how the house is held, their work or business,
what their household owes and is owed, the people closest to them, and their beliefs,
interests, worries and voice. `business` is an owner's shop as its books and till show
it. `KNOWHOW` is how a trade works, the same words for everyone in it, with only figures
read from their own shop filled in. Nothing here says what anyone may or should do, and
nothing here changes the store.
"""

from __future__ import annotations

from datetime import timedelta

from townsim.town import firms
from townsim.town.acts import knows, name_of_who
from townsim.town.clock import DAY_ZERO, DAYS, date_text, dow, week
from townsim.town.obligations import LATE_FEE_CENTS, OWED, SCHEDULED, money, name_of, upcoming
from townsim.town.store import Store, id_key

WHO_WORDS = 300
AHEAD = 6                          # "this week": today and the six days after
PEOPLE_SHOWN = 12

# The reader's side of a tie, in plain words.
RELATION = {"partner": "your partner", "parent": "your mother or father", "child": "your son or daughter",
            "sibling": "your brother or sister", "kin": "a relative", "friend": "a friend",
            "coworker": "works with you", "employer": "your employer", "employee": "works for you",
            "landlord": "your landlord", "tenant": "your tenant", "debtor": "owes you money",
            "creditor": "you owe them money", "banker": "the banker"}
_RANK = {"partner": 1, "parent": 1, "child": 1, "sibling": 1, "kin": 1, "friend": 2, "employer": 3,
         "employee": 3, "coworker": 3, "landlord": 4, "tenant": 4, "creditor": 4, "debtor": 4, "banker": 4}
# How a tie seen only from the other side reads from this one.
_OTHER_SIDE = {"partner": "partner", "parent": "child", "child": "parent", "sibling": "sibling", "kin": "kin",
               "friend": "friend", "coworker": "coworker", "employer": "employee", "employee": "employer",
               "landlord": "tenant", "tenant": "landlord", "creditor": "debtor", "debtor": "creditor"}
_ONE_SIDED = {"banker": "has a loan from the bank"}

KNOWHOW: dict[str, list[str]] = {
    "owner": [
        "Stock ordered by Saturday arrives on Monday and is paid for on arrival; with no new order, "
        "last week's order comes again.",
        "Food not sold within {shelf_weeks} weeks of arriving is thrown away.",
        "One member of staff working a full {staff_week}-hour week can serve about {per_staff} customers.",
        "The till's weekly report counts customers turned away for want of stock and for want of staff.",
        "Suppliers sometimes send less than was ordered, and never more than the shop's account can pay for.",
        "A price can be changed at any time.",
    ],
    "banker": [
        "A loan from the bank is repaid in weekly instalments, taken from the borrower's account on their day.",
        f"An instalment the account cannot cover stays owed, and a late fee of {money(LATE_FEE_CENTS)} is added "
        "to it after a week.",
        "After two weeks the borrower is sent a notice that it is overdue.",
        "After four weeks it is recorded on the public register as a default, and it is still owed.",
    ],
    "landlord": [
        "Rent is taken from the tenant's account each week on its day.",
        "A change of rent takes effect four weeks after the tenant is told of it.",
        "The public register records rent four weeks overdue as a default, and an agreement stopped before "
        "its end as a breach.",
    ],
}


def _words(text: str) -> int:
    return len(text.split())


def _and(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def when(day: int) -> str:
    """A day as written, with its year when it is not 2026."""
    year = (DAY_ZERO + timedelta(days=day)).year
    return date_text(day) + ("" if year == DAY_ZERO.year else f" {year}")


def _verb(word: str) -> str | None:
    """A verb as `he` has it, as `you` have it: `works` gives `work`, `fixes` `fix`, `is`
    `are`; None for a word that is not one."""
    if word == "is":
        return "are"
    if len(word) <= 3 or not word.endswith("s") or word.endswith("ss") or not word.isalpha() or not word.islower():
        return None
    if word.endswith("es") and word[:-2].endswith(("sh", "ch", "x", "z", "ss")):
        return word[:-2]
    return word[:-1]


def _second_person(line: str) -> str:
    """A line about someone, as said to them: `works on the till at X` gives `You work on the
    till at X`, and a verb after `and` changes too; a line that does not start with a verb,
    such as `retired`, gives `You are retired`."""
    words = line.split(" ")
    first = _verb(words[0].lower() if words[0][:1].isupper() else words[0])
    if first is None:
        return "You are " + line
    out = [first]
    for prev, w in zip(words, words[1:]):
        out.append((_verb(w) or w) if prev == "and" else w)
    return "You " + " ".join(out)


# --- the people a person knows --------------------------------------------------------


def _tie_kinds(store: Store, pid: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """The kinds of the ties `pid` holds to each person, and of those others hold to `pid`."""
    mine: dict[str, list[str]] = {}
    theirs: dict[str, list[str]] = {}
    for t in store.all("ties"):
        if t.person == pid:
            mine.setdefault(t.other, []).append(t.kind)
        elif t.other == pid:
            theirs.setdefault(t.person, []).append(t.kind)
    return mine, theirs


def ranks(store: Store, pid: str) -> dict[str, int]:
    """`acts.tie_rank` for every person `pid` knows, worked out in one pass."""
    household = store.get("people", pid).household
    mine, _ = _tie_kinds(store, pid)
    out = {}
    for other in knows(store, pid):
        if store.get("people", other).household == household:
            out[other] = 0
        elif other in mine:
            out[other] = min(_RANK.get(k, 5) for k in mine[other])
        else:
            out[other] = 5
    return out


def people_known(store: Store, pid: str) -> list[tuple[str, int, str]]:
    """Everyone `pid` knows as (id, rank, relation words), closest first, then by id."""
    household = store.get("people", pid).household
    mine, theirs = _tie_kinds(store, pid)
    rank = ranks(store, pid)
    wrote = {m.sender for m in store.all("messages") if m.to == pid}
    rows = []
    for other, r in sorted(rank.items(), key=lambda kv: (kv[1], id_key(kv[0]))):
        kinds = sorted(set(mine.get(other, [])), key=lambda k: (_RANK.get(k, 5), k))
        said = [RELATION.get(k, k) for k in kinds]
        if not said:
            for k in sorted(set(theirs.get(other, [])), key=lambda k: (_RANK.get(k, 5), k)):
                said.append(_ONE_SIDED[k] if k in _ONE_SIDED else RELATION.get(_OTHER_SIDE.get(k, ""), "knows you"))
        if not said:
            if store.get("people", other).household == household:
                said = ["lives with you"]
            else:
                said = ["has written to you" if other in wrote else "you have written to them"]
        rows.append((other, r, ", ".join(dict.fromkeys(said))))
    return rows


def people_lines(store: Store, pid: str, n: int = PEOPLE_SHOWN) -> list[str]:
    """`<name>, <relation words>` for the `n` people closest to `pid`."""
    return [f"{store.get('people', o).name}, {words}" for o, _, words in people_known(store, pid)[:n]]


# --- who a person is ----------------------------------------------------------------------


def _active(store: Store, **where) -> list:
    return [o for o in store.where("obligations", **where) if o.status in SCHEDULED]


def _payday(ob) -> str:
    return DAYS[dow(ob.first)] + "s"


def _home(store: Store, pid: str) -> list[str]:
    p = store.get("people", pid)
    h = store.get("households", p.household)
    others = [store.get("people", m).name for m in h.members if m != pid]
    place = ", ".join(x for x in (h.street, store.place) if x)
    where = (f" at {place}," if h.street else f" in {place}") if place else ""
    hname = h.name if "household" in h.name else f"{h.name} household"
    if others:
        line = f"You live{where} with {_and(others)}"
    else:
        line = f"You live alone{where}".rstrip(",")
    lines = [f"{line}; your household is the {hname}."]
    rent = [o for o in _active(store, payer=h.id) if o.kind == "rent"]
    mortgage = [o for o in _active(store, payer=h.id) if o.kind == "loan" and o.memo.startswith("mortgage")]
    if rent:
        lord = name_of_who(store, h.landlord) if h.landlord else name_of(store, rent[0].payee)
        lines.append(f"The house is rented from {lord} for {money(rent[0].cents)} a week, paid on {_payday(rent[0])}.")
    elif mortgage:
        m = mortgage[0]
        last = f", the last payment on {when(m.last)}" if m.last is not None else ""
        lines.append(f"The house is yours, with a mortgage of {money(m.cents)} a week to "
                     f"{name_of(store, m.payee)}{last}.")
    elif h.street:
        lines.append("The house is yours outright.")
    return lines


def _work(store: Store, pid: str) -> list[str]:
    p = store.get("people", pid)
    lines = [_second_person(p.job) + "."] if p.job.strip() else []
    home = p.household
    jobs = store.where("jobs", person=pid)
    owned = [o for o in store.all("orgs") if o.owner == pid and not o.outside]
    for j in jobs:
        org = store.get("orgs", j.org)
        ob = store.get("obligations", j.wage_ob) if store.has("obligations", j.wage_ob) else None
        paid = f", paid on {_payday(ob)}" if ob is not None else ""
        if org.owner == pid:
            lines.append(f"You own {org.name} and work there {j.hours:g} hours a week, drawing {money(j.weekly)} "
                         f"a week{paid}.")
        else:
            lines.append(f"You work {j.hours:g} hours a week at {org.name} for {money(j.weekly)} a week{paid}.")
    for org in owned:
        if not any(j.org == org.id for j in jobs):
            lines.append(f"You own {org.name}.")
        staff = [store.get("people", j.person).name for j in store.where("jobs", org=org.id) if j.person != pid]
        if staff:
            lines.append(f"Also working at {org.name}: {_and(staff)}.")
    for other in store.all("households"):
        if other.landlord in (pid, home) and other.id != home:
            got = [o for o in _active(store, payer=other.id) if o.kind == "rent" and o.payee == home]
            what = f" for {money(got[0].cents)} a week" if got else ""
            who = "You let" if other.landlord == pid else "Your household lets"
            lines.append(f"{who} {other.street or 'a house'} to {_the(other.name)}{what}.")
    if not jobs:
        h = store.get("people", pid).household
        wages = {j.wage_ob for j in store.all("jobs")}
        for o in _active(store, payee=h):
            if o.kind == "wage" and o.id not in wages and not store.has("households", o.payer):
                lines.append(f"Your household is paid {o.memo}: {money(o.cents)} a week, on {_payday(o)}.")
    return lines


def _the(name: str, start: bool = False) -> str:
    """A household's name as said in a sentence: `the Stott household`."""
    if "household" in name.split():
        return ("The " if start else "the ") + name
    return name


def _debts(store: Store, pid: str) -> list[tuple[int, str]]:
    """(cents, line) for what the household owes and is owed with a last date, largest first;
    the mortgage is told with the house."""
    h = store.get("people", pid).household
    rows = []
    for o in _active(store, payer=h):
        if o.last is not None and not (o.kind == "loan" and o.memo.startswith("mortgage")):
            rows.append((o.cents, f"Your household owes {_the(name_of(store, o.payee))}: {o.memo}, {money(o.cents)} a "
                                  f"week, the last payment on {when(o.last)}."))
    for o in _active(store, payee=h):
        if o.last is not None:
            rows.append((o.cents, f"{_the(name_of(store, o.payer), True)} owes your household: "
                                  f"{o.memo}, {money(o.cents)} a week, the last payment on {when(o.last)}."))
    return sorted(rows, key=lambda r: -r[0])


def who(store: Store, pid: str) -> str:
    """The person in the second person, from their rows, in at most 300 words where it can
    be: what gives way is their interests, then their friends, then debts past the three
    largest."""
    p = store.get("people", pid)
    head = [f"You are {p.name}, {p.age}."] + _home(store, pid) + _work(store, pid)
    debts = [line for _, line in _debts(store, pid)]
    known = people_known(store, pid)
    close = [f"{store.get('people', o).name}, {w}" for o, r, w in known if r <= 1]
    friends = [store.get("people", o).name for o, r, _ in known if r == 2]
    close_line = [f"Family and household: {'; '.join(close)}."] if close else []
    friends_line = [f"Your friends: {_and(friends)}."] if friends else []
    beliefs = [f"What you think: {' '.join(p.beliefs)}"] if p.beliefs else []
    interests = [f"You are interested in {_and(p.interests)}."] if p.interests else []
    worries = [f"What worries you: {' '.join(p.worries)}"] if p.worries else []
    voice = [_second_person(p.voice) if _verb(p.voice.split(" ")[0].lower()) else p.voice] if p.voice.strip() else []

    def text() -> str:
        return "\n".join(head + debts + close_line + friends_line + beliefs + interests + worries + voice)

    for give in ("interests", "friends", "debts"):
        if _words(text()) <= WHO_WORDS:
            break
        if give == "interests":
            interests = []
        elif give == "friends":
            friends_line = []
        else:
            debts = debts[:3]
    return text()


# --- an owner's business ------------------------------------------------------------------


def _coming(store: Store, account: str) -> list[str]:
    rows = []
    for u in upcoming(store, account, AHEAD):
        if not u.out:
            continue
        late = any(d.status in OWED for d in store.where("dues", obligation=u.obligation, day=u.day))
        day = f"overdue since {date_text(u.day)}" if late else date_text(u.day)
        rows.append(f"  {day}  {money(u.cents)}  {name_of(store, u.other)}  {u.memo} ({u.obligation})")
    return rows or ["  nothing"]


def _till(label: str, t) -> str:
    return (f"{label}: sold {t.sold}, turned away {t.turned_stock} for want of stock and {t.turned_capacity} "
            f"for want of staff, threw away {t.spoiled}, took {money(t.takings)}")


def _shelf(store: Store, f) -> str:
    now = week(store.day)
    this = sum(u for u, w in zip(f.stock_units, f.stock_weeks) if w >= now)
    last = sum(u for u, w in zip(f.stock_units, f.stock_weeks) if w == now - 1)
    older = sum(f.stock_units) - this - last
    more = f", {older} older" if older else ""
    return f"On the shelf: {sum(f.stock_units)} units ({this} from this week, {last} from last week{more})"


def _supplier_name(store: Store, sid: str) -> str:
    if not store.has("suppliers", sid):
        return sid
    return name_of_who(store, store.get("suppliers", sid).org)


def business(store: Store, pid: str, weeks: int = 2) -> str:
    """Each organisation in the town that `pid` owns, as its books and till show it: for a
    shop its account, price, shelf, order, staff, its last `weeks` till weeks (at most two)
    and what goes out from its account this week. "" if they own none."""
    blocks = []
    for org in store.all("orgs"):
        if org.owner != pid or org.outside:
            continue
        lines = [org.name, f"Account: {money(store.balance(org.id))}"]
        shops = store.where("firms", org=org.id)
        for f in shops:
            lines.append(f"Price: {money(f.price)} a basket")
            lines.append(_shelf(store, f))
            if f.order_units > 0:
                lines.append(f"On order for Monday: {f.order_units} units from "
                             f"{_supplier_name(store, f.order_supplier)}")
            elif f.last_units > 0 and f.last_supplier:
                lines.append(f"Nothing ordered; last week's order was {f.last_units} units from "
                             f"{_supplier_name(store, f.last_supplier)}")
            else:
                lines.append("Nothing ordered")
        staff = store.where("jobs", org=org.id)
        if staff:
            lines.append("Staff:")
            lines += [f"  {store.get('people', j.person).name}, {j.hours:g} hours, {money(j.weekly)} a week"
                      for j in staff]
        for f in shops:
            tills = sorted((t for t in store.all("tills") if t.firm == f.id), key=lambda t: -t.week)
            for label, t in list(zip(("Last week", "The week before"), tills))[:max(0, min(weeks, 2))]:
                lines.append(_till(label, t))
        lines.append("Going out this week:")
        lines += _coming(store, org.id)
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def till_weeks(store: Store, pid: str) -> int:
    """How many till weeks `business` shows for `pid` at most (two, or fewer when fewer closed)."""
    shops = [f for o in store.all("orgs") if o.owner == pid and not o.outside for f in store.where("firms", org=o.id)]
    return max((min(2, len([t for t in store.all("tills") if t.firm == f.id])) for f in shops), default=0)


# --- how a trade works ----------------------------------------------------------------------


def knowhow(store: Store, pid: str) -> list[str]:
    """`KNOWHOW` for `pid`'s kind, with the figures read from their own shop; none for a kind
    with no trade. A line that needs a figure their rows do not give is left out."""
    p = store.get("people", pid)
    lines = KNOWHOW.get(p.kind, [])
    shop = next((f for o in store.all("orgs") if o.owner == pid and not o.outside
                 for f in store.where("firms", org=o.id)), None)
    figures = {"staff_week": f"{firms.HOURS_PER_STAFF:g}"}
    if shop is not None:
        figures |= {"shelf_weeks": str(shop.shelf_weeks), "per_staff": str(shop.units_per_staff)}
    out = []
    for line in lines:
        try:
            out.append(line.format(**figures))
        except KeyError:
            continue
    return out


__all__ = ["KNOWHOW", "RELATION", "business", "knowhow", "people_known", "people_lines", "ranks",
           "till_weeks", "when", "who"]
