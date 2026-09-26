# Input contract v1 (legacy sections)

For current optional sections and changed basis support, see INPUT_V02.md.
The caveats below about separate financing describe the legacy `valuation` model.

The executable reference is `examples/emerging_demo.json`; `engine.validate`
performs structural and domain checks. JSON NaN/Infinity are rejected by the CLI.

All money and share counts use ABSOLUTE units. Ratios are decimal fractions.
One common currency is mandatory; no FX conversion, splits or ADR inference.
Date strings are ISO YYYY-MM-DD, interpreted as end-of-day for research purposes.

## Required fields

| Field | Contract |
|---|---|
| schema_version | 1 |
| as_of | research cutoff |
| currency / unit | common ISO currency label / `absolute` |
| company | id, name, ticker, currency, sector |
| sources | unique id, title, kind, locator, published_at, available_at |
| financials | zero or more annual standalone GAAP rows, start/end/available_at/currency/basis/source_ids |

Annual rows may contain nullable revenue, gross_profit, ebit, net_income, cfo,
capex, sbc, cash, debt, shares, invested_capital. Source references apply to the
whole row in v1: use one consistent normalized period, not mixed provider values.
No automatic GAAP/IFRS reconciliation is provided. Fiscal dates are retained.

## Optional sections

- quote: price, currency, as_of, source_ids.
- signals: unique id, dimension, claim, direction, review_status, observed_at,
  reviewed_at, source_ids. Dimensions: market/adoption/advantage/execution/unit_economics.
  Direction: positive/negative. Status: unverified/supported/contradicted.
- milestones: id, description, due_at, success_criterion, failure_criterion.
- thesis: user-supplied narrative. This is commentary, not a verified historical
  statement. Future-known information must not be included in a historical input.
- funding: assumptions_as_of, source_ids, available_cash, ordered periods.
  Each period has operating_cash_flow, capex, debt_repayment, committed_financing.
  CFO includes cash interest and taxes; uncommitted raises must not be entered.
- valuation: assumptions_as_of, source_ids, share_equivalents, excess_cash,
  debt, other_claims, sbc_policy, scenarios. Policy must equal
  future_expensed_existing_claims_in_shares.
- scenario: name, rationale, wacc, terminal_growth, terminal_roic, years.
- forecast year: growth, operating_margin, cash_tax_rate, reinvestment.

Periods of the funding model are sequential periods of the user's chosen
duration, normally quarters. The report labels them by index, not fiscal date.
Use the same period length and document it in the thesis. Funding inputs and DCF
inputs are independently supplied; no automatic reconciliation is claimed.

## Date controls and remaining limits

Future available financial rows, source evidence, quotes, signal reviews and
scenario assumptions are excluded. Snapshots preserve original input/report JSON
and input hash. The schema does not verify whether user-entered dates are true.
The text of a historical thesis and planned milestone must also be reviewed for
look-ahead by the analyst. This is not a certified point-in-time dataset.

SEC extraction uses `filed` dates and the latest eligible version for an exact
period. Same-day conflicting values raise an error. It does not infer intraday
availability or solve custom taxonomy/segment accounting. Current companyfacts
responses may not retain every historically removed/corrected filing.

## Non-goals of validation

Validation checks arithmetic/structure and eligibility. It cannot certify an
authentic source, reasonable growth forecast, patent moat or economic feasibility.
Assumptions remain visible and require analyst review.
