"""Unit tests for the core schema building blocks."""

from datetime import datetime

from miniverse.schemas import (
    AgentStatus,
    EnvironmentState,
    ResourceState,
    Stat,
    WorldEvent,
    WorldState,
)


def test_stat_basic_fields():
    stat = Stat(value=72, unit="%", label="Battery", description="Charge level")
    assert stat.value == 72
    assert stat.unit == "%"
    assert stat.label == "Battery"
    assert stat.metadata == {}


def test_metrics_block_get_metric_creates_stat():
    env = EnvironmentState()

    metric = env.get_metric("temperature", default=-18, unit="°C", label="Ambient Temp")
    assert metric.value == -18
    assert env.metrics["temperature"].label == "Ambient Temp"

    # Subsequent access should return the same instance.
    metric_again = env.get_metric("temperature")
    assert metric_again is metric


def test_agent_status_attribute_helper():
    agent = AgentStatus(agent_id="agent-1", location="operations")

    energy = agent.get_attribute("energy", default=80, unit="%", label="Energy")
    energy.value -= 5

    stress = agent.get_attribute("stress", default=30, unit="%", label="Stress")
    stress.value += 10

    assert agent.attributes["energy"].value == 75
    assert agent.attributes["stress"].value == 40


def test_world_state_construction():
    environment = EnvironmentState(
        metrics={"temp": Stat(value=-20, unit="°C", label="Outside")}
    )
    resources = ResourceState(
        metrics={"power": Stat(value=150.0, unit="kWh", label="Power")}
    )
    agent = AgentStatus(agent_id="agent-1", location="operations")

    world_state = WorldState(
        tick=0,
        timestamp=datetime(2160, 3, 21, 10, 0, 0),
        environment=environment,
        resources=resources,
        agents=[agent],
    )

    assert world_state.environment.metrics["temp"].value == -20
    assert world_state.resources.metrics["power"].unit == "kWh"
    assert world_state.agents[0].agent_id == "agent-1"


def test_world_event_generic_payload():
    event = WorldEvent(
        event_id="evt-1",
        tick=3,
        category="system",
        description="Power subsystem warning",
        severity=4,
        metrics={"power_drop": Stat(value=12.5, unit="kWh", label="Power Delta")},
    )

    assert event.metrics["power_drop"].value == 12.5
    assert event.category == "system"


def test_format_resource_summary_generic():
    resources = ResourceState(
        metrics={
            "power": Stat(value=120.5, unit="kWh", label="Power"),
            "water": Stat(value=520.0, unit="L", label="Water"),
        }
    )
    world_state = WorldState(
        tick=1,
        timestamp=datetime(2160, 3, 21, 11, 0, 0),
        environment=EnvironmentState(metrics={}),
        resources=resources,
        agents=[],
    )

    from miniverse.simulation_rules import format_resources_generic

    summary = format_resources_generic(world_state)
    assert "Power=120.5 kWh" in summary
    assert "Water=520.0 L" in summary
