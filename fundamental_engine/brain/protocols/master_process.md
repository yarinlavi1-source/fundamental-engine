# Master research process (best-practice synthesis)

A compact order of work drawn from widely used professional methods. Each step names
its origin so the method stays inspectable. The existing packets hold the detail.

1. **Identity and data** — issuer, share class, currency, fiscal calendar, cutoff.
   Pull 5 annual years + latest quarters through the user's connectors
   (session_capabilities: AV/FMP structured first). Map AV statements with
   `import_statements`; reconcile material numbers to filings.
2. **Company type** — `classify_company` (Damodaran life cycle; Lynch categories).
   The type chooses the questions, metrics and valuation model (company_type).
3. **Business machine** — how money is made, from whom, and the 2–3 variables that
   dominate economics (business; sector playbooks; several for hybrids).
4. **Outside view before inside view** — start forecasts from base rates for firms
   of similar size and stage (Kahneman's outside view; Mauboussin's Base Rate Book;
   McKinsey growth fade), then argue for departures with evidence.
5. **Moat and returns** — ROIC level, stability and incremental ROIC; which of the
   five moat sources is evidenced (Morningstar); returns fade toward the cost of
   capital unless a mechanism prevents it (Mauboussin).
6. **Earnings quality and forensics** — cash conversion, accruals, SBC, capitalized
   costs, acquisitions; Piotroski/Altman/Beneish screens (forensic, earnings_quality).
7. **Scuttlebutt** — evidence from customers, suppliers, competitors, ex-employees,
   channel data and job/pricing signals, not management alone (Fisher). Treat as
   observations with sources, not as rumours repeated.
8. **Management and capital allocation** — track promises versus delivery
   (research_controls), incentives, dilution, acquisition returns (management).
9. **Financing** — runway, maturities, who funds growth and what it costs today's
   owner (funding).
10. **Valuation by type** — annual_valuation with bear/base/bull through 2030; the
    model fits the type (DCF with fade, normalized cyclical margins, NAV, rNPV,
    residual income). Diagnostics and forecast base-rate check.
11. **Expectations** — reverse DCF: what the current price already assumes
    (Rappaport & Mauboussin, Expectations Investing); variant perception: where and
    why our view differs from the market, and what evidence will settle it.
12. **Pre-mortem and counter-thesis** — assume the investment failed in three years;
    list the most likely causes (Klein's pre-mortem; adversarial). Define falsifiers
    and kill criteria before concluding.
13. **Plain verdict** — plain_verdict and the plain_language writing rules.
14. **Checkpoint and monitoring list** — research_checkpoint; the 3–5 observations
    that would change the view, with dates.

Stop rule: finish when the next retrieval would not change the decision; record the
remaining uncertainty rather than searching indefinitely.
