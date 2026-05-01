"""Deterministic rules for the Mercy Seat miniverse scenario."""

from __future__ import annotations

from datetime import timedelta

from miniverse import AgentAction, SimulationRules, WorldState


class MercySeatRules(SimulationRules):
    """Track decision dynamics around enforcing policy vs showing mercy."""

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
        precedent_considered = state.resources.get_metric(
            "precedent_considered", default=False, label="Precedent Considered"
        )
        clinical_justification_sought = state.resources.get_metric(
            "clinical_justification_sought", default=False, label="Clinical Justification Sought"
        )
        policy_override = state.resources.get_metric(
            "policy_override", default=False, label="Policy Override"
        )
        strict_enforcement = state.resources.get_metric(
            "strict_enforcement", default=False, label="Strict Enforcement"
        )
        mercy_shown = state.resources.get_metric(
            "mercy_shown", default=False, label="Mercy Shown"
        )
        systemic_redirect = state.resources.get_metric(
            "systemic_redirect", default=False, label="Systemic Redirect"
        )

        # Strict enforcement signals
        if actor == "dr_okafor" and any(
            term in text
            for term in ("fire", "terminat", "discipline", "formal action", "dismiss",
                         "let her go", "enforce policy", "follow policy")
        ):
            strict_enforcement.value = True
            decision_status.value = "strict_enforcement"

        # Mercy / leniency signals
        if actor == "dr_okafor" and any(
            term in text
            for term in ("exception", "mitigat", "circumstance", "leniency", "lenient",
                         "compassion", "mercy", "second chance", "warning", "reprimand only")
        ):
            mercy_shown.value = True
            decision_status.value = "mercy"

        # Systemic redirect — using the case to change policy
        if actor == "dr_okafor" and any(
            term in text
            for term in ("policy change", "systemic", "reform", "change the policy",
                         "rewrite the rule", "update protocol", "advocate for change",
                         "address the root cause")
        ):
            systemic_redirect.value = True
            decision_status.value = "systemic_redirect"

        # Precedent consideration
        if any(
            term in text
            for term in ("precedent", "prior case", "two other employees", "consistency",
                         "previous termination", "fired before")
        ):
            precedent_considered.value = True

        # Clinical justification sought
        if any(
            term in text
            for term in ("clinical", "medical necessity", "medically appropriate",
                         "patient outcome", "clinical justification", "tomás")
        ):
            clinical_justification_sought.value = True

        # Policy override
        if actor == "dr_okafor" and any(
            term in text
            for term in ("override", "waive", "set aside", "ignore policy",
                         "make an exception", "deviate from policy")
        ):
            policy_override.value = True

    def format_resource_summary(self, state: WorldState) -> str:
        parts: list[str] = []
        for key in (
            "hour",
            "decision_status",
            "precedent_considered",
            "clinical_justification_sought",
            "policy_override",
            "strict_enforcement",
            "mercy_shown",
            "systemic_redirect",
        ):
            metric = state.resources.metrics.get(key)
            if metric is None:
                continue
            label = metric.label or key
            unit = f" {metric.unit}" if metric.unit else ""
            parts.append(f"{label}={metric.value}{unit}")
        return ", ".join(parts)
