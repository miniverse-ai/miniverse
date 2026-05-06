#!/usr/bin/env python3
"""Serve the Miniverse repo and open the curated LLM Bazaar viewer."""

from __future__ import annotations

import argparse
import http.server
import socket
import socketserver
import sys
import webbrowser
from functools import partial
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "experiments" / "basin-discovery" / "llm-bazaar" / "viewer.html").exists():
            return path
    raise SystemExit("Could not find Miniverse repo root containing experiments/basin-discovery/llm-bazaar/viewer.html")


def choose_port(preferred: int) -> int:
    for port in range(preferred, preferred + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"No available local port found from {preferred} to {preferred + 99}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Serve without opening the browser")
    args = parser.parse_args()

    script_path = Path(__file__).resolve()
    repo_root = find_repo_root(script_path)
    port = choose_port(args.port)
    url = f"http://127.0.0.1:{port}/experiments/basin-discovery/llm-bazaar/viewer.html"

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(repo_root))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Serving LLM Bazaar viewer at {url}")
        print("Press Ctrl-C to stop.")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
