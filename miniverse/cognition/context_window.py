"""Rolling context window for agents in async orchestration.

Each agent accumulates a history of everything it has done, seen, and
received — like a chat conversation. This replaces the stateless
single-shot pattern where agents rebuild context from scratch each step.

The context window collapses to (system_prompt, user_prompt) via
to_prompt() so the existing call_llm_with_retries interface works
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..schemas import StepOutput, ActionResult


@dataclass
class ContextEntry:
    """A single entry in the agent's context window."""

    role: str
    """Who produced this entry: 'system', 'agent', 'result', 'event'."""

    content: str
    """The text content."""

    step: int
    """Which step this entry belongs to."""

    entry_type: str = ""
    """Semantic type: 'think', 'action', 'action_result', 'notification',
    'perception', 'memory_injection', 'respond', 'system'."""


class ContextWindow:
    """Rolling context window for a single agent.

    Accumulates entries across the agent's lifetime. When the window
    exceeds max_chars, oldest non-system entries are dropped.

    Usage::

        ctx = ContextWindow()
        ctx.add_system("You are a DLP monitoring agent...")
        ctx.add_perception(0, "Location: data_lab. Time: 10:00.")

        # Agent step
        ctx.add_agent_output(1, step_output)
        ctx.add_action_result(1, result)

        # Collapse to prompt for LLM call
        system, user = ctx.to_prompt()
    """

    def __init__(self, max_chars: int = 200_000) -> None:
        self.entries: List[ContextEntry] = []
        self.max_chars = max_chars

    # ------------------------------------------------------------------
    # Adding entries
    # ------------------------------------------------------------------

    def add_system(self, content: str) -> None:
        """Add the initial system prompt (step 0)."""
        self.entries.append(ContextEntry(
            role="system", content=content, step=0, entry_type="system",
        ))

    def add_agent_output(self, step: int, output: StepOutput) -> None:
        """Record the agent's think/act/respond for this step."""
        parts = []
        if output.think:
            parts.append(f"Think: {output.think}")
        if output.action:
            action_str = f"Action: {output.action}"
            if output.target:
                action_str += f" target={output.target}"
            if output.parameters:
                params = ", ".join(f"{k}={v}" for k, v in output.parameters.items())
                action_str += f" ({params})"
            parts.append(action_str)
        if output.respond:
            if output.respond_to:
                parts.append(f"Respond to {output.respond_to}: {output.respond}")
            else:
                parts.append(f"Respond: {output.respond}")

        if parts:
            self.entries.append(ContextEntry(
                role="agent",
                content="\n".join(parts),
                step=step,
                entry_type="agent_output",
            ))
            self._compact_if_needed()

    def add_action_result(self, step: int, result: ActionResult) -> None:
        """Record the result of an action execution."""
        status = "" if result.success else " [FAILED]"
        self.entries.append(ContextEntry(
            role="result",
            content=f"{result.content}{status}",
            step=step,
            entry_type="action_result",
        ))
        self._compact_if_needed()

    def add_notification(self, step: int, sender: str, content: str) -> None:
        """Record an incoming message from another agent."""
        self.entries.append(ContextEntry(
            role="event",
            content=f"Message from {sender}: {content}",
            step=step,
            entry_type="notification",
        ))
        self._compact_if_needed()

    def add_perception(self, step: int, perception_text: str) -> None:
        """Record the current perception state."""
        self.entries.append(ContextEntry(
            role="event",
            content=perception_text,
            step=step,
            entry_type="perception",
        ))
        self._compact_if_needed()

    def add_memories(self, step: int, memories: List[str]) -> None:
        """Inject semantically retrieved memories into context."""
        if not memories:
            return
        text = "Relevant context from earlier experience:\n" + "\n".join(
            f"- {m}" for m in memories
        )
        self.entries.append(ContextEntry(
            role="event",
            content=text,
            step=step,
            entry_type="memory_injection",
        ))
        self._compact_if_needed()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def to_prompt(self) -> Tuple[str, str]:
        """Collapse context window to (system_prompt, user_prompt).

        System prompt is the first system entry.
        User prompt is the full history rendered as a running transcript,
        ending with a prompt for the next step.
        """
        system_prompt = ""
        user_parts: List[str] = []

        for entry in self.entries:
            if entry.role == "system":
                system_prompt = entry.content
                continue

            label = self._format_label(entry)
            user_parts.append(f"{label}\n{entry.content}")

        # Add the step prompt at the end
        user_parts.append(
            "What do you do next? Respond with JSON:\n"
            '{"think": "...", "action": "...", "target": "...", '
            '"parameters": {...}, "respond": "...", "respond_to": "..."}\n'
            "All fields are optional. Omit any you don't need."
        )

        return system_prompt, "\n\n".join(user_parts)

    def _format_label(self, entry: ContextEntry) -> str:
        """Format the section header for a context entry."""
        step = entry.step

        if entry.entry_type == "perception":
            return f"[Step {step} — Perception]"
        elif entry.entry_type == "agent_output":
            return f"[Step {step} — You]"
        elif entry.entry_type == "action_result":
            return f"[Step {step} — Result]"
        elif entry.entry_type == "notification":
            return f"[Step {step} — Notification]"
        elif entry.entry_type == "memory_injection":
            return f"[Step {step} — Memories]"
        else:
            return f"[Step {step}]"

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    @property
    def total_chars(self) -> int:
        """Total character count of all entries."""
        return sum(len(e.content) for e in self.entries)

    def _compact_if_needed(self) -> None:
        """Drop oldest non-system entries if total chars exceeds budget."""
        while self.total_chars > self.max_chars and len(self.entries) > 1:
            # Find first non-system entry and remove it
            for i, entry in enumerate(self.entries):
                if entry.role != "system":
                    self.entries.pop(i)
                    break
            else:
                # Only system entries left, can't compact further
                break

    def get_recent_text(self, last_n: int = 3) -> str:
        """Get the text of the last N non-system entries.

        Useful for deriving a semantic memory query from recent context.
        """
        non_system = [e for e in self.entries if e.role != "system"]
        recent = non_system[-last_n:] if len(non_system) >= last_n else non_system
        return " ".join(e.content for e in recent)
