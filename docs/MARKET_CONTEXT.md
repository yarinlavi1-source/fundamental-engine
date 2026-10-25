# Market context: country risk, geopolitical exposure, market regime (25.10.2026)

`fundamental_engine/market_context.py`, switched on by `inputs.market_context` (pipeline stage `market_context`). Three
parts with three different rules. Without the key nothing runs and every existing output is byte-identical (checked on the
ADBE fixture and on TSEM, PPG and UBER from `runs/universe_2026-10-21`: casefile hash, report, table and paragraph equal to
the code before the change). Claude's research protocol: `brain/protocols/market_context.md` (packet `market_context`).

```json
"market_context": {
  "geography":  {"shares": {"United States": 0.6, "Taiwan": 0.4}, "evidence_ids": ["..."], "note": "what and where from"},
  "operations": {"shares": {"TW": 0.8, "US": 0.2}, "evidence_ids": ["..."], "note": "where it produces"},
  "exposures":  [{"kind": "china_export_controls", "share_of_revenue": 0.25, "evidence_ids": ["..."], "note": "..."},
                 {"kind": "tariffs", "share_of_supply": 0.4, "cost_rate": 0.25, "evidence_ids": ["..."], "note": "..."}],
  "regime":     "<market_context.read_regime(folder, as_of)>",
  "country_risk": true
}
```

Every key is optional; `{}` switches the stage on with the company's own disclosures only. Evidence ids must be FactBook
source or qualitative ids. Unknown keys, a share outside 0-1, shares adding to more than 100%, a missing note or missing
evidence are errors: the stage is recorded as failed and nothing is applied.

## 1. Country risk: two cost-of-equity rows (Damodaran, net of the market's own exposure)

**Table.** `brain/country_risk.json` (packaged with `brain/*.json`): Damodaran, *Country Default Spreads and Risk
Premiums* (https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html, retrieved 2026-10-25; 178
countries from the page, equity CRP = rating-based default spread x relative equity market volatility 1.52; 17
GDP-weighted regional and grouping averages from the xlsx). **Dated 2026-02-16**, not the page's "Last updated January 5,
2026": the contents carry the February 16 sovereign-ratings update (xlsx: "Updated Feb 16 for sovereign ratings"; the Turkey
row: "updated February 2026"), so the point-in-time guard reads the latest dated update inside the table (`as_of`, with
`page_last_updated` and `as_of_basis` kept beside it; `scripts/build_country_risk.py` derives it). US Aa1: CRP 0.23%, US ERP 4.46% = mature 4.23% + 0.23%.
Taiwan Aa3: CRP 0.78%. `scripts/build_country_risk.py` rebuilds the file from a saved page + xlsx
(`scripts/collect_market_context.py --country-risk`); a new table moves every country row, so review, test and log it.

**Exposure.** Revenue by geography from the company's annual report (`business_map.tables.geography`, latest year), or an
evidenced `geography` override. A second channel is where it produces: long-lived assets by geography
(`business_map.assets_by_geography`) or an evidenced `operations` override (a foundry's fabs, a single plant).

**Formula.**

    add-on(channel) = sum_i share_i x max(0, CRP_i - CRP_US)
    add-on          = max(add-on(revenue), add-on(operations))

The engine's ERP is the US implied premium, which already carries the US CRP, so only the part above the US is added
(Damodaran's operations-weighted CRP on a US base). A country below the US (Germany, Canada, Australia: 0%) earns no
credit: the row only adds (conservative, at most 0.23% per unit of exposure). The larger channel, not the sum: one
sovereign risk reaches the shares through either channel and adding both would count it twice.

Worked example (the test): 60% United States, 40% Taiwan -> 0.6 x 0 + 0.4 x (0.78% - 0.23%) = **0.22%**.

**The market's own exposure is netted out (it is already in the ERP and in the index return).** The ERP is Damodaran's
S&P 500 implied premium and the decision's index return is the cap-weighted implied return of the largest US companies:
both are prices of companies that already sell abroad (about 40% of S&P 500 revenue), so both already carry the market's
average country exposure. `forecast.MARKET_NEUTRAL_ADJUSTMENT` (-1pp) was measured WITHOUT a country row and states the
invariant that the value-weighted average company is discounted at risk-free + ERP; adding the full add-on on top would
break it by the market's average add-on (with terminal growth = risk-free and a mature spread of about 2.6pp, a systematic
0.2-0.4pp over-add is roughly 7-15% of a typical mature name's value). So a second row nets it out:

    country_risk_market_neutral = - COUNTRY_RISK_MARKET_RATE x mapped share
    net = add-on - COUNTRY_RISK_MARKET_RATE x mapped share

`COUNTRY_RISK_MARKET_RATE` = sum(w x add-on) / sum(w x mapped share) over the sweep companies with a country row, w = market
value: measured by `scripts/measure_country_risk_market.py` on `runs/universe_2026-10-21` (queue large500, this table; output
kept in Git: `runs/macro/country_risk_market_2026-10-21.json`):

| measure | value |
|---|---|
| companies with a country row | 125 of 490 (22 complete, 103 partial; 365 without a readable split: 307 have no saved annual report) |
| share of the sweep's market value covered | 26% (listing values) |
| rate, listing-feed market values | **0.42pp** per unit of mapped exposure |
| rate, universe rows' values (calibration's weights) | 0.49pp (they count Alphabet at 2.3x its listing value) |
| rate without Alphabet (half of the weighted add-on) | 0.31pp |
| rate over the 22 complete splits only | 0.68pp |
| value-weighted add-on / mapped share | 0.33pp / 79%; equal-weighted add-on 0.31pp, median 0.21pp |

Used: **0.40pp** (the listing-weighted 0.42pp rounded, like the -1pp row). Why "x mapped share": an unmapped or unitemized
share counts as average exposure, neither added nor netted, so a partial split is not turned into a credit; a company with
no readable split gets no row at all (average by default, as before). On the measuring sweep the value-weighted net is zero
by construction. A domestic company now ends 0.40pp below the average (as `MARKET_NEUTRAL` puts a company with no add-on
below it), a more exposed one above it: 60/40 US/Taiwan nets -0.18pp. The rate is a sample estimate (a quarter of the
value, dominated by one name); re-measure it when the table or the saved annual reports change. Because the rate is
measured with the same rule as each company's add-on (regional averages and the largest-add-on split included), the
rule's average lean cancels in the net; a company-specific lean does not (below).

**Where it goes.** `inputs.forecast.country_risk` {value, mapped_share, as_of, channel, title, url, detail, evidence_ids}
(an explicit one wins; `mapped_share` is required, 0-1) -> two rows in `forecast._cost_of_equity` (and in the bank /
insurer residual-income table): `country_risk` (the add-on) and `country_risk_market_neutral` (the netting), sourced
`input:country_risk` + the exposure evidence in the cost-of-equity ledger. Both are structural, so the terminal rate keeps
them (not in `TERMINAL_KE_DROPPED`). The decision adds the same net to the required return (`premium_components.country_risk`,
outside the permanent-loss premium cap; the index return already carries the market's average), and the marginal band's
"index low/high + premium" rows carry it too, so the band keeps its shape around the hurdle (review 25.10.2026: on PPG the
hurdle moved while those rows did not, the high one ending below the hurdle). `pipeline.summary` gets `country_risk`
{gross, net} and `calibration.market_implied` reports their value-weighted means (`country_risk.mean_net` should stay near
zero) when a sweep has the rows. When the card's `geopolitics` dimension is assessed, its register entry can read the row
as its pricing (`risk.DIM_RATE_WORDS` includes 'country'), net of the netting row (`risk.component_value`; a company at or
below the market's average exposure prices nothing through it). The Hebrew table gets one explanation line, e.g. (PPG, revenue):
"סיכון מדינה: כ־14% מההכנסות באמריקה הלטינית והאיים הקריביים (פרמיה 4.40%), כ־34% מההכנסות באירופה, המזרח התיכון ואפריקה
(פרמיה 1.68%), כ־19% מההכנסות באסיה (פרמיה 1.49%), לעומת 0.23% בארה״ב: תוספת של כ־1.30% לפי החשיפה; פרמיית השוק כבר כוללת
את החשיפה הממוצעת של החברות הגדולות בארה״ב לחו״ל (כ־0.40% לפי החלק המפורט), ולכן נטו נוספו כ־0.90% לשיעור ההיוון". Every
table country and region has a Hebrew name (a name without one is written after a maqaf, never glued to Latin letters).

**Filings are messy.** Handled deterministically, each with an open question rather than a guess:
- Aggregates that name no country (`Rest of world`, `Other countries`, `Non US`, `International`, `Americas`) carry no
  add-on; the add-on is then labelled a lower bound (`status: partial`, `uncovered_share`). Supply an evidenced split.
- Members not itemized (the table covers only part of revenue): the same, a lower bound.
- XBRL country codes (`TW`), Damodaran region names and their usual filing variants (`EMEA`, `Asia Pacific`, `Latin
  America`), `Country of domicile` (the company's country), and a split by US state (two-letter codes that are only states,
  so `CA` = California) are mapped; `Greater China` is read as China (noted).
- Two complete splits tagged on one axis (regions and countries; members add to ~200%): the engine takes the subsets of
  members that add to the total within 1.5% with no region next to what it covers, and uses the one with the largest
  add-on. None -> no add-on, open question. This is NOT a conservative lower bound: an aggregate pulls a split down, but a
  region read at its regional average pulls it up, so the largest can overstate (next bullet).
- Regional averages lean high for a typical US company's mix: 'EMEA' maps to Damodaran's GDP-weighted Europe + Middle
  East + Africa grouping (1.68%, against 1.04% for Western Europe alone: Africa 7.72% and the Middle East 2.01% are in it),
  'Latin America' to the Latin America & Caribbean grouping (4.40%, against Mexico 2.46% and Brazil 3.24%: Argentina 9.71%
  and Venezuela 26.66% are in it). PPG's 1.30% comes mostly from these averages. The rows carry a note saying so; the
  netting removes the method's average lean, not a company's own: when regions dominate the split, supply an evidenced
  country split (`geography`).
- An asset table with an aggregate or a region next to its own country is not used (its normalized shares cannot reveal
  overlap).
- A table dated after `as_of` is not used (point in time).

Survey of the 183 annual reports with a geography table in `runs/universe_2026-10-21` (no overrides): 21 applied, 105
partial (an aggregate or unitemized share), 57 no readable exposure; add-ons from 0 to 1.30% (PPG, revenue: Latin America,
EMEA, Asia), TSEM 0.81% via its fabs (operations channel). Effect on value on 2026-10-21 data, with the netting (before it,
in brackets, the gross row): PPG add-on 1.30%, net +0.90%: base 180.9 -> 147.5 (136.6), hurdle 10.25% -> 11.15%; TSEM 0.81%,
net +0.41%: 26.4 -> 25.7 (25.0); UBER 0.87%, net +0.47%: 6.29 -> 5.75; GOOGL 0.64% on 94% mapped, net +0.26%: 342.4 -> 320.8.

## 2. Geopolitical and regulatory exposures: named failure scenarios

`exposures: [{kind, share_of_revenue | share_of_supply, evidence_ids, note, shock?, cost_rate?}]`

| kind | default shock | share accepted |
|---|---|---|
| `taiwan_supply` | revenue | share_of_supply, share_of_revenue |
| `china_export_controls` | revenue | share_of_revenue |
| `tariffs` | margin (needs `cost_rate`) | share_of_revenue, share_of_supply |
| `sanctions` | revenue | share_of_revenue |
| `regulatory_ban` | revenue | share_of_revenue |
| `conflict_disruption` | revenue | share_of_revenue, share_of_supply |

The shock is sized **only** from the evidenced share (and, for a margin shock, the evidenced rate); there is no default
severity and no probability.
- **Revenue shock:** the exposed share is lost from the first full fiscal year and does not come back:
  growth_1 -> (1 + growth_1) x (1 - share) - 1; later years keep the row's growth on the smaller base. Costs flex with
  revenue (no operating deleverage is assumed, so a high-fixed-cost business loses more than this shows). A supply share is
  read as the share of output that cannot be shipped. A loss beyond the driver contract's -90% growth is clamped and said.
- **Margin shock:** exposed revenue bears the rate (cash cost ratio + share x rate), or the exposed share of cash costs rises
  by it (cash cost ratio x (1 + share x rate)), every year and in the terminal margin (floored at 1%); not passed on.

Each exposure becomes a failure scenario `exposure:<kind>` in `risk.failure_scenarios`, re-run exactly like the
permanent-loss scenarios (driver spec -> `drivers.build_case` -> `valuation.value_company`, a funding-blocked path closed
with equity at a depressed price): `value_per_share` = the bear row with the shock (the stress value), and
`base_value_with_shock` = the base row with the shock. The re-run removes the row's own funding plan (sized for its own cash
flows) and re-closes the gaps the same way, so the effect is measured against a **zero-shock control**: the same row
re-run through the same strip and re-close path without the shock (`base_value_control`), `base_effect = 1 - base with
shock / base control` (and `bear_effect` likewise). Comparing with the executed base instead counted the strip as the
event: a 0.0001% sanctions share moved LITE's funded base 2.80 -> 15.80 ("464% higher if sanctions happen") and COHR's by
+0.5% (review 25.10.2026); with the control both read about 0 (the test builds a funded base row and checks it). Each
competes with the permanent-loss scenarios for `downside.permanent_loss_value` (the worst failure scenario). One register entry `geo.exposure.<kind>` (category geopolitics; severity high
when the base effect reaches the pricing materiality bar; `pricing_status` not_priced: it is quantified, not silently in
the executed bear path or the rate) carries it into the counter-thesis, the analyst sheet's geopolitics question
(`evidenced`) and one Hebrew line under the table: "חשיפה: מגבלות יצוא לסין (כ־25% מההכנסות): אם זה יקרה, השווי בתרחיש
הסביר נמוך בכ־26% ...; זה תרחיש, לא הסתברות." The executed bear/base/bull are unchanged.

Example (the ADBE fixture with SYNTHETIC exposures, test inputs only): base 591.4, bear 275.4. 25% of revenue lost to
export controls -> base with the shock 438.9 (-25.8%), bear with the shock 202.0; 40% of supply under a 25% tariff -> base
495.8 (-16.2%), bear 215.3. Doubling the evidenced share lowers the value further; the same share gives the same value.

## 3. Market regime: context only

`python scripts/collect_market_context.py` saves FRED's `DGS10` (10-year Treasury), `BAA10Y` (Baa minus 10-year), `VIXCLS`
and `T10Y2Y` (10y-2y) verbatim with a MANIFEST (URL, retrieval time, SHA-256) under `runs/macro/fred_<date>/`
(gitignored). `market_context.read_regime(folder, as_of)` (pure, reads files) takes the latest reading on or before
`as_of` and the reading a year earlier. `classify_regime` adds the engine's dated implied ERP (`inputs.forecast.
equity_risk_premium` with an `as_of`) and the engine market return (`inputs.decision.index`, calibration) when present.

Bands (descriptive; percentiles of each full FRED daily history computed 2026-10-25, linear interpolation; ERP from
Damodaran's year-end implied ERP 1961-2025, histimpl.html, FCFE column, 65 years):

| reading | bands | basis |
|---|---|---|
| BAA10Y % | tight < 1.75 <= normal < 2.65 <= wide < 3.10 <= stress | 1986-2026: p25 1.74, median 2.13, p75 2.66, p90 3.11 |
| VIX | calm < 14 <= normal < 23 <= elevated < 30 <= high | 1990-2026: p25 14.0, median 17.6, p75 22.7, p90 28.5; 30 the usual fear line |
| T10Y2Y % | inverted < 0 <= flat < 0.25 <= normal < 1.5 <= steep | 1976-2026: p25 0.19, median 0.77, p75 1.47 |
| implied ERP | rich < 3.5% <= near history < 5.1% <= cheap | 1961-2025: p25 3.48%, median 4.10%, p75 5.08% (1999 low 2.05%, 1979 high 6.45%) |

Flags: `curve_inverted`, `credit_wide`, `credit_stress`, `elevated_volatility`, `high_volatility`, `market_rich`,
`market_cheap`. Output: one Hebrew row in the table, e.g. on 2026-10-21 data: "מצב השוק ב־21.10.2026 (רקע) | ריבית ל־10 שנים
5.13%; פערי אשראי 1.44% (צרים); תנודתיות (VIX) 16.7 (רגילה); עקום התשואות +0.32% (רגיל). רקע בלבד: לא משנה את השווי", and a
shorter clause in the paragraph, fitted last: it is kept only where it fits, so it never pushes out a sentence of the
analysis (review 25.10.2026: on PPG it had displaced the draft sentence).

**It moves nothing.** No value, rate, scenario or verdict reads the regime (tested: fc, val, rk and dc are identical with
and without it). `--macro runs/macro/fred_<date>` on `pipeline` / `deep` adds the readings and switches the stage on for
the regime alone (`country_risk: false`), so it cannot change a value through the country row either; `--country-risk`
(or the company's own `inputs.market_context`) switches the country row on. Readings dated after `as_of` are dropped and
listed. The bands are not signals: do not call a stock cheap or expensive because of them.

## Limits and open items

- Sovereign ratings measure default risk, not every channel of a company's country exposure (expropriation, currency
  controls, wars); exposures in part 2 are the place for named mechanisms.
- Regional averages are GDP-weighted; a sub-region ('Rest of Europe') keeps its region's average (noted). They can
  overstate (section 1), and the largest-add-on split choice leans the same way.
- The market-average rate is measured on a quarter of the sweep's value, half of it one company; it is an estimate, and
  it is only as right as the weights (the universe rows' values overstate Alphabet 2.3x).
- The review digest binds `market_context.geography`, `operations` and `exposures` when supplied (an exposure added after
  a review voids it; `review.MARKET_BOUND`); the regime readings are not bound (context only), the country switch moves the
  valuation hash.
- No credit below the US base and max of two channels: deliberate, conservative choices, not Damodaran's exact lambda.
- A revenue shock assumes costs flex; a supply share is read as an output share; tariffs are not passed on. These are the
  simple mechanisms the evidence can size; anything richer is an analyst forecast override with its own evidence.
- LITE on 2026-10-21 data (base 2.80 at a quote of 834.51: a near-zero equity under the survival / external-funding model)
  shows a HIGHER base value (3.07 with its net +0.75% row; 3.21 with the gross 1.15%): the existing model is not monotonic
  in the cost of equity for such near-zero equities. Not specific to the country row; an open item for the valuation core.
