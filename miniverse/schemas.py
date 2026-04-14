"""
Pydantic schemas for Varela simulation system.

All data structures used in the simulation are defined here.

Design Philosophy:
- Generic `Stat` model for all metrics (no domain-specific fields)
- Composable via inheritance (EnvironmentState, ResourceState extend MetricsBlock)
- Metadata fields for scenario-specific extensions
- Pydantic validation ensures data integrity across persistence layers
"""

from pydantic import BaseModel, Field

from miniverse.environment import (
    EnvironmentGraphState,
    EnvironmentGridState,
    GridTileState,
)
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime
from uuid import UUID


# ============================================================================
# World State Schemas
# ============================================================================

# StatValue allows metrics to represent diverse data types (numeric KPIs, boolean flags,
# string labels, etc.) without rigid schema constraints. This flexibility supports
# simulations ranging from factory dashboards (numeric) to social networks (text).
StatValue = Union[int, float, str, bool]


class Stat(BaseModel):
    """Generic metric used across environments, resources, and agents.

    The Stat model is the fundamental building block for ALL simulation metrics.
    Rather than baking domain-specific fields into schemas, we use Stat to represent
    everything: temperature, energy, morale, stress, inventory counts, etc.

    This design enables:
    - Domain flexibility (same schema for factories, habitats, offices, etc.)
    - Easy serialization (JSON-compatible)
    - Scenario-specific extensions via metadata
    - Consistent access patterns across different metric types
    """

    value: StatValue = Field(..., description="Current value of the metric")
    unit: Optional[str] = Field(None, description="Optional unit label (%, kWh, °C, etc.)")
    label: Optional[str] = Field(None, description="Human-friendly name for UI or prompts")
    description: Optional[str] = Field(None, description="Optional explanation of the metric")
    # Metadata allows scenarios to attach custom data without modifying core schema.
    # Example: {"formula": "a + b", "source": "sensor_3", "confidence": 0.95}
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Free-form scenario metadata")


class MetricsBlock(BaseModel):
    """Container for a set of metrics keyed by arbitrary identifiers.

    MetricsBlock provides a flexible key-value store for Stat objects. Used as base
    class for EnvironmentState, ResourceState, and AgentStatus to provide consistent
    metric access patterns. The get_metric() helper auto-creates missing metrics
    with defaults, simplifying deterministic rules that expect certain metrics to exist.
    """

    metrics: Dict[str, Stat] = Field(default_factory=dict, description="Keyed metrics")
    # Metadata stores scenario-level data that doesn't fit metric pattern. Examples:
    # simulation config, domain-specific flags, computed aggregates, etc.
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Scenario-defined metadata")

    def get_metric(
        self,
        key: str,
        *,
        default: Optional[StatValue] = None,
        unit: Optional[str] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Stat:
        """Return the Stat for the requested metric, optionally creating it.

        This helper simplifies deterministic rules by auto-creating metrics with sensible
        defaults. Instead of checking existence every time, rules can call get_metric()
        and trust the metric will exist. Auto-generated labels use title-cased key names
        (e.g., "task_backlog" → "Task Backlog") for human-friendly displays.

        Raises:
            KeyError: If metric not found and no default provided (forces explicit defaults)
        """

        if key not in self.metrics:
            # Metric doesn't exist - check if caller provided default value
            if default is None:
                # No default - raise error to force explicit handling. This prevents
                # silent bugs where code expects metric but scenario didn't populate it.
                raise KeyError(f"Metric '{key}' not found")
            # Auto-create metric with default value. Label defaults to prettified key name
            # for human readability in UI/logs ("power_kwh" → "Power Kwh").
            self.metrics[key] = Stat(
                value=default,
                unit=unit,
                label=label or key.replace("_", " ").title(),
                description=description,
            )
        return self.metrics[key]


class EnvironmentState(MetricsBlock):
    """Environmental conditions exposed by the simulation world.

    Stores shared environmental metrics that all agents can potentially observe
    (based on their access rights). Examples: temperature, humidity, time of day,
    ambient noise, lighting conditions, weather. Deterministic rules update these
    each tick to simulate environmental changes (day/night cycles, seasonal effects).
    """


class ResourceState(MetricsBlock):
    """Shared resources for the scenario (inventory, budgets, etc.).

    Stores consumable/producible resources managed by the simulation. Examples:
    - Inventory counts (widgets produced, materials remaining)
    - Budgets (money, energy credits, ration points)
    - Capacities (storage space, processing power, bandwidth)

    Agents interact with resources through actions (consume, produce, transfer).
    Deterministic rules enforce constraints (can't consume more than available).
    """


class AgentStatus(BaseModel):
    """Current status of an agent in the simulation.

    Tracks dynamic state that changes tick-to-tick: location, activity, attributes (health,
    stress, etc.). Complements static AgentProfile (personality, goals, skills). Status is
    stored in WorldState and updated by deterministic rules + world engine. Agents observe
    their own status in perception but may have limited visibility into other agents' status
    (partial observability).

    Relationship to AgentProfile:
    - AgentProfile = static personality (who the agent IS)
    - AgentStatus = dynamic state (what the agent IS DOING and their current condition)
    """

    agent_id: str = Field(..., description="Unique agent identifier")
    # Display name allows scenarios to override agent_id for prettier logs ("Alice" vs "agent_42")
    display_name: Optional[str] = Field(None, description="Optional override for printing")
    role: Optional[str] = Field(None, description="Short summary of agent role")
    # Location enables spatial reasoning and partial observability (agents only see nearby entities)
    # For Tier-0/Tier-1 environments, location is a named zone (e.g., "habitat", "workshop")
    # For Tier-2 grids, location may reference a semantic area while grid_position holds coordinates
    location: Optional[str] = Field(None, description="Current location or zone")
    # Grid position for Tier-2 spatial environments [row, col]. None for Tier-0/Tier-1.
    # Enables collision detection, pathfinding, and proximity-based partial observability.
    # Scenarios can maintain both location (semantic name) and grid_position (spatial coords)
    # to support hybrid systems (e.g., "kitchen" at [12, 34])
    # Using List instead of Tuple for OpenAI function schema compatibility
    grid_position: Optional[List[int]] = Field(
        None,
        description="Optional [row, col] coordinates for Tier-2 grid environments",
        min_length=2,
        max_length=2,
    )
    # Activity tracks what agent is doing this tick (updated by world engine after action selection)
    activity: Optional[str] = Field(None, description="Current activity or task")
    # Attributes store per-agent metrics (health, stress, focus, quota_met, etc.)
    # Using Stat model provides units, labels, metadata for rich UI/prompts
    attributes: Dict[str, Stat] = Field(
        default_factory=dict, description="Per-agent metrics (health, stress, quota, etc.)"
    )
    # Tags enable filtering/grouping (e.g., ["morning_shift", "engineering_team", "veteran"])
    tags: List[str] = Field(default_factory=list, description="Extra labels (shift, faction, etc.)")
    # Metadata stores scenario-specific data (inventory items, relationship scores, quest state)
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Scenario-defined metadata for this agent"
    )

    def get_attribute(
        self,
        key: str,
        *,
        default: Optional[StatValue] = None,
        unit: Optional[str] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Stat:
        """Return a Stat for the requested attribute, optionally creating it.

        Mirrors MetricsBlock.get_metric() pattern for consistency. Deterministic rules
        use this to safely access/update agent attributes without checking existence.
        Auto-creation with defaults prevents KeyError spam in rules.
        """

        if key not in self.attributes:
            if default is None:
                # No default - raise error to force explicit handling in rules
                raise KeyError(f"Attribute '{key}' not found for agent {self.agent_id}")
            # Auto-create attribute with default value and prettified label
            self.attributes[key] = Stat(
                value=default,
                unit=unit,
                label=label or key.replace("_", " ").title(),
                description=description,
            )
        return self.attributes[key]

    # Convenience accessors for common metrics ---------------------------------
    # These properties provide type-safe, ergonomic access to frequently-used attributes.
    # Instead of `agent.get_attribute("health").value`, can use `agent.health`.
    # Auto-creates with defaults to prevent KeyError in simple scenarios.

    @property
    def health(self) -> float:
        """Health percentage (0-100). Auto-creates if missing."""
        return float(self.get_attribute("health", default=0, unit="%", label="Health").value)

    @health.setter
    def health(self, value: float) -> None:
        """Set health percentage. Creates attribute if doesn't exist."""
        self.get_attribute("health", default=value, unit="%", label="Health").value = value

    @property
    def stress(self) -> float:
        """Stress percentage (0-100). Auto-creates if missing."""
        return float(self.get_attribute("stress", default=0, unit="%", label="Stress").value)

    @stress.setter
    def stress(self, value: float) -> None:
        """Set stress percentage. Creates attribute if doesn't exist."""
        self.get_attribute("stress", default=value, unit="%", label="Stress").value = value

    @property
    def energy(self) -> float:
        """Energy percentage (0-100). Auto-creates if missing."""
        return float(self.get_attribute("energy", default=0, unit="%", label="Energy").value)

    @energy.setter
    def energy(self, value: float) -> None:
        """Set energy percentage. Creates attribute if doesn't exist."""
        self.get_attribute("energy", default=value, unit="%", label="Energy").value = value

    @property
    def current_activity(self) -> Optional[str]:  # pragma: no cover - simple alias
        """Alias for .activity field (backward compatibility)."""
        return self.activity

    @current_activity.setter
    def current_activity(self, value: Optional[str]) -> None:  # pragma: no cover
        """Alias for .activity field (backward compatibility)."""
        self.activity = value


class WorldEvent(BaseModel):
    """An event emitted by the simulation world.

    Events represent significant occurrences that affect agent decision-making: system alerts,
    environmental changes, agent interactions, emergent phenomena. World engine generates events
    in response to agent actions or deterministic rules. Events are stored in WorldState.recent_events
    and pruned after a few ticks to keep state size manageable.

    Event flow:
    1. Deterministic rules or world engine detects event condition
    2. Creates WorldEvent with category, severity, affected agents
    3. Adds to WorldState.recent_events
    4. Orchestrator converts events to memories for affected agents
    5. Agents observe events in next tick's perception
    """

    event_id: str = Field(..., description="Unique event identifier")
    tick: int = Field(..., ge=0, description="Tick when event occurred")
    # Category enables filtering (e.g., show only "critical_alert" events in UI)
    category: str = Field(..., description="Domain-specific category (outage, morale, etc.)")
    # Description is natural language for agent perception and memory storage
    description: str = Field(..., description="Human-readable description")
    # Severity drives memory importance (high severity → high importance → more likely to influence decisions)
    severity: Optional[int] = Field(
        None,
        description="Optional severity ranking (1-10 or scenario-defined scale)",
    )
    # Affected agents receive this event as memory with elevated importance
    affected_agents: List[str] = Field(
        default_factory=list, description="Agent IDs directly affected"
    )
    # Metrics attach quantitative data to events (e.g., {"damage": Stat(value=25, unit="%")})
    metrics: Dict[str, Stat] = Field(
        default_factory=dict, description="Metrics captured or impacted by this event"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional contextual data")


class WorldState(BaseModel):
    """Complete state of the simulation world at a specific tick.

    WorldState is the canonical representation of the simulation at a point in time.
    It's immutable-by-convention - each tick produces a new WorldState rather than
    mutating the previous one. This enables:
    - Time travel (rewind to any tick)
    - Branching (fork from tick N, explore alternatives)
    - Debugging (inspect state at failure point)

    State composition:
    - environment: Shared environmental metrics (temperature, time, weather)
    - resources: Consumable/producible resources (inventory, budgets)
    - agents: Dynamic status of all agents (location, activity, attributes)
    - recent_events: Recent world events (pruned after few ticks)
    - environment_graph: Optional Tier 1 logical locations (rooms, teams)
    - environment_grid: Optional Tier 2 spatial tiles (2D/3D maps)

    Serialization:
    - Pydantic enables JSON serialization for all persistence backends
    - Large states (100+ agents) compress well due to Stat reuse
    - Metadata fields allow scenarios to extend without schema changes
    """

    tick: int = Field(..., ge=0, description="Current simulation tick")
    # Timestamp tracks simulated time (not wall-clock time). Scenarios decide tick→time mapping
    # (e.g., 1 tick = 1 minute, 1 hour, 1 day). Used for day/night cycles, scheduling.
    timestamp: datetime = Field(..., description="Simulated timestamp")
    environment: EnvironmentState = Field(..., description="Environmental conditions")
    resources: ResourceState = Field(..., description="Shared resource pools")
    # Agents list contains dynamic status for ALL agents. Order doesn't matter (use agent_id to find).
    agents: List[AgentStatus] = Field(..., description="Status of all agents")
    # Recent events are kept for few ticks (typically 3-5) then pruned to prevent unbounded growth.
    # Orchestrator converts events to memories before pruning so information isn't lost.
    recent_events: List[WorldEvent] = Field(
        default_factory=list, description="Recent events (typically last few ticks)"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Scenario-defined world metadata"
    )
    # Environment graph provides Tier 1 logical structure (rooms/teams with adjacency/capacity).
    # None for Tier 0 simulations (KPI-only). Populated for spatial reasoning scenarios.
    environment_graph: Optional[EnvironmentGraphState] = Field(
        None,
        description="Tier 1 logical environment graph (rooms/teams/etc.)",
    )
    # Environment grid provides Tier 2 spatial structure (tile map with collision/objects).
    # None for Tier 0/1 simulations. Populated for Stanford-style spatial scenarios.
    environment_grid: Optional[EnvironmentGridState] = Field(
        None,
        description="Tier 2 spatial grid description",
    )


# ============================================================================
# Agent Schemas
# ============================================================================


class AgentProfile(BaseModel):
    """Static personality profile of an agent (WHO the agent IS, not what they're DOING).

    AgentProfile captures the immutable identity and character of an agent based on
    Stanford Generative Agents research. This separates static traits (personality,
    background, skills) from dynamic state (location, activity, health in AgentStatus).

    Key distinction:
    - AgentProfile = Static identity (loaded once from scenario, never changes)
    - AgentStatus = Dynamic state (changes every tick based on actions/rules)

    This separation enables:
    - Personality-driven behavior (LLM uses profile to stay in character)
    - Relationship modeling (agents remember social connections)
    - Goal-oriented planning (agents pursue consistent objectives)
    - Skill-based constraints (deterministic rules validate actions against skills)

    Stanford pattern: "Generative agents create believable simulacra of human behavior
    for interactive applications" - profile provides the stable foundation for emergent
    behavior to build upon.
    """

    agent_id: str = Field(..., description="Unique agent identifier")
    # Name provides human-friendly reference for prompts and logs
    name: str = Field(..., description="Agent's full name")
    age: Optional[int] = Field(None, ge=18, le=70, description="Age in years (optional, for human agents)")
    # Background written as first-person interview gives LLM context for character consistency.
    # Example: "I joined the mission because my family... I've always been..."
    background: str = Field(
        ..., description="Detailed backstory in interview style (first-person narrative)"
    )
    # Role determines default behaviors and responsibilities in deterministic rules
    role: str = Field(
        ...,
        description="Role (medical_officer, engineer, commander_botanist, geologist, etc.)",
    )
    # Personality affects decision-making style in LLM prompts. Based on Big Five traits.
    personality: str = Field(
        ..., description="Personality type (agreeable, neurotic, reactive, social)"
    )
    # Skills dict enables deterministic validation ("Can agent X repair system Y?")
    # Format: {"skill_name": "proficiency_level"}
    skills: Dict[str, str] = Field(
        ...,
        description="Skills and proficiency levels (expert, advanced, intermediate, basic)",
    )
    # Goals drive agent planning and action selection in LLM prompts
    goals: List[str] = Field(..., description="Personal and role-based goals")
    # Relationships enable social reasoning (cooperate with friends, avoid conflicts)
    # Format: {other_agent_id: "relationship description"}
    relationships: Dict[str, str] = Field(
        ..., description="Relationships with other agents {agent_id: description}"
    )


class AgentAction(BaseModel):
    """A discrete action taken by an agent in a single simulation tick.

    AgentAction represents the output of agent cognition - after perceiving the world
    and considering options, the agent commits to ONE action per tick. This action is
    then validated by deterministic rules and processed by the world engine to update
    world state.

    Action lifecycle:
    1. Agent cognition generates proposed action (LLM decision)
    2. SimulationRules.validate_action() checks if physically possible
    3. World engine processes valid action (updates WorldState)
    4. Action stored in persistence for history/replay
    5. Action converted to memory for agent's own reflection

    Design rationale:
    - reasoning field captures LLM's decision process (enables debugging and agent learning)
    - communication field separates social actions from physical actions
    - parameters dict allows domain-specific action data without schema changes
    - Pydantic validation ensures all actions have required fields
    """

    agent_id: str = Field(..., description="Agent performing the action")
    tick: int = Field(..., ge=0, description="Tick when action was taken")
    # action_type drives deterministic rule validation and world engine processing.
    # Common types: work, rest, communicate, repair, monitor, investigate, move
    action_type: str = Field(
        ...,
        description="Type of action (work, rest, communicate, repair, monitor, investigate)",
    )
    # target identifies what the action affects (another agent, equipment, location).
    # None for self-directed actions (rest, reflect).
    target: Optional[str] = Field(
        None, description="Target of action (system, agent, location, equipment)"
    )
    # parameters stores action-specific data (e.g., {"duration": 60, "priority": "high"})
    # Enables rich actions without hardcoding every possible action type in schema
    parameters: Optional[Dict[str, Union[str, int, float, bool]]] = Field(
        None, description="Action-specific parameters"
    )
    # reasoning preserves LLM's decision process for debugging and agent self-reflection.
    # Example: "Health critical, need rest before continuing repairs"
    reasoning: str = Field("", description="Why this action was chosen")
    # communication stores message content when action_type involves messaging.
    # Format: {"to": "other_agent_id", "message": "text content"}
    # Enables social coordination without separate message queue
    communication: Optional[Dict[str, str]] = Field(
        None, description="Communication content if action includes messaging"
    )


class VisibleGridTile(BaseModel):
    """A tile visible to the agent within their local grid view."""

    position: Tuple[int, int] = Field(..., description="(x, y) coordinate of the tile")
    tile: GridTileState = Field(..., description="Metadata describing the tile contents")


class GridVisibility(BaseModel):
    """Container describing the agent's local grid view."""

    center: Tuple[int, int] = Field(..., description="Agent grid position used as visibility center")
    radius: int = Field(..., ge=0, description="Visibility radius in tiles (Chebyshev distance)")
    tiles: List[VisibleGridTile] = Field(
        default_factory=list,
        description="Tiles within the visible window around the agent",
    )


class AgentPerception(BaseModel):
    """What a specific agent can perceive in a single tick (partial observability pattern).

    AgentPerception implements the Stanford Generative Agents pattern of partial observability -
    agents only know what they can realistically perceive, not the complete world state. This
    is critical for believable behavior: real people don't have omniscient knowledge.

    What agents CAN perceive:
    - Their own status (health, stress, energy) - introspection
    - Their current location - spatial awareness
    - Base-wide resource levels - displayed on screens/dashboards
    - High-severity events - broadcast alerts
    - Direct messages sent to them - social communication
    - Recent memories - context for decision-making

    What agents CANNOT perceive:
    - Other agents' internal attributes (health, stress) unless communicated
    - Events in distant locations (unless severity >= 5 triggers broadcast)
    - Complete event history (only recent_events in WorldState)
    - Future state or deterministic rule outcomes

    Design rationale:
    - Enforces information asymmetry (enables secrets, rumors, misunderstandings)
    - Prevents "meta-gaming" where agents act on info they shouldn't have
    - Mirrors Stanford research finding: "Partial observability creates emergent coordination"
    - build_agent_perception() constructs this filtered view from complete WorldState
    """

    tick: int = Field(..., ge=0, description="Current tick")
    # location enables spatial reasoning ("I'm in the lab, can't access reactor")
    location: Optional[str] = Field(None, description="Agent's current location")
    # grid_position provides the agent's coordinates in Tier 2 environments (if applicable)
    grid_position: Optional[List[int]] = Field(
        None,
        min_length=2,
        max_length=2,
        description="Agent's grid coordinates [x, y] when environment_grid is present",
    )
    # personal_attributes = what agent feels/knows about themselves (introspection)
    personal_attributes: Dict[str, Stat] = Field(
        default_factory=dict, description="Self-reported attributes (health, morale, etc.)"
    )
    # visible_resources = shared metrics displayed on dashboards (oxygen tanks, power levels)
    # All agents see same resource state - information is public
    visible_resources: Dict[str, Stat] = Field(
        default_factory=dict, description="Shared resources visible to this agent"
    )
    # environment_snapshot = ambient conditions (temperature, time of day, weather)
    environment_snapshot: Dict[str, Stat] = Field(
        default_factory=dict, description="Relevant environment metrics"
    )
    # system_alerts = high-severity events (severity >= 5) broadcast to all agents
    # Lower severity events require direct observation (being in same location)
    system_alerts: List[str] = Field(
        default_factory=list, description="Current system warnings and alerts"
    )
    # messages = communication directed AT this agent (filtered from all actions)
    # Format: [{"from": "sender_id", "message": "content"}]
    messages: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Messages sent to this agent {from: agent_id, message: str}",
    )
    # recent_observations = last N memories providing context for decision-making
    # Stanford pattern: agents use memory stream to inform current actions
    recent_observations: List[str] = Field(
        default_factory=list,
        description="Recent memories (last 10 observations from memory stream)",
    )
    # grid_visibility describes the local window of tiles around the agent in Tier 2 grids
    grid_visibility: Optional[GridVisibility] = Field(
        None,
        description="Visible grid window with tile metadata for spatial environments",
    )
    # ASCII rendering removed from core schema; scenarios should add custom context via
    # SimulationRules.customize_perception() when they need human-readable grids.


# ============================================================================
# Database Schemas
# ============================================================================


class SimulationRun(BaseModel):
    """Metadata tracking for a single simulation run (persistence record).

    SimulationRun stores high-level metadata about a simulation execution for historical
    tracking and analysis. Used by all persistence backends (InMemory, JSON, Postgres).

    Purpose:
    - Track multiple simulation runs (experiments, parameter sweeps, debugging)
    - Store configuration for reproducibility
    - Monitor run status (running/completed/failed)
    - Enable querying ("show me all runs from last week")

    Lifecycle:
    1. Created before simulation starts (status="running")
    2. Updated each tick by persistence backend
    3. Finalized when simulation completes (status="completed", end_time set)
    4. Queried for analysis/replay

    Design rationale:
    - UUID id enables distributed simulation (no collision risk)
    - config dict stores LLM settings, scenario name, physics rules for reproducibility
    - Wall-clock times (start_time, end_time) track real execution duration
    - num_ticks/num_agents enable filtering runs by scale
    """

    id: UUID = Field(..., description="Unique run identifier")
    # start_time = wall-clock time when simulation began (not simulated time)
    start_time: datetime = Field(..., description="When simulation started (wall-clock)")
    # end_time = wall-clock time when simulation finished (None if still running)
    end_time: Optional[datetime] = Field(None, description="When simulation ended (wall-clock)")
    # num_ticks = planned duration (set at start), may differ from actual if crashed
    num_ticks: int = Field(..., ge=0, description="Number of ticks to run")
    num_agents: int = Field(..., ge=1, description="Number of agents in simulation")
    # status tracks execution state: "running", "completed", "failed"
    status: str = Field(
        ..., description="Run status (running, completed, failed)"
    )
    # config stores all settings needed to reproduce this run: LLM model, scenario, physics
    # Example: {"scenario": "mars_base", "llm_model": "gpt-4", "use_physics": true}
    config: Dict = Field(..., description="Simulation configuration for reproducibility")
    created_at: datetime = Field(..., description="When record was created")


class AgentMemory(BaseModel):
    """A single memory in an agent's memory stream (Stanford Generative Agents pattern).

    AgentMemory implements the memory stream pattern from Stanford research: agents accumulate
    natural language observations over time, which inform future decisions. Each memory is a
    timestamped, importance-weighted record that can be retrieved and reflected upon.

    Stanford pattern: "The memory stream is a database of an agent's experiences, stored as
    natural language. Memories have importance scores that decay over time, and agents retrieve
    relevant memories when making decisions."

    Memory types:
    - observation: Perceived events ("Oxygen levels dropped to 15%")
    - action: Agent's own actions ("I repaired the water recycler")
    - communication: Messages received ("Alice asked me to check the greenhouse")
    - reflection: Higher-order summaries ("Team morale declining due to isolation")

    Importance scoring (1-10):
    - 1-3: Mundane observations (routine status checks)
    - 4-6: Notable events (equipment repairs, conversations)
    - 7-9: Significant events (emergencies, conflicts, breakthroughs)
    - 10: Life-changing events (disasters, major discoveries)

    Future extensions:
    - embedding_key: Reference to vector store for semantic retrieval
    - branch_id: Support for timeline branching (loom pattern)
    - tags: Enable filtering by topic/entity for focused retrieval
    """

    id: UUID = Field(..., description="Unique memory identifier")
    run_id: UUID = Field(..., description="Simulation run this memory belongs to")
    agent_id: str = Field(..., description="Agent who owns this memory")
    # tick provides temporal ordering (memories sorted by recency)
    tick: int = Field(..., ge=0, description="Tick when memory was created")
    # memory_type drives retrieval strategy (e.g., recent actions vs recent conversations)
    memory_type: str = Field(
        ...,
        description="Type of memory (observation, action, communication, reflection)",
    )
    # content = natural language description (fed to LLM in agent prompts)
    content: str = Field(..., description="Memory content (natural language)")
    # importance = Stanford pattern for weighting retrieval (high importance → more influence)
    # Severity from events maps to importance (high severity event → high importance memory)
    importance: int = Field(
        ..., ge=1, le=10, description="Importance score (1-10, Stanford pattern)"
    )
    # tags enable topic-based retrieval (e.g., ["equipment", "oxygen_system"])
    tags: List[str] = Field(
        default_factory=list,
        description="Optional labels for retrieval (topics, entities, etc.)",
    )
    # metadata stores structured data for custom retrieval (e.g., {"mentioned_agents": [...]})
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary structured metadata for custom retrievers",
    )
    # embedding_key = future integration with vector stores for semantic search
    # Current memory strategies use recency/importance, but semantic search would enable
    # "find memories related to conflict" rather than "find last 10 memories"
    embedding_key: Optional[str] = Field(
        None,
        description="Reference to an external embedding/vector store entry",
    )
    # branch_id = future support for timeline branching (loom pattern)
    # Enables "what if" scenarios: fork at tick 50, explore alternative paths
    branch_id: Optional[str] = Field(
        None,
        description="Timeline identifier for branching/loom scenarios",
    )
    created_at: datetime = Field(..., description="When record was created")
    # last_accessed tracks when this memory was last retrieved for use in cognition.
    # Enables Stanford-style exponential decay: memories fade unless re-accessed.
    # None means never accessed (only created).
    last_accessed_tick: Optional[int] = Field(
        None,
        description="Tick when this memory was last retrieved (for importance decay)",
    )


class SimulationError(BaseModel):
    """Error tracking record for debugging simulation failures.

    SimulationError captures exceptions and failures during simulation execution for
    post-mortem analysis. Stored by persistence backends to enable debugging without
    losing error context.

    Use cases:
    - LLM errors (rate limits, invalid JSON, timeout)
    - Validation errors (agent proposes impossible action)
    - Physics errors (deterministic rules raise exception)
    - Persistence errors (database connection lost)

    Error handling strategy:
    1. Orchestrator catches exception during tick processing
    2. Creates SimulationError record with context
    3. Persists error for later analysis
    4. Optionally continues simulation (for transient errors) or halts (for fatal errors)
    5. User queries errors to diagnose failures

    Design rationale:
    - tick field enables pinpointing when failure occurred (replay up to tick N-1)
    - error_type enables grouping similar failures (e.g., "all rate limit errors")
    - stack_trace preserves full context for debugging
    - Separate from logging - these are stored permanently for analysis
    """

    id: UUID = Field(..., description="Unique error identifier")
    run_id: UUID = Field(..., description="Simulation run where error occurred")
    # tick enables time-travel debugging (replay up to failing tick)
    tick: int = Field(..., ge=0, description="Tick when error occurred")
    # error_type categorizes errors (LLMError, ValidationError, PhysicsError, etc.)
    error_type: str = Field(..., description="Type of error (exception class name)")
    # error_message = human-readable description
    error_message: str = Field(..., description="Error message")
    # stack_trace = full Python traceback for debugging
    stack_trace: Optional[str] = Field(None, description="Full stack trace if available")
    created_at: datetime = Field(..., description="When record was created")
