"""Casefile rendering: one complete markdown report and one plain Hebrew paragraph.

Why: the owner reads one short Hebrew paragraph by default (brain/protocols/plain_language.md) while
the full audit trail must stay one step away. Both views are rendered from the same casefile
(pipeline.run output: company, as_of, qa, an, card, fc, val, rk, rv, dc), so the paragraph can never
state a number the report does not show. Nothing is recomputed here except presentation arithmetic
(rounding, discount = 1 - price/value); every figure carries its provenance class where practical,
ranges are shown instead of single points, and rounding avoids fake precision.

The fair value the owner sees is the executed base value at the engine's own cost of equity for the company, with
the scenario range of the same valuation; beside it, the buy-below price for the owner's required return (the price
at which the base IRR equals the decision hurdle, where the verdict is taken), and when that price cannot be computed
the text says so. The five-year figure is at the fair-value rate and states that it assumes the price converges to
that value. The market return every comparison uses is named: the engine-consistent one of a calibration sweep, or
the generic risk-free + ERP assumption, said as such. The expected price path (decision._price_path) is shown with
its walk-forward accuracy band, never as a target.
A marginal verdict says so and on which side it flips. Every draft is labelled as a draft: an unreviewed draft
shows its numbers labelled, never as a conclusion; an unreleased forensic or governance red flag, demo inputs
or a reviewer's rejected assumption withhold value, discount and five-year figures (from structured flags, never
from words inside free text). A result that rests on an accepted analyst override says so, and the mechanical
result is always named as such. An unresolved forecast material issue is named (and withholds every value when the
executed number is not a valuation of this business); a verdict that rests on a priced financing path says it is
conditional on it.
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
           'distress': 'מצוקה פיננסית וצורך בהון חדש',
           'rare_sustained_growth': 'התחזית נשענת על צמיחה מהירה ומתמשכת שמעט חברות משיגות',
           'above_own_history': 'התחזית מניחה צמיחה מהירה בהרבה מזו שהחברה השיגה בעבר',
           'base_rate': 'התחזית נשענת על צמיחה שמעט חברות משיגות',
           'price_requires_revenue': 'המחיר דורש הכנסות גבוהות בהרבה מתרחיש הבסיס',
           'capital_efficiency': 'המחיר דורש יעילות הון גבוהה בהרבה מתרחיש הבסיס'}
# risks that say "the price requires more than the base case": never the main risk next to a price at or below the value shown
PRICE_REQUIRES = ('price_implies_growth', 'price_requires_revenue', 'wmbt')
# Each risk-module gate has its own text: a gate is never described by its category (a red-team failure is not
# "the price already embeds success").
GATE_HE = {'gate.blocked_data': 'נתונים קריטיים חסרים או סותרים, ולכן אין בסיס לשווי',
           'gate.redteam_critical_failure': 'הבדיקה המכנית מצאה כשל קריטי בחישוב השווי, ולכן אין עדיין שווי מאומת',
           'gate.unfunded_plan': 'תוכנית עסקית שתלויה במימון שעוד לא הובטח',
           'gate.equity_impaired': 'התחייבויות שעולות על שווי העסק',
           'gate.manipulation_signal': 'סימן לניפוח רווחים יחד עם המרה חלשה של רווח למזומן',
           'gate.distress': 'מצוקה פיננסית וצורך קרוב בהון חדש',
           'gap.understanding_insufficient': 'העסק עוד לא מובן מספיק כדי להעריך אותו'}
# among risks of the same tier, the business threats the owner asked to see first
PRIORITY_RISKS = ('biz.technology_ai', 'biz.competition', 'biz.substitutes', 'biz.moat', 'biz.retention')
DRAFT_STATE_HE = {'assumption_rejected': 'הבודק הבלתי תלוי דחה הנחה שהחישוב נשען עליה',
                  'primary_evidence_missing': 'הבדיקה המכנית מצאה שנתוני הפתיחה לא נקראו מהדוח עצמו',
                  'underwriting_incomplete': 'הבדיקה המכנית מצאה פערים בביסוס ההנחות',
                  'underwriting_required': 'הבדיקה המכנית מצאה הנחות שעוד לא בוססו',
                  'conditional_valuation': 'הבדיקה המכנית מצאה ששווי הטיוטה מותנה בהנחות שלא נבדקו'}
DRAFT_CHANGE_HE = {'assumption_rejected': 'תיקון ההנחה שהבודק דחה והרצה מחדש',
                   'primary_evidence_missing': 'קריאת נתוני הפתיחה מהדוח עצמו ובדיקה בלתי תלויה',
                   'underwriting_incomplete': 'השלמת ביסוס ההנחות ובדיקה בלתי תלויה',
                   'underwriting_required': 'ביסוס ההנחות ובדיקה בלתי תלויה',
                   'conditional_valuation': 'ביסוס ההנחות ובדיקה בלתי תלויה'}
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


def _hp(v, near=None):
    """Hebrew percent: whole percent unless a comparison within one point needs a decimal."""
    if not _n(v):
        return 'לא ידוע'
    d = 1 if _n(near) and abs(v - near) < .01 else 0
    return f'{v * 100:.{d}f}%'


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


def _dig(node, *path):
    for k in path:
        node = node.get(k) if isinstance(node, dict) else None
    return node


# ---------------------------------------------------------------- Hebrew paragraph

def _risk_he(r, cheap=False):
    """Hebrew name of a register item. cheap: the price is at or below the value shown, so a 'the price requires more'
    risk (and the generic valuation-category text 'the price already embeds success') would contradict the discount."""
    rid = str(r.get('id') or '')
    if rid in GATE_HE:
        return GATE_HE[rid]
    if r.get('description_he'):
        return r['description_he']
    tokens = rid.split('.')
    if cheap and any(t in PRICE_REQUIRES for t in tokens):
        return None
    for token in reversed(tokens):
        if token in RISK_HE:
            return RISK_HE[token]
    if rid.startswith(('gate.', 'gap.')):
        return None                                    # a gate is never described by its category
    if cheap and r.get('category') == 'valuation':
        return None
    return RISK_HE.get(r.get('category'))


def _charged_everywhere(cf):
    """Register ids whose cost every scenario already charges (so they are not 'the main risk'): SBC when the red team
    derived that every scenario expenses it as a cash-equivalent cost or settles it in explicit shares."""
    sbc = _dig(cf.get('rv') or {}, 'mechanical', 'redteam', 'derived_inputs', 'sbc_treatment', 'value')
    return {'dil.sbc'} if sbc in ('expensed', 'diluted') else set()


def _top_risk(cf, dc):
    """Most material risk in Hebrew: disqualifying first; then evidenced governance red flags; then assessed risks by
    severity (AI and competition first among equals); then high-relevance open questions. Data limits and the size of
    possible valuation error (register valuation_error entries: error_size, a confidence matter) are not company risks, and a
    fully priced risk (in the bear path) and a cost every scenario already charges (SBC) are not 'the main risk'. An
    unassessed item is named as an open question, never as an evidenced risk."""
    for x in (dc.get('gates') or {}).get('disqualifying') or []:
        if _risk_he(x):
            return _risk_he(x)
    priced = {p.get('id') for p in dc.get('risk_pricing') or [] if p.get('status') == 'priced'}
    skip = _charged_everywhere(cf) | priced
    view, er = dc.get('valuation_view') or {}, dc.get('expected_return') or {}
    shown = view.get('base') if _n(view.get('base')) else er.get('value_at_required_return')     # the headline fair value
    cheap = _n(shown) and _n(view.get('price')) and view['price'] <= shown
    reg = [r for r in ((cf.get('rk') or {}).get('register') or []) if isinstance(r, dict) and r.get('category') != 'data'
           and not r.get('valuation_error') and r.get('id') not in skip and _risk_he(r, cheap)]
    if not reg:
        return None

    def tier(r):
        open_q = r.get('assessed') is False or r.get('status') == 'unassessed'
        if r.get('class') == 'disqualifying':
            return 0
        if r.get('withhold_price'):
            return 1
        if not open_q and r.get('severity') == 'high':
            return 2
        if open_q and r.get('severity') == 'high' and r.get('id') in PRIORITY_RISKS:
            return 3
        if not open_q and r.get('severity') == 'medium':
            return 4
        return 5 + SEVERITY.get(r.get('severity'), 3)
    reg.sort(key=lambda r: (tier(r), PRIORITY_RISKS.index(r['id']) if r.get('id') in PRIORITY_RISKS else 9, r.get('id') or ''))
    r = reg[0]
    open_q = r.get('assessed') is False or r.get('status') == 'unassessed'
    return _risk_he(r, cheap) + (' (שאלה פתוחה שעוד לא נבדקה לעומק)' if open_q else '')


def _gap_he(price, value, ref):
    d = 1 - price / value
    if not ref:
        return f'הנחה של כ־{d:.0%}' if d >= .005 else f'המחיר גבוה בכ־{-d:.0%}' if d <= -.005 else 'בערך באותה רמה'
    return (f'הנחה של כ־{d:.0%} מול {ref}' if d >= .005 else f'מחיר גבוה בכ־{-d:.0%} מ{ref}' if d <= -.005 else f'בערך {ref}')


def _buy_he(er, base, price, adj='', secondary=True, compact=False):
    """Clause on the buy-below price: the price at which the base IRR equals the owner's required return (where the verdict
    is taken), with today's price against it; when the required return is the company's own rate it is the fair value itself
    and is said once (dropped with secondary=False: no new number). compact: the price only. When it cannot be computed the
    clause says so."""
    pfh, h, ke = er.get('value_at_required_return'), er.get('hurdle'), er.get('model_cost_of_equity')
    req = f'לתשואה הנדרשת שלך{adj} (כ־{_hp(h, ke)} בשנה)' if _n(h) else f'לתשואה הנדרשת שלך{adj}'
    if _n(pfh) and pfh > 0:
        if _n(base) and base > 0 and abs(pfh / base - 1) < .005:
            return f'; זה גם מחיר הקנייה המרבי {req}' if secondary else ''
        if compact:
            return f'; מחיר קנייה מרבי לתשואה שלך{adj} (כ־{_hp(h, ke)}): כ־{_ps(pfh)}' if _n(h) else f'; מחיר קנייה מרבי: כ־{_ps(pfh)}'
        d = 1 - price / pfh if _n(price) else None
        rel = ('' if d is None else f', והמחיר נמוך ממנו בכ־{d:.0%}' if d >= .005 else f', והמחיר גבוה ממנו בכ־{-d:.0%}' if d <= -.005
               else ', בערך המחיר של היום')
        return f'; מחיר הקנייה המרבי {req} הוא כ־{_ps(pfh)}{rel}'
    if (er.get('price_for_hurdle') or {}).get('status') == 'invalid':
        return f'; מחיר הקנייה המרבי {req} לא ניתן לחישוב (התחזית אינה תקפה בשיעור זה)'
    return ''


def _value_he(dc, cur, secondary=True, compact=False):
    """Fair value first: the executed base value at the engine's own cost of equity for the company, with the scenario range
    of the same valuation; then the buy-below price for the owner's required return (_buy_he), where the verdict is taken.
    secondary=False drops the scenario range and a buy-below clause that only repeats the fair value (a different buy-below
    price stays: it is what the verdict rests on); compact shortens that clause to the price."""
    view, er = dc.get('valuation_view') or {}, dc.get('expected_return') or {}
    price, base, bear, bull = view.get('price'), view.get('base'), view.get('bear'), view.get('bull')
    ke = er.get('model_cost_of_equity') if _n(er.get('model_cost_of_equity')) else view.get('model_cost_of_equity')
    if _n(base) and base > 0 and _n(price):
        r = f', טווח תרחישים {_ps(bear)}–{_ps(bull)}' if secondary and _n(bear) and _n(bull) else ''
        at = f' (בשיעור ההיוון של החברה, כ־{_hp(ke, er.get("hurdle"))}{r})' if _n(ke) else (f' ({r[2:]})' if r else '')
        return (f'השווי ההוגן{at} הוא כ־{_ps(base)} {cur} מול מחיר {_ps(price)}: {_gap_he(price, base, "")}'
                f'{_buy_he(er, base, price, secondary=secondary, compact=compact)}.')
    pfh = er.get('value_at_required_return')
    if _n(pfh) and pfh > 0 and _n(price):
        return f'אין שווי הוגן להצגה{_buy_he(er, None, price)}.'
    return ''


def _five_he(dc, draft=False, compact=False):
    """Five-year base value at the fair-value rate (the engine's own cost of equity, the executed valuation) plus
    distributions (buybacks/dividends), with the total; the change from today assumes the price converges to that value."""
    view = dc.get('valuation_view') or {}
    price = view.get('price')
    m, f = view.get('five_year'), None
    if m:
        f = {'date': m['date'], 'years': m['years'], 'base_value': m['base_value'],
             'cumulative_distributions_base': m.get('cumulative_distributions_base', m.get('cumulative_dividends_base')) or 0.}
    at = 'בשיעור ההיוון של החברה'
    if not f or not _n(f.get('base_value')) or not _n(price):
        why = view.get('five_year_unavailable')
        return 'עדיין אין הערכת שווי מבוססת לחמש שנים מהיום' + ('' if not why else ' (אין בהערכה תאריך קרוב מספיק ליום השנה החמישי)') + '.'
    v, dist = f['base_value'], f.get('cumulative_distributions_base') or 0.
    total = v + dist
    ch = total / price - 1
    yrs = f.get('years') or 5
    ann = (total / price) ** (1 / yrs) - 1 if total > 0 and yrs > 0 else None
    when = f"ב־{f['date']}" + ('' if abs(f['years'] - 5) < .05 else f" (כ־{f['years']:.1f} שנים מהיום)")
    dist_he = (f', ועוד חלוקות (רכישות עצמיות/דיבידנד) של כ־{_ps(dist)} למניה; סך הכול כ־{_ps(total)}' if dist > .005 * price
               else ', ללא חלוקות (רכישות עצמיות/דיבידנד) משמעותיות')
    move = f"{'עלייה' if ch >= 0 else 'ירידה'} של כ־{abs(ch):.0%} מהמחיר כעת" + (f' (כ־{abs(ann):.0%} לשנה)' if ann is not None else '')
    # the change from today assumes a re-rating: the price converges to that value (its annual rate can differ from the base IRR)
    conv = 'ובהנחה שהמחיר יתכנס לשווי ההוגן'
    if compact:                                            # the fair value's rate was just named: not repeated
        return (f'{when}, בתרחיש הבסיס ובהתכנסות המחיר לשווי, השווי כ־{_ps(v)}{dist_he}, {move}'
                + ('.' if draft else '; זו הערכה מותנית, לא תחזית מחיר.'))
    if draft:
        return f'בתרחיש הבסיס, {conv}, {when} השווי {at} כ־{_ps(v)}{dist_he}: {move}.'
    return f'אם תרחיש הבסיס יתממש {conv}, {when} השווי {at} עשוי להיות כ־{_ps(v)}{dist_he}, {move}; זו הערכה מותנית, לא תחזית מחיר.'


def _marginal_he(v):
    """Marginal wording with the side where the call flips (decision._margin_text); after an analyst verdict override it
    is the result before the override that is marginal, and the text says so."""
    m = v.get('margin_detail') or {}
    if m.get('status') != 'marginal':
        return ''
    he = m.get('text_he') or 'שינוי קטן בתשואה הנדרשת משנה את המסקנה'
    if v.get('overridden_from'):
        return f', והתוצאה שלפני הכרעת האנליסט גבולית: {he}'
    return f', אם כי זו הכרעה גבולית: {he}'


MATERIAL_REASON_HE = 'בתחזית נותרו שאלות מהותיות פתוחות'
BUY_MARK_HE = ' (מחיר הקנייה לתשואה הנדרשת)'             # decision._verdict's label on a price that is the buy-below price


def _confidence_he(dc, skip_margin, short=False, skip_material=False):
    """Confidence with its first reason; a reason the paragraph already states (the marginal call, a named material issue)
    is not repeated."""
    conf = dc.get('confidence') or {}
    rs = [r for r in conf.get('reasons_he') or [] if not (skip_margin and r.startswith('ההכרעה גבולית'))
          and not (skip_material and r.startswith(MATERIAL_REASON_HE))]
    first = rs[0].split(';')[0] if rs and short else rs[0] if rs else None
    return 'רמת הביטחון בהערכה: ' + conf.get('level_he', 'לא ידועה') + (f' — {first}.' if first else '.')


MAX_WORDS = 140
GAPS_HE = 'הנחות בלי נתון של החברה'
MIN_WORDS = 80
# The market return each comparison uses, named (decision.COMPARATORS): the engine-consistent one cancels a shared model bias;
# the generic assumption (risk-free + ERP) is built differently from a stock's implied return and is said to be that.
MARKET_HE = {'engine_consistent': 'השוק כולו, באותו מנוע', 'generic_assumption': 'הנחת שוק כללית',
             'user_assumption': 'הנחת השוק שהוזנה'}
MARKET_NOTE_HE = {
    'engine_consistent': '"השוק כולו, באותו מנוע" = התשואה שאותו מנוע מוצא במחירי החברות הגדולות (קובץ כיול), כך שהטיה משותפת '
                         'של המודל מתקזזת בהשוואה.',
    'generic_assumption': '"הנחת שוק כללית" = ריבית חסרת סיכון ועוד פרמיית סיכון למניות, ולא תשואת השוק כפי שאותו מנוע מעריך '
                          'אותה; המנוע שמרני, ולכן ההשוואה הזו מחמירה (קובץ כיול של השוק מתקן זאת).',
    'user_assumption': '"הנחת השוק שהוזנה" = הנחה שסופקה לניתוח, לא תשואת השוק כפי שאותו מנוע מעריך אותה.'}


def _money(v, cur):
    return f'{_ps(v)}$' if cur == 'USD' else f'{_ps(v)} {cur}'


def _horizon(view, n):
    h = ((view or {}).get('horizons') or {})
    return h.get(n) or h.get(str(n))


def lynch_type(casefile):
    """Peter Lynch's company kinds (One Up on Wall Street), read from the model card's route and the 3-year revenue CAGR:
    cyclical, turnaround, young and still losing money, fast grower (>= 20% a year), stalwart (8-20%), slow grower (< 8%).
    Each kind is judged differently (a cyclical looks cheapest at its peak; a fast grower lives on how long growth lasts).
    Without a route (no forecast built) only a loss-making company is typed."""
    cf = casefile or {}
    route = (((cf.get('fc') or {}).get('anchors') or {}).get('route') or {})
    m = ((cf.get('an') or {}).get('metrics') or {})
    g = (m.get('revenue_cagr_3y') or {}).get('value')
    mg = (m.get('operating_margin') or {}).get('value')
    if not route:
        return 'צעירה, עדיין מפסידה' if _n(mg) and mg < 0 else None
    if route.get('stage') == 'turnaround':
        return 'בהתאוששות'
    if route.get('cyclical'):
        return 'מחזורית'
    if _n(mg) and mg < 0:
        return 'צעירה, עדיין מפסידה'
    if _n(g) and g >= .20:
        return 'צומחת מהר'
    if _n(g) and g >= .08:
        return 'יציבה וצומחת'
    return 'צומחת לאט' if _n(g) else None


def hebrew_table(casefile):
    """The owner's valuation format (20.10.2026): one clear table in plain Hebrew words and a few short explanations; the
    calculations and the business detail stay in the casefile. Every row is read from the executed casefile, nothing is
    recomputed: price, fair value (the base value at the engine's own cost of equity for the company) and the gap, the
    bear-bull range of the same valuation, the buy-below price for the owner's required return (where the verdict is taken),
    the expected annual return at today's price against the named market return (engine-consistent, or the generic
    assumption said as such), the expected price path with its walk-forward accuracy band (replaced, when inputs.company_path is supplied, by the
    company's own yearly price table: next-fiscal-year EPS x an evidenced multiple per scenario), the conditional five-year value,
    business quality, how rare the forecast growth is (base rates), what today's price assumes, the main risk (the analyst's
    evidenced key_risk first), confidence and the call. A draft says so; a held valuation shows no value."""
    cf = casefile or {}
    dc = cf.get('dc') or {}
    v = dc.get('verdict') or {}
    company = dc.get('company') or cf.get('company') or {}
    name = company.get('name') or company.get('ticker') or 'החברה'
    view, er = dc.get('valuation_view') or {}, dc.get('expected_return') or {}
    cur = (dc.get('price') or {}).get('currency') or company.get('currency') or ''
    price = view.get('price')
    st = _state(dc) if v else {'hold': True, 'state': 'שלב ההחלטה לא רץ'}
    code, withheld = v.get('code'), bool(v.get('price_claim_withheld'))
    rows = [('מחיר היום', _money(price, cur) if _n(price) else 'לא ידוע')]
    fv = er.get('fair_value') or {}
    fair = fv.get('value') if _n(fv.get('value')) else view.get('base')
    ke = fv.get('cost_of_equity') if _n(fv.get('cost_of_equity')) else view.get('model_cost_of_equity')
    req, pfh = er.get('hurdle'), er.get('value_at_required_return')
    show = bool(v) and code in PRICE_VERDICTS and not st['hold'] and _n(fair) and fair > 0 and _n(price)
    notes = []
    if show:
        gap = price / fair - 1
        rows.append(('שווי הוגן (תרחיש סביר)', _money(fair, cur)))
        rows.append(('המחיר מול השווי', f"{'יקרה' if gap > 0 else 'זולה'} בכ־{abs(gap):.0%}" if abs(gap) >= .03 else 'בערך במחיר השווי'))
        lo, hi = (fv.get('bear'), fv.get('bull')) if _n(fv.get('bear')) and _n(fv.get('bull')) else (view.get('bear'), view.get('bull'))
        if _n(lo) and _n(hi):
            rows.append(('תרחיש רע / תרחיש טוב', f'{_money(lo, cur)} / {_money(hi, cur)}'))
        buy = _n(req) and _n(pfh) and pfh > 0 and abs(pfh / fair - 1) >= .005
        if _n(req) and _n(pfh) and pfh > 0:
            d = price / pfh - 1
            rel = ('בערך המחיר של היום' if abs(d) < .03 else f"המחיר של היום {'גבוה' if d > 0 else 'נמוך'} ממנו בכ־{abs(d):.0%}")
            rows.append((f'מחיר קנייה מרבי לתשואה שאתה דורש ({req * 100:.1f}% בשנה)',
                         _money(pfh, cur) + (f' ({rel})' if buy else ' (זהה לשווי ההוגן)')))
        elif _n(req) and (er.get('price_for_hurdle') or {}).get('status') == 'invalid':
            rows.append((f'מחיר קנייה מרבי לתשואה שאתה דורש ({req * 100:.1f}% בשנה)', 'לא ניתן לחישוב בתשואה זו'))
        irr = er.get('base_irr')
        idx = er.get('index_expected_return') or {}
        mkt = (view.get('market_consistent') or {}).get('market_return')
        if not _n(mkt):
            mkt = idx.get('value')
        kind = idx.get('comparator') or ('generic_assumption' if idx.get('origin') in ('engine_default', 'forecast_cost_of_equity') else None)
        mk_he = MARKET_HE.get(kind, 'השוק כולו')
        if _n(irr):
            vs = ''
            if _n(mkt):
                d = irr - mkt
                vs = (f' ({mk_he}: כ־{mkt * 100:.1f}%; ' + ('בערך כמו השוק)' if abs(d) < .005 else
                                                        f"{'יותר' if d > 0 else 'פחות'} מהשוק בכ־{abs(d) * 100:.1f}% בשנה)"))
            rows.append(('תשואה שנתית צפויה במחיר של היום', f'כ־{irr * 100:.1f}% בשנה{vs}'))
        pp = er.get('expected_price_path') or {}
        if (cf.get('company_path') or {}).get('status') == 'computed':
            pp = {}                       # the company's own EPS x multiple path replaces the universe blend (company_path.py)
        if pp.get('status') == 'computed' and pp.get('rows'):
            cells = ' · '.join((str(r['date'])[:7].replace('-', '/') if r.get('date') else f"שנה {r['years']}") + f": {_money(r['price'], cur)}"
                               for r in pp['rows'])
            rows.append((f"מחיר צפוי למניה (כ־{pp['rate'] * 100:.1f}% בשנה)", cells))
        f5 = view.get('five_year') or {}
        if _n(f5.get('base_value')):
            dist = f5.get('cumulative_distributions_base') or 0.
            rows.append((f"שווי משוער בעוד 5 שנים ({f5.get('date', '')[:4]})",
                         _money(f5['base_value'], cur) + (f' + כ־{_money(dist, cur)} חלוקות לבעלי המניות' if dist > .005 * price else '')))
        notes.append('שווי הוגן = כמה המניה שווה היום אם התרחיש הסביר מתממש, בשיעור ההיוון שהמנוע דורש מהחברה הזו לפי הסיכון שלה'
                     + (f' (כ־{ke * 100:.1f}% בשנה).' if _n(ke) else '.'))
        if buy:
            notes.append(f'מחיר קנייה מרבי = עד איזה מחיר התרחיש הסביר עוד נותן את התשואה שאתה דורש (כ־{req * 100:.1f}% בשנה: תשואת השוק '
                         'ועוד תוספת על הסיכון של מניה בודדת); זו דרישה שלך, לא שווי החברה, ולפיה נקבעת השורה התחתונה.')
        edge = (irr - mkt) if _n(irr) and _n(mkt) and _n(ke) else None
        if edge is not None and gap > .03 and edge > .005:
            notes.append(f'למה "יקרה" אבל מניבה יותר מהשוק: השווי ההוגן מחושב לפי תשואה של כ־{ke * 100:.1f}% בשנה, והשוק כולו מתומחר '
                         f'היום לכ־{mkt * 100:.1f}% בלבד; ביחס למניות אחרות היא לא יקרה.')
        elif edge is not None and gap < -.03 and edge < -.005:
            notes.append(f'למה "זולה" אבל מניבה פחות מהשוק: גם בשיעור ההיוון של החברה יש מרווח, אבל השוק מתומחר לתשואה גבוהה יותר '
                         f'(כ־{mkt * 100:.1f}%); ביחס למניות אחרות היא לא זולה.')
        notes.append('תשואה צפויה = מה תרוויח בשנה, בממוצע, אם תקנה היום והתרחיש הסביר יתממש. ' + MARKET_NOTE_HE.get(kind, ''))
        if pp.get('status') == 'computed' and pp.get('rows'):
            hit = pp.get('hit_rate_within_band') or {}
            when = [f"בכ־{hit[k]:.0%}" + (' מהמקרים' if i == 0 else '') + (' אחרי שנה' if int(k) == 1 else f' אחרי {k} שנים')
                    for i, k in enumerate(sorted(hit, key=int))]
            band = ', '.join(when[:-1]) + ' ו' + when[-1] if len(when) > 1 else ''.join(when)
            k = pp.get('pull_to_market', .5)
            mid = 'באמצע הדרך' if abs(k - .5) < 1e-9 else f'בכ־{k:.0%} מהדרך'
            notes.append(f"מחיר צפוי למניה = המחיר של היום, שגדל כל שנה {mid} בין התשואה הצפויה במניה (כ־{pp['base_irr'] * 100:.1f}%) "
                         f"לתשואת השוק (כ־{pp['market_return'] * 100:.1f}%). זו הערכה סטטיסטית, לא הבטחה ולא יעד: בבדיקה על 2016–2025 גם "
                         f"השיטה הטובה ביותר פגעה בטווח של ±{pp.get('band', .25):.0%} מהמחיר בפועל רק {band}.")
        notes.append('שווי בעוד 5 שנים הוא הערכה מותנית, לא תחזית למחיר המניה.')
    elif v:
        rows.append(('שווי הוגן', 'אין עדיין שווי אמין להצגה'))
        notes.append('הסיבה: ' + (st['state'] if st.get('hold') else (v.get('reason_he') or 'הערכת השווי לא הושלמה')) + '.')
    q = ((dc.get('assessments') or {}).get('business_quality') or {})
    kind = lynch_type(cf)
    if kind:
        rows.append(('סוג החברה', kind))
    if q.get('level_he'):
        rows.append(('איכות העסק', q['level_he']))
    ov = _horizon((((cf.get('fc') or {}).get('anchors') or {}).get('outside_view') or {}).get('base'), 10)
    if show and ov:
        rows.append(('כמה התחזית אופטימית', f"מניחה צמיחה של כ־{_hp(ov['real_cagr'])} בשנה (מעבר לאינפלציה) לעשר שנים; "
                                             f"רק כ־{ov['share_at_least']:.0%} מהחברות בגודל הזה הצליחו בכך בעבר"))
    pig = cf.get('price_implied_growth') or {}
    if show and pig.get('status') == 'solved' and _n(pig.get('implied_real_cagr')):
        sh = pig.get('growth_shift')
        n = pig.get('horizon', 10)
        diff = (f" (כ־{abs(sh) * 100:.0f} נק׳ אחוז {'יותר' if sh > 0 else 'פחות'} בשנה מהתרחיש הסביר)"
                if _n(sh) and abs(sh) >= .005 else ' (בערך כמו התרחיש הסביר)')
        tail = (f"; רק כ־{pig['share_at_least']:.0%} מהחברות בגודל הזה הצליחו בכך בעבר" if _n(pig.get('share_at_least')) else '')
        rows.append(('מה המחיר של היום מניח', f"צמיחה של כ־{_hp(pig['implied_real_cagr'])} בשנה (מעבר לאינפלציה) ל־{n} שנים"
                                               f"{diff}{tail}"))
        notes.append('"מה המחיר מניח" = איזו צמיחה צריכה להתממש כדי שהמחיר של היום יהיה מוצדק באותו מודל (רגישות, לא תחזית שוק); '
                     'כך רואים אם המחיר כבר מגלם צמיחה נדירה, או משאיר מרווח.')
    kr = (cf.get('analyst_sheet') or {}).get('key_risk') or {}
    risk = kr.get('reading_he') or _top_risk(cf, dc) or 'שהצמיחה והרווחיות לא יתממשו כפי שהונח'
    rows.append(('הסיכון המרכזי', str(risk).rstrip('.')))
    if cf.get('market_context'):          # market_context.py: one market line and the country-risk / exposure explanations
        from .market_context import country_risk_he, exposure_lines_he
        rg = (cf['market_context'].get('regime') or {})
        if rg.get('body_he'):              # context only: no value reads it (market_context.classify_regime)
            rows.append(('מצב השוק' + (f" ב־{rg['date_he']}" if rg.get('date_he') else '') + ' (רקע)', rg['body_he'].rstrip('.')))
        crh = country_risk_he(cf['market_context'])
        if crh:
            notes.append(crh)
        notes += ['חשיפה: ' + x for x in exposure_lines_he(cf)]
    gaps = dc.get('default_levers') if 'default_levers' in dc else (cf.get('fc') or {}).get('driver_gaps')
    if gaps is not None:      # levers still on a rule that is not this company's evidence (forecast.driver_gaps); a gap, not a basis
        rows.append((GAPS_HE, ', '.join(g.get('label_he') or g.get('lever', '') for g in gaps) or 'אין'))
    rows.append(('רמת ביטחון', (dc.get('confidence') or {}).get('level_he', 'לא ידועה')))
    call = CALL_HE.get(code, 'אין עדיין מסקנה') if v else 'אין עדיין מסקנה'
    rows.append(('שורה תחתונה', call + (' (טיוטה שעוד לא נבדקה)' if withheld else '')))
    if withheld:
        notes.append('זו טיוטה: הניתוח עוד לא עבר בדיקה בלתי תלויה, ולכן זו לא מסקנה סופית.')
    out = [f"**{name} ({company.get('ticker', '')}) — הערכת שווי, {cf.get('as_of') or dc.get('as_of') or ''}**", '',
           '| | |', '|---|---|'] + [f'| {k} | {str(x).replace("|", "/")} |' for k, x in rows]
    if show:
        from .company_path import TITLE_HE as CP_TITLE, table_he as cp_table
        cp_rows, cp_notes = cp_table(cf.get('company_path'), lambda x: _money(x, cur))
        if cp_rows:
            out += ['', f'**{CP_TITLE}**', '', '| שנה | דובי | בסיסי | שורי |', '|---|---|---|---|'] + cp_rows
            notes += cp_notes
    yearly = _yearly_rows(er, price, cur) if show else []
    if yearly:
        out += ['', '**שווי למניה בכל שנה, לפי תרחיש**', '', '| מועד | תרחיש רע | תרחיש סביר | תרחיש טוב |', '|---|---|---|---|'] + yearly
        notes.append('הטבלה השנתית: כמה המניה תהיה שווה בכל מועד אם התרחיש יתממש (באותו שיעור היוון כמו השווי ההוגן); חלוקות '
                     'לבעלי המניות משולמות בנוסף.')
    return '\n'.join(out + ([''] + [f'- {n}' for n in notes] if notes else []))


def _yearly_rows(er, price, cur):
    """One row per date of the annual path (today, each fiscal year end, the fifth anniversary) of the executed valuation
    (each scenario at the engine's own cost of equity: the fair-value rate)."""
    y = (er or {}).get('yearly_at_model_cost_of_equity') or {}
    out = []
    for i, r in enumerate(y.get('rows') or []):
        if not all(_n(r.get(s)) for s in ('bear', 'base', 'bull')):
            continue
        when = 'היום' if i == 0 else str(r['date'])[:7].replace('-', '/')
        out.append(f"| {when} | {_money(r['bear'], cur)} | {_money(r['base'], cur)} | {_money(r['bull'], cur)} |")
    return out if len(out) >= 3 else []


def hebrew_paragraph(casefile):
    """ONE plain Hebrew paragraph (normally 80-140 words): the call, one reason, the fair value at the company's own cost of
    equity and the discount, the buy-below price for the required return, the conditional five-year value with
    distributions, the key risk and the confidence. Future values are conditional estimates. A draft is labelled as a
    draft, never a conclusion. Above 140 words the confidence reason is cut to its first clause, then the scenario range is
    dropped; below 80 words one sentence on what the result still lacks is added. The market-regime clause (context only) is
    fitted last, on the finished analysis: it is kept only where it fits, so it never pushes out (or stands in for) a
    sentence of the analysis itself."""
    for kw in ({}, {'short': True}, {'short': True, 'secondary': False}, {'short': True, 'secondary': False, 'compact': True}):
        text = _paragraph(casefile, market=False, **kw)
        if len(text.split()) <= MAX_WORDS:
            break
    if len(text.split()) < MIN_WORDS:
        kw = {'pad': True}
        text = _paragraph(casefile, market=False, pad=True)
    with_market = _paragraph(casefile, **kw)
    if with_market != text and len(with_market.split()) <= MAX_WORDS:
        text = with_market
    return text


def _state(dc):
    """Structured draft state (never substring tests on prose): unreleased forensic flag, governance flag, demo inputs,
    a reviewer's rejected assumption, a failed valuation stage, an unresolved forecast material issue (named), or the
    first mechanical draft code."""
    gates, view = dc.get('gates') or {}, dc.get('valuation_view') or {}
    codes = gates.get('draft_codes') or []
    forensic = bool(gates.get('red_flags')) and not gates.get('red_flags_released')
    gov = bool(gates.get('withhold_price')) or 'governance_red_flag' in codes
    demo = bool(view.get('is_demo')) or 'is_demo' in codes
    rejected = 'assumption_rejected' in codes
    # an unresolved forecast material issue is named (decision.MATERIAL_HE); a 'hold' issue means no value is a valuation
    mat = gates.get('material_issues') or []
    he = list(dict.fromkeys(m.get('description_he') or str(m.get('id')) for m in mat))
    material = ('בתחזית נותרה שאלה מהותית פתוחה: ' + he[0] + (f' (ועוד {len(he) - 1})' if len(he) > 1 else '')) if he else None
    if forensic:
        state, change = 'בדיקות הדוחות הדליקו דגל אדום שעוד לא הוסבר', 'הסבר מתועד מהדוחות לדגל האדום ובדיקה בלתי תלויה'
    elif gov:
        g = next((x.get('description_he') for x in gates.get('withhold_price') or [] if x.get('description_he')), None)
        state, change = f'יש ראיה ל{g or "דגל אדום בממשל התאגידי"}', 'הסבר מתועד בראיות לדגל האדום ובדיקה בלתי תלויה'
    elif demo:
        state, change = 'החישוב נעשה על נתוני הדגמה', 'הרצה על נתוני חברה אמיתית'
    elif rejected:
        state, change = DRAFT_STATE_HE['assumption_rejected'], DRAFT_CHANGE_HE['assumption_rejected']
    elif (gates.get('valuation_failed') or []):
        state, change = 'שלב הערכת השווי נכשל בהרצה', 'תיקון התקלה בשלב הערכת השווי והרצה מחדש'
    elif mat:
        state, change = material, 'הכרעה מבוססת ראיות בשאלה הזו ובדיקה בלתי תלויה'
    else:
        state = next((DRAFT_STATE_HE[c] for c in codes if c in DRAFT_STATE_HE), 'הניתוח טרם עבר בדיקה בלתי תלויה')
        change = next((DRAFT_CHANGE_HE[c] for c in codes if c in DRAFT_CHANGE_HE), 'השלמת בדיקה בלתי תלויה של הנתונים וההנחות')
    hold = forensic or gov or demo or rejected or any(m.get('hold') for m in mat)
    return {'state': state, 'change': change, 'hold': hold, 'material': material}


def _analyst_he(dc, v):
    """Clause naming an accepted analyst override and the mechanical result it departs from (empty when none)."""
    acc = [o for o in dc.get('overrides') or [] if o.get('status') == 'accepted' and o.get('material', True)]
    if not acc:
        return ''
    mech = dc.get('mechanical_verdict') or {}
    differs = mech.get('code') and mech.get('code') != v.get('code')
    return ('התוצאה כוללת הכרעת אנליסט מתועדת' + (f'; התוצאה המכנית היא "{mech.get("label_he")}"' if differs else
                                                 '; המסקנה המכנית זהה') + '.')


def _paragraph(casefile, short=False, secondary=True, pad=False, compact=False, market=True):
    cf = casefile or {}
    dc = cf.get('dc') or {}
    v = dc.get('verdict')
    company = dc.get('company') or cf.get('company') or {}
    name = company.get('name') or company.get('ticker') or 'החברה'
    if not v:
        return f'{name}: שלב ההחלטה לא רץ, ולכן אין כאן מסקנת השקעה. יש להשלים את שלבי הניתוח ולהריץ מחדש.'
    code, withheld = v['code'], bool(v.get('price_claim_withheld'))
    view = dc.get('valuation_view') or {}
    cur = (dc.get('price') or {}).get('currency') or company.get('currency') or ''
    price, bear = view.get('price'), view.get('bear')
    mech = dc.get('mechanical_verdict') or {}
    acc = [o for o in dc.get('overrides') or [] if o.get('status') == 'accepted' and o.get('material', True)]
    st = _state(dc)
    also = st['material'] + '.' if st['material'] and st['material'] != st['state'] else ''
    cond = (v.get('conditional_on') or {}).get('text_he')
    parts = []
    if withheld and code in PRICE_VERDICTS:
        if st['hold']:
            parts.append(f"{name}: {st['state']}, ולכן אינני קובע עדיין אם המניה אטרקטיבית במחיר הנוכחי.")
            parts.append(also)
            parts.append(dc.get('business_reason_he', '') + '.')
            parts.append('בשלב זה אינני מציג שווי, הנחה או ערך לחמש שנים כמסקנה.')
        else:
            parts.append(f"{name}: {st['state']}, ולכן מה שכאן הוא טיוטה שטרם נבדקה ולא מסקנה.")
            parts.append(also)
            parts.append(dc.get('business_reason_he', '') + '.')
            er = dc.get('expected_return') or {}
            base, ke = view.get('base'), view.get('model_cost_of_equity')
            edge = ', קרוב לגבול' if (v.get('margin_detail') or {}).get('status') == 'marginal' else ''
            # the mechanical result is named as such; an analyst-adjusted result is labelled separately
            label = f'בטיוטה בלבד התוצאה המכנית היא "{mech.get("label_he") or v.get("label_he")}"'
            if acc:
                label += (f', והתוצאה אחרי הכרעת אנליסט מתועדת היא "{v.get("label_he")}"' if mech.get('code') != code
                          else ', והתוצאה המוצגת כוללת הכרעת אנליסט מתועדת')
            adj = ' אחרי התאמת האנליסט' if er.get('mechanical') else ''
            if _n(base) and base > 0 and _n(price):
                d = 1 - price / base
                rel = f"נמוך בכ־{abs(d):.0%}" if d >= 0 else f"גבוה בכ־{abs(d):.0%}"
                at = f' (בשיעור ההיוון של החברה, כ־{_hp(ke, er.get("hurdle"))})' if _n(ke) else ''
                parts.append(f'{label}{edge}: שווי הוגן של כ־{_ps(base)} {cur}{at}, והמחיר {_ps(price)} {rel} ממנו'
                             f'{_buy_he(er, base, price, adj, secondary, compact)}.')
                parts.append(f'{cond}.' if cond else '')
                parts.append(_five_he(dc, draft=True, compact=compact))
                parts.append('' if compact else 'אלה מספרי טיוטה שטרם נבדקו, לא מסקנה.')   # the opening already says it is a draft
            else:
                parts.append(f'{label}{edge}; אין עדיין שווי הוגן להצגה.')
    elif withheld:
        # a non-price verdict in a draft is labelled as a draft too
        parts.append(f'{name} (טיוטה שטרם נבדקה, לא מסקנה): {CALL_HE[code]}{_marginal_he(v)}.')
        parts.append('הסיבה העיקרית: ' + v['reason_he'] + '.')
        parts.append(st['material'] + '.' if st['material'] else '')
        parts.append('מה שכן אפשר לומר כרגע: ' + dc.get('business_reason_he', '') + '; עם זאת, אין כאן הערכת שווי או הנחה כמסקנה.')
        parts.append(_analyst_he(dc, v))
    else:
        parts.append(f'{name}: {CALL_HE[code]}{_marginal_he(v)}.')
        parts.append('הסיבה העיקרית: ' + v['reason_he'] + '.')
        parts.append(f'{cond}.' if cond else '')
        parts.append(_analyst_he(dc, v))
        if code not in PRICE_VERDICTS:
            parts.append('מה שכן אפשר לומר כרגע: ' + dc.get('business_reason_he', '') + '; עם זאת, אין כאן הערכת שווי או הנחה כמסקנה.')
    show = code in PRICE_VERDICTS and not withheld and _n(price)
    if show:
        parts.append(_value_he(dc, cur, secondary, compact))
        parts.append(_five_he(dc, compact=compact))
    risk = _top_risk(cf, dc)
    if risk:
        parts.append(f'הסיכון המרכזי: {risk}.')
    elif show and _n(bear) and bear < price:
        parts.append(f'הסיכון המרכזי: בתרחיש השמרני השווי היום כ־{_ps(bear)}, נמוך בכ־{1 - bear / price:.0%} מהמחיר.')
    elif show and _n(bear):
        parts.append(f'הסיכון המרכזי: שהתחזית לא תתממש; גם התרחיש השמרני (כ־{_ps(bear)}) אינו נמוך מהמחיר, אבל הוא אינו המקרה הגרוע ביותר האפשרי.')
    else:
        parts.append('הסיכון המרכזי: שהצמיחה והרווחיות לא יתממשו כפי שהונח.')
    if withheld and code in PRICE_VERDICTS:
        parts.append('מה ישנה את התמונה: ' + st['change'] + '.')
    elif withheld:
        parts.append('מה ישנה את התמונה: ' + (v.get('what_would_change_it_he') or '') + ('; וגם ' + st['change'] if st['change'] not in (v.get('what_would_change_it_he') or '') else '') + '.')
    elif v.get('what_would_change_it_he') and code != 'attractive_at_price':
        w = v['what_would_change_it_he']
        er, base = dc.get('expected_return') or {}, view.get('base')
        pfh = er.get('value_at_required_return')
        # compact: a buy-below price the value sentence just stated (it differs from the fair value) is not said twice
        said = (compact and show and BUY_MARK_HE in w and _n(pfh) and pfh > 0 and _n(base) and base > 0
                and abs(pfh / base - 1) >= .005)
        if not said:
            parts.append('מה ישנה את התמונה: ' + (w.replace(BUY_MARK_HE, '') if compact else w) + '.')
    rg = ((cf.get('market_context') or {}).get('regime') or {})
    if market and rg.get('short_he'):
        parts.append(rg['short_he'])                       # context only (market_context.classify_regime): moves no value
    if pad:
        parts.append('כל עוד הניתוח לא הושלם ולא עבר בדיקה בלתי תלויה, זו טיוטה ולא המלצה, וכל מספר בה עלול להשתנות.' if withheld else
                     'גם הערכה זו מותנית בראיות שבידינו כעת, והיא עשויה להשתנות כשיתווספו נתונים חדשים על העסק ועל המחיר.')
    parts.append(_confidence_he(dc, skip_margin=not withheld or code not in PRICE_VERDICTS, short=short, skip_material=bool(st['material'])))
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
    er = dc.get('expected_return') or {}
    view = dc.get('valuation_view') or {}
    conf = dc.get('confidence') or {}
    status = v.get('status', 'n/a') + (' (unreviewed draft: not a conclusion)' if v.get('price_claim_withheld') else '')
    out = ['## Verdict', '', f"> **{v.get('label_he', '')}** (`{v.get('code')}`) — {status}; confidence **{conf.get('level', 'n/a')}**"
           + (f"; margin **{v['margin']}**" if v.get('margin') else ''), '>', f"> {v.get('reason', '')}", '>']
    if v.get('price_claim_withheld'):
        out += ['> Price claim withheld: ' + '; '.join(v.get('withheld_reasons') or []), '>']
    if v.get('conditional_on'):
        out += ['> Conditional: ' + v['conditional_on']['text'], '>']
    pfh, price = er.get('price_for_hurdle') or {}, view.get('price')
    rel = lambda x: (f"price {_ps(price)} is {_p(abs(1 - price / x), 0)} {'below' if price <= x else 'above'} it"
                     if _n(price) and _n(x) and x > 0 else 'no price')
    if _n(view.get('base')):
        out += [f"> Fair value: base value at the company's own cost of equity ({_p(er.get('model_cost_of_equity'))}, the executed valuation): "
                f"**{_ps(view['base'])}** — {rel(view['base'])}; scenario range {_ps(view.get('bear'))}–{_ps(view.get('bull'))} [calculation]", '>']
    if _n(pfh.get('value')):
        ar = er.get('at_required_return') or {}
        out += [f"> Buy-below price for your required return. Value at your required return (the price at which the base IRR equals the "
                f"{_p(er.get('hurdle'), 2)} required return; the verdict is taken here): **{_ps(pfh['value'])}** — {rel(pfh['value'])}; "
                f"scenario range at that return {_ps(ar.get('bear'))}–{_ps(ar.get('bull'))} [calculation]", '>']
    elif pfh.get('status') == 'invalid':
        out += [f"> Buy-below price at your required return ({_p(er.get('hurdle'), 2)}): **not computable** — {pfh.get('reason')}; the fair value "
                "above is at the company's own cost of equity, a different rate", '>']
    mos = v.get('margin_of_safety') or {}
    if _n(mos.get('loss_to_bear')) or mos.get('bear_value') is not None:
        out += [f"> Margin of safety: bear value {_ps(mos.get('bear_value'))} ({mos.get('basis')}), loss from price {_p(mos.get('loss_to_bear'), 0)} "
                f"vs tolerance {_p(mos.get('tolerance'), 0)} [calculation]", '>']
    idx = er.get('index_expected_return') or {}
    if _n(er.get('hurdle')):
        mech = er.get('mechanical') or {}
        fl = er.get('model_cost_of_equity_floor') or {}
        out += [f"> Required return {_p(er['hurdle'], 2)} = index {_p(idx.get('value'), 2)} ({idx.get('origin', 'n/a')}) + premium {_p(er.get('risk_premium'), 2)}"
                + (f" (permanent-loss premium {_p(er.get('permanent_loss_premium'), 2)}, lifted to the model cost of equity {_p(fl.get('value'), 2)})"
                   if fl.get('binding') else '')
                + (f" — effective after accepted overrides; mechanical {_p(mech.get('hurdle'), 2)}" if mech else ''), '>']
    md = v.get('margin_detail') or {}
    if md:
        lo, hi = md.get('hurdle_range') or (None, None)
        pl, ph = md.get('value_range') or (None, None)
        out += [f"> Marginal band: required return {_p(lo, 2)}–{_p(hi, 2)} ↔ value at required return {_ps(pl)}–{_ps(ph)}, and the bear-loss "
                f"tolerance ±5pp: **{md.get('status')}**" + (f" ({md['text']})" if md.get('text') else ' (verdict unchanged inside the band)')
                + (f"; applies to {md['applies_to']}" if md.get('applies_to') else ''), '>']
    mp = (er.get('band') or {}).get('moat_proxy') or {}
    if mp:
        wi, wo = (mp.get('value_today_with_proxy') or {}).get('base'), (mp.get('value_today_without_proxy') or {}).get('base')
        out += [(f"> Computed moat proxy (not an analyst assessment): base value today {_ps(wi)} with it, {_ps(wo)} without it (model cost of "
                 f"equity); value at your required return without it {_ps(mp.get('value_at_required_return_without_proxy'))}; the verdict is "
                 're-run without it inside the marginal band') if mp.get('status') == 'computed' else
                f"> Computed moat proxy applied; band test without it unavailable: {mp.get('reason')}", '>']
    out += ['> What would change it:'] + [f'> - {x}' for x in v.get('what_would_change_it') or []]
    out += ['', 'Confidence reasons: ' + ('; '.join(conf.get('reasons') or []) or 'none') + f" ({conf.get('rule', '')})"]
    if m and m.get('code') != v.get('code'):
        out += ['', f"Mechanical verdict: `{m.get('code')}` (route `{m.get('gate')}`) — {m.get('reason')}"]
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
    view = dc.get('valuation_view') or {}
    out = ['## Expected return at today\'s price', '', f"[calculation] {er.get('semantics', '')}", '']
    st = er.get('irr_status') or {}

    def irr(s):
        b = (st.get(s) or {}).get('bound')
        return _p(er.get(f'{s}_irr')) if er.get(f'{s}_irr') is not None else f"{b['type']} {_p(b['rate'])}" if b else 'n/a'
    out += _table(['scenario', 'IRR', 'status'], [[s, irr(s), (st.get(s) or {}).get('status', 'n/a')] for s in ('bear', 'base', 'bull')])
    rng = f" (range {_p(idx.get('low'), 2)}–{_p(idx.get('high'), 2)})" if _n(idx.get('low')) and _n(idx.get('high')) else ' (no range supplied)'
    mech = er.get('mechanical') or {}
    fl = er.get('model_cost_of_equity_floor') or {}
    crp = next((c.get('value') for c in er.get('premium_components') or [] if c.get('component') == 'country_risk'), None)
    out += ['', f"- Index expected return: **{_p(idx.get('value'), 2)}**{rng} [{idx.get('provenance')}; origin {idx.get('origin', 'n/a')}; "
                f"comparator {idx.get('comparator', 'n/a')}: {idx.get('source')}] — {idx.get('basis')}",
            f"- Required return (hurdle): **{_p(er.get('hurdle'), 2)}** = index + premium {_p(er.get('risk_premium'), 2)} [model_assumption]; "
            f"{er.get('hurdle_basis', '')}" + (f" (mechanical {_p(mech.get('hurdle'), 2)}, premium {_p(mech.get('risk_premium'), 2)})" if mech else ''),
            f"- Floor: the model's own cost of equity {_p(fl.get('value'), 2)} — " + ('**binding** (index + permanent-loss premium '
            + ('+ country-risk row ' if crp is not None else '')
            + f"{_p((idx.get('value') or 0) + (er.get('permanent_loss_premium') or 0) + (crp or 0), 2)} is lower)" if fl.get('binding') else 'not binding'), '']
    comps = er.get('premium_components') or []
    if comps:
        out += ['Premium for the risk of permanent loss (valuation error is not in it; it widens the marginal band):', '']
        out += _table(['component', 'premium', 'basis'], [[c.get('component'), _p(c.get('value'), 2), c.get('basis')] for c in comps])
        out += [f"- Rule: {er.get('hurdle_rule', '')}" + (' (cap applied)' if er.get('premium_capped') else ''), '']
    pfh = er.get('price_for_hurdle') or {}
    price = view.get('price')
    if _n(pfh.get('value')) and pfh['value'] > 0:
        rel = (f"price {_ps(price)} is {_p(abs(1 - price / pfh['value']), 0)} {'below' if price <= pfh['value'] else 'above'} it" if _n(price) else 'no price')
        out.append(f"- Value at your required return (buy-below price): **{_ps(pfh['value'])}** ({rel}) [calculation]; fair value = "
                   f"base value at the model cost of equity {_p(er.get('model_cost_of_equity'))}: {_ps(view.get('base'))} (the headline)")
    band = er.get('band') or {}
    if band.get('rows') and any(_n(r.get('value_at_required_return')) for r in band['rows']):
        out += ['', f"Marginal band (step ±{band.get('step', 0) * 100:.1f}pp incl. widening {band.get('error_widening', 0) * 100:.1f}pp for "
                    f"{band.get('error_level')} valuation error, and the index range):", '']
        out += _table(['test', 'required return', 'value at that return'], [[r['label'], _p(r['hurdle'], 2), _ps(r.get('value_at_required_return'))] for r in band['rows']])
    f5 = er.get('five_year_at_required_return')
    if f5:
        out += ['', f"- Five-year base value at your required return ({f5['date']}, {f5['years']:.1f} years): {_ps(f5['base_value'])} + distributions "
                    f"(buybacks/dividends) {_ps(f5['cumulative_distributions_base'])} = {_ps(f5['total_value_with_distributions'])}; "
                    f"{_p(f5['change_including_distributions'], 0)} vs price including distributions ({_p(f5['change_excluding_distributions'], 0)} excluding; "
                    f"{_p(f5.get('annualized_including_distributions'))}/yr) [calculation] — assumes a re-rating: the price converges to the value at "
                    f"your required return by then, so the rate differs from the base IRR {_p(er.get('base_irr'))}"]
    pp = er.get('expected_price_path') or {}
    if pp.get('status') == 'computed':
        hit = pp.get('hit_rate_within_band') or {}
        out += ['', f"- Expected price path ({pp.get('formula')}; base IRR {_p(pp.get('base_irr'))}, market {_p(pp.get('market_return'))} "
                    f"[{pp.get('comparator')}], r = {_p(pp.get('rate'))}): "
                    + '; '.join(f"{r.get('date') or str(r['years']) + 'y'} {_ps(r['price'])}" for r in pp.get('rows') or [])
                    + f" [calculation; estimate] — within ±{_p(pp.get('band'), 0)} of the realized price in only "
                    + '/'.join(_p(hit[k], 0) for k in sorted(hit, key=int)) + ' of walk-forward rows at '
                    + '/'.join(str(k) for k in sorted(hit, key=int)) + ' years; not a value, not a target']
    aspire = er.get('meets_owner_aspiration_14pct')
    out += [f"- Owner's 14% aspiration (comparison only, not the hurdle): base IRR {_p(er.get('base_irr'))} "
            f"{'meets' if aspire else 'does not meet' if aspire is False else 'cannot be compared to'} it"]
    d = er.get('decomposition') or {}
    if d.get('status') == 'computed':
        out += ['', f"Re-rating scenario at the model's cost of equity ({_p(d.get('model_cost_of_equity'))}) — not the return at your required return "
                    f"and not the IRR: base return to {d['date']} ({d['years']:.1f} years) if the stock trades at the model's base value then [calculation]:", '']
        span = (f"; over the actual {d['anchor_span_years']:.1f}-year anchor-to-end span: {_p(d['business_growth_over_anchor_span'])}/yr (the anchor ends {d['anchor_end']})"
                if _n(d.get('business_growth_over_anchor_span')) and _n(d.get('anchor_span_years')) and abs(d['anchor_span_years'] - d['years']) > .05 else '')
        out += _table(['component', 'annualised', 'basis'],
                      [['business growth', _p(d['business_growth']), f"{d['metric']}: {_ps(d['anchor']['value'])} ({d['anchor']['basis']}) -> {_ps(d['end']['value'])} ({d['end']['basis']}){span}"],
                       ['multiple change', _p(d['multiple_change']), f"{_x(d['current_multiple'])} today -> {_x(d['exit_multiple'])} implied at the boundary"],
                       ['cash yield', _p(d['cash_yield']), 'base-case distributions (buybacks or dividends)'],
                       ['total (re-rating scenario)', _p(d['total_annualized']), '(1+g)(1+m)(1+c) - 1']])
        out += ['', f"Formula: {d['formula']} {d.get('semantics', '')}"]
    else:
        out += [f"- Re-rating decomposition unavailable: {d.get('reason', 'n/a')}"]
    return out + ['']


def _p0(x, d=0):
    return 'n/a' if x is None else f'{x * 100:.{d}f}%'


def _judgement(dc):
    j = (dc or {}).get('judgement')
    if not j:
        return []
    r = j['return_vs_alternatives']
    out = ['## Judgement: disqualifiers, attractiveness, risks', '', f"**{j['reading']}**", '',
           '- Disqualifiers: ' + ('; '.join(f"{d['kind']}: {d['what']}" for d in j['disqualifiers'][:5]) or 'none'),
           '- For: ' + (', '.join(f['factor'] for f in j['attractiveness']['for']) or '-')
           + ' | neutral: ' + (', '.join(f['factor'] for f in j['attractiveness']['neutral']) or '-')
           + ' | against: ' + (', '.join(f['factor'] for f in j['attractiveness']['against']) or '-')
           + ' | unknown: ' + (', '.join(f['factor'] for f in j['attractiveness']['unknown']) or '-'),
           f"- Return vs alternatives: bear/base/bull {_p0(r['bear_irr'], 1)} / {_p0(r['base_irr'], 1)} / {_p0(r['bull_irr'], 1)}; index "
           f"{_p0(r['index_expected_return'], 1)}; required {_p0(r['required_return'], 1)}; excess over index {_p0(r['excess_over_index'], 1)}; "
           f"loss to bear {_p0(r['loss_to_bear'])} (14% aspiration reported, never a threshold: "
           f"{'met' if r.get('meets_owner_aspiration_14pct') else 'not met'})",
           f"- Risks: {len(j['risks']['priced'])} priced in the bear case, {len(j['risks']['to_price'])} still to price, "
           f"{len(j['risks']['open_questions'])} open research questions", '']
    return out


def _business(cf):
    b = cf.get('business') or {}
    out = ['## How the company makes money (annual report XBRL)', '']
    if b.get('status') != 'parsed':
        return out + ['Annual report not parsed: segments, products and geography are unknown here (not assumed).', '']
    src = b.get('source') or {}
    out += [f"Source: {src.get('form')} filed {src.get('filed')} for the period {src.get('period')} ({src.get('url')}).", '']
    for name, t in (b.get('parts') or {}).items():
        out += [f"**By {name.replace('_', ' ')}** ({t.get('reconciliation')})", '']
        out += _table(['part', 'revenue', 'share', 'growth/yr', 'span', 'share of growth', 'op. margin', 'margin change'],
                      [[x['name'], x['revenue'], _p0(x['share']), _p0(x['cagr'], 1), x['span'] or '-', _p0(x['share_of_growth']),
                        _p0(x['operating_margin'], 1), 'n/a' if x['margin_change'] is None else f"{x['margin_change'] * 100:+.1f} pts"]
                       for x in t['rows'][:12]]) + ['']
    if b.get('rpo'):
        out.append(f"- Contracted, not yet recognized revenue (RPO): {b['rpo']['value']:,.0f} at {b['rpo']['as_of']}"
                   + (f"; {_p0(b['rpo'].get('share_in_first_period'))} expected in the first period" if b['rpo'].get('share_in_first_period') else ''))
    for c in b.get('customer_concentration_revenue') or []:
        out.append(f"- Concentration: {c['customer']} {_p0(c['share'], 1)} of {c['benchmark']} ({c['period_end']})")
    cc = b.get('cross_check')
    if cc:
        out.append(f"- Segment-built growth (each part at its own pace, fading) {_p0(cc['segment_built_avg_growth'], 1)}/yr vs the consolidated "
                   f"base path {_p0(cc['consolidated_base_avg_growth'], 1)}/yr over {cc['years']} years: {cc['reading']}")
    sv = cf.get('segment_view') or {}
    if sv.get('status') == 'computed' and sv.get('base_value_segment_path') is not None:
        out.append(f"- Second view: base value with the segment-built path {sv['base_value_segment_path']:,.2f} against "
                   f"{sv['base_value_consolidated']:,.2f} on the consolidated path ({_p0(sv['difference'])}); quote {sv['quote']}. "
                   'Which view holds is a research question, not a model choice.')
    out += ['- Not in the XBRL (read the text): ' + '; '.join(f"{x['item']} ({x['where']})" for x in b.get('not_disclosed') or []), '']
    m = cf.get('management')
    if m:
        out += ['**How management is paid (proxy statement, pay versus performance)**', '',
                f"- Measures: {', '.join(m['measures']) or 'not tagged (smaller reporting companies may omit the list)'}"
                + (f"; company-selected: {m['company_selected_measure']}" if m.get('company_selected_measure') else ''),
                f"- Orientation: {m['orientation'].replace('_', ' ')} (per-share or return words: {', '.join(m['per_share_words']) or 'none'}; "
                f"size words: {', '.join(m['size_words']) or 'none'}): a reading of the stated measures, not of the plan's weights"]
        cap, tsr = m.get('peo_comp_actually_paid') or {}, m.get('total_shareholder_return') or {}
        if cap and tsr:
            y = max(set(cap) & set(tsr), default=None)
            if y:
                out.append(f"- CEO pay actually paid {cap[y]:,.0f} in the year to {y}; shareholder return index {tsr[y]:,.0f} "
                           f"(peer group {(m.get('peer_tsr') or {}).get(y, 'n/a')})")
        out.append('')
    return out


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
    notes = cf.get('collection_notes') or []
    if notes:
        out += ['Collection notes (from building the FactBook):'] + [f'- {n}' for n in notes] + ['']
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
    view = dc.get('valuation_view') or {}
    out += ['', f"Values are conditional per-share values at each date at the model cost of equity ({_p(view.get('model_cost_of_equity'))}) "
                '[calculation from model_assumption inputs], not predicted market prices: the base column is the fair value the owner sees; '
                'the verdict uses the value at your required return, the buy-below price (Verdict, Expected return).']
    five = view.get('five_year')
    if five:
        dist = five.get('cumulative_distributions_base', five.get('cumulative_dividends_base')) or 0.
        out.append(f"Five-year date {five['date']} ({five['years']:.1f} years; {five.get('status', 'n/a')}): base {_ps(five['base_value'])} "
                   f"(range {_ps(five.get('bear_value'))}–{_ps(five.get('bull_value'))}) at the model cost of equity, plus distributions (buybacks/dividends) "
                   f"{_ps(dist)}; change vs price {_p(five.get('price_change_including_distributions'), 0)} including distributions, "
                   f"{_p(five['price_change_excluding_dividends'], 0)} excluding.")
    elif view.get('five_year_unavailable'):
        out.append(f"Five-year value unavailable: {view['five_year_unavailable']}.")
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
        out += ['', f"- Model-rate sensitivity (±{_p(band.get('shift'), 0)} around the model cost of equity, not the decision's required return): "
                    f"base value {_ps(band.get('value_higher_rate'))}–{_ps(band.get('value_lower_rate'))}; price inside: {band.get('quote_inside_band')}. "
                    'The decision\'s marginal band is in Expected return.']
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


def _risks(cf, dc=None):
    rk = cf.get('rk')
    out = ['## Risks and failure scenarios', '']
    if not rk:
        return out + ['Risk assessment unavailable.', '']
    pricing = {p.get('id'): p for p in (dc or {}).get('risk_pricing') or []}
    out += _table(['id', 'category', 'class', 'severity', 'in bear case', 'priced?', 'description', 'evidence'],
                  [[r.get('id'), r.get('category'), r.get('class'), r.get('severity'), r.get('in_bear_case'),
                    (lambda p: f"{p['status']} ({p['basis']})" if p else 'n/a')(pricing.get(r.get('id'))), r.get('description'), _ids(r.get('evidence_ids'))]
                   for r in rk.get('register') or []])
    out += ['', "_Priced = in the executed bear path, or an explicit quantified adjustment that moves value materially; a small "
                "cost-of-equity add-on is partially priced._"]
    if rk.get('failure_scenarios'):
        out += ['', 'Failure scenarios:', '']
        out += _table(['name', 'mechanism', 'observable signs', 'value/share', 'loss from price'],
                      [[f.get('name'), f.get('mechanism'), f.get('observable_signs'), _ps(f.get('value_per_share')), _p(f.get('loss_from_price'), 0)]
                       for f in rk['failure_scenarios']])
    dn, er = rk.get('downside') or {}, rk.get('error_size') or {}
    out += ['', f"- Downside: bear value {_ps(dn.get('bear_value'))} (loss {_p(dn.get('loss_to_bear'), 0)}); permanent-loss value "
                f"{_ps(dn.get('permanent_loss_value'))} (loss {_p(dn.get('loss_to_permanent'), 0)}) [calculation]",
            f"- Size of possible valuation error: **{er.get('level', 'n/a')}**; value range {_p(er.get('value_range_rel'), 0)}; terminal share "
            f"{_p(er.get('terminal_share'), 0)}; {'; '.join(map(str, er.get('reasons') or []))} (widens the decision's marginal band; not part of the required return)"]
    gaps = er.get('process_gaps') or []
    if gaps:
        out.append('- Process gaps (confidence, not valuation error; counted once in confidence): '
                   + '; '.join(f"{gp.get('code')} ({gp.get('severity')}): {gp.get('detail')}" for gp in gaps))
    out.append(f"- Disqualifying: {_ids(rk.get('disqualifying'))}")
    if rk.get('withhold_price'):
        out.append(f"- Withholding the price call until documented: {_ids(rk.get('withhold_price'))}")
    return out + ['']


def _review(cf):
    rv = cf.get('rv')
    out = ['## Review: mechanical check and independent reviewer', '']
    if not rv:
        return out + ['Review did not run (neither the mechanical check nor an independent reviewer): every price claim is a draft.', '']
    m = rv.get('mechanical') or {}
    out += [f"Status: **{rv.get('status')}**; price conclusion allowed: **{rv.get('price_conclusion_allowed')}**"]
    out += [f'- {r}' for r in rv.get('reasons') or []]
    rt = m.get('redteam') or {}
    bc = rt.get('by_construction') or []
    out += [f"- Mechanical check: dataqa {m.get('dataqa')}; valuation audit {_dig(m, 'valuation_audit', 'status') or 'n/a'}; "
            f"red team {rt.get('verdict') or 'n/a'} (capital structure from {rt.get('capital_structure_source') or 'n/a'}; "
            + (f"passing by construction, not independent evidence: {', '.join(map(str, bc))}" if bc else 'no check passes by construction')
            + f"); consistency findings {len(m.get('consistency') or [])}"]
    out += [f"  - {c.get('code')}: {c.get('status')} ({c.get('severity')}) — {c.get('message')}" if isinstance(c, dict) else f'  - {c}'
            for c in m.get('consistency') or []]
    r = rv.get('reviewer')
    if r:
        rc = rv.get('reviewer_check') or {}
        out.append(f"- Independent reviewer: {r.get('reviewer') or r.get('name') or 'n/a'} (reviewed_at {r.get('reviewed_at') or r.get('signed_at') or r.get('date') or 'undated'}; "
                   f"independent: {r.get('independent')}; valid: {rc.get('valid', 'n/a')}) — {r.get('statement') or ''}")
        out += [f'  - check error: {e}' for e in rc.get('errors') or []]
        for f in r.get('findings') or []:
            out.append(f"  - [{f.get('severity')}] {f.get('text')} — {'resolved: ' + str(f.get('response')) if f.get('resolved') else 'UNRESOLVED'}"
                       if isinstance(f, dict) else f'  - {f}')
    else:
        out.append('- No independent reviewer: only the mechanical check ran, so nothing here is independently reviewed.')
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


def _market(cf):
    """Market context section (market_context.py): country-risk rows, exposure scenarios, the regime readings and open questions."""
    from .market_context import share_text
    mc = cf.get('market_context') or {}
    cr = mc.get('country_risk') or {}
    out = ['## Market context', '']
    t = cr.get('table') or {}
    out.append(f"- Country risk: **{cr.get('status') or 'n/a'}**" + (f", add-on {_p(cr.get('add_on'), 2)} to the cost of equity ({cr.get('channel')} "
               'channel; the larger of revenue and operations' + (f", a lower bound: {_p(cr.get('uncovered_share'), 0)} of the channel is not "
                                                                   'mapped to a country' if cr.get('status') == 'partial' else '') + ')'
               if cr.get('status') in ('applied', 'partial') else '') +
               f" [model_assumption; {t.get('title', 'country risk table')}, {t.get('as_of', 'n/a')}, {t.get('url', '')}]")
    out.append(f"- Formula: {cr.get('formula', 'n/a')}; US base CRP {_p((cr.get('base') or {}).get('crp'), 2)}")
    for ch in ('revenue', 'operations'):
        c = cr.get(ch)
        if not c:
            continue
        out += ['', f"**{ch}** ({c['source']}, {c.get('as_of') or 'n/a'}; evidence {_ids(c.get('evidence_ids'))}): add-on {_p(c['add_on'], 2)}, "
                f"unmapped {_p(c['unmapped_share'], 0)}, not itemized {_p(c['not_itemized_share'], 0)}", '']
        out += _table(['member', 'share', 'mapped to', 'CRP', 'above US', 'contribution'],
                      [[r['member'], _p(r['share']), (r['mapped_to'] or '-') + (f" ({r['note']})" if r.get('note') else ''), _p(r['crp'], 2),
                        _p(r['above_base'], 2), _p(r['contribution'], 3)] for r in c['rows']])
    scen = {x.get('name'): x for x in (cf.get('rk') or {}).get('failure_scenarios') or []}
    if mc.get('exposures'):
        out += ['', '**Evidenced exposures (failure scenarios; no probability)**', '']
        rows = []
        for x in mc['exposures']:
            s = scen.get('exposure:' + x['id']) or {}
            rows.append([x['id'], f"{share_text(x['share'])} {x['share_basis']}", x['shock'] + (f" @ {_p(x['cost_rate'])}" if x.get('cost_rate') else ''),
                         _ps(s.get('value_per_share')), _ps(s.get('base_value_with_shock')), _ps(s.get('base_value_control')),
                         _p(s.get('base_effect'), 0), _ids(x['evidence_ids'])])
        out += _table(['exposure', 'share', 'shock', 'bear with shock', 'base with shock', 'base, same path without the shock', 'base effect',
                       'evidence'], rows)
        out += ['', '- Effect = 1 - base with shock / the base row re-run the same way without the shock (its funding plan removed and gaps '
                're-closed in both), so the re-run path is not counted as the event; the executed base is unchanged.']
    rg = mc.get('regime')
    if rg:
        out += ['', f"**Market regime (context only; no value reads it)**: {rg.get('line_he') or 'no readings'}", '']
        out += _table(['series', 'value', 'date', 'source'], [[k, f"{r['value']:g}", r.get('date'), r.get('url') or '-']
                                                            for k, r in (rg.get('readings') or {}).items()])
        out += ['', f"- Levels: {_cell(rg.get('levels'))}; flags: {', '.join(rg.get('flags') or []) or 'none'}"]
    if mc.get('open_questions'):
        out += ['', '**Open questions**', ''] + [f'- {q}' for q in mc['open_questions']]
    return out + ['']


def markdown(casefile):
    """Complete readable report of one casefile; sections never silently disappear (an unavailable
    stage is stated). Paragraph first, so the short answer and its audit trail sit together."""
    cf = casefile or {}
    dc = cf.get('dc') or {}
    out = _header(cf, dc)
    out += ['## In one paragraph', '', hebrew_paragraph(cf), '']
    if cf.get('analyst_sheet'):
        from .analyst_sheet import render_he
        out += render_he(cf['analyst_sheet']).split('\n') + ['']
    out += _verdict_box(dc) + _assessments(dc) + _expected(dc) + _judgement(dc) + _business(cf) + _card(cf) + _financials(cf) + _dataqa(cf)
    out += _forecast(cf) + _valuation(cf, dc) + _risks(cf, dc) + (_market(cf) if cf.get('market_context') else []) + _review(cf)
    out += _limitations(cf, dc)
    out += ['## Provenance legend', ''] + _table(['class', 'meaning'], PROVENANCE) + ['']
    return '\n'.join(out)
