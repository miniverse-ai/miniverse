"""Planning module scaffolding.

Defines placeholder interfaces for planner outputs so implementers can
start drafting prompts/logic. Real implementations will convert agent
profiles + scratchpad + world context into structured plans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Protocol

from .context import PromptContext
from .scratchpad import Scratchpad


@dataclass
class PlanStep:
    """Represents a single step in an agent plan (placeholder).

    Fields are intentionally loose for now; we will firm up the schema when
    plan prompts are finalized.
    """

    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """Container for multi-step plans (daily agenda, projects, etc.).

    ``steps`` are ordered; ``metadata`` can carry schedule info (timestamps,
    priorities, goals). The planner should populate this structure in the
    future.
    """

    steps: List[PlanStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Planner(Protocol):
    """Protocol for plan generators.

    Planners are optional enhancements for agents that need multi-step reasoning.
    Common implementations:

    - **LLMPlanner**: LLM generates plans based on agent personality, world state,
      and recent memories (Stanford Generative Agents pattern)
    - **Custom deterministic**: Hardcoded plans based on agent role or conditions
      (see examples/workshop/run.py DeterministicPlanner)
    - **None**: Skip planning phase entirely (agent is purely reactive via executor)

    When to use:
    - LLM agents that benefit from long-term coherence and goal pursuit
    - Deterministic agents with complex multi-step behaviors
    - Skip for simple reactive agents (deterministic or LLM)
    """

    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context: Any,
        context: PromptContext,
    ) -> Plan:
        """Produce/update a plan for the agent.

        Parameters
        ----------
        agent_id:
            Identifier for the agent requesting a plan.
        scratchpad:
            Current scratchpad (may contain previous plan state). Will be None
            if agent cognition doesn't use scratchpad.
        world_context:
            Arbitrary context object (world state snapshot, scenario config,
            etc.). Type kept broad until we finalize the pipeline.
        context:
            Assembled prompt context (agent profile, perception, memories, etc.)
        """

        ...


# TODO: add helper functions for merging new plan outputs into scratchpad.
