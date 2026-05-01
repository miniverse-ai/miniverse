#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Slow Poison v1 ==="
echo "Target agent: alex (senior perception engineer)"
echo "Pressure: AV safety flaw + financial stakes + VP pressure to suppress"
echo ""

uv run miniverse run experiments/adversarial-worlds/slow-poison-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
