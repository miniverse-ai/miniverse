# Claude Code Guide – Miniverse

Authoritative instructions for working on the **Miniverse** codebase.

---

## Start Here

**Read these documents in order:**

1. **VISION.md** – The north star. What Miniverse should become. Read this first.
2. **ROADMAP.md** – Phased implementation plan. What we're building and when.
3. **This file (CLAUDE.md)** – Operational guidance for development.

---

## What is Miniverse?

Miniverse is a **CLI-first platform for computational social science and organizational simulation**.

The goal: let researchers run "what if" experiments on social dynamics that are impossible in the real world. Simulate how rumors spread, how reorgs affect trust, how policies change behavior—all reproducibly and at scale.

### Core Capabilities

- **CLI interface**: `miniverse init`, `run`, `analyze`, `compare`, `export`
- **LLM-driven agents**: Personality, goals, memory, planning, reflection
- **Deterministic physics**: Controllable rules layer for domain-specific constraints
- **Branching/intervention**: Fork simulations, inject changes, compare outcomes
- **Research-ready outputs**: Structured data for statistical analysis

### Current Status

**Phase 1 (CLI Foundation) complete** on branch `cev-redesign`:
- `miniverse run/list/info` commands working
- Template system with `org-hierarchy` scenario
- 43 tests passing

Library core is solid:
- Orchestrator with dependency injection
- Cognition stack (planner, executor, reflection)
- Memory and persistence strategies
- Environment tiers (abstract → graph → grid)

**Next**: Phase 2 (Validation) - replicate known social science findings.

---

## Development Priorities

### 1. CLI First

Every feature should be usable from the command line. The library exists to support the CLI, not the other way around.

```bash
# Currently implemented (Phase 1)
miniverse list                                    # Show available scenarios
miniverse info org-hierarchy                      # Show scenario details
miniverse run org-hierarchy --ticks 10            # Deterministic mode
miniverse run org-hierarchy --ticks 10 --llm      # LLM cognition
miniverse run org-hierarchy --ticks 10 --output json  # JSON output
miniverse run org-hierarchy --ticks 10 --seed 42  # Reproducible

# Performance tuning (Phase 1.1)
miniverse run org-hierarchy --ticks 10 --llm --world-engine deterministic  # Fast LLM mode

# Provider options (set via environment)
export LLM_PROVIDER=ollama LLM_MODEL=llama3.2     # Local models via Ollama
export LLM_PROVIDER=openai LLM_MODEL=gpt-5-nano   # Fast OpenAI model

# Planned (Phase 2+)
miniverse init --template <name>                  # Generate new scenario
miniverse analyze --metrics diffusion             # Post-run analysis
miniverse compare baseline treatment              # Compare branches
miniverse export --format csv                     # Export for R/Python
```

### 2. Research Validity

Simulations must produce believable, reproducible behavior. Prioritize:
- Deterministic seeding (same seed = same outcome)
- Full logging (every decision is traceable)
- Validation against known social science findings

### 3. AI-Native Workflows

Design for Claude to operate. The `/miniverse` skill should let Claude guide users through:
- Research question → scenario design
- Simulation execution → analysis
- Interpretation → export

---

## Quick Orientation

### Key Files

| File | Purpose |
|------|---------|
| `miniverse/cli.py` | CLI entry point (run/list/info commands) |
| `miniverse/templates/` | Scenario templates (org-hierarchy implemented) |
| `miniverse/orchestrator.py` | Simulation loop, dependency injection |
| `miniverse/schemas.py` | All Pydantic models |
| `miniverse/cognition/` | Planner, executor, reflection, prompts |
| `miniverse/cognition/context.py` | Prompt context assembly (metadata stripped for agent privacy) |
| `miniverse/memory.py` | Memory strategies |
| `miniverse/persistence.py` | State persistence backends |
| `miniverse/viewer.py` | HTML transcript viewer (renders logs to self-contained HTML) |

### Common Commands

```bash
# Install dependencies
uv sync

# Run tests
UV_CACHE_DIR=.uv-cache uv run pytest

# --- CLI Commands (Phase 1 complete) ---

# List available scenarios
miniverse list

# Show scenario details
miniverse info org-hierarchy

# Run simulation (deterministic - no API key needed)
miniverse run org-hierarchy --ticks 10

# Run simulation (LLM cognition)
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o
export OPENAI_API_KEY=your_key
miniverse run org-hierarchy --ticks 10 --llm

# JSON output for scripts
miniverse run org-hierarchy --ticks 10 --output json

# Reproducible run with seed
miniverse run org-hierarchy --ticks 10 --seed 42

# Quiet mode (suppress per-tick output)
miniverse run org-hierarchy --ticks 10 --quiet

# Debug mode (enables DEBUG_LLM, DEBUG_MEMORY, MINIVERSE_VERBOSE)
miniverse run org-hierarchy --ticks 10 --debug

# --- Legacy Example (still works) ---
uv run python examples/workshop/run.py --ticks 6
```

---

## Architecture Overview

### Tick Flow

1. **Physics**: `SimulationRules.apply_tick()` updates deterministic state
2. **Perception**: Build partial observability view for each agent
3. **Cognition**: Planner/executor decide agent actions
4. **Actions**: Process actions (deterministic or LLM world engine)
5. **Memory**: Store observations for future context
6. **Reflection**: Periodic synthesis of experiences
7. **Persistence**: Save state, actions, memories

### Core Principles

1. **Dependency Injection**: Orchestrator receives all dependencies as constructor args
2. **Protocols for Interfaces**: `Planner`, `Executor`, `ReflectionEngine`, `MemoryStrategy`, `PersistenceStrategy`
3. **Structured Data**: Pydantic models everywhere
4. **Deterministic + Emergent**: Physics is predictable; cognition is creative

---

## How to Work Safely

### Before You Code

1. Check VISION.md – does this align with where we're going?
2. Check ROADMAP.md – which phase does this belong to?
3. Check the GitHub issue tracker – is there a related known issue?

### While You Code

1. Write tests alongside features
2. Use existing patterns (see `tests/` for examples)
3. Keep changes focused – one thing per PR
4. Update docs if you change behavior

### Before You Submit

1. Run `UV_CACHE_DIR=.uv-cache uv run pytest`
2. Verify examples still work
3. Update or close the related GitHub issue if you resolve something
4. Update ROADMAP.md if you complete a deliverable

---

## Code Style

### Patterns to Follow

- Pydantic for all data models
- Protocols for pluggable interfaces
- Async for IO-bound operations (LLM, persistence)
- Type hints everywhere

### Naming Conventions

- `*Strategy` for pluggable backends (Persistence, Memory)
- `*Engine` for processing modules (Reflection)
- `*State` for Pydantic schemas (WorldState, AgentStatus)
- `*Rules` for deterministic physics (WorkshopRules)

### Error Handling

- Tenacity for LLM retries
- Schema feedback for self-correction
- Clear exceptions for invalid states

---

## What NOT to Build (Yet)

Per ROADMAP.md, these are deferred:

- **Visualization dashboard** – Valuable but not on critical path
- **Advanced memory retrieval** – BM25/embeddings are nice-to-have
- **Multi-model comparison** – Useful for research but later
- **Real-time collaboration** – Enterprise feature, much later

Focus on: **CLI → Validation → Intervention → Export**

---

## Petri-Inspired Patterns

We're adopting patterns from Anthropic's [Petri](https://github.com/anthropics/petri) project:

### Branching/Rollback
```python
# Fork at decision point
branch = sim.fork()
branch.intervene(action="promote Alice")
result = branch.run(ticks=50)
```

### Multi-Dimensional Scoring
```python
SOCIAL_DIMENSIONS = {
    "goal_alignment": "Agent pursues stated goals",
    "information_fidelity": "Agent transmits info accurately",
    "coordination_success": "Agent coordinates effectively",
}
```

### Transcript Citations
```python
# Evidence linking for analysis
citation = Citation(
    tick=23,
    agent="alice",
    action="communicate",
    quote="Did you hear about the reorg?"
)
```

---

## Quick Checklist for New Features

- [ ] Aligns with VISION.md?
- [ ] Fits a ROADMAP.md phase?
- [ ] Has CLI interface (or contributes to one)?
- [ ] Has tests?
- [ ] Updates relevant docs?
- [ ] Maintains backward compatibility?
- [ ] Passes `uv run pytest`?

---

## Getting Help

- **Understanding the vision**: Read VISION.md
- **Finding what to work on**: Read ROADMAP.md
- **Known issues**: Use the GitHub issue tracker
- **Architecture questions**: Read `docs/architecture/`
- **When in doubt**: Ask before implementing new patterns

---

## Document Hierarchy

```
VISION.md          ← North star (what we're building toward)
    │
    ▼
ROADMAP.md         ← Implementation phases (how we get there)
    │
    ▼
CLAUDE.md          ← Operational guidance (how to work)
    │
    ▼
docs/              ← Deep dives (architecture, usage, research)
```

---

_Stay within these guardrails and Miniverse becomes the standard platform for computational social science._

-- Claude | 2025-12-29
