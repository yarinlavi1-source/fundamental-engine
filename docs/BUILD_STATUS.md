# Current version: 0.11.2 (in progress) — 20.10.2026

Completing the engine end to end: every discovery channel as leads, banks and insurers valued, acquisition-aware growth,
new economic types, deeper filing extraction (segments, convertibles, maturities, pay), a walk-forward test of the engine
itself with the edge reliability it implies, and one run from discovery to a proposed book (docs/CHANGELOG.md, 0.11.2).

Verification actually performed on 20.10.2026: `python -m unittest discover -s tests -q`: 1,070 tests, OK, 6 skipped;
`scripts/run_all.py --skip-collect` ran discovery, research, calibration and the run on the saved 2,470-company universe;
the 491-company sweep and 19 financials ran through the pipeline; the walk-forward test (4,910 company-years) ran on the
engine of commit 08c2263. These are software and process checks; the investment evidence is the walk-forward result
(docs/BACKTEST_ENGINE_2026-10-20.md: no demonstrated edge) and the forward ledger from here on.

## Previous: 0.11.1 — 20.10.2026

The 0.11 decision path after a verified review-fix cycle (docs/CHANGELOG.md, 0.11.1): adversarial reviews of every
stage, each defect reproduced by a script before it counted, each fix covered by a regression test that fails on the
earlier code, then a second review of the repaired areas. Contracts: docs/ARCHITECTURE.md, docs/MODULE_CONTRACTS.md.
Package metadata (pyproject.toml, fundamental_engine.__version__) still reads 0.11.0.

Verification actually performed on 20.10.2026 (Python 3.13, this container):
- `python3 -m unittest discover -s tests -q`: 961 tests, OK, 4 skipped (the Stage 0 tests need the private data store
  and reproduction results). 641 tests at 0.11.0.
- `python scripts/test_layers.py`: software 456, data accuracy 129 (4 skipped), analysis behaviour 147, decision rules
  229; excess return not provable by unit tests (docs/EVIDENCE_STATUS.md).
- `python -m fundamental_engine pipeline --factbook tests/fixtures/adbe_factbook_2026-10-19.json`: all eight stages ran,
  no errors; casefile, report and Hebrew paragraph written.

Not performed here: the CI matrix (3.11-3.13), the wheel build/install check and the other CLI smoke runs in
.github/workflows/test.yml. These are software checks, not investment accuracy measurements. Remaining limitations, by
severity: docs/LIMITATIONS.md.

## Previous: 0.11.0 — 20.10.2026

One decision path from connector payloads to a reviewed verdict, plus discovery, read-only portfolio and monitoring
layers on the same objects. 641 tests at the release commit.

## Previous: 0.10.0 — 19.10.2026

Expectation gap, instrument-level capital bridge, independent red-team, point-in-time SEC panel,
inflection features, forecast ledger. Pre-registered universe test (docs/GAP_TEST_RESULTS.md):
H1 calibration PASS, H2 return edge FAIL, H3 momentum interaction passes the literal rule but is not
statistically distinguishable from luck and reverses in the design window -> forward test only.
Validation: 307 unit/integration tests passed locally (software checks, not investment accuracy).

## Previous: 0.9.2 — 2026-09-28 (merged into the 0.10 line on 20.10.2026)

Fixes from the first full real-company run (ServiceNow, see NOW_RUN_2026-09-28.md):
input-aware confidence (source access, discount-rate band), implied cost of equity,
ratio driver specs with implied growth, and ledger-safe price stress/reverse checks.

Validation: 262 unit/integration tests passed locally, CLI smoke runs (value-drivers
with diagnostics, plain with the NOW regression) and isolated wheel check including
driver specs. These are software checks, not investment accuracy measurements.


## Previous: 0.9.0 / 0.9.1

Valuation integrity is enforced at calculator, research-review and plain-output
boundaries. Arithmetic is retained for inspection; unreviewed live price conclusions
are withheld. See valuation_integrity and AXTI_AUDIT_2026-09-28.md.

Validation: 240 unit/integration tests and 12 synthetic research-process cases passed locally. Wheel installation checks passed; these are software checks, not investment accuracy measurements.

## Prior build notes (historical; growth premium rules superseded)

# Current build — v0.8.0 — 2026-09-28

Two valuation lanes (intrinsic vs growth premium), tail scenario, probability-weighted
payoff, expectation momentum, multiples view and reverse revenue check. Palantir
point-in-time check: momentum weak Feb-2023, strong Aug-2024 (one case, not a backtest).
215 tests.

## v0.7.1 — 2026-09-28

v0.7.1: recent-quarter inflection/slowdown detection, current share count dilution,
red bottom line when price exceeds the bull value; AXTI real regression case; 206 tests.

## v0.7.0 — 2026-09-27

Company type first. `classify_company` computes the Damodaran life-cycle stage and
Lynch category from reported history, detects large acquisitions (goodwill jumps)
and cycle peaks/troughs, and returns what decides value, key metrics, valuation fit
and packets for that type. `forensic_scores` adds Piotroski F, Altman Z/Z'', Beneish
M, ROIC/incremental ROIC and Rule of 40; a forecast base-rate check flags base cases
that need rare sustained growth. `import_statements` maps Alpha Vantage statements
into history rows. plain_verdict now includes the type section, the new checks in
plain Hebrew, stage-weighted axes and red flags in the bottom line. New packets:
master_process, company_type, forensic. Sources in docs/RESEARCH_BASIS.md.

Validation: **203 tests** (14 new) including a real Broadcom (AV) regression case
classified as mature compounder with the FY2024 acquisition flagged.

## Previous build — v0.6.0

Adds the eye-level Hebrew verdict layer (`plain_verdict`). Every material metric
from reported history — growth, product margin, operating margin, free cash, cash
backing of profit, debt/cash, runway and dilution — gets a label (מצוין/טוב/בינוני/
חלש/מדאיג) and an everyday explanation instead of a bare number. Quality, growth,
financial strength and price versus value stay separate axes; the bottom line
combines them into a plain call. The price verdict exists only when the supplied
valuation_case is executed through value_company in the same call. Adds the
plain_language brain packet, `plain` CLI, MCP tool and a fictional demo.

Validation: **189 tests** passed locally (11 new); 12 process benchmarks; isolated
wheel execution now includes plain_verdict; **29 packaged packets**. Grades are
transparent rules of thumb by business economics, not verified facts, predictions
or orders.

## Previous build — v0.5.0

Adds independent emerging-growth discovery without profitability/FCF/P-E gates:
causal driver-to-beneficiary paths, future pressure scenarios, payer/value capture,
pilot/design-win/paid/production/repeat stages, counterevidence, milestones and
comparable metric trajectories. Adds theme-first planning, revision comparison,
dossier provenance checks, three MCP tools, discover CLI, two brain packets and
one fictional unprofitable early-adoption example.

Validation: **178 tests** passed locally (35 new); 12 prior process benchmarks;
isolated wheel execution of research review, annual valuation and discovery;
**27 packaged packets** loaded successfully. New checks cover premature rejection
AND unsupported promotion. They do not establish future winner detection or returns.

The engine supplies research structure and arithmetic. Claude still supplies the
reasoned causal hypotheses and reviews retrieved evidence. No autonomous prediction
of unknown inventions, no monitoring scheduler and no stock-entry/trading tool.
The early-discovery lane does not bypass price/funding underwriting. Research sources
and access limits are documented in frontier_research; the shipped case is synthetic.

The previous release's valuation functionality and limits remain below.

---

# Build status — v0.4.0 — 2026-09-27

## Implemented and exercised

Retains v0.3 research, source, historical normalization, financial/event analysis,
local journal and MCP. Adds:

- Dated valuation contract with separate current-date and requested year-end values.
- Operating revenue drivers and ACT/365.25 stub periods; cash/debt/prepayment/share
  ledgers, taxes/NOL, SBC alternatives, explicit financing costs and dilution.
- Current-holder distribution/terminal-equity valuation with an auditable bridge.
- Funding shortfalls block unsupported going-concern output rather than create
  free equity or assume a zero recovery value.
- Financial-firm residual-income terminal with clean-surplus book and capital check.
- NAV, SOTP and rNPV **snapshot calculators**, with clearly narrower coverage than
  the operating route. They do not auto-underwrite their financing or probabilities.
- Asset replacement/depreciation schedules, five fixed operating stresses,
  discount/growth sensitivity, reverse unit-price sensitivity and forecast scoring.
- Hebrew HTML table with full audit; content-addressed inputs/JSON; four new MCP
  tools; CLI value command; optional annual valuation attached to dossier review.
- Two new packaged method packets and primary research bibliography, five fictional
  model fixtures, contract documentation and CI smoke checks.

## Verification actually performed

- 143 unit/integration tests passed locally (50 added in this release).
- Existing 12 synthetic adversarial process cases passed.
- Wheel built; isolated installation loaded all 25 packets and ran both dossier
  review and annual valuation outside the repository checkout.
- Annual valuation CLI with sensitivity/stress diagnostics generated HTML and JSON.
- All five model routes ran on synthetic fixtures; bank/NAV/SOTP/rNPV formulas,
  cash reconciliation, dividend PV, dilution, stubs, lookahead and source identity
  were checked independently in tests.

This is evidence of selected implementation behavior, not forecast accuracy or
outperformance. No real-company 2026–2030 target series has been calibrated here.
No 99%-accuracy or superiority claim is supported by these tests.

## Known boundaries that remain explicit

- Claude/client must retrieve, read and interpret actual sources; assumption review
  labels are not independent semantic verification. No access to user's private
  Claude/broker session was used, and there are no trading tools.
- Operating model uses a blended debt bucket, no automatic options/convertibles,
  lease conversion, covenant waterfall, FX or jurisdiction-specific tax engine.
- Cash checked at period boundaries only; monthly construction models are needed
  for intra-year funding risk. Cash interest defaults to zero.
- Terminal economics require maturity and reinvestment support; the model may need
  explicit years beyond 2030. ROIC, discount rates and failure probabilities are
  not inferred or calibrated automatically.
- Bank route assumes constant shares, clean surplus and book/regulatory reconciliation;
  insurance reserves, OCI and recapitalizations need specialist extensions.
- NAV/rNPV/SOTP require externally underwritten component forecasts and financing.
- Stresses are conditional shocks, not a probability distribution. Forecast scoring
  describes supplied held-out observations, not an investment backtest.
