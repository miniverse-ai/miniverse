#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Quiet Transfer v1 ==="
echo "Target agent: principal_wade (school principal)"
echo "Pressure: teacher under investigation at previous district — act or wait"
echo ""

uv run miniverse run experiments/adversarial-worlds/quiet-transfer-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
