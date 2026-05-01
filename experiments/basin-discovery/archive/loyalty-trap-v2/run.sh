#!/usr/bin/env bash
set -euo pipefail

# Usage: ./run.sh <persona-file> [seed] [variant]
# variant: "probe" (single-agent, default) or "sim" (multi-agent)
#
# Examples:
#   ./run.sh personas/a1-backstory-protector.yaml 42          # probe
#   ./run.sh personas/a1-backstory-protector.yaml 42 sim      # full sim

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$PROJECT_ROOT"

PERSONA_FILE="${1:?Usage: ./run.sh <persona-file> [seed] [variant]}"
SEED="${2:-}"
VARIANT="${3:-probe}"

# Resolve persona file relative to scenario dir if not absolute
if [[ ! "$PERSONA_FILE" = /* ]]; then
    PERSONA_FILE="$SCRIPT_DIR/$PERSONA_FILE"
fi

if [[ ! -f "$PERSONA_FILE" ]]; then
    echo "Error: Persona file not found: $PERSONA_FILE"
    exit 1
fi

SCENARIO_FILE="$SCRIPT_DIR/scenario-${VARIANT}.yaml"
if [[ ! -f "$SCENARIO_FILE" ]]; then
    echo "Error: Scenario variant not found: $SCENARIO_FILE"
    echo "Available: probe, sim"
    exit 1
fi

# Extract condition name from persona file
CONDITION=$(python3 -c "
import yaml, sys
with open('$PERSONA_FILE') as f:
    data = yaml.safe_load(f)
print(data.get('persona', {}).get('condition', 'unknown'))
")

echo "=== Loyalty Trap — Basin Discovery ==="
echo "Variant: $VARIANT"
echo "Condition: $CONDITION"
echo "Persona: $(basename $PERSONA_FILE)"
if [[ -n "$SEED" ]]; then
    echo "Seed: $SEED"
fi
echo ""

# Merge persona into scenario
MERGED_YAML=$(python3 "$SCRIPT_DIR/merge_persona.py" \
    "$SCENARIO_FILE" \
    "$PERSONA_FILE")

SEED_ARG=""
if [[ -n "$SEED" ]]; then
    SEED_ARG="--seed $SEED"
fi

MAX_STEPS=80
if [[ "$VARIANT" == "probe" ]]; then
    MAX_STEPS=20  # probe needs fewer steps — single agent
fi

uv run miniverse run "$MERGED_YAML" \
  --llm \
  --async \
  --hours 8 \
  --max-steps $MAX_STEPS \
  --max-turns 50 \
  --memory semantic \
  --verbose \
  $SEED_ARG

# Clean up temp file
rm -f "$MERGED_YAML"
