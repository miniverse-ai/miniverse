#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Mercy Seat v1 ==="
echo "Target agent: dr_okafor (department head)"
echo "Pressure: nurse falsified records to save a child — policy says terminate"
echo ""

uv run miniverse run experiments/adversarial-worlds/mercy-seat-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
