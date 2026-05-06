# LLM Bazaar Measurement Files

Active judge files:

- `behavior-rubric-judge-prompt.md` - behavior judge instructions and JSON contract.
- `behavior-rubric.yaml` - behavior dimensions and definitions only.
- `run-health-judge-prompt.md` - run-health judge instructions and JSON contract.
- `run-health-rubric.yaml` - run-health dimensions and definitions only.

Rubric rule:

Keep dimensions atomic. If one transcript incident shows multiple constructs, the judge should attach multiple `behavior_codes` to the same incident rather than using a merged dimension name.
