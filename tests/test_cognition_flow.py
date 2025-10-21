"""Tests covering the new cognition pipeline (planner/executor/reflection)."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from miniverse import (
    AgentAction,
    AgentCognition,
    AgentProfile,
    AgentStatus,
    EnvironmentState,
    Orchestrator,
    Plan,
    PlanStep,
    ReflectionResult,
    ResourceState,
    Scratchpad,
    SimulationRules,
    Stat,
    WorldState,
)
from miniverse.cognition.cadence import (
    PLANNER_LAST_TICK_KEY,
    REFLECTION_LAST_TICK_KEY,
    CognitionCadence,
    PlannerCadence,
    ReflectionCadence,
    TickInterval,
)
from miniverse.schemas import AgentMemory


class DummyRules(SimulationRules):
    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)
        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True


class RecordingMemory:
    """Memory strategy that records writes for assertions."""

    def __init__(self):
        self.records = []

    async def initialize(self):  # pragma: no cover - trivial
        pass

    async def close(self):  # pragma: no cover - trivial
        pass

    async def add_memory(
        self,
        run_id,
        agent_id,
        tick,
        memory_type,
        content,
        importance=5,
        **kwargs,
    ):
        mem = AgentMemory(
            id=uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=kwargs.get("tags", []),
            metadata=kwargs.get("metadata", {}),
            embedding_key=kwargs.get("embedding_key"),
            branch_id=kwargs.get("branch_id"),
            created_at=datetime.now(timezone.utc),
        )
        self.records.append(mem)
        return mem

    async def get_recent_memories(self, run_id, agent_id, limit=10):
        return [mem.content for mem in self.records if mem.agent_id == agent_id][
            -limit:
        ]

    async def get_relevant_memories(
        self, run_id, agent_id, query, limit=5
    ):  # pragma: no cover
        return []

    async def clear_agent_memories(self, run_id, agent_id):  # pragma: no cover
        self.records = [m for m in self.records if m.agent_id != agent_id]


class PlannerStub:
    def __init__(self):
        self.calls = 0

    async def generate_plan(self, agent_id, scratchpad, *, world_context, context):
        self.calls += 1
        return Plan(steps=[PlanStep(description=f"step-{self.calls}", metadata={})])


class ExecutorStub:
    def __init__(self):
        self.calls = []

    async def choose_action(
        self, agent_id, perception, scratchpad, *, plan, plan_step, context
    ):
        self.calls.append(
            (
                plan_step.description if plan_step else None,
                context.plan_state["current_index"],
            )
        )
        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type="work",
            target=None,
            parameters={},
            reasoning="Executing plan step",
            communication=None,
        )


class ReflectionStub:
    def __init__(self):
        self.calls = 0

    async def maybe_reflect(
        self,
        agent_id,
        scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context=None,
    ):
        self.calls += 1
        return [ReflectionResult(content="reflection note", importance=6)]


@pytest.mark.asyncio
async def test_cognition_pipeline(monkeypatch):
    world_state = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={}),
        agents=[
            AgentStatus(
                agent_id="alpha",
                display_name="Agent Alpha",
                role="operator",
                location="ops",
                activity=None,
                attributes={},
            )
        ],
    )

    profile = AgentProfile(
        agent_id="alpha",
        name="Agent Alpha",
        age=30,
        background="Test agent",
        role="operator",
        personality="steady",
        skills={},
        goals=["Execute plan"],
        relationships={},
    )

    cognition = AgentCognition(
        planner=PlannerStub(),
        executor=ExecutorStub(),
        reflection=ReflectionStub(),
        scratchpad=Scratchpad(),
    )

    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="Test world",
        agent_prompts={"alpha": "You are methodical."},
        llm_provider="openai",
        llm_model="gpt-5-nano",
        simulation_rules=DummyRules(),
        memory=RecordingMemory(),
        agent_cognition={"alpha": cognition},
    )

    # Bypass world update LLM
    async def fake_world_update(current_state, actions, tick, *args, **kwargs):
        return current_state.model_copy(update={"tick": tick})

    monkeypatch.setattr(
        "miniverse.orchestrator.process_world_update", fake_world_update
    )

    await orchestrator.run(num_ticks=1)

    assert cognition.planner.calls == 1
    assert cognition.executor.calls[0][0] == "step-1"
    assert cognition.reflection.calls == 1
    assert any(mem.memory_type == "reflection" for mem in orchestrator.memory.records)


@pytest.mark.asyncio
async def test_cognition_cadence_respects_intervals(monkeypatch):
    world_state = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={}),
        agents=[
            AgentStatus(
                agent_id="alpha",
                display_name="Agent Alpha",
                role="operator",
                location="ops",
                activity=None,
                attributes={},
            )
        ],
    )

    profile = AgentProfile(
        agent_id="alpha",
        name="Agent Alpha",
        age=30,
        background="Test agent",
        role="operator",
        personality="steady",
        skills={},
        goals=["Execute plan"],
        relationships={},
    )

    planner = PlannerStub()
    executor = ExecutorStub()
    reflection = ReflectionStub()

    cadence = CognitionCadence(
        planner=PlannerCadence(interval=TickInterval(every=2, offset=1)),
        reflection=ReflectionCadence(
            interval=TickInterval(every=3, offset=1),
            require_new_memories=True,
        ),
    )

    cognition = AgentCognition(
        planner=planner,
        executor=executor,
        reflection=reflection,
        scratchpad=Scratchpad(),
        cadence=cadence,
    )

    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="Test world",
        agent_prompts={"alpha": "You are methodical."},
        llm_provider="openai",
        llm_model="gpt-5-nano",
        simulation_rules=DummyRules(),
        memory=RecordingMemory(),
        agent_cognition={"alpha": cognition},
    )

    async def fake_world_update(current_state, actions, tick, *args, **kwargs):
        return current_state.model_copy(update={"tick": tick})

    monkeypatch.setattr(
        "miniverse.orchestrator.process_world_update", fake_world_update
    )

    await orchestrator.run(num_ticks=4)

    assert planner.calls == 2  # ticks 1 and 3
    assert reflection.calls == 2  # ticks 1 and 4 (require_new_memories=True satisfied)
    assert cognition.scratchpad.state.get(PLANNER_LAST_TICK_KEY) == 3
    assert cognition.scratchpad.state.get(REFLECTION_LAST_TICK_KEY) == 4
