"""Deterministic simulation rules for the threshold demo scenario."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from miniverse import AgentAction, SimulationRules, WorldState


class ThresholdDistrictRules(SimulationRules):
    """Simple district-time physics for the Order of the Threshold scenario."""

    def __init__(
        self,
        *,
        tick_hours: int = 4,
        start_hour: int = 20,
        start_day: int = 14,
        month_label: str = "Jul",
    ) -> None:
        self.tick_hours = max(1, int(tick_hours))
        self.start_hour = int(start_hour)
        self.start_day = int(start_day)
        self.month_label = str(month_label)

    def get_tick_duration_seconds(self) -> int:
        return self.tick_hours * 3600

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        """Advance simulated local time and publish date/time resource metrics."""
        updated = state.model_copy(deep=True)

        total_hours = self.start_hour + (tick * self.tick_hours)
        day_offset = total_hours // 24
        hour24 = total_hours % 24

        if hour24 == 0:
            hour12, ampm = 12, "am"
        elif hour24 < 12:
            hour12, ampm = hour24, "am"
        elif hour24 == 12:
            hour12, ampm = 12, "pm"
        else:
            hour12, ampm = hour24 - 12, "pm"

        hour = updated.resources.get_metric(
            "hour",
            default=hour12,
            unit=ampm,
            label="Current Time",
        )
        hour.value = hour12
        hour.unit = ampm

        day = updated.resources.get_metric(
            "day",
            default=self.start_day,
            unit=self.month_label,
            label="Date",
        )
        day.value = self.start_day + day_offset
        day.unit = self.month_label

        updated.timestamp = state.timestamp + timedelta(hours=self.tick_hours)
        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True

    def process_actions(
        self, state: WorldState, actions: list[AgentAction], tick: int
    ) -> WorldState:
        """Apply deterministic action effects (location + activity string)."""
        updated = state.model_copy(deep=True)
        updated.tick = tick

        for action in actions:
            status = next(
                (agent for agent in updated.agents if agent.agent_id == action.agent_id),
                None,
            )
            if status is None:
                continue

            preview = (action.reasoning or "").strip().replace("\n", " ")
            if len(preview) > 64:
                preview = preview[:64].rstrip() + "..."
            status.activity = (
                f"{action.action_type}: {preview}"
                if preview
                else action.action_type
            )

            if action.action_type == "move_to" and isinstance(action.target, str):
                status.location = action.target

        return updated

    def format_resource_summary(self, state: WorldState) -> str:
        hour = state.resources.metrics.get("hour")
        day = state.resources.metrics.get("day")
        parts: list[str] = []
        if day is not None:
            parts.append(f"Date={int(day.value)} {day.unit or ''}".rstrip())
        if hour is not None:
            parts.append(f"Current Time={int(hour.value)} {hour.unit or ''}".rstrip())
        return ", ".join(parts)
