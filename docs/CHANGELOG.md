## 0.11.2 (in progress, 20.10.2026)

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
- Business type from the annual report's own words where the SEC code is ambiguous: a machinery or hardware label whose
  annual report mentions semiconductor/wafer/lithography at least 40 times per 100,000 words is typed semiconductor
  (equipment), the label kept as secondary (`business.industry_terms`, `model_card.SEMI_TERMS_DENSITY`; a heuristic, ASML has
  about 260).

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
