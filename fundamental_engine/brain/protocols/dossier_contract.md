# Dossier input contract — case_version 1

Complete executable example: examples/research_dossier_demo.json (SYNTHETIC).
The dossier is a local working artifact, not a file that must be committed to Git.
The historical company examples under examples/real use the older financial-input
contract; do not pass them directly as dossiers.

## Top-level fields

- case_version: 1; is_demo: boolean; as_of: YYYY-MM-DD research cutoff.
- identity: company_id, ticker, name, currency. Verify legal issuer and security.
- question: research objective; archetypes: one or more of software, platform,
  industrial, consumer, infrastructure, financials, biotech, conglomerate.
- sources[], observations[], claims[], business_model, adversarial.
- Optional financial_input: the existing analyze_company JSON (schema_version 1).
- Optional candidate_conclusion, management_promises[], research_log[], search_budget.

Sources: id, title, locator, kind, origin_id, published_at, available_at. Real primary
kinds: filing, earnings_release, regulator, official_statistics. Other sources can
be commentary/research/transcript but cannot alone establish a reported fact via the
primary-fact gate. Do not mislabel a transcript as a filing to pass the gate.
Synthetic sources require is_demo=true. Financial and dossier source IDs must refer
to identical locator, dates and kind. Add actual retrieved_at separately.

Observation: id, source_id, statement, location, review_note, kind, observed_at,
reviewed_at, reviewed (boolean). kind: reported_fact, management_statement,
analyst_inference, opinion. The review note states what was checked, not 'verified'
as an empty assertion. location is page/table/paragraph or equivalent.
Optional numeric: metric, value (finite number), unit, scope, basis, period_end,
optional period_start. A future guidance period is allowed for management statements;
future realized facts are not. Different definitions must not be averaged together.

Claim: id, statement, kind, dimension, materiality, support_ids[], challenge_ids[],
reviewed_at, optional falsifier. kind: fact, management_guidance, hypothesis, opinion,
assumption. materiality: critical, high, medium, low. dimension: identity, business,
accounting, demand, moat, management, potential, funding, valuation, expectations,
risk, catalyst. IDs reference observations. The same observation cannot both support
and challenge the same claim. Critical/high hypotheses need meaningful falsifiers.
The relationship is analyst-reviewed: code cannot tell whether a citation's text
actually entails a claim. Guidance remains guidance even if well sourced.

Business model: offering, payer, customer, revenue_drivers, cost_drivers, capital_needs,
competition, mechanism and claim_ids. The first seven must be nonempty; mechanism
explains how cash is earned and claim_ids anchors that explanation in evidence.
Adversarial: mechanism, decisive_test and claim_ids. A generic risk paragraph without
linked evidence is insufficient.

Candidate conclusion classification: unresolved, watchlist, conditional_attractive,
not_attractive. A price judgment additionally needs rationale and valuation_basis:
`{"model":"ownership","scenario":"exact executed scenario name"}` (or legacy_dcf).
The selected scenario must support the price direction. A legacy DCF cannot bypass
an ownership model, a funding gap or a required dedicated-sector valuation.
Acceptance for synthesis is not endorsement of the recommendation.

## Original management promises

Each: id, guidance_observation_id, outcome_observation_ids[], expected, direction
(at_least/at_most), due_at. Guidance must be a reviewed management_statement with a
numeric threshold exactly equal to expected. Actuals require a reviewed primary
reported_fact with the same metric/unit/scope/basis/economic period. Keep revisions
as separate promises; do not rewrite the original. Missing data is not a miss.

## Investigation attempts

research_log records id, question_id, connector, query, completed_at,
outcome (new_evidence/no_new_evidence/access_blocked). question_id is the claim_id
or topic from next_actions. Default search_budget: max_actions=24,
max_unproductive_attempts=3. Bounds are configurable integers 1..256. Logs are
caller-reported; the engine cannot count invisible connector calls. On exhaustion,
checkpoint the blocker rather than claiming the question was resolved.

## Persistence

research_checkpoint accepts case, optional case_id and expected_revision (0 for new).
It saves immutable content and the actual review; stale writers fail instead of
silently overwriting work. research_load returns full case/review. research_history
lists checkpoints by company_id. research_compare reports claim and gate changes.
MCP research_review returns compact calculation summaries; analyze_company or a
loaded checkpoint includes the complete financial audit. No external account writes.
