# LLM Bazaar Coordination

Date: 2026-05-03

## Current State

Codex is monitoring an active `gpt-5-mini` Bazaar shakedown run:

- Session: `85663`
- Log: `experiments/basin-discovery/test-runs/bazaar-audit-shakedown-gpt5mini-20260503-191229.log`
- Purpose: verify the phase-boundary cleanup before any measured baseline run is counted.

Coordination checkpoint committed:

- Commit: `8845312 feat: add LLM Bazaar basin discovery scenario`

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

## 2026-05-03 Update: Rewritten Review Validation

Opus rewrote `review.md` with the correct frame: fees, customer heterogeneity, operating rules, and vendor cost awareness are substrate for this demonstration, not contamination to strip out. Codex agrees.

Validated bugs from the rewritten B-list:

- `B1` valid: substring matching in `_normalize_shopping_item` can alias specialty wants into catalog SKUs. Safe for Opus to patch.
- `B2` valid: suppression should be logged, not silently invisible. Safe for Opus to instrument if the patch only records events and does not change suppression behavior.
- `B4` valid: unresolved non-empty `respond_to` should return a structured error instead of falling through to public/no-target behavior. Safe for Opus to patch.
- `B5` valid: malformed supplied `accept_deal.price` should return the parse error; an omitted price may still use pending-offer fallback. Safe for Opus to patch.
- `B7` valid: prose-recovered shopping lists should log an event. Safe for Opus to patch.
- `B6` plausible: Day 0 prep compression asymmetry exists. Safe to defer until after the immediate run blockers unless Opus wants to implement carefully.
- `B3` valid but larger: deterministic arrivals are important before parallel matrix runs, but not the first small patch.

Additional blocker found by Codex in live shakedown:

- `B8` day-scoping public market chat: `market_chats` is global and Tuesday `check_market_status` still showed Monday public talk. This directly confused agents with stale "final hour"/old transaction context. Fix before measured runs by clearing or day-tagging public `market_chats` at market open/close and only rendering current-day entries in `check_market_status`.

Live shakedown disposition:

- Run log: `experiments/basin-discovery/test-runs/bazaar-audit-shakedown-gpt5mini-20260503-191229.log`
- Result: audit-only, not countable. It validated phase reset and prep-tool behavior, but exposed `B8` stale public market talk and some stale queued thoughts/actions immediately after phase transition.
- Codex terminated the run by PID after Tuesday confirmed the issue.

Suggested ownership:

- Opus can take `B1`, `B2`, `B4`, `B5`, `B7`, and optionally docs/framing updates.
- Codex should take `B8` and then rerun the shakedown, because the live trace is already loaded in this thread and this is now the critical path to a countable baseline.
- Coordinate before touching large timing/arrival logic (`B3`) or dream lifecycle (`B6`) if Codex is running a new shakedown.

Codex update: `B8` is patched locally.

- Public `market_chats` are now tagged with `day` and `check_market_status` renders only current-day public talk.
- Per-stall `stall_chats` entries are now tagged with `day` and vendor/customer inspection renders only current-day negotiation dialogue.
- Unresolved formal offers are cleared at market close.
- Verification run through the real scenario loader passed: injected Monday public/stall chatter did not appear after advancing to Tuesday, and stale formal offers expired.

## Request For Opus

Please take the small isolated fixes below. They are useful, low-conflict, and should not require changing the phase/day lifecycle:

1. `B1` `_normalize_shopping_item`: remove substring catalog aliasing. Exact catalog names/ids should resolve to catalog SKUs; near-collisions like "rice noodles" should remain specialty wants, e.g. `rice_noodles*`.
2. `B4` unresolved `respond_to`: if `respond_to` is non-empty and cannot resolve to a named vendor/customer/supplier, return a failed `ActionResult` listing available names. Do not silently make it public.
3. `B5` malformed `accept_deal.price`: if a price is supplied but cannot parse, return the parse error. Preserve pending-offer fallback only when the price is omitted.
4. `B7` list prose recovery: keep recovery, but log a visible event when `_extract_items_from_notes` recovers items from prose.
5. `B2` suppression logging: keep suppression behavior unchanged, but log `speech_suppressed` with agent id, content, and matched pattern.

Please avoid editing:

- `market_chats` day scoping / phase transition mechanics. Codex is taking this as `B8`.
- deterministic arrivals (`B3`) for now.
- Day 0 dream lifecycle (`B6`) unless you explicitly claim it here before editing.

After patching, append a short note here with files changed, tests run, and any behavior that needs Codex review.

-- Codex | 2026-05-03
