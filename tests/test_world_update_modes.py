from datetime import datetime, timezone

import pytest

from miniverse import Orchestrator
from miniverse.cognition import AgentCognition
from miniverse.cognition.executor import Executor
from miniverse.schemas import (
    AgentAction,
    AgentProfile,
    AgentStatus,
    EnvironmentState,
    ResourceState,
    WorldState,
)
from miniverse.simulation_rules import SimulationRules


class RulesWithDeterministicProcessor(SimulationRules):
    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)
        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True

    # Deterministic world update hook
    def process_actions(
        self, state: WorldState, actions: list[AgentAction], tick: int
    ) -> WorldState:
        updated = state.model_copy(deep=True)
        # Mark via metadata to assert branch was taken
        updated.metadata["processed_by"] = "rules"
        # Mirror activity
        for a in updated.agents:
            act = next((x for x in actions if x.agent_id == a.agent_id), None)
            a.activity = act.action_type if act else a.activity
        return updated


class DummyExec(Executor):
    async def choose_action(
        self,
        agent_id,
        perception,
        scratchpad,
        *,
        plan=None,
        plan_step=None,
        context=None,
    ):
        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type="work",
            target=None,
            reasoning="deterministic test",
            communication=None,
        )


@pytest.mark.asyncio
async def test_deterministic_world_update_branch_taken():
    world = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={}),
        agents=[AgentStatus(agent_id="a", display_name="A")],
        metadata={},
    )
    agents = {
        "a": AgentProfile(
            agent_id="a",
            name="A",
            age=18,
            background="",
            role="worker",
            personality="",
            skills={},
            goals=[],
            relationships={},
        )
    }

    orch = Orchestrator(
        world_state=world,
        agents=agents,
        world_prompt="",
        agent_prompts={"a": ""},
        simulation_rules=RulesWithDeterministicProcessor(),
        agent_cognition={"a": AgentCognition(executor=DummyExec())},
        world_update_mode="auto",
    )

    result = await orch.run(num_ticks=1)
    assert result["final_state"].metadata.get("processed_by") == "rules"
