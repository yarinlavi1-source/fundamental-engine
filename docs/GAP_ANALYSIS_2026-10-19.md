# Gap analysis — 19.10.2026 (v0.9.1 repo + live "fundamental brain")

Scope inspected: this repository (`fundamental_engine/`, 249 passing tests, synthetic
fixtures), and the live brain the user actually runs (`claude/fundamental-brain/*`
in the Claude project: `engine.py`, `params.py`, `facts.py`, `brain_lite`, the
10/13 backtest, the 10/12 stock-finder study, the 10/19 quality scan, the picks
journal and the 10/18–10/19 deep-research runs).

## What works and must be preserved

- Repo: dated valuation contract, owner/financing model (`ownership.py`), funding-gap
  blocking, residual-income/NAV/rNPV routes, valuation audit gates, research journal,
  company-type classifier, forensic scores, Hebrew plain verdict, 249 tests.
- Brain: real point-in-time SEC + Yahoo pipeline; a pre-registered 2011–2025 backtest
  with honest negative results; pre-registered rules; picks journal written before
  outcomes; the finding that earnings momentum + near-52w-high beats the average stock
  and that "already priced" flags crash risk.

## Prioritized weaknesses

| # | Weakness | Severity | Where | Consequence | Fix | Test |
|---|---|---|---|---|---|---|
| 1 | No deterministic capital-structure / per-share bridge. Converts, accreting principal, warrants, prepayments, leases and future financing are typed by hand. | Critical | brain `engine.analyze` (`net_cash` = cash+STI−debt), `params.NBIS_MANUAL`, `run_holdings` share overrides; repo `ownership.py` knows only plain equity/debt | Real errors in the last 48h: ASTS net cash +$1.6B instead of −$0.95B (double count, missing converts); NBIS 280M shares instead of 308.7M; NBIS future debt (~$30B) ignored; an earlier NBIS thesis claimed a 19% cost of debt (actual 4–7%) | `capital.py`: instrument-level ledger (cash classes, debt at accreted value, convert if-converted vs net-share, warrants, RSU, prepayments as quasi-liability), fully-diluted equity bridge, projected financing | Golden tests from SEC numbers for NBIS & ASTS |
| 2 | No independent accounting red-team with hard verdicts | Critical | repo `valuation_audit.py` checks evidence references, not arithmetic relationships; brain has none | Errors above passed silently into decisions | `redteam.py` → Passed / Passed-with-limitations / Failed / Unable-to-verify; a Failed critical check marks valuation unverified | Fault-injection tests (double count, ARR-as-revenue, backlog-as-revenue, prepayment-as-profit, missing SBC, stale shares) |
| 3 | The "expectation gap" is not operationalized correctly. Reverse-DCF starts from TTM revenue and a net-margin path; valuation is "net margin × P/E", blind to capex/ROIC | Critical | brain `reverse_dcf`, `scenario`; brain_lite; repo `finance.reverse_dcf` (constant growth, fixed reinvestment) | Over/understated implied growth for hyper-growers (NBIS "47%/yr"); capital-heavy firms valued on EBITDA-like logic; backtest: valuation gap has ~0 rank correlation with returns (0.01–0.03) | `expectations.py`: solve the price-implied driver path inside the same driver→FCF→per-share model (forward start, reinvestment and dilution linked), output what must be true (revenue, margin, ROIC, unit price) and compare with base rates and the company's demonstrated trajectory → explicit gap with evidence | Analytic test cases + NBIS regression (implied $/MW) |
| 4 | Signals are not tested by company stage and failure cases | High | stock-finder/backtest (whole universe only); repo has only synthetic cases | Risk of learning success stories in hindsight; no evidence the engine separates early-commercial, turning-profitable, failed, and over-priced quality | Point-in-time case suite across the 4 groups + universe-level test of the expectation-gap and inflection signals, pre-registered, out-of-sample | Reproducible PIT dataset build + tests on the builder |
| 5 | Growth analysis is YoY-only; no inflection detector with organic/acquisition separation and incremental economics | High | repo `growth.momentum` (first vs last YoY, single margin delta); brain uses one quarter | One strong quarter extrapolated; acquired growth counted as organic (APH/ONDS needed manual patches) | `inflection.py`: sequential + YoY acceleration with persistence, goodwill/acquisition flags, incremental gross/operating margin, cash-burn trajectory, dilution per unit of growth | Unit tests on synthetic series + real cases |
| 6 | Scores are opaque single numbers; no evidence trail per component | High | brain gates G1–G7, quality-scan 0–100 score; repo plain grades | User cannot see what raised/lowered a rank or what falsifies it | Component scorecard with value, peer/stage percentile, evidence ids, direction, falsifier; no blended "AI score" | Snapshot tests |
| 7 | No forecast ledger that scores the engine's own predictions over time | High | picks journal is prose; deep-research base cases not stored as data | Cannot measure if the engine improves (forecast accuracy, calibration, return vs benchmark) | `ledger.py`: append-only, hashed forecasts (metric, period, value, scenario probabilities, price) + scorer when actuals arrive | Tests for immutability/look-ahead/scoring |
| 8 | Two diverging engines; live numbers come from untested `.txt` code in the project | Medium-High | `claude/fundamental-brain/*.py.txt` vs repo | Fixes do not propagate; regressions invisible | Move brain calculators into the repo behind tests; brain runs import repo modules | CI |
| 9 | Discovery uses rigid universal thresholds (14% growth, 40% GM) and social ideas ad hoc | Medium | QUALITY_COMPOUNDER_RULES, Finviz screen | Misses capital-light-but-low-GM and early inflections; no exploratory queue | Stage-relative percentiles; exploratory queue; social-signal hypothesis ledger | Later stage |
| 10 | Macro/geopolitical context is a single 200-day trend rule | Medium | stock-finder proposal | No explicit transmission to company drivers (rates→financing cost, export controls→TAM) | Mechanism table feeding driver assumptions only | Later stage |

## Implementation sequence (by expected decision value)

1. **Capital structure + red-team (W1, W2)** — removes the class of errors that already
   changed real position decisions.
2. **Expectation-gap engine (W3, W6)** — the user's core objective: the distance between
   what the business can become and what the price assumes, shown component by component.
3. **Point-in-time validation (W4, W5, W7)** — build the dataset in the repo, the inflection
   detector, the stage case suite and the forecast ledger; pre-register; test out of sample.
4. Unify brain into repo (W8), then discovery, value chain, social and macro (W9, W10).
