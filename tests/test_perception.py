"""Tests for partial perception construction."""

from datetime import datetime

from miniverse.environment import EnvironmentGridState, GridTileState
from miniverse.perception import build_agent_perception
from miniverse.schemas import (
    AgentAction,
    AgentPerception,
    AgentStatus,
    EnvironmentState,
    ResourceState,
    Stat,
    WorldEvent,
    WorldState,
)


def make_world_state() -> WorldState:
    environment = EnvironmentState(
        metrics={
            "temperature": Stat(value=-12, unit="°C", label="Ambient Temperature"),
            "wind_speed": Stat(value=22, unit="km/h", label="Wind Speed"),
        }
    )
    resources = ResourceState(
        metrics={
            "power": Stat(value=140.0, unit="kWh", label="Stored Power"),
            "water": Stat(value=460.0, unit="L", label="Water Reserve"),
        }
    )
    agents = [
        AgentStatus(
            agent_id="alpha",
            location="operations",
            activity="monitor",
            attributes={
                "health": Stat(value=92, unit="%", label="Health"),
                "stress": Stat(value=28, unit="%", label="Stress"),
                "energy": Stat(value=76, unit="%", label="Energy"),
            },
        ),
        AgentStatus(
            agent_id="beta",
            location="lab",
            activity="research",
            attributes={
                "health": Stat(value=88, unit="%", label="Health"),
                "stress": Stat(value=34, unit="%", label="Stress"),
                "energy": Stat(value=82, unit="%", label="Energy"),
            },
        ),
    ]

    events = [
        WorldEvent(
            event_id="evt-1",
            tick=3,
            category="alert",
            description="External wind gust exceeding threshold",
            severity=6,
        ),
        WorldEvent(
            event_id="evt-2",
            tick=2,
            category="info",
            description="Routine power check complete",
            severity=2,
        ),
    ]

    return WorldState(
        tick=3,
        timestamp=datetime(2160, 3, 21, 10, 30, 0),
        environment=environment,
        resources=resources,
        agents=agents,
        recent_events=events,
    )


def test_build_agent_perception_structure():
    world_state = make_world_state()
    recent_messages = [{"from": "beta", "message": "Expect gusts over 25 km/h."}]

    perception = build_agent_perception(
        agent_id="alpha",
        world_state=world_state,
        recent_messages=recent_messages,
        recent_memories=["Calibrated sensors at tick 2"],
    )

    assert isinstance(perception, AgentPerception)
    assert "health" in perception.personal_attributes
    assert "power" in perception.visible_resources
    assert "temperature" in perception.environment_snapshot
    assert perception.system_alerts == ["External wind gust exceeding threshold"]
    assert perception.messages == [
        {"from": "beta", "message": "Expect gusts over 25 km/h."}
    ]


def test_build_agent_perception_includes_grid_visibility():
    world_state = make_world_state()

    # Attach Tier 2 grid to world state
    grid = EnvironmentGridState(
        width=5,
        height=5,
        tiles={
            (2, 2): GridTileState(game_object="snake_head", collision=True),
            (3, 2): GridTileState(game_object="food", collision=False),
            (1, 1): GridTileState(game_object="wall", collision=True),
        },
    )
    world_state.environment_grid = grid
    world_state.metadata["grid_visibility_radius"] = 1

    # Configure agent grid position + override radius via metadata
    agent_status = next(a for a in world_state.agents if a.agent_id == "alpha")
    agent_status.grid_position = [2, 2]
    agent_status.metadata["grid_visibility_radius"] = 2

    perception = build_agent_perception(
        agent_id="alpha",
        world_state=world_state,
        recent_messages=[],
        recent_memories=[],
    )

    assert perception.grid_position == [2, 2]
    assert perception.grid_visibility is not None
    assert (
        perception.grid_visibility.radius == 2
    )  # agent metadata overrides world metadata
    assert len(perception.grid_visibility.tiles) == 25  # (2*radius+1)^2
    tiles = {
        tuple(tile.position): tile.tile for tile in perception.grid_visibility.tiles
    }
    assert tiles[(3, 2)].game_object == "food"
    assert tiles[(1, 1)].collision is True
    assert tiles[(2, 2)].game_object == "snake_head"
    assert perception.recent_observations
    grid_line = perception.recent_observations[0]
    assert grid_line.startswith("GRID ASCII:")
    assert "●" in grid_line
