"""Prompt template scaffolding for cognition stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class PromptTemplate:
    """Represents a templated prompt with placeholders."""

    name: str
    system: str
    user: str
    description: str = ""


class PromptLibrary:
    """Container for named prompt templates per cognition stage."""

    def __init__(self) -> None:
        self.templates: Dict[str, PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        self.templates[template.name] = template

    def get(self, name: str) -> PromptTemplate:
        return self.templates[name]


# Default placeholders ---------------------------------------------------------

DEFAULT_PROMPTS = PromptLibrary()

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="plan",
        system=(
            "You are the agent's planning assistant. Review the provided context and produce a JSON schedule "
            "for the next few hours. Always follow the JSON schema shown in the example."
        ),
        user=(
            "Context summary:\n{{context_summary}}\n\n"
            "Full context JSON:\n{{context_json}}\n\n"
            "Example output:\n"
            "{\n"
            '  "steps": [\n'
            '    {"description": "coordinate morning stand-up", "metadata": {"duration_minutes": 45}},\n'
            '    {"description": "inspect life-support systems", "metadata": {"priority": "high"}}\n'
            "  ],\n"
            '  "metadata": {"planning_horizon": "next 4 hours"}\n'
            "}\n\n"
            "Respond with JSON only."
        ),
        description="Generates an agenda based on goals and memories.",
    )
)

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="default",
        system=(
            "{{character_prompt}}\n\n"
            "{{simulation_instructions}}\n\n"
            "Available actions:\n{{action_catalog}}\n"
        ),
        user=(
            "{{initial_state_agent_prompt}}\n\n" "Perception:\n{{perception_json}}\n"
        ),
        description="Minimal default executor template using character/base/action_catalog placeholders.",
    )
)

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="execute_tick",
        system=(
            "You are the agent's execution module. Decide the next action that best follows the current plan and "
            "the situational context. Respond with valid AgentAction JSON."
        ),
        user=(
            "Perception record:\n{{perception_json}}\n\n"
            "Plan state:\n{{plan_json}}\n\n"
            "Recent memories:\n{{memories_text}}\n\n"
            "Additional context:\n{{context_summary}}\n\n"
            "Guidelines:\n"
            '- Use agent_ids for agent targets and for communication.to (e.g., "beta"), not display names.\n'
            '- If "target" refers to a location, use the location id (e.g., "lab").\n'
            '- Include "communication" only for communicate actions; set it to null otherwise.\n'
            "- action_type can be custom; common values: communicate, work, move_to, rest, investigate, monitor.\n\n"
            "Example outputs (return one):\n\n"
            "Work action:\n"
            "{\n"
            '  "agent_id": "lead",\n'
            '  "tick": 5,\n'
            '  "action_type": "work",\n'
            '  "target": "ops",\n'
            '  "parameters": {"focus": "coordinate technicians"},\n'
            '  "reasoning": "Need to follow up on the backlog and brief the team",\n'
            '  "communication": null\n'
            "}\n\n"
            "Communicate action (use communication with to/message; to must be agent_id):\n"
            "{\n"
            '  "agent_id": "lead",\n'
            '  "tick": 5,\n'
            '  "action_type": "communicate",\n'
            '  "target": "beta",\n'
            '  "parameters": null,\n'
            '  "reasoning": "Need to coordinate with teammate about the briefing",\n'
            '  "communication": {"to": "beta", "message": "Hey, can we sync up about the morning briefing? I want to align on priorities."}\n'
            "}\n\n"
            "Move action:\n"
            "{\n"
            '  "agent_id": "lead",\n'
            '  "tick": 5,\n'
            '  "action_type": "move_to",\n'
            '  "target": "lab",\n'
            '  "parameters": {"speed": "normal"},\n'
            '  "reasoning": "Head to the lab to review sensor data",\n'
            '  "communication": null\n'
            "}\n\n"
            "Rest action:\n"
            "{\n"
            '  "agent_id": "lead",\n'
            '  "tick": 5,\n'
            '  "action_type": "rest",\n'
            '  "target": null,\n'
            '  "parameters": {},\n'
            '  "reasoning": "Low energy. Recuperate this tick.",\n'
            '  "communication": null\n'
            "}\n\n"
            "Investigate action:\n"
            "{\n"
            '  "agent_id": "lead",\n'
            '  "tick": 5,\n'
            '  "action_type": "investigate",\n'
            '  "target": "greenhouse",\n'
            '  "parameters": {"subject": "sensor anomaly"},\n'
            '  "reasoning": "Look into unusual humidity readings",\n'
            '  "communication": null\n'
            "}\n\n"
            "Monitor action:\n"
            "{\n"
            '  "agent_id": "lead",\n'
            '  "tick": 5,\n'
            '  "action_type": "monitor",\n'
            '  "target": "operations",\n'
            '  "parameters": {"metric": "power"},\n'
            '  "reasoning": "Track power fluctuations while team works on recyclers",\n'
            '  "communication": null\n'
            "}\n\n"
            "Custom action (example):\n"
            "{\n"
            '  "agent_id": "lead",\n'
            '  "tick": 5,\n'
            '  "action_type": "study",\n'
            '  "target": null,\n'
            '  "parameters": {"topic": "botany"},\n'
            '  "reasoning": "Prepare for tomorrow\'s greenhouse experiment",\n'
            '  "communication": null\n'
            "}\n\n"
            "Return JSON only."
        ),
        description="Chooses an action for the current tick.",
    )
)

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="reflect_diary",
        system=(
            "You are the reflection module. Summarize key learnings as a short diary entry. Use the JSON schema in "
            "the example so the system can store your reflections."
        ),
        user=(
            "Context summary:\n{{context_summary}}\n\n"
            "Full context JSON:\n{{context_json}}\n\n"
            "Example output:\n"
            "{\n"
            '  "reflections": [\n'
            '    {"content": "Coordinating early keeps the backlog manageable. Need to request more filters.", "importance": 6}\n'
            "  ]\n"
            "}\n\n"
            "Respond with JSON only."
        ),
        description="Produces diary entries when reflection triggers fire.",
    )
)
