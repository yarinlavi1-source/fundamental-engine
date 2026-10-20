# Discovery — emerging opportunities (v0.5) and the universe screen

The discovery track is designed to reduce premature rejection of emerging companies
WITHOUT presenting early hypotheses as buy signals. It does not demand earnings,
FCF, low multiples or a completed valuation. Price/funding models remain independent.

## Workflow

1. `frontier_plan({request:{theme,as_of}})` — investigate a theme, not just tickers.
2. Read frontier_discovery/frontier_research; use the client's existing connections.
3. Build and run `discovery_scan({case})`; CLI: `fundamental discover INPUT.json`.
4. Test the weakest causal edge and next commercial milestone. Track comparable
   operating metrics. Run `discovery_compare({before,after})` on later evidence.
5. Underwrite business/funding/annual valuation; do not wait for mature EPS to begin.

## Case contract

Required: discovery_version=1, company_id, ticker, as_of, sources, nodes, edges,
observations, opportunities. Optional: trajectories, freshness_days (default 365),
is_demo. Monetary valuations and suitability are outside this case.

Sources: id/title/url/kind/origin_id/published_at/available_at. Synthetic sources
require is_demo=true. Future sources are excluded; duplicate IDs are rejected.
Origin IDs identify original provenance, not number of syndicated articles. Their
count is descriptive, never an evidence score or independence guarantee.

Nodes: id, description, kind=driver/constraint/solution/beneficiary/substitute.
Edges: id, from, to, mechanism, condition, falsifier, kind=observed/hypothesis,
reviewed (boolean), lag_months, as_of, source_ids. Lag is an analyst assumption,
not an automatically predicted bottleneck date. Each opportunity uses a contiguous
noncyclic edge path from driver to beneficiary. Alternative paths may share nodes.

Observations: id, role, statement, location, direction=supports/opposes,
kind=reported/guidance/opinion, reviewed, observed_at, reviewed_at, source_ids.
Roles: technical, pilot, design_win, paid_adoption, production, repeat, scaling,
unit_economics, value_capture, demand, supply, substitute, funding. Only dated,
reviewed reported observations promote evidenced stage. Guidance/opinions remain
hypothesis material. Review labels are supplied by the analyst, not verified by AI
arithmetic. Stale observations are exposed; change freshness explicitly with a
reason when an older fact is durable, instead of claiming new progress.

Opportunity: id, problem, payer, product, capture_mechanism, substitutes,
supply_response, why_now, falsifier, path (edge IDs), observation_ids, milestones.
Nonempty descriptions may explicitly say unknown plus the concrete verification
question; do not invent a budget owner to satisfy the input contract.
Milestone: due_at, test, success, failure, action_if_success, action_if_failure.

Optional pressure_cases: case=low/base/high; kind=observed/scenario; date, unit,
scope, demand, supply, rationale, source_ids. Both quantities must already be
normalized to matching units/scope by the analyst. Demand/supply and shortfall are
arithmetic checks; a future-date observed gap is rejected. Supply=0 returns a null
ratio rather than infinite confidence. No shortage probability is inferred.

Optional trajectories: metric, definition, unit, scope, better=higher/lower,
points with date/value/period_days/unit/scope/source_ids. Definitions are shared by
the series; scope/units mismatch is rejected; materially unequal period lengths
cannot produce an improvement label. Changes are descriptive, not causation or
acceleration estimates. Seasonality and comparability still need analyst review.

## Output

Research lanes: explore; monitor_progress; underwrite_early_growth;
resolve_counterevidence; repair_evidence_path. All remain research opportunities.
Underwrite_early_growth requires paid/production/repeat/scaling evidence plus
value-capture support; it does not require profit. A design win alone is not paid
adoption. Counterevidence is kept alongside supportive observations. Current path
availability and hypothetical links remain explicit. Unit economics require more
than a standalone pilot margin to be described as demonstrated at scale.

Output includes dated source/observation references, pressure cases, next questions,
milestones, trajectories, input hash and exclusions. No watchlist monitoring job is
scheduled automatically. CLI saves immutable-by-input JSON under runs/discovery/.

`research_review` accepts discovery_input and checks issuer/date/source identity
against the dossier. Existing priced-conclusion gates remain unchanged; failure of
a pricing gate does not erase the separate discovery result.

## Universe screen (`screen.py`) — research agenda before full analysis

`screen.scan(rows, options)` turns a broad universe into lens candidates, near misses, a shortlist
and a stated exclusion for every other name. It never values, recommends, sizes or trades; every
shortlisted name goes through `pipeline.run`. CLI `fundamental screen ROWS.json`; MCP
`screen_universe` / `screen_changes`.

Rows come from adapters with a data grade: `rows_from_fmp_screen` (vendor TTM, `screening`),
`rows_from_sec_frames` (SEC XBRL frames, current screens only), `rows_from_pit` (filing-dated
companyfacts) and `rows_from_factbooks` (`factbook`, or `verified` when dataqa is ok). Missing debt
leaves net debt, EV and ROIC unknown (never net cash). A vendor interest coverage of exactly 0 with a
positive operating margin is a placeholder, read as unknown. Metric values of the wrong type become
unknown and are named in the row errors; one bad value never aborts the scan. Duplicate rows resolve
on status, grade, latest as_of, fewer vendor errors and more metrics, never on input order.

Eight dimensions are reported separately, each with inputs, anchors and percentiles:

- business_quality: strong needs a strong ROIC or margin level (stability and cash conversion alone
  are adequate); a cash-rich company's ROIC on a tiny operating base is not counted.
- growth_quality: a normal fade from a high base is neutral; consensus is compared with the lower of
  latest growth and the 3y CAGR, and "far below" means more than 10 points and more than half of it;
  a consensus decline weighs against; buybacks do not offset shrinking revenue.
- expected_return_proxy: owner FCF yield on equity, (FCF - SBC) / market cap (FCF is after interest,
  so its denominator is the equity, not EV), plus bounded sustainable growth minus excess dilution.
  Unknown when owner FCF <= 0. Rough; it orders within a lens and is never a target.
- priced_in: growth implied by EV / after-tax operating profit (21% tax convention) under a standard
  fade model; market cap / owner FCF when operating profit is thin or unknown, and as a
  `cross_check` otherwise. The implied growth is held 5 years (the calibrated priced-for-perfection
  convention, >= 30%); a consensus reference covers 2 years, so the multiple that consensus held 2
  years then fading justifies is converted to its 5-year equivalent before the gap is read
  (`justified_multiple`, `reference_equivalent_growth`). A cyclical row without consensus is compared
  with long-run (terminal) growth, not a boom-era CAGR. When it cannot be read (owner FCF <= 0, no
  market value), `price_check` carries a crude labelled fallback: EV/sales per Rule-of-40 point,
  EV/sales, EV <= 0, or P/E. A demanding fallback raises `price_demanding`.
- financial_strength, valuation, risk (high-severity flags, accounting and cash-conversion flags,
  low data flags such as `leverage_unknown`, `dilution_unknown`, `cyclical_unchecked`; Altman alone
  is not leverage data) and data_uncertainty (a vendor denial that leaves coverage below half is a
  coverage gap, counted and excluded as such, not evidence about the company).

Cyclical handling. A row is cyclical when model_card's CYCLICAL_WORDS appear in its industry or
sector, when model_card types it as a cyclical type (commodity, semiconductor), or when the data
shows a boom after a downturn (a >= 10% revenue drop, then revenue >= 25% above the pre-downturn
level with the latest year +20% and the margin up >= 3 points), whatever the label. A mixed label
resolved to a platform by its signature (booking platform under "Hotels, Resorts & Cruise Lines")
does not take the operators' word. Peak signs: consensus far below demonstrated growth; boom-like
growth with expanding margins (not when consensus expects growth near the demonstrated rate, not for
a recovery from a down year, not when the margin is not above its own history); a plateau after a
boom; an unlevered price (EV / NOPAT) implying <= -10%/yr; the margin far above its own multi-year
history. Missing consensus and history leave the check `incomplete`, never "no peak". A possible or
unchecked peak fails quality_fair_price and mispriced_turning (peak earnings make the price look low)
and orders the name last in the other lenses with a `possible_cyclical_peak` or
`peak_check_incomplete` tag; `through_cycle` means the check ran and found no sign. An analyst can
confirm or reject cyclicality with a reason: `options.cyclical {symbol: {value, reason}}`.

Lenses (a name may appear in several; one failed condition = near miss with the reason):

- quality_compounder: growth >= 12%; quality strong/adequate; profitable (operating margin or ROIC
  at its weak anchor; loss-makers belong in early growth); growth quality strong; per share (share
  count known, <= 5%/yr, no high dilution flag); no weak strength, no high leverage/coverage/funding
  flag, no accounting or cash-conversion flag. Ordered by cyclical check, quality tier, price check
  (assessed, unassessed, demanding), return proxy.
- quality_fair_price: business quality strong; valuation and priced_in strong/adequate; trajectory
  (growth quality not weak and revenue not shrinking); leverage known; no high balance-sheet or
  earnings-quality flag; return proxy strong/adequate; no possible or unchecked cyclical peak.
  Ordered by growth-quality tier, then return proxy (cheapness alone never ranks first).
- early_commercial_growth: earlier stage by evidence (loss, burn, inflection stage, or a thin
  operating margin with a high or unknown gross margin; a profitable steel maker or contractor is not early)
  and growth >= 20%; >= 3 of 4 commercial proofs, where operating leverage and cash trajectory count
  only while losses and burn are not widening in dollars; share count known and <= 10%/yr.
- mispriced_turning: >= 2 improvement signals incl. a profit/cash one (a recovery from a down year
  counts); a >= 30% drawdown is context and only relaxes this to one signal with intact quality; the
  price lags; share count known and <= 8%/yr; no high balance-sheet flag; no possible peak.
- structural_tailwind: analyst theme or industry, or a numeric footprint (organic share stated when
  known); durable growth; not already priced (unknown priced_in passes only with a theme, a known
  market value and a fallback read that is not demanding: a theme is not a price check); per-share
  funding (share count known and <= 10%/yr, no high funding or dilution flag).

Unsupported economics: XBRL bank/insurer markers, UNSUPPORTED_INDUSTRIES and model_card text rules
(financials, REIT, financial conglomerates, mortgage lenders), compared case-insensitively.
`options.unsupported_industries` adds labels; only `unsupported_defaults: false` drops the built-in
rules. Insurance brokers are screened as fee businesses (fiduciary flag). Labels listing fee firms
with balance-sheet firms are decided by the row's liabilities/assets when present; without a balance
sheet "asset management" stays screenable with a `balance_sheet_unverified` flag and "credit
services" needs `options.supported_override {symbol: reason}`.

Shortlist: round-robin over lenses (`top_n` each), per-lens `sector_cap`, optional
`shortlist_sector_cap`, and a concentration note when one sector holds more than half. Policy fields
state that cheapness, momentum, size and volatility do not select; the price path is context only.

## Research and examples

frontier_research documents IEA/NIST sources, the NBER productivity-J-curve abstract
and explicit access limits. ALAB-type connectivity and NBIS-type cloud infrastructure
are question maps, not current recommendations or verified price targets. The
shipped example is a fictional unprofitable agent-security supplier. It cannot be
used to claim a particular security company will benefit from AI.

## Evaluation boundary

Tests deliberately check false-negative risks (losses/no DCF/pilots retained) and
false-positive risks (endorsements, guidance, duplicate articles, future evidence,
contradictions). They do not demonstrate the ability to pick the next market winner.
That needs frozen historical cohorts, failures/delistings, matched vintage data,
held-out outcomes and both recall and false-positive reporting.
