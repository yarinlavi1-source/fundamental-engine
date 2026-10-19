# Dated connector map — Yarin's Claude/Cowork session

Last probe reported by user: **2026-09-27**, live calls against **AXTI**.
Provenance: user-supplied report of another session. This repository update did NOT
execute these connector probes, inspect their raw responses, or independently
verify subscription tiers. Applies to that Claude session, not every installation.
Reconfirm tool presence on a new session and recheck after plan/connector changes,
contradictory results or material staleness. Do not discard this map and repeat all
known access failures on every dossier. Tool names below describe the reported
capabilities; resolve the actual exposed tool/schema before calling.

## Standing collection rule

For each dossier in this session, collect structured numeric observations from
Alpha Vantage / FMP as available BEFORE generic WebSearch. IB is preferred for
security-specific prices. WebSearch/WebFetch remain appropriate for qualitative
context, original filings/notes and data unavailable through the current plans.
Original issuer/regulator documents resolve definition conflicts and material
footnotes: collection priority is not a claim that vendor data is more authoritative.
No automatic purchase or request to upgrade a plan.

## Interactive Brokers

Reported connected, existing technical-engine use: search_contracts and
get_price_history. Quotes/history preferred for live security data; verify contract,
exchange, share class, currency, timestamp and adjustment basis. Reported exposed:
read-only positions/balances, watchlists, alerts, get_company_themes and
get_company_connections. Exposed does not mean each capability was probed against
AXTI. Theme/connection tools can start frontier_plan before choosing a ticker;
their output is a discovery lead, not evidence of commercial value capture.
Portfolio use remains read-only. Do not create/modify watchlists or alerts under a
research request without the required user authorization. No orders.

## FMP — lower-tier plan, per reported probes

| Capability | Reported status | Operational treatment |
|---|---|---|
| company / profile-symbol | Working | Identity/profile and quote-equivalent data |
| quote | Working in supplied routing table | Resolve exact quote vs company tool; timestamp and cross-check |
| secFilings | ACCESS DENIED | Skip on current plan; use SEC/IR |
| analyst targets/grades | ACCESS DENIED | Use AV overview where available |
| statements | ACCESS DENIED | Probe AV statements; otherwise issuer filings |
| news | ACCESS DENIED | AV news if available, otherwise web/IR |
| earningsTranscript | Report says Ultimate/Enterprise only; not live-confirmed here | Treat unavailable until proven otherwise |
| form13F | Report says Ultimate/Enterprise only; not live-confirmed here | Treat unavailable until proven otherwise |
| discountedCashFlow, senate, insiderTrades, calendar, ESG, technicalIndicators | Untested | Unknown; one bounded capability probe when relevant |

Tier labels are the user's dated report, not a universal/current pricing claim.
An ACCESS DENIED is a plan limit in this reported session, not evidence of a broken
engine. Retry only after a relevant change or evidence the failure was transient.

## Alpha Vantage

Reported connected, with no plan restriction encountered in these probes.

- CONFIRMED WORKING in the user's session: COMPANY_OVERVIEW, including fundamental
  fields, valuation ratios, AnalystTargetPrice, rating buckets and ownership %;
  INSIDER_TRANSACTIONS with executive/date/price detail; INSTITUTIONAL_HOLDINGS.
- UNTESTED / UNKNOWN: EARNINGS_CALL_TRANSCRIPT, CONGRESS_TRADES, NEWS_SENTIMENT,
  BALANCE_SHEET, CASH_FLOW, INCOME_STATEMENT, EARNINGS_ESTIMATES, technical indicators
  and macro series (CPI, GDP, unemployment, policy rates, Treasury yields).
  The user's expectation that they share the tier is not confirmation of access.

INSTITUTIONAL_HOLDINGS can be large: persist a bounded raw artifact outside Git and
extract a relevant digest using jq/Python. Preserve filing/holding dates, managers,
changes and exact references. Do not dump the entire payload into the context or
dossier. Delegation is optional only when authorized by the active environment.

## LunarCrush

Reported connected; not tested in this session. Social/sentiment, primarily
crypto-oriented according to the report. Low default priority for equity
fundamentals. Use only for a specific sentiment question; never as accounting data.

## Routing by need

| Need | Preferred collection | Legitimate fallback |
|---|---|---|
| Identity/profile | FMP company/profile-symbol or AV COMPANY_OVERVIEW | Web/issuer identity |
| Annual/quarterly statements | AV BALANCE_SHEET/CASH_FLOW/INCOME_STATEMENT: probe, unknown | FMP only if plan permits, then issuer filings/IR/web |
| SEC filings | FMP secFilings only if plan permits; currently blocked | SEC EDGAR/IR via web |
| Insider transactions | AV INSIDER_TRANSACTIONS | FMP insiderTrades: unknown |
| Institutional holdings/13F | AV INSTITUTIONAL_HOLDINGS, digest first | FMP form13F only if availability confirmed |
| Congress/Senate transactions | FMP senate or AV CONGRESS_TRADES: unknown | Record gap; no inference of trades |
| Earnings transcripts | AV EARNINGS_CALL_TRANSCRIPT: unknown | FMP only if access confirmed; official IR transcript/webcast |
| Analyst targets/ratings | AV COMPANY_OVERVIEW | FMP analyst only if plan changes |
| Provider DCF | FMP discountedCashFlow: unknown | Engine's own valuation tools |
| News/sentiment | AV NEWS_SENTIMENT: unknown | FMP only if plan permits; web/IR |
| Live price/history | IB, existing contract/history workflow | FMP quote/profile-equivalent, cross-check |
| Theme discovery | IB get_company_themes/get_company_connections if exposed | Primary industry/technical sources and frontier_plan |

A provider DCF is attributed external model output, not reported intrinsic value.
It can be collected first as requested; the engine still computes its own explicit
annual scenarios. Analyst targets/ratings are opinions/estimates, not facts about
fair value. Overview ratios may use different dates/share counts: reconcile them.
Ownership and transaction filings may lag the positions/trades they describe;
13F is not a live portfolio, and an insider transaction is not automatically a
voluntary open-market purchase. Read transaction codes and reporting scope.

## Probe and provenance discipline

Record capability state available/unavailable/unknown with session, exact tool,
probe time, request class, outcome/error and reason. A transient rate limit differs
from an entitlement denial. For unknown relevant endpoints, try one small permitted
request; record outcome and switch to a fallback. Never invent successful probes.
Do not buy subscriptions or work around access controls.

Every numeric observation retains original origin_id/document or filing reference,
retrieved_at separately from available_at/published_at, economic period, unit,
currency, scope, basis and review note. Map evidence to actual dossier-contract
kinds: reported financial data, guidance, analyst opinion or inference as applicable.
Never label an entire provider response reported_fact when it mixes those types.
A current mutable overview cannot establish historical point-in-time knowledge.
If the original publication/availability date cannot be established, record that
limitation; do not manufacture a date to pass the input validator. Vendors repeating
the same filing are not independent confirmation of its contents.

## Addendum — 2026-09-28 NOW valuation (Claude Code cloud session, not Cowork)

Observed while running ServiceNow; applies to that session's tools and network:
- Alpha Vantage INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW, COMPANY_OVERVIEW and
  TREASURY_YIELD returned data (large payloads saved to file, then import_statements).
  EARNINGS_ESTIMATES failed with a rate-limit message after ~6 calls in a burst; the
  message cited a free-key limit of 25 requests/day and 1/second. Budget roughly 6-8
  AV calls per company and space them; the user's actual plan was not verified.
- IB search_contracts + get_price_snapshot returned a REALTIME quote (conid 109911821).
- WebFetch to sec.gov, investor.servicenow.com, newsroom and most finance sites was
  blocked by that environment's egress proxy; WebSearch worked. This is a property
  of the cloud container, not evidence that Cowork is blocked. In Cowork, read the
  10-Q/earnings release directly and set sources[].retrieval=primary_document.
