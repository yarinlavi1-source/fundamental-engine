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
