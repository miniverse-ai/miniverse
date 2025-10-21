"""Cognition module scaffolding for Miniverse.

This package houses the agent cognition stack (scratchpad, planner,
executor, reflection). All modules except executor are optional - use
None to skip planning or reflection phases.
"""

from .cadence import (
    CognitionCadence,
    PlannerCadence,
    ReflectionCadence,
    TickInterval,
    tick_to_time_block,
)
from .context import PromptContext, build_prompt_context
from .executor import DefaultRuleBasedExecutor, Executor, RuleBasedExecutor
from .llm import LLMExecutor, LLMPlanner, LLMReflectionEngine
from .planner import Plan, Planner, PlanStep
from .prompts import DEFAULT_PROMPTS, PromptLibrary, PromptTemplate
from .reflection import ReflectionEngine, ReflectionResult
from .renderers import RenderedPrompt, render_prompt
from .runtime import AgentCognition, AgentCognitionMap, build_default_cognition
from .scratchpad import Scratchpad

__all__ = [
    "Scratchpad",
    "Planner",
    "Plan",
    "PlanStep",
    "Executor",
    "RuleBasedExecutor",
    "DefaultRuleBasedExecutor",
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
