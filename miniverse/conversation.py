"""Conversation manager for async multi-agent communication.

Routes messages between co-located agents, manages multi-turn conversation
state, and supports both public (talk) and private (message) modes.
Conversations are first-class: they persist across agent decision cycles
and naturally conclude when participants disengage.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A single message in a conversation."""

    id: str
    conversation_id: str
    sender: str
    content: str
    timestamp: datetime
    addressed_to: Optional[str] = None  # None = broadcast to all in conversation
    in_reply_to: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_private(self) -> bool:
        return self.metadata.get("mode") == "private"


@dataclass
class Conversation:
    """An active multi-turn conversation between co-located agents.

    A conversation starts when an agent talks in a location. All agents
    present in that location become participants. The conversation continues
    until all agents have disengaged or left the location.

    For private conversations (message mode), only the sender and recipient
    are participants.
    """

    id: str
    location: str
    mode: str  # "public" or "private"
    participants: Set[str] = field(default_factory=set)
    messages: List[Message] = field(default_factory=list)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    # Track who has pending messages to respond to.
    # Maps agent_id -> list of message IDs they haven't responded to yet.
    pending: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    @property
    def turn_count(self) -> int:
        return len(self.messages)

    def add_message(self, msg: Message) -> None:
        """Add a message and mark it as pending for other participants."""
        self.messages.append(msg)
        for participant in self.participants:
            if participant != msg.sender:
                self.pending.setdefault(participant, []).append(msg.id)

    def get_pending_messages(self, agent_id: str) -> List[Message]:
        """Get messages this agent hasn't responded to yet."""
        pending_ids = self.pending.get(agent_id, [])
        if not pending_ids:
            return []
        msg_map = {m.id: m for m in self.messages}
        return [msg_map[mid] for mid in pending_ids if mid in msg_map]

    def acknowledge(self, agent_id: str) -> None:
        """Mark all pending messages as seen by this agent."""
        self.pending.pop(agent_id, None)

    def remove_participant(self, agent_id: str) -> None:
        """Remove a participant (they left or disengaged)."""
        self.participants.discard(agent_id)
        self.pending.pop(agent_id, None)
        if not self.participants:
            self.ended_at = datetime.now()

    def transcript(self) -> str:
        """Return the full conversation as readable text."""
        lines = []
        for msg in self.messages:
            prefix = f"[{msg.sender}]"
            if msg.addressed_to:
                prefix += f" (to {msg.addressed_to})"
            lines.append(f"{prefix}: {msg.content}")
        return "\n".join(lines)

    def recent_transcript(self, last_n: int = 5) -> str:
        """Return the last N messages as readable text."""
        recent = self.messages[-last_n:]
        lines = []
        for msg in recent:
            prefix = f"[{msg.sender}]"
            if msg.addressed_to:
                prefix += f" (to {msg.addressed_to})"
            lines.append(f"{prefix}: {msg.content}")
        return "\n".join(lines)


class ConversationManager:
    """Routes messages between agents and manages conversation state.

    Supports:
    - Public conversations: agent talks in a location, all co-located agents hear
    - Private messages: point-to-point, only sender and recipient see
    - Group conversations: 3+ agents participating in the same location
    - Multiple concurrent conversations in different locations
    - Conversation history for memory integration
    """

    def __init__(self) -> None:
        # Active conversations by ID
        self._conversations: Dict[str, Conversation] = {}
        # Index: location -> active public conversation ID (at most one per location)
        self._location_conversations: Dict[str, str] = {}
        # Index: agent_id -> set of conversation IDs they're in
        self._agent_conversations: Dict[str, Set[str]] = {}
        # Notification queue: agent_id -> asyncio.Event (set when new messages arrive)
        self._notify: Dict[str, asyncio.Event] = {}
        # Completed conversations (for logging/analysis)
        self._history: List[Conversation] = []

    def register_agent(self, agent_id: str) -> None:
        """Register an agent so they can receive notifications."""
        self._agent_conversations.setdefault(agent_id, set())
        if agent_id not in self._notify:
            self._notify[agent_id] = asyncio.Event()

    def start_conversation(
        self,
        initiator: str,
        location: str,
        message: str,
        timestamp: datetime,
        *,
        mode: str = "public",
        target: Optional[str] = None,
        participants: Optional[Set[str]] = None,
    ) -> Conversation:
        """Start a new conversation or join an existing one at a location.

        For public mode:
          If there's already an active conversation at this location, join it
          and add the message. Otherwise create a new one with all provided
          participants.

        For private mode:
          Always creates a new conversation between initiator and target.

        Parameters
        ----------
        initiator: Agent starting the conversation.
        location: Where the conversation happens.
        message: What the initiator says.
        timestamp: Current simulation time.
        mode: "public" (location broadcast) or "private" (point-to-point).
        target: For private mode, who to message. For public, who to address (optional).
        participants: For public mode, all agents in the location (including initiator).
        """
        if mode == "private":
            if not target:
                raise ValueError("Private conversations require a target")
            conv = Conversation(
                id=str(uuid.uuid4()),
                location=location,
                mode="private",
                participants={initiator, target},
                started_at=timestamp,
            )
            self._conversations[conv.id] = conv
            self._agent_conversations.setdefault(initiator, set()).add(conv.id)
            self._agent_conversations.setdefault(target, set()).add(conv.id)
        else:
            # Public: join existing location conversation or create new
            existing_id = self._location_conversations.get(location)
            if existing_id and existing_id in self._conversations:
                conv = self._conversations[existing_id]
                # Add initiator if not already in
                conv.participants.add(initiator)
                if participants:
                    conv.participants.update(participants)
            else:
                all_participants = participants or {initiator}
                all_participants.add(initiator)
                conv = Conversation(
                    id=str(uuid.uuid4()),
                    location=location,
                    mode="public",
                    participants=all_participants,
                    started_at=timestamp,
                )
                self._conversations[conv.id] = conv
                self._location_conversations[location] = conv.id
                for p in all_participants:
                    self._agent_conversations.setdefault(p, set()).add(conv.id)

        # Add the initial message
        msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            sender=initiator,
            content=message,
            timestamp=timestamp,
            addressed_to=target,
            metadata={"mode": mode},
        )
        conv.add_message(msg)

        # Notify participants
        for p in conv.participants:
            if p != initiator and p in self._notify:
                self._notify[p].set()

        logger.debug(
            "Conversation %s started by %s at %s (%s mode, %d participants)",
            conv.id[:8], initiator, location, mode, len(conv.participants),
        )
        return conv

    def send_message(
        self,
        sender: str,
        conversation_id: str,
        content: str,
        timestamp: datetime,
        *,
        addressed_to: Optional[str] = None,
    ) -> Message:
        """Send a message in an active conversation.

        Parameters
        ----------
        sender: Who's speaking.
        conversation_id: Which conversation.
        content: What they say.
        timestamp: Current simulation time.
        addressed_to: Optionally address a specific participant.
        """
        conv = self._conversations.get(conversation_id)
        if not conv:
            raise ValueError(f"Conversation {conversation_id} not found")
        if not conv.is_active:
            raise ValueError(f"Conversation {conversation_id} has ended")
        if sender not in conv.participants:
            raise ValueError(f"{sender} is not in conversation {conversation_id}")

        msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            sender=sender,
            content=content,
            timestamp=timestamp,
            addressed_to=addressed_to,
            metadata={"mode": conv.mode},
        )
        conv.add_message(msg)

        # Notify other participants
        for p in conv.participants:
            if p != sender and p in self._notify:
                self._notify[p].set()

        return msg

    def get_pending(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get all pending messages across all conversations for an agent.

        Returns a list of dicts with conversation context:
        [
            {
                "conversation_id": str,
                "conversation_mode": "public" | "private",
                "location": str,
                "participants": [str, ...],
                "messages": [Message, ...],  # unread messages
                "transcript": str,           # recent conversation history
            }
        ]
        """
        result = []
        conv_ids = self._agent_conversations.get(agent_id, set())

        for conv_id in conv_ids:
            conv = self._conversations.get(conv_id)
            if not conv or not conv.is_active:
                continue
            pending_msgs = conv.get_pending_messages(agent_id)
            if not pending_msgs:
                continue

            result.append({
                "conversation_id": conv.id,
                "conversation_mode": conv.mode,
                "location": conv.location,
                "participants": sorted(conv.participants),
                "messages": pending_msgs,
                "transcript": conv.recent_transcript(last_n=8),
                "turn_count": conv.turn_count,
            })

        return result

    def acknowledge(self, agent_id: str, conversation_id: str) -> None:
        """Mark pending messages as read in a conversation."""
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.acknowledge(agent_id)

    def acknowledge_all(self, agent_id: str) -> None:
        """Mark all pending messages in all conversations as read."""
        conv_ids = self._agent_conversations.get(agent_id, set())
        for conv_id in conv_ids:
            conv = self._conversations.get(conv_id)
            if conv:
                conv.acknowledge(agent_id)

    async def wait_for_messages(self, agent_id: str, timeout: float = 30.0) -> bool:
        """Wait until this agent has new messages, or timeout.

        Returns True if messages arrived, False on timeout.
        """
        event = self._notify.get(agent_id)
        if not event:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            event.clear()
            return True
        except asyncio.TimeoutError:
            event.clear()
            return False

    def leave_conversation(self, agent_id: str, conversation_id: str) -> None:
        """Agent leaves a conversation."""
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.remove_participant(agent_id)
            agent_convs = self._agent_conversations.get(agent_id)
            if agent_convs:
                agent_convs.discard(conversation_id)
            # If conversation ended, archive it
            if not conv.is_active:
                self._archive_conversation(conv)

    def leave_all(self, agent_id: str) -> None:
        """Agent leaves all conversations (e.g., when moving locations)."""
        conv_ids = list(self._agent_conversations.get(agent_id, set()))
        for conv_id in conv_ids:
            self.leave_conversation(agent_id, conv_id)

    def get_active_conversation_at(self, location: str) -> Optional[Conversation]:
        """Get the active public conversation at a location, if any."""
        conv_id = self._location_conversations.get(location)
        if conv_id:
            conv = self._conversations.get(conv_id)
            if conv and conv.is_active:
                return conv
        return None

    def get_agent_conversations(self, agent_id: str) -> List[Conversation]:
        """Get all active conversations this agent is in."""
        conv_ids = self._agent_conversations.get(agent_id, set())
        return [
            self._conversations[cid]
            for cid in conv_ids
            if cid in self._conversations and self._conversations[cid].is_active
        ]

    def agents_at_location(self, location: str) -> Set[str]:
        """Get agents that are in an active conversation at this location.

        Note: this only returns agents in conversations, not all agents at the
        location. The orchestrator should provide the full location roster.
        """
        conv = self.get_active_conversation_at(location)
        if conv:
            return set(conv.participants)
        return set()

    def _archive_conversation(self, conv: Conversation) -> None:
        """Move a completed conversation to history."""
        self._history.append(conv)
        self._conversations.pop(conv.id, None)
        loc_conv_id = self._location_conversations.get(conv.location)
        if loc_conv_id == conv.id:
            del self._location_conversations[conv.location]
        logger.debug(
            "Conversation %s archived (%d messages, %s)",
            conv.id[:8], conv.turn_count, conv.mode,
        )

    @property
    def history(self) -> List[Conversation]:
        """All completed conversations."""
        return list(self._history)

    @property
    def active_count(self) -> int:
        return sum(1 for c in self._conversations.values() if c.is_active)

    def stats(self) -> Dict[str, Any]:
        """Summary statistics for logging."""
        return {
            "active_conversations": self.active_count,
            "total_completed": len(self._history),
            "total_messages": sum(c.turn_count for c in self._conversations.values())
            + sum(c.turn_count for c in self._history),
        }
