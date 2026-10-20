## 0.11.1 — verified review-fix cycle (20.10.2026)

Adversarial reviewers attacked every stage; a defect counted only when a script reproduced it. Each fix came with a
regression test that fails on the earlier code, and a second review attacked the repaired areas again. Counts below are
verified defects fixed: first review + verification of the repairs + follow-ups (review notes are kept outside the
repository). Tests: 641 at 0.11.0, 961 now (OK, 4 skipped). Software checks, not investment accuracy. Package metadata
(pyproject.toml, fundamental_engine.__version__) still reads 0.11.0, so casefiles record engine_version 0.11.0.

- facts / dataqa / analysis — 42 (3 found on real ADBE data + 9 + 29 of 30 + 1): bundle fiscal-year rows stay annual;
  dataqa takes facts.ttm's signed value (ADBE revenue_ttm was 61.1bn, now 25.97bn) with a `ttm_matches_facts` check; every
  SEC tag merged per period end; undated filings wait for their deadline (annual 90 days, Q4 rows the annual lag, foreign
  120/180); facts about periods ending after as_of are unusable; issuer documents vs secondary sources, independence by source
  family; splits need positive evidence (dated action, as-traded price bar, restated weighted shares) and split + issuance
  keeps the issuance; FY+YTD TTM checks; debt scope by roll-forward; capital-structure lines issued after as_of not read;
  negative cash or investments rejected as a data error (`liquid_assets_nonnegative`), never an opening balance.
- model_card — 6 (4 + 2 follow-ups) plus its share of the discovery verification below: industry text decides the type;
  fee networks and exchanges get an operating route; cyclical industry words are a flag, normalization needs data or the
  analyst (`inputs.cyclical`); evidence published after as_of is not evidence; a lender-shaped balance sheet under another
  label routes to residual income (`flags.lender_shaped`); a profitable company that needs outside money keeps the FCFF path
  with `valuation.funding_flag` instead of the survival model.
- screen / sec_frames — 7 in the first review + 38 of 38 in the discovery verification (shared with model_card; the record
  does not split them by file): owner yield (FCF - SBC) / market cap; priced-in on EV/NOPAT with a cross-check and a price
  check in every lens; cyclical peaks from data keep peak earners out of compounder/fair-price lenses; missing debt is
  unknown, never net cash; per-share funding discipline in the structural lens; loss-making growers can show traction.
- forecast / valuation — 39 (14 + 22 of 26 + 3): value independent of the quote (unlevered table rate relevered on the
  model's own equity, expected cost of debt, never the coupon); mid-cycle cyclicals and through-the-cycle margins; current
  year from YTD actuals; funding order investments -> SBC in shares -> debt within 3x EBITDA -> equity at min(quote, own
  value); investments and excess cash at face; reinvestment converging to g/ROIC; decline stage may keep declining; fifth
  anniversary always a valuation date; unknown moat only as a labelled, continuous proxy; case sources carry the document
  date and how it was read; a rate typed into the inputs is `analyst_input`, never a filing; the terminal debt weight is
  stored in the case and rate diagnostics move the terminal WACC by (1 - w).
- risk / review — 16 (8 + 2 + 6 follow-ups): only an accepted reviewer unlocks a price call (author statuses are notes);
  the review is bound to the valuation and to the analyst inputs (`inputs_sha256`, `rv['binding']`); self-review detected
  by normalized name; red-flag releases need filing documents beyond the flag's own evidence; error size is valuation-only;
  permanent-loss cases never milder than bear; pre-revenue and unclassified companies and the risk variants complete;
  `pricing_status` priced / partially_priced / not_priced / open_question / gate; valuation-error entries labelled; Hebrew
  descriptions for base-rate and what-must-be-true entries; a negative cash figure is never runway.
- decision / render — 50 (18 + 28 of 31 + 4; the other 3 were review.py items, fixed above): one required return, never
  below the model's own cost of equity; marginal band; IRR and values at a required return move the terminal WACC by the
  equity weight; upgrades cannot bypass a gate or lower the hurdle; crashed valuation is an evidence gap; funding gates only
  blocked or unreviewed financing (a reviewed priced path is valued with `conditional_on`); unresolved forecast material
  issues keep a named draft; the band is re-run without a computed moat proxy; register pricing status read, never upgraded.
- portfolio — 2 in the first review (account-like keys never become symbols; ETFs default to diversified) plus the
  economics/privacy verification (34 regression tests; the record gives no defect count): common factors separate from
  driver clusters, adds funded from cash then pro-rata sales, order-shaped payloads refused.
- monitor / ledger / mcp — 6: current fiscal year frozen and checked; one-year growth triggers; backdated recordings
  refused and late rows excluded from learning; events recorded after an outcome are post-hoc; MCP ledger paths confined
  to the corpus directory; thesis_score takes actuals.
- pipeline / cli — 3 (2 + 1): later-dated price, share count and consensus skipped instead of clamped to as_of; a draft
  verdict no longer reads as supporting a price in `summary`; `summary` carries decision_version, margin, hurdle and
  value_at_required_return. CLI: `python -m fundamental_engine.cli` works; inline JSON for --company and portfolio --options.

Still open: docs/LIMITATIONS.md (no index-beating evidence, SEC blocked, leverage drift deferred, price-history basis
not filled, 1:1 sensitivity grid, vendor quotas, no structured GOOGL/NVDA statements).

## 0.11.0 — one decision path from sources to verdict; discovery, portfolio and monitoring layers (20.10.2026)

- facts.py: FactBook, one schema with provenance and availability date for every number; adapters for FMP
  as-reported statements, SEC companyfacts (first filed, filed+2d), Alpha Vantage, IB prices, vendor share
  counts, consensus and researched evidence bundles; point-in-time accessors; TTM from quarters or FY + YTD.
- dataqa.py: eleven critical fields (verified / reported / single_source / conflict / missing / stale), accounting
  identities, cross-provider checks, debt-scope resolution by roll-forward, split-aware share continuity.
- analysis.py, model_card.py: ~35 traced metrics; business type, stage, funding, driver tree, valuation method by
  type x stage, understanding checklist evidenced only with dated evidence.
- forecast.py: bear/base/bull driver specs (guidance or consensus anchored, growth fade, margins after SBC,
  reinvestment linked to growth, explicit funding, cost of equity from a documented table), what-must-be-true.
- pipeline.py: collection plan, raw folder -> FactBook, one valuation route (drivers -> value_company + diagnostics),
  run() over all stages with failures recorded, summary().
- risk.py, review.py: disqualifying vs pricing vs accepted risks, permanent-loss and funding-failure cases,
  red-team package derived from execution, independent reviewer sign-off validated by re-running the audit.
- decision.py, render.py: four separate assessments, IRR at today's price vs index-based hurdle (14% only as a
  comparison), closed verdict set with gates, price for hurdle, governed overrides; markdown report and Hebrew
  paragraph.
- screen.py, sec_frames.py: discovery lenses (quality compounder, quality at a fair price, early commercial growth,
  mispriced/turning, structural) on eight separate dimensions; whole-market SEC frames adapter.
- portfolio.py: read-only IBKR book, concentration, economic exposure overlaps, scenario stress, review triggers,
  candidate vs holdings vs index vs cash options (never orders).
- monitor.py + ledger v2: thesis records, material-only alerts, frozen forecasts with system timestamps,
  error attribution; no backdating, no forecasting the past.
- Hardening of earlier gates (redteam precedence, margin of safety, archetype match, hypothetical financing,
  red-flag gate, stage override reason, driver defaults, Alpha Vantage availability).
- CLI: pipeline-plan, pipeline, screen, portfolio. MCP: collection_plan, run_pipeline, screen_universe,
  screen_changes, portfolio_analyze, portfolio_compare, thesis_record/check/freeze/score.

## 0.10.0 — expectation gap, capital bridge, red-team, point-in-time validation (19.10.2026)

- capital.py: instrument-level capital structure (accreted converts, rational conversion fixed point,
  pre-funded warrants, post-statement events, prepayments kept out of net cash). Golden NBIS fixture.
- redteam.py: independent audit of a valuation package; critical failure = valuation UNVERIFIED.
- expectations.py / expectations_vec.py: one driver model (revenue -> EBIT after SBC -> reinvestment
  -> FCFF), reverse-solve one lever at a time, detects when growth destroys value.
- pit.py / inflection.py: point-in-time SEC companyfacts (first filed value, filed+2d), Q4 derivation,
  multi-class cover shares; acceleration, incremental margins, acquisition flag, stage, transparent scorecard.
- ledger.py: append-only hash-chained forecast ledger.
- scripts/: pit_build, pit_panel, gap_backtest (pre-registered), case_suite, gap_card, gap_explore (exploratory).
- MCP tools capital_bridge, redteam_audit, expectation_gap, forecast_ledger.
- Results of the pre-registered universe test: docs/GAP_TEST_RESULTS.md. Calibration is not a return edge.

## 0.9.2 — honest confidence, driver specs and reverse diagnostics (from the NOW run)

- `sources[].retrieval` and `underwriting_audit.source_access`: confidence drops when
  opening balances rest on primary sources that were not read directly.
- `discount_rate_band` (±1pt) and `implied_cost_of_equity`; confidence drops when a
  one-point change flips cheap/expensive or moves value by more than 25%.
- The concise paragraph now names the main confidence reason.
- `driver_version: 1` specs: `build_valuation_from_drivers` (MCP), `value-drivers`
  CLI, `implied_growth_shift`; fictional `examples/valuation/software_drivers.json`.
- `prepayment_price_linkage` (scaled/fixed): price stress and reverse sensitivity no
  longer break deferred-revenue ledgers; reverse search restricted to funded domain.
- NOW regression input and run notes (`docs/NOW_RUN_2026-09-28.md`); dated connector
  observations appended to session_capabilities. No accuracy claim.

## 0.9.1 — concise decisions and potential underwriting

- Default plain/MCP/CLI text is one Hebrew investment paragraph; full detail retained.
- Separate current-value discount from upside and dated five-year conditional price.
- Withhold approved values for demo, unresolved review and funding blocks.
- Traceable short analyst notes connect potential, risk and milestones to observations.
- Add SOFI lessons on economic inference, flow/stock, segment/margin definitions,
  financial-sector capital and discounted future targets to existing brain packets.
- This improves process and delivery, not a claim of validated forecast accuracy.

# v0.9.0 — valuation integrity and AXTI regression

Separates calculation success from permission to publish a price conclusion. Adds
opening assets/share bridge, investment ledger, comparable-consensus review,
terminal maturity review, exact reproduction input and executed dossier binding.
Corrects AV cash/share definitions and AXTI point-in-time dilution. Removes default
scenario probabilities, momentum valuation overrides, and fixed five-year 25x claims.
Preserves Claude v0.6–v0.8 classification, recent-quarter and forensic work with
regressions. No replacement AXTI target or investment-accuracy claim.

## Historical release notes (superseded where v0.9 changes behavior)

# Changelog

## 0.3.0

Research operating system for Claude's existing data connections. Adds on-demand
protocols/playbooks, evidence-led dossier supervisor, counter-thesis gates, named
valuation basis, investigation-loop controls, promise tracking, append-only research
memory, seven MCP tools and installed-package verification. See START_HERE.md.

## 0.2.0

Business/event analysis, symmetric normalization, financing-aware ownership model,
growth-linked reinvestment, exact-concept SEC periods, source corpus, stdio MCP,
Hebrew reports and three historical primary-source examples.

## 0.1.0

Deterministic annual financial analysis, DCF/reverse DCF, emerging-potential evidence,
funding-gap simulation, SEC transport, immutable run snapshots and Hebrew report.

## 0.4.0 — 2026-09-27

Annual value paths and dedicated model routes. Adds operating-driver revenue,
prepayment/cash/debt/share reconciliation, explicit financing and SBC alternatives,
terminal FCFF-to-equity bridge, annual current-holder values, funding gates,
residual-income capital checks, NAV/rNPV/SOTP snapshots, asset-cohort schedules,
stress/sensitivity/reverse-price tools, frozen forecast scoring, Hebrew report,
CLI and MCP integration, and primary methodology research. 50 new tests; 143 total.
All shipped new valuation examples are fictional. No calibrated issuer targets or
predictive-accuracy claims are included.

## 0.5.0 — 2026-09-27

Theme-first discovery, causal bottleneck paths, payer/value-capture checks, adoption
stages, comparable progress metrics, milestones, evidence revisions and independent
discovery/valuation lanes. Adds three MCP tools, discover CLI, dossier integration,
two research packets and a fictional unprofitable early-adoption case. 35 new tests;
178 total. No trading, automatic surveillance or calibrated winner probabilities.

## 0.6.0 — 2026-09-27

Eye-level Hebrew verdicts. `plain_verdict` (MCP), `plain` CLI and the plain_language
packet turn reported history into good/not-good labels with everyday explanations,
sector-aware margin bars, stage-aware treatment of losses and cash burn, separate
quality/growth/strength/price axes, a plain bottom line, and price versus executed
bear/base/bull value in words through 2030. Synthesis now leads with this layer.
11 new tests; 189 total. Research indication only; no orders or promised returns.

## 0.7.0 — 2026-09-27

Company typing (Damodaran life cycle + Lynch), acquisition and cycle detection,
Piotroski/Altman/Beneish/ROIC/Rule-of-40 scorecards, forecast base-rate check,
Alpha Vantage statement mapper, master_process/company_type/forensic packets, three
MCP tools, classify and import-av CLI, stage-weighted plain verdict. Real Broadcom
regression example. 14 new tests; 203 total. Screens direct research; no return odds.

## 0.7.1 — 2026-09-28

Turning points. Optional `recent_quarters` (AV mapper fills them) let company typing
and plain verdicts see inflections or slowdowns that annual history hides; a recent
quarterly profitability item; `current_shares` catches issuance after the last annual
report; a price above even the bull value now yields a red bottom line. Found while
running AXTI; real AXTI regression example added. 3 new tests; 206 total.

## 0.8.0 — 2026-09-28

Two valuation lanes. Intrinsic (numbers-dominant, e.g. Nvidia) versus potential
(young/unprofitable/inflecting). Optional executed `tail` scenario in value_company;
probability-weighted payoff; expectation momentum (acceleration, margin expansion,
beats/raises, estimate revisions, gross margin); analyst-style multiples view;
reverse revenue requirement; lane verdicts that allow a premium only with strong
momentum. growth_valuation packet, expectations_momentum MCP tool, point-in-time
Palantir momentum case, AXTI tail case. 9 new tests; 215 total. No return prediction.
