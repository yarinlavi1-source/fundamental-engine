# Stage 0 baseline report (20.10.2026)

Plan: `docs/STAGE0_PLAN.md` (committed before any run). Public machine-readable results: `stage0/price_audit.json`,
`stage0/pr7_pit_check.json`, `stage0/storage_check.json`.

**Privacy split.** The reproductions run code and records that name Yarin's positions (which names, sizes, account
value). Those files stay out of this public repository: the legacy code copies, fixtures, field-by-field results
(`repro_results.json`), the known-error register and the harness (`stage0_repro.py`, `stage0_legacy.py`) live in the
private stage 0 store (backed up as described in section 5). This report gives the results without naming positions.

**Status: stage 0 is NOT complete.** Criterion E6 is not met for two data sets (the public SEC golden bundle and the
private stage 0 tree each have one verified backup outside this container, not two). Every other criterion is met.
Details in section 5.

What this report is not: none of it is evidence that the engine picks stocks well. It only shows that the recorded
decisions are reproduced by the code that made them, from recorded or reconstructed inputs, including their known
mistakes (run type L). No known mistake was corrected (run type D) and no quality test was run (run type Q).

## 1. Reproductions (L)

Tolerance: 1% relative, or 0.0005 absolute for values within ±0.05 of zero; strings must be identical.
"Evidence fields" exclude inputs that were fed in (price, market cap, consensus, levels, percentiles).

| Case | Decision | Fields compared | Evidence fields matched (non-empty) | Deviations > tolerance |
|---|---|---|---|---|
| 1b | Portfolio run 12.10 (9 names), engine as it ran (addendum block removed) | 693 | 540 of 540 | 0 |
| 1a | Same, engine exactly as preserved | 693 | 514 | **26** (explained, see 1.1) |
| 2 | APH pick 13.10 (stage-2 row + aph_detail) | 49 | 34 of 34 | 0 |
| 3 | Portfolio re-run 18.10 (11 names) | 176 | 143 of 143 | 0 |
| 3b | Portfolio run 18.10 (11 names) | 858 | 662 of 662 | 0 |
| 4 | NOW 18.10: revenue-stream model + brain fix (as run and fixed) | 243 | 243 of 243 | 0 |
| 5 | ASTS 18.10: model + brain fix (as run and fixed) | 295 | 294 of 294 | 0 |
| 6 | Scan 19.10 incl. GOOG (`brain_lite`, 10 names, rounded table) | 38 | 38 of 38 | 0 |
| 7 | Quality scan 19.10 incl. TSM (`brain_lite`, 27 names, rounded table) | 52 | 52 of 52 | 0 |
| 8 | NBIS 19.10: model + brain run (3 capital-structure variants) | 319 | 319 of 319 | 0 |

"Matched both missing" (a field empty in the record and in the reproduction, e.g. NBIS fields with no XBRL) is counted
separately in the json and not as evidence: 81 (case 1), 108 (3b), 1 (5).

Facts (revenue, margins, cash, debt, growth, surprises) in cases 1, 2, 3b, 4, 5 were **recomputed** by the original
`facts.py` from SEC data rebuilt out of the 19.10 bulk file (facts filed on or before the run date) and matched the
recorded facts for every US filer. They are counted as "fact" rows, separate from the valuation/decision rows.

### 1.1 The only deviations (case 1a): the preserved 12.10 engine is not the version that ran

The only copy of the 12.10 `engine.py` already contains the "addendum 13.10.2026" net-margin cap. The 12.10 records
have no `nm_raw` field, i.e. they were written before that addendum. Run as preserved, the capped margin changes
3 of the 9 names (net margin, base/bull margins, prices and returns) and one action changes from `HOLD_NO_ADD` to
`TRIM_HALF`. With only the marked addendum block removed (case 1b), all 693 fields match. So the preserved code is
not the code behind the 12.10 recommendations; the 12.10 decisions used the uncapped margin.
Classification (E2): code version not preserved (harness substitution, documented). Not unexplained.

### 1.2 Substitutions used (all listed per case in the json)

- `SEC_RECONSTRUCTED`: the reduced SEC dump the old code read was not saved; rebuilt from the bulk file of 19.10,
  keeping only facts filed on or before the run date. The latest filing date actually used is recorded and checked.
- `YAHOO_FROM_RECORD`: price, market cap, report date, shares and consensus taken from the run's own output.
- `RECOVERED_FROM_OUTPUT`: Yahoo cash/debt solved back from net cash; dividend yield from cagr3; the 18.10
  `levels.json` from the output; APH 13.10 consensus solved from the csv's g1 and EV/S. Fields that then match
  trivially are classed as inputs.
- `LEVELS_STUB`: daily prices were not saved; the recorded level block is returned instead.
- `EVDIST_SYNTHETIC` (12.10): the earnings-event distribution was not saved; a synthetic one reproduces the recorded
  percentiles. `ENGINE_SUBSTITUTE` (13.10): the 18.10 engine (same logic plus a percentile override) is used for APH.
- Script output paths under `/home/claude/...` are redirected into the sandbox.

### 1.3 Not reproducible (E3)

| Part | Missing input |
|---|---|
| INCY 19.10 "after fix" (6.7%/yr, 6/7) | the corrected inputs were not saved (as-run values are reproduced) |
| `aph_detail.py` price statistics (relative strength, earnings-day moves, volatility) | daily prices of 13.10 |
| `sens.py` (NBIS sensitivity tables) | runs, but its printed output was never saved, so nothing to compare |
| Levels (50/200-day averages, supports) in every engine run | daily prices; used as recorded inputs |
| SUE/SURGE percentiles | the 2018-2026 event distribution; used as recorded inputs |
| 13.10 stage-2 rows other than APH | their Yahoo snapshots; only the APH row was reconstructed |
| 19.10 Yahoo consensus for NBIS | used the 18.10 consensus; ev_s_ntm still matched |

### 1.4 Deviations caused by known wrong data

None. Nine known input/logic errors are registered (private register KW-1..KW-9) and every one is reproduced as it
was used, not corrected. Examples from the research notes: NOW net cash −$3.79B (correct per the note −$0.81B), ASTS
+$1.6B and 389M shares (−$0.95B, 401M), NBIS 280M shares (308.7M economic); the 12.10 uncapped net margin (1.1);
INCY's 19.10 input corrected after the scan. Where a note contains a corrected run (NOW, ASTS, NBIS), that run is also
reproduced, and the recorded decision does not change.

## 2. No look-ahead (E5)

- Golden snapshots: in all 20 point-in-time views, 0 facts filed after as-of − 2 days are present (asserted when
  built and again in the tests). The raw companyfacts files, which do contain later filings, are labelled as such.
- Reproduction sandboxes: the latest SEC filing used is on or before the run date for every name (recorded per case).
- PR #7's PIT layer gives identical TTM values and share counts from the full files and from the golden views for all
  20 companies (`stage0/pr7_pit_check.json`).
- Prices: for all 20 as-of dates the as-traded close of the last trading day on or before the as-of date was found
  (Alpha Vantage `close`, not `adjusted close`) and checked by a second source within 1%: 8 against Interactive
  Brokers (differences ≤ 0.03%), 12 against the Yahoo monthly file with later splits undone (IB has 5 years of
  history only). 0 unverified, 0 missing. Later splits that this check had to undo correctly: CRWD 4:1 (2026), CVNA
  5:1 (2026), BYND 1:30 reverse (2026). ENPH and PYPL as-of dates fall on a weekend; the previous trading day is used.

**Label:** every historical snapshot is a reconstruction from data retrieved on 19-20.10.2026, filtered by filing date.
None is presented as an original same-day snapshot. Restated values known by each as-of date are listed per tag.

## 3. Price sources (audit, before use)

| Source | Coverage seen | Limits seen | Splits / dividends | As-traded price recoverable |
|---|---|---|---|---|
| Alpha Vantage `TIME_SERIES_MONTHLY_ADJUSTED` (primary) | all 20; from 1999 or the IPO | ~25k tokens per call; no rate limit hit in ~30 calls; the plain `TIME_SERIES_MONTHLY` tool returned an entitlement error | `close` is as traded, `adjusted close` includes splits and dividends | yes (monthly bars; a mid-month as-of uses the month-to-date bar, pulled after the close) |
| Alpha Vantage `SPLITS` | returned `{}` for 8 of 11 names, incl. names with splits | unreliable | — | splits inferred instead from close / adjusted-close jumps (factor outside 0.8-1.25) and cross-checked with Yahoo |
| Interactive Brokers `get_price_history` (check) | 8 names | 5 years per call (monthly) | not tested on a post-split name | yes inside 5 years |
| Yahoo chart monthly file (second check) | all except NBIS | bulk download of 19.10 | `c` split-adjusted; undone with its split list | yes after undoing splits |

Caveats: NBIS's Alpha Vantage history starts in 2011 (Yandex era, same ticker); TDOC's last bar is 16.10; the Yahoo
file was pulled intraday on 19.10 (so it is not used for the 19.10 close).

## 4. Criteria

| ID | Result |
|---|---|
| E1 | Met for every reproducible output (case 1b is the 12.10 reproduction of record; 1a is reported, not hidden) |
| E2 | Met: the 26 deviations of 1a are classified (code version); 0 unexplained |
| E3 | Met: section 1.3 |
| E4 | Met: 319 tests pass in the repository (307 existing + 12 new); 12 more private tests pass in the private store |
| E5 | Met: section 2 |
| E6 | **NOT MET** (section 5) |
| E7 | Met: `git diff origin/engine-upgrade-2026-10 -- fundamental_engine/` adds `snapshot.py` only; no existing file changed |
| E8 | Met: section 6; PR #7 stays open |

## 5. Snapshot storage (E6)

| Data | Location 1 | Location 2 | Result |
|---|---|---|---|
| Private: vendor prices + price audit | Yarin's computer, `Documents\fundamental-engine-stage0` (read back, SHA-256 identical) | claude.ai Project doc `claude/stage0/stage0_private_price_pack.json` (read back, identical) | met |
| Private: project files used (code, records with position sizes) | Yarin's computer (in the private bundle, identical) | the claude.ai Project itself (source; repeated reads identical) | met |
| Private: stage 0 tree (harness, legacy copies, results, register, detailed report) | Yarin's computer (read back, identical) | none: the claude.ai Project refused the write, it is at its maximum size | **not met** |
| Public SEC golden bundle (3.8 MB) | Yarin's computer (read back, identical) | none | **not met** |

Tried for the second copy of the SEC bundle: Google Drive (round trip works for small text, but the connector only
takes content inline, so a 3.8 MB file cannot be uploaded from here), the Project (refuses binary), a draft GitHub
release asset (blocked by this session's permission policy; not retried). For the private tree the Project refused
the write because it is full (nothing was deleted to make room). Ways to close both: Yarin copies the folder
`Documents\fundamental-engine-stage0` to Drive or another disk (one drag and drop; SHA256SUMS is inside to check), or
frees room in the Project, or approves a release asset for the public SEC bundle only.

## 6. PR #7 reviewed against the reproduction (E8)

- PR #7 changes no legacy file and nothing the baseline runs; the reproduced decisions do not depend on it.
- Its PIT layer agrees with the golden snapshots on all 20 companies (no late fact leaks).
- Findings for stage 1, not fixed here: `pit.shares_at` returns APH's pre-split cover-page count (1.23B, 28.7.2026)
  after the ~2:1 split (KW-8); for V, CRWD, CVNA and NBIS it finds no share count, and for ASTS, PTON, ZM and NKE only
  a Class A weighted count (KW-9). PR #7's NBIS capital example uses 308.7M shares, the corrected count of KW-3.

## 7. Files

New only, in Git: `stage0/` (golden set, price audit, PR #7 check, storage check), `scripts/stage0_extract.py`,
`stage0_prices.py`, `stage0_golden.py`, `stage0_pr7_check.py`, `fundamental_engine/snapshot.py` (provenance helpers, no
business logic), `tests/test_stage0.py`, this report, `docs/SNAPSHOT_POLICY.md`.
Private store only: legacy code copies (position lines redacted even there), fixtures, `repro_results.json`, the
known-error register, the harness and its tests, the detailed report with names, vendor price files, SEC bulk data.
