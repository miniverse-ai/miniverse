"""Protocol for scenario-specific action execution.

Each scenario can define an actions.py that handles tool execution
and returns results to the agent's context window. This module
provides the base class.

Usage in a scenario::

    # experiments/my-scenario/actions.py
    from miniverse.scenario_actions import ScenarioActions
    from miniverse.schemas import ActionResult

    class MyActions(ScenarioActions):
        async def execute(self, action_type, target, parameters, agent_id):
            if action_type == "check_logs":
                return ActionResult(content="[log entries...]")
            return None

        def get_available_actions(self):
            return [{"name": "check_logs", "description": "Review access logs"}]
"""

from __future__ import annotations

from abc import ABC
from typing import Any, Dict, List, Optional

from .schemas import ActionResult


class ScenarioActions(ABC):
    """Base class for scenario-specific action execution.

    Processes agent actions and returns results that go into
    the agent's context window. Returns None for actions that
    aren't handled by the scenario (built-in actions like
    move_to, check_inbox are handled by the orchestrator).
    """

    async def execute(
        self,
        action_type: str,
        target: Optional[str],
        parameters: Optional[Dict[str, Any]],
        agent_id: str,
    ) -> Optional[ActionResult]:
        """Execute a scenario action and return the result.

        Parameters
        ----------
        action_type : str
            The action name (e.g., "check_logs", "review_alert").
        target : str, optional
            The target of the action (e.g., alert ID, log query).
        parameters : dict, optional
            Action-specific parameters.
        agent_id : str
            The agent performing the action.

        Returns
        -------
        ActionResult or None
            ActionResult with content for the agent's context window,
            or None if this action isn't handled by the scenario.
        """
        return None

    def get_available_actions(self) -> List[Dict[str, Any]]:
        """Return the action catalog for this scenario.

        Each entry should have at minimum:
        - "name": action name
        - "description": human-readable description

        This is used to build the agent's system prompt so it
        knows what tools are available.
        """
        return []
