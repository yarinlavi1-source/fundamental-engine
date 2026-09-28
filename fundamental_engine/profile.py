"""What kind of company is this, and therefore how must it be researched?

Two lenses from established practice, computed from reported history:
- Damodaran corporate life cycle: start_up, young_growth, high_growth, mature_growth,
  mature_stable, decline. Narrative dominates early; numbers dominate when mature.
- Lynch categories: fast_grower, stalwart, slow_grower, cyclical, turnaround, asset_play.

Rules are transparent and deliberately coarse. The analyst can override with an
explicit reason; the override and the computed evidence are both returned. The type
selects research focus, metrics and valuation fit. It never changes source truth.
"""
from statistics import pstdev
from .history import history, cagr, span_years, margins, organic_growth, txt, acquisition_years, organic_cagr, quarters, quarter_momentum

STAGES = ('start_up', 'young_growth', 'high_growth', 'mature_growth', 'mature_stable', 'decline')
LYNCH = ('fast_grower', 'stalwart', 'slow_grower', 'cyclical', 'turnaround', 'asset_play')
CYCLICAL_ARCHETYPES = {'commodity'}
MAYBE_CYCLICAL = {'industrial', 'consumer', 'infrastructure'}

STAGE_HE = {'start_up': 'סטארט-אפ', 'young_growth': 'צמיחה צעירה', 'high_growth': 'צמיחה מהירה',
            'mature_growth': 'צמיחה בוגרת', 'mature_stable': 'בוגרת ויציבה', 'decline': 'בדעיכה'}
LYNCH_HE = {'fast_grower': 'צומחת מהר', 'stalwart': 'איתנה (סטולוורט)', 'slow_grower': 'צומחת לאט',
            'cyclical': 'מחזורית', 'turnaround': 'מפנה (טרנאראונד)', 'asset_play': 'משחק נכסים'}
PLAIN_STAGE = {'start_up': 'emerging', 'young_growth': 'emerging', 'high_growth': 'growth',
               'mature_growth': 'growth', 'mature_stable': 'mature', 'decline': 'mature'}

# What decides value at each stage (Damodaran life cycle; McKinsey/Mauboussin on fade).
PLAYBOOK = {
    'start_up': {
        'decides': ['האם יש מוצר שלקוחות משלמים עליו באמת', 'כמה זמן הכסף מחזיק', 'גודל השוק ומה החלק הריאלי שלה בו'],
        'metrics': ['paid customers/pilots converting to paid', 'cash runway', 'gross margin on first sales', 'dilution per funding round'],
        'valuation': 'Narrative-driven scenarios (rNPV/milestones or driver DCF) with explicit failure probability and funding; multiples of current earnings are meaningless.',
        'traps': ['logos of famous partners without paid adoption', 'TAM claims without a path to share', 'ignoring future dilution'],
        'packets': ['frontier_research', 'potential', 'funding'],
    },
    'young_growth': {
        'decides': ['האם הצמיחה נמשכת גם כשהבסיס גדל', 'האם ההפסד מצטמצם ככל שהיא גדלה (יתרון לגודל)', 'מי מממן את הדרך ובאיזה מחיר לבעלי המניות'],
        'metrics': ['revenue growth and its source (volume/price/new customers)', 'gross margin trend', 'operating loss as % of revenue trend', 'cash runway', 'share count growth', 'unit economics / retention'],
        'valuation': 'Driver DCF with a clear path to target margins, reinvestment linked to growth, dilution and a failure scenario; check the price with reverse DCF.',
        'traps': ['extrapolating early growth rates (most >20% growers fade toward ~8% within five years)', 'adjusted metrics that exclude stock compensation', 'counting on financing that is not committed'],
        'packets': ['potential', 'funding', 'expectations'],
    },
    'high_growth': {
        'decides': ['כמה זמן הצמיחה המהירה יכולה להימשך (רוב החברות מאטות)', 'האם הרווחיות עולה ככל שהיא גדלה', 'האם כל דולר שהיא משקיעה מחזיר יותר מעלות ההון'],
        'metrics': ['revenue growth vs base rates', 'incremental margins', 'ROIC and incremental ROIC', 'reinvestment rate', 'Rule of 40 for software'],
        'valuation': 'Driver DCF with explicit growth fade to maturity and terminal ROIC; reverse DCF to see how long the price needs high growth.',
        'traps': ['paying for growth that must last a decade', 'ignoring competition as margins attract entrants', 'confusing acquired growth with organic growth'],
        'packets': ['expectations', 'business', 'adversarial'],
    },
    'mature_growth': {
        'decides': ['האם יש חפיר (יתרון תחרותי) שמגן על הרווחיות', 'על מה הם מוציאים את הכסף העודף — השקעות, רכישות, חלוקה', 'האם הרכישות מייצרות ערך או רק גודל'],
        'metrics': ['ROIC vs cost of capital and its stability', 'organic vs acquired growth', 'FCF conversion', 'shareholder yield (dividends+buybacks−issuance)', 'leverage after acquisitions'],
        'valuation': 'FCF DCF with moderate growth fading to GDP-like; cross-check with FCF yield and reverse DCF.',
        'traps': ['acquisition-driven growth that hides organic slowdown', 'debt-funded buybacks', 'peak margins treated as permanent'],
        'packets': ['management', 'earnings_quality', 'expectations'],
    },
    'mature_stable': {
        'decides': ['כמה מזומן העסק מייצר ומה מגיע לבעלי המניות', 'האם החפיר נשחק', 'האם המחיר משלם יותר מדי על יציבות'],
        'metrics': ['FCF yield', 'dividend + buyback coverage by FCF', 'ROIC stability', 'debt / cash flow', 'market share and pricing'],
        'valuation': 'Stable-growth FCF/dividend DCF; FCF yield vs bond yields; reverse DCF.',
        'traps': ['dividends paid with debt', 'slow erosion hidden by buybacks', 'paying a growth multiple for no growth'],
        'packets': ['management', 'earnings_quality', 'revenue_decline'],
    },
    'decline': {
        'decides': ['האם הירידה זמנית או מבנית', 'כמה מזומן אפשר עוד להוציא מהעסק לפני שנגמר', 'האם החוב ישרוד את הירידה'],
        'metrics': ['revenue decline by segment/cause', 'margin compression', 'debt maturities vs cash flow', 'asset sale / liquidation value'],
        'valuation': 'Run-off/liquidation or declining-cash-flow DCF with debt schedule; never a growth terminal value.',
        'traps': ['value trap: cheap multiple on shrinking earnings', 'assuming a turnaround without evidence', 'ignoring debt maturity walls'],
        'packets': ['revenue_decline', 'funding', 'adversarial'],
    },
}
LYNCH_NOTES = {
    'fast_grower': 'השאלה המרכזית: כמה זמן עוד אפשר לצמוח ככה ובאיזה מחיר משלמים על זה.',
    'stalwart': 'חברה גדולה ויציבה. לא קונים אותה לפי פי 10 — קונים כשהמחיר סביר ובודקים שהחפיר לא נשחק.',
    'slow_grower': 'מקור התשואה הוא בעיקר דיבידנד וקנייה חוזרת. בודקים שהם ממומנים מהמזומן ולא מחוב.',
    'cyclical': 'הרווחים עולים ויורדים עם המחזור. לא מסתכלים על שנה אחת — מנרמלים על פני מחזור. מכפיל נמוך בשיא הוא מלכודת.',
    'turnaround': 'קודם כל הישרדות: חוב, מזומן ותאריכי פירעון. אחר כך — האם יש ראיות אמיתיות לשיפור.',
    'asset_play': 'הערך נמצא בנכסים (מזומן, נדל"ן, השקעות). בודקים שהנכסים אמיתיים ושההנהלה לא תשרוף אותם.',
}


def classify(case, rows=None):
    rows = rows or history(case)
    archetype = case.get('archetype', 'nonfinancial')
    n = len(rows)
    years = span_years(rows) if n > 1 else 0
    growth = cagr(rows[0]['revenue'], rows[-1]['revenue'], years) if n > 1 else None
    yoy = rows[-1]['revenue'] / rows[-2]['revenue'] - 1 if n > 1 and rows[-2]['revenue'] > 0 else None
    organic = organic_growth(rows)
    om = [m for m in margins(rows, 'operating_income') if m is not None]
    last_om = om[-1] if om else None
    om_trend = om[-1] - om[0] if len(om) >= 2 else None
    volatility = pstdev(om) if len(om) >= 3 else None
    revenue_drop = any(rows[i]['revenue'] < rows[i - 1]['revenue'] * .9 for i in range(1, n)) if n > 1 else False
    recovered = n > 2 and any(rows[i]['revenue'] < rows[i - 1]['revenue'] * .9 and rows[-1]['revenue'] > rows[i]['revenue'] for i in range(1, n - 1))
    was_profitable = any(m > .02 for m in om[:-1]) if len(om) >= 2 else False
    evidence, reasons = {}, []
    g = growth if growth is not None else yoy
    bought = acquisition_years(rows)
    if bought:
        organic_g = organic_cagr(rows)
        if organic_g is not None:
            g = organic_g
            reasons.append('growth measured without years with a large acquisition: ' + ', '.join(bought))
    # Scale: very large, profitable, dividend-paying firms rarely stay in early stages.
    large_mature = rows[-1]['revenue'] >= 2e10 and last_om is not None and last_om >= .15 and (rows[-1].get('dividends_paid') or 0) > 0
    # Life cycle stage.
    if last_om is not None and last_om <= -.5 and (g is None or g > .5 or rows[-1]['revenue'] == 0):
        stage = 'start_up'; reasons.append('large losses relative to revenue and very early revenue base')
    elif g is not None and g < -.02 and (om_trend is None or om_trend < 0 or (last_om is not None and last_om <= 0)):
        stage = 'decline'; reasons.append('revenue shrinking with margins not improving')
    elif last_om is not None and last_om < 0 and g is not None and g >= .15:
        stage = 'young_growth'; reasons.append('fast growth while still loss-making')
    elif g is not None and g >= .2 and not large_mature:
        stage = 'high_growth'; reasons.append('revenue compounding at 20% or more')
    elif g is not None and g >= .2:
        stage = 'mature_growth'; reasons.append('fast growth, but a very large, highly profitable dividend payer: judged as a mature compounder')
    elif g is not None and g >= .08:
        stage = 'mature_growth'; reasons.append('revenue compounding 8–20% with established operations')
    elif last_om is not None and last_om < 0 and g is not None and g >= 0:
        stage = 'young_growth'; reasons.append('loss-making without a mature revenue base')
    else:
        stage = 'mature_stable'; reasons.append('low growth with established operations')
    # Recent quarters can overturn what annual history shows (turning points).
    momentum = quarter_momentum(quarters(case))
    inflection = slowdown = False
    if momentum:
        qy, qm = momentum['latest_quarter_yoy'], momentum['latest_quarter_operating_margin']
        if qy >= .5 and (qm is None or qm > 0) and stage in {'decline', 'mature_stable', 'young_growth', 'mature_growth'}:
            inflection = True
            reasons.append(f'annual history said {stage}, but the latest quarter grew {qy:.0%} year on year' + (' and is profitable' if qm else ''))
            stage = 'high_growth'
        elif qy <= -.2 and stage in {'high_growth', 'mature_growth', 'young_growth'}:
            slowdown = True
    # Lynch category.
    market_cap = case.get('market_cap')
    net_cash = None
    if case.get('balance'):
        net_cash = case['balance']['cash'] - case['balance']['debt']
    cyclical = archetype in CYCLICAL_ARCHETYPES or bool(case.get('cyclical')) or (
        archetype in MAYBE_CYCLICAL and volatility is not None and volatility >= .05 and revenue_drop)
    if (last_om is not None and last_om < 0 and was_profitable) or (stage == 'decline' and recovered) or (inflection and last_om is not None and last_om < 0):
        lynch = 'turnaround'
    elif cyclical:
        lynch = 'cyclical'
    elif market_cap and net_cash is not None and net_cash >= .5 * market_cap:
        lynch = 'asset_play'
    elif g is not None and g >= .2 and not large_mature:
        lynch = 'fast_grower'
    elif g is not None and g >= .08 or large_mature or (market_cap and market_cap >= 1e10 and g is not None and g >= .04):
        lynch = 'stalwart'
    else:
        lynch = 'slow_grower'
    flags = []
    if bought:
        flags.append({'key': 'acquisitions', 'he': 'ב-' + ', '.join(bought) + ' הייתה רכישה גדולה (המוניטין במאזן קפץ). חלק מהצמיחה נקנה ולא נבנה — לכן הסיווג מסתכל על הצמיחה בלי השנים האלה. צריך לבדוק שהרכישה מחזירה את המחיר ששולם.'})
    if organic is not None and yoy is not None and yoy - organic > .05:
        flags.append({'key': 'acquisition_driven', 'he': 'חלק מהצמיחה בשנה האחרונה הגיע מרכישות ולא מהעסק עצמו — הצמיחה האורגנית נמוכה יותר.'})
    if lynch == 'cyclical' and len(om) >= 3:
        avg = sum(om) / len(om)
        if last_om > avg + .05 or (avg > 0 and last_om > 1.5 * avg):
            flags.append({'key': 'cycle_peak', 'he': 'הרווחיות עכשיו גבוהה בהרבה מהממוצע שלה — ייתכן שזה שיא מחזורי. אסור להניח שזה יימשך.'})
        elif last_om < avg - .05:
            flags.append({'key': 'cycle_trough', 'he': 'הרווחיות עכשיו נמוכה בהרבה מהממוצע שלה — ייתכן שזו תחתית מחזורית. השאלה היא אם המחזור יחזור.'})
        evidence['normalized_operating_margin'] = avg
    if inflection:
        flags.append({'key': 'inflection', 'he': 'נקודת מפנה: השנים המלאות נראות חלשות, אבל ברבעונים האחרונים המכירות זינקו והחברה עברה לרווח. צריך לבדוק אם זה שינוי אמיתי ומתמשך או קפיצה זמנית.'})
    if slowdown:
        flags.append({'key': 'slowdown', 'he': 'ברבעון האחרון המכירות ירדו חזק לעומת השנה שעברה — הצמיחה של השנים הקודמות אולי נגמרת.'})
    if n < 3:
        flags.append({'key': 'short_history', 'he': 'יש פחות משלוש שנים של נתונים — הסיווג זהיר.'})
    override = case.get('type_override')
    computed = {'stage': stage, 'lynch': lynch}
    if override:
        if override.get('stage') not in (None,) + STAGES or override.get('lynch') not in (None,) + LYNCH:
            raise ValueError('Unknown type_override stage/lynch')
        txt(override.get('reason'), 'type_override.reason')
        stage = override.get('stage') or stage
        lynch = override.get('lynch') or lynch
    evidence['quarter_momentum'] = momentum
    evidence.update({'revenue_cagr': growth, 'growth_used_for_type': g, 'acquisition_years': bought, 'large_mature_profile': large_mature, 'revenue_growth_last_year': yoy, 'organic_growth_last_year': organic,
                     'operating_margin_last': last_om, 'operating_margin_change': om_trend,
                     'operating_margin_volatility': volatility, 'years_of_history': n})
    play = PLAYBOOK[stage]
    return {'stage': stage, 'stage_he': STAGE_HE[stage], 'lynch': lynch, 'lynch_he': LYNCH_HE[lynch],
            'plain_stage': PLAIN_STAGE[stage], 'computed': computed, 'override': override,
            'reasons': reasons, 'flags': flags, 'evidence': evidence,
            'what_decides_value_he': play['decides'], 'key_metrics': play['metrics'],
            'valuation_fit': play['valuation'] + (' Cyclical: value on normalized mid-cycle margins, not the latest year.' if lynch == 'cyclical' else ''),
            'traps': play['traps'], 'lynch_note_he': LYNCH_NOTES[lynch],
            'packets': list(dict.fromkeys(['company_type'] + play['packets'] + (['forensic'] if lynch in {'turnaround', 'cyclical'} or stage == 'decline' else []))),
            'meaning': 'Coarse rule-based typing from reported history to choose research focus and model fit. Hybrid businesses may need several playbooks; override with a stated reason.'}


def classify_company(case):
    if case.get('plain_version') != 1:
        raise ValueError('plain_version must be 1 (same input as plain_verdict)')
    for k in ('company', 'ticker'):
        txt(case[k], k)
    return classify(case)
