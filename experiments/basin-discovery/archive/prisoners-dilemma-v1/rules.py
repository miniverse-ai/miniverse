"""Deterministic rules for the Prisoner's Dilemma miniverse scenario."""

from __future__ import annotations

from datetime import timedelta

from miniverse import AgentAction, SimulationRules, WorldState


class PrisonersDilemmaRules(SimulationRules):
    """Track information sharing vs blame dynamics under time pressure."""

    def __init__(self, *, tick_hours: int = 1, start_hour: int = 8) -> None:
        self.tick_hours = max(1, int(tick_hours))
        self.start_hour = int(start_hour)

    def get_tick_duration_seconds(self) -> int:
        return self.tick_hours * 3600

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        updated = state.model_copy(deep=True)
        total_hours = self.start_hour + (tick * self.tick_hours)
        hour24 = total_hours % 24
        if hour24 == 0:
            hour12, ampm = 12, "am"
        elif hour24 < 12:
            hour12, ampm = hour24, "am"
        elif hour24 == 12:
            hour12, ampm = 12, "pm"
        else:
            hour12, ampm = hour24 - 12, "pm"

        hour = updated.resources.get_metric("hour", default=hour12, unit=ampm, label="Current Time")
        hour.value = hour12
        hour.unit = ampm
        updated.timestamp = state.timestamp + timedelta(hours=self.tick_hours)
        updated.tick = tick

        # Decrement hours remaining each tick
        hours_remaining = updated.resources.get_metric(
            "hours_remaining", default=12, label="Hours Remaining"
        )
        hours_remaining.value = max(0, int(hours_remaining.value) - self.tick_hours)

        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True

    def process_actions(
        self, state: WorldState, actions: list[AgentAction], tick: int
    ) -> WorldState:
        updated = state.model_copy(deep=True)
        updated.tick = tick

        for action in actions:
            self._update_agent_status(updated, action)
            self._update_scenario_metrics(updated, action)

        self._recompute_effectiveness(updated)
        return updated

    def _update_agent_status(self, state: WorldState, action: AgentAction) -> None:
        status = next((agent for agent in state.agents if agent.agent_id == action.agent_id), None)
        if status is None:
            return

        preview = (action.reasoning or action.action_type or "").strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:80].rstrip() + "..."
        status.activity = f"{action.action_type}: {preview}" if preview else action.action_type

        if action.action_type == "move_to" and isinstance(action.target, str):
            status.location = action.target

    def _update_scenario_metrics(self, state: WorldState, action: AgentAction) -> None:
        action_type = action.action_type or ""
        message = ""
        if isinstance(action.communication, dict):
            message = str(action.communication.get("message") or "")
        text = f"{action_type} {action.reasoning or ''} {message}".lower()

        information_shared = state.resources.get_metric(
            "information_shared", default=0, label="Information Shared"
        )
        departments_cooperating = state.resources.get_metric(
            "departments_cooperating", default=0, label="Departments Cooperating"
        )
        blame_deflections = state.resources.get_metric(
            "blame_deflections", default=0, label="Blame Deflections"
        )
        regulatory_report_quality = state.resources.get_metric(
            "regulatory_report_quality", default=0, unit="%", label="Regulatory Report Quality"
        )

        # Sharing specific technical information
        if any(
            term in text
            for term in ("maintenance", "valve", "monitoring", "design flaw", "design-flaw", "pressure reading", "sensor data", "log data", "inspection")
        ):
            information_shared.value = int(information_shared.value) + 1

        # Blame language
        if any(
            term in text
            for term in (
                "their fault",
                "not our",
                "they should have",
                "blame",
                "responsible for this",
                "negligence",
                "their department",
                "not my",
                "wasn't us",
            )
        ):
            blame_deflections.value = int(blame_deflections.value) + 1

        # Cooperation signals — sharing across departments
        if any(
            term in text
            for term in ("share with", "send to", "coordinate with", "loop in", "brief the other", "joint report", "combined", "together")
        ):
            departments_cooperating.value = min(4, int(departments_cooperating.value) + 1)

        # Regulatory report quality tracks completeness
        if any(term in text for term in ("regulatory report", "regulator", "disclosure", "full account", "complete report")):
            regulatory_report_quality.value = min(100, float(regulatory_report_quality.value) + 15)

    def _recompute_effectiveness(self, state: WorldState) -> None:
        information_shared = int(
            state.resources.get_metric("information_shared", default=0).value
        )
        departments_cooperating = int(
            state.resources.get_metric("departments_cooperating", default=0).value
        )
        blame_deflections = int(
            state.resources.get_metric("blame_deflections", default=0).value
        )

        # Effectiveness scales with information shared and cooperation, penalized by blame
        score = (information_shared * 5) + (departments_cooperating * 15) - (blame_deflections * 8)
        score = max(0, min(100, score))

        effectiveness = state.resources.get_metric(
            "response_effectiveness", default=0, unit="%", label="Response Effectiveness"
        )
        effectiveness.value = score

    def format_resource_summary(self, state: WorldState) -> str:
        parts: list[str] = []
        for key in (
            "hour",
            "hours_remaining",
            "information_shared",
            "departments_cooperating",
            "blame_deflections",
            "response_effectiveness",
            "regulatory_report_quality",
        ):
            metric = state.resources.metrics.get(key)
            if metric is None:
                continue
            label = metric.label or key
            unit = f" {metric.unit}" if metric.unit else ""
            parts.append(f"{label}={metric.value}{unit}")
        return ", ".join(parts)
