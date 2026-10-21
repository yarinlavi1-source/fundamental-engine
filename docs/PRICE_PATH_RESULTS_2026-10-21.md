# Expected price path lane: walk-forward results (21.10.2026)

Preregistration: `docs/prereg_price_path.md` (commit a8dc43b, written before the module). Method: `fundamental_engine/pricepath.py`;
runner: `scripts/backtest_pricepath.py`; this is the first and only run (commit 86c8a34), on the saved 2016-2025 walk-forward
data. Nothing in sections 2-6 of the preregistration was changed after the results were seen; no re-run. Machine-written
tables: `runs/backtest/pricepath/REPORT.md`, numbers: `runs/backtest/pricepath/summary.json`.

## Verdict (section 6.7, in its words)

**FAIL.** S2 (bias) fails at every horizon: the base path's median log error is -0.11 at 1 year, -0.31 at 3 years and
-0.50 at 5 years against the registered limit of +-0.10. S1 (accuracy) and S3 (band) fail as well. Per the registered rule,
"Fail (S2 fails) -> recorded in docs; not shown in the table": the lane does NOT appear in the valuation table, not even as
a draft row. `pricepath.price_path` still runs for research (its band statement now carries the measured error and the
verdict), and the dated guidance / consensus variant of section 5 remains untested.

## What was tested

One formation a year (1 July 2016 ... 1 July 2025, as_of 30 June), 490 of today's largest US operating companies with saved SEC
companyfacts (survivors), the first three pipeline stages only (facts usable when filed by as_of), the traded June close, the
GS10 of the as_of month and Damodaran's start-of-year implied ERP. The lane's base path (trailing run rate faded with the Base
Rate Book medians for the company's revenue size, a structural or mid-cycle margin, a justified trailing P/E from the
scenario's own growth / ROIC payout / cost of equity, and today's premium over that multiple halving at the stage growth
half-life) is the point forecast; bear and bull are the band. No guidance, consensus or analyst input (none saved point in
time). Realized price = split-adjusted close at the h-th anniversary month, no dividends.

## Sample (section 6.3)

4,900 (symbol, formation) pairs; 701 skipped (no price or nothing filed by as_of); 4,199 rows: **3,287 with a lane path, 912
excluded, 0 errors**. Exclusions: TTM net income not positive 554, no point-in-time share count 233, TTM net income missing
143, normalized (mid-cycle) net margin not positive 77, no positive TTM revenue 3. (The DCF lane keeps the NI <= 0 names; an
earnings multiple cannot.)

| horizon | formations | rows in range | no lane path | no engine base_irr | no 3-year EPS CAGR | common sample | lane-only sample |
|---|---|---|---|---|---|---|---|
| 1y | 2016-2025 | 4,199 | 912 | 305 | 466 | 2,600 | 3,287 |
| 3y | 2016-2023 | 3,257 | 743 | 218 | 333 | 2,023 | 2,514 |
| 5y | 2016-2021 | 2,349 | 558 | 155 | 245 | 1,435 | 1,791 |

No row lacked a realized price. Engine base_irr: the fresh 2026-10-21 walk-forward of the current engine (3,354 rows).

## Accuracy and bias (section 6.5, common sample; e = log(pred / actual))

| h | predictor | n | median e (bias) | mean e | median abs e | within +-25% | share above actual |
|---|---|---|---|---|---|---|---|
| 1y | **lane_base** | 2,600 | **-0.113** | -0.125 | **0.213** | **53%** | 34% |
| 1y | random_walk (P_0) | 2,600 | -0.120 | -0.119 | 0.195 | 56% | 32% |
| 1y | drift_7 (P_0 x 1.07^h) | 2,600 | -0.053 | -0.051 | 0.173 | 61% | 42% |
| 1y | analyst_const (P_0 x (1+g_eps)^h) | 2,600 | -0.030 | -0.019 | 0.190 | 57% | 46% |
| 1y | engine_irr (P_0 x (1+irr-dy)^h) | 2,600 | -0.065 | -0.065 | 0.175 | 60% | 40% |
| 1y | lane_bear / lane_bull | 2,600 | -0.524 / +0.199 | | 0.532 / 0.269 | 16% / 43% | 7% / 74% |
| 3y | **lane_base** | 2,023 | **-0.311** | -0.336 | **0.399** | **29%** | 24% |
| 3y | random_walk | 2,023 | -0.311 | -0.325 | 0.362 | 31% | 20% |
| 3y | drift_7 | 2,023 | -0.108 | -0.122 | 0.278 | 43% | 39% |
| 3y | analyst_const | 2,023 | -0.013 | -0.025 | 0.374 | 32% | 49% |
| 3y | engine_irr | 2,023 | -0.163 | -0.165 | 0.286 | 41% | 33% |
| 3y | lane_bear / lane_bull | 2,023 | -0.885 / +0.123 | | 0.900 / 0.326 | 7% / 37% | 7% / 60% |
| 5y | **lane_base** | 1,435 | **-0.504** | -0.510 | **0.588** | **18%** | 21% |
| 5y | random_walk | 1,435 | -0.501 | -0.522 | 0.557 | 20% | 16% |
| 5y | drift_7 | 1,435 | -0.162 | -0.184 | 0.363 | 32% | 38% |
| 5y | analyst_const | 1,435 | -0.023 | -0.042 | 0.577 | 21% | 49% |
| 5y | engine_irr | 1,435 | -0.270 | -0.273 | 0.408 | 29% | 30% |
| 5y | lane_bear / lane_bull | 1,435 | -1.124 / -0.028 | | 1.143 / 0.381 | 7% / 31% | 8% / 48% |

engine_irr with the dividend yield left in (P_0 x (1+irr)^h): bias -0.049 / -0.114 / -0.202, MdALE 0.173 / 0.278 / 0.386.
Lane-only sample (3,287 / 2,514 / 1,791 rows, no engine or EPS requirement): lane_base bias -0.115 / -0.330 / -0.528, MdALE
0.220 / 0.418 / 0.620, within25 51% / 27% / 17%; random_walk MdALE 0.195 / 0.364 / 0.571; drift_7 0.174 / 0.282 / 0.373. Same picture.

Head to head (share of common-sample rows where the lane's |e| is the smaller): vs random_walk 46% / 44% / 47%; vs drift_7
39% / 33% / 30%; vs analyst_const 45% / 47% / 49%; vs engine_irr 39% / 34% / 31% (1y / 3y / 5y). The lane never beats a
baseline on more than half the rows.

## Band (S3)

Realized price within [bear, bull]: 67% / 54% / 40%; below the bear 7% / 7% / 8%; above the bull 26% / 40% / 52% (1y / 3y / 5y).
S3 needs >= 50% inside and both tails <= 35% at 3 and 5 years: fails at both (the above-bull tail). The bull path is the one
with ~zero median bias at 5 years (-0.03): the median surviving large cap did the lane's bull case in 2016-2025. The bear
path's median error is -0.52 / -0.89 / -1.12: a bear that cuts growth and margin and fades the premium fastest sits far below
anything the median survivor did.

## Secondary rank statistic (section 6.5; not part of the verdict)

Yearly Spearman of pred / P_0 against the realized ratio, mean (t) over formation years: lane_base 0.058 (1.5) / **0.135
(5.0)** / **0.148 (5.4)**; engine_irr 0.085 (3.0) / 0.115 (2.9) / 0.144 (3.7); analyst_const 0.048 (1.4) / 0.075 (2.3) / 0.077
(1.8); random_walk and drift_7 ~0 by construction. The lane's ratio to today's price (which is mostly how far today's multiple
sits from the justified one, i.e. a value signal) ranks the cross-section of 3- and 5-year outcomes, with the highest mean rho
of the predictors tested. That is an ordering, not a level: it was registered as secondary and does not change the verdict.
The 3- and 5-year windows of consecutive formation years overlap, so the t-statistics overstate independence.

## Survivorship-deflated check (section 6.8; robustness, not the verdict)

Per formation window the realized ratios are rescaled so the sample median equals SPY's price-only ratio. The sample's median
ratio was BELOW SPY's in most windows (SPY was carried by the largest few names, the median large cap lagged it), so deflation
makes every bias a little more negative: lane_base -0.122 / -0.362 / -0.582, MdALE 0.221 / 0.428 / 0.647, within25 50% / 26% /
15%; random_walk -0.130 / -0.347 / -0.587; drift_7 -0.062 / -0.144 / -0.249; analyst_const -0.039 / -0.059 / -0.094;
engine_irr -0.075 / -0.204 / -0.348. Band inside 66% / 50% / 35%. Survivorship of the universe does not explain the failure.

## Breakdowns (lane_base median bias, 1y / 3y / 5y; n at 1y)

By today's multiple on normalized trailing earnings (the registered M_0 buckets):

| M_0 | n (1y) | 1y | 3y | 5y | reading |
|---|---|---|---|---|---|
| < 10x | 113 | +0.13 | +0.31 | +0.60 | the lane closes the discount to the justified multiple; the market was right that earnings would fall (LYB, BIIB, CCL) |
| 10-15x | 243 | -0.05 | -0.01 | -0.20 | nearly unbiased to 3 years |
| 15-20x | 395 | -0.10 | -0.20 | -0.33 | |
| 20-30x | 840 | -0.11 | -0.29 | -0.44 | the median row: the premium faded, the market kept paying it |
| 30-60x | 772 | -0.13 | -0.44 | -0.69 | |
| > 60x (capped) | 237 | -0.46 | -1.01 | -1.41 | 11% of rows: near-zero margins (FTNT 2016 at 0.3%, NFLX 2016 at 1.8%) or cyclical mid-cycle earnings far below TTM; an earnings multiple has no footing |

By stage (3y): mature -0.28 (n 1,067), maturing -0.32 (624), growth -0.41 (262), decline -0.38 (69), early -2.13 (1 row).
By cyclical flag (3y): structural -0.29 (MdALE 0.37 vs random walk 0.35), cyclical -0.35 (0.43 vs 0.38); the mid-cycle
normalization did not help the cyclicals' level. By size class (3y): >$25bn and >$50bn -0.14 / -0.15; $12-25bn -0.24;
$1.25-12bn -0.34 to -0.40; below $1.25bn -0.8 (small n). The larger the company, the smaller the miss: the base-rate fade is
least wrong where the base rates are flattest.
By formation year the lane's bias tracks the random walk's within a few points in every year (3y: 2016 -0.48 vs -0.45; 2018
-0.44 vs -0.46; 2020 -0.33 vs -0.34; 2022 -0.17 vs -0.36; 2023 -0.35 vs -0.31): the lane's five-year price is, on median,
today's price.

## Diagnostics (section 6.6)

- Median lambda (base) = 1.0 in every size class; the base-rate ceiling bound in 6.8% of base paths and 6.4% of bull paths
  (most in the $325m-1.25bn and >$50bn classes, 10-13%). The median base path sits at the 44-55th percentile of its class'
  5-year distribution: the fade puts the typical company at its class median, as designed.
- Premium over the justified multiple, D_0 / M_0: p10 -0.41, p25 +0.04, median **+0.32**, p75 +0.51, p90 +0.63. Median M_0
  26.2x, median justified 16.8x, median D_0 8.9x. Three quarters of the rows traded above what the method justifies.
- Justified multiple today / Damodaran January-2026 cross-section PE (13.12 + 10.52 + 36.47 gEPS + 8.46 payout; not point in
  time): p25 0.44, median **0.54**, p75 0.67. The lane's justified multiple is about half of what the cross-section pays for
  the same growth and payout; the regression's intercept (23.6x at zero growth and zero payout, beta 1) already sits above
  the lane's median justified level.
- Flag shares: cyclical_normalized 49% (the industry-cyclical flag fires for half of the universe), multiple_capped 11%,
  roic_missing 11% (R = ke), share_pace_clipped 9%, margin_floored_bear 19% (the bear's margin hit the 1% floor), lambda
  shrunk 7%, ni_ttm_from_facts 1.6%, run_rate_clipped 1.3%, margin_floored_base 1.9%.

## Why it fails: the decomposition (median base path over the 3,287 rows)

| year | growth | margin | EPS_h / EPS_0 | justified multiple | premium left | multiple | M_h / M_0 | P_h / P_0 |
|---|---|---|---|---|---|---|---|---|
| 1 | +6.3% | 10.8% | 1.12 | 15.6x | 7.0x | 23.3x | 0.89 | 1.01 |
| 2 | +6.0% | 10.6% | 1.19 | 15.6x | 5.5x | 21.6x | 0.83 | 0.99 |
| 3 | +5.7% | 10.1% | 1.22 | 16.2x | 4.3x | 21.0x | 0.79 | 0.97 |
| 4 | +5.6% | 10.1% | 1.30 | 16.1x | 3.4x | 20.0x | 0.75 | 0.98 |
| 5 | +5.5% | 10.1% | 1.38 | 16.0x | 2.7x | 19.3x | 0.72 | 1.00 |

EPS up 38% in five years (6.6% a year: the run rate of 6.3% fading toward the class medians, with a net-buyback share path), the
multiple down 28% (today's 26x converging on a 16-17x justified level): the price goes nowhere. The median realized 5-year
ratio was about 1.65 (random-walk bias -0.50). The bull path (growth +10% fading to +7%, margin +2.8 points, premium fading
slowest; EPS x2.06, multiple 20.8x, P_5 / P_0 1.58) is what the median survivor did.

What the result says about the design, beyond "2016-2025 was a strong decade":
1. The justified multiple is a value, not a price. ke = rf + Damodaran's implied ERP (+ a stage add-on; median 8.4%), growth
   faded to 3-5% nominal within a few years, and a payout from the sustainable-growth identity with R clipped to [ke, 50%]
   (median R 13.7%, payout 67%) give 1/ke-like multiples of 16-17x. The market paid 26x for the median large cap throughout
   the decade and did not converge. The premium half-life softened the pull (the preregistration's lesson from repA), but a
   fade is still a fade: at 5 years the lane carries only 30% of the premium. The same mechanism makes the DCF lane's value
   sit at ~0.74 of the price (BASELINE_NOTE, 2026-10-21): this lane inherits it through the justified multiple.
2. Convergence is wrong in both directions. Above 60x the market was pricing earnings the trailing run rate could not see
   (NVDA, AMD, NFLX, FTNT); below 10x it was pricing an earnings fall the run rate could not see (LYB, BIIB, CCL, PCG). Today's
   multiple carries information about future earnings that a trailing run rate plus base rates does not have; fading it
   toward a fundamentals-only level discards that information. The <10x and >60x buckets are the two worst.
3. The bear "changes the business" as required (growth - spread, margin - after-tax spread over the stage's conv years,
   premium fading fastest), and the result is a path the median survivor never approached (7-8% of rows below it). As a
   downside scenario that is defensible; as the lower edge of a forecast band it is not calibrated.
4. The cross-sectional ordering survives (rank statistic above): how far today's multiple sits from the justified one ranks
   outcomes. That is the engine's existing value signal in another form, not a price forecast.

## Named examples from both tails (common sample)

Lane far below the realized price (3 years): FTNT 2016, P_0 31.6 -> 76.8; lane 1.6 (net margin 0.3%, 1,809x normalized
earnings capped at 60x, margin floored at 1%, multiple 13.5x by year 3). NFLX 2016, 91.5 -> 367.3; lane 13.6 (margin 1.8%,
310x capped). AMD 2023, 105.7 -> 470.7; lane 18.1 (cyclical mid-cycle margin 2.9%, 251x capped). STRL 2023, 55.8 -> 839.4
(x15); lane 42.6. At 5 years: NVDA 2019, 164.2 -> 4,941.6 (x30); lane 102.0 (bull 202.9): the run rate was +23%, the
mid-cycle margin 13.3%, the multiple 70x faded to 26x. NVDA 2020, 379.9 -> 6,319.6; lane 130.8. FTNT 2017 and 2018 (x7.6, x6.1)
lane 10.1 and 15.5.
Lane far above the realized price (3 years): CCL 2019, 46.5 -> 8.6 (lane 76.4: 8x on mid-cycle earnings "converging" to 14x
into the pandemic); PCG 2017, 70.4 -> 9.3 (lane 63.4; wildfire bankruptcy); LYB 2022, 87.5 -> 57.9 (lane 315.4: 5.2x on
peak-cycle earnings, the mid-cycle median margin still 10.9%, justified 26x); PYPL 2021, 291.5 -> 58.0 (lane 293.8: a 26% run
rate at 66x, the premium faded but the earnings did not come). At 5 years: BIIB 2020, 267.5 -> 125.6 (lane 1,083.6: 7.4x
"converging" to 22x while the franchise eroded); KDP 2018 (a reverse-merger share count), BBY 2021 (a pandemic run rate of
+22% at 12.9x: the lane extrapolated the boom and lifted the multiple).
Closest at 3 years (|e| < 0.005): JBL 2017, ABBV 2018, REGN 2020, CPAY 2018; at 5 years PNW 2017, TRMB 2021, GD 2018, AMGN 2021:
mid-multiple (18-26x), mid-growth (0-18%) names where the fade and the market agreed.

## Data-layer defects surfaced by the lane (reported, left in the sample)

20 rows have M_0 < 1x, which no earnings multiple on a listed large cap can be; they are unit or class errors in the shared
point-in-time layer that the engine's own rows read as well: BRK-B (every year; the cover-page count 941,481 is the Class A
count, not Class B equivalents), FOXA 2020-2025 (share count 1), CMG 2022 (27,962: thousands), TEVA 2016 (1,022,255),
MKSI 2018 (TTM revenue 555.8bn: a scaling error in one companyfacts value; the other MKSI years read 0.8-3.7bn), MPLX 2019
(0.63x). They are 0.6% of rows; the medians above are unaffected, the means are not. The preregistration forbids a sample
rule after the results, so they stay; they belong to dataqa / sec_frames as a fix (a cover count below 5m for a > $10bn
market cap, a revenue step of 100x year on year), outside this lane.

## Caveats (the engine walk-forward's, unchanged)

- Survivorship: the universe is today's 491 largest US companies, so every realized price is a survivor's; the deflated check
  (6.8) rescales each window to SPY and is reported next to the raw results, not instead of them.
- In-sample design: the stage parameters, the base-rate tables, the P/E floor and cap and the method were written in 2026 with
  2016-2025 known; the preregistration fixed them before this run, but they are not out of sample.
- No guidance or consensus: the run-rate + base-rate variant only; the guided / consensus variant of section 5 is untested
  and must not be shown as measured.
- One formation a year: the yearly rank statistics come from 6-10 overlapping windows.
- The engine_irr baseline comes from the fresh engine walk-forward rows of 2026-10-21 (bt_pre), the current engine at commit
  ff36ede, and shares the data-layer defects above.

## What follows (not done here; no constant may change in this spec)

The lane is recorded as failed and is not wired into the valuation table. `pricepath.MEASURED` and `MEASURED_VERDICT` carry
the common-sample error into every live `price_path` output's band statement, so a research use of the lane shows the
measured bias. Any second version is a new preregistration (a new spec_version with its own sections 2-6), not an edit of
this one. The two directions of the failure suggest what such a version would have to test: today's multiple kept as the
level (the market's information) with the fundamentals used only to rank or to flag, and an earnings path that uses the
market's implied growth rather than the trailing run rate; both are hypotheses, not findings.

## Hebrew note for the owner (written after the verdict, per section 8)

מסלול "מחיר צפוי לחמש שנים" נבדק על 2016–2025 ונכשל במבחן שנרשם מראש: המחיר הבסיסי שלו נמוך מהמחיר שהתממש בחציון ב-11% אחרי
שנה, ב-27% אחרי שלוש שנים וב-40% אחרי חמש (שגיאת לוג -0.11 / -0.31 / -0.50), ודיוקו אינו טוב משל "המחיר של היום" או "המחיר
של היום + 7% לשנה". הסיבה: הרווח למניה עולה במסלול כ-38% בחמש שנים אך המכפיל יורד כ-28% (מ-26 ל-19) לעבר מכפיל "מוצדק" של
כ-17, והשוק לא ירד לשם. לכן המסלול לא מוצג בטבלת השווי, גם לא כטיוטה; הערכות השווי ממשיכות עם ה-DCF ומבחני פער הציפיות.

## Reproduction

```
python scripts/backtest_pricepath.py /home/user/fundamental-engine/runs/universe_2026-10-21 --name large500 --from 2016 --to 2025 \
    --workers 2 --prices /home/user/fundamental-engine/runs/backtest/raw/prices \
    --rates /home/user/fundamental-engine/runs/backtest/raw/macro/fred_GS10.csv \
    --engine-rows <engine walk-forward rows.jsonl> --out <dir>        # ~8 minutes; rows.jsonl ~28MB, resumable
python scripts/backtest_pricepath.py ... --evaluate-only              # rebuilds summary.json and REPORT.md
```
