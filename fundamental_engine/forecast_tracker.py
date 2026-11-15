"""Forecast tracker: scores every published lean base case against what was reported after it. Pure logic (no network,
no file access); scripts/forecast_tracker.py reads the params, runs scripts/lean_model.py and fetches the data.

It only measures. It never changes a model or a rule: rules change only through a preregistered test (CLAUDE.md).

What is compared, per company and valuation date (the base scenario of lean_params.json):
- a fiscal year completed and reported after the valuation: revenue, operating margin and operating EPS against the
  model's year (the base year has a revenue estimate only: the model's margins and EPS start one year later);
- the fiscal year in progress: year-to-date revenue growth against the same period a year earlier versus the model's
  FULL-YEAR growth, and the year-to-date operating margin versus the model's margin for that year. A partial year is
  compared with a full-year path: seasonality and a fading growth rate make this a rough reading, labelled as such;
- price: the latest close against the model's base value path interpolated to that day.
Actuals are SEC companyfacts. A period counts as reported after the valuation when its results release (8-K item 2.02,
when the script supplies the dates) or else its first XBRL filing is dated after the valuation's as_of. Without release
dates, a filing that follows the valuation within RELEASE_LAG_DAYS, for a period that had already ended, may already
have been in it: the row says so and stays out of the summary. The actual margin is GAAP operating income / revenue; the model's margin is after SBC and may leave out
items, so a small margin gap can be a definition gap. Actual operating EPS = operating income x (1 - the model's tax
rate) / diluted shares, the model's own definition.
"""
import math
from datetime import date, timedelta

TRACKER_VERSION = 1
MIN_ROWS = 8                 # below this many scored rows a median is reported with 'too few to conclude'
MIN_PRICE_AGE_DAYS = 90      # a price gap of a younger valuation is a level, not yet a test of the path
REV_TOL = 0.02               # on track: revenue within 2% (completed year) or growth within 2 points (year in progress)
MARGIN_TOL = 0.01            # on track: operating margin within 1 point
DATE_TOL = 12                # days: 52/53-week fiscal calendars and rounded days_to_base_year_end
PRICE_SCALE_LIMIT = 1.8      # latest close vs the valuation's price beyond this factor: split or another unit, not scored
MAX_PERIOD_AGE_DAYS = 200    # a period that ended longer before the valuation was reported before it
RELEASE_LAG_DAYS = 45        # a first filing this soon after the valuation may follow a release the valuation used
                             # (an annual report can follow its results release by several weeks)
TOO_FEW = 'too few to conclude'
REQUIRED = ('as_of', 'price', 'base_year', 'rev0_m', 'days_to_base_year_end', 'tax', 'scenarios')
TAGS = {
    'us-gaap': {'revenue': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax',
                            'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueNet', 'RevenuesNetOfInterestExpense'],
                'operating_income': ['OperatingIncomeLoss'],
                'diluted_shares': ['WeightedAverageNumberOfDilutedSharesOutstanding']},
    'ifrs-full': {'revenue': ['Revenue', 'RevenueFromContractsWithCustomers'],
                  'operating_income': ['ProfitLossFromOperatingActivities'],
                  'diluted_shares': ['AdjustedWeightedAverageShares']},
}
SPANS = (('Q1', 80, 100), ('H1', 170, 192), ('9M', 260, 285), ('FY', 350, 380))
READ_HE = {'on_track': 'בקצב', 'ahead': 'מקדים את המסלול', 'behind': 'מפגר אחרי המסלול', 'mixed': 'מעורב',
           'no_report': 'אין דוח מאז', 'custom': 'ניקוד ידני', 'not_scorable': 'לא נוקד'}
SPAN_HE = {'Q1': 'רבעון ראשון', 'H1': 'חצי שנה', '9M': 'תשעה חודשים', 'FY': 'שנה מלאה'}


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _shift(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:                       # 29 February
        return d.replace(year=d.year + years, day=28)


def _span(start, end):
    n = (_d(end) - _d(start)).days
    return next((name for name, lo, hi in SPANS if lo <= n <= hi), None)


# ------------------------------------------------------------------ the model's path

def lean_format(params):
    """(True, None) when the params are a lean_model file this tracker can score, else (False, reason)."""
    if not isinstance(params, dict):
        return False, 'not a params object'
    missing = [k for k in REQUIRED if k not in params]
    base = (params.get('scenarios') or {}).get('base') if isinstance(params.get('scenarios'), dict) else None
    if missing or not isinstance(base, dict) or not all(isinstance(base.get(k), list) for k in ('g', 'm')):
        return False, 'not the lean_model format (' + (', '.join(missing) or 'no base growth and margin path') + ')'
    if max(base['m']) >= 0.95:
        return False, "the model's revenue line is not sales (operating margin of 95% or more): a remapped model"
    return True, None


def model_path(params, result):
    """The base case as a path: revenue, growth, margin and operating EPS by fiscal year, the fiscal year ends, and the
    dated value points. result = lean_model.run(params)."""
    b = result['scenarios']['base']
    as_of, y0 = _d(params['as_of']), int(params['base_year'])
    base_end = as_of + timedelta(days=params['days_to_base_year_end'])
    ke0 = b['ke0_pct'] / 100
    points = [(as_of, b['value_today'])]
    if base_end > as_of:
        points.append((base_end, b['value_today'] * (1 + ke0) ** (params['days_to_base_year_end'] / 365)))
    points += [(_shift(base_end, int(y) - y0), v) for y, v in sorted((int(k), v) for k, v in b['year_end_values'].items())]
    return {'as_of': as_of, 'base_year': y0, 'base_end': base_end, 'tax': params['tax'], 'price': params['price'],
            'revenue': {int(k): v * 1e6 for k, v in b['revenue_m'].items()},
            'growth': {y0 + 1 + i: g for i, g in enumerate(params['scenarios']['base']['g'])},
            'margin': {int(k): v for k, v in b['op_margin'].items()},
            'eps_operating': {int(k): v for k, v in b['eps_operating'].items()},
            'value_points': points}


def value_at(points, when):
    """The base value path at a date: log-linear between the dated points; None outside them."""
    when = _d(when)
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if d0 <= when <= d1 and v0 > 0 and v1 > 0:
            w = (when - d0).days / max((d1 - d0).days, 1)
            return math.exp(math.log(v0) + w * (math.log(v1) - math.log(v0)))
    return None


def fiscal_year(end, path):
    """(label, year end, year start) of the model fiscal year a period end falls in."""
    k = math.ceil(((_d(end) - path['base_end']).days - DATE_TOL) / 365.25)
    fy_end = _shift(path['base_end'], k)
    return path['base_year'] + k, fy_end, _shift(fy_end, -1) + timedelta(days=1)


# ------------------------------------------------------------------ companyfacts -> periods

def periods(payload):
    """{'currency', 'annual_only', 'revenue'|'operating_income'|'diluted_shares': {(start, end): {val, first_filed, form}}}.
    Per period the latest filed value stands (restatements) and first_filed is when the period first appeared. Revenue
    takes the larger value across its tags (a total is never below a part)."""
    facts = payload.get('facts') or {}

    def last_end(tax):
        return max((f.get('end', '') for tag in TAGS[tax]['revenue'] for fs in ((facts.get(tax) or {}).get(tag) or {}).get('units', {}).values()
                    for f in fs if f.get('start')), default='')
    tax = max(TAGS, key=lambda t: (last_end(t), t == 'us-gaap'))
    out = {'currency': None, 'annual_only': False, 'revenue': {}, 'operating_income': {}, 'diluted_shares': {}}
    if not last_end(tax):
        return out
    node = facts.get(tax) or {}
    by_unit = {}
    for tag in TAGS[tax]['revenue']:
        for unit, fs in (node.get(tag) or {}).get('units', {}).items():
            by_unit[unit] = max([by_unit.get(unit, '')] + [f['end'] for f in fs if f.get('start')])
    out['currency'] = max(by_unit, key=lambda u: (by_unit[u], u != 'USD'))      # a USD line beside another currency is a translation
    forms = set()
    for metric, tags in TAGS[tax].items():
        unit = 'shares' if metric == 'diluted_shares' else out['currency']
        for tag in tags:
            per = {}
            for f in (node.get(tag) or {}).get('units', {}).get(unit) or []:
                if not f.get('start') or f.get('val') is None or not f.get('filed'):
                    continue
                key = (f['start'], f['end'])
                cur = per.setdefault(key, {'val': float(f['val']), 'filed': f['filed'], 'first_filed': f['filed'], 'form': f.get('form')})
                if f['filed'] >= cur['filed']:
                    cur.update(val=float(f['val']), filed=f['filed'])
                if f['filed'] < cur['first_filed']:
                    cur.update(first_filed=f['filed'], form=f.get('form'))
                forms.add(f.get('form'))
            for key, row in per.items():
                have = out[metric].get(key)
                if have is None:
                    out[metric][key] = row
                else:
                    first = min(have['first_filed'], row['first_filed'])
                    if metric == 'revenue' and row['val'] > have['val']:
                        have.update(val=row['val'])
                    have['first_filed'] = first
    out['annual_only'] = bool(forms) and not any(str(f).startswith('10-Q') for f in forms)
    return out


def _find(rows, start, end):
    """The period that starts and ends within DATE_TOL days of the two dates."""
    best = None
    for (s, e), r in rows.items():
        gap = abs((_d(s) - start).days) + abs((_d(e) - end).days)
        if abs((_d(s) - start).days) <= DATE_TOL and abs((_d(e) - end).days) <= DATE_TOL and (best is None or gap < best[0]):
            best = (gap, r)
    return best[1] if best else None


def _reported_on(end, first_filed, release_dates):
    """(date the results became public, basis): the first results release dated after the period end and not after the
    first XBRL filing, else the filing itself."""
    c = sorted(d for d in release_dates or [] if end < d <= first_filed)
    return (c[0], 'release') if c else (first_filed, 'filing')


# ------------------------------------------------------------------ scoring one valuation

def _err(actual, forecast):
    return actual / forecast - 1 if actual is not None and forecast else None


def _read(items):
    """on_track / ahead / behind / mixed from the business items (price is not part of the read)."""
    signs = []
    for kind, x in items:
        if x is None:
            continue
        tol = MARGIN_TOL if kind == 'margin' else REV_TOL
        signs.append(1 if x > tol else -1 if x < -tol else 0)
    if not signs:
        return None
    if all(s == 0 for s in signs):
        return 'on_track'
    if any(s > 0 for s in signs) and any(s < 0 for s in signs):
        return 'mixed'
    return 'ahead' if any(s > 0 for s in signs) else 'behind'


def score(ticker, params, result, payload, today, price=None, release_dates=None, params_path=None):
    """One row: the valuation's base case against what was reported after its as_of.
    price: {'value', 'date'} (the latest close) or None. release_dates: ISO dates of results releases or None."""
    row = {'ticker': ticker, 'valuation_date': str(params.get('as_of'))[:10] if isinstance(params, dict) else None,
           'params_path': params_path, 'status': None, 'reported': [], 'completed': None, 'in_progress': None, 'price': None,
           'read': None, 'notes': []}
    ok, why = lean_format(params)
    if not ok:
        row.update(status='custom_model', read='custom', reason=f'custom model: scored by hand ({why})')
        return row
    path = model_path(params, result)
    as_of = path['as_of'].isoformat()
    if price and price.get('value') and price.get('date', '') >= as_of:
        vp = value_at(path['value_points'], price['date'])
        scale = price['value'] / path['price']
        if not 1 / PRICE_SCALE_LIMIT <= scale <= PRICE_SCALE_LIMIT:
            row['notes'].append(f"price {price['value']:,.2f} is {scale:.2f}x the valuation's price: a split or another unit, price not scored")
        elif vp:
            row['price'] = {'price': price['value'], 'date': price['date'], 'value_path': round(vp, 2),
                            'vs_value_path': price['value'] / vp - 1, 'price_at_valuation': path['price'],
                            'gap_at_valuation': path['price'] / path['value_points'][0][1] - 1,
                            'age_days': (_d(price['date']) - path['as_of']).days}
    if payload is None:
        row.update(status='not_scorable', read='not_scorable', reason='no SEC companyfacts for this company')
        return row
    per = periods(payload)
    if per['currency'] != 'USD':
        row.update(status='not_scorable', read='not_scorable',
                   reason=f"SEC facts are in {per['currency'] or 'no currency'}, the model in USD: scored by hand")
        return row
    rev = per['revenue']
    row['annual_filer'] = per['annual_only']
    new, off_calendar = [], False
    for (s, e), r in rev.items():
        if r['first_filed'] <= as_of or r['first_filed'] > today or e > today or (path['as_of'] - _d(e)).days > MAX_PERIOD_AGE_DAYS:
            continue                                             # an old period refiled under a new tag is not a new report
        label, fy_end, fy_start = fiscal_year(e, path)
        span = _span(s, e)
        if span is None or abs((_d(s) - fy_start).days) > DATE_TOL:
            off_calendar = off_calendar or span in ('H1', '9M', 'FY')
            continue                                             # a single later quarter: the year-to-date period is used
        on, basis = _reported_on(e, r['first_filed'], release_dates)
        if on <= as_of:
            continue                                             # released before the valuation, filed after it
        new.append({'fiscal_year': label, 'period': span, 'start': s, 'end': e, 'reported_on': on, 'reported_basis': basis,
                    'first_filed': r['first_filed'], 'fy_end': fy_end, 'fy_start': fy_start,
                    'may_be_in_valuation': basis == 'filing' and (_d(on) - path['as_of']).days <= RELEASE_LAG_DAYS
                    and (path['as_of'] - _d(e)).days >= 14})
    new.sort(key=lambda x: x['end'])
    row['reported'] = [{k: x[k] for k in ('fiscal_year', 'period', 'end', 'reported_on', 'reported_basis', 'may_be_in_valuation')}
                       for x in new]
    if off_calendar:
        row['notes'].append("a period filed after the valuation does not start at the model's fiscal year start: check "
                            'base_year and days_to_base_year_end against the fiscal calendar (that period is not scored)')
    if not new:
        row.update(status='no_report_since', read='no_report')
        if per['annual_only']:
            row['notes'].append('annual filer: quarters are not in SEC XBRL, so a quarterly report shows here only with the annual one')
        return row
    row['status'] = 'scored'
    row['in_sample_suspected'] = all(x['may_be_in_valuation'] for x in new)
    opi, sh = per['operating_income'], per['diluted_shares']
    # a year that may already have been in the valuation is not scored beside a later report that was not
    full = [x for x in new if x['period'] == 'FY' and (row['in_sample_suspected'] or not x['may_be_in_valuation'])]
    items = []
    if full:
        x = full[-1]
        y = x['fiscal_year']
        a_rev = rev[(x['start'], x['end'])]['val']
        a_oi = (opi.get((x['start'], x['end'])) or {}).get('val')
        a_sh = (sh.get((x['start'], x['end'])) or {}).get('val')
        a_m = a_oi / a_rev if a_oi is not None and a_rev else None
        a_eps = a_oi * (1 - path['tax']) / a_sh if a_oi is not None and a_sh else None
        c = {'fiscal_year': y, 'end': x['end'],
             'revenue': {'actual': a_rev, 'forecast': path['revenue'].get(y), 'error': _err(a_rev, path['revenue'].get(y))},
             'margin': {'actual': a_m, 'forecast': path['margin'].get(y),
                        'diff_pts': a_m - path['margin'][y] if a_m is not None and y in path['margin'] else None},
             'eps_operating': {'actual': a_eps, 'forecast': path['eps_operating'].get(y), 'error': _err(a_eps, path['eps_operating'].get(y))}}
        if y == path['base_year']:
            c['note'] = 'the base year: the model holds a revenue estimate only (its margins and EPS start one year later)'
        elif y < path['base_year']:
            c['note'] = 'a year before the model base year: not forecast, not scored'
        row['completed'] = c
        items += [('revenue', c['revenue']['error']), ('margin', c['margin']['diff_pts'])]
    last = new[-1]
    if last['period'] != 'FY':
        y = last['fiscal_year']
        cur = rev[(last['start'], last['end'])]['val']
        prior = _find(rev, _shift(_d(last['start']), -1), _shift(_d(last['end']), -1))
        ytd_g = cur / prior['val'] - 1 if prior and prior['val'] > 0 else None
        if y in path['growth']:
            model_g, g_basis = path['growth'][y], 'model growth for the year'
        else:
            prev = _find(rev, _shift(last['fy_start'], -1), _shift(last['fy_end'], -1))
            model_g = path['revenue'][y] / prev['val'] - 1 if y == path['base_year'] and prev and prev['val'] > 0 else None
            g_basis = "the model's base-year revenue estimate against the reported prior year"
        a_oi = (opi.get((last['start'], last['end'])) or {}).get('val')
        a_m = a_oi / cur if a_oi is not None and cur else None
        row['in_progress'] = {
            'fiscal_year': y, 'period': last['period'], 'end': last['end'],
            'revenue_growth': {'ytd_actual': ytd_g, 'model_full_year': model_g, 'model_basis': g_basis if model_g is not None else None,
                               'diff_pts': ytd_g - model_g if ytd_g is not None and model_g is not None else None},
            'margin': {'ytd_actual': a_m, 'model_full_year': path['margin'].get(y),
                       'diff_pts': a_m - path['margin'][y] if a_m is not None and y in path['margin'] else None},
            'note': 'a partial year compared with a full-year path: seasonality and fading growth are not adjusted'}
        items += [('revenue', row['in_progress']['revenue_growth']['diff_pts']), ('margin', row['in_progress']['margin']['diff_pts'])]
    row['read'] = _read(items) or 'not_scorable'
    if row['read'] == 'not_scorable':
        row['notes'].append('a report came after the valuation, but no item could be compared (missing tags or model year)')
    if row['in_sample_suspected']:
        row['notes'].append(f'the filing followed the valuation within {RELEASE_LAG_DAYS} days and no release date is known: '
                            'the valuation may already have used these results; the row is kept out of the summary')
    return row


# ------------------------------------------------------------------ summary

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


ITEMS = (
    ('revenue_completed_year', 'הכנסות, שנה שהסתיימה (בפועל חלקי תחזית פחות 1)', 'pct', lambda r: ((r.get('completed') or {}).get('revenue') or {}).get('error')),
    ('margin_completed_year', 'שיעור רווח תפעולי, שנה שהסתיימה (נקודות)', 'pts', lambda r: ((r.get('completed') or {}).get('margin') or {}).get('diff_pts')),
    ('eps_completed_year', 'רווח תפעולי למניה, שנה שהסתיימה', 'pct', lambda r: ((r.get('completed') or {}).get('eps_operating') or {}).get('error')),
    ('revenue_growth_year_in_progress', 'צמיחת הכנסות מתחילת השנה מול הצמיחה השנתית במודל (נקודות)', 'pts',
     lambda r: ((r.get('in_progress') or {}).get('revenue_growth') or {}).get('diff_pts')),
    ('margin_year_in_progress', 'שיעור רווח תפעולי מתחילת השנה מול המודל (נקודות)', 'pts',
     lambda r: ((r.get('in_progress') or {}).get('margin') or {}).get('diff_pts')),
)


def summarize(rows):
    """Median error per item with its count. Fewer than MIN_ROWS scored rows: 'too few to conclude'."""
    scored = [r for r in rows if r['status'] == 'scored' and not r.get('in_sample_suspected') and r['read'] != 'not_scorable']
    out = {'rows': len(rows), 'scored_rows': len(scored), 'scored_companies': len({r['ticker'] for r in scored}),
           'no_report_since': sum(r['status'] == 'no_report_since' for r in rows),
           'custom_model': sum(r['status'] == 'custom_model' for r in rows),
           'not_scorable': sum(r['status'] == 'not_scorable' or (r['status'] == 'scored' and r['read'] == 'not_scorable') for r in rows),
           'in_sample_suspected': sum(bool(r.get('in_sample_suspected')) for r in rows), 'min_rows': MIN_ROWS, 'items': {}}
    for key, he, unit, get in ITEMS:
        xs = [x for x in (get(r) for r in scored) if x is not None]
        out['items'][key] = {'label_he': he, 'unit': unit, 'n': len(xs), 'median': _median(xs),
                             'conclusion': TOO_FEW if len(xs) < MIN_ROWS else 'median over the scored rows'}
    px = [r['price'] for r in rows if r.get('price')]
    ages = [p['age_days'] for p in px]
    young = bool(px) and _median(ages) < MIN_PRICE_AGE_DAYS
    out['items']['price_vs_value_path'] = {
        'label_he': 'מחיר מול מסלול השווי הבסיסי', 'unit': 'pct', 'n': len(px),
        'companies': len({r['ticker'] for r in rows if r.get('price')}), 'median': _median([p['vs_value_path'] for p in px]),
        'median_gap_at_valuation': _median([p['gap_at_valuation'] for p in px]), 'median_age_days': _median(ages),
        'conclusion': TOO_FEW if len(px) < MIN_ROWS or young else 'median over the priced rows',
        'note': 'a gap in level between price and value, mostly the gap that existed on the valuation day; it tests the '
                f'path only as the valuations age (median age under {MIN_PRICE_AGE_DAYS} days: too early)' if young else None}
    out['conclusion'] = TOO_FEW if len(scored) < MIN_ROWS else 'see the medians per item'
    return out


def build(rows, custom, as_of, sources=None):
    """The tracker document: rows (one per company and valuation date), the companies without lean params, the summary."""
    rows = sorted(rows, key=lambda r: (r['ticker'], r['valuation_date'] or ''))
    return {'forecast_tracker_version': TRACKER_VERSION, 'as_of': as_of, 'rows': rows,
            'custom_model': sorted(custom, key=lambda c: c['ticker']), 'summary': summarize(rows), 'sources': sources or {},
            'thresholds': {'revenue': REV_TOL, 'margin_pts': MARGIN_TOL, 'min_rows': MIN_ROWS},
            'meaning': 'Measurement only: published lean base cases against SEC-reported results and the latest close. '
                       'No model or rule is changed from this file.'}


# ------------------------------------------------------------------ Hebrew output

def _p(x, signed=True, d=1):
    if x is None:
        return '—'
    s = f'{x * 100:+.{d}f}%' if signed else f'{x * 100:.{d}f}%'
    return '‎' + s


def _pts(x):
    return '—' if x is None else '‎' + f'{x * 100:+.1f}' + ' נק׳'


def _row_he(r):
    if r['status'] == 'no_report_since':
        what, rv, mg = 'אין דוח מאז', '—', '—'
        if r.get('annual_filer'):
            what = 'אין דוח שנתי מאז (ל-SEC החברה מגישה מספרים רק פעם בשנה: דוח רבעוני לא נראה כאן, ניקוד ידני)'
    elif r['status'] != 'scored':
        what, rv, mg = 'לא נוקד: ' + ('הנתונים ב-SEC לא במטבע של המודל או חסרים' if r['status'] == 'not_scorable' else 'מודל מותאם'), '—', '—'
    else:
        last = r['reported'][-1]
        what = f"{SPAN_HE[last['period']]} של שנת {last['fiscal_year']} (עד {last['end']}, פורסם {last['reported_on']})"
        parts_r, parts_m = [], []
        c, ip = r.get('completed'), r.get('in_progress')
        if c and c['revenue']['error'] is not None:
            parts_r.append(f"שנה מלאה {c['fiscal_year']}: {c['revenue']['actual'] / 1e6:,.0f} מול {c['revenue']['forecast'] / 1e6:,.0f} מיליון ({_p(c['revenue']['error'])})")
        if c and c['margin']['diff_pts'] is not None:
            parts_m.append(f"שנה מלאה {c['fiscal_year']}: {_p(c['margin']['actual'], False)} מול {_p(c['margin']['forecast'], False)} ({_pts(c['margin']['diff_pts'])})")
        if ip:
            g, m = ip['revenue_growth'], ip['margin']
            if g['diff_pts'] is not None:
                parts_r.append(f"צמיחה מתחילת השנה {_p(g['ytd_actual'], False)} מול {_p(g['model_full_year'], False)} במודל לשנה מלאה ({_pts(g['diff_pts'])})")
            elif g['ytd_actual'] is not None:
                parts_r.append(f"צמיחה מתחילת השנה {_p(g['ytd_actual'], False)}; אין במודל צמיחה לשנה הזאת")
            if m['diff_pts'] is not None:
                parts_m.append(f"מתחילת השנה {_p(m['ytd_actual'], False)} מול {_p(m['model_full_year'], False)} במודל ({_pts(m['diff_pts'])})")
            elif m['ytd_actual'] is not None:
                parts_m.append(f"מתחילת השנה {_p(m['ytd_actual'], False)}; אין במודל שיעור רווח לשנת הבסיס")
        rv, mg = '; '.join(parts_r) or '—', '; '.join(parts_m) or '—'
        if r.get('in_sample_suspected'):
            what += ' (ייתכן שכבר היה בהערכה)'
    px = r.get('price')
    pr = (f"{px['price']:,.2f} מול {px['value_path']:,.2f} ({_p(px['vs_value_path'], d=0)}; ביום ההערכה {_p(px['gap_at_valuation'], d=0)})"
          if px else '—')
    return f"| {r['ticker']} | {r['valuation_date']} | {what} | {rv} | {mg} | {pr} | {READ_HE.get(r['read'], r['read'])} |"


def render_he(doc):
    s = doc['summary']
    lines = [f"# מעקב תחזיות: מה קרה מאז כל הערכת שווי ({doc['as_of']})", '',
             'הטבלה רק מודדת. היא לא משנה אף מודל ואף כלל.', '',
             '| חברה | תאריך ההערכה | מה דווח מאז | הכנסות מול המסלול | שיעור רווח תפעולי מול המסלול | מחיר מול מסלול השווי | קריאה |',
             '|---|---|---|---|---|---|---|']
    lines += [_row_he(r) for r in doc['rows'] if r['status'] != 'custom_model']
    lines.append('')
    custom = sorted({c['ticker'] for c in doc['custom_model']} | {r['ticker'] for r in doc['rows'] if r['status'] == 'custom_model'})
    if custom:
        lines.append(f"- מודל מותאם או מודל אחר (אין מודל רזה שאפשר לנקד), ניקוד ידני: {', '.join(custom)}.")
    lines += ['', '## סיכום', '',
              f"- שורות: {s['rows']}; עם דוח מאז ההערכה שנוקד: {s['scored_rows']} ({s['scored_companies']} חברות); "
              f"בלי דוח מאז: {s['no_report_since']}; מודל רזה שבנוי אחרת (ניקוד ידני): {s['custom_model']}; לא נוקדו: {s['not_scorable']}. "
              f"חברות בלי מודל רזה בכלל (ניקוד ידני): {len(doc['custom_model'])}."]
    if s['scored_rows'] < s['min_rows']:
        lines.append(f"- פחות מ-{s['min_rows']} שורות מנוקדות: מעט מדי כדי להסיק מסקנה (too few to conclude).")
    for key, it in s['items'].items():
        if key == 'price_vs_value_path':
            continue
        med = '—' if it['median'] is None else (_p(it['median']) if it['unit'] == 'pct' else _pts(it['median']))
        tail = ': מעט מדי כדי להסיק (too few to conclude)' if it['conclusion'] == TOO_FEW else ''
        lines.append(f"- {it['label_he']}: חציון {med}, מספר שורות {it['n']}{tail}.")
    it = s['items']['price_vs_value_path']
    if it['n']:
        tail = ('; ההערכות צעירות (גיל חציוני ' + f"{it['median_age_days']:.0f}" + ' ימים), לכן זה פער ברמה ולא מבחן של המסלול: '
                'מעט מדי כדי להסיק (too few to conclude)') if it['conclusion'] == TOO_FEW else ''
        lines.append(f"- {it['label_he']}: חציון {_p(it['median'], d=0)} (ביום ההערכה {_p(it['median_gap_at_valuation'], d=0)}), "
                     f"מספר שורות {it['n']}" + (f" ({it['companies']} חברות)" if it.get('companies') else '') + f"{tail}.")
    lines += ['', '## איך לקרוא', '',
              '- שנה שעדיין לא הסתיימה: משווים את הצמיחה מתחילת השנה (מול אותה תקופה לפני שנה) לצמיחה שהמודל מניח לשנה המלאה, '
              'ואת שיעור הרווח מתחילת השנה לשיעור של המודל לאותה שנה. זו השוואה של חלק משנה למסלול של שנה מלאה, ולכן היא גסה.',
              '- שנה שהסתיימה: משווים הכנסות, שיעור רווח תפעולי ורווח תפעולי למניה ישירות.',
              '- שיעור הרווח בפועל הוא לפי הדוח הרשמי (GAAP). במודל הוא אחרי תגמול במניות ולפעמים בלי סעיפים חד-פעמיים, '
              'לכן פער קטן יכול לנבוע מההגדרה.',
              f"- קריאה: 'בקצב' כשההכנסות בטווח של {REV_TOL:.0%} (או 2 נקודות צמיחה) ושיעור הרווח בטווח של נקודה אחת. המחיר לא נכנס לקריאה.",
              '- מחיר מול מסלול השווי: מחיר הסגירה האחרון חלקי השווי הבסיסי של המודל לאותו יום. מינוס = המחיר מתחת לשווי. '
              'השווי הוא ערך לפי המודל, לא תחזית מחיר.',
              '- המקור לנתונים בפועל: דיווחי SEC (companyfacts). חברות שמדווחות ל-SEC רק פעם בשנה יופיעו כאן רק אחרי הדוח השנתי.']
    return '\n'.join(lines) + '\n'
