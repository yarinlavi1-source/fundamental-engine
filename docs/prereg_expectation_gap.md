# Pre-registration: does the expectation gap predict business surprises and returns?

Written 19.10.2026 BEFORE the point-in-time dataset is built or any result is seen.
Code: `scripts/pit_build.py`, `scripts/gap_backtest.py`. Any deviation is logged below with a date.

## Question
The user's objective is a gap between what a business can become and what the price assumes.
The 10/13 brain backtest found the old valuation gap had ~0 rank correlation with returns, but it
started from TTM revenue and net-margin multiples. This test asks whether a correctly specified
reverse model plus an empirical, conditional base rate carries information.

## Data (point in time)
- Fundamentals: SEC companyfacts bulk (all filers), quarterly values reconstructed from 10-Q/10-K
  with the FIRST filed value for each period; a fact is usable only from `filed + 2 days`.
- Prices: Yahoo monthly adjusted closes for currently listed Nasdaq/NYSE tickers.
- Known bias: delisted/acquired companies have fundamentals but no prices here. Coverage is reported.

## Universe, each month-end t, 2011-01 .. 2025-12
TTM revenue ≥ $50M, market cap (dei cover shares × price) ≥ $300M, last filing ≤ 150 days old,
domestic us-gaap filers. Financial companies (banks/insurers, SIC 6000-6799) are excluded from
the gap model (revenue/reinvestment model does not apply) but kept in the stage tables.

## Implied growth (uniform, fixed before seeing outcomes)
Driver model `fundamental_engine.expectations.run_model`: forward revenue = TTM; 10 years; constant
growth g for 5 years then linear fade to 3%; mature EBIT margin = clip(best TTM operating margin of
the last 12 quarters, 8%, 35%), reached in 5 years from the current TTM margin; tax 21%; sales-to-capital
1.5; WACC 9%; terminal ROIC = max(final ROIC, WACC). Solve g so EV = market cap + debt − cash.

## Business capability (empirical base rate)
Bucket table, refit at every month t on starts s with s + 36 months ≤ t only (expanding window, no outcome
after t is ever used): buckets = TTM revenue size (5) × trailing YoY growth (6) × acceleration sign (2) ×
margin-trend sign (2); buckets with < 30 observations fall back to size × growth.
P(beat) = share of bucket observations whose realized 3-year revenue CAGR ≥ the implied g.
Months before 2013 have too few completed outcomes and are skipped. Design window = 2013–2017, holdout = 2018–2025.

## Hypotheses and pass criteria (all must hold to call the signal useful)
H1 Calibration: in the holdout, realized beat frequency rises monotonically across P(beat) quintiles and the
   top-minus-bottom difference in realized beat rate is ≥ 15 points.
H2 Returns: equal-weight top P(beat) quintile, monthly rebalanced, 12-month forward returns, beats the
   eligible universe by ≥ 2 points/yr in BOTH windows, holdout t-stat ≥ 2, and its max drawdown is not
   worse than the universe by more than 10 points.
H3 Interaction with the proven signal (earnings momentum + near 52w high, 10/12 study): adding the top-two
   P(beat) quintiles must raise the holdout return of that sleeve by ≥ 2 points/yr without raising the
   share of positions that halve.

## Stage tables (descriptive, include failures)
Early (TTM op margin < 0, growth ≥ 25%), turning profitable (op margin crossed 0 within last 4 quarters),
compounder (op margin > 15%, growth 10–25%), priced for perfection (implied g ≥ 30% and P(beat) < 15%),
other. For each: count, median 36m forward return, % halved within 12m, % tripled within 36m, % still
listed, and named examples from both tails.

## Not allowed
Changing buckets, assumptions, thresholds or windows after looking at holdout results. If a bug is found,
fix it, re-run everything, and log the bug and both results here.

## Deviations log
(none yet)
