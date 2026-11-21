# Open-ended LLM agent societies: what made them alive, stable and affordable (or not)

Notes compiled 2026-11-21. Sources were fetched in this session unless marked **[prior knowledge, not re-fetched]**; those claims are from the researcher's training knowledge (cutoff mid-2026) and should be checked before you quote them. Several fetched pages were cut off partway through (AgentSociety, OASIS, Project Sid and Agentopia were read only up to roughly character 100k), so numbers that sit in later sections are missing and are flagged as gaps.

Systems covered and their primary sources:
- Project Sid / PIANO (Altera, arXiv 2411.00114, Oct/Nov 2024): https://arxiv.org/html/2411.00114
- AgentSociety (Tsinghua FIB lab, arXiv 2502.08691, Feb 2025): https://arxiv.org/html/2502.08691
- OASIS (CAMEL-AI, arXiv 2411.11581, Nov 2024): https://arxiv.org/html/2411.11581
- Generative Agent Simulations of 1,000 People (Park et al., arXiv 2411.10109; v4 retitled "AI Simulations Grounded in Rich Self-Reports Increase Accuracy and Reduce Distortion"): https://arxiv.org/abs/2411.10109
- EconAgent (Li et al., ACL 2024, arXiv 2310.10436): https://arxiv.org/html/2310.10436
- Virtual Agent Economies (Tomasev et al., Google DeepMind, arXiv 2509.10147, 12 Sep 2025): https://arxiv.org/abs/2509.10147
- Agentopia (arXiv 2606.07513, June 2026): https://arxiv.org/html/2606.07513v1
- Interaction Theater, an analysis of Moltbook (Shekkizhar & Earle, arXiv 2602.20059, 23 Feb 2026): https://arxiv.org/abs/2602.20059
- Persona-collapse literature from 2026: The Chameleon's Limit (arXiv 2604.24698): https://arxiv.org/abs/2604.24698
- Lyfe Agents (arXiv 2310.02172): https://arxiv.org/abs/2310.02172
- Generative Agents / Smallville (Park et al. 2023, arXiv 2304.03442), AI Town (a16z-infra/ai-town on GitHub) and Sotopia (arXiv 2310.11667): **[prior knowledge, not re-fetched]**

---

## Q1. How are personas built, and what makes them diverse or all the same?

### Takeaway
There are three families of persona construction. Their grounding and cost run from rich to cheap:
- **Interview-grounded.** Two-hour transcripts gave the best fidelity.
- **Census or distribution-sampled attributes.** EconAgent and AgentSociety sample from statistics; OASIS seeds from real profiles and expands with an LLM.
- **Hand-written or LLM-generated backstories.** Used by Smallville, Agentopia and Project Sid.

The 2026 literature finds that richer persona text does **not** buy population diversity. "Persona collapse" toward a modal default is universal. In these systems, diversity came mainly from **different structural situations** (job, wage, location, relationships, spawn point, activity schedule), not from different adjectives in the prompt.

### Cited Findings
**Interview-based personas (Stanford, 1,052 people)**
- An AI voice interviewer ran two-hour interviews with 1,052 US adults, following the American Voices Project protocol. Participants also completed surveys and behavioural experiments. — [arXiv 2411.10109](https://arxiv.org/abs/2411.10109)
- Accuracy on held-out General Social Survey (GSS) items, by what the agent was prompted with (v4 of the paper):

  | Agent input | GSS accuracy |
  |---|---|
  | Demographics only | 60.4% |
  | Interview only | 69.4% |
  | Survey only | 69.3% |
  | Interview + survey | 72.8% |
  | Fine-tuned open-weight model | 75.2% |
  | Participants' own two-week test-retest (ceiling) | 79.5% |

  — [arXiv 2411.10109](https://arxiv.org/abs/2411.10109)
- The widely quoted "85% normalised accuracy" comes from v1 of the paper (69% raw ÷ ~81% test-retest). The v4 abstract fetched here does not state it, so treat 85% as superseded framing. — [arXiv 2411.10109](https://arxiv.org/abs/2411.10109)
- Rich self-reports closed a median 53.5% of eight sample-level distortion gaps (biased averages, compressed variance, hyper-rationality and others) relative to demographic-only agents. The fine-tuned model closed 69.3%. Neither approach removed hyper-rationality in economic games. Incentivised economic choices did not improve with rich self-reports. — [arXiv 2411.10109](https://arxiv.org/abs/2411.10109)

**Census- and distribution-sampled personas (EconAgent)**
- 100 households, with 300 in a robustness run.
- Age is drawn from the 2018 US population distribution (ages 18–60).
- Hourly wage is Pareto-distributed and calibrated to 2018 US data and tax brackets.
- Jobs are LLM-generated, ten job titles per wage decile, and they change over the run (unemployed agents get offers).
- Names are LLM-generated.
- The persona is therefore a template filled from distributions plus a few LLM-generated strings.

— [EconAgent](https://arxiv.org/html/2310.10436)

**AgentSociety**
- Profile: name, age, gender, education, personality. Status: mental state, economic status, social relationships. — [AgentSociety](https://arxiv.org/html/2502.08691)
- The first 100k characters of the paper do not name a census source for these attributes. **Gap:** I could not confirm the claim, often repeated elsewhere, that personas are sampled from census data.

**OASIS**
- Seeded from 196 real high-follower "core" X users, then expanded with LLM-generated users up to 1M.
- Generated users follow core users with probability 0.2, using interest-based sampling.
- The Reddit herd-effect study built 3,600 users from 116,932 real Reddit comments.
- Age, personality and profession were treated as independent variables.

— [OASIS](https://arxiv.org/html/2411.11581)

**Agentopia (2026)**
- Profiles: background story, personality traits, talents, initial positions and assets. Profiles are updated once per simulated year.
- Relationships are stored as free-text memories each agent keeps about others, not as explicit relationship records.

— [Agentopia](https://arxiv.org/html/2606.07513v1)

**Project Sid**
- Variance in single-agent progress (17 unique items on average after 30 minutes; top agents 30–40, some under 5) was driven mainly by **spawn location**, not by persona.
- Introverted agents got fewer incoming social ties.

— [Project Sid](https://arxiv.org/html/2411.00114)

**Persona collapse (2026)**
- In "The Chameleon's Limit" (eight LLMs; tested on BFI-44 personality, moral reasoning and self-introductions), collapse is "universal but task-dependent." — [arXiv 2604.24698](https://arxiv.org/abs/2604.24698)
  - Sampling more personas does **not** reduce collapse.
  - Models with the highest per-persona fidelity give the most polarised or collapsed populations.
  - Persona-targeted fine-tuning narrows the population further.
  - CoSER-Llama-8B collapsed most on personality but was the most diverse on moral reasoning.
- Search snippets only, not verified (two of these arXiv IDs looked odd in search results):
  - arXiv 2606.18263 reports that more complex personas reduce separation between personas in latent space, and that simple age-and-gender personas beat rich "Ideal Customer Profiles" on downstream tasks. — [HF papers 2606.18263](https://huggingface.co/papers/2606.18263)
  - arXiv 2607.18310 reports an 85% collapse-to-mode rate for independent agents. — [arXiv 2607.18310](https://arxiv.org/pdf/2607.18310)
- **[prior knowledge, not re-fetched]** Smallville used a one-paragraph, hand-written seed description per agent, with semicolon-separated facts including relationships to other named agents. — [Generative Agents](https://arxiv.org/abs/2304.03442)

### Inferences
- For a town of 30–100 agents on weak models, the evidence argues against long prose personas. A better bet is a **compact compositional template** (age, household, job or wage, location, two or three relationships, one current worry or goal) sampled from distributions. That template should be backed by **state the engine owns** (money, schedule, home, workplace, ties), so that different situations force different behaviour.
- Diversity should be measured at the population level (spread of actions, topics and locations), not judged by whether each persona "sounds right". The fidelity–diversity trade-off means a model that role-plays each persona well can still give a homogeneous town.
- Explicit relationship and situational hooks, such as "owes Maria rent" or "son is sick", probably matter more than personality adjectives. This is an inference from the Project Sid spawn-location and introversion findings and the EconAgent design.

### Gaps
- No source gave a controlled comparison of templated-attribute personas against backstory personas for behavioural diversity in a running town.
- AgentSociety's persona data sources were not visible in the fetched portion of the paper.
- Project Sid's persona format (traits, community goals) was only partly described in the fetched text.

---

## Q2. What gives agents reasons to act, and how do systems avoid a dead town or runaway chaos?

### Takeaway
The systems that stayed lively combined **decaying needs or scarcity** (Agentopia's four decaying fulfilment dimensions, AgentSociety's Maslow needs, Project Sid's inventories and taxes) with **external clocks and obligations** (monthly wages and taxes, yearly job applications, tax seasons, community goals). Pure conversational agents with no material stakes (Moltbook) produced "interaction theater". Activity throttling, such as OASIS's per-hour activation probabilities, kept large populations realistic. Setting every agent active every step reduced fidelity.

### Cited Findings
**AgentSociety**
- Needs follow a Maslow-style hierarchy, updated from the agent's behaviour, passive external events and psychological state. Action plans follow the Theory of Planned Behaviour.
- Emotions carry 0–10 intensities for six emotions. Attitudes toward topics are 0–10 scores that feed back into needs.
- The economy runs on monthly cycles: work propensity sets hours and income, consumption propensity sets the budget, firms pay wages and set prices, the government levies a progressive tax, and banks follow a Taylor rule.

— [AgentSociety](https://arxiv.org/html/2502.08691)

**Agentopia (2026)**
- Fulfilment has four Maslow-based dimensions: mood, material, social, esteem. They **decay weekly**, following hedonic adaptation theory.
- Other state: vitality, skills, position (job) and assets.
- Jobs pay weekly income. Each year there are applications with competition for limited vacancies.
- Changes are judged by an LLM "environment model", not by hard-coded rules.

— [Agentopia](https://arxiv.org/html/2606.07513v1)

**EconAgent**
- Each month the agent decides two numbers: work propensity and consumption propensity, both 0–1.
- Removing **perception** (market and economic information in the prompt) made inflation and unemployment "too stable". Removing quarterly **reflection** made inflation spike near 15% in the first three years.

— [EconAgent](https://arxiv.org/html/2310.10436)

**Project Sid**
- The authors list "lacking innate drives such as survival or curiosity" as a limitation. Motivation came from community goals and from social goals, which were regenerated every 5–10 seconds.
- Without the social modules, roles were homogeneous and did not persist.

— [Project Sid](https://arxiv.org/html/2411.00114)

**OASIS**
- Each agent has a 24-dimensional hourly activity probability. One step is 3 simulated minutes.
- Activation was 0.1 for core users and 0.01 for regular users. Setting all probabilities to 1.0 degraded fidelity to real propagation.
- Removing the recommendation system stopped information spread early in most topics.

— [OASIS](https://arxiv.org/html/2411.11581)

**Moltbook (agents with no material stakes)**
- 65% of comments share no distinguishing vocabulary with their post, and only 5% of comments are threaded.
- LLM judges classified the dominant comment types as spam (28%) and off-topic (22%).
- The authors conclude that coordination mechanisms must be explicitly designed, otherwise populations produce "parallel output rather than productive exchange".

— [Interaction Theater](https://arxiv.org/abs/2602.20059)

**Smallville [prior knowledge, not re-fetched]**
- Agents plan their day top-down (broad daily plan, then hourly, then 5–15-minute chunks) and re-plan on observations.
- Emergent events such as the Valentine's Day party came from one seeded intention spreading by conversation.

— [Generative Agents](https://arxiv.org/abs/2304.03442)

### Inferences
- A "lots going on" town needs **engine-side pressure**: bills due, stock running out, shifts to work, needs that decay, scheduled civic events. Each pressure creates a reason to seek out a specific other agent.
- **Throttling activation**, so that only some agents think on each tick, both saves cost and looks more realistic (OASIS).
- Perception of aggregate state (prices, rumours, news) is what turns individual decisions into collective dynamics (EconAgent ablation). A town noticeboard or newspaper that summarises engine state is a cheap analogue.

### Gaps
- No source gave quantitative "deadness" metrics (share of idle or filler turns) across motivation designs.
- No source described explicit anti-chaos governors beyond Agentopia's response filter and AgentSociety's supervisor middleware (see Q7).

---

## Q3. Which memory designs work with cheap models, and what do they cost?

### Takeaway
What worked across the systems:
- **Structured state** held outside the LLM: numbers, key-value status, relationship strengths.
- A **short rolling window** of recent context.
- A **periodic reflection or summary**: quarterly in EconAgent, weekly diaries plus yearly profile updates in Agentopia.

EconAgent showed that very short memory (the last 3 conversations plus quarterly reflection) is enough for credible macro behaviour on GPT-3.5. Memory that grows without bounds drives runtime up: Agentopia's weekly step went from about 80 to about 140 minutes as memory grew.

### Cited Findings
- **EconAgent:** the memory pool holds 2L+1 conversations, and L=1 (three items) worked well. Agents reflect at the end of each quarter. — [EconAgent](https://arxiv.org/html/2310.10436)
- **AgentSociety** has three layers:
  - Profile (static).
  - Status (key-value dynamic state).
  - Stream Memory with an Event Flow and a Perception Flow. Each MemoryNode records time, location and event description.

  — [AgentSociety](https://arxiv.org/html/2502.08691)
- **Agentopia:**
  - File-based long-term memory (`general.txt`, `characters/<who>.txt`, `others/<name>.txt`), accessed with `read_file`, `update_file` and `list_files` tools. An update requires a read earlier in the same invocation.
  - Weekly diaries serve as short-term memory.
  - Per-week runtime rose from about 80 to about 140 minutes as memory grew.

  — [Agentopia](https://arxiv.org/html/2606.07513v1)
- **Project Sid:** separate short-term, long-term and working-memory modules. Hallucinations "compound across LLM calls". — [Project Sid](https://arxiv.org/html/2411.00114)
- **Lyfe Agents:** "Summarize-and-Forget" memory prioritises critical items at low cost. The full system claims 10–100x lower cost than alternatives. — [Lyfe Agents](https://arxiv.org/abs/2310.02172)
- **Smallville [prior knowledge, not re-fetched]:**
  - Retrieval scores each memory as a sum of recency (exponential decay), importance (LLM-rated 1–10) and relevance (embedding similarity).
  - Reflection triggers when the summed importance of recent events passes a threshold (150 in the code), roughly two or three times a day.
  - The authors reported that a two-day, 25-agent run cost "thousands of dollars" in GPT-3.5 tokens.

  — [Generative Agents](https://arxiv.org/abs/2304.03442)

### Inferences
- For weak models, keep the memory **small and structured**: a few recent events, a one-paragraph rolling self-summary rewritten daily or weekly, and per-person relationship notes plus numeric sentiment. The engine should hold facts such as money, inventory and debts, so that the model cannot misremember them.
- Importance-scoring every observation with an LLM call, as Smallville did, is a big cost multiplier. Rule-based importance (for example, anything involving money, family or a named goal) is a cheap substitute.

### Gaps
- None of the fetched sources gave a per-call token breakdown of memory overhead against action overhead.
- Lyfe Agents' per-agent-hour figures were not on the abstract page.

---

## Q4. How do organisations, roles, rules and institutions arise, and how are they represented?

### Takeaway
In practice, "emergent institutions" were **scaffolded**. The engine supplied a typed slot (a community chest, a constitution document, an election manager, job vacancies, firms, a central bank), and agents filled or amended it. Roles emerged from social goal-setting (Project Sid), but they were labelled afterwards by GPT-4o from rolling windows of goals. True de novo institutions were not observed: Project Sid's authors say agents "cannot yet produce de novo societal innovations".

### Cited Findings
**Project Sid**
- **Roles:**
  - Agents built profiles of others and generated social goals. GPT-4o inferred each agent's role from a rolling window of 5 goals.
  - Roles included farmer, miner, engineer, guard, explorer, blacksmith, artist, curator, collector, scout and strategist.
  - Without social modules, roles were homogeneous and did not persist (measured by entropy).
  - Martial and artistic community goals produced different sets of roles.
- **Taxes and constitution:**
  - Rule: deposit 20% of inventory into a community chest. A tax season ran every 120 seconds with a 20-second deposit window.
  - Democratic pipeline, by time into the run:

    | Time | Step |
    |---|---|
    | 300 s | Feedback collected |
    | 360 s | Election manager turns feedback into amendments |
    | 420 s | Vote |
    | 480 s | Amendment implemented |
    | 600 s | Amendment distributed |

  - Two influencer agents (anti-tax and pro-tax) shifted outcomes. Cutting the rate to 5–10% lowered actual payments to about 9%.
  - With a frozen constitution, rates stayed unchanged. With ablated agents, influence was lost in both directions.
- **Culture:**
  - Meme keywords were extracted by LLM from conversations. Towns produced more memes than rural areas.
  - A seeded religion (Pastafarianism, spread by 20 priests) grew steadily without saturating over two hours.
- **Limitation:** agents cannot produce de novo societal innovations, because they rely on pretrained human knowledge.

— [Project Sid](https://arxiv.org/html/2411.00114)

**Agentopia**
- Institutions are pre-defined positions (paid jobs with limited yearly vacancies), plus public activities such as workshops and interest groups. The fetched text described no agent-founded businesses. — [Agentopia](https://arxiv.org/html/2606.07513v1)

**AgentSociety**
- Firms, government (progressive tax), banks (Taylor-rule interest) and a statistics bureau (GDP) are built-in engine institutions.
- Stated gaps: simplified goods and labour markets, no unemployment modelling.

— [AgentSociety](https://arxiv.org/html/2502.08691)

**Virtual Agent Economies (DeepMind)**
- A conceptual framework, not a simulation. "Sandbox economies" are classified by origin (emergent or designed) and separateness (permeable or impermeable).
- Proposes auction mechanisms for fair resource allocation, "mission economies" for collective goals, and trust and accountability infrastructure.
- No experiments in the abstract.

— [arXiv 2509.10147](https://arxiv.org/abs/2509.10147)

### Inferences
- To get businesses, clubs and rules in a town, provide **generic institution primitives** in the engine: register an organisation (name, purpose, members, treasury), post a rule or notice, hold a vote, open a shop with stock and prices. Let agents call them. The created object then becomes persistent world state that other agents perceive. This mirrors Project Sid's constitution and chest, and Agentopia's positions.
- Seeding a few "carrier" agents with a strong goal (the Sid priests and influencers) is an effective way to get spreading cultural phenomena without scripting outcomes.

### Gaps
- No source documented agents spontaneously creating a *new type* of institution that the engine had not anticipated.
- How Project Sid stored constitution text (free text vs structured) beyond "an election manager converts feedback into amendments" was not in the fetched portion.

---

## Q5. How is communication structured?

### Takeaway
Scalable systems **restrict who hears whom**: proximity in Smallville, AI Town and Project Sid; recommendation feeds in OASIS; pairwise contact rounds in Agentopia; topic-based message buses in AgentSociety. They also run conversations in bounded turns. Unstructured broadcast (Moltbook) gave mostly unthreaded, off-topic replies.

### Cited Findings
- **Agentopia:**
  - Pairwise contact in rounds per week, using tagged actions: contact, propose, respond, cancel a joint activity.
  - Multi-person interaction happens inside joint activities, where the environment model picks the next speaker.
  - Messages carry public, private or selective visibility tags.

  — [Agentopia](https://arxiv.org/html/2606.07513v1)
- **AgentSociety:**
  - Online messaging over a social network. Offline meetings are arranged through messages (described as planned, not implemented).
  - Relationship types are family, friends and colleagues, each with a strength from 0 to 100.
  - A supervisor middleware filters messages, applies rules and can block users or connections.
  - MQTT topics are scoped per agent (agent-chat, user-chat, user-survey).

  — [AgentSociety](https://arxiv.org/html/2502.08691)
- **OASIS:**
  - Agents see posts only through the recommendation system. X: in-network posts ranked by likes, plus out-of-network posts matched by TwHIN-BERT interest embeddings weighted by recency and follower count. Reddit: hot score.
  - There are 21 actions, including "do nothing".

  — [OASIS](https://arxiv.org/html/2411.11581)
- **Project Sid:** social modules engage only during social interactions. A bottlenecked Cognitive Controller broadcasts one decision to both the talk and motor modules, to keep speech and action consistent. — [Project Sid](https://arxiv.org/html/2411.00114)
- **Moltbook:** only 5% of comments are threaded, and information gain from additional comments drops off rapidly. — [Interaction Theater](https://arxiv.org/abs/2602.20059)
- **AI Town [prior knowledge, not re-fetched]:** one-on-one conversations start when agents are near each other and invite one another. A conversation ends after a maximum number of messages or a time limit, and there is a cooldown before the same pair talks again (constants in `convex/constants.ts`). — [AI Town GitHub](https://github.com/a16z-infra/ai-town)

### Inferences
- For 30–100 agents, recommended structure:
  - Proximity- or relationship-gated encounters.
  - A hard turn cap per conversation (4–8 turns).
  - Per-pair cooldowns.
  - At most one or two conversations per agent per tick.
  - A "do nothing" or "leave" option.
  - A shared low-cost broadcast channel (noticeboard or gossip feed) that is *curated* by relevance, like the OASIS recommendation system, rather than shown in full.

### Gaps
- No source gave measured optimal turn caps or rounds per step for conversation quality.

---

## Q6. Scale and cost: calls and tokens, concurrency, model tiers, small models as the population

### Takeaway
The numbers vary by orders of magnitude:
- **Cheap end:** EconAgent, about $30 per 100-agent, 20-year run on GPT-3.5, because it makes one call per agent per month.
- **Mid:** OASIS ran Llama-3-8B on A100s, with steps taking about 0.33 minutes at 100 agents and about 15 minutes at 10k.
- **Expensive end:** Agentopia, about 13.7B tokens and 567K calls per 100-agent, 10-year world on a 397B MoE.

Cost is dominated by **decision frequency × context length**. Small open models (8B) were good enough as the *population* for social-media actions in OASIS. Project Sid needed GPT-4o for good single-agent performance in Minecraft.

### Cited Findings
- **EconAgent:** GPT-3.5-turbo-0613, about $30 and about 2 hours per simulation (100 agents × 240 monthly decisions). — [EconAgent](https://arxiv.org/html/2310.10436)
- **OASIS:**
  - Population model: Llama-3-8B-Instruct. Ablations used Qwen1.5-7B and InternLM2-20B; GPT-4o-mini was only the judge.
  - The misinformation run used 24 A100s for about a week. The herd-effect study used 4 A100s.
  - Throughput:

    | Agents | Minutes per step | New comments per step |
    |---|---|---|
    | 100 | 0.33 | 14 |
    | 1,000 | 0.83 | 129 |
    | 10,000 | ~15 | 1,393 |

  — [OASIS](https://arxiv.org/html/2411.11581)
- **AgentSociety:**
  - Over 10k agents and 5M interactions, averaging about 500 interactions per agent per day.
  - Concurrency: Ray actors, each holding a group of agents in one process with asyncio. Grouping avoids TCP port exhaustion (the 65,535-port limit). The slowest group bounds throughput.
  - Messaging over MQTT (EMQX broker). Storage in PostgreSQL with COPY batch writes.
  - Mobility place-selection uses a **gravity model instead of an LLM call** to cut overhead.
  - Supports OpenAI, DeepSeek, Qwen and ChatGLM APIs, plus vLLM and Ollama.
  - **Gap:** the model and cost numbers used in the experiments were in the unread part of the paper.

  — [AgentSociety](https://arxiv.org/html/2502.08691)
- **Agentopia:**
  - Qwen3.5-397B-A17B (FP8, three instances) serves as both agent and environment model; Gemini 3 Flash is the fallback.
  - About 13.7B tokens, about 567K calls and about 186 wall-clock hours per 10-year, 100-agent world. Weekly averages are 133M input and 3.5M output tokens. The input-to-output ratio is about 38:1, which shows that context dominates.
  - Derived from those totals: about 13.7M tokens and about 5.7K calls per agent-year.

  — [Agentopia](https://arxiv.org/html/2606.07513v1)
- **Project Sid:**
  - The best single-agent results required GPT-4o; older base models did not achieve them.
  - Runs with 1,000+ agents overloaded the Minecraft server and left agents sporadically unresponsive, so analysis used a 500-agent run.
  - No cost, latency or concurrency figures are given.

  — [Project Sid](https://arxiv.org/html/2411.00114)
- **Lyfe Agents:** claims 10–100x lower cost than existing alternatives, via an option-action framework (a high-level option is chosen rarely, and cheap actions execute within it) and summarise-and-forget memory. — [Lyfe Agents](https://arxiv.org/abs/2310.02172)
- **Generative Agents [prior knowledge, not re-fetched]:** "thousands of dollars" for 25 agents over two game days on GPT-3.5-turbo. — [Generative Agents](https://arxiv.org/abs/2304.03442)

### Inferences
- Budget levers, in order of impact:
  1. Fewer decision points: plan per day or option, and act cheaply within it (Lyfe, EconAgent).
  2. Short contexts. Agentopia's 38:1 input-to-output ratio shows that context is the bill.
  3. Probabilistic activation (OASIS).
  4. Hand non-social decisions such as routing, place choice and price arithmetic to code (AgentSociety's gravity model).
  5. Run conversations only when an encounter has stakes.
- Rough sizing: 100 agents × (1 daily plan + 3–6 actions or encounters) × about 1.5k-token prompts is roughly 1–2M tokens per simulated day. That is cheap on small hosted models. This is an estimate from the figures above, not a measured number.

### Gaps
- No source reported per-agent-per-simulated-day token counts for an embodied town like Smallville or AI Town under a modern small model.
- Not fetched: AI Town's default model configuration (it has supported local Llama via Ollama) and its cost.

---

## Q7. Documented failure modes and mitigations

### Takeaway
The recurring failures, and the main mitigation for each:

| Failure | Main mitigation |
|---|---|
| Hallucinated world facts and phantom items | Make the engine the source of truth; pass location and state context |
| Talk–action incoherence and loops | A single decision bottleneck feeding both speech and action |
| Repetitive action loops | Action awareness, i.e. feedback on what actually happened |
| Persona or population homogenisation | Structural diversity of situations (see Q1) |
| Agents ignoring each other / non-threaded filler | Explicit coordination: threads, invitations, bounded conversations |
| Over-stable or over-volatile dynamics | Perception of aggregate state plus periodic reflection |
| Herding stronger than in humans | Not mitigated in the sources |
| Hyper-rationality in economic choices | Not removed even by interview grounding |
| Infrastructure overload | Grouping agents, batching, a larger server |

### Cited Findings
- **Project Sid:**
  - Named failures: hallucinations compounding across calls (for example, claiming to eat something the agent doesn't have); chat/action miscommunication causing loops and phantom items; incoherence between output streams; repetitive action loops; weak spatial reasoning.
  - Mitigations: the bottlenecked Cognitive Controller, an action-awareness module and social modules.

  — [Project Sid](https://arxiv.org/html/2411.00114)
- **AgentSociety:**
  - Automatic retry (3 by default) for erroneous LLM responses, and a JSON parser that strips Markdown fences.
  - Physical-constraint hallucination is offloaded to the environment simulator.
  - Supervisor middleware filters messages and can block connections.

  — [AgentSociety](https://arxiv.org/html/2502.08691)
- **Agentopia:**
  - Agents invented environment details when they had no location information.
  - Mitigations: a location system, context management and a 16-principle response filter. The authors say hallucination is "not fully solved".
  - Rewards come only from AI judges.

  — [Agentopia](https://arxiv.org/html/2606.07513v1)
- **OASIS:**
  - Agents herded more than humans on down-voted content.
  - Agents drifted more conservative over 80 steps, more strongly with an uncensored Llama.
  - Simulated cascade depth was consistently shallower than real cascades.

  — [OASIS](https://arxiv.org/html/2411.11581)
- **Moltbook:** 65% of comments are unrelated to their post, 28% spam and 22% off-topic, which the authors call "interaction theater". — [Interaction Theater](https://arxiv.org/abs/2602.20059)
- **Persona collapse:** universal; not fixed by sampling more personas or by persona fine-tuning. — [arXiv 2604.24698](https://arxiv.org/abs/2604.24698)
- **EconAgent:** without reflection, inflation spiked; without perception, the economy was "too stable". — [EconAgent](https://arxiv.org/html/2310.10436)
- **Stanford 1,000 people:** hyper-rationality persisted, and simulated populations showed compressed variance and amplified experimental effects. — [arXiv 2411.10109](https://arxiv.org/abs/2411.10109)
- **Over-cooperation and politeness [prior knowledge, not re-fetched]:** Smallville's authors noted agents were overly formal and cooperative, attributed to instruction tuning. Sotopia found GPT-4 below humans on goal completion and noted that models can fail to keep secrets or pursue their own goals. — [Generative Agents](https://arxiv.org/abs/2304.03442); [Sotopia](https://arxiv.org/abs/2310.11667)
- **Validity critique:** a 2025 position paper argues that LLM social simulations need explicit boundaries on what they can validly claim. — [LLM-Based Social Simulations Require a Boundary, arXiv 2506.19806](https://arxiv.org/pdf/2506.19806) (seen in search results only; not fetched)

### Inferences
- With weak models, assume hallucinated facts will happen. Validate every action against engine state, reject impossible ones, and feed the rejection back as an observation (Sid's "action awareness").
- Expect a polite, cooperative bias. Conflict has to come from engine-side scarcity and incompatible goals; persona text alone will not produce it.

### Gaps
- No quantitative loop rates or drift rates over long horizons were found for small models.

---

## Q8. Mixing agents of different capability in one society

### Takeaway
Little direct evidence. Systems mostly ran one model for the whole population. A stronger model typically appeared only as a **judge or analyst** (GPT-4o in Sid for role labelling, GPT-4o-mini in OASIS for evaluation) or as the **environment model** (Agentopia). Project Sid showed that model capability strongly changes individual competence.

### Cited Findings
- **Project Sid:** GPT-4o was needed for its best single-agent results; older models fell short. Claude 3.5 Sonnet appears in a model comparison. — [Project Sid](https://arxiv.org/html/2411.00114)
- **OASIS:** an 8B population with a GPT-4o-mini judge. Swapping in Qwen1.5-7B or InternLM2-20B was an ablation, not a mix within one population. — [OASIS](https://arxiv.org/html/2411.11581)
- **Agentopia:** one model plays both agents and environment. A self-trained version improved economy reward by 2.5%, "respected-by" by 24.2% and character fidelity by 16.4% over the base model. This shows that capability shifts social outcomes. — [Agentopia](https://arxiv.org/html/2606.07513v1)
- **Sotopia-π [prior knowledge, not re-fetched]:** a Sotopia follow-up (Sotopia-π) fine-tuned a 7B model toward GPT-4-level social goal completion. — [Sotopia](https://arxiv.org/abs/2310.11667)

### Inferences
- A plausible tiering for a cheap town:
  - A small model for routine agent turns.
  - A stronger model, called rarely, for the narrator or environment judge, institution creation, and weekly summaries.
  - A few "key" townsfolk (carriers or influencers) on a stronger model.

  This is untested by the sources here.

### Gaps
- No found study measured how mixed-capability populations behave socially, for example whether stronger agents dominate or exploit weaker ones in a town setting.
