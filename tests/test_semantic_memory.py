"""Tests for SemanticMemoryStrategy (embeddings + BM25 + decay)."""

import asyncio
from uuid import uuid4
import pytest

from miniverse.memory import SemanticMemoryStrategy
from miniverse.persistence import InMemoryPersistence


@pytest.mark.asyncio
async def test_semantic_retrieval_finds_related_memories():
    """Semantic search should find memories related by meaning, not just keywords."""
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SemanticMemoryStrategy(persistence)
    await memory.initialize()

    run_id = uuid4()
    agent_id = "sera"

    await memory.add_memory(
        run_id, agent_id, tick=1, memory_type="observation",
        content="Dex reported a promising contact at the market who asked about upgrades",
        importance=7,
    )
    await memory.add_memory(
        run_id, agent_id, tick=2, memory_type="action",
        content="I organized the clinic supplies and checked equipment",
        importance=3,
    )
    await memory.add_memory(
        run_id, agent_id, tick=3, memory_type="observation",
        content="Noor mentioned she feels stuck and wants something more meaningful",
        importance=6,
    )
    await memory.add_memory(
        run_id, agent_id, tick=4, memory_type="action",
        content="I repaired a malfunctioning diagnostic unit",
        importance=4,
    )

    # Query about recruitment — should find the Dex report and Noor's vulnerability
    # even though "recruitment" doesn't appear in any memory
    results = await memory.get_relevant_memories(
        run_id, agent_id, query="potential recruit showing interest in joining", limit=2
    )

    assert len(results) == 2
    # The Dex report about a promising contact should rank high
    assert any("promising contact" in r or "upgrades" in r for r in results)

    await memory.close()
    await persistence.close()


@pytest.mark.asyncio
async def test_importance_decay_over_ticks():
    """Old memories should score lower than recent ones of equal importance."""
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SemanticMemoryStrategy(persistence, decay_rate=0.9)
    await memory.initialize()

    run_id = uuid4()
    agent_id = "dex"

    # Old memory
    await memory.add_memory(
        run_id, agent_id, tick=1, memory_type="observation",
        content="Noticed suspicious activity near the alley",
        importance=7,
    )
    # Recent memory with same content and importance
    await memory.add_memory(
        run_id, agent_id, tick=10, memory_type="observation",
        content="Noticed suspicious activity near the market",
        importance=7,
    )

    results = await memory.get_relevant_memories(
        run_id, agent_id, query="suspicious activity", limit=2
    )

    # Recent memory should come first due to recency decay
    assert len(results) == 2
    assert "market" in results[0]  # tick=10, more recent
    assert "alley" in results[1]   # tick=1, older

    await memory.close()
    await persistence.close()


@pytest.mark.asyncio
async def test_bm25_still_contributes():
    """Exact keyword matches should still boost retrieval alongside embeddings."""
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SemanticMemoryStrategy(persistence)
    await memory.initialize()

    run_id = uuid4()
    agent_id = "vasek"

    await memory.add_memory(
        run_id, agent_id, tick=1, memory_type="observation",
        content="The checksums on Channel B need verification before the next meeting",
        importance=5,
    )
    await memory.add_memory(
        run_id, agent_id, tick=2, memory_type="observation",
        content="Weather in the district has been mild",
        importance=3,
    )

    results = await memory.get_relevant_memories(
        run_id, agent_id, query="checksums Channel B", limit=1
    )

    assert len(results) == 1
    assert "checksums" in results[0]

    await memory.close()
    await persistence.close()


@pytest.mark.asyncio
async def test_empty_query_falls_back_to_recency():
    """Empty query should return recent important memories."""
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SemanticMemoryStrategy(persistence)
    await memory.initialize()

    run_id = uuid4()
    agent_id = "rho"

    await memory.add_memory(
        run_id, agent_id, tick=1, memory_type="observation",
        content="Old mundane observation", importance=2,
    )
    await memory.add_memory(
        run_id, agent_id, tick=5, memory_type="observation",
        content="Recent important event", importance=8,
    )

    results = await memory.get_relevant_memories(run_id, agent_id, query="", limit=2)
    assert len(results) == 2
    # Recent + important should come first
    assert "Recent important" in results[0]

    await memory.close()
    await persistence.close()
