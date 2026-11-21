# Hamlet Without Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A generated hamlet of eighteen people (households, three shops, a bank, a landlord, a small software firm, jobs, debts, ties) that lives for eight weeks on scripted replies with everything a person can do in place: paying from more than one account, running a shop, hiring and quitting, agreements, loans, rent, talking and appointments. No language model is called; the next plan puts cheap models in the people's seats.

**Architecture:** Everything is built on the engine core in `src/townsim/town/` (store with a cause on every change, one schedule for everything owed, atomic operations, stored replies, exact replay). New kinds of thing are new dataclass rows in new store tables; new abilities are new registered operations; the tested economy in `src/townsim/econ/` (demand rule, perishable stock, suppliers) is driven through a small adapter so that all its money moves through the store.

**Tech Stack:** Python 3.12, uv, pytest, standard library only. No new dependencies.

**Spec:** `docs/design.md` sections 2, 3.1 to 3.5, 4.2, 4.5 and 5.1 (the company's people only). Background: `docs/trials-step1.md`, `docs/trials-step1-round2.md`. Reference code to read, never to import or run: `spikes/town2/gen.py`, `world.py`, `views.py`, `sim.py`, `ops.py`.

This is the second of the plans for design step 2a. It ends with a hamlet that runs without models. The next plan (people played by models: a model replier with a spending cap, the probe of model families, the first model-played run and its readable account) is written when this one is done.

## Global Constraints

- Money is integer cents. `Ledger.total()` is 0 after every operation and every day. There is exactly one way anything owed is collected: `obligations.collect`. No new code path takes a wage, a rent, a loan instalment or a bill by itself.
- The store changes only through `Store` methods, and every change carries a cause: `routine:<what>`, `op:<actor>:<op>:d<day>.<n>`, or `op:<actor>/<principal>:<op>:d<day>.<n>`.
- Rows hold scalars and flat lists of scalars only (no dicts, no nested lists). A list field is replaced whole.
- Deterministic: no threads, no wall clock, no unseeded randomness, no iteration over a `set` or use of built-in `hash()` anywhere that reaches state or output. All randomness comes from `random.Random` seeded with a string built from `store.seed` and what the draw is for. Floats written to the store are rounded to 6 places.
- No test calls a model or the network. Nothing in this plan spends money.
- An operation applies in full or not at all; junk in a reply never raises out of the engine.
- Wording shown to people states records and facts. It never says what the reader may or should do, hands them no lever as advice, and never mentions tests, simulations, models or games. Every refusal starts `Not done:`. Know-how of a trade describes how things work and is word for word the same for everyone in that trade.
- The list of operations a person is shown depends only on their position (everyone; owner of a firm; employed; landlord; banker), never on the person.
- Nothing in generation is tuned toward how a run turns out. Checks on a generated place are about its structure only.
- A person's view is about 1,200 words on an ordinary day and never over 2,000, system and user messages together, and building it changes nothing.
- Do not import from `spikes/`. No real names, email addresses or local paths in the repository. Commits are authored `conorg000 <20813490+conorg000@users.noreply.github.com>` and end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- Run the suite with `uv run pytest -q`. It must pass at every commit and stay under ninety seconds.

## Decisions already made

1. **Shopping.** Each household, and each of about 120 background buyers who only shop, has a fixed shopping day. On it, its whole week's wants go through `econ.demand.shop`. A firm's capacity is counted over the week.
2. **Needs.** Food is offered in the hamlet. Every other need is bought out of town as one weekly `regular spending` obligation, as in the thin town. Putting other needs through the rule waits for the plan that lets people found organisations.
3. **Standing arrangements.** If nobody places an order by Saturday, Monday's delivery repeats last week's order exactly. Prices, pay and staff stay as they are until someone changes them.
4. **Hours.** A person's free hours each Monday are 70 less their job's hours less the hours of the recurring work they hold. Giving hours back when another does the work comes with connections, in a later plan.
5. **Wages.** Pay is a fixed weekly sum for contracted hours, an obligation of kind `wage` from the employer's account on Fridays. Extra shifts are paid once at the hourly rate. A person whose job ends is paid benefit automatically from the Friday of the following week until a job starts.
6. **Ties.** Rank 0 household, 1 kin, 2 friend, 3 employer, employee or coworker, 4 landlord, tenant, creditor, debtor or banker, 5 anyone else known, 6 unknown.
7. **Aims.** A reply may carry `"aims"`: up to five short lines. Absent means unchanged. An aim keeps its age when it is at least 0.6 similar (`difflib.SequenceMatcher.ratio`) to one already held.
8. **Names.** A name resolves if it is someone's full name or an organisation's name, ignoring case, or a first name borne by exactly one person the actor knows. Otherwise the letter comes back.
9. **Money asked for by request** counts toward arrears between the two accounts and never toward a future instalment, unless the request names what it is for.
10. **Requests wake a person** on the day after they are made and on their last day.
11. **Size.** Eighteen people in about eight households; three food shops (one with four staff, two with one each); a bank with a banker; a landlord who lets three houses; a small software firm with two people, described without any mention of AI; an outside employer; two outside suppliers.
12. **Talking.** At most three turns a side; each speaker sees their own view, the transcript, and what is open between the two; at most two talks a person a day, an hour each.
13. **Owners work in their shops.** An owner has a job row at their own firm: its hours count toward the shop's capacity and its wage is their draw.
14. **Not here.** The weekly digest of what was published, public notices, and the refund of hours when another does the work come in later plans.

## Review Focus

1. **A second money path:** a wage paid by hand from the household while the firm's wage obligation also runs; stock paid for on delivery and also billed; a loan repaid by `repay` and also by its schedule; rent asked for by request and also collected. Expected: each counts toward what is owed, and nothing is received twice. (Tasks 1, 7, 8, and random play in Task 6)
2. **A firm that cannot pay:** wages due with an empty till; a delivery larger than the cash. Expected: wages go to arrears and climb the ladder with the owner told; the delivery is cut to what can be paid for. (Tasks 2, 7)
3. **A reply that names people loosely:** a first name shared by two people the actor knows; a name that is no one; an organisation's name with different capitals. Expected: resolved only when unambiguous; otherwise the letter comes back and nothing else happens. (Task 1)
4. **A person with no hours:** ill, or the week used up. Expected: they still get their turn, each act is refused with the plain reason, a talk with them is not held, and an appointment that day is recorded as missed. (Tasks 3, 9)
5. **A long life:** eight weeks of letters, ties, a shop's reports and a loan book. Expected: no view over 1,200 words on any day for any person, and the three most pressing inbox items always shown in full. (Tasks 5, 6)

---

### Task 1: Accounts, positions, names and requests

**Files:**
- Modify: `src/townsim/town/entities.py`, `store.py`, `acts.py`, `ops_core.py`, `obligations.py`, `views.py`, `loop.py`, `rundb.py`, `thin.py`
- Test: `tests/town/test_accounts.py`, and the existing tests kept passing

**Interfaces:**
- `entities`: `Org` gains `owner: str = ""`, `outside: bool = False`. `Person` gains `kind: str = "resident"`, `age: int = 40`, `family: str = ""`, `job: str = ""`, `voice: str = ""`, `beliefs: list[str]`, `interests: list[str]`, `worries: list[str]` (default empty), `ill_until: int = -1`. `Household` gains `street: str = ""`, `shop_day: int = 5`, `landlord: str = ""`. `Request` gains `kind: str = "ask"`, `ob: str = ""`, `summary: str = ""`, `seen: bool = False`, and the flat term fields `org: str = ""`, `every: int = 0`, `first: int = -1`, `times: int = 0`, `hours: float = 0.0`. `Message` gains `org: str = ""` (the organisation it was addressed to) and `unknown: list[str]` (default empty). New row `Tie(id, person, other, kind)` in a new table `ties`.
- `store`: `Store.seed: int = 0`, part of `canonical()`. `rundb.SCHEMA = 2`.
- `acts`:
  - `accounts_of(store, pid) -> list[str]`: the household's account, then every organisation the person owns, in id order
  - `controls(store, pid, account: str) -> bool`
  - `from_account(store, act) -> tuple[str | None, str | None]`: the account an act draws on and, if it cannot, the reason. No `from`, or `"household"`, is the principal's household; a name is an organisation they control, else `'<x>' is not an account of yours.`
  - `OpSpec` gains `who: str = "all"` (`all`, `owner`, `employed`, `landlord`, `banker`), `takes_from: bool = False`, and `hours_of: Callable[[Store, Act], float] | None = None`, used in place of `hours` when given
  - `positions(store, pid) -> list[str]`: always `all`; `owner` if they own an organisation that is not outside; `employed` if a job row names them (table `jobs`, Task 7; absent table means none); `landlord` if any household names them or their household as `landlord`; `banker` if their `kind` is `banker`
  - `forms(store, actor)` lists only operations whose `who` is among the actor's positions; for an owner, an operation with `takes_from` shows `, "from": "household" | "<your business>"` before its closing brace
  - `knows(store, pid) -> list[str]`: ids of everyone in their household, everyone tied to them, and everyone who has written to them or been written to by them
  - `resolve(store, name, actor: str | None = None) -> str | None` by decision 8
  - `tie_rank(store, pid, other) -> int` by decision 6
- `obligations`: `people_of` returns an organisation's owner; `pay(..., match=True | False | "arrears")`, where `"arrears"` counts toward arrears between the two accounts and nothing else; `ensure` nothing else changes.
- `ops_core`: `pay`, `standing_payment`, `stop` and `read` draw on `from_account`. `request` takes an optional `for` naming an obligation the requester's household or business is owed on by the addressee; `answer` pays with `ref` when the request has `ob`, else with `match="arrears"`. A letter to an organisation in the hamlet goes to its owner with `Message.org` set; to an outside organisation it comes back.
- `loop.who_acts`: an open request wakes its addressee when it is unseen or today is its `expires` day; it is marked seen with the rest.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_owner_pays_from_the_shop():
    s = town_with_shop()                     # p01 owns org3 "Marsh's Stores", which holds $2,000
    out = apply_act(s, Act("p01", "p01", "pay", {"to": "Wenlow Lettings", "amount": 50, "from": "Marsh's Stores"}), 0)
    assert out.ok and s.balance("org3") == 195_000

def test_from_an_account_that_is_not_theirs_is_refused():
    s = town_with_shop(); before = s.hash()
    out = apply_act(s, Act("p02", "p02", "pay", {"to": "Wenlow Lettings", "amount": 50, "from": "Marsh's Stores"}), 0)
    assert out.msg == "Not done: 'Marsh's Stores' is not an account of yours." and s.hash() == before

def test_a_firm_in_arrears_tells_its_owner(): ...        # a bill the shop cannot pay puts a notice in p01's table rows
def test_forms_depend_on_position_only():
    s = town_with_two_shops()
    assert forms(s, "p01") == forms(s, "p05") != forms(s, "p02")       # two owners alike, an employee different
    assert any('"from": "household" | "<your business>"' in f for f in forms(s, "p01"))

def test_rent_asked_for_and_paid_is_not_taken_again():
    s = thin_town(); run_days(s, 1)          # day 0: the Pryces' rent is taken; make it arrears instead by emptying h2 first
    ...                                      # landlord's request for the overdue $185 is accepted: it counts toward the arrears; collect takes $0 for that due
def test_a_loan_on_request_does_not_touch_a_standing_payment(): ...   # match="arrears": no arrears, so nothing is matched
def test_a_first_name_resolves_only_when_one_person_known_bears_it():
    s = thin_town()
    assert resolve(s, "megan", actor="p01") == "p04" and resolve(s, "Megan Pryce") == "p04"
    add_person(s, "p09", "Megan Shaw"); tie(s, "p01", "p09", "friend")
    assert resolve(s, "Megan", actor="p01") is None
def test_a_letter_to_a_shop_reaches_its_owner(): ...      # Message.to == owner, Message.org == org id
def test_a_request_wakes_on_arrival_and_on_its_last_day(): ...
def test_the_seed_is_part_of_the_hash(): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/town/test_accounts.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Keep `account_of` as the household account of a person; operations stop using it for the paying side.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, the thin town and random play included (update pinned hashes if the new fields change them, and say so).
- [ ] **Step 5: Commit** `town: more than one account, positions, names by acquaintance, requests that name what they are for`.

### Task 2: Shops, suppliers and shopping

**Files:**
- Create: `src/townsim/town/firms.py`
- Modify: `src/townsim/town/entities.py`, `store.py` (tables `firms`, `suppliers`, `buyers`, `tills`)
- Test: `tests/town/test_firms.py`

**Interfaces:**
- Rows: `FirmRow(id, org, need, price: int, quality: float, visibility: float, reputation: float, units_per_staff: int, shelf_weeks: int, stock_units: list[int], stock_weeks: list[int], order_supplier: str, order_units: int, last_supplier: str, last_units: int, sold: int, turned_stock: int, turned_capacity: int, spoiled: int, first_served: int, first_refused: int, extra_hours: float)` (prefix `fm`); `SupplierRow(id, org, unit_cents: int, reliability: float)` (prefix `sp`); `Buyer(id, account, size: int, wtp: float, budget: int, shop_day: int, last_bought: str)` (prefix `by`; `account` is a household id, or `outside:households` for a background buyer); `TillWeek(id, firm, week, price, sold, turned_stock, turned_capacity, spoiled, stock_left, cash)` (prefix `tw`).
- `staff_hours(store, org) -> float`: contracted hours of everyone with a job there (table `jobs`, Task 7; until then a field-free stand-in that tests can set through `FirmRow.extra_hours`), plus the firm's `extra_hours` this week
- `capacity(store, firm_id) -> int`: `units_per_staff * staff_hours // 38`
- `deliver(store) -> None` (Mondays), `shop_today(store) -> None` (every day), `close_week(store) -> None` (Sundays, after people have acted)

**Rules:**
- `shop_today` puts every buyer whose `shop_day` is today's weekday through `townsim.econ.demand.shop` for need `food`, with `week = clock.week(store.day)`, `seed = store.seed`, `demand_mult = 1.0`. The econ function is handed a stand-in ledger and stand-in firm objects; nothing in `econ` is changed. The stand-in ledger answers `balance` from the store plus what this call has already moved (a credit account answers a very large number) and gathers transfers; afterwards one `store.transfer` is made per (payer account, payee account) with memo `food x<n>` and cause `routine:shopping`. Firm rows are written back with `store.set`. The whole call is one transaction.
- A shop refuses a customer when it has no stock or has already sold its week's capacity.
- `deliver`: for each firm, the order is `order_units` from `order_supplier` if an order was placed since last Monday, else last week's order again. The supplier sends `Supplier.fill(units, Random(f"fill:{seed}:{week}:{firm id}"))`, cut to `balance // unit_cents`. The firm pays the supplier's account for what arrives (cause `routine:delivery:<firm id>`, memo `<n> units from <supplier name>`), the units join the stock as a new cohort, `last_supplier` and `last_units` record what was ordered, and `order_units` returns to 0. Stock is paid for on delivery and never billed.
- `close_week`: stock past `shelf_weeks` is thrown away and counted; reputation moves as `econ.firm.Firm.update_reputation` moves it; supplier prices and reliability drift as `Supplier.drift` with `Random(f"drift:{seed}:{week}:{supplier id}")`; a `TillWeek` row records the week; the week's counters and `extra_hours` return to 0.

- [ ] **Step 1: Write the failing tests**

```python
def test_shopping_conserves_money_and_logs_every_move():
    s = shops_town(seed=3); before = len(s.ledger.transfers)
    for _ in range(7): firms.shop_today(s); s.advance_day()
    assert s.ledger.total() == 0 and len(s.ledger.transfers) > before
    assert all(t.cause == "routine:shopping" and t.memo.startswith("food x") for t in s.ledger.transfers[before:])
def test_shopping_can_be_undone_exactly(): ...           # mark, shop_today, undo_to: hash equal
def test_shopping_is_the_same_whatever_the_hash_seed(): ...   # two subprocesses, equal hashes after a week
def test_an_empty_shelf_turns_customers_away(): ...      # stock 0: sold 0, turned_stock > 0, no money moves to that shop
def test_capacity_is_counted_over_the_week(): ...        # units_per_staff 100, 38 staff hours: at most 100 sold across the seven days
def test_nobody_ordering_repeats_last_week_exactly():
    s = shops_town(); set_order(s, "fm1", "sp1", 300); firms.deliver(s); run_week(s); firms.deliver(s)
    assert [c for c in delivered(s, "fm1")] == [300, 300] or reliability_cut_noted(s)   # with reliability 1.0: exactly [300, 300]
def test_a_delivery_is_cut_to_what_the_shop_can_pay_for():
    s = shops_town(); set_cash(s, "org3", 100_000); set_order(s, "fm1", "sp1", 300)      # $1,000 at $7.00 a unit
    firms.deliver(s); assert stock(s, "fm1") == 142 and s.balance("org3") == 600
def test_old_stock_is_thrown_away_and_the_week_is_recorded(): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/town/test_firms.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Read `src/townsim/econ/demand.py`, `firm.py` and `suppliers.py` first; reuse their classes for the arithmetic by building them from rows and writing the rows back.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `town: shops, suppliers and shopping through the one rule of demand`.

### Task 3: Hours

**Files:**
- Create: `src/townsim/town/hours.py`
- Modify: `src/townsim/town/entities.py`, `store.py` (table `work`), `loop.py`
- Test: `tests/town/test_hours.py`

**Interfaces:**
- `WEEK_HOURS = 70.0`; `Work(id, holder, account, what, hours: float)` (prefix `wk`); `WORK_HOURS = {"household paperwork": 1.5, "ordering": 4.0, "the books": 3.0, "rota and pay": 2.0}`
- `job_hours(store, pid) -> float` (0 until Task 7 adds jobs), `free_hours(store, pid) -> float`: `WEEK_HOURS - job_hours - sum of hours of work held`, never below 0, and 0 in a week that begins with `ill_until >= store.day`
- `monday_hours(store) -> None`: sets every person's `hours_week` and `hours_left` to `free_hours` (cause `routine:week`); `loop.routine` calls it on Mondays in place of the plain reset

- [ ] **Step 1: Write the failing tests**

```python
def test_free_hours_are_what_a_job_and_recurring_work_leave():
    s = town_with_shop(); give_work(s, "p01", "org3", "ordering"); give_work(s, "p01", "org3", "the books"); give_work(s, "p01", "h1", "household paperwork")
    hours.monday_hours(s); assert s.get("people", "p01").hours_left == 61.5
def test_an_ill_week_has_no_hours(): ...                 # ill_until == day + 3 on a Monday: 0.0; the next Monday: back
def test_hours_never_go_below_zero(): ...
def test_a_person_with_no_hours_is_refused_plainly(): ...  # apply_act: "Not done: you have no time left this week for that."
```

- [ ] **Step 2: Run**, expect FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q`, expect PASS. **Step 5: Commit** `town: hours in a week, less a job and recurring work`.

### Task 4: The generator

**Files:**
- Create: `src/townsim/town/gen.py`, `src/townsim/town/names.py`, `config/families.yaml`
- Modify: `entities.py`, `store.py` (tables `jobs`, `events`)
- Test: `tests/town/test_gen.py`

**Interfaces:**
- `Job(id, person, org, weekly: int, hours: float, wage_ob: str, since: int)` (prefix `jb`); `Event(id, day, kind, who, amount: int, weeks: int, done: bool = False)` (prefix `ev`)
- `generate(seed: int, families: list[str]) -> Store`, `check(store) -> list[str]` (empty when sound), `describe(store) -> str` (a page a person could read: who lives where with whom, who works where for what, who owns and owes what, the deck)
- `names.FIRST_NAMES`, `names.SURNAMES`, `names.STREETS`, `names.PLACES`, `names.SHOP_WORDS`: plain lists, at least 60 first names
- `config/families.yaml`: a mapping of family name to `{model: <id>, params: {...}, left_out: false}`; the engine reads only the family names

**What a generated place holds:** the place's name; eighteen people in seven to nine households, each with age, kind, voice, two to four beliefs drawn on fixed axes (trust in institutions, attitude to change, attitude to technology, religion, politics, appetite for risk) worded as plain statements, interests, and worries that follow from their circumstances; ties (household, kin across households, friends, coworkers, landlord and tenant, creditor and debtor); three food shops with owners, staff, stock, prices, a supplier each and recurring work held by the owner; two outside suppliers; a bank organisation owned by nobody, with a banker whose job is there, holding at least three loans; one landlord person who lets three houses; a software firm of two people paid from its own account, which is funded by an outside investor's weekly payment; an outside employer paying three people; pensions or benefit for those without work; about 120 background buyers; for every household: wages in, rent or mortgage, energy, `regular spending`, any loan instalments, all as obligations, a `Buyer` row, a shopping day, `household paperwork` held by one adult; opening balances moved in from outside accounts; a deck of four to seven dated events over the first twelve weeks, of kinds `illness` (a person, a week), `job_lost` (someone employed outside the hamlet), `repair` (a household, a one-off bill), `supplier_short` (a supplier, a week), `cold_month` (energy bills up by 30% for four weeks). Model families are dealt at random within each kind of person.

**`check` refuses a place unless:** everyone is in exactly one household; partners are within eight years of age; every wage is at least `1100` cents an hour; nobody works for a firm their own household owns unless they own it; one landlord lets at least three houses; the largest shop employs at least 40% of the three shops' staff; the bank holds at least three loans, one of them to a firm; at least two shops serve the same need; there is a tenant and landlord pair and a creditor and debtor pair among residents; the banker owes the bank nothing; full names are unique, no two people known to the same person share a first name with a third in a way that leaves any household member's first name ambiguous within the household, and no person is named like an organisation; each shop's expected weekly takings at its starting price, worked out with `econ.demand.true_choice_probabilities`, cover its wages, supplies and fixed costs to within 25% either way; every household's regular income is between 85% and 130% of its regular outgoings, with at least two households below 100%; each family plays at least two people and each kind with four or more people is played by at least two families; `Ledger.total()` is 0. `generate` redraws with `Random(f"gen:{seed}:{attempt}")` until `check` is empty, at most 200 times, then raises.

- [ ] **Step 1: Write the failing tests**

```python
FAMILIES = ["a", "b", "c", "d"]
@pytest.mark.parametrize("seed", range(1, 31))
def test_every_seed_gives_a_sound_place(seed):
    s = gen.generate(seed, FAMILIES); assert gen.check(s) == [] and s.ledger.total() == 0 and len(s.all("people")) == 18
def test_the_same_seed_gives_the_same_place():
    assert gen.generate(7, FAMILIES).hash() == gen.generate(7, FAMILIES).hash() != gen.generate(8, FAMILIES).hash()
def test_seed_1_is_frozen():
    assert gen.generate(1, FAMILIES).hash() == SEED_1_HASH        # a constant in the test file, changed only on purpose
def test_ordinary_inequality(): ...      # a landlord with >= 3 lets; the largest shop >= 40% of shop staff; the bank's book >= 3 loans
def test_families_are_dealt_within_kinds(): ...
def test_the_deck_is_dated_and_fixed_at_generation(): ...   # 4 to 7 events, all with day < 84, identical on regeneration
def test_nothing_in_a_generated_place_mentions_what_it_is_for():
    text = gen.describe(gen.generate(1, FAMILIES)).lower()
    for word in ("simulat", " ai ", "artificial", "language model", "experiment", "test"): assert word not in text
def test_a_generated_place_runs_a_week_of_routine_without_error(): ...   # loop.routine + firms for 7 days: total 0, engine_errors == []
```

- [ ] **Step 2: Run**, expect FAIL. **Step 3: Implement.** Read `spikes/town2/gen.py` for the shape of budgets from spending shares (:462–478), worries from circumstances (:362–389) and ties (:185–236); take nothing from it that fixes the number or names of people. **Step 4: Run** `uv run pytest -q`, expect PASS (the thirty seeds in under fifteen seconds). **Step 5: Commit** `town: a generated hamlet, frozen by its seed`.

### Task 5: What a person is shown

**Files:**
- Create: `src/townsim/town/facts.py`, `src/townsim/town/aims.py`
- Modify: `src/townsim/town/views.py`, `entities.py`, `store.py` (table `aims`), `ops_core.py` (the unknown-name note on delivery)
- Test: `tests/town/test_views.py` (extend), `tests/town/test_aims.py`

**Interfaces:**
- `facts.who(store, pid) -> str`: the system message's account of the person, in the second person, from their rows: name and age; household, street, and whether they rent (from whom) or own; work, hours and pay, or their business and its staff; what they owe and to whom in one line each; the people closest to them (rank 0 to 2) with the relation; beliefs, interests and worries as they stand; the voice line
- `facts.business(store, pid) -> str` for an owner: the shop's account balance, price, stock on the shelf by age, what is on order and from whom, staff with pay and hours, and the last two `TillWeek` rows as a till prints them (sold, turned away for want of stock, turned away for want of staff, thrown away)
- `facts.KNOWHOW: dict[str, list[str]]` by kind of person
- `aims.Aim(id, person, text, since: int)` (prefix `am`); `aims.update(store, pid, aims, cause) -> None` by decision 7 (each aim cleaned, cut to 30 words; more than five keeps the first five; a non-list changes nothing); `aims.lines(store, pid) -> list[str]` as `for <n> days: <text>`, oldest first
- `views.inbox(store, pid) -> tuple[list, int, str]`: the items shown in full, how many more wait, and the counted line `(<n> more: <a> letters, <b> notices)`
- `views.REPLY_FORM`: `{"thoughts": "<what goes through your mind; no one else sees this>", "aims": [<up to five short lines: what is on your mind now>], "acts": [<operations from the list, in order>]}`

**Rules:**
- The user message's sections, in order, a heading omitted when its section is empty: `TODAY`; `WHAT CAME OF YOUR LAST ACTS`; `YOUR HOUSEHOLD'S ACCOUNT`; `YOUR BUSINESS` (owners); `WHAT IS OWED` (`agreements_text` for each account they control); `LETTERS AND NOTICES`; `WAITING FOR YOUR ANSWER`; `APPOINTMENTS` (Task 9; empty until then); `PEOPLE YOU KNOW` (name and relation, closest first, at most twelve); `HOW THINGS WORK` (`KNOWHOW` for their kind); `ON YOUR MIND`; `YOUR NOTES`; the hours line; `OPERATIONS`.
- Inbox: unseen letters and notices sorted by (tie rank of the sender, with a notice ranking as 0; day; id). The first three are shown in full and marked seen with the turn; the rest are counted and stay unseen. Requests waiting are sorted the same way; three are shown with their summaries and the rest counted.
- A letter shows `From <name>` or `From <name> to <organisation>`; beneath it, for each name in `Message.unknown`, `(<Name> is not someone you know.)`. `unknown` is worked out when the letter is delivered: every run of two capitalised words whose first is in `names.FIRST_NAMES` and which is not the full name of someone in `knows(reader)`.
- The cap covers system and user together. What gives way, in order: statement lines down to none; people you know beyond the six closest; the business's second till week; then the third and second inbox items move to the counted line. Results and requests in full never give way.
- `KNOWHOW` says how the trade works and nothing about what to do, for example for a shop owner: `Stock ordered by Saturday arrives on Monday and is paid for when it arrives.` `What is not sold in two weeks is thrown away.` `A member of staff working a full week can serve about <n> customers.` The same lines for every owner; none for kinds with no trade.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_inbox_shows_the_three_most_pressing_and_counts_the_rest():
    s = gen.generate(1, FAMILIES); pid = someone_with_a_partner_a_friend_and_a_landlord(s)
    send(s, landlord, pid, "about the gutter"); send(s, stranger, pid, "hello"); send(s, friend, pid, "Saturday?"); send(s, partner, pid, "the bill"); send(s, stranger2, pid, "a question")
    shown, more, line = views.inbox(s, pid)
    assert [senders(x) for x in shown] == [partner, friend, landlord] and more == 2 and line == "(2 more: 2 letters, 0 notices)"
def test_only_what_was_shown_in_full_is_marked_seen(): ...
def test_a_name_the_reader_does_not_know_is_noted_under_the_letter(): ...
def test_every_view_states_facts_only():
    s = gen.generate(1, FAMILIES)
    for p in s.all("people"):
        text = "\n".join(m["content"] for m in views.person_view(s, p.id)).lower()
        for phrase in ("you should", "you may", "you can", "you could", "consider", "try to", "it would be", "remember to", "simulat", "language model", "experiment"):
            assert phrase not in text
def test_owners_see_the_same_knowhow_word_for_word(): ...
def test_a_view_changes_nothing(): ...                    # hash and len(changes) unchanged for all eighteen
def test_aims_keep_their_age_when_they_are_the_same_matter():
    s = thin_town(); aims.update(s, "p03", ["Find the $185 for Monday's rent"], "op:p03:aims:d0.0"); run_days(s, 9)
    aims.update(s, "p03", ["Find $185 for the rent on Monday", "Ask Megan about the car"], "op:p03:aims:d9.0")
    assert aims.lines(s, "p03") == ["for 9 days: Find $185 for the rent on Monday", "for 0 days: Ask Megan about the car"]
def test_aims_survive_junk(): ...                          # None, a string, a list of numbers, six aims, a 500-word aim
def test_the_cap_holds_with_everything_at_once(): ...      # an owner with twenty letters, long notes, a full statement: <= 1200 words
```

- [ ] **Step 2: Run**, expect FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q`, expect PASS. **Step 5: Commit** `town: a person is shown their life as facts, the three most pressing letters, and what is on their mind`.

### Task 6: A day in the hamlet

**Files:**
- Create: `src/townsim/town/hamlet.py`
- Modify: `src/townsim/town/loop.py`, `obligations.py`, `src/townsim/cli.py`, `tests/town/test_random_play.py`
- Test: `tests/town/test_hamlet.py`, `tests/test_cli.py`

**Interfaces:**
- `loop.routine(store)` in this order: on Mondays `hours.monday_hours`; today's events (below); on Mondays `firms.deliver`; lapse requests; `open_dues`; `age_arrears`; `collect`; `firms.shop_today`
- `loop.close(store)`: on Sundays `firms.close_week`; then `advance_day`. `run_day` is `routine`, people, `close`.
- `run_day` applies a reply's `aims` with `aims.update` (cause `op:<pid>:aims:d<day>.<n>`) before its acts.
- `obligations.collect`: when a due that was in arrears is taken, each of `people_of(payer)` gets a notice `<memo>, due <date>, was taken on <date>.`
- `hamlet.apply_events(store)`: an `illness` sets the person's `ill_until` to the end of that week and tells them; `job_lost` ends an outside job from that Friday and tells the person; `repair` creates a once-only `bill` and tells the household; `supplier_short` halves that supplier's reliability for the week; `cold_month` creates four once-only weekly bills of 30% of the household's energy bill. Causes `routine:event:<event id>`. Notices state what happened and nothing else.
- `hamlet.script(key, messages) -> str`: a scripted life for a generated place, reading names and ids out of the view text; `townsim town-hamlet --seed 1 --weeks 8 --db PATH --run-id ID` prints the weekly line, engine errors and `replay ok`, as `town-thin` does.

- [ ] **Step 1: Write the failing tests**

```python
def test_eight_weeks_in_the_hamlet_conserve_and_replay(tmp_path):
    db = RunDB(str(tmp_path / "h.sqlite")); db.create_run("h1", seed=1)
    final = run(db, "h1", gen.generate(1, FAMILIES), ScriptedReplier(hamlet.script), 56)
    assert final.ledger.total() == 0 and db.errors("h1") == [] and replay(db, "h1") == []
def test_nothing_is_collected_twice_and_no_view_is_over_length(): ...   # every due one collect cause; every person every day <= 1200 words
def test_events_happen_on_their_day_and_are_told_plainly(): ...
def test_arrears_taken_are_told(): ...
def test_shops_have_customers_and_tills_every_week(): ...                # each shop sold > 0 in at least six of eight weeks
@pytest.mark.parametrize("seed", range(20))
def test_random_play_in_the_hamlet_never_breaks_the_books(seed): ...     # the invariants of the thin-town random play, on gen.generate(seed + 1, FAMILIES), 28 days
```

- [ ] **Step 2: Run**, expect FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q` (under a minute) and `uv run townsim town-hamlet --seed 1 --weeks 8 --db <tmp>/h.sqlite --run-id h1`. Expected: PASS; `replay ok`. **Step 5: Commit** `town: a day in the generated hamlet, with shopping, deliveries and events`.

### Task 7: Jobs and running a shop

**Files:**
- Create: `src/townsim/town/ops_work.py`, `src/townsim/town/jobs.py`
- Modify: `obligations.py` (`end`), `firms.py` (`staff_hours` from jobs), `hours.py` (`job_hours`), `ops_core.py` (`answer` for request kinds), `facts.py`
- Test: `tests/town/test_jobs.py`, `tests/town/test_ops_work.py`

**Interfaces:**
- `obligations.end(store, ob_id, last_day: int, cause) -> None`: no instalment after `last_day`; dues already opened stand
- `jobs.MIN_HOURLY = 1100`, `jobs.BENEFIT_WEEKLY = 14_000`; `jobs.start(store, pid, org, weekly, hours, cause) -> str`; `jobs.finish(store, job_id, cause) -> None`; `jobs.pay_benefit(store) -> None` (routine, Mondays)
- Operations, with their forms: `offer_job` (`{"op": "offer_job", "to": "<name>", "weekly_pay": <dollars>, "hours": <hours a week>, "text": "<optional words>"}`, owner, 0.5 h), `offer_shifts` (`{"op": "offer_shifts", "to": "<name>", "hours": <extra hours this week>}`, owner, 0.25), `quit` (`{"op": "quit"}`, employed, 0.1), `dismiss` (`{"op": "dismiss", "who": "<name>", "text": "<optional words>"}`, owner, 0.5), `set_staff_pay` (`{"op": "set_staff_pay", "who": "<name>", "weekly_pay": <dollars>}`, owner, 0.1), `set_price` (`{"op": "set_price", "price": <dollars>}`, owner, 0.1), `order_stock` (`{"op": "order_stock", "supplier": "<name>", "units": <number>}`, owner, 0.5), `work_extra_hours` (`{"op": "work_extra_hours", "hours": <number>}`, owner, the hours themselves), `move_money` (`{"op": "move_money", "from": "household" | "<your business>", "to": "household" | "<your business>", "amount": <dollars>}`, owner, 0.1)

**Rules:**
- A job is a `Job` row and a `wage` obligation from the employer's account to the person's household every Friday, `ends="both"`. `jobs.finish` ends the obligation after that week's Friday and removes the job's hours from the next Monday.
- `offer_job` and `offer_shifts` are requests (`kind` `job`, `shifts`) with a summary written by code: `<org> offers <name> a job: <$x> a week for <h> hours, paid Fridays, from Monday <date>.`, and for a person who has a job, ` Accepting ends your job at <other org> on <date>.` Accepting is `answer`. Terms are checked again at acceptance: the offerer still owns the firm, pay is at least `MIN_HOURLY` an hour, the person has the hours.
- Shifts: accepted hours are taken from the person at once, added to the firm's `extra_hours`, and paid as a once-only `wage` obligation on Friday of `round(hours * weekly / contract hours)` cents.
- `quit` and `dismiss` end the job after that week's Friday pay; the other side gets a notice. Benefit: a person with no job and not past pension age gets a `wage` obligation `jobseeker's benefit` from the regional office every Friday from the week after the job ends; it ends when a job starts.
- `set_staff_pay` for someone else below the minimum is refused; it ends the old wage obligation after this Friday and starts the new one the Friday after. For oneself it sets the owner's draw, an obligation from the firm to their household.
- `order_stock` replaces any order already placed this week; units must be a whole number from 1 to 5,000; the supplier must be one of the two. `set_price` takes effect at once; between $1 and $100.
- `move_money` moves between accounts the actor controls; the same account twice is refused.
- An owner's `work_extra_hours` adds to the firm's `extra_hours` this week and costs the owner those hours.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_accepted_offer_makes_a_wage_due_on_fridays(): ...   # $440 for 38 hours from Monday: first wage Friday of that week, 44_000, from the shop's account
def test_quitting_on_tuesday_is_paid_that_friday_and_never_again(): ...
def test_a_new_job_ends_the_old_one_and_the_summary_said_so(): ...
def test_pay_below_the_minimum_is_refused_at_offer_and_at_acceptance(): ...
def test_eight_extra_hours_at_440_for_38_pay_9263_once(): ...
def test_wages_from_an_empty_till_go_to_arrears_and_the_owner_is_told(): ...
def test_wages_paid_by_hand_from_the_household_count_toward_the_wage():   # Review Focus 1
    ...   # the owner pays an employee $440 from the household on Thursday: Friday's wage takes $0 from the shop
def test_benefit_starts_the_week_after_and_ends_with_a_job(): ...
def test_an_order_placed_by_saturday_arrives_monday_and_replaces_an_earlier_one(): ...
def test_extra_hours_raise_this_weeks_capacity_only(): ...
def test_moving_money_between_ones_own_accounts(): ...
def test_junk_in_every_argument_of_every_new_operation(): ...   # the junk table of test_ops_core, applied to these nine
```

The fourth-from-last rule needs deciding in code: a hand payment from the owner's household to an employee's household is between different accounts than the wage (shop to household), so it would not be matched. Make `pay` by an owner, when the payee is owed a wage by a firm the payer owns and no obligation exists between the two accounts themselves, count toward that wage as arrears then next instalment, and say so in what the payer is told.

- [ ] **Step 2: Run**, expect FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q`; extend the hamlet random play to these operations. **Step 5: Commit** `town: jobs, pay, shifts, and running a shop`.

### Task 8: Agreements, loans and rent

**Files:**
- Create: `src/townsim/town/ops_deals.py`
- Modify: `obligations.py` (`repay`), `ops_core.py`, `facts.py`
- Test: `tests/town/test_ops_deals.py`

**Interfaces:**
- `obligations.repay(store, ob_id, cents, cause) -> list[Match]`: one transfer; counts toward arrears, then the next instalment, then whole instalments taken off the end of the schedule (moving `last` earlier); a remainder smaller than an instalment reduces the last remaining instalment
- Operations: `propose_agreement` (`{"op": "propose_agreement", "to": "<name>", "amount": <dollars>, "per": "week" | "once", "weeks": <number, or 0 for until ended>, "start": "<first date>", "pays": "me" | "them", "what": "<plain words>"}`, all, 0.5 h), `propose_end` (`{"op": "propose_end", "id": "<id of the agreement>"}`, all, 0.25), `borrow` (`{"op": "borrow", "amount": <dollars>, "weeks": <number>, "purpose": "<what for>"}`, all, takes `from`, 1.0), `repay` (`{"op": "repay", "id": "<id of the loan or agreement>", "amount": <dollars>}`, all, takes `from`, 0.25), `set_rent` (`{"op": "set_rent", "tenant": "<name>", "weekly": <dollars>}`, landlord, 0.25), `serve_notice` (`{"op": "serve_notice", "tenant": "<name>"}`, landlord, 0.5)

**Rules:**
- An agreement is proposed as a request of kind `agreement` whose summary code writes: `<payer> pays <payee> <$x> every week from <date> for <n> weeks (<$total> in all): <what>.` Accepted, it becomes one obligation of kind `agreement`, `ends="both"`, listed for both. Terms are checked again at acceptance. A proposal that would start in the past is refused.
- `propose_end` sends a request of kind `end`; accepted, the agreement ends after today (`obligations.end`) with status `finished`, and no breach is recorded.
- `borrow` sends a request of kind `loan` to the banker with the summary `<name> asks the bank for <$x> over <n> weeks, repaying <$w> a week (<$total> in all) from <date>: <purpose>. They now owe the bank <$y> on <k> loans, <$z> of it overdue.` The total is `round(principal * (1 + 0.12 * weeks / 52))` and the weekly sum `ceil(total / weeks)`, the last instalment taking the rest. Accepted by the banker, the principal moves from the bank's account to the account named and a `loan` obligation starts the following Friday. Between $50 and $20,000, 4 to 104 weeks.
- `repay` draws on `from_account` and goes through `obligations.repay`.
- `set_rent` tells the tenant, ends the present rent obligation 28 days on and starts the new one the same day; a second change before the first takes effect replaces it. `serve_notice` is refused unless the tenant's rent has been in arrears for 14 days; it adds `Record(kind="notice to quit")` and tells the tenant the date, 28 days on. Nothing further follows in this plan.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_agreement_is_one_thing_listed_for_both_with_its_start_date(): ...
def test_what_was_only_said_in_a_letter_is_listed_nowhere(): ...
def test_terms_are_checked_again_when_accepted(): ...        # the proposer has since left the hamlet's shop they proposed for: refused
def test_ending_by_agreement_is_no_breach(): ...
def test_a_loan_of_1000_over_26_weeks_repays_4077_a_week_and_1060_in_all(): ...   # total 106_000; weekly 4_077; last instalment 4_075
def test_the_banker_decides_and_the_money_moves_once(): ...
def test_repaying_early_shortens_the_loan_from_its_end(): ...
def test_a_loan_repaid_by_repay_is_not_also_taken_in_full_by_its_schedule(): ...   # Review Focus 1
def test_a_rent_change_takes_28_days_and_the_tenant_is_told(): ...
def test_notice_needs_14_days_of_arrears(): ...
def test_junk_in_every_argument_of_every_new_operation(): ...
```

- [ ] **Step 2: Run**, expect FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest -q`; extend the hamlet random play to these operations. **Step 5: Commit** `town: agreements, loans from the banker, and rent`.

### Task 9: Talking

**Files:**
- Create: `src/townsim/town/talk.py`
- Modify: `entities.py`, `store.py` (tables `talks`, `lines`, `appointments`), `loop.py` (the day's third part), `views.py`, `ops_core.py` or a new `ops_talk.py`, `hamlet.py`
- Test: `tests/town/test_talk.py`

**Interfaces:**
- Rows: `Talk(id, day, a, b, kind, opening, status)` with kind `asked | appointment | household` and status `waiting | held | not held` (prefix `tk`); `Line(id, talk, n, speaker, text)` (prefix `ln`); `Appointment(id, day, a, b, about, made_on, status)` with status `booked | held | missed` (prefix `ap`)
- Operations: `converse` (`{"op": "converse", "with": "<name>", "opening": "<your first words>"}`, all, 1 h) and `book_appointment` (`{"op": "book_appointment", "with": "<name>", "day": "<e.g. Thursday 12 March>", "about": "<what for>"}`, all, 0.1 h)
- `talk.hold(store, replier, keys, talk_id) -> None`; `talk.todays(store) -> list[str]` (appointments for today by id, then asked talks by id, then household talks); `talk.view(store, pid, talk_id) -> list[dict]`
- `loop.run_day`: `routine`, people, `talk` (every talk in `todays`), `close`

**Rules:**
- `converse` books a talk for today and takes the hour from the asker at once. It is held after everyone has acted, if the other person has an hour left and neither has already had two talks today; otherwise its status is `not held`, the asker's hour is given back, and the asker is told `<name> had no time to talk today.`
- An appointment for today is held the same way; if either has no hour it is `missed` and both are told. Booking one for a day already past, or today, is refused.
- A household talk is held on Sundays between two adults of a household when either has an unseen letter from the other, an aim naming the other, or an obligation of the household is in arrears; at most one a household a week.
- A talk: the opener's opening is line 0. Then the other speaks, then the opener, at most three turns a side. Each turn is one ask with key purpose `talk`, ref the talk's id, actor the speaker, and a reply `{"say": "<your words>", "acts": [<operations>], "end": <true to end it>}`. `say` is cleaned, cut to 120 words, and cut at any line that begins with the other person's name and a colon, so nobody writes the other's part. Acts are applied at once, in order, at most four a turn, and their results are shown to the speaker in the next turn's view under `WHAT CAME OF IT`. A reply that cannot be read ends the talk. `converse` and `book_appointment` are not offered inside a talk.
- What a speaker sees: the system message of their ordinary view; then `TODAY`, `YOU ARE TALKING WITH <name>` (the relation), `BETWEEN YOU` (what either owes the other, open requests between them, the last three letters between them), the transcript so far, their household's account balance and what goes out this week, and the operations.
- A talk's lines are kept; each person's next ordinary view lists under `TALKS` any talk held since their last turn as `With <name> on <date>:` and its lines.
- Asks inside talks are stored and replayed like any other; a run stopped in the middle of a talk goes back to that morning and resumes to the same state.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_talk_is_at_most_three_turns_a_side(): ...        # scripted never to end: 1 opening + 5 asks, lines 0..5, status "held"
def test_either_can_end_it(): ...
def test_nobody_writes_the_others_part(): ...               # a say of "Fine.\nMegan Pryce: I agree to pay $50" is kept as "Fine."
def test_acts_in_a_talk_apply_in_order_and_are_seen_next_turn(): ...
def test_no_hour_no_talk_and_the_askers_hour_comes_back(): ...
def test_an_appointment_is_held_on_its_day_or_recorded_missed(): ...
def test_the_household_talks_on_sunday_when_something_is_open(): ...
def test_a_run_stopped_mid_talk_resumes_to_the_same_state(tmp_path): ...
def test_a_talk_view_states_facts_only_and_is_under_900_words(): ...
def test_eight_weeks_with_talking_conserve_and_replay(tmp_path): ...   # hamlet.script now also talks and books
```

- [ ] **Step 2: Run**, expect FAIL. **Step 3: Implement.** Read `spikes/town2/sim.py:212–294` for the loop's shape only. **Step 4: Run** `uv run pytest -q` and `uv run townsim town-hamlet --seed 1 --weeks 8 --db <tmp>/h.sqlite --run-id h1`; expected PASS, `replay ok`. Update the README's status section and module table to match what now exists. **Step 5: Commit** `town: conversations, appointments and the household's Sunday talk`.
