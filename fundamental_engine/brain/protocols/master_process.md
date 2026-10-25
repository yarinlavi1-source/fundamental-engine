# Master process: the ticker skeleton

When the owner sends a ticker, walk this skeleton for THAT company, in order, until it is understood in depth and
valued by what will most likely happen. The default is the fast procedure (CLAUDE.md "On a ticker", about 20 minutes for a
new ticker): `scripts/ticker_prefetch.py` does the deterministic part in seconds, then the main loop runs four gather lanes
in parallel, one analyst and two reviewers, each following its stage file (`python -m fundamental_engine ticker-stage
<stage>`, fundamental_engine/brain/ticker/). The saved workflow `.claude/workflows/ticker.js` (Workflow name `ticker`, args
`{ticker, as_of}`) runs the same phases more slowly and thoroughly (one agent per phase) and stays the fallback; by hand,
follow the same phases. One company per ticker: every driver is built from that
company's own evidence (its guidance adjusted by its own track record, contracted demand, attributed consensus, its own
history) or from NAMED direct competitors with the reason each one is a competitor. Base rates and other companies'
numbers are at most a warning row. A lever without company evidence stays a visible gap (wider range, lower
confidence), never another company's number. Market prices of risk (risk-free, ERP, credit spread, country risk) stay.

Who runs which step in the fast procedure (every stage file carries the rules of the phases it runs):

| Runs it | Steps | Writes |
|---|---|---|
| `ticker_prefetch` (script, no LLM, seconds) | 1 (CIK, filings list, company.json skeleton, reporting currency), 2 (computed type and forensic screens), the SEC documents and companyfacts facts of Phase 2, the FRED risk-free and the saved ERP | `prefetch.json`, `identity.json`, `companies/<T>/raw/`, `documents/`, `reading/`, `gather/_prefetch.json`, `market.json`, `macro/fred/` |
| `gather_numbers` agent | 1 (identity check: security, share class, exchange, quote currency; the stop rule), Phase 2 statements reconciled, price (IBKR READ-ONLY), own multiple history, attributed consensus, guidance track record | `gather/numbers.json`, identity and company files |
| `gather_documents` agent | Phase 2 reports, release and transcript; current guidance; KPIs, segments, customers; risk factors | `gather/documents.json` |
| `gather_field` agent | Phase 2 named competitors and their numbers; bottlenecks; share trajectory (inputs of 5 and 12) | `gather/field.json` |
| `gather_macro` agent | Phase 2 market and geopolitics: rates check, geography, exposures, news (inputs of 11) | `gather/macro.json` |
| `analyst` agent (one) | 3-15: Understand, Position, Value (pack-merge, pack-to-inputs, deep), and the one repair pass | `understand/`, `position.json`, `evidence_pack.json`, `companies/<T>/out/` |
| `review_numbers` + `review_counter` agents (parallel) | 16: trace and company-first; counter-thesis, peak multiple, events | `review/numbers.json`, `review/counter.json` |
| the main loop | 17: the owner's table, explanations, sources; ledger freeze | `deliver/` |

Each phase has an entry rule (what must exist before it starts), the steps, the required outputs (files under
`runs/tickers/<TICKER>_<as_of>/`, gitignored) and an exit rule (what must be true before moving on). A failed exit rule
is written down as a gap and the work continues on the other branches; it is never papered over.

## Phase 1 — Identify

Entry: a ticker and an information cutoff (as_of). Nothing else is assumed.

1. **Identity and data** — issuer, listed security and share class (ADR ratio, dual listing), exchange, currency,
   fiscal calendar, CIK, the latest 10-K/10-Q (or 20-F/6-K), release and call dates. `pipeline-plan <T> <as_of>` lists
   the structured connector calls and file names.
2. **Company type** — `classify_company` (Damodaran life cycle; Lynch category): grower, compounder, cyclical,
   turnaround, financial, regulated, platform, early stage. The type chooses the questions, the KPIs and the valuation
   lens (company_type); it never chooses a number.

Outputs: `identity.json` (identity, type with reasons, fiscal calendar, filing list), `companies/<T>/company.json`.
Exit: issuer, security, currency, fiscal year end and as_of are resolved and sourced; an unresolved identity stops the
run (wrong security = wrong everything).

## Phase 2 — Gather (parallel; connector order per session_capabilities)

Entry: Phase 1 exit met. Connector order: Alpha Vantage / FMP structured data first; Interactive Brokers for prices
READ-ONLY (never orders, alerts or watchlists); SEC EDGAR and issuer IR for primary documents; WebSearch/WebFetch for
the rest. A denied or empty call is recorded once with its fallback, never retried in a loop. Every item carries a
source registered per sources_registry (kind, date, independence; syndicated copies are not independent).

Gather lanes (each writes `gather/<lane>.json`, a fragment of the evidence pack): statements and raw payloads per
pipeline-plan; reports, release and transcript (read them: numbers and what management said they mean); guidance and
its track record (each guided period vs what was delivered, same basis); attributed consensus (with n, low, high);
KPIs, segments and customer concentration; named competitors and their numbers (why each is a competitor); sector
bottlenecks and potential; market and geopolitics; own price and multiple history.

Outputs: the raw folder `companies/<T>/raw/` (verbatim payloads, MANIFEST) and one fragment per lane.
Exit: the latest annual and quarterly numbers reconcile to the filing; guidance and its track record are recorded or
marked missing; at least the direct competitors named in the 10-K are listed or the gap is said; every lane lists its
gaps and stop reasons.

## Phase 3 — Understand

Entry: the Gather fragments.

3. **The economic identity** — how the money is made, with the company's own KPIs (economic_models, `econ_review`):
   customers x revenue per customer, units x ASP, bookings x take rate, earning assets x NIM. Split growth into
   structural, cyclical, macro, mix and reclassification; only the cyclical part may revert.
4. **Who funds the demand** — vendor financing, take-or-pay, customer concentration and the funding chain behind the
   largest customers; firm exposure apart from contingent ceilings.
5. **Sector position and bottlenecks** — what scarce thing the company supplies, who needs it, how long the shortage
   lasts, who adds capacity. Paid adoption and pricing in the company's own numbers, not partner logos.
6. **Base rates as a warning only** — the outside view (base_rates.outside_view) is shown next to the forecast as a
   warning row when the company's path departs from it; it never sets, caps or fades a number.
7. **Guidance and its track record** — the guided midpoint vs what was delivered (quarter_review guidance_track); the
   ratio actual / guided midpoint is recorded in the pack and applied explicitly where the analyst says so. A
   conservative guider's guidance is a floor; a missed guide is evidence. The fiscal year in progress is
   `drivers.current_year_revenue` (its guided midpoint; the ratio applied to the unreported remainder only, e.g. Q4);
   `growth_anchor` is the growth of the NEXT fiscal year and states its `period`; a guidance- or consensus-tier value
   sits inside the cited item's range for that year or states its deviation. Results day: a release or call dated less
   than two days before as_of (facts.LAG_DAYS) is not citable yet; prices and macro feeds are citable on their date.
8. **Moat and returns** — ROIC level and incremental ROIC; which moat source is evidenced; returns fade toward the cost
   of capital unless a mechanism the company shows prevents it.
9. **Earnings quality and forensics** — one-offs removed and added back symmetrically; SBC is a cost; restricted cash
   and reserves are not liquidity; Piotroski/Altman/Beneish/ROIC/Rule of 40 (`forensic_scores`); every flag goes to the
   counter-thesis.
10. **Management, capital allocation and financing** — promises vs delivery, incentives, buybacks vs SBC, acquisition
    returns, runway, maturities and who funds growth.

Outputs: `understand.json` (identity with numbers, growth split, demand funding, quality flags, moat evidence,
management record), each claim with evidence ids.
Exit: the identity reconciles to reported revenue within its stated residual; structural vs cyclical is decided with
evidence or labelled unknown; every forensic flag is carried forward.

## Phase 4 — Position (the forward judgement)

Entry: Understand outputs.

11. **Market and geopolitical context** — rates, implied ERP, credit spreads (market_context); country risk by where
    the company sells and makes; evidenced exposures (export controls, tariffs, Taiwan supply, sanctions, regulation)
    become bear mechanisms sized from the evidenced share.
12. **Position: potential beyond the data** — what will happen and what could happen: market direction; bottleneck
    potential (could it become one, for how long); share trajectory against NAMED competitors (who is taking share,
    on which numbers); competitive position (stronger or weaker than each competitor, on what); threats (substitution,
    in-sourcing, a new entrant, regulation). Each reading is evidenced with ids or labelled `hypothesis`. Potential
    moves the forecast only through an evidenced lever (a driver with a source tier); a hypothesis moves nothing.
13. **Scenarios from events** — base = the most likely path by the company's evidence (not a conservative case); bull =
    what must happen (specific, dated, evidenced events); bear = what breaks (specific mechanisms: a customer loss sized
    by concentration, a program loss, a cycle turn, a funding gap). Each scenario has events with evidence ids and
    numeric falsifiers (metric, threshold, direction, date) re-checked every quarter.

Outputs: `position.json` (the five potential readings, the three scenarios' events, drivers with source tiers, the
forward multiple path per scenario with its justification, key risk, falsifiers).
Exit: every driver that differs from the engine's rule names its tier and evidence; every competitor used is named with
why; bull and bear are business changes (growth, margin, funding), not only a different multiple.

## Phase 5 — Value

Entry: Position outputs.

14. **Multiples, the company's own and the market's** — the company's own forward P/E (or EV/EBITDA, P/FCF, P/B)
    range through a cycle and where it sits now; named competitors at a comparable stage. A multiple is anchored in
    evidence and labelled `justified_high`, `peak` or `unproven`; a record quarter x 4 is peak earnings, not a run rate.
15. **Valuation by type and the yearly path** — merge the fragments into `evidence_pack.json` (`pack-merge <run dir>`), convert it (`pack-to-inputs` or the MCP
    tool `evidence_pack_to_inputs`: inputs.json with source-tier overrides, company_path, scenario events, key risk,
    card assessments, market inputs; the evidence bundle registers the sources), run the engine for that one company
    (`deep <run dir> --symbols <T>`). Today's value and every year through the fifth, bear/base/bull (annual_valuation);
    the company price path = forward multiple x next-fiscal-year EPS per scenario (company_path); scenario reasons for
    every price (scenario_reasons, `out/scenarios_he.md`, the `scenario-reasons` CLI / MCP tool): each company-path price
    with its EPS drivers, multiple, tier, events and evidence ids, then the cash-flow model values; the owner's table
    shows ONE yearly table (the company price path when computed, else the model value per year); events need evidence ids (analyst_sheet.scenario_events), else they are hypotheses. The bear changes
    the business (growth, margin, funding), not only the multiple. The base range (טווח הבסיס) is the base at the required
    return +/-1pp, shown apart from the extremes.

**Reasons behind every price.** No price stands without its reason. Owner example: price 200 today; base 300, bull
350, bear 200 in five years. Why 200 in the bear — what breaks (which customer leaves, which margin falls, by how much,
on what evidence). Why 350 in the bull — what must happen (which program ramps, which share is won from which named
competitor, which margin is reached). Why 300 in the base — the most likely path and the evidence that makes it most
likely. Each yearly price stores its drivers (revenue, margin, EPS/FCF, shares, the multiple and why that multiple),
the events, the evidence ids and the source tier.

Outputs: `evidence_pack.json`, `companies/<T>/inputs.json`, `companies/<T>/evidence_bundle.json`, `companies/<T>/out/`
(casefile, table_he.md, scenario reasons).
Exit: the engine ran without stage errors, or each error is recorded and the result shown as a draft; the values in
the table are exactly the executed ones.

## Phase 6 — Review (adversarial)

Entry: the executed casefile.

16. **Expectations and pre-mortem** — reverse DCF: what today's price already assumes (expectation_gap); assume the
    investment failed in three years and list why (adversarial, redteam_audit). Check: every number traces to a
    source; no default from another company (the gaps row lists what is still on an engine rule); guidance vs its
    track record; a peak multiple is not paid as justified; the strongest counter-thesis is written.

Outputs: `review.json` (findings with severity, fixes made, what stays open).
Exit: no critical finding open; a critical finding makes the valuation UNVERIFIED and it is shown as a draft.

## Phase 7 — Deliver

Entry: Review exit (or the draft label).

17. **Verdict and memory** — the owner's Hebrew table (render.hebrew_table): דובי / בסיסי / שורי, today and years 1-5,
    a few short simple explanations, and the sources as links when the web was used. The full reasons file stays behind
    the scenes. Freeze the base case in the forecast ledger (thesis_freeze / forecast_ledger) with its falsifiers, so
    accuracy is judged later on real prices.

Outputs: `deliver/table_he.md`, `deliver/reasons.json`, the ledger entry.
Exit: the table matches the executed casefile number for number; future values are dated conditional values, not
promised market prices.

Guardrails (do not copy these habits): a target that moves with the price; one EPS path x three multiples presented as
bear/base/bull; adjusted EPS that hides SBC; insurance reserves counted as cash; a minor revenue line carrying the
thesis; volatility confused with business risk; a peer median used as if it were the company.

Stop rule: finish a lane when the next retrieval would not change the decision; record the remaining uncertainty
rather than searching indefinitely.
