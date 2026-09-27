# Research basis — v0.7

Methods added in v0.7 and where they come from. Web access during the build was
limited to search-engine summaries (direct page/PDF fetches were blocked by the
environment's network policy), so formulas below are the canonical published ones
and numeric reference points are quoted conservatively with their origin.

| Method | Use in the engine | Source |
|---|---|---|
| Corporate life cycle (start-up → decline) | `classify_company` stage; axis weights; valuation fit | Damodaran, *The Corporate Life Cycle* (2024); [NYU paper](https://pages.stern.nyu.edu/~adamodar/pdfiles/country/corporatelifecycleLongX.pdf); [blog](https://aswathdamodaran.blogspot.com/2024/08/the-corporate-life-cycle-corporate.html) |
| Six stock categories | `classify_company` Lynch category and notes | Lynch, *One Up on Wall Street*; [summary](https://pictureperfectportfolios.com/peter-lynch-six-stock-categories/) |
| Base rates / outside view | forecast base-rate check; master_process step 4 | Mauboussin, Callahan & Majd, *The Base Rate Book* (Credit Suisse, 2016); [Bayes and Base Rates](https://www.morganstanley.com/im/publication/insights/articles/article_bayesandbaserates_ltr.pdf) |
| Growth fade (>20% growers → ~8% in 5y, ~5% in 10y; ~85% of supergrowers fail to sustain) | `forecast_base_rate` flags | Koller, Goedhart & Wessels, *Valuation* (McKinsey); [Grow fast or die slow](https://www.mckinsey.com/~/media/McKinsey/Industries/Technology%20Media%20and%20Telecommunications/High%20Tech/Our%20Insights/Grow%20fast%20or%20die%20slow/Grow%20fast%20or%20die%20slow.pdf) |
| ROIC reverts toward cost of capital (~8%); persistence tied to business model | ROIC grading vs 8% reference | Mauboussin, [ROIC and the Investment Process](https://www.morganstanley.com/im/publication/insights/articles/article_roicandtheinvestmentprocess.pdf) |
| Five moat sources | company_type moat test | Morningstar, [economic moat](https://www.morningstar.com/investing-terms/economic-moat); [VanEck white paper](https://www.vaneck.com/us/en/investments/morningstar-wide-moat-etf-moat/what-makes-a-moat-white-paper.pdf/) |
| Normalized cyclical earnings | cycle peak/trough flags; normalized margin | Damodaran, [Ups and Downs: Valuing Cyclical and Commodity Companies](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/commodity.pdf) |
| Piotroski F-score | `forensic_scores`, plain "מגמה כספית" | Piotroski (2000), *Journal of Accounting Research* |
| Altman Z (1968) and Z'' (1995) | `forensic_scores`, plain "סיכון קריסה" | Altman (1968), *Journal of Finance*; Altman (1995/2005) emerging-market Z'' |
| Beneish M-score (8 variables, cutoff −1.78) | `forensic_scores`, plain "אמינות הדוחות" | Beneish (1999), *Financial Analysts Journal* |
| Rule of 40 (median public SaaS ~25–30%, ~28% clear 40) | software/platform item | [Aventis 2026 data](https://aventis-advisors.com/rule-of-40-in-saas-2026/); [getaleph](https://www.getaleph.com/answers/rule-of-40-saas-2026) |
| Scuttlebutt | master_process step 7 | Fisher, *Common Stocks and Uncommon Profits* (1958) |
| Expectations investing / reverse DCF | master_process step 11 (existing reverse_price) | Rappaport & Mauboussin, *Expectations Investing* |
| Pre-mortem | master_process step 12 | Klein, "Performing a Project Premortem", HBR (2007) |

Not included: calibrated return predictions, proprietary factor weights, or
claims that any score predicts stock performance. Videos were not watched; where
video content informed the search, only published text summaries were used.

Real-data check: `examples/real/avgo_plain_2025.json` (Alpha Vantage annual
statements FY2021–FY2025, retrieved 2026-09-27) must classify Broadcom as a mature
compounder (mature_growth / stalwart) with the FY2024 VMware acquisition flagged,
not as a fast grower from acquisition-inflated growth.
