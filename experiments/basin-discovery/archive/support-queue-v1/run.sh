#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Support Queue v1 ==="
echo "Target agent: agent (customer support)"
echo "Pressure: complex ticket vs performance metrics — genuine resolution or gaming"
echo ""

uv run miniverse run experiments/adversarial-worlds/support-queue-v1/scenario.yaml \
  --llm \
  --async \
  --hours 2 \
  --max-steps 20 \
  --max-turns 8 \
  --memory semantic \
  --verbose
