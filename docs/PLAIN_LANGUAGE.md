# Eye-level verdicts — v0.6

`plain_verdict` turns reported numbers into judgements a non-specialist can use.
Input (`plain_version: 1`): `company`, `ticker`, `as_of`, `currency`, `archetype`,
`stage` (mature/growth/emerging), `history` (1–15 fiscal years, oldest first, each
with `fiscal_year`, `period_end`, `revenue`, `source_ids`, and optionally
`gross_profit`, `operating_income`, `net_income`, `operating_cash_flow`, `capex`,
`shares_diluted`), optional `balance` (`cash` = unrestricted, `debt`), optional
`research_status` (latest research_review status) and optional `valuation_case`
(a value_company input — executed inside the call; results are never typed by hand).

## Grades (5 = מצוין … 1 = מדאיג)

| Metric | 5 | 4 | 3 | 2 | else 1 |
|---|---|---|---|---|---|
| Revenue growth, last year | ≥30% | ≥15% | ≥5% | ≥0% | declining |
| Gross margin — software/platform/biotech | ≥75% | ≥60% | ≥45% | ≥30% | |
| Gross margin — industrial/infra/other | ≥50% | ≥38% | ≥25% | ≥15% | |
| Gross margin — consumer/commodity | ≥35% | ≥25% | ≥17% | ≥10% | |
| Operating margin (same three profiles) | 30/20/12% | 18/12/8% | 8/6/4% | ≥0 | loss |
| Free cash / revenue | ≥20% | ≥10% | ≥3% | ≥0% | burning |
| Operating cash / net income | ≥1.1 | ≥0.9 | ≥0.7 | ≥0.5 | |
| Net debt / operating cash (years) | net cash | ≤1 | ≤3 | ≤5 | >5 or no cash flow |
| Runway when burning (years) | ≥5 | ≥3 | ≥1.5 | ≥0.75 | |
| Share count growth per year | ≤0% | ≤2% | ≤5% | ≤10% | |

Emerging stage: an operating loss is graded 3 when the margin is improving, else 2;
cash burn is not graded below 2 — runway carries the funding risk. Banks, insurers
and REITs are refused (capital/NAV metrics required).

## Axes and bottom line

Quality = product margin, operating margin, free cash, earnings backing. Growth =
revenue growth. Financial strength = debt/cash, runway, dilution. Price = today's
quote versus executed values: at or below bear → "זול — גם בתרחיש הרע"; ≤80% of
base → cheap; ≤110% → fair; ≤ bull → expensive; above bull → very expensive; a
funding block is a warning, never a value. The bottom line crosses the business
average (≥3.5 good, ≥2.5 average) with the price bucket.

Confidence drops for an unfinished research_review, demo inputs, unreviewed
assumptions, >80% terminal value, a quote older than 7 days, fewer than three
years, or missing metrics. These are transparent rules of thumb for communication;
they do not verify inputs, forecast prices, rank stocks or place orders.
