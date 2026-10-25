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

## 1. Country risk: one cost-of-equity row (Damodaran)

**Table.** `brain/country_risk.json` (packaged with `brain/*.json`): Damodaran, *Country Default Spreads and Risk
Premiums*, page dated January 5, 2026 (https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html,
retrieved 2026-10-25; 178 countries from the page, equity CRP = rating-based default spread x relative equity market
volatility 1.52; 17 GDP-weighted regional and grouping averages from the xlsx). US Aa1: CRP 0.23%, US ERP 4.46% = mature 4.23% + 0.23%.
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

**Where it goes.** `inputs.forecast.country_risk` (an explicit one wins) -> its own row `country_risk` in
`forecast._cost_of_equity` (and in the bank / insurer residual-income table), sourced `input:country_risk` + the exposure
evidence in the cost-of-equity ledger. It is structural, so the terminal rate keeps it (not in `TERMINAL_KE_DROPPED`). The
decision adds the same row to the required return (`premium_components.country_risk`, outside the permanent-loss premium
cap): the index return it is compared with is a US market return. When the card's `geopolitics` dimension is assessed,
its register entry can read the row as its pricing (`risk.DIM_RATE_WORDS` includes 'country'). The Hebrew table gets one explanation line, e.g. (TSEM, operations channel):
"סיכון מדינה: כ־24% מהייצור והנכסים בישראל (פרמיה 2.07%), כ־32% ... באירופה (פרמיה 1.04%), כ־17% ... ביפן (פרמיה 0.91%),
לעומת 0.23% בארה״ב; נוספו כ־0.81% לשיעור ההיוון".

**Filings are messy.** Handled deterministically, each with an open question rather than a guess:
- Aggregates that name no country (`Rest of world`, `Other countries`, `Non US`, `International`, `Americas`) carry no
  add-on; the add-on is then labelled a lower bound (`status: partial`, `uncovered_share`). Supply an evidenced split.
- Members not itemized (the table covers only part of revenue): the same, a lower bound.
- XBRL country codes (`TW`), Damodaran region names and their usual filing variants (`EMEA`, `Asia Pacific`, `Latin
  America`), `Country of domicile` (the company's country), and a split by US state (two-letter codes that are only states,
  so `CA` = California) are mapped; `Greater China` is read as China (noted).
- Two complete splits tagged on one axis (regions and countries; members add to ~200%): the engine takes the subsets of
  members that add to the total within 1.5% with no region next to what it covers, and uses the one with the largest
  add-on (every split's add-on is a lower bound, the largest is the tightest). None -> no add-on, open question.
- An asset table with an aggregate or a region next to its own country is not used (its normalized shares cannot reveal
  overlap).
- A table dated after `as_of` is not used (point in time).

Survey of the 183 annual reports with a geography table in `runs/universe_2026-10-21` (no overrides): 21 applied, 105
partial (an aggregate or unitemized share), 57 no readable exposure; add-ons from 0 to 1.30% (PPG, revenue: Latin America,
EMEA, Asia), TSEM 0.81% via its fabs (operations channel). Effect on value on 2026-10-21 data: PPG base 180.9 -> 136.6
(-24%, a levered mature name whose terminal spread is small); TSEM 26.4 -> 25.0.

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
`base_value_with_shock` / `base_effect` = the base row with the shock alone (what the event costs the base case). Each
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
shorter clause in the paragraph (dropped only when the paragraph is still over its word limit after the other shortenings).

**It moves nothing.** No value, rate, scenario or verdict reads the regime (tested: fc, val, rk and dc are identical with
and without it). `--macro runs/macro/fred_<date>` on `pipeline` / `deep` adds the readings and switches the stage on for
the regime alone (`country_risk: false`), so it cannot change a value through the country row either; `--country-risk`
(or the company's own `inputs.market_context`) switches the country row on. Readings dated after `as_of` are dropped and
listed. The bands are not signals: do not call a stock cheap or expensive because of them.

## Limits and open items

- Sovereign ratings measure default risk, not every channel of a company's country exposure (expropriation, currency
  controls, wars); exposures in part 2 are the place for named mechanisms.
- Regional averages are GDP-weighted; a sub-region ('Rest of Europe') keeps its region's average (noted).
- No credit below the US base and max of two channels: deliberate, conservative choices, not Damodaran's exact lambda.
- A revenue shock assumes costs flex; a supply share is read as an output share; tariffs are not passed on. These are the
  simple mechanisms the evidence can size; anything richer is an analyst forecast override with its own evidence.
- LITE on 2026-10-21 data (base 2.80 at a quote of 834.51: a near-zero equity under the survival / external-funding model)
  shows a HIGHER base value (3.21) with its 1.15% country row: the existing model is not monotonic in the cost of equity for
  such near-zero equities. Not specific to the country row; an open item for the valuation core.
