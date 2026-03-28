"""
Main simulation orchestrator.

Fully decoupled from file I/O, database, and config.
All dependencies are injected by the user.

Coordinates the simulation loop:
1. Apply deterministic physics (if simulation_rules provided)
2. Build agent perceptions (partial observability)
3. Gather agent actions in parallel (LLM calls)
4. Process world update (World Engine LLM call)
5. Persist via injected strategy
6. Update agent memories via injected strategy
"""

import asyncio
import os
import re
import shutil
import textwrap
from typing import Any, Callable, List, Dict, Optional
from uuid import UUID, uuid4

from .perception import build_agent_perception
from .llm_calls import process_world_update
from .schemas import AgentProfile, WorldState, AgentAction, AgentMemory
from .simulation_rules import SimulationRules, format_resources_generic
from .persistence import PersistenceStrategy, InMemoryPersistence
from .memory import MemoryStrategy, SimpleMemoryStream, BM25MemoryStrategy
from .logging_utils import (
    colored,
    Color,
    LOG_TAG_DETERMINISTIC,
    LOG_TAG_LLM,
    LOG_TAG_SUCCESS,
)
from .cognition import (
    AgentCognition,
    AgentCognitionMap,
    PromptContext,
    build_default_cognition,
    build_prompt_context,
    DEFAULT_PROMPTS,
    LLMPlanner,
    LLMExecutor,
    LLMReflectionEngine,
)
from .cognition.cadence import PLANNER_LAST_TICK_KEY, REFLECTION_LAST_TICK_KEY
from .cognition.planner import Plan, PlanStep


# Log verbosity levels for orchestrator progress output.
_LOG_MODE_LEVELS: Dict[str, int] = {
    "none": 0,
    "concise": 1,
    "verbose": 2,
}

_SIDECAR_HISTORY_KEY = "__sidecar_history"
_SIDECAR_WINDOW_TICKS = 3
_SIDECAR_MAX_IN_WINDOW = 2
_COMM_MEMORY_CAP_SIDE_CAR = 3


def _env_enabled(name: str) -> bool:
    """Return True only for explicit truthy env flag values."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


# =============================
# Module-level Exceptions
# =============================

class AgentActionsFailedError(Exception):
    """Raised when one or more agent actions fail during a tick.

    Contains a mapping of agent_id to the underlying exception for better
    diagnostics, along with guidance on common remediation steps.
    """

    def __init__(self, *, tick: int, errors: Dict[str, Exception]) -> None:
        self.tick = tick
        self.errors = errors
        message_lines = [
            f"One or more agent actions failed at tick {tick}.",
            "Agents that failed:",
        ]
        for agent_id, exc in errors.items():
            message_lines.append(f"  - {agent_id}: {exc}")
        message_lines.extend(
            [
                "\nRemediation tips:",
                "  - Verify LLM configuration (LLM_PROVIDER, LLM_MODEL, API key)",
                "  - Enable DEBUG_LLM=true to inspect prompts/responses",
                "  - Enable DEBUG_PERCEPTION=true to inspect agent inputs",
                "  - Enable MINIVERSE_VERBOSE=true to see reasoning",
            ]
        )
        super().__init__("\n".join(message_lines))


class WorldEngineUnavailableError(Exception):
    """Raised when World Engine LLM is not configured but required."""

    def __init__(self, *, tick: int, reason: str) -> None:
        self.tick = tick
        self.reason = reason
        message = (
            f"World Engine unavailable at tick {tick}: {reason}\n\n"
            "Remediation tips:\n"
            "  - Set LLM_PROVIDER and LLM_MODEL environment variables\n"
            "  - Ensure API key env var is set for your provider\n"
            "  - DEBUG_LLM=true to inspect prompts/responses if still failing"
        )
        super().__init__(message)


class WorldEngineValidationError(Exception):
    """Raised when World Engine LLM response fails validation after retries."""

    def __init__(self, *, tick: int, underlying: Exception) -> None:
        self.tick = tick
        self.underlying = underlying
        message = (
            f"World Engine validation failed at tick {tick}: {underlying}\n\n"
            "The LLM response remained invalid after retries.\n"
            "Remediation tips:\n"
            "  - DEBUG_LLM=true to inspect prompts and validation feedback\n"
            "  - Review world prompt and response schema\n"
            "  - Consider simplifying the request or adjusting constraints"
        )
        super().__init__(message)


class Orchestrator:
    """
    Main simulation orchestrator.

    Fully decoupled - accepts all dependencies as parameters.
    No file I/O, no database requirement, no global config.
    """

    def __init__(
        self,
        world_state: WorldState,
        agents: Dict[str, AgentProfile],
        world_prompt: str,
        agent_prompts: Dict[str, str],
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        simulation_rules: Optional[SimulationRules] = None,
        world_update_mode: str = "auto",
        persistence: Optional[PersistenceStrategy] = None,
        memory: Optional[MemoryStrategy] = None,
        agent_cognition: Optional[AgentCognitionMap] = None,
        tick_listeners: Optional[
            List[Callable[[int, WorldState, WorldState, List[AgentAction]], None]]
        ] = None,
        log_mode: str = "verbose",
    ):
        """Initialize orchestrator with all dependencies injected.

        Args:
            world_state: Initial WorldState
            agents: Dict mapping agent_id to AgentProfile
            world_prompt: System prompt for world engine
            agent_prompts: Dict mapping agent_id to system prompt
            llm_provider: Optional LLM provider name (e.g., "openai", "anthropic")
            llm_model: Optional model identifier (e.g., "gpt-5-nano")
            simulation_rules: Optional SimulationRules for deterministic physics
            persistence: Optional persistence strategy (defaults to InMemory)
            memory: Optional memory strategy (defaults to BM25MemoryStrategy)
            agent_cognition: Optional mapping of agent_id to AgentCognition;
                defaults provide empty planner/executor/reflection stacks.
            tick_listeners: Optional callables invoked after each tick for
                additional analysis or logging. Each listener receives
                (tick, previous_state, new_state, actions).
            log_mode: Progress output verbosity. One of:
                - "none": no tick-level logs (final result only)
                - "concise": compact per-tick progress/action summaries
                - "verbose": detailed step-by-step logs
        """
        self.current_state = world_state
        self.agents = agents
        self.world_prompt = world_prompt
        self.agent_prompts = agent_prompts
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.simulation_rules = simulation_rules
        self.world_update_mode = world_update_mode  # 'auto' | 'deterministic' | 'llm'
        self._world_llm_warning_emitted = False

        # Use defaults if strategies not provided. InMemoryPersistence is fastest for
        # prototyping but data is lost after run. BM25MemoryStrategy provides proper
        # information retrieval with BM25 scoring, recency decay, and importance weighting.
        self.persistence = persistence or InMemoryPersistence()
        self.memory = memory or BM25MemoryStrategy(self.persistence)

        # Prepare cognition modules per agent. Each agent gets a bundle containing
        # planner (generates multi-step plans), executor (chooses tick actions),
        # reflection engine (periodically summarizes experiences), and scratchpad
        # (working memory for plan state). If user doesn't provide custom cognition,
        # build_default_cognition() returns simple deterministic implementations.
        cognition_map: AgentCognitionMap = {}
        for agent_id in agents:
            if agent_cognition and agent_id in agent_cognition:
                # User provided custom cognition for this agent
                cognition_map[agent_id] = agent_cognition[agent_id]
            else:
                # Fall back to default cognition (deterministic, no-op reflection)
                cognition_map[agent_id] = build_default_cognition()
        self.agent_cognition: AgentCognitionMap = cognition_map

        # Tick listeners are optional callbacks invoked after each tick completes.
        # They receive (tick, previous_state, new_state, actions) for analysis/logging.
        self.tick_listeners = tick_listeners or []

        if log_mode not in _LOG_MODE_LEVELS:
            raise ValueError(
                f"Invalid log_mode '{log_mode}'. "
                f"Expected one of: {', '.join(sorted(_LOG_MODE_LEVELS))}"
            )
        self.log_mode = log_mode
        self._total_ticks = 0

        # Give simulation rules a chance to modify initial state (e.g., compute
        # derived metrics, initialize tracking structures). Not all rules need this.
        if self.simulation_rules:
            self.current_state = self.simulation_rules.on_simulation_start(
                self.current_state
            )

        # Generate unique ID for this simulation run. All persistence/memory operations
        # tag data with this ID so multiple runs can coexist in the same backend.
        self.run_id: UUID = uuid4()

        # Preflight cache for one-time warnings per template
        self._prompt_warnings_emitted: Dict[str, bool] = {}

    # (no inner exception classes)

    def _get_scratchpad_state(self, cognition: AgentCognition) -> dict:
        """Safely get scratchpad state dict (returns empty dict if scratchpad is None)."""
        if cognition.scratchpad is None:
            return {}
        return cognition.scratchpad.state

    def _resolve_agent_id(self, name_or_id: str) -> Optional[str]:
        """
        Map agent name or ID to agent_id.

        LLMs naturally use agent names ("Ayesha Khan") in communications,
        but our agent dictionary is keyed by agent_id ("ayesha").
        This helper resolves both formats.

        Args:
            name_or_id: Either agent_id ("ayesha") or display name ("Ayesha Khan")

        Returns:
            Agent ID if found, None otherwise
        """
        def normalize(value: str) -> str:
            return "".join(ch for ch in value.lower() if ch.isalnum())

        if not name_or_id:
            return None
        candidate = name_or_id.strip()

        # Check if already an agent_id
        if candidate in self.agents:
            return candidate

        normalized_candidate = normalize(candidate)

        # Search by normalized id or display name.
        for agent_id, profile in self.agents.items():
            if normalize(agent_id) == normalized_candidate:
                return agent_id
            if normalize(profile.name) == normalized_candidate:
                return agent_id

        return None

    def _is_sidecar_mode_enabled(self) -> bool:
        """Return True when scenario cognition mode allows sidecar communication."""
        metadata = self.current_state.metadata or {}
        runtime = metadata.get("runtime") if isinstance(metadata, dict) else None
        if not isinstance(runtime, dict):
            return False
        cognition = runtime.get("cognition")
        if not isinstance(cognition, dict):
            return False
        kwargs = cognition.get("kwargs")
        if not isinstance(kwargs, dict):
            return False
        mode = str(kwargs.get("communication_mode", "")).strip().lower()
        return mode == "sidecar"

    @staticmethod
    def _is_communication_memory_text(content: str) -> bool:
        text = content.lower()
        return " told " in text or text.startswith("i told ")

    @classmethod
    def _cap_communication_memories(
        cls, memories: List[str], *, max_communication: int
    ) -> List[str]:
        """Keep memory context diverse so communication chatter does not dominate."""
        if max_communication < 0:
            return memories
        communication_count = 0
        balanced: List[str] = []
        for memory in memories:
            if cls._is_communication_memory_text(memory):
                if communication_count >= max_communication:
                    continue
                communication_count += 1
            balanced.append(memory)
        return balanced

    @staticmethod
    def _normalize_sidecar_signature(recipient: str, message: str) -> str:
        normalized_message = re.sub(r"\s+", " ", message.strip().lower())
        return f"{recipient.strip().lower()}|{normalized_message}"

    def _apply_sidecar_budget(
        self,
        *,
        tick: int,
        agent_id: str,
        cognition: AgentCognition,
        action: AgentAction,
    ) -> None:
        """Constrain sidecar usage to reduce repetitive coordination chatter."""
        if not self._is_sidecar_mode_enabled():
            return
        if action.action_type == "communicate":
            return
        if not action.communication or not isinstance(action.communication, dict):
            return

        recipient = action.communication.get("to")
        message_raw = action.communication.get("message") or action.communication.get("content")
        message = str(message_raw or "").strip()
        if not recipient or not message:
            action.communication = None
            return

        # Sidecar is intended for execution follow-through, not replacing communication turns.
        if action.action_type not in {"work", "repair"}:
            action.communication = None
            return

        if len(message) > 220:
            message = f"{message[:217].rstrip()}..."
            action.communication["message"] = message

        history = self._get_scratchpad_value(cognition, _SIDECAR_HISTORY_KEY, [])
        if not isinstance(history, list):
            history = []

        window_start = tick - (_SIDECAR_WINDOW_TICKS - 1)
        compact_history: List[Dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            item_tick = item.get("tick")
            signature = item.get("signature")
            if not isinstance(item_tick, int) or not isinstance(signature, str):
                continue
            if item_tick >= window_start:
                compact_history.append({"tick": item_tick, "signature": signature})

        signature = self._normalize_sidecar_signature(str(recipient), message)
        if any(item["signature"] == signature for item in compact_history):
            action.communication = None
            self._set_scratchpad_value(cognition, _SIDECAR_HISTORY_KEY, compact_history)
            return

        if len(compact_history) >= _SIDECAR_MAX_IN_WINDOW:
            action.communication = None
            self._set_scratchpad_value(cognition, _SIDECAR_HISTORY_KEY, compact_history)
            return

        compact_history.append({"tick": tick, "signature": signature})
        self._set_scratchpad_value(cognition, _SIDECAR_HISTORY_KEY, compact_history)

    def _should_log(self, level: str) -> bool:
        """Return whether current log mode includes the requested level."""
        if level not in _LOG_MODE_LEVELS:
            return False
        return _LOG_MODE_LEVELS[self.log_mode] >= _LOG_MODE_LEVELS[level]

    def _log(self, message: str, *, level: str = "concise") -> None:
        """Print message if it meets the current log verbosity threshold."""
        if self._should_log(level):
            print(message, flush=True)

    @staticmethod
    def _wrap_for_log(
        text: str,
        *,
        prefix: str = "",
        continuation: Optional[str] = None,
        width: Optional[int] = None,
    ) -> str:
        """Wrap long log lines for terminal readability without truncation."""
        normalized = " ".join(str(text).split())
        if not normalized:
            return prefix.rstrip()
        if width is None:
            terminal_width = shutil.get_terminal_size(fallback=(112, 24)).columns
            width = max(76, min(140, terminal_width - 2))
        width = max(width, len(prefix) + 20)
        return textwrap.fill(
            normalized,
            width=width,
            initial_indent=prefix,
            subsequent_indent=continuation if continuation is not None else " " * len(prefix),
            break_long_words=False,
            break_on_hyphens=False,
        )

    @staticmethod
    def _format_stat_value_for_log(stat: Any) -> str:
        """Format Stat-like objects for concise perception logging."""
        if stat is None:
            return "n/a"
        value = getattr(stat, "value", stat)
        unit = getattr(stat, "unit", "") or ""
        if isinstance(value, float):
            rendered = f"{value:.1f}" if abs(value) >= 10 else f"{value:.2f}"
        else:
            rendered = str(value)
        return f"{rendered}{unit}" if unit else rendered

    async def _seed_initial_memories(self) -> int:
        """Optionally seed run-local memories from world metadata.

        Supported metadata shape:
        - world.metadata["initial_memories"] = {
            "<agent_id>": [
                {"content": "...", "memory_type": "observation", "importance": 6, ...},
                "plain string memory"
            ]
          }
        - or a flat list of dict entries containing `agent_id`.
        """
        payload = self.current_state.metadata.get("initial_memories")
        if not payload:
            return 0

        seeded_count = 0
        entries_by_agent: Dict[str, list[Any]] = {}

        if isinstance(payload, dict):
            for agent_id, entries in payload.items():
                if isinstance(entries, list):
                    entries_by_agent[agent_id] = entries
        elif isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                agent_id = item.get("agent_id")
                if not isinstance(agent_id, str):
                    continue
                entries_by_agent.setdefault(agent_id, []).append(item)

        for agent_id, entries in entries_by_agent.items():
            if agent_id not in self.agents:
                continue
            for entry in entries:
                if isinstance(entry, str):
                    content = entry.strip()
                    memory_type = "observation"
                    importance = 5
                    tags: list[str] = []
                    metadata: Dict[str, Any] = {}
                    tick = 0
                elif isinstance(entry, dict):
                    content = str(entry.get("content", "")).strip()
                    memory_type = str(entry.get("memory_type", "observation"))
                    importance = int(entry.get("importance", 5))
                    importance = max(1, min(10, importance))
                    tags = entry.get("tags", [])
                    if not isinstance(tags, list):
                        tags = []
                    metadata = entry.get("metadata", {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                    tick = int(entry.get("tick", 0))
                else:
                    continue

                if not content:
                    continue

                await self.memory.add_memory(
                    run_id=self.run_id,
                    agent_id=agent_id,
                    tick=max(0, tick),
                    memory_type=memory_type,
                    content=content,
                    importance=importance,
                    tags=tags,
                    metadata=metadata,
                )
                seeded_count += 1

        return seeded_count

    def _preflight_prompt_templates(self) -> None:
        """Warn once per missing template before the tick loop starts.

        Checks planner/executor/reflection template names requested by each agent's
        cognition. If a named template is missing from the configured prompt library,
        logs a one-time warning and indicates which default will be used instead.
        """
        for agent_id, cognition in self.agent_cognition.items():
            library = cognition.prompt_library or DEFAULT_PROMPTS

            # Planner
            if isinstance(cognition.planner, LLMPlanner):
                planner_obj = cognition.planner
                name = getattr(planner_obj, "template_name", None)
                # One-time notice: using default template
                if getattr(planner_obj, "template", None) is None and (name is None or name == "plan") and library is DEFAULT_PROMPTS:
                    key = f"planner_default:{agent_id}"
                    if not self._prompt_warnings_emitted.get(key):
                        self._log(
                            f"  [Prompts] Agent '{agent_id}' planner using default template 'plan'.",
                            level="verbose",
                        )
                        self._prompt_warnings_emitted[key] = True
                # Missing named template warning
                if name and name not in library.templates and not self._prompt_warnings_emitted.get(f"planner_missing:{name}"):
                    self._log(
                        f"  [Prompts] Agent '{agent_id}' planner template '{name}' not found; using default 'plan'.",
                        level="concise",
                    )
                    self._prompt_warnings_emitted[f"planner_missing:{name}"] = True

            # Executor
            if isinstance(cognition.executor, LLMExecutor):
                exec_obj = cognition.executor
                name = getattr(exec_obj, "template_name", None)
                # One-time notice: using default template
                if getattr(exec_obj, "template", None) is None and (name is None or name in ("default", "execute_tick")) and library is DEFAULT_PROMPTS:
                    key = f"executor_default:{agent_id}"
                    if not self._prompt_warnings_emitted.get(key):
                        self._log(
                            f"  [Prompts] Agent '{agent_id}' executor using default template '{'default' if name in (None, 'default') else name}'.",
                            level="verbose",
                        )
                        self._prompt_warnings_emitted[key] = True
                # Missing named template warning
                if name and name not in library.templates and not self._prompt_warnings_emitted.get(f"executor_missing:{name}"):
                    self._log(
                        f"  [Prompts] Agent '{agent_id}' executor template '{name}' not found; using default 'default'.",
                        level="concise",
                    )
                    self._prompt_warnings_emitted[f"executor_missing:{name}"] = True

            # Reflection
            if isinstance(cognition.reflection, LLMReflectionEngine):
                refl_obj = cognition.reflection
                name = getattr(refl_obj, "template_name", None)
                # One-time notice: using default template
                if getattr(refl_obj, "template", None) is None and (name is None or name == "reflect_diary") and library is DEFAULT_PROMPTS:
                    key = f"reflection_default:{agent_id}"
                    if not self._prompt_warnings_emitted.get(key):
                        self._log(
                            f"  [Prompts] Agent '{agent_id}' reflection using default template 'reflect_diary'.",
                            level="verbose",
                        )
                        self._prompt_warnings_emitted[key] = True
                # Missing named template warning
                if name and name not in library.templates and not self._prompt_warnings_emitted.get(f"reflection_missing:{name}"):
                    self._log(
                        f"  [Prompts] Agent '{agent_id}' reflection template '{name}' not found; using default 'reflect_diary'.",
                        level="concise",
                    )
                    self._prompt_warnings_emitted[f"reflection_missing:{name}"] = True

    def _describe_world_update_mode(self) -> str:
        """Return a one-line summary of the selected world update mode and reason."""
        mode = self.world_update_mode
        rules = self.simulation_rules
        if mode == "deterministic":
            if rules and getattr(rules, "process_actions").__func__ is not SimulationRules.process_actions:
                return "[Preflight] World updates: deterministic (rules.process_actions)"
            return "[Preflight] World updates: deterministic (basic)"
        if mode == "llm":
            return "[Preflight] World updates: LLM (fail-fast if misconfigured)"
        # auto mode
        if rules and getattr(rules, "process_actions").__func__ is not SimulationRules.process_actions:
            return "[Preflight] World updates (auto): deterministic via rules.process_actions"
        if self.llm_provider and self.llm_model:
            return "[Preflight] World updates (auto): LLM"
        return "[Preflight] World updates (auto): deterministic (basic)"

    def _set_scratchpad_value(self, cognition: AgentCognition, key: str, value: Any) -> None:
        """Safely set scratchpad value (no-op if scratchpad is None)."""
        if cognition.scratchpad is not None:
            cognition.scratchpad.state[key] = value

    def _get_scratchpad_value(self, cognition: AgentCognition, key: str, default: Any = None) -> Any:
        """Safely get scratchpad value (returns default if scratchpad is None)."""
        if cognition.scratchpad is None:
            return default
        return cognition.scratchpad.state.get(key, default)

    async def run(self, num_ticks: int) -> Dict:
        """Run simulation for N ticks.

        Args:
            num_ticks: Number of ticks to simulate

        Returns:
            Dict with run_id and final_state

        Raises:
            Exception: If simulation fails
        """
        # Initialize persistence and memory backends. Each backend (InMemory, JSON, Postgres)
        # may need setup (e.g., opening DB connections, creating directories). Initialization
        # is idempotent - safe to call multiple times.
        await self.persistence.initialize()
        await self.memory.initialize()

        try:
            # Save initial state at tick 0. This establishes baseline for debugging and allows
            # users to rewind simulations. All persistence operations tag data with run_id so
            # multiple simulation runs can coexist in the same backend.
            await self.persistence.save_state(self.run_id, 0, self.current_state)
            seeded_memories = await self._seed_initial_memories()
            if seeded_memories:
                self._log(
                    f"[Preflight] Seeded {seeded_memories} initial memory record(s).",
                    level="concise",
                )

            self._total_ticks = num_ticks

            self._log(f"Starting simulation run {self.run_id}", level="concise")
            self._log(f"Agents: {len(self.agents)}, Ticks: {num_ticks}\n", level="concise")

            # Preflight: warn early if requested prompt templates are missing
            self._preflight_prompt_templates()

            # Preflight: report world update mode
            self._log(self._describe_world_update_mode(), level="concise")

            # Main simulation loop. Each tick is independent - if one tick fails, we propagate
            # the exception immediately rather than continuing with corrupted state.
            ticks_completed = 0
            stopped_early = False
            for tick in range(1, num_ticks + 1):
                if self._should_log("verbose"):
                    self._log(f"=== Tick {tick}/{num_ticks} ===", level="verbose")

                try:
                    await self._run_tick(tick)
                except Exception as e:
                    self._log(f"ERROR at tick {tick}: {e}", level="concise")
                    raise

                ticks_completed = tick

                if self.simulation_rules and self.simulation_rules.should_stop(
                    self.current_state, tick
                ):
                    stopped_early = True
                    self._log(
                        f"\nSimulation stopped early at tick {tick} (signaled by simulation rules).",
                        level="concise",
                    )
                    break

            # Give simulation rules a chance to compute final statistics, aggregate metrics,
            # or clean up tracking structures. Not all rules need this hook.
            if self.simulation_rules:
                self.current_state = self.simulation_rules.on_simulation_end(
                    self.current_state, ticks_completed
                )

            if stopped_early:
                self._log(
                    "\nSimulation finished early (simulation rules signaled stop).",
                    level="concise",
                )
            else:
                self._log("\n✅ Simulation complete!", level="concise")

            return {"run_id": self.run_id, "final_state": self.current_state}

        finally:
            # Always cleanup persistence and memory backends even if simulation fails.
            # Ensures DB connections are closed, file handles released, etc.
            await self.persistence.close()
            await self.memory.close()

    async def _run_tick(self, tick: int) -> None:
        """Execute single tick.

        Args:
            tick: Current tick number
        """
        # Preserve previous state for tick listeners. Listeners compare before/after states
        # to detect emergent patterns, compute metrics, or trigger custom events.
        previous_state = self.current_state

        if self.log_mode == "concise":
            total = self._total_ticks or "?"
            self._log(
                f"Tick {tick}/{total}: perception -> planning -> action -> world -> persistence",
                level="concise",
            )

        # 0. Apply deterministic physics FIRST (if simulation_rules provided).
        # Physics runs BEFORE agent actions to ensure agents perceive the consequences of
        # previous tick's physics. Example: resource consumption, environmental decay,
        # scheduled events. Physics is pure Python (fast, testable, deterministic).
        if self.simulation_rules:
            self._log(
                colored(
                    f"  {LOG_TAG_DETERMINISTIC} [Physics] Applying deterministic rules for tick {tick}...",
                    Color.BLUE,
                ),
                level="verbose",
            )
            self.current_state = self.simulation_rules.apply_tick(
                self.current_state, tick
            )
            self._log(
                colored(f"  {LOG_TAG_SUCCESS} [Physics] Physics applied", Color.GREEN),
                level="verbose",
            )

        # 1. Gather agent actions in parallel. Each agent gets partial observability based on
        # their location and access rights. Running in parallel minimizes total LLM latency
        # (critical for large agent populations). Each agent's cognition stack independently
        # builds perceptions, retrieves memories, generates/advances plans, and selects actions.
        actions = await self._gather_agent_actions(tick)

        # 2. World engine processes all actions and returns updated state. The world engine
        # uses an LLM to resolve action conflicts, apply narrative consistency, and generate
        # emergent events. If LLM fails validation after retries, we fall back to deterministic
        # physics to prevent simulation crashes.
        new_state = await self._process_world_update(tick, actions)

        # 3. Persist tick data (state, actions, memories) to backend. Persistence happens
        # AFTER the world engine succeeds so we never save partial/corrupt data. Memory
        # creation includes reflection engine invocations for agents that hit their cadence.
        await self._persist_tick(tick, new_state, actions)

        # 4. Print human-readable summary for monitoring. Shows resource levels, agent actions,
        # and recent events. Helps users understand simulation progress without reading logs.
        self._print_tick_summary(tick, actions, new_state, previous_state)

        # 5. Update current state to serve as baseline for next tick. This happens AFTER
        # persistence to avoid race conditions between tick listeners and the next tick.
        self.current_state = new_state

        # 6. Invoke tick listeners for optional analysis/debugging. Listeners receive
        # (tick, prev_state, new_state, actions) to compute custom metrics, detect anomalies,
        # or trigger external integrations (e.g., logging to analytics platforms).
        # Listener failures are logged but don't crash the simulation.
        if self.tick_listeners:
            for listener in self.tick_listeners:
                try:
                    listener(tick, previous_state, new_state, actions)
                except Exception as exc:  # pragma: no cover - diagnostic hook
                    self._log(f"  [Analysis] Listener failed: {exc}", level="concise")

    async def _gather_agent_actions(self, tick: int) -> List[AgentAction]:
        """Get all agent actions in parallel.

        Args:
            tick: Current tick number

        Returns:
            List of agent actions
        """
        # Build perceptions and gather actions in parallel. Each agent's action decision is
        # independent (no coordination) so we run all agents concurrently to minimize total
        # LLM latency. With N agents and ~2s LLM latency, parallel execution takes ~2s total
        # vs ~2N seconds sequential.
        task_map: Dict[str, asyncio.Task[AgentAction]] = {}
        for agent_id in self.agents:
            task_map[agent_id] = asyncio.create_task(
                self._get_single_agent_action(agent_id, tick)
            )

        # For verbose LLM runs, emit intra-tick progress while waiting on agent decisions.
        # This prevents the appearance of "hanging" when first-tick LLM calls are slow.
        if (
            self._should_log("verbose")
            and self.llm_provider
            and self.llm_model
            and task_map
        ):
            pending: set[asyncio.Task[AgentAction]] = set(task_map.values())
            loop = asyncio.get_running_loop()
            started = loop.time()
            progress_interval_seconds = 10

            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    timeout=progress_interval_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending and not done:
                    elapsed = int(loop.time() - started)
                    total = len(task_map)
                    waiting = len(pending)
                    completed = total - waiting
                    self._log(
                        f"  [progress] Tick {tick}: waiting on {waiting}/{total} "
                        f"agent decisions ({elapsed}s elapsed, {completed} complete)",
                        level="verbose",
                    )
        else:
            await asyncio.wait(set(task_map.values()))

        # Fail-fast: if any agent action failed, raise an informative error with all failures.
        valid_actions: List[AgentAction] = []
        failures: Dict[str, Exception] = {}
        for agent_id, task in task_map.items():
            try:
                result = task.result()
            except Exception as exc:
                failures[agent_id] = exc
            else:
                valid_actions.append(result)

        if failures:
            raise AgentActionsFailedError(tick=tick, errors=failures)

        return valid_actions

    async def _get_single_agent_action(
        self, agent_id: str, tick: int
    ) -> AgentAction:
        """Get single agent's action decision.

        Args:
            agent_id: ID of agent
            tick: Current tick number

        Returns:
            AgentAction from LLM

        Raises:
            Exception: If LLM call fails
        """
        agent_name = self.agents[agent_id].name
        cognition = self.agent_cognition[agent_id]
        self._log(
            colored(
                f"  {LOG_TAG_DETERMINISTIC} [{agent_name}] Building perception...",
                Color.BLUE,
            ),
            level="verbose",
        )

        # A2: Do not read messages from actions. Messages are sourced from memories.

        # Get current plan step to use as memory retrieval query.
        # This enables BM25 to surface relevant memories based on what agent is doing.
        existing_plan = self._get_scratchpad_value(cognition, "plan")
        existing_plan_index = self._get_scratchpad_value(cognition, "plan_index", 0)
        memory_query = ""
        if isinstance(existing_plan, Plan) and existing_plan.steps:
            idx = min(existing_plan_index, len(existing_plan.steps) - 1)
            current_step = existing_plan.steps[idx]
            memory_query = current_step.description

        # Retrieve memories via the memory strategy (BM25, importance-weighted, etc.)
        # If we have a query (from plan step), use get_relevant_memories for BM25 ranking.
        # Otherwise fall back to get_recent_memories for recency-based retrieval.
        if memory_query:
            recent_memory_strings = await self.memory.get_relevant_memories(
                self.run_id, agent_id, query=memory_query, limit=10
            )
        else:
            recent_memory_strings = await self.memory.get_recent_memories(
                self.run_id, agent_id, limit=10
            )
        if self._is_sidecar_mode_enabled() and recent_memory_strings:
            recent_memory_strings = self._cap_communication_memories(
                recent_memory_strings,
                max_communication=_COMM_MEMORY_CAP_SIDE_CAR,
            )

        # Also get structured memory objects for message extraction and debug output.
        # This goes to persistence directly since we need the full AgentMemory objects.
        recent_agent_memories = await self.persistence.get_recent_memories(
            self.run_id, agent_id, limit=10
        )

        # Get debug flag once at top of function
        debug_memory = _env_enabled("DEBUG_MEMORY")

        # DEBUG_MEMORY: Show what memories agent retrieved and the query used
        if debug_memory:
            if memory_query:
                query_preview = memory_query[:50] + "..." if len(memory_query) > 50 else memory_query
                print(colored(f"\n  [DEBUG_MEMORY] {agent_name} - BM25 query: \"{query_preview}\"", Color.CYAN))
            else:
                print(colored(f"\n  [DEBUG_MEMORY] {agent_name} - No query (using recency only)", Color.CYAN))
            print(colored(f"  [DEBUG_MEMORY] Retrieved {len(recent_memory_strings)} memories:", Color.CYAN))
            for i, mem_str in enumerate(recent_memory_strings[:5], 1):  # Show first 5
                mem_preview = mem_str[:80] + "..." if len(mem_str) > 80 else mem_str
                print(colored(f"    {i}. {mem_preview}", Color.CYAN))
            if len(recent_memory_strings) > 5:
                print(colored(f"    ... and {len(recent_memory_strings) - 5} more", Color.CYAN))
        elif _env_enabled("MINIVERSE_VERBOSE") and self.llm_provider and self.llm_model:
            if memory_query:
                query_preview = (
                    memory_query[:80] + "..."
                    if len(memory_query) > 80
                    else memory_query
                )
                self._log(
                    colored(
                        self._wrap_for_log(
                            f"[Memory] {agent_name}: retrieved {len(recent_memory_strings)} "
                            f"memory item(s) for query '{query_preview}'",
                            prefix="    ",
                            continuation="      ",
                        ),
                        Color.CYAN,
                    ),
                    level="verbose",
                )
            else:
                if recent_memory_strings:
                    self._log(
                        colored(
                            f"    [Memory] {agent_name}: retrieved {len(recent_memory_strings)} "
                            "recent memory item(s)",
                            Color.CYAN,
                        ),
                        level="verbose",
                    )
                else:
                    self._log(
                        colored(
                            f"    [Memory] {agent_name}: no prior memories yet (starting fresh)",
                            Color.CYAN,
                        ),
                        level="verbose",
                    )

        # Build direct messages for this agent from recent memories (recipient entries only)
        recent_messages: List[Dict[str, str]] = []
        for mem in recent_agent_memories:
            if mem.memory_type != "communication":
                continue
            role = (mem.metadata or {}).get("role")
            if role != "recipient":
                continue
            sender = (mem.metadata or {}).get("sender") or (mem.metadata or {}).get("sender_name") or "unknown"
            message_text = (mem.metadata or {}).get("message") or mem.content
            recent_messages.append({"from": sender, "message": message_text})

        # Build partial observability view (Stanford Generative Agents pattern).
        # Perception includes: agent's own status, messages to them, and memory context.
        perception = build_agent_perception(
            agent_id,
            self.current_state,
            recent_messages,
            recent_memory_strings,
        )

        if self.simulation_rules:
            try:
                perception = self.simulation_rules.customize_perception(
                    agent_id, perception, self.current_state
                )
            except Exception as exc:  # pragma: no cover - defensive
                raise RuntimeError(
                    f"SimulationRules.customize_perception raised an error for agent {agent_id}"
                ) from exc

        if _env_enabled("MINIVERSE_VERBOSE") and self.llm_provider and self.llm_model:
            clock = self._format_stat_value_for_log(
                perception.environment_snapshot.get("shift_time_local")
            )
            remaining = self._format_stat_value_for_log(
                perception.environment_snapshot.get("shift_minutes_remaining")
            )
            phase = self._format_stat_value_for_log(
                perception.environment_snapshot.get("shift_phase")
            )
            backlog_visible = self._format_stat_value_for_log(
                perception.visible_resources.get("task_backlog")
            )
            arrived_tick = self._format_stat_value_for_log(
                perception.visible_resources.get("tasks_arrived_tick")
            )
            completed_tick = self._format_stat_value_for_log(
                perception.visible_resources.get("tasks_completed_tick")
            )
            execution_capacity = self._format_stat_value_for_log(
                perception.visible_resources.get("execution_capacity")
            )
            energy_now = self._format_stat_value_for_log(
                perception.personal_attributes.get("energy")
            )
            stress_now = self._format_stat_value_for_log(
                perception.personal_attributes.get("stress")
            )

            self._log(
                colored(
                    self._wrap_for_log(
                        (
                            f"[Perception] {agent_name}: time={clock}, remaining={remaining}, "
                            f"phase={phase}, backlog={backlog_visible}, arrived={arrived_tick}, "
                            f"completed={completed_tick}, exec_capacity={execution_capacity}, "
                            f"energy={energy_now}, stress={stress_now}"
                        ),
                        prefix="    ",
                        continuation="      ",
                    ),
                    Color.CYAN,
                ),
                level="verbose",
            )

        # DEBUG_PERCEPTION: Log what agent perceives (parallel to DEBUG_LLM)
        if _env_enabled("DEBUG_PERCEPTION"):
            print(f"\n[DEBUG_PERCEPTION] {agent_name} (tick {tick})")
            print(f"  Recent memories ({len(recent_memory_strings)}):")
            for i, mem in enumerate(recent_memory_strings[:5], 1):  # Show first 5
                preview = mem[:80] + "..." if len(mem) > 80 else mem
                print(f"    {i}. {preview}")
            if len(recent_memory_strings) > 5:
                print(f"    ... and {len(recent_memory_strings) - 5} more")
            print(f"  Messages ({len(perception.messages)}):")
            for msg in perception.messages:
                preview = msg["message"][:60] + "..." if len(msg["message"]) > 60 else msg["message"]
                print(f"    - From {msg['from']}: {preview}")
            if not perception.messages:
                print(f"    (none)")
            print(f"  System alerts: {len(perception.system_alerts)}")
            print()

        # Use custom prompt library if agent's cognition provides one, otherwise fall back
        # to system defaults. Prompt library contains templates for planning, execution,
        # and reflection - allowing per-agent customization of reasoning styles.
        prompt_library = cognition.prompt_library or DEFAULT_PROMPTS

        # Assemble common metadata for all prompt contexts (planning, execution, reflection).
        # This avoids repeating the same data across multiple context builds.
        extra_common = {
            # Per-agent private context (character voice, hidden knowledge, secret goals).
            # Injected every tick so agents maintain consistent identity and private state.
            "initial_state_agent_prompt": self.agent_prompts.get(agent_id, ""),
            # Optional simulation instructions (system-level contract). If empty, template default applies
            "simulation_instructions": self.world_prompt or "",
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "prompt_library": prompt_library,
        }

        # Extract existing plan from scratchpad. Plan may be None (agent hasn't planned yet)
        # or Plan object (agent has active multi-step plan). We normalize to Plan() for
        # consistency - empty plans are valid (agent will rest or use heuristics).
        existing_plan = self._get_scratchpad_value(cognition, "plan")
        existing_plan_index = self._get_scratchpad_value(cognition, "plan_index", 0)
        if isinstance(existing_plan, Plan):
            initial_plan = existing_plan
        else:
            # No plan exists - use empty plan as placeholder
            initial_plan = Plan()
            existing_plan_index = 0

        # Summarize current plan state for planning context. Planner needs to see existing
        # plan to decide whether to refresh, extend, or keep current plan.
        planning_plan_state = self._plan_state_summary(initial_plan, existing_plan_index)

        # Build prompt context for planning. Planner receives full world state, perception,
        # memories, and current plan to generate/refresh multi-step plans. Context is
        # JSON-serializable for template rendering.
        planning_context = await build_prompt_context(
            agent_profile=self.agents[agent_id],
            perception=perception,
            world_state=self.current_state,
            scratchpad_state=self._get_scratchpad_state(cognition),
            plan_state=planning_plan_state,
            memories=recent_agent_memories,
            extra=extra_common,
        )

        # Ensure agent has valid plan. Planner cadence determines whether to generate fresh
        # plan or reuse existing. Returns (plan, current_step, step_index) tuple. Plan may
        # be empty if planner is deterministic or LLM unavailable.
        plan, plan_step, plan_index = await self._ensure_plan(
            agent_id, cognition, planning_context, tick
        )

        if _env_enabled("MINIVERSE_VERBOSE") and self.llm_provider and self.llm_model:
            if plan.steps:
                current_step = plan.steps[min(plan_index, len(plan.steps) - 1)]
                step_preview = (
                    current_step.description[:100] + "..."
                    if len(current_step.description) > 100
                    else current_step.description
                )
                self._log(
                    colored(
                        self._wrap_for_log(
                            f"[Plan] {agent_name}: step {plan_index + 1}/{len(plan.steps)} -> {step_preview}",
                            prefix="    ",
                            continuation="      ",
                        ),
                        Color.CYAN,
                    ),
                    level="verbose",
                )
            else:
                self._log(
                    colored(f"    [Plan] {agent_name}: no active plan", Color.CYAN),
                    level="verbose",
                )

        # Summarize updated plan state for execution context. Executor needs to see full
        # plan to coordinate actions with long-term goals.
        plan_state = self._plan_state_summary(plan, plan_index)

        # Build prompt context for execution. Executor receives same data as planner but
        # with updated plan state (in case planner just refreshed the plan).
        context = await build_prompt_context(
            agent_profile=self.agents[agent_id],
            perception=perception,
            world_state=self.current_state,
            scratchpad_state=self._get_scratchpad_state(cognition),
            plan_state=plan_state,
            memories=recent_agent_memories,
            extra=extra_common,
        )

        # Delegate action selection to executor. Executor considers current plan step,
        # perception, memories, and world state to choose concrete action (move, interact,
        # communicate, rest). Executor may deviate from plan if circumstances changed.
        # Choose appropriate tag based on executor capability
        executor_obj = cognition.executor
        uses_llm = False
        if hasattr(executor_obj, "uses_llm"):
            try:
                uses_llm = bool(executor_obj.uses_llm())  # type: ignore[attr-defined]
            except Exception:
                uses_llm = False
        else:
            # Fallback: detect known LLM executor type
            try:
                from .cognition.llm import LLMExecutor as _LLMExec
                uses_llm = isinstance(executor_obj, _LLMExec)
            except Exception:
                uses_llm = False

        exec_tag = LOG_TAG_LLM if uses_llm else LOG_TAG_DETERMINISTIC
        self._log(
            colored(
                f"  {exec_tag} [{agent_name}] Choosing action via executor...",
                Color.YELLOW,
            ),
            level="verbose",
        )
        action = await cognition.executor.choose_action(
            agent_id,
            perception,
            cognition.scratchpad,  # Pass scratchpad directly (can be None)
            plan=plan,
            plan_step=plan_step,
            context=context,
        )

        # Ensure agent_id matches the requesting agent. LLM may hallucinate wrong agent_id
        # or executor may use templates that don't populate it correctly. Overwriting here
        # prevents actions from being misattributed.
        action.agent_id = agent_id

        # Normalize communication recipients/targets to canonical agent_ids when possible.
        if action.communication and isinstance(action.communication, dict):
            raw_to = action.communication.get("to")
            if isinstance(raw_to, str):
                resolved_to = self._resolve_agent_id(raw_to)
                if resolved_to:
                    action.communication["to"] = resolved_to

        if action.action_type == "communicate" and isinstance(action.target, str):
            resolved_target = self._resolve_agent_id(action.target)
            if resolved_target:
                action.target = resolved_target

        # Apply sidecar budgeting after recipient normalization to avoid duplicate chatter.
        self._apply_sidecar_budget(
            tick=tick,
            agent_id=agent_id,
            cognition=cognition,
            action=action,
        )

        # Show action details if verbose mode enabled
        if _env_enabled("MINIVERSE_VERBOSE"):
            self._log(
                colored(
                    self._wrap_for_log(
                        action.reasoning or "",
                        prefix="    Reasoning: ",
                        continuation="      ",
                    ),
                    Color.CYAN,
                ),
                level="verbose",
            )
            if action.communication and action.communication.get("message"):
                message_text = str(action.communication.get("message") or "")
                self._log(
                    colored(
                        self._wrap_for_log(
                            f"\"{message_text}\"",
                            prefix="    Message: ",
                            continuation="      ",
                        ),
                        Color.CYAN,
                    ),
                    level="verbose",
                )

        self._log(
            colored(
                f"  {LOG_TAG_SUCCESS} [{agent_name}] Got action: {action.action_type}",
                Color.GREEN,
            ),
            level="verbose",
        )

        # Advance to next plan step after action executes. If plan is exhausted, wrap around
        # to step 0 (plans loop until planner refreshes). This keeps agents active rather
        # than blocking when plan completes.
        self._advance_plan(agent_id, cognition, plan)

        return action

    async def _process_world_update(
        self, tick: int, actions: List[AgentAction]
    ) -> WorldState:
        """World engine processes actions and returns new state.

        Args:
            tick: Current tick number
            actions: List of all agent actions this tick

        Returns:
            Updated WorldState from World Engine LLM

        Raises:
            Exception: If LLM call fails
        """
        # Tag world engine step based on mode/branch
        # Compute branch to tag the world update step
        has_rules_processor = bool(
            self.simulation_rules and getattr(self.simulation_rules, "process_actions").__func__ is not SimulationRules.process_actions
        )
        will_use_llm = (
            self.world_update_mode == "llm"
            or (
                self.world_update_mode == "auto"
                and not has_rules_processor
                and self.llm_provider
                and self.llm_model
            )
        )
        self._log(
            f"  [{'LLM' if will_use_llm else '•'}] [World Engine] Processing {len(actions)} actions...",
            level="verbose",
        )
        mode = self.world_update_mode
        rules = self.simulation_rules

        # Helper to detect overridden process_actions
        has_rules_processor = bool(
            rules and getattr(rules, "process_actions").__func__ is not SimulationRules.process_actions
        )

        # Mode selection
        if mode == "deterministic":
            if has_rules_processor:
                return rules.process_actions(self.current_state, actions, tick)  # type: ignore[arg-type]
            return self._apply_deterministic_world_update(tick, actions)

        if mode == "llm":
            # Require LLM and call
            physics_applied = rules is not None
            if not self.llm_provider or not self.llm_model:
                raise WorldEngineUnavailableError(
                    tick=tick,
                    reason=(
                        "World Engine LLM is not configured (missing LLM_PROVIDER and/or LLM_MODEL). "
                        "Set provider/model and API key, or disable world engine usage."
                    ),
                )
            try:
                new_state = await process_world_update(
                    self.current_state,
                    actions,
                    tick,
                    self.world_prompt,
                    self.llm_provider,
                    self.llm_model,
                    physics_applied=physics_applied,
                )
            except Exception as exc:
                raise WorldEngineValidationError(tick=tick, underlying=exc)
            self._log("  [World Engine] ✓ World state updated", level="verbose")
            return new_state

        # auto mode
        if has_rules_processor:
            return rules.process_actions(self.current_state, actions, tick)  # type: ignore[arg-type]
        if self.llm_provider and self.llm_model:
            physics_applied = rules is not None
            try:
                new_state = await process_world_update(
                    self.current_state,
                    actions,
                    tick,
                    self.world_prompt,
                    self.llm_provider,
                    self.llm_model,
                    physics_applied=physics_applied,
                )
            except Exception as exc:
                raise WorldEngineValidationError(tick=tick, underlying=exc)
            self._log("  [World Engine] ✓ World state updated", level="verbose")
            return new_state
        # No LLM configured and no rules processor: basic deterministic
        return self._apply_deterministic_world_update(tick, actions)

    async def _persist_tick(
        self, tick: int, state: WorldState, actions: List[AgentAction]
    ) -> None:
        """Save tick data via persistence strategy.

        Args:
            tick: Current tick number
            state: New world state
            actions: Agent actions this tick
        """
        self._log(f"  [Persistence] Persisting tick {tick}...", level="verbose")
        await self.persistence.save_state(self.run_id, tick, state)

        # A4: Actions should not persist full communication content. Keep only minimal reference.
        sanitized_actions: List[AgentAction] = []
        for action in actions:
            comm = action.communication
            if comm:
                # Preserve only the recipient reference; drop message body from action history
                recipient = comm.get("to") if isinstance(comm, dict) else None
                if recipient is None:
                    recipient = "unknown"
                action = action.model_copy(update={"communication": {"to": recipient}})
            sanitized_actions.append(action)

        await self.persistence.save_actions(self.run_id, tick, sanitized_actions)
        await self._update_memories(tick, actions, state)
        self._log(f"  [Persistence] ✓ Tick {tick} persisted", level="verbose")

    async def _update_memories(
        self, tick: int, actions: List[AgentAction], state: WorldState
    ) -> None:
        """Create memory records for actions, communications, and events.

        Args:
            tick: Current tick number
            actions: Agent actions this tick
            state: New world state
        """
        debug_memory = _env_enabled("DEBUG_MEMORY")

        if debug_memory:
            print(colored(f"\n  [DEBUG_MEMORY] Tick {tick} - Creating memories...", Color.CYAN))

        new_memories: Dict[str, List[AgentMemory]] = {}

        # Store actions as memories
        for action in actions:
            # Capture the action itself so downstream retrieval engines can filter by type/target.
            memory = await self.memory.add_memory(
                run_id=self.run_id,
                agent_id=action.agent_id,
                tick=tick,
                memory_type="action",
                content=f"I {action.action_type}: {action.reasoning}",
                importance=5,
                tags=[f"action:{action.action_type}"],
                metadata={
                    "target": action.target,
                    "parameters": action.parameters or {},
                    "reasoning": action.reasoning,
                },
            )
            new_memories.setdefault(action.agent_id, []).append(memory)

            # Store communication memories.
            if action.communication:
                recipient = action.communication.get("to", "unknown")
                message = action.communication.get("message") or action.communication.get("content", "")
                recipient_id = self._resolve_agent_id(recipient)
                sender_name = self.agents[action.agent_id].name
                msg_preview = message[:60] + "..." if len(message) > 60 else message

                if debug_memory:
                    print(colored(f"    💬 {sender_name} → {recipient}: \"{msg_preview}\"", Color.CYAN))

                # In sidecar mode, non-communicate actions can attach short messages.
                # Storing sender+recipient communication memories for every sidecar
                # quickly floods retrieval with coordination chatter. For sidecar payloads,
                # keep recipient memory only (enough for information diffusion).
                sidecar_payload = (
                    self._is_sidecar_mode_enabled() and action.action_type != "communicate"
                )
                store_sender_memory = not sidecar_payload
                if sidecar_payload and (not recipient_id or recipient_id == "unknown"):
                    # Fall back to sender-only memory if we cannot resolve recipient.
                    store_sender_memory = True
                if store_sender_memory:
                    sender_memory = await self.memory.add_memory(
                        run_id=self.run_id,
                        agent_id=action.agent_id,
                        tick=tick,
                        memory_type="communication",
                        content=f"I told {recipient}: {message}",
                        importance=6,
                        tags=["communication", f"to:{recipient}"],
                        metadata={
                            "message": message,
                            "recipient": recipient,
                            "action_type": action.action_type,
                            "role": "sender",
                        },
                    )
                    new_memories.setdefault(action.agent_id, []).append(sender_memory)
                    if debug_memory:
                        print(colored(f"       Sender memory stored: \"I told {recipient}: ...\"", Color.CYAN))

                if recipient_id and recipient_id != "unknown":
                    recipient_memory = await self.memory.add_memory(
                        run_id=self.run_id,
                        agent_id=recipient_id,  # Use resolved agent_id, not raw name
                        tick=tick,
                        memory_type="communication",
                        content=f"{sender_name} told me: {message}",
                        importance=7 if store_sender_memory else 5,
                        tags=[
                            "communication",
                            f"from:{action.agent_id}",
                            "sidecar" if not store_sender_memory else "direct",
                        ],
                        metadata={
                            "message": message,
                            "sender": action.agent_id,
                            "sender_name": sender_name,
                            "action_type": action.action_type,
                            "role": "recipient",
                        },
                    )
                    new_memories.setdefault(recipient_id, []).append(recipient_memory)

                    if debug_memory:
                        recipient_name = self.agents[recipient_id].name
                        memory_note = (
                            "Recipient memory stored"
                            if store_sender_memory
                            else "Sidecar recipient memory stored"
                        )
                        print(colored(f"       {memory_note}: \"{recipient_name} received: ...\"", Color.CYAN))

        # Store events as observations for affected agents
        for event in state.recent_events:
            if event.tick == tick:  # Only new events
                for agent_id in event.affected_agents:
                    # Events may not have severity; default to 5 (medium importance)
                    importance = event.severity if event.severity is not None else 5

                    memory = await self.memory.add_memory(
                        run_id=self.run_id,
                        agent_id=agent_id,
                        tick=tick,
                        memory_type="observation",
                        content=event.description,
                        importance=importance,
                        tags=[
                            "event",
                            f"severity:{event.severity}" if event.severity is not None else "severity:unknown",
                            event.category,
                        ],
                        metadata={
                            "event_id": event.event_id,
                            "category": event.category,
                        },
                    )
                    new_memories.setdefault(agent_id, []).append(memory)

        await self._run_reflections(tick, new_memories)

    def _default_action(self, agent_id: str, tick: int) -> AgentAction:
        """Fallback action when agent fails.

        Args:
            agent_id: ID of agent
            tick: Current tick number

        Returns:
            Default rest action
        """
        return AgentAction(
            agent_id=agent_id,
            tick=tick,
            action_type="rest",
            target=None,
            parameters={},
            reasoning="System default: agent failed to respond",
            communication=None,
        )

    async def _ensure_plan(
        self,
        agent_id: str,
        cognition: AgentCognition,
        planning_context: PromptContext,
        tick: int,
    ) -> tuple[Plan, PlanStep | None, int]:
        """Ensure the agent has an active plan and return the current step.

        If planner is None (agent doesn't use planning), returns empty plan with no steps.
        Otherwise implements the planner cadence pattern - plans are refreshed periodically
        (e.g., once per day) rather than every tick, reducing LLM calls while maintaining
        long-term coherence. Cadence is configurable per agent via AgentCognition.
        """

        # If agent doesn't use planner, return empty plan (agent is purely reactive)
        if cognition.planner is None:
            return Plan(), None, 0

        # Extract existing plan from scratchpad. Plan may be None if agent never planned,
        # or Plan object if agent has active plan. We need to check type because scratchpad
        # stores arbitrary key-value pairs.
        plan_state = self._get_scratchpad_value(cognition, "plan")
        if isinstance(plan_state, Plan):
            plan = plan_state
        else:
            plan = None

        # Check cadence to determine if plan needs refresh. Cadence considers:
        # 1. Time elapsed since last plan (e.g., 24 ticks = 1 simulated day)
        # 2. Whether current plan is empty/expired
        # 3. Custom trigger conditions (scenario-specific)
        cadence = cognition.cadence.planner
        last_plan_tick = self._get_scratchpad_value(cognition, PLANNER_LAST_TICK_KEY)

        should_refresh = cadence.should_generate(
            tick=tick,
            last_run_tick=last_plan_tick,
            current_plan=plan,
        )

        if should_refresh:
            # Generate fresh plan via LLM or deterministic planner. Planner considers
            # agent's personality, current world state, recent memories, and existing plan
            # to produce coherent multi-step agenda. New plan replaces old plan entirely
            # (no merging) and resets plan_index to 0.
            plan = await cognition.planner.generate_plan(
                agent_id,
                cognition.scratchpad,  # Pass scratchpad directly (can be None)
                world_context=self.current_state,
                context=planning_context,
            )
            # Normalize non-Plan return values (shouldn't happen but defensive)
            if not isinstance(plan, Plan):
                plan = Plan()
            # Store new plan and reset index. Recording tick enables cadence tracking.
            self._set_scratchpad_value(cognition, "plan", plan)
            self._set_scratchpad_value(cognition, "plan_index", 0)
            self._set_scratchpad_value(cognition, PLANNER_LAST_TICK_KEY, tick)
        else:
            # Reuse existing plan. If plan somehow missing (shouldn't happen due to cadence
            # logic), create empty plan as safety net.
            if plan is None:  # safety net; shouldn't happen due to cadence
                plan = Plan()
                self._set_scratchpad_value(cognition, "plan", plan)
            # Ensure plan_index exists (default to 0 if missing)
            if self._get_scratchpad_value(cognition, "plan_index") is None:
                self._set_scratchpad_value(cognition, "plan_index", 0)

        # Extract current step from plan. If plan_index exceeds plan length (can happen if
        # plan was shortened), wrap to 0 to keep agent active. Empty plans return None step,
        # signaling executor to use fallback behavior (rest, wander, reactive actions).
        plan_index = self._get_scratchpad_value(cognition, "plan_index", 0)
        if plan.steps:
            if plan_index >= len(plan.steps):
                # Plan exhausted - wrap around to beginning to keep agent active
                plan_index = 0
                self._set_scratchpad_value(cognition, "plan_index", plan_index)
            plan_step = plan.steps[plan_index]
        else:
            # Empty plan - executor will use fallback logic
            plan_step = None

        return plan, plan_step, plan_index

    def _advance_plan(
        self, agent_id: str, cognition: AgentCognition, plan: Plan
    ) -> None:
        """Advance the plan index after an action executes.

        This implements sequential plan execution - agent moves to next step after each tick.
        When plan exhausts, wraps to step 0 to keep agent active. Alternative strategies
        (conditional branching, parallel steps) could be implemented by subclassing.
        """

        # No planning or empty plan - nothing to advance
        if cognition.planner is None or not plan.steps:
            return

        # Move to next step, or wrap to beginning if plan exhausted. Wrap-around prevents
        # agents from blocking when plan completes - they loop through plan until planner
        # refreshes with new agenda. This is appropriate for daily routines (wake -> eat ->
        # work -> sleep -> repeat) but not for project plans (may want to stop at end).
        plan_index = self._get_scratchpad_value(cognition, "plan_index", 0)
        if plan_index < len(plan.steps) - 1:
            # More steps remaining - advance to next step
            self._set_scratchpad_value(cognition, "plan_index", plan_index + 1)
        else:
            # Plan exhausted - wrap to beginning to maintain activity
            self._set_scratchpad_value(cognition, "plan_index", 0)

    @staticmethod
    def _plan_state_summary(plan: Plan, plan_index: int) -> Dict[str, Any]:
        return {
            "current_index": plan_index,
            "steps": [
                {"description": step.description, "metadata": step.metadata}
                for step in plan.steps
            ],
        }

    async def _run_reflections(
        self, tick: int, new_memories: Dict[str, List[AgentMemory]]
    ) -> None:
        """Invoke reflection engines for each agent.

        Stanford Generative Agents pattern: Periodically synthesize recent experiences into
        higher-level insights (e.g., "I'm running low on resources" from multiple actions).
        Reflections are stored as memories with elevated importance, making them more likely
        to influence future decisions.
        """

        for agent_id, cognition in self.agent_cognition.items():
            # Skip agents that don't use reflection
            if cognition.reflection is None:
                continue

            # Count new memories created this tick. Reflection cadence may trigger based on
            # memory accumulation (e.g., reflect after every 5 new experiences) rather than
            # fixed time intervals.
            new_memory_count = len(new_memories.get(agent_id, []))
            cadence = cognition.cadence.reflection
            last_reflection_tick = self._get_scratchpad_value(cognition, REFLECTION_LAST_TICK_KEY)

            # Check if reflection should trigger. Cadence considers both time elapsed and
            # memory count to balance insight generation with LLM cost. Skip if not ready.
            if not cadence.should_reflect(
                tick=tick,
                last_run_tick=last_reflection_tick,
                new_memories=new_memory_count,
            ):
                continue

            # Retrieve larger memory window (20 vs 10) for reflection. Reflections synthesize
            # longer-term patterns, so need broader historical context than tick actions.
            recent_memories = await self.persistence.get_recent_memories(
                self.run_id, agent_id, limit=20
            )

            prompt_library = cognition.prompt_library or DEFAULT_PROMPTS

            # Convert first 10 memories to strings for perception's recent_observations.
            # Reflection uses broader window (20) for context but perception shows recent subset.
            recent_memory_strings = [m.content for m in recent_memories[:10]]

            # Build minimal perception for reflection. Reflections focus on internal state
            # (memories, plans, goals) rather than environment, so pass empty actions list.
            perception = build_agent_perception(
                agent_id,
                self.current_state,
                [],  # No direct messages needed for reflection context
                recent_memory_strings,
            )

            # Extract current plan for reflection context. Reflections may comment on plan
            # progress or suggest adjustments based on accumulated experiences.
            plan_obj = self._get_scratchpad_value(cognition, "plan")
            plan_index = self._get_scratchpad_value(cognition, "plan_index", 0)
            if not isinstance(plan_obj, Plan):
                plan_obj = Plan()
                plan_index = 0
            plan_state = self._plan_state_summary(plan_obj, plan_index)

            # Build prompt context for reflection. Reflection engine receives full memories,
            # current plan, and world state to generate synthesized insights.
            reflection_context = await build_prompt_context(
                agent_profile=self.agents[agent_id],
                perception=perception,
                world_state=self.current_state,
                scratchpad_state=self._get_scratchpad_state(cognition),
                plan_state=plan_state,
                memories=recent_memories,
                extra={
                    "llm_provider": self.llm_provider,
                    "llm_model": self.llm_model,
                    "prompt_library": prompt_library,
                },
            )

            # Invoke reflection engine. May return empty list if no interesting patterns
            # detected. Reflections are free-form text with importance scores (typically
            # higher than raw observations to influence future retrieval).
            reflections = await cognition.reflection.maybe_reflect(
                agent_id,
                cognition.scratchpad,  # Pass scratchpad directly (can be None)
                recent_memories,
                trigger_context={
                    "tick": tick,
                    "new_memories": new_memory_count,
                },
                context=reflection_context,
            )

            if _env_enabled("MINIVERSE_VERBOSE") and self.llm_provider and self.llm_model and reflections:
                agent_name = self.agents[agent_id].name
                self._log(
                    colored(
                        f"    [Reflection] {agent_name}: {len(reflections)} reflection(s)",
                        Color.CYAN,
                    ),
                    level="verbose",
                )
                for reflection in reflections:
                    self._log(
                        colored(
                            self._wrap_for_log(
                                reflection.content,
                                prefix=f"      - ({reflection.importance}/10) ",
                                continuation="        ",
                            ),
                            Color.CYAN,
                        ),
                        level="verbose",
                    )

            # Record reflection tick to prevent double-reflection same tick
            self._set_scratchpad_value(cognition, REFLECTION_LAST_TICK_KEY, tick)

            # Store reflections as memories. Reflections get memory_type="reflection" for
            # retrieval filtering and typically have elevated importance (6-10) vs actions (5).
            # High importance ensures reflections surface in future memory retrieval.
            for reflection in reflections:
                await self.memory.add_memory(
                    run_id=self.run_id,
                    agent_id=agent_id,
                    tick=tick,
                    memory_type="reflection",
                    content=reflection.content,
                    importance=reflection.importance,
                    metadata=reflection.metadata,
                )

    def _apply_deterministic_world_update(
        self, tick: int, actions: List[AgentAction]
    ) -> WorldState:
        """Clone the current state and apply basic deterministic updates.

        This is the fallback world update when LLM world engine fails validation or is
        unavailable. Applies minimal physics: update agent activities, process move actions,
        update occupancy tracking. Does NOT generate events, resolve conflicts, or apply
        narrative consistency - purely mechanical state updates.
        """

        # Deep copy current state to avoid mutating baseline. Pydantic model_copy ensures
        # nested objects (agents, locations, resources) are fully cloned.
        new_state = self.current_state.model_copy(deep=True)
        new_state.tick = tick
        # Clear recent_events since we're not generating new events in deterministic mode
        new_state.recent_events = []

        # Extract occupancy tracker from simulation rules if present. Occupancy prevents
        # multiple agents from occupying same location and enforces capacity limits.
        occupancy = getattr(self.simulation_rules, "occupancy", None)

        for action in actions:
            # Find agent's status object in world state. Status tracks location, activity,
            # resources, and other dynamic properties. Skip if agent not found (shouldn't happen).
            status = next((agent for agent in new_state.agents if agent.agent_id == action.agent_id), None)
            if status is None:
                continue

            # Record previous location before move for occupancy tracking
            previous_location = status.location
            # Update activity regardless of action type (move, interact, rest, etc.)
            status.activity = action.action_type

            # Process move actions: update location and occupancy. Other action types
            # (interact, rest, communicate) don't affect location.
            if action.action_type == "move" and action.target:
                target = action.target
                # No-op moves: if target equals current location, skip occupancy ops entirely
                if target == previous_location:
                    continue
                entered = True
                if occupancy is not None:
                    # Attempt to enter new location respecting capacity; if refused, keep agent in place
                    try:
                        entered = bool(occupancy.enter(target, action.agent_id))
                    except Exception:
                        entered = False
                    if entered and previous_location:
                        try:
                            occupancy.leave(previous_location, action.agent_id)
                        except Exception:
                            pass
                if entered:
                    status.location = target

        return new_state

    def _print_tick_summary(
        self,
        tick: int,
        actions: List[AgentAction],
        state: WorldState,
        previous_state: Optional[WorldState] = None,
    ) -> None:
        """Print human-readable tick summary.

        Args:
            tick: Current tick number
            actions: Agent actions this tick
            state: New world state
        """
        if self.log_mode == "none":
            return

        # Print resource levels
        resource_summary = (
            self.simulation_rules.format_resource_summary(state)
            if self.simulation_rules
            else format_resources_generic(state)
        )

        # Optional shift clock summary (if scenario publishes these environment metrics).
        shift_time = state.environment.metrics.get("shift_time_local")
        shift_remaining = state.environment.metrics.get("shift_minutes_remaining")
        shift_phase = state.environment.metrics.get("shift_phase")
        clock_summary = None
        if shift_time or shift_remaining or shift_phase:
            time_value = self._format_stat_value_for_log(shift_time) if shift_time else "n/a"
            remaining_value = (
                self._format_stat_value_for_log(shift_remaining)
                if shift_remaining
                else "n/a"
            )
            phase_value = self._format_stat_value_for_log(shift_phase) if shift_phase else "n/a"
            clock_summary = f"Clock: {time_value} | Remaining: {remaining_value} | Phase: {phase_value}"

        # Optional task-flow interpretation block (if metrics are published).
        arrived_tick = state.resources.metrics.get("tasks_arrived_tick")
        completed_tick = state.resources.metrics.get("tasks_completed_tick")
        net_change_tick = state.resources.metrics.get("task_net_change_tick")
        task_flow_summary = None
        if arrived_tick or completed_tick or net_change_tick:
            arrived_value = int(arrived_tick.value) if arrived_tick else 0
            completed_value = int(completed_tick.value) if completed_tick else 0
            net_value = int(net_change_tick.value) if net_change_tick else (arrived_value - completed_value)
            sign = "+" if net_value >= 0 else ""
            task_flow_summary = (
                f"Task flow: arrived={arrived_value}, completed={completed_value}, "
                f"net_change={sign}{net_value}"
            )

        backlog_delta_summary = None
        current_backlog_stat = state.resources.metrics.get("task_backlog")
        previous_backlog_stat = (
            previous_state.resources.metrics.get("task_backlog")
            if previous_state is not None
            else None
        )
        if current_backlog_stat is not None and previous_backlog_stat is not None:
            current_backlog = int(current_backlog_stat.value)
            previous_backlog = int(previous_backlog_stat.value)
            delta = current_backlog - previous_backlog
            sign = "+" if delta >= 0 else ""
            backlog_delta_summary = (
                f"Pending Tasks: {previous_backlog} -> {current_backlog} (Δ {sign}{delta})"
            )

        if self.log_mode == "concise":
            if clock_summary:
                self._log(f"  {clock_summary}", level="concise")
            if backlog_delta_summary:
                self._log(f"  {backlog_delta_summary}", level="concise")
            if task_flow_summary:
                self._log(f"  {task_flow_summary}", level="concise")
            if resource_summary:
                self._log(f"  Resources: {resource_summary}", level="concise")

            directed_messages = 0
            for action in actions:
                if (
                    action.communication
                    and isinstance(action.communication, dict)
                    and action.communication.get("to")
                ):
                    directed_messages += 1

            if actions:
                self._log("  Actions:", level="concise")
                for action in actions:
                    agent = self.agents[action.agent_id]
                    target = f" target={action.target}" if action.target is not None else ""
                    comm_to = None
                    if action.communication and isinstance(action.communication, dict):
                        comm_to = action.communication.get("to")
                    comm = f" to={comm_to}" if comm_to else ""
                    reason = action.reasoning or ""
                    reason_preview = (
                        reason[:100] + "..." if len(reason) > 100 else reason
                    )
                    self._log(
                        f"    - {agent.name}: {action.action_type}{target}{comm}"
                        f" | {reason_preview}",
                        level="concise",
                    )

            self._log(
                f"  Coordination signals: directed messages={directed_messages}",
                level="concise",
            )
            self._log("", level="concise")
            return

        # Verbose mode prints full details.
        if clock_summary:
            self._log(f"  {clock_summary}", level="verbose")
        if backlog_delta_summary:
            self._log(f"  {backlog_delta_summary}", level="verbose")
        if task_flow_summary:
            self._log(f"  {task_flow_summary}", level="verbose")
        if resource_summary:
            self._log(f"  Resources: {resource_summary}", level="verbose")

        if actions:
            self._log("  Actions:", level="verbose")

        for action in actions:
            agent = self.agents[action.agent_id]
            reasoning_text = action.reasoning if action.reasoning is not None else ""
            target_str = f" target={action.target}" if action.target is not None else ""
            params_str = f" params={action.parameters}" if action.parameters else ""
            comm_to = None
            if action.communication and isinstance(action.communication, dict):
                comm_to = action.communication.get("to")
            comm_str = f" comm.to={comm_to}" if comm_to else ""
            prefix = (
                f"    - {agent.name}: {action.action_type}{target_str}{params_str}{comm_str} - "
            )
            wrapped = self._wrap_for_log(
                reasoning_text,
                prefix=prefix,
                continuation="      ",
            )
            self._log(
                wrapped,
                level="verbose",
            )

        for event in state.recent_events:
            if event.tick == tick:
                self._log(
                    f"  EVENT (severity {event.severity}): {event.description}",
                    level="verbose",
                )

        self._log("", level="verbose")
