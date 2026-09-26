# Build status — v0.3.0 — 2026-09-26

## Implemented

Retains v0.2 financial/event/ownership analysis, source corpus, SEC normalization,
Hebrew reports and executable MCP. Adds:

- A research supervisor that routes the client's existing connectors into a staged
  research plan with on-demand methods and eight economic archetype playbooks.
- A structured evidence dossier distinguishing facts, guidance, hypotheses,
  opinions and assumptions; date/source gates and same-definition numeric conflicts.
- Required business mechanism, material-claim review, counter-thesis and falsifiers.
- Actual financial-input execution with identity/provenance checks; price conclusions
  require a named executed valuation basis and cannot bypass financing/sector limits.
- Original management promise/outcome tracking with matched economic definitions.
- Recorded search budgets and repeat/unproductive-query detection; unresolved
  questions remain unresolved when retrieval is exhausted.
- Append-only local research revisions, optimistic concurrency, resume/history and
  material-claim/gate comparisons across revisions.
- Seven additional MCP tools and matching CLI commands.
- START_HERE.md, compact CLAUDE.md, 23 packaged brain packets, synthetic dossier,
  independent-install package check and adversarial process evaluation protocol.

## Verification actually performed

- **93 unit/integration tests passed locally**, including the previous 65 tests.
- **12/12 synthetic adversarial process cases passed** in scripts/evaluate_brain.py.
- Subprocess MCP: initialize, discover tools, analyze, corpus operations, dossier
  review, checkpoint revisions, comparison and resume.
- A built wheel loaded all **23 packets** and executed dossier review outside the
  checkout using an isolated Python process.
- v0.2's three historical issuer examples remain regression cases. Their inputs
  were manually reviewed, and no live quote/forecast valuation was fabricated.

These checks validate selected engineering behavior. They do not measure a model's
semantic source comprehension, source truth, causal inference, investment returns
or performance relative to humans. benchmarks/README.md specifies the separate
held-out Claude evaluation that is still needed in the user's environment.

## External boundaries

No connection to the user's private Claude session, Interactive Brokers account or
other live client integrations was available to this build process. The architecture
reuses those existing connections rather than replacing them; compatibility with
that exact session must be exercised there. No credentials or private holdings
were requested, copied to the repository or used in public fixtures.

No server-side paid model calls or broker order tools. No autonomous scheduler.
Specialist financial-sector/biotech/SOTP valuations, full tax/options/FX/stub-period
models and verified real-time extraction remain outside the implemented calculator.
The brain explicitly routes or withholds those conclusions rather than faking support.

The latest GitHub CI result is recorded in GitHub Actions, separately from this
local verification record.
