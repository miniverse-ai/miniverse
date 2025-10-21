"""Workshop simulation demonstrating deterministic and LLM cognition stacks.

By default the example runs in deterministic mode (no LLM calls):

    uv run python examples/workshop/run.py --ticks 6

To enable LLM-driven planning/execution/reflection (requires provider, model,
API key), pass `--llm`:

    uv run python examples/workshop/run.py --llm --ticks 8

Environment variables expected when `--llm` is used:
- `LLM_PROVIDER` (e.g., `openai`)
- `LLM_MODEL` (e.g., `gpt-5-nano`)
- Provider-specific API key (e.g., `OPENAI_API_KEY`)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import random
from dataclasses import asdict
from pathlib import Path
from typing import Dict

from miniverse import (
    AgentAction,
    AgentCognition,
    AgentProfile,
    EnvironmentGraph,
    EnvironmentGraphState,
    GraphOccupancy,
    LocationNode,
    Orchestrator,
    Plan,
    PlanStep,
    ReflectionResult,
    SimulationRules,
    WorldState,
)
from miniverse.cognition import (
    LLMPlanner,
    LLMReflectionEngine,
    PromptLibrary,
    PromptTemplate,
    Scratchpad,
)
from miniverse.cognition.context import PromptContext
from miniverse.cognition.executor import Executor
from miniverse.cognition.llm import LLMExecutor
from miniverse.cognition.planner import Planner
from miniverse.cognition.reflection import ReflectionEngine
from miniverse.config import Config
from miniverse.scenario import ScenarioLoader


def build_environment_graph(
    state: EnvironmentGraphState | None,
) -> EnvironmentGraph | None:
    if state is None:
        return None
    nodes = {
        node_id: LocationNode(
            name=node_state.name,
            capacity=node_state.capacity,
            metadata=node_state.metadata,
        )
        for node_id, node_state in state.nodes.items()
    }
    return EnvironmentGraph(nodes=nodes, adjacency=dict(state.adjacency))


class WorkshopRules(SimulationRules):
    """Deterministic updates with optional stochastic arrivals for the workshop."""

    def __init__(
        self,
        occupancy: GraphOccupancy | None = None,
        *,
        rng: random.Random | None = None,
        task_arrival_chance: float = 0.0,
        max_new_tasks: int = 0,
    ) -> None:
        self.occupancy = occupancy
        self.rng = rng
        self.task_arrival_chance = max(0.0, task_arrival_chance)
        self.max_new_tasks = max(0, max_new_tasks)

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)
        backlog = updated.resources.get_metric(
            "task_backlog", default=0, label="Pending Tasks"
        )
        power = updated.resources.get_metric(
            "power_kwh", default=120.0, unit="kWh", label="Battery Reserve"
        )

        active_agents = 0
        for agent in updated.agents:
            energy = agent.get_attribute("energy", default=80, unit="%")
            stress = agent.get_attribute("stress", default=25, unit="%")
            if (agent.activity or "").lower() in {"work", "analyze", "repair"}:
                active_agents += 1
                energy.value = max(0.0, float(energy.value) - 5)
                stress.value = min(100.0, float(stress.value) + 2)
            else:
                energy.value = min(100.0, float(energy.value) + 3)
                stress.value = max(0.0, float(stress.value) - 1)

        incoming_tasks = 0
        if (
            self.rng is not None
            and self.task_arrival_chance > 0.0
            and self.max_new_tasks > 0
            and self.rng.random() < self.task_arrival_chance
        ):
            incoming_tasks = self.rng.randint(1, self.max_new_tasks)

        backlog.value = max(0, int(backlog.value) - active_agents + incoming_tasks)

        drain_multiplier = 1.5
        if self.rng is not None:
            drain_multiplier += self.rng.uniform(-0.2, 0.2)
        power.value = max(0.0, float(power.value) - active_agents * drain_multiplier)

        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        if action.action_type == "move" and self.occupancy:
            target = action.target
            if not target:
                return False
            return self.occupancy.can_enter(target, action.agent_id)
        return True


class DeterministicPlanner(Planner):
    """Role-based deterministic planner (used when LLM disabled)."""

    def __init__(self, role_plans: Dict[str, list[str]]):
        self.role_plans = role_plans

    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context: WorldState,
        context: PromptContext,
    ) -> Plan:
        profile: AgentProfile = context.agent_profile
        steps = [
            PlanStep(description=desc, metadata={"role": profile.role})
            for desc in self.role_plans.get(profile.role, ["coordinate"])
        ]
        return Plan(steps=steps)


class DeterministicExecutor(Executor):
    """Executor that maps plan steps to predefined actions."""

    ROLE_ACTIONS = {
        "lead": {"coordinate": ("work", "ops"), "check-in": ("communicate", "ops")},
        "technician": {
            "repair": ("work", "workbench"),
            "restock": ("move", "inventory"),
        },
        "analyst": {"analyze": ("analyze", "ops"), "report": ("communicate", "ops")},
    }

    async def choose_action(
        self,
        agent_id: str,
        perception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> AgentAction:
        profile: AgentProfile = context.agent_profile
        role_map = self.ROLE_ACTIONS.get(profile.role, {})
        if plan_step is None:
            action_type, target = ("rest", perception.location)
        else:
            action_type, target = role_map.get(
                plan_step.description, ("work", perception.location)
            )
        reasoning = (
            f"Executing plan step '{plan_step.description}'"
            if plan_step
            else "No plan available, defaulting to rest"
        )
        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type=action_type,
            target=target,
            parameters={},
            reasoning=reasoning,
            communication=None,
        )


class DeterministicReflection(ReflectionEngine):
    """Reflection engine generating lightweight diary notes."""

    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context: PromptContext | None = None,
    ) -> list[ReflectionResult]:
        if not trigger_context or trigger_context.get("tick") % 3 != 0:
            return []
        latest = next(iter(recent_memories), None)
        content = (
            "Reviewed progress and adjusted plan."
            if latest is None
            else f"Noted: {latest.content}"
        )
        return [ReflectionResult(content=content, importance=6)]


class DebugPlanner(Planner):
    """Planner wrapper that logs inputs/outputs when debugging."""

    def __init__(
        self,
        inner: Planner,
        agent_id: str,
        provider: str | None,
        model: str | None,
    ) -> None:
        self.inner = inner
        self.agent_id = agent_id
        self.provider = provider
        self.model = model

    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context: WorldState,
        context: PromptContext,
    ) -> Plan:
        inner_name = self.inner.__class__.__name__
        print(
            f"    [Debug][{self.agent_id}] Planner -> {inner_name}"
            + (
                f" (provider={self.provider}, model={self.model})"
                if self.provider and self.model
                else ""
            )
        )
        plan = await self.inner.generate_plan(
            agent_id,
            scratchpad,
            world_context=world_context,
            context=context,
        )
        print("      Plan output:\n" + json.dumps(asdict(plan), indent=2, default=str))
        return plan


class DebugExecutor(Executor):
    """Executor wrapper that logs chosen actions."""

    def __init__(self, inner: Executor, agent_id: str) -> None:
        self.inner = inner
        self.agent_id = agent_id

    async def choose_action(
        self,
        agent_id: str,
        perception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> AgentAction:
        inner_name = self.inner.__class__.__name__
        print(f"    [Debug][{self.agent_id}] Executor -> {inner_name}")
        if plan_step is not None:
            print(
                "      Plan step:\n"
                + json.dumps(asdict(plan_step), indent=2, default=str)
            )
        action = await self.inner.choose_action(
            agent_id,
            perception,
            scratchpad,
            plan=plan,
            plan_step=plan_step,
            context=context,
        )
        print(
            "      Action JSON:\n"
            + json.dumps(action.model_dump(), indent=2, default=str)
        )
        return action


class DebugReflection(ReflectionEngine):
    """Reflection wrapper that logs reflection outputs."""

    def __init__(
        self,
        inner: ReflectionEngine,
        agent_id: str,
        provider: str | None,
        model: str | None,
    ) -> None:
        self.inner = inner
        self.agent_id = agent_id
        self.provider = provider
        self.model = model

    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context: PromptContext | None = None,
    ) -> list[ReflectionResult]:
        inner_name = self.inner.__class__.__name__
        print(
            f"    [Debug][{self.agent_id}] Reflection -> {inner_name}"
            + (
                f" (provider={self.provider}, model={self.model})"
                if self.provider and self.model
                else ""
            )
        )
        reflections = await self.inner.maybe_reflect(
            agent_id,
            scratchpad,
            recent_memories,
            trigger_context=trigger_context,
            context=context,
        )
        if reflections:
            payload = [asdict(reflection) for reflection in reflections]
            print("      Reflections:\n" + json.dumps(payload, indent=2, default=str))
        else:
            print("      Reflections: []")
        return reflections


class TickAnalyzer:
    """Simple per-tick summary printer for quick diagnostics."""

    def report(
        self,
        tick: int,
        previous_state: WorldState,
        new_state: WorldState,
        actions: list[AgentAction],
    ) -> None:
        prev_backlog_stat = previous_state.resources.metrics.get("task_backlog")
        new_backlog_stat = new_state.resources.metrics.get("task_backlog")
        if prev_backlog_stat and new_backlog_stat:
            prev_val = float(prev_backlog_stat.value)
            new_val = float(new_backlog_stat.value)
            delta = new_val - prev_val
            print(
                f"  [Analysis] Backlog {prev_val:.1f} → {new_val:.1f} (Δ {delta:+.1f})"
            )

        energies = []
        stresses = []
        for agent in new_state.agents:
            energy_stat = agent.attributes.get("energy")
            if energy_stat is not None:
                energies.append(float(energy_stat.value))
            stress_stat = agent.attributes.get("stress")
            if stress_stat is not None:
                stresses.append(float(stress_stat.value))

        if energies:
            avg_energy = sum(energies) / len(energies)
            print(f"  [Analysis] Avg energy: {avg_energy:.1f}%")
        if stresses:
            avg_stress = sum(stresses) / len(stresses)
            print(f"  [Analysis] Avg stress: {avg_stress:.1f}%")

        if actions:
            counts: Dict[str, int] = {}
            for action in actions:
                counts[action.action_type] = counts.get(action.action_type, 0) + 1
            summary = ", ".join(f"{k}={v}" for k, v in counts.items())
            print(f"  [Analysis] Actions this tick: {summary}")


def build_prompt_library() -> PromptLibrary:
    library = PromptLibrary()
    library.register(
        PromptTemplate(
            name="plan_workshop",
            system=(
                "You plan the maintenance crew's upcoming tasks. Use the context to produce a JSON plan "
                "following the schema in the example."
            ),
            user=(
                "Context summary:\n{{context_summary}}\n\n"
                "Environment JSON:\n{{context_json}}\n\n"
                "Example output:\n"
                "{\n"
                '  "steps": [\n'
                '    {"description": "coordinate stand-up in operations", "metadata": {"duration_minutes": 30}},\n'
                '    {"description": "inspect recycler filters", "metadata": {"location": "workbench"}}\n'
                "  ],\n"
                '  "metadata": {"planning_horizon": "next 3 hours"}\n'
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="execute_workshop",
            system=(
                "Decide the next action that follows the plan and respects room capacities. Return AgentAction JSON."
            ),
            user=(
                "Perception:\n{{perception_json}}\n\n"
                "Plan state:\n{{plan_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Summary:\n{{context_summary}}\n\n"
                "Example output:\n"
                "{\n"
                '  "agent_id": "tech",\n'
                '  "tick": 7,\n'
                '  "action_type": "move",\n'
                '  "target": "inventory",\n'
                '  "parameters": {"reason": "pick up spare filters"},\n'
                '  "reasoning": "Plan requires restocking filters before repairs",\n'
                '  "communication": null\n'
                "}\n\n"
                "Return JSON only."
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="reflect_workshop",
            system=(
                "Write a brief diary entry summarizing key takeaways. Return JSON with a 'reflections' list."
            ),
            user=(
                "Context summary:\n{{context_summary}}\n\n"
                "Full JSON:\n{{context_json}}\n\n"
                "Example output:\n"
                "{\n"
                '  "reflections": [\n'
                '    {"content": "Coordinated early with Lin, backlog dropped. Need to request more filters.", "importance": 6}\n'
                "  ]\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    return library


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Workshop simulation")
    parser.add_argument(
        "--llm", action="store_true", help="Use LLM-based cognition modules"
    )
    parser.add_argument(
        "--ticks", type=int, default=5, help="Number of ticks to simulate"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose cognition logging (planner/executor/reflection outputs)",
    )
    parser.add_argument(
        "--analysis",
        action="store_true",
        help="Print per-tick analysis summary (backlog delta, averages)",
    )
    return parser.parse_args()


async def run_simulation(
    ticks: int,
    *,
    use_llm: bool = False,
    seed: int | None = None,
    task_arrival_chance: float = 0.35,
    max_new_tasks: int = 2,
    verbose: bool = True,
    debug: bool = False,
    analysis: bool = False,
) -> Dict[str, object]:
    loader = ScenarioLoader(scenarios_dir=Path(__file__).parent)
    world_state, profiles = loader.load("scenario")
    profiles_map = {profile.agent_id: profile for profile in profiles}

    env_graph = build_environment_graph(world_state.environment_graph)
    occupancy = GraphOccupancy(env_graph) if env_graph else None
    if occupancy:
        for agent_status in world_state.agents:
            if agent_status.location:
                occupancy.enter(agent_status.location, agent_status.agent_id)

    role_plans = {
        "lead": ["coordinate", "check-in"],
        "technician": ["repair", "restock"],
        "analyst": ["analyze", "report"],
    }

    rng = random.Random(seed) if seed is not None else None

    if use_llm:
        Config.validate()

    if debug:
        mode = "LLM" if use_llm else "deterministic"
        print(
            f"[debug] Mode={mode}, provider={Config.LLM_PROVIDER}, model={Config.LLM_MODEL}"
        )

    prompt_library = build_prompt_library() if use_llm else None

    # Define available actions for LLM executor
    available_actions = [
        {
            "action_type": "work",
            "description": "Work on current task",
            "schema": {
                "action_type": "work",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "agent_id": "lead",
                    "tick": 1,
                    "action_type": "work",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Continue coordinating team",
                    "communication": None,
                }
            ],
        },
        {
            "action_type": "communicate",
            "description": "Send message to another agent",
            "schema": {
                "action_type": "communicate",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": {"to": "<agent_id>", "message": "<string>"},
            },
            "examples": [
                {
                    "agent_id": "lead",
                    "tick": 2,
                    "action_type": "communicate",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Coordinate with team",
                    "communication": {
                        "to": "tech",
                        "message": "Can you check station B?",
                    },
                }
            ],
        },
        {
            "action_type": "move",
            "description": "Move to different location",
            "schema": {
                "action_type": "move",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "agent_id": "tech",
                    "tick": 3,
                    "action_type": "move",
                    "target": "workbench",
                    "parameters": {},
                    "reasoning": "Need tools for repair",
                    "communication": None,
                }
            ],
        },
        {
            "action_type": "rest",
            "description": "Rest to recover energy",
            "schema": {
                "action_type": "rest",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "agent_id": "lead",
                    "tick": 5,
                    "action_type": "rest",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Low energy, need break",
                    "communication": None,
                }
            ],
        },
        {
            "action_type": "analyze",
            "description": "Analyze metrics or situation",
            "schema": {
                "action_type": "analyze",
                "target": "<location>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "agent_id": "analyst",
                    "tick": 4,
                    "action_type": "analyze",
                    "target": "ops",
                    "parameters": {},
                    "reasoning": "Review backlog metrics",
                    "communication": None,
                }
            ],
        },
        {
            "action_type": "monitor",
            "description": "Monitor systems or environment",
            "schema": {
                "action_type": "monitor",
                "target": "<subject>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "agent_id": "analyst",
                    "tick": 6,
                    "action_type": "monitor",
                    "target": "battery_reserve",
                    "parameters": {},
                    "reasoning": "Check power levels",
                    "communication": None,
                }
            ],
        },
    ]

    cognition_map: Dict[str, AgentCognition] = {}
    for agent_id, profile in profiles_map.items():
        if use_llm and prompt_library is not None:
            scratchpad = Scratchpad(
                state={"execute_prompt_template": "execute_workshop"}
            )
            cognition_map[agent_id] = AgentCognition(
                planner=LLMPlanner(
                    template_name="plan_workshop",
                    prompt_library=prompt_library,
                ),
                executor=LLMExecutor(
                    template_name="default", available_actions=available_actions
                ),
                reflection=LLMReflectionEngine(
                    template_name="reflect_workshop",
                    prompt_library=prompt_library,
                ),
                scratchpad=scratchpad,
                prompt_library=prompt_library,
            )
        else:
            cognition_map[agent_id] = AgentCognition(
                planner=DeterministicPlanner(role_plans),
                executor=DeterministicExecutor(),
                reflection=DeterministicReflection(),
                scratchpad=Scratchpad(),
            )

    provider = Config.LLM_PROVIDER if use_llm else None
    model = Config.LLM_MODEL if use_llm else None

    if debug:
        for agent_id, cognition in cognition_map.items():
            cognition_map[agent_id] = AgentCognition(
                planner=DebugPlanner(cognition.planner, agent_id, provider, model),
                executor=DebugExecutor(cognition.executor, agent_id),
                reflection=DebugReflection(
                    cognition.reflection, agent_id, provider, model
                ),
                scratchpad=cognition.scratchpad,
                prompt_library=cognition.prompt_library,
            )

    tick_listeners = []
    if analysis or debug:
        tick_listeners.append(TickAnalyzer().report)

    orchestrator = Orchestrator(
        world_state=world_state,
        agents=profiles_map,
        world_prompt="You oversee workshop state transitions.",
        agent_prompts={
            agent_id: (
                f"You are {profile.name}, the {profile.role}. Coordinate with your teammates, "
                "keep the backlog under control, and follow your plan steps."
            )
            for agent_id, profile in profiles_map.items()
        },
        llm_provider=provider,
        llm_model=model,
        simulation_rules=WorkshopRules(
            occupancy,
            rng=rng,
            task_arrival_chance=task_arrival_chance if rng else 0.0,
            max_new_tasks=max_new_tasks if rng else 0,
        ),
        agent_cognition=cognition_map,
        tick_listeners=tick_listeners,
    )

    context_manager = contextlib.nullcontext()
    if not verbose:
        context_manager = contextlib.redirect_stdout(io.StringIO())

    with context_manager:
        result = await orchestrator.run(num_ticks=ticks)

    return result


async def main(args: argparse.Namespace) -> None:
    analysis_flag = args.analysis or args.debug
    try:
        result = await run_simulation(
            args.ticks,
            use_llm=args.llm,
            seed=None,
            verbose=True,
            debug=args.debug,
            analysis=analysis_flag,
        )
    except ValueError as exc:
        print(f"[warning] {exc}. Falling back to deterministic cognition.")
        result = await run_simulation(
            args.ticks,
            use_llm=False,
            seed=None,
            verbose=True,
            debug=args.debug,
            analysis=analysis_flag,
        )

    final_state: WorldState = result["final_state"]
    print("Final backlog:", final_state.resources.get_metric("task_backlog").value)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
