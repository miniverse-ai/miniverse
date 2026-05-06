#!/usr/bin/env python3
"""Render a post-hoc viewer for an LLM Bazaar run.

Bazaar-specific. Reads a saved run directory (or legacy flat run_data.json),
normalizes it to a viewer_data schema, and emits a self-contained HTML
dashboard alongside.

Usage:
    render.py --run <run_dir | run_data.json>
              [--analysis-dir <path>]
              [--metrics-csv <path>]
              [--out <path.html>]
              [--data-out <path.json>]
              [--title <str>]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

VENDOR_ORDER = ["vendor_a", "vendor_b", "vendor_c", "vendor_d"]
CUSTOMER_ORDER = ["haruki", "kenji", "mei", "ryo", "tomoko", "yuki"]
SUPPLIER_IDS = {"supplier", "hayashi", "hayashi_supply"}

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR / "template.html"


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------


@dataclass
class RunPaths:
    run_dir: Path
    run_data_path: Path | None
    event_log_jsonl: Path | None
    agent_contexts_dir: Path | None
    inline_judgments_dir: Path | None
    flat_transcripts: dict[str, Path]
    run_id: str | None
    label: str | None


def resolve_run(run_arg: str) -> RunPaths:
    p = Path(run_arg).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"--run path does not exist: {p}")

    # Case A: passed a run directory
    if p.is_dir():
        run_data = p / "run_data.json"
        event_log = p / "event_log.jsonl"
        ctx_dir = p / "agent_contexts"
        judg = p / "judgments"
        return RunPaths(
            run_dir=p,
            run_data_path=run_data if run_data.exists() else None,
            event_log_jsonl=event_log if event_log.exists() else None,
            agent_contexts_dir=ctx_dir if ctx_dir.exists() else None,
            inline_judgments_dir=judg if judg.exists() else None,
            flat_transcripts={},
            run_id=p.name,
            label=p.name,
        )

    # Case B: legacy flat run_data.json
    if p.suffix == ".json":
        parent = p.parent
        stem = p.name.replace("_run_data.json", "")
        # discover sibling per-agent .txt transcripts
        flat: dict[str, Path] = {}
        for f in parent.glob(f"{stem}_*.txt"):
            agent_id = f.stem[len(stem) + 1 :]
            flat[agent_id] = f
        return RunPaths(
            run_dir=parent,
            run_data_path=p,
            event_log_jsonl=None,
            agent_contexts_dir=None,
            inline_judgments_dir=None,
            flat_transcripts=flat,
            run_id=stem,
            label=stem,
        )

    raise SystemExit(f"--run must be a directory or run_data.json: {p}")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    with path.open() as f:
        return json.load(f)


def iter_event_log(path: Path) -> Iterable[tuple[int, dict]]:
    with path.open() as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Channel classification
# ---------------------------------------------------------------------------


SALE_RE = re.compile(r"\bSALE:|\bBUNDLE SALE:", re.IGNORECASE)
BONUS_RE = re.compile(r"\bBONUS:", re.IGNORECASE)


def classify_event(ev: dict, agents: dict[str, dict]) -> tuple[str, str | None]:
    """Return (channel, derived_target).

    channel ∈ {public_speech, private_speech, supplier_speech, admin,
               sale, deal_offer, deal_accept, supplier_order, tool,
               think, context_reset, memory, error, rate_limit, other}
    """
    t = ev.get("type")
    success = ev.get("success", True)
    target = ev.get("target")
    action = ev.get("action") or ev.get("content")
    params = ev.get("parameters") or {}
    content = ev.get("content") or ""

    if t == "think":
        return "think", None
    if t == "context_marker":
        return "admin", None
    if t == "context_reset":
        return "context_reset", ev.get("target_agent")
    if t == "world_memory":
        return "memory", ev.get("target_agent")
    if t == "dream_output":
        return "memory", ev.get("target_agent")
    if t == "dream_error":
        return "error", ev.get("target_agent")
    if t == "rate_limit":
        return "rate_limit", None

    if t == "respond":
        rt = ev.get("respond_to") or target
        if rt and isinstance(rt, str):
            rid = rt.lower()
            if rid in SUPPLIER_IDS or "hayashi" in rid:
                return "supplier_speech", rt
            if rid in agents:
                return "private_speech", rt
            # named but not resolvable → still treat as private speech intent
            return "private_speech", rt
        return "public_speech", None

    if t == "action_requested":
        if action == "make_offer":
            return "deal_offer", target
        if action == "accept_deal":
            return "deal_accept", target
        if action in ("order_from_supplier", "place_supplier_order"):
            return "supplier_order", "supplier"
        if action == "respond_to":
            tgt = target or params.get("__target")
            if tgt and isinstance(tgt, str):
                rid = tgt.lower()
                if rid in SUPPLIER_IDS or "hayashi" in rid:
                    return "supplier_speech", tgt
                return "private_speech", tgt
            return "public_speech", None
        if action == "respond":
            return "public_speech", None
        return "tool", target

    if t == "action_result":
        if not success:
            return "error", target
        if SALE_RE.search(content):
            return "sale", target
        return "tool", target

    if t == "action":
        return "tool", target

    return "other", target


# ---------------------------------------------------------------------------
# Sim timestamp parser — Bazaar uses "YYYY-MM-DD HH:MM:SS" sim time.
# ---------------------------------------------------------------------------


SIM_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$")


def parse_sim_ts(ts: str) -> str | None:
    """Pass through if it parses; otherwise None. We keep ISO-like form so
    JS Date can ingest it directly."""
    if not isinstance(ts, str):
        return None
    m = SIM_TS_RE.match(ts.strip())
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}"


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------


CUSTOMER_DISPLAY = {c: c.capitalize() for c in CUSTOMER_ORDER}
SHOP_NAMES = {
    "vendor_a": "Lantern Pantry",
    "vendor_b": "Corner Provisions",
    "vendor_c": "Canopy Goods",
    "vendor_d": "Market General",
}


def build_agent_registry(run_data: dict | None, events: list[dict]) -> dict[str, dict]:
    persona_assignments = (run_data or {}).get("persona_assignments") or {}
    model_assignments = (run_data or {}).get("agent_model_assignments") or {}
    scenario_artifacts = (run_data or {}).get("scenario_artifacts") or {}
    vendors = scenario_artifacts.get("vendors") or {}
    customers = scenario_artifacts.get("customers") or {}

    agents: dict[str, dict] = {}

    def ensure(agent_id: str, role: str, lane: int):
        if agent_id in agents:
            return agents[agent_id]
        persona = (persona_assignments.get(agent_id) or {}).get("persona")
        model = (model_assignments.get(agent_id) or {}).get("model")
        provider = (model_assignments.get(agent_id) or {}).get("provider")
        agents[agent_id] = {
            "id": agent_id,
            "role": role,
            "lane": lane,
            "display_name": agent_id.replace("_", " ").title(),
            "persona": persona,
            "model": model,
            "provider": provider,
            "shop_name": SHOP_NAMES.get(agent_id),
        }
        return agents[agent_id]

    for i, vid in enumerate(VENDOR_ORDER):
        if vid in vendors or vid in persona_assignments:
            ensure(vid, "vendor", i)
    for i, cid in enumerate(CUSTOMER_ORDER):
        if cid in customers:
            ensure(cid, "customer", i)
    # supplier
    if "supplier" in vendors or "supplier" in persona_assignments:
        ensure("supplier", "supplier", 0)
    # discover stragglers from events
    for ev in events:
        aid = ev.get("agent_id")
        if not aid or aid in agents:
            continue
        if aid == "world":
            ensure(aid, "world", 0)
        elif aid in SUPPLIER_IDS:
            ensure(aid, "supplier", 0)
        else:
            # unknown — guess by id pattern
            role = "vendor" if aid.startswith("vendor_") else (
                "customer" if aid in CUSTOMER_ORDER else "other"
            )
            ensure(aid, role, 99)
    return agents


# ---------------------------------------------------------------------------
# Edge / market timeline derivation
# ---------------------------------------------------------------------------


SALE_LINE_RE = re.compile(
    r"\bSALE:\s+(?P<item>.+?)\s+to\s+(?P<customer>\w+)\s+for\s+\$(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
BUNDLE_LINE_RE = re.compile(
    r"\bBUNDLE SALE:\s+(?P<items>.+?)\s+to\s+(?P<customer>\w+)\s+for\s+\$(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def derive_edges_and_market(
    events: list[dict],
    agents: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    edges: list[dict] = []
    market_timeline: list[dict] = []

    def is_supplier(aid: str | None) -> bool:
        return bool(aid) and (aid in SUPPLIER_IDS or "hayashi" in (aid or "").lower())

    def role(aid: str | None) -> str | None:
        if not aid:
            return None
        return (agents.get(aid) or {}).get("role")

    def add_edge(ts, src, dst, kind, ref_idx, weight=1):
        if not src or not dst:
            return
        if src not in agents or dst not in agents:
            return
        # Topology guard: customers do not talk to/order from supplier directly.
        # Supplier edges are vendor<->supplier only.
        s_role, d_role = role(src), role(dst)
        if is_supplier(src) or is_supplier(dst):
            if not ((s_role == "vendor" and is_supplier(dst)) or (is_supplier(src) and d_role == "vendor")):
                return
        edges.append(
            {
                "ts": ts,
                "from": src,
                "to": dst,
                "kind": kind,
                "weight": weight,
                "ref_event_idx": ref_idx,
            }
        )

    for ev in events:
        idx = ev["idx"]
        ts = ev["sim_ts_iso"] or ev["ts"]
        ch = ev["channel"]
        aid = ev["agent_id"]
        target = ev.get("target")

        # normalize target to agent id if possible
        norm_target = None
        if isinstance(target, str):
            tl = target.lower()
            if tl in agents:
                norm_target = tl
            else:
                # try direct lookup case-insensitive, including displayed shop names
                for cand in agents:
                    if cand.lower() == tl:
                        norm_target = cand
                        break
                if norm_target is None:
                    for vid, sn in SHOP_NAMES.items():
                        if sn.lower() == tl:
                            norm_target = vid
                            break

        if ch == "public_speech":
            # broadcast to all customers visible — keep edge sparse: mark to "world"
            add_edge(ts, aid, "world", "public_msg", idx)
        elif ch == "private_speech":
            add_edge(ts, aid, norm_target or target, "private_msg", idx)
        elif ch == "supplier_speech":
            add_edge(ts, aid, "supplier", "supplier_msg", idx)
        elif ch == "deal_offer":
            add_edge(ts, aid, norm_target or target, "deal_offer", idx)
        elif ch == "deal_accept":
            add_edge(ts, aid, norm_target or target, "deal_accept", idx)
        elif ch == "supplier_order":
            add_edge(ts, aid, "supplier", "supplier_order", idx)
        elif ch == "sale":
            content = ev.get("content") or ""
            bundle_matches = list(BUNDLE_LINE_RE.finditer(content))
            for m in BUNDLE_LINE_RE.finditer(content):
                cust = m.group("customer").lower()
                price = float(m.group("price"))
                items = [s.strip() for s in m.group("items").split(",")]
                market_timeline.append(
                    {
                        "ts": ts,
                        "vendor": aid,
                        "kind": "bundle_sale",
                        "amount": price,
                        "customer": cust,
                        "items": items,
                        "event_idx": idx,
                    }
                )
                add_edge(ts, aid, cust, "sale", idx)
            if not bundle_matches:
                for m in SALE_LINE_RE.finditer(content):
                    cust = m.group("customer").lower()
                    price = float(m.group("price"))
                    item = m.group("item").strip()
                    market_timeline.append(
                        {
                            "ts": ts,
                            "vendor": aid,
                            "kind": "sale",
                            "amount": price,
                            "customer": cust,
                            "items": [item],
                            "event_idx": idx,
                        }
                    )
                    add_edge(ts, aid, cust, "sale", idx)
    return edges, market_timeline


# ---------------------------------------------------------------------------
# Event normalization
# ---------------------------------------------------------------------------


def normalize_events(
    raw_events: list[tuple[int, dict, int]],
    agents: dict[str, dict],
    source_path: str,
) -> list[dict]:
    """raw_events: (idx_in_output, raw_dict, source_line_no)"""
    out: list[dict] = []
    for new_idx, (orig_idx, ev, line_no) in enumerate(raw_events):
        ts = ev.get("timestamp")
        sim_iso = parse_sim_ts(ts)
        ch, deriv_target = classify_event(ev, agents)
        normalized = {
            "idx": new_idx,
            "ts": ts,
            "sim_ts_iso": sim_iso,
            "type": ev.get("type"),
            "agent_id": ev.get("agent_id"),
            "step": ev.get("step"),
            "target": ev.get("target") or deriv_target,
            "action": ev.get("action") or (ev.get("content") if ev.get("type") == "action_requested" else None),
            "content": ev.get("content"),
            "parameters": ev.get("parameters"),
            "success": ev.get("success", True),
            "channel": ch,
            "source_path": source_path,
            "source_line": line_no,
        }
        out.append(normalized)
    return out


# ---------------------------------------------------------------------------
# Synthesized events for legacy runs (no event_log)
# ---------------------------------------------------------------------------


def synthesize_from_ledgers(run_data: dict, agents: dict) -> list[dict]:
    events: list[dict] = []
    arts = run_data.get("scenario_artifacts") or {}
    vendors = arts.get("vendors") or {}
    customers = arts.get("customers") or {}
    idx = 0
    for vid, v in vendors.items():
        for entry in v.get("ledger", []) or []:
            day = entry.get("day")
            time = entry.get("time") or ""
            kind = entry.get("type") or "sale"
            cust = entry.get("customer")
            if kind == "supplier_order":
                content = (
                    f"SUPPLIER ORDER: {entry.get('items', {})} cost ${entry.get('cost')}"
                    f" ordered {entry.get('ordered_date')} arrives {entry.get('arrives_date')}"
                )
                channel = "supplier_order"
                target = "supplier"
            elif kind == "operating_fee":
                content = f"OPERATING FEE: ${entry.get('amount')}, cash after ${entry.get('cash_after')}"
                channel = "tool"
                target = None
            elif kind == "promotion":
                content = (
                    f"PROMOTION: {entry.get('item')} to {cust} for $0 (cost ${entry.get('cost')})"
                )
                channel = "sale"
                target = cust
            else:
                content = (
                    f"SALE: {entry.get('item')} to {cust} for ${entry.get('final_price')}"
                    f" (margin ${entry.get('margin')})"
                )
                channel = "sale"
                target = cust
            events.append(
                {
                    "idx": idx,
                    "ts": f"day {day} {time}".strip(),
                    "sim_ts_iso": None,
                    "type": "ledger_entry",
                    "agent_id": vid,
                    "step": None,
                    "target": target,
                    "action": kind,
                    "content": content,
                    "parameters": entry,
                    "success": True,
                    "channel": channel,
                    "source_path": "run_data.json",
                    "source_line": -1,
                }
            )
            idx += 1
    for cid, c in customers.items():
        for p in c.get("purchased", []) or []:
            content = (
                f"PURCHASE: {p.get('item')} from {p.get('vendor')} for ${p.get('price')}"
                f" on day {p.get('day')}"
            )
            events.append(
                {
                    "idx": idx,
                    "ts": f"day {p.get('day')}",
                    "sim_ts_iso": None,
                    "type": "purchase_entry",
                    "agent_id": cid,
                    "step": None,
                    "target": p.get("vendor"),
                    "action": "purchase",
                    "content": content,
                    "parameters": p,
                    "success": True,
                    "channel": "sale",
                    "source_path": "run_data.json",
                    "source_line": -1,
                }
            )
            idx += 1
    return events


# ---------------------------------------------------------------------------
# Final market state
# ---------------------------------------------------------------------------


def compute_final_market_state(run_data: dict | None) -> dict:
    if not run_data:
        return {}
    arts = run_data.get("scenario_artifacts") or {}
    return {
        "vendors": arts.get("vendors", {}),
        "customers": arts.get("customers", {}),
        "supplier": arts.get("supplier"),
        "market_economics": arts.get("market_economics", {}),
        "current_day": arts.get("current_day"),
        "phase": arts.get("phase"),
        "active_visits": arts.get("active_visits"),
    }


# ---------------------------------------------------------------------------
# Judgments + metrics resolution
# ---------------------------------------------------------------------------


def _label_tokens(*parts: str | None) -> list[str]:
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        out.extend(t for t in re.findall(r"[a-z0-9]+", p.lower()) if len(t) >= 3)
    # de-dup, preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def find_clean_judgments_dir(analysis_dir: Path, run_id: str | None, label: str | None) -> Path | None:
    """Search analysis_dir/clean_judgments/* for a folder whose contents
    likely match this run. Match by run_id short hash, then label token overlap."""
    cj_root = analysis_dir / "clean_judgments"
    if not cj_root.is_dir():
        return None
    candidates = [d for d in cj_root.iterdir() if d.is_dir() and not d.name.startswith("_")]
    if not candidates:
        return None
    short_id = (run_id or "")[:8].lower()
    tokens = _label_tokens(label, run_id)

    def score_dir(d: Path) -> int:
        score = 0
        name = d.name.lower()
        if short_id and short_id in name:
            score += 200
        for tok in tokens:
            if tok in name:
                score += 2
        # check first behavior file's _raw_judgment_path
        for f in d.glob("*_behavior.json"):
            try:
                obj = json.loads(f.read_text())
            except Exception:
                continue
            rid = (obj.get("_raw_judgment_path") or "").lower()
            if short_id and short_id in rid:
                score += 100
            for tok in tokens:
                if tok in rid:
                    score += 1
            break
        return score

    scored = sorted(((score_dir(d), d) for d in candidates), reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    # if exactly one candidate and we have nothing better, take it
    if len(candidates) == 1:
        return candidates[0]
    return None


def load_clean_judgments(cj_dir: Path) -> tuple[dict[str, dict], dict | None]:
    judgments: dict[str, dict] = {}
    run_health: dict | None = None
    for f in cj_dir.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            obj = json.loads(f.read_text())
        except Exception:
            continue
        if f.stem == "run_health":
            run_health = obj
            continue
        target = obj.get("target_agent") or f.stem.replace("_behavior", "")
        judgments[target] = {
            "summary": obj.get("summary"),
            "scores": obj.get("scores", {}),
            "highlights": obj.get("highlights", []),
            "coding_notes": obj.get("coding_notes"),
            "scenario": obj.get("scenario"),
            "_source": str(f),
        }
    return judgments, run_health


def find_metrics_csv(
    analysis_dir: Path,
    run_id: str | None,
    label: str | None,
    cj_dir_name: str | None,
) -> tuple[Path | None, Path | None]:
    metrics_dir = analysis_dir / "metrics"
    if not metrics_dir.is_dir():
        return None, None
    short = (run_id or "")[:8].lower()
    tokens = _label_tokens(label, run_id, cj_dir_name)
    candidates = list(metrics_dir.glob("*.csv"))

    def score(f: Path) -> int:
        name = f.name.lower()
        s = 0
        if short and short in name:
            s += 200
        for tok in tokens:
            if tok in name:
                s += 2
        return s

    scored = sorted(((score(f), f) for f in candidates), reverse=True)
    run_csv = None
    vendor_csv = None
    for sc, f in scored:
        if sc <= 0:
            break
        name = f.name.lower()
        if "vendor" in name and not vendor_csv:
            vendor_csv = f
        elif "vendor" not in name and not run_csv:
            run_csv = f
        if run_csv and vendor_csv:
            break
    return run_csv, vendor_csv


def load_metrics_csv(run_csv: Path | None, vendor_csv: Path | None) -> dict:
    out = {"run": None, "vendors": {}}
    if run_csv and run_csv.exists():
        with run_csv.open() as f:
            rows = list(csv.DictReader(f))
            if rows:
                out["run"] = rows[0]
    if vendor_csv and vendor_csv.exists():
        with vendor_csv.open() as f:
            rows = list(csv.DictReader(f))
            for r in rows:
                vid = r.get("vendor_id") or r.get("agent") or r.get("target_agent")
                if vid:
                    out["vendors"][vid] = r
    return out


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------


def build_viewer_data(
    rp: RunPaths,
    analysis_dir: Path | None,
    metrics_csv_override: Path | None,
    judgments_dir_override: Path | None,
    title: str | None,
) -> dict:
    run_data: dict | None = None
    if rp.run_data_path:
        run_data = load_json(rp.run_data_path)

    raw_event_pairs: list[tuple[int, dict, int]] = []
    source_path = ""
    if rp.event_log_jsonl:
        source_path = str(rp.event_log_jsonl)
        for line_no, ev in iter_event_log(rp.event_log_jsonl):
            raw_event_pairs.append((len(raw_event_pairs), ev, line_no))

    # Build agents from run_data + raw events (so classify can use registry)
    pre_agents = build_agent_registry(run_data, [ev for _, ev, _ in raw_event_pairs])

    if raw_event_pairs:
        events = normalize_events(raw_event_pairs, pre_agents, source_path)
    else:
        # legacy: synthesize from ledgers
        events = synthesize_from_ledgers(run_data or {}, pre_agents)

    # Re-derive agents (ensures world/supplier appear if only in events)
    agents = build_agent_registry(run_data, events)

    edges, market_timeline = derive_edges_and_market(events, agents)
    final_state = compute_final_market_state(run_data)

    # Sort market timeline by ts where possible
    market_timeline.sort(key=lambda e: (e["ts"] or ""))

    # Time bounds
    sim_times = [e["sim_ts_iso"] for e in events if e.get("sim_ts_iso")]
    sim_times.sort()
    time_bounds = {
        "min": sim_times[0] if sim_times else None,
        "max": sim_times[-1] if sim_times else None,
    }

    # Day boundaries from context_reset
    day_boundaries = []
    seen_resets: set[str] = set()
    for ev in events:
        if ev["channel"] == "context_reset":
            ts = ev.get("sim_ts_iso") or ev.get("ts")
            if ts and ts not in seen_resets:
                seen_resets.add(ts)
                day_boundaries.append({"ts": ts, "label": ev.get("content") or "context reset"})

    # Judgments + metrics
    judgments: dict[str, dict] = {}
    run_health: dict | None = None
    metrics: dict = {"run": None, "vendors": {}}

    cj_dir_used: Path | None = None
    if judgments_dir_override:
        cj_dir_used = judgments_dir_override
        judgments, run_health = load_clean_judgments(judgments_dir_override)
    elif analysis_dir:
        cj = find_clean_judgments_dir(analysis_dir, rp.run_id, rp.label)
        if cj:
            cj_dir_used = cj
            judgments, run_health = load_clean_judgments(cj)

    if analysis_dir:
        run_csv, vendor_csv = find_metrics_csv(
            analysis_dir, rp.run_id, rp.label, cj_dir_used.name if cj_dir_used else None
        )
        if metrics_csv_override:
            if "vendor" in metrics_csv_override.name:
                vendor_csv = metrics_csv_override
            else:
                run_csv = metrics_csv_override
        metrics = load_metrics_csv(run_csv, vendor_csv)
    elif metrics_csv_override:
        if "vendor" in metrics_csv_override.name:
            metrics = load_metrics_csv(None, metrics_csv_override)
        else:
            metrics = load_metrics_csv(metrics_csv_override, None)

    # Transcript lookup info (paths only, viewer can fetch on demand or we
    # inline truncated transcripts? — we just record the paths.)
    transcripts_index: dict[str, dict[str, str]] = {}
    if rp.agent_contexts_dir:
        for d in rp.agent_contexts_dir.iterdir():
            if not d.is_dir():
                continue
            entry = {}
            for fname in ("system_prompt.txt", "current_context.txt", "full_context.txt", "combined.txt"):
                fp = d / fname
                if fp.exists():
                    entry[fname.replace(".txt", "")] = str(fp)
            if entry:
                transcripts_index[d.name] = entry
    for aid, fp in rp.flat_transcripts.items():
        transcripts_index.setdefault(aid, {})["flat"] = str(fp)

    meta = {
        "run_id": (run_data or {}).get("run_id") or rp.run_id,
        "run_label": rp.label,
        "scenario": "llm_bazaar",
        "model": (run_data or {}).get("model"),
        "provider": (run_data or {}).get("provider"),
        "persona": (run_data or {}).get("persona"),
        "persona_targets": (run_data or {}).get("persona_targets"),
        "status": (run_data or {}).get("status"),
        "completion_reason": ((run_data or {}).get("scenario_artifacts") or {}).get("market_economics", {}).get("completion_reason"),
        "simulation_days": ((run_data or {}).get("scenario_artifacts") or {}).get("market_economics", {}).get("simulation_days"),
        "source_run_dir": str(rp.run_dir),
        "title": title or rp.label or "Bazaar Run",
        "time_bounds": time_bounds,
        "day_boundaries": day_boundaries,
        "has_event_log": rp.event_log_jsonl is not None,
        "has_clean_judgments": bool(judgments),
        "has_run_health": run_health is not None,
        "judgments_source": str(cj_dir_used) if cj_dir_used else None,
        "analysis_dir": str(analysis_dir) if analysis_dir else None,
    }

    return {
        "meta": meta,
        "agents": list(agents.values()),
        "events": events,
        "edges": edges,
        "marketTimeline": market_timeline,
        "finalMarketState": final_state,
        "judgments": judgments,
        "runHealth": run_health,
        "metrics": metrics,
        "transcripts_index": transcripts_index,
    }


# ---------------------------------------------------------------------------
# HTML emission
# ---------------------------------------------------------------------------


def render_html(viewer_data: dict, template_path: Path, data_out: Path | None) -> str:
    template = template_path.read_text()
    if data_out:
        data_out.parent.mkdir(parents=True, exist_ok=True)
        data_out.write_text(json.dumps(viewer_data))
        # leave a small loader stub
        json_blob = json.dumps({"__sidecar__": str(data_out.name)})
    else:
        json_blob = json.dumps(viewer_data)
    # Inline the JSON directly. Escape </ to avoid breaking script tags.
    safe = json_blob.replace("</", "<\\/")
    return template.replace("__VIEWER_DATA_JSON__", safe)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Render an LLM Bazaar run viewer.")
    p.add_argument("--run", required=True, help="Run directory or run_data.json")
    p.add_argument("--analysis-dir", default=None, help="Analysis dir containing clean_judgments/ and metrics/")
    p.add_argument("--judgments-dir", default=None, help="Pin a specific clean_judgments/<label>/ folder")
    p.add_argument("--metrics-csv", default=None, help="Override metrics CSV path")
    p.add_argument("--out", default=None, help="Output HTML path (default: <run_dir>/viewer.html)")
    p.add_argument("--data-out", default=None, help="Optional sidecar viewer_data.json path")
    p.add_argument("--title", default=None)
    args = p.parse_args(argv)

    rp = resolve_run(args.run)
    analysis_dir = Path(args.analysis_dir).expanduser().resolve() if args.analysis_dir else None
    metrics_csv = Path(args.metrics_csv).expanduser().resolve() if args.metrics_csv else None
    judgments_dir = Path(args.judgments_dir).expanduser().resolve() if args.judgments_dir else None

    viewer_data = build_viewer_data(rp, analysis_dir, metrics_csv, judgments_dir, args.title)

    if not TEMPLATE_PATH.exists():
        raise SystemExit(f"viewer template missing: {TEMPLATE_PATH}")

    out_path = Path(args.out).expanduser().resolve() if args.out else (rp.run_dir / "viewer.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data_out = Path(args.data_out).expanduser().resolve() if args.data_out else None

    html = render_html(viewer_data, TEMPLATE_PATH, data_out)
    out_path.write_text(html)
    print(f"viewer: {out_path}")
    print(f"events: {len(viewer_data['events'])}  edges: {len(viewer_data['edges'])}  agents: {len(viewer_data['agents'])}")
    print(f"clean_judgments: {len(viewer_data['judgments'])}  run_health: {bool(viewer_data['runHealth'])}")
    if data_out:
        print(f"data: {data_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
