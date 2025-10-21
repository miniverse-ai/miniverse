"""Utilities for configuring cognition scheduling cadences.

These helpers allow scenarios to throttle how often planners and
reflection engines execute without having to duplicate bookkeeping in
every example.  The orchestrator stores the last run tick in each
agent's scratchpad and consults the cadence prior to invoking the
planner/reflection modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .planner import Plan

DEFAULT_OFFSET = 1
"""Default tick offset so cadence aligns with tick=1 runs."""


@dataclass(frozen=True)
class TickInterval:
    """Represents an ``every N ticks`` cadence with an optional offset."""

    every: int = 1
    offset: int = DEFAULT_OFFSET

    def is_due(self, *, tick: int, last_run_tick: Optional[int]) -> bool:
        """Return ``True`` when the cadence fires on this tick."""

        if self.every <= 0:
            return True

        if last_run_tick is None:
            return ((tick - self.offset) % self.every) == 0

        if tick <= last_run_tick:
            return False

        return ((tick - self.offset) % self.every) == 0


@dataclass(frozen=True)
class PlannerCadence:
    """Configuration knobs for planner execution frequency."""

    interval: TickInterval = field(default_factory=TickInterval)
    run_when_empty: bool = True

    def should_generate(
        self,
        *,
        tick: int,
        last_run_tick: Optional[int],
        current_plan: Optional[Plan],
    ) -> bool:
        """Return ``True`` when the planner should refresh the plan."""

        if self.run_when_empty and (
            not isinstance(current_plan, Plan) or not current_plan.steps
        ):
            return True

        return self.interval.is_due(tick=tick, last_run_tick=last_run_tick)


@dataclass(frozen=True)
class ReflectionCadence:
    """Configuration knobs for reflection execution frequency."""

    interval: TickInterval = field(default_factory=TickInterval)
    require_new_memories: bool = False

    def should_reflect(
        self,
        *,
        tick: int,
        last_run_tick: Optional[int],
        new_memories: int,
    ) -> bool:
        """Return ``True`` when the reflection engine should run."""

        if self.require_new_memories and new_memories <= 0:
            return False

        return self.interval.is_due(tick=tick, last_run_tick=last_run_tick)


@dataclass(frozen=True)
class CognitionCadence:
    """Bundle for planner/reflection cadence configuration."""

    planner: PlannerCadence = field(default_factory=PlannerCadence)
    reflection: ReflectionCadence = field(default_factory=ReflectionCadence)


# Scratchpad keys used by the orchestrator to cache cadence metadata.
PLANNER_LAST_TICK_KEY = "__planner_last_tick"
REFLECTION_LAST_TICK_KEY = "__reflection_last_tick"


def tick_to_time_block(
    *, tick: int, ticks_per_block: int, block_label: str = "day"
) -> dict[str, int]:
    """Translate a tick index into higher-level time blocks.

    Parameters
    ----------
    tick:
        1-based tick value coming from the orchestrator.
    ticks_per_block:
        Number of ticks that constitute one logical block ("day",
        "shift", etc.). Must be ``>= 1``.
    block_label:
        Name to include in the returned mapping.

    Returns
    -------
    dict
        ``{"block": int, "offset": int, "label": str}`` where
        ``block`` counts completed blocks (0-based) and ``offset`` is the
        tick position within the current block (1-based).
    """

    if ticks_per_block <= 0:
        raise ValueError("ticks_per_block must be >= 1")

    block_index = (tick - 1) // ticks_per_block
    offset = ((tick - 1) % ticks_per_block) + 1
    return {"label": block_label, "block": block_index, "offset": offset}
