"""Reflection module scaffolding.

Reflection converts accumulated experiences into higher-level insights and
feeds them back into memory. This placeholder captures the planned surface
area without committing to an implementation yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from miniverse.schemas import AgentMemory

from .context import PromptContext
from .scratchpad import Scratchpad


@dataclass
class ReflectionResult:
    """Represents a reflection output awaiting persistence."""

    content: str
    importance: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)


class ReflectionEngine(Protocol):
    """Protocol for reflection strategies.

    Reflection engines are optional enhancements that synthesize insights from
    accumulated experiences (Stanford Generative Agents pattern). Common implementations:

    - **LLMReflectionEngine**: LLM reviews recent memories and generates high-level
      insights like "I'm running low on resources" or "Team coordination is deteriorating"
    - **Custom deterministic**: Hardcoded heuristics that detect patterns in memory
      (e.g., "if stress > 80 for 3+ ticks, reflect on burnout")
    - **None**: Skip reflection phase entirely (no memory synthesis)

    When to use:
    - LLM agents in long-running simulations where patterns emerge over time
    - Agents that need to "learn" and adapt based on accumulated experiences
    - Skip for short simulations, deterministic agents, or simple reactive agents

    Reflections are stored as memories with elevated importance (6-10 vs 5 for actions)
    so they surface more frequently in future memory retrieval.
    """

    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories: Iterable[AgentMemory],
        *,
        trigger_context: dict[str, Any] | None = None,
        context: PromptContext | None = None,
    ) -> list[ReflectionResult]:
        """Produce zero or more reflections for the agent.

        Parameters
        ----------
        agent_id:
            Identifier for the reflecting agent.
        scratchpad:
            Current scratchpad (may contain plan state, notes). Will be None
            if agent cognition doesn't use scratchpad.
        recent_memories:
            Recent memory objects (actions, observations, previous reflections).
        trigger_context:
            Optional metrics such as importance accumulation, tick number, etc.
        context:
            Assembled prompt context (agent profile, perception, memories, etc.).

        Returns
        -------
        list[ReflectionResult]
            Zero or more reflection insights to store as high-importance memories.
        """

        ...


# TODO: integrate with memory strategy once importance scoring is available.
