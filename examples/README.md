# Miniverse Examples

This directory keeps the maintained code examples.

## Workshop Examples

The `workshop/` directory contains progressive examples showing increasing complexity:

- `01_hello_world` - one hardcoded agent.
- `02_deterministic` - threshold logic with two workers.
- `03_llm_single` - one LLM-driven agent.
- `04_team_chat` - multi-agent communication.
- `05_stochastic` - random events plus LLM adaptation.

Run from the repo root:

```bash
uv run python -m examples.workshop.01_hello_world.run
uv run python -m examples.workshop.02_deterministic.run
uv run python -m examples.workshop.03_llm_single.run
uv run python -m examples.workshop.04_team_chat.run
uv run python -m examples.workshop.05_stochastic.run
```

## Smallville Valentine's

`smallville/` keeps the Stanford Generative Agents-style Valentine's Day party example.

```bash
uv run python examples/smallville/valentines_party.py
```

## LLM Configuration

LLM examples require provider env vars:

```bash
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-5-mini
export OPENAI_API_KEY=your_key
```
