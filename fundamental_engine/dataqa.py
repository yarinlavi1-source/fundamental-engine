"""Stage 1 data validation: may a price conclusion rest on these numbers?

Why: the engine's costliest errors were data errors, not model errors. NBIS used a stale share count
and missed converts and pre-funded warrants; ASTS counted net cash twice; NOW's 1:5 split showed up as
394% "dilution"; APH debt existed only as a fair-value tag; MSFT/META net cash read +36.5B in one
source and -52B in another because one side counted leases and long-term investments. Definitions
therefore matter as much as numbers. Every critical field is resolved here with an explicit
definition, a dated source, the checks that confirm it and a status:

  verified       two independent confirmations: independent providers agree within tolerance, or a
                 filing-derived value passes an identity / continuity check that involves it
  reported       one filing-derived value, not yet confirmed
  single_source  vendor-normalized only (or an unresolved definitional choice)
  conflict       sources disagree beyond tolerance, or a like-for-like identity fails
  missing        not available (or an instrument exists without its terms)
  stale          too old for the as_of date (newer reports exist but are absent)

conflict / missing on shares, cash, debt, revenue_ttm or cfo_ttm blocks a price conclusion; the rest
limit confidence. Nothing is fetched and no fact published after fb['as_of'] is read: every lookup
goes through facts.candidates. docs/MODULE_CONTRACTS.md (dataqa.validate) is the output contract.
"""
from datetime import date, timedelta
from math import log

from . import capital
from . import facts as F

TOL = {'shares_outstanding': .005, 'shares_outstanding_cover': .005, 'shares_outstanding_vendor': .005,
       'cash': .01, 'short_term_investments': .01, 'cash_incl_restricted': .01, 'restricted_cash': .01,
       'long_term_investments': .02, 'debt': .01, 'long_term_debt': .01, 'long_term_debt_tagged': .01,
       'current_portion_ltd': .01, 'short_term_borrowings': .01, 'debt_total_vendor': .01,
       'revenue': .005, 'operating_income': .005, 'cfo': .01, 'capex': .01, 'sbc': .02}
DEFAULT_TOL = .01
IDENTITY_TOL = .005           # statement identities are exact up to rounding
ROLLFORWARD_TOL = .025        # debt also moves with discount amortization, hedge fair value and FX: wider than a source tolerance
MAX_AGE = {'shares': 120, 'balance_sheet': 200, 'ttm': 200, 'annual': 456}
SPLIT_FACTORS = (1.5, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50)
SPLIT_BAND = .05
TTM_FIELDS = (('revenue_ttm', 'revenue'), ('operating_income_ttm', 'operating_income'), ('cfo_ttm', 'cfo'),
              ('capex_ttm', 'capex'), ('sbc_ttm', 'sbc'))
CRITICAL = ('shares_outstanding', 'cash_and_liquid_investments', 'investments', 'debt', 'convertibles',
            'post_period_events') + tuple(n for n, _ in TTM_FIELDS)
BLOCKING = {'shares_outstanding', 'cash_and_liquid_investments', 'debt', 'revenue_ttm', 'cfo_ttm'}
RANK = {'conflict': 0, 'missing': 1, 'stale': 2, 'single_source': 3, 'reported': 4, 'verified': 5}
FILING_KINDS = {'filing_derived', 'document'}
BS_TAGS = ('total_assets', 'total_liabilities', 'liabilities_and_equity', 'current_assets', 'equity')
DEBT_PARTS = ('long_term_debt', 'long_term_debt_tagged', 'current_portion_ltd', 'short_term_borrowings')
HYPOTHESES = ('noncurrent+current', 'tagged_total', 'tagged_plus_current')
SHARE_ROUTES = (('shares_outstanding_cover', 'cover'), ('shares_outstanding', 'balance_sheet'),
                ('shares_outstanding_vendor', 'vendor_float'))
ROUTE_PREF = {'cover': 0, 'balance_sheet': 1, 'capital_structure': 2, 'vendor_float': 3}
POST_TOPICS = {'post_period_event', 'post_period_events', 'subsequent_event', 'subsequent_events'}
CONVERT_TOPICS = {'convertible', 'convertibles', 'convertible_notes'}
WARRANT_TOPICS = {'warrant', 'warrants', 'prefunded_warrant', 'pre_funded_warrant', 'prefunded_warrants'}
DEFINITIONS = {
    'shares_outstanding': 'latest point-in-time common share count usable at as_of (cover page, balance sheet, vendor or '
                          'capital-structure common lines); never a weighted average',
    'cash': 'cash and cash equivalents at carrying value; restricted cash excluded and shown separately',
    'short_term_investments': 'current marketable securities; spendable, added to cash once',
    'long_term_investments': 'noncurrent investments: not spendable cash, shown separately and excluded from net_cash',
    'debt': 'borrowings at carrying value: long-term debt including current maturities plus short-term borrowings; '
            'operating leases excluded (lease_liabilities)',
    'net_cash': 'cash + short_term_investments - debt (leases, restricted cash and long-term investments excluded; '
                'net_cash_incl_long_term_investments shown separately)',
    'deferred_revenue': 'contract liabilities, current + noncurrent (cash already collected for future service)',
}


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _days(a, b):
    return (_d(b) - _d(a)).days


def _rel(a, b, base=None):
    m = max(abs(a), abs(b), abs(base or 0.0))
    return 0.0 if m == 0 else abs(a - b) / m


def _fmt(v):
    return 'n/a' if v is None else f'{v:,.0f}'


def _weakest(sts):
    return min(sts, key=RANK.__getitem__)


def _ref(f):
    return {'metric': f['metric'], 'period': f['period'], 'source_id': f['source_id'], 'value': f['value']}


def _kind(f):
    p = f['period']
    if p.startswith('I@'):
        return 'instant'
    if p.startswith(('FY', 'AVFY@')):
        return 'annual'
    if p.startswith('Q@') or p[-2:] in ('Q1', 'Q2', 'Q3', 'Q4'):
        return 'quarter'
    return 'other'


def _span_kind(a, b):
    n = _days(a, b)
    return 'annual' if 330 <= n <= 400 else 'quarter' if 80 <= n <= 120 else None


def _val(metric, f):
    return abs(f['value']) if metric == 'capex' else f['value']


def _split_factor(r):
    """Nearest split factor (k or 1/k) within SPLIT_BAND of a share-count ratio, else None."""
    if not r or r <= 0:
        return None
    for k in SPLIT_FACTORS:
        for x in (k, 1.0 / k):
            if abs(r / x - 1) <= SPLIT_BAND:
                return x
    return None


class _QA:
    """Run state: the FactBook, cached point-in-time candidates, the check log and identity results."""

    def __init__(self, fb, options):
        o = options or {}
        self.fb, self.as_of = fb, fb['as_of']
        self.tol = {**TOL, **(o.get('tolerances') or {})}
        self.max_age = {**MAX_AGE, **(o.get('max_age') or {})}
        self.ccy = fb['company'].get('currency', 'USD')
        self.checks, self.ident, self.cross, self._c = [], [], {}, {}

    def add(self, code, status, severity, message, detail=None, period=None, fields=()):
        c = {'code': code, 'status': status, 'severity': severity, 'message': message, 'detail': detail or {}}
        if period:
            c['period'] = period
        if fields:
            c['fields'] = list(fields)
        self.checks.append(c)
        return c

    def cands(self, metric):
        if metric not in self._c:
            self._c[metric] = F.candidates(self.fb, metric)      # look-ahead filter + best-first order
        return self._c[metric]

    def near(self, metric, end, kind, days=7):
        """Facts of one period, wherever providers date it (52/53-week ends vs month ends differ by days)."""
        return [f for f in self.cands(metric) if _kind(f) == kind and abs(_days(f['period_end'], end)) <= days]

    def best(self, metric, end, kind='instant'):
        c = self.near(metric, end, kind)
        return c[0] if c else None

    def v(self, metric, end, kind='instant'):
        f = self.best(metric, end, kind)
        return f['value'] if f else None

    def timeline(self, metric, kind):
        """[(period_end, best fact)] ascending, one per period (ends within 7 days are the same period)."""
        keep = []
        for f in self.cands(metric):
            if _kind(f) == kind and not any(abs(_days(e, f['period_end'])) <= 7 for e, _ in keep):
                keep.append((f['period_end'], f))
        return sorted(keep, key=lambda x: x[0])

    def src(self, f):
        return self.fb['sources'].get(f['source_id'], {})

    def provider(self, f):
        return self.src(f).get('provider') or f['source_id'].split(':')[0]

    def grade(self, f):
        return 'reported' if self.src(f).get('kind') in FILING_KINDS and f['basis'] != 'normalized' else 'single_source'

    def has_bs(self, end):
        return any(self.best(m, end) for m in BS_TAGS)

    def age(self, end):
        return _days(end, self.as_of)

    def latest_bs(self):
        ends = [tl[-1][0] for tl in (self.timeline(m, 'instant') for m in BS_TAGS) if tl]
        return max(ends) if ends else None

    def idents(self, metric, end):
        return [x for x in self.ident if x['metric'] == metric and abs(_days(x['end'], end)) <= 7]

    def price(self):
        p = (self.fb.get('market') or {}).get('price') or {}
        return p['value'] if p.get('value') and p.get('as_of') and _d(p['as_of']) <= _d(self.as_of) else None

    def closes(self, start, end):
        return [float(r['close']) for r in (self.fb.get('market') or {}).get('price_history') or []
                if r.get('close') and r.get('date') and _d(start) < _d(r['date']) <= min(_d(end), _d(self.as_of))]

    def dated(self, x, missing=False):
        """Qualitative item usable at as_of; undated items count only where that is the conservative reading."""
        return _d(x['as_of']) <= _d(self.as_of) if x.get('as_of') else missing


def _field(q, name, status, value, unit, as_of, facts=(), checks=(), notes=(), **extra):
    return {'field': name, 'status': status, 'value': value, 'unit': unit, 'as_of': as_of,
            'sources': sorted({f['source_id'] for f in facts}), 'facts': [_ref(f) for f in facts],
            'checks': sorted(set(checks)), 'note': '; '.join(n for n in notes if n), **extra}


def _stale(q, fld, kind, what):
    """Downgrade a field to stale when its date is older than the age limit (keeps the pre-age status)."""
    if fld['as_of'] is None or fld['status'] in ('conflict', 'missing'):
        return fld
    age = q.age(fld['as_of'])
    fld['age_days'] = age
    if age > q.max_age[kind]:
        fld['source_status'], fld['status'] = fld['status'], 'stale'
        fld['note'] = '; '.join(x for x in (fld['note'], f'{what} dated {fld["as_of"]} is {age} days old at {q.as_of} '
                                            f'(limit {q.max_age[kind]}): newer reports exist but are absent') if x)
        fld['checks'] = sorted(set(fld['checks']) | {'staleness'})
        q.add('staleness', 'fail', 'major', f"{fld['field']}: {what} is {age} days old", period=fld['as_of'],
              detail={'as_of': fld['as_of'], 'age_days': age, 'limit_days': q.max_age[kind]}, fields=(fld['field'],))
    return fld


# ---------------------------------------------------------------- cross-source and identities

def _explain(q, metric, f, b):
    """Known definitional reasons two providers differ for the same period (not a conflict, but recorded)."""
    a, end, tol = f['value'], f['period_end'], q.tol.get(metric, DEFAULT_TOL)
    if metric == 'cash':
        sti, rc, incl = q.v('short_term_investments', end), q.v('restricted_cash', end), q.v('cash_incl_restricted', end)
        if sti and _rel(a + sti, b) <= tol:
            return 'other source includes short-term investments in cash (adding them again would double count)'
        if (incl and _rel(incl, b) <= tol) or (rc and _rel(a + rc, b) <= tol):
            return 'other source includes restricted cash'
    if metric.startswith('shares_outstanding') and a and _split_factor(b / a):
        return f'differs by a split factor ({_split_factor(b / a):g}): split-adjusted versus unadjusted count'
    return None


def _cross(q, metric, f):
    """Compare a chosen fact with every other provider's value for the same period (vendor families count once)."""
    key = (metric, f['period_end'], _kind(f), f['source_id'])
    if key in q.cross:
        return q.cross[key]
    mine, others = q.provider(f), {}
    for g in q.near(metric, f['period_end'], _kind(f)):
        others.setdefault(q.provider(g), g)
    others.pop(mine, None)
    out = {'agree': [], 'disagree': [], 'explained': [], 'compared': bool(others)}
    a, tol = _val(metric, f), q.tol.get(metric, DEFAULT_TOL)
    base = None
    if _kind(f) in ('annual', 'quarter') and metric != 'revenue':
        rev = q.best('revenue', f['period_end'], _kind(f))
        base = .2 * abs(rev['value']) if rev else None     # near-zero margins compare on a revenue-scaled base
    for p, g in sorted(others.items()):
        b = _val(metric, g)
        r = _rel(a, b, base)
        row = {'provider': p, 'source_id': g['source_id'], 'value': b, 'rel_diff': round(r, 6)}
        if r <= tol:
            out['agree'].append(row)
            continue
        why = _explain(q, metric, f, b)
        if why:
            out['explained'].append({**row, 'definition': why})
        else:
            if q.grade(g) == 'reported' and _d(g['available_at']) > _d(f['available_at']):
                row['note'] = 'later filing-derived value: possible restatement (first-filed value kept for point-in-time use)'
            out['disagree'].append(row)
    if others:
        bad = bool(out['disagree'])
        q.add('cross_source', 'fail' if bad else 'pass', 'major' if bad else 'minor',
              f"{metric} {f['period']}: {mine} {_fmt(a)} vs " + ', '.join(f"{r['provider']} {_fmt(r['value'])}"
                                                                         for k in ('agree', 'explained', 'disagree') for r in out[k])
              + (' (beyond tolerance)' if bad else ' (agree or definitional difference)'),
              detail={'metric': metric, 'value': a, 'provider': mine, 'source_id': f['source_id'], 'tolerance': tol,
                      **{k: out[k] for k in ('agree', 'explained', 'disagree')}}, period=f['period'])
    q.cross[key] = out
    return out


def _status_of(q, metric, f, corroborated=False):
    """Status of one fact: grade by source, upgraded by an independent confirmation, conflict on unresolved disagreement."""
    grade = q.grade(f)
    cx = _cross(q, metric, f)
    ids = q.idents(metric, f['period_end'])
    codes = [x['code'] for x in ids] + (['cross_source'] if cx['compared'] else [])
    notes = [f"{r['provider']} reports {_fmt(r['value'])}: {r['definition']}" for r in cx['explained']]
    failed = sorted({x['code'] for x in ids if x['status'] == 'fail' and x['lfl']})
    if failed:
        return 'conflict', codes, notes + [f"{metric} {f['period']} fails {', '.join(failed)}"]
    id_ok = grade == 'reported' and (corroborated or any(x['status'] == 'pass' for x in ids))
    if cx['disagree']:
        who = ', '.join(f"{r['provider']} {_fmt(r['value'])}" for r in cx['disagree'])
        if cx['agree'] or id_ok:
            return 'verified', codes, notes + [f"{metric} {f['period']}: outlier source(s) {who} recorded; chosen value "
                                               f"corroborated by {'another provider' if cx['agree'] else 'an accounting check'}"]
        return 'conflict', codes, notes + [f"{metric} {f['period']}: {q.provider(f)} {_fmt(f['value'])} vs {who} beyond tolerance"]
    if cx['agree'] or id_ok:
        return 'verified', codes, notes
    return grade, codes, notes


def _identity(q, metric, end, code, status, lfl):
    q.ident.append({'metric': metric, 'end': end, 'code': code, 'status': status, 'lfl': lfl})


def _identities(q):
    """Accounting identities that confirm (or contradict) the reported numbers they involve."""
    for end, ta in q.timeline('total_assets', 'instant')[-6:]:
        le, tl, eq = (q.best(m, end) for m in ('liabilities_and_equity', 'total_liabilities', 'equity'))
        if le:
            other, how = le['value'], 'liabilities_and_equity'
        elif tl and eq:
            other, how = tl['value'] + eq['value'], 'total_liabilities + equity'
        else:
            continue
        ok = _rel(ta['value'], other) <= IDENTITY_TOL
        d = {'total_assets': ta['value'], how: other}
        if le and tl and eq:
            d['noncontrolling_or_mezzanine'] = le['value'] - tl['value'] - eq['value']
        q.add('balance_sheet_identity', 'pass' if ok else 'fail', 'major' if le or ok else 'minor',
              f'assets {_fmt(ta["value"])} vs {how} {_fmt(other)}' + ('' if ok or le else ' (gap may be noncontrolling/mezzanine equity)'),
              detail=d, period=ta['period'])
    for kind, n in (('annual', 5), ('quarter', 4)):
        for end, rev in q.timeline('revenue', kind)[-n:]:
            cor, gp = q.best('cost_of_revenue', end, kind), q.best('gross_profit', end, kind)
            if not (cor and gp):
                continue
            diff = rev['value'] - cor['value'] - gp['value']
            ok = abs(diff) <= IDENTITY_TOL * abs(rev['value'])
            lfl = len({rev['source_id'], cor['source_id'], gp['source_id']}) == 1
            q.add('gross_profit_identity', 'pass' if ok else 'fail', 'major',
                  f"revenue - cost of revenue - gross profit = {_fmt(diff)}", period=rev['period'], fields=('revenue_ttm',),
                  detail={'revenue': rev['value'], 'cost_of_revenue': cor['value'], 'gross_profit': gp['value'], 'difference': diff,
                          'same_source': lfl})
            for x in (rev, cor, gp):
                _identity(q, x['metric'], end, 'gross_profit_identity', 'pass' if ok else 'fail', lfl)
        for end, cfo in q.timeline('cfo', kind)[-n:]:
            cfi, cff, chg, fx = (q.best(m, end, kind) for m in ('cfi', 'cff', 'change_in_cash', 'fx_effect'))
            if not (cfi and cff and chg):
                continue
            s = cfo['value'] + cfi['value'] + cff['value'] + (fx['value'] if fx else 0.0)
            base = max(abs(cfo['value']), abs(cfi['value']), abs(cff['value']), abs(chg['value']))
            ok = abs(s - chg['value']) <= IDENTITY_TOL * base
            lfl = fx is not None or ok          # without an FX line a small gap may be the missing FX effect
            q.add('cash_flow_tie', 'pass' if ok else 'fail', 'major' if lfl else 'minor',
                  f"cfo + cfi + cff{' + fx' if fx else ''} = {_fmt(s)} vs change in cash {_fmt(chg['value'])}",
                  period=cfo['period'], fields=('cfo_ttm',),
                  detail={'cfo': cfo['value'], 'cfi': cfi['value'], 'cff': cff['value'], 'fx_effect': fx['value'] if fx else None,
                          'change_in_cash': chg['value'], 'difference': s - chg['value']})
            for m in ('cfo', 'change_in_cash'):
                _identity(q, m, end, 'cash_flow_tie', 'pass' if ok else 'fail', lfl)
    tl = q.timeline('cash', 'instant')
    for (pe, p), (ce, c) in list(zip(tl, tl[1:]))[-5:]:
        kind = _span_kind(pe, ce)
        chg = q.best('change_in_cash', ce, kind) if kind else None
        if not chg:
            continue
        incl = 'restricted' in (chg.get('concept') or '')
        lfl = not incl
        bp, bc, basis = p['value'], c['value'], 'cash'
        if incl:
            ip, ic = q.v('cash_incl_restricted', pe), q.v('cash_incl_restricted', ce)
            rp, rc = q.v('restricted_cash', pe), q.v('restricted_cash', ce)
            if ip is not None and ic is not None:
                bp, bc, basis, lfl = ip, ic, 'cash_incl_restricted', True
            elif rp is not None and rc is not None:
                bp, bc, basis, lfl = bp + rp, bc + rc, 'cash + restricted_cash', True
        delta = bc - bp
        ok = abs(delta - chg['value']) <= q.tol['cash'] * max(abs(bp), abs(bc))
        q.add('cash_rollforward', 'pass' if ok else 'fail', 'major' if lfl else 'minor',
              f'balance-sheet {basis} moved {_fmt(delta)}; cash-flow statement change {_fmt(chg["value"])}'
              + ('' if lfl else ' (statement change includes restricted cash, balance sheet basis may not)'),
              period=f'{pe}..{ce}', fields=('cash_and_liquid_investments',),
              detail={'prior': bp, 'current': bc, 'balance_change': delta, 'cash_flow_change': chg['value'], 'basis': basis,
                      'like_for_like': lfl})
        for e in (pe, ce):
            _identity(q, 'cash', e, 'cash_rollforward', 'pass' if ok else 'fail', lfl)
    for end, ni in q.timeline('net_income', 'annual')[-5:]:
        by = {}
        for g in q.near('net_income', end, 'annual'):
            by.setdefault(q.provider(g), {}).setdefault(g['source_id'], g)
        for prov, rows in by.items():
            if len(rows) < 2:
                continue
            vals = {s: g['value'] for s, g in rows.items()}
            ok = max(vals.values()) - min(vals.values()) <= IDENTITY_TOL * max(abs(x) for x in vals.values())
            q.add('net_income_is_vs_cf', 'pass' if ok else 'fail', 'minor',
                  f'net income across {prov} statements: ' + ', '.join(f'{s} {_fmt(x)}' for s, x in vals.items())
                  + ('' if ok else ' (cash-flow statement may start from profit including noncontrolling interests)'),
                  period=ni['period'], detail={'values': vals})
    for m in ('revenue', 'operating_income', 'cfo', 'capex', 'sbc'):
        qs = q.timeline(m, 'quarter')
        if len(qs) < 4:
            continue
        for end, a in q.timeline(m, 'annual')[-3:]:
            start = a.get('period_start') or (_d(end) - timedelta(days=364)).isoformat()
            inq = [(e, f) for e, f in qs if _days(start, e) > 0 and _days(e, end) >= -7]
            if len(inq) != 4:
                continue
            s = sum(_val(m, f) for _, f in inq)
            rev = q.best('revenue', end, 'annual')
            ok = _rel(s, _val(m, a), .2 * abs(rev['value']) if rev and m != 'revenue' else None) <= q.tol.get(m, DEFAULT_TOL)
            lfl = len({q.provider(f) for _, f in inq} | {q.provider(a)}) == 1
            derived = any(f['basis'] == 'derived' or f.get('derived_from') for _, f in inq)
            q.add('quarters_sum_to_annual', 'pass' if ok else 'fail', 'major',
                  f'{m}: four quarters sum to {_fmt(s)} vs fiscal year {_fmt(_val(m, a))}'
                  + (' (a quarter is derived from the annual: tie is not independent)' if derived else ''),
                  period=a['period'], detail={'metric': m, 'quarters': {e: _val(m, f) for e, f in inq}, 'annual': _val(m, a)})
            if not derived or not ok:
                for e in [end] + [e for e, _ in inq]:
                    _identity(q, m, e, 'quarters_sum_to_annual', 'pass' if ok else 'fail', lfl)


# ---------------------------------------------------------------- capital structure

def _line_date(x, cs):
    return x.get('as_of') or x.get('issue_date') or cs['as_of']


def _capital(q, cs):
    """capital.py structure -> amounts by kind, statement-dated subset and post-period lines (validated, never trusted blind).

    A line's date is its as_of, else its issue_date, else the structure's as_of (undated lines read as post-period)."""
    if not cs:
        return None
    fb = q.fb
    try:
        capital.validate(cs)
    except (ValueError, KeyError, TypeError) as e:
        q.add('capital_structure_valid', 'fail', 'critical', f'capital structure rejected: {e}', fields=('convertibles',))
        return None
    if _d(cs['as_of']) > _d(q.as_of):
        q.add('capital_structure_lookahead', 'fail', 'critical', f"capital structure dated {cs['as_of']} after as_of {q.as_of}: ignored")
        return None
    try:
        summ, err = capital.summarize(cs), None
    except (ValueError, KeyError, TypeError) as e:
        summ, err = None, f'{type(e).__name__}: {e}'
    q.add('capital_structure_valid', 'fail' if err else 'pass', 'major' if err else 'minor',
          f'capital structure as of {cs["as_of"]} ' + (f'could not be summarized ({err})' if err else 'validated and summarized'),
          detail={'summary': summ} if summ else {'error': err})

    def grade(lines):
        ids = [x['source_id'] for x in lines]
        return 'reported' if ids and all(fb['sources'].get(s, {}).get('kind') in FILING_KINDS for s in ids) else 'single_source'

    def lines(group, kinds, until=None):
        return [x for x in cs.get(group, []) if x['kind'] in kinds and (until is None or _d(_line_date(x, cs)) <= _d(until))]

    def amount(xs, key='amount'):
        return sum(float(x.get(key) or 0.0) for x in xs)

    def debt_amount(xs):
        return sum(float(x.get('carrying_value', x.get('principal')) or 0.0) for x in xs)
    return {'raw': cs, 'as_of': cs['as_of'], 'summary': summ, 'error': err, 'lines': lines, 'grade': grade,
            'amount': amount, 'debt_amount': debt_amount, 'source_ids': sorted({x['source_id'] for g in ('cash', 'shares', 'debt', 'quasi_debt')
                                                                                 for x in cs.get(g, [])}),
            'converts': [x for x in cs.get('debt', []) if x['kind'] == 'convertible'],
            'share_kinds': {s['kind'] for s in cs.get('shares', [])}}


def _cs_compare(q, cs, field, label, stmt_value, stmt_end, kinds_group, explain=None):
    """Cross-check the capital structure against the statements at the statement date.

    Returns (use_cs, corroborated, conflict, note). A structure dated at the statement date must tie; a newer one
    (a bridge with post-period lines) is compared on its statement-dated lines and then used as the newer source."""
    group, kinds = kinds_group
    newer = _days(stmt_end, cs['as_of']) > 7 if stmt_end else True
    older = _days(cs['as_of'], stmt_end) > 7 if stmt_end else False
    if older:
        return False, False, False, f'capital structure ({cs["as_of"]}) older than the statements: not used for {label}'
    xs = cs['lines'](group, kinds, until=(_d(stmt_end) + timedelta(days=7)).isoformat() if stmt_end else None)
    if stmt_value is None or stmt_end is None:
        return True, False, False, f'{label} from the capital structure only (no statement value to compare)'
    cs_v = cs['debt_amount'](xs) if group == 'debt' else cs['amount'](xs, 'count' if group == 'shares' else 'amount')
    tol = q.tol.get('debt' if group == 'debt' else 'shares_outstanding' if group == 'shares' else 'cash', DEFAULT_TOL)
    r = _rel(stmt_value, cs_v)
    why = explain(stmt_value, cs_v) if explain and r > tol else None
    ok = r <= tol or bool(why)
    post = [x['id'] for x in cs.get('raw', {}).get(group, []) if x['kind'] in kinds and _d(_line_date(x, cs)) > _d(stmt_end) + timedelta(days=7)]
    q.add('capital_structure_crosscheck', 'pass' if ok else 'fail', 'minor' if ok else ('major' if newer else 'critical'),
          f'{label}: statements {_fmt(stmt_value)} at {stmt_end} vs capital-structure lines dated by then {_fmt(cs_v)}'
          + (f' ({why})' if why else '') + (f'; post-period lines {post}' if post else ''),
          period=stmt_end, fields=(field,),
          detail={'statement': stmt_value, 'capital_structure': cs_v, 'rel_diff': round(r, 6), 'tolerance': tol,
                  'explanation': why, 'post_period_lines': post, 'capital_structure_as_of': cs['as_of']})
    if ok:
        return newer, True, False, (f'capital structure ties to the statements at {stmt_end}' + (f' ({why})' if why else '')
                                    + ('; newer capital structure used' if newer else ''))
    if newer:   # exchanges/repayments after the statement date can change pre-existing lines: limited, not a conflict
        return True, False, False, (f'capital structure lines dated by {stmt_end} ({_fmt(cs_v)}) do not tie to the statements '
                                    f'({_fmt(stmt_value)}); may reflect post-period exchanges or repayments: confirm')
    return False, False, True, (f'capital structure {label} {_fmt(cs_v)} does not tie to the statements {_fmt(stmt_value)} '
                                f'at {stmt_end}: an instrument may be missing or a balance-sheet scope is wrong')


# ---------------------------------------------------------------- shares

def _split_evidence(q, a, b, k):
    ev = {}
    for metric, key in (('eps_diluted', 'eps'), ('shares_diluted_wavg', 'wavg')):
        x0, x1 = q.best(metric, a['as_of'], 'annual'), q.best(metric, b['as_of'], 'annual')
        if x0 and x1 and x0['value'] and x0['value'] * x1['value'] > 0:
            r = x1['value'] / x0['value']
            ev[f'{key}_ratio'] = r
            ev[f'{key}_supports_split'] = abs(log(r * k)) < abs(log(r)) if key == 'eps' else abs(r / k - 1) <= .25
    p0, p1 = q.closes((_d(a['as_of']) - timedelta(days=6)).isoformat(), a['as_of']), \
        q.closes((_d(b['as_of']) - timedelta(days=6)).isoformat(), b['as_of'])
    if p0 and p1:
        r = p1[-1] / p0[-1]
        ev['price_ratio'] = r
        ev['price_supports_split'] = abs(log(r * k)) < abs(log(r))
    tested = [x for x in ev if x.endswith('supports_split')]
    return ev, any(ev[x] for x in tested), bool(tested)


def _continuity(q, a, b):
    """Share count change between two dated counts versus the cash spent on buybacks over the same span."""
    kind = _span_kind(a['as_of'], b['as_of'])
    chg = b['value'] - a['value']
    d = {'from': a['as_of'], 'to': b['as_of'], 'routes': [a['basis'], b['basis']], 'prior': a['value'], 'current': b['value'],
         'change': chg, 'change_pct': chg / a['value'] if a['value'] else None}
    bb = q.best('buybacks', b['as_of'], kind) if kind else None
    status, sev, msg = 'skip', 'minor', f"shares {_fmt(a['value'])} ({a['as_of']}) -> {_fmt(b['value'])} ({b['as_of']})"
    if bb:
        d['buybacks'] = abs(bb['value'])
    if chg < 0 and bb and abs(bb['value']) > 0:
        implied = abs(bb['value']) / -chg
        d['implied_avg_repurchase_price'] = implied
        closes = q.closes(a['as_of'], b['as_of'])
        if closes:
            lo, hi = min(closes), max(closes)
            d['price_range'] = [lo, hi]
            if implied < .67 * lo:
                status, sev = 'fail', 'major'
                msg += (f': reduction implies buying at {implied:,.2f}, far below the period low {lo:,.2f} - count may be stale, '
                        f'split-affected or wrongly scoped')
            else:
                status = 'pass'
                msg += f': implied repurchase price {implied:,.2f} vs period range {lo:,.2f}-{hi:,.2f}' + (
                    ' (issuance from SBC/conversions offset part of the buyback)' if implied > 1.5 * hi else '')
        else:
            msg += f': implied repurchase price {implied:,.2f} recorded (no price history to test it)'
    elif chg < 0:
        msg += ': reduction without recorded buybacks over this span (recorded)'
    else:
        msg += f": {'+' if chg >= 0 else ''}{(d['change_pct'] or 0):.1%} (issuance net of buybacks, recorded)"
    q.add('share_continuity', status, sev, msg, detail=d, period=f"{a['as_of']}..{b['as_of']}", fields=('shares_outstanding',))
    return status


def _shares(q, cs):
    fb, routes = q.fb, []
    for metric, basis in SHARE_ROUTES:
        for end, f in q.timeline(metric, 'instant'):
            routes.append({'as_of': end, 'value': f['value'], 'basis': basis, 'fact': f})
    if cs:
        com = cs['lines']('shares', {'common'})
        if com:
            routes.append({'as_of': max(_line_date(x, cs) for x in com), 'value': cs['amount'](com, 'count'),
                           'basis': 'capital_structure', 'fact': None, 'grade': cs['grade'](com), 'source_ids': sorted({x['source_id'] for x in com})})
    if not routes:
        fld = _field(q, 'shares_outstanding', 'missing', None, 'shares', None,
                     notes=['no point-in-time share count (cover, balance sheet, vendor or capital structure); weighted averages are never used'])
        return fld, {'value': None, 'as_of': None, 'basis': None, 'status': 'missing'}, []
    merged = {}
    for r in sorted(routes, key=lambda r: ROUTE_PREF[r['basis']]):     # one count per date cluster, filing routes first
        if not any(abs(_days(e, r['as_of'])) <= 7 for e in merged):
            merged[r['as_of']] = r
    tl = [merged[e] for e in sorted(merged)]
    pick, splits, last_cont = tl[-1], [], None
    for a, b in zip(tl, tl[1:]):
        k = _split_factor(b['value'] / a['value']) if a['value'] else None
        if k:
            ev, support, tested = _split_evidence(q, a, b, k)
            code = 'possible_split' if support or not tested else 'large_share_change'
            msg = (f"share count x{b['value'] / a['value']:.3f} between {a['as_of']} and {b['as_of']} is near a "
                   + (f'{k:g}:1 split: ' if k > 1 else f'1:{1 / k:g} reverse split: ')
                   + ('treated as a split, not dilution - restate per-share history before comparing' if code == 'possible_split'
                      else 'per-share data do not move inversely: treated as real issuance/consolidation - verify'))
            q.add(code, 'fail', 'major', msg, period=f"{a['as_of']}..{b['as_of']}", fields=('shares_outstanding',),
                  detail={'factor': k, 'ratio': b['value'] / a['value'], 'evidence': ev, 'per_share_tested': tested,
                          'from': a['as_of'], 'to': b['as_of']})
            if code == 'possible_split':
                splits.append({'from': a['as_of'], 'to': b['as_of'], 'factor': k, 'evidence': ev})
                if b is pick:
                    last_cont = 'split'
                continue
        st = _continuity(q, a, b)
        if b is pick:
            last_cont = st
    f, checks = pick['fact'], ['share_continuity'] if len(tl) > 1 else []
    notes = [f"{pick['basis']} count dated {pick['as_of']}" + (': vendor-normalized only, confirm against the filing cover page'
                                                               if pick['basis'] == 'vendor_float' else '')]
    if f:
        status, codes, n = _status_of(q, f['metric'], f, corroborated=last_cont == 'pass')
        checks += codes
        notes += n
        facts = [f]
    else:
        status, facts = pick['grade'], []
        if last_cont == 'pass' and status == 'reported':
            status = 'verified'
    # independent routes dated within a week of the pick (vendor vs filing, structure vs filing)
    agree, disagree = [], []
    for r in routes:
        if r is pick or abs(_days(r['as_of'], pick['as_of'])) > 7 or not r['value']:
            continue
        indep = (r['fact'] is None) != (f is None) or (r['fact'] is not None and f is not None and q.provider(r['fact']) != q.provider(f))
        rr = _rel(pick['value'], r['value'])
        row = {'basis': r['basis'], 'as_of': r['as_of'], 'value': r['value'], 'rel_diff': round(rr, 6), 'independent': indep}
        if rr <= q.tol['shares_outstanding']:
            agree.append(row)
        elif _split_factor(r['value'] / pick['value']):
            notes.append(f"{r['basis']} count {_fmt(r['value'])} differs by a split factor (split-adjusted vs unadjusted)")
        else:
            disagree.append(row)
    if agree or disagree:
        checks.append('share_routes')
        q.add('share_routes', 'fail' if disagree else 'pass', 'major' if disagree else 'minor',
              f"{pick['basis']} {_fmt(pick['value'])} ({pick['as_of']}) vs " + ', '.join(f"{r['basis']} {_fmt(r['value'])}" for r in agree + disagree),
              detail={'pick': {k: pick[k] for k in ('basis', 'as_of', 'value')}, 'agree': agree, 'disagree': disagree},
              period=pick['as_of'], fields=('shares_outstanding',))
    if any(r['independent'] for r in agree) and status in ('reported', 'single_source'):
        status = 'verified'
    if disagree and status != 'conflict':
        if any(r['independent'] for r in agree) or last_cont == 'pass':
            notes.append('a route dated within a week disagrees: recorded, pick corroborated')
        else:
            status = 'conflict'
            notes.append('routes dated within a week disagree beyond 0.5%: ' + ', '.join(f"{r['basis']} {_fmt(r['value'])}" for r in disagree))
    # weighted-average counts must never stand in for a point-in-time count
    for m in ('shares_basic_wavg', 'shares_diluted_wavg'):
        for kind in ('annual', 'quarter'):
            w = q.best(m, pick['as_of'], kind)
            if w and _rel(w['value'], pick['value']) < 1e-9:
                q.add('shares_not_wavg', 'fail', 'major', f'point-in-time count equals the {m} figure for {w["period"]}: '
                      'a weighted average may have been stored as the outstanding count', period=pick['as_of'], fields=('shares_outstanding',))
                checks.append('shares_not_wavg')
                status = _weakest([status, 'single_source'])
    if last_cont == 'fail':
        status = _weakest([status, 'single_source'])
        notes.append('share continuity versus buybacks failed: count may be stale or split-affected')
    # pre-funded / penny warrants are economic shares (NBIS): evidence without structure lines limits the count
    ev = [x['id'] for x in fb.get('qualitative', []) if x.get('topic') in WARRANT_TOPICS and q.dated(x, missing=True)]
    ev += [f"{g['metric']}:{g.get('concept')}" for g in fb['facts'] if F.usable(fb, g) and 'warrant' in (g['metric'] + (g.get('concept') or '')).lower()]
    incomplete = bool(ev) and not (cs and cs['share_kinds'] & {'prefunded_warrant', 'warrant'})
    if ev:
        q.add('dilutive_instruments_without_terms', 'fail' if incomplete else 'pass', 'major' if incomplete else 'minor',
              f"warrant evidence ({', '.join(ev[:4])}) " + ('without warrant lines in a capital structure: economic share count may be understated'
                                                            if incomplete else 'is modelled in the capital structure'),
              fields=('shares_outstanding',), detail={'evidence': ev})
        checks.append('dilutive_instruments_without_terms')
        if incomplete:
            notes.append('warrants/pre-funded warrants evidenced but not modelled: economic share count incomplete')
    fld = _field(q, 'shares_outstanding', status, pick['value'], 'shares', pick['as_of'], facts, checks, notes,
                 basis=pick['basis'], source_ids=pick.get('source_ids') or [x['source_id'] for x in facts])
    if incomplete:
        fld['incomplete'] = True
    if splits:
        fld['note'] = '; '.join([fld['note']] + [f"possible split x{x['factor']:g} between {x['from']} and {x['to']}: per-share "
                                                  f"history must be restated before comparing" for x in splits])
        fld['checks'] = sorted(set(fld['checks']) | {'possible_split'})
    _stale(q, fld, 'shares', 'share count')
    res = {'value': pick['value'], 'as_of': pick['as_of'], 'basis': pick['basis'], 'status': fld['status'],
           'source_id': f['source_id'] if f else (pick.get('source_ids') or [None])[0], 'definition': DEFINITIONS['shares_outstanding']}
    if cs and cs['summary']:
        res['basic_economic_shares'] = cs['summary']['basic_economic_shares']     # common + pre-funded + zero-strike
        res['rsu_shares'], res['economic_shares_as_of'] = cs['summary']['rsu_shares'], cs['as_of']
    return fld, res, splits


# ---------------------------------------------------------------- cash and investments

def _cash(q, cs):
    fb, ccy, res = q.fb, q.ccy, {}
    tl = q.timeline('cash', 'instant')
    bs_end = q.latest_bs()
    notes, cash_f, fallback = [], None, False
    if tl:
        end, cash_f = tl[-1]
    else:
        inc = q.timeline('cash_incl_restricted', 'instant')
        if inc:
            end, cash_f, fallback = inc[-1][0], inc[-1][1], True
            notes.append('cash taken from the cash-flow total that includes restricted cash (no cash-equivalents line)')
        else:
            end = None
    if cash_f and bs_end and _days(end, bs_end) > 7:
        notes.append(f'cash not tagged on the latest balance sheet ({bs_end}); using {end}')
    parts, st, checks, facts, comp_src = {}, [], [], [], {}
    if cash_f:
        s, c, n = _status_of(q, cash_f['metric'], cash_f)
        st.append(s if not fallback and not (bs_end and _days(end, bs_end) > 7) else _weakest([s, 'single_source']))
        checks += c
        notes += n
        facts.append(cash_f)
        parts['cash'], comp_src['cash'] = cash_f['value'], [cash_f['source_id']]
        bs = q.has_bs(end)
        for m in ('short_term_investments', 'long_term_investments', 'restricted_cash'):
            g = q.best(m, end)
            if g:
                parts[m], comp_src[m] = g['value'], [g['source_id']]
                if m == 'short_term_investments':
                    s, c, n = _status_of(q, m, g)
                    st.append(s)
                    checks += c
                    notes += n
                    facts.append(g)
            else:
                parts[m] = 0.0 if bs else None
        if parts['short_term_investments'] is None:
            st.append('single_source')
            notes.append('no balance sheet context at the cash date: short-term investments unknown, cash only')
        # double-count guard (ASTS): liquid assets cannot exceed current assets, and one amount is not both cash and STI
        ca = q.best('current_assets', end)
        liquid = parts['cash'] + (parts['short_term_investments'] or 0.0)
        dup = bool(parts.get('short_term_investments')) and _rel(parts['cash'], parts['short_term_investments']) < 1e-9
        if ca or dup:
            bad = dup or (ca is not None and liquid > ca['value'] * (1 + IDENTITY_TOL))
            q.add('liquid_assets_within_current_assets', 'fail' if bad else 'pass', 'critical' if bad else 'minor',
                  ('the same amount is tagged as cash and as short-term investments' if dup else
                   f'cash + short-term investments {_fmt(liquid)} vs current assets {_fmt(ca["value"])}')
                  + (': double count suspected' if bad else ''), period=end, fields=('cash_and_liquid_investments',),
                  detail={'cash': parts['cash'], 'short_term_investments': parts['short_term_investments'],
                          'current_assets': ca['value'] if ca else None})
            checks.append('liquid_assets_within_current_assets')
            if bad:
                st.append('conflict')
                notes.append('liquid assets double counted or mis-scoped')
    value = parts['cash'] + (parts.get('short_term_investments') or 0.0) if cash_f else None
    basis, as_of, src_ids = 'balance_sheet', end, sorted({x['source_id'] for x in facts})
    if cs:
        def expl(sv, cv):
            return ('capital structure counts restricted cash as unrestricted' if parts.get('restricted_cash')
                    and _rel(sv + parts['restricted_cash'], cv) <= q.tol['cash'] else None)
        use, corr, conf, note = _cs_compare(q, cs, 'cash_and_liquid_investments', 'cash + short-term investments', value, end,
                                            ('cash', {'unrestricted', 'short_term_investments'}), expl)
        notes.append(note)
        checks.append('capital_structure_crosscheck')
        if conf:
            st.append('conflict')
        if corr:
            st = ['verified' if x == 'reported' else x for x in st]
        if use:
            un, sti_l, lti_l, rc_l = (cs['lines']('cash', {k}) for k in ('unrestricted', 'short_term_investments',
                                                                          'long_term_investments', 'restricted'))
            stmt = dict(parts)
            parts = {'cash': cs['amount'](un), 'short_term_investments': cs['amount'](sti_l),
                     'long_term_investments': cs['amount'](lti_l), 'restricted_cash': cs['amount'](rc_l)}
            value, basis, as_of = parts['cash'] + parts['short_term_investments'], 'capital_structure', cs['as_of']
            src_ids = sorted({x['source_id'] for x in un + sti_l})
            comp_src = {k: sorted({x['source_id'] for x in xs}) for k, xs in
                        (('cash', un), ('short_term_investments', sti_l), ('long_term_investments', lti_l), ('restricted_cash', rc_l))}
            g = cs['grade'](un + sti_l) if un + sti_l else 'missing'
            st = [g] + ([] if corr else ['single_source'] if stmt.get('cash') is not None else [])
            notes.append(f'statement values at {end}: {stmt}' if end else 'no statement cash: capital structure only')
    if value is None:
        fld = _field(q, 'cash_and_liquid_investments', 'missing', None, ccy, None, checks=checks,
                     notes=notes + ['no cash and cash-equivalents line usable at as_of'])
    else:
        fld = _field(q, 'cash_and_liquid_investments', _weakest(st), value, ccy, as_of, facts, checks, notes,
                     basis=basis, components={'cash': parts.get('cash'), 'short_term_investments': parts.get('short_term_investments')},
                     restricted_cash_excluded=parts.get('restricted_cash'), source_ids=src_ids)
        _stale(q, fld, 'balance_sheet', 'balance sheet')
    # long-term investments: separate from spendable cash
    lti_f = q.best('long_term_investments', end) if end else None
    if basis == 'capital_structure':
        lti_lines = cs['lines']('cash', {'long_term_investments'})
        inv = _field(q, 'investments', cs['grade'](lti_lines) if lti_lines else 'reported', parts['long_term_investments'], ccy, as_of,
                     notes=['from the capital structure' + ('' if lti_lines else ': no long-term investment lines (none)')],
                     basis='capital_structure', haircut_value=(cs['summary'] or {}).get('investments_after_haircut'))
    elif lti_f:
        s, c, n = _status_of(q, 'long_term_investments', lti_f)
        inv = _field(q, 'investments', s, lti_f['value'], ccy, end, [lti_f], c, n + ['long-term investments: not spendable cash, excluded from net cash'],
                     basis='balance_sheet')
    elif end and q.has_bs(end):
        inv = _field(q, 'investments', 'reported', 0.0, ccy, end,
                     notes=[f'no long-term investment line on the balance sheet dated {end}: taken as none (conservative)'],
                     basis='balance_sheet', assumed_zero=True)
    else:
        inv = _field(q, 'investments', 'missing', None, ccy, None, notes=['no balance sheet usable at as_of'])
    if inv['value'] is not None:
        _stale(q, inv, 'balance_sheet', 'balance sheet')
    for k in ('cash', 'short_term_investments', 'long_term_investments', 'restricted_cash'):
        res[k] = {'value': parts.get(k) if value is not None or k == 'long_term_investments' else None, 'as_of': as_of,
                  'basis': basis, 'status': (inv if k == 'long_term_investments' else fld)['status'],
                  'source_ids': comp_src.get(k, []), 'definition': DEFINITIONS.get(k, 'restricted cash: not spendable, excluded from net cash')}
    if res['long_term_investments']['value'] is None:
        res['long_term_investments']['value'] = inv['value']
    dr_end = end or bs_end
    if dr_end:
        drc, drn = q.best('deferred_revenue_current', dr_end), q.best('deferred_revenue_noncurrent', dr_end)
        res['deferred_revenue'] = {'value': (drc['value'] if drc else 0.0) + (drn['value'] if drn else 0.0) if drc or drn else None,
                                   'as_of': dr_end, 'components': {'current': drc['value'] if drc else None, 'noncurrent': drn['value'] if drn else None},
                                   'source_ids': sorted({x['source_id'] for x in (drc, drn) if x}), 'definition': DEFINITIONS['deferred_revenue']}
    else:
        res['deferred_revenue'] = {'value': None, 'as_of': None}
    return fld, inv, res


# ---------------------------------------------------------------- debt

def _debt_totals(q, end):
    """Candidate total debt at a date under each scope hypothesis; absent lines on a reported balance sheet are zero."""
    p = {m: q.best(m, end) for m in DEBT_PARTS}
    bs = q.has_bs(end)
    cur = p['current_portion_ltd']['value'] if p['current_portion_ltd'] else 0.0
    stb = p['short_term_borrowings']['value'] if p['short_term_borrowings'] else 0.0
    out = {}
    if p['long_term_debt']:
        out['noncurrent+current'] = p['long_term_debt']['value'] + cur + stb
    if p['long_term_debt_tagged']:
        out['tagged_total'] = p['long_term_debt_tagged']['value'] + stb
        out['tagged_plus_current'] = p['long_term_debt_tagged']['value'] + cur + stb
    if not out and (p['current_portion_ltd'] or p['short_term_borrowings']):
        out['noncurrent+current'] = cur + stb
    if not out and bs:
        out = {h: 0.0 for h in HYPOTHESES}               # balance sheet reported without any debt line
    return out, p


def _rollforward(q, pe, ce):
    kind = _span_kind(pe, ce)
    if not kind:
        return None
    tp, _ = _debt_totals(q, pe)
    tc, _ = _debt_totals(q, ce)
    iss, rep = q.best('debt_issued', ce, kind), q.best('debt_repaid', ce, kind)
    if not (iss or rep or any(q.best(m, ce, kind) for m in ('cfo', 'cff'))):
        return None                                       # no cash-flow statement for the span: cannot test
    flows = (abs(iss['value']) if iss else 0.0) - (abs(rep['value']) if rep else 0.0)
    ta = q.best('total_assets', ce)
    hyp = {}
    for h in HYPOTHESES:
        if h in tp and h in tc:
            exp = tp[h] + flows
            tol = max(ROLLFORWARD_TOL * max(abs(tp[h]), abs(tc[h])), .002 * abs(ta['value']) if ta else 0.0)
            hyp[h] = {'prior': tp[h], 'current': tc[h], 'expected': exp, 'residual': tc[h] - exp, 'tolerance': tol,
                      'pass': abs(tc[h] - exp) <= tol}
    if not hyp:
        return None
    passing = [h for h, r in hyp.items() if r['pass']]
    dist = 'tagged_total' in hyp and any(abs(hyp['tagged_plus_current'][k] - hyp['tagged_total'][k]) > hyp['tagged_total']['tolerance']
                                         for k in ('prior', 'current'))
    rec = {'prior': pe, 'current': ce, 'kind': kind, 'issued': abs(iss['value']) if iss else None,
           'repaid': abs(rep['value']) if rep else None, 'flows_absent_taken_as_zero': not (iss or rep),
           'hypotheses': hyp, 'passing': passing, 'distinguishable': dist}
    q.add('debt_rollforward', 'pass' if passing else 'fail', 'major',
          f'debt {pe} -> {ce}: prior + issued {_fmt(rec["issued"] or 0)} - repaid {_fmt(rec["repaid"] or 0)}; '
          + ', '.join(f"{h} residual {_fmt(r['residual'])} ({'pass' if r['pass'] else 'fail'})" for h, r in hyp.items()),
          detail=rec, period=f'{pe}..{ce}', fields=('debt',))
    return rec


def _tagged_winner(pr):
    w = [h for h in pr['passing'] if h in ('tagged_total', 'tagged_plus_current')]
    return w[0] if pr['distinguishable'] and len(w) == 1 else None


def _debt(q, cs, mcap):
    fb, ccy, notes, checks = q.fb, q.ccy, [], []
    bs_end = q.latest_bs()
    dates = []
    for m in DEBT_PARTS:
        for e, _ in q.timeline(m, 'instant'):
            if not any(abs(_days(x, e)) <= 7 for x in dates):
                dates.append(e)
    dates.sort()
    if dates and bs_end and _days(dates[-1], bs_end) > 7:
        dates.append(bs_end)       # debt lines gone from the latest balance sheet: test "repaid" with the roll-forward
        notes.append(f'no debt line on the latest balance sheet ({bs_end}); roll-forward tests whether it was repaid')
    leases = None
    pairs = [r for r in (_rollforward(q, a, b) for a, b in zip(dates, dates[1:])) if r]
    if pairs:
        checks.append('debt_rollforward')
    value = method = end = None
    decisive, basis, facts, status = False, None, [], None
    scope = {}
    if dates:
        end = dates[-1]
        tot, parts = _debt_totals(q, end)
        latest = pairs[-1] if pairs and pairs[-1]['current'] == end else None
        nc, tg = parts['long_term_debt'], parts['long_term_debt_tagged']
        if nc:
            method, decisive, basis = 'noncurrent+current', True, 'unambiguous_noncurrent_tag'
            if tg:
                cur = parts['current_portion_ltd']['value'] if parts['current_portion_ltd'] else 0.0
                scope['direct'] = ('tagged_plus_current' if _rel(tg['value'], nc['value']) <= q.tol['debt'] else
                                   'tagged_total' if _rel(tg['value'], nc['value'] + cur) <= q.tol['debt'] else 'inconsistent')
                if scope['direct'] == 'inconsistent':
                    notes.append(f"LongTermDebt tag {_fmt(tg['value'])} matches neither the noncurrent line nor noncurrent + current")
        elif tg:
            gap = abs(tot['tagged_plus_current'] - tot['tagged_total'])
            hist = [w for w in (_tagged_winner(pr) for pr in pairs) if w]
            if latest and _tagged_winner(latest):
                method, decisive, basis = _tagged_winner(latest), True, 'latest_rollforward'
            elif len(hist) >= 2 and len(set(hist)) == 1:
                method, decisive, basis = hist[0], True, 'history'
            elif gap <= q.tol['debt'] * max(abs(tot['tagged_total']), abs(tot['tagged_plus_current']), 1.0):
                method, decisive, basis = (hist[-1] if hist else 'tagged_total'), True, 'no_ambiguity'
            else:
                method, decisive, basis = 'tagged_plus_current', False, 'unresolved_conservative'
            scope['gap_between_hypotheses'] = gap
            if not decisive:
                material = gap > q.tol['debt'] * max(tot.values()) and (mcap is None or gap > .005 * mcap)
                scope['material'] = material
                notes.append(f"scope of the LongTermDebt tag unresolved (tagged {_fmt(tot['tagged_total'])} vs tagged + current "
                             f"{_fmt(tot['tagged_plus_current'])}); conservative choice: tagged + current"
                             + (' - material' if material else ' - immaterial'))
        elif tot:
            method, decisive, basis = 'noncurrent+current', True, 'current_or_none_tagged'
        if method:
            value = tot[method]
            use = {'noncurrent+current': ('long_term_debt', 'current_portion_ltd', 'short_term_borrowings'),
                   'tagged_total': ('long_term_debt_tagged', 'short_term_borrowings'),
                   'tagged_plus_current': ('long_term_debt_tagged', 'current_portion_ltd', 'short_term_borrowings')}[method]
            facts = [parts[m] for m in use if parts[m]]
            rf_ok = bool(latest and method in latest['passing'])
            sts = []
            for g in facts:
                s, c, n = _status_of(q, g['metric'], g, corroborated=rf_ok)
                sts.append(s)
                checks += c
                notes += n
                if 'lease' in (g.get('concept') or ''):
                    notes.append(f"{g['metric']} {g['period']} concept {g['concept']} includes finance-lease obligations")
                if 'fairvalue' in (g.get('concept') or ''):
                    sts.append('single_source')
                    notes.append(f"{g['metric']} uses a fair-value concept ({g['concept']}), not principal/carrying value")
            if not facts:
                sts.append('reported' if rf_ok else 'single_source')
                notes.append(f'no debt line on the balance sheet dated {end}: taken as zero'
                             + (' (consistent with the roll-forward)' if rf_ok else ''))
            status = _weakest(sts)
            if not decisive and scope.get('material'):
                status = _weakest([status, 'single_source'])
            # vendor total: definitional differences (leases, scope) are recorded, not conflicts
            vt = q.best('debt_total_vendor', end)
            lc, ln = q.v('operating_lease_current', end), q.v('operating_lease_noncurrent', end)
            if lc is not None or ln is not None:
                leases = (lc or 0.0) + (ln or 0.0)
            if vt:
                r = _rel(value, vt['value'])
                alt = {h: t for h, t in tot.items() if h != method and _rel(t, vt['value']) <= q.tol['debt']}
                why = ('vendor total includes operating lease liabilities' if leases and _rel(value + leases, vt['value']) <= q.tol['debt']
                       else f'vendor applies the {next(iter(alt))} scope; roll-forward supports {method}' if alt else None)
                ok = r <= q.tol['debt']
                q.add('cross_source', 'pass' if ok or why else 'fail', 'minor' if ok or why else 'major',
                      f"debt {end}: resolved {_fmt(value)} vs {q.provider(vt)} total {_fmt(vt['value'])}" + (f' ({why})' if why and not ok else ''),
                      period=end, fields=('debt',), detail={'resolved': value, 'vendor': vt['value'], 'rel_diff': round(r, 6), 'definition': why})
                checks.append('cross_source')
                indep = q.provider(vt) not in {q.provider(g) for g in facts}
                if ok and indep and status in ('reported', 'single_source') and decisive:
                    status = 'verified'
                elif why and not ok:
                    notes.append(f"{q.provider(vt)} total {_fmt(vt['value'])}: {why}")
                elif not ok and not rf_ok:
                    status = 'conflict'
                    notes.append(f"vendor total {_fmt(vt['value'])} disagrees with {_fmt(value)} and no roll-forward corroborates either")
            q.add('debt_scope', 'pass' if decisive or not scope.get('material') else 'fail', 'major',
                  f'debt at {end}: {method} = {_fmt(value)} ({basis})', period=end, fields=('debt',),
                  detail={'method': method, 'decisive': decisive, 'basis': basis, 'candidates': tot,
                          'parts': {m: (parts[m]['value'] if parts[m] else None) for m in DEBT_PARTS},
                          'history': [_tagged_winner(pr) for pr in pairs], **scope})
            checks.append('debt_scope')
    if value is None:
        fv = q.timeline('debt_fair_value', 'instant')
        if fv:     # APH: debt only tagged at fair value -> a proxy, never silently principal
            end, g = fv[-1]
            value, method, basis, facts, status = g['value'], 'fair_value_proxy', 'fair_value_only', [g], 'single_source'
            notes.append('only a fair-value debt figure is available: used as a proxy for principal (limited)')
        else:
            bs_ok = bs_end is not None
            ttm_int = F.ttm(fb, 'interest_expense')
            rev = F.ttm(fb, 'revenue')
            sig = ttm_int and ttm_int['value'] > 0 and (not rev or ttm_int['value'] > .002 * abs(rev['value']))
            if bs_ok and not sig:
                value, method, basis, end, status = 0.0, 'none_tagged', 'none_tagged', bs_end, 'reported'
                notes.append(f'no debt line on the balance sheet dated {bs_end} and no material interest expense: taken as zero')
            else:
                status = 'missing'
                notes.append('no debt line usable at as_of' + (f"; interest expense {_fmt(ttm_int['value'])} indicates debt exists but is not captured"
                                                               if sig else ''))
    stmt_value, stmt_end = value, end
    if cs:
        def expl(sv, cv):
            if leases and _rel(sv, cv - leases) <= q.tol['debt']:
                return 'capital structure includes lease lines as debt'
            return 'carrying value below principal (unamortized discount/issuance costs)' if 0 < cv - sv <= .03 * cv else None
        use, corr, conf, note = _cs_compare(q, cs, 'debt', 'debt', stmt_value, stmt_end,
                                            ('debt', {k for k in capital.DEBT_KINDS if k != 'lease'}), expl)
        notes.append(note)
        checks.append('capital_structure_crosscheck')
        if conf:
            status = 'conflict'
        elif corr and status == 'reported':
            status = 'verified'
        if use:
            lines = cs['lines']('debt', {k for k in capital.DEBT_KINDS if k != 'lease'})
            notes.append(f'statement debt at {stmt_end}: {_fmt(stmt_value)} ({method})' if stmt_end else 'no statement debt: capital structure only')
            value, method, basis, end = cs['debt_amount'](lines), 'capital_structure', 'capital_structure', cs['as_of']
            status = cs['grade'](lines) if lines else 'reported'
            if not corr and stmt_value is not None:
                status = _weakest([status, 'single_source'])
            facts, decisive = [], True
    fld = _field(q, 'debt', status or 'missing', value, ccy, end, facts, checks, notes, method=method, decisive=decisive,
                 decisive_basis=basis, lease_liabilities_excluded=leases)
    if value is not None:
        _stale(q, fld, 'balance_sheet', 'balance sheet')
    res = {'value': value, 'as_of': end, 'method': method, 'decisive': decisive, 'decisive_basis': basis, 'status': fld['status'],
           'source_ids': fld['sources'] or (cs['source_ids'] if cs and method == 'capital_structure' else []),
           'definition': DEFINITIONS['debt']}
    if cs and cs['summary'] and method == 'capital_structure':
        res['claims_incl_accretion'] = cs['summary']['debt_claims']
    lease = {'value': leases, 'as_of': stmt_end if leases is not None else None, 'included_in_debt': False,
             'definition': 'operating lease liabilities (current + noncurrent); excluded from debt and net cash'}
    return fld, res, lease


# ---------------------------------------------------------------- convertibles and post-period events

def _convertibles(q, cs):
    fb, ev = q.fb, []
    for f in fb['facts']:
        txt = (f['metric'] + ' ' + (f.get('concept') or '')).lower()
        if F.usable(fb, f) and (('convertible' in txt and f['value']) or f.get('conv')) and q.age(f['period_end']) <= q.max_age['annual']:
            ev.append({'kind': 'fact', **_ref(f)})
    ev += [{'kind': 'qualitative', 'id': x['id'], 'statement': x.get('statement')} for x in fb.get('qualitative', [])
           if x.get('topic') in CONVERT_TOPICS and q.dated(x, missing=True)]
    if fb['company'].get('conv') or fb['company'].get('has_convertibles'):
        ev.append({'kind': 'company_flag'})
    blocks, rows, gaps = False, [], []
    if cs:
        for x in cs['converts']:
            miss = [k for k in ('principal', 'conversion_price', 'maturity') if x.get(k) in (None, '')]
            if miss:
                gaps.append({'id': x['id'], 'missing_terms': miss})
                continue
            try:
                row = {'id': x['id'], 'principal': float(x['principal']), 'conversion_price': float(x['conversion_price']),
                       'maturity': x['maturity'], 'conversion_shares': capital.conversion_shares(x), 'source_id': x['source_id']}
                row['accreted_claim'] = capital.accreted_value(x, cs['as_of'])
            except (ValueError, KeyError, TypeError) as e:
                gaps.append({'id': x['id'], 'missing_terms': [f'usable terms ({e})']})
                continue
            rows.append(row)
        if gaps:
            status, blocks, note = 'missing', True, f'convertible instrument(s) without terms: {gaps}'
        elif rows:
            status, note = cs['grade']([x for x in cs['converts']]), f'{len(rows)} convertible instrument(s) with terms from the capital structure'
        elif ev:
            status, blocks, note = 'missing', True, f'evidence of convertibles ({len(ev)} item(s)) but the capital structure lists none'
        else:
            status, note = 'reported', f"capital structure as of {cs['as_of']} lists no convertible instruments"
    elif ev:
        status, blocks, note = 'missing', True, 'evidence of convertibles without instrument terms (supply a capital structure)'
    else:
        status, note = 'missing', 'no capital structure and no convertible evidence in the FactBook: confirm from the latest debt footnote'
    q.add('convertible_terms', 'fail' if blocks else 'pass' if status != 'missing' else 'skip', 'critical' if blocks else 'minor',
          note, detail={'evidence': ev, 'instruments': rows, 'gaps': gaps}, fields=('convertibles',))
    value = sum(r['principal'] for r in rows) if rows else (0.0 if status == 'reported' else None)
    fld = _field(q, 'convertibles', status, value, q.ccy, cs['as_of'] if cs else None, checks=['convertible_terms'], notes=[note],
                 instruments=rows, evidence=ev, conversion_shares=sum(r['conversion_shares'] for r in rows) if rows else None)
    fld['_blocks'] = blocks
    return fld


def _post_period(q, cs, bs_end):
    fb = q.fb
    items = [x for x in fb.get('qualitative', []) if x.get('topic') in POST_TOPICS and q.dated(x)]
    later = []
    if cs and bs_end:
        for g in ('cash', 'shares', 'debt', 'quasi_debt'):
            later += [{'group': g, 'id': x['id'], 'date': _line_date(x, cs), 'source_id': x['source_id']}
                      for x in cs['raw'].get(g, []) if _days(bs_end, _line_date(x, cs)) > 7]
    if items or later:
        kinds = {fb['sources'].get(s, {}).get('kind') for x in items for s in x.get('source_ids', [])} | \
                {fb['sources'].get(x['source_id'], {}).get('kind') for x in later}
        status = 'reported' if kinds & FILING_KINDS else 'single_source'
        note = f'{len(items)} recorded post-period event(s)' + (f'; {len(later)} capital-structure line(s) dated after {bs_end}' if later else '')
    else:
        status = 'missing'
        note = f'no post-period review recorded after the latest balance sheet ({bs_end}): check 8-K/6-K filings for issuance, converts, M&A, buybacks'
    q.add('post_period_review', 'pass' if status != 'missing' else 'skip', 'minor', note, fields=('post_period_events',),
          detail={'events': [{'id': x['id'], 'statement': x.get('statement'), 'kind': x.get('kind'), 'as_of': x['as_of']} for x in items],
                  'capital_structure_lines': later})
    fld = _field(q, 'post_period_events', status, len(items) + len(later) if status != 'missing' else None, 'count', bs_end,
                 checks=['post_period_review'], notes=[note], events=[x['id'] for x in items], capital_structure_lines=later)
    fld['_blocks'] = False
    return fld


# ---------------------------------------------------------------- TTM flows

def _ttm(q, metric):
    """facts.ttm, re-pointed at the best fact of each period.

    A vendor's month-end can shadow a 52/53-week filing end (facts.series keys periods by exact date), which makes
    facts.ttm fall back to the fiscal year or pick a vendor figure; quarters are re-clustered within 7 days here."""
    t = F.ttm(q.fb, metric)
    qs = q.timeline(metric, 'quarter')
    if (not t or t['method'] != 'quarters') and len(qs) >= 4:
        last4, a = qs[-4:], q.timeline(metric, 'annual')
        if 250 <= _days(last4[0][0], last4[-1][0]) <= 300 and (not a or _days(a[-1][0], last4[-1][0]) >= -7):
            t = {'period_end': last4[-1][0], 'method': 'quarters', 'facts': [f for _, f in last4],
                 'reclustered': 'providers date the same quarters differently; quarters matched within 7 days'}
    if not t:
        return None
    fs = [q.best(metric, f['period_end'], _kind(f)) or f for f in t['facts']]
    return {**t, 'facts': fs, 'value': sum(_val(metric, f) for f in fs)}


def _ttm_field(q, name, metric):
    t = _ttm(q, metric)
    if not t:
        return _field(q, name, 'missing', None, q.ccy, None, notes=[f'no {metric} usable at as_of'])
    sts, checks, notes = [], [], []
    for f in t['facts']:
        s, c, n = _status_of(q, metric, f)
        sts.append(s)
        checks += c
        notes += n
    fld = _field(q, name, _weakest(sts), t['value'], q.ccy, t['period_end'], t['facts'], checks,
                 notes + ([t['reclustered']] if t.get('reclustered') else []), method=t['method'], period_end=t['period_end'])
    if t['method'] == 'latest_fiscal_year':
        fld['note'] = '; '.join(x for x in (fld['note'], f"TTM is the fiscal year ended {t['period_end']} (no four contiguous quarters)") if x)
    _stale(q, fld, 'ttm', 'trailing period' + (' (quarterly data missing)' if t['method'] == 'latest_fiscal_year' else ''))
    return fld


# ---------------------------------------------------------------- entry point

def validate(fb, capital_structure=None, options=None):
    """Validate the critical fields of a FactBook at fb['as_of'] (docs/MODULE_CONTRACTS.md, dataqa.validate).

    capital_structure: capital.py structure (defaults to fb['capital_structure']); its instruments are the source
    for convertibles and a cross-check for cash, debt and shares (or the source, when newer than the statements).
    options: {'tolerances': {metric: relative}, 'max_age': {'shares'|'balance_sheet'|'ttm'|'annual': days}}."""
    q = _QA(fb, options)
    late = [f for f in fb['facts'] if not F.usable(fb, f)]
    est = sum(1 for f in fb['facts'] if F.usable(fb, f) and f.get('available_at_estimated'))
    q.add('lookahead_excluded', 'pass', 'minor', f'{len(late)} fact(s) published after {q.as_of} ignored; '
          f'{est} usable fact(s) carry estimated availability dates',
          detail={'excluded': len(late), 'excluded_metrics': sorted({f['metric'] for f in late}), 'estimated_availability': est})
    cs = _capital(q, fb.get('capital_structure') if capital_structure is None else capital_structure)
    _identities(q)
    fields, res = {}, {}
    fields['shares_outstanding'], res['shares_outstanding'], splits = _shares(q, cs)
    price, sh = q.price(), res['shares_outstanding']['value']
    mcap = price * sh if price and sh else None
    fields['cash_and_liquid_investments'], fields['investments'], cash_res = _cash(q, cs)
    res.update(cash_res)
    fields['debt'], res['debt'], res['lease_liabilities'] = _debt(q, cs, mcap)
    fields['convertibles'] = _convertibles(q, cs)
    fields['post_period_events'] = _post_period(q, cs, q.latest_bs())
    for name, metric in TTM_FIELDS:
        fields[name] = _ttm_field(q, name, metric)
    ends = [x[-1][0] for x in (q.timeline(m, 'annual') for m in ('revenue', 'cfo')) if x]
    if ends:
        age = q.age(max(ends))
        q.add('annual_recency', 'fail' if age > q.max_age['annual'] else 'pass', 'major' if age > q.max_age['annual'] else 'minor',
              f'latest fiscal year ended {max(ends)} ({age} days before {q.as_of})', detail={'latest_fiscal_year_end': max(ends), 'age_days': age})
    c, d = res['cash'], res['debt']
    if c['value'] is not None and d['value'] is not None:
        sti = res['short_term_investments']['value'] or 0.0
        lti = res['long_term_investments']['value'] or 0.0
        st = _weakest([fields['cash_and_liquid_investments']['status'], fields['debt']['status']])
        res['net_cash'] = {'value': c['value'] + sti - d['value'], 'as_of': c['as_of'], 'status': st,
                           'formula': f"cash {_fmt(c['value'])} + short_term_investments {_fmt(sti)} - debt {_fmt(d['value'])}",
                           'net_cash_incl_long_term_investments': c['value'] + sti + lti - d['value'],
                           'lease_liabilities_excluded': res['lease_liabilities']['value'],
                           'restricted_cash_excluded': res['restricted_cash']['value'], 'definition': DEFINITIONS['net_cash'],
                           'dates_differ': c['as_of'] != d['as_of'] and abs(_days(c['as_of'], d['as_of'])) > 7}
    else:
        res['net_cash'] = {'value': None, 'as_of': None, 'status': 'missing', 'definition': DEFINITIONS['net_cash']}
    res['definitions'] = dict(DEFINITIONS)
    blocking, limited = [], []
    for name in CRITICAL:
        fld = fields[name]
        blocks = fld.pop('_blocks', None)
        if blocks is None:
            blocks = name in BLOCKING and fld['status'] in ('conflict', 'missing')
        fld['blocking'] = blocks
        if blocks:
            blocking.append(name)
        elif fld['status'] in ('conflict', 'missing', 'stale', 'single_source') or fld.get('incomplete'):
            limited.append(name)
    return {'status': 'blocked' if blocking else 'limited' if limited else 'ok', 'as_of': q.as_of,
            'fields': fields, 'checks': q.checks, 'resolved': res, 'blocking': blocking, 'limited': limited,
            'splits': splits}
