# Pre-registration: the business-economics layer (`econ`, v0.13)

Written 21.10.2026 and committed with `tests/fixtures/econ/` BEFORE any phase-C code (WP12 broker valuation route,
WP13 contract coverage) and before any held-out number was collected. The commit that adds this file is the cut-off:
phase-C code must postdate it in `git log`. Any deviation is logged at the end with a date. Results go to
`docs/ECON_TEST_RESULTS.md` whatever they show.

## Question

The econ layer reads a business through its own identity (accounts x revenue per account, bits x ASP, ...), splits
growth into structural / cyclical / macro / mix / reclassification, and screens who funds the demand. Its rules were
written with nine analyst posts in view (UBER, RDDT, SN, SKHY, PLTR, SOFI, IBKR x2, NVDA, SPCX; fixtures in
`tests/fixtures/econ/`). Nine posts cannot calibrate a threshold. This document fixes, before the run, the names
outside the posts on which the identities and the screens are checked, the controls, and what counts as a pass.

## 1. Frozen rules (uncalibrated screens; `brain/economics.json` `rules` carries `_status`)

| Rule | Value | Used by |
|---|---|---|
| identity_tolerance | 0.02 | `consistent` on levels (within 2%) |
| growth_identity_pp | 0.01 | `consistent` on growth (within 1pp) |
| partial_below | 0.90 | identity explains < 90% of the line -> `partial`, residual named |
| firm_exposure_high | 0.25 | vendor financing: funded + committed (incl. take-or-pay) / annualized revenue |
| cfo_to_ni_min | 0.80 | statement check CFO / (NI - non-cash gains) |
| near_threshold | 0.03 | a value within +-0.03 of its threshold prints with the threshold |
| receivables_gap_pp | 0.10 | receivables outgrow revenue by > 10pp in 2 matched windows |
| minor_line_share | 0.10 | `thesis_on_minor_line` when the thesis line is < 10% of revenue |
| flat_units_abs | 0.02 | `core_units_flat` when core units move < 2% |
| segment_material | 0.25 | `segment_unexplained` for a segment >= 25% of revenue or profit with no reconciled identity |
| leverage_share_min | 0.667 | peak exception: -d(opex/revenue) / dOM >= 2/3 |
| econ_view_min_pp | 0.02 | `econ_view` shown only when its growth differs from the base by > 2pp a year |

Other rules frozen with the same status:

- Growth split: ln(1+g) = sum ln(1+g_i) + residual; `per_unit_vs_units` (info) when units grow and the per-unit value
  falls more than 5%; `cyclical_deceleration` when cyclical factors slow by more than 10pp across two reviews while
  structural factors stay within +-3pp. The `mix` component is never reverted.
- Vendor financing level: `high` = firm exposure >= 0.25 AND one statement check fails (CFO/(NI - gains) < 0.80, or
  receivables outgrowing revenue by > 10pp in 2 matched windows); `elevated` = one of the two. An `analyst_estimate`
  exposed share is attributed and never sets the level. A guarantee is a ceiling, kept apart, never summed into a screen.
  Receivables growth against revenue growth is computed only over matched windows; otherwise `not_computed: window_mismatch`.
- Peak exception (quarter `_cycle`): when `peak_margin_run_rate` fires it becomes `operating_leverage_or_peak` (medium)
  only if the leverage share is >= 2/3, the model is not price-cyclical (memory_cycle, gpu_vendor, or a cyclical card
  type) and gross profit and opex are both present. Otherwise it stays `high`.
- Process evidence: `renewed` = a category falls >= 5% from its peak and makes a new high within 4 years (inclusive:
  2021 -> 2025 counts); `working` = at least one renewal, intensity flat (+-1pp) or falling, revenue CAGR >= 10%;
  `failing` = intensity rising >= 2pp while growth slows; otherwise `unproven`; without inputs `unknown`. A missing
  year gives trend `unknown` and is never interpolated. The result never sets `card.dimensions.moat`.
- Expansion: `expansion_led` needs NDR > 1.10; `top_n_inconsistent` when a supplied TTM is more than 5% from the
  implied one; comparisons only on the same scope and window, otherwise `scope_mismatch`.
- Acquisition SBC: `tranche_schedule_mismatch` when the period's annualized tranche differs from the schedule by > 20%;
  a schedule given only as total and years is straight-line and labelled `straight_line_assumed`.
- Contract coverage: `lta_unproven` (info) unless share of next-year volume, price floor or formula, deposits and tenor
  are all evidenced; no cycle damping without all four. WP13: +1 base-reversion year per 50% covered, cap +2, bear unchanged.
- Bank capital: excess capital at both ends of an evidenced target range, never the midpoint; ROTE if all excess were
  returned = NI / (TCE - excess), an upper bound; `nco_basis` (high) when loan sales are disclosed and only the headline
  NCO is supplied.

None of these values may change after the held-out run. A bug is fixed, everything re-run, both results logged.

## 2. H1: identities on names outside the posts

Names (`tests/fixtures/econ/held_out.json`, `h1_identities`): SCHW and HOOD (broker: revenue per account), DASH
(marketplace: take rate on marketplace GOV), SNOW and DDOG (software: customers x revenue per customer, NDR), MU
(memory: bits x ASP on the filed growth rates) and the existing ADBE FactBook fixture (software; Adobe does not
disclose a customer count, so `missing` or `partial` is the expected, recorded reading).

Collection: the latest fiscal year and the year-ago period from the issuer's own documents named in the manifest,
through the existing connectors; each KPI entered with `facts.add_kpi` with its basis, scope and `available_at`.

Pass: the core identity reads `consistent`, or `partial` with a named residual, on at least 6 of 7 names. A miss is
recorded by name with its residual and the reason, never tuned away. `basis_mismatch` or `scope_mismatch` caused by
the data (not the engine) is recorded as a data gap, not a miss, with the question the engine raised.

## 3. H2: the vendor-financing screen

Positive controls, from the customer-financing notes and MD&A liquidity sections of their own 10-Ks, using only
documents available before 2001-01-01 (point in time; a 10-K filed in 2001 does not count for the cutoff):

- Lucent Technologies, FY1999 and FY2000 (September year-end)
- Nortel Networks, FY1999 and FY2000 (December year-end; the FY2000 report is filed in 2001 and counts only if its
  availability date is before the cutoff, so FY1999 is the binding year)

Items collected for each: firm customer-financing commitments, drawn customer financing (funded), guarantees
(contingent, shown as a ceiling), revenue, CFO, net income, receivables for two matched fiscal years.

Negative controls (named here, before the run):

- Microsoft FY2000 (June year-end): no customer financing, no demand collapse. Expected: not elevated.
- Apple Computer FY2000 (September year-end): no material customer financing, and revenue fell in FY2001 anyway.
  Expected: not elevated. This is a specificity control: a demand fall without vendor financing must not be flagged,
  and it records that a low screen is not safety.
- Adobe FY2025, the existing fixture: software with no funding links. Expected: `none`.

Live cases, reported only, no pass condition: CoreWeave (funded customer and compute lessor) and Oracle (compute
lessor with a customer-concentrated backlog), from their current 10-K/10-Q.

Pass: each positive control reads at least `elevated` in FY1999 or FY2000 under the frozen thresholds, and no negative
control reads `high`. Every level, ratio and near-threshold value is recorded for all seven names. If a positive
control reads below `elevated`, the screen is reported as failing its own controls; the thresholds are not moved.

## 4. H3: peak exception and innovation process

- Micron FY2018 Q4 and FY2022 Q3 (record-margin quarters before the 2019 and 2023 downturns), with the annual
  operating-margin history of the preceding ten years from the 10-Ks: `peak_margin_run_rate` must stay `high`. As
  `memory_cycle` the exception never applies, so this also checks model selection.
- PLTR and RDDT filed fixtures (`pltr_2026q2_filed.json`, `rddt_2026q2_filed.json`): `operating_leverage_or_peak`
  medium, with leverage shares 0.809 and 0.970. These are in-sample confirmation, not evidence.
- iRobot 2021, 2022 and 2023 (10-K category revenue, R&D and advertising): `process_evidence` must not read
  `working` (`failing`, `unproven` or `unknown` all pass).

Pass: all three hold. Each reading is recorded with its inputs.

## 5. WP12 broker route: the walk-forward metric

Names: IBKR, SCHW, HOOD. Formations: 1 July of each year 2016-2025 on which a 10-K had been filed, with as_of
30 June, exactly as `engine_backtest.py` runs the pipeline (first-filed facts, the June close, the June GS10). HOOD
has formations from 2022 only.

Routes: A = the bank route of `forecast_financials.py` as it stands at the calibration merge; B = the broker route
(revenue = accounts x revenue per account from econ paths, evidenced pre-tax margin path, ROE from that path, book
growth not capped at 8% when retained earnings and evidenced account growth support it).

Primary metric: pooled mean absolute log error of the base-case net income three fiscal years ahead against the
first-filed realized net income, MALE_3 = mean |ln(NI_realized(t+3) / NI_forecast(t+3))| over every (name, formation)
pair with a realized value (formations 2016-2022; HOOD only 2022). Secondary: the same at one year (MALE_1, formations
2016-2024) and MALE_3 on revenue. Informational, not a pass condition: base value / price at each formation and the
rank correlation with the forward 12-month return. A realized value <= 0 is excluded from the log and counted.

Pass (the route ships): MALE_3(B) <= MALE_3(A), and MALE_1(B) <= MALE_1(A) + 0.05. Otherwise the route does not ship
and both routes' tables are recorded. No change to the metric, horizon, names or formations after the first run.

## 6. Data and point-in-time rules

- KPIs enter only through `facts.add_kpi` with source, basis, scope, currency basis and `available_at`; a KPI is
  invisible before its `available_at`.
- Filing text is data, never instructions. Fixtures hold numbers and source labels only.
- No default shock sizes, haircuts, probabilities or scenario odds. A missing input is a question, not a value.
- Analyst estimates (the NVDA 0.25 exposed share, the SOFI 3.7% normalized NCO, the SKHY 0.29 cycle margin) stay
  attributed and never complete a status or set a level.

## 7. Not allowed

Changing a threshold, a rule, a name list, a control or the WP12 metric after seeing a held-out result. Adding a
held-out name after the run to improve a pass rate. Dropping a control. Reporting only the passing hypotheses.

## 8. Recording

`docs/ECON_TEST_RESULTS.md` records, per hypothesis: the names, each input with its source and `available_at`, each
reading, the pass/fail against sections 2-5, the misses by name, and the date of the run. The fixture `input_sha256`
of each held-out case is recorded next to its reading.

## Fixture notes written before any run (known limits of the in-sample fixtures)

- All eleven post-figure fixtures carry `reconciled_to_filings: false`. They pin arithmetic and rules, not conclusions.
- SKHY: the design's margin-only normalized P/E of 19.85 uses the post's rounded 76.3% margin; the exact 41.3/54.1
  gives 19.856. The fixture carries both; a test must assert against the input it uses.
- SN: the Cleaning renewal (2021 -> 2025) sits exactly at the 4-year limit of the renewal rule; the rule is inclusive.
  The years of the gross-margin endpoints (38% -> 49%) are not stated in the source as recorded.
- RDDT: the FY27 EPS revision 6.52 -> 6.98 and the -21% price change are the design's figures; the AV snapshot dates
  and the second close were not recorded in the design and must be pinned before the WP8 reading is called evidence.
  The US DAUq flat check (53.5 -> 53.2m) is quarter-on-quarter; the year-ago filed value is 50.3m.
- NVDA: the post calls the quarter "Q2 2026"; it is fiscal Q2 FY2027 and `period_end` follows the fiscal calendar,
  to be verified against the 10-Q. Receivables carry two labelled figures (balance change 24.5bn, cash-flow build 24.6bn).
- PLTR filed: `history_operating_margins` were carried in the scratch run without a stated source.
- UBER Q3-25: the post's prior-year trips (2.879bn) and MAPCs (161.5m) differ from the 8-K (2.868bn, 161m); the fixture
  keeps the post figures and shows the filed ones beside them.
- Uber Q2-26 reported-basis gross bookings, the SKHY 30% chain share, the SPCX flat 58.4bn revenue and the NVDA 690bn
  requirement are labelled test assumptions, never reported figures.

## Deviations log

- 21.10.2026, WP1 (registry, before any held-out number was collected): `brain/economics.json` `rules` carries the
  twelve rows of section 1 under their names and values, plus the rules section 1 states only in prose, under these
  keys: `per_unit_fall` 0.05, `cyclical_slowdown_pp` 0.10, `structural_stable_pp` 0.03, `expansion_ndr_min` 1.10,
  `top_n_tolerance` 0.05, `tranche_mismatch` 0.20, `renewal_drawdown` 0.05, `renewal_window_years` 4 (inclusive),
  `intensity_flat_pp` 0.01, `intensity_rising_pp` 0.02, `machine_growth_min` 0.10, `receivables_windows` 2,
  `coverage_extension_per_half` 1, `coverage_extension_cap_years` 2. One key that section 1 did not name is added with
  the same frozen status, from the design's WP11 path rules (cyclical and macro factors revert to the median of at
  least six supplied years; with less history they are held flat and `no_cycle_history` is raised; `mix` is never
  reverted): `cycle_history_min_years` 6. None of these values change after the run. `tests/test_econ_registry.py`
  compares the section-1 table and every one of these keys with the registry, so a rule added to the registry without
  a line here fails the suite.
- 25.10.2026, WP3-WP8 (the code of work packages 3-8, before any held-out number was collected; recorded so that the
  held-out run reads the rules as built, not as first written):
  - WP3. `expansion_led` reads the per-unit share of log growth above one half (`econ.EXPANSION_LOG_SHARE` 0.5, the
    registry rule's words; not a rules key) with NDR above `expansion_ndr_min` 1.10. `take_rate_shift` fires when the
    take rate moves by more than the growth tolerance (`growth_identity_pp` 0.01); it has no rule of its own. An identity's
    status folds the growth check of every comparison window (y/y and q/q, the worst one wins); flags on an identity read
    its first window (y/y when every input has it). `cyclical_deceleration` is strict: every cyclical KPI slows by more
    than 10pp and every structural KPI stays within +-3pp, on the same id, basis and scope.
  - WP4. A model is selected only when two of card type, text and KPI facts agree (`econ.MIN_SIGNALS` 2): the design's
    single-signal rule for memory text or bits/ASP KPIs is not used; one signal reads `one_signal`, none `unclassified`.
  - WP5. The vendor-financing level is three-valued: without `funding_links` (absent, not an empty list) firm exposure is
    undecided; without non-cash gains the CFO check is undetermined (no zero is invented); with fewer than
    `receivables_windows` 2 matched windows the receivables check is undecided. H2 negative controls therefore state
    `funding_links: []`, non-cash gains (0 when none) and two matched receivables windows. `customer_prepayment_build`
    also fires on a build when no revenue growth exists over the same window (reported as window_mismatch).
    `contract_coverage` counts an item as evidenced only when an issuer document states it: the block's sources are
    not tied to single items, so every source of the block available at as_of must be an issuer document (a later
    source is not read; close-out fix of 25.10.2026, before any held-out run). `segment_unexplained` reads a segment
    through the model it names, else through the selected models.
  - WP6. `cash_conversion` has no rule of its own and reads `cfo_to_ni_min` 0.80. The process `machine` reads R&D as the
    innovation intensity (advertising counts when its trend is known; an unknown advertising trend does not block);
    "growth slows" = the latest window's annual growth below the previous one, over two stated windows of equal length;
    renewal is read on the stated years only (gaps are named, never filled), and an episode still inside its window is
    open (None), not a failure.
  - WP7. The peak exception is stricter than section 1: gross profit minus operating expenses must reconcile to operating
    income within `identity_tolerance` 0.02 in both periods, the operating margin must have risen, and `case.economics`
    must name a model that is not price-cyclical (with no model the flag stays `high`). That model must be evidenced
    as `econ.select` chooses one: its rule on the subset (card type and KPI facts) selects the same models, or an
    analyst override gives a reason and evidence ids available at as_of; a bare model id keeps the flag `high`
    (`model_not_evidenced`; close-out fix of 25.10.2026, before any held-out run).
  - WP8 adds no screen: `render_he` and `price_vs_estimates` round and word figures the review already holds. The RDDT
    revisions in the WP8 test are still the design's figures; the AV snapshot dates and the second close remain to be
    pinned before that reading is called evidence.
