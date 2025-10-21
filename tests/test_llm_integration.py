"""Integration test that performs a real LLM call when configured.

This is intentionally minimal and skipped by default unless LLM env vars are set.
"""

import os

import pytest

from miniverse.llm_utils import call_llm_with_retries
from miniverse.schemas import AgentAction

REQUIRED_ENVS = ("LLM_PROVIDER", "LLM_MODEL")


pytestmark = pytest.mark.skipif(
    any(not os.getenv(v) for v in REQUIRED_ENVS),
    reason="LLM integration test skipped (missing LLM_PROVIDER/LLM_MODEL)",
)


@pytest.mark.asyncio
@pytest.mark.llm
async def test_real_llm_returns_agent_action():
    # Keep prompts small; ask the model to output exactly this JSON.
    system = (
        "You output ONLY valid JSON for an AgentAction. Do not add commentary. "
        "Echo the provided JSON exactly."
    )
    user = (
        '{"agent_id": "tester", "tick": 1, "action_type": "rest", '
        '"target": null, "parameters": null, "reasoning": "health recovery", "communication": null}'
    )

    action = await call_llm_with_retries(
        system_prompt=system,
        user_prompt=user,
        llm_provider=os.getenv("LLM_PROVIDER"),
        llm_model=os.getenv("LLM_MODEL"),
        response_model=AgentAction,
    )

    assert isinstance(action, AgentAction)
    assert action.agent_id == "tester"
    assert action.action_type == "rest"
