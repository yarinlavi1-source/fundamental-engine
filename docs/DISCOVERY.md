# Emerging-opportunity discovery — v0.5

The discovery track is designed to reduce premature rejection of emerging companies
WITHOUT presenting early hypotheses as buy signals. It does not demand earnings,
FCF, low multiples or a completed valuation. Price/funding models remain independent.

## Workflow

1. `frontier_plan({request:{theme,as_of}})` — investigate a theme, not just tickers.
2. Read frontier_discovery/frontier_research; use the client's existing connections.
3. Build and run `discovery_scan({case})`; CLI: `fundamental discover INPUT.json`.
4. Test the weakest causal edge and next commercial milestone. Track comparable
   operating metrics. Run `discovery_compare({before,after})` on later evidence.
5. Underwrite business/funding/annual valuation; do not wait for mature EPS to begin.

## Case contract

Required: discovery_version=1, company_id, ticker, as_of, sources, nodes, edges,
observations, opportunities. Optional: trajectories, freshness_days (default 365),
is_demo. Monetary valuations and suitability are outside this case.

Sources: id/title/url/kind/origin_id/published_at/available_at. Synthetic sources
require is_demo=true. Future sources are excluded; duplicate IDs are rejected.
Origin IDs identify original provenance, not number of syndicated articles. Their
count is descriptive, never an evidence score or independence guarantee.

Nodes: id, description, kind=driver/constraint/solution/beneficiary/substitute.
Edges: id, from, to, mechanism, condition, falsifier, kind=observed/hypothesis,
reviewed (boolean), lag_months, as_of, source_ids. Lag is an analyst assumption,
not an automatically predicted bottleneck date. Each opportunity uses a contiguous
noncyclic edge path from driver to beneficiary. Alternative paths may share nodes.

Observations: id, role, statement, location, direction=supports/opposes,
kind=reported/guidance/opinion, reviewed, observed_at, reviewed_at, source_ids.
Roles: technical, pilot, design_win, paid_adoption, production, repeat, scaling,
unit_economics, value_capture, demand, supply, substitute, funding. Only dated,
reviewed reported observations promote evidenced stage. Guidance/opinions remain
hypothesis material. Review labels are supplied by the analyst, not verified by AI
arithmetic. Stale observations are exposed; change freshness explicitly with a
reason when an older fact is durable, instead of claiming new progress.

Opportunity: id, problem, payer, product, capture_mechanism, substitutes,
supply_response, why_now, falsifier, path (edge IDs), observation_ids, milestones.
Nonempty descriptions may explicitly say unknown plus the concrete verification
question; do not invent a budget owner to satisfy the input contract.
Milestone: due_at, test, success, failure, action_if_success, action_if_failure.

Optional pressure_cases: case=low/base/high; kind=observed/scenario; date, unit,
scope, demand, supply, rationale, source_ids. Both quantities must already be
normalized to matching units/scope by the analyst. Demand/supply and shortfall are
arithmetic checks; a future-date observed gap is rejected. Supply=0 returns a null
ratio rather than infinite confidence. No shortage probability is inferred.

Optional trajectories: metric, definition, unit, scope, better=higher/lower,
points with date/value/period_days/unit/scope/source_ids. Definitions are shared by
the series; scope/units mismatch is rejected; materially unequal period lengths
cannot produce an improvement label. Changes are descriptive, not causation or
acceleration estimates. Seasonality and comparability still need analyst review.

## Output

Research lanes: explore; monitor_progress; underwrite_early_growth;
resolve_counterevidence; repair_evidence_path. All remain research opportunities.
Underwrite_early_growth requires paid/production/repeat/scaling evidence plus
value-capture support; it does not require profit. A design win alone is not paid
adoption. Counterevidence is kept alongside supportive observations. Current path
availability and hypothetical links remain explicit. Unit economics require more
than a standalone pilot margin to be described as demonstrated at scale.

Output includes dated source/observation references, pressure cases, next questions,
milestones, trajectories, input hash and exclusions. No watchlist monitoring job is
scheduled automatically. CLI saves immutable-by-input JSON under runs/discovery/.

`research_review` accepts discovery_input and checks issuer/date/source identity
against the dossier. Existing priced-conclusion gates remain unchanged; failure of
a pricing gate does not erase the separate discovery result.

## Research and examples

frontier_research documents IEA/NIST sources, the NBER productivity-J-curve abstract
and explicit access limits. ALAB-type connectivity and NBIS-type cloud infrastructure
are question maps, not current recommendations or verified price targets. The
shipped example is a fictional unprofitable agent-security supplier. It cannot be
used to claim a particular security company will benefit from AI.

## Evaluation boundary

Tests deliberately check false-negative risks (losses/no DCF/pilots retained) and
false-positive risks (endorsements, guidance, duplicate articles, future evidence,
contradictions). They do not demonstrate the ability to pick the next market winner.
That needs frozen historical cohorts, failures/delistings, matched vintage data,
held-out outcomes and both recall and false-positive reporting.
