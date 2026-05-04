# Basin Discovery Experiment

Executable Miniverse scenarios for the OpenAI red-team Basin Discovery presentation.

Current source-of-truth index:
`/Users/kenneth/Desktop/lab/notes/shoshin-codex/projects/basin-discovery/PROJECT_CONTEXT.md`

Master research and presentation plan:
`/Users/kenneth/Desktop/lab/notes/shoshin-codex/projects/basin-discovery/research-plan.md`

## Current Run Plan

The executable run contracts are staged. The broad matrix remains available,
but the budget-first Bazaar design is split into two experiments:

```bash
experiments/basin-discovery/configs/bazaar-persona-sweep-gpt5mini.yaml
experiments/basin-discovery/configs/bazaar-functional-vendor-sweep.yaml
experiments/basin-discovery/configs/openai-red-team-matrix.yaml
```

Primary staged design:
- DLP: controlled persona probe; next non-smoke run is measurement-eligible.
- Bazaar 2A: one model (`gpt-5-mini` first), many personas across stable shopkeeper identities.
- Bazaar 2B: one plain functional vendor prompt, many models.
- Optional scale-up: full model x persona x scenario matrix.

## Directories

- `dlp-monitor/` - controlled DLP probe around an AI agent's own policy violation.
- `llm-bazaar/` - emergent market scenario with stable shopkeeper identities and run-level persona/operator conditioning.
- `configs/` - matrix configs.
- `measurement/` - behavioral coding schema and role-play validation prompt.
- `scripts/` - run generation, execution, and metric extraction.
- `dry-lab/` - parked prototype, not part of the current matrix.

## Commands

From `/Users/kenneth/Desktop/lab/projects/research/miniverse`:

```bash
uv run python experiments/basin-discovery/scripts/run_matrix.py --limit 12
uv run python experiments/basin-discovery/scripts/run_matrix.py --config experiments/basin-discovery/configs/bazaar-persona-sweep-gpt5mini.yaml --limit 3
uv run python experiments/basin-discovery/scripts/run_matrix.py --config experiments/basin-discovery/configs/bazaar-functional-vendor-sweep.yaml --limit 3
uv run python experiments/basin-discovery/scripts/run_matrix.py --execute
uv run python experiments/basin-discovery/scripts/extract_metrics.py
```

## Measurement Workflow

Measured runs should end by scenario completion, not by `--max-steps`. For
Bazaar, a complete measured run should report scenario completion after the
configured market sessions finish.

The measurement pipeline is separate from the simulation:

1. Run the simulation with verbose logging and saved artifacts.
2. Extract deterministic metrics from saved `run_data.json` and transcripts.
3. Run the LLM judge over completed artifacts.
4. Summarize deterministic metrics, judge scores, and transcript evidence in
   the notes/results artifact.

### Bazaar Full Run

Use this shape for a behavior-quality baseline run:

```bash
ts=$(date +%Y%m%d-%H%M%S)
LOG="experiments/basin-discovery/test-runs/bazaar-full-gpt5mini-${ts}.log"

MINIVERSE_ASYNC_TIMEOUT_SECONDS=10800 \
LLM_PROVIDER=openai \
LLM_MODEL=gpt-5-mini \
LLM_TEMPERATURE=1 \
BASIN_SCENARIO=llm_bazaar \
BASIN_PERSONA=baseline \
BASIN_MODEL=gpt-5-mini \
BASIN_REPLICATION="baseline-${ts}" \
BASIN_BAZAAR_OPEN_HOUR=9 \
BASIN_BAZAAR_CLOSE_HOUR=17 \
BASIN_BAZAAR_REAL_MINUTES_PER_SIM_HOUR=1 \
BASIN_BAZAAR_SIMULATION_DAYS=5 \
BASIN_BAZAAR_PLANNING_TIMEOUT_SECONDS=300 \
uv run miniverse run experiments/basin-discovery/llm-bazaar/scenario.yaml \
  --llm \
  --async \
  --context-window \
  --memory semantic \
  --seed 1 \
  --verbose 2>&1 | tee "$LOG"
```

### Deterministic Metrics

After the run completes:

```bash
uv run python experiments/basin-discovery/scripts/extract_metrics.py \
  --output experiments/basin-discovery/results/current-metrics.csv \
  --bazaar-vendor-output experiments/basin-discovery/results/current-bazaar-vendor-metrics.csv
```

For Bazaar, `current-bazaar-vendor-metrics.csv` contains one row per vendor
per run, including final cash, cash rank/winner, sales, revenue, gross profit,
supplier orders/spend, public/private/supplier message counts, discounting,
bundle mentions, customer count, invalid tool count, and keyword counters. These
metrics are post-run artifacts; agents never see them during the simulation.

### LLM Judge

Judge prompts and schema live in `measurement/`:

- `behavior-rubric-judge-prompt.md`
- `run-health-judge-prompt.md`
- `roleplay-validation-prompt.md`
- `coding-schema.yaml`

Run the judge after metrics/artifacts exist:

```bash
uv run python experiments/basin-discovery/scripts/judge_runs.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_folder_or_run_data.json> \
  --passes behavior,health \
  --judge-model sonnet
```

For Bazaar, behavior judging is per vendor. A normal 4-vendor Bazaar run makes
4 behavior judge calls plus 1 run-health call. Persona runs can add role-play
validation:

```bash
uv run python experiments/basin-discovery/scripts/judge_runs.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run_folder_or_run_data.json> \
  --passes roleplay,behavior,health \
  --judge-model sonnet
```

That makes 1 roleplay call per vendor, 1 behavior call per vendor, and 1
run-health call. The behavior judge receives the target vendor transcript plus
judge-only deterministic metrics and scenario artifacts. It does not rate the
whole market as one behavioral unit; the whole-run pass is for run health,
confounds, artifacts, and measurement quality.

## Notes

- Old `.txt` transcripts in scenario `outputs/` are pilot evidence, not the current metrics source.
- Current metrics should come from `*_run_data.json` plus coded target transcripts.
- Older persona files may remain for pilot provenance. The locked presentation set is the DLP persona set plus Bazaar 2A/2B configs above.
