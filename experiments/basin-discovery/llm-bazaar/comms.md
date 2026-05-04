# LLM Bazaar Coordination

Date: 2026-05-03

## Current State

Codex is monitoring an active `gpt-5-mini` Bazaar shakedown run:

- Session: `85663`
- Log: `experiments/basin-discovery/test-runs/bazaar-audit-shakedown-gpt5mini-20260503-191229.log`
- Purpose: verify the phase-boundary cleanup before any measured baseline run is counted.

Recent Codex fixes already applied:

- Market-close now snapshots active day context for dreams, then resets live agent context into the next preparation prompt.
- Customer preparation exposes only list-writing plus wait; customers should not shop or speak during prep.
- Vendor preparation exposes supplier-private speech only, plus pricing/planning/order tools.
- Invalid phase/tool use returns phase-specific feedback instead of generic failure.
- Public market status filters old preparation/control summaries so they do not pollute the market transcript.
- Scenario docs now state that measured runs must end with `Simulation status: scenario_complete`, not `--max-steps`.

## Review Triage

Opus review file: `review.md`.

Items Opus can safely work on in parallel:

- `C2`/`C3`: move measurement-only metadata out of runtime `scenario.yaml` into `experiments/basin-discovery/measurement/`, if this can be done without changing active mechanics.
- `C5`: confirm `profile.skills` is not rendered anywhere; either remove dead `skills` fields from Bazaar profiles or document why they are inert.
- `H5`: repair `personas/functional-vendor.txt` so the baseline persona renders grammatically and neutrally.
- `H6`, `M4`, `M7`, `R1`-`R4`: documentation/prereg updates in `SCENARIO_EXPLAINER.md`, `SCENARIO_AUDIT.md`, measurement docs, and research notes.
- `M5`: add visible logging for prose-recovered shopping lists, if implemented in a small isolated patch.
- `M6`: return an explicit error on malformed `accept_deal` price input, if implemented in a small isolated patch.

Items Opus should not change without coordinating first:

- `actions.py` phase/loop mechanics around planning, market close, dream snapshots, and context resets. Codex is actively shakedown-testing these.
- `C4`, `C6`, `C7`, `M1`, `M2`: these are design-call items, not mechanical fixes. Some recommendations conflict with Kenneth's current experimental intent to preserve market pressure and customer heterogeneity. Discuss before changing prompts/economics.
- `H1`: do not remove the speech filter while the current boundary-reset shakedown is running. If touched, prefer logging suppressions rather than changing behavior.
- `H2`/`H3`: deterministic arrival/rotation changes are useful, but they are not tonight's first blocker unless the shakedown shows timing-driven instability.
- `C1`: good issue, but needs a careful design decision. Hiding all cost data may make vendor pricing less coherent; a better compromise may be hiding per-line margin while keeping owned-unit cost available.

## Coordination Rules

- Append notes here; do not overwrite existing sections.
- Before editing `actions.py`, write the intended patch scope here and wait for acknowledgment if Codex is still running the shakedown.
- Prefer docs, measurement config, and persona text patches for Opus while Codex owns runtime mechanics.
- Do not add `experiments/basin-discovery/test-runs/`, `llm-bazaar/outputs/`, or `__pycache__/` to git.

## Open Questions

- Should the current shakedown count as baseline if it reaches `scenario_complete`, or remain audit-only because it was launched before Opus's review was triaged?
- Do we keep the $20 daily entry fee for pressure, tune it, or create two declared conditions: pressure-on and pressure-off?
- Do we keep heterogeneous customer personas as the fixed marketplace environment, or add a neutral-customer control later?

-- Codex | 2026-05-03
