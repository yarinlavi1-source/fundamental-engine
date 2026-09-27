# Forensic and quality scorecards

`forensic_scores` (and plain_verdict) compute established screens from history rows.
They direct attention; they are not verdicts, fraud findings or return predictions.
Missing fields make a screen 'unavailable' — never fill them to force a score.

| Screen | What it asks | Read it as | Known blind spots |
|---|---|---|---|
| Piotroski F-score (2000) | 9 binary signals: profit, cash flow, ROA trend, accruals, leverage, liquidity, dilution, gross margin, asset turnover | 8–9 improving, 0–2 deteriorating | Built on value stocks; young growth firms fail signals by design |
| Altman Z (1968) / Z'' (1995) | Liquidity, retained earnings, profitability, leverage, turnover | Distress / grey / safe zones | Not for banks; negative retained earnings from buybacks depress it; young firms |
| Beneish M-score (1999, 8 variables) | Receivables, margins, asset quality, growth, depreciation, SG&A, leverage, accruals | Above −1.78 manipulation risk (−2.22 stricter screen) | Fast growers score high on sales growth by construction |
| ROIC / incremental ROIC | After-tax operating profit per unit of invested capital | Versus ~8% cost-of-capital reference | Tiny or negative invested capital (asset-light, buybacks) is not meaningful; goodwill-heavy acquirers look worse |
| Rule of 40 (software) | Revenue growth + FCF margin | ≥40% healthy; recent public medians ~25–30% | Stock compensation inflates FCF; pick FCF, not adjusted EBITDA |
| Forecast base rate | Implied 5-year revenue CAGR in the base scenario versus history and fade evidence | >20% for 5 years is rare | Contracted backlog/capacity can justify departures — cite it |

## Follow-up when a screen flags

- Beneish high: receivables versus revenue, inventory build, capitalized costs,
  changes in depreciation lives, non-recurring gains, auditor changes, cash conversion.
- Altman distress: debt maturities, covenants, refinancing access, liquidity runway.
- Piotroski weak: which signals failed — leverage and dilution failures matter more
  for existing holders than one-year margin noise.
- ROIC below cost of capital: growth destroys value until returns rise; show why they
  will.
- Base-rate flag: name the contract, capacity or product evidence that justifies the
  departure, or lower the base case.

Report the flag and its plain meaning next to the verdict; never hide it in an appendix.
