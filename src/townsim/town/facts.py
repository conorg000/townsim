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

import dataclasses
import re
from datetime import timedelta

from townsim.econ.firm import Firm
from townsim.town import firms, jobs
from townsim.town.acts import TIE_RANKS, knows, name_of_who
from townsim.town.clock import DAY_ZERO, DAYS, date_text, dow, week
from townsim.town.obligations import (LATE_FEE_CENTS, OWED, SCHEDULED, last_cents, last_instalment, money, name_of,
                                      cents_now, owed_dues, pending_change, upcoming)
from townsim.town.store import Store, id_key

WHO_WORDS = 300
AHEAD = 6                          # "this week": today and the six days after
PEOPLE_SHOWN = 12

# The reader's side of a tie, in plain words.
RELATION = {"partner": "your partner", "parent": "your parent", "child": "your child",
            "sibling": "your brother or sister", "kin": "a relative", "friend": "a friend",
            "coworker": "works with you", "employer": "your employer", "employee": "works for you",
            "landlord": "your landlord", "tenant": "your tenant", "debtor": "owes you money",
            "creditor": "you owe them money", "banker": "the banker"}
_RANK = TIE_RANKS
CARRY_PER_100 = round(100 * next(f.default for f in dataclasses.fields(Firm) if f.name == "carry_loss"))
# How a tie seen only from the other side reads from this one.
_OTHER_SIDE = {"partner": "partner", "parent": "child", "child": "parent", "sibling": "sibling", "kin": "kin",
               "friend": "friend", "coworker": "coworker", "employer": "employee", "employee": "employer",
               "landlord": "tenant", "tenant": "landlord", "creditor": "debtor", "debtor": "creditor"}
_ONE_SIDED = {"banker": "has a loan from the bank"}

KNOWHOW: dict[str, list[str]] = {
    "owner": [
        "Stock ordered before Monday arrives on Monday and is paid for on arrival; with no new order, "
        "last week's order comes again.",
        "Of what is left on the shelf at the end of a week, about {carry} in every 100 units go off; anything "
        "{shelf_weeks} weeks old is thrown away.",
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


UNTIL_ENDED = "either side ends it"


def ends_on(ob) -> str:
    """The day the last instalment of `ob` falls, as written: never its raw `last` day; for
    one that runs until ended, `either side ends it` (read after `until`)."""
    day = last_instalment(ob)
    return UNTIL_ENDED if day is None else when(day)


def rents(store: Store, payer: str, payee: str | None = None) -> list:
    """The rents `payer` pays (to `payee`) that are in force: a let has one rent obligation for
    its life, re-priced when the rent changes (`obligations.pending_change` gives a change
    announced and not yet come)."""
    return [o for o in store.where("obligations", payer=payer)
            if o.kind == "rent" and o.status in SCHEDULED and (payee is None or o.payee == payee)]


def _home(store: Store, pid: str) -> list[str]:
    p = store.get("people", pid)
    h = store.get("households", p.household)
    place = ", ".join(x for x in (h.street, store.place) if x)
    where = (f" at {place}" if h.street else f" in {place}") if place else ""
    hname = h.name if "household" in h.name else f"{h.name} household"
    alone = "" if len(h.members) > 1 else " alone"
    lines = [f"You live{alone}{where}; your household is the {hname}."]
    rent = rents(store, h.id)
    mortgage = [o for o in _active(store, payer=h.id) if o.kind == "loan" and o.memo.startswith("mortgage")]
    if rent:
        lord = name_of_who(store, h.landlord) if h.landlord else name_of(store, rent[0].payee)
        line = (f"The house is rented from {lord} for {money(cents_now(store, rent[0]))} a week, paid on "
                f"{_payday(rent[0])}.")
        coming = pending_change(store, rent[0])
        if coming:
            line += f" The rent changes to {money(coming[0])} a week from {when(coming[1])}."
        lines.append(line)
    elif mortgage:
        m = mortgage[0]
        last = f", the last payment on {ends_on(m)}" if m.last is not None else ""
        lines.append(f"The house is yours, with a mortgage of {money(m.cents)} a week to "
                     f"{name_of(store, m.payee)}{last}.")
    elif h.street:
        lines.append("The house is yours outright.")
    return lines


def _does(store: Store, pid: str) -> str:
    """What someone does, said of them: their job line, or where they work, or ""."""
    line = job_line(store, pid)
    if line:
        return line if _verb(line.split(" ")[0]) else f"is {line}"
    held = jobs.held(store, person=pid)
    if held:
        return f"works at {store.get('orgs', held[0].org).name}"
    return "has no work at present" if store.where("jobs", person=pid) else ""


_COUNT_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _count(n: int) -> str:
    return _COUNT_WORDS[n] if 0 <= n < len(_COUNT_WORDS) else str(n)


def _lets(store: Store, pid: str) -> int:
    """How many houses `pid` lets, themselves or through their household."""
    home = store.get("people", pid).household
    return len([h for h in store.all("households") if h.landlord in (pid, home) and h.id != home])


def job_line(store: Store, pid: str) -> str:
    """What `pid` does, said of them, as it is today. The line written when the place was made
    (`Person.job`) while it is still true: every organisation it names is one they work at or
    own today, or it names none and they hold no job. Otherwise a line read from the rows:
    the houses they let, what they run and where they work, joined by `and`; "" if none."""
    p = store.get("people", pid)
    line = p.job.strip()
    held = jobs.held(store, person=pid)
    owned = [o for o in store.all("orgs") if o.owner == pid and not o.outside]
    now = {store.get("orgs", j.org).name for j in held if store.has("orgs", j.org)} | {o.name for o in owned}
    named = [o.name for o in store.all("orgs") if o.name and o.name in line]
    if line and ((named and all(n in now for n in named)) or (not named and not held)):
        return line
    parts = []
    n = _lets(store, pid)
    if n:
        parts.append(f"lets {_count(n)} house" + ("" if n == 1 else "s"))
    parts += [f"runs {o.name}" for o in owned]
    parts += [f"works at {store.get('orgs', j.org).name}" for j in held
              if store.has("orgs", j.org) and store.get("orgs", j.org).owner != pid]
    return " and ".join(parts)


def _relation_counts(store: Store, pid: str) -> bool:
    """Whether `pid` is the one whose household's own debts are on their mind: living alone,
    the head of the household (its first member), or the head's partner."""
    members = store.get("households", store.get("people", pid).household).members
    return len(members) == 1 or members[0] == pid or any(
        t.person == members[0] and t.other == pid and t.kind == "partner" for t in store.all("ties"))


def _live(store: Store, **where: str) -> list:
    return [o for o in store.where("obligations", **where) if o.status in SCHEDULED]


def worries(store: Store, pid: str, n: int) -> list[str]:
    """The first `n` of what is on `pid`'s mind about money and work, each true of the rows
    today: more going out than coming in; debts between people and when they end; the
    household's loans; their shop's custom, loans and rent; their hours; finding work on
    benefit; a small pension; their rent or mortgage; the houses they let; the bank's loans;
    the software firm's funding; their job; the earner they depend on; else the bills."""
    from townsim.town import gen        # the measures of a week's money (gen reads this module)
    p = store.get("people", pid)
    home = p.household
    h = store.get("households", home)
    held = jobs.held(store, person=pid)
    found: list[str] = []
    inn, out = gen.weekly_in(store, home), gen.weekly_out(store, home)
    if inn < out:
        found.append(f"More goes out each week ({money(out)}) than comes in ({money(inn)}).")
    for t in store.all("ties"):
        if t.person == pid and t.kind in ("creditor", "debtor") and store.has("people", t.other):
            other = store.get("people", t.other)
            payer, payee = (home, other.household) if t.kind == "creditor" else (other.household, home)
            owed = [o for o in _live(store, payer=payer) if o.payee == payee and o.kind == "agreement"]
            if owed:
                o, first = owed[0], other.name.split()[0]
                found.append(f"Paying back {first}: {money(o.cents)} a week until {ends_on(o)}."
                             if t.kind == "creditor" else
                             f"Whether {first} keeps up the {money(o.cents)} a week owed to you.")
    mine = _relation_counts(store, pid)
    for o in _live(store, payer=home):
        if o.kind == "loan" and not o.memo.startswith("mortgage") and mine:
            found.append(f"The {o.memo} costs {money(o.cents)} a week until {ends_on(o)}.")
    food = [f for f in store.all("firms") if f.need == firms.NEED]
    smallest = min(food, key=lambda f: (f.last_units, id_key(f.id))).org if food else ""
    for org in [o for o in store.all("orgs") if o.owner == pid and not o.outside and store.where("firms", org=o.id)]:
        if org.id == smallest:
            found.append(f"Whether enough custom comes into {org.name}, the smallest of the {_count(len(food))} shops.")
        for o in _live(store, payer=org.id):
            if o.kind == "loan":
                found.append(f"The {o.memo} costs {org.name} {money(o.cents)} a week.")
            elif o.kind == "rent":
                found.append(f"Whether {org.name} takes enough to pay its rent and running costs "
                             f"of {money(o.cents)} a week.")
    shop_jobs = [j for j in held if store.where("firms", org=j.org) and store.get("orgs", j.org).owner != pid]
    if shop_jobs and shop_jobs[0].hours < 30:
        found.append(f"You only get {shop_jobs[0].hours:g} hours a week at {store.get('orgs', shop_jobs[0].org).name}.")
    benefit = jobs.benefit_held(store, pid)
    if not held and benefit:
        found.append(f"Finding work; your benefit is {money(store.get('obligations', benefit).cents)} a week.")
    pensioners = [q for q in h.members if store.get("people", q).kind == "retired"
                  or (store.get("people", q).kind == "landlord" and store.get("people", q).age >= jobs.PENSION_AGE)]
    pensions = [o for o in store.where("obligations", payee=home) if o.memo == "state pension"]
    if pid in pensioners and pensioners.index(pid) < len(pensions):
        mine_ob = pensions[pensioners.index(pid)]
        all_cents = sorted(o.cents for o in store.all("obligations") if o.memo == "state pension" and o.status in SCHEDULED)
        if mine_ob.status in SCHEDULED and mine_ob.cents <= all_cents[len(all_cents) // 2]:
            found.append(f"Getting by on your pension of {money(mine_ob.cents)} a week.")
    if store.has("people", h.landlord) and h.landlord != pid:
        rent = rents(store, home)
        if rent:
            found.append(f"Your rent of {money(cents_now(store, rent[0]))} a week to "
                         f"{store.get('people', h.landlord).name}.")
    mortgage = [o for o in _live(store, payer=home) if o.memo.startswith("mortgage")]
    if mortgage and mine:
        found.append(f"Your mortgage of {money(mortgage[0].cents)} a week.")
    if p.kind == "landlord" and _lets(store, pid):
        found.append(f"Keeping the {_count(_lets(store, pid))} houses you let in good repair.")
    banks = [o for o in store.all("orgs") if o.kind == "bank"]
    if p.kind == "banker" and banks and any(j.org == banks[0].id for j in held):
        k = len([o for o in _live(store, payee=banks[0].id) if o.kind == "loan"])
        found.append(f"Whether the {k} loans on the books at {banks[0].name} are kept up.")
    if p.kind == "software":
        for firm in [o for o in store.all("orgs") if o.kind == "firm" and not o.outside]:
            if firm.owner == pid:
                inv = [o for o in _live(store, payee=firm.id) if store.has("orgs", o.payer)
                       and store.get("orgs", o.payer).kind == "investor"]
                if inv:
                    found.append(f"Whether {store.get('orgs', inv[0].payer).name} keeps paying {firm.name} "
                                 f"{money(inv[0].cents)} a week.")
            elif any(j.org == firm.id for j in held):
                found.append(f"Whether {firm.name} keeps its funding.")
    if p.kind == "outside_worker":
        outside = [j for j in held if store.get("orgs", j.org).outside]
        if outside:
            found.append(f"Keeping your job at {store.get('orgs', outside[0].org).name}.")
    if p.kind == "home":
        earners = [q for q in h.members if jobs.held(store, person=q)]
        if earners:
            found.append(f"Getting by on what {store.get('people', earners[0]).name.split()[0]} brings in.")
    if shop_jobs and not any(x.startswith("You only") for x in found):
        found.append(f"Keeping your hours at {store.get('orgs', shop_jobs[0].org).name}.")
    if not found:
        found.append("Keeping up with the bills.")
    return found[:n]


def _housemates(store: Store, pid: str, known: list[tuple[str, int, str]]) -> list[str]:
    """One line for each other member of the household: name, relation, what they do."""
    household = store.get("people", pid).household
    lines = []
    for other, _, words in known:
        if store.get("people", other).household != household:
            continue
        does = _does(store, other)
        lines.append(f"{store.get('people', other).name}, {words}" + (f", {does}." if does else "."))
    return lines


def _work(store: Store, pid: str) -> list[str]:
    p = store.get("people", pid)
    home = p.household
    said = job_line(store, pid)
    held = jobs.held(store, person=pid)
    owned = [o for o in store.all("orgs") if o.owner == pid and not o.outside]
    told = False                                     # whether the job line has been said in a fuller line
    lines = []
    for j in held:
        org = store.get("orgs", j.org)
        ob = store.get("obligations", j.wage_ob) if store.has("obligations", j.wage_ob) else None
        paid = f", paid on {_payday(ob)}" if ob is not None else ""
        mine = org.owner == pid
        if not told and said and org.name in said:
            told = True
            head = _second_person(said) + (", which you own" if mine else "")
            pay = f"drawing {money(j.weekly)} a week" if mine else f"for {money(j.weekly)} a week"
            lines.append(f"{head}: {j.hours:g} hours a week, {pay}{paid}.")
        elif mine:
            lines.append(f"You own {org.name} and work there {j.hours:g} hours a week, drawing {money(j.weekly)} "
                         f"a week{paid}.")
        else:
            lines.append(f"You work {j.hours:g} hours a week at {org.name} for {money(j.weekly)} a week{paid}.")
    if said and not told:
        lines.insert(0, _second_person(said) + ".")
    for org in owned:
        if not any(j.org == org.id for j in held) and org.name not in said:
            lines.append(f"You own {org.name}.")
        staff = [store.get("people", j.person).name for j in jobs.held(store, org=org.id) if j.person != pid]
        if staff:
            lines.append(f"Also working at {org.name}: {_and(staff)}.")
    for other in store.all("households"):
        if other.landlord in (pid, home) and other.id != home:
            got = rents(store, other.id, home)
            what = f" for {money(cents_now(store, got[0]))} a week" if got else ""
            coming = pending_change(store, got[0]) if got else None
            if coming:
                what += f"; it changes to {money(coming[0])} from {when(coming[1])}"
            who = "You let" if other.landlord == pid else "Your household lets"
            lines.append(f"{who} {other.street or 'a house'} to the {other.name.split(' household')[0]} household{what}.")
    if not held:
        wages = {j.wage_ob for j in store.all("jobs")}
        for o in _active(store, payee=home):
            if o.kind == "wage" and o.id not in wages and not store.has("households", o.payer):
                lines.append(f"Your household is paid {memo(o.memo)}: {money(o.cents)} a week, on {_payday(o)}.")
    if not lines and store.where("jobs", person=pid):
        lines.append("You have no work at present.")
    return lines


def _the(name: str, start: bool = False) -> str:
    """A household's name as said in a sentence: `the Stott household`."""
    if "household" in name.split():
        return ("The " if start else "the ") + name
    return name


def _last_sum(ob) -> str:
    """`, <$y>,` when the last instalment differs from the rest; else ""."""
    return f", {money(last_cents(ob))}," if last_cents(ob) != ob.cents else ""


def _debts(store: Store, pid: str) -> list[tuple[int, str]]:
    """(cents, line) for what the household owes and is owed with a last date, largest first;
    the mortgage and the rent are told with the house."""
    h = store.get("people", pid).household
    rows = []
    for o in _active(store, payer=h):
        if o.last is not None and o.kind != "rent" and not (o.kind == "loan" and o.memo.startswith("mortgage")):
            rows.append((o.cents, f"Your household owes {_the(name_of(store, o.payee))}: {memo(o.memo)}, "
                                  f"{money(o.cents)} a week, the last payment{_last_sum(o)} on {ends_on(o)}."))
    for o in _active(store, payee=h):
        if o.last is not None and o.kind != "rent":
            rows.append((o.cents, f"{_the(name_of(store, o.payer), True)} owes your household: "
                                  f"{memo(o.memo)}, {money(o.cents)} a week, the last payment{_last_sum(o)} on "
                                  f"{ends_on(o)}."))
    return sorted(rows, key=lambda r: -r[0])


def who(store: Store, pid: str) -> str:
    """The person in the second person, from their rows, in at most 300 words: what gives way
    is debts past the three largest by weekly sum (one line counts them), then their interests,
    then their friends, then their family in other houses (`PEOPLE YOU KNOW` has both), then
    their beliefs, worries and voice; and if that is still too long, the words past the 299th."""
    p = store.get("people", pid)
    known = people_known(store, pid)
    head = [f"You are {p.name}, {p.age}."] + _home(store, pid) + _housemates(store, pid, known) + _work(store, pid)
    debts = [line for _, line in _debts(store, pid)]
    household = p.household
    kin = [f"{store.get('people', o).name}, {w}" for o, r, w in known
           if r == 1 and store.get("people", o).household != household]
    friends = [store.get("people", o).name for o, r, _ in known if r == 2]
    kin_line = [f"Family in other houses: {'; '.join(kin)}."] if kin else []
    friends_line = [f"Your friends: {_and(friends)}."] if friends else []
    beliefs = [f"What you think: {' '.join(p.beliefs)}"] if p.beliefs else []
    interests = [f"You are interested in {_and(p.interests)}."] if p.interests else []
    mind = worries(store, pid, len(p.worries))
    worry_line = [f"What worries you: {' '.join(mind)}"] if mind else []
    voice = [_second_person(p.voice) if _verb(p.voice.split(" ")[0].lower()) else p.voice] if p.voice.strip() else []

    def text() -> str:
        return "\n".join(head + debts + kin_line + friends_line + beliefs + interests + worry_line + voice)

    for give in ("debts", "interests", "friends", "kin", "beliefs", "worries", "voice"):
        if _words(text()) <= WHO_WORDS:
            break
        if give == "debts" and len(debts) > 3:
            more = len(debts) - 3
            debts = debts[:3] + [f"and {more} more debt{'' if more == 1 else 's'}."]
        elif give == "interests":
            interests = []
        elif give == "friends":
            friends_line = []
        elif give == "kin":
            kin_line = []
        elif give == "beliefs":
            beliefs = []
        elif give == "worries":
            worry_line = []
        elif give == "voice":
            voice = []
    out = text()
    if _words(out) > WHO_WORDS:                    # only the account of their own rows is left: cut it
        out = cut_words(out, WHO_WORDS - 1)
    return out


# --- what is owed, and the bank's book --------------------------------------------------------

OWED_LINES = 8
OWED_KEPT = 4                      # what is owed when it gives way
BOOK_LINES = 12
GOING_OUT = 10
COMING_IN = 6
from townsim.town.statement import MEMO_CHARS, MEMO_WORDS, Counted, cut_words, label, memo  # noqa: E402,F401
_OWED_KINDS = ("loan", "agreement", "standing")


def arrears(store: Store, ob_id: str) -> tuple[int, int | None]:
    """Cents still owed on dues past their day, and the day of the oldest."""
    dues = owed_dues(store, ob_id)
    return sum(d.cents - d.paid for d in dues), min((d.day for d in dues), default=None)


def until(ob) -> str:
    """`, until <date>`, or `, the last, on <date>, <$y>` when the last instalment differs; ""
    for an obligation with no last day."""
    if ob.last is None:
        return ""
    if last_cents(ob) != ob.cents:
        return f", the last, on {ends_on(ob)}, {money(last_cents(ob))}"
    return f", until {ends_on(ob)}"


def owed_lines(store: Store, account: str, most: int = OWED_LINES) -> list[str]:
    """What `account` owes and is owed that matters week to week: loans, agreements and
    standing payments in force, and anything at all with arrears; one line each, in id order,
    at most `most` and then a line counting the rest."""
    rows = []
    for ob in store.all("obligations"):
        if account not in (ob.payer, ob.payee):
            continue
        late, _ = arrears(store, ob.id)
        if not late and not (ob.kind in _OWED_KINDS and ob.status in SCHEDULED):
            continue
        party = f"to {name_of(store, ob.payee)}" if ob.payer == account else f"from {name_of(store, ob.payer)}"
        if ob.every == 0:
            how = f"once on {when(ob.first)} {party}"
        else:
            every = {1: "a day", 7: "a week"}.get(ob.every, f"every {ob.every} days")
            how = f"{every} {party}" + until(ob)
        rows.append(f"{ob.id}: {memo(ob.memo)}, {money(ob.cents)} {how}" + (f", {money(late)} overdue" if late else ""))
    if len(rows) > most:
        rows = rows[:most] + [Counted(f"({len(rows) - most} more not shown)")]
    return rows


def bank_book(store: Store, pid: str) -> str:
    """`bank_book_rows` as text."""
    return "\n".join(bank_book_rows(store, pid))


def bank_book_rows(store: Store, pid: str) -> list[str]:
    """For a banker, the bank's book: its name, its account, and each loan owed to it that is
    in force, in id order (at most twelve, then a line counting the rest). None for anyone else."""
    if store.get("people", pid).kind != "banker":
        return []
    banks = [o for o in store.all("orgs") if o.kind == "bank"]
    bank = next((o for o in banks if o.contact == pid), banks[0] if banks else None)
    if bank is None:
        return []
    rows = []
    for ob in store.where("obligations", payee=bank.id):
        if ob.kind != "loan" or ob.status not in SCHEDULED:
            continue
        ends = until(ob)
        late, since = arrears(store, ob.id)
        overdue = f", {money(late)} overdue since {date_text(since)}" if late else ""
        rows.append(f"{ob.id}: {name_of(store, ob.payer)}, {memo(ob.memo)}, {money(ob.cents)} a week{ends}{overdue}")
    if len(rows) > BOOK_LINES:
        rows = rows[:BOOK_LINES] + [Counted(f"({len(rows) - BOOK_LINES} more not shown)")]
    fees: dict[str, int] = {}
    for ob in store.where("obligations", payee=bank.id):
        if ob.penalty:
            unpaid = sum(d.cents - d.paid for d in store.where("dues", obligation=ob.id) if d.status in ("due",) + OWED)
            if unpaid > 0:
                fees[ob.payer] = fees.get(ob.payer, 0) + unpaid
    late = [f"{name_of(store, who)}: late fees of {money(c)} unpaid" for who, c in fees.items()]
    if len(late) > BOOK_LINES:
        late = late[:BOOK_LINES] + [Counted(f"({len(late) - BOOK_LINES} more not shown)")]
    rows += late
    return [bank.name, f"Account: {money(store.balance(bank.id))}"] + rows


# --- an owner's business ------------------------------------------------------------------


def coming(store: Store, ups: list, most: int) -> list[str]:
    """The rows for upcoming payments `ups`, each memo cut to a label: what is overdue first,
    then soonest first, at most `most`, then a line counting the rest; `  nothing` for none."""
    rows = []
    for u in ups:
        late = any(d.status in OWED for d in store.where("dues", obligation=u.obligation, day=u.day))
        day = f"overdue since {date_text(u.day)}" if late else date_text(u.day)
        rows.append((not late, u.day, f"  {day}  {money(u.cents)}  {name_of(store, u.other)}  {memo(u.memo)} "
                                      f"({u.obligation})"))
    rows.sort(key=lambda r: r[:2])
    out = [r[2] for r in rows[:most]]
    if len(rows) > most:
        out.append(Counted(f"  ({len(rows) - most} more not shown)"))
    return out or ["  nothing"]


def _coming(store: Store, account: str) -> list[str]:
    return coming(store, [u for u in upcoming(store, account, AHEAD) if u.out], GOING_OUT)


def transfer_rows(store: Store, lines: list) -> list[str]:
    """The rows `statement_lines` shows for a statement's in-and-out `lines`, oldest first."""
    return [f"  {date_text(x.day)}  {money(x.cents)}  {name_of(store, x.other)}  {memo(x.memo)}" for x in lines]


def statement_lines(store: Store, st) -> str:
    """`statement_rows` as text."""
    return "\n".join(statement_rows(store, st))


def statement_rows(store: Store, st) -> list[str]:
    """A statement as `statement.statement_text` lays it out, with every memo cut to a label
    and at most ten payments going out and six coming in."""
    rows = [f"Balance: {money(st.balance)}", f"In and out since {date_text(st.since)}:"]
    if st.earlier:
        rows.append(Counted(f"  ({st.earlier} earlier lines not shown)"))
    rows += transfer_rows(store, st.lines)
    if not st.lines and not st.earlier:
        rows.append("  nothing")
    rows.append(f"Going out by {date_text(st.until)}:")
    rows += coming(store, st.out, GOING_OUT)
    rows.append(f"Expected in by {date_text(st.until)}:")
    rows += coming(store, st.coming_in, COMING_IN)
    rows.append(f"After what goes out: {money(st.after_out)}")
    return rows


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
    """`business_rows` as text."""
    return "\n".join(business_rows(store, pid, weeks))


def business_rows(store: Store, pid: str, weeks: int = 2) -> list[str]:
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
        staff = jobs.held(store, org=org.id)
        if staff:
            lines.append("Staff:")
            lines += [f"  {store.get('people', j.person).name}, {j.hours:g} hours, {money(j.weekly)} a week"
                      for j in staff]
        for f in shops:
            tills = sorted((t for t in store.all("tills") if t.firm == f.id), key=lambda t: -t.week)
            for label, t in list(zip(("Last week", "The week before"), tills))[:max(0, min(weeks, 2))]:
                lines.append(_till(label, t))
        lines.append(f"Going out by {date_text(store.day + AHEAD)}:")
        lines += _coming(store, org.id)
        blocks += lines
    return blocks


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
    figures = {"staff_week": f"{firms.HOURS_PER_STAFF:g}", "carry": str(CARRY_PER_100)}
    if shop is not None:
        figures |= {"shelf_weeks": str(shop.shelf_weeks), "per_staff": str(shop.units_per_staff)}
    out = []
    for line in lines:
        try:
            out.append(line.format(**figures))
        except KeyError:
            continue
    return out


__all__ = ["KNOWHOW", "RELATION", "bank_book", "business", "knowhow", "owed_lines", "people_known", "people_lines",
           "ranks", "till_weeks", "when", "who"]
