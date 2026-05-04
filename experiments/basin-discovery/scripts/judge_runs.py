"""Run Basin Discovery LLM judge passes over saved Miniverse artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from extract_metrics import bazaar_vendor_rows, infer_scenario, read_text


REPO_ROOT = Path(__file__).resolve().parents[3]
MEASUREMENT_DIR = REPO_ROOT / "experiments" / "basin-discovery" / "measurement"
BEHAVIOR_PROMPT = MEASUREMENT_DIR / "behavior-rubric-judge-prompt.md"
HEALTH_PROMPT = MEASUREMENT_DIR / "run-health-judge-prompt.md"
ROLEPLAY_PROMPT = MEASUREMENT_DIR / "roleplay-validation-prompt.md"
CODING_SCHEMA = MEASUREMENT_DIR / "coding-schema.yaml"


def load_run_data(path_or_dir: Path) -> tuple[dict[str, Any], Path]:
    if path_or_dir.is_dir():
        candidates = [path_or_dir / "run_data.json", *path_or_dir.glob("*_run_data.json")]
        for candidate in candidates:
            if candidate.exists():
                return json.loads(candidate.read_text()), candidate
    return json.loads(path_or_dir.read_text()), path_or_dir


def output_dir_for(run_data_path: Path, payload: dict[str, Any]) -> Path:
    run_dir = Path(payload.get("run_dir") or "")
    if run_dir.exists():
        return run_dir / "judgments"
    if run_data_path.name == "run_data.json":
        return run_data_path.parent / "judgments"
    return run_data_path.with_name(run_data_path.stem.replace("_run_data", "") + "_judgments")


def agent_context(payload: dict[str, Any], agent_id: str, key: str = "full_context") -> str:
    context = payload.get("agent_contexts", {}).get(agent_id, {})
    if isinstance(context, dict):
        for candidate in (key, "combined", "legacy_transcript", "current_context"):
            value = context.get(candidate)
            if value:
                text = read_text(value)
                if text:
                    return text
    for path in payload.get("transcripts", []):
        if str(path).endswith(f"_{agent_id}.txt"):
            return read_text(path)
    return ""


def agent_system_prompt(payload: dict[str, Any], agent_id: str) -> str:
    context = payload.get("agent_contexts", {}).get(agent_id, {})
    if isinstance(context, dict) and context.get("system_prompt"):
        return read_text(context["system_prompt"])
    transcript = agent_context(payload, agent_id)
    if "=== CURRENT TRANSCRIPT ===" in transcript:
        return transcript.split("=== CURRENT TRANSCRIPT ===", 1)[0]
    return ""


def target_agents(payload: dict[str, Any], scenario_id: str, requested: list[str]) -> list[str]:
    if requested:
        return requested
    if scenario_id == "llm_bazaar":
        return sorted(payload.get("scenario_artifacts", {}).get("vendors", {}).keys())
    targets = payload.get("persona_targets") or []
    if targets:
        return list(targets)
    return ["dlp_agent"] if scenario_id == "dlp_monitor" else []


def render(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def compact_run_data(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": payload.get("run_id"),
        "scenario": payload.get("scenario"),
        "model": payload.get("model"),
        "provider": payload.get("provider"),
        "persona": payload.get("persona"),
        "persona_targets": payload.get("persona_targets"),
        "run_dir": payload.get("run_dir"),
        "transcripts": payload.get("transcripts"),
    }


def analysis_artifacts(payload: dict[str, Any], scenario_id: str, target_agent: str) -> dict[str, Any]:
    artifacts: dict[str, Any] = {"run": compact_run_data(payload)}
    scenario_artifacts = payload.get("scenario_artifacts", {})
    if scenario_id == "llm_bazaar":
        vendor_rows = bazaar_vendor_rows(payload)
        artifacts["target_vendor_metrics"] = [
            row for row in vendor_rows if row.get("vendor_id") == target_agent
        ]
        artifacts["all_vendor_metrics"] = vendor_rows
        artifacts["target_vendor_state"] = scenario_artifacts.get("vendors", {}).get(target_agent, {})
        artifacts["market_economics"] = scenario_artifacts.get("market_economics", {})
    else:
        artifacts["scenario_artifacts"] = scenario_artifacts
    return artifacts


def behavior_prompt(payload: dict[str, Any], scenario_id: str, target_agent: str) -> str:
    schema = yaml.safe_load(CODING_SCHEMA.read_text())
    rubric_key = "bazaar_target_behaviors" if scenario_id == "llm_bazaar" else "dlp_target_behaviors"
    rubric = {
        rubric_key: schema.get(rubric_key, {}),
        "petri_inspired_behavior_dimensions": schema.get("petri_inspired_behavior_dimensions", {}),
    }
    return render(
        BEHAVIOR_PROMPT.read_text(),
        scenario_id=scenario_id,
        target_agent=target_agent,
        rubric_yaml=yaml.safe_dump(rubric, sort_keys=False),
        analysis_artifacts=json.dumps(analysis_artifacts(payload, scenario_id, target_agent), indent=2, default=str),
        transcript=agent_context(payload, target_agent),
    )


def health_prompt(payload: dict[str, Any]) -> str:
    contexts = {
        agent_id: {
            "system_prompt": agent_system_prompt(payload, agent_id),
            "full_context_path": context.get("full_context") if isinstance(context, dict) else None,
        }
        for agent_id, context in payload.get("agent_contexts", {}).items()
    }
    return render(
        HEALTH_PROMPT.read_text(),
        run_data_json=json.dumps(compact_run_data(payload), indent=2, default=str),
        agent_contexts=json.dumps(contexts, indent=2, default=str),
        scenario_artifacts=json.dumps(payload.get("scenario_artifacts", {}), indent=2, default=str),
    )


def roleplay_prompt(payload: dict[str, Any], target_agent: str) -> str:
    persona_text = ""
    persona_file = payload.get("persona_file")
    if persona_file:
        persona_path = Path(persona_file)
        if not persona_path.is_absolute():
            persona_path = REPO_ROOT / persona_path
        persona_text = read_text(str(persona_path))
    return render(
        ROLEPLAY_PROMPT.read_text(),
        persona_text=persona_text,
        transcript=agent_context(payload, target_agent),
    )


def run_claude(prompt: str, model: str) -> str:
    cmd = ["claude", "-p", "--model", model, "--output-format", "json"]
    result = subprocess.run(cmd, input=prompt, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def write_pass(
    judgments_dir: Path,
    pass_name: str,
    target: str,
    prompt: str,
    model: str,
    dry_run: bool,
    force: bool,
) -> None:
    prompt_dir = judgments_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / f"{target}_{pass_name}.md"
    output_path = judgments_dir / f"{target}_{pass_name}.json"
    if output_path.exists() and not force:
        print(f"skip existing {output_path}")
        return
    prompt_path.write_text(prompt)
    if dry_run:
        print(f"rendered {prompt_path}")
        return
    raw = run_claude(prompt, model)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"raw": raw, "parse_error": True}
    output_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False))
    print(f"wrote {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Run folder or run_data.json path")
    parser.add_argument("--passes", default="behavior,health", help="Comma-separated: behavior,health,roleplay")
    parser.add_argument("--target-agent", action="append", default=[])
    parser.add_argument("--judge-model", default="sonnet")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    payload, run_data_path = load_run_data(args.run)
    scenario_id = infer_scenario(payload)
    if scenario_id == "unknown":
        raise SystemExit(f"Could not infer scenario from {run_data_path}")
    judgments_dir = output_dir_for(run_data_path, payload)
    passes = {item.strip() for item in args.passes.split(",") if item.strip()}
    targets = target_agents(payload, scenario_id, args.target_agent)

    if "behavior" in passes:
        for target in targets:
            write_pass(
                judgments_dir,
                "behavior",
                target,
                behavior_prompt(payload, scenario_id, target),
                args.judge_model,
                args.dry_run,
                args.force,
            )
    if "roleplay" in passes:
        for target in targets:
            write_pass(
                judgments_dir,
                "roleplay",
                target,
                roleplay_prompt(payload, target),
                args.judge_model,
                args.dry_run,
                args.force,
            )
    if "health" in passes:
        write_pass(
            judgments_dir,
            "health",
            "run",
            health_prompt(payload),
            args.judge_model,
            args.dry_run,
            args.force,
        )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
