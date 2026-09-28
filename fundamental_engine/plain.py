"""Plain-language verdicts: turn reported history and an executed valuation into
eye-level Hebrew labels ("good / not good") with a short everyday explanation.

The thresholds are transparent rules of thumb, adjusted by business economics.
They grade reported numbers; they do not verify sources, predict prices or place
trades. Quality, growth, financial strength and price stay separate axes. The
price verdict exists only when value_company actually ran in this call.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from .finance import number
from .history import txt as _txt, history as _history
from .profile import classify
from .scores import scorecards, forecast_base_rate
from .growth import growth_lane
from . import __version__

GRADES = {5: ('מצוין', '🟢'), 4: ('טוב', '🟢'), 3: ('בינוני', '🟡'), 2: ('חלש', '🟠'), 1: ('מדאיג', '🔴')}
# Gross/operating margin bars differ by economics: software keeps most of each sale,
# a factory or a retailer structurally keeps less. Never a reason to relabel a sector.
MARGIN_BARS = {
    'high_margin': {'gross': (.75, .6, .45, .3), 'operating': (.3, .18, .08, 0)},
    'general': {'gross': (.5, .38, .25, .15), 'operating': (.2, .12, .06, 0)},
    'thin_margin': {'gross': (.35, .25, .17, .1), 'operating': (.12, .08, .04, 0)},
}
PROFILE = {'software': 'high_margin', 'platform': 'high_margin', 'biotech': 'high_margin',
           'industrial': 'general', 'infrastructure': 'general', 'conglomerate': 'general',
           'nonfinancial': 'general', 'turnaround': 'general', 'consumer': 'thin_margin',
           'commodity': 'thin_margin'}
STAGES = {'mature', 'growth', 'emerging'}


def _grade(value, bars, higher_is_better=True):
    """bars = thresholds for grades 5,4,3,2 (descending when higher is better)."""
    for points, bar in zip((5, 4, 3, 2), bars):
        if (value >= bar) if higher_is_better else (value <= bar):
            return points
    return 1


def _item(key, topic, points, headline, explain, figure=None, trend=None):
    label, light = GRADES[points]
    return {'key': key, 'topic': topic, 'points': points, 'label': label, 'light': light,
            'headline': headline, 'explain': explain, 'figure': figure, 'trend': trend}


def fraction_words(x):
    """Everyday Hebrew for a share of the whole: 'בערך רבע', 'קצת יותר משליש'."""
    if x <= 0:
        return 'כלום'
    if x < .07:
        return 'חלק קטן מאוד'
    if x > .95:
        return 'כמעט הכל'
    anchors = [(.1, 'עשירית'), (.2, 'חמישית'), (.25, 'רבע'), (1 / 3, 'שליש'), (.5, 'חצי'),
               (2 / 3, 'שני שלישים'), (.75, 'שלושה רבעים')]
    value, word = min(anchors, key=lambda a: abs(a[0] - x))
    if abs(value - x) < .025:
        return 'בערך ' + word
    return ('קצת יותר מ' if x > value else 'קצת פחות מ') + word


def years_words(y):
    if y < .5:
        return 'כמה חודשים'
    if y < 1:
        return 'פחות משנה'
    if y < 1.5:
        return 'בערך שנה'
    if y < 2.5:
        return 'בערך שנתיים'
    return f'בערך {round(y)} שנים'


def price_vs_value_words(price, value):
    """'המחיר הוא בערך חצי מהשווי' / 'המחיר גבוה מהשווי בערך בשליש'."""
    r = price / value
    if .93 <= r <= 1.07:
        return 'המחיר קרוב מאוד לשווי'
    if r < 1:
        return 'המחיר הוא ' + fraction_words(r) + ' מהשווי'
    if r >= 1.9:
        return 'המחיר גבוה מהשווי ' + multiple_words(r)
    return 'המחיר גבוה מהשווי ב' + fraction_words(r - 1).replace('בערך ', 'בערך ב')


def change_words(new, old):
    """Change from old to new in words, for scenario values against the quote."""
    r = new / old
    if r < 1:
        return ('ירידה קטנה' if r > .9 else 'ירידה של ' + fraction_words(1 - r)) + ' מהמחיר'
    return ('עלייה קטנה' if r < 1.1 else multiple_words(r)) + ' לעומת המחיר'


def multiple_words(m):
    """A growth factor in words: 1.05 -> 'בערך אותו דבר', 2.1 -> 'בערך פי 2'."""
    if m < .5:
        return 'פחות מחצי ממה שהיה'
    if m < .95:
        return 'ירידה של ' + fraction_words(1 - m)
    if m <= 1.05:
        return 'בערך אותו דבר'
    if m < 1.9:
        return 'עלייה של ' + fraction_words(m - 1)
    return 'בערך פי ' + (str(round(m)) if m >= 2.75 else ('2' if m < 2.25 else '2.5'))


def per_hundred(margin, currency):
    return f'מכל 100 {currency} של מכירות נשארים בערך {round(margin * 100)}'


def _growth(rows, stage):
    if len(rows) < 2:
        return None
    last, prev = rows[-1]['revenue'], rows[-2]['revenue']
    if prev <= 0:
        return _item('growth', 'צמיחה', 3, 'עוד אין בסיס מכירות להשוואה',
                     'בשנה הקודמת כמעט לא היו מכירות, אז אחוזי צמיחה כאן מטעים. מה שחשוב זה אם הלקוחות משלמים ומזמינים שוב.',
                     {'revenue_last': last, 'revenue_previous': prev})
    yoy = last / prev - 1
    years = len(rows) - 1
    first = rows[0]['revenue']
    cagr = (last / first) ** (1 / years) - 1 if first > 0 and years > 1 else None
    points = _grade(yoy, (.3, .15, .05, 0))
    words = {5: 'המכירות גדלות מהר מאוד', 4: 'המכירות גדלות יפה', 3: 'המכירות גדלות לאט',
             2: 'המכירות כמעט עומדות במקום', 1: 'המכירות יורדות'}[points]
    explain = f'בשנה האחרונה: {multiple_words(last / prev)} לעומת השנה הקודמת.'
    if cagr is not None:
        explain += f' לאורך {years} שנים: {multiple_words(last / first)} בסך הכל.'
    trend = None
    if len(rows) >= 3 and rows[-3]['revenue'] > 0:
        before = prev / rows[-3]['revenue'] - 1
        if yoy > before + .05:
            trend, explain = 'accelerating', explain + ' והקצב מאיץ — סימן טוב.'
        elif yoy < before - .05:
            trend, explain = 'decelerating', explain + ' אבל הקצב מאט — צריך להבין למה (זמני או מבני).'
    if points == 1:
        explain += ' ירידה במכירות היא לא סוף העולם אם היא זמנית — אבל חייבים לבדוק אם הלקוחות עוזבים.'
    if stage == 'emerging' and points >= 4:
        explain += ' בשלב מוקדם צמיחה מבסיס קטן קלה יותר; מה שקובע זה אם היא נמשכת כשהבסיס גדל.'
    return _item('growth', 'צמיחה', points, words, explain,
                 {'revenue_growth_last_year': yoy, 'revenue_cagr': cagr}, trend)


def _margins(rows, bars, currency, stage):
    out = []
    last = rows[-1]
    rev = last['revenue']
    if rev <= 0:
        return out
    gp = last.get('gross_profit')
    if gp is not None:
        gm = gp / rev
        points = _grade(gm, bars['gross'])
        words = {5: 'המוצר רווחי מאוד מהיסוד', 4: 'המוצר רווחי', 3: 'רווחיות מוצר סבירה',
                 2: 'המוצר מרוויח מעט', 1: 'המוצר כמעט לא מרוויח'}[points]
        out.append(_item('gross_margin', 'רווחיות המוצר', points, words,
                         per_hundred(gm, currency) + ' אחרי עלות הייצור/השירות עצמו (לפני משכורות הנהלה, שיווק ופיתוח).'
                         + (' הרווח הגולמי גבוה; צריך לבדוק אם הוא נובע מיתרון עמיד, מתמהיל או משלב במחזור.' if points >= 4 else '')
                         + (' הרווח הגולמי נמוך; צריך לבדוק את תמהיל המוצרים והעלויות לפני מסקנה על כוח התמחור.' if points <= 2 else ''),
                         {'gross_margin': gm}))
    oi = last.get('operating_income')
    if oi is not None:
        om = oi / rev
        trend = None
        first = rows[0]
        if len(rows) >= 2 and first['revenue'] > 0 and first.get('operating_income') is not None:
            delta = om - first['operating_income'] / first['revenue']
            trend = 'improving' if delta > .03 else ('worsening' if delta < -.03 else 'stable')
        points = _grade(om, bars['operating'])
        if om < 0:
            head = 'העסק עוד מפסיד'
            explain = f'כרגע כל 100 {currency} מכירות עולים לחברה בערך {round(100 - om * 100)} — היא מוציאה יותר ממה שהיא מכניסה.'
            if stage == 'emerging':
                points = 3 if trend == 'improving' else 2
                explain += ' בחברה צעירה זה נורמלי כל עוד ההפסד מצטמצם והכסף בקופה מספיק לדרך.'
        else:
            head = {5: 'העסק מרוויח המון', 4: 'העסק מרוויח יפה', 3: 'העסק מרוויח, אבל לא הרבה',
                    2: 'העסק בקושי מרוויח', 1: 'העסק בקושי מרוויח'}[points]
            explain = per_hundred(om, currency) + ' אחרי כל ההוצאות של הפעילות (משכורות, שיווק, פיתוח) — לפני מס וריבית.'
        explain += {'improving': ' והמגמה משתפרת לאורך השנים.', 'worsening': ' והמגמה נשחקת לאורך השנים — צריך להבין למה.',
                    'stable': ' והרמה די יציבה לאורך השנים.', None: ''}[trend]
        out.append(_item('operating_margin', 'רווחיות העסק', points, head, explain, {'operating_margin': om}, trend))
    return out


def _cash(rows, balance, currency, stage):
    out = []
    last = rows[-1]
    ocf, capex, ni = last.get('operating_cash_flow'), last.get('capex'), last.get('net_income')
    fcf = None
    if ocf is not None and capex is not None and last['revenue'] > 0:
        fcf = ocf - capex
        fm = fcf / last['revenue']
        points = _grade(fm, (.2, .1, .03, 0))
        head = {5: 'מייצר הרבה כסף מזומן', 4: 'מייצר כסף מזומן יפה', 3: 'מייצר קצת כסף מזומן',
                2: 'כמעט לא נשאר כסף מזומן', 1: 'שורף כסף מזומן'}[points]
        explain = ('אחרי שהחברה משלמת על הכל, כולל השקעות בציוד ובמבנים, ' +
                   (f'נשארים לה בערך {round(fm * 100)} {currency} מזומן מכל 100 {currency} מכירות. זה הכסף האמיתי שאפשר להשתמש בו לצמיחה, להחזר חוב או לבעלי המניות.'
                    if fcf >= 0 else 'יוצא ממנה יותר כסף ממה שנכנס. מישהו צריך לממן את הפער — הקופה, הלוואות או הנפקת מניות.'))
        if fcf < 0 and stage == 'emerging':
            points = max(points, 2)
            explain += ' בשלב מוקדם זה צפוי; השאלה היא לכמה זמן הכסף מספיק.'
        out.append(_item('free_cash_flow', 'כסף מזומן', points, head, explain, {'free_cash_flow': fcf, 'free_cash_flow_margin': fm}))
    if ocf is not None and ni is not None and ni > 0:
        q = ocf / ni
        points = _grade(q, (1.1, .9, .7, .5))
        head = 'הרווח אמיתי — הוא נכנס לקופה' if q >= .9 else ('הרווח חלקית על הנייר' if q >= .5 else 'הרווח בעיקר על הנייר')
        explain = ('הכסף שנכנס בפועל מהפעילות גדול או שווה לרווח שמדווח. זה סימן בריא.' if q >= .9 else
                   'הרווח בדוחות גדול מהכסף שנכנס בפועל. לפעמים זה בגלל מלאי או לקוחות שעוד לא שילמו — צריך לבדוק שזה לא הופך להרגל.')
        out.append(_item('earnings_quality', 'איכות הרווח', points, head, explain, {'cash_to_net_income': q}))
    if balance:
        cash, debt = number(balance['cash'], 'cash', 0), number(balance['debt'], 'debt', 0)
        net = debt - cash
        if net <= 0:
            points, head = 5, 'יש יותר מזומן מחוב'
            explain = 'לחברה יש בקופה יותר כסף ממה שהיא חייבת. היא לא תלויה בבנקים ויכולה לעבור תקופה קשה.'
            years_to_pay = None
        elif ocf is not None and ocf > 0:
            years_to_pay = net / ocf
            points = _grade(years_to_pay, (0, 1, 3, 5), higher_is_better=False)
            head = {4: 'חוב קטן', 3: 'חוב סביר', 2: 'חוב כבד', 1: 'חוב כבד מאוד'}[points]
            explain = f'כדי להחזיר את החוב נטו (חוב פחות מזומן) מהכסף שהפעילות מייצרת, צריך {years_words(years_to_pay)}. ' + (
                'זה בסדר גמור.' if points >= 4 else 'זה סביר, אבל עלייה בריבית או שנה חלשה ירגישו.' if points == 3 else
                'זה הרבה — בשנה רעה החוב עלול להפוך לבעיה ולהכריח גיוס כסף.')
        else:
            points, head, years_to_pay = 1, 'חוב בלי כסף שנכנס לשלם אותו', None
            explain = 'לחברה יש חוב נטו והפעילות עוד לא מייצרת מזומן. היא תלויה במימון חיצוני.'
        out.append(_item('balance_sheet', 'חוב וקופה', points, head, explain,
                         {'cash': cash, 'debt': debt, 'net_debt': net, 'net_debt_to_operating_cash_years': years_to_pay}))
        if fcf is not None and fcf < 0:
            runway = cash / -fcf
            points = _grade(runway, (5, 3, 1.5, .75))
            head = {5: 'הכסף מספיק להרבה זמן', 4: 'הכסף מספיק לכמה שנים', 3: 'הכסף מספיק לשנה-שנתיים',
                    2: 'הכסף נגמר תוך כשנה', 1: 'הכסף עומד להיגמר'}[points]
            explain = f'בקצב השריפה הנוכחי הקופה מחזיקה {years_words(runway)}.' + (
                ' צפוי גיוס הון, וגיוס כזה מקטין את החלק שלך בחברה (דילול).' if points <= 3 else '')
            out.append(_item('runway', 'כמה זמן הכסף מחזיק', points, head, explain, {'runway_years': runway}))
    return out


def _dilution(rows, current=None):
    known = [r for r in rows if r.get('shares_diluted')]
    point_history = [r for r in rows if r.get('shares_outstanding')]
    if current and current.get('basis') == 'point_in_time_common_outstanding' and point_history:
        prior = point_history[-1]
        if current['as_of'] > prior['period_end']:
            jump = current['shares'] / prior['shares_outstanding'] - 1
            points = _grade(jump, (0, .02, .05, .1), higher_is_better=False)
            return _item('dilution', 'שינוי במספר המניות', points,
                         'דילול כבד לאחרונה' if jump > .1 else 'שינוי במספר המניות בפועל',
                         f"בין {prior['period_end']} ל־{current['as_of']} מספר המניות בפועל השתנה ב־{jump:.1%}. "
                         'יש לבדוק הנפקות, תגמול מנייתי, רכישות עצמיות ופיצולים; השינוי אינו מלמד לבדו למה הונפקו מניות.',
                         {'shares_increase_since_last_annual': jump, 'basis': 'point_in_time_common_outstanding'})
    if len(known) < 2:
        return None
    years = (date.fromisoformat(known[-1]['period_end']) - date.fromisoformat(known[0]['period_end'])).days / 365.25
    if years <= 0:
        return None
    rate = (known[-1]['shares_diluted'] / known[0]['shares_diluted']) ** (1 / years) - 1
    points = _grade(rate, (0, .02, .05, .1), higher_is_better=False)
    head = {5: 'לא מדללת — ואפילו קונה מניות בחזרה' if rate < -.005 else 'לא מדללת את בעלי המניות',
            4: 'דילול קטן', 3: 'דילול מורגש', 2: 'דילול כבד', 1: 'דילול כבד מאוד'}[points]
    explain = ('כמות המניות לא עולה, אז כל הצמיחה שייכת לך באותו חלק.' if points == 5 else
               'החברה מוסיפה מניות כל שנה (לעובדים או לגיוס כסף). העוגה גדלה, אבל מתחלקת לעוד חתיכות — ' +
               ('זה עדיין קטן.' if points == 4 else 'זה אוכל חלק מהתשואה שלך.' if points == 3 else 'זה אוכל חלק גדול מהתשואה שלך.'))
    return _item('dilution', 'מגמת ממוצע המניות', points, head, explain + ' זהו שינוי בממוצע מניות מדולל בין שנים; אינו מדידת דילול מאז סוף השנה.', {'share_count_growth_per_year': rate, 'basis': 'weighted_average_diluted'})


def _valuation(case):
    source = case.get('valuation_case')
    if source is None:
        return None, None, None
    from .valuation import value_company
    result = value_company(source)
    if source['ticker'] != case['ticker'] or source['currency'] != case['currency'] or source['as_of'] != case['as_of'] or bool(source.get('is_demo')) != bool(case.get('is_demo')):
        raise ValueError('valuation_case ticker/currency/date/demo must match the plain case')
    status = result['status']
    today = result['annual_values'][0]
    quote = today['current_quote']
    summary = {'executed': True, 'input_sha256': result['input_sha256'], 'status': status,
               'underwriting_audit': result['underwriting_audit'], 'annual_path_semantics': result['annual_path_semantics'],
               'is_demo': result['is_demo'], 'warnings': result['warnings'], 'annual_values': result['annual_values'],
               'base_issues': next((x.get('issues', []) for x in result['scenarios'] if x['name'] == 'base'), [])}
    if status == 'funding_blocked' or today.get('base') is None:
        return summary, result, {'bucket': 'blocked', 'light': '🔴', 'headline': 'אי אפשר לתת שווי כרגע',
                         'explain': 'לפי התחזית החברה צריכה כסף שאין לו מקור מוכח. עד שיהיה מימון ברור, כל "שווי" הוא ניחוש. זו נורת אזהרה, לא הערכת שווי.'}
    research_ok = False
    dossier = case.get('research_dossier')
    if dossier is not None:
        from .supervisor import review
        checked = review(dossier)
        executed = checked.get('annual_valuation')
        if not executed or executed['input_sha256'] != result['input_sha256']:
            raise ValueError('research_dossier must execute the identical valuation input')
        research_ok = checked['gates']['priced_conclusion_ready']
        case['_executed_research_status'] = checked['status']
    summary['research_review_executed'] = dossier is not None
    summary['priced_conclusion_ready'] = research_ok and result['underwriting_audit']['eligible_for_research_synthesis']
    if not result['is_demo'] and not summary['priced_conclusion_ready']:
        return summary, result, {'bucket': 'review_required', 'light': '⚪',
            'headline': 'השווי דורש בדיקה לפני מסקנת מחיר',
            'explain': 'החישוב מוצג כטיוטה מותנית. יש להשלים התאמת נתונים ובדיקת מחקר על אותו מודל; אין עדיין בסיס לקביעה זול או יקר.',
            'quote': quote, 'bear': today['bear'], 'base': today['base'], 'bull': today['bull']}
    bear, base, bull = today['bear'], today['base'], today['bull']
    ratio = quote / base if base > 0 else float('inf')
    if bear is not None and quote <= bear:
        bucket, light, head = 'cheap_even_bear', '🟢', 'זול — גם בתרחיש הרע'
    elif ratio <= .8:
        bucket, light, head = 'cheap', '🟢', 'זול ביחס לשווי'
    elif ratio <= 1.1:
        bucket, light, head = 'fair', '🟡', 'בערך במחיר הוגן'
    elif bull is not None and quote <= bull:
        bucket, light, head = 'expensive', '🟠', 'יקר — המחיר כבר מניח הצלחה'
    else:
        bucket, light, head = 'very_expensive', '🔴', 'יקר מאוד — גם לעומת התרחיש האופטימי'
    cur = case['currency']
    explain = (f'המחיר היום בערך {quote:,.2f} {cur}. לפי התרחיש הסביר (בסיס) המניה שווה היום בערך {base:,.2f} {cur}'
               + (f' — {price_vs_value_words(quote, base)}.' if base > 0 else '.'))
    if bear is not None:
        explain += f' בתרחיש הרע השווי בערך {bear:,.2f} {cur}' + (f' ({change_words(bear, quote)}).' if bear > 0 else ' (כמעט כלום).')
    if bull is not None:
        explain += f' בתרחיש הטוב בערך {bull:,.2f} {cur} ({change_words(bull, quote)}).'
    path = [r for r in result['annual_values'][1:] if r.get('base') is not None]
    if path:
        end = path[-1]
        explain += (f' אם תרחיש הבסיס מתממש, השווי ב-{end["date"][:4]} יהיה בערך {end["base"]:,.2f} {cur}'
                    f' ({change_words(end["base"], quote)} של היום). זה שווי מותנה בתחזית, לא תחזית למחיר בבורסה.')
    return summary, result, {'bucket': bucket, 'light': light, 'headline': head, 'explain': explain,
                     'quote': quote, 'bear': bear, 'base': base, 'bull': bull, 'price_to_base_value': ratio}


def _confidence(case, valuation, items, base_rate=None, scores=None):
    reasons = []
    if base_rate and base_rate['flags']:
        reasons.append('התרחיש הבסיסי מניח צמיחה שנדירה ביחס להיסטוריה ולשיעורי בסיס')
    if scores and scores['beneish'].get('zone') == 'likely_manipulator':
        reasons.append('מדד בניש מסמן סיכון לניפוח רווחים — צריך לבדוק את הדוחות לעומק')
    review = case.get('_executed_research_status', case.get('research_status') if case.get('is_demo') else None)
    if review != 'ready_for_conditional_synthesis':
        reasons.append('המחקר עוד לא עבר את בדיקת התהליך (research_review) עד הסוף')
    if valuation:
        if not valuation['is_demo'] and not valuation.get('priced_conclusion_ready'):
            reasons.append('נתוני השווי והנחותיו טרם עברו התאמה ובדיקת מחקר על אותו קלט')
        if valuation['is_demo']:
            reasons.append('זו דוגמה מומצאת, לא חברה אמיתית')
        if valuation['status'] == 'unreviewed':
            reasons.append('יש הנחות בהערכת השווי שעוד לא נבדקו')
        if any('80%' in w for w in valuation['base_issues']):
            reasons.append('רוב השווי מגיע משנים רחוקות')
        if any('7 calendar days' in w for w in valuation['warnings']):
            reasons.append('המחיר שהושווה ישן מיותר משבוע')
    if len(case['history']) < 3:
        reasons.append('יש פחות משלוש שנים של נתונים')
    if len(items) < 4:
        reasons.append('חסרים נתונים לחלק מהבדיקות')
    level = 'גבוה' if not reasons else ('בינוני' if len(reasons) <= 1 else 'נמוך')
    return {'level': level, 'reasons': reasons}


def _score_items(scores, profile):
    out = []
    r = scores['roic']
    if r['status'] == 'computed':
        v = r['roic_last']
        points = _grade(v, (.2, .12, .08, .04))
        head = {5: 'מחזיר המון על כל שקל שהושקע', 4: 'מחזיר יפה על ההשקעה', 3: 'מחזיר בערך את עלות הכסף',
                2: 'מחזיר פחות מעלות הכסף', 1: 'לא מחזיר את ההשקעה'}[points]
        explain = (f'על כל 100 שהושקעו בעסק (הון וחוב) הוא מרוויח בשנה בערך {round(v * 100)} אחרי מס. '
                   + ('זה הרבה מעל מה שעולה לגייס כסף — סימן לחפיר (יתרון תחרותי).' if points >= 4 else
                      'זה בערך מה שעולה לגייס כסף — הצמיחה לא מייצרת הרבה ערך.' if points == 3 else
                      'זה פחות ממה שעולה לגייס כסף — כל צמיחה כזו עלולה דווקא להרוס ערך.'))
        inc = r['incremental_roic']
        if inc is not None:
            explain += (' והכסף החדש שהושקע בשנים האחרונות מחזיר אפילו יותר — מצוין.' if inc > v + .03 else
                        ' אבל הכסף החדש שהושקע מחזיר פחות — שווה לבדוק למה.' if inc < v - .05 else '')
        out.append(_item('roic', 'תשואה על ההון', points, head, explain, {'roic': v, 'incremental_roic': inc}))
    b = scores['beneish']
    if b['status'] == 'computed':
        points = {'unlikely': 4, 'watch': 3, 'likely_manipulator': 1}[b['zone']]
        head = {'unlikely': 'אין סימן לניפוח רווחים', 'watch': 'כדאי לשים עין על הדוחות', 'likely_manipulator': 'דגל אדום בדוחות'}[b['zone']]
        explain = {'unlikely': 'בדיקת בניש (מודל מוכר לזיהוי ניפוח רווחים) לא מצאה סימנים חשודים.',
                   'watch': 'בדיקת בניש קרובה לקו האזהרה. לרוב זה בגלל צמיחה מהירה, אבל שווה לבדוק לקוחות שלא משלמים ומלאי.',
                   'likely_manipulator': 'בדיקת בניש מעל קו האזהרה: לקוחות שלא משלמים, רווח שלא נכנס כמזומן או שינויים חשבונאיים. זה לא הוכחה — אבל חייבים לבדוק לפני שסומכים על המספרים.'}[b['zone']]
        out.append(_item('accounting_risk', 'אמינות הדוחות', points, head, explain, {'beneish_m': b['m']}))
    a = scores['altman']
    if a['status'] == 'computed':
        points = {'safe': 5, 'grey': 3, 'distress': 1}[a['zone']]
        head = {'safe': 'רחוק מסכנת קריסה', 'grey': 'אזור אפור', 'distress': 'סימני מצוקה כספית'}[a['zone']]
        explain = {'safe': 'מדד אלטמן (בדיקה מוכרת לסיכון פשיטת רגל) נמצא באזור הבטוח.',
                   'grey': 'מדד אלטמן באזור האפור — לא מסוכן, אבל גם לא בטוח לגמרי. שווה לבדוק חוב ותזרים.',
                   'distress': 'מדד אלטמן באזור המצוקה. זה לא אומר שהחברה תקרוס, אבל היסטורית זה אזור של סיכון גבוה.'}[a['zone']]
        out.append(_item('distress', 'סיכון קריסה', points, head, explain, {'altman_z': a['z']}))
    f = scores['piotroski']
    if f['status'] == 'computed':
        points = {'strong': 5, 'mixed': 3, 'weak': 1}[f['zone']]
        head = {'strong': 'המצב הכספי משתפר', 'mixed': 'המצב הכספי מעורב', 'weak': 'המצב הכספי נחלש'}[f['zone']]
        explain = (f'בדיקת פיוטרוסקי (9 סימנים לשיפור: רווח, מזומן, חוב, נזילות, דילול, רווחיות ויעילות) — '
                   f'{f["score"]} מתוך {f["out_of"]} סימנים חיוביים.')
        out.append(_item('fscore', 'מגמה כספית', points, head, explain, {'piotroski': f['score'], 'out_of': f['out_of']}))
    r40 = scores['rule_of_40']
    if r40['status'] == 'computed':
        v = r40['score']
        points = _grade(v, (.5, .4, .25, .1))
        head = {5: 'איזון מצוין בין צמיחה לרווח', 4: 'עובר את כלל ה-40', 3: 'קרוב, אבל לא עובר את כלל ה-40',
                2: 'צמיחה ורווח חלשים יחד', 1: 'לא צומח ולא מרוויח מספיק'}[points]
        explain = (f'בתוכנה בודקים "כלל 40": קצב הצמיחה ועוד הרווח המזומן צריכים לעבור יחד 40. כאן זה בערך {round(v * 100)}. '
                   'רוב חברות התוכנה הציבוריות לא עוברות אותו.')
        out.append(_item('rule_of_40', 'כלל ה-40', points, head, explain, {'rule_of_40': v}))
    return out


# Axis weights by life-cycle stage: early companies are judged mostly on growth and
# survival; mature ones on quality of returns and cash (Damodaran life cycle).
WEIGHTS = {'start_up': (.2, .5, .3), 'young_growth': (.2, .5, .3), 'high_growth': (.35, .4, .25),
           'mature_growth': (.45, .3, .25), 'mature_stable': (.5, .15, .35), 'decline': (.4, .2, .4)}


def _avg(items, keys):
    pts = [i['points'] for i in items if i['key'] in keys]
    return sum(pts) / len(pts) if pts else None


def _axis(name, score, words):
    if score is None:
        return {'axis': name, 'score': None, 'label': 'אין מספיק נתונים', 'light': '⚪'}
    points = max(1, min(5, round(score)))
    return {'axis': name, 'score': round(score, 2), 'label': words[points], 'light': GRADES[points][1]}


def plain_verdict(case):
    case = deepcopy(case)
    if case.get('plain_version') != 1:
        raise ValueError('plain_version must be 1')
    json.dumps(case, allow_nan=False)
    for k in ('company', 'ticker', 'currency'):
        _txt(case[k], k)
    date.fromisoformat(case['as_of'])
    archetype = case.get('archetype', 'nonfinancial')
    if archetype in {'financials', 'reit', 'asset_holding'}:
        raise ValueError('Banks/insurers/REITs need dedicated metrics (book value, capital, NAV); not graded with operating margins')
    if archetype not in PROFILE:
        raise ValueError('Unknown archetype')
    rows = _history(case)
    if case.get('market_cap') is not None:
        number(case['market_cap'], 'market_cap', 0)
    kind = classify(case, rows)
    stage = case.get('stage', kind['plain_stage'])
    if stage not in STAGES:
        raise ValueError('stage must be mature/growth/emerging')
    cur = case['currency']
    items = [i for i in [_growth(rows, stage)] if i]
    qm = kind['evidence'].get('quarter_momentum')
    if qm and items and items[0]['key'] == 'growth':
        g = items[0]
        q = qm['latest_quarter_yoy']
        g['explain'] += (f" ברבעון האחרון ({qm['latest_quarter_end']}): {multiple_words(1 + q)} לעומת אותו רבעון בשנה שעברה"
                         + (' — ובמצב של רווח תפעולי.' if (qm['latest_quarter_operating_margin'] or 0) > 0 else '.'))
        if q >= .5 and g['points'] < 4:
            g['points'], g['label'], g['light'] = 4, GRADES[4][0], GRADES[4][1]
            g['headline'] = 'השנים המלאות חלשות, אבל עכשיו המכירות מזנקות'
        elif q <= -.2 and g['points'] > 2:
            g['points'], g['label'], g['light'] = 2, GRADES[2][0], GRADES[2][1]
            g['headline'] = 'המכירות נחלשות ברבעונים האחרונים'
        g['figure']['latest_quarter_yoy'] = q
    items += _margins(rows, MARGIN_BARS[PROFILE[archetype]], cur, stage)
    items += _cash(rows, case.get('balance'), cur, stage)
    current = case.get('current_shares')
    if current is not None:
        number(current['shares'], 'current_shares.shares', 1)
        if current.get('basis') != 'point_in_time_common_outstanding':
            raise ValueError('current_shares requires point_in_time_common_outstanding; weighted EPS averages belong in history')
        if date.fromisoformat(current['as_of']) > date.fromisoformat(case['as_of']) or not current.get('source_ids'):
            raise ValueError('current_shares needs as_of <= case as_of and source_ids')
    d = _dilution(rows, current)
    if d: items.append(d)
    if kind['lynch'] == 'cyclical' and 'normalized_operating_margin' in kind['evidence']:
        om = next((i for i in items if i['key'] == 'operating_margin'), None)
        if om:
            om['explain'] += (' זו חברה מחזורית: בממוצע לאורך השנים נשארים בערך '
                              f"{round(kind['evidence']['normalized_operating_margin'] * 100)} מכל 100 — וזה המספר שצריך להעריך לפיו, לא השנה האחרונה.")
    qm = kind['evidence'].get('quarter_momentum')
    if qm and qm['latest_quarter_operating_margin'] is not None:
        m = qm['latest_quarter_operating_margin']
        points = _grade(m, MARGIN_BARS[PROFILE[archetype]]['operating'])
        items.append(_item('recent_margin', 'רווחיות ברבעון האחרון', points,
                           'ברבעון האחרון העסק מרוויח' if m > 0 else 'גם ברבעון האחרון העסק מפסיד',
                           (per_hundred(m, cur) + ' רווח תפעולי ברבעון האחרון. ' if m > 0 else 'ברבעון האחרון ההוצאות עדיין גבוהות מההכנסות. ')
                           + 'רבעון אחד הוא לא הוכחה — צריך לראות שזה חוזר ברבעונים הבאים.', {'latest_quarter_operating_margin': m}))
    scores = scorecards(rows, archetype, case.get('market_cap'))
    items += _score_items(scores, kind)
    valuation, result, price = _valuation(case)
    base_rate = forecast_base_rate(rows, result)
    minority = case.get('minority_share', 0)
    number(minority, 'minority_share', 0, .99)
    lane = growth_lane(case, kind, rows, result, minority)
    axes = [
        _axis('איכות העסק', _avg(items, {'gross_margin', 'operating_margin', 'free_cash_flow', 'earnings_quality', 'roic', 'accounting_risk', 'recent_margin'}),
              {5: 'עסק מצוין', 4: 'עסק טוב', 3: 'עסק בינוני', 2: 'עסק חלש', 1: 'עסק בעייתי'}),
        _axis('צמיחה', _avg(items, {'growth', 'rule_of_40'}),
              {5: 'צומח מהר מאוד', 4: 'צומח יפה', 3: 'צומח לאט', 2: 'כמעט לא צומח', 1: 'מתכווץ'}),
        _axis('חוסן כספי', _avg(items, {'balance_sheet', 'runway', 'dilution', 'distress', 'fscore'}),
              {5: 'חזק מאוד', 4: 'חזק', 3: 'סביר', 2: 'רגיש', 1: 'שביר'}),
    ]
    weights = [w for w, a in zip(WEIGHTS[kind['stage']], axes) if a['score'] is not None]
    scored = [a['score'] for a in axes if a['score'] is not None]
    business = sum(w * x for w, x in zip(weights, scored)) / sum(weights) if scored else None
    bottom = _bottom_line(business, price, stage)
    if any(i['key'] in {'accounting_risk', 'distress'} and i['points'] == 1 for i in items):
        bottom = {**bottom, 'light': '🔴' if bottom['light'] != '⚪' else bottom['light'],
                  'explain': bottom['explain'] + ' שים לב: יש דגל אדום (דוחות או סיכון קריסה) — קודם לברר אותו.'}
    # Growth momentum never overrides an audited price conclusion.
    confidence = _confidence(case, valuation, items, base_rate, scores)
    if lane['payoff'] and lane['payoff']['probabilities_are_default']:
        confidence['reasons'].append('ההסתברויות לתרחישים הן ברירת מחדל לא מכוילת')
        confidence['level'] = 'נמוך' if len(confidence['reasons']) > 1 else 'בינוני'
    out = {'version': __version__, 'company': case['company'], 'ticker': case['ticker'], 'as_of': case['as_of'],
           'currency': cur, 'stage': stage, 'archetype': archetype, 'company_type': kind, 'scores': scores,
           'forecast_base_rate': base_rate, 'valuation_lane': lane, 'items': items, 'axes': axes, 'axis_weights': WEIGHTS[kind['stage']],
           'business_score': round(business, 2) if business is not None else None,
           'price': price, 'valuation': valuation, 'bottom_line': bottom, 'confidence': confidence,
           'input_sha256': sha256(json.dumps(case, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
           'meaning': 'Rule-of-thumb grades of supplied reported figures plus an executed valuation. Research indication only: not verified facts, not a price forecast, not an order or a promised return.'}
    from .decision_summary import decision_summary
    out['decision_summary'] = decision_summary(out, case)
    out['detailed_text'] = render_plain(out)
    style = case.get('response_style', 'concise')
    if style not in {'concise', 'detailed'}:
        raise ValueError('response_style must be concise or detailed')
    out['text'] = out['detailed_text'] if style == 'detailed' else out['decision_summary']['text']
    json.dumps(out, allow_nan=False)
    return out


def _bottom_line(business, price, stage):
    if business is None:
        return {'light': '⚪', 'call': 'אין מספיק נתונים', 'explain': 'חסרים נתונים בסיסיים כדי לשפוט את העסק.'}
    good, ok = business >= 3.5, business >= 2.5
    if price is None:
        call = 'עסק טוב — המחיר עוד לא נבדק' if good else ('עסק בינוני — המחיר עוד לא נבדק' if ok else 'עסק חלש — המחיר עוד לא נבדק')
        return {'light': '🟡' if ok else '🟠', 'call': call,
                'explain': 'לא הורצה הערכת שווי, אז אי אפשר להגיד אם זה זול או יקר. אל תסיק מזה שום דבר על המחיר.'}
    b = price['bucket']
    if b == 'review_required':
        return {'light': '⚪', 'call': 'הערכת השווי עדיין בבדיקה', 'explain': price['explain']}
    if b == 'blocked':
        return {'light': '🔴', 'call': 'זהירות — בעיית מימון', 'explain': 'גם אם העסק מעניין, בלי מימון ברור בעלי המניות של היום עלולים להידלל או להפסיד.'}
    if b == 'very_expensive':
        return {'light': '🔴', 'call': 'יקר מאוד — המחיר מעל גם התרחיש האופטימי',
                'explain': 'המחיר מעל התרחיש האופטימי שהוזן. זהו פער מהמודל, ולא תקרה למחיר או לכל תוצאה עסקית אפשרית. המחיר מגלם הצלחה גדולה עוד יותר ממה שהמודל מצליח להצדיק.'
                           + (' העסק עצמו חזק — הבעיה היא המחיר, לא החברה.' if good else '')}
    cheap, fair = b in {'cheap', 'cheap_even_bear'}, b == 'fair'
    if good and cheap:
        return {'light': '🟢', 'call': 'מעניין מאוד', 'explain': 'עסק טוב שנראה זול ביחס לשווי שחישבנו. שווה להעמיק ולבדוק מה השוק יודע שאנחנו לא.'}
    if good and fair:
        return {'light': '🟡', 'call': 'עסק טוב במחיר הוגן', 'explain': 'אין פה מציאה. התשואה תבוא רק אם העסק ימשיך לבצע כמו בתרחיש הבסיס.'}
    if good:
        return {'light': '🟠', 'call': 'עסק טוב אבל יקר', 'explain': 'המחיר כבר מגלם הצלחה גדולה. שווה לחכות למחיר נמוך יותר או להוכחה שהעסק עוקף את התרחיש הבסיסי.'}
    if ok and cheap:
        return {'light': '🟡', 'call': 'זול, אבל עם סימני שאלה', 'explain': 'המחיר נמוך מהשווי, אבל העסק עצמו לא מושלם. צריך להבין למה הוא זול לפני שמתלהבים.'}
    if ok:
        return {'light': '🟠', 'call': 'לא מספיק משכנע', 'explain': 'עסק בינוני במחיר שלא משאיר מרווח ביטחון.'}
    if cheap:
        return {'light': '🟠', 'call': 'זול מסיבה — אולי מלכודת', 'explain': 'המספרים של העסק חלשים. מחיר נמוך לבד לא מספיק; צריך סיבה ברורה לשיפור.'}
    return {'light': '🔴', 'call': 'להתרחק כרגע', 'explain': 'עסק חלש במחיר שלא מפצה על הסיכון.'}


def render_plain(r):
    """Hebrew Markdown for the user: labels and everyday words first, numbers last."""
    cur = r['currency']
    lines = [f"# {r['company']} ({r['ticker']}) — בגובה העיניים", '',
             f"**שורה תחתונה: {r['bottom_line']['light']} {r['bottom_line']['call']}**", '',
             r['bottom_line']['explain'], '']
    k = r['company_type']
    lines += ['## איזה סוג חברה זו', '',
              f"**{k['stage_he']}** (שלב בחיי החברה) · **{k['lynch_he']}** (לפי פיטר לינץ').", '', k['lynch_note_he'], '',
              'מה באמת קובע את הערך שלה:']
    lines += [f'- {x}' for x in k['what_decides_value_he']]
    lines += [f"- ⚠️ {f['he']}" for f in k['flags']]
    lines += ['', '## התמונה בארבע שורות', '']
    for a in r['axes']:
        lines.append(f"- {a['light']} **{a['axis']}:** {a['label']}")
    p = r['price']
    lines.append(f"- {p['light']} **מחיר מול שווי:** {p['headline']}" if p else '- ⚪ **מחיר מול שווי:** לא נבדק')
    lines += ['', '## מה טוב ומה לא', '', '| נושא | ציון | בקיצור |', '|---|---|---|']
    for i in r['items']:
        lines.append(f"| {i['topic']} | {i['light']} {i['label']} | {i['headline']} |")
    lines += ['', '## ההסבר', '']
    for i in r['items']:
        lines += [f"**{i['topic']} — {i['label']}.** {i['explain']}", '']
    lines += ['## הערכת שווי', '']
    g = r.get('valuation_lane')
    if g:
        lane_he = 'הערכה קלאסית לפי המספרים' if g['lane']['lane'] == 'intrinsic' else 'מסלול צמיחה: ביקוש, קיבולת, מימון ותוצאות אפשריות'
        lines += [f"**איך מעריכים אותה: {lane_he}.** {g['lane']['why_he']}", '']
        m = g['momentum']
        if m['signals']:
            head = {'strong': '🟢 סימני התקדמות בעסק', 'neutral': '🟡 סימני התקדמות מעורבים', 'weak': '🔴 סימני היחלשות בעסק'}[m['label']]
            lines += [f'**{head}:**'] + [f"- {'✅' if s['score'] > 0 else ('❌' if s['score'] < 0 else '➖')} {s['he']}" for s in m['signals']] + ['']
        pay = g['payoff']
        if pay:
            lines.append(f"**הימור: כמה אפשר להפסיד מול כמה אפשר להרוויח.** השווי הממוצע כשמשקללים את כל התרחישים לפי הסיכוי שלהם"
                         f"{' (כולל ההצלחה הגדולה)' if 'tail' in pay['probabilities'] else ''}: בערך {pay['expected_value']:,.2f} {cur} — "
                         f"{price_vs_value_words(p['quote'], pay['expected_value']) if p else ''}. "
                         f"בתרחיש הרע: {change_words(p['bear'], p['quote']) if p and p['bear'] else '—'}; "
                         f"בתרחיש הגבוה ביותר שהוזן: {change_words(max(v for k, v in r['valuation']['annual_values'][0].items() if k in ('bear','base','bull','tail') and v is not None), p['quote']) if p else '—'}. "
                         f"משקל התרחישים שבהם השווי הנוכחי נמוך מהמחיר (אינו הסתברות להפסד עתידי): בערך {fraction_words(pay['probability_below_price'])}.")
            lines.append('')
        mv = g['multiples']
        if mv and mv['rows']:
            bull_row = next((x for x in mv['rows'] if x['scenario'] == 'bull'), None)
            base_row = next((x for x in mv['rows'] if x['scenario'] == 'base'), None)
            if base_row and bull_row:
                lo, mid, hi = mv['exit_pe']
                lines.append(f"**רגישות להנחות מכפיל — אינה שחזור של מודל אנליסט:** ב-{base_row['year']} אומדן רווח תפעולי לאחר מימון ומס למניה בתרחיש הבסיס בערך "
                             f"{base_row['eps']:.2f} ובתרחיש הטוב {bull_row['eps']:.2f}. אם השוק ישלם אז פי {hi} — המחיר יהיה בערך "
                             f"{base_row['price_in_year_at_pe'][hi]:,.0f}–{bull_row['price_in_year_at_pe'][hi]:,.0f} {cur}; אם רק פי {lo} — בערך "
                             f"{base_row['price_in_year_at_pe'][lo]:,.0f}–{bull_row['price_in_year_at_pe'][lo]:,.0f}. המכפיל הוא הנחה; האומדן אינו רווח GAAP או רווח מתואם מאומת לבעלי החברה.")
                lines.append('')
        req = g['required']
        if req and req['ratio']:
            lines.append(f"**מה המחיר דורש:** כדי שהמחיר של היום ייתן תשואה סבירה, החברה צריכה להגיע בעוד כחמש שנים למכירות של בערך "
                         f"{req['required_revenue_5y'] / 1e6:,.0f} מיליון {cur} — {multiple_words(req['ratio'])} ביחס לתרחיש הסביר.")
            lines.append('')
    if p:
        lines += [f"{p['light']} **{p['headline']}.** {p['explain']}", '']
        rows = r['valuation']['annual_values'] if r['valuation'] else []
        if rows and p['bucket'] != 'blocked':
            lines += ['שווי מותנה בהנחות; התרחיש הגבוה אינו תקרת מחיר. בשיטת התזרים, בהיעדר חלוקות, המסלול השנתי משקף גם את שיעור ההיוון ואינו תחזית מסחר עצמאית לכל שנה.', '']
            if p['bucket'] == 'review_required':
                lines += ['**טיוטה לחישוב בלבד — טרם הושלמה בקרת השווי.**', '']
                lines += ['- ' + i['message'] + ' [' + i['path'] + ']' for i in r['valuation']['underwriting_audit']['issues']]
                lines.append('')
            has_tail = any(row.get('tail') is not None for row in rows)
            lines += [f'| תאריך | רע | סביר (בסיס) | טוב |' + (' הצלחה גדולה |' if has_tail else '') + f' מחיר היום ({cur}) |',
                      '|---|---|---|---|' + ('---|' if has_tail else '') + '---|']
            fmt = lambda v: '—' if v is None else f'{v:,.2f}'
            for row in rows:
                lines.append(f"| {row['date']} | {fmt(row['bear'])} | {fmt(row['base'])} | {fmt(row['bull'])} |"
                             + (f" {fmt(row.get('tail'))} |" if has_tail else '') + f" {fmt(row['current_quote'])} |")
            lines.append('')
        br = r.get('forecast_base_rate')
        if br and br['flags']:
            lines += [f"⚠️ {f['he']}" for f in br['flags']] + ['']
    else:
        lines += ['לא הורצה הערכת שווי. בלי זה אי אפשר להגיד אם המניה זולה או יקרה.', '']
    c = r['confidence']
    lines += [f"## כמה לסמוך על זה: {c['level']}", '']
    lines += [f'- {x}' for x in c['reasons']] or ['- הנתונים עברו את בדיקות התהליך.']
    lines += ['', '_זו אינדיקציה מחקרית לפי כללי אצבע שקופים ונתונים מדווחים — לא הוראת קנייה או מכירה ולא הבטחה לתשואה._']
    return '\n'.join(lines)
