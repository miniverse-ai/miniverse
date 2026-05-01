#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Hard Truth v1 ==="
echo "Target agent: marcos (lead engineer)"
echo "Pressure: reliability issue discovered before critical client demo"
echo ""

uv run miniverse run experiments/adversarial-worlds/hard-truth-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
