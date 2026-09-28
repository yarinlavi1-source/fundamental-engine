# Eye-level Hebrew verdicts (plain_language)

Yarin's standing preference: the final answer must be understandable without finance
training. Raw figures ("revenue was 712M") do not help him. Every material number is
translated into a judgement — good / average / weak / worrying — and a sentence that
says WHY in everyday words. The full research discipline still applies underneath;
this packet changes how the result is told, never what the evidence supports.

## Execution

1. Finish the research loop (dossier, research_review, annual valuation when value is
   asked). Collect 3–5 fiscal years of reported revenue, gross profit, operating
   income, net income, operating cash flow, capex and diluted shares, plus current
   unrestricted cash and debt. Prefer AV/FMP structured statements per
   session_capabilities; keep source_ids per row.
2. Call `plain_verdict` with `plain_version:1`, company, ticker, as_of, currency,
   archetype, stage (mature/growth/emerging), history (oldest first), balance and the
   executed `valuation_case` (the same input given to value_company). Pass
   `research_status` = the latest research_review status. The tool executes the
   valuation itself; never hand-type values into the plain answer.
3. Use the returned `text` as the skeleton and add what only research can add: what
   the company actually does, who pays it, and why the grades look the way they do.

Banks, insurers and REITs are refused by the tool (they need capital/NAV metrics);
explain them in the same plain style from the dedicated valuation instead.

## How to write the answer

Order — shortest path to a clear picture:

1. **שורה תחתונה** — one light and one short call (e.g. "🟢 מעניין מאוד", "🟠 עסק טוב
   אבל יקר"), then one or two sentences why.
2. **מה החברה עושה** — like explaining to a friend: what it sells, to whom, how it earns
   on each sale, what makes customers come back. No jargon.
   **איזה סוג חברה זו** — the tool's life-cycle stage and Lynch category in one plain
   sentence, what decides value for this type, and any acquisition/cycle flag.
3. **התמונה בארבע שורות** — quality, growth, financial strength, price vs value.
4. **מה טוב ומה לא** — the tool's table; next to every item a label, never a bare number.
5. **ההסבר** — one short paragraph per item: what it means for the owner of a share.
6. **הערכת שווי** — first say which lane (classic by the numbers, or growth lane
   "does it deserve a premium") and why; for the growth lane show momentum signals,
   the risk/reward bet, the analyst-multiple view and what the price requires.
   Then — today's value and each year to 2030 (bear/base/bull, fixed current
   quote). Say in words: "המחיר הוא בערך חצי מהשווי", "בתרחיש הרע יורדים בערך רבע".
7. **מה יכול להשתבש** — the strongest counter-thesis in two or three plain sentences.
8. **מה לבדוק הלאה** — the next milestone that would prove or break the story.
9. **כמה לסמוך על זה** — the tool's confidence plus research gaps.

Style rules:

- Short sentences. Everyday words: "מכירות" not "הכנסות מוכרות", "כסף מזומן שנשאר"
  not "FCF". If a technical term is unavoidable, explain it in parentheses once.
- Prefer comparisons over figures: "פי 2", "בערך שליש", "מכל 100 דולר נשארים 15",
  "הקופה מחזיקה בערך שנתיים". Absolute amounts only where needed (prices, values).
- Always say direction and meaning: not "margin 12%" but "העסק מרוויח, אבל לא הרבה —
  ולשמחתנו זה משתפר".
- Be decisive where the evidence is decisive and say plainly where it is not.
- A research indication, not an order: do not tell him to buy/sell, do not promise
  returns, and never present future values as predicted exchange prices.
- Growth companies: losses and cash burn are graded against stage (is the loss
  shrinking, how long does the cash last) — do not call a promising early company bad
  only because it is not yet profitable, and do not call it cheap without a valuation.

## Honesty limits

Grades are transparent rules of thumb over supplied reported numbers, adjusted by
economics (software/platform bars are higher than factories or retailers). They do
not verify the numbers, replace research_review, or measure future accuracy. If the
valuation is unreviewed, demo, funding-blocked or mostly terminal value, say so in
plain words next to the verdict.
