"""Financial analysis: the measurements an excellent analyst reads, each with formula, inputs, period and status.

Why: margins, FCF, dilution and ROIC were computed four times with four definitions (engine.analyze,
plain._margins/_cash, inflection.features, scores.py). This stage is the pipeline's single source. It reads
the FactBook only through the point-in-time accessors of facts.py (nothing with available_at > as_of), takes
balance-sheet resolutions and field statuses from dataqa, reuses scores.py (Piotroski / Altman / Beneish /
ROIC / Rule of 40) through an adapter to history.py rows, and inflection's quarter spacing for acceleration.

Every metric carries its formula, the facts it used (source ids), its period and the weakest data status of
its inputs. A metric that cannot be computed is None with a reason, never zero or guessed. A ratio whose
denominator is not meaningful (invested capital that fell, losses) is None with the explanation instead of
an infinite or misleading number. Flags are observations with a severity for the analyst and the risk
register, never verdicts; positive observations (customer prepayments funding growth) are flagged too.

Definitions fixed here because they change numbers:
- debt = financial debt (notes, loans, commercial paper); operating leases excluded. The ambiguous
  LongTermDebt tag is read per year with dataqa's per-hypothesis roll-forward (prior and current debt under the
  same scope, prior + issued - repaid): the scope the adjacent year pairs support, else the scope every
  distinguishable pair supports, else the current portion is added (dataqa's conservative rule). A DebtCurrent tag
  is the current-debt total: short-term borrowings and commercial paper tagged beside it are not added again.
- share splits: exactly dataqa's qa['splits'] (positive evidence: a dated split, an as-traded price bar, weighted
  shares restated by the full ratio; factors incl. 3-for-2, with any residual issuance kept) are divided out of the
  year-end share counts; dataqa's qa['unresolved_splits'] stay in as issuance and mark the dilution unverified.
  Without a dataqa result dataqa's own split rule runs on the FactBook.
- net cash = cash + short-term investments - debt (dataqa's definition: long-term investments, restricted cash
  and leases shown separately, never counted as spendable cash).
- invested capital (used for ROIC) = equity + debt - cash - investments (financing side). The asset-side
  version NWC + PP&E + goodwill + intangibles is reported beside it; the gap is other operating assets
  and liabilities (leases, deferred taxes, other).
- payment lines (buybacks, dividends, acquisitions, debt flows) absent from a reported cash-flow statement,
  and investments/goodwill/intangibles absent from a reported balance sheet, are taken as zero (as-reported
  statements omit zero lines); every such fill is listed in `assumptions`.
- growth that coincides with a material acquisition is shown as reported and with that year excluded;
  revenue is never adjusted, lost sales are never added and recurring costs never removed.
"""
from datetime import date, timedelta
from math import isfinite, log
from statistics import stdev
from . import dataqa as DQ
from . import facts as F
from .history import history as check_history_rows, cagr, NONNEGATIVE
from .inflection import _yoy_list
from .scores import scorecards, roic as scores_roic

ANALYSIS_VERSION = 1
STATUS_ORDER = ('conflict', 'missing', 'stale', 'single_source', 'reported', 'verified')   # weakest first
KIND_STATUS = {'filing_derived': 'reported', 'issuer_document': 'reported', 'primary_document': 'reported', 'document': 'reported',
               'synthetic': 'reported'}
DUR = {'revenue': 'revenue', 'gross_profit': 'gross_profit', 'operating_income': 'operating_income',
       'net_income': 'net_income', 'cfo': 'cfo', 'capex': 'capex', 'sbc': 'sbc', 'd_and_a': 'd_and_a',
       'buybacks': 'buybacks', 'dividends': 'dividends_paid', 'acquisitions': 'acquisitions',
       'shares_diluted_wavg': 'shares_diluted_wavg', 'interest_expense': 'interest_expense',
       'pretax_income': 'pretax_income', 'income_tax': 'income_tax', 'sga': 'sga', 'sm': 'sm', 'ga': 'ga',
       'debt_issued': 'debt_issued', 'debt_repaid': 'debt_repaid'}
INST = ('cash', 'short_term_investments', 'long_term_investments', 'equity', 'goodwill', 'intangibles',
        'shares_outstanding', 'total_assets', 'current_assets', 'current_liabilities', 'total_liabilities',
        'receivables', 'ppe', 'deferred_revenue_current', 'deferred_revenue_noncurrent', 'long_term_debt',
        'long_term_debt_tagged', 'current_portion_ltd', 'short_term_borrowings', 'debt_total_vendor',
        'retained_earnings')
PAYMENTS = {'capex', 'buybacks', 'dividends', 'acquisitions', 'debt_issued', 'debt_repaid'}   # sign-normalised outflows
CF_ZERO = ('buybacks', 'dividends', 'acquisitions', 'debt_issued', 'debt_repaid')
BS_ZERO = ('short_term_investments', 'long_term_investments', 'goodwill', 'intangibles')
QA_FIELD = {'revenue': ('revenue_ttm',), 'operating_income': ('operating_income_ttm',), 'cfo': ('cfo_ttm',),
            'capex': ('capex_ttm',), 'sbc': ('sbc_ttm',), 'shares_outstanding': ('shares_outstanding',),
            'cash': ('cash_and_liquid_investments',), 'investments': ('cash_and_liquid_investments', 'investments'),
            'short_term_investments': ('cash_and_liquid_investments',), 'long_term_investments': ('investments',),
            'debt': ('debt',), 'net_cash': ('cash_and_liquid_investments', 'debt')}   # dataqa: investments = long-term
ANNUAL_KEYS = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'fcf', 'sbc',
               'fcf_after_sbc', 'd_and_a', 'buybacks', 'dividends', 'acquisitions', 'shares_diluted_wavg',
               'shares_outstanding', 'cash', 'investments', 'debt', 'equity', 'goodwill', 'intangibles',
               'invested_capital', 'deferred_revenue', 'nwc')
ANNUAL_EXTRA = ('invested_capital_asset_side', 'total_assets', 'current_assets', 'current_liabilities', 'ppe',
                'interest_expense', 'pretax_income', 'income_tax')
TTM_KEYS = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'sbc', 'd_and_a',
            'buybacks', 'dividends', 'acquisitions', 'interest_expense', 'pretax_income', 'income_tax')
HISTORY_MAP = {'gross_profit': 'gross_profit', 'operating_income': 'operating_income', 'net_income': 'net_income',
               'operating_cash_flow': 'cfo', 'pretax_income': 'pretax_income', 'income_tax': 'income_tax',
               'retained_earnings': 'retained_earnings', 'equity': 'equity', 'capex': 'capex',
               'shares_diluted': 'shares_diluted_wavg', 'total_assets': 'total_assets',
               'current_assets': 'current_assets', 'current_liabilities': 'current_liabilities',
               'total_liabilities': 'total_liabilities', 'receivables': 'receivables', 'sga': 'sga',
               'depreciation': 'd_and_a', 'ppe': 'ppe', 'total_debt': 'debt', 'cash': 'cash',
               'dividends_paid': 'dividends', 'buybacks': 'buybacks', 'interest_expense': 'interest_expense',
               'goodwill': 'goodwill'}
DERIVED = {'fcf': ('cfo', 'capex'), 'fcf_after_sbc': ('cfo', 'capex', 'sbc'), 'investments': ('short_term_investments',),
           'invested_capital': ('equity', 'debt', 'cash'), 'nwc': ('current_assets', 'cash', 'current_liabilities'),
           'invested_capital_asset_side': ('current_assets', 'cash', 'current_liabilities', 'ppe'),
           'deferred_revenue': ('deferred_revenue_current', 'deferred_revenue_noncurrent')}
TOL = 20                                  # days: two dates are the same fiscal year end (52/53-week years drift)
ACQ_PCT_REVENUE, GOODWILL_JUMP_PCT_PRIOR_REVENUE = .05, .10
RESTATED_BASE_PCT = .02                   # prior-year revenue restated by more than this: growth uses the restated base


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _days(a, b):
    return (_d(b) - _d(a)).days


def _weakest(statuses):
    ss = [s for s in statuses if s in STATUS_ORDER]
    return min(ss, key=STATUS_ORDER.index) if ss else 'missing'


def _fact_status(fb, f):
    if f['basis'] == 'normalized':
        return 'single_source'
    return KIND_STATUS.get(fb['sources'].get(f['source_id'], {}).get('kind'), 'single_source')


def _ref(fb, f, value=None):
    return {'metric': f['metric'], 'period': f['period'], 'period_end': f['period_end'],
            'value': f['value'] if value is None else value, 'source_id': f['source_id'], 'status': _fact_status(fb, f)}


def C(value=None, refs=()):
    """A cell: a value and the fact references it was computed from."""
    return {'value': value, 'refs': list(refs)}


def _lin(*terms):
    """Signed sum of cells; None when any term is missing."""
    if any(c['value'] is None for _, c in terms):
        return C(None, [r for _, c in terms for r in c['refs']])
    return C(sum(s * c['value'] for s, c in terms), [r for _, c in terms for r in c['refs']])


def _z(c):
    return c if c['value'] is not None else C(0.0)


def _zero(key, pe, evidence, statement):
    r = evidence['refs'][0] if evidence['refs'] else {'source_id': None, 'status': 'reported'}
    return C(0.0, [{'metric': key, 'period': 'assumed', 'period_end': pe, 'value': 0.0, 'source_id': r['source_id'],
                    'status': r['status'], 'assumption': f'line absent from a reported {statement}: taken as zero'}])


def _qa_status(qa, key, pe, latest):
    out = []
    for name in QA_FIELD.get(key, ()):
        fld = (qa.get('fields') or {}).get(name) or {}
        if fld.get('status') and (fld.get('as_of') == pe or (fld.get('as_of') is None and latest)):
            out.append((name, fld['status']))
    return out


def _apply_qa(cell, key, qa, pe, latest):
    """Critical fields take dataqa's validated status (verified / conflict / stale ...) for that period."""
    st = _qa_status(qa, key, pe, latest)
    if st and cell['refs']:
        s, names = _weakest(x for _, x in st), '+'.join(n for n, _ in st)
        cell['refs'] = [{**r, 'status': s, 'qa_field': names} for r in cell['refs']]
    return cell


def _nopat(c):
    """Operating income after tax: the rule of scores.roic (effective rate bounded 0..35%, else 21%)."""
    oi, tax, pti = c['operating_income']['value'], c['income_tax']['value'], c['pretax_income']['value']
    if oi is None:
        return None, None
    rate = min(max(tax / pti, 0), .35) if tax is not None and pti is not None and pti > 0 else .21
    return oi * (1 - rate), rate


def _rank(fb, f):
    kind = fb['sources'].get(f['source_id'], {}).get('kind')
    return (kind == 'secondary', F.BASIS_RANK[f['basis']], kind != 'filing_derived')


def _pick(series, pe):
    best = None
    for e, v, f in series:
        gap = abs(_days(pe, e))
        if gap <= TOL and (best is None or gap < best[0]):
            best = (gap, v, f)
    return best


def _year_ends(fb):
    s = F.series(fb, 'revenue', 'annual')
    if not s:
        s = sorted((x for m in DUR.values() for x in F.series(fb, m, 'annual')), key=lambda x: x[0])
    ends = []
    for e, _, f in s:
        if ends and _days(ends[-1][0], e) <= TOL:       # two sources dating the same fiscal year differently
            if _rank(fb, f) < _rank(fb, ends[-1][1]):
                ends[-1] = (e, f)
            continue
        ends.append((e, f))
    return [(e, f['period'] if f['period'].startswith('FY') else 'FY@' + e) for e, f in ends]


# ---------------------------------------------------------------- annual table

def _debt_pairs(fb, years):
    """dataqa's per-hypothesis debt roll-forward for each pair of consecutive fiscal year-ends: {(prior, current): record}."""
    q, out = DQ._QA(fb, None), {}
    for a, z in zip(years, years[1:]):
        rec = DQ._rollforward(q, a, z)
        if rec:
            out[(a, z)] = rec
    return out


def _debt(c, pe, prev, nxt, pairs):
    """Financial debt for one year-end and the method used (dataqa naming). The LongTermDebt tag's scope follows dataqa's
    per-hypothesis roll-forward of the year pairs around this year-end (each tests prior and current debt under the same
    scope), else the scope every distinguishable pair supports, else the conservative 'plus current'."""
    ltd, cur, stb, tag, ven = (c[k] for k in ('long_term_debt', 'current_portion_ltd', 'short_term_borrowings',
                                               'long_term_debt_tagged', 'debt_total_vendor'))
    if ltd['value'] is not None:
        return _lin((1, ltd), (1, _z(cur)), (1, _z(stb))), 'noncurrent+current', None
    if tag['value'] is not None:
        base = _lin((1, tag), (1, _z(stb)))
        if not cur['value']:
            return base, 'tagged_total', None
        plus = _lin((1, base), (1, cur))
        before, after = pairs.get((prev, pe)), pairs.get((pe, nxt))
        votes = [w for w in (DQ._tagged_winner(x) for x in (before, after) if x) if w]
        hist = [w for w in (DQ._tagged_winner(x) for x in pairs.values()) if w]
        pick = votes[0] if votes and len(set(votes)) == 1 else None
        exp = ((before or {}).get('hypotheses') or {}).get(pick or 'tagged_plus_current', {}).get('expected')
        detail = {'period_end': pe, 'tagged': tag['value'], 'current_portion': cur['value'], 'roll_forward_expected': exp,
                  'roll_forward_passing': {'pair_ending_here': before['passing'] if before else None,
                                           'pair_starting_here': after['passing'] if after else None}}
        if pick:
            return (plus if pick == 'tagged_plus_current' else base), pick, {
                **detail, 'decision': ('current portion added' if pick == 'tagged_plus_current' else 'current portion not added')
                + f": dataqa's per-hypothesis roll-forward supports {pick}"}
        if not votes and len(hist) >= 2 and len(set(hist)) == 1:
            return (plus if hist[0] == 'tagged_plus_current' else base), hist[0], {
                **detail, 'decision': f'{hist[0]}: the scope every distinguishable year pair supports (no pair around this year decides)'}
        if abs(cur['value']) <= .01 * max(abs(base['value']), 1.0):
            return base, 'tagged_total', {**detail, 'decision': 'current portion immaterial: scopes agree within 1%'}
        return plus, 'tagged_plus_current', {**detail, 'decision': 'current portion added (conservative, as dataqa): ' + (
            'the roll-forwards around this year disagree' if votes else 'no roll-forward distinguishes the scopes')}
    if ven['value'] is not None:
        return ven, 'vendor_total', None
    if cur['value'] is not None or stb['value'] is not None:
        return _lin((1, _z(cur)), (1, _z(stb))), 'current_only', None
    bs = next((c[k] for k in ('total_assets', 'equity', 'current_assets') if c[k]['value'] is not None), None)
    if bs is not None:
        return _zero('debt', pe, bs, 'balance sheet (no debt lines)'), 'none_reported', None
    return C(), None, None


def _annual(fb, qa):
    ye = _year_ends(fb)
    dur = {k: F.series(fb, m, 'annual') for k, m in DUR.items()}
    inst = {m: F.series(fb, m, 'instant') for m in INST}
    res = qa.get('resolved') or {}
    years, labels, cells, assumptions = [], {}, {}, []
    for i, (pe, label) in enumerate(ye):
        latest = i == len(ye) - 1
        c, filled = {}, []
        for k, s in dur.items():
            p = _pick(s, pe)
            c[k] = C(abs(p[1]) if k in PAYMENTS else p[1], [_ref(fb, p[2], abs(p[1]) if k in PAYMENTS else None)]) if p else C()
        for m, s in inst.items():
            p = _pick(s, pe)
            c[m] = C(p[1], [_ref(fb, p[2])]) if p else C()
        cp = _pick(inst['current_portion_ltd'], pe)
        if cp and (cp[2].get('concept') or '').lower() == 'debtcurrent' and c['short_term_borrowings']['value'] is not None:
            stb = c['short_term_borrowings']['value']
            extra = DQ._current_debt(cp[2], {'value': stb})[1]      # DebtCurrent already includes borrowings / commercial paper
            assumptions.append({'code': 'debt_current_total', 'period_end': pe, 'debt_current': cp[1], 'short_term_borrowings': stb,
                                'added': extra, 'note': 'DebtCurrent is the current-debt total (includes short-term borrowings and '
                                                        'commercial paper): only an excess over it is added'})
            c['short_term_borrowings'] = C(extra, [{**r, 'value': extra} for r in c['short_term_borrowings']['refs']])
        bs = next((c[k] for k in ('total_assets', 'equity', 'current_assets') if c[k]['value'] is not None), None)
        for keys, evidence, statement in ((CF_ZERO, c['cfo'], 'cash-flow statement'), (BS_ZERO, bs, 'balance sheet')):
            if evidence is None or evidence['value'] is None:
                continue
            for k in keys:
                if c[k]['value'] is None:
                    c[k] = _zero(k, pe, evidence, statement)
                    filled.append(k)
        if c['sga']['value'] is None and c['sm']['value'] is not None and c['ga']['value'] is not None:
            c['sga'] = _lin((1, c['sm']), (1, c['ga']))
        if filled:
            assumptions.append({'code': 'absent_line_as_zero', 'period_end': pe, 'keys': sorted(filled)})
        years.append(pe)
        labels[pe] = label
        cells[pe] = c
        c['_latest'] = latest
    pairs = _debt_pairs(fb, years)
    for i, pe in enumerate(years):
        c = cells[pe]
        debt, method, detail = _debt(c, pe, years[i - 1] if i else None, years[i + 1] if i + 1 < len(years) else None, pairs)
        r = res.get('debt') or {}
        if r.get('value') is not None and r.get('as_of') == pe and (
                debt['value'] is None or abs(debt['value'] - r['value']) > .005 * max(abs(r['value']), 1.0)):
            # dataqa validated the latest balance sheet (capital structure, roll-forward): its resolution wins
            assumptions.append({'code': 'debt_from_dataqa', 'period_end': pe, 'annual_resolution': debt['value'],
                                'dataqa': r['value'], 'dataqa_method': r.get('method'),
                                'note': 'dataqa resolution used for this year-end'})
            debt, method, detail = C(float(r['value']), [{'metric': 'dataqa.resolved.debt', 'period': 'I@' + pe, 'period_end': pe,
                                                          'value': float(r['value']), 'source_id': 'dataqa.resolved',
                                                          'status': 'reported'}]), r.get('method') or method, None
        if detail:
            assumptions.append({'code': 'debt_scope', 'method': method, **detail})
        c['debt'], c['_debt_method'] = _apply_qa(debt, 'debt', qa, pe, c['_latest']), method
    for pe in years:
        c, latest = cells[pe], cells[pe]['_latest']
        for k in ('revenue', 'operating_income', 'cfo', 'capex', 'sbc', 'shares_outstanding', 'cash'):
            _apply_qa(c[k], k, qa, pe, latest)
        sti, lti = c['short_term_investments'], c['long_term_investments']
        c['investments'] = C() if sti['value'] is None and lti['value'] is None else _lin((1, _z(sti)), (1, _z(lti)))
        _apply_qa(c['investments'], 'investments', qa, pe, latest)
        c['fcf'] = _lin((1, c['cfo']), (-1, c['capex']))
        c['fcf_after_sbc'] = _lin((1, c['fcf']), (-1, c['sbc']))
        dc, dn = c['deferred_revenue_current'], c['deferred_revenue_noncurrent']
        c['deferred_revenue'] = C() if dc['value'] is None and dn['value'] is None else _lin((1, _z(dc)), (1, _z(dn)))
        c['invested_capital'] = _lin((1, c['equity']), (1, c['debt']), (-1, c['cash']), (-1, _z(c['investments'])))
        cur_debt = _lin((1, _z(c['current_portion_ltd'])), (1, _z(c['short_term_borrowings'])))
        c['nwc'] = _lin((1, c['current_assets']), (-1, c['cash']), (-1, _z(sti)), (-1, c['current_liabilities']), (1, cur_debt))
        c['invested_capital_asset_side'] = _lin((1, c['nwc']), (1, c['ppe']), (1, _z(c['goodwill'])), (1, _z(c['intangibles'])))
    return years, labels, cells, assumptions


# ---------------------------------------------------------------- metric helpers

def _dedupe(refs):
    seen, out = set(), []
    for r in refs:
        k = (r.get('metric'), r.get('period_end'), r.get('source_id'), r.get('value'))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _m(value, unit, formula, refs, period, reason=None, missing=(), **extra):
    refs = _dedupe(refs)
    if missing:
        value, status, reason = None, 'missing', reason or 'missing input: ' + ', '.join(missing)
    else:
        status = _weakest(r['status'] for r in refs)
    out = {'value': value, 'unit': unit, 'formula': formula, 'inputs': refs, 'period': period, 'status': status}
    if value is None:
        out['reason'] = reason or 'not computable from the available facts'
    out.update(extra)
    return out


def _back(years, pe, n):
    """The fiscal year ending n years before pe (same fiscal calendar), or None."""
    if pe is None:
        return None
    target = _d(pe) - timedelta(days=round(n * 365.25))
    best = min(((abs((_d(y) - target).days), y) for y in years if y < pe), default=None)
    return best[1] if best and best[0] <= TOL else None


def _step_ok(a, b):
    return 350 <= _days(a, b) <= 380


def _span(labels, a, b):
    return f'{labels[a]}..{labels[b]}'


# ---------------------------------------------------------------- quarterly

def _quarterly(fb):
    q = F.series(fb, 'revenue', 'quarter')
    if len(q) < 5:
        return {'status': 'unavailable', 'value': None,
                'reason': f'needs at least 5 standalone quarters of revenue; {len(q)} available'}
    yoy = _yoy_list([(e, v) for e, v, _ in q])
    if not yoy:
        return {'status': 'unavailable', 'value': None,
                'reason': 'no quarter has a comparable quarter 340-390 days earlier'}
    byend = {e: (v, f) for e, v, f in q}
    ends = [e for e, _, _ in q]
    last = yoy[-1][0]
    i = ends.index(last)
    prev = ends[i - 4]
    refs = [_ref(fb, byend[last][1]), _ref(fb, byend[prev][1])]
    out = {'value': yoy[-1][1], 'unit': 'ratio', 'period': last,
           'formula': 'revenue[q] / revenue[q-4] - 1 for standalone quarters 340-390 days apart (inflection._yoy_list); '
                      'acceleration_2q = yoy[q] - yoy[q-2]; incremental margin = change in profit / change in revenue '
                      'versus the same quarter a year earlier (revenue change > 2%)',
           'yoy_series': [{'period_end': e, 'yoy': g} for e, g in yoy]}
    if len(yoy) >= 3:
        a, b, c = yoy[-3][1], yoy[-2][1], yoy[-1][1]
        streak = 0
        for k in range(len(yoy) - 1, 0, -1):
            if yoy[k][1] > yoy[k - 1][1]:
                streak += 1
            else:
                break
        out.update({'acceleration_2q': c - a, 'accelerating_two_quarters': c > b > a,
                    'decelerating_two_quarters': c < b < a, 'acceleration_streak': streak})
    else:
        out.update({'acceleration_2q': None, 'acceleration_reason': 'needs three quarters with a year-earlier comparison'})
    dr = byend[last][0] - byend[prev][0]
    for key, metric in (('incremental_operating_margin_yoy', 'operating_income'), ('incremental_gross_margin_yoy', 'gross_profit')):
        s = {e: (v, f) for e, v, f in F.series(fb, metric, 'quarter')}
        if last in s and prev in s and byend[prev][0] > 0 and dr > .02 * byend[prev][0]:
            out[key] = (s[last][0] - s[prev][0]) / dr
            refs += [_ref(fb, s[last][1]), _ref(fb, s[prev][1])]
        else:
            out[key] = None
    if len(q) >= 8 and all(80 <= _days(ends[k - 1], ends[k]) <= 120 for k in range(len(ends) - 7, len(ends))):
        now, before = sum(v for _, v, _ in q[-4:]), sum(v for _, v, _ in q[-8:-4])
        out['ttm_growth'] = now / before - 1 if before > 0 else None
        s = {e: v for e, v, _ in F.series(fb, 'operating_income', 'quarter')}
        if all(e in s for e in ends[-8:]) and now - before > .02 * before > 0:
            out['incremental_operating_margin_ttm'] = (sum(s[e] for e in ends[-4:]) - sum(s[e] for e in ends[-8:-4])) / (now - before)
    out['inputs'] = _dedupe(refs)
    out['status'] = _weakest(r['status'] for r in out['inputs'])
    out['note'] = 'one quarter is not an inflection: acceleration counts only when two consecutive quarters improve'
    return out


# ---------------------------------------------------------------- TTM and resolved balance

def _ytd_flow(fb, metric, after, gaps=False):
    """{period_end: (value, facts, quarters missing)} of a flow since the fiscal year ending `after`: running sums of
    contiguous standalone quarters (gaps=True skips a missing quarter and counts it: the sum is then a lower bound), and
    year-to-date facts ending 8..366 days later (preferred over a lower bound). Payments by magnitude."""
    out, prev, run, used, missing = {}, _d(after), 0.0, [], 0
    for e, v, f in F.series(fb, metric, 'quarter'):
        if _d(e) <= _d(after) + timedelta(days=7):
            continue
        if _days(after, e) > 373:
            break
        gap = (_d(e) - prev).days
        if not 60 <= gap <= 120:
            if not gaps or gap < 60:
                break
            missing += max(1, round(gap / 91.3) - 1)
        prev, run, used = _d(e), run + F._fv(metric, f), used + [f]
        out[e] = (run, list(used), missing)
    for f in F.candidates(fb, metric):
        e = f['period_end']
        if f['period'].startswith('YTD@') and 7 < _days(after, e) <= 366 and (e not in out or out[e][2]):
            out[e] = (F._fv(metric, f), [f], 0)
    return out


def _resolved(qa):
    res, fields, out = qa.get('resolved') or {}, qa.get('fields') or {}, {}
    for k in ('shares_outstanding', 'cash', 'short_term_investments', 'long_term_investments', 'debt', 'net_cash',
              'deferred_revenue'):
        r = res.get(k)
        if not isinstance(r, dict) or (r.get('value') is None and k != 'net_cash'):
            continue
        flds = [fields[n] for n in QA_FIELD.get(k, ()) if n in fields and fields[n].get('status')]
        sts = [f['status'] for f in flds] + ([r['status']] if r.get('status') else [])
        status = _weakest(sts) if sts else ('single_source' if r.get('basis') == 'vendor_float' else 'reported')
        srcs = set(r.get('source_ids') or []) | ({r['source_id']} if r.get('source_id') else set()) | {
            x for f in flds for x in f.get('sources') or []}
        val = None if r.get('value') is None else float(r['value'])
        out[k] = C(val, [{'metric': 'dataqa.resolved.' + k, 'period': 'I@' + str(r.get('as_of')), 'period_end': r.get('as_of'),
                          'value': val, 'source_id': 'dataqa.resolved', 'status': status, 'sources': sorted(srcs),
                          'basis': r.get('basis') or r.get('method')}])
        out[k]['as_of'], out[k]['definition'] = r.get('as_of'), r.get('definition') or r.get('formula')
    return out


def _ttm(fb, qa, years, labels, cells):
    base = F.ttm(fb, 'revenue')
    if not base:
        return {'period_end': None, 'method': None, 'label': None, 'reason': 'no revenue facts'}, {}
    pe, method = base['period_end'], base['method']
    label = labels.get(pe) if method == 'latest_fiscal_year' and labels.get(pe) else f'TTM@{pe}'
    info, tc, excluded = {'period_end': pe, 'method': method, 'label': label}, {}, {}
    for key in TTM_KEYS:
        t = F.ttm(fb, DUR[key])
        if t and t['period_end'] == pe and t['method'] == method:
            v = abs(t['value']) if key in PAYMENTS else t['value']
            tc[key] = _apply_qa(C(v, [_ref(fb, f) for f in t['facts']]), key, qa, pe, True)
        else:
            tc[key] = C()
            if t:
                excluded[key] = {'period_end': t['period_end'], 'method': t['method'],
                                 'reason': 'different period from the revenue TTM; not mixed'}
    if tc['cfo']['value'] is not None:
        for k in ('buybacks', 'dividends', 'acquisitions'):
            if tc[k]['value'] is None and k not in excluded:
                tc[k] = _zero(k, pe, tc['cfo'], 'cash-flow statement')
    tc['fcf'] = _lin((1, tc['cfo']), (-1, tc['capex']))
    tc['fcf_after_sbc'] = _lin((1, tc['fcf']), (-1, tc['sbc']))
    for k, c in tc.items():
        info[k] = c['value']
    rev = info['revenue']
    for k, num in (('gross_margin', 'gross_profit'), ('operating_margin', 'operating_income'), ('fcf_margin', 'fcf'),
                   ('fcf_after_sbc_margin', 'fcf_after_sbc'), ('sbc_pct_revenue', 'sbc')):
        info[k] = info[num] / rev if info.get(num) is not None and rev and rev > 0 else None
    if method == 'latest_fiscal_year' and years and pe == years[-1]:
        info['shares_diluted_wavg'] = cells[years[-1]]['shares_diluted_wavg']['value']
    rs = {k: c for k, c in _resolved(qa).items() if c['value'] is not None}
    if rs:
        info['balance_basis'] = 'dataqa.resolved'
        info['balance_as_of'] = {k: c.get('as_of') for k, c in rs.items()}
        for k in ('shares_outstanding', 'cash', 'debt', 'net_cash', 'deferred_revenue'):
            info[k] = rs[k]['value'] if k in rs else None
        if 'short_term_investments' in rs or 'long_term_investments' in rs:
            info['investments'] = sum(rs[k]['value'] for k in ('short_term_investments', 'long_term_investments') if k in rs)
        else:
            info['investments'] = None
    elif years:
        c = cells[years[-1]]
        info['balance_basis'] = 'latest_fiscal_year'
        info['balance_as_of'] = {k: years[-1] for k in ('shares_outstanding', 'cash', 'investments', 'debt', 'deferred_revenue')}
        for k in ('shares_outstanding', 'cash', 'investments', 'debt', 'deferred_revenue'):
            info[k] = c[k]['value']
        sti = c['short_term_investments']['value'] or 0
        info['net_cash'] = None if None in (info['cash'], info['debt']) else info['cash'] + sti - info['debt']
    if excluded:
        info['excluded'] = excluded
    return info, tc


# ---------------------------------------------------------------- history rows (adapter to history.py / scores.py)

def _history_rows(years, labels, cells):
    rows, dropped = [], []
    for pe in years:
        c = cells[pe]
        rev = c['revenue']['value']
        if rev is None or rev < 0:
            continue
        r, used = {'fiscal_year': labels[pe], 'period_end': pe, 'revenue': rev}, [c['revenue']]
        for hk, ck in HISTORY_MAP.items():
            v = c[ck]['value']
            if v is None:
                continue
            if hk in NONNEGATIVE and v < 0:
                dropped.append({'period_end': pe, 'field': hk, 'value': v})
                continue
            r[hk] = v
            used.append(c[ck])
        r['source_ids'] = sorted({x['source_id'] for u in used for x in u['refs'] if x.get('source_id')}) or ['factbook']
        rows.append(r)
    return rows, dropped


def _forensics(rows, as_of, archetype, market_cap, roic_block):
    if len(rows) < 2:
        return {'status': 'unavailable', 'reason': f'needs at least two fiscal years with revenue; {len(rows)} available'}
    try:
        check_history_rows({'history': rows[-15:], 'as_of': as_of})
        out = scorecards(rows[-15:], archetype or 'nonfinancial', market_cap)
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        return {'status': 'unavailable', 'reason': f'scores.py could not run on the history rows: {e}'}
    if roic_block is not None:
        out['roic'] = roic_block
    out['status'] = 'computed'
    out['archetype'] = archetype or 'nonfinancial'
    out['note'] = 'screens that direct research; not verdicts, verified facts or return odds (market cap: see an.market_cap)'
    return out


# ---------------------------------------------------------------- main

def analyze(fb, qa=None, archetype=None, market_cap=None):
    """FactBook + dataqa result -> annual table, TTM, metrics with formulas/inputs/status, forensics, flags.

    archetype is a valuation.ROUTES key (scores.py uses it for Altman Z vs Z'' and Rule of 40); market_cap
    defaults to price x dataqa-resolved shares when both are available."""
    qa = qa or {}
    years, labels, cells, assumptions = _annual(fb, qa)
    ttm, tc = _ttm(fb, qa, years, labels, cells)
    mismatch = []
    for key, name in (('revenue', 'revenue_ttm'), ('operating_income', 'operating_income_ttm'), ('cfo', 'cfo_ttm'),
                      ('capex', 'capex_ttm'), ('sbc', 'sbc_ttm')):
        fld = (qa.get('fields') or {}).get(name) or {}
        mine = ttm.get(key)
        if fld.get('value') is not None and mine is not None and fld.get('period_end') == ttm.get('period_end') \
                and abs(abs(fld['value']) - abs(mine)) > 1e-9 * max(abs(mine), 1.0):
            mismatch.append({'field': name, 'dataqa': fld['value'], 'analysis': mine, 'period_end': ttm['period_end']})
    if mismatch:
        ttm['dataqa_mismatch'] = mismatch
    rows, dropped = _history_rows(years, labels, cells)
    if dropped:
        assumptions.append({'code': 'history_field_dropped', 'fields': dropped,
                            'note': 'negative value in a field history.py requires to be non-negative'})
    mc_basis = 'supplied' if market_cap is not None else None
    if market_cap is None:
        price = (fb.get('market') or {}).get('price') or {}
        sh = ((qa.get('resolved') or {}).get('shares_outstanding') or {}).get('value')
        if price.get('value') and sh and price.get('currency', fb['company']['currency']) == fb['company']['currency'] \
                and price.get('as_of') and _d(price['as_of']) <= _d(fb['as_of']):          # never a later quote
            market_cap, mc_basis = price['value'] * sh, f"price {price['value']} ({price.get('as_of')}) x dataqa-resolved shares {sh}"
    # ROIC: scores.roic on adapter rows whose 'cash' is cash + investments, so its equity + debt - cash equals
    # the invested capital defined here (one ROIC computation, not two competing ones)
    roic_rows = []
    for r in rows:
        x = dict(r)
        if x.get('cash') is not None:
            x['cash'] = x['cash'] + (cells[r['period_end']]['investments']['value'] or 0)
        roic_rows.append(x)
    rs = scores_roic(roic_rows) if roic_rows else {'status': 'unavailable', 'reason': 'no fiscal years with revenue'}
    if rs.get('status') == 'computed':
        rs['invested_capital_definition'] = 'equity + debt - cash - investments (financing side; analysis.py)'
    metrics, acq = _metrics(fb, qa, years, labels, cells, ttm, tc, rs)
    forensics = _forensics(rows, fb['as_of'], archetype, market_cap, rs if rs.get('status') == 'computed' else None)
    flags = _flags(years, labels, cells, metrics, acq, forensics)
    for x in ttm.get('dataqa_mismatch') or []:
        flags.append({'code': 'ttm_inconsistent', 'severity': 'high', 'kind': 'data', 'evidence': x,
                      'message': f"{x['field']} differs between dataqa ({x['dataqa']:,.0f}) and analysis ({x['analysis']:,.0f}) "
                                 f"for the period ending {x['period_end']}: one TTM computation is wrong; do not use either"})
    annual = {}
    for pe in years:
        c = cells[pe]
        row = {k: c[k]['value'] for k in ANNUAL_KEYS}
        row.update({k: c[k]['value'] for k in ANNUAL_EXTRA})
        row['fiscal_period'], row['debt_method'] = labels[pe], c['_debt_method']
        annual[pe] = row
    out = {'analysis_version': ANALYSIS_VERSION, 'as_of': fb['as_of'], 'currency': fb['company'].get('currency'),
           'years': years, 'annual': annual, 'ttm': ttm, 'metrics': metrics, 'forensics': forensics, 'flags': flags,
           'history_rows': rows, 'acquisition_years': acq, 'assumptions': assumptions,
           'market_cap': {'value': market_cap, 'basis': mc_basis or 'unavailable: needs a price dated on or before as_of and '
                          'dataqa-resolved shares', 'used_for': 'Altman Z (public manufacturers) in forensics'},
           'definitions': {'invested_capital': 'equity + debt - cash - investments (used for ROIC)',
                           'invested_capital_asset_side': 'NWC + PP&E + goodwill + intangibles (cross-check)',
                           'nwc': 'current assets - cash - short-term investments - (current liabilities - current debt)',
                           'debt': 'financial debt excluding operating leases; see assumptions.debt_scope',
                           'fcf': 'cfo - capex', 'fcf_after_sbc': 'fcf - sbc',
                           'status': 'weakest input status: ' + ' < '.join(STATUS_ORDER)}}
    return _clean(out)


def _clean(o):
    if isinstance(o, float):
        return o if isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not str(k).startswith('_')}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _metrics(fb, qa, years, labels, cells, ttm, tc, rs):
    M = {}
    last = years[-1] if years else None
    g = lambda pe, k: cells[pe][k] if pe in cells else C()
    v = lambda pe, k: g(pe, k)['value']
    lab = lambda pe: labels.get(pe, '?')
    b3, b5 = _back(years, last, 3), _back(years, last, 5)
    w3 = [y for y in years if 0 <= _days(y, last) <= 2 * 365.25 + TOL] if last else []   # the last 3 fiscal years

    def missing(pe, *keys):
        """Names of absent inputs; a derived value is expanded into the raw facts it lacks."""
        out = []
        for k in keys:
            if v(pe, k) is None:
                raw = [x for x in DERIVED.get(k, ()) if v(pe, x) is None]
                out.append(f"{k} {lab(pe)}" + (f" (no {', '.join(raw)})" if raw else ''))
        return out

    def ratio(pe, num, den, formula, unit='ratio', cn=None, cd=None):
        n, d = cn or g(pe, num), cd or g(pe, den)
        miss = missing(pe, num, den) if pe else ['fiscal years']
        if miss:
            return _m(None, unit, formula, n['refs'] + d['refs'], lab(pe) if pe else None, missing=miss)
        if d['value'] <= 0:
            return _m(None, unit, formula, n['refs'] + d['refs'], lab(pe), f'{den} not positive ({d["value"]:.6g}): ratio not meaningful')
        return _m(n['value'] / d['value'], unit, formula, n['refs'] + d['refs'], lab(pe))

    def need_back(name, n, b, unit, formula):
        if not years:
            return _m(None, unit, formula, [], None, missing=['fiscal years'])
        avail = (_d(last) - _d(years[0])).days / 365.25
        return _m(None, unit, formula, [], None, f'needs a fiscal year ending about {n} years before {lab(last)} '
                  f'on the same fiscal calendar; available span {avail:.1f} years')

    # ---- growth
    steps = [(years[i - 1], years[i]) for i in range(1, len(years)) if _step_ok(years[i - 1], years[i])]
    comp = [(f['period_end'], f['value']) for f in F.candidates(fb, 'revenue_prior_comparable')]

    def base_of(a, z):
        """Revenue of year a as the base for growth into year z: the comparative column of z's annual report when a later
        divestiture or discontinued operation restated a by more than RESTATED_BASE_PCT (like for like), else a's own
        first-filed revenue. -> (base, restated?)"""
        ra = v(a, 'revenue')
        c = next((x for e, x in comp if abs(_days(e, z)) <= TOL), None)
        if c and ra and c > 0 and abs(c / ra - 1) > RESTATED_BASE_PCT:
            return c, True
        return ra, False
    acq, acq_set = [], set()
    for i, pe in enumerate(years):
        rev, a = v(pe, 'revenue'), v(pe, 'acquisitions')
        prev = years[i - 1] if i and _step_ok(years[i - 1], pe) else None
        apct = a / rev if a is not None and rev and rev > 0 else None
        gj = None
        if prev and None not in (v(pe, 'goodwill'), v(prev, 'goodwill')) and v(prev, 'revenue') and v(prev, 'revenue') > 0:
            gj = (v(pe, 'goodwill') - v(prev, 'goodwill')) / v(prev, 'revenue')
        if (apct is not None and apct > ACQ_PCT_REVENUE) or (gj is not None and gj > GOODWILL_JUMP_PCT_PRIOR_REVENUE):
            acq.append({'period_end': pe, 'fiscal_period': lab(pe), 'acquisitions_pct_revenue': apct,
                        'goodwill_increase_pct_prior_revenue': gj, 'growth_step_in_table': prev is not None,
                        'note': 'growth into this year may include bought revenue; the next year may carry its annualisation'})
            if prev is not None:
                acq_set.add(pe)

    def growth_cagr(name, n, b):
        f = f'(revenue[{lab(last)}] / revenue[{n} fiscal years earlier]) ^ (1/{n}) - 1; same fiscal calendar'
        if b is None:
            return need_back(name, n, b, 'ratio', f)
        r0, r1 = g(b, 'revenue'), g(last, 'revenue')
        val = cagr(r0['value'], r1['value'], n)
        window = [y for y in years if b <= y <= last]
        excl = [y for y in window[1:] if y in acq_set]
        detail = {'reported': val, 'excluded_acquisition_years': [lab(y) for y in excl]}
        pairs = list(zip(window, window[1:]))
        restated = [z for a, z in pairs if base_of(a, z)[1]]
        if restated and len(pairs) == n and all(_step_ok(a, z) for a, z in pairs) and all(base_of(a, z)[0] and base_of(a, z)[0] > 0
                                                                                        and v(z, 'revenue') is not None for a, z in pairs):
            # a divestiture restated part of the window: chain like-for-like steps (each year's growth on its own report's
            # comparative), never first-filed revenue with the sold business against revenue without it
            prod = 1.0
            for a, z in pairs:
                prod *= v(z, 'revenue') / base_of(a, z)[0]
            detail.update(first_filed=val, restated_bases=[lab(z) for z in restated],
                          like_for_like_note='steps into ' + ', '.join(lab(z) for z in restated) + ' use the prior year as restated '
                          'in that year\'s annual report (divestiture or discontinued operation)')
            val = detail['reported'] = prod ** (1 / n) - 1 if prod > 0 else None
            f += '; like-for-like steps where a later report restated the prior year'
        if excl:
            ws = [(a, z) for a, z in pairs if z not in acq_set]
            ok = all(_step_ok(a, z) for a, z in pairs) and ws and all(base_of(a, z)[0] and base_of(a, z)[0] > 0 for a, z in ws)
            prod = 1.0
            for a, z in ws:
                prod *= v(z, 'revenue') / base_of(a, z)[0] if ok else 1
            detail['ex_acquisition_years'] = prod ** (1 / len(ws)) - 1 if ok and prod > 0 else None
            detail['note'] = ('ex_acquisition_years drops the whole growth step of each flagged year (organic growth in '
                              'those years is dropped too); revenue itself is never adjusted')
        else:
            detail['ex_acquisition_years'] = val
        reason = None if val is not None else 'revenue not positive at an endpoint'
        return _m(val, 'ratio', f, r0['refs'] + r1['refs'], _span(labels, b, last), reason, detail=detail)

    M['revenue_cagr_3y'] = growth_cagr('revenue_cagr_3y', 3, b3)
    M['revenue_cagr_5y'] = growth_cagr('revenue_cagr_5y', 5, b5)
    if len(years) >= 2 and all(_step_ok(a, z) for a, z in zip(years, years[1:])):
        n = len(years) - 1
        M['revenue_cagr_full'] = _m(cagr(v(years[0], 'revenue'), v(last, 'revenue'), n), 'ratio',
                                    f'(revenue[last] / revenue[first]) ^ (1/{n}) - 1 over all {n + 1} fiscal years',
                                    g(years[0], 'revenue')['refs'] + g(last, 'revenue')['refs'], _span(labels, years[0], last),
                                    'revenue not positive at an endpoint')
    else:
        M['revenue_cagr_full'] = _m(None, 'ratio', 'CAGR over all fiscal years', [], None,
                                    'needs two or more consecutive fiscal years on one fiscal calendar')
    series, srefs = [], []
    for a, z in steps:
        (ra, rst), rz = base_of(a, z), v(z, 'revenue')
        if ra and ra > 0 and rz is not None:
            series.append({'period_end': z, 'fiscal_period': lab(z), 'growth': rz / ra - 1,
                           'acquisition_year': z in acq_set, **({'restated_base': ra} if rst else {})})
            srefs += g(a, 'revenue')['refs'] + g(z, 'revenue')['refs']
    M['revenue_growth_series'] = _m(series or None, 'ratio', 'revenue[t] / revenue[t-1] - 1 for consecutive fiscal years (350-380 days apart)',
                                    srefs, _span(labels, years[0], last) if years else None, 'needs two consecutive fiscal years')
    lastg = series[-1] if series and series[-1]['period_end'] == last else None
    M['revenue_growth_last'] = _m(lastg['growth'] if lastg else None, 'ratio', 'revenue[t] / revenue[t-1] - 1',
                                  g(last, 'revenue')['refs'] + (g(years[-2], 'revenue')['refs'] if len(years) > 1 else []),
                                  lab(last) if last else None, 'needs the latest and the prior fiscal year',
                                  acquisition_year=bool(lastg and lastg['acquisition_year']))
    gs = [s['growth'] for s in series]
    M['growth_volatility'] = _m(stdev(gs) if len(gs) >= 3 else None, 'ratio',
                                'sample standard deviation of year-over-year revenue growth', srefs,
                                M['revenue_growth_series']['period'], f'needs three or more yearly growth rates; {len(gs)} available',
                                n=len(gs))
    acq_c, rev_c = [g(y, 'acquisitions') for y in w3], [g(y, 'revenue') for y in w3]
    miss = [f'{k} {lab(y)}' for y, a, r in zip(w3, acq_c, rev_c) for k, c in (('acquisitions', a), ('revenue', r)) if c['value'] is None]
    sa, sr = sum(c['value'] or 0 for c in acq_c), sum(c['value'] or 0 for c in rev_c)
    window, ytd_note, yrefs, ytd = (_span(labels, w3[0], w3[-1]) if w3 else None), None, [], None
    if last:      # current fiscal year to date (acquisitions after the last 10-K, e.g. a deal closed this year)
        ya, yr = _ytd_flow(fb, 'acquisitions', last, gaps=True), _ytd_flow(fb, 'revenue', last)
        common = sorted((e, f) for e in ya for f in yr if abs(_days(e, f)) <= 7)
        if common:
            e, fr = common[-1]
            ytd = {'period_end': e, 'acquisitions': ya[e][0], 'revenue': yr[fr][0]}
            if ya[e][2]:
                ytd.update(lower_bound=True, quarters_missing=ya[e][2])
                ytd_note = (f'acquisitions in the current fiscal year to {e} are a lower bound: {ya[e][2]} quarter(s) after '
                            f'{lab(last)} have no acquisition row in the FactBook (check the 10-Q cash-flow statement)')
            yrefs = [_ref(fb, x, abs(x['value']) if x['metric'] == 'acquisitions' else None) for x in ya[e][1] + yr[fr][1]]
            window = f'{window} + year to date {e}' if window else None
            sa, sr = sa + ytd['acquisitions'], sr + ytd['revenue']
        elif ya:
            ytd_note = (f"acquisition rows after {lab(last)} ({', '.join(sorted(ya))}) have no revenue year to date to the same "
                        'date: not counted in the window')
        elif yr:
            ytd_note = (f'revenue is reported to {max(yr)} but no acquisition cash flow after {lab(last)} is in the FactBook: '
                        'acquisitions in the current fiscal year are not counted (check the latest 10-Q cash-flow statement)')
    M['acquisition_spend_pct_revenue_3y'] = _m(
        sa / sr if sr > 0 and not miss and len(w3) == 3 else None, 'ratio',
        'sum(acquisitions net of cash acquired, last 3 fiscal years + current year to date when reported) / sum(revenue, '
        'same window)', [r for c in acq_c + rev_c for r in c['refs']] + yrefs, window, 'revenue not positive',
        missing=miss or ([] if len(w3) == 3 else ['three fiscal years']), window=window,
        window_kind='fiscal_3y_plus_ytd' if ytd else 'fiscal_3y', year_to_date=ytd, note=ytd_note)
    if ytd and ytd['revenue'] > 0 and ytd['acquisitions'] / ytd['revenue'] > ACQ_PCT_REVENUE:
        acq.append({'period_end': ytd['period_end'], 'fiscal_period': f"YTD@{ytd['period_end']}", 'current_year_to_date': True,
                    'acquisitions_pct_revenue': ytd['acquisitions'] / ytd['revenue'], 'goodwill_increase_pct_prior_revenue': None,
                    'growth_step_in_table': False, 'acquisitions': ytd['acquisitions'], 'lower_bound': bool(ytd.get('lower_bound')),
                    'note': 'acquisitions in the current fiscal year to date: TTM and current-year growth include bought revenue'})

    # ---- margins
    for name, num in (('gross_margin', 'gross_profit'), ('operating_margin', 'operating_income')):
        M[name] = ratio(last, num, 'revenue', f'{num} / revenue (fiscal year)')
        f = f'{name}[{lab(last)}] - {name}[3 fiscal years earlier] (ratio difference; 0.01 = 1 percentage point)'
        if b3 is None:
            M[name + '_trend_3y'] = need_back(name, 3, b3, 'ratio', f)
        else:
            m0 = ratio(b3, num, 'revenue', '')
            val = M[name]['value'] - m0['value'] if None not in (M[name]['value'], m0['value']) else None
            miss = [m['reason'] for m in (m0, M[name]) if m['status'] == 'missing']
            M[name + '_trend_3y'] = _m(val, 'ratio', f, M[name]['inputs'] + m0['inputs'], _span(labels, b3, last),
                                       M[name].get('reason') or m0.get('reason'), missing=miss,
                                       start=m0['value'], end=M[name]['value'])
    w = w3
    oms = [(y, ratio(y, 'operating_income', 'revenue', '')) for y in w]
    known = [(y, m) for y, m in oms if m['value'] is not None]
    best = max(known, key=lambda x: x[1]['value']) if known else None
    M['operating_margin_best_3y'] = _m(best[1]['value'] if best else None, 'ratio', 'max(operating_income / revenue) over the last 3 fiscal years',
                                       [r for _, m in oms for r in m['inputs']], _span(labels, w[0], w[-1]) if w else None,
                                       'no fiscal year with operating income and revenue', best_year=lab(best[0]) if best else None,
                                       years_used=len(known))
    M['fcf_margin'] = ratio(last, 'fcf', 'revenue', '(cfo - capex) / revenue (fiscal year)')
    M['fcf_after_sbc_margin'] = ratio(last, 'fcf_after_sbc', 'revenue', '(cfo - capex - sbc) / revenue (fiscal year); SBC treated as a real cost')
    M['sbc_pct_revenue'] = ratio(last, 'sbc', 'revenue', 'sbc / revenue (fiscal year)')
    if last and v(last, 'net_income') is not None and v(last, 'net_income') <= 0 and v(last, 'cfo') is not None:
        M['cfo_to_net_income'] = _m(None, 'x', 'cfo / net_income', g(last, 'cfo')['refs'] + g(last, 'net_income')['refs'], lab(last),
                                    'net income not positive: cash conversion ratio not meaningful (compare cfo and net income directly)',
                                    cfo=v(last, 'cfo'), net_income=v(last, 'net_income'))
    else:
        M['cfo_to_net_income'] = ratio(last, 'cfo', 'net_income', 'cfo / net_income (fiscal year)', 'x')
    prev = years[-2] if len(years) > 1 and _step_ok(years[-2], last) else None
    f = '(net_income - cfo) / average(total_assets[t-1], total_assets[t]); positive = earnings ahead of cash'
    parts = [('net_income', g(last, 'net_income')), ('cfo', g(last, 'cfo')), ('total_assets', g(last, 'total_assets'))] if last else []
    miss = [f'{k} {lab(last)}' for k, c in parts if c['value'] is None] or ([] if last else ['fiscal years'])
    ta0 = v(prev, 'total_assets') if prev else None
    if miss:
        M['accruals_ratio'] = _m(None, 'ratio', f, [r for _, c in parts for r in c['refs']], lab(last) if last else None, missing=miss)
    else:
        avg = (v(last, 'total_assets') + ta0) / 2 if ta0 is not None else v(last, 'total_assets')
        M['accruals_ratio'] = _m((v(last, 'net_income') - v(last, 'cfo')) / avg if avg > 0 else None, 'ratio', f,
                                 [r for _, c in parts for r in c['refs']] + (g(prev, 'total_assets')['refs'] if ta0 is not None else []),
                                 lab(last), 'total assets not positive',
                                 note=None if ta0 is not None else 'prior-year assets unavailable: year-end assets used')
    M['capex_pct_revenue'] = ratio(last, 'capex', 'revenue', 'capex / revenue (fiscal year)')
    M['capex_to_da'] = ratio(last, 'capex', 'd_and_a', 'capex / D&A (fiscal year); below 1 = asset-light or under-investing, '
                             'well above 1 = growth capex or rising capital intensity', 'x')

    # ---- returns on capital (scores.roic on the analysis definition of invested capital)
    ser = {s['fiscal_year']: s for s in rs.get('series', [])}
    roic_f = ('NOPAT / average(invested capital[t-1], invested capital[t]) (year-end if no prior year); NOPAT = operating income '
              'x (1 - tax rate), tax rate = income tax / pretax income bounded 0..35%, 21% when pretax income <= 0 or '
              'missing; invested capital = equity + debt - cash - investments (financing side); computed by scores.roic')
    ic_keys = ('operating_income', 'income_tax', 'pretax_income', 'equity', 'debt', 'cash', 'investments')
    roic_refs = [r for pe in (prev, last) if pe for k in ic_keys for r in g(pe, k)['refs']]
    gf = 'NOPAT / average(invested capital - goodwill)'
    if not last:
        M['roic'] = _m(None, 'ratio', roic_f, [], None, missing=['fiscal years'])
        M['roic_ex_goodwill'] = _m(None, 'ratio', gf, [], None, missing=['fiscal years'])
    else:
        nopat, rate = _nopat(cells[last])
        miss = [f'{k} {lab(last)}' for k in ('operating_income', 'equity', 'debt', 'cash') if v(last, k) is None]
        ic0, ic1 = (v(prev, 'invested_capital') if prev else None), v(last, 'invested_capital')
        avg_ic = (ic0 + ic1) / 2 if None not in (ic0, ic1) else ic1
        value = (ser.get(lab(last)) or {}).get('roic')
        reason = None
        if value is None and not miss:
            reason = (f'invested capital not positive (average {avg_ic:.6g}): ROIC not meaningful; the business runs on little or '
                      'negative net operating capital (customer prepayments, buybacks)' if avg_ic is not None and avg_ic <= 0
                      else rs.get('reason') or 'not computable')
        a0, a1 = (v(prev, 'invested_capital_asset_side') if prev else None), v(last, 'invested_capital_asset_side')
        aavg = (a0 + a1) / 2 if None not in (a0, a1) else a1
        M['roic'] = _m(value, 'ratio', roic_f, roic_refs, lab(last), reason, missing=miss, basis='financing_side',
                       nopat=nopat, tax_rate=rate, average_invested_capital=avg_ic,
                       roic_asset_side=nopat / aavg if nopat is not None and aavg and aavg > 0 else None,
                       average_invested_capital_asset_side=aavg,
                       note='asset-side invested capital (NWC + PP&E + goodwill + intangibles) is a cross-check, not the basis')
        gw0, gw1 = (v(prev, 'goodwill') if prev else None), v(last, 'goodwill')
        x1 = ic1 - gw1 if None not in (ic1, gw1) else None
        x0 = ic0 - gw0 if None not in (ic0, gw0) else None
        xavg = (x0 + x1) / 2 if None not in (x0, x1) else x1
        gref = roic_refs + [r for pe in (prev, last) if pe for r in g(pe, 'goodwill')['refs']]
        if nopat is None or xavg is None:
            M['roic_ex_goodwill'] = _m(None, 'ratio', gf, gref, lab(last), missing=miss or ['goodwill or invested capital'])
        else:
            M['roic_ex_goodwill'] = _m(nopat / xavg if xavg > 0 else None, 'ratio', gf, gref, lab(last),
                                       f'invested capital excluding goodwill not positive ({xavg:.6g}): the operating business '
                                       'needs no net tangible capital; ROIC ex goodwill not meaningful (high returns, not infinite)',
                                       nopat=nopat, average_invested_capital_ex_goodwill=xavg)
    for name, numk, unit, f in (('incremental_roic_3y', 'nopat', 'ratio', '(NOPAT[t] - NOPAT[t-3]) / (invested capital[t] - '
                                 'invested capital[t-3]) (year-end invested capital; NOPAT rule as in roic)'),
                                ('sales_to_capital_incremental_3y', 'revenue', 'x', '(revenue[t] - revenue[t-3]) / '
                                 '(invested capital[t] - invested capital[t-3])')):
        if b3 is None:
            M[name] = need_back(name, 3, b3, unit, f)
            continue
        keys = ic_keys if numk == 'nopat' else ('revenue', 'equity', 'debt', 'cash', 'investments')
        refs = [r for pe in (b3, last) for k in keys for r in g(pe, k)['refs']]
        n0 = _nopat(cells[b3])[0] if numk == 'nopat' else v(b3, 'revenue')
        n1 = _nopat(cells[last])[0] if numk == 'nopat' else v(last, 'revenue')
        i0, i1 = v(b3, 'invested_capital'), v(last, 'invested_capital')
        if None in (n0, n1, i0, i1):
            M[name] = _m(None, unit, f, refs, _span(labels, b3, last), missing=[f'{numk} or invested capital at {lab(b3)}/{lab(last)}'])
            continue
        dn, di = n1 - n0, i1 - i0
        if numk == 'nopat' and min(n0, n1) <= 0:
            M[name] = _m(None, unit, f, refs, _span(labels, b3, last), f'NOPAT not positive at an endpoint ({n0:.6g} -> {n1:.6g}): '
                         'the ratio would measure loss reduction per unit of capital, not a return on it',
                         delta_numerator=dn, delta_invested_capital=di)
            continue
        reason = None if di > 0 else (f'not meaningful: invested capital fell (or was unchanged) from {i0:.6g} to {i1:.6g} '
                                      f'while {numk} changed by {dn:+.6g}; growth needed no net new capital or capital was '
                                      'returned (buybacks), so the ratio would be negative or infinite')
        M[name] = _m(dn / di if di > 0 else None, unit, f, refs, _span(labels, b3, last), reason,
                     delta_numerator=dn, delta_invested_capital=di)
    M['nwc_pct_revenue'] = ratio(last, 'nwc', 'revenue', 'NWC / revenue; NWC = current assets - cash - short-term investments - '
                                 '(current liabilities - current debt); negative = customers and suppliers fund operations')
    M['deferred_revenue_pct_revenue'] = ratio(last, 'deferred_revenue', 'revenue', '(deferred revenue current + noncurrent) / revenue; '
                                              'customer prepayments that fund growth')

    # ---- balance sheet and leverage
    rsv = _resolved(qa)
    parts = ('cash', 'short_term_investments', 'long_term_investments', 'debt')
    nf = 'cash + short-term investments - debt (operating leases, restricted cash and long-term investments excluded; dataqa definition)'
    if 'net_cash' in rsv and rsv['net_cash']['value'] is None:
        # dataqa could not resolve it (conflict / missing input): never re-derive a number it refused
        st = rsv['net_cash']['refs'][0]['status']
        M['net_cash'] = _m(None, 'USD', nf, rsv['net_cash']['refs'], None, f'dataqa could not resolve net cash (status {st})',
                           basis='dataqa.resolved')
        M['net_cash']['status'] = st if st in ('conflict', 'stale') else 'missing'
    elif 'net_cash' in rsv or ('cash' in rsv and 'debt' in rsv):
        nc = rsv.get('net_cash') or _lin((1, rsv['cash']), (1, rsv.get('short_term_investments', C(0.0))), (-1, rsv['debt']))
        asof = (rsv.get('net_cash') or rsv['cash'])['as_of']
        M['net_cash'] = _m(nc['value'], 'USD', (rsv.get('net_cash') or {}).get('definition') or nf,
                           nc['refs'] + [r for k in parts if k in rsv for r in rsv[k]['refs']], f'I@{asof}',
                           detail={k: rsv[k]['value'] for k in parts if k in rsv}, basis='dataqa.resolved')
    else:
        nc = _lin((1, g(last, 'cash')), (1, _z(g(last, 'short_term_investments'))), (-1, g(last, 'debt'))) if last else C()
        miss = [f'{k} {lab(last)}' for k in ('cash', 'debt') if v(last, k) is None] if last else ['balance sheet']
        M['net_cash'] = _m(nc['value'], 'USD', nf.replace('dataqa definition', 'latest fiscal year-end'), nc['refs'],
                           f'I@{last}' if last else None, missing=miss,
                           detail={k: v(last, k) for k in parts} if last else {}, basis='latest_fiscal_year')
    def flows(*keys):
        """TTM cells when every key shares the TTM period, else the latest fiscal year; never mixed."""
        cs = [tc.get(k, C()) for k in keys]
        if last and any(c['value'] is None for c in cs) and all(v(last, k) is not None for k in keys):
            return [g(last, k) for k in keys], lab(last)
        return cs, ttm.get('label')

    (oi, da), per = flows('operating_income', 'd_and_a')
    ebitda = _lin((1, oi), (1, da))
    f = '-(net cash) / EBITDA; EBITDA = operating income + D&A (TTM, else latest fiscal year); negative = net cash'
    if M['net_cash']['value'] is None or ebitda['value'] is None:
        M['net_debt_to_ebitda'] = _m(None, 'x', f, M['net_cash']['inputs'] + ebitda['refs'], per,
                                     missing=['net cash' if M['net_cash']['value'] is None else 'EBITDA (operating income, D&A)'])
    else:
        nd = -M['net_cash']['value']
        M['net_debt_to_ebitda'] = _m(nd / ebitda['value'] if ebitda['value'] > 0 else None, 'x', f, M['net_cash']['inputs'] + ebitda['refs'],
                                     per, f'EBITDA not positive ({ebitda["value"]:.6g}); net debt {nd:.6g}',
                                     net_debt=nd, ebitda=ebitda['value'])
    (oi, ie), per = flows('operating_income', 'interest_expense')
    f = 'operating income / interest expense (TTM, else latest fiscal year)'
    if oi['value'] is None:
        M['interest_coverage'] = _m(None, 'x', f, oi['refs'], per, missing=['operating income'])
    elif not ie['value']:
        debt = M['net_cash'].get('detail', {}) or {}
        M['interest_coverage'] = _m(None, 'x', f, oi['refs'] + ie['refs'], per,
                                    'no interest expense reported' + (' (no debt)' if not debt.get('debt') else ''))
    elif oi['value'] <= 0:
        M['interest_coverage'] = _m(None, 'x', f, oi['refs'] + ie['refs'], per,
                                    f'operating income not positive ({oi["value"]:.6g}): interest is not covered by operations',
                                    interest_expense=ie['value'])
    else:
        M['interest_coverage'] = _m(oi['value'] / ie['value'], 'x', f, oi['refs'] + ie['refs'], per)

    # ---- share count, buybacks, SBC
    f = ('(product of split-adjusted year-end ratios) ^ (1/3) - 1 from point-in-time year-end counts; a step containing a '
         "split dataqa established (qa['splits']: a dated split, an as-traded price bar or weighted shares restated by the full "
         'ratio) is divided by its factor, any residual issuance kept; an unresolved split-like step counts as issuance '
         '(unverified); without dataqa the same rule runs on the FactBook')
    if b3 is None:
        M['share_count_cagr_3y'] = need_back('share_count_cagr_3y', 3, b3, 'ratio', f)
    else:
        win = [y for y in years if b3 <= y <= last]
        sc = [g(y, 'shares_outstanding') for y in win]
        refs = [r for c in sc for r in c['refs']]
        if any(c['value'] is None or c['value'] <= 0 for c in sc):
            M['share_count_cagr_3y'] = _m(None, 'ratio', f, refs, _span(labels, b3, last),
                                          missing=[f'shares_outstanding {lab(y)}' for y, c in zip(win, sc) if c['value'] is None] or ['positive share count'])
        else:
            steps, basis, outside = _split_steps(fb, qa, [(y, c['value']) for y, c in zip(win, sc)])
            prod = 1.0
            for st in steps:
                prod *= st['adjusted_ratio']
                st['from'], st['to'] = lab(st['from']), lab(st['to'])
            n = len(steps)
            M['share_count_cagr_3y'] = _m(prod ** (1 / n) - 1 if n else None, 'ratio', f, refs, _span(labels, b3, last),
                                          'no year-end steps', steps_used=n, split_basis=basis,
                                          excluded_split_pairs=[st for st in steps if st.get('factor') and st.get('applied')],
                                          unverified_split_like=[st for st in steps if st.get('factor') and not st.get('applied')],
                                          splits_outside_window=outside, net_change_3y=prod - 1,
                                          net_change_3y_unadjusted=sc[-1]['value'] / sc[0]['value'] - 1)
    if b3:
        d0, d1 = g(b3, 'shares_diluted_wavg'), g(last, 'shares_diluted_wavg')
        M['diluted_wavg_share_cagr_3y'] = _m(cagr(d0['value'], d1['value'], 3), 'ratio',
                                             '(diluted weighted-average shares[t] / [t-3]) ^ (1/3) - 1; per-share earnings context only',
                                             d0['refs'] + d1['refs'], _span(labels, b3, last),
                                             missing=[k for k, c in (('shares_diluted_wavg ' + lab(b3), d0), ('shares_diluted_wavg ' + lab(last), d1)) if c['value'] is None])
    else:
        M['diluted_wavg_share_cagr_3y'] = need_back('diluted_wavg_share_cagr_3y', 3, b3, 'ratio', 'diluted weighted-average share CAGR')
    bb, fc, sb = ([g(y, k) for y in w3] for k in ('buybacks', 'fcf', 'sbc'))
    f = 'sum(buybacks, last 3 fiscal years) / sum(fcf, same years)'
    miss = [f'{k} {lab(y)}' for k, cs in (('buybacks', bb), ('fcf', fc)) for y, c in zip(w3, cs) if c['value'] is None]
    sfcf = sum(c['value'] or 0 for c in fc)
    span3 = _span(labels, w3[0], w3[-1]) if w3 else None
    if len(w3) < 3 or miss:
        M['buybacks_pct_fcf_3y'] = _m(None, 'ratio', f, [r for c in bb + fc for r in c['refs']], span3,
                                      missing=miss or ['three fiscal years'])
    else:
        M['buybacks_pct_fcf_3y'] = _m(sum(c['value'] for c in bb) / sfcf if sfcf > 0 else None, 'ratio', f,
                                      [r for c in bb + fc for r in c['refs']], span3,
                                      f'3-year FCF not positive ({sfcf:.6g}): buybacks were not funded by free cash flow',
                                      buybacks_3y=sum(c['value'] for c in bb), fcf_3y=sfcf)
    f = ('sum(buybacks, 3y) / sum(SBC, 3y) in dollars, read with the point-in-time share change over the same years: '
         'net dilution after buybacks = shares_outstanding[t] / shares_outstanding[t-3] - 1')
    miss = [f'{k} {lab(y)}' for k, cs in (('buybacks', bb), ('sbc', sb)) for y, c in zip(w3, cs) if c['value'] is None]
    if len(w3) < 3 or miss:
        M['sbc_offset_by_buybacks'] = _m(None, 'x', f, [r for c in bb + sb for r in c['refs']], span3, missing=miss or ['three fiscal years'])
    else:
        sbb, ssb = sum(c['value'] for c in bb), sum(c['value'] for c in sb)
        net = M['share_count_cagr_3y'].get('net_change_3y')
        if net is None:
            reading = 'share change unavailable: dollar comparison only'
        elif net > .005:
            reading = ('share count rose despite buybacks: SBC dilution was not offset' if sbb > 0
                       else 'no buybacks and the share count rose: SBC and issuance dilute holders')
        elif net < -.005:
            reading = ('share count fell: buybacks more than offset SBC issuance; the cash spent is the real cost of SBC'
                       if ssb > 0 else 'share count fell through buybacks')
        else:
            reading = 'share count roughly flat: buybacks spent cash to neutralise SBC issuance'
        M['sbc_offset_by_buybacks'] = _m(sbb / ssb if ssb > 0 else None, 'x', f, [r for c in bb + sb for r in c['refs']] + M['share_count_cagr_3y']['inputs'],
                                         span3, 'no SBC reported', buybacks_3y=sbb, sbc_3y=ssb,
                                         net_dilution_after_buybacks_3y=net, reading=reading)
    M['quarterly'] = _quarterly(fb)
    return M, acq


def _signed_factor(k):
    """dataqa's ratio-direction factor (5, 1.5, 0.1) -> 5, 1.5, -10 (negative = reverse split)."""
    return k if k >= 1 else -round(1 / k, 6)


def _overlap(s, a, z):
    return (min(_d(s['to']), _d(z)) - max(_d(s['from']), _d(a))).days


def _split_steps(fb, qa, pts):
    """[(year_end, count)] -> (steps, basis, splits outside the steps). Each step: from, to, ratio, adjusted_ratio and,
    when split-like, factor (signed), applied, basis, reason (unverified=True when split or issuance is not established).

    With a dataqa result (qa carries 'splits') exactly its splits are removed: each is assigned to the year step its
    dates overlap most and divided out when the year-end counts moved by it (counts already split-adjusted are left);
    its residual issuance stays in. qa['unresolved_splits'] mark their steps as counted-as-issuance, unverified.
    Without one, dataqa's split rule (_split_decision: positive evidence only) runs on the FactBook."""
    pairs = list(zip(pts, pts[1:]))
    steps = [{'from': a, 'to': z, 'ratio': cz / ca, 'adjusted_ratio': cz / ca} for (a, ca), (z, cz) in pairs]
    if isinstance(qa, dict) and 'splits' in qa:
        outside = []
        for sp, kind in [(x, 'split') for x in qa.get('splits') or []] + [(x, 'unresolved') for x in qa.get('unresolved_splits') or []]:
            ov = [(_overlap(sp, st['from'], st['to']), i) for i, st in enumerate(steps)]
            best = max(ov, default=(0, None))
            if best[0] <= 0:
                if kind == 'split':
                    outside.append({k: sp.get(k) for k in ('from', 'to', 'factor')})
                continue
            st = steps[best[1]]
            how = sp.get('decided_by') or (sp.get('evidence') or {}).get('decided_by', 'n/a')
            if kind == 'unresolved':
                if not st.get('applied'):
                    st.update(factor=_signed_factor(sp['factor']), applied=False, basis='dataqa', unverified=True,
                              reason=f"dataqa could not establish a split {sp['from']}..{sp['to']} ({how}): counted as issuance, "
                                     'dilution unverified (overstated if it was a split)')
                continue
            k, r = sp['factor'] * (st.get('_k') or 1.0), st['ratio']
            applied = abs(log(r / k)) < abs(log(r))
            st.update(_k=k, factor=_signed_factor(k), applied=applied, basis='dataqa', adjusted_ratio=r / k if applied else r,
                      unverified=False, reason=f"dataqa split {sp['from']}..{sp['to']}, decided by {how}"
                      + ('' if applied else ': year-end counts did not move by the factor (already split-adjusted), not divided'))
        for st in steps:
            st.pop('_k', None)
        return steps, 'dataqa', outside
    q = None
    for st in steps:
        k, in_band = DQ._split_candidate(st['ratio'])
        q = q or DQ._QA(fb, None)
        a, z = {'as_of': st['from'], 'value': 1.0}, {'as_of': st['to'], 'value': st['ratio']}
        dec = DQ._split_decision(q, a, z, k) if k or DQ._split_events(q, a, z) else None
        if not dec or (not in_band and dec['decision'] != 'split'):
            continue
        applied, by = dec['decision'] == 'split', dec['decided_by']
        tested = {t: x for t, x in dec['evidence']['tests'].items() if x != 'untested'}
        st.update(factor=_signed_factor(dec['factor']), applied=applied, basis='factbook_evidence', evidence=dec['evidence'],
                  decided_by=by, unverified=dec['decision'] == 'unresolved',
                  adjusted_ratio=st['ratio'] / dec['factor'] if applied else st['ratio'],
                  reason=f'split supported by {by} evidence' if applied else
                  f'{by} evidence contradicts a split: counted as issuance' if dec['decision'] == 'issuance' else
                  ('no dated split, no price or per-share evidence: counted as issuance until verified (dilution overstated if '
                   'it was a split)' if not tested else 'the evidence does not decide (' + ', '.join(f'{t} {x}' for t, x in tested.items())
                   + '): counted as issuance until verified'))
    return steps, 'factbook_evidence (no dataqa result)', []


# ---------------------------------------------------------------- flags

def _flags(years, labels, cells, M, acq, forensics):
    out = []
    add = lambda code, sev, msg, kind='concern', **ev: out.append({'code': code, 'severity': sev, 'message': msg, 'kind': kind, 'evidence': ev})
    val = lambda k: M.get(k, {}).get('value')
    if years and not all(_step_ok(a, z) for a, z in zip(years, years[1:])):
        add('fiscal_calendar_gap', 'medium', 'fiscal years are not consecutive 12-month periods (missing year or fiscal year-end change): '
            'growth across the gap is not computed', 'data', years=years)
    s = val('sbc_pct_revenue')
    if s is not None and s > .10:
        add('sbc_heavy', 'high' if s > .20 else 'medium', f'stock-based compensation is {s:.1%} of revenue: a real cost paid in shares; '
            'judge margins and FCF after SBC', sbc_pct_revenue=s)
    weak = []
    for pe in years[-3:]:
        ni, cfo = cells[pe]['net_income']['value'], cells[pe]['cfo']['value']
        if ni is not None and cfo is not None and ni > 0 and cfo / ni < .8:
            weak.append({'year': labels[pe], 'cfo_to_net_income': cfo / ni})
    if len(weak) >= 2:
        add('cash_conversion_weak', 'medium', f'operating cash flow below 80% of net income in {len(weak)} of the last 3 years: '
            'earnings are not turning into cash (working capital, capitalised costs or aggressive recognition)', years=weak)
    sc = M.get('share_count_cagr_3y', {})
    d = sc.get('value')
    basis = 'point-in-time shares outstanding'
    if d is None and M.get('diluted_wavg_share_cagr_3y', {}).get('value') is not None:
        d, basis = M['diluted_wavg_share_cagr_3y']['value'], 'diluted weighted-average shares (point-in-time count unavailable)'
    unv = [p for p in sc.get('unverified_split_like') or [] if p.get('unverified')]
    if d is not None and d > .02:
        add('dilution', 'high' if d > .05 else 'medium', f'share count growing {d:.1%} a year ({basis}): per-share value grows '
            'slower than the business' + (f'; unverified: {len(unv)} split-like step(s) counted as issuance until a split is ruled '
                                          'out (overstated if it was a split)' if unv else ''),
            share_cagr_3y=d, basis=basis, unverified=bool(unv), **({'unverified_steps': [{k: p.get(k) for k in ('from', 'to', 'factor')}
                                                                                      for p in unv]} if unv else {}))
    for p in sc.get('excluded_split_pairs') or []:
        add('possible_split', 'medium', f"shares outstanding changed by a factor of {p['ratio']:.2f} from {p['from']} to {p['to']}: "
            'a ' + ('reverse ' if p['factor'] < 0 else '') + f"{abs(p['factor']):g}-for-1 split ({p['reason']}); divided out of the "
            'share-count CAGR; per-share history must be split-adjusted', 'data', **p)
    for p in sc.get('unverified_split_like') or []:
        add('possible_split', 'medium', f"shares outstanding changed by a factor of {p['ratio']:.2f} from {p['from']} to {p['to']}, "
            'near a ' + ('reverse ' if p['factor'] < 0 else '') + f"{abs(p['factor']):g}-for-1 split factor: {p['reason']}", 'data', **p)
    nd = M.get('net_debt_to_ebitda', {})
    if nd.get('value') is not None and nd['value'] > 3:
        add('leverage_high', 'high' if nd['value'] > 5 else 'medium', f"net debt is {nd['value']:.1f}x EBITDA", net_debt_to_ebitda=nd['value'])
    elif nd.get('value') is None and (nd.get('net_debt') or 0) > 0 and (nd.get('ebitda') is not None and nd['ebitda'] <= 0):
        add('leverage_high', 'high', 'net debt with no positive EBITDA to service it', net_debt=nd['net_debt'], ebitda=nd['ebitda'])
    fcfs = [(labels[pe], cells[pe]['fcf']['value']) for pe in years[-3:] if cells[pe]['fcf']['value'] is not None]
    if fcfs and fcfs[-1][0] == labels[years[-1]] and fcfs[-1][1] < 0:
        all_neg = len(fcfs) == 3 and all(x < 0 for _, x in fcfs)
        nc = M.get('net_cash', {}).get('value')
        runway = nc / -fcfs[-1][1] if nc is not None and nc > 0 else None
        add('negative_fcf', 'high' if all_neg else 'medium', f'free cash flow negative in {fcfs[-1][0]}'
            + (' and in each of the last 3 years' if all_neg else '')
            + (f'; net cash covers about {runway:.1f} years at this burn' if runway is not None else '; funding need: check cash and financing'),
            fcf=fcfs[-1][1], runway_years=runway)
    t = val('operating_margin_trend_3y')
    if t is not None and t < -.03:
        add('margin_compression', 'medium', f'operating margin down {-t * 100:.1f} percentage points over 3 years: test temporary '
            'versus structural causes', operating_margin_trend_3y=t)
    hit = [a for a in acq if a['growth_step_in_table']]
    if hit:
        g3 = M.get('revenue_cagr_3y', {}).get('detail') or {}
        add('acquisition_affected_growth', 'medium', 'material acquisitions in ' + ', '.join(a['fiscal_period'] for a in hit)
            + ': part of reported growth was bought; see revenue_cagr detail for growth with those years excluded',
            years=[a['fiscal_period'] for a in hit], revenue_cagr_3y_reported=g3.get('reported'),
            revenue_cagr_3y_ex_acquisition_years=g3.get('ex_acquisition_years'))
    for a in (x for x in acq if x.get('current_year_to_date')):
        add('acquisition_current_year', 'medium', f"acquisitions of {'at least ' if a.get('lower_bound') else ''}"
            f"{a['acquisitions']:,.0f} ({a['acquisitions_pct_revenue']:.1%} of revenue) in the current fiscal year to {a['period_end']}: "
            'TTM and current-year growth include bought revenue; separate organic growth before extrapolating',
            period_end=a['period_end'], acquisitions=a['acquisitions'], acquisitions_pct_revenue=a['acquisitions_pct_revenue'],
            lower_bound=bool(a.get('lower_bound')))
    a = val('accruals_ratio')
    if a is not None and a > .10:
        add('accruals_high', 'medium', f'net income exceeds operating cash flow by {a:.1%} of assets: earnings quality needs checking', accruals_ratio=a)
    ic = val('interest_coverage')
    if ic is not None and ic < 3:
        add('interest_coverage_low', 'high' if ic < 1.5 else 'medium', f'operating income covers interest {ic:.1f}x', interest_coverage=ic)
    dr, nwc = val('deferred_revenue_pct_revenue'), val('nwc_pct_revenue')
    if dr is not None and dr >= .10 and nwc is not None and nwc < 0:
        add('deferred_revenue_funded', 'low', f'customer prepayments (deferred revenue {dr:.0%} of revenue) fund working capital '
            f'(NWC {nwc:.0%} of revenue): growth needs little capital; reverses if billing terms shorten or customers leave',
            'positive', deferred_revenue_pct_revenue=dr, nwc_pct_revenue=nwc)
    if forensics.get('status') == 'computed':
        al, be, pi = forensics.get('altman', {}), forensics.get('beneish', {}), forensics.get('piotroski', {})
        if al.get('zone') == 'distress':
            add('altman_distress', 'high', f"Altman {al.get('model')} = {al.get('z'):.2f} in the distress zone (weak for very young or "
                'buyback-heavy firms)', z=al.get('z'))
        if be.get('zone') == 'likely_manipulator':
            add('beneish_high', 'medium', f"Beneish M = {be.get('m'):.2f} above -1.78: investigate accruals and revenue recognition; "
                'fast growers score high by construction, do not accuse', m=be.get('m'))
        if pi.get('zone') == 'weak':
            add('piotroski_weak', 'low', f"Piotroski {pi.get('score')}/{pi.get('out_of')}: financial strength deteriorating on most signals",
                score=pi.get('score'))
    return out
