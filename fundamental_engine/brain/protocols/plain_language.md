# Simple investment conclusion — default delivery

Yarin's latest instruction (2026-10-20) for every valuation: one clear table in plain Hebrew words and a few short,
simple explanations; calculations and business detail stay behind the scenes (in the casefile). The pipeline writes it
as `out/table_he.md` (`render.hebrew_table`): price, fair value (the base value at the company's own cost of equity) and
the gap, the bear / bull scenario (דובי / שורי), the buy-below price for the owner's required return (where the call is made), expected annual
return against the named market return (the engine-consistent one from a calibration file; otherwise the generic
risk-free + ERP assumption, said so), the expected price path with its walk-forward accuracy band (an estimate, never a
target), the conditional five-year value, business quality, how rare the forecast growth is, what today's price assumes,
the main risk (the analyst's evidenced `analyst_sheet.key_risk` in plain words), confidence and the call. A draft says it
is a draft. The scenario words are the owner's (25.10.2026): bear = דובי, base = בסיסי, bull = שורי, never 'סביר'
(likely), 'רע' or 'טוב'; the yearly table is '| מועד | דובי | בסיסי | שורי |' and the fair value is 'שווי הוגן (תרחיש
בסיסי)'. Every scenario price carries its condition: under the yearly table one short line per scenario ('דובי = אם …',
condition only); the dated reasons ('אם …, אז ב־2028: הכנסות …, רווח תפעולי …% → שווי …') stay behind the scenes in
out/scenarios_he.md (scenario_reasons). The bear changes the business, not only the multiple; events need evidence ids,
otherwise they are hypotheses and are called so; without events the condition is the model's own drivers in plain words.
The paragraph below remains for portfolio and discovery summaries.

Yarin's latest instruction (2026-09-28) supersedes older long-report presentation:
complete the full company-specific research privately, then deliver ONE concise
Hebrew paragraph, normally 80–140 words. No headings, scorecards, annual tables,
raw financial statements or technical vocabulary unless explicitly requested.

## Execution

Finish research_plan -> company/sector packets -> evidence -> dossier ->
research_review -> valuation_audit and deterministic valuation before concluding
on price. Load potential and financials when applicable. These stages remain
mandatory internally; brevity must not reduce research depth.

Call plain_verdict with plain_version:1, history, matching valuation_case and full
research_dossier. Its default text is now the short paragraph; detailed_text and
structured metrics preserve the audit. response_style:detailed explicitly opts in
to the former report. Never append that report to the paragraph by default.

Supply decision_notes to give the paragraph company-specific meaning:
- potential: {text: short Hebrew causal thesis, observation_ids: [reviewed IDs]}
- risk: {text: strongest material counter-thesis, observation_ids: [reviewed IDs]}
- milestone: {text: next discriminating business milestone, observation_ids: [IDs]}
Each text is at most 25 words, one line, without computed valuation figures.
The notes are analyst inferences. Dated references establish traceability, not
semantic correctness; inspect source meaning and contradicting evidence yourself.
Notes never unlock price conclusions or override accounting/funding problems.

## Required paragraph

State whether the price looks attractive for investment, fair, unattractive, or
not yet assessable; one company-specific reason; estimated value TODAY (central
estimate and scenario range); discount against that estimate; conditional value
in about five years and possible price change; strongest risk and confidence.
Do not repeat 'what is a share' explanations or append generic boilerplate.
This is a research opinion, not a trade instruction or a suitability assessment.

Discount = 1 - price / value. Upside = value / price - 1. They differ.
For price 80 and value 100, discount is 20%, upside is 25%.
Future conditional values are not today's values. Discount future exit prices
using justified cost of equity and actual elapsed time. Do not confuse WACC with
cost of equity or manufacture a technology multiple to reach a desired answer.
Never claim to know an exact true value or promise how much a stock will rise.

Use an explicit model boundary at the fifth anniversary or next year-end (at
most 100 days later), label the actual date. For September 2026, December 2030
is NOT five years; model through September/December 2031. Extend operating and
funding forecasts, do not extrapolate the final price or force terminal maturity.
If no reviewed five-year boundary exists, say the five-year estimate is missing.
Price changes exclude dividends; use total-return calculations when dividends matter.

## Potential without reliable valuation

Research early companies even without profit, free cash flow or a low multiple.
Say 'יש פוטנציאל בגלל [evidence-backed mechanism], אבל עדיין אין בסיס אמין
לשווי ולכן לא ניתן לקבוע שהמחיר זול; מה שיכריע הוא [milestone]'.
A short history or incomplete valuation does not make the business bad. A promising
story does not prove a discount. Carry funded scale, dilution and substitution risk.

Banks/insurers/REITs remain outside plain_verdict's operating-margin grading.
Use their dedicated executed/reviewed valuation and compose this same paragraph
from it; do not relabel them to force the generic tool, or claim this renderer has
implemented a new bank valuation. When valuation is unavailable, deliver a useful
qualitative conclusion and say which business milestone would enable pricing.
Demo/unreviewed/funding-blocked cases cannot display an approved discount.

Confidence (v0.9.2): the paragraph ends with the level and its first reason, e.g.
'רמת הביטחון בהערכה: בינוני — נתוני המאזן לא נקראו ישירות מהדוח עצמו'. Do not
replace the engine's level with a higher one; you may explain a lower one.
