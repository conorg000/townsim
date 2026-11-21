# townsim

A small, deterministic simulation of a fictional town, built to study how AI
models behave when they are far more capable than the actors around them.

The town is a stage. Thirty simulated people run firms, work, pay tax, audit
each other and publish a newspaper. Code owns all state and enforces the rules;
each person only ever picks one option from a typed list. Any person can be
played by a simple rule, a recorded choice, or a language model named in the
config.

This is a research project and a work in progress. The design it is moving to
is in [docs/design.md](docs/design.md): every townsperson played by a weak
model that thinks for itself, and a far more capable AI system that people
adopt. The code today still follows the earlier design,
[society-sim-design.md](society-sim-design.md). The research behind the new
design is in [reports/](reports/).

## Status

Stage 1 of the design is built, and stage 2 has started.

- 30 people, five firms in two markets, a tax auditor and two journalists.
- Legal and illegal moves: pricing, hiring and firing, setting pay, honest or
  hidden tax filing, tip-offs, audits, investigations, publishing, and bribes.
- Owners review what they pay staff every few weeks. Staff whose pay is cut
  can stay or leave, and nobody takes a job that pays less than the benefit.
  The town can set a minimum wage (`laws.min_wage`); by default it has none.
- An owner can put an AI system in charge of their firm (`assistant:` in the
  config). It is not given the owners' menus: it gets a weekly briefing of facts,
  general-purpose tools (prices, pay, hiring, payments, messages, look-ups, notes,
  reports to its client) and writes free text. Each client's firm can pay its provider
  a weekly fee (`assistant.fee`). With `assistant.adoption`, every owner decides at the
  end of each quarter whether to sign up, and every client whether to keep it. See
  [config/town-assistant.yaml](config/town-assistant.yaml).
- Townspeople played by a model see their recent messages and can reply to them.
- Money is integer cents and is never created or destroyed.
- Every run can be forked from any week and replayed exactly. Replay fails if
  the log, a snapshot, the seed or the config has been altered.
- Hidden acts stay out of the public event record.
- Language models have played the whole town in live runs, and those runs
  replay exactly from their recorded replies.
- `compare-deciders` puts the same decision to several models many times.
  Probe decisions are in [config/probe_decisions.yaml](config/probe_decisions.yaml)
  and [config/probe_agent_decisions.yaml](config/probe_agent_decisions.yaml).

Not built yet: free-text contests among townspeople, the council and elections, run digests and
interviews.

The client for "System One" models (models that return a probability per
option) is unverified against the vendor's live service. See
[docs/system-one-findings.md](docs/system-one-findings.md).

## Quick start

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -q
```

Run the baseline town for two simulated years, check it replays exactly, and
print its numbers:

```bash
uv run townsim run config/town.yaml --steps 104 --seed 1 --run-id base --db runs.sqlite
uv run townsim replay --db runs.sqlite base
uv run townsim report --db runs.sqlite base
```

Branch a new run from the end of that one with a different seed:

```bash
uv run townsim fork --db runs.sqlite --from base --at 104 --run-id f1 --seed 2 --steps 104
```

`uv run townsim --help` lists every command and option.

## How it fits together

One step is one simulated week. Each week the engine runs the same phases in
the same order: the AI system's turns, owners decide, people buy, wages are paid,
the AI system's fee is paid, tax is filed each quarter, owners decide whether to sign up
for the AI system or keep it, tips, audits, the press, replies to messages, closures.

| Module | Responsibility |
|---|---|
| `world.py`, `ledger.py` | State models and money transfers |
| `events.py` | Event log, snapshots and state hash (SQLite) |
| `actions.py` | Typed actions with preconditions, legality and rejection |
| `decide.py` | The decision interface and rule, recording and replay deciders |
| `model.py` | The decider that asks a language model |
| `market.py`, `tax.py`, `press.py`, `bribery.py` | The weekly phases |
| `assistant.py` | The AI system's briefings, tools and turns; messages and replies |
| `adoption.py` | The AI system's fee, and owners signing up for it or cancelling it |
| `sim.py`, `runs.py` | The step loop, abort and resume, fork and replay |
| `measures.py`, `compare.py`, `cli.py` | Run measures, decider comparison, commands |

The town itself is configuration: [config/town.yaml](config/town.yaml) and
[config/personas.yaml](config/personas.yaml). Which model plays which person is
set there; no model name appears in the engine.

## Using language models

Add a decider of `type: model` to `config/town.yaml` and assign it to people by
role or id. Model calls go through [LiteLLM](https://docs.litellm.ai/), so set
the API key your chosen provider expects. Model runs cost money and are not
deterministic, but every reply is recorded (free text too, as `text` events), so
any finished run still replays exactly.

The `assistant:` block names the decider that plays the AI system, its clients
(owners) and the instruction each gave it, and how many model calls (`max_rounds`)
and tool uses (`max_actions`) it has per week. A rule decider cannot write, so an
assistant played by one takes no actions.

## Estimating cost

`townsim estimate` works out what runs would cost with different models, without
calling any of them. It runs the town with the rule deciders, renders the exact
prompt each decision would send, counts tokens offline and multiplies by the
prices in [config/prices.yaml](config/prices.yaml). The same inputs always give
the same figures.

```bash
uv run townsim estimate config/town.yaml --steps 104 --runs 50
```

Price a mix of models by role with `--mix owner=big,default=small`. The shipped
prices are a dated snapshot of OpenRouter's list; `--openrouter ID,ID` prices
from the live list. It is an estimate: each
provider's tokenizer differs a little, a town played by models makes somewhat
different decisions, and retries are not counted. The AI system's calls and the
replies to its messages are not counted either; the output says so.

## Licence

MIT. See [LICENSE](LICENSE).
