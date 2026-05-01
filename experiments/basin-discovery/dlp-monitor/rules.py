"""Simulation rules for DLP Monitoring Station scenario.

Lightweight — mostly time advancement and basic metric tracking.
The heavy lifting is in actions.py (tool execution).
"""

from __future__ import annotations

from datetime import timedelta

from miniverse import AgentAction, SimulationRules, WorldState


class DLPMonitorRules(SimulationRules):
    """Track DLP monitoring scenario state."""

    def __init__(self, *, tick_hours: int = 1, start_hour: int = 10) -> None:
        self.tick_hours = max(1, int(tick_hours))
        self.start_hour = int(start_hour)

    def get_tick_duration_seconds(self) -> int:
        return self.tick_hours * 3600

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)
        updated.timestamp = state.timestamp + timedelta(hours=self.tick_hours)
        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True

    def process_actions(
        self, state: WorldState, actions: list[AgentAction], tick: int
    ) -> WorldState:
        updated = state.model_copy(deep=True)
        updated.tick = tick
        return updated

    def format_resource_summary(self, state: WorldState) -> str:
        parts: list[str] = []
        for key, metric in state.resources.metrics.items():
            label = metric.label or key
            unit = f" {metric.unit}" if metric.unit else ""
            parts.append(f"{label}: {metric.value}{unit}")
        return ", ".join(parts)
