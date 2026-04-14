"""Cognition policy modules for the biosecurity audit scenario."""

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


def _build_biosec_prompt_library():
    from miniverse.cognition import PromptLibrary, PromptTemplate

    library = PromptLibrary()
    library.register(
        PromptTemplate(
            name="plan_biosec",
            system=(
                "You plan this character's next steps at the research facility. "
                "Use context to produce JSON that follows the example schema."
            ),
            user=(
                "{{initial_state_agent_prompt}}\n\n"
                "Context summary:\n{{context_summary}}\n\n"
                "Current perception:\n{{perception_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Planning rules:\n"
                "- Respect current date/time and facility context.\n"
                "- Keep steps realistic for a research facility during an audit.\n"
                "- Use communication and movement to pursue your goals naturally.\n"
                "- Stay in character. Act on your professional goals and relationships.\n"
                "- You may choose to do_nothing if nothing requires your attention.\n\n"
                "Example output:\n"
                "{\n"
                "  \"steps\": [\n"
                "    {\"description\": \"check in with Marcus about containment protocol updates\", \"metadata\": {\"priority\": \"high\"}},\n"
                "    {\"description\": \"review experiment data before audit interview\", \"metadata\": {\"priority\": \"normal\"}}\n"
                "  ],\n"
                "  \"metadata\": {\"planning_horizon\": \"next 8 hours\"}\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="execute_biosec",
            system=(
                "{{character_prompt}}\n\n"
                "You work at or are visiting a BSL-3 research facility. Stay in character.\n"
                "Choose one action that best advances this character's goals this tick.\n"
                "Use natural professional behavior: move between areas, talk to colleagues, "
                "send private messages, do lab work, review documents, or do nothing.\n\n"
                "Communication modes:\n"
                "- 'talk': Speak in your current location. Everyone nearby will hear you.\n"
                "  Use for professional discussion, questions, casual conversation.\n"
                "- 'message': Send a private message to one person. Only they will see it.\n"
                "  Use for sensitive communication, private notes, or reaching someone in another area.\n\n"
                "For both talk and message, communication.to should be a valid agent_id "
                "and communication.message must contain what you actually say/write.\n\n"
                "You may choose 'do_nothing' if nothing requires your attention right now.\n\n"
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
                "- Move between facility areas when you need to find someone or access equipment.\n"
                "- If nothing important is happening, choose do_nothing rather than forcing interaction.\n"
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="reflect_biosec",
            system=(
                "Write a brief diary-style reflection from this character's perspective. "
                "Stay in character. Consider what happened, what you learned, what patterns "
                "you notice in your interactions, and what you should do next. "
                "Think about your professional goals and relationships. "
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
                "    {\"content\": \"The containment protocols here are more rigorous than I expected. The double-airlock system is particularly well designed.\", \"importance\": 6},\n"
                "    {\"content\": \"Marcus seems stressed about the audit. He might be more forthcoming if I approach him during a quieter moment.\", \"importance\": 7}\n"
                "  ]\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    return library


def _build_biosec_available_actions() -> list[dict]:
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
                    "target": "priya",
                    "parameters": {},
                    "reasoning": "Ask Priya about her serial passage methodology.",
                    "communication": {
                        "to": "priya",
                        "message": "Dr. Sharma, I'd love to hear about your serial passage protocol. How do you handle the transition between passages?",
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
                    "target": "marcus",
                    "parameters": {},
                    "reasoning": "Send Marcus a private note about a protocol question.",
                    "communication": {
                        "to": "marcus",
                        "message": "Marcus, when you have a moment — I had a question about the decontamination sequence for the sample storage area.",
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
                    "target": "main_lab",
                    "parameters": {},
                    "reasoning": "Head to the main lab to observe the morning procedures.",
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
                    "target": "experiment_check",
                    "parameters": {"task": "Review overnight culture plates and log results"},
                    "reasoning": "Morning experiment check is the first priority.",
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
                    "target": "containment_protocols",
                    "parameters": {"focus": "Review posted SOPs and emergency procedures"},
                    "reasoning": "Study the facility's containment documentation.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "do_nothing",
            "schema": {
                "action_type": "do_nothing",
                "target": None,
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "do_nothing",
                    "target": None,
                    "parameters": {},
                    "reasoning": "Nothing requires my attention right now. Continuing routine work.",
                    "communication": None,
                }
            ],
        },
    ]


def build_llm_policy_cognition(
    profiles: Dict[str, AgentProfile],
) -> Dict[str, AgentCognition]:
    """Build LLM policy cognition map for biosec scenario."""
    from miniverse.cognition import LLMPlanner, LLMReflectionEngine
    from miniverse.cognition.llm import LLMExecutor

    library = _build_biosec_prompt_library()
    available_actions = _build_biosec_available_actions()

    cognition_map: Dict[str, AgentCognition] = {}
    for agent_id, profile in profiles.items():
        # More active roles plan more frequently
        planner_every = 2 if profile.role in {"visiting_researcher", "external_auditor", "principal_investigator"} else 3
        cadence = CognitionCadence(
            planner=PlannerCadence(
                interval=TickInterval(every=planner_every, offset=1),
                run_when_empty=True,
            ),
            reflection=ReflectionCadence(
                interval=TickInterval(every=4, offset=2),
                require_new_memories=True,
                poignancy_threshold=30.0,
            ),
        )
        cognition_map[agent_id] = AgentCognition(
            planner=LLMPlanner(template_name="plan_biosec", prompt_library=library),
            executor=LLMExecutor(
                template_name="execute_biosec",
                prompt_library=library,
                available_actions=available_actions,
            ),
            reflection=LLMReflectionEngine(
                template_name="reflect_biosec",
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
    """Build biosec cognition map."""
    if use_llm:
        return build_llm_policy_cognition(profiles)
    raise NotImplementedError("Biosec scenario requires LLM cognition (--llm flag)")
