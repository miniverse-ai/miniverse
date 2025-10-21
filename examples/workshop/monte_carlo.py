"""Batch runner that samples workshop outcomes with stochastic task arrivals.

Example usage (runs 100 trials, each 20 ticks, base seed 42):

    UV_CACHE_DIR=.uv-cache uv run python -m examples.workshop.monte_carlo \
        --runs 100 --ticks 20 --base-seed 42

The script reuses ``run_simulation`` from ``examples.workshop.run`` and injects
random arrivals via ``WorkshopRules`` (seeded per run). It prints summary
statistics so you can gauge how often the crew clears the backlog under
uncertainty.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
from typing import List

from .run import run_simulation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monte Carlo workshop batch runner")
    parser.add_argument(
        "--runs", type=int, default=50, help="Number of simulations to execute"
    )
    parser.add_argument("--ticks", type=int, default=20, help="Ticks per simulation")
    parser.add_argument(
        "--base-seed",
        type=int,
        default=0,
        help="Base seed (each run adds its index); 0 disables deterministic seeding",
    )
    parser.add_argument(
        "--arrival-chance",
        type=float,
        default=0.35,
        help="Probability that new tasks arrive in a tick (0-1)",
    )
    parser.add_argument(
        "--max-new",
        type=int,
        default=2,
        help="Maximum number of tasks that can arrive when a tick spawns new work",
    )
    return parser.parse_args()


def _seed_for_index(base_seed: int, index: int) -> int | None:
    if base_seed <= 0:
        return None
    return base_seed + index


async def run_trial(
    ticks: int,
    *,
    seed: int | None,
    arrival_chance: float,
    max_new: int,
) -> float:
    result = await run_simulation(
        ticks,
        use_llm=False,
        seed=seed,
        task_arrival_chance=arrival_chance,
        max_new_tasks=max_new,
        verbose=False,
    )
    final_state = result["final_state"]
    backlog_metric = final_state.resources.get_metric("task_backlog")
    return float(backlog_metric.value)


async def run_batch(args: argparse.Namespace) -> None:
    outcomes: List[float] = []
    for idx in range(args.runs):
        seed = _seed_for_index(args.base_seed, idx)
        backlog = await run_trial(
            args.ticks,
            seed=seed,
            arrival_chance=args.arrival_chance,
            max_new=args.max_new,
        )
        outcomes.append(backlog)

    cleared = sum(1 for value in outcomes if math.isclose(value, 0.0))
    mean_backlog = statistics.fmean(outcomes)
    stdev_backlog = statistics.pstdev(outcomes) if len(outcomes) > 1 else 0.0
    max_backlog = max(outcomes)

    print(f"Completed {args.runs} runs of {args.ticks} ticks each.")
    print(f"  Arrival chance: {args.arrival_chance:.2f}, Max new tasks: {args.max_new}")
    print(f"  Mean final backlog: {mean_backlog:.2f} (σ={stdev_backlog:.2f})")
    print(f"  P(backlog cleared): {cleared / args.runs:.2%}")
    print(f"  Worst-case final backlog: {max_backlog:.0f}")


def main() -> None:
    args = parse_args()
    asyncio.run(run_batch(args))


if __name__ == "__main__":
    main()
