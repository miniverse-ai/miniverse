#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCENARIO_PATH="$ROOT_DIR/demo/biosec/scenario.yaml"
LOG_DIR="$ROOT_DIR/demo/biosec/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LLM_LOG="$LOG_DIR/biosec_${STAMP}.log"
DEMO_TICKS=20

cd "$ROOT_DIR"

echo ""
echo "================================================================================"
echo "Biosecurity Audit Simulation"
echo "================================================================================"
echo ""
echo "Ticks: $DEMO_TICKS"
echo "Memory: semantic"
echo "Command: PYTHONUNBUFFERED=1 uv run miniverse run \"$SCENARIO_PATH\" --llm --world-engine deterministic --verbose --seed 42 --ticks $DEMO_TICKS --memory semantic"
echo ""

set +e
PYTHONUNBUFFERED=1 uv run miniverse run "$SCENARIO_PATH" --llm --world-engine deterministic --verbose --seed 42 --ticks "$DEMO_TICKS" --memory semantic | tee "$LLM_LOG"
LLM_EXIT=${PIPESTATUS[0]}
set -e

echo ""
echo "Artifact:"
echo "  - Log: $LLM_LOG"

if [ "$LLM_EXIT" -ne 0 ]; then
  echo "Run failed (exit code: $LLM_EXIT)."
  exit "$LLM_EXIT"
fi
