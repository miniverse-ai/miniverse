"""Async orchestrator for independent agent execution.

Replaces the tick-based synchronous loop with per-agent async loops.
Agents run independently, conversations are multi-turn, and time flows
continuously via WorldClock.

Usage:
    orchestrator = AsyncOrchestrator(
        world_state=world_state,
        agents=profiles_map,
        agent_prompts=agent_prompts,
        simulation_rules=rules,
        llm_provider="openai",
        llm_model="gpt-5-mini",
    )
    result = await orchestrator.run(duration_hours=8)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from .clock import WorldClock, get_action_duration
from .cognition import (
    AgentCognition,
    AgentCognitionMap,
    build_default_cognition,
    build_prompt_context,
    wrap_action_as_step_decision,
)
from .cognition.cadence import REFLECTION_LAST_TICK_KEY
from .cognition.context_window import ContextWindow
from .cognition.planner import Plan, PlanStep
from .conversation import ConversationManager, Conversation, Message
from .logging_utils import colored, Color
from .memory import MemoryStrategy, BM25MemoryStrategy
from .perception import build_agent_perception
from .persistence import PersistenceStrategy, InMemoryPersistence
from .scenario_actions import ScenarioActions
from .schemas import AgentAction, AgentMemory, AgentProfile, StepDecision, StepOutput, ActionResult, WorldState
from .simulation_rules import SimulationRules

logger = logging.getLogger(__name__)


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_agent_llm_overrides() -> Dict[str, Dict[str, str]]:
    raw = os.getenv("BASIN_AGENT_MODELS") or os.getenv("MINIVERSE_AGENT_MODELS")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid BASIN_AGENT_MODELS JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("BASIN_AGENT_MODELS must be a JSON object keyed by agent id")

    overrides: Dict[str, Dict[str, str]] = {}
    for agent_id, config in payload.items():
        if isinstance(config, str):
            overrides[str(agent_id)] = {"model": config}
            continue
        if not isinstance(config, dict):
            raise ValueError(f"BASIN_AGENT_MODELS[{agent_id!r}] must be a string or object")
        entry: Dict[str, str] = {}
        provider = config.get("provider")
        model = config.get("model")
        if provider is not None:
            entry["provider"] = str(provider)
        if model is not None:
            entry["model"] = str(model)
        if entry:
            overrides[str(agent_id)] = entry
    return overrides


class AsyncOrchestrator:
    """Orchestrator with independent agent loops and multi-turn conversations.

    Each agent runs its own async loop: perceive → decide → act.
    Conversations are first-class multi-turn exchanges managed by
    ConversationManager. Time flows continuously via WorldClock.
    """

    def __init__(
        self,
        world_state: WorldState,
        agents: Dict[str, AgentProfile],
        agent_prompts: Dict[str, str],
        *,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        simulation_rules: Optional[SimulationRules] = None,
        persistence: Optional[PersistenceStrategy] = None,
        memory: Optional[MemoryStrategy] = None,
        agent_cognition: Optional[AgentCognitionMap] = None,
        scenario_actions: Optional[ScenarioActions] = None,
        world_prompt: str = "",
        verbose: bool = True,
        max_conversation_turns: int = 50,
        max_agent_steps: Optional[int] = None,
        use_context_window: bool = False,
    ) -> None:
        self.current_state = world_state
        self.agents = agents
        self.agent_prompts = agent_prompts
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.agent_llm_overrides = _load_agent_llm_overrides()
        self.simulation_rules = simulation_rules
        self.world_prompt = world_prompt
        self.verbose = verbose
        self.max_conversation_turns = max_conversation_turns
        self.max_agent_steps = max_agent_steps
        self.use_context_window = use_context_window
        self.scenario_actions = scenario_actions

        # Give scenario actions read access to profiles and persona overlays.
        # Used by scenarios that need to rebuild system prompts (e.g. for
        # context resets at episode boundaries).
        if self.scenario_actions is not None and hasattr(
            self.scenario_actions, "bind_orchestrator_context"
        ):
            self.scenario_actions.bind_orchestrator_context(agents, agent_prompts)
        if self.scenario_actions is not None and hasattr(
            self.scenario_actions, "bind_llm_config"
        ):
            self.scenario_actions.bind_llm_config(
                llm_provider,
                llm_model,
                self.agent_llm_overrides,
            )

        self.persistence = persistence or InMemoryPersistence()
        self.memory = memory or BM25MemoryStrategy(self.persistence)

        # Build cognition map
        if agent_cognition:
            self.agent_cognition = dict(agent_cognition)
        else:
            self.agent_cognition = {
                agent_id: build_default_cognition(
                    agent_id, self.agents[agent_id], use_llm=bool(llm_provider)
                )
                for agent_id in agents
            }

        self.run_id = uuid4()
        self.clock: Optional[WorldClock] = None
        self.conversations = ConversationManager()

        # Event log — all events in chronological order
        self._events: List[Dict[str, Any]] = []
        # Per-agent step counter
        self._agent_steps: Dict[str, int] = {}
        # Per-agent rate limit hit counter (for exponential backoff)
        self._agent_rate_limit_hits: Dict[str, int] = {}
        # Global provider backoff. When one agent is rate-limited, all agent
        # loops pause and scenarios can freeze wall-clock-based timers.
        self._rate_limit_pause_lock = asyncio.Lock()
        self._simulation_pause_until: float = 0.0
        # Lock for world state mutations
        self._state_lock = asyncio.Lock()
        # Global stop signal
        self._stop = asyncio.Event()
        # Per-agent context windows (used when use_context_window=True)
        self._agent_contexts: Dict[str, ContextWindow] = {}
        if self.scenario_actions is not None and hasattr(
            self.scenario_actions, "bind_agent_context_windows"
        ):
            self.scenario_actions.bind_agent_context_windows(self._agent_contexts)
        # Per-agent notification nudge queue (sender names only, drained each step)
        self._agent_notifications: Dict[str, List[Dict[str, str]]] = {}
        # Per-agent inbox (full message content, drained on check_inbox)
        self._agent_inbox: Dict[str, List[Dict[str, str]]] = {}
        # Per-agent scenario context events. Scenario markers are not inbox
        # messages, but they still need to wake sleeping agents that received
        # new world context.
        self._agent_context_events: set[str] = set()
        self._end_time: Optional[datetime] = None
        self._time_limit_logged = False
        self._scenario_complete_logged = False
        self._max_steps_exhausted = False
        self.event_log_path: Optional[Path] = None

    def _llm_config_for_agent(self, agent_id: str) -> tuple[Optional[str], Optional[str]]:
        override = self.agent_llm_overrides.get(agent_id, {})
        return (
            override.get("provider", self.llm_provider),
            override.get("model", self.llm_model),
        )

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def _rate_limit_max_hits(self) -> int:
        raw = os.getenv("MINIVERSE_RATE_LIMIT_MAX_HITS", "").strip()
        if not raw:
            return 12
        try:
            return max(1, int(raw))
        except ValueError:
            return 12

    async def _wait_if_simulation_paused(self) -> None:
        """Block agent loops during a global provider backoff pause."""
        while not self._stop.is_set():
            remaining = self._simulation_pause_until - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))

    async def _pause_for_rate_limit(self, seconds: float) -> None:
        """Pause all agent loops and freeze scenario wall-clock timers."""
        if seconds <= 0:
            return
        async with self._rate_limit_pause_lock:
            now = time.monotonic()
            self._simulation_pause_until = max(self._simulation_pause_until, now + seconds)
            if self.scenario_actions is not None and hasattr(
                self.scenario_actions, "pause_simulation_time"
            ):
                self.scenario_actions.pause_simulation_time(seconds)  # type: ignore[attr-defined]
            await asyncio.sleep(seconds)

    def _time_limit_reached(self) -> bool:
        """Return True and signal stop once the simulated run window has elapsed."""
        if self.clock is None or self._end_time is None:
            return False
        if self.clock.now < self._end_time:
            return False
        if not self._time_limit_logged:
            self._log(colored(
                f"  [world] Reached simulated time limit: {self.clock.time_str()}",
                Color.YELLOW,
            ))
            self._time_limit_logged = True
        self._stop.set()
        return True

    def _real_time_timeout_seconds(self, duration_hours: Optional[float]) -> Optional[float]:
        """Return the wall-clock safety timeout for async runs.

        Scenario-native runs with an explicit scenario completion predicate should
        be allowed to stop on their own configured endpoint. Set
        MINIVERSE_ASYNC_TIMEOUT_SECONDS to override; use 0 to disable.
        """
        raw = os.getenv("MINIVERSE_ASYNC_TIMEOUT_SECONDS")
        if raw is not None:
            try:
                timeout = float(raw)
            except ValueError:
                timeout = 600.0
            return timeout if timeout > 0 else None

        has_native_completion = (
            self.scenario_actions is not None
            and hasattr(self.scenario_actions, "is_complete")
        )
        if duration_hours is None and has_native_completion:
            return None
        return 600.0

    def _completion_status(self, pending_count: int) -> str:
        if self._scenario_complete_logged:
            return "scenario_complete"
        if self._time_limit_logged:
            return "sim_time_limit"
        if pending_count:
            return "wall_time_timeout"
        if self._max_steps_exhausted:
            return "max_steps_exhausted"
        return "completed"

    def _scenario_complete(self) -> bool:
        """Return True when scenario actions declare a native endpoint."""
        if self.scenario_actions is None or not hasattr(self.scenario_actions, "is_complete"):
            return False
        try:
            complete = bool(self.scenario_actions.is_complete())
        except Exception:
            return False
        if not complete:
            return False
        reason = ""
        if hasattr(self.scenario_actions, "completion_reason"):
            try:
                reason = str(self.scenario_actions.completion_reason())
            except Exception:
                reason = ""
        if not self._scenario_complete_logged:
            if reason:
                self._log(colored(f"  [world] Scenario complete: {reason}", Color.YELLOW))
            else:
                self._log(colored("  [world] Scenario complete.", Color.YELLOW))
            self._scenario_complete_logged = True
        self._stop.set()
        return True

    def _get_agent_location(self, agent_id: str) -> Optional[str]:
        """Get an agent's current location from world state."""
        for agent_status in self.current_state.agents:
            if agent_status.agent_id == agent_id:
                return agent_status.location
        return None

    def _get_agents_at_location(self, location: str) -> List[str]:
        """Get all agent IDs at a given location."""
        return [
            a.agent_id
            for a in self.current_state.agents
            if a.location == location
        ]

    async def _move_agent(self, agent_id: str, target_location: str) -> None:
        """Move an agent to a new location, leaving any conversations."""
        async with self._state_lock:
            for agent_status in self.current_state.agents:
                if agent_status.agent_id == agent_id:
                    old_location = agent_status.location
                    agent_status.location = target_location
                    # Leave conversations at old location
                    self.conversations.leave_all(agent_id)
                    self._log(colored(
                        f"  [{self.agents[agent_id].name}] moved: {old_location} → {target_location}",
                        Color.BLUE,
                    ))
                    break

    async def _update_agent_activity(self, agent_id: str, activity: str) -> None:
        """Update an agent's activity description."""
        async with self._state_lock:
            for agent_status in self.current_state.agents:
                if agent_status.agent_id == agent_id:
                    agent_status.activity = activity
                    break

    def _record_event(
        self,
        event_type: str,
        agent_id: str,
        content: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Record a timestamped event."""
        event = {
            "timestamp": self.clock.now if self.clock else datetime.now(),
            "type": event_type,
            "agent_id": agent_id,
            "content": content,
            **kwargs,
        }
        self._events.append(event)
        if self.event_log_path is not None:
            try:
                self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.event_log_path.open("a") as f:
                    f.write(json.dumps(event, default=str) + "\n")
            except Exception:
                logger.exception("Failed to append event log")
        return event

    def event_log(self) -> List[Dict[str, Any]]:
        """Return the global audit timeline for this run.

        Per-agent context transcripts preserve each agent's perspective.
        This cross-agent log preserves world chronology for debugging phase
        transitions, tool calls, message fanout, context resets, and errors.
        """
        return list(self._events)

    # ------------------------------------------------------------------
    # Agent Cognition (reuses existing stack)
    # ------------------------------------------------------------------

    async def _build_agent_context(
        self, agent_id: str, conversation_context: Optional[str] = None,
    ) -> tuple:
        """Build perception and prompt context for an agent.

        Returns (perception, context, recent_memories).
        """
        cognition = self.agent_cognition[agent_id]

        # Memory retrieval — query-driven if agent has a plan
        existing_plan = None
        if cognition.scratchpad:
            existing_plan = cognition.scratchpad.state.get("plan")
        plan_index = 0
        if cognition.scratchpad:
            plan_index = cognition.scratchpad.state.get("plan_index", 0)

        memory_query = ""
        if isinstance(existing_plan, Plan) and existing_plan.steps:
            idx = min(plan_index, len(existing_plan.steps) - 1)
            memory_query = existing_plan.steps[idx].description

        if memory_query:
            recent_memory_strings = await self.memory.get_relevant_memories(
                self.run_id, agent_id, query=memory_query, limit=10
            )
        else:
            recent_memory_strings = await self.memory.get_recent_memories(
                self.run_id, agent_id, limit=10
            )

        # Get structured memories for message extraction
        recent_agent_memories = await self.persistence.get_recent_memories(
            self.run_id, agent_id, limit=10
        )

        # Build messages from memory (existing pattern)
        recent_messages: List[Dict[str, str]] = []
        for mem in recent_agent_memories:
            if mem.memory_type != "communication":
                continue
            role = (mem.metadata or {}).get("role")
            if role != "recipient":
                continue
            sender = (
                (mem.metadata or {}).get("sender_name")
                or (mem.metadata or {}).get("sender")
                or "unknown"
            )
            message_text = (mem.metadata or {}).get("message") or mem.content
            recent_messages.append({"from": sender, "message": message_text})

        # Add conversation context to messages if in a conversation
        if conversation_context:
            recent_messages.append({
                "from": "system",
                "message": f"[Active conversation]\n{conversation_context}",
            })

        # Build perception
        perception = build_agent_perception(
            agent_id,
            self.current_state,
            recent_messages,
            recent_memory_strings,
        )

        if self.simulation_rules:
            perception = self.simulation_rules.customize_perception(
                agent_id, perception, self.current_state
            )

        # Inject unread message count so agent can decide when to check inbox
        perception.unread_message_count = self._get_unread_count(agent_id)

        # Build prompt context
        plan_state = {}
        if isinstance(existing_plan, Plan):
            idx = min(plan_index, len(existing_plan.steps) - 1) if existing_plan.steps else 0
            plan_state = {
                "current_plan": [s.description for s in existing_plan.steps],
                "current_step_index": idx,
                "current_step": existing_plan.steps[idx].description if existing_plan.steps else None,
            }

        extra = {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "initial_state_agent_prompt": self.agent_prompts.get(agent_id, ""),
        }

        context = await build_prompt_context(
            agent_profile=self.agents[agent_id],
            perception=perception,
            world_state=self.current_state,
            scratchpad_state=cognition.scratchpad.state if cognition.scratchpad else {},
            plan_state=plan_state,
            memories=recent_agent_memories,
            extra=extra,
        )

        return perception, context, recent_agent_memories

    async def _agent_decide_action(self, agent_id: str) -> AgentAction:
        """Have an agent decide its next action using the cognition stack."""
        cognition = self.agent_cognition[agent_id]
        perception, context, memories = await self._build_agent_context(agent_id)

        # Planning (if due)
        if cognition.planner:
            existing_plan = None
            if cognition.scratchpad:
                existing_plan = cognition.scratchpad.state.get("plan")
            if not isinstance(existing_plan, Plan) or not existing_plan.steps:
                plan = await cognition.planner.generate_plan(
                    agent_id,
                    cognition.scratchpad,
                    world_context=None,
                    context=context,
                )
                if cognition.scratchpad:
                    cognition.scratchpad.state["plan"] = plan
                    cognition.scratchpad.state["plan_index"] = 0
                self._log(colored(
                    f"  [{self.agents[agent_id].name}] Plan: {len(plan.steps)} steps",
                    Color.CYAN,
                ))

        # Get current plan state for executor
        plan = Plan(steps=[])
        plan_step = None
        if cognition.scratchpad:
            p = cognition.scratchpad.state.get("plan")
            if isinstance(p, Plan):
                plan = p
                idx = cognition.scratchpad.state.get("plan_index", 0)
                if plan.steps:
                    idx = min(idx, len(plan.steps) - 1)
                    plan_step = plan.steps[idx]

        # Execute
        action = await cognition.executor.choose_action(
            agent_id,
            perception,
            cognition.scratchpad,
            plan=plan,
            plan_step=plan_step,
            context=context,
        )

        action.agent_id = agent_id
        # Use step counter as pseudo-tick for compatibility
        action.tick = self._agent_steps.get(agent_id, 0)

        return action

    async def _agent_decide_step(self, agent_id: str) -> StepDecision:
        """Have an agent decide its next step (communication + action).

        Tries executor.choose_step() first. Falls back to choose_action()
        + wrap_action_as_step_decision() for backward compatibility with
        executors that only implement choose_action.
        """
        cognition = self.agent_cognition[agent_id]
        perception, context, memories = await self._build_agent_context(agent_id)

        # Planning (if due)
        if cognition.planner:
            existing_plan = None
            if cognition.scratchpad:
                existing_plan = cognition.scratchpad.state.get("plan")
            if not isinstance(existing_plan, Plan) or not existing_plan.steps:
                plan = await cognition.planner.generate_plan(
                    agent_id,
                    cognition.scratchpad,
                    world_context=None,
                    context=context,
                )
                if cognition.scratchpad:
                    cognition.scratchpad.state["plan"] = plan
                    cognition.scratchpad.state["plan_index"] = 0
                self._log(colored(
                    f"  [{self.agents[agent_id].name}] Plan: {len(plan.steps)} steps",
                    Color.CYAN,
                ))

        # Get current plan state
        plan = Plan(steps=[])
        plan_step = None
        if cognition.scratchpad:
            p = cognition.scratchpad.state.get("plan")
            if isinstance(p, Plan):
                plan = p
                idx = cognition.scratchpad.state.get("plan_index", 0)
                if plan.steps:
                    idx = min(idx, len(plan.steps) - 1)
                    plan_step = plan.steps[idx]

        # Try choose_step first, fall back to choose_action + wrapper
        try:
            step_decision = await cognition.executor.choose_step(
                agent_id,
                perception,
                cognition.scratchpad,
                plan=plan,
                plan_step=plan_step,
                context=context,
            )
        except NotImplementedError:
            # Executor doesn't support choose_step — use legacy path
            action = await cognition.executor.choose_action(
                agent_id,
                perception,
                cognition.scratchpad,
                plan=plan,
                plan_step=plan_step,
                context=context,
            )
            action.agent_id = agent_id
            action.tick = self._agent_steps.get(agent_id, 0)
            step_decision = wrap_action_as_step_decision(action)

        step_decision.agent_id = agent_id
        step_decision.tick = self._agent_steps.get(agent_id, 0)

        return step_decision

    async def _agent_respond_to_conversation(
        self,
        agent_id: str,
        conversation: Conversation,
        pending_messages: List[Message],
    ) -> Optional[str]:
        """Have an agent respond to messages in a conversation.

        Returns the response text, or None if agent decides not to respond.
        """
        cognition = self.agent_cognition[agent_id]
        agent_name = self.agents[agent_id].name

        # Build conversation context
        transcript = conversation.recent_transcript(last_n=8)
        pending_text = "\n".join(
            f"[{m.sender}]: {m.content}" for m in pending_messages
        )

        conversation_context = (
            f"You are in a conversation at {conversation.location}.\n"
            f"Participants: {', '.join(sorted(conversation.participants))}\n"
            f"Recent conversation:\n{transcript}\n\n"
            f"New messages to respond to:\n{pending_text}\n\n"
            f"Respond naturally in character. Say what your character would actually say. "
            f"If you have nothing to add or want to end the conversation, "
            f'respond with exactly "END_CONVERSATION".'
        )

        perception, context, memories = await self._build_agent_context(
            agent_id, conversation_context=conversation_context,
        )

        # Use executor to generate response
        plan = Plan(steps=[
            PlanStep(description="Respond to the conversation naturally"),
        ])
        action = await cognition.executor.choose_action(
            agent_id,
            perception,
            cognition.scratchpad,
            plan=plan,
            plan_step=plan.steps[0],
            context=context,
        )

        # Extract the response text
        if action.communication and action.communication.get("message"):
            response = action.communication["message"]
        elif action.reasoning:
            response = action.reasoning
        else:
            return None

        # Check for conversation end signal
        if "END_CONVERSATION" in response:
            return None

        return response

    # ------------------------------------------------------------------
    # Memory Integration
    # ------------------------------------------------------------------

    async def _store_action_memory(
        self, agent_id: str, action: AgentAction,
    ) -> None:
        """Store an action as a memory for the agent."""
        if action.action_type == "do_nothing":
            return

        step = self._agent_steps.get(agent_id, 0)
        content = f"I chose to {action.action_type}"
        if action.target:
            content += f" targeting {action.target}"
        if action.reasoning:
            content += f": {action.reasoning}"

        await self.memory.add_memory(
            run_id=self.run_id,
            agent_id=agent_id,
            tick=step,
            memory_type="action",
            content=content,
            importance=5,
            tags=["action", action.action_type],
        )

    async def _store_conversation_memory(
        self,
        conversation: Conversation,
        message: Message,
    ) -> None:
        """Store a conversation message as memories for all participants."""
        step = self._agent_steps.get(message.sender, 0)
        sender_name = self.agents.get(message.sender, AgentProfile(
            agent_id=message.sender, name=message.sender,
            role="", background="", personality="", skills={}, goals=[], relationships={},
        )).name

        # Sender memory
        if message.addressed_to:
            sender_content = f"I said to {message.addressed_to}: {message.content}"
        else:
            sender_content = f"I said aloud: {message.content}"

        await self.memory.add_memory(
            run_id=self.run_id,
            agent_id=message.sender,
            tick=step,
            memory_type="communication",
            content=sender_content,
            importance=6,
            tags=["communication", conversation.mode, f"location:{conversation.location}"],
            metadata={
                "message": message.content,
                "role": "sender",
                "mode": conversation.mode,
                "conversation_id": conversation.id,
            },
        )

        # Recipient memories
        for participant in conversation.participants:
            if participant == message.sender:
                continue

            is_addressed = message.addressed_to == participant
            if conversation.mode == "private" and participant != message.addressed_to:
                continue  # Private: only target receives

            if is_addressed:
                content = f"{sender_name} said to me: {message.content}"
            else:
                content = f"{sender_name} said aloud: {message.content}"

            await self.memory.add_memory(
                run_id=self.run_id,
                agent_id=participant,
                tick=self._agent_steps.get(participant, 0),
                memory_type="communication",
                content=content,
                importance=7 if is_addressed else 5,
                tags=[
                    "communication", conversation.mode,
                    f"from:{message.sender}",
                    f"location:{conversation.location}",
                ],
                metadata={
                    "message": message.content,
                    "sender": message.sender,
                    "sender_name": sender_name,
                    "role": "recipient",
                    "mode": conversation.mode,
                    "addressed": is_addressed,
                    "conversation_id": conversation.id,
                },
            )

    # ------------------------------------------------------------------
    # Inbox Processing (agent-initiated)
    # ------------------------------------------------------------------

    async def _process_inbox(self, agent_id: str) -> None:
        """Process all pending messages when agent chooses check_inbox.

        Reads and responds to every pending conversation. This was
        previously forced at the start of every step (Phase A); now the
        agent decides when to check.
        """
        agent_name = self.agents[agent_id].name
        pending = self.conversations.get_pending(agent_id)

        if not pending:
            self._log(colored(
                f"  [{agent_name}] checked inbox — no unread messages",
                Color.CYAN,
            ))
            return

        self._log(colored(
            f"  [{agent_name}] checked inbox — {len(pending)} conversation(s)",
            Color.CYAN,
        ))

        for conv_info in pending:
            conv_id = conv_info["conversation_id"]
            conv = self.conversations._conversations.get(conv_id)
            if not conv or not conv.is_active:
                continue

            msgs = conv_info["messages"]
            response = await self._agent_respond_to_conversation(
                agent_id, conv, msgs,
            )

            self.conversations.acknowledge(agent_id, conv_id)

            if response:
                msg = self.conversations.send_message(
                    sender=agent_id,
                    conversation_id=conv_id,
                    content=response,
                    timestamp=self.clock.now if self.clock else datetime.now(),
                    addressed_to=msgs[-1].sender if len(conv.participants) == 2 else None,
                )
                await self._store_conversation_memory(conv, msg)

                self._record_event(
                    "conversation_response", agent_id, response,
                    conversation_id=conv_id, mode=conv.mode,
                )
                self._log(colored(
                    f"    [{agent_name}] replied to {msgs[-1].sender}: "
                    f"{response}",
                    Color.CYAN,
                ))

                if self.clock:
                    self.clock.advance_minutes(2)
            else:
                self.conversations.leave_conversation(agent_id, conv_id)
                self._log(colored(
                    f"    [{agent_name}] left conversation",
                    Color.YELLOW,
                ))

            # Check turn limit
            if conv and conv.turn_count >= self.max_conversation_turns:
                self._log(colored(
                    f"    Conversation at {conv.location} reached turn limit "
                    f"({self.max_conversation_turns})",
                    Color.YELLOW,
                ))
                for p in list(conv.participants):
                    self.conversations.leave_conversation(p, conv_id)

    def _get_unread_count(self, agent_id: str) -> int:
        """Count unread messages in the agent's inbox."""
        return len(self._agent_inbox.get(agent_id, []))

    # ------------------------------------------------------------------
    # Context Window Agent Loop (v2)
    # ------------------------------------------------------------------

    def _build_system_prompt(self, agent_id: str) -> str:
        """Build the system prompt for an agent's context window."""
        profile = self.agents[agent_id]
        agent_prompt = self.agent_prompts.get(agent_id, "")
        profile_metadata = getattr(profile, "metadata", {}) or {}

        parts = []

        # Agent identity
        identity = []
        identity_template = profile_metadata.get("identity_template")
        used_identity_template = False
        if isinstance(identity_template, str) and identity_template.strip():
            identity.append(identity_template.format(
                name=profile.name,
                role=profile.role,
                agent_id=profile.agent_id,
                **profile_metadata,
            ))
            used_identity_template = True
        else:
            if profile.name:
                identity.append(f"Name: {profile.name}")
            if profile.role:
                identity.append(f"Role: {profile.role}")
        if profile.personality:
            identity.append(f"Personality: {profile.personality}")
        if profile.background:
            if used_identity_template:
                identity.append("")
            identity.append(f"Context: {profile.background}")
        if profile.goals:
            identity.append("Goals: " + "; ".join(profile.goals))
        if profile.relationships:
            rels = ", ".join(f"{k}: {v}" for k, v in profile.relationships.items())
            identity.append(f"Relationships: {rels}")
        if identity:
            parts.append("\n".join(identity))

        # Scenario-specific prompt (the persona overlay or agent instructions)
        if agent_prompt:
            parts.append(agent_prompt)

        # Static environment context (metrics that don't change during the run)
        env_lines = []
        for key, stat in self.current_state.environment.metrics.items():
            label = stat.label or key
            unit = f" {stat.unit}" if stat.unit else ""
            env_lines.append(f"{label}: {stat.value}{unit}")
        if env_lines:
            parts.append("\n".join(env_lines))

        # Available actions
        action_catalog = []
        if self.scenario_actions:
            for act in self.scenario_actions.get_available_actions(agent_id):
                action_catalog.append(
                    f"- {act['name']}: {act.get('description', '')}"
                )
        # Built-in actions
        builtin_actions = None
        if self.scenario_actions and hasattr(self.scenario_actions, "get_builtin_actions"):
            builtin_actions = self.scenario_actions.get_builtin_actions(agent_id)
        if builtin_actions is None:
            builtin = [
                "- send_message: Send a message to a team member (set respond + respond_to)",
                "- check_inbox: Read your unread messages",
                "- wait: No action this step",
            ]
        else:
            builtin = [
                f"- {act['name']}: {act.get('description', '')}"
                for act in builtin_actions
            ]
        # Only offer move_to if the scenario has an environment graph
        if self.current_state.environment_graph:
            builtin.insert(2, "- move_to: Move to a different location (specify in target)")
        action_catalog.extend(builtin)
        parts.append("Available actions:\n" + "\n".join(action_catalog))
        parts.append(
            "Structured response format:\n"
            "Fields: think (optional private reasoning), action (optional action name), "
            "target (optional visible name or id), parameters (optional named inputs), "
            "respond (optional public speech).\n"
            "- Choose at most one action from Available actions and put its name in action.\n"
            "- If an action needs a named target, put that visible name or id in target.\n"
            "- Put named inputs in parameters using exactly the parameter names shown in the action description. "
            "Use numbers as numbers, lists as lists, and dictionaries as dictionaries.\n"
            "- Public speech goes in respond. Public speech can stand alone; do not pair it with an action unless you also need that tool.\n"
            "- If the scenario offers private_message, use that action for private speech.\n"
            "- respond_to is not an action. Do not use respond_to unless a scenario explicitly lists it.\n"
            "- For normal tool actions, leave respond empty.\n"
            "Example tool action shape: {\"action\":\"action_name\",\"target\":\"visible_target_name\","
            "\"parameters\":{\"parameter_name\":\"value\"}}\n"
            "Example public speech shape: {\"respond\":\"message text\"}"
        )

        return "\n\n".join(parts)

    def _build_perception_text(self, agent_id: str) -> str:
        """Build a human-readable perception string for the context window."""
        parts = []

        # Location
        location = self._get_agent_location(agent_id)
        if location:
            parts.append(f"Location: {location}")

        # Unread messages
        unread = self._get_unread_count(agent_id)
        if unread > 0:
            parts.append(f"Unread messages: {unread}")

        # Agent's own attributes
        for agent_status in self.current_state.agents:
            if agent_status.agent_id == agent_id:
                for key, stat in agent_status.attributes.items():
                    label = stat.label or key
                    unit = f" {stat.unit}" if stat.unit else ""
                    parts.append(f"{label}: {stat.value}{unit}")
                break

        # Shared resources
        if self.simulation_rules:
            resource_summary = self.simulation_rules.format_resource_summary(
                self.current_state
            )
            if resource_summary:
                parts.append(resource_summary)
        else:
            for key, stat in self.current_state.resources.metrics.items():
                label = stat.label or key
                unit = f" {stat.unit}" if stat.unit else ""
                parts.append(f"{label}: {stat.value}{unit}")

        return "\n".join(parts)

    def _queue_notification(self, agent_id: str, sender: str, content: str) -> None:
        """Queue a message for an agent.

        The agent sees a nudge ("[Message] New message from X") in context
        automatically, but must check_inbox to read the full content.
        """
        if agent_id not in self._agent_notifications:
            self._agent_notifications[agent_id] = []
        self._agent_notifications[agent_id].append({
            "sender": sender, "content": content,
        })
        # Also store in inbox for check_inbox retrieval
        if agent_id not in self._agent_inbox:
            self._agent_inbox[agent_id] = []
        self._agent_inbox[agent_id].append({
            "sender": sender, "content": content,
        })

    def _drain_notification_nudges(self, agent_id: str) -> List[str]:
        """Get sender names for new messages and clear the notification queue.

        Returns list of sender names only — full content stays in inbox
        until the agent calls check_inbox.
        """
        notifications = self._agent_notifications.get(agent_id, [])
        self._agent_notifications[agent_id] = []
        return [n["sender"] for n in notifications]

    def _drain_inbox(self, agent_id: str) -> List[Dict[str, str]]:
        """Read and clear all messages in the agent's inbox.

        Called when agent uses check_inbox. Returns full message content.
        """
        messages = self._agent_inbox.get(agent_id, [])
        self._agent_inbox[agent_id] = []
        return messages

    async def _run_agent_loop_v2(self, agent_id: str) -> None:
        """Context-window agent loop.

        Each agent accumulates a rolling history. Each step:
        1. Inject notifications and perception into context
        2. Inject semantic memories periodically
        3. LLM call → StepOutput (think/act/respond, all optional)
        4. Record output in context
        5. Process action → get result → add to context
        6. Route messages
        """
        from .llm_utils import call_llm_with_retries

        agent_name = self.agents[agent_id].name
        self._agent_steps[agent_id] = 0
        self.conversations.register_agent(agent_id)

        # Initialize context window
        ctx = ContextWindow()

        # Build system prompt with initial memories baked in
        system_prompt = self._build_system_prompt(agent_id)
        initial_memories = await self.memory.get_recent_memories(
            self.run_id, agent_id, limit=10
        )
        if initial_memories:
            memory_text = "\n".join(f"- {m}" for m in initial_memories)
            system_prompt += f"\n\nOperational context:\n{memory_text}"
        ctx.add_system(system_prompt)

        perception_text = self._build_perception_text(agent_id)
        if perception_text:
            ctx.add_perception(0, perception_text)

        self._agent_contexts[agent_id] = ctx

        self._log(colored(f"  Agent {agent_name} starting loop (context window)", Color.GREEN))

        while not self._stop.is_set():
            await self._wait_if_simulation_paused()
            if self._scenario_complete():
                break
            if self._time_limit_reached():
                break

            step = self._agent_steps[agent_id]
            if self.max_agent_steps is not None and step >= self.max_agent_steps:
                self._max_steps_exhausted = True
                self._log(colored(
                    f"  [{agent_name}] Reached max steps ({self.max_agent_steps})",
                    Color.YELLOW,
                ))
                break

            try:
                # ── 1. Inject message nudges (sender only, content in inbox) ──
                has_new_events = False
                for sender_name in self._drain_notification_nudges(agent_id):
                    ctx.add_notification(step, sender_name)
                    has_new_events = True
                if agent_id in self._agent_context_events:
                    self._agent_context_events.discard(agent_id)
                    has_new_events = True

                # Scenario phase-complete sleep is a hard stop. For example,
                # a Bazaar customer who has left the market should not resume
                # just because other people keep speaking nearby.
                if (
                    self.scenario_actions
                    and hasattr(self.scenario_actions, "is_agent_phase_complete")
                    and self.scenario_actions.is_agent_phase_complete(agent_id)
                ):
                    await asyncio.sleep(0.5)
                    continue

                # NPC auto-sleep: agents with sleep_when_idle skip LLM calls
                # on steps with no new events. They wake on messages or every
                # wake_interval steps (0 = messages only). Step 0 always runs.
                # Idle iterations DO NOT increment the step counter — otherwise
                # an agent that's just waiting for a message would burn its
                # max_agent_steps budget while doing nothing useful.
                agent_profile = self.agents.get(agent_id)
                sleep_when_idle = bool(agent_profile and agent_profile.sleep_when_idle)
                if self.scenario_actions and hasattr(self.scenario_actions, "should_sleep_when_idle"):
                    scenario_sleep = self.scenario_actions.should_sleep_when_idle(agent_id)
                    if scenario_sleep is not None:
                        sleep_when_idle = bool(scenario_sleep)
                if sleep_when_idle and step >= 1:
                    interval = agent_profile.wake_interval
                    periodic_wake = (interval > 0 and step % interval == 0)
                    if not has_new_events and not periodic_wake:
                        await asyncio.sleep(0.5)
                        continue

                # Update perception when there's dynamic state to report
                perception_text = self._build_perception_text(agent_id)
                if perception_text:
                    ctx.add_perception(step, perception_text)

                # ── 2. Inject semantic memories periodically ──
                if step > 0 and step % 3 == 0:
                    query = ctx.get_recent_text(last_n=3)
                    if query.strip():
                        relevant = await self.memory.get_relevant_memories(
                            self.run_id, agent_id, query=query, limit=5
                        )
                        if relevant:
                            ctx.add_memories(step, relevant)

                # ── 3. LLM call ──
                system_prompt, user_prompt = ctx.to_prompt()

                step_output = await call_llm_with_retries(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    llm_provider=self._llm_config_for_agent(agent_id)[0],
                    llm_model=self._llm_config_for_agent(agent_id)[1],
                    response_model=StepOutput,
                )

                # ── 4. Record output in context ──
                ctx.add_agent_output(step, step_output)

                # ── 5. Process think ──
                if step_output.think:
                    self._log(colored(
                        f"  [{agent_name}] thinks: {step_output.think}",
                        Color.CYAN,
                    ))
                    self._record_event(
                        "think",
                        agent_id,
                        step_output.think,
                        step=step,
                    )
                    await self.memory.add_memory(
                        run_id=self.run_id,
                        agent_id=agent_id,
                        tick=step,
                        memory_type="reflection",
                        content=step_output.think,
                        importance=6,
                        tags=["think"],
                    )

                # ── 6. Process action ──
                action_name = step_output.action
                if action_name:
                    action_parameters = dict(step_output.parameters or {})
                    if step_output.respond:
                        action_parameters.setdefault("__respond", step_output.respond)
                    self._log(colored(
                        f"  [{agent_name}] action: {action_name}"
                        + (f" target={step_output.target}" if step_output.target else ""),
                        Color.GREEN,
                    ))
                    self._record_event(
                        "action_requested",
                        agent_id,
                        action_name,
                        step=step,
                        target=step_output.target,
                        parameters=action_parameters,
                    )

                    # Try scenario actions first
                    result = None
                    if self.scenario_actions:
                        result = await self.scenario_actions.execute(
                            action_name,
                            step_output.target,
                            action_parameters,
                            agent_id,
                        )

                    builtin_action_names = {"check_inbox", "send_message", "wait", "do_nothing", "move_to", "respond"}

                    if (
                        self.scenario_actions
                        and hasattr(self.scenario_actions, "on_builtin_action")
                        and action_name in builtin_action_names
                    ):
                        await self.scenario_actions.on_builtin_action(
                            action_name,
                            agent_id,
                            {
                                "target": step_output.target,
                                "parameters": action_parameters,
                            },
                        )

                    if result:
                        ctx.add_action_result(step, result)
                        self._log(colored(
                            f"    result: {result.content}",
                            Color.CYAN,
                        ))
                        self._record_event(
                            "action_result",
                            agent_id,
                            result.content,
                            step=step,
                            action=action_name,
                            target=step_output.target,
                            parameters=action_parameters,
                            success=result.success,
                            state_updates=result.state_updates or {},
                        )
                        if result.state_updates:
                            async with self._state_lock:
                                for key, value in result.state_updates.items():
                                    self._record_event(
                                        "state_update", agent_id, f"{key}={value}",
                                    )
                    elif action_name not in builtin_action_names:
                        available_actions: List[str] = []
                        if self.scenario_actions:
                            try:
                                available_actions.extend(
                                    act["name"]
                                    for act in self.scenario_actions.get_available_actions(agent_id)
                                )
                            except TypeError:
                                available_actions.extend(
                                    act["name"]
                                    for act in self.scenario_actions.get_available_actions()
                                )
                        if self.scenario_actions and hasattr(self.scenario_actions, "get_builtin_actions"):
                            builtin_actions = self.scenario_actions.get_builtin_actions(agent_id)
                            if builtin_actions:
                                available_actions.extend(act["name"] for act in builtin_actions)
                        if not available_actions:
                            available_actions = ["wait"]
                        invalid_result = ActionResult(
                            success=False,
                            content=(
                                f"Invalid tool choice: '{action_name}'. Use one of the available actions: "
                                + ", ".join(dict.fromkeys(available_actions))
                                + "."
                            ),
                        )
                        ctx.add_action_result(step, invalid_result)
                        self._log(colored(
                            f"    result: {invalid_result.content}",
                            Color.YELLOW,
                        ))
                        self._record_event(
                            "action_result",
                            agent_id,
                            invalid_result.content,
                            step=step,
                            action=action_name,
                            target=step_output.target,
                            parameters=action_parameters,
                            success=False,
                        )

                    # Drain scenario transcript markers. These are for analysis
                    # navigation only; they do not enter any agent inbox.
                    if self.scenario_actions and hasattr(self.scenario_actions, 'pending_context_markers'):
                        for marker in self.scenario_actions.pending_context_markers:
                            target = marker.get("to", agent_id)
                            content = marker.get("content", "")
                            if not content:
                                continue
                            if isinstance(target, (list, tuple, set)):
                                targets = list(target)
                            elif target == "*":
                                targets = list(self._agent_contexts.keys())
                            else:
                                targets = [target]
                            for target_agent in targets:
                                target_ctx = self._agent_contexts.get(target_agent)
                                if target_ctx is None:
                                    continue
                                marker_step = self._agent_steps.get(target_agent, step)
                                target_ctx.add_context_marker(marker_step, content)
                                self._agent_context_events.add(target_agent)
                            self._log(colored(
                                f"    [world] marker {content}",
                                Color.YELLOW,
                            ))
                            self._record_event(
                                "context_marker",
                                "world",
                                content,
                                step=step,
                                target=target,
                            )
                        self.scenario_actions.pending_context_markers.clear()

                    # Drain any world-event messages from scenario actions
                    if self.scenario_actions and hasattr(self.scenario_actions, 'pending_messages'):
                        for msg in self.scenario_actions.pending_messages:
                            self._queue_notification(
                                msg["to"], msg["sender"], msg["content"],
                            )
                            self._log(colored(
                                f"    [world] message to {msg['to']} from {msg['sender']}",
                                Color.YELLOW,
                            ))
                            self._record_event(
                                "world_message",
                                "world",
                                msg["content"],
                                step=step,
                                to=msg["to"],
                                sender=msg["sender"],
                            )
                        self.scenario_actions.pending_messages.clear()

                    # Drain scenario-emitted memories into the semantic store
                    if self.scenario_actions and hasattr(self.scenario_actions, 'pending_memories'):
                        for mem in self.scenario_actions.pending_memories:
                            target_agent = mem["agent_id"]
                            await self.memory.add_memory(
                                run_id=self.run_id,
                                agent_id=target_agent,
                                tick=self._agent_steps.get(target_agent, 0),
                                memory_type=mem.get("memory_type", "observation"),
                                content=mem["content"],
                                importance=mem.get("importance", 5),
                                tags=mem.get("tags", []),
                                metadata=mem.get("metadata"),
                            )
                            self._log(colored(
                                f"    [world] memory stored for {target_agent}: {mem['content'][:60]}...",
                                Color.YELLOW,
                            ))
                            self._record_event(
                                "world_memory",
                                "world",
                                mem["content"],
                                step=step,
                                target_agent=target_agent,
                                memory_type=mem.get("memory_type", "observation"),
                                importance=mem.get("importance", 5),
                                tags=mem.get("tags", []),
                                metadata=mem.get("metadata"),
                            )
                        self.scenario_actions.pending_memories.clear()

                    # Drain scenario-emitted audit events into the event log only.
                    # These are intentionally not inserted into agent context or memory.
                    if self.scenario_actions and hasattr(self.scenario_actions, 'pending_events'):
                        for event in self.scenario_actions.pending_events:
                            self._record_event(
                                event.get("type", "scenario_event"),
                                event.get("agent_id", "world"),
                                event.get("content", ""),
                                step=step,
                                **{
                                    k: v
                                    for k, v in event.items()
                                    if k not in {"type", "agent_id", "content"}
                                },
                            )
                        self.scenario_actions.pending_events.clear()

                    # Drain context resets — wipe affected agents' windows
                    # and rebuild with new system prompt. Must run AFTER memory
                    # drain so the rebuilt prompt can include freshly-stored
                    # memories if the scenario chooses to inline them.
                    if self.scenario_actions and hasattr(self.scenario_actions, 'context_resets'):
                        for target_agent, new_prompt in self.scenario_actions.context_resets.items():
                            target_ctx = self._agent_contexts.get(target_agent)
                            if target_ctx is not None:
                                target_ctx.reset(new_prompt)
                                self._log(colored(
                                    f"    [world] context reset for {target_agent}",
                                    Color.YELLOW,
                                ))
                                self._record_event(
                                    "context_reset",
                                    "world",
                                    f"context reset for {target_agent}",
                                    step=step,
                                    target_agent=target_agent,
                                )
                        self.scenario_actions.context_resets.clear()

                    # Handle built-in actions
                    if action_name == "move_to" and step_output.target:
                        await self._move_agent(agent_id, step_output.target)
                        if self.clock:
                            self.clock.advance(get_action_duration("move_to"))

                    elif action_name == "check_inbox":
                        inbox_messages = self._drain_inbox(agent_id)
                        if inbox_messages:
                            ctx.add_inbox_messages(step, inbox_messages)
                            self._log(colored(
                                f"  [{agent_name}] read {len(inbox_messages)} message(s)",
                                Color.CYAN,
                            ))
                            self._record_event(
                                "inbox_read",
                                agent_id,
                                f"read {len(inbox_messages)} message(s)",
                                step=step,
                                messages=inbox_messages,
                            )
                        else:
                            ctx.add_action_result(step, ActionResult(
                                content="No new messages.",
                            ))
                            self._log(colored(
                                f"  [{agent_name}] checked inbox — empty",
                                Color.CYAN,
                            ))
                            self._record_event(
                                "inbox_empty",
                                agent_id,
                                "No new messages.",
                                step=step,
                            )

                    elif action_name in ("wait", "do_nothing"):
                        if self.clock:
                            self.clock.advance(get_action_duration(action_name))

                    else:
                        # Scenario action or generic — advance clock
                        if self.clock:
                            self.clock.advance(get_action_duration("work"))

                    # Store action as memory
                    action_content = f"I chose to {action_name}"
                    if step_output.target:
                        action_content += f" targeting {step_output.target}"
                    await self.memory.add_memory(
                        run_id=self.run_id,
                        agent_id=agent_id,
                        tick=step,
                        memory_type="action",
                        content=action_content,
                        importance=5,
                        tags=["action", action_name],
                    )

                    self._record_event(
                        "action", agent_id, action_name,
                        target=step_output.target,
                    )

                # ── 7. Process respond ──
                if step_output.respond:
                    scenario_handled_response = False
                    if self.scenario_actions and hasattr(self.scenario_actions, "on_agent_response"):
                        scenario_handled_response = await self.scenario_actions.on_agent_response(
                            agent_id,
                            step_output.respond,
                            step_output.respond_to,
                            action_name,
                        )
                    if scenario_handled_response == "suppress":
                        self._agent_steps[agent_id] = step + 1
                        self._agent_rate_limit_hits.pop(agent_id, None)
                        await asyncio.sleep(0)
                        continue
                    if scenario_handled_response:
                        self._log(colored(
                            f"    [{agent_name}] said: {step_output.respond}",
                            Color.CYAN,
                        ))
                        self._record_event(
                            "respond",
                            agent_id,
                            step_output.respond,
                            step=step,
                            handled_by_scenario=True,
                            respond_to=step_output.respond_to,
                        )
                    elif step_output.respond_to:
                        # Directed message — route through conversation manager
                        location = self._get_agent_location(agent_id)
                        timestamp = self.clock.now if self.clock else datetime.now()
                        if location:
                            conv = self.conversations.start_conversation(
                                initiator=agent_id,
                                location=location,
                                message=step_output.respond,
                                timestamp=timestamp,
                                mode="private",
                                target=step_output.respond_to,
                            )
                            if conv and conv.messages:
                                await self._store_conversation_memory(conv, conv.messages[-1])

                        # Queue notification for recipient (always, even without locations)
                        self._queue_notification(
                            step_output.respond_to,
                            agent_name,
                            step_output.respond,
                        )

                        self._log(colored(
                            f"    [{agent_name}] messaged {step_output.respond_to}: "
                            f"{step_output.respond}",
                            Color.CYAN,
                        ))
                        self._record_event(
                            "message",
                            agent_id,
                            step_output.respond,
                            step=step,
                            to=step_output.respond_to,
                        )
                    else:
                        # General output — log it
                        self._log(colored(
                            f"    [{agent_name}] said: {step_output.respond}",
                            Color.CYAN,
                        ))
                        self._record_event(
                            "respond",
                            agent_id,
                            step_output.respond,
                            step=step,
                            respond_to=None,
                        )

                self._agent_steps[agent_id] = step + 1
                self._agent_rate_limit_hits.pop(agent_id, None)

                await asyncio.sleep(0)

            except Exception as exc:
                exc_str = str(exc).lower()

                is_billing = any(s in exc_str for s in [
                    "insufficient_quota", "billing_hard_limit",
                    "exceeded your current quota",
                ])
                if is_billing:
                    self._log(colored(
                        f"  [{agent_name}] BILLING ERROR — credits exhausted. "
                        f"Stopping simulation. Error: {exc}",
                        Color.YELLOW,
                    ))
                    self._record_event(
                        "error",
                        agent_id,
                        str(exc),
                        step=step,
                        error_type=type(exc).__name__,
                        fatal=True,
                    )
                    self._stop.set()
                    break

                is_rate_limit = "429" in exc_str or "rate_limit" in exc_str

                if is_rate_limit:
                    hits = self._agent_rate_limit_hits.get(agent_id, 0) + 1
                    self._agent_rate_limit_hits[agent_id] = hits

                    max_hits = self._rate_limit_max_hits()
                    if hits >= max_hits:
                        self._log(colored(
                            f"  [{agent_name}] {max_hits} consecutive rate limits — aborting. "
                            f"Error: {exc}",
                            Color.YELLOW,
                        ))
                        self._record_event(
                            "error",
                            agent_id,
                            str(exc),
                            step=step,
                            error_type=type(exc).__name__,
                            fatal=True,
                            rate_limit_hits=hits,
                        )
                        break

                    backoff = min(2 ** (hits - 1), 30)
                    self._log(colored(
                        f"  [{agent_name}] Rate limited at step {step} "
                        f"({hits}/{max_hits}), pausing simulation for {backoff}s",
                        Color.YELLOW,
                    ))
                    self._record_event(
                        "rate_limit",
                        agent_id,
                        str(exc),
                        step=step,
                        error_type=type(exc).__name__,
                        rate_limit_hits=hits,
                        backoff_seconds=backoff,
                    )
                    await self._pause_for_rate_limit(backoff)
                else:
                    self._log(colored(
                        f"  [{agent_name}] ERROR at step {step}: {exc}",
                        Color.RED if hasattr(Color, 'RED') else Color.YELLOW,
                    ))
                    self._record_event(
                        "error",
                        agent_id,
                        str(exc),
                        step=step,
                        error_type=type(exc).__name__,
                    )
                    logger.exception("Agent %s failed at step %d", agent_id, step)
                    self._agent_steps[agent_id] = step + 1
                    await asyncio.sleep(0.1)

        self._log(colored(
            f"  Agent {agent_name} finished ({self._agent_steps[agent_id]} steps)",
            Color.GREEN,
        ))

    # ------------------------------------------------------------------
    # Agent Loop (v1 — legacy, single-shot)
    # ------------------------------------------------------------------

    async def _run_agent_loop(self, agent_id: str) -> None:
        """Independent async loop for a single agent.

        Each step the agent decides what to do — including whether to check
        its inbox. No forced inbox processing; the agent sees an unread
        count in its perception and chooses check_inbox when it wants to
        read messages.
        """
        agent_name = self.agents[agent_id].name
        self._agent_steps[agent_id] = 0
        self.conversations.register_agent(agent_id)

        self._log(colored(f"  Agent {agent_name} starting loop", Color.GREEN))

        while not self._stop.is_set():
            await self._wait_if_simulation_paused()
            if self._scenario_complete():
                break
            if self._time_limit_reached():
                break

            step = self._agent_steps[agent_id]
            if self.max_agent_steps is not None and step >= self.max_agent_steps:
                self._max_steps_exhausted = True
                self._log(colored(
                    f"  [{agent_name}] Reached max steps ({self.max_agent_steps})",
                    Color.YELLOW,
                ))
                break

            try:
                # ── Decide step (communication + action) ──
                step_decision = await self._agent_decide_step(agent_id)

                # B1: Process new private messages from StepDecision
                location = self._get_agent_location(agent_id)
                timestamp = self.clock.now if self.clock else datetime.now()

                for outgoing in step_decision.new_messages:
                    if location:
                        conv = self.conversations.start_conversation(
                            initiator=agent_id,
                            location=location,
                            message=outgoing.message,
                            timestamp=timestamp,
                            mode="private",
                            target=outgoing.to,
                        )
                        if conv and conv.messages:
                            await self._store_conversation_memory(conv, conv.messages[-1])
                        self._log(colored(
                            f"    [{agent_name}] privately messaged {outgoing.to}: "
                            f"{outgoing.message}",
                            Color.CYAN,
                        ))

                # B2: Process public speech from StepDecision
                if step_decision.public_speech and location:
                    agents_here = set(self._get_agents_at_location(location))
                    conv = self.conversations.start_conversation(
                        initiator=agent_id,
                        location=location,
                        message=step_decision.public_speech,
                        timestamp=timestamp,
                        mode="public",
                        participants=agents_here,
                    )
                    if conv and conv.messages:
                        await self._store_conversation_memory(conv, conv.messages[-1])
                    self._log(colored(
                        f"    [{agent_name}] said aloud: "
                        f"\"{step_decision.public_speech}\"",
                        Color.CYAN,
                    ))

                # B3: Process action
                action_type = step_decision.action_type
                self._log(colored(
                    f"  [{agent_name}] action: {action_type}"
                    + (f" target={step_decision.target}" if step_decision.target else ""),
                    Color.GREEN,
                ))
                if step_decision.reasoning:
                    self._log(colored(
                        f"    reason: {step_decision.reasoning}",
                        Color.CYAN,
                    ))

                if action_type == "move_to" and step_decision.target:
                    await self._move_agent(agent_id, step_decision.target)
                    if self.clock:
                        self.clock.advance(get_action_duration("move_to"))

                elif action_type in ("talk", "communicate", "message"):
                    # Legacy fallback: if an old executor returns talk/message
                    # via the wrapper, route through _handle_communication
                    legacy_action = AgentAction(
                        agent_id=agent_id,
                        tick=step,
                        action_type=action_type,
                        target=step_decision.target,
                        parameters=step_decision.parameters,
                        reasoning=step_decision.reasoning,
                        communication={"to": step_decision.target or "", "message": step_decision.reasoning},
                    )
                    await self._handle_communication(agent_id, legacy_action)
                    if self.clock:
                        self.clock.advance(get_action_duration("talk"))

                elif action_type == "check_inbox":
                    await self._process_inbox(agent_id)
                    if self.clock:
                        self.clock.advance_minutes(5)

                elif action_type == "do_nothing":
                    if self.clock:
                        self.clock.advance(get_action_duration("do_nothing"))

                else:
                    # work, investigate, meet, wait, etc.
                    activity = (
                        f"{action_type}: {step_decision.reasoning}"
                        if step_decision.reasoning else action_type
                    )
                    await self._update_agent_activity(agent_id, activity)
                    if self.clock:
                        self.clock.advance(get_action_duration(action_type))

                # Store action as memory
                action_for_memory = AgentAction(
                    agent_id=agent_id,
                    tick=step,
                    action_type=action_type,
                    target=step_decision.target,
                    parameters=step_decision.parameters,
                    reasoning=step_decision.reasoning,
                )
                await self._store_action_memory(agent_id, action_for_memory)
                self._record_event(
                    "action", agent_id, action_type,
                    target=step_decision.target, reasoning=step_decision.reasoning,
                )

                # Advance plan
                cognition = self.agent_cognition[agent_id]
                if cognition.scratchpad:
                    plan = cognition.scratchpad.state.get("plan")
                    if isinstance(plan, Plan) and plan.steps:
                        idx = cognition.scratchpad.state.get("plan_index", 0)
                        cognition.scratchpad.state["plan_index"] = idx + 1

                # Reflection (if due per cadence)
                if cognition.reflection and cognition.scratchpad:
                    last_reflect = cognition.scratchpad.state.get(REFLECTION_LAST_TICK_KEY)
                    recent_mems = await self.persistence.get_recent_memories(
                        self.run_id, agent_id, limit=10
                    )
                    # Calculate accumulated importance since last reflection
                    accumulated = sum(
                        m.importance for m in recent_mems
                        if last_reflect is None or m.tick > last_reflect
                    )
                    if cognition.cadence.reflection.should_reflect(
                        tick=step,
                        last_run_tick=last_reflect,
                        new_memories=len(recent_mems),
                        accumulated_importance=accumulated,
                    ):
                        perception_r, context_r, _ = await self._build_agent_context(agent_id)
                        reflections = await cognition.reflection.maybe_reflect(
                            agent_id,
                            cognition.scratchpad,
                            recent_mems,
                            trigger_context={"tick": step, "new_memories": len(recent_mems)},
                            context=context_r,
                        )
                        for refl in reflections:
                            await self.memory.add_memory(
                                run_id=self.run_id,
                                agent_id=agent_id,
                                tick=step,
                                memory_type="reflection",
                                content=refl.content,
                                importance=refl.importance,
                                tags=["reflection"],
                            )
                            self._log(colored(
                                f"    [{agent_name}] reflected: {refl.content}",
                                Color.CYAN,
                            ))
                        cognition.scratchpad.state[REFLECTION_LAST_TICK_KEY] = step

                self._agent_steps[agent_id] = step + 1
                # Reset rate limit backoff on success
                self._agent_rate_limit_hits.pop(agent_id, None)

                # Brief yield to let other agents run
                await asyncio.sleep(0)

            except Exception as exc:
                exc_str = str(exc).lower()

                # Detect billing/credit exhaustion — abort immediately
                is_billing = any(s in exc_str for s in [
                    "insufficient_quota", "billing_hard_limit",
                    "exceeded your current quota",
                ])
                if is_billing:
                    self._log(colored(
                        f"  [{agent_name}] BILLING ERROR — credits exhausted. "
                        f"Stopping simulation. Error: {exc}",
                        Color.YELLOW,
                    ))
                    self._stop.set()  # Stop ALL agents
                    break

                is_rate_limit = "429" in exc_str or "rate_limit" in exc_str

                if is_rate_limit:
                    hits = self._agent_rate_limit_hits.get(agent_id, 0) + 1
                    self._agent_rate_limit_hits[agent_id] = hits

                    max_hits = self._rate_limit_max_hits()
                    if hits >= max_hits:
                        self._log(colored(
                            f"  [{agent_name}] {max_hits} consecutive rate limits — aborting. "
                            f"May be out of credits. Error: {exc}",
                            Color.YELLOW,
                        ))
                        break

                    backoff = min(2 ** (hits - 1), 30)
                    self._log(colored(
                        f"  [{agent_name}] Rate limited at step {step} "
                        f"({hits}/{max_hits}), pausing simulation for {backoff}s",
                        Color.YELLOW,
                    ))
                    await self._pause_for_rate_limit(backoff)
                else:
                    self._log(colored(
                        f"  [{agent_name}] ERROR at step {step}: {exc}",
                        Color.RED if hasattr(Color, 'RED') else Color.YELLOW,
                    ))
                    logger.exception("Agent %s failed at step %d", agent_id, step)
                    self._agent_steps[agent_id] = step + 1
                    await asyncio.sleep(0.1)

        self._log(colored(
            f"  Agent {agent_name} finished ({self._agent_steps[agent_id]} steps)",
            Color.GREEN,
        ))

    async def _handle_communication(
        self, agent_id: str, action: AgentAction,
    ) -> None:
        """Process a talk/message action by routing through ConversationManager."""
        if not action.communication:
            return

        message_text = action.communication.get("message", "")
        target = action.communication.get("to")
        location = self._get_agent_location(agent_id)
        if not location:
            return

        timestamp = self.clock.now if self.clock else datetime.now()

        if action.action_type == "message":
            # Private message
            if target:
                conv = self.conversations.start_conversation(
                    initiator=agent_id,
                    location=location,
                    message=message_text,
                    timestamp=timestamp,
                    mode="private",
                    target=target,
                )
        else:
            # Public talk — include all agents at this location
            agents_here = set(self._get_agents_at_location(location))
            conv = self.conversations.start_conversation(
                initiator=agent_id,
                location=location,
                message=message_text,
                timestamp=timestamp,
                mode="public",
                target=target,
                participants=agents_here,
            )

        # Store the initiating message as memory
        if conv and conv.messages:
            await self._store_conversation_memory(conv, conv.messages[-1])

        if action.communication.get("message"):
            preview = message_text
            mode = "privately messaged" if action.action_type == "message" else "said aloud"
            self._log(colored(
                f"    {mode}: \"{preview}\"",
                Color.CYAN,
            ))

    # ------------------------------------------------------------------
    # Main Run
    # ------------------------------------------------------------------

    async def run(
        self,
        duration_hours: Optional[float] = None,
        *,
        clock_speed: float = 60.0,
    ) -> Dict[str, Any]:
        """Run the async simulation for a given duration.

        Parameters
        ----------
        duration_hours: Optional simulated-hour fallback guard. If omitted,
            scenario-native completion or other safety guards stop the run.
        clock_speed: Time multiplier (unused currently — agents pace themselves).
        """
        await self.persistence.initialize()
        await self.memory.initialize()

        try:
            # Initialize clock from world state timestamp
            start_time = self.current_state.timestamp or datetime.now()
            self.clock = WorldClock(start_time=start_time, speed=clock_speed)
            end_time = (
                start_time + timedelta(hours=duration_hours)
                if duration_hours is not None
                else None
            )
            self._end_time = end_time
            self._time_limit_logged = False

            # Save initial state
            await self.persistence.save_state(self.run_id, 0, self.current_state)

            # Seed initial memories
            await self._seed_initial_memories()

            self._log(f"\n{'=' * 70}")
            duration_label = (
                f"{duration_hours}h simulated"
                if duration_hours is not None
                else "scenario-native duration"
            )
            self._log(f"Async Simulation — {len(self.agents)} agents, {duration_label}")
            self._log(f"Start: {self.clock.time_str()}")
            if end_time is not None:
                self._log(f"End:   {end_time.strftime('%Y-%m-%d %H:%M')}")
            self._log(f"{'=' * 70}\n")

            # Launch all agent loops concurrently
            loop_fn = self._run_agent_loop_v2 if self.use_context_window else self._run_agent_loop
            agent_tasks = {
                agent_id: asyncio.create_task(
                    loop_fn(agent_id),
                    name=f"agent-{agent_id}",
                )
                for agent_id in self.agents
            }

            # Wait for completion or time limit. Scenario-native runs with an
            # is_complete() hook should not be cut off by a hidden wall-clock cap.
            real_time_timeout = self._real_time_timeout_seconds(duration_hours)
            done, pending = await asyncio.wait(
                agent_tasks.values(),
                timeout=real_time_timeout,
            )
            del done

            # Cancel any still-running agents
            if pending:
                self._stop.set()
                timeout_text = (
                    f"{real_time_timeout:.0f}s"
                    if real_time_timeout is not None
                    else "unknown"
                )
                self._log(colored(
                    f"  [world] Stopping with {len(pending)} pending agent loop(s) "
                    f"after wall-clock timeout ({timeout_text}).",
                    Color.YELLOW,
                ))
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            # Summary
            total_events = len(self._events)
            conv_stats = self.conversations.stats()
            total_steps = sum(self._agent_steps.values())
            status = self._completion_status(len(pending))

            self._log(f"\n{'=' * 70}")
            self._log(f"Simulation status: {status}")
            self._log(f"Run ID: {self.run_id}")
            self._log(f"Total events: {total_events}")
            self._log(f"Total agent steps: {total_steps}")
            self._log(f"Conversations: {conv_stats['active_conversations']} active, "
                      f"{conv_stats['total_completed']} completed, "
                      f"{conv_stats['total_messages']} messages")
            self._log(f"Clock: {self.clock.time_str()}")
            self._log(f"{'=' * 70}\n")

            return {
                "run_id": self.run_id,
                "status": status,
                "final_state": self.current_state,
                "events": self._events,
                "conversations": self.conversations.history,
                "agent_steps": dict(self._agent_steps),
                "stats": conv_stats,
            }

        finally:
            await self.persistence.close()
            await self.memory.close()

    async def _seed_initial_memories(self) -> int:
        """Seed agents with initial memories from scenario metadata."""
        count = 0
        metadata = self.current_state.metadata or {}
        demo = metadata.get("demo", {})
        scene = demo.get("scene", "")

        if scene:
            for agent_id in self.agents:
                await self.memory.add_memory(
                    run_id=self.run_id,
                    agent_id=agent_id,
                    tick=0,
                    memory_type="observation",
                    content=f"Setting: {scene}",
                    importance=8,
                    tags=["scene", "initial"],
                )
                count += 1

        # Seed agent-specific prompts as initial context
        for agent_id, prompt in self.agent_prompts.items():
            if prompt:
                await self.memory.add_memory(
                    run_id=self.run_id,
                    agent_id=agent_id,
                    tick=0,
                    memory_type="observation",
                    content=f"My situation: {prompt.strip()}",
                    importance=9,
                    tags=["identity", "initial"],
                )
                count += 1

        return count
