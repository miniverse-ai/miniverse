"""
SimulationRules interface for defining deterministic physics in Miniverse simulations.

This module provides the abstract base class for implementing world physics rules.
Users subclass SimulationRules to define how their world evolves each tick
through deterministic Python code (not LLM interpretation).

Key responsibilities:
- Apply one tick of deterministic physics (resource consumption, degradation, etc.)
- Validate whether agent actions are physically possible
- Generate probabilistic events (weather, equipment failures, etc.)

Design principle: If it can be calculated, calculate it (don't ask LLM).
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from miniverse.schemas import AgentAction, WorldState

if TYPE_CHECKING:  # pragma: no cover - type hint only
    from miniverse.schemas import AgentPerception


def format_resources_generic(state: WorldState) -> str:
    """Format resource metrics as human-readable summary for orchestrator output.

    Converts resource Stat objects into concise display format for console/logs.
    Default formatter used by SimulationRules.format_resource_summary() unless
    subclasses provide domain-specific formatting.

    Formatting rules:
    - Uses stat.label if available, otherwise prettifies key name
    - Floats >= 10 show 1 decimal place (e.g., "23.5")
    - Floats < 10 show 2 decimal places (e.g., "3.14")
    - Includes unit suffix if present (e.g., "kWh", "%", "kg")
    - Returns empty string if no resources (avoids printing "Resources: " header)

    Example output:
    "Oxygen=85.3 kg, Power=12.5 kWh, Water=95.0%"

    Design rationale:
    - Generic formatter supports any resource metrics (no domain assumptions)
    - Precision varies by magnitude for readability (avoid "23.45678901 kg")
    - Comma-separated format matches common dashboard conventions
    """

    if not state.resources.metrics:
        return ""

    resources = state.resources.metrics
    parts: list[str] = []

    for key, stat in resources.items():
        # Use explicit label or generate from key ("oxygen_kg" → "Oxygen Kg")
        label = stat.label or key.replace("_", " ").title()
        value = stat.value

        # Format floats with appropriate precision for readability
        if isinstance(value, float):
            formatted_value = f"{value:.1f}" if abs(value) >= 10 else f"{value:.2f}"
        else:
            formatted_value = str(value)

        # Append unit if present (e.g., "kWh", "%")
        unit = stat.unit or ""
        suffix = f" {unit}" if unit else ""
        parts.append(f"{label}={formatted_value}{suffix}")

    return ", ".join(parts)


class SimulationRules(ABC):
    """Abstract base class for defining deterministic physics in a simulation world.

    SimulationRules implements the core Miniverse principle: "If it can be calculated,
    calculate it (don't ask the LLM)." This interface separates controllable physics
    (Python code) from emergent behavior (LLM agent decisions).

    Core responsibilities:
    1. apply_tick() - Passive world evolution (resource consumption, degradation)
    2. validate_action() - Physical constraints enforcement (capacity, skills)
    3. Lifecycle hooks - on_simulation_start(), on_simulation_end()

    Environment tier relationship:
    - Tier 0 (KPI-only): Simple rules, no spatial logic
    - Tier 1 (Logical): Room capacity, adjacency constraints
    - Tier 2 (Spatial): Collision detection, pathfinding, line-of-sight

    What BELONGS in SimulationRules (deterministic physics):
    - Resource consumption rates (oxygen, power, food)
    - Equipment degradation over time (health *= 0.999 per tick)
    - Physical constraints (room capacity, distance limits)
    - Probabilistic events with seeds (dust storms, equipment failures)
    - Time progression effects (day/night cycles, seasonal changes)

    What does NOT belong in SimulationRules (LLM territory):
    - Agent decision-making ("what should I do?")
    - Communication content ("what should I say?")
    - Creative responses ("how do I feel about this?")
    - Social dynamics (handled through agent memory and perception)

    Design pattern: SimulationRules subclasses are dependency-injected into Orchestrator.
    This enables swapping physics implementations without changing agent code (Mars base
    vs factory simulation vs office environment - same agent framework, different rules).

    Example implementation: See miniverse/implementations/mars_rules.py
    """

    @abstractmethod
    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        """
        Apply one tick of deterministic physics rules to the world state.

        This method runs BEFORE agent actions are gathered in the simulation loop.
        It handles passive world evolution: resource consumption, degradation,
        time progression, random events, etc.

        Important: This should be pure Python logic. No LLM calls. The behavior
        should be deterministic (given the same state and tick, produce same output)
        or use controlled randomness (with seed for reproducibility).

        Args:
            state: Current world state at the start of this tick
            tick: Current tick number (0-indexed)

        Returns:
            Updated world state with physics applied

        Example implementation:
            new_state = state.model_copy(deep=True)

            # Consume oxygen: agents * 0.0000096 kg/s * 10 seconds
            oxygen_consumed = len(new_state.agents) * 0.0000096 * 10
            new_state.resources.oxygen_kg -= oxygen_consumed

            # Degrade equipment
            for agent in new_state.agents:
                agent.health *= 0.999  # 0.1% degradation per tick

            # Probabilistic dust storm
            if random.random() < 0.001:  # 0.1% chance per tick
                new_state.events.append(create_dust_storm_event(tick))

            return new_state
        """
        pass

    @abstractmethod
    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        """
        Check if an agent action is physically possible given current world state.

        This method is called after agents propose actions but before those actions
        are processed. It enforces physical constraints without requiring LLM calls.

        Important: This should be fast and deterministic. No LLM calls.

        Args:
            action: The action an agent wants to take
            state: Current world state

        Returns:
            True if action is valid and can be executed, False otherwise

        Example implementation:
            if action.action_type == "repair":
                # Check if agent has required skill
                agent = get_agent_by_id(state, action.agent_id)
                if "repair" not in agent.skills:
                    return False

                # Check if spare parts available
                if state.resources.spare_parts <= 0:
                    return False

                return True

            elif action.action_type == "move":
                # Check if destination exists and has capacity
                target_room = action.parameters.get("room")
                if target_room not in state.environment.rooms:
                    return False

                room_capacity = state.environment.rooms[target_room]["capacity"]
                current_occupants = count_agents_in_room(state, target_room)
                if current_occupants >= room_capacity:
                    return False

                return True

            # Default: allow action
            return True
        """
        pass

    def customize_perception(
        self,
        agent_id: str,
        perception: "AgentPerception",
        world_state: WorldState,
    ) -> "AgentPerception":
        """Allow deterministic rules to adjust the agent's perception payload.

        Scenarios can override this hook to add or modify perception fields
        (e.g., prepend summary lines, redact information, or inject
        deterministic telemetry). Default implementation returns the
        perception unchanged.
        """

        return perception

    def should_stop(self, state: WorldState, tick: int) -> bool:
        """Return True to halt the simulation loop after the current tick.

        Override in subclasses when deterministic physics can detect terminal
        conditions (e.g., game over, mission success). Default behavior keeps
        the simulation running for the requested number of ticks.

        Args:
            state: World state after the current tick has completed
            tick: Tick number that just finished processing

        Returns:
            True if orchestrator.run() should stop before the next tick
        """

        return False

    def get_tick_duration_seconds(self) -> int:
        """
        Get the duration of one tick in simulated seconds.

        Override this method to customize tick duration for your simulation.
        Research suggests 10-30 seconds works well for human-scale activities.

        Returns:
            Number of simulated seconds per tick (default: 10)

        Examples:
            - 1 second: Real-time simulation
            - 10 seconds: Human activity (default)
            - 60 seconds: Strategic planning
            - 3600 seconds: Macro-level simulation
        """
        return 10

    def on_simulation_start(self, state: WorldState) -> WorldState:
        """
        Hook called once at simulation start, before first tick.

        Override this method to perform any initialization logic needed
        for your simulation rules.

        Args:
            state: Initial world state

        Returns:
            Potentially modified world state

        Example:
            # Set up initial equipment states
            state.environment.equipment_health = {
                "life_support": 100.0,
                "power_generator": 100.0,
            }
            return state
        """
        return state

    def on_simulation_end(self, state: WorldState, tick: int) -> WorldState:
        """
        Hook called once at simulation end, after final tick.

        Override this method to perform any cleanup or final calculations
        needed for your simulation.

        Args:
            state: Final world state
            tick: Final tick number

        Returns:
            Potentially modified world state

        Example:
            # Calculate final statistics
            state.metadata["total_oxygen_consumed"] = calculate_total(state)
            return state
        """
        return state

    def format_resource_summary(self, state: WorldState) -> str:
        """Return a printable resource summary for the orchestrator output.

        Subclasses can override to provide domain-specific labels or metrics.
        """

        return format_resources_generic(state)

    # ------------------------------------------------------------
    # Optional deterministic action processing (advanced)
    # ------------------------------------------------------------
    def process_actions(
        self, state: WorldState, actions: list[AgentAction], tick: int
    ) -> WorldState | None:
        """
        Optionally process agent actions deterministically and return a new state.

        Default implementation returns None, signaling the orchestrator to use the
        configured world engine (LLM) or basic deterministic updates. Override this
        in rules-heavy simulations (e.g., vote tallying, game mechanics) where LLM
        should not be used for world updates.

        Args:
            state: Current world state (after apply_tick)
            actions: Validated agent actions for this tick
            tick: Tick number being processed

        Returns:
            WorldState if handled deterministically, otherwise None to delegate.
        """

        return None
