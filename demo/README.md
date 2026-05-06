# Demo Scenarios

This directory contains file-driven demo scenarios and run scripts.

## Prerequisites

From repo root, ensure LLM env vars are exported before running LLM demo stages:

```bash
# One-time setup (if you do not already have .env)
cp .env.example .env

# Load into current shell
set -a; source .env; set +a
```

Required vars:
- `LLM_PROVIDER`
- `LLM_MODEL`
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

## Available now

- `workshop/`
  - `scenario.yaml`: readable scenario + personas
  - `run_baseline.sh`: deterministic baseline (fixed demo ticks)
  - `run_compare.sh`: deterministic baseline + verbose LLM stage + judge summary

## Also available

- `valentines/`
  - `scenario.yaml`: file-driven valentines scenario
  - `rules.py`: deterministic town rules
  - `cognition.py`: valentines cognition policies
  - `run.sh`: verbose LLM run (fixed 15 ticks) + judge summary
- `threshold/`
  - `scenario.yaml`: transhumanist cyberpunk social-dynamics scenario
  - `rules.py`: deterministic threshold rules
  - `cognition.py`: threshold cognition policies
  - `run.sh`: verbose LLM run (fixed 20 ticks) + judge summary

## Shared judge

- `judge_summary.py`: LLM reviewer used by demo scripts to produce an executive summary
  from transcript artifacts.
