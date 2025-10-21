"""Agent cognition runtime scaffolding.

Provides a lightweight container that bundles the planner, executor,
reflection engine, and scratchpad for each agent. The goal is to make the
orchestrator agnostic to the specific implementations while giving us a
clear place to hang configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from .cadence import CognitionCadence
from .executor import DefaultRuleBasedExecutor, Executor
from .planner import Planner
from .prompts import DEFAULT_PROMPTS, PromptLibrary
from .reflection import ReflectionEngine
from .scratchpad import Scratchpad


@dataclass
class AgentCognition:
    """Collection of cognition modules bound to a single agent.

    Only executor is required - planner, reflection, and scratchpad are optional
    enhancements for LLM-driven agents:

    - **planner**: Optional. Generates multi-step plans (LLMPlanner for emergent behavior,
      custom Planner for deterministic multi-step logic, None for purely reactive agents)
    - **executor**: Required. Chooses actions each tick (LLMExecutor for LLM decisions,
      custom executor for deterministic if/then logic)
    - **reflection**: Optional. Synthesizes insights from memories (LLMReflectionEngine
      for Stanford-style reflection, None to skip reflection phase)
    - **scratchpad**: Optional. Working memory for plan state and temporary data
      (needed if using planner, optional otherwise)

    Examples:
        Minimal deterministic agent (just executor):
            AgentCognition(executor=MyExecutor())

        Simple reactive LLM agent (no planning/reflection):
            AgentCognition(executor=LLMExecutor())

        LLM with planning:
            AgentCognition(
                executor=LLMExecutor(),
                planner=LLMPlanner(),
                scratchpad=Scratchpad()
            )

        Full Stanford-style agent:
            AgentCognition(
                executor=LLMExecutor(),
                planner=LLMPlanner(),
                reflection=LLMReflectionEngine(),
                scratchpad=Scratchpad()
            )
    """

    executor: Executor
    planner: Optional[Planner] = None
    reflection: Optional[ReflectionEngine] = None
    scratchpad: Optional[Scratchpad] = None
    prompt_library: PromptLibrary = DEFAULT_PROMPTS
    cadence: CognitionCadence = field(default_factory=CognitionCadence)


def build_default_cognition() -> AgentCognition:
    """Return a minimal cognition stack for quick-start scenarios.

    Notes
    -----
    Returns a minimal agent configuration:
    - No planner (purely reactive)
    - No reflection (no memory synthesis)
    - No scratchpad (no working memory)

    For production use, provide an explicit executor implementation.
    """

    return AgentCognition(
        executor=DefaultRuleBasedExecutor(),
        planner=None,
        reflection=None,
        scratchpad=None,
        prompt_library=DEFAULT_PROMPTS,
        cadence=CognitionCadence(),
    )


AgentCognitionMap = Dict[str, AgentCognition]
"""Convenience alias for the agent cognition registry."""
