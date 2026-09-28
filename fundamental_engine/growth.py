"""Which valuation lane fits, and when does a growth stock deserve a premium?

Two lanes (Damodaran life cycle: numbers dominate mature firms, narrative plus
evidence dominates young ones):
- intrinsic: profitable, cash-generating firms with a reliable record (e.g. Nvidia
  after 2023). A DCF of the base case is the anchor; price above the bull value is
  expensive.
- potential: young/high-growth/inflecting firms where a base-case DCF almost always
  says "expensive" because value sits in a right tail. Here the anchor becomes the
  probability-weighted value INCLUDING an executed tail scenario, and the premium
  above the base case is allowed only when the business is outrunning expectations.

Evidence of outrunning expectations (Mauboussin & Rappaport, Expectations Investing;
estimate-revision research): accelerating growth, expanding margins, beat-and-raise
quarters and upward estimate revisions, plus quality of growth (Rule of 40 / gross
margin). Probabilities are explicit analyst assumptions; defaults are labelled
uncalibrated. Nothing here predicts which stock becomes a multi-bagger, places
orders or promises returns.
"""
from datetime import date
from .finance import number
from .history import quarters, txt

DEFAULT_PROBABILITIES = {'bear': .25, 'base': .45, 'bull': .22, 'tail': .08}
DEFAULT_EXIT_PE = (15, 25, 40)


def _yoy_series(qs):
    return [(qs[i]['period_end'], qs[i]['revenue'] / qs[i - 4]['revenue'] - 1)
            for i in range(4, len(qs)) if qs[i - 4]['revenue'] > 0]


def momentum(case, rows=None):
    """Signals that the business is running ahead of (or behind) expectations."""
    qs = quarters(case)
    signals = []
    def add(key, score, he, value=None):
        signals.append({'key': key, 'score': score, 'he': he, 'value': value})
    yoy = _yoy_series(qs)
    if len(yoy) >= 2:
        first, last = yoy[0][1], yoy[-1][1]
        if last > first + .05:
            add('acceleration', 1, 'הצמיחה מאיצה — כל רבעון צומח מהר יותר מהקודם', last - first)
        elif last < first - .05:
            add('acceleration', -1, 'הצמיחה מאטה ברבעונים האחרונים', last - first)
        else:
            add('acceleration', 0, 'קצב הצמיחה יציב', last - first)
    if len(qs) >= 5 and qs[-1].get('operating_income') is not None and qs[-5].get('operating_income') is not None \
            and qs[-1]['revenue'] > 0 and qs[-5]['revenue'] > 0:
        d = qs[-1]['operating_income'] / qs[-1]['revenue'] - qs[-5]['operating_income'] / qs[-5]['revenue']
        add('margin_expansion', 1 if d > .03 else (-1 if d < -.03 else 0),
            'הרווחיות משתפרת ככל שהיא גדלה' if d > .03 else ('הרווחיות נשחקת' if d < -.03 else 'הרווחיות יציבה'), d)
    track = case.get('expectations_track') or []
    if track:
        beats = raises = 0
        for t in track:
            date.fromisoformat(t['period_end'])
            if not t.get('source_ids'):
                raise ValueError('expectations_track rows need source_ids')
            expected = t.get('guidance_midpoint', t.get('consensus_revenue'))
            if expected is not None and number(t['revenue'], 'revenue', 0) > number(expected, 'expected revenue', 0) * 1.01:
                beats += 1
            if t.get('next_guidance_midpoint') is not None and t.get('prior_next_expectation') is not None and \
                    t['next_guidance_midpoint'] > t['prior_next_expectation'] * 1.01:
                raises += 1
        share = beats / len(track)
        add('beats', 1 if share >= .75 else (-1 if share < .5 else 0),
            f'עקפה את הציפיות ב-{beats} מתוך {len(track)} רבעונים' + (f', והעלתה תחזית {raises} פעמים' if raises else ''), share)
    revisions = case.get('estimate_revisions') or []
    if len(revisions) >= 2:
        revs = sorted(revisions, key=lambda r: r['as_of'])
        for r in revs:
            if date.fromisoformat(r['as_of']) > date.fromisoformat(case['as_of']) or not r.get('source_ids'):
                raise ValueError('estimate_revisions need as_of <= case as_of and source_ids')
        change = number(revs[-1]['value'], 'estimate', 1e-12) / number(revs[0]['value'], 'estimate', 1e-12) - 1
        add('revisions', 1 if change > .05 else (-1 if change < -.05 else 0),
            'האנליסטים מעלים את התחזיות לשנה הבאה' if change > .05 else ('האנליסטים מורידים תחזיות' if change < -.05 else 'התחזיות יציבות'), change)
    if len(qs) >= 4 and qs[-1]['revenue'] > 0 and qs[-1].get('gross_profit') is not None:
        gm = qs[-1]['gross_profit'] / qs[-1]['revenue']
        add('gross_margin', 1 if gm >= .6 else (-1 if gm < .3 else 0),
            'המוצר רווחי מאוד (יש כוח תמחור)' if gm >= .6 else ('המוצר עם רווחיות נמוכה' if gm < .3 else 'רווחיות מוצר סבירה'), gm)
    score = sum(s['score'] for s in signals)
    label = 'strong' if score >= 2 else ('weak' if score <= -1 else 'neutral')
    return {'score': score, 'label': label, 'signals': signals,
            'latest_yoy': yoy[-1][1] if yoy else None,
            'note': 'Signals from reported quarters plus optional dated guidance/consensus inputs; they measure whether results outrun expectations, not future returns.'}


def choose_lane(kind, rows):
    """Numbers-dominant (intrinsic) versus evidence-plus-narrative (potential)."""
    last = rows[-1]
    rev = last['revenue']
    om = last['operating_income'] / rev if last.get('operating_income') is not None and rev > 0 else None
    fcf = (last['operating_cash_flow'] - last['capex']) / rev if last.get('operating_cash_flow') is not None \
        and last.get('capex') is not None and rev > 0 else None
    profitable_years = sum(1 for r in rows if r.get('operating_income') is not None and r['operating_income'] > 0)
    reliable = om is not None and om >= .1 and fcf is not None and fcf > 0 and profitable_years >= min(3, len(rows))
    inflecting = any(f['key'] == 'inflection' for f in kind['flags'])
    if not inflecting and (kind['stage'] in {'mature_growth', 'mature_stable', 'decline'} or (reliable and kind['stage'] == 'high_growth')):
        lane, why = 'intrinsic', 'רווחית, מייצרת מזומן ועם היסטוריה אמינה — מעריכים לפי המספרים (כמו אנבידיה).'
    else:
        lane, why = 'potential', 'חברה צעירה, צומחת מהר או בנקודת מפנה — הערכת שווי רגילה כמעט תמיד תגיד "יקר", כי הערך נמצא בסיכוי להצלחה גדולה. בודקים אם העסק מצדיק "הנחה".'
    return {'lane': lane, 'why_he': why, 'evidence': {'operating_margin': om, 'fcf_margin': fcf, 'profitable_years': profitable_years}}


def payoff(annual_today, quote, probabilities=None):
    probs = dict(probabilities or DEFAULT_PROBABILITIES)
    calibrated = probabilities is not None
    names = [k for k in ('bear', 'base', 'bull', 'tail') if annual_today.get(k) is not None]
    if 'tail' not in names and not calibrated:
        probs = {'bear': .27, 'base': .5, 'bull': .23}
    if set(probs) != set(names):
        raise ValueError('scenario_probabilities must cover exactly the executed scenarios: ' + ', '.join(names))
    for k, v in probs.items():
        number(v, 'probability ' + k, 0, 1)
    if abs(sum(probs.values()) - 1) > 1e-6:
        raise ValueError('scenario_probabilities must sum to 1')
    ev = sum(probs[k] * annual_today[k] for k in names)
    downside = annual_today['bear'] / quote - 1
    upside = max(annual_today[k] for k in names) / quote - 1
    loss_prob = sum(probs[k] for k in names if annual_today[k] < quote)
    return {'expected_value': ev, 'expected_vs_price': ev / quote - 1, 'probabilities': probs,
            'probabilities_are_default': not calibrated, 'downside_to_bear': downside, 'upside_to_best': upside,
            'probability_below_price': loss_prob,
            'note': 'Probability-weighted present value of executed scenarios. Default probabilities are uncalibrated placeholders; tail probability should reflect base rates (few firms sustain hypergrowth).'}


def multiples_view(result, quote, minority_share=0.0, exit_pe=DEFAULT_EXIT_PE, year_index=4):
    """Analyst-style check: scenario earnings per share in a future year x P/E, discounted back."""
    out = []
    for s in result['scenarios']:
        rows = s.get('forecast') or []
        if len(rows) <= year_index or 'ebit' not in rows[year_index] or not s.get('timeline'):
            continue
        p = rows[year_index]
        eps = (p['ebit'] - p['interest'] - p['cash_taxes']) * (1 - minority_share) / p['shares']
        yrs = (date.fromisoformat(p['end']) - date.fromisoformat(result['as_of'])).days / 365.25
        ke = result.get('_cost_of_equity', {}).get(s['name'], .1)
        vals = {pe: eps * pe / (1 + ke) ** yrs for pe in exit_pe}
        out.append({'scenario': s['name'], 'year': p['end'][:4], 'eps': eps,
                    'price_in_year_at_pe': {pe: eps * pe for pe in exit_pe}, 'discounted_today_at_pe': vals})
    return {'rows': out, 'exit_pe': list(exit_pe),
            'note': 'What the market might pay if it keeps a given multiple; this is how most price targets are built. Multiples are assumptions about future sentiment, not value.'}


def required_revenue(market_cap, net_cash, cost_of_equity, margin, tax=.2, exit_pe=25, years=5):
    """Reverse check: revenue needed in N years for today's price to earn the required return."""
    ev_needed = (market_cap - max(net_cash, 0)) * (1 + cost_of_equity) ** years
    return ev_needed / (margin * (1 - tax) * exit_pe)


def lane_verdict(lane, mom, pay, today, quote):
    base, bull = today['base'], today['bull']
    tail = today.get('tail')
    if lane == 'intrinsic':
        if quote <= base * 1.1:
            return {'light': '🟢' if quote <= base * .8 else '🟡', 'call': 'מתומחרת בסביר לפי המספרים' if quote > base * .8 else 'זולה לפי המספרים',
                    'explain': 'חברה שהמספרים שלה אמינים, והמחיר לא גבוה מהשווי בתרחיש הסביר.'}
        if quote <= bull:
            return {'light': '🟠', 'call': 'יקרה — המחיר מניח את התרחיש הטוב',
                    'explain': 'כדי שהמחיר יהיה מוצדק, צריך שהתרחיש הטוב יתממש. אין פה מרווח ביטחון.'}
        return {'light': '🔴', 'call': 'יקרה מאוד — מעל גם התרחיש הטוב', 'explain': 'גם התרחיש הטוב לא מצדיק את המחיר.'}
    ev = pay['expected_value']
    m = mom['label']
    if m == 'weak':
        return {'light': '🔴', 'call': 'יקרה בלי ראיות', 'explain': 'המחיר גבוה, והעסק לא רץ מהר מהציפיות — לא מגיעה לו "הנחה". זה האזור שבו מניות צמיחה קורסות.'}
    if quote <= base * 1.1:
        return {'light': '🟢', 'call': 'מניית צמיחה במחיר סביר', 'explain': 'אפילו בלי לשלם על ההצלחה הגדולה, המחיר מכוסה בתרחיש הסביר.'}
    if quote <= ev:
        if m == 'strong':
            return {'light': '🟢', 'call': 'יקרה על הנייר — אבל הימור צמיחה מוצדק',
                    'explain': 'הערכת שווי רגילה אומרת יקר, אבל העסק רץ מהר מהציפיות, וכשמחשבים גם את הסיכוי להצלחה הגדולה — השווי הממוצע מעל המחיר. מגיעה לה "הנחה". גודל פוזיציה צריך להתאים להימור: אפשר גם להפסיד.'}
        return {'light': '🟡', 'call': 'הימור צמיחה אפשרי — צריך עוד הוכחות',
                'explain': 'השווי הממוצע (כולל ההצלחה הגדולה) מעל המחיר, אבל הראיות שהעסק עוקף ציפיות עוד לא חזקות.'}
    if tail is not None and quote <= tail:
        return {'light': '🟠' if m == 'strong' else '🔴', 'call': 'המחיר כבר מתמחר את ההצלחה הגדולה',
                'explain': 'המחיר מעל השווי הממוצע. הוא מוצדק רק אם התרחיש הגדול באמת יקרה — אתה משלם היום על הזנב, בלי מרווח.'
                           + (' העסק כן רץ מהר — שווה לעקוב ולחכות למחיר טוב יותר.' if m == 'strong' else '')}
    return {'light': '🔴', 'call': 'יקרה גם כהימור צמיחה', 'explain': 'המחיר גבוה אפילו מהתרחיש הגדול ביותר שחישבנו.'}


def growth_lane(case, kind, rows, result, minority_share=0.0):
    """Assemble lane, momentum, payoff, multiples and reverse check for plain_verdict."""
    lane = choose_lane(kind, rows)
    mom = momentum(case, rows)
    if result is None or result['annual_values'][0].get('base') is None:
        return {'lane': lane, 'momentum': mom, 'payoff': None, 'multiples': None, 'required': None, 'verdict': None}
    today = result['annual_values'][0]
    quote = today['current_quote']
    probs = case.get('scenario_probabilities')
    if probs is not None:
        txt(case.get('probability_rationale'), 'probability_rationale')
    pay = payoff(today, quote, probs)
    kes = {s['name']: s['cost_of_equity'] for s in case['valuation_case']['scenarios']}
    result = dict(result, _cost_of_equity=kes)
    mult = multiples_view(result, quote, minority_share)
    req = None
    if case.get('market_cap') and case.get('balance'):
        base_s = next(s for s in case['valuation_case']['scenarios'] if s['name'] == 'base')
        margin = base_s.get('terminal', {}).get('operating_margin', .2)
        need = required_revenue(case['market_cap'], case['balance']['cash'] - case['balance']['debt'], kes['base'], margin)
        forecast = next(s for s in result['scenarios'] if s['name'] == 'base')['forecast']
        base5 = forecast[5]['revenue'] if len(forecast) > 5 else forecast[-1]['revenue']
        req = {'required_revenue_5y': need, 'base_revenue_5y': base5, 'ratio': need / base5 if base5 else None,
               'assumed_margin': margin, 'exit_pe': 25,
               'note': 'Revenue needed in ~5 years for today\'s price to earn the base cost of equity at the base terminal margin and a 25x exit P/E.'}
    return {'lane': lane, 'momentum': mom, 'payoff': pay, 'multiples': mult, 'required': req,
            'verdict': lane_verdict(lane['lane'], mom, pay, today, quote)}
