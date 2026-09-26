# Model assumptions and financial boundaries

## Event normalization

A reported effect is SIGNED: a cost reducing EBIT is negative; a gain is positive.
For an eligible exceptional booked effect, adjustment = -reported_effect.
Only disclosed, analyst-supported, isolated impacts with supported attribution
are eligible. Structural evidence blocks the adjustment. Missing reported metric
blocks it. Future impacts are omitted, not displayed. IDs prevent identical
impact IDs being counted twice; economically overlapping but differently named
impacts still require analyst review.

Revenue comparability (FX, divestitures, timing) is shown as evidence, never silently
added to historical revenue. Revenue/capex normalization is deliberately not
implemented. There is no generic transfer of an EBIT add-back to CFO or taxes.
A recurring restructuring cost cannot be removed by calling it exceptional.
Both gains and expenses follow the same rule. Accepted adjustments do not alter
original financial rows or automatically change forecast starting values.

Event status is a rule-based interpretation of supplied review labels, not proof.
A temporary event requires supporting and recovery evidence, recovery conditions
and falsifiers. Recurrence or structural evidence triggers concern; both kinds of
evidence produce a mixed classification. A source origin group is not independent
corroboration. Unknown origin remains unknown.

## Legacy DCF and reverse DCF

FCFF = NOPAT - net reinvestment. SBC is included in operating margins; existing
claims are in share equivalents. Future financing is NOT integrated in this mode.
With `reinvestment_mode=sales_to_capital`:

`net reinvestment = max(revenue_t - revenue_(t-1), 0) / sales_to_capital + base_net_reinvestment`

No automatic release of capital on falling sales. The base amount is NET capital
investment after depreciation, not gross capex. It must not count maintenance
capex without its corresponding depreciation adjustment a second time.
Reverse DCF recomputes this investment as growth changes. Other inputs remain
fixed; the result is conditional, not inferred consensus. A sampled monotonicity
check can refuse to solve; it is not proof of global uniqueness.
Terminal FCFF = final NOPAT × (1+g) × (1-g/ROIC). Positive final NOPAT and sensible
terminal rate ordering are mandatory. No automatic loss tax refund.

## Ownership model

This is a finite cash/ownership forecast followed by a hypothetical sale of the
business at the horizon. It is not the legacy DCF with dilution pasted onto it.

- Financing arrives at START of each year. Equity shares issued = gross proceeds /
  supplied issue price. Net cash received deducts financing fees. Debt increases
  principal and incurs that year's specified rate. Committed versus hypothetical
  is an analyst-supplied distinction; commitments are not verified automatically.
- EBIT includes economic SBC expense. This model treats it as a cash-equivalent
  compensation reserve and does not add a second SBC share issuance. It is an
  approximation, not an option valuation model or a GAAP cash forecast.
- Cash tax = max(EBIT - cash interest, 0) × tax rate. No NOL/refund inference.
- Cash to equity before financing = EBIT - interest - cash tax - net reinvestment
  - principal repayment. Dividend is paid at year end.
- Opening after financing and closing cash must cover minimum operating cash.
  Within-year cash troughs are not modeled. A gap returns unavailable value and
  the shortfall, not an automatic zero share price. Liquidation recovery is unknown.
- Terminal operating value capitalizes normalized FCFF using the supplied terminal
  capitalization rate and ROIC. Equity sale proceeds add only cash ABOVE minimum
  operating cash, then subtract debt and other claims. Final per-share sale proceeds
  and prior per-share dividends are discounted at the supplied cost of equity.
- Retained cash earns zero. No buybacks, recapitalization, auto debt capacity or
  optimal financing solver. Interest on all debt uses a supplied annual blended rate.
- Growth/margins and capital efficiency, terminal rate and Ke require a mutually
  coherent analyst rationale. The engine cannot establish fair issue prices,
  leverage equilibrium or market access. Rate validation is not economic validation.
- A no-hypothetical-financing branch and 0.5×/1×/1.5× issue-price sensitivity are
  reported. No probabilities are fabricated. A price haircut changes ownership,
  not the proceeds raised in these sensitivities.

Forecast years are abstract annual steps from the valuation date. The latest
reported annual revenue is used as the base; no automatic stub-period roll-forward
from fiscal year-end to the valuation date is performed. Material gaps need a
reviewed starting forecast before using results for a decision.

## Market reaction

The optional market context computes stock total return - beta × benchmark total
return over a supplied common window. This is a descriptive comparison, not a
causal event study or statistical significance test. A possible-overreaction flag
requires reviewed liquidity, no declared confounders, exclusively supported
temporary events, no modeled funding gap, and all supplied legacy DCF values above
a date-matched recent quote. Ownership-model cases are withheld from this shortcut.
It never establishes that the market is wrong and is not a trading trigger.

## Coverage deliberately withheld

No automatic financial-sector DCF, SOTP, NAV, employee option model, lease/ADR/FX
conversion, segment valuation, calibrated success probabilities or alpha claims.
Business driver multiplication uses analyst-supplied units; only addition enforces
matching unit strings. The driver tree is not automatically fitted to reported sales.
