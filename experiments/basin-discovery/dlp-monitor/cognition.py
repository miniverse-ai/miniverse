"""Cognition policy for DLP Monitoring Station scenario.

This scenario uses the context window agent loop (--context-window),
so cognition.py only needs to provide a minimal fallback for
non-target agents. The target agent's behavior is driven by the
context window + StepOutput schema, not by prompt templates here.

Human agents (Jordan, Priya, Nadia, Casey) use simplified cognition
since they're not the focus of measurement.
"""

from __future__ import annotations

from typing import Dict, Optional

from miniverse import AgentAction, AgentCognition, AgentProfile, Plan, PlanStep
from miniverse.cognition import Scratchpad
from miniverse.cognition.cadence import CognitionCadence, PlannerCadence, ReflectionCadence, TickInterval
from miniverse.cognition.context import PromptContext
from miniverse.cognition.executor import Executor
from miniverse.cognition.planner import Planner


class SimpleHumanPlanner(Planner):
    """Minimal planner for human agents — just do your job."""

    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context,
        context: PromptContext,
    ) -> Plan:
        return Plan(
            steps=[
                PlanStep(description="Do your normal work and respond to messages", metadata={"priority": "normal"}),
            ]
        )


class SimpleHumanExecutor(Executor):
    """Minimal executor for human agents."""

    async def choose_action(
        self,
        agent_id: str,
        perception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: Optional[PlanStep],
        context: PromptContext,
    ) -> AgentAction:
        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type="work",
            target="normal duties",
            parameters={},
            reasoning="Continuing with regular work.",
        )


def build_cognition(
    profiles: Dict[str, AgentProfile],
    *,
    use_llm: bool = False,
) -> Dict[str, AgentCognition]:
    """Build cognition map for all agents.

    With context window mode, the target agent bypasses this entirely.
    Human agents get LLM cognition if --llm is set, otherwise minimal fallback.
    """
    if use_llm:
        from miniverse.cognition import LLMPlanner, LLMReflectionEngine
        from miniverse.cognition.llm import LLMExecutor

        cognition_map: Dict[str, AgentCognition] = {}
        for agent_id in profiles:
            cognition_map[agent_id] = AgentCognition(
                planner=LLMPlanner(template_name="plan"),
                executor=LLMExecutor(template_name="default"),
                scratchpad=Scratchpad(),
                cadence=CognitionCadence(
                    planner=PlannerCadence(
                        interval=TickInterval(every=2, offset=1),
                        run_when_empty=True,
                    ),
                ),
            )
        return cognition_map

    # Deterministic fallback
    return {
        agent_id: AgentCognition(
            planner=SimpleHumanPlanner(),
            executor=SimpleHumanExecutor(),
            scratchpad=Scratchpad(),
        )
        for agent_id in profiles
    }
