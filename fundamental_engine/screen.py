"""Discovery screen: a broad universe -> research candidates by opportunity type, BEFORE full analysis.

It prioritizes research. It never values a company, recommends, sizes or trades; every shortlisted
name goes through pipeline.run with verified data before any price conclusion.

Why it is built this way (docs/GAP_TEST_RESULTS.md):
- Ranking by "cheap versus computed value" did not predict returns out of sample (H2 failed), and
  momentum + near-high is a timing signal, not a selection source. So no lens is ordered by
  cheapness alone and price momentum is never used for selection. "What the price requires" was
  well calibrated and prices needing heroic growth halved 3-4x as often, so it is used as a
  crash-risk check next to the business evidence, not as a buy signal.
- Opportunities differ (quality compounders, quality at a fair price, early commercial growth,
  mispriced/turning businesses, structural tailwinds), so five transparent lenses replace one
  weighted score. A company may appear in several. Each candidate states why; a company that
  misses exactly one condition is kept as a near miss with that reason, never silently dropped.
- Absolute anchors are conventional reference points, not calibrated cutoffs. A dimension level
  needs a balance of signals, so one metric missing an anchor cannot remove a quality company;
  margins are also read against sector peers in the same universe (percentiles shown everywhere).
- Disqualifying conditions (unsupported sector economics, unreliable data, distress) are kept
  separate from attractiveness and from risks that can be priced.
- No preference for growth, size, volatility or stories: none of them is rewarded by itself.
"""
from bisect import bisect_left, bisect_right
from datetime import date
from statistics import median
import json
import math

from . import facts as F
from .expectations import growth_path
from .inflection import features, stage as _stage
from .pit import ttm_at, latest_at, shares_at
from .scores import piotroski as _piotroski, roic as _roic

SCREEN_VERSION = 1
GRADES = {'verified': 'FactBook whose dataqa status is ok',
          'factbook': 'FactBook facts (filing-derived; dataqa not run or not ok)',
          'pit_sec': 'SEC companyfacts, first-filed values usable at t (pit.py)',
          'sec_frames': 'SEC XBRL frames: one calendar period per concept for all filers, no filing dates',
          'screening': 'vendor TTM ratios (normalized, not reconciled to filings)'}
GRADE_ORDER = list(GRADES)
LENSES = ('quality_compounder', 'quality_fair_price', 'early_commercial_growth', 'mispriced_turning', 'structural_tailwind')
DIMENSIONS = ('business_quality', 'growth_quality', 'financial_strength', 'valuation', 'expected_return_proxy',
              'priced_in', 'risk', 'data_uncertainty')
FMP_CALLS = {'A': 'key-metrics-ttm', 'B': 'metrics-ratios-ttm', 'C': 'financial-statement-growth',
             'D': 'financial-scores', 'E': 'financial-estimates'}

# metric: (strong, weak, better). Conventional reference points, NOT calibrated cutoffs; override via options['anchors'].
ANCHORS = {
    'roic': (.15, .06, 'higher'), 'gross_margin': (.45, .20, 'higher'), 'operating_margin': (.20, .05, 'higher'),
    'gross_margin_change': (-.01, -.03, 'higher'), 'fcf_conversion': (.90, .50, 'higher'), 'piotroski': (7, 4, 'higher'),
    'growth_level': (.12, 0., 'higher'), 'acceleration': (.03, -.08, 'higher'), 'operating_margin_trend': (.02, -.05, 'higher'),
    'dilution_yoy': (-.01, .04, 'lower'), 'net_debt_to_ebitda': (1., 3.5, 'lower'), 'interest_coverage': (8., 3., 'higher'),
    'fcf_margin': (.05, 0., 'higher'), 'runway_years': (4., 2., 'higher'),
    'ev_to_fcf': (18., 45., 'lower'), 'pe': (18., 45., 'lower'), 'ev_sales_per_r40': (.25, .60, 'lower'),
    'expected_return_proxy': (.11, .06, 'higher'), 'volatility_1y': (.30, .55, 'lower'),
}
DEFAULTS = {'as_of': None, 'universe': None, 'top_n': 3, 'sector_cap': 2, 'discount_rate': .09, 'terminal_growth': .03,
            'fade_high_years': 5, 'fade_years': 10, 'consensus_hold_years': 2, 'growth_start_bounds': [-.10, .30],
            'sustainable_growth_bounds': [-.05, .15], 'priced_in_tolerance': .04, 'perfection_growth': .30,
            'min_percentile_n': 5, 'min_sector_peers': 4, 'early_growth_min': .20, 'drawdown_overreaction': -.30,
            'shortlist_mode': 'distinct', 'themes': {}, 'structural_industries': [], 'unsupported_industries': None, 'anchors': {}}
UNSUPPORTED_INDUSTRIES = ('Diversified Banks', 'Regional Banks', 'Life & Health Insurance', 'Property & Casualty Insurance',
                          'Multi-line Insurance', 'Reinsurance', 'Consumer Finance', 'Investment Banking & Brokerage',
                          'Commercial & Residential Mortgage Finance', 'Thrifts & Mortgage Finance', 'Multi-Sector Holdings')
KEY_METRICS = ('revenue_growth', 'revenue_cagr_3y', 'gross_margin', 'operating_margin', 'fcf_margin', 'roic', 'ev',
               'market_cap', 'net_debt', 'dilution_yoy', 'consensus_growth_2y', 'piotroski')
PCT_METRICS = {'roic': 'higher', 'gross_margin': 'higher', 'operating_margin': 'higher', 'gross_margin_change': 'higher',
               'fcf_conversion': 'higher', 'piotroski': 'higher', 'growth_level': 'higher', 'revenue_growth': 'higher',
               'acceleration': 'higher', 'operating_margin_trend': 'higher', 'dilution_yoy': 'lower',
               'net_debt_to_ebitda': 'lower', 'interest_coverage': 'higher', 'fcf_margin': 'higher', 'ev_to_fcf': 'lower',
               'pe': 'lower', 'ev_sales_per_r40': 'lower', 'fcf_yield': 'higher', 'expected_return_proxy': 'higher',
               'volatility_1y': 'lower'}
SECTOR_RELATIVE = ('gross_margin', 'operating_margin')
CORE_METRICS = ('gross_margin', 'operating_margin', 'roic', 'revenue_growth', 'ev', 'fcf_margin')   # any one -> the row has data
LABELS = {'roic': ('ROIC', 'pct'), 'gross_margin': ('gross margin', 'pct'), 'operating_margin': ('operating margin', 'pct'),
          'margin_level': ('margin level', 'pct'), 'gross_margin_change': ('gross margin change', 'pts'),
          'fcf_conversion': ('FCF / net income', 'x2'), 'piotroski': ('Piotroski (of 9)', 'n'),
          'growth_level': ('revenue growth', 'pct'), 'consistency': ('growth consistency', 'list'),
          'acceleration': ('growth acceleration', 'pts'), 'operating_margin_trend': ('operating margin trend', 'pts'),
          'dilution_yoy': ('share count change', 'pct1'), 'organic': ('acquired-growth hint', 'pct'),
          'net_debt_to_ebitda': ('net debt / EBITDA', 'x'), 'interest_coverage': ('interest coverage', 'x'),
          'altman': ('Altman zone', 'str'), 'fcf_margin': ('FCF margin', 'pct'), 'runway_years': ('cash runway', 'yrs'),
          'ev_to_fcf': ('EV / FCF', 'x'), 'pe': ('P/E', 'x'), 'ev_sales_per_r40': ('EV/sales per Rule-of-40 point', 'x2')}
WORD = {1: 'supports', 0: 'neutral', -1: 'weighs against', None: 'unknown'}

ER_FORMULA = ('expected_return_proxy = owner FCF yield on EV ((FCF - SBC) / EV) + sustainable growth - excess dilution. '
              'Sustainable growth = geometric mean of a 10-year path that holds the start growth (consensus 2y revenue CAGR, '
              'else demonstrated 3y CAGR, else latest growth; bounded) for 2 years and fades linearly to terminal growth '
              '(expectations.growth_path), then bounded. Excess dilution = max(0, share growth - SBC / market cap), so SBC is '
              'charged once. Assumes a constant multiple and FCF growing with revenue.')
MEANINGS = {
    'business_quality': 'Returns on capital, margin level and stability, cash conversion, Piotroski. strong = high and durable economics today.',
    'growth_quality': 'Growth level, consistency, acceleration, operating leverage, per-share dilution and acquired-growth hints. strong = sizeable growth that is repeatable, organic and accrues per share.',
    'financial_strength': 'Net debt/EBITDA, interest coverage, Altman zone, FCF sign and cash runway. strong = no funding dependence.',
    'valuation': 'EV/FCF, P/E and EV/sales per Rule-of-40 point. strong = undemanding multiples. Cheapness alone is not a selection signal (H2 failed).',
    'expected_return_proxy': 'ROUGH screening-grade annual return proxy; see formula. Not an IRR, not a forecast, never a target. Unknown when owner FCF <= 0 (yield-based proxies cannot see a J-curve).',
    'priced_in': 'Growth the EV / owner-FCF multiple implies under a standard fade model, versus consensus or demonstrated growth. strong = price assumes less than the evidence; weak = more, or priced for perfection. A crash-risk check, not a return predictor.',
    'risk': 'Leverage, distress, dilution, funding runway, priced-for-perfection and volatility flags. strong = no material flag; weak = a high-severity flag.',
    'data_uncertainty': 'Coverage of key metrics and the data grade. strong = low uncertainty (validated FactBook); screening-grade data is at best adequate.',
}
LENS_RULES = {
    'quality_compounder': {
        'label': 'Quality company with strong growth',
        'scope': 'growth_level >= growth anchor (12%/yr). Fast growth is not treated as risky by itself.',
        'conditions': ['business_quality strong or adequate', 'growth_quality strong (repeatable, organic, per share)',
                       'financial_strength not weak'],
        'ordering': 'quality tier (strong before adequate), then not priced for perfection before priced for perfection, then expected-return proxy (desc). Meaning: among comparable quality, the price that leaves the most return ranks first.'},
    'quality_fair_price': {
        'label': 'Quality company at an attractive or fair price',
        'scope': 'business_quality strong (size is irrelevant; a mega cap qualifies).',
        'conditions': ['valuation strong or adequate', 'priced_in strong or adequate', 'growth_quality not weak or unknown (guards value traps)',
                       'financial_strength not weak', 'expected_return_proxy strong or adequate'],
        'ordering': 'expected-return proxy (desc), then business-quality percentile (desc). Meaning: rough return for the price among strong businesses; not a valuation gap ranking.'},
    'early_commercial_growth': {
        'label': 'Earlier-stage growth with commercial proof',
        'scope': 'earlier stage (operating margin < 10%, net loss, or FCF margin < 5%) and growth >= early_growth_min (20%). Size and missing net profit do not reject.',
        'conditions': ['commercial proof: >= 3 of 4 (unit economics, operating leverage, cash trajectory, repeat growth)',
                       'share count growth <= 10%/yr', 'no distress flag'],
        'ordering': 'proof count (desc), then funded before funding-conditional, then per-share growth (desc). Meaning: measured commercial evidence first; story without numbers is never rewarded.'},
    'mispriced_turning': {
        'label': 'Mispriced or turning-point business',
        'scope': 'at least one improvement signal, or a >= 30% drawdown from the high when price data is supplied.',
        'conditions': ['>= 2 improvement signals incl. a profit/cash one, or overreaction path (drawdown with intact quality and growth)',
                       'the price lags: priced_in strong, or valuation strong when priced_in is unknown, or (overreaction path) priced_in not weak',
                       'share count growth <= 8%/yr', 'financial_strength not weak'],
        'ordering': 'improvement-signal count (desc), then priced_in (strong, adequate, unknown), then expected-return proxy (desc). Meaning: business evidence first, price second.'},
    'structural_tailwind': {
        'label': 'Structural long-term opportunity',
        'scope': 'an analyst theme for the symbol (options.themes) or industry (options.structural_industries), or the numeric footprint of an improving competitive position (growth in the top quartile of its sector in this universe with a non-falling gross margin).',
        'conditions': ['durable growth: 3y CAGR >= 8% without repeated declines', 'business_quality not weak',
                       'potential not already priced (priced_in not weak)', 'financial_strength not weak'],
        'ordering': 'priced_in (strong, adequate, unknown), then expected-return proxy (desc), then growth (desc). Meaning: of real tailwinds, those the price has not fully absorbed first. Themes are analyst hypotheses, not evidence.'},
}
LENS_CHECKS = {
    'quality_compounder': 'Durability of growth: organic split, customer concentration, reinvestment runway, and what growth the price requires with company drivers (expectations.expectation_gap).',
    'quality_fair_price': 'Why the price is moderate: identify the market\'s concern and test temporary versus structural; confirm the moat and reinvestment runway.',
    'early_commercial_growth': 'Paid adoption and unit economics from filings (cohorts, retention, gross margin by product), funding runway to the next milestone and dilution to current holders.',
    'mispriced_turning': 'Is the improvement durable (segment/driver evidence over several quarters, not one) or the problem temporary? What does the market fear?',
    'structural_tailwind': 'Value capture: who keeps the profit, supply response and substitutes; how much of the theme the price already assumes.',
}


# ----------------------------------------------------------------- small helpers

def _f(x):
    """Finite float or None (vendor nulls, strings, NaN/inf, booleans become None)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _div(a, b):
    return a / b if a is not None and b is not None and b != 0 else None


def _date(t):
    return t if isinstance(t, date) else date.fromisoformat(str(t)[:10])


def _clamp(x, lo, hi):
    return min(max(x, lo), hi)


def _fmt(v, kind='pct'):
    if v is None:
        return 'n/a'
    if isinstance(v, str):
        return v
    if kind == 'pct':
        return f'{v * 100:.0f}%'
    if kind == 'pct1':
        return f'{v * 100:.1f}%'
    if kind == 'pts':
        return f'{v * 100:+.1f} pts'
    if kind == 'x':
        return f'{v:.1f}x'
    if kind == 'x2':
        return f'{v:.2f}'
    if kind == 'yrs':
        return f'{v:.1f} yrs'
    if kind == 'list':
        return '/'.join('n/a' if x is None else f'{x * 100:.0f}%' for x in v) if isinstance(v, list) else str(v)
    return f'{v:g}'


def _err_kind(text):
    t = str(text)
    return 'access_denied' if 'ACCESS DENIED' in t.upper() else ('skipped' if t.lower().startswith('skipped') else 'error')


def _price_stats(history, as_of):
    """Annualized volatility and drawdown from the 1-year high from closes on or before as_of (no look-ahead)."""
    cut = _date(as_of)
    pts = sorted((_date(p['date']), _f(p.get('close'))) for p in history or [] if _date(p['date']) <= cut)
    pts = [(d, c) for d, c in pts if c and c > 0 and (cut - d).days <= 366]
    if len(pts) < 10:
        return None, None
    rets = [math.log(b[1] / a[1]) for a, b in zip(pts, pts[1:])]
    gap = median((b[0] - a[0]).days for a, b in zip(pts, pts[1:])) or 1
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
    return sd * math.sqrt(365.25 / gap), pts[-1][1] / max(c for _, c in pts) - 1


def _row(symbol, name, sector, industry, as_of, grade, source, m, b, errors=None, status=None, reason=None, extra=None):
    m = {k: v for k, v in m.items() if v is not None}
    status = status or ('ok' if any(k in m for k in CORE_METRICS) else 'no_data')
    if status == 'no_data' and not reason:
        reason = ('vendor returned no data: ' + ', '.join(f'{k} {v}' for k, v in sorted((errors or {}).items()))) if errors else 'no usable metrics'
    row = {'symbol': symbol, 'name': name, 'sector': sector, 'industry': industry, 'market_cap': m.get('market_cap'),
           'as_of': as_of, 'data_grade': grade, 'status': status, 'no_data_reason': reason if status == 'no_data' else None,
           'source': source, 'metrics': m, 'basis': {k: v for k, v in b.items() if k in m}, 'errors': errors or {}}
    row.update(extra or {})
    return row


def _growth_from_series(m, b, series, label):
    s = [x for x in series if x is not None]
    if not s:
        return
    m['revenue_growth'], b['revenue_growth'] = s[-1], f'{label}, latest year'
    m['revenue_growth_series'], b['revenue_growth_series'] = s[-4:], f'{label}, oldest first'
    if len(s) >= 3:
        p = 1.
        for x in s[-3:]:
            p *= 1 + x
        if p > 0:
            m['revenue_cagr_3y'], b['revenue_cagr_3y'] = p ** (1 / 3) - 1, f'geometric mean of the last 3 annual growth rates ({label})'
        m['acceleration'], b['acceleration'] = s[-1] - (s[-2] + s[-3]) / 2, 'latest growth minus the mean of the two prior years'
    elif len(s) == 2:
        m['acceleration'], b['acceleration'] = s[-1] - s[-2], 'latest growth minus prior year'


# ----------------------------------------------------------------- adapters

def rows_from_fmp_screen(records, universe_meta=None, as_of=None, prices=None):
    """FMP screening records (runs/universe_*/batches/*.jsonl lines) -> universe rows, data_grade 'screening'.

    Margins and ratios are vendor TTM; growth rows are fiscal years; consensus is a vendor estimate.
    Vendor error texts are classified (access_denied / skipped / error) and never copied: they are
    untrusted data. A symbol whose calls were denied becomes status 'no_data' (reported under no_data).
    prices: optional {symbol: {'volatility_1y', 'drawdown_from_high'}}."""
    meta = {x['symbol']: x for x in universe_meta or []}
    out = []
    for r in records:
        r = json.loads(r) if isinstance(r, str) else r
        sym = r['symbol']
        km, rt, sc = r.get('km') or {}, r.get('rt') or {}, r.get('scores') or {}
        day = as_of or (r.get('retrieved_utc') or '')[:10] or None
        m, b = {}, {}

        def put(k, v, basis):
            if v is not None:
                m[k], b[k] = v, basis
        g = lambda d, k: _f(d.get(k))
        mcap = g(km, 'marketCap') or g(sc, 'marketCap')
        put('market_cap', mcap, 'FMP marketCap')
        ev = g(km, 'enterpriseValueTTM')
        put('ev', ev, 'FMP enterpriseValueTTM')
        if ev is not None and mcap is not None:
            put('net_debt', ev - mcap, 'FMP EV - market cap (includes minorities and preferred)')
        if ev and g(km, 'evToEBITDATTM'):
            put('ebitda', ev / g(km, 'evToEBITDATTM'), 'FMP EV / (EV/EBITDA TTM)')
        if (g(km, 'evToFreeCashFlowTTM') or 0) > 0:
            put('ev_to_fcf', g(km, 'evToFreeCashFlowTTM'), 'FMP evToFreeCashFlowTTM')
        fy = g(km, 'freeCashFlowYieldTTM')
        put('fcf_yield', fy, 'FMP freeCashFlowYieldTTM (FCF / market cap)')
        if fy is not None and mcap:
            put('fcf_ttm', fy * mcap, 'FMP FCF yield x market cap')
        put('revenue_ttm', g(sc, 'revenue'), 'FMP financial-scores revenue')
        for k, src in (('roic', 'returnOnInvestedCapitalTTM'), ('roe', 'returnOnEquityTTM'), ('income_quality', 'incomeQualityTTM'),
                       ('sbc_to_revenue', 'stockBasedCompensationToRevenueTTM'), ('capex_to_revenue', 'capexToRevenueTTM')):
            put(k, g(km, src), f'FMP {src}')
        for k, src in (('gross_margin', 'grossProfitMarginTTM'), ('operating_margin', 'operatingProfitMarginTTM'),
                       ('net_margin', 'netProfitMarginTTM'), ('interest_coverage', 'interestCoverageRatioTTM')):
            put(k, g(rt, src), f'FMP {src}')
        if (g(rt, 'priceToEarningsRatioTTM') or 0) > 0:
            put('pe', g(rt, 'priceToEarningsRatioTTM'), 'FMP priceToEarningsRatioTTM')
        fps, rps, nps = g(rt, 'freeCashFlowPerShareTTM'), g(rt, 'revenuePerShareTTM'), g(rt, 'netIncomePerShareTTM')
        if rps and rps > 0:
            put('fcf_margin', _div(fps, rps), 'FMP FCF per share / revenue per share (TTM)')
        if nps and nps > 0:
            put('fcf_conversion', _div(fps, nps), 'FMP FCF per share / net income per share (TTM)')
        z = g(sc, 'altmanZScore')
        if z is not None:
            put('altman_z', z, 'FMP altmanZScore (original Z: safe > 2.99, distress < 1.81)')
            put('altman_zone', 'safe' if z > 2.99 else ('distress' if z < 1.81 else 'grey'), 'zone from FMP altmanZScore')
        put('piotroski', g(sc, 'piotroskiScore'), 'FMP piotroskiScore (0-9)')
        gr = sorted(r.get('growth') or [], key=lambda x: str(x.get('date')))
        _growth_from_series(m, b, [_f(x.get('revenueGrowth')) for x in gr], 'FMP revenueGrowth (fiscal years)')
        if gr:
            last = gr[-1]
            rg, gpg, oig = _f(last.get('revenueGrowth')), _f(last.get('grossProfitGrowth')), _f(last.get('operatingIncomeGrowth'))
            gm, om = m.get('gross_margin'), m.get('operating_margin')
            if None not in (rg, gpg, gm) and 1 + rg > 0 and 1 + gpg > 0:
                rel = (1 + gpg) / (1 + rg)
                put('gross_margin_change', gm - gm / rel, 'approx: TTM gross margin x (1 - (1+revenueGrowth)/(1+grossProfitGrowth)), last FY')
            if None not in (rg, oig, om) and om > 0 and 1 + rg > 0 and 1 + oig > 0:
                rel = (1 + oig) / (1 + rg)
                put('operating_margin_trend', om - om / rel, 'approx: TTM operating margin with last FY operatingIncomeGrowth vs revenueGrowth')
            dil = [_f(x.get('weightedAverageSharesDilutedGrowth')) for x in gr]
            dil = [x for x in dil if x is not None]
            if dil:
                put('dilution_yoy', dil[-1], 'FMP weightedAverageSharesDilutedGrowth, latest FY')
                put('dilution_3y', sum(dil[-3:]) / len(dil[-3:]), 'mean of the last 3 FY diluted share growth rates')
        if day:
            est = sorted((str(x.get('date'))[:10], _f(x.get('revenueAvg')), x.get('numAnalystsRevenue')) for x in r.get('estimates') or [])
            fut = [e for e in est if e[0] > day and e[1] and e[1] > 0]
            if len(fut) >= 2:
                k = min(2, len(fut) - 1)
                put('consensus_growth_2y', (fut[k][1] / fut[0][1]) ** (1 / k) - 1,
                    f'FMP revenueAvg FY{fut[0][0][:4]} -> FY{fut[k][0][:4]} CAGR (vendor consensus, not a reported fact)')
                put('consensus_n_analysts', _f(fut[k][2]), 'FMP numAnalystsRevenue')
        p = (prices or {}).get(sym) or {}
        put('volatility_1y', _f(p.get('volatility_1y')), 'supplied price data')
        put('drawdown_from_high', _f(p.get('drawdown_from_high')), 'supplied price data')
        mt = meta.get(sym, {})
        errs = {FMP_CALLS.get(k, k): _err_kind(v) for k, v in sorted((r.get('errors') or {}).items()) if v}
        out.append(_row(sym, mt.get('name'), mt.get('sector'), mt.get('sub_industry') or mt.get('industry'), day, 'screening',
                        {'provider': 'fmp', 'kind': 'vendor_normalized', 'retrieved_at': r.get('retrieved_utc'),
                         'datasets': [v for k, v in FMP_CALLS.items() if v not in errs], 'universe_source': mt.get('source')},
                        m, b, errs))
    return out


def rows_from_pit(records, prices, t, meta=None):
    """pit.company records {symbol: rec} + prices {symbol: {'price', 'market_cap'?, 'volatility_1y'?, 'drawdown_from_high'?}}
    at date t -> universe rows, data_grade 'pit_sec'. Only facts filed + lag <= t are used (inflection.features,
    pit.ttm_at). The price must be the close at t, unadjusted for later splits (or adjust as scripts/pit_panel.py)."""
    t = _date(t)
    out = []
    for sym in sorted(records):
        rec, p, mt = records[sym], (prices or {}).get(sym) or {}, (meta or {}).get(sym) or {}
        f = features(rec, t)
        src = {'provider': 'sec', 'kind': 'filing_derived', 'dataset': 'companyfacts (pit.company)', 'as_of': t.isoformat()}
        name, sector, ind = mt.get('name') or rec.get('name'), mt.get('sector'), mt.get('industry') or mt.get('sub_industry')
        if not f:
            out.append(_row(sym, name, sector, ind, t.isoformat(), 'pit_sec', src, {}, {}, status='no_data',
                            reason='fewer than 8 usable revenue quarters or latest quarter older than 150 days at t'))
            continue
        m, b = {}, {}

        def put(k, v, basis):
            if v is not None:
                m[k], b[k] = v, basis
        rt, rt1 = f['rev_ttm'], f['rev_ttm'] / (1 + f['g_ttm'])
        put('revenue_ttm', rt, 'sum of 4 standalone quarters usable at t')
        ttms = [ttm_at(rec['rev'], t, offset=k)[0] for k in (0, 4, 8, 12)]
        series = [(_div(ttms[i], ttms[i + 1]) - 1) if ttms[i] and ttms[i + 1] and ttms[i + 1] > 0 else None for i in (2, 1, 0)]
        _growth_from_series(m, b, series, 'TTM revenue growth at 0/4/8 quarter offsets')
        put('revenue_growth', f['g_ttm'], 'TTM / TTM one year earlier - 1 (inflection.features)')
        put('acceleration', f.get('accel_2q'), 'YoY growth of the latest quarter minus two quarters earlier (inflection.features)')
        put('acceleration_streak', f.get('accel_streak'), 'consecutive improving YoY quarters')
        put('gross_margin', f.get('gross_margin'), 'gross profit TTM / revenue TTM')
        if f.get('gross_margin') is not None and f.get('gp_ttm_1y') is not None:
            put('gross_margin_change', f['gross_margin'] - f['gp_ttm_1y'] / rt1, 'gross margin TTM minus one year earlier')
        put('operating_margin', f.get('op_margin'), 'operating income TTM / revenue TTM')
        put('operating_margin_trend', f.get('op_margin_trend'), 'operating margin TTM minus one year earlier')
        put('incremental_operating_margin', f.get('incremental_op_margin'), 'change in operating income / change in revenue')
        ni = ttm_at(rec.get('ni') or {}, t)[0]
        put('net_income_ttm', ni, 'net income TTM')
        put('net_margin', _div(ni, rt), 'net income TTM / revenue TTM')
        if f.get('ocf_ttm') is not None:
            fcf = f['ocf_ttm'] - (f.get('capex_ttm') or 0)
            put('fcf_ttm', fcf, 'operating cash flow TTM - capex TTM')
            put('fcf_margin', f.get('fcf_margin'), 'FCF TTM / revenue TTM')
            if f.get('fcf_margin_1y') is not None:
                put('fcf_margin_change', f['fcf_margin'] - f['fcf_margin_1y'], 'FCF margin TTM minus one year earlier')
            if ni and ni > 0:
                put('fcf_conversion', fcf / ni, 'FCF TTM / net income TTM')
        put('sbc_to_revenue', f.get('sbc_to_revenue'), 'SBC TTM / revenue TTM')
        put('capex_to_revenue', f.get('capex_to_revenue'), 'capex TTM / revenue TTM')
        put('dilution_yoy', f.get('dilution_yoy'), 'cover-page shares vs ~1 year earlier')
        put('acquisition_driven', f.get('acquisition_driven_risk'), 'goodwill jump > 10% of revenue in a year (inflection.features)')
        put('goodwill_jump_to_revenue', f.get('goodwill_jump_to_rev'), 'goodwill change over ~1 year / revenue TTM')
        cash = (latest_at(rec.get('cash') or {}, t) or 0) + (latest_at(rec.get('sti') or {}, t) or 0)
        debt = (latest_at(rec.get('ltd') or {}, t) or 0) + (latest_at(rec.get('ltd_cur') or {}, t) or 0)
        put('net_debt', debt - cash, 'term debt + current debt - cash - short-term investments (converts/leases not included)')
        if f.get('oi_ttm') is not None:
            put('ebitda', f['oi_ttm'], 'EBIT proxy: D&A is not in the PIT record (overstates leverage)')
            eq = latest_at(rec.get('equity') or {}, t)
            if eq is not None and eq + debt - cash > 0:
                put('roic', f['oi_ttm'] * .79 / (eq + debt - cash), 'approx: operating income TTM x (1 - 21%) / (equity + debt - cash)')
        price, mcap = _f(p.get('price')), _f(p.get('market_cap'))
        if mcap is None and price:
            _, sh, sh_src = shares_at(rec, t)
            if sh:
                mcap = sh * price
                b['market_cap'] = f'price x shares ({sh_src}, pit.shares_at)'
        if mcap:
            b.setdefault('market_cap', 'supplied market cap')
            m['market_cap'] = mcap
            put('ev', mcap + debt - cash, 'market cap + debt - cash')
            if ni and ni > 0:
                put('pe', mcap / ni, 'market cap / net income TTM')
        put('volatility_1y', _f(p.get('volatility_1y')), 'supplied price data')
        put('drawdown_from_high', _f(p.get('drawdown_from_high')), 'supplied price data')
        m['stage_pit'], b['stage_pit'] = f.get('stage'), 'inflection.stage'
        if rec.get('financial'):
            m['financial_statements'], b['financial_statements'] = True, 'bank/insurer XBRL markers (pit.FIN_MARKERS)'
        out.append(_row(sym, name, sector, ind, t.isoformat(), 'pit_sec', src, m, b))
    return out


def rows_from_sec_frames(frame_rows, prices=None, history=None, as_of=None):
    """sec_frames.rows output (one calendar period) -> universe rows, data_grade 'sec_frames'.

    history: optional list of earlier sec_frames.rows outputs, oldest first, for growth and trends.
    prices: {symbol: {'price'?, 'market_cap'?, 'volatility_1y'?, 'drawdown_from_high'?}}. Frames carry no filing
    dates, so these rows are for a CURRENT screen only (never a historical test)."""
    hist = [{r['symbol']: r for r in rows} for rows in history or []]
    out = []
    for r in frame_rows:
        sym, p = r['symbol'], (prices or {}).get(r['symbol']) or {}
        m, b = {}, {}

        def put(k, v, basis):
            if v is not None:
                m[k], b[k] = v, basis
        rev, gp, oi, ni, fcf, sbc = (_f(r.get(k)) for k in ('revenue', 'gross_profit', 'operating_income', 'net_income', 'fcf', 'sbc'))
        if rev and rev > 0:
            put('revenue_ttm', rev, 'calendar-year frame (not TTM)')
            put('gross_margin', _div(gp, rev), 'gross profit / revenue (frame)')
            put('operating_margin', _div(oi, rev), 'operating income / revenue (frame)')
            put('net_margin', _div(ni, rev), 'net income / revenue (frame)')
            put('fcf_margin', _div(fcf, rev), '(CFO - capex) / revenue (frame)')
            put('sbc_to_revenue', _div(sbc, rev), 'SBC / revenue (frame)')
        put('fcf_ttm', fcf, 'CFO - capex (frame)')
        put('net_income_ttm', ni, 'net income (frame)')
        if fcf is not None and ni and ni > 0:
            put('fcf_conversion', fcf / ni, 'FCF / net income (frame)')
        cash = (_f(r.get('cash')) or 0) + (_f(r.get('short_term_investments')) or 0)
        debt = _f(r.get('debt'))
        if debt is not None or r.get('cash') is not None:
            put('net_debt', (debt or 0) - cash, f"debt ({r.get('debt_scope', 'unknown')} scope) - cash - short-term investments")
        put('ebitda', oi, 'EBIT proxy: D&A not in frames (overstates leverage)')
        eq = _f(r.get('equity'))
        if oi is not None and eq is not None and eq + (debt or 0) - cash > 0:
            put('roic', oi * .79 / (eq + (debt or 0) - cash), 'approx: operating income x (1 - 21%) / (equity + debt - cash)')
        mcap = _f(p.get('market_cap')) or _f(r.get('market_cap'))
        price = _f(p.get('price')) or _f(r.get('price'))
        if mcap is None and price and _f(r.get('shares_outstanding')):
            mcap = price * _f(r.get('shares_outstanding'))
            b['market_cap'] = 'price x balance-sheet shares outstanding (frame instant)'
        if mcap:
            b.setdefault('market_cap', 'supplied market cap')
            m['market_cap'] = mcap
            put('ev', mcap + (m.get('net_debt') or 0), 'market cap + net debt')
            if ni and ni > 0:
                put('pe', mcap / ni, 'market cap / net income (frame)')
        prev = [h.get(sym) for h in hist]
        revs = [_f(x.get('revenue')) if x else None for x in prev] + [rev]
        _growth_from_series(m, b, [(_div(revs[i + 1], revs[i]) - 1) if revs[i] and revs[i + 1] and revs[i] > 0 else None
                                   for i in range(len(revs) - 1)], 'calendar-year frames revenue growth')
        last = prev[-1] if prev else None
        if last and _f(last.get('revenue')):
            lr = _f(last['revenue'])
            if gp is not None and _f(last.get('gross_profit')) is not None and rev:
                put('gross_margin_change', gp / rev - _f(last['gross_profit']) / lr, 'gross margin minus prior calendar year')
            if oi is not None and _f(last.get('operating_income')) is not None and rev:
                put('operating_margin_trend', oi / rev - _f(last['operating_income']) / lr, 'operating margin minus prior calendar year')
            if fcf is not None and _f(last.get('fcf')) is not None and rev:
                put('fcf_margin_change', fcf / rev - _f(last['fcf']) / lr, 'FCF margin minus prior calendar year')
            s0, s1 = _f(last.get('shares_outstanding')), _f(r.get('shares_outstanding'))
            if s0 and s1:
                put('dilution_yoy', s1 / s0 - 1, 'balance-sheet shares outstanding vs prior calendar year')
            g0, g1 = _f(last.get('goodwill')), _f(r.get('goodwill'))
            if g0 is not None and g1 is not None and rev:
                put('goodwill_jump_to_revenue', (g1 - g0) / rev, 'goodwill change / revenue')
                put('acquisition_driven', (g1 - g0) / rev > .10, 'goodwill jump > 10% of revenue')
        put('volatility_1y', _f(p.get('volatility_1y')), 'supplied price data')
        put('drawdown_from_high', _f(p.get('drawdown_from_high')), 'supplied price data')
        out.append(_row(sym, r.get('name'), r.get('sector'), r.get('industry'), as_of or r.get('as_of'), 'sec_frames',
                        {'provider': 'sec', 'kind': 'filing_derived', 'dataset': 'XBRL frames (sec_frames.rows)', 'cik': r.get('cik')}, m, b))
    return out


def _pick(seq, i, ticker):
    if seq is None:
        return None
    return seq.get(ticker) if isinstance(seq, dict) else (seq[i] if i < len(seq) else None)


def _fb_tables(fb, an):
    """Annual rows (ascending), TTM and latest balance in analysis.analyze key names. Uses the analysis output when
    given; otherwise a light read of the FactBook through its point-in-time accessors (labelled)."""
    if an and an.get('annual'):
        years = [dict(an['annual'][e], period_end=e) for e in an.get('years') or sorted(an['annual'])]
        ttm = dict(an.get('ttm') or {})
        bal = {k: ttm.get(k, years[-1].get(k) if years else None) for k in ('cash', 'investments', 'debt', 'equity')}
        return years, ttm, bal, 'analysis.analyze output'
    dur = ['revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'sbc', 'd_and_a', 'interest_expense',
           'acquisitions', 'income_tax', 'pretax_income']
    ins = ['cash', 'short_term_investments', 'long_term_investments', 'long_term_debt', 'long_term_debt_tagged',
           'current_portion_ltd', 'short_term_borrowings', 'equity', 'total_assets', 'current_assets', 'current_liabilities',
           'total_liabilities']

    def shape(v):
        v = dict(v)
        if v.get('cfo') is not None and v.get('capex') is not None:
            v['fcf'] = v['cfo'] - abs(v['capex'])
        v['investments'] = (v.get('short_term_investments') or 0) + (v.get('long_term_investments') or 0)
        if v.get('long_term_debt') is not None:
            v['debt'] = v['long_term_debt'] + (v.get('current_portion_ltd') or 0) + (v.get('short_term_borrowings') or 0)
        elif v.get('long_term_debt_tagged') is not None:
            v['debt'] = v['long_term_debt_tagged'] + (v.get('short_term_borrowings') or 0)
        return v
    years = [shape(dict(v, period_end=e)) for e, v in F.annual_table(fb, dur + ['shares_diluted_wavg'] + ins).items() if v.get('revenue')]
    ttm = {}
    for k in dur:
        t = F.ttm(fb, k)
        if t:
            ttm[k] = t['value']
            ttm.setdefault('method', t['method'])
    ttm = shape(ttm)
    lat = {k: (F.latest_instant(fb, k) or {}).get('value') for k in ins}
    bal = shape(lat)
    return years, ttm, {k: bal.get(k) for k in ('cash', 'investments', 'debt', 'equity')}, 'FactBook light read (no analysis output; debt scope unvalidated)'


def rows_from_factbooks(factbooks, analyses=None, qas=None, prices=None):
    """FactBooks (+ optional analysis.analyze outputs and dataqa results, as {ticker: x} or parallel lists)
    -> universe rows. data_grade 'verified' when dataqa status is ok, else 'factbook'.

    Reads only facts available at each FactBook's as_of. ROIC and Piotroski come from the analysis output
    when given, else from scores.py over the FactBook annual rows (Altman needs retained earnings: unknown)."""
    out = []
    for i, fb in enumerate(factbooks):
        co, tk = fb['company'], fb['company']['ticker']
        an, qa, p = _pick(analyses, i, tk), _pick(qas, i, tk), (prices or {}).get(tk) or {}
        years, ttm, bal, how = _fb_tables(fb, an)
        res = (qa or {}).get('resolved') or {}
        m, b = {}, {}

        def put(k, v, basis):
            if v is not None:
                m[k], b[k] = v, basis
        rev = ttm.get('revenue')
        put('revenue_ttm', rev, f"revenue TTM ({ttm.get('method', 'analysis')})")
        if rev and rev > 0:
            put('gross_margin', _div(ttm.get('gross_profit'), rev), 'gross profit / revenue (TTM)')
            put('operating_margin', _div(ttm.get('operating_income'), rev), 'operating income / revenue (TTM)')
            put('net_margin', _div(ttm.get('net_income'), rev), 'net income / revenue (TTM)')
            put('fcf_margin', _div(ttm.get('fcf'), rev), '(CFO - capex) / revenue (TTM)')
            put('sbc_to_revenue', _div(ttm.get('sbc'), rev), 'SBC / revenue (TTM)')
        put('fcf_ttm', ttm.get('fcf'), 'CFO - capex (TTM)')
        put('net_income_ttm', ttm.get('net_income'), 'net income (TTM)')
        if ttm.get('fcf') is not None and (ttm.get('net_income') or 0) > 0:
            put('fcf_conversion', ttm['fcf'] / ttm['net_income'], 'FCF / net income (TTM)')
        rv = [y.get('revenue') for y in years]
        _growth_from_series(m, b, [(_div(rv[j + 1], rv[j]) - 1) if rv[j] and rv[j + 1] and rv[j] > 0 else None
                                   for j in range(len(rv) - 1)], 'annual revenue growth (FactBook)')
        if len(years) >= 2:
            y1, y0 = years[-1], years[-2]
            mg = lambda y, k: _div(y.get(k), y.get('revenue'))
            for name, k in (('gross_margin_change', 'gross_profit'), ('operating_margin_trend', 'operating_income'), ('fcf_margin_change', 'fcf')):
                if mg(y1, k) is not None and mg(y0, k) is not None:
                    put(name, mg(y1, k) - mg(y0, k), f'{k} margin, last fiscal year minus prior')
            s1, s0 = y1.get('shares_diluted_wavg'), y0.get('shares_diluted_wavg')
            if s1 and s0:
                put('dilution_yoy', s1 / s0 - 1, 'diluted weighted shares, last FY vs prior')
            acq = [abs(y.get('acquisitions') or 0) for y in years[-3:]]
            if any(y.get('acquisitions') is not None for y in years[-3:]):
                put('acquisitions_pct_revenue', sum(acq) / sum(y['revenue'] for y in years[-3:]), 'acquisition spend / revenue, last 3 FY')
        cash = (bal.get('cash') or 0) + (bal.get('investments') or 0)
        nc = (res.get('net_cash') or {}).get('value')
        if nc is not None:
            put('net_debt', -nc, 'dataqa resolved net cash (negated)')
        elif bal.get('debt') is not None or bal.get('cash') is not None:
            put('net_debt', (bal.get('debt') or 0) - cash, f'debt - cash - investments ({how})')
        if ttm.get('operating_income') is not None:
            put('ebitda', ttm['operating_income'] + (ttm.get('d_and_a') or 0),
                'operating income + D&A (TTM)' if ttm.get('d_and_a') is not None else 'EBIT proxy (D&A unknown)')
        if (ttm.get('interest_expense') or 0) > 0 and ttm.get('operating_income') is not None:
            put('interest_coverage', ttm['operating_income'] / ttm['interest_expense'], 'operating income / interest expense (TTM)')
        met = (an or {}).get('metrics') or {}
        mv = lambda k: _f((met.get(k) or {}).get('value')) if isinstance(met.get(k), dict) else None
        for k, src in (('roic', 'roic'), ('net_debt_to_ebitda', 'net_debt_to_ebitda'), ('interest_coverage', 'interest_coverage'),
                       ('acquisitions_pct_revenue', 'acquisition_spend_pct_revenue_3y'), ('dilution_3y', 'share_count_cagr_3y')):
            if mv(src) is not None:
                m[k], b[k] = mv(src), f'analysis metric {src}'
        hist = [{'fiscal_year': y['period_end'][:4], 'period_end': y['period_end'], 'revenue': y.get('revenue'),
                 'gross_profit': y.get('gross_profit'), 'operating_income': y.get('operating_income'), 'net_income': y.get('net_income'),
                 'operating_cash_flow': y.get('cfo'), 'capex': y.get('capex'), 'total_assets': y.get('total_assets'),
                 'current_assets': y.get('current_assets'), 'current_liabilities': y.get('current_liabilities'),
                 'total_liabilities': y.get('total_liabilities'), 'equity': y.get('equity'), 'cash': y.get('cash'),
                 'total_debt': y.get('debt'), 'shares_diluted': y.get('shares_diluted_wavg'), 'income_tax': y.get('income_tax'),
                 'pretax_income': y.get('pretax_income')} for y in years]
        fx = (an or {}).get('forensics') or {}
        pio = fx.get('piotroski') if isinstance(fx.get('piotroski'), dict) else (_piotroski(hist) if hist else {})
        if pio.get('status') == 'computed' and pio.get('out_of'):
            put('piotroski', pio['score'] / pio['out_of'] * 9, f"Piotroski {pio['score']}/{pio['out_of']} scaled to 9 (scores.piotroski)")
        alt = fx.get('altman') if isinstance(fx.get('altman'), dict) else {}
        if alt.get('status') == 'computed':
            put('altman_zone', alt['zone'], f"scores.altman {alt.get('model')}")
        if 'roic' not in m and hist:
            ro = _roic(hist)
            if ro.get('status') == 'computed':
                put('roic', ro['roic_last'], 'scores.roic: NOPAT / (equity + debt - cash)')
        price = ((fb.get('market') or {}).get('price') or {}).get('value')
        sh = (res.get('shares_outstanding') or {}).get('value')
        sh_basis = 'dataqa resolved shares outstanding'
        if sh is None:
            cands = [s[-1] for k in ('shares_outstanding_cover', 'shares_outstanding', 'shares_outstanding_vendor')
                     for s in [F.series(fb, k, 'instant')] if s]
            if cands:
                e, sh, fact = max(cands, key=lambda x: x[0])
                sh_basis = f"latest share count {fact['metric']} at {e} (source {fact['source_id']})"
        if price and sh:
            put('market_cap', price * sh, f'price x {sh_basis}')
            put('ev', price * sh + (m.get('net_debt') or 0), 'market cap + net debt')
            if (ttm.get('net_income') or 0) > 0:
                put('pe', price * sh / ttm['net_income'], 'market cap / net income (TTM)')
        est = sorted((e['period_end'], _f(e['value'])) for e in fb.get('estimates') or []
                     if e.get('metric') == 'revenue' and str(e.get('as_of', fb['as_of'])) <= fb['as_of'] and e['period_end'] > fb['as_of'])
        est = [e for e in est if e[1] and e[1] > 0]
        if len(est) >= 2:
            k = min(2, len(est) - 1)
            put('consensus_growth_2y', (est[k][1] / est[0][1]) ** (1 / k) - 1, 'FactBook revenue estimates CAGR (vendor consensus)')
        vol, dd = _price_stats((fb.get('market') or {}).get('price_history'), fb['as_of'])
        put('volatility_1y', _f(p.get('volatility_1y')) if p.get('volatility_1y') is not None else vol, 'price history <= as_of')
        put('drawdown_from_high', _f(p.get('drawdown_from_high')) if p.get('drawdown_from_high') is not None else dd, 'price history <= as_of')
        grade = 'verified' if qa and qa.get('status') == 'ok' else 'factbook'
        out.append(_row(tk, co.get('name'), co.get('sector'), co.get('industry'), fb['as_of'], grade,
                        {'provider': 'factbook', 'kind': 'filing_derived', 'tables': how, 'fingerprint': F.fingerprint(fb),
                         'dataqa_status': (qa or {}).get('status')}, m, b))
    return out


# ----------------------------------------------------------------- derived metrics and universe context

def _derive(m, b):
    """Fill metrics every dimension needs from whatever the adapter supplied (each with its formula)."""
    def put(k, v, basis):
        if m.get(k) is None and v is not None:
            m[k], b[k] = v, basis
    mc, rev = m.get('market_cap'), m.get('revenue_ttm')
    if m.get('ev') is None and mc is not None and m.get('net_debt') is not None:
        put('ev', mc + m['net_debt'], 'market cap + net debt')
    if m.get('net_debt') is None and m.get('ev') is not None and mc is not None:
        put('net_debt', m['ev'] - mc, 'EV - market cap')
    if m.get('fcf_ttm') is None:
        put('fcf_ttm', m['fcf_margin'] * rev if m.get('fcf_margin') is not None and rev else None, 'FCF margin x revenue')
        put('fcf_ttm', m['fcf_yield'] * mc if m.get('fcf_yield') is not None and mc else None, 'FCF yield x market cap')
    fcf, ev = m.get('fcf_ttm'), m.get('ev')
    put('fcf_margin', _div(fcf, rev) if rev and rev > 0 else None, 'FCF / revenue')
    put('fcf_yield', _div(fcf, mc) if mc and mc > 0 else None, 'FCF / market cap')
    put('ev_to_fcf', ev / fcf if ev and ev > 0 and fcf and fcf > 0 else None, 'EV / FCF')
    put('ev_to_sales', ev / rev if ev and ev > 0 and rev and rev > 0 else None, 'EV / revenue')
    put('sbc_ttm', m['sbc_to_revenue'] * rev if m.get('sbc_to_revenue') is not None and rev else None, 'SBC / revenue x revenue')
    if fcf is not None and m.get('sbc_ttm') is not None:
        put('owner_fcf', fcf - m['sbc_ttm'], 'FCF - SBC')
    nd, eb = m.get('net_debt'), m.get('ebitda')
    if nd is not None and eb is not None and eb > 0 and nd > 0:
        put('net_debt_to_ebitda', nd / eb, 'net debt / EBITDA')
    put('growth_level', m.get('revenue_cagr_3y'), 'revenue CAGR 3y')
    put('growth_level', m.get('revenue_growth'), 'latest revenue growth (no 3y CAGR)')
    gl, fm = m.get('growth_level'), m.get('fcf_margin')
    if m.get('revenue_growth') is not None and fm is not None:
        put('rule_of_40', m['revenue_growth'] + fm, 'revenue growth + FCF margin')
        if m['rule_of_40'] > .05 and m.get('ev_to_sales'):
            put('ev_sales_per_r40', m['ev_to_sales'] / (m['rule_of_40'] * 100), 'EV/sales / (Rule-of-40 in points)')
    if gl is not None:
        put('per_share_growth', gl - (m.get('dilution_yoy') or 0), 'growth level - share count growth')
    if fcf is not None and fcf < 0 and nd is not None and nd < 0:
        put('runway_years', -nd / -fcf, 'net cash / annual FCF burn')
    if m.get('operating_margin') is not None and m.get('revenue_growth') is not None:
        tr = m.get('operating_margin_trend')
        put('stage', _stage({'op_margin': m['operating_margin'], 'g_ttm': m['revenue_growth'],
                             'op_margin_1y': m['operating_margin'] - tr if tr is not None else None}), 'inflection.stage')


def _percentiles(ctx, o):
    """Mid-rank percentile (direction-adjusted: 1 = best) within this scan's supported universe and within sector."""
    pct, spct = {s: {} for s in ctx}, {s: {} for s in ctx}
    for k, better in PCT_METRICS.items():
        vals = {s: c['m'][k] for s, c in ctx.items() if isinstance(c['m'].get(k), (int, float)) and not isinstance(c['m'].get(k), bool)}

        def rank(pool, v):            # pool sorted ascending
            lo = bisect_left(pool, v)
            p = (lo + .5 * (bisect_right(pool, v) - lo)) / len(pool)
            return round(p if better == 'higher' else 1 - p, 3)
        if len(vals) >= o['min_percentile_n']:
            pool = sorted(vals.values())
            for s, v in vals.items():
                pct[s][k] = rank(pool, v)
        if k in SECTOR_RELATIVE or k == 'growth_level':
            by = {}
            for s, v in vals.items():
                by.setdefault(ctx[s]['row'].get('sector'), []).append(v)
            by = {sec: sorted(v) for sec, v in by.items()}
            for s, v in vals.items():
                peers = by[ctx[s]['row'].get('sector')]
                if ctx[s]['row'].get('sector') and len(peers) >= o['min_sector_peers']:
                    spct[s][k] = rank(peers, v)
    return pct, spct


# ----------------------------------------------------------------- dimensions

def _sig(v, a):
    if v is None:
        return None
    s, w, better = a
    if better == 'higher':
        return 1 if v >= s else (-1 if v < w else 0)
    return 1 if v <= s else (-1 if v > w else 0)


def _level(signals, min_known=2):
    """Balance of signals, so one miss cannot flip a level: strong needs net-positive share >= 0.4 and two positives
    (e.g. four supporting signals and one against is strong; two against out of five is adequate)."""
    k = [s for s in signals if s is not None]
    if len(k) < min_known:
        return 'unknown'
    pos, neg = k.count(1), k.count(-1)
    bal = (pos - neg) / len(k)
    if bal >= .4 and pos >= min(2, len(k)):
        return 'strong'
    return 'weak' if bal < -.34 else 'adequate'


def _inp(c, key, value, signal, metric=None, note=None):
    metric = metric or key
    d = {'value': value, 'signal': signal, 'basis': c['b'].get(metric)}
    a = c['anchors'].get(metric)
    if a:
        d['anchor'] = {'strong': a[0], 'weak': a[1], 'better': a[2]}
    if c['pct'].get(metric) is not None:
        d['percentile'] = c['pct'][metric]
    if c['spct'].get(metric) is not None:
        d['sector_percentile'] = c['spct'][metric]
    if note:
        d['note'] = note
    return d


def _dim(inputs, min_known=2, extra_reasons=()):
    lvl = _level([v['signal'] for v in inputs.values()], min_known)
    for k, v in inputs.items():
        if v['value'] is not None or v['signal'] is not None:
            lab, kind = LABELS.get(k, (k, 'n'))
            v['reason'] = f"{lab} {_fmt(v['value'], kind)}: {WORD[v['signal']]}" + (f" ({v['note']})" if v.get('note') else '')
    reasons = [v['reason'] for v in inputs.values() if v.get('reason')]
    ps = [v['percentile'] for v in inputs.values() if v.get('percentile') is not None]
    sig = [v['signal'] for v in inputs.values()]
    return {'level': lvl, 'reasons': reasons + list(extra_reasons), 'inputs': inputs,
            'percentile': round(median(ps), 3) if ps else None,
            'signals': {'positive': sig.count(1), 'neutral': sig.count(0), 'negative': sig.count(-1), 'unknown': sig.count(None)}}


def _business_quality(c):
    m, A = c['m'], c['anchors']
    roic_s = _sig(m.get('roic'), A['roic'])
    gm_s, om_s = _sig(m.get('gross_margin'), A['gross_margin']), _sig(m.get('operating_margin'), A['operating_margin'])
    note = None
    if gm_s is None and om_s is None:
        ml = None
    elif om_s == 1 or (gm_s == 1 and om_s in (0, None)):
        ml = 1
    elif (gm_s == -1 and om_s in (-1, None)) or (om_s == -1 and gm_s in (0, None)):
        ml = -1
    else:
        ml = 0
    if ml == -1:
        sp = max(c['spct'].get('gross_margin', 0), c['spct'].get('operating_margin', 0))
        if roic_s == 1:
            ml, note = 0, 'low margins offset by high capital turnover (ROIC strong)'
        elif sp >= .75:
            ml, note = 0, 'low in absolute terms but top quartile of its sector in this universe'
    inputs = {'roic': _inp(c, 'roic', m.get('roic'), roic_s,
                           note='very high: small or negative invested capital' if (m.get('roic') or 0) > 1 else None),
              'margin_level': _inp(c, 'margin_level', m.get('gross_margin') if m.get('gross_margin') is not None else m.get('operating_margin'),
                                   ml, metric='gross_margin', note=(note or '') + ('; ' if note else '') + f"operating margin {_fmt(m.get('operating_margin'))}"),
              'gross_margin_change': _inp(c, 'gross_margin_change', m.get('gross_margin_change'), _sig(m.get('gross_margin_change'), A['gross_margin_change']),
                                          note='margin stability, a pricing-power hint')}
    conv, fs = m.get('fcf_conversion'), None
    if conv is not None:
        fs = _sig(conv, A['fcf_conversion'])
    elif m.get('fcf_margin') is not None and (m.get('net_margin') is None or m['net_margin'] <= 0):
        fs = 0 if m['fcf_margin'] > 0 else -1
    inputs['fcf_conversion'] = _inp(c, 'fcf_conversion', conv, fs,
                                    note=None if conv is not None or fs is None else f"net loss; FCF margin {_fmt(m.get('fcf_margin'))}")
    inputs['piotroski'] = _inp(c, 'piotroski', m.get('piotroski'), _sig(m.get('piotroski'), A['piotroski']))
    return _dim(inputs)


def _consistency(series):
    s = [x for x in series or [] if x is not None]
    if len(s) < 3:
        return None
    neg = sum(1 for x in s if x < 0)
    if neg == 0 and max(s) - min(s) <= .15:
        return 1
    return -1 if neg >= 2 or min(s) < -.10 else 0


def _growth_quality(c):
    m, A = c['m'], c['anchors']
    acc, acc_s, note = m.get('acceleration'), _sig(m.get('acceleration'), A['acceleration']), None
    if acc_s == 1 and m.get('acceleration_streak') is not None and m['acceleration_streak'] < 2:
        acc_s, note = 0, 'needs >= 2 consecutive improving quarters; one quarter is not an inflection'
    org = None
    if m.get('acquisition_driven') is True:
        org = -1
    elif m.get('acquisitions_pct_revenue') is not None:
        org = 1 if m['acquisitions_pct_revenue'] < .02 else (-1 if m['acquisitions_pct_revenue'] > .10 else 0)
    elif m.get('acquisition_driven') is False:
        org = 0
    inputs = {'growth_level': _inp(c, 'growth_level', m.get('growth_level'), _sig(m.get('growth_level'), A['growth_level'])),
              'consistency': _inp(c, 'consistency', m.get('revenue_growth_series'), _consistency(m.get('revenue_growth_series')),
                                  metric='revenue_growth_series'),
              'acceleration': _inp(c, 'acceleration', acc, acc_s, note=note),
              'operating_margin_trend': _inp(c, 'operating_margin_trend', m.get('operating_margin_trend'),
                                             _sig(m.get('operating_margin_trend'), A['operating_margin_trend']),
                                             note='does growth bring operating leverage?'),
              'dilution_yoy': _inp(c, 'dilution_yoy', m.get('dilution_yoy'), _sig(m.get('dilution_yoy'), A['dilution_yoy']),
                                   note='per-share growth leaks when shares grow'),
              'organic': _inp(c, 'organic', m.get('acquisitions_pct_revenue', m.get('goodwill_jump_to_revenue')), org,
                              metric='acquisitions_pct_revenue', note='acquired growth is not organic' if org == -1 else None)}
    d = _dim(inputs)
    if d['level'] == 'strong' and inputs['growth_level']['signal'] != 1:
        d['level'] = 'adequate'
        d['reasons'].append('capped at adequate: strong growth quality needs growth at or above the level anchor')
    return d


def _financial_strength(c):
    m, A = c['m'], c['anchors']
    nd, eb = m.get('net_debt'), m.get('ebitda')
    if nd is not None and nd <= 0:
        lev, note = 1, 'net cash'
    elif nd is not None and eb is not None and eb <= 0:
        lev, note = -1, 'net debt with negative EBITDA'
    else:
        lev, note = _sig(m.get('net_debt_to_ebitda'), A['net_debt_to_ebitda']), None
    cov = m.get('interest_coverage')
    cov_s = None if (nd is not None and nd <= 0 and not cov) else _sig(cov, A['interest_coverage'])
    zone = m.get('altman_zone')
    inputs = {'net_debt_to_ebitda': _inp(c, 'net_debt_to_ebitda', m.get('net_debt_to_ebitda'), lev, note=note),
              'interest_coverage': _inp(c, 'interest_coverage', cov, cov_s),
              'altman': _inp(c, 'altman', zone, {'safe': 1, 'grey': 0, 'distress': -1}.get(zone), metric='altman_zone',
                             note='weak for asset-light buyback firms with negative retained earnings' if zone == 'distress' else None),
              'fcf_margin': _inp(c, 'fcf_margin', m.get('fcf_margin'), _sig(m.get('fcf_margin'), A['fcf_margin']))}
    if m.get('runway_years') is not None or ((m.get('fcf_ttm') or 0) < 0 and nd is not None and nd >= 0):
        inputs['runway_years'] = _inp(c, 'runway_years', m.get('runway_years'),
                                      _sig(m.get('runway_years'), A['runway_years']) if m.get('runway_years') is not None else -1,
                                      note=None if m.get('runway_years') is not None else 'burning cash without net cash: funding needed')
    return _dim(inputs)


def _valuation(c):
    m, A = c['m'], c['anchors']
    inputs = {'ev_to_fcf': _inp(c, 'ev_to_fcf', m.get('ev_to_fcf'), _sig(m.get('ev_to_fcf'), A['ev_to_fcf']),
                                note='FCF <= 0: not meaningful' if m.get('ev_to_fcf') is None and (m.get('fcf_ttm') or 0) <= 0 and m.get('fcf_ttm') is not None else None),
              'pe': _inp(c, 'pe', m.get('pe'), _sig(m.get('pe'), A['pe'])),
              'ev_sales_per_r40': _inp(c, 'ev_sales_per_r40', m.get('ev_sales_per_r40'), _sig(m.get('ev_sales_per_r40'), A['ev_sales_per_r40']),
                                       note=f"EV/sales {_fmt(m.get('ev_to_sales'), 'x')} vs growth + FCF margin {_fmt(m.get('rule_of_40'))}")}
    d = _dim(inputs, min_known=1)
    d['reasons'].append('cheapness alone is not a selection signal (docs/GAP_TEST_RESULTS.md H2)')
    return d


def _fade_multiple(g, o):
    r, gt = o['discount_rate'], o['terminal_growth']
    f, pv = 1., 0.
    for t, gy in enumerate(growth_path(g, gt, o['fade_high_years'], o['fade_years']), 1):
        f *= 1 + gy
        pv += f / (1 + r) ** t
    return pv + f * (1 + gt) / (r - gt) / (1 + r) ** o['fade_years']


def implied_growth(multiple, options=None):
    """Growth g, held fade_high_years then fading linearly to terminal growth by fade_years
    (expectations.growth_path), at which the PV of owner FCF per $1 of current FCF equals `multiple`.
    Returns (g, status): solved | below_range (price implies a decline beyond -20%/yr) | above_range (> 80%/yr)."""
    o = _options(options)
    lo, hi = -.20, .80
    if multiple <= _fade_multiple(lo, o):
        return lo, 'below_range'
    if multiple >= _fade_multiple(hi, o):
        return hi, 'above_range'
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _fade_multiple(mid, o) < multiple else (lo, mid)
    return (lo + hi) / 2, 'solved'


def _reference_growth(m):
    if m.get('consensus_growth_2y') is not None:
        return m['consensus_growth_2y'], 'consensus 2y revenue CAGR (vendor estimate)'
    if m.get('revenue_cagr_3y') is not None:
        return m['revenue_cagr_3y'], 'demonstrated 3y revenue CAGR'
    if m.get('revenue_growth') is not None:
        return m['revenue_growth'], 'latest revenue growth'
    return None, None


def _priced_in(c, o):
    m = c['m']
    base, basis = (m.get('owner_fcf'), 'owner FCF (FCF - SBC)') if m.get('owner_fcf') is not None else (m.get('fcf_ttm'), 'FCF (SBC unknown: overstated)')
    ev = m.get('ev')
    ref, ref_basis = _reference_growth(m)
    out = {'level': 'unknown', 'class': 'not_assessable', 'implied_growth': None, 'status': None, 'multiple': None, 'multiple_basis': basis,
           'reference_growth': ref, 'reference_basis': ref_basis, 'demonstrated_growth': m.get('revenue_cagr_3y', m.get('revenue_growth')),
           'consensus_growth': m.get('consensus_growth_2y'), 'gap': None,
           'model': {'discount_rate': o['discount_rate'], 'terminal_growth': o['terminal_growth'], 'high_years': o['fade_high_years'],
                     'years': o['fade_years'], 'path': 'expectations.growth_path'}, 'reasons': []}
    if ev is None or base is None:
        out['reasons'].append('EV or FCF unknown')
        return out
    if ev <= 0 or base <= 0:
        out['class'] = 'not_assessable_negative_fcf' if base <= 0 else 'not_assessable_negative_ev'
        out['reasons'].append('owner FCF <= 0: a cash-flow multiple cannot say what the price requires; deep analysis must run '
                              'expectations.expectation_gap with company drivers' if base <= 0 else 'EV <= 0 (net cash above market cap)')
        return out
    mult = ev / base
    g, status = implied_growth(mult, o)
    out.update({'multiple': mult, 'implied_growth': g, 'status': status})
    tol = o['priced_in_tolerance']
    if g >= o['perfection_growth'] or status == 'above_range':
        out.update(level='weak', **{'class': 'priced_for_perfection'})
        out['reasons'].append(f"EV/{basis} {mult:.0f}x implies ~{_fmt(g)}/yr for {o['fade_high_years']} years: priced for perfection "
                              '(implied >= 30% halved ~3-4x as often in the 2018-2025 holdout; a crash-risk measure, not a return signal)')
    elif ref is None:
        out.update(level='unknown', **{'class': 'no_reference_growth'})
        out['reasons'].append(f'implied ~{_fmt(g)}/yr; no consensus or demonstrated growth to compare')
    else:
        gap = g - ref
        out['gap'] = gap
        cls = 'price_assumes_less_than_evidence' if gap <= -tol else ('roughly_consistent' if gap <= tol else 'price_assumes_more_than_evidence')
        out.update(level={'price_assumes_less_than_evidence': 'strong', 'roughly_consistent': 'adequate'}.get(cls, 'weak'), **{'class': cls})
        out['reasons'].append(f"EV/{basis} {mult:.0f}x implies ~{_fmt(g)}/yr growth for {o['fade_high_years']} years then fade to "
                              f"{_fmt(o['terminal_growth'])}; {ref_basis} {_fmt(ref)} ({cls.replace('_', ' ')})")
    if m.get('consensus_growth_2y') is not None and m.get('revenue_cagr_3y') is not None and m['consensus_growth_2y'] > m['revenue_cagr_3y'] + .10:
        out['reasons'].append(f"consensus {_fmt(m['consensus_growth_2y'])} far above demonstrated {_fmt(m['revenue_cagr_3y'])}")
    return out


def _expected_return(c, o):
    m, A = c['m'], c['anchors']
    base, note = (m.get('owner_fcf'), None) if m.get('owner_fcf') is not None else (m.get('fcf_ttm'), 'SBC unknown: yield before SBC (overstated)')
    out = {'level': 'unknown', 'value': None, 'status': 'unknown', 'formula': ER_FORMULA, 'components': {}, 'bounded': [], 'reasons': []}
    ev, mc = m.get('ev'), m.get('market_cap')
    g0, g0_basis = _reference_growth(m)
    if base is None or ev is None or ev <= 0 or g0 is None:
        out['reasons'].append('needs EV > 0, FCF and a growth estimate')
        return out
    if base <= 0:
        out.update(status='not_meaningful')
        out['reasons'].append('owner FCF <= 0: a yield-based proxy cannot see a J-curve; underwrite in the pipeline instead')
        return out
    y = base / ev
    lo, hi = o['growth_start_bounds']
    g0b = _clamp(g0, lo, hi)
    if g0b != g0:
        out['bounded'].append(f'start growth {_fmt(g0)} bounded to {_fmt(g0b)}')
    p = 1.
    for gy in growth_path(g0b, o['terminal_growth'], o['consensus_hold_years'], o['fade_years']):
        p *= 1 + gy
    gs = p ** (1 / o['fade_years']) - 1
    slo, shi = o['sustainable_growth_bounds']
    gsb = _clamp(gs, slo, shi)
    if gsb != gs:
        out['bounded'].append(f'sustainable growth {_fmt(gs, "pct1")} bounded to {_fmt(gsb, "pct1")}')
    sbc_dil = _div(m.get('sbc_ttm'), mc) if mc and mc > 0 else None
    dil = m.get('dilution_yoy')
    excess = max(0., dil - (sbc_dil or 0)) if dil is not None else None
    er = y + gsb - (excess or 0)
    out.update(value=er, status='computed', level={1: 'strong', 0: 'adequate', -1: 'weak'}[_sig(er, A['expected_return_proxy'])],
               components={'owner_fcf_yield_on_ev': y, 'growth_start': g0b, 'growth_start_basis': g0_basis, 'sustainable_growth': gsb,
                           'sbc_implied_dilution': sbc_dil, 'share_count_growth': dil, 'excess_dilution': excess})
    out['reasons'].append(f'{_fmt(y, "pct1")} owner yield + {_fmt(gsb, "pct1")} sustainable growth ({g0_basis} {_fmt(g0)} fading)'
                          + (f' - {_fmt(excess, "pct1")} excess dilution' if excess else '') + f' = {_fmt(er, "pct1")}/yr (rough)')
    if note:
        out['reasons'].append(note)
    if dil is None:
        out['reasons'].append('share count change unknown: dilution not charged')
    out['percentile'] = c['pct'].get('expected_return_proxy')
    return out


def _risk(c, o, pi):
    m, flags = c['m'], []
    add = lambda code, sev, msg: flags.append({'code': code, 'severity': sev, 'message': msg})
    nde, cov, zone = m.get('net_debt_to_ebitda'), m.get('interest_coverage'), m.get('altman_zone')
    nd, fcf, run = m.get('net_debt'), m.get('fcf_ttm'), m.get('runway_years')
    if nde is not None and nde > 3.5:
        add('leverage', 'high' if nde > 5 else 'medium', f'net debt / EBITDA {nde:.1f}x')
    if nd is not None and nd > 0 and m.get('ebitda') is not None and m['ebitda'] <= 0:
        add('leverage', 'high', 'net debt with negative EBITDA')
    if cov is not None and cov < 2 and not (nd is not None and nd <= 0):
        add('interest_coverage', 'high' if cov < 1.5 else 'medium', f'interest coverage {cov:.1f}x')
    burn_short = fcf is not None and fcf < 0 and (run is None or run < 1)
    if zone == 'distress':
        hard = (cov is not None and cov < 1.5 and not (nd is not None and nd <= 0)) or burn_short or (nde is not None and nde > 6)
        add('distress', 'high' if hard else 'medium', 'Altman distress zone' + (' with weak coverage, cash burn or heavy leverage' if hard
                                                                              else ' (often a false alarm for buyback-heavy asset-light firms)'))
    if fcf is not None and fcf < 0:
        if run is None:
            add('funding', 'high' if nd is not None and nd > 0 else 'medium', 'negative FCF' + (' with net debt' if nd is not None and nd > 0 else ', runway unknown'))
        elif run < 2:
            add('funding', 'high' if run < 1 else 'medium', f'negative FCF, cash runway {run:.1f} years')
    d = m.get('dilution_yoy')
    if d is not None and d > .05:
        add('dilution', 'high' if d > .10 else 'medium', f'share count +{d * 100:.0f}%/yr')
    if pi.get('class') == 'priced_for_perfection':
        add('priced_for_perfection', 'medium', 'price requires rare growth (crash-risk flag)')
    v = m.get('volatility_1y')
    if v is not None and v > c['anchors']['volatility_1y'][1]:
        add('volatility', 'medium', f'annualized volatility {v * 100:.0f}%')
    if m.get('acquisition_driven') is True:
        add('acquired_growth', 'low', 'growth may be bought, not organic')
    known = any(m.get(k) is not None for k in ('net_debt', 'net_debt_to_ebitda', 'interest_coverage', 'altman_zone'))
    sev = {f['severity'] for f in flags}
    lvl = 'weak' if 'high' in sev else ('adequate' if 'medium' in sev else ('strong' if known else 'unknown'))
    return {'level': lvl, 'flags': flags, 'reasons': [f"{f['severity']}: {f['message']}" for f in flags] or (['no material flag'] if known else ['no leverage data']),
            'disqualifying': [f['code'] for f in flags if f['code'] == 'distress' and f['severity'] == 'high']}


def _data_uncertainty(c, scan_as_of):
    row, m = c['row'], c['m']
    have = [k for k in KEY_METRICS if m.get(k) is not None]
    cov = len(have) / len(KEY_METRICS)
    grade = row['data_grade']
    lvl = 'weak' if cov < .5 else ('strong' if grade == 'verified' and cov >= .85 else 'adequate')
    reasons = [f'{len(have)} of {len(KEY_METRICS)} key metrics present', f"data grade {grade}: {GRADES[grade]}"]
    missing = [k for k in KEY_METRICS if m.get(k) is None]
    if missing:
        reasons.append('missing: ' + ', '.join(missing))
    if (row.get('source') or {}).get('dataqa_status') not in (None, 'ok'):
        reasons.append(f"dataqa status {row['source']['dataqa_status']}: some critical fields single-source, stale or missing")
    if row.get('errors'):
        reasons.append('vendor calls failed: ' + ', '.join(f'{k} {v}' for k, v in sorted(row['errors'].items())))
    if row.get('as_of') and scan_as_of:
        age = (_date(scan_as_of) - _date(row['as_of'])).days
        if age > 120:
            lvl = 'weak' if age > 400 else ('adequate' if lvl == 'strong' else lvl)
            reasons.append(f'data {age} days older than the scan date')
    return {'level': lvl, 'coverage': round(cov, 3), 'missing': missing, 'reasons': reasons}


# ----------------------------------------------------------------- lenses

def _neg(d):
    bad = [v['reason'] for v in d.get('inputs', {}).values() if v['signal'] == -1 and v.get('reason')]
    return '; '.join(bad[:3]) or ('insufficient data' if d['level'] == 'unknown' else 'mixed signals')


def _quality_phrase(c):
    m = c['m']
    return (f"ROIC {_fmt(m.get('roic'))}, gross margin {_fmt(m.get('gross_margin'))}, operating margin {_fmt(m.get('operating_margin'))}"
            + (f", FCF/net income {_fmt(m.get('fcf_conversion'), 'x2')}" if m.get('fcf_conversion') is not None else ''))


def _price_phrase(c):
    m, pi, er = c['m'], c['dims']['priced_in'], c['dims']['expected_return_proxy']
    s = f"EV/FCF {_fmt(m.get('ev_to_fcf'), 'x')}, P/E {_fmt(m.get('pe'), 'x')}"
    if pi['implied_growth'] is not None:
        s += f"; the price implies ~{_fmt(pi['implied_growth'])}/yr growth" + (f" vs {pi['reference_basis']} {_fmt(pi['reference_growth'])}" if pi['reference_growth'] is not None else '')
    if er['value'] is not None:
        s += f"; return proxy {_fmt(er['value'], 'pct1')}/yr (rough)"
    return s


PI_RANK = {'strong': 0, 'adequate': 1, 'unknown': 2, 'weak': 3}


def _improvements(c, o):
    m, d = c['m'], c['dims']
    out = []
    acc = m.get('acceleration')
    if acc is not None and acc >= .03 and (m.get('acceleration_streak') is None or m['acceleration_streak'] >= 2):
        out.append(('acceleration', False, f'growth accelerating {_fmt(acc, "pts")}'))
    if (m.get('operating_margin_trend') or 0) >= .02:
        out.append(('margin_expansion', True, f"operating margin {_fmt(m['operating_margin_trend'], 'pts')}"))
    if (m.get('fcf_margin_change') or 0) >= .03:
        out.append(('fcf_improving', True, f"FCF margin {_fmt(m['fcf_margin_change'], 'pts')}"))
    if (m.get('piotroski') or 0) >= 8:
        out.append(('piotroski', False, f"Piotroski {m['piotroski']:.0f}/9"))
    if m.get('stage') == 'turning_profitable' or m.get('stage_pit') == 'turning_profitable':
        out.append(('turning_profitable', True, 'operating income turned positive'))
    dd = m.get('drawdown_from_high')
    over = (dd is not None and dd <= o['drawdown_overreaction'] and d['business_quality']['level'] in ('strong', 'adequate')
            and d['growth_quality']['level'] in ('strong', 'adequate'))
    return out, over


def _lens(name, c, o):
    """-> (in_scope, conditions [(name, ok, detail)], ordering [(label, value, asc|desc)], why, tags)."""
    m, d = c['m'], c['dims']
    bq, gq, fs, val, pi, er = (d[k]['level'] for k in ('business_quality', 'growth_quality', 'financial_strength', 'valuation', 'priced_in',
                                                         'expected_return_proxy'))
    erv, gl = d['expected_return_proxy']['value'], m.get('growth_level')
    fs_c = ('financial_strength', fs != 'weak', f'financial strength weak: {_neg(d["financial_strength"])}')
    if name == 'quality_compounder':
        scope = gl is not None and gl >= c['anchors']['growth_level'][0]
        ppf = d['priced_in']['class'] == 'priced_for_perfection'
        conds = [('business_quality', bq in ('strong', 'adequate'), f'fast growth without demonstrated quality ({bq}): {_neg(d["business_quality"])}'),
                 ('growth_quality', gq == 'strong', f'growth quality {gq}, needs strong: {_neg(d["growth_quality"])}'), fs_c]
        order = [('quality tier (0 strong, 1 adequate)', 0 if bq == 'strong' else 1, 'asc'), ('priced for perfection (0 no, 1 yes)', int(ppf), 'asc'),
                 ('expected-return proxy', erv, 'desc')]
        why = (f"Revenue grows {_fmt(gl)}/yr with {gq} growth quality and {bq} business quality ({_quality_phrase(c)}). Price check: {_price_phrase(c)}"
               + ('. Priced for perfection: high growth already assumed.' if ppf else '.'))
        return scope, conds, order, why, (['priced_for_perfection'] if ppf else [])
    if name == 'quality_fair_price':
        conds = [('valuation', val in ('strong', 'adequate'), f'quality but expensive: {_neg(d["valuation"])}'),
                 ('priced_in', pi in ('strong', 'adequate'), f"price assumption {d['priced_in']['class'].replace('_', ' ')}: "
                  + '; '.join(d['priced_in']['reasons'][:1])),
                 ('trajectory', gq in ('strong', 'adequate'), f'growth quality {gq}: {_neg(d["growth_quality"])} (cannot rule out deterioration)'),
                 fs_c,
                 ('expected_return', er in ('strong', 'adequate'), f"return proxy {er}: {'; '.join(d['expected_return_proxy']['reasons'][:1])}")]
        order = [('expected-return proxy', erv, 'desc'), ('business-quality percentile', d['business_quality']['percentile'], 'desc')]
        why = f"Strong business ({_quality_phrase(c)}) with {gq} growth ({_fmt(gl)}/yr) at a price rated {val}: {_price_phrase(c)}."
        return bq == 'strong', conds, order, why, []
    if name == 'early_commercial_growth':
        om, nm, fm = m.get('operating_margin'), m.get('net_margin'), m.get('fcf_margin')
        early = (om is not None and om < .10) or (nm is not None and nm <= 0) or (fm is not None and fm < .05)
        g = max(x for x in (m.get('revenue_growth'), gl, -1) if x is not None)
        scope = early and g >= o['early_growth_min']
        proofs = []
        if (m.get('gross_margin') or 0) >= .40 or (m.get('gross_margin_change') or 0) >= .02:
            proofs.append(f"unit economics: gross margin {_fmt(m.get('gross_margin'))} ({_fmt(m.get('gross_margin_change'), 'pts')})")
        if (m.get('operating_margin_trend') or 0) >= .02 or ((m.get('incremental_operating_margin') or -9) > (om or 0) + .05):
            proofs.append(f"operating leverage: operating margin {_fmt(m.get('operating_margin_trend'), 'pts')}")
        if (m.get('fcf_margin_change') or 0) >= .03 or (fm is not None and fm >= 0):
            proofs.append(f"cash trajectory: FCF margin {_fmt(fm)} ({_fmt(m.get('fcf_margin_change'), 'pts')})")
        ser = [x for x in m.get('revenue_growth_series') or [] if x is not None]
        if _consistency(ser) == 1 or (len(ser) >= 2 and min(ser) > .15):
            proofs.append(f"repeat growth: {_fmt(ser, 'list')}")
        dil, run = m.get('dilution_yoy'), m.get('runway_years')
        funded = (fm is not None and fm >= 0) or (run is not None and run >= 2)
        conds = [('commercial_proof', len(proofs) >= 3, f'growth without enough commercial proof ({len(proofs)} of 4: {"; ".join(proofs) or "none"}); '
                  f"gross margin {_fmt(m.get('gross_margin'))}, operating margin trend {_fmt(m.get('operating_margin_trend'), 'pts')}, FCF margin {_fmt(fm)}"),
                 ('dilution', dil is None or dil <= .10, f'share count +{_fmt(dil)}/yr: growth paid for with current holders\' ownership'),
                 ('no_distress', not d['risk']['disqualifying'] and not any(f['code'] == 'distress' for f in d['risk']['flags']), 'distress flag')]
        order = [('commercial-proof count', len(proofs), 'desc'), ('funding (0 funded, 1 conditional)', 0 if funded else 1, 'asc'),
                 ('per-share growth', m.get('per_share_growth'), 'desc')]
        why = (f"Grows {_fmt(g)}/yr at an early stage (operating margin {_fmt(om)}) with commercial proof: {'; '.join(proofs)}. "
               + ('Funded by own cash flow or runway.' if funded else f"Funding-conditional (runway {_fmt(run, 'yrs')}, dilution {_fmt(dil)}/yr).")
               + ' Paid adoption and cohorts are not in screening data: verify.')
        return scope, conds, order, why, ['funded' if funded else 'funding_conditional']
    if name == 'mispriced_turning':
        imp, over = _improvements(c, o)
        scope = bool(imp) or (m.get('drawdown_from_high') is not None and m['drawdown_from_high'] <= o['drawdown_overreaction'])
        evid = (len(imp) >= 2 and any(p for _, p, _ in imp)) or over
        price_ok = pi == 'strong' or (pi == 'unknown' and val == 'strong') or (over and pi != 'weak')
        dil = m.get('dilution_yoy')
        conds = [('improvement_evidence', evid, f"one improvement signal is not a turn ({'; '.join(t for _, _, t in imp) or 'none'})"
                  + ('' if m.get('drawdown_from_high') is None else f"; drawdown {_fmt(m['drawdown_from_high'])} but quality {bq}/growth {gq}")),
                 ('not_priced_in', price_ok, f"the price does not lag the improvement: priced_in {pi} ({d['priced_in']['class'].replace('_', ' ')}), valuation {val}"),
                 ('dilution', dil is None or dil <= .08, f'share count +{_fmt(dil)}/yr consumes per-share gains'), fs_c]
        order = [('improvement-signal count', len(imp) + int(over), 'desc'), ('priced_in rank (0 strong .. 3 weak)', PI_RANK[pi], 'asc'),
                 ('expected-return proxy', erv, 'desc')]
        why = ((f"Improving: {'; '.join(t for _, _, t in imp)}. " if imp else '')
               + (f"Down {_fmt(-m['drawdown_from_high'])} from its high with {bq} quality and {gq} growth: possible overreaction, test temporary vs structural. " if over else '')
               + f"Price check: {_price_phrase(c)}.")
        return scope, conds, order, why, (['overreaction_candidate'] if over else []) + [k for k, _, _ in imp]
    if name == 'structural_tailwind':
        themes = (o['themes'] or {}).get(c['row']['symbol']) or []
        ind = c['row'].get('industry') in (o['structural_industries'] or [])
        sp = c['spct'].get('growth_level')
        footprint = sp is not None and sp >= .75 and (m.get('gross_margin_change') is None or m['gross_margin_change'] >= -.01)
        scope = bool(themes) or ind or footprint
        cg = m.get('revenue_cagr_3y', gl)
        conds = [('durable_growth', cg is not None and cg >= .08 and _consistency(m.get('revenue_growth_series')) != -1,
                  f'growth {_fmt(cg)} 3y or repeated declines: no durable footprint yet'),
                 ('business_quality', bq != 'weak', f'economics weak: {_neg(d["business_quality"])}'),
                 ('not_already_priced', pi != 'weak', 'potential appears already priced: ' + '; '.join(d['priced_in']['reasons'][:1])), fs_c]
        order = [('priced_in rank (0 strong .. 3 weak)', PI_RANK[pi], 'asc'), ('expected-return proxy', erv, 'desc'), ('growth level', gl, 'desc')]
        src = ('analyst theme: ' + ', '.join(t if isinstance(t, str) else t.get('theme', '?') for t in themes) if themes else
               ('structural industry (analyst list)' if ind else f'improving competitive position: growth in the top quartile of its sector (percentile {sp})'))
        why = (f"{src} (a hypothesis, not evidence). Footprint: {_fmt(cg)}/yr growth, {bq} business quality. "
               f"Already priced? {_price_phrase(c)}" + (' (price check unavailable: FCF <= 0)' if pi == 'unknown' else '') + '.')
        return scope, conds, order, why, ['analyst_theme' if themes or ind else 'numeric_footprint']
    raise ValueError(f'unknown lens {name}')


def _sortkey(order, sym):
    return tuple((1, 0) if v is None else (0, v if way == 'asc' else -v) for _, v, way in order) + (sym,)


# ----------------------------------------------------------------- scan

def _options(options):
    options = dict(options or {})
    bad = set(options) - set(DEFAULTS)
    if bad:
        raise ValueError(f'unknown screen options: {sorted(bad)}')
    o = {**DEFAULTS, **{k: v for k, v in options.items() if k != 'anchors'}}
    for k in ('top_n', 'sector_cap'):
        if o[k] is not None and (isinstance(o[k], bool) or not isinstance(o[k], int) or o[k] < 1):
            raise ValueError(f'{k} must be a positive integer' + (' or None' if k == 'sector_cap' else ''))
    a = {**ANCHORS, **(options.get('anchors') or {})}
    for k, v in a.items():
        if len(v) != 3 or v[2] not in ('higher', 'lower'):
            raise ValueError(f'anchor {k} must be (strong, weak, higher|lower)')
    o['anchors'] = {k: [v[0], v[1], v[2]] for k, v in sorted(a.items())}
    if o['terminal_growth'] >= o['discount_rate']:
        raise ValueError('terminal_growth must be below discount_rate')
    return o


def _unsupported(row, o):
    ind = row.get('industry') or ''
    lst = o['unsupported_industries'] if o['unsupported_industries'] is not None else UNSUPPORTED_INDUSTRIES
    if row['metrics'].get('financial_statements') or ind in lst or 'REIT' in ind:
        return ('bank/insurer/REIT-type economics: FCF, EV and gross-margin metrics do not describe them; needs a sector-specific '
                'screen (book value/ROE, FFO). Not relabelled to force a generic model.')
    return None


def _clean_rows(rows):
    out = []
    for r in rows:
        if 'metrics' not in r:
            if r.get('data_grade') == 'sec_frames':
                r = rows_from_sec_frames([r])[0]
            else:
                raise ValueError(f"row {r.get('symbol')}: no 'metrics'; build rows with a screen.rows_from_* adapter")
        if not r.get('symbol'):
            raise ValueError('row without symbol')
        if r.get('data_grade') not in GRADES:
            raise ValueError(f"row {r['symbol']}: data_grade must be one of {GRADE_ORDER}")
        m = {k: (v if isinstance(v, (bool, str)) or v is None else ([_f(x) for x in v] if isinstance(v, list) else _f(v)))
             for k, v in (r.get('metrics') or {}).items()}
        m = {k: v for k, v in m.items() if v is not None}
        status, reason = r.get('status') or 'ok', r.get('no_data_reason')
        if status == 'ok' and not any(k in m for k in CORE_METRICS):
            status, reason = 'no_data', 'no usable metrics after removing nulls and non-finite values'
        out.append({**r, 'metrics': m, 'basis': dict(r.get('basis') or {}), 'errors': dict(r.get('errors') or {}),
                    'status': status, 'no_data_reason': reason})
    return out


def scan(rows, options=None):
    """Universe rows (screen.rows_from_* adapters) -> dimensions, lens candidates, shortlist, exclusions, notes.

    Deterministic and order-independent (ties break on symbol). options: see DEFAULTS; anchors override ANCHORS.
    The output is a research agenda, never a recommendation."""
    o = _options(options)
    rows = _clean_rows(rows)
    best, notes = {}, []
    for r in sorted(rows, key=lambda r: (r['symbol'], r['status'] != 'ok', GRADE_ORDER.index(r['data_grade']))):
        if r['symbol'] in best:
            notes.append(f"duplicate rows for {r['symbol']}: kept {best[r['symbol']]['data_grade']} ({best[r['symbol']]['status']}), dropped {r['data_grade']}")
            continue
        best[r['symbol']] = r
    dates = [r['as_of'] for r in best.values() if r.get('as_of')]
    as_of = o['as_of'] or (max(dates) if dates else None)
    for r in best.values():
        if as_of and r.get('as_of') and _date(r['as_of']) > _date(as_of):
            raise ValueError(f"row {r['symbol']} dated {r['as_of']} after scan as_of {as_of} (look-ahead)")
    no_data = [{'symbol': s, 'reason': r.get('no_data_reason') or 'no usable metrics', 'errors': r['errors'], 'data_grade': r['data_grade']}
               for s, r in sorted(best.items()) if r['status'] != 'ok']
    ctx = {}
    for s, r in sorted(best.items()):
        if r['status'] != 'ok':
            continue
        m, b = dict(r['metrics']), dict(r['basis'])
        _derive(m, b)
        ctx[s] = {'row': r, 'm': m, 'b': b, 'anchors': {k: tuple(v) for k, v in o['anchors'].items()}}
    unsupported = {s: _unsupported(c['row'], o) for s, c in ctx.items()}
    supported = {s: c for s, c in ctx.items() if not unsupported[s]}
    for s, c in ctx.items():          # the return proxy is computed first so its percentile can join the table
        c['pct'], c['spct'] = {}, {}
        c['er'] = _expected_return(c, o)
        if c['er']['value'] is not None:
            c['m']['expected_return_proxy'] = c['er']['value']
    pct, spct = _percentiles(supported, o)
    dims = {}
    for s, c in ctx.items():
        c['pct'], c['spct'] = pct.get(s, {}), spct.get(s, {})
        c['er']['percentile'] = c['pct'].get('expected_return_proxy')
        pi = _priced_in(c, o)
        c['dims'] = {'business_quality': _business_quality(c), 'growth_quality': _growth_quality(c),
                     'financial_strength': _financial_strength(c), 'valuation': _valuation(c), 'expected_return_proxy': c['er'],
                     'priced_in': pi, 'risk': _risk(c, o, pi), 'data_uncertainty': _data_uncertainty(c, as_of)}
        r = c['row']
        dims[s] = {'profile': {'name': r.get('name'), 'sector': r.get('sector'), 'industry': r.get('industry'), 'market_cap': c['m'].get('market_cap'),
                               'data_grade': r['data_grade'], 'source': r.get('source'), 'as_of': r.get('as_of'), 'stage': c['m'].get('stage')},
                   **c['dims'], 'metrics': c['m'], 'basis': c['b']}
    disq = {}
    for s, c in ctx.items():
        if unsupported[s]:
            disq[s] = 'not supported: ' + unsupported[s]
        elif c['dims']['data_uncertainty']['level'] == 'weak':
            disq[s] = 'unreliable data: ' + '; '.join(c['dims']['data_uncertainty']['reasons'][:3])
        elif c['dims']['risk']['disqualifying']:
            disq[s] = 'distress: ' + '; '.join(f['message'] for f in c['dims']['risk']['flags'] if f['severity'] == 'high')
    lenses, near, scoped = {}, {}, {}
    for ln in LENSES:
        passed, nm = [], []
        for s, c in ctx.items():
            if s in disq:
                continue
            scope, conds, order, why, tags = _lens(ln, c, o)
            if not scope:
                continue
            fails = [x for x in conds if not x[1]]
            if not fails:
                passed.append((_sortkey(order, s), s, order, why, tags))
            elif len(fails) == 1:
                nm.append({'symbol': s, 'failed': fails[0][0], 'reason': fails[0][2]})
            else:
                scoped.setdefault(s, []).append((ln, fails))
        passed.sort()
        cand, per = [], {}
        for _, s, order, why, tags in passed:
            sec = ctx[s]['row'].get('sector') or 'unknown'
            if o['sector_cap'] and per.get(sec, 0) >= o['sector_cap']:
                nm.append({'symbol': s, 'failed': 'sector_cap', 'reason': f"sector cap: {per[sec]} higher-ranked {sec} names already in this lens "
                                                                         f"(cap {o['sector_cap']}); qualifies otherwise"})
                continue
            per[sec] = per.get(sec, 0) + 1
            c = ctx[s]
            cand.append({'rank': len(cand) + 1, 'symbol': s, 'name': c['row'].get('name'), 'sector': c['row'].get('sector'), 'why': why,
                         'ordering': {'fields': [{'name': lab, 'value': v, 'order': way} for lab, v, way in order], 'meaning': LENS_RULES[ln]['ordering']},
                         'levels': {k: c['dims'][k]['level'] for k in DIMENSIONS}, 'tags': tags, 'data_grade': c['row']['data_grade'],
                         'risk_flags': [f['code'] for f in c['dims']['risk']['flags']]})
        lenses[ln], near[ln] = cand, sorted(nm, key=lambda x: x['symbol'])
    shortlist, seen = _shortlist(lenses, o)
    for e in shortlist:
        c = ctx[e['symbol']]
        first = sorted(e['lenses'], key=lambda x: x['lens'] != e['selected_by'])
        e['reason'] = ' | '.join(f"{LENS_RULES[x['lens']]['label']} (#{x['rank']}): {x['why']}" for x in first)
        e['check_first'] = _check_first(c, [x['lens'] for x in e['lenses']])
    excluded, ranks, nearest = {}, {}, {}
    for ln in LENSES:
        for cd in lenses[ln]:
            ranks[cd['symbol']] = min(ranks.get(cd['symbol'], (cd['rank'], ln)), (cd['rank'], ln))
        for x in near[ln]:
            nearest.setdefault(x['symbol'], (ln, x))
    for s, c in ctx.items():
        if s in seen:
            continue
        excluded[s] = _exclusion(s, c, disq, ranks.get(s), nearest.get(s), scoped.get(s), o)
    return {'screen_version': SCREEN_VERSION, 'as_of': as_of,
            'universe': {'definition': o['universe'], 'rows': len(best), 'with_data': len(ctx), 'no_data': len(no_data),
                         'not_supported': sum(1 for v in unsupported.values() if v), 'eligible': len(ctx) - len(disq),
                         'data_grades': {g: sum(1 for c in ctx.values() if c['row']['data_grade'] == g) for g in GRADE_ORDER}},
            'dimensions': dims, 'dimension_meanings': MEANINGS, 'lenses': lenses, 'lens_rules': LENS_RULES, 'near_misses': near,
            'shortlist': shortlist, 'excluded': dict(sorted(excluded.items())), 'no_data': no_data,
            'notes': notes + _notes(o, ctx, no_data), 'options': o,
            'policy': {'is_recommendation': False, 'orders_supported': False, 'valuation_gap_used_for_ranking': False,
                       'momentum_used_for_selection': False, 'size_or_growth_preference': False,
                       'single_composite_score': False},
            'input_sha256': F.digest({'rows': [best[s] for s in sorted(best)], 'options': o})}


def _shortlist(lenses, o):
    """'distinct' (default): round-robin over lenses, each contributing up to top_n names not already taken, so a name that
    leads several lenses does not crowd out other opportunity types and no lens is privileged by its position.
    'union': the literal top_n of every lens, deduplicated. Every lens rank of a shortlisted name is recorded."""
    if o['shortlist_mode'] not in ('distinct', 'union'):
        raise ValueError("shortlist_mode must be 'distinct' or 'union'")
    picks, taken, cursor, count = [], set(), {ln: 0 for ln in LENSES}, {ln: 0 for ln in LENSES}
    if o['shortlist_mode'] == 'union':
        for ln in LENSES:
            for cd in lenses[ln][:o['top_n']]:
                if cd['symbol'] not in taken:
                    taken.add(cd['symbol']); picks.append((cd['symbol'], ln))
    else:
        progress = True
        while progress:
            progress = False
            for ln in LENSES:
                cands = lenses[ln]
                while count[ln] < o['top_n'] and cursor[ln] < len(cands) and cands[cursor[ln]]['symbol'] in taken:
                    cursor[ln] += 1
                if count[ln] < o['top_n'] and cursor[ln] < len(cands):
                    s = cands[cursor[ln]]['symbol']
                    taken.add(s); picks.append((s, ln)); count[ln] += 1; progress = True
    shortlist, seen = [], {}
    for s, ln in picks:
        cd = next(c for c in lenses[ln] if c['symbol'] == s)
        seen[s] = {'symbol': s, 'name': cd['name'], 'sector': cd['sector'], 'data_grade': cd['data_grade'], 'selected_by': ln,
                   'lenses': [{'lens': x, 'rank': c['rank'], 'why': c['why']} for x in LENSES for c in lenses[x] if c['symbol'] == s],
                   'levels': cd['levels'], 'risk_flags': cd['risk_flags']}
        shortlist.append(seen[s])
    return shortlist, seen


def _check_first(c, lenses):
    m, d = c['m'], c['dims']
    out = []
    if c['row']['data_grade'] != 'verified':
        out.append(f"Reconcile {c['row']['data_grade']}-grade inputs to filings (dataqa: shares, cash, debt, TTM revenue/CFO) before any price conclusion.")
    out += [LENS_CHECKS[x] for x in lenses]
    codes = {f['code'] for f in d['risk']['flags']}
    if 'priced_for_perfection' in codes:
        out.append('The price requires rare growth: run company-driver expectations (expectations.expectation_gap) and the bear case first.')
    if 'dilution' in codes or (m.get('dilution_yoy') or 0) > .04:
        out.append('Share count path: SBC, issuance, converts and warrants (capital.py) and dilution to current holders.')
    if 'funding' in codes:
        out.append('Funding: runway to the next milestone and the financing the plan needs (finance.funding_path).')
    if 'leverage' in codes or 'interest_coverage' in codes:
        out.append('Debt maturities, covenants and refinancing cost.')
    if 'distress' in codes:
        out.append('Altman distress zone: confirm or dismiss (retained earnings distorted by buybacks?).')
    if m.get('acquisition_driven') or (m.get('acquisitions_pct_revenue') or 0) > .10:
        out.append('Organic versus acquired growth.')
    if d['business_quality']['inputs']['fcf_conversion']['signal'] == -1:
        out.append('Earnings quality: accruals, working capital, capitalized costs.')
    if d['priced_in']['class'] == 'not_assessable_negative_fcf':
        out.append('What the price requires cannot be read from a cash-flow multiple: build the company-driver forecast.')
    return list(dict.fromkeys(out))


def _exclusion(s, c, disq, ranked, nm, scoped, o):
    if s in disq:
        return disq[s]
    if ranked:
        return f"ranked #{ranked[0]} in {ranked[1]} (shortlist takes up to {o['top_n']} names per lens, {o['shortlist_mode']} mode)"
    if nm:
        return f"near miss in {nm[0]}: {nm[1]['reason']}"
    d, m = c['dims'], c['m']
    lv = lambda k: d[k]['level']
    tags = []
    if lv('valuation') == 'strong' and (lv('growth_quality') == 'weak' or ((m.get('gross_margin_change') or 0) < -.02 and (m.get('operating_margin_trend') or 0) < -.02)):
        tags.append('cheap but deteriorating (possible value trap)')
    if lv('business_quality') == 'strong' and (lv('valuation') == 'weak' or lv('priced_in') == 'weak'):
        tags.append('quality but expensive')
    for ln, fails in scoped or []:
        tags.append(f"in scope of {ln} but failed {len(fails)} conditions ({' | '.join(n + ': ' + d.split(';')[0] for n, _, d in fails)})")
    return ('no lens matched: ' + (', '.join(tags) + '; ' if tags else '') +
            f"quality {lv('business_quality')}, growth {lv('growth_quality')} ({_fmt(m.get('growth_level'))}), strength {lv('financial_strength')}, "
            f"valuation {lv('valuation')}, priced-in {lv('priced_in')}, return proxy {_fmt(d['expected_return_proxy']['value'], 'pct1')}")


def _notes(o, ctx, no_data):
    grades = sorted({c['row']['data_grade'] for c in ctx.values()}, key=GRADE_ORDER.index)
    n = ['Research agenda only: not a recommendation, not a valuation, no orders. Every shortlisted name must pass pipeline.run '
         '(dataqa, model card, forecast, valuation, risk, review, decision) before any price conclusion.',
         'Data grades in this scan: ' + ', '.join(f'{g} ({GRADES[g]})' for g in grades) + '.' if grades else 'No usable rows.',
         'Survivorship and universe definition: the universe is whatever rows were supplied' + (f" ({o['universe']})" if o['universe'] else '')
         + '; a current-constituent list omits delisted and failed companies, and percentiles are relative to this universe only.',
         'Anchors are conventional reference points, not calibrated thresholds; levels need a balance of signals, so one metric '
         'missing an anchor does not remove a company.',
         'The expected-return proxy is rough (constant multiple, FCF growing with revenue, bounded growth). It orders candidates '
         'within a lens; it is not an IRR, not a forecast and not compared with a target. The owner aspiration is not used.',
         'priced_in uses a standard FCF fade model (not the pre-registered calibrated revenue model and without base rates): '
         'read it as "what the price requires", a crash-risk check. Valuation-gap ranking failed out of sample (docs/GAP_TEST_RESULTS.md).',
         'Paid adoption, retention, moat, management and capital allocation are not in screening data: lenses use numeric proxies '
         'and say so; the deep analysis must test them.',
         'Structural themes are analyst hypotheses supplied in options; numbers alone cannot identify a structural shift.']
    if no_data:
        kinds = sorted({v for x in no_data for v in x['errors'].values()})
        n.append(f'{len(no_data)} symbols without data' + (f" (vendor: {', '.join(kinds)})" if kinds else '') + ': listed under no_data, '
                 'not excluded on merit. A plan denial is a coverage gap, not evidence about the company.')
    return n


# ----------------------------------------------------------------- monitoring

def rank_changes(prev_scan, new_scan):
    """Which candidates entered or left the shortlist and lenses between two scans, and why (levels that moved)."""
    def lens_map(sc):
        return {ln: {c['symbol']: c['rank'] for c in sc['lenses'].get(ln, [])} for ln in LENSES}
    pa, pb = {e['symbol']: e for e in prev_scan['shortlist']}, {e['symbol']: e for e in new_scan['shortlist']}
    la, lb = lens_map(prev_scan), lens_map(new_scan)

    def levels(sc, s):
        d = sc['dimensions'].get(s)
        return {k: d[k]['level'] for k in DIMENSIONS} if d else None

    def moved(s):
        a, b = levels(prev_scan, s), levels(new_scan, s)
        if not a or not b:
            return {}
        return {k: [a[k], b[k]] for k in DIMENSIONS if a[k] != b[k]}

    def why_out(s):
        if s in new_scan['excluded']:
            return new_scan['excluded'][s]
        nd = next((x for x in new_scan['no_data'] if x['symbol'] == s), None)
        return f"no data in the new scan: {nd['reason']}" if nd else 'not in the new universe'
    entered = [{'symbol': s, 'lenses': [x['lens'] for x in pb[s]['lenses']], 'reason': pb[s]['reason'], 'level_changes': moved(s)}
               for s in sorted(set(pb) - set(pa))]
    left = [{'symbol': s, 'lenses_before': [x['lens'] for x in pa[s]['lenses']], 'reason': why_out(s), 'level_changes': moved(s)}
            for s in sorted(set(pa) - set(pb))]
    lens_moves = {}
    for ln in LENSES:
        ch = []
        for s in sorted(set(la[ln]) | set(lb[ln])):
            a, b = la[ln].get(s), lb[ln].get(s)
            if a != b:
                nm = next((x['reason'] for x in new_scan['near_misses'].get(ln, []) if x['symbol'] == s), None)
                ch.append({'symbol': s, 'from': a, 'to': b, 'reason': nm if b is None and nm else None, 'level_changes': moved(s)})
        lens_moves[ln] = ch
    return {'as_of_before': prev_scan.get('as_of'), 'as_of_after': new_scan.get('as_of'), 'entered': entered, 'left': left,
            'lens_moves': lens_moves, 'stayed': sorted(set(pa) & set(pb)),
            'note': 'Changes in a research agenda, not trading signals. A move can come from new data, a different universe or '
                    'changed options; compare input_sha256 and options before reading it as a change in the business.'}
