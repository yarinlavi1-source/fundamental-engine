# Limitations, by severity (v0.11, 20.10.2026)

What the engine cannot do yet, or does only partly. Each item says what it affects and what would fix it.
Companion to docs/EVIDENCE_STATUS.md, which separates what tests prove from what they do not.

## Critical — limits whether any conclusion can be trusted

1. **No proof that the method beats an index.** The gap test calibrates "what the price requires" (H1 passed)
   but ranking by the price/value gap failed (H2). The new pipeline's verdicts have no track record. Every
   frozen decision goes to the forecast ledger so that 12/24/36-month comparisons become possible.
   Fix: forward ledger, delisted-company prices, a pre-registered quality-at-a-reasonable-price test.
2. **Primary filings cannot be read from this environment.** data.sec.gov and www.sec.gov are not reachable
   from the container and web pages cannot be fetched; only search excerpts and vendor feeds (FMP, Alpha
   Vantage, IB) are. Newer quarters therefore come from single-source excerpts, the opening balance is read
   indirectly, and data status is "limited". Fix: allow the SEC domains in the environment's network policy,
   or run collection where filings can be fetched.
3. **Independent review is an input, not automatic.** A price verdict is a visible draft until a reviewer
   who is valid and independent of the author signs it. The engine checks the sign-off; it cannot check the
   reviewer's diligence.

## High — limits coverage or a whole category of answers

4. **Universe discovery cannot run at full scale here.** FMP's plan denies index constituents and most
   symbols and has a daily quota; Alpha Vantage allows 25 calls a day; SEC frames (code exists) are blocked.
   Discovery runs on rows or FactBooks that were collected, not on the whole market.
5. **Financial companies.** Banks, insurers, REITs and similar balance-sheet businesses have no executed
   pipeline valuation (valuation.py has residual-income/NAV calculators; the forecast does not build their
   inputs yet). They end as insufficient evidence instead of a forced operating model.
6. **Historical tests are survivorship-biased.** Prices exist only for currently listed tickers; results
   are an upper bound.

## Medium — affects precision or needs judgement

7. Consensus estimates have no point-in-time history; vendor statements retrieved later may include
   restatements (FMP as-reported mitigates for statements).
8. Qualitative judgement (moat, management, AI exposure) is analyst input with evidence ids. The engine
   checks that evidence exists and was available at the cutoff; it does not judge what the evidence means.
9. Portfolio exposure weights are analyst judgement unless measured data is supplied; IB themes are a vendor
   classification, not weights. Positions in other currencies need FX rates.
10. Discount rates come from a risk-free + equity premium framework with stated add-ons; one point of
    required return moves a long-duration value by about 30%; a verdict that clears the hurdle by less than
    that margin is fragile and must be read with the stated rate band.

## Low

11. Hebrew output covers the paragraph and verdict; the detailed report is in English.
12. Performance: single-company runs take seconds; no batch scheduler for monitoring (manual or session
    check-ins).
