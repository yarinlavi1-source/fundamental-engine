# Annual fair-value underwriting: current date through 2030 and beyond

Deliver a simple Hebrew table: date, bear, base, bull, fixed current quote,
base gap versus quote. The owner's words for the scenarios are bear = דובי, base =
בסיסי, bull = שורי ('סביר', 'likely', is not a scenario name). Include TODAY separately from 31 December of each year.
A future-date fair value is not present value and not a market-price prediction.
The difference from today's price is not annualized return. Do not imply that the
scenario interval is a statistically calibrated confidence interval.

## Execute, do not narrate

1. Read valuation_research and the economic archetype playbook.
2. Resolve exact security, share class, currency and cutoff. Bridge the most recent
   balance sheet to cutoff with subsequent financing/acquisitions and elapsed cash
   use. If unavailable, make a visible assumption and stress it. Never silently
   relabel June cash as September cash. Customer advances are not spare equity.
3. Build a valuation_version=1 case using examples/valuation as FORMAT ONLY.
   Read docs/ANNUAL_VALUATION.md for definitions. Replace every synthetic input.
   For ratio-driven businesses (software, subscriptions) write a driver_version=1
   spec (growth and costs as % of revenue; examples/valuation/software_drivers.json)
   and call build_valuation_from_drivers; pass the returned case on unchanged.
   Record sources[].retrieval honestly: primary_document only if the filing itself
   was read, otherwise provider_normalized / search_excerpt / market_feed.
4. Forecast business drivers under bear/base/bull, cite sources and write specific
   analyst assumptions and falsifiers. Guidance is not reported revenue. Decompose
   actual cohorts/segments; use average units operating during the period.
5. Build capex, replacement cohorts, taxes, prepayment recognition, debt maturity,
   interest, issuance prices/fees and share count. Match periods and currencies.
   Roll commercial paper and maturing notes unless repayment from cash is evidenced;
   an invented repayment can create a false funding block under stress.
6. Run value_company. Run valuation_diagnostics for the operating route. Review
   funding gaps, terminal dependence, terminal margin jumps and unreviewed inputs.
   Read implied_cost_of_equity and discount_rate_band: if the price conclusion
   flips within one point of required return, say so. For driver specs also run
   implied_growth_shift to state what growth the price needs. Terminal growth
   follows the ERP's convention (= risk-free, see valuation_research); override
   it only with evidence.
7. Resolve the largest VALUE-SENSITIVE uncertainties. Use a full contract or note
   when needed; repeated articles do not resolve a missing contractual fact.
8. Save annual_valuation_input in the dossier, execute research_review, checkpoint,
   then report the actual annual_values. Never handwrite a target table.

## Every scenario price carries its condition (25.10.2026)

The owner: "every price has a reason: if something happens, then this price". Behind
the scenes, scenario_reasons (MCP tool; CLI `scenario-reasons <casefile.json>`;
casefile['scenario_reasons'] and out/scenarios_he.md from pipeline and deep) reads the
executed casefile and gives, for each of דובי / בסיסי / שורי, the condition ('אם …')
and, for today and every dated price of the yearly table, that fiscal year's revenue,
executed growth, operating margin after SBC, the model's EPS estimate (not GAAP) and FCF
per share, shares, distributions and the implied P/E on next year's estimate (banks on
the residual-income route: ROE, payout, book value and net income per share). It copies
the executed values; it never revalues. Dated prices follow the table's own rows: when
the table has no yearly part (a scenario without a value), no dated price is listed.
The owner's table shows one short condition line per scenario; the rest stays behind.

- The bear changes the business (growth, margin, funding, dilution), not only the
  discount rate or the multiple. A bear that differs from the base only in its
  multiple is not a bear.
- Events come only from the analyst, with evidence ids: inputs.analyst_sheet.
  scenario_events = {bear|base|bull: [{event_he, evidence_ids}]}. An event whose ids
  the FactBook does not hold (or that has none) is a hypothesis and is said to be one.
  evidence_ids is a list (or one id as text); an event that cannot be read is listed
  in analyst_sheet.scenario_events_rejected and said, never dropped silently.
- Without events the condition is the model's own drivers in plain Hebrew (growth
  first -> last year and long run, margin start -> end, external funding and
  dilution, cost of equity); never invent a story to fill it.
- Missing drivers are gaps, not numbers. A held valuation shows no scenario price.
- The reasons explain the model's conditional values; they are not forecasts,
  probabilities or price targets.

## Economic routing

- Mature nonfinancial / consumer: operating cash model, replacement capex, pricing,
  working capital, competitive fade and normalized terminal returns.
- Growth / software / platform: cohorts and monetization, declining growth,
  acquisition spend, SBC and funding; no automatic extrapolation of ARR.
- Infrastructure: commissioned average capacity, utilization and contracted pricing;
  customer acceptance, delivery delays, cash restrictions, depreciation versus
  physical replacement, prepayment burn-down, debt amortization and capital raises.
- Cyclical / commodity / turnaround: full-cycle normalization with a timed recovery,
  commodity volumes/prices, cost curve and balance-sheet survival. A low peak P/E
  is not proof of value. Run a separate no-recovery case.
- Financials: residual-income equity route. Reconcile book to regulatory capital;
  inspect credit losses, duration/mismatch and underwriting reserves. No generic
  FCFF or subtraction of deposit liabilities from bank EV.
- REIT / asset holding: property-level forward NOI/cap rates and ownership NAV;
  cross-check AFFO after recurring capital needs. NAV snapshots do not fund growth.
- Biotech: program-level dated rNPV with probability of REACHING each cash flow;
  committed trial costs are not multiplied by eventual approval probability.
  Keep financing and potential dilution separate; no terminal perpetuity beyond IP.
- Conglomerate: SOTP with explicit enterprise/equity basis, ownership, minority
  claims, central debt/costs and intercompany eliminations exactly once.

## Finish with a conclusion

Give a reasoned base case and range; explain the two or three variables that move
value most. Do not stop merely because uncertainty exists. Conversely, a material
funding gap is not a license to assume free capital: supply an explicit conditional
financing case plus a separate failure/recovery case, or state the missing value.
Use judgment to choose assumptions with evidence and analogs, not to invent facts.
Do not claim precision, 99% superiority, or a measured win rate from unit tests.
