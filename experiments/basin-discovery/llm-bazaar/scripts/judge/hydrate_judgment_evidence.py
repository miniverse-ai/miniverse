"""Hydrate judge event-id citations with canonical event text from run_data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from judge_packet import build_packet, load_run_data
from validate_judgment_citations import packet_event_map, quote_value


def hydrate_span(span: dict[str, Any], events: dict[str, str]) -> None:
    event_id = span.get("event_id")
    event_text = events.get(event_id, "")
    hint = quote_value(span)
    span["event_text"] = event_text
    span["quote_exact"] = bool(hint and hint in event_text)


def hydrate_incident(incident: dict[str, Any], events: dict[str, str]) -> None:
    focal_event_id = incident.get("focal_event_id")
    incident["focal_event_text"] = events.get(focal_event_id, "")
    incident["context_events"] = [
        {"event_id": event_id, "event_text": events.get(event_id, "")}
        for event_id in incident.get("context_event_ids", []) or []
    ]


def hydrate_judgment(judgment: dict[str, Any], events: dict[str, str]) -> None:
    for item in judgment.get("coded_observations", []) or []:
        if isinstance(item, dict):
            item["focal_events"] = [
                {"event_id": event_id, "event_text": events.get(event_id, "")}
                for event_id in item.get("focal_event_ids", []) or []
            ]
            item["context_events"] = [
                {"event_id": event_id, "event_text": events.get(event_id, "")}
                for event_id in item.get("context_event_ids", []) or []
            ]
    for item in judgment.get("highlights", []) or []:
        if isinstance(item, dict):
            hydrate_span(item, events)
    for item in judgment.get("incidents", []) or []:
        if isinstance(item, dict):
            hydrate_incident(item, events)
    for score in (judgment.get("scores") or {}).values():
        if not isinstance(score, dict):
            continue
        for item in score.get("evidence_spans", []) or []:
            if isinstance(item, dict):
                hydrate_span(item, events)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--target-agent", required=True)
    parser.add_argument("--judgment", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload, _ = load_run_data(args.run)
    packet = build_packet(payload, args.target_agent)
    events = packet_event_map(packet)
    judgment: dict[str, Any] = json.loads(args.judgment.read_text())

    hydrate_judgment(judgment, events)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(judgment, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
