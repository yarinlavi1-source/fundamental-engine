# One decision path (v0.11)

This file is the contract between the modules of the unified pipeline. Every number
that reaches a conclusion travels through exactly one route:

```
sources (connector payloads, filings)            facts.py      FactBook (one schema, provenance per number)
  -> data validation                              dataqa.py     critical fields: verified / single_source / conflict / missing / stale
  -> business understanding                       model_card.py business model, stage, driver tree, method choice, understanding checklist
  -> financial analysis                           analysis.py   growth, margins, FCF, ROIC, reinvestment, dilution, balance sheet, quality of earnings
  -> business forecast                            forecast.py   bear / base / bull driver specs linked to reinvestment, funding and dilution
  -> valuation                                    pipeline.py   drivers.build_case -> valuation.value_company (+ diagnostics, reverse, capital bridge)
  -> risks and failure scenarios                  risk.py       disqualifying / pricing / accepted risks, permanent-loss case, size of error
  -> independent review                           review.py     mechanical audit (dataqa, valuation_audit, redteam) + signed reviewer input
  -> investment decision                          decision.py   four separate assessments, closed verdict set, index alternative
  -> report                                       render.py     markdown report + one Hebrew paragraph
```

Around it, using the same FactBook, the same validation and the same decision objects:

```
screen.py     universe rows -> lens shortlists (quality compounder, quality at a fair price, early commercial growth,
              mispriced / turning, structural tailwind) -> candidates for pipeline.run
portfolio.py  read-only broker positions + casefile summaries -> weights, concentration, shared exposures,
              scenario overlap, candidate vs holdings vs index vs cash options (never orders)
monitor.py    thesis records, forecast freezing into ledger.py, re-check triggers, forecast-vs-actual attribution
```

Earlier routes stay importable for backward compatibility (engine.analyze legacy DCF, ownership.owner_value,
expectations.run_model, supervisor.review dossiers, plain.plain_verdict). They are not decision routes: a
price conclusion produced by the pipeline comes only from `decision.decide`.

## Provenance classes (every number in a casefile carries one)

| class | meaning | examples |
|---|---|---|
| `verified` | reported fact confirmed by two independent routes (second source, or filing-derived value plus a passing identity/continuity check) | FY revenue that matches across SEC and vendor and ties to segments |
| `reported` | reported fact from one filing-derived source, no confirmation yet | as-reported XBRL value via a vendor |
| `vendor_estimate` | vendor-normalized or consensus number | analyst revenue estimates, vendor TTM ratios |
| `model_assumption` | chosen by the engine or the analyst; has rationale and falsifier | terminal margin, fade speed, cost of equity |
| `calculation` | deterministic function of the above; lists its inputs | ROIC, IRR, value per share |

## FactBook (facts.py)

```
{
 "factbook_version": 1,
 "company": {"ticker", "name", "cik"?, "currency", "exchange"?, "sector"?, "industry"?, "country"?, "fiscal_year_end"? "MM-DD"},
 "as_of": "YYYY-MM-DD",                       # information cutoff; nothing available later may be used
 "sources": {sid: {"id", "provider": sec|fmp|alpha_vantage|ib|issuer|web|manual|synthetic,
                   "dataset", "kind": filing_derived|vendor_normalized|market_feed|consensus|document|synthetic,
                   "retrieved_at", "sha256"?, "url"?, "note"?}},
 "facts": [ {"metric", "period": "FY2025" | "2026Q3" | "TTM@2026-08-28" | "I@2026-08-28",
             "period_end": "YYYY-MM-DD", "period_start"?: "YYYY-MM-DD", "value": float, "unit": "USD"|"shares"|"USD/share"|"ratio",
             "source_id", "basis": "as_reported"|"normalized"|"derived", "concept"?: raw tag,
             "available_at": "YYYY-MM-DD", "available_at_estimated": bool, "derived_from"?: [fact keys]} ],
 "market": {"price": {"value", "currency", "as_of", "source_id", "kind": last|close}, "price_history"?: [{"date","close"}]},
 "estimates": [ {"metric": revenue|eps|ebitda|net_income, "fiscal_year", "period_end", "value", "low"?, "high"?,
                 "n_analysts"?, "source_id", "as_of"} ],
 "qualitative": [ {"id", "topic", "statement", "kind": reported|guidance|opinion|assumption, "source_ids", "as_of"} ],
 "capital_structure"?: capital.py structure (instrument level, from filings)
}
```

Canonical metrics (USD unless noted): revenue, cost_of_revenue, gross_profit, rd, sm, ga, sga, operating_income,
interest_expense, interest_income, pretax_income, income_tax, net_income, eps_diluted (USD/share),
shares_basic_wavg, shares_diluted_wavg (shares), d_and_a, sbc, cfo, capex, acquisitions, buybacks,
dividends_paid, debt_issued, debt_repaid, change_in_cash, deferred_revenue_change; instants: cash,
short_term_investments, long_term_investments, restricted_cash, receivables, inventory, current_assets, ppe,
goodwill, intangibles, total_assets, payables, deferred_revenue_current, deferred_revenue_noncurrent,
current_liabilities, short_term_debt, long_term_debt, operating_lease_liabilities, total_liabilities, equity,
shares_outstanding (shares).

`facts.get(fb, metric, period)` returns the best fact (filing-derived before normalized, earliest available
that is <= as_of); `facts.series(fb, metric, kind='annual'|'quarter')` returns `[(period_end, value, fact)]`
ascending; `facts.ttm(fb, metric)` sums four contiguous quarters or falls back to the latest fiscal year
(labelled). Nothing with `available_at > as_of` is ever returned.

## Data validation (dataqa.py)

`validate(fb) -> {"fields": {name: Field}, "checks": [Check], "blocking": [names], "limited": [names], "status": ok|limited|blocked}`

Field = `{"field", "value", "unit", "as_of", "status": verified|reported|single_source|conflict|missing|stale,
"sources": [...], "checks": [codes], "note"}`. Critical fields: shares_outstanding, cash_and_liquid_investments,
investments, debt, convertibles, post_period_events, cfo_ttm, capex_ttm, revenue_ttm, operating_income_ttm, sbc_ttm.
A critical field that is `conflict` or `missing` blocks a price conclusion; `single_source`/`stale` limit
confidence. Checks include accounting identities, quarter/annual tie-out, cross-source agreement, share
continuity versus buybacks/issuance, debt roll-forward and staleness.

## Decision objects (decision.py)

Four assessments are produced separately and never averaged:
`business_quality`, `trajectory`, `price_attractiveness`, `risk_and_error`
(each: `{"level": strong|adequate|weak|unknown, "reasons": [...], "evidence": [...]}`), plus
`growth_quality`, `financial_strength`, `management_capital_allocation`, `data_uncertainty` for ranking.

Closed verdict set: `attractive_at_price`, `quality_but_expensive`, `promising_funding_conditional`,
`interesting_not_yet_valuable`, `insufficient_evidence`, `index_preferable`, `avoid`.
Every verdict carries one main reason, the condition that would change it (for price verdicts: the price at
which the base case meets the hurdle), and a confidence with its reasons. Analyst overrides need a written
justification and evidence ids, are stored next to the mechanical verdict, and cannot lift a disqualifying
gate (blocked data, unfunded plan, impaired equity, critical red-team failure).

Expected return is the base-case internal rate of return at today's price (valuation_tools.implied_cost_of_equity),
shown with bear/bull IRRs and decomposed into business growth, cash yield and multiple change. The owner's 14%
aspiration is reported as a comparison, never as a filter or a target the model is tuned to.
