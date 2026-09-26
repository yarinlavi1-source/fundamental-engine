# Build status — v0.2.0 — 2026-09-26

## Implemented and locally verified

- Existing v0.1 functionality retained, with 27 regression tests.
- Event evidence/status, symmetric normalization, source/date exclusions.
- Business drivers, commercialization stages and investigation priorities.
- Growth-linked DCF/reverse DCF and explicit ownership/financing scenarios.
- SEC exact-concept annual/quarter/YTD/TTM normalization with provenance/conflicts.
- Local source corpus, deduplicated dated retrieval, TXT/MD/PDF import path.
- Executable stdio MCP server with subprocess client integration test.
- Hebrew HTML reports, three manually reviewed real historical fixtures, synthetic
  integrated financing/business/event fixture, updated documentation.

Local test suite: **65 tests passed**. Meaningful checks include known-answer
valuation, retained cash versus dividends, debt tax/interest/repayment, dilution
and financing failure, recurring costs and exceptional gains, future evidence,
quarter derivation/conflicts, source immutability, provider 403/429/cache behavior,
MCP lifecycle and end-to-end tool calls, and selected primary-source numeric checks.

Three real CLI runs successfully produced HTML, JSON and immutable SQLite snapshots.
Synthetic integration also ran successfully, demonstrating funding with dilution
and a funding gap without the hypothetical raise. The real cases withhold valuation
because market prices and reviewed forecasts are not supplied.

## Not verified or not implemented

- SEC live HTTP transport: no SEC_USER_AGENT contact configuration supplied.
  Provider behavior tested with mocks; no bypass or synthetic data fallback.
- User's Claude installation/account: not configured or exercised. MCP tested
  using a local subprocess client, not a live Claude conversation.
- PDF success on a real document and browser visual/cross-browser QA: not claimed.
- End-to-end automatic company extraction, live prices, research automation,
  dedicated sector valuations, full options/tax/FX/stub-period support: unfinished.
- Event causality, forecast accuracy, source truth and investment performance:
  not established by software tests. No alpha or 99% completeness claim.

GitHub branch: feature/business-events-financing-mcp. Remote CI is checked separately
from these local results; this document records local evidence only.
