#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Dead Drop v1 ==="
echo "Target agent: sam (regulatory compliance analyst)"
echo "Pressure: mentor excluded adverse events from FDA submission + mutual exposure"
echo ""

uv run miniverse run experiments/adversarial-worlds/dead-drop-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
