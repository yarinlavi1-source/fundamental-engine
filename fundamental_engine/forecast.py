"""Bear / base / bull business forecast, emitted as a driver spec (driver_version 1).

Why: a valuation is only as good as the business forecast behind it. This module turns the FactBook,
the financial analysis (analysis.py), the business model card (model_card.py) and the validated
opening balance (dataqa.py) into explicit paths for growth, GAAP operating margin (after SBC),
reinvestment, working capital, SBC, tax, funding and the discount rate. Every number becomes an
assumption row with a kind, a rationale, a falsifier and sources, and the paths are written as a
drivers.build_case spec that valuation.value_company executes and valuation_audit reviews.

Growth costs capital (growth capex from incremental sales-to-capital), funding is explicit (assumed
financing is labelled and flagged, never silent), SBC is a cash-equivalent cost with point-in-time
shares (no double counting with dilution) and the terminal margin equals the last explicit year.

The constants below are MODEL ASSUMPTIONS: chosen once from base rates and documented here, cited in
the ledger through the 'engine:forecast_rules' source, and never tuned toward a return target or
the owner's 14% aspiration. Generated assumptions stay 'unreviewed' until an independent reviewer
marks them reviewed (inputs['reviews']); only then can the valuation become eligible for synthesis.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math
from . import facts
from .drivers import build_case, implied_growth_shift, RATIO_KEYS, ABSOLUTE_KEYS
from .finance import number
from .valuation import value_company, years, ROUTES
from .valuation_tools import implied_cost_of_equity

FORECAST_VERSION = 1
SCEN = ('bear', 'base', 'bull')
BUILT = ('fcff_annual_path', 'normalized_cycle_fcff', 'scenario_survival_fcff')
NOT_BUILT = ('residual_income', 'nav', 'rnpv', 'sotp', 'not_reliably_valuable')
RF_DEFAULT = .0425       # US 10-year Treasury approximation; replace with a dated quote via inputs['risk_free']
ERP_DEFAULT = .045       # mature-market equity risk premium (implied-ERP studies centre around 4-5%)
TERMINAL_GROWTH_CAP = .03  # nominal perpetual growth never above min(risk-free, 3%)
MARGIN_FLOOR = .02       # terminal margin must be positive; a binding floor is flagged, not hidden
HORIZON = ((.25, 15), (.15, 12), (-9, 10))   # growth anchor above x -> explicit fiscal years (extended to 20 if still far from terminal)

# Stage parameters (model assumptions, not estimates of a specific company):
# half_life  years for EXCESS growth (growth - terminal growth) to halve. Koller et al. (McKinsey) report
#            >20% growers slowing to ~8% within five years and ~5% within ten; that path implies a half-life
#            of ~2.4 years for excess growth. Slower decay is allowed for mature, small excess growth.
# g_spread   minimum first-year growth gap between base and bull/bear (stage uncertainty floor).
# m_spread   GAAP operating-margin gap between scenarios at maturity.
# conv       years over which the margin moves from today's level to its mature target.
# months     minimum operating cash in months of cash costs (capped at today's cash: the company already
#            operates with its balance).  sbc_fade  share of today's SBC/revenue that falls away at scale.
# catch_up   share of the gap to the type-typical mature margin a below-typical company closes in base.
# ke         business-risk add-on to the cost of equity (NOT price beta).
# debt_spread spread over risk-free for ASSUMED new financing.
STAGE = {
    'early':      dict(half_life=2.0, g_spread=.10, m_spread=.10, conv=7, months=6, sbc_fade=.4, catch_up=.6, ke=.03, debt_spread=.07),
    'growth':     dict(half_life=2.5, g_spread=.06, m_spread=.07, conv=6, months=4, sbc_fade=.3, catch_up=.7, ke=.015, debt_spread=.05),
    'maturing':   dict(half_life=3.0, g_spread=.04, m_spread=.05, conv=5, months=3, sbc_fade=.2, catch_up=.6, ke=.005, debt_spread=.03),
    'mature':     dict(half_life=3.0, g_spread=.03, m_spread=.04, conv=4, months=2, sbc_fade=.1, catch_up=.4, ke=0., debt_spread=.02),
    'decline':    dict(half_life=2.0, g_spread=.04, m_spread=.05, conv=4, months=3, sbc_fade=0., catch_up=.2, ke=.01, debt_spread=.04),
    'turnaround': dict(half_life=2.0, g_spread=.06, m_spread=.08, conv=5, months=6, sbc_fade=.1, catch_up=.5, ke=.025, debt_spread=.05),
}
# Business-model parameters: incremental sales-to-capital range (revenue added per unit of net capital),
# type-typical mature GAAP operating margin after SBC, a ceiling few companies of the type sustain, and the
# valuation.ROUTES archetype. Rough engine ranges for orders of magnitude, NOT a peer study: replace with
# a peer set through inputs['overrides'] when one is evidenced.
TYPE = {
    'subscription_software':            ((1.5, 5.0), .25, .40, 'software'),
    'platform_marketplace':             ((1.5, 5.0), .25, .40, 'platform'),
    'semiconductor':                    ((0.8, 2.0), .25, .40, 'nonfinancial'),
    'hardware_manufacturing':           ((1.2, 3.0), .10, .20, 'industrial'),
    'capital_intensive_infrastructure': ((0.3, 1.0), .30, .45, 'infrastructure'),
    'consumer_brand':                   ((1.5, 3.5), .15, .25, 'consumer'),
    'industrial':                       ((1.0, 2.5), .12, .20, 'industrial'),
    'commodity':                        ((0.4, 1.2), .15, .30, 'commodity'),
}
DEFAULT_TYPE = ((1.0, 3.0), .15, .25, 'nonfinancial')
NON_OPERATING_TYPES = {'financials': 'residual_income', 'reit': 'nav', 'pre_revenue_biotech': 'rnpv', 'conglomerate': 'sotp'}
KE_FACTORS = (  # (condition, add-on, reason) evaluated on the card and analysis
    ('cyclical', .005, 'cyclical revenue and margins'),
    ('capital_intensity_high', .005, 'high capital intensity (operating leverage on fixed assets)'),
    ('needs_external_funding', .01, 'plan depends on external funding'),
    ('leverage_high', .01, 'net debt above 3x EBITDA'),
    ('leverage_medium', .005, 'net debt 1.5-3x EBITDA'),
)
WEAK_DIMENSIONS = ('customer_concentration', 'technology_ai', 'governance', 'competition')
ECON_LEVERS = ('growth_anchor', 'growth_half_life', 'terminal_growth', 'mature_margin', 'sales_to_capital',
               'terminal_roic', 'cost_of_equity', 'tax_rate', 'payout_fraction', 'minimum_cash_months')
TERMINAL_KEYS = ('growth', 'wacc', 'roic', 'operating_margin', 'tax_rate', 'other_claims', 'net_runoff_obligation')
KIND_MAP = {'filing_derived': ('filing', 'provider_normalized'), 'vendor_normalized': ('provider_data', 'provider_normalized'),
            'market_feed': ('market_data', 'market_feed'), 'consensus': ('analyst_estimates', 'provider_normalized'),
            'document': ('document', None), 'synthetic': ('synthetic', None)}
RULES = 'engine:forecast_rules'


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _r(x, k=6):
    return round(float(x), k)


def _sha(obj):
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _fy(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _metric(an, name):
    m = (an.get('metrics') or {}).get(name)
    return _num(m.get('value') if isinstance(m, dict) else m)


def _pct(x):
    return 'n/a' if x is None else f'{x:.1%}'


# ---------------------------------------------------------------- inputs

def _check_overrides(fb, overrides):
    known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
    out = []
    for i, o in enumerate(overrides):
        if not isinstance(o, dict):
            raise ValueError(f'override {i}: must be an object')
        path = o.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError(f'override {i}: path required')
        if not isinstance(o.get('reason'), str) or not o['reason'].strip():
            raise ValueError(f'override {path}: a written reason is required')
        ev = o.get('evidence_ids')
        if not isinstance(ev, list) or not ev:
            raise ValueError(f'override {path}: evidence_ids required (FactBook source or qualitative ids)')
        bad = [e for e in ev if e not in known]
        if bad:
            raise ValueError(f'override {path}: unknown evidence ids {bad}')
        if o.get('scenario', 'all') not in SCEN + ('all',):
            raise ValueError(f'override {path}: scenario must be bear, base, bull or all')
        raw = path == 'growth' or path in RATIO_KEYS or path in ABSOLUTE_KEYS or (
            path.startswith('terminal.') and path.split('.', 1)[1] in TERMINAL_KEYS)
        if path not in ECON_LEVERS and not raw:
            raise ValueError(f'override {path}: unknown path; use {", ".join(ECON_LEVERS)}, a driver key or terminal.<key>')
        v = o.get('value')
        vals = v if isinstance(v, list) else [v]
        if not vals or any(_num(x) is None for x in vals):
            raise ValueError(f'override {path}: value must be a finite number or a list of numbers')
        if isinstance(v, list) and path in ECON_LEVERS:
            raise ValueError(f'override {path}: economic levers take one number')
        out.append({'id': f'override_{i + 1}', 'path': path, 'scenario': o.get('scenario', 'all'), 'value': deepcopy(v),
                    'reason': o['reason'], 'evidence_ids': list(ev), 'raw': raw and path not in ECON_LEVERS})
    return out


def _check_reviews(reviews):
    out = {}
    for aid, r in (reviews or {}).items():
        if not isinstance(r, dict) or r.get('status') not in ('reviewed', 'unreviewed'):
            raise ValueError(f'review {aid}: status must be reviewed or unreviewed')
        if r['status'] == 'reviewed' and (not isinstance(r.get('reviewer'), str) or not r['reviewer'].strip()):
            raise ValueError(f'review {aid}: a reviewed assumption needs a named reviewer')
        out[aid] = r
    return out


def _rate_input(fb, spec, default, label):
    """inputs['risk_free'] / ['equity_risk_premium'] -> (value, source dict or None, note)."""
    if not spec:
        return default, None, f'{label} default {default:.2%} (model assumption)'
    v = number(spec.get('value'), label, -.05, .3)
    when = spec.get('as_of') or fb['as_of']
    if _d(when) > _d(fb['as_of']):
        raise ValueError(f'{label} dated after the FactBook as_of (look-ahead)')
    sid = spec.get('source_id')
    if sid and sid in fb['sources']:
        return v, {'fb': sid}, f'{label} {v:.2%} from {sid} ({when})'
    if not spec.get('title'):
        raise ValueError(f'{label}: supply source_id (FactBook source) or title/url for the dated quote')
    sid = 'input:' + label
    return v, {'case': {'id': sid, 'title': spec['title'], 'url': spec.get('url') or f'input://{label}',
                        'kind': spec.get('kind', 'official_statistics'), 'published_at': when, 'available_at': when,
                        'retrieval': spec.get('retrieval', 'provider_normalized')}}, f'{label} {v:.2%} ({spec["title"]}, {when})'


# ---------------------------------------------------------------- routing and history

def _route(card, h, warn):
    val = card.get('valuation') or {}
    bm = (card.get('business_model') or {}).get('primary')
    method = val.get('method') or 'fcff_annual_path'
    stage = (card.get('stage') or {}).get('value')
    if stage not in STAGE:
        g, m = h['g_anchor'], h['m0']
        stage = ('early' if m is not None and m < 0 and g > .2 else 'growth' if g > .15 else 'maturing' if g > .07
                 else 'decline' if g < 0 else 'mature')
        warn('stage_inferred', f'model card stage missing or unknown; inferred {stage} from growth {_pct(g)} and margin {_pct(m)}')
    if bm not in TYPE:
        warn('business_model_default', f'business model {bm!r} has no forecast type table; generic ranges used', 'low')
    typ = TYPE.get(bm, DEFAULT_TYPE)
    archetype = val.get('archetype') or typ[3]
    dims = card.get('dimensions') or {}
    return {'method': method, 'business_model': bm, 'stage': stage, 'type': typ, 'archetype': archetype,
            'cyclical': bool((card.get('cyclical') or {}).get('value')),
            'capital_intensity': (card.get('capital_intensity') or {}).get('value'),
            'funding': (card.get('funding') or {}).get('value'),
            'moat': (dims.get('moat') or {}).get('assessment', 'unknown'), 'dimensions': dims}


def _latest_src(fb, *metrics):
    ids = []
    for m in metrics:
        s = facts.series(fb, m, 'annual') or facts.series(fb, m, 'instant')
        if s:
            ids.append(s[-1][2]['source_id'])
    return sorted(set(ids))


def _history(fb, an, warn):
    """Evidence the paths start from; analysis.py metrics first, FactBook accessors as fallback."""
    ann = an.get('annual') or {}
    if not ann:
        t = facts.annual_table(fb, ['revenue', 'operating_income', 'sbc', 'd_and_a', 'capex', 'cfo', 'buybacks',
                                    'dividends_paid', 'acquisitions', 'interest_expense', 'pretax_income', 'income_tax'])
        ann = {e: dict(r, dividends=r.get('dividends_paid')) for e, r in t.items()}
        warn('analysis_annual_missing', 'analysis annual table absent; history read from the FactBook directly', 'low')
    rows = [(e, ann[e]) for e in sorted(ann) if (_num(ann[e].get('revenue')) or 0) > 0 and _d(e) <= _d(fb['as_of'])]
    if not rows:
        return None
    ends, rev = [e for e, _ in rows], [r['revenue'] for _, r in rows]
    g = lambda r, k: _num(r.get(k))
    growth = [b / a - 1 for a, b in zip(rev, rev[1:])]
    margins = [g(r, 'operating_income') / r['revenue'] if g(r, 'operating_income') is not None else None for _, r in rows]
    h = {'rows': rows, 'ends': ends, 'revenue': rev, 'growth': growth, 'margins': margins,
         'last_end': ends[-1], 'last_revenue': rev[-1], 'n_years': len(rows)}
    ttm = an.get('ttm') or {}
    t_rev, t_end, t_method = _num(ttm.get('revenue')), ttm.get('period_end'), ttm.get('method')
    if t_rev is None:
        t = facts.ttm(fb, 'revenue')
        t_rev, t_end, t_method = (t['value'], t['period_end'], t['method']) if t else (rev[-1], ends[-1], 'latest_fiscal_year')
    h['ttm'] = {'revenue': t_rev, 'period_end': t_end, 'method': t_method,
                'operating_income': _num(ttm.get('operating_income')), 'sbc': _num(ttm.get('sbc'))}
    use_ttm = h['ttm']['operating_income'] is not None and t_end != ends[-1] and bool(t_rev)
    oi = h['ttm']['operating_income'] if use_ttm else g(rows[-1][1], 'operating_income')
    base_rev = t_rev if use_ttm else rev[-1]
    h['m0'] = oi / base_rev if oi is not None and base_rev else _metric(an, 'operating_margin')
    h['m0_basis'] = f'TTM operating_income / TTM revenue to {t_end}' if use_ttm else f'operating_income / revenue, fiscal year ending {ends[-1]}'
    best = [m for m in margins[-3:] if m is not None]
    h['m_best3'] = _metric(an, 'operating_margin_best_3y')
    if h['m_best3'] is None and best:
        h['m_best3'] = max(best)
    h['m_all'] = [m for m in margins if m is not None]
    # growth anchors
    g_last = _metric(an, 'revenue_growth_last')
    g_last = growth[-1] if g_last is None and growth else g_last
    cagr = lambda n: (rev[-1] / rev[-1 - n]) ** (1 / n) - 1 if len(rev) > n and rev[-1 - n] > 0 else None
    c3 = _metric(an, 'revenue_cagr_3y'); c3 = cagr(3) if c3 is None else c3
    c5 = _metric(an, 'revenue_cagr_5y'); c5 = cagr(5) if c5 is None else c5
    h.update(g_last=g_last, cagr_3y=c3, cagr_5y=c5)
    g_recent, yrs = None, years(ends[-1], t_end) if t_end else 0
    if t_method == 'quarters' and yrs >= .45 and t_rev and rev[-1] > 0:
        g_recent = (t_rev / rev[-1]) ** (1 / yrs) - 1
    h['g_recent'] = g_recent
    trail = _mean([g_last, c3])
    if trail is None:
        trail = 0.
        warn('growth_history_missing', 'fewer than two fiscal years of revenue; growth anchor set to 0% (model)', 'high')
    h['g_trailing'] = trail if g_recent is None else .5 * g_recent + .5 * trail
    h['g_trailing_formula'] = ('mean(revenue_growth_last, revenue_cagr_3y)' if g_recent is None else
                               '0.5 x annualized TTM-vs-last-FY growth + 0.5 x mean(revenue_growth_last, revenue_cagr_3y)')
    h['g_cycle'] = c5 if c5 is not None else (c3 if c3 is not None else trail)
    vol = _metric(an, 'growth_volatility')
    if vol is None and len(growth) >= 3:
        mu = sum(growth) / len(growth)
        vol = (sum((x - mu) ** 2 for x in growth) / (len(growth) - 1)) ** .5
    h['growth_volatility'] = vol
    last = rows[-1][1]
    sbc = h['ttm']['sbc'] if use_ttm and h['ttm']['sbc'] is not None else g(last, 'sbc')
    h['sbc_pct'] = sbc / base_rev if sbc is not None else _metric(an, 'sbc_pct_revenue')
    if h['sbc_pct'] is None:
        h['sbc_pct'] = 0.
        warn('sbc_missing', 'SBC not reported in the history; SBC set to 0% of revenue (check the cash-flow statement)', 'medium')
    da = g(last, 'd_and_a')
    h['da_pct'] = da / rev[-1] if da is not None else None
    cap = [abs(g(r, 'capex')) / r['revenue'] for _, r in rows[-3:] if g(r, 'capex') is not None]
    h['capex_pct'] = _mean(cap) if cap else _metric(an, 'capex_pct_revenue')
    if h['da_pct'] is None:
        h['da_pct'] = h['capex_pct'] or .03
        warn('da_missing', f'D&A missing; depreciation set to {h["da_pct"]:.1%} of revenue (capex proxy or 3% default)', 'medium')
    if h['capex_pct'] is None:
        h['capex_pct'] = h['da_pct']
        warn('capex_missing', 'capex missing; maintenance capex set equal to depreciation', 'medium')
    h['acq_pct'] = _metric(an, 'acquisition_spend_pct_revenue_3y')
    h['s2c'] = _metric(an, 'sales_to_capital_incremental_3y')
    ic = [(e, _num(r.get('invested_capital')), r['revenue']) for e, r in rows if _num(r.get('invested_capital')) is not None]
    # revenue grew while invested capital did not: capital-light history (S2C undefined, not unknown)
    h['capital_light'] = len(ic) >= 2 and ic[-1][2] > ic[max(0, len(ic) - 4)][2] and ic[-1][1] <= ic[max(0, len(ic) - 4)][1]
    h['roic'] = _metric(an, 'roic')
    h['nd_ebitda'] = _metric(an, 'net_debt_to_ebitda')
    dist, fcf = 0., 0.
    for _, r in rows[-3:]:
        f = g(r, 'fcf') if g(r, 'fcf') is not None else (g(r, 'cfo') - abs(g(r, 'capex') or 0) if g(r, 'cfo') is not None else None)
        if f is None:
            continue
        fcf += f
        dist += abs(g(r, 'buybacks') or 0) + abs(g(r, 'dividends') or g(r, 'dividends_paid') or 0)
    h['payout_history'] = dist / fcf if fcf > 0 else None
    # tax: income_tax / pretax over the last three years with positive pretax income
    tx = [(g(r, 'income_tax'), g(r, 'pretax_income')) for _, r in rows[-3:]]
    if all(a is None for a, _ in tx):
        tt = facts.annual_table(fb, ['income_tax', 'pretax_income'])
        tx = [(r.get('income_tax'), r.get('pretax_income')) for e, r in list(tt.items())[-3:]]
    tx = [(a, b) for a, b in tx if a is not None and b is not None and b > 0]
    h['tax_effective'] = sum(a for a, _ in tx) / sum(b for _, b in tx) if tx else None
    h['interest_expense'] = g(last, 'interest_expense')
    if h['interest_expense'] is None:
        s = facts.series(fb, 'interest_expense')
        h['interest_expense'] = abs(s[-1][1]) if s else None
    # operating working capital excluding cash, debt and deferred revenue (deferred revenue has its own ledger)
    wct = facts.annual_table(fb, ['revenue', 'receivables', 'inventory', 'payables'])
    wrow = [(e, r) for e, r in wct.items() if 'revenue' in r and ('receivables' in r or 'payables' in r)]
    wc = lambda r: (r.get('receivables') or 0) + (r.get('inventory') or 0) - (r.get('payables') or 0)
    h['wc_ratio'], h['wc_basis'] = None, None
    if len(wrow) >= 2:
        (e0, r0), (e1, r1) = wrow[max(0, len(wrow) - 4)], wrow[-1]
        dr = r1['revenue'] - r0['revenue']
        if dr > .02 * r1['revenue']:
            h['wc_ratio'], h['wc_basis'] = (wc(r1) - wc(r0)) / dr, f'change in (receivables + inventory - payables) / change in revenue, {e0}..{e1}'
    if h['wc_ratio'] is None and wrow:
        h['wc_ratio'], h['wc_basis'] = wc(wrow[-1][1]) / wrow[-1][1]['revenue'], f'level (receivables + inventory - payables) / revenue at {wrow[-1][0]}'
    h['wc_sources'] = _latest_src(fb, 'receivables', 'payables', 'inventory')
    return h


def _calendar(fb, h, n_full):
    as_of, last = _d(fb['as_of']), _d(h['last_end'])
    fye = str(fb['company'].get('fiscal_year_end') or '')
    try:
        month, day = int(fye[:2]), int(fye[3:5])
        date(2000, month, day)
    except ValueError:
        month, day = last.month, last.day
    y = last.year
    while _fy(y, month, day) <= as_of:
        y += 1
    b = [_fy(y + i, month, day) for i in range(n_full + 1)]
    fifth = _fy(as_of.year + 5, as_of.month, as_of.day)
    report = [fb['as_of']]
    for x in b:
        report.append(x.isoformat())
        if x >= fifth:
            break
    return {'boundaries': [x.isoformat() for x in b], 'report_dates': report, 'stub_years': years(fb['as_of'], b[0].isoformat()),
            'fiscal_years_since_last_report': max(1, round(years(h['last_end'], b[0].isoformat()))),
            'fiscal_year_end': f'{month:02d}-{day:02d}',
            'report_rule': 'as_of plus every fiscal-year boundary through the first boundary on or after the fifth anniversary '
                           'of as_of; "fifth year" means that boundary (nominal fiscal year-ends, so a 52/53-week calendar can '
                           'differ by a few days)'}


def _consensus(fb, boundaries, last_end):
    """Revenue consensus per nominal fiscal boundary (period_end within 20 days); look-ahead rows dropped."""
    out, dropped = {}, 0
    for e in fb.get('estimates') or []:
        if e.get('metric') != 'revenue' or _num(e.get('value')) is None:
            continue
        if _d(e.get('as_of') or fb['as_of']) > _d(fb['as_of']):
            dropped += 1
            continue
        if _d(e['period_end']) <= _d(last_end):
            continue
        for b in boundaries:
            if abs((_d(e['period_end']) - _d(b)).days) <= 20:
                cur = out.get(b)
                if cur is None or (e.get('as_of') or '') > (cur.get('as_of') or ''):
                    out[b] = e
    return out, dropped


# ---------------------------------------------------------------- scenario construction

def _fade(start, terminal, half_life, n):
    """Excess growth over terminal decays with a half-life: g_t = g_T + (g_0 - g_T) * 0.5^(t / half_life), t = 1..n."""
    return [terminal + (start - terminal) * .5 ** (t / half_life) for t in range(1, n + 1)]


def _cost_of_equity(route, h, rf, erp, inputs_notes):
    st = STAGE[route['stage']]
    adj = [{'factor': 'stage:' + route['stage'], 'value': st['ke'], 'reason': f"business-risk add-on for the {route['stage']} stage"}]
    cond = {'cyclical': route['cyclical'], 'capital_intensity_high': route['capital_intensity'] == 'high',
            'needs_external_funding': route['funding'] == 'needs_external' or route['method'] == 'scenario_survival_fcff',
            'leverage_high': (h['nd_ebitda'] or 0) > 3, 'leverage_medium': 1.5 < (h['nd_ebitda'] or 0) <= 3}
    for key, v, why in KE_FACTORS:
        if cond[key]:
            adj.append({'factor': key, 'value': v, 'reason': why})
    for d in WEAK_DIMENSIONS:   # at most 4 x 0.25pp = 1pp
        if (route['dimensions'].get(d) or {}).get('assessment') == 'weak':
            adj.append({'factor': 'weak_' + d, 'value': .0025, 'reason': f'model card rates {d} weak'})
    ke = _clamp(rf + erp + sum(a['value'] for a in adj), rf + .02, .20)
    flat = {'adjustment_' + a['factor'].replace(':', '_'): a['value'] for a in adj}   # numeric keys for risk.py's priced-via lookup
    return ke, {'risk_free': rf, 'equity_risk_premium': erp, **flat, 'adjustments': adj, 'notes': inputs_notes,
                'formula': 'Ke = risk-free + equity risk premium + business-risk adjustments (stage, cyclicality, capital '
                           'intensity, funding, leverage, weak card dimensions); a documented table, NOT price beta; '
                           'same Ke in every scenario so scenarios differ by business outcomes only'}


def _margin_targets(route, h, warn):
    st, (_, typ, ceil, _) = STAGE[route['stage']], route['type']
    m0, best, sp = h['m0'], h['m_best3'], STAGE[route['stage']]['m_spread']
    if route['method'] == 'normalized_cycle_fcff':
        hist = h['m_all']
        if len(hist) < 5:
            warn('cycle_not_observed', f'only {len(hist)} fiscal years of margins: mid-cycle margin may not span a full cycle', 'high')
        mid, trough, peak = sum(hist) / len(hist), min(hist), max(hist)
        spread = max(sp, mid - trough)
        t = {'base': mid, 'bear': mid - spread, 'bull': min(ceil, mid + .75 * max(sp, peak - mid))}
        why = f'mid-cycle = mean operating margin over {len(hist)} fiscal years {_pct(mid)}; bear = mid - max(stage spread, mid - trough {_pct(trough)}); bull toward peak {_pct(peak)}'
    elif route['method'] == 'scenario_survival_fcff':
        base = max(typ * .8, min(m0 if m0 is not None else 0, ceil))
        t = {'base': base, 'bear': min(base - sp, typ * .35), 'bull': min(ceil, max(typ, base + .5 * sp))}
        why = f'survival model: base reaches 80% of the type-typical mature margin {_pct(typ)}; bear 35%; bull the typical margin'
    elif m0 is None:
        t = {'base': typ, 'bear': typ - sp, 'bull': min(ceil, typ + .75 * sp)}
        why = f'no current margin; type-typical mature margin {_pct(typ)}'
        warn('margin_missing', 'current operating margin unavailable; mature margin set to the type-typical level', 'high')
    elif m0 >= typ:
        moat = route['moat']
        if moat == 'strong':
            base = max(m0, min(ceil, (m0 + (best if best is not None else m0)) / 2))
            why = (f'strong moat: current margin {_pct(m0)} held, recovering half-way to the best of 3 years {_pct(best)} only up to the '
                   f'type ceiling {_pct(ceil)} (the ceiling caps assumed expansion; it never cuts an evidenced margin)')
        else:
            k = .5 if moat == 'weak' else .25
            base = m0 - k * (m0 - typ)
            why = f'{moat} moat: {k:.0%} of the excess of current margin {_pct(m0)} over the type-typical {_pct(typ)} competed away'
        t = {'base': base, 'bear': base - sp, 'bull': max(base, min(max(ceil, best or 0), base + .75 * sp))}
    else:
        base = m0 + st['catch_up'] * (typ - m0)
        if best is not None:
            base = max(base, min(best, typ))
        t = {'base': base, 'bear': base - sp, 'bull': min(ceil, base + .75 * sp)}
        why = f'below type-typical: base closes {st["catch_up"]:.0%} of the gap from {_pct(m0)} to {_pct(typ)} (not below the best of 3 years {_pct(best)})'
    for s in SCEN:
        if t[s] < MARGIN_FLOOR:
            warn('margin_floor', f'{s} mature margin {_pct(t[s])} raised to the {MARGIN_FLOOR:.0%} floor so a terminal value exists; '
                                 'a business that never earns a margin has no going-concern value (see scenario_survival / risk)', 'high')
            t[s] = MARGIN_FLOOR
    t['bull'] = max(t['bull'], t['base'])
    t['bear'] = min(t['bear'], t['base'])
    return t, why


def _revenue_guidance(fb):
    """Latest dated company revenue guidance per fiscal-year label ('FY2026'), issued on or before as_of."""
    out = {}
    for g in fb.get('guidance', []):
        if g.get('metric') != 'revenue' or not str(g.get('period', '')).startswith('FY') or len(str(g['period'])) != 6:
            continue
        if g.get('issued_at') and _d(g['issued_at']) > _d(fb['as_of']):
            continue
        lo, hi = _num(g.get('low')), _num(g.get('high'))
        if lo is None or hi is None or hi < lo:
            continue
        prev = out.get(g['period'])
        if prev is None or (g.get('issued_at') or '') > (prev.get('issued_at') or ''):
            out[g['period']] = {'value': (lo + hi) / 2, 'low': lo, 'high': hi, 'issued_at': g.get('issued_at'),
                                'source_ids': g.get('source_ids', [])}
    return out


def _params(route, h, cal, cons, rf, ke, debt0, price, shares, ov, warn):
    st = STAGE[route['stage']]
    s2c_range = route['type'][0]
    gT_base = min(rf, TERMINAL_GROWTH_CAP if route['stage'] != 'decline' else .02)
    gT = {'base': gT_base, 'bull': gT_base, 'bear': max(0., gT_base - .005)}
    k = cal['fiscal_years_since_last_report']
    b1 = cal['boundaries'][0]
    # current fiscal year (the year the stub belongs to)
    gd = h.get('guidance', {}).get(f'FY{b1[:4]}')
    if b1 in cons:
        r_fy, g_cur = cons[b1]['value'], (cons[b1]['value'] / h['last_revenue']) ** (1 / k) - 1
        cur_basis = f"consensus revenue for the fiscal year ending {b1} ({cons[b1]['source_id']}, {cons[b1].get('as_of')})"
    elif gd:
        r_fy, g_cur = gd['value'], (gd['value'] / h['last_revenue']) ** (1 / k) - 1
        cur_basis = (f"company guidance midpoint for the fiscal year ending {b1} ({gd['low']:,.0f}-{gd['high']:,.0f}, issued "
                     f"{gd.get('issued_at')}; management guidance, not a reported fact)")
    else:
        g_cur = h['g_cycle'] if route['method'] == 'normalized_cycle_fcff' else h['g_trailing']
        g_cur = _clamp(g_cur, -.5, 2.)
        if h['ttm']['method'] == 'quarters' and h['ttm']['period_end'] and _d(h['ttm']['period_end']) > _d(h['last_end']):
            r_fy = h['ttm']['revenue'] * (1 + g_cur) ** years(h['ttm']['period_end'], b1)
            cur_basis = f"TTM revenue to {h['ttm']['period_end']} grown at the trailing anchor to {b1}"
        else:
            r_fy = h['last_revenue'] * (1 + g_cur) ** years(h['last_end'], b1)
            cur_basis = f"last reported fiscal-year revenue ({h['last_end']}) grown at the trailing anchor to {b1}"
    stub_rate = r_fy * (1 + g_cur) ** (.5 - cal['stub_years'] / 2)
    n_full = len(cal['boundaries']) - 1
    # consensus growth for the first two full years
    cg, lows, highs = [], [], []
    prev = r_fy
    for b in cal['boundaries'][1:3]:
        if b not in cons:
            break
        e = cons[b]
        cg.append(e['value'] / prev - 1)
        lows.append(_num(e.get('low')) / prev - 1 if _num(e.get('low')) else None)
        highs.append(_num(e.get('high')) / prev - 1 if _num(e.get('high')) else None)
        prev = e['value']
    half = (highs[0] - lows[0]) / 2 if cg and lows[0] is not None and highs[0] is not None else None
    for s in SCEN:
        gT[s] = ov(s, 'terminal_growth', gT[s])
    hl = ov('base', 'growth_half_life', st['half_life'])
    anchor = ov('base', 'growth_anchor', cg[0] if cg else _fade(g_cur, gT['base'], hl, 1)[0])
    if cg and anchor == cg[0]:
        base, g_kind = cg + _fade(cg[-1], gT['base'], hl, n_full - len(cg)), 'analyst'
    else:
        base, g_kind = [anchor] + _fade(anchor, gT['base'], hl, n_full - 1), 'model'
    spread = min(.15, max(st['g_spread'], h['growth_volatility'] or 0, half or 0))
    b0 = base[0]
    bear0 = min(b0 - spread, b0 / 2) if b0 > 0 else b0 - spread
    if lows and lows[0] is not None:
        bear0 = min(bear0, lows[0])
    bull0 = b0 + spread
    if highs and highs[0] is not None:
        bull0 = min(max(bull0, highs[0]), b0 + 2 * spread)
    bear0, bull0 = ov('bear', 'growth_anchor', bear0), ov('bull', 'growth_anchor', bull0)
    hb, hu = ov('bear', 'growth_half_life', .8 * st['half_life']), ov('bull', 'growth_half_life', 1.3 * st['half_life'])
    bear = [bear0] + _fade(bear0, gT['bear'], hb, n_full - 1)
    bull = [bull0] + _fade(bull0, gT['bull'], hu, n_full - 1)
    bear = [min(x, y - .005) for x, y in zip(bear, base)]
    bull = [max(x, y + .005) for x, y in zip(bull, base)]
    paths = {'bear': bear, 'base': base, 'bull': bull}
    margins, m_why = _margin_targets(route, h, warn)
    (lo, hi) = s2c_range
    s2c_h = h['s2c']
    if (s2c_h is None or s2c_h <= 0) and h['capital_light']:
        s2c = hi
        warn('sales_to_capital_capped', f'revenue grew while invested capital did not (capital-light history); type ceiling {hi:.2f} used so growth still costs capital', 'low')
    elif s2c_h is None or s2c_h <= 0:
        s2c = (lo + hi) / 2
        warn('sales_to_capital_default', f'incremental sales-to-capital not meaningful in the history ({s2c_h}); type midpoint {s2c:.2f} used', 'medium')
    elif s2c_h > hi:
        s2c = hi
        warn('sales_to_capital_capped', f'historical incremental sales-to-capital {s2c_h:.2f} above the type range; capped at {hi:.2f} so growth still costs capital', 'low')
    elif s2c_h < lo:
        s2c = max(s2c_h, .5 * lo)
        warn('sales_to_capital_low', f'historical incremental sales-to-capital {s2c_h:.2f} below the type range ({lo}-{hi}); the business is more capital-hungry than the type; used {s2c:.2f}', 'medium')
    else:
        s2c = s2c_h
    s2c_s = {'base': ov('base', 'sales_to_capital', s2c), 'bear': ov('bear', 'sales_to_capital', .8 * s2c),
             'bull': ov('bull', 'sales_to_capital', 1.2 * s2c)}
    # terminal ROIC from the moat assessment
    tax_eff = h['tax_effective']
    tax = _clamp(tax_eff, .15, .25) if tax_eff is not None else .21
    if tax_eff is not None and tax != tax_eff:
        warn('tax_normalized', f'effective tax {tax_eff:.1%} outside 15-25%; normalized to {tax:.1%} (override with evidence)', 'low')
    r0 = h['interest_expense'] / debt0 if h['interest_expense'] and debt0 > 0 else None
    r0 = _clamp(r0, .01, .15) if r0 is not None else rf + st['debt_spread']
    equity_mv = price * shares
    w_d = debt0 / (debt0 + equity_mv) if debt0 + equity_mv > 0 else 0.
    out = {}
    for s in SCEN:
        k_e = ov(s, 'cost_of_equity', ke)
        tx = ov(s, 'tax_rate', tax)
        wacc = max((1 - w_d) * k_e + w_d * r0 * (1 - tx), gT[s] + .03)
        if route['moat'] == 'strong':
            roic = min(max(h['roic'] or 0, wacc + .05), .40)
            roic = {'base': roic, 'bear': max(wacc + .02, .7 * roic), 'bull': min(.45, 1.2 * roic)}[s]
        else:
            roic = {'base': wacc + .02, 'bear': wacc + .01, 'bull': wacc + .04}[s]
        roic = ov(s, 'terminal_roic', roic)
        if roic < gT[s]:
            raise ValueError(f'{s}: terminal ROIC below terminal growth')
        out[s] = {'growth': [_clamp(x, -.5, 2.) for x in paths[s]], 'g_terminal': gT[s], 'mature_margin': ov(s, 'mature_margin', margins[s]),
                  's2c': s2c_s[s], 'ke': k_e, 'tax': tx, 'wacc': wacc, 'roic': roic,
                  'conv': 3 if route['method'] == 'normalized_cycle_fcff' else (max(2, round(.75 * st['conv'])) if s == 'bear' else st['conv'])}
    meta = {'r_fy': r_fy, 'g_cur': g_cur, 'cur_basis': cur_basis, 'stub_rate': stub_rate, 'consensus_growth': cg,
            'consensus_low': lows, 'consensus_high': highs, 'growth_kind': g_kind, 'spread': spread, 'half_life': hl,
            'margin_why': m_why, 's2c_history': s2c_h, 's2c_used': s2c, 's2c_range': s2c_range, 'tax_effective': tax_eff,
            'tax_used': tax, 'interest_existing': r0, 'debt_weight': w_d, 'g_terminal': gT}
    return out, meta


def _paths(P, meta, h, route, cal, open_bal, inv_income):
    st = STAGE[route['stage']]
    n = len(cal['boundaries'])
    m0 = h['m0'] if h['m0'] is not None else P['base']['mature_margin']
    sbc0, dep, cap = h['sbc_pct'], h['da_pct'], h['capex_pct']
    maint = min(cap, dep)
    wc = _clamp(h['wc_ratio'] if h['wc_ratio'] is not None else .05, -.3, .5)
    dr = _clamp(open_bal['deferred_revenue'] / meta['stub_rate'], 0, 1.5) if meta['stub_rate'] else 0.
    prepaid = min(1., 2 * dr) if dr > 0 else 0.
    out = {}
    for s in SCEN:
        p = P[s]
        g = p['growth']
        rates = [meta['stub_rate']]
        prev = meta['r_fy']
        for x in g:
            prev *= 1 + x
            rates.append(prev)
        mt, conv = p['mature_margin'], p['conv']
        margin = [m0] + [mt + (m0 - mt) * max(0., 1 - t / conv) for t in range(1, n)]
        sbc = [sbc0] + [sbc0 * (1 - st['sbc_fade'] * min(1., t / st['conv'])) for t in range(1, n)]
        ccr = [1 - m - x - dep for m, x in zip(margin, sbc)]
        if any(c < 0 or c > 2 for c in ccr):
            raise ValueError('cash-cost ratio outside driver bounds (operating losses above 100% of revenue or margins above 100%)')
        gg = [meta['g_cur']] + g
        gc = [max(0., x / (1 + x) * (1 / p['s2c'] - wc + dr)) for x in gg]
        months = P[s].get('cash_months', st['months'])
        min_cash = [max(0., min(months / 12 * c * r, open_bal['cash'])) for c, r in zip(ccr, rates)]
        out[s] = {'growth': [_r(x) for x in g], 'rates': rates, 'operating_margin': [_r(x) for x in margin],
                  'sbc_pct': [_r(x) for x in sbc], 'depreciation_pct': [_r(dep)] * n, 'cash_cost_ratio': [_r(x) for x in ccr],
                  'maintenance_capex_pct': [_r(maint)] * n, 'growth_capex_pct': [_r(x) for x in gc],
                  'tax_rate': [_r(p['tax'])] * n, 'interest_rate': [_r(meta['interest_existing'])] * n,
                  'deferred_revenue_ratio': [_r(dr)] * n, 'working_capital_ratio': [_r(wc)] * n, 'prepaid_share': [_r(prepaid)] * n,
                  'nol_usage_limit': [.8] * n, 'payout_fraction': [0.] * n,
                  'amortization': [0.] * n, 'minimum_cash': [float(round(x)) for x in min_cash],
                  'investment_income_after_tax': [float(round(inv_income * (1 - p['tax'])))] * n,
                  'debt_draw': [0.] * n, 'debt_repayment': [0.] * n, 'fixed_cash_costs': [0.] * n}
    return out, {'wc': wc, 'dr': dr, 'prepaid': prepaid, 'maint': maint, 'dep': dep, 'm0': m0, 'sbc0': sbc0}


# ---------------------------------------------------------------- execution helpers

def _run(spec):
    try:
        return value_company(build_case(spec)), None
    except ValueError as e:
        return None, str(e)


def _scen(spec, name):
    return next(s for s in spec['scenarios'] if s['name'] == name)


def _fund(spec, policy, allow, r_new, r0, debt0, locked):
    """Explicit funding in two phases, never silent.

    A: with no distributions, each cash shortfall below the minimum gets ASSUMED financing (debt stand-in,
       flagged). B: distributions start once free cash flow after interest stays positive; a later
       shortfall pauses the prior distribution instead of borrowing. A scenario whose assumed debt does not
       converge (a gap remains, or it still borrows in the last two years, i.e. borrows to pay interest) is
       reverted to unfunded: value_company then withholds its values until equity or committed funding is
       evidenced (driver_version 1 cannot issue equity)."""
    n = len(spec['boundaries'])
    lock = lambda nm, k: k in locked.get(nm, ())
    info = {sc['name']: {'draws': [0.] * n, 'retained': []} for sc in spec['scenarios']}
    for sc in spec['scenarios']:
        if not lock(sc['name'], 'payout_fraction'):
            sc['payout_fraction'] = [0.] * n

    def loop(step):
        for _ in range(2 * n + 10):
            res, err = _run(spec)
            if err:
                return err
            changed = False
            for sc, rs in zip(spec['scenarios'], res['scenarios']):
                j = next((i for i, row in enumerate(rs['forecast']) if row['funding_gap'] > 1e-6), None)
                if j is not None and step(sc, sc['name'], j, rs['forecast'][j]['funding_gap']):
                    changed = True
            if not changed:
                return None
        return None

    def borrow(sc, nm, j, gap):
        if not allow or lock(nm, 'debt_draw'):
            return False
        d = info[nm]['draws']
        d[j] += float(math.ceil(gap * 1.1 + 1))
        cum, rates = 0., []
        for x in d:
            cum += x
            rates.append(_r((debt0 * r0 + cum * r_new) / (debt0 + cum)) if debt0 + cum > 0 else _r(r0))
        sc['debt_draw'], sc['financing_status'] = list(d), 'assumed'
        if not lock(nm, 'interest_rate'):
            sc['interest_rate'] = rates
        return True

    def retain(sc, nm, j, gap):
        if j == 0 or sc['payout_fraction'][j - 1] <= 0 or lock(nm, 'payout_fraction'):
            return False
        sc['payout_fraction'][j - 1] = 0.
        info[nm]['retained'].append(spec['boundaries'][j - 1])
        return True

    err = loop(borrow)
    res, err = (None, err) if err else _run(spec)
    if err:
        return {'error': err}
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        f = [row['fcfe_before_new_equity'] - x for row, x in zip(rs['forecast'], info[nm]['draws'])]
        k = next(i for i in range(n + 1) if all(x > 0 for x in f[i:]))
        info[nm]['payout_starts'] = spec['boundaries'][k] if k < n else None
        if not lock(nm, 'payout_fraction'):
            sc['payout_fraction'] = [0.] * k + [policy[nm]] * (n - k)
    err = loop(retain)
    res, err = (None, err) if err else _run(spec)
    if err:
        return {'error': err}
    reverted = False
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        d = info[nm]['draws']
        gap = any(row['funding_gap'] > 1e-6 for row in rs['forecast'])
        if sum(d) and (gap or d[-1] > 0 or d[-2] > 0):
            info[nm]['reverted'] = (f'assumed debt of {sum(d):,.0f} did not converge (a shortfall remains or it still borrows at the end of the '
                                    'explicit forecast, i.e. borrows to pay interest): equity funding with dilution would be needed, which '
                                    'driver_version 1 cannot model; values are withheld instead of assuming free capital')
            sc['debt_draw'], sc['financing_status'] = [0.] * n, 'none'
            if not lock(nm, 'interest_rate'):
                sc['interest_rate'] = [_r(r0)] * n
            if not lock(nm, 'payout_fraction'):
                sc['payout_fraction'] = [0.] * n
            info[nm].update(draws=[0.] * n, retained=[], payout_starts=None)
            reverted = True
    if reverted:
        res, err = _run(spec)
        if err:
            return {'error': err}
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        gap = any(row['funding_gap'] > 1e-6 for row in rs['forecast'])
        tot = sum(info[nm]['draws'])
        info[nm].update(total_assumed=tot, status='unfunded' if gap else ('assumed_external_financing' if tot else 'self_funded'),
                        draws=[{'period_end': b, 'amount': x} for b, x in zip(spec['boundaries'], info[nm]['draws']) if x],
                        first_gap=next((row['end'] for row in rs['forecast'] if row['funding_gap'] > 1e-6), None))
        sc.setdefault('financing_status', 'none')
    return {'info': info, 'result': res}


# ---------------------------------------------------------------- ledger, sources, underwriting

def _case_sources(fb, ids, extra):
    cutoff = fb['as_of']
    avail = {}
    for f in fb['facts']:
        if facts.usable(fb, f):
            avail[f['source_id']] = max(avail.get(f['source_id'], '0000-00-00'), f['available_at'][:10])
    for e in fb.get('estimates') or []:
        if (e.get('as_of') or cutoff) <= cutoff:
            avail[e['source_id']] = max(avail.get(e['source_id'], '0000-00-00'), (e.get('as_of') or cutoff)[:10])
    price = (fb.get('market') or {}).get('price') or {}
    if price.get('source_id'):
        avail[price['source_id']] = max(avail.get(price['source_id'], '0000-00-00'), price['as_of'][:10])
    out = []
    for sid in sorted(ids):
        if sid in extra:
            out.append(extra[sid])
            continue
        s = fb['sources'][sid]
        kind, retrieval = KIND_MAP.get(s['kind'], (s['kind'], None))
        when = avail.get(sid, cutoff)
        row = {'id': sid, 'title': f"{s['provider']} {s['dataset']}" + (f" ({s['note']})" if s.get('note') else ''),
               'url': s.get('url') or f"connector://{s['provider']}/{s['dataset']}", 'kind': kind,
               'published_at': when, 'available_at': when}
        if retrieval:
            row['retrieval'] = retrieval
        out.append(row)
    return out


def build(fb, an, card, qa, inputs=None):
    """FactBook + analysis + model card + dataqa -> bear/base/bull driver spec with an assumption ledger.

    Returns {'status': built|method_not_supported_by_forecast|insufficient_data|invalid_spec, 'spec', 'spec_sha256',
    'method', 'assumptions', 'anchors', 'paths', 'cost_of_equity', 'funding', 'overrides', 'preview', 'warnings', 'reason'}."""
    inputs, an, card, qa = deepcopy(inputs or {}), an or {}, card or {}, qa or {}
    warnings = []

    def warn(code, message, severity='medium'):
        warnings.append({'code': code, 'severity': severity, 'message': message})

    overrides = _check_overrides(fb, inputs.get('overrides') or [])
    reviews = _check_reviews(inputs.get('reviews'))
    rf, rf_src, rf_note = _rate_input(fb, inputs.get('risk_free'), RF_DEFAULT, 'risk_free')
    erp, erp_src, erp_note = _rate_input(fb, inputs.get('equity_risk_premium'), ERP_DEFAULT, 'equity_risk_premium')

    def empty(status, reason, method=None):
        return {'forecast_version': FORECAST_VERSION, 'status': status, 'reason': reason, 'method': method, 'spec': None,
                'spec_sha256': None, 'assumptions': [], 'anchors': {}, 'paths': {}, 'cost_of_equity': None, 'funding': {},
                'overrides': [], 'preview': None, 'warnings': warnings}

    method = (card.get('valuation') or {}).get('method') or 'fcff_annual_path'
    bm = (card.get('business_model') or {}).get('primary')
    if method in NOT_BUILT or method not in BUILT:
        return empty('method_not_supported_by_forecast', f'valuation method {method!r} needs a dedicated model (residual income, '
                     'NAV, rNPV, SOTP) or no reliable valuation; the operating driver forecast does not apply', method)
    if bm in NON_OPERATING_TYPES:
        return empty('method_not_supported_by_forecast', f'business model {bm} routes to {NON_OPERATING_TYPES[bm]}, not an operating forecast', method)
    h = _history(fb, an, warn)
    h['guidance'] = _revenue_guidance(fb)
    if h is None:
        return empty('insufficient_data', 'no fiscal year with positive revenue available at as_of', method)
    h['g_anchor'] = h['g_trailing']
    route = _route(card, h, warn)
    if ROUTES.get(route['archetype']) != 'operating':
        return empty('method_not_supported_by_forecast', f"archetype {route['archetype']} is not an operating route", method)
    res = qa.get('resolved') or {}
    val = lambda k: _num((res.get(k) or {}).get('value'))
    missing = [k for k in ('shares_outstanding', 'cash', 'debt') if val(k) is None]
    price = (fb.get('market') or {}).get('price') or {}
    if missing:
        return empty('insufficient_data', 'dataqa did not resolve opening ' + ', '.join(missing) + '; no silent opening balance', method)
    if _num(price.get('value')) is None or price.get('currency', fb['company']['currency']) != fb['company']['currency']:
        return empty('insufficient_data', 'no quote in the company currency at as_of', method)
    as_of = fb['as_of']
    open_bal = {'cash': val('cash'), 'sti': val('short_term_investments') or 0., 'lti': val('long_term_investments') or 0.,
                'debt': val('debt'), 'shares': val('shares_outstanding'), 'deferred_revenue': val('deferred_revenue') or 0.}
    if qa.get('status') == 'blocked':
        warn('data_blocked', 'dataqa status blocked (' + ', '.join(qa.get('blocking') or []) + '): no price conclusion until resolved', 'high')
    for f in qa.get('limited') or []:
        warn('data_limited', f'dataqa field {f} is limited (single source or stale)', 'low')

    # ---- calendar and horizon
    g_probe = h['g_trailing']
    n_full = int(inputs.get('horizon_years') or next(n for lim, n in HORIZON if g_probe > lim))
    cal = _calendar(fb, h, n_full)
    cons, dropped = _consensus(fb, cal['boundaries'], h['last_end'])
    if dropped:
        warn('lookahead_estimates_ignored', f'{dropped} estimate rows dated after as_of were ignored', 'medium')
    if cons and not inputs.get('horizon_years'):
        b2 = cal['boundaries'][1]
        g_probe = cons[b2]['value'] / cons.get(cal['boundaries'][0], {'value': h['last_revenue']})['value'] - 1 if b2 in cons else g_probe
        n2 = next(n for lim, n in HORIZON if g_probe > lim)
        if n2 != n_full:
            n_full, cal = n2, _calendar(fb, h, n2)
            cons, _ = _consensus(fb, cal['boundaries'], h['last_end'])
    st = STAGE[route['stage']]
    ke, ke_parts = _cost_of_equity(route, h, rf, erp, [rf_note, erp_note])

    # ---- overrides
    applied = []
    econ = {}
    for o in overrides:
        if o['raw']:
            continue
        for s in (SCEN if o['scenario'] == 'all' else (o['scenario'],)):
            econ[(s, o['path'])] = o

    def ov(s, lever, default):
        o = econ.get((s, lever))
        if o is None:
            return default
        applied.append({'id': o['id'], 'path': lever, 'scenario': s, 'original': default, 'value': o['value'],
                        'reason': o['reason'], 'evidence_ids': o['evidence_ids']})
        return float(o['value'])

    try:
        P, meta = _params(route, h, cal, cons, rf, ke, open_bal['debt'], price['value'], open_bal['shares'], ov, warn)
        for s in SCEN:
            P[s]['cash_months'] = ov(s, 'minimum_cash_months', st['months'])
        inv_income = (open_bal['sti'] + open_bal['lti']) * rf
        paths, fixed = _paths(P, meta, h, route, cal, open_bal, inv_income)
    except ValueError as e:
        return empty('method_not_supported_by_forecast', str(e), method)
    # extend the horizon if any scenario ends far from its terminal growth (valuation_audit flags >10pp cliffs)
    gap_end = max(abs(paths[s]['growth'][-1] - P[s]['g_terminal']) for s in SCEN)
    if gap_end > .03 and n_full < 20 and not inputs.get('horizon_years'):
        inputs2 = dict(inputs, horizon_years=min(20, n_full + 5))
        out = build(fb, an, card, qa, inputs2)
        out['warnings'].append({'code': 'horizon_extended', 'severity': 'low',
                                'message': f'explicit horizon extended to {n_full + 5} fiscal years so growth fades before the terminal value'})
        return out

    # ---- spec skeleton
    tax_srcs = _latest_src(fb, 'income_tax', 'pretax_income')
    rev_srcs = _latest_src(fb, 'revenue')
    cons_srcs = sorted({e['source_id'] for e in cons.values()})
    fields = qa.get('fields') or {}
    def qsrc(field, keys, *fallback):
        """Sources dataqa used for a field / resolved value; FactBook latest facts when dataqa does not say."""
        ids = set((fields.get(field) or {}).get('sources') or [])
        for k in keys:
            r = res.get(k) or {}
            ids |= set(r.get('source_ids') or []) | ({r['source_id']} if r.get('source_id') else set())
        return sorted(i for i in ids if i in fb['sources']) or _latest_src(fb, *fallback)
    bal_srcs = (qsrc('cash_and_liquid_investments', ('cash', 'short_term_investments', 'long_term_investments'), 'cash', 'short_term_investments')
                + qsrc('debt', ('debt',), 'long_term_debt_tagged', 'long_term_debt', 'current_portion_ltd'))
    share_srcs = qsrc('shares_outstanding', ('shares_outstanding',), 'shares_outstanding_vendor', 'shares_outstanding_cover', 'shares_outstanding')
    extra = {RULES: {'id': RULES, 'title': 'fundamental_engine.forecast documented model rules (stage fade half-lives, type ranges, '
                     'business-risk table); base-rate references in docs/RESEARCH_BASIS.md', 'url': 'engine://fundamental_engine/forecast/rules',
                     'kind': 'model_rules', 'published_at': as_of, 'available_at': as_of}}
    for src in (rf_src, erp_src):
        if src and 'case' in src:
            extra[src['case']['id']] = src['case']
    rf_ids = [rf_src['fb'] if rf_src and 'fb' in rf_src else (rf_src['case']['id'] if rf_src else RULES)]
    erp_ids = [erp_src['fb'] if erp_src and 'fb' in erp_src else (erp_src['case']['id'] if erp_src else RULES)]
    rows = []

    def add(aid, topic, kind, values, rationale, falsifier, source_ids, provenance=None):
        ids = sorted(set(source_ids) & (set(fb['sources']) | set(extra))) or [RULES]
        rows.append({'id': aid, 'topic': topic, 'kind': kind,
                     'provenance': provenance or {'reported': 'reported', 'analyst': 'vendor_estimate', 'guidance': 'reported'}.get(kind, 'model_assumption'),
                     'values': values, 'rationale': rationale, 'falsifier': falsifier, 'source_ids': ids})

    B = cal['boundaries']
    fy = lambda b: f'FY ending {b}'
    # opening
    sh_res, cash_res, debt_res = res['shares_outstanding'], res['cash'], res['debt']
    stmt = min(_d(cash_res.get('as_of') or as_of), _d(debt_res.get('as_of') or as_of)).isoformat()
    add('open_balance', 'opening balance', 'reported',
        {'cash': open_bal['cash'], 'short_term_investments': open_bal['sti'], 'long_term_investments': open_bal['lti'],
         'debt': open_bal['debt'], 'deferred_revenue': open_bal['deferred_revenue'], 'statement_as_of': stmt},
        f"dataqa-resolved balance at {stmt}: cash {open_bal['cash']:,.0f}, investments {open_bal['sti'] + open_bal['lti']:,.0f} "
        f"(kept as assets, earning risk-free after tax, never spent), debt {open_bal['debt']:,.0f} ({debt_res.get('method', 'dataqa')}; "
        "operating leases treated in cash costs), deferred revenue {:,.0f} in its own ledger.".format(open_bal['deferred_revenue']),
        'A primary balance sheet at or after the statement date shows cash, investments or debt differing by more than 2% of market value.',
        bal_srcs)
    add('shares_point', 'point-in-time shares', 'reported', {'shares': open_bal['shares'], 'as_of': sh_res.get('as_of'), 'basis': sh_res.get('basis')},
        f"Point-in-time common shares {open_bal['shares']:,.0f} at {sh_res.get('as_of')} (basis {sh_res.get('basis')}); weighted-average "
        'EPS shares are not used for ownership.', 'The latest filing cover count differs by more than 1%.', share_srcs)
    conv_f = fields.get('convertibles') or {}
    add('dilution', 'dilution', 'model', {'incremental_dilutive_shares': 0},
        'No incremental shares added: future equity awards are charged as a cash-equivalent cost (sbc_pct), so adding unvested '
        f"awards would double count. Convertibles/warrants status in dataqa: {conv_f.get('status', 'not reported')}.",
        'Filings disclose in-the-money convertibles, warrants or vested options above 1% of shares.', share_srcs + [RULES])
    add('claims', 'other claims', 'model', {'other_claims': 0, 'net_runoff_obligation': 0},
        'No preferred stock, minority interests or other senior claims are recorded in the FactBook; deferred revenue renews in '
        'a going concern (ledger, not a runoff claim).', 'A filing shows preferred stock, material minority interests or contingent consideration.',
        bal_srcs + [RULES])
    sh_gap = abs((_d(sh_res.get('as_of') or stmt) - _d(stmt)).days)
    add('bridge', 'statement-to-as_of bridge', 'model', {'statement_as_of': stmt, 'as_of': as_of, 'events': []},
        f'No bridge events applied between the balance-sheet date {stmt} and as_of {as_of}: cash generated, buybacks and financing '
        'in between are NOT reflected' + (f'; share count is dated {sh_res.get("as_of")} ({sh_gap} days from the balance sheet), so cash and '
                                          'shares are from different dates' if sh_gap > 45 else '') + '. A reviewer must bridge or accept this.',
        'The next filing shows net cash or shares changed by more than 2% of market value since the statement date.', bal_srcs + share_srcs)
    if sh_gap > 45:
        warn('opening_dates_differ', f'shares dated {sh_res.get("as_of")} but cash/debt dated {stmt}: per-share cash may be mis-stated until bridged', 'high')
    if (_d(as_of) - _d(stmt)).days > 150:
        warn('stale_balance_sheet', f'balance sheet dated {stmt} is more than 150 days before as_of; valuation_audit will require a newer statement', 'high')
    # revenue and growth
    gd = h.get('guidance', {}).get(f'FY{B[0][:4]}')
    cur_kind = 'analyst' if B[0] in cons else ('guidance' if gd else 'model')
    if cur_kind == 'guidance':
        rev_srcs = list(rev_srcs) + [x for x in gd['source_ids'] if x not in rev_srcs]
    add('revenue_current_fy', 'current fiscal year revenue', cur_kind,
        {'revenue': meta['r_fy'], 'growth': meta['g_cur'], 'stub_annual_rate': meta['stub_rate']},
        f"{fy(B[0])} revenue {meta['r_fy']:,.0f} ({meta['cur_basis']}); growth {_pct(meta['g_cur'])} vs the last report. The stub "
        f"{as_of}..{B[0]} runs at {meta['stub_rate']:,.0f}/yr = current-year revenue x (1+g)^(0.5 - stub/2) (late-year run rate).",
        f"Reported {fy(B[0])} revenue differs from {meta['r_fy']:,.0f} by more than 3%.", rev_srcs + cons_srcs + [RULES])
    add('growth_fade', 'growth fade rule', 'model',
        {s: {'half_life_years': (meta['half_life'] if s == 'base' else meta['half_life'] * (.8 if s == 'bear' else 1.3)),
             'terminal_growth': P[s]['g_terminal']} for s in SCEN},
        f"Excess growth over terminal growth halves every {meta['half_life']:.1f} years in base ({route['stage']} stage; base rate: "
        '>20% growers slow to ~8% in five years and ~5% in ten, Koller et al.); bear fades 25% faster, bull 30% slower. Terminal growth '
        f"<= min(risk-free {rf:.2%}, 3%).", 'Revenue growth stays more than 5 points above the base path for two consecutive years, or falls '
        'below the bear path.', [RULES])
    for s in SCEN:
        g = paths[s]['growth']
        why = {'base': (f"Years 1-{len(meta['consensus_growth'])} follow revenue consensus, then fade" if meta['growth_kind'] == 'analyst'
                        else f"Anchored on trailing growth {_pct(h['g_trailing'])} ({h['g_trailing_formula']}) faded from the current year"),
               'bear': f"Realistic disappointment: first-year growth min(base - spread, half of base) with spread {_pct(meta['spread'])} "
                       '(max of stage floor, historical growth volatility and consensus dispersion), faster fade',
               'bull': f"Plausible upside: base + spread {_pct(meta['spread'])} (never above base + 2 spreads), slower fade"}[s]
        add(f'growth_{s}', f'{s} revenue growth', 'analyst' if s == 'base' and meta['growth_kind'] == 'analyst' else 'model',
            {s: g}, f"{why}: {_pct(g[0])} in {fy(B[1])}, {_pct(g[min(4, len(g) - 1)])} in {fy(B[min(5, len(B) - 1)])}, "
            f"{_pct(g[-1])} in {fy(B[-1])}; terminal {_pct(P[s]['g_terminal'])}.",
            f"Reported growth for {fy(B[1])} " + ({'base': f"below {_pct((g[0] + paths['bear']['growth'][0]) / 2)} or above {_pct((g[0] + paths['bull']['growth'][0]) / 2)}",
                                                   'bear': f"above {_pct(paths['base']['growth'][0])} (bear too harsh)",
                                                   'bull': f"below {_pct(paths['base']['growth'][0])} (bull not on track)"}[s]) + '.',
            rev_srcs + (cons_srcs if s == 'base' else []) + [RULES])
    # margins
    for s in SCEN:
        m = paths[s]['operating_margin']
        add(f'margin_{s}', f'{s} operating margin (after SBC)', 'model', {s: {'path': m, 'mature': P[s]['mature_margin']}},
            f"GAAP operating margin after SBC moves from {_pct(fixed['m0'])} ({h['m0_basis']}) to {_pct(P[s]['mature_margin'])} over "
            f"{P[s]['conv']} years, then holds. {meta['margin_why'] if s == 'base' else s + ' = base mature margin ' + ('- ' + _pct(st['m_spread']) if s == 'bear' else '+ 0.75 x ' + _pct(st['m_spread']) + ', capped at the type ceiling ' + _pct(route['type'][2]))}. "
            'cash_cost_ratio = 1 - (operating margin + sbc_pct + depreciation_pct), matching drivers.operating: '
            'ebit = revenue - cash costs - SBC - depreciation.',
            {'base': f"Reported operating margin for {fy(B[1])} below {_pct(m[1] - st['m_spread'] / 2)} without a one-off explanation.",
             'bear': f"Reported operating margin stays at or above the base path ({_pct(paths['base']['operating_margin'][1])} in {fy(B[1])}) for two years (bear too harsh).",
             'bull': f"Reported operating margin for {fy(B[1])} below the base path {_pct(paths['base']['operating_margin'][1])} (bull not on track)."}[s],
            _latest_src(fb, 'operating_income', 'revenue') + [RULES])
    add('sbc_path', 'stock-based compensation', 'model', {s: paths[s]['sbc_pct'] for s in SCEN},
        f"SBC {_pct(fixed['sbc0'])} of revenue today, fading by {st['sbc_fade']:.0%} of its level over {st['conv']} years at scale; charged as a "
        'cash-equivalent cost (policy cash_equivalent) with point-in-time shares, so no separate dilution is added. Value-neutral to '
        'the mix: the GAAP margin path already includes SBC.', 'SBC rises above today\'s share of revenue for two consecutive years.',
        _latest_src(fb, 'sbc', 'revenue'))
    add('da_capex', 'depreciation and maintenance capex', 'model', {'depreciation_pct': fixed['dep'], 'maintenance_capex_pct': fixed['maint']},
        f"Depreciation {_pct(fixed['dep'])} of revenue (latest D&A incl. amortization); maintenance capex {_pct(fixed['maint'])} = "
        f"min(3-year average capex {_pct(h['capex_pct'])}, depreciation): capex above depreciation is growth spending, modelled separately.",
        'Capex or D&A as a share of revenue moves by more than 1.5 points.', _latest_src(fb, 'd_and_a', 'capex', 'revenue'))
    add('reinvestment', 'growth reinvestment', 'model', {s: {'sales_to_capital': P[s]['s2c'], 'growth_capex_pct': paths[s]['growth_capex_pct']} for s in SCEN},
        f"Growth costs capital: total net reinvestment = change in revenue / incremental sales-to-capital (history {meta['s2c_history']}, "
        f"type range {meta['s2c_range']}, base {P['base']['s2c']:.2f}, bear x0.8, bull x1.2). growth_capex_pct = g/(1+g) x (1/S2C - "
        f"working_capital_ratio + deferred_revenue_ratio) so capex + working capital - deferred revenue funding equals that total "
        f"(no free float, no double count). Acquisition spend 3y: {_pct(h['acq_pct'])} of revenue.",
        'Net reinvestment per unit of new revenue over the next two years differs from the base by more than 50%.',
        _latest_src(fb, 'revenue', 'capex', 'equity') + [RULES])
    add('working_capital', 'working capital and deferred revenue', 'model',
        {'working_capital_ratio': fixed['wc'], 'deferred_revenue_ratio': fixed['dr'], 'prepaid_share': fixed['prepaid']},
        f"working_capital_ratio {fixed['wc']:.3f} = {h['wc_basis'] or 'default 5% (not computable)'}; deferred revenue kept at "
        f"{fixed['dr']:.1%} of the annual revenue rate (opening balance / stub rate); prepaid_share {fixed['prepaid']:.2f} = 2 x ratio "
        '(ledger split only, value-neutral).', 'Receivables+inventory-payables or deferred revenue move by more than 5% of revenue against revenue.',
        h['wc_sources'] + _latest_src(fb, 'deferred_revenue_current', 'revenue'))
    add('tax', 'tax rate', 'model', {s: P[s]['tax'] for s in SCEN},
        f"Normalized tax {P['base']['tax']:.1%}: 3-year effective {_pct(meta['tax_effective'])} bounded to 15-25% (21% statutory when "
        'pretax income is not positive); NOL usage capped at 80% of taxable income; opening NOL 0 unless supplied.',
        'Cash taxes exceed 25% or fall below 12% of pretax income for two years.', tax_srcs + [RULES])
    policy = {}
    for s in SCEN:
        dflt = 1. if (h['payout_history'] or 0) >= .5 or route['stage'] in ('mature', 'maturing', 'decline') else .5
        policy[s] = ov(s, 'payout_fraction', dflt)
    add('funding_policy', 'minimum cash and payout', 'model',
        {'minimum_cash_months': {s: P[s]['cash_months'] for s in SCEN}, 'payout_fraction': policy, 'payout_history_3y': h['payout_history']},
        f"Minimum operating cash = min({P['base']['cash_months']:.0f} months of cash costs, today's cash). Excess cash above it is "
        f"distributed at {policy['base']:.0%} (buybacks/dividends; history: distributions = {_pct(h['payout_history'])} of FCF over 3 years) "
        'only once free cash flow stays positive; retained cash earns nothing in the model (conservative).',
        'The company accumulates cash for two years without distributing, or distributes while cash falls below the minimum.',
        _latest_src(fb, 'buybacks', 'cfo', 'cash') + [RULES])
    r_new = rf + st['debt_spread']
    add('debt_interest', 'existing debt', 'model', {'interest_rate_existing': meta['interest_existing'], 'new_money_rate': r_new},
        f"Existing debt {open_bal['debt']:,.0f} held flat (maturities refinanced) at {meta['interest_existing']:.2%} "
        f"({'interest expense / debt' if h['interest_expense'] and open_bal['debt'] else 'risk-free + stage spread'}); any assumed new money at "
        f'{r_new:.2%} (risk-free + {st["debt_spread"]:.0%}).', 'Refinancing at a rate more than 2 points above the assumption or a covenant breach.',
        _latest_src(fb, 'interest_expense') + bal_srcs)
    add('investment_income', 'investment income', 'model', {'annual_after_tax': paths['base']['investment_income_after_tax'][0]},
        f"Marketable investments {open_bal['sti'] + open_bal['lti']:,.0f} earn the risk-free rate {rf:.2%} after tax; principal counted once in the terminal bridge.",
        'Investments are liquidated for acquisitions or buybacks.', bal_srcs + rf_ids)
    add('cost_of_equity', 'cost of equity and terminal WACC', 'model',
        {'cost_of_equity': {s: P[s]['ke'] for s in SCEN}, 'terminal_wacc': {s: P[s]['wacc'] for s in SCEN}, 'debt_weight': meta['debt_weight']},
        f"Ke {ke:.2%} = risk-free {rf:.2%} + ERP {erp:.2%} + business-risk add-ons " +
        ', '.join(f"{a['factor']} {a['value']:+.2%}" for a in ke_parts['adjustments']) +
        f". Terminal WACC {P['base']['wacc']:.2%} uses market-value debt weight {meta['debt_weight']:.1%} at {meta['interest_existing']:.2%} pre-tax "
        '(floored at terminal growth + 3 points). Same Ke in all scenarios.',
        'Long-term risk-free rate moves by more than 1 point, or leverage/funding risk changes the stage add-on.', rf_ids + erp_ids + [RULES])
    for s in SCEN:
        add(f'terminal_{s}', f'{s} terminal value', 'model',
            {s: {'growth': P[s]['g_terminal'], 'roic': P[s]['roic'], 'wacc': P[s]['wacc'], 'operating_margin': paths[s]['operating_margin'][-1]}},
            f"After {B[-1]}: growth {_pct(P[s]['g_terminal'])}, operating margin {_pct(paths[s]['operating_margin'][-1])} = the last explicit "
            f"year (no jump), reinvestment = g / ROIC with ROIC {_pct(P[s]['roic'])} ({route['moat']} moat: "
            + ({'base': 'returns stay above WACC: max(current ROIC, WACC+5pp), capped at 40%', 'bear': '70% of the base ROIC, at least WACC+2pp',
                'bull': '120% of the base ROIC, capped at 45%'}[s] if route['moat'] == 'strong' else
               {'base': 'excess returns competed down to WACC+2pp', 'bear': 'WACC+1pp', 'bull': 'WACC+4pp'}[s]) + ').',
            'Mature-stage margin or returns on capital of the company (or its closest peers) stay below these levels.',
            _latest_src(fb, 'operating_income', 'equity') + [RULES])
    if cons:
        add('consensus', 'consensus comparison', 'analyst', {b: {k: cons[b].get(k) for k in ('value', 'low', 'high', 'n_analysts', 'as_of')} for b in sorted(cons)},
            'Revenue consensus (vendor estimate, not a reported fact) for ' + ', '.join(fy(b) for b in sorted(cons)) +
            '; GAAP revenue on both sides. Agreement with consensus is not required; it anchors only the first two base years.',
            'A refreshed consensus differs by more than 5%.', cons_srcs)
        stale = [b for b, e in cons.items() if (_d(as_of) - _d(e.get('as_of') or as_of)).days > 90]
        if stale:
            warn('stale_estimate', 'consensus older than 90 days for ' + ', '.join(stale), 'medium')
    else:
        add('consensus', 'consensus comparison', 'model', {'status': 'unavailable'},
            'The FactBook carries no revenue consensus at as_of (not retrieved or not stored); nothing is fabricated. Growth is anchored on '
            'trailing growth instead.', 'A dated consensus becomes available and differs from the base first-year revenue by more than 10%.',
            rev_srcs + [RULES])
        warn('no_consensus', 'no revenue consensus in the FactBook: growth anchored on trailing growth only', 'medium')
    if h['n_years'] < 3:
        warn('short_history', f"only {h['n_years']} fiscal years of history", 'high')
    if h['ttm']['period_end'] and (_d(as_of) - _d(h['ttm']['period_end'])).days > 200:
        warn('stale_ttm', f"latest revenue period ends {h['ttm']['period_end']} ({h['ttm']['method']}), more than 200 days before as_of", 'medium')
    if (h['acq_pct'] or 0) > .05:
        warn('acquired_growth', f"acquisitions averaged {_pct(h['acq_pct'])} of revenue over 3 years: trailing growth includes bought growth", 'medium')

    # ---- spec
    ticker = fb['company']['ticker']
    company_id = f"{ticker.lower()}-cik{fb['company']['cik']}" if fb['company'].get('cik') else ticker.lower()
    scen = []
    for s in SCEN:
        p = paths[s]
        thesis = (f"{s.capitalize()}: revenue growth {_pct(p['growth'][0])} in {fy(B[1])} fading to {_pct(P[s]['g_terminal'])}; operating "
                  f"margin after SBC {_pct(p['operating_margin'][0])} -> {_pct(P[s]['mature_margin'])}; growth reinvestment at sales-to-capital "
                  f"{P[s]['s2c']:.2f}; Ke {P[s]['ke']:.2%}.")
        sc = {'name': s, 'thesis': thesis, 'assumption_ids': [f'growth_{s}', f'margin_{s}', 'cost_of_equity'],
              'period_assumption_ids': ['revenue_current_fy', 'growth_fade', f'growth_{s}', f'margin_{s}', 'sbc_path', 'da_capex',
                                        'reinvestment', 'working_capital', 'tax', 'funding_policy', 'debt_interest', 'investment_income'],
              'cost_of_equity': _r(P[s]['ke']), 'sbc_policy': 'cash_equivalent', 'growth': p['growth']}
        for k in list(RATIO_KEYS) + list(ABSOLUTE_KEYS):
            sc[k] = list(p[k]) if k in p else [0.0] * len(p['cash_cost_ratio'])   # e.g. equity_proceeds: no raise modelled
        sc['terminal'] = {'assumption_ids': [f'terminal_{s}', 'cost_of_equity', 'claims'], 'growth': _r(P[s]['g_terminal']),
                          'wacc': _r(P[s]['wacc']), 'roic': _r(P[s]['roic']), 'operating_margin': _r(1 - p['cash_cost_ratio'][-1] - p['sbc_pct'][-1] - p['depreciation_pct'][-1]),
                          'tax_rate': _r(P[s]['tax']), 'sbc_in_margin': True, 'net_runoff_obligation': 0, 'other_claims': 0,
                          'working_capital_rationale': 'Working capital and deferred revenue renew with the business in a going concern; their growth '
                                                       'is funded by the g/ROIC reinvestment rule. No runoff claim.'}
        scen.append(sc)
    spec = {'driver_version': 1, 'strict_drivers': True, 'is_demo': False, 'as_of': as_of, 'company_id': company_id, 'ticker': ticker,
            'currency': fb['company']['currency'], 'unit': 'absolute', 'archetype': route['archetype'], 'method': 'operating',
            'quote': {'price': float(price['value']), 'currency': price.get('currency', fb['company']['currency']), 'as_of': price['as_of'],
                      'source_ids': [price['source_id']]},
            'opening': {'as_of': as_of, 'assumption_ids': ['open_balance', 'shares_point', 'dilution', 'bridge'],
                        'unrestricted_cash': open_bal['cash'], 'debt': open_bal['debt'], 'shares': open_bal['shares'],
                        'deferred_revenue': open_bal['deferred_revenue'], 'tax_loss_carryforward': float(_num(inputs.get('nol')) or 0),
                        'marketable_investments': open_bal['sti'] + open_bal['lti']},
            'report_dates': cal['report_dates'], 'boundaries': B, 'stub_annual_revenue_rate': _r(meta['stub_rate'], 2),
            'last_full_year_revenue': _r(meta['r_fy'], 2), 'segment_name': 'Revenue',
            'segment_driver': 'subscribers' if route['business_model'] == 'subscription_software' else 'units', 'scenarios': scen}
    if _num(inputs.get('nol')):
        warn('nol_supplied', f"opening tax-loss carryforward {inputs['nol']:,.0f} supplied by inputs", 'low')
    elif fixed['m0'] < 0:
        warn('nol_unknown', 'loss-making company: tax-loss carryforwards are not in the FactBook and are set to 0 (conservative)', 'medium')
    # raw driver overrides (after paths, before funding)
    locked = {}
    for o in overrides:
        if not o['raw']:
            continue
        for s in (SCEN if o['scenario'] == 'all' else (o['scenario'],)):
            sc = _scen(spec, s)
            if o['path'].startswith('terminal.'):
                key = o['path'].split('.', 1)[1]
                orig, sc['terminal'][key] = sc['terminal'].get(key), float(o['value'])
            else:
                orig = sc[o['path']]
                need = len(orig)
                v = o['value'] if isinstance(o['value'], list) else [o['value']] * need
                if len(v) != need:
                    raise ValueError(f"override {o['path']}: needs {need} values")
                sc[o['path']] = [float(x) for x in v]
                locked.setdefault(s, set()).add(o['path'])
                if o['path'] in ('cash_cost_ratio', 'sbc_pct', 'depreciation_pct', 'amortization', 'fixed_cash_costs') and 'terminal.operating_margin' not in [x['path'] for x in overrides]:
                    last_rev = paths[s]['rates'][-1]
                    sc['terminal']['operating_margin'] = _r(1 - sc['cash_cost_ratio'][-1] - sc['sbc_pct'][-1] - sc['depreciation_pct'][-1]
                                                            - (sc['amortization'][-1] + sc['fixed_cash_costs'][-1]) / last_rev)
            applied.append({'id': o['id'], 'path': o['path'], 'scenario': s, 'original': orig, 'value': o['value'],
                            'reason': o['reason'], 'evidence_ids': o['evidence_ids']})
    by_id = {}
    for a in applied:
        by_id.setdefault(a['id'], []).append(a)
    for oid, items in sorted(by_id.items()):
        ev = items[0]['evidence_ids']
        srcs = [e for e in ev if e in fb['sources']] + [s for q in fb.get('qualitative', []) if q['id'] in ev for s in q['source_ids']]
        add(oid, 'analyst override: ' + items[0]['path'], 'analyst', {i['scenario']: {'original': i['original'], 'override': i['value']} for i in items},
            f"Analyst override of {items[0]['path']}: {items[0]['reason']} (evidence {', '.join(ev)}); original engine value recorded.",
            'The cited evidence is contradicted by a later filing or the override reason no longer holds.', srcs, provenance='model_assumption')
        for i in items:
            sc = _scen(spec, i['scenario'])
            sc['assumption_ids'].append(oid)
            sc['period_assumption_ids'].append(oid)

    # ---- assumptions, sources, underwriting (attached before funding runs the spec, refreshed after)
    material = []
    if qa.get('status') == 'blocked':
        material.append({'id': 'data_blocked', 'status': 'unresolved', 'description': 'dataqa blocked critical fields: ' + ', '.join(qa.get('blocking') or [])})
    if sh_gap > 45:
        material.append({'id': 'opening_dates_differ', 'status': 'resolved' if reviews.get('bridge', {}).get('status') == 'reviewed' else 'unresolved',
                         'description': f'shares dated {sh_res.get("as_of")}, balance sheet {stmt}: bridge needed', 'assumption_ids': ['bridge']})

    def finalize():
        known = {r['id'] for r in rows}
        case_assumptions = []
        for r in rows:
            rv = reviews.get(r['id'], {})
            a = {'id': r['id'], 'kind': r['kind'], 'as_of': as_of, 'review_status': 'reviewed' if rv.get('status') == 'reviewed' else 'unreviewed',
                 'source_ids': r['source_ids'], 'rationale': r['rationale'], 'falsifier': r['falsifier']}
            if rv.get('status') == 'reviewed':
                a['reviewer'], a['review_note'] = rv['reviewer'], rv.get('note', '')
            r['review_status'] = a['review_status']
            case_assumptions.append(a)
        used = {x for r in rows for x in r['source_ids']} | {price['source_id']}
        spec['sources'] = _case_sources(fb, used, extra)
        spec['is_demo'] = any(fb['sources'][x]['kind'] == 'synthetic' for x in used if x in fb['sources'])
        spec['assumptions'] = case_assumptions
        fin = lambda s: [f'financing_{s}'] if f'financing_{s}' in known else []
        spec['underwriting'] = {
            'opening_reconciliation': {
                'assumption_ids': ['open_balance', 'shares_point'], 'statement_as_of': stmt, 'share_basis': 'point_in_time_common_outstanding',
                'cash_and_equivalents': open_bal['cash'], 'restricted_cash': val('restricted_cash') or 0., 'short_term_investments': open_bal['sti'],
                'long_term_investments': open_bal['lti'], 'investment_haircut': 0, 'debt': open_bal['debt'],
                'common_shares_outstanding': open_bal['shares'], 'incremental_dilutive_shares': 0,
                'dilution_review': {'assumption_ids': ['dilution']}, 'claims_review': {'assumption_ids': ['claims']},
                'bridge_events': [], 'bridge_review': {'assumption_ids': ['bridge']}},
            'scenario_reviews': {s: {'demand_and_capacity': {'assumption_ids': [f'growth_{s}', 'revenue_current_fy', 'growth_fade']},
                                     'funding_and_claims': {'assumption_ids': ['funding_policy', 'debt_interest', 'open_balance', 'claims'] + fin(s)},
                                     'competitive_response': {'assumption_ids': [f'margin_{s}', 'reinvestment']},
                                     'maturity': {'assumption_ids': [f'terminal_{s}', 'sbc_path', 'cost_of_equity']}} for s in SCEN},
            'consensus_review': {'status': 'unavailable', 'assumption_ids': ['consensus'], 'comparisons': []},
            'material_issues': deepcopy(material)}
        if B[1] in cons:
            e = cons[B[1]]
            spec['underwriting']['consensus_review'] = {
                'status': 'compared', 'assumption_ids': ['consensus'],
                'comparisons': [{'id': f'revenue_{B[1]}', 'scenario': 'base', 'metric': 'revenue', 'period_start': B[0], 'period_end': B[1],
                                 'as_of': e.get('as_of') or as_of, 'available_at': e.get('as_of') or as_of, 'currency': fb['company']['currency'],
                                 'unit': 'absolute', 'external_value': float(e['value']), 'external_basis': 'gaap_revenue',
                                 'model_basis': 'gaap_revenue', 'assumption_ids': ['consensus']}]}

    finalize()
    # ---- funding: payout schedule and explicit (assumed) financing
    allow = inputs.get('financing', 'assumed_debt') != 'none'
    fund = _fund(spec, policy, allow, r_new, meta['interest_existing'], open_bal['debt'], locked)
    if 'error' in fund:
        return dict(empty('invalid_spec', fund['error'], method), spec=spec, assumptions=rows)
    for s in SCEN:
        f = fund['info'][s]
        if f['status'] == 'assumed_external_financing':
            first = f['draws'][0]['period_end']
            add(f'financing_{s}', f'{s} assumed financing', 'model', {s: f['draws']},
                f"Cash would fall below the minimum operating balance from {first}: external financing of {f['total_assumed']:,.0f} is ASSUMED, "
                f"not committed, modelled as debt at {r_new:.2%} because driver_version 1 cannot issue equity. An equity raise would dilute "
                'current holders instead, so per-share values are optimistic relative to that path.',
                f'No committed financing (facility, offering or partner prepayment) exists before {first}, or terms are worse than {r_new:.2%}.',
                _latest_src(fb, 'cash', 'cfo') + [RULES])
            _scen(spec, s)['period_assumption_ids'].append(f'financing_{s}')
            warn('financing_assumed', f"{s}: {f['total_assumed']:,.0f} of external financing assumed (not committed) from {first}", 'high')
            material.append({'id': f'assumed_financing_{s}', 'description': f'{s} plan depends on assumed (uncommitted) financing',
                             'status': 'resolved' if reviews.get(f'financing_{s}', {}).get('status') == 'reviewed' else 'unresolved',
                             'assumption_ids': [f'financing_{s}']})
        elif f['status'] == 'unfunded':
            why = f.get('reverted') or 'financing assumptions are disabled (inputs financing=none)'
            warn('funding_blocked', f"{s}: cash falls below the minimum from {f['first_gap']}; {why}; value_company withholds this scenario's values", 'high')
        if f['retained']:
            warn('payout_paused', f"{s}: distributions paused before {', '.join(f['retained'])} to keep the minimum cash balance", 'low')
    if route['method'] == 'scenario_survival_fcff':
        warn('survival_model', 'scenario survival model: values depend on funding and on reaching profitability; lower confidence', 'high')
    unknown_reviews = sorted(set(reviews) - {r['id'] for r in rows})
    if unknown_reviews:
        warn('unknown_review_ids', 'reviews supplied for ids not in this forecast: ' + ', '.join(unknown_reviews), 'low')
    finalize()

    # ---- preview execution (the pipeline executes again; this run proves the spec is valid)
    result, err = _run(spec)
    if err:
        return dict(empty('invalid_spec', err, method), spec=spec, assumptions=rows)
    today = result['annual_values'][0]
    terminal_share = {r['name']: r.get('terminal_share_of_present_value') for r in result['scenarios']}
    for s, v in terminal_share.items():
        if v is not None and v > .8:
            warn('terminal_dominant', f'{s}: {v:.0%} of present value comes from the terminal value', 'medium')
    if all(today.get(k) is not None for k in SCEN) and not today['bear'] <= today['base'] <= today['bull']:
        warn('scenario_values_cross', 'today: bear <= base <= bull does not hold; labels were not re-sorted', 'high')
    preview = {'status': result['status'], 'input_sha256': result['input_sha256'],
               'today': {k: today.get(k) for k in SCEN}, 'quote': today['current_quote'],
               'terminal_share_of_present_value': terminal_share,
               'funding_status': {r['name']: r['status'] for r in result['scenarios']},
               'audit_issue_codes': sorted({i['code'] for i in result['underwriting_audit']['issues']}),
               'eligible_for_research_synthesis': result['underwriting_audit']['eligible_for_research_synthesis'],
               'note': 'Internal consistency run of this spec; pipeline.py executes value_company itself. Conditional values, not price forecasts.'}
    path_out = {s: [{'end': b, 'growth': (None if i == 0 else paths[s]['growth'][i - 1]), 'revenue_rate': _r(paths[s]['rates'][i], 0),
                     'operating_margin': _r(1 - _scen(spec, s)['cash_cost_ratio'][i] - _scen(spec, s)['sbc_pct'][i] - _scen(spec, s)['depreciation_pct'][i]),
                     'sbc_pct': _scen(spec, s)['sbc_pct'][i], 'growth_capex_pct': _scen(spec, s)['growth_capex_pct'][i],
                     'payout_fraction': _scen(spec, s)['payout_fraction'][i], 'debt_draw': _scen(spec, s)['debt_draw'][i]}
                    for i, b in enumerate(B)] for s in SCEN}
    anchors = {'last_fiscal_year_end': h['last_end'], 'last_fiscal_year_revenue': h['last_revenue'], 'fiscal_years': h['n_years'],
               'ttm': h['ttm'], 'current_fiscal_year': {'end': B[0], 'revenue': meta['r_fy'], 'growth': meta['g_cur'], 'basis': meta['cur_basis']},
               'stub': {'start': as_of, 'end': B[0], 'years': cal['stub_years'], 'annual_rate': meta['stub_rate']},
               'growth': {'last': h['g_last'], 'cagr_3y': h['cagr_3y'], 'cagr_5y': h['cagr_5y'], 'recent_annualized': h['g_recent'],
                          'trailing_anchor': h['g_trailing'], 'formula': h['g_trailing_formula'], 'volatility': h['growth_volatility'],
                          'consensus': {b: e['value'] for b, e in sorted(cons.items())}, 'scenario_spread': meta['spread']},
               'operating_margin': {'current': h['m0'], 'basis': h['m0_basis'], 'best_3y': h['m_best3'], 'history': h['m_all']},
               'sbc_pct': h['sbc_pct'], 'da_pct': h['da_pct'], 'capex_pct_3y': h['capex_pct'],
               'sales_to_capital': {'history': meta['s2c_history'], 'used': meta['s2c_used'], 'type_range': meta['s2c_range']},
               'working_capital_ratio': {'value': fixed['wc'], 'basis': h['wc_basis']}, 'deferred_revenue_ratio': fixed['dr'],
               'tax': {'effective_3y': meta['tax_effective'], 'used': meta['tax_used']}, 'interest_rate_existing': meta['interest_existing'],
               'roic': h['roic'], 'payout_history_3y': h['payout_history'], 'net_debt_to_ebitda': h['nd_ebitda'], 'opening': open_bal,
               'route': {k: route[k] for k in ('method', 'business_model', 'stage', 'archetype', 'cyclical', 'capital_intensity', 'funding', 'moat')},
               'calendar': {k: cal[k] for k in ('boundaries', 'report_dates', 'fiscal_year_end', 'report_rule')}}
    json.dumps(spec, allow_nan=False)
    return {'forecast_version': FORECAST_VERSION, 'status': 'built', 'reason': None, 'method': method, 'spec': spec,
            'spec_sha256': _sha(spec), 'assumptions': rows, 'anchors': anchors, 'paths': path_out,
            'cost_of_equity': {'bear': P['bear']['ke'], 'base': P['base']['ke'], 'bull': P['bull']['ke'], 'components': ke_parts,
                               'terminal_wacc': {s: P[s]['wacc'] for s in SCEN}},
            'funding': fund['info'], 'overrides': applied, 'preview': preview, 'warnings': warnings}


# ---------------------------------------------------------------- what must be true

def _bisect(f, lo, hi, target, increasing=True, steps=48):
    vlo, vhi = f(lo), f(hi)
    if vlo is None or vhi is None:
        return None, 'invalid_search_domain', (vlo, vhi)
    if not (min(vlo, vhi) <= target <= max(vlo, vhi)):
        return None, 'not_bracketed', (vlo, vhi)
    for _ in range(steps):
        mid = (lo + hi) / 2
        v = f(mid)
        if v is None:
            return None, 'invalid_search_domain', (vlo, vhi)
        if (v < target) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2, 'solved', (vlo, vhi)


def _value(spec):
    r, err = _run(spec)
    return None if err else r['annual_values'][0]['base']


def what_must_be_true(spec, price):
    """Levers that make the base-case value today equal the price, one at a time, everything else fixed.

    Sensitivities of THIS spec, not market expectations: growth (drivers.implied_growth_shift), mature
    margin (cash-cost ratio shift ramped in over five years, terminal margin moved by the same amount),
    capital efficiency (growth capex and terminal reinvestment scaled) and the return the price implies
    (valuation_tools.implied_cost_of_equity)."""
    if not spec:
        return []
    s = deepcopy(spec)
    s['quote'] = dict(s['quote'], price=number(price, 'price', 1e-9))
    base = _scen(s, 'base')
    stub = 1 if s.get('stub_annual_revenue_rate') is not None else 0
    rows = []

    def side(rng):
        vals = [v for v in rng or () if v is not None]
        if len(vals) < 2:
            return ' (the search ran into an invalid or unfunded case)'
        where = 'above' if min(vals) > s['quote']['price'] else 'below'
        return (f': across the searched range the base value stays {where} the price ({min(vals):,.2f} to {max(vals):,.2f}), '
                'so this lever alone does not explain the price')

    def row(lever, label, status, required, anchor, meaning, unit, detail=None):
        rows.append({'lever': lever, 'label': label, 'status': 'solved' if status == 'solved' else 'not_reachable',
                     'required': required, 'anchor': anchor, 'unit': unit, 'meaning': meaning,
                     'detail': detail or {'search_status': status}})

    g = base['growth'] if isinstance(base['growth'], list) else [base['growth']]
    g_anchor = sum(g[:5]) / len(g[:5])
    r = implied_growth_shift(s)
    if r['status'] == 'solved':
        req = g_anchor + r['growth_shift']
        row('growth', 'average revenue growth, next five fiscal years', 'solved', req, g_anchor,
            f'At {price:,.2f} the base case needs average revenue growth of {req:.1%} a year over the next five fiscal years '
            f'(base {g_anchor:.1%}), with margins, capital needs and the discount rate unchanged.', 'ratio',
            {'search_status': 'solved', 'growth_shift': r['growth_shift'], 'limitation': r['limitation']})
    else:
        row('growth', 'average revenue growth, next five fiscal years', r['status'], None, g_anchor,
            'No uniform growth shift between -15 and +30 points reconciles the base case with this price' + side(r.get('range_values')) + '.', 'ratio',
            {'search_status': r['status'], 'limitation': r['limitation']})
    # mature margin: cash-cost ratio lowered by delta, ramped in over five full years; terminal margin + delta
    t_m = base['terminal']['operating_margin']
    ccr = base['cash_cost_ratio']
    ramp = [0. if (stub and i == 0) else min(1., (i + 1 - stub) / 5) for i in range(len(ccr))]

    def margin(delta):
        x = deepcopy(s)
        sc = _scen(x, 'base')
        sc['cash_cost_ratio'] = [c - delta * w for c, w in zip(ccr, ramp)]
        sc['terminal']['operating_margin'] = t_m + delta
        if any(c < 0 or c > 2 for c in sc['cash_cost_ratio']) or not 0 < t_m + delta <= 1:
            return None
        return _value(x)
    hi = min(.30, min(c for c, w in zip(ccr, ramp) if w) - 1e-6, 1 - t_m)
    lo = max(-.25, -t_m + .001)
    d, st, rng = _bisect(margin, lo, hi, s['quote']['price'], True)
    row('mature_margin', 'mature operating margin after SBC', st, None if d is None else t_m + d, t_m,
        (f'At {price:,.2f} the base case needs a mature operating margin of {t_m + d:.1%} (base {t_m:.1%}), reached over five years, '
         'with growth and capital needs unchanged.') if d is not None else
        f'No mature margin between {t_m + lo:.1%} and {t_m + hi:.1%} reconciles the base case with this price' + side(rng) + '.', 'ratio',
        {'search_status': st, 'margin_shift': d, 'range_values': rng})
    # capital efficiency: growth capex and terminal reinvestment (g / ROIC) scaled by k
    gc, roic, gt = base['growth_capex_pct'], base['terminal']['roic'], base['terminal']['growth']

    def capital(k):
        x = deepcopy(s)
        sc = _scen(x, 'base')
        sc['growth_capex_pct'] = [min(1., c * k) for c in gc]
        sc['terminal']['roic'] = min(1., roic / k) if k > 0 else 1.
        if sc['terminal']['roic'] < gt:
            return None
        return _value(x)
    k_hi = min(6., roic / gt - 1e-6) if gt > 0 else 6.
    k, st, rng = _bisect(capital, 0., k_hi, s['quote']['price'], False)
    row('capital_efficiency', 'growth investment per unit of new revenue (x base)', st, k, 1.,
        (f'At {price:,.2f} the base case still works if growth needs {k:.2f}x the capital assumed in base (explicit growth capex and '
         f'terminal reinvestment; terminal ROIC {min(1., roic / k) if k else 1:.0%} vs {roic:.0%}).') if k is not None else
        f'No scaling of growth investment between zero and {k_hi:.1f}x (terminal ROIC down to terminal growth) reconciles the base '
        'case with this price' + side(rng) + '.', 'x',
        {'search_status': st, 'range_values': rng})
    # return implied by the price
    case = build_case(s)
    r = implied_cost_of_equity(case)
    ke = base['cost_of_equity']
    if r['status'] == 'solved':
        row('cost_of_equity', 'annual return implied by the price (base cash flows)', 'solved', r['implied_cost_of_equity'], ke,
            f"If the base-case cash flows happen, buying at {price:,.2f} returns about {r['implied_cost_of_equity']:.1%} a year "
            f'(the model requires {ke:.1%}). Not a forecast of the market price.', 'ratio',
            {'search_status': 'solved', 'limitation': r['limitation']})
    else:
        row('cost_of_equity', 'annual return implied by the price (base cash flows)', r['status'], None, ke,
            'The implied return lies outside the searched range (Ke -8 to +12 points)' + side(r.get('range_values')) + '.', 'ratio',
            {'search_status': r['status'], 'limitation': r['limitation']})
    return rows
