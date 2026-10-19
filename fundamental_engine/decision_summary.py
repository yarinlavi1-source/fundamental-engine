"""Compact Hebrew delivery over executed analysis; never estimates missing values.

Qualitative notes are analyst inferences with dated observation references, not
machine-verified business truth. They cannot unlock a price conclusion.
"""
from datetime import date
from math import isfinite


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0


def _notes(case):
    notes = case.get('decision_notes', {})
    if not isinstance(notes, dict) or set(notes) - {'potential', 'risk', 'milestone'}:
        raise ValueError('decision_notes accepts potential, risk, milestone')
    dossier = case.get('research_dossier') or {}
    if notes:
        identity = dossier.get('identity', {})
        if identity.get('ticker') != case['ticker'] or dossier.get('as_of') != case['as_of']:
            raise ValueError('decision_notes require a matching dated research_dossier')
    observations = {o['id']: o for o in dossier.get('observations', [])}
    sources = {s['id']: s for s in dossier.get('sources', [])}
    cutoff = date.fromisoformat(case['as_of'])
    result = {}
    for key, note in notes.items():
        text = note.get('text', '')
        refs = note.get('observation_ids', [])
        if (not isinstance(text, str) or not text.strip() or len(text.split()) > 25
                or '\n' in text or not isinstance(refs, list) or not refs):
            raise ValueError('Each decision note needs one short sentence and observation_ids')
        for ref in refs:
            o = observations.get(ref, {})
            s = sources.get(o.get('source_id'), {})
            if not o.get('reviewed') or not o.get('review_note') or not s.get('available_at'):
                raise ValueError('decision_notes require reviewed observations and dated sources')
            for stamp in (s['available_at'], o.get('reviewed_at'), o.get('observed_at')):
                if not stamp or date.fromisoformat(stamp[:10]) > cutoff:
                    raise ValueError('decision_notes evidence must be available by as_of')
        result[key] = {'text': text.strip().rstrip('.'), 'observation_ids': refs,
                       'kind': 'analyst_inference'}
    return result


def decision_summary(report, case):
    notes = _notes(case)
    p = report.get('price') or {}
    v = report.get('valuation') or {}
    demo = bool(case.get('is_demo'))
    eligible = (not demo and v.get('priced_conclusion_ready') is True
                and p.get('bucket') not in {None, 'blocked', 'review_required'})
    quote, base = p.get('quote'), p.get('base')
    eligible = eligible and _positive(quote) and _positive(base)
    metrics = {'discount_to_base_value': None, 'upside_to_base_value': None,
               'five_year': None}
    parts = [f"{report['ticker']} —"]
    if demo:
        call = 'הדגמה בלבד, אין כאן מסקנת השקעה בחברה אמיתית'
    elif p.get('bucket') == 'blocked':
        call = 'לא הייתי נכנס כרגע בלי פתרון למימון שהחברה צריכה'
    elif not eligible:
        call = 'עדיין אין בסיס מספיק לקבוע אם המחיר כדאי להשקעה'
    else:
        call = {
            'cheap': 'המחיר נראה מעניין להשקעה לפי התרחיש המרכזי',
            'cheap_even_bear': 'המחיר נראה מעניין גם ביחס לתרחיש השמרני שנבדק',
            'fair': 'המחיר קרוב לשווי שהוערך, בלי הנחה ברורה',
            'expensive': 'במחיר הזה הייתי מחכה; נדרשת הצלחה מעבר לתרחיש המרכזי',
            'very_expensive': 'במחיר הזה הייתי מחכה; אפילו התרחיש האופטימי שנבדק אינו מצדיק אותו',
        }[p['bucket']]
    parts.append(call + '.')
    if 'potential' in notes:
        parts.append('להערכתי, הפוטנציאל הוא ' + notes['potential']['text'] + '.')
    elif report['stage'] in {'growth', 'emerging'}:
        parts.append('הפוטנציאל העתידי עדיין דורש ביסוס; זה לא אומר שהחברה חסרת פוטנציאל.')
    if eligible:
        discount = 1 - quote / base
        metrics['discount_to_base_value'] = discount
        metrics['upside_to_base_value'] = base / quote - 1
        values = [p.get(k) for k in ('bear', 'base', 'bull')]
        finite = [x for x in values if isinstance(x, (int, float)) and isfinite(x) and x >= 0]
        spread = f" (טווח התרחישים {min(finite):,.1f}–{max(finite):,.1f})" if len(finite) == 3 else ''
        gap = (f'הנחה של כ־{discount:.0%} מול השווי המרכזי' if discount > 0
               else f'מחיר גבוה בכ־{-discount:.0%} מהשווי המרכזי' if discount < 0 else 'ללא הנחה')
        parts.append(f"מול מחיר {quote:,.1f}, השווי המוערך היום הוא כ־{base:,.1f} {report['currency']}{spread}: {gap}.")
        start = date.fromisoformat(report['as_of'])
        try:
            target = start.replace(year=start.year + 5)
        except ValueError:
            target = start.replace(year=start.year + 5, day=28)
        # Never rename a four-year endpoint as five years, extrapolate, or use
        # an early row. A year-end up to 100 days after the anniversary is dated.
        candidates = []
        for row in v.get('annual_values', [])[1:]:
            d = date.fromisoformat(row['date'])
            if 0 <= (d - target).days <= 100 and _positive(row.get('base')):
                candidates.append((d, row))
        if candidates:
            d, row = min(candidates, key=lambda pair: pair[0])
            years = (d - start).days / 365.25
            upside = row['base'] / quote - 1
            metrics['five_year'] = {'date': d.isoformat(), 'years': years,
                'conditional_base_value': row['base'], 'price_change_excluding_dividends': upside,
                'annualized_price_change_excluding_dividends': (row['base'] / quote) ** (1 / years) - 1}
            direction = 'עלייה' if upside >= 0 else 'ירידה'
            parts.append(f"אם התרחיש המרכזי יתממש, השווי ב־{d.isoformat()} עשוי להיות כ־{row['base']:,.1f}, {direction} של כ־{abs(upside):.0%} מול המחיר כעת, ללא דיבידנדים; זו אינה הבטחת מחיר.")
        else:
            parts.append('עדיין אין הערכת שווי מבוססת לחמש שנים מלאות מהיום.')
    else:
        parts.append('אין עדיין הערכת שווי מאושרת לשימוש במסקנה; אי אפשר לקבוע את ההנחה או השווי בעוד חמש שנים באמינות.')
    if eligible and report.get('business_score') is not None and report['business_score'] < 2.5:
        parts.append('הנתונים הנוכחיים חלשים; ההזדמנות תלויה בהוכחת השיפור בעסק.')
    if any(i['key'] in {'accounting_risk', 'distress'} and i['points'] == 1 for i in report['items']):
        parts.append('לפני השקעה צריך לברר דגל אדום בדוחות או בחוסן הכספי.')
    if 'risk' in notes:
        parts.append('הסיכון המרכזי: ' + notes['risk']['text'] + '.')
    if 'milestone' in notes:
        parts.append('מה שיכריע: ' + notes['milestone']['text'] + '.')
    reasons = report['confidence'].get('reasons') or []
    # One short reason keeps the paragraph honest without turning it into a report.
    parts.append('רמת הביטחון בהערכה: ' + report['confidence']['level'] + (f' — {reasons[0]}.' if reasons else '.'))
    return {'text': ' '.join(parts), 'price_conclusion_eligible': bool(eligible),
            'metrics': metrics, 'notes': notes,
            'semantics': 'Conditional estimates, not observed true value or promised prices; scenario range is not a confidence interval.'}
