"""Growth research signals and explicitly assumed scenario sensitivities.

Potential never exempts a business from valuation. Momentum cannot validate a
premium, and scenario weights are not calibrated investment-loss probabilities.
"""
from datetime import date
from .finance import number
from .history import quarters, txt

# No implicit scenario probabilities: absence means unavailable.
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
            add('acceleration', 1, 'קצב הצמיחה השנתי ברבעון האחרון גבוה מהראשון שנבדק', last - first)
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
            if not (t.get('expectation_available_at') and t.get('actual_available_at')) or not (
                t['expectation_available_at'] <= t['period_end'] <= t['actual_available_at'] <= case['as_of']):
                raise ValueError('expectations_track requires pre-result expectations and available actuals')
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
        definition = ('metric', 'period_end', 'basis', 'currency', 'unit')
        if any(not r.get(k) for r in revs for k in definition) or len({tuple(r[k] for k in definition) for r in revs}) != 1:
            raise ValueError('estimate_revisions must share metric, target period, basis, currency and unit')
        change = number(revs[-1]['value'], 'estimate', 1e-12) / number(revs[0]['value'], 'estimate', 1e-12) - 1
        add('revisions', 1 if change > .05 else (-1 if change < -.05 else 0),
            'האנליסטים מעלים את התחזיות לשנה הבאה' if change > .05 else ('האנליסטים מורידים תחזיות' if change < -.05 else 'התחזיות יציבות'), change)
    if len(qs) >= 4 and qs[-1]['revenue'] > 0 and qs[-1].get('gross_profit') is not None:
        gm = qs[-1]['gross_profit'] / qs[-1]['revenue']
        add('gross_margin', 1 if gm >= .6 else (-1 if gm < .3 else 0),
            'שיעור הרווח הגולמי גבוה; כוח תמחור דורש בדיקה נפרדת' if gm >= .6 else ('שיעור הרווח הגולמי נמוך לפי סף כללי; יש לבדוק ביחס לענף' if gm < .3 else 'רווחיות גולמית בטווח הביניים של הסף הכללי'), gm)
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
        lane, why = 'potential', 'חברה צעירה, צומחת מהר או בנקודת מפנה — בונים מסלולי ביקוש, קיבולת ורווחיות, כולל הצלחה גדולה וסיכוני ביצוע. אין הנחת מחיר אוטומטית בגלל צמיחה.'
    return {'lane': lane, 'why_he': why, 'evidence': {'operating_margin': om, 'fcf_margin': fcf, 'profitable_years': profitable_years}}


def payoff(annual_today, quote, probabilities=None):
    if probabilities is None:
        return None
    probs = dict(probabilities)
    names = [k for k in ('bear', 'base', 'bull', 'tail') if annual_today.get(k) is not None]
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
            'probabilities_are_default': False, 'calibrated': False, 'downside_to_bear': downside, 'upside_to_best': upside,
            'probability_below_price': loss_prob,
            'note': 'Analyst-weighted conditional present value; supplied probabilities remain uncalibrated. Weight below current price is not a future loss probability.'}


def multiples_view(result, quote, minority_share=0.0, exit_pe=DEFAULT_EXIT_PE, year_index=4):
    """Analyst-style check: scenario earnings per share in a future year x P/E, discounted back."""
    out = []
    if minority_share:
        return {'rows': [], 'exit_pe': list(exit_pe), 'note': 'A flat minority haircut to all parent earnings is not supported. Supply attributable earnings reconciliation.'}
    for s in result['scenarios']:
        rows = s.get('forecast') or []
        if len(rows) <= year_index or 'ebit' not in rows[year_index] or not s.get('timeline'):
            continue
        p = rows[year_index]
        duration = (date.fromisoformat(p['end']) - date.fromisoformat(p['start'])).days / 365.25
        if not .9 <= duration <= 1.01:
            continue
        eps = (p['ebit'] - p['interest'] - p['cash_taxes']) * (1 - minority_share) / p['shares']
        yrs = (date.fromisoformat(p['end']) - date.fromisoformat(result['as_of'])).days / 365.25
        ke = result.get('_cost_of_equity', {}).get(s['name'], .1)
        if eps <= 0:
            continue  # P/E is not meaningful for negative earnings.
        vals = {pe: eps * pe / (1 + ke) ** yrs for pe in exit_pe}
        out.append({'scenario': s['name'], 'year': p['end'][:4], 'eps': eps,
                    'price_in_year_at_pe': {pe: eps * pe for pe in exit_pe}, 'discounted_today_at_pe': vals})
    return {'rows': out, 'exit_pe': list(exit_pe),
            'note': 'Illustrative multiples sensitivity, not analyst-method attribution. EPS is an operating earnings proxy; reconcile investment income, minority interests, GAAP adjustments and weighted shares before comparing consensus.'}


def required_revenue(market_cap, terminal_net_cash, cost_of_equity, margin,
                     tax=.2, exit_nopat_multiple=25, years=5):
    """No-dividend equity-return hurdle translated with EV/NOPAT, never P/E.

    terminal_net_cash is a FUTURE net asset value, not today's cash compounded.
    Fixed share count/claims only. This sensitivity is not an executable expansion plan.
    """
    number(market_cap, 'market_cap', 0)
    number(terminal_net_cash, 'terminal_net_cash')
    number(cost_of_equity, 'cost_of_equity', 0, 1)
    number(margin, 'margin', 1e-12, 1); number(tax, 'tax', 0, .999999)
    number(exit_nopat_multiple, 'exit_nopat_multiple', 1e-12)
    number(years, 'years', 1e-12)
    enterprise_needed = max(0, market_cap * (1 + cost_of_equity) ** years - terminal_net_cash)
    return enterprise_needed / (margin * (1 - tax) * exit_nopat_multiple)


def lane_verdict(lane, mom, pay, today, quote):
    """Descriptive scenario location; momentum never grants a valuation premium."""
    highest = max(today[k] for k in ('bear', 'base', 'bull', 'tail') if today.get(k) is not None)
    if quote > highest:
        return {'light': '🟠', 'call': 'המחיר מעל התרחישים שהוזנו',
                'explain': 'יש לבדוק אם חסר מסלול עסקי נתמך או שהמחיר דורש הנחות חזקות יותר. התרחיש הגבוה אינו תקרת מחיר.'}
    return {'light': '🟡', 'call': 'המחיר בתוך טווח התרחישים שהוזנו',
            'explain': 'אין מסקנת כדאיות מתוך מומנטום או משקלי תרחישים בלבד. נדרשת בקרת המחקר והערכת השווי.'}


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
    # No hardcoded five-year reverse case, terminal margin or 25x P/E.
    # Use valuation_diagnostics for an explicitly labeled driver sensitivity.
    req = None
    return {'lane': lane, 'momentum': mom, 'payoff': pay, 'multiples': mult, 'required': req,
            'verdict': lane_verdict(lane['lane'], mom, pay, today, quote)}
