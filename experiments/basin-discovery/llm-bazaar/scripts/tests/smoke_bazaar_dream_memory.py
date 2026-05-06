#!/usr/bin/env python3
"""Cheap smoke test for Bazaar dream-memory generation.

Runs the Bazaar dream compression path for one synthetic vendor day. This avoids
running a full market simulation while still exercising:
- per-agent LLM routing,
- structured dream output,
- queued dream_summary and dream memory records,
- audit-only dream_output events.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path


def load_bazaar_actions(repo_root: Path):
    actions_path = repo_root / "experiments/basin-discovery/llm-bazaar/actions.py"
    spec = importlib.util.spec_from_file_location("bazaar_actions_smoke", actions_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {actions_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--agent", default="vendor_a")
    parser.add_argument(
        "--context-file",
        type=Path,
        help="Optional full context text to pass through the dream compressor.",
    )
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=0,
        help="If set with --context-file, truncate context to this many chars.",
    )
    parser.add_argument(
        "--base-model",
        default="gpt-5-mini",
        help="Global/default model; --model is applied as the target agent override.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[5]
    mod = load_bazaar_actions(repo_root)
    state_path = repo_root / "experiments/basin-discovery/llm-bazaar/state.yaml"
    actions = mod.BazaarActions(state_path)

    # Cost-control: exercise one target agent only.
    mod.VENDOR_IDS.clear()
    mod.CUSTOMER_IDS.clear()
    mod.VENDOR_IDS.add(args.agent)

    actions.bind_llm_config(
        args.provider,
        args.base_model,
        {args.agent: {"provider": args.provider, "model": args.model}},
    )
    actions.current_day = 1
    actions.vendors[args.agent]["plan"] = (
        "Restock rice, protect cash, and remember Mei is budget constrained."
    )
    actions.vendors[args.agent]["ledger"].append(
        {
            "day": 1,
            "type": "sale",
            "customer": "mei",
            "item": "rice",
            "listed_price": 7.0,
            "final_price": 6.0,
            "margin": 2.0,
        }
    )
    if args.context_file:
        context_text = args.context_file.read_text()
        if args.max_context_chars and len(context_text) > args.max_context_chars:
            context_text = context_text[-args.max_context_chars :]
    else:
        context_text = (
            "=== ACTIVE SYSTEM PROMPT ===\n"
            "You are a vendor at Kōen Market.\n\n"
            "=== ACTIVE DAY CONTEXT ===\n"
            "Mei said she is on a tight student budget. "
            "I sold Mei Short-grain Rice for $6. "
            "I should restock rice and keep prices accessible."
        )
    actions.vendors[args.agent]["_dream_context_snapshot"] = context_text

    await actions._run_dream_phase()

    counts: dict[str, int] = {}
    for mem in actions.pending_memories:
        counts[mem["memory_type"]] = counts.get(mem["memory_type"], 0) + 1

    result = {
        "counts": counts,
        "context_chars": len(context_text),
        "audit": actions._dream_audit,
        "events": actions.pending_events,
    }
    print(json.dumps(result, indent=2))

    if counts.get("dream_summary") != 1 or counts.get("dream", 0) < 3:
        raise SystemExit("FAIL: expected one dream_summary and at least three dream memories")
    if counts.get("dream_fallback"):
        raise SystemExit("FAIL: dream path fell back")
    audit_model = actions._dream_audit[0].get("model") if actions._dream_audit else None
    if audit_model != args.model:
        raise SystemExit(f"FAIL: expected dream model {args.model}, got {audit_model}")


if __name__ == "__main__":
    asyncio.run(main())
