# Valuation integrity — mandatory before a price conclusion (v0.9)

Do not tune a model to agree with the market or with the user's hoped-for upside.
Do not defend a low valuation merely because tests pass. Diagnose the difference.
Discovery remains open to early, loss-making businesses: these checks restrict
publication of an unsubstantiated price conclusion, not entry to the research list.

## Execute and retain the exact model

1. Preserve all inputs to `value_company`, the returned `input_sha256`, output and
   source snapshots in the local research checkpoint. Output embeds reproduction_input.
2. `valuation_audit` executes the same model and returns issues, comparisons and
   terminal transitions. `underwriting_required` means draft arithmetic, not a verdict.
3. Attach identical input as dossier.annual_valuation_input; run research_review.
4. Pass that full dossier as plain_case.research_dossier and the identical
   plain_case.valuation_case. plain_verdict re-executes review and checks the hash.
   A hand-entered research_status string cannot unlock live price conclusions.
5. A demo may illustrate mechanics but may never masquerade as a real company.

## Opening reconciliation (operating route)

Use underwriting.opening_reconciliation with reviewed assumption_ids linked to
primary filing/issuer evidence (source kind filing, earnings_release or issuer_release):
- statement_as_of and share_basis=point_in_time_common_outstanding;
- cash_and_equivalents EXCLUDING restricted cash; restricted_cash separately;
- short_term_investments, long_term_investments, investment_haircut;
- debt, common_shares_outstanding, incremental_dilutive_shares;
- dilution_review and claims_review, each with assumption_ids;
- bridge_events: date, assumption_ids and optional unrestricted_cash_delta,
  marketable_investments_delta, debt_delta, shares_delta. Events occur after the
  statement and no later than as_of. bridge_review is mandatory when dates differ,
  including an evidenced explicit estimate of no events. Never silently date-roll.

Cash, investment and debt totals must match opening. Shares must reconcile actual
common shares plus incremental awards/convertibles. EPS weighted-average shares
are a different metric. For current_shares in plain inputs require basis=
point_in_time_common_outstanding; history.shares_outstanding supplies prior balances.
History.shares_diluted remains weighted-average diluted EPS shares only.

The operating ledger accepts opening.marketable_investments. They remain assets,
not cash available for capex, until an explicit period.investment_liquidation.
Liquidation transfers assets once; no free cash creation or double terminal value.
Period investment_income_after_tax and noncontrolling_distributions are optional
explicit cash flows; substantiate tax, ownership and availability. Remaining
investments enter the terminal equity bridge. Other_claims must address preferred
stock and subsidiary minority value. Do not haircut parent cash by a subsidiary's
minority percentage; avoid counting the same minority claim twice. Keep customer
advances in the deferred-revenue ledger, never as free earnings.

## Forecast and terminal reviews

For EVERY scenario, underwriting.scenario_reviews[name] requires assumption_ids
under demand_and_capacity, funding_and_claims, competitive_response, and maturity.
Each reference must point to an actually reviewed economic rationale and falsifier.
Do not reuse generic prose to claim all four questions have been answered.

The code flags >10 percentage-point growth transitions, >5-point margin changes,
and a terminal year annualized from <0.9 years. Extend the explicit forecast and
fade growth/margins when necessary. A discontinuous change requires a separately
supported transition_exception. Zero final maintenance capex in capital-heavy
businesses requires replacement_exception. Thresholds trigger review, not empirical
laws. Reporting through 2030 does NOT require terminal maturity in 2030; forecast
through 2035/2040 if justified and keep report_dates limited to the requested years.

## Comparable consensus (not obedience to consensus)

underwriting.consensus_review requires status=compared/unavailable/not_applicable
and assumption_ids. If unavailable, record the actual failed access or lack of
coverage; do not fabricate estimates. Analyst agreement is never required.

A comparison contains id, scenario, metric (revenue/ebit/earnings_proxy_per_share),
period_start (exclusive model boundary), period_end, as_of, available_at, currency,
unit=absolute, external_value, external_basis, model_basis, assumption_ids.
Revenue and EBIT are summed from the actual executed matching periods. EPS proxy
sums (EBIT-interest-cash tax)/end-period shares; it is NOT GAAP/adjusted attributable
EPS. EPS comparisons always need basis_reconciliation with assumption_ids and
adjustment_to_model_basis; for other metrics it is required when bases differ.
Show adjustments for SBC, investment income, minority attribution, taxes and share
averaging explicitly in the referenced rationale. Never claim an adjusted $2.25
estimate validates a GAAP/operating $0.79 model without a bridge.

A >20% difference or nonzero model versus zero consensus requires separately
reviewed difference_explanation.assumption_ids. Estimates older than 90 days require
refresh; do not relabel the retrieval date as the estimate date. Preserve each
provider's original snapshot/definition and discuss conflicting observations.
material_issues entries have id, status=unresolved/resolved, description and (when
resolved) assumption_ids. Unresolved material issues withhold a price conclusion.

## Explain the result

Show quality, commercial potential, financing and price separately. Bull is the
optimistic scenario supplied, never the highest possible future price. All annual
values are conditional. In an operating model without dividends, the plotted path
can primarily be the same terminal value rolled back at cost of equity; it is not
six independent market-price predictions. Explain this beside the annual table.

Reconcile disagreement by business drivers, duration of growth, margin, capex,
working capital, ownership, discount rate and terminal assumptions. Compare DCF to
explicit multiple sensitivities, but do not attribute a sensitivity to an analyst
without that analyst's model. No automatic scenario weights, momentum-based price
premium or fixed 25x/five-year claim about what the market requires. Reverse driver
sensitivity holds other assumptions fixed and must be re-underwritten for capacity
and funding. Multiple methods are cross-checks, not independent votes to average.

An internally reconciled result is not independently verified. Primary source
reading and a substantive research_review are still required. Existing fixtures
and passing tests validate mechanics, not a 99% investing edge.

CLI with an audited real dossier: `python -m fundamental_engine plain PLAIN.json --valuation VALUATION.json --dossier DOSSIER.json`. The two valuation inputs must match exactly.
