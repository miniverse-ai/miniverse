"""
Scenario loading and management for declarative simulation initialization.

This module provides ScenarioLoader for converting JSON/YAML scenario files into
WorldState and AgentProfile objects. Scenarios define the initial conditions for
a simulation:
- Agent profiles (personality, skills, relationships)
- Agent starting status (location, health, activity)
- Environment state (temperature, time, weather)
- Resource levels (oxygen, power, food)
- Optional starting events (system failures, alerts)
- Optional environment graph (Tier 1 logical locations)
- Optional environment grid (Tier 2 spatial tiles)

Design philosophy:
- Scenarios are data (JSON/YAML), not code - enables non-programmers to create simulations
- Validation ensures required fields present (prevents runtime errors)
- Flexible schema via metadata fields (scenarios can extend without code changes)
- Stat auto-conversion (simple values → full Stat objects with labels/units)

Scenario file structure:
```yaml
{
  "name": "Mars Base Alpha",
  "description": "...",
  "initial_timestamp": "2157-03-15T08:00:00",
  "agents": [
    {
      "profile": {"agent_id": "alice", "name": "Alice", ...},
      "status": {"location": "habitat", "attributes": {...}}
    }
  ],
  "environment": {"metrics": {"temperature": -60, ...}},
  "resources": {"metrics": {"oxygen_kg": 850.0, ...}},
  "environment_graph": {"nodes": {...}, "adjacency": {...}},
  "initial_events": [...]
}
```

Usage:
    loader = ScenarioLoader()
    world_state, agent_profiles = loader.load("mars_base")
    # Pass to Orchestrator to start simulation
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime

from .config import Config
from .scenario_files import (
    list_scenario_names,
    load_structured_data_file,
    resolve_scenario_file,
)
from .schemas import (
    WorldState,
    AgentProfile,
    EnvironmentState,
    ResourceState,
    AgentStatus,
    WorldEvent,
    Stat,
)
from .environment import (
    EnvironmentGraphState,
    EnvironmentGridState,
    LocationNodeState,
    GridTileState,
)


class ScenarioLoader:
    """Load and validate simulation scenarios from JSON/YAML files.

    ScenarioLoader handles all parsing, validation, and conversion of scenario data
    into Pydantic models (WorldState, AgentProfile). Scenarios provide declarative
    initialization - users define initial state in a data file rather than writing Python code.

    Directory structure:
    - Default: {PROJECT_ROOT}/examples/scenarios/
    - Override via constructor: ScenarioLoader(Path("/custom/scenarios"))
    - Scenario files: {scenario_name}.yaml/.yml/.json

    Validation:
    - Required fields: name, description, agents, environment, resources
    - Each agent must have both "profile" and "status" blocks
    - At least one agent required (can't simulate empty world)
    - Raises ValueError if validation fails (early failure better than cryptic runtime errors)

    Stat auto-conversion:
    - Simple values (850.0) → Stat(value=850.0)
    - Full Stat objects ({"value": 850, "unit": "kg"}) → Stat model
    - Enables both concise and detailed scenario definitions
    """

    def __init__(self, scenarios_dir: Optional[Path] = None):
        """Initialize scenario loader.

        Args:
            scenarios_dir: Directory containing scenario files.
                          Defaults to {PROJECT_ROOT}/examples/scenarios
        """
        self.scenarios_dir = scenarios_dir or (Config.PROJECT_ROOT / "examples" / "scenarios")

    def load(self, scenario_name: str) -> Tuple[WorldState, List[AgentProfile]]:
        """Load a scenario by name from JSON/YAML file.

        Main entry point for scenario loading. Reads scenario file, validates structure,
        parses agents/environment/resources, and constructs WorldState + AgentProfile list.

        Data flow:
        1. Resolve {scenario_name}.yaml/.yml/.json from scenarios directory
        2. Validate required fields (raises ValueError if invalid)
        3. Parse agent profiles and status for each agent
        4. Build initial WorldState from environment/resources/events
        5. Return (WorldState, [AgentProfile, ...]) tuple

        Args:
            scenario_name: Name of scenario (without extension) or filename.
                          Example: "mars_base" loads "mars_base.yaml" if present.

        Returns:
            Tuple of (initial WorldState at tick 0, list of AgentProfiles)
            Ready to pass to Orchestrator for simulation

        Raises:
            FileNotFoundError: If scenario file doesn't exist in scenarios_dir
            ValueError: If scenario data missing required fields or malformed
        """
        scenario_path = resolve_scenario_file(self.scenarios_dir, scenario_name)
        data = load_structured_data_file(scenario_path)

        # Validate required fields
        self._validate_scenario(data)

        # Parse agents
        agents: List[AgentProfile] = []
        agent_statuses: List[AgentStatus] = []

        for agent_entry in data["agents"]:
            profile_data = agent_entry["profile"]
            status_data = agent_entry["status"]

            profile = AgentProfile(**profile_data)
            agents.append(profile)

            status = self._parse_agent_status(status_data, profile.agent_id)
            agent_statuses.append(status)

        # Parse initial world state
        world_state = self._build_world_state(data, agent_statuses)

        return world_state, agents

    def _validate_scenario(self, data: Dict) -> None:
        """Validate scenario data has required fields.

        Args:
            data: Scenario data

        Raises:
            ValueError: If required fields are missing
        """
        required = ["name", "description", "agents", "environment", "resources"]
        missing = [field for field in required if field not in data]

        if missing:
            raise ValueError(f"Scenario missing required fields: {missing}")

        if not data["agents"]:
            raise ValueError("Scenario must have at least one agent")

        for agent in data["agents"]:
            if "profile" not in agent or "status" not in agent:
                raise ValueError(
                    "Each agent entry must include 'profile' and 'status' blocks"
                )

    def _build_world_state(self, data: Dict, agent_statuses: List[AgentStatus]) -> WorldState:
        """Build initial WorldState from scenario data.

        Args:
            data: Scenario data

        Returns:
            Initial WorldState
        """
        # Parse environment
        environment = self._parse_environment(data.get("environment", {}))

        # Parse resources
        resources = self._parse_resources(data.get("resources", {}))

        environment_graph = None
        if "environment_graph" in data:
            environment_graph = self._parse_environment_graph(data["environment_graph"])

        environment_grid = None
        if "environment_grid" in data:
            environment_grid = self._parse_environment_grid(data["environment_grid"])

        # Build agent statuses from agent data
        # Parse initial events if provided
        initial_events = []
        if "initial_events" in data:
            for event_data in data["initial_events"]:
                event = self._parse_event(event_data)
                initial_events.append(event)

        # Get starting timestamp
        timestamp_str = data.get("initial_timestamp", "2157-03-15T08:00:00")
        timestamp = datetime.fromisoformat(timestamp_str)

        return WorldState(
            tick=0,
            timestamp=timestamp,
            environment=environment,
            resources=resources,
            agents=agent_statuses,
            recent_events=initial_events,
            metadata=data.get("metadata", {}),
            environment_graph=environment_graph,
            environment_grid=environment_grid,
        )

    def _parse_metrics(self, raw: Dict[str, Any]) -> Dict[str, Stat]:
        """Convert raw metric dict into Stat objects.

        Auto-converts between concise and detailed metric formats:
        - Simple: {"oxygen_kg": 850.0} → {"oxygen_kg": Stat(value=850.0)}
        - Detailed: {"oxygen_kg": {"value": 850, "unit": "kg", "label": "Oxygen"}}
                   → {"oxygen_kg": Stat(value=850, unit="kg", label="Oxygen")}

        This flexibility enables:
        - Quick prototypes (simple values)
        - Production scenarios (full Stat objects with units/labels/metadata)
        - Mixed formats in same scenario (use detail where needed)

        Args:
            raw: Raw metrics dictionary from scenario file

        Returns:
            Dict mapping metric keys to Stat objects
        """

        metrics: Dict[str, Stat] = {}
        for key, value in raw.items():
            # Check if value is already a Stat-like dict (has "value" field)
            if isinstance(value, dict) and "value" in value:
                # Full Stat object - unpack fields into Stat constructor
                metrics[key] = Stat(**value)
            else:
                # Simple value - wrap in Stat with just value field
                metrics[key] = Stat(value=value)
        return metrics

    def _parse_environment(self, data: Dict[str, Any]) -> EnvironmentState:
        metrics = self._parse_metrics(data.get("metrics", {}))
        return EnvironmentState(
            metrics=metrics,
            metadata=data.get("metadata", {}),
        )

    def _parse_resources(self, data: Dict[str, Any]) -> ResourceState:
        metrics = self._parse_metrics(data.get("metrics", {}))
        return ResourceState(
            metrics=metrics,
            metadata=data.get("metadata", {}),
        )

    def _parse_agent_status(
        self, data: Dict[str, Any], fallback_agent_id: str
    ) -> AgentStatus:
        # Parse per-agent metrics (health, stress, etc.) into Stat objects.
        # Auto-converts simple values → Stat(value=...) or detailed dicts → Stat objects.
        attributes = self._parse_metrics(data.get("attributes", {}))

        # Parse optional grid_position for Tier-2 spatial scenarios.
        # Accepts [row, col] list, (row, col) tuple, or {"row": int, "col": int} dict.
        # Returns list[int] for schema compatibility (OpenAI doesn't support Tuple schemas).
        grid_position = None
        if "grid_position" in data:
            raw_pos = data["grid_position"]
            if isinstance(raw_pos, (list, tuple)) and len(raw_pos) == 2:
                grid_position = [int(raw_pos[0]), int(raw_pos[1])]
            elif isinstance(raw_pos, dict) and "row" in raw_pos and "col" in raw_pos:
                grid_position = [int(raw_pos["row"]), int(raw_pos["col"])]

        return AgentStatus(
            agent_id=data.get("agent_id", fallback_agent_id),
            display_name=data.get("display_name"),
            role=data.get("role"),
            location=data.get("location"),
            grid_position=grid_position,
            activity=data.get("activity"),
            attributes=attributes,
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

    def _parse_event(self, data: Dict[str, Any]) -> WorldEvent:
        metrics = self._parse_metrics(data.get("metrics", {}))
        return WorldEvent(
            event_id=data["event_id"],
            tick=data["tick"],
            category=data.get("category", "event"),
            description=data["description"],
            severity=data.get("severity"),
            affected_agents=data.get("affected_agents", []),
            metrics=metrics,
            metadata=data.get("metadata", {}),
        )

    def _parse_environment_graph(self, data: Dict[str, Any]) -> EnvironmentGraphState:
        nodes: Dict[str, LocationNodeState] = {}
        raw_nodes = data.get("nodes", {})
        # Support both dict-keyed format and list-of-objects format:
        #   dict: {market: {name: Neon Market, capacity: 8}}
        #   list: [{id: market, label: Neon Market, capacity: 8}]
        if isinstance(raw_nodes, list):
            for item in raw_nodes:
                node_id = item.get("id", "")
                nodes[node_id] = LocationNodeState(
                    name=item.get("label", item.get("name", node_id)),
                    capacity=item.get("capacity"),
                    metadata=item.get("metadata", {}),
                )
        elif isinstance(raw_nodes, dict):
            for key, value in raw_nodes.items():
                if isinstance(value, dict):
                    nodes[key] = LocationNodeState(
                        name=value.get("name", key),
                        capacity=value.get("capacity"),
                        metadata=value.get("metadata", {}),
                    )
                else:
                    nodes[key] = LocationNodeState(name=key)

        adjacency: Dict[str, List[str]] = {}
        raw_adj = data.get("adjacency", {})
        # Support both dict format and list-of-pairs format:
        #   dict: {market: [clinic, lounge]}
        #   list: [[market, clinic], [market, lounge]]
        if isinstance(raw_adj, list):
            for pair in raw_adj:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    a, b = pair
                    adjacency.setdefault(a, []).append(b)
                    adjacency.setdefault(b, []).append(a)
        elif isinstance(raw_adj, dict):
            for key, neighbors in raw_adj.items():
                if isinstance(neighbors, (list, tuple)):
                    adjacency[key] = list(neighbors)
                else:
                    adjacency[key] = []

        return EnvironmentGraphState(nodes=nodes, adjacency=adjacency)

    def _parse_environment_grid(self, data: Dict[str, Any]) -> EnvironmentGridState:
        width = int(data.get("width", 0))
        height = int(data.get("height", 0))
        tiles_data = data.get("tiles", {})
        tiles: Dict[Tuple[int, int], GridTileState] = {}

        if isinstance(tiles_data, dict):
            for key, value in tiles_data.items():
                if isinstance(key, str) and "," in key:
                    row_str, col_str = key.split(",", 1)
                    row, col = int(row_str), int(col_str)
                else:
                    continue
                tile_kwargs = value if isinstance(value, dict) else {}
                tiles[(row, col)] = GridTileState(**tile_kwargs)
        elif isinstance(tiles_data, list):
            for item in tiles_data:
                if not isinstance(item, dict):
                    continue
                if "coordinate" in item and isinstance(item["coordinate"], (list, tuple)):
                    row, col = item["coordinate"]
                else:
                    row = item.get("row")
                    col = item.get("col")
                if row is None or col is None:
                    continue
                tile_kwargs = {
                    "world": item.get("world"),
                    "sector": item.get("sector"),
                    "arena": item.get("arena"),
                    "game_object": item.get("game_object"),
                    "collision": item.get("collision", False),
                    "metadata": item.get("metadata", {}),
                }
                tiles[(int(row), int(col))] = GridTileState(**tile_kwargs)

        return EnvironmentGridState(width=width, height=height, tiles=tiles)

    def list_scenarios(self) -> List[str]:
        """List all available scenario files.

        Returns:
            List of scenario names (without extension)
        """
        return list_scenario_names(self.scenarios_dir)

    def get_scenario_info(self, scenario_name: str) -> Dict[str, str]:
        """Get scenario metadata without loading full scenario.

        Args:
            scenario_name: Name of scenario

        Returns:
            Dict with name, description, num_agents, etc.
        """
        scenario_path = resolve_scenario_file(self.scenarios_dir, scenario_name)
        data = load_structured_data_file(scenario_path)

        return {
            "name": data.get("name", scenario_name),
            "description": data.get("description", "No description"),
            "num_agents": len(data.get("agents", [])),
            "recommended_ticks": data.get("recommended_ticks", 50),
        }


def load_scenario(scenario_name: str) -> Tuple[WorldState, List[AgentProfile]]:
    """Convenience function to load a scenario.

    Args:
        scenario_name: Name of scenario to load

    Returns:
        Tuple of (initial WorldState, list of AgentProfiles)
    """
    loader = ScenarioLoader()
    return loader.load(scenario_name)
