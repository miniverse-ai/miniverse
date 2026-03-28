"""HTML transcript viewer for Miniverse simulation logs.

Parses .log files from Miniverse simulations and renders self-contained HTML
with per-agent timeline panels, a communications sidebar, and tick-by-tick
navigation. Dark theme adapted from Helm's viewer aesthetic.

Usage:
    from miniverse.viewer import render_log
    render_log("demo/valentines/logs/valentines_llm_20260216_232436.log")

CLI:
    python -m miniverse.viewer path/to/simulation.log [-o output.html]
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── ANSI stripping ──────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _escape(text: str) -> str:
    return html.escape(str(text))


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass
class AgentIdentity:
    full_name: str
    agent_id: str
    role: str
    description: str = ""


@dataclass
class Communication:
    tick: int
    sender_id: str
    sender_name: str
    recipient_id: str
    message: str
    reasoning: str = ""


@dataclass
class AgentAction:
    tick: int
    agent_id: str
    agent_name: str
    action_type: str
    target: str = ""
    reasoning: str = ""
    message: str = ""  # for communicate actions
    comm_to: str = ""


@dataclass
class Reflection:
    tick: int
    agent_id: str
    agent_name: str
    importance: int
    text: str


@dataclass
class TickSummary:
    tick: int
    total_ticks: int
    resources: str = ""  # e.g. "Date=14 Feb, Current Time=12 am"
    actions: list[str] = field(default_factory=list)


@dataclass
class SimulationData:
    title: str = ""
    description: str = ""
    scene: str = ""
    num_agents: int = 0
    num_ticks: int = 0
    seed: int = 0
    cognition_mode: str = ""
    rules_class: str = ""
    agents: dict[str, AgentIdentity] = field(default_factory=dict)
    agent_order: list[str] = field(default_factory=list)
    actions: list[AgentAction] = field(default_factory=list)
    communications: list[Communication] = field(default_factory=list)
    reflections: list[Reflection] = field(default_factory=list)
    tick_summaries: list[TickSummary] = field(default_factory=list)


# ── Parser ──────────────────────────────────────────────────────────────────

# Map from display name -> agent_id, built during parse
_name_to_id: dict[str, str] = {}


def _parse_log(log_path: Path) -> SimulationData:
    """Parse a Miniverse .log file into structured data."""
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    text = _strip_ansi(raw)
    lines = text.split("\n")

    sim = SimulationData()
    name_to_id: dict[str, str] = {}
    current_tick = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── Run Setup metadata ──
        if stripped.startswith("- Title:"):
            sim.title = stripped.removeprefix("- Title:").strip()
        elif stripped.startswith("- Description:"):
            desc = stripped.removeprefix("- Description:").strip()
            # Multi-line description
            j = i + 1
            while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith("-"):
                desc += " " + lines[j].strip()
                j += 1
            sim.description = desc
        elif stripped.startswith("- Scene:"):
            scene = stripped.removeprefix("- Scene:").strip()
            j = i + 1
            while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith("-") and not lines[j].strip().startswith("Run configuration"):
                scene += " " + lines[j].strip()
                j += 1
            sim.scene = scene
        elif stripped.startswith("- Agents:") and "Ticks:" not in stripped:
            try:
                sim.num_agents = int(stripped.removeprefix("- Agents:").strip())
            except ValueError:
                pass
        elif stripped.startswith("- Ticks:"):
            try:
                sim.num_ticks = int(stripped.removeprefix("- Ticks:").strip())
            except ValueError:
                pass
        elif stripped.startswith("- Seed:"):
            try:
                sim.seed = int(stripped.removeprefix("- Seed:").strip())
            except ValueError:
                pass
        elif stripped.startswith("- Cognition mode:"):
            sim.cognition_mode = stripped.removeprefix("- Cognition mode:").strip()
        elif stripped.startswith("- Rules class:"):
            sim.rules_class = stripped.removeprefix("- Rules class:").strip()

        # ── Agent identity lines ──
        # Format: "  - Full Name [agent_id] (role)"
        m = re.match(r"^-\s+(.+?)\s+\[(\w+)\]\s+\(([^)]+)\)", stripped)
        if m and "Agent identity prompts" not in stripped:
            full_name, agent_id, role = m.group(1), m.group(2), m.group(3)
            if agent_id not in sim.agents:
                # Read the description block that follows
                desc_lines = []
                j = i + 1
                while j < len(lines):
                    next_stripped = lines[j].strip()
                    # Stop at next agent definition or section
                    if re.match(r"^\s+-\s+.+?\s+\[\w+\]\s+\(\w+\)", next_stripped):
                        break
                    if next_stripped.startswith("Shared executor") or next_stripped.startswith("Available actions"):
                        break
                    if next_stripped:
                        desc_lines.append(next_stripped)
                    j += 1

                sim.agents[agent_id] = AgentIdentity(
                    full_name=full_name,
                    agent_id=agent_id,
                    role=role,
                    description="\n".join(desc_lines),
                )
                if agent_id not in sim.agent_order:
                    sim.agent_order.append(agent_id)
                name_to_id[full_name] = agent_id

        # ── Tick markers ──
        tick_m = re.match(r"=== Tick (\d+)/(\d+) ===", stripped)
        if tick_m:
            current_tick = int(tick_m.group(1))
            sim.num_ticks = max(sim.num_ticks, int(tick_m.group(2)))

        # ── LLM Executor lines (action decisions) ──
        exec_m = re.match(r"\[LLM Executor\]\s+(\w+):\s+(\w+)(?:\s+target=(.+))?", stripped)
        if exec_m:
            agent_id = exec_m.group(1)
            action_type = exec_m.group(2)
            target = (exec_m.group(3) or "").strip()
            agent_name = _agent_name(sim, agent_id)

            # Look ahead for comm_to, reasoning, message
            reasoning = ""
            message = ""
            comm_to = ""
            j = i + 1
            while j < len(lines) and j < i + 15:
                ahead = _strip_ansi(lines[j]).strip()
                if ahead.startswith("communication.to="):
                    comm_to = ahead.removeprefix("communication.to=").strip()
                elif ahead.startswith("Reasoning:"):
                    reasoning = ahead.removeprefix("Reasoning:").strip()
                elif ahead.startswith("Message:"):
                    msg = ahead.removeprefix("Message:").strip()
                    # Multi-line messages in quotes
                    if msg.startswith('"') and not msg.endswith('"'):
                        k = j + 1
                        while k < len(lines) and k < j + 20:
                            cont = _strip_ansi(lines[k]).strip()
                            msg += " " + cont
                            if cont.endswith('"'):
                                break
                            k += 1
                    message = msg.strip('"')
                elif ahead.startswith("[") and ("Got action" in ahead or "Executor" in ahead):
                    break
                elif ahead.startswith("=== Tick"):
                    break
                j += 1

            action = AgentAction(
                tick=current_tick,
                agent_id=agent_id,
                agent_name=agent_name,
                action_type=action_type,
                target=target,
                reasoning=reasoning,
                message=message,
                comm_to=comm_to,
            )
            sim.actions.append(action)

            # Also track communications separately
            if action_type == "communicate" and message:
                recipient_id = comm_to or target
                sim.communications.append(Communication(
                    tick=current_tick,
                    sender_id=agent_id,
                    sender_name=agent_name,
                    recipient_id=recipient_id,
                    message=message,
                    reasoning=reasoning,
                ))

        # ── Reflections ──
        refl_m = re.match(r"\[Reflection\]\s+(.+?):\s+(\d+)\s+reflection", stripped)
        if refl_m:
            agent_display = refl_m.group(1)
            agent_id = name_to_id.get(agent_display, agent_display.lower().replace(" ", "_"))
            # Read following reflection entries
            j = i + 1
            while j < len(lines):
                rline = _strip_ansi(lines[j]).strip()
                rm = re.match(r"-\s+\((\d+)/10\)\s+(.*)", rline)
                if rm:
                    importance = int(rm.group(1))
                    rtext = rm.group(2)
                    # Multi-line reflection
                    k = j + 1
                    while k < len(lines):
                        cont = _strip_ansi(lines[k]).strip()
                        if not cont or cont.startswith("-") or cont.startswith("[") or cont.startswith("==="):
                            break
                        rtext += " " + cont
                        k += 1
                    sim.reflections.append(Reflection(
                        tick=current_tick,
                        agent_id=agent_id,
                        agent_name=agent_display,
                        importance=importance,
                        text=rtext,
                    ))
                    j = k
                    continue
                elif rline.startswith("[") or rline.startswith("===") or rline.startswith("Resources:"):
                    break
                j += 1

        # ── Tick summary (Resources + Actions block) ──
        if stripped.startswith("Resources:"):
            resources = stripped.removeprefix("Resources:").strip()
            summary = TickSummary(tick=current_tick, total_ticks=sim.num_ticks, resources=resources)
            j = i + 1
            if j < len(lines) and _strip_ansi(lines[j]).strip() == "Actions:":
                j += 1
                while j < len(lines):
                    aline = _strip_ansi(lines[j]).strip()
                    if aline.startswith("- "):
                        action_text = aline.removeprefix("- ").strip()
                        # Multi-line action summary
                        k = j + 1
                        while k < len(lines):
                            cont = _strip_ansi(lines[k]).strip()
                            if not cont or cont.startswith("- ") or cont.startswith("===") or cont.startswith("["):
                                break
                            action_text += " " + cont
                            k += 1
                        summary.actions.append(action_text)
                        j = k
                        continue
                    elif aline.startswith("===") or aline.startswith("[") or not aline:
                        break
                    j += 1
            sim.tick_summaries.append(summary)

        i += 1

    return sim


def _agent_name(sim: SimulationData, agent_id: str) -> str:
    if agent_id in sim.agents:
        return sim.agents[agent_id].full_name
    return agent_id


# ── HTML Rendering ──────────────────────────────────────────────────────────

_ACTION_COLORS = {
    "communicate": "#4ade80",  # green
    "move_to": "#60a5fa",     # blue
    "investigate": "#c084fc",  # purple
    "work": "#a3a3a3",        # neutral gray
    "rest": "#fbbf24",        # amber
    "monitor": "#22d3ee",     # cyan
}

_ACTION_ICONS = {
    "communicate": "\U0001F4AC",  # speech bubble
    "move_to": "\U0001F6B6",     # walking
    "investigate": "\U0001F50D",  # magnifying glass
    "work": "\U0001F6E0",        # wrench
    "rest": "\U0001F4A4",        # zzz
    "monitor": "\U0001F441",     # eye
}


def _build_agent_panels(sim: SimulationData) -> str:
    """Build per-agent timeline panels."""
    panels = []

    for agent_id in sim.agent_order:
        agent = sim.agents.get(agent_id)
        if not agent:
            continue

        # Collect events for this agent: actions + reflections, sorted by tick
        events_html = []
        last_tick = 0

        # Group actions and reflections by tick
        agent_actions = [a for a in sim.actions if a.agent_id == agent_id]
        agent_reflections = [r for r in sim.reflections if r.agent_id == agent_id]

        # Merge into tick-ordered stream
        tick_events: dict[int, dict] = {}
        for a in agent_actions:
            tick_events.setdefault(a.tick, {"actions": [], "reflections": []})
            tick_events[a.tick]["actions"].append(a)
        for r in agent_reflections:
            tick_events.setdefault(r.tick, {"actions": [], "reflections": []})
            tick_events[r.tick]["reflections"].append(r)

        for tick in sorted(tick_events.keys()):
            if tick > 0:
                events_html.append(
                    f"<div class='tick-marker'>"
                    f"<span class='tick-num'>Tick {tick}</span>"
                    f"</div>"
                )

            for action in tick_events[tick]["actions"]:
                color = _ACTION_COLORS.get(action.action_type, "#a3a3a3")
                icon = _ACTION_ICONS.get(action.action_type, "\u2022")

                target_str = ""
                if action.target:
                    target_name = _agent_name(sim, action.target)
                    if target_name == action.target:
                        target_str = f" <span class='action-target'>\u2192 {_escape(action.target)}</span>"
                    else:
                        target_str = f" <span class='action-target'>\u2192 {_escape(target_name)}</span>"

                action_html = (
                    f"<div class='ev' data-type='{_escape(action.action_type)}'>"
                    f"<div class='ev-head' style='border-left-color: {color}'>"
                    f"<span class='action-icon'>{icon}</span>"
                    f"<span class='action-type' style='color: {color}'>{_escape(action.action_type)}</span>"
                    f"{target_str}"
                    f"</div>"
                )

                if action.reasoning:
                    action_html += f"<div class='ev-reasoning'>{_escape(action.reasoning)}</div>"

                if action.message:
                    recipient_name = _agent_name(sim, action.comm_to or action.target)
                    action_html += (
                        f"<div class='ev-message'>"
                        f"<span class='msg-to'>to {_escape(recipient_name)}</span>"
                        f"<div class='msg-text'>{_escape(action.message)}</div>"
                        f"</div>"
                    )

                action_html += "</div>"
                events_html.append(action_html)

            for refl in tick_events[tick]["reflections"]:
                importance_cls = "refl-high" if refl.importance >= 7 else "refl-mid" if refl.importance >= 5 else "refl-low"
                text = _escape(refl.text)
                if len(refl.text) > 300:
                    events_html.append(
                        f"<div class='ev ev-reflection {importance_cls}'>"
                        f"<div class='refl-head'>"
                        f"<span class='refl-icon'>\U0001F4D3</span>"
                        f"<span class='refl-label'>reflection</span>"
                        f"<span class='refl-score'>{refl.importance}/10</span>"
                        f"</div>"
                        f"<div class='refl-text refl-clipped'>"
                        f"<div class='refl-content'>{text}</div>"
                        f"</div></div>"
                    )
                else:
                    events_html.append(
                        f"<div class='ev ev-reflection {importance_cls}'>"
                        f"<div class='refl-head'>"
                        f"<span class='refl-icon'>\U0001F4D3</span>"
                        f"<span class='refl-label'>reflection</span>"
                        f"<span class='refl-score'>{refl.importance}/10</span>"
                        f"</div>"
                        f"<div class='refl-text'>{text}</div>"
                        f"</div>"
                    )

        n_actions = len(agent_actions)
        n_comms = sum(1 for a in agent_actions if a.action_type == "communicate")

        panels.append(
            f"<div class='panel active' data-agent='{_escape(agent_id)}'>"
            f"<div class='panel-head'>"
            f"<div class='panel-info'>"
            f"<span class='panel-label'>{_escape(agent.full_name)}</span>"
            f"<span class='panel-role'>{_escape(agent.role)}</span>"
            f"</div>"
            f"<div class='panel-stats'>"
            f"<span class='panel-count' title='actions'>{n_actions}</span>"
            f"<span class='panel-comms' title='communications'>\U0001F4AC {n_comms}</span>"
            f"</div>"
            f"</div>"
            f"<div class='panel-scroll'>{''.join(events_html)}</div></div>"
        )

    return "".join(panels)


def _build_communications_sidebar(sim: SimulationData) -> str:
    """Build the communications log sidebar."""
    if not sim.communications:
        return (
            "<div id='coord' class='coord'>"
            "<div class='coord-empty'>No communications</div></div>"
        )

    rows = []
    last_tick = 0
    for comm in sim.communications:
        if comm.tick != last_tick:
            rows.append(
                f"<div class='comm-tick-marker'>"
                f"<span>Tick {comm.tick}</span></div>"
            )
            last_tick = comm.tick

        sender_name = _agent_name(sim, comm.sender_id)
        recipient_name = _agent_name(sim, comm.recipient_id)

        msg_text = _escape(comm.message)
        if len(comm.message) > 200:
            content = (
                f"<div class='comm-msg comm-clipped'>"
                f"<div class='comm-msg-inner'>{msg_text}</div></div>"
            )
        else:
            content = f"<div class='comm-msg'>{msg_text}</div>"

        rows.append(
            f"<div class='comm-entry' data-sender='{_escape(comm.sender_id)}' "
            f"data-recipient='{_escape(comm.recipient_id)}'>"
            f"<div class='comm-route'>"
            f"<span class='comm-sender'>{_escape(sender_name)}</span>"
            f"<span class='comm-arrow'>\u2192</span>"
            f"<span class='comm-recipient'>{_escape(recipient_name)}</span>"
            f"</div>"
            f"{content}"
            f"</div>"
        )

    return (
        f"<div id='coord' class='coord'>"
        f"<div class='coord-head'>"
        f"<span>communications</span>"
        f"<span class='coord-count'>{len(sim.communications)}</span>"
        f"</div>"
        f"<div class='coord-scroll'>{''.join(rows)}</div></div>"
    )


def _build_left_sidebar(sim: SimulationData) -> str:
    """Build the metadata section of the left sidebar."""
    parts = []

    # Title + description
    parts.append(
        f"<div class='left-section'>"
        f"<div class='left-label'>scenario</div>"
        f"<div class='left-title'>{_escape(sim.title)}</div>"
    )
    if sim.description:
        desc = _escape(sim.description)
        if len(sim.description) > 200:
            parts.append(
                f"<div class='meta-detail meta-clipped'>"
                f"<div class='meta-detail-inner'>{desc}</div></div>"
            )
        else:
            parts.append(f"<div class='meta-detail'>{desc}</div>")
    parts.append("</div>")

    # Config
    parts.append(
        f"<div class='left-section'>"
        f"<div class='left-label'>configuration</div>"
    )
    config_rows = [
        ("agents", str(sim.num_agents)),
        ("ticks", str(sim.num_ticks)),
        ("seed", str(sim.seed)),
        ("cognition", sim.cognition_mode),
        ("rules", sim.rules_class),
    ]
    for key, val in config_rows:
        if val and val != "0":
            parts.append(
                f"<div class='meta-row'>"
                f"<span class='meta-key'>{_escape(key)}</span>"
                f"<span class='meta-val'>{_escape(val)}</span></div>"
            )
    parts.append("</div>")

    # Scene
    if sim.scene:
        scene = _escape(sim.scene)
        parts.append(
            f"<div class='left-section'>"
            f"<div class='left-label'>scene</div>"
            f"<div class='meta-detail'>{scene}</div>"
            f"</div>"
        )

    # Agent roster
    parts.append(
        f"<div class='left-section'>"
        f"<div class='left-label'>agents</div>"
    )
    for agent_id in sim.agent_order:
        agent = sim.agents[agent_id]
        parts.append(
            f"<div class='agent-roster-entry'>"
            f"<span class='agent-roster-name'>{_escape(agent.full_name)}</span>"
            f"<span class='agent-roster-role'>{_escape(agent.role)}</span>"
            f"</div>"
        )
    parts.append("</div>")

    return "".join(parts)


def render_html(sim: SimulationData) -> str:
    """Render parsed simulation data as a self-contained HTML file."""
    panels_html = _build_agent_panels(sim)
    coord_html = _build_communications_sidebar(sim)
    left_html = _build_left_sidebar(sim)

    # Header toggles
    toggles = []
    for agent_id in sim.agent_order:
        agent = sim.agents[agent_id]
        toggles.append(
            f"<label class='toggle'>"
            f"<input type='checkbox' data-agent='{_escape(agent_id)}' checked>"
            f" {_escape(agent.full_name)}</label>"
        )

    header_html = (
        f"<div class='hdr-title'>{_escape(sim.title)}</div>"
        f"<div class='hdr-controls'>"
        f"<label class='toggle comms-toggle'>"
        f"<input type='checkbox' id='comms-only'> comms only</label>"
        f"</div>"
        f"<div class='hdr-toggles'>{''.join(toggles)}</div>"
    )

    return _TEMPLATE.format(
        title=_escape(sim.title),
        header=header_html,
        left_sidebar=left_html,
        coordination=coord_html,
        panels=panels_html,
        agent_ids_json=json.dumps(sim.agent_order),
    )


# ── Main entry point ────────────────────────────────────────────────────────


def render_log(log_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Parse a Miniverse log and render to a self-contained HTML file.

    Args:
        log_path: Path to the .log file.
        output_path: Where to write the HTML. Defaults to same directory
                     with .html extension.

    Returns:
        Path to the generated HTML file.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    sim = _parse_log(log_path)

    if output_path is None:
        output_path = log_path.with_suffix(".html")
    else:
        output_path = Path(output_path)

    html_content = render_html(sim)
    output_path.write_text(html_content, encoding="utf-8")
    return output_path


# ── HTML Template ───────────────────────────────────────────────────────────

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Miniverse</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap');

:root {{
  --bg: #0c0c0c;
  --bg-1: #121212;
  --bg-2: #181818;
  --bg-3: #1e1e1e;
  --border: #2a2a2a;
  --border-strong: #363636;
  --text: #d4d4d4;
  --text-dim: #8a8a8a;
  --text-faint: #5a5a5a;
  --text-bright: #f0f0f0;
  --green: #4ade80;
  --green-bg: #1a2e1a;
  --blue: #60a5fa;
  --blue-bg: #1a1e2e;
  --purple: #c084fc;
  --amber: #fbbf24;
  --cyan: #22d3ee;
  --red: #f87171;
  --mono: 'IBM Plex Mono', 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
  --sans: 'IBM Plex Sans', -apple-system, system-ui, sans-serif;
  --radius: 3px;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ height: 100%; overflow: hidden; }}

body {{
  font-family: var(--sans);
  background: var(--bg);
  color: var(--text);
  display: flex;
  flex-direction: column;
}}

/* Scrollbar */
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--text-faint); }}
* {{ scrollbar-width: thin; scrollbar-color: var(--border) transparent; }}

pre {{
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.6;
}}

code {{
  font-family: var(--mono);
  font-size: 11px;
  background: var(--bg-3);
  padding: 1px 4px;
  border-radius: 2px;
}}

/* ─── HEADER BAR ─── */
.hdr {{
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 16px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  min-height: 40px;
}}
.hdr-title {{
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-bright);
  white-space: nowrap;
}}
.hdr-controls {{
  display: flex;
  gap: 10px;
}}
.hdr-toggles {{
  display: flex;
  gap: 10px;
  margin-left: auto;
  flex-shrink: 0;
  flex-wrap: wrap;
}}
.toggle {{
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  user-select: none;
}}
.toggle input {{ cursor: pointer; accent-color: var(--text-dim); }}
.toggle:has(input:checked) {{ color: var(--text-bright); }}
.comms-toggle {{
  background: var(--bg-3);
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
}}
.comms-toggle:has(input:checked) {{
  background: var(--green-bg);
  border-color: var(--green);
  color: var(--green);
}}

/* ─── MAIN LAYOUT ─── */
.workspace {{ flex: 1; display: flex; overflow: hidden; }}

/* ─── LEFT SIDEBAR ─── */
.left {{
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 2px solid var(--border-strong);
  background: var(--bg);
  overflow: hidden;
}}
.left-meta {{
  flex-shrink: 0;
  border-bottom: 1px solid var(--border);
  overflow-y: auto;
  max-height: 45vh;
}}
.left-section {{
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}}
.left-label {{
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 500;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 6px;
}}
.left-title {{
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-bright);
  word-break: break-word;
  margin-bottom: 6px;
}}
.meta-row {{
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 2px 0;
  gap: 8px;
}}
.meta-key {{
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
  flex-shrink: 0;
}}
.meta-val {{
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-bright);
  text-align: right;
}}
.meta-detail {{
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 4px;
  line-height: 1.5;
}}
.meta-clipped .meta-detail-inner {{
  max-height: 60px;
  overflow: hidden;
  position: relative;
}}
.meta-clipped .meta-detail-inner::after {{
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 24px;
  background: linear-gradient(transparent, var(--bg));
  pointer-events: none;
}}
.meta-clipped {{ cursor: pointer; }}
.meta-expanded .meta-detail-inner {{
  max-height: none;
  overflow: visible;
}}
.meta-expanded .meta-detail-inner::after {{ display: none; }}

/* Agent roster */
.agent-roster-entry {{
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 11px;
}}
.agent-roster-name {{
  font-family: var(--mono);
  color: var(--text);
  font-weight: 500;
}}
.agent-roster-role {{
  font-family: var(--mono);
  color: var(--text-faint);
  font-size: 10px;
}}

/* ─── COMMUNICATIONS SIDEBAR ─── */
.coord {{
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}}
.coord-head {{
  padding: 10px 14px;
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 500;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 1px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}}
.coord-count {{
  background: var(--green-bg);
  color: var(--green);
  padding: 1px 7px;
  border-radius: 8px;
  font-size: 10px;
}}
.coord-scroll {{
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}}
.coord-empty {{
  padding: 20px;
  text-align: center;
  color: var(--text-faint);
  font-size: 12px;
}}

/* Communication entries */
.comm-tick-marker {{
  padding: 6px 14px;
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  background: var(--bg-2);
  border-bottom: 1px solid var(--border);
}}
.comm-entry {{
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  border-left: 2px solid var(--green);
}}
.comm-entry:hover {{ background: var(--bg-1); }}
.comm-route {{
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}}
.comm-sender {{
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--green);
}}
.comm-arrow {{
  color: var(--text-faint);
  font-size: 11px;
}}
.comm-recipient {{
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 500;
  color: var(--text);
}}
.comm-msg {{
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
}}
.comm-clipped .comm-msg-inner {{
  max-height: 60px;
  overflow: hidden;
  position: relative;
}}
.comm-clipped .comm-msg-inner::after {{
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 30px;
  background: linear-gradient(transparent, var(--bg));
  pointer-events: none;
}}
.comm-clipped {{ cursor: pointer; }}
.comm-expanded .comm-msg-inner {{
  max-height: none;
  overflow: visible;
}}
.comm-expanded .comm-msg-inner::after {{ display: none; }}

/* ─── AGENT GRID ─── */
.grid {{
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  grid-auto-rows: minmax(0, 1fr);
  overflow: hidden;
  min-width: 0;
}}

/* ─── AGENT PANEL ─── */
.panel {{
  display: none;
  flex-direction: column;
  border-right: 2px solid var(--border-strong);
  border-bottom: 2px solid var(--border-strong);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}}
.panel.active {{ display: flex; }}
.panel-head {{
  padding: 10px 14px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}}
.panel-info {{ display: flex; align-items: baseline; gap: 8px; }}
.panel-label {{
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-bright);
}}
.panel-role {{
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.panel-stats {{
  display: flex;
  gap: 10px;
  align-items: center;
}}
.panel-count {{
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-dim);
  background: var(--bg-3);
  padding: 1px 7px;
  border-radius: 8px;
}}
.panel-comms {{
  font-family: var(--mono);
  font-size: 10px;
  color: var(--green);
}}
.panel-scroll {{
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}}

/* ─── TICK MARKERS ─── */
.tick-marker {{
  padding: 6px 14px;
  background: var(--bg-2);
  border-bottom: 1px solid var(--border);
  border-top: 1px solid var(--border);
}}
.tick-num {{
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}

/* ─── EVENTS ─── */
.ev {{
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
}}
.ev:hover {{ background: var(--bg-1); }}
.ev-head {{
  display: flex;
  align-items: center;
  gap: 6px;
  border-left: 3px solid var(--text-faint);
  padding-left: 8px;
  margin-bottom: 4px;
}}
.action-icon {{ font-size: 13px; }}
.action-type {{
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.action-target {{
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-dim);
}}
.ev-reasoning {{
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.5;
  margin: 4px 0 4px 11px;
}}

/* ─── COMMUNICATION MESSAGES (in agent panels) ─── */
.ev-message {{
  margin: 6px 0 2px 11px;
  padding: 8px 10px;
  background: var(--green-bg);
  border: 1px solid #2a4a2a;
  border-radius: var(--radius);
  border-left: 3px solid var(--green);
}}
.msg-to {{
  font-family: var(--mono);
  font-size: 10px;
  color: var(--green);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  display: block;
  margin-bottom: 4px;
}}
.msg-text {{
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
}}

/* ─── REFLECTIONS ─── */
.ev-reflection {{
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-2);
}}
.ev-reflection:hover {{ background: var(--bg-3); }}
.refl-head {{
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}}
.refl-icon {{ font-size: 12px; }}
.refl-label {{
  font-family: var(--mono);
  font-size: 10px;
  color: var(--purple);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.refl-score {{
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-faint);
  margin-left: auto;
}}
.refl-text {{
  font-size: 12px;
  font-style: italic;
  color: var(--text-dim);
  line-height: 1.6;
}}
.refl-high .refl-text {{ color: var(--text); }}
.refl-high .refl-label {{ color: var(--amber); }}
.refl-clipped .refl-content {{
  max-height: 60px;
  overflow: hidden;
  position: relative;
}}
.refl-clipped .refl-content::after {{
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 30px;
  background: linear-gradient(transparent, var(--bg-2));
  pointer-events: none;
}}
.refl-clipped {{ cursor: pointer; }}
.refl-expanded .refl-content {{
  max-height: none;
  overflow: visible;
}}
.refl-expanded .refl-content::after {{ display: none; }}

/* ─── COMMS-ONLY MODE ─── */
body.comms-only .ev:not([data-type="communicate"]) {{ display: none; }}
body.comms-only .ev-reflection {{ display: none; }}
body.comms-only .tick-marker {{ display: none; }}

/* Show tick markers that precede visible comms - handled by JS */
body.comms-only .tick-marker.has-comm {{ display: block; }}
</style>
</head>
<body>
<header class="hdr">
{header}
</header>
<div class="workspace">
<div class="left">
<div class="left-meta">
{left_sidebar}
</div>
{coordination}
</div>
<div class="grid" id="grid">
{panels}
</div>
</div>
<script>
const agentIds = {agent_ids_json};
const panels = document.querySelectorAll('.panel');
const checkboxes = document.querySelectorAll('.toggle input[data-agent]');
const commsOnly = document.getElementById('comms-only');

function updateGrid() {{
  panels.forEach(p => {{
    const aid = p.dataset.agent;
    const cb = document.querySelector(`input[data-agent="${{aid}}"]`);
    p.classList.toggle('active', cb && cb.checked);
  }});
  const visible = document.querySelectorAll('.panel.active').length;
  const grid = document.getElementById('grid');
  if (visible <= 1) {{
    grid.style.gridTemplateColumns = '1fr';
  }} else if (visible <= 2) {{
    grid.style.gridTemplateColumns = 'repeat(2, 1fr)';
  }} else if (visible <= 4) {{
    grid.style.gridTemplateColumns = 'repeat(2, 1fr)';
  }} else if (visible <= 6) {{
    grid.style.gridTemplateColumns = 'repeat(3, 1fr)';
  }} else {{
    grid.style.gridTemplateColumns = 'repeat(auto-fit, minmax(300px, 1fr))';
  }}
}}

checkboxes.forEach(cb => cb.addEventListener('change', updateGrid));

// Communications-only toggle
commsOnly.addEventListener('change', () => {{
  document.body.classList.toggle('comms-only', commsOnly.checked);
}});

// Click-to-expand on clipped elements
document.addEventListener('click', (e) => {{
  const clipped = e.target.closest('.comm-clipped, .refl-clipped, .meta-clipped');
  if (clipped) {{
    clipped.classList.toggle('comm-expanded');
    clipped.classList.toggle('refl-expanded');
    clipped.classList.toggle('meta-expanded');
  }}
}});

updateGrid();
</script>
</body>
</html>
"""


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m miniverse.viewer <log_file> [-o output.html]")
        sys.exit(1)

    log_file = sys.argv[1]
    out_file = None
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            out_file = sys.argv[idx + 1]

    result = render_log(log_file, out_file)
    print(f"Rendered: {result}")
