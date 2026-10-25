# One decision path (v0.11.1)

This file is the contract between the modules of the unified pipeline. Every number
that reaches a conclusion travels through exactly one route:

```
sources (connector payloads, filings)            facts.py      FactBook (one schema, provenance and availability per number)
  -> data validation                              dataqa.py     critical fields: verified / reported / single_source / conflict / missing / stale
  -> financial analysis                           analysis.py   growth, margins, FCF, ROIC, reinvestment, split-aware dilution, balance sheet, quality of earnings
  -> business understanding                       model_card.py business model (lender shape), stage, cycle, funding, driver tree, method choice, understanding checklist
  -> business forecast                            forecast.py   bear / base / bull driver specs linked to reinvestment, funding, dilution and a quote-independent cost of equity
  -> valuation                                    pipeline.py   drivers.build_case -> valuation.value_company (+ valuation_tools diagnostics, what-must-be-true, capital check)
  -> risks and failure scenarios                  risk.py       disqualifying / pricing / accepted risks with pricing_status, permanent-loss case, size of valuation error
  -> independent review                           review.py     mechanical audit (dataqa, valuation_audit, redteam) + signed reviewer bound to the valuation and the analyst inputs
  -> investment decision                          decision.py   separate assessments, one required return, marginal band, closed verdict set, governed overrides
  -> report                                       render.py     markdown report + one Hebrew paragraph + the owner's table
  -> scenario reasons (reads only)                scenario_reasons.py  the condition and the business behind every דובי / בסיסי / שורי price
```

`pipeline.run` executes the stages in this order (analysis runs before the model card because the card reads it).
docs/MODULE_CONTRACTS.md lists the inputs and outputs of each function.

Around it, using the same FactBook, the same validation and the same decision objects:

```
screen.py     universe rows -> lens shortlists (quality compounder, quality at a fair price, early commercial growth,
              mispriced / turning, structural tailwind) -> candidates for pipeline.run
portfolio.py  read-only broker positions + casefile summaries -> weights, concentration, common factors and driver clusters,
              scenario overlap, candidate vs holdings vs index vs cash options (never orders)
monitor.py    thesis records, forecast freezing into ledger.py, re-check triggers, forecast-vs-actual attribution
```

Earlier routes stay importable for backward compatibility (engine.analyze legacy DCF, ownership.owner_value,
expectations.run_model, supervisor.review dossiers, plain.plain_verdict). They are not decision routes: a
price conclusion produced by the pipeline comes only from `decision.decide`.

## Provenance classes (every number in a casefile carries one)

| class | meaning | examples |
|---|---|---|
| `verified` | reported fact confirmed by two independent source families (a second family, or a filing-derived value plus a passing identity/continuity check) | FY revenue that matches between the issuer's filings and an independently normalizing vendor |
| `reported` | reported fact from one filing-derived or issuer-document source, no confirmation yet | as-reported XBRL value via a vendor |
| `management_guidance` | the company's own forecast, dated and sourced | current fiscal-year revenue guidance |
| `vendor_estimate` | vendor-normalized or consensus number | analyst revenue estimates, vendor TTM ratios |
| `model_assumption` | chosen by the engine or the analyst; has rationale and falsifier | terminal margin, fade speed, cost of equity |
| `user_assumption` | supplied by the user without a FactBook source | an index expected return in decision inputs |
| `calculation` | deterministic function of the above; lists its inputs | ROIC, IRR, value per share |

One document reached through two vendors is one source family: copies confirm nothing. A secondary source (press,
analysis) never confirms or contradicts a value and never rises above `single_source`.

## FactBook (facts.py)

```
{
 "factbook_version": 1,
 "company": {"ticker", "name", "cik"?, "currency", "exchange"?, "sector"?, "industry"?, "country"?, "fiscal_year_end"? "MM-DD",
             "filer"? | "annual_form"?},          # 'foreign' / '20-F' / '40-F' switch to the foreign filing lags
 "as_of": "YYYY-MM-DD",                       # information cutoff; nothing available later may be used
 "sources": {sid: {"id", "provider": sec|fmp|alpha_vantage|ib|issuer|web|manual|synthetic|<bundle kind>,
                   "dataset", "kind": filing_derived|issuer_document|primary_document|document|vendor_normalized|market_feed|
                                     consensus|secondary|synthetic,
                   "retrieved_at", "sha256"?, "url"?, "note"?,
                   "published_at"?, "retrieval"?: primary_document|provider_normalized|search_excerpt|market_feed, "undated"?}},
 "facts": [ {"metric", "period": "FY2025" | "2026Q3" | "Q@2026-08-28" | "YTD@2026-08-28" | "I@2026-08-28" | "AVFY@2025-11-28",
             "period_end": "YYYY-MM-DD", "period_start"?: "YYYY-MM-DD", "value": float, "unit": "USD"|"shares"|"USD/share"|"ratio",
             "source_id", "basis": "as_reported"|"normalized"|"derived", "concept"?: raw tag,
             "available_at": "YYYY-MM-DD", "available_at_estimated": bool, "derived_from"?: [fact keys]} ],
 "market": {"price": {"value", "currency", "as_of", "source_id", "kind": last|close},
            "price_history"?: [{"date", "close", "basis"?: as_traded|split_adjusted, "adjusted"?: bool}],
            "price_history_basis"?: "as_traded"|"split_adjusted",      # unknown when absent and rows are untagged
            "price_history_bar_date"?: "start",                          # bars stamped at their start (IB monthly): a close is known at the next bar
            "splits"?: [{"date", "factor": shares after / before, "source_id"}]},
 "estimates": [ {"metric": revenue|eps|ebitda|net_income, "fiscal_year", "period_end", "value", "low"?, "high"?,
                 "n_analysts"?, "source_id", "as_of", "as_of_requested"?} ],
 "qualitative": [ {"id", "topic", "statement", "kind": reported|guidance|opinion|assumption, "source_ids", "as_of",
                   "available_at"?, "available_at_estimated"?} ],
 "guidance"?: [ {"metric", "period": "FY2026", "low", "high", "issued_at", "source_ids", "reaffirmed_at"?, ...} ],
 "research_notes"?: {"conflicts", "open_questions", "skipped_rows"?, "withheld_at_as_of"?},
 "collection_notes"?: [text],                 # pipeline.factbook_from_run_dir: what was skipped as dated after as_of
 "capital_structure"?: capital.py structure (instrument level, from filings)
}
```

Kinds: `filing_derived` (XBRL via SEC or FMP as-reported), `issuer_document` (the issuer's filing or release read outside
XBRL; `dataset` keeps filing / issuer_release / earnings_release), `primary_document` (a regulator filing or official
statistics, graded like an issuer document), `document` (legacy name of issuer_document), `secondary` (press, analysis,
anything else). `facts.REPORTED_KINDS` = filing_derived, issuer_document, primary_document, document. `price_history`
is only used by dataqa's split test, and only an as-traded series dense enough to locate the split bar (closes at most 40
days apart) can decide a split; `pipeline.factbook_from_run_dir` does not fill it today (docs/LIMITATIONS.md).

Availability: a filing or dated bundle document is usable two days after publication. Without a filing date the
deadline is used and flagged estimated: annual 90 days, quarterly 45; a quarter or balance sheet ending at a fiscal-year
end takes the annual lag; foreign filers 120 days annual and 180 interim. A bundle fact is public with the earliest cited
issuer document dated on or after its period end (else its secondary sources; undated sources count at the deadline,
capped at the bundle retrieval date); a qualitative item with the latest of its cited documents. Consensus fetched today is
dated no earlier than its retrieval unless it is a dated vendor snapshot. Bundle guidance needs an issue date usable by
as_of; conflicts and open questions written after as_of are kept only when every source they cite is dated and usable.

Canonical metrics (USD unless noted): revenue, cost_of_revenue, gross_profit, rd, sm, ga, sga, operating_income,
interest_expense, pretax_income, income_tax, net_income, eps_diluted (USD/share),
shares_basic_wavg, shares_diluted_wavg (shares), d_and_a, sbc, cfo, capex, acquisitions, buybacks,
dividends_paid, debt_issued, debt_repaid, change_in_cash, deferred_revenue_change, cfi, cff, fx_effect; instants: cash,
cash_incl_restricted, short_term_investments, long_term_investments, restricted_cash, receivables, inventory, current_assets,
ppe, goodwill, intangibles, total_assets, payables, deferred_revenue_current, deferred_revenue_noncurrent,
current_liabilities, current_portion_ltd, short_term_borrowings, long_term_debt, long_term_debt_tagged (scope resolved by
dataqa), operating_lease_current, operating_lease_noncurrent, total_liabilities, equity, retained_earnings,
convertible_debt, debt_fair_value, warrants_outstanding, liabilities_and_equity, shares_outstanding, treasury_shares;
bundle and vendor extras: shares_outstanding_cover, shares_outstanding_vendor, rpo, arr, commercial_paper_info (issuer
debt totals include it), deferred_revenue_total, cash_and_investments_total. SEC tags are merged per period end (earliest
filed wins; tag order only breaks ties); payments (capex, buybacks, dividends, acquisitions, debt flows) are summed by
magnitude.

`facts.usable(fb, f)`: published by as_of AND about a period that ended by as_of. `facts.candidates(fb, metric, ...)` orders
usable facts best first: secondary sources last, then basis (as_reported, derived, normalized), filing-derived before other
kinds, an issuer's filing before its earlier release, earliest available. `facts.get` returns the first;
`facts.series(fb, metric, kind='annual'|'quarter'|'instant')` returns `[(period_end, value, fact)]` ascending with one fact
per period (7-day tolerance for 52/53-week calendars); `facts.ttm(fb, metric)` takes four contiguous quarters, the latest
fiscal year plus the quarters since less the same quarters a year earlier, or the latest fiscal year plus the year to date
less the prior year to date (most recent wins; YTD must end within 366 days of that fiscal year), else the latest fiscal
year (labelled); it returns the facts with their signs. Nothing with `available_at > as_of` is ever returned.

## Data validation (dataqa.py)

`validate(fb, capital_structure=None, options=None) -> {"status": ok|limited|blocked, "fields", "checks", "resolved",
"blocking", "limited", "splits", "unresolved_splits", "as_of"}`.

Field = `{"field", "value", "unit", "as_of", "status": verified|reported|single_source|conflict|missing|stale,
"sources", "facts", "checks", "note", "blocking", ...}`. Critical fields: shares_outstanding, cash_and_liquid_investments,
investments, debt, convertibles, post_period_events, cfo_ttm, capex_ttm, revenue_ttm, operating_income_ttm, sbc_ttm.
`conflict` or `missing` on shares_outstanding, cash_and_liquid_investments, debt, revenue_ttm or cfo_ttm blocks a price
conclusion; the others and `single_source`/`stale` limit confidence. Checks include accounting identities, quarter/annual
tie-out, cross-source agreement between independent source families, share continuity versus buybacks/issuance (a split
needs positive evidence: a dated corporate action, an as-traded price bar or fully restated weighted shares; otherwise the
step stays unresolved and counts as dilution), debt scope by roll-forward, capital-structure lines issued after as_of
(not read), negative cash or investments (rejected as a data error, never an opening balance), TTM equal to facts.ttm,
researcher-recorded conflicts and staleness (a TTM older than twice its age limit is missing).

## Review binding (review.py)

A price conclusion needs every mechanical gate plus an ACCEPTED independent reviewer: valid, independent, `valuation_sha256`
equal to the executed valuation's input_sha256, and `inputs_sha256` equal to `review.inputs_digest(val, inputs)`, which
covers the executed valuation and the analyst's `inputs.decision`, `inputs.card` and `inputs.forecast` as supplied to
`pipeline.run` (shown in `rv['binding']`). An analyst override added after the review changes the digest and voids the
review. `reviewed_at` lies between the valuation cutoff and today. The reviewer's normalized name must differ from every
author-side signer (`inputs.analyst`, forecast `reviews` signers, case assumption reviewers). Author `review_status` values
are notes, never sign-offs. Releasing an evidenced governance red flag (going-concern doubt, material weakness,
restatement) needs a documented `risk_overrides` entry citing FactBook filing evidence beyond the flag's own, at least one
of it a filing or issuer document. The engine checks the sign-off; it cannot check the reviewer's diligence.

## Decision objects (decision.py)

Four assessments are produced separately and never averaged:
`business_quality`, `trajectory`, `price_attractiveness`, `risk_and_error`
(each: `{"level": strong|adequate|weak|unknown, "reasons": [...], "evidence": [...]}`), plus
`growth_quality`, `financial_strength`, `management_capital_allocation`, `data_uncertainty` for ranking. Quality uses no
price input; a cyclical is graded on cycle averages; a loss-making early/growth company stays at most adequate until its
unit economics are evidenced.

Closed verdict set: `attractive_at_price`, `quality_but_expensive`, `promising_funding_conditional`,
`interesting_not_yet_valuable`, `insufficient_evidence`, `index_preferable`, `avoid`, from one precedence list
(decision.VERDICT_RULES). Every verdict carries one main reason, the condition that would change it (for price verdicts:
the price at which the base case meets the required return), a confidence with its reasons, a status (`final` or
`draft`, with `withheld_reasons`) and a margin (`marginal` or `clear`).

Required return (hurdle) = max(index expected return + a premium for permanent-loss risk, the model's own cost of equity).
The index return defaults to the forecast's risk-free + equity premium (+-1pp range) unless the user supplies one (never
below the risk-free rate). Expected return is the base-case IRR at today's price with cash flows held fixed; the cost of
equity moves the terminal WACC by the equity weight only, (1 - w) x shift, for the IRR, the value at the required return,
the bear value and the five-year value. The value shown to the owner is the price at which the base IRR equals the
required return. A verdict that flips inside the marginal band (hurdle +-1pp widened by the size of valuation error, the
index range, a bear loss within 5pp of its tolerance, and the value without a computed moat proxy) is marked marginal and
says on which side it flips. The owner's 14% aspiration is reported as a comparison, never as a filter or a tuning target.

Gates: disqualifying risk, impaired equity, blocked data, a failed valuation stage, insufficient understanding, a critical
red-team failure, an unfunded plan or no valuation can never be lifted by an override. Funding gates only a blocked plan
or assumed financing whose forecast material issue is unresolved; a reviewed, priced financing path (or SBC settled in new
shares) is valued and the verdict carries `conditional_on`. Any unresolved forecast material issue keeps the verdict a
draft and is named in the owner text. Analyst overrides need a written justification and evidence ids; upgrades need
filing or issuer evidence, can never reach a verdict the mechanical route refuses and never lower the required return;
downgrades are never blocked. Overrides are stored next to the untouched mechanical verdict.
