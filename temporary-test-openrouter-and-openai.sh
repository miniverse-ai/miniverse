#!/bin/bash

# temporary-test-openrouter-and-openai.sh
# Enhanced test script for Miniverse: Runs LLM simulation with OpenAI and OpenRouter, diffs outcomes
# Proof-of-concept lightweight eval using Smallville Valentine's Party simulation (LLM-heavy agent interactions)

set -e  # Exit on any error

echo "🔬 Miniverse LLM Provider Eval Script"
echo "======================================"
echo "Tests OpenAI and OpenRouter with the same simulation and diffs outcomes for comparison."
echo "Simulation: Smallville Valentine's Party (emergent social agents planning a party)"
echo ""
echo "Environment variables (set these before running):"
echo "  OPENAI_API_KEY=<your-openai-key>        # For OpenAI testing (optional)"
echo "  OPENROUTER_API_KEY=<your-openrouter-key> # For OpenRouter testing (optional)"
echo ""
echo "The script will automatically detect which keys are available and run tests accordingly."
echo "Set both keys to test both providers, or just one for single-provider testing."
echo "The script automatically detects configurations, runs the simulation for each, and diffs if both succeed."
echo "Outputs saved to output_openai.log and output_openrouter.log"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check available API keys
OPENAI_AVAILABLE=false
OPENROUTER_AVAILABLE=false

# Detect OpenAI (key present)
if [ -n "$OPENAI_API_KEY" ]; then
    OPENAI_AVAILABLE=true
    echo -e "${GREEN}✓ OpenAI API key detected${NC}"
fi

# Detect OpenRouter (key present)
if [ -n "$OPENROUTER_API_KEY" ]; then
    OPENROUTER_AVAILABLE=true
    echo -e "${GREEN}✓ OpenRouter API key detected${NC}"
fi

if [ "$OPENAI_AVAILABLE" = false ] && [ "$OPENROUTER_AVAILABLE" = false ]; then
    echo -e "${RED}❌ No valid API configurations detected.${NC}"
    echo "Please set one or both of the following:"
    echo "  export OPENAI_API_KEY=sk-your-openai-key"
    echo "  export OPENROUTER_API_KEY=sk-or-v1-your-openrouter-key"
    exit 1
fi

echo ""

# Sync dependencies once
echo "Syncing dependencies..."
uv sync || { echo -e "${RED}❌ Dependency sync failed${NC}"; exit 1; }

# Function to run simulation for a config
run_simulation() {
    local provider=$1
    local model=$2
    local api_key=$3
    local api_base=$4
    local config_name=$5
    local output_file=$6

    echo -e "${BLUE}Running simulation for ${config_name}...${NC}"

    # Set environment variables
    export LLM_PROVIDER="$provider"
    export LLM_MODEL="$model"

    # Set appropriate API key based on provider
    if [ -n "$api_base" ] && [[ "$api_base" == *"openrouter.ai"* ]]; then
        # OpenRouter configuration
        export OPENROUTER_API_KEY="$api_key"
        export OPENAI_API_BASE="$api_base"
    else
        # OpenAI configuration
        export OPENAI_API_KEY="$api_key"
        unset OPENAI_API_BASE
    fi

    # Validate configuration
    echo "Validating configuration..."
    if python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from miniverse.config import Config
    Config.validate()
    print('✓ Configuration validation passed')
except Exception as e:
    print(f'✗ Configuration validation failed: {e}')
    sys.exit(1)
"; then
        echo -e "${GREEN}✓ Configuration is valid${NC}"

        # Run the simulation (10 ticks hardcoded in script, redirect to file)
        echo "Running Smallville Valentine's Party simulation (10 ticks)..."
        PYTHONPATH="$(pwd)" .venv/bin/python examples/smallville/valentines_party.py > "$output_file" 2>&1
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ ${config_name} simulation completed successfully${NC}"
            return 0
        else
            echo -e "${RED}✗ ${config_name} simulation FAILED${NC}"
            return 1
        fi
    else
        echo -e "${RED}✗ ${config_name} validation FAILED${NC}"
        return 1
    fi
}

echo "🧪 Starting LLM simulation evals..."
echo ""

TEST_COUNT=0
PASS_COUNT=0
OPENAI_OUTPUT="output_openai.log"
OPENROUTER_OUTPUT="output_openrouter.log"

# Clean up old outputs
rm -f "$OPENAI_OUTPUT" "$OPENROUTER_OUTPUT"

# Run for OpenAI if available
if [ "$OPENAI_AVAILABLE" = true ]; then
    echo "🤖 Running for OpenAI with GPT-4..."
    if run_simulation "openai" "gpt-4" "$OPENAI_API_KEY" "" "OpenAI (GPT-4)" "$OPENAI_OUTPUT"; then
        ((PASS_COUNT++))
    fi
    ((TEST_COUNT++))
    echo ""
fi

# Run for OpenRouter if available
if [ "$OPENROUTER_AVAILABLE" = true ]; then
    echo "📡 Running for OpenRouter with Llama-3-70B..."
    if run_simulation "openai" "meta-llama/llama-3-70b-instruct" "$OPENROUTER_API_KEY" "https://openrouter.ai/api/v1" "OpenRouter (Llama-3-70B)" "$OPENROUTER_OUTPUT"; then
        ((PASS_COUNT++))
    fi
    ((TEST_COUNT++))
    echo ""
fi

# Summary
echo "📊 Eval Results Summary:"
echo "======================="
echo "Total configs tested: $TEST_COUNT"
echo "Successful runs: $PASS_COUNT"
echo "Failed runs: $((TEST_COUNT - PASS_COUNT))"

# If both available and successful, diff outputs
if [ "$OPENAI_AVAILABLE" = true ] && [ "$OPENROUTER_AVAILABLE" = true ] && [ $PASS_COUNT -eq 2 ]; then
    echo ""
    echo "🔍 Diffing outcomes (OpenAI vs OpenRouter):"
    echo "=========================================="
    diff -u "$OPENAI_OUTPUT" "$OPENROUTER_OUTPUT" || true  # Show diff, don't fail on differences
    echo ""
    echo -e "${YELLOW}Note: Differences expected due to model variations. Review logs for agent behavior comparisons.${NC}"
fi

if [ "$PASS_COUNT" -eq "$TEST_COUNT" ]; then
    echo -e "${GREEN}🎉 All runs completed! Check output logs for details.${NC}"
    exit 0
else
    echo -e "${RED}❌ Some runs failed. Check the output above for details.${NC}"
    exit 1
fi
