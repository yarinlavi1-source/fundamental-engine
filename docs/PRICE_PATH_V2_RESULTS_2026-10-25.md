# Expected price path, version 2: walk-forward results (25.10.2026)

Preregistration: `docs/prereg_price_path_v2.md` (registered 25.10.2026 at cc2f3bd, before any v2 code; its text is unchanged
since except the title's status marker; section 10 holds the dated pre-run clarifications and this run's record). Method: `fundamental_engine/pricepath.py`
spec_version 2; runner: `scripts/backtest_pricepath.py --spec 2`. This is the single registered run (section 6.1), made at commit
5b813ae of branch price-path, on the saved 2016-2025 walk-forward data. No bug was found, so nothing was changed or re-run, and
nothing in sections 2-7 changed after the results were seen. Machine-written tables: `runs/backtest/pricepath_v2/REPORT.md`;
numbers: `runs/backtest/pricepath_v2/summary.json`. Version 1 for comparison: `docs/PRICE_PATH_RESULTS_2026-10-21.md` and the
audit `docs/PRICE_PATH_2026-10.md`.

## Verdict (section 7, in its words)

**FAIL.** "FAIL = S2 or S4 fails -> recorded in docs; not shown." S2 (bias) fails at 3 and 5 years, and S1 (accuracy) fails
as well. S3 (band) and S4 (attribution) pass. The lane does NOT appear in the valuation table, not even as a draft row
(section 9: "FAIL: no rows"). `pricepath.price_path(..., spec_version=2)` still runs for research; its band statement now
carries the measured error and this verdict (`pricepath.MEASURED_V2`, `MEASURED_VERDICT_V2`, `TABLE_STATUS_V2 = 'not_shown'`).

| criterion | registered test | result | numbers |
|---|---|---|---|
| S1 accuracy | MdALE at 3 and 5 years below analyst_const, engine_irr AND lane_v1; at 1, 3, 5 years not worse than min(random_walk, drift_7) by more than 0.03 | **FAIL** | 3y: lane 0.337 vs analyst_const 0.374, engine_irr **0.286**, lane_v1 0.399. 5y: 0.455 vs 0.577, **0.408**, 0.588. Against drift_7: 0.206 vs 0.173, 0.337 vs 0.278, 0.455 vs 0.363, all more than 0.03 worse |
| S2 bias | median log error within +-0.10 at 1, 3, 5 years | **FAIL** | -0.045 (pass), **-0.119**, **-0.215** |
| S3 band | at 3 and 5 years >= 50% of realized prices inside [bear, bull], neither tail above 30% | pass | inside 78.5% / 77.2%; below bear 5.8% / 4.7%; above bull 15.6% / 18.1% |
| S4 attribution | at 3 years median e_rev, e_margin, e_multiple each within +-0.10 (n 1,639) | pass | e_rev -0.021, e_margin -0.094, e_multiple -0.034 (e_margin passes by 0.006) |

S3 has no limit on the band's width, and the pass comes from a much wider band than v1's (section "Band" below). The expectation
written into section 7 before the run was that v2 "may pass S2 at 1-3 years and fail it at 5 years" because of the structural
margin term; it failed at 3 years too (by 0.019).

## How the run was made (section 6.1)

- lane_v1 baseline: the unchanged spec-1 code re-run at 5b813ae (`--spec 1 --workers 2`). All 4,900 rows are byte-identical to the
  21.10.2026 run, including every input and output hash of the 3,287 ok rows; its metrics reproduce v1's recorded numbers.
- v2: `--spec 2 --v1-rows <v1 rows> --workers 2` (two passes per formation: every company's anchors, then the cross-section table
  and the paths), started 25.10.2026 07:57:55 on a clean worktree at 5b813ae; then `--evaluate-only`, which reproduced every output
  file with identical sha256 (summary.json 8b624ea8..., REPORT.md f26579d8..., rows.jsonl 360a0d0f..., tables.json 8726c662...,
  anchors.jsonl 377c26d1...).
- Engine base_irr: the 2026-10-21 engine walk-forward rows (3,354 rows with base_irr, engine at ff36ede), as v1.
- Pipeline: the price-path branch's own. Two later commits on the main working branch (2355e4a, b2012e4, both change how cyclical
  companies are flagged) are not in it, so v1 and v2 ran on the same pipeline as the 21.10.2026 run.
- Independent arithmetic check outside the module, on all 3,364 ok rows: margin targets and path, cell lookup and fallback, L_0
  and its clip, multiples with their order and clip, price = EPS x multiple, the growth fade on 9,554 scenario paths. The only
  differences are 6-decimal rounding on near-zero values in data-defect rows (BRK-B, FOXA, BKR, GRMN 2016).
- A read-only audit of 5b813ae against sections 2-7 found no blocking deviation (minor points: section 10 post-run notes).

## Sample (section 6.3)

4,900 (symbol, formation) pairs; 701 skipped (no price or nothing filed by as_of); 4,199 rows: **3,364 with a v2 path, 835
excluded, 0 errors, 0 ordering assertions**. Exclusions (a row can carry two reasons): TTM net income not positive 554, no
point-in-time share count 233, TTM net income missing 143, no positive TTM revenue 3. v1's fifth exclusion (normalized net
margin not positive, 77 rows) has no counterpart in v2 (clarification 8), which is why 77 more rows have a v2 path.

| horizon | formations | rows in range | no lane path | no engine base_irr | no 3-year EPS CAGR | no lane_v1 path | common sample | lane-only sample |
|---|---|---|---|---|---|---|---|---|
| 1y | 2016-2025 | 4,199 | 835 | 350 | 527 | 77 | 2,600 | 3,364 |
| 3y | 2016-2023 | 3,257 | 687 | 252 | 376 | 56 | 2,023 | 2,570 |
| 5y | 2016-2021 | 2,349 | 519 | 181 | 274 | 39 | 1,435 | 1,830 |

(Independent counts.) The common sample has exactly v1's rows and sizes (2,600 / 2,023 / 1,435), so every baseline number
below equals v1's. No row lacked a realized price.

## Accuracy and bias (section 6.5, common sample; e = log(pred / actual))

| h | predictor | n | median e (bias) | mean e | median abs e | within +-25% | share above actual |
|---|---|---|---|---|---|---|---|
| 1y | **lane_base (v2)** | 2,600 | **-0.045** | -0.048 | **0.206** | 53% | 44% |
| 1y | random_walk (P_0) | 2,600 | -0.120 | -0.119 | 0.195 | 56% | 32% |
| 1y | drift_7 (P_0 x 1.07^h) | 2,600 | -0.053 | -0.051 | 0.173 | 61% | 42% |
| 1y | analyst_const (P_0 x (1+g_eps)^h) | 2,600 | -0.030 | -0.019 | 0.190 | 57% | 46% |
| 1y | engine_irr (P_0 x (1+irr-dy)^h) | 2,600 | -0.065 | -0.065 | 0.175 | 60% | 40% |
| 1y | lane_v1 (v1 base path) | 2,600 | -0.113 | -0.125 | 0.213 | 53% | 34% |
| 1y | lane_bear / lane_bull | 2,600 | -0.547 / +0.452 | | 0.551 / 0.473 | 14% / 22% | 6% / 91% |
| 3y | **lane_base (v2)** | 2,023 | **-0.119** | -0.134 | **0.337** | 34% | 39% |
| 3y | random_walk | 2,023 | -0.311 | -0.325 | 0.362 | 31% | 20% |
| 3y | drift_7 | 2,023 | -0.108 | -0.122 | 0.278 | 43% | 39% |
| 3y | analyst_const | 2,023 | -0.013 | -0.025 | 0.374 | 32% | 49% |
| 3y | engine_irr | 2,023 | -0.163 | -0.165 | 0.286 | 41% | 33% |
| 3y | lane_v1 | 2,023 | -0.311 | -0.336 | 0.399 | 29% | 24% |
| 3y | lane_bear / lane_bull | 2,023 | -0.911 / +0.562 | | 0.916 / 0.596 | 7% / 18% | 6% / 84% |
| 5y | **lane_base (v2)** | 1,435 | **-0.215** | -0.243 | **0.455** | 28% | 35% |
| 5y | random_walk | 1,435 | -0.501 | -0.522 | 0.557 | 20% | 16% |
| 5y | drift_7 | 1,435 | -0.162 | -0.184 | 0.363 | 32% | 38% |
| 5y | analyst_const | 1,435 | -0.023 | -0.042 | 0.577 | 21% | 49% |
| 5y | engine_irr | 1,435 | -0.270 | -0.273 | 0.408 | 29% | 30% |
| 5y | lane_v1 | 1,435 | -0.504 | -0.510 | 0.588 | 18% | 21% |
| 5y | lane_bear / lane_bull | 1,435 | -1.303 / +0.597 | | 1.305 / 0.668 | 4% / 16% | 5% / 82% |

- engine_irr with the dividend yield left in (engine_irr_total): bias -0.049 / -0.114 / -0.202, MdALE 0.173 / 0.278 / 0.386.
- 2 and 4 years (not in the verdict): lane_base bias -0.081 / -0.161, MdALE 0.265 / 0.406, within +-25% 44% / 29%.
- Lane-only sample (3,364 / 2,570 / 1,830 rows; no engine, EPS or lane_v1 requirement): lane_base bias -0.044 / -0.114 / -0.231,
  MdALE 0.222 / 0.360 / 0.480; random_walk MdALE 0.197 / 0.366 / 0.576; drift_7 0.176 / 0.284 / 0.374. Same picture.
- Head to head (share of rows where the v2 lane's |e| is smaller; 1y / 3y / 5y): vs random_walk 49% / 54% / 60%; vs drift_7
  42% / 40% / 42%; vs analyst_const 49% / 53% / 58%; vs engine_irr 43% / 43% / 46%; vs lane_v1 56% / 63% / 68%.

Reading: v2 halves v1's bias at every horizon and beats v1, the random walk and the constant analyst-EPS path on MdALE at 3 and
5 years. It does not beat the two simplest price paths a reader already has: today's price grown 7% a year, and the engine's
own expected return compounded from today's price. Those two stay closer on more than half of the rows at every horizon.

## Band (S3 and every horizon)

| | 1y | 2y | 3y | 4y | 5y |
|---|---|---|---|---|---|
| realized inside [bear, bull] | 85% | 81% | 79% | 79% | 77% |
| below the bear | 6% | 6% | 6% | 4% | 5% |
| above the bull | 9% | 13% | 16% | 17% | 18% |
| median bull / bear, v2 | 2.8x | | 4.4x | | 7.0x |
| median bull / bear, v1 | 1.9x | | 2.5x | | 2.8x |
| v1 inside / above bull | 67% / 26% | | 54% / 40% | | 40% / 52% |

(Band widths: median of ln(bull / bear) on the common sample, p25-p75 at 5 years 1.73-2.12, i.e. 5.6x-8.3x.)

The band now contains the realized price about four times in five, but it is wide and lopsided. Over all ok rows the median
bear at 5 years is 0.47 of today's price (margin 6.0%, EPS x0.65, multiple 17x), while on the common sample the realized 5-year
ratio's p10 was about 0.84: only 5% of realized prices fell below the bear. The misses are on the upside (18% above the bull at 5 years). As a downside
scenario the bear is severe by design (half of today's margin in 73% of rows, the cross-section's p25 multiple); as the lower
edge of a forecast band it is not calibrated, which S3 as registered does not test.

## Attribution (section 6.6, registered primary diagnostic)

log(P_lane / P_actual) = e_rev + e_margin + e_shares + e_multiple, exact (largest identity residual 5e-5), on the common rows with
a usable v2 row at t + h (the realized side). Median (p10 / p90):

| h, n | lane | e_total | e_rev | e_margin | e_shares | e_multiple |
|---|---|---|---|---|---|---|
| 1y, 2,201 | v2 | -0.047 (-0.42 / 0.35) | +0.005 (-0.13 / 0.13) | -0.032 (-0.51 / 0.45) | -0.002 (-0.05 / 0.03) | -0.013 (-0.58 / 0.58) |
| 1y, 2,201 | v1 | -0.113 (-0.49 / 0.26) | +0.002 (-0.14 / 0.13) | -0.059 (-0.51 / 0.42) | -0.002 (-0.05 / 0.03) | -0.068 (-0.63 / 0.53) |
| 3y, 1,639 | v2 | -0.140 (-0.84 / 0.46) | -0.021 (-0.32 / 0.28) | -0.094 (-0.68 / 0.54) | -0.008 (-0.16 / 0.11) | -0.034 (-0.67 / 0.58) |
| 3y, 1,639 | v1 | -0.323 (-0.93 / 0.26) | -0.018 (-0.33 / 0.29) | -0.172 (-0.76 / 0.43) | -0.008 (-0.16 / 0.11) | -0.104 (-0.73 / 0.49) |
| 5y, 1,108 | v2 | -0.260 (-1.14 / 0.54) | -0.066 (-0.43 / 0.26) | -0.165 (-0.88 / 0.47) | -0.007 (-0.24 / 0.20) | -0.041 (-0.70 / 0.60) |
| 5y, 1,108 | v1 | -0.547 (-1.35 / 0.23) | -0.076 (-0.46 / 0.25) | -0.241 (-0.90 / 0.42) | -0.007 (-0.24 / 0.20) | -0.220 (-0.90 / 0.43) |

The v1 lines reproduce section 1 of the preregistration exactly. What each v2 change did:
- Multiple target = the market's own class multiple: the multiple term fell from -0.10 / -0.22 to -0.03 / -0.04 (3 / 5 years).
  On the median row the base multiple stays at about 24x for five years (today's 24.0x, year 5 23.7x), where v1 faded 26x to 19x.
- Base margin = TTM for cyclicals too: the cyclicals' margin term fell from -0.33 / -0.41 to -0.10 / -0.20.
- Run-rate blend: the revenue term barely moved (-0.02 / -0.07 against -0.02 / -0.08).
- What is left is the structural margin term, unchanged because v1 already held it: -0.08 / -0.13 for non-cyclicals in both
  versions. This is the term section 7 named in advance ("about -0.08 / -0.13 ... which no point-in-time rule removes"); the
  survivors' margins rose over the decade and a held TTM margin does not see it.

| margin term, 3y / 5y | v2 | v1 |
|---|---|---|
| non-cyclical (n 901 / 628) | -0.08 / -0.13 | -0.08 / -0.13 |
| cyclical (n 738 / 480) | -0.10 / -0.20 | -0.33 / -0.41 |

The median base path (3,358 ok rows; median of each ratio):

| year | growth | margin | EPS_h / EPS_0 | multiple | M_h / M_0 | P_h / P_0 |
|---|---|---|---|---|---|---|
| 1 | +6.7% | 11.1% | 1.08 | 24.0x | 1.00 | 1.07 |
| 3 | +5.9% | 11.1% | 1.23 | 23.7x | 1.00 | 1.20 |
| 5 | +5.6% | 11.1% | 1.39 | 23.7x | 0.99 | 1.34 |

On the 5-year common sample the base path's median log ratio to today's price is +0.29 against a realized +0.50 (x1.33 against
x1.65): the multiple now holds, and the shortfall is earnings growth (mostly margin, some revenue at 5 years).

## Breakdowns (lane_base median bias / MdALE; common sample)

By formation year, with the MdALE of drift_7 and engine_irr in the same window:

| formation | 3y bias | 3y MdALE | drift_7 | engine_irr | 5y bias | 5y MdALE | drift_7 | engine_irr |
|---|---|---|---|---|---|---|---|---|
| 2016 | -0.23 | 0.42 | 0.31 | 0.36 | -0.50 | 0.59 | 0.52 | 0.59 |
| 2017 | -0.13 | 0.34 | 0.29 | 0.32 | -0.21 | 0.39 | 0.31 | 0.35 |
| 2018 | -0.24 | 0.37 | 0.30 | 0.32 | -0.17 | 0.41 | 0.33 | 0.33 |
| 2019 | -0.07 | 0.27 | 0.20 | 0.21 | -0.20 | 0.41 | 0.33 | 0.39 |
| 2020 | -0.17 | 0.31 | 0.24 | 0.26 | -0.23 | 0.46 | 0.41 | 0.45 |
| 2021 | -0.01 | 0.31 | 0.23 | 0.23 | -0.12 | 0.47 | 0.40 | 0.41 |
| 2022 | -0.07 | 0.39 | 0.32 | 0.33 | | | | |
| 2023 | -0.09 | 0.38 | 0.30 | 0.30 | | | | |

At 3 and 5 years the bias is negative in every window (at 1 year two of ten windows are positive, 2019 and 2021), and the lane's
MdALE is above drift_7's in every formation year at 1, 3 and 5 years.

| group | 3y bias / MdALE (n) | 5y bias / MdALE (n) |
|---|---|---|
| stage mature | -0.13 / 0.31 (1,067) | -0.19 / 0.42 (804) |
| stage maturing | -0.11 / 0.34 (624) | -0.25 / 0.49 (389) |
| stage growth | -0.10 / 0.50 (262) | -0.30 / 0.65 (177) |
| stage decline | -0.19 / 0.43 (69) | -0.26 / 0.51 (64) |
| not cyclical | -0.12 / 0.33 (1,081) | -0.18 / 0.47 (800) |
| cyclical | -0.12 / 0.34 (942) | -0.26 / 0.45 (635) |

By revenue size class (Base Rate Book classes), bias at 3y / 5y: >$50bn +0.05 / -0.01; >$25bn +0.06 / -0.02; $12-25bn -0.05 /
-0.15; $7-12bn -0.13 / -0.22; $4.5-7bn -0.17 / -0.25; $3-4.5bn -0.15 / -0.26; $2-3bn -0.25 / -0.38; $1.25-2bn -0.30 / -0.33;
$700m-1.25bn -0.46 / -0.64 (n 59 / 43); $325-700m -0.87 / -1.13 (n 34 / 29). The largest companies are close to unbiased; the
miss grows as the company gets smaller, as in v1.

By today's raw trailing P/E (the registered M_0 buckets), bias at 3y / 5y: < 10x +0.59 / +0.77 (n 148 / 82); 10-15x +0.19 /
+0.11; 15-20x -0.03 / -0.05; 20-30x -0.12 / -0.23; 30-60x -0.33 / -0.49; > 60x -0.94 / -1.12 (n 168 / 135). The two tails are the
section 8 hypotheses deliberately left out of v2 (a very low multiple prices an earnings fall, a very high one an earnings surge,
that trailing fundamentals do not show); they are as wrong as in v1. Among formation cells with at least 30 rows at 5 years the
thin-margin (< 5%) cells carry the most negative biases (growth < 0% -0.59, 0-5% -0.56, 10-20% -0.42).

Yearly Spearman of pred / P_0 against the realized ratio, mean (t) over formation years (secondary, not in the verdict):
lane_base 0.071 (1.9) / 0.088 (2.2) / 0.142 (3.1); lane_v1 0.058 (1.5) / 0.135 (5.0) / 0.148 (5.4); engine_irr 0.085 (3.0) /
0.115 (2.9) / 0.144 (3.7); analyst_const 0.048 (1.4) / 0.075 (2.3) / 0.077 (1.8). v2 ranks no better than v1 or the engine
(overlapping windows; the t-statistics overstate independence).

## Diagnostics (section 6.7)

- Ordering assertions: **0** (the registered requirement). Errors: none.
- L_0 (log premium over the class median): p10 -0.80, p25 -0.34, median 0.00, p75 +0.36, p90 +0.87; clipped at +-ln 3 in 13% of
  rows (`premium_clipped`).
- Spec 1's Gordon multiple / the class median: today p10 0.47, median 0.69, p90 1.03; year 5 median 0.67. The market's class level
  sits about 1.45x above spec 1's justified level on the median row (v1 found 0.54 of the Damodaran cross-section).
- Cell usage: at formation a full cell 56%, the growth row 39%, the whole cross-section 5%; along the paths 59% / 40% / 2%. Only
  4-9 of the 23-24 cells reach CELL_MIN = 20 rows in a formation year, so the growth row does much of the work.
- Margin targets: the bear is set by half of today's margin in 73% of rows (own low 16%, stage spread 11%); the bull by 1.5x
  today's margin in 44% (stage spread 41%, own high 14%).
- Flags: cyclical growth trend 49%, premium_clipped 13%, roic_missing 12%, share pace clipped 9%, base lambda shrunk 7% (bull
  6%), bear margin floored 5%, bear growth ordered 3%, bull multiple capped 3%, bull multiple ordered 3%, base margin floored 2%,
  ni_ttm_from_facts 2%, run rate clipped 1%.
- Cross-section tables: n 262-397 per formation (every company with NI_0 > 0, a share count and a price; 2-8 a year outside the
  [1, 500] P/E range). Median trailing P/E 21.8x-28.6x by year (2021 the high year); p25 14.8-20.1x, p75 30.6-42.6x.

## Robustness (section 6.8; reported, not the verdict)

- Survivorship-deflated (each window's realized ratios rescaled so the sample median equals SPY's price-only ratio): lane_base
  bias -0.057 / -0.151 / -0.304, MdALE 0.206 / 0.348 / 0.490; drift_7 MdALE 0.176 / 0.281 / 0.387; engine_irr 0.179 / 0.300 /
  0.445; band inside 84% / 78% / 75%. Deflation makes the bias more negative, as in v1: survivorship does not explain the miss.
- Data-defect rows removed (raw M_0 < 1x, clarification 12): identical to the raw result. The 21 such rows (19 of the 20 named in
  section 2: BRK-B 2016-2025, FOXA 2020-2025, CMG 2022, TEVA 2016, MPLX 2019; plus BKR 2019 and 2022, which only v2 reaches) all
  lack an engine base_irr, so none is in the common sample.
- Not caught by that filter: the 20th named row, MKSI 2018, whose TTM revenue carries the scaling defect v1 reported (555.8bn).
  v2's raw P/E uses net income, not revenue, so it reads a normal 13.8x and the row stays; its TTM margin reads 0.1%, the 1% floor
  lifts the base's earnings about tenfold, and it sits in the common sample with e = +2.79 / +3.54 at 3 / 5 years. One row does not
  move a median.

## Named examples (common sample)

Lane far below the realized price at 3 years: AMD 2023 (P_0 105.7 -> 470.7; lane 13.6; 431x trailing at a 1.7% margin), NFLX 2016
(91.5 -> 367.3; lane 15.1; 310x), PTC 2019 (1,348x at 0.6%), FTNT 2016 (1,809x at 0.3%), MU 2023 (69.9 -> 958.7; lane 68.3),
NVDA 2023 (423.0 -> 2,000.9; lane 151.7; 218x). At 5 years FTNT 2016 (31.6 -> 238.2; lane 4.2), NVDA 2019 and 2020 (x30 and x17;
lane 241.6 and 297.4). Most are > 60x or thin-margin names (AMD, NFLX, PTC, FTNT, GWRE, NVDA 2020 and 2023): the premium clip at 3x
the class median and the held TTM margin put the base path far below today's price where the market was pricing earnings the
trailing record did not show. MU 2023 (47.5x, 7% margin) and NVDA 2019 (30x, 31% margin, +23% run rate) are upswings that a
faded run rate and a held margin cannot reach.
Lane far above at 3 years: MKSI 2018 (data defect above), CCL 2019 and 2017 (8-12x into the pandemic), PCG 2017 (wildfire
bankruptcy), OVV 2023 and EBAY 2022 (raw trailing P/E 2.1x and 2.0x: TTM net income far above the usual run of earnings, EBAY's
above its revenue; v2 reads reported TTM net income, as registered), AER 2019, INTC 2021. At 5 years BIIB 2020, PYPL 2021, GILD
2016, RVTY 2021 (a 50% run rate in mid-2021).
Closest (|e| < 0.002): AMGN 2022, GD 2021, LECO 2023, HCA 2022, AAPL 2022 at 3 years; RTX 2017, MKSI 2021, AVY 2020, CHKP 2020,
QCOM 2017 at 5 years.

## Limitations

- Survivorship: the universe is today's ~490 largest US companies, so every realized price is a survivor's, and the cross-section
  table is survivor-biased by construction. A PASS here would have overstated the lane on the full population; the FAIL stands
  regardless (the deflated check is worse, not better).
- In-sample design decade: every v2 change was chosen from v1's attribution on this same 2016-2025 window (sections 1 and 8 of the
  preregistration). The constants were stated with reasons and fixed before the run, not searched, but the test is not out of
  sample. A failure on an in-sample design is the more informative direction.
- A bull-market decade: the median survivor's 5-year price ratio was about x1.65 and its margins rose; a method that holds today's
  margin is biased low in such a decade by construction. The deflated check and the forward ledger remain the honest tests.
- No guidance or consensus: only the run-rate + base-rate variant was tested; the guided variant (section 5) is untested and must
  not be shown as measured.
- One formation a year: 6-10 overlapping windows; yearly rank statistics with t below about 2 are noise.
- Point in time, as v1 (low severity, no point-in-time source): today's industry / sector / SIC labels drive the cyclical flag for
  every formation; `CPI_MAY` is the 2026 vintage; The Base Rate Book (September 2016) postdates the July 2016 formation.
- Pipeline: measured at 5b813ae of branch price-path; the main branch's later cyclical-flag changes (2355e4a, b2012e4) are not
  in this measurement. engine_irr comes from the engine at ff36ede; later engine changes are unmeasured.
- S3 tests coverage, not width: the band passed by being 4.4x / 7.0x wide at 3 / 5 years.
- The base path's 1% margin floor (clarification 3) lifts a sub-1% TTM margin, about 2% of rows (mostly distributors), the MKSI
  2018 defect among them.
- The attribution conditions on a usable row (NI > 0) at t + h; it locates the error, it does not validate a fix.

## What follows (nothing here changes v2)

- Recorded and not shown. `pricepath.MEASURED_V2` / `MEASURED_VERDICT_V2` / `TABLE_STATUS_V2` carry the measured error into every
  live spec-2 output's band statement; `render.hebrew_table` is not changed. The table keeps what it shows today (value, the
  expected-return row; `docs/PRICE_PATH_2026-10.md` recommends `pricepath.irr_path_band_he()`, the engine-IRR price path's
  measured error, under it). That path (engine_irr) remains the best-measured price path in this build, and it is itself biased
  low (-0.06 / -0.16 / -0.27).
- No constant of v2 may change. Any third version is a new preregistration with a new spec_version. What this result locates:
  the multiple target is now right on median; the remaining error is the earnings path (the held margin among survivors, the two
  multiple tails, thin margins, small companies). The section 8 hypotheses (reading an earnings fall or surge off a multiple far
  from its class) are the registered candidates; the evidence here neither tests nor supports them.
- A plain 7% drift was the most accurate price path tested on this sample; no fundamental path has beaten it yet. That is a fact
  about this decade's survivors, not a recommendation to forecast prices with it.

## Hebrew note for the owner (written after the verdict)

גרסה 2 של "מחיר צפוי לחמש שנים" נבדקה פעם אחת על 2016–2025, בדיוק כפי שנרשם מראש, ונכשלה. היא טובה מגרסה 1: המחיר ה"סביר"
שלה נמוך מהמחיר שהתממש בחציון בכ-4% אחרי שנה, 11% אחרי שלוש שנים ו-19% אחרי חמש (בגרסה 1: 11% / 27% / 40%). אבל אחרי 3 ו-5
שנים הסטייה גדולה מהמותר (10%), והדיוק שלה חלש מזה של "המחיר של היום ועוד 7% בשנה" ומזה של מסלול התשואה הצפויה של המנוע.
הטווח בין "רע" ל"טוב" הכיל את המחיר שהתממש ב-79% / 77% מהמקרים, אבל רק כי הוא רחב מאוד (פי 4.4 אחרי שלוש שנים ופי 7 אחרי
חמש). מה תוקן: יעד המכפיל הוא עכשיו המכפיל שהשוק באמת שילם, וזה כמעט ביטל את טעות המכפיל. מה נשאר: המסלול מחזיק את שולי
הרווח של היום, והחברות ששרדו הגדילו אותם. לכן המסלול לא מוצג בטבלה, גם לא כטיוטה; הטבלה ממשיכה להציג שווי ותשואה צפויה.

## Reproduction

```
ARGS="/home/user/fundamental-engine/runs/universe_2026-10-21 --name large500 --from 2016 --to 2025 --workers 2 \
      --prices /home/user/fundamental-engine/runs/backtest/raw/prices \
      --rates /home/user/fundamental-engine/runs/backtest/raw/macro/fred_GS10.csv --engine-rows <engine rows.jsonl>"
python scripts/backtest_pricepath.py $ARGS --spec 1 --out <v1 dir>                                   # lane_v1 baseline, ~8 min
python scripts/backtest_pricepath.py $ARGS --spec 2 --v1-rows <v1 dir>/rows.jsonl --out <v2 dir>     # ~10 min
python scripts/backtest_pricepath.py $ARGS --spec 2 --v1-rows <v1 dir>/rows.jsonl --out <v2 dir> --evaluate-only
```

Deterministic: rows.jsonl (43 MB) and anchors.jsonl / tables.json are not in Git; summary.json and REPORT.md are
(`runs/backtest/pricepath_v2/`), and `tests/test_pricepath_v2.py` checks that `MEASURED_V2` is read off that summary.
