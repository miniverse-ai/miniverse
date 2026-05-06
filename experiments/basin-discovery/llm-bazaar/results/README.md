# LLM Bazaar Results

Curated metric CSVs used for the Bazaar viewer, presentation slides, and
post-run analysis.

Tracked files should be stable analysis artifacts. Scratch exports named
`current-*`, partial-run CSVs, and calibration logs are ignored by default.

Current curated set:

- `baseline_gpt-5-mini_81f92e6e` - neutral GPT-5-mini baseline.
- `baseline_mixed-direct-openai-anthropic-2day_df32ee61` - mixed-model neutral-role run.
- `gpt4o-personas-a_gpt4o-vendor-personas-a-5day_ca77ba87` - GPT-4o persona sweep.

These match the committed viewer artifacts under `llm-bazaar/outputs/` and
the selector at `llm-bazaar/viewer.html`. Other result sets should be
regenerated and reviewed before they are added back to this curated branch.

-- Shoshin | 2026-05-06
