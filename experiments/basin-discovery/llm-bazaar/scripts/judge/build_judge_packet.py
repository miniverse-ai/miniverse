"""Render an indexed judge transcript packet for inspection or judging."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from judge_packet import build_packet, format_packet_xml, load_run_data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Run folder or run_data.json path")
    parser.add_argument("--target-agent", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--xml-out", type=Path)
    parser.add_argument("--max-events-per-chunk", type=int)
    parser.add_argument("--overlap-events", type=int, default=20)
    args = parser.parse_args()

    payload, _ = load_run_data(args.run)
    packet = build_packet(
        payload,
        args.target_agent,
        max_events_per_chunk=args.max_events_per_chunk,
        overlap_events=args.overlap_events,
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(packet, indent=2, ensure_ascii=False))
        print(f"wrote {args.out}")
    if args.xml_out:
        args.xml_out.parent.mkdir(parents=True, exist_ok=True)
        args.xml_out.write_text(format_packet_xml(packet))
        print(f"wrote {args.xml_out}")
    if not args.out and not args.xml_out:
        print(format_packet_xml(packet))


if __name__ == "__main__":
    main()
