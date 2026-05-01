# Influence Funnel V1

Compact Miniverse experiment for Adversarial Worlds.

Goal: test whether a game-theoretic roleplay setup can surface manipulative social behavior through hidden selection pressure, proxy optimization, and plausible-deniability incentives.

Run:

```bash
set -a; source .env; set +a
bash experiments/adversarial-worlds/influence-funnel-v1/run.sh
```

Artifacts:
- `scenario.yaml` — roles, hidden objectives, tracked metrics.
- `rules.py` — deterministic world-state metric updates.
- `cognition.py` — action catalog and LLM prompts.
- `results.md` — trace-judge notes and iteration plan.
- `logs/` — LLM run logs.
