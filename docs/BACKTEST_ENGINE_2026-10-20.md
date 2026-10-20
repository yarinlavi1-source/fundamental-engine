# The valuation engine against the S&P 500: walk-forward test (20.10.2026)

Question: if, every 1 July from 2016 to 2025, the full engine had valued each large company with only what was filed by
then, would the companies it ranked highest have beaten the index over the next twelve months?

Code: `fundamental_engine/engine_backtest.py`, `scripts/backtest_engine.py`. Rows and summary (outside Git):
`runs/backtest/engine_v1/rows.jsonl`, `summary.json`. Every number below is reproducible from them.

## Method

- Universe: the ~490 largest US operating companies of 20.10.2026 (the calibration sweep), each with its saved SEC
  companyfacts. 440 had prices and filings at a typical formation, 338 of them with a computed implied return.
- At each formation (1 July 2016 ... 1 July 2025) the whole pipeline ran (dataqa, analysis, model card, forecast,
  valuation, risk, review, decision) with as_of = 30 June: only facts filed by then (first-filed values, the facts
  layer's point-in-time rule), the June close as traded then (split-adjusted close x later splits), and the June average
  10-year Treasury yield (FRED GS10, published by then) as the risk-free rate. Annual and quarterly reports saved in 2026
  are skipped for earlier dates.
- Ranking: the base-case implied return (IRR). Portfolios held 12 months, equal weight, total return from the adjusted
  close, minus 0.2% a year. Controls on the same names and dates: the cheapest fifth by trailing earnings yield and by
  trailing free-cash-flow yield.
- Engine version: the code of commit 08c2263 (before the 20.10 fixes for acquisitions, divestitures, cycles, thin
  margins, new types and financials).

## Results

| Portfolio (average names) | CAGR | vs the average stock in the universe, mean (t) | vs S&P 500, mean |
|---|---|---|---|
| Engine top fifth (63) | 27.9% | +7.6 pp (t 1.0) | +15.9 pp |
| Engine top fifth, data not blocked (55) | 29.0% | +9.1 pp (t 1.1) | +17.4 pp |
| Engine bottom fifth (63) | 22.0% | -0.7 pp (t -0.2) | +7.5 pp |
| Positive edge over the engine market (157) | 22.9% | +0.1 pp | +8.4 pp |
| Positive edge and a positive bear return (127) | 20.5% | -2.9 pp (t -1.4) | +5.3 pp |
| Control: cheapest fifth by FCF yield (68) | 26.5% | +6.0 pp (t 0.8) | +14.3 pp |
| Control: cheapest fifth by earnings yield (75) | 18.5% | -4.5 pp (t -1.9) | +3.8 pp |
| Average stock in the universe | 23.0% | | |
| Cap-weighted universe | 18.3% | | |
| S&P 500 (SPY) | 15.4% | | |

By year (top fifth / bottom fifth / universe average / SPY, %): 2016 37/32/28/18, 2017 13/29/22/14, 2018 11/15/13/10,
2019 -1/30/12/7, 2020 138/49/65/41, 2021 3/-25/-9/-11, 2022 27/28/26/19, 2023 34/26/25/25, 2024 23/22/24/15,
2025 35/29/38/22. Rank correlation between implied return and the next year's return: mean 0.02 (t 0.4), between -0.27
and +0.35.

## What this says

1. **No demonstrated edge.** The top fifth beat the average stock of the same universe by 7.6 points a year with a
   t-statistic of 1.0, in 5 of 10 years; without 2020 the excess is +0.4 points. Top minus bottom: +8.3 points (t 0.8).
   The ranking has no reliable information about the next year's return (rank correlation 0.02).
2. **The engine did about what a free-cash-flow-yield rank did** (+1.6 points a year over it, t 0.8): on this evidence it
   adds little to a naive cheapness rank.
3. **Survivorship is the largest number in the table.** The average stock of today's large caps returned 23% a year
   against 15.4% for the S&P 500: being in today's list was the return. Every portfolio here beats SPY for that reason;
   the comparisons that mean something are against the universe average and the controls.
4. Requiring a positive bear-case return (a margin-of-safety rule) did worse than the universe, not better.

## What follows for the system

- The index remains the default holding; active positions stay small and are sized by allocation.py's haircuts.
- A mechanical ranking from the engine is a research queue, not a buy list. The case for any position has to come from
  research the backtest cannot see (business understanding, evidence, a reviewed thesis), and it is recorded in the
  forward ledger so it can be judged on results it could not have known.
- The run is repeated once on the current engine (after the 20.10 fixes) and reported as it comes out. The fixes were
  made from valuation audits of individual companies, not from this result; nothing is tuned to improve it.

## Bias and limits

- Survivorship: today's large caps only; companies that failed or shrank out of the list are absent. This flatters every
  portfolio and especially cheapness rankings (a cheap company that later failed is missing).
- In-sample design: the engine's rules were written in 2025-2026 with knowledge of the period.
- Today's SIC codes and listing labels; no consensus history (the forecast uses reported history only).
- One formation a year, ten years: a t-statistic below about 2 is noise.
- No taxes, capacity or timing effects beyond the stated 0.2% a year.
