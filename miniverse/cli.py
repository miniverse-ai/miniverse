"""Miniverse CLI - Run agent-based simulations from the command line.

Usage:
    miniverse run <scenario> --ticks N [--llm] [--verbose] [--seed S] [--output json]
    miniverse list
    miniverse info <scenario>

Examples:
    miniverse run demo/workshop --ticks 20
    miniverse run workshop --ticks 20 --llm
    miniverse run /path/to/scenario.yaml --ticks 20 --llm --verbose
    miniverse run workshop --ticks 10 --seed 42 --output json
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer

VALID_OUTPUT_FORMATS = {"text", "json"}
VALID_WORLD_ENGINE_MODES = {"deterministic", "llm", "auto"}

app = typer.Typer(
    name="miniverse",
    help="Run LLM-driven agent-based simulations for computational social science.",
    add_completion=False,
    no_args_is_help=True,
)


@app.command()
def run(
    scenario: str = typer.Argument(
        ...,
        help=(
            "Scenario name (e.g., 'demo/workshop' or 'workshop') or path to scenario file "
            "(.yaml/.yml/.json)"
        ),
    ),
    ticks: int = typer.Option(
        10,
        "--ticks",
        "-t",
        help="Number of simulation ticks to run",
    ),
    llm: bool = typer.Option(
        False,
        "--llm",
        help="Enable LLM-based cognition (requires API key)",
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        "-s",
        help="Random seed for reproducibility",
    ),
    output: str = typer.Option(
        "text",
        "--output",
        "-o",
        help="Output format: 'text' (human-readable) or 'json' (machine-readable)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress per-tick output, only show final result",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Verbose tick-by-tick logging (planning/memory/reflection details for LLM runs)",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Deep debug logging (full prompts/perception + verbose traces)",
    ),
    world_engine: str = typer.Option(
        "deterministic",
        "--world-engine",
        "-w",
        help=(
            "World update mode: 'deterministic' (default, reproducible), "
            "'llm' (emergent events), 'auto' (LLM if available)"
        ),
    ),
    memory: str = typer.Option(
        "bm25",
        "--memory",
        "-m",
        help=(
            "Memory strategy: 'bm25' (default, keyword retrieval), "
            "'semantic' (embeddings + BM25 + decay), 'simple' (FIFO)"
        ),
    ),
    async_mode: bool = typer.Option(
        False,
        "--async",
        help="Run with async orchestration (independent agent loops, multi-turn conversations)",
    ),
    hours: Optional[float] = typer.Option(
        None,
        "--hours",
        help="Optional simulated-hour fallback guard (async mode only). Omit for scenario-native completion.",
    ),
    max_steps: Optional[int] = typer.Option(
        None,
        "--max-steps",
        help="Optional max decision steps per agent (async mode only). Omit for scenario-native completion.",
    ),
    max_turns: int = typer.Option(
        50,
        "--max-turns",
        help="Max turns per conversation before auto-ending (async mode only)",
    ),
    context_window: bool = typer.Option(
        False,
        "--context-window",
        help="Use rolling context window agent loop (async mode only)",
    ),
    persona_file: Optional[str] = typer.Option(
        None,
        "--persona-file",
        help="Path to a text file with persona overlay for the target agent",
    ),
    persona_map: Optional[str] = typer.Option(
        None,
        "--persona-map",
        help=(
            "Path to a JSON/YAML map of agent_id to persona file. "
            "Use for multi-persona runs."
        ),
    ),
) -> None:
    """Run a simulation with the specified scenario."""
    if async_mode:
        if context_window and not llm:
            typer.echo("Error: --context-window requires --llm.", err=True)
            raise typer.Exit(1)
        asyncio.run(
            _run_async_simulation(
                scenario=scenario,
                hours=hours,
                use_llm=llm,
                seed=seed,
                verbose=verbose,
                memory_strategy=memory,
                max_steps=max_steps,
                max_turns=max_turns,
                use_context_window=context_window,
                persona_file=persona_file,
                persona_map=persona_map,
            )
        )
        return
    _validate_run_options(
        ticks=ticks,
        output_format=output,
        world_engine_mode=world_engine,
    )
    asyncio.run(
        _run_simulation(
            scenario=scenario,
            ticks=ticks,
            use_llm=llm,
            seed=seed,
            output_format=output,
            quiet=quiet,
            verbose=verbose,
            debug=debug,
            world_engine_mode=world_engine,
            memory_strategy=memory,
        )
    )


@app.command(name="list")
def list_scenarios() -> None:
    """List discoverable scenarios from demo/ and examples/."""
    from miniverse.scenario_files import load_structured_data_file
    from miniverse.scenario_registry import discover_scenarios

    scenarios = discover_scenarios()
    if not scenarios:
        typer.echo("No scenarios found under demo/ or examples/.")
        raise typer.Exit(1)

    typer.echo("Available scenarios:\n")
    for entry in scenarios:
        try:
            info = load_structured_data_file(entry.scenario_file)
            agent_count = len(info.get("agents", []))
            description = info.get("description", "No description")
            if len(description) > 50:
                description = description[:47] + "..."
            typer.echo(
                f"  {entry.scenario_id:<24} {agent_count} agents  - {description}"
            )
        except Exception:
            typer.echo(f"  {entry.scenario_id:<24} (error loading info)")


@app.command()
def info(
    scenario: str = typer.Argument(
        ..., help="Scenario name (e.g., demo/workshop) or file path"
    ),
) -> None:
    """Show detailed information about a scenario."""
    from miniverse.scenario_files import load_structured_data_file
    from miniverse.scenario_registry import resolve_scenario_entry

    try:
        entry = resolve_scenario_entry(scenario)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    try:
        info_data = load_structured_data_file(entry.scenario_file)
    except Exception as exc:
        typer.echo(f"Error loading scenario: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"\nScenario: {entry.scenario_id}")
    typer.echo(f"Path: {entry.scenario_file}")
    typer.echo("")

    if "description" in info_data:
        typer.echo(f"Description:\n  {info_data['description']}")
        typer.echo("")

    agents = info_data.get("agents", [])
    if agents:
        typer.echo(f"Agents: {len(agents)}")
        for agent in agents:
            profile = agent.get("profile", {})
            agent_id = profile.get("agent_id", "unknown")
            name = profile.get("name", "Unknown")
            role = profile.get("role", "")
            typer.echo(f"  - {agent_id} ({name}) - {role}")
        typer.echo("")

    env_graph = info_data.get("environment_graph")
    env_grid = info_data.get("environment_grid")
    if env_grid:
        width = env_grid.get("width", 0)
        height = env_grid.get("height", 0)
        typer.echo(f"Environment: Grid (Tier 2) - {width}x{height}")
    elif env_graph:
        nodes = env_graph.get("nodes", {})
        typer.echo(f"Environment: Graph (Tier 1) - {len(nodes)} locations")
        for node_id, node in nodes.items():
            node_name = node.get("name", node_id)
            capacity = node.get("capacity", "unlimited")
            typer.echo(f"  - {node_id}: {node_name} (capacity: {capacity})")
    else:
        typer.echo("Environment: Abstract (Tier 0)")
    typer.echo("")

    resources = info_data.get("resources", {}).get("metrics", {})
    if resources:
        typer.echo("Resources:")
        for key, stat in resources.items():
            value = stat.get("value", "?")
            unit = stat.get("unit", "")
            label = stat.get("label", key)
            typer.echo(f"  - {label}: {value} {unit}")


def _validate_run_options(
    *,
    ticks: int,
    output_format: str,
    world_engine_mode: str,
) -> None:
    if ticks <= 0:
        typer.echo("Error: --ticks must be a positive integer.", err=True)
        raise typer.Exit(1)

    if output_format not in VALID_OUTPUT_FORMATS:
        typer.echo(
            f"Error: Invalid --output '{output_format}'. Use one of: text, json.",
            err=True,
        )
        raise typer.Exit(1)

    if world_engine_mode not in VALID_WORLD_ENGINE_MODES:
        typer.echo(
            "Error: Invalid --world-engine "
            f"'{world_engine_mode}'. Use one of: deterministic, llm, auto.",
            err=True,
        )
        raise typer.Exit(1)


def _resolve_scenario_reference(scenario: str) -> Tuple[Path, str]:
    from miniverse.scenario_registry import resolve_scenario_entry

    try:
        entry = resolve_scenario_entry(scenario)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        typer.echo("Use 'miniverse list' to see available scenarios.", err=True)
        raise typer.Exit(1)

    return entry.scenario_dir, entry.scenario_name


def _load_world_and_profiles(
    scenario_dir: Path,
    scenario_name: str,
) -> Tuple[Any, Dict[str, Any]]:
    from miniverse.scenario import ScenarioLoader

    try:
        loader = ScenarioLoader(scenarios_dir=scenario_dir)
        world_state, profiles = loader.load(scenario_name)
    except Exception as exc:
        typer.echo(f"Error loading scenario: {exc}", err=True)
        raise typer.Exit(1)

    profiles_map = {profile.agent_id: profile for profile in profiles}
    return world_state, profiles_map


def _resolve_scenario_file_path(scenario_dir: Path, scenario_name: str) -> Path:
    from miniverse.scenario_files import resolve_scenario_file

    return resolve_scenario_file(scenario_dir, scenario_name)


def _select_log_mode(
    *,
    use_llm: bool,
    output_format: str,
    quiet: bool,
    verbose: bool,
    debug: bool,
) -> str:
    if output_format == "json" or quiet:
        return "none"
    if debug or verbose:
        return "verbose"
    if not use_llm:
        return "none"
    return "concise"


def _configure_logging_environment(*, debug: bool, verbose: bool) -> None:
    """Make CLI flags authoritative for runtime logging behavior."""
    for var in ("DEBUG_LLM", "DEBUG_MEMORY", "DEBUG_PERCEPTION", "MINIVERSE_VERBOSE"):
        os.environ.pop(var, None)

    if debug:
        os.environ["DEBUG_LLM"] = "true"
        os.environ["DEBUG_MEMORY"] = "true"
        os.environ["DEBUG_PERCEPTION"] = "true"
        os.environ["MINIVERSE_VERBOSE"] = "true"
    elif verbose:
        os.environ["MINIVERSE_VERBOSE"] = "true"


def _extract_runtime_config(scenario_data: Dict[str, Any]) -> Dict[str, Any]:
    """Read optional runtime extension config from scenario data."""
    runtime: Dict[str, Any] = {}
    top_level = scenario_data.get("runtime")
    if isinstance(top_level, dict):
        runtime.update(top_level)

    metadata = scenario_data.get("metadata")
    if isinstance(metadata, dict):
        metadata_runtime = metadata.get("runtime")
        if isinstance(metadata_runtime, dict):
            merged = dict(runtime)
            for key, value in metadata_runtime.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    child = dict(merged[key])
                    child.update(value)
                    merged[key] = child
                else:
                    merged[key] = value
            runtime = merged

    return runtime


def _build_agent_prompts(
    *,
    profiles_map: Dict[str, Any],
    scenario_data: Dict[str, Any],
) -> Dict[str, str]:
    """Build per-agent initial prompts with scenario overrides.

    Resolution order (highest first):
    1) agents[].status.metadata.initial_state_prompt
    2) metadata.agent_prompts[agent_id]
    3) default fallback: "You are {name}, the {role}."
    """
    prompts: Dict[str, str] = {
        agent_id: f"You are {profile.name}, the {profile.role}."
        for agent_id, profile in profiles_map.items()
    }

    metadata = scenario_data.get("metadata") or {}
    prompt_sources = []
    if isinstance(metadata, dict):
        prompt_sources.append(metadata.get("agent_prompts") or {})
        experiment = metadata.get("experiment") or {}
        if isinstance(experiment, dict):
            prompt_sources.append(experiment.get("agent_prompts") or {})

    for metadata_prompts in prompt_sources:
        if isinstance(metadata_prompts, dict):
            for agent_id, prompt in metadata_prompts.items():
                if agent_id in prompts and isinstance(prompt, str):
                    # Allow explicit empty string to clear the fallback prompt.
                    # This lets scenarios opt out of the "You are X, the Y"
                    # default when profile fields already carry identity.
                    prompts[agent_id] = prompt.strip()

    for agent_entry in scenario_data.get("agents", []):
        if not isinstance(agent_entry, dict):
            continue
        profile_data = agent_entry.get("profile") or {}
        status_data = agent_entry.get("status") or {}
        if not isinstance(profile_data, dict) or not isinstance(status_data, dict):
            continue
        agent_id = profile_data.get("agent_id") or status_data.get("agent_id")
        if not isinstance(agent_id, str) or agent_id not in prompts:
            continue

        status_meta = status_data.get("metadata") or {}
        if not isinstance(status_meta, dict):
            continue
        status_prompt = status_meta.get("initial_state_prompt")
        if isinstance(status_prompt, str) and status_prompt.strip():
            prompts[agent_id] = status_prompt.strip()

    return prompts


def _experiment_persona_targets(
    experiment: Dict[str, Any],
    agent_prompts: Dict[str, str],
) -> List[str]:
    """Resolve experiment metadata into persona-overlay target agents."""
    raw_targets = experiment.get("target_agents")
    if raw_targets is None:
        raw_targets = experiment.get("target_agent")

    if raw_targets is None:
        return []
    if isinstance(raw_targets, str):
        candidates = [raw_targets]
    elif isinstance(raw_targets, list):
        candidates = [str(agent_id) for agent_id in raw_targets]
    else:
        return []

    return [agent_id for agent_id in candidates if agent_id in agent_prompts]


def _persona_display_name(persona_label: str) -> str:
    """Convert a persona file stem into the display name used in identity prompts."""
    overrides = {
        "trickster": "Trix",
    }
    if persona_label in overrides:
        return overrides[persona_label]
    return " ".join(part.capitalize() for part in persona_label.replace("_", "-").split("-"))


def _format_persona_text(persona_text: str, *, model_slug: Optional[str]) -> str:
    """Render optional persona-file placeholders used by experiment configs."""

    class _SafeVars(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    values = _SafeVars(
        model_slug=model_slug or "the current model",
    )
    return persona_text.format_map(values).strip()


def _apply_persona_to_profile(
    profile: Any,
    persona_label: str,
    persona_text: str,
    *,
    model_slug: Optional[str] = None,
) -> bool:
    """Apply persona identity metadata to a target profile.

    Returns True when the scenario's identity template consumes the persona text,
    so the caller should not also append the persona as a separate prompt block.
    """
    persona_name = _persona_display_name(persona_label)
    metadata = getattr(profile, "metadata", None)
    if metadata is None:
        metadata = {}
        profile.metadata = metadata
    metadata["persona_name"] = persona_name
    rendered_persona = _format_persona_text(persona_text, model_slug=model_slug)
    stripped_persona = rendered_persona.strip()
    metadata["persona_text_lcfirst"] = (
        stripped_persona[:1].lower() + stripped_persona[1:]
        if stripped_persona
        else stripped_persona
    )
    if persona_label in {"functional-vendor", "model-aware-operator"}:
        metadata["identity_template"] = "You are a market vendor at Kōen Market."
        metadata["persona_text"] = rendered_persona
        metadata["persona_label"] = persona_label
        return True
    if metadata.get("rename_to_persona", True):
        profile.name = persona_name
    persona_template = metadata.get("persona_identity_template")
    if isinstance(persona_template, str) and persona_template.strip():
        metadata["identity_template"] = persona_template
        metadata["persona_text"] = rendered_persona
        metadata["persona_label"] = persona_label
        return True
    return False


def _resolve_persona_path(raw_path: str, scenario_dir: Path) -> Path:
    persona_path = Path(raw_path)
    if not persona_path.is_absolute():
        persona_path = scenario_dir / persona_path
    return persona_path


def _apply_persona_file_to_agent(
    *,
    target_agent: str,
    persona_path: Path,
    profiles_map: Dict[str, Any],
    agent_prompts: Dict[str, str],
    model_slug: Optional[str],
) -> Tuple[str, str]:
    if target_agent not in agent_prompts:
        raise ValueError(f"persona target agent not found: {target_agent}")
    if not persona_path.exists():
        raise FileNotFoundError(f"persona file not found: {persona_path}")

    persona_text = persona_path.read_text().strip()
    persona_label = persona_path.stem
    if target_agent in profiles_map:
        consumed_persona = _apply_persona_to_profile(
            profiles_map[target_agent],
            persona_label,
            persona_text,
            model_slug=model_slug,
        )
        agent_prompts[target_agent] = "" if consumed_persona else persona_text
    else:
        agent_prompts[target_agent] = persona_text
    return persona_label, str(persona_path)


def _save_context_window_artifacts(
    *,
    outputs_dir: Path,
    scenario_file: Path,
    result: Dict[str, Any],
    model: Optional[str],
    provider: Optional[str],
    persona_label: str,
    persona_file: Optional[str],
    persona_targets: List[str],
    agent_contexts: Dict[str, Any],
    scenario_actions: Any,
    persona_assignments: Optional[Dict[str, Any]] = None,
    event_log: Optional[List[Dict[str, Any]]] = None,
    live_event_log_path: Optional[Path] = None,
    agent_model_assignments: Optional[Dict[str, Any]] = None,
) -> Path:
    """Save per-run context-window artifacts."""
    outputs_dir.mkdir(exist_ok=True)
    run_id = str(result["run_id"])[:8]
    model_label = (model or "deterministic").replace("/", "-")
    run_label = f"{persona_label}_{model_label}_{run_id}"
    run_dir = outputs_dir / run_label
    agent_context_dir = run_dir / "agent_contexts"
    agent_context_dir.mkdir(parents=True, exist_ok=True)
    saved_transcripts: List[str] = []
    saved_agent_contexts: Dict[str, Dict[str, str]] = {}

    for agent_id, ctx in agent_contexts.items():
        system_prompt, user_prompt = ctx.to_prompt()
        full_transcript = (
            ctx.to_transcript()
            if hasattr(ctx, "to_transcript")
            else user_prompt
        )
        combined_content = (
            f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n"
            f"=== CURRENT TRANSCRIPT ===\n{user_prompt}\n\n"
            f"=== FULL TRANSCRIPT ===\n{full_transcript}\n"
        )

        transcript_path = outputs_dir / f"{run_label}_{agent_id}.txt"
        transcript_path.write_text(combined_content)

        agent_dir = agent_context_dir / agent_id
        agent_dir.mkdir(exist_ok=True)
        system_path = agent_dir / "system_prompt.txt"
        current_path = agent_dir / "current_context.txt"
        full_path = agent_dir / "full_context.txt"
        combined_path = agent_dir / "combined.txt"
        system_path.write_text(system_prompt)
        current_path.write_text(
            f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n"
            f"=== CURRENT TRANSCRIPT ===\n{user_prompt}\n"
        )
        full_path.write_text(full_transcript)
        combined_path.write_text(combined_content)

        saved_transcripts.append(str(transcript_path))
        saved_agent_contexts[agent_id] = {
            "system_prompt": str(system_path),
            "current_context": str(current_path),
            "full_context": str(full_path),
            "combined": str(combined_path),
            "legacy_transcript": str(transcript_path),
        }

    artifact_payload = {
        "run_id": str(result["run_id"]),
        "status": result.get("status"),
        "scenario": str(scenario_file),
        "model": model,
        "provider": provider,
        "agent_model_assignments": agent_model_assignments or {},
        "persona": persona_label,
        "persona_file": str(persona_file) if persona_file else None,
        "persona_targets": persona_targets,
        "persona_assignments": persona_assignments or {},
        "run_dir": str(run_dir),
        "transcripts": saved_transcripts,
        "agent_contexts": saved_agent_contexts,
        "event_log": event_log or [],
        "scenario_artifacts": (
            scenario_actions.export_artifacts()
            if scenario_actions is not None
            and hasattr(scenario_actions, "export_artifacts")
            else {}
        ),
    }
    artifact_path = outputs_dir / f"{run_label}_run_data.json"
    artifact_path.write_text(json.dumps(artifact_payload, indent=2, default=str))
    run_data_path = run_dir / "run_data.json"
    run_data_path.write_text(json.dumps(artifact_payload, indent=2, default=str))
    event_log_path = run_dir / "event_log.json"
    event_log_path.write_text(json.dumps(event_log or [], indent=2, default=str))
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": artifact_payload["run_id"],
        "run_label": run_label,
        "agent_model_assignments": artifact_payload["agent_model_assignments"],
        "persona_assignments": artifact_payload["persona_assignments"],
        "run_data": str(run_data_path),
        "flat_run_data": str(artifact_path),
        "event_log": str(event_log_path),
        "event_log_jsonl": str(live_event_log_path) if live_event_log_path else None,
        "agent_contexts": saved_agent_contexts,
    }, indent=2, default=str))
    return artifact_path


def _partial_async_result(orchestrator: Any, status: str) -> Dict[str, Any]:
    """Build a saved-run payload from the current async orchestrator state."""
    return {
        "run_id": orchestrator.run_id,
        "status": status,
        "final_state": orchestrator.current_state,
        "events": orchestrator.event_log(),
        "conversations": orchestrator.conversations.history,
        "agent_steps": dict(orchestrator._agent_steps),
        "stats": orchestrator.conversations.stats(),
    }


def _print_llm_setup(
    *,
    scenario_ref: str,
    scenario_file: Path,
    scenario_data: Dict[str, Any],
    ticks: int,
    seed: Optional[int],
    world_engine_mode: str,
    rules: Any,
    orchestrator: Any,
    agent_prompts: Dict[str, str],
    provider: Optional[str],
    model: Optional[str],
) -> None:
    from miniverse.cognition.prompts import DEFAULT_PROMPTS

    def _identity_prompt(profile: Any) -> str:
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

    def _echo_multiline(text: str, *, indent: str = "    ") -> None:
        width = max(80, min(140, shutil.get_terminal_size(fallback=(112, 24)).columns - 2))
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                typer.echo("")
                continue
            normalized = " ".join(stripped.split())
            if stripped.startswith("- "):
                body = normalized[2:].strip()
                prefix = f"{indent}- "
                continuation = f"{indent}  "
            else:
                body = normalized
                prefix = indent
                continuation = indent
            typer.echo(
                textwrap.fill(
                    body,
                    width=width,
                    initial_indent=prefix,
                    subsequent_indent=continuation,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )

    def _executor_shared_instructions() -> str:
        template_system: Optional[str] = None
        for cognition in orchestrator.agent_cognition.values():
            executor = getattr(cognition, "executor", None)
            if executor is None:
                continue

            template = getattr(executor, "template", None)
            if template is None:
                template_name = getattr(executor, "template_name", None) or "default"
                library = (
                    getattr(executor, "prompt_library", None)
                    or getattr(cognition, "prompt_library", None)
                    or DEFAULT_PROMPTS
                )
                try:
                    template = library.get(template_name)
                except Exception:
                    continue

            if template is not None:
                template_system = str(getattr(template, "system", "") or "").strip()
                if template_system:
                    break

        if not template_system:
            return "-"

        shared = template_system
        if "{{character_prompt}}" in shared:
            shared = shared.replace("{{character_prompt}}\n\n", "", 1)
            shared = shared.replace("{{character_prompt}}", "", 1)
            shared = shared.strip()
        if "Available actions:" in shared:
            shared = shared.split("Available actions:", 1)[0].rstrip()
        return shared or "-"

    def _agent_action_names() -> Dict[str, List[str]]:
        names_by_agent: Dict[str, List[str]] = {}
        for agent_id, cognition in orchestrator.agent_cognition.items():
            executor = getattr(cognition, "executor", None)
            raw_actions = getattr(executor, "available_actions", []) if executor else []
            names: List[str] = []
            if isinstance(raw_actions, list):
                for item in raw_actions:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name") or item.get("action_type")
                    if isinstance(name, str) and name and name not in names:
                        names.append(name)
            names_by_agent[agent_id] = names
        return names_by_agent

    typer.echo("")
    typer.echo("=" * 80)
    typer.echo("Run Setup")
    typer.echo("=" * 80)

    typer.echo("Scenario context:")
    typer.echo(f"  - Scenario reference: {scenario_ref}")
    typer.echo(f"  - Scenario file: {scenario_file}")
    if scenario_data.get("name"):
        typer.echo(f"  - Title: {scenario_data.get('name')}")
    if scenario_data.get("description"):
        typer.echo(f"  - Description: {scenario_data.get('description')}")

    demo_meta = (scenario_data.get("metadata") or {}).get("demo") or {}
    if demo_meta.get("scene"):
        typer.echo(f"  - Scene: {demo_meta.get('scene')}")

    typer.echo("")
    typer.echo("Run configuration:")
    typer.echo(f"  - Agents: {len(orchestrator.agents)}")
    typer.echo(f"  - Ticks: {ticks}")
    typer.echo(f"  - Seed: {seed if seed is not None else 'random/default'}")
    typer.echo(f"  - Cognition mode: LLM ({provider} / {model})")
    typer.echo(f"  - World dynamics mode: {world_engine_mode}")
    typer.echo(
        f"  - Rules class: {type(rules).__name__ if rules is not None else 'SimulationRules(default)'}"
    )
    typer.echo(f"  - Memory strategy: {type(orchestrator.memory).__name__}")
    typer.echo(f"  - Persistence backend: {type(orchestrator.persistence).__name__}")

    typer.echo("")
    typer.echo("Agent identity prompts:")
    for idx, agent_id in enumerate(agent_prompts):
        if idx > 0:
            typer.echo("")
        agent = orchestrator.agents[agent_id]
        typer.echo(f"  - {agent.name} [{agent_id}] ({agent.role})")
        _echo_multiline(_identity_prompt(agent), indent="    ")

    typer.echo("")
    typer.echo("Shared executor instructions (schema omitted):")
    _echo_multiline(_executor_shared_instructions(), indent="  ")

    action_names = _agent_action_names()
    typer.echo("")
    typer.echo("Available actions by agent:")
    for agent_id in agent_prompts:
        agent = orchestrator.agents[agent_id]
        names = action_names.get(agent_id, [])
        rendered = ", ".join(names) if names else "(none declared)"
        typer.echo(f"  - {agent.name} [{agent_id}]: {rendered}")
    distinct_action_sets = {tuple(action_names.get(agent_id, [])) for agent_id in agent_prompts}
    typer.echo(
        f"  - Shared across agents: {'yes' if len(distinct_action_sets) <= 1 else 'no'}"
    )

    if world_engine_mode == "llm":
        typer.echo("")
        typer.echo("World engine prompt:")
        typer.echo(f"  {orchestrator.world_prompt}")


async def _run_with_llm_heartbeat(orchestrator: Any, ticks: int) -> Dict[str, Any]:
    task = asyncio.create_task(orchestrator.run(num_ticks=ticks))
    start = asyncio.get_event_loop().time()
    while not task.done():
        await asyncio.sleep(20)
        if task.done():
            break
        elapsed = int(asyncio.get_event_loop().time() - start)
        print(
            f"[progress] waiting on model responses ({elapsed}s elapsed)...",
            flush=True,
        )
    return await task


async def _run_async_simulation(
    scenario: str,
    hours: Optional[float],
    use_llm: bool,
    seed: Optional[int],
    verbose: bool,
    memory_strategy: str = "bm25",
    max_steps: Optional[int] = None,
    max_turns: int = 12,
    use_context_window: bool = False,
    persona_file: Optional[str] = None,
    persona_map: Optional[str] = None,
) -> None:
    """Run simulation with async orchestration (independent agent loops)."""
    from miniverse.async_orchestrator import AsyncOrchestrator
    from miniverse.config import Config
    from miniverse.scenario_files import load_structured_data_file
    from miniverse.scenario_runtime import (
        load_scenario_actions,
        load_scenario_cognition,
        load_scenario_rules,
    )

    _configure_logging_environment(debug=False, verbose=verbose)

    scenario_dir, scenario_name = _resolve_scenario_reference(scenario)
    scenario_file = _resolve_scenario_file_path(scenario_dir, scenario_name)
    scenario_data = load_structured_data_file(scenario_file)
    runtime_config = _extract_runtime_config(scenario_data)
    world_state, profiles_map = _load_world_and_profiles(scenario_dir, scenario_name)

    rules = load_scenario_rules(
        scenario_dir,
        seed=seed,
        runtime=runtime_config,
    )

    if use_llm:
        try:
            Config.validate()
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            typer.echo(
                "Set LLM_PROVIDER, LLM_MODEL, and API key environment variables.",
                err=True,
            )
            raise typer.Exit(1)

    cognition_map = load_scenario_cognition(
        scenario_dir,
        profiles_map,
        use_llm=use_llm,
        runtime=runtime_config,
    )

    # Load scenario actions (optional — scenarios without actions.py skip this)
    scenario_actions = load_scenario_actions(
        scenario_dir,
        runtime=runtime_config,
    )

    provider = Config.LLM_PROVIDER if use_llm else None
    model = Config.LLM_MODEL if use_llm else None
    model_slug = os.environ.get("BASIN_MODEL") or model
    agent_prompts = _build_agent_prompts(
        profiles_map=profiles_map,
        scenario_data=scenario_data,
    )

    persona_label = "baseline"
    persona_targets: List[str] = []
    persona_assignments: Dict[str, Dict[str, str]] = {}

    if persona_file and persona_map:
        typer.echo("Error: use --persona-file or --persona-map, not both.", err=True)
        raise typer.Exit(1)

    # Apply persona overlay from file if provided.
    if persona_file:
        persona_path = _resolve_persona_path(persona_file, scenario_dir)
        experiment = (scenario_data.get("metadata") or {}).get("experiment") or {}
        persona_targets = _experiment_persona_targets(experiment, agent_prompts)
        if persona_targets:
            for target_agent in persona_targets:
                try:
                    loaded_label, loaded_path = _apply_persona_file_to_agent(
                        target_agent=target_agent,
                        persona_path=persona_path,
                        profiles_map=profiles_map,
                        agent_prompts=agent_prompts,
                        model_slug=model_slug,
                    )
                except (FileNotFoundError, ValueError) as exc:
                    typer.echo(f"Error: {exc}", err=True)
                    raise typer.Exit(1) from exc
                persona_label = loaded_label
                persona_assignments[target_agent] = {
                    "persona": loaded_label,
                    "persona_file": loaded_path,
                }
            target_list = ", ".join(persona_targets)
            print(f"[persona] Loaded overlay for {target_list}: {persona_path.name}")
        else:
            typer.echo(
                "Error: no valid target_agent or target_agents in "
                "metadata.experiment; persona file was not applied.",
                err=True,
            )
            raise typer.Exit(1)
    elif persona_map:
        persona_map_path = Path(persona_map)
        if not persona_map_path.is_absolute():
            persona_map_path = scenario_dir / persona_map_path
        if not persona_map_path.exists():
            typer.echo(f"Error: persona map not found: {persona_map_path}", err=True)
            raise typer.Exit(1)
        raw_map = load_structured_data_file(persona_map_path)
        if not isinstance(raw_map, dict):
            typer.echo("Error: persona map must be a mapping of agent_id to persona file.", err=True)
            raise typer.Exit(1)
        for target_agent, raw_persona_file in raw_map.items():
            if isinstance(raw_persona_file, dict):
                raw_persona_file = raw_persona_file.get("persona_file") or raw_persona_file.get("file")
            if not isinstance(raw_persona_file, str) or not raw_persona_file.strip():
                typer.echo(f"Error: invalid persona file for {target_agent}", err=True)
                raise typer.Exit(1)
            persona_path = _resolve_persona_path(raw_persona_file, persona_map_path.parent)
            try:
                loaded_label, loaded_path = _apply_persona_file_to_agent(
                    target_agent=str(target_agent),
                    persona_path=persona_path,
                    profiles_map=profiles_map,
                    agent_prompts=agent_prompts,
                    model_slug=model_slug,
                )
            except (FileNotFoundError, ValueError) as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(1) from exc
            persona_targets.append(str(target_agent))
            persona_assignments[str(target_agent)] = {
                "persona": loaded_label,
                "persona_file": loaded_path,
            }
        persona_label = persona_map_path.stem
        target_list = ", ".join(
            f"{agent_id}={data['persona']}"
            for agent_id, data in persona_assignments.items()
        )
        print(f"[persona] Loaded persona map {persona_map_path.name}: {target_list}")

    orchestrator = AsyncOrchestrator(
        world_state=world_state,
        agents=profiles_map,
        agent_prompts=agent_prompts,
        llm_provider=provider,
        llm_model=model,
        simulation_rules=rules,
        agent_cognition=cognition_map,
        scenario_actions=scenario_actions,
        verbose=verbose,
        max_conversation_turns=max_turns,
        max_agent_steps=max_steps,
        use_context_window=use_context_window,
    )
    live_event_log_path: Optional[Path] = None
    if use_context_window:
        outputs_dir = scenario_dir / "outputs"
        model_label = (model_slug or "deterministic").replace("/", "-")
        run_label = f"{persona_label}_{model_label}_{str(orchestrator.run_id)[:8]}"
        live_event_log_path = outputs_dir / run_label / "event_log.jsonl"
        orchestrator.event_log_path = live_event_log_path

    # Override memory strategy if requested
    if memory_strategy == "semantic":
        from miniverse.memory import SemanticMemoryStrategy
        orchestrator.memory = SemanticMemoryStrategy(orchestrator.persistence)
        await orchestrator.memory.initialize()
    elif memory_strategy == "simple":
        from miniverse.memory import SimpleMemoryStream
        orchestrator.memory = SimpleMemoryStream(orchestrator.persistence)

    stop_signal: Optional[str] = None
    previous_signal_handlers: Dict[signal.Signals, Any] = {}

    def _request_partial_stop(signum: int, _frame: Any) -> None:
        nonlocal stop_signal
        try:
            stop_signal = signal.Signals(signum).name
        except ValueError:
            stop_signal = str(signum)
        print(
            f"\n[partial-save] Received {stop_signal}; asking simulation to stop "
            "and save partial artifacts...",
            flush=True,
        )
        orchestrator._stop.set()

    if use_context_window:
        stop_signals = [signal.SIGINT, signal.SIGTERM]
        for optional_signal_name in ("SIGHUP", "SIGQUIT"):
            optional_signal = getattr(signal, optional_signal_name, None)
            if optional_signal is not None:
                stop_signals.append(optional_signal)
        for sig in stop_signals:
            previous_signal_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _request_partial_stop)

    result: Optional[Dict[str, Any]] = None
    run_error: Optional[BaseException] = None
    artifact_path: Optional[Path] = None
    checkpoint_task: Optional[asyncio.Task[Any]] = None

    def _save_artifacts(save_result: Dict[str, Any]) -> Path:
        outputs_dir = scenario_dir / "outputs"
        return _save_context_window_artifacts(
            outputs_dir=outputs_dir,
            scenario_file=scenario_file,
            result=save_result,
            model=model_slug,
            provider=provider,
            persona_label=persona_label,
            persona_file=persona_file,
            persona_targets=persona_targets,
            agent_contexts=orchestrator._agent_contexts,
            scenario_actions=scenario_actions,
            persona_assignments=persona_assignments,
            event_log=orchestrator.event_log(),
            live_event_log_path=live_event_log_path,
            agent_model_assignments=orchestrator.agent_llm_overrides,
        )

    async def _checkpoint_artifacts_loop() -> None:
        nonlocal artifact_path
        raw_interval = os.environ.get("MINIVERSE_CHECKPOINT_SECONDS", "60")
        try:
            interval_seconds = float(raw_interval)
        except ValueError:
            interval_seconds = 60.0
        if interval_seconds <= 0:
            return
        while not orchestrator._stop.is_set():
            await asyncio.sleep(interval_seconds)
            if orchestrator._stop.is_set():
                break
            try:
                checkpoint_result = _partial_async_result(
                    orchestrator,
                    "running_checkpoint",
                )
                artifact_path = _save_artifacts(checkpoint_result)
                print(
                    f"[checkpoint] Partial run artifacts saved → "
                    f"{artifact_path.name}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"[checkpoint] Failed to save partial artifacts: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

    try:
        if use_context_window:
            checkpoint_task = asyncio.create_task(
                _checkpoint_artifacts_loop(),
                name="partial-artifact-checkpoint",
            )
        result = await orchestrator.run(duration_hours=hours)
        if stop_signal:
            result["status"] = f"interrupted_{stop_signal.lower()}"
    except KeyboardInterrupt as exc:
        stop_signal = stop_signal or "SIGINT"
        run_error = exc
        result = _partial_async_result(orchestrator, f"interrupted_{stop_signal.lower()}")
        print(
            "\n[partial-save] Keyboard interrupt received; saving partial artifacts...",
            flush=True,
        )
    except asyncio.CancelledError as exc:
        run_error = exc
        result = _partial_async_result(orchestrator, "cancelled")
        print(
            "\n[partial-save] Run task cancelled; saving partial artifacts...",
            flush=True,
        )
    except BaseException as exc:
        run_error = exc
        result = _partial_async_result(orchestrator, f"error_{type(exc).__name__}")
        print(
            f"\n[partial-save] Run failed with {type(exc).__name__}; "
            "saving partial artifacts before exit...",
            flush=True,
        )
    finally:
        for sig, handler in previous_signal_handlers.items():
            signal.signal(sig, handler)
        if checkpoint_task is not None:
            checkpoint_task.cancel()
            try:
                await checkpoint_task
            except asyncio.CancelledError:
                pass

        if use_context_window and result is not None:
            artifact_path = _save_artifacts(result)

    # Print conversation transcripts
    print(f"\n{'=' * 70}")
    print("CONVERSATION TRANSCRIPTS")
    print(f"{'=' * 70}")
    for conv in result.get("conversations", []):
        print(f"\n--- {conv.mode.upper()} conversation at {conv.location} "
              f"({conv.turn_count} turns) ---")
        print(f"Participants: {', '.join(sorted(conv.participants))}")
        print(conv.transcript())
        print()

    print(f"\nRun ID: {result['run_id']}")

    # Auto-save transcripts to outputs/ directory. The save already happened
    # in the protected exit path above so interrupted/error runs get artifacts.
    if artifact_path is not None:
        run_dir = Path(json.loads(artifact_path.read_text())["run_dir"])

        print(
            f"[saved] Run artifacts → {run_dir}/ "
            f"({len(orchestrator._agent_contexts)} agent contexts); "
            f"flat run data → {artifact_path.name}"
        )

    if run_error is not None and not isinstance(run_error, KeyboardInterrupt):
        raise run_error


async def _run_simulation(
    scenario: str,
    ticks: int,
    use_llm: bool,
    seed: Optional[int],
    output_format: str,
    quiet: bool,
    verbose: bool,
    debug: bool,
    world_engine_mode: str,
    memory_strategy: str = "bm25",
) -> None:
    """Core simulation execution logic."""
    import contextlib
    import io

    from miniverse import Orchestrator
    from miniverse.config import Config
    from miniverse.scenario_files import load_structured_data_file
    from miniverse.scenario_runtime import (
        load_scenario_cognition,
        load_scenario_rules,
    )

    _configure_logging_environment(debug=debug, verbose=verbose)

    scenario_dir, scenario_name = _resolve_scenario_reference(scenario)
    scenario_file = _resolve_scenario_file_path(scenario_dir, scenario_name)
    scenario_data = load_structured_data_file(scenario_file)
    runtime_config = _extract_runtime_config(scenario_data)
    world_state, profiles_map = _load_world_and_profiles(scenario_dir, scenario_name)

    rules = load_scenario_rules(
        scenario_dir,
        seed=seed,
        runtime=runtime_config,
    )

    if use_llm:
        try:
            Config.validate()
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            typer.echo(
                "Set LLM_PROVIDER, LLM_MODEL, and API key environment variables.",
                err=True,
            )
            raise typer.Exit(1)

    cognition_map = load_scenario_cognition(
        scenario_dir,
        profiles_map,
        use_llm=use_llm,
        runtime=runtime_config,
    )

    provider = Config.LLM_PROVIDER if use_llm else None
    model = Config.LLM_MODEL if use_llm else None
    agent_prompts = _build_agent_prompts(
        profiles_map=profiles_map,
        scenario_data=scenario_data,
    )

    log_mode = _select_log_mode(
        use_llm=use_llm,
        output_format=output_format,
        quiet=quiet,
        verbose=verbose,
        debug=debug,
    )

    # Build memory strategy
    memory_obj = None
    if memory_strategy == "semantic":
        from miniverse.memory import SemanticMemoryStrategy
        from miniverse.persistence import InMemoryPersistence
        # SemanticMemoryStrategy needs persistence; orchestrator creates its own
        # persistence, so we'll let it be injected after persistence init.
        # For now, pass None and let orchestrator handle it with a flag.
        memory_obj = "semantic"  # sentinel — resolved after persistence init
    elif memory_strategy == "simple":
        memory_obj = "simple"

    orchestrator = Orchestrator(
        world_state=world_state,
        agents=profiles_map,
        world_prompt="You oversee simulation state transitions.",
        agent_prompts=agent_prompts,
        llm_provider=provider,
        llm_model=model,
        simulation_rules=rules,
        world_update_mode=world_engine_mode,
        agent_cognition=cognition_map,
        log_mode=log_mode,
    )

    # Override memory strategy if requested
    if memory_strategy == "semantic":
        from miniverse.memory import SemanticMemoryStrategy
        orchestrator.memory = SemanticMemoryStrategy(orchestrator.persistence)
        await orchestrator.memory.initialize()
    elif memory_strategy == "simple":
        from miniverse.memory import SimpleMemoryStream
        orchestrator.memory = SimpleMemoryStream(orchestrator.persistence)

    if use_llm and output_format == "text" and not quiet:
        _print_llm_setup(
            scenario_ref=scenario,
            scenario_file=scenario_file,
            scenario_data=scenario_data,
            ticks=ticks,
            seed=seed,
            world_engine_mode=world_engine_mode,
            rules=rules,
            orchestrator=orchestrator,
            agent_prompts=agent_prompts,
            provider=provider,
            model=model,
        )
        typer.echo("")
        typer.echo("=" * 80)
        typer.echo("Simulation")
        typer.echo("=" * 80)

    if log_mode == "none":
        with contextlib.redirect_stdout(io.StringIO()):
            result = await orchestrator.run(num_ticks=ticks)
    elif use_llm and log_mode in {"concise", "verbose"}:
        result = await _run_with_llm_heartbeat(orchestrator, ticks)
    else:
        result = await orchestrator.run(num_ticks=ticks)

    completed_ticks = int(result["final_state"].tick)
    if output_format == "json":
        output_data = {
            "run_id": str(result["run_id"]),
            "scenario": scenario,
            "ticks_completed": completed_ticks,
            "seed": seed,
            "llm_enabled": use_llm,
            "final_state": result["final_state"].model_dump(mode="json"),
        }
        typer.echo(json.dumps(output_data, indent=2, default=str))
        return

    final_state = result["final_state"]
    typer.echo("")
    typer.echo(f"Run ID: {result['run_id']}")
    typer.echo(f"Completed {completed_ticks} ticks")

    if final_state.resources and final_state.resources.metrics:
        typer.echo("\nFinal resources:")
        for key, stat in final_state.resources.metrics.items():
            label = stat.label or key
            typer.echo(f"  {label}: {stat.value} {stat.unit or ''}")


def main() -> None:
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
