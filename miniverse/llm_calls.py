"""
LLM call functions using Mirascope for provider-agnostic LLM integration.

This module provides:
- Agent action generation (get_agent_action)
- World state processing (process_world_update)
- Structured Pydantic outputs

All functions are stateless and accept prompts/config as parameters.
No file I/O or global state.
"""

import json

from miniverse.schemas import AgentAction, AgentPerception, WorldState

from .llm_utils import call_llm_with_retries

# ============================================================================
# LLM Call Functions
# ============================================================================


async def get_agent_action(
    system_prompt: str,
    perception: AgentPerception,
    llm_provider: str,
    llm_model: str,
    *,
    user_prompt_override: str | None = None,
) -> AgentAction:
    """
    Get agent action using specified LLM provider.

    Args:
        system_prompt: Agent's full system prompt
        perception: Agent's current perception of the world
        llm_provider: LLM provider name (e.g., "openai", "anthropic", "ollama")
        llm_model: Model identifier (e.g., "gpt-5-nano", "claude-sonnet-4-5")

    Returns:
        AgentAction with agent's decision

    Raises:
        Exception: If LLM call fails
    """
    perception_str = perception.model_dump_json(indent=2)
    user_prompt = f"""
Current situation (tick {perception.tick}):

{perception_str}

What do you do? Output JSON matching AgentAction schema.
"""

    user_prompt = (
        user_prompt_override if user_prompt_override is not None else user_prompt
    )

    return await call_llm_with_retries(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm_provider=llm_provider,
        llm_model=llm_model,
        response_model=AgentAction,
    )


async def process_world_update(
    current_state: WorldState,
    actions: list[AgentAction],
    tick: int,
    system_prompt: str,
    llm_provider: str,
    llm_model: str,
    physics_applied: bool = False,
) -> WorldState:
    """
    Process world update using specified LLM provider.

    Args:
        current_state: Current WorldState
        actions: List of all agent actions this tick
        tick: New tick number
        system_prompt: World engine system prompt
        llm_provider: LLM provider name (e.g., "openai", "anthropic", "ollama")
        llm_model: Model identifier (e.g., "gpt-5-nano", "claude-sonnet-4-5")
        physics_applied: If True, deterministic physics already applied.
                        If False, LLM must handle all physics (legacy mode).

    Returns:
        Updated WorldState

    Raises:
        Exception: If LLM call fails
    """
    state_str = current_state.model_dump_json(indent=2)
    actions_str = json.dumps([a.model_dump() for a in actions], indent=2)

    physics_mode = (
        "Physics already applied"
        if physics_applied
        else "Legacy mode - handle all physics"
    )

    user_prompt = f"""
{physics_mode}

Current world state (tick {tick - 1}):

{state_str}

Agent actions this tick:

{actions_str}

Process these actions and generate updated world state for tick {tick}.
Validate physical constraints. Generate events if warranted.
Output JSON matching WorldState schema.
"""

    return await call_llm_with_retries(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm_provider=llm_provider,
        llm_model=llm_model,
        response_model=WorldState,
    )
