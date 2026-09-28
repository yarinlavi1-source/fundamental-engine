# AXTI audit and migration — 2026-09-28

This is a data/process audit, NOT a replacement price target. The Claude branch at
96f33c77e26673a977d41f0dc01a5ccfbedee4e1 contains a historical AXTI plain fixture,
but no complete AXTI valuation_case reproducing the reported $8 base/$38 2030 bull.
Those figures must not be treated as validated engine reference outputs.

## Primary balance reconciliation

Source: [Q2 2026 10-Q](https://www.sec.gov/Archives/edgar/data/1051627/000143774926027677/axti20260630_10q.htm),
consolidated balance and investments note, inspected 2026-09-28. USD absolute:

| Metric | 2026-06-30 |
|---|---:|
| Cash/equivalents (excluding restricted) | 412,167,000 |
| Restricted cash | 33,050,000 |
| Short-term investments | 5,031,000 |
| Long-term investments | 298,600,000 |
| Common shares outstanding | 65,570,000 |
| Quarter weighted-average diluted EPS shares | 63,474,000 |
| Common shares outstanding at 2025-12-31 | 55,337,000 |

Common share balance rose about 18.5% between these two dates, not the roughly 45%
obtained by mixing a quarterly diluted average with the prior annual EPS average.
This is not fully diluted September shares. Review awards, conversion rights,
subsequent issuance, preferred stock, subsidiary claims and security adjustments.
The old market_cap was removed: it embedded the wrong share denominator.
The historical AV-derived debt figure still requires instrument-level reconciliation.
Investments must be modeled separately, with restrictions/haircuts and liquidation
assumptions. No automatic claim that cash covers every future expansion commitment.

## Research gaps to resolve before rerunning value

- Rebuild the exact source-linked forecast inputs and save them with output/hash.
- Bridge the June balance to the valuation date; do not rename June cash September.
- Reconcile GAAP and adjusted attributable EPS, fiscal year and analyst snapshot.
  [StockAnalysis](https://stockanalysis.com/stocks/axti/forecast/) displayed 2027
  adjusted EPS 2.25 and revenue 460.69M at audit time, conflicting with the report's
  0.79 EPS. These are third-party estimates, not realized earnings; re-fetch through
  available connectors and preserve the original snapshot before using them.
- Incorporate the [Lumentum capacity agreement](https://www.businesswire.com/news/home/20260729446850/en/AXT-Inc.-Announces-Long-Term-Supplier-Agreement-with-Lumentum).
  The release describes a term through 2031 and two 43.5M deposits; the second's
  timing/terms are to be determined in 2028. Read the actual contract/8-K and treat
  deposits as shipment credits/deferred obligations, not free profit or certain
  receipt dates. This audit does not claim cash has already been collected.
- Model capacity/yield, customer qualification, export permits, competitive supply,
  volume versus ASP and expansion/replacement capex. Explain growth after 2030.
- Attribute Tongmei minority value to its own cash flows, not all parent assets.
- Reconcile analyst targets by horizon/method; do not alter DCF to hit their prices.

The fixture is a regression for definitions and inflection recognition. It is not
an investment backtest or a certified financial dataset. All original annual rows
not explicitly reconciled above remain provider observations requiring review.
