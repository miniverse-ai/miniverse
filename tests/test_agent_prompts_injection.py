"""Test that agent_prompts are properly injected into user prompts."""

from datetime import datetime, timezone

import pytest

from miniverse.cognition.context import PromptContext
from miniverse.cognition.prompts import DEFAULT_PROMPTS
from miniverse.cognition.renderers import render_prompt
from miniverse.schemas import (
    AgentPerception,
    AgentProfile,
    EnvironmentState,
    ResourceState,
    WorldState,
)


def test_agent_prompts_injection():
    """Verify that base_agent_prompt from context.extra is prepended to user prompt."""

    # Create minimal context with a test marker
    test_marker = "[TEST_MARKER_ABC123]"
    agent_prompt = f"{test_marker} You are a test agent with custom instructions."

    context = PromptContext(
        agent_profile=AgentProfile(
            agent_id="test_agent",
            name="Test Agent",
            background="Test background",
            role="tester",
            personality="methodical",
            skills={},
            goals=["verify prompts work"],
            relationships={},
        ),
        perception=AgentPerception(
            agent_id="test_agent",
            tick=0,
            location="test_location",
            nearby_agents=[],
            recent_memories=[],
            messages=[],
            alerts=[],
        ),
        world_snapshot=WorldState(
            tick=0,
            timestamp=datetime.now(timezone.utc),
            environment=EnvironmentState(metrics={}),
            resources=ResourceState(metrics={}),
            agents=[],
        ),
        scratchpad_state={},
        plan_state={},
        memories=[],
        extra={"initial_state_agent_prompt": agent_prompt},
    )

    # Use default template (has initial_state_agent_prompt placeholder)
    template = DEFAULT_PROMPTS.get("default")

    # Render the prompt
    rendered = render_prompt(template, context)

    # Verify marker appears in user prompt
    assert (
        test_marker in rendered.user
    ), f"Test marker not found in user prompt. Got: {rendered.user[:200]}"

    # Verify it's at the beginning (prepended)
    assert rendered.user.startswith(
        test_marker
    ), f"Test marker should be at start of user prompt. Got: {rendered.user[:100]}"

    # Verify template content still appears
    assert (
        "simulation" in rendered.system.lower()
    ), "Default template content should still be present"


def test_agent_prompts_with_snake_example():
    """Test using actual snake game marker pattern."""

    snake_marker = "[SNAKE_AI_MARKER_XYZ123]"
    snake_prompt = f"""{snake_marker} You are a snake. Your ONLY action is "move" with direction parameter.

RULES:
- O = your head, o = body, * = food, # = wall
- Eat food to grow and score
- Hit wall or body = game over

REQUIRED ACTION FORMAT:
{{
  "action_type": "move",
  "parameters": {{"direction": "up|down|left|right"}}
}}

DO NOT use any other action type. ONLY move actions."""

    context = PromptContext(
        agent_profile=AgentProfile(
            agent_id="snake",
            name="Snake AI",
            background="LLM-powered snake",
            role="player",
            personality="cautious",
            skills={},
            goals=["Eat food", "Avoid walls"],
            relationships={},
        ),
        perception=AgentPerception(
            agent_id="snake",
            tick=0,
            location="grid",
            nearby_agents=[],
            recent_memories=[],
            messages=[],
            alerts=[],
        ),
        world_snapshot=WorldState(
            tick=0,
            timestamp=datetime.now(timezone.utc),
            environment=EnvironmentState(metrics={}),
            resources=ResourceState(metrics={}),
            agents=[],
        ),
        scratchpad_state={},
        plan_state={},
        memories=[],
        extra={"initial_state_agent_prompt": snake_prompt},
    )

    template = DEFAULT_PROMPTS.get("default")
    rendered = render_prompt(template, context)

    # Verify snake marker and instructions are present (in USER)
    assert snake_marker in rendered.user
    assert "ONLY move actions" in rendered.user
    assert rendered.user.startswith(snake_marker)

    # Verify template is still there
    assert "simulation" in rendered.system.lower()
