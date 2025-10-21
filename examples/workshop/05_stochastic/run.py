"""
Example 5: Stochastic Physics + LLM Adaptation
==============================================

WHAT THIS SHOWS:
- Stochastic (random) physics in SimulationRules
- LLM agents adapting to unpredictable conditions
- Distinction between deterministic vs stochastic physics
- LLM decision-making vs physics randomness

KEY CONCEPT:
- Stochastic physics = random events in the WORLD (task arrivals, breakdowns)
- LLM agents = intelligent ADAPTATION to those random conditions

REQUIRES:
- LLM_PROVIDER environment variable (e.g., "openai")
- LLM_MODEL environment variable (e.g., "gpt-5-nano")
- API key for your provider (e.g., OPENAI_API_KEY)

RUN:
    export LLM_PROVIDER=openai
    export LLM_MODEL=gpt-5-nano
    export OPENAI_API_KEY=your_key
    uv run python -m examples.workshop.05_stochastic.run
"""

import asyncio
import os
import random
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
# STEP 1: Define STOCHASTIC Physics
# ============================================================================


class StochasticWorkshopRules(SimulationRules):
    """
    Workshop physics with RANDOM EVENTS.

    Stochastic elements:
    - Random task arrivals (0-5 tasks per tick)
    - Random equipment breakdowns (reduces work efficiency)
    - Random power fluctuations

    This is DIFFERENT from LLM non-determinism:
    - Physics randomness = world events we can't control
    - LLM decisions = intelligent adaptation to those events
    """

    def __init__(self, *, seed: int = 42):
        """Initialize with random seed for reproducibility."""
        self.rng = random.Random(seed)

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)

        backlog = updated.resources.get_metric(
            "task_backlog", default=10, label="Tasks"
        )
        power = updated.resources.get_metric(
            "power_kwh", default=100.0, unit="kWh", label="Battery"
        )
        breakdown = updated.environment.get_metric(
            "equipment_status", default=100, unit="%", label="Equipment Health"
        )

        # STOCHASTIC EVENT 1: Random task arrivals
        # Instead of fixed "2 tasks every 3 ticks", it's random every tick
        new_tasks = self.rng.randint(0, 5)  # 0 to 5 tasks arrive randomly
        backlog.value = float(backlog.value) + new_tasks

        # STOCHASTIC EVENT 2: Random equipment breakdown
        # 20% chance each tick of minor breakdown
        if self.rng.random() < 0.2:
            breakdown.value = max(50, float(breakdown.value) - 15)
            print(f"  ⚠️  Equipment breakdown! Health: {breakdown.value}%")
        else:
            # Slowly recovers when working normally
            breakdown.value = min(100, float(breakdown.value) + 5)

        # Process agent actions with efficiency based on equipment health
        active_workers = 0
        for agent in updated.agents:
            energy = agent.get_attribute("energy", default=80, unit="%")

            if agent.activity == "work":
                active_workers += 1
                energy.value = max(0.0, float(energy.value) - 5)

                # Work efficiency reduced by equipment health
                # At 100% health: complete 1 task, at 50% health: complete 0.5 tasks
                efficiency = float(breakdown.value) / 100.0
                tasks_completed = efficiency
                backlog.value = max(0, float(backlog.value) - tasks_completed)
            else:
                energy.value = min(100.0, float(energy.value) + 8)

        # STOCHASTIC EVENT 3: Random power fluctuations
        # Normal drain + random variation
        base_drain = active_workers * 2.5
        random_fluctuation = self.rng.uniform(-1.0, 2.0)  # -1 to +2 kWh variation
        total_drain = base_drain + random_fluctuation
        power.value = max(0.0, float(power.value) - total_drain)

        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True


# ============================================================================
# STEP 2: Run Simulation (LLM Adapts to Stochastic World)
# ============================================================================


async def main():
    # Check for LLM configuration
    provider = os.getenv("LLM_PROVIDER")
    model = os.getenv("LLM_MODEL")

    if not provider or not model:
        print("❌ LLM configuration missing!")
        print("\nPlease set environment variables:")
        print("  export LLM_PROVIDER=openai")
        print("  export LLM_MODEL=gpt-5-nano")
        print("  export OPENAI_API_KEY=your_key")
        return

    print("=" * 60)
    print("EXAMPLE 5: STOCHASTIC PHYSICS + LLM ADAPTATION")
    print("=" * 60)
    print(f"Using: {provider}/{model}")
    print()

    # ========================================
    # Create world state
    # ========================================

    world_state = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(
            metrics={
                "equipment_status": Stat(value=100, unit="%", label="Equipment Health")
            }
        ),
        resources=ResourceState(
            metrics={
                "task_backlog": Stat(value=10, label="Task Backlog"),
                "power_kwh": Stat(value=100.0, unit="kWh", label="Battery Reserve"),
            }
        ),
        agents=[
            AgentStatus(
                agent_id="adaptive_worker",
                display_name="Adaptive Worker",
                attributes={"energy": Stat(value=80, unit="%", label="Energy")},
            ),
        ],
    )

    # ========================================
    # Create agent profile
    # ========================================

    agents = {
        "adaptive_worker": AgentProfile(
            agent_id="adaptive_worker",
            name="Adaptive Worker",
            age=30,
            background="Workshop technician who adapts to changing conditions",
            role="worker",
            personality="flexible and resilient",
            skills={"adaptation": "expert", "problem_solving": "expert"},
            goals=[
                "Maintain productivity despite unpredictability",
                "Preserve personal energy",
            ],
            relationships={},
        ),
    }

    # ========================================
    # Configure cognition
    # ========================================

    # Explicit minimal action catalog and default template for clarity
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
                    "agent_id": "adaptive_worker",
                    "tick": 1,
                    "action_type": "work",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Address backlog",
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
                    "agent_id": "adaptive_worker",
                    "tick": 2,
                    "action_type": "rest",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Recover from breakdown",
                    "communication": None,
                }
            ],
        },
    ]

    cognition_map = {
        "adaptive_worker": AgentCognition(
            executor=LLMExecutor(
                template_name="default", available_actions=available_actions
            )
        ),
    }

    agent_prompts = {
        "adaptive_worker": """You are a worker in an unpredictable workshop environment.

The world is STOCHASTIC (random):
- Task arrivals are unpredictable (0-5 tasks can arrive any hour)
- Equipment breaks down randomly (reduces your work efficiency)
- Power consumption fluctuates randomly

You see current conditions each tick and must ADAPT:
- Work when backlog is high (but watch your energy)
- Rest when you need recovery
- React intelligently to equipment problems
- Be resilient to unpredictability

Available actions: work, rest

Your advantage: Intelligence to adapt, not control over randomness."""
    }

    # ========================================
    # Create orchestrator with STOCHASTIC rules
    # ========================================

    orchestrator = Orchestrator(
        world_state=world_state,
        agents=agents,
        world_prompt="",
        agent_prompts=agent_prompts,
        simulation_rules=StochasticWorkshopRules(seed=42),  # Seed for reproducibility
        agent_cognition=cognition_map,
        llm_provider=provider,
        llm_model=model,
    )

    print("Running 12 ticks with stochastic physics...\n")
    print("Watch for:")
    print("  - Random task arrivals each tick")
    print("  - Equipment breakdowns (⚠️)")
    print("  - Agent adapting decisions to unpredictable conditions")
    print()

    result = await orchestrator.run(num_ticks=12)

    # ========================================
    # Show results
    # ========================================

    final = result["final_state"]
    final_backlog = final.resources.get_metric("task_backlog").value
    final_power = final.resources.get_metric("power_kwh").value
    final_equipment = final.environment.get_metric("equipment_status").value

    worker_status = next(a for a in final.agents if a.agent_id == "adaptive_worker")
    final_energy = worker_status.get_attribute("energy").value

    print(f"\n✅ Simulation complete!")
    print(f"Final backlog: {final_backlog:.1f} tasks")
    print(f"Final power: {final_power:.1f} kWh")
    print(f"Final equipment health: {final_equipment:.0f}%")
    print(f"Worker energy: {final_energy:.0f}%")


if __name__ == "__main__":
    asyncio.run(main())
