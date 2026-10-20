"""Bear / base / bull business forecast, emitted as a driver spec (driver_version 1).

Why: a valuation is only as good as the business forecast behind it. This module turns the FactBook,
the financial analysis (analysis.py), the business model card (model_card.py) and the validated
opening balance (dataqa.py) into explicit paths for growth, GAAP operating margin (after SBC),
reinvestment, working capital, SBC, tax, funding and the discount rate. Every number becomes an
assumption row with a kind, a rationale, a falsifier and sources, and the paths are written as a
drivers.build_case spec that valuation.value_company executes and valuation_audit reviews.

Growth costs capital (growth capex from incremental sales-to-capital, converging to the terminal g / ROIC rule), funding
is explicit (investments first, SBC settled in shares, assumed debt within capacity, then assumed equity with priced
dilution; labelled and flagged, never silent), SBC is a cash-equivalent cost with point-in-time shares (no double counting
with dilution), the terminal margin equals the last explicit year and depreciation converges to maintenance capex. The
cost of equity never depends on the quote: leverage is measured against the model's own equity value. Cyclicals start
from the cycle (mid-cycle trend revenue and margin), declining businesses may keep declining, and the fifth anniversary
of as_of is always an explicit valuation date.

The constants below are MODEL ASSUMPTIONS: chosen once from base rates and documented here, cited in
the ledger through the 'engine:forecast_rules' source, and never tuned toward a return target or
the owner's 14% aspiration. Generated assumptions stay 'unreviewed' until an independent reviewer
marks them reviewed (inputs['reviews']); only then can the valuation become eligible for synthesis.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from . import facts
from .drivers import build_case, RATIO_KEYS, ABSOLUTE_KEYS
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
KE_FACTORS = (  # (condition, add-on, reason) evaluated on the card and the executed funding result
    ('cyclical', .005, 'cyclical revenue and margins'),
    ('capital_intensity_high', .005, 'high capital intensity (operating leverage on fixed assets)'),
    ('needs_external_funding', .01, 'base plan depends on external funding (executed funding result)'),
)
# Financial leverage is NOT a table add-on: Ke is relevered from the unlevered (business-risk) rate with the debt weight
# taken from the model's own base equity value (fixed point, never the quote): Harris-Pringle, tax shields at the
# operating risk, Ke = Ku + (Ku - Kd) x D/E and WACC = Ku - w x Kd x tax.
W_MAX = .6                # debt weight cap when the modelled equity is tiny (distress): flagged, not hidden
DEBT_CAPACITY = 3.0       # assumed new borrowing only up to 3x EBITDA before SBC (the usual high-leverage line); beyond -> equity
EQUITY_FEE = .03          # gross spread on an assumed follow-on equity raise
# Revenue-scale growth ceilings for the trailing anchor (revenue below x -> anchor at most y). Orders of magnitude from
# revenue-growth base rates by company size (sustained growth this fast is rare at that scale; cf. Mauboussin & Callahan,
# The Base Rate Book, 2016), NOT a calibrated study. Guidance or consensus replaces the anchor when present; a backlog
# (RPO) of at least one year of revenue lifts the ceiling one band.
SCALE_CAPS = ((1e8, 1.0), (1e9, .6), (1e10, .4), (5e10, .25), (float('inf'), .15))
CYCLE_TREND = (-.03, TERMINAL_GROWTH_CAP + .02)   # bounds on the long-run (trend) growth of a cyclical's revenue
CYCLE_CONV = {'bear': 2, 'base': 3, 'bull': 4}     # years for a cyclical to revert from today's level to its cycle path
DECLINE_FLOOR = -.05      # lowest perpetual growth for a structurally declining business
KNOWN_INPUTS = ('overrides', 'reviews', 'risk_free', 'equity_risk_premium', 'horizon_years', 'financing', 'nol')
FINANCING_MODES = ('assumed', 'assumed_debt', 'none')   # 'assumed_debt' kept as the legacy name of the default
WEAK_DIMENSIONS = ('customer_concentration', 'technology_ai', 'governance', 'competition')
ECON_LEVERS = ('growth_anchor', 'growth_half_life', 'terminal_growth', 'mature_margin', 'sales_to_capital',
               'terminal_roic', 'cost_of_equity', 'tax_rate', 'payout_fraction', 'minimum_cash_months')
TERMINAL_KEYS = ('growth', 'wacc', 'roic', 'operating_margin', 'tax_rate', 'other_claims', 'net_runoff_obligation')
KIND_MAP = {'filing_derived': ('filing', 'provider_normalized'), 'vendor_normalized': ('provider_data', 'provider_normalized'),
            'market_feed': ('market_data', 'market_feed'), 'consensus': ('analyst_estimates', 'provider_normalized'),
            'document': ('document', None), 'synthetic': ('synthetic', None), 'secondary': ('secondary_source', 'search_excerpt')}
# issuer documents (and legacy 'document' sources whose dataset is a filing or release) keep their primary kind; how they
# were read is carried in retrieval, so a 10-Q seen through search excerpts counts as primary but not read directly
ISSUER_DATASETS = {'filing': 'filing', 'issuer_release': 'issuer_release', 'earnings_release': 'earnings_release'}
RETRIEVALS = ('primary_document', 'provider_normalized', 'search_excerpt', 'market_feed')
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
        extra = sorted(set(o) - {'path', 'scenario', 'value', 'reason', 'evidence_ids'})
        if extra:
            raise ValueError(f'override {i}: unknown keys {extra} (path, scenario, value, reason, evidence_ids)')
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
    if not isinstance(spec, dict):
        raise ValueError(f'{label}: must be an object with value, as_of and source_id or title')
    extra = sorted(set(spec) - {'value', 'as_of', 'source_id', 'title', 'url', 'kind', 'retrieval'})
    if extra:
        raise ValueError(f'{label}: unknown keys {extra}')
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
    cyc_card = card.get('cyclical') or {}
    cyclical = bool(cyc_card.get('value')) or method == 'normalized_cycle_fcff'        # cyclical economics (Ke add-on)
    normalize = method == 'normalized_cycle_fcff' or bool(cyc_card.get('normalize'))   # history shows a cycle: normalize it
    if normalize and stage in ('early', 'growth'):
        warn('cyclical_stage_normalized', f'card stage {stage} for a cyclical business: growth measured into a cyclical peak is not a '
                                          'growth stage; the forecast uses mature-stage parameters around the cycle', 'medium')
        stage = 'mature'
    if bm not in TYPE:
        warn('business_model_default', f'business model {bm!r} has no forecast type table; generic ranges used', 'low')
    typ = TYPE.get(bm, DEFAULT_TYPE)
    archetype = val.get('archetype') or typ[3]
    dims = card.get('dimensions') or {}
    moat, basis = (dims.get('moat') or {}).get('assessment', 'unknown'), 'card'
    if moat not in ('strong', 'adequate', 'weak'):
        # unknown moat: use the computed record (labelled as a proxy) instead of silently capping a demonstrated compounder
        pp, roic, inc = (dims.get('pricing_power') or {}).get('assessment'), h.get('roic'), h.get('inc_roic')
        if not normalize and pp == 'strong' and (roic or 0) >= .20 and (inc is None or inc >= .15):
            moat, basis = 'strong', (f'computed proxy (no analyst moat assessment): pricing-power proxy strong, ROIC {_pct(roic)}, '
                                     f'incremental ROIC {_pct(inc)}')
            warn('moat_computed_proxy', f'moat unassessed; strong-moat rules applied from the computed record ({basis}); an analyst '
                                        'assessment with evidence replaces it', 'medium')
        else:
            moat, basis = 'unknown', 'no analyst assessment and no computed proxy (pricing power strong and ROIC >= 20%)'
    return {'method': method, 'business_model': bm, 'stage': stage, 'type': typ, 'archetype': archetype, 'cyclical': cyclical, 'normalize': normalize,
            'capital_intensity': (card.get('capital_intensity') or {}).get('value'),
            'funding': (card.get('funding') or {}).get('value'), 'moat': moat, 'moat_basis': basis, 'dimensions': dims}


def _latest_src(fb, *metrics):
    ids = []
    for m in metrics:
        s = facts.series(fb, m, 'annual') or facts.series(fb, m, 'instant')
        if s:
            ids.append(s[-1][2]['source_id'])
    return sorted(set(ids))


def _ytd(fb, metric, last_end, upto=None):
    """Fiscal year to date after the last fiscal year end (standalone quarters, contiguous, or a YTD fact), with the same
    months a year earlier when reported: {'value', 'end', 'prior', 'basis', 'source_ids'} or None. upto: end at that date."""
    a_end = _d(last_end)
    lim = _d(upto) + timedelta(days=7) if upto else None
    q = [x for x in facts.series(fb, metric, 'quarter') if lim is None or _d(x[0]) <= lim]
    post = [x for x in q if 7 < (_d(x[0]) - a_end).days <= 366]     # the fiscal year after the last reported one only
    steps = [(_d(b[0]) - _d(a)).days for a, b in zip([last_end] + [x[0] for x in post], post)]
    if post and all(60 <= x <= 120 for x in steps) and (upto is None or abs((_d(post[-1][0]) - _d(upto)).days) <= 7):
        prior = [next((y for y in q if abs((_d(x[0]) - _d(y[0])).days - 364) <= 10), None) for x in post]
        return {'value': sum(x[1] for x in post), 'end': post[-1][0], 'prior': sum(y[1] for y in prior) if all(prior) else None,
                'basis': f'{len(post)} reported quarter(s) after {last_end}', 'source_ids': sorted({x[2]['source_id'] for x in post})}
    ytd = [f for f in facts.candidates(fb, metric) if f['period'].startswith('YTD@')]
    cur = sorted((f for f in ytd if 7 < (_d(f['period_end']) - a_end).days <= 366 and (lim is None or _d(f['period_end']) <= lim)),
                 key=lambda f: f['period_end'])
    if cur and (upto is None or abs((_d(cur[-1]['period_end']) - _d(upto)).days) <= 7):
        now = cur[-1]
        prev = [f for f in ytd if abs((_d(now['period_end']) - _d(f['period_end'])).days - 364) <= 10]
        return {'value': now['value'], 'end': now['period_end'], 'prior': prev[0]['value'] if prev else None,
                'basis': f'year-to-date fact to {now["period_end"]}', 'source_ids': [now['source_id']]}
    return None


def _scale_cap(rev, backlog):
    i = next(k for k, (lim, _) in enumerate(SCALE_CAPS) if rev < lim)
    lifted = backlog is not None and backlog >= rev and i > 0
    return SCALE_CAPS[i - 1 if lifted else i][1], lifted


def _cycle_stats(h):
    """Mid-cycle revenue: window mean placed at the window's mid date, a log-linear trend slope bounded to a long-run
    rate, and the historical peak / trough relative to that trend."""
    ends, rev = h['ends'][-10:], h['revenue'][-10:]
    x = [years(ends[0], e) for e in ends]
    y = [math.log(r) for r in rev]
    xm, ym = sum(x) / len(x), sum(y) / len(y)
    sxx = sum((a - xm) ** 2 for a in x)
    slope = math.exp(sum((a - xm) * (b - ym) for a, b in zip(x, y)) / sxx) - 1 if sxx > 0 else 0.
    g = _clamp(slope, *CYCLE_TREND)
    mid = (_d(ends[0]) + timedelta(days=round(xm * 365.25))).isoformat()
    mean = sum(rev) / len(rev)
    ratio = [r / (mean * (1 + g) ** years(mid, e)) for e, r in zip(ends, rev)]
    return {'mean_revenue': mean, 'mid_date': mid, 'slope': slope, 'trend_growth': g, 'years': len(rev),
            'peak_ratio': max(ratio), 'trough_ratio': min(ratio), 'window': [ends[0], ends[-1]]}


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
    h['ytd'] = _ytd(fb, 'revenue', ends[-1])
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
    g_recent, yrs, recent_how = None, years(ends[-1], t_end) if t_end else 0, None
    ytd = h['ytd']
    if ytd and ytd['prior'] and ytd['prior'] > 0 and years(ends[-1], ytd['end']) >= .2:
        g_recent, recent_how = ytd['value'] / ytd['prior'] - 1, 'year-to-date revenue vs the same months a year earlier'
    elif t_method in ('quarters', 'annual_plus_ytd') and yrs >= .45 and t_rev and rev[-1] > 0:
        g_recent, recent_how = (t_rev / rev[-1]) ** (1 / yrs) - 1, 'annualized TTM-vs-last-FY growth'
    h['g_recent'] = g_recent
    tiny = len(rev) > 3 and rev[-4] < .1 * rev[-1]       # a 3-year CAGR from a tiny base says nothing about the next year
    trail = _mean([g_last, None if tiny else c3])
    if trail is None:
        trail = 0.
        warn('growth_history_missing', 'fewer than two fiscal years of revenue; growth anchor set to 0% (model)', 'high')
    raw = trail if g_recent is None else .5 * g_recent + .5 * trail
    h['g_trailing_formula'] = (('mean(revenue_growth_last, revenue_cagr_3y)' if not tiny else 'revenue_growth_last (3-year base below 10% of '
                                'today\'s revenue)') if g_recent is None else
                               f'0.5 x {recent_how} + 0.5 x mean(revenue_growth_last, revenue_cagr_3y)')
    scale = t_rev if t_rev else rev[-1]
    bl = facts.series(fb, 'rpo', 'instant')
    h['backlog'] = bl[-1][1] if bl else None
    cap, lifted = _scale_cap(scale, h['backlog'])
    h['g_scale_cap'] = {'cap': cap, 'revenue': scale, 'backlog': h['backlog'], 'backlog_lifted': lifted, 'raw_anchor': raw}
    if raw > cap:
        warn('growth_scale_capped', f'trailing growth {_pct(raw)} is above the base-rate ceiling {_pct(cap)} for revenue of {scale:,.0f}'
                                    + (' (lifted one band by a backlog of ' + f'{h["backlog"]:,.0f})' if lifted else '') +
                                    '; anchor capped (guidance or consensus replaces it when present)', 'medium')
        h['g_trailing_formula'] += f', capped at the revenue-scale base rate {_pct(cap)}'
    h['g_trailing'] = min(raw, cap)
    h['g_cycle'] = c5 if c5 is not None else (c3 if c3 is not None else trail)
    vol = _metric(an, 'growth_volatility')
    if vol is None and len(growth) >= 3:
        mu = sum(growth) / len(growth)
        vol = (sum((x - mu) ** 2 for x in growth) / (len(growth) - 1)) ** .5
    h['growth_volatility'] = vol
    last = rows[-1][1]
    # SBC share of revenue over ONE period: TTM SBC with TTM revenue, else the SBC TTM with revenue over the same twelve
    # months, else the last fiscal year (never fiscal-year SBC over TTM revenue)
    h['sbc_pct'], h['sbc_basis'] = None, None
    if use_ttm and h['ttm']['sbc'] is not None:
        h['sbc_pct'], h['sbc_basis'] = h['ttm']['sbc'] / t_rev, f'TTM SBC / TTM revenue to {t_end}'
    else:
        ts = facts.ttm(fb, 'sbc')
        if ts and ts.get('method') != 'latest_fiscal_year' and _d(ts['period_end']) > _d(ends[-1]):
            ry = _ytd(fb, 'revenue', ends[-1], upto=ts['period_end'])
            if ry and ry['prior'] is not None and rev[-1] + ry['value'] - ry['prior'] > 0:
                h['sbc_pct'] = ts['value'] / (rev[-1] + ry['value'] - ry['prior'])
                h['sbc_basis'] = f"TTM SBC / revenue over the same twelve months to {ts['period_end']}"
        if h['sbc_pct'] is None and g(last, 'sbc') is not None:
            h['sbc_pct'], h['sbc_basis'] = g(last, 'sbc') / rev[-1], f'SBC / revenue, fiscal year ending {ends[-1]}'
    if h['sbc_pct'] is None:
        h['sbc_pct'], h['sbc_basis'] = _metric(an, 'sbc_pct_revenue'), 'analysis sbc_pct_revenue'
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
    h['inc_roic'] = _metric(an, 'incremental_roic_3y')
    h['nd_ebitda'] = _metric(an, 'net_debt_to_ebitda')
    h['cycle'] = _cycle_stats(h) if len(rev) >= 3 else None
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
    """Fiscal-year boundaries after as_of, plus the fifth anniversary of as_of as its own valuation date (it splits the
    fiscal year containing it unless it falls on a boundary); the horizon always reaches one full fiscal year past it."""
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
    fifth = _fy(as_of.year + 5, as_of.month, as_of.day)
    j = next(i for i in range(60) if _fy(y + i, month, day) >= fifth)
    need = j if _fy(y + j, month, day) == fifth else j + 1
    extended = n_full < need
    n_full = max(n_full, need)
    b = [_fy(y + i, month, day) for i in range(n_full + 1)]
    interim = [] if fifth in b else [fifth.isoformat()]
    report = [fb['as_of']] + sorted([x.isoformat() for x in b[:j + 1]] + interim)
    return {'boundaries': [x.isoformat() for x in b], 'periods': sorted([x.isoformat() for x in b] + interim), 'interim': interim,
            'fifth_anniversary': fifth.isoformat(), 'n_full': n_full, 'extended_to_fifth': extended,
            'report_dates': report, 'stub_years': years(fb['as_of'], b[0].isoformat()),
            'fiscal_years_since_last_report': max(1, round(years(h['last_end'], b[0].isoformat()))),
            'fiscal_year_end': f'{month:02d}-{day:02d}',
            'report_rule': 'as_of, the fifth anniversary of as_of (an explicit valuation date: it splits its fiscal year, which '
                           'keeps one annual revenue rate) and every fiscal-year boundary through the first one on or after it '
                           '(nominal fiscal year-ends, so a 52/53-week calendar can differ by a few days)'}


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


def _cost_of_equity(route, rf, erp, inputs_notes, external):
    """Unlevered (business-risk) cost of equity Ku from the documented table; leverage is added by _relever."""
    st = STAGE[route['stage']]
    adj = [{'factor': 'stage:' + route['stage'], 'value': st['ke'], 'reason': f"business-risk add-on for the {route['stage']} stage"}]
    cond = {'cyclical': route['cyclical'], 'capital_intensity_high': route['capital_intensity'] == 'high',
            'needs_external_funding': bool(external)}
    for key, v, why in KE_FACTORS:
        if cond[key]:
            adj.append({'factor': key, 'value': v, 'reason': why})
    for d in WEAK_DIMENSIONS:   # at most 4 x 0.25pp = 1pp
        if (route['dimensions'].get(d) or {}).get('assessment') == 'weak':
            adj.append({'factor': 'weak_' + d, 'value': .0025, 'reason': f'model card rates {d} weak'})
    ku = _clamp(rf + erp + sum(a['value'] for a in adj), rf + .02, .20)
    return ku, {'risk_free': rf, 'equity_risk_premium': erp, 'adjustments': adj, 'notes': inputs_notes,
                'formula': 'Ku = risk-free + equity risk premium + business-risk adjustments (stage, cyclicality, capital intensity, '
                           'funding need of the executed base plan, weak card dimensions); a documented table, NOT price beta. '
                           'Ke = Ku + (Ku - Kd) x D/E and terminal WACC = Ku - w x Kd x tax (Harris-Pringle) with D/E and w from '
                           "the model's own base equity value (fixed point), never the quote; same Ke in every scenario so "
                           'scenarios differ by business outcomes only'}


def _ke_parts(parts, ku, lev):
    """Final components: the table rows plus the leverage relevering as its own row (Ke = sum of the rows)."""
    adj = list(parts['adjustments'])
    if lev['ke'] - ku > 1e-12:
        adj.append({'factor': 'leverage_relevered', 'value': lev['ke'] - ku,
                    'reason': f"financial leverage: Ku + (Ku - Kd) x D/E with D/E {lev['d_to_e']:.1%} from the model's own equity value"})
    flat = {'adjustment_' + a['factor'].replace(':', '_'): a['value'] for a in adj}   # numeric keys for risk.py's priced-via lookup
    return {**{k: v for k, v in parts.items() if k != 'adjustments'}, **flat, 'adjustments': adj, 'unlevered': ku, 'leverage': lev}


def _relever(spec, P, ku, kd, net_debt, ov_ke):
    """Debt weight from the base scenario's own equity value today (fixed point), so value does not depend on the quote."""
    shares = spec['opening']['shares']
    w, it, status, e = 0., 0, 'no_net_debt' if net_debt <= 0 else 'converged', None

    def apply(w):
        de = w / (1 - w)
        ke = ku + max(0., ku - kd) * de
        for sc in spec['scenarios']:
            s = sc['name']
            k_e = ov_ke.get(s, ke)
            tx = P[s]['tax']
            wacc = (ku - w * kd * tx) if s not in ov_ke else (1 - w) * k_e + w * kd * (1 - tx)
            P[s].update(ke=k_e, wacc=max(wacc, P[s]['g_terminal'] + .03))
            sc['cost_of_equity'] = _r(k_e)
            sc['terminal']['wacc'] = _r(P[s]['wacc'])
        return ke, de
    ke, de = apply(0.)
    if net_debt > 0:
        for it in range(1, 41):
            v = _value(spec)
            if v is None or v <= 0:
                status = 'equity_not_positive'
                w = W_MAX
                ke, de = apply(w)
                break
            e = v * shares
            w2 = min(W_MAX, net_debt / (net_debt + e))
            if abs(w2 - w) < 1e-7:
                w = w2
                break
            w = w2
            ke, de = apply(w)
        else:
            status = 'not_converged'
        if w >= W_MAX - 1e-12 and status == 'converged':
            status = 'capped'
    return {'ke': ke, 'unlevered': ku, 'kd': kd, 'net_debt': net_debt, 'model_equity': e, 'debt_weight': w, 'd_to_e': de,
            'iterations': it, 'status': status,
            'basis': 'net debt = debt - marketable investments - cash above the first-period minimum; equity = base value today x '
                     'point-in-time shares (the model\'s own value, not the quote)'}


def _margin_targets(route, h, warn):
    st, (_, typ, ceil, _) = STAGE[route['stage']], route['type']
    m0, best, sp = h['m0'], h['m_best3'], STAGE[route['stage']]['m_spread']
    if route['method'] == 'normalized_cycle_fcff':
        hist = h['m_all']
        if len(hist) < 5:
            warn('cycle_not_observed', f'only {len(hist)} fiscal years of margins: mid-cycle margin may not span a full cycle', 'high')
        mid, trough, peak = sum(hist) / len(hist), min(hist), max(hist)
        t = {'base': mid, 'bear': mid - max(sp, .5 * (mid - trough)), 'bull': min(ceil, mid + max(sp, .5 * (peak - mid)))}
        why = (f'mid-cycle = mean operating margin over {len(hist)} fiscal years {_pct(mid)}; around the cycle, not at its extremes: '
               f'bear = mid - max(stage spread, half the distance to the trough {_pct(trough)}), bull = mid + max(stage spread, half the '
               f'distance to the peak {_pct(peak)}), capped at the type ceiling')
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


def _current_year(route, h, cal, cons):
    """Current fiscal year revenue from the best dated evidence: consensus, company guidance, reported year to date plus the
    remaining months at the run rate; None when only the trailing anchor is available."""
    k, b1 = cal['fiscal_years_since_last_report'], cal['boundaries'][0]
    gd = h.get('guidance', {}).get(f'FY{b1[:4]}')
    ytd = h['ytd'] if h['ytd'] and k == 1 and _d(h['ytd']['end']) < _d(b1) else None
    if b1 in cons:
        r, kind = cons[b1]['value'], 'analyst'
        basis = f"consensus revenue for the fiscal year ending {b1} ({cons[b1]['source_id']}, {cons[b1].get('as_of')})"
    elif gd:
        r, kind = gd['value'], 'guidance'
        basis = (f"company guidance midpoint for the fiscal year ending {b1} ({gd['low']:,.0f}-{gd['high']:,.0f}, issued "
                 f"{gd.get('issued_at')}; management guidance, not a reported fact)")
    elif ytd:
        rem = years(ytd['end'], b1)
        if ytd['prior'] and 0 < ytd['prior'] < h['last_revenue']:
            left, how = (h['last_revenue'] - ytd['prior']) * ytd['value'] / ytd['prior'], 'the same months a year earlier x year-to-date growth'
        else:
            left, how = ytd['value'] / years(h['last_end'], ytd['end']) * rem, 'the annualized year-to-date run rate'
        r, kind = ytd['value'] + left, 'model'      # reported year to date plus a modelled remainder
        basis = (f"reported year to date {ytd['value']:,.0f} ({ytd['basis']}) + the remaining {rem:.2f} years to {b1} at {how} "
                 f"({left:,.0f})")
    else:
        return None
    out = {'r_fy': r, 'g_cur': (r / h['last_revenue']) ** (1 / k) - 1, 'basis': basis, 'kind': kind, 'ytd': ytd}
    if ytd and r - ytd['value'] > 0 and years(ytd['end'], b1) > .02:
        out['stub_rate'] = (r - ytd['value']) / years(ytd['end'], b1)
        out['stub_basis'] = f"(current-year revenue - reported year to date) / years from {ytd['end']} to {b1}"
    return out


def _params(route, h, cal, cons, rf, ku, debt0, ov, warn):
    st = STAGE[route['stage']]
    s2c_range = route['type'][0]
    cyc = route['method'] == 'normalized_cycle_fcff' and h.get('cycle')
    if route['stage'] == 'decline':
        gT_base = _clamp(.5 * h['g_trailing'], DECLINE_FLOOR, 0.)
        gT = {'base': gT_base, 'bear': max(DECLINE_FLOOR, gT_base - .01), 'bull': min(gT_base + .02, rf)}
    else:
        gT_base = min(rf, TERMINAL_GROWTH_CAP)
        gT = {'base': gT_base, 'bull': gT_base, 'bear': max(0., gT_base - .005)}
    k = cal['fiscal_years_since_last_report']
    b1 = cal['boundaries'][0]
    n_full = len(cal['boundaries']) - 1
    for s in SCEN:
        gT[s] = ov(s, 'terminal_growth', gT[s])
    hl = ov('base', 'growth_half_life', st['half_life'])
    cur = _current_year(route, h, cal, cons)
    level, e0 = None, 0.
    if cyc:
        trend_b1 = cyc['mean_revenue'] * (1 + cyc['trend_growth']) ** years(cyc['mid_date'], b1)
    if cur:
        r_fy, g_cur, cur_basis, cur_kind = cur['r_fy'], cur['g_cur'], cur['basis'], cur['kind']
        level = r_fy
    elif cyc:
        newer = h['ttm']['period_end'] and _d(h['ttm']['period_end']) > _d(h['last_end']) and h['ttm']['revenue']
        level, l_end = (h['ttm']['revenue'], h['ttm']['period_end']) if newer else (h['last_revenue'], h['last_end'])
        e0 = years(l_end, b1)
        w0 = min(1., e0 / CYCLE_CONV['base'])
        r_fy = level + (trend_b1 - level) * w0
        g_cur, cur_kind = (r_fy / h['last_revenue']) ** (1 / k) - 1, 'model'
        cur_basis = (f"cyclical: today's level {level:,.0f} (to {l_end}) moving {w0:.0%} of the way to the mid-cycle trend {trend_b1:,.0f} "
                     f"by {b1} (reversion over {CYCLE_CONV['base']} years; never the peak grown at a trough-to-peak growth rate)")
    else:
        g_cur, cur_kind = _clamp(h['g_trailing'], -.5, 2.), 'model'
        newer = h['ttm']['method'] in ('quarters', 'annual_plus_ytd') and h['ttm']['period_end'] and _d(h['ttm']['period_end']) > _d(h['last_end'])
        if newer:
            r_fy = h['ttm']['revenue'] * (1 + g_cur) ** years(h['ttm']['period_end'], b1)
            cur_basis = f"TTM revenue to {h['ttm']['period_end']} grown at the trailing anchor to {b1}"
        else:
            r_fy = h['last_revenue'] * (1 + g_cur) ** years(h['last_end'], b1)
            cur_basis = f"last reported fiscal-year revenue ({h['last_end']}) grown at the trailing anchor to {b1}"
    if cur and cur.get('stub_rate'):
        stub_rate, stub_basis = cur['stub_rate'], cur['stub_basis']
    else:
        stub_rate = r_fy * (1 + g_cur) ** (.5 - cal['stub_years'] / 2)
        stub_basis = 'current-year revenue x (1+g)^(0.5 - stub/2) (late-year run rate)'
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
    spread = min(.15, max(st['g_spread'], h['growth_volatility'] or 0, half or 0))
    cyc_meta, weights = None, {}
    if cyc:
        g_long = {'base': ov('base', 'growth_anchor', cyc['trend_growth'])}
        g_long['bear'] = ov('bear', 'growth_anchor', g_long['base'] - .01)
        g_long['bull'] = ov('bull', 'growth_anchor', g_long['base'] + .01)
        factor = {'base': 1., 'bear': min(1., cyc['trough_ratio']) ** .5, 'bull': max(1., cyc['peak_ratio']) ** .5}
        rates = {}
        for s in SCEN:
            hs = hl * {'base': 1., 'bear': .8, 'bull': 1.3}[s]
            gt, trend, r = _fade(g_long[s], gT[s], hs, n_full), trend_b1, [r_fy]
            weights[s] = [min(1., e0 / CYCLE_CONV['base'])]
            for t in range(1, n_full + 1):
                trend *= 1 + gt[t - 1]
                w = min(1., (e0 + t) / CYCLE_CONV[s])
                weights[s].append(w)
                r.append(level * (1 - w) + factor[s] * trend * w)
            rates[s] = r
        rates['bear'] = [min(x, y * (1 - 1e-3)) if i else x for i, (x, y) in enumerate(zip(rates['bear'], rates['base']))]
        rates['bull'] = [max(x, y * (1 + 1e-3)) if i else x for i, (x, y) in enumerate(zip(rates['bull'], rates['base']))]
        paths = {s: [b / a - 1 for a, b in zip(rates[s], rates[s][1:])] for s in SCEN}
        g_kind = 'model'
        cyc_meta = dict(cyc, trend_at_current_fy=trend_b1, level=level, elapsed_at_current_fy=e0, long_run_growth=g_long,
                        level_factor=factor, conv=CYCLE_CONV)
    else:
        anchor = ov('base', 'growth_anchor', cg[0] if cg else _fade(g_cur, gT['base'], hl, 1)[0])
        if cg and anchor == cg[0]:
            base, g_kind = cg + _fade(cg[-1], gT['base'], hl, n_full - len(cg)), 'analyst'
        else:
            base, g_kind = [anchor] + _fade(anchor, gT['base'], hl, n_full - 1), 'model'
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
    tax_eff = h['tax_effective']
    tax = _clamp(tax_eff, .15, .25) if tax_eff is not None else .21
    if tax_eff is not None and tax != tax_eff:
        warn('tax_normalized', f'effective tax {tax_eff:.1%} outside 15-25%; normalized to {tax:.1%} (override with evidence)', 'low')
    r0 = h['interest_expense'] / debt0 if h['interest_expense'] and debt0 > 0 else None
    r0 = _clamp(r0, .01, .15) if r0 is not None else rf + st['debt_spread']
    out = {}
    for s in SCEN:
        k_u = ov(s, 'cost_of_equity', ku)
        tx = ov(s, 'tax_rate', tax)
        w0 = max(k_u, gT[s] + .03)    # unlevered cost of capital: the hurdle excess returns fade toward (leverage-free)
        if route['moat'] == 'strong':
            roic = min(max(h['roic'] or 0, w0 + .05), .40)
            roic = {'base': roic, 'bear': max(w0 + .02, .7 * roic), 'bull': min(.45, 1.2 * roic)}[s]
        else:
            roic = {'base': w0 + .02, 'bear': w0 + .01, 'bull': w0 + .04}[s]
        roic = ov(s, 'terminal_roic', roic)
        if roic < gT[s]:
            raise ValueError(f'{s}: terminal ROIC below terminal growth')
        mm = ov(s, 'mature_margin', margins[s])
        out[s] = {'growth': [_clamp(x, -.5, 2.) for x in paths[s]], 'g_terminal': gT[s], 'mature_margin': mm,
                  's2c': s2c_s[s], 's2c_terminal': roic / max(mm * (1 - tx), 1e-6), 'ke': k_u, 'tax': tx, 'wacc': w0, 'hurdle': w0,
                  'roic': roic, 'cycle_weights': weights.get(s),
                  'conv': CYCLE_CONV[s] if cyc else (max(2, round(.75 * st['conv'])) if s == 'bear' else st['conv'])}
    meta = {'r_fy': r_fy, 'g_cur': g_cur, 'cur_basis': cur_basis, 'cur_kind': cur_kind, 'ytd': cur['ytd'] if cur else None,
            'stub_rate': stub_rate, 'stub_basis': stub_basis, 'consensus_growth': cg,
            'consensus_low': lows, 'consensus_high': highs, 'growth_kind': g_kind, 'spread': spread, 'half_life': hl,
            'margin_why': m_why, 's2c_history': s2c_h, 's2c_used': s2c, 's2c_range': s2c_range, 'tax_effective': tax_eff,
            'tax_used': tax, 'interest_existing': r0, 'g_terminal': gT, 'cycle': cyc_meta}
    return out, meta


def _paths(P, meta, h, route, cal, open_bal, inv_income):
    """Fiscal-year paths. Depreciation converges to maintenance capex and the capital needed per unit of new revenue converges
    to the terminal g / ROIC rule by the last explicit year, so the last explicit year and the terminal value describe the same
    steady state. Growth capex closes the reinvestment identity per period on the bases drivers.build_case uses:
    growth capex + change in working capital - change in deferred revenue = change in revenue x capital per unit of revenue."""
    st = STAGE[route['stage']]
    B = cal['boundaries']
    n = len(B)
    m0 = h['m0'] if h['m0'] is not None else P['base']['mature_margin']
    sbc0, dep0, cap = h['sbc_pct'], h['da_pct'], h['capex_pct']
    maint = min(cap, dep0)
    wc = _clamp(h['wc_ratio'] if h['wc_ratio'] is not None else .05, -.3, .5)
    dr = _clamp(open_bal['deferred_revenue'] / meta['stub_rate'], 0, 1.5) if meta['stub_rate'] else 0.
    prepaid = min(1., 2 * dr) if dr > 0 else 0.
    dur = [cal['stub_years']] + [years(a, b) for a, b in zip(B, B[1:])]
    conv_w = [t / (n - 1) if n > 1 else 1. for t in range(n)]          # 0 today -> 1 in the last explicit year
    dep = [dep0 + (maint - dep0) * w for w in conv_w]
    out, ident = {}, {}
    for s in SCEN:
        p = P[s]
        g = p['growth']
        rates = [meta['stub_rate']]
        prev = meta['r_fy']
        for x in g:
            prev *= 1 + x
            rates.append(prev)
        mt, conv = p['mature_margin'], p['conv']
        if p.get('cycle_weights'):
            margin = [m0 + (mt - m0) * w for w in p['cycle_weights']]
        else:
            margin = [m0] + [mt + (m0 - mt) * max(0., 1 - t / conv) for t in range(1, n)]
        sbc = [sbc0] + [sbc0 * (1 - st['sbc_fade'] * min(1., t / st['conv'])) for t in range(1, n)]
        ccr = [1 - m - x - d for m, x, d in zip(margin, sbc, dep)]
        if any(c < 0 or c > 2 for c in ccr):
            raise ValueError('cash-cost ratio outside driver bounds (operating losses above 100% of revenue or margins above 100%)')
        capint = [1 / p['s2c'] + (1 / p['s2c_terminal'] - 1 / p['s2c']) * w for w in conv_w]
        fy_rate = [meta['r_fy']] + rates[1:]
        gc, need = [], []
        for t in range(n):
            if t == 0:
                d_rev = meta['r_fy'] - meta['r_fy'] / (1 + meta['g_cur'])
                nr, dwc, ddef = d_rev * capint[0] * dur[0], 0., 0.
            else:
                d_rev = fy_rate[t] - fy_rate[t - 1]
                nr, dwc = d_rev * capint[t], wc * d_rev
                ddef = dr * (rates[t] - (meta['stub_rate'] if t == 1 else rates[t - 1]))
            need.append(nr)
            gc.append(max(0., nr - dwc + ddef) / (rates[t] * dur[t]))
        months = P[s].get('cash_months', st['months'])
        min_cash = [max(0., min(months / 12 * c * r, open_bal['cash'])) for c, r in zip(ccr, rates)]
        out[s] = {'growth': [_r(x) for x in g], 'rates': rates, 'operating_margin': [_r(x) for x in margin],
                  'sbc_pct': [_r(x) for x in sbc], 'depreciation_pct': [_r(x) for x in dep], 'cash_cost_ratio': [_r(x) for x in ccr],
                  'maintenance_capex_pct': [_r(maint)] * n, 'growth_capex_pct': [_r(x) for x in gc],
                  'tax_rate': [_r(p['tax'])] * n, 'interest_rate': [_r(meta['interest_existing'])] * n,
                  'deferred_revenue_ratio': [_r(dr)] * n, 'working_capital_ratio': [_r(wc)] * n, 'prepaid_share': [_r(prepaid)] * n,
                  'nol_usage_limit': [.8] * n, 'payout_fraction': [0.] * n,
                  'amortization': [0.] * n, 'minimum_cash': [float(round(x)) for x in min_cash],
                  'investment_income_after_tax': [float(round(inv_income * (1 - p['tax'])))] * n,
                  'debt_draw': [0.] * n, 'debt_repayment': [0.] * n, 'fixed_cash_costs': [0.] * n}
        ident[s] = {'net_reinvestment_target': need, 'capital_per_revenue': capint}
    return out, {'wc': wc, 'dr': dr, 'prepaid': prepaid, 'maint': maint, 'dep': dep0, 'dep_path': dep, 'm0': m0, 'sbc0': sbc0,
                 'identity': ident}


# ---------------------------------------------------------------- execution helpers

def _run(spec):
    try:
        return value_company(build_case(spec)), None
    except ValueError as e:
        return None, str(e)


def _scen(spec, name):
    return next(s for s in spec['scenarios'] if s['name'] == name)


def _fund(spec, policy, allow, r_new, r0, debt0, locked, inv0):
    """Explicit funding, never silent, cheapest source first, per period with a cash shortfall:

    1. marketable investments are liquidated (their after-tax income falls with the balance);
    2. the period's cash-equivalent SBC charge is settled in new shares (dilution at the quote, never debt);
    3. ASSUMED new debt, only up to DEBT_CAPACITY x EBITDA before SBC (none while EBITDA is not positive);
    4. an ASSUMED equity raise at the quote (EQUITY_FEE fees) for the rest, so dilution is priced into per-share values.
    Distributions start once free cash flow (before new external money and SBC settlement) stays positive; a later
    shortfall pauses the prior distribution instead. A scenario that keeps a gap, or still needs external money in its
    last explicit year (the terminal value would assume a self-funding business the explicit path never reaches), is
    reverted to unfunded: value_company then withholds its values."""
    n = len(spec['boundaries'])
    lock = lambda nm, k: k in locked.get(nm, ())
    info = {sc['name']: {'draws': [0.] * n, 'equity': [0.] * n, 'liquidation': [0.] * n, 'settled': [0.] * n, 'retained': []}
            for sc in spec['scenarios']}
    inc0 = {sc['name']: list(sc['investment_income_after_tax']) for sc in spec['scenarios']}
    for sc in spec['scenarios']:
        if not lock(sc['name'], 'payout_fraction'):
            sc['payout_fraction'] = [0.] * n
        sc.setdefault('equity_issue_price', spec['quote']['price'])
        sc.setdefault('equity_fee_rate', EQUITY_FEE)

    def loop(step):
        for _ in range(4 * n + 20):
            res, err = _run(spec)
            if err:
                return err
            changed = False
            for sc, rs in zip(spec['scenarios'], res['scenarios']):
                j = next((i for i, row in enumerate(rs['forecast']) if row['funding_gap'] > 1e-6), None)
                if j is not None and step(sc, sc['name'], j, rs['forecast'][j]['funding_gap'], rs['forecast']):
                    changed = True
            if not changed:
                return None
        return None

    def fund(sc, nm, j, gap, rows):
        I, rem, changed = info[nm], gap * 1.0001 + 1, False
        left = inv0 - sum(I['liquidation'])
        if left > 1 and not lock(nm, 'investment_liquidation'):
            x = min(left, rem)
            I['liquidation'][j] += x
            rem, changed = rem - x, True
            bal, inc = inv0, []
            for i in range(n):
                bal -= I['liquidation'][i]
                inc.append(float(round(inc0[nm][i] * max(0., bal) / inv0)))
            sc['investment_liquidation'] = list(I['liquidation'])
            if not lock(nm, 'investment_income_after_tax'):
                sc['investment_income_after_tax'] = inc
        room = (sc['sbc_pct'][j] * rows[j]['revenue'] - I['settled'][j]) * (1 - 1e-9)
        if rem > 0 and room > 1 and not lock(nm, 'sbc_share_settlement'):
            x = min(room, rem)
            I['settled'][j] += x
            rem, changed = rem - x, True
            sc['sbc_share_settlement'] = list(I['settled'])
        if rem <= 0 or not allow:
            return changed
        dur = years(rows[j]['start'], rows[j]['end'])
        head = DEBT_CAPACITY * max(0., rows[j]['ebitda_before_sbc'] / dur) - rows[j]['debt']
        if head > 1 and not lock(nm, 'debt_draw'):
            x = float(math.ceil(min(head, rem * 1.1)))
            I['draws'][j] += x
            rem, changed = rem - x, True
            cum, rates = 0., []
            for d in I['draws']:
                cum += d
                rates.append(_r((debt0 * r0 + cum * r_new) / (debt0 + cum)) if debt0 + cum > 0 else _r(r0))
            sc['debt_draw'], sc['financing_status'] = list(I['draws']), 'assumed'
            if not lock(nm, 'interest_rate'):
                sc['interest_rate'] = rates
        if rem > 0 and not lock(nm, 'equity_proceeds'):
            I['equity'][j] += float(math.ceil(rem * 1.1 / (1 - sc['equity_fee_rate'])))
            sc['equity_proceeds'], sc['financing_status'], changed = list(I['equity']), 'assumed', True
        return changed

    def retain(sc, nm, j, gap, rows):
        if j == 0 or sc['payout_fraction'][j - 1] <= 0 or lock(nm, 'payout_fraction'):
            return False
        sc['payout_fraction'][j - 1] = 0.
        info[nm]['retained'].append(spec['boundaries'][j - 1])
        return True

    err = loop(fund)
    res, err = (None, err) if err else _run(spec)
    if err:
        return {'error': err}
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        I = info[nm]
        f = [row['fcfe_before_new_equity'] - d - x for row, d, x in zip(rs['forecast'], I['draws'], I['settled'])]
        k = next(i for i in range(n + 1) if all(x > 0 for x in f[i:]))
        I['payout_starts'] = spec['boundaries'][k] if k < n else None
        if not lock(nm, 'payout_fraction'):
            sc['payout_fraction'] = [0.] * k + [policy[nm]] * (n - k)
    err = loop(retain)
    res, err = (None, err) if err else _run(spec)
    if err:
        return {'error': err}
    reverted = False
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        I = info[nm]
        gap = any(row['funding_gap'] > 1e-6 for row in rs['forecast'])
        ext = sum(I['draws']) + sum(I['equity'])
        if ext and (gap or I['draws'][-1] > 0 or I['equity'][-1] > 0):
            I['reverted'] = (f'assumed external financing of {ext:,.0f} did not converge (a shortfall remains or the plan still needs new '
                             'money in its last explicit year, so the terminal value would assume a self-funding business the explicit '
                             'path never reaches); values are withheld instead of assuming free capital')
            sc['debt_draw'], sc['equity_proceeds'], sc['financing_status'] = [0.] * n, [0.] * n, 'none'
            if not lock(nm, 'interest_rate'):
                sc['interest_rate'] = [_r(r0)] * n
            if not lock(nm, 'payout_fraction'):
                sc['payout_fraction'] = [0.] * n
            I.update(draws=[0.] * n, equity=[0.] * n, retained=[], payout_starts=None)
            reverted = True
    if reverted:
        res, err = _run(spec)
        if err:
            return {'error': err}
    for sc, rs in zip(spec['scenarios'], res['scenarios']):
        nm = sc['name']
        I = info[nm]
        gap = any(row['funding_gap'] > 1e-6 for row in rs['forecast'])
        debt, eq = sum(I['draws']), sum(I['equity'])
        B = spec['boundaries']
        I.update(total_assumed=debt + eq, debt_assumed=debt, equity_raised=eq, investments_liquidated=sum(I['liquidation']),
                 sbc_settled_in_shares=sum(I['settled']),
                 new_financing_shares=sum(r['new_financing_shares'] for r in rs['forecast']),
                 sbc_settlement_shares=sum(r.get('sbc_settlement_shares', 0) for r in rs['forecast']),
                 status='unfunded' if gap else ('assumed_external_financing' if debt + eq else 'self_funded'),
                 draws=[{'period_end': b, 'amount': x} for b, x in zip(B, I['draws']) if x],
                 equity=[{'period_end': b, 'amount': x} for b, x in zip(B, I['equity']) if x],
                 liquidation=[{'period_end': b, 'amount': x} for b, x in zip(B, I['liquidation']) if x],
                 settled=[{'period_end': b, 'amount': x} for b, x in zip(B, I['settled']) if x],
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
        if s['kind'] == 'issuer_document' or (s['kind'] == 'document' and s.get('dataset') in ISSUER_DATASETS):
            kind = ISSUER_DATASETS.get(s.get('dataset'), 'document')
            retrieval = s.get('retrieval') if s.get('retrieval') in RETRIEVALS else 'search_excerpt'
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
    'method', 'assumptions', 'anchors', 'paths', 'cost_of_equity', 'funding', 'overrides', 'preview', 'warnings', 'notes',
    'reason'}. Unknown top-level input keys are ignored with a warning and a note (never silently); unknown keys inside a
    known input are rejected. The funding add-on to the cost of equity comes from the executed base funding result: when
    it disagrees with the card's funding label the forecast is rebuilt once with the executed answer."""
    if inputs is not None and not isinstance(inputs, dict):
        raise ValueError('forecast inputs must be an object')
    inputs = deepcopy(inputs or {})
    ignored = sorted(set(inputs) - set(KNOWN_INPUTS))
    if inputs.get('financing', 'assumed') not in FINANCING_MODES:
        raise ValueError(f"financing must be one of {', '.join(FINANCING_MODES)}")
    if inputs.get('horizon_years') is not None and (isinstance(inputs['horizon_years'], bool) or not isinstance(inputs['horizon_years'], int)
                                                    or not 1 <= inputs['horizon_years'] <= 30):
        raise ValueError('horizon_years must be an integer from 1 to 30')
    clean = {k: v for k, v in inputs.items() if k in KNOWN_INPUTS}
    out = _build(fb, an, card, qa, clean, None)
    if out.get('status') == 'built' and out['_external'] != out['_external_assumed']:
        again = _build(fb, an, card, qa, clean, out['_external'])
        if again.get('status') == 'built' and again['_external'] != again['_external_assumed']:
            again['warnings'].append({'code': 'funding_addon_unstable', 'severity': 'medium',
                                      'message': 'the external-funding add-on flips the funding result; the second build is kept'})
        out = again
    out['notes'] = [f'input {k!r} is not a forecast input ({", ".join(KNOWN_INPUTS)}): ignored, nothing applied' for k in ignored]
    for k in ignored:
        out['warnings'].append({'code': 'input_ignored', 'severity': 'medium', 'message': out['notes'][ignored.index(k)]})
    out.pop('_external', None)
    out.pop('_external_assumed', None)
    return out


def _build(fb, an, card, qa, inputs, external):
    an, card, qa = an or {}, card or {}, qa or {}
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
    if h is None:
        return empty('insufficient_data', 'no fiscal year with positive revenue available at as_of', method)
    h['guidance'] = _revenue_guidance(fb)
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
    external_assumed = external if external is not None else (route['funding'] == 'needs_external' or route['method'] == 'scenario_survival_fcff')

    # ---- calendar and horizon
    cyc_h = route['method'] == 'normalized_cycle_fcff' and h.get('cycle')
    g_probe = h['cycle']['trend_growth'] if cyc_h else h['g_trailing']
    n_full = int(inputs.get('horizon_years') or next(n for lim, n in HORIZON if g_probe > lim))
    cal = _calendar(fb, h, n_full)
    cons, dropped = _consensus(fb, cal['boundaries'], h['last_end'])
    if dropped:
        warn('lookahead_estimates_ignored', f'{dropped} estimate rows dated after as_of were ignored', 'medium')
    if cons and not inputs.get('horizon_years') and not cyc_h:
        b2 = cal['boundaries'][1]
        g_probe = cons[b2]['value'] / cons.get(cal['boundaries'][0], {'value': h['last_revenue']})['value'] - 1 if b2 in cons else g_probe
        n2 = next(n for lim, n in HORIZON if g_probe > lim)
        if n2 != n_full:
            cal = _calendar(fb, h, n2)
            cons, _ = _consensus(fb, cal['boundaries'], h['last_end'])
    if cal['extended_to_fifth']:
        warn('horizon_extended_to_fifth_year', f"explicit horizon extended to {cal['n_full']} fiscal years so the forecast covers the fifth "
                                               f"anniversary of as_of ({cal['fifth_anniversary']}) and one full fiscal year after it", 'low')
    n_full = cal['n_full']
    st = STAGE[route['stage']]
    ku, ke_parts = _cost_of_equity(route, rf, erp, [rf_note, erp_note], external_assumed)

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
        P, meta = _params(route, h, cal, cons, rf, ku, open_bal['debt'], ov, warn)
        for s in SCEN:
            P[s]['cash_months'] = ov(s, 'minimum_cash_months', st['months'])
        inv_income = (open_bal['sti'] + open_bal['lti']) * rf
        paths, fixed = _paths(P, meta, h, route, cal, open_bal, inv_income)
    except ValueError as e:
        return empty('method_not_supported_by_forecast', str(e), method)
    # extend the horizon if any scenario ends far from its terminal growth (valuation_audit flags >10pp cliffs)
    gap_end = max(abs(paths[s]['growth'][-1] - P[s]['g_terminal']) for s in SCEN)
    if gap_end > .03 and n_full < 20 and not inputs.get('horizon_years'):
        out = _build(fb, an, card, qa, dict(inputs, horizon_years=min(20, n_full + 5)), external)
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
        row = {'id': aid, 'topic': topic, 'kind': kind,
               'provenance': provenance or {'reported': 'reported', 'analyst': 'vendor_estimate', 'guidance': 'management_guidance'}.get(kind, 'model_assumption'),
               'values': values, 'rationale': rationale, 'falsifier': falsifier, 'source_ids': ids}
        at = next((i for i, r in enumerate(rows) if r['id'] == aid), None)
        if at is None:
            rows.append(row)
        else:
            rows[at] = row

    B = cal['boundaries']
    fy = lambda b: f'FY ending {b}'
    cyc = meta['cycle']
    # opening
    sh_res, cash_res, debt_res = res['shares_outstanding'], res['cash'], res['debt']
    stmt = min(_d(cash_res.get('as_of') or as_of), _d(debt_res.get('as_of') or as_of)).isoformat()
    add('open_balance', 'opening balance', 'reported',
        {'cash': open_bal['cash'], 'short_term_investments': open_bal['sti'], 'long_term_investments': open_bal['lti'],
         'debt': open_bal['debt'], 'deferred_revenue': open_bal['deferred_revenue'], 'statement_as_of': stmt},
        f"dataqa-resolved balance at {stmt}: cash {open_bal['cash']:,.0f}, investments {open_bal['sti'] + open_bal['lti']:,.0f} "
        f"(kept as assets earning risk-free after tax; liquidated before any external financing is assumed), debt {open_bal['debt']:,.0f} "
        f"({debt_res.get('method', 'dataqa')}; operating leases treated in cash costs), deferred revenue "
        "{:,.0f} in its own ledger.".format(open_bal['deferred_revenue']),
        'A primary balance sheet at or after the statement date shows cash, investments or debt differing by more than 2% of market value.',
        bal_srcs)
    add('shares_point', 'point-in-time shares', 'reported', {'shares': open_bal['shares'], 'as_of': sh_res.get('as_of'), 'basis': sh_res.get('basis')},
        f"Point-in-time common shares {open_bal['shares']:,.0f} at {sh_res.get('as_of')} (basis {sh_res.get('basis')}); weighted-average "
        'EPS shares are not used for ownership.', 'The latest filing cover count differs by more than 1%.', share_srcs)
    conv_f = fields.get('convertibles') or {}
    add('dilution', 'dilution', 'model', {'incremental_dilutive_shares': 0},
        'No incremental shares added today: future equity awards are charged as a cash-equivalent cost (sbc_pct), so adding unvested '
        'awards would double count; shares are added only where a cash shortfall is closed by settling SBC in shares or by an '
        f"equity raise (see the funding rows). Convertibles/warrants status in dataqa: {conv_f.get('status', 'not reported')}.",
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
    cur_kind = meta['cur_kind']
    cur_srcs = list(rev_srcs)
    if cur_kind == 'guidance':
        cur_srcs += [x for x in gd['source_ids'] if x not in cur_srcs]
    if meta['ytd']:
        cur_srcs += [x for x in meta['ytd']['source_ids'] if x not in cur_srcs]
    add('revenue_current_fy', 'current fiscal year revenue', cur_kind,
        {'revenue': meta['r_fy'], 'growth': meta['g_cur'], 'stub_annual_rate': meta['stub_rate'],
         'year_to_date': meta['ytd'] and {k: meta['ytd'][k] for k in ('value', 'end', 'prior')}},
        f"{fy(B[0])} revenue {meta['r_fy']:,.0f} ({meta['cur_basis']}); growth {_pct(meta['g_cur'])} vs the last report. The stub "
        f"{as_of}..{B[0]} runs at {meta['stub_rate']:,.0f}/yr = {meta['stub_basis']}.",
        f"Reported {fy(B[0])} revenue differs from {meta['r_fy']:,.0f} by more than 3%.", cur_srcs + cons_srcs + [RULES])
    add('growth_fade', 'growth fade rule', 'model',
        {s: {'half_life_years': (meta['half_life'] if s == 'base' else meta['half_life'] * (.8 if s == 'bear' else 1.3)),
             'terminal_growth': P[s]['g_terminal']} for s in SCEN},
        f"Excess growth over terminal growth halves every {meta['half_life']:.1f} years in base ({route['stage']} stage; base rate: "
        '>20% growers slow to ~8% in five years and ~5% in ten, Koller et al.); bear fades 25% faster, bull 30% slower. Terminal growth '
        + (f"for a declining business: half the trailing decline, bounded at {DECLINE_FLOOR:.0%} (base {_pct(P['base']['g_terminal'])}, "
           f"bear {_pct(P['bear']['g_terminal'])}, bull {_pct(P['bull']['g_terminal'])}); the business is not forced to recover."
           if route['stage'] == 'decline' else f"<= min(risk-free {rf:.2%}, 3%)."),
        'Revenue growth stays more than 5 points above the base path for two consecutive years, or falls below the bear path.', [RULES])
    if cyc:
        add('cycle_normalization', 'mid-cycle revenue', 'model',
            {'mean_revenue': cyc['mean_revenue'], 'mid_date': cyc['mid_date'], 'window': cyc['window'], 'trend_slope': cyc['slope'],
             'trend_growth': cyc['long_run_growth'], 'trend_at_current_fy': cyc['trend_at_current_fy'], 'level_factor': cyc['level_factor'],
             'reversion_years': cyc['conv'], 'peak_ratio': cyc['peak_ratio'], 'trough_ratio': cyc['trough_ratio']},
            f"Cyclical revenue is anchored to the cycle, not the latest year: mean revenue {cyc['mean_revenue']:,.0f} over {cyc['years']} fiscal "
            f"years ({cyc['window'][0]}..{cyc['window'][1]}) placed at {cyc['mid_date']} and grown at the long-run trend {_pct(cyc['long_run_growth']['base'])} "
            f"(log-linear slope {_pct(cyc['slope'])} bounded to {_pct(CYCLE_TREND[0])}..{_pct(CYCLE_TREND[1])}) = {cyc['trend_at_current_fy']:,.0f} at {B[0]}. "
            f"Revenue and margin revert together from today's level over {CYCLE_CONV['base']} years (bear {CYCLE_CONV['bear']}, bull {CYCLE_CONV['bull']}); "
            f"bear settles at trend x {cyc['level_factor']['bear']:.2f} (half-way to the historical trough ratio {cyc['trough_ratio']:.2f} in log terms), "
            f"bull at trend x {cyc['level_factor']['bull']:.2f} (half-way to the peak ratio {cyc['peak_ratio']:.2f}).",
            'Revenue stays more than 15% above the trend path for two years after the reversion period (a structural step-up, not a cycle).',
            rev_srcs + [RULES])
    for s in SCEN:
        g = paths[s]['growth']
        why = {'base': (f"Years 1-{len(meta['consensus_growth'])} follow revenue consensus, then fade" if meta['growth_kind'] == 'analyst'
                        else 'Revenue reverts from today\'s level to the mid-cycle trend (cycle_normalization), then grows at the trend fading to terminal'
                        if cyc else f"Anchored on trailing growth {_pct(h['g_trailing'])} ({h['g_trailing_formula']}) faded from the current year"),
               'bear': ('Faster reversion to a lower cycle level, lower trend growth' if cyc else
                        f"Realistic disappointment: first-year growth min(base - spread, half of base) with spread {_pct(meta['spread'])} "
                        '(max of stage floor, historical growth volatility and consensus dispersion), faster fade'),
               'bull': ('Slower reversion to a higher cycle level, higher trend growth' if cyc else
                        f"Plausible upside: base + spread {_pct(meta['spread'])} (never above base + 2 spreads), slower fade")}[s]
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
    k_moat = .5 if route['moat'] == 'weak' else .25
    typ_m = route['type'][1]
    haircut = k_moat * (fixed['m0'] - typ_m) if route['moat'] not in ('strong',) and fixed['m0'] > typ_m and not cyc else 0.
    add('moat_basis', 'competitive advantage behind margins and returns', 'model',
        {'moat': route['moat'], 'basis': route['moat_basis'], 'margin_competed_away': haircut, 'terminal_roic_base': P['base']['roic'],
         'roic_demonstrated': h['roic'], 'unlevered_cost_of_capital': P['base']['hurdle']},
        (f"Moat {route['moat']} ({route['moat_basis']}): " +
         ('current margin held and terminal ROIC max(demonstrated ROIC, Ku+5pp) capped at 40%.' if route['moat'] == 'strong' else
          f"{k_moat:.0%} of today's margin excess over the type-typical {_pct(typ_m)} is competed away ({haircut:.1%} of revenue) and terminal "
          f"ROIC fades to Ku+2pp = {_pct(P['base']['roic'])} against a demonstrated ROIC {_pct(h['roic'])}. A quantified model haircut, "
          'not evidence: an evidenced analyst moat assessment replaces it.')),
        f"Returns on capital stay above {_pct(P['base']['roic'] + .05)} and margins above {_pct(P['base']['mature_margin'] + .03)} for three years "
        '(haircut too harsh), or fall below the bear path (too generous).', _latest_src(fb, 'operating_income', 'gross_profit', 'equity') + [RULES])
    add('sbc_path', 'stock-based compensation', 'model', {s: paths[s]['sbc_pct'] for s in SCEN},
        f"SBC {_pct(fixed['sbc0'])} of revenue today ({h['sbc_basis']}), fading by {st['sbc_fade']:.0%} of its level over {st['conv']} years at "
        'scale; charged as a cash-equivalent cost (policy cash_equivalent) with point-in-time shares, so no separate dilution is added '
        'unless a cash shortfall is closed by settling it in shares. Value-neutral to the mix: the GAAP margin path already includes SBC.',
        'SBC rises above today\'s share of revenue for two consecutive years.', _latest_src(fb, 'sbc', 'revenue'))
    add('da_capex', 'depreciation and maintenance capex', 'model',
        {'depreciation_pct': fixed['dep'], 'depreciation_path': [_r(x) for x in fixed['dep_path']], 'maintenance_capex_pct': fixed['maint']},
        f"Depreciation {_pct(fixed['dep'])} of revenue today (latest D&A incl. amortization) converges to maintenance capex "
        f"{_pct(fixed['maint'])} = min(3-year average capex {_pct(h['capex_pct'])}, depreciation) by the last explicit year, so the terminal "
        'year carries no free depreciation tax shield or unfunded replacement. The GAAP margin path is held, so D&A above maintenance capex '
        '(mostly amortization of acquired intangibles) is treated as a recurring economic cost by the end, not released as cash.',
        'Capex or D&A as a share of revenue moves by more than 1.5 points.', _latest_src(fb, 'd_and_a', 'capex', 'revenue'))
    add('reinvestment', 'growth reinvestment', 'model',
        {s: {'sales_to_capital': P[s]['s2c'], 'sales_to_capital_terminal': P[s]['s2c_terminal'], 'growth_capex_pct': paths[s]['growth_capex_pct']}
         for s in SCEN},
        f"Growth costs capital: net reinvestment = change in revenue x capital per unit of new revenue, starting at 1 / sales-to-capital "
        f"(history {meta['s2c_history']}, type range {meta['s2c_range']}, base {P['base']['s2c']:.2f}, bear x0.8, bull x1.2) and converging "
        f"by the last explicit year to the terminal rule g / ROIC (sales-to-capital = ROIC / NOPAT margin, base {P['base']['s2c_terminal']:.2f}). "
        'Per period growth capex + change in working capital - change in deferred revenue equals that need on the bases drivers uses '
        '(stub: the current year\'s need pro rata, no working-capital step; first year: change from the current fiscal year); growth '
        f"capex is floored at zero where customer prepayments fund more than the need. Acquisition spend 3y: {_pct(h['acq_pct'])} of revenue.",
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
    mc0 = paths['base']['minimum_cash'][0]
    add('funding_policy', 'minimum cash and payout', 'model',
        {'minimum_cash_months': {s: P[s]['cash_months'] for s in SCEN}, 'payout_fraction': policy, 'payout_history_3y': h['payout_history'],
         'minimum_cash_today': mc0, 'minimum_cash_per_share': mc0 / open_bal['shares']},
        f"Minimum operating cash = min({P['base']['cash_months']:.0f} months of cash costs, today's cash): {mc0:,.0f} of today's cash "
        f"{open_bal['cash']:,.0f} (about {mc0 / open_bal['shares']:,.2f} per share) is HELD throughout and never released, so it adds no value "
        'beyond earning nothing (a judgement: the company already operates with this balance); only cash above it and the investments '
        f"enter the terminal bridge. Excess cash above it is distributed at {policy['base']:.0%} (buybacks/dividends; history: distributions = "
        f"{_pct(h['payout_history'])} of FCF over 3 years) only once free cash flow stays positive; retained cash earns nothing in the model "
        '(conservative). A shortfall is closed by investments, then SBC settled in shares, then assumed debt within capacity, then an '
        'assumed equity raise.',
        'The company accumulates cash for two years without distributing, or distributes while cash falls below the minimum.',
        _latest_src(fb, 'buybacks', 'cfo', 'cash') + [RULES])
    r_new = rf + st['debt_spread']
    add('debt_interest', 'existing debt', 'model', {'interest_rate_existing': meta['interest_existing'], 'new_money_rate': r_new},
        f"Existing debt {open_bal['debt']:,.0f} held flat (maturities refinanced) at {meta['interest_existing']:.2%} "
        f"({'interest expense / debt' if h['interest_expense'] and open_bal['debt'] else 'risk-free + stage spread'}); any assumed new money at "
        f'{r_new:.2%} (risk-free + {st["debt_spread"]:.0%}), only up to {DEBT_CAPACITY:.0f}x EBITDA before SBC.',
        'Refinancing at a rate more than 2 points above the assumption or a covenant breach.', _latest_src(fb, 'interest_expense') + bal_srcs)
    add('investment_income', 'investment income', 'model', {'annual_after_tax': paths['base']['investment_income_after_tax'][0]},
        f"Marketable investments {open_bal['sti'] + open_bal['lti']:,.0f} earn the risk-free rate {rf:.2%} after tax; principal counted once in the "
        'terminal bridge, or spent first when cash falls short (income falls with the balance).',
        'Investments are liquidated for acquisitions or buybacks.', bal_srcs + rf_ids)
    ke_row = lambda: add('cost_of_equity', 'cost of equity and terminal WACC', 'model', {'cost_of_equity': {s: P[s]['ke'] for s in SCEN}},
                         'placeholder until the capital structure is solved', 'n/a', [RULES])
    ke_row()

    def term_row(s):
        rule = ({'base': 'returns stay above the cost of capital: max(demonstrated ROIC, Ku+5pp), capped at 40%', 'bear': '70% of the base ROIC, '
                 'at least Ku+2pp', 'bull': '120% of the base ROIC, capped at 45%'}[s] if route['moat'] == 'strong' else
                {'base': 'excess returns competed down to Ku+2pp', 'bear': 'Ku+1pp', 'bull': 'Ku+4pp'}[s])
        add(f'terminal_{s}', f'{s} terminal value', 'model',
            {s: {'growth': P[s]['g_terminal'], 'roic': P[s]['roic'], 'wacc': P[s]['wacc'], 'operating_margin': paths[s]['operating_margin'][-1]}},
            f"After {B[-1]}: growth {_pct(P[s]['g_terminal'])}, operating margin {_pct(paths[s]['operating_margin'][-1])} = the last explicit "
            f"year (no jump), reinvestment = g / ROIC = {P[s]['g_terminal'] / P[s]['roic']:.1%} of NOPAT with ROIC {_pct(P[s]['roic'])} "
            f"({route['moat']} moat, {rule}; Ku = unlevered cost of capital {_pct(P[s]['hurdle'])}); the last explicit year already "
            f"reinvests on that rule (reinvestment), discounted at WACC {_pct(P[s]['wacc'])}.",
            'Mature-stage margin or returns on capital of the company (or its closest peers) stay below these levels.',
            _latest_src(fb, 'operating_income', 'equity') + [RULES])
    for s in SCEN:
        term_row(s)
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

    # ---- spec: fiscal-year paths expanded onto the period boundaries (the fifth anniversary may split one fiscal year)
    Pb = cal['periods']
    pmap = [next(j for j, b in enumerate(B) if b >= d) for d in Pb]
    starts = [as_of] + Pb[:-1]
    cont = [x in cal['interim'] for x in starts]
    fy_pidx = [Pb.index(b) for b in B]
    ticker = fb['company']['ticker']
    company_id = f"{ticker.lower()}-cik{fb['company']['cik']}" if fb['company'].get('cik') else ticker.lower()
    scen = []
    for s in SCEN:
        p = paths[s]
        thesis = (f"{s.capitalize()}: revenue growth {_pct(p['growth'][0])} in {fy(B[1])} fading to {_pct(P[s]['g_terminal'])}; operating "
                  f"margin after SBC {_pct(p['operating_margin'][0])} -> {_pct(P[s]['mature_margin'])}; growth reinvestment at sales-to-capital "
                  f"{P[s]['s2c']:.2f} converging to {P[s]['s2c_terminal']:.2f}.")
        sc = {'name': s, 'thesis': thesis, 'assumption_ids': [f'growth_{s}', f'margin_{s}', 'cost_of_equity'],
              'period_assumption_ids': ['revenue_current_fy', 'growth_fade', f'growth_{s}', f'margin_{s}', 'sbc_path', 'da_capex',
                                        'reinvestment', 'working_capital', 'tax', 'funding_policy', 'debt_interest', 'investment_income']
                                       + (['cycle_normalization'] if cyc else []),
              'cost_of_equity': _r(P[s]['ke']), 'sbc_policy': 'cash_equivalent',
              'growth': [0. if cont[i] else p['growth'][pmap[i] - 1] for i in range(1, len(Pb))]}
        for k in list(RATIO_KEYS) + list(ABSOLUTE_KEYS):
            sc[k] = [p[k][pmap[i]] for i in range(len(Pb))] if k in p else [0.0] * len(Pb)   # e.g. equity_proceeds: none before funding
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
            'report_dates': cal['report_dates'], 'boundaries': Pb, 'interim_boundaries': list(cal['interim']),
            'stub_annual_revenue_rate': _r(meta['stub_rate'], 2), 'last_full_year_revenue': _r(meta['r_fy'], 2), 'segment_name': 'Revenue',
            'segment_driver': 'subscribers' if route['business_model'] == 'subscription_software' else 'units', 'scenarios': scen}
    if _num(inputs.get('nol')):
        warn('nol_supplied', f"opening tax-loss carryforward {inputs['nol']:,.0f} supplied by inputs", 'low')
    elif fixed['m0'] < 0:
        warn('nol_unknown', 'loss-making company: tax-loss carryforwards are not in the FactBook and are set to 0 (conservative)', 'medium')
    # raw driver overrides (after paths, before funding); lists are per period boundary (spec['boundaries'])
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
        fin = lambda s: [x for x in (f'financing_{s}', f'internal_funding_{s}') if x in known]
        spec['underwriting'] = {
            'opening_reconciliation': {
                'assumption_ids': ['open_balance', 'shares_point'], 'statement_as_of': stmt, 'share_basis': 'point_in_time_common_outstanding',
                'cash_and_equivalents': open_bal['cash'], 'restricted_cash': val('restricted_cash') or 0., 'short_term_investments': open_bal['sti'],
                'long_term_investments': open_bal['lti'], 'investment_haircut': 0, 'debt': open_bal['debt'],
                'common_shares_outstanding': open_bal['shares'], 'incremental_dilutive_shares': 0,
                'dilution_review': {'assumption_ids': ['dilution']}, 'claims_review': {'assumption_ids': ['claims']},
                'bridge_events': [], 'bridge_review': {'assumption_ids': ['bridge']}},
            'scenario_reviews': {s: {'demand_and_capacity': {'assumption_ids': [f'growth_{s}', 'revenue_current_fy', 'growth_fade']
                                                             + (['cycle_normalization'] if cyc else [])},
                                     'funding_and_claims': {'assumption_ids': ['funding_policy', 'debt_interest', 'open_balance', 'claims'] + fin(s)},
                                     'competitive_response': {'assumption_ids': [f'margin_{s}', 'reinvestment', 'moat_basis']},
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
    # ---- funding: investments, SBC settled in shares, assumed debt within capacity, assumed equity; then the payout schedule
    allow = inputs.get('financing', 'assumed') != 'none'
    inv0 = open_bal['sti'] + open_bal['lti']
    fund = _fund(spec, policy, allow, r_new, meta['interest_existing'], open_bal['debt'], locked, inv0)
    if 'error' in fund:
        return dict(empty('invalid_spec', fund['error'], method), spec=spec, assumptions=rows)
    frows = {r['name']: r['forecast'] for r in fund['result']['scenarios']}
    for s in SCEN:
        f = fund['info'][s]
        internal = (f"investments liquidated {f['investments_liquidated']:,.0f}" if f['investments_liquidated'] else '') + \
                   ((', ' if f['investments_liquidated'] else '') + f"SBC settled in {f['sbc_settlement_shares']:,.0f} new shares "
                    f"({f['sbc_settled_in_shares']:,.0f} of the cash-equivalent charge)" if f['sbc_settled_in_shares'] else '')
        if f['status'] == 'assumed_external_financing':
            first = min([d['period_end'] for d in f['draws'] + f['equity']])
            own = frows[s][-1]['original_ownership']
            add(f'financing_{s}', f'{s} assumed financing', 'model', {s: {'debt': f['draws'], 'equity': f['equity'], 'investments': f['liquidation'],
                                                                          'sbc_settled': f['settled'], 'new_shares': f['new_financing_shares']}},
                f"Cash would fall below the minimum operating balance from {first}" + (f" even after internal sources ({internal})" if internal else '') +
                f": external financing of {f['total_assumed']:,.0f} is ASSUMED, not committed: debt {f['debt_assumed']:,.0f} at {r_new:.2%} "
                f"(only within {DEBT_CAPACITY:.0f}x EBITDA before SBC) and equity {f['equity_raised']:,.0f} at the quote "
                f"{spec['quote']['price']:,.2f} less {EQUITY_FEE:.0%} fees ({f['new_financing_shares']:,.0f} new shares; today's holders keep "
                f"{own:.1%} of the company by {spec['boundaries'][-1]}). The dilution is priced into per-share values; a raise at a lower price "
                'dilutes more.',
                f"No committed financing (facility, offering or partner prepayment) exists before {first}, or terms are worse "
                f"(debt above {r_new:.2%}, equity below {spec['quote']['price']:,.2f}).", _latest_src(fb, 'cash', 'cfo') + [RULES])
            _scen(spec, s)['period_assumption_ids'].append(f'financing_{s}')
            warn('financing_assumed', f"{s}: {f['total_assumed']:,.0f} of external financing assumed (not committed) from {first}"
                                      f" (debt {f['debt_assumed']:,.0f}, equity {f['equity_raised']:,.0f})", 'high')
            material.append({'id': f'assumed_financing_{s}', 'description': f'{s} plan depends on assumed (uncommitted) financing',
                             'status': 'resolved' if reviews.get(f'financing_{s}', {}).get('status') == 'reviewed' else 'unresolved',
                             'assumption_ids': [f'financing_{s}']})
        elif f['status'] == 'unfunded':
            why = f.get('reverted') or 'external financing assumptions are disabled (inputs financing=none)'
            warn('funding_blocked', f"{s}: cash falls below the minimum from {f['first_gap']}; {why}; value_company withholds this scenario's values", 'high')
        if internal and f['status'] != 'assumed_external_financing':
            first = min([d['period_end'] for d in f['liquidation'] + f['settled']])
            add(f'internal_funding_{s}', f'{s} internal funding', 'model', {s: {'investments': f['liquidation'], 'sbc_settled': f['settled']}},
                f"Cash would fall below the minimum operating balance from {first}; closed with internal sources, no external financing: "
                f"{internal}. Settling SBC in shares makes the dilution explicit instead of borrowing to pay a non-cash charge.",
                'The investments are restricted, pledged or illiquid, or the company borrows or raises equity to fund operations.',
                bal_srcs + [RULES])
            _scen(spec, s)['period_assumption_ids'].append(f'internal_funding_{s}')
            warn('internal_funding', f"{s}: shortfall closed internally ({internal})", 'low')
        if f['retained']:
            warn('payout_paused', f"{s}: distributions paused before {', '.join(f['retained'])} to keep the minimum cash balance", 'low')
    if route['method'] == 'scenario_survival_fcff':
        warn('survival_model', 'scenario survival model: values depend on funding and on reaching profitability; lower confidence', 'high')
    unknown_reviews = sorted(set(reviews) - {r['id'] for r in rows} - {f'{x}_{s}' for x in ('financing', 'internal_funding') for s in SCEN})
    if unknown_reviews:
        warn('unknown_review_ids', 'reviews supplied for ids not in this forecast: ' + ', '.join(unknown_reviews), 'low')
    finalize()
    base_external = fund['info']['base']['status'] in ('assumed_external_financing', 'unfunded')

    # ---- capital structure: debt weight from the model's own value (fixed point), Ke relevered, terminal WACC
    excess0 = max(0., open_bal['cash'] - _scen(spec, 'base')['minimum_cash'][0])
    net_debt = max(0., open_bal['debt'] - inv0 - excess0)
    ov_ke = {s: float(econ[(s, 'cost_of_equity')]['value']) for s in SCEN if (s, 'cost_of_equity') in econ}
    lev = _relever(spec, P, ku, meta['interest_existing'], net_debt, ov_ke)
    if lev['status'] in ('equity_not_positive', 'capped', 'not_converged'):
        warn('leverage_' + lev['status'], f"debt weight {lev['debt_weight']:.0%} ({lev['status']}): the model's own equity value is too small "
                                          'to carry the debt; the cost of equity is relevered at the cap', 'high')
    ke_parts = _ke_parts(ke_parts, ku, lev)
    ke = P['base']['ke']
    add('cost_of_equity', 'cost of equity and terminal WACC', 'model',
        {'cost_of_equity': {s: P[s]['ke'] for s in SCEN}, 'unlevered': ku, 'terminal_wacc': {s: P[s]['wacc'] for s in SCEN},
         'debt_weight': lev['debt_weight'], 'net_debt': net_debt, 'model_equity': lev['model_equity'], 'kd': lev['kd']},
        f"Ke {ke:.2%} = unlevered Ku {ku:.2%} (risk-free {rf:.2%} + ERP {erp:.2%} + business-risk add-ons " +
        (', '.join(f"{a['factor']} {a['value']:+.2%}" for a in ke_parts['adjustments'] if a['factor'] != 'leverage_relevered') or 'none') +
        f") + leverage {ke - ku:+.2%} = (Ku - Kd {lev['kd']:.2%}) x D/E {lev['d_to_e']:.1%}. Net debt {net_debt:,.0f} (debt - investments - "
        f"cash above the minimum) against the model's own base equity value {lev['model_equity'] or 0:,.0f} gives debt weight "
        f"{lev['debt_weight']:.1%} ({lev['status']}, {lev['iterations']} iterations): a fixed point on the model's value, never the quote, so "
        f"the value is the same at any price. Terminal WACC {P['base']['wacc']:.2%} = Ku - w x Kd x tax (tax shield at operating risk), "
        f"floored at terminal growth + 3 points. External-funding add-on {'applied' if external_assumed else 'not applied'} from the "
        'executed base funding result. Same Ke in all scenarios.',
        'Long-term risk-free rate moves by more than 1 point, the company changes its leverage materially, or the base plan '
        'starts or stops needing external funding.', rf_ids + erp_ids + [RULES])
    for s in SCEN:
        term_row(s)
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
    path_out = {s: [{'end': b, 'growth': (None if j == 0 else paths[s]['growth'][j - 1]), 'revenue_rate': _r(paths[s]['rates'][j], 0),
                     'operating_margin': _r(1 - _scen(spec, s)['cash_cost_ratio'][i] - _scen(spec, s)['sbc_pct'][i] - _scen(spec, s)['depreciation_pct'][i]),
                     'sbc_pct': _scen(spec, s)['sbc_pct'][i], 'growth_capex_pct': _scen(spec, s)['growth_capex_pct'][i],
                     'payout_fraction': _scen(spec, s)['payout_fraction'][i], 'debt_draw': _scen(spec, s)['debt_draw'][i],
                     'equity_proceeds': _scen(spec, s)['equity_proceeds'][i]}
                    for j, (b, i) in enumerate(zip(B, fy_pidx))] for s in SCEN}
    anchors = {'last_fiscal_year_end': h['last_end'], 'last_fiscal_year_revenue': h['last_revenue'], 'fiscal_years': h['n_years'],
               'ttm': h['ttm'], 'year_to_date': h['ytd'],
               'current_fiscal_year': {'end': B[0], 'revenue': meta['r_fy'], 'growth': meta['g_cur'], 'basis': meta['cur_basis'], 'kind': cur_kind},
               'stub': {'start': as_of, 'end': B[0], 'years': cal['stub_years'], 'annual_rate': meta['stub_rate'], 'basis': meta['stub_basis']},
               'growth': {'last': h['g_last'], 'cagr_3y': h['cagr_3y'], 'cagr_5y': h['cagr_5y'], 'recent_annualized': h['g_recent'],
                          'trailing_anchor': h['g_trailing'], 'formula': h['g_trailing_formula'], 'volatility': h['growth_volatility'],
                          'scale_cap': h['g_scale_cap'], 'consensus': {b: e['value'] for b, e in sorted(cons.items())}, 'scenario_spread': meta['spread']},
               'cycle': cyc,
               'operating_margin': {'current': h['m0'], 'basis': h['m0_basis'], 'best_3y': h['m_best3'], 'history': h['m_all']},
               'sbc_pct': h['sbc_pct'], 'sbc_basis': h['sbc_basis'], 'da_pct': h['da_pct'], 'capex_pct_3y': h['capex_pct'],
               'sales_to_capital': {'history': meta['s2c_history'], 'used': meta['s2c_used'], 'type_range': meta['s2c_range'],
                                    'terminal': {s: P[s]['s2c_terminal'] for s in SCEN}},
               'working_capital_ratio': {'value': fixed['wc'], 'basis': h['wc_basis']}, 'deferred_revenue_ratio': fixed['dr'],
               'tax': {'effective_3y': meta['tax_effective'], 'used': meta['tax_used']}, 'interest_rate_existing': meta['interest_existing'],
               'roic': h['roic'], 'payout_history_3y': h['payout_history'], 'net_debt_to_ebitda': h['nd_ebitda'], 'opening': open_bal,
               'reinvestment_identity': fixed['identity'],
               'route': {k: route[k] for k in ('method', 'business_model', 'stage', 'archetype', 'cyclical', 'capital_intensity', 'funding', 'moat', 'moat_basis')},
               'calendar': {k: cal[k] for k in ('boundaries', 'periods', 'interim', 'fifth_anniversary', 'report_dates', 'fiscal_year_end', 'report_rule')}}
    json.dumps(spec, allow_nan=False)
    return {'forecast_version': FORECAST_VERSION, 'status': 'built', 'reason': None, 'method': method, 'spec': spec,
            'spec_sha256': _sha(spec), 'assumptions': rows, 'anchors': anchors, 'paths': path_out,
            'cost_of_equity': {'bear': P['bear']['ke'], 'base': P['base']['ke'], 'bull': P['bull']['ke'], 'components': ke_parts,
                               'terminal_wacc': {s: P[s]['wacc'] for s in SCEN}},
            'funding': fund['info'], 'overrides': applied, 'preview': preview, 'warnings': warnings,
            '_external': base_external, '_external_assumed': bool(external_assumed)}


# ---------------------------------------------------------------- what must be true

def _bisect(f, lo, hi, target, increasing=True, steps=48):
    vlo, vhi = f(lo), f(hi)
    if vlo is None or vhi is None:
        return None, 'invalid_search_domain', (vlo, vhi)
    if not (min(vlo, vhi) <= target <= max(vlo, vhi)):
        return None, 'not_bracketed', (vlo, vhi)
    if increasing is None:
        increasing = vhi >= vlo
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

    Sensitivities of THIS spec, not market expectations: growth (the first five fiscal-year growth rates shifted together), mature
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

    # growth: the first five fiscal-year steps shifted together (a continuation period of a split year has no step); later
    # years and terminal growth unchanged, so the answer is the five-year growth the price needs, as the label says
    n = len(s['boundaries'])
    starts = [s['as_of']] + s['boundaries'][:-1]
    interim = set(s.get('interim_boundaries') or [])
    steps = [i for i in range(stub, n) if starts[i] not in interim]
    gl = base['growth'] if isinstance(base['growth'], list) else [base['growth']] * (n - stub)
    first5 = [i - stub for i in steps[:5]]
    g_anchor = sum(gl[i] for i in first5) / len(first5)

    def grow(shift):
        x = deepcopy(s)
        _scen(x, 'base')['growth'] = [v + shift if i in first5 else v for i, v in enumerate(gl)]
        return _value(x)
    lo_g = max(-.3, -.85 - min(gl[i] for i in first5))
    d, st, rng = _bisect(grow, lo_g, .5, s['quote']['price'], None)
    if d is not None:
        req = g_anchor + d
        row('growth', 'average revenue growth, next five fiscal years', 'solved', req, g_anchor,
            f'At {price:,.2f} the base case needs average revenue growth of {req:.1%} a year over the next five fiscal years '
            f'(base {g_anchor:.1%}), with later years, terminal growth, margins, capital needs and the discount rate unchanged.', 'ratio',
            {'search_status': 'solved', 'growth_shift': d, 'years_shifted': len(first5), 'range_values': rng,
             'limitation': 'Shift of the first five fiscal-year growth rates of this spec, everything else fixed; a sensitivity, not market consensus.'})
    else:
        row('growth', 'average revenue growth, next five fiscal years', st, None, g_anchor,
            f'No five-year growth shift between {lo_g:+.0%} and +50 points reconciles the base case with this price' + side(rng) + '.', 'ratio',
            {'search_status': st, 'range_values': rng})
    # mature margin: cash-cost ratio lowered by delta, ramped in over five full years; terminal margin + delta
    t_m = base['terminal']['operating_margin']
    ccr = base['cash_cost_ratio']
    ramp = [0. if (stub and i == 0) else min(1., sum(1 for j in steps if j <= i) / 5) for i in range(len(ccr))]

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
