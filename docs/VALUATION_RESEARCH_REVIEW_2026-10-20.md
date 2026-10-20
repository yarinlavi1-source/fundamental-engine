# Valuation research review (20.10.2026)

Why: the owner challenged the engine ("the price tells you something; how do I know you are not too conservative?") and asked
for the method to rest on published research, not on rules invented here. On 20.10.2026 the engine valued 87% of the 481
largest US operating companies below their price. This file records what was read, what the research says, how the engine
compared, what changed and what did not, and the measured effect. Every number below is from a source read in this session
(depth noted), from the engine's own runs, or marked as a judgement.

## What was read

| Source | Depth | Used for |
|---|---|---|
| Mauboussin, Callahan & Majd, *The Base Rate Book* (Credit Suisse, 26.9.2016) | full text, tables transcribed | sales-growth base rates by size; margin, gross-profitability and CFROI persistence by sector |
| Damodaran, *Terminal value* slides and *Terminal value myths* (NYU Stern) | full text | growth cap, reinvestment = g / ROC, excess-return persistence, rate/growth consistency |
| Damodaran Online home page, 1.10.2026 | page read | implied ERP 4.20% (trailing 12 months, adjusted payout) with a 5.29% treasury |
| Damodaran, cost of capital by industry, January 2026 (`wacc.html`) | table read | industry costs of equity (Retail General 7.54%, Software 9.64%, Semiconductor 10.72%, Utility General 5.02%, total market 8.02% / 8.37% without financials) |
| FMP treasury rates, 25.9-19.10.2026 | data | US 10-year 5.15% on 19.10.2026 |
| macroradar.io daily snapshot of FRED T10YIE, 8.10.2026 | secondary | 10-year breakeven inflation 2.35%, 10-year real yield 2.92% |
| Chan, Karceski & Lakonishok, *The Level and Persistence of Growth Rates* (JF 2003) | abstract and summaries | growth persistence is close to chance; analysts' long-term growth forecasts are too optimistic |
| Jiang & Koller, *A long-term look at ROIC* (McKinsey 2006) | abstract and summaries | median US ROIC near 10% for decades; patents and brands sustain high returns |
| Mauboussin, *Death, Taxes, and Reversion to the Mean* (2007) | secondary | 41% of top-quintile ROIC companies still top quintile nine years later |
| Liu, Nissim & Thomas, *Equity Valuation Using Multiples* (JAR 2002) | abstract | forward-earnings multiples best; about half of pricing errors within 15% |
| Kaplan & Ruback, *The Valuation of Cash Flow Forecasts* (JF 1995) | abstract | DCF within about 10% of transaction values on average (half within 15%) |
| Frankel & Lee, *Accounting valuation, market expectation and cross-sectional stock returns* (JAE 1998) | abstract | value estimates track prices closely; the value/price ratio predicts returns for about three years |
| Bernstein & Arnott, *Earnings Growth: The Two Percent Dilution* (FAJ 2003) | abstract | incumbents' per-share growth trails the economy by about two points |
| Asness, Frazzini & Pedersen, *Quality Minus Junk* (2013 draft) | summary | quality is priced only modestly higher and earns high risk-adjusted returns |
| Lev & Srivastava, *Explaining the Recent Failure of Value Investing* (2021) | summary | expensed intangibles distort book value and earnings |
| Mauboussin & Rappaport, *Expectations Investing* / MICAP | summaries | read the price backwards, then judge the implied expectations against base rates |

Not reached: McKinsey's 2026 pages (HTTP 503), Mauboussin's *The Impact of Intangibles on Base Rates* (HTTP 403), FRED (connection refused).

## Findings, the engine against them, and the decision

### 1. Discount rate: dated inputs and a market-neutral risk table (changed)

- Research: use today's risk-free rate and an implied (forward-looking) ERP; the value-weighted market's expected return is
  risk-free + ERP by definition. Damodaran 1.10.2026: ERP 4.20% with a 5.29% treasury; FMP 19.10.2026: 5.15%.
- Engine before: risk-free 4.25% and ERP 4.50% (undated defaults; research runs did not pass dated quotes) plus business-risk
  add-ons that only add. On the 481-company sweep the value-weighted add-on was +0.93pp (calibration's market values; +1.2pp
  with the listing feed's): the average company was discounted about one point above the ERP's own definition.
  Amazon: 4.25 + 4.50 + 0.5 (maturing) + 0.5 (capital intensity) = 9.75%.
- Change: `forecast.MARKET_NEUTRAL_ADJUSTMENT = -1pp` as its own visible row (the measured average removed; a company with
  no add-on is now below rf + ERP, as a below-average-risk company must be), `calibration.market_implied` reports the
  value-weighted net adjustment after each sweep (`mean_risk_adjustment`, should stay near zero), and dated market inputs
  pass through `fundamental_engine deep --market FILE` (`handoff.with_market_inputs`; risk-free only for USD reporters).
  Amazon now: 5.15 + 4.20 + 1.0 - 1.0 = 9.35%. The higher dated risk-free rate offsets most of the add-on correction:
  the change is consistency, not a lower rate across the board.
- Cross-check: Damodaran's January 2026 industry costs of equity (lower treasury then) run from 5.0% (utilities) to 10.7%
  (semiconductors), total market 8.0-8.4%. The engine's table is a documented risk table, not industry betas; replacing it
  with bottom-up industry betas is a candidate, not done.

### 2. Excess returns fade slowly and part of the way (changed)

- Research: The Base Rate Book (HOLT CFROI since 1950): over ten years the spread between top and bottom quintiles shrinks
  from 18 to 9 points (half left); operating-margin spreads from 0.21 to 0.15 (71% left); four-year CFROI persistence by
  sector from 0.35 (energy) to 0.78 (staples). Regression is toward the sector mean, not toward the cost of capital.
  Damodaran: "excess returns seem to last far longer than high growth rates do". McKinsey: patents and brands sustain
  returns; Mauboussin: 41% of the top quintile is still there after nine years.
- Engine before: without an analyst moat assessment or a computed moat proxy, terminal ROIC faded to Ku + 2pp whatever the
  company earned. Amazon: 21.4% demonstrated ROIC (5-year average 21.6%) faded to 11.75%.
- Change: `forecast.EXCESS_RETURN_PERSISTENCE` keeps 50% (base) / 71% (bull) of the demonstrated excess over Ku, with
  demonstrated = min(latest, 5-year average) capped at 40%; never below the former Ku+2pp / Ku+4pp; the bear still tests a
  failing moat (Ku+1pp) and an analyst `weak` moat keeps the full fade. Regression to the sector mean (not Ku) would be
  more generous for high-return sectors; not done because the book's sector means are partly unreadable in the copy read.
  Amazon now: terminal ROIC 15.4% in the base case (11.75% before).

### 3. Growth: the fade was right; the outside view was missing (check added, rules unchanged)

- Research: growth has little persistence beyond chance (Chan, Karceski & Lakonishok; Base Rate Book: correlation of sales
  growth 0.30 / 0.17 / 0.19 over 1 / 3 / 5 years); sustained fast growth is rarer the larger the company (Base Rate Book
  exhibit 4: >$50bn sales, 10-year real CAGR median 1.1%, only 4% above 10%); analysts' long-term growth is optimistic.
- Engine: excess growth halves every 2-3 years toward terminal growth, starting from consensus. Consistent with the research;
  for mega caps it is on the generous side. Amazon's base case grows 6.6% a year after inflation for ten years, which only
  16% of companies with more than $50bn of 2015-dollar sales achieved.
- Change: `base_rates.py` transcribes exhibit 4 (one size class rebuilt from its observation counts: the printed
  percentages of $325-700m sum to 92% in every column). Every USD forecast now carries `anchors.outside_view` (5- and 10-year
  real CAGR per scenario, the share of the size class that grew at least as fast, the class median), an
  `outside_view_growth` assumption and warnings `growth_above_base_rate` (base needs growth fewer than 10% of the class
  reached) or `growth_below_base_rate` (base slower than 60% of the class). The price-implied five-year growth
  (`what_must_be_true`) carries the same reading. Inflation 2.35% (dated breakeven) and CPI 2015/2026 restate sales and
  growth into the book's 2015 real terms. Caveats in the output: the book's sales include acquisitions and survivors only,
  and 1950-2015 real GDP grew 3.2% a year.

### 4. Terminal growth (unchanged, now justified)

- Research: Damodaran caps stable growth at the risk-free rate as a proxy for nominal economic growth and insists the rate and
  the growth assumption be consistent. Today the 10-year real yield (2.92%) is well above expected real growth, so the 5.15%
  treasury overstates nominal growth (2.35% breakeven + about 2% real is about 4.3%). Bernstein & Arnott: incumbents grow
  about two points slower than the economy. Both readings land near 2.2-3.2%.
- Engine: min(risk-free, 3%). Kept. Raising it to the treasury would add perpetual growth the research does not support for
  an existing company at today's real rates.

### 5. Margins (unchanged; one generous rule flagged)

- Research: margins regress slowly (Base Rate Book: five-year correlations 0.62-0.89 by sector; "start with last year's
  margin and seek reasons to move away from it").
- Engine: an unassessed moat loses 25% of its margin excess over the type-typical level: consistent (the evidence implies
  11-38% over five years). A company below the type-typical margin closes 40-70% of the gap by stage: generous next to the
  base rates for established companies (11-38% in five years). Flagged as a candidate change, not made: it would lower
  values and needs a backtest, and the high catch-up of young companies reflects operating leverage the base rates (top
  1,000 companies) do not measure.

### 6. How accurate can a valuation be, and what the price says (comparison basis unchanged)

- Kaplan & Ruback: careful DCFs land within about 10% of transaction values on average, half within 15%. Liu, Nissim &
  Thomas: the best multiples (forward earnings) put about half of companies within 15% of price. A value 10-15% from the price
  is inside the documented error of good valuations, not a finding.
- Frankel & Lee: intrinsic values track prices closely, and the value/price ratio still predicts returns for about three
  years. The price is informative but not the truth.
- Damodaran: with an implied ERP a valuation is market-neutral; the stock is then judged relative to the market's own pricing.
  The engine's `market_consistent` reading (calibration's rate shift) does exactly this with its own cash flows; kept as the
  comparison basis for "beats the market". A forward-multiple cross-check (Liu-Nissim-Thomas) is a candidate.

### 7. Not changed, recorded as gaps

- Intangibles (Lev & Srivastava; Damodaran capitalises R&D): ROIC and sales-to-capital are measured on reported capital, which
  overstates ROIC for R&D-heavy companies (capped at 40%) and understates growth investment.
- Quality is priced only modestly (Asness, Frazzini & Pedersen): the engine measures quality but does not use it as a return
  signal; a pre-registered quality-at-a-reasonable-price test stays open (LIMITATIONS 1).
- Sector-mean regression of margins and returns; bottom-up industry betas.

## Measured effect

Filled from the rerun of the 481-company sweep with the changed engine and the dated market inputs
(`runs/universe_2026-10-20`, before: `summaries_before_research_review.json`).

Pending: the sweep rerun was in progress at the first commit of this file.
