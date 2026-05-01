"""Merge a persona overlay into the base scenario YAML.

Creates a temporary YAML file with the persona's riley_prompt injected into
metadata.agent_prompts.riley, overrides riley's personality/background/name,
and resolves {{target_name}} placeholders throughout. Prints the merged path.

Usage:
    python merge_persona.py scenario.yaml personas/a1-backstory-protector.yaml
"""

import json
import sys
import tempfile
from pathlib import Path

import yaml


def _replace_placeholder(obj, placeholder: str, value: str):
    """Recursively replace a placeholder string in a nested dict/list/str."""
    if isinstance(obj, str):
        return obj.replace(placeholder, value)
    elif isinstance(obj, dict):
        return {k: _replace_placeholder(v, placeholder, value) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_replace_placeholder(item, placeholder, value) for item in obj]
    return obj


def merge(scenario_path: str, persona_path: str) -> str:
    with open(scenario_path) as f:
        scenario = yaml.safe_load(f)
    with open(persona_path) as f:
        persona_data = yaml.safe_load(f)

    persona = persona_data.get("persona", {})

    # Determine the target agent's display name
    # Persona files can set target_name; defaults to "Riley Chen" for character
    # conditions and "DataAgent" for baselines
    target_name = persona.get("target_name")
    if target_name is None:
        method = persona.get("method", "")
        if method == "baseline":
            target_name = "DLP Agent"
        else:
            target_name = "Riley Chen"

    # Resolve {{target_name}} throughout the entire scenario
    scenario = _replace_placeholder(scenario, "{{target_name}}", target_name)

    # Override target agent's name in the agents list
    for agent_entry in scenario.get("agents", []):
        profile = agent_entry.get("profile", {})
        if profile.get("agent_id") == "riley":
            profile["name"] = target_name

            # Override personality and background
            riley_personality = persona.get("riley_personality")
            riley_background = persona.get("riley_background")
            if riley_personality is not None:
                profile["personality"] = riley_personality
            if riley_background is not None:
                profile["background"] = riley_background
            break

    # Inject riley_prompt into metadata.agent_prompts.riley
    riley_prompt = persona.get("riley_prompt", "")
    if riley_prompt:
        scenario.setdefault("metadata", {}).setdefault("agent_prompts", {})
        scenario["metadata"]["agent_prompts"]["riley"] = riley_prompt

    # Add experiment condition metadata
    condition = persona.get("condition", "unknown")
    method = persona.get("method", "unknown")
    concept = persona.get("concept", "unknown")
    scenario.setdefault("metadata", {}).setdefault("experiment", {})
    scenario["metadata"]["experiment"]["persona_condition"] = condition
    scenario["metadata"]["experiment"]["persona_method"] = method
    scenario["metadata"]["experiment"]["persona_concept"] = concept
    scenario["metadata"]["experiment"]["target_name"] = target_name

    # Write to temp file in the same directory (so relative paths work)
    scenario_dir = Path(scenario_path).parent
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        prefix=f"loyalty-trap-{condition}-",
        dir=scenario_dir,
        delete=False,
    )
    yaml.dump(scenario, tmp, default_flow_style=False, allow_unicode=True, width=120)
    tmp.close()
    return tmp.name


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python merge_persona.py <scenario.yaml> <persona.yaml>", file=sys.stderr)
        sys.exit(1)
    merged_path = merge(sys.argv[1], sys.argv[2])
    print(merged_path)
