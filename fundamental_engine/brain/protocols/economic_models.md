# Economic models: read each business through its own identity

Load with `evidence` and `business`. The registry `brain/economics.json` (`econ.registry()`)
names ten economic models, six overlays, the KPIs each model needs with their basis and tag,
the identities that reconcile those KPIs to reported lines, the checks, the falsifiers and a
reading plan per model. It holds no company data. A sector label from a data provider is a
hint, never the model: Uber is "Software - Application" at FMP and a marketplace in economics.

## The models and their core identities

| Model | Core identity | Also computed |
|---|---|---|
| marketplace | gross bookings = consumers x frequency x bookings per trip | take rate = revenue / bookings; segment EBITDA per bookings; incentives per bookings |
| broker | revenue = accounts x revenue per account; per account = equity per account (mix) x revenue yield on equity (cyclical) | margin interest = average loans x rate; rate sensitivity per 25bp |
| data_platform_software | revenue = customers x revenue per customer | NDR; top-N average and share; SBC / revenue |
| consumer_innovator | revenue = sum of categories | geographic split; R&D and advertising intensity; category renewal |
| memory_cycle | revenue = bits x ASP | margin-only normalized run rate; contract coverage |
| gpu_vendor | revenue = GW shipped x revenue per GW, by generation | vendor-financing ledger; segment mix |
| compute_leasing | revenue = energized GW x utilization x price per GW-year | capex per GW (refused during construction); backlog coverage |
| subscriber_network | revenue = average subscribers x ARPU | the residual named (hardware, enterprise, government); EBITDA less capex |
| digital_bank | NII = earning assets x NIM | products per member (cumulative); deposit benefit; bank capital |
| ads_platform | revenue = DAU x ARPU, by geography | logged-in share; licensing = contracts x renewal price; the share of a minor line |

Overlays: `vendor_financing` (who funds the demand: funded, committed, contingent, buy-back,
with statement checks), `funded_customer_chain` (a supplier's exposure through its funded
customer, linked by review hash), `acquisition_sbc` (tranches end; the ongoing ratio),
`segments` (no consolidated margin over segments with different margins), `contract_coverage`
(`lta_unproven` unless volume share, price mechanism, deposits and tenor are all evidenced),
`bank_capital` (excess capital at both ends of the target range; the capital lever bounded).

## Tags: what kind of growth is it

Every factor carries one tag: `structural` (built over time, not lost in a bad year: accounts,
customers, subscribers), `cyclical` (moves with the market or the price cycle: trading, client
equity, ASP), `macro` (rates, FX, policy), `mix` (composition: smaller new accounts, generation
mix; never reverted to a median), `reclassification` (what the line measures changed; only with
evidence, then the unit basis is used). Growth splits as ln(1+g) = sum ln(1+g_i) + residual
and the residual is always shown. A fall in the cyclical part may revert; the mix part never
does; the structural part is what the price can be measured against (accounts a year, GW a
year, ROTE).

## Order of work

1. Pick the model by economics, not by the provider label. State the reason and the KPI that
   decides it; a hybrid names a primary and secondary models with the overlays. When nothing
   fits, say `unclassified`; never relabel a sector to force a model.
2. Read the model's reading plan (`econ.reading_plan(model_id, fb)`): 10-K Item 1 for why
   customers pay and why they stay (issuer documents only), the KPI document, the notes, the
   transcript. It marks which KPIs the FactBook already holds and fetches nothing.
3. Collect the KPIs from AV/FMP structured data first, then the issuer release, for the latest
   period, the prior quarter and the year-ago period. State each KPI's basis (balance, average,
   flow, cumulative, rate), its scope (segment or total, logged-in or total) and its currency
   basis (reported or constant currency). KPIs enter only with a source and `available_at`
   (`facts.add_kpi`, work package 2); a KPI dated after the case date is invisible.
4. Compute the identities. Same basis and scope on both sides or nothing is divided:
   `basis_mismatch` and `scope_mismatch` are answers, with the question that resolves them
   ("supply reported gross-bookings growth"). A residual is named, not filled; `partial` stays
   partial. One quarter's capex over that quarter's capacity additions is refused.
5. Split growth by tag, run the model's checks, and keep the falsifiers numeric so each quarter
   reports intact, weakening or broken. A falsifier the issuer stops disclosing is
   `unobservable`, not intact.
6. Carry every flag into the counter-thesis (`segment_unexplained`, `nco_basis`,
   `thesis_on_minor_line`, `funding_level`, `tranche_schedule_mismatch`, ...). Thresholds are
   uncalibrated screens frozen in docs/prereg_econ.md; a value near a threshold is shown with it.
7. Run this read before a results-day review or a valuation. It is not a valuation: it changes
   no verdict code and may lower confidence, never raise it. Moat, expansion and process
   evidence are for Claude to cite; they never set a dimension or a rating.

Tool status (v0.13, work package 1): `econ.registry()`, `econ.reading_plan()`,
`econ.required_kpis()`, `econ.falsifiers()` exist. `facts.add_kpi`, the `econ_review` tool,
the quarter-review integration and the forecast overrides arrive with later work packages;
until then compute the identities by hand from the registry and show the arithmetic as
calculations in the dossier, each number with its source.

Hebrew note for the owner's table: מבני = נבנה לאורך זמן ולא נעלם בשנה רעה; מחזורי = עולה
ויורד עם השוק והריבית.
