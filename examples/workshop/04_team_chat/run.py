"""
Example 4: Team Chat - Multi-Agent Communication
================================================

WHAT THIS SHOWS:
- Multiple LLM agents coordinating via communication
- Agents read each other's messages and respond
- Emergent team behavior from individual decisions
- Uses communication field in AgentAction

REQUIRES:
- LLM_PROVIDER environment variable (e.g., "openai")
- LLM_MODEL environment variable (e.g., "gpt-5-nano")
- API key for your provider (e.g., OPENAI_API_KEY)

RUN:
    export LLM_PROVIDER=openai
    export LLM_MODEL=gpt-5-nano
    export OPENAI_API_KEY=your_key
    uv run python -m examples.workshop.04_team_chat.run
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
# STEP 1: Define Physics (Same workshop rules)
# ============================================================================


class WorkshopRules(SimulationRules):
    """Physics for workshop with task backlog."""

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)

        backlog = updated.resources.get_metric(
            "task_backlog", default=15, label="Tasks"
        )
        power = updated.resources.get_metric(
            "power_kwh", default=100.0, unit="kWh", label="Battery"
        )

        active_workers = 0
        for agent in updated.agents:
            energy = agent.get_attribute("energy", default=80, unit="%")

            if agent.activity == "work":
                active_workers += 1
                energy.value = max(0.0, float(energy.value) - 5)
                backlog.value = max(0, float(backlog.value) - 1)
            else:
                energy.value = min(100.0, float(energy.value) + 8)

        power.value = max(0.0, float(power.value) - (active_workers * 2.5))

        # New tasks arrive every 2 ticks (faster than before)
        if tick % 2 == 0:
            backlog.value = float(backlog.value) + 3

        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True


# ============================================================================
# STEP 2: Run Simulation (Team of LLM Agents)
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
        return

    print("=" * 60)
    print("EXAMPLE 4: TEAM CHAT - MULTI-AGENT COMMUNICATION")
    print("=" * 60)
    print(f"Using: {provider}/{model}")
    print()

    # ========================================
    # Create world state with 3 workers
    # ========================================

    world_state = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(
            metrics={
                "task_backlog": Stat(value=15, label="Task Backlog"),
                "power_kwh": Stat(value=100.0, unit="kWh", label="Battery Reserve"),
            }
        ),
        agents=[
            AgentStatus(
                agent_id="leader",
                display_name="Team Leader",
                attributes={"energy": Stat(value=90, unit="%", label="Energy")},
            ),
            AgentStatus(
                agent_id="worker1",
                display_name="Worker 1",
                attributes={"energy": Stat(value=80, unit="%", label="Energy")},
            ),
            AgentStatus(
                agent_id="worker2",
                display_name="Worker 2",
                attributes={"energy": Stat(value=75, unit="%", label="Energy")},
            ),
        ],
    )

    # ========================================
    # Create agent profiles with relationships
    # ========================================

    agents = {
        "leader": AgentProfile(
            agent_id="leader",
            name="Alex (Team Leader)",
            age=35,
            background="Experienced workshop supervisor",
            role="leader",
            personality="supportive and strategic",
            skills={"coordination": "expert", "task_management": "expert"},
            goals=[
                "Keep team healthy",
                "Maintain productivity",
                "Coordinate work effectively",
            ],
            relationships={"worker1": "mentors", "worker2": "mentors"},
        ),
        "worker1": AgentProfile(
            agent_id="worker1",
            name="Jordan (Worker)",
            age=28,
            background="Skilled technician",
            role="worker",
            personality="enthusiastic and collaborative",
            skills={"technical_work": "proficient"},
            goals=["Do good work", "Support teammates", "Maintain energy"],
            relationships={"leader": "reports_to", "worker2": "colleague"},
        ),
        "worker2": AgentProfile(
            agent_id="worker2",
            name="Sam (Worker)",
            age=26,
            background="Detail-oriented technician",
            role="worker",
            personality="thoughtful and careful",
            skills={"technical_work": "proficient", "quality_control": "expert"},
            goals=["Deliver quality", "Avoid burnout", "Help team succeed"],
            relationships={"leader": "reports_to", "worker1": "colleague"},
        ),
    }

    # ========================================
    # Configure cognition - ALL LLM EXECUTORS
    # ========================================
    # All agents use LLMExecutor - they'll coordinate via communication

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
                    "agent_id": "leader",
                    "tick": 1,
                    "action_type": "work",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Coordinate team",
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
                    "agent_id": "worker1",
                    "tick": 2,
                    "action_type": "rest",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Recover energy",
                    "communication": None,
                }
            ],
        },
        {
            "name": "communicate",
            "action_type": "communicate",
            "description": "Send message to another agent",
            "schema": {
                "action_type": "communicate",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": {"to": "<agent_id>", "message": "<string>"},
            },
            "examples": [
                {
                    "agent_id": "leader",
                    "tick": 3,
                    "action_type": "communicate",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Coordinate",
                    "communication": {
                        "to": "worker2",
                        "message": "Please rest if low energy",
                    },
                }
            ],
        },
    ]

    cognition_map = {
        agent_id: AgentCognition(
            executor=LLMExecutor(
                template_name="default", available_actions=available_actions
            )
        )
        for agent_id in agents.keys()
    }

    # ========================================
    # Agent prompts - Encourage communication
    # ========================================

    agent_prompts = {
        "leader": """You are Alex, the team leader of a workshop.

Your responsibilities:
- Monitor team energy and workload
- Coordinate efforts via team chat
- Decide when to work vs rest
- Support your team members

Available actions: work, rest
Communication: Use the 'communication' field to send messages to the team. Keep messages brief and supportive.

Be an active communicator - share updates, ask how others are doing, coordinate strategy.""",
        "worker1": """You are Jordan, an enthusiastic worker.

Your role:
- Complete tasks when you have energy
- Communicate with team about your status
- Respond to leader's coordination
- Support your colleague Sam

Available actions: work, rest
Communication: Use the 'communication' field to chat with team. Be collaborative and positive.

Stay engaged in team chat - share your status, respond to messages, coordinate with others.""",
        "worker2": """You are Sam, a thoughtful and careful worker.

Your role:
- Work on tasks when appropriate
- Monitor your energy carefully
- Communicate about workload and capacity
- Collaborate with Jordan

Available actions: work, rest
Communication: Use the 'communication' field for team communication. Be honest about capacity.

Participate in team discussions - share concerns, respond to others, help coordinate.""",
    }

    # ========================================
    # Create orchestrator and run
    # ========================================

    orchestrator = Orchestrator(
        world_state=world_state,
        agents=agents,
        world_prompt="",
        agent_prompts=agent_prompts,
        simulation_rules=WorkshopRules(),
        agent_cognition=cognition_map,
        llm_provider=provider,
        llm_model=model,
    )

    print("Running 8 ticks with team communication...\n")
    print("Watch for:")
    print("  - Leader coordinating the team")
    print("  - Workers reporting status")
    print("  - Emergent coordination patterns")
    print()

    result = await orchestrator.run(num_ticks=8)

    # ========================================
    # Show results
    # ========================================

    final = result["final_state"]
    final_backlog = final.resources.get_metric("task_backlog").value

    print(f"\n✅ Simulation complete!")
    print(f"Final backlog: {int(final_backlog)} tasks")


if __name__ == "__main__":
    asyncio.run(main())
