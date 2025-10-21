"""
Miniverse - LLM-driven agent-based simulation library.

Create emergent behavior simulations with LLM-powered agents.

Phase 2: Fully decoupled library.
No file I/O required. No database required. No global config.
All dependencies injected by user.
"""

__version__ = "0.2.0"

from .cognition import (
    DEFAULT_PROMPTS,
    AgentCognition,
    AgentCognitionMap,
    Executor,
    Plan,
    Planner,
    PlanStep,
    PromptContext,
    PromptLibrary,
    ReflectionEngine,
    ReflectionResult,
    Scratchpad,
    build_default_cognition,
)
from .environment import (
    EnvironmentGraph,
    EnvironmentGraphState,
    EnvironmentGrid,
    EnvironmentGridState,
    GraphOccupancy,
    GridTile,
    GridTileState,
    LocationNode,
    LocationNodeState,
    grid_shortest_path,
    shortest_path,
)
from .memory import ImportanceWeightedMemory, MemoryStrategy, SimpleMemoryStream

# Main simulation components
from .orchestrator import Orchestrator
from .persistence import (
    InMemoryPersistence,
    JsonPersistence,
    PersistenceStrategy,
    PostgresPersistence,
)

# Scenario loader helpers
from .scenario import ScenarioLoader, load_scenario

# Core schemas
from .schemas import (
    AgentAction,
    AgentMemory,
    AgentPerception,
    AgentProfile,
    AgentStatus,
    EnvironmentState,
    GridVisibility,
    ResourceState,
    SimulationRun,
    Stat,
    VisibleGridTile,
    WorldEvent,
    WorldState,
)

# Core interfaces
from .simulation_rules import SimulationRules, format_resources_generic

__all__ = [
    # Main class
    "Orchestrator",
    # Core interfaces
    "SimulationRules",
    "PersistenceStrategy",
    "InMemoryPersistence",
    "PostgresPersistence",
    "JsonPersistence",
    "MemoryStrategy",
    "SimpleMemoryStream",
    "ImportanceWeightedMemory",
    "AgentCognition",
    "AgentCognitionMap",
    "build_default_cognition",
    "Scratchpad",
    "Planner",
    "Plan",
    "PlanStep",
    "Executor",
    "ReflectionEngine",
    "ReflectionResult",
    "PromptContext",
    "PromptLibrary",
    "DEFAULT_PROMPTS",
    # World schemas
    "WorldState",
    "EnvironmentState",
    "ResourceState",
    "AgentStatus",
    "WorldEvent",
    "Stat",
    # Agent schemas
    "AgentProfile",
    "AgentAction",
    "AgentPerception",
    "GridVisibility",
    "VisibleGridTile",
    # Database schemas
    "SimulationRun",
    "AgentMemory",
    # Scenario helpers
    "load_scenario",
    "ScenarioLoader",
    # Utilities
    "format_resources_generic",
    # Environment helpers
    "EnvironmentGraph",
    "EnvironmentGrid",
    "EnvironmentGraphState",
    "EnvironmentGridState",
    "GridTile",
    "GridTileState",
    "LocationNode",
    "LocationNodeState",
    "GraphOccupancy",
    "shortest_path",
    "grid_shortest_path",
]
