"""Cognition module scaffolding for Miniverse.

This package houses the agent cognition stack (scratchpad, planner,
executor, reflection). All modules except executor are optional - use
None to skip planning or reflection phases.
"""

from .scratchpad import Scratchpad
from .planner import Planner, Plan, PlanStep
from .executor import Executor, RuleBasedExecutor, DefaultRuleBasedExecutor, wrap_action_as_step_decision
from .reflection import ReflectionEngine, ReflectionResult
from .runtime import AgentCognition, AgentCognitionMap, build_default_cognition
from .context import PromptContext, build_prompt_context
from .prompts import PromptLibrary, PromptTemplate, DEFAULT_PROMPTS
from .renderers import render_prompt, RenderedPrompt
from .llm import LLMPlanner, LLMReflectionEngine, LLMExecutor
from .cadence import (
    CognitionCadence,
    PlannerCadence,
    ReflectionCadence,
    TickInterval,
    tick_to_time_block,
)

__all__ = [
    "Scratchpad",
    "Planner",
    "Plan",
    "PlanStep",
    "Executor",
    "RuleBasedExecutor",
    "DefaultRuleBasedExecutor",
    "wrap_action_as_step_decision",
    "ReflectionEngine",
    "ReflectionResult",
    "AgentCognition",
    "AgentCognitionMap",
    "build_default_cognition",
    "PromptContext",
    "build_prompt_context",
    "PromptLibrary",
    "PromptTemplate",
    "DEFAULT_PROMPTS",
    "render_prompt",
    "RenderedPrompt",
    "LLMPlanner",
    "LLMExecutor",
    "LLMReflectionEngine",
    "CognitionCadence",
    "PlannerCadence",
    "ReflectionCadence",
    "TickInterval",
    "tick_to_time_block",
]
