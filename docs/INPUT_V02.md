# Input extensions in v0.2

`schema_version: 1` remains backward compatible; new sections are optional.
The full executable example is `examples/business_financing_demo.json`.
All money/shares are absolute units. Dates are ISO days. Null means unknown.
The engine's annual rows now accept explicit GAAP or IFRS; growth across a basis
change is not calculated. Conversion between accounting bases is not inferred.

## Common evidence metadata

New records carry `source_ids` and `reviewed_at`. All referenced sources must exist
and be available at the research cutoff. Sources may carry `origin_id`, identifying
the ORIGINAL issuer/document family, not the republishing website. An origin that
is not supplied does not count as independently corroborated evidence.

A retrospective example's review date represents its simulated cutoff; the source
also records its actual retrieval date and retrospective review mode. Do not claim
that the example was really researched on its historical cutoff date.

## events[]

Fields: id, description, cause, observed_at, reviewed_at, source_ids,
management_claim, cash_effect (`lost|delayed|noncash|unknown`), recurrence
(`isolated|repeated|unknown`), evidence[], impacts[], recovery_requirements[], falsifiers[].
Causes: fx, divestiture, acquisition, seasonality, comparison, delivery_delay,
recognition_timing, strike, restructuring, customer_loss, competition,
technology_disruption, demand, legal, asset_sale, other.

Evidence: claim, role (`supports_temporary|supports_structural|recovery|attribution`),
status (`supported|unverified|contradicted`), source_ids, reviewed_at.

Impact: globally unique id, metric (`revenue|ebit|net_income|cfo|capex`), period_end,
signed reported_effect or null, classification
(`booked_exceptional|comparability|counterfactual|unknown`), measurement
(`disclosed|analyst_estimate|unknown`), review_status, source_ids, reviewed_at.
See MODELS.md for acceptance rules. Do not duplicate the same economic effect
under two impact IDs. The engine only detects identical IDs automatically.

## business

Common evidence metadata plus description, optional revenue_driver, claims[],
opportunities[]. Claims carry kind, claim, status, reviewed_at, source_ids.
Kinds: customer, supplier, moat, competition, segment, geography, capital_allocation,
value_chain. Status uses supported/unverified/contradicted.

Driver nodes: label, unit, op. For `input`, add value, source_ids, reviewed_at.
For `sum|product`, add children (2..30). Depth capped at 12. Missing or future
leaves propagate missing values. Sum units must match; product units require review.

Opportunity: name, claimed_stage, reviewed_at, source_ids, stage_evidence[],
milestones[], shareholder_risks[]. Stages are idea, prototype, pilot,
paying_customers, repeat_orders, scaling, proven_unit_economics. Stage evidence
has stage, claim, status, source_ids, reviewed_at. Highest evidenced stage is
reported separately from the claimed stage, with no success probability.
Milestones carry due_at, success_criterion, failure_criterion and optional cost.

## valuation.scenarios[] additions

`reinvestment_mode: sales_to_capital` selects growth-linked net reinvestment.
Each year then requires sales_to_capital and optional base_net_reinvestment
(default zero). Legacy `absolute` mode still requires reinvestment per year.
Do not feed both modes' investments into the same economic forecast.

## ownership_valuation

assumptions_as_of, source_ids, cash, debt, shares, minimum_cash, other_claims,
cost_equity, terminal_capitalization_rate, terminal_growth, terminal_roic,
sbc_policy=`cash_equivalent_expensed_no_extra_dilution`, scenarios[].
Each scenario: name, rationale, years[]. Other model assumptions may be overridden
in the scenario. base_revenue is always supplied from eligible annual financials.
Year: growth, operating_margin, cash_tax_rate, sales_to_capital,
base_net_reinvestment, debt_interest_rate, debt_repayment, dividend, financing[].
Financing: kind=`equity|debt`, commitment=`committed|hypothetical`, gross_amount,
fee_rate, and issue_price for equity. Consult MODELS.md before interpreting values.

## market_context

reviewed_at, source_ids, start, end, return_basis=`total_return`, stock_return,
benchmark_return, benchmark, beta, beta_method, confounders_reviewed (boolean),
confounders (list), liquidity_reviewed (boolean; must be true for a hypothesis flag).
Returns are fractions, not percentages. No default beta estimation or price fetch.

## Corpus document

id, title, locator, origin_id, published_at, available_at, kind, body.
Kinds: filing, earnings_release, transcript, commentary, research, user_note.
Extra metadata is retained: for example author, language, conflicts_of_interest,
classification_notes. A creator's trade/opinion is not a company filing.
Documents cannot be overwritten under the same ID; revisions use a new ID.
Use source-import with the same metadata without body for TXT/MD/searchable PDF.
