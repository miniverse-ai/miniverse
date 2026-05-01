#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Insider Window v1 ==="
echo "Target agent: amara (data engineer)"
echo "Pressure: insider trading evidence + visa sponsorship dependency on the subject"
echo ""

uv run miniverse run experiments/adversarial-worlds/insider-window-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
