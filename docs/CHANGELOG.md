## Unreleased — engine-computed multiples and draft tables with numbers (26.10.2026)

Every ticker run of 26.10.2026 (NVDA, CRDO, APH) needed a repair pass, partly for the same engine-shaped reasons: the path
agent derived its multiples at another cost of equity and on another EPS path than the engine executed (a critical finding
in NVDA and CRDO), and a review-only hold (CRDO's unexplained forensic flag) left the owner's table with no number at all.

- company_path economics mode (`multiple_from: 'economics'`, the new default of analyst_path): a scenario gives its
  incremental ROIC (`roic`, a decimal, backed by the scenario's evidence_ids and source_tier) instead of a hand
  multiple_path, and the engine computes the path from ITS OWN executed numbers for that scenario: the fiscal-year EPS path,
  the explicit cost of equity, the terminal cost of equity and the terminal growth of the valuation case, with the
  analysts' formula (their path_calc): value_t = the PV of D_k = E_k (1 - g_(k+1)/ROIC) over the executed fiscal years
  still ahead (each at the midpoint of its remaining part) + E_N (1 - g_T/ROIC)/(Ke_T - g_T), multiple_t = value_t /
  EPS_(t+1); a partial fiscal year is annualized for its growth only. The scenario's `economics` record holds the formula,
  Ke, Ke_T, g_T, the ROIC, the engine's own terminal ROIC (and the difference), the engine's value today, every fiscal
  year's EPS, growth and distribution, the terminal value and value_t; the price of each row equals value_t. A year whose
  value or next-year EPS is not positive has no price (`nonpositive_value`); a ROIC at or below g_T fails the stage. Hand
  paths stay supported unchanged (and can sit next to computed ones); the justification labels and the peak check against
  own_history apply to computed multiples too. On the CRDO casefile the engine reproduces the repaired hand path within
  0.1-0.6 turns (base 31.0/26.2/23.1/21.6/20.4/19.8 vs 31.1/26.4/23.2/21.7/20.5/19.9); on ADBE it matches the analysts'
  index-based formula within 0.02%.
- evidence_pack company_path (contract 2 of the pack): per scenario either {multiple_from 'economics', roic} or a hand
  multiple_path; a pack with both, a ROIC typed as a percent, or a roic on a hand path is refused. The analyst_path stage file
  and skeleton ask for the ROIC with its evidence (no multiple arithmetic, no restated Ke or EPS); review_numbers and
  review_counter check the ROIC and its evidence, not the arithmetic at another rate; value.json carries each scenario's
  economics rates.
- Draft table with numbers: when the price claim is withheld only for review or verification reasons (an unreleased
  forensic flag, an open forecast material issue, an unreviewed draft), render.hebrew_table shows the value rows and the
  yearly bear/base/bull table under the banner 'טיוטה — לא אומתה' with the reasons, and the bottom line says there is no
  buy/sell conclusion yet; scenarios_he.md carries the same banner (scenario_reasons `draft_reason_he`). Blocked data, a
  failed, missing or unverified valuation, a blocked review, demo inputs, a rejected assumption, governance evidence and a
  'hold' material issue still show no number (render.value_display `draft`, INVALID_GATES). The paragraph is unchanged.
- Invariance: on the ADBE fixture (plain and with the hand company_path) and the synthetic demo company every casefile leaf is
  identical to 5517114 except the new `scenario_reasons.draft_reason_he` and the casefile hash; the paragraph and every
  value are unchanged, the table changes only by the draft banner and bottom line. Tests: test_company_path
  (EconomicsModeTests), test_evidence_pack, test_company_first_fixes (EconomicsPathEndToEnd), test_draft_numbers.

## Unreleased — the fast ticker procedure (25.10.2026)

The owner's limit is 10-20 minutes for one ticker, without harming the result. Measured on NVDA with the saved Workflow
`ticker` (25.10.2026): it started at 22:30 UTC and was still in its repair pass at 00:17 UTC. The Workflow runtime caps
concurrent agents at min(16, CPUs - 2) = 2 on the 4-CPU box: Identify alone took 9 minutes, the four gather lanes 7-9 minutes
each in two waves, and Understand (22 min, 83 tool calls) and Position (22 min, 80 calls) ran one after another; about 25 of
those 44 minutes went to reading engine source to learn the JSON formats.

- New default: the main loop orchestrates (CLAUDE.md "On a ticker"), each agent following its stage file. (1) In one turn
  `scripts/ticker_prefetch.py` (background, seconds) and four background gather lanes on model sonnet: gather_numbers,
  gather_documents (now also completing and running the quarter-review and econ-review cases), gather_field (the canonical
  competitor list), gather_macro; each lane validates its fragment with `ticker-check` before replying. (2) `ticker-prep`
  (deterministic: fragment checks, understand/auto.json with the fiscal years, track-record ratio, history ratios, ROIC and
  forensic flags, evidence_ids.json, position skeletons), then in parallel the three analyst parts, which write disjoint
  pack keys (`analyst` core: understand/business.json, drivers, forecast_options, events, key risk, falsifiers;
  `analyst_field`: potential and assessments; `analyst_path`: company_path), and `review_trace`, which traces the gathered
  numbers before the valuation instead of after it. (3) `ticker-value` (deterministic: the parts merged into position.json
  with position/coherence.md, pack-merge, pack-to-inputs, deep for this one company, value.json); pack problems go to one
  `repair` agent. (4) `review_numbers` and `review_counter` in parallel; a critical finding gets one `repair` pass or the
  result is a draft. (5) `ticker-status` decides verified or draft from this attempt's files and the main loop's own list of
  returned agents, and records the stage times; the main loop delivers and freezes the ledger.
- Time, honestly: a design estimate from those measured stage times, NOT measured end to end: about 25-35 minutes for a new
  ticker and 20-30 for a repeat within 30 days, plus 5-8 per repair pass. That is above the owner's 10-20 minutes; the stage
  times of each run are recorded so the next cut is chosen on measurements. The saved Workflow stays as the thorough
  fallback.
- `scripts/ticker_prefetch.py <TICKER> <as_of> [--reuse auto|none|<run dir>] [--peers A,B]`: the deterministic part, no
  LLM, seconds. SEC CIK (saved ticker map first), submissions cut at as_of, companyfacts, the latest 10-K / 10-Q / proxy in
  the raw layout the engine reads, the other 10-Qs and the exhibits 99 of the last 8 earnings 8-Ks with their plain text under
  reading/, the FRED regime series and market.json, the company and identity skeletons, the companyfacts facts of 6 fiscal
  years and 8 quarters (each citable LAG_DAYS after its filing), the computed type and forensic screens, quarter-review and
  econ-review case skeletons, the named competitors' companyfacts (--peers, or the competitors of a reused field.json) and
  gather/_prefetch.json. A rerun of the same ticker and date first moves the earlier attempt's judgement and engine output to
  attempts/<utc>/. --reuse auto (and an explicit folder, under the same 30 days and identity check) copies an earlier run's
  fragments without their market block, never anything written or retrieved after as_of, and lists the filings dated after
  it; when SEC was not refreshed that list is null with the reason, never an empty list. A 20-F / 40-F filer's 6-K releases
  are a recorded gap. Network failures and a missing SEC_USER_AGENT (an owner setting, never invented) are gaps, never fatal.
- `fundamental_engine/ticker_run.py` and the CLI commands `ticker-check`, `ticker-prep`, `ticker-value`, `ticker-trace`
  (the numbers with their sources and candidate matches in the saved text, a reviewer's aid) and `ticker-status`;
  `ticker-stage <stage> [--prompt | --skeleton]` (the prompt names the repo root and the run folder absolutely).
- Stage files `fundamental_engine/brain/ticker/` (packaged; scripts/check_wheel.py asserts they ship and fill):
  gather_numbers, gather_documents, gather_field, gather_macro, analyst, analyst_field, analyst_path, review_trace,
  review_numbers, review_counter, repair, deliver. Identical common blocks carry the ticker.js rules (connector order per
  session_capabilities, IBKR strictly READ-ONLY, untrusted documents, source registration, citability with LAG_DAYS,
  company-first, the stop rule, the one Alpha Vantage quota, the ledger as the only write outside the run folder); the exact
  fragment and position shapes are embedded; budgets are sized from the measured calls (numbers 28, documents 22, field 25,
  macro 12, each reviewer 25); master_process's guardrails are a review_counter check and a line of the analyst's.
- session_capabilities: the 2026-10-25 Alpha Vantage observations (COMPANY_OVERVIEW premium, 25 requests a day).
  master_process names which step runs in which agent or script. No valuation rule, engine number or review gate changed.

## 0.14.0 — the ticker brain: one company, from its own evidence (25.10.2026)

The owner sends a ticker; the saved Workflow `ticker` (`.claude/workflows/ticker.js`) walks the `master_process` skeleton
(Identify, Gather, Understand, Position, Value, Review, Deliver) and builds every driver from that company: guidance
adjusted by its track record, contracted demand and KPIs, attributed consensus, its own history and named direct
competitors; size-class base rates are a warning row and every lever still on an engine rule is listed as a gap. The owner
gets ONE Hebrew table with a yearly price for years 1-5 in דובי / בסיסי / שורי (next-fiscal-year EPS x an evidenced
multiple labelled justified_high / peak / unproven), and every price keeps its reason behind the scenes (scenario_reasons:
the condition, the events with evidence or marked hypotheses, the drivers and their tiers). Also in this release: the
business-economics layer (econ, phase A), the market and geopolitical context (country risk, exposures, regime) and the
two preregistered price-path lanes, both recorded as FAILED and not shown. No accuracy claim is made for the ticker brain:
its forecasts are frozen in the forecast ledger and judged later on real prices. Sections below, newest first.

### company-first review fixes: ONE yearly table, a reason behind every price, the guided year kept apart (25.10.2026)

Twenty-nine findings of the company-first review, each verified on the synthetic demo (tests/fixtures/demo_company +
examples/evidence_pack_demo.json) or the ADBE fixture before the fix. No value of a run without the new inputs changes
(ADBE fixture: today's bear/base/bull, verdict and warnings identical to 49ef7b8).

- ONE table (render.py). `owner_table` splits the owner's table from what stays behind the scenes. The table holds ONE
  yearly bear/base/bull table (`| שנה | דובי | בסיסי | שורי |`, rows 'שנה t (YYYY/MM)'): the company price path
  ('מחיר המניה בכל שנה, לפי תרחיש', years 1-5 on the anniversaries of as_of; today's EPS x multiple stays in the casefile)
  when it is computed, else the executed value per year (today, then for each year 1-5 the fiscal row nearest its
  anniversary: a stub year-end weeks after as_of is no longer shown as a year). Moved to report.md ('Owner table: behind
  the scenes'): the today range 'דובי / שורי', the five-year value row, the universe price path (PRICE_PATH_PULL, now
  labelled 'לפי ממוצע השוק (לא מנתוני החברה)'), the market-assumption and 'מה המחיר מניח' notes, and the value table
  when the price table is shown. One note says the fair value and buy-below price come from the cash-flow model and the
  yearly prices are EPS x multiple. The scenario condition lines follow whichever yearly table is shown. With an evidence
  pack and no computed company path, a row says why ('מחיר המניה לפי רווח × מכפיל | לא חושב: …') instead of a universe row.
  About eight short notes. 'רק' only before a minority share; a negative implied growth reads 'ירידה של כ־X%'. The company
  type is not 'מחזורית' after an evidenced structural treatment (or a ramp history the forecast does not normalize): the
  type then carries '(בענף מחזורי)' (route.cycle_input).
- Reasons behind every price. company_path rows store their events (analyst_sheet.scenario_events, hypotheses marked), the
  EPS drivers with tier, value and evidence (fc.driver_sources and overrides) and the levers still on a rule;
  `evidence_ids` is now every id behind the price (`multiple_evidence_ids` keeps the multiple's). scenario_reasons adds
  `price_rows` per scenario and scenarios_he.md / the CLI print each company-path price with its EPS, revenue, margin,
  shares, multiple, justification, tier, competitors and condition, the drivers and evidence once per scenario, then the
  cash-flow model values under 'שווי במודל תזרים המזומנים'; the preamble names the price table. The model's EPS is labelled
  'רווח תפעולי למניה' (it excludes investment income, unlike the price table's EPS). The paragraph's five-year clause reads
  the company path's base year-5 price and annual change when it is computed (the same number as the table).
- Evidence pack. Market feeds (kind price or macro, or retrieval market_feed) are citable on their date in the pack and
  the bundle (facts.MARKET_FEED_KINDS); documents keep the two-day lag, and an item is citable only when all its sources
  are (guidance: its issue date too). The fiscal year in progress is `drivers.current_year_revenue` (guidance tier, the FY
  guidance item and the remainder item): the track-record ratio is applied to the unreported remainder only and reaches the
  engine as `forecast.current_year_revenue`; `growth_anchor` / `growth` state `period` (the next fiscal year) and a
  guidance- or consensus-tier value must sit inside the cited item's range for that year (or state `deviation`). Tiers:
  consensus counts only for tier consensus; guidance needs a guidance item; competitor needs the named competitor's
  source; a tier per scenario is allowed. forecast_options are checked like forecast.build (horizon 1-30, financing mode,
  cyclical with ids mapped to `web:`); market rates need a value in range and an as_of not after the pack's. Refused
  instead of dropped: a non-text note, a non-object identity (fiscal_year_end now required), a non-numeric consensus
  low/high or n, a bottleneck without id; company_path is required. `merge_fragments` / CLI `pack-merge` merges the
  ticker workflow's fragments (sources deduplicated by URL, ids renamed everywhere, duplicate item ids refused). The demo
  pack: current_year_revenue from FY2026 guidance + the Q4 remainder; growth_anchor FY2027 from the consensus low/mean/high.
- Engine. `forecast.current_year_revenue` input (fiscal year in progress, checked against the calendar). An `sbc_pct`
  override with an evidenced after-SBC `mature_margin` (or terminal margin) moves cash costs by the SBC difference, so the
  evidenced margin is the executed one (it was charged twice). The scenario-order rule (bear 0.5pp below base, bull above)
  is reported as `binding_bound: scenario_order_0.5pp` with a warning when it overwrites an evidenced value. New gap levers:
  margin convergence (new econ lever `margin_convergence_years`), minimum cash months, tax rate, working capital and
  financing terms (when external money or a debt without interest history uses the rule). Analyst-tier overrides
  (JUDGEMENT) and market-tier overrides of company levers (MARKET) stay visible gaps. A sales-to-capital default every
  override replaced is no longer warned about. analyst_sheet: an event with unknown keys is rejected ('unknown_keys'), a
  key risk the FactBook cannot back is listed in `key_risk_rejected`.
- Pipeline and monitor. `casefile['evidence_pack']` keeps the pack's provenance (potential readings, competitors,
  falsifiers, warnings; report.md 'Evidence pack'); `thesis_from_casefile` freezes each pack falsifier as a dated trigger
  (review_by no later than its date) and `check` lists a due one as a re-review reason.
- Ticker workflow. Four gather lanes with retrieval budgets (numbers incl. price and market; reports and guidance;
  expectations; field), one Understand agent, the pack merged by `pack-merge`; AV statements and SEC companyfacts are the
  primary structured sources (the FMP statements/analyst denials are skipped; pipeline-plan's folder field ignored);
  results-day citability rules in COMMON; Position sets current_year_revenue, period, forecast_options; Value defines
  `ran` as forecast and valuation stages ok and reports the engine's review_status; `verified` needs all three review
  lenses; Deliver copies the engine table verbatim. `deep` also finds a same-version calibration in runs/universe_*.
- Tests: test_company_first_fixes (end to end on the synthetic demo company), updated company_path, scenario_reasons,
  headline_value, decision_render, driver_sources, evidence_pack.

### company price path per scenario: next-fiscal-year EPS x an evidenced multiple (company_path, 25.10.2026)

- `company_path.py` (new): `inputs.company_path = {metric 'eps', multiple 'forward_pe', scenarios {bear|base|bull:
  {multiple_path [today, y1..y5], justification justified_high|peak|unproven, reason_he, source_tier, competitors,
  evidence_ids}}, own_history {low, median, high, years}, peers [named competitors]}`. Price at year t = the scenario's
  multiple x the EPS of fiscal year t+1 of the engine's own executed forecast (net income = EBIT after SBC - interest -
  modelled taxes + investment income after tax - noncontrolling distributions, over time-weighted shares); a scenario
  without inputs is absent, never defaulted; zero or negative EPS, a year beyond the forecast or a funding-blocked scenario
  has no price, with the reason; a 'peak' multiple above the company's own-history median is flagged with its stated
  reason. `pipeline.run` stores `casefile['company_path']` (stage 'company_path'; display only, outside the review digest;
  no other value changes). Each priced row stores its reason (`reason_he`). Values are conditional estimates, not market
  prices.

### bull, base and bear in the owner's words, and a reason behind every scenario price (25.10.2026)

The owner (25.10.2026): "Not 'likely' — what is 'likely'? I want bullish, base and bearish. And behind the scenes every
price has a reason: if something happens, then this price."

- Labels. Every owner-facing Hebrew word that names a valuation scenario is now bear = דובי, base = בסיסי, bull = שורי:
  the yearly table header `| מועד | דובי | בסיסי | שורי |`, `שווי הוגן (תרחיש בסיסי)`, the range row `דובי / שורי` and
  the table notes (render.py); the paragraph's risk sentence ('בתרחיש הדובי', was 'השמרני'); plain_verdict's headlines,
  explanation, sensitivity text and yearly table (`| תאריך | דובי | בסיסי | שורי |`, the tail column 'הצלחה גדולה' kept);
  the analyst sheet's asymmetry reading, closing note and thesis; decision's bear-tolerance reason, margin text and the
  no-IRR clause; decision_summary's paragraph ('המרכזי' / 'השמרני' / 'האופטימי'); the run report column ('הפסד בתרחיש
  הדובי'); the HTML valuation table (was 'שלילי / חיובי'); 'דובי/בסיסי/שורי' in handoff and allocation. Unchanged: every
  bucket and verdict key, quality levels ('סביר' / 'סבירה' = adequate), 'חוב סביר', 'תשואה סבירה', 'מחיר סביר', risk.py's
  'בתרחיש הבסיס' (already base), the generic 'טווח תרחישים'. Packets (plain_language, annual_valuation, master_process step
  13, synthesis, analyst_sheet), docs/PLAIN_LANGUAGE.md and CLAUDE.md now prescribe these words; the 'likely range' of
  master_process is the base range ('טווח הבסיס', the base at the required return ±1pp). Release notes below keep their
  original wording.
- `scenario_reasons.py` (new): `build(casefile)` reads the executed casefile and never recomputes or changes a value. For
  each scenario: the condition (`if_he`: analyst events, else the model's own drivers in plain Hebrew: revenue growth in
  the first forecast year -> the last explicit year and the long-run rate, operating margin after SBC start -> end,
  external funding and dilution, cost of equity; never an invented story); for today and each dated row of the owner's
  yearly table (`dc.expected_return.yearly_at_model_cost_of_equity`, else `val.result.annual_values` through the fifth
  anniversary) the executed value, the drivers of the fiscal year that holds the date (revenue, growth, operating margin
  after SBC, EPS and FCF per share on average model shares, diluted shares, cumulative distributions; an interim split at
  the fifth anniversary merged back into its fiscal year), the implied P/E = value / EPS of the next full fiscal year
  (null with the reason when it is missing or not positive) and `why_he` ('אם …, אז ב־2027/11: הכנסות …, רווח תפעולי …
  → שווי …'); the assumption ids (scenario, period, terminal, underwriting scenario reviews, review status), the analyst's
  validated overrides as context, and `gaps` for what the casefile does not hold (a synthetic casefile without forecast
  paths gets gaps, not filled-in numbers). Status computed / partial / not_available; a valuation the owner's table does
  not show (`render.value_display`, factored out of hebrew_table without a behaviour change) gets no values and says why.
  Output is deterministic; rounding is stated in the output. `render_he` writes the short Hebrew sections.
- Events: optional `inputs.analyst_sheet.scenario_events = {bear|base|bull: [{event_he, evidence_ids}]}`, validated by
  `analyst_sheet.build` against the FactBook's sources and qualitative ids (like key_risk); an event without ids, or with an
  id the FactBook does not hold, is kept and marked a hypothesis ('השערה: …'). The sheet carries the key only when the
  input is supplied; it is not bound to the reviewer's signature and changes no number.
- Wiring: `pipeline.run` stores `casefile['scenario_reasons']` after the analyst sheet (not a stage: a failure is recorded
  inside the key, so no other casefile key changes; the casefile hash covers it). `pipeline` and `deep` write
  `out/scenarios_he.md`; `hebrew_table` adds one condition line per scenario under the yearly table ('דובי = אם …', no
  prices). CLI `scenario-reasons <casefile.json> [--json]`, MCP `scenario_reasons {casefile}` (docs/MCP.md).
- Review fixes (same day). The current fiscal year: a valuation date shortly after the fiscal year end leaves a 0.9-1.0
  year stub, which was read as a full year (stub revenue and EPS shown as the fiscal year, the implied P/E on the stub's
  earnings, a false growth gap); the first period, starting on the valuation date, is now always the current fiscal
  year (a full year only when it starts on the previous year end), so the P/E uses the next fiscal year. Banks
  (residual income) get their own drivers: the ROE path, payout, book value per share, EPS = model net income / shares,
  the merged fifth-anniversary year's net income summed; another route ('nav' and the specialist snapshots) says that its
  drivers are not read, and two different values never share one condition text without a gap saying so. Rows show
  executed revenue growth (the path rate is kept as `revenue_growth_assumption`); the condition names the long-run
  discount rate when it differs from the explicit years' and drops the rate when the scenario has no value. Dated prices
  follow the owner's table rows (`render.yearly_table_rows`, one rule for both): when the table has no yearly part, none
  are listed and the text says so instead of pointing to it. Hebrew: the note names the year of the first growth rate and
  dates the end point; a decline followed by growth reads 'ואחר כך יחזרו לצמוח'; a negative margin is an operating loss;
  distributions in the future tense; 'אומדן רווח למניה' with one line saying it is not GAAP; gaps as plain nouns with
  their fiscal year; hypotheses agree in number and the owner's note carries no raw evidence ids. Events: evidence_ids may
  be a list or one id as text (`analyst_sheet.evidence_id_list`, shared); a malformed event never raises out of the
  analyst sheet (it cost key_risk and the risk row before) and is listed in `analyst_sheet.scenario_events_rejected`
  (replacing `scenario_events_ignored`) and said in the reasons. A malformed casefile is invalid input (ValueError) for the
  CLI and the MCP tool. -0.0 is written 0.0; the rounding note states the whole-number P/E. The paragraph, decision texts,
  analyst sheet and the HTML header now say 'התרחיש הבסיסי' where they said 'תרחיש הבסיס' (risk.py unchanged).
- Invariance, checked against 88cda3f on the ADBE fixture pipeline, the synthetic scale-up platform and the synthetic bank:
  no number, verdict code, valuation input or hash input changes. The casefile keys that differ are `scenario_reasons`,
  `casefile_sha256` (which covers it) and, only by scenario-name strings, `analyst_sheet` (reading_he, to_close_he,
  thesis_he) and `dc`'s verdict texts (reason_he, what_would_change_it_he: 'תרחיש הבסיס' -> 'התרחיש הבסיסי'); every
  changed string equals the old one after mapping the old scenario words to the new ones. The table and paragraph are
  identical after the same mapping, except the three condition lines. tests/test_scenario_reasons.py keeps the in-repo
  part of this (the same run with the builder switched off differs only in that key).
- Honest limits: the reasons explain the model's conditional values; they are not forecasts, probabilities or price
  targets, and they do not make a draft a conclusion. EPS is the engine's estimate, not GAAP: on the operating route
  (EBIT after SBC - interest - cash taxes) / average model shares, investment income excluded; model shares carry no
  buybacks, so per-share growth from cash returns shows in the distributions, not in EPS; on the residual-income route
  model net income / constant shares.

### ticker runner, evidence pack, skeleton brain (25.10.2026)

- Saved Workflow `ticker` (`.claude/workflows/ticker.js`, args `{ticker, as_of}`): one company on the owner's ticker in
  seven phases — Identify; Gather (nine parallel lanes: statements, reports/release/transcript, guidance and its track
  record, consensus, KPIs/segments/customers, named competitors, bottlenecks and potential, market and geopolitics, own
  price and multiple history; connector order per session_capabilities, Interactive Brokers read-only); Understand (three
  parallel parts); Position (potential vs named competitors, base = most likely path, bull = what must happen, bear =
  what breaks, events with evidence and numeric falsifiers); Value (evidence pack -> inputs -> `deep` for that company);
  Review (three adversarial lenses, one repair pass, otherwise a draft); Deliver (the Hebrew דובי/בסיסי/שורי table, short
  explanations, sources as links, reasons file, forecast-ledger freeze). Outputs under runs/tickers/ (gitignored).
- `evidence_pack.py`: the evidence pack format and `to_inputs` / `to_bundle`. The inputs carry forecast overrides with
  `source_tier` (contract shared with the engine's tier work), `company_path`, `analyst_sheet.scenario_events` and
  `key_risk`, card assessments, dated market inputs and an `evidence_pack` provenance block; the bundle registers the pack's
  sources (`web:<id>`) and items in the FactBook so every cited id resolves. Strict: unknown keys, unknown or not yet
  citable evidence, look-ahead dates, consensus as evidence of a fact, guidance from a non-issuer source, a tier without
  matching evidence, an unnamed competitor or peer, a syndicated copy marked independent are refused, all listed at once.
  The guidance track record (mean actual / guided midpoint) is always recorded and applied only where a guidance-tier
  growth driver says so: (1 + g) x ratio - 1, before and after kept. CLI `pack-to-inputs <pack> --out --bundle`, MCP
  `evidence_pack_to_inputs`; example `examples/evidence_pack_demo.json` (synthetic).
- Brain: `master_process` rewritten as the ticker skeleton (the steps grouped into the seven phases, each with entry,
  outputs and exit rules; the new Position step; the reasons-behind-every-price rule; base rates only as a warning row);
  new packet `sources_registry` (kinds, quality order, dates, independence, syndicated copies, untrusted text), in the
  research plan. CLAUDE.md "On a ticker". scripts/check_wheel.py asserts the packets and the converter ship.
- No valuation arithmetic changed; the converter fetches and estimates nothing. Tests: test_evidence_pack.

### company-first levers: source tiers and visible gaps (Track A, 25.10.2026)

- Forecast overrides take an optional `source_tier` (guidance, contracted, consensus, own_history, competitor, market,
  analyst; absent = analyst); `competitor` needs `competitors`, the named direct competitors. Applied overrides and the
  ledger carry the tier.
- `fc['driver_sources']`: for every lever the DCF uses (growth anchor, growth half-life, mature margin, sales-to-capital,
  terminal ROIC, SBC, cost-of-equity premium, cyclical treatment, horizon) and every scenario, the tier and evidence ids, or
  the engine default with its DEFAULTS_MAP class (SIZE / CONST / TYPE / XSEC; None when the company's own numbers set it)
  and the validation bound that binds. `fc['driver_gaps']` lists the levers still on SIZE/CONST/TYPE/XSEC defaults;
  `dc['default_levers']` carries it and the Hebrew table shows one row "הנחות בלי נתון של החברה" (or "אין"). Gaps never
  raise the confidence; the outside-view row stays a warning.
- Company evidence wins: a company-tier override of base growth lifts the revenue-scale cap (SCALE_CAPS) on the trailing
  anchor and the first year (`growth_scale_cap_not_applied`, a warning only) and drops the card's VERY_LARGE stage relabel
  (`size_relabel_not_applied`); a company-tier base half-life, mature margin or sales-to-capital sets the bear and bull
  defaults around the evidence instead of the stage or type table.
- No-override invariance: without overrides (or with untiered ones) every spec and value is unchanged (spec hashes of the
  ADBE, synthetic and pipeline fixtures pinned in tests/test_driver_sources.py).

### business-economics layer `econ`, phase A (25.10.2026)

- `econ` reads each business through its own economic identity (docs/ECONOMICS.md; branch `econ-layer`, work packages
  0-8 of the 21.10.2026 design). Registry `brain/economics.json` (10 models, 6 overlays, KPIs with unit, basis and tag,
  identities, checks, falsifiers, reading plans, the Hebrew vocabulary) and packet `economic_models`; KPI intake
  `facts.add_kpi` / `econ.kpi_table` (sourced, point in time); identities and the tagged growth split
  (`evaluate_identity`, `growth_split`, `review`: structural / cyclical / macro / mix / reclassification with the residual
  shown); model selection by economics (`select`: two agreeing signals, KPI facts before labels, `unclassified` instead
  of a forced label); overlays for who funds the demand, the supplier chain, acquisition SBC tranches, bank capital,
  contract coverage and segments; process and expansion evidence (never a moat rating); `quarter_review(case.economics)`
  with the identity-based peak exception (PLTR 0.809 and RDDT 0.970 drop to medium, SKHY stays high).
- Close-out review of WP4-WP8: the peak exception lowers the flag only for an evidenced model (econ.select's rule on the
  subset, or an analyst override with a reason and evidence ids available at as_of); a bare model id keeps it high
  (`model_not_evidenced`). `contract_coverage` counts its terms as issuer-stated only when every source of the block
  available at as_of is an issuer document (before, any cited issuer source, even one published after as_of, sufficed).
- WP8: MCP tool `econ_review({case, options?})` and CLI `econ-review` (selection, review, Hebrew text, estimates vs
  price); `econ.render_he` returns the owner's rows word for word as the design's section 4 (row "איך העסק מרוויח", the
  digital-bank extension of "מה המחיר של היום מניח", a risk-row fallback, the note line; names in Hebrew only through
  the caller's `names_he`); `econ.price_vs_estimates` (estimate change, price change, implied forward-multiple change,
  `multiple_moved_more`; "what moved; not a signal; not backtested."). The registry validates that every KPI and factor
  has a Hebrew word. Prereg deviations from WP3-WP7 logged in docs/prereg_econ.md before any held-out run.
- No-input invariance: the pipeline never calls the layer; with no KPI facts and no econ input every existing output is
  byte-identical. Thresholds are uncalibrated screens; the preregistered held-out checks H1-H3 have not been run, so no
  accuracy claim is made. Tests: test_econ_fixtures, test_econ_registry, test_econ_kpi, test_econ, test_econ_select,
  test_econ_overlays, test_econ_process, test_quarter_econ, test_econ_render.

### market context: country risk, geopolitical exposures, market regime (25.10.2026)

Repairs after review (25.10.2026):
- Country risk net of the market's own exposure: the ERP (S&P 500 implied) and the index return (cap-weighted implied
  return of the largest US companies) already carry the market's average country exposure, and `MARKET_NEUTRAL_ADJUSTMENT`
  was measured without a country row. New row `country_risk_market_neutral` = -`market_context.COUNTRY_RISK_MARKET_RATE`
  (0.40pp) x the mapped share, next to `country_risk`, in the operating and residual-income tables, kept in the terminal
  rate; the decision adds the net. The rate is measured by `scripts/measure_country_risk_market.py` on the 2026-10-21
  large-cap sweep (0.42pp listing-weighted, 0.49pp with the universe rows' values, 0.31pp without Alphabet; 125 of 490
  companies, 26% of value; `runs/macro/country_risk_market_2026-10-21.json` kept in Git). `inputs.forecast.country_risk`
  now requires `mapped_share`. `pipeline.summary` carries `country_risk` {gross, net} and `calibration.market_implied`
  reports their value-weighted means, both only when present. A register entry priced via the country row reads the net
  (`risk.component_value`, `decision._risk_pricing`). PPG: net +0.90% (gross 1.30%), base 147.5 (was 136.6).
- Marginal band: the "index low/high + premium" rows carry the country row like the hurdle (`_hurdle_band`); on PPG they
  had stayed at 9.25% / 11.25% while the hurdle moved to 11.55%.
- Exposure effects are measured against a zero-shock control (`risk._effect`): the same row re-run through the same funding
  strip and re-close without the shock (`base_value_control`, `bear_value_control`, `bear_effect`). Against the executed
  base, a 0.0001% share had read as LITE +464% and COHR -0.5%; now about 0 (test with a funded base row).
- Point in time: `brain/country_risk.json` is dated 2026-02-16 (the February 16 ratings update in its contents), with the
  page's "Last updated January 5, 2026" kept as `page_last_updated` and `as_of_basis`; `build_country_risk.py` derives it.
- The paragraph's market-regime clause is fitted last (kept only where it fits): it no longer displaces analysis sentences.
- `review.inputs_digest` binds `market_context.geography / operations / exposures` when supplied (`review.MARKET_BOUND`,
  listed in `binding.covers`); regime readings stay unbound. Absent -> the digest is unchanged.
- Docs: regional averages ('EMEA', 'Latin America') and the largest-add-on split choice can overstate (row notes say so);
  Hebrew names for every table country and region, a maqaf before a name without one; small shares keep their digits
  (0.3%, not 0%).

- New `market_context.py`, switched on by `inputs.market_context` (pipeline stage `market_context`; docs/MARKET_CONTEXT.md,
  brain packet `market_context`). Without the key nothing runs: casefile hash, report, table and paragraph are
  byte-identical (ADBE fixture; TSEM, PPG, UBER from runs/universe_2026-10-21).
- Country risk (Damodaran): packaged dated table `brain/country_risk.json` (ctryprem page of January 5, 2026, retrieved
  2026-10-25: 178 countries, 17 regional averages; US Aa1 CRP 0.23%, Taiwan 0.78%), weighted by revenue by geography from
  the annual report or an evidenced override, and by long-lived assets / an evidenced operations split as a second channel:
  add-on = max over the two channels of sum share_i x max(0, CRP_i - CRP_US) (the engine ERP is the US implied premium).
  Its own `country_risk` row in the cost of equity (operating and residual-income routes), kept in the terminal rate
  (structural), sourced `input:country_risk`, and in the decision's required return. Aggregates ('Rest of world', 'Non US',
  'Americas') and unitemized shares carry no add-on and make it a labelled lower bound; overlapping region/country splits
  use the complete non-overlapping subset with the largest add-on; no readable split -> no row and an open question.
  `inputs.forecast.country_risk` {value, as_of, channel, title, url, detail, evidence_ids} is validated (0-20%, dated on or
  before as_of, evidence ids). 60% US / 40% Taiwan -> +0.22%.
- Exposures: `market_context.exposures` [{kind: taiwan_supply | china_export_controls | tariffs | sanctions | regulatory_ban |
  conflict_disruption, share_of_revenue | share_of_supply, evidence_ids, note, shock?, cost_rate?}] -> one failure scenario
  `exposure:<kind>` each (`risk.exposure_variant`: the evidenced share of revenue lost from the first full year, or the
  evidenced cost rate on the exposed share; applied to the bear row and, separately, to the base row; re-run through
  drivers + valuation like the permanent-loss scenarios) and one register entry `geo.exposure.<kind>` in the counter-thesis.
  No default severity, no probability; the executed bear/base/bull are unchanged.
- Market regime (context only): `scripts/collect_market_context.py` saves FRED DGS10, BAA10Y, VIXCLS, T10Y2Y with URL,
  retrieval time and SHA-256 under runs/macro/ (gitignored); `read_regime` reads them at as_of; `classify_regime` bands are
  historical percentiles (documented in the module) -> one Hebrew row in `render.hebrew_table` and a clause in the
  paragraph when it fits. No value, rate, scenario or verdict reads it (tested). CLI: `--macro <folder>` on `pipeline` and
  `deep` (regime only: never switches the country row on) and `--country-risk`.
- Render: country-risk and exposure explanation lines under the Hebrew table, a `## Market context` report section; the
  analyst sheet's geopolitics question carries the computed exposure effects.
- Known open item: a near-zero equity under the survival / external-funding model (LITE on 2026-10-21 data) is not
  monotonic in the cost of equity: its base rose 2.80 -> 3.21 with a 1.15% country row.

## 0.13.0 — calibration fixes (25.10.2026)

The owner found the engine's values far below market prices and below a human analyst's; the 2016-2025 walk-forward (491
large caps) confirmed values ~35-45% below the prices reached 1-5 years later, a gap that never closed, and value/price with
about zero ranking power. Authorized fix, kept principled: inconsistencies and bugs only; no anchoring to the current price,
no momentum premium, no constant tuned to the backtest. Each change below was A/B'd on the same 61 names (2016-2025) where
it could move a number. Combined result (`scripts/backtest_engine.py` on the 2026-10-21 universe, 0.13.0 at 88cda3f): with every 0.13.0 fix the median engine value / price is 0.673 (0.12.0: 0.719); the latest formations rose (2024 0.53 -> 0.63, 2025 0.51 -> 0.66) and the early ones fell under the dated ERP (2016 0.86 -> 0.67), so the year profile is flat (2022 aside, 1.12). The value dated one and three years ahead sits a median 37% below the price then realized (log bias -0.46 at both horizons; 0.12.0: -0.37 / -0.35), median absolute log error 0.56 / 0.64 (0.12.0: 0.50 / 0.57); the IRR path is unchanged (bias -0.09 / -0.22); value/price IC 0.024 (t 0.5): no ranking power. No errors on 497 valued rows.


- Expected price path, spec_version 2: the single registered walk-forward (25.10.2026, commit 5b813ae) FAILED
  (`docs/PRICE_PATH_V2_RESULTS_2026-10-25.md`). S2 bias fails at 3 / 5 years (base-path median log error -0.045 / -0.119 /
  -0.215, half of v1's) and S1 accuracy fails (MdALE 0.206 / 0.337 / 0.455: better than v1 and the constant analyst-EPS path,
  worse than the engine-IRR path and a 7% drift); S3 band (inside 79% / 77% at 3 / 5 years, but 4.4x / 7.0x wide) and S4
  attribution pass. The market class multiple removed the multiple term (-0.22 -> -0.04 at 5 years); the held margin among
  survivors is what is left. Not shown in the table (section 9: no rows). `pricepath.MEASURED_V2` / `MEASURED_VERDICT_V2` /
  `TABLE_STATUS_V2` record it in every spec-2 output; `runs/backtest/pricepath_v2/{summary.json,REPORT.md}` kept in Git; the
  run record and post-run notes are in section 10 of the preregistration. No constant changed, no re-run.
- Expected price path, spec_version 2 implemented (`docs/prereg_price_path_v2.md`, registered before this code):
  `pricepath.anchors(..., spec_version=2)` / `price_path(..., spec_version=2, cross_section=...)` -- run rate = mean of the
  latest FY, latest quarter and 3-year CAGR; TTM margin held in the base for every company (floored at 1% as spec 1,
  clarification 3), bear / bull margins at the
  company's own 10-year low / high bounded at 0.5x / 1.5x today's; bear / bull growth fading to the size class p25 / p75 at
  one half-life; the multiple target is the market's point-in-time trailing P/E by growth x margin bucket
  (`cross_section_table`, CELL_MIN 20 with growth-row and whole-cross-section fallbacks) with today's log premium clipped
  at +-ln 3 halving at the stage half-life; bear <= base <= bull by construction, asserted (`OrderingError`).
  `evaluate_v2` adds the lane_v1 baseline, the ex-post attribution (S4), 30% band tails and the data-defect robustness.
  `scripts/backtest_pricepath.py --spec 2 --v1-rows ...` runs two passes (anchors, then table and paths). Spec 1 is the
  default and byte-identical (pinned hashes in `tests/test_pricepath_v2.py`). Pre-run clarifications: v2 section 10.
- Expected price path lane (`fundamental_engine/pricepath.py`, `scripts/backtest_pricepath.py`, preregistered in
  `docs/prereg_price_path.md`): the 2016-2025 walk-forward FAILED every registered criterion (base-path median log error
  -0.11 / -0.31 / -0.50 at 1 / 3 / 5 years, the random walk's; `docs/PRICE_PATH_RESULTS_2026-10-21.md`). Not shown in the
  valuation table. Post-run audit and error attribution in `docs/PRICE_PATH_2026-10.md` (the margin held at its anchor and the
  Gordon-level multiple target are the two terms; a registered scenario-ordering defect is pinned by `Spec1AuditTests`);
  `pricepath.MEASURED_BASELINES` and `irr_path_band_he()` record what the table shows instead; the read-only diagnostics are
  `scripts/pricepath_v1_*.py`. Version 2 is registered, not run: `docs/prereg_price_path_v2.md`.
- Terminal growth follows the convention of the ERP it is paired with (`forecast.TERMINAL_BEAR_GAP` comment): base = bull =
  risk-free, bear 0.5pp lower (never above base), replacing min(risk-free, 3%). Damodaran's implied ERP is solved with
  perpetual growth at the Treasury yield; pairing it with a 3% cap marked every company down whenever the risk-free rate
  was above 3% (2023-2026). Reinvestment stays g / ROIC; the terminal WACC floor (g + 3pp) is unchanged and can now bind for
  levered companies at any rate level, as it already could when rf < 3%. Risk-free above 6% -> warning `terminal_growth_high`.
  An analyst `terminal_growth` override still wins; decline stage unchanged. The cyclical trend bound keeps -3%..+5%
  (`CYCLE_TREND`, was written as cap + 2pp). Residual-income route: terminal growth = min(risk-free, ROE x retention,
  Ke - 0.5pp); book-growth floor kept at 3% as `BOOK_GROWTH_FLOOR`. `review` tolerates the spec's 6-decimal rounding in
  `terminal_growth_vs_risk_free`.
- Terminal growth stops at the valuation contract's bound (`valuation.TERMINAL_GROWTH_MAX` = 8%, the validator's existing
  limit, now one named constant): a risk-free rate above it (a high-rate reporting currency, `fb.market.risk_free` by
  currency) holds base and bull at 8% and the bear at 7.5% with the high-severity warning `terminal_growth_capped`, where
  growth = rate failed the contract with `invalid_spec` ('growth: above 0.08'); the residual-income route takes the same
  bound. `terminal_growth_high` and `terminal_growth_capped` now read the growth actually used, after analyst overrides
  (an override in every scenario removes them). The residual-income ledger states its ROE - 0.1pp bound. The drift quoted
  for the old convention is approximate (a ten-point fit).
- Terminal cost of equity = the mature table (`forecast.TERMINAL_KE_DROPPED`). The stage add-on (early +3pp, growth +1.5pp,
  maturing +0.5pp, turnaround +2.5pp) and the one-off `needs_external_funding` add-on were charged on the perpetuity after a
  forecast that is mature by its terminal date (growth faded, mature margin): the risk of a phase the model has ended,
  discounted forever (Damodaran: beta moves toward one in stable growth; his young-growth model moves the cost of capital
  to the mature rate). Now they apply to the explicit years only; the terminal WACC is built from the terminal cost of
  equity = the same table without them (structural rows stay: cyclicality, capital intensity, regulated utility, weak
  card dimensions, market-neutral), unlevered at `MARKET_DE` and relevered at the same model D/E, floored at g + 3pp as
  before. The terminal ROIC hurdle (Ku + 2pp, the persistence blend, the strong-moat floor) and the franchise-durability
  excess are measured against the same mature table (one hurdle for the terminal state; with a binding WACC floor this can
  lower a levered name slightly). Unchanged: the explicit years and the roll-back of the terminal value at Ke, the mature
  and decline stages, an analyst `cost_of_equity` override (used for both); the same terminal rate in every scenario (the
  bear is never below the mature table). Output: `cost_of_equity.terminal_cost_of_equity` {s}, `components.terminal`
  {table_rate, dropped, kept, asset_unlevered, cost_of_equity, leverage_relevered, formula, reason}, the spec's
  `terminal.cost_of_equity`, and the cost_of_equity ledger row names the rate and the dropped rows. No constant tuned.
- Walk-forward ERP: `engine_backtest.IMPLIED_ERP` (Damodaran's start-of-year implied ERP, FCFE, 2009-2026, from
  histimpl.html; implpr.html agrees through 2016) is passed at each formation, point in time (a January formation uses the
  previous year's estimate), instead of the fixed `ERP_DEFAULT` 4.5%; rows record `risk_free` and `erp`.
- Cyclical industry word at a 'peak' on a non-cycle history (`model_card._cyclical`). An industry word (`CYCLICAL_WORDS`:
  travel, leisure, freight, machinery, construction, ...) plus the 'peak' margin position had normalized the valuation to
  the window-mean margin even when the reported history was a scale-up from losses, a loss-making grower or pre-commercial
  noise, the three histories the same function already exempts from the data-cycle test (an Uber-like 'travel' history,
  -22% .. +11% on revenue rising every year, was valued on its -2% start-up mean: 0.08x price, 0.49x with the engine's own
  `inputs.cyclical` structural treatment). Now the margin position is read off a cycle-shaped window only (those histories
  get no position), and without a cycle in the data neither does a window with a loss year on normally growing revenue
  (the data-cycle test's own demand-dip rule says no: start-up or investment losses, or a one-off charge, not a cycle
  trough). A loss year on a demand dip is the usual trough and keeps the peak reading, and so does a loss year whose revenue
  change is unknown (the window's first year). The word stays a visible analyst question: card `cyclical.history`
  (scale_up | loss_grower | pre_commercial | None, read by `history.history_shape` over the card's window),
  `cyclical.peak_question`, a reason and a valuation limitation; `inputs.cyclical` (card: value + reason; forecast:
  treatment) settles it either way (the question is then cleared), and a confirmed cycle restores the position.
  Downstream: the forecast's `cyclical_growth_start` (fade from the long-run CAGR instead of this year's growth) is skipped
  only when the card reads such a history (route `cycle_history`, cleared once an analyst confirms a cycle) and the CAGR's
  own fiscal years have that shape too (`history_shape` over the last 6, or 4 for a 3-year CAGR): that CAGR is the ramp
  from a small base, not a like-for-like cycle average. Kept as they were: the data-cycle path (a history that shows a
  cycle, loss years included), the +0.5pp cyclicality row of the cost of equity and the decision premium
  (industry-economics risk statements, not a peak reading), and the screen's own peak check. No constant tuned.
  Repair after review: the first version asked for a window of positive margins (min over the whole 7-15-year window) and
  gated the growth start on the card's whole-window label. That extrapolated Micron's 18.5% peak at the 2016-07
  walk-forward formation (2009 and 2012 memory troughs: base 0.59x -> 1.20x price, bear 0.01x -> 0.55x) and let Tesla's
  2010-2011 pre-commercial years skip the cyclical growth start for its commercial 2018-2023 CAGR (2024: 0.32x -> 0.20x).
  Both are back to the cycle reading. Known limits: a flagged scale-up has no cycle stress in its bear (the forecast's
  cycle-peak bear cap needs the card's peak; its 'cycle average' would be a start-up mean), and the scale-up label cannot
  tell a volume ramp from a price-driven commodity ramp (revenue up every year from a loss year); `inputs.cyclical` decides.
- Cyclicality is charged in the rates only when it is evidenced (`model_card.cyclical_evidence`). The +0.5pp `cyclical` row of
  the cost of equity (`forecast.KE_FACTORS`) and the 1pp `cyclicality` component of the decision premium (`decision.PREMIUM`)
  had been charged from an industry word alone (`CYCLICAL_WORDS`: machinery, construction, travel, hotel, ...): MKT
  ('industrial machinery', no cycle in its reported history, not normalized) paid +0.5pp on Ke, about 9% of value at Ke, and
  1pp on the hurdle (11.35% against a Ke of 8.85%). Evidence now means one of: the reported history shows a cycle (the card's
  `data_shows_cycle`), an analyst confirmed one (card `inputs.cyclical` value true, forecast `inputs.cyclical` treatment
  'cyclical', or a `method_override` to normalized_cycle_fcff), or the business type is cyclical by economics
  (`CYCLICAL_TYPES`: semiconductor, commodity). An analyst who rejects a cycle settles it either way. A word-only flag, also a
  word at a 'peak' margin of a history with no cycle (still valued around the cycle, with its unresolved material issue),
  charges neither and stays a visible question: the card's `cyclical.industry_word`, forecast route and
  `cost_of_equity.components.cyclical_evidence` {evidenced, basis, why, question}, the low-severity warning
  `cyclical_unevidenced`, and the premium component's basis. Evidence only gates a charge: a business the card does not flag
  is never charged; a card with no recorded basis (hand-written, older dossiers) keeps its stated value. Netting with the
  decision layer, checked: nothing is counted twice, so nothing is netted. The hurdle is max(index + premium, Ke), two
  alternatives, and the IRR and every value at the required return shift the explicit and terminal rates uniformly from Ke,
  so the Ke's cyclical row cancels out of both and reaches the hurdle only through the Ke floor, whose top-up row is already
  net of the premium component (subtracting it would under-charge exactly when the index leg binds). Unchanged: the 0.5pp and
  1pp sizes, the cycle normalization paths, the durability-lift exclusion and the cyclical growth start (they still read the
  word flag; not rate charges). `MARKET_NEUTRAL_ADJUSTMENT` was measured with the word-only rows included, so the market's
  average add-on is now slightly lower; calibration.market_implied reports the net adjustment on the next sweep. No constant
  tuned.
- Platform routing (`model_card._business_model`, `business.BUSINESS_TERMS`). In the 2016-2025 walk-forward only 13 of 3,685
  valued rows were `platform_marketplace` (mature margin 25%/40% against the consumer brand's 15%/25% and the industrial
  12%/20%): Visa, Mastercard, eBay, MSCI and Corpay were consumer brands through Nasdaq's sector for every SIC 7389 filer
  ('Consumer Discretionary'), Airbnb an industrial ('Diversified Commercial Services'), Meta, Pinterest and Reddit
  subscription software, because the annual report that decides a generic label is (correctly) not used before it was filed.
  Three evidence rules, no ticker named: (1) SEC codes under which platforms file next to operators (`INTERMEDIARY_SIC`:
  4700-4799 transportation services, 5960-5969 nonstore retail, 7300-7369 and 7380-7399 business services) with a type from
  the sector or a catch-all label (`CATCH_ALL_LABEL`: diversified, NEC, business/commercial services) take the existing
  `platform_signature` (the MIXED_RULES rule: gross margin >= 70% and capex <= 8%; no cost-of-revenue line: capex <= 8% and
  operating margin >= 20%) when the business section did not decide; the label type stays secondary and a failed signature
  is stated with what would decide. A specific label (C.H. Robinson's 'Integrated Freight & Logistics') and codes outside the
  list (a high-margin cosmetics brand) are never relabelled; financials, REIT, biotech and conglomerate routes untouched.
  (2) IT/data-processing labels: gross margin >= 65% is subscription software only when customers prepay (deferred revenue
  >= `SUBSCRIPTION_DEFERRED` 15% of revenue, the existing subscription-signature threshold, or not reported); billed as
  delivered (Meta, Pinterest, Reddit, AppLovin: 0-3%) it is a platform; the report's platform vocabulary decides first.
  (3) Item 1 vocabulary rechecked on 153 business sections: a bare 'driver(s)' (truck drivers, cost drivers) had typed XPO a
  marketplace and Rivian a marketplace-then-infrastructure company, and a bare 'rider(s)' reads utility tariff riders, so only
  driver/rider/courier pairs count; new `online_travel` (cut 20: Booking 58, Expedia 44 vs at most 7) and `advertising`
  (cut 25: Alphabet 43, Reddit 57, Pinterest 85 vs at most 10) groups. The collector also reads the annual report for those
  SEC codes (Airbnb had none saved). Census on the 2026-10-21 universe, annual dates 2016-2025: platform rows 13 -> 114 of
  4,251; on 2026-10-21 11 -> 18. Unchanged and stated: Uber, DoorDash and MercadoLibre in the walk-forward (no signature
  before their margins rose; their Item 1 decides from the report's filing date), Amazon (retail-weighted: gross margin 50%,
  capex 18%, 5 marketplace terms). Cuts set on document words, not on valuations or prices; no constant tuned to the
  walk-forward.
- Scale-up terminal ROIC (`forecast._post_breakeven_roic`, `_demonstrated_roic`). The terminal persistence rule (no moat
  assessment: Ku + 50% base / 71% bull of the demonstrated excess) took demonstrated ROIC = min(latest, 5-year average); on a
  scale-up the average spans the start-up losses (diagnosis profile ROIC -79%, -28%, 18%, 42%, 124%: average 15.6%, faded to
  about Ku+3pp against 124% latest), while the model card already refuses to average start-up losses into margins on the same
  path. When the ROIC window's own fiscal years read `history.history_shape` 'scale_up' (revenue up every year, operating
  margin from losses to the window high; forensics labels matched to the annual rows, no match no reading), the average is
  taken over the years after the window's last loss year, at least `SCALE_UP_ROIC_MIN_YEARS` = 2 (else the 5-year mean
  stays). Unchanged: min(latest, average), the 40% cap, the bear (Ku+1pp), the weak-moat fade, the strong-moat rule, the
  durability lift (a window with a loss year varies by at least 50%: stability 0) and the moat proxy. Shown in `moat_basis`
  (`roic_average_demonstrated`, its basis), the terminal rows and the low warning `roic_average_post_breakeven`.
- Headline value, comparator and expected price path (presentation; no valuation number moves). (a) The table's and the
  paragraph's "fair value" was the value at the hurdle, max(index + permanent-loss premium, Ke): 14-33% below the value at
  the company's own cost of equity (diagnosis profiles before the calibration fixes: QG30 $46.3 vs $53.9, MKT $21.1 vs $31.3;
  the ADBE test fixture today, generic index: $414 vs $591), a required-return choice shown as the company's worth. The headline is now the executed base value at the model
  cost of equity (`expected_return.fair_value`), with the bear/bull range, the yearly bad/likely/good table
  (`yearly_at_model_cost_of_equity`, read from the executed result) and the five-year row of the same valuation; the hurdle
  value is its own row, "buy-below price for your required return X%" (`buy_below_price` = `value_at_required_return`),
  where the verdict, the margin of safety and the marginal band are still measured. (b) The index record names its
  comparator (`decision.COMPARATORS`): `engine_consistent` (a calibration sweep's `index_input`), `generic_assumption`
  (risk-free + ERP, the default; the table says it is the generic assumption and that a calibration file corrects it) or
  `user_assumption`. `deep` and `pipeline` (new `--index`) use the newest calibration file dated on or before the run and
  at most `calibration.MAX_AGE_DAYS` = 92 days older (`calibration.pick`: never a later file), filling `decision.index`
  only when the inputs set none; `--index none` applies none. (c) New row "expected price per share": today's price x
  (1 + r)^h, h = 1..5, r = base IRR moved `PRICE_PATH_PULL` = halfway to the market return, because in the 2016-2025
  walk-forward the IRR path was about as accurate as the best simple baseline (price x market drift) and the dated value was
  not; stated beside it: even the best predictor landed within +-25% of the realized price only ~65% / 50% / 37% of the
  time at 1 / 3 / 5 years (`PRICE_PATH_HIT`). An estimate, never a target; it changes no value, verdict or hurdle. The
  "what today's price assumes" row is unchanged.

- Integration review of the platform, scale-up and headline changes (lean mode: they had not been reviewed one by one).
  (a) The automatic calibration pick took any computed sweep dated within 92 days, so every default `deep`/`pipeline` run
  would have compared stocks with 0.12.0's 2026-10-20 sweep (7.0%) labelled the engine-consistent market of "the same
  engine", after these fixes moved values and IRRs. The bias that cancels in stock IRR - market IRR is one engine's:
  `calibration.market_implied` records `engine_version` (file and `index_input`), `calibration.pick` takes only a sweep of
  the running version (the saved 2026-10-20 file records none: it is not picked), the CLI says why nothing was picked, and
  `decision._index` names an index that records another version a `user_assumption`. An explicit `--index FILE` still
  applies the file. Until `scripts/calibrate.py` is rerun on 0.13.0 the default comparator is the generic risk-free + ERP
  assumption, said as such. (b) Under a platform SEC code a catch-all industry label ('Diversified Commercial Services')
  was relabelled by the platform signature only: the annual report the collector fetches for these codes was never read,
  while a failed signature said the business section decides. Item 1 now decides such a label first, as for a sector label
  (walk-forward rows have no report before it was filed, so the A/B cannot move). The scale-up ROIC change was found
  correct (bear untouched, labels matched, no constant tuned); its missing changelog entry is above.
- Walk-forward crash fixed: 10 rows of the fresh 2026-10-21 walk-forward (LITE 2018/2023/2024, SYY 2017/2019/2023/2024, TPR
  2018/2023/2024) stopped with OverflowError. Their fiscal year ends the day after the July formation's as_of, so
  `valuation._roll_back` annualized the endpoint wealth to the 365th power and, at the implied-return search's low trial
  rates, overflowed. `valuation._annualized` returns None for a rate that is not a real number in float range (no time
  elapsed, a negative endpoint wealth, which had been a complex number, or an overflow); every representable figure is
  computed exactly as before and dated values, IRRs and verdicts are unchanged. The 10 rows now value.
- Version 0.13.0 (`fundamental_engine.__version__`, pyproject, brain catalog). Tests: 1,212 (4 skipped).

## 0.12.0 — results-day review from analyst comparison (21.10.2026)

Includes the 0.11.2 work below. Adds `quarter_review` (MCP tool, `quarter-review` CLI, packet, trigger
`earnings_event`): symmetric after-tax one-off normalization (no default tax; non-recurring unusual opex is added back
in the same pass), opex-vs-revenue spread, adjusted-vs-reported gap, FCF and spendable vs restricted liquidity, segment
profit carriers, KPI basis, guidance vs prior and attributed consensus, guidance track, peak-cycle run rate,
dual-listing premium, bank justified P/TBV and required ROTE, EPS x multiple implied returns, numeric falsifiers.
Four teaching inputs from third-party analyst posts (unverified) in examples/quarter;
docs/ANALYST_COMPARISON_2026-10.md records what was adopted and what was rejected. 9 new tests. No accuracy claim.

## 0.11.2 (in progress, 20.10.2026)

- Engine conservatism diagnosed and the honest fix (21.10.2026, `docs/ENGINE_CONSERVATISM_2026-10-21.md`). The 491-company
  walk-forward shows the engine's value/price drifting to ~0.5 by 2024-2025 (deepest in software 0.33, semis 0.45, growth
  0.51), and the deepest-discount names did NOT underperform — the conservatism on growth is mostly structural (discount rate
  and growth fade) and largely appropriate, not a bug to tune away. Terminal excess-return persistence is now
  durability-scaled rather than a flat 50%/71%: a franchise with high, stable realized ROIC AND durable margins keeps more of
  its excess (ceiling 75%/85%), a thin or volatile record keeps the floor, and inherently cyclical businesses
  (`CYCLICAL_LIFT_EXCLUDED_TYPES`, `route['cyclical']`, `cycle_path`) and weak moats keep the floor; the bear is never lifted
  (`_franchise_durability`). An A/B measured this lever's own effect at under 1% of value — it corrects a principled
  inconsistency, it is not the cure for the perceived over-valuation, and is not presented as one.
- Reframe, not inflate (owner's choice, 21.10.2026): every plain-language table (`render.hebrew_table`) gains a
  "מה המחיר של היום מניח" row from a reverse-DCF the engine already supports (`drivers.implied_growth_shift` via
  `decision.price_implied_growth`): the uniform growth the current price assumes, against the base case and the size-class
  base rates (e.g. ServiceNow — the price assumes ~14% real growth for a decade, ~8pp/yr above the base case, which only ~4%
  of comparable companies achieved). Computed once per table (never in the pipeline or backtest); it changes no value.

- Broad discovery universe on real data: `scripts/collect_universe.py` saves SEC XBRL frames (2021-2025, all filers),
  the SEC ticker map and the Nasdaq listings file with a manifest; `universe.build` makes one row per issuer (multi-class
  market caps summed) with a coverage report (exchanges, countries, sectors, size bands, every exclusion counted with its
  reason); `fundamental_engine universe FOLDER --as-of D` builds and scans. 20.10.2026: 2,470 issuers >= $200m on NYSE and
  Nasdaq scanned in about 5 seconds.
- Discovery stage 3, data check before ranking: `universe.sanity` drops impossible values (margins above 100% of revenue,
  P/E on negligible or non-operating earnings, FCF/NI outliers) and records flags; a critical flag makes the row's data
  uncertainty weak so it cannot be ranked until verified. `scripts/verify_candidates.py` fetches SEC submissions for the
  candidates only (760 on 20.10.2026); the SIC code replaces the coarse listing industry and balance-sheet SIC ranges
  (banks, credit, broker-dealers, insurance carriers, REITs, blank checks) are routed away from operating lenses.
- SEC frames revenue: the larger total of the same period wins over a component tagged 'Revenues' (Verra Mobility).
- Six discovery lanes: new `recovery_mispriced` lens (a setback in revenue, margins, cash or price in a business that
  earned well before, not a three-year decline or a gross-margin collapse, and a price that assumes the setback lasts;
  temporary-versus-structural stays an open question on the candidate). `mispriced_turning` is labelled as the turnaround
  lane; `quality_compounder` now requires no unconfirmed cyclical peak; the listing industry label still gives the
  cyclical cue after the SEC SIC description replaces it (a gold miner no longer passes as a compounder).
- Candidate cards (`candidates.py`): per company, why it surfaced and in which lane, what could make it exceptional, what
  the data supports, what is still a hypothesis, what would falsify it, what the price assumes, risks (including data
  flags), missing data and whether it warrants the full analysis (yes / maybe / no with the reason). Nothing is re-scored:
  every line is read from the scan. Exploratory path: a name the numeric lanes did not admit enters with a written reason
  and dated evidence (source, date, statement), keeping its numeric failures on the card; banks and other unsupported
  economics are not forced through, distress needs a funding view first. `research_queue` rotates across lanes so no
  lane fills the queue. `universe` writes cards.json and a Hebrew cards_he.md and prints the queue
  (20.10.2026: 65 cards, queue ANIP, COLL, GLBE, HROW, NOAH, TCOM, one per lane). A queue is a research priority only.
- Discovery to deep research without copying (`handoff.py`, `scripts/collect_companies.py`, `fundamental_engine deep`):
  the queue's companies get SEC companyfacts + submissions saved verbatim, an identity check (reporting currency from the
  fact units, depositary-share ratio from the listing) blocks what the engine cannot value per share and backfills the
  lane with its next candidate (20.10.2026: Noah and Trip.com report in CNY -> QuinStreet and monday.com), then
  `pipeline.run` runs on each and `handoff.compare` writes comparison.json / comparison_he.md: business quality, growth
  quality, financial strength, capital allocation, price, implied return against the required return and the index, loss
  to bear, data uncertainty, evidenced risks and open questions, each separate, no composite, drafts flagged. A listing
  last sale is the price fallback when no broker quote is saved. MCP: discovery_cards, identity_check, compare_candidates.
- Fixes found on those six real runs:
  - cash roll-forward compared unlike bases because 'restricted' was matched case-sensitively in CamelCase SEC concepts
    (Global-E: cash blocked as a conflict);
  - a scale-up from losses to profit on revenue that rose every year is no longer read as a business cycle (Global-E was
    valued on its -14% average start-up margin: $1.94 a share; $22.6 after the fix); a revenue fall keeps the cycle reading;
  - debt: LongTermNotesPayable, SeniorNotes, notes/loans payable current read; borrowings tagged only as components
    (LongTermLoansPayable + ConvertibleLongTermNotesPayable + LoansPayableCurrent) are summed as derived lines; a debt
    line that disappears without a repayment (recently, or while interest is still paid) blocks instead of reading zero
    (Harrow: $292m of notes read as zero; Collegium: $1.09bn read as zero);
  - financial strength: net debt with EBITDA at or below zero and interest not covered by operating income are weak, not
    unknown (Harrow was rated strong on self-funding alone);
  - the base growth rationale states the anchor actually used (the current fiscal year from year-to-date reports), not
    the trailing figure.
- Systematic undervaluation found on the holdings run (read-only IBKR book, 20.10.2026): the history cycle detector
  counted any two margin reversals in up to 19 years, so write-downs and fines on growing revenue (Microsoft 2012/2015,
  Meta 2012/2019) made mature franchises 'cyclical' and reverted their revenue toward a 'mid-cycle trend' (Microsoft:
  revenue cut 25%, base value $143 against a $529 quote; $329 after the fix). A reversal now counts only when the low
  year is a demand dip (revenue falling, or growth more than 5 points below both neighbouring years and under half the
  median); the other dips are listed as one-off charges. Industry flags and analyst confirmation are unchanged.
- Honest test against the S&P 500 (`backtest.py`, docs/BACKTEST_DISCOVERY_2026-10-20.md): walk-forward 2012-2025 on SEC
  frames and monthly prices, no number used before it could have been filed, share-basis mismatches excluded. The numeric
  screen alone shows no robust edge over SPY; recovery/mispricing and structural tailwind lanes lost in both variants and are
  demoted in the research queue (`LANE_EVIDENCE`); early commercial growth is promising and unproven. A split-factor bug found
  during the test (reverse splits inflated past market values) was fixed before any result was recorded.
- Engine-consistent market return (`calibration.py`): the largest US operating companies valued by the same engine give the
  market's implied return on the same basis (85-company sample, 20.10.2026: 6.5% cap-weighted vs the 8.75% assumption, only
  15% of companies above it). `deep --index calibration.json` passes it as inputs.index, so a stock's edge is measured like
  for like and a shared model bias cancels.
- Portfolio construction (`allocation.py`): the index is the default; active weight = quarter-Kelly x edge / bear-loss^2 x
  confidence, after a valuation-error and cost haircut, capped per name and per shared economic driver; drafts are
  provisional; overweight holdings are listed for review, never as orders.
- Discovery channels (`sources.py`) with explicit roles (screen, signal, attention, lead, evidence) and SEC insider
  purchase clusters from the Form 3/4/5 data sets (`insiders.py`, `universe --insiders`): 212 universe companies with
  clusters in the last 180 days; attached to lane cards, strong ones open 'signal' cards; social attention (LunarCrush) is
  crowding and leads only.
- Convertible notes without terms block a price conclusion only when material (>= 2% of market value): ServiceNow's senior
  notes tagged as convertible and $5m at Ondas no longer exclude the company.
- How the company makes money, from the annual report itself (`business.py`): the collector saves the latest 10-K / 20-F
  and 10-Q verbatim; their inline XBRL gives revenue (and operating income where tagged) by reportable segment, by
  revenue type (standard Product/Service/License members), by product line and by geography for every year in the
  report, segment x product tables, customer concentration, remaining performance obligations and cover share counts per
  class (Meta's multi-class count, which companyfacts omits). Every table is reconciled to the consolidated total; what
  XBRL does not carry (units, prices, retention, unit economics, segment capital, why customers pay, competitors) is listed
  with where to read it, and Items 1, 1A and 7 are extracted to out/reading/ for the analyst. A segment-built growth path
  (each part at its own pace, fading) cross-checks the consolidated forecast (20.10.2026: Microsoft and Meta consistent;
  Tesla's segments imply ~4%/yr against 11%, Harrow's ~24% against 3%: the open temporary-versus-structural questions).
  The model card counts the segment table as segment evidence. A document filed after as_of is never used.
- Judgement block on every decision: material disqualifiers (reliability, unacceptable risk, thesis without a basis yet,
  the last a research task), attractiveness factors for / neutral / against / unknown with return against the index and
  the required return, and risks priced in the bear case vs still to price vs open questions. Derived only: no new
  threshold, no verdict change; the 14% aspiration is reported, never a threshold.
- Reinvestment: when the 3-year incremental sales-to-capital is below the type range only because revenue plateaued while
  capital kept rising, the 5-year ratio is used (Tesla: 0.62 had charged every new dollar of revenue $1.60 of capital and
  produced a funding 'gap' for a company with $43bn of liquidity).
- Identity check reads the currency of the latest annual report (Nebius: Yandex-era RUB history, USD now); the handoff
  resolves holdings outside the screened universe through the SEC ticker map; holdings runs write their own queue and
  comparison files (`--name holdings`).
- Biases found on the 491-company large-cap sweep (20.10.2026) and fixed before ranking anything:
  - moat proxy without a gross margin: networks and service companies that report no cost of revenue (Mastercard, Visa)
    got no pricing-power evidence and an unassessed-moat haircut; a high, stable operating margin is now the fallback
    evidence (level 20% -> 35%, range 8pp -> 3pp), labelled as such;
  - thin-margin established companies converged toward the type-typical margin they had never earned (McKesson, Cigna:
    3-8 invented points of margin); at the maturing, mature and decline stages the base now holds the company's own best
    margin of the last ten years, an analyst override with evidence lifts it;
  - the filer's own SEC SIC code now routes the type before the listing label: managed care filed as 'Medical
    Specialities' (Cigna, SIC 6324) is an insurer, not an operating company; banks, credit, broker-dealers, insurance
    carriers and REITs likewise.
- Acquired and divested revenue (found on DICK'S, FIS, Omnicom): each annual report's prior-year comparative is kept
  (`revenue_prior_comparable`) so growth is like for like across divestitures; pro forma revenue (ASC 805, companyfacts
  and the filings' inline XBRL per acquisition) separates bought from organic growth, the fade starts from organic growth
  and the acquired revenue is a level shift (DICK'S: +58% year to date, 2.7% organic; base value 434 -> 206).
- Cycles and margins: boom-then-bust revenue reads as a cycle; impairment years at established scale are not
  pre-commercial noise; cyclical growth fades from the long-run rate, not this year's price-driven change; cycle windows
  stop at a reorganization gap or a material acquisition; operating income is derived (pretax + interest) where the
  latest annual report has no subtotal (Genuine Parts: 15% typical margin invented before); a stable thin-margin company
  holds its own median margin (five profitable years inside a 5-point band), a company scaling through losses does not.
- New economic types with drivers, risks and margin profiles: healthcare_products (commercial pharma, devices, tools),
  healthcare_services, it_services (decided by gross margin: software or platform when the economics say so) and
  distribution; the filer's SIC decides wholesale and health services and is the fallback when no label matches.
- Banks and insurers valued (`forecast_financials.py`): residual income on common book equity less preferred stock and
  AOCI, ROE fading to the company's own median with part of the excess kept (more when it beat the cost of equity in 80%
  of seven or more years), payout at least what book growth the business can use leaves; regulatory capital stated as
  not checked; fields the route never reads (debt, cash flow) no longer block it (19 financials valued on 20.10).
- Discovery from every channel (`leads.py`, `channels.py`, `scripts/collect_channels.py`): EDGAR form-index events
  (13D stakes as leads, tender offers, late filings, spin-off and IPO registrations), 8-K items for all 2,470 filers,
  quarterly revenue acceleration from SEC frames, insider clusters, and parsers for Alpha Vantage news, estimates and
  earnings, LunarCrush tables, FMP targets and IBKR links; syndicated copies count once; red flags never support a card;
  `universe --channels` attaches them; a collection plan names each connector call and file.
- Deeper filings: segment view (the base case valued again with each segment at its own pace when the two paths differ
  by more than 2 points a year); convertible terms per instrument from inline XBRL (DoorDash, Pinterest, Collegium
  unblocked); the two-year refinancing wall from the annual principal maturity schedule; how management is paid
  (pay-versus-performance measures from the proxy statement, per-share or size orientation).
- Walk-forward test of the engine itself (`engine_backtest.py`, docs/BACKTEST_ENGINE_2026-10-20.md): no demonstrated edge
  (top fifth +7.6 points a year over the universe average, t 1.0; rank correlation 0.02); edge reliability (median yearly
  slope of realized on predicted edge, 0.40) shrinks every edge in allocation.
- End-to-end run (`runbook.py`, `fundamental_engine run`, `scripts/run_all.py`): research list, proposed book against the
  read-only holdings and the index, one Hebrew report; never an order.
- Fixes: float tolerance on liquidating the whole investment balance (5 crashes), an analysis KeyError (Ferguson), the
  Beneish disqualifier now needs the growth-neutral score and weak operating cash flow (NVIDIA's growth alone tripped it).
- `scripts/calibrate.py` (engine market return from a sweep: 6.28% over 351 of 491 companies, 81% of their value, against
  the 8.75% assumption), `scripts/rank_opportunities.py` (edge over that return with bear loss, quality, confidence,
  stage, disqualifiers and origin side by side; a research priority, not a recommendation) and `scripts/record_theses.py`
  (freeze casefiles into the forward ledger).

- Investments no longer vanish on a tag change: DebtSecuritiesCurrent/Noncurrent read as short/long-term investments;
  listed equity stakes (EquitySecuritiesFvNi) resolved as `equity_securities_fv` and valued once at fair value in the
  opening balance; a line present within the last 400 days but missing on the latest balance sheet fails
  `investment_line_continuity` and limits the cash field instead of reading as zero. Found on NVIDIA (fiscal 2027
  10-Q: about $77bn of marketable securities read as zero). Private stakes without a readily determinable fair value
  stay excluded (conservative; noted in the opening-balance rationale).
- review's opening check counts listed stakes once, like the forecast (otherwise a correct opening failed as overstated).
- `us-gaap:Depreciation` read as d_and_a after the combined tags (Alphabet tags only it; the forecast had used capex,
  16% of revenue, as the depreciation proxy instead of about 5%).
- The revenue-scale base rate bounds the first forecast year when the current year comes from a year-to-date surge
  (`growth_start_capped`); before, NVIDIA's +96% year to date started the next year at +73% while the warning claimed a 15%
  cap. Consensus, company guidance or an analyst override with evidence still replaces it.
- An analyst growth path is charged its own capital: raw `growth` overrides now recompute growth/maintenance capex with the
  engine's capital per unit of new revenue (`growth_capex_recomputed`) unless capex paths are overridden too, and the recorded
  reinvestment assumption shows the executed path. Before, the capex of the discarded engine path was kept (NVIDIA: a change
  to that discarded path moved the value by about $20 a share).
- Found on the ServiceNow valuation (20.10.2026): the issuer's own split tag (StockholdersEquityNoteStockSplitConversionRatio1,
  5-for-1 of December 2025) is now a dated corporate action for dataqa (direction checked against the counts, so a 1-for-10
  tagged as 10 reads as a reverse split; a ratio tagged over a whole year is not placed at year end). Before, the split
  stayed 'unresolved' and was counted as issuance: share growth 72.8% a year, growth quality 'weak' and dilution as the
  main risk in the paragraph. Weighted-average diluted shares straddling a split are restated by it. Consensus read from a
  public page (`web__consensus-estimates__annual.json`, `facts.from_consensus_table`) is recorded as consensus from a
  secondary source, dated at retrieval, when the vendor feeds are over quota.
- Discovery coverage: the screen read two revenue tags, so about 700 listed US companies above $200m never entered it
  (Valero and refiners report revenue including excise taxes, utilities tag regulated and unregulated revenue, brokers net
  of interest; also CrowdStrike, TJX, NextEra, SanDisk, Seagate, Western Digital). The frames now carry the facts layer's
  revenue tags plus utility revenue, and the alternative net income, cash-flow and capex tags; a filer whose newest annual
  frame is not published keeps the previous year's as its latest (flag `stale_frame`, history a year shorter, never a year
  counted twice). Universe 20.10.2026: 2,470 -> 2,673 issuers (`collect_universe.py --missing-only` fetched the 35 new
  frames). Still outside: banks (interest income is not operating revenue; they are valued through residual income), new
  listings without an annual report (tracked as pre-listing leads), preferred shares and notes.
- IFRS filers in discovery: `sec_frames.ifrs_frame_urls` / `merge_ifrs` read ifrs-full frames per currency, keep each filer's
  reporting currency (USD convenience translations dropped) and convert at the period-end H.10 rate. On 20.10.2026 the SEC
  served those frames for CY2021 only, so collection is opt-in (`--ifrs-only`) and foreign filers enter discovery through
  the holdings, the exploratory path or per-company collection; the valuation side handles them fully.
- Analyst sheet (`analyst_sheet.py`, a pipeline stage, first in report.md, one line per company in the run report): the
  owner's six questions answered the way an analyst reads a casefile, not as gates. (1) Is it a good business: the graded
  quality, growth, strength and capital allocation, red flags, and that a strong grade without an evidenced advantage is a
  result, not an explanation. (2) Is it mispriced: base value against the price and the growth the price needs
  (what_must_be_true); a strong business far above the model is read as a question about how long its advantage lasts, a
  cheap one as an opportunity or a trap until the market's fear is checked. (3) Geopolitics: revenue and long-lived assets
  in sensitive regions from the annual report (customers and production are different exposures; TSMC: 80% of assets at
  home, ASML: 29% of revenue from China), with the analyst's explicit scenario priced as a share of value. (4) Asymmetry:
  gain to the bull case against loss to the bear and permanent-loss cases. (5) Bottleneck: the moat evidence in numbers and
  what confirming it is worth in the model (`conditional_values`: the forecast rebuilt with an analyst's strong moat;
  Visa +38%, TSMC +21%). (6) Future relevance: which business lines grow fastest, contracted revenue, and what evidence of
  structural demand is still needed. Each question is evidenced, numbers only or open, with what would close it; analyst
  readings come in through inputs['analyst_sheet'] and close a question only with evidence ids.
- Business map: IFRS revenue axes (products, geography, segments) and long-lived assets by region (`assets_by_geography`,
  including company extensions for plant and equipment).
- Foreign issuers are valued in their own currency instead of being blocked (`fx.py`, facts, pipeline, handoff):
  - the latest annual report's monetary facts decide the reporting currency (USD convenience translations beside it are
    ignored); IFRS filers are read from the ifrs-full taxonomy with their own tag map (revenue, operating profit, cash flows,
    borrowings, share counts), and every monetary fact carries the reporting currency as its unit, quarters included;
  - the quote becomes the price of one ordinary share in the reporting currency: quote x the FRED H.10 rate dated on or
    within seven days before the quote / the ordinary shares per depositary share stated in the annual report
    (`business.ads_ratio`: numbers, words and share classes; a direct listing such as ASML is one for one). Without a dated
    rate or a stated ratio nothing is guessed: the quote is left and the forecast reports no quote in the company currency;
    a USD depositary share without a stated ratio withholds the forecast;
  - `handoff.identity_check` no longer blocks a non-USD reporter with a rate series; a depositary share needs the stated
    ratio (blocked when the report is not saved, states none or several); the listing-implied ratio is a cross-check;
  - `scripts/collect_companies.py --docs-when-needed` fetches the annual report for foreign, depositary and ambiguous-SIC
    issuers and the FRED series for the reporting currency (generic user agent: the SEC contact e-mail goes only to the SEC);
  - companyfacts can lag a filing (TSMC's 2025 20-F, filed 2026-04-16, was absent in October): the periods after the latest
    companyfacts year are read from the saved document's own inline XBRL under their own source (`facts.inline_payload`,
    `facts.from_annual_inline`);
  - IFRS cover share classes are summed (Novo Nordisk A and B shares had overwritten each other: blocked as a conflict);
    IFRS depreciation and amortisation tagged as two lines are summed (TSMC had used capex as the depreciation proxy); SAP's
    combined capex line is mapped; Alpha Vantage rows in another currency are skipped, never mixed.
- Cash flows are discounted in their own currency: a non-USD reporter uses its currency's long-term government yield
  (`fx.local_rate`, OECD monthly series on FRED: EUR from the Bund, DKK, JPY, GBP, CHF, ...) as the risk-free rate; without
  one the forecast keeps the USD default and raises `risk_free_currency` (high: a low-rate currency is undervalued by about
  the rate gap). The run compares such a company with the market return shifted by the same rate gap (interest parity), so a
  EUR or TWD return is not judged against a USD figure. TWD and CNY have no series on FRED yet: they keep the warning.
- Cyclical companies with a secular trend (found on ASML, 20.10.2026): the mid-cycle revenue had been the window mean moved
  to today at a growth rate bounded to 5% even inside the sample, which put ASML's 2026 revenue 25% under its 2025 level
  although nine years of 18% trend growth left 2025 below its own trend. Now, with at least six years at today's scale,
  the trend level at the latest year is the fitted value of the log-linear regression (in sample) and only the growth after
  it is bounded: it may exceed the 5% long-run bound only as far as the data support it with about 95% confidence (slope
  less two standard errors), within the revenue-scale base rate, fading to terminal. A noisy boom-bust history keeps the
  bound. Today's distance from the trend closes geometrically (a flat level blended with a fast trend had made a catch-up
  jump). The cycle's bull margin is capped at the type ceiling or the evidenced peak, whichever is higher (ASML's bull had
  collapsed onto the base under the 20% industrial ceiling). The moat proxy is no longer blocked for every cyclical: with at
  least six years of ROIC the lowest year of the last ten replaces the average (a franchise earns its excess return at the
  trough). ASML base value: 245 -> 525 EUR a share against a 1,462 EUR quote (still far below: no consensus, unknown moat).
- Generic labels no longer decide the business model: where no industry or SIC rule matches ('Services-Business
  Services, NEC' for Visa, Mastercard, Uber, DoorDash, MercadoLibre and Accenture; a sector label alone), the annual
  report's business section (Item 1) decides by what it talks about, per 10,000 words: payment-network, marketplace,
  defense, vehicle and IT/payroll-services vocabularies (`business.business_terms`, `model_card.BUSINESS_TERM_RULES`; set on
  34 annual reports, in sample: Visa and Mastercard -> platform, Booking, Airbnb, Uber, DoorDash, Etsy, MercadoLibre,
  PayPal -> platform, Accenture, Cognizant, EPAM, ADP, Paychex -> IT services, Tesla, GM, Ford -> hardware, Lockheed,
  Northrop, General Dynamics -> industrial; consumer brands and media stay). The sector type is kept as secondary; a specific
  label is never overruled by words. The collection step fetches the annual report for such companies.
- Collection kept losing documents: a re-collection that did not need a saved annual or quarterly report rewrote the
  manifest without its row, and the pipeline then ignored the file as undated (the holdings run had dropped them for
  Microsoft, Meta, Tesla, Palantir, ServiceNow, Amphenol, Reddit and Nebius). A saved document of the same filing now keeps
  its row; the eight manifests were restored from the SEC filing list (same accession, same period).
- Business type from the annual report's own words where the SEC code is ambiguous: a machinery or hardware label whose
  annual report mentions semiconductor/wafer/lithography at least 40 times per 100,000 words is typed semiconductor
  (equipment), the label kept as secondary (`business.industry_terms`, `model_card.SEMI_TERMS_DENSITY`; a heuristic, ASML has
  about 260).

- Research round 1 (POWL, GLBE, DECK, MELI, BCRX; `docs/learning_log.json`) found ten engine problems, now fixed:
  a split dated just after a count that the later filing had already restated (Powell's 3-for-1 of 2.4.2026 is in its
  31.3.2026 count) belongs to the step into that count, and a dated split counts only for a step it explains; SIC
  3610-3629 (switchgear, electrical apparatus) falls back to industrial; a noncurrent debt line under another tag that adds
  up with the current portion to the LongTermDebt tag settles the tag as the total (MercadoLibre's debt had been read as
  $17.1bn instead of $10.6bn, in dataqa and in the annual table); the release-bounded terminal ROIC of a shrinking
  scenario is capped at 100% (BioCryst's bear had crashed the valuation at 167%); `what_must_be_true` bisects to the
  edge of the valid domain when one end of a search is unfunded or invalid instead of reporting 'not reachable'; an
  evidenced analyst cycle treatment (`inputs.forecast.cyclical`) now also governs a cycle the model card read from the
  history (it was silently ignored for Powell), with a warning and a material issue for the reviewer, and the forecast
  reports the routed method.
- New evidenced forecast inputs for patterns the engine could not express: `nonrecurring` (one-offs of listed kinds -
  acquired IPR&D, impairment, gain or loss on sale, litigation settlement, one-time licence revenue, divested business in
  revenue - removed from the fiscal years, TTM and year to date the paths start from; restructuring and other recurring
  costs are refused), `debt_like_claims` (royalty financing, litigation, pension deficit, deferred consideration,
  preferred or minority claims added to opening debt) and `operating_funding` (borrowings that fund a lending book and
  whose interest is already in operating costs leave the bridge; a high warning until reinvestment is measured net of
  them). The opening reconciliation carries these as `debt_adjustments`; the review's opening check compares with
  validated debt plus them and asks the reviewer to accept them instead of failing, and the revenue-basis check accepts
  recognized revenue less evidenced one-offs.
- Splits after the last filing (Amphenol 2-for-1 on 3.9.2026, Monster 3-for-1): the post-split quote had been divided by
  the pre-split count, doubling or tripling value per share (APH showed $121 'attractive' instead of about $60). IBKR
  corporate actions (`ib__corporate-actions-<T>__current.json`, in the connector plan) become dated splits; a split dated
  after a count that was published before it restates the count (a count published after the split is already restated);
  the listing's market cap over its last sale, off from the count by a split factor with no dated split to explain it, makes
  the share count a conflict (blocked). `handoff.listing_price` now keeps the listing market cap. Of 536 casefiles of
  20.10.2026, two were affected (APH, MNST).
- Debt: LongTermDebtAndCapitalLeaseObligationsCurrent maps to current maturities, and the filer's own total including
  current maturities settles the scope of the long-term tag (Amphenol's $1.6bn current portion had been missed).
- Missing debt guard: interest expense above 25% of the debt read (and above 0.5% of revenue) raises `debt_incomplete`, a
  held material issue (decision.MATERIAL_HOLD): no value is shown and the company leaves ranked lists (held issues are now
  judgement disqualifiers). T-Mobile's long-term debt under issuer tags had produced a false +74% upside. Open from the
  same screen: noncontrolling interests are not yet deducted (Charter, Tenet) and fertilizer producers type as industrial.
- Market-consistent reading (owner's question: "how do I know the valuation is not too conservative?"). Measured on 330
  clean casefiles of 20.10.2026: 87% had a base value below the price, median -47%, because each company is discounted at
  the engine's own cost of equity (cap-weighted 9.68%) while the same engine finds the market priced at 6.87% (median
  6.68%). The absolute value answers "what to pay for my hurdle"; it is not "cheap or dear against the market".
  `calibration.market_implied` now records the engine's market cost of equity and the gap (`rate_shift`, 2.8pp) in the
  index input; with that index `decision` adds `valuation_view.market_consistent`: the base value with the company's cost
  of equity moved down by the same gap (relative risk kept, the market as a whole at fair value), the upside on it and
  `edge_vs_market` (base IRR - market implied return). Amazon at $262.5: value at its 9.75% cost of equity $138, market
  consistent $262, edge +0.1pp: priced like the market, not "40% overvalued". Long-duration values move 20-40% per point
  of discount rate, so the edge in return points is the steadier comparison. Verdicts keep their own hurdle.
- Learning loop (`learning.py`, `scripts/learning_report.py`): reads every research round's evidenced inputs and executed
  casefiles and reports where the analyst corrected the engine (lever, scenario, business type, direction, median move),
  the special inputs used, recurring warnings, engine fixes per company researched (from `docs/learning_log.json`) and the
  forecast ledger's scoring status. A lever moved the same way for three or more companies of one type is listed as a
  candidate default change; candidates are suggestions, never applied automatically.
- Research-based valuation review (owner: "don't invent; read the research"): docs/VALUATION_RESEARCH_REVIEW_2026-10-20.md
  records the sources read (Base Rate Book, Damodaran, McKinsey, Chan-Karceski-Lakonishok, Liu-Nissim-Thomas, Kaplan-Ruback,
  Frankel-Lee, Bernstein-Arnott, QMJ, Lev-Srivastava), how the engine compared and what changed:
  - market-neutral risk table: the business-risk add-ons only added and averaged about +1pp on the 481-company sweep, so the
    average company was discounted above risk-free + ERP (the ERP's own definition); `forecast.MARKET_NEUTRAL_ADJUSTMENT`
    (-1pp, its own visible row) removes the measured average and `calibration.market_implied` reports the value-weighted net
    adjustment after each sweep (`mean_risk_adjustment`; `pipeline.summary` carries `risk_adjustment`);
  - dated market inputs: `fundamental_engine deep --market FILE` passes a dated risk-free rate (USD reporters only) and ERP to
    every company that sets none (`handoff.with_market_inputs`); 20.10.2026: 10-year Treasury 5.15% (FMP, 19.10) and
    Damodaran's implied ERP 4.20% (1.10), replacing the undated 4.25% / 4.50% defaults in research runs;
  - excess returns persist: without a moat assessment the terminal ROIC keeps 50% (base) / 71% (bull) of the demonstrated
    excess over Ku (HOLT CFROI and margin spreads after ten years, Base Rate Book), never below the former Ku+2pp / Ku+4pp;
    the bear and an analyst 'weak' moat keep the full fade (`forecast.EXCESS_RETURN_PERSISTENCE`);
  - outside view: `base_rates.py` (Base Rate Book exhibit 4, sales growth by size class 1950-2015 in 2015 dollars; the
    $325-700m class rebuilt from its observation counts) gives every USD forecast `anchors.outside_view`, an
    `outside_view_growth` assumption and `growth_above_base_rate` / `growth_below_base_rate` warnings, and the price-implied
    five-year growth in `what_must_be_true` the share of companies of that size that grew as fast;
  - unchanged after review: growth fade (consistent with the research), terminal growth min(risk-free, 3%) (today's 2.9% real
    yield overstates nominal growth; incumbents trail the economy), margin competition for an unassessed moat. Flagged, not
    changed: the catch-up of below-typical margins is generous next to the base rates for established companies.
  Measured on the rerun of 491 large caps: net risk adjustment +0.93pp -> -0.06pp, engine market cost of equity 9.29%
  (rf + ERP 9.35%), market return implied by the engine's cash flows 6.96%, rate shift 2.81 -> 2.33pp, median value +6.9%,
  share below price 90% -> 85%; value-weighted, the base cases need growth only 24% of companies of the same size reached.
  Amazon: cost of equity 9.75% -> 9.35%, terminal ROIC 11.75% -> 15.4%, value $138 -> $161, market-consistent $275 at $262.5.
- Valuation table (owner, 20.10.2026: "every valuation as a clear table in simple words, calculations behind the scenes"):
  `render.hebrew_table` reads the executed casefile (price, fair value at the required return and the gap, bad/good
  scenario, expected annual return against the market's, five-year conditional value, business quality, how rare the
  forecast growth is, the main risk, confidence, the call) and adds short explanations, including why a stock can be dear at
  the required return yet cheap against the market; `deep` and `pipeline` write it as out/table_he.md. The analyst's main
  risk in plain words comes from an evidenced `analyst_sheet.key_risk`. Below it, one row per date of the annual path
  (today, each fiscal year end, the fifth anniversary) with the bad / likely / good value at the required return
  (`expected_return.yearly_at_required_return`, from the executed valuation at that rate; distributions shown apart).
- Private stakes: `inputs.forecast.non_operating_assets` [{kind private_stake | equity_method_stake, fair_value, haircut,
  book_value_in_bridge, reason, evidence_ids}] adds fair value x (1 - haircut) less what the bridge already holds to opening
  investments (valued at face), excluded from the net debt that sets leverage (a stake does not repay debt), reconciled for
  the reviewer (`investment_adjustments` in review's opening check). Microsoft: about 25% of OpenAI at its $852bn March 2026
  round, 25% haircut, less $24.6bn of non-marketable investments = $135bn added; plus its $66.6bn of finance leases as a
  debt-like claim and FY2027-28 consensus.
- Just-listed foreign ADRs (SK hynix / SKHY, full research): three fixes so a company whose only SEC structured data is the
  filing-fee exhibit and whose financials are a researched K-IFRS evidence bundle can be valued:
  - `facts.reporting_currency` returns None (not 'USD') when companyfacts hold no annual monetary facts, so the declared
    reporting currency (won) is kept instead of relabelling every figure USD (`pipeline.factbook_from_run_dir`);
  - the share-count listing cross-check multiplies the implied count by the ordinary-shares-per-depositary-share ratio before
    testing for a split: an ADS market cap no longer looks like a 10x split of the ordinary count (`dataqa`);
  - `inputs.forecast.capital_events` [{kind share_issue | share_buyback, date, cash, shares, reason, evidence_ids}] adds a
    share issue or buyback completed after the balance-sheet date and by as_of to opening cash (and shares when the
    point-in-time count predates it), listed as a bridge event for the reviewer (SK hynix's 14 July 2026 ADR listing:
    17.79m new shares for W39.9tn). SK hynix values as cyclical at a cycle peak (quality_but_expensive): mid-cycle fair
    value well below the price, the per-year bad/likely/good table showing mean reversion.

## 0.11.1 — verified review-fix cycle (20.10.2026)

Adversarial reviewers attacked every stage; a defect counted only when a script reproduced it. Each fix came with a
regression test that fails on the earlier code, and a second review attacked the repaired areas again. Counts below are
verified defects fixed: first review + verification of the repairs + follow-ups (review notes are kept outside the
repository). Tests: 641 at 0.11.0, 961 now (OK, 4 skipped). Software checks, not investment accuracy. Package metadata
(pyproject.toml, fundamental_engine.__version__) still reads 0.11.0, so casefiles record engine_version 0.11.0.

- facts / dataqa / analysis — 42 (3 found on real ADBE data + 9 + 29 of 30 + 1): bundle fiscal-year rows stay annual;
  dataqa takes facts.ttm's signed value (ADBE revenue_ttm was 61.1bn, now 25.97bn) with a `ttm_matches_facts` check; every
  SEC tag merged per period end; undated filings wait for their deadline (annual 90 days, Q4 rows the annual lag, foreign
  120/180); facts about periods ending after as_of are unusable; issuer documents vs secondary sources, independence by source
  family; splits need positive evidence (dated action, as-traded price bar, restated weighted shares) and split + issuance
  keeps the issuance; FY+YTD TTM checks; debt scope by roll-forward; capital-structure lines issued after as_of not read;
  negative cash or investments rejected as a data error (`liquid_assets_nonnegative`), never an opening balance.
- model_card — 6 (4 + 2 follow-ups) plus its share of the discovery verification below: industry text decides the type;
  fee networks and exchanges get an operating route; cyclical industry words are a flag, normalization needs data or the
  analyst (`inputs.cyclical`); evidence published after as_of is not evidence; a lender-shaped balance sheet under another
  label routes to residual income (`flags.lender_shaped`); a profitable company that needs outside money keeps the FCFF path
  with `valuation.funding_flag` instead of the survival model.
- screen / sec_frames — 7 in the first review + 38 of 38 in the discovery verification (shared with model_card; the record
  does not split them by file): owner yield (FCF - SBC) / market cap; priced-in on EV/NOPAT with a cross-check and a price
  check in every lens; cyclical peaks from data keep peak earners out of compounder/fair-price lenses; missing debt is
  unknown, never net cash; per-share funding discipline in the structural lens; loss-making growers can show traction.
- forecast / valuation — 39 (14 + 22 of 26 + 3): value independent of the quote (unlevered table rate relevered on the
  model's own equity, expected cost of debt, never the coupon); mid-cycle cyclicals and through-the-cycle margins; current
  year from YTD actuals; funding order investments -> SBC in shares -> debt within 3x EBITDA -> equity at min(quote, own
  value); investments and excess cash at face; reinvestment converging to g/ROIC; decline stage may keep declining; fifth
  anniversary always a valuation date; unknown moat only as a labelled, continuous proxy; case sources carry the document
  date and how it was read; a rate typed into the inputs is `analyst_input`, never a filing; the terminal debt weight is
  stored in the case and rate diagnostics move the terminal WACC by (1 - w).
- risk / review — 16 (8 + 2 + 6 follow-ups): only an accepted reviewer unlocks a price call (author statuses are notes);
  the review is bound to the valuation and to the analyst inputs (`inputs_sha256`, `rv['binding']`); self-review detected
  by normalized name; red-flag releases need filing documents beyond the flag's own evidence; error size is valuation-only;
  permanent-loss cases never milder than bear; pre-revenue and unclassified companies and the risk variants complete;
  `pricing_status` priced / partially_priced / not_priced / open_question / gate; valuation-error entries labelled; Hebrew
  descriptions for base-rate and what-must-be-true entries; a negative cash figure is never runway.
- decision / render — 50 (18 + 28 of 31 + 4; the other 3 were review.py items, fixed above): one required return, never
  below the model's own cost of equity; marginal band; IRR and values at a required return move the terminal WACC by the
  equity weight; upgrades cannot bypass a gate or lower the hurdle; crashed valuation is an evidence gap; funding gates only
  blocked or unreviewed financing (a reviewed priced path is valued with `conditional_on`); unresolved forecast material
  issues keep a named draft; the band is re-run without a computed moat proxy; register pricing status read, never upgraded.
- portfolio — 2 in the first review (account-like keys never become symbols; ETFs default to diversified) plus the
  economics/privacy verification (34 regression tests; the record gives no defect count): common factors separate from
  driver clusters, adds funded from cash then pro-rata sales, order-shaped payloads refused.
- monitor / ledger / mcp — 6: current fiscal year frozen and checked; one-year growth triggers; backdated recordings
  refused and late rows excluded from learning; events recorded after an outcome are post-hoc; MCP ledger paths confined
  to the corpus directory; thesis_score takes actuals.
- pipeline / cli — 3 (2 + 1): later-dated price, share count and consensus skipped instead of clamped to as_of; a draft
  verdict no longer reads as supporting a price in `summary`; `summary` carries decision_version, margin, hurdle and
  value_at_required_return. CLI: `python -m fundamental_engine.cli` works; inline JSON for --company and portfolio --options.

Still open: docs/LIMITATIONS.md (no index-beating evidence, SEC blocked, leverage drift deferred, price-history basis
not filled, 1:1 sensitivity grid, vendor quotas, no structured GOOGL/NVDA statements).

## 0.11.0 — one decision path from sources to verdict; discovery, portfolio and monitoring layers (20.10.2026)

- facts.py: FactBook, one schema with provenance and availability date for every number; adapters for FMP
  as-reported statements, SEC companyfacts (first filed, filed+2d), Alpha Vantage, IB prices, vendor share
  counts, consensus and researched evidence bundles; point-in-time accessors; TTM from quarters or FY + YTD.
- dataqa.py: eleven critical fields (verified / reported / single_source / conflict / missing / stale), accounting
  identities, cross-provider checks, debt-scope resolution by roll-forward, split-aware share continuity.
- analysis.py, model_card.py: ~35 traced metrics; business type, stage, funding, driver tree, valuation method by
  type x stage, understanding checklist evidenced only with dated evidence.
- forecast.py: bear/base/bull driver specs (guidance or consensus anchored, growth fade, margins after SBC,
  reinvestment linked to growth, explicit funding, cost of equity from a documented table), what-must-be-true.
- pipeline.py: collection plan, raw folder -> FactBook, one valuation route (drivers -> value_company + diagnostics),
  run() over all stages with failures recorded, summary().
- risk.py, review.py: disqualifying vs pricing vs accepted risks, permanent-loss and funding-failure cases,
  red-team package derived from execution, independent reviewer sign-off validated by re-running the audit.
- decision.py, render.py: four separate assessments, IRR at today's price vs index-based hurdle (14% only as a
  comparison), closed verdict set with gates, price for hurdle, governed overrides; markdown report and Hebrew
  paragraph.
- screen.py, sec_frames.py: discovery lenses (quality compounder, quality at a fair price, early commercial growth,
  mispriced/turning, structural) on eight separate dimensions; whole-market SEC frames adapter.
- portfolio.py: read-only IBKR book, concentration, economic exposure overlaps, scenario stress, review triggers,
  candidate vs holdings vs index vs cash options (never orders).
- monitor.py + ledger v2: thesis records, material-only alerts, frozen forecasts with system timestamps,
  error attribution; no backdating, no forecasting the past.
- Hardening of earlier gates (redteam precedence, margin of safety, archetype match, hypothetical financing,
  red-flag gate, stage override reason, driver defaults, Alpha Vantage availability).
- CLI: pipeline-plan, pipeline, screen, portfolio. MCP: collection_plan, run_pipeline, screen_universe,
  screen_changes, portfolio_analyze, portfolio_compare, thesis_record/check/freeze/score.

## 0.10.0 — expectation gap, capital bridge, red-team, point-in-time validation (19.10.2026)

- capital.py: instrument-level capital structure (accreted converts, rational conversion fixed point,
  pre-funded warrants, post-statement events, prepayments kept out of net cash). Golden NBIS fixture.
- redteam.py: independent audit of a valuation package; critical failure = valuation UNVERIFIED.
- expectations.py / expectations_vec.py: one driver model (revenue -> EBIT after SBC -> reinvestment
  -> FCFF), reverse-solve one lever at a time, detects when growth destroys value.
- pit.py / inflection.py: point-in-time SEC companyfacts (first filed value, filed+2d), Q4 derivation,
  multi-class cover shares; acceleration, incremental margins, acquisition flag, stage, transparent scorecard.
- ledger.py: append-only hash-chained forecast ledger.
- scripts/: pit_build, pit_panel, gap_backtest (pre-registered), case_suite, gap_card, gap_explore (exploratory).
- MCP tools capital_bridge, redteam_audit, expectation_gap, forecast_ledger.
- Results of the pre-registered universe test: docs/GAP_TEST_RESULTS.md. Calibration is not a return edge.

## 0.9.2 — honest confidence, driver specs and reverse diagnostics (from the NOW run)

- `sources[].retrieval` and `underwriting_audit.source_access`: confidence drops when
  opening balances rest on primary sources that were not read directly.
- `discount_rate_band` (±1pt) and `implied_cost_of_equity`; confidence drops when a
  one-point change flips cheap/expensive or moves value by more than 25%.
- The concise paragraph now names the main confidence reason.
- `driver_version: 1` specs: `build_valuation_from_drivers` (MCP), `value-drivers`
  CLI, `implied_growth_shift`; fictional `examples/valuation/software_drivers.json`.
- `prepayment_price_linkage` (scaled/fixed): price stress and reverse sensitivity no
  longer break deferred-revenue ledgers; reverse search restricted to funded domain.
- NOW regression input and run notes (`docs/NOW_RUN_2026-09-28.md`); dated connector
  observations appended to session_capabilities. No accuracy claim.

## 0.9.1 — concise decisions and potential underwriting

- Default plain/MCP/CLI text is one Hebrew investment paragraph; full detail retained.
- Separate current-value discount from upside and dated five-year conditional price.
- Withhold approved values for demo, unresolved review and funding blocks.
- Traceable short analyst notes connect potential, risk and milestones to observations.
- Add SOFI lessons on economic inference, flow/stock, segment/margin definitions,
  financial-sector capital and discounted future targets to existing brain packets.
- This improves process and delivery, not a claim of validated forecast accuracy.

# v0.9.0 — valuation integrity and AXTI regression

Separates calculation success from permission to publish a price conclusion. Adds
opening assets/share bridge, investment ledger, comparable-consensus review,
terminal maturity review, exact reproduction input and executed dossier binding.
Corrects AV cash/share definitions and AXTI point-in-time dilution. Removes default
scenario probabilities, momentum valuation overrides, and fixed five-year 25x claims.
Preserves Claude v0.6–v0.8 classification, recent-quarter and forensic work with
regressions. No replacement AXTI target or investment-accuracy claim.

## Historical release notes (superseded where v0.9 changes behavior)

# Changelog

## 0.3.0

Research operating system for Claude's existing data connections. Adds on-demand
protocols/playbooks, evidence-led dossier supervisor, counter-thesis gates, named
valuation basis, investigation-loop controls, promise tracking, append-only research
memory, seven MCP tools and installed-package verification. See START_HERE.md.

## 0.2.0

Business/event analysis, symmetric normalization, financing-aware ownership model,
growth-linked reinvestment, exact-concept SEC periods, source corpus, stdio MCP,
Hebrew reports and three historical primary-source examples.

## 0.1.0

Deterministic annual financial analysis, DCF/reverse DCF, emerging-potential evidence,
funding-gap simulation, SEC transport, immutable run snapshots and Hebrew report.

## 0.4.0 — 2026-09-27

Annual value paths and dedicated model routes. Adds operating-driver revenue,
prepayment/cash/debt/share reconciliation, explicit financing and SBC alternatives,
terminal FCFF-to-equity bridge, annual current-holder values, funding gates,
residual-income capital checks, NAV/rNPV/SOTP snapshots, asset-cohort schedules,
stress/sensitivity/reverse-price tools, frozen forecast scoring, Hebrew report,
CLI and MCP integration, and primary methodology research. 50 new tests; 143 total.
All shipped new valuation examples are fictional. No calibrated issuer targets or
predictive-accuracy claims are included.

## 0.5.0 — 2026-09-27

Theme-first discovery, causal bottleneck paths, payer/value-capture checks, adoption
stages, comparable progress metrics, milestones, evidence revisions and independent
discovery/valuation lanes. Adds three MCP tools, discover CLI, dossier integration,
two research packets and a fictional unprofitable early-adoption case. 35 new tests;
178 total. No trading, automatic surveillance or calibrated winner probabilities.

## 0.6.0 — 2026-09-27

Eye-level Hebrew verdicts. `plain_verdict` (MCP), `plain` CLI and the plain_language
packet turn reported history into good/not-good labels with everyday explanations,
sector-aware margin bars, stage-aware treatment of losses and cash burn, separate
quality/growth/strength/price axes, a plain bottom line, and price versus executed
bear/base/bull value in words through 2030. Synthesis now leads with this layer.
11 new tests; 189 total. Research indication only; no orders or promised returns.

## 0.7.0 — 2026-09-27

Company typing (Damodaran life cycle + Lynch), acquisition and cycle detection,
Piotroski/Altman/Beneish/ROIC/Rule-of-40 scorecards, forecast base-rate check,
Alpha Vantage statement mapper, master_process/company_type/forensic packets, three
MCP tools, classify and import-av CLI, stage-weighted plain verdict. Real Broadcom
regression example. 14 new tests; 203 total. Screens direct research; no return odds.

## 0.7.1 — 2026-09-28

Turning points. Optional `recent_quarters` (AV mapper fills them) let company typing
and plain verdicts see inflections or slowdowns that annual history hides; a recent
quarterly profitability item; `current_shares` catches issuance after the last annual
report; a price above even the bull value now yields a red bottom line. Found while
running AXTI; real AXTI regression example added. 3 new tests; 206 total.

## 0.8.0 — 2026-09-28

Two valuation lanes. Intrinsic (numbers-dominant, e.g. Nvidia) versus potential
(young/unprofitable/inflecting). Optional executed `tail` scenario in value_company;
probability-weighted payoff; expectation momentum (acceleration, margin expansion,
beats/raises, estimate revisions, gross margin); analyst-style multiples view;
reverse revenue requirement; lane verdicts that allow a premium only with strong
momentum. growth_valuation packet, expectations_momentum MCP tool, point-in-time
Palantir momentum case, AXTI tail case. 9 new tests; 215 total. No return prediction.
