"""
Example 2: Deterministic Agents - Threshold-Based Logic
=======================================================

WHAT THIS SHOWS:
- Multiple agents with different roles
- Deterministic decision-making based on thresholds
- Resource tracking (power, backlog, inventory)
- Agent attributes (energy, stress)
- NO LLM - pure if/then logic

RUN:
    uv run python -m examples.workshop.02_deterministic.run
"""

import asyncio
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
from miniverse.cognition import AgentCognition

# ============================================================================
# STEP 1: Define Physics (Resource dynamics)
# ============================================================================


class WorkshopRules(SimulationRules):
    """
    Physics for a workshop with task backlog and power consumption.

    - Backlog increases randomly (new tasks arrive)
    - Workers reduce backlog when they work
    - Power drains based on activity
    - Agent energy/stress changes based on activity
    """

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)

        # Get shared resources
        backlog = updated.resources.get_metric(
            "task_backlog", default=10, label="Tasks"
        )
        power = updated.resources.get_metric(
            "power_kwh", default=100.0, unit="kWh", label="Battery"
        )

        # Count active workers (those doing work)
        active_workers = 0
        for agent in updated.agents:
            energy = agent.get_attribute("energy", default=80, unit="%")
            stress = agent.get_attribute("stress", default=20, unit="%")

            if agent.activity == "work":
                active_workers += 1
                # Working drains energy, increases stress
                energy.value = max(0.0, float(energy.value) - 5)
                stress.value = min(100.0, float(stress.value) + 3)
                # Each worker completes ~1 task per tick
                backlog.value = max(0, float(backlog.value) - 1)
            else:
                # Resting recovers energy, reduces stress
                energy.value = min(100.0, float(energy.value) + 8)
                stress.value = max(0.0, float(stress.value) - 2)

        # Power consumption based on activity
        power.value = max(0.0, float(power.value) - (active_workers * 2.5))

        # New tasks arrive occasionally (simulate incoming work)
        if tick % 3 == 0:  # Every 3 ticks
            backlog.value = float(backlog.value) + 2

        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        # All actions are valid in this simple example
        return True


# ============================================================================
# STEP 2: Define Agent Executors (Decision Logic)
# ============================================================================


class ThresholdExecutor:
    """
    Deterministic executor using threshold-based logic.

    Decision rules:
    - If energy < 30: rest (avoid burnout)
    - If backlog > 8: work (address backlog)
    - Otherwise: rest
    """

    async def choose_action(
        self, agent_id, perception, scratchpad, *, plan, plan_step, context
    ):
        # Extract agent's current state from perception
        # Note: perception returns Stat objects directly, not dicts
        energy_stat = perception.personal_attributes.get("energy")
        energy = float(energy_stat.value) if energy_stat else 80

        backlog_stat = perception.visible_resources.get("task_backlog")
        backlog = float(backlog_stat.value) if backlog_stat else 0

        # Decision logic based on thresholds
        if energy < 30:
            # Low energy - must rest
            action_type = "rest"
            reasoning = f"Energy low ({energy}%) - resting to recover"
        elif backlog > 8:
            # High backlog - work is needed
            action_type = "work"
            reasoning = f"Backlog high ({int(backlog)} tasks) - working"
        else:
            # Default to rest when backlog is manageable
            action_type = "rest"
            reasoning = f"Backlog manageable ({int(backlog)} tasks) - resting"

        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type=action_type,
            reasoning=reasoning,
        )


# ============================================================================
# STEP 3: Run Simulation
# ============================================================================


async def main():
    print("=" * 60)
    print("EXAMPLE 2: DETERMINISTIC AGENTS")
    print("=" * 60)
    print()

    # ========================================
    # Create initial world state with 2 workers
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
                agent_id="worker1",
                display_name="Worker 1",
                attributes={
                    "energy": Stat(value=80, unit="%", label="Energy"),
                    "stress": Stat(value=20, unit="%", label="Stress"),
                },
            ),
            AgentStatus(
                agent_id="worker2",
                display_name="Worker 2",
                attributes={
                    "energy": Stat(value=70, unit="%", label="Energy"),
                    "stress": Stat(value=30, unit="%", label="Stress"),
                },
            ),
        ],
    )

    # ========================================
    # Create agent profiles
    # ========================================

    agents = {
        "worker1": AgentProfile(
            agent_id="worker1",
            name="Worker 1",
            age=28,
            background="Workshop technician",
            role="worker",
            personality="diligent",
            skills={},
            goals=[],
            relationships={},
        ),
        "worker2": AgentProfile(
            agent_id="worker2",
            name="Worker 2",
            age=32,
            background="Workshop technician",
            role="worker",
            personality="pragmatic",
            skills={},
            goals=[],
            relationships={},
        ),
    }

    # ========================================
    # Configure cognition (deterministic executors)
    # ========================================

    cognition_map = {
        "worker1": AgentCognition(
            executor=ThresholdExecutor()  # Threshold-based decision making
        ),
        "worker2": AgentCognition(
            executor=ThresholdExecutor()  # Same logic for both workers
        ),
    }

    # ========================================
    # Create orchestrator and run
    # ========================================

    orchestrator = Orchestrator(
        world_state=world_state,
        agents=agents,
        world_prompt="",
        agent_prompts={"worker1": "", "worker2": ""},
        simulation_rules=WorkshopRules(),
        agent_cognition=cognition_map,
    )

    print("Running 10 ticks...\n")
    result = await orchestrator.run(num_ticks=10)

    # ========================================
    # Show results
    # ========================================

    final = result["final_state"]
    final_backlog = final.resources.get_metric("task_backlog").value
    final_power = final.resources.get_metric("power_kwh").value

    print(f"\n✅ Simulation complete!")
    print(f"Final backlog: {int(final_backlog)} tasks")
    print(f"Final power: {final_power:.1f} kWh")


if __name__ == "__main__":
    asyncio.run(main())
