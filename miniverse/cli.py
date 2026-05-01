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
    hours: float = typer.Option(
        8.0,
        "--hours",
        help="Simulated hours to run (async mode only)",
    ),
    max_steps: int = typer.Option(
        50,
        "--max-steps",
        help="Max decision steps per agent (async mode only)",
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
) -> None:
    """Run a simulation with the specified scenario."""
    if async_mode:
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

    metadata_prompts = (
        (scenario_data.get("metadata") or {}).get("agent_prompts") or {}
    )
    if isinstance(metadata_prompts, dict):
        for agent_id, prompt in metadata_prompts.items():
            if (
                agent_id in prompts
                and isinstance(prompt, str)
                and prompt.strip()
            ):
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
    hours: float,
    use_llm: bool,
    seed: Optional[int],
    verbose: bool,
    memory_strategy: str = "bm25",
    max_steps: int = 50,
    max_turns: int = 12,
    use_context_window: bool = False,
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
    agent_prompts = _build_agent_prompts(
        profiles_map=profiles_map,
        scenario_data=scenario_data,
    )

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

    # Override memory strategy if requested
    if memory_strategy == "semantic":
        from miniverse.memory import SemanticMemoryStrategy
        orchestrator.memory = SemanticMemoryStrategy(orchestrator.persistence)
        await orchestrator.memory.initialize()
    elif memory_strategy == "simple":
        from miniverse.memory import SimpleMemoryStream
        orchestrator.memory = SimpleMemoryStream(orchestrator.persistence)

    result = await orchestrator.run(duration_hours=hours)

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
