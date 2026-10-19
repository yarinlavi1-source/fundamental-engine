# Primary-source acceptance cases

Prepared 2026-09-26. Figures were manually reviewed against issuer earnings-release
financial tables and converted from USD millions to absolute USD. Source metadata
preserves the issuer URL, original release date and actual retrieval date.
These are retrospective research reconstructions, not live feeds or performance tests.
The releases' financial tables are unaudited; no claim of audited annual-filing
verification or independent corroboration is made.

| Case | Cutoff | Current/prior annual revenue, USD m | Other numeric check |
|---|---|---|---|
| Meta FY2022 | 2023-02-01 | 116,609 / 117,929 | CFO 50,475 minus gross PP&E purchases 31,431 = 19,044 |
| Boeing FY2024 | 2025-01-28 | 66,517 / 77,794 | CFO -12,080 minus PP&E 2,230 = -14,310 |
| Nike FY2025 | 2025-06-26 | 46,309 / 51,362 | Gross profit 19,790; net income 3,219 |

Meta's simple CFO-minus-gross-capex result differs from its issuer FCF definition:
the issuer nets PP&E proceeds and includes finance-lease principal. The engine
preserves that distinction in the case input rather than substituting the issuer
figure under another definition. Its disclosed FX bridge is comparability evidence,
not added-back revenue. Restructuring is not erased when further charges are expected.

Boeing combines a resolved work stoppage with continuing program-performance
questions. Result: mixed, with no invented amount of recovered or deferred revenue.
Nike has reported operating pressure and management recovery expectations;
expectations do not constitute demonstrated recovery. Result: structural concern
requiring investigation, not a claim that recovery is impossible.

Sources:
- Meta: https://investor.atmeta.com/investor-news/press-release-details/2023/Meta-Reports-Fourth-Quarter-and-Full-Year-2022-Results/default.aspx
- Boeing: https://investors.boeing.com/investors/news/press-release-details/2025/Boeing-Reports-Fourth-Quarter-Results/default.aspx
- Nike: https://investors.nike.com/investors/news-events-and-reports/investor-news/investor-news-details/2025/NIKE-Inc--Reports-Fiscal-2025-Fourth-Quarter-and-Full-Year-Results/default.aspx

Reproduce the fixtures with `python scripts/build_cases.py`. Run each through
`python -m fundamental_engine analyze examples/real/NAME.json`.
Versioned HTML examples are in `examples/reports/`; generated snapshots in `runs/`
are ignored by Git. `RealCaseTests` checks selected figures and withheld conclusions.

No real case includes a quote or an invented valuation forecast: all three therefore
remain `research_incomplete` with market overreaction `not_established`. Full numeric
valuation/financing/MCP execution is exercised by the separate clearly synthetic
business_financing_demo case. That is a material coverage limit, not a real-company
valuation validation claim.

## ServiceNow driver regression (v0.9.2)

`examples/real/now_drivers_2026q3.json` is the executed 2026-09-28 ServiceNow input
as a driver spec, with a dated quote and full underwriting. Its balances came from
Alpha Vantage and search excerpts (sources carry `retrieval` labels), so it tests the
conversion and the reduced-confidence path, not the value's correctness. See
`docs/NOW_RUN_2026-09-28.md`; `NowRegressionTests` pins the executed outputs.
