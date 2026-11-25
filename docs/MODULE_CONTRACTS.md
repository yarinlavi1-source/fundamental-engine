# Module contracts (v0.11.1 pipeline)

Shapes every pipeline module consumes and produces, read from the code on 20.10.2026. Read with docs/ARCHITECTURE.md.
All functions are pure (no network; the only clocks are review's `today` and monitor/ledger system time), stdlib only,
JSON-serialisable outputs (no NaN/inf), and never read a fact that `facts.usable` rejects (published after `fb['as_of']`
or about a period ending after it).

Field status (dataqa, analysis): `verified | reported | single_source | conflict | missing | stale` (weakest first:
conflict < missing < stale < single_source < reported < verified). Source kinds (facts.KINDS): `filing_derived`,
`issuer_document`, `primary_document`, `document` (legacy issuer_document), `vendor_normalized`, `market_feed`, `consensus`,
`secondary`, `synthetic`; `facts.REPORTED_KINDS` = filing_derived, issuer_document, primary_document, document.

## facts (FactBook accessors used by every stage)

`facts.ttm(fb, metric) -> {"value","period_end","method": quarters|annual_plus_ytd|latest_fiscal_year,"facts","signs",
"alternatives"?,"newer_quarters_unmatched"?,"ytd_rejected"?}` or None; `value == sum(sign * fact value)`, payments by
magnitude. A year to date ending more than 366 days after the latest fiscal year is rejected (`ytd_rejected`).
`facts.candidates/get/value/series(kind=annual|quarter|instant)/latest_instant`, `facts.usable(fb, fact)`,
`facts.item_usable(fb, item, missing=False)`, `facts.fiscal_year(end)` (calendar year of the end; ends on Jan 1-7 count as
the prior year). Adapters: `from_fmp_as_reported`, `from_sec_companyfacts`, `from_alpha_vantage`, `from_fmp_estimates`
(dated no earlier than retrieval unless `dated_snapshot=True`; the asked date is kept in `as_of_requested`), `set_price`
(refuses a date after as_of), `add_qualitative`, `add_qualitative_bundle` (see ARCHITECTURE: availability rules).

## pipeline

```
collection_plan(ticker, as_of, cik=None) -> {"ticker","as_of","folder","calls": [{"provider","tool","args","file"}],"rules"}
factbook_from_run_dir(raw_dir, company, as_of) -> FactBook      # MANIFEST retrieved_at_utc read; anything dated after
                                                                # as_of is skipped and listed in fb['collection_notes']
valuate(fb, qa, fc, inputs=None) -> {"status","case","result","diagnostics": {discount_rate_band, implied_cost_of_equity: {s: ...},
        reverse_price, stress, sensitivity},"what_must_be_true","capital_check","errors": [{"diagnostic","error"}]}
        or {"status": "not_computed","reason"}
run(fb, inputs=None) -> casefile {"casefile_version": 1,"engine_version","company","as_of","factbook_sha256","inputs_sha256",
        "errors": [{"stage","error"}],"stages": {stage: ok|failed},"collection_notes","qa","an","card","fc","val","rk","rv","dc",
        "business","segment_view","management","analyst_sheet","scenario_reasons","casefile_sha256"}
summary(cf) -> {"symbol","as_of","verdict","verdict_status","price_claim_withheld","confidence","quality","risk_level","price",
        "bear_value","base_value","bull_value","base_irr" (also the owner-facing expected return, decision SAME_SCALE),"bear_irr",
        "convergence_return" (report field: price converging to the base fair value path, distributions reinvested),
        "decision_version","margin": marginal|clear|None,"hurdle",
        "value_at_required_return","review_status","casefile_sha256"}
```
`inputs` = {"card"?: model_card inputs, "forecast"?: forecast inputs, "reviewer"?: review input, "decision"?: decision inputs,
"analyst"?: name or list of names, "analyst_sheet"?: evidenced readings {question: {reading_he, evidence_ids}}, key_risk and
scenario_events {bear|base|bull: [{event_he, evidence_ids}]} (analyst_sheet.py; evidence_ids a list or one id as text;
what cannot be read is listed in analyst_sheet.scenario_events_rejected, never raised; not bound to the reviewer's
signature, and they change no number)}. `scenario_reasons` is not a stage: it is built last from the finished casefile (a failure is
recorded inside the key as status 'failed'), so no other key changes; the casefile hash covers it. `run` snapshots the inputs before any stage reads them and passes
`review.inputs_digest(val, snapshot)` and `review.input_authors(snapshot)` to `review.audit`. The casefile's own
`inputs_sha256` hashes every input including the reviewer; it is NOT the value a reviewer signs (see review). A stage that
raises ValueError/KeyError/ZeroDivisionError/TypeError is recorded as failed and later stages degrade.

## dataqa.validate(fb, capital_structure=None, options=None) -> qa

```
{"status": "ok"|"limited"|"blocked", "as_of",
 "fields": {name: {"field","status","value","unit","as_of","sources": [source_id],"facts": [{"metric","period","source_id","value"}],
                   "checks": [codes],"note","blocking": bool, "basis"?, "components"?, "rejected_value"?, "method"?, "period_end"?,
                   "signs"?, "incomplete"?, "assumed_zero"?}},
 "checks": [{"code","status": pass|fail|skip,"severity": critical|major|minor,"message","detail": {...},"period"?,"fields"?}],
 "resolved": {"shares_outstanding": {"value","as_of","basis": cover|balance_sheet|vendor_float|capital_structure,"status",
                                     "basic_economic_shares"?,"rsu_shares"?,"economic_shares_as_of"?},
              "cash"|"short_term_investments"|"long_term_investments"|"restricted_cash": {"value","as_of","basis","status","source_ids","definition"},
              "debt": {"value","as_of","method": noncurrent+current|tagged_total|tagged_plus_current|capital_structure,"decisive",
                       "decisive_basis","status",...},
              "lease_liabilities": {...}, "deferred_revenue": {"value","as_of","components"?,"source_ids"?,"definition"?},
              "net_cash": {"value","as_of","status","formula","net_cash_incl_long_term_investments","lease_liabilities_excluded",
                           "restricted_cash_excluded","definition","dates_differ"} | {"value": None,"status": "missing",...},
              "definitions": {...}},
 "blocking": [field names], "limited": [field names],
 "splits": [{"from","to","factor","residual_ratio","evidence","decided_by": corporate_action|price|per_share|price+per_share}],
 "unresolved_splits": [...]}                     # unresolved: decided_by conflicting|ambiguous|untested; counted as issuance
```
Critical fields: `shares_outstanding, cash_and_liquid_investments, investments, debt, convertibles, post_period_events,
revenue_ttm, operating_income_ttm, cfo_ttm, capex_ttm, sbc_ttm`. `conflict`/`missing` on shares_outstanding,
cash_and_liquid_investments, debt, revenue_ttm or cfo_ttm blocks; the others limit (convertibles/post_period_events block
only when an instrument exists without its terms). A TTM older than twice its age limit is `missing`. Negative cash or
short-term investments: critical `liquid_assets_nonnegative` failure, field `conflict` with `value` None and the number in
`rejected_value` (resolved cash and net cash None); negative long-term investments: same, but `investments` only limits.
TTM fields carry facts.ttm's value unchanged (`ttm_matches_facts` check). Check codes: annual_recency, balance_sheet_identity,
capital_structure_crosscheck, capital_structure_lookahead, capital_structure_valid, cash_flow_tie, cash_rollforward,
convertible_terms, cross_source, debt_rollforward, debt_scope, dilutive_instruments_without_terms, gross_profit_identity,
issuer_revision, large_share_change, liquid_assets_nonnegative, liquid_assets_within_current_assets, lookahead_excluded,
net_income_is_vs_cf, possible_split, post_period_review, quarters_sum_to_annual, research_conflict, secondary_spread,
share_continuity, share_routes, shares_not_wavg, split_unresolved, staleness, ttm_matches_facts.
`options` = {"tolerances": {metric: relative}, "max_age": {shares|balance_sheet|ttm|annual: days}, "resolutions": [{"field",
"metric","period_end"?,"resolution": label_difference|use_source,"source_id","reason","evidence_ids"}]} (pipeline
`inputs.dataqa_resolutions`). A resolution acts only on the fact-level cross-source comparison of the named field and metric
(`dataqa.RESOLUTION_METRICS`): label_difference records the named source's value as an explained definitional difference
(the chosen value does not move); use_source (period_end required) chooses the named source at that date, only when that
resolves a cross-source disagreement (one that resolves none, e.g. values within tolerance, is rerun without choosing: unused,
the chosen value does not move). It needs FILING evidence (a filing-derived source, an issuer document of the filing dataset,
or an item citing one, usable at as_of); a rejected or unmatched one changes nothing. Every one is recorded: `qa["resolutions"]` (status applied|unused|rejected, `why`,
`applied` comparisons; the key exists only when resolutions were supplied), an `analyst_resolution` check, and on the field
`analyst_resolutions` with a note; a field resting on an applied one is listed in `limited`. The review digest binds them.
Inside one source family (two transcriptions of the same filing: companyfacts XBRL vs the filing read directly) a resolution
applies only when its evidence cites the filing DOCUMENT itself (an issuer document of the filing dataset, or an item citing
one; `dataqa._primary_filing_evidence`, 0.14.2; the document must carry retrieval primary_document, a search excerpt of it does
not count); a filing-derived copy alone never settles a disagreement between copies. The cash rollforward allows, at each end,
half the rounding unit of a balance reported as a whole multiple of 10^6..10^8 (`_rounding_unit`, capped at 0.1B: a whole
number of billions is not evidence of rounding to the billion; ASTS's cash including restricted, tagged to 0.1B, against a flow
to the dollar).
Split evidence (0.14.2): dated split records with the same factor (within SPLIT_BAND) inside ONE count step are one corporate
action (the issuer's XBRL tag, dated by its context up to ~90 days from the event, and a market feed's effective date): later
ones are listed under the kept event's `same_event` and not multiplied in (`_same_split_once`); `_split_factor_of` counts them
twice only where that product, and not the single factor, leaves the residual count ratio inside SPLIT_ISSUANCE_BAND (two real
splits of one factor in one step). A dated split whose residual stays outside SPLIT_ISSUANCE_BAND is not decisive
(corporate_action 'ambiguous', `corporate_action_note`): the price and per-share tests decide, else the step stays unresolved
(ServiceNow's 5-for-1 had read as 25).

## analysis.analyze(fb, qa=None, archetype=None, market_cap=None) -> an

```
{"analysis_version": 1, "as_of", "currency", "years": [period_end ...ascending],
 "annual": {period_end: {"revenue","gross_profit","operating_income","net_income","cfo","capex","fcf","sbc","fcf_after_sbc",
            "d_and_a","buybacks","dividends","acquisitions","shares_diluted_wavg","shares_outstanding","cash","investments","debt",
            "equity","goodwill","intangibles","invested_capital","deferred_revenue","nwc","invested_capital_asset_side",
            "total_assets","current_assets","current_liabilities","ppe","interest_expense","pretax_income","income_tax",
            "fiscal_period","debt_method"}},                                     # None where not available
 "ttm": {"period_end","method","label", <TTM keys and ratios>, "balance_basis","balance_as_of": {...}, "dataqa_mismatch"?: [...]},
 "metrics": {name: {"value","unit": ratio|USD|x|years|count,"formula","inputs": [...],"period","status", ...}},
 "forensics": scores.scorecards(...) + {"status": "computed","archetype","note","roic"?} | {"status": "unavailable","reason"},
 "flags": [{"code","severity": high|medium|low,"message","kind"?,"evidence"?}], "history_rows": [history.py rows],
 "acquisition_years": [...], "assumptions": [{"code",...}], "market_cap": {"value","basis","used_for"}, "definitions": {...}}
```
Metrics: revenue_cagr_3y, revenue_cagr_5y, revenue_cagr_full, revenue_growth_last, revenue_growth_series, growth_volatility,
acquisition_spend_pct_revenue_3y (`window`, `window_kind` fiscal_3y|fiscal_3y_plus_ytd, `year_to_date`), gross_margin,
gross_margin_trend_3y, operating_margin, operating_margin_trend_3y, operating_margin_best_3y, fcf_margin, fcf_after_sbc_margin,
sbc_pct_revenue, cfo_to_net_income, accruals_ratio, capex_pct_revenue, capex_to_da, roic, roic_ex_goodwill,
incremental_roic_3y, sales_to_capital_incremental_3y, nwc_pct_revenue, deferred_revenue_pct_revenue, net_cash,
net_debt_to_ebitda, interest_coverage, share_count_cagr_3y (`split_basis`, `unverified_split_like`, `splits_outside_window`,
`net_change_3y` split-adjusted, `net_change_3y_unadjusted`, `excluded_split_pairs` [{..., adjusted_ratio, applied, basis,
reason}]), diluted_wavg_share_cagr_3y, buybacks_pct_fcf_3y, sbc_offset_by_buybacks, quarterly. With a dataqa result the
share metric divides out exactly `qa['splits']`; without one, a split-like step counts as issuance unless the FactBook's own
price or EPS evidence supports a split (flag `possible_split`). Flags: accruals_high, acquisition_affected_growth,
acquisition_current_year, cash_conversion_weak, deferred_revenue_funded, dilution, fiscal_calendar_gap,
interest_coverage_low, leverage_high, margin_compression, negative_fcf, possible_split, sbc_heavy, ttm_inconsistent (high:
dataqa and analysis TTM disagree). Debt scope follows dataqa: the tagged total only when the roll-forward supports it.

## model_card.build(fb, an, qa, inputs=None) -> card

```
{"card_version": 1, "ticker", "as_of", "currency",
 "business_model": {"primary","secondary": [...],"reasons","override","computed": {"primary","secondary","evidence","text_financials"},
                    "evidence","text_matches","signature": {... "liabilities_to_assets","revenue_to_assets",...},
                    "flags": {"text_financials","possible_financial","pharma_text","lender_balance_sheet","financial_label","lender_shaped"}},
 # an infrastructure KEYWORD (TEXT_RULES 'cable', 'wireless', ...) with capex below INFRA_CAPEX_MIN (6%) of revenue yields to
 # the same label's next type or the filer's SIC_FALLBACK type (0.14.2, Amphenol's 'cable assemblies'); reason recorded
 "stage": {"value": early|growth|maturing|mature|decline|turnaround,"reasons","computed","override","confidence","data",
           "evidence_needed","cross_check"},
 "cyclical": {"value": bool,"normalize": bool,"basis": data|industry_flag|industry_flag+peak|analyst|None,"industry_flag",
              "data_shows_cycle","computed","override","reasons","margin_volatility","position"?: peak|mid|trough,
              "history": scale_up|loss_grower|pre_commercial|None (a history that is not cycle-shaped, history.history_shape
                         over the card's window: no position and no flag peak unless an analyst confirms a cycle),
              "peak_question": str|None (a flagged industry at a possible peak that is not read as a cycle peak: a non-cycle
                               history, or a loss year on normally growing revenue; None once an analyst settles it)},
 "capital_intensity": {"value": low|medium|high|unknown,"reasons","data"},
 "funding": {"value": self_funded|needs_external|unknown,"reasons","data"},
 "playbooks": [...], "research_focus": {"profile_stage","key_metrics","traps","packets"},
 "driver_tree": [{"driver","why","metric","data_status","for_type",...}], "driver_coverage": {"available","total","forecast_basis"},
 "valuation": {"method","archetype": a valuation.ROUTES key,"route","why","cross_checks","limitations","requirements","confidence",
               "intended_method","default_for_type","override_reason","computed": {"method","archetype","intended_method"},
               "funding_flag"?: {"basis": capex_exceeds_cfo|refinancing,"capital_intensity","funding","why"},
               "override_rejected"?: {"method","reason","why_rejected"}},
 "understanding": [{"id","question","critical","status": evidenced|asserted|open,"answer","evidence_ids"}],
 "dimensions": {moat|switching_costs|pricing_power|retention|customer_concentration|market_size|competition|substitutes|
                management|capital_allocation|governance|regulation|technology_ai|geopolitics:
                {"assessment": strong|adequate|weak|unknown,"evidence_ids","note","source": computed|analyst}},
 "completeness": {"status": complete|partial|insufficient,"missing","rule"}, "notes": [...], "meaning"}
```
Methods: fcff_annual_path, normalized_cycle_fcff, scenario_survival_fcff, residual_income, nav, rnpv, sotp,
not_reliably_valuable. Business-model types: subscription_software, platform_marketplace, semiconductor,
hardware_manufacturing, capital_intensive_infrastructure, consumer_brand, industrial, financials, pre_revenue_biotech,
conglomerate, reit, commodity, or `unclassified`. Industry text decides the type (sector only as a fallback).
Lender-shaped (non-financial label, liabilities >= 85% of assets, revenue <= 30% of assets `LENDER_TURNOVER`, capex < 10% of
revenue or unknown `LENDER_CAPEX`, and no cost-of-revenue line or lending data `LENDING_KEYS`; fee-platform labels
`FEE_FINANCIAL` need lending data): primary becomes `financials` (residual_income, or not_reliably_valuable without book
equity) and an FCFF method_override is rejected. A profitable company (operating margin and CFO positive in the latest two
fiscal years) that needs outside money keeps fcff_annual_path / normalized_cycle_fcff with `funding_flag`, not the survival
model. `normalize` (not `value`) decides normalized-cycle valuation.
`inputs` = {"business_model"?: {"primary","reason","secondary"?,"evidence_ids"?,"fiduciary_balance_sheet"?},
"stage"?: {"value","reason"}, "cyclical"?: {"value": bool,"reason","evidence_ids"?}, "assessments": {dimension:
{"assessment","evidence_ids","note"}}, "understanding": {qid: {"answer","evidence_ids"}}, "method_override"?:
{"method","reason","archetype"?}}. Overrides without a reason raise; unknown keys are listed in `notes`. Public helpers:
clean_text, text_type, cyclical_word, platform_signature; constants FEE_FINANCIAL, LENDER_TURNOVER, LENDER_CAPEX,
LENDING_KEYS, NON_OPERATING_TYPES, MIXED_RULES.

## forecast.build(fb, an, card, qa, inputs=None) -> fc ; forecast.what_must_be_true(spec, price)

```
{"forecast_version": 1, "status": built|method_not_supported_by_forecast|insufficient_data|invalid_spec, "reason", "method",
 "spec": drivers.build_case spec (driver_version 1; scenarios bear/base/bull with reinvestment_rule, per-period
         equity_issue_price, terminal {growth, wacc, cost_of_equity (the terminal's own rate behind wacc), roic, operating_margin,
         tax_rate, other_claims, net_runoff_obligation, debt_weight, ke_leverage_premium, debt_tax_shield_rate, ...}; interim_boundaries;
         opening {..., convertibles?, nondeductible_debt?};
         sources; assumptions; underwriting
         {material_issues, opening_reconciliation, consensus_review, scenario_reviews}),
 "spec_sha256", "assumptions": [{"id","topic","kind","provenance","values": {"bear","base","bull"},"rationale","falsifier",
                                 "source_ids","review_status"}],
 "anchors": {..., "ttm","year_to_date","current_fiscal_year","stub","growth","cycle","sbc_basis","reinvestment_identity",
             "route": {"method","moat","moat_basis","moat_weights","cycle_path","cycle_peak","cycle_treatment",...},
             "calendar": {"boundaries","periods","interim","fifth_anniversary","report_dates",...}},
 "paths": {"bear","base","bull"},
 "cost_of_equity": {"bear","base","bull","terminal_cost_of_equity": {s},"terminal_wacc": {s},
                    "components": {"risk_free","equity_risk_premium","table_rate",
                    "unlevered","asset_unlevered","adjustments": [...],"adjustment_<factor>","formula","notes",
                    "terminal": {"table_rate","dropped": [rows],"kept": [factors],"asset_unlevered","cost_of_equity",
                                 "tax_shield_rate": {s},"formula","reason"},
                    "leverage": {"ke","kd": expected,"kd_expected","kd_promised","market_debt_to_equity","net_debt",
                                 "model_equity","debt_weight","d_to_e","terminal_tax_shield_rate": {s},"issue_prices","status","basis",...}}},
 "funding": {s: {"status","debt_assumed","equity","equity_raised","issue_prices","issue_price_rule","price_dependent",
                 "liquidation","investments_liquidated","settled","sbc_settled_in_shares","sbc_settlement_shares",
                 "new_financing_shares","value_if_issued_at_own_value"? (base), ...}},
 "overrides": [...], "preview": {...}, "warnings": [{"code","severity","message"}], "moat_proxy": {...} | None, "notes": [...]}
what_must_be_true -> [{"lever","label","status": solved|not_reachable,"required","anchor","unit","meaning","detail"}]
```
`terminal.wacc` is built from `terminal.cost_of_equity`, the mature table (the explicit table without the stage and one-off
funding add-ons, `forecast.TERMINAL_KE_DROPPED`) relevered at the same D/E; the explicit years and the roll-back of the
terminal value use the scenario's `cost_of_equity`.
`terminal.debt_weight` is 0 for a forecast case (its terminal rate is the business's own; the relevering fixed point stays in
`cost_of_equity.components.leverage.debt_weight`, on the model's own base equity value, never the quote; capped at 60% with a
warning). `terminal.ke_leverage_premium` = the explicit Ke's relevering premium (Ke - table; 0 under an analyst Ke override):
a required return / IRR is on the return scale Ke - premium (`valuation_tools.rate_anchor`), the verdict scale; the
owner-facing expected return is that verdict-scale base IRR (`decision` expected_return.owner_facing_return, SAME_SCALE; the
debt premium is charged in the fair value, so it is not added); expected_return.convergence_return (the base case converging to
the base fair value path by the five-year date, distributions reinvested at the market return) is a separate report field;
expected_return.ke_scale (IRR + premium) is internal only. `opening.nondeductible_debt` =
debt-like claims of kinds `forecast.NONDEDUCTIBLE_CLAIM_KINDS` (preferred_or_minority, pension_deficit): `valuation.operating`
deducts interest only on the debt above it and gives the terminal shield only on the horizon debt above it. Funding status adds `share_settled`.
Case sources (`spec.sources`): {id, title, url, kind, published_at, available_at, retrieval?, published_at_estimated?}; kind
from the FactBook kind (filing_derived -> filing, vendor_normalized -> provider_data, market_feed -> market_data, consensus ->
analyst_estimates, secondary -> secondary_source, issuer_document -> its dataset: filing|issuer_release|earnings_release,
primary_document -> its dataset: regulator|official_statistics); the source's own `retrieval` passes through when it is one
of primary_document|provider_normalized|search_excerpt|market_feed; `published_at` is the document date when the FactBook
has one (a date after as_of gives `invalid_spec`), else the first availability of its facts with `published_at_estimated`.
`inputs` (KNOWN_INPUTS): {"overrides": [{"path","scenario": bear|base|bull|all,"value","reason","evidence_ids"}],
"reviews": {assumption_id: {"status": reviewed|unreviewed,"reviewer"}}, "risk_free"|"equity_risk_premium": {"value","as_of",
"source_id" | "title","url"?,"kind"?,"retrieval"?}, "horizon_years": 1..30, "financing": assumed|assumed_debt|none,
"nol": opening tax-loss carryforward (number), "cyclical": {"treatment": structural|cyclical,"reason","evidence_ids"},
"current_year_revenue": {"value","fiscal_year_end","basis","evidence_ids","source_tier"} (a tier other than guidance or
consensus is a 'model' row with its own tier), "acquired_revenue": {"value","fiscal_year_end","acquisition","closed","reason",
"evidence_ids"} (bought revenue in the step into the fiscal year in progress, kept out of the stub's growth reinvestment; not
applied, warned high, when it closed after the balance sheet; capped at the step, and at the step above organic growth when
the filings give that growth, warned medium `acquired_revenue_capped`), "debt_like_claims": [{"kind" (DEBT_LIKE_KINDS),"amount",
"reason","evidence_ids"}] (claims ahead of the shares the debt lines miss, added to opening debt; from an evidence pack as
forecast_options.debt_like_claims, citing the company's own documents; kind finance_leases without a sales_to_capital or
growth_capex_pct override warns `finance_leases_reinvestment_basis` high), "options": [{"kind" vested_options|warrants,"count",
"strike","reason","evidence_ids"}] (0.14.2: vested options at their weighted strike -> case `opening.options` [{id, count,
strike}]; valuation._roll_back exercises them by the treasury method at each date's value per share, (v x shares + count x
strike) / (shares + count) where v > strike, and company_path prices likewise on the year's weighted shares, recording
`price_before_options`), "capital_link": {scenario: {"roic","source_tier","evidence_ids","reason","competitors"?}} (0.14.2:
pipeline builds it from company_path scenarios in multiple_from 'economics'; for each linked scenario without an override of
terminal_roic / terminal.roic a terminal_roic override at min(roic, 1.0) is added with the path's tier and evidence, and without
an override of sales_to_capital / growth_capex_pct / maintenance_capex_pct the explicit sales-to-capital equals the terminal
rule ROIC / (final EBIT margin x (1 - tax)) from the first year; driver_sources records the path's tier; a path roic above 1
warns `capital_link_capped`), "build_phase": {"start_margin" -1..0.6,"depreciation_pct" 0..1,"sbc_pct" 0..1,"reason",
"evidence_ids","source_tier" guidance|contracted|own_history} (0.14.2: the evidence must include the filing DOCUMENT, an
issuer document of the filing dataset or an item citing one, `forecast._filing_document_evidence`: structured companyfacts alone
is refused; tier guidance needs a guidance item among evidence_ids; applied to a company in the build-phase shape, TTM operating margin <= BUILD_PHASE_MARGIN_MAX -50% or capex >=
BUILD_PHASE_CAPEX_MIN 100% of revenue, where tier own_history must lie within BUILD_PHASE_LOAD_BAND 10% of revenue of the
engine's own measured load (TTM where measured, else the last fiscal year); to any other company only when today's own loads
fail in period 0 (`PathImpossible.period == 0`, not a later margin path) and every value is within that band; elsewhere it is
dropped, warned `build_phase_not_applied` high. The forecast error names build-phase only for a period-0 failure or a company
in the shape; a later failure says the scenario's margin path is impossible. The paths start from the evidenced margin and SBC load; depreciation_pct is the D&A load from the first period AND the
maintenance level (no D&A roll-off is added to the margin, so it never raises the operating margin path); warned
`build_phase_start`. Without it, when the last fiscal year's D&A load makes
the cash-cost ratio leave its bounds and the margin is on the TTM basis, the load of the same trailing twelve months is used,
warned `da_load_ttm`; otherwise status method_not_supported_by_forecast names the build_phase input), "bank_drivers" (0.14.3,
residual-income route only; `forecast_financials.check_bank_drivers`, ValueError on refusal): {"bear"|"base"|"bull": {"roe_start"
-0.3..0.6, "roe_year5" -0.3..0.6, "roe_terminal" 0.005..0.4, "book_growth_start" -0.1..0.4, "payout"? 0..1, "terminal_reason"
(required when roe_terminal is above the cost of equity), "source_tier" own_history|guidance|contracted (guidance needs a
guidance item), "reason", "evidence_ids"}, "opening"?: {"book_equity" (common, the FactBook basis: equity less preferred and
AOCI), "shares"?, "as_of", "reason", "evidence_ids" with the filing DOCUMENT}, "regulatory_capital"?: {"risk_weighted_assets",
"required_capital_ratio" 0..0.3, "cet1_capital"? (reported CET1; the book-to-CET1 gap is held as deductions so the check is CET1 >= ratio x RWA), "rwa_growth_start"? -0.2..1 (evidenced RWA growth, fading like book growth; else RWA follow book), "as_of", "reason", "evidence_ids" with the filing DOCUMENT}}. Without analyst opening shares, value per share uses the latest diluted weighted-average share count when it is above the point-in-time common count (warned `diluted_shares_used`). A scenario set replaces the
own-history ROE path (start, year 5, terminal; linear as before), starts book growth at book_growth_start (fading to terminal
growth by year 10; above BOOK_GROWTH_CAP allowed only while book_growth_start <= roe_start x (1 - payout), else refused) and
uses payout as the distribution floor (payout = max(floor, 1 - book growth / ROE)); without payout it is the history, or with no
history 0 (warned `bank_payout_from_growth`; `payout_unknown` only when a scenario still takes the 50% default). opening applies
only when dated after the FactBook's last balance sheet and on or before as_of (else `bank_opening_not_applied`), within 0.5-2x
that book (else refused), warned `bank_opening_applied`; its shares replace the point-in-time count. regulatory_capital fills
risk_weighted_assets (growing with the scenario's book growth path) and required_capital_ratio in every period (the valuation's
funding_blocked check), replaces `regulatory_capital_not_checked`, and warns `capital_below_requirement` (high) when the
opening book is below the requirement. Applied scenarios are assumption rows of kind analyst and `bank_drivers_applied`;
absent, the case is unchanged; on the operating route `bank_drivers_not_applied`. forecast_options.bank_drivers carries it
from the evidence pack}, "country_risk_unmeasured" (0.14.3, set by the pipeline for a foreign filer whose requested country
row has no exposure data): {"reason"} -> material issue `country_risk_unmeasured` on both routes (residual income: on
`fc.material_issues`), resolved by reviews.country_risk. Evidence-pack routes (0.14.3): forecast_options.classification_review
{reviewer, reason, evidence_ids (filing)} -> reviews.classification; forecast_options.revenue_geography {shares, basis, as_of,
reason, evidence_ids (issuer document)} -> inputs.market_context.geography. The operating route's lender check reads the
card's `business_model.flags.operating_asset_base` (a low note `lender_check_operating_base` instead of `possible_lender`). Raw override path
`equity_issue_price` (0.14.2): a positive number or one per boundary, at source_tier contracted only and only with an
equity_proceeds override at tier contracted in force for that scenario (else ValueError); it applies only to the periods whose
contracted equity_proceeds are positive (every other period keeps the quote and the issue-price fixed point); contracted
equity_proceeds are net proceeds, so that scenario's equity_fee_rate is 0 (its equity is locked: no assumed raise is added).
equity_proceeds / debt_draw overrides whose tiers are all 'contracted' make the scenario's financing_status 'committed', any
other tier 'assumed' (never 'none' with money in it); a raw `growth` list shorter than the spec's periods (0.14.2) is one value
per fiscal year from the next one (analyst.md), spread onto the periods of that year (a continuation part 0, as the engine's
own path), and the fiscal years after the list fade from its last value to terminal growth at the scenario's half-life
(`growth_list_faded`, low); a list longer than the fiscal years, or of another length for another raw path, is refused with
the period count and ends; an assumed-financing revert keeps the analyst's own locked financing. The fiscal year in progress: an input, else consensus, else
fiscal-year guidance, else the reported year to date + the company's dated guide for the NEXT unreported quarter + the
quarters after it (sequential at the guided step, seasonal at its year-over-year growth on last year's quarters, or flat:
`anchors.current_fiscal_year.quarter_guide` {rule, quarter, guided, sequential_growth, year_over_year_growth,
remaining_quarters, ...}), else the year-to-date extrapolation. Bought revenue in the current year's step (the input, else
the ASC 805 pro forma reading) is recorded in `anchors.stub.acquired_step_excluded` and `revenue_change_reinvested`. A stub-only
debt draw no larger than the free cash flow after distributions reported since the balance sheet carries
`funding[s].stub_draw_immaterial` (no material issue, no external-funding add-on, decision `gates.funding_immaterial`). A rate typed into the inputs (no
FactBook source_id) becomes case kind `analyst_input` (any other stated kind kept in `stated_kind`; undated ->
`published_at_estimated`); a kind in PRIMARY_CLAIMS (filing, earnings_release, issuer_release, regulator,
official_statistics, facts.REPORTED_KINDS) or an unknown retrieval raises ValueError. Unknown top-level keys are ignored
with an `input_ignored` warning; unknown keys inside a known input raise. `reviews` are author notes: the review stage
treats every reviews signer as author-side. Material-issue ids include data_blocked, opening_dates_differ,
cyclical_peak_unconfirmed, moat_proxy, possible_lender, leverage_at_cap, convertibles_unapplied, assumed_financing_<s>.

## valuation_tools (diagnostics used by pipeline, risk and decision)

`rate_anchor(case, scenario='base') -> (anchor, premium)`: the return scale of every absolute required return / IRR: anchor =
Ke - `terminal.ke_leverage_premium` (Ke for a hand-built case). A required return h sets explicit Ke = h + premium and moves
the terminal WACC by (1 - w) x (h - anchor), so the rates at h do not depend on the financing.
`debt_weight(case, scenario='base') -> (w, source)`: the scenario's `terminal.debt_weight` (capped at `W_CAP` 0.95), else
opening net debt / (net debt + base value x opening shares), 0 without net debt or for a non-operating case.
`shifted_value(case, scenario, shift, w=None)`, `discount_rate_band(case, scenario='base', shift=.01, w=None) ->
{"scenario","shift","value_lower_rate","value","value_higher_rate","relative_width","quote_inside_band","debt_weight",
"debt_weight_source","terminal_wacc_shift","meaning"}`, `implied_cost_of_equity(case, scenario='base', low, high, w=None) ->
{"status": solved|not_bracketed|invalid_search_domain,"implied_cost_of_equity" (Ke + shift: the scale of
"model_cost_of_equity", equal to it when the quote equals the scenario value),"model_cost_of_equity"?,"implied_return_scale"?
(anchor + shift: the premium-free verdict scale, monotone in debt; what decision._irr uses),"model_rate_return_scale"?,
"scale"?,"shift"?,"terminal_wacc_shift"?,"debt_weight","debt_weight_source","leverage_premium","limitation",...}`: the cost of
equity moves by the shift and an operating terminal WACC by (1 - w) x shift; the band is centred on the model value. The CLI
`value-drivers --diagnostics` and the MCP `valuation_diagnostics` tool return this dict unchanged. `reverse_price`, `stress_test`, `forecast_score`, `asset_schedule` unchanged.
`valuation.sensitivity` (the report's sensitivity grid) still moves the terminal WACC 1:1 (docs/LIMITATIONS.md).

## risk.assess(fb, qa, an, card, fc, val) -> rk

```
{"register": [{"id","category","description","description_he"?,"evidence_ids","class": disqualifying|pricing|accepted_priced,
               "severity": high|medium|low,"in_bear_case","status","assessed","relevance"?,"levers","pricing_status","pricing_basis",
               "priced_via_effect"?: {"add_on","base_value","base_value_without_add_on","value_effect","material_threshold"},
               "valuation_error"?: true, "withhold_price"?: true, "evidence_gap"?: true, "values"?}],
 "failure_scenarios": [{"name","kind","mechanism","observable_signs","value_per_share"?,"loss_from_price"?,"gap_vs_price"?,"status",
                        "source","not_more_severe_than_bear"?,"severity_note"?}],
 "downside": {"price","bear_value","loss_to_bear","bear_vs_price","permanent_loss_value","loss_to_permanent","permanent_loss_case",
              "not_more_severe_than_bear": [names],"not_computed","meaning"},
 "error_size": {"label","level": low|medium|high,"value_range_rel","discount_rate_band","discount_rate_band_source","terminal_share",
                "reasons","process_gaps": [{"code": data_blocked|data_limited|understanding_insufficient|understanding_partial|
                dimensions_unassessed,"severity","detail","fields"?|"ids"?}],"scope","rule","formula"},
 "disqualifying": [ids], "evidence_gaps": [ids], "withhold_price": [ids],
 "summary": {"disqualifying","pricing","accepted_priced","partially_priced","unassessed","valuation_error"},
 "bear_levers", "liquidity", "pricing_materiality", "redteam": {"verdict","usable","critical_failed","critical_unverifiable",
 "capital_structure_source","by_construction"}, "meaning"}
```
`pricing_status`: priced (in the executed bear path, or a cost-of-equity add-on whose removal raises base value by at least
max(half the base-to-bear markdown, 10%), 15% when the markdown is unknown: `risk.rate_effect`), partially_priced (smaller or
unquantifiable add-on, not in the bear path), not_priced, open_question (unassessed dimension), gate (disqualifying). Class
`accepted_priced` only for `priced`. `val.wide_range` / `val.terminal_share` repeat error_size triggers and carry
`valuation_error: true` (size of possible valuation error, not a company risk). `val.base_rate.*`, `val.wmbt.*` and
`gov.*` entries carry `description_he`. Evidenced going-concern doubt, material weakness or restatement creates `gov.*`
entries with `withhold_price`. error_size uses valuation triggers only; process gaps lower confidence elsewhere. A
negative cash figure is never liquidity or runway. Public names: rate_effect, WMBT_UNREACHABLE_HE, ERROR_SCOPE,
variant_spec, fund_with_equity(case, name, price, detail=None), failure_scenarios, error_size, liquidity.

## review.audit(fb, qa, an, card, fc, val, rk, reviewer=None, today=None, inputs_sha256=None, authors=()) -> rv

```
{"mechanical": {"dataqa","dataqa_blocking","dataqa_limited","valuation_audit": {..., "author_review_notes"?,
                "full_signoff_would_close"?,"reaudit_with_reviewer"?,"basis"?},"redteam","consistency": [...]},
 "reviewer": the input or None,
 "reviewer_check": {"present","valid","independent","errors","reviewed","rejected","unresolved_critical","unresolved_major",
                    "bound_to_valuation","bound_to_inputs","accepted","risk_overrides","self_review"},
 "binding": {"valuation_sha256","inputs_sha256","covers": ["valuation input_sha256","inputs.decision","inputs.card","inputs.forecast"],
             "supplied_by","meaning"},
 "risk_overrides_applied": [ids], "price_conclusion_allowed": bool, "status": reviewed|unreviewed_draft|blocked,
 "reasons": [...], "reason_codes": [...], "open_items": [...], "meaning"}
```
Reviewer input: {"reviewer","statement","reviewed_at" (ISO, between the valuation cutoff and `today`),"independent": true,
"valuation_sha256" (= executed input_sha256, mandatory),"inputs_sha256" (= `rv['binding']['inputs_sha256']`, mandatory),
"assumption_reviews": {id: reviewed|rejected},"findings": [{"severity","text","resolved","response"?}],
"risk_overrides"?: {risk_id: {"justification" (>= 5 words),"evidence_ids"}}}. `accepted` = valid AND independent AND bound to
the valuation AND bound to the inputs. `review.inputs_digest(val, inputs)` hashes the executed valuation's input_sha256 with
`inputs.decision`, `inputs.card` and `inputs.forecast` (BOUND_INPUTS; empty = none; reviewer and analyst excluded); `audit`
called without `inputs_sha256` binds to `inputs_digest(val)`. Self-review: a reviewer whose normalized name (case-folded,
sorted word tokens, bracketed remarks dropped) equals an author-side signer (`authors` = `review.input_authors(inputs)`:
inputs.analyst and every inputs.forecast.reviews signer, plus every case/spec assumption `reviewer`) is not independent and
is listed in `self_review`. A risk override releases a `withhold_price` flag only when, ignoring the flag's own evidence ids,
every cited id is FactBook filing evidence (`decision._evidence_class` in RELEASE_CLASSES: filing_document, filing_dataset)
and at least one is a filing document (issuer_document, primary_document or document). Author `review_status` values are
notes; only an accepted reviewer's sign-offs re-run the underwriting audit. Reason codes: dataqa_missing, data_blocked,
valuation_not_executed, is_demo, underwriting_incomplete, primary_evidence_missing, review_required, assumption_rejected,
review_incomplete, funding_blocked, valuation_status, understanding_insufficient, risk_not_assessed, disqualifying_risk,
governance_red_flag, redteam_not_usable, consistency_failure, critical_finding_unresolved; HARD codes give `blocked`.

## decision.decide(parts, inputs=None) -> dc

```
{"decision_version": 3, "as_of", "company", "price",
 "assessments": {business_quality|trajectory|price_attractiveness|risk_and_error|growth_quality|financial_strength|
                 management_capital_allocation|data_uncertainty: {"level": strong|adequate|weak|unknown,"level_he","reasons",
                 "evidence", "override"?, "mechanical_level"?, ...}},
 "mechanical_assessments": {name: level},
 "expected_return": {"price","base_irr","bear_irr","bull_irr","base_irr_bound","irr_status","decomposition","index_expected_return":
                     {"value","low","high","provenance","origin","comparator": engine_consistent|generic_assumption|user_assumption,
                     "source","components","basis"},"hurdle","required_return",
                     "hurdle_rule","hurdle_basis","risk_premium","premium_components","premium_capped","permanent_loss_premium",
                     "model_cost_of_equity","model_cost_of_equity_floor": {"value","binding","basis"},"at_required_return":
                     {"base","bear","bull","loss_to_bear","rate","semantics"},"value_at_required_return",
                     "base_value_at_model_cost_of_equity","fair_value": {"value","bear","bull","cost_of_equity","change_from_price",
                     "label","semantics"} (the headline: the executed base value at the model cost of equity),"buy_below_price":
                     {"value","status","required_return","reason","change_from_price","label"} (= value_at_required_return),
                     "expected_price_path": {"status","rate" (blend of the verdict-scale base IRR and the market),"base_irr",
                     "market_return","comparator","pull_to_market","from_price",
                     "from_date","rows": [{"years","date","price"}],"band","hit_rate_within_band","formula","semantics"},
                     "yearly_at_model_cost_of_equity": {"rate","model_cost_of_equity","rows","semantics"},
                     "price_for_hurdle","five_year_at_required_return","band": {"hurdle_low",
                     "hurdle_high","step","error_level","error_widening","price_low","price_high","rows","bear_tolerance_step",
                     "moat_proxy"?,"rule"},"debt_weight","debt_weight_source","owner_aspiration","meets_owner_aspiration_14pct" (on base_irr),
                     "owner_aspiration_basis": "base_irr",
                     "owner_facing_return": {"value" (= base_irr),"basis": "base_irr","leverage_premium_base","semantics"}
                     (decision SAME_SCALE: the expected annual return at today's price the owner reads; render.hebrew_table adds
                     one note when leverage_premium_base > 0: the debt risk is charged in the fair value, so it is not added),
                     "convergence_return": {"status": computed|unavailable,"base","at_fair_value","at_buy_below_price",
                     "market_return","base_minus_market","date","years","base_value_at_date","cumulative_distributions_base",
                     "distributions_with_reinvestment","distributions" (how: reinvested at the market return | summed),
                     "reinvestment_rate","total_value_with_distributions","model_cost_of_equity","formula","assumes",
                     "semantics"|"reason"} (decision SAME_SCALE, a separate report field, NOT the expected return:
                     ((V5 + sum_i D_i (1 + m)^(T - t_i)) / price)^(1/years) - 1, the price converging to the base fair value
                     path by the five-year date, each base distribution reinvested at the market return m; non-increasing in debt
                     at a fixed price on the tested sweeps; its level inherits the engine's calibration bias; shown in one
                     labelled table note and the paragraph's five-year clause; no verdict reads it),
                     "ke_scale": {"bear","base","bull","base_bound","leverage_premium": {scenario: p},"model_cost_of_equity",
                     "market_return","market_return_same_scale","required_return","required_return_same_scale",
                     "base_minus_market","base_minus_required","scale","semantics"} (INTERNAL diagnostic, never shown to the owner:
                     each scenario's IRR + its relevering premium p; its level rises with debt at a fixed price, decision SAME_SCALE),
                     "semantics","mechanical"?},
 "verdict": {"code","label_he","gate","reason","reason_he","what_would_change_it","what_would_change_it_he","hurdle_used",
             "risk_premium_used","allowed_overrides","status": final|draft,"price_claim_withheld","withheld_reasons","confidence",
             "margin": marginal|clear,"margin_detail": {"status","flips": [{"test","side","threshold",...}],"verdict","text","text_he",
             "hurdle_range","value_range","price_inside_value_range","rule","applies_to"?},"margin_of_safety"?,"price_for_hurdle",
             "value_at_required_return","conditional_on"?: {"kind": funding|share_settlement,"items","price_dependent","text",
             "text_he"},"overridden_from"?},
 "mechanical_verdict": {...same shape...}, "overrides": [{"target","value","justification","evidence_ids","status","reason",
             "direction"?: upgrade|downgrade|same,"material"?,"effect"?}],
 "confidence": {"level": low|medium|high,"level_he","reasons","reasons_he","rule"},
 "valuation_view", "risk_pricing": [{"id","status","basis","value_effect","register_class","register_pricing_status",
             "register_pricing_basis","valuation_error"}],
 "gates": {"data_blocked","disqualifying","impaired","valuation_failed","no_valuation","understanding","evidence_gaps",
           "review_blocked","valuation_unverified","funding","funding_conditional","material_issues","red_flags","red_flags_released",
           "withhold_price","withhold_price_released","reviewer_present","draft","draft_codes",
           "funding_immaterial"? (only when a bear/base stub draw is immaterial: [{"scenario","draw","period_end","cover","text","text_he"}])},
 "rules", "business_reason_he", "semantics"}
```
Verdict codes: attractive_at_price, quality_but_expensive, promising_funding_conditional, interesting_not_yet_valuable,
insufficient_evidence, index_preferable, avoid (precedence: decision.VERDICT_RULES). Required return = max(index return +
permanent-loss premium, model cost of equity). IRR and every value at a required return move the terminal WACC by (1 - w) x
the shift. Funding gates only a blocked plan or an unresolved `assumed_financing_<s>` issue; a reviewed priced financing path
or SBC settled in shares is valued with `conditional_on`; a stub-only debt draw the forecast marks `stub_draw_immaterial` is
neither (named in `funding_immaterial` and in the verdict reason). Any unresolved forecast material issue (case, else spec
underwriting) keeps the verdict a draft and is named (`withheld_reasons`, confidence); for possible_lender and leverage_at_cap
the paragraph shows no value. With `fc['moat_proxy']` the band adds a no-proxy row and the margin test re-runs the verdict
without the proxy. `reviewer_present` is true only for an accepted reviewer bound to the valuation and the inputs.
`inputs` = {"index"?: {value, basis, source, low?, high?} (never below the risk-free rate), "overrides"?: [{"target":
assessment|verdict|red_flags,"value","justification","evidence_ids"}]}. `price_for_hurdle(case, hurdle, w=0.)`.

## render

`render.markdown(casefile) -> str` (full report) and `render.hebrew_paragraph(casefile) -> str` (one Hebrew paragraph, 80-140
words, drafts labelled). `render.hebrew_table(casefile) -> str` (the owner's table; the yearly table's columns are
דובי / בסיסי / שורי and one condition line per scenario follows it). `render.value_display(casefile)` is the one rule for
whether values are shown (the table and scenario_reasons use it). Under the company's yearly table its flags' `text_he` are
notes (company_path 0.14.2: `capital_intensity_gap` when the path roic and the DCF's terminal ROIC differ by more than
CAPITAL_GAP_TOL 2 points; `year1_carries_gap` when the base's year-0 EPS x multiple is more than YEAR0_GAP_NOTE 25% from the
quote, so year 1's change is mostly that gap: its Hebrew note names only the quote, never a second 'worth today' number beside
the fair value; `roic_at_or_below_growth` when an economics-mode roic is at or below the executed terminal growth: that scenario's
rows are not priced (economics_unavailable), the others still are).

## evidence_pack (pack -> FactBook bundle / pipeline inputs)

Money in a scaled unit (0.14.2): facts, segments, KPIs and guidance whose `unit` names millions / billions / thousands
('USD_millions', 'USD_mn', 'USD bn', 'USDm', 'USDbn', 'EUR thousands', 'EURk') are converted to the base unit when the pack is read (`evidence_pack._to_base_unit`; NBIS's revenue
guidance 3,000-3,400 'USD_millions' had started the forecast at $3,200 of revenue). forecast_options.build_phase takes tier
guidance / contracted / own_history only, with a sec_filing source among its evidence (directly or through an item); drivers.equity_issue_price tier contracted only.

## ticker_run.value(run) -> value.json

`{"ran","valued","failed_at","not_valued_he"?,...}`: `valued` (and `ran`, 0.14.2) need the forecast, valuation (and when inputs
carry company_path, company_path) stages ok AND fc.status 'built', a computed valuation with a finite base value today (not
funding_blocked) and a computed company_path; `not_valued_he` is one plain Hebrew line (ticker_run.NOT_VALUED_HE: build phase
only for a build-phase forecast reason, forecast, funding, funding_scenario when only bear and/or bull are blocked (it names the
scenario), leverage, no value, path, stage); a stage
that ran without valuing gives `failed_at` 'not valued: forecast method_not_supported_by_forecast: ...'. `ticker_run.status`
reports 'not valued: ...' as the draft reason and `valued` beside `engine_ran`.

## scenario_reasons

`scenario_reasons.build(casefile) -> {"reasons_version": 1,"status": computed|partial|not_available,"company","as_of",
"currency","values_shown","draft","why_no_values_he","source": {"values","rate","model_cost_of_equity"},"scenarios": [{"name",
"label_he","status","condition": {"kind": mechanical|analyst_events,"if_he","events": [{event_he, evidence_ids, unknown_ids,
hypothesis, hypothesis_he}],"drivers"},"note_he","rows": [{"date","when_he","value","cumulative_distributions","fiscal_year",
"drivers": {revenue, revenue_growth, operating_margin_after_sbc, eps, fcf_per_share, diluted_shares},"driver_sources",
"implied_pe": {value, eps_next_fiscal_year, next_fiscal_year_start, next_fiscal_year_end, reason},"then_he","why_he"}],
"assumption_ids": {scenario, periods, terminal, scenario_reviews, review_status},"analyst_overrides","gaps": [{code, detail}],
"thesis","why_no_values_he"}],"gaps","definitions","rounding","meaning"}`. Reads only: values are copied from
`dc.expected_return.yearly_at_model_cost_of_equity` (else `val.result.annual_values` through the fifth anniversary), drivers
from `val.result.scenarios[].forecast` (interim splits merged into fiscal years) and `fc.paths/anchors/funding/cost_of_equity`.
`scenario_reasons.render_he(reasons) -> str` is `out/scenarios_he.md`. The review section names red-team checks that pass by construction; it does not yet print
`rv['binding']`.

## screen, portfolio, monitor, ledger

`screen.rows_from_fmp_screen | rows_from_pit | rows_from_sec_frames | rows_from_factbooks` -> universe rows
`{"symbol","name","sector","industry","market_cap","data_grade": screening|verified,"as_of","status","metrics","basis",
"errors",...}`; rows may lack `net_debt` and `ev` when debt is unknown (never defaulted to 0).
`screen.scan(rows, options=None) -> {"screen_version","as_of","universe","dimensions": {symbol: {business_quality,
growth_quality, financial_strength, valuation, expected_return_proxy, priced_in, risk, data_uncertainty, ...}},
"dimension_meanings","lenses": {quality_compounder|quality_fair_price|early_commercial_growth|mispriced_turning|
structural_tailwind: [candidates]},"lens_rules","near_misses","shortlist","shortlist_concentration","excluded","no_data",
"notes","options","policy","input_sha256"}`; `expected_return_proxy` component `owner_fcf_yield` with `yield_basis`
((FCF - SBC) / market cap); `priced_in.multiple_basis` EV/NOPAT or market cap/owner FCF with `cross_check`; risk codes
include cyclical_peak and consensus_downside; lens conditions include not_cyclical_peak and per_share_funding.
`screen.rank_changes(prev_scan, new_scan)`, `screen.implied_growth(multiple, options=None)`.
`portfolio.from_ib(positions_payload, summary_payload=None, as_of=None, fx=None, tolerance=0.005) -> book` {"kind":
"portfolio_book","private": true,"read_only": true,...} (never written into Git; account ids never copied or used as
symbols); `portfolio.analyze(book, holdings_info=None, options=None) -> {"as_of","read_only","weights","concentration",
"exposures","overlaps": {"clusters","common_factors","offsets",...},"scenarios","drivers","review_needed","holdings","params",
"notes"}`; `portfolio.compare(candidate, book_analysis, alternatives=None, options=None) -> {"candidate","options",
"diversification","comparisons","assumptions","notes"}` (options only, no orders; order-shaped payloads refused).
`monitor.thesis_from_casefile(cf, clock=None, options=None) -> thesis`; `monitor.check(thesis, new_fb, new_an=None,
price=None, options=None) -> {"thesis_id","as_of","new_data","changes","impact": {business_quality|value|expected_return|
price_only},"alerts","confirmations","price","not_checked"}`; `monitor.freeze(ledger, thesis, clock=None) -> {"thesis_id",
"made_at","written","skipped","rejected","ledger"}`; `monitor.score(ledger, actuals=None, options=None) -> {"items",
"by_metric","by_thesis","recurring_errors","unresolved","not_scored","evaluated_at","limitations"}`.
`ledger.Ledger(path, clock=None, max_recording_lag_days=7)`: append-only, hash-chained; refuses made_at more than the lag
before the system recording time; late rows are excluded from coverage, returns and learning.
`holdings_watch.update(positions, ledger_path, as_of, factbook_for, price_for=None, casefile_for=None, root=None) ->
{"holdings_update_version","as_of","positions_as_of","rows": [{"ticker","weight","status": new_holding|data_unavailable|
no_new_report|light_update|full_rerun_needed|index_fund|valuation_only,"position_currency","position_currency_assumed",
"picture_date","view": {"price","base_value_today","discount_to_base","expected_return": {"value","kind": implied_irr|proxy}},
"value_comparison": {"source": fundamental_picture|ledger_thesis|null,"date","path","base_value_today","base_basis",
"base_is_weighted","bear_value_today","bull_value_today","discount_to_base","outside_band","last_year","buy_test_price",
"margin_of_safety_price","verdict_he","expected_return","price_at_valuation","skipped_sources": [{"code": currency|unit|
price_scale|no_base_value,"reason","reason_he","source","date"}]},"newer_saved_run": {"date","path"},
"detail": {"changes","falsifiers_tripped","confirmations","alerts","to_check"},"value_signal","conclusion","start_from",
"line_he"}],"removed","skipped_non_stock","index_funds","counts","full_rerun","meaning"}`: the latest frozen thesis per ticker
(union of the ledgers by thesis_id) checked with `monitor.check` against a fresh FactBook; full_rerun_needed on a
high-severity alert or a tripped assumption falsifier. `factbook_for(ticker, as_of, thesis)` is injected (CLI:
`sec_factbook_for`, SEC companyfacts, the saved copy unless `--refresh`); `holdings_watch.render_he(res)` is the Hebrew table
(a value column with its source and date, and a detail list of the saved pictures used). No revaluation, no orders.
Rows added 15.11.2026: `index_fund` (a fund is listed and never valued: an explicit ETF / FUND / INDEX type on the position
row, or the named set `INDEX_FUNDS` for an untyped or stock-typed row; never by name pattern) and `valuation_only` (no frozen
thesis but a saved fundamental picture: its values are shown, no filing check).
`holdings_watch.fundamental_picture(root, ticker)` reads the newest `research/technical/<T>/<date>/fundamental.json` (the
folder name is its date) and uses only the standard keys: `price`, `currency`, `unit` / `share_unit`, `share_basis`,
`values.today.{bear,base,bull}` (else `values.weighted_base_today`, marked weighted), the latest `values.end_<YYYY>` (read as
31 December of that year: a fiscal year that ends in another month must not use this key), `buy_test_price`,
`margin_of_safety_price`, `verdict_he`, `annual_return_pct.base`. Every key is optional; a picture without a base value in
these keys is skipped with the reason `no_base_value`. `value_comparison` takes the picture when it is newer than the ledger
thesis, else the thesis; a value in another currency or share unit, or a position price beyond `PRICE_SCALE_LIMIT` (1.8x) of
the source's own price, is not compared and the reason is printed instead. The expected return of a picture row is a proxy:
(last year-end base value / price)^(1/years) - 1, distributions excluded. The filing check still runs on the ledger thesis.
`newer_saved_run` names a dated valuation folder later than the value shown that this table does not read.
`forecast_tracker`: replaced on 16.11.2026 by tracker version 2, which scores frozen ledger rows only (see "lean path"
below; version 1 scored a rerun of the params file and no longer exists).
`research_store.save(run, root='.', ledger_path=None)` copies a finished run's essentials (inputs, evidence pack, position
parts, reviews, table_he/scenarios_he, identity.json, understand/business.json, the four gather fragments, the casefile summary
with assumption ids and values, and `casefile_core.json`: `research_store.casefile_core(cf)`, the extract
`monitor.thesis_from_casefile` reads, same thesis_id and irr_basis as the full casefile, about 40-50 KB) into
`research/companies/<T>/<as_of>/`, updates `latest.json` and appends the thesis and its forecasts to
`research/forecast_ledger.jsonl` (deduplicated by thesis_id / id; rows kept verbatim, the chain is verified on the source; the
thesis row is read from there by thesis_id). The full casefile is not kept (rebuild it from the saved inputs.json with the same
engine version). `research_store.load(ticker, root='.')` -> the newest saved valuation, else the newest
`runs/tickers/<T>_<date>` with a casefile labelled `saved: false` (`unsaved_run`), else None. A save is a reuse source:
`scripts/ticker_prefetch.py T D --reuse research/companies/<T>/<as_of>` (and `--reuse auto` falls back to it when no runs/
folder qualifies); within 30 days its fragments are copied (dated by the folder), older only its judgement files under
reuse/. Company research only: no positions. `holdings-update --positions` rows: {t, shares, price, value, weight, avg_cost?,
sec_type?}; t also as ticker/symbol, sec_type also as asset_class/secType (non-stock rows are skipped and counted).

## econ (business economics, phase A; not called by the pipeline)

Registry and intake: `econ.registry()` (validated `brain/economics.json`, cached by file sha256), `econ.reading_plan(model_id,
fb=None)`, `econ.required_kpis / factor_tags / falsifiers / checks(model_id)`, `facts.add_kpi(fb, kpi_id, period, period_end,
value, source_id, available_at, *, kpi_basis, scope='total', currency_basis='reported', unit=None, period_start=None)`,
`econ.kpi_table(fb, model_id, as_of=None)`.
```
econ.review(case, model=None, prior=None, *, upstream=None) -> {"company","period_end","as_of","currency","period",
  "economics_version","registry_sha256","model": {"primary","secondary","source"},"prior_review","status": assessed|not_assessed,
  "check_window","models": [{"model","role","label_he","price_cyclical","core_status","core","missing","residuals"}],
  "kpis","lines","identities","growth_splits","flags","readings","measures","overlays": {overlay_id: {...}},"falsifiers",
  "open_questions","checks_not_run","unavailable","not_read","input_sha256","engine_version","boundary"}
econ.evaluate_identity(case, model_id, identity_id, scope='total'); econ.growth_split(case, model_id, scope='total')
econ.select(card_type, profile_text, kpi_ids, override=None, *, segments=None, evidence=None) -> {"primary","secondary",
  "overlays","status": selected|not_assessed,"source": rule|analyst,"reasons","rule","override","candidates","inputs",...}
econ.select_case(case, card_type=None, profile_text=None, override=None); econ.select_factbook(fb, card_type, override=None, ...)
econ.vendor_financing | chain(case, upstream=None) | acquisition_sbc | bank_capital | contract_coverage | segments(case, model=None)
econ.process_evidence(categories, intensity, gross_margin=None); econ.expansion(split=None, top_n=None, ndr=None)
econ.render_he(review, translation=None, *, names_he=None) -> {"how_it_earns","price_requires_units","risk_fallback","note"}
  # each a Hebrew text or None; translation (WP10's translate_requirement) refused in phase A
econ.price_vs_estimates(revisions, prices, *, as_of=None) -> {"check","metric","target_period","basis","currency","unit",
  "as_of","from","to","revisions_between","estimate_change","price_change","implied_forward_multiple_change",
  "reading": multiple_moved_more|estimate_moved_more,"reading_rule","text_he","boundary","input_sha256","engine_version"}
econ.econ_review(case, options=None) -> {"company","period_end","as_of","selection","review","text_he",
  "price_vs_estimates","text","input_sha256","engine_version","boundary"}
  # options: model | card_type, profile_text, override; prior, upstream; names_he; revisions + prices
```
`quarter.quarter_review(case)` with `case['economics']` (the econ case subset; company, period_end, as_of and currency
inherited, period quarter) adds `economics` (the review, `checks_run_by_quarter`, `quarter_link`), merges its flags as
`econ.<key>` and feeds its falsifiers. Every function is pure and deterministic; nothing is fetched or filled in.

## lean path (scripts/ and the modules they call; 15-16.11.2026)

The daily valuation path (runbook: docs/LEAN_PATH.md). The scripts live in `scripts/` (not in the wheel) and import
`fundamental_engine` from the checkout; the modules below are pure unless a line says otherwise. Company research only:
nothing here reads or writes positions except `portfolio_health` / `due`, whose output goes only under `runs/`.

`scripts/lean_model.py` (also imported as a module: `sys.path` + `import lean_model`). Params: the lean format
(`REQUIRED`: price, rf, erp, beta, beta_mature, shares_m, net_cash_m, rev0_m, base_year, tax, cash_yield, roic_terminal,
buy_return, probabilities {bear, base, bull}, scenarios {bear|base|bull: {g[6], m[6], conv[6], pe, shares_m?, charge?,
funding?}}; also read: as_of, base_year_end (ISO), days_to_base_year_end, years_to_horizon, debt_rate, bands, calibrated,
worlds; extra keys such as notes, guidance, judgement, tags, reconciliation are ignored by the model).
```
check_params(p) -> (errors, warnings, dates)          # six numbers per list, probabilities summing to 1 (1e-6), positive
                                                      # price/shares/rev0, fractions where meant, charge keys 0-5
prepare(p) -> (params with derived dates, inputs)     # raises ParamsError(errors); hand dates kept unless base_year_end
                                                      # differs by more than DATE_TOLERANCE_YEARS (0.02)
reprice(p, price=None, as_of=None) -> params copy + "repriced" {price_from, as_of_from, as_of, dates_moved, note, held}
                                                      # dates move only with base_year_end; the saved file is never written
run(p, worlds=None, base_dir=None, source=None) -> {"scenarios": {s: {value_today, year_end_values, annual_return_pct,
        dcf_today, multiple_today, revenue_m, op_margin, eps_operating, eps_with_interest, ke0_pct}},"weighted_value_today","weighted_end_value","weighted_return_pct","index_return_pct","index_parity_price",
        "buy_price","two_lens","calibrated_diagnostics","price_ladder": {method, horizon_year, years_to_horizon, price,
        end_value, rungs: {margin_of_safety_13 (13%, MARGIN_OF_SAFETY_PCT) | buy_test (12%) | margin_of_safety (the file's
        buy_return; meaning 'company hurdle') | index | hold_floor (6%) | sell_line (3%): {label, return_pct, ours,
        calibrated, meaning?, note?, same_as?}}},"buy_test": {rule, price, legs {base_ours|base_calibrated|bull|bear: {return_pct,
        threshold_pct, pass, max_price}}, complete, pass, binding_leg, max_price_passing, max_price_vs_price_pct,
        rerun {calibrated_max_price, max_price_passing, binding_leg, lower_than_stated, note}},"ranges","funding",
        "sensitivity","sensitivity_base","sensitivity_terminal","reverse_reading","market_implied_growth_pct",
        "market_implied_end_revenue_m","inputs": {dates used, warnings, repriced},"dated_values": {year-ends with ISO
        dates; at +1, +2, +5 years},"method_gap": {per scenario DCF alone / exit multiple alone, warnings},
        "return_with_distributions","warnings","worlds"? }
```
Wiped scenarios: value 0 and return null in the 15.11 keys, the 0.01 floor in the older keys. `worlds`: per world the
values, dated values, returns, buy test, 13% price and file sha256, plus the weighted values, lenses, ladder and buy test
(its rerun price holds each world's own multiple); every world must share price, as_of, base_year and the two date keys.
`funding` per scenario: {equity_raise_m {year index: $M}, issue_price (number | "model"), debt_raise_m, debt_rate, why}.
CLI: `params [--summary] [--out F] [--price P] [--as-of D] [--params-out F] [--worlds F]`; exit 2 on a ParamsError.
Every existing key keeps its value for every saved params file (tests/test_saved_valuations.py).

`valuation_record` (research/companies/<T>/<date>/valuation.json, `SCHEMA` 'valuation_record/1'):
```
build_from_lean(params, result, inputs=None, files=None) -> record   # result must hold REPORTING_KEYS
adapt_bank | adapt_segment(params, result, inputs=None, files=None); adapt_three_lane(ticker, as_of, model_result,
        finish_result=None, inputs=None, files=None)                 # BNY, SPCX, META; NU = 'lean_remapped'
record_for_folder(folder, root, inputs=None, result=None, run=None) -> (record | None, spec)
latest_valuation(root, ticker, run=None) -> {ticker, as_of, folder, kind: lean|custom|engine|gather_only, model, record,
        record_source, note, older_folders} | None       # root = the research folder; newest dated folder of any kind
validate(record) -> [problems]                           # numbers are numbers or null (note fields carry the reason),
                                                         # ISO dates, probabilities sum to 1, entry price is a ladder rung
project_handoff(record, previous=None) -> fundamental.json  # every V1_KEYS key in its v1 meaning, schema_version 2, "v2",
                                                         # "carried_from_previous"
index_row(record, keep=None); numbers_block(record); replace_block(text, block); replace_index_row(text, ticker, row)
check_tree(root, run=None) -> [(kind, ticker, text)]     # the --check problems; reads only
```
Record: {schema, ticker, company, as_of, price {value, time_utc, source}, currency, share_basis {basis, model_shares_m,
known_split}, model {kind, params, result, code: {path, sha256}, reproducible}, scenarios {s: {probability, condition_he,
value_today, dated_values, at, annual_return_pct, wiped}}, probability_label, weighted, second_lens,
base_path, hurdles {horizon_date, years_to_horizon, rungs [{id, return_pct, price, price_second_lens, label}],
buy_test {price, stated_price, rerun_price, rerun_used, binding_leg, pass_at_price, complete, legs, rule, note},
buy_test_price (= buy_test.price: the rerun price when lean_model warns), base_12_price, margin_of_safety_price (the
13% rung), index_parity_price, hold_floor_price, sell_line_price, labels_he, entry_price {value, rung, note}}, display,
historical_multiple {label_he, company_type, basis, next_year_figure, median, p25, p75, months, value, low, high,
computed}, conditions / falsifiers [{id, kind exit|trim|add|too_low, metric, op, threshold, by_date, text_he, state}],
next_report {date | null, window {from, to}, confirmed, text, check_he}, last_fresh_pull, verdict {code, text_he, draft},
worlds?, notes_he, thesis_link, warnings}. Judgement no model computes comes from a thesis-inputs dict
(`THESIS_INPUTS_KEYS`) or `inputs_from_handoff(h)`. Nothing here fetches or writes.
`scripts/publish_valuation.py <lean_params.json | custom folder> [--thesis-inputs F] [--root R]` writes lean_result.json
(refused when a saved result is not reproduced), valuation.json, the handoff, the company's one index row and the thesis
numbers block between its markers; `--check [research]` reads only, exit 1 on any problem.

`lean_check.run_checks(lm, params, prev=None, facts=None, price=None, answers=None, lessons=None, market=None, today=None,
facts_cutoff=None) -> {"check_version","ticker","as_of","price","base_value_today","base_return_pct","buy_test_pass",
"buy_test_pass_at_quote","buy_test_price","counts","blocking","would_block_a_buy","exit_code": 0|1,"verdict",
"model_warnings","answers_given","inputs_supplied","items": [{id, group, status: pass|fail|open|not_applicable, blocks_buy,
detail, lessons, function, ...}]}`. lm = the lean_model module; facts = an SEC companyfacts payload; answers = {lesson id: one
line}; lessons = research/lessons.jsonl rows {id, date, company, what_went_wrong, category, check, enforcement, blocks_buy,
source, ask?}. Automatic items: inputs, lenses_agree, calibrated_lens, funding, equity_wiped, method_gap, on_the_line,
rerun_price, reverse_reading, distributions_band, probabilities_label, reconcile_revenue / shares / net_cash, guidance,
digestion_year, price, judgement, market_rates, prev_inputs, prev_levels; then the manual `ask.<id>` questions that apply.
Thresholds are module constants (TOLERANCE 3%, NET_CASH_TOLERANCE, PRICE_MAX_TRADING_DAYS 3). Never edits params.
`scripts/lean_check.py params [--prev] [--facts] [--price] [--answers] [--today] [--facts-cutoff] [--json] [--all]
[--save]`: lean_check_answers.json beside the params is read by default; --save writes lean_check.json there; exit 0/1,
2 when the params cannot run.

`lean_freeze` (research/ledger.jsonl through `ledger.Ledger`, opened with the lag refusal off):
`classify(folder) -> {kind: lean|custom|None, model?, params?, result?, reason?}`;
`freeze_folder(folder, ledger, *, root=None, run=None, lean_model_path=None, next_report=None, falsifiers=None, ...) ->
{status, thesis_id, rows_written, forecasts_written, rejected, made_at, on_time, late_recording, supersedes, not_forecast,
notes}`; `freeze(params_path, result_path, falsifiers=None, *, ledger)`; `freeze_all(root, ledger, *, next_reports=None,
skip=()) -> {frozen, left_out, ledger}`; `status(ledger) -> {verify, rows, theses, forecasts, outcomes, in_force}`.
Thesis id 'lean:<T>:<as_of>:<12 hex>'; the thesis row holds the params / result / lean_model hashes, the values and
dated values, the calibrated base, the buy test, the 13% and index prices, pre-review against post-review base, variants,
next report and on_time; forecast rows: base revenue, operating margin after SBC and operating EPS per model year with
the bear-bull envelope (numbers from the saved result, never a rerun). Unchanged inputs add nothing; changed inputs or a
newer valuation add a superseding thesis. `scripts/lean_freeze.py <folder> | --all | --verify [--next-report D]
[--next-reports F] [--falsifiers F] [--ledger L] [--root R]`.

`locked_rules` (pure; constants BETA_WEIGHT 0.67, BETA_PRIOR 0.33, BETA_WEEKS 104, MULTIPLE_MONTHS 60):
`beta_2y_weekly(prices, spy_prices, as_of=None, weeks=104) -> {raw, adjusted, weeks, window, correlation, short_history,
rule}`; `multiple_history(prices, figures, basis='forward_pe'|'ev_sales'|'p_tbv', months=60, as_of=None) -> {basis,
median, p25, p75, min, max, months, window, latest, skipped_nonpositive}`; `hist_multiple_value(company_type,
next_year_figure, history, net_cash_per_share=0, peer_multiple=None) -> {company_type, basis, anchor, next_year_figure,
multiple {median, p25, p75, months}, net_cash_per_share, value, low, high, peer_value, label_he, notes}` (types: stable_profitable, grower_growth_changed, low_profit, bank, cyclical,
levered_complex = no value); `validate_judgement(block, params=None, market=None) -> {ok, errors, warnings, rule}`;
`load_market(path=None)`, `market_in_force(market, as_of)`, `check_market(params, market)` over research/market.json
({schema, in_force: [{from, rf {value, as_of, ...}, erp {...}, index_return}], convention, update_rule}).
`scripts/locked_rules.py T [--as-of D] [--weeks N] [--save F]` fetches weekly closes (Yahoo chart API) and prints the
`judgement.beta` block.

`forecast_tracker` version 2 (pure; `scripts/forecast_tracker.py [--as-of] [--out research] [--ledger] [--offline]
[--refresh] [--no-ledger]` reads the ledger, the saved files, SEC companyfacts and the filing index, and writes
research/FORECAST_TRACKER_he.md, research/forecast_tracker.json and outcome rows):
`frozen_theses(rows) -> {ticker: [{thesis_id, ticker, as_of, made_at, recorded_at, record, forecasts, outcomes}]}`;
`file_check(record, now) -> same|rewritten|changed|missing`; `choose(candidates, reported_on) -> (candidate, 'test' |
'descriptive', reason)`; `company_rows(ticker, candidates, payload, today, price=None, release_dates=None, filings=None,
scorecards=())`; `score(ticker, params, result, payload, today, price=None, release_dates=None, params_path=None,
path=None, since=None, until=None)`; `check_scorecard(sc)`, `score_scorecard(sc, theses=None, path=None)` over
`SCORECARD_FORM` (research/companies/<T>/<date>/scorecard*.json: period, period_end, reported_on, sources, against,
items [{metric, model_value, actual, unit, kind actual|partial|guidance, forecast_id?, note}], falsifiers [{id, text,
state fired|not_fired|not_measurable, action_if_fired, acted_on, reason_not_acted, on_file}]);
`outcome_candidates(rows, theses_by_id)`, `review_changes(theses)`, `filing_events(recent, after, today)`,
`price_check(path, price, known_split=None)`; `summarize(rows)` (needs MIN_ROWS 8 scored rows from MIN_COMPANIES 3);
`build(rows, custom, as_of, sources=None, review=None, outcomes=None, ledger=None) -> {"forecast_tracker_version": 2,
"as_of","rows","custom_model","summary","falsifiers","review_bias","outcomes","ledger","sources","thresholds",
"meaning"}`; `render_he(doc)`. One row per company and reported period, scored against the latest valuation frozen
before the report day; anything else is 'descriptive only' and never counted. A partial year has numbers and no
ahead/behind word. Measurement only.

`portfolio_health` (private output; `write` refuses a folder outside runs/):
`load_rules(path=None, obj=None)` (runs/portfolio_rules.json: bands, caps, index funds, study fund, role and tags per
holding, `lagging_core_rule` hold_no_new_money | cap_6pct; drafted fields say 'draft: owner to confirm');
`health(positions, rules, root='.', as_of=None, holdings_update=None, lm=None) -> {"portfolio_health_version": 1,
"as_of","positions_as_of","positions_source","rules_source","lagging_core_reading","rules_drafts","rules_problems",
"bands_pct","portfolio": {total, cash, study_fund, index_funds, index_share, index_target, index_position, stock_share,
stock_part, stock_position, linked_exposure, ...},"rows": [{ticker, role, role_draft, tags, price, price_date,
valuation {as_of, folder, kind, model, record_source, age_days, older_folders}, reprice, lenses {ours, second
{.., not_second_opinion}, agree, decision_band, decision_basis, edge_distance_pts},
buy_test, next_report, next_report_passed, valuation_older_than_last_report, conditions, verdict_he, gaps, reasons}],
"meaning"}`; `write(doc, out_dir, root='.')` -> portfolio_health.json, portfolio_health_he.md;
`due(root, today, days=30, held=(), filings=None, tickers=None) -> {"due_version": 1,"today","days","until","rows",
"upcoming","passed_without_update","held_stale","no_date","held_6k_to_check","meaning"}`; `render_due_he(doc)`.
A lean holding is rerun with `lean_model.reprice` at the position price and date (worlds weighted the same way); a custom
model's saved horizon values are read at that price. Rule results, never orders. Scripts:
`scripts/portfolio_health.py --positions F [--rules F] [--as-of D] [--out runs/...]` (reads holdings_update.json from the
output folder when present) and `scripts/due.py [--days N] [--today D] [--positions F] [--out DIR] [--refresh] [--json]`.

`quality_momentum` saved-valuation gate (15.11.2026; the gates and ranking do not read it):
`saved_book(research_root) -> {root, folders, rejected}`; `saved_valuation(book, symbol, price, as_of, price_date=None,
lean=None) -> {state, date, kind lean|custom|engine|rejected, model, age_days, base_value, price_then, price, price_date,
buy_test_pass, buy_test_price, buy_test_price_saved, binding_leg, basis, basis_text, verdict, source, repriced, rejected,
note}`;
`attach_saved(rows, book, as_of, price_date=None, lean=None)`; `saved_he(sv)`, `saved_rows(res)`. A lean file is repriced
to the screen's close and rerun; a custom model is compared with its record's buy-test price; a heavy-engine valuation
or a price more than 1.8x away is 'unknown'. `scripts/quality_momentum_screen.py OUT --as-of D [--min-cap] [--reuse]
[--research R]`: SEC companyfacts per company (not XBRL frames), the Nasdaq listing feed and Yahoo closes; screen.json
gains `saved_valuations` and the counts `passers_saved_*`, `near_misses_saved_*`.

`quality_momentum` screen v2 (25.11.2026; price is information, never a gate): `price_info(t, spy) -> {rel_strength,
vs_ma200, ma200_rising, below_high} | None`; `parse_estimates(surprise_json, forecast_json, momentum_json) ->
{surprises, years, momentum, gaps}` (three Nasdaq analyst pages, data by Zacks); `eps_revision(est, history, as_of) ->
{fiscal_year_end, now, then, pct, window_days, source nasdaq_month|own_history, state ok|not_positive|no_data}`;
`business_gates(est, history=(), as_of=None) -> [eps_revisions, earnings_beats]` (pass None = missing: the row is
incomplete); `classify(quality, business)`; `needs_business(quality)`; `divergence(price_info, business)` (flag
`price_down_business_up`) and `divergence_rows(lists)` (the report's top section: passers in rank order, the other
lists by 12-month revenue growth); `rank(rows)` on `RANK_KEYS` (a missing `eps_revision_pct` gets the median);
`history_lines(est, as_of)`; `symbol_variants(symbol)`; `calendar_days(as_of)`, `parse_calendar(day, payload)`,
`earnings_radar(calendar, rows, book, as_of) -> {reported, upcoming}`. Row keys: `business`, `price_info`,
`trend_measures`, `rel_strength`, `eps_revision_pct`, `eps_revision_window_days`, `surprise_min_pct`, `divergence`,
`flags`, `estimates`; screen.json keys `divergence`, `radar`, `gaps.estimates`, `gaps.calendar`. The script also writes
`OUT/estimates/<SYMBOL>.json`, `OUT/calendar/<day>.json` and `<parent of OUT>/_estimates_history/<SYMBOL>.jsonl`.

`research_store.latest(ticker, root='.', full=False, run=None)` (CLI `research-latest T [--full]`): the newest dated
folder of research/companies/<T>/ of any kind through `valuation_record.latest_valuation`, with {ticker, as_of, folder,
kind, model, record_source, note, older_folders, saved, summary {price, currency, value_today, annual_return_pct,
second_lens, buy_test_price, margin_of_safety_price, verdict_he, next_report, variants}, handoff, heavy_engine_save
{as_of, folder, thesis_id, superseded, note}, next}; no dated folder gives the older `load` answer. `load` keeps its
meaning (latest.json of research-save).
`holdings_watch` (16.11.2026 additions): the value comparison reads the valuation record (`latest_valuation`) before the
handoff when it is at least as new, with both lenses, dated values and typed conditions; the handoff is read under any
dated key shape (end_YYYY, end_fy..., june_YYYY, calibrated_*); the filing check is labelled the old (heavy) engine's
thesis check (`filing_check`); new status `cannot_tell` for a filer without quarterly XBRL facts (20-F / 6-K) whose
companyfacts show no new period.
