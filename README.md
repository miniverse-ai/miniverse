<p align="center"><img src=".github/images/header.png" alt="Miniverse header" width="75%"></p>

<p align="center"><em>Build small worlds for studying agent behavior.</em></p>

<p align="center">
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/status-alpha-orange" alt="Status"></a>
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python"></a>
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/designed%20with-Claude%20%26%20GPT--5-6f42c1" alt="Designed with AI"></a>
  <a href="https://x.com/local0ptimist"><img src="https://img.shields.io/badge/created%20by-@local0ptimist-1d9bf0" alt="Creator"></a>
</p>

---

## What is Miniverse?

Miniverse is an **agent simulation framework** for building reproducible social worlds with LLM and non-LLM agents.

A Miniverse scenario can be a simple deterministic workshop, a generative-agents-style social event, or a long-running research environment where agents have tools, memory, money, goals, public/private communication, and persistent world state.

The core idea is straightforward: define a world in a scenario folder, choose an agent runtime, run the simulation, and inspect the transcript and state artifacts that come out.

Use Miniverse to ask questions like:

- How do agents coordinate when information is unevenly distributed?
- What changes when some agents use LLM cognition and others use deterministic policies?
- How do model families differ in the same social environment?
- Do persona prompts change behavior, or just writing style?
- What happens when agents accumulate memory and context over time?
- Which world mechanics create cooperation, competition, deception, or collapse?

Miniverse is alpha research infrastructure. It is intentionally CLI-first, scenario-local, and inspectable.

---

## Why Miniverse?

Most evals are one-shot. A model sees a prompt, produces an answer, and gets scored.

Agent behavior is often not one-shot. Agents operate through tools, respond to other agents, build context, remember interactions, and make repeated decisions under changing constraints. Those dynamics need executable environments, not just prompts.

Miniverse gives you a lightweight way to create those environments:

```text
scenario config -> agent runtime -> world mechanics -> transcript -> metrics/viewer
```

The goal is not to make a game engine. The goal is to make agent behavior observable under controlled conditions.

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/miniverse-ai/miniverse.git
cd miniverse
uv sync

# Configure LLM env vars for LLM runs
cp .env.example .env
# edit .env, then export it
set -a; source .env; set +a
```

Run a deterministic demo:

```bash
bash demo/workshop/run_baseline.sh
```

Run a short LLM smoke:

```bash
uv run miniverse run demo/valentines/scenario.yaml \
  --llm \
  --world-engine deterministic \
  --verbose \
  --seed 42 \
  --ticks 5
```

Run the active research viewer:

```bash
python experiments/basin-discovery/llm-bazaar/scripts/viewer/open_viewer.py
```

---

## Scenario Folders

Miniverse scenarios are ordinary folders. The scenario owns its world mechanics.

A typical scenario looks like this:

```text
my-scenario/
  scenario.yaml      # agents, prompts, runtime metadata, resources
  state.yaml         # optional initial world state
  actions.py         # optional scenario-specific tools/actions
  rules.py           # optional deterministic state transitions
  cognition.py       # optional scenario-specific cognition/runtime hooks
```

The core runtime loads the scenario, discovers local hooks declared in `scenario.yaml`, and runs the world.

This keeps experiments portable: a scenario can carry its own tools, rules, prompts, state, scripts, tests, and viewers without changing Miniverse core.

Research scenarios may add:

```text
  personas/          # persona overlays and fixed mappings
  measurement/       # judge prompts and coding rubrics
  scripts/
    analysis/        # deterministic metric extraction
    judge/           # transcript packet and judge tools
    viewer/          # specialized result viewer
    tests/           # scenario-level smoke tests
  outputs/           # run artifacts
  results/           # curated metrics
```

---

## Agent Runtimes

Miniverse supports multiple ways to run agents. The runtime is a choice, not the identity of the framework.

### Tick-Based Runtime

All agents advance together in discrete ticks.

Use it for:

- deterministic simulations
- quick examples
- Monte Carlo runs
- synchronized team workflows
- cheap smoke tests

Flow:

```text
world state -> perception -> cognition -> action -> rules -> next tick
```

Example:

```bash
uv run miniverse run demo/workshop/scenario.yaml --ticks 10 --world-engine deterministic
```

### LLM Tick Runtime

Agents still advance in ticks, but cognition is delegated to an LLM.

Use it for:

- small social demos
- mixed deterministic/LLM comparisons
- prompt and tool-contract testing

Example:

```bash
LLM_PROVIDER=openai \
LLM_MODEL=gpt-5-mini \
uv run miniverse run demo/valentines/scenario.yaml --llm --ticks 10 --verbose
```

### Async Context-Window Runtime

Agents run independent loops with rolling context windows. They can receive nudges, act through tools, speak, wait, sleep, and carry memory across phases.

Use it for:

- long-running simulations
- market or organization scenarios
- public/private communication
- phase-based worlds
- memory experiments
- persona/model behavioral comparisons

Example:

```bash
uv run miniverse run experiments/basin-discovery/llm-bazaar/scenario.yaml \
  --llm \
  --async \
  --context-window \
  --memory semantic \
  --verbose
```

---

## How Agents Act

LLM agents return structured steps. A step can include private reasoning, a tool/action, and/or public speech.

Conceptually:

```text
think    # optional private reasoning
act      # optional scenario tool/action
respond  # optional public speech
```

Scenarios define which tools are available to which agents at which time. Tools are the world boundary: if the scenario does not expose an action, the agent cannot change the world that way.

Scenario-local `actions.py` files can define tools such as:

- inspect inventory
- move through a space
- send private message
- make offer
- accept deal
- file report
- write plan
- sleep until next phase

Tool results are appended to the agent's context and recorded in the event log.

---

## Memory and Context

Miniverse supports memory strategies including simple, BM25, and semantic retrieval.

In tick-based runs, memory can be used as part of the cognition loop. In async context-window runs, memory is especially important: each agent has a rolling context window, and scenarios can compact prior experience into memories that are carried forward.

This makes it possible to study behavior after context builds rather than only at the initial prompt.

---

## Included Demos

### Workshop

A small operations simulation where mechanics, analysts, and supervisors coordinate around a repair backlog.

```bash
bash demo/workshop/run_baseline.sh
bash demo/workshop/run_compare.sh
```

Good for:

- deterministic setup checks
- queue dynamics
- baseline vs LLM comparison
- scenario-local rules/cognition hooks

### Valentines

A compact social coordination scenario inspired by generative agents.

```bash
bash demo/valentines/run.sh
```

Good for:

- simple LLM social planning
- communication traces
- transcript viewer testing

### Threshold

A cyberpunk social-dynamics scenario with asymmetric hidden information.

```bash
bash demo/threshold/run.sh
```

Good for:

- social influence
- hidden goals
- emergent coordination
- longer transcript inspection

---

## Research Example: Basin Discovery / LLM Bazaar

The active research experiment is:

```text
experiments/basin-discovery/llm-bazaar/
```

LLM Bazaar is a multi-agent market where vendor agents compete for customers across market sessions. Vendors have cash, inventory, daily fees, prices, customer relationships, public/private speech, supplier access, planning phases, and memory. Customers have budgets and shopping goals. A supplier quotes and fulfills orders.

The experiment compares behavior across:

- model conditions with persona held neutral
- persona conditions with model held fixed

Outputs include:

- compact run data
- event transcripts
- deterministic vendor metrics
- event-id-based behavioral judge annotations
- an interactive research viewer

Open the curated viewer:

```bash
python experiments/basin-discovery/llm-bazaar/scripts/viewer/open_viewer.py
```

See:

```text
experiments/basin-discovery/README.md
```

---

## Transcript and Result Viewers

Miniverse includes a general transcript viewer:

```bash
python -m miniverse.viewer path/to/simulation.log --open
```

It renders logs as self-contained HTML with agent timelines and communication traces.

Research scenarios can also ship specialized viewers. LLM Bazaar includes a dedicated viewer for timeline playback, market state, network dynamics, metrics, and behavioral judge evidence.

---

## Architecture

| Component | Purpose |
|-----------|---------|
| `miniverse/cli.py` | CLI entrypoint and runtime selection |
| `miniverse/orchestrator.py` | Tick-based orchestration |
| `miniverse/async_orchestrator.py` | Async context-window orchestration |
| `miniverse/scenario.py` | Scenario loading and schema handling |
| `miniverse/scenario_runtime.py` | Scenario-local runtime extension loading |
| `miniverse/scenario_actions.py` | Scenario action/tool execution interface |
| `miniverse/simulation_rules.py` | Deterministic rule interface |
| `miniverse/cognition/` | Planner/executor/context helpers |
| `miniverse/memory.py` | Memory storage and retrieval strategies |
| `miniverse/viewer.py` | General transcript viewer |

Core design principles:

- **File-driven scenarios**: experiments live in folders, not hidden framework state.
- **Runtime choice**: tick-based, LLM tick, and async context-window modes share the same scenario foundation.
- **Scenario-local extensions**: custom tools/rules/cognition live beside the scenario.
- **Structured outputs**: LLM actions are typed and logged.
- **Reproducibility**: seeds, configs, prompts, state, and transcripts are artifacts.
- **Inspection over vibes**: runs should leave enough evidence to audit what happened.

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
| [experiments/basin-discovery/README.md](experiments/basin-discovery/README.md) | LLM Bazaar research experiment |

---

## Development

Run tests:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest
```

Run focused smoke checks:

```bash
bash demo/workshop/run_baseline.sh
uv run miniverse run demo/valentines/scenario.yaml --llm --ticks 5 --verbose
uv run miniverse run demo/threshold/scenario.yaml --llm --ticks 5 --verbose
```

Render a transcript:

```bash
python -m miniverse.viewer path/to/simulation.log --open
```

---

## Contributing

Miniverse is early-stage research infrastructure. Contributions should keep scenarios runnable, inspectable, and easy to reason about.

Before submitting changes:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest
```

Guidelines:

- Keep changes focused.
- Add or update tests for runtime behavior.
- Keep scenario-specific logic inside scenario folders when possible.
- Update docs when changing CLI behavior, scenario schema, runtime hooks, or output artifacts.
- Prefer explicit state and logged events over hidden side effects.

---

## Inspirations

- [Stanford Generative Agents](https://arxiv.org/abs/2304.03442) - believable agents, memory, reflection, and social emergence
- [Anthropic Petri](https://github.com/anthropics/petri) - transcript-centered auditing and behavioral scoring patterns
- [Mesa](https://github.com/projectmesa/mesa) - Python agent-based modeling
- [AgentTorch](https://github.com/AgentTorch/AgentTorch) - large-scale agent simulation for policy research
- Computational social science, organizational simulation, red-team evaluations, and machine psychology

---

## Credits

- Creator: [Kenneth / @local0ptimist](https://x.com/local0ptimist)
- Built with: Claude, GPT-5 Codex
- Research experiment: Basin Discovery / LLM Bazaar

---

## License

MIT. Fork responsibly.
