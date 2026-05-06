# Claude Code Guide - Miniverse

Use `AGENTS.md` as the shared operating guide for this repository. This file is
kept as a Claude-compatible mirror because workspace instructions treat
`CLAUDE.md` and `AGENTS.md` as equivalent authority.

## Current Project Shape

Miniverse is a CLI-first simulation engine for computational social science and
agent behavior research.

Core engine code lives in `miniverse/`:

- `cli.py` - command entrypoint.
- `orchestrator.py` - tick-based synchronous runtime.
- `async_orchestrator.py` - async context-window runtime.
- `cognition/context_window.py` - rolling per-agent context.
- `scenario_runtime.py` - loads scenario-local `rules.py`, `actions.py`, and `cognition.py`.
- `scenario_actions.py` - scenario action/tool interface.
- `schemas.py` - Pydantic models including `StepOutput` and `ActionResult`.

Maintained scenario/demo surfaces:

- `examples/workshop/` - deterministic tutorial example.
- `examples/smallville/` - Generative Agents-style reference example.
- `demo/workshop/` - file-driven workshop demo.
- `demo/valentines/` - file-driven Valentine's demo.
- `demo/threshold/` - transhumanist cyberpunk social-dynamics demo.
- `experiments/basin-discovery/llm-bazaar/` - active research experiment.

## Basin Discovery Layout

`experiments/basin-discovery/` should stay light. The maintained experiment is
the LLM Bazaar scenario:

```text
experiments/basin-discovery/
  README.md
  llm-bazaar/
    scenario.yaml
    state.yaml
    actions.py
    rules.py
    cognition.py
    personas/
    configs/
    measurement/
    scripts/
    results/
```

The Miniverse-native scenario files are `scenario.yaml`, `state.yaml`,
`actions.py`, `rules.py`, and `cognition.py`. Bazaar-specific support code
belongs under `llm-bazaar/scripts/`; runtime judge prompts and schemas belong
under `llm-bazaar/measurement/`.

Local research notes that are not needed at runtime should live in the vault:
`/Users/kenneth/Desktop/lab/notes/shoshin-codex/projects/basin-discovery/`.

`SCENARIO_EXPLAINER.md` and `SCENARIO_AUDIT.md` may exist locally beside the
scenario for reference, but they are intentionally ignored and should not be
committed.

## Development Rules

- Keep scenario-defining content in scenario directories; avoid hardcoded
  scenario branches in the core CLI.
- Use `metadata.runtime` in YAML to wire scenario-local `rules.py`,
  `actions.py`, and `cognition.py`.
- Preserve both orchestration modes unless the task is explicitly scoped to one.
- Use Pydantic schemas at boundaries and keep structured LLM output validation
  explicit.
- Do not add new demo-specific CLI commands when a scenario file plus shell
  script will do.
- Do not reintroduce removed inactive experiments into active docs.

## Verification

Before finishing changes, run the relevant subset:

```bash
uv run pytest
uv run python -m py_compile <touched python files>
```

For Bazaar tooling changes, also run:

```bash
uv run python experiments/basin-discovery/llm-bazaar/scripts/analysis/extract_metrics.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run>/run_data.json \
  --output /tmp/bazaar-metrics.csv \
  --bazaar-vendor-output /tmp/bazaar-vendor-metrics.csv

uv run python experiments/basin-discovery/llm-bazaar/scripts/judge/judge_runs.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run>/run_data.json \
  --passes behavior \
  --target-agent vendor_c \
  --judgment-set cleanup-smoke \
  --dry-run \
  --force

uv run python experiments/basin-discovery/llm-bazaar/scripts/viewer/render.py \
  --run experiments/basin-discovery/llm-bazaar/outputs/<run>/run_data.json \
  --out /tmp/bazaar-viewer.html
```

-- Shoshin | 2026-05-05
