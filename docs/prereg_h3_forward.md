# Pre-registration: forward test of H3 (momentum + near high + achievable price)

Written 19.10.2026 after the historical test (docs/GAP_TEST_RESULTS.md), so history cannot confirm it;
only months after this date count.

Selection each month-end t from 2026-11: the gap_backtest universe and definitions unchanged
(TTM revenue ≥ $50M, market cap ≥ $300M, non-financial); revenue-surprise rank ≥ 0.9 and earnings-surprise
rank ≥ 0.5 among companies whose new quarter became usable during the month; price ≥ 85% of the 12-month
high. Sleeve A = all such names; sleeve B = names in the top two P(beat) quintiles (live base-rate table
refit monthly on completed outcomes only). Equal weight, 3-month hold, overlapping cohorts.

Recording: every month's A and B lists and the P(beat) inputs are written to the forecast ledger before
returns are known (hash chain). Delisted names keep their last price, then 0%.

Decision after 24 live months (2028-11), pass only if all hold:
- B − A ≥ +3 pts/yr and B − SPY ≥ 0;
- B's share of names halving within 12 months ≤ A's;
- B beats A in ≥ 55% of 3-month cohorts.
Fail → drop the combination. Not decided → extend 12 months once, then drop.
No change to rules during the test; bugs are logged with both results.
