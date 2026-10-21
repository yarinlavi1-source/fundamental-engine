# Pre-registration: the expected price path lane (growth fade + justified multiple)

Written 21.10.2026 BEFORE `fundamental_engine/pricepath.py`, `scripts/backtest_pricepath.py` or any
evaluation run exists. Every constant, rule, sample definition, metric and pass criterion below is fixed
here. After the first full run nothing in sections 2-6 may change; a bug fix re-runs everything and both
results are logged in section 9 with a date (the practice of `docs/prereg_expectation_gap.md`).

## 1. Question and what the lane is

Next to the DCF the owner wants the thing a good analyst produces: a year-by-year price for the next five
years, in bad / likely / good versions, built from (a) the growth the company already shows, faded with the
base rates for its revenue scale, (b) a margin path, and (c) a multiple that is tied to those fundamentals
and compresses as growth slows, with today's multiple allowed in only as a starting point that converges to
the justified one. The DCF lane measures value; this lane forecasts the *price*, and it is tested as a price
forecast. A dated price here is a conditional estimate, not a predicted exchange price, and it never
authorises a trade.

What is already known and shaped the design (disclosed so the test is not presented as blind to it):
`scratchpad/accuracy/repA` and `repB` (saved engine_v3 rows, 2016-2025 formations, same universe) found the
engine's base value under-predicts realized prices by a median log error of about -0.5 to -0.9 at 1/3/5
years; an analyst-style path (today's P/E kept, EPS compounding at the trailing 3-year EPS CAGR) has about
zero bias at 1 year and +0.05/+0.09 at 3/5 years but is no more accurate than price x 1.07^h (median
|log error| 0.19 / 0.35 / 0.49 vs 0.18 / 0.26 / 0.38); pulling the multiple one third of the way to 20x
made the bias negative (-0.04 / -0.01 / -0.09). The best predictor was within +-25% of the realized price in
only ~65% / 50% / 37% of rows. No constant below was fitted to those numbers; the qualitative lesson taken
is that a multiple pulled toward a level the market does not pay produces a negative bias, which is why
the premium over the justified multiple fades with a half-life instead of being removed at once, and why
the multiple's level is checked against documented base rates rather than set to 15x or 20x.

## 2. Data and point-in-time rules (identical to the engine walk-forward)

- Universe: `runs/universe_2026-10-21/companies/QUEUE_large500.json` (491 of today's largest US operating
  companies, each with saved SEC companyfacts). Survivorship: today's survivors; stated with every result.
- Formation dates: 1 July 2016 ... 1 July 2025; as_of = 30 June (the day before), as
  `engine_backtest.formation_inputs`. Facts are usable only when filed by as_of and about a period ended by
  then (`facts.usable`: filed + 2 days). Documents saved in 2026 are skipped by `pipeline._business`.
- Price P_0: `engine_backtest.traded_price` (Yahoo split-adjusted June close x the splits after as_of).
- Risk-free rf: FRED GS10 monthly average of the as_of month (`runs/backtest/raw/macro/fred_GS10.csv`).
- ERP: Damodaran's start-of-year implied ERP in force at as_of (`engine_backtest.IMPLIED_ERP`, usable from
  1 February; the 2016-2025 values are already in the repository).
- Company facts: the first three pipeline stages only -- `pipeline.factbook_from_run_dir`, `dataqa.validate`,
  `analysis.analyze`, `model_card.build` -- so the lane reads exactly the TTM, annual rows, point-in-time
  share count, stage and cyclical flag the engine reads. The forecast/valuation stages are not run by the lane.
- No guidance, no consensus, no analyst inputs in the backtest (none are saved point in time). The backtest
  therefore tests the trailing-run-rate + base-rate-fade variant only. Section 5 defines how the live engine
  may add dated guidance/consensus with evidence ids; that variant is NOT tested here and is never shown as
  if it were.
- Realized price at horizon h (years): close of the month as_of + 12h months (`backtest._at`, within its
  two-month tolerance), split-adjusted, no dividends: realized ratio R_h = close(t+h) / close(t); actual_h =
  P_0 x R_h. Price only (dividends are payout, forecast separately), so every predictor is a price.

## 3. The method, exactly

Notation: t = formation, h = 1..5 = years after as_of (the h-th anniversary month end). Scenario s in
{bear, base, bull}. Stage = `card['stage']['value']` (early / growth / maturing / mature / decline /
turnaround). Cyclical = `card['cyclical']['value']` is true (industry flag AND the data show a cycle).

### 3.1 Anchors (from `an`, `qa.resolved`, `facts`)
- Rev_0 = `an['ttm']['revenue']` (facts.ttm rule: four standalone quarters, else FY + quarters since less
  the same quarters a year earlier, else the latest fiscal year).
- NI_0 = `an['ttm']['net_income']`. Required NI_0 > 0 (an earnings multiple needs earnings; the count of
  rows excluded for NI_0 <= 0 is reported; the DCF lane keeps those companies).
- S_0 = `qa['resolved']['shares_outstanding']['value']` (latest point-in-time common count: cover page,
  never a weighted average).
- Structural growth run rate: g_FY = `an['metrics']['revenue_growth_last']` (latest FY / prior FY - 1);
  g_Q = latest standalone quarter / the quarter ending 364 +- 10 days earlier - 1 (facts.series 'quarter').
  g_run = mean of the two that exist. Structural margin anchor m_anchor = NI_0 / Rev_0 (net margin, TTM).
- Cyclical companies (the structural/cyclical split): g_run = `revenue_cagr_5y` (else `revenue_cagr_3y`)
  clipped to [CYC_G_LO, CYC_G_HI] = [-0.05, +0.25] -- the trend, not the last leg; m_anchor = median of the
  fiscal-year net margins of the last <= 10 years (>= 3 years, else the TTM margin) = mid-cycle margin; the
  margin path starts at m_TTM and reaches m_anchor linearly over CYC_CONV = 3 years (forecast.CYCLE_CONV
  base). A peak-margin year therefore gets a lower normalized earnings base and a higher price-to-normalized-
  earnings multiple, which is the classic cyclical trap handled explicitly.
- g_run is clipped to [G_RUN_LO, G_RUN_HI] = [-0.30, +0.60]: beyond that a year is an event (acquisition,
  divestiture, collapse), not a run rate; clipped rows are counted and reported. The engine's SCALE_CAPS are
  not applied to year 1 (they contradict a reported run rate); scale enters through 3.2.
- Normalized trailing EPS: E*_0 = Rev_0 x m_anchor / S_0. Today's multiple M_0 = P_0 / E*_0.
- Share path: s = `an['metrics']['share_count_cagr_3y']` (point-in-time year-end counts, split-adjusted)
  clipped to [-0.06, +0.06], 0 when absent; S_h = S_0 (1 + s)^h, the same in every scenario (current-holder
  dilution or buyback pace persists; it is the only funding link the lane models).
- Return on capital R = `an['metrics']['roic']` (NOPAT / average invested capital, latest FY) clipped to
  [ke, R_MAX = 0.50]; R = ke when absent (no value creation assumed: steady-state multiple).
- Cost of equity ke = rf + ERP + STAGE[stage]['ke'] (forecast.STAGE business-risk add-on: early .03,
  growth .015, maturing .005, mature 0, decline .01, turnaround .025). No beta.
- Terminal growth g_T = min(rf, g_{h+10}) (the engine's convention that perpetual growth follows the
  risk-free rate, never above the path it continues).

### 3.2 Growth path (base-rate fade by revenue size class)
Size class at formation: `base_rates.reference_class(Rev_0_in_2015_USD)`, with Rev_0 restated to 2015
dollars by the CPI-U of May of the formation year (section 4: the latest index published by as_of).
c_class(n) = nominal class median CAGR at horizon n = class median real CAGR (base_rates.CLASSES,
horizons 1/3/5/10) + PI, PI = 0.02 (a fixed long-run inflation assumption; the dated breakeven is not saved
point in time); linear between the tabulated horizons, flat after 10.
Each year the class is re-read from the forecast revenue deflated at PI (growth slows as the company gets
bigger): class_h = class(Rev_{h-1} / (1+PI)^{h-1}).

Year-h growth, scenario s:
    g_h(s) = c_{class_h}(h) + lambda_s x (g_1(s) - c_{class_1}(1)) x 0.5^((h-1) / HL_s),  h = 1..15
with g_1(base) = g_run, g_1(bear) = g_run - g_spread, g_1(bull) = g_run + g_spread (forecast.STAGE
g_spread: early .10, growth .06, maturing .04, mature .03, decline .04, turnaround .06);
HL_base = forecast.STAGE half_life (early 2.0, growth 2.5, maturing 3.0, mature 3.0, decline 2.0,
turnaround 2.0; Koller et al.: >20% growers reach ~8% in five years and ~5% in ten), HL_bear = 0.8 HL_base,
HL_bull = 1.3 HL_base (the engine's scenario rule).
Base-rate ceiling: lambda_s in (0, 1] is the largest value for which the 5-year real CAGR of the path is
not rarer in the size class than CEIL_s = {base: 0.10, bull: 0.05, bear: none} (base_rates.share_at_least
>= CEIL_s; bisection, 40 steps); lambda_s = 1 when the unshrunk path already passes. Rationale: The Base
Rate Book's own advice that a forecast in the tail needs specific evidence; an automatic run has none (the
live engine may lift the ceiling with evidenced backlog or guidance, section 5). lambda and the percentile
are reported per row.
Rev_h = Rev_0 x prod_{k<=h} (1 + g_k).

### 3.3 Margin path (the bear changes the business)
m_h(base) = m_anchor (structural) or the cyclical convergence above. m_h(bear) moves linearly from m_1 to
m_anchor - MS over CONV years and holds; m_h(bull) to m_anchor + 0.75 MS (the engine's bull margin rule),
where MS = (1 - TAX_STAT) x forecast.STAGE m_spread (after-tax version of the engine's operating spread:
early .10, growth .07, maturing .05, mature .04, decline .05, turnaround .08; TAX_STAT = 0.21) and CONV =
forecast.STAGE conv (early 7, growth 6, maturing 5, mature 4, decline 4, turnaround 5). A margin path is
floored at M_FLOOR = 0.01 (a negative forecast margin ends the P/E basis; flagged, the row stays with the
floor).
Earnings: NI_h(s) = Rev_h(s) x m_h(s); E_h(s) = NI_h(s) / S_h.

### 3.4 Justified multiple (fundamentals: growth path, ROIC via payout, cost of equity)
Reinvestment need from the sustainable-growth identity: payout_k(s) = clip(1 - g_{k+1}(s) / R, 0, 1); cash
returned C_k = NI_k x payout_k (dividends and buybacks together; the per-share split is the share path).
Justified trailing P/E at the end of year h on the scenario's own path, N = 10 explicit years after h:
    V_h(s) = sum_{k=h+1}^{h+N} C_k(s) / (1+ke)^(k-h)
           + [ NI_{h+N}(s) (1+g_T) x clip(1 - g_T / R, 0, 1) / (ke - g_T) ] / (1+ke)^N
    M_just(h, s) = V_h(s) / NI_h(s)
Properties fixed by construction (unit tests): with R = ke and zero growth the multiple is 1/ke (the
steady-state "commodity" multiple of Mauboussin & Callahan 2014, 12.5x at 8%); it rises with growth only when
R > ke; it falls year by year as the remaining growth fades -- the multiple compresses with the growth path,
and the bear's multiple is lower than the base's because its growth and margin are lower, not by assumption.
M_just is clipped to [PE_FLOOR, PE_CAP] = [5, 60] (section 4 for the sources).

### 3.5 Today's multiple as an input that converges
Premium at formation: D_0 = min(M_0, PE_INPUT_CAP = 60) - M_just(0, base). (Above 60x normalized trailing
earnings the market is not pricing current earnings; the lane's claim is limited to earnings multiples; the
count of capped rows is reported and the results are broken down by M_0 bucket.)
    M_h(s) = clip( M_just(h, s) + D_0 x 0.5^(h / HL_s), PE_FLOOR, PE_CAP )
The premium (or discount) decays with the SAME half-life as the scenario's excess growth: a bear fades it
fastest, a bull slowest. A negative D_0 (price below the justified multiple) closes at the same speed.

### 3.6 Price per year
    P_h(s) = E_h(s) x M_h(s),   h = 1..5, s in {bear, base, bull}
The base path is the point forecast under test; bear and bull are the band. Output per row: the three paths
with revenue, growth, margin, EPS, M_just, remaining premium, M_h, price; the anchors with their fact
references; lambda and the 5-year base-rate percentile; the flags (clipped run rate, capped M_0, floored
margin, cyclical normalization); spec_version 1 and a sha256 of inputs + outputs.

## 4. Constants and sources (all in `pricepath.py`, documented in place)
| constant | value | source / reason |
|---|---|---|
| STAGE half-life, g_spread, m_spread, conv, ke add-on | forecast.STAGE | engine's documented stage model (Koller et al. growth decay) |
| class medians, horizons, share_at_least | base_rates.CLASSES | Mauboussin, Callahan & Majd, The Base Rate Book (2016), exhibit 4 |
| CPI at formation (May of the formation year, published mid-June, before as_of) 2016-2025 | 239.557, 244.004, 250.792, 255.296, 255.802, 268.383, 291.298, 303.334, 313.175, 320.620 | FRED CPIAUCSL monthly (retrieved 2026-10-21); the 2015 base is base_rates.CPI_2015 = 237.017 (BLS annual average, not seasonally adjusted: the SA/NSA gap is < 0.5%, immaterial at a class edge); a one-band class error moves the median by ~1 point |
| PI | 0.02 | long-run inflation assumption (Fed target); the dated breakeven is not point-in-time data |
| G_RUN_LO/HI | -0.30 / +0.60 | event vs run-rate boundary (judgment, stated) |
| CYC_G_LO/HI, CYC_CONV | -0.05 / +0.25, 3 | cf. forecast.CYCLE_TREND and CYCLE_CONV (wider upper bound: the lane has no fitted-trend lower-bound rule) |
| CEIL base / bull | 0.10 / 0.05 | base-rate ceiling: base no rarer than the class top decile, bull top 5% |
| R_MAX | 0.50 | above 50% ROIC the reinvestment need is already ~0; avoids a 300% ROIC making payout 1 for any growth |
| TAX_STAT | 0.21 | US statutory rate (TCJA) for the after-tax spread |
| M_FLOOR | 0.01 | a P/E path needs positive earnings |
| PE_FLOOR / PE_CAP | 5 / 60 | floor: Mauboussin & Callahan 2014 exhibit 1 (steady-state P/E 1/ke bottomed just over 5x in 1981; long-run average 10.4x) and exhibit 5 (warranted P/E 7.1x-25.7x for growth 4-10%, ROIC 4-24%, 15 years, 8% Ke); Damodaran Jan-2026 industry table: lowest industry current PE 6.4x. Cap: Damodaran Jan-2026 US regression PE = 13.12 + 10.52 Beta + 36.47 gEPS + 8.46 Payout (R2 38.7%) gives 32.8x at 25% expected 5-year EPS growth and 41.9x at 50% (beta 1, no payout); 60x is above what the cross-section pays for the fastest growth analysts forecast |
| PE_INPUT_CAP | 60 | same |
| s clip | +-0.06 | buyback/dilution pace beyond 6% a year is an event |
| N (years after h in the justified multiple), explicit path | 10, 15 | the engine's 10-15 year explicit horizon |
Point-in-time note: the Damodaran regression is the January-2026 fit, so it is used only as a diagnostic
(section 6.6), never in the price. Earlier years' fits exist on his site (MReg{YY}.html) but are not
transcribed; if they are later, the diagnostic becomes point in time and section 9 says so.

## 5. Live-engine additions (NOT tested by this backtest; shown as such)
With dated inputs carrying evidence ids (facts.guidance / estimates, issued_at <= as_of): the next fiscal
year's revenue guidance midpoint replaces the structural year-1 growth, adjusted by the company's own
track record (expectations_track: midpoint x (1 + median past revenue surprise) when >= 4 rows, else the
midpoint); a dated revenue consensus for years 1-2 replaces the run rate for those years and is labelled
consensus; a guided margin replaces m_1. The base-rate fade then starts from the last guided year. Evidenced
backlog (RPO >= one year of revenue) lifts CEIL to {base .25, bull .10}. Every output states which years are
guided / consensus / run-rate / base-rate. The DCF, expectation_gap and redteam gates are unchanged: the
lane adds a row to the table, it does not authorize a price conclusion.

## 6. Evaluation protocol (fixed before the first run)

### 6.1 Runs
`scripts/backtest_pricepath.py runs/universe_2026-10-21 --name large500 --from 2016 --to 2025 --workers 2`
writes one row per (symbol, formation) to `rows.jsonl` (resumable) with the inputs, the three paths and the
flags; `--engine-rows` names the current engine walk-forward rows (scripts/backtest_engine.py output) that
supply base_irr for the engine baseline; `--evaluate-only` rebuilds `summary.json` and `REPORT.md`.

### 6.2 Horizons and formation years
h = 1: formations 2016-2025 (realized June 2017-2026); h = 3: 2016-2023; h = 5: 2016-2021.

### 6.3 Common sample (per horizon)
Rows with ALL of: a lane base path (NI_0 > 0, m_anchor > 0, S_0, Rev_0, P_0 present); a realized price at
t+h; the engine's base_irr for the same (symbol, formation); a trailing 3-year diluted-EPS CAGR (facts
eps_diluted annual series, latest FY and three FYs earlier both > 0). Counts excluded by each condition are
reported. A lane-only sample (lane rows with a realized price) is reported for the lane vs the two
mechanical baselines.

### 6.4 Predictors of actual_h (all prices in t-share units)
- lane_base: P_h(base) (the test subject); lane_bear, lane_bull: the band.
- random_walk: P_0.
- drift_7: P_0 x 1.07^h.
- analyst_const: P_0 x (1 + g_eps)^h, g_eps = trailing 3-year diluted-EPS CAGR clipped [-0.10, +0.30]
  (repA's definition: today's multiple kept, EPS compounding at its trailing rate).
- engine_irr: P_0 x (1 + base_irr - dy_0)^h, dy_0 = TTM dividends paid / (P_0 x S_0) (price-only version of
  the engine's implied return); P_0 x (1 + base_irr)^h is also reported.

### 6.5 Metrics (per horizon; also by formation year, stage, cyclical flag, M_0 bucket, size class)
e = log(pred / actual). bias = median(e) and mean(e); MdALE = median(|e|); within25 = share(|e| <= ln 1.25);
share of predictions above actual; head-to-head share of rows where the lane's |e| is smaller than each
baseline's; band coverage = share of actual in [lane_bear, lane_bull], share below bear, share above bull;
yearly Spearman of pred/P_0 vs R_h (mean, t over formation years) as a secondary rank statistic.

### 6.6 Diagnostics (reported, no effect on the price)
Median lambda and the 5-year base-rate percentile by size class; distribution of D_0 / M_0; the ratio
M_just(0, base) / PE_reg where PE_reg = 13.12 + 10.52 + 36.47 x (5-year base EPS CAGR) + 8.46 x payout_1
(Damodaran January 2026, not point in time); the share of rows with each flag.

### 6.7 Success criteria (all on the raw common sample; the deflated check is robustness, not the verdict)
S1 accuracy: at h = 3 and h = 5 the lane's MdALE is below analyst_const's AND below engine_irr's, and at
   every horizon it is not worse than min(random_walk, drift_7) by more than 0.03.
S2 bias: |median log error| <= 0.10 at h = 1, 3 and 5.
S3 band: at h = 3 and h = 5 the realized price lies within [bear, bull] in >= 50% of rows, and neither tail
   (below bear, above bull) exceeds 35%.
Pass = S1, S2 and S3 all hold -> the lane may appear next to the DCF in the valuation table as the expected
price path, carrying its measured error band (within25 and MdALE by horizon) in the explanation.
Partial (S2 holds, S1 or S3 fails) -> shown only as a draft row labelled with what failed.
Fail (S2 fails) -> recorded in docs; not shown in the table.
No threshold, constant, window or sample rule changes after the results are seen.

### 6.8 One preregistered robustness check
Survivorship deflation (repA's rule): per formation window, realized ratios are rescaled so the sample's
median realized ratio equals SPY's price-only ratio over the same window (runs/backtest/raw/prices/SPY.json);
all metrics are recomputed. Reported next to the raw results; it does not change the verdict.

## 7. Module layout (buildable in one module plus one script)
`fundamental_engine/pricepath.py`: constants (section 4) -> `anchors(fb, an, qa, card, price, rf, erp)` ->
`growth_path`, `margin_path`, `share_path`, `justified_multiple`, `price_path(anch)` (pure, deterministic,
hashed) -> `predictions(row, h)`, `evaluate(rows, prices, engine_rows)` (metrics of 6.5-6.8).
`scripts/backtest_pricepath.py`: the walk-forward runner (multiprocessing, `--workers 2`), report writer.
`tests/test_pricepath.py`: synthetic numbers only -- fade arithmetic and class re-reading; lambda shrinks a
tail path to the ceiling and leaves a modest one alone; steady-state multiple = 1/ke when R = ke, g = 0;
M_just falls with h on a fading path; bear < base < bull prices in every year; premium halves per half-life
and converges to M_just; cyclical normalization lowers E*_0 at a peak margin; evaluation metrics and band
coverage on invented rows; a fact filed after as_of is not used (through the facts layer).

## 8. Reporting
`docs/PRICE_PATH_RESULTS_<date>.md`: the tables of 6.5-6.8, the counts of 6.3, the diagnostics of 6.6, the
verdict against 6.7 in those words, the survivorship and in-sample-design caveats of the engine walk-forward,
and named examples from both tails. Hebrew wording for the table row is written only after the verdict.

## 9. Deviations log (dated; empty at registration)

Pre-run clarifications, 21.10.2026, logged before the first evaluation run (no result had been seen; sections 2-6 unchanged):

1. Section 5, backlog ceiling. The text prints the lifted ceilings as {base .25, bull .10}. CEIL is a MINIMUM share of the
   size class that must have grown at least as fast (share_at_least >= CEIL), so a larger number tightens the constraint;
   the only reading consistent with "lift the ceiling" is a smaller minimum share. `pricepath.CEIL_BACKLOG` = {base .05,
   bull .025} (the base takes the bull's level, the bull half of it). Live-engine input only; the backtest never uses it.
2. Section 3.1, NI_0. `analysis.analyze` keeps a TTM net income only when it was built by the same method as the revenue
   TTM (four quarters vs annual + year to date); when it drops it for that label, the lane reads `facts.ttm(fb,
   'net_income')` for the SAME period end (within facts.SAME_PERIOD_DAYS), which is the same twelve months. Flag
   `ni_ttm_from_facts`, counted in the flag shares. The registered exclusion is for NI_0 <= 0, not for a method label.
3. Section 3.3, bear / bull margin convergence. "Moves linearly from m_1 to the target over CONV years" is read the way
   the engine's own margin path (forecast.py: year t is t / conv of the way) and section 3.1's cyclical rule (starts at
   m_TTM, year h is h / CYC_CONV of the way) count it: m_h(s) = m_1 + (target - m_1) x min(h / CONV, 1), so the bear's
   margin is below the base's from year 1 (an interrupted earlier draft had used (h - 1) / CONV, which left year 1 equal).
4. Section 3.4, the "growth adds value only when R > ke" property. It holds where the payout is unclipped (g <= R): with
   R = ke the forward multiple is 1 / ke for any growth path (trailing (1 + g) / ke). When g > R the registered clip at 0
   means the path grows faster than full reinvestment at R can fund, and the multiple rises with growth even at R = ke;
   the formula is unchanged (R_MAX and the lambda ceiling keep such paths rare). The terminal switch g_T = min(rf, g)
   costs a little value when g > rf (year h+N retains for g, the terminal grows at rf); also as registered.
5. Section 6.1, locations. The saved walk-forward data live outside the worktree (`/home/user/fundamental-engine/runs`,
   git-ignored); the script takes absolute `--prices` / `--rates` paths and the engine rows of the fresh 2026-10-21
   baseline run of the current engine (scratchpad `bt_pre/rows.jsonl`, 3,354 rows with base_irr), read-only.

Run record, 21.10.2026 (after the clarifications above, nothing in sections 2-6 changed afterwards):

- First and only full run: commit 86c8a34 of branch price-path, 490 companies x formations 2016-2025 (4,900 pairs; 701 skipped
  for no price / nothing filed; 3,287 lane rows, 912 excluded for registered reasons, 0 errors). Engine base_irr from the
  fresh 2026-10-21 engine walk-forward (3,354 rows). Verdict **FAIL**: S2 (bias -0.113 / -0.311 / -0.504 at 1 / 3 / 5 years),
  S1 and S3 all fail. Results: docs/PRICE_PATH_RESULTS_2026-10-21.md; numbers: runs/backtest/pricepath/summary.json. No
  threshold, constant, window or sample rule was changed after the results; no re-run. Twenty rows with a share-count or
  revenue unit defect in the shared data layer (M_0 < 1x: BRK-B, FOXA, CMG 2022, TEVA 2016, MKSI 2018, MPLX 2019) were found
  and left in the sample, as the rule requires; they are reported in the results document for the data layer to fix.
- Consequence (6.7): not shown in the valuation table. `pricepath.MEASURED` / `MEASURED_VERDICT` / `TABLE_STATUS` record the
  measured error in every live output. Any changed method is a new spec_version with its own preregistration.
