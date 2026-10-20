# Analyst sheet: answering the owner's six questions

The engine fills the numbers (`analyst_sheet.py`); the analyst closes the questions with read, dated evidence. Work like a
good analyst, not a checklist: weigh mixed signals, say what would change the view, and never let a number stand in for a
question that was not read. Every reading separates reported fact, management guidance, third-party opinion and your own
hypothesis. Pass closed readings in `inputs.json` under `analyst_sheet` with evidence ids; an unsourced reading is kept as
opinion and does not close the question.

## 1. Is it really a good business?

- Returns on capital above the cost of capital for many years, and still above it in the worst year of the cycle. High
  returns fade for most companies (base rates of ROIC persistence); the question is what stops the fade here.
- Name the source of the advantage: intangibles (brand, patents, licences), switching costs, network effects, cost
  advantage (scale, process, location), efficient scale. If none can be named, a strong grade is a result, not a moat.
- Cash backs the profit (cash conversion, accruals), the balance sheet survives a bad year, management allocates capital
  well (returns on acquisitions, buybacks at sensible prices, dilution).
- Pitfall: a great past with an eroding position (substitutes, a technology shift, a regulator). Check the trend, not the
  level.

## 2. Is the market pricing it below its worth?

- Start from the price, not from your forecast: what growth, margin and duration does it require (what_must_be_true,
  reverse DCF)? Then ask which of those you have evidence against.
- Find the market's fear in one sentence (competition, regulation, cycle, a customer, technology, geopolitics) and judge
  whether it is temporary or structural, with evidence. Mispricing usually comes from a fear that is overdone or a duration
  that is underestimated, rarely from a market that "has not noticed" a large company.
- A strong business far above the model is mostly a question of how long its advantage lasts; a cheap stock is an
  opportunity or a trap until the fear is checked (value traps: leverage, secular decline, accounting).
- Compare with consensus estimates when available: agreeing with consensus is not an edge; a variant view needs a reason
  and evidence the market can see later (a milestone, a filing, a price change).

## 3. Geopolitical exposure

- Three exposures, separately: customers (revenue by region), production and supply (where plants, fabs, suppliers are;
  single-country dependence), and rules (export controls, sanctions, tariffs, subsidies, forced localisation).
- Write one explicit scenario for the material exposure: what happens, what share of revenue or production stops, for how
  long, what it costs, what mitigates it (capacity elsewhere, inventory, customer alternatives). Price it as a share of the
  base value (`value_impact`). Do not average it away into the discount rate unless you say so.
- Use dated primary sources (filings' risk factors, government rules, company capacity plans); news is a lead.

## 4. Asymmetry

- Define the bear case around the main fear, not as a generic haircut; define the bull case from evidence, not hope.
- Read gain to bull against loss to bear and to permanent loss. A favourable ratio with a bear that ignores the main fear
  is not asymmetry.
- Scenarios are not probabilities. If you state odds, give their basis; the engine never invents them.

## 5. A bottleneck?

- Map the chain: the scarce resource (capacity, technology, licence, data, network), its units and geography, who owns it,
  installed versus announced supply, lead time to replicate it, substitutes and workarounds, and who captures the
  economics (frontier_discovery and frontier_research protocols).
- Evidence of pricing power: price increases without volume loss, gross margin held through a downturn, backlog and lead
  times, customers funding capacity, share stable or rising.
- Duration: customers' incentive to second-source, competitors' roadmaps, antitrust or political limits. A bottleneck
  with a known expiry date is worth less than one without.
- When proven, record the moat as an analyst rating with evidence (card dimensions) and rerun: the sheet shows what that
  confirmation is worth in the model.

## 6. More relevant in the future?

- Name the structural driver (computing demand, electrification, ageing, digitisation, security) and the link from the
  driver to this company's paid revenue: the parts of the business that grow, customers that pay, contracted revenue.
- Sales growth decays toward the economy's for almost every company (growth base rates); long high growth needs an
  explicit reason. Size the opportunity from the bottom up (customers, units, price), not from a market-size headline.
- Look for what could stop it: substitution, efficiency gains that cut demand, saturation, regulation.

## Closing

Write the thesis in one paragraph: what kind of business, what the price assumes, the main fear and why it is or is not
overdone, the asymmetry, the conditions it depends on and what would change your mind. Keep potential, quality, price and
portfolio fit separate. A sheet with open questions is a research agenda; a price conclusion still needs the independent
review.
