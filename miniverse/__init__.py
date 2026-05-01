"""
Miniverse - LLM-driven agent-based simulation library.

Create emergent behavior simulations with LLM-powered agents.

Phase 2: Fully decoupled library.
No file I/O required. No database required. No global config.
All dependencies injected by user.
"""

__version__ = "0.2.0"

# Main simulation components
from .orchestrator import Orchestrator

# Core interfaces
from .simulation_rules import SimulationRules, format_resources_generic
from .persistence import (
    PersistenceStrategy,
    InMemoryPersistence,
    PostgresPersistence,
    JsonPersistence,
)
from .memory import MemoryStrategy, SimpleMemoryStream, ImportanceWeightedMemory, BM25MemoryStrategy
from .cognition import (
    AgentCognition,
    AgentCognitionMap,
    build_default_cognition,
    Scratchpad,
    Planner,
    Plan,
    PlanStep,
    Executor,
    ReflectionEngine,
    ReflectionResult,
    PromptContext,
    PromptLibrary,
    DEFAULT_PROMPTS,
)
from .environment import (
    EnvironmentGraph,
    EnvironmentGrid,
    EnvironmentGraphState,
    EnvironmentGridState,
    GridTile,
    GridTileState,
    LocationNode,
    LocationNodeState,
    GraphOccupancy,
    shortest_path,
    grid_shortest_path,
)

# Core schemas
from .schemas import (
    WorldState,
    EnvironmentState,
    ResourceState,
    AgentStatus,
    WorldEvent,
    AgentProfile,
    AgentAction,
    OutgoingMessage,
    StepDecision,
    AgentPerception,
    GridVisibility,
    VisibleGridTile,
    SimulationRun,
    AgentMemory,
    Stat,
)

# Scenario loader helpers
from .scenario import load_scenario, ScenarioLoader

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
    "BM25MemoryStrategy",
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
    "OutgoingMessage",
    "StepDecision",
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
