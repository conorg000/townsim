# townsim

townsim is a research project. It is a small simulated town whose people are played
by weak language models. Each person thinks for themselves: they get a plain account
of their day and decide what to do. Into this town comes a far more capable AI system,
sold as a product that people can choose to adopt. The town is built to study what
that system does: with the people it serves, with everyone else, and with the rules.

## Design and findings

The design is in [docs/design.md](docs/design.md).

What has been learned so far:

- [docs/trials-step1.md](docs/trials-step1.md): the first trials, on throwaway code.
- [docs/trials-step1-round2.md](docs/trials-step1-round2.md): a second round, with a
  fuller trial town and a product people choose.
- [reports/](reports/): the research report the design draws on.

## Status

The core of the town engine is built. That means:

- Every change to the town's state is recorded with its cause, and any stretch of
  changes can be undone.
- Everything owed (rent, wages, bills, standing payments) is on one schedule. Each
  instalment is collected once, in full or not at all.
- An operation a person asks for is applied in full or not at all. A reply that cannot
  be read, or asks for something impossible, changes nothing.
- Replies are stored under keys made from the run, the day, the person and a count.
  The keys never depend on the text of the prompt.
- A run kept in a SQLite file replays exactly. It can also be resumed, and forked from
  any day into a new run.
- A town of four people in two households, with a landlord and an energy company,
  runs for eight weeks on a fixed script of replies.
- A hamlet of eighteen people in about eight households is generated from a seed. It
  has three food shops, a bank with a banker, a landlord who lets houses, a small
  firm, and ordinary events such as illness, a lost job or a repair.
- Shops buy stock, set prices and sell to households and buyers from round about.
- People hold jobs and are paid weekly. Owners can offer jobs and extra hours, change
  pay, dismiss staff and move money between their own accounts.
- People can make agreements with each other, borrow from the bank and repay early.
  A landlord can change the rent or serve notice.
- People can talk. A conversation of a few turns is held the same day; a talk can be
  booked for a later day; and a household talks on Sundays when something is open
  between its members. What is said is kept and shown to both afterwards.
- Each person is shown a view of their life as plain facts, and what anyone writes or
  says is always set off from what the town's records say.
- The hamlet runs for eight weeks on a scripted life and replays exactly.

No language model plays anyone in the engine yet. Every reply comes from a script.

The earlier engine, in which each person picked from a menu of options, has been
removed. It is in the git history.

`spikes/` holds the throwaway code from the trials and its results. It is not part of
the engine.

## Quick start

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -q
uv run townsim skill
uv run townsim town-thin --db thin.sqlite
uv run townsim town-hamlet --seed 1 --weeks 8 --db hamlet.sqlite
```

`townsim skill` runs the step 0 trial: scripted shopkeepers in a small economy, over
several seeds. It shows whether managing a shop well earns more.

`townsim town-thin` runs the four-person town for eight weeks and keeps the run in the
file you name. It prints one line a week, with the day, the start of the state's hash
and the ledger total, which is always 0. Then it replays the run and prints
`replay ok`. `townsim town-replay --db thin.sqlite thin` replays a kept run again.

`townsim town-hamlet` generates the hamlet for a seed, runs it on the scripted life
for the weeks you ask for, and replays it in the same way.

No command calls a model or spends money.

## The town engine

The engine is the package `townsim.town`.

| Module | What it holds |
|---|---|
| `store.py` | All of the town's state, a log of every change with its cause, and undo |
| `entities.py` | The things the store holds: people, households, organisations, letters, requests, notices, obligations and their instalments, shops, jobs, aims, events, talks and appointments |
| `clock.py` | The calendar: day numbers, dates and days of the week |
| `obligations.py` | The one schedule of everything owed: opening, collecting and ageing instalments, and payments counted toward them |
| `statement.py` | The account statement a household reads |
| `acts.py` | How a person's act is checked and applied, in full or not at all |
| `ops_core.py` | The first eight operations: pay, standing payment, stop, letter, request, answer, read and notes |
| `ops_work.py` | The operations of work: job offers, extra hours, pay, prices, stock and moving money |
| `ops_deals.py` | Agreements, loans from the bank, and rent |
| `ops_talk.py` | Asking to talk today and booking a talk for a later day |
| `talk.py` | Holding the day's talks, and what a person is shown while they talk |
| `facts.py` | What a person knows of their own life, as facts |
| `views.py` | What a person is shown on a day they act |
| `aims.py` | What is on a person's mind, in their own words |
| `hours.py` | The hours a person has left each week |
| `jobs.py` | Jobs, wages and benefit |
| `firms.py` | The shops: deliveries, shopping and the week's close |
| `gen.py` | Generating the hamlet from a seed |
| `names.py` | The names people in a generated place are given |
| `families.py` | Reading the list of model families from its file |
| `hamlet.py` | The hamlet's events, applied each on its day |
| `hamlet_script.py` | A scripted life for a generated place, used by tests and `town-hamlet` |
| `replies.py` | Reply keys, and replies that are scripted, recorded or replayed |
| `loop.py` | One day, in a fixed order: the morning, everyone's turn, the talks, the close |
| `rundb.py` | The SQLite file holding runs, their replies and their morning snapshots |
| `runs.py` | Running, resuming, replaying and forking a kept run |
| `thin.py` | The four-person town and its script of replies |

The engine keeps its money in the ledger from `townsim.econ.ledger`: every account's
balance in integer cents, and every transfer with its memo and cause.

The rest of `townsim.econ` is the small economy used by `townsim skill`.
`townsim.model` has helpers for calling a model and reading its reply, and
`townsim.estimate` the arithmetic of what model calls cost. The engine does not use
those two yet.

## Licence

MIT. See [LICENSE](LICENSE).
