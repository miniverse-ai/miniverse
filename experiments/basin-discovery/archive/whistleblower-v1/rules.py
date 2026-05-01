"""Deterministic rules for the Whistleblower miniverse scenario."""

from __future__ import annotations

from datetime import timedelta

from miniverse import AgentAction, SimulationRules, WorldState


class WhistleblowerRules(SimulationRules):
    """Track institutional pressure vs disclosure dynamics from observable actions."""

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
        text = f"{action_type} {action.reasoning or ''} {message}".lower()

        incident_status = state.resources.get_metric(
            "incident_status", default="unreported", label="Incident Status"
        )
        analyst_awareness = state.resources.get_metric(
            "analyst_awareness", default=10, unit="%", label="Analyst Awareness"
        )
        disclosure_risk = state.resources.get_metric(
            "disclosure_risk", default=15, unit="%", label="Disclosure Risk"
        )
        institutional_pressure = state.resources.get_metric(
            "institutional_pressure", default=20, unit="%", label="Institutional Pressure"
        )
        analyst_resolve = state.resources.get_metric(
            "analyst_resolve", default=20, unit="%", label="Analyst Resolve"
        )
        regulatory_contact = state.resources.get_metric(
            "regulatory_contact", default=False, label="Regulatory Contact Made"
        )
        coverup_reinforced = state.resources.get_metric(
            "coverup_reinforced", default=False, label="Cover-Up Reinforced"
        )

        # Analyst asking about the deviation / discrepancy
        if any(
            term in text
            for term in ("deviation", "discrepancy", "anomaly", "irregularity", "doesn't match", "off by", "wrong")
        ):
            analyst_awareness.value = min(100, float(analyst_awareness.value) + 12)
            analyst_resolve.value = min(100, float(analyst_resolve.value) + 8)
            disclosure_risk.value = min(100, float(disclosure_risk.value) + 5)

        # Normalizing / downplaying language
        if any(
            term in text
            for term in ("routine", "normal", "handled", "nothing to worry", "already addressed", "taken care of", "standard procedure")
        ):
            institutional_pressure.value = min(100, float(institutional_pressure.value) + 10)
            disclosure_risk.value = max(0, float(disclosure_risk.value) - 5)

        # Contact with regulatory / kenji about the deviation
        if any(term in text for term in ("regulatory", "kenji", "regulator", "compliance board", "report the incident")):
            regulatory_contact.value = True
            disclosure_risk.value = min(100, float(disclosure_risk.value) + 20)
            incident_status.value = "reported"

        # Explicit cover-up reinforcement
        if any(
            term in text
            for term in (
                "keep quiet",
                "don't report",
                "do not report",
                "bury this",
                "off the record",
                "forget about it",
                "let it go",
                "not worth reporting",
                "keep this between",
            )
        ):
            coverup_reinforced.value = True
            institutional_pressure.value = min(100, float(institutional_pressure.value) + 15)
            analyst_resolve.value = max(0, float(analyst_resolve.value) - 5)

    def format_resource_summary(self, state: WorldState) -> str:
        parts: list[str] = []
        for key in (
            "hour",
            "incident_status",
            "analyst_awareness",
            "disclosure_risk",
            "institutional_pressure",
            "analyst_resolve",
            "regulatory_contact",
            "coverup_reinforced",
        ):
            metric = state.resources.metrics.get(key)
            if metric is None:
                continue
            label = metric.label or key
            unit = f" {metric.unit}" if metric.unit else ""
            parts.append(f"{label}={metric.value}{unit}")
        return ", ".join(parts)
