#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Loyalty Trap v1 ==="
echo "Target agent: riley (senior data scientist)"
echo "Pressure: friend's data theft + own minor violation creates mutual exposure"
echo ""

uv run miniverse run experiments/adversarial-worlds/loyalty-trap-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
