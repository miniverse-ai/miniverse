"""Build indexed transcript packets for Basin Discovery judge passes.

The packet format borrows the useful part of Petri's judge setup: every
judge-visible event has a stable id, role attribution, and exact text that can
be cited back into the saved run artifact.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


VENDOR_SHOP_NAMES = {
    "vendor_a": "Lantern Pantry",
    "vendor_b": "Corner Provisions",
    "vendor_c": "Canopy Goods",
    "vendor_d": "Market General",
}

CUSTOMER_NAMES = {
    "haruki": "Haruki",
    "yuki": "Yuki",
    "kenji": "Kenji",
    "mei": "Mei",
    "tomoko": "Tomoko",
}

SUPPLIER_NAMES = {
    "supplier": "Hayashi Supply",
    "hayashi_supply": "Hayashi Supply",
}

EVENT_TYPES_TO_KEEP = {
    "think",
    "respond",
    "action_requested",
    "action",
    "action_result",
    "context_marker",
    "context_reset",
    "world_memory",
    "rate_limit",
}


def load_run_data(path_or_dir: Path) -> tuple[dict[str, Any], Path]:
    if path_or_dir.is_dir():
        for candidate in (path_or_dir / "run_data.json", *path_or_dir.glob("*_run_data.json")):
            if candidate.exists():
                return json.loads(candidate.read_text()), candidate
    return json.loads(path_or_dir.read_text()), path_or_dir


def display_name(agent_id: str | None) -> str:
    if not agent_id:
        return ""
    return (
        VENDOR_SHOP_NAMES.get(agent_id)
        or CUSTOMER_NAMES.get(agent_id)
        or SUPPLIER_NAMES.get(agent_id)
        or agent_id
    )


def alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for agent_id, name in {**VENDOR_SHOP_NAMES, **CUSTOMER_NAMES, **SUPPLIER_NAMES}.items():
        aliases[agent_id.lower()] = agent_id
        aliases[name.lower()] = agent_id
    return aliases


def resolve_agent(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return alias_map().get(value.strip().lower(), value.strip())


def event_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    if content is None:
        content = event.get("message")
    if content is not None and event.get("parameters"):
        content = (
            f"{content}\n"
            f"parameters: {json.dumps(event.get('parameters'), ensure_ascii=False, default=str)}"
        )
    if content is None:
        pieces = []
        for key in ("action", "parameters", "success", "state_updates", "error"):
            if key in event:
                pieces.append(f"{key}: {json.dumps(event[key], ensure_ascii=False, default=str)}")
        content = "\n".join(pieces)
    return str(content or "").strip()


def is_public_response(event: dict[str, Any]) -> bool:
    return event.get("type") == "respond" and not event.get("respond_to")


def visible_to_target(event: dict[str, Any], target_agent: str) -> bool:
    event_type = event.get("type")
    if event_type not in EVENT_TYPES_TO_KEEP:
        return False

    agent_id = event.get("agent_id")
    if agent_id == target_agent:
        return True

    target_field = resolve_agent(event.get("target_agent") or event.get("target"))
    if target_field == target_agent:
        return True

    if event_type == "respond":
        if is_public_response(event):
            return True
        if resolve_agent(event.get("respond_to")) == target_agent:
            return True

    if event_type in {"context_marker", "context_reset", "world_memory", "rate_limit"}:
        text = event_text(event).lower()
        target_name = display_name(target_agent).lower()
        return target_agent.lower() in text or target_name in text

    return False


def event_role(event: dict[str, Any], target_agent: str) -> str:
    event_type = event.get("type")
    agent_id = event.get("agent_id")
    if agent_id == target_agent and event_type in {"think", "respond", "action_requested", "action"}:
        return "target"
    if agent_id == target_agent and event_type == "action_result":
        return "tool_result"
    if agent_id == "world" or event_type in {"context_marker", "context_reset", "world_memory", "rate_limit"}:
        return "controller"
    return "other_agent"


def packet_events(payload: dict[str, Any], target_agent: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, event in enumerate(payload.get("event_log", [])):
        if not isinstance(event, dict) or not visible_to_target(event, target_agent):
            continue
        text = event_text(event)
        if not text:
            continue
        agent_id = event.get("agent_id")
        packet_event = {
            "id": f"ev_{index:06d}",
            "event_index": index,
            "step": event.get("step"),
            "time": event.get("timestamp"),
            "role": event_role(event, target_agent),
            "agent_id": agent_id,
            "agent_name": display_name(agent_id),
            "type": event.get("type"),
            "respond_to": event.get("respond_to"),
            "action": event.get("action") or event.get("target"),
            "parameters": event.get("parameters"),
            "content": text,
        }
        events.append(packet_event)
    return events


def chunk_events(
    events: list[dict[str, Any]],
    *,
    max_events: int | None = None,
    overlap_events: int = 20,
) -> list[dict[str, Any]]:
    if not max_events or len(events) <= max_events:
        return [{"chunk_index": 0, "start_event": events[0]["id"] if events else None, "end_event": events[-1]["id"] if events else None, "events": events}]
    chunks: list[dict[str, Any]] = []
    start = 0
    chunk_index = 0
    stride = max(1, max_events - max(0, overlap_events))
    while start < len(events):
        window = events[start : start + max_events]
        chunks.append(
            {
                "chunk_index": chunk_index,
                "start_event": window[0]["id"],
                "end_event": window[-1]["id"],
                "events": window,
            }
        )
        chunk_index += 1
        start += stride
    return chunks


def format_event_xml(event: dict[str, Any]) -> str:
    attrs = {
        "id": event.get("id"),
        "event_index": event.get("event_index"),
        "step": event.get("step"),
        "time": event.get("time"),
        "role": event.get("role"),
        "agent": event.get("agent_name") or event.get("agent_id"),
        "agent_id": event.get("agent_id"),
        "type": event.get("type"),
    }
    if event.get("respond_to"):
        attrs["respond_to"] = event.get("respond_to")
    if event.get("action"):
        attrs["action"] = event.get("action")
    attr_text = " ".join(
        f'{key}="{html.escape(str(value), quote=True)}"'
        for key, value in attrs.items()
        if value is not None
    )
    content = html.escape(event.get("content", ""), quote=False)
    return f"<event {attr_text}>{content}</event>"


def format_packet_xml(packet: dict[str, Any], *, chunk_index: int | None = None) -> str:
    chunks = packet.get("chunks") or []
    if chunk_index is not None:
        chunks = [chunk for chunk in chunks if chunk.get("chunk_index") == chunk_index]
    lines = [
        f'<transcript_packet id="{html.escape(str(packet.get("packet_id")), quote=True)}" '
        f'target_agent="{html.escape(str(packet.get("target_agent")), quote=True)}" '
        f'target_name="{html.escape(str(packet.get("target_name")), quote=True)}">'
    ]
    for chunk in chunks:
        lines.append(
            f'  <chunk index="{chunk.get("chunk_index")}" '
            f'start_event="{chunk.get("start_event")}" end_event="{chunk.get("end_event")}">'
        )
        for event in chunk.get("events", []):
            lines.append("    " + format_event_xml(event))
        lines.append("  </chunk>")
    lines.append("</transcript_packet>")
    return "\n".join(lines)


def build_packet(
    payload: dict[str, Any],
    target_agent: str,
    *,
    max_events_per_chunk: int | None = None,
    overlap_events: int = 20,
) -> dict[str, Any]:
    events = packet_events(payload, target_agent)
    return {
        "packet_id": f"{payload.get('run_id', 'run')}_{target_agent}",
        "run_id": payload.get("run_id"),
        "target_agent": target_agent,
        "target_name": display_name(target_agent),
        "event_count": len(events),
        "source": "event_log",
        "visibility_model": "target-centric: target events, tool results for target, public speech, direct messages to target, and controller/memory events addressed to target",
        "chunks": chunk_events(events, max_events=max_events_per_chunk, overlap_events=overlap_events),
    }
