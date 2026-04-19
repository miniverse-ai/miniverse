"""Continuous world clock for async orchestration.

Replaces tick-based time with a continuous clock that agents and the world
state reference independently. Actions have durations — while one agent
has a 30-minute conversation, another completes a 2-hour containment check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class WorldClock:
    """Continuous simulation clock.

    Tracks simulated time independently of agent actions. Agents read the
    clock to know "what time is it" and the orchestrator advances it based
    on elapsed real time or a speed multiplier.

    Parameters
    ----------
    start_time: Simulation start time (e.g., 2026-09-17T08:00:00).
    speed: Time multiplier. 1.0 = real time, 60.0 = 1 real second = 1 sim minute.
    """

    start_time: datetime
    speed: float = 60.0
    _current: datetime = field(init=False)
    _real_start: float = field(init=False)

    def __post_init__(self) -> None:
        import time

        self._current = self.start_time
        self._real_start = time.monotonic()

    @property
    def now(self) -> datetime:
        return self._current

    def advance(self, duration: timedelta) -> datetime:
        """Advance the clock by a fixed duration. Returns new time."""
        self._current += duration
        return self._current

    def advance_minutes(self, minutes: float) -> datetime:
        """Convenience: advance by N minutes."""
        return self.advance(timedelta(minutes=minutes))

    def elapsed_since(self, timestamp: datetime) -> timedelta:
        """How much sim time has passed since a given timestamp."""
        return self._current - timestamp

    def time_str(self) -> str:
        """Human-readable current time."""
        return self._current.strftime("%Y-%m-%d %H:%M")

    def hour(self) -> int:
        return self._current.hour

    def is_work_hours(self) -> bool:
        """Convenience check for 8am-6pm."""
        return 8 <= self._current.hour < 18


# ---------------------------------------------------------------------------
# Action Durations
# ---------------------------------------------------------------------------

# Default durations in minutes for each action type.
# Scenarios can override these.
DEFAULT_ACTION_DURATIONS: dict[str, float] = {
    "talk": 3.0,        # One conversational exchange
    "message": 1.0,     # Send a private message
    "move_to": 5.0,     # Walk between adjacent locations
    "work": 30.0,       # Focused task
    "investigate": 20.0, # Research/review activity
    "do_nothing": 10.0,  # Idle / wait
    "rest": 15.0,        # Take a break
}


def get_action_duration(
    action_type: str,
    overrides: Optional[dict[str, float]] = None,
) -> timedelta:
    """Get the duration for an action type.

    Parameters
    ----------
    action_type: The action type string.
    overrides: Optional scenario-specific duration overrides.
    """
    durations = {**DEFAULT_ACTION_DURATIONS, **(overrides or {})}
    minutes = durations.get(action_type, 5.0)
    return timedelta(minutes=minutes)
