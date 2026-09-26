# Review before implementation

Baseline: 6d02436ae613565cf1cfe863837a03a9ac6a0afb (v0.1.0).
The existing deterministic DCF, dated evidence, SEC exact-tag selection, immutable
snapshots and escaping are retained. Existing tests remain regression gates.

Material gaps found:

1. No event-level distinction between reported disruption, management attribution,
   recovery evidence and structural damage. No symmetric normalization bridge.
2. Funding gaps do not affect ownership or valuation. Reverse DCF holds absolute
   reinvestment fixed even as the growth rate changes.
3. No business-driver model, commercialization stages or investigation priorities.
4. Annual-only research input; quarterly helpers not integrated with SEC extraction.
5. CLAUDE.md is instructions, not an executable integration.
6. Only synthetic end-to-end data; no primary-source case studies.

Implementation proceeds in a separate branch. A rule-based engine can audit
structured evidence and do mathematics; it cannot independently establish causality
or turn management assertions into verified facts. This boundary is explicit.
