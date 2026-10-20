# Limitations, by severity (v0.11.1, 20.10.2026)

What the engine cannot do yet, or does only partly. Each item says what it affects and what would fix it.
Companion to docs/EVIDENCE_STATUS.md, which separates what tests prove from what they do not.

## Critical — limits whether any conclusion can be trusted

1. **No proof that the method beats an index.** The gap test calibrates "what the price requires" (H1 passed)
   but ranking by the price/value gap failed (H2). The pipeline's verdicts have no track record. Every frozen
   decision goes to the forecast ledger so that 12/24/36-month comparisons become possible.
   Fix: forward ledger, delisted-company prices, a pre-registered quality-at-a-reasonable-price test.
2. **Primary filings could not be read from this environment until 20.10.2026.** On 20.10.2026 data.sec.gov and
   www.sec.gov answered from the container (companyfacts, submissions and the 10-Q/8-K documents for GOOGL and NVDA were
   fetched and read); SEC requires a contact e-mail in the User-Agent, supplied by the user for the session and never
   stored in the repository. Treat access as variable and re-check it per session. Earlier text, kept for the record: data.sec.gov and www.sec.gov are not reachable
   from the container and web pages cannot be fetched; only search excerpts and vendor feeds (FMP, Alpha
   Vantage, IB) are. Newer quarters come from single-source excerpts, the opening balance is read indirectly
   (source access `secondary_access`), and data status is "limited". Fix: allow the SEC domains in the
   environment's network policy, or run collection where filings can be fetched.
3. **Independent review is an input, not automatic.** A price verdict stays a visible draft until a reviewer signs
   it, bound to the executed valuation and to the analyst's inputs (`rv['binding']`), dated between the cutoff and
   today and not named as an author. The engine checks the sign-off, the binding and the name; it cannot verify who
   the reviewer is (an author under another name is not detected) or how diligent the review was.

## High — limits coverage or a whole category of answers

4. **No structured statements for GOOGL and NVDA today.** Their 2026-10-20 collection holds only researched evidence
   bundles and IB quotes stamped after the 2026-10-19 cutoff (skipped as look-ahead); Alpha Vantage hit its daily limit
   on the first call and FMP statements were not collected. The pipeline cannot value them and no multi-company
   comparison has run. Fix: collect FMP as-reported statements (or Alpha Vantage) after the quota resets and a quote
   dated on or before as_of.
5. **Vendor quotas and plan limits.** FMP's plan denies the standard statement endpoints (as-reported works), index
   constituents and most symbols, and has a daily quota; Alpha Vantage allows 25 calls a day; SEC frames (code exists) are
   blocked. Discovery runs on rows or FactBooks already collected, not on the whole market.
6. **REITs, pre-revenue biotech and conglomerates.** Banks, insurers, broker-dealers and managed care are valued by
   residual income since 20.10.2026 (regulatory capital not checked: risk-weighted assets are not in companyfacts).
   REITs (NAV needs property NOI), pre-revenue biotech (rNPV needs program data) and conglomerates (segments) still have
   no executed valuation and end as interesting-not-yet-valuable instead of a forced operating model.
7. **Historical tests are survivorship-biased.** Prices exist only for currently listed tickers; results
   are an upper bound.

## Medium — affects precision or needs judgement

8. **Leverage drift (deferred).** One cost of equity and terminal WACC are used for the whole path while leverage
   changes as debt is repaid or drawn; on the levered test case the value is about 1.3% below a period-consistent one.
   Fix: a per-period cost of equity in the valuation contract.
9. **Split test without price history.** `pipeline.factbook_from_run_dir` does not fill `market.price_history`,
   `price_history_basis` or `price_history_bar_date`, so on pipeline FactBooks a split is decided only by a dated
   corporate action or fully restated weighted shares; otherwise the step stays unresolved and counts as dilution
   (conservative; it can overstate dilution). Fix: fill an as-traded close series with its basis and bar dating.
10. **Sensitivity grid still shifts 1:1.** `valuation.sensitivity` (the report's "Sensitivity grid", rate +-2pp and
    terminal growth +-1pp) moves the terminal WACC by the full rate shift, while the IRR, the discount-rate band, the
    implied cost of equity and the decision use the equity weight (1 - w). For levered companies the grid overstates rate
    sensitivity.
    Fix: use `valuation_tools.debt_weight` in `valuation.sensitivity`.
11. **Discount rates are a framework, not a measurement.** Risk-free + equity premium + documented business-risk
    add-ons, relevered on the model's own value. One point of required return moves a long-duration value by about 30%;
    the decision marks a verdict inside its band as marginal, but the band is only as good as the framework.
12. **Uncalibrated model constants.** Debt capacity 3x EBITDA, equity issue fee 3%, revenue-scale growth ceilings,
    cycle reversion years, moat-proxy ramps and the lender thresholds are documented assumptions, not peer studies.
    Plans funded by new equity have a per-share value that depends on the issue price (labelled price-dependent and
    conditional).
13. Consensus estimates have no point-in-time history; vendor statements retrieved later may include restatements
    (FMP as-reported mitigates for statements). The FMP screen adapter assumes growth fields are (x_t - x_t-1) / x_t-1
    with the raw sign; this was not confirmed against a live response.
14. Qualitative judgement (moat, management, AI exposure) is analyst input with evidence ids. The engine checks that
    evidence exists, was available at the cutoff and is of an accepted kind; it does not judge what the evidence means.
15. Acquired revenue is separated from organic growth when the filings carry pro forma revenue; without it the
    pre-deal growth is used and flagged high. Months of the prior year before a closing are not added back
    (conservative), and an acquisition whose goodwill and spend stay below the analysis thresholds is not detected.
16. Portfolio exposure weights are analyst judgement unless measured data is supplied; IB themes are a vendor
    classification, not weights. Positions in other currencies need FX rates.

## Found on the GOOGL/NVDA runs (20.10.2026)

21. **No 'cyclical risk without normalization' option.** The card offers cyclical true (normalize to the history's mean
    revenue trend and margin) or false; NVIDIA needs the cycle in the bear case while a 2017-2026 mean (~$68bn revenue)
    is meaningless after a 30x change in scale. The run used cyclical false with an explicit downturn bear path; a card
    option `normalize: false` with the cycle kept in the cost of equity would be cleaner.
22. **Private stakes are excluded.** Equity securities without a readily determinable fair value (NVIDIA $51bn, Alphabet
    $131bn non-marketable securities) are not in the opening balance: conservative, about $2 and $11 per share.
23. **Mandatory convertible preferred is a terminal claim.** Entered through `terminal.other_claims`, it is discounted from
    the end of the horizon although it converts in 2029; the error is small (Alphabet: about $1 per share) but systematic.
24. **Explicit reinvestment uses historical sales-to-capital for every year.** For a hyper-grower the first years reinvest
    $150-280bn a year (NVIDIA) when the incremental capital is mostly working capital; an analyst override is the only lever.

## Found on the discovery handoff runs (20.10.2026)

25. **No currency translation or ADS ratio.** Issuers reporting in another currency (Noah, Trip.com: CNY) and depositary
    shares whose ratio is not 1 (Noah: about 5 shares per ADS) are blocked by `handoff.identity_check` and the lane is
    backfilled; analysing them needs a manual FactBook in the reporting currency, a dated FX rate and the deposit ratio.
26. **Foreign private issuers are annual-only in SEC data.** 20-F filers (Global-E, monday.com) have no 10-Q facts: the
    balance sheet and TTM fields are up to a year old and are limited by dataqa; interim 6-K figures need an evidence bundle.
27. **Debt tags are still a coverage risk.** The map now reads notes payable, loans payable and convertible notes and sums
    component-only borrowings, and a debt line that disappears without a repayment blocks instead of reading zero; an
    issuer using another custom concept can still be missed when no earlier line exists to notice the gap.
28. **The listing price is a fallback.** Without a saved broker quote the handoff uses the listing file's last sale (dated
    to the session it belongs to; exchange holidays not modelled).
29. **Drafts by construction.** The automatic handoff supplies no analyst card, evidence or independent reviewer, so every
    casefile is a draft with price claims withheld; the comparison says so on every row. Business-understanding questions
    (why customers pay, switching, what proves the thesis wrong) stay open until an analyst answers them with evidence.

## Low

17. The lender checks differ at the margin: the model card's rule has revenue and capex limits and a fee-platform
    exception, the forecast's `possible_lender` warning does not, so an airline-like or fee-platform company can carry an
    unresolved `possible_lender` issue (a draft until a reviewer resolves it) while it is valued as an operating business.
18. The report does not print `rv['binding']`; a reviewer reads the values to sign from the casefile JSON.
19. Hebrew output covers the paragraph and verdict; the detailed report is in English.
20. Performance: single-company runs take seconds; no batch scheduler for monitoring (manual or session
    check-ins).

## Found while completing the engine (20.10.2026)

30. **The engine's ranking has no demonstrated edge** (docs/BACKTEST_ENGINE_2026-10-20.md): on today's large caps,
    2016-2025, the top fifth beat the universe average by 7.6 points a year with t 1.0, mostly in 2020, about what a
    free-cash-flow-yield rank did. Edges are shrunk by the measured reliability (0.40) and the index is the default.
31. **Connector channels depend on quotas.** Alpha Vantage (25 calls a day) and FMP were exhausted on 20.10.2026; their
    parsers follow the documented response formats and must be checked on the first live payload. LunarCrush returns a
    markdown table in limited mode. SEC channels are automatic and cover every filer.
32. **Discovery events need reading.** A 13D may be a founder, a foundation or a parent, not an activist; 8-K item 1.01
    covers credit facilities as well as customer contracts; Forms 15 and 25 are often other security classes. They are
    leads or events, never support by themselves. Revenue acceleration can be an acquisition or a commodity price.
33. **Segment view and pay measures are evidence, not inputs.** The segment-built value is reported beside the base case
    (which one holds is a research question); the pay orientation reads the stated measures, not the plan's weights;
    small reporting companies may omit the measures list.
34. **Convertible terms from XBRL are partial.** Principal is taken at par (accreting notes need the footnote), maturity
    can be only a year from the instrument name, and a 10-Q without a conversion term keeps the block (Rubrik, Okta)
    until the annual report is read.
35. **Engine version drift in the tests.** The first walk-forward run used the engine before the 20.10 fixes; the second
    run on the current engine is reported as it comes out, and nothing is tuned to it.
