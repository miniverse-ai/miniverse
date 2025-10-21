"""
Snake Game with LLM Agent

Simple snake game where an LLM decides which direction to move.
The snake sees an ASCII grid and chooses: up, down, left, or right.

Run: uv run python examples/snake/run.py
"""

import asyncio
import os
import random
from datetime import datetime, timezone
from typing import List, Tuple

from miniverse import (
    AgentProfile,
    AgentStatus,
    EnvironmentGridState,
    EnvironmentState,
    GridTileState,
    Orchestrator,
    ResourceState,
    SimulationRules,
    Stat,
    WorldState,
)
from miniverse.cognition import AgentCognition, LLMExecutor, PromptTemplate
from miniverse.memory import SimpleMemoryStream
from miniverse.persistence import InMemoryPersistence
from miniverse.schemas import AgentAction

# Smaller grid for better LLM comprehension
GRID_WIDTH = 12
GRID_HEIGHT = 12
VISIBILITY_RADIUS = 3

# Directions
DIRECTIONS = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}


SNAKE_EXECUTOR_TEMPLATE = PromptTemplate(
    name="snake_executor_ascii",
    system=(
        "You control Snake AI in a classic grid-based snake game. Use the ASCII board to decide a move. "
        'Return a valid AgentAction JSON with fields: agent_id ("snake"), tick ({{current_tick}}), '
        'action_type="move", target=null, parameters={"direction": <up|down|left|right>}, and reasoning as an empty string. '
        "Respond with JSON only."
    ),
    user=(
        "ASCII board:\n{{perception_text}}\n\n"
        "Directions available: up, down, left, right. Choose a safe move toward the food (★)."
    ),
)


class SnakeRules(SimulationRules):
    """Snake game physics - movement, collision, food spawning."""

    def __init__(self):
        self.snake_body: List[Tuple[int, int]] = [(6, 6)]  # Start in middle
        self.direction = "right"
        self.food_pos: Tuple[int, int] = self._spawn_food()
        self.score = 0
        self.game_over = False
        self.last_notice: str | None = None

    def _spawn_food(self) -> Tuple[int, int]:
        """Spawn food at random empty location."""
        while True:
            x = random.randint(1, GRID_WIDTH - 2)
            y = random.randint(1, GRID_HEIGHT - 2)
            if (x, y) not in self.snake_body:
                return (x, y)

    def _build_grid(self) -> EnvironmentGridState:
        """Build sparse grid representation with walls, snake, and food."""
        grid = EnvironmentGridState(width=GRID_WIDTH, height=GRID_HEIGHT)

        # Border walls
        for x in range(GRID_WIDTH):
            grid.tiles[(x, 0)] = GridTileState(game_object="wall", collision=True)
            grid.tiles[(x, GRID_HEIGHT - 1)] = GridTileState(
                game_object="wall", collision=True
            )
        for y in range(GRID_HEIGHT):
            grid.tiles[(0, y)] = GridTileState(game_object="wall", collision=True)
            grid.tiles[(GRID_WIDTH - 1, y)] = GridTileState(
                game_object="wall", collision=True
            )

        # Snake body (head first)
        for idx, (x, y) in enumerate(self.snake_body):
            obj = "snake_head" if idx == 0 else "snake_body"
            grid.tiles[(x, y)] = GridTileState(game_object=obj, collision=True)

        # Food
        fx, fy = self.food_pos
        grid.tiles[(fx, fy)] = GridTileState(game_object="food", collision=False)

        return grid

    def _evaluate_move(self, direction: str) -> Tuple[str, dict]:
        """Evaluate a prospective move and return status + metadata."""

        dx, dy = DIRECTIONS[direction]
        head_x, head_y = self.snake_body[0]
        new_pos = (head_x + dx, head_y + dy)

        status = "clear"
        reason = None

        # Off-grid = wall collision
        if (
            new_pos[0] <= 0
            or new_pos[0] >= GRID_WIDTH - 1
            or new_pos[1] <= 0
            or new_pos[1] >= GRID_HEIGHT - 1
        ):
            status = "blocked"
            reason = "wall"
        else:
            body_without_tail = self.snake_body[:-1] if len(self.snake_body) > 1 else []
            if new_pos in body_without_tail:
                status = "blocked"
                reason = "self"
            elif new_pos == self.food_pos:
                status = "food"

        payload = {
            "direction": direction,
            "status": status,
            "reason": reason,
            "position": list(new_pos),
        }

        return status, payload

    def _snapshot_state(
        self,
        state,
        *,
        head_position: Tuple[int, int] | None = None,
        status_message: str | None = None,
    ):
        """Clone world state and attach grid metadata for default perception."""

        self.last_notice = status_message

        updated = state.model_copy(deep=True)
        updated.environment_grid = self._build_grid()
        updated.metadata["grid_visibility_radius"] = VISIBILITY_RADIUS

        if updated.agents:
            head = list(head_position or self.snake_body[0])
            agent = updated.agents[0]
            agent.grid_position = head
            agent.metadata["grid_visibility_radius"] = VISIBILITY_RADIUS

        return updated

    def apply_tick(self, state, tick):
        """Update world state (snake movement happens in process_action)."""
        if self.game_over:
            return state

        updated = state.model_copy(deep=True)
        updated.tick = tick
        return updated

    def validate_action(self, action, state):
        """Validate movement action."""
        if action.action_type != "move":
            return True

        params = action.parameters or {}
        direction = params.get("direction")
        if direction not in DIRECTIONS:
            return False

        status, _ = self._evaluate_move(direction)
        return status != "blocked"

    def process_actions(self, state, actions, tick):
        """Process agent actions deterministically - move snake based on LLM decision."""
        visibility_radius = VISIBILITY_RADIUS

        if self.game_over or not actions:
            updated = self._snapshot_state(state)
            return updated

        # Process first action (only one snake)
        action = actions[0]
        if action.action_type != "move":
            updated = self._snapshot_state(state)
            return updated

        params = action.parameters or {}
        new_dir = params.get("direction")
        if new_dir not in DIRECTIONS:
            updated = self._snapshot_state(
                state, status_message="Invalid move command (missing direction)"
            )
            return updated

        status, move_payload = self._evaluate_move(new_dir)
        if status == "blocked":
            reason = move_payload.get("reason", "blocked")
            message = f"Cannot move {new_dir}: {reason}"
            self.game_over = True
            updated = self._snapshot_state(state, status_message=message)
            updated.resources.get_metric("game_status").value = "game_over"
            return updated

        self.direction = new_dir

        # Calculate new head position
        head_x, head_y = self.snake_body[0]
        dx, dy = DIRECTIONS[self.direction]
        new_head = (head_x + dx, head_y + dy)

        # Move snake
        self.snake_body.insert(0, new_head)

        # Check food collision
        if new_head == self.food_pos:
            self.score += 1
            self.food_pos = self._spawn_food()
            status_message = f"Ate food moving {new_dir}!"
        else:
            # Remove tail (snake doesn't grow unless eating)
            self.snake_body.pop()
            status_message = f"Moved {new_dir}"

        # Update world state with new snake position and grid snapshot
        updated = self._snapshot_state(
            state,
            head_position=new_head,
            status_message=status_message,
        )
        updated.resources.get_metric("score").value = self.score
        return updated

    def customize_perception(self, agent_id, perception, world_state):
        ascii_grid = render_grid(self)

        perception.recent_observations = [ascii_grid]
        # Ensure structured artifacts are removed so default template stays lean
        perception.grid_visibility = None
        if hasattr(perception, "grid_ascii"):
            try:
                delattr(perception, "grid_ascii")
            except AttributeError:
                perception.grid_ascii = None
        return perception

    def should_stop(self, state, tick):
        """Stop simulation once the deterministic rules mark the game as over."""

        if self.game_over:
            return True

        status = state.resources.metrics.get("game_status") if state.resources else None
        if status and getattr(status, "value", None) == "game_over":
            return True

        return False


def render_grid(rules: SnakeRules) -> str:
    """Render ASCII grid with clearer symbols (2x width for readability)."""
    lines = []

    # Top border (double width)
    lines.append("██" * GRID_WIDTH)

    # Grid rows (reversed so (0,0) is bottom-left)
    for y in range(GRID_HEIGHT - 1, -1, -1):
        row = ""
        for x in range(GRID_WIDTH):
            pos = (x, y)

            # Border walls (double width)
            if x == 0 or x == GRID_WIDTH - 1 or y == 0 or y == GRID_HEIGHT - 1:
                row += "██"
            # Snake head (with space)
            elif pos == rules.snake_body[0]:
                row += "● "
            # Snake body (with space)
            elif pos in rules.snake_body:
                row += "o "
            # Food (with space)
            elif pos == rules.food_pos:
                row += "★ "
            # Empty (double space)
            else:
                row += "  "

        lines.append(row)

    # Bottom border (double width)
    lines.append("██" * GRID_WIDTH)

    return "\n".join(lines)


async def main():
    # Initialize simulation
    rules = SnakeRules()

    # World state
    world_state = WorldState(
        tick=0,
        timestamp=datetime.now(timezone.utc),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(
            metrics={
                "score": Stat(value=0, unit="points", label="Score"),
                "game_status": Stat(value="playing", unit="", label="Status"),
            }
        ),
        agents=[
            AgentStatus(
                agent_id="snake",
                display_name="Snake AI",
                location=None,
                grid_position=list(rules.snake_body[0]),
                metadata={
                    "grid_visibility_radius": VISIBILITY_RADIUS,
                },
            )
        ],
        environment_grid=rules._build_grid(),
        metadata={
            "grid_visibility_radius": VISIBILITY_RADIUS,
        },
    )

    world_state = rules._snapshot_state(world_state)

    # Agent profile
    agents = {
        "snake": AgentProfile(
            agent_id="snake",
            name="Snake AI",
            role="player",
            background="I am a snake learning to play the game.",
            personality="Strategic, cautious, food-seeking",
            skills={"navigation": "Expert at spatial reasoning and pathfinding"},
            goals=[
                "Eat food (★)",
                "Avoid walls (█)",
                "Avoid my body (o)",
                "Get high score",
            ],
            relationships={},
        )
    }

    # Simple prompt - just show the grid and let LLM decide
    agent_prompts = {
        "snake": (
            "You are a snake in a grid world. Each perception provides the ASCII board of the arena as a single "
            "string (top row first, walls shown as █, snake head as ●, body as o, food as ★). Choose a safe "
            "direction (`up`, `down`, `left`, or `right`) that moves toward the food while avoiding walls and "
            "your own body. Always return an action with `action_type='move'` and `parameters={\"direction\": "
            "<direction>}` and explain your reasoning."
        )
    }

    cognition_map = {
        "snake": AgentCognition(executor=LLMExecutor(template=SNAKE_EXECUTOR_TEMPLATE))
    }

    # LLM config
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-5")

    # Memory and persistence
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SimpleMemoryStream(persistence)

    # Orchestrator
    orchestrator = Orchestrator(
        world_state=world_state,
        agents=agents,
        world_prompt="",
        agent_prompts=agent_prompts,
        simulation_rules=rules,
        agent_cognition=cognition_map,
        llm_provider=provider,
        llm_model=model,
        persistence=persistence,
        memory=memory,
        world_update_mode="deterministic",
    )

    print("SNAKE GAME - LLM Edition")
    print(f"Using: {provider}/{model}")
    print(f"Grid: {GRID_WIDTH}x{GRID_HEIGHT}")
    print("\nPress Ctrl+C to stop\n")

    max_ticks = 50

    # Show initial state
    print(f'\n{"="*40}')
    print(f"Tick 0 | Score: {rules.score}")
    print(render_grid(rules))

    game_over_announced = False

    def print_tick(tick: int, prev_state, new_state, actions):
        nonlocal game_over_announced
        if game_over_announced:
            return

        print(f'\n{"="*40}')
        print(f"Tick {tick} | Score: {rules.score}")
        print(render_grid(rules))

        if actions:
            action = actions[0]
            direction = (action.parameters or {}).get("direction")
            if direction:
                print(f"Action: move {direction}  |  Reason: {action.reasoning}")

        if rules.game_over:
            print("GAME OVER!")
            game_over_announced = True

    orchestrator.tick_listeners.append(print_tick)

    await orchestrator.run(num_ticks=max_ticks)

    print(f'\n{"="*40}')
    if rules.game_over:
        print("GAME OVER!")
    else:
        print("Time limit reached!")

    print(f"\nFinal Score: {rules.score}")
    print(f"Snake Length: {len(rules.snake_body)}")


if __name__ == "__main__":
    asyncio.run(main())
