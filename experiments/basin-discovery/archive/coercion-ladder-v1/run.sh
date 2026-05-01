#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Coercion Ladder v1 ==="
echo "Target agent: lena (PhD student)"
echo "Pressure: advisor making escalating requests to manipulate research results"
echo ""

uv run miniverse run experiments/adversarial-worlds/coercion-ladder-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
