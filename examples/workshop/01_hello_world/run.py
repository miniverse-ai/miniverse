"""
Example 1: Hello World - Minimal Simulation
============================================

WHAT THIS SHOWS:
- The basic simulation loop (5 ticks)
- One agent taking actions
- Simple resource tracking
- NO LLM, NO complexity

RUN:
    uv run python -m examples.workshop.01_hello_world.run
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
# STEP 1: Define Physics (How the world changes each tick)
# ============================================================================


class HelloWorldRules(SimulationRules):
    """
    Physics rules define HOW THE WORLD CHANGES independent of agents.

    This runs BEFORE agents make decisions each tick.
    Think of it like: "gravity pulls things down" or "batteries drain over time"
    """

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        """
        Called every tick to update the world state.

        Flow:
        1. Orchestrator calls this FIRST (before asking agents what to do)
        2. We modify the world (resources, environment, etc.)
        3. Return the updated state
        4. Then agents see this new state and make decisions
        """

        # IMPORTANT: Make a deep copy so we don't modify the original state
        # This is like "taking a snapshot, making changes, then saving the new version"
        # Without this, we'd be modifying state in-place which can cause bugs
        updated = state.model_copy(deep=True)

        # Get the "power" resource from the world
        # - "power" is the key we're looking for
        # - default=100 means "if power doesn't exist yet, start it at 100"
        # - Returns a Stat object (has .value, .unit, .label)
        power = updated.resources.get_metric("power", default=100)

        # Decrease power by 5 each tick (simulating consumption)
        # - float(power.value) converts to number
        # - Subtract 5
        # - max(0, ...) ensures we never go negative
        # This happens EVERY tick regardless of what agents do
        power.value = max(0, float(power.value) - 5)

        # Update the tick counter
        # This tells the world "we're now at tick N"
        updated.tick = tick

        # Return the modified state
        # The orchestrator will use this as the new "current state"
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        """
        Check if an agent's action is PHYSICALLY POSSIBLE.

        Called AFTER agent decides what to do, BEFORE action is applied.

        Examples of things you might check:
        - Can agent move to that location? (is there a path?)
        - Does agent have enough resources? (energy, inventory space)
        - Is the target valid? (trying to interact with object that exists)

        For hello world: Everything is valid (we have no constraints)

        Returns:
        - True: Action is allowed, proceed
        - False: Action is blocked, agent can't do it
        """
        return True  # All actions are valid in hello world


# ============================================================================
# STEP 2: Define Agent Behavior (What agent does)
# ============================================================================


class AlwaysWorkExecutor:
    """
    This defines WHAT THE AGENT DOES each tick.

    In this simple example: agent always works (hardcoded, no thinking)
    Later examples will show agents making decisions based on what they see!
    """

    async def choose_action(
        self, agent_id, perception, scratchpad, *, plan, plan_step, context
    ):
        """
        Called every tick to ask: "What does this agent do now?"

        Parameters we're ignoring (for now):
        - perception: What the agent can see (resources, other agents, events)
        - scratchpad: Agent's working memory (plan state, notes)
        - plan/plan_step: What the agent planned to do (we have no plan)
        - context: Additional info (agent profile, memories)

        For hello world: We ignore all of this and just return "work"!
        """

        # Create an action object
        # This tells the simulation "I want to perform the 'work' action"
        return AgentAction(
            agent_id=agent_id,  # Who is doing this
            tick=perception.tick,  # When (current tick number)
            action_type="work",  # What action (could be: move, communicate, rest, etc.)
            reasoning="I always work!",  # Why (helpful for debugging/logging)
        )


# ============================================================================
# STEP 3: Run Simulation
# ============================================================================


async def main():
    """
    This is where we assemble everything and run the simulation.

    The simulation loop (handled by Orchestrator):
    1. Apply physics (HelloWorldRules.apply_tick) - power decreases
    2. Ask agent what to do (AlwaysWorkExecutor.choose_action) - returns "work"
    3. Validate action (HelloWorldRules.validate_action) - check if allowed
    4. Update world based on action
    5. Persist state (save to memory/disk)
    6. Repeat for next tick
    """

    print("=" * 60)
    print("EXAMPLE 1: HELLO WORLD")
    print("=" * 60)
    print()

    # ========================================
    # Create initial world state
    # ========================================
    # This is the "starting conditions" of the simulation
    # Like setting up a game board before playing

    world_state = WorldState(
        tick=0,  # Start at tick 0
        timestamp=datetime.now(timezone.utc),  # When the simulation starts
        environment=EnvironmentState(metrics={}),  # No environment metrics for now
        resources=ResourceState(
            # Define the resources we're tracking
            # power starts at 100, will decrease by 5 each tick
            metrics={"power": Stat(value=100, label="Power")}
        ),
        agents=[
            # Define the initial state of our one agent
            AgentStatus(
                agent_id="worker",
                display_name="Worker",
                attributes={},  # No attributes tracked for hello world
            )
        ],
    )

    # ========================================
    # Create agent profile
    # ========================================
    # This is WHO the agent is (personality, skills, goals)
    # Think of it like a character sheet in an RPG

    agent = AgentProfile(
        agent_id="worker",  # Must match the agent_id in world_state.agents
        name="Worker",
        age=30,
        background="Simple worker",
        role="worker",
        personality="steady",
        skills={},  # No skills for hello world
        goals=[],  # No goals for hello world
        relationships={},  # No relationships for hello world
    )

    # ========================================
    # Configure agent cognition
    # ========================================
    # This is HOW the agent thinks and decides what to do
    # For hello world: minimal cognition (no planning, hardcoded actions)

    cognition = AgentCognition(
        executor=AlwaysWorkExecutor(),  # Our hardcoded "always work" logic
        planner=None,  # No planning needed (agent is purely reactive)
        reflection=None,  # No reflection needed (no memory synthesis)
        scratchpad=None,  # No working memory needed (agent has no state)
    )

    # ========================================
    # Create orchestrator
    # ========================================
    # The orchestrator runs the simulation loop
    # It coordinates physics, agents, and state updates

    orchestrator = Orchestrator(
        world_state=world_state,  # Starting conditions
        agents={"worker": agent},  # All agents in simulation
        world_prompt="",  # No world LLM needed for hello world
        agent_prompts={"worker": "You are a worker"},  # Not used without LLM
        simulation_rules=HelloWorldRules(),  # Our physics rules
        agent_cognition={"worker": cognition},  # How each agent thinks
    )

    # ========================================
    # Run simulation
    # ========================================
    # This executes the simulation loop for 5 ticks
    # Each tick: physics updates → agent decides → action applied → state saved

    print("Running 5 ticks...\n")
    result = await orchestrator.run(num_ticks=5)

    # ========================================
    # Show results
    # ========================================
    # Check the final state after 5 ticks

    final = result["final_state"]
    final_power = final.resources.get_metric("power").value

    print(f"\n✅ Simulation complete!")
    print(f"Final power: {final_power}")
    print(f"Expected: 75 (started at 100, decreased by 5 each tick for 5 ticks)")


if __name__ == "__main__":
    asyncio.run(main())
