"""Tests for environment helper utilities."""

from miniverse.environment import (
    EnvironmentGraph,
    EnvironmentGrid,
    EnvironmentGridState,
    GraphOccupancy,
    GridTile,
    GridTileState,
    LocationNode,
    get_visible_tiles,
    grid_shortest_path,
    render_ascii_window,
    shortest_path,
    validate_graph_move,
    validate_grid_move,
)


def test_graph_shortest_path_and_occupancy():
    graph = EnvironmentGraph(
        nodes={
            "ops": LocationNode(name="Ops", capacity=2),
            "stock": LocationNode(name="Stock", capacity=1),
            "loading": LocationNode(name="Loading"),
        },
        adjacency={
            "ops": ["stock", "loading"],
            "stock": ["ops"],
            "loading": ["ops"],
        },
    )

    path = shortest_path(graph, "ops", "loading")
    assert path == ["ops", "loading"]

    occupancy = GraphOccupancy(graph)
    assert occupancy.enter("stock", "alpha") is True
    assert occupancy.enter("stock", "beta") is False  # capacity reached
    occupancy.leave("stock", "alpha")
    assert occupancy.enter("stock", "beta") is True


def test_grid_shortest_path():
    grid = EnvironmentGrid(
        width=4,
        height=4,
        tiles={
            (1, 1): GridTile(collision=True),
            (1, 2): GridTile(collision=True),
        },
    )

    path = grid_shortest_path(grid, (0, 0), (2, 3))
    assert path is not None
    assert path[0] == (0, 0)
    assert path[-1] == (2, 3)
    assert (1, 1) not in path and (1, 2) not in path


def test_validate_grid_move():
    # Create a 5x5 grid with wall at (2,2) blocking direct paths
    grid = EnvironmentGrid(
        width=5,
        height=5,
        tiles={
            (2, 2): GridTile(collision=True),  # wall in center
        },
    )

    # Valid move: adjacent cell, walkable
    assert validate_grid_move(grid, (1, 1), (1, 2)) is True

    # Invalid: target has collision
    assert validate_grid_move(grid, (1, 1), (2, 2)) is False

    # Invalid: out of bounds
    assert validate_grid_move(grid, (1, 1), (10, 10)) is False

    # Valid path exists going around obstacle
    assert validate_grid_move(grid, (1, 2), (3, 2)) is True

    # Invalid: max_distance constraint violated (path too long)
    # Path from (0,0) to (4,4) requires at least 8 steps
    assert validate_grid_move(grid, (0, 0), (4, 4), max_distance=4) is False
    # Same path allowed without distance constraint
    assert validate_grid_move(grid, (0, 0), (4, 4)) is True


def test_validate_graph_move():
    graph = EnvironmentGraph(
        nodes={
            "kitchen": LocationNode(name="Kitchen", capacity=2),
            "bedroom": LocationNode(name="Bedroom", capacity=1),
            "bathroom": LocationNode(name="Bathroom"),
        },
        adjacency={
            "kitchen": ["bedroom"],
            "bedroom": ["kitchen", "bathroom"],
            "bathroom": ["bedroom"],
        },
    )
    occupancy = GraphOccupancy(graph)

    # Valid: adjacent nodes, has capacity
    assert validate_graph_move(graph, occupancy, "kitchen", "bedroom", "alice") is True

    # Invalid: nodes not adjacent (kitchen -> bathroom requires going through bedroom)
    assert (
        validate_graph_move(graph, occupancy, "kitchen", "bathroom", "alice") is False
    )

    # Valid if we disable adjacency requirement
    assert (
        validate_graph_move(
            graph, occupancy, "kitchen", "bathroom", "alice", require_adjacent=False
        )
        is True
    )

    # Fill bedroom to capacity
    assert occupancy.enter("bedroom", "bob") is True

    # Invalid: bedroom at capacity
    assert validate_graph_move(graph, occupancy, "kitchen", "bedroom", "alice") is False

    # Valid: agent already in bedroom can "move" to bedroom (stay)
    assert validate_graph_move(graph, occupancy, "bedroom", "bedroom", "bob") is True


def test_get_visible_tiles_state_returns_window():
    grid_state = EnvironmentGridState(
        width=4,
        height=4,
        tiles={
            (0, 0): GridTileState(game_object="wall", collision=True),
            (1, 1): GridTileState(game_object="resource", collision=False),
        },
    )

    visible = get_visible_tiles(grid_state, (1, 1), radius=1)
    # radius 1 → 3x3 window inside bounds
    assert len(visible) == 9
    assert visible[(0, 0)].game_object == "wall"
    assert visible[(1, 1)].game_object == "resource"
    # Tiles not present in sparse state default to empty metadata
    assert visible[(2, 2)].game_object is None


def test_render_ascii_window_outputs_symbols():
    grid_state = EnvironmentGridState(
        width=5,
        height=5,
        tiles={
            (2, 2): GridTileState(game_object="snake_head", collision=True),
            (1, 2): GridTileState(game_object="snake_body", collision=True),
            (3, 3): GridTileState(game_object="food", collision=False),
            (0, 0): GridTileState(game_object="wall", collision=True),
        },
    )

    ascii_window = render_ascii_window(grid_state, (2, 2), radius=1)
    assert "●" in ascii_window
    assert "o" in ascii_window
    assert "★" in ascii_window
