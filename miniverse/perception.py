"""
Perception construction module implementing Stanford partial observability pattern.

This module builds AgentPerception objects that filter complete WorldState into what
a specific agent can realistically observe. Partial observability is a cornerstone
of the Stanford Generative Agents research - believable behavior requires information
asymmetry, not omniscience.

Perception filtering rules (what agents CAN perceive):
- Their own status (health, stress, energy) - introspection
- Their current location - spatial awareness
- Base-wide resource levels (displayed on screens/dashboards) - public information
- System alerts (severity >= 5) - broadcast alerts
- Direct messages sent to them - social communication
- Recent memories (last 10) - context for decision-making

Perception filtering rules (what agents CANNOT perceive):
- Other agents' internal attributes (health, stress) unless communicated
- Events in distant locations (unless high severity triggers broadcast)
- Complete event history (only recent_events buffer)
- Future state or deterministic rule outcomes

Design rationale:
- Enforces information asymmetry (enables secrets, rumors, misunderstandings)
- Prevents "meta-gaming" where agents act on info they shouldn't have
- Creates emergent coordination (agents must communicate to share information)
- Mirrors Stanford finding: "Partial observability essential for believability"

Usage:
    perception = build_agent_perception(
        agent_id="alice",
        world_state=current_state,
        recent_messages=[{"from": "bob", "message": "Hi Alice"}],
        recent_memories=memory_stream[-10:]
    )
    # perception now contains ONLY what alice can perceive
"""

from typing import Dict, List, Tuple

from miniverse.environment import get_visible_tiles, render_ascii_window
from miniverse.schemas import (
    AgentPerception,
    GridVisibility,
    VisibleGridTile,
    WorldState,
)

DEFAULT_GRID_VISIBILITY_RADIUS = 2


def build_agent_perception(
    agent_id: str,
    world_state: WorldState,
    recent_messages: List[Dict[str, str]],
    recent_memories: List[str],
) -> AgentPerception:
    """Build partial observability perception for a specific agent.

    Constructs an AgentPerception by filtering the complete WorldState through the lens
    of what this agent can realistically perceive. This is the critical information
    bottleneck that creates emergent coordination and believable behavior.

    Data flow:
    1. Find agent's current status in world state (or raise if missing)
    2. Deep copy agent's own attributes (introspection)
    3. Deep copy shared resources (public dashboards)
    4. Deep copy environment metrics (ambient conditions)
    5. Filter events to high-severity broadcasts (severity >= 5)
    6. Include direct messages (derived from memory stream, not actions)
    7. Include recent memories for context
    8. Return filtered AgentPerception object

    Deep copying rationale:
    - Perception snapshots are immutable (prevent accidental mutations)
    - Agent cognition cannot modify world state through perception reference
    - Enables safe parallel processing (multiple agents, same world state)

    Message handling:
    - Messages are sourced from the agent's memory stream (recipient entries)
    - Prevents eavesdropping on private communications
    - Enables private coordination vs public announcements

    System alert filtering:
    - Severity threshold of 5 determines broadcast vs local events
    - Example: Severity 3 "minor equipment noise" - not broadcast
    - Example: Severity 8 "oxygen system failure" - broadcast to all

    Args:
        agent_id: ID of agent to build perception for
        world_state: Current complete world state (omniscient view)
        recent_messages: Direct messages for THIS agent (from memory)
        recent_memories: Recent memory strings for THIS agent (last 10)

    Returns:
        AgentPerception with only what this agent can perceive

    Raises:
        ValueError: If agent_id not found in world state (likely logic error)

    Example:
        >>> perception = build_agent_perception(
        ...     agent_id="alice",
        ...     world_state=current_state,
        ...     recent_messages=[{"from": "bob", "message": "Expect gusts over 25 km/h."}],
        ...     recent_memories=["Repaired oxygen tank", "Spoke with Bob"]
        ... )
        >>> # perception.personal_attributes contains only Alice's health/stress
        >>> # perception.messages contains only messages TO Alice
        >>> # perception.system_alerts contains only severity >= 5 events
    """
    # Find this agent's status in the world state
    agent_status = None
    for agent in world_state.agents:
        if agent.agent_id == agent_id:
            agent_status = agent
            break

    if agent_status is None:
        # Agent not found - likely caller error (typo in agent_id, or agent removed from state)
        raise ValueError(f"Agent {agent_id} not found in world state")

    # Deep copy agent's own attributes (introspection - agents know their own state)
    # Clone prevents agent cognition from accidentally mutating world state
    personal_attributes = {
        key: stat.model_copy(deep=True) for key, stat in agent_status.attributes.items()
    }

    # Deep copy shared resources (public information - displayed on dashboards)
    # All agents see same resource state (oxygen levels, power, etc.)
    visible_resources = {
        key: stat.model_copy(deep=True)
        for key, stat in world_state.resources.metrics.items()
    }

    # Deep copy environment metrics (ambient conditions - temperature, time, weather)
    # All agents perceive same environmental state
    environment_snapshot = {
        key: stat.model_copy(deep=True)
        for key, stat in world_state.environment.metrics.items()
    }

    # Extract system alerts (only high-severity events broadcast to all agents)
    # Severity threshold of 5: minor events are local, major events are broadcast
    system_alerts = [
        event.description
        for event in world_state.recent_events
        if event.severity is not None and event.severity >= 5
    ]

    # Use provided messages (already filtered from memory for this agent)
    messages = recent_messages

    # Use provided recent memories (already filtered to last 10 by memory strategy)
    # Provides context for decision-making without overwhelming LLM prompt
    recent_observations = list(recent_memories)

    # Determine grid visibility for Tier 2 environments (if configured)
    grid_position: List[int] | None = None
    grid_visibility: GridVisibility | None = None
    if world_state.environment_grid and agent_status.grid_position:
        grid_position = list(agent_status.grid_position)
        center: Tuple[int, int] = (grid_position[0], grid_position[1])

        # Allow scenarios to override visibility radius via metadata
        radius = DEFAULT_GRID_VISIBILITY_RADIUS
        if isinstance(world_state.metadata, dict):
            radius = int(world_state.metadata.get("grid_visibility_radius", radius))
        if isinstance(agent_status.metadata, dict):
            radius = int(agent_status.metadata.get("grid_visibility_radius", radius))

        tiles = get_visible_tiles(
            world_state.environment_grid,
            center,
            radius=radius,
        )
        visible_tiles = [
            VisibleGridTile(position=pos, tile=tile)
            for pos, tile in sorted(tiles.items())
        ]
        grid_visibility = GridVisibility(
            center=center, radius=radius, tiles=visible_tiles
        )
        grid_ascii = render_ascii_window(
            world_state.environment_grid,
            center,
            radius=radius,
        )

        # Default builder adds an ASCII snapshot for human/LLM readability.
        # Scenario-specific hooks can override recent_observations entirely if desired.
        recent_observations.insert(0, f"GRID ASCII:\n{grid_ascii}")

    return AgentPerception(
        tick=world_state.tick,
        personal_attributes=personal_attributes,
        location=agent_status.location,
        grid_position=grid_position,
        visible_resources=visible_resources,
        environment_snapshot=environment_snapshot,
        system_alerts=system_alerts,
        messages=messages,
        recent_observations=recent_observations,
        grid_visibility=grid_visibility,
        grid_ascii=None,
    )
