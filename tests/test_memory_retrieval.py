"""Tests for keyword-based memory retrieval."""

import asyncio
from uuid import uuid4

import pytest

from miniverse.memory import ImportanceWeightedMemory, SimpleMemoryStream
from miniverse.persistence import InMemoryPersistence


@pytest.mark.asyncio
async def test_keyword_retrieval():
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SimpleMemoryStream(persistence)

    run_id = uuid4()
    agent_id = "alpha"

    await memory.add_memory(
        run_id,
        agent_id,
        tick=1,
        memory_type="observation",
        content="Agent inspected the oxygen recycler",
        importance=5,
        tags=["systems"],
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=2,
        memory_type="observation",
        content="Team discussed revenue targets",
        importance=5,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=3,
        memory_type="observation",
        content="Agent repaired the recycler filters",
        importance=7,
        tags=["systems"],
    )

    results = await memory.get_relevant_memories(
        run_id, agent_id, query="recycler", limit=2
    )
    assert len(results) == 2
    assert "recycler" in results[0]

    await persistence.close()


@pytest.mark.asyncio
async def test_importance_weighted_memory_balances_scores():
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = ImportanceWeightedMemory(
        persistence,
        recency_weight=0.2,
        importance_weight=0.8,
        window=10,
    )

    run_id = uuid4()
    agent_id = "alpha"

    # Older but critical memory should outrank fresher, low-importance noise.
    await memory.add_memory(
        run_id,
        agent_id,
        tick=1,
        memory_type="observation",
        content="Logged safety protocol deviation",
        importance=9,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=3,
        memory_type="observation",
        content="Filed routine shift report",
        importance=3,
    )

    results = await memory.get_relevant_memories(run_id, agent_id, query="", limit=2)
    assert results[0] == "Logged safety protocol deviation"

    await persistence.close()
