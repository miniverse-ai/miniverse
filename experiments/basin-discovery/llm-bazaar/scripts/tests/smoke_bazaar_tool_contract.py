#!/usr/bin/env python3
"""Cheap smoke test for Bazaar tool-contract prompts.

This does not run the full simulation. It asks one model to make decisions in
three small Bazaar-like contexts and validates that it uses the intended
structured-output shape:
- public speech in `respond`,
- private speech via `private_message`,
- planning lists via `write_list` with `parameters.items`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from miniverse.llm_utils import call_llm_with_retries
from miniverse.schemas import StepOutput


SYSTEM_PROMPT = """You are a Kōen Market customer.

Available actions:
- check_market_status: See current market time, active vendors, active customers, the vendor you are currently engaging, and recent public market talk.
- inspect_vendor: Inspect one vendor's current goods and listed prices. Target: shop name.
- make_offer: Make a formal offer after negotiation. Target: shop name. Parameters: item or items, price.
- private_message: Send one private message to a visible participant. Target: exact visible participant name. Required parameter: message (text). Public speech should use the respond field instead.
- check_budget: View your remaining budget, shopping list, and purchases.
- leave_market: Leave the market for the day when you are done shopping or decide to stop.

Structured response format:
Fields: think, action, target, parameters, respond.
- If you use an action, action must be one exact name from Available actions.
- If you only speak publicly, set action to null.
- Public speech goes in respond and is heard by the market.
- Public speech can stand alone; do not pair it with an action unless you also need that tool.
- If the user asks for only public speech, set action, target, and parameters to null and put the utterance in respond.
- Private speech uses action="private_message" with target set to the exact visible recipient name and parameters.message set to the message text.
- respond_to is not an action. Do not use respond_to.
- For normal tool actions, leave respond empty.
Example public speech shape: {"respond":"message text"}
Example private speech shape: {"action":"private_message","target":"Corner Provisions","parameters":{"message":"message text"}}
"""


CASES = [
    {
        "name": "public_speech",
        "user": (
            "The market is open. You want to ask all vendors whether anyone has "
            "chashu pork today. Put exactly this public market utterance in the "
            "respond field: \"Does anyone have chashu pork today?\" Set action, "
            "target, and parameters to null. Do not check market status or use any action."
        ),
        "expect": {"respond": True, "forbid_actions": ["respond", "respond_to", "private_message"]},
    },
    {
        "name": "private_persona_alias",
        "user": (
            "The market is open. You are currently engaging Corner Provisions. "
            "The operator introduced herself as Aura. Privately ask Aura whether "
            "she can hold one Dried Seaweed Sheets (50-pack)."
        ),
        "expect": {"action": "private_message", "message": True},
    },
    {
        "name": "planning_write_list",
        "user": (
            "The market is closed. Market-facing tools are no longer available. "
            "Available preparation actions: write_list. Create tomorrow's shopping "
            "list with Miso Paste (500g), Pickled Ginger (jar), and Dried Seaweed "
            "Sheets (50-pack)."
        ),
        "system_suffix": (
            "\nAvailable actions:\n"
            "- write_list: Set your shopping list for the next market session and finish preparation. "
            "Required parameter: items (list of 3-6 item names).\n"
            "\nUse write_list now."
        ),
        "expect": {"action": "write_list", "items": True},
    },
]


def validate(case: dict[str, Any], output: StepOutput) -> list[str]:
    errors: list[str] = []
    expected = case["expect"]
    if expected.get("forbid_actions"):
        if output.action in set(expected["forbid_actions"]):
            errors.append(f"unexpected action {output.action!r}")
    elif expected.get("action") is None:
        if output.action:
            errors.append(f"expected no action, got {output.action!r}")
    elif output.action != expected["action"]:
        errors.append(f"expected action {expected['action']!r}, got {output.action!r}")
    if expected.get("respond") and not output.respond:
        errors.append("expected public respond text")
    if expected.get("message"):
        params = output.parameters or {}
        if not params.get("message"):
            errors.append(f"expected parameters.message, got {params!r}")
    if expected.get("items"):
        params = output.parameters or {}
        items = params.get("items")
        if not isinstance(items, list) or len(items) < 3:
            errors.append(f"expected parameters.items list, got {params!r}")
    if output.action == "respond_to" or output.respond_to:
        errors.append(f"unexpected respond_to use: action={output.action!r}, respond_to={output.respond_to!r}")
    return errors


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", default="gpt-5-mini")
    args = parser.parse_args()

    results = []
    failures = []
    for case in CASES:
        system_prompt = SYSTEM_PROMPT + case.get("system_suffix", "")
        output = await call_llm_with_retries(
            system_prompt=system_prompt,
            user_prompt=case["user"],
            llm_provider=args.provider,
            llm_model=args.model,
            response_model=StepOutput,
        )
        errors = validate(case, output)
        row = {
            "case": case["name"],
            "output": output.model_dump(mode="json"),
            "errors": errors,
        }
        results.append(row)
        if errors:
            failures.append(row)

    print(json.dumps(results, indent=2))
    if failures:
        raise SystemExit(f"FAIL: {len(failures)} tool-contract case(s) failed")


if __name__ == "__main__":
    asyncio.run(main())
