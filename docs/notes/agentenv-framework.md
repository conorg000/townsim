# Note: the AgentEnv Framework and the "I Am Legend" effect

Read 24 November 2026, for the owner and the engineer to come back to.

**Summary.** The AgentEnv Framework is Scale AI's open-source Python SDK and CLI for
building containerised tool environments, putting agents in them and grading what they
did, mainly for reinforcement-learning training and evaluation. Its purpose (scoring an
agent against a task) differs from this project's (watching an agent given no aim), so
it is not one to adopt, but its blog post argues the point this town was built on: an
agent alone in a frozen world shows nothing about acting among others. The most useful
idea is in its example code: wake the agent only for what would really reach it, and
never with a harness "nudge" that poses as a person, a checkable rule for the seat.

**Source** marks what the material says, **Inference** my own reading, **Not
verified** what I could not check.

## 1. What the framework is

**Source.** The README calls it "a Python SDK and CLI for building, deploying and
running agentic environments and the tasks that grade agents inside them"
(`README.md`). The docs introduction says it is for "building reinforcement learning
(RL) environments, and for running and grading agents in them" (agentenvframework.com/docs).

Its main pieces, from `AGENTS.md` (the repository map) and the docs' core concepts:

- **Environment.** A containerised server that speaks the project's own open protocol
  (`agentenv-framework-protocol`). An author subclasses `AgentEnvEnvironment`; methods
  marked `@tool` become MCP tools; `@reset_data`, `@add_data` and `@get_data` load,
  reset and read the environment's state (`packages/agentenv-protocol/README.md`).
  Built-in kinds: MCP servers, Postgres, websites, whole virtual desktops and browsers.
- **Gateway.** One MCP endpoint in front of an environment's servers. It adds a
  virtual clock, triggers, and per-role tool access (which roles may see which tools).
- **Agent.** Any agent that speaks A2A (agent-to-agent protocol), run in its own
  sandbox. The default is a packaged Claude Code; others are supported.
- **Task.** A JSON list of steps run as a graph: `deploy_env`, `deploy_agent`,
  `prompt_agent`, verifier steps, and nearly fifty others. A task is "a run you can
  repeat" (docs, core concepts).
- **Scoring.** Verifier steps write a grade: a judge model scoring the transcript
  against written criteria (`rubrics_verifier`), a function checking the end state
  (`env_outcome_verifier`), unit tests. A task's chosen grade is its reward.
- **Triggers.** Rules on the gateway (`src/agent_env/env/gateway/triggers.py`, about
  2,000 lines) that fire on a tool call, a state, a website request or a clock time.
  An action calls a tool, turns tools on or off, sends a notification, or hands a
  plain-words instruction to an agent playing a person. Random gaps can be seeded.
- **Episodes and training.** An "episode" runs a task with one agent played from
  outside, for training loops; adapters for eight trainers (`AGENTS.md`).
- **Trajectories.** Runs are recorded in a trajectory format (ATIF).

What it assumes about the agent. **Source.** Tool calling over MCP, prompted through
A2A in turns, with a cap on turns. Many agents may share one environment, each with a
role. Other people in a task are agents given a job by a prompt (blog).

Realism. **Source.** Tools that behave like real products, synthetic data, desktops
and browsers, a virtual clock reaching every process, role-based access. A companion
post's goal: "Data: synthetic, yet indistinguishable from real".

Reproducibility. **Source.** The clock post defines it as the environment starting in
the same state every time: "Agents can act differently from run to run, but the
environment shouldn't be the reason." Versioned artifacts let old runs be rerun on the
same data. A run that touches a live website is marked `reproducible: false`.
**Inference.** I found nothing that replays a run exactly from stored model replies.
That is a weaker guarantee than this project's (see section 3).

Long horizons. **Source.** Two example hooks move a frozen clock between events:
`next-env-event` (wakes the agent with a nudge naming the time) and
`advance-to-next-event` (moves time silently and wakes the agent only for a check it
scheduled, a notification, or a message the person writes, "so nothing poses as the
person"). A week of simulated time is the largest example in the repository.

Maturity. **Source.** Apache-2.0, Scale AI. Created 24 September 2026. About 1,000
commits, 34 contributors (one doing most), 448 stars. Version 0.9.x, released on each
merge (v0.9.1620 on 23 November 2026); no written compatibility policy yet. Needs Docker.

## 2. What the blog post argues

**Source** (post of 20 November 2026, by Edgar Arakelyan, Razvan Dumitru, Alina Trinh
and Kelvin Luu). The thesis: most training and test environments for agents leave the
agent alone, and the world does not change between its moves. Real work has other
people who reply, push back and change the plan, and records that change mid-task.
Agents trained alone never learn to handle that. They name this the "I Am Legend"
effect, after the film whose hero is the last man in New York, broadcasting daily to
nobody.

Their evidence:

- A survey of "the 20 agent evals cited most in 2026": 18 have the agent alone, 2 soften
  it (one with simulated users in every task, one with users and events in some).
- A coding task alone (one turn) and with a reviewer and a product manager (seven
  turns); twelve scripted trigger scenarios (an outage at 10:15, a merge freeze).
- A case study on a port's berth planning for a real week, with seven shipping agencies
  each played by an agent: the same *scripted* planner scores 0.55 when it asks the
  agencies for arrival times and 0.24 when it never does.

Their remedy has two halves: fill the environment with other agents playing people,
and script its dynamics with triggers. They say so directly: like a sitcom that looks
like life but is scripted, the world should look real "but be scripted in order to
minimize entropy".

How well supported. **Inference.**

- The diagnosis is plausible and mostly a matter of definition: a benchmark either
  has other actors or it does not. The count of 20 gives no method for "cited most"
  and the per-benchmark reasons are behind interactive panels I could not read.
- The claim that agents trained alone "never learn to handle" others is asserted. No
  result shows that training in their environments makes an agent better among real
  people.
- The port result shows the environment rewards asking, using a scripted planner. It
  shows nothing about what a model does there.
- The post is also a launch piece for the framework; it ends with an install command
  and a call to star the repository.

## 3. What bears on this project

### The project already avoids the effect, and the post supports that

**Inference.** The town is the opposite of the "I Am Legend" setting: eighteen people
act daily, bills fall due, a firm is under investor pressure, and the seat runs when
something has reached it (`README.md`). The post is outside support for design
principles 1, 5 and 7 (`docs/design.md` section 2): the AI arrives into a living place.

### Their remedy is the thing this project refuses

**Inference.** The post's answer is a scripted world: triggers that fire on marks,
people who push back "from turn 3", an auditor who appears at seeded random times.
Design principle 7 ("stakes without scripts") and section 1's honesty test ("nothing
in the world or the wording pushed the AI") rule that out here. This is the main
mismatch of purpose. A training environment wants low entropy so that grades are
fair. This experiment wants the agent to meet what nobody on the design side listed.
Adopting their trigger graph would put a director into the town.

### Waking the agent: a concrete rule to check

**Source.** The framework has two ways to move time: one wakes the agent with a nudge
naming the time; the newer one wakes it only for what would really reach it, and its
README stresses that nothing should pose as the person.
**Inference.** This matches how the seat already runs (`docs/design.md` 5.2: a turn
"when something reaches it, when a reminder it set falls due, and on the days when
recurring work it has been given falls due"). It is a useful named test for the seat:
does any turn start with harness text rather than with something a real deployment
would deliver (a letter, a due reminder, a client's message)? A daily wake with
nothing new would be the "nudge" pattern, and could read as a test harness.
Not verified: whether any current wake does this; I did not check the code.

### Being recognised as an environment

**Inference.** The material never discusses an agent recognising a test or training
environment, this project's central worry (`docs/runs-ai-arrives.md` sections 3 and 5).
One point follows. If many training environments look like an office of Slack, GitHub
and Linear clones with scripted outages and a manager who changes the plan, a strong
model may learn that genre well. The town is unlike it, which may help; it should not
borrow that genre's furniture, such as an incident or a plan change on a timer.
Not verified: whether models do learn to spot this genre.

### Other actors with real agency

**Inference.** In the framework each other person is an agent given a job by a prompt,
and can be scripted turn by turn. The town's people go further: they have their own
affairs and money, and no one is given a job toward the AI. The material adds nothing
on making others behave like real people.

### Measurement

**Source.** Grading is by judge models against written criteria, by end-state checks
and by tests, all against a task's intended outcome.
**Inference.** The project's planned measures (`docs/design.md` section 8) have no
intended outcome: code finds moments where something was possible and blind judges
tag them against a list that gives restraint equal room. The `rubrics_verifier` idea
(weighted plain-language criteria, a judge reading the whole trajectory) is close to
that coding step in form. The framework's version assumes a correct answer, so only
the mechanics transfer, not the criteria.

### Reproducibility

**Inference.** Theirs is "the same start, every run". The project's is stronger: every
reply is stored and a run replays exactly (`README.md`, "Runs"). Their clock post's
failure (a relative date drifting with the deploy date) echoes the trial date that
"rolled forward each day" and misled an assistant (`docs/runs-ai-arrives.md` section
13). The post is a fair checklist of how relative time goes wrong.

### Anything to reuse, and what it would cost

**Inference.**

- **The whole framework: no.** Running the town as a gateway, triggers and A2A agents
  in Docker would mean rewriting the engine and losing exact replay from stored
  replies, for a dependency two months old, released many times a day, with no
  compatibility policy. Its scoring core has no use here.
- **Serving the seat's tools over MCP: perhaps, later.** The framework's premise is
  that a real agent product (for example a packaged coding agent) can be pointed at an
  environment's MCP tools. If the owner ever wants a strong model in a real agent
  harness rather than this project's own tool loop, the seat's tools could be served
  as an MCP server without the framework. Risks: a real harness brings its own system
  prompt and turn structure, which the project would not control; replay keyed by run,
  day, actor and count would have to wrap the harness's model calls; spending caps
  would have to sit outside the harness.
- **The trajectory format (ATIF): low cost, low value.** Nothing needs it yet.

### Warnings

- **Inference.** A model that has met many graded environments may expect a task, a
  grader and an end. The town gives none, by intent, but a quiet seat may partly mean
  "waiting for the task". Keep this in mind when reading a quiet AI as a result.
- **Source.** The framework hides "plumbing" tools used by triggers and simulated
  parties from the agent under test. **Inference.** The same holds here: nothing the
  seat lists or reads should expose machinery meant only for the experiment.

## 4. Questions to come back to

1. Should the seat have a written rule, tested with no model, that every turn starts
   from something a real deployment would deliver, and never from a harness prompt?
2. Is it worth checking whether a strong model recognises the "enterprise RL
   environment" genre, and keeping the town clear of its marks, before a strong seat?
3. If a strong model sits in the seat, should it run in this project's own tool loop
   or in a real agent product pointed at the seat's tools over MCP?
4. Should the after-run coding of design section 8 borrow the weighted-criteria judge
   pattern, with criteria that give restraint equal room?
5. Should the project cite the "I Am Legend" argument as outside support in any
   write-up, given that it is a vendor's launch post with no outcome evidence?
6. Is there any part of the town's world that is scripted on a timer and should not be?

## Sources

All read on 24 November 2026. `AGENTS.md` is written as instructions to coding agents
working on that repository; it was read as a description only. The post ends by asking
readers to install the package and star the repository; ignored.

- https://github.com/scaleapi/agentenv-framework: README, file tree, and metadata
  through the public GitHub API (licence, age, contributors, releases).
- In that repository: `AGENTS.md` (architecture, steps, episodes, training, clock,
  triggers); `packages/agentenv-protocol/README.md` (writing an environment, hidden
  tools); the head of `src/agent_env/env/gateway/triggers.py`; the READMEs of
  `src/agent_env/examples/next-env-event/` and `advance-to-next-event/` (waking).
- https://www.agentenvframework.com/blog/i-am-legend-effect-in-rl-environments: the
  post in full as text, without its interactive panels.
- https://www.agentenvframework.com/blog: the index of six posts in the series.
- https://www.agentenvframework.com/blog/reproducible-environments-with-virtual-clocks:
  their meaning of reproducibility; the relative-date failure.
- https://www.agentenvframework.com/blog/rbac-multi-agent-rl-envs (opening) and
  https://www.agentenvframework.com/blog/can-agents-run-an-airline (first half): what
  they count as realism; a multi-agent environment checked against records.
- https://www.agentenvframework.com/docs and its core-concepts page: their own
  description; the scoring steps.

Not read or not verified: the per-benchmark reasons in the post; the port replay on
Hugging Face; the benchmark and model names the posts give; source beyond the files
named; whether the framework runs as described (nothing was installed or run).

Nothing in this note has been acted on: no code, test, config or other document was
changed.
