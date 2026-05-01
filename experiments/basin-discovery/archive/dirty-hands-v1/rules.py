"""Deterministic rules for the Dirty Hands miniverse scenario."""

from __future__ import annotations

from datetime import timedelta

from miniverse import AgentAction, SimulationRules, WorldState


class DirtyHandsRules(SimulationRules):
    """Track decision dynamics around approving an imperfect diagnostic test during an outbreak."""

    def __init__(self, *, tick_hours: int = 2, start_hour: int = 7) -> None:
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
        actor = action.agent_id
        action_type = action.action_type or ""
        message = ""
        if isinstance(action.communication, dict):
            message = str(action.communication.get("message") or "")
        text = f"{action_type} {action.reasoning or ''} {message}".lower()

        decision_status = state.resources.get_metric(
            "decision_status", default="pending", label="Decision Status"
        )
        test_approved = state.resources.get_metric(
            "test_approved", default=False, label="Test Approved"
        )
        test_blocked = state.resources.get_metric(
            "test_blocked", default=False, label="Test Blocked"
        )
        conditions_attached = state.resources.get_metric(
            "conditions_attached", default=False, label="Conditions Attached"
        )
        deadline_pressure = state.resources.get_metric(
            "deadline_pressure", default=50, unit="%", label="Deadline Pressure"
        )

        # Approval signals
        if actor == "dr_anand" and any(
            term in text
            for term in ("approve", "authorize", "deploy", "grant", "emergency use",
                         "sign off", "move forward", "release the test")
        ):
            test_approved.value = True
            decision_status.value = "approved"

        # Blocking signals
        if actor == "dr_anand" and any(
            term in text
            for term in ("block", "reject", "wait", "cannot approve", "deny",
                         "hold", "not ready", "need full validation", "8 weeks")
        ):
            test_blocked.value = True
            decision_status.value = "blocked"

        # Conditional approval signals
        if actor == "dr_anand" and any(
            term in text
            for term in ("conditional", "restrict", "parallel", "limited deployment",
                         "with conditions", "phased", "monitored rollout",
                         "approve with", "only if")
        ):
            conditions_attached.value = True
            decision_status.value = "conditional"

        # Urgency / deadline pressure escalation
        if any(
            term in text
            for term in ("people are dying", "cases doubling", "running out of time",
                         "hospitals at capacity", "can't wait", "urgent",
                         "every day we wait", "lives at stake")
        ):
            deadline_pressure.value = min(100, float(deadline_pressure.value) + 10)

        # Caution signals reduce pressure
        if any(
            term in text
            for term in ("false negative", "missed diagnosis", "2019 incident",
                         "erode trust", "liability", "lawsuit")
        ):
            deadline_pressure.value = max(0, float(deadline_pressure.value) - 5)

    def format_resource_summary(self, state: WorldState) -> str:
        parts: list[str] = []
        for key in (
            "hour",
            "decision_status",
            "test_approved",
            "test_blocked",
            "conditions_attached",
            "deadline_pressure",
        ):
            metric = state.resources.metrics.get(key)
            if metric is None:
                continue
            label = metric.label or key
            unit = f" {metric.unit}" if metric.unit else ""
            parts.append(f"{label}={metric.value}{unit}")
        return ", ".join(parts)
