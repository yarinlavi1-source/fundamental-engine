# Annual valuation contract — v0.4

Run `python -m fundamental_engine value examples/valuation/infrastructure.json --diagnostics`.
Use `value_company` and `valuation_diagnostics` through MCP. Results include today's
value plus each requested date, detailed financial paths, provenance and warnings.
All included fixtures are FICTIONAL. They are not IREN forecasts or price targets.

## Common input

`valuation_version:1`, `company_id`, `ticker`, `as_of` (ISO date), `currency`,
`unit: absolute`, `archetype`, `method`, `quote`, `sources`, `assumptions`, `opening`,
`report_dates`, and `bear`, `base`, `bull` scenarios plus an optional `tail`
(large-outcome case for the growth lane; see growth_valuation). See executable examples.
One security and currency only. No automatic ADR/share-class/FX conversion.

Each source: id, title, url, kind, published_at, available_at. Each assumption:
id, kind (reported/guidance/analyst), as_of, review_status (reviewed/unreviewed),
source_ids, rationale, falsifier. Source dates cannot exceed the cutoff. A review
label is an analyst assertion, not semantic verification. Opening, every period,
scenario and terminal carry assumption_ids. Cite the relevant passages in the
research dossier; do not cite a whole annual report as support for invented targets.

Opening balances must be AS OF the valuation date, with a documented bridge from
filed statements. Use unrestricted cash only. Existing restricted cash and project
restrictions need a separate deployment schedule; do not add restricted cash as
spare equity. Quote date/currency/evidence are explicit; stale quotes warn.

Periods have exclusive start, inclusive end, contiguous dates, each at most 1.01
years. ACT/365.25 discounting and revenue accrual; annual values thus differ slightly
between leap/nonleap years. Use monthly periods when liquidity timing matters.
Display dates must be modeled boundaries. Horizon may extend up to 120 periods to
reach stable economics AFTER 2030 while reporting only 2026–2030.

## Operating route

Routes: software, platform, industrial, consumer, infrastructure, commodity,
nonfinancial, turnaround. No assumption that all routes use the same input values.

Opening: unrestricted_cash, debt, shares, deferred_revenue, tax_loss_carryforward.
Scenario: cost_of_equity, sbc_policy, periods, terminal. Segment inputs per period:
name, driver (capacity/subscribers/units/commodity), average_units,
annual_revenue_per_unit, utilization, cash_cost_ratio. Revenue equals average
units * annual price * utilization * period years. For one-time unit sales supply
an annualized sales run rate, not installed capacity. Segment names must be unique.

Other period inputs (monetary figures are period totals, not annualized):
- fixed_cash_costs, depreciation, sbc_expense, sbc_shares;
- debt_draw, debt_repayment, interest_rate, debt_fees;
- tax_rate, nol_usage_limit (0..1), change_working_capital_excluding_deferred;
- customer_prepayments (cash in), revenue_from_prepayments (revenue already paid);
- maintenance_capex, growth_capex, minimum_cash, payout_fraction;
- equity_proceeds (gross), equity_issue_price, equity_fee_rate;
- financing_status: committed / assumed / none. Assumed financing is allowed but
  visible, and tested absent. This label is not a verification of loan availability.

Debt draws occur at period start, principal repayments and distributions at end.
Interest = (opening debt + draw)*rate*period fraction. One blended debt bucket;
model covenants/tranches/converts separately and reconcile into inputs. Shares
issued = gross proceeds / issue price; fees reduce cash, not issued share count.
No automatic equity plug or circular inference of issuance price from model value.

CFO = cash EBITDA - economic cash SBC (cash policy only) - interest - cash taxes
- change in WC excluding deferred + advances received - advance revenue recognized.
Cash = opening cash + CFO - both capex types + debt draw - debt repayment + net
new equity - debt fees - dividends. Debt and deferred-revenue ledgers reconcile.
A cash deficit at any boundary blocks going-concern prices, even if later funds
arrive. This is a funding BLOCK, not an estimated zero recovery value.

SBC policies: cash_equivalent deducts SBC as cash, prohibits future SBC shares.
explicit_shares adds specified shares, keeps SBC in accounting EBIT/tax computation
and adds it back in cash flow. This simplified tax treatment must match the issuer;
no jurisdiction-specific option tax rules, expirations or tax-refund assumptions.
Neither policy values existing options automatically; include existing equivalent
claims in opening shares or terminal other_claims consistently, never both.

NOL usage is capped by a supplied fraction of positive taxable income. Losses do
not create a cash tax refund. Model assumes no expiry and no ownership-change
limitations beyond the supplied usage cap; specialist tax cases require extension.

## Terminal and current-holder value

Next revenue = last period revenue annualized * (1+g).
NOPAT = next revenue * terminal operating_margin * (1-tax_rate).
FCFF = NOPAT*(1-g/ROIC). EV = FCFF/(WACC-g).
Terminal equity = EV + excess cash - debt - other_claims - net_runoff_obligation.
Excess cash excludes minimum operating cash. Margin must INCLUDE economic future
SBC. The terminal reinvestment rule presumes stable maintenance/depreciation and
working-capital economics. Use asset cohorts and extend explicit years if not true.

Explain terminal working-capital/prepayment renewal versus runoff. Input any net
runoff obligation explicitly; do not subtract all deferred revenue mechanically.
Future margins, discount rates and ROIC need economic support, not a default label.

Current holder value = PV(dividends PER SHARE + terminal equity PER SHARE), using
Ke and actual dated share counts. No second subtraction of equity raised or future
negative FCF. Future-date values roll back remaining distributions and terminal
value; same as-of information set, conditional on that path. They are NOT future
updated analyst opinions or market-price forecasts. Pre-2030 retained cash stays
in the ledger, not also in dividends. Cash earns zero interest in this version.

## Specialist calculators

- residual_income: financials. book_equity and shares opening. Period roe,
  payout_fraction, risk_weighted_assets, required_capital_ratio. Clean-surplus book
  rollforward with no OCI or share changes. Terminal ROE/g and Ke produce
  B*(ROE-g)/(Ke-g). Regulatory capital shortfall blocks output. Use only after
  reconciling book to eligible capital; this is not a bank/insurance regulatory engine.
- nav: reit/asset_holding. Opening asset_value/cash/debt/other_claims/shares.
  Each period: properties (forward_noi, cap_rate, ownership), other_asset_value,
  unrestricted_cash, debt, other_claims, selling_costs_and_taxes, shares.
- sotp: conglomerate. Components with id, basis (enterprise/equity), value,
  ownership; enterprise components also cash, debt, other_claims. Snapshot-level
  unrestricted_cash, holding_debt, other_claims, corporate_costs_pv, shares. Remove
  intercompany claims; do not subtract subsidiary debt twice.
- rnpv: biotech. Components have id and dated cashflows, each cash_flow and
  probability OF OCCURRENCE. Snapshot cash/claims same as SOTP. Cash flows must be
  after snapshot date. No automatic patent extensions, correlated probabilities,
  development-cost inference, or financing/dilution forecasting.

For NAV/SOTP/rNPV `opening_snapshot` may override each scenario's common opening
value assumptions (must include as_of and assumption_ids). These are snapshot
calculators; future components and financing must be separately underwritten.
Their output is not a certification of a funded company valuation. Dossier price
conclusions for specialist archetypes remain gated pending specialist underwriting.

## Diagnostics and evaluation

Sensitivity varies Ke (and terminal WACC for operating models) and terminal g.
Invalid combinations remain explicit. Stresses cover price, cost, capex, issuance
price and loss of assumed financing; no calibrated probabilities are attached.
Reverse pricing solves a uniform unit-price multiplier, with fixed units, capex,
financing and terminal margin. It is a conditional sensitivity, not market consensus.
Asset replacement tool separates depreciation life from physical replacement life;
initial acquisition belongs in growth capex. Forecast scoring requires frozen dated
forecasts and later actuals with matching definitions; no return backtest implied.

## Output and scope

HTML provides the requested Hebrew table and expandable full JSON audit; JSON has
input SHA256, annual_values, forecast, terminal bridge, source/assumption ledger,
funding status, warnings and terminal dependence. CLI writes content-addressed
input and reports to runs/. No private issuer analysis is published automatically.
Changing the model version can change output even for identical input; archive the
engine version alongside the input hash. There is no trade execution.

rNPV caution: cash-flow probabilities and discount rates address different risks.
Do not add the same clinical failure penalty again to the discount rate. Historical
cohort rates require stage, indication, modality and vintage matching. The engine
cannot infer those clinical distinctions from a ticker.

## v0.9 valuation integrity

Read the `valuation_integrity` packet and the executable fictional contract in
`examples/valuation/audited_infrastructure.json`. Run
`python -m fundamental_engine valuation-audit INPUT.json` or MCP `valuation_audit`.
Calculations preserve reproduction_input and underwriting_audit. A live model with
missing reconciliation returns underwriting_required, even if arithmetic succeeds.
Pass the full research_dossier to plain_verdict; it must execute the same valuation
input. All gates are process checks, not independent verification or forecast accuracy.
Discovery needs no positive earnings/FCF or low multiple. Preserve early opportunities.

## v0.9.2 — driver specs, source access and discount-rate diagnostics

### Driver specs (`driver_version: 1`)

For subscription/software and other ratio-driven businesses, write the forecast as
ratios and let `build_valuation_from_drivers` (MCP) or
`python -m fundamental_engine value-drivers SPEC.json --diagnostics` generate the
absolute period inputs. Fictional format: `examples/valuation/software_drivers.json`.

Top level: every ordinary case field (as_of, ticker, quote, sources, assumptions,
opening, report_dates, underwriting) plus `boundaries` (period ends, first period
starts at as_of), `last_full_year_revenue` (annual revenue the first full year grows
from) and optional `stub_annual_revenue_rate` (annual run rate of a short first
period; that period has no growth entry).

Per scenario: name, thesis, assumption_ids, optional period_assumption_ids,
cost_of_equity, terminal and these drivers (a single number or one value per period):
`growth` (one per non-stub period), `cash_cost_ratio` (cash costs excluding SBC and
D&A / revenue), `sbc_pct`, `depreciation_pct`, `amortization` (absolute), 
`maintenance_capex_pct`, `growth_capex_pct` (use it for recurring acquisitions),
`tax_rate`, `interest_rate`, `deferred_revenue_ratio` (balance / annual revenue),
`prepaid_share` (share of revenue recognized from the liability; ledger split only),
`working_capital_ratio` (x change in annual revenue), `payout_fraction`,
`minimum_cash`, `investment_income_after_tax` (annual), `debt_draw`,
`debt_repayment`, `fixed_cash_costs`. SBC is charged as a cash-equivalent cost.

Generated cases carry `generated_from.spec_sha256` and
`prepayment_price_linkage: scaled`, so price stresses move billings with price.
Hand-built cases default to `fixed` (contracted advances), where amounts stay and
recognition is only capped at shocked revenue. The spec adds arithmetic, not
evidence: each ratio still needs sourced assumptions and falsifiers.

### Source access (`sources[].retrieval`)

Optional: `primary_document`, `provider_normalized`, `search_excerpt`,
`market_feed`. `underwriting_audit.source_access` lists the primary sources behind
the opening balance and whether each was actually read in the original document.
Anything other than `primary_read` lowers plain_verdict confidence; it does not
block publication, because vendor data can be consistent while definitions or
footnotes still differ. Unrecorded retrieval counts as not read directly.

### Diagnostics

- `implied_cost_of_equity`: the required return (terminal WACC shifted equally) at
  which the scenario value equals the quote, cash flows fixed.
- `discount_rate_band`: value at Ke −1pt / +1pt. If the quote falls inside, the
  cheap/expensive call depends on a one-point judgement; confidence drops. A band
  wider than 50% of the central value also lowers confidence.
- `implied_growth_shift` (driver specs): uniform growth uplift that justifies the
  quote with every ratio fixed.
- `reverse_price` now searches only the funded part of its multiplier domain and
  says so, instead of failing when low multipliers starve cash.
All are sensitivities of the supplied model, never market consensus or odds.
