"""Generate or execute the Basin Discovery experiment matrix."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CONFIG = (
    REPO_ROOT
    / "experiments"
    / "basin-discovery"
    / "configs"
    / "openai-red-team-matrix.yaml"
)


def load_config(path: Path) -> Dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def build_commands(config: Dict[str, Any], *, limit: int | None = None) -> Iterable[Dict[str, Any]]:
    defaults = config["defaults"]
    count = 0
    for scenario in config["scenarios"]:
        for persona in config["personas"]:
            for model in config["models"]:
                for replication in range(1, int(model["n_per_cell"]) + 1):
                    cmd = [
                        "uv",
                        "run",
                        "miniverse",
                        "run",
                        scenario["path"],
                        "--max-turns",
                        "50",
                        "--memory",
                        defaults["memory"],
                    ]
                    max_steps = scenario.get("max_steps", defaults.get("max_steps"))
                    if max_steps is not None:
                        cmd.extend(["--max-steps", str(max_steps)])
                    hours = scenario.get("hours", defaults.get("hours"))
                    if hours is not None:
                        cmd.extend(["--hours", str(hours)])
                    if defaults.get("llm"):
                        cmd.append("--llm")
                    if defaults.get("async"):
                        cmd.append("--async")
                    if defaults.get("context_window"):
                        cmd.append("--context-window")
                    persona_file = persona.get("file")
                    if persona_file:
                        cmd.extend(["--persona-file", persona_file])
                    cmd.extend(["--seed", str(replication)])
                    yield {
                        "cmd": cmd,
                        "env": {
                            "LLM_PROVIDER": model["provider"],
                            "LLM_MODEL": model["model"],
                            "LLM_TEMPERATURE": str(defaults["temperature"]),
                            "BASIN_SCENARIO": scenario["id"],
                            "BASIN_PERSONA": persona["id"],
                            "BASIN_MODEL": model["id"],
                            "BASIN_REPLICATION": str(replication),
                        },
                    }
                    count += 1
                    if limit is not None and count >= limit:
                        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    for item in build_commands(config, limit=args.limit):
        env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in item["env"].items())
        command_text = " ".join(shlex.quote(part) for part in item["cmd"])
        print(f"{env_prefix} {command_text}")
        if args.execute:
            env = os.environ.copy()
            env.update(item["env"])
            subprocess.run(item["cmd"], cwd=REPO_ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
