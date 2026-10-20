# GOOGL and NVDA through the v0.11 decision path — 20.10.2026

A process record, not price targets. Continues the open item of the 0.11.1 session (deleted conversation): GOOGL and NVDA
had no structured statements, so no multi-company comparison had run. Inputs to reproduce (analyst judgement and evidence
bundles, public data only): `examples/real/runs_2026-10-20/`. Raw SEC payloads and documents are not committed
(`runs/`, several MB); `raw_MANIFEST.json` lists what was fetched, when, with SHA-256.

## Data collected

- FMP and Alpha Vantage were at their daily limits (recorded as denials, not retried).
- data.sec.gov and www.sec.gov answered from the container for the first time: companyfacts, submissions, the latest 10-Q
  and the earnings-release exhibits were fetched and read (GOOGL Q2 2026; NVDA Q2 FY2027). IB realtime quotes.
- Evidence bundles cite the documents read directly (`retrieval: primary_document`); NVIDIA's fiscal 2028 outlook (+70%,
  supply-constrained) comes from two secondary reports of the call and is labelled as such.

## Engine defects found and fixed (each with a regression test that fails on the earlier code)

| Defect | Effect on the run | Fix |
|---|---|---|
| Investment tags `DebtSecuritiesCurrent`, `EquitySecuritiesFvNi` not read; a vanished line read as zero | NVIDIA opening balance missed about $77bn | tags mapped, `equity_securities_fv`, `investment_line_continuity` |
| review opening check ignored listed stakes | correct opening failed as overstated | counted once, as in the forecast |
| `us-gaap:Depreciation` not read | Alphabet depreciation proxied by capex (16% vs 5%) | tag mapped after the combined tags |
| First forecast year escaped the revenue-scale cap | NVIDIA started next year at +73% while the warning said 15% | `growth_start_capped` |
| Growth overrides kept the discarded path's capex | about $20 a share from a path no longer used | capex recomputed for the analyst path |

## Analyst judgement used (all in `inputs.json`, with reasons and evidence ids)

- NVDA: cycle normalization to the 2017-2026 mean rejected (a ~$68bn revenue trend after a 30x change in scale); explicit
  paths instead: base FY28 +60%, FY29 +20%, FY30 +8%; bear FY28 +35% then FY29 -30%, FY30 -15% with a 35% mature margin;
  bull FY28 +70%, FY29 +35%. Terminal ROIC 30% base (15% bear, 40% bull) instead of the 12% unknown-moat default against
  a demonstrated ~110%.
- GOOGL: moat and technology/AI assessed strong for margins; terminal ROIC 25% base (near the measured incremental 24.7%),
  15% bear, 35% bull, because returns on the doubled AI capex are not yet demonstrated; mandatory convertible preferred
  ($19.1bn) as a claim.

## Results (drafts: no independent review)

| | Price | Bear | Base | Bull | Base IRR | Required | Engine verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| GOOGL (20.10) | 344.46 | 132 | 217 | 375 | 7.4% | 9.75% | quality but expensive |
| NVDA (20.10) | 216.31 | 58 | 205 | 322 | 9.9% | 11.3% | avoid (forensic gate) |
| ADBE (19.10, fixture) | 246.87 | 221 | 301 | 402 | 10.6% | 9.75% | attractive at price, marginal |

NVDA's verdict is the forensic gate (Beneish likely-manipulator zone with weak cash conversion), not the valuation: the
documents explain much of it (net income includes $24bn of other income, mostly equity-security gains, in the first half;
receivables rose on extended payment terms, itself a counterparty-financing risk next to $108.5bn of guarantees). Only an
independent reviewer may release the gate. Every verdict stays a draft until a reviewer who did not write the thesis signs
the casefile.

Not established: forecast accuracy or any edge over an index (docs/EVIDENCE_STATUS.md).
