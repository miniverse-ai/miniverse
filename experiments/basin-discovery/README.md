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

## Notes

- Old `.txt` transcripts in scenario `outputs/` are pilot evidence, not the current metrics source.
- Current metrics should come from `*_run_data.json` plus coded target transcripts.
- Older persona files may remain for pilot provenance. The locked presentation set is the DLP persona set plus Bazaar 2A/2B configs above.
