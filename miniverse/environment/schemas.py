"""Pydantic schemas for environment tiers.

These models mirror the lightweight dataclasses in ``graph.py`` and
``grid.py`` but ensure world state snapshots remain serializable. They
will evolve as we add helper utilities and loaders.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class LocationNodeState(BaseModel):
    """Represents a node in a logical environment graph (Tier 1)."""

    name: str
    capacity: Optional[int] = Field(
        None,
        description="Maximum occupancy; None means unbounded",
    )
    metadata: Dict[str, str] = Field(
        default_factory=dict,
        description="Free-form metadata (e.g., department, tags)",
    )


class EnvironmentGraphState(BaseModel):
    """Adjacency map between logical locations."""

    nodes: Dict[str, LocationNodeState] = Field(
        default_factory=dict,
        description="Map of node_id → node definition",
    )
    adjacency: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Map of node_id → list of adjacent node_ids",
    )


class GridTileState(BaseModel):
    """Encodes metadata about a grid tile for Tier 2 environments."""

    world: Optional[str] = None
    sector: Optional[str] = None
    arena: Optional[str] = None
    game_object: Optional[str] = None
    collision: bool = False
    metadata: Dict[str, str] = Field(default_factory=dict)


class EnvironmentGridState(BaseModel):
    """Sparse representation of a 2D environment grid."""

    width: int
    height: int
    tiles: Dict[Tuple[int, int], GridTileState] = Field(
        default_factory=dict,
        description="Sparse map: (row, col) → tile metadata",
    )
