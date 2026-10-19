# Stage 0 plan — reproduction baseline (written 20.10.2026, before any run)

Approved by Yarin on 19.10.2026 with six conditions (fixed golden set, AV primary + IB check for prices,
stored snapshots with full provenance, no look-ahead, separation of goals, scope documented first).
This file is committed before the reproductions run. Results go to `docs/STAGE0_BASELINE.md`; any change
to this plan after the first run is logged at the bottom with the reason.

## What stage 0 is, and what it is not

| Run type | Question | In stage 0? |
|---|---|---|
| **L — logic reproduction** | Same recorded inputs + the code that ran → same outputs? | Yes, the core |
| **D — data correction** | Was an input wrong? (e.g. ASTS net cash) | Recorded as `KNOWN_WRONG`, **not applied** to the reproduction |
| **Q — model quality** | Were the decisions good? | **No.** Stage 3b / 5. Nothing here is evidence of stock-picking skill |

A pass means "the baseline is faithful, including its known mistakes". It does not mean the engine chooses
stocks well.

## Golden set (frozen; dates do not change after results are seen)

Current names use as-of **2026-10-19** (the date of the frozen SEC bulk snapshot). The only allowed
exception is the pre-written rule: if a company has fewer reported quarters than a computation needs at
the as-of date, the first later month-end with enough data is used, and the switch is logged.

| # | Ticker | CIK | As-of | Group |
|---|---|---|---|---|
| 1 | NBIS | 1513845 | 2026-10-19 | current |
| 2 | ASTS | 1780312 | 2026-10-19 | current |
| 3 | NOW | 1373715 | 2026-10-19 | current |
| 4 | PLTR | 1321655 | 2026-10-19 | current |
| 5 | APH | 820313 | 2026-10-19 | current |
| 6 | COST | 909832 | 2016-06-30 | established |
| 7 | MSFT | 789019 | 2016-06-30 | established |
| 8 | V | 1403161 | 2016-06-30 | established |
| 9 | CRWD | 1535527 | 2020-06-30 | growth, early |
| 10 | UBER | 1543151 | 2023-06-30 | growth, turning profitable |
| 11 | ENPH | 1463101 | 2019-06-30 | growth, turning profitable |
| 12 | PTON | 1639825 | 2021-03-31 | failed |
| 13 | TDOC | 1477449 | 2021-03-31 | failed |
| 14 | BYND | 1655210 | 2020-06-30 | failed |
| 15 | CVNA | 1690820 | 2021-09-30 | failed, then recovered |
| 16 | INTC | 50863 | 2021-06-30 | established, declined |
| 17 | ZM | 1585521 | 2020-12-31 | quality, priced high |
| 18 | SNOW | 1640147 | 2021-11-30 | quality, priced high |
| 19 | PYPL | 1633917 | 2021-07-31 | quality, priced high |
| 20 | NKE | 320187 | 2021-11-30 | quality, priced high |

Machine-readable copy: `stage0/golden_set.json` (the tests check it equals this table).

## The eight reproductions (inputs, code, comparison)

| # | Decision | Code that ran | Recorded inputs | Compared against |
|---|---|---|---|---|
| 1 | Holdings 12.10 (9 names) | engine/params/run_holdings (project bundle) | per-name facts, consensus, levels, parameters in `holdings_2026-10-12.json` | the same file's outputs |
| 2 | APH pick 13.10 | run_stage2b gates + aph_detail | `stage2_2026-10-13.csv` (+ APH quarter facts from the 18.10 record, same quarter) | csv columns + scan note |
| 3 | Re-run 18.10 | `brain_rerun_2026-10-18.py` (inline inputs) | inline B dict; `levels.json` missing → its three fields per name are taken from the run's own output | `holdings_2026-10-18.json` |
| 4 | NOW 18.10 | `now_model.py` (+ `now_brain_fix.py`) | constants in the script; live 18.10 brain record | `now_model_out.json`; brain fix vs the deep-research note |
| 5 | ASTS 18.10 | `asts_model.py` (+ `asts_brain_fix.py`) | same | `asts_model_out.json`; note |
| 6 | GOOG 19.10 | `brain_lite_2026-10-19.py` | `facts_2026-10-19.json` | table in `2026-10-19_scan.md` (rounded) |
| 7 | TSM 19.10 | `brain_lite_2026-10-19.py` | `facts2_2026-10-19.json` | table in `2026-10-19_quality-scan-v2.md` (rounded) |
| 8 | NBIS 19.10 | `nbis_model.py`, `sens.py`, `run_nbis.py` | constants in the scripts | `nbis_model_out.json`, `nbis_brain_2026-10-19.json` |

Rules for the harness:
- Original code is copied **byte-for-byte** from the project files (extracted programmatically, hash recorded).
  It is never edited. Where it reads a data file that was not saved, the harness writes that file from the
  recorded values, or replaces a pure data-access function (`daily`, `levels`, Yahoo snapshot) with a stub
  that returns the recorded value. Every stub is listed in the report.
- Where an input must be recovered from an output (e.g. dividend yield from cagr3 − price CAGR, or the
  18.10 levels), it is flagged `RECOVERED_FROM_OUTPUT`; outputs that then match trivially are not counted
  as evidence.
- Position sizes and account value are private: they are removed from everything committed to Git; weight
  and value outputs are excluded from comparison.

## Pass criteria (measurable)

| ID | Criterion |
|---|---|
| E1 | Every reproducible numeric output matches within 1% relative, or within 0.0005 absolute for rates/percent quantities whose recorded value is within ±0.05 of zero; every action / gate string is identical |
| E2 | Every deviation above E1 is classified: harness substitution, recorded input missing, rounding of a text table, or unexplained. Unexplained = fail |
| E3 | Every non-reproducible part is listed with its missing input |
| E4 | The 307 existing tests and all new tests pass |
| E5 | No look-ahead: in every golden snapshot, 0 facts filed after (as-of − 2 days) are used; every price is the as-traded close of the last trading day ≤ as-of, with split adjustments undone; a price that cannot be checked against a second source within 1% is marked `UNVERIFIED`/missing, not used as verified |
| E6 | Snapshots stored in at least two locations outside this container, read back, SHA-256 identical; each has source, retrieval time, information date, code version (git commit), SHA-256 |
| E7 | No existing business-logic file changes: `git diff origin/engine-upgrade-2026-10 -- fundamental_engine/` shows additions only in new files |
| E8 | PR #7 is reviewed against the reproduction results; it stays open |

If any criterion fails, the report says so and stage 0 is not declared complete.

## Price-source audit (before prices are used)

For each golden name: Alpha Vantage monthly raw series (primary), Alpha Vantage split events, Interactive
Brokers history (check), the Yahoo monthly file already on disk (third reference, split-adjusted).
Documented per source: coverage start, pull limits observed, how splits and dividends are applied, and
whether the price "available on the date" (as-traded close) can be recovered. IB returns at most five years
of bars per call in this connector, so for 2016–2021 dates IB may be unable to check, which is recorded.

## Data needing manual extraction (documented in stage 0, extracted in stage 1)

From the availability check of 19.10 (`stage0/golden_availability.json` after the run):
- Share counts by class from the cover page (not in SEC structured data): ASTS, CRWD, V, PTON, CVNA, ZM, NKE.
- Convertible-note terms and option/RSU counts (only in note text): most names; listed per company.
- Operating income not tagged: CVNA, NKE (a definition is needed, not a guess).
- Short-term investments under a non-standard tag: MSFT 2016.
- Debt principal per instrument (only fair value tagged): APH.
- NBIS: only annual XBRL (20-F); quarterly figures from 6-K text.
- Events between the last filing and the as-of date (8-K): V (Visa Europe, 21.06.2016), PTON (convert, 02.2021)
  and a scan for all names.
Stage 0 stores the filing index (accession, form, filed date, URL) needed for these, not the extracted values.

## Files (all new; no existing business logic touched)

- `stage0/golden_set.json`, `stage0/README.md`
- `stage0/legacy/` — verbatim copies of the code that ran, with `SOURCES.json` (project path, sha256, retrieval time)
- `stage0/fixtures/` — recorded inputs/outputs, private fields removed
- `scripts/stage0_extract.py` — pulls files out of the session transcript byte-exact (no retyping)
- `scripts/stage0_golden.py` — builds point-in-time golden snapshots + availability + filing index
- `scripts/stage0_prices.py` — price-source audit
- `scripts/stage0_repro.py` — runs the eight reproductions, writes `stage0/repro_results.json`
- `fundamental_engine/snapshot.py` — manifest/sha256 helpers (no business logic)
- `tests/test_stage0.py` — golden set frozen, PIT guard, snapshot round-trip, reproduction tolerances
- `docs/STAGE0_PLAN.md` (this), `docs/STAGE0_BASELINE.md` (report), `docs/SNAPSHOT_POLICY.md`

## Change log
(none yet)
