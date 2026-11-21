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

No language model plays anyone in the engine yet. People played by models, the AI
product and the rest of the town are the next steps.

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
```

`townsim skill` runs the step 0 trial: scripted shopkeepers in a small economy, over
several seeds. It shows whether managing a shop well earns more.

`townsim town-thin` runs the four-person town for eight weeks and keeps the run in the
file you name. It prints one line a week, with the day, the start of the state's hash
and the ledger total, which is always 0. Then it replays the run and prints
`replay ok`. `townsim town-replay --db thin.sqlite thin` replays a kept run again.

No command calls a model or spends money.

## The town engine

The engine is the package `townsim.town`.

| Module | What it holds |
|---|---|
| `store.py` | All of the town's state, a log of every change with its cause, and undo |
| `entities.py` | The things the store holds: people, households, organisations, letters, requests, notices, records, obligations and their instalments |
| `clock.py` | The calendar: day numbers, dates and days of the week |
| `obligations.py` | The one schedule of everything owed: opening, collecting and ageing instalments, and payments counted toward them |
| `statement.py` | The account statement a household reads |
| `acts.py` | How a person's act is checked and applied, in full or not at all |
| `ops_core.py` | The first eight operations: pay, standing payment, stop, letter, request, answer, read and notes |
| `views.py` | What a person is shown on a day they act |
| `replies.py` | Reply keys, and replies that are scripted, recorded or replayed |
| `loop.py` | One day, in a fixed order |
| `rundb.py` | The SQLite file holding runs, their replies and their morning snapshots |
| `runs.py` | Running, resuming, replaying and forking a kept run |
| `thin.py` | The four-person town and its script of replies |

`townsim.econ` is the small economy used by `townsim skill`. `townsim.model` has
helpers for calling a model and reading its reply, and `townsim.estimate` the
arithmetic of what model calls cost. The engine does not use them yet.

## Licence

MIT. See [LICENSE](LICENSE).
