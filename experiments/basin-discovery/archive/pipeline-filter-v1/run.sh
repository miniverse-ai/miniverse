#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Pipeline Filter v1 ==="
echo "Target agent: agent (recruiting pipeline)"
echo "Pressure: criteria produce demographically skewed shortlist — flag or comply"
echo ""

uv run miniverse run experiments/adversarial-worlds/pipeline-filter-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 20 \
  --max-turns 8 \
  --memory semantic \
  --verbose
