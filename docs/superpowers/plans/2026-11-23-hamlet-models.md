# People Played by Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put cheap language models in the seats of the hamlet's eighteen people, behind a spending cap that cannot be passed, and produce a readable account of what they did.

**Architecture:** A `ModelReplier` implements the existing `Replier` protocol over `townsim.model.complete`, routing each person to the model of the family they were dealt when the place was generated. The day's ordinary turns are asked as one batch (several calls at once); talks stay one call at a time. Every reply is stored through the existing `Recording`, so a live run can be stopped, resumed and replayed exactly as a scripted one can. Three commands sit on top: a probe of the model families, a live run, and a reader that turns a kept run into an account.

**Tech Stack:** Python 3.12, uv, pytest, LiteLLM (already a dependency) reaching models through OpenRouter, SQLite through the existing `RunDB`.

**Spec:** `docs/design.md` (sections 2, 3.3, 3.4, 4.5, 5, 9 step 2a, 10) and `docs/trials-step1-round2.md` (what the trials found about model families and the provider's refusal).

## Global Constraints

- No test calls a model or the network. `ModelReplier` takes its completion function as an argument; tests pass a fake. The suite stays under ninety seconds.
- Only models listed in the families file may be called, and only if `config/prices.yaml` prices them at no more than $0.20 per million tokens in and $1.00 per million out. A family that fails this is a `ValueError` when the file is loaded, naming the family and the price. There is no flag that lifts this.
- Every live command takes `--cap <dollars>`; there is no default, and a cap above 5 is refused. Before any call or batch, spend already stored for the run plus the worst case of what is about to be sent (characters ÷ 3 as tokens in, the family's `max_tokens` out, at the listed prices) must not pass the cap; if it would, nothing is sent and the run stops.
- Spend is what is stored: a run's spend is the sum of the `cost` of its stored replies, so a resumed run counts what it spent before. A reply that was paid for is always stored, including when the same batch stops the run.
- The messages a person is sent are exactly `views.view` / the talk view. The replier adds, removes and rewrites nothing, and sends no extra system text, tools or response-format parameter.
- A reply the provider refused (finish reason `content_filter` with no text, or a non-empty `refusal` on the message) is stored with `error` beginning `refused:` and stops the run at once. A model that gives no answer after its retries stops the run and stores nothing for that ask.
- The key is read by LiteLLM from the environment (`OPENROUTER_API_KEY`); nothing in the repository reads, prints, logs or stores it. Commands are run as `uv run --env-file .env townsim …`. `.env` is never opened by code or by an agent.
- A live run replays exactly: after it ends or stops, `runs.replay` passes on its completed days.
- Nothing printed or written by these commands contains a real name of the repository's owner, an email address, or a path outside the repository.
- Commit messages end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`; commits are authored as `conorg000 <20813490+conorg000@users.noreply.github.com>`.

## Review Focus

- A provider returns text that is not JSON, JSON inside a code fence, two JSON objects, or prose then JSON: the person's turn is counted unreadable or read by the existing `parse_reply` rules; nothing raises. (Task 2)
- A provider returns a reply with no `usage`, or `usage` with missing or non-numeric fields, or a cost function that raises: cost falls back to listed prices × counted tokens (characters ÷ 3 when no count came back) and is never recorded as 0 for a paid call. (Task 2)
- One call in a batch of eighteen hangs, raises, or is refused: the other seventeen replies are stored, the run stops, and a resume asks only for what is missing. (Tasks 2, 3)
- The process is killed mid-batch: on resume no stored reply is asked for again and the day's state matches a straight run with the same replies. (Task 3)
- A run is resumed with a families file that now maps a person to a different model: refused as `ReplayMismatch("model changed")` before any call is paid for. (Task 5)

---

### Task 1: Families with their models, and the price guard

**Files:**
- Modify: `src/townsim/town/families.py`
- Test: `tests/town/test_families.py`

**Interfaces:**
- Consumes: `townsim.estimate.load_prices(path) -> dict[str, Price]`.
- Produces:
  - `@dataclass(frozen=True) class Family: name: str; model: str; params: dict` (`model` is the OpenRouter id as the file gives it, e.g. `tencent/hy4-flash`; `params` are passed to the call as given).
  - `families.load(path: str, prices_path: str) -> dict[str, Family]` — the families whose `left_out` is false, in file order. Raises `ValueError` (one plain line) for: a file that is not YAML or not a mapping; a family without a `model` string; `params` that is not a mapping or lacks an integer `max_tokens` between 1 and 4000; a model with no entry in the prices file; a price above `MAX_IN = 0.20` or `MAX_OUT = 1.00`; fewer than two families left in.
  - `families.names(path)` stays as it is.
  - `families.worst_case(family: Family, price: Price, messages: list[dict]) -> float` — dollars: `ceil(total characters of message contents / 3)` tokens in plus `params["max_tokens"]` tokens out, at the price.

- [ ] **Step 1: Write the failing tests** — `test_load_returns_families_in_order`, `test_left_out_is_skipped`, `test_model_without_price_is_refused`, `test_price_over_ceiling_is_refused` (a prices file listing a model at `input_per_mtok: 2.0` → `ValueError` whose text names the family and `2.0`), `test_fewer_than_two_families_is_refused`, `test_bad_max_tokens_is_refused` (missing, 0, 5000, `"1200"`), `test_worst_case` (messages totalling 3,000 characters, `max_tokens` 1200, price 0.10/0.50 → `(1000*0.10 + 1200*0.50)/1e6`).
- [ ] **Step 2: Run them, see them fail** — `uv run pytest tests/town/test_families.py -q`.
- [ ] **Step 3: Implement `Family`, `load`, `worst_case`.**
- [ ] **Step 4: Check the shipped files load** — a test that `families.load("config/families.yaml", "config/prices.yaml")` returns the four families; if a listed model has no price entry under exactly that id, correct `config/families.yaml` or `config/prices.yaml` so ids agree (the prices file is the snapshot of record; do not invent a price). Each family's `params` also carries the `reasoning_effort` the step 1 trials used (`"none"` for haiku, deepseek and hunyuan; `"low"` for minimax, which answers poorly without it), so no family spends hidden reasoning tokens by default; `load` accepts `reasoning_effort` only as one of `none`, `low`.
- [ ] **Step 5: Run the file's tests, then commit.**

### Task 2: The model replier

**Files:**
- Create: `src/townsim/town/modelreplier.py`
- Test: `tests/town/test_modelreplier.py`

**Interfaces:**
- Consumes: `Family`, `worst_case`; `townsim.model.complete`, `reply_text`, `reasoning_text`, `finish_reason`, `token_count`, `ModelUnavailable`; `replies.Reply`, `ReplyKey`, `StopRun`; `estimate.Price`.
- Produces:
  - `class ModelReplier` with
    `__init__(self, families: dict[str, Family], prices: dict[str, Price], family_of: dict[str, str], completion_fn: Callable, cap: float, spent: Callable[[], float], cost_fn: Callable | None = None, workers: int = 8, sleep_fn=time.sleep)`
    — `family_of` maps a person's id to a family name; `spent()` returns dollars already stored for the run; `cost_fn(response) -> float` is LiteLLM's `completion_cost(completion_response=…)` in live use.
  - `model_for(actor: str) -> str` — the family's model id; `KeyError` for an actor with no family.
  - `ask(key: ReplyKey, messages: list[dict]) -> Reply` — raises `StopRun` when the cap would be passed (nothing sent) or the model gave no answer; returns a `Reply` otherwise, including a refused one.
  - `ask_many(pairs: list[tuple[ReplyKey, list[dict]]]) -> list[Reply | StopRun]` — one element per pair, in order; never raises for a single call's failure. The cap is checked for the whole batch before anything is sent (stored spend + the sum of worst cases); if it would pass, every element is a `StopRun` and nothing is sent. Calls run on up to `workers` threads.
  - `stopped: str | None` — why the replier last stopped a run: `"cap: …"`, `"refused: <model>: …"`, `"unavailable: <model>: …"`.
  - After a refused reply has been returned, every later `ask` raises `StopRun` and every later `ask_many` returns only `StopRun`s.
- The call: `complete(completion_fn, model="openrouter/" + family.model, messages=messages, **family.params)`.
- The reply: `text=reply_text(resp)`, `reasoning=reasoning_text(resp)`, `finish=finish_reason(resp)`, `tokens_in`/`tokens_out` from `resp.usage` (`prompt_tokens`, `completion_tokens`), `cost` from `cost_fn` when it returns a positive number, else listed prices × the token counts, with characters ÷ 3 standing in for a missing count; `error` set only for a refusal.

- [ ] **Step 1: Write the failing tests** with a fake completion function returning simple objects of the OpenAI shape:
  - `test_routes_each_person_to_their_family` (the fake records `model`; two people in different families).
  - `test_passes_messages_and_params_unchanged`.
  - `test_reply_fields` (text, reasoning, finish, tokens, cost from `cost_fn`).
  - `test_cost_falls_back_to_prices` (`cost_fn` raises; `cost_fn` returns 0; no `usage` at all → cost > 0).
  - `test_cap_stops_before_sending` (`spent` returns cap − a hair; the fake must not be called; `stopped` starts `cap:`).
  - `test_unavailable_stops_and_returns_nothing` (fake always raises; `sleep_fn` a no-op; `StopRun`; `stopped` starts `unavailable:`).
  - `test_refusal_is_returned_then_everything_stops` (finish `content_filter`, no text → `Reply.error` starts `refused:`; the next `ask` raises).
  - `test_refusal_field_on_message_counts`.
  - `test_ask_many_keeps_order_and_isolates_failures` (eighteen pairs; the fake raises for one actor, refuses another; sixteen replies, one refused reply, one `StopRun`, in the pairs' order).
  - `test_ask_many_checks_the_cap_for_the_whole_batch`.
  - `test_unreadable_text_is_just_text` (prose, fenced JSON, two objects: returned as text; nothing raises).
- [ ] **Step 2: Run them, see them fail.**
- [ ] **Step 3: Implement `ModelReplier`.** Threads: `concurrent.futures.ThreadPoolExecutor`; collect results by index.
- [ ] **Step 4: Run the file's tests, then commit.**

### Task 3: A day's turns asked as one batch

**Files:**
- Modify: `src/townsim/town/replies.py`, `src/townsim/town/loop.py`
- Test: `tests/town/test_replies.py`, `tests/town/test_runs.py`

**Interfaces:**
- Produces:
  - `replies.ask_all(replier, pairs: list[tuple[ReplyKey, list[dict]]]) -> list[Reply]` — uses `replier.ask_many` when the replier has one, else asks one at a time; raises `StopRun` if any element is one, after everything else in the batch has been dealt with.
  - `Recording.ask_many(pairs) -> list[Reply | StopRun]` — for each pair, the stored reply if there is one (with the same `model changed` / `prompt changed` checks as `ask`, raised before anything is asked); the rest are asked of `inner` in one batch (`inner.ask_many` if it has one, else one at a time, a `StopRun` raised by `inner.ask` becoming that element and ending the asking); every `Reply` that came back is stored, in the pairs' order, before returning.
  - `Replaying.ask_many(pairs)` — each through `ask`.
  - `loop.run_day` asks the day's ordinary turns through `ask_all`. Talks are unchanged.
- The stored order of replies within a day stays the order of the pairs, so `Replaying.unused()` and existing stored runs' semantics are unchanged; a scripted run's hashes do not change.

- [ ] **Step 1: Write the failing tests:**
  - `test_ask_all_falls_back_to_one_at_a_time` (a `ScriptedReplier`).
  - `test_recording_ask_many_stores_what_came_back_before_stopping` (inner returns `[Reply, StopRun, Reply]` → two rows stored; `ask_all` raises `StopRun`).
  - `test_recording_ask_many_serves_stored_and_asks_only_the_rest` (the inner sees only the missing pairs).
  - `test_recording_ask_many_model_changed_raises_before_asking`.
  - `test_run_stopped_mid_batch_resumes_to_the_same_state` (a replier whose `ask_many` stops on the third person of day 9; resume with a replier that does not stop; hashes equal a straight run's for every morning; each day's changes, turns and errors stored once).
  - `test_scripted_hashes_unchanged` (seed 1, eight scripted weeks: the final hash equals the value at the commit before this task — read it from a run made before changing the loop and pin it in the test).
- [ ] **Step 2: Run them, see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `tests/town -q`, then commit.**

### Task 4: What a run spent and who played it

**Files:**
- Modify: `src/townsim/town/rundb.py`
- Test: `tests/town/test_rundb.py`

**Interfaces:**
- Produces:
  - `RunDB.spent(run_id) -> float` — the sum of `cost` over the run's stored replies (0.0 for none).
  - `RunDB.replies(run_id) -> list[tuple[str, Reply]]` — every stored reply with its full key text, in stored order.
  - `RunDB.put_note(run_id, name, value: str)` / `RunDB.note(run_id, name) -> str | None` — small named texts kept with a run (table `notes(run, name, value, primary key (run, name))`; `put_note` replaces).
- `SCHEMA` does not change (no snapshot format changes); a file made before this task opens and gains the table.

- [ ] **Step 1: Failing tests** — `test_spent_sums_stored_costs`, `test_replies_in_stored_order`, `test_notes_round_trip_and_replace`, `test_old_file_gains_notes_table`.
- [ ] **Step 2–4: Fail, implement, pass, commit.**

### Task 5: `townsim town-live`

**Files:**
- Modify: `src/townsim/cli.py`
- Create: `src/townsim/town/live.py`
- Test: `tests/town/test_live.py`

**Interfaces:**
- Consumes: Tasks 1–4; `gen.generate`; `runs.run`, `runs.resume`, `runs.replay`.
- Produces:
  - `live.play(db: RunDB, run_id: str, seed: int, weeks: int, families: dict[str, Family], prices, cap: float, completion_fn, cost_fn=None, echo=print) -> LiveResult` where `LiveResult(days_done: int, spent: float, stopped: str | None, engine_errors: int)`. The place is `gen.generate(seed, list(families), weeks=max(weeks, 21))`, so its dated events cover the run. Creates the run (noting `families` as JSON of name → model, the seed and the cap with `put_note`) or resumes it; a resume whose noted seed differs is a `ValueError`, and one whose noted families map differs is a `ValueError` naming the family (before any call). Runs week by week up to `weeks` weeks from day 0 in total (a resumed run continues to the same total), echoing one line a week: week number, date of the Monday, state hash (12 characters), asks made, replies that could not be read, spend so far. Stops when the replier stops; echoes why.
  - Command `town-live` with `--db`, `--run-id`, `--seed` (default 1), `--weeks`, `--cap` (required; > 0 and ≤ 5), `--families`, `--prices`. It builds the live completion function (`litellm.completion` with `litellm.drop_params = True`, `litellm.suppress_debug_info = True`) and `cost_fn`, calls `live.play`, prints spend by family, engine errors, then replays the run and prints `replay ok` or the mismatch. Exit code 1 on engine errors or a replay mismatch; 0 when stopped by the cap (it says so).
- If `OPENROUTER_API_KEY` is not in the environment the command fails in one line before making the run: `no OPENROUTER_API_KEY in the environment; run with: uv run --env-file .env townsim town-live …`. It never prints the key or any part of it.

- [ ] **Step 1: Failing tests** (all with a fake completion function that answers `{"thoughts": "", "acts": []}` and a talk form reply):
  - `test_play_two_weeks_and_replay` (14 days done; notes stored; `runs.replay` returns `[]`).
  - `test_play_stops_at_the_cap_and_resumes` (cap small enough to stop in week 1; second call with a larger cap finishes; total stored spend equals the sum; no reply asked twice — the fake counts calls by slot).
  - `test_resume_with_other_seed_or_families_is_refused`.
  - `test_refusal_stops_the_run_and_is_stored`.
  - `test_cli_requires_cap_and_key` (typer's test runner: no `--cap` → usage error; `--cap 9` → refused; no key in env → the one-line failure, no run created).
- [ ] **Step 2–4: Fail, implement, pass, commit.**

### Task 6: `townsim town-probe`

**Files:**
- Create: `src/townsim/town/probe.py`
- Modify: `src/townsim/cli.py`
- Test: `tests/town/test_probe.py`

**Interfaces:**
- Produces:
  - `probe.cases(seed: int, family_names: list[str]) -> list[Case]` where `Case(label: str, pid: str, kind: "turn" | "talk", messages: list[dict], store: Store)`: from the seed's hamlet run ten scripted days, then four cases chosen by code — a shop owner on their stock day; a person with something in arrears or due within three days that their balance does not cover (else the person with the lowest balance); a person with an open request to answer; and one talk view (the first talk the script holds after day 3, at its second turn). Each case carries a copy of the store as it was when the view was built.
  - `probe.run(cases, families, prices, completion_fn, cap, n=3, cost_fn=None) -> list[Row]`, one `Row` per family × case × repeat: `family, label, got_text, readable, acts, acts_ok, refusals: list[str]` (the refusal messages of acts the engine refused when applied, in order, to a fresh copy of the case's store), `provider_refused, tokens_in, tokens_out, cost, seconds`. Stops (raising `StopRun`) at the cap or on a provider refusal, returning nothing further; rows so far are written by the command.
  - `probe.table(rows) -> str` — per family: calls, readable %, acts per reply, acts carried out %, the three commonest refusal messages, provider refusals, mean tokens in/out, dollars, mean seconds.
  - Command `town-probe` with `--families`, `--prices`, `--cap` (required, ≤ 1), `--seed` (default 1), `--n` (default 3), `--out` (default `runs/probe.json`; the directory is git-ignored). Writes the rows and every reply's text and reasoning to `--out`; prints the table.

- [ ] **Step 1: Failing tests** — `test_cases_are_four_and_stable` (same seed twice → equal messages), `test_run_scores_a_good_reply`, `test_run_scores_unreadable_and_refused_acts` (a fake that returns prose for one family and an act with a bad date for another; the row's `refusals` holds the engine's message), `test_applying_acts_does_not_touch_the_case_store`, `test_run_stops_on_provider_refusal`, `test_table_lists_every_family`.
- [ ] **Step 2–4: Fail, implement, pass, commit.**

### Task 7: `townsim town-story` — reading a run

**Files:**
- Create: `src/townsim/town/story.py`
- Modify: `src/townsim/cli.py`
- Test: `tests/town/test_story.py`

**Interfaces:**
- Consumes: `RunDB.turns`, `changes`, `replies`, `errors`, `note`, `load_snapshot`; `loop.parse_reply`; the talk parser.
- Produces:
  - `story.account(db: RunDB, run_id: str) -> str` (Markdown) with, in order:
    1. **The run**: seed, days, families and how many people each plays, spend by family, engine errors, why it stopped.
    2. **Week by week**: for each week a heading with its dates; under it each person who did anything, by name with their family in brackets: what they wrote to themselves (the reply's `thoughts`, cut to 60 words), each act as a plain line with its result (`✓` carried out / `✗` and the engine's reason), letters and requests quoted in full on `>` lines, and each talk as its lines with who spoke.
    3. **Counts by family**: asks; replies that could not be read (%); acts; acts refused (%); the five commonest refusal messages with counts; mean words of thoughts.
    4. **Things to look at**, each a list of dated lines taken from the change log, not from a model: every `stop`; every hand payment that was counted toward nothing; every standing payment set up toward a payee who already has something scheduled from the same payer; every time the unknown-name flag fired, with the name; every request accepted that moved money; every change an owner made to a price, a wage, an order or staff; every job offered, accepted, quit, dismissed; every loan asked and the banker's answer; every rent change and notice; every aim added or dropped.
    5. **Balances**: each household's and business's balance on the first and last day.
  - Command `town-story <run-id> --db … --out runs/<run-id>-story.md` (prints the path, relative to the working directory).
- No model is called; everything comes from what is stored.

- [ ] **Step 1: Failing tests** on a scripted eight-week run of seed 1 kept in a temporary `RunDB`: `test_account_has_the_five_parts`, `test_every_carried_out_act_appears_once` (count of `✓` lines equals the number of outcomes with `ok` in the stored turns), `test_refused_acts_carry_the_engines_reason`, `test_letters_are_quoted_never_inline` (a scripted letter containing `# Heading` and `| a | b |` appears only on `>` lines), `test_counts_by_family_add_up`, `test_things_to_look_at_lists_every_stop`.
- [ ] **Step 2–4: Fail, implement, pass, commit.**

### Task 8: What reached a household and a workplace this week

Design 3.4 ("Hearing things"): code decides who learns of an event, from channels, never a model.

**Files:**
- Create: `src/townsim/town/heard.py`
- Modify: `src/townsim/town/loop.py` (the routine, Mondays), `src/townsim/town/views.py` (nothing new to lay out if the digest is a `Notice`; confirm)
- Test: `tests/town/test_heard.py`

**Interfaces:**
- Produces: `heard.weekly(store: Store) -> None`, called by the routine on Mondays after events: for each person, one `Notice` (cause `routine:heard:<pid>:w<week>`) headed `Heard last week:` with at most eight lines, built only from the change log of the seven days before, each line a plain record with its day. A person with no lines gets no notice.
- Channels and lines (each event is told once to each person it reaches; a person never hears of their own act, or what their own results already told them):
  - **Home** (the other adults of the household): a member started or left a job (`<Name> started at <org>` / `<Name> no longer works at <org>`); a member was ill; notice to quit served on the house (if not already told).
  - **Work** (the owner and everyone holding a job at the organisation): someone joined, left or was dismissed; the shop's price changed (`The price of a basket went from $x to $y`); the days last week the shop sold out or turned people away (`Sold out on Tuesday and Friday` / `Turned people away on 3 days`), read from what the shop's own records hold.
  - **The street** (every household that bought at a shop last week): that shop's price change; that it had nothing to sell on a day they came.
  - **The register** (everyone, as a public record the know-how already mentions): a notice to quit served; a default recorded; a business opened or closed.
- Nothing is told that the channel's members could not have seen for themselves: no balances, pay, loan terms, letters or talk.

- [ ] **Step 1: Failing tests** — `test_staff_hear_of_a_price_change_and_owner_does_not_hear_own_act`, `test_household_hears_a_member_lost_a_job`, `test_customers_hear_of_a_price_change_only_if_they_bought_there`, `test_no_private_figures_in_any_line` (over eight scripted weeks on seeds 1–3: no digest line contains a `$` amount other than a shelf price), `test_at_most_eight_lines_and_nothing_twice`, `test_replay_exact_with_digests` (eight scripted weeks replay), `test_views_stay_within_ceilings`.
- [ ] **Step 2–4: Fail, implement, pass; run the whole suite; commit.**

### Task 9: The probe, and the first two weeks (spends money: about $0.15, cap $0.50 in all)

No code. Run by the controller, not a subagent.

- [ ] **Step 1:** `uv run --env-file .env townsim town-probe --cap 0.25 --n 3 --out runs/probe.json`. Read the table and every reply in `runs/probe.json`.
- [ ] **Step 2:** Rule on each family against these bars, set before looking: readable ≥ 90% of replies; ≥ 60% of acts carried out; no provider refusal; mean seconds ≤ 30. A family that fails is set `left_out: true` with a comment giving its figures; if fewer than three families pass, stop and report instead of running.
- [ ] **Step 3:** `uv run --env-file .env townsim town-live --db runs/hamlet-m1.sqlite --run-id m1 --seed 1 --weeks 2 --cap 0.25`.
- [ ] **Step 4:** `uv run townsim town-story m1 --db runs/hamlet-m1.sqlite`. Read the whole account. Write down, with counts: what people did unprompted; every muddle and which wording or missing fact caused it; every refusal message that did not let the person correct themselves next time; anything in any reply that questions the setting; which families did what.
- [ ] **Step 5:** Fix what the two weeks show is broken in the engine or the views (each fix with a test; scripted hashes may change; record each in the write-up). Do not tune the place toward any outcome.

### Task 10: Eight weeks (spends money: about $1, cap $2)

- [ ] **Step 1:** `uv run --env-file .env townsim town-live --db runs/hamlet-m2.sqlite --run-id m2 --seed 1 --weeks 8 --cap 2`.
- [ ] **Step 2:** `town-story m2`; read it.
- [ ] **Step 3:** Write `docs/runs-hamlet-models-1.md`: what ran and what it cost; the probe table; counts by family; what people did (dealings between people, with the letters quoted); whether lives pressed on them (who fell behind, who changed a price, a job, a rent, a loan); the muddles and their causes; what in the views or operations should change before the AI product is built; what is not yet known (one seed, eight weeks). Plain language; no real names; no local paths.
- [ ] **Step 4:** Commit the write-up and any fixes; push.
