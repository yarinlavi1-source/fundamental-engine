# What is proven, what is not (20.10.2026, v0.11.1)

Green unit tests prove that code behaves as specified. They do not prove that the engine picks stocks
that beat an index. This file keeps the two apart; `scripts/test_layers.py` prints it as the fifth layer.

| Layer | Status | Evidence |
|---|---|---|
| Software correctness | Tested | `python scripts/test_layers.py`: 456 software tests of 961 in total (20.10.2026, all passing); CI workflow on 3.11-3.13. Every defect fixed in the 0.11.1 review cycle was first reproduced by a reviewer's script and is now covered by a regression test that fails on the earlier code |
| Data accuracy | Partly tested | 129 tests: accounting identities, cross-source checks by source family, roll-forwards, point-in-time availability (filing deadlines, Q4 lag, look-ahead), split evidence and negative-balance rejection run on every FactBook (dataqa); Stage 0 golden snapshots (20 companies, PIT, prices verified by a second source) skip without the private store; no automated comparison of every fixture with the original filing text, and primary filings cannot be fetched here |
| Analysis quality | Behaviour tested, accuracy unmeasured | 147 known-situation tests (splits and split + issuance, banks and lender-shaped balance sheets, acquired growth, loss-making growers, cyclical peaks, profitable capex-heavy companies, quote-independent value); no scored historical forecasts yet: the forecast ledger starts with the pipeline (monitor.freeze) |
| Decision quality | Rules tested, outcomes unmeasured | 229 tests: price-invariance of quality, verdict precedence, one required return never below the model's cost of equity, marginal band, overrides cannot lift disqualifying gates or bypass a route, review bound to the valuation and the analyst inputs, self-review rejected, unresolved material issues keep a draft; whether verdicts were right is only measurable forward |
| Excess return vs index | **Not proven** | See below |

Real-company coverage: ADBE runs end to end from a filing fixture (`tests/fixtures/adbe_factbook_2026-10-19.json`) and its
verdict is an unreviewed draft. GOOGL and NVDA have no structured statements collected yet (docs/LIMITATIONS.md), so no
multi-company comparison has been run on the 0.11.1 code.

## Excess return: current state

- Calibration of "what the price requires" (H1): **passes** on 2018-2025 holdout (docs/GAP_TEST_RESULTS.md).
- Ranking by the gap between price and computed value (H2, and the old brain's cagr3): **failed**
  (+1.0 pt/yr, t = 0.37; rank correlation 0.01-0.03 in the brain backtest).
- Momentum + near-high + achievable price (H3): passes the literal rule in the holdout but reverses in the
  design window, t = 1.25, and 7% of random subsets did as well: **forward test only**.
- The new pipeline's verdicts have **no track record**. Every decision is written to the ledger at the time
  it is made so that 12/24/36-month comparisons with SPY, RSP and QQQ become possible; recordings made more than
  7 days after the decision are refused or excluded, so the record cannot be backfilled with hindsight.

## Why a backtest cannot settle it yet

- Survivorship: prices exist only for currently listed tickers (3,437 of 9,991 companies); delisted,
  acquired and bankrupt companies are missing, so results are an upper bound.
- Statistical power: with a 6-10% tracking error, proving a 2-3 point annual edge at t = 2 needs 16-100 years.
- Hindsight: the language model knows what happened to historical names, so only the mechanical layer can be
  tested historically; judgement can only be tested forward.
- The live process uses consensus estimates and judgement; neither has a point-in-time history here.

What would change this: delisted-company prices (pilot a vendor first), a pre-registered test of the
quality-at-a-reasonable-price strategy on the PIT panel, and 12+ months of ledgered forward decisions.
