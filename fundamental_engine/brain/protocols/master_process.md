# Master research process (best-practice synthesis)

A compact order of work drawn from widely used professional methods and from comparing a strong human analyst's
write-ups with the engine (docs/ANALYST_COMPARISON_2026-10.md, 25.10.2026). Each step names its origin or tool so
the method stays inspectable; the packets hold the detail. One company at a time: every number is the company's own,
read from its own reports, not a rule applied to all companies alike.

1. **Identity and data** — issuer, share class (ADR ratio, dual listing premium), currency, fiscal calendar,
   cutoff. Pull 5 annual years + the latest quarters through the user's connectors (session_capabilities: AV/FMP
   structured first); reconcile material numbers to the filing. Read the latest 10-K/10-Q (or 20-F/6-K), the
   earnings release and the call transcript yourself: the numbers and what management said they mean.
2. **Company type** — `classify_company` (Damodaran life cycle; Lynch categories). The type chooses the
   questions, the metrics and the valuation lens (company_type): grower, compounder, cyclical, turnaround,
   financial, regulated, platform, early stage.
3. **The economic identity** — how the money is made, written as an identity with the company's own KPIs
   (economic_models packet, `econ_review`): accounts x revenue per account (broker), gross bookings x take
   rate (marketplace), customers x revenue per customer + NDR (software), bits x ASP (memory), GW x revenue per
   GW (compute), subscribers x ARPU, earning assets x NIM (bank). Split growth into structural, cyclical, macro,
   mix and reclassification; only the cyclical part may revert. Segment economics separately when margins differ.
4. **Who funds the demand** — vendor financing, guarantees, take-or-pay, equity stakes in customers,
   receivables and inventory against revenue, customer concentration and the funding chain behind the largest
   customers (demand-quality overlay). Firm exposure and contingent ceilings are kept apart.
5. **Sector position and bottlenecks** — what scarce thing the company supplies, who needs it, how long the
   shortage lasts and who adds capacity (frontier_discovery / frontier_research). A bottleneck story needs paid
   adoption and pricing in the company's own numbers, not partner logos.
6. **Outside view before inside view** — start the forecast from base rates for firms of the same size and stage
   (Kahneman; Mauboussin's Base Rate Book; McKinsey growth fade), then depart from them only with evidence.
7. **Guidance and its track record** — the company's guidance, compared on the same basis with what it then
   delivered (quarter_review guidance_track): a conservative guider's guidance is a floor, not a forecast; a
   missed guide is evidence. Attributed consensus where available.
8. **Moat and returns** — ROIC level, stability and incremental ROIC; which moat source is evidenced
   (Morningstar); the moat as a repeatable process where it is one (new categories renewed, intensity vs growth);
   returns fade toward the cost of capital unless a mechanism prevents it.
9. **Earnings quality and forensics** — one-offs removed AND added back symmetrically after tax; recurring
   "one-offs" stay; SBC is a cost and acquisition share awards are dilution; restricted cash and insurance
   reserves are not liquidity; cumulative KPIs are not activity; Piotroski/Altman/Beneish screens.
10. **Management, capital allocation and financing** — promises vs delivery, incentives, buybacks vs SBC,
    acquisition returns, runway, maturities and who funds growth (management, funding).
11. **Market and geopolitical context** — rates, implied ERP, credit spreads, volatility (market_context);
    country risk by where the company sells and makes (Damodaran CRP); evidenced exposures (Taiwan supply, China
    export controls, tariffs, sanctions, regulation) become bear mechanisms sized from the evidenced share.
12. **Multiples, the company's own and the market's** — the company's own P/E, EV/EBITDA, P/FCF or P/B range
    through a full cycle and where it sits now; what the market pays for businesses with the same growth and
    margin today. A multiple is anchored in evidence, never just today's multiple carried forward; a record
    cyclical quarter x 4 is peak earnings, not a run rate.
13. **Valuation by type and the yearly path** — annual_valuation: today's value and every year through the fifth,
    bear/base/bull, the model fitting the type (DCF with fade, normalized cyclical margins, residual income, NAV,
    rNPV). The bear changes the business (growth, margin, funding), not only the multiple. In the owner's words the
    scenarios are דובי / בסיסי / שורי. Show the base range (טווח הבסיס: the base at the required return ±1pp)
    separately from the extremes, and the measured error band (render.hebrew_table). Every scenario price carries its
    condition behind the scenes (scenario_reasons, out/scenarios_he.md): what has to happen and that year's business;
    events need evidence ids (analyst_sheet.scenario_events), else they are hypotheses.
14. **Expectations** — reverse DCF: what today's price already assumes (Rappaport & Mauboussin); where and why
    our view differs and what evidence will settle it.
15. **Pre-mortem and counter-thesis** — assume the investment failed in three years and list why (Klein;
    adversarial). Numeric falsifiers with thresholds, re-checked every quarter (quarter_review falsifiers).
16. **Verdict and memory** — the owner's Hebrew table and short explanations (render.hebrew_table,
    plain_language). Freeze the base case in the forecast ledger next to any analyst's dated target for the same
    company, so accuracy is judged later on real prices; research_checkpoint with the 3–5 observations that would
    change the view.

Guardrails learned from the analyst comparison (do not copy these habits): a target that moves with the price;
one EPS path x three multiples presented as bear/base/bull; adjusted EPS that hides SBC; insurance reserves counted
as cash; a minor revenue line carrying the thesis; volatility confused with business risk.

Stop rule: finish when the next retrieval would not change the decision; record the remaining uncertainty rather
than searching indefinitely.
