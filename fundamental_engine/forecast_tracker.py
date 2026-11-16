"""Forecast tracker: scores the FROZEN base cases (research/ledger.jsonl, written by lean_freeze) against what was reported
after them. Pure logic (no network, no file access); scripts/forecast_tracker.py reads the ledger, the saved files and
the SEC data, and writes the outcome rows.

It only measures. It never changes a model or a rule: rules change only through a preregistered forward test with at
least MIN_ROWS scored reports from at least MIN_COMPANIES companies (research/RULE_CHANGES_he.md). A measured bias is a
warning row, never an input.

What is a test (v2, 15.11.2026):
- the numbers come from the ledger (the frozen thesis row and its forecast rows), never from a rerun of an editable
  params file; the params and result files are read only when their sha256 still equals the frozen one (the tax rate
  and the fiscal calendar), and a changed file is reported;
- one row per company and reported period: the latest valuation frozen BEFORE that report (recorded day < report day).
  A valuation frozen after the report, or never frozen, gives a row labelled 'descriptive only' that no summary counts.
  Three revisions of one company before one report are one row;
- a completed fiscal year: revenue, operating margin and operating EPS against the model's year (the base year has a
  revenue estimate only: the model's margins and EPS start one year later), with an on-track / ahead / behind word;
- a year in progress: the newly reported part (the year to date now minus the year to date already reported before
  this report) against the model year minus what was already reported; plus the step per remaining quarter the model
  year still needs. A partial year gets numbers and no ahead/behind word (seasonality is not modelled);
- custom models, banks and foreign filers without quarterly XBRL: a manual scorecard.json (SCORECARD_FORM) next to the
  valuation it scores; its typed falsifiers are listed as fired / not fired / not measurable;
- price: the latest close against the frozen base value path (a known split from the record rescales the close).
Actuals are SEC companyfacts. A period counts as reported after the valuation when its results release (8-K item 2.02,
when the script supplies the dates) or else its first XBRL filing is dated after the valuation's as_of. Without release
dates, a filing that follows the valuation within RELEASE_LAG_DAYS, for a period that had already ended, may already
have been in it: the row says so and stays out of the summary. The actual margin is GAAP operating income / revenue; the
model's margin is after SBC and may leave out items, so a small margin gap can be a definition gap. Actual operating EPS
= operating income x (1 - the model's tax rate) / diluted shares, the model's own definition.
"""
import math
from datetime import date, timedelta

TRACKER_VERSION = 2
MIN_ROWS = 8                 # below this many scored rows a median is reported with 'too few to conclude'
MIN_COMPANIES = 3            # ... and below this many companies (RULE_CHANGES_he.md)
MIN_PRICE_AGE_DAYS = 90      # a price gap of a younger valuation is a level, not yet a test of the path
REV_TOL = 0.02               # on track: revenue within 2% (completed year)
MARGIN_TOL = 0.01            # on track: operating margin within 1 point
DATE_TOL = 12                # days: 52/53-week fiscal calendars and rounded days_to_base_year_end
PRICE_SCALE_LIMIT = 1.8      # latest close vs the valuation's price beyond this factor: split or another unit, not scored
MAX_PERIOD_AGE_DAYS = 200    # a period that ended longer before the valuation was reported before it
RELEASE_LAG_DAYS = 45        # a first filing this soon after the valuation may follow a release the valuation used
                             # (an annual report can follow its results release by several weeks)
FILING_MATCH_DAYS = 10       # a filing-index report this close to a scored report or a scorecard is the same report
TOO_FEW = 'too few to conclude'
DESCRIPTIVE = 'descriptive only'
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
MONTHS = {'Q1': 3, 'H1': 6, '9M': 9, 'FY': 12}
RESULT_FORMS = {'10-Q': 'quarterly report', '10-K': 'annual report', '20-F': 'annual report (foreign filer)',
                '40-F': 'annual report (Canadian filer)', '6-K': 'foreign filer current report (results or other news)'}
FALSIFIER_STATES = ('fired', 'not_fired', 'not_measurable')
FIRED_ACTIONS = ('exit', 'trim')             # a fired falsifier with one of these actions needs acted_on or a reason
SCORECARD_VERSION = 1
SCORECARD_FORM = {
    'scorecard_version': SCORECARD_VERSION,
    'ticker': 'T', 'period': 'Q3 2026 (a label)', 'period_end': 'YYYY-MM-DD', 'reported_on': 'YYYY-MM-DD',
    'sources': [{'title': 'results release (6-K / 8-K exhibit)', 'url': 'https://www.sec.gov/...'}],
    'against': {'thesis_id': 'the frozen thesis id in research/ledger.jsonl (or null)', 'ledger': 'research/ledger.jsonl',
                'as_of': 'YYYY-MM-DD', 'folder': 'research/companies/T/YYYY-MM-DD'},
    'items': [{'metric': 'name', 'model_value': 0.0, 'actual': 0.0, 'unit': 'USD | ratio | ...', 'kind': 'actual | guidance',
               'forecast_id': 'optional: a ledger forecast id this actual resolves (period ended, same basis)', 'note': ''}],
    'falsifiers': [{'id': 'f1', 'text': 'what breaks the thesis', 'state': 'fired | not_fired | not_measurable', 'note': '',
                    'action_if_fired': 'exit | trim | add_blocked | review | null', 'acted_on': None, 'reason_not_acted': None,
                    'on_file': 'where the falsifier was written before the report'}],
    'notes': [],
}
READ_HE = {'on_track': 'בקצב', 'ahead': 'מקדים את המסלול', 'behind': 'מפגר אחרי המסלול', 'mixed': 'מעורב',
           'partial': 'שנה חלקית: מספרים בלי קביעה', 'no_report': 'אין דוח מאז', 'custom': 'ניקוד ידני',
           'scorecard': 'כרטיס ניקוד ידני', 'not_scorable': 'לא נוקד', 'report_not_scored': 'דוח בלי ניקוד'}
SPAN_HE = {'Q1': 'רבעון ראשון', 'H1': 'חצי שנה', '9M': 'תשעה חודשים', 'FY': 'שנה מלאה'}
STATE_HE = {'fired': 'הופעל', 'not_fired': 'לא הופעל', 'not_measurable': 'אי אפשר למדוד עדיין'}


def _d(s):
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def _shift(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:                       # 29 February
        return d.replace(year=d.year + years, day=28)


def _span(start, end):
    n = (_d(end) - _d(start)).days
    return next((name for name, lo, hi in SPANS if lo <= n <= hi), None)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


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
    dated value points. result: the SAVED lean_result.json (or lean_model.run(params), the same keys)."""
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
            'value_points': points, 'source': 'saved result'}


def ledger_path(thesis, params=None):
    """The frozen base case as a path, from the ledger rows only: the thesis row (values, year-end dates, price) and its
    forecast rows (revenue, operating margin, operating EPS per fiscal year). params: the params file, given only when
    its sha256 still equals the frozen one; it supplies the tax rate (operating EPS) and the exact fiscal calendar.
    Value points: today and the dated year-ends, log-linear between them."""
    rec = thesis['record']
    vals = rec.get('values') or {}
    as_of = _d(rec['as_of'])
    series = {}
    for f in thesis.get('forecasts') or []:
        y = (f.get('context') or {}).get('fiscal_year')
        if y is not None:
            series.setdefault(f['metric'], {})[int(y)] = f['scenarios']['base']
    ye_dates = {int(y): _d(v) for y, v in (vals.get('year_end_dates') or {}).items()}
    if params and 'base_year' in params and params.get('days_to_base_year_end') is not None:
        base_year, base_end = int(params['base_year']), as_of + timedelta(days=params['days_to_base_year_end'])
    elif ye_dates:
        y1 = min(ye_dates)
        base_year, base_end = y1 - 1, _shift(ye_dates[y1], -1)
    else:
        base_year, base_end = None, None
    revenue = series.get('revenue', {})
    points = [(as_of, (vals.get('today') or {}).get('base'))]
    points += sorted((ye_dates[int(y)], v) for y, v in ((vals.get('year_end') or {}).get('base') or {}).items()
                     if int(y) in ye_dates and ye_dates[int(y)] > as_of)
    points = [(d, v) for d, v in points if _num(v)]
    return {'as_of': as_of, 'base_year': base_year, 'base_end': base_end, 'tax': params.get('tax') if params else None,
            'price': rec.get('price'), 'revenue': revenue,
            'growth': {y: revenue[y] / revenue[y - 1] - 1 for y in revenue if revenue.get(y - 1)},
            'margin': series.get('operating_margin', {}), 'eps_operating': series.get('eps_operating', {}),
            'value_points': points, 'source': 'ledger', 'thesis_id': thesis.get('thesis_id'),
            'known_split': (rec.get('share_basis') or {}).get('known_split') or rec.get('known_split')}


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


def filing_events(recent, after, today):
    """Results-type filings in the SEC filing index after a date: 8-K item 2.02 (results release), 10-Q, 10-K, 20-F,
    40-F and every 6-K (a foreign filer's results arrive as a 6-K, among other news). recent: {'form', 'filingDate',
    'items', ...} lists of the submissions file (filings.recent). Returns [{form, date, kind}] sorted by date."""
    out = []
    forms, dates = (recent or {}).get('form') or [], (recent or {}).get('filingDate') or []
    items = (recent or {}).get('items') or [''] * len(forms)
    for f, d, it in zip(forms, dates, items):
        f = str(f)
        if not (after < d <= today):
            continue
        if f.startswith('8-K') and '2.02' in str(it):
            out.append({'form': f, 'date': d, 'kind': 'results release (8-K item 2.02)'})
        elif f.split('/')[0] in RESULT_FORMS:
            out.append({'form': f, 'date': d, 'kind': RESULT_FORMS[f.split('/')[0]]})
    return sorted(out, key=lambda x: x['date'])


# ------------------------------------------------------------------ scoring one valuation against one report

def _err(actual, forecast):
    return actual / forecast - 1 if actual is not None and forecast else None


def _read(items):
    """on_track / ahead / behind / mixed from the business items of a COMPLETED year (price is not part of the read)."""
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


def price_check(path, price, known_split=None):
    """{'price', 'date', 'value_path', 'vs_value_path', ...} or (None, note). A close beyond PRICE_SCALE_LIMIT x the
    valuation's price is a split or another unit: with a known split {date, ratio (new shares per old)} dated after the
    valuation, the close is put back on the valuation's share basis; otherwise the price is not scored."""
    if not price or not price.get('value') or price.get('date', '') < path['as_of'].isoformat() or not path.get('price'):
        return None, None
    close, note = price['value'], None
    scale = close / path['price']
    if not 1 / PRICE_SCALE_LIMIT <= scale <= PRICE_SCALE_LIMIT:
        ks = known_split or path.get('known_split')
        if ks and _num(ks.get('ratio')) and ks['ratio'] > 0 and ks.get('date') and path['as_of'].isoformat() < str(ks['date'])[:10] <= price['date']:
            close = close * ks['ratio']
            note = f"close {price['value']:,.2f} x {ks['ratio']:g} for the split of {str(ks['date'])[:10]} (from the record)"
            if not 1 / PRICE_SCALE_LIMIT <= close / path['price'] <= PRICE_SCALE_LIMIT:
                return None, note + ': still outside the scale guard, price not scored'
        else:
            return None, f"price {price['value']:,.2f} is {scale:.2f}x the valuation's price: a split or another unit, price not scored"
    vp = value_at(path['value_points'], price['date'])
    if not vp:
        return None, note
    return {'price': round(close, 4), 'date': price['date'], 'value_path': round(vp, 2), 'vs_value_path': close / vp - 1,
            'price_at_valuation': path['price'], 'gap_at_valuation': path['price'] / path['value_points'][0][1] - 1,
            'age_days': (_d(price['date']) - path['as_of']).days, 'split_adjusted': note}, note


def _ytd_before(rev, fy_start, end, before_on, release_dates):
    """(value, months, end) of the latest year-to-date period of the same fiscal year that ended before `end` and was
    public before `before_on`; (0.0, 0, None) when none."""
    best = None
    for (s, e), r in rev.items():
        span = _span(s, e)
        if span not in ('Q1', 'H1', '9M') or abs((_d(s) - fy_start).days) > DATE_TOL or e >= end:
            continue
        if _reported_on(e, r['first_filed'], release_dates)[0] >= before_on:
            continue
        if best is None or e > best[2]:
            best = (r['val'], MONTHS[span], e, (s, e))
    return best or (0.0, 0, None, None)


def score(ticker, params, result, payload, today, price=None, release_dates=None, params_path=None, path=None,
          since=None, until=None):
    """One row: a valuation's base case against what was reported after its as_of.

    path: the frozen path (ledger_path); without it the params and the SAVED result give model_path (no rerun).
    since / until: only periods reported after `since` (default: the as_of) and on or before `until` (default: today)
    are new for this row; earlier ones count as already reported. price: {'value', 'date'} (the latest close) or None.
    release_dates: ISO dates of results releases or None."""
    if path is None:
        ok, why = lean_format(params)
        if not ok:
            return {'ticker': ticker, 'valuation_date': str(params.get('as_of'))[:10] if isinstance(params, dict) else None,
                    'params_path': params_path, 'status': 'custom_model', 'read': 'custom', 'reported': [], 'completed': None,
                    'in_progress': None, 'price': None, 'notes': [], 'reason': f'custom model: scored by hand ({why})'}
        path = model_path(params, result)
    as_of = path['as_of'].isoformat()
    row = {'ticker': ticker, 'valuation_date': as_of, 'params_path': params_path, 'thesis_id': path.get('thesis_id'),
           'path_source': path.get('source'), 'status': None, 'reported': [], 'completed': None, 'in_progress': None,
           'price': None, 'read': None, 'notes': []}
    px, note = price_check(path, price)
    row['price'] = px
    if note:
        row['notes'].append(note)
    if payload is None:
        row.update(status='not_scorable', read='not_scorable', reason='no SEC companyfacts for this company')
        return row
    per = periods(payload)
    if per['currency'] != 'USD':
        row.update(status='not_scorable', read='not_scorable',
                   reason=f"SEC facts are in {per['currency'] or 'no currency'}, the model in USD: scored by hand")
        return row
    if path.get('base_end') is None:
        row.update(status='not_scorable', read='not_scorable', reason='the frozen record has no fiscal calendar')
        return row
    rev = per['revenue']
    row['annual_filer'] = per['annual_only']
    lo, hi = since or as_of, until or today
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
        if on <= lo or on > hi:
            continue                                             # released before the valuation (or an earlier row), or later
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
            row['notes'].append('annual filer: quarters are not in SEC XBRL, so a quarterly report shows here only with the annual '
                                'one; fill a scorecard.json for the quarter')
        return row
    row['status'] = 'scored'
    row['reported_on'] = max(x['reported_on'] for x in new)
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
        a_eps = a_oi * (1 - path['tax']) / a_sh if a_oi is not None and a_sh and path.get('tax') is not None else None
        c = {'fiscal_year': y, 'end': x['end'], 'start': x['start'], 'reported_on': x['reported_on'],
             'revenue': {'actual': a_rev, 'forecast': path['revenue'].get(y), 'error': _err(a_rev, path['revenue'].get(y))},
             'margin': {'actual': a_m, 'forecast': path['margin'].get(y),
                        'diff_pts': a_m - path['margin'][y] if a_m is not None and y in path['margin'] else None},
             'eps_operating': {'actual': a_eps, 'forecast': path['eps_operating'].get(y), 'error': _err(a_eps, path['eps_operating'].get(y))}}
        if y == path['base_year']:
            c['note'] = 'the base year: the model holds a revenue estimate only (its margins and EPS start one year later)'
        elif y < path['base_year']:
            c['note'] = 'a year before the model base year: not forecast, not scored'
        if path.get('tax') is None and a_oi is not None:
            row['notes'].append('operating EPS not scored: the tax rate comes from the params file, which no longer equals the frozen one')
        row['completed'] = c
        items += [('revenue', c['revenue']['error']), ('margin', c['margin']['diff_pts'])]
    last = new[-1]
    if last['period'] != 'FY':
        row['in_progress'] = _partial(path, rev, opi, last, release_dates)
    if full:
        row['read'] = _read(items) or 'not_scorable'
    elif row['in_progress'] and row['in_progress']['revenue']['new_part'] is not None:
        row['read'] = 'partial'
    else:
        row['read'] = 'not_scorable'
    if row['read'] == 'not_scorable':
        row['notes'].append('a report came after the valuation, but no item could be compared (missing tags or model year)')
    if row['in_sample_suspected']:
        row['notes'].append(f'the filing followed the valuation within {RELEASE_LAG_DAYS} days and no release date is known: '
                            'the valuation may already have used these results; the row is kept out of the summary')
    return row


def _partial(path, rev, opi, last, release_dates):
    """The newly reported part of a year in progress against the model year minus what was already reported."""
    y, months = last['fiscal_year'], MONTHS[last['period']]
    cur = rev[(last['start'], last['end'])]['val']
    prev_val, prev_m, prev_end, prev_key = _ytd_before(rev, last['fy_start'], last['end'], last['reported_on'], release_dates)
    notes = ['a partial year: the new part is compared with what the model year still needed; seasonality is not '
             'modelled, so there is no ahead/behind word']
    if prev_end is None and months > 3:
        notes.append('no earlier year-to-date figure of this year in the filings: the whole year to date is treated as new')
    new_rev, new_m = cur - prev_val, months - prev_m
    model_fy = path['revenue'].get(y)
    out = {'fiscal_year': y, 'period': last['period'], 'end': last['end'], 'reported_on': last['reported_on'],
           'already_reported': {'end': prev_end, 'months': prev_m, 'revenue': prev_val}, 'notes': notes}
    r = {'new_part': new_rev if new_m > 0 else None, 'new_months': new_m, 'model_year': model_fy,
         'remainder_before': None, 'share_of_remainder': None, 'time_share': None, 'needed_rest': None, 'step_per_quarter': None}
    if model_fy and new_m > 0:
        rem = model_fy - prev_val
        r.update(remainder_before=rem, share_of_remainder=new_rev / rem if rem > 0 else None,
                 time_share=new_m / (12 - prev_m) if prev_m < 12 else None)
        left = 12 - months
        r['needed_rest'] = model_fy - cur
        if left > 0 and new_rev > 0:
            r['step_per_quarter'] = (r['needed_rest'] / (left / 3)) / (new_rev / (new_m / 3)) - 1
    out['revenue'] = r
    oi_cur = (opi.get((last['start'], last['end'])) or {}).get('val')
    oi_prev = 0.0 if prev_end is None else (opi.get(prev_key) or {}).get('val')
    m = {'new_part': None, 'model_year': path['margin'].get(y), 'diff_pts': None}
    if oi_cur is not None and oi_prev is not None and new_rev > 0 and new_m > 0:
        m['new_part'] = (oi_cur - oi_prev) / new_rev
        if m['model_year'] is not None:
            m['diff_pts'] = m['new_part'] - m['model_year']
    out['margin'] = m
    return out


# ------------------------------------------------------------------ the ledger

def frozen_theses(rows):
    """{ticker: [thesis]} from ledger lines, each {'thesis_id', 'ticker', 'as_of', 'made_at', 'recorded_at', 'record',
    'forecasts', 'outcomes'}, sorted by as_of then recorded_at. Only lean_freeze rows (research/ledger.jsonl)."""
    out, by_id = {}, {}
    for r in rows:
        if r.get('type') == 'thesis' and str((r.get('record') or {}).get('frozen_by', '')).startswith('lean_freeze'):
            t = {'thesis_id': r['thesis_id'], 'ticker': r['entity'], 'as_of': r['record']['as_of'], 'made_at': r['made_at'],
                 'recorded_at': r['recorded_at'], 'record': r['record'], 'forecasts': [], 'source': 'ledger'}
            out.setdefault(r['entity'], []).append(t)
            by_id[r['thesis_id']] = t
    for r in rows:
        if r.get('type') == 'forecast' and r.get('thesis_id') in by_id:
            by_id[r['thesis_id']]['forecasts'].append(r)
    for v in out.values():
        v.sort(key=lambda t: (t['as_of'], t['recorded_at']))
    return out


def file_check(record, now):
    """Whether the files behind a frozen thesis still hold the frozen content. now: {'params_sha256', 'result_sha256',
    'result_values_today'} of the files as they are today (None for a missing file). Returns {'params', 'result',
    'holds', 'note'}: 'same' | 'changed' | 'missing' | None (no params file); a result rewritten with extra keys but
    the frozen values is 'rewritten'."""
    files = record.get('files') or {}
    out = {'params': None, 'result': None}
    if files.get('params'):
        p = now.get('params_sha256')
        out['params'] = 'missing' if p is None else 'same' if p == files['params']['sha256'] else 'changed'
    r = now.get('result_sha256')
    if r is None:
        out['result'] = 'missing'
    elif r == files['result']['sha256']:
        out['result'] = 'same'
    else:
        frozen = (record.get('values') or {}).get('today') or {}
        cur = now.get('result_values_today') or {}
        out['result'] = 'rewritten' if frozen and all(cur.get(k) == v for k, v in frozen.items()) else 'changed'
    out['holds'] = out['params'] in (None, 'same') and out['result'] in ('same', 'rewritten')
    bad = [f'{k} file {v}' for k, v in out.items() if k in ('params', 'result') and v in ('changed', 'missing')]
    out['note'] = (('; '.join(bad) + ' since the freeze: scored from the frozen ledger rows only') if bad else
                   'result file rewritten with extra keys; the frozen values are unchanged' if out['result'] == 'rewritten' else None)
    return out


def choose(candidates, reported_on):
    """(candidate, 'test' | 'descriptive', reason) for a report: the latest valuation frozen before the report day; else
    the latest valuation made before it, labelled descriptive only; (None, None, None) when there is none."""
    before = [c for c in candidates if c['as_of'] < reported_on]
    frozen = [c for c in before if c.get('recorded_at') and c['recorded_at'][:10] < reported_on]
    if frozen:
        return max(frozen, key=lambda c: (c['recorded_at'], c['as_of'])), 'test', None
    if before:
        c = max(before, key=lambda c: (c['as_of'], c.get('recorded_at') or ''))
        why = (f"{DESCRIPTIVE}: the valuation of {c['as_of']} was frozen on {c['recorded_at'][:10]}, after the report of {reported_on}"
               if c.get('recorded_at') else f"{DESCRIPTIVE}: the valuation of {c['as_of']} was never frozen in research/ledger.jsonl")
        return c, 'descriptive', why
    return None, None, None


def company_rows(ticker, candidates, payload, today, price=None, release_dates=None, filings=None, scorecards=()):
    """One row per reported period (the latest valuation frozen before it), plus one row for the newest valuation when
    nothing was reported after it. candidates: [{'thesis_id' or None, 'as_of', 'recorded_at' or None, 'kind' ('lean' |
    'custom'), 'path' (ledger_path / model_path or None), 'file_check', 'params_path'}]; the newest gets the price.
    scorecards: the scorecard rows of this company (score_scorecard)."""
    cands = sorted(candidates, key=lambda c: (c['as_of'], c.get('recorded_at') or ''))
    if not cands:
        return []
    newest, earliest = cands[-1], cands[0]['as_of']
    rows, report_days = [], []
    lean = [c for c in cands if c['kind'] == 'lean' and c.get('path') and c['path'].get('base_end')]
    if lean and payload is not None:
        cal = lean[-1]['path']
        probe = score(ticker, None, None, payload, today, release_dates=release_dates, path=dict(cal, as_of=_d(earliest)))
        days = sorted({x['reported_on'] for x in probe.get('reported') or []})
        prev = None
        for on in days:
            c, mode, why = choose(cands, on)
            if c is None:
                continue
            if c['kind'] != 'lean' or not c.get('path'):
                row = {'ticker': ticker, 'valuation_date': c['as_of'], 'thesis_id': c.get('thesis_id'), 'status': 'custom_model',
                       'read': 'custom', 'reported_on': on, 'reported': [], 'completed': None, 'in_progress': None, 'price': None,
                       'notes': [], 'reason': 'custom model: fill a scorecard.json for this report'}
            else:
                since = prev if prev and prev >= c['as_of'] else None
                row = score(ticker, None, None, payload, today, release_dates=release_dates, path=c['path'], since=since, until=on,
                            params_path=c.get('params_path'))
                row['price'] = None
            row.update(mode=mode, descriptive_reason=why, file_check=c.get('file_check'))
            if row['status'] == 'scored' or row['status'] == 'custom_model':
                rows.append(row)
                report_days.append(on)
            prev = on
    for sc in scorecards or []:
        rows = [r for r in rows if not (r.get('reported_on') and abs((_d(r['reported_on']) - _d(sc['reported_on'])).days) <= FILING_MATCH_DAYS
                                        and r['status'] == 'custom_model')]
        rows.append(sc)
        report_days.append(sc['reported_on'])
    filed_days = []
    for f in filings or []:
        if f['form'].startswith('10-'):
            continue                                       # a US filer's 10-Q / 10-K numbers arrive through XBRL above
        if f['date'] <= earliest or any(abs((_d(f['date']) - _d(d)).days) <= FILING_MATCH_DAYS for d in report_days + filed_days):
            continue
        c, _, _ = choose(cands, f['date'])
        rows.append({'ticker': ticker, 'valuation_date': c['as_of'] if c else None, 'thesis_id': (c or {}).get('thesis_id'),
                     'status': 'report_not_scored', 'read': 'report_not_scored', 'reported_on': f['date'], 'form': f['form'],
                     'reported': [], 'completed': None, 'in_progress': None, 'price': None, 'mode': None, 'descriptive_reason': None,
                     'notes': [f"{f['form']} filed {f['date']} ({f['kind']}): no XBRL period and no scorecard.json yet"
                               + ('; if it is a results report, fill a scorecard.json' if f['form'].startswith('6-K') else '')]})
        filed_days.append(f['date'])
    after_newest = [d for d in report_days if d > newest['as_of']]     # a filing-only event may be other news: not a report
    if not after_newest:
        if newest['kind'] == 'lean' and newest.get('path') and newest['path'].get('base_end'):
            row = score(ticker, None, None, payload, today, price=price, release_dates=release_dates, path=newest['path'],
                        params_path=newest.get('params_path'))
            if row['status'] == 'scored':                  # a report the probe did not see (calendar of another valuation)
                row['notes'].append('reported after this valuation; scored here because the probe did not date it')
        else:
            row = {'ticker': ticker, 'valuation_date': newest['as_of'], 'thesis_id': newest.get('thesis_id'),
                   'status': 'no_report_since', 'read': 'no_report', 'reported': [], 'completed': None, 'in_progress': None,
                   'notes': [], 'price': None, 'custom': True}
            if newest.get('path'):
                row['price'], note = price_check(newest['path'], price)
                if note:
                    row['notes'].append(note)
        mode = 'test' if newest.get('recorded_at') else 'descriptive'
        row.update(mode=mode, descriptive_reason=None if mode == 'test' else
                   f"{DESCRIPTIVE}: the valuation of {newest['as_of']} was never frozen in research/ledger.jsonl",
                   file_check=newest.get('file_check'), current=True)
        rows.append(row)
    for r in rows:
        fc = r.get('file_check') or {}
        if fc.get('note') and fc['note'] not in r['notes']:
            r['notes'].append(fc['note'])
    return sorted(rows, key=lambda r: (r.get('reported_on') or '9999', r.get('valuation_date') or ''))


# ------------------------------------------------------------------ manual scorecards

def check_scorecard(sc):
    """Problems with a scorecard against SCORECARD_FORM (empty list: valid)."""
    p = []
    if not isinstance(sc, dict):
        return ['not an object']
    for k in ('ticker', 'period', 'period_end', 'reported_on', 'sources', 'against', 'items', 'falsifiers'):
        if k not in sc:
            p.append(f'missing {k}')
    for k in ('period_end', 'reported_on'):
        try:
            _d(sc.get(k))
        except (TypeError, ValueError):
            p.append(f'{k} is not an ISO date')
    if not sc.get('sources') or not all(isinstance(s, dict) and str(s.get('url', '')).startswith('http') for s in sc.get('sources') or []):
        p.append('every source needs a url')
    for i, it in enumerate(sc.get('items') or []):
        if not isinstance(it, dict) or not it.get('metric') or not it.get('unit'):
            p.append(f'item {i}: metric and unit required')
        elif it.get('actual') is not None and not _num(it['actual']):
            p.append(f"item {it['metric']}: actual must be a number or null")
        elif it.get('model_value') is not None and not _num(it['model_value']):
            p.append(f"item {it['metric']}: model_value must be a number or null")
    for f in sc.get('falsifiers') or []:
        if not isinstance(f, dict) or not f.get('id') or not f.get('text'):
            p.append('falsifier: id and text required')
        elif f.get('state') not in FALSIFIER_STATES:
            p.append(f"falsifier {f['id']}: state must be one of {', '.join(FALSIFIER_STATES)}")
    return p


def score_scorecard(sc, theses=None, path=None):
    """A row from a manual scorecard. theses: {thesis_id: thesis} of research/ledger.jsonl. The row is a test only when
    the thesis it names was frozen there before the report; otherwise descriptive only."""
    problems = check_scorecard(sc)
    on = str(sc.get('reported_on'))[:10]
    against = sc.get('against') or {}
    t = (theses or {}).get(against.get('thesis_id'))
    if problems:
        mode, why = 'descriptive', f'{DESCRIPTIVE}: the scorecard is not valid ({"; ".join(problems)})'
    elif t is None:
        mode, why = 'descriptive', (f"{DESCRIPTIVE}: the valuation it is scored against ({against.get('thesis_id') or against.get('as_of')}"
                                    f"{', ' + against['ledger'] if against.get('ledger') else ''}) is not a thesis frozen in research/ledger.jsonl")
    elif t['recorded_at'][:10] >= on:
        mode, why = 'descriptive', f"{DESCRIPTIVE}: the valuation of {t['as_of']} was frozen on {t['recorded_at'][:10]}, after the report of {on}"
    else:
        mode, why = 'test', None
    items = []
    for it in sc.get('items') or []:
        if not isinstance(it, dict):
            continue
        a, m = it.get('actual'), it.get('model_value')
        both = _num(a) and _num(m)
        ratio = it.get('unit') in ('ratio', 'pct')
        if it.get('kind') == 'partial':            # a part of a model year: a share, no error and no ahead/behind word
            items.append(dict(it, error=None, diff_pts=None, share=a / m if both and m else None))
            continue
        items.append(dict(it, error=None if not both or ratio or not m else a / m - 1, diff_pts=a - m if both and ratio else None))
    fals = []
    for f in sc.get('falsifiers') or []:
        if not isinstance(f, dict):
            continue
        g = dict(f)
        g['needs_reason'] = (f.get('state') == 'fired' and f.get('action_if_fired') in FIRED_ACTIONS and not f.get('acted_on')
                             and not str(f.get('reason_not_acted') or '').strip())
        fals.append(g)
    row = {'ticker': sc.get('ticker'), 'valuation_date': (t or {}).get('as_of') or against.get('as_of'),
           'thesis_id': against.get('thesis_id'), 'status': 'scorecard', 'read': 'scorecard', 'mode': mode, 'descriptive_reason': why,
           'reported_on': on, 'period': sc.get('period'), 'period_end': sc.get('period_end'), 'sources': sc.get('sources'),
           'scorecard_path': sc.get('_path'), 'items': items, 'falsifiers': fals, 'problems': problems, 'reported': [],
           'completed': None, 'in_progress': None, 'price': None, 'notes': list(sc.get('notes') or [])}
    if any(f['needs_reason'] for f in fals):
        row['notes'].append('a fired exit or trim falsifier was not acted on and has no reason: add reason_not_acted')
    return row


# ------------------------------------------------------------------ outcomes, review bias

OUTCOME_METRICS = (('revenue', 'revenue', 'actual'), ('operating_margin', 'margin', 'actual'), ('eps_operating', 'eps_operating', 'actual'))


def outcome_candidates(rows, theses_by_id):
    """[{forecast_id, actual, unit, currency, basis, observed_at, source_id}] the tracker may write as ledger outcome rows:
    test rows only, a completed fiscal year matched to the thesis' forecast row of that metric and year, and scorecard
    items that name a forecast_id. The ledger itself refuses a period that has not ended, a second outcome and a
    different definition."""
    out = []
    for r in rows:
        if r.get('mode') != 'test':
            continue
        t = theses_by_id.get(r.get('thesis_id'))
        if t is None:
            continue
        fc = {f['id']: f for f in t['forecasts']}
        c = r.get('completed')
        if r['status'] == 'scored' and c and not r.get('in_sample_suspected'):
            for metric, key, field in OUTCOME_METRICS:
                a = (c.get(key) or {}).get(field)
                f = next((f for f in t['forecasts'] if f['metric'] == metric and (f.get('context') or {}).get('fiscal_year') == c['fiscal_year']), None)
                if f is None or not _num(a):
                    continue
                out.append({'forecast_id': f['id'], 'actual': a, 'unit': f['unit'], 'currency': f.get('currency'), 'basis': f['basis'],
                            'observed_at': c['reported_on'], 'source_id': f"sec:companyfacts:{r['ticker']}:{c['start']}..{c['end']}"})
        for it in r.get('items') or []:
            f = fc.get(it.get('forecast_id'))
            if f is None or not _num(it.get('actual')) or it.get('unit') != f['unit']:
                continue
            out.append({'forecast_id': f['id'], 'actual': it['actual'], 'unit': f['unit'], 'currency': f.get('currency'),
                        'basis': f['basis'], 'observed_at': r['reported_on'],
                        'source_id': ((r.get('sources') or [{}])[0].get('url') or r.get('scorecard_path') or 'scorecard')})
    return out


def review_changes(theses):
    """The engine's first draft against the reviewed base, per frozen thesis that holds both (pre-review params rerun
    at freeze, or review_findings.json): [{ticker, as_of, pre, post, change, change_pct, source}] plus a median.
    A warning row only: no rule is changed from it."""
    out = []
    for t in sorted(theses, key=lambda t: (t['ticker'], t['as_of'])):
        rv = t['record'].get('review') or {}
        post = rv.get('post_review_base_today')
        pre = (rv.get('pre_review') or {}).get('base_today')
        src = 'pre-review params rerun at freeze'
        if pre is None and rv.get('review_findings'):
            pre, src = rv['review_findings'].get('pre_review_base_today'), 'review_findings.json'
            post = rv['review_findings'].get('final_base_today') or post
        if _num(pre) and _num(post) and pre:
            out.append({'ticker': t['ticker'], 'as_of': t['as_of'], 'thesis_id': t['thesis_id'], 'pre': pre, 'post': post,
                        'change': round(post - pre, 2), 'change_pct': post / pre - 1, 'source': src})
    pcts = [x['change_pct'] for x in out]
    return {'rows': out, 'median_change_pct': _median(pcts), 'n': len(out), 'companies': len({x['ticker'] for x in out}),
            'conclusion': TOO_FEW if len(out) < MIN_ROWS or len({x['ticker'] for x in out}) < MIN_COMPANIES else 'median over the theses',
            'meaning': "the reviewed base minus the first draft: a positive median means the first drafts ran low. Warning row only."}


# ------------------------------------------------------------------ summary

def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


ITEMS = (
    ('revenue_completed_year', 'הכנסות, שנה שהסתיימה (בפועל חלקי תחזית פחות 1)', 'pct', lambda r: ((r.get('completed') or {}).get('revenue') or {}).get('error')),
    ('margin_completed_year', 'שיעור רווח תפעולי, שנה שהסתיימה (נקודות)', 'pts', lambda r: ((r.get('completed') or {}).get('margin') or {}).get('diff_pts')),
    ('eps_completed_year', 'רווח תפעולי למניה, שנה שהסתיימה', 'pct', lambda r: ((r.get('completed') or {}).get('eps_operating') or {}).get('error')),
    ('rest_of_year_step', 'שנה חלקית: הקפיצה לרבעון שהמודל עוד צריך, מול הרבעון החדש', 'pct',
     lambda r: ((r.get('in_progress') or {}).get('revenue') or {}).get('step_per_quarter')),
    ('margin_new_part', 'שנה חלקית: שיעור הרווח של החלק החדש מול שנת המודל (נקודות)', 'pts',
     lambda r: ((r.get('in_progress') or {}).get('margin') or {}).get('diff_pts')),
)


def counted(r):
    """A row a summary may count: a test (frozen before the report), scored, not possibly in the valuation already."""
    return r.get('mode', 'test') == 'test' and r['status'] == 'scored' and not r.get('in_sample_suspected') and r['read'] != 'not_scorable'


def summarize(rows):
    """Median error per item with its count. Fewer than MIN_ROWS scored rows or MIN_COMPANIES companies: too few."""
    scored = [r for r in rows if counted(r)]
    cards = [r for r in rows if r['status'] == 'scorecard' and r.get('mode') == 'test']
    out = {'rows': len(rows), 'scored_rows': len(scored), 'scored_companies': len({r['ticker'] for r in scored}),
           'scorecard_tests': len(cards), 'descriptive_only': sum(r.get('mode') == 'descriptive' for r in rows),
           'no_report_since': sum(r['status'] == 'no_report_since' for r in rows),
           'custom_model': sum(r['status'] == 'custom_model' for r in rows),
           'report_not_scored': sum(r['status'] == 'report_not_scored' for r in rows),
           'not_scorable': sum(r['status'] == 'not_scorable' or (r['status'] == 'scored' and r['read'] == 'not_scorable') for r in rows),
           'in_sample_suspected': sum(bool(r.get('in_sample_suspected')) for r in rows), 'min_rows': MIN_ROWS,
           'min_companies': MIN_COMPANIES, 'items': {}}
    for key, he, unit, get in ITEMS:
        got = [(r['ticker'], get(r)) for r in scored]
        xs = [x for _, x in got if x is not None]
        comp = len({t for t, x in got if x is not None})
        out['items'][key] = {'label_he': he, 'unit': unit, 'n': len(xs), 'companies': comp, 'median': _median(xs),
                             'conclusion': TOO_FEW if len(xs) < MIN_ROWS or comp < MIN_COMPANIES else 'median over the scored rows'}
    px = [r for r in rows if r.get('price') and r.get('mode', 'test') == 'test']
    ages = [r['price']['age_days'] for r in px]
    young = bool(px) and _median(ages) < MIN_PRICE_AGE_DAYS
    comp = len({r['ticker'] for r in px})
    out['items']['price_vs_value_path'] = {
        'label_he': 'מחיר מול מסלול השווי הבסיסי', 'unit': 'pct', 'n': len(px), 'companies': comp,
        'median': _median([r['price']['vs_value_path'] for r in px]),
        'median_gap_at_valuation': _median([r['price']['gap_at_valuation'] for r in px]), 'median_age_days': _median(ages),
        'conclusion': TOO_FEW if len(px) < MIN_ROWS or comp < MIN_COMPANIES or young else 'median over the priced rows',
        'note': 'a gap in level between price and value, mostly the gap that existed on the valuation day; it tests the '
                f'path only as the valuations age (median age under {MIN_PRICE_AGE_DAYS} days: too early)' if young else None}
    out['conclusion'] = TOO_FEW if len(scored) < MIN_ROWS or out['scored_companies'] < MIN_COMPANIES else 'see the medians per item'
    return out


def falsifier_list(rows):
    """Every typed falsifier in the scorecards with its state, per company and report."""
    return [{'ticker': r['ticker'], 'period': r.get('period'), 'reported_on': r.get('reported_on'), 'mode': r.get('mode'),
             **{k: f.get(k) for k in ('id', 'text', 'state', 'note', 'action_if_fired', 'acted_on', 'reason_not_acted', 'on_file',
                                      'needs_reason')}}
            for r in rows if r['status'] == 'scorecard' for f in r.get('falsifiers') or []]


def build(rows, custom, as_of, sources=None, review=None, outcomes=None, ledger=None):
    """The tracker document: rows (one per company and reported period, plus the newest valuation without a report),
    the companies without any valuation the tracker can read, falsifiers, the review bias and the outcome rows."""
    rows = sorted(rows, key=lambda r: (r['ticker'], r.get('reported_on') or '9999', r.get('valuation_date') or ''))
    return {'forecast_tracker_version': TRACKER_VERSION, 'as_of': as_of, 'rows': rows,
            'custom_model': sorted(custom, key=lambda c: c['ticker']), 'summary': summarize(rows), 'falsifiers': falsifier_list(rows),
            'review_bias': review, 'outcomes': outcomes, 'ledger': ledger, 'sources': sources or {},
            'thresholds': {'revenue': REV_TOL, 'margin_pts': MARGIN_TOL, 'min_rows': MIN_ROWS, 'min_companies': MIN_COMPANIES},
            'meaning': 'Measurement only: frozen base cases (research/ledger.jsonl) against SEC-reported results, manual scorecards '
                       'and the latest close. No model or rule is changed from this file.'}


# ------------------------------------------------------------------ Hebrew output

def _p(x, signed=True, d=1):
    if x is None:
        return '—'
    s = f'{x * 100:+.{d}f}%' if signed else f'{x * 100:.{d}f}%'
    return '‎' + s


def _pts(x):
    return '—' if x is None else '‎' + f'{x * 100:+.1f}' + ' נק׳'


def _m(x):
    return '—' if x is None else f'{x / 1e6:,.0f}'


def _what(r):
    if r['status'] == 'no_report_since':
        what = 'אין דוח מאז'
        if r.get('annual_filer'):
            what = 'אין דוח שנתי מאז (ל-SEC החברה מגישה מספרים רק פעם בשנה: רבעון נוקד רק בכרטיס ניקוד ידני)'
        return what
    if r['status'] == 'scorecard':
        return f"{r.get('period')} (פורסם {r.get('reported_on')}), כרטיס ניקוד ידני"
    if r['status'] == 'report_not_scored':
        return f"{r.get('form')} מ-{r.get('reported_on')}: אין עדיין מספרים לניקוד"
    if r['status'] == 'custom_model':
        return f"דוח מ-{r.get('reported_on')}: מודל מותאם, צריך כרטיס ניקוד ידני"
    if r['status'] != 'scored':
        return 'לא נוקד: הנתונים ב-SEC לא במטבע של המודל או חסרים'
    last = r['reported'][-1]
    what = f"{SPAN_HE[last['period']]} של שנת {last['fiscal_year']} (עד {last['end']}, פורסם {last['reported_on']})"
    return what + (' (ייתכן שכבר היה בהערכה)' if r.get('in_sample_suspected') else '')


def _v(x, unit=None):
    if unit in ('ratio', 'pct'):
        return _p(x, False)
    return f'{x / 1e6:,.1f} מיליון' if abs(x) >= 1e6 else f'{x:,.2f}'


def _row_he(r):
    rv, mg = '—', '—'
    if r['status'] == 'scored':
        parts_r, parts_m = [], []
        c, ip = r.get('completed'), r.get('in_progress')
        if c and c['revenue']['error'] is not None:
            parts_r.append(f"שנה מלאה {c['fiscal_year']}: {c['revenue']['actual'] / 1e6:,.0f} מול {c['revenue']['forecast'] / 1e6:,.0f} מיליון ({_p(c['revenue']['error'])})")
        if c and c['margin']['diff_pts'] is not None:
            parts_m.append(f"שנה מלאה {c['fiscal_year']}: {_p(c['margin']['actual'], False)} מול {_p(c['margin']['forecast'], False)} ({_pts(c['margin']['diff_pts'])})")
        if ip:
            g, m = ip['revenue'], ip['margin']
            if g['share_of_remainder'] is not None:
                parts_r.append(f"החלק החדש {_m(g['new_part'])} מיליון = {_p(g['share_of_remainder'], False)} ממה שנשאר במודל לשנה "
                               f"({_m(g['remainder_before'])}; חלק הזמן {_p(g['time_share'], False, 0)})"
                               + (f"; כדי להגיע לשנת המודל צריך {_p(g['step_per_quarter'])} לרבעון מול הרבעון החדש"
                                  if g['step_per_quarter'] is not None else ''))
            elif g['new_part'] is not None:
                parts_r.append(f"החלק החדש {_m(g['new_part'])} מיליון; אין במודל הכנסות לשנה הזאת")
            if m['diff_pts'] is not None:
                parts_m.append(f"החלק החדש {_p(m['new_part'], False)} מול {_p(m['model_year'], False)} בשנת המודל ({_pts(m['diff_pts'])})")
            elif m['new_part'] is not None:
                parts_m.append(f"החלק החדש {_p(m['new_part'], False)}; אין במודל שיעור רווח לשנת הבסיס")
        rv, mg = '; '.join(parts_r) or '—', '; '.join(parts_m) or '—'
    elif r['status'] == 'scorecard':
        bits = []
        for it in sorted(r.get('items') or [], key=lambda i: {'partial': 0, 'actual': 1}.get(i.get('kind') or 'actual', 2)):
            if it.get('share') is not None:
                bits.append(f"{it['metric']}: {_v(it['actual'], it.get('unit'))} מול {_v(it['model_value'], it.get('unit'))} "
                            f"({_p(it['share'], False)} מהם, שנה חלקית)")
            elif it.get('error') is not None or it.get('diff_pts') is not None:
                d = _p(it['error']) if it.get('error') is not None else _pts(it['diff_pts'])
                tag = ' (תחזית החברה)' if it.get('kind') == 'guidance' else ''
                bits.append(f"{it['metric']}{tag}: {_v(it['actual'], it.get('unit'))} מול {_v(it['model_value'], it.get('unit'))} ({d})")
        rv = '; '.join(bits[:4]) or '—'
        mg = f"{sum(f.get('state') == 'fired' for f in r.get('falsifiers') or [])} תנאים הופעלו מתוך {len(r.get('falsifiers') or [])}"
    px = r.get('price')
    pr = (f"{px['price']:,.2f} מול {px['value_path']:,.2f} ({_p(px['vs_value_path'], d=0)}; ביום ההערכה {_p(px['gap_at_valuation'], d=0)})"
          if px else '—')
    mode = {'test': 'מבחן', 'descriptive': 'תיאור בלבד'}.get(r.get('mode', 'test'), '—')
    return (f"| {r['ticker']} | {r.get('valuation_date') or '—'} | {_what(r)} | {rv} | {mg} | {pr} | {mode} | "
            f"{READ_HE.get(r['read'], r['read'])} |")


def render_he(doc):
    s = doc['summary']
    lines = [f"# מעקב תחזיות: מה קרה מאז כל הערכת שווי ({doc['as_of']})", '',
             'הטבלה רק מודדת. היא לא משנה אף מודל ואף כלל. המספרים של ההערכות נלקחים מהיומן המוקפא (research/ledger.jsonl), '
             'לא מהרצה חדשה.', '',
             '| חברה | תאריך ההערכה | מה דווח מאז | הכנסות מול המסלול | שיעור רווח / תנאים | מחיר מול מסלול השווי | מעמד | קריאה |',
             '|---|---|---|---|---|---|---|---|']
    lines += [_row_he(r) for r in doc['rows']]
    lines.append('')
    desc = [r for r in doc['rows'] if r.get('mode') == 'descriptive' and r.get('descriptive_reason')]
    if desc:
        lines += ['## שורות של תיאור בלבד (לא נספרות)', '']
        lines += [f"- {r['ticker']} {r.get('reported_on') or r.get('valuation_date')}: {r['descriptive_reason']}" for r in desc]
        lines.append('')
    changed = [r for r in doc['rows'] if (r.get('file_check') or {}).get('note')]
    if changed:
        lines += ['## קבצים שהשתנו אחרי ההקפאה', '']
        lines += sorted({f"- {r['ticker']} {r.get('valuation_date')}: {r['file_check']['note']}" for r in changed})
        lines.append('')
    if doc.get('falsifiers'):
        lines += ['## תנאים ותנאי שבירה מכרטיסי הניקוד', '', '| חברה | דוח | תנאי | מצב | הערה | מעמד |', '|---|---|---|---|---|---|']
        for f in doc['falsifiers']:
            extra = ' (חסרה סיבה לאי-פעולה)' if f.get('needs_reason') else ''
            lines.append(f"| {f['ticker']} | {f.get('period')} | {f['id']}: {f['text']} | {STATE_HE.get(f['state'], f['state'])}{extra} | "
                         f"{f.get('note') or ''} | {'מבחן' if f.get('mode') == 'test' else 'תיאור בלבד'} |")
        lines.append('')
    custom = sorted({c['ticker'] for c in doc['custom_model']})
    if custom:
        lines.append(f"- חברות בלי הערכה מוקפאת או קבצים שהמעקב יכול לקרוא (ניקוד ידני): {', '.join(custom)}.")
    rb = doc.get('review_bias') or {}
    if rb.get('rows'):
        lines += ['', '## הטיוטה הראשונה מול הבסיס אחרי הבדיקה', '',
                  '| חברה | תאריך | בסיס לפני הבדיקה | בסיס אחרי | שינוי |', '|---|---|---|---|---|']
        lines += [f"| {x['ticker']} | {x['as_of']} | {x['pre']:,.2f} | {x['post']:,.2f} | {_p(x['change_pct'])} |" for x in rb['rows']]
        tail = ': מעט מדי כדי להסיק (too few to conclude)' if rb['conclusion'] == TOO_FEW else ''
        lines += ['', f"- חציון השינוי {_p(rb['median_change_pct'])} ({rb['n']} הערכות, {rb['companies']} חברות){tail}. "
                      'שורת אזהרה בלבד: שום כלל לא משתנה ממנה.']
    oc = doc.get('outcomes') or {}
    lines += ['', '## סיכום', '',
              f"- שורות: {s['rows']}; מבחנים שנוקדו: {s['scored_rows']} ({s['scored_companies']} חברות); כרטיסי ניקוד שהם מבחן: "
              f"{s['scorecard_tests']}; תיאור בלבד: {s['descriptive_only']}; בלי דוח מאז: {s['no_report_since']}; דוח בלי ניקוד: "
              f"{s['report_not_scored']}; מודל מותאם בלי כרטיס: {s['custom_model']}.",
              f"- תוצאות שנכתבו ליומן: {len(oc.get('written') or [])}; סורבו: {len(oc.get('refused') or [])}; כבר היו: {len(oc.get('existing') or [])}."]
    if s['scored_rows'] < s['min_rows'] or s['scored_companies'] < s['min_companies']:
        lines.append(f"- פחות מ-{s['min_rows']} מבחנים מנוקדים מלפחות {s['min_companies']} חברות: מעט מדי כדי להסיק מסקנה (too few to conclude).")
    for key, it in s['items'].items():
        if key == 'price_vs_value_path':
            continue
        med = '—' if it['median'] is None else (_p(it['median']) if it['unit'] == 'pct' else _pts(it['median']))
        tail = ': מעט מדי כדי להסיק (too few to conclude)' if it['conclusion'] == TOO_FEW else ''
        lines.append(f"- {it['label_he']}: חציון {med}, מספר שורות {it['n']} ({it['companies']} חברות){tail}.")
    it = s['items']['price_vs_value_path']
    if it['n']:
        tail = ('; ההערכות צעירות (גיל חציוני ' + f"{it['median_age_days']:.0f}" + ' ימים), לכן זה פער ברמה ולא מבחן של המסלול: '
                'מעט מדי כדי להסיק (too few to conclude)') if it['conclusion'] == TOO_FEW else ''
        lines.append(f"- {it['label_he']}: חציון {_p(it['median'], d=0)} (ביום ההערכה {_p(it['median_gap_at_valuation'], d=0)}), "
                     f"מספר שורות {it['n']} ({it['companies']} חברות){tail}.")
    lines += ['', '## איך לקרוא', '',
              '- מבחן: ההערכה הוקפאה ביומן לפני הדוח. תיאור בלבד: ההערכה הוקפאה אחרי הדוח או לא הוקפאה, ולכן היא לא נספרת.',
              '- שורה אחת לכל חברה ולכל דוח: ההערכה האחרונה שהוקפאה לפני הדוח. כמה גרסאות של אותה חברה לפני אותו דוח נספרות פעם אחת.',
              '- שנה שהסתיימה: משווים הכנסות, שיעור רווח תפעולי ורווח תפעולי למניה ישירות, עם קביעה (בקצב, מקדים, מפגר).',
              '- שנה חלקית: החלק החדש שדווח מול מה שנשאר בשנת המודל אחרי מה שכבר דווח לפני הדוח, והקפיצה לרבעון שהמודל עוד צריך. '
              'בלי קביעה של מקדים או מפגר, כי עונתיות לא נמדדת.',
              '- שיעור הרווח בפועל הוא לפי הדוח הרשמי (GAAP). במודל הוא אחרי תגמול במניות ולפעמים בלי סעיפים חד-פעמיים, '
              'לכן פער קטן יכול לנבוע מההגדרה.',
              '- חברות עם מודל מותאם, בנקים וחברות זרות בלי דוח רבעוני ב-XBRL: כרטיס ניקוד ידני (scorecard.json ליד ההערכה).',
              '- מחיר מול מסלול השווי: מחיר הסגירה האחרון חלקי השווי הבסיסי המוקפא לאותו יום. השווי הוא ערך לפי המודל, לא תחזית מחיר.',
              f"- סיכום נחשב רק מ-{MIN_ROWS} מבחנים לפחות מ-{MIN_COMPANIES} חברות לפחות (research/RULE_CHANGES_he.md).",
              '- המקור לנתונים בפועל: דיווחי SEC (companyfacts) ואינדקס ההגשות (8-K 2.02, 10-Q, 10-K, 6-K, 20-F, 40-F).']
    return '\n'.join(lines) + '\n'
