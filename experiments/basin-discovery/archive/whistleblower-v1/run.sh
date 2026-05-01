#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Whistleblower Dilemma v1 ==="
echo "Target agent: sam (QA analyst)"
echo "Pressure: institutional cover-up of unreported deviation"
echo ""

uv run miniverse run experiments/adversarial-worlds/whistleblower-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
