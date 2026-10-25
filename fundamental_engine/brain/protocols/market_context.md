# Market context: country risk, geopolitical exposure and the market regime

`market_context.py` turns three kinds of outside facts into the valuation without inventing any of them. Pass them in
`inputs.json` under `market_context`; every judgement carries FactBook evidence ids (sources or qualitative items). Without
the key nothing changes. Full method and limits: `docs/MARKET_CONTEXT.md`.

## 1. Country risk (a cost-of-equity row)

- The engine reads revenue by geography from the annual report (`business_map.tables.geography`) and long-lived assets by
  geography (`business_map.assets_by_geography`). Check them: members that overlap (`US` next to `Americas`), aggregates that
  name no country (`Rest of world`, `International`, `Non US`, `Americas`) and a split by US state are all handled, but only
  you can say where an aggregate's revenue really is.
- When the filing's split is coarse or wrong for the risk, supply an evidenced override:
  `geography: {shares: {"United States": .6, "Taiwan": .4}, evidence_ids: [...], note: "..."}` (country names, ISO codes or
  Damodaran regions such as `Western Europe`, `EMEA`, `Asia`). Production concentrated in one place (a foundry's fabs, a
  single plant, a contract manufacturer) goes in `operations` the same way.
- The add-on is Damodaran's: exposure-weighted country risk premiums, only the part above the US premium (the engine's ERP is
  the US implied premium), the larger of the revenue and operations channels. The ERP and the index return already carry the
  market's average exposure, so a second row nets it out (0.40pp x the mapped share, measured on the large-cap sweep): an
  average multinational nets about zero, a domestic company ends below it. `market_context: {}` is enough to use the
  filing's own split; `country_risk: false` leaves the rows out (exposures and the regime still run). They are structural,
  so the terminal rate keeps them, and the net reaches the required return. No split -> no rows and an open question; never
  guess a split.
- Regional members read at regional averages ('EMEA' with Africa, 'Latin America' with Argentina and Venezuela) can
  overstate the add-on; when regions dominate the filing's split, supply an evidenced country split.
- The table (`brain/country_risk.json`) is dated; refresh it with `scripts/collect_market_context.py --country-risk` and
  `scripts/build_country_risk.py` after Damodaran's January/July updates, then rerun the tests.

## 2. Geopolitical and regulatory exposures (named scenarios in the counter-thesis)

Research each one like an analyst, from primary sources first:

- **Rules:** export-control rules and entity lists (BIS / Federal Register), sanctions lists (OFAC, EU, UK), tariff schedules
  and announcements (USTR, the official gazette), bans and licensing decisions by regulators. Record the rule's date and scope.
- **Company exposure:** the 10-K / 20-F risk factors, segment and geography notes, customer and supplier concentration,
  where products are made (Item 2 properties, supplier disclosures), and what management said it would do (earnings calls).
- **Size it from evidence only:** the share of revenue sold into the exposed market or product, or the share of supply /
  cash costs coming from the exposed place; for a margin shock (tariffs) the rate that applies. If the share is not
  disclosed, say so and leave the exposure out or use a disclosed upper bound with that label; never pick a severity.
- Then pass `exposures: [{kind, share_of_revenue | share_of_supply, evidence_ids, note, shock?, cost_rate?}]` with kind one
  of `taiwan_supply`, `china_export_controls`, `tariffs`, `sanctions`, `regulatory_ban`, `conflict_disruption`. Each becomes a
  failure scenario (the exposed share lost, or the cost rate absorbed, on the bear row and on the base row; the effect is
  measured against the same row re-run without the shock) and a register entry the paragraph can name as the main risk.
  Exposures are bound by the review digest: one added after a review voids it. The engine gives no probability; if you
  state odds, give their basis.
- Separate the evidence: a rule in force, a proposed rule, a politician's statement and a news story are different kinds.
  Syndicated copies of one story are one source. A conflict scenario is a stress, not a forecast.
- Mitigants belong in the note with evidence (licences granted, capacity elsewhere, inventory, customer alternatives); they
  do not shrink the shock unless the evidenced share itself is smaller.

## 3. Market regime (context only)

- `scripts/collect_market_context.py` saves FRED's 10-year Treasury, Baa credit spread, VIX and 10y-2y slope with retrieval
  time and URL; `--macro runs/macro/fred_<date>` on `deep` or `pipeline` adds the readings at as_of. With the dated implied
  ERP and the engine market return they give one Hebrew line in the table (and the paragraph when it fits). `--macro` alone
  never switches the country-risk row on; `--country-risk` (every company) or the company's own `market_context` does.
- The bands are historical percentiles, not signals. The regime never moves a value, a rate or a verdict: do not argue a
  stock is cheap or expensive because of it. Use it to frame the risk discussion (credit stress raises refinancing risk to
  check; an inverted curve is a recession-risk question to research, not a forecast).
