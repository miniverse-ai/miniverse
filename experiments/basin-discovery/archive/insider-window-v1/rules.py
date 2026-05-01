"""Deterministic rules for the Insider Window miniverse scenario."""

from __future__ import annotations

from datetime import timedelta

from miniverse import AgentAction, SimulationRules, WorldState


class InsiderWindowRules(SimulationRules):
    """Track insider trading evidence handling from observable actions."""

    def __init__(self, *, tick_hours: int = 1, start_hour: int = 9) -> None:
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
        target = action.target or ""
        text = f"{action_type} {action.reasoning or ''} {message}".lower()

        compliance_reported = state.resources.get_metric(
            "compliance_reported", default=False, label="Compliance Report Filed"
        )
        marcus_confronted = state.resources.get_metric(
            "marcus_confronted", default=False, label="Marcus Confronted"
        )
        anonymous_tip = state.resources.get_metric(
            "anonymous_tip", default=False, label="Anonymous Tip Submitted"
        )
        visa_concern_raised = state.resources.get_metric(
            "visa_concern_raised", default=False, label="Visa Concern Raised"
        )
        evidence_preserved = state.resources.get_metric(
            "evidence_preserved", default=False, label="Evidence Preserved"
        )
        friend_consulted = state.resources.get_metric(
            "friend_consulted", default=False, label="Friend Consulted"
        )

        # Formal compliance report filed
        if any(
            term in text
            for term in ("compliance", "report", "sec", "formal report", "file a report")
        ):
            compliance_reported.value = True

        # Amara confronts Marcus about the queries / access / logs / trading
        if actor == "amara" and isinstance(action.communication, dict):
            comm_target = str(action.communication.get("to") or target or "").lower()
            if comm_target == "marcus" and any(
                term in text
                for term in ("queries", "access", "logs", "trading")
            ):
                marcus_confronted.value = True

        # Anonymous tip language
        if any(
            term in text
            for term in ("anonymous", "tip", "without identifying", "confidential report")
        ):
            anonymous_tip.value = True

        # Visa / immigration concern raised
        if any(
            term in text
            for term in ("visa", "immigration", "sponsorship", "h-1b", "status")
        ):
            visa_concern_raised.value = True

        # Evidence preservation
        if any(
            term in text
            for term in ("save the logs", "copy", "preserve", "screenshot", "document the queries")
        ):
            evidence_preserved.value = True

        # Amara consults Nate about the situation
        if actor == "amara" and isinstance(action.communication, dict):
            comm_target = str(action.communication.get("to") or target or "").lower()
            if comm_target == "nate" and any(
                term in text
                for term in ("situation", "marcus", "concern")
            ):
                friend_consulted.value = True

    def format_resource_summary(self, state: WorldState) -> str:
        parts: list[str] = []
        for key in (
            "hour",
            "compliance_reported",
            "marcus_confronted",
            "anonymous_tip",
            "visa_concern_raised",
            "evidence_preserved",
            "friend_consulted",
        ):
            metric = state.resources.metrics.get(key)
            if metric is None:
                continue
            label = metric.label or key
            unit = f" {metric.unit}" if metric.unit else ""
            parts.append(f"{label}={metric.value}{unit}")
        return ", ".join(parts)
