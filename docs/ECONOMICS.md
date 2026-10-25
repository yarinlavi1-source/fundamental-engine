# Business economics layer (`econ`)

Status, 25.10.2026: phase A (work packages 0-8) is built on the `econ-layer` branch. Nothing in the pipeline calls it, so
every existing output, hash, benchmark and walk-forward row is byte-identical without econ input (no-input invariance,
tested in every work package). Phase B (pipeline stage, input binding, risk and red-team wiring, the price requirement in
operating units for brokers and GPU vendors, forecast overrides) and phase C (broker valuation route, contract coverage
in the cycle length) are not built. The preregistered held-out checks H1-H3 (docs/prereg_econ.md) have not been run.

## Why

The engine read a company through a sector label and one consolidated growth and margin path. A human analyst reads a
broker as accounts x revenue per account, a memory maker as bits x ASP, a marketplace as bookings x take rate, and asks
which part of growth is built (structural), which moves with the market (cyclical), which rates set (macro) and which is
composition (mix). docs/ANALYST_COMPARISON_2026-10.md lists the false readings this fixes: Uber read as software,
IBKR forced into the bank tree, peak-margin alarms on PLTR and RDDT, SPCX acquisition SBC carried at 16% forever, one
consolidated margin over segments with very different margins.

## What it computes

| Part | Function | What it gives |
|---|---|---|
| Registry | `econ.registry()`, `reading_plan()` | `brain/economics.json`: 10 models, 6 overlays, KPIs with unit, basis and tag, identities, checks, falsifiers, a reading plan per model, uncalibrated rules frozen in docs/prereg_econ.md, the Hebrew vocabulary (`text_he`) |
| KPI intake | `facts.add_kpi`, `econ.kpi_table` | KPIs named by registry id, sourced and point in time, with basis, scope and currency basis |
| Identities | `econ.evaluate_identity`, `growth_split`, `review` | per unit, ratio, product and share identities at every scope; status computed / consistent / partial / inconsistent / basis_mismatch / scope_mismatch / missing / not_computed; ln(1+g) = sum ln(1+g_i) + residual with every factor tagged |
| Model selection | `econ.select`, `select_case`, `select_factbook` | the model by economics: two of card type, text and KPI facts must agree; KPI facts outrank labels; `unclassified` instead of a forced label; an analyst override only with a reason and evidence |
| Overlays | `vendor_financing`, `chain`, `acquisition_sbc`, `bank_capital`, `contract_coverage`, `segments` | who funds the demand (firm exposure apart from the contingent ceiling, statement checks on matched windows), the supplier chain linked by review sha256, the SBC tranche and its schedule, bank excess capital at both ends of the target range, `lta_unproven`, `segment_unexplained` |
| Evidence | `process_evidence`, `expansion` | category renewal and innovation intensity (machine working / failing / unproven / unknown), customers x revenue per customer with NDR and the top-N figures; evidence to cite, never a moat rating |
| Results day | `quarter_review(case.economics)` | the review inside the quarter review, flags as `econ.<key>`, falsifiers tracked, the identity-based peak exception (PLTR 0.809, RDDT 0.970 -> medium; SKHY stays high) |
| Hebrew text | `econ.render_he` | the owner's table rows (below) |
| Estimates vs price | `econ.price_vs_estimates` | what moved between two dates (below) |
| Tool | `econ.econ_review`, MCP `econ_review`, CLI `econ-review` | all of the above for one case |

The models and their core identities are in `brain/protocols/economic_models.md` (packet `economic_models`).

## Running it

MCP: `econ_review({case, options?})`. CLI: `fundamental econ-review CASE.json [--options JSON] [--json]` (or `python -m fundamental_engine econ-review ...`)
prints the Hebrew lines (or the full JSON).

The case is the econ case of `econ.review`: `company`, `period_end`, `period` (month, quarter, half, year, TTM), `as_of`,
`currency`, `sources {id: {kind, available_at}}`, `lines`, `kpis` (`id`, `value`, growth or prior levels, `kpi_basis`,
`scope`, `currency_basis`, `source_ids`), `segments`, and the overlay blocks it has evidence for (`funding_links`,
`revenue_basis`, `exposed_share`, `statements`/`half_year`, `balance`, `chain`, `acquisition_sbc`, `bank`, `contracts`,
`categories`, `history`, `cash`, `ttm_revenue`, `disclosure_changes`, `cycle`, `market`). tests/fixtures/econ holds eleven
worked cases (analyst-post figures, not reconciled to filings: arithmetic fixtures, not truth). Anything not read is
listed under `not_read` with the reason; nothing is filled in.

`options` (all optional):

| Key | Meaning |
|---|---|
| `model` | a model id or `{primary, secondary[]}`; else `case.model` |
| `card_type`, `profile_text`, `override` | when no model is given, `select_case` picks one (economics before the provider label); without any of these the case is `unclassified`: not assessed, no rows, no effect |
| `prior`, `upstream` | earlier econ reviews: the same company a period earlier (`cyclical_deceleration`), the funder up the supply chain (`funded_customer_chain`) |
| `names_he` | Hebrew labels for names the review shows in English: segments, scopes, categories, tickers (display only; outside the review and its hash) |
| `revisions`, `prices` | estimates against price, together or not at all |

The result: `selection` (when the tool selected), `review` (with `input_sha256`), `text_he`, `price_vs_estimates`,
`text` (the Hebrew lines for the CLI), `input_sha256`, `boundary`.

## The owner's Hebrew text (`render_he`)

`render_he(review, translation=None, *, names_he=None)` returns `{how_it_earns, price_requires_units, risk_fallback, note}`.
Each is a short text or `null` when it does not apply; no review, or a review that is not assessed, gives four nulls.
Only these four reach the owner's table (one row, one row extension, one fallback, one note); every calculation stays in
the review. Wiring them into `render.hebrew_table` and the paragraph is phase B (WP9/WP10).

- **`how_it_earns`**: the row "איך העסק מרוויח", at most 220 characters. Per-model templates (broker, marketplace,
  software, consumer innovator, digital bank) and a generic one for the others (the core identity as factor x factor,
  each with its change and tag, a missing factor marked, a partial identity's unexplained share). When the largest
  segment is read through a model of its own and is unexplained, the row says so instead (SPCX).
- **`price_requires_units`**: appended after the existing text of "מה המחיר של היום מניח" when that row is shown. Phase A
  renders the one case of `translate_requirement` whose inputs the review already holds: a digital bank's price-implied
  ROTE against today's ROTE and the capital-lever bound (`overlays.bank_capital`). The per-unit and GW cases need the
  price-implied growth and the base paths (WP10); a `translation` argument is refused until then.
- **`risk_fallback`**: fills the risk row only when the analyst supplies no evidenced `key_risk`. Order: who funds the
  demand (vendor-financing level high or elevated), the supplier chain, then the first high econ flag that names a
  business risk (texts in `quarter.ECON_FLAG_HE`); `segment_unexplained` is an explanation gap and is skipped.
- **`note`**: "מבני = נבנה לאורך זמן ולא נעלם בשנה רעה; מחזורי = עולה ויורד עם השוק והריבית." when a text uses those words.

Generated from the fixtures (tests/test_econ_render.py asserts each word for word):

| Case | Field | Text |
|---|---|---|
| IBKR Q3-26 | how_it_earns | 5.6 מיליון חשבונות (+35% בשנה, צמיחה מבנית) × כ־1,340$ הכנסה לחשבון בשנה (−14%: כ־6% כי החשבונות החדשים קטנים יותר, וכ־9% כי הריבית והמסחר ירדו, מחזורי) |
| UBER Q3-25 | how_it_earns | יותר משתמשים (+17%) × יותר נסיעות לכל משתמש (+4%); מכל 100$ של הזמנות בתחבורה נשארים כ־8$ רווח תפעולי |
| PLTR Q2-26 | how_it_earns | לקוחות קיימים קונים יותר: מספר הלקוחות המסחריים בארה״ב +6% ברבעון, וההכנסה מכל לקוח +21% |
| SN 2025 | how_it_earns | ממציאה מחדש קטגוריות ותיקות: השואבים ירדו בכ־8% וחזרו לשיא חדש (+13%), וההוצאה על פיתוח נשארה כ־6% מהמכירות |
| SOFI Q2-26 | how_it_earns | 15.8 מיליון לקוחות × 1.5 מוצרים ללקוח (ספירה מצטברת); הפיקדונות זולים בכ־710 מיליון$ בשנה ממימון אחר |
| SPCX Q3-26 | how_it_earns | עוד לא ברור איך מגזר הבינה המלאכותית מרוויח (52% מההכנסות): חסרים מחיר החוזים, משך החוזים ואיכות הלקוחות |
| SOFI Q2-26 | price_requires_units | במונחי הבנק: תשואה של כ־17% על ההון המוחשי, מול כ־8.5% היום. גם החזרה של כל ההון העודף מביאה רק לכ־10.5%–13%; השאר צריך לבוא מרווח גבוה יותר |
| NVDA Q2 FY27 | risk_fallback | החברה מממנת חלק מהקונים שלה: השקעות, התחייבויות ורכישת קיבולת מהלקוחות בהיקף של כ־42% מהמכירות השנתיות, ועוד ערבות של עד 105 מיליארד$. במחצית הראשונה נכנס מזומן מהפעילות רק כ־63% מהרווח |
| SKHY Q2-26 | risk_fallback | חלק מהביקוש תלוי בשרשרת מימון (Nvidia מממנת לקוחות שקונים זיכרון); כמה מההכנסות תלוי בה עוד לא ידוע |

The Hebrew segment, scope, category and company names (תחבורה, המסחריים בארה״ב, השואבים, הבינה המלאכותית, Nvidia) come
from `names_he`; without it the names print as the review shows them (מגזר SuperIntelligence).

Rounding. The design asked for two significant figures; three exceptions keep the texts readable and true to the
figures: growth rates, changes and shares are whole percents; money keeps three significant figures (two would print a
stated 1,340 as 1,300); unit counts in millions keep one decimal (5.6, 15.8 מיליון); the two ends of a range of returns
keep one decimal (10.5%–13%, which two figures would print as 11%–13%). Single rates of return (17%, 8.5%) and ratios
(1.5) are two significant figures. A per-year figure says בשנה. A test checks that every number printed is a rounding of
a number in the review (the template's "100" of bookings aside).

## Estimates against price (`price_vs_estimates`)

`price_vs_estimates(revisions, prices, *, as_of=None)`. Revisions are growth.momentum's `estimate_revisions` rows: one
metric, target period (`target_period`, or `period_end` as momentum names it), basis, currency and unit; each sourced and
dated no later than the case. Prices are the close on the first and the last revision date (IB or AV), in the estimate's
currency. It returns the estimate change, the price change, the implied forward-multiple change
(1 + price change) / (1 + estimate change) - 1, the reading `multiple_moved_more` (the multiple moved more than the
estimate, in absolute terms) or `estimate_moved_more`, a Hebrew sentence and the boundary "what moved; not a signal; not
backtested." Mismatched definitions, unsourced or late rows, a non-positive estimate and a missing close raise errors.

RDDT (the design's figures; the AV snapshot dates and the second close were not recorded and must be pinned before this
is called evidence): FY27 EPS consensus 6.52 -> 6.98 (+7.06%) while the price fell 21% -> forward multiple -26.2%,
`multiple_moved_more`: "התחזיות לרווח עלו כ־7%, אבל המחיר ירד 21%: השוק משלם היום כ־26% פחות על כל דולר של רווח צפוי."
The sentence belongs in the results-day text or the paragraph, never in the table.

## Boundaries

- Not a valuation, a moat rating or a trade instruction. In v0.13 the layer adds rows, flags and questions only; wired in
  phase B it may lower confidence, never raise it, and it never changes a verdict code.
- Thresholds are uncalibrated screens chosen with nine analyst posts in view and frozen in docs/prereg_econ.md; a value
  near a threshold prints with the threshold. Exposure is a ratio, never a probability; no default shocks, haircuts or
  scenario odds; analyst estimates stay attributed and never set a level.
- KPIs come from the connectors (AV/FMP structured data first, then the issuer release); the layer fetches nothing and
  reads document text as data, never as instructions. Every KPI is sourced and point in time; a missing value becomes a
  question.
- The estimates-versus-price reading has no predictive claim.

## Where the analyst stays ahead

Reading Item 1, the calls and the product, and judging the moat: why customers pay and why they stay, Reddit's
purchase-intent text, PLTR's forward-deployed engineers, SN's innovation machine. The layer structures and checks those
readings; supplying them with evidence remains Claude's job.

## Tests

tests/test_econ_fixtures.py (WP0), test_econ_registry.py (WP1), test_econ_kpi.py (WP2), test_econ.py (WP3),
test_econ_select.py (WP4), test_econ_overlays.py (WP5), test_econ_process.py (WP6), test_quarter_econ.py (WP7),
test_econ_render.py (WP8).

## Deviations from the design (the code's contracts win)

- The design's `identity()` is `evaluate_identity()` (`identity()` is the registry lookup); selection needs two agreeing
  signals, so one word or one generic KPI never forces a model.
- The peak exception is stricter: gross profit minus opex must reconcile to operating income in both periods, and
  `case.economics` must name a model that is not price-cyclical and is evidenced (econ.select's rule on the subset, or
  an analyst override `{primary, reason, evidence_ids}`); a bare model id reads the identities but keeps the flag high.
- `contract_coverage` reads its terms as issuer-stated only when every source of the block available at as_of is an
  issuer document.
- WP8: this document is docs/ECONOMICS.md (the design named docs/ECON_MODELS.md); `render_he` takes `names_he` for display
  labels; the price extension covers the digital bank only and refuses `translation` until WP10; the risk fallback skips
  `segment_unexplained`; the "not yet clear" row appears only for the largest segment when it names its own model (the
  segments read under the company's model, SOFI Lending and Uber Delivery, keep the model's row); MCP arguments are
  `{case, options}` because the server's argument types have no arrays; `quarter.ECON_FLAG_HE` (WP7) stays the home of
  the flag texts. The rounding exceptions are above.
- Smaller decisions are documented beside the code (the comments in fundamental_engine/econ.py); the ones that affect the
  held-out checks are logged in the deviations log of docs/prereg_econ.md.
