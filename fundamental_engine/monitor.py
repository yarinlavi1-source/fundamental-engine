"""Thesis records, re-check triggers, forecast freezing and forecast-vs-actual learning.

Why: a report written once and forgotten teaches nothing. A thesis record keeps, with dates and
hashes, what was known (FactBook and casefile sha256), what was assumed (assumptions with falsifiers),
what was forecast (per fiscal year, bear/base/bull envelope, unit and basis) and which observable
events must force a re-review. `check` compares later filings with that record and separates business
changes from price moves; only material deviations alert, each saying why it matters. `freeze` writes
the record and its forecasts into the append-only ledger with the system time, so `score` can later
say whether a miss was an analysis error (business outside the envelope), a valuation error (business
on track, realized return outside the expected band) or an external shock (only when an event with
evidence was recorded in the ledger before the result became public; never inferred, and an event
written afterwards is reported as a post-hoc explanation). The recurring-error report uses only
original forecasts the ledger proves were recorded on time (within 7 days of made_at) and before
their outcomes, needs several companies before it calls anything a pattern, never filters by which
stocks went up, and only suggests what to review: it does not tune any assumption, so the engine
cannot fit itself to winners or to hindsight.

The current fiscal year (the stub period the valuation starts with) is frozen too: revenue at the
revenue_current_fy anchor with the +/-3% band of its falsifier, plus revenue and margin triggers.
Growth thresholds always compare one fiscal year with the previous one (never a two-year CAGR).

Thresholds (DEFAULTS) are monitoring tolerances, not investment rules.
"""
from datetime import timedelta
from math import comb, sqrt
import json
from . import __version__
from . import facts as F
from .ledger import (Ledger, stamp, day, cmp, envelope, effective_outcomes, mismatch, observed, is_legacy, is_late,
                     recording_lag_days, LATE_RECORDING_DAYS)

DEFAULTS = {
    'fy_match_days': 12,            # 52/53-week fiscal years end on different calendar days
    'margin_tolerance': .01,        # below the bear margin by more than definition noise (model EBIT vs GAAP)
    'assumption_tolerance': .01,    # an assumption is breached only beyond its bear value by more than 1 point
    'fcf_margin_drop': .05,         # FCF margin fall from the analysis anchor that matters
    'share_growth_buffer': .02,     # dilution above the assumed share path, per year (min one year)
    'net_debt_increase_mcap': .10,  # net debt above the planned path by 10% of equity value at analysis
    'debt_issue_mcap': .05,         # net debt raised in one new period >= 5% of equity value at analysis
    'ttm_growth_shortfall': .03,    # provisional: trailing growth below the bear-year growth by 3 points
    'current_year_band': .03,       # current fiscal year revenue band: the revenue_current_fy falsifier (differs by > 3%)
    'irr_change': .0025,
    'review_max_days': 365, 'filing_lag_days': 75,
    'min_return_years': 1.0, 'return_noise': .10,   # realized-return noise allowance, scaled by 1/sqrt(years)
    'recurring_min_n': 5, 'recurring_min_entities': 3, 'recurring_max_p': .10,
    'recurring_min_rel': .03, 'recurring_min_pp': .01, 'overdue_days': 150,
}
IMPACTS = ('business_quality', 'value', 'expected_return', 'price_only')
RETURN_METRIC = 'total_return_annualized'
FINANCING_TOPICS = ('convertible', 'convert', 'debt', 'notes', 'offering', 'equity_issu', 'financing', 'atm', 'warrant', 'dilution')
ANNUAL_INPUTS = ('revenue', 'operating_income', 'gross_profit', 'cfo', 'capex', 'sbc', 'd_and_a', 'income_tax', 'pretax_income',
                 'debt_issued', 'debt_repaid', 'buybacks', 'dividends_paid')
INSTANT_INPUTS = ('shares_outstanding', 'shares_outstanding_cover', 'cash', 'short_term_investments', 'long_term_investments',
                  'long_term_debt', 'current_portion_ltd', 'short_term_borrowings', 'long_term_debt_tagged')
# Bases _fy_rows computes from filings; a forecast on another basis is never scored against them.
BASES = {'revenue': 'reported_revenue', 'ebit': 'operating_income_after_sbc', 'operating_margin': 'operating_income_after_sbc/revenue',
         'shares_outstanding': 'period_end_shares_outstanding', 'fcf_after_sbc': 'cfo_minus_capex_minus_sbc',
         'fcf_after_sbc_margin': 'cfo_minus_capex_minus_sbc/revenue', 'fcf': 'cfo_minus_capex', 'fcf_margin': 'cfo_minus_capex/revenue'}
# Assumption id (then topic) keywords -> a quantity with the same meaning observable in filings (first match wins).
# Valuation inputs are re-checked at re-review; the manual group has no single filed number with the same meaning.
ASSUMPTION_METRICS = (
    (('cost_of_equity', 'discount', 'wacc', 'terminal', 'multiple', 'risk_free', 'equity_risk'), 'valuation_review'),
    (('reinvest', 'claims', 'bridge', 'open_balance', 'investment_income', 'funding', 'payout', 'interest', 'deferred',
      'working_capital', 'prepaid', 'maintenance'), 'manual'),
    (('gross',), 'gross_margin'), (('sbc', 'stock_comp', 'share_based'), 'sbc_pct_revenue'),
    (('share', 'dilution'), 'share_count_growth'), (('capex', 'capital_intensity'), 'capex_pct_revenue'),
    (('fcf', 'free_cash'), 'fcf_margin'), (('cash_cost',), 'cash_cost_ratio'), (('depreciation',), 'd_and_a_pct_revenue'),
    (('tax',), 'tax_rate'), (('margin', 'operating'), 'operating_margin'), (('growth', 'revenue', 'demand'), 'revenue_growth'),
)
FAMILY = {'revenue': 'revenue', 'revenue_growth': 'revenue', 'operating_margin': 'margin', 'ebit': 'margin', 'fcf': 'fcf', 'fcf_after_sbc': 'fcf', 'cash_cost_ratio': 'margin',
          'gross_margin': 'margin', 'd_and_a_pct_revenue': 'margin', 'fcf_margin': 'fcf', 'fcf_after_sbc_margin': 'fcf',
          'capex_pct_revenue': 'fcf', 'share_count_growth': 'shares', 'shares_outstanding': 'shares', 'sbc_pct_revenue': 'shares',
          'net_debt': 'debt', 'tax_rate': 'tax'}
SCEN = ('bear', 'base', 'bull')
IMPACT_OF = {'revenue': ['value'], 'margin': ['business_quality', 'value'], 'fcf': ['business_quality', 'value'],
             'shares': ['value'], 'debt': ['value', 'business_quality'], 'tax': ['value']}
ONE_YEAR = (.9, 1.1)            # growth is only compared over about one fiscal year (52/53-week years included)


def _years(a, b):
    return (day(b) - day(a)).days / 365.25


def _div(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def _one_year(a, b):
    return bool(a and b) and ONE_YEAR[0] <= _years(a, b) <= ONE_YEAR[1]


def _order(b, m, u):
    return 'ascending' if b <= m <= u else 'descending' if b >= m >= u else 'crossing'


def _interp(path, d):
    """Linear interpolation of [[date, value], ...] at d, clamped at both ends."""
    pts = sorted((day(a), v) for a, v in path if v is not None)
    if not pts:
        return None
    t = day(d)
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0).days / max((t1 - t0).days, 1)
    return pts[-1][1]


def _irr(basis, price, at):
    """Annual rate equating the scenario's per-share distributions after `at` plus its terminal value to price.

    At the analysis price and date this returns the scenario's cost of equity whenever price equals the
    rolled-back value (valuation._roll_back), so it is the same arithmetic, not a second model."""
    if not basis or not price or price <= 0 or basis['terminal'][1] is None:
        return None
    flows = [(_years(at, d), x) for d, x in basis['flows'] if day(d) > day(at) and x]
    flows.append((_years(at, basis['terminal'][0]), basis['terminal'][1]))
    # financial assets are valued at face (valuation._roll_back): each period holds them at its start and returns them
    # grown at their own after-tax yield at its end; at Ke these pairs add exactly the roll-back credit
    for start, end, a, b in basis.get('financial') or []:
        if day(end) > day(at):
            flows += [(max(0., _years(at, start)), a), (_years(at, end), -b)]
    flows = [(t, x) for t, x in flows if t >= 0 and (t > 0 or x)]
    if not flows or sum(x for _, x in flows) <= 0:
        return None
    pv = lambda r: sum(x / (1 + r) ** t for t, x in flows) - price
    # the held-asset pairs make the value non-monotonic only at absurd rates: bracket from the lowest sensible bound
    lo, hi = next((x for x in (-.95, -.9, -.75, -.5) if pv(x) >= 0), -.5), 3.0
    if pv(lo) < 0 or pv(hi) > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if pv(mid) > 0 else (lo, mid)
    return (lo + hi) / 2


def _valuation(cf):
    v = cf.get('val') or {}
    res = v.get('result') if isinstance(v.get('result'), dict) else (v if 'annual_values' in v else None)
    return res, v.get('case') or (res or {}).get('reproduction_input')


FLOW_KEYS = ('revenue', 'ebitda_before_sbc', 'ebit', 'interest', 'cash_taxes', 'cfo', 'maintenance_capex', 'growth_capex',
             'fcfe_before_new_equity', 'investment_liquidation', 'investment_income_after_tax', 'noncontrolling_distributions',
             'new_financing_shares', 'sbc_shares', 'sbc_settled_in_shares', 'sbc_settlement_shares', 'dividends',
             'dividend_per_share', 'funding_gap')


def _merge_interim(rows, interim):
    """Fiscal-year rows: a valuation date inside a fiscal year (the fifth anniversary) splits it into two model
    periods; flows are summed back into one fiscal year, balances come from the year end."""
    out, i, rows = [], 0, list(rows or [])
    while i < len(rows):
        r = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if r.get('end') in interim and nxt and nxt.get('start') == r.get('end'):
            m = dict(nxt, start=r.get('start'), cash_open=r.get('cash_open', nxt.get('cash_open')), merged_interim=r['end'])
            for k in FLOW_KEYS:
                if isinstance(r.get(k), (int, float)) and isinstance(nxt.get(k), (int, float)):
                    m[k] = r[k] + nxt[k]
            seg = {}
            for x in (r.get('segments') or []) + (nxt.get('segments') or []):
                seg[x.get('name')] = seg.get(x.get('name'), 0) + (x.get('revenue') or 0)
            m['segments'] = [{'name': k, 'revenue': v} for k, v in seg.items()]
            out.append(m)
            i += 2
        else:
            out.append(r)
            i += 1
    return out


def _metric_defs(case):
    """(metric, unit, basis, f(model row), sided) for the executed forecast rows."""
    cur = case['currency']
    fcf = lambda r: r['cfo'] - r['maintenance_capex'] - r['growth_capex']
    defs = [('revenue', cur, BASES['revenue'], lambda r: r['revenue'], 'two'),
            ('ebit', cur, BASES['ebit'], lambda r: r['ebit'], 'two'),
            ('operating_margin', 'ratio', BASES['operating_margin'], lambda r: _div(r['ebit'], r['revenue']), 'two'),
            # model share counts carry no buybacks (excess cash is paid out), so only dilution above the range is a miss
            ('shares_outstanding', 'shares', BASES['shares_outstanding'], lambda r: r['shares'], 'upper')]
    pol = {s.get('sbc_policy') for s in case.get('scenarios', [])}
    if len(pol) == 1:
        name = 'fcf_after_sbc' if pol == {'cash_equivalent'} else 'fcf'   # model CFO is charged SBC only under cash_equivalent
        defs += [(name, cur, BASES[name], fcf, 'two'),
                 (name + '_margin', 'ratio', BASES[name + '_margin'], lambda r: _div(fcf(r), r['revenue']), 'two')]
    return defs


# ------------------------------------------------------------------ actuals from a FactBook (PIT accessors only)

def _near(series, end, tol):
    best = None
    for e, v, f in series:
        gap = abs((day(e) - day(end)).days)
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, e, v, f)
    return best[1:] if best else None


def _balance(ins, end, tol):
    g = lambda m: (_near(ins[m], end, tol) or (None, None, None))[1]
    ltd, cur, stb, tagged, cash = (g(m) for m in ('long_term_debt', 'current_portion_ltd', 'short_term_borrowings', 'long_term_debt_tagged', 'cash'))
    if ltd is not None:
        debt, method = ltd + (cur or 0) + (stb or 0), 'long_term_debt + current portion + short-term borrowings'
    elif tagged is not None:
        debt, method = tagged + (stb or 0), 'tagged long-term debt (scope ambiguous: may include the current portion)'
    elif cur is not None or stb is not None:
        debt, method = (cur or 0) + (stb or 0), 'current debt only'
    else:
        debt, method = 0.0, 'no debt facts: treated as zero (verify)'
    if cash is None:
        return {'debt': debt, 'net_debt': None, 'method': method}
    liquid = cash + (g('short_term_investments') or 0) + (g('long_term_investments') or 0)
    return {'debt': debt, 'liquid': liquid, 'net_debt': debt - liquid, 'method': method + '; minus cash and investments'}


def _fy_rows(fb, an=None, tol=12):
    """Fiscal-year actual rows (revenue-led) with the derived metrics the thesis monitors.

    Optional analysis output overlays resolved values (debt scope, share basis) for the same period."""
    ann = {m: F.series(fb, m, 'annual') for m in ANNUAL_INPUTS}
    ins = {m: F.series(fb, m, 'instant') for m in INSTANT_INPUTS}
    aa = (an or {}).get('annual') or {}
    rows = []
    for end, rev, fact in ann['revenue']:
        r = {'period_end': end, 'available_at': fact['available_at'], 'estimated': bool(fact.get('available_at_estimated')),
             'source_ids': [fact['source_id']], 'revenue': rev}
        for m in ANNUAL_INPUTS[1:]:
            x = _near(ann[m], end, tol)
            r[m] = x[1] if x else None
        for m in ('shares_outstanding', 'shares_outstanding_cover'):
            x = _near(ins[m], end, tol)
            r[m] = x[1] if x else None
        cover = r.pop('shares_outstanding_cover')
        if r['shares_outstanding'] is None:
            r['shares_outstanding'] = cover
        bal = _balance(ins, end, tol)
        r['net_debt'], r['net_debt_method'] = bal['net_debt'], bal['method']
        ak = next((k for k in aa if abs((day(k) - day(end)).days) <= tol), None)
        if ak:
            a = aa[ak]
            for m in ('operating_income', 'gross_profit', 'cfo', 'capex', 'sbc', 'd_and_a', 'shares_outstanding'):
                if a.get(m) is not None:
                    r[m] = a[m]
            if a.get('debt') is not None and a.get('cash') is not None:
                r['net_debt'] = a['debt'] - a['cash'] - (a.get('investments') or 0)
                r['net_debt_method'] = 'analysis.annual debt - cash - investments'
            r['source_ids'].append('analysis')
        capex = abs(r['capex']) if r['capex'] is not None else None
        r['ebit'] = r['operating_income']
        r['operating_margin'] = _div(r['operating_income'], rev)
        r['fcf'] = r['cfo'] - capex if r['cfo'] is not None and capex is not None else None
        r['fcf_after_sbc'] = r['fcf'] - r['sbc'] if r['fcf'] is not None and r['sbc'] is not None else None
        for m in ('fcf', 'fcf_after_sbc'):
            r[m + '_margin'] = _div(r[m], rev)
        r['gross_margin'] = _div(r['gross_profit'], rev)
        r['sbc_pct_revenue'] = _div(r['sbc'], rev)
        r['capex_pct_revenue'] = _div(capex, rev)
        r['d_and_a_pct_revenue'] = _div(r['d_and_a'], rev)
        parts = (r['operating_income'], r['sbc'], r['d_and_a'])
        r['cash_cost_ratio'] = 1 - (sum(parts) / rev) if rev and all(x is not None for x in parts) else None
        r['tax_rate'] = _div(r['income_tax'], r['pretax_income']) if (r['pretax_income'] or 0) > 0 else None
        prev = rows[-1] if rows and 330 <= (day(end) - day(rows[-1]['period_end'])).days <= 400 else None
        r['revenue_growth'] = _div(rev, prev['revenue']) - 1 if prev and prev['revenue'] else None
        r['share_count_growth'] = (_div(r['shares_outstanding'], prev['shares_outstanding']) - 1
                                   if prev and prev.get('shares_outstanding') and r['shares_outstanding'] else None)
        rows.append(r)
    return rows


def _latest_balance(fb, after, tol):
    """Latest balance-sheet readings first available after `after` (catches dilution and borrowing between annual reports)."""
    out = {}
    shares = [(e, v, m) for m in ('shares_outstanding', 'shares_outstanding_cover', 'shares_outstanding_vendor')
              for e, v, f in F.series(fb, m, 'instant') if day(f['available_at']) > day(after)]
    if shares:
        e, v, m = max(shares, key=lambda x: x[0])
        out['shares_outstanding'] = {'period_end': e, 'value': v, 'metric': m}
    cash = [(e, f) for e, v, f in F.series(fb, 'cash', 'instant') if day(f['available_at']) > day(after)]
    if cash:
        e = cash[-1][0]
        ins = {m: F.series(fb, m, 'instant') for m in INSTANT_INPUTS}
        bal = _balance(ins, e, 0)
        if bal['net_debt'] is not None:
            out['net_debt'] = {'period_end': e, 'value': bal['net_debt'], 'metric': bal['method']}
    return out


def _ttm_growth(fb, after):
    q = F.series(fb, 'revenue', 'quarter')
    if len(q) < 8 or day(q[-1][2]['available_at']) <= day(after):
        return None
    if not 600 <= (day(q[-1][0]) - day(q[-8][0])).days <= 760:
        return None
    now, prev = sum(x[1] for x in q[-4:]), sum(x[1] for x in q[-8:-4])
    return {'period_end': q[-1][0], 'growth': now / prev - 1 if prev else None, 'ttm': now, 'ttm_prior': prev,
            'formula': 'sum(last 4 quarters) / sum(prior 4 quarters) - 1'}


# ------------------------------------------------------------------ thesis record

def _numeric(v):
    if isinstance(v, dict) and 'path' in v:
        v = v['path']
    ok = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if ok(v):
        return float(v)
    return [float(x) for x in v] if isinstance(v, list) and v and all(ok(x) for x in v) else None


def _assumption_metric(a):
    for text in (str(a.get('id') or '').lower(), str(a.get('topic') or '').lower()):
        for keys, m in ASSUMPTION_METRICS:
            if any(k in text for k in keys):
                return m
    return None


def _assumption_values(a, raw):
    """{'bear','base','bull'} for one assumption: a contract triple, or forecast.py's per-scenario rows (<stem>_<scenario>) merged."""
    stem, _, suf = str(a.get('id') or '').rpartition('_')
    if stem and suf in SCEN:
        sib = {}
        for x in raw:
            s2, _, f2 = str(x.get('id') or '').rpartition('_')
            if s2 == stem and f2 in SCEN:
                sib[f2] = _numeric((x.get('values') or {}).get(f2))
        return sib, suf
    vals = a.get('values') or {}
    return {k: _numeric(vals[k]) for k in SCEN if k in vals}, None


def _direction(b, m):
    bs, ms = (b if isinstance(b, list) else None), (m if isinstance(m, list) else None)
    n = max(len(bs or [0]), len(ms or [0]))
    for i in range(n):
        x, y = (bs[min(i, len(bs) - 1)] if bs else b), (ms[min(i, len(ms) - 1)] if ms else m)
        if x < y - 1e-12:
            return '<'
        if x > y + 1e-12:
            return '>'
    return None


def _assumptions(fc, case, ends_all, ends_full, triggers, tol, current=None):
    """Thesis assumptions with falsifiers; those with an observable same-meaning metric get a bear-value trigger.

    Periods where bear equals base carry no range (e.g. the current-year starting margin) and get no threshold.
    The current-year revenue anchor is checked through its frozen forecast (below the band: revenue trigger;
    above it: above_bull_case), so its trigger names those instead of a second threshold."""
    out = []
    raw = fc.get('assumptions') or [dict(a, topic=a.get('id')) for a in (case or {}).get('assumptions', [])]
    for a in raw:
        metric = _assumption_metric(a)
        observable = metric not in (None, 'valuation_review', 'manual')
        vals, suf = _assumption_values(a, raw)
        b, m = vals.get('bear'), vals.get('base')
        trig = None
        if current and current.get('assumption_id') and a.get('id') == current['assumption_id']:
            e0 = current['period_end']
            metric, observable, m = 'revenue', True, current['base']
            trig = {'metric': 'revenue', 'op': 'outside', 'threshold': [current['low'], current['high']], 'tolerance': 0,
                    'period_kind': 'fiscal_year', 'period_end': e0, 'forecast_id': current['id'],
                    'evaluated_by': [f'revenue_below_bear@{e0}', 'above_bull_case']}
        elif observable and suf in (None, 'base') and b is not None and m is not None:
            op, thr = _direction(b, m), b
            if isinstance(b, list):
                ends = ends_all if len(b) == len(ends_all) else ends_full if len(b) == len(ends_full) else None
                ms = m if isinstance(m, list) else [m] * len(b)
                thr = [[e, v] for e, v, w in zip(ends, b, ms + [ms[-1]] * len(b)) if abs(v - w) > 1e-12] if ends else None
            if op and thr:
                trig = {'metric': metric, 'op': op, 'threshold': thr, 'tolerance': tol, 'period_kind': 'fiscal_year'}
        fam = FAMILY.get(metric) if observable else None
        linked = [t['id'] for t in triggers if fam and FAMILY.get(t['metric']) == fam]
        out.append({'id': a.get('id'), 'label': a.get('topic') or a.get('id'), 'value': m if suf in (None, 'base') else vals.get(suf),
                    'values': {k: v for k, v in vals.items() if v is not None} or None, 'kind': a.get('kind'),
                    'provenance': a.get('provenance'), 'rationale': a.get('rationale'), 'falsifier': a.get('falsifier'),
                    'source_ids': a.get('source_ids') or [], 'trigger': trig, 'linked_triggers': linked,
                    'monitor': 'filings' if trig or linked else metric if metric in ('valuation_review', 'manual') else 'manual'})
    return out


def _current_year(fc, sc, as_of, last_end, tol):
    """The fiscal year the valuation's stub period completes, with its revenue anchor, or None.

    Anchor, in order: the revenue_current_fy assumption (forecast.py; its falsifier is 'reported revenue
    differs by more than 3%'), forecast anchors.current_fiscal_year, or a driver spec's
    last_full_year_revenue when the spec has a stub (drivers.build_case grows the first full year from
    exactly that number). Nothing is guessed when the forecast's current year is another period."""
    rows = (sc.get('base') or {}).get('forecast') or []
    if not rows or ('start' in rows[0] and _years(rows[0]['start'], rows[0]['end']) >= .9) or day(rows[0]['end']) <= day(as_of):
        return None
    end = rows[0]['end']
    near = lambda e: isinstance(e, str) and abs((day(e) - day(end)).days) <= tol
    an = (fc.get('anchors') or {}).get('current_fiscal_year') or {}
    if an.get('end') and not near(an['end']):
        return None
    pos = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0
    a = next((x for x in fc.get('assumptions') or [] if x.get('id') == 'revenue_current_fy'), None)
    rev = ((a or {}).get('values') or {}).get('revenue')
    sp = fc.get('spec') or {}
    if pos(rev):
        found = (float(rev), 'revenue_current_fy assumption', 'revenue_current_fy')
    elif pos(an.get('revenue')):
        found = (float(an['revenue']), 'forecast anchors.current_fiscal_year', None)
    elif sp.get('stub_annual_revenue_rate') is not None and pos(sp.get('last_full_year_revenue')) and near((sp.get('boundaries') or [None])[0]):
        found = (float(sp['last_full_year_revenue']), 'driver spec last_full_year_revenue (the year the stub completes)', None)
    else:
        return None
    start = last_end if _one_year(last_end, end) else (day(end) - timedelta(days=364)).isoformat()
    return {'period_start': start, 'period_end': end, 'revenue': found[0], 'source': found[1], 'assumption_id': found[2]}


def thesis_from_casefile(cf, clock=None, options=None):
    """Dated thesis record from a pipeline casefile (pipeline.run). clock: callable for created_at (system time)."""
    o = {**DEFAULTS, **(options or {})}
    res, case = _valuation(cf)
    co = cf.get('company') or {}
    ticker = co.get('ticker') or (case or {}).get('ticker')
    if not ticker or not cf.get('as_of'):
        raise ValueError('casefile needs company.ticker and as_of')
    as_of = cf['as_of']
    cf_sha = cf.get('casefile_sha256') or F.digest(json.loads(json.dumps(cf, default=str)))
    created = stamp(clock() if clock else None)
    if day(created) < day(as_of):
        raise ValueError('a thesis cannot be created before its information cutoff (check clock and as_of)')
    tid = f'{ticker}:{as_of}:{cf_sha[:12]}'
    dc, card, an, fc, rv = ((cf.get(k) or {}) for k in ('dc', 'card', 'an', 'fc', 'rv'))
    currency = (case or {}).get('currency') or co.get('currency')
    limits = []
    q = (case or {}).get('quote') or {}
    av = (res or {}).get('annual_values') or []
    p0 = q.get('price') if q.get('price') is not None else (av[0].get('current_quote') if av else None)
    price_at = {'value': p0, 'as_of': q.get('as_of') or as_of, 'currency': q.get('currency') or currency,
                'source_ids': q.get('source_ids') or []} if p0 else None
    path = [{'date': r['date'], **{k: r.get(k) for k in ('bear', 'base', 'bull')}} for r in av]
    five = next((r for r in path if _years(as_of, r['date']) >= 4.98), None)
    if path and not five:
        limits.append('valuation report dates stop before the fifth anniversary; 5y value is the last date reported')
    values = {'today': path[0] if path else None, '5y': five or (path[-1] if path else None), 'five_year_reached': five is not None,
              'path': path, 'meaning': 'Conditional value per share at each date (valuation.value_company); not a price forecast.'}
    basis = {}
    for s in (res or {}).get('scenarios', []):
        rows, term = s.get('forecast') or [], s.get('terminal') or {}
        if s.get('status') != 'funding_blocked' and rows and 'end' in rows[-1] and term.get('value_per_share') is not None:
            basis[s['name']] = {'flows': [[r['end'], r.get('dividend_per_share') or 0] for r in rows],
                                'terminal': [rows[-1]['end'], term['value_per_share']],
                                'financial': [[r['start'], r['end'], r['financial_assets_open'] / r['shares_open'],
                                               (r['financial_assets_open'] + (r.get('investment_income_after_tax') or 0)) / r['shares_open']]
                                              for r in rows if (r.get('financial_assets_open') or 0) > 0 and r.get('shares_open')]}
    mon = {n: _irr(basis.get(n), p0, price_at['as_of']) if p0 else None for n in SCEN}
    er = dc.get('expected_return') or {}
    exp = {n: er.get(n + '_irr') for n in SCEN}
    if all(exp[n] is not None for n in SCEN):
        exp['source'] = 'decision.expected_return (implied cost of equity; terminal WACC moves with it)'
    else:
        exp = dict(mon, source='monitor: IRR of each scenario per-share distributions and terminal value at the analysis price')
    op = (case or {}).get('opening') or {}
    shares0 = op.get('shares')
    nd0 = op['debt'] - op.get('unrestricted_cash', 0) - op.get('marketable_investments', 0) if 'debt' in op else None
    ann = an.get('annual') or {}
    last_end = max((k for k in ann if day(k) <= day(as_of)), default=None, key=day)
    la = ann.get(last_end) or {}
    fcf = la.get('fcf') if la.get('fcf') is not None else (la['cfo'] - abs(la['capex']) if la.get('cfo') is not None and la.get('capex') is not None else None)
    fcfs = la.get('fcf_after_sbc') if la.get('fcf_after_sbc') is not None else (fcf - la['sbc'] if fcf is not None and la.get('sbc') is not None else None)
    if shares0 is None:
        shares0 = la.get('shares_outstanding')
    anchors = {'period_end': last_end, 'revenue': la.get('revenue'), 'operating_margin': _div(la.get('operating_income'), la.get('revenue')),
               'fcf_margin': _div(fcf, la.get('revenue')), 'fcf_after_sbc_margin': _div(fcfs, la.get('revenue')),
               'shares': shares0, 'net_debt': nd0, 'market_cap': p0 * shares0 if p0 and shares0 else None,
               'formula': 'last fiscal year in the casefile analysis; shares/net debt from the valuation opening balance; market cap = price x shares'}
    # ---- forecasts (fiscal-year rows of the executed case; the stub period stands for the current fiscal
    # year, frozen as its revenue anchor +/- the falsifier band; other short periods are not forecasts)
    forecasts, skipped, current, cur_margin = [], [], None, None
    sc = {s['name']: s for s in (res or {}).get('scenarios', [])}
    interim = set(((fc or {}).get('anchors') or {}).get('calendar', {}).get('interim') or ((fc or {}).get('spec') or {}).get('interim_boundaries') or [])
    if interim:
        sc = {n: dict(s, forecast=_merge_interim(s.get('forecast'), interim)) for n, s in sc.items()}
    ends_all = [r.get('end') for r in (sc.get('base') or {}).get('forecast') or []]
    if case and all(n in sc for n in ('bear', 'base', 'bull')):
        defs = _metric_defs(case)
        prev = {'end': last_end, **{n: la.get('revenue') for n in ('bear', 'base', 'bull')}} if la.get('revenue') else None
        cy = _current_year(fc, sc, as_of, last_end, o['fy_match_days'])
        if cy:
            r0, band, e0, unit = cy['revenue'], o['current_year_band'], cy['period_end'], case['currency']
            v = {'bear': r0 * (1 - band), 'base': r0, 'bull': r0 * (1 + band)}
            current = {'id': f"{tid}:revenue:{e0}", 'metric': 'revenue', 'period_start': cy['period_start'], 'period_end': e0,
                       'period_kind': 'fiscal_year', 'horizon_years': round(_years(as_of, e0), 3), 'value': r0, 'low': v['bear'],
                       'high': v['bull'], **v, 'unit': unit, 'currency': unit if unit == currency else None, 'basis': BASES['revenue'],
                       'scenario_order': 'ascending', 'sided': 'two', 'provenance': 'assumption', 'current_fiscal_year': True,
                       'assumption_id': cy['assumption_id'], 'anchor_source': cy['source'],
                       'formula': f"current fiscal year revenue anchor ({cy['source']}) +/- {band:.0%}, the band of the revenue_current_fy "
                                  'falsifier; bear/bull are the band edges, not model scenarios'}
            lr = la.get('revenue')
            current['growth'] = {n: v[n] / lr - 1 if lr and _one_year(last_end, e0) else None for n in ('bear', 'base', 'bull')}
            forecasts.append(current)
            prev = {'end': e0, **{n: r0 for n in ('bear', 'base', 'bull')}}   # every scenario grows from the same current year
            stub = {n: next((r for r in sc[n].get('forecast') or [] if r.get('end') == e0), None) for n in ('bear', 'base', 'bull')}
            if all(stub.values()):
                ms = {n: _div(stub[n].get('ebit'), stub[n].get('revenue')) for n in stub}
                cur_margin = ms if all(x is not None for x in ms.values()) else None
        for b in sc['base'].get('forecast') or []:
            if 'start' not in b or _years(b['start'], b['end']) < .9:
                if not (current and b.get('end') == current['period_end']):
                    skipped.append({'period_end': b.get('end'), 'reason': 'stub period (< 0.9 year) without a current fiscal-year revenue anchor'})
                continue
            rows = {n: next((r for r in sc[n].get('forecast') or [] if r.get('end') == b['end']), None) for n in ('bear', 'base', 'bull')}
            if any(r is None for r in rows.values()):
                skipped.append({'period_end': b['end'], 'reason': 'scenario periods do not align'})
                continue
            for metric, unit, bas, fn, sided in defs:
                try:
                    v = {n: fn(rows[n]) for n in rows}
                except (KeyError, TypeError):
                    continue
                if any(x is None for x in v.values()):
                    continue
                item = {'id': f"{tid}:{metric}:{b['end']}", 'metric': metric, 'period_start': b['start'], 'period_end': b['end'],
                        'period_kind': 'fiscal_year', 'horizon_years': round(_years(as_of, b['end']), 3), 'value': v['base'],
                        'low': min(v.values()), 'high': max(v.values()), **v, 'unit': unit,
                        'currency': unit if unit == currency else None, 'basis': bas, 'scenario_order': _order(v['bear'], v['base'], v['bull']),
                        'sided': sided, 'provenance': 'calculation',
                        'formula': f'valuation.value_company forecast row for the period ({bas}); low/high = envelope of bear/base/bull'}
                if metric == 'revenue' and prev:
                    one = _one_year(prev['end'], b['end'])      # never a multi-year CAGR: TTM growth is one year
                    item['growth'] = {n: v[n] / prev[n] - 1 if one and prev[n] else None for n in ('bear', 'base', 'bull')}
                    prev = {'end': b['end'], **v}
                forecasts.append(item)
    else:
        limits.append('no executed valuation with bear/base/bull: no business forecasts frozen; monitoring is event- and balance-sheet-based')
    if current:
        limits.append(f"current fiscal year ending {current['period_end']}: revenue frozen at the {current['anchor_source']} "
                      f"+/- {o['current_year_band']:.0%} (its falsifier band, not model scenarios); its operating margin is monitored "
                      'by a trigger, not frozen')
    if skipped:
        limits.append('stub periods without a current fiscal-year revenue anchor are not frozen; the first full fiscal year is the first scored forecast')
    if five and p0 and all(exp.get(n) is not None for n in ('bear', 'base', 'bull')):
        v = {n: exp[n] for n in ('bear', 'base', 'bull')}
        forecasts.append({'id': f"{tid}:{RETURN_METRIC}:{five['date']}", 'metric': RETURN_METRIC, 'period_start': price_at['as_of'],
                          'period_end': five['date'], 'period_kind': 'holding_period', 'horizon_years': round(_years(as_of, five['date']), 3),
                          'value': v['base'], 'low': min(v.values()), 'high': max(v.values()), **v, 'unit': 'ratio', 'currency': None,
                          'basis': 'annualized_total_return_from_analysis_price', 'scenario_order': _order(v['bear'], v['base'], v['bull']),
                          'sided': 'two', 'price': p0, 'provenance': 'calculation',
                          'formula': 'expected: scenario IRR at the analysis price; realized: ((price + dividends) / analysis price)^(1/years) - 1'})
    # ---- triggers
    by = {(f['metric'], f['period_end']): f for f in forecasts}
    ends = sorted({f['period_end'] for f in forecasts if f['period_kind'] == 'fiscal_year'})
    fm = next((f['metric'] for f in forecasts if f['metric'] in ('fcf_after_sbc_margin', 'fcf_margin')), None)
    trig = []
    for e in ends:
        r, m = by.get(('revenue', e)), by.get(('operating_margin', e))
        if r:
            t = {'id': f'revenue_below_bear@{e}', 'metric': 'revenue', 'op': '<', 'threshold': r['bear'], 'period_kind': 'fiscal_year',
                 'period_end': e, 'impact': ['value'], 'severity': 'high',
                 'why': 'Revenue below the bear path: even the slowest demand case was too optimistic, so every scenario value is overstated until re-underwritten.'}
            if r.get('current_fiscal_year'):
                t.update(current_fiscal_year=True, why=f"Current fiscal year revenue more than {o['current_year_band']:.0%} below its anchor "
                         '(the revenue_current_fy falsifier): every scenario grows from too high a starting revenue.')
            if r.get('assumption_id'):
                t['assumption_ids'] = [r['assumption_id']]
            trig.append(t)
        if m:
            trig.append({'id': f'operating_margin_below_bear@{e}', 'metric': 'operating_margin', 'op': '<', 'threshold': m['bear'] - o['margin_tolerance'],
                         'period_kind': 'fiscal_year', 'period_end': e, 'impact': ['business_quality', 'value'], 'severity': 'high',
                         'why': 'Operating margin below the bear case beyond definition noise: unit economics or pricing power are weaker than the worst case assumed.'})
        elif current and cur_margin and e == current['period_end']:
            trig.append({'id': f'operating_margin_below_bear@{e}', 'metric': 'operating_margin', 'op': '<',
                         'threshold': min(cur_margin.values()) - o['margin_tolerance'], 'period_kind': 'fiscal_year', 'period_end': e,
                         'impact': ['business_quality', 'value'], 'severity': 'high', 'current_fiscal_year': True,
                         'formula': 'lowest scenario operating margin of the stub period that completes the current fiscal year - margin tolerance',
                         'why': 'Current fiscal year operating margin below the lowest margin any scenario starts from, beyond definition noise: '
                                'every margin path starts too high.'})
        if fm and by.get((fm, e)):
            anc = anchors.get(fm)
            # a slower-growing bear reinvests less, so its FCF margin can sit ABOVE base in early years: the threshold never exceeds
            # the base path less the margin tolerance, so an on-track base year cannot trigger it
            cap_base = by[(fm, e)]['base'] - o['margin_tolerance'] if by[(fm, e)].get('base') is not None else float('inf')
            thr = min(anc - o['fcf_margin_drop'], by[(fm, e)]['bear'], cap_base) if anc is not None else min(by[(fm, e)]['bear'] - o['fcf_margin_drop'], cap_base)
            trig.append({'id': f'fcf_margin_drop@{e}', 'metric': fm, 'op': '<', 'threshold': thr, 'period_kind': 'fiscal_year', 'period_end': e,
                         'impact': ['business_quality', 'value'], 'severity': 'high',
                         'formula': 'min(anchor margin - drop, bear forecast margin, base forecast margin - tolerance)',
                         'why': f"FCF margin fell more than {o['fcf_margin_drop']:.0%} points from the analysis anchor and below the bear case: cash conversion is deteriorating, not just growth."})
    if shares0:
        sp = [[as_of, shares0]] + [[e, by[('shares_outstanding', e)]['base']] for e in ends if by.get(('shares_outstanding', e))]
        trig.append({'id': 'dilution_above_assumption', 'metric': 'shares_outstanding', 'op': '>', 'period_kind': 'balance_sheet',
                     'base_path': sp, 'buffer': {'type': 'compound', 'rate': o['share_growth_buffer'], 'min_years': 1},
                     'impact': ['value'], 'severity': 'high',
                     'why': f"Share count grew more than {o['share_growth_buffer']:.0%} a year above the assumed path: current holders own less of every future dollar."})
    if nd0 is not None and anchors['market_cap']:
        npth = [[as_of, nd0]]
        for e in ends:
            row = next((r for r in sc['base']['forecast'] if r.get('end') == e), {})
            if row.get('debt') is not None:
                npth.append([e, max(nd0, row['debt'] - row.get('cash', 0) - row.get('marketable_investments', 0))])
        trig.append({'id': 'net_debt_increase', 'metric': 'net_debt', 'op': '>', 'period_kind': 'balance_sheet', 'base_path': npth,
                     'buffer': {'type': 'additive', 'amount': o['net_debt_increase_mcap'] * anchors['market_cap']},
                     'impact': ['value', 'business_quality'], 'severity': 'high',
                     'why': f"Net debt rose above the planned path by more than {o['net_debt_increase_mcap']:.0%} of the equity value at analysis: claims ahead of shareholders changed."})
    tt = [[by[('revenue', e)]['period_start'], e, by[('revenue', e)]['growth']['bear'] - o['ttm_growth_shortfall']]
          for e in ends if by.get(('revenue', e)) and (by[('revenue', e)].get('growth') or {}).get('bear') is not None]
    if tt:
        trig.append({'id': 'revenue_ttm_growth_below_bear', 'metric': 'revenue_ttm_growth', 'op': '<', 'period_kind': 'ttm',
                     'threshold_by_period': tt, 'provisional': True, 'impact': ['value'], 'severity': 'medium',
                     'why': 'Trailing-four-quarter growth is running below the bear-year growth: an early signal before the annual report.'})
    trig += [{'id': 'guidance_change', 'metric': 'qualitative:guidance', 'op': 'new_item', 'period_kind': 'event', 'impact': ['value'], 'severity': 'high',
              'why': 'Management changed its own outlook: the forecast anchors may no longer hold.'},
             {'id': 'financing_event', 'metric': 'financing', 'op': 'new_item', 'period_kind': 'event', 'impact': ['value'], 'severity': 'high',
              'why': 'New debt, convertible or equity changes claims, dilution and the funding assumption of the thesis.'}]
    if path:
        trig.append({'id': 'price_outside_value_band', 'metric': 'price', 'op': 'outside', 'period_kind': 'market',
                     'threshold': 'bear..bull value per share interpolated at the price date', 'impact': ['expected_return'], 'severity': 'medium',
                     'why': 'Price left the bear..bull value band: expected return moved outside every scenario of the thesis.'})
    # the evidence pack's dated numeric falsifiers (OWNER_ADDENDUM item 2): frozen as triggers, due on their date
    for f in (cf.get('evidence_pack') or {}).get('falsifiers') or []:
        trig.append({'id': f"falsifier:{f['id']}", 'metric': f"analyst:{f['metric']}", 'op': '<' if f['direction'] == 'below' else '>',
                     'threshold': f['threshold'], 'period_kind': 'analyst_falsifier', 'by': f['by'], 'scenario': f['scenario'],
                     'evidence_ids': list(f.get('evidence_ids') or []), 'impact': ['value'], 'severity': 'high',
                     'why': f"Evidence-pack falsifier of the {f['scenario']} case: {f['metric']} {f['direction']} {f['threshold']:,} by "
                            f"{f['by']} means that scenario no longer holds."})
    full = [r.get('end') for r in (sc.get('base') or {}).get('forecast') or [] if 'start' in r and _years(r['start'], r['end']) >= .9]
    assumptions = _assumptions(fc, case, ends_all, full, trig, o['assumption_tolerance'], current)
    rb = day(as_of) + timedelta(days=o['review_max_days'] if ends else o['review_max_days'] // 2)
    if ends:
        rb = min(rb, day(ends[0]) + timedelta(days=o['filing_lag_days']))
    events = ([f'annual report for the fiscal year ending {ends[0]} (expected by {day(ends[0]) + timedelta(days=o["filing_lag_days"])})'] if ends else [])
    fals = sorted((t for t in trig if t['period_kind'] == 'analyst_falsifier'), key=lambda t: t['by'])
    if fals:
        rb = min(rb, day(fals[0]['by']))
    events += [f"{t['metric'][8:]} {t['op']} {t['threshold']:,} by {t['by']} ({t['scenario']} falsifier)" for t in fals]
    events += ['any guidance change', 'any debt, convertible or equity issuance', 'price outside the bear..bull value band',
               'any assumption falsifier observed', f'scheduled review by {rb.isoformat()}']
    verdict = dc.get('verdict') or {}
    return {'thesis_version': 1, 'monitor_version': __version__, 'thesis_id': tid, 'ticker': ticker, 'company_name': co.get('name'),
            'currency': currency, 'created_at': created, 'as_of': as_of,
            'factbook_sha256': cf.get('factbook_sha256') or cf.get('fb_sha256'), 'casefile_sha256': cf_sha,
            'engine_version': cf.get('engine_version'), 'verdict': verdict.get('code'),
            'verdict_detail': {k: verdict.get(k) for k in ('reason', 'what_would_change_it', 'price_for_hurdle')},
            'confidence': (dc.get('confidence') or {}).get('level'), 'review_status': rv.get('status'),
            'stage': (card.get('stage') or {}).get('value'), 'business_model': (card.get('business_model') or {}).get('primary'),
            'valuation_method': (card.get('valuation') or {}).get('method'), 'price_at_analysis': price_at, 'values': values,
            'expected_return': exp, 'irr_at_analysis': mon, 'irr_basis': basis, 'anchors': anchors, 'assumptions': assumptions, 'forecasts': forecasts,
            'triggers': trig, 'review_by': rb.isoformat(), 'review_events': events, 'skipped_periods': skipped,
            'current_fiscal_year': {**{k: current[k] for k in ('period_start', 'period_end', 'base', 'low', 'high', 'anchor_source', 'assumption_id')},
                                    'forecast_id': current['id'], 'band': o['current_year_band'], 'operating_margin_by_scenario': cur_margin}
                                   if current else None,
            'limitations': limits + ['valuation paths pay excess cash as distributions; buybacks show up as fewer actual shares, not as a miss',
                                     'value per share is conditional on the scenario; it is never scored as a price forecast']}


# ------------------------------------------------------------------ check new information against the thesis

def _fires(op, a, thr):
    return a < thr if op == '<' else a > thr


def _threshold(t, d, as_of):
    base, buf = _interp(t['base_path'], d), t['buffer']
    if base is None:
        return None
    if buf['type'] == 'compound':
        return base * (1 + buf['rate']) ** max(buf.get('min_years', 1), _years(as_of, d))
    return base + buf['amount']


def _change(f, a, r):
    pos = 'below' if a < f['low'] else 'above' if a > f['high'] else 'within'
    dev = {'vs_base_pp': a - f['base']} if f['unit'] == 'ratio' else {'vs_base_abs': a - f['base'], 'vs_base_rel': _div(a - f['base'], abs(f['base']))}
    return {'metric': f['metric'], 'period': f['period_end'], 'actual_period_end': r['period_end'], 'expected_range': [f['low'], f['high']],
            'base': f['base'], 'actual': a, 'deviation': dev, 'position': pos, 'unit': f['unit'], 'basis': f['basis'],
            'forecast_id': f['id'], 'source_ids': r['source_ids'], 'available_at': r['available_at'],
            'available_at_estimated': r['estimated']}


def _fmt(v, unit):
    if v is None:
        return 'n/a'
    if unit == 'ratio':
        return f'{v:.1%}'
    if unit == 'shares':
        return f'{v / 1e6:,.1f}M shares'
    return f'{v / 1e9:,.2f}B' if abs(v) >= 1e8 else f'{v:,.2f}'


def _price_view(thesis, pv, pd, o):
    p0 = (thesis.get('price_at_analysis') or {}).get('value')
    path = thesis['values']['path']
    band = {k: _interp([[r['date'], r[k]] for r in path], pd) for k in ('bear', 'base', 'bull')} if path else None
    irr = {n: _irr(thesis['irr_basis'].get(n), pv, pd) for n in ('bear', 'base', 'bull')}
    old = (thesis.get('irr_at_analysis') or {}).get('base')        # same IRR definition as `irr`, so the direction is meaningful
    dirn = None
    if irr['base'] is not None and old is not None:
        dirn = 'lower' if irr['base'] < old - o['irr_change'] else 'higher' if irr['base'] > old + o['irr_change'] else 'unchanged'
    out = {'price': pv, 'as_of': pd, 'price_at_analysis': p0, 'change': _div(pv, p0) - 1 if p0 else None, 'value_band': band,
           'implied_irr': irr, 'base_irr_at_analysis': old, 'irr_direction': dirn,
           'decision_base_irr_at_analysis': thesis['expected_return'].get('base'),
           'outside_band': bool(band and band['bear'] is not None and band['bull'] is not None
                                and not min(band['bear'], band['bull']) <= pv <= max(band['bear'], band['bull'])),
           'formula': 'band: bear/bull value path interpolated at the price date; IRR: same scenario flows as the thesis, new price and date'}
    ch = f"{out['change']:+.1%}" if out['change'] is not None else 'n/a'
    ir = f"base IRR {old:.1%} -> {irr['base']:.1%} ({dirn})" if dirn else 'implied IRR not computable'
    bd = f"value band {band['bear']:,.2f}..{band['bull']:,.2f}" if band and band['bear'] is not None else 'no value band'
    out['summary'] = f'Price {pv:,.2f} vs {p0:,.2f} at analysis ({ch}); {ir}; {bd}.' if p0 else f'Price {pv:,.2f}; {ir}; {bd}.'
    return out


def _new_data(fb, t0):
    new = {}
    for f in fb['facts']:
        if day(t0) < day(f['available_at']) <= day(fb['as_of']):
            x = new.setdefault((f['period'], f['period_end']), {'period': f['period'], 'period_end': f['period_end'],
                                                               'available_at': f['available_at'], 'metrics': 0})
            x['metrics'] += 1
            x['available_at'] = min(x['available_at'], f['available_at'])
    return {'periods': sorted(new.values(), key=lambda x: (x['period_end'], x['period'])),
            'qualitative_ids': [q['id'] for q in fb.get('qualitative') or [] if day(q['as_of']) > day(t0)],
            'ignored_not_yet_available': sum(day(f['available_at']) > day(fb['as_of']) for f in fb['facts'])}


def check(thesis, new_fb, new_an=None, price=None, options=None):
    """Compare a later FactBook (and optional analysis/price) with the thesis. Alerts only for material events.

    price: number or {'value','as_of'}; defaults to new_fb market price. Facts are read through the
    point-in-time accessors, so nothing published after new_fb['as_of'] is used."""
    o = {**DEFAULTS, **(options or {})}
    t0, now, tol = thesis['as_of'], new_fb['as_of'], o['fy_match_days']
    if day(now) < day(t0):
        raise ValueError('new FactBook predates the thesis cutoff: nothing to check')
    co = new_fb.get('company') or {}
    if co.get('ticker') != thesis['ticker']:
        raise ValueError('FactBook belongs to another company')
    if co.get('currency') != thesis['currency']:
        raise ValueError('reporting currency differs from the thesis: convert explicitly before checking')
    out = {'thesis_id': thesis['thesis_id'], 'as_of': now, 'new_data': _new_data(new_fb, t0), 'changes': [],
           'impact': {k: [] for k in IMPACTS}, 'alerts': [], 'confirmations': [], 'price': None, 'not_checked': []}
    alerts, business = {}, [False]

    def alert(code, period, message, t, **ev):
        key = (code, period)
        if key in alerts:
            return alerts[key]
        a = {'code': code, 'period': period, 'severity': t.get('severity', 'high'), 'message': message, 'why_it_matters': t['why'],
             'impact': t['impact'], 'evidence': ev, 'assumption_ids': list(t.get('assumption_ids') or []), 'provisional': bool(t.get('provisional')),
             'family': FAMILY.get(t.get('metric'))}
        alerts[key] = a
        out['alerts'].append(a)
        for k in t['impact']:
            out['impact'][k].append(message)
        if 'expected_return' not in t['impact'] or len(t['impact']) > 1:
            business[0] = True
        return a

    trig = thesis['triggers']
    fys = [r for r in _fy_rows(new_fb, new_an, tol) if day(r['available_at']) > day(t0)]
    fcs = [f for f in thesis['forecasts'] if f['period_kind'] == 'fiscal_year']
    for r in fys:
        e = r['period_end']
        for f in (f for f in fcs if abs((day(f['period_end']) - day(e)).days) <= tol):
            a = r.get(f['metric'])
            if a is None:
                out['not_checked'].append({'metric': f['metric'], 'period': f['period_end'], 'reason': 'actual not in the FactBook'})
                continue
            ch = _change(f, a, r)
            out['changes'].append(ch)
            label = f"FY ending {e} {f['metric']} {_fmt(a, f['unit'])}"
            rng = f"{_fmt(f['low'], f['unit'])}..{_fmt(f['high'], f['unit'])}"
            if ch['position'] == 'within':
                out['confirmations'].append(f'{label} inside the thesis range {rng}')
            elif ch['position'] == 'below' and f['sided'] == 'upper':
                out['confirmations'].append(f'{label} below the modeled range {rng}: fewer shares (buybacks), not a miss')
            elif ch['position'] == 'above' and f['metric'] in ('revenue', 'ebit', 'fcf', 'fcf_after_sbc'):
                cyf = bool(f.get('current_fiscal_year'))
                alert('above_bull_case', f['period_end'], f"{label} above the {'current-year band' if cyf else 'bull case'} {_fmt(f['high'], f['unit'])}",
                      {'severity': 'medium', 'impact': ['value'], 'metric': f['metric'],
                       'assumption_ids': [f['assumption_id']] if f.get('assumption_id') else [],
                       'why': ('Current fiscal year revenue is above its anchor band (the revenue_current_fy falsifier): every scenario grows '
                               'from too low a starting revenue; re-underwrite before acting.') if cyf else
                              'The business is running ahead of the most optimistic case: values and the verdict are stale on the upside; re-underwrite before acting.'},
                      actual=a, high=f['high'], source_ids=r['source_ids'])
        for t in trig:
            if t['period_kind'] == 'fiscal_year' and abs((day(t['period_end']) - day(e)).days) <= tol and r.get(t['metric']) is not None:
                a = r[t['metric']]
                if _fires(t['op'], a, t['threshold']):
                    alert(t['id'].split('@')[0], t['period_end'], f"FY ending {e}: {t['metric']} {_fmt(a, 'ratio' if 'margin' in t['metric'] else 'USD')} "
                          f"{t['op']} threshold {_fmt(t['threshold'], 'ratio' if 'margin' in t['metric'] else 'USD')}", t,
                          actual=a, threshold=t['threshold'], source_ids=r['source_ids'], available_at=r['available_at'])
    # balance-sheet triggers at each new fiscal year end and at the latest interim balance sheet
    latest = _latest_balance(new_fb, t0, tol)
    for t in (t for t in trig if t['period_kind'] == 'balance_sheet'):
        points = [(r['period_end'], r.get(t['metric']), r['source_ids']) for r in fys]
        lt = latest.get(t['metric'])
        if lt and not any(abs((day(lt['period_end']) - day(p[0])).days) <= tol for p in points):
            points.append((lt['period_end'], lt['value'], [lt['metric']]))
        for d, a, src in points:
            thr = _threshold(t, d, t0)
            if a is not None and thr is not None and _fires(t['op'], a, thr):
                unit = 'shares' if t['metric'] == 'shares_outstanding' else 'USD'
                alert(t['id'], d, f"{d}: {t['metric']} {_fmt(a, unit)} above threshold {_fmt(thr, unit)}", t, actual=a, threshold=thr,
                      source_ids=src, formula=t['buffer'])
    # assumption falsifiers that map to an observable quantity
    for asm in (x for x in thesis['assumptions'] if x.get('trigger') and not x['trigger'].get('evaluated_by')):
        t = asm['trigger']
        for r in fys:
            thr = t['threshold']
            if isinstance(thr, list):
                thr = next((v for e, v in thr if e and abs((day(e) - day(r['period_end'])).days) <= tol), None)
            a = r.get(t['metric'])
            if a is None or thr is None or not _fires(t['op'], a, thr - t.get('tolerance', 0) if t['op'] == '<' else thr + t.get('tolerance', 0)):
                continue
            fam = FAMILY.get(t['metric'])
            hit = next((x for x in out['alerts'] if x['family'] == fam and x['code'] != 'above_bull_case'
                        and abs((day(x['period']) - day(r['period_end'])).days) <= tol), None)
            if hit is None:
                hit = alert('assumption_breached', r['period_end'], f"FY ending {r['period_end']}: {asm['label']} actual {_fmt(a, 'ratio')} "
                            f"{t['op']} bear value {_fmt(thr, 'ratio')}",
                            {'severity': 'medium', 'impact': IMPACT_OF.get(fam, ['value']), 'metric': t['metric'],
                             'why': 'A thesis assumption moved beyond its own bear value; its falsifier: ' + str(asm.get('falsifier') or 'not stated')},
                            actual=a, threshold=thr, metric=t['metric'], source_ids=r['source_ids'])
            if asm['id'] not in hit['assumption_ids']:
                hit['assumption_ids'].append(asm['id'])
            out['changes'].append({'metric': t['metric'], 'period': r['period_end'], 'assumption_id': asm['id'], 'expected_range': None,
                                   'base': asm.get('value') if not isinstance(asm.get('value'), list) else None, 'actual': a,
                                   'deviation': {'vs_bear': a - thr}, 'position': 'beyond_bear', 'source_ids': r['source_ids']})
    tt = next((t for t in trig if t['period_kind'] == 'ttm'), None)
    g = _ttm_growth(new_fb, t0) if tt else None
    if g and g['growth'] is not None:
        win = next((w for w in tt['threshold_by_period'] if day(w[0]) < day(g['period_end']) <= day(w[1]) + timedelta(days=tol)), None)
        if win and g['growth'] < win[2]:
            alert(tt['id'], g['period_end'], f"TTM revenue growth {g['growth']:.1%} to {g['period_end']} below bear-year growth less "
                  f"{o['ttm_growth_shortfall']:.0%} ({win[2]:.1%})", tt, **g)
        elif win:
            out['confirmations'].append(f"TTM revenue growth {g['growth']:.1%} to {g['period_end']} not below the bear-year threshold {win[2]:.1%} (provisional)")
    # events: guidance and financing, dated after the thesis cutoff
    ev = {t['id']: t for t in trig if t['period_kind'] == 'event'}
    for q in new_fb.get('qualitative') or []:
        if day(q['as_of']) <= day(t0):
            continue
        topic = str(q.get('topic') or '').lower()
        if topic == 'guidance' or q.get('kind') == 'guidance':
            if str(q.get('direction') or '').lower() in ('reaffirmed', 'maintained'):
                out['confirmations'].append(f"guidance reaffirmed ({q['id']}, {q['as_of']})")
            else:
                alert('guidance_change', q['as_of'], f"Guidance update {q['id']} ({q['as_of']}): {q['statement']}", ev['guidance_change'],
                      item_id=q['id'], direction=q.get('direction') or 'not stated: read the source', source_ids=q.get('source_ids'))
        elif any(w in topic for w in FINANCING_TOPICS):
            alert('financing_event', q['as_of'], f"Financing item {q['id']} ({q['as_of']}): {q['statement']}", ev['financing_event'],
                  item_id=q['id'], source_ids=q.get('source_ids'))
    cs = new_fb.get('capital_structure') or {}
    for inst in (cs.get('debt') or []) + (cs.get('shares') or []):
        if inst.get('issue_date') and day(t0) < day(inst['issue_date']) <= day(now) and inst.get('kind') != 'common':
            alert('financing_event', inst['issue_date'], f"New {inst.get('kind')} instrument {inst['id']} issued {inst['issue_date']}",
                  ev['financing_event'], instrument=inst['id'], kind=inst.get('kind'), principal=inst.get('principal'), count=inst.get('count'))
    mcap = thesis['anchors'].get('market_cap')
    if mcap:
        for kind in ('quarter', 'annual'):
            iss = [(e, v, f) for e, v, f in F.series(new_fb, 'debt_issued', kind) if day(f['available_at']) > day(t0)]
            rep = {e: v for e, v, f in F.series(new_fb, 'debt_repaid', kind)}
            for e, v, f in iss:
                net = v - abs(rep.get(e) or 0)
                if net >= o['debt_issue_mcap'] * mcap:
                    alert('financing_event', e, f"Net debt raised {_fmt(net, 'USD')} in the period ending {e} (>= {o['debt_issue_mcap']:.0%} of equity value at analysis)",
                          ev['financing_event'], issued=v, repaid=rep.get(e), source_ids=[f['source_id']])
            if iss:
                break
    # price: report always, alert only outside the bear..bull band
    p = price if price is not None else (new_fb.get('market') or {}).get('price')
    pv, pd = (p.get('value'), p.get('as_of') or now) if isinstance(p, dict) else (p, now)
    if pv:
        if day(pd) < day(t0):
            raise ValueError('price dated before the thesis cutoff')
        pi = _price_view(thesis, float(pv), pd, o)
        out['price'] = pi
        if pi['outside_band']:
            side = 'below the bear' if pv < min(pi['value_band']['bear'], pi['value_band']['bull']) else 'above the bull'
            alert('price_outside_value_band', pd, f"Price {pv:,.2f} {side} value at {pd}. " + pi['summary'],
                  next(t for t in trig if t['id'] == 'price_outside_value_band'), band=pi['value_band'], implied_irr=pi['implied_irr'])
        if business[0]:
            out['impact']['expected_return'].append(pi['summary'] + ' Business inputs changed too: recompute the expected return after re-underwriting.')
        elif not pi['outside_band']:
            out['impact']['price_only'].append(pi['summary'] + ' No business change detected: no alert.')
    due = [t for t in trig if t.get('period_kind') == 'analyst_falsifier' and day(now) >= day(t['by'])]
    for t in due:
        out['not_checked'].append({'metric': t['metric'], 'period': t['by'], 'reason': 'evidence-pack falsifier due: compare the reported '
                                   f"value with {t['op']} {t['threshold']:,} (a free-text metric no FactBook field maps)"})
    reasons = [a['message'] for a in out['alerts']] + [f"falsifier {t['id']} due {t['by']}: check it" for t in due]
    if day(now) >= day(thesis['review_by']):
        reasons.append(f"scheduled review date {thesis['review_by']} reached")
    out['needs_rereview'] = bool(reasons)
    out['rereview_reasons'] = reasons
    out['meaning'] = ('Changes compare new filings with the frozen thesis ranges; alerts are limited to material events. '
                      'Price moves inside the value band are reported as price_only and do not alert.')
    return out


# ------------------------------------------------------------------ freeze into the ledger

def freeze(ledger, thesis, clock=None):
    """Write the thesis record and its forecasts with the system time; idempotent per thesis_id.

    Forecasts the ledger refuses (e.g. a period that has already ended) are reported, never forced. A thesis
    the ledger refuses (e.g. frozen more than the allowed lag after it was made) is reported with all its
    forecasts unwritten: a frozen record must be written when it is made."""
    L = ledger if isinstance(ledger, Ledger) else Ledger(ledger)
    if clock is not None:
        L = Ledger(L.path, clock=clock, max_recording_lag_days=L.max_recording_lag_days)
    rows = L.lines()
    tid = thesis['thesis_id']
    have = next((r for r in rows if r.get('type') == 'thesis' and r.get('thesis_id') == tid), None)
    made = have['made_at'] if have else thesis['created_at']
    written, skipped, rejected = [], [], []
    ids = {r.get('id') for r in rows}
    if have:
        skipped.append(tid)
    else:
        try:
            L.thesis(thesis_id=tid, entity=thesis['ticker'], made_at=made, model_version=f'monitor/{__version__}',
                     record={k: v for k, v in thesis.items() if k != 'irr_basis'} | {'thesis_sha256': F.digest(thesis)})
            written.append(tid)
        except ValueError as e:
            why = str(e)
            rejected = [{'id': tid, 'reason': why}] + [{'id': f['id'], 'reason': 'thesis record refused: ' + why}
                                                       for f in thesis['forecasts'] if f['id'] not in ids]
            return {'thesis_id': tid, 'made_at': made, 'written': written, 'skipped': skipped, 'rejected': rejected, 'ledger': L.verify()}
    fc_rows = [r for r in rows if r.get('type') == 'forecast']
    breakers = {}                    # falsifiers by metric family; the thesis entry keeps all of them
    for a in thesis['assumptions']:
        fam = FAMILY.get(_assumption_metric({'id': a.get('id'), 'topic': a.get('label')}))
        if fam and a.get('falsifier'):
            breakers.setdefault(fam, []).append(a['falsifier'])
    for f in thesis['forecasts']:
        if f['id'] in ids:
            skipped.append(f['id'])
            continue
        prior = [r for r in fc_rows if r['entity'] == thesis['ticker'] and r['metric'] == f['metric'] and r.get('thesis_id') != tid
                 and abs((day(r['period_end']) - day(f['period_end'])).days) <= DEFAULTS['fy_match_days']]
        ctx = {'thesis_id': tid, 'stage': thesis.get('stage'), 'business_model': thesis.get('business_model'), 'verdict': thesis.get('verdict'),
               'valuation_method': thesis.get('valuation_method'), 'horizon_years': f['horizon_years'], 'sided': f['sided'],
               'period_start': f['period_start'], 'period_kind': f['period_kind'], 'vintage': 'revision' if prior else 'original'}
        if f.get('current_fiscal_year'):
            ctx.update(current_fiscal_year=True, assumption_id=f.get('assumption_id'), anchor_source=f.get('anchor_source'))
        try:
            L.forecast(id=f['id'], entity=thesis['ticker'], made_at=made, metric=f['metric'], period_end=f['period_end'],
                       scenarios={k: f[k] for k in ('bear', 'base', 'bull')}, unit=f['unit'], currency=f['currency'], basis=f['basis'],
                       model_version=f'monitor/{__version__}', price=f.get('price'), inputs_hash=thesis['casefile_sha256'],
                       thesis_breakers=breakers.get(FAMILY.get(f['metric']), []), scenario_order=f['scenario_order'], thesis_id=tid,
                       supersedes=prior[-1]['id'] if prior else None, context=ctx,
                       note='scenario paths cross for this metric' if f['scenario_order'] == 'crossing' else '')
            written.append(f['id'])
        except ValueError as e:
            rejected.append({'id': f['id'], 'reason': str(e)})
    return {'thesis_id': tid, 'made_at': made, 'written': written, 'skipped': skipped, 'rejected': rejected, 'ledger': L.verify()}


# ------------------------------------------------------------------ score forecasts against actuals

def _factbooks(actuals):
    if not actuals:
        return {}
    if isinstance(actuals, dict) and 'facts' in actuals:
        actuals = [actuals]
    elif isinstance(actuals, dict):
        fbs = actuals.get('factbooks') or []
        actuals = list(fbs.values()) if isinstance(fbs, dict) else fbs
    return {fb['company']['ticker']: fb for fb in actuals if isinstance(fb, dict) and 'facts' in fb}


def _from_fb(fb, rows, f, tol):
    if BASES.get(f['metric']) != f.get('basis'):
        return None, 'basis cannot be derived from a FactBook'
    if f['unit'] not in ('ratio', 'shares') and fb['company'].get('currency') != f.get('currency'):
        return None, 'currency differs between forecast and FactBook'
    r = next((r for r in rows if abs((day(r['period_end']) - day(f['period_end'])).days) <= tol), None)
    if r is None or r.get(f['metric']) is None:
        return None, None
    return {'value': r[f['metric']], 'observed_at': r['available_at'], 'source': 'factbook', 'source_ids': r['source_ids'],
            'corrected': False}, None


def _sign_p(k, n):
    """Two-sided exact sign test (p=0.5)."""
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return None if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def _stats(its):
    errs = [i['error'] for i in its if i['error'] is not None]
    over, under = sum(e < 0 for e in errs), sum(e > 0 for e in errs)
    return {'n': len(errs), 'entities': len({i['entity'] for i in its}), 'median_error': _median(errs),
            'mean_error': sum(errs) / len(errs) if errs else None, 'over_forecast': over, 'under_forecast': under,
            'sign_test_p': _sign_p(over, over + under), 'coverage': sum(i['within'] for i in its) / len(its) if its else None}


def _bucket(h):
    return None if h is None else '<=1y' if h <= 1.05 else '1-2y' if h <= 2.05 else '2-3y' if h <= 3.05 else '>3y'


def _recurring(items, o):
    elig = [i for i in items if i['eligible_for_learning']]
    excluded = {}
    for i in items:
        if not i['eligible_for_learning']:
            excluded[i['learning_exclusion']] = excluded.get(i['learning_exclusion'], 0) + 1
    groups = {}
    for i in elig:
        for dims in (('metric',), ('metric', 'stage'), ('metric', 'business_model'), ('metric', 'horizon'), ('metric', 'verdict')):
            key = tuple((d, _bucket(i['horizon_years']) if d == 'horizon' else i.get(d)) for d in dims)
            if all(v is not None for _, v in key):
                groups.setdefault(key, []).append(i)
    out = []
    for key, its in groups.items():
        s = _stats(its)
        kind = its[0]['error_kind']
        thr = o['recurring_min_pp'] if kind == 'pp' else o['recurring_min_rel']
        if s['n'] < o['recurring_min_n'] or s['entities'] < o['recurring_min_entities']:
            status = 'insufficient_sample'
        elif s['sign_test_p'] <= o['recurring_max_p'] and abs(s['median_error']) >= thr:
            status = 'recurring_over_forecast' if s['median_error'] < 0 else 'recurring_under_forecast'
        else:
            status = 'no_pattern'
        g = {'group': dict(key), 'error_kind': kind, **s, 'status': status,
             'excluding_external_shocks': _stats([i for i in its if i['attribution'] != 'external_shock'])}
        if status.startswith('recurring'):
            fmt = (lambda x: f'{x:+.1%} pts') if kind == 'pp' else (lambda x: f'{x:+.1%}')
            g['review_hint'] = (f"{dict(key)}: actual {'below' if s['median_error'] < 0 else 'above'} base in "
                                f"{max(s['over_forecast'], s['under_forecast'])}/{s['over_forecast'] + s['under_forecast']} forecasts "
                                f"(median {fmt(s['median_error'])}, {s['entities']} companies). Review how this assumption is set for this group; "
                                'do not apply a mechanical haircut fitted to this sample.')
        out.append(g)
    out.sort(key=lambda g: (not g['status'].startswith('recurring'), g['sign_test_p'], str(g['group'])))
    return {'eligible': len(elig), 'excluded': excluded, 'groups': out, 'recurring': [g for g in out if g['status'].startswith('recurring')],
            'rules': [f"only original (non-revised) forecasts recorded by the ledger before the actual existed and within {LATE_RECORDING_DAYS} "
                      'days of made_at; legacy and late recordings excluded',
                      f"a pattern needs >= {o['recurring_min_n']} forecasts from >= {o['recurring_min_entities']} companies, sign-test p <= "
                      f"{o['recurring_max_p']} and a median error of at least {o['recurring_min_rel']:.0%} ({o['recurring_min_pp']:.0%} points for ratios)",
                      'groups are by metric and ex-ante labels (stage, business model, horizon, verdict) only; never by realized stock return',
                      'external-shock attribution does not remove an error from the pattern statistics (shown separately)',
                      'output is a review hint; no assumption is adjusted automatically']}


def score(ledger, actuals=None, options=None):
    """Forecast-vs-actual scoring with attribution and a recurring-error report.

    ledger: Ledger, path or list of ledger rows. actuals (optional): a FactBook, a list of FactBooks, or
    {'factbooks': [...], 'prices': [{'entity','date','price','dividends_per_share'?: cumulative since the
    analysis price,'benchmark_return'?}]}. Ledger outcomes take precedence over FactBook-derived actuals."""
    o = {**DEFAULTS, **(options or {})}
    rows = ledger.lines() if isinstance(ledger, Ledger) else Ledger(ledger).lines() if isinstance(ledger, str) else list(ledger)
    tol = o['fy_match_days']
    heads, counts = effective_outcomes(rows)
    shocks = [r for r in rows if r.get('type') == 'event' and r.get('kind') == 'external_shock' and r.get('evidence_ids')]
    fbs = _factbooks(actuals)
    fy_rows = {k: _fy_rows(fb, None, tol) for k, fb in fbs.items()}
    prices = (actuals.get('prices') or []) if isinstance(actuals, dict) and 'facts' not in actuals else []
    eval_dates = [fb['as_of'] for fb in fbs.values()] + [p['date'] for p in prices] + [r['recorded_at'] for r in rows if r.get('recorded_at')]
    eval_at = o.get('as_of') or (max(eval_dates, key=day) if eval_dates else None)
    items, not_scored, unresolved = [], [], []
    fcs = [r for r in rows if r.get('type') == 'forecast']
    for f in fcs:
        ctx = f.get('context') or {}
        act, why = None, None
        if f['id'] in heads:
            oc = heads[f['id']]
            why = mismatch(f, oc)
            if not why:
                act = {'value': oc['actual'], 'observed_at': observed(oc), 'source': 'ledger', 'source_ids': [oc.get('source_id')],
                       'corrected': counts[f['id']] > 1}
        elif f['entity'] in fbs and f['metric'] != RETURN_METRIC:
            act, why = _from_fb(fbs[f['entity']], fy_rows[f['entity']], f, tol)
        if why:
            not_scored.append({'id': f['id'], 'reason': why})
            continue
        if act is None:
            overdue = bool(eval_at) and (day(eval_at) - day(f['period_end'])).days > o['overdue_days']
            unresolved.append({'id': f['id'], 'entity': f['entity'], 'metric': f['metric'], 'period_end': f['period_end'], 'overdue': overdue})
            continue
        s, a = f['scenarios'], act['value']
        lo, hi = envelope(s)
        sided = ctx.get('sided', 'two')
        pos = 'below' if a < lo else 'above' if a > hi else 'within'
        within = pos == 'within' or (sided == 'upper' and pos == 'below')
        ratio = f.get('unit') == 'ratio'
        err = (a - s['base']) if ratio else _div(a - s['base'], abs(s['base']))
        legacy, late_rec = is_legacy(f), is_late(f)
        pre = (not legacy) and bool(f.get('recorded_at')) and cmp(f['recorded_at'], act['observed_at']) < 0
        ev, post = [], []
        if f['metric'] == RETURN_METRIC:
            attr = 'return_within_range' if within else 'return_outside_range'
        elif within:
            attr = 'within_range'
        else:
            hits = [e for e in shocks if e['entity'] == f['entity'] and (not e.get('affects') or f['metric'] in e['affects'] or f['id'] in e['affects'])
                    and cmp(e['occurred_at'], f['made_at']) > 0 and day(e['occurred_at']) <= day(f['period_end'])]
            # only an event on file before the result became public can explain the miss; one written later is post-hoc
            ev = [e['id'] for e in hits if not is_legacy(e) and e.get('recorded_at') and cmp(e['recorded_at'], act['observed_at']) < 0]
            post = [e['id'] for e in hits if e['id'] not in ev]
            attr = 'external_shock' if ev else 'analysis_error'
        excl = ('legacy' if legacy else 'late_recording' if late_rec else 'not_recorded_before_outcome' if not pre
                else 'revision' if f.get('supersedes') or ctx.get('vintage') == 'revision' else None)
        items.append({'forecast_id': f['id'], 'thesis_id': f.get('thesis_id') or ctx.get('thesis_id'), 'entity': f['entity'], 'metric': f['metric'],
                      'period_end': f['period_end'], 'made_at': f['made_at'], 'recorded_at': f.get('recorded_at'), 'horizon_years': ctx.get('horizon_years'),
                      'stage': ctx.get('stage'), 'business_model': ctx.get('business_model'), 'verdict': ctx.get('verdict'),
                      'base': s['base'], 'low': lo, 'high': hi, 'actual': a, 'position': pos, 'within': within, 'sided': sided,
                      'error': err, 'error_kind': 'pp' if ratio else 'relative', 'abs_error': a - s['base'], 'unit': f.get('unit'),
                      'basis': f.get('basis'), 'outcome_source': act['source'], 'observed_at': act['observed_at'], 'corrected': act['corrected'],
                      'attribution': attr, 'external_events': ev, 'post_hoc_explanations': post, 'events_recorded_after_outcome': post,
                      'legacy': legacy, 'late_recording': late_rec, 'recording_lag_days': recording_lag_days(f), 'recorded_before_outcome': pre,
                      'eligible_for_learning': excl is None, 'learning_exclusion': excl})
    # metric aggregates (late recordings never count toward coverage or error)
    by_metric = {}
    for m in sorted({i['metric'] for i in items}):
        every = [i for i in items if i['metric'] == m]
        its = [i for i in every if not i['late_recording']]
        s = _stats(its)
        by_metric[m] = {**s, 'error_kind': every[0]['error_kind'], 'median_abs_error': _median([abs(i['error']) for i in its if i['error'] is not None]),
                        'excluded_late_recordings': len(every) - len(its)}
    # thesis-level attribution
    theses = {r['thesis_id']: r for r in rows if r.get('type') == 'thesis'}
    ret_fc = {r.get('thesis_id'): r for r in fcs if r['metric'] == RETURN_METRIC}
    by_thesis = {}
    for tid in sorted({i['thesis_id'] for i in items if i['thesis_id']} | set(theses) | {k for k in ret_fc if k}):
        every = [i for i in items if i['thesis_id'] == tid]
        late_ids = [i['forecast_id'] for i in every if i['late_recording']]
        its = [i for i in every if not i['late_recording']]       # late recordings: no attribution, no pick return
        biz = [i for i in its if i['metric'] != RETURN_METRIC]
        rf = ret_fc.get(tid)
        rf_late = bool(rf) and is_late(rf)
        if rf_late and rf['id'] not in late_ids:
            late_ids.append(rf['id'])
        rec = (theses.get(tid) or {}).get('record') or {}
        entity = (theses.get(tid) or rf or (every[0] if every else {})).get('entity')
        ret = None
        final = next((i for i in its if i['metric'] == RETURN_METRIC), None)
        if rf and rf_late:
            pass
        elif rf and final:
            ret = {'realized': final['actual'], 'years': rf['context'].get('horizon_years'), 'final': True, 'date': final['observed_at']}
        elif rf and rf.get('price'):
            p0_date = (rec.get('price_at_analysis') or {}).get('as_of') or rf['context'].get('period_start') or rf['made_at']
            obs = sorted((p for p in prices if p.get('entity') == entity and day(p['date']) > day(p0_date)), key=lambda p: day(p['date']))
            if obs:
                p = obs[-1]
                yrs = _years(p0_date, p['date'])
                wealth = (p['price'] + (p.get('dividends_per_share') or 0)) / rf['price']
                ret = {'realized': wealth ** (1 / yrs) - 1 if yrs > 0 and wealth > 0 else None, 'years': yrs, 'final': False,
                       'date': p['date'], 'benchmark_return': p.get('benchmark_return'),
                       'formula': '((price + cumulative dividends) / analysis price)^(1/years) - 1'}
        if ret and rf:
            lo, hi = envelope(rf['scenarios'])
            ret['expected'] = {'bear': rf['scenarios']['bear'], 'base': rf['scenarios']['base'], 'bull': rf['scenarios']['bull']}
            noise = o['return_noise'] / sqrt(max(ret['years'] or 0, o['min_return_years']))
            ret['assessable'] = (ret['years'] or 0) >= o['min_return_years'] and ret['realized'] is not None
            ret['diverged'] = ret['assessable'] and not lo - noise <= ret['realized'] <= hi + noise
            ret['noise_allowance'] = noise
        errs = [i['forecast_id'] for i in biz if i['attribution'] == 'analysis_error']
        shock = [i['forecast_id'] for i in biz if i['attribution'] == 'external_shock']
        post = sorted({x for i in biz for x in i['post_hoc_explanations']})
        if errs:
            attr = 'analysis_error'
            why = f'{len(errs)} business forecast(s) outside the bear..bull envelope without an external event recorded before the result.'
            if post:
                why += (f" {len(post)} event(s) recorded after the result became public ({', '.join(post)}) are post-hoc explanations "
                        'and do not change the attribution.')
        elif shock:
            attr = 'external_shock'
            why = ('Business misses coincide with external events (with evidence) that occurred after the forecast and were recorded '
                   'before the result became public.')
        elif not biz and late_ids:
            attr = 'not_assessable_late_recording'
            why = (f'Forecasts recorded more than {LATE_RECORDING_DAYS} days after their made_at date: hindsight cannot be ruled out, '
                   'so they are not used for attribution, returns or learning.')
        elif not biz:
            attr, why = 'not_assessable_yet', 'No business forecast resolved yet.'
        elif ret and ret['diverged']:
            attr = 'valuation_error'
            why = (f"Business forecasts landed inside the envelope but the realized return {ret['realized']:.1%}/yr is outside the expected "
                   f"{min(ret['expected'].values()):.1%}..{max(ret['expected'].values()):.1%} band (noise allowance {ret['noise_allowance']:.1%}): "
                   f"the value or multiple, not the business, was misjudged{'' if ret['final'] else ' (provisional: horizon not reached)'}.")
        elif ret and ret['assessable']:
            attr, why = 'on_track', 'Business inside the envelope and realized return inside the expected band.'
        else:
            attr, why = 'business_on_track_return_not_assessable', 'Business inside the envelope; no return observation of at least one year yet.'
        by_thesis[tid] = {'thesis_id': tid, 'entity': entity, 'verdict': rec.get('verdict'), 'n_business': len(biz),
                          'n_outside': sum(not i['within'] for i in biz), 'analysis_errors': errs, 'external_shocks': shock,
                          'post_hoc_explanations': post, 'late_recordings': late_ids,
                          'return': ret, 'attribution': attr, 'explanation': why,
                          'provisional': bool(ret and not ret['final'])}
    rec_report = _recurring(items, o)
    overdue = [u for u in unresolved if u['overdue']]
    warn = []
    if overdue and len(overdue) / (len(overdue) + len(items)) > .2:
        warn.append(f'{len(overdue)} forecasts are past their period end by more than {o["overdue_days"]} days without an outcome: '
                    'resolve them (including delisted or abandoned names) before trusting any pattern; missing failures bias the sample.')
    rec_report['warnings'] = warn
    return {'items': items, 'by_metric': by_metric, 'by_thesis': by_thesis, 'recurring_errors': rec_report,
            'unresolved': unresolved, 'not_scored': not_scored, 'evaluated_at': eval_at,
            'limitations': ['Errors are descriptive; a small sample says little about skill.',
                            'external_shock requires a ledger event with evidence that occurred after the forecast and was recorded before '
                            'the result became public; it is never inferred, and later events are listed as post-hoc explanations only.',
                            f'Forecasts recorded more than {LATE_RECORDING_DAYS} days after made_at are excluded from coverage, returns and learning.',
                            'valuation_error compares realized return with the expected IRR band; before the horizon it is provisional and noisy.',
                            'No win rate or accuracy guarantee is claimed.']}
