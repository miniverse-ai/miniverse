"""Tests for information isolation — agents must not see other agents' private state."""

import asyncio
import json
from datetime import datetime
import pytest

from miniverse.schemas import (
    AgentProfile, AgentStatus, AgentPerception, WorldState, Stat,
    EnvironmentState, ResourceState,
)
from miniverse.cognition.context import PromptContext, build_prompt_context


def _make_world_state():
    """Create a world state with two agents and scenario metadata."""
    return WorldState(
        tick=5,
        timestamp=datetime(2089, 7, 14, 20, 0),
        environment=EnvironmentState(metrics={
            "time": Stat(value="20:00", label="Time"),
        }),
        resources=ResourceState(metrics={}),
        agents=[
            AgentStatus(
                agent_id="sera",
                display_name="Sera Okafor",
                role="researcher",
                location="clinic",
                activity="working on secret research",
                attributes={"energy": Stat(value=80, unit="%", label="Energy")},
                metadata={"faction": "order_of_threshold"},
            ),
            AgentStatus(
                agent_id="juno",
                display_name="Juno Park",
                role="food_vendor",
                location="market",
                activity="selling noodles",
                attributes={"energy": Stat(value=60, unit="%", label="Energy")},
            ),
        ],
        recent_events=[],
        metadata={
            "demo": {
                "agent_prompts": {
                    "sera": "You are a cult leader. Recruit carefully.",
                    "juno": "You run a noodle stand. Life is routine.",
                }
            }
        },
    )


def _make_profile(agent_id: str, name: str, role: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=name,
        role=role,
        background=f"{name} lives in the district.",
        personality="neutral",
        skills={},
        goals=[],
        relationships={},
    )


def _make_perception(agent_id: str, location: str) -> AgentPerception:
    return AgentPerception(
        tick=5,
        location=location,
        personal_attributes={"energy": Stat(value=80, unit="%", label="Energy")},
        visible_resources={},
        environment_snapshot={"time": Stat(value="20:00", label="Time")},
        system_alerts=[],
        messages=[],
        recent_observations=["A quiet evening in the district."],
    )


@pytest.mark.asyncio
async def test_context_json_excludes_other_agents():
    """to_payload() should not contain other agents' statuses."""
    world = _make_world_state()
    profile = _make_profile("juno", "Juno Park", "food_vendor")
    perception = _make_perception("juno", "market")

    ctx = await build_prompt_context(
        agent_profile=profile,
        perception=perception,
        world_state=world,
        scratchpad_state={},
        plan_state={},
        memories=[],
        extra={"initial_state_agent_prompt": "You run a noodle stand."},
    )

    payload = ctx.to_payload()
    payload_json = json.dumps(payload)

    # Must not contain other agents' data
    assert "sera" not in payload_json.lower() or "sera" in payload.get("extra", {}).get("initial_state_agent_prompt", "").lower() is False
    # More specific checks:
    assert "agents" not in payload["world"], "World dump should not contain agents list"
    assert "cult leader" not in payload_json, "Other agent's private prompt must not appear"
    assert "order_of_threshold" not in payload_json, "Other agent's faction metadata must not appear"
    assert "secret research" not in payload_json, "Other agent's activity must not appear"


@pytest.mark.asyncio
async def test_context_json_excludes_scenario_metadata():
    """to_payload() should not contain scenario-level metadata (agent_prompts etc)."""
    world = _make_world_state()
    profile = _make_profile("juno", "Juno Park", "food_vendor")
    perception = _make_perception("juno", "market")

    ctx = await build_prompt_context(
        agent_profile=profile,
        perception=perception,
        world_state=world,
        scratchpad_state={},
        plan_state={},
        memories=[],
        extra={"initial_state_agent_prompt": "You run a noodle stand."},
    )

    payload = ctx.to_payload()
    payload_json = json.dumps(payload)

    assert "metadata" not in payload["world"], "World-level metadata must be stripped"
    assert "Recruit carefully" not in payload_json, "Sera's private prompt must not appear in Juno's context"


@pytest.mark.asyncio
async def test_context_json_excludes_llm_credentials():
    """to_payload() should not include LLM provider/model in extra."""
    world = _make_world_state()
    profile = _make_profile("juno", "Juno Park", "food_vendor")
    perception = _make_perception("juno", "market")

    ctx = await build_prompt_context(
        agent_profile=profile,
        perception=perception,
        world_state=world,
        scratchpad_state={},
        plan_state={},
        memories=[],
        extra={
            "initial_state_agent_prompt": "You run a noodle stand.",
            "llm_provider": "openai",
            "llm_model": "gpt-5-mini",
            "prompt_library": "<some object>",
        },
    )

    payload = ctx.to_payload()
    extra = payload.get("extra", {})

    assert "llm_provider" not in extra, "LLM provider must not appear in serialized extra"
    assert "llm_model" not in extra, "LLM model must not appear in serialized extra"
    assert "prompt_library" not in extra, "Prompt library must not appear in serialized extra"


@pytest.mark.asyncio
async def test_agent_sees_own_prompt_only():
    """Agent's extra should contain their own prompt, not other agents'."""
    world = _make_world_state()
    profile = _make_profile("juno", "Juno Park", "food_vendor")
    perception = _make_perception("juno", "market")

    ctx = await build_prompt_context(
        agent_profile=profile,
        perception=perception,
        world_state=world,
        scratchpad_state={},
        plan_state={},
        memories=[],
        extra={"initial_state_agent_prompt": "You run a noodle stand."},
    )

    payload = ctx.to_payload()
    agent_prompt = payload["extra"].get("initial_state_agent_prompt", "")

    assert "noodle stand" in agent_prompt, "Agent should see their own prompt"
    assert "cult" not in agent_prompt.lower(), "Agent must not see another agent's prompt"
