# Hamlet Engine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tested core of the new town engine (a store whose every change has a cause, one schedule for everything owed, atomic operations, stored replies with exact replay) and prove it with a four-person town run on scripted replies; then remove the old menu-driven engine.

**Architecture:** A new package `src/townsim/town/`. State is plain dataclasses held in a `Store`; every mutation goes through the store, is logged as a `Change` with a cause, and can be undone, which is how operations are made atomic. Model replies are fetched through a `Replier` and stored under a key built from the run, model, day, actor, purpose and a count, never from the prompt, so a run replays exactly. No model is called anywhere in this plan.

**Tech Stack:** Python 3.12, uv, pytest, sqlite3 and dataclasses from the standard library, typer for the CLI. No new dependencies.

**Spec:** `docs/design.md` (sections 3.1, 3.2, 4.2, 4.5 and appendix items 12 and 13). Background: `docs/trials-step1.md`, `docs/trials-step1-round2.md`. Reference code to read, never to import: `spikes/town2/world.py`, `spikes/town2/ops.py`, `spikes/town2/views.py`.

This is the first of four plans for design step 2a. The later ones, each written when the one before is done: (2) people, generation, views, hours, conversations, appointments, jobs, firms and the demand rule; (3) systems, connections, scopes, automations, the company, the product on tool calls; (4) narrator, web, outsiders, bank, measures and the story reader.

## Global Constraints

- Money is integer cents. `Ledger.total()` is 0 after every operation and every day.
- The store changes only through `Store` methods, and every change carries a non-empty cause: `routine:<what>`, `op:<actor>:<op>:d<day>.<n>` (or `op:<actor>/<principal>:<op>:d<day>.<n>` for an act done for someone else), `auto:<id>` or `narrator:<id>`.
- Deterministic: no threads, no wall clock, no unseeded randomness, no iteration over a `set` or use of built-in `hash()` anywhere that reaches state or output. Where an order is needed among entities it is `id_key` order.
- No test calls a model or the network. Nothing in this plan spends money.
- An operation applies in full or not at all. A reply from a model is data: junk in it must never raise out of the engine.
- Wording shown to people states records and facts. It never says what the reader may or should do, never mentions tests, simulations or models. Every refusal told to an actor starts with `Not done:`.
- Do not import from `spikes/`. Do not import the old engine (`townsim.world`, `townsim.market`, `townsim.sim`, `townsim.events`, `townsim.runs`, `townsim.decide`) from `townsim.town`.
- No real names, email addresses or local paths in the repository. Commits are authored `conorg000 <20813490+conorg000@users.noreply.github.com>` and end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- Run the suite with `uv run pytest -q`. It must pass at every commit.

## Review Focus

1. **Junk acts from a weak model:** an amount given as `"$1,308.50"`, `"lots"`, `-5`, `true` or a list; an unknown operation; `args` that is not an object; a name that is no one. Expected: the act is refused with a `Not done:` message, nothing changes, the run continues. (Task 5)
2. **An account that cannot cover what is due:** expected: the instalment becomes arrears, is retried, draws one late fee at 7 days, a notice at 14, a default on the register at 28, and is never forgotten or partly taken. (Task 3)
3. **Paying by hand what is also scheduled:** expected: the payment counts toward the instalment and the schedule takes only what remains, so the payee never receives it twice. (Task 4)
4. **A run stopped in the middle of a day:** expected: the store goes back to that morning, replies already fetched are kept, and running again continues to the same final hash as an uninterrupted run. (Task 7)
5. **A view that has changed since a run was recorded:** expected: replay fails with an error naming the day, the actor and the key, instead of feeding an old reply to a different prompt. (Task 6, Task 7)

---

### Task 1: Calendar, and a time index on the ledger

**Files:**
- Create: `src/townsim/town/__init__.py` (empty), `src/townsim/town/clock.py`
- Modify: `src/townsim/econ/ledger.py`, `src/townsim/econ/sim.py:308` and any other user of `Ledger.week` or `Transfer.week`
- Test: `tests/town/__init__.py` (empty), `tests/town/test_clock.py`, existing `tests/econ/`

**Interfaces:**
- Produces: `clock.dow(day: int) -> int` (0 is Monday), `clock.week(day: int) -> int` (1-based, `day // 7 + 1`), `clock.DAYS: tuple[str, ...]`, `clock.date_text(day: int) -> str`, `clock.parse_date(text: str, today: int) -> int | None`.
- Produces: `Ledger.t: int` and `Transfer.t: int`, replacing `week`. It is the caller's time index: weeks in `econ`, days in `town`.

- [ ] **Step 1: Write the failing tests**

```python
def test_day_zero_is_monday_2_march():
    assert clock.dow(0) == 0 and clock.date_text(0) == "Monday 2 March"
    assert clock.date_text(14) == "Monday 16 March" and clock.date_text(30) == "Wednesday 1 April"
    assert clock.week(0) == 1 and clock.week(6) == 1 and clock.week(7) == 2

def test_parse_date_reads_what_people_write():
    assert clock.parse_date("Thursday 12 March", today=3) == 10
    assert clock.parse_date("12 March", today=3) == 10
    assert clock.parse_date("thursday", today=3) == 10      # the next Thursday after today
    assert clock.parse_date("Thursday", today=10) == 17     # never today
    assert clock.parse_date("Thursday 5 March", today=10) is None   # in the past
    assert clock.parse_date("soon", today=3) is None and clock.parse_date(None, today=3) is None
```

- [ ] **Step 2: Run** `uv run pytest tests/town/test_clock.py -q`. Expected: FAIL, module not found.
- [ ] **Step 3: Implement `clock.py`.** Day 0 is `date(2026, 3, 2)`. `date_text` has no year and no leading zero.
- [ ] **Step 4: Rename** `week` to `t` on `Ledger` and `Transfer` and at every use. Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `town: calendar, and a time index on the ledger`.

### Task 2: The store

**Files:**
- Create: `src/townsim/town/entities.py`, `src/townsim/town/store.py`
- Test: `tests/town/test_store.py`

**Interfaces:**
- Consumes: `townsim.econ.ledger.Ledger`, `Overdraft`.
- Produces, in `entities.py`, dataclasses (all fields plain values or lists of them):
  - `Person(id, name, household, hours_week: float, hours_left: float, stock_day: int, notes: str = "")`
  - `Household(id, name, members: list[str])`; its account id is its own id
  - `Org(id, name, kind: str)`; its account id is its own id
  - `Message(id, day, sender, to, text, seen: bool = False)`
  - `Request(id, day, sender, to, text, cents: int, expires: int, status: str = "open")` with status `open | accepted | declined | lapsed`
  - `Notice(id, day, to, text, seen: bool = False)`: something code tells a person
  - `Record(id, day, kind, about, text)`: an entry on the public register
  - `Obligation` and `Due` (fields in Task 3)
- Produces, in `store.py`:
  - `Change(day, table, id, field, old, new, cause)` (a NamedTuple)
  - `id_key(id: str) -> tuple[str, int]`: `"ob12"` gives `("ob", 12)`; ids are a prefix of letters and colons followed by digits
  - `class Store` with attributes `day: int`, `place: str`, `ledger: Ledger`, `changes: list[Change]`, `engine_errors: list[str]`, and methods:
    - `new_id(prefix: str) -> str` (`"ob1"`, `"ob2"`; counters are state and are undone with everything else)
    - `add(table: str, obj, cause: str) -> None`, `set(table: str, id: str, cause: str, **fields) -> None`, `get(table: str, id: str)`, `has(table, id) -> bool`, `all(table: str) -> list` in `id_key` order
    - `open_account(id: str, cause: str, allow_credit: bool = False) -> None`, `transfer(src, dst, cents, memo, cause) -> None`, `balance(account: str) -> int`
    - `advance_day(cause: str = "routine:close") -> None` (also sets `ledger.t`)
    - `mark() -> int`, `undo_to(mark: int) -> None`, `transaction()` (a context manager that undoes to its mark and re-raises if the body raises)
    - `canonical() -> dict`, `hash() -> str`
  - `TABLES = ("people", "households", "orgs", "obligations", "dues", "messages", "requests", "notices", "records")`

- [ ] **Step 1: Write the failing tests**

```python
def test_every_change_is_logged_with_its_cause():
    s = Store(); s.open_account("h1", "routine:setup"); s.open_account("outside:x", "routine:setup", allow_credit=True)
    s.add("households", Household("h1", "Ashby", ["p1"]), "routine:setup")
    s.set("households", "h1", "op:p1:x:d0.0", name="Ashby-Lee")
    s.transfer("outside:x", "h1", 500, "opening", "routine:setup")
    assert all(c.cause for c in s.changes)
    assert s.changes[-1].table == "ledger" and s.get("households", "h1").name == "Ashby-Lee"
    with pytest.raises(ValueError): s.set("households", "h1", "", name="x")
    with pytest.raises(KeyError): s.set("households", "nope", "routine:x", name="x")
    with pytest.raises(AttributeError): s.set("households", "h1", "routine:x", colour="red")

def test_undo_restores_the_exact_state():
    s = small_store(); before = s.hash(); m = s.mark()
    s.add("messages", Message(s.new_id("m"), 0, "p1", "p2", "hello"), "op:p1:message:d0.0")
    s.transfer("h1", "outside:x", 100, "x", "op:p1:pay:d0.1"); s.set("people", "p1", "routine:x", hours_left=1.0); s.advance_day()
    s.undo_to(m)
    assert s.hash() == before and s.ledger.total() == 0 and s.new_id("m") == "m1" and s.day == 0

def test_transaction_undoes_on_error_and_reraises():
    s = small_store(); before = s.hash()
    with pytest.raises(Overdraft):
        with s.transaction():
            s.set("people", "p1", "routine:x", notes="changed")
            s.transfer("h1", "outside:x", 10**9, "too much", "op:p1:pay:d0.0")
    assert s.hash() == before

def test_hash_is_stable_and_ignores_history():
    a, b = small_store(), small_store()
    assert a.hash() == b.hash()
    m = a.mark(); a.set("people", "p1", "routine:x", notes="n"); a.undo_to(m)
    assert a.hash() == b.hash()          # the change log is history, not state

def test_all_is_in_id_order_not_text_order():
    s = Store()
    for i in range(1, 12): s.add("notices", Notice(s.new_id("n"), 0, "p1", str(i)), "routine:x")
    assert [n.id for n in s.all("notices")][-2:] == ["n10", "n11"]
```

`small_store()` is a test helper: two people in two households with opened accounts and an `outside:x` account that allows credit.

- [ ] **Step 2: Run** `uv run pytest tests/town/test_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** `set` rejects a field the dataclass does not have. `add` stores the object itself and `set` uses `setattr`, so a `Change` for an added object records a copy (`dataclasses.replace`) and list-valued fields are replaced whole, never mutated in place. Undo walks `changes` backwards from the end to the mark, reversing each kind (field set, add, account opened, transfer, counter, day), and truncates the log. `canonical()` is `{"day", "place", "counters", "accounts": {id: [balance, allow_credit]}, "transfers": [...], "tables": {table: {id: asdict}}}`; `hash()` is the SHA-256 of `json.dumps(canonical, sort_keys=True, separators=(",", ":"))`.
- [ ] **Step 4: Run** `uv run pytest tests/town -q`. Expected: PASS.
- [ ] **Step 5: Commit** `town: a store whose every change has a cause and can be undone`.

### Task 3: One schedule for everything owed

**Files:**
- Create: `src/townsim/town/obligations.py`
- Modify: `src/townsim/town/entities.py` (add `Obligation`, `Due`)
- Test: `tests/town/test_obligations.py`

**Interfaces:**
- Consumes: `Store`, `clock.date_text`.
- Produces:
  - `Obligation(id, kind, payer, payee, cents, first: int, every: int, last: int | None, memo, ends, made_by, made_on: int, penalty: bool = False, status: str = "active")`. `kind` is one of `KIND_ORDER = ("wage", "rent", "loan", "bill", "agreement", "standing", "fee")`. `every` is days between instalments, 0 for once. `ends` is `"payer" | "both" | "service"`. `status` is `active | stopped | breached | finished`.
  - `Due(id, obligation, day: int, cents: int, paid: int = 0, status: str = "due", fee_charged: bool = False, notice_sent: bool = False)` with status `due | paid | arrears | defaulted`.
  - `LATE_FEE_CENTS = 2500`, `FEE_AFTER_DAYS = 7`, `NOTICE_AFTER_DAYS = 14`, `DEFAULT_AFTER_DAYS = 28`
  - `create(store, *, kind, payer, payee, cents, first, every=0, last=None, memo, ends, made_by, cause, penalty=False) -> str`
  - `falls_on(ob: Obligation, day: int) -> bool`
  - `ensure_due(store, ob_id: str, day: int, cause: str) -> Due` (the one existing row for that instalment, created if absent)
  - `open_dues(store) -> None`, `age_arrears(store) -> None`, `collect(store) -> None`, each acting on `store.day`
  - `people_of(store, account: str) -> list[str]` (a household's members; `[]` for anything else)
  - `Upcoming(day, obligation, other, cents, memo, out: bool)` and `upcoming(store, account: str, ahead: int = 7) -> list[Upcoming]`: instalments not yet paid that fall from today to `today + ahead`, plus all arrears, soonest first, `cents` being what remains

**Rules** (design 3.2):
- `collect` takes dues with status `due` or `arrears` and `day <= store.day`. Wages go first, oldest first. Then everything else, ordered by `(due.day, KIND_ORDER.index(kind), id_key(due.id))`.
- For each, the remainder is `cents - paid`. If it is 0 the due becomes `paid`. If the payer's balance covers it (or the account allows credit), one transfer of the remainder is made with cause `routine:collect:<due id>` and memo `<ob.memo>, due <date_text(due.day)>`, and it becomes `paid`. Otherwise it becomes `arrears` and nothing is taken.
- `age_arrears`, for each due in `arrears` with age `store.day - due.day`: at 7 days or more, once, and only if the obligation is not itself a penalty, create a once-only obligation of kind `bill`, `penalty=True`, for `LATE_FEE_CENTS` from the same payer to the same payee falling today, with memo `late fee on <ob.memo> due <date>`, and a `Notice` to each of `people_of(payer)`; at 14 or more, once, a `Notice` to the same people that the amount is two weeks overdue; at 28 or more the due becomes `defaulted`, is no longer retried, and a `Record(kind="default", about=<payer>)` is added.
- An obligation with `every == 0`, or with `last` set, becomes `finished` when its last instalment is paid.
- A stopped, breached or finished obligation opens no new dues. Dues already opened still stand.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_instalment_is_collected_once_on_its_day():
    s = town(h1=50_000)
    ob = O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=17_500, first=0, every=7, memo="rent", ends="both", made_by="routine", cause="routine:setup")
    run_days(s, 15)                      # open_dues, age_arrears, collect, advance_day
    paid = [t for t in s.ledger.transfers if t.dst == "outside:lettings"]
    assert [t.t for t in paid] == [0, 7, 14] and all(t.cents == 17_500 for t in paid)
    assert len({t.cause for t in paid}) == 3 and s.ledger.total() == 0

def test_what_cannot_be_paid_becomes_arrears_and_is_never_part_paid():
    s = town(h1=10_000)
    O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=17_500, first=0, memo="rent", ends="both", made_by="routine", cause="routine:setup")
    run_days(s, 1)
    assert s.balance("h1") == 10_000 and only_due(s).status == "arrears"
    s.transfer("outside:employers", "h1", 20_000, "wages", "routine:test"); run_days(s, 1)
    assert only_due(s).status == "paid" and s.balance("h1") == 12_500

def test_the_ladder_fee_at_7_notice_at_14_default_at_28():
    s = town(h1=0)
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=4_800, first=0, memo="energy", ends="service", made_by="routine", cause="routine:setup")
    run_days(s, 8)
    fees = [o for o in s.all("obligations") if o.penalty]
    assert len(fees) == 1 and fees[0].cents == 2_500 and "late fee on energy due Monday 2 March" == fees[0].memo
    run_days(s, 7);  assert sum("two weeks" in n.text for n in s.all("notices")) == len(O.people_of(s, "h1"))
    run_days(s, 14); assert first_due(s).status == "defaulted" and [r.kind for r in s.all("records")] == ["default"]
    assert len([o for o in s.all("obligations") if o.penalty]) == 1      # no fee on a fee, no second fee
    assert s.balance("h1") == 0 and s.ledger.total() == 0

def test_wages_land_before_anything_is_taken_and_older_debts_go_first():
    s = town(h1=0)
    O.create(s, kind="rent", payer="h1", payee="outside:lettings", cents=10_000, first=0, memo="rent", ends="both", made_by="routine", cause="routine:setup")
    O.create(s, kind="bill", payer="h1", payee="outside:northgrid", cents=10_000, first=2, memo="energy", ends="service", made_by="routine", cause="routine:setup")
    O.create(s, kind="wage", payer="outside:employers", payee="h1", cents=15_000, first=4, memo="wages", ends="both", made_by="routine", cause="routine:setup")
    run_days(s, 5)
    assert s.balance("h1") == 5_000 and status_of(s, "rent") == "paid" and status_of(s, "energy") == "arrears"

def test_upcoming_lists_what_remains_with_dates():
    ...  # rent 17,500 due day 7 with 5,000 already paid shows cents == 12_500, out is True; a wage shows out is False
```

- [ ] **Step 2: Run** `uv run pytest tests/town/test_obligations.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** to the rules above.
- [ ] **Step 4: Run** `uv run pytest tests/town -q`. Expected: PASS.
- [ ] **Step 5: Commit** `town: one schedule for everything owed, with arrears, fee, notice and default`.

### Task 4: Paying by hand, stopping, and the statement

**Files:**
- Modify: `src/townsim/town/obligations.py`
- Create: `src/townsim/town/statement.py`
- Test: `tests/town/test_paying.py`, `tests/town/test_statement.py`

**Interfaces:**
- Produces in `obligations.py`:
  - `Match(due: str, cents: int)` and `pay(store, payer, payee, cents, memo, cause, ref: str | None = None) -> list[Match]`. Raises `Overdraft` if the payer cannot cover it.
  - `stop(store, ob_id: str, by: str, cause: str) -> str`, returning `"stopped"`, `"left"` or `"breach"`. Raises `ValueError` if `by` is not the payer's account or the obligation is not active.
- Produces in `statement.py`:
  - `Line(day, other, cents, memo)` (cents negative when money left the account)
  - `Statement(account, day, balance, lines: list[Line], out: list[Upcoming], coming_in: list[Upcoming], after_out: int)`
  - `statement(store, account: str, back: int = 14, ahead: int = 7, max_lines: int = 25) -> Statement`
  - `statement_text(store, st: Statement) -> str`
  - `agreements_text(store, account: str) -> str`: every obligation the account pays or receives, with its id, memo, amount, how often, first date and status

**Rules** (design appendix 13):
- `pay` makes one transfer of the whole sum. It then counts the sum toward what is owed between the same two accounts: with `ref`, that obligation's arrears oldest first and then its next instalment; without, all arrears between the two accounts oldest first, then the single next instalment of any active obligation between them (created with `ensure_due`). It never counts toward more than one future instalment. What is left over is an ordinary payment and is recorded nowhere else. Each match raises that due's `paid`, and a due whose `paid` reaches `cents` becomes `paid`.
- When any of it was matched, the transfer's memo ends with ` (counted toward <ob.memo> due <date>)`.
- `stop`: `ends == "payer"` gives status `stopped` and returns `"stopped"`; `"service"` gives `stopped` and `"left"`; `"both"` gives `breached`, adds `Record(kind="breach", about=<payer>)` and returns `"breach"`.
- `after_out` is the balance less everything in `out`. Incoming money is listed apart and never netted into it.
- `statement_text` shows, in this order and with dates from `date_text`: the balance; what has come in and gone out, newest last; what goes out in the next seven days with its date and id; what is expected in; the balance after what goes out. Names are the other party's `name` when it is a household or org, and the text after `outside:` otherwise.

- [ ] **Step 1: Write the failing tests**

```python
def test_rent_paid_by_hand_is_not_taken_again():
    s = town(h1=50_000); rent = make_rent(s, cents=17_500, first=7, every=7)
    run_days(s, 5)
    m = O.pay(s, "h1", "outside:lettings", 17_500, "rent early", "op:p1:pay:d5.0")
    assert [x.cents for x in m] == [17_500]
    run_days(s, 4)                                   # through day 8
    assert received(s, "outside:lettings") == 17_500  # once
    assert "counted toward rent due Monday 9 March" in s.ledger.transfers[-1].memo or any("counted toward rent due Monday 9 March" in t.memo for t in s.ledger.transfers)

def test_part_paid_by_hand_leaves_the_rest_to_the_schedule():
    s = town(h1=50_000); make_rent(s, cents=17_500, first=7, every=7); run_days(s, 5)
    O.pay(s, "h1", "outside:lettings", 5_000, "toward rent", "op:p1:pay:d5.0"); run_days(s, 4)
    assert received(s, "outside:lettings") == 17_500 and [t.cents for t in to(s, "outside:lettings")] == [5_000, 12_500]

def test_a_surplus_is_an_ordinary_payment_and_never_a_hidden_credit():
    s = town(h1=90_000); make_rent(s, cents=17_500, first=7, every=7); run_days(s, 5)
    m = O.pay(s, "h1", "outside:lettings", 40_000, "a lot", "op:p1:pay:d5.0")
    assert sum(x.cents for x in m) == 17_500
    run_days(s, 11)                                  # through day 15: the day-14 instalment is still taken in full
    assert received(s, "outside:lettings") == 40_000 + 17_500

def test_arrears_are_matched_before_the_next_instalment():
    ...  # one instalment in arrears of 17,500 and the next not yet due; pay 20,000: matches are [17_500, 2_500]

def test_a_payment_to_someone_who_is_owed_nothing_matches_nothing():
    s = town(h1=50_000); assert O.pay(s, "h1", "h2", 2_000, "a gift", "op:p1:pay:d0.0") == []

def test_stop_says_what_happened():
    s = town(h1=50_000)
    sp = O.create(s, kind="standing", payer="h1", payee="h2", cents=2_000, first=2, every=7, memo="to Megan", ends="payer", made_by="p1", cause="op:p1:standing_payment:d0.0")
    fee = O.create(s, kind="fee", payer="h1", payee="outside:club", cents=500, first=1, every=7, memo="club", ends="service", made_by="p1", cause="routine:setup")
    plan = O.create(s, kind="agreement", payer="h1", payee="h2", cents=2_000, first=3, every=7, last=31, memo="repaying the $800", ends="both", made_by="p1", cause="routine:setup")
    assert O.stop(s, sp, "h1", "op:p1:stop:d0.1") == "stopped" and O.stop(s, fee, "h1", "op:p1:stop:d0.2") == "left"
    assert O.stop(s, plan, "h1", "op:p1:stop:d0.3") == "breach" and s.all("records")[-1].kind == "breach"
    with pytest.raises(ValueError): O.stop(s, sp, "h1", "op:p1:stop:d0.4")     # already stopped
    run_days(s, 10); assert to(s, "h2") == [] and to(s, "outside:club") == []

def test_statement_shows_dates_what_goes_out_and_what_is_left():
    s = town(h1=73_192); make_rent(s, cents=17_500, first=0, every=7); make_wage(s, "h1", cents=48_600, first=4)
    run_days(s, 2); text = statement_text(s, statement(s, "h1"))
    assert "$556.92" in text and "Monday 2 March" in text and "Monday 9 March" in text and "Friday 6 March" in text
    assert "$381.92" in text          # after the next rent goes out; the wage is not netted in
    assert s.hash() == hash_before_calling_statement   # building a statement changes nothing
```

- [ ] **Step 2: Run** `uv run pytest tests/town -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/town -q`. Expected: PASS.
- [ ] **Step 5: Commit** `town: payments by hand count toward what is owed; stop; statements`.

### Task 5: Operations

**Files:**
- Create: `src/townsim/town/acts.py`, `src/townsim/town/ops_core.py`
- Test: `tests/town/test_acts.py`, `tests/town/test_ops_core.py`

**Interfaces:**
- Consumes: `Store`, `obligations`, `statement`, `clock`.
- Produces in `acts.py`:
  - `Act(actor: str, principal: str, op: str, args: dict)`, `Outcome(ok: bool, msg: str, act: Act)`
  - `OpSpec(name: str, form: str, desc: str, hours: float, check: Callable[[Store, Act], str | None], do: Callable[[Store, Act, str], str])`. `check` returns the reason it cannot be done, without the `Not done:` prefix, or `None`. `do` returns what the actor is told.
  - `REGISTRY: dict[str, OpSpec]`, `register(spec: OpSpec) -> None`
  - `AUTHORISERS: list[Callable[[Store, Act], str | None]]`: each returns a reason to refuse or `None`. This plan installs one: refuse when `act.principal != act.actor`.
  - `apply_act(store: Store, act: Act, n: int) -> Outcome`
  - `to_cents(value) -> int | None`: dollars as an int or float, or a string such as `"$1,308.50"`, to cents; `None` for a bool, a non-number, zero, a negative, or more than 100,000,000 cents
  - `resolve(store, name) -> str | None`: the id of the person, household or org with that name, ignoring case and surrounding spaces; `None` if no one or if `name` is not a string
  - `account_of(store, who: str) -> str`: a person's household account, or the id itself for a household or org
  - `forms(store, actor: str) -> list[str]`: the `form` of every operation, in registration order
- Produces in `ops_core.py`, registered on import: `pay`, `standing_payment`, `stop`, `message`, `request`, `answer`, `read`, `write_notes`.

**Rules:**
- `apply_act` never raises. In order: `args` not a dict, or `op` not registered, is refused; each authoriser; hours (`Not done: you have no time left this week for that.` when `hours_left < spec.hours`); `spec.check`; then inside `store.transaction()` it calls `spec.do` with cause `op:<actor>:<op>:d<day>.<n>` and takes the hours from the actor. If `do` raises, the transaction undoes it, a line is appended to `store.engine_errors`, and the outcome is `Not done: that could not be carried out.`
- Forms shown to people, exactly:
  - `{"op": "pay", "to": "<name>", "amount": <dollars>, "note": "<what for>", "for": "<id of what it is owed on, if any>"}` (0.25 hours)
  - `{"op": "standing_payment", "to": "<name>", "amount": <dollars>, "day": "<day of the week>", "start": "<first date, e.g. Monday 13 April>", "weeks": <number, or 0 for until stopped>, "note": "<what for>"}` (0.25)
  - `{"op": "stop", "id": "<id of the payment or agreement>"}` (0.1)
  - `{"op": "message", "to": "<name>", "text": "<what you write>"}` (0.25)
  - `{"op": "request", "to": "<name>", "text": "<what you ask for>", "amount": <dollars they would pay you, or 0>}` (0.25)
  - `{"op": "answer", "request": "<id>", "answer": "accept" | "decline", "note": "<optional words>"}` (0.1)
  - `{"op": "read", "what": "account" | "account in full" | "agreements"}` (0.1)
  - `{"op": "write_notes", "text": "<for yourself>"}` (0.1)
- `pay` pays from the actor's household account through `obligations.pay`, passing `for` as `ref` when it names an obligation between the two accounts. What the actor is told includes any match, in the words `counted toward <memo> due <date>`. A name that is no one: `no one called '<name>' is known in the town.` Not enough money: `the account holds <$x> and this needs <$y>.`
- `standing_payment` creates an obligation of kind `standing`, `ends="payer"`, `every=7`, first falling on the given weekday on or after `start` (`start` missing means the next such weekday after today), `last` set from `weeks` when it is above 0. If the payer already owes the payee on an active obligation, what the actor is told ends with `This is in addition to <memo> (<id>), which already goes to them on <date>.`
- `stop` passes the actor's household account as `by`; told `Stopped.`, `You have left; the fee ends.`, or `That was an agreement with <name>. Stopping it is recorded as a breach.` An id the actor's household does not pay: `you have nothing with the id '<id>'.`
- `message` is to a person only. A name that is no one: `your letter to '<name>' came back undelivered.`
- `request` creates a `Request` expiring 14 days on. `answer` is for the addressee of an open request; accepting one with `cents > 0` pays that sum from the answerer's household to the sender's through `obligations.pay` and is refused if the money is not there. Either answer adds a `Notice` to the sender.
- `read` returns `statement_text` (`account in full` uses `back=28, max_lines=60`) or `agreements_text`.
- `write_notes` replaces the person's notes; more than 300 words is refused.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("args", [None, [], "pay", {"to": "Megan Pryce"}, {"to": "Megan Pryce", "amount": "lots"},
    {"to": "Megan Pryce", "amount": -5}, {"to": "Megan Pryce", "amount": True}, {"to": ["x"], "amount": 5},
    {"to": "Sanjay", "amount": 5}, {"to": "Megan Pryce", "amount": 10**9}])
def test_junk_is_refused_and_changes_nothing(args):
    s = thin(); before = s.hash()
    out = apply_act(s, Act("p01", "p01", "pay", args), 0)
    assert not out.ok and out.msg.startswith("Not done:") and s.hash() == before and s.engine_errors == []

def test_unknown_operation_and_acting_for_another_are_refused():
    s = thin()
    assert apply_act(s, Act("p01", "p01", "cancel_everything", {}), 0).msg.startswith("Not done:")
    assert not apply_act(s, Act("p01", "p03", "pay", {"to": "Ruth Ashby", "amount": 5}), 1).ok

def test_an_operation_that_breaks_is_undone_and_reported(monkeypatch):
    s = thin(); before = s.hash()
    def boom(store, act, cause):
        store.set("people", act.actor, cause, notes="half done"); raise RuntimeError("bug")
    register(OpSpec("boom", "{}", "", 0.0, lambda s, a: None, boom))
    out = apply_act(s, Act("p01", "p01", "boom", {}), 0)
    assert not out.ok and s.hash() == before and len(s.engine_errors) == 1

def test_hours_are_taken_and_run_out():
    s = thin(); s.set("people", "p01", "routine:test", hours_left=0.3)
    assert apply_act(s, Act("p01", "p01", "message", {"to": "Craig Pryce", "text": "hello"}), 0).ok
    assert s.get("people", "p01").hours_left == pytest.approx(0.05)
    assert "no time left" in apply_act(s, Act("p01", "p01", "message", {"to": "Craig Pryce", "text": "again"}), 1).msg

def test_to_cents():
    assert [to_cents(x) for x in (12, 12.5, "12", "$1,308.50", " 7.25 ")] == [1200, 1250, 1200, 130850, 725]
    assert [to_cents(x) for x in (0, -1, True, None, "lots", [], 10**7)] == [None] * 7
```

and in `test_ops_core.py`: one test per operation for its ordinary use and each refusal named in the rules above, including `test_a_second_payment_to_someone_already_owed_is_shown_as_a_second`, `test_stop_on_an_agreement_is_a_breach_and_says_so`, `test_accepting_a_request_for_money_moves_it_once`, `test_a_letter_to_no_one_comes_back`, `test_pay_with_for_counts_toward_that_obligation`.

- [ ] **Step 2: Run** `uv run pytest tests/town -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** `thin()` in the tests is `townsim.town.thin.thin_town()` from Task 7; until then build the same two households in a test helper and switch the import in Task 7.
- [ ] **Step 4: Run** `uv run pytest tests/town -q`. Expected: PASS.
- [ ] **Step 5: Commit** `town: atomic operations and the first eight of them`.

### Task 6: Stored replies

**Files:**
- Create: `src/townsim/town/replies.py`, `src/townsim/town/rundb.py`
- Test: `tests/town/test_replies.py`

**Interfaces:**
- Produces in `replies.py`:
  - `ReplyKey(run: str, model: str, day: int, actor: str, purpose: str, ref: str, n: int)` (frozen) with `text() -> str` giving `"<run>|<model>|d<day>|<actor>|<purpose>:<ref>|<n>"`
  - `Reply(text: str | None, tool: dict | None = None, reasoning: str | None = None, finish: str | None = None, tokens_in: int = 0, tokens_out: int = 0, cost: float = 0.0, error: str | None = None)`
  - `prompt_hash(messages: list[dict]) -> str`
  - `class Replier(Protocol)`: attribute `model: str`, method `ask(key: ReplyKey, messages: list[dict]) -> Reply`
  - `class Keys`: `Keys(run: str)`, `next(model, day, actor, purpose, ref="") -> ReplyKey`, counting from 0 for each `(day, actor, purpose, ref)`
  - `class ScriptedReplier`: `ScriptedReplier(fn: Callable[[ReplyKey, list[dict]], str | None])`, `model = "scripted"`
  - `class Recording`: `Recording(inner: Replier, db: RunDB, run: str)`; `model` is the inner's. `ask` returns the stored reply when the key is already stored with the same prompt hash, without calling `inner`; raises `ReplayMismatch` when it is stored with a different hash; otherwise asks `inner`, stores the reply and returns it.
  - `class Replaying`: `Replaying(db: RunDB, run: str, model: str)`; `ask` returns the stored reply or raises `ReplayMismatch`; `unused() -> list[str]`
  - `class ReplayMismatch(Exception)` with attributes `key: str` and `why: str` (`"missing"`, `"prompt changed"`, `"unused"`, `"state differs"`)
  - `class StopRun(Exception)`: a replier raises it when a run must stop (a spending cap, a refused prompt)
- Produces in `rundb.py`: `class RunDB` over one SQLite file:
  - `RunDB(path: str)`, `create_run(run_id, seed: int, parent: str | None = None, parent_day: int | None = None) -> None`, `get_run(run_id) -> dict`
  - `put_reply(run_id, key: str, prompt_hash: str, reply: Reply) -> None`, `get_reply(run_id, key: str) -> tuple[str, Reply] | None`, `reply_keys(run_id) -> list[str]`
  - `save_snapshot(run_id, day: int, store: Store) -> None` (the store as it stands that morning; replaces an existing one for that day), `load_snapshot(run_id, day) -> Store`, `snapshot_hash(run_id, day) -> str | None`, `snapshot_days(run_id) -> list[int]`

- [ ] **Step 1: Write the failing tests**

```python
def test_keys_count_per_day_actor_purpose_and_ref():
    k = Keys("r1")
    a = k.next("scripted", 3, "p01", "turn"); b = k.next("scripted", 3, "p01", "turn"); c = k.next("scripted", 3, "p01", "conv", "c7")
    assert (a.n, b.n, c.n) == (0, 1, 0) and a.text() == "r1|scripted|d3|p01|turn:|0"

def test_recording_stores_once_and_serves_a_resume_without_asking(tmp_path):
    db = RunDB(str(tmp_path / "x.sqlite")); db.create_run("r1", seed=1); calls = []
    inner = ScriptedReplier(lambda key, msgs: calls.append(key) or "hello")
    rec = Recording(inner, db, "r1"); key = Keys("r1").next("scripted", 0, "p01", "turn"); msgs = [{"role": "user", "content": "view"}]
    assert rec.ask(key, msgs).text == "hello" and rec.ask(key, msgs).text == "hello" and len(calls) == 1
    with pytest.raises(ReplayMismatch) as e: rec.ask(key, [{"role": "user", "content": "a different view"}])
    assert e.value.why == "prompt changed" and "d0|p01" in e.value.key

def test_replaying_fails_on_a_missing_key_and_reports_unused_ones(tmp_path): ...
def test_a_reply_is_stored_whole(tmp_path): ...     # text, tool, reasoning, finish, tokens, cost, error all round-trip, including text None with an error
def test_snapshots_round_trip_and_keep_their_hash(tmp_path): ...   # load_snapshot(...).hash() == snapshot_hash(...) == the original's
```

- [ ] **Step 2: Run** `uv run pytest tests/town/test_replies.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Snapshots are stored as `pickle` bytes beside the hash from `Store.hash()`. Replies are stored as JSON.
- [ ] **Step 4: Run** `uv run pytest tests/town -q`. Expected: PASS.
- [ ] **Step 5: Commit** `town: replies stored under keys that never depend on the prompt`.

### Task 7: A day, a thin town, and exact replay

**Files:**
- Create: `src/townsim/town/views.py`, `src/townsim/town/loop.py`, `src/townsim/town/runs.py`, `src/townsim/town/thin.py`
- Modify: `tests/town/test_acts.py`, `tests/town/test_ops_core.py` (use `thin_town`)
- Test: `tests/town/test_loop.py`, `tests/town/test_runs.py`, `tests/town/test_views.py`

**Interfaces:**
- Consumes: everything above.
- Produces in `views.py`: `person_view(store, pid: str) -> list[dict]` (a system message and a user message; pure) and `mark_seen(store, pid: str, cause: str) -> None`.
- Produces in `loop.py`:
  - `MAX_ACTS = 8`
  - `routine(store) -> None`: on a Monday, everyone's `hours_left` back to `hours_week`; open requests past `expires` lapse, with a `Notice` to the sender; then `open_dues`, `age_arrears`, `collect`
  - `who_acts(store) -> list[str]`: people with an unseen message or notice, an open request addressed to them, or whose `stock_day` is today's weekday, in `id_key` order
  - `parse_acts(text: str | None) -> list[dict] | None`: the `acts` list of the first JSON object in the text that has one; `None` when there is none
  - `Turn(pid, key: str, invalid: bool, outcomes: list[Outcome])`, `DayReport(day, turns: list[Turn])`
  - `run_day(store, replier: Replier, keys: Keys) -> DayReport`: `routine`; then every view is built from the store as it stands, every reply is fetched, and only then are acts applied, person by person in `id_key` order, at most `MAX_ACTS` each, numbered across the whole day; `mark_seen` for each person who acted; `advance_day`
- Produces in `runs.py`:
  - `run(db: RunDB, run_id: str, store: Store, replier: Replier, days: int) -> Store`: for each day, save the morning snapshot, then `run_day` with the replier wrapped in `Recording`. After the last day it saves a snapshot of the final state. If `StopRun` is raised, it returns the morning snapshot of the day it was raised in, loaded fresh from the database.
  - `resume(db, run_id) -> Store`: the latest snapshot
  - `replay(db, run_id) -> None`: starts from the earliest snapshot, runs each recorded day with `Replaying`, and raises `ReplayMismatch` if a morning hash differs (`why="state differs"`, the key naming the day) or, at the end, if any stored reply was not used
  - `fork(db, parent: str, at_day: int, new_id: str, seed: int | None = None) -> Store`: creates the run, copies the parent's snapshot for `at_day` as the child's first, returns that store
- Produces in `thin.py`: `thin_town() -> Store` and `thin_script(key: ReplyKey, messages: list[dict]) -> str | None`.

**The view** (design 2.8, 4.2). System message: `You are <name>, of the <household name> household in <place>.` and the reply format `Reply with JSON only, in this form:\n{"thoughts": "<what goes through your mind; no one else sees this>", "acts": [<operations from the list, in order>]}`. User message, sections in this order, a heading omitted when its section is empty: `TODAY: <date>.`; `YOUR HOUSEHOLD'S ACCOUNT` then `statement_text`; `NEW` with unseen messages (`From <name>: <text>`) and notices, oldest first; `WAITING FOR YOUR ANSWER` with open requests addressed to them (`<id> from <name>: <text>` and, when money is asked, ` They ask you for <$x>.`); `YOUR NOTES`; `You have <h> hours left this week.`; `OPERATIONS` then one form per line. At most 1,200 words: when over, the oldest statement lines are dropped first.

**The thin town.** Place `Wenlow`. Outside accounts that allow credit: `outside:employers`. Other outside accounts: `outside:lettings`, `outside:northgrid`, `outside:shops`. Household `h1` Ashby (`p01` Ruth Ashby, stock day 4; `p02` Tom Ashby, stock day 3) opening with $600. Household `h2` Pryce (`p03` Craig Pryce, stock day 4; `p04` Megan Pryce, stock day 5) opening with $300. Everyone has 12 hours a week. Obligations, all weekly: wages on Friday (day 4) of $520 to `h1` and $330 to `h2`; rent on Monday of $210 from `h1` and $185 from `h2`, kind `rent`, `ends="both"`; energy on Wednesday of $48 from each, kind `bill`, `ends="service"`; `food and regular spending` on Saturday of $230 from each to `outside:shops`, kind `bill`, `ends="service"`. Opening balances are transfers from `outside:employers`. The Pryces spend $133 a week more than comes in, so they fall behind within a month; that is what exercises arrears.

**The thin script** must, over eight weeks, do at least these, deterministically from the key alone: Ruth sets up a standing payment of $20 a week to Megan Pryce and stops it three weeks later; Craig pays his rent by hand on the first Friday, three days before it is due, once; Craig asks Ruth for $50 and she accepts; Tom writes to someone who does not exist; one reply is not JSON; one reply holds an act with junk arguments; one reply holds twelve acts.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_view_changes_nothing_and_says_nothing_about_what_to_do():
    s = thin_town(); before = s.hash(); msgs = person_view(s, "p03")
    assert s.hash() == before and msgs[0]["role"] == "system" and "Craig Pryce" in msgs[0]["content"]
    text = msgs[1]["content"]
    assert text.startswith("TODAY: Monday 2 March.") and '{"op": "pay"' in text
    for word in ("you may", "you should", "you can ", "consider", "simulat", "test", "model", "game"):
        assert word not in text.lower() and word not in msgs[0]["content"].lower()

def test_views_stay_within_1200_words_after_eight_weeks():
    s = eight_weeks(); assert all(len(person_view(s, p.id)[1]["content"].split()) <= 1200 for p in s.all("people"))

def test_everyone_sees_the_morning_and_acts_apply_in_id_order():
    ...  # p01 and p03 both act on the first Friday; p01 pays p03's household; p03's view, captured by the scripted replier, shows the balance before that payment

def test_eight_weeks_conserve_money_and_collect_nothing_twice():
    s = eight_weeks()
    assert s.ledger.total() == 0 and s.engine_errors == [] and s.day == 56
    collected = Counter(t.cause for t in s.ledger.transfers if t.cause.startswith("routine:collect:"))
    assert max(collected.values()) == 1
    assert all(d.paid <= d.cents for d in s.all("dues"))
    # Craig paid Monday 9 March's rent by hand on Friday 6 March: from day 4 to day 10 exactly $185 reached outside:lettings from h2
    assert sum(t.cents for t in s.ledger.transfers if t.src == "h2" and t.dst == "outside:lettings" and 4 <= t.t <= 10) == 18_500
    assert any(o.penalty for o in s.all("obligations"))      # the Pryces fall behind, as the figures make them

def test_a_run_replays_exactly(tmp_path):
    db = RunDB(str(tmp_path / "t.sqlite")); db.create_run("r1", seed=1)
    final = run(db, "r1", thin_town(), ScriptedReplier(thin_script), 56)
    replay(db, "r1")                                        # no error
    assert db.snapshot_hash("r1", 56) == final.hash() and db.snapshot_days("r1") == list(range(57))

def test_replay_notices_a_changed_view_a_changed_state_and_an_unused_reply(tmp_path): ...
    # each of: altering one stored prompt hash, altering one snapshot's hash, adding one stored reply nobody asks for
    # raises ReplayMismatch whose str() names the day and, for the first, the actor

def test_a_run_stopped_mid_day_resumes_to_the_same_end(tmp_path):
    straight = run(db_a, "a", thin_town(), ScriptedReplier(thin_script), 21).hash()
    stopper = ScriptedReplier(stop_on(day=9, actor="p03"))              # raises StopRun for that one ask, the first time only
    back = run(db_b, "b", thin_town(), stopper, 21)
    assert back.day == 9 and back.hash() == db_b.snapshot_hash("b", 9)
    assert run(db_b, "b", resume(db_b, "b"), stopper, 21 - 9).hash() == straight

def test_a_fork_shares_the_past_and_has_its_own_future(tmp_path): ...
    # fork at day 14; the child's day-14 hash equals the parent's; run the child 7 days with a different script; parent still replays; child replays

def test_two_processes_reach_the_same_hash():
    ...  # run `python -c "..."` twice with PYTHONHASHSEED set to different values; the printed final hashes are equal
```

- [ ] **Step 2: Run** `uv run pytest tests/town -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, in under two minutes.
- [ ] **Step 5: Commit** `town: a day in a fixed order, a four-person town, exact replay, resume and fork`.

### Task 8: Random play, and removing the old engine

**Files:**
- Create: `tests/town/test_random_play.py`
- Modify: `src/townsim/cli.py`, `src/townsim/model.py`, `src/townsim/estimate.py`, `tests/test_model.py`, `tests/test_estimate.py`, `tests/test_cli.py`, `README.md`, `spikes/*/llm.py` only if an import they use has moved
- Delete: `src/townsim/{actions,adoption,assistant,bribery,compare,config,decide,events,ledger,market,measures,press,runs,sim,story,tax,typesafe,world}.py`; `tests/{conftest,helpers}.py` if nothing kept uses them; `tests/test_{actions,adoption,assistant,bribery,compare,config,crime_path,decide,events,ledger,market,measures,owner_decisions,press,runs,sim,stability,tax,wages}.py`; every file in `config/` except `prices.yaml`

**Interfaces:**
- Produces: `townsim.model` holding only the client helpers `reply_text`, `reasoning_text`, `finish_reason`, `token_count`, `ModelUnavailable`, and a function `complete(completion_fn, *, model, messages, timeout=60.0, sleep_fn=time.sleep, **params)` that retries twice as the old `_complete` did. `townsim.estimate` holding only `Price`, `load_prices`, `cost`, `count_tokens`.
- Produces: CLI commands `townsim skill` (unchanged), `townsim town-thin --weeks 8 --db PATH --run-id ID` (prints one line a week with the day, the hash and the ledger total, then `replay ok`), `townsim town-replay --db PATH RUN_ID`.

- [ ] **Step 1: Write the random-play test**

```python
@pytest.mark.parametrize("seed", range(40))
def test_random_acts_never_break_the_books(seed):
    rng = random.Random(seed); s = thin_town(); replier = ScriptedReplier(random_script(rng))
    keys = Keys("r")
    for _ in range(42):
        run_day(s, replier, keys)
        assert s.ledger.total() == 0 and s.engine_errors == []
        assert all(0 <= d.paid <= d.cents for d in s.all("dues"))
    collected = Counter(t.cause for t in s.ledger.transfers if t.cause.startswith("routine:collect:"))
    assert not collected or max(collected.values()) == 1
    for ob in s.all("obligations"):          # nobody is ever paid more on a schedule than the schedule says
        assert sum(d.paid for d in s.all("dues") if d.obligation == ob.id) <= ob.cents * len([d for d in s.all("dues") if d.obligation == ob.id])
```

`random_script` returns, for each ask, a JSON reply with zero to ten acts drawn from every registered operation with arguments that are sometimes valid (real names, real ids read from the view text, sensible amounts) and sometimes junk (wrong types, unknown names, huge or negative amounts, missing keys), and now and then text that is not JSON.

- [ ] **Step 2: Run** `uv run pytest tests/town/test_random_play.py -q`. Expected: PASS. If it fails, the fault is in Tasks 2 to 7: fix it there, with a test that pins the case.
- [ ] **Step 3: Trim `model.py` and `estimate.py`** to the interfaces above and their tests to match. Delete the files listed. Rewrite `cli.py` with the three commands. Confirm `grep -rn "townsim\.\(world\|market\|sim\|events\|runs\|decide\|assistant\)" src tests spikes` finds nothing, apart from spike imports of `townsim.model`, which must still resolve.
- [ ] **Step 4: Rewrite the README's Status, Quick start, How it fits together, Using language models and Estimating cost sections** to describe what now exists: the design in `docs/design.md`; the trial write-ups; `townsim skill`; `townsim town-thin`; the modules of `townsim.town` in a table of one line each; that no model plays anyone in the engine yet. Plain words, short sentences, no claims about what is not built. Keep the first paragraph's description of the project accurate to the design (people played by weak models who think for themselves; a far more capable AI system that people adopt).
- [ ] **Step 5: Run** `uv run pytest -q` and `uv run townsim town-thin --weeks 8 --db /tmp/thin.sqlite --run-id t1`. Expected: all tests pass; the command ends with `replay ok`.
- [ ] **Step 6: Commit** `Remove the menu-driven engine; the town engine core stands alone`.
