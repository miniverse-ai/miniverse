"""Run one run-health judge pass over one saved Miniverse run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from judge_runs import health_prompt, load_run_data, run_claude


def assert_can_write(path: Path, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise SystemExit(f"Refusing to overwrite existing file: {path}. Pass --overwrite to replace it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Run folder or run_data.json path")
    parser.add_argument("--out", type=Path, required=True, help="Raw Claude Code JSON output path")
    parser.add_argument("--prompt-out", type=Path, default=None, help="Optional rendered judge prompt path")
    parser.add_argument("--judge-model", default="default")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing output files")
    args = parser.parse_args()

    payload, _run_data_path = load_run_data(args.run)
    prompt = health_prompt(payload)
    if args.prompt_out:
        assert_can_write(args.prompt_out, overwrite=args.overwrite)
        args.prompt_out.parent.mkdir(parents=True, exist_ok=True)
        args.prompt_out.write_text(prompt)

    assert_can_write(args.out, overwrite=args.overwrite)
    raw = run_claude(prompt, args.judge_model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        args.out.write_text(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        args.out.write_text(json.dumps({"raw": raw, "parse_error": True}, indent=2, ensure_ascii=False))
    print(f"wrote raw run-health judgment to {args.out}")


if __name__ == "__main__":
    main()
