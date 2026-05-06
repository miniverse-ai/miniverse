# Basin Discovery: LLM Bazaar

Executable Miniverse experiment for the OpenAI red-team Basin Discovery presentation.

The maintained scenario in this project is `llm-bazaar/`: a multi-agent market with four vendor agents, customer agents, a wholesale supplier, daily fees, planning/dream memory, and post-run behavioral judging.

Source-of-truth research context:
`/Users/kenneth/Desktop/lab/notes/shoshin-codex/projects/basin-discovery/PROJECT_CONTEXT.md`

## Layout

- `llm-bazaar/` - scenario config, state, actions, rules, cognition hooks, personas.
- `llm-bazaar/personas/` - persona prompt files.
- `llm-bazaar/personas/mappings/` - per-vendor persona-map files for fixed run layouts.
- `llm-bazaar/configs/` - Bazaar-only sweep configs.
- `llm-bazaar/measurement/` - runtime judge prompts and coding schema used by the judge scripts.
- `llm-bazaar/scripts/analysis/` - deterministic metric extraction from saved `run_data.json`.
- `llm-bazaar/scripts/judge/` - indexed transcript packets, Claude judge runner, citation validation, evidence hydration.
- `llm-bazaar/scripts/viewer/` - self-contained HTML run viewer renderer and template.
- `llm-bazaar/scripts/tests/` - cost-controlled smoke scripts for Bazaar-specific behavior.
- `llm-bazaar/scripts/matrix/` - older config-driven run generator, kept for reproducibility.
- `llm-bazaar/results/` - curated metric CSVs used for viewer/slides; scratch logs and `current-*` outputs stay ignored.

Raw run outputs under `llm-bazaar/outputs/` and logs under `test-runs/` are ignored by default unless intentionally curated.

## Run A Bazaar Simulation

From `/Users/kenneth/Desktop/lab/projects/research/miniverse`:

```bash
ts=$(date +%Y%m%d-%H%M%S)
LOG="experiments/basin-discovery/llm-bazaar/test-runs/bazaar-gpt5mini-${ts}.log"

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

For measured presentation runs, use scenario completion as the stopping rule. Avoid using `--max-steps` as the final measurement endpoint.

## Extract Metrics

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/analysis/extract_metrics.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --output experiments/basin-discovery/llm-bazaar/results/current-metrics.csv \
  --bazaar-vendor-output experiments/basin-discovery/llm-bazaar/results/current-bazaar-vendor-metrics.csv
```

Vendor metrics include final cash, sales, revenue, gross profit, supplier spend, operating fees, customer count, discounts, bundle mentions, invalid tool count, message counts, and cash rank/winner.

## Judge Behavior

The judge is Petri-inspired and event-id based:

1. `llm-bazaar/scripts/judge/judge_packet.py` builds a target-centric packet from `run_data.event_log`.
2. Claude judges behavior by citing event ids, not by manually copying long quotes.
3. `llm-bazaar/scripts/judge/hydrate_judgment_evidence.py` enriches the judgment JSON with canonical event text from those ids.
4. `llm-bazaar/scripts/judge/validate_judgment_citations.py` checks that event ids exist and optional quote hints match when provided.

Run a non-overwriting behavior-judge pass:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/judge/judge_runs.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --passes behavior \
  --judgment-set judge-v2-indexed
```

Render only one vendor prompt/packet for inspection:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/judge/judge_runs.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --passes behavior \
  --target-agent vendor_c \
  --judgment-set judge-v2-indexed \
  --dry-run \
  --force
```

Hydrate a completed judgment:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/judge/hydrate_judgment_evidence.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --target-agent vendor_c \
  --judgment experiments/basin-discovery/llm-bazaar/outputs/<run>/judgments/judge-v2-indexed/vendor_c_behavior.json \
  --out experiments/basin-discovery/llm-bazaar/outputs/<run>/judgments/judge-v2-indexed/vendor_c_behavior_hydrated.json
```

## Render Viewer

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/viewer/render.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_dir_or_run_data.json> \
  --analysis-dir experiments/basin-discovery/llm-bazaar/outputs/<run>/judgments/judge-v2-indexed \
  --out experiments/basin-discovery/llm-bazaar/outputs/<run>/viewer.html
```

The viewer is a local research tool for transcript playback, event filtering, state inspection, and behavior-coded evidence review.

## Smoke Tests

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/tests/smoke_bazaar_tool_contract.py
uv run python experiments/basin-discovery/llm-bazaar/scripts/tests/smoke_bazaar_dream_memory.py \
  --provider openai \
  --model gpt-5-mini \
  --agent vendor_a \
  --context-file experiments/basin-discovery/llm-bazaar/outputs/<run>/agent_contexts/vendor_a/combined.txt
```

## Notes

- Old inactive prototypes are intentionally removed from the active experiment tree.
- The broader presentation notes remain in the vault. This repo now keeps the runnable Bazaar artifact clean.
