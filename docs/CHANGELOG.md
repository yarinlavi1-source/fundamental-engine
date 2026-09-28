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
