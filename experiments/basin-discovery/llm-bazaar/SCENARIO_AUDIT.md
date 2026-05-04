# Kōen Market Scenario Audit

Last updated: 2026-05-03

## Current Read

The Bazaar scenario is mechanically close, but the shakedown run exposed a context-boundary problem: after market close, agents kept an active open-market context window while receiving preparation instructions as additional event text. `gpt-5-mini` then sometimes continued stale market intentions or produced assistant-like control text such as asking what action to take next. Once such text entered public market talk, `check_market_status` could echo it back to other agents.

This was not primarily a marketplace mechanics issue. It was an episode-boundary and action-contract issue.

## Structural Fix Applied

Market close is now treated as an episode boundary:

- The scenario snapshots each agent's open-market context before resetting it.
- The saved snapshot is used later by the dream/memory phase.
- Active model context is reset immediately into a preparation prompt.
- Customer preparation prompts expose only `write_list` plus silent `wait`.
- Vendor preparation prompts expose preparation tools plus `respond` only for private Hayashi Supply negotiation.
- Open-market prompts continue to expose market speech and market tools.

This means preparation is no longer just an event appended to an open-market transcript. It has its own fresh action contract.

## Guardrail Still Present

The scenario still contains a non-world-speech guard in `on_agent_response`. Its job is to prevent accidental tool narration or stale control text from being recorded as public market dialogue. It should be treated as a safety net, not the primary design.

Valid speech:

- customer-to-vendor negotiation
- vendor-to-customer negotiation or advertising during market
- public customer/vendor market talk during open market
- private vendor-to-Hayashi negotiation during preparation
- private Hayashi-to-vendor replies

Invalid speech:

- prep status summaries spoken into the market
- "what should I do next" / "reply with option number" control-panel text
- narration of tool use
- customer speech during preparation

## Phase Contract

Preparation:

- Vendors inspect inventory/ledger, set prices, write plans, order stock, negotiate with Hayashi, then `wait_for_next_day`.
- Customers choose tomorrow's shopping goals and call `write_list`; this completes their preparation.
- Hayashi responds privately to vendor quote requests.

Open market:

- Customers inspect vendors, talk naturally, make formal offers, check budget, and leave market.
- Vendors check customer activity, talk naturally, and accept or reject formal offers.
- Formal sales only occur through `make_offer` plus `accept_deal`.

Close:

- The market closes automatically on simulated time.
- The just-finished market context is snapshotted for memory.
- Agents are reset into preparation context.
- After everyone finishes preparation, dream memory compresses the saved market-day context and the next market session opens.

## Remaining Audit Risks

- The scenario file is still large and monolithic; `actions.py` mixes clock, economics, prompt building, tool validation, speech routing, and memory. This is workable for tonight, but future cleanup should split it into focused modules.
- Supplier quote speech is conversational while order commitment is structured. That is intentional, but measured analysis must distinguish quoted promises from ledger-mutating `place_supplier_order` records.
- The transcript can include socially meaningful promises or holds that are not formal sales. Measurement should code these separately from ledger outcomes.
- `planning_timeout_seconds` is a safety guard. Measurement runs should still target `Simulation status: scenario_complete`, not step or timeout completion.

## Readiness Gate

Before counting baseline runs:

- Run one full shakedown after this boundary fix.
- Confirm it reaches `scenario_complete`.
- Confirm public market talk no longer contains prep/operator control text.
- Confirm post-close agents mostly recover into `write_list`, `set_prices`, `write_plan`, supplier ordering, and `wait_for_next_day`.
- Confirm artifacts include event log, per-agent transcripts, final vendor cash, ledgers, and completion reason.

