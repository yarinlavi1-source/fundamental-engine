# Module contracts (v0.11 pipeline)

Shapes every pipeline module consumes and produces. Read with docs/ARCHITECTURE.md. All functions are
pure (no network, no clock unless stated), stdlib only, JSON-serialisable outputs (no NaN/inf), and
never read a fact with `available_at > fb['as_of']` (use the accessors in facts.py).

Provenance class on any reported number: `verified | reported | vendor_estimate | model_assumption | calculation`.
Field status (dataqa): `verified | reported | single_source | conflict | missing | stale`.

## dataqa.validate(fb, capital_structure=None) -> qa

```
{"status": "ok"|"limited"|"blocked",
 "fields": {name: {"field", "status", "value", "unit", "as_of", "sources": [source_id],
                   "facts": [{"metric","period","source_id","value"}], "checks": [check codes], "note"}},
 "checks": [{"code", "status": "pass"|"fail"|"skip", "severity": "critical"|"major"|"minor",
             "period"?, "detail": {...}, "message"}],
 "resolved": {"shares_outstanding": {"value","as_of","basis": cover|balance_sheet|vendor_float|capital_structure},
              "cash": {...}, "short_term_investments": {...}, "long_term_investments": {...},
              "debt": {"value","as_of","method": "noncurrent+current"|"tagged_total"|"tagged_plus_current"|"capital_structure"},
              "net_cash": {...}, "deferred_revenue": {...}},
 "blocking": [critical field names], "limited": [field names]}
```
Critical fields: `shares_outstanding, cash_and_liquid_investments, investments, debt, convertibles,
post_period_events, revenue_ttm, operating_income_ttm, cfo_ttm, capex_ttm, sbc_ttm`.
`conflict`/`missing` on shares_outstanding, cash_and_liquid_investments, debt, revenue_ttm or cfo_ttm blocks;
convertibles/post_period_events are `missing` -> limited (not blocking) unless the FactBook or capital
structure shows the instrument exists and its terms are absent (then blocking).

## analysis.analyze(fb, qa) -> an

```
{"years": [period_end ...ascending],
 "annual": {period_end: {"revenue","gross_profit","operating_income","net_income","cfo","capex","fcf","sbc",
            "fcf_after_sbc","d_and_a","buybacks","dividends","acquisitions","shares_diluted_wavg",
            "shares_outstanding","cash","investments","debt","equity","goodwill","intangibles",
            "invested_capital","deferred_revenue","nwc"}},          # None where not available
 "ttm": {"period_end","method", <same keys where available>},
 "metrics": {name: {"value","unit": ratio|USD|x|years|count,"formula","inputs":[...],"period","status"}},
 "forensics": scores.scorecards(...) output or {"status": "unavailable", "reason"},
 "flags": [{"code","severity": high|medium|low,"message"}],
 "history_rows": [history.py rows]}
```
Metric names (at least): revenue_cagr_3y, revenue_cagr_5y, revenue_growth_last, revenue_growth_series,
growth_volatility, acquisition_spend_pct_revenue_3y, gross_margin, gross_margin_trend_3y, operating_margin,
operating_margin_trend_3y, operating_margin_best_3y, fcf_margin, fcf_after_sbc_margin, sbc_pct_revenue,
cfo_to_net_income, accruals_ratio, capex_pct_revenue, capex_to_da, roic, roic_ex_goodwill,
incremental_roic_3y, sales_to_capital_incremental_3y, nwc_pct_revenue, deferred_revenue_pct_revenue,
net_cash, net_debt_to_ebitda, interest_coverage, share_count_cagr_3y, buybacks_pct_fcf_3y,
sbc_offset_by_buybacks.

## model_card.build(fb, an, qa, inputs=None) -> card

```
{"business_model": {"primary", "secondary": [...], "reasons": [...], "override"},
 "stage": {"value": early|growth|maturing|mature|decline|turnaround, "reasons", "computed", "override"},
 "cyclical": {"value": bool, "reasons"}, "capital_intensity": {"value": low|medium|high, "reasons"},
 "funding": {"value": self_funded|needs_external|unknown, "reasons"},
 "playbooks": [brain playbook names], "driver_tree": [{"driver","why","metric","data_status"}],
 "valuation": {"method": fcff_annual_path|normalized_cycle_fcff|scenario_survival_fcff|residual_income|nav|rnpv|sotp|not_reliably_valuable,
               "archetype": a valuation.ROUTES key, "why", "cross_checks": [...], "limitations": [...],
               "default_for_type": bool, "override_reason"},
 "understanding": [{"id","question","critical": bool,"status": evidenced|asserted|open,"answer","evidence_ids"}],
 "dimensions": {moat|switching_costs|pricing_power|retention|customer_concentration|market_size|competition|
                substitutes|management|capital_allocation|governance|regulation|technology_ai|geopolitics:
                {"assessment": strong|adequate|weak|unknown, "evidence_ids", "note", "source": computed|analyst}},
 "completeness": {"status": complete|partial|insufficient, "missing": [...]}}
```
Business-model types: subscription_software, platform_marketplace, semiconductor, hardware_manufacturing,
capital_intensive_infrastructure, consumer_brand, industrial, financials, pre_revenue_biotech, conglomerate,
reit, commodity. `inputs` = {"business_model"?: {"primary","reason"}, "stage"?: {"value","reason"},
"assessments": {dimension: {"assessment","evidence_ids","note"}}, "understanding": {qid: {"answer","evidence_ids"}},
"method_override"?: {"method","reason"}}. Overrides without a reason are rejected.

## forecast.build(fb, an, card, qa, inputs=None) -> fc

```
{"spec": drivers.build_case spec (driver_version 1, bear/base/bull, underwriting block),
 "assumptions": [{"id","topic","kind": reported|guidance|analyst|model,"provenance","values": {"bear","base","bull"},
                  "rationale","falsifier","source_ids"}],
 "anchors": {...evidence the paths start from...}, "cost_of_equity": {"bear","base","bull","components"},
 "warnings": [...]}
forecast.what_must_be_true(spec, price) -> [{"lever","label","status": solved|not_reachable,"required","anchor","meaning"}]
```
`inputs`: {"overrides": [{"path","scenario","value","reason","evidence_ids"}], "reviews": {assumption_id: {...}},
"risk_free": {...}, "equity_risk_premium": {...}}.

## risk.assess(fb, qa, an, card, fc, val) -> rk ; review.audit(...) -> rv

```
rk = {"register": [{"id","category","description","evidence_ids","class": disqualifying|pricing|accepted_priced,
                    "severity": high|medium|low,"in_bear_case": bool}],
      "failure_scenarios": [{"name","mechanism","observable_signs","value_per_share"?,"loss_from_price"?}],
      "downside": {"bear_value","loss_to_bear","permanent_loss_value","loss_to_permanent"},
      "error_size": {"level": low|medium|high,"value_range_rel","discount_rate_band","terminal_share","reasons"},
      "disqualifying": [ids]}
rv = {"mechanical": {"dataqa": qa.status,"valuation_audit": {...},"redteam": {...},"consistency": [...]},
      "reviewer": signed review input or None,
      "price_conclusion_allowed": bool, "status": reviewed|unreviewed_draft|blocked, "reasons": [...]}
```

## decision.decide(parts, inputs=None) -> dc

```
{"assessments": {"business_quality","trajectory","price_attractiveness","risk_and_error",
                 "growth_quality","financial_strength","management_capital_allocation","data_uncertainty":
                 {"level": strong|adequate|weak|unknown,"reasons","evidence"}},
 "expected_return": {"base_irr","bear_irr","bull_irr","decomposition": {"business_growth","cash_yield","multiple_change"},
                     "index_expected_return": {"value","basis"},"hurdle","meets_owner_aspiration_14pct"},
 "verdict": {"code","label_he","reason","what_would_change_it","price_for_hurdle"},
 "mechanical_verdict": {...}, "overrides": [...], "confidence": {"level": low|medium|high,"reasons"}}
```
Verdict codes: attractive_at_price, quality_but_expensive, promising_funding_conditional,
interesting_not_yet_valuable, insufficient_evidence, index_preferable, avoid.

## screen, portfolio, monitor

`screen.rows_from_*` adapters -> universe rows `{"symbol","name","sector","industry","market_cap","data_grade":
screening|verified,...metrics}`; `screen.scan(rows, options) -> {"dimensions": {symbol: {...}}, "lenses":
{lens: [candidates]}, "shortlist": [...], "excluded": {...}, "notes"}`.
`portfolio.from_ib(positions, summary) -> book` (private; never written into Git); `portfolio.analyze(book,
holdings_info, options) -> {"weights","concentration","exposures","overlaps","scenarios","review_needed"}`;
`portfolio.compare(candidate, book_analysis, alternatives) -> {"options": [...], "notes"}` (options only, no orders).
`monitor.thesis_from_casefile(cf) -> thesis`; `monitor.check(thesis, new_fb) -> {"changes","impact","alerts"}`;
`monitor.freeze(ledger, thesis)`; `monitor.score(ledger, actuals) -> attribution`.
