"""Tests for truthful logging tags ([LLM] vs [•]) in orchestrator output.

These tests assert that:
- Executor step prints [LLM] only when an LLM-backed executor is used
- World update step prints [LLM] only when the LLM branch is taken
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from datetime import datetime, timezone

import pytest

from miniverse.cognition import AgentCognition, DefaultRuleBasedExecutor
from miniverse.cognition.llm import LLMExecutor
from miniverse.orchestrator import Orchestrator
from miniverse.schemas import (
    AgentAction,
    AgentProfile,
    AgentStatus,
    EnvironmentState,
    ResourceState,
    Stat,
    WorldState,
)
from miniverse.simulation_rules import SimulationRules


class RulesNoProcessor(SimulationRules):
    """Deterministic rules that do not override process_actions."""

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)
        updated.tick = tick
        return updated

    def validate_action(
        self, action: AgentAction, state: WorldState
    ) -> bool:  # pragma: no cover - trivial
        return True


class RulesWithProcessor(SimulationRules):
    """Rules that provide deterministic action processing via process_actions."""

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)
        updated.tick = tick
        return updated

    def validate_action(
        self, action: AgentAction, state: WorldState
    ) -> bool:  # pragma: no cover - trivial
        return True

    def process_actions(
        self, state: WorldState, actions: list[AgentAction], tick: int
    ) -> WorldState:
        new_state = state.model_copy(deep=True)
        new_state.tick = tick
        # Mark agent activity according to the action taken
        for action in actions:
            for ag in new_state.agents:
                if ag.agent_id == action.agent_id:
                    ag.activity = action.action_type
        return new_state


def _world_state_single_agent() -> tuple[WorldState, AgentProfile]:
    world_state = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={"power_kwh": Stat(value=100.0, unit="kWh")}),
        agents=[
            AgentStatus(
                agent_id="alpha",
                display_name="Alpha",
                role="worker",
                location=None,
                activity=None,
                attributes={"energy": Stat(value=80, unit="%")},
            )
        ],
    )
    profile = AgentProfile(
        agent_id="alpha",
        name="Alpha",
        age=30,
        background="",
        role="worker",
        personality="",
        skills={},
        goals=[],
        relationships={},
    )
    return world_state, profile


@pytest.mark.asyncio
async def test_executor_tag_deterministic(monkeypatch):
    world_state, profile = _world_state_single_agent()
    cognition = AgentCognition(executor=DefaultRuleBasedExecutor())
    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="",
        agent_prompts={"alpha": ""},
        simulation_rules=RulesWithProcessor(),
        agent_cognition={"alpha": cognition},
        world_update_mode="deterministic",
    )

    # Capture output
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await orchestrator.run(num_ticks=1)
    out = buf.getvalue()

    assert "[•] [Alpha] Choosing action via executor..." in out
    assert "[LLM] [Alpha] Choosing action via executor..." not in out


@pytest.mark.asyncio
async def test_executor_tag_llm(monkeypatch):
    world_state, profile = _world_state_single_agent()

    # Mock LLM action to avoid network
    async def fake_action(*args, **kwargs):
        return AgentAction(
            agent_id="alpha",
            tick=1,
            action_type="work",
            target=None,
            parameters={},
            reasoning="mock",
            communication=None,
        )

    # Patch LLM call to avoid network and return a deterministic action
    async def fake_call_llm_with_retries(**kwargs):
        return await fake_action()

    monkeypatch.setattr(
        "miniverse.cognition.llm.call_llm_with_retries", fake_call_llm_with_retries
    )

    cognition = AgentCognition(executor=LLMExecutor(template_name="default"))
    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="",
        agent_prompts={"alpha": ""},
        simulation_rules=RulesWithProcessor(),
        agent_cognition={"alpha": cognition},
        llm_provider="openai",
        llm_model="gpt-5-nano",
        world_update_mode="deterministic",  # keep world deterministic to isolate executor
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await orchestrator.run(num_ticks=1)
    out = buf.getvalue()

    assert "[LLM] [Alpha] Choosing action via executor..." in out


@pytest.mark.asyncio
async def test_world_update_tag_modes(monkeypatch):
    world_state, profile = _world_state_single_agent()

    # Deterministic with rules.process_actions
    cognition = AgentCognition(executor=DefaultRuleBasedExecutor())
    orch_det = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="",
        agent_prompts={"alpha": ""},
        simulation_rules=RulesWithProcessor(),
        agent_cognition={"alpha": cognition},
        world_update_mode="deterministic",
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await orch_det.run(num_ticks=1)
    assert "[•] [World Engine] Processing" in buf.getvalue()

    # LLM mode forces [LLM]
    async def fake_world_update(*args, **kwargs):
        state = args[0]
        return state.model_copy(update={"tick": kwargs.get("tick", 1)})

    monkeypatch.setattr(
        "miniverse.orchestrator.process_world_update", fake_world_update
    )

    orch_llm = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="",
        agent_prompts={"alpha": ""},
        simulation_rules=RulesNoProcessor(),
        agent_cognition={"alpha": cognition},
        llm_provider="openai",
        llm_model="gpt-5-nano",
        world_update_mode="llm",
    )
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        await orch_llm.run(num_ticks=1)
    assert "[LLM] [World Engine] Processing" in buf2.getvalue()

    # Auto mode with rules.process_actions uses deterministic tag
    orch_auto_rules = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="",
        agent_prompts={"alpha": ""},
        simulation_rules=RulesWithProcessor(),
        agent_cognition={"alpha": cognition},
        world_update_mode="auto",
    )
    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        await orch_auto_rules.run(num_ticks=1)
    assert "[•] [World Engine] Processing" in buf3.getvalue()

    # Auto mode without rules processor and with LLM configured uses [LLM]
    orch_auto_llm = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="",
        agent_prompts={"alpha": ""},
        simulation_rules=RulesNoProcessor(),
        agent_cognition={"alpha": cognition},
        llm_provider="openai",
        llm_model="gpt-5-nano",
        world_update_mode="auto",
    )
    buf4 = io.StringIO()
    with contextlib.redirect_stdout(buf4):
        await orch_auto_llm.run(num_ticks=1)
    assert "[LLM] [World Engine] Processing" in buf4.getvalue()
