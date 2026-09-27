# Company type decides the research (life cycle + Lynch)

Identify the type BEFORE choosing questions, metrics or a valuation model. A young
growth company and a mature compounder such as Broadcom fail and succeed for
different reasons; applying one checklist to both produces confident nonsense.

## Execute

Call `classify_company` (same input as plain_verdict) once 3–5 years of history are
available; `plain_verdict` runs it automatically. It returns:
- Damodaran life-cycle stage: start_up, young_growth, high_growth, mature_growth,
  mature_stable, decline.
- Lynch category: fast_grower, stalwart, slow_grower, cyclical, turnaround, asset_play.
- Flags: acquisitions (goodwill jump > 10% of prior assets, or supplied
  acquired_revenue), cycle peak/trough, short history.
- What decides value, key metrics, valuation fit, traps and packets to load.

The rules are coarse. Override with `type_override: {stage, lynch, reason}` when the
evidence supports it (e.g. a segment-level cyclical inside a stable group, a spin-off,
a regulated monopoly). Hybrid businesses: research each economic engine with its own
playbook and value them separately (SOTP) when economics differ materially.

## Stage by stage

**Start-up / young growth.** Narrative dominates numbers. Test: paying customers
(not pilots or logos), repeat purchase, gross margin on real sales, cost to acquire
and keep a customer, runway, and the dilution each funding round costs today's
owner. Valuation: explicit scenarios including failure, funding and dilution;
reverse DCF to see what the price requires. Earnings multiples are meaningless.

**High growth.** The question is duration. McKinsey (Koller et al.) finds firms
growing >20% real typically slow to ~8% within five years and ~5% within ten; most
"supergrowers" do not sustain it. Mauboussin's base rates: start from the growth
distribution of companies of the same size, then justify any departure. Test
incremental margins, ROIC on new capital, competition attracted by high returns,
organic versus acquired growth. Valuation: driver DCF with explicit fade to maturity.

**Mature growth (compounders, e.g. Broadcom).** Value lives in durable excess
returns and capital allocation. Test the moat (Morningstar's five sources:
intangibles, switching costs, network effects, cost advantage, efficient scale) with
evidence — pricing after increases, retention, share, ROIC stability over a cycle.
Test acquisitions: price paid versus returns earned, integration, leverage after the
deal, organic growth of the combined business. Test cash use: reinvestment,
dividends, buybacks net of stock compensation. Valuation: FCF DCF fading to GDP-like
growth; cross-check FCF yield and reverse DCF.

**Mature stable.** Test cash generation, coverage of dividends and buybacks by FCF
(not debt), slow moat erosion hidden by buybacks, and whether the price pays a growth
multiple for no growth. Valuation: stable-growth FCF/dividend model; FCF yield
versus bond yields.

**Decline.** Temporary or structural (load revenue_decline)? Cash that can still be
harvested, debt maturities versus shrinking cash flow, liquidation/asset value.
Never a growth terminal value. Classic value trap: low multiple on shrinking earnings.

**Cyclical (any stage).** Latest-year earnings reflect the cycle position. Normalize
over a full cycle (Damodaran: average margins over 5–10 years applied to current
revenue, or sector averages). A low P/E at peak margins is a trap; a high P/E at
trough can be cheap. Balance sheet must survive the trough.

**Turnaround.** Survival first: liquidity, covenants, maturities. Then evidence of
operational change (costs actually removed, customers returning), not promises.

**Asset play.** Verify asset values independently, discount for taxes/costs to
realize them, and check whether management will unlock or waste them.

## Output requirement

State the type and why in one plain sentence, what decides value for this type,
and which checks were therefore prioritized. Carry the flags into the counter-thesis.
