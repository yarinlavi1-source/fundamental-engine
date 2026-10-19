"""Casefile rendering: one complete markdown report and one plain Hebrew paragraph.

Why: the owner reads one short Hebrew paragraph by default (brain/protocols/plain_language.md) while
the full audit trail must stay one step away. Both views are rendered from the same casefile
(pipeline.run output: company, as_of, qa, an, card, fc, val, rk, rv, dc), so the paragraph can never
state a number the report does not show. Nothing is recomputed here except presentation arithmetic
(rounding, discount = 1 - price/value); every figure carries its provenance class where practical,
ranges are shown instead of single points, and rounding avoids fake precision.

A withheld price claim (unreviewed draft, demo, forensic red flag) removes value, discount and
five-year figures from the paragraph and says so plainly; the report still shows them, labelled draft.
"""

PRICE_VERDICTS = ('attractive_at_price', 'quality_but_expensive', 'index_preferable')
CALL_HE = {
    'attractive_at_price': 'לפי הניתוח, ההשקעה נראית אטרקטיבית במחיר הנוכחי',
    'quality_but_expensive': 'עסק איכותי, אבל במחיר הנוכחי ההשקעה לא נראית אטרקטיבית מספיק',
    'index_preferable': 'במחיר הנוכחי קרן מדד רחבה נראית חלופה עדיפה',
    'interesting_not_yet_valuable': 'יש כאן פוטנציאל, אבל עדיין אין בסיס אמין לשווי ולכן לא ניתן לקבוע אם המחיר זול',
    'promising_funding_conditional': 'העסק נראה מבטיח, אבל ההשקעה תלויה במימון חיצוני שעוד לא הובטח',
    'insufficient_evidence': 'אין עדיין מספיק ראיות כדי לקבוע אם כדאי להשקיע',
    'avoid': 'הייתי נמנע כרגע מהשקעה',
}
RISK_HE = {'competition': 'תחרות שתשחק מחירים או נתח שוק', 'funding': 'צורך במימון חיצוני ודילול בעלי המניות',
           'dilution': 'דילול בעלי המניות', 'leverage': 'חוב גבוה ביחס לרווח', 'accounting': 'איכות הדיווח החשבונאי',
           'customer_concentration': 'תלות במספר קטן של לקוחות גדולים', 'technology': 'שינוי טכנולוגי שיחליש את המוצר',
           'technology_ai': 'כלי בינה מלאכותית שיחלישו את הצורך במוצר', 'regulation': 'שינוי רגולציה',
           'cyclical': 'ירידה מחזורית בביקושים', 'execution': 'ביצוע חלש של התוכנית', 'valuation': 'מחיר שכבר מגלם הצלחה רבה',
           'governance': 'ממשל תאגידי חלש', 'geopolitics': 'סיכון גאופוליטי', 'demand': 'האטה בביקושים',
           'margin': 'שחיקה ברווחיות', 'substitutes': 'תחליפים למוצר', 'moat': 'יתרון תחרותי שנשחק', 'switching_costs': 'לקוחות שיכולים לעבור בקלות',
           'pricing_power': 'כוח תמחור חלש', 'retention': 'נטישת לקוחות', 'market_size': 'שוק מוגבל בגודלו', 'management': 'איכות ההנהלה',
           'capital_allocation': 'הקצאת הון לא יעילה', 'coverage': 'כיסוי ריבית חלש', 'refinancing': 'צורך קרוב במחזור חוב',
           'runway': 'שריפת מזומנים מול הקופה הקיימת', 'sbc': 'דילול מתגמול במניות', 'share_growth': 'גידול מתמשך במספר המניות',
           'convertibles': 'דילול מאג"ח להמרה', 'cash_conversion': 'המרה חלשה של רווח למזומן', 'beneish': 'סימני אזהרה חשבונאיים',
           'altman': 'סימני מצוקה פיננסית', 'piotroski': 'הרעה ברוב מדדי האיתנות הפיננסית', 'external_funding': 'צורך בהון חיצוני',
           'financing_plan': 'תלות במימון חיצוני', 'capex_plan': 'תוכנית השקעות גדולה בהרבה מההיסטוריה',
           'capex_below_history': 'השקעות נמוכות מההיסטוריה שמייפות את התזרים', 'stress_funding_gap': 'פער מימון בתרחישי לחץ',
           'terminal_share': 'רוב השווי מגיע משנים רחוקות', 'wide_range': 'פער רחב מאוד בין התרחישים',
           'price_implies_growth': 'המחיר מגלם צמיחה מהירה מזו שהוכחה', 'growth': 'צמיחה איטית מזו שהמחיר דורש',
           'mature_margin': 'רווחיות בוגרת נמוכה מזו שהמחיר דורש', 'unfunded_plan': 'תוכנית ללא מימון מובטח',
           'equity_impaired': 'התחייבויות שעולות על שווי העסק', 'manipulation_signal': 'סימן לניפוח רווחים',
           'distress': 'מצוקה פיננסית וצורך בהון חדש'}
SEVERITY = {'high': 0, 'medium': 1, 'low': 2}
PROVENANCE = [('verified', 'reported fact confirmed by two independent routes (second source, or filing value plus a passing identity/continuity check)'),
              ('reported', 'reported fact from one filing-derived source (or a market feed for the price), not yet confirmed'),
              ('vendor_estimate', 'vendor-normalized or consensus number'),
              ('model_assumption', 'chosen by the engine or the analyst; has a rationale and a falsifier'),
              ('calculation', 'deterministic function of the above; inputs listed')]
FIELD_CLASS = {'verified': 'verified', 'reported': 'reported', 'single_source': 'reported', 'stale': 'reported',
               'conflict': 'unresolved', 'missing': 'missing'}


def _n(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _ps(v):
    """Per-share value: 3 significant figures at most (no fake precision)."""
    if not _n(v):
        return 'n/a'
    return f'{v:,.0f}' if abs(v) >= 100 else f'{v:,.1f}' if abs(v) >= 10 else f'{v:,.2f}'


def _mm(v):
    return f'{v / 1e6:,.0f}' if _n(v) else 'n/a'


def _p(v, d=1):
    return f'{v * 100:.{d}f}%' if _n(v) else 'n/a'


def _x(v):
    return f'{v:.1f}x' if _n(v) else 'n/a'


def _cell(v):
    if v is None:
        return 'n/a'
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, float):
        return '0' if v == 0 else _p(v) if abs(v) < 1.5 else f'{v:,.4g}' if abs(v) < 1e5 else _mm(v) + 'm'
    if isinstance(v, (list, tuple)):
        if v and all(_n(x) for x in v):
            return '→'.join(_cell(float(x)) for x in (v if len(v) <= 3 else (v[0], v[-1])))
        return ', '.join(_cell(x) for x in v)
    if isinstance(v, dict):
        return '; '.join(f'{k}: {_cell(x)}' for k, x in v.items())
    return str(v).replace('|', '/').replace('\n', ' ')


def _table(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '|'.join('---' for _ in headers) + '|']
    out += ['| ' + ' | '.join(_cell(c) if not isinstance(c, str) else c.replace('|', '/').replace('\n', ' ') for c in r) + ' |' for r in rows]
    return out


def _ids(xs):
    return ', '.join(map(str, xs)) if xs else '-'


# ---------------------------------------------------------------- Hebrew paragraph

def _risk_he(r):
    if r.get('description_he'):
        return r['description_he']
    for token in reversed(str(r.get('id') or '').split('.')):
        if token in RISK_HE:
            return RISK_HE[token]
    return RISK_HE.get(r.get('category'))


def _top_risk(cf, dc):
    """Strongest risk in Hebrew: disqualifying first, then evidenced (assessed) before open questions, then
    severity. Data limits are a confidence matter, not the business risk. An unassessed item is named as an
    open question, never as an evidenced risk."""
    for x in (dc.get('gates') or {}).get('disqualifying') or []:
        if _risk_he(x):
            return _risk_he(x)
    reg = [r for r in ((cf.get('rk') or {}).get('register') or []) if isinstance(r, dict) and r.get('class') != 'accepted_priced'
           and r.get('category') != 'data' and _risk_he(r)]
    reg.sort(key=lambda r: (r.get('class') != 'disqualifying', r.get('assessed') is False, SEVERITY.get(r.get('severity'), 3)))
    if not reg:
        return None
    r = reg[0]
    return _risk_he(r) + (' (שאלה פתוחה שעוד לא נבדקה לעומק)' if r.get('assessed') is False else '')


def hebrew_paragraph(casefile):
    """ONE plain Hebrew paragraph (normally 80-140 words): the call, one reason, value today and discount,
    conditional five-year value, the key risk and the confidence. Future values are conditional estimates."""
    cf = casefile or {}
    dc = cf.get('dc') or {}
    v = dc.get('verdict')
    company = dc.get('company') or cf.get('company') or {}
    name = company.get('name') or company.get('ticker') or 'החברה'
    if not v:
        return f'{name}: שלב ההחלטה לא רץ, ולכן אין כאן מסקנת השקעה. יש להשלים את שלבי הניתוח ולהריץ מחדש.'
    code, withheld = v['code'], v.get('price_claim_withheld')
    view, er = dc.get('valuation_view') or {}, dc.get('expected_return') or {}
    cur = (dc.get('price') or {}).get('currency') or company.get('currency') or ''
    price, base, bear, bull = view.get('price'), view.get('base'), view.get('bear'), view.get('bull')
    parts = []
    if code in PRICE_VERDICTS and withheld:
        why = ' '.join(v.get('withheld_reasons') or [])
        state = ('בדיקות הדוחות הדליקו דגל אדום שעוד לא הוסבר' if 'forensic' in why else 'החישוב נעשה על נתוני הדגמה' if 'demo' in why
                 else 'הניתוח עדיין טיוטה שטרם עברה בדיקה בלתי תלויה')
        parts.append(f'{name}: {state}, ולכן אינני קובע עדיין אם המניה אטרקטיבית במחיר הנוכחי.')
        parts.append(dc.get('business_reason_he', '') + '.')
        parts.append('בשלב זה אינני מציג שווי, הנחה או ערך לחמש שנים כמסקנה.')
    else:
        parts.append(f'{name}: {CALL_HE[code]}.')
        parts.append('הסיבה העיקרית: ' + v['reason_he'] + '.')
        if code not in PRICE_VERDICTS:
            parts.append('מה שכן אפשר לומר כרגע: ' + dc.get('business_reason_he', '') + '; עם זאת, אין כאן הערכת שווי או הנחה כמסקנה.')
    show = code in PRICE_VERDICTS and not withheld and _n(price) and _n(base) and base > 0
    if show:
        disc = 1 - price / base
        gap = (f'הנחה של כ־{disc:.0%} מול השווי המרכזי' if disc >= .005 else
               f'מחיר גבוה בכ־{-disc:.0%} מהשווי המרכזי' if disc <= -.005 else 'בערך השווי המרכזי')
        rng = f' (טווח תרחישים {_ps(bear)}–{_ps(bull)})' if _n(bear) and _n(bull) else ''
        parts.append(f'השווי המוערך היום הוא כ־{_ps(base)} {cur}{rng} מול מחיר {_ps(price)}: {gap}.')
        five = view.get('five_year')
        if five and _n(five.get('base_value')):
            ch = five['price_change_excluding_dividends']
            div = ' (לא כולל דיבידנדים)' if (five.get('cumulative_dividends_base') or 0) > .01 * price else ''
            parts.append(f"אם תרחיש הבסיס יתממש, השווי ב־{five['date']} עשוי להיות כ־{_ps(five['base_value'])}, "
                         f"{'עלייה' if ch >= 0 else 'ירידה'} של כ־{abs(ch):.0%} מהמחיר כעת{div}; זו הערכה מותנית, לא תחזית מחיר.")
        else:
            parts.append('עדיין אין הערכת שווי מבוססת לחמש שנים מלאות מהיום.')
    risk = _top_risk(cf, dc)
    if risk:
        parts.append(f'הסיכון המרכזי: {risk}.')
    elif show and _n(bear) and bear < price:
        parts.append(f'הסיכון המרכזי: בתרחיש השמרני השווי היום כ־{_ps(bear)}, נמוך בכ־{1 - bear / price:.0%} מהמחיר.')
    elif show and _n(bear):
        parts.append(f'הסיכון המרכזי: שהתחזית לא תתממש; גם התרחיש השמרני (כ־{_ps(bear)}) אינו נמוך מהמחיר, אבל הוא אינו המקרה הגרוע ביותר האפשרי.')
    else:
        parts.append('הסיכון המרכזי: שהצמיחה והרווחיות לא יתממשו כפי שהונח.')
    if code in PRICE_VERDICTS and withheld:
        parts.append('מה ישנה את התמונה: ' + ('הסבר מתועד בראיות לדגל האדום' if 'forensic' in why else 'הרצה על נתוני חברה אמיתית'
                                               if 'demo' in why else 'השלמת בדיקה בלתי תלויה של הנתונים וההנחות') + '.')
    elif v.get('what_would_change_it_he') and code != 'attractive_at_price':
        parts.append('מה ישנה את התמונה: ' + v['what_would_change_it_he'] + '.')
    conf = dc.get('confidence') or {}
    rs = conf.get('reasons_he') or []
    parts.append('רמת הביטחון בהערכה: ' + conf.get('level_he', 'לא ידועה') + (f' — {rs[0]}.' if rs else '.'))
    return ' '.join(p for p in parts if p and p != '.').replace('..', '.').replace('\n', ' ')


# ---------------------------------------------------------------- markdown report

def _header(cf, dc):
    c = dc.get('company') or cf.get('company') or {}
    pr = dc.get('price') or {}
    out = [f"# {c.get('name') or c.get('ticker', 'Company')} ({c.get('ticker', '?')}) — investment casefile", '',
           f"- Information cutoff (as_of; nothing published later is used): **{cf.get('as_of') or dc.get('as_of')}**",
           f"- Price: **{_ps(pr.get('price'))} {pr.get('currency') or ''}** ({pr.get('price_kind') or 'n/a'}, {pr.get('price_as_of') or 'n/a'}, "
           f"source {pr.get('price_source') or 'n/a'}) [reported, market feed]",
           f"- Engine {cf.get('engine_version', 'n/a')}; casefile sha256 `{(cf.get('casefile_sha256') or 'n/a')[:16]}`; "
           f"FactBook sha256 `{(cf.get('factbook_sha256') or 'n/a')[:16]}`"]
    st = cf.get('stages') or {}
    if st:
        out.append('- Stages: ' + ', '.join(f'{k} {v}' for k, v in st.items()))
    for e in cf.get('errors') or []:
        out.append(f"- Stage error ({e.get('stage')}): {e.get('error')}")
    return out + ['']


def _verdict_box(dc):
    v, m = dc.get('verdict') or {}, dc.get('mechanical_verdict') or {}
    conf = dc.get('confidence') or {}
    out = ['## Verdict', '', f"> **{v.get('label_he', '')}** (`{v.get('code')}`) — {v.get('status', 'n/a')}; confidence **{conf.get('level', 'n/a')}**",
           '>', f"> {v.get('reason', '')}", '>']
    if v.get('price_claim_withheld'):
        out += ['> Price claim withheld: ' + '; '.join(v.get('withheld_reasons') or []), '>']
    pfh = v.get('price_for_hurdle') or {}
    if pfh.get('value') is not None:
        out += [f"> Price at which the base IRR equals the {_p(v.get('hurdle_used'))} hurdle: **{_ps(pfh['value'])}** "
                f"({_p(pfh.get('change_from_price'), 0)} vs price) [calculation]", '>']
    out += ['> What would change it:'] + [f'> - {x}' for x in v.get('what_would_change_it') or []]
    out += ['', 'Confidence reasons: ' + ('; '.join(conf.get('reasons') or []) or 'none') + f" ({conf.get('rule', '')})"]
    if m and m.get('code') != v.get('code'):
        out += ['', f"Mechanical verdict: `{m.get('code')}` — {m.get('reason')}"]
    for o in dc.get('overrides') or []:
        out.append(f"- Override {o.get('target')} -> {o.get('value')}: **{o.get('status')}** ({o.get('reason')}); "
                   f"justification: {o.get('justification')}; evidence: {_ids(o.get('evidence_ids'))}")
    return out + ['']


def _assessments(dc):
    A = dc.get('assessments') or {}
    names = {'business_quality': '1. How good is the business', 'trajectory': '2. State and trajectory',
             'price_attractiveness': '3. Attractiveness at today\'s price', 'risk_and_error': '4. Risks and size of possible error'}
    out = ['## The four questions (assessed separately, never averaged)', '']
    for k, title in names.items():
        a = A.get(k) or {}
        ov = f" (override; mechanical {a['mechanical_level']})" if a.get('override') else ''
        out += [f"### {title} — **{a.get('level', 'unknown')}**{ov}", '']
        if a.get('level_semantics'):
            out.append(f"_Level meaning: {a['level_semantics']}_")
        out += [f'- {r}' for r in a.get('reasons') or []]
        ev = [e.get('metric') or e.get('dimension') or e.get('risk_id') or e.get('flag') for e in a.get('evidence') or [] if isinstance(e, dict)]
        ev = [e for e in dict.fromkeys(ev) if e]
        if ev:
            out.append('- Evidence refs: ' + ', '.join(map(str, ev)))
        out.append('')
    out += ['### Supporting assessments (for ranking)', '']
    out += _table(['assessment', 'level', 'main reasons'],
                  [[k, (A.get(k) or {}).get('level', 'unknown'), '; '.join((A.get(k) or {}).get('reasons', [])[:3])]
                   for k in ('growth_quality', 'financial_strength', 'management_capital_allocation', 'data_uncertainty')])
    return out + ['']


def _expected(dc):
    er = dc.get('expected_return') or {}
    idx = er.get('index_expected_return') or {}
    out = ['## Expected return at today\'s price', '', f"[calculation] {er.get('semantics', '')}", '']
    st = er.get('irr_status') or {}

    def irr(s):
        b = (st.get(s) or {}).get('bound')
        return _p(er.get(f'{s}_irr')) if er.get(f'{s}_irr') is not None else f"{b['type']} {_p(b['rate'])}" if b else 'n/a'
    out += _table(['scenario', 'IRR', 'status'], [[s, irr(s), (st.get(s) or {}).get('status', 'n/a')] for s in ('bear', 'base', 'bull')])
    rng = f" (range {_p(idx.get('low'), 0)}–{_p(idx.get('high'), 0)})" if _n(idx.get('low')) and _n(idx.get('high')) else ''
    out += ['', f"- Index expected return: **{_p(idx.get('value'))}**{rng} [{idx.get('provenance')}; {idx.get('source')}] — {idx.get('basis')}",
            f"- Risk premium {_p(er.get('risk_premium'), 0)} -> hurdle **{_p(er.get('hurdle'))}** ({er.get('hurdle_rule')}) [model_assumption]",
            f"- Owner's 14% aspiration (comparison only, not the hurdle): base IRR {'meets' if er.get('meets_owner_aspiration_14pct') else 'does not meet' if er.get('meets_owner_aspiration_14pct') is False else 'cannot be compared to'} it"]
    d = er.get('decomposition') or {}
    if d.get('status') == 'computed':
        out += ['', f"Base return to {d['date']} ({d['years']:.1f} years) if the stock trades at the base value then [calculation]:", '']
        out += _table(['component', 'annualised', 'basis'],
                      [['business growth', _p(d['business_growth']), f"{d['metric']}: {_ps(d['anchor']['value'])} ({d['anchor']['basis']}) -> {_ps(d['end']['value'])} ({d['end']['basis']})"],
                       ['multiple change', _p(d['multiple_change']), f"{_x(d['current_multiple'])} today -> {_x(d['exit_multiple'])} implied at the boundary"],
                       ['cash yield', _p(d['cash_yield']), 'base-case distributions (dividends or equivalent buybacks)'],
                       ['total', _p(d['total_annualized']), '(1+g)(1+m)(1+c) - 1']])
        out += ['', f"Formula: {d['formula']}"]
    else:
        out += [f"- Return decomposition unavailable: {d.get('reason', 'n/a')}"]
    return out + ['']


def _card(cf):
    card = cf.get('card')
    out = ['## Business understanding (model card)', '']
    if not card:
        return out + ['Model card unavailable.', '']
    g = lambda *p: 'n/a' if _dig(card, *p) is None else _dig(card, *p)
    out += [f"- Business model: **{g('business_model', 'primary')}** (secondary: {_ids(g('business_model', 'secondary'))}); "
            f"reasons: {'; '.join(map(str, g('business_model', 'reasons') or []))}",
            f"- Stage: **{g('stage', 'value')}** (computed {g('stage', 'computed')}; override {g('stage', 'override') or 'none'})",
            f"- Cyclical: {g('cyclical', 'value')}; capital intensity: {g('capital_intensity', 'value')}; funding: **{g('funding', 'value')}**",
            f"- Playbooks: {_ids(card.get('playbooks'))}",
            f"- Valuation method: **{g('valuation', 'method')}** ({g('valuation', 'archetype')}) — {g('valuation', 'why')}; "
            f"cross-checks: {_ids(g('valuation', 'cross_checks'))}",
            f"- Completeness: **{g('completeness', 'status')}**; missing: {_ids(g('completeness', 'missing'))}", '']
    if card.get('driver_tree'):
        out += _table(['driver', 'why', 'metric', 'data'], [[d.get('driver'), d.get('why'), d.get('metric'), d.get('data_status')] for d in card['driver_tree']]) + ['']
    if card.get('understanding'):
        out += _table(['question', 'critical', 'status', 'answer', 'evidence'],
                      [[q.get('question'), q.get('critical'), q.get('status'), q.get('answer') or '-', _ids(q.get('evidence_ids'))] for q in card['understanding']]) + ['']
    dims = card.get('dimensions') or {}
    if dims:
        out += _table(['dimension', 'assessment', 'source', 'evidence', 'note'],
                      [[k, d.get('assessment'), d.get('source'), _ids(d.get('evidence_ids')), d.get('note') or '-'] for k, d in dims.items() if isinstance(d, dict)]) + ['']
    return out


def _dig(node, *path):
    for k in path:
        node = node.get(k) if isinstance(node, dict) else None
    return node


def _financials(cf):
    an = cf.get('an')
    out = ['## Financial analysis (last five fiscal years)', '']
    if not an or not an.get('annual'):
        return out + ['Financial analysis unavailable.', '']
    years = sorted(an['annual'])[-5:]
    prev = {y: sorted(an['annual'])[i - 1] if i else None for i, y in enumerate(sorted(an['annual']))}
    rows = []
    for y in years:
        r = an['annual'][y]
        rv, p = r.get('revenue'), (an['annual'].get(prev[y]) or {}).get('revenue') if prev[y] else None
        ratio = lambda a: a / rv if _n(a) and _n(rv) and rv > 0 else None
        ic, oi = r.get('invested_capital'), r.get('operating_income')
        cash = sum(x for x in (r.get('cash'), r.get('investments')) if _n(x)) if _n(r.get('cash')) else None
        rows.append([y, _mm(rv), _p(rv / p - 1) if _n(rv) and _n(p) and p > 0 else 'n/a', _p(ratio(r.get('gross_profit'))),
                     _p(ratio(oi)), _mm(r.get('fcf')), _mm(r.get('fcf_after_sbc')), _p(ratio(r.get('sbc'))),
                     _p(oi * .79 / ic) if _n(oi) and _n(ic) and ic > 0 else 'n/a',
                     f"{r['shares_diluted_wavg'] / 1e6:,.1f}" if _n(r.get('shares_diluted_wavg')) else 'n/a',
                     _mm(cash - r['debt']) if _n(cash) and _n(r.get('debt')) else 'n/a'])
    out += _table(['FY end', 'Revenue m [reported]', 'Growth [calc]', 'Gross margin [calc]', 'Op. margin [calc]', 'FCF m [calc]',
                   'FCF after SBC m [calc]', 'SBC % rev [calc]', 'ROIC* [calc]', 'Diluted shares m [reported]', 'Net cash m [calc]'], rows)
    out += ['', '*ROIC here = operating income x (1 - 21%) / invested capital (year-end); the assessment uses the analysis metric `roic`.', '']
    m = an.get('metrics') or {}
    if m:
        out += _table(['metric', 'value', 'status', 'period', 'formula'],
                      [[k, ('series: see analysis' if isinstance(x.get('value'), (list, dict)) else x.get('value')) if not _n(x.get('value')) or x.get('unit') in ('ratio', None) else (f"{x['value']:,.2f}" if x.get('unit') in ('x', 'years') else _mm(x['value']) + 'm'),
                        x.get('status'), x.get('period'), x.get('formula')] for k, x in m.items() if isinstance(x, dict)]) + ['']
    for f in an.get('flags') or []:
        out.append(f"- Flag ({f.get('severity')}): {f.get('code')} — {f.get('message')}")
    fz = an.get('forensics') or {}
    if fz.get('status') != 'unavailable' and fz:
        out.append('- Forensics: ' + '; '.join(f"{k}: {(v or {}).get('zone') or (v or {}).get('status')}" for k, v in fz.items() if isinstance(v, dict)))
    return out + ['']


def _dataqa(cf):
    qa = cf.get('qa')
    out = ['## Data validation', '']
    if not qa:
        return out + ['Data validation did not run: no price conclusion is possible.', '']
    out += [f"Status: **{qa.get('status')}**; blocking: {_ids(qa.get('blocking'))}; limited: {_ids(qa.get('limited'))}", '']
    f = qa.get('fields') or {}
    out += _table(['critical field', 'status', 'class', 'value', 'as of', 'sources', 'note'],
                  [[k, x.get('status'), FIELD_CLASS.get(x.get('status'), x.get('status')),
                    (_mm(x['value']) + 'm' if abs(x['value']) >= 1e5 else f"{x['value']:,.4g}") if _n(x.get('value')) else 'n/a',
                    x.get('as_of'), _ids(x.get('sources')), x.get('note') or '-'] for k, x in f.items() if isinstance(x, dict)])
    bad = [c for c in qa.get('checks') or [] if c.get('status') == 'fail']
    if bad:
        out += ['', 'Failed checks:'] + [f"- [{c.get('severity')}] {c.get('code')} {c.get('period') or ''}: {c.get('message')}" for c in bad]
    return out + ['']


def _forecast(cf):
    fc = cf.get('fc')
    out = ['## Forecast assumptions', '']
    if not fc:
        return out + ['Forecast unavailable.', '']
    out += _table(['id', 'topic', 'bear', 'base', 'bull', 'kind', 'provenance', 'rationale', 'falsifier', 'sources'],
                  [[a.get('id'), a.get('topic'), *((a.get('values') or {}).get(s) for s in ('bear', 'base', 'bull')), a.get('kind'),
                    a.get('provenance'), a.get('rationale'), a.get('falsifier'), _ids(a.get('source_ids'))] for a in fc.get('assumptions') or []])
    ke = fc.get('cost_of_equity') or {}
    if ke:
        out += ['', f"Cost of equity [model_assumption]: bear {_p(ke.get('bear'))}, base {_p(ke.get('base'))}, bull {_p(ke.get('bull'))}; components: {_cell(ke.get('components'))}"]
    out += [f"- Warning ({w.get('severity', 'n/a')}): {w.get('message') or w.get('code')}" if isinstance(w, dict) else f'- Warning: {w}'
            for w in fc.get('warnings') or []]
    return out + ['']


def _valuation(cf, dc):
    val = cf.get('val') or {}
    res = val.get('result') or {}
    out = ['## Valuation', '']
    if not res:
        return out + [f"Valuation not computed: {val.get('reason') or 'no valuation stage output'}.", '']
    out += [f"Method **{res.get('method')}**, status **{res.get('status')}** (calculation {res.get('calculation_status')}), demo: {res.get('is_demo')}, "
            f"input sha256 `{(res.get('input_sha256') or '')[:16]}`. {res.get('meaning', '')}", '']
    pr = (dc.get('price') or {}).get('price')
    rows = [[r['date'], _ps(r.get('bear')), _ps(r.get('base')), _ps(r.get('bull')), _ps(pr if pr else r.get('current_quote')),
             _p((r['base'] / pr - 1) if _n(r.get('base')) and _n(pr) else r.get('base_gap_vs_quote'), 0)] for r in res.get('annual_values') or []]
    out += _table(['date', 'bear', 'base', 'bull', 'price today (fixed)', 'base / price - 1'], rows)
    out += ['', 'Values are conditional per-share values at each date [calculation from model_assumption inputs], not predicted market prices.']
    five = (dc.get('valuation_view') or {}).get('five_year')
    if five:
        out.append(f"Five-year boundary {five['date']} ({five['years']:.1f} years): base {_ps(five['base_value'])} "
                   f"(range {_ps(five.get('bear_value'))}–{_ps(five.get('bull_value'))}), price change excl. dividends {_p(five['price_change_excluding_dividends'], 0)}.")
    w = [x for x in val.get('what_must_be_true') or [] if isinstance(x, dict)]
    if w:
        hist = {'growth': 'revenue_cagr_3y', 'mature_margin': 'operating_margin'}
        metric = lambda name: ((cf.get('an') or {}).get('metrics') or {}).get(name) or {}
        unit = lambda x, v: _p(v) if x.get('unit') == 'ratio' and _n(v) else f'{v:.3g}' if _n(v) else _cell(v)
        out += ['', 'What must be true for the base case to justify today\'s price [calculation]:', '']
        out += _table(['lever', 'status', 'price requires', 'base case', 'demonstrated', 'meaning'],
                      [[x.get('label') or x.get('lever'), x.get('status'), unit(x, x.get('required')), unit(x, x.get('anchor')),
                        (f"{_p(metric(hist[x['lever']]).get('value'))} ({hist[x['lever']]})" if x.get('lever') in hist and _n(metric(hist[x['lever']]).get('value')) else '-'),
                        x.get('meaning')] for x in w])
    d = val.get('diagnostics') or {}
    band = d.get('discount_rate_band') or {}
    if band:
        out += ['', f"- Discount-rate band (±{_p(band.get('shift'), 0)}): base value {_ps(band.get('value_higher_rate'))}–{_ps(band.get('value_lower_rate'))}; "
                f"price inside band: {band.get('quote_inside_band')}"]
    grid = [c.get('value_today') for c in d.get('sensitivity') or [] if _n(c.get('value_today'))]
    if grid:
        out.append(f'- Sensitivity grid (rate ±2pp, terminal growth ±1pp): base value today {_ps(min(grid))}–{_ps(max(grid))}')
    rp = d.get('reverse_price') or {}
    if rp:
        out.append(f"- Reverse price: {rp.get('status')}" + (f", uniform unit-price multiplier {rp['multiplier']:.2f}" if _n(rp.get('multiplier')) else '') + ' (sensitivity, not market consensus)')
    if (val.get('capital_check') or {}).get('difference') is not None:
        out.append(f"- Fully diluted shares vs model opening shares: {_p(val['capital_check']['difference'])}")
    out += [f'- Warning: {x}' for x in res.get('warnings') or []]
    for s in res.get('scenarios') or []:
        out += [f"- {s['name']} issue: {x}" for x in s.get('issues') or [] if 'Cash is checked at period boundaries' not in x]
    return out + ['']


def _risks(cf):
    rk = cf.get('rk')
    out = ['## Risks and failure scenarios', '']
    if not rk:
        return out + ['Risk assessment unavailable.', '']
    out += _table(['id', 'category', 'class', 'severity', 'in bear case', 'description', 'evidence'],
                  [[r.get('id'), r.get('category'), r.get('class'), r.get('severity'), r.get('in_bear_case'), r.get('description'), _ids(r.get('evidence_ids'))]
                   for r in rk.get('register') or []])
    if rk.get('failure_scenarios'):
        out += ['', 'Failure scenarios:', '']
        out += _table(['name', 'mechanism', 'observable signs', 'value/share', 'loss from price'],
                      [[f.get('name'), f.get('mechanism'), f.get('observable_signs'), _ps(f.get('value_per_share')), _p(f.get('loss_from_price'), 0)]
                       for f in rk['failure_scenarios']])
    dn, er = rk.get('downside') or {}, rk.get('error_size') or {}
    out += ['', f"- Downside: bear value {_ps(dn.get('bear_value'))} (loss {_p(dn.get('loss_to_bear'), 0)}); permanent-loss value "
                f"{_ps(dn.get('permanent_loss_value'))} (loss {_p(dn.get('loss_to_permanent'), 0)}) [calculation]",
            f"- Size of possible error: **{er.get('level', 'n/a')}**; value range {_p(er.get('value_range_rel'), 0)}; terminal share "
            f"{_p(er.get('terminal_share'), 0)}; {'; '.join(map(str, er.get('reasons') or []))}",
            f"- Disqualifying: {_ids(rk.get('disqualifying'))}"]
    return out + ['']


def _review(cf):
    rv = cf.get('rv')
    out = ['## Independent review', '']
    if not rv:
        return out + ['Review did not run: every price claim is a draft.', '']
    m = rv.get('mechanical') or {}
    out += [f"Status: **{rv.get('status')}**; price conclusion allowed: **{rv.get('price_conclusion_allowed')}**"]
    out += [f'- {r}' for r in rv.get('reasons') or []]
    out += [f"- Mechanical: dataqa {m.get('dataqa')}; valuation audit {_dig(m, 'valuation_audit', 'status') or 'n/a'}; "
            f"red team {_dig(m, 'redteam', 'verdict') or 'n/a'}; consistency findings {len(m.get('consistency') or [])}"]
    out += [f"  - {c.get('code')}: {c.get('status')} ({c.get('severity')}) — {c.get('message')}" if isinstance(c, dict) else f'  - {c}'
            for c in m.get('consistency') or []]
    r = rv.get('reviewer')
    if r:
        out.append(f"- Reviewer: {r.get('reviewer') or r.get('name') or 'n/a'} ({r.get('signed_at') or r.get('date') or 'undated'})")
        for f in r.get('findings') or []:
            out.append(f"  - {_cell(f)}")
    else:
        out.append('- No signed reviewer input.')
    return out + ['']


def _limitations(cf, dc):
    lim = list(_dig(cf.get('card') or {}, 'valuation', 'limitations') or [])
    lim += (dc.get('valuation_view') or {}).get('notes') or []
    lim += [f"{e.get('stage')}: {e.get('error')}" for e in cf.get('errors') or []]
    lim += (dc.get('verdict') or {}).get('withheld_reasons') or []
    lim += ['Assessments use stated rules of thumb; a missing input stays unknown, never imputed.',
            'Scenario values are conditional estimates, not predicted prices; the scenario range is not a confidence interval.',
            'The engine audits evidence and arithmetic; it does not establish that sources are true or that forecasts will be met.',
            dc.get('semantics') or '']
    return ['## Limitations', ''] + [f'- {x}' for x in dict.fromkeys(lim) if x] + ['']


def markdown(casefile):
    """Complete readable report of one casefile; sections never silently disappear (an unavailable
    stage is stated). Paragraph first, so the short answer and its audit trail sit together."""
    cf = casefile or {}
    dc = cf.get('dc') or {}
    out = _header(cf, dc)
    out += ['## In one paragraph', '', hebrew_paragraph(cf), '']
    out += _verdict_box(dc) + _assessments(dc) + _expected(dc) + _card(cf) + _financials(cf) + _dataqa(cf)
    out += _forecast(cf) + _valuation(cf, dc) + _risks(cf) + _review(cf) + _limitations(cf, dc)
    out += ['## Provenance legend', ''] + _table(['class', 'meaning'], PROVENANCE) + ['']
    return '\n'.join(out)
