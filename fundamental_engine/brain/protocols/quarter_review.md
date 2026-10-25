# Results-day review: normalize, compare with expectations, keep the thesis falsifiable

Use when a company has just reported (trigger `earnings_event`), or when you are
updating an existing thesis with a new quarter. Run `quarter_review` with sourced
figures in one currency. It is a fast, deterministic read. It is not a valuation:
price conclusions still go through value_company, valuation_audit and research_review.

Order of work. These lessons come from comparing human results-day write-ups with
engine output (docs/ANALYST_COMPARISON_2026-10.md):

1. Identity and units. State the security line (local share, ADR, class) and the
   reporting currency. If the issuer reports in another currency, convert at one
   dated rate and say that dollar growth mixes business and FX.
2. Headline versus normalized. List every one-off gain and charge with its
   after-tax amount. Examples: tax-allowance release, equity-stake revaluation,
   asset sale, legal settlement. Remove gains AND add back charges. Recurring
   "one-offs" stay. A pre-tax item needs an explicit tax rate. Adjusted EBITDA
   growing far faster than reported operating income needs an explanation.
3. Operating leverage. Compare opex growth with revenue growth, both reported and
   excluding unusual items. Incremental margin is the change in operating income
   divided by the change in revenue.
4. Cash and liquidity. FCF is OCF minus capex, using the definition as supplied.
   Restricted cash, insurance reserves and customer funds back liabilities; they
   are not spendable liquidity. Net cash uses spendable liquidity only. A heavy
   capex phase lowers FCF; say whether it is temporary and what return it needs.
5. Segments and KPIs. The segment carrying the profit can differ from the segment
   in the story. Record each KPI's basis (active, flow, balance, cumulative). A
   cumulative count, such as products ever opened, is not evidence of activity.
6. Expectations. Price reacts to results versus expectations. Compare guidance
   with the prior guidance and with an attributed consensus. Raising revenue while
   holding profit guidance means management is reinvesting the upside. Without
   expectations data, no overreaction conclusion is possible.
7. Guidance credibility. Compare guidance with outcomes on the same basis. A small
   sample of beats suggests the guidance acts as a floor; it does not license
   assuming future beats. Rate guidance is compared in percentage points.
8. Cycle. If the operating margin is far above its own history, the quarter times
   four is peak earnings, not a normalized base. Value through the cycle.
   With `case.economics` (the econ case subset: its model, KPIs and blocks; company,
   dates and currency come from the quarter case, sources merge with `case.sources`)
   the flag can become `operating_leverage_or_peak` (medium). That happens only when
   gross profit and opex reconcile to operating income, at least 2/3 of the y/y
   margin rise comes from opex falling as a share of revenue, and the model is not
   price-cyclical (never memory_cycle, gpu_vendor or a cyclical card type). Both
   readings are shown. The econ review comes back as `economics`, its flags as
   `econ.<key>`, and its falsifiers join `falsifiers` (bind a threshold with
   `econ {id, scope, field}`). Without `case.economics` nothing changes.
9. Market checks. Show P/E on run rate and on attributed forward EPS. For a dual
   listing, compute the premium to the home-market equivalent; the gap can close.
10. Banks. Price/tangible book means something only against ROTE and cost of
    equity: justified P/TBV = (ROTE - g)/(COE - g), and the ROTE that the price
    requires. Also check excess capital versus target and headroom to the minimum,
    and fair-value marks as a share of equity. COE and g are explicit inputs, never
    defaults.
11. EPS x multiple tables. These are dated sensitivities. Show the implied annual
    return from today's price, and anchor multiples in documented history or peers.
    A multiple taken "from thin air" is flagged.
12. Falsifiers. Keep the holder's kill criteria as numeric thresholds, for example
    R&D intensity, gross margin, growth floor or ROTE. Report intact, weakening or
    broken every quarter. A broken falsifier calls for a re-underwrite, not an
    explanation.

What not to copy from write-ups: momentum price targets ("will reach X this
quarter"), sell levels set by hype, certainty about negotiations ("odds are near
zero"), personal product anecdotes as demand evidence, insurance reserves counted
as liquidity, and a peak quarter annualized and called cheap. Separate the quality
of the business, the price and suitability (volatility tolerance is the holder's,
not the company's).

Output: by default one short Hebrew paragraph (the `text` field), with the flags and
next-quarter watch list behind it. The figures are research indication only.
