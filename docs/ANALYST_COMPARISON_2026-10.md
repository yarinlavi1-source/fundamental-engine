# Human analyst versus engine: results-day comparison (2026-10-21)

The input was seven community posts by one analyst, supplied by the user. They
were written between July and August 2026 and cover UBER Q3-25, RDDT, SN, SKHY Q2-26,
PLTR Q2-26, SOFI Q2-26 and IBKR/TSMC Q2-26. I read them against what the engine
could do at v0.9.1 (the branch this comparison started from; main has since moved to 0.11.x). Figures in `examples/quarter/` are taken from those posts
and have **not** been reconciled to filings. They are teaching inputs for the
tool, not evidence or targets.

## Gap found

Six of the seven posts are results-day reviews of one report. The engine was
built around annual history, valuation paths and dossiers, and had no deterministic
tool for one quarter. v0.12.0 adds `quarter_review` (MCP and CLI) plus the
`quarter_review` packet, routed by the research-plan trigger `earnings_event`.

## What the analyst does well, now built in (v0.12.0)

| Analyst habit | Example | Engine v0.12 |
|---|---|---|
| Strips one-off gains from net income | UBER tax release and stake marks; SKHY investment gains; TSMC Vanguard sale; PLTR SpaceX mark | Symmetric after-tax normalization; explicit tax rate; recurring items kept; `headline_distorted` flag |
| Opex growth versus revenue growth, excluding an unusual item | UBER legal charge | Reported and ex-unusual spread, incremental margin. The unusual charge is also added back to normalized net income, so the treatment stays symmetric |
| Watches which segment really earns | SOFI lending versus technology platform | Segment growth, margin change, share of profit, concentration flag |
| Questions KPI definitions | SOFI cumulative "products" | KPI `basis`; cumulative KPIs flagged as non-activity evidence |
| "Price moves on the gap versus expectations" | IBKR, UBER, SOFI held EPS guidance | Guidance compared with prior guidance and attributed consensus; `reinvesting_upside`; `no_expectations_data` blocks overreaction claims |
| Guidance credibility | SN guided 5–7% and delivered 30% | `guidance_track` on the same basis; rates compared in percentage points; small-sample warning |
| Bank lens: P/TBV against ROTE | SOFI at 2.15x TBV with an 8–9% ROTE | Justified P/TBV = (ROTE−g)/(COE−g), the ROTE the price requires, excess capital, marks/equity |
| Dual-listing arbitrage | SKHY ADR about 25% over KOSPI | Home-market equivalent price and premium flag |
| Capex cycle depresses FCF | TSMC | `investment_phase` flag; FCF definition stated |
| Rate sensitivity against volume growth | IBKR: 25 bp is about $81m | `sensitivities` with share of base and the growth needed to offset |
| Explicit thesis breakers | SN: innovation cost, gross margin, retailer power | `falsifiers` status: intact, weakening or broken |
| Customer concentration | SN top 3 = 45.7%; PLTR top 20 about 40% | Concentration flag |

## What the engine deliberately does not copy

| Analyst move | Why it is unsafe | Engine behavior |
|---|---|---|
| Annualizes a 76%-margin memory quarter ×4 and calls P/E 7.5 cheap (SKHY) | Peak-cycle earnings | `peak_margin_run_rate`. With the post's own multiples, the 6x case implies about −20%/yr |
| Counts about $12bn of insurance reserves as "liquidity" (UBER) | Restricted; backs claims | Kept outside spendable liquidity; net cash is −1.7bn on the post's figures |
| Calls 6.6bn the "adjusted" net income | That figure still includes the one-offs | Normalized ≈ 0.87bn under explicit tax assumptions |
| Multiples "pulled out of thin air" (PLTR 55/65/75x, SOFI 20/25x) | Unanchored sensitivity | `unanchored_multiples`, implied annual return, optional discounting |
| "I'll sell if hype takes it to 200–220 this quarter" | Momentum and price prediction | Not modeled; no price forecasts or orders |
| "Odds the Google negotiation collapses approach zero" (RDDT) | Certainty without evidence | Kept as an unresolved hypothesis in the dossier; no default probabilities |
| Personal product anecdotes as demand evidence (RDDT watch, SN blender) | Sample of one | Not evidence; use cohort, retention and paid-adoption data |
| Volatility as the reason not to own (SKHY) | Suitability, not business quality | Kept on a separate axis from quality and price |

## Results on the post figures (tool arithmetic, unverified inputs)

- SKHY Q2-26: normalized NI is about 31.3bn against 64.1bn reported, assuming a 24%
  tax rate on the gains. The operating margin is 76% against a 29% historical
  median, which flags peak earnings. The ADR premium is +24.5%.
- SOFI Q2-26: at COE 10% and g 4%, the justified P/TBV is 0.75x against 2.11x
  paid. The price requires a ROTE of about 16.7% against 8.5%. Excess capital
  over a 13.5% target is about 2.6bn. Fair-value marks are about 19% of equity.
- UBER Q3-25: about 87% of reported NI is one-off. Opex excluding legal grew
  about 12.5% against revenue up 20%. Adjusted EBITDA rose 33% while operating
  income rose 5%.
- SN FY25: guidance beat in 2 of 2 years (small sample). The top-3 customers
  are 45.7% of sales. The R&D-intensity and gross-margin falsifiers are intact.

These checks change how the engine reads a report. They do not show that the
engine's investment conclusions are better. No forecast-accuracy claim is made.
