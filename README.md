<p align="center"><img src=".github/images/header.png" alt="Miniverse header" width="75%"></p>

<p align="center"><em>In silico social science. Simulate what you can't experiment on.</em></p>

<p align="center">
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/status-alpha-orange" alt="Status"></a>
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python"></a>
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/designed%20with-Claude%20%26%20GPT--5-6f42c1" alt="Designed with AI"></a>
  <a href="https://x.com/local0ptimist"><img src="https://img.shields.io/badge/created%20by-@local0ptimist-1d9bf0" alt="Creator"></a>
</p>

---

## What is Miniverse?

Miniverse is a **CLI-first platform for computational social science and organizational simulation**.

Traditional social science has a fundamental limitation: you can't run experiments on societies. You can't A/B test policy changes, replay historical decisions, or observe counterfactuals.

Miniverse changes this. Build believable agent simulations, inject interventions, and analyze what emerges—all reproducibly and at scale.

**Use cases:**
- How does a rumor spread through an organization?
- What happens when you restructure teams?
- Will this policy improve coordination or create friction?
- How do information cascades form and break?

**Use cases (research):**
- How do different persona prompts change agent behavior in safety-relevant scenarios?
- Do AI agents with different personality overlays respond differently when discovering their own policy violations?
- Which character archetypes produce the most distinct behavioral patterns?

**Alpha:** Core architecture is stable. Two orchestration modes: tick-based (synchronous) and async with rolling context windows. Active research experiment running.

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/miniverse-ai/miniverse.git
cd miniverse
uv sync

# Configure LLM env vars (required for LLM demo stages)
# If you already have .env, skip the copy.
cp .env.example .env
# Edit .env with your provider/model/API key, then export it:
set -a; source .env; set +a

# Run demo scripts (recommended)
bash demo/workshop/run_baseline.sh
bash demo/workshop/run_compare.sh
bash demo/valentines/run.sh
bash demo/threshold/run.sh

# Optional: run workshop example (deterministic mode)
uv run python examples/workshop/run.py --ticks 10

# Optional: run workshop example with LLM cognition
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-5-nano
export OPENAI_API_KEY=your_key
uv run python examples/workshop/run.py --llm --ticks 10
```

---

## Demo Scripts

Workshop demo assets live under `demo/workshop` and are file-driven:

```bash
# Setup + deterministic baseline
bash demo/workshop/run_baseline.sh

# Setup + deterministic + LLM comparison
bash demo/workshop/run_compare.sh

# Valentines demo (file-driven demo scenario)
bash demo/valentines/run.sh

# Threshold demo (transhumanist cyberpunk social dynamics)
bash demo/threshold/run.sh
```

These scripts:
- Read setup context from scenario files under `demo/` (e.g. `demo/workshop/scenario.yaml`,
  `demo/valentines/scenario.yaml`)
- Run deterministic and LLM stages via the core `miniverse run` command
- Use fixed tick counts (no runtime tick overrides) for consistent demos
- Use verbose LLM logging in comparison mode for readable plan/memory/reflection traces
- Keep the demo behavior in files/scripts rather than custom CLI demo logic
- Load scenario-local extensions declared in `metadata.runtime` (workshop uses
  `demo/workshop/rules.py` + `demo/workshop/cognition.py`)
- Surface shift-clock and queue-flow metrics per tick in verbose mode
- Save baseline JSON + LLM verbose logs under `demo/workshop/logs/`, including
  queue accounting (`initial + arrivals - completions`) and post-run baseline-vs-LLM comparison
- Run a final LLM judge pass to produce an executive summary

Preconfigured workshop demo scenario:
- `demo/workshop/scenario.yaml`
  - Includes workshop persona definitions directly in `agents[].profile/status`.

Demo scripts:
- `demo/workshop/run_baseline.sh`
- `demo/workshop/run_compare.sh`
- `demo/valentines/run.sh`
- `demo/threshold/run.sh`

LLM demo stages require exported env vars in your shell:
- `LLM_PROVIDER`
- `LLM_MODEL`
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

---

## How It Works

### Two Orchestration Modes

**Tick-based** (`--ticks N`) — synchronous, all agents act per tick:
```
Physics → Perception → Cognition → Actions → Memory → Reflection → Persist
```

**Async context window** (`--async --context-window`) — agents run independent loops with rolling conversation history:
```
System prompt (identity + persona + tools + memories)
  ↓
Loop:
  1. Inject message nudges + perception changes
  2. LLM call → StepOutput (think/act/respond, all optional)
  3. Execute action → ActionResult added to context
  4. Route messages → nudge recipients
  5. Fire world events (action-triggered, deterministic)
  ↓
Auto-save per-agent transcripts to outputs/
```

**Persona overlays** (`--persona-file path.txt`) inject character descriptions into the target agent's system prompt at runtime. Different overlays produce different behavior in the same scenario — the independent variable in behavioral experiments.

**Scenario actions** (`actions.py`) provide per-scenario tools with per-agent filtering. Target agents get full tool access; NPCs get read-only observation tools appropriate to their role.

---

## Examples

### Workshop Scenario

A team coordination simulation with mechanics, analysts, and supervisors managing a repair backlog.

```bash
# Deterministic baseline
uv run python examples/workshop/run.py --ticks 20

# With LLM cognition
uv run python examples/workshop/run.py --llm --ticks 20

# Monte Carlo (100 trials with different seeds)
uv run python examples/workshop/monte_carlo.py --runs 100 --ticks 20
```

### Smallville Valentine's

Recreation of Stanford Generative Agents' party coordination scenario.

```bash
# Demo script (recommended)
bash demo/valentines/run.sh

# Direct CLI invocation
uv run miniverse run demo/valentines/scenario.yaml --llm --world-engine deterministic --verbose --ticks 15
```

### Basin Discovery / LLM Bazaar

Current active research experiment for persona-conditioned market behavior. See `experiments/basin-discovery/README.md`.

### Order of the Threshold

9-agent cyberpunk scenario exploring emergent social dynamics with asymmetric hidden information. Some agents have secret goals that drive covert coordination and social influence — behaviors emerge from faithful character simulation, not explicit instruction.

```bash
# Run the simulation
bash demo/threshold/run.sh

# View the transcript in browser
python -m miniverse.viewer demo/threshold/logs/threshold_llm_*.log --open
```

---

## Transcript Viewer

Miniverse includes an HTML transcript viewer adapted from [Helm](https://github.com/k3nnethfrancis/helm)'s viewer. It renders simulation logs as self-contained HTML files with per-agent timelines, color-coded actions, and a communications sidebar.

```bash
# Render a log to HTML and open in browser
python -m miniverse.viewer path/to/simulation.log --open

# Render to a specific output path
python -m miniverse.viewer path/to/simulation.log -o transcript.html

# Render without opening
python -m miniverse.viewer path/to/simulation.log
```

Features:
- **Per-agent panels** with tick-by-tick action timelines
- **Communications sidebar** showing all inter-agent messages chronologically
- **Color-coded actions** (communicate=green, move=blue, investigate=purple, work=gray, rest=amber)
- **Comms-only toggle** to filter to just inter-agent messages
- **Agent visibility toggles** to show/hide specific agents
- **Dark theme** with IBM Plex fonts, self-contained single HTML file

---

## Architecture

### Core Components

| Component | Purpose |
|-----------|---------|
| **Orchestrator** | Tick-based loop, dependency injection |
| **AsyncOrchestrator** | Async context window loop, NPC auto-sleep, inbox nudge pattern |
| **ContextWindow** | Rolling conversation history per agent, collapses to (system, user) |
| **ScenarioActions** | Per-scenario tool execution with per-agent filtering |
| **SimulationRules** | Deterministic physics (resources, constraints, events) |
| **Cognition Stack** | Planner, executor, reflection (tick-based mode) |
| **Memory Strategy** | Store and retrieve agent experiences (BM25, semantic, simple) |
| **Persistence** | Save state (in-memory, JSON, PostgreSQL) |

### Design Principles

1. **CLI-First**: Every feature usable from command line
2. **Dependency Injection**: Swap strategies without modifying core
3. **Reproducibility**: Seed everything, log everything
4. **Research-Ready**: Structured outputs for statistical analysis

---

## Debugging

```bash
# Show LLM prompts and responses
DEBUG_LLM=true uv run python examples/workshop/run.py --llm

# Show memory operations
DEBUG_MEMORY=true uv run python examples/workshop/run.py --llm

# Show agent perceptions
DEBUG_PERCEPTION=true uv run python examples/workshop/run.py --llm

# Maximum verbosity
DEBUG_LLM=true DEBUG_MEMORY=true MINIVERSE_VERBOSE=true \
  uv run python examples/workshop/run.py --llm
```

---

## Documentation

| Document | Purpose |
|----------|---------|
| [CLAUDE.md](CLAUDE.md) | Development guidelines |
| [AGENTS.md](AGENTS.md) | Agent working protocol |
| [docs/README.md](docs/README.md) | Documentation index |
| [docs/USAGE.md](docs/USAGE.md) | Scenario authoring and runtime configuration |
| [docs/PROMPTS.md](docs/PROMPTS.md) | Prompt system guide |
| [docs/PARITY.md](docs/PARITY.md) | Generative Agents parity and differences |
| [docs/architecture/](docs/architecture/) | Deep dives |

---

## Contributing

```bash
# Run tests before submitting
UV_CACHE_DIR=.uv-cache uv run pytest
```

- Keep changes focused; include test coverage
- Update docs when changing behavior

---

## Inspirations

- [Stanford Generative Agents](https://arxiv.org/abs/2304.03442) – Original emergent behavior research
- [Anthropic Petri](https://github.com/anthropics/petri) – Auditing patterns (branching, scoring)
- [AgentTorch](https://github.com/AgentTorch/AgentTorch) – Large-scale policy simulation
- [Mesa](https://github.com/projectmesa/mesa) – Python ABM framework

---

## Credits

- Creator: [Kenneth / @local0ptimist](https://x.com/local0ptimist)
- Built with: Claude, GPT-5 Codex
- Research notes: [docs/RESEARCH.md](docs/RESEARCH.md)

## License

MIT. Fork responsibly.
