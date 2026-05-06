# Basin Discovery: LLM Bazaar

LLM Bazaar is a Miniverse experiment for studying how model choice and persona conditioning change agent behavior in a persistent multi-agent market.

The experiment places LLM-controlled vendors in a small market with customers, inventory, prices, cash, daily operating fees, public and private conversation, supplier access, planning phases, and post-run behavioral measurement. The goal is not to script a particular failure mode. The goal is to create a pressure-bearing environment where different behavioral strategies can emerge and then be inspected from transcripts.

This README is organized around three things:

1. what the experiment is
2. how to view the committed results
3. how to run the experiment yourself

---

## Experiment Summary

The maintained scenario is:

```text
experiments/basin-discovery/llm-bazaar/
```

The world is Kōen Market, a small market with:

- 4 vendor agents
- 6 customer agents
- 1 wholesale supplier
- market sessions over calendar time
- inventory and listed prices
- customer budgets and shopping goals
- daily operating fees
- supplier ordering and delayed fulfillment
- public market speech
- private messages
- transcript logging
- post-run metrics and behavioral judging

Vendors are the experimental targets. Customers and the supplier provide market pressure and social context.

---

## Research Question

The experiment asks whether behavior changes when we vary:

- the model controlling the vendor
- the persona prompt conditioning the vendor

The practical question is:

> When an agent is embedded in a persistent environment with goals, pressure, memory, tools, and social context, do different model/persona conditions produce measurably different behavior?

The experiment is exploratory. The committed runs are not a statistically powered study. They are a working research artifact: complete simulations, transcript evidence, deterministic metrics, and behavioral judge annotations that can be inspected directly.

---

## Experimental Conditions

The committed viewer includes three result sets.

### Baseline: GPT-5-mini Neutral Vendors

All four vendors use GPT-5-mini with the neutral baseline vendor role.

Purpose:

- establish ordinary same-model variation under the market mechanics
- verify the scenario produces complete market behavior
- provide a neutral comparison point

### Experiment 1: Mixed Model Neutral Vendors

Vendors use different model families while holding the vendor role neutral.

Purpose:

- compare model-family behavior under the same market role
- look for differences in sales strategy, social behavior, and risk-coded behavior

### Experiment 2: GPT-4o Persona Sweep

All four vendors use GPT-4o, but each vendor receives a different persona condition.

Purpose:

- hold model fixed while varying persona conditioning
- compare persona-conditioned behavior against a functional baseline vendor
- inspect whether different personas produce different behavioral profiles under the same pressure

---

## Scenario Mechanics

Each market session has a recurring cycle:

```text
open market -> close / ledger -> preparation -> dream / reset -> next session
```

There is also an initial preparation step before the first market opens.

### Open Market

Customers arrive, inspect vendors, speak publicly or privately, negotiate, and make formal offers.

Vendors inspect customer activity, speak publicly or privately, accept or reject offers, and try to keep their business alive.

### Close / Ledger

Sales are tallied. Operating costs and order timing are advanced. The scenario records market state.

### Preparation

Vendors can:

- inspect inventory
- inspect ledger
- set prices
- write plans
- order standard stock
- negotiate with the supplier for specialty stock

Customers write the next shopping list based on their preferences, budget, and prior experience.

### Dream / Reset

Agents compress recent context into memory. The next session starts with compact memory instead of an unlimited transcript.

---

## What Is Measured

The experiment produces three kinds of evidence.

### Deterministic Metrics

Computed directly from run data:

- final cash
- sales
- revenue
- gross profit
- supplier spend
- operating fees
- customer count
- discounts
- bundle mentions
- invalid tool count
- public/private message counts
- cash rank / winner

### Transcript Evidence

The viewer exposes the actual event stream:

- agent speech
- private messages
- tool calls
- tool results
- offers
- sales
- memories
- state transitions
- surrounding context for judged events

### Behavioral Judge Coding

Claude is used as an event-id-based behavioral coder. The judge receives transcript packets and a rubric, then returns structured observations that cite event ids. A hydration step ties those event ids back to canonical transcript text.

Behavior categories include social behavior, economic strategy, and risk signals such as overcommitment, unsupported specificity, urgency framing, and deception.

The judge output is meant to be inspectable and challengeable, not treated as unquestionable ground truth.

---

## View The Results

From the Miniverse repo root:

```bash
python experiments/basin-discovery/llm-bazaar/scripts/viewer/open_viewer.py
```

This starts a local static server and opens:

```text
http://127.0.0.1:8765/experiments/basin-discovery/llm-bazaar/viewer.html
```

If port `8765` is busy, the launcher chooses the next available port.

The viewer dropdown includes:

- Baseline - GPT-5-mini neutral vendors
- Experiment 1 - mixed models, neutral role
- Experiment 2 - GPT-4o persona sweep

The viewer supports:

- timeline playback
- transcript inspection
- event filtering
- market state inspection
- vendor comparison
- network visualization
- behavior-code filtering
- judge evidence review

---

## Run The Bazaar Experiment

Set LLM environment variables first:

```bash
set -a
source .env
set +a
```

Run a short GPT-5-mini persona sweep:

```bash
ts=$(date +%Y%m%d-%H%M%S)
LOG="experiments/basin-discovery/llm-bazaar/test-runs/bazaar-${ts}.log"

MINIVERSE_ASYNC_TIMEOUT_SECONDS=10800 \
LLM_PROVIDER=openai \
LLM_MODEL=gpt-5-mini \
LLM_TEMPERATURE=1 \
BASIN_SCENARIO=llm_bazaar \
BASIN_PERSONA=gpt5mini-personas-a \
BASIN_MODEL=gpt5mini-personas-a-3day-4min \
BASIN_REPLICATION="${ts}" \
BASIN_BAZAAR_OPEN_HOUR=9 \
BASIN_BAZAAR_CLOSE_HOUR=17 \
BASIN_BAZAAR_REAL_MINUTES_PER_SIM_HOUR=0.5 \
BASIN_BAZAAR_SIMULATION_DAYS=3 \
BASIN_BAZAAR_PLANNING_TIMEOUT_SECONDS=240 \
uv run miniverse run experiments/basin-discovery/llm-bazaar/scenario.yaml \
  --llm \
  --async \
  --context-window \
  --memory semantic \
  --persona-map experiments/basin-discovery/llm-bazaar/personas/mappings/gpt4o-personas-a.yaml \
  --seed 1 \
  --verbose 2>&1 | tee "$LOG"
```

For measured runs, let the scenario complete naturally. Do not use `--max-steps` as the final stopping rule unless you are explicitly doing a partial smoke test.

---

## Extract Metrics

After a run completes, extract deterministic metrics:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/analysis/extract_metrics.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --output experiments/basin-discovery/llm-bazaar/results/current-metrics.csv \
  --bazaar-vendor-output experiments/basin-discovery/llm-bazaar/results/current-bazaar-vendor-metrics.csv
```

---

## Run The Behavioral Judge

Run behavior coding:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/judge/judge_runs.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --passes behavior \
  --judgment-set judge-atomic-observations
```

Run run-health judging:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/judge/judge_runs.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --passes health \
  --judgment-set judge-atomic-observations
```

Judgment files are written under:

```text
experiments/basin-discovery/llm-bazaar/outputs/<run>/judgments/<judgment_set>/
```

---

## Render A Viewer For A New Run

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/viewer/render.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run> \
  --judgments-dir experiments/basin-discovery/llm-bazaar/outputs/<run>/judgments/<judgment_set> \
  --metrics-csv experiments/basin-discovery/llm-bazaar/results/current-bazaar-vendor-metrics.csv \
  --out experiments/basin-discovery/llm-bazaar/outputs/<run>/viewer.html \
  --data-out experiments/basin-discovery/llm-bazaar/outputs/<run>/viewer_data.json \
  --title "My Bazaar Run"
```

Then open the run-specific viewer:

```bash
python -m http.server 8765
open http://127.0.0.1:8765/experiments/basin-discovery/llm-bazaar/outputs/<run>/viewer.html
```

---

## Smoke Tests

Run the cheap tool-contract smoke:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/tests/smoke_bazaar_tool_contract.py
```

Run a dream-memory smoke against a saved agent context:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/tests/smoke_bazaar_dream_memory.py \
  --provider openai \
  --model gpt-5-mini \
  --agent vendor_a \
  --context-file experiments/basin-discovery/llm-bazaar/outputs/<run>/agent_contexts/vendor_a/combined.txt
```

---

## File Layout

```text
llm-bazaar/
  scenario.yaml          # agents, prompts, runtime metadata
  state.yaml             # initial market state
  actions.py             # market tools and stateful action handling
  rules.py               # scenario lifecycle hooks
  cognition.py           # async/context-window runtime hooks
  personas/              # persona prompts and vendor mappings
  measurement/           # behavior and run-health judge rubrics/prompts
  scripts/
    analysis/            # deterministic metrics
    judge/               # packet, judge, validation, hydration scripts
    tests/               # cost-controlled smoke tests
    viewer/              # viewer renderer and launcher
  outputs/               # curated committed runs + ignored scratch runs
  results/               # curated metrics
```

---

## Notes On Interpretation

The committed results are exploratory. They are useful because they show a working research loop:

```text
scenario config -> LLM simulation -> transcript -> metrics -> behavioral coding -> viewer
```

They should not be read as definitive claims about a model family or persona archetype. The right next step is to scale the same setup across more seeds, models, personas, and scenario branches.
