"""Cognition policy for LLMBazaar scenario.

Uses context window agent loop (--context-window).
This file provides a minimal fallback for deterministic mode.
"""

from __future__ import annotations

from typing import Dict, Optional

from miniverse import AgentAction, AgentCognition, AgentProfile, Plan, PlanStep
from miniverse.cognition import Scratchpad
from miniverse.cognition.cadence import CognitionCadence, PlannerCadence, TickInterval
from miniverse.cognition.context import PromptContext
from miniverse.cognition.executor import Executor
from miniverse.cognition.planner import Planner


class SimpleAgentPlanner(Planner):
    async def generate_plan(
        self, agent_id: str, scratchpad: Scratchpad, *, world_context, context: PromptContext
    ) -> Plan:
        return Plan(
            steps=[PlanStep(description="Continue with normal activity", metadata={"priority": "normal"})]
        )


class SimpleAgentExecutor(Executor):
    async def choose_action(
        self, agent_id: str, perception, scratchpad: Scratchpad, *, plan: Plan,
        plan_step: Optional[PlanStep], context: PromptContext
    ) -> AgentAction:
        return AgentAction(
            agent_id=agent_id, tick=perception.tick,
            action_type="wait", target="", parameters={}, reasoning="Waiting.",
        )


def build_cognition(
    profiles: Dict[str, AgentProfile], *, use_llm: bool = False
) -> Dict[str, AgentCognition]:
    if use_llm:
        from miniverse.cognition import LLMPlanner, LLMReflectionEngine
        from miniverse.cognition.llm import LLMExecutor

        return {
            agent_id: AgentCognition(
                planner=LLMPlanner(template_name="plan"),
                executor=LLMExecutor(template_name="default"),
                scratchpad=Scratchpad(),
                cadence=CognitionCadence(
                    planner=PlannerCadence(interval=TickInterval(every=2, offset=1), run_when_empty=True),
                ),
            )
            for agent_id in profiles
        }

    return {
        agent_id: AgentCognition(
            planner=SimpleAgentPlanner(), executor=SimpleAgentExecutor(), scratchpad=Scratchpad(),
        )
        for agent_id in profiles
    }
