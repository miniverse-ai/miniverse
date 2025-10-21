"""Prompt rendering utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .context import PromptContext
from .prompts import DEFAULT_PROMPTS, PromptTemplate


@dataclass
class RenderedPrompt:
    system: str
    user: str


def render_prompt(
    template: PromptTemplate | None,
    context: PromptContext,
    *,
    include_default: bool = True,
) -> RenderedPrompt:
    """Render a prompt template using the supplied context.

    Performs simple placeholder replacement to inject context data into prompt templates.
    Supports multiple format options (JSON, text summary, etc.) to accommodate different
    LLM reasoning styles and token budgets.

    Parameters
    ----------
    template:
        PromptTemplate to render. When ``None`` and ``include_default`` is True,
        the ``DEFAULT_PROMPTS`` library is consulted.
    context:
        Prompt context assembled by the orchestrator.
    include_default:
        Whether to fall back to ``DEFAULT_PROMPTS`` when template is missing.
    """

    # Resolve template reference. If None and defaults enabled, use executor default
    # template. This keeps configuration optional for the common path.
    if template is None and include_default:
        template = DEFAULT_PROMPTS.get("default")
    elif template is None:
        raise ValueError("Prompt template not provided and defaults disabled")

    # Generate all available context formats. Templates choose which placeholders to use
    # based on their needs (compact text vs detailed JSON). Pre-generating all formats
    # simplifies template authoring - no need to call context methods inside templates.
    context_json = (
        context.to_json()
    )  # Full context as JSON (agent profile, perception, world, memories, plan)
    summary = (
        context.summary()
    )  # Human-readable text summary (location, plan steps, recent conversation)
    perception_json = (
        context.perception_json()
    )  # Just perception as JSON (what agent observes)
    perception_text = "\n".join(context.perception.recent_observations)
    plan_json = context.plan_json()  # Just plan as JSON (current step, remaining steps)
    memories_text = context.memories_text()  # Recent memories as bulleted text list
    scratchpad_json = (
        context.scratchpad_json()
    )  # Scratchpad state as JSON (working memory)

    # Build replacement map for placeholder substitution. Placeholders use {{double_brace}}
    # syntax to avoid conflicts with JSON braces. String replacement is simple and fast;
    # no need for complex templating engines (Jinja, Mustache) for this use case.
    # Plain replacements only; templates control structure.
    # Optional placeholders provided via context.extra
    initial_state_agent_prompt = context.extra.get("initial_state_agent_prompt", "")
    simulation_instructions = context.extra.get(
        "simulation_instructions",
        "You are an agent in a simulation. Read perception and return an AgentAction JSON.",
    )
    character_prompt = getattr(context, "character_prompt_text", None)
    character_prompt = character_prompt() if callable(character_prompt) else ""
    # Build action catalog if provided
    action_catalog_items = context.extra.get("available_actions") or []
    if action_catalog_items:
        lines = ["Action Catalog (choose one):"]
        for item in action_catalog_items:
            schema = item.get("schema")
            # Prefer explicit name; fallback to action_type from item or schema; else "(unnamed)"
            name = (
                item.get("name")
                or item.get("action_type")
                or (schema.get("action_type") if isinstance(schema, dict) else None)
                or "(unnamed)"
            )
            lines.append(f"- {name}")
            if schema is not None:
                import json as _json

                lines.append("  Schema:")
                lines.append(
                    "    " + _json.dumps(schema, indent=2).replace("\n", "\n    ")
                )
            examples = item.get("examples") or []
            if examples:
                lines.append("  Examples:")
                for ex in examples:
                    import json as _json

                    pretty = _json.dumps(ex)
                    lines.append(f"    {pretty}")
        action_catalog_str = "\n".join(lines)
    else:
        action_catalog_str = ""

    replacements: Dict[str, str] = {
        "{{context_json}}": context_json,
        "{{context_summary}}": summary,
        "{{perception_json}}": perception_json,
        "{{perception_text}}": perception_text,
        "{{plan_json}}": plan_json,
        "{{memories_text}}": memories_text,
        "{{scratchpad_json}}": scratchpad_json,
        "{{action_catalog}}": action_catalog_str,
        "{{initial_state_agent_prompt}}": initial_state_agent_prompt,
        "{{simulation_instructions}}": simulation_instructions,
        "{{character_prompt}}": character_prompt,
        "{{agent_id}}": getattr(context.agent_profile, "agent_id", ""),
        "{{current_tick}}": str(getattr(context.perception, "tick", "")),
    }

    # Extract system and user prompt components from template. Templates separate system
    # (role definition, constraints) from user (immediate task) to align with LLM APIs.
    system = template.system
    user = template.user

    # Perform placeholder replacement in both system and user prompts. We iterate through
    # all replacements even if template only uses subset - unused placeholders remain
    # as-is (no harm). Multiple passes not needed since placeholders don't nest.
    for placeholder, value in replacements.items():
        system = system.replace(placeholder, value)
        user = user.replace(placeholder, value)

    return RenderedPrompt(system=system, user=user)
