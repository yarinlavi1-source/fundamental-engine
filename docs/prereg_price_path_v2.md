# Pre-registration: expected price path lane, version 2 (NOT YET RUN)

Drafted 21.10.2026 and finalized 25.10.2026, after version 1 (`docs/prereg_price_path.md`, spec_version 1) FAILED its
preregistered walk-forward (`docs/PRICE_PATH_RESULTS_2026-10-21.md`, `docs/PRICE_PATH_2026-10.md`) and before any v2 code
exists. Nothing below has been computed: no v2 path, no v2 metric, no constant tried on the data. A later session implements
spec_version 2 from this document, runs it once as section 6 says, and reports whatever comes out. Every change from v1 is
grounded in v1's error decomposition (section 1); no change is a tuning of a v1 constant. The v1 module stays unchanged as a
baseline (section 6.4). After the first full run nothing in sections 2-7 may change; a bug fix re-runs everything and both
results are logged in section 10.

## 1. What v1's decomposition showed, and the change it grounds

v1's base path: trailing run rate faded to the Base Rate Book size-class median, the margin held at its structural (or mid-cycle)
anchor, a Gordon-justified trailing P/E from growth / ROIC-payout / cost of equity, today's premium over it halving at the stage
growth half-life. Ex-post attribution on the common sample, using the lane's own rows at t + h as the realized fundamentals
(log(P_lane / P_actual) = revenue + margin + shares + multiple terms, exact; medians; n 2,201 / 1,639 / 1,108 at 1 / 3 / 5
years, the common rows with an ok v1 row at t + h, i.e. NI > 0 then; `scripts/pricepath_v1_attribution.py`):

| term | 1y | 3y | 5y | what happened | v2 change (section 3) |
|---|---|---|---|---|---|
| total | -0.11 | -0.32 | -0.55 | the lane's price sat below the realized one at every horizon | |
| revenue | +0.00 | -0.02 | -0.08 | the fade is right on median; wrong in both tails: run rate < 0 (lane x0.97 vs realized x1.17 at 3y) and run rate > 20% (x1.66 vs x1.35) | run rate = mean of last FY, last quarter AND the 3-year CAGR (3.1): the last leg no longer stands alone |
| margin | -0.06 | -0.17 | -0.24 | held at the anchor (median 10.4%) while the median survivor's TTM margin went 11.1% -> 12.3% in three years (5-year subsample: 10.5% -> 12.7%); cyclicals -0.33 / -0.41 at 3 / 5 years (mid-cycle normalization pulled the base down and the cycle did not turn); structural -0.08 / -0.13 | base = TTM margin held, for cyclicals too (3.3); the mid-cycle / own-history margins define the bear and bull instead |
| shares | -0.00 | -0.01 | -0.01 | immaterial | unchanged |
| multiple | -0.07 | -0.10 | -0.22 | today's 26x faded toward a 16.8x justified level; the market's median trailing P/E stayed 23-25x throughout (3-year subsample: 23.6x at formation, 24.5x three years later); the Gordon level is 0.54 of the Damodaran cross-section and 13-23x by growth bucket where the market paid 21-40x | the convergence target = the market's own point-in-time multiple by growth / profitability class (3.4); the fade speed is kept, in log space (3.5) |

Two further facts from v1 that shape the band:
- The market DID mean-revert multiples toward the cross-sectional median in both tails, at about the lane's speed: from above 60x
  it closed 45% of the log gap to the median in three years (70.6x -> 43.9x against a 24.5x median), from below 10x 55% (7.8x ->
  14.7x). A three-year half-life in log space removes 50%. The target was wrong, not the half-life. (In the < 10x bucket the
  rise came with an earnings fall, margin term +0.27: the market was pricing a decline the run rate could not see; v2 does not try
  to read that from the price, section 8.)
- v1's band was one-sided: 7% of realized prices below the bear and 40% / 52% above the bull at 3 / 5 years. The bear subtracted an
  ABSOLUTE margin spread (a 1% floor in 19% of rows), faded a NEGATIVE premium or excess growth faster than the base (the
  registered half-life rule; the bear's multiple was at or above the base's in some year in 17% of rows) and had no ordering
  clamp (`scripts/pricepath_v1_ordering.py`). v2 builds the band from documented quantiles (growth: the class p25 / p75;
  margin: the company's own 10-year low / high; multiple: the cross-section's p25 / p75) and orders the scenarios by
  construction.

Realized distributions the band is measured against (the attributed subsample above, for the record, not a target): 3-year
log price ratio p10 -0.13, p25 +0.08, median +0.33, p75 +0.59, p90 +0.88; 5-year p10 -0.03, p25 +0.23, median +0.56, p75 +0.92,
p90 +1.31.

## 2. Data and point-in-time rules

As v1 section 2 (universe `runs/universe_2026-10-21` QUEUE_large500, formations 1 July 2016-2025, as_of 30 June, `facts.usable`
filed + 2 days, `engine_backtest.traded_price`, GS10, Damodaran's dated implied ERP, the first three pipeline stages, no
guidance / consensus in the backtest, realized price = split-adjusted close at the anniversary month, no dividends), plus:
- The cross-section table of section 3.4 is built, at each formation, from the universe's own rows at that as_of (every company
  whose anchors are usable then). It is point in time by construction (nothing after as_of enters) and survivor-biased by
  construction (today's survivors). The live engine builds it from the latest saved universe run at the valuation's as_of.
- Disclosed v1 look-ahead of low severity, unchanged in v2 because no point-in-time source exists: the cyclical flag and the
  business type read today's listing industry / sector and SIC for every formation; `CPI_MAY` is the 2026 vintage; The Base Rate
  Book (26 September 2016) postdates the July 2016 formation (data 1950-2015). The 20 share-unit defect rows (M_0 < 1x, BRK-B,
  FOXA, CMG 2022, TEVA 2016, MKSI 2018, MPLX 2019) stay unless the shared data layer fixes them BEFORE the v2 run; a fix there is
  logged in section 10 with the row count.

## 3. The method, exactly (differences from v1 marked [v2])

Notation as v1: t formation, h = 1..5, s in {bear, base, bull}; stage, cyclical from the model card.

### 3.1 Anchors
- Rev_0, NI_0 (> 0 required, same exclusion), S_0, dividends: as v1 (deviation 2 of v1 kept: facts.ttm for the same period end).
- [v2] g_run = mean of the available among g_FY (latest FY / prior FY - 1), g_Q (latest standalone quarter vs 364 +- 10 days
  earlier) and g_3 (`revenue_cagr_3y`); at least one required. Cyclical: the 5-year trend rule of v1 unchanged (revenue_cagr_5y,
  else 3y, clipped to [-0.05, +0.25]). Clip to G_RUN = [-0.30, +0.60], flagged.
- [v2] m_TTM = NI_0 / Rev_0 is the base margin for every company (no mid-cycle anchor in the base). Own margin history:
  m_lo / m_hi = lowest / highest fiscal-year net margin of the last <= 10 FYs with revenue > 0 (>= 3 FYs; else m_TTM -/+ MS,
  flagged `margin_history_short`). The mid-cycle median of v1 is reported as a diagnostic.
- [v2] EPS_0 = NI_0 / S_0 (reported TTM, not normalized); M_0 = P_0 / EPS_0 = the raw trailing P/E. No input cap: the premium of
  3.5 is clipped in log space instead.
- Share path, ROIC R, cost of equity ke, terminal growth: as v1 (R and ke now serve only the diagnostic Gordon multiple of 3.6).

### 3.2 Growth path (base-rate quantiles per scenario; same half-life; ordered)
Size class and nominal class medians as v1 (base_rates.CLASSES + PI, class re-read yearly from the scenario's revenue path).
[v2] Scenario targets: T_base(n) = the class median at horizon n (as v1); T_bear(n) = the class 25th percentile at n, T_bull(n) =
the 75th, each read from the class' n-year bin frequencies with linear interpolation inside a 5-point bin (the open tails read as
5-point bins, as `base_rates.share_at_least`), nominal (+ PI), T_bear clamped <= T_base <= T_bull.
    g_1(s) = g_run + {-g_spread, 0, +g_spread}   (forecast.STAGE g_spread, as v1)
    g_h(s) = T_s(h) + lambda_s (g_1(s) - T_s(1)) 0.5^((h-1) / HL),  h = 1..15,  HL = forecast.STAGE half_life for EVERY scenario
[v2] HL_SCALE is dropped: the half-life rule inverted the scenarios whenever the excess was negative (v1 audit). The bear and bull
differ by their year-1 spread and their quantile target, not by speed.
[v2] Ordering by construction, every year: g_h(bear) = min(g_h(bear), g_h(base)); g_h(bull) = max(g_h(bull), g_h(base)).
Base-rate ceiling lambda: as v1 (base 0.10, bull 0.05, bear none; bisection).

### 3.3 Margin path (the bear and the bull change the business; relative, bounded by the company's own history)
MS = (1 - TAX_STAT) x forecast.STAGE m_spread, CONV = forecast.STAGE conv (as v1).
    base:  m_h = m_TTM  (guided m_1 as section 5)
    bear:  target_bear = max( min(m_lo, m_TTM - MS), 0.5 m_TTM )
    bull:  target_bull = min( max(m_hi, m_TTM + 0.75 MS), 1.5 m_TTM )
    m_h(s) = m_TTM + (target_s - m_TTM) x min(h / CONV, 1)
The bear goes to the worse of the company's own worst year and the stage spread, but never below half of today's margin (a
halving is already a severe shock; v1's absolute spread made a near-wipeout of thin margins); the bull symmetrically. A cyclical
at a peak therefore has a bear at its own trough margin (the cycle is in the band, not in the base). Floor M_FLOOR = 0.01 kept
(binds only when m_TTM < 2%), flagged. NI_h(s) = Rev_h(s) m_h(s); E_h(s) = NI_h(s) / S_h.

### 3.4 Multiple target: the market's point-in-time multiple by growth / profitability class [v2]
At formation t, from every universe company with usable anchors at the same as_of (NI_0 > 0, S_0, P_0; raw trailing P/E
PE_i = P_i S_i / NI_i within [1, 500]):
- growth bucket by g_run: < 0%, 0-5%, 5-10%, 10-20%, 20-30%, > 30%; margin bucket by m_TTM: < 5%, 5-10%, 10-20%, > 20%.
- M_cs(t; g-bucket, m-bucket; q) = the q-quantile of PE over the cell, q = 0.25 (bear), 0.50 (base), 0.75 (bull); a cell needs
  CELL_MIN = 20 rows, else the growth bucket's row (>= 20), else the whole cross-section. Quantiles are ordered across the
  scenarios by construction.
- Lookup for scenario s at year h: growth bucket of g_h(s) (year 0: g_run), margin bucket of m_h(s) (year 0: m_TTM). As the
  scenario's growth fades down the bucket ladder its target multiple falls: the multiple compresses with the growth path, and
  the bear's target is the lower quantile of a lower-growth class, by construction.
Why this and not v1's Gordon level: the v1 justified multiple was 0.54 of the Damodaran cross-section and 13-23x by growth bucket
against 21-40x paid, and the market converged on its own class medians (section 1), which were stable within a few turns over
2016-2025 (5-10% growers: 19-28x by year; 2021 the high year). Profitability stays in through the margin bucket; the growth path
and the cost of equity / payout stay in only through the diagnostic ratio of 3.6, because the market level, not the Gordon level,
is what a PRICE path has to meet.

### 3.5 Today's multiple as an input that converges, in log space [v2]
    L_0 = clip( ln M_0 - ln M_cs(t; bucket(g_run), bucket(m_TTM); 0.50), -ln 3, +ln 3 )
    ln M_h(s) = ln M_cs(t; bucket(g_h(s)), bucket(m_h(s)); q_s) + L_0 x 0.5^(h / HL),  HL = the stage half-life (all scenarios)
    M_h(bear) = min(M_h(bear), M_h(base)); M_h(bull) = max(M_h(bull), M_h(base)); clip to [PE_FLOOR, PE_CAP] = [5, 120], flagged.
The premium is the log distance from the base class median; it halves at the stage half-life (v1's speed, which section 1 found
matched the market's own reversion from both tails); it is the same L_0 in every scenario (the scenarios differ by target
quantile, growth and margin). +-ln 3 bounds a company at one third to three times its class median: beyond that the market is
pricing something the trailing fundamentals do not show (v1's > 60x bucket), flagged `premium_clipped`.

### 3.6 Price and diagnostics
P_h(s) = E_h(s) x M_h(s), h = 1..5. Output per row as v1 (paths, anchors with fact references, lambda and percentile, flags,
spec_version 2, sha256 of inputs + outputs), plus: the cross-section cell used per year and scenario (bucket labels, n, quantile),
L_0, the v1 Gordon multiple M_just(h, base) and its ratio to M_cs (diagnostic: how far the market level sits above the
no-value-creation level), the own-history margins, and `ordering_checked: true` (assertion bear <= base <= bull in growth, margin,
multiple and price every year; a violation is an error, not a flag).

## 4. Constants (new or changed; the rest as v1 section 4)
| constant | value | reason |
|---|---|---|
| g_run blend | mean(g_FY, g_Q, g_3y) | section 1: the last leg alone extrapolated events in both tails |
| T_bear / T_bull quantiles | class p25 / p75 (bin-interpolated) | The Base Rate Book exhibit 4 frequencies already in base_rates.CLASSES; a quartile band is the documented spread of the class |
| HL_SCALE | removed (1.0 for all) | the scenario speed rule inverted negative excess / premium (v1 audit) |
| bear / bull margin targets | min(m_lo, m_TTM - MS) floored at 0.5 m_TTM; max(m_hi, m_TTM + 0.75 MS) capped at 1.5 m_TTM | relative shock bounded by the company's own history; v1's absolute spread floored 19% of bears |
| margin history | last <= 10 FYs, >= 3 | the same window as v1's mid-cycle median |
| growth buckets | < 0, 0-5, 5-10, 10-20, 20-30, > 30% | v1's attribution buckets (section 1); 114-884 rows per bin over 2016-2025, as few as 5 in one formation year (hence CELL_MIN and the fallbacks) |
| margin buckets | < 5, 5-10, 10-20, > 20% | as the attribution; thin-margin / high-growth cells are where the cross-section pays 60x |
| cross-section quantiles | 0.25 / 0.50 / 0.75 | interquartile band |
| CELL_MIN | 20 | a quantile of fewer rows is noise |
| PE range for the table | [1, 500] | excludes unit defects and near-zero earnings |
| L_0 clip | +-ln 3 | one third to three times the class median |
| PE_FLOOR / PE_CAP | 5 / 120 | floor as v1; cap = about 3 x the highest class median (39.6x for > 30% growers) |
| PI, G_RUN, CYC_G, CYC_CONV, CEIL, R_MAX, TAX_STAT, M_FLOOR, SHARE_CLIP, N_JUST, PATH_YEARS, CPI_MAY | as v1 | unchanged |

## 5. Live-engine additions (untested by the backtest, shown as such)
As v1 section 5 (dated guidance / consensus with evidence ids replace g_1 / g_2 and m_1; backlog lifts the ceiling), with the
cross-section table of 3.4 built from the latest saved universe run at as_of; the output names the run and its date. The DCF,
expectation_gap and redteam gates are unchanged; the lane adds rows to the table only under section 7's rule.

## 6. Evaluation protocol (fixed before the first v2 run)
6.1 Runs: `scripts/backtest_pricepath.py ... --spec 2` (two passes per formation: anchors for every company, then the table and
the paths), `--workers 2`; `--engine-rows` as v1; `--evaluate-only` rebuilds summary and report. The v1 rows supply the
`lane_v1` baseline: they are not in Git (29 MB), so re-run the unchanged spec-1 code (scripts/backtest_pricepath.py at commit
24d5ef0, or `--spec 1` once the flag exists), which is deterministic (an interrupted second run of 21.10.2026 reproduced
3,730 rows with identical input and output hashes).
6.2 Horizons and formation years: as v1 (1y 2016-2025, 3y 2016-2023, 5y 2016-2021).
6.3 Common sample: as v1 (lane path, realized price, engine base_irr, 3-year EPS CAGR), plus the v1 base path for the same
(symbol, formation); counts of each exclusion reported. Attributed subsample: common rows with a usable v2 row at t + h.
6.4 Predictors: lane_base, lane_bear, lane_bull; random_walk; drift_7; analyst_const; engine_irr (and engine_irr_total); lane_v1
(v1's base path). All prices in t-share units.
6.5 Metrics: as v1 (bias median / mean, MdALE, within25, share above, head to head vs every baseline including lane_v1, band
coverage at every horizon 1-5, yearly Spearman), by formation year, stage, cyclical flag, raw M_0 bucket, size class, and
by cross-section cell.
6.6 Attribution (registered, primary diagnostic): per attributed row e_rev, e_margin, e_shares, e_multiple as section 1 defines
them, with the v2 row at t + h as the realized side; medians and p10 / p90 per horizon and per bucket; the same for lane_v1.
6.7 Diagnostics: ordering assertion count (must be 0), flag shares, the L_0 distribution, M_just / M_cs ratio, cell usage
(share of rows served by a cell, a growth row, the whole cross-section), table medians per formation year.
6.8 Robustness (reported, not the verdict): SPY-deflated metrics as v1 6.8; and the metrics with the data-defect rows removed.

## 7. Success criteria (raw common sample)
S1 accuracy: at h = 3 and 5 the lane's MdALE is below analyst_const's, engine_irr's AND lane_v1's; at h = 1, 3, 5 it is not
   worse than min(random_walk, drift_7) by more than 0.03.
S2 bias: |median log error| <= 0.10 at h = 1, 3, 5.
S3 band: at h = 3 and 5 the realized price lies within [bear, bull] in >= 50% of rows and neither tail exceeds 30% (v1: 35%;
   v2's band is built from quartiles of each driver, so a calibrated tail should be near or below 25%).
S4 attribution: at h = 3 the medians of e_rev, e_margin and e_multiple are each within +-0.10 (a pass must not come from
   offsetting errors), on the attributed subsample.
PASS = S1-S4 all hold -> the lane appears next to the DCF as the expected price path with the band sentence of section 9.
PARTIAL = S2 and S4 hold, S1 or S3 fails -> a draft row labelled with what failed.
FAIL = S2 or S4 fails -> recorded in docs; not shown.
Expectation written before the run, so a result is not read as a surprise: section 1's attribution leaves a margin term of
about -0.08 / -0.13 (3 / 5 years) for structural businesses even with the TTM margin held, which no point-in-time rule
removes (plausibly the 2018 tax cut and a decade of margin expansion among survivors; not decomposed further); v2 may
therefore pass S2 at 1-3 years and fail it at 5 years. The criteria are not softened to avoid that. A PASS on this survivor
sample would still overstate the lane on the full population of companies (delisted names are absent); the deflated check and
the forward ledger remain the honest tests.

## 8. What was seen before this registration, and what is deliberately NOT in v2
Seen: v1's full results, the leakage audit, the attribution tables, the point-in-time class multiple tables (by year, growth
bucket, margin bucket, stage), the scenario-ordering counts. Not computed: any v2 path or metric. Judgment constants (section 4)
were chosen from the structure of those tables, not fitted to the errors: their values are stated with reasons and a later
reader can disagree with them, but they were not searched.
Not in v2 (hypotheses the decomposition suggests but that would read the FUTURE off the price, left for a v3 if v2 fails):
reading an earnings decline from a multiple below the class p25 (the < 10x bucket's margin term +0.27) or an earnings surge from
one above 3x the median (the > 60x bucket's -0.68); an earnings path from the market's implied growth; a multiple fade toward a
regime-dependent level. v2 keeps the trailing fundamentals + base rates design and changes only what the attribution located.

## 9. Presentation if the verdict is PASS (Hebrew wording for render.hebrew_table; written now so it is not fitted later)
The value rows stay as they are. Added under them, labelled as PRICE estimates, not value, with the numbers read from the
executed path (never typed by hand):
- one row in the main table: | מחיר משוער בעוד 5 שנים (לא שווי) | רע X / סביר Y / טוב Z |
- a yearly block after the value block, one row per anniversary of as_of through the fifth (bear / base / bull paths):

**מחיר משוער בכל שנה, לפי תרחיש (הערכה מותנית, לא שווי)**

| מועד | תרחיש רע | תרחיש סביר | תרחיש טוב |
|---|---|---|---|
| היום | P_0 | P_0 | P_0 |
| בעוד שנה ... בעוד 5 שנים | P_h(bear) | P_h(base) | P_h(bull) |

- ONE band sentence in the notes, filled from the v2 run's summary.json:
"במבחן על 2016–2025 (חברות גדולות ששרדו) המחיר ה'סביר' היה בטווח של ±25% מהמחיר שהתממש ב־A% / B% / C% מהמקרים אחרי 1 / 3 / 5
שנים, והמחיר שהתממש היה בין 'רע' ל'טוב' ב־D% / E% מהמקרים אחרי 3 / 5 שנים. זו הערכה מותנית למחיר, לא תחזית ולא אישור לעסקה;
תחזיות הנהלה וקונצנזוס, אם נכנסו, לא נמדדו."
PARTIAL: the same rows, the block title and the main-table row prefixed "טיוטה (נכשל במבחן: ...)" naming the failed criterion in
plain words. FAIL: no rows; the table keeps what it shows today (the expected-return row; `docs/PRICE_PATH_2026-10.md` recommends
the measured sentence `pricepath.irr_path_band_he()` under it).

## 10. Deviations log (dated; empty at registration)

Pre-run clarifications, 25.10.2026, logged with the implementation (`fundamental_engine/pricepath.py` spec_version 2,
`scripts/backtest_pricepath.py --spec 2`, `tests/test_pricepath_v2.py`) and before any v2 run. Sections 2-7 are unchanged; no v2
path was computed on market data and no v2 metric exists. Each item is a reading the text left open, not a change of method:

1. 3.2, T_bear / T_bull between the book's horizons. Bin frequencies exist only for n = 1, 3, 5 and 10 years: the p25 / p75 are
   read from those bins (`pricepath.class_quantile`, the inverse of `base_rates.share_at_least`) and interpolated linearly
   between them, flat after 10 years, which is the median's own rule (`class_median`); PI is added as for the median.
2. 3.2, where the ordering bound sits. The bound is applied year by year inside the path: the bounded rate is the one that
   compounds revenue, so the next year's size class is read from the scenario's final revenue path; the base path is built
   first; the bull's base-rate ceiling bisects on the bounded path (the path that is output). Synthetic probes show why the
   bound binds at all: a scenario that crosses a size-class edge fades toward the larger class' lower target (a $300m grower's
   bull enters $325-700m in year 3 and falls below the base, still in $0-325m). Flags `growth_ordered_<s>` count it.
3. 3.3, the margin floor. "M_FLOOR = 0.01 kept" is read as spec 1 applied it: to every scenario's path, so a base with
   m_TTM < 1% is lifted to 1% (flag `margin_floored_base`; spec 1's `margin_path` floored its base the same way). Flooring the
   bear but not the base would put the bear above a sub-1% base and break the registered order.
4. 3.4, quantile estimator: linear interpolation between order statistics at (n - 1) q (numpy's default; the median at
   q = 0.5). Bucket edges: the lower edge belongs to the bucket (growth 0% is in 0-5%, 5% in 5-10%; margin 20% in > 20%), as in
   the v1 attribution and class-multiple scripts that produced the tables of sections 1 and 3.4.
5. 3.4, the last rung of the ladder. The whole cross-section is used whatever its size (flag `cross_section_below_cell_min`
   under 20 rows; a backtest formation holds hundreds); a formation without any usable row cannot be priced (error rows). The
   year-h lookup reads the scenario's final year-h growth (after the ordering bound) and margin (after the floor).
6. 3.5, the multiple order is applied before the [5, 120] clip (a clip is monotone, so the order survives). A binding order
   clamp is flagged `multiple_ordered_<s>`, the clip `multiple_floored_<s>` / `multiple_capped_<s>`.
7. 3.6, the Gordon diagnostic: spec 1's `justified_multiple` on the v2 base path (NI_0 as reported, R and ke as spec 1),
   clipped to spec 1's [5, 60] as spec 1 reported it, divided by the base cell's median, today and for h = 1..5. Undefined
   (ke <= g_T) -> None, flagged `gordon_diagnostic_undefined`; it never enters a price, so it does not stop the row. The
   ordering assertion raises `pricepath.OrderingError` (an AssertionError, not an `assert` statement, so it cannot be disabled);
   the runner records it as an error row and 6.7 counts those rows.
8. 3.1: spec 1's exclusion "normalized (mid-cycle) net margin not positive" has no counterpart (the base margin is m_TTM,
   positive whenever NI_0 is); the mid-cycle median is kept as `m_mid_diagnostic` (None under 3 fiscal years).
9. Section 5 (live engine only; never in the backtest): a guided m_1 replaces the base's year-1 margin and bounds the bear's /
   bull's year-1 margins (min / max), so the order holds by construction there too. The live table is the latest saved table
   not after as_of (`pricepath.latest_cross_section`), named with its source and date in the output. Untested, like all of
   section 5.
10. 6.1, the runner. Pass 1 appends every company's anchors per formation to `anchors.jsonl` (resumable); pass 2 builds each
    formation's table from all ok anchors in it, then the paths, and rewrites `rows.jsonl` and `tables.json` (sorted by
    formation and symbol, deterministic). The table's source string names the universe and as_of without machine paths, so
    the hashes do not depend on where the run is made. `--spec 2` requires `--v1-rows`. The spec-1 code of this commit
    reproduced the 21.10.2026 run's rows byte for byte for AAPL, BRK-B, CAH, COP, MSFT and NUE (60 rows, 2016-2025, ok and
    excluded), and `tests/test_pricepath_v2.py` pins the hashes of spec-1 outputs computed before any v2 code.
11. 6.3 / 6.6, attribution. The realized side is the status-ok v2 row of the same symbol at formation year t + h (its TTM
    revenue, net income, cover share count and traded price), with section 1's exact identity
    (`scripts/pricepath_v1_attribution.py`) and the lane's rounded year-h output as there; lane_v1's terms on the same rows
    (its own n reported). Rows formed in 2025 (1y), 2023 (3y) and 2021 (5y) have no row at t + h (no 2026 formation) and fall
    out of the attributed subsample, as in section 1.
12. 6.5 "by cross-section cell" is the formation cell, bucket(g_run) x bucket(m_TTM). 6.8 "data-defect rows" are the rows with
    a raw M_0 below 1x (section 2's description of the 20 rows), listed by symbol and year in the report so the list can be
    checked against the 20 named.
13. Section 9 is not wired into `render.hebrew_table`: it applies only after a PASS or PARTIAL verdict. Until the run is
    recorded `pricepath.MEASURED_V2` is empty, `TABLE_STATUS_V2 = 'not_shown'` and every v2 output's band statement says the
    lane is untested.

Before the run, a pass-1 smoke check read v2 anchors (inputs only; statuses and flag names printed) for AAPL, COP and CAH at two
formations. No path, table, realized price or metric was computed.

The registered run (section 6.1), for the session that makes it (engine rows as v1: the 2026-10-21 engine walk-forward rows of
v1 deviation 5; `--evaluate-only` on the last line only rebuilds summary.json and REPORT.md):

    ARGS="/home/user/fundamental-engine/runs/universe_2026-10-21 --name large500 --from 2016 --to 2025 --workers 2 \
          --prices /home/user/fundamental-engine/runs/backtest/raw/prices \
          --rates /home/user/fundamental-engine/runs/backtest/raw/macro/fred_GS10.csv --engine-rows <engine rows.jsonl>"
    python scripts/backtest_pricepath.py $ARGS --spec 1 --out <v1 dir>
    python scripts/backtest_pricepath.py $ARGS --spec 2 --v1-rows <v1 dir>/rows.jsonl --out <v2 dir>
    python scripts/backtest_pricepath.py $ARGS --spec 2 --v1-rows <v1 dir>/rows.jsonl --out <v2 dir> --evaluate-only
