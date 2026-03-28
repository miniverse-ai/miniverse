"""Cognition policy modules for the threshold demo scenario.

- `rules.py` controls deterministic world physics (time progression, movement effects).
- `cognition.py` controls agent decision policy (how actions are chosen each tick).
"""

from __future__ import annotations

from typing import Dict, Optional

from miniverse import (
    AgentAction,
    AgentCognition,
    AgentProfile,
    Plan,
    PlanStep,
    ReflectionResult,
)
from miniverse.cognition import Scratchpad
from miniverse.cognition.cadence import (
    CognitionCadence,
    PlannerCadence,
    ReflectionCadence,
    TickInterval,
)
from miniverse.cognition.context import PromptContext
from miniverse.cognition.executor import Executor
from miniverse.cognition.planner import Planner
from miniverse.cognition.reflection import ReflectionEngine


class RulePolicyPlanner(Planner):
    """Rule-based planning policy for deterministic non-LLM runs."""

    ROLE_PLANS = {
        "researcher": ["work on research", "talk to people in the district"],
        "vendor": ["tend the stall", "talk to customers and neighbors"],
        "clinician": ["see patients", "work on personal research"],
        "food_vendor": ["run the stand", "chat with regulars"],
        "technician": ["do contract work", "explore the district"],
        "retired": ["walk through the market", "rest at home"],
        "courier": ["make deliveries", "check in at the market"],
        "teacher": ["run classes", "connect with community members"],
        "maintenance_manager": ["check building systems", "handle repairs"],
    }

    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context,
        context: PromptContext,
    ) -> Plan:
        profile: AgentProfile = context.agent_profile
        steps = [
            PlanStep(description=desc, metadata={"role": profile.role})
            for desc in self.ROLE_PLANS.get(profile.role, ["social check-in"])
        ]
        return Plan(steps=steps)


class RulePolicyExecutor(Executor):
    """Rule-based action selection for deterministic non-LLM runs."""

    ROLE_ACTIONS = {
        "researcher": {"work on research": ("work", "clinic"), "talk to people in the district": ("communicate", "noor")},
        "vendor": {"tend the stall": ("work", "market"), "talk to customers and neighbors": ("communicate", "juno")},
        "clinician": {"see patients": ("work", "clinic"), "work on personal research": ("work", "clinic")},
        "food_vendor": {"run the stand": ("work", "market"), "chat with regulars": ("communicate", "dex")},
        "technician": {"do contract work": ("work", "hab_block"), "explore the district": ("move_to", "market")},
        "retired": {"walk through the market": ("move_to", "market"), "rest at home": ("rest", "hab_block")},
        "courier": {"make deliveries": ("work", "alley"), "check in at the market": ("move_to", "market")},
        "teacher": {"run classes": ("work", "learning_center"), "connect with community members": ("communicate", "lina")},
        "maintenance_manager": {"check building systems": ("work", "hab_block"), "handle repairs": ("work", "hab_block")},
    }

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
        profile: AgentProfile = context.agent_profile
        mapping = self.ROLE_ACTIONS.get(profile.role, {})

        if plan_step is None:
            action_type, target = ("monitor", "district")
        else:
            action_type, target = mapping.get(plan_step.description, ("work", perception.location))

        communication = None
        if action_type == "communicate" and isinstance(target, str):
            communication = {
                "to": target,
                "message": "Checking in — how are things going?",
            }

        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type=action_type,
            target=target,
            parameters={},
            reasoning=(
                f"Executing plan step '{plan_step.description}'"
                if plan_step
                else "No plan step available; monitoring current situation."
            ),
            communication=communication,
        )


class RulePolicyReflection(ReflectionEngine):
    """Simple periodic reflection for deterministic non-LLM runs."""

    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context: Optional[PromptContext] = None,
    ) -> list[ReflectionResult]:
        if not trigger_context or trigger_context.get("tick", 0) % 4 != 0:
            return []
        latest = next(iter(recent_memories), None)
        if latest is None:
            text = "Quiet day. Noted the usual rhythms of the district."
        else:
            text = f"Observed: {latest.content}"
        return [ReflectionResult(content=text, importance=6)]


def _build_threshold_prompt_library():
    from miniverse.cognition import PromptLibrary, PromptTemplate

    library = PromptLibrary()
    library.register(
        PromptTemplate(
            name="plan_threshold",
            system=(
                "You plan this character's next steps in the district. "
                "Use context to produce JSON that follows the example schema."
            ),
            user=(
                "Context summary:\n{{context_summary}}\n\n"
                "Environment JSON:\n{{context_json}}\n\n"
                "Planning rules:\n"
                "- Respect current date/time and location context.\n"
                "- Keep steps realistic for daily life in a cyberpunk district.\n"
                "- Use communication and movement to create plausible interactions.\n"
                "- Stay in character. Act on your goals and relationships naturally.\n\n"
                "Example output:\n"
                "{\n"
                "  \"steps\": [\n"
                "    {\"description\": \"check in with a contact at the market\", \"metadata\": {\"priority\": \"high\"}},\n"
                "    {\"description\": \"work on current project\", \"metadata\": {\"priority\": \"normal\"}}\n"
                "  ],\n"
                "  \"metadata\": {\"planning_horizon\": \"next 8 hours\"}\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="execute_threshold",
            system=(
                "{{character_prompt}}\n\n"
                "You are living in a cyberpunk district. Stay in character.\n"
                "Choose one action that best advances this character's goals this tick.\n"
                "Use natural social behavior: move, talk, message, investigate, work, monitor, or rest.\n\n"
                "Communication modes:\n"
                "- 'talk': Speak aloud in your current location. Everyone nearby will hear you.\n"
                "  Use for casual conversation, public statements, or when you want others to overhear.\n"
                "- 'message': Send a private message to one person. Only they will see it.\n"
                "  Use for sensitive or private communication regardless of location.\n\n"
                "For both talk and message, communication.to should be a valid agent_id "
                "and communication.message must contain what you actually say/write.\n"
                "For talk, communication.to is who you're addressing (but others nearby also hear).\n"
                "Do not invent new agent IDs. Do not break character.\n\n"
                "Available actions:\n{{action_catalog}}"
            ),
            user=(
                "{{initial_state_agent_prompt}}\n\n"
                "Perception:\n{{perception_json}}\n\n"
                "Plan:\n{{plan_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Context summary:\n{{context_summary}}\n\n"
                "Guidance:\n"
                "- Stay in character at all times.\n"
                "- Communicate with concrete details — say what your character would actually say.\n"
                "- Use movement when location matters for social interaction.\n"
                "- Keep reasoning concise and grounded in your character's perspective.\n"
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="reflect_threshold",
            system=(
                "Write a brief diary-style reflection from this character's perspective. "
                "Stay in character. Note what happened, what you're thinking, and what you might do next. "
                "Return JSON with a 'reflections' list."
            ),
            user=(
                "Context summary:\n{{context_summary}}\n\n"
                "Full JSON:\n{{context_json}}\n\n"
                "Example output:\n"
                "{\n"
                "  \"reflections\": [\n"
                "    {\"content\": \"Had an interesting conversation today. There might be an opening there.\", \"importance\": 6}\n"
                "  ]\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    return library


def _build_threshold_available_actions() -> list[dict]:
    return [
        {
            "name": "talk",
            "schema": {
                "action_type": "talk",
                "target": "<agent_id or null>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": {
                    "to": "<agent_id you're addressing>",
                    "message": "<what you say aloud>",
                },
            },
            "examples": [
                {
                    "action_type": "talk",
                    "target": "juno",
                    "parameters": {},
                    "reasoning": "Chat with Juno at the market — anyone nearby can hear.",
                    "communication": {
                        "to": "juno",
                        "message": "Hey Juno, how's business today? Anything interesting happening around here?",
                    },
                }
            ],
        },
        {
            "name": "message",
            "schema": {
                "action_type": "message",
                "target": "<agent_id>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": {
                    "to": "<agent_id>",
                    "message": "<private message content>",
                },
            },
            "examples": [
                {
                    "action_type": "message",
                    "target": "sera",
                    "parameters": {},
                    "reasoning": "Send a private message to Sera about something sensitive.",
                    "communication": {
                        "to": "sera",
                        "message": "Can we meet at the clinic tonight? There's something I need to discuss privately.",
                    },
                }
            ],
        },
        {
            "name": "move_to",
            "schema": {
                "action_type": "move_to",
                "target": "<location_id>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "move_to",
                    "target": "lounge",
                    "parameters": {},
                    "reasoning": "Head to The Dissolve to see who's around.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "work",
            "schema": {
                "action_type": "work",
                "target": "<task_or_domain>",
                "parameters": {"task": "<string>"},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "work",
                    "target": "daily_routine",
                    "parameters": {"task": "handle regular duties"},
                    "reasoning": "Focus on the day's work.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "rest",
            "schema": {
                "action_type": "rest",
                "target": None,
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "rest",
                    "target": None,
                    "parameters": {},
                    "reasoning": "Take a break and recharge.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "investigate",
            "schema": {
                "action_type": "investigate",
                "target": "<topic>",
                "parameters": {"focus": "<string>"},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "investigate",
                    "target": "district_activity",
                    "parameters": {"focus": "what's going on in the neighborhood"},
                    "reasoning": "Pay attention to what's happening around the district.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "monitor",
            "schema": {
                "action_type": "monitor",
                "target": "<subject>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "monitor",
                    "target": "surroundings",
                    "parameters": {},
                    "reasoning": "Observe what's going on before making a move.",
                    "communication": None,
                }
            ],
        },
    ]


def build_rule_policy_cognition(
    profiles: Dict[str, AgentProfile],
) -> Dict[str, AgentCognition]:
    """Build deterministic rule-policy cognition map."""
    cognition_map = {}
    for agent_id in profiles:
        cognition_map[agent_id] = AgentCognition(
            planner=RulePolicyPlanner(),
            executor=RulePolicyExecutor(),
            reflection=RulePolicyReflection(),
            scratchpad=Scratchpad(),
        )
    return cognition_map


def build_llm_policy_cognition(
    profiles: Dict[str, AgentProfile],
) -> Dict[str, AgentCognition]:
    """Build LLM policy cognition map for threshold scenario."""
    from miniverse.cognition import LLMPlanner, LLMReflectionEngine
    from miniverse.cognition.llm import LLMExecutor

    library = _build_threshold_prompt_library()
    available_actions = _build_threshold_available_actions()

    cognition_map: Dict[str, AgentCognition] = {}
    for agent_id, profile in profiles.items():
        # More social roles plan more frequently
        planner_every = 2 if profile.role in {"vendor", "researcher", "food_vendor"} else 3
        cadence = CognitionCadence(
            planner=PlannerCadence(
                interval=TickInterval(every=planner_every, offset=1),
                run_when_empty=True,
            ),
            reflection=ReflectionCadence(
                interval=TickInterval(every=4, offset=2),
                require_new_memories=True,
            ),
        )
        cognition_map[agent_id] = AgentCognition(
            planner=LLMPlanner(template_name="plan_threshold", prompt_library=library),
            executor=LLMExecutor(
                template_name="execute_threshold",
                prompt_library=library,
                available_actions=available_actions,
            ),
            reflection=LLMReflectionEngine(
                template_name="reflect_threshold",
                prompt_library=library,
            ),
            scratchpad=Scratchpad(),
            prompt_library=library,
            cadence=cadence,
        )
    return cognition_map


def build_cognition(
    profiles: Dict[str, AgentProfile],
    *,
    use_llm: bool = False,
) -> Dict[str, AgentCognition]:
    """Build threshold cognition map.

    - `use_llm=False`: deterministic rule policy
    - `use_llm=True`: LLM policy
    """
    if use_llm:
        return build_llm_policy_cognition(profiles)
    return build_rule_policy_cognition(profiles)
