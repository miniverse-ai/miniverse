"""LLM-backed implementations for planner, executor, and reflection engines."""

from __future__ import annotations

import shutil
import textwrap
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from miniverse.llm_calls import call_llm_with_retries
from miniverse.schemas import AgentAction, AgentPerception, StepDecision

from .context import PromptContext
from .executor import Executor
from .planner import Plan, PlanStep, Planner
from .prompts import DEFAULT_PROMPTS, PromptLibrary
from .reflection import ReflectionEngine, ReflectionResult
from .renderers import render_prompt
from .scratchpad import Scratchpad


class LLMPlanStepModel(BaseModel):
    description: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LLMPlanResponse(BaseModel):
    steps: List[LLMPlanStepModel] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LLMReflectionItem(BaseModel):
    content: str
    importance: int = Field(default=5, ge=1, le=10)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LLMReflectionResponse(BaseModel):
    reflections: List[LLMReflectionItem] = Field(default_factory=list)


def _wrap_verbose_line(
    text: str,
    *,
    prefix: str,
    continuation: Optional[str] = None,
) -> str:
    normalized = " ".join(str(text).split())
    if not normalized:
        return prefix.rstrip()
    terminal_width = shutil.get_terminal_size(fallback=(112, 24)).columns
    width = max(76, min(140, terminal_width - 2))
    width = max(width, len(prefix) + 20)
    return textwrap.fill(
        normalized,
        width=width,
        initial_indent=prefix,
        subsequent_indent=continuation if continuation is not None else " " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )


class LLMPlanner(Planner):
    """Planner that delegates plan generation to an LLM.

    Uses LLM to generate multi-step plans based on agent personality, current world state,
    recent memories, and existing plan. Supports custom prompt templates for different
    planning styles (daily routine, project planning, reactive vs proactive).
    """

    def __init__(
        self,
        *,
        template_name: str = "plan",
        prompt_library: Optional[PromptLibrary] = None,
    ) -> None:
        # Template name identifies which prompt to use from library (e.g., "plan_daily",
        # "plan_project", "plan_crisis"). Allows per-agent or per-scenario customization.
        self.template_name = template_name
        # Custom prompt library for scenario-specific planning styles. If None, uses
        # context-provided library or system defaults.
        self.prompt_library = prompt_library

    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context: Any,
        context: PromptContext,
    ) -> Plan:
        # Extract LLM provider and model from context. These come from orchestrator config.
        # LLMPlanner REQUIRES LLM configuration - fail fast with clear error if missing.
        provider = context.extra.get("llm_provider")
        model = context.extra.get("llm_model")
        if not provider or not model:
            raise ValueError(
                f"LLMPlanner requires LLM configuration (agent: {agent_id}). "
                f"Set LLM_PROVIDER and LLM_MODEL environment variables, or use "
                f"planner=None for agents that don't need planning."
            )

        # Resolve prompt library with three-level fallback: instance config -> context
        # config -> system defaults. This allows maximum flexibility in prompt customization.
        library = self.prompt_library or context.extra.get("prompt_library") or DEFAULT_PROMPTS
        try:
            # Look up template by name. Template contains system and user prompt components
            # with placeholders for context injection.
            template = library.get(self.template_name)
        except KeyError:
            # Template not found - fall back to default planning template
            template = DEFAULT_PROMPTS.get("plan")

        # Render template with context. Replaces placeholders like {{perception_json}},
        # {{memories_text}} with actual context data. Returns system and user prompts.
        rendered = render_prompt(template, context, include_default=False)

        # Debug logging: Show LLM prompts if enabled
        import os
        debug_llm = os.getenv('DEBUG_LLM', '').lower() in ('1', 'true', 'yes')
        verbose_llm = os.getenv("MINIVERSE_VERBOSE", "").lower() in ("1", "true", "yes")
        if debug_llm:
            print(f"\n{'='*80}")
            print(f"[LLM PLANNER] Agent: {agent_id}")
            print(f"{'='*80}")
            print(f"\n[SYSTEM PROMPT]")
            print(f"{'-'*80}")
            print(rendered.system)
            print(f"\n[USER PROMPT]")
            print(f"{'-'*80}")
            print(rendered.user)
            print(f"{'='*80}\n")

        # Call LLM with retry logic. LLM generates structured response matching
        # LLMPlanResponse schema (list of steps with descriptions and metadata).
        # Retries up to 3 times with validation feedback if schema doesn't match.
        response = await call_llm_with_retries(
            system_prompt=rendered.system,
            user_prompt=rendered.user,
            llm_provider=provider,
            llm_model=model,
            response_model=LLMPlanResponse,
        )

        # Debug logging: Show LLM response
        if debug_llm:
            print(f"\n[LLM RESPONSE]")
            print(f"{'-'*80}")
            print(f"Steps generated: {len(response.steps)}")
            for i, step in enumerate(response.steps, 1):
                print(f"  {i}. {step.description}")
                if step.metadata:
                    print(f"     Metadata: {step.metadata}")
            print(f"{'='*80}\n")
        elif verbose_llm:
            print(f"    [LLM Planner] {agent_id}: generated {len(response.steps)} plan step(s)")
            for i, step in enumerate(response.steps[:1], 1):
                print(
                    _wrap_verbose_line(
                        step.description,
                        prefix=f"      {i}. ",
                        continuation="         ",
                    )
                )

        # Convert LLM response (Pydantic models) to internal Plan/PlanStep dataclasses.
        # This decouples LLM response schema from internal planning representation,
        # allowing us to change LLM format without breaking downstream code.
        steps = [
            PlanStep(description=step.description, metadata=dict(step.metadata))
            for step in response.steps
        ]
        return Plan(steps=steps, metadata=dict(response.metadata))


class LLMReflectionEngine(ReflectionEngine):
    """Reflection engine that requests diary entries from an LLM.

    Stanford Generative Agents pattern: Periodically synthesize recent experiences into
    higher-level insights. LLM reviews recent memories and generates reflections like
    "I'm running low on resources" or "My relationship with Agent X is deteriorating".
    Reflections are stored as high-importance memories to influence future decisions.
    """

    def __init__(
        self,
        *,
        template_name: str = "reflect_diary",
        prompt_library: Optional[PromptLibrary] = None,
    ) -> None:
        # Template name identifies reflection style (diary entries, strategic analysis, etc.)
        self.template_name = template_name
        # Custom prompt library for scenario-specific reflection styles
        self.prompt_library = prompt_library

    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context: dict[str, Any] | None = None,
        context: PromptContext | None = None,
    ) -> List[ReflectionResult]:
        # Context is required for reflection - contains memories, world state, plan.
        # If None, caller didn't build context properly - fail fast with clear error.
        if context is None:
            raise ValueError(
                f"LLMReflectionEngine requires PromptContext (agent: {agent_id}). "
                f"This is likely an orchestrator bug - context should always be provided."
            )

        # Extract LLM credentials from context. LLMReflectionEngine REQUIRES LLM
        # configuration - fail fast with clear error if missing.
        provider = context.extra.get("llm_provider")
        model = context.extra.get("llm_model")
        if not provider or not model:
            raise ValueError(
                f"LLMReflectionEngine requires LLM configuration (agent: {agent_id}). "
                f"Set LLM_PROVIDER and LLM_MODEL environment variables, or use "
                f"reflection=None for agents that don't need reflection."
            )

        # Resolve prompt library with three-level fallback (same as planner)
        library = self.prompt_library or context.extra.get("prompt_library") or DEFAULT_PROMPTS
        try:
            # Look up reflection template. Reflection prompts typically ask LLM to
            # identify patterns, synthesize insights, or update beliefs based on memories.
            template = library.get(self.template_name)
        except KeyError:
            # Template not found - fall back to default diary-style reflection
            template = DEFAULT_PROMPTS.get("reflect_diary")

        # Render template with context. Reflection prompts receive larger memory window
        # (20 vs 10) to identify longer-term patterns.
        rendered = render_prompt(template, context, include_default=False)

        # Debug logging: Show LLM prompts if enabled
        import os
        debug_llm = os.getenv('DEBUG_LLM', '').lower() in ('1', 'true', 'yes')
        if debug_llm:
            print(f"\n{'='*80}")
            print(f"[LLM REFLECTION] Agent: {agent_id}")
            print(f"{'='*80}")
            print(f"\n[SYSTEM PROMPT]")
            print(f"{'-'*80}")
            print(rendered.system)
            print(f"\n[USER PROMPT]")
            print(f"{'-'*80}")
            print(rendered.user)
            print(f"{'='*80}\n")

        # Call LLM with retry logic. LLM generates structured response containing list
        # of reflections. Each reflection has content (text), importance (1-10), and
        # optional metadata (tags, categories, etc.).
        response = await call_llm_with_retries(
            system_prompt=rendered.system,
            user_prompt=rendered.user,
            llm_provider=provider,
            llm_model=model,
            response_model=LLMReflectionResponse,
        )

        # Debug logging: Show LLM response
        if debug_llm:
            print(f"\n[LLM RESPONSE]")
            print(f"{'-'*80}")
            print(f"Reflections generated: {len(response.reflections)}")
            for i, refl in enumerate(response.reflections, 1):
                print(f"  {i}. [Importance: {refl.importance}/10] {refl.content}")
                if refl.metadata:
                    print(f"     Metadata: {refl.metadata}")
            print(f"{'='*80}\n")
        # Verbose reflection rendering is handled centrally by orchestrator to
        # avoid duplicate lines and preserve wrapped formatting.

        # Convert LLM response to ReflectionResult objects. Reflections are stored as
        # memories with elevated importance (typically 6-10 vs 5 for actions), making
        # them more likely to surface in future memory retrieval.
        return [
            ReflectionResult(
                content=item.content,
                importance=item.importance,
                metadata=dict(item.metadata),
            )
            for item in response.reflections
        ]


class LLMExecutor(Executor):
    """Executor that calls LLM for action selection on every tick.

    Pure LLM-driven executor with NO deterministic fallback. LLMExecutor REQUIRES
    LLM configuration and raises ValueError if missing.

    Use cases:
    - When you want guaranteed LLM-driven agent behavior
    - Social simulations where emergent decisions are critical
    - Scenarios where agents must adapt to unpredictable conditions

        For deterministic behavior, use RuleBasedExecutor instead.
    For pure deterministic logic, create custom executor (e.g., RuleBasedExecutor).

    Example:
        # Pure LLM cognition stack
        cognition = AgentCognition(
            planner=LLMPlanner(template_name="warehouse_plan"),
            executor=LLMExecutor(template_name="warehouse_execute"),
            reflection=LLMReflectionEngine(template_name="warehouse_reflect"),
            scratchpad=Scratchpad(),
        )

    Args:
        template_name: Name of execution prompt template in library (default: "execute_tick")
        prompt_library: Custom prompt library (optional, falls back to defaults)
    """

    def __init__(
        self,
        *,
        template: Optional[PromptTemplate] = None,
        template_name: Optional[str] = None,
        prompt_library: Optional[PromptLibrary] = None,
        available_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        # Template name identifies which execution prompt to use. Common patterns:
        # - "execute_tick": General-purpose action selection
        # - "execute_warehouse": Domain-specific (inventory vs fulfillment decisions)
        # - "execute_social": Communication-focused actions
        self.template = template
        self.template_name = template_name
        # Custom prompt library for scenario-specific execution styles
        self.prompt_library = prompt_library
        self.available_actions = available_actions or []

    async def choose_action(
        self,
        agent_id: str,
        perception: AgentPerception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> AgentAction:
        # Extract LLM credentials from context. These come from Orchestrator initialization.
        provider = context.extra.get("llm_provider")
        model = context.extra.get("llm_model")

        # LLMExecutor is strict: if no LLM configured, it's a configuration error.
        # This is intentional - forces users to be explicit about LLM vs deterministic modes.
        if not provider or not model:
            raise ValueError(
                f"LLMExecutor requires LLM configuration (agent: {agent_id}). "
                f"Set LLM_PROVIDER and LLM_MODEL environment variables, or use RuleBasedExecutor "
                f"for deterministic actions when LLM is unavailable or undesired."
            )

        # Determine template source: inline overrides library/name; else require name
        if self.template is not None:
            template = self.template
        else:
            name = self.template_name or "default"
            # Resolve prompt library with three-level fallback (same as planner/reflection)
            library = self.prompt_library or context.extra.get("prompt_library") or DEFAULT_PROMPTS
            try:
                template = library.get(name)
            except KeyError:
                # Final fallback to minimal default
                template = DEFAULT_PROMPTS.get("default")

        # Render template with context. Replaces placeholders like {{perception_json}},
        # {{plan_step_description}}, {{memories_text}} with actual agent data.
        # Inject available_actions into context.extra for renderer consumption
        # (non-destructive copy)
        context.extra.setdefault("available_actions", self.available_actions)

        rendered = render_prompt(template, context, include_default=False)

        # Debug logging: Show LLM prompts if enabled
        import os
        debug_llm = os.getenv('DEBUG_LLM', '').lower() in ('1', 'true', 'yes')
        verbose_llm = os.getenv("MINIVERSE_VERBOSE", "").lower() in ("1", "true", "yes")
        if debug_llm:
            print(f"\n{'='*80}")
            print(f"[LLM EXECUTOR] Agent: {agent_id}")
            print(f"{'='*80}")
            print(f"\n[SYSTEM PROMPT]")
            print(f"{'-'*80}")
            print(rendered.system)
            print(f"\n[USER PROMPT]")
            print(f"{'-'*80}")
            print(rendered.user)
            print(f"{'='*80}\n")

        # Call LLM with retry logic. LLM generates structured AgentAction response
        # matching Pydantic schema (action_type, target, parameters, reasoning, communication).
        # Retries up to 3 times if validation fails, providing schema feedback to LLM.
        action = await call_llm_with_retries(
            system_prompt=rendered.system,
            user_prompt=rendered.user,
            llm_provider=provider,
            llm_model=model,
            response_model=AgentAction,
        )

        # Debug logging: Show LLM response
        if debug_llm:
            print(f"\n[LLM RESPONSE]")
            print(f"{'-'*80}")
            print(f"Action: {action.action_type}")
            if action.target:
                print(f"Target: {action.target}")
            if action.parameters:
                print(f"Parameters: {action.parameters}")
            print(f"Reasoning: {action.reasoning}")
            if action.communication:
                print(f"Communication:")
                print(f"  To: {action.communication.get('to', 'N/A')}")
                msg = action.communication.get('message', '')
                print(f"  Message: {msg}")
            print(f"{'='*80}\n")
        elif verbose_llm:
            target = f" target={action.target}" if action.target else ""
            print(
                _wrap_verbose_line(
                    f"[LLM Executor] {agent_id}: {action.action_type}{target}",
                    prefix="    ",
                    continuation="      ",
                )
            )
            if action.communication:
                recipient = action.communication.get("to")
                if recipient:
                    print(
                        _wrap_verbose_line(
                            f"communication.to={recipient}",
                            prefix="      ",
                            continuation="        ",
                        )
                    )

        return action

    async def choose_step(
        self,
        agent_id: str,
        perception: AgentPerception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> StepDecision:
        """Select a composite step (communication + action) via LLM call.

        Same pipeline as choose_action but returns StepDecision instead of
        AgentAction — the LLM outputs messages, public speech, and an action
        in a single structured response.
        """
        provider = context.extra.get("llm_provider")
        model = context.extra.get("llm_model")

        if not provider or not model:
            raise ValueError(
                f"LLMExecutor requires LLM configuration (agent: {agent_id}). "
                f"Set LLM_PROVIDER and LLM_MODEL environment variables."
            )

        if self.template is not None:
            template = self.template
        else:
            name = self.template_name or "default"
            library = self.prompt_library or context.extra.get("prompt_library") or DEFAULT_PROMPTS
            try:
                template = library.get(name)
            except KeyError:
                template = DEFAULT_PROMPTS.get("default")

        context.extra.setdefault("available_actions", self.available_actions)
        rendered = render_prompt(template, context, include_default=False)

        import os
        debug_llm = os.getenv('DEBUG_LLM', '').lower() in ('1', 'true', 'yes')
        verbose_llm = os.getenv("MINIVERSE_VERBOSE", "").lower() in ("1", "true", "yes")
        if debug_llm:
            print(f"\n{'='*80}")
            print(f"[LLM EXECUTOR choose_step] Agent: {agent_id}")
            print(f"{'='*80}")
            print(f"\n[SYSTEM PROMPT]")
            print(f"{'-'*80}")
            print(rendered.system)
            print(f"\n[USER PROMPT]")
            print(f"{'-'*80}")
            print(rendered.user)
            print(f"{'='*80}\n")

        step = await call_llm_with_retries(
            system_prompt=rendered.system,
            user_prompt=rendered.user,
            llm_provider=provider,
            llm_model=model,
            response_model=StepDecision,
        )

        if debug_llm:
            print(f"\n[LLM RESPONSE — StepDecision]")
            print(f"{'-'*80}")
            print(f"Action: {step.action_type}")
            if step.target:
                print(f"Target: {step.target}")
            if step.new_messages:
                for m in step.new_messages:
                    print(f"Message to {m.to}: {m.message}")
            if step.public_speech:
                print(f"Public speech: {step.public_speech}")
            print(f"Reasoning: {step.reasoning}")
            print(f"{'='*80}\n")
        elif verbose_llm:
            target = f" target={step.target}" if step.target else ""
            print(
                _wrap_verbose_line(
                    f"[LLM Executor] {agent_id}: {step.action_type}{target}",
                    prefix="    ",
                    continuation="      ",
                )
            )

        return step

    def uses_llm(self) -> bool:
        return True
