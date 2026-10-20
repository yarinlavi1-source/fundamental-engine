# Discovery lanes vs the S&P 500: walk-forward test (20.10.2026)

Question: do the six discovery lanes find stocks that beat the index? Code: `fundamental_engine/backtest.py`,
`scripts/collect_backtest.py`, `scripts/run_backtest.py`. Raw data (SEC frames 2009-2025, Yahoo monthly prices for
7,311 current tickers) is outside Git under `runs/backtest/`; every number below is reproducible from the scripts.

## Method

- Formation every year (1 July, and 1 October as a robustness variant), 2012-2025: 14 periods, each held 12 months.
- At formation F the screen sees only annual SEC frames of earlier calendar years, and only values whose period ended at
  least 95 days before F (annual reports are filed within 60-90 days). Rules of the lanes are today's rules (see bias).
- Universe: NYSE/Nasdaq US-GAAP filers with revenue, market value >= $1bn at F (split-adjusted close x reported shares x
  later splits). 402 issuers whose SEC share count and quoted price are on different bases (ADRs quoted per depositary share,
  units, class mismatches; checked against today's published market value) are excluded from every year.
- Portfolios: each lane's candidates equal-weight (and cap-weighted), total return with dividends, minus 0.2% a year.
- Benchmarks: SPY (S&P 500, total return) and the equal-weight / cap-weighted universe built from the same data.

## Results

| Lane (avg names) | CAGR Jul | vs average stock (EW), Jul / Oct | vs S&P 500, Jul / Oct |
|---|---|---|---|
| All lanes (46) | 16.4% | +3.1 pp (t 1.4) / +1.1 pp (t 0.5) | +2.4 pp (t 0.8) / -0.5 pp (t -0.2) |
| Shortlist (18) | 18.7% | +5.7 pp (t 1.6) / +2.8 pp (t 1.0) | +4.9 pp (t 1.2) / +1.2 pp (t 0.4) |
| Quality compounder (10) | 19.5% | +6.7 pp (t 1.9) / +1.7 pp (t 0.6) | +6.0 pp (t 1.5) / 0.0 pp |
| Quality at a fair price (12) | 14.1% | +0.9 pp / +2.0 pp | +0.2 pp / +0.4 pp |
| Early commercial growth (6) | 25.9% | +15.9 pp (t 1.8) / +14.6 pp (t 1.3) | +15.2 pp (t 1.7) / +13.0 pp (t 1.2) |
| Turnaround (9) | 18.3% | +4.6 pp (t 1.3) / +3.0 pp (t 1.2) | +3.8 pp (t 1.2) / +1.4 pp (t 0.5) |
| Recovery or mispricing (13) | 9.9% | -3.0 pp / **-5.4 pp (t -2.4)** | -3.8 pp / **-7.0 pp (t -2.5)** |
| Structural tailwind (12) | 8.8% | -3.5 pp / **-6.5 pp (t -2.3)** | -4.2 pp / **-8.1 pp (t -2.3)** |

Benchmarks, same years: S&P 500 14.8% a year; equal-weight universe 13.5% (Jul) / 12.9% (Oct); cap-weighted universe
17.7% / 16.6%.

## What this says

1. **No demonstrated edge over the S&P 500 from the numeric screen alone.** The combined lanes are within noise of SPY, and
   the result moves by 3 points a year between two formation months.
2. **Survivorship bias is large**: the cap-weighted universe of today's survivors beat SPY by about 3 points a year, so every
   stock portfolio here is flattered by roughly that much against SPY. Positive excess of 2-5 points is consistent with zero.
3. **Two lanes hurt in both variants** (recovery/mispricing and structural tailwind: -4 to -8 points a year against SPY). Their
   research priority is lowered in `candidates.research_queue` (`LANE_EVIDENCE`); they are kept and re-tested, not deleted.
4. **Early commercial growth** shows large excess in both variants but with six names a year and t below 2: promising, unproven.
5. The earlier valuation-gap test (docs/GAP_TEST_RESULTS.md) also found no return edge from ranking by valuation gap alone.

The honest conclusion for portfolio construction: the index is the default holding; active positions need research beyond the
screen (business understanding, valuation against an engine-consistent market return, risks) and are sized modestly; every
recommendation is recorded in the forward ledger so the method is judged on results it could not have seen.

## Bias and limits (read before using any number)

- Survivorship: only currently listed tickers have prices; delisted, acquired and bankrupt companies are missing.
- In-sample rules: the lane rules were written in 2026 with knowledge of the period; this is not an out-of-sample test of
  their design. The forward ledger is the out-of-sample test.
- One observation a year: 14 periods cannot separate a few points of edge from noise (t below about 2 is noise).
- Sector labels are today's listing labels (minor look-ahead in the cyclical cue); annual frames only; no capacity or tax.
- Found and fixed while running this test: market values were divided by the split factor at formation, which inflated
  companies with later reverse splits by up to millions of times and let failing micro-caps into the $1bn universe; that
  version overstated the lanes against the average stock (+7 points a year instead of +1 to +3). The numbers above are after
  the fix.
