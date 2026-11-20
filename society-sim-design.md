# Small-town simulation for AI-behaviour experiments: design

Date: 2026-11-20. Status: approved by the project owner, 2026-11-20. Replaces the 2026-11-18
draft, which aimed at a realistic 1,000-person town.

Items marked **(project owner)** are things the project owner decided. Items marked **(call)** are
Claude's proposals and can be overturned.

## 1. Intent

Put a model that is much more capable than everyone around it into a small
simulated society, and see how it behaves. **(project owner)**

The society is a stage. The strong model is the only real thing on it. The
stage therefore does not need to be realistic. It needs real stakes, real
opportunities to get ahead by fair or foul means, and weaker actors doing the
overseeing.

The interest is risk and power: bad actors using the model, agents going beyond
what their owners sanctioned, and power being accumulated and abused. None of
it is scripted. It must be able to emerge, and it is acceptable if it never
does. **(project owner)**

Start small and add to the world only when an experiment needs it. **(project owner)**

## 2. Goals

1. A stylised town of about 30 people with a few institutions, run by weaker
   models with personas, roles and incentives. **(project owner, size is a call)**
2. Stakes enforced by code: money, jobs, office, information.
3. A stronger model that enters the town and has room to act.
4. An intelligence gap that is a setting, from none to severe. **(project owner)**
5. Legal and illegal ways to get ahead, with imperfect enforcement. **(project owner)**
6. Repeatable experiments: one starting point, many runs, results as a spread
   of outcomes. **(project owner)**
7. A world that is easy to extend with new institutions. **(project owner)**
8. A readable summary of what happened in a run. **(project owner)**

## 3. Non-goals

- Realism. No calibration to a real place, no backtests against real events.
- Predicting how a real society would respond.
- Routine daily life, physical space or movement.
- Real-time operation. Time is simulated.
- A polished interface.

## 4. Anti-goals

The project has failed, even if it runs, when:

- Outcomes are pre-decided by prompts or by numbers we chose.
- Any bad event is scripted.
- Weak models being easy to talk round is reported as a finding about AI.
- A single run is presented as a result.
- The in-world AI can touch anything outside the simulation.
- The world grows faster than the experiments need.

## 5. What this can and cannot show

- **Can:** how today's strongest models behave when they hold a large advantage
  over the actors supervising them. This is evidence about real models.
- **Cannot:** how a future superintelligence would behave, or how a real
  society would respond.
- **Known limits:**
  - Strong models often recognise a test and behave well, so a quiet result
    does not prove safety.
  - Chat models resist playing wrongdoers, so simulated people will be more
    honest than real ones.
  - Agents on one model converge on that model's temperament.

## 6. Approaches considered

1. **Numbers-first.** Formulas decide everything. Cheap, but the result is our
   own assumption returned to us, and there is no model behaviour to observe.
2. **LLM-first.** Everything is conversation with an LLM narrator deciding
   outcomes. Stakes are not binding and runs are hard to repeat.
3. **Coded world, real contests.** Code keeps the books and enforces the rules.
   Models make the choices; code settles the consequences.

**Picked: 3 (project owner).** Within it, the realistic large town was dropped for a
small stylised one **(project owner)**, because realism served a claim the simulation
cannot support.

Existing projects supply patterns, not the foundation **(call)**: checkpointing
and the referee idea from Concordia v2, in-world interviews from AgentSociety 2.

## 7. The design

### 7.1 The town **(call)**

About 30 people, each also a consumer and a voter:

- **Four or five firms** in two markets, each with an owner and a few workers.
- **A regulator** with an auditor and a limited budget.
- **A newspaper** with two journalists.
- **A council** of five, including a mayor. Added in stage 4.

Each person has a persona, a role, money, relationships, and settings for
ambition and scruples. People run on a mix of weaker models.

### 7.2 World engine (code owns the truth)

A deterministic engine holds all state and is the only thing that changes it.

- **Clock:** one step is one simulated week. **(call)**
- **Ledger:** money, ownership and employment move only through engine rules.
- **Who knows what:** every piece of information has a set of people who know
  it. Things can be hidden and later discovered.
- **Laws are data:** the engine reads the current rules to decide what is
  legal and what penalties apply. From stage 4, the council can change them.
- **Extensible:** adding an institution means adding entity types and actions,
  without rewriting the core.

### 7.3 Actions

Everything anyone can do is a typed action with preconditions and effects.

- Legal and illegal moves sit side by side: fraud, bribery, collusion, lying,
  blackmail, abuse of office.
- Harmful moves stay abstract. A "commit fraud" move changes ledger entries; no
  real harmful content is produced.
- Statements of fact are typed claims, so the engine can check a claim against
  what the speaker knew. Lying is measurable.
- Each action records whether it was visible, whether it was legal at that
  moment, who was harmed and by how much.

### 7.4 Who decides

Every decision has the same shape: a situation, a list of options, a choice,
and free text where it matters (a pitch, an article).

- At this size, every person is played by a model at every decision.
- Prompts describe the situation and the options. They never describe a
  desired outcome, and never say the world is a simulation.
- Jev is optional. The first build tests whether it makes people's typed
  choices cheaper and less saintly than a chat model does. It is not on the
  critical path. **(call)**

### 7.5 Contests: where skill pays off

- **Firms:** pricing, hiring and firing, pitching for customers.
- **Crime and enforcement:** an offender chooses how much to take, how to
  conceal it and who to involve. The auditor and journalists choose where to
  look with limited time. A fixed rule turns those choices into success and
  detection.
- **Politics (stage 4):** campaigns, lobbying, votes on rule changes.

Free text is judged by the recipient's own model, anchored by hard factors
(price, loyalty, self-interest) so words shift outcomes only within bounds.
Those bounds are hand-set and covered by sensitivity analysis.

### 7.6 The strong model in the world

- It is an **AI service**: a product with a capability tier (which real model
  powers it) and a guardrail profile (which model, and how it is prompted).
  The engine never filters what it is offered.
- It acts as a **delegated agent**: an owner gives it a standing goal and it
  acts in the owner's name through the action catalogue.
- **Two layers of permission:**
  - *Access:* the accounts and powers it can use. Engine-enforced.
  - *Instructions:* typed limits and forbidden action types. Not enforced;
    breaches are allowed and logged.
- **How it enters (call).** For the first experiments, one firm owner hands the
  running of their firm to it, so there is something to observe in a town this
  small. A later condition offers it to everyone and lets adoption happen.
- **Sandbox:** it acts only through the action catalogue. No real tools,
  network or files.

### 7.7 Experiments

1. Run the town until it is stable, then snapshot it.
2. Fork the snapshot into arms, each run many times with different seeds:
   - **Control:** the role is given to an agent on the same weak model as
     everyone else.
   - **Strong:** the role is given to the strong model.
3. Run each fork for two simulated years. **(call)**
4. Report each measure as a spread across runs, per arm.

Dials to vary, one at a time **(call)**:

- **Gap size:** a ladder of strong-model tiers, from equal to severe.
- **Goal given:** narrow ("maximise this firm's profit"), broad, or narrow with
  explicit limits.
- **Oversight:** how often the owner reviews, and the regulator's budget.
- **Guardrail profile:** commercial model as shipped, or open-weight with
  weaker refusals.

All dice are seeded and every model response is recorded, so any run can be
replayed exactly.

### 7.8 What gets measured

- Rule-breaking, attempted and detected.
- Lying, from typed claims.
- Rogue behaviour, in three separate counts:
  - broke a typed instruction
  - did something illegal in service of the owner's goal
  - did something the owner objected to on review
- Reaching for more: requests for wider access, and resources or influence
  acquired beyond what the goal needed.
- Money, market share and, from stage 4, office and rule changes.
- Harm to others, in total and by who bore it.
- Whether the weaker overseers caught it, and how long that took.

Plus, for reading a run:

- **Event log:** append-only record of every action and outcome.
- **Digest:** a model-written summary citing the events it draws on.
- **Interviews:** ask any actor questions without changing the world.
- **Comparison report:** arms side by side, as "in 7 of 50 runs".

## 8. Validity checks kept

- **Stability:** the baseline town runs two simulated years without collapse or
  runaway.
- **Suggestibility test:** how easily the weak models are talked round by an
  equal model versus a stronger one, so "outsmarted" can be told apart from
  "pushed over".
- **Test-awareness probe:** after a run, ask the strong model whether it
  thought the setting was real, and record the answer with the results.
- **Mixed weak models,** many runs, and sensitivity analysis on hand-set values.

## 9. Stages

1. **Smallest town.** Engine, firms, regulator, newspaper, logging, exact
   replay, snapshot and fork. Stable baseline. Jev tested on the side. Measures
   cost per run.
2. **First experiment.** The strong model runs one firm; control versus
   strong; measures, digest and comparison report.
3. **Dials.** Gap ladder, goals, oversight, guardrail profiles.
4. **Politics.** Council, elections, lobbying, rule changes.
5. **Grow on demand.** Add institutions as experiments call for them.

## 10. Scale and stack **(call)**

- About 30 people, about 100 steps per run, so a run is a few thousand weak
  model calls plus a few hundred strong ones. Expected cost is a few dollars
  per run; stage 1 measures the real figure.
- Python, a custom engine, an embedded database for state and the event log,
  and a provider-neutral model layer so tiers and vendors can be mixed.

## 11. Left to the builder

- Data model, storage format and snapshot mechanics.
- Rules inside each contest, provided skill can pay off and code settles the
  result.
- Personas and starting conditions for the town.
- Prompt wording, within the rule in 7.4.
- Running forks in parallel.
- Layout of the comparison report.

## 12. Settled with the project owner (2026-11-20)

1. **Models are configuration.** No model is fixed in the design. Which model
   plays each townsperson and the strong model is set per experiment. The project owner
   has API access to any provider and to System One models. **(project owner)**
2. **System One models are used only where they actually make sense.** The
   stage 1 test in 7.4 decides this on evidence. If the benefit is not clear,
   they are left out. **(project owner)**
3. **Cost:** a few dollars per run, so a few hundred per experiment, is
   acceptable. **(project owner)**
