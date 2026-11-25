# The lean path: the daily runbook (16.11.2026)

The default path for every question about a company, every results day, the portfolio check and the stock search.
The heavy pipeline (`ticker-stage` agents, `deep`, the Workflow `ticker`) is a cross-check that runs only when the owner
asks for it (START_HERE.md). Every command below was run on 16.11.2026 with `--help` or a dry run on real files.

Names used here: `T` ticker, `D` today (YYYY-MM-DD), `P` today's price, `B` the baseline folder
`research/companies/T/<date>/` that `research-latest` prints, `N` the new folder `research/companies/T/D/`.

Rules that hold on every step:
- A saved `lean_params*.json` is never edited, and no existing key of a saved `lean_result*.json` changes
  (`tests/test_saved_valuations.py` fails otherwise). A changed valuation goes in a new dated folder `N`.
- `research/` is company research only: no holdings, share counts, costs, amounts or advice about the owner's portfolio.
  Portfolio files live under `runs/portfolio_D/` (gitignored).
- Broker tools are read-only: `get_price_snapshot` and `get_account_positions` only.
- SEC requests read `SEC_USER_AGENT` from the environment. Never print it or write it to a file.
- Time: usually 5-15 minutes per stock. A custom model takes about 15-25 minutes and a buy verification pass adds more:
  say so before starting. Never run 30 minutes or more without saying so first. Report the real time of every run.

## The price labels: one definition each

| Label shown to the owner | Definition | Where the number is |
|---|---|---|
| מחיר מבחן הקנייה | The highest price today at which all four legs of the buy test pass: base >= 12% a year in both lenses, bull >= 17%, bear >= -10% (yearly return from the price to the horizon year-end). When lean_model warns that a rerun at that price holds a lower multiple, it is the rerun price. | `valuation.json` `hurdles.buy_test_price`; `fundamental.json` `v2.buy_test.price_all_legs`; lean_model `buy_test.max_price_passing`, or `buy_test.rerun.max_price_passing` when `buy_test.rerun.lower_than_stated` is true |
| מחיר כניסה עם מרווח ביטחון | The same buy test with the base at 13% a year in both lenses instead of 12% (bull >= 17%, bear >= -10% unchanged): the highest price at which all four legs pass, the rerun price when the model warns. So it is never above the buy-test price (16.11.2026; until then it was the base alone at 13%, which could lie above the buy-test price and above today's price: ORCL, APP). The same 13% for every company. It is never called the owner's requirement or target. | lean_model `buy_test.margin_of_safety_price` (with `margin_of_safety_binding_leg`, `margin_of_safety_rerun`); weighted worlds: `worlds.weighted.buy_test.margin_of_safety_price`; `valuation.json` `hurdles.margin_of_safety_price` and `hurdles.entry_price`; `fundamental.json` `margin_of_safety_price` and `v2.buy_test.margin_of_safety` |
| שווי לפי המכפיל ההיסטורי | The next-year figure times the company's own 5-year median multiple, with the p25-p75 range. The anchor follows the company type (06.11.2026 rule). | `locked_rules.hist_multiple_value`; `valuation.json` `historical_multiple` |

- The other rungs are always quoted with their return: index parity (rf + ERP from `research/market.json`, about 9.1%),
  the hold floor (6%) and the sell line (3%). These are bands, not entry prices.
- The ladder rung `margin_of_safety` in `lean_result.json` (id `company_hurdle` in the record) is the file's own
  `buy_return`. Its old label text says "entry with a margin of safety", but it is **not** the margin-of-safety price.
  Never quote it under that name.
- The ladder rung `margin_of_safety_13` (record `hurdles.base_13_price`) is the base case alone at 13% a year. Its
  label says "margin of safety: fixed 13%", but since 16.11.2026 it is **not** the margin-of-safety price either: it is a
  rung, quoted only with its return.
- `fundamental.json` `buy_test_price` (v1) is null, with `buy_test_price_note`, when no price passes all four legs or the
  model computes none; the base-alone 12% price is `v2.buy_test.base_12_price`.
- Second lens tied to the price (16.11.2026, optional): `calibrated.pe_rule: "net_of_cash"` holds (price - net cash per
  share at the base year-end) / first-year operating EPS at every price, so the buy-test rerun and `--price` move it. The
  default (price / EPS) is unchanged; for a saved valuation the rule goes in a sidecar `calibrated_rule.json` beside
  `lean_params.json` (`lean_model.py --calibrated-rule`; `publish_valuation.py` applies it to the record and keeps
  `lean_result.json` the params' own result). In force for FIVE only; the measurement for every saved valuation is in
  runs/engine_upgrade_2026-11-15/calibrated_cash_measure.md (an owner decision). `lean_freeze.py`, the screen
  (`quality_momentum`) and `portfolio_health` do not read the sidecar yet: the frozen FIVE row keeps the saved second
  lens, and a rerun of FIVE at another price holds the fixed 21.1 (the screen gives 231.47 at 246.68, the record
  220.47). For FIVE, take the buy-test price from `valuation.json` or `lean_model.py --calibrated-rule`.
- `fundamental.json` `buy_test_price` (v1) keeps its v1 meaning for the drawing session. It can differ from
  `v2.buy_test.price_all_legs` when the rerun price is used. The owner is told the v2 number.
- Two lenses: "ours" is lean_model as built. "Second lens" (calibrated) holds today's forward P/E as the exit multiple
  and does not cut the margin below the first forecast year. `calibrated_diagnostics` says when the second lens is not a
  second opinion (an inflated held multiple). A buy, sell or trim call is made only where both lenses agree on the band.

## (a) A question about a ticker

1. **Baseline.** Run `python -m fundamental_engine research-latest T` (`--full` adds the record). It writes nothing.
   - It prints the newest dated folder of any kind (lean, custom or heavy engine), plus its summary: values, returns,
     second lens, the two prices above, the verdict and the next report.
   - It also prints the handoff path and the superseded heavy-engine save.
   - "none" means a new company: build from the filings.
2. **Fresh pull (03.11 rule), all in the same turn.**
   - `python scripts/ticker_prefetch.py T D --reuse auto` as a background Bash. It writes `runs/tickers/T_D/`,
     including `companies/T/raw/sec__companyfacts.json`, `reading/`, `prefetch.json` and `identity.json`.
   - IBKR `get_price_snapshot`. Check that the contract is T on its exchange (lesson L27).
   - Two background lanes, each about 8 tool calls and about 3 minutes, returning GAP for the rest:
     - the filings lane runs the fine-print checklist;
     - the web lane covers the call, guidance and its record, attributed consensus, competitors and news since the
       last filing.
   - If no new fact touches the inputs, reprice only and say that the answer reuses the saved valuation:
     `python scripts/lean_model.py B/lean_params.json --price P --as-of D --summary`. This writes nothing.
3. **New params from the baseline. Change only what the new facts touch.**
   1. `mkdir -p N`, then
      `python scripts/lean_model.py B/lean_params.json --price P --as-of D --params-out N/lean_params.json`.
   2. Dates move only when the file holds `base_year_end` (ISO). If it is missing, add it to `N/lean_params.json`
      and run `python scripts/lean_model.py N/lean_params.json --as-of D --params-out N/lean_params.json`.
      That rewrites `as_of`, `days_to_base_year_end` and `years_to_horizon`. Keep the two hand keys: the tracker,
      the freeze and the golden test read them.
   3. Edit only the touched inputs, and write the reason for each change in `notes`.
   - Optional additive keys read by lean_check: `guidance`, `judgement`, `tags`, `reconciliation`,
     `one_off_revenue_m`, `price_time_utc`.
   - Variants with weights go in `N/worlds.json` (`--worlds`).
   - Converts with puts, project debt or a patent cliff need a custom model in `N`. Add a new custom script to
     `CUSTOM` in `tests/test_saved_valuations.py`.
4. **Locked rules.**
   - Beta: `python scripts/locked_rules.py T --as-of D --save runs/tickers/T_D/beta.json`. It prints the
     `judgement.beta` block: raw, adjusted = 0.67 x raw + 0.33, and the 104-week window.
   - Exit P/E and the historical-multiple report use `fundamental_engine.locked_rules.multiple_history(prices,
     figures, basis)`, then `hist_multiple_value(company_type, next_year_figure, history)`. The series come from the
     session's connectors.
   - rf and ERP come from the `research/market.json` entry in force on D.
   - A `used` value that differs from the rule needs `reason_if_different`.
5. **Model.** Run `python scripts/lean_model.py N/lean_params.json --summary`. It writes nothing. Read these parts:
   - values today, at the year-ends and at exactly 1, 2 and 5 years;
   - both lenses and the price ladder;
   - the buy test with its rerun line;
   - the method gap, sensitivity and warnings.
6. **Self-check.** Run
   `python scripts/lean_check.py N/lean_params.json --prev B/lean_params.json --facts runs/tickers/T_D/companies/T/raw/sec__companyfacts.json --price P --today D --save`.
   - It writes `N/lean_check.json`.
   - Manual questions are answered one line each in `N/lean_check_answers.json` (`{"L21": "..."}`), which is read by
     default. A question that blocks a buy needs `{"answer": "...", "confirmed": true, "evidence": ["file or URL"]}`
     (16.11 lesson P09): a text alone stays open, `"confirmed": false` is a fail.
   - Exit 1 means the buy test passes but a blocking item is failed or open, so this is not deliverable as a buy.
     Exit 2 means the params cannot run.
7. **For a buy only: the verification pass.** Parallel lanes check every number and event against a primary source,
   and one red-team reviewer looks for the strongest counter-case (04.11 rule).
   - Save the params from before the review as `N/lean_params_pre_review.json`.
   - Save the findings as `N/review_findings.json`, in the form of
     `research/companies/UBER/2026-11-13/review_findings.json`.
   - Then repeat steps 5-6. Anything not verified is dropped, or the answer is labelled a draft.
7b. **Calibration pass (25.11.2026 owner rule), for every valuation.** Four evidence lanes in parallel: historical
   analogs of the same situation with numbers; long-dated option implied volatility turned into 50% and 80% bands
   (risk-neutral caveat) plus the analyst range; the industry outlook for 2-3 years from dated sources; our own measured
   biases for this kind of company. Then set labelled subjective probabilities per scenario (with a note in
   `lean_params.json` `notes.probabilities`), and write the most-likely price (median) with ~50% and ~80% ranges at 1, 2
   and 5 years, and the path risk, into the thesis section `הסתברויות וטווחים מכוילים` and `thesis_inputs.json`
   `notes_he`. The base value and the likely price are shown apart. A large path risk means a staged buy. The buy test
   does not change. Example: research/companies/DELL/2026-11-25.
8. **Publish.**
   - Command: `python scripts/publish_valuation.py N/lean_params.json --thesis-inputs N/thesis_inputs.json`. For a
     custom model, pass the folder `N` instead.
   - `thesis_inputs.json` holds the verdict, the conditions, typed falsifiers, the next report and the
     historical-multiple inputs. Its keys are `valuation_record.THESIS_INPUTS_KEYS`.
   - Without `--thesis-inputs`, only the texts of a handoff already saved for the same date are carried over, untyped. A
     new folder has no such handoff, so its verdict and conditions would stay empty.
   - It writes `N/lean_result.json`, `N/valuation.json` and `research/technical/T/D/fundamental.json`, replaces the
     company's one row in `research/THESES_he.md`, and rewrites the numbers block of
     `research/companies/T/thesis_he.md`.
   - A new company needs its thesis started from `research/THESIS_TEMPLATE_he.md`, including the two marker lines.
   - Prose and the history line are written by hand.
   - Then run `python scripts/publish_valuation.py --check`. It must print 0 problems.
9. **Freeze.** Run
   `python scripts/lean_freeze.py N --next-report YYYY-MM-DD [--falsifiers falsifiers.json]`.
   - It appends the thesis and its forecast rows to `research/ledger.jsonl`. A newer valuation supersedes the older one.
   - `python scripts/lean_freeze.py --verify` checks the chain.
   - A check abandoned before a full valuation gets one line in `research/rejected.jsonl`, in the form of the existing
     rows, so the screen marks the name.
10. **Answer, in the thesis-book order:**
    1. the bottom line;
    2. fair value today, in one year and in five years (דובי / בסיסי / שורי), with the 2-year and 5-year ranges and the
       note that the 1-2 year number is the noisiest;
    3. the thesis;
    4. key points from the two reports (fundamental with both lenses, then the historical multiple);
    5. the risks;
    6. what would change the view;
    7. sources as links, and the real run time.

## (b) Results day

1. **Who reported.** Run `python scripts/due.py --days 7 --today D`. It prints the Hebrew table. With `--out DIR` it
   writes `due.json` and `due_he.md`. Then run `research-latest T` for each company.
2. **Fresh pull.**
   - `ticker_prefetch.py T D --reuse auto`, which lists the filings after the reused run.
   - IBKR price.
   - The two lanes on the release and the call: guidance against the prior guide and attributed consensus, one-offs,
     share count and net cash.
3. **New folder `N` from `B`,** as in step (a)3. Change only the inputs the report touches: base-year actuals, the
   guided year, shares and net cash.
4. **Self-check against the previous file.** Run
   `python scripts/lean_check.py N/lean_params.json --prev B/lean_params.json --facts ... --price P --today D --save`.
   - `prev_inputs` gives each changed input with its own effect on the value.
   - `prev_levels` gives the level change per year.
   - From these, state in one line what moved the value and by how much.
   - Before any threshold changes, record every condition that fired (lesson P06). The typed conditions are in
     `research-latest T --full`.
5. **Model, publish and freeze,** as in steps (a)5, (a)8 and (a)9. The new thesis supersedes the old one in the
   ledger.
6. **Score.** Run `python scripts/forecast_tracker.py`. It writes `research/FORECAST_TRACKER_he.md` and
   `research/forecast_tracker.json`, and adds outcome rows to `research/ledger.jsonl`. `--no-ledger` writes no rows;
   `--offline` uses cached data only.
   - It scores only valuations frozen before the report day.
   - For a custom model, a bank or a foreign filer, first fill `scorecard.json` in the folder of the valuation being
     scored (`B`). The form is `forecast_tracker.SCORECARD_FORM`; filled examples are
     `research/companies/NBIS/2026-11-11/` and `research/companies/NU/2026-11-01/`.
7. **Thesis.** Add the history line and the prose, then answer in the thesis-book order.

## (c) Portfolio check (the owner's holdings, private)

1. **Positions.** Call IBKR `get_account_positions` (READ-ONLY) and save the result as
   `runs/portfolio_D/positions.json` in the form `{as_of, source, rows: [{t, shares, price, value, weight, avg_cost}]}`.
   It never goes under `research/`.
2. **Filing check.** Run
   `python -m fundamental_engine holdings-update --positions runs/portfolio_D/positions.json --as-of D --refresh --out runs/portfolio_D`.
   It writes `holdings_update.json` and `holdings_update_he.md`.
3. **Rules.** Run `python scripts/portfolio_health.py --positions runs/portfolio_D/positions.json --out runs/portfolio_D`.
   It writes `portfolio_health.json` and `portfolio_health_he.md`. The output folder must be under `runs/`.
   - It reprices each holding at its position price and date.
   - Per holding it shows: the band per lens and whether the lenses agree; the buy-test legs; sizes against 15% / 20%
     (5% for a small bet); linked exposure; the age of the valuation; and the state of its conditions.
   - At portfolio level it shows the index share against 60-70%.
   - It reads `holdings_update.json` from the same folder and the private `runs/portfolio_rules.json`. Without the
     rules file it uses the defaults and gives no roles.
4. **Due.** Run
   `python scripts/due.py --days 30 --today D --positions runs/portfolio_D/positions.json --out runs/portfolio_D`.
   It writes `due.json` and `due_he.md`.
5. **Act.**
   - Rows `new_holding` or `full_rerun_needed`, and reports that passed without an update, go to (a) or (b).
   - Light updates keep the frozen values.
   - Deliver the Hebrew tables. They state rule results, never orders.

## (d) Stock search

1. **Screen.** Run `python scripts/quality_momentum_screen.py runs/screens/D --as-of D [--reuse]`. It writes
   `screen.json` and `screen_he.md` under `runs/screens/D` (about 15 minutes for a fresh run).
   - Version 2 (25.11.2026): **price is information, never a gate.** A stock far below its high or under its average
     is not dropped. The gates are the quality gates (SEC filings) plus two business-momentum gates from analyst data:
     analysts are not cutting EPS estimates (4-week revisions up minus down >= 0, consensus not more than 1% lower),
     and reported EPS met the consensus in each of the last two quarters (a miss of up to 2%, or of up to one cent a
     share, counts as met). The two analyst gates count as one failed condition: a missed quarter lowers the full-year
     consensus by itself. A company without analyst data is "חסר נתון", never a passer.
   - What stops what: a business that stopped growing (the PayPal pattern) is stopped by the quality gates. The
     analyst gates only check that the last two quarters and the last month of estimates are not getting worse.
   - "לא קריא" in the list "חסר להן נתון": reported EPS swung more than 25% above the consensus in one quarter and
     below it in the other (investment gains inside EPS, as at GOOGL, or a base of a few cents). The analyst gates are
     not read for such a name; check it by hand before giving it up.
   - Sources: SEC companyfacts per company (not XBRL frames), the Nasdaq listing feed, the Nasdaq analyst pages per
     company (an unofficial public API; Nasdaq names Zacks as its vendor) and its earnings calendar, Yahoo daily closes
     (information).
   - The revision window is about one month (Nasdaq). Each run also appends the consensus to
     `runs/screens/_estimates_history/<SYMBOL>.jsonl`. That folder is under runs/, not in Git: only while it is kept
     between runs does the window grow once our own readings are older than a month (up to 100 days; the window is
     printed beside each number and can differ between companies). Run the screen on the as-of day: the analyst pages
     are a snapshot and cannot be rebuilt later; the report says so when they were read on another day.
   - Read first the section "המחיר ירד, העסק משתפר" (a passer or near miss 25% or more below its high whose analyst
     data shows an improvement: more analysts raised than cut, the consensus is up, both quarters at or above it; the
     DELL pattern) and "רדאר דוחות" (who reported in the last 10 days with a surprise of +5% or more, who reports in
     the next 14; its column says whether the quality numbers already hold the new report).
   - Every passer, near miss and thesis-book name carries its saved valuation ("הערכה שמורה"). The rejected list is
     `research/rejected.jsonl`.
2. **Triage, in the order of the 15.11 rule:** the business gates already cover beats and revisions, so check guidance
   (raised or held) from the company's own release, then price sanity and world view. For a name whose price fell, say
   why it fell before proposing it. For a near miss on the analyst gates, read whether the miss was in the business or
   in a one-off line of EPS. Never propose a name whose saved valuation reads "לא עוברת".
3. **The best 1-2 go to a deep check,** which is (a), and then to the buy test in both lenses.

## What the tools do not do

- No orders, alerts, watchlists or saved screeners. Broker access is read-only.
- No universe backtests, sweeps or walk-forwards unless the owner asks (06.11.2026). `run_all.py`,
  `collect_universe.py` and `backtest_engine.py` belong to the older cycle.
- The screen is not a valuation and not a buy signal. It decides where research time goes; a passer can still fail the
  buy test on price.
- `lean_check` asks and records. It never edits params or values, and a clean check does not make a forecast right.
- The tracker measures and never changes a rule. Rules change only through `research/RULE_CHANGES_he.md`:
  - a check may be added from one case, as a line in `research/lessons.jsonl`;
  - a modelling rule changes only through a forward test written before the data, with at least 8 scored reports from
    at least 3 companies.
- Values are conditional values on each path, not price forecasts. The scenario probabilities are subjective.
- No technical analysis. Levels, charts and Pine scripts belong to the drawing session, which reads `fundamental.json`.
