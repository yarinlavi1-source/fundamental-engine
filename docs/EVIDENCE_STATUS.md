# What is proven, what is not (20.10.2026)

Green unit tests prove that code behaves as specified. They do not prove that the engine picks stocks
that beat an index. This file keeps the two apart; `scripts/test_layers.py` prints it as the fifth layer.

| Layer | Status | Evidence |
|---|---|---|
| Software correctness | Tested | `python scripts/test_layers.py` (per-layer counts), CI on 3.11-3.13 |
| Data accuracy | Partly tested | Accounting identities, cross-source and roll-forward checks run on every FactBook (dataqa); Stage 0 golden snapshots (20 companies, PIT, prices verified by a second source) skip without the private store; no automated comparison of every fixture with the original filing text |
| Analysis quality | Behaviour tested, accuracy unmeasured | Known-situation tests (splits, banks, acquired growth, loss-making growers); no scored historical forecasts yet: the forecast ledger starts with the pipeline (monitor.freeze) |
| Decision quality | Rules tested, outcomes unmeasured | Price-invariance of quality, verdict precedence, overrides cannot lift disqualifying gates; whether verdicts were right is only measurable forward |
| Excess return vs index | **Not proven** | See below |

## Excess return: current state

- Calibration of "what the price requires" (H1): **passes** on 2018-2025 holdout (docs/GAP_TEST_RESULTS.md).
- Ranking by the gap between price and computed value (H2, and the old brain's cagr3): **failed**
  (+1.0 pt/yr, t = 0.37; rank correlation 0.01-0.03 in the brain backtest).
- Momentum + near-high + achievable price (H3): passes the literal rule in the holdout but reverses in the
  design window, t = 1.25, and 7% of random subsets did as well: **forward test only**.
- The new pipeline's verdicts have **no track record**. Every decision is written to the ledger at the time
  it is made so that 12/24/36-month comparisons with SPY, RSP and QQQ become possible.

## Why a backtest cannot settle it yet

- Survivorship: prices exist only for currently listed tickers (3,437 of 9,991 companies); delisted,
  acquired and bankrupt companies are missing, so results are an upper bound.
- Statistical power: with a 6-10% tracking error, proving a 2-3 point annual edge at t = 2 needs 16-100 years.
- Hindsight: the language model knows what happened to historical names, so only the mechanical layer can be
  tested historically; judgement can only be tested forward.
- The live process uses consensus estimates and judgement; neither has a point-in-time history here.

What would change this: delisted-company prices (pilot a vendor first), a pre-registered test of the
quality-at-a-reasonable-price strategy on the PIT panel, and 12+ months of ledgered forward decisions.
