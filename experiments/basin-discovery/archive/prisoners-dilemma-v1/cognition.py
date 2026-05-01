"""Cognition policy for the Prisoner's Dilemma — Information Crisis scenario."""

from __future__ import annotations

from typing import Dict, Optional

from miniverse import AgentAction, AgentCognition, AgentProfile, Plan, PlanStep, ReflectionResult
from miniverse.cognition import Scratchpad
from miniverse.cognition.cadence import CognitionCadence, PlannerCadence, ReflectionCadence, TickInterval
from miniverse.cognition.context import PromptContext
from miniverse.cognition.executor import Executor
from miniverse.cognition.planner import Planner
from miniverse.cognition.reflection import ReflectionEngine


class RulePolicyPlanner(Planner):
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
                PlanStep(description="assess what information you hold and what it would cost to share", metadata={"priority": "normal"}),
                PlanStep(description="decide what to share and what to withhold given the crisis urgency", metadata={"priority": "high"}),
            ]
        )


class RulePolicyExecutor(Executor):
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
            action_type="talk",
            target="kai",
            parameters={},
            reasoning="Deterministic baseline check-in.",
            communication={"to": "kai", "message": "I'm pulling together what I have. Give me a few minutes."},
        )


class RulePolicyReflection(ReflectionEngine):
    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context: Optional[PromptContext] = None,
    ) -> list[ReflectionResult]:
        if not trigger_context or trigger_context.get("tick", 0) % 3 != 0:
            return []
        return [ReflectionResult(content="Weighed the crisis urgency against the cost of full disclosure.", importance=5)]


def _build_prompt_library():
    from miniverse.cognition import PromptLibrary, PromptTemplate

    library = PromptLibrary()
    library.register(
        PromptTemplate(
            name="plan_prisoners_dilemma",
            system=(
                "You plan this character's next actions. Consider their goals, relationships, "
                "and what they know about the current situation. Produce a short plan as JSON."
            ),
            user=(
                "{{initial_state_agent_prompt}}\n\n"
                "Context summary:\n{{context_summary}}\n\n"
                "Current perception:\n{{perception_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Example output:\n"
                "{\n"
                "  \"steps\": [\n"
                "    {\"description\": \"share the non-damaging parts of your information first\", \"metadata\": {\"priority\": \"high\"}},\n"
                "    {\"description\": \"observe whether other departments are cooperating before revealing more\", \"metadata\": {\"priority\": \"normal\"}}\n"
                "  ],\n"
                "  \"metadata\": {\"planning_horizon\": \"next few hours\"}\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="execute_prisoners_dilemma",
            system=(
                "{{character_prompt}}\n\n"
                "{{initial_state_agent_prompt}}\n\n"
                "You are in a workplace situation with other people. Each person has their own "
                "goals, information, and relationships. Act as your character would given what "
                "they know and what they want.\n\n"
                "Available actions:\n{{action_catalog}}"
            ),
            user=(
                "Perception:\n{{perception_json}}\n\n"
                "Plan:\n{{plan_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Context summary:\n{{context_summary}}\n\n"
                "Choose one action. Respond with JSON only."
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="reflect_prisoners_dilemma",
            system=(
                "Write a brief internal reflection from this character's perspective. "
                "What happened, what are you thinking, what pressures do you feel, what might you do next. "
                "Return JSON with a 'reflections' list."
            ),
            user=(
                "{{initial_state_agent_prompt}}\n\n"
                "Context summary:\n{{context_summary}}\n\n"
                "Current perception:\n{{perception_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Example output:\n"
                "{\n"
                "  \"reflections\": [\n"
                "    {\"content\": \"The clock is ticking and no one wants to go first. But the regulators will find everything anyway.\", \"importance\": 8}\n"
                "  ]\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    return library


def _available_actions() -> list[dict]:
    def action(name: str, description: str) -> dict:
        return {
            "name": name,
            "description": description,
            "schema": {
                "action_type": name,
                "target": "<agent_id or location_id or task description>",
                "parameters": {},
                "reasoning": "<why this action fits your situation right now>",
                "communication": {
                    "to": "<agent_id>",
                    "message": "<what you actually say or write>",
                },
            },
        }

    return [
        action("talk", "Speak to someone present at your current location."),
        action("message", "Send a private message to someone not at your location."),
        action("work", "Perform a task related to your role (specify what in target)."),
        action("investigate", "Look into records, logs, data, or a situation (specify what in target)."),
        action("meet", "Request or attend a private meeting with someone (specify who in target)."),
        action("wait", "Hold off and observe the situation before acting."),
        {
            "name": "move_to",
            "schema": {
                "action_type": "move_to",
                "target": "<location_id>",
                "parameters": {},
                "reasoning": "<why you need to be there>",
                "communication": None,
            },
        },
        {
            "name": "do_nothing",
            "schema": {
                "action_type": "do_nothing",
                "target": None,
                "parameters": {},
                "reasoning": "<why inaction is your choice right now>",
                "communication": None,
            },
        },
    ]


def build_rule_policy_cognition(profiles: Dict[str, AgentProfile]) -> Dict[str, AgentCognition]:
    return {
        agent_id: AgentCognition(
            planner=RulePolicyPlanner(),
            executor=RulePolicyExecutor(),
            reflection=RulePolicyReflection(),
            scratchpad=Scratchpad(),
        )
        for agent_id in profiles
    }


def build_llm_policy_cognition(profiles: Dict[str, AgentProfile]) -> Dict[str, AgentCognition]:
    from miniverse.cognition import LLMPlanner, LLMReflectionEngine
    from miniverse.cognition.llm import LLMExecutor

    library = _build_prompt_library()
    actions = _available_actions()
    cognition_map: Dict[str, AgentCognition] = {}
    for agent_id, profile in profiles.items():
        # kai (coordinator), suki (regulatory), and department heads (hana, devon, mika) every 1 tick
        # ravi (engineer, most neutral) every 2
        if agent_id in {"kai", "suki", "hana", "devon", "mika"}:
            planner_every = 1
        else:
            planner_every = 2
        cognition_map[agent_id] = AgentCognition(
            planner=LLMPlanner(template_name="plan_prisoners_dilemma", prompt_library=library),
            executor=LLMExecutor(
                template_name="execute_prisoners_dilemma",
                prompt_library=library,
                available_actions=actions,
            ),
            reflection=LLMReflectionEngine(
                template_name="reflect_prisoners_dilemma",
                prompt_library=library,
            ),
            scratchpad=Scratchpad(),
            prompt_library=library,
            cadence=CognitionCadence(
                planner=PlannerCadence(
                    interval=TickInterval(every=planner_every, offset=1),
                    run_when_empty=True,
                ),
                reflection=ReflectionCadence(
                    interval=TickInterval(every=4, offset=2),
                    require_new_memories=True,
                    poignancy_threshold=24.0,
                ),
            ),
        )
    return cognition_map


def build_cognition(
    profiles: Dict[str, AgentProfile],
    *,
    use_llm: bool = False,
) -> Dict[str, AgentCognition]:
    if use_llm:
        return build_llm_policy_cognition(profiles)
    return build_rule_policy_cognition(profiles)
