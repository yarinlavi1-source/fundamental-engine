# Engine conservatism: diagnosis and the persistence fix (2026-10-21)

Yarin's observation: "הערכת שווי נראת לי מוגזמת ביחס למחירים בשוק כרגע" — the engine's values look
too low against current market prices. This is a record of whether that is true, whether the
conservatism was *earned* (did the cheap-looking calls actually do better and the dear-looking calls
worse?), and the one lever changed in response.

Everything here comes from the saved 10-year walk-forward in `runs/backtest/engine_v3/` (491 of today's
large caps, one formation each July 2016–2025, the full pipeline run on point-in-time SEC facts with the
price as traded and the month's 10-year Treasury). Survivorship and in-sample-design caveats apply exactly
as in `scripts/backtest_engine.py` — the universe is today's survivors, so every stock book is flattered;
the honest out-of-sample test is still the forward ledger, not this.

## 1. The drift is real, and it widened

Engine base value ÷ traded price, by formation year (median across the names valued that year):

| formation | names | median value / price | share valued below price |
|-----------|------:|---------------------:|-------------------------:|
| 2016 | 264 | 0.81 | 68% |
| 2017 | 272 | 0.72 | 75% |
| 2018 | 281 | 0.66 | 77% |
| 2019 | 339 | 0.67 | 73% |
| 2020 | 343 | 0.83 | 60% |
| 2021 | 336 | 0.60 | 76% |
| 2022 | 347 | 0.69 | 71% |
| 2023 | 357 | 0.55 | 79% |
| 2024 | 344 | 0.50 | 87% |
| 2025 | 350 | 0.49 | 88% |

By 2024–2025 the engine valued the *median* large cap at about half its price and marked ~88% of them
below price. That is the "מוגזם" Yarin saw. The drift tracks the market's multiple expansion: the engine
anchors to normalized fundamentals and did not follow prices up.

## 2. The conservatism was not earned

If the discipline were right, the names the engine marked far below price should have gone on to
underperform. They did not. Forward return in excess of the *same formation year's* universe average,
bucketed by value/price:

| value / price | 1-year excess | 3-year excess | n (1y / 3y) |
|---------------|--------------:|--------------:|-------------|
| < 0.4  | +1.8% | **+9.0%**  | 798 / 541 |
| 0.4–0.6 | +2.5% | −0.7% | 713 / 529 |
| 0.6–0.8 | −2.7% | −8.7% | 541 / 425 |
| 0.8–1.0 | −3.2% | **−18.8%** | 396 / 344 |
| 1.0–1.3 | −4.2% | −0.7% | 332 / 295 |
| > 1.3  | +2.1% | **+14.5%** | 453 / 405 |

The relationship is a U, not a clean "cheap beats dear". The engine's genuinely cheap bucket (value/price
> 1.3) did earn +14.5% over three years — real signal at that extreme. But the *most pessimistic* bucket
(value < 0.4× price) also beat the field by +9% over three years: those deep discounts did not predict weak
returns, so the conservatism there bought nothing. The ranking has a little edge (top IRR quintile +6.8%/yr
vs the universe, t=0.89; it beats the naive earnings-yield screen, which is negative, t=−1.87), but the
*level* is systematically too low and the harshest marks were often simply wrong in the pessimistic
direction.

## 3. The conservatism concentrates in proven compounders

Median value/price by business type:

- subscription_software **0.33**, semiconductor **0.45**, healthcare_products 0.59, hardware 0.64,
  industrial 0.61, commodity 0.57, consumer_brand 0.79, distribution 1.26.
- by stage: growth **0.51**, maturing 0.61, mature 0.69.
- by quality: weak 0.51, adequate 0.63, strong 0.68.

Within software, a *relatively* higher engine value did predict better 3-year returns (Spearman +0.21) — the
engine picks the right software, it just marks all of it at a third of price. Semiconductors are the
opposite (Spearman −0.28): the ones it liked least ran hardest, the classic cyclical-trough markdown. That
split is why the fix is deliberately withheld from cyclicals.

## 4. The lever: durability-scaled terminal persistence (and nothing else)

The chosen scope (Yarin, 2026-10-21): raise the terminal excess-return persistence *only* for genuinely
high-quality, wide-moat compounders — leave cyclicals and weak businesses alone, and do **not** re-anchor
values to price.

In an automatic run (no analyst moat row — exactly the backtest's conditions) every company fell on the
unassessed-moat branch, whose terminal ROIC kept a flat 50% of the demonstrated excess over the hurdle
(71% in the bull). That flat 50% is what marked the proven compounders down. The flat share contradicts the
very evidence it cites: the Base Rate Book's own CFROI data and the McKinsey ROIC transition matrices
(Koller et al.) both show that firms whose realized ROIC is **high and stable over a long record** stay high
far longer than the average fader — a wide-moat compounder keeps much more than a company with one good
year.

So `EXCESS_RETURN_PERSISTENCE` became a floor (0.50 base / 0.71 bull), lifted continuously toward a ceiling
(0.75 base / 0.85 bull) by an evidence-only durability weight, `_roic_durability`:

- **excess weight** — how far the demonstrated ROIC (the lower of latest and the 5-year average) clears the
  hurdle: +5pp → floor, +20pp → ceiling.
- **stability weight** — the coefficient of variation of the realized ROIC series: 50% → floor, 20% →
  ceiling.
- the weakest of the two decides (as in the moat proxy); fewer than five years of ROIC, or an average ROIC
  that never cleared the hurdle, earns **nothing**.

Gates: the lift is withheld entirely from cyclicals (`cycle_path` — their return is the cycle's, already
assessed through the mid-cycle margin) and from a weak moat; the **bear is never lifted** (it still tests
the moat failing at Ku+1pp); no analyst input is required, so an automatic run earns the lift from the
realized record alone. A 1pp move in any input never flips the value. An analyst `strong` moat already keeps
up to 100% and is unchanged; this only touches the unassessed-moat / computed-proxy path.

The engine's confidence level is untouched — the fix changes the modelled value, not how sure the engine is
of it.

## 5. Measured effect — and why the lever is a footnote, not the fix

A clean A/B isolated the persistence lever alone (same engine, same 2024 formation, lift ceiling on vs set
equal to the floor) on 30 names spanning compounders, cyclicals and weaker businesses. **The lever moves
value by under 1%** even for the exact compounders it targets: MSCI +0.6%, IDXX +0.5%, MA +0.4%, MSFT +0.2%,
and ADBE / INTU / ISRG / NOW / V +0.0%. The reason is arithmetic: for a high-ROIC franchise the terminal ROIC
is already near its cap, and keeping 75% rather than 50% of the demonstrated excess changes terminal
reinvestment (g / ROIC) only at the second decimal. The large "lift" seen when comparing the current engine
to the saved v3 rows (~+20% median) came almost entirely from engine changes that pre-date those rows (the
market-neutral cost-of-equity adjustment and the persistence floor itself), **not** from this change.

So the persistence refinement is kept because it is correct in principle and now gated, tested and
transparent — but it is **not** the answer to the "exaggerated" feeling, and is not presented as one.

A sensitivity on the software/compounder cohort shows no single honest lever closes the gap either (median
value/price at the 2024 formation):

| | median value / price |
|---|---|
| baseline | 0.29 |
| growth fade 1.5yr slower | 0.35 |
| discount rate −1pp | 0.36 |

Getting these names from ~0.3x to ~1.0x of price would mean stacking assumptions to underwrite 2024's peak
multiples — i.e. chasing the market, which destroys the point of an independent valuation. The honest
reconciliation: the engine measures value; the market also pays for growth optionality and momentum a
disciplined DCF will not underwrite; across a long bull run (and in survivor-biased data) paying up worked,
so "cheap by the engine" is not a reliable "it will fall" signal. The engine's edge is in ranking and in
refusing to overpay at tops, not in the absolute level.

## 6. The actual change shipped: reframe, do not inflate (owner's choice, 2026-10-21)

Rather than inflate values, the engine now makes the value-vs-price comparison legible. Every plain-language
table (`render.hebrew_table`) gains a row read from a reverse-DCF the engine already supports
(`drivers.implied_growth_shift`, surfaced through `decision.price_implied_growth`): the uniform growth the
current price assumes, against the base case and against the size-class base rates. For ServiceNow at 2026-10-20
(fair value $80 vs price $149) it reads:

> מה המחיר של היום מניח: צמיחה של כ-14% בשנה (מעבר לאינפלציה) ל-10 שנים (כ-8 נק׳ אחוז יותר בשנה מהתרחיש הסביר);
> רק כ-4% מהחברות בגודל הזה הצליחו בכך בעבר.

The deep discount is no longer mysterious: the price is betting on ~14% real growth for a decade, eight points
a year above the realistic case, which only ~4% of comparable companies ever delivered. The owner can now
judge the bet instead of distrusting the number. It is a sensitivity of this spec, not a market forecast, it
is computed once per table (never inside the pipeline or the backtest — it costs many DCF evaluations), and it
changes no value.
