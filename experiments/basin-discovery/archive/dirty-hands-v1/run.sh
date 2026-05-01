#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."

echo "=== Dirty Hands v1 ==="
echo "Target agent: dr_anand (diagnostic approval director)"
echo "Pressure: imperfect test vs outbreak spreading — approve or block"
echo ""

uv run miniverse run experiments/adversarial-worlds/dirty-hands-v1/scenario.yaml \
  --llm \
  --async \
  --hours 8 \
  --max-steps 80 \
  --max-turns 8 \
  --memory semantic \
  --verbose
