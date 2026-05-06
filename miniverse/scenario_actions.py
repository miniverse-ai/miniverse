"""Protocol for scenario-specific action execution.

Each scenario can define an actions.py that handles tool execution
and returns results to the agent's context window. This module
provides the base class.

Drain queues
------------

Scenario actions can communicate with the orchestrator via three
optional queues. The orchestrator drains them after each action:

- ``pending_messages``: ``[{"to", "sender", "content"}]`` — queued
  notifications routed to recipient agents' inboxes.
- ``pending_memories``: ``[{"agent_id", "content", "memory_type",
  "importance", "tags", "metadata"}]`` — written into the run's
  semantic memory store. Used for dream summaries, reflections,
  scenario-emitted observations.
- ``context_resets``: ``{agent_id: new_system_prompt}`` — wipes
  the agent's context window and rebuilds it with the new prompt.
  Used at episode/day boundaries when accumulated history should
  be replaced with compressed memories + pinned artifacts.
- ``pending_context_markers``: ``[{"to", "content"}]`` — inserts
  marker lines into one or more context-window transcripts. Use
  ``to: "*"`` to mark every active context.
- ``pending_events``: ``[{"type", "agent_id", "content", ...}]`` —
  audit-only event log entries that are not injected into any agent
  context. Used for diagnostics such as failed background compression.

All three are populated by subclasses; the base class just
declares the attributes so the orchestrator can rely on them.

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

    Subclasses can populate the drain queues (``pending_messages``,
    ``pending_memories``, ``context_resets``) to send signals back
    to the orchestrator. See module docstring for shape of each.
    """

    def __init__(self) -> None:
        self.pending_messages: List[Dict[str, str]] = []
        self.pending_memories: List[Dict[str, Any]] = []
        self.context_resets: Dict[str, str] = {}
        self.pending_context_markers: List[Dict[str, str]] = []
        self.pending_events: List[Dict[str, Any]] = []
        # Optional orchestrator-provided context — set by bind_orchestrator_context.
        self.agent_profiles: Dict[str, Any] = {}
        self.agent_prompts: Dict[str, str] = {}
        self.agent_context_windows: Dict[str, Any] = {}
        self.llm_provider: Optional[str] = None
        self.llm_model: Optional[str] = None
        self.agent_llm_overrides: Dict[str, Dict[str, str]] = {}

    def bind_orchestrator_context(
        self,
        agent_profiles: Dict[str, Any],
        agent_prompts: Dict[str, str],
    ) -> None:
        """Receive references to agent profiles and persona prompts.

        Called once by the orchestrator during initialization. Scenarios
        that need to reconstruct system prompts (e.g., for context
        resets) can read from these dicts. Default behavior just stores
        the references; subclasses can override for additional setup.
        """
        self.agent_profiles = agent_profiles
        self.agent_prompts = agent_prompts

    def bind_agent_context_windows(self, agent_context_windows: Dict[str, Any]) -> None:
        """Receive live per-agent context-window references from the orchestrator.

        Scenarios with native episode boundaries can use these references to
        compress the same active prompt context an agent actually had before
        asking the orchestrator to reset that window.
        """
        self.agent_context_windows = agent_context_windows

    def bind_llm_config(
        self,
        llm_provider: Optional[str],
        llm_model: Optional[str],
        agent_llm_overrides: Dict[str, Dict[str, str]],
    ) -> None:
        """Receive the same LLM routing configuration the orchestrator uses."""
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.agent_llm_overrides = agent_llm_overrides

    def llm_config_for_agent(self, agent_id: str) -> tuple[Optional[str], Optional[str]]:
        override = self.agent_llm_overrides.get(agent_id, {})
        return (
            override.get("provider", self.llm_provider),
            override.get("model", self.llm_model),
        )

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

    async def on_builtin_action(
        self,
        action_type: str,
        agent_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Observe an orchestrator-handled action.

        Built-in actions such as ``check_inbox``, ``send_message``, and
        ``wait`` are handled outside scenario ``execute``. Scenarios with
        their own clocks or event schedulers can override this hook to keep
        scenario time in sync with built-in agent activity.
        """
        return None

    async def on_agent_response(
        self,
        agent_id: str,
        content: str,
        respond_to: Optional[str] = None,
        action_type: Optional[str] = None,
    ) -> bool:
        """Observe or handle an agent's natural-language response.

        Return True when the scenario handled delivery itself and the
        orchestrator should not route the response through the generic
        inbox/conversation layer.
        """
        return False

    def should_sleep_when_idle(self, agent_id: str) -> Optional[bool]:
        """Override profile sleep_when_idle for scenario-specific phases.

        Return None to use the agent profile. Return True/False to override
        whether the orchestrator should skip LLM calls while no new events
        are pending.
        """
        return None

    def is_agent_phase_complete(self, agent_id: str) -> bool:
        """Return whether an agent is done until the scenario advances phase.

        This is stronger than idle sleep: incidental world context should not
        wake the agent. Scenarios clear this condition when the next planning
        or market phase starts.
        """
        return False

    def get_available_actions(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the action catalog for this scenario.

        Each entry should have at minimum:
        - "name": action name
        - "description": human-readable description

        Parameters
        ----------
        agent_id : str, optional
            If provided, return only actions available to this agent.
            If None, return the full catalog (backward compatible).

        This is used to build the agent's system prompt so it
        knows what tools are available.
        """
        return []

    def get_builtin_actions(self, agent_id: Optional[str] = None) -> Optional[List[Dict[str, str]]]:
        """Override the default built-in action catalog for this scenario.

        Return None to use the orchestrator defaults. Return a list to expose
        only those built-ins in the agent-facing prompt.
        """
        return None

    def export_artifacts(self) -> Dict[str, Any]:
        """Return scenario-specific machine-readable run artifacts.

        The CLI saves this payload next to transcripts when context-window
        mode is used. Subclasses can override to expose final state needed
        for analysis without scraping transcript text.
        """
        return {}
