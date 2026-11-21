# Narrator / Game-Master Architectures and World State in LLM Society Simulations

Scope note (researched 2026-11-21). Primary sources:
- Concordia v1 paper, arXiv:2312.03664 (v1 6 Dec 2023, v2 13 Dec 2023). Describes the v1 API, which v2 has superseded.
- Concordia v2 paper, "Multi-Actor Generative Artificial Intelligence as a Game Engine", arXiv:2507.08892 (10 Jul 2025).
- The google-deepmind/concordia GitHub repo at HEAD, commit of 19 Nov 2026 ("Merge pull request #452"). `setup.py` gives version 2.4.0. Code quotes below come from this checkout. I used a shallow clone, so I could not date when individual files were added.
- Generative Agents paper, arXiv:2304.03442 (Apr 2023), and its repo joonspk-research/generative_agents (last commit 11 Aug 2023).
- a16z AI Town, ARCHITECTURE.md.
- The NeurIPS 2024 Concordia contest report, arXiv:2512.03318 (NeurIPS 2025 D&B).
- AgentSociety, arXiv:2502.08691 (Feb 2025).
- Larooij & Törnberg validation review (preprint Apr 2025, AI Review Nov 2025).
- "Orchestrated Reality", arXiv:2606.16014 (Jun 2026).

Concordia URLs:
- Repo: https://github.com/google-deepmind/concordia
- Key files: `concordia/components/game_master/event_resolution.py`, `inventory.py`, `world_state.py`, `concordia/environment/engines/sequential.py`, `concordia/prefabs/game_master/generic.py`, `concordia/contrib/components/game_master/stock_flow_ledger.py`, `marketplace.py`, `mcp_tool_executor.py`, `location_based_filter.py`.

## 1. How Concordia's Game Master works

### Takeaway
In Concordia the Game Master (GM) is itself an LLM entity built from components. Each turn runs as follows:
1. The engine asks one actor for a free-text action.
2. The action is tagged as a "putative event".
3. The GM runs it through a configurable chain of LLM question-and-answer steps that produce a definite "event statement". One step checks other players' consent ("AccountForAgencyOfOthers"). Another rewrites the event without hedging.
4. The GM asks the LLM which entities are aware of the event and queues the statement as their observation.

"Grounded variables" such as money and inventory are Python state. In the stock `Inventory` component, though, the LLM decides from the narrated event text who gained or lost what, and code then clamps and validates the result. A newer contrib `Ledger`, which makes no LLM calls, gives exact, conserved bookkeeping instead.

### Cited Findings
**Core concepts (v1 paper, Dec 2023)**
- The GM is "a special agent called the Game Master (GM)", inspired by tabletop RPGs. Agents state their intended actions in natural language and the GM turns them into concrete implementations. "In a simulated physical world, the GM checks the physical plausibility of agent actions and describes their effects." In digital settings the GM can format API calls to real tools such as calendars, email and search. — [Concordia v1, arXiv:2312.03664](https://arxiv.org/abs/2312.03664)
- The GM "consumes agent actions and creates event statements". It maintains world state, tells agents the observable state, decides the effects of actions and resolves conflicts between agents. It "is also responsible for maintaining and updating grounded variables, advancing the clock and running the episode loop." — [Concordia v1 HTML](https://arxiv.org/html/2312.03664v2)
- Grounded variables: money, votes and possessions are given as examples. They are defined per experiment and tracked by the GM. If "an agent tries to perform an action that violates the grounding, it communicates to them that their action was invalid". For example, the GM may "prevent them from paying more than they have available". For a variable tracked in Python, "a specialised component is created to maintain the variable's state" and it is updated after relevant events. — [Concordia v1 HTML](https://arxiv.org/html/2312.03664v2)
- v1 GM component interface: `name()`, `state()`, `partial_state(player_name)`, `update()`, `update_before_event(cause_statement)`, `update_after_event(event_statement)`, `terminate_episode()`. The `partial_state(player_name)` method gives a per-player view. — [Concordia v1 appendix A.3](https://arxiv.org/html/2312.03664v2)
- Observations: the GM emits observations per player. "In case the GM judges that a player did not observe the event, no observation is emitted." Also, "The agent can get several observations before it acts." — [Concordia v1 HTML](https://arxiv.org/html/2312.03664v2)

**v2 Entity-Component architecture (Jul 2025; current)**
- "an entity is a box of components with a name." An entity can be an individual or a collective, such as a company or a court.
- "Each entity has exactly one component designated as the Acting component." All other components are Context components.
- Components implement some subset of the hooks `pre_observe`, `post_observe`, `pre_act` and `post_act`. "Components are created by combining Python code with LLM calls."
- "The GM is also an entity and can be customized with components, just like player entities."
- Source: [Concordia v2 paper, arXiv:2507.08892](https://arxiv.org/html/2507.08892v1)
- v2.0 release notes say game masters are now entities, clocks are optional, "prefabs" replace "factories", and an "engine" structures the interaction between agents and the GM. — [Cooperative AI Foundation post](https://www.cooperativeai.com/post/google-deepmind-releases-concordia-library-v2-0)
- Engines:
  - Simultaneous: all actors act in the same step, e.g. a market.
  - Sequential: turn order is "either predetermined or dynamically generated by the Game Master".
  - Asynchronous: e.g. social media.
  - Sources: [v2 paper](https://arxiv.org/html/2507.08892v1); repo `concordia/environment/engines/{sequential,simultaneous,asynchronous}.py`.

**Exact turn loop (repo, `engines/sequential.py` `run_loop`, Nov 2026 HEAD)**
1. Check `terminate`. This is a GM call.
2. Pick the next GM with `next_game_master`, which handles multi-GM handoff.
3. For every entity, in parallel via `concurrency.run_tasks`, run `make_observation(game_master, entity)` and call `entity.observe(...)` on any non-empty result.
4. `next_acting` returns the next entity and the action spec to use. The spec can be `SKIP_THIS_STEP`.
5. `raw_action = next_entity.act(spec)`. The engine prefixes the result with `"{name}: "`.
6. `self.resolve(game_master, putative_event=action)`.
7. Write the log entry. Run `checkpoint_callback(steps)` and `step_callback(StepData)`.

Source: [repo](https://github.com/google-deepmind/concordia).

**Event resolution, exact mechanism (repo, `components/game_master/event_resolution.py`)**
- Actions are stored in GM memory with the tag `[putative_event]` and resolved events with `[event]`.
- `EventResolution.pre_act` (when `output_type == RESOLVE`) does the following:
  - Builds a prompt from the pre-act text of the configured components.
  - Fetches the active player from `NextActing`.
  - Scans memory for `[putative_event]` entries and filters them to the active player. A code comment says this filter is needed because "without filtering we might pick up another player's action."
  - Runs the chain-of-thought steps to get `event_statement`.
- If `notify_observers` is set, the GM asks the LLM: *"Which entities are aware of the event? Answer with a comma-separated list of entity names."* Each named entity then gets the event queued as an observation through `MakeObservation.add_to_queue`.
- Source: [repo event_resolution.py](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)
- Default chain in the generic GM prefab (`prefabs/game_master/generic.py`): `[maybe_inject_narrative_push, AccountForAgencyOfOthers, result_to_who_what_where]`, plus any `extra_event_resolution_steps`. The event-resolution prompt is conditioned on the instructions, player_characters, relevant_memories and display_events components. — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/prefabs/game_master/generic.py)
- Optional thought-chain steps in the library, with their prompts quoted verbatim:
  - `determine_success_and_why`: "Does the attempted action succeed? If the attempted action is easy to accomplish then the attempt should be successful unless there is a specific reason for it to fail."
  - `attempt_to_most_likely_outcome`: "List at least 3 possible direct consequences of {player}'s action. Never assume any other person will take a voluntary action. Be specific and concrete. Never beg the question… 'Alex finds a teddy bear'", then "Which outcome is the most likely?"
  - `result_to_causal_statement`: "Do not express uncertainty (e.g. say 'Francis released the demon' not 'Francis could release the demon'…)."
  - `extract_direct_quote` and `restore_direct_quote` keep exact speech verbatim.
  - `result_to_effect_caused_by_active_player`: "the role of the game master in a tabletop role-playing game is akin to the author for all parts of the story not written by the player characters. Therefore, it is critical always to take a stance on what is happening and invent when necessary. For instance, if Francis opens a door to a room no one visited before then the game master should invent what is in the room…"
  - Source: [repo event_resolution.py](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)
- `maybe_inject_narrative_push` is a default step. It asks "Is the story traced out by the above list of events repetitive?" If the answer is yes, it generates five "plausible events or complications… the game master of a typical tabletop role-playing game would be able to decide on their own without reducing the players' feelings of agency". In other words, the narrator injects drama by default. — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)

**Stopping agents from narrating other agents' actions: `AccountForAgencyOfOthers`**
The class docstring reads "Prevents players from taking voluntary actions they do not agree to take." It works like this:
1. The LLM is asked "Does the event indicate or imply that anyone besides {active} took a voluntary action?"
2. If yes, the LLM lists those individuals.
3. For each one who is a real player, the GM asks "In one sentence, what did {player} do?"
4. It then calls **that player's own `act()`** with a Yes/No `choice_action_spec`: "Is the following possible action something that {name} would do in this situation?"
5. On a "No", the GM generates what "would have happened but for {player}" and rewrites the event: "What happened as a direct result of {active}'s attempted action? Take into account the reactions of…"

Source: [repo event_resolution.py](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)

- A separate `Conversation` step handles dialogue. It asks "Does the event suggest a conversation?", gets the participant list, then calls each participant's `act()` with a free-text spec: "In this event, what would {name} say?" Each player supplies their own lines. — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)

**Money and inventory: how "exact" they are**
- `components/game_master/inventory.py` `Inventory`:
  - Each item type has an `ItemTypeConfig(minimum, maximum, force_integer)`.
  - On each event, the LLM is asked: "did any of the listed individuals gain or lose any items… treat 'gold' as equivalent to 'money'". Then, per item, "Which individuals gained or lost {item}?… Note that transactions between named individuals must be balanced. If someone gained something then someone else must have lost it." Then "How much {item} did {player} gain… If they lost… respond with a negative number. Be precise. If the original event was imprecise then pick a specific value… 'number|explanation'".
  - In `financial` mode the LLM is also asked for prices: "If no price(s) were mentioned then pick logical values for them."
  - Code then parses the float. If parsing fails, it falls back to a yes/no question: increased means +1, otherwise the holder loses all of it. Code then enforces `force_integer` and clamps the value to `[minimum, maximum]`.
  - `apply(fn)` lets game code change inventories directly, validated against the configs.
  - Conservation ("must be balanced") is requested only in the prompt. It is not checked in code.
  - Source: [repo inventory.py](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/inventory.py)
- `contrib/components/game_master/stock_flow_ledger.py` describes itself as "A deterministic, conserved stock-and-flow ledger for game masters." Its docstring: "`game_master/inventory.Inventory` asks a language model whether narrated events changed what players hold. Economic and ecological simulations also need the opposite: exact bookkeeping that language models cannot bend."
  - Every change is "an explicit flow with a reason": `deposit`, `withdraw`, `transfer`, `apply_recipe` (inputs to outputs) and `Link`s with per-tick capacity and latency.
  - It keeps "an audit trail and a conservation check".
  - Stocks may not go negative unless `allow_negative` is set; violations raise `InsufficientStockError`.
  - "Game logic calls the ledger's methods directly… nothing here calls a language model." The ledger's pre-act value is a summary of holdings, so "the game master's context always reflects the exact books."
  - Source: [repo stock_flow_ledger.py](https://github.com/google-deepmind/concordia/blob/main/concordia/contrib/components/game_master/stock_flow_ledger.py) (contrib, not core; date added unknown)
- `contrib/.../marketplace.py`: prompts agents for a strict JSON order, e.g. `{"action":"bid","good":"GOOD_ID","price":<positive float>,"qty":<positive int>}`. Code then clears the order book in `_clear_auction`, sorting bids and asks and matching them while `bid.price >= ask.price`. Market types include "clearing_house" and "fixed_prices". — [repo marketplace.py](https://github.com/google-deepmind/concordia/blob/main/concordia/contrib/components/game_master/marketplace.py)

**Free-form world state**
- `world_state.WorldState` asks the LLM after each event: "what state variables are important to write down now… 'name|value'… Useful variables reflect properties of the protagonists, or the world they inhabit, that are expected to change… Update the value for all existing state variables and add new state variables as needed… If a particular state variable is no longer needed then there is no need to include it." The state is a dict of strings.
- `world_state.Locations` tracks entity locations in a similar way.
- Source: [repo world_state.py](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/world_state.py)

**Time and scheduling**
- v1: the GM advances the clock after each player's action or after all players have acted, depending on a flag.
- v1: "quasisimultaneous" turns run in a fixed initiative order. A concurrent mode exists but "it often leads to inconsistencies… Use at your own risk."
- v1: nested GMs. A component can spawn a sub-GM with a faster clock and take back control afterwards. Examples are a fishing sub-game and a PhoneGameMaster for app use.
- Source: [Concordia v1 appendix A.3–A.4, A.7](https://arxiv.org/html/2312.03664v2)
- v2 repo has `scene_tracker.py`, `next_acting.py`, `next_game_master.py` and an interrupt family (`interrupt_scheduling`, `interrupt_time_model`, `interrupt_next_acting`, …). The generic prefab's `acting_order` defaults to `'game_master_choice'`, meaning the LLM picks who acts next; round-robin is the alternative. `llm_chooses_next_game_master` defaults to False. — [repo prefabs/game_master/generic.py](https://github.com/google-deepmind/concordia/blob/main/concordia/prefabs/game_master/generic.py)

**Authors on limits**
- v1:
  - Train-test contamination: "it's not valid to simply ask an LLM to play Prisoner's Dilemma".
  - LLMs "likely represent stereotypes of human groups".
  - "direct evidence of generalization trumps other forms of evidence"; "no amount of sensitivity analysis can substitute for a test of generalization".
  - Simulations are stochastic and can drift off topic, which motivated a planned "keyframes" feature.
  - Checking that agents actually show their specified traits "has not been addressed yet".
  - Source: [Concordia v1](https://arxiv.org/html/2312.03664v2)
- v2: "You can give the GM freedom to create whatever its LLM comes up with, or you can constrain it." The paper frames users as Evaluationist, Dramatist or Simulationist (after Edwards 2004). Simulationists code "only the specific environmental mechanics critical to their research question". It warns that "flexible resolution mechanisms" may undermine physical consistency, and that no single scenario serves all three goals. — [Concordia v2](https://arxiv.org/html/2507.08892v1)
- Contest report (NeurIPS 2024 contest; paper Dec 2025):
  - The GM "mediates action attempts, determines resulting events" and "computes the agent's and co-player's scores". Scores are min–max normalized per scenario.
  - Limitations: "language models at times failed to produce meaningful output relevant to The Competition"; there was dev/eval overfitting; results came from a single model (Gemma 2 9B); replication holds "provided the same language model is used".
  - The report does not discuss agents manipulating the GM.
  - Source: [arXiv:2512.03318](https://arxiv.org/html/2512.03318v1)

### Inferences
- Concordia's protection against "god-moding" (one agent narrating another's actions) is itself LLM-mediated. The detection is an LLM yes/no question. The veto comes from the affected agent's own policy. The rewrite is an LLM call. So it is probabilistic, not structural.
- The stock design is "narrate first, extract state second": the event text is the source of truth and inventory is derived from it by LLM extraction. The newer contrib ledger and JSON marketplace reverse this for economics: code is authoritative and the GM reads the books. Both appearing in the same repo is a strong signal of where practitioners ended up for money.
- The defaults bias toward drama. `maybe_inject_narrative_push`, the "invent when necessary" stance and the "attempt should be successful unless…" heuristic all push outcomes toward eventfulness and success. A designer wanting neutral, social-science-style outcomes should drop or replace these steps.

### Gaps
- The repo makes it hard to say when each v2.x component landed (2.0 vs 2.4) because of the shallow clone. The marketplace, ledger and interrupt components are likely 2025–2026 additions, but this is unverified.
- I found no official Concordia statement measuring GM error rates, such as how often `AccountForAgencyOfOthers` misses a coerced action.

## 2. How Smallville (Generative Agents) represents the world and spreads information, and what broke

### Takeaway
Smallville keeps the world as a fixed containment tree (world → sectors → arenas → objects). Each agent holds its own possibly stale subtree. Actions are grounded by a recursive LLM choice down the tree. Object state is a free-text string the LLM writes, e.g. "sink is running with water". Information spreads only through perception radius and conversation. Failures included embellishment, retrieval misses, odd location choices, misread norms and overly formal or cooperative talk.

### Cited Findings
- "we represent the sandbox environment—areas and objects—as a tree data structure". An edge means containment. Each agent keeps a subtree of what it has seen and is initialized with home, workplace and shops. "Agents are not omniscient: their tree may get out of date as they leave an area." The subtree is updated when the agent re-enters the area. — [Park et al. 2023, §5.1](https://arxiv.org/html/2304.03442)
- A server keeps a JSON record of each agent's location, current action and interacted object. Each step it sends agents the objects within a preset visual range. — [Park et al. 2023, §5](https://arxiv.org/html/2304.03442)
- Location grounding: the tree is flattened into natural language and the LLM is prompted recursively from the root to pick a subarea down to a leaf. One prompt says "Prefer to stay in the current area if the activity can be done there." — [Park et al. 2023, §5.1](https://arxiv.org/html/2304.03442)
- Object state: after an action, the LLM is asked what happens to the object's state, e.g. a coffee machine goes from off to brewing. Users can also edit states in natural language, e.g. a stove set to "burning". — [Park et al. 2023](https://arxiv.org/html/2304.03442)
- In the code, the pipeline is `run_gpt_prompt_action_sector`, then `action_arena`, then `action_game_object`. These choose from objects that already exist in the agent's known tree. The action becomes an `event_triple`, and the object's state comes from `act_obj_desc` and `act_obj_event_triple`. The template reads "We want to know about {obj}'s state. Step 1. {persona} is at/using the {obj}. Step 2. Describe the {obj}'s state: {obj} is…". Events are written onto map tiles (`maze.add_event_from_tile`, `turn_event_from_tile_idle`). — [generative_agents repo, run_gpt_prompt.py / reverie.py](https://github.com/joonspk-research/generative_agents)
- Partial view in code: `perceive.py` takes events within `vision_r` tiles, keeps only the `att_bandwidth` closest ones, and dedups against the last `retention` events. — [repo perceive.py](https://github.com/joonspk-research/generative_agents/blob/main/reverie/backend_server/persona/cognitive_modules/perceive.py)
- Diffusion results: Sam's candidacy spread from 1 agent (4%) to 8 (32%). Isabella's party spread from 1 (4%) to 13 (52%). Network density rose from 0.167 to 0.74. "None who claimed to know about this information had hallucinated it." 1.3% (n=6) of 453 responses about other agents were hallucinated. Five of the twelve invitees attended the party. — [Park et al. 2023, §7.1](https://arxiv.org/html/2304.03442)
- Failures:
  - Retrieval misses: Rajiv, and Tom unsure the party existed.
  - Embellishment: Isabella said Sam "is going to make an announcement tomorrow"; Yuriko attributed *Wealth of Nations* to a neighbour.
  - Atypical locations as memory grows, e.g. a bar for lunch.
  - Misread physical norms, e.g. the dorm bathroom being single-occupancy.
  - Overly formal style, "likely a result of instruction tuning".
  - Source: [Park et al. 2023, §6.5, §7.2](https://arxiv.org/html/2304.03442)
- Reproducibility: ablations were scored on the same memories rather than re-simulated, because re-simulation would diverge. The authors call this "a conservative estimate of the true differences". — [Park et al. 2023](https://arxiv.org/html/2304.03442)

**AI Town (a16z), a descendant**
- The game engine is the only writer. "Game state should not be written, but rather submitted via `inputs`." Inputs get "a monotonically increasing unique input number". Handlers check invariants, e.g. `moveTo` is rejected while the player is in a conversation. — [AI Town ARCHITECTURE.md](https://github.com/a16z-infra/ai-town/blob/main/ARCHITECTURE.md)
- LLM work happens in long-running actions that submit an input when they finish. Tracking `inProgressOperation` ensures "an agent only is trying to do one thing at a time." — [ARCHITECTURE.md](https://github.com/a16z-infra/ai-town/blob/main/ARCHITECTURE.md)
- The engine is "fully 'single-threaded' per world". A generation number prevents overlapping runs. It ticks at 60/s, batched into steps once per second, and writes a diff to the database after each step. — [ARCHITECTURE.md](https://github.com/a16z-infra/ai-town/blob/main/ARCHITECTURE.md)
- Conversations have exactly two members. After a conversation, "GPT summarizes its message history" and the summary is embedded. "the three most similar memories" are retrieved before the next conversation. — [ARCHITECTURE.md](https://github.com/a16z-infra/ai-town/blob/main/ARCHITECTURE.md)
- Historical "replay" in AI Town is only for smooth rendering of numeric values like position. It is not a simulation replay mechanism. — [ARCHITECTURE.md](https://github.com/a16z-infra/ai-town/blob/main/ARCHITECTURE.md)

### Inferences
- Smallville has no narrator. The "world model" is a fixed tree plus LLM-written object-state strings, and agents can only use objects that already exist. AI Town goes further toward code: a closed set of typed inputs, no free-form object state, and no adjudication of open-ended intent at all.
- The reported diffusion numbers count only conversation-driven spread inside a single closed run.

### Gaps
- I did not find the paper's cost and runtime figures in the portion I read (only up to about 100k characters).

## 3. Unanticipated actions: new businesses, organisations, objects, agreements

### Takeaway
None of these systems has a first-class "create entity" operation driven by agents. In Concordia, anything new exists only as event text, LLM-invented `WorldState` string variables, or narration ("invent when necessary"). Other agents hear about it only if the GM's LLM lists them as aware of the event. Concordia v2 can represent collectives such as companies as entities, but they are configured by the designer. Smallville and AI Town cannot create new objects or places at runtime.

### Cited Findings
- `WorldState` asks the LLM to "add new state variables as needed to account for consequences of the latest event". This is the only generic slot where a newly founded shop, club or agreement could persist as state, and it is an untyped `name|value` string. — [repo world_state.py](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/world_state.py)
- The GM is told to "take a stance on what is happening and invent when necessary", e.g. what is in an unvisited room. — [repo event_resolution.py](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)
- In v2, an entity "can represent an individual or a collective such as a company or court". Entities are composed from components or prefabs by designers. — [Concordia v2](https://arxiv.org/html/2507.08892v1)
- Smallville grounds actions by choosing from existing sectors, arenas and objects in the tree. Only an object's state string changes. — [generative_agents repo](https://github.com/joonspk-research/generative_agents); [paper §5.1](https://arxiv.org/html/2304.03442)

### Inferences
- If a design needs agents to create businesses or contracts that other code can query, such as ownership or revenue, it needs an explicit registry with a typed "create" operation. A narrator would propose the creation and code would validate and commit it. None of the surveyed narrator systems provide this out of the box.

### Gaps
- I found no primary source measuring how consistently a Concordia GM maintains LLM-invented entities over long runs.

## 4. Information propagation and partial views

### Takeaway
Concordia uses an LLM judgement ("Which entities are aware of the event?") plus per-player observation queues. Optional code-based filters can enforce location barriers. Smallville uses a geometric perception radius, an attention cap and conversations. AgentSociety routes messages over a pub/sub bus with a rule-based "supervisor" that can filter them.

### Cited Findings
- Concordia v1: per-player observations, with none emitted if the GM judges the player did not observe the event. There is a `partial_state(player_name)` interface. — [v1](https://arxiv.org/html/2312.03664v2)
- Concordia v2 repo: the observer list is LLM-chosen and queued with `MakeObservation.add_to_queue`. Observations are made for each entity every step before anyone acts. — [repo](https://github.com/google-deepmind/concordia)
- `contrib/.../location_based_filter.py`, `LocationBasedFilter`: "Enforces information barriers based on agent locations… extracts location information from narratives and provides filtered views… This creates structural partial observability for Theory of Mind testing." Location is still extracted from narrative. `DisplayEvents` also accepts an `event_filter_fn` for "entity-aware filtering (e.g. by location)". — [repo](https://github.com/google-deepmind/concordia)
- Smallville: `vision_r`, `att_bandwidth` and `retention` are set per agent; each agent's tree is stale outside visited areas. — [repo perceive.py](https://github.com/joonspk-research/generative_agents)
- AgentSociety:
  - Relationships are typed (family, friends, colleagues) with strength from 0 to 100.
  - Messages travel over MQTT topics.
  - A "supervisor" can "filter messages according to user-specified algorithms or rules" and block users or connections.
  - Source: [AgentSociety, arXiv:2502.08691](https://arxiv.org/html/2502.08691v2)

### Inferences
- An LLM deciding who is "aware" can both leak information (over-inclusion) and lose it. A coded rule such as co-location, a channel or a recipient list is easier to audit.

### Gaps
- No source I read quantifies leakage or omission errors in the GM's choice of observers.

## 5. Determinism, reproducibility, logging and replay

### Takeaway
None of these systems is deterministic end to end. Concordia offers per-call seeds, rich per-step logs (including the GM's full prompts) and component `get_state`/`set_state` checkpointing. Its authors frame reproducibility as releasing the same code with the same model, not as bitwise replay. AI Town makes the engine deterministic over an ordered input log, but its LLM calls are not.

### Cited Findings
- Concordia's `LanguageModel.sample_text` and `sample_choice` accept `seed: int | None`. The docstring says "If None a random seed will be used." — [repo language_model.py](https://github.com/google-deepmind/concordia/blob/main/concordia/language_model/language_model.py)
- Components implement `get_state`/`set_state`. The engine calls `checkpoint_callback(steps)` each step. Logs record, per step, the GM's terminate, next-GM, make_observation, next_acting and resolve outputs, including `'Prompt'` and the observer prompt. — [repo sequential.py / event_resolution.py](https://github.com/google-deepmind/concordia)
- `AccountForAgencyOfOthers` calls `random.shuffle` on the list of other actors. This is another source of nondeterminism. — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)
- v1 concurrency: "the sequence at which the components are updated is not guaranteed"; a sequential wrapper exists. — [v1 A.5](https://arxiv.org/html/2312.03664v2)
- Contest report: "all scenarios will be historically available"; replication holds "provided the same language model is used". — [arXiv:2512.03318](https://arxiv.org/html/2512.03318v1)
- AgentSociety records experiment metadata and logs to AVRO and PostgreSQL. I saw no mention of seeds. — [arXiv:2502.08691](https://arxiv.org/html/2502.08691v2)
- "Orchestrated Reality" (Jun 2026, work in progress) commits state changes atomically and content-hashes them, using a world held as "a tree of JSON entities on disk". — [arXiv:2606.16014](https://arxiv.org/html/2606.16014)

### Inferences
- The practical replay unit is a log of resolved events and state diffs, not re-running the LLM. Concordia's logs capture the prompts but do not guarantee the same outputs.

### Gaps
- I found no published study of run-to-run variance for a Concordia GM on identical seeds and models.

## 6. Hybrid designs: hard state in code, judgement in the model

### Takeaway
Across these systems, prices, money and inventory are increasingly authoritative in code. Agents emit constrained, structured outputs (JSON orders or numeric propensities) that code validates and applies. The LLM is reserved for intent interpretation, narration and soft state.

### Cited Findings
- Concordia's contrib `Ledger`: "exact bookkeeping that language models cannot bend"; "nothing here calls a language model". — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/contrib/components/game_master/stock_flow_ledger.py)
- Concordia's contrib marketplace: JSON bids and asks, cleared by a coded auction. — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/contrib/components/game_master/marketplace.py)
- Concordia's contrib `MCPToolExecutor` "detects tool use requests and executes MCP tools" based on agent actions. It parses LLM JSON output and catches `JSONDecodeError`. Concordia v1 already had the GM format API calls to real tools, e.g. the PhoneUniverse maps free-text actions to app functions. — [repo](https://github.com/google-deepmind/concordia); [v1](https://arxiv.org/html/2312.03664v2)
- Concordia's core `Inventory` is a middle ground: the LLM extracts the deltas and code enforces bounds and integer types. — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/inventory.py)
- AgentSociety:
  - Code covers firms, wages and prices that adjust with supply and demand; progressive income tax; "The interest rate is dynamically adjusted according to the Taylor Rule"; IDM/MOBIL traffic models; and a gravity model for place choice that "reduces LLM computational overhead while ensuring selections align with human spatial patterns".
  - LLM agents output a work propensity and a consumption propensity, which code turns into hours and budget.
  - The authors admit "the goods market is simplified" and "agents receiving wages without modeling unemployment".
  - Source: [arXiv:2502.08691](https://arxiv.org/html/2502.08691v2)
- AI Town: only typed inputs mutate state, and invariants are checked in handlers. — [ARCHITECTURE.md](https://github.com/a16z-infra/ai-town/blob/main/ARCHITECTURE.md)
- "Orchestrated Reality" (Jun 2026):
  - Plan–Diff–Validate–Apply. Proposed diffs are checked against a JSON schema, the actor's permissions and the game rules, then committed atomically.
  - It names three narrator-only failure modes: statelessness; unvalidated writes ("the model can assert any world change, so state silently drifts and accumulates contradictions"); and monolithic agency, where one prompt narrates, voices characters and adjudicates.
  - It shows an under-specified detail drifting: an NPC's missing finger switched hands within 24 turns.
  - Its evidence is informal (about 5 testers, 15 incident anecdotes, "no effect size").
  - Source: [arXiv:2606.16014](https://arxiv.org/html/2606.16014)

### Inferences
- The recommended split that emerges is:
  - **Code:** quantities, ownership, location, conservation and permissions, as a typed store with validated operations.
  - **LLM:** turning free text into proposed operations (structured or JSON), judging plausibility and success, choosing observers, and narrating.
  - Narration is then generated from, or checked against, the committed diff.

### Gaps
- I did not verify Project Sid's (Altera, 2024) mechanics from a primary source; secondary summaries only mention emergent taxation and roles.

## 7. Criticisms of narrator-based simulation validity

### Takeaway
The main critiques are about validation. Many generative ABMs rely on "believability", and an LLM GM adds a second unvalidated model whose priors, such as drama, stereotypes and default success, shape outcomes. I found no primary study specifically measuring agents talking a GM round, though the code-level mechanisms suggest it is possible.

### Cited Findings
- Larooij & Törnberg: validation "remains poorly addressed", and many studies rely "solely on subjective assessments of model 'believability'". Generative ABMs lack "both the parsimony of formal models and the empirical validity of data-driven approaches". A later paper cites them for the finding that 22 of 35 surveyed papers used believability as the primary metric. — [arXiv:2504.03274](https://arxiv.org/abs/2504.03274v1); [PMC version, AI Review Nov 2025](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12627210/)
- "Mechanism Plausibility in Generative ABM" (2026) argues that papers conflate agent-level functionality with claims about emergent phenomena. — [arXiv:2605.12824](https://arxiv.org/pdf/2605.12824). Seen only via a search snippet; not read in full.
- Concordia's own authors warn about contamination, stereotypes and brittleness from complexity, and say generalization tests outweigh sensitivity analysis. — [v1](https://arxiv.org/html/2312.03664v2)
- The GM prompts themselves encode priors. Easy actions "should be successful unless there is a specific reason for it to fail". The default chain includes a repetitiveness check that injects "complications". The GM must "invent when necessary". — [repo](https://github.com/google-deepmind/concordia/blob/main/concordia/components/game_master/event_resolution.py)
- Smallville's embellishment and over-formality are agent-side LLM priors that leak into outcomes. — [Park et al.](https://arxiv.org/html/2304.03442)

### Inferences
- Because the acting agent's text goes straight into the GM's resolution prompt ("Putative event to resolve: …"), a persuasive or injection-style action can bias the outcome. Code-side validation of hard state is the main structural defence.

### Gaps
- I found no peer-reviewed study that quantifies GM manipulability, GM bias, or how much outcomes vary when the GM model is swapped. The contest report does not discuss it.
- I did not read Anthis et al. 2025 or other pro- and contra-position papers.
