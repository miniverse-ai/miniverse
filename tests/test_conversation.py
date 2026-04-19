"""Tests for ConversationManager."""

import asyncio
from datetime import datetime

import pytest

from miniverse.conversation import ConversationManager, Message


def _now():
    return datetime(2026, 9, 17, 10, 0)


def test_start_public_conversation():
    cm = ConversationManager()
    cm.register_agent("fen")
    cm.register_agent("priya")
    cm.register_agent("tomas")

    conv = cm.start_conversation(
        initiator="fen",
        location="main_lab",
        message="Good morning Priya, can you walk me through the protocol?",
        timestamp=_now(),
        mode="public",
        target="priya",
        participants={"fen", "priya", "tomas"},
    )

    assert conv.is_active
    assert conv.mode == "public"
    assert conv.turn_count == 1
    assert {"fen", "priya", "tomas"} == conv.participants

    # Priya and Tomas have pending messages
    assert len(conv.get_pending_messages("priya")) == 1
    assert len(conv.get_pending_messages("tomas")) == 1
    assert len(conv.get_pending_messages("fen")) == 0  # sender doesn't get pending


def test_multi_turn_conversation():
    cm = ConversationManager()
    for a in ("fen", "priya"):
        cm.register_agent(a)

    conv = cm.start_conversation(
        initiator="fen",
        location="main_lab",
        message="How do you handle the serial passage transitions?",
        timestamp=_now(),
        mode="public",
        participants={"fen", "priya"},
    )

    # Priya responds
    cm.acknowledge("priya", conv.id)
    cm.send_message(
        sender="priya",
        conversation_id=conv.id,
        content="We transfer 100 microliters every 48 hours into fresh cell culture.",
        timestamp=_now(),
    )

    assert conv.turn_count == 2
    # Now Fen has a pending message
    assert len(conv.get_pending_messages("fen")) == 1
    assert len(conv.get_pending_messages("priya")) == 0

    # Fen follows up
    cm.acknowledge("fen", conv.id)
    cm.send_message(
        sender="fen",
        conversation_id=conv.id,
        content="What containment measures during the transfer?",
        timestamp=_now(),
    )

    assert conv.turn_count == 3


def test_private_message():
    cm = ConversationManager()
    cm.register_agent("fen")
    cm.register_agent("marcus")

    conv = cm.start_conversation(
        initiator="fen",
        location="anteroom",
        message="Marcus, can I see the incident logs?",
        timestamp=_now(),
        mode="private",
        target="marcus",
    )

    assert conv.mode == "private"
    assert conv.participants == {"fen", "marcus"}

    # Only marcus has pending
    pending = cm.get_pending("marcus")
    assert len(pending) == 1
    assert pending[0]["conversation_mode"] == "private"

    # Fen has no pending (she sent it)
    assert len(cm.get_pending("fen")) == 0


def test_group_conversation_overhear():
    """When Fen talks to Priya, Tomas (co-located) can also hear and respond."""
    cm = ConversationManager()
    for a in ("fen", "priya", "tomas"):
        cm.register_agent(a)

    conv = cm.start_conversation(
        initiator="fen",
        location="main_lab",
        message="What PPE is required for handling the enhanced samples?",
        timestamp=_now(),
        mode="public",
        target="priya",
        participants={"fen", "priya", "tomas"},
    )

    # Both Priya and Tomas have pending
    assert len(conv.get_pending_messages("priya")) == 1
    assert len(conv.get_pending_messages("tomas")) == 1

    # Tomas jumps in first (he's chatty)
    cm.acknowledge("tomas", conv.id)
    cm.send_message(
        sender="tomas",
        conversation_id=conv.id,
        content="Double gloves, N95, face shield, and a Tyvek suit. I can show you the gowning process!",
        timestamp=_now(),
    )

    # Fen has 1 pending (Tomas's response)
    assert len(conv.get_pending_messages("fen")) == 1
    # Priya has 2 pending — Fen's original + Tomas's response (she never acknowledged)
    assert len(conv.get_pending_messages("priya")) == 2


def test_leave_conversation():
    cm = ConversationManager()
    for a in ("fen", "priya", "tomas"):
        cm.register_agent(a)

    conv = cm.start_conversation(
        initiator="fen",
        location="main_lab",
        message="Morning everyone.",
        timestamp=_now(),
        mode="public",
        participants={"fen", "priya", "tomas"},
    )

    assert conv.is_active

    # Tomas leaves
    cm.leave_conversation("tomas", conv.id)
    assert "tomas" not in conv.participants
    assert conv.is_active  # Still active with fen + priya

    # Both leave
    cm.leave_conversation("fen", conv.id)
    cm.leave_conversation("priya", conv.id)
    assert not conv.is_active
    assert len(cm.history) == 1


def test_join_existing_location_conversation():
    """When a new agent talks at a location with an active conversation, they join it."""
    cm = ConversationManager()
    for a in ("fen", "priya", "marcus"):
        cm.register_agent(a)

    conv1 = cm.start_conversation(
        initiator="fen",
        location="main_lab",
        message="How's the experiment going?",
        timestamp=_now(),
        mode="public",
        participants={"fen", "priya"},
    )

    # Marcus arrives and talks at the same location
    conv2 = cm.start_conversation(
        initiator="marcus",
        location="main_lab",
        message="Just checking containment readings.",
        timestamp=_now(),
        mode="public",
        participants={"fen", "priya", "marcus"},
    )

    # Should join the existing conversation, not create a new one
    assert conv1.id == conv2.id
    assert "marcus" in conv1.participants
    assert conv1.turn_count == 2


def test_transcript():
    cm = ConversationManager()
    for a in ("fen", "priya"):
        cm.register_agent(a)

    conv = cm.start_conversation(
        initiator="fen",
        location="main_lab",
        message="How does the passage work?",
        timestamp=_now(),
        mode="public",
        target="priya",
        participants={"fen", "priya"},
    )
    cm.acknowledge("priya", conv.id)
    cm.send_message("priya", conv.id, "We transfer every 48 hours.", _now())

    transcript = conv.transcript()
    assert "[fen] (to priya): How does the passage work?" in transcript
    assert "[priya]: We transfer every 48 hours." in transcript


@pytest.mark.asyncio
async def test_wait_for_messages():
    cm = ConversationManager()
    cm.register_agent("priya")
    cm.register_agent("fen")

    async def delayed_message():
        await asyncio.sleep(0.05)
        cm.start_conversation(
            initiator="fen",
            location="main_lab",
            message="Hi Priya!",
            timestamp=_now(),
            mode="public",
            participants={"fen", "priya"},
        )

    task = asyncio.create_task(delayed_message())
    got_message = await cm.wait_for_messages("priya", timeout=2.0)

    assert got_message is True
    assert len(cm.get_pending("priya")) == 1
    await task
