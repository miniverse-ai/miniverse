"""Tests for in-memory persistence and memory stream utilities."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from miniverse.memory import SimpleMemoryStream
from miniverse.persistence import InMemoryPersistence
from miniverse.schemas import (
    AgentAction,
    AgentMemory,
    AgentStatus,
    EnvironmentState,
    ResourceState,
    SimulationRun,
    Stat,
    WorldState,
)


def make_world_state() -> WorldState:
    return WorldState(
        tick=0,
        timestamp=datetime(2160, 1, 1, 12, 0, 0),
        environment=EnvironmentState(metrics={"temperature": Stat(value=20.0)}),
        resources=ResourceState(metrics={"power": Stat(value=100.0)}),
        agents=[
            AgentStatus(
                agent_id="alpha",
                location="operations",
                attributes={
                    "energy": Stat(value=80, unit="%"),
                    "stress": Stat(value=20, unit="%"),
                },
            )
        ],
        metadata={},
    )


@pytest.mark.asyncio
async def test_in_memory_persistence_round_trip():
    persistence = InMemoryPersistence()
    await persistence.initialize()

    run_id = uuid4()
    state = make_world_state()

    await persistence.save_state(run_id, 1, state)
    retrieved_state = await persistence.get_state(run_id, 1)
    assert retrieved_state == state

    action = AgentAction(
        agent_id="alpha",
        tick=1,
        action_type="inspect",
        target="station",
        parameters=None,
        reasoning="Check anomalies",
        communication=None,
    )

    await persistence.save_action(run_id, action)
    actions = await persistence.get_actions(run_id, 1)
    assert actions and actions[0].reasoning == "Check anomalies"

    memory = AgentMemory(
        id=uuid4(),
        run_id=run_id,
        agent_id="alpha",
        tick=1,
        memory_type="observation",
        content="Noted anomaly on line 2",
        importance=5,
        created_at=datetime.now(timezone.utc),
    )

    await persistence.save_memory(run_id, memory)
    stored_memories = await persistence.get_recent_memories(run_id, "alpha", limit=5)
    assert stored_memories and stored_memories[0].content == "Noted anomaly on line 2"

    run_metadata = SimulationRun(
        id=run_id,
        start_time=datetime.now(timezone.utc),
        end_time=None,
        num_ticks=10,
        num_agents=1,
        status="running",
        config={"scenario": "test"},
        created_at=datetime.now(timezone.utc),
    )

    await persistence.save_run_metadata(run_metadata)
    await persistence.update_run_status(run_id, "completed", datetime.now(timezone.utc))
    assert persistence.runs[run_id].status == "completed"

    await persistence.close()


@pytest.mark.asyncio
async def test_simple_memory_stream_uses_persistence():
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SimpleMemoryStream(persistence)

    run_id = uuid4()
    await memory.add_memory(
        run_id=run_id,
        agent_id="alpha",
        tick=1,
        memory_type="observation",
        content="Observed queue forming",
        importance=6,
    )

    recent = await memory.get_recent_memories(run_id, "alpha", limit=5)
    assert recent == ["Observed queue forming"]

    await memory.close()
