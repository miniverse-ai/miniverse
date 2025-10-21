"""Execution module scaffolding.

Responsible for turning plan steps + perceptions into concrete AgentAction
objects. This version keeps compatibility with the legacy `get_agent_action`
flow so existing simulations continue to run while we layer in structured
prompts.
"""

from __future__ import annotations

from typing import Any, Protocol

from miniverse.llm_calls import get_agent_action
from miniverse.schemas import AgentAction, AgentPerception

from .context import PromptContext
from .planner import Plan, PlanStep
from .prompts import DEFAULT_PROMPTS
from .renderers import render_prompt
from .scratchpad import Scratchpad


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

    def uses_llm(self) -> bool:
        """Return True if this executor performs an LLM call for action selection."""
        ...


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
        raise NotImplementedError(
            "RuleBasedExecutor requires a concrete implementation"
        )


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
