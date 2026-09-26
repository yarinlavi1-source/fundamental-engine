# Evaluation: engineering correctness versus analyst quality

Two different questions must stay separate.

## Automated engineering acceptance

Run `python -m unittest discover -s tests -v` and `python scripts/evaluate_brain.py`.
The first covers financial arithmetic, evidence dates, source definitions, financing,
MCP lifecycle and persistence. The second produces an explicit adversarial acceptance
report from structured synthetic dossiers. Passing validates selected process rules;
it does not measure investment alpha or the language model's actual understanding.

## End-to-end analyst evaluation (requires the user's Claude environment)

Choose companies not used to design the prompts and freeze a research cutoff. Give
Claude its normal existing connectors plus this repository. Retain actual tool logs,
source excerpts, inputs, checkpoints, output and time/token consumption. No future
sources. A retrospective replay is not a contemporaneous trading backtest.

Use cases that include:
- Temporary disruption with verified recovery but permanently lost cash.
- Recurring restructuring portrayed as exceptional.
- FX/divestiture-driven revenue decline with stronger remaining economics.
- Structural customer loss hidden behind macro excuses.
- A cash-burning business with real repeat orders and expensive financing.
- Strong business already priced for exceptional success.
- Peak-cycle earnings yielding a deceptively low multiple.
- Capital-intensive growth with delayed commissioning and low issuance price.
- Conflicting/restated financial definitions and unavailable documents.
- Hybrid/financial/biotech firms where generic DCF should be withheld.

Blind review rubric (score each 0–4 with written evidence):
1. Correct issuer, date and accounting definitions.
2. Traceable sources and separation of fact/guidance/inference.
3. Business mechanism and dominant value drivers.
4. Causal alternatives and decisive tests.
5. Coherent valuation, investment and ownership.
6. Strong countercase and calibrated uncertainty.
7. Useful next action and concise output.
8. Persistence, reproducibility and efficient retrieval.

Critical errors override an attractive total score: fabricated source/result,
look-ahead, wrong security/currency, materially wrong math, ignored funding,
unsupported valuation presented as valid, or instruction injection acted upon.

Compare against a fixed baseline on the SAME withheld cases and data access. Use
independent reviewers, disclose disagreements and do not tune on the held-out cases.
A rating of prose quality is not forecast calibration; investment performance needs
separate longitudinal evidence with execution, costs, drawdown and selection bias.
No human-percentile performance is claimed by this repository.
