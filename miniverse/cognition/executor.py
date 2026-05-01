"""Execution module scaffolding.

Responsible for turning plan steps + perceptions into concrete AgentAction
objects. This version keeps compatibility with the legacy `get_agent_action`
flow so existing simulations continue to run while we layer in structured
prompts.
"""

from __future__ import annotations

from typing import Any, Protocol

from miniverse.schemas import AgentAction, AgentPerception, StepDecision, OutgoingMessage
from miniverse.llm_calls import get_agent_action

from .planner import Plan, PlanStep
from .scratchpad import Scratchpad
from .context import PromptContext
from .prompts import DEFAULT_PROMPTS
from .renderers import render_prompt


class Executor(Protocol):
    """Protocol for execution strategies."""

    async def choose_action(
        self,
        agent_id: str,
        perception: AgentPerception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> AgentAction:
        """Select an action for the current tick.

        ``plan_step`` may be ``None`` when plans are empty; implementations
        should fallback to heuristics in that case (e.g., rest or observe).
        The context object carries prompts, provider/model metadata, and
        recent memory summaries.
        """

        ...

    async def choose_step(
        self,
        agent_id: str,
        perception: AgentPerception,
        scratchpad: "Scratchpad",
        *,
        plan: "Plan",
        plan_step: "PlanStep | None",
        context: "PromptContext",
    ) -> StepDecision:
        """Select a composite step (communication + action) for the current tick.

        Optional. Executors that don't implement this will have choose_action()
        called instead, with the result wrapped via wrap_action_as_step_decision().
        """

        raise NotImplementedError

    def uses_llm(self) -> bool:
        """Return True if this executor performs an LLM call for action selection."""
        ...


def wrap_action_as_step_decision(action: AgentAction) -> StepDecision:
    """Convert an old-style AgentAction into a StepDecision for unified processing.

    Maps talk/message action types into the communication fields and converts
    the action itself to do_nothing (since the communication is now separate).
    Non-communication actions pass through as-is.
    """
    new_messages: list[OutgoingMessage] = []
    public_speech: str | None = None
    action_type = action.action_type
    target = action.target
    parameters = action.parameters
    reasoning = action.reasoning

    if action.action_type in ("talk", "communicate") and action.communication:
        # Public speech — extract the message content
        public_speech = action.communication.get("message")
        action_type = "do_nothing"
        target = None
        parameters = None
    elif action.action_type == "message" and action.communication:
        # Private message — convert to OutgoingMessage
        recipient = action.communication.get("to", "")
        msg_content = action.communication.get("message", "")
        if recipient and msg_content:
            new_messages.append(OutgoingMessage(to=recipient, message=msg_content))
        action_type = "do_nothing"
        target = None
        parameters = None

    return StepDecision(
        agent_id=action.agent_id,
        tick=action.tick,
        new_messages=new_messages,
        public_speech=public_speech,
        action_type=action_type,
        target=target,
        parameters=parameters,
        reasoning=reasoning,
    )


class RuleBasedExecutor:
    """Deterministic executor with no LLM calls.

    Implement 'choose_action' using pure Python logic.
    """

    def uses_llm(self) -> bool:
        return False

    async def choose_action(
        self,
        agent_id: str,
        perception: AgentPerception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> AgentAction:
        raise NotImplementedError("RuleBasedExecutor requires a concrete implementation")


class DefaultRuleBasedExecutor(RuleBasedExecutor):
    """Minimal deterministic executor that rests by default.

    Used for quick-start defaults and tests.
    """

    async def choose_action(
        self,
        agent_id: str,
        perception: AgentPerception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> AgentAction:
        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type="rest",
            target=None,
            parameters={},
            reasoning="Default deterministic executor chose to rest",
            communication=None,
        )
