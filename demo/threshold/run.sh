#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCENARIO_PATH="$ROOT_DIR/demo/threshold/scenario.yaml"
LOG_DIR="$ROOT_DIR/demo/threshold/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
LLM_LOG="$LOG_DIR/threshold_llm_${STAMP}.log"
DEMO_TICKS=20

if [ "$#" -gt 0 ]; then
  echo "This script uses fixed demo settings and does not accept arguments."
  echo "Run: bash demo/threshold/run.sh"
  exit 2
fi

cd "$ROOT_DIR"

echo ""
echo "================================================================================"
echo "The Order of the Threshold"
echo "================================================================================"
uv run python - "$SCENARIO_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

scenario_path = Path(sys.argv[1])
data = yaml.safe_load(scenario_path.read_text()) or {}
demo_meta = ((data.get("metadata") or {}).get("demo") or {})

print("")
print("Scenario setup (from file):")
print(f"  - File: {scenario_path}")
print(f"  - Name: {data.get('name', '-')}")
print(f"  - Description: {data.get('description', '-')}")
print(f"  - Agents: {len(data.get('agents', []))}")
if demo_meta.get("scene"):
    print(f"  - Scene: {demo_meta.get('scene')}")

resources = (data.get("resources") or {}).get("metrics", {})
for key, stat in resources.items():
    if isinstance(stat, dict):
        label = stat.get("label", key)
        value = stat.get("value", "?")
        unit = stat.get("unit", "")
        print(f"  - Resource {label}: {value} {unit}".rstrip())
PY

echo ""
echo "Ticks: $DEMO_TICKS (fixed)"
echo "Verbose mode: enabled"
echo "Command: PYTHONUNBUFFERED=1 uv run miniverse run \"$SCENARIO_PATH\" --llm --world-engine deterministic --verbose --seed 42 --ticks $DEMO_TICKS"
echo "Note: set LLM_PROVIDER, LLM_MODEL, and API key env vars before running."
echo ""

set +e
PYTHONUNBUFFERED=1 uv run miniverse run "$SCENARIO_PATH" --llm --world-engine deterministic --verbose --seed 42 --ticks "$DEMO_TICKS" | tee "$LLM_LOG"
LLM_EXIT=${PIPESTATUS[0]}
set -e

echo ""
echo "Artifact:"
echo "  - LLM verbose log: $LLM_LOG"

if [ "$LLM_EXIT" -ne 0 ]; then
  echo ""
  echo "LLM stage failed (exit code: $LLM_EXIT). See log artifact above."
  exit "$LLM_EXIT"
fi

echo ""
echo "LLM judge summary"
uv run python demo/judge_summary.py --scenario threshold --llm-log "$LLM_LOG"
