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

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

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
    scenario_path: str = ""


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
        if stripped.startswith("- Scenario file:"):
            sim.scenario_path = stripped.removeprefix("- Scenario file:").strip()
        elif stripped.startswith("- Title:"):
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


_AGENT_COLORS = [
    "#4ade80", "#60a5fa", "#c084fc", "#fbbf24",
    "#22d3ee", "#f87171", "#fb923c", "#a78bfa", "#34d399",
]


def render_html(
    sim: SimulationData,
    locations: list[dict] | None = None,
    starting_locations: dict[str, str] | None = None,
    adjacency: dict[str, list[str]] | None = None,
) -> str:
    """Render parsed simulation data as a self-contained HTML file.

    Args:
        sim: Parsed simulation data.
        locations: List of dicts with 'id' and 'name' keys for environment locations.
        starting_locations: Map of agent_id -> location_id at tick 0.
        adjacency: Map of location_id -> list of adjacent location_ids.
    """
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
        f"<label class='toggle network-toggle'>"
        f"<input type='checkbox' id='network-view'> network</label>"
        f"</div>"
        f"<div class='hdr-toggles'>{''.join(toggles)}</div>"
    )

    # Serialize graph data for the network view
    agents_data = []
    color_map = {}
    for idx, agent_id in enumerate(sim.agent_order):
        agent = sim.agents[agent_id]
        color = _AGENT_COLORS[idx % len(_AGENT_COLORS)]
        color_map[agent_id] = color
        agents_data.append({
            "id": agent_id,
            "name": agent.full_name.split()[0],  # first name only
            "fullName": agent.full_name,
            "role": agent.role,
            "color": color,
        })

    comms_data = []
    for c in sim.communications:
        comms_data.append({
            "tick": c.tick,
            "from": c.sender_id,
            "to": c.recipient_id,
            "msg": c.message[:120],
        })

    # Build actions data for the network animation
    actions_data = []
    for a in sim.actions:
        actions_data.append({
            "tick": a.tick,
            "agent": a.agent_id,
            "type": a.action_type,
            "target": a.target,
            "commTo": a.comm_to,
        })

    max_tick = max(
        max((c.tick for c in sim.communications), default=1),
        max((a.tick for a in sim.actions), default=1),
    )

    has_locations = bool(locations)

    return _TEMPLATE.format(
        title=_escape(sim.title),
        header=header_html,
        left_sidebar=left_html,
        coordination=coord_html,
        panels=panels_html,
        agent_ids_json=json.dumps(sim.agent_order),
        agents_data_json=json.dumps(agents_data),
        comms_data_json=json.dumps(comms_data),
        actions_data_json=json.dumps(actions_data),
        locations_json=json.dumps(locations or []),
        starting_locations_json=json.dumps(starting_locations or {}),
        adjacency_json=json.dumps(adjacency or {}),
        has_locations_json=json.dumps(has_locations),
        max_tick=max_tick,
    )


# ── Main entry point ────────────────────────────────────────────────────────


def _load_scenario_locations(scenario_path: str) -> tuple[
    list[dict] | None,
    dict[str, str] | None,
    dict[str, list[str]] | None,
]:
    """Try to load location data from a scenario YAML file.

    Returns (locations, starting_locations, adjacency) or (None, None, None).
    """
    if not scenario_path or _yaml is None:
        return None, None, None

    path = Path(scenario_path)
    if not path.exists():
        return None, None, None

    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None

    env_graph = data.get("environment_graph")
    if not env_graph or not env_graph.get("nodes"):
        return None, None, None

    locations = []
    for loc_id, loc_data in env_graph["nodes"].items():
        locations.append({
            "id": loc_id,
            "name": loc_data.get("name", loc_id),
        })

    adjacency = env_graph.get("adjacency", {})

    starting_locations: dict[str, str] = {}
    for agent_block in data.get("agents", []):
        status = agent_block.get("status", {})
        aid = status.get("agent_id")
        loc = status.get("location")
        if aid and loc:
            starting_locations[aid] = loc

    return locations, starting_locations, adjacency


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

    # Try to load location data from the scenario YAML
    locations, starting_locations, adjacency = _load_scenario_locations(
        sim.scenario_path
    )

    # Fallback: extract locations from move_to action targets if no YAML
    if locations is None:
        loc_ids: set[str] = set()
        for a in sim.actions:
            if a.action_type == "move_to" and a.target:
                loc_ids.add(a.target)
        if loc_ids:
            locations = [{"id": lid, "name": lid.replace("_", " ").title()} for lid in sorted(loc_ids)]
            adjacency = {}
            starting_locations = {}

    if output_path is None:
        output_path = log_path.with_suffix(".html")
    else:
        output_path = Path(output_path)

    html_content = render_html(
        sim,
        locations=locations,
        starting_locations=starting_locations,
        adjacency=adjacency,
    )
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
.network-toggle {{
  background: var(--bg-3);
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
}}
.network-toggle:has(input:checked) {{
  background: var(--blue-bg);
  border-color: var(--blue);
  color: var(--blue);
}}

/* ─── NETWORK VIEW ─── */
.network-container {{
  display: none;
  flex: 1;
  flex-direction: column;
  background: var(--bg);
  position: relative;
}}
.network-container.active {{
  display: flex;
}}
.network-canvas-wrap {{
  flex: 1;
  position: relative;
  overflow: hidden;
}}
.network-canvas-wrap canvas {{
  display: block;
  width: 100%;
  height: 100%;
}}

/* ─── PLAYBACK CONTROLS ─── */
.playback-bar {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  background: var(--bg-1);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}}
.playback-bar button {{
  background: var(--bg-3);
  border: 1px solid var(--border);
  color: var(--text);
  font-family: var(--mono);
  font-size: 13px;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
  min-width: 36px;
}}
.playback-bar button:hover {{ background: var(--border); }}
.playback-bar button.active {{ background: var(--blue-bg); border-color: var(--blue); color: var(--blue); }}
.playback-bar input[type=range] {{
  flex: 1;
  accent-color: var(--blue);
  cursor: pointer;
}}
.playback-bar .tick-val {{
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-bright);
  min-width: 70px;
  text-align: center;
}}
.speed-group {{
  display: flex;
  gap: 4px;
}}
.speed-group button {{
  font-size: 10px;
  padding: 3px 8px;
}}

.network-tooltip {{
  display: none;
  position: absolute;
  background: var(--bg-2);
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  padding: 8px 12px;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text);
  pointer-events: none;
  z-index: 100;
  max-width: 240px;
  line-height: 1.6;
}}
.network-tooltip .tt-name {{
  font-weight: 600;
  color: var(--text-bright);
  font-size: 12px;
}}
.network-tooltip .tt-role {{
  color: var(--text-dim);
  margin-bottom: 4px;
}}
.network-tooltip .tt-stat {{
  color: var(--text-dim);
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
<div class="network-container" id="network-container">
  <div class="network-canvas-wrap">
    <canvas id="network-canvas"></canvas>
    <div class="network-tooltip" id="network-tooltip"></div>
  </div>
  <div class="playback-bar">
    <button id="play-btn" title="Play/Pause">&#9654;</button>
    <input type="range" id="tick-slider" min="1" max="{max_tick}" value="1">
    <span class="tick-val" id="tick-val">Tick 1 / {max_tick}</span>
    <div class="speed-group">
      <button class="speed-btn active" data-speed="1">1x</button>
      <button class="speed-btn" data-speed="2">2x</button>
      <button class="speed-btn" data-speed="4">4x</button>
    </div>
  </div>
</div>
</div>
<script>
const agentIds = {agent_ids_json};
const agentsData = {agents_data_json};
const commsData = {comms_data_json};
const actionsData = {actions_data_json};
const locationsData = {locations_json};
const startingLocations = {starting_locations_json};
const adjacencyData = {adjacency_json};
const hasLocations = {has_locations_json};
const maxTick = {max_tick};
const panels = document.querySelectorAll('.panel');
const checkboxes = document.querySelectorAll('.toggle input[data-agent]');
const commsOnly = document.getElementById('comms-only');
const networkToggle = document.getElementById('network-view');
const gridEl = document.getElementById('grid');
const networkEl = document.getElementById('network-container');

function updateGrid() {{
  panels.forEach(p => {{
    const aid = p.dataset.agent;
    const cb = document.querySelector(`input[data-agent="${{aid}}"]`);
    p.classList.toggle('active', cb && cb.checked);
  }});
  const visible = document.querySelectorAll('.panel.active').length;
  if (visible <= 1) {{
    gridEl.style.gridTemplateColumns = '1fr';
  }} else if (visible <= 2) {{
    gridEl.style.gridTemplateColumns = 'repeat(2, 1fr)';
  }} else if (visible <= 4) {{
    gridEl.style.gridTemplateColumns = 'repeat(2, 1fr)';
  }} else if (visible <= 6) {{
    gridEl.style.gridTemplateColumns = 'repeat(3, 1fr)';
  }} else {{
    gridEl.style.gridTemplateColumns = 'repeat(auto-fit, minmax(300px, 1fr))';
  }}
}}

checkboxes.forEach(cb => cb.addEventListener('change', updateGrid));

commsOnly.addEventListener('change', () => {{
  document.body.classList.toggle('comms-only', commsOnly.checked);
}});

document.addEventListener('click', (e) => {{
  const clipped = e.target.closest('.comm-clipped, .refl-clipped, .meta-clipped');
  if (clipped) {{
    clipped.classList.toggle('comm-expanded');
    clipped.classList.toggle('refl-expanded');
    clipped.classList.toggle('meta-expanded');
  }}
}});

updateGrid();

// ─── NETWORK VIEW ───────────────────────────────────────────────────
networkToggle.addEventListener('change', () => {{
  const on = networkToggle.checked;
  gridEl.style.display = on ? 'none' : '';
  networkEl.classList.toggle('active', on);
  if (on) initNetwork();
}});

let netInitialized = false;
let nodes = [];
let edges = [];
let currentTick = 1;
let dragNode = null;
let hoverNode = null;
let animId = null;

// ─── Playback state ─────────────────────────────────────────────────
let playing = false;
let playSpeed = 1;
let playTimer = null;
let activeArcs = [];
let tickPhase = 0; // 0=start, 1=moves done, 2=comms done

function initNetwork() {{
  if (netInitialized) {{ drawFrame(); return; }}
  netInitialized = true;

  const canvas = document.getElementById('network-canvas');
  const wrap = canvas.parentElement;
  const ctx = canvas.getContext('2d');
  const tooltip = document.getElementById('network-tooltip');
  const slider = document.getElementById('tick-slider');
  const tickVal = document.getElementById('tick-val');
  const playBtn = document.getElementById('play-btn');

  function resize() {{
    canvas.width = wrap.clientWidth * devicePixelRatio;
    canvas.height = wrap.clientHeight * devicePixelRatio;
    canvas.style.width = wrap.clientWidth + 'px';
    canvas.style.height = wrap.clientHeight + 'px';
  }}
  resize();
  window.addEventListener('resize', () => {{ resize(); drawFrame(); }});

  const W = () => wrap.clientWidth;
  const H = () => wrap.clientHeight;

  // ─── Location regions ───────────────────────────────────────────────
  // Build location box positions in a 2x3 or 3x2 grid
  const locMap = {{}};
  let locBoxes = [];

  function buildLocationBoxes() {{
    locBoxes = [];
    if (!hasLocations || locationsData.length === 0) return;
    const pad = 20;
    const w = W() - pad * 2;
    const h = H() - pad * 2;
    const cols = locationsData.length <= 4 ? 2 : 3;
    const rows = Math.ceil(locationsData.length / cols);
    const boxW = (w - (cols - 1) * 12) / cols;
    const boxH = (h - (rows - 1) * 12) / rows;
    locationsData.forEach((loc, i) => {{
      const col = i % cols;
      const row = Math.floor(i / cols);
      const bx = pad + col * (boxW + 12);
      const by = pad + row * (boxH + 12);
      const box = {{ id: loc.id, name: loc.name, x: bx, y: by, w: boxW, h: boxH,
                     cx: bx + boxW / 2, cy: by + boxH / 2 }};
      locBoxes.push(box);
      locMap[loc.id] = box;
    }});
  }}

  buildLocationBoxes();
  window.addEventListener('resize', () => {{ buildLocationBoxes(); positionNodesInLocations(); }});

  // ─── Build nodes ────────────────────────────────────────────────────
  const cx = W() / 2, cy = H() / 2;
  // Track current location per agent
  const agentLocation = {{}};
  // Copy starting locations
  Object.keys(startingLocations).forEach(aid => {{ agentLocation[aid] = startingLocations[aid]; }});

  nodes = agentsData.map((a, i) => {{
    const angle = (2 * Math.PI * i) / agentsData.length - Math.PI / 2;
    const r = Math.min(W(), H()) * 0.3;
    return {{
      id: a.id, name: a.name, fullName: a.fullName, role: a.role,
      color: a.color,
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      targetX: 0, targetY: 0,
      vx: 0, vy: 0,
      sent: 0, recv: 0,
      lastAction: '',
      dimmed: false,
    }};
  }});
  const nodeMap = {{}};
  nodes.forEach(n => nodeMap[n.id] = n);

  // Assign positions within location boxes, avoiding overlap
  function positionNodesInLocations() {{
    if (!hasLocations || locBoxes.length === 0) return;
    // Group nodes by location
    const groups = {{}};
    nodes.forEach(n => {{
      const loc = agentLocation[n.id] || '';
      if (!groups[loc]) groups[loc] = [];
      groups[loc].push(n);
    }});
    Object.keys(groups).forEach(locId => {{
      const box = locMap[locId];
      if (!box) return;
      const members = groups[locId];
      const cols = Math.ceil(Math.sqrt(members.length));
      const rows = Math.ceil(members.length / cols);
      const spacingX = box.w / (cols + 1);
      const spacingY = (box.h - 24) / (rows + 1); // 24 for label
      members.forEach((n, i) => {{
        const col = i % cols;
        const row = Math.floor(i / cols);
        n.x = box.x + spacingX * (col + 1);
        n.y = box.y + 24 + spacingY * (row + 1);
        n.targetX = n.x;
        n.targetY = n.y;
      }});
    }});
  }}

  // ─── Build per-tick action index ──────────────────────────────────
  const tickActions = {{}};
  actionsData.forEach(a => {{
    if (!tickActions[a.tick]) tickActions[a.tick] = [];
    tickActions[a.tick].push(a);
  }});

  // Apply actions up to a given tick (for scrubbing)
  function applyStateTo(tick) {{
    // Reset locations to starting
    Object.keys(startingLocations).forEach(aid => {{ agentLocation[aid] = startingLocations[aid]; }});
    nodes.forEach(n => {{ n.sent = 0; n.recv = 0; n.lastAction = ''; n.dimmed = false; }});

    for (let t = 1; t <= tick; t++) {{
      const acts = tickActions[t] || [];
      acts.forEach(a => {{
        if (a.type === 'move_to' && a.target) {{
          agentLocation[a.agent] = a.target;
        }}
        if (a.type === 'communicate') {{
          if (nodeMap[a.agent]) nodeMap[a.agent].sent++;
          const recip = a.commTo || a.target;
          if (nodeMap[recip]) nodeMap[recip].recv++;
        }}
        if (nodeMap[a.agent]) nodeMap[a.agent].lastAction = a.type;
      }});
    }}

    // Mark resting agents as dimmed
    nodes.forEach(n => {{
      n.dimmed = (n.lastAction === 'rest');
    }});
  }}

  function computeEdges(maxT) {{
    const edgeMap = {{}};
    commsData.forEach(c => {{
      if (c.tick > maxT) return;
      const a = c.from < c.to ? c.from : c.to;
      const b = c.from < c.to ? c.to : c.from;
      const key = a + '|' + b;
      if (!edgeMap[key]) edgeMap[key] = {{ a, b, count: 0 }};
      edgeMap[key].count++;
    }});
    return Object.values(edgeMap);
  }}

  // ─── Force-directed layout (fallback for no locations) ─────────────
  function runForceLayout() {{
    edges = computeEdges(maxTick);
    for (let iter = 0; iter < 300; iter++) {{
      for (let i = 0; i < nodes.length; i++) {{
        for (let j = i + 1; j < nodes.length; j++) {{
          let dx = nodes[j].x - nodes[i].x;
          let dy = nodes[j].y - nodes[i].y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) d2 = 1;
          const f = 600 / d2;
          const fx = f * dx / Math.sqrt(d2);
          const fy = f * dy / Math.sqrt(d2);
          nodes[i].vx -= fx; nodes[i].vy -= fy;
          nodes[j].vx += fx; nodes[j].vy += fy;
        }}
      }}
      edges.forEach(e => {{
        const na = nodeMap[e.a], nb = nodeMap[e.b];
        if (!na || !nb) return;
        const dx = nb.x - na.x, dy = nb.y - na.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = 0.012 * (d - 140);
        const fx = f * dx / d, fy = f * dy / d;
        na.vx += fx; na.vy += fy;
        nb.vx -= fx; nb.vy -= fy;
      }});
      nodes.forEach(n => {{
        n.vx += (W() / 2 - n.x) * 0.008;
        n.vy += (H() / 2 - n.y) * 0.008;
        n.vx *= 0.82; n.vy *= 0.82;
        n.x += n.vx; n.y += n.vy;
        n.x = Math.max(30, Math.min(W() - 30, n.x));
        n.y = Math.max(30, Math.min(H() - 30, n.y));
      }});
    }}
  }}

  // ─── Initialize ─────────────────────────────────────────────────────
  if (hasLocations && locBoxes.length > 0) {{
    applyStateTo(1);
    positionNodesInLocations();
    edges = computeEdges(1);
  }} else {{
    runForceLayout();
  }}

  // ─── Communication arcs ─────────────────────────────────────────────
  function spawnArc(fromId, toId, color) {{
    const from = nodeMap[fromId];
    const to = nodeMap[toId];
    if (!from || !to) return;
    activeArcs.push({{
      x1: from.x, y1: from.y,
      x2: to.x, y2: to.y,
      color: color || from.color,
      startTime: performance.now(),
      duration: 1500,
    }});
  }}

  // ─── Drawing ────────────────────────────────────────────────────────
  let lastFrameTime = 0;
  let animating = false;

  function drawFrame(timestamp) {{
    if (!timestamp) timestamp = performance.now();
    // Reset transform for clearing, then apply pan/zoom
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, W(), H());
    ctx.setTransform(devicePixelRatio * zoom, 0, 0, devicePixelRatio * zoom,
                     panX * devicePixelRatio, panY * devicePixelRatio);

    // Animate node positions towards targets (smooth drift)
    if (hasLocations) {{
      nodes.forEach(n => {{
        const dx = n.targetX - n.x;
        const dy = n.targetY - n.y;
        if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {{
          n.x += dx * 0.08;
          n.y += dy * 0.08;
        }}
      }});
    }}

    // Draw location boxes
    if (hasLocations && locBoxes.length > 0) {{
      locBoxes.forEach(box => {{
        // Subtle fill
        ctx.fillStyle = 'rgba(30, 30, 30, 0.6)';
        ctx.fillRect(box.x, box.y, box.w, box.h);
        ctx.strokeStyle = 'rgba(100, 100, 100, 0.5)';
        ctx.lineWidth = 1;
        ctx.strokeRect(box.x, box.y, box.w, box.h);
        // Label — high contrast
        ctx.font = "600 11px 'IBM Plex Mono', monospace";
        ctx.fillStyle = 'rgba(200, 200, 200, 0.85)';
        ctx.textAlign = 'left';
        ctx.fillText(box.name.toUpperCase(), box.x + 8, box.y + 16);
      }});

      // Draw adjacency lines between location centers (very faint)
      ctx.strokeStyle = 'rgba(60, 60, 60, 0.2)';
      ctx.lineWidth = 1;
      Object.keys(adjacencyData).forEach(locId => {{
        const from = locMap[locId];
        if (!from) return;
        (adjacencyData[locId] || []).forEach(adjId => {{
          const to = locMap[adjId];
          if (!to) return;
          if (locId < adjId) {{ // draw each edge once
            ctx.beginPath();
            ctx.moveTo(from.cx, from.cy);
            ctx.lineTo(to.cx, to.cy);
            ctx.stroke();
          }}
        }});
      }});
    }}

    // Draw communication edges (cumulative)
    const maxCount = Math.max(1, ...edges.map(e => e.count));
    edges.forEach(e => {{
      const na = nodeMap[e.a], nb = nodeMap[e.b];
      if (!na || !nb) return;
      const isHover = hoverNode && (hoverNode.id === e.a || hoverNode.id === e.b);
      const alpha = hoverNode ? (isHover ? 0.5 : 0.05) : 0.2;
      ctx.beginPath();
      ctx.moveTo(na.x, na.y);
      ctx.lineTo(nb.x, nb.y);
      ctx.strokeStyle = isHover ? na.color : `rgba(100,100,100,${{alpha}})`;
      ctx.lineWidth = 1 + (e.count / maxCount) * 4;
      ctx.stroke();
    }});

    // Draw animated arcs
    const now = timestamp;
    activeArcs = activeArcs.filter(arc => {{
      const elapsed = now - arc.startTime;
      if (elapsed > arc.duration) return false;
      const t = elapsed / arc.duration;
      // Opacity: fade in then out
      const opacity = t < 0.3 ? (t / 0.3) * 0.8 : 0.8 * (1 - (t - 0.3) / 0.7);
      // Quadratic bezier with control point offset
      const mx = (arc.x1 + arc.x2) / 2;
      const my = (arc.y1 + arc.y2) / 2;
      const dx = arc.x2 - arc.x1;
      const dy = arc.y2 - arc.y1;
      const cpx = mx - dy * 0.25;
      const cpy = my + dx * 0.25;

      // Parse color to rgba
      let cr = 96, cg = 165, cb = 250; // default blue
      if (arc.color.startsWith('#')) {{
        cr = parseInt(arc.color.slice(1, 3), 16);
        cg = parseInt(arc.color.slice(3, 5), 16);
        cb = parseInt(arc.color.slice(5, 7), 16);
      }}

      // Dashed arc line — visually distinct from edges
      ctx.beginPath();
      ctx.moveTo(arc.x1, arc.y1);
      ctx.quadraticCurveTo(cpx, cpy, arc.x2, arc.y2);
      ctx.strokeStyle = `rgba(${{cr}},${{cg}},${{cb}},${{opacity}})`;
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      // Traveling dot (💬 indicator)
      const dt = Math.min(t * 1.5, 1);
      const bx = (1-dt)*(1-dt)*arc.x1 + 2*(1-dt)*dt*cpx + dt*dt*arc.x2;
      const by = (1-dt)*(1-dt)*arc.y1 + 2*(1-dt)*dt*cpy + dt*dt*arc.y2;
      // Draw a small filled circle with a chat icon
      ctx.beginPath();
      ctx.arc(bx, by, 5, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${{cr}},${{cg}},${{cb}},${{Math.min(opacity * 1.5, 1)}})`;
      ctx.fill();
      // Small ring around the dot to make comms distinctive
      ctx.beginPath();
      ctx.arc(bx, by, 8, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${{cr}},${{cg}},${{cb}},${{opacity * 0.5}})`;
      ctx.lineWidth = 1;
      ctx.stroke();
      return true;
    }});

    // Draw nodes
    const maxTotal = Math.max(1, ...nodes.map(n => n.sent + n.recv));
    nodes.forEach(n => {{
      // Check agent visibility toggle
      const cb = document.querySelector(`input[data-agent="${{n.id}}"]`);
      if (cb && !cb.checked) return;

      const total = n.sent + n.recv;
      const radius = hasLocations ? 10 : 8 + (total / maxTotal) * 16;
      const isHover = hoverNode === n;
      const isDim = hoverNode && !isHover &&
        !edges.some(e => (e.a === hoverNode.id || e.b === hoverNode.id) &&
                         (e.a === n.id || e.b === n.id));
      const restDim = n.dimmed;

      // Resting agents: reduced opacity
      const baseAlpha = restDim ? 0.4 : 1.0;

      ctx.globalAlpha = isDim ? 0.25 : baseAlpha;
      ctx.beginPath();
      ctx.arc(n.x, n.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.fill();
      if (isHover) {{
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();
      }}

      // Agent name label
      ctx.font = `${{isHover ? '600' : '500'}} 11px 'IBM Plex Sans', sans-serif`;
      ctx.textAlign = 'center';
      ctx.fillStyle = '#e0e0e0';
      ctx.fillText(n.name, n.x, n.y + radius + 14);

      // Action status label (dim, below name)
      if (n.lastAction) {{
        ctx.font = "400 9px 'IBM Plex Mono', monospace";
        ctx.fillStyle = restDim ? 'rgba(140,140,140,0.4)' : 'rgba(140,140,140,0.7)';
        ctx.fillText(n.lastAction, n.x, n.y + radius + 25);
      }}

      ctx.globalAlpha = 1;
    }});

    // Continue animation if arcs are active or nodes are moving
    let needsAnim = activeArcs.length > 0;
    if (hasLocations) {{
      nodes.forEach(n => {{
        if (Math.abs(n.targetX - n.x) > 0.5 || Math.abs(n.targetY - n.y) > 0.5) needsAnim = true;
      }});
    }}
    if (needsAnim) {{
      animId = requestAnimationFrame(drawFrame);
    }} else {{
      animating = false;
    }}
  }}

  function ensureAnimating() {{
    if (!animating) {{
      animating = true;
      animId = requestAnimationFrame(drawFrame);
    }}
  }}

  // ─── Go to a specific tick (scrub) ─────────────────────────────────
  function goToTick(tick, animate) {{
    currentTick = tick;
    slider.value = tick;
    tickVal.textContent = `Tick ${{tick}} / ${{maxTick}}`;

    applyStateTo(tick);
    edges = computeEdges(tick);

    if (hasLocations) {{
      // Recompute target positions
      const groups = {{}};
      nodes.forEach(n => {{
        const loc = agentLocation[n.id] || '';
        if (!groups[loc]) groups[loc] = [];
        groups[loc].push(n);
      }});
      Object.keys(groups).forEach(locId => {{
        const box = locMap[locId];
        if (!box) return;
        const members = groups[locId];
        const cols = Math.ceil(Math.sqrt(members.length));
        const rows = Math.ceil(members.length / cols);
        const spacingX = box.w / (cols + 1);
        const spacingY = (box.h - 24) / (rows + 1);
        members.forEach((n, i) => {{
          const col = i % cols;
          const row = Math.floor(i / cols);
          n.targetX = box.x + spacingX * (col + 1);
          n.targetY = box.y + 24 + spacingY * (row + 1);
          if (!animate) {{
            n.x = n.targetX;
            n.y = n.targetY;
          }}
        }});
      }});
    }}

    // Spawn communication arcs for this tick only if animating
    if (animate) {{
      const tickComms = commsData.filter(c => c.tick === tick);
      tickComms.forEach(c => {{
        const sender = nodeMap[c.from];
        spawnArc(c.from, c.to, sender ? sender.color : '#60a5fa');
      }});
    }}

    ensureAnimating();
    if (!animate && !animating) {{
      drawFrame(performance.now());
    }}
  }}

  window.drawNetwork = () => drawFrame(performance.now());

  // Initial draw
  goToTick(1, false);

  // ─── Playback controls ──────────────────────────────────────────────
  function stopPlayback() {{
    playing = false;
    playBtn.innerHTML = '&#9654;';
    if (playTimer) {{ clearTimeout(playTimer); playTimer = null; }}
  }}

  function startPlayback() {{
    playing = true;
    playBtn.innerHTML = '&#9646;&#9646;';
    advanceTick();
  }}

  function advanceTick() {{
    if (!playing) return;
    if (currentTick >= maxTick) {{
      stopPlayback();
      return;
    }}
    currentTick++;
    goToTick(currentTick, true);
    const delay = 2000 / playSpeed;
    playTimer = setTimeout(advanceTick, delay);
  }}

  playBtn.addEventListener('click', () => {{
    if (playing) {{
      stopPlayback();
    }} else {{
      if (currentTick >= maxTick) currentTick = 0;
      startPlayback();
    }}
  }});

  document.querySelectorAll('.speed-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      playSpeed = parseInt(btn.dataset.speed);
    }});
  }});

  // Slider scrub
  slider.addEventListener('input', () => {{
    stopPlayback();
    goToTick(parseInt(slider.value), false);
  }});

  // ─── Mouse interaction ──────────────────────────────────────────────
  function getNode(mx, my) {{
    const maxTotal = Math.max(1, ...nodes.map(n => n.sent + n.recv));
    for (let i = nodes.length - 1; i >= 0; i--) {{
      const n = nodes[i];
      const r = hasLocations ? 10 : 8 + ((n.sent + n.recv) / maxTotal) * 16;
      if ((mx - n.x) ** 2 + (my - n.y) ** 2 < (r + 4) ** 2) return n;
    }}
    return null;
  }}

  // Pan/zoom state
  let panX = 0, panY = 0, zoom = 1;
  let isPanning = false, panStartX = 0, panStartY = 0;

  function toWorld(mx, my) {{
    return {{ x: (mx - panX) / zoom, y: (my - panY) / zoom }};
  }}

  canvas.addEventListener('mousedown', (e) => {{
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const wp = toWorld(mx, my);
    const node = getNode(wp.x, wp.y);
    if (node && !hasLocations) {{
      // Allow dragging only in force-directed mode
      dragNode = node;
    }} else {{
      // Pan the canvas
      isPanning = true;
      panStartX = mx - panX;
      panStartY = my - panY;
      canvas.style.cursor = 'grabbing';
    }}
  }});
  canvas.addEventListener('mousemove', (e) => {{
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (isPanning) {{
      panX = mx - panStartX;
      panY = my - panStartY;
      ensureAnimating();
    }} else if (dragNode) {{
      const wp = toWorld(mx, my);
      dragNode.x = wp.x; dragNode.y = wp.y;
      dragNode.targetX = wp.x; dragNode.targetY = wp.y;
      ensureAnimating();
    }} else {{
      const wp = toWorld(mx, my);
      const prev = hoverNode;
      hoverNode = getNode(wp.x, wp.y);
      if (hoverNode !== prev) ensureAnimating();
      if (hoverNode) {{
        const loc = agentLocation[hoverNode.id] || 'unknown';
        tooltip.style.display = 'block';
        tooltip.innerHTML = `<div class="tt-name">${{hoverNode.fullName}}</div>` +
          `<div class="tt-role">${{hoverNode.role}}${{hasLocations ? ' — ' + loc : ''}}</div>` +
          `<div class="tt-stat">Sent: ${{hoverNode.sent}} &middot; Received: ${{hoverNode.recv}}</div>` +
          (hoverNode.lastAction ? `<div class="tt-stat">Action: ${{hoverNode.lastAction}}</div>` : '');
        tooltip.style.left = (mx + 16) + 'px';
        tooltip.style.top = (my - 10) + 'px';
      }} else {{
        tooltip.style.display = 'none';
      }}
    }}
  }});
  canvas.addEventListener('mouseup', () => {{
    dragNode = null;
    isPanning = false;
    canvas.style.cursor = '';
  }});
  canvas.addEventListener('mouseleave', () => {{
    dragNode = null;
    isPanning = false;
    hoverNode = null;
    tooltip.style.display = 'none';
    canvas.style.cursor = '';
    ensureAnimating();
  }});

  // Zoom with scroll wheel
  canvas.addEventListener('wheel', (e) => {{
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const oldZoom = zoom;
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    zoom = Math.max(0.3, Math.min(4, zoom * delta));
    // Zoom toward cursor position
    panX = mx - (mx - panX) * (zoom / oldZoom);
    panY = my - (my - panY) * (zoom / oldZoom);
    ensureAnimating();
  }}, {{ passive: false }});

  // ─── Agent toggle visibility also affects network ───────────────────
  checkboxes.forEach(cb => cb.addEventListener('change', () => {{ ensureAnimating(); }}));
}}
</script>
</body>
</html>
"""


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import webbrowser

    if len(sys.argv) < 2:
        print("Usage: python -m miniverse.viewer <log_file> [-o output.html] [--open]")
        sys.exit(1)

    log_file = sys.argv[1]
    out_file = None
    should_open = "--open" in sys.argv

    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        if idx + 1 < len(sys.argv):
            out_file = sys.argv[idx + 1]

    result = render_log(log_file, out_file)
    print(f"Rendered: {result}")

    if should_open:
        webbrowser.open(f"file://{Path(result).resolve()}")
