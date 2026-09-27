# Annual fair-value underwriting: current date through 2030 and beyond

Deliver a simple Hebrew table: date, bear, base, bull, fixed current quote,
base gap versus quote. Include TODAY separately from 31 December of each year.
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
4. Forecast business drivers under bear/base/bull, cite sources and write specific
   analyst assumptions and falsifiers. Guidance is not reported revenue. Decompose
   actual cohorts/segments; use average units operating during the period.
5. Build capex, replacement cohorts, taxes, prepayment recognition, debt maturity,
   interest, issuance prices/fees and share count. Match periods and currencies.
6. Run value_company. Run valuation_diagnostics for the operating route. Review
   funding gaps, terminal dependence, terminal margin jumps and unreviewed inputs.
7. Resolve the largest VALUE-SENSITIVE uncertainties. Use a full contract or note
   when needed; repeated articles do not resolve a missing contractual fact.
8. Save annual_valuation_input in the dossier, execute research_review, checkpoint,
   then report the actual annual_values. Never handwrite a target table.

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
