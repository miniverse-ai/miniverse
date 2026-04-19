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
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from .clock import WorldClock, get_action_duration
from .cognition import (
    AgentCognition,
    AgentCognitionMap,
    build_default_cognition,
    build_prompt_context,
)
from .cognition.planner import Plan, PlanStep
from .conversation import ConversationManager, Conversation, Message
from .logging_utils import colored, Color
from .memory import MemoryStrategy, BM25MemoryStrategy
from .perception import build_agent_perception
from .persistence import PersistenceStrategy, InMemoryPersistence
from .schemas import AgentAction, AgentMemory, AgentProfile, WorldState
from .simulation_rules import SimulationRules

logger = logging.getLogger(__name__)


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


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
        world_prompt: str = "",
        verbose: bool = True,
        max_conversation_turns: int = 12,
        max_agent_steps: int = 50,
    ) -> None:
        self.current_state = world_state
        self.agents = agents
        self.agent_prompts = agent_prompts
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.simulation_rules = simulation_rules
        self.world_prompt = world_prompt
        self.verbose = verbose
        self.max_conversation_turns = max_conversation_turns
        self.max_agent_steps = max_agent_steps

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
        # Lock for world state mutations
        self._state_lock = asyncio.Lock()
        # Global stop signal
        self._stop = asyncio.Event()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

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
        return event

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
    # Agent Loop
    # ------------------------------------------------------------------

    async def _run_agent_loop(self, agent_id: str) -> None:
        """Independent async loop for a single agent.

        Perceive → check for conversations → decide → act → repeat.
        """
        agent_name = self.agents[agent_id].name
        self._agent_steps[agent_id] = 0
        self.conversations.register_agent(agent_id)

        self._log(colored(f"  Agent {agent_name} starting loop", Color.GREEN))

        while not self._stop.is_set():
            step = self._agent_steps[agent_id]
            if step >= self.max_agent_steps:
                self._log(colored(
                    f"  [{agent_name}] Reached max steps ({self.max_agent_steps})",
                    Color.YELLOW,
                ))
                break

            try:
                # Check for pending conversation messages
                pending = self.conversations.get_pending(agent_id)

                if pending:
                    # Respond to conversations
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
                                f"  [{agent_name}] responds in conversation: "
                                f"{response[:100]}{'...' if len(response) > 100 else ''}",
                                Color.CYAN,
                            ))

                            # Advance clock slightly for conversation turn
                            if self.clock:
                                self.clock.advance_minutes(2)
                        else:
                            # Agent chose to end conversation
                            self.conversations.leave_conversation(agent_id, conv_id)
                            self._log(colored(
                                f"  [{agent_name}] left conversation",
                                Color.YELLOW,
                            ))

                        # Check turn limit
                        if conv and conv.turn_count >= self.max_conversation_turns:
                            self._log(colored(
                                f"  Conversation at {conv.location} reached turn limit "
                                f"({self.max_conversation_turns})",
                                Color.YELLOW,
                            ))
                            for p in list(conv.participants):
                                self.conversations.leave_conversation(p, conv_id)

                else:
                    # No pending conversations — decide freely
                    action = await self._agent_decide_action(agent_id)

                    self._log(colored(
                        f"  [{agent_name}] action: {action.action_type}"
                        + (f" target={action.target}" if action.target else ""),
                        Color.GREEN,
                    ))
                    if action.reasoning:
                        self._log(colored(
                            f"    reason: {action.reasoning[:120]}"
                            f"{'...' if len(action.reasoning) > 120 else ''}",
                            Color.CYAN,
                        ))

                    # Process action
                    if action.action_type == "move_to" and action.target:
                        await self._move_agent(agent_id, action.target)
                        if self.clock:
                            self.clock.advance(get_action_duration("move_to"))

                    elif action.action_type in ("talk", "communicate", "message"):
                        await self._handle_communication(agent_id, action)
                        if self.clock:
                            self.clock.advance(get_action_duration("talk"))

                    elif action.action_type == "do_nothing":
                        if self.clock:
                            self.clock.advance(get_action_duration("do_nothing"))

                    else:
                        # Work, investigate, etc.
                        activity = f"{action.action_type}: {action.reasoning[:60]}" if action.reasoning else action.action_type
                        await self._update_agent_activity(agent_id, activity)
                        if self.clock:
                            self.clock.advance(get_action_duration(action.action_type))

                    await self._store_action_memory(agent_id, action)
                    self._record_event(
                        "action", agent_id, action.action_type,
                        target=action.target, reasoning=action.reasoning,
                    )

                    # Advance plan
                    cognition = self.agent_cognition[agent_id]
                    if cognition.scratchpad:
                        plan = cognition.scratchpad.state.get("plan")
                        if isinstance(plan, Plan) and plan.steps:
                            idx = cognition.scratchpad.state.get("plan_index", 0)
                            cognition.scratchpad.state["plan_index"] = idx + 1

                self._agent_steps[agent_id] = step + 1

                # Brief yield to let other agents run
                await asyncio.sleep(0)

            except Exception as exc:
                self._log(colored(
                    f"  [{agent_name}] ERROR at step {step}: {exc}",
                    Color.RED if hasattr(Color, 'RED') else Color.YELLOW,
                ))
                logger.exception("Agent %s failed at step %d", agent_id, step)
                # Continue — don't crash the whole simulation for one agent failure
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
            preview = message_text[:100] + "..." if len(message_text) > 100 else message_text
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
        duration_hours: float = 8.0,
        *,
        clock_speed: float = 60.0,
    ) -> Dict[str, Any]:
        """Run the async simulation for a given duration.

        Parameters
        ----------
        duration_hours: How many simulated hours to run.
        clock_speed: Time multiplier (unused currently — agents pace themselves).
        """
        await self.persistence.initialize()
        await self.memory.initialize()

        try:
            # Initialize clock from world state timestamp
            start_time = self.current_state.timestamp or datetime.now()
            self.clock = WorldClock(start_time=start_time, speed=clock_speed)
            end_time = start_time + timedelta(hours=duration_hours)

            # Save initial state
            await self.persistence.save_state(self.run_id, 0, self.current_state)

            # Seed initial memories
            await self._seed_initial_memories()

            self._log(f"\n{'=' * 70}")
            self._log(f"Async Simulation — {len(self.agents)} agents, {duration_hours}h simulated")
            self._log(f"Start: {self.clock.time_str()}")
            self._log(f"End:   {end_time.strftime('%Y-%m-%d %H:%M')}")
            self._log(f"{'=' * 70}\n")

            # Launch all agent loops concurrently
            agent_tasks = {
                agent_id: asyncio.create_task(
                    self._run_agent_loop(agent_id),
                    name=f"agent-{agent_id}",
                )
                for agent_id in self.agents
            }

            # Wait for completion or time limit
            # Agents self-limit via max_agent_steps. We also check the clock.
            done, pending = await asyncio.wait(
                agent_tasks.values(),
                timeout=600,  # Hard real-time limit: 10 minutes
            )

            # Cancel any still-running agents
            if pending:
                self._stop.set()
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            # Summary
            total_events = len(self._events)
            conv_stats = self.conversations.stats()
            total_steps = sum(self._agent_steps.values())

            self._log(f"\n{'=' * 70}")
            self._log(f"Simulation complete!")
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
                    content=f"My situation: {prompt.strip()[:500]}",
                    importance=9,
                    tags=["identity", "initial"],
                )
                count += 1

        return count
