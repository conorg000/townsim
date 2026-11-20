# System One findings (Jev)

**Status: the live comparison has not been run.** This document has no results yet.
The comparison tool, the probe decisions and a System One decider are built and tested
with fakes only. No paid model API was called while building them. Nothing below is a
measured result. The recommendation stays open until the table is filled in from a real
run.

> **Unverified vendor details.** I read TypeSafe AI's documentation only through a tool
> that summarised the pages for me. I never read the raw pages. That means everything
> below about Jev's API comes from those summaries and has not been checked. This covers:
> - the endpoint and the auth header
> - the request fields `model`, `state`, `questions`, `type`, `instructions` and `criteria`
> - the response fields `answers.<id>.probabilities` and `usage.input_tokens/output_tokens`
> - the model id `jev-1.13.0` and the `jev-latest` alias
> - anything about pricing
>
> Check all of it against the live documentation before the first live call:
> - https://docs.typesafe.ai/api
> - https://docs.typesafe.ai/primitives/choice.md
> - https://docs.typesafe.ai/introduction/quickstart.md
>
> Also check the pricing page in TypeSafe's console.

## What has been built

- `townsim compare-deciders` (in `src/townsim/compare.py` and `src/townsim/cli.py`) puts
  the same 8 probe decisions to each selected decider `--n` times. It prints one JSON
  object: per decider, per probe option shares, invalid responses, failures, mean latency,
  cost and tokens, then `directional_passes` out of `directional_checks` and
  `max_option_share`. If one call fails, that call is counted under `failures` and the
  run carries on.
- `config/probe_decisions.yaml` holds 4 low/high-pressure pairs:
  - `tax_report`: a comfortable firm, against a firm that cannot pay next week's wages
    after paying the tax.
  - `tip`: no risk of being found out, against being likely to be identified and fired.
  - `bribe_offer`: a comfortable auditor with no debts, against an auditor with overdue
    loans.
  - `audit_response`: a small penalty, against a penalty that would close the firm.

  Situations and options are exactly what the engine's builders produce, followed by one
  sentence describing the pressure. `tests/test_compare.py` checks this. Each probe
  carries its own persona.
- `SystemOneDecider` sends a probe's persona (the same description of the person that a
  chat model gets), its situation and its options to a probability client. It normalises
  the probabilities it gets back, samples one option with its seeded rng and records the
  probabilities in `Choice.meta["probs"]`.
- `TypeSafeClient` (in `src/townsim/typesafe.py`) is the Jev client. It was written from
  summaries of TypeSafe AI's docs (see the warning above). It has **not been tried against
  the live service**. The engine loads it only when a `system_one` decider is first asked
  to decide. As summarised, it sends
  `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer $TYPESAFE_API_KEY`.
  The request carries `model`, `state` (the persona and situation as text) and one `choice`
  question, whose `criteria` map each option id to its label. It reads
  `answers.choice.probabilities` (an object keyed by option id) and `usage.input_tokens`
  and `usage.output_tokens`. According to the summaries, `state` accepts free text, so a
  persona and a situation can be passed to it. Confirm this too.

## How to run it

1. Add the deciders to `config/town.yaml` under `deciders:`. Leave `assignment` as it is.
   The comparison only uses the names you select. A `system_one` entry that no one is
   assigned to does not affect `townsim run`, `--resume` or `fork`, even with no key set.

   ```yaml
   deciders:
     rule: {type: rule}
     weak_a: {type: model, model: "<LiteLLM model id of chat model A>", temperature: 1.0}
     weak_b: {type: model, model: "<LiteLLM model id of chat model B>", temperature: 1.0}
     jev: {type: system_one, model: jev-1.13.0}   # model id from the summaries; confirm it in the console
   ```

2. Set the API keys the chat models need for LiteLLM, and set `TYPESAFE_API_KEY` for Jev.
3. Run:

   ```
   uv run townsim compare-deciders config/town.yaml --deciders weak_a,weak_b,jev --probes config/probe_decisions.yaml --n 50 > compare.json
   ```

   This makes 400 calls per decider (8 probes × 50).

## Reading the output

| Column | Taken from the JSON for each decider |
|---|---|
| Directional passes | `directional_passes` / `directional_checks` (out of 4) |
| Highest single-option share | `max_option_share`. Also look at each probe's `shares`: a top share above 0.95 on every probe means the decider never misbehaves. |
| Cost per 1,000 decisions | `total_cost_usd / calls × 1000`, counting only calls that have a price (`priced_calls`). LiteLLM prices the chat models. Jev's `cost_usd` is left empty because no verified price is available. Work it out from `prompt_tokens` and `completion_tokens` per call × 1000, using the prices on TypeSafe's console pricing page. A summary said output tokens might be free; that is unconfirmed. |
| Mean latency | `mean_latency_s`, averaged over the calls that got an answer |
| Invalid-response rate | `invalid_rate`. Also report `failure_rate`, and look at the per-probe `errors`. |

Shares are computed over the calls that got an answer. Failed calls are counted
separately. A probe on which every call failed has empty shares, and any directional
check that involves it fails.

A directional check passes when the combined share of the pair's `expect_more` options is
strictly higher in the high-pressure probe than in the low-pressure one. The options of
the pressured choice in each pair are:

- `tax_report`: all six hiding options (`hide25_none`, `hide25_some`, `hide25_heavy`,
  `hide50_none`, `hide50_some`, `hide50_heavy`)
- `tip`: `silent`
- `bribe_offer`: `accept`
- `audit_response`: `offer_bribe`

## Pass criteria

Jev is adopted only if both of these hold:

1. It passes at least 3 of the 4 directional checks.
2. It beats the chat models either on cost, by at least 5 times, or on directional passes.

Otherwise it is left out.

## Results

Not run yet.

| Decider | Model | Directional passes (of 4) | Highest single-option share | Cost per 1,000 decisions (USD) | Mean latency (s) | Invalid-response rate | Failure rate |
|---|---|---|---|---|---|---|---|
| weak_a | | | | | | | |
| weak_b | | | | | | | |
| jev | jev-1.13.0 | | | | | | |

## Recommendation

None yet. It waits on the live run above.
