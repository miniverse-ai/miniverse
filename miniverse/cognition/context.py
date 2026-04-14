"""Context assembly utilities for cognition prompts.

This module will gather all the data required for plan/execute/reflect
prompts: agent profile, scratchpad state, retrieved memories, environment
summary, etc. For now we define placeholder data structures and helper
functions so executor implementations have a clear contract to target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

import json
from datetime import datetime
import os

from miniverse.schemas import AgentProfile, AgentPerception, AgentMemory, WorldState


@dataclass
class PromptContext:
    """Structured context passed to planner/executor/reflection prompts."""

    agent_profile: AgentProfile
    perception: AgentPerception
    world_snapshot: WorldState
    scratchpad_state: Dict[str, Any] = field(default_factory=dict)
    plan_state: Dict[str, Any] = field(default_factory=dict)
    memories: List[AgentMemory] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        """Return a JSON-serializable payload of the context.

        Information isolation: strips metadata (private prompts, scenario secrets)
        and agents list (other agents' statuses) from world dump. Agents observe
        the world through their perception, not the raw omniscient state.
        """

        world_dump = self.world_snapshot.model_dump(mode="json")
        # Strip runtime/demo metadata to prevent agents from seeing other agents'
        # private prompts or scenario-level secrets.
        world_dump.pop("metadata", None)
        # Strip agents list — agents should see other agents only through
        # perception (partial observability) and memory (what they've observed).
        # Including the full agents list leaks locations, activities, and attributes
        # of all agents, breaking information asymmetry.
        world_dump.pop("agents", None)

        # Sanitize extra but strip LLM credentials and internal keys
        extra_safe = _sanitize(self.extra)
        for key in ("llm_provider", "llm_model", "prompt_library"):
            extra_safe.pop(key, None)

        return {
            "profile": self.agent_profile.model_dump(mode="json"),
            "perception": self.perception.model_dump(mode="json"),
            "world": world_dump,
            "scratchpad": _sanitize(self.scratchpad_state),
            "plan_state": self.plan_state,
            "memories": [memory.model_dump(mode="json") for memory in self.memories],
            "extra": extra_safe,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2, default=_json_default)

    def plan_json(self) -> str:
        return json.dumps(self.plan_state, indent=2, default=_json_default)

    def perception_json(self) -> str:
        return json.dumps(self.perception.model_dump(mode="json"), indent=2, default=_json_default)

    def memories_text(self, limit: int = 5) -> str:
        lines = []
        for memory in self.memories[:limit]:
            lines.append(f"- [{memory.memory_type}] {memory.content}")
        return "\n".join(lines) if lines else "- (none)"

    def scratchpad_json(self) -> str:
        return json.dumps(self.scratchpad_state, indent=2, default=_json_default)

    def summary(self) -> str:
        lines: List[str] = []
        if self.perception.location:
            lines.append(f"Location: {self.perception.location}")
        if self.plan_state.get("steps"):
            lines.append("Plan:")
            for idx, step in enumerate(self.plan_state["steps"]):
                desc = step.get("description", "(no description)")
                lines.append(f"  {idx+1}. {desc}")
        transcript = self.scratchpad_state.get("recent_transcript")
        if transcript:
            lines.append("Recent conversation:")
            for entry in transcript[-3:]:
                lines.append(f"  - {entry}")
        if self.memories:
            lines.append("Recent memories:")
            for memory in self.memories[:5]:
                lines.append(f"  - [{memory.memory_type}] {memory.content}")
        if not lines:
            return "No notable context."
        return "\n".join(lines)

    # Identity text used by templates via {{character_prompt}}
    def character_prompt_text(self) -> str:
        profile = self.agent_profile
        lines: List[str] = []
        if getattr(profile, "name", None):
            lines.append(f"I am {profile.name}.")
        if getattr(profile, "age", None) is not None:
            lines.append(f"I am {profile.age} years old.")
        if getattr(profile, "role", None):
            role_label = str(profile.role).replace("_", " ")
            lines.append(f"I work as a {role_label}.")
        if getattr(profile, "background", None):
            lines.append(f"Background: {profile.background}")
        if getattr(profile, "personality", None):
            personality_text = str(profile.personality).rstrip(". ")
            if personality_text:
                lines.append(f"My personality is {personality_text}.")
        if getattr(profile, "skills", None):
            if isinstance(profile.skills, dict) and profile.skills:
                skill_parts = [f"{k} ({v})" for k, v in profile.skills.items()]
                lines.append("My skills include: " + ", ".join(skill_parts) + ".")
        if getattr(profile, "goals", None):
            if isinstance(profile.goals, list) and profile.goals:
                lines.append("My goals are: " + ", ".join(profile.goals) + ".")
        if getattr(profile, "relationships", None):
            if isinstance(profile.relationships, dict) and profile.relationships:
                lines.append("My relationships with others:")
                for other_id, relation in profile.relationships.items():
                    lines.append(f"- {other_id}: {relation}")
        return "\n".join(lines).strip()


async def build_prompt_context(
    *,
    agent_profile: AgentProfile,
    perception: AgentPerception,
    world_state: WorldState,
    scratchpad_state: Dict[str, Any],
    plan_state: Dict[str, Any],
    memories: Iterable[AgentMemory],
    extra: Dict[str, Any] | None = None,
    ) -> PromptContext:
    """Assemble a `PromptContext` from disparate sources.

    Notes
    -----
    * This helper stays async so future versions can fetch additional data
      (e.g., relevant memories via retrieval strategies) without changing the
      signature.
    * ``extra`` is a generic bag for scenario-specific metadata (shift info,
      KPI summaries, etc.).
    """

    ctx = PromptContext(
        agent_profile=agent_profile,
        perception=perception,
        world_snapshot=world_state,
        scratchpad_state=scratchpad_state,
        plan_state=plan_state,
        memories=list(memories),
        extra=extra or {},
    )

    # Minimal debug logging for prompt context assembly
    # Enable with DEBUG_PROMPT_CONTEXT=1
    try:
        if os.getenv("DEBUG_PROMPT_CONTEXT", "").lower() in ("1", "true", "yes"):  # pragma: no cover
            agent_id = getattr(agent_profile, "agent_id", "?")
            tick = getattr(perception, "tick", "?")
            location = getattr(perception, "location", None)
            messages = getattr(perception, "messages", []) or []
            memories_count = len(ctx.memories)
            extras = ctx.extra or {}
            has_initial_state = bool(extras.get("initial_state_agent_prompt") or extras.get("base_agent_prompt"))
            has_sim_instructions = bool(extras.get("simulation_instructions"))
            available_actions = extras.get("available_actions") or []
            print(f"[PROMPT_CONTEXT] agent={agent_id} tick={tick} location={location}")
            print(f"  memories={memories_count} messages={len(messages)} actions={len(available_actions)}")
            print(f"  initial_state_agent_prompt={'yes' if has_initial_state else 'no'} sim_instructions={'yes' if has_sim_instructions else 'no'}")
    except Exception:
        # Never let debug logging break execution
        pass

    return ctx


def _sanitize(value: Any) -> Any:
    """Recursively sanitize values for JSON serialization.

    Handles Plan objects (convert to dict), nested dicts/lists (recurse), primitives
    (pass through), and non-serializable objects (convert to string repr). This ensures
    scratchpad state (which may contain arbitrary Python objects) can be safely serialized
    to JSON for prompt templates.
    """
    # Special case: Plan objects have steps and metadata. Convert to dict representation
    # for JSON serialization. Try-except handles cases where attributes don't exist.
    if hasattr(value, "steps") and hasattr(value, "metadata"):
        try:
            return {
                "steps": [
                    {"description": step.description, "metadata": step.metadata}
                    for step in value.steps
                ],
                "metadata": value.metadata,
            }
        except Exception:  # pragma: no cover - best effort sanitization
            pass
    # Recursively sanitize dict values. Preserves structure while converting nested objects.
    if isinstance(value, dict):
        return {key: _sanitize(val) for key, val in value.items()}
    # Recursively sanitize list elements
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    # Primitives are JSON-safe - pass through unchanged
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Non-serializable objects - convert to string representation as fallback
    return repr(value)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return repr(obj)
