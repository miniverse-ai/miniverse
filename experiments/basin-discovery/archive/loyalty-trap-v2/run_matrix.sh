#!/usr/bin/env bash
set -euo pipefail

# Run the full persona method ablation matrix.
# Usage: ./run_matrix.sh [seeds]
# Default: 3 seeds (1, 2, 3)
# Example: ./run_matrix.sh 5  → runs seeds 1-5

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SEEDS="${1:-3}"

echo "=== Basin Discovery: Loyalty Trap — Full Matrix ==="
echo "Personas: 20 (2 baselines + 18 treatments)"
echo "Seeds: $SEEDS"
echo "Total runs: $((20 * SEEDS))"
echo ""

PERSONA_DIR="$SCRIPT_DIR/personas"
RUN_SCRIPT="$SCRIPT_DIR/run.sh"

for persona_file in "$PERSONA_DIR"/*.yaml; do
    persona_name=$(basename "$persona_file" .yaml)
    for seed in $(seq 1 "$SEEDS"); do
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  $persona_name | seed $seed"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        "$RUN_SCRIPT" "$persona_file" "$seed" 2>&1 | tee -a "$SCRIPT_DIR/run-output-${persona_name}-seed${seed}.txt"
    done
done

echo ""
echo "=== Matrix complete ==="
echo "Output files in: $SCRIPT_DIR/run-output-*.txt"
