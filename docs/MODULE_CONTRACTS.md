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
        "casefile_sha256"}
summary(cf) -> {"symbol","as_of","verdict","verdict_status","price_claim_withheld","confidence","quality","risk_level","price",
        "bear_value","base_value","bull_value","base_irr","bear_irr","decision_version","margin": marginal|clear|None,"hurdle",
        "value_at_required_return","review_status","casefile_sha256"}
```
`inputs` = {"card"?: model_card inputs, "forecast"?: forecast inputs, "reviewer"?: review input, "decision"?: decision inputs,
"analyst"?: name or list of names}. `run` snapshots the inputs before any stage reads them and passes
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
`options` = {"tolerances": {metric: relative}, "max_age": {shares|balance_sheet|ttm|annual: days}}.

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
         tax_rate, other_claims, net_runoff_obligation, debt_weight, ...}; interim_boundaries; opening {..., convertibles?};
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
                                 "leverage_relevered","formula","reason"},
                    "leverage": {"ke","kd": expected,"kd_expected","kd_promised","market_debt_to_equity","net_debt",
                                 "model_equity","debt_weight","d_to_e","issue_prices","status","basis",...}}},
 "funding": {s: {"status","debt_assumed","equity","equity_raised","issue_prices","issue_price_rule","price_dependent",
                 "liquidation","investments_liquidated","settled","sbc_settled_in_shares","sbc_settlement_shares",
                 "new_financing_shares","value_if_issued_at_own_value"? (base), ...}},
 "overrides": [...], "preview": {...}, "warnings": [{"code","severity","message"}], "moat_proxy": {...} | None, "notes": [...]}
what_must_be_true -> [{"lever","label","status": solved|not_reachable,"required","anchor","unit","meaning","detail"}]
```
`terminal.wacc` is built from `terminal.cost_of_equity`, the mature table (the explicit table without the stage and one-off
funding add-ons, `forecast.TERMINAL_KE_DROPPED`) relevered at the same D/E; the explicit years and the roll-back of the
terminal value use the scenario's `cost_of_equity`.
`terminal.debt_weight` is the unrounded weight of `cost_of_equity.components.leverage.debt_weight` (the relevering fixed point
on the model's own base equity value, never the quote; capped at 60% with a warning). Funding status adds `share_settled`.
Case sources (`spec.sources`): {id, title, url, kind, published_at, available_at, retrieval?, published_at_estimated?}; kind
from the FactBook kind (filing_derived -> filing, vendor_normalized -> provider_data, market_feed -> market_data, consensus ->
analyst_estimates, secondary -> secondary_source, issuer_document -> its dataset: filing|issuer_release|earnings_release,
primary_document -> its dataset: regulator|official_statistics); the source's own `retrieval` passes through when it is one
of primary_document|provider_normalized|search_excerpt|market_feed; `published_at` is the document date when the FactBook
has one (a date after as_of gives `invalid_spec`), else the first availability of its facts with `published_at_estimated`.
`inputs` (KNOWN_INPUTS): {"overrides": [{"path","scenario": bear|base|bull|all,"value","reason","evidence_ids"}],
"reviews": {assumption_id: {"status": reviewed|unreviewed,"reviewer"}}, "risk_free"|"equity_risk_premium": {"value","as_of",
"source_id" | "title","url"?,"kind"?,"retrieval"?}, "horizon_years": 1..30, "financing": assumed|assumed_debt|none,
"nol": opening tax-loss carryforward (number), "cyclical": {"treatment": structural|cyclical,"reason","evidence_ids"}}. A rate typed into the inputs (no
FactBook source_id) becomes case kind `analyst_input` (any other stated kind kept in `stated_kind`; undated ->
`published_at_estimated`); a kind in PRIMARY_CLAIMS (filing, earnings_release, issuer_release, regulator,
official_statistics, facts.REPORTED_KINDS) or an unknown retrieval raises ValueError. Unknown top-level keys are ignored
with an `input_ignored` warning; unknown keys inside a known input raise. `reviews` are author notes: the review stage
treats every reviews signer as author-side. Material-issue ids include data_blocked, opening_dates_differ,
cyclical_peak_unconfirmed, moat_proxy, possible_lender, leverage_at_cap, convertibles_unapplied, assumed_financing_<s>.

## valuation_tools (diagnostics used by pipeline, risk and decision)

`debt_weight(case, scenario='base') -> (w, source)`: the scenario's `terminal.debt_weight` (capped at `W_CAP` 0.95), else
opening net debt / (net debt + base value x opening shares), 0 without net debt or for a non-operating case.
`shifted_value(case, scenario, shift, w=None)`, `discount_rate_band(case, scenario='base', shift=.01, w=None) ->
{"scenario","shift","value_lower_rate","value","value_higher_rate","relative_width","quote_inside_band","debt_weight",
"debt_weight_source","terminal_wacc_shift","meaning"}`, `implied_cost_of_equity(case, scenario='base', low, high, w=None) ->
{"status": solved|not_bracketed|invalid_search_domain,"implied_cost_of_equity","model_cost_of_equity"?,"shift"?,
"terminal_wacc_shift"?,"debt_weight","debt_weight_source","limitation",...}`: the cost of equity moves by the shift and an
operating terminal WACC by (1 - w) x shift. `reverse_price`, `stress_test`, `forecast_score`, `asset_schedule` unchanged.
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
                     {"value","low","high","provenance","origin","source","components","basis"},"hurdle","required_return",
                     "hurdle_rule","hurdle_basis","risk_premium","premium_components","premium_capped","permanent_loss_premium",
                     "model_cost_of_equity","model_cost_of_equity_floor": {"value","binding","basis"},"at_required_return":
                     {"base","bear","bull","loss_to_bear","rate","semantics"},"value_at_required_return",
                     "base_value_at_model_cost_of_equity","price_for_hurdle","five_year_at_required_return","band": {"hurdle_low",
                     "hurdle_high","step","error_level","error_widening","price_low","price_high","rows","bear_tolerance_step",
                     "moat_proxy"?,"rule"},"debt_weight","debt_weight_source","owner_aspiration","meets_owner_aspiration_14pct",
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
           "withhold_price","withhold_price_released","reviewer_present","draft","draft_codes"},
 "rules", "business_reason_he", "semantics"}
```
Verdict codes: attractive_at_price, quality_but_expensive, promising_funding_conditional, interesting_not_yet_valuable,
insufficient_evidence, index_preferable, avoid (precedence: decision.VERDICT_RULES). Required return = max(index return +
permanent-loss premium, model cost of equity). IRR and every value at a required return move the terminal WACC by (1 - w) x
the shift. Funding gates only a blocked plan or an unresolved `assumed_financing_<s>` issue; a reviewed priced financing path
or SBC settled in shares is valued with `conditional_on`. Any unresolved forecast material issue (case, else spec
underwriting) keeps the verdict a draft and is named (`withheld_reasons`, confidence); for possible_lender and leverage_at_cap
the paragraph shows no value. With `fc['moat_proxy']` the band adds a no-proxy row and the margin test re-runs the verdict
without the proxy. `reviewer_present` is true only for an accepted reviewer bound to the valuation and the inputs.
`inputs` = {"index"?: {value, basis, source, low?, high?} (never below the risk-free rate), "overrides"?: [{"target":
assessment|verdict|red_flags,"value","justification","evidence_ids"}]}. `price_for_hurdle(case, hurdle, w=0.)`.

## render

`render.markdown(casefile) -> str` (full report) and `render.hebrew_paragraph(casefile) -> str` (one Hebrew paragraph, 80-140
words, drafts labelled). The review section names red-team checks that pass by construction; it does not yet print
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
