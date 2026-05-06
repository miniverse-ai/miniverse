"""Run Basin Discovery LLM judge passes over saved Miniverse artifacts."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from extract_metrics import bazaar_vendor_rows, infer_scenario, read_text
from judge_packet import build_packet, format_packet_xml


REPO_ROOT = Path(__file__).resolve().parents[5]
MEASUREMENT_DIR = REPO_ROOT / "experiments" / "basin-discovery" / "llm-bazaar" / "measurement"
BEHAVIOR_PROMPT = MEASUREMENT_DIR / "behavior-rubric-judge-prompt.md"
HEALTH_PROMPT = MEASUREMENT_DIR / "run-health-judge-prompt.md"
BEHAVIOR_RUBRIC = MEASUREMENT_DIR / "behavior-rubric.yaml"
RUN_HEALTH_RUBRIC = MEASUREMENT_DIR / "run-health-rubric.yaml"
JUDGE_CWD = Path("/tmp/basin-discovery-claude-judge-cwd")
JUDGE_SYSTEM_PROMPT = """You are an independent research measurement judge.

Your only task is to evaluate the experiment artifact provided in the user prompt.
Use only the rubric, metrics, transcript, and instructions included in that prompt.
Do not use project memory, workspace instructions, previous sessions, or outside context.
Do not browse, inspect files, call tools, or infer from anything outside the prompt.
Return only the requested JSON object.
"""

SENSITIVE_ARTIFACT_KEYS = {
    "model",
    "provider",
    "scenario",
    "vendor_model",
    "persona",
    "persona_file",
    "persona_targets",
    "vendor_persona",
    "run_dir",
    "transcripts",
}

REDACTED_VALUE_PATTERNS = [
    "Trix",
    "trickster",
    "Aura",
    "aura",
    "Sage",
    "sage",
    "functional-vendor",
    "gpt4o-personas-a",
    "gpt4o-vendor-personas-a-5day",
    "baseline_gpt-5-mini",
    "baseline_mixed-direct-openai-anthropic-2day",
]


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


def redact_behavior_transcript(text: str) -> str:
    """Remove experimental condition cues before behavioral judging.

    Behavior coding should score what the target agent did, not whether the
    behavior matches a visible persona/model label. Run-health judging keeps
    unredacted prompts separately for setup inspection.
    """
    if not text:
        return text
    text = re.sub(
        r"\[System Prompt\]\n.*?(?=\n\[(?:You|Marker|Result|Context Reset|System Prompt)\]|\Z)",
        "[System Prompt]\n[redacted scenario and identity prompt for blinded behavior coding]",
        text,
        flags=re.S,
    )
    for pattern in REDACTED_VALUE_PATTERNS:
        text = re.sub(re.escape(pattern), "[redacted_condition]", text, flags=re.I)
    return text


def redact_analysis_artifact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in SENSITIVE_ARTIFACT_KEYS:
                continue
            redacted[key] = redact_analysis_artifact(item)
        return redacted
    if isinstance(value, list):
        return [redact_analysis_artifact(item) for item in value]
    if isinstance(value, str):
        for pattern in REDACTED_VALUE_PATTERNS:
            value = re.sub(re.escape(pattern), "[redacted_condition]", value, flags=re.I)
        return value
    return value


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
    return []


def render(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def compact_run_data(payload: dict[str, Any], *, blinded: bool = False) -> dict[str, Any]:
    data = {
        "run_id": payload.get("run_id"),
        "scenario": payload.get("scenario"),
        "model": payload.get("model"),
        "provider": payload.get("provider"),
        "persona": payload.get("persona"),
        "persona_targets": payload.get("persona_targets"),
        "run_dir": payload.get("run_dir"),
        "transcripts": payload.get("transcripts"),
    }
    return redact_analysis_artifact(data) if blinded else data


def analysis_artifacts(
    payload: dict[str, Any],
    scenario_id: str,
    target_agent: str,
    *,
    blinded: bool = False,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {"run": compact_run_data(payload, blinded=blinded)}
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
    return redact_analysis_artifact(artifacts) if blinded else artifacts


def behavior_prompt(
    payload: dict[str, Any],
    scenario_id: str,
    target_agent: str,
    *,
    max_events_per_chunk: int | None = None,
    chunk_index: int | None = None,
) -> str:
    rubric = yaml.safe_load(BEHAVIOR_RUBRIC.read_text())
    packet = build_packet(payload, target_agent, max_events_per_chunk=max_events_per_chunk)
    return render(
        BEHAVIOR_PROMPT.read_text(),
        target_agent=target_agent,
        rubric_yaml=yaml.safe_dump(rubric, sort_keys=False),
        analysis_artifacts=json.dumps(
            analysis_artifacts(payload, scenario_id, target_agent, blinded=True),
            indent=2,
            default=str,
        ),
        transcript=redact_behavior_transcript(format_packet_xml(packet, chunk_index=chunk_index)),
    )


def write_behavior_packet(
    judgments_dir: Path,
    payload: dict[str, Any],
    target_agent: str,
    *,
    max_events_per_chunk: int | None = None,
) -> None:
    packet_dir = judgments_dir / "packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet = build_packet(payload, target_agent, max_events_per_chunk=max_events_per_chunk)
    (packet_dir / f"{target_agent}_behavior_packet.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False, default=str)
    )
    (packet_dir / f"{target_agent}_behavior_packet.xml").write_text(format_packet_xml(packet))


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
        rubric_yaml=yaml.safe_dump(yaml.safe_load(RUN_HEALTH_RUBRIC.read_text()), sort_keys=False),
        run_data_json=json.dumps(compact_run_data(payload), indent=2, default=str),
        agent_contexts=json.dumps(contexts, indent=2, default=str),
        scenario_artifacts=json.dumps(payload.get("scenario_artifacts", {}), indent=2, default=str),
    )


def run_claude(prompt: str, model: str | None) -> str:
    if JUDGE_CWD.exists():
        shutil.rmtree(JUDGE_CWD)
    JUDGE_CWD.mkdir(parents=True)
    mcp_config = JUDGE_CWD / "empty-mcp.json"
    mcp_config.write_text('{"mcpServers":{}}\n')
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config),
        "--system-prompt",
        JUDGE_SYSTEM_PROMPT,
    ]
    if model and model != "default":
        cmd[2:2] = ["--model", model]
    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
        cwd=JUDGE_CWD,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def strip_json_fence(text: str) -> str:
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S)
    if fence:
        return fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_claude_result_text(text: str) -> dict[str, Any]:
    parsed = json.loads(strip_json_fence(text))
    if not isinstance(parsed, dict):
        return {"raw": parsed}
    return parsed


def extract_event_result(events: list[Any]) -> dict[str, Any]:
    result_text = None
    metadata: dict[str, Any] = {}
    for event in events:
        if isinstance(event, dict) and event.get("type") == "result":
            result_text = event.get("result")
            metadata = {
                "judge_model_usage": event.get("modelUsage"),
                "judge_total_cost_usd": event.get("total_cost_usd"),
                "judge_duration_ms": event.get("duration_ms"),
                "judge_session_id": event.get("session_id"),
            }
    if not isinstance(result_text, str) or not result_text.strip():
        return {"raw": events, "parse_error": True, "parse_error_message": "No Claude result event found"}
    parsed = parse_claude_result_text(result_text)
    parsed.setdefault("_judge_extraction", metadata)
    return parsed


def parse_judge_output(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
        try:
            result = parse_claude_result_text(parsed["result"])
            result.setdefault(
                "_judge_extraction",
                {
                    "judge_model_usage": parsed.get("modelUsage"),
                    "judge_total_cost_usd": parsed.get("total_cost_usd"),
                    "judge_duration_ms": parsed.get("duration_ms"),
                    "judge_session_id": parsed.get("session_id"),
                },
            )
            return result
        except json.JSONDecodeError:
            return {"claude_result": parsed, "parse_error": True}
    if isinstance(parsed, dict) and isinstance(parsed.get("raw"), list):
        return extract_event_result(parsed["raw"])
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return extract_event_result(parsed)
    return {"raw": parsed, "parse_error": True}


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
    existing = [path for path in (prompt_path, output_path) if path.exists()]
    if existing and not force:
        print(
            "skip existing "
            + ", ".join(str(path) for path in existing)
            + " (pass --force to overwrite)"
        )
        return
    prompt_path.write_text(prompt)
    if dry_run:
        print(f"rendered {prompt_path}")
        return
    raw = run_claude(prompt, model)
    try:
        parsed = parse_judge_output(raw)
    except json.JSONDecodeError:
        parsed = {"raw": raw, "parse_error": True}
    output_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False))
    print(f"wrote {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Run folder or run_data.json path")
    parser.add_argument("--passes", default="behavior,health", help="Comma-separated: behavior,health")
    parser.add_argument("--target-agent", action="append", default=[])
    parser.add_argument(
        "--judge-model",
        default="default",
        help="Claude model name. Use 'default' to omit --model and use the local Claude Code default.",
    )
    parser.add_argument(
        "--judgment-set",
        help="Optional subdirectory under judgments/ for non-overwriting reruns, e.g. judge-v2-indexed.",
    )
    parser.add_argument("--max-events-per-chunk", type=int)
    parser.add_argument("--chunk-index", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    payload, run_data_path = load_run_data(args.run)
    scenario_id = infer_scenario(payload)
    if scenario_id == "unknown":
        raise SystemExit(f"Could not infer scenario from {run_data_path}")
    judgments_dir = output_dir_for(run_data_path, payload)
    if args.judgment_set:
        judgments_dir = judgments_dir / args.judgment_set
    passes = {item.strip() for item in args.passes.split(",") if item.strip()}
    targets = target_agents(payload, scenario_id, args.target_agent)

    if "behavior" in passes:
        for target in targets:
            write_behavior_packet(
                judgments_dir,
                payload,
                target,
                max_events_per_chunk=args.max_events_per_chunk,
            )
            behavior_pass_name = (
                f"behavior_chunk{args.chunk_index}"
                if args.chunk_index is not None
                else "behavior"
            )
            write_pass(
                judgments_dir,
                behavior_pass_name,
                target,
                behavior_prompt(
                    payload,
                    scenario_id,
                    target,
                    max_events_per_chunk=args.max_events_per_chunk,
                    chunk_index=args.chunk_index,
                ),
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
