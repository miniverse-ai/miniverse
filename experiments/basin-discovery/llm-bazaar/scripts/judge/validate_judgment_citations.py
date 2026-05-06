"""Validate judge evidence spans against an indexed transcript packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from judge_packet import build_packet, load_run_data


def iter_evidence(judgment: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for item in judgment.get("coded_observations", []) or []:
        if not isinstance(item, dict):
            continue
        obs_label = item.get("behavior_code")
        for event_id in item.get("focal_event_ids", []) or []:
            spans.append(
                {
                    "location": "coded_observations.focal_event_ids",
                    "event_id": event_id,
                    "quote_hint": "",
                    "observation_behavior_code": obs_label,
                    "incident_focal": True,
                }
            )
        for event_id in item.get("context_event_ids", []) or []:
            spans.append(
                {
                    "location": "coded_observations.context_event_ids",
                    "event_id": event_id,
                    "quote_hint": "",
                    "observation_behavior_code": obs_label,
                }
            )
    for item in judgment.get("highlights", []) or []:
        if isinstance(item, dict):
            spans.append({"location": "highlights", **item})
    for item in judgment.get("incidents", []) or []:
        if not isinstance(item, dict):
            continue
        focal_event_id = item.get("focal_event_id")
        if focal_event_id:
            spans.append(
                {
                    "location": "incidents.focal_event_id",
                    "event_id": focal_event_id,
                    "quote_hint": "",
                    "incident_id": item.get("incident_id"),
                    "incident_focal": True,
                }
            )
        for event_id in item.get("context_event_ids", []) or []:
            spans.append(
                {
                    "location": "incidents.context_event_ids",
                    "event_id": event_id,
                    "quote_hint": "",
                    "incident_id": item.get("incident_id"),
                }
            )
    for score_name, score in (judgment.get("scores") or {}).items():
        if not isinstance(score, dict):
            continue
        for item in score.get("evidence_spans", []) or []:
            if isinstance(item, dict):
                spans.append({"location": f"scores.{score_name}.evidence_spans", **item})
    return spans


def packet_event_map(packet: dict[str, Any]) -> dict[str, str]:
    events: dict[str, str] = {}
    for chunk in packet.get("chunks", []) or []:
        for event in chunk.get("events", []) or []:
            event_id = event.get("id")
            if event_id:
                events[event_id] = event.get("content") or ""
    return events


def packet_event_meta(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for chunk in packet.get("chunks", []) or []:
        for event in chunk.get("events", []) or []:
            event_id = event.get("id")
            if event_id:
                events[event_id] = event
    return events


def quote_value(span: dict[str, Any]) -> str:
    quote = span.get("quote_hint")
    if quote is None:
        quote = span.get("quote")
    return str(quote or "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--target-agent", required=True)
    parser.add_argument("--judgment", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    payload, _ = load_run_data(args.run)
    packet = build_packet(payload, args.target_agent)
    events = packet_event_map(packet)
    event_meta = packet_event_meta(packet)
    judgment = json.loads(args.judgment.read_text())

    checks = []
    for span in iter_evidence(judgment):
        event_id = span.get("event_id")
        quote = quote_value(span)
        event_text = events.get(event_id)
        role = (event_meta.get(event_id) or {}).get("role")
        hydrated_quote = event_text or ""
        if quote and event_text and quote in event_text:
            hydrated_quote = quote
        checks.append(
            {
                "location": span.get("location"),
                "event_id": event_id,
                "event_exists": event_text is not None,
                "event_role": role,
                "incident_focal": bool(span.get("incident_focal")),
                "focal_is_target": not span.get("incident_focal") or role == "target",
                "quote_present": bool(event_text is not None and quote and quote in event_text),
                "quote_empty": quote == "",
                "hydrated_text": hydrated_quote,
                "quote": quote,
            }
        )

    report = {
        "target_agent": args.target_agent,
        "judgment": str(args.judgment),
        "checked_spans": len(checks),
        "missing_event_ids": [c for c in checks if not c["event_exists"]],
        "non_target_focal_event_ids": [
            c for c in checks if c["event_exists"] and c["incident_focal"] and not c["focal_is_target"]
        ],
        "empty_quotes": [c for c in checks if c["event_exists"] and c["quote_empty"]],
        "missing_quotes": [
            c for c in checks if c["event_exists"] and not c["quote_present"] and not c["quote_empty"]
        ],
        "checks": checks,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"wrote {args.out}")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
