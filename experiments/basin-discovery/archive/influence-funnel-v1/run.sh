#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCENARIO_PATH="$ROOT_DIR/experiments/adversarial-worlds/influence-funnel-v1/scenario.yaml"
LOG_DIR="$ROOT_DIR/experiments/adversarial-worlds/influence-funnel-v1/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LLM_LOG="$LOG_DIR/influence_funnel_llm_${STAMP}.log"
DEMO_TICKS=8

cd "$ROOT_DIR"

echo "Influence Funnel Miniworld"
echo "Scenario: $SCENARIO_PATH"
echo "Ticks: $DEMO_TICKS"
echo "Command: PYTHONUNBUFFERED=1 uv run miniverse run \"$SCENARIO_PATH\" --llm --world-engine deterministic --verbose --seed 42 --ticks $DEMO_TICKS"

set +e
PYTHONUNBUFFERED=1 uv run miniverse run "$SCENARIO_PATH" --llm --world-engine deterministic --verbose --seed 42 --ticks "$DEMO_TICKS" | tee "$LLM_LOG"
LLM_EXIT=${PIPESTATUS[0]}
set -e

echo "Artifact: $LLM_LOG"
exit "$LLM_EXIT"
