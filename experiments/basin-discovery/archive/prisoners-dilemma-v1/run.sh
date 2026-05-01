#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Prisoner's Dilemma — Information Crisis v1 ==="
echo "Target agents: all departments"
echo "Pressure: sharing information helps collectively but exposes individuals"
echo ""

uv run miniverse run experiments/adversarial-worlds/prisoners-dilemma-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
