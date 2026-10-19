# Expectation-gap test — results (19.10.2026)

Pre-registration: `docs/prereg_expectation_gap.md` (written before the dataset existed; deviations
and bug fixes logged there). Code: `scripts/pit_build.py`, `pit_panel.py`, `gap_backtest.py`,
`gap_verdict.py` (mechanical pass/fail), `case_suite.py`; exploratory only: `gap_explore.py`,
`gap_h3_robust.py`. Raw outputs are not in Git (SEC/Yahoo data, ~1.5 GB); every number below is
reproducible from the scripts.

## Bottom line

| Hypothesis | Verdict (run 3, judged) | What it means |
|---|---|---|
| H1 calibration | **PASS** | When the engine says "x% of comparable businesses grew as fast as this price requires", that is roughly what happened. It measures what the price assumes, correctly. |
| H2 top P(beat) quintile beats the universe | **FAIL** | Knowing which prices are *achievable* earns nothing by itself: +1.0 pt/yr arithmetic, t = 0.37, CAGR 11.6% vs 11.8%, drawdown 11 pts worse. The market already prices realistic expectations. |
| H3 adds to momentum + near-high | **PASS by the literal rule, not proven** | Holdout +9.3 pts/yr (19.2% vs 9.9%), fewer halvings (4.0% vs 4.9%). But the design window goes the other way (−2.9 pts/yr), paired t = 1.25, and 7% of random same-size subsets did as well. |

The pre-registered conclusion "signal useful" requires all three: **not met**. The gap engine is a
validated *measurement* of what a price requires, not a validated source of excess return. The H3
combination is the only candidate for a return edge and it now goes to forward testing (ledger), not
into position sizing.

## Data and coverage (be explicit about what this cannot show)

- Fundamentals: SEC companyfacts bulk, 20,472 filers → 9,991 with ≥ 8 revenue quarters. First-filed
  value per period, usable from filed + 2 days, Q4 = FY − 9M.
- Prices: Yahoo monthly, **currently listed tickers only**: 3,437 companies have prices. 4,119 of the
  9,991 have a current ticker. Companies that were delisted, acquired or went bankrupt are missing.
  This is severe survivorship bias: failures that disappeared are under-represented, so absolute
  returns are overstated and the measured "% halved" is a lower bound. r36 is almost never missing
  (0.002%), which is itself the symptom.
- Eligible (TTM revenue ≥ $50M, market cap ≥ $300M): 237,551 company-months, 2,368 companies;
  gap model (non-financial, EV > 0, margin known): 187,432 observations (98.7% solved, 0.8% need > 150%/yr,
  0.5% below −50%/yr).
- Known residual data issues: companies that do not tag OperatingIncomeLoss (NKE, HOOD, SOFI, LMND,
  CVNA) have no margin and are outside the gap model; foreign 40-F/20-F filers (SHOP) and
  class-dimensioned-only issuers with no fallback (V) are absent; at February month-ends the 150-day
  freshness rule drops many companies until 10-Ks arrive; a few post-bankruptcy tickers attach new-equity
  prices to old fundamentals (e.g. CHRD +10,663% in the stage tails). Medians are robust to these.
  The share fallback (weighted basic shares) counts only Class A for Up-C structures (e.g. ASTS), which
  understates market value; such cases need `capital_bridge`. Foreign filers (NBIS) get no card.

## H1 — calibration (holdout 2018–2025, run 3)

| P(beat) quintile | Predicted | Realized beat rate | n |
|---|---|---|---|
| 1 (price needs rare growth) | 0.2% | 1.2% | 13,592 |
| 2 | 2.9% | 4.2% | 13,469 |
| 3 | 13.9% | 15.3% | 13,448 |
| 4 | 55.6% | 55.0% | 13,468 |
| 5 (price needs ordinary growth) | 93.4% | 89.6% | 13,514 |

Monotonic, top − bottom = 88 points (threshold 15). Design window 2013–2017: 2.5% → 91.9%, also monotonic.

## H2 — returns (run 3)

| Window | Top quintile CAGR | Universe CAGR | SPY | Excess (arith., /yr) | t | Max DD top / universe |
|---|---|---|---|---|---|---|
| Design 2013–17 | 12.3% (44 m) | 16.5% | 15.8% | −0.8 pts | −0.32 | −18% / −14% |
| Holdout 2018–25 | 11.6% | 11.8% | 13.7% | +1.0 pts | 0.37 | −43% / −31% |

Fails every H2 criterion. Note the equal-weight universe of listed survivors lagged SPY in the holdout.

## H3 — interaction with earnings momentum + near 52-week high (run 3)

| Window | Momentum sleeve | + top-two P(beat) quintiles | Halved within 12m (a / b) | names a / b |
|---|---|---|---|---|
| Design | 18.7% | 15.8% | 0.9% / 1.3% | 1,321 / 159 |
| Holdout | 9.9% | 19.2% | 4.9% / 4.0% | 2,406 / 348 |

Exploratory robustness (`gap_h3_robust.py`, NOT pre-registered, cannot change the verdict):
holdout paired monthly difference +8.1 pts/yr, t = 1.25; against 300 random subsets of the scored
momentum names with the same count per month, median 11.0%, 95th percentile 21.0%, share ≥ filtered = 7%.
Design: filtered below 85% of random subsets. Reading: consistent with a real but noisy effect in
2018–2025 and with luck; the two windows disagree. Requires forward evidence.

## All runs (nothing hidden)

| Run | Change | H1 holdout q1→q5 | H2 holdout excess / t / DD gap | H3 holdout added / halved a→b |
|---|---|---|---|---|
| Dry run | 964 priced companies (debug only) | 3.1% → 90.7% | −2.3 / −0.43 / −12 | −2.2 / 4.5%→2.4% |
| 1 | full data | 1.6% → 90.0% | +0.6 / 0.22 / −11.8 | +3.6 / 4.8%→3.1% |
| 2 | 16-week quarters, share fallback, ticker label | 1.4% → 90.7% | +0.6 / 0.21 / −12.0 | +7.7 / 5.1%→4.1% |
| 3 | earliest filed across concepts and direct/derived, bad prices | 1.2% → 89.6% | +1.0 / 0.37 / −11.4 | +9.3 / 4.9%→4.0% |

Verdicts were identical in runs 1–3 (H1 pass, H2 fail, H3 pass). The H3 gain grew as data errors
were fixed; that is the direction a real effect would take, but it is also why it must be re-tested
out of sample rather than trusted.

## Stage tables (descriptive, holdout starts 2018-01..2022-09, survivors only)

| Stage | n | Median 36m return | Halved within 12m | Tripled within 36m |
|---|---|---|---|---|
| Mature | 16,979 | +27.8% | 3.0% | 3.1% |
| Compounder | 6,349 | +28.8% | 3.0% | 4.9% |
| Other growth | 12,128 | +26.6% | 5.9% | 6.1% |
| Turning profitable | 3,003 | +0.1% | 10.1% | 5.4% |
| Early commercial | 478 | −11.0% | 16.9% | 5.6% |
| **Priced for perfection** (implied g ≥ 30%, P(beat) < 15%) | 21,413 | **+2.1%** | **11.0%** | 5.3% |

Design window: priced-for-perfection median +32%, halved 3.4% — the penalty for heroic prices is
concentrated in the 2021–2022 reversal. Both tails are named in `gap_backtest_results.json`
(e.g. priced-for-perfection worst VELO, AMC; best APP, PLUG).

## Case suite (the user's four groups + steady compounders; point in time, first eligible month)

Median by group: implied growth / P(beat) / realized 3y revenue CAGR / 36m return

| Group | Implied g | P(beat) | Realized growth | 36m return |
|---|---|---|---|---|
| Early growth that worked (CRWD, DDOG, ZS, TTD, ENPH, AXON, MDB, NET, TSLA; APP too recent) | 123% | 0.00 | 44% | +53% |
| Turned profitable (36m outcome known for ABNB, CELH, SHAK only) | 99% | 0.00 | 28% | +60% |
| Promising then failed (PTON, BYND, FSLY, TDOC, OPEN, DOCU, W, CHGG) | 140% | 0.00 | 11% | −86% |
| Quality priced too high (ZM, SNOW, NVDA, NKE, CRM, PYPL, ADBE, NFLX, MRNA) | 50% | 0.00 | 14% | −49% |
| Steady compounders (COST, MSFT, AAPL, ODFL, CPRT, TJX, INTU) | 6% | 0.58 | 11% | +141% |

The most important finding: **the uniform screening model gives winners and failures among
high-growth companies the same P(beat) ≈ 0.** Both "required" more than 100%/yr. Winners grew 44%
and still rose (median +53%, ENPH/TTD/TSLA 8–14×); failures grew 11% and collapsed. What separated them is realized growth relative
to *each other*, not relative to the uniform implied growth. Cause: the uniform model (sales-to-capital
1.5, mature margin capped at 35%, 9% WACC for all) overstates the growth a capital-light software or
platform business needs. It is a fair universe-level yardstick and a poor single-company verdict for
young growth companies. Company-specific drivers (`expectation_gap` + `capital_bridge`) are required
there, and their accuracy has not yet been measured.

## Exploratory (not pre-registered)

Expected growth (rolling bucket median) minus implied growth, by quintile, mean 12-month return:
holdout bottom-gap quintile 15.6%, top 16.2%, middle 11.8–13.2% — no monotonic return relation
(top − bottom +0.6 pts, positive in 47% of months; design −3.4 pts). The bottom quintile (price
needs far more than typical) halved within 12 months **12.2%** of the time vs 3.0–6.3% for the others.
This replicates the 13.10 brain finding: the gap is a crash-risk measure, not a return predictor.

## What this changes in the engine

1. P(beat) and the implied growth are shown on every card as "what the price requires and how often
   that happens" — a measurement with proven calibration — never as a buy signal.
2. Low P(beat) / priced-for-perfection is a risk flag (≈ 3–4× halving rate in the holdout), weighed
   against the stage; for young growth companies the uniform number must be replaced by company drivers.
3. H3 (momentum + near high + achievable price) gets a forward test with rules fixed now in
   `docs/prereg_h3_forward.md`. The monthly live run that writes selections to the forecast ledger is
   NOT built yet. No allocation weight until it passes out of sample.
4. Next pre-registration candidates (not tested): sector-specific sales-to-capital and margin caps;
   delisted-company prices (needs a source with delisted history, e.g. CRSP-like data the user may have).
