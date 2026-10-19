# The single decision path (v0.11)

Use this path whenever the user asks whether a company is attractive, what it is worth, or how
it compares with alternatives. Earlier tools remain for detail work; the decision comes only from
pipeline.run -> decision.decide.

1. Collect: `collection_plan(ticker, as_of)` lists the connector calls and the raw file name for
   each payload. Save payloads verbatim under runs/<TICKER>_<as_of>/raw/ (gitignored) with a
   MANIFEST (retrieval time, sha256, denials). Never retype numbers; copy saved tool-result files.
   Plan-tier denials and rate limits are recorded once, not retried in loops.
2. Build the FactBook: `factbook_from_run_dir(raw_dir, company, as_of)`; add qualitative evidence
   with facts.add_qualitative (reported / guidance / opinion / assumption, with source ids). Read
   primary filings for anything material that structured data cannot settle (converts, events after
   the balance sheet, segment definitions) and record what was read directly.
3. Run `pipeline.run(fb, inputs)`. Inputs carry the analyst's judgement, always with reasons and
   evidence ids: model-card answers and dimension assessments, forecast overrides, decision overrides.
   An override without a reason is rejected; no override lifts a disqualifying gate.
4. Independent review: a reviewer who did not write the thesis reads the casefile and the sources,
   then signs `inputs['reviewer']` (assumption reviews, findings with severity). Without it the
   result is a labelled draft and the price claim is withheld.
5. Report: render.markdown(casefile) for the full report, render.hebrew_paragraph for the default
   answer. Keep verified data, vendor estimates, model assumptions and calculations labelled.
6. Track: monitor.thesis_from_casefile + monitor.freeze write the forecasts to the ledger at the time
   they are made; monitor.check compares new filings with the thesis and alerts only on material
   changes.

Discovery (screen.py) and the portfolio layer (portfolio.py) use the same FactBook/casefile objects.
Portfolio data is read-only and private: it is analysed in the session and never written to Git.
