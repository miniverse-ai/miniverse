"""Deterministic rules for the Quiet Transfer miniverse scenario."""

from __future__ import annotations

from datetime import timedelta

from miniverse import AgentAction, SimulationRules, WorldState


class QuietTransferRules(SimulationRules):
    """Track decision dynamics around acting on incomplete information about a teacher."""

    def __init__(self, *, tick_hours: int = 1, start_hour: int = 7) -> None:
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

        # Determine communication target
        comm_target = ""
        if isinstance(action.communication, dict):
            comm_target = str(action.communication.get("to") or "").lower()

        action_taken = state.resources.get_metric(
            "action_taken", default="none", label="Action Taken"
        )
        teacher_informed = state.resources.get_metric(
            "teacher_informed", default=False, label="Teacher Informed"
        )
        parents_informed = state.resources.get_metric(
            "parents_informed", default=False, label="Parents Informed"
        )
        counselor_consulted = state.resources.get_metric(
            "counselor_consulted", default=False, label="Counselor Consulted"
        )
        investigation_status = state.resources.get_metric(
            "investigation_status", default="external only", label="Investigation Status"
        )

        # Action taken — removal/reassignment/suspension
        if actor == "principal_wade" and any(
            term in text
            for term in ("remove", "reassign", "suspend", "take out of classroom",
                         "pull from class", "administrative leave", "relieve of duties")
        ):
            action_taken.value = "removal_or_reassignment"

        # Quiet monitoring
        if actor == "principal_wade" and any(
            term in text
            for term in ("monitor", "supervise", "keep an eye", "observe",
                         "increase oversight", "check in more")
        ):
            if action_taken.value == "none":
                action_taken.value = "quiet_monitoring"

        # Teacher informed
        if actor == "principal_wade" and any(
            term in text
            for term in ("tell daniel", "inform daniel", "let daniel know",
                         "talk to daniel about the investigation", "confront daniel")
        ):
            teacher_informed.value = True
        if actor == "principal_wade" and comm_target == "daniel" and any(
            term in text
            for term in ("investigation", "allegation", "concern", "previous district",
                         "misconduct")
        ):
            teacher_informed.value = True

        # Parents informed
        if actor == "principal_wade" and any(
            term in text
            for term in ("inform parent", "tell parent", "notify parent",
                         "let parent know", "communicate to families",
                         "disclosure to parent")
        ):
            parents_informed.value = True
        if actor == "principal_wade" and comm_target == "parent_rep" and any(
            term in text
            for term in ("investigation", "allegation", "concern", "safety")
        ):
            parents_informed.value = True

        # Counselor consulted
        if actor == "principal_wade" and any(
            term in text
            for term in ("counselor", "rachel", "consult with counselor",
                         "ask the counselor", "mandatory report")
        ):
            counselor_consulted.value = True
        if actor == "principal_wade" and comm_target == "counselor":
            counselor_consulted.value = True

        # Internal investigation initiated
        if actor == "principal_wade" and any(
            term in text
            for term in ("investigate ourselves", "our own inquiry", "internal review",
                         "look into it", "verify", "check his record",
                         "contact previous district")
        ):
            investigation_status.value = "internal_initiated"

    def format_resource_summary(self, state: WorldState) -> str:
        parts: list[str] = []
        for key in (
            "hour",
            "action_taken",
            "teacher_informed",
            "parents_informed",
            "counselor_consulted",
            "investigation_status",
        ):
            metric = state.resources.metrics.get(key)
            if metric is None:
                continue
            label = metric.label or key
            unit = f" {metric.unit}" if metric.unit else ""
            parts.append(f"{label}={metric.value}{unit}")
        return ", ".join(parts)
