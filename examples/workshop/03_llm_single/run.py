"""
Example 3: Simple Reactive LLM Agent
====================================

WHAT THIS SHOWS:
- Single LLM agent making intelligent decisions
- NO planning or reflection (purely reactive)
- Agent adapts to changing conditions
- Demonstrates minimal LLM configuration

REQUIRES:
- LLM_PROVIDER environment variable (e.g., "openai")
- LLM_MODEL environment variable (e.g., "gpt-5-nano")
- API key for your provider (e.g., OPENAI_API_KEY)

RUN:
    export LLM_PROVIDER=openai
    export LLM_MODEL=gpt-5-nano
    export OPENAI_API_KEY=your_key
    uv run python -m examples.workshop.03_llm_single.run
"""

import asyncio
import os
from datetime import datetime, timezone

from miniverse import (
    AgentAction,
    AgentProfile,
    AgentStatus,
    EnvironmentState,
    Orchestrator,
    ResourceState,
    SimulationRules,
    Stat,
    WorldState,
)
from miniverse.cognition import AgentCognition, LLMExecutor

# ============================================================================
# STEP 1: Define Physics (Same as example 02)
# ============================================================================


class WorkshopRules(SimulationRules):
    """Physics for workshop with task backlog and power consumption."""

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)

        backlog = updated.resources.get_metric(
            "task_backlog", default=10, label="Tasks"
        )
        power = updated.resources.get_metric(
            "power_kwh", default=100.0, unit="kWh", label="Battery"
        )

        active_workers = 0
        for agent in updated.agents:
            energy = agent.get_attribute("energy", default=80, unit="%")
            stress = agent.get_attribute("stress", default=20, unit="%")

            if agent.activity == "work":
                active_workers += 1
                energy.value = max(0.0, float(energy.value) - 5)
                stress.value = min(100.0, float(stress.value) + 3)
                backlog.value = max(0, float(backlog.value) - 1)
            else:
                energy.value = min(100.0, float(energy.value) + 8)
                stress.value = max(0.0, float(stress.value) - 2)

        power.value = max(0.0, float(power.value) - (active_workers * 2.5))

        # New tasks arrive every 3 ticks
        if tick % 3 == 0:
            backlog.value = float(backlog.value) + 2

        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True


# ============================================================================
# STEP 2: Run Simulation (LLM Agent)
# ============================================================================


async def main():
    # Check for LLM configuration
    provider = os.getenv("LLM_PROVIDER")
    model = os.getenv("LLM_MODEL")

    if not provider or not model:
        print("❌ LLM configuration missing!")
        print("\nPlease set environment variables:")
        print("  export LLM_PROVIDER=openai")
        print("  export LLM_MODEL=gpt-4")
        print("  export OPENAI_API_KEY=your_key")
        print("\nThen run this example again.")
        return

    print("=" * 60)
    print("EXAMPLE 3: SIMPLE REACTIVE LLM AGENT")
    print("=" * 60)
    print(f"Using: {provider}/{model}")
    # Optional: allow overriding world update mode for debugging speed
    world_update_mode = os.getenv("WORLD_UPDATE_MODE", "auto")
    debug_llm = os.getenv("DEBUG_LLM")
    debug_perc = os.getenv("DEBUG_PERCEPTION")
    verbose = os.getenv("MINIVERSE_VERBOSE")
    print(f"WORLD_UPDATE_MODE={world_update_mode} (auto|deterministic|llm)")
    print(
        f"DEBUG_LLM={'on' if debug_llm else 'off'} | DEBUG_PERCEPTION={'on' if debug_perc else 'off'} | MINIVERSE_VERBOSE={'on' if verbose else 'off'}"
    )
    # Explain phases so users understand where time is spent
    print(
        "Phases per tick: Physics -> Agent LLM (choose_action) -> World Engine (LLM or deterministic) -> Persist -> Summary"
    )
    print()

    # ========================================
    # Create world state (same as example 02)
    # ========================================

    world_state = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(
            metrics={
                "task_backlog": Stat(value=10, label="Task Backlog"),
                "power_kwh": Stat(value=100.0, unit="kWh", label="Battery Reserve"),
            }
        ),
        agents=[
            AgentStatus(
                agent_id="ai_worker",
                display_name="AI Worker",
                attributes={
                    "energy": Stat(value=80, unit="%", label="Energy"),
                    "stress": Stat(value=20, unit="%", label="Stress"),
                },
            ),
        ],
    )

    # ========================================
    # Create agent profile
    # ========================================

    agents = {
        "ai_worker": AgentProfile(
            agent_id="ai_worker",
            name="AI Worker",
            age=25,
            background="Workshop technician powered by AI",
            role="worker",
            personality="thoughtful and adaptive",
            skills={"task_management": "expert"},
            goals=["Maintain personal wellbeing", "Complete tasks efficiently"],
            relationships={},
        ),
    }

    # ========================================
    # Configure cognition - JUST LLM EXECUTOR
    # ========================================
    # KEY DIFFERENCE FROM EXAMPLE 02:
    # - Uses LLMExecutor instead of custom deterministic logic
    # - No planner (agent reacts to current state, no multi-step plans)
    # - No reflection (no memory synthesis)
    # - Agent makes intelligent decisions based on context

    # Provide a minimal explicit action catalog and default template for clarity
    available_actions = [
        {
            "name": "work",
            "action_type": "work",
            "description": "Work on current task",
            "schema": {
                "action_type": "work",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "agent_id": "ai_worker",
                    "tick": 1,
                    "action_type": "work",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Continue coordinating",
                    "communication": None,
                }
            ],
        },
        {
            "name": "rest",
            "action_type": "rest",
            "description": "Rest to recover energy",
            "schema": {
                "action_type": "rest",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "agent_id": "ai_worker",
                    "tick": 2,
                    "action_type": "rest",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Low energy",
                    "communication": None,
                }
            ],
        },
    ]

    cognition_map = {
        "ai_worker": AgentCognition(
            executor=LLMExecutor(
                template_name="default", available_actions=available_actions
            )
        ),
    }

    # ========================================
    # Create orchestrator and run
    # ========================================

    orchestrator = Orchestrator(
        world_state=world_state,
        agents=agents,
        world_prompt="",
        agent_prompts={
            "ai_worker": """You are an AI worker in a workshop. You can choose to 'work' or 'rest' each hour.

Your goals:
- Keep yourself healthy (don't let energy drop too low)
- Address the task backlog when it gets high
- Be thoughtful about balancing productivity and wellbeing

Available actions: work, rest"""
        },
        simulation_rules=WorkshopRules(),
        agent_cognition=cognition_map,
        llm_provider=provider,
        llm_model=model,
        world_update_mode=world_update_mode,
    )

    print("Running 10 ticks with LLM agent...\n")
    result = await orchestrator.run(num_ticks=10)

    # ========================================
    # Show results
    # ========================================

    final = result["final_state"]
    final_backlog = final.resources.get_metric("task_backlog").value
    final_power = final.resources.get_metric("power_kwh").value

    ai_worker_status = next(a for a in final.agents if a.agent_id == "ai_worker")
    final_energy = ai_worker_status.get_attribute("energy").value
    final_stress = ai_worker_status.get_attribute("stress").value

    print(f"\n✅ Simulation complete!")
    print(f"Final backlog: {int(final_backlog)} tasks")
    print(f"Final power: {final_power:.1f} kWh")
    print(f"AI Worker energy: {final_energy:.0f}%")
    print(f"AI Worker stress: {final_stress:.0f}%")


if __name__ == "__main__":
    asyncio.run(main())
