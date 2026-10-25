# Expected price path lane: results, audit and what the table shows (October 2026)

Status: **spec_version 1 FAILED its preregistered walk-forward; it is not shown in the valuation table.** Version 2
(`docs/prereg_price_path_v2.md`, `pricepath` spec_version 2) was run once on 25.10.2026 and FAILED as well (S2 at 3 / 5 years,
S1; `docs/PRICE_PATH_V2_RESULTS_2026-10-25.md`); it is not shown either. Sections 6-7 below were written before that run.
Sources: preregistration `docs/prereg_price_path.md` (commit a8dc43b, before any code), the single run (commit 86c8a34, 21.10.2026), `docs/PRICE_PATH_RESULTS_2026-10-21.md` (full results),
`runs/backtest/pricepath/summary.json`, the post-run audit (prereg section 9, 21.10.2026) and the read-only diagnostics
`scripts/pricepath_v1_*.py`. Nothing in the method, the constants or the sample rule changed after the results.

## 1. Method, as preregistered (spec 1)

- Universe and dates: 490 of today's largest US operating companies (`runs/universe_2026-10-21`, QUEUE_large500; survivors),
  formations 1 July 2016-2025, as_of 30 June; facts usable only when filed by as_of (`facts.usable`); traded June close; GS10;
  Damodaran's start-of-year implied ERP. No guidance or consensus (none saved point in time): the backtest tests the trailing
  run rate + base-rate fade variant only.
- Growth: run rate = mean(latest FY growth, latest quarter vs a year earlier); cyclicals use the 5-year revenue trend clipped to
  [-5%, +25%]. Faded to the Base Rate Book median for the revenue size class (re-read yearly), stage half-life (2-3 years),
  bear/bull = -/+ the stage spread in year 1 with half-life x0.8 / x1.3; base no rarer than the class top decile.
- Margin: TTM net margin held (cyclicals: moving to the 10-year median over 3 years); bear -> anchor minus the after-tax stage
  spread, bull -> anchor + 0.75 spread, over the stage convergence years; 1% floor. The bear changes the business.
- Multiple: a justified trailing P/E from the scenario's own growth path, ROIC-implied payout and cost of equity (1/ke with no
  value creation; rises with growth only when ROIC > ke; falls as growth fades), clipped to [5x, 60x]. Today's multiple on
  normalized earnings enters only as a premium D_0 = min(M_0, 60) - M_just(0) that halves at the scenario's half-life.
- Price_h = EPS_h x multiple_h, h = 1..5. Pass needs S1 (accuracy vs baselines), S2 (|median log error| <= 0.10 at 1/3/5
  years) and S3 (band coverage); FAIL (S2 fails) means recorded in docs, not shown.

## 2. Results against the baselines (common sample; e = log(predicted / realized price))

| h | predictor | n | median e | MdALE | within +-25% |
|---|---|---|---|---|---|
| 1 | **lane base** | 2,600 | **-0.113** | **0.213** | **53%** |
| 1 | today's price | | -0.120 | 0.195 | 56% |
| 1 | price x 1.07^h | | -0.053 | 0.173 | 61% |
| 1 | analyst-style (today's P/E, EPS at trailing 3y CAGR) | | -0.030 | 0.190 | 57% |
| 1 | engine IRR path (P_0 x (1 + IRR - dividend yield)^h) | | -0.065 | 0.175 | 60% |
| 3 | **lane base** | 2,023 | **-0.311** | **0.399** | **29%** |
| 3 | today's price | | -0.311 | 0.362 | 31% |
| 3 | price x 1.07^h | | -0.108 | 0.278 | 43% |
| 3 | analyst-style | | -0.013 | 0.374 | 32% |
| 3 | engine IRR path | | -0.163 | 0.286 | 41% |
| 5 | **lane base** | 1,435 | **-0.504** | **0.588** | **18%** |
| 5 | today's price | | -0.501 | 0.557 | 20% |
| 5 | price x 1.07^h | | -0.162 | 0.363 | 32% |
| 5 | analyst-style | | -0.023 | 0.577 | 21% |
| 5 | engine IRR path | | -0.270 | 0.408 | 29% |

All ten registered checks fail. S2: bias -0.11 / -0.31 / -0.50. S1: worse than analyst-style and the engine IRR path at 3 and
5 years and 0.04 / 0.12 / 0.22 worse than the best mechanical baseline. S3: inside the band 53.8% / 40.1% at 3 / 5 years with
39.6% / 52.2% above the bull. Head to head the lane's error is the smaller in at most 49% of rows against any baseline.
Lane-only sample (3,287 / 2,514 / 1,791 rows): bias -0.115 / -0.330 / -0.528, same picture. Recomputed independently
(`scripts/pricepath_v1_recompute.py`, own price parsing, no engine imports): identical to the third decimal.

Band coverage (realized inside / below bear / above bull): 1y 67.3 / 6.8 / 25.9%; 2y 60.8 / 5.8 / 33.5%; 3y 53.8 / 6.6 /
39.6%; 4y 47.2 / 7.8 / 45.0%; 5y 40.1 / 7.7 / 52.2% (horizons 2 and 4 reported, not registered). The miss is one-sided: the
bull path is near-unbiased at 5 years (-0.03); the bear sits below almost every survivor (bias -0.52 / -0.89 / -1.12).

By formation year, 3 years (lane / today's price / x1.07 / analyst / engine IRR): 2016 -0.48/-0.45/-0.24/-0.23/-0.28; 2017
-0.29/-0.28/-0.07/-0.07/-0.14; 2018 -0.44/-0.46/-0.26/-0.15/-0.28; 2019 -0.30/-0.24/-0.03/+0.12/-0.10; 2020 -0.33/-0.34/-0.13/
+0.01/-0.20; 2021 -0.20/-0.19/+0.01/+0.10/-0.05; 2022 -0.17/-0.35/-0.15/-0.02/-0.17; 2023 -0.35/-0.31/-0.10/-0.01/-0.14. The
lane tracks today's price in every window except 2022; at 1 year it is negative in 9 of 10 years (2021 +0.08).

By type (lane bias 1y / 3y / 5y; n at 3y): mature -0.09/-0.28/-0.45 (1,067), maturing -0.12/-0.32/-0.51 (624), growth
-0.17/-0.41/-0.82 (262), decline -0.19/-0.38/-0.61 (69). Structural -0.12/-0.29/-0.44 (1,081), cyclical -0.10/-0.35/-0.58
(942). Today's multiple: < 10x +0.13/+0.31/+0.59 (91), 10-15x -0.05/-0.01/-0.20 (193), 15-20x -0.10/-0.20/-0.33 (310), 20-30x
-0.11/-0.29/-0.44 (676), 30-60x -0.13/-0.44/-0.69 (570), > 60x capped -0.46/-1.01/-1.41 (183). Size: > $25bn -0.05/-0.14 to
-0.15/-0.26 to -0.32; $1.25-12bn -0.34 to -0.40 at 3 years; smaller classes worse (n small). Secondary, not the verdict: the
lane's ratio to today's price RANKS 3- and 5-year outcomes (mean yearly Spearman 0.135, t 5.0, 8 of 8 years positive; 0.148
at 5 years), an ordering signal, not a level. SPY-deflated (survivorship check) every bias moves more negative.

## 3. Why it fails (ex-post attribution, diagnostic only)

Median base path (3,281 rows): EPS x1.38 in five years while the multiple falls from 26.2x toward a 16.8x justified level (x0.72),
so the five-year price is today's price (x1.00) against a realized median of x1.65. Exact split of each row's log error into
revenue, margin, share-count and multiple terms, the lane's own rows at t + h as realized fundamentals (rows with NI > 0 at
t + h: n 2,201 / 1,639 / 1,108; their bias -0.113 / -0.323 / -0.547, close to the full sample):

| term (median) | 1y | 3y | 5y | reading |
|---|---|---|---|---|
| total | -0.11 | -0.32 | -0.55 | |
| revenue | +0.00 | -0.02 | -0.08 | right on median, wrong in both tails: run rate < 0 lane x0.97 vs realized x1.17 (3y); 20-30% x1.66 vs x1.35 |
| margin | -0.06 | -0.17 | -0.24 | held at the anchor (10.4%) while the median survivor's TTM margin rose 11.1% -> 12.3% (3y); cyclicals -0.33 / -0.41 (the mid-cycle normalization cut the base and the cycle did not turn), structural -0.08 / -0.13 |
| shares | -0.00 | -0.01 | -0.01 | immaterial |
| multiple | -0.07 | -0.10 | -0.22 | the Gordon target (13-23x by growth bucket) against a market that paid 21-40x by the same buckets; median trailing P/E 23.6x at formation, 24.5x three years later |

The market did mean-revert extreme multiples at about the lane's speed (above 60x: 70.6x -> 43.9x in three years, 45% of the
log gap to the median; below 10x: 7.8x -> 14.7x, 55%), so the target, not the half-life, was wrong. Below 10x the multiple rose
while earnings fell (margin term +0.27): the low multiple was pricing a decline the run rate could not see.

## 4. Audit findings and what was done

| finding | severity | action |
|---|---|---|
| Point in time: facts via `facts.usable`, 2026 listing market cap rejected, Damodaran 2026 regression never in a price, engine rows complete before the lane ran | no leakage found | none needed |
| Today's industry / sector / SIC labels drive the cyclical flag for every formation; CPI_MAY is the 2026 vintage; The Base Rate Book (Sep 2016) postdates the July 2016 formation; GS10 June average published early July | low | disclosed in prereg section 9 and carried into v2 section 2 (no point-in-time source exists) |
| 20 rows with M_0 < 1x from shared data-layer unit defects (BRK-B class count, FOXA count 1, CMG 2022, TEVA 2016, MKSI 2018, MPLX 2019); P_0 in Yahoo-split units vs a filing-dated cover count | low (medians unaffected) | kept in the sample as registered; reported for dataqa / sec_frames, not fixed here |
| Scenario half-life rule inverts for a negative premium (22.5% of rows) or negative excess growth: bear multiple >= base in some year in 16.9% of rows, bear price >= base in 1.6%; no ordering clamp | medium, registered | pinned by `tests/test_pricepath.py` Spec1AuditTests; fixed by construction only in v2 |
| Bear margin spread is absolute: the 1% floor binds in 19.1% of rows (593 of 628 with a margin < 5%) | medium, registered | pinned by a test; v2 uses a relative shock bounded by the company's own history |
| Above 60x (11% of rows) the premium decays from the 60x cap, a constant, not from the price | low, registered | reported by bucket; v2 clips the log premium at 3x the class median instead |
| Results doc printed the 2022 3-year random-walk bias as -0.36 (it is -0.355) | typo | corrected to -0.35 |

Also recorded, changing no price or output hash: `pricepath.MEASURED_BASELINES` (the baselines' measured error, engine_irr from
the engine at ff36ede) and `pricepath.irr_path_band_he()` (section 6). The interrupted second run (3,730 rows) matched the first
row for row (input and output hashes), so the lane is deterministic; it was not resumed.

## 5. Limitations

- Survivorship: today's survivors only; delisted names are absent, so every realized price is a survivor's. The SPY-deflated
  check makes the lane worse, not better, so survivorship does not explain the failure, but it flatters any future pass.
- No guidance or consensus in the backtest; the live engine's guided variant (prereg section 5) is untested.
- Regime: one decade (2016-2025) of rising margins and P/E near 22-29x; overlapping 3- and 5-year windows from 8 and 6
  formation years, so year-level statistics have few independent observations.
- In-sample design: stage parameters, P/E floor and cap and the method were written in 2026 with the decade known.
- The attribution is ex post and conditions on positive earnings at t + h; it locates the error, it does not validate a fix.
- The engine IRR baseline measures the engine at ff36ede; later engine changes are unmeasured.

## 6. Recommended presentation

Now (verdict FAIL): **no price-path rows in the table, not even as a draft.** The table keeps what it shows: today's price,
fair value at the required return and the gap, the bad / good value range, the expected annual return at today's price against
the market's ("תשואה שנתית צפויה במחיר של היום"), the conditional five-year value and the yearly bad / likely / good VALUE table
(value at the required return, labelled as value). The only price path with a measured error is the expected-return row read as
a path (price rising at the base IRR less the dividend yield). Recommended, not yet wired into `render.hebrew_table`: one note
under that row, `pricepath.irr_path_band_he()`:

> במבחן על 2016–2025 (חברות גדולות ששרדו; מחיר בלבד, בלי דיבידנדים) מחיר שעולה כל שנה בקצב התשואה הצפויה פחות הדיבידנד היה
> בטווח של ±25% מהמחיר שהתממש ב־60% / 41% / 29% מהמקרים אחרי 1 / 3 / 5 שנים, ובחציון נמוך ממנו בכ־6% / 15% / 24%. התשואה
> הצפויה היא הערכה מותנית, לא תחזית למחיר המניה.

If v2 later PASSES (prereg v2 section 9, wording fixed before its run): one main-table row "מחיר משוער בעוד 5 שנים (לא שווי)
| רע X / סביר Y / טוב Z", a yearly block "מחיר משוער בכל שנה, לפי תרחיש (הערכה מותנית, לא שווי)" with bad / likely / good for
each anniversary through the fifth, and one band sentence filled from the v2 run ("המחיר ה'סביר' היה בטווח של ±25% מהמחיר
שהתממש ב־A% / B% / C% מהמקרים ... והמחיר שהתממש היה בין 'רע' ל'טוב' ב־D% / E% מהמקרים אחרי 3 / 5 שנים"). PARTIAL: the same
rows prefixed "טיוטה (נכשל במבחן: ...)". FAIL: as now.

## 7. Version 2 (registered before its run; run 25.10.2026: FAIL, see the status above): what changes and why

Each change answers one term of section 3; nothing was tried on the data. Full text and success criteria:
`docs/prereg_price_path_v2.md`.
- Multiple target: the market's own point-in-time trailing P/E by growth x margin class (p25 / median / p75 of the universe
  at the same as_of, >= 20 rows per cell with fallbacks) instead of the Gordon level; today's multiple enters as a log premium
  over the class median (clipped to 1/3-3x) that halves at the stage half-life; the target falls as the growth path moves down
  the bucket ladder, so the multiple still compresses with growth. The Gordon multiple stays as a diagnostic.
- Margin: the TTM margin held in the base for every company (no mid-cycle cut); the cycle and the business shocks move to the
  band: bear to the worse of the company's own 10-year low and the stage spread, never below half today's margin; bull
  symmetric.
- Growth: run rate = mean of the latest FY, latest quarter and 3-year CAGR; bear / bull fade to the size class p25 / p75 at the
  same half-life (the scenario speed rule is dropped).
- Ordering bear <= base <= bull in growth, margin, multiple and price every year, asserted (a violation is an error).
- Criteria: v1's S1 (now also beating lane_v1) and S2; S3 with tails <= 30%; new S4: at 3 years the revenue, margin and
  multiple attribution terms each within +-0.10, so a pass cannot come from offsetting errors. Expectation written before the
  run: the structural margin term (-0.08 / -0.13) may make it fail S2 at 5 years; the criteria are not softened for that.

## Reproduction

Lane run and evaluation: `docs/PRICE_PATH_RESULTS_2026-10-21.md` (Reproduction). Diagnostics on the saved rows (not in Git,
29 MB; regenerate with the run command, deterministic):
`python -I scripts/pricepath_v1_recompute.py <rows.jsonl> <engine rows.jsonl> <prices dir>`, the same arguments for
`pricepath_v1_attribution.py`; `pricepath_v1_ordering.py <rows.jsonl>`; `pricepath_v1_class_multiples.py <rows.jsonl>`.
