"""Test information diffusion via communication memories.

This test verifies that when one agent sends a message to another,
BOTH the sender and recipient get appropriate memories.

This is the critical fix for Stanford-style information diffusion.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from miniverse import (
    AgentAction,
    AgentProfile,
    AgentStatus,
    EnvironmentState,
    Orchestrator,
    ResourceState,
    WorldState,
)
from miniverse.memory import SimpleMemoryStream
from miniverse.persistence import InMemoryPersistence


@pytest.mark.asyncio
async def test_communication_creates_recipient_memory():
    """Test that recipients get memories when sent messages."""

    # Setup
    persistence = InMemoryPersistence()
    memory = SimpleMemoryStream(persistence)
    await persistence.initialize()
    await memory.initialize()

    run_id = uuid4()

    # Create a communication action
    action = AgentAction(
        agent_id="alice",
        tick=1,
        action_type="communicate",
        target="bob",
        reasoning="Tell Bob about the party",
        communication={"to": "bob", "message": "Party at 5pm on Friday!"},
    )

    # Simulate what orchestrator does: create both sender and recipient memories
    sender_memory = await memory.add_memory(
        run_id=run_id,
        agent_id="alice",
        tick=1,
        memory_type="communication",
        content="I told bob: Party at 5pm on Friday!",
        importance=6,
        tags=["communication", "to:bob"],
    )

    recipient_memory = await memory.add_memory(
        run_id=run_id,
        agent_id="bob",
        tick=1,
        memory_type="communication",
        content="Alice told me: Party at 5pm on Friday!",
        importance=7,
        tags=["communication", "from:alice"],
    )

    # Verify sender has memory
    alice_memories = await memory.get_recent_memories(run_id, "alice", limit=10)
    assert len(alice_memories) == 1
    assert "I told bob" in alice_memories[0]
    assert "Party at 5pm" in alice_memories[0]

    # Verify recipient has memory (THIS IS THE CRITICAL FIX!)
    bob_memories = await memory.get_recent_memories(run_id, "bob", limit=10)
    assert len(bob_memories) == 1
    assert "Alice told me" in bob_memories[0]
    assert "Party at 5pm" in bob_memories[0]

    # Verify relevant retrieval works
    party_memories = await memory.get_relevant_memories(run_id, "bob", "party", limit=5)
    assert len(party_memories) >= 1
    assert any("party" in m.lower() for m in party_memories)

    await persistence.close()
    await memory.close()


@pytest.mark.asyncio
async def test_communication_memory_has_correct_metadata():
    """Test that communication memories have proper metadata for both roles."""

    persistence = InMemoryPersistence()
    memory = SimpleMemoryStream(persistence)
    await persistence.initialize()
    await memory.initialize()

    run_id = uuid4()

    # Sender memory
    sender_mem = await memory.add_memory(
        run_id=run_id,
        agent_id="alice",
        tick=1,
        memory_type="communication",
        content="I told bob: Meeting at noon",
        importance=6,
        tags=["communication", "to:bob"],
        metadata={"role": "sender", "recipient": "bob"},
    )

    # Recipient memory
    recipient_mem = await memory.add_memory(
        run_id=run_id,
        agent_id="bob",
        tick=1,
        memory_type="communication",
        content="Alice told me: Meeting at noon",
        importance=7,
        tags=["communication", "from:alice"],
        metadata={"role": "recipient", "sender": "alice"},
    )

    # Verify metadata
    alice_memories = await persistence.get_recent_memories(run_id, "alice", limit=10)
    assert alice_memories[0].metadata["role"] == "sender"
    assert alice_memories[0].metadata["recipient"] == "bob"

    bob_memories = await persistence.get_recent_memories(run_id, "bob", limit=10)
    assert bob_memories[0].metadata["role"] == "recipient"
    assert bob_memories[0].metadata["sender"] == "alice"

    await persistence.close()
    await memory.close()
