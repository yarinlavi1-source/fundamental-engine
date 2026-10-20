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
  separate from attractiveness and from risks that can be priced. A possible cyclical peak is a
  condition only where peak earnings make the price look low (quality_fair_price,
  mispriced_turning); elsewhere it is a flag and an ordering demotion.
- No preference for growth, size, volatility or stories: none of them is rewarded by itself.
"""
from bisect import bisect_left, bisect_right
from datetime import date
from statistics import mean, median
import json
import math

from . import facts as F
from .expectations import growth_path
from .inflection import features, stage as _stage
from .model_card import CYCLICAL_TYPES, LENDER_LIABILITIES, clean_text, cyclical_word, platform_signature, text_type
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
    'ev_to_fcf': (18., 45., 'lower'), 'pe': (18., 45., 'lower'), 'ev_sales_per_r40': (.25, .60, 'lower'), 'ev_to_sales': (3., 10., 'lower'),
    'expected_return_proxy': (.11, .06, 'higher'), 'volatility_1y': (.30, .55, 'lower'),
}
DEFAULTS = {'as_of': None, 'universe': None, 'top_n': 3, 'sector_cap': 2, 'discount_rate': .09, 'terminal_growth': .03,
            'fade_high_years': 5, 'fade_years': 10, 'consensus_hold_years': 2, 'growth_start_bounds': [-.10, .30],
            'sustainable_growth_bounds': [-.05, .15], 'priced_in_tolerance': .04, 'perfection_growth': .30,
            'min_percentile_n': 5, 'min_sector_peers': 4, 'early_growth_min': .20, 'drawdown_overreaction': -.30,
            'shortlist_mode': 'distinct', 'shortlist_sector_cap': None, 'themes': {}, 'structural_industries': [],
            'unsupported_industries': None, 'unsupported_defaults': True, 'supported_override': {}, 'cyclical': {}, 'anchors': {}}
TAX = .21            # statutory US rate: a convention for after-tax operating profit, not the company's rate
UNSUPPORTED_TYPES = ('financials', 'reit')     # model_card text types whose economics the screen cannot describe
UNSUPPORTED_INDUSTRIES = ('Diversified Banks', 'Regional Banks', 'Life & Health Insurance', 'Property & Casualty Insurance',
                          'Multi-line Insurance', 'Reinsurance', 'Consumer Finance', 'Investment Banking & Brokerage',
                          'Commercial & Residential Mortgage Finance', 'Thrifts & Mortgage Finance', 'Multi-Sector Holdings')
# Labels listing fee businesses with balance-sheet firms (model_card.BALANCE_MIXED_RULES) that stay screenable when the row
# carries no balance sheet (the pre-fix behaviour; flagged); other mixed labels ('credit services') then need the balance
# sheet or options.supported_override, because most of their members are lenders.
SCREEN_MIXED_SUPPORTED = ('asset management',)
# Metric types a hand-built or CLI row may carry; every other metric must be numeric (anything else becomes unknown).
STR_METRICS = {'altman_zone': ('safe', 'grey', 'distress'), 'beneish_zone': ('likely_manipulator', 'watch', 'unlikely'),
               'stage_pit': None}
BOOL_METRICS = ('acquisition_driven', 'financial_statements', 'accruals_high', 'beneish_high', 'interest_coverage_vendor_zero')
LIST_METRICS = ('revenue_growth_series', 'operating_margin_history')
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
          'ev_to_fcf': ('EV / FCF', 'x'), 'pe': ('P/E', 'x'), 'ev_sales_per_r40': ('EV/sales per Rule-of-40 point', 'x2'),
          'consensus_outlook': ('consensus revenue growth', 'pct'), 'ev_to_sales': ('EV / sales', 'x')}
WORD = {1: 'supports', 0: 'neutral', -1: 'weighs against', None: 'unknown'}

ER_FORMULA = ('expected_return_proxy = owner FCF yield on equity ((FCF - SBC) / market cap; FCF is after interest, so its '
              'denominator is the equity, not EV) + sustainable growth - excess dilution. '
              'Sustainable growth = geometric mean of a 10-year path that holds the start growth (consensus 2y revenue CAGR, '
              'else demonstrated 3y CAGR, else latest growth; bounded) for 2 years and fades linearly to terminal growth '
              '(expectations.growth_path), then bounded. Excess dilution = max(0, share growth - SBC / market cap), so SBC is '
              'charged once. Assumes a constant multiple and FCF growing with revenue.')
MEANINGS = {
    'business_quality': 'Returns on capital, margin level and stability, cash conversion, Piotroski. strong = high and durable economics today (needs a strong ROIC or margin level, not stability and conversion alone).',
    'growth_quality': 'Growth level, consistency, acceleration (a normal fade from a high base is neutral), operating leverage, per-share dilution (buybacks do not offset shrinking revenue), acquired-growth hints and consensus far below demonstrated growth or expecting a decline. strong = sizeable growth that is repeatable, organic and accrues per share.',
    'financial_strength': 'Net debt/EBITDA, interest coverage, Altman zone, FCF sign and cash runway. strong = no funding dependence.',
    'valuation': 'EV/FCF, P/E and EV/sales per Rule-of-40 point. strong = undemanding multiples. Cheapness alone is not a selection signal (H2 failed).',
    'expected_return_proxy': 'ROUGH screening-grade annual return proxy; see formula. Not an IRR, not a forecast, never a target. Unknown when owner FCF <= 0 (yield-based proxies cannot see a J-curve).',
    'priced_in': 'Growth the EV / after-tax operating profit multiple implies under a standard fade model (market cap / owner FCF when operating profit is thin or unknown; the other basis is shown as a cross-check), versus consensus (compared on its own 2-year horizon) or demonstrated growth (a cyclical without consensus is compared with long-run growth). strong = price assumes less than the evidence; weak = more, or priced for perfection. A crash-risk check, not a return predictor. price_check carries a crude fallback (EV/sales per Rule-of-40 point, EV/sales, P/E) when it cannot be read.',
    'risk': 'Leverage, distress, dilution, funding runway, priced-for-perfection, demanding fallback price, accounting and cash-conversion, cyclical-peak, consensus-downside and volatility flags, plus low data flags (leverage or dilution unknown, unchecked cyclical). strong = leverage data present and no material flag; weak = a high-severity flag.',
    'data_uncertainty': 'Coverage of key metrics and the data grade. strong = low uncertainty (validated FactBook); screening-grade data is at best adequate. A vendor denial that leaves coverage below half is a coverage gap.',
}
LENS_RULES = {
    'quality_compounder': {
        'label': 'Quality company with strong growth',
        'scope': 'growth_level >= growth anchor (12%/yr). Fast growth is not treated as risky by itself.',
        'conditions': ['business_quality strong or adequate', 'profitable: operating margin or ROIC at or above its weak anchor (loss-makers belong in early_commercial_growth)',
                       'growth_quality strong (repeatable, organic, per share)', 'per share: share count known and growing <= 5%/yr, no high dilution flag',
                       'financial_strength not weak', 'no high-severity leverage, interest-coverage or funding flag',
                       'earnings quality: no accounting or cash-conversion flag'],
        'ordering': 'cyclical peak check (none or through-cycle, incomplete, possible peak), then quality tier (strong before adequate), then price check (assessed, unassessed, demanding or priced for perfection), then expected-return proxy (desc). Meaning: growth that may be a cyclical peak ranks after repeatable growth; among comparable quality an unread or demanding price ranks later; then the price that leaves the most return.'},
    'quality_fair_price': {
        'label': 'Quality company at an attractive or fair price',
        'scope': 'business_quality strong (size is irrelevant; a mega cap qualifies).',
        'conditions': ['valuation strong or adequate', 'priced_in strong or adequate (EV / after-tax operating profit, so growth capex does not read as expensive)',
                       'trajectory: growth_quality strong or adequate and revenue not shrinking over 3 years (guards value traps; buybacks do not offset)',
                       'financial_strength not weak', 'leverage known (net debt in the data)', 'no high-severity leverage, interest-coverage or funding flag',
                       'earnings quality: no accounting or cash-conversion flag', 'expected_return_proxy strong or adequate',
                       'not a cyclical at a possible or unchecked peak (peak FCF is not a fair price; missing consensus is not "no peak")'],
        'ordering': 'growth-quality tier (strong before adequate), then expected-return proxy (desc), then business-quality percentile (desc). Meaning: among strong businesses the better trajectory first; the rough return for the price only breaks ties; not a valuation gap ranking.'},
    'early_commercial_growth': {
        'label': 'Earlier-stage growth with commercial proof',
        'scope': 'earlier stage by evidence (net loss, operating loss, cash burn, inflection stage early_commercial or turning_profitable, or operating margin < 10% with gross margin >= 40% or unknown; a low margin alone does not make a steel maker or contractor early) and growth >= early_growth_min (20%). Size and missing net profit do not reject.',
        'conditions': ['commercial proof: >= 3 of 4 (unit economics, operating leverage, cash trajectory, repeat growth); operating leverage and cash trajectory count only while the loss or burn is not widening in dollars',
                       'share count known and growing <= 10%/yr', 'no distress flag'],
        'ordering': 'proof count (desc), then funded before funding-conditional, then cyclical peak check, then price check (assessed, unassessed, demanding: fallback EV/sales per Rule-of-40 point when owner FCF <= 0), then per-share growth (desc). Meaning: measured commercial evidence first; story without numbers is never rewarded, and an extreme price never ties with a moderate one.'},
    'mispriced_turning': {
        'label': 'Mispriced or turning-point business',
        'scope': 'at least one improvement signal (a recovery from a down year counts), or a >= 30% drawdown from the high when price data is supplied (context only).',
        'conditions': ['>= 2 improvement signals incl. a profit/cash one, or overreaction path (drawdown with intact quality and growth plus at least one improvement signal)',
                       'the price lags: priced_in strong, or valuation strong when priced_in is unknown, or (overreaction path) priced_in not weak',
                       'share count known and growing <= 8%/yr', 'financial_strength not weak', 'no high-severity leverage, interest-coverage or funding flag',
                       'not a cyclical at a possible or unchecked peak (peak earnings make a price look like it lags)'],
        'ordering': 'improvement-signal count (desc), then priced_in (strong, adequate, unknown), then expected-return proxy (desc). Meaning: business evidence first, price second.'},
    'structural_tailwind': {
        'label': 'Structural long-term opportunity',
        'scope': 'an analyst theme for the symbol (options.themes) or industry (options.structural_industries), or the numeric footprint of fast growth (top quartile of its sector in this universe with a non-falling gross margin; organic share stated when known).',
        'conditions': ['durable growth: 3y CAGR >= 8% without repeated declines', 'business_quality not weak',
                       'potential not already priced (priced_in not weak; unknown counts only with an analyst theme, a known market value and a fallback price read that is not demanding: a theme is not a price check)',
                       'share count known and growing <= 10%/yr and no high funding/dilution flag', 'financial_strength not weak',
                       'no high-severity leverage, interest-coverage or funding flag'],
        'ordering': 'cyclical peak check, then price rank (priced_in, or the fallback read when unknown: strong, adequate, unassessed, weak), then expected-return proxy (desc), then growth (desc). Meaning: of real tailwinds, those the price has not fully absorbed first. Themes are analyst hypotheses, not evidence.'},
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
                       ('net_margin', 'netProfitMarginTTM')):
            put(k, g(rt, src), f'FMP {src}')
        icov = g(rt, 'interestCoverageRatioTTM')
        if icov == 0 and (m.get('operating_margin') or 0) > 0:      # EBIT / interest = 0 with EBIT > 0 is impossible: a placeholder
            put('interest_coverage_vendor_zero', True, 'vendor interestCoverageRatioTTM = 0 with a positive operating margin: interest '
                'expense not reported; coverage read as unknown, never as 0x')
        else:
            put('interest_coverage', icov, 'FMP interestCoverageRatioTTM')
        if (g(rt, 'priceToEarningsRatioTTM') or 0) > 0:
            put('pe', g(rt, 'priceToEarningsRatioTTM'), 'FMP priceToEarningsRatioTTM')
        fps, rps, nps = g(rt, 'freeCashFlowPerShareTTM'), g(rt, 'revenuePerShareTTM'), g(rt, 'netIncomePerShareTTM')
        if rps and rps > 0:
            put('fcf_margin', _div(fps, rps), 'FMP FCF per share / revenue per share (TTM)')
        if nps and nps > 0:
            put('fcf_conversion', _div(fps, nps), 'FMP FCF per share / net income per share (TTM)')
        if 'revenue_ttm' not in m and rps and fps and m.get('fcf_ttm'):
            put('revenue_ttm', rps * m['fcf_ttm'] / fps, 'FMP revenue per share x shares (FCF TTM / FCF per share)')
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
            fcg = _f(last.get('freeCashFlowGrowth'))
            gm, om, fm = m.get('gross_margin'), m.get('operating_margin'), m.get('fcf_margin')
            if None not in (rg, gpg, gm) and 1 + rg > 0 and 1 + gpg > 0:
                rel = (1 + gpg) / (1 + rg)
                put('gross_margin_change', gm - gm / rel, 'approx: TTM gross margin x (1 - (1+revenueGrowth)/(1+grossProfitGrowth)), last FY')
            # prior-year margin = margin x (1 + revenue growth) / (1 + item growth); vendor growth is (x_t - x_t-1) / x_t-1 with its
            # raw sign, so the identity holds for losses and sign changes (a loss-making grower can show operating leverage)
            if None not in (rg, oig, om) and 1 + rg > 0 and 1 + oig != 0:
                om0 = om * (1 + rg) / (1 + oig)
                put('operating_margin_trend', om - om0, 'approx: TTM operating margin minus prior-FY margin from operatingIncomeGrowth vs revenueGrowth')
                if rg >= .05:
                    put('incremental_operating_margin', (om * (1 + rg) - om0) / rg, 'approx: change in operating income / change in revenue, last FY')
            if None not in (rg, fcg, fm) and 1 + rg > 0 and 1 + fcg != 0:
                put('fcf_margin_change', fm - fm * (1 + rg) / (1 + fcg), 'approx: TTM FCF margin minus prior-FY margin from freeCashFlowGrowth vs revenueGrowth')
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
        dparts = [latest_at(rec.get(k) or {}, t) for k in ('ltd', 'ltd_cur')]
        debt = None if all(x is None for x in dparts) else sum(x or 0 for x in dparts)    # no debt fact usable at t: unknown, never 0
        if debt is not None:
            put('net_debt', debt - cash, 'term debt + current debt - cash - short-term investments (converts/leases not included)')
        if f.get('oi_ttm') is not None:
            put('ebitda', f['oi_ttm'], 'EBIT proxy: D&A is not in the PIT record (overstates leverage)')
            eq = latest_at(rec.get('equity') or {}, t)
            if eq is not None and debt is not None and eq + debt - cash > 0:
                put('roic', f['oi_ttm'] * (1 - TAX) / (eq + debt - cash), 'approx: operating income TTM x (1 - 21%) / (equity + debt - cash)')
        price, mcap = _f(p.get('price')), _f(p.get('market_cap'))
        if mcap is None and price:
            _, sh, sh_src = shares_at(rec, t)
            if sh:
                mcap = sh * price
                b['market_cap'] = f'price x shares ({sh_src}, pit.shares_at)'
        if mcap:
            b.setdefault('market_cap', 'supplied market cap')
            m['market_cap'] = mcap
            if debt is not None:
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
        debt = _f(r.get('debt'))          # None (scope 'missing'): net debt, EV and ROIC unknown, never net cash
        if debt is not None:
            put('net_debt', debt - cash, f"debt ({r.get('debt_scope', 'unknown')} scope) - cash - short-term investments")
        put('ebitda', oi, 'EBIT proxy: D&A not in frames (overstates leverage)')
        eq = _f(r.get('equity'))
        if oi is not None and eq is not None and debt is not None and eq + debt - cash > 0:
            put('roic', oi * (1 - TAX) / (eq + debt - cash), 'approx: operating income x (1 - 21%) / (equity + debt - cash)')
        mcap = _f(p.get('market_cap')) or _f(r.get('market_cap'))
        price = _f(p.get('price')) or _f(r.get('price'))
        if mcap is None and price and _f(r.get('shares_outstanding')):
            mcap = price * _f(r.get('shares_outstanding'))
            b['market_cap'] = 'price x balance-sheet shares outstanding (frame instant)'
        if mcap:
            b.setdefault('market_cap', 'supplied market cap')
            m['market_cap'] = mcap
            if m.get('net_debt') is not None:
                put('ev', mcap + m['net_debt'], 'market cap + net debt')
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
        window = [x for x in prev[-2:] + [r] if x and (_f(x.get('revenue')) or 0) > 0]
        if any(_f(x.get('acquisitions')) is not None for x in window):    # capital allocation: acquired growth and buybacks
            put('acquisitions_pct_revenue', sum(abs(_f(x.get('acquisitions')) or 0) for x in window) / sum(_f(x['revenue']) for x in window),
                f'acquisition spend / revenue over {len(window)} calendar-year frame(s)')
        fw = [x for x in window if _f(x.get('fcf')) is not None]
        if any(_f(x.get('buybacks')) is not None for x in fw) and sum(_f(x['fcf']) for x in fw) > 0:
            put('buybacks_pct_fcf', sum(abs(_f(x.get('buybacks')) or 0) for x in fw) / sum(_f(x['fcf']) for x in fw),
                f'buybacks / FCF over {len(fw)} calendar-year frame(s)')
        oms = [_div(_f(x.get('operating_income')), _f(x.get('revenue'))) for x in prev + [r] if x and (_f(x.get('revenue')) or 0) > 0]
        if len(oms) >= 3 and None not in oms:
            put('operating_margin_history', oms[-5:], 'operating income / revenue per calendar-year frame, oldest first')
        if (_f(r.get('total_assets')) or 0) > 0 and _f(r.get('total_liabilities')) is not None:
            put('liabilities_to_assets', _f(r['total_liabilities']) / _f(r['total_assets']), 'total liabilities / total assets (frame)')
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
        elif bal.get('debt') is not None:              # debt unknown: net debt and EV unknown, never net cash
            put('net_debt', bal['debt'] - cash, f'debt - cash - investments ({how})')
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
        ben = fx.get('beneish') if isinstance(fx.get('beneish'), dict) else {}
        if ben.get('status') == 'computed' and ben.get('zone') in STR_METRICS['beneish_zone']:
            put('beneish_zone', ben['zone'], f"analysis forensics.beneish (M {_f(ben.get('m'))})")
        put('accruals_ratio', mv('accruals_ratio'), 'analysis metric accruals_ratio ((net income - CFO) / average assets)')
        codes = {x.get('code') for x in (an or {}).get('flags') or [] if isinstance(x, dict)}
        for code in ('beneish_high', 'accruals_high'):
            if code in codes:
                put(code, True, f'analysis flag {code}')
        oms = [(y['period_end'], _div(y.get('operating_income'), y.get('revenue'))) for y in years if (y.get('revenue') or 0) > 0]
        oms = [x for x in oms if x[1] is not None][-5:]
        if len(oms) >= 3:
            put('operating_margin_history', [x[1] for x in oms], f'annual operating margin {oms[0][0]}..{oms[-1][0]}, oldest first')
        if years and (years[-1].get('total_assets') or 0) > 0 and years[-1].get('total_liabilities') is not None:
            put('liabilities_to_assets', years[-1]['total_liabilities'] / years[-1]['total_assets'],
                f"total liabilities / total assets at {years[-1]['period_end']}")
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
            if m.get('net_debt') is not None:
                put('ev', price * sh + m['net_debt'], 'market cap + net debt')
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
    if m.get('operating_margin') is not None and rev and rev > 0:
        put('nopat', m['operating_margin'] * rev * (1 - TAX), 'operating margin x revenue x (1 - 21%, tax-rate convention)')
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
    nd, mc = m.get('net_debt'), m.get('market_cap')
    cash_box = nd is not None and nd < 0 and mc and -nd >= .5 * mc       # net cash is at least half the equity value
    roic_note = 'very high: small or negative invested capital' if (m.get('roic') or 0) > 1 else None
    if cash_box and roic_s == 1 and (om_s or 0) <= 0 and (gm_s or 0) <= 0:
        roic_s, roic_note = 0, (f"ROIC on a small operating base inside a cash-rich company (net cash {_fmt(-nd / mc)} of market cap) "
                                'without a strong margin: not counted as a quality signal')
    if ml == -1:
        sp = max(c['spct'].get('gross_margin', 0), c['spct'].get('operating_margin', 0))
        if roic_s == 1:
            ml, note = 0, 'low margins offset by high capital turnover (ROIC strong)'
        elif sp >= .75:
            ml, note = 0, 'low in absolute terms but top quartile of its sector in this universe'
    inputs = {'roic': _inp(c, 'roic', m.get('roic'), roic_s, note=roic_note),
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
    d = _dim(inputs)
    if d['level'] == 'strong' and 1 not in (roic_s, ml):
        d['level'] = 'adequate'
        d['reasons'].append('capped at adequate: strong business quality needs a strong return on capital or margin level, not '
                            'stability and cash conversion alone')
    return d


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
    ser = [x for x in m.get('revenue_growth_series') or [] if x is not None]
    if acc_s == -1 and len(ser) >= 3:        # deceleration from a high base is normal unless consensus expects a sharp break
        prior, latest, cons = (ser[-2] + ser[-3]) / 2, ser[-1], m.get('consensus_growth_2y')
        if prior >= .20 and latest >= A['growth_level'][0] and (
                (cons is not None and not _consensus_downside(m)) or (cons is None and latest >= .6 * prior)):
            acc_s, note = 0, (f'normal fade from a high base: latest {_fmt(latest)} vs {_fmt(prior)} before, '
                              + ('consensus does not expect a sharp break' if cons is not None else 'still >= 60% of the prior rate'))
    org = None
    if m.get('acquisition_driven') is True:
        org = -1
    elif m.get('acquisitions_pct_revenue') is not None:
        org = 1 if m['acquisitions_pct_revenue'] < .02 else (-1 if m['acquisitions_pct_revenue'] > .10 else 0)
    elif m.get('acquisition_driven') is False:
        org = 0
    gl_s = _sig(m.get('growth_level'), A['growth_level'])
    dil_s, dil_note = _sig(m.get('dilution_yoy'), A['dilution_yoy']), 'per-share growth leaks when shares grow'
    if dil_s == 1 and gl_s == -1:
        dil_s, dil_note = 0, 'buybacks do not offset shrinking revenue: per-share gains from a smaller business'
    inputs = {'growth_level': _inp(c, 'growth_level', m.get('growth_level'), gl_s),
              'consistency': _inp(c, 'consistency', m.get('revenue_growth_series'), _consistency(m.get('revenue_growth_series')),
                                  metric='revenue_growth_series'),
              'acceleration': _inp(c, 'acceleration', acc, acc_s, note=note),
              'operating_margin_trend': _inp(c, 'operating_margin_trend', m.get('operating_margin_trend'),
                                             _sig(m.get('operating_margin_trend'), A['operating_margin_trend']),
                                             note='does growth bring operating leverage?'),
              'dilution_yoy': _inp(c, 'dilution_yoy', m.get('dilution_yoy'), dil_s, note=dil_note),
              'organic': _inp(c, 'organic', m.get('acquisitions_pct_revenue', m.get('goodwill_jump_to_revenue')), org,
                              metric='acquisitions_pct_revenue', note='acquired growth is not organic' if org == -1 else None)}
    down, cons = _consensus_downside(m), m.get('consensus_growth_2y')
    if not down and cons is not None and cons < 0:
        down = f'consensus expects revenue to decline ({_fmt(cons)}/yr)'
    inputs['consensus_outlook'] = _inp(c, 'consensus_outlook', cons if down else None, -1 if down else None,
                                       metric='consensus_growth_2y', note=down or None)
    d = _dim(inputs)
    if d['level'] == 'strong' and inputs['growth_level']['signal'] != 1:
        d['level'] = 'adequate'
        d['reasons'].append('capped at adequate: strong growth quality needs growth at or above the level anchor')
    if d['level'] == 'strong' and inputs['consensus_outlook']['signal'] == -1:
        d['level'] = 'adequate'
        d['reasons'].append('capped at adequate: consensus does not expect the demonstrated growth to repeat, so it is not shown to be repeatable')
    return d


def _consensus_downside(m):
    """Text when consensus revenue growth is far below demonstrated growth (analysts expect a slowdown or a decline: cycle, lost
    customer, tough comparisons), else ''. Demonstrated = the lower of latest growth and the 3y CAGR (older, faster years do
    not inflate it); 'far below' = more than 10 points and more than half of it, so a 45% grower expected to slow to 30% is
    a normal fade, not a downside. Only a downside gap counts against growth quality."""
    demo = [x for x in (m.get('revenue_growth'), m.get('revenue_cagr_3y')) if x is not None]
    cons = m.get('consensus_growth_2y')
    if not demo or cons is None:
        return ''
    base = min(demo)
    band = max(.10, .5 * base)
    if cons >= base - band:
        return ''
    return (f'consensus {_fmt(cons)}/yr is far below demonstrated {_fmt(base)}/yr (lower of latest growth and 3y CAGR): the '
            'demonstrated growth is not expected to repeat')


def _downturn(m):
    """(index, level, decline) for the most recent revenue decline of >= 10% before the latest year: level = revenue now relative
    to the year before that decline (1.0 = back to the pre-downturn level). None when there is no such decline."""
    ser = [x for x in m.get('revenue_growth_series') or [] if x is not None]
    for i in range(len(ser) - 2, -1, -1):
        if ser[i] <= -.10:
            level = 1.
            for x in ser[i:]:
                level *= 1 + x
            return i, level, ser[i]
    return None


def _boom_after_downturn(m, A):
    """Data sign of a cycle, whatever the label: a >= 10% revenue drop, then growth back to >= 25% above the pre-downturn level, with
    the latest year +20% or more and the operating margin up >= 3 points at a profitable level (memory-type upswing)."""
    dt, g, tr, om = _downturn(m), m.get('revenue_growth'), m.get('operating_margin_trend'), m.get('operating_margin')
    if not dt or None in (g, tr, om) or g < .20 or tr < .03 or om < A['operating_margin'][1] or dt[1] < 1.25:
        return ''
    return (f'revenue fell {_fmt(-dt[2])} and is now {_fmt(dt[1] - 1)} above its pre-downturn level; latest +{_fmt(g)} with operating '
            f'margin {_fmt(tr, "pts")} to {_fmt(om)} (boom after a downturn)')


def _recovery(m):
    """A decline year (>= 5%) followed by growth and an operating-margin recovery (>= 2 points), with revenue still near (< 25% above)
    its pre-decline level: a turning point, not a peak."""
    ser = [x for x in m.get('revenue_growth_series') or [] if x is not None]
    g, tr = m.get('revenue_growth'), m.get('operating_margin_trend')
    i = next((j for j in range(len(ser) - 2, -1, -1) if ser[j] <= -.05), None)
    if i is None or g is None or g <= 0 or tr is None or tr < .02:
        return ''
    level = 1.
    for x in ser[i:]:
        level *= 1 + x
    if level >= 1.25:
        return ''
    return f'revenue recovering after a {_fmt(-ser[i])} decline (now {_fmt(level - 1)} vs the pre-decline level) with operating margin {_fmt(tr, "pts")}'


def _cycle(c, o):
    """Cyclical identity, before any peak test: model_card.CYCLICAL_WORDS in the industry/sector text, a model_card cyclical TYPE
    (commodity, semiconductor: same definition as the card), or the data (a boom after a downturn, whatever the label). A mixed
    label whose financial signature is a platform's (a booking platform under 'Hotels, Resorts & Cruise Lines') does not take the
    operators' word. An analyst confirms or rejects it with a reason (options.cyclical {symbol: {value, reason}}). None when not
    cyclical and no analyst statement."""
    row, m, A = c['row'], c['m'], c['anchors']
    ov = (o['cyclical'] or {}).get(row['symbol'])
    t = text_type({'industry': row.get('industry'), 'sector': row.get('sector')})
    word, notes = cyclical_word(row), []
    if word and t and t.get('signature') == 'platform' and t['field'] == 'industry' and cyclical_word({'industry': t['text']}):
        cx = m.get('capex_to_revenue')
        plat, rule = platform_signature(m.get('gross_margin'), abs(cx) if cx is not None else None, m.get('operating_margin'))
        if plat:
            word = cyclical_word({'sector': row.get('sector')})
            notes.append(f"'{t['text']}' mixes {t['mixes']}; platform signature ({rule}): the operators' cyclical word does not flag it")
    typ = t['type'] if t and t['type'] in CYCLICAL_TYPES else None
    cons, down = m.get('consensus_growth_2y'), _consensus_downside(m)
    boom = _boom_after_downturn(m, A) if (cons is None or down) else ''      # consensus expecting more growth contradicts a boom
    basis = 'industry_word' if word else ('type' if typ else ('data' if boom else None))
    if ov is None and not basis:
        return None
    out = {'value': bool(basis), 'basis': basis, 'word': word, 'type': typ, 'data_boom': boom or None, 'recovery': _recovery(m) or None,
           'override': None, 'peak': False, 'peak_check': None, 'signs': [], 'checked': [], 'notes': notes}
    if ov is not None:
        out['override'] = {'value': ov['value'], 'reason': ov['reason']}
        out['value'] = ov['value']
        out['basis'] = 'analyst'
        notes.append(f"analyst {'confirms' if ov['value'] else 'rejects'} cyclical economics ({ov['reason']})")
    return out


def _peak(c, o, pi):
    """Possible-peak signs for a cyclical row, filled into c['cycle'] after the price check. Consensus-free signs exist, so missing
    consensus is 'incomplete', never 'no peak'. Signs: consensus far below demonstrated growth; boom-like growth with expanding
    margins (unless consensus expects growth near the demonstrated rate, the year is a recovery, or the margin is not above its own
    history); a plateau after a boom; a price that discounts falling earnings (an unlevered multiple, EV / NOPAT, implying <= -10%/yr);
    the operating margin far above its own multi-year history."""
    cy = c.get('cycle')
    if not cy or not cy['value']:
        return
    m, A = c['m'], c['anchors']
    g, tr, om, cagr = m.get('revenue_growth'), m.get('operating_margin_trend'), m.get('operating_margin'), m.get('revenue_cagr_3y')
    cons, down = m.get('consensus_growth_2y'), _consensus_downside(m)
    confirms = cons is not None and not down
    hist = [x for x in m.get('operating_margin_history') or [] if x is not None]
    signs, checked = [], []
    if cons is not None:
        checked.append('consensus')
    if down:
        signs.append(down)
    if cy.get('data_boom'):
        signs.append(cy['data_boom'])
    elif None not in (g, tr, om) and g >= .15 and tr >= .03 and om >= A['operating_margin'][1] and not confirms and not cy['recovery']:
        if len(hist) >= 3 and om <= mean(hist[:-1]):
            cy['notes'].append(f'growth +{_fmt(g)} with margin {_fmt(tr, "pts")}, but the margin {_fmt(om)} is not above its own history '
                               f'average {_fmt(mean(hist[:-1]))}: not read as a peak')
        else:
            signs.append(f'revenue +{_fmt(g)} with operating margin {_fmt(tr, "pts")} to {_fmt(om)} (boom-like)')
    if cagr is not None and g is not None and cagr >= .15 and g <= cagr / 2 and (tr is None or tr >= -.01) and not confirms:
        signs.append(f'3y revenue CAGR {_fmt(cagr)} slowed to {_fmt(g)} with the operating margin holding ({_fmt(om)}): a plateau after a boom')
    unlevered = (pi.get('multiple_basis') or '').startswith('EV') or (m.get('net_debt') is not None and m['net_debt'] <= 0)
    if pi.get('implied_growth') is not None and pi.get('multiple_basis') and unlevered:     # a levered equity multiple is low by leverage
        checked.append('price')
        if pi['status'] == 'below_range' or pi['implied_growth'] <= -.10:
            signs.append(f"the price discounts falling earnings ({pi['multiple_basis'].split(' (')[0]} {pi['multiple']:.1f}x implies "
                         f"~{_fmt(pi['implied_growth'])}/yr): peak earnings priced as such")
    if len(hist) >= 3:
        checked.append('margin_history')
        avg, last = mean(hist), hist[-1]
        if last > 0 and (last > avg + .05 or (avg > 0 and last > 1.5 * avg)):
            signs.append(f'operating margin {_fmt(last)} vs {_fmt(avg)} average over {len(hist)} years')
        elif len(hist) >= 4 and last > 0 and last >= max(hist) - .005 and last - min(hist) >= .05 and last > avg:
            signs.append(f'operating margin {_fmt(last)} is the {len(hist)}-year high after a climb from {_fmt(min(hist))}')
    cy.update(signs=signs, checked=checked, peak=bool(signs),
              peak_check='possible_peak' if signs else ('no_peak_signs' if {'consensus', 'margin_history'} & set(checked) else 'incomplete'))


CYC_RANK = {None: 0, 'no_peak_signs': 0, 'incomplete': 1, 'possible_peak': 2}


def _cyc_state(c):
    """(peak_check or None, tag) for lens ordering and tags: through_cycle / peak_check_incomplete / possible_cyclical_peak."""
    cy = c.get('cycle')
    if not cy or not cy['value']:
        return None, []
    pc = cy['peak_check']
    return pc, [{'no_peak_signs': 'through_cycle', 'incomplete': 'peak_check_incomplete', 'possible_peak': 'possible_cyclical_peak'}[pc]]


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
    if cov == 0 and (m.get('operating_margin') or 0) > 0:
        cov = None                      # vendor placeholder: EBIT / interest cannot be 0 with EBIT > 0
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


def _fade_multiple(g, o, hold=None):
    """PV per $1 of current owner cash when it grows at g for `hold` years (default fade_high_years), then fades linearly to
    terminal growth by fade_years."""
    r, gt = o['discount_rate'], o['terminal_growth']
    f, pv = 1., 0.
    for t, gy in enumerate(growth_path(g, gt, o['fade_high_years'] if hold is None else hold, o['fade_years']), 1):
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


def _reference_growth(c, o):
    """(growth, basis, kind) the price is compared with: consensus 2y CAGR, else demonstrated 3y CAGR, else latest growth. A cyclical
    row without consensus uses min(demonstrated, terminal growth): a boom-era CAGR is not evidence of repeatable growth."""
    m = c['m']
    if m.get('consensus_growth_2y') is not None:
        return m['consensus_growth_2y'], 'consensus 2y revenue CAGR (vendor estimate)', 'consensus'
    demo, basis = ((m['revenue_cagr_3y'], 'demonstrated 3y revenue CAGR') if m.get('revenue_cagr_3y') is not None else
                   ((m['revenue_growth'], 'latest revenue growth') if m.get('revenue_growth') is not None else (None, None)))
    cy = c.get('cycle')
    if demo is not None and cy and cy['value'] and demo > o['terminal_growth']:
        return (o['terminal_growth'], f"cyclical without consensus: long-run growth {_fmt(o['terminal_growth'])} (terminal), not the "
                f'{basis} {_fmt(demo)} that may include a boom', 'cyclical_long_run')
    return demo, basis, 'demonstrated' if demo is not None else None


def _price_check(m, A, pi):
    """Price read used by every lens: priced_in when assessable; otherwise a fallback multiple (EV/sales per Rule-of-40 point, EV/sales,
    EV <= 0, P/E); otherwise 'unassessed'. A crude read, labelled as such; it never replaces company-driver expectations."""
    if pi['level'] != 'unknown':
        return {'level': pi['level'], 'source': 'priced_in', 'basis': (pi['multiple_basis'] or '').split(' (')[0], 'value': pi['multiple']}
    for k, lab in (('ev_sales_per_r40', 'EV/sales per Rule-of-40 point'), ('ev_to_sales', 'EV/sales')):
        if m.get(k) is not None:
            return {'level': {1: 'strong', 0: 'adequate', -1: 'weak'}[_sig(m[k], A[k])], 'source': 'fallback', 'basis': lab, 'value': m[k]}
    if m.get('ev') is not None and m['ev'] <= 0 and m.get('market_cap'):
        return {'level': 'strong', 'source': 'fallback', 'basis': 'EV <= 0 (net cash at or above market cap)', 'value': m['ev']}
    if m.get('pe') is not None:
        return {'level': {1: 'strong', 0: 'adequate', -1: 'weak'}[_sig(m['pe'], A['pe'])], 'source': 'fallback', 'basis': 'P/E', 'value': m['pe']}
    return {'level': 'unassessed', 'source': None, 'basis': 'no market value or no usable multiple', 'value': None}


def _priced_in(c, o):
    """What growth the price requires. Primary basis EV / after-tax operating profit (unlevered over unlevered; growth capex
    does not depress it); market cap / owner FCF (levered over equity, the return-proxy basis) when operating profit is unknown
    or thin (operating margin below its weak anchor without strong ROIC, e.g. an early grower near breakeven), and as a
    cross-check otherwise. The implied growth is held fade_high_years (the calibrated priced-for-perfection convention); a consensus
    reference covers consensus_hold_years, so it is compared on the same horizon: the multiple the consensus path justifies
    (consensus held consensus_hold_years, then the same fade) is converted to its fade_high_years-equivalent growth."""
    m, A = c['m'], c['anchors']
    owner, obasis = (m['owner_fcf'], 'owner FCF (FCF - SBC)') if m.get('owner_fcf') is not None else (m.get('fcf_ttm'), 'FCF (SBC unknown: overstated)')
    ev, mc, nopat, om = m.get('ev'), m.get('market_cap'), m.get('nopat'), m.get('operating_margin')
    ref, ref_basis, ref_kind = _reference_growth(c, o)
    out = {'level': 'unknown', 'class': 'not_assessable', 'implied_growth': None, 'status': None, 'multiple': None, 'multiple_basis': None,
           'reference_growth': ref, 'reference_basis': ref_basis, 'reference_equivalent_growth': None, 'justified_multiple': None,
           'demonstrated_growth': m.get('revenue_cagr_3y', m.get('revenue_growth')),
           'consensus_growth': m.get('consensus_growth_2y'), 'gap': None, 'cross_check': None,
           'model': {'discount_rate': o['discount_rate'], 'terminal_growth': o['terminal_growth'], 'high_years': o['fade_high_years'],
                     'reference_hold_years': o['consensus_hold_years'] if ref_kind == 'consensus' else o['fade_high_years'],
                     'years': o['fade_years'], 'path': 'expectations.growth_path'}, 'reasons': []}
    eq = None
    if mc and mc > 0 and owner is not None and owner > 0:
        g_eq, st_eq = implied_growth(mc / owner, o)
        eq = {'basis': f'market cap / {obasis}', 'short': 'market cap / owner FCF', 'multiple': mc / owner, 'implied_growth': g_eq, 'status': st_eq}
    thin = om is not None and om < A['operating_margin'][1] and (m.get('roic') or 0) < A['roic'][0]
    op = ev is not None and ev > 0 and nopat is not None and nopat > 0 and om is not None and not thin
    fallback = None
    if op:
        mult, basis, short = ev / nopat, 'EV / after-tax operating profit (NOPAT, 21% tax convention)', 'EV / NOPAT'
        out['cross_check'] = eq
    elif eq:
        mult, basis, short = eq['multiple'], eq['basis'], eq['short']
        why = ('EV unknown (net debt or market cap missing)' if ev is None else 'EV <= 0 (net cash at or above market cap)' if ev <= 0 else
               'operating profit unknown' if nopat is None or om is None else 'operating profit <= 0' if nopat <= 0 else
               'operating margin below its weak anchor without strong ROIC (thin operating profit)')
        fallback = f'{why}: price read on {basis}'
    else:
        if owner is not None and owner <= 0:
            out['class'] = 'not_assessable_negative_fcf'
            out['reasons'].append('owner FCF <= 0 and operating profit thin or negative: a cash-flow multiple cannot say what the price '
                                  'requires; deep analysis must run expectations.expectation_gap with company drivers')
        elif ev is not None and ev <= 0:
            out['class'] = 'not_assessable_negative_ev'
            out['reasons'].append('EV <= 0 (net cash above market cap) and owner FCF unknown')
        elif not mc:
            out['class'] = 'not_assessable_no_market_value'
            out['reasons'].append('market value unknown (no market cap or price): no price check at all')
        else:
            out['reasons'].append('EV and operating profit, or market cap and FCF, unknown')
        return out
    g, status = implied_growth(mult, o)
    out.update({'multiple': mult, 'multiple_basis': basis, 'implied_growth': g, 'status': status})
    tol = o['priced_in_tolerance']
    if g >= o['perfection_growth'] or status == 'above_range':
        out.update(level='weak', **{'class': 'priced_for_perfection'})
        out['reasons'].append(f"{short} {mult:.0f}x implies ~{_fmt(g)}/yr for {o['fade_high_years']} years: priced for perfection "
                              '(implied >= 30% halved ~3-4x as often in the 2018-2025 holdout; a crash-risk measure, not a return signal)')
    elif ref is None:
        out.update(level='unknown', **{'class': 'no_reference_growth'})
        out['reasons'].append(f'{short} {mult:.0f}x implies ~{_fmt(g)}/yr; no consensus or demonstrated growth to compare')
    else:
        if ref_kind == 'consensus':
            just = _fade_multiple(ref, o, o['consensus_hold_years'])
            ref_eq = implied_growth(just, o)[0]
            out.update(justified_multiple=just, reference_equivalent_growth=ref_eq)
            ref_txt = (f"{ref_basis} {_fmt(ref)} held {o['consensus_hold_years']} years then fading justifies {just:.0f}x "
                       f"(~{_fmt(ref_eq)}/yr on the {o['fade_high_years']}-year convention)")
        else:
            ref_eq, ref_txt = ref, f'{ref_basis} {_fmt(ref)}'
            out['reference_equivalent_growth'] = ref_eq
        gap = g - ref_eq
        out['gap'] = gap
        cls = 'price_assumes_less_than_evidence' if gap <= -tol else ('roughly_consistent' if gap <= tol else 'price_assumes_more_than_evidence')
        out.update(level={'price_assumes_less_than_evidence': 'strong', 'roughly_consistent': 'adequate'}.get(cls, 'weak'), **{'class': cls})
        out['reasons'].append(f"{short} {mult:.0f}x implies ~{_fmt(g)}/yr growth for {o['fade_high_years']} years then fade to "
                              f"{_fmt(o['terminal_growth'])}; {ref_txt} ({cls.replace('_', ' ')})")
    if fallback:
        out['reasons'].append(fallback)
    if op:
        out['reasons'].append('NOPAT is read as owner cash: heavy reinvestors need more growth than shown'
                              + (f"; cross-check {eq['short']} {eq['multiple']:.0f}x implies ~{_fmt(eq['implied_growth'])}/yr" if eq else ''))
    if m.get('consensus_growth_2y') is not None and m.get('revenue_cagr_3y') is not None and m['consensus_growth_2y'] > m['revenue_cagr_3y'] + .10:
        out['reasons'].append(f"consensus {_fmt(m['consensus_growth_2y'])} far above demonstrated {_fmt(m['revenue_cagr_3y'])}")
    if _consensus_downside(m):
        out['reasons'].append(_consensus_downside(m) + ' (a low multiple may be pricing the slowdown, not a mistake)')
    return out


def _expected_return(c, o):
    m, A = c['m'], c['anchors']
    base, note = (m.get('owner_fcf'), None) if m.get('owner_fcf') is not None else (m.get('fcf_ttm'), 'SBC unknown: yield before SBC (overstated)')
    out = {'level': 'unknown', 'value': None, 'status': 'unknown', 'formula': ER_FORMULA, 'components': {}, 'bounded': [], 'reasons': []}
    mc = m.get('market_cap')
    g0, g0_basis, _ = _reference_growth(c, o)
    if base is None or mc is None or mc <= 0 or g0 is None:
        out['reasons'].append('needs market cap > 0, FCF and a growth estimate')
        return out
    if base <= 0:
        out.update(status='not_meaningful')
        out['reasons'].append('owner FCF <= 0: a yield-based proxy cannot see a J-curve; underwrite in the pipeline instead')
        return out
    y = base / mc
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
    sbc_dil = _div(m.get('sbc_ttm'), mc)
    dil = m.get('dilution_yoy')
    excess = max(0., dil - (sbc_dil or 0)) if dil is not None else None
    er = y + gsb - (excess or 0)
    out.update(value=er, status='computed', level={1: 'strong', 0: 'adequate', -1: 'weak'}[_sig(er, A['expected_return_proxy'])],
               components={'owner_fcf_yield': y, 'yield_basis': '(FCF - SBC) / market cap' if note is None else 'FCF / market cap',
                           'growth_start': g0b, 'growth_start_basis': g0_basis, 'sustainable_growth': gsb,
                           'sbc_implied_dilution': sbc_dil, 'share_count_growth': dil, 'excess_dilution': excess})
    out['reasons'].append(f'{_fmt(y, "pct1")} owner yield on equity + {_fmt(gsb, "pct1")} sustainable growth ({g0_basis} {_fmt(g0)} fading)'
                          + (f' - {_fmt(excess, "pct1")} excess dilution' if excess else '') + f' = {_fmt(er, "pct1")}/yr (rough)')
    if note:
        out['reasons'].append(note)
    if dil is None:
        out['reasons'].append('share count change unknown: dilution not charged')
    out['percentile'] = c['pct'].get('expected_return_proxy')
    return out


def _cyc_label(cy):
    return {'industry_word': f"industry '{cy['word']}'", 'type': f"type {cy['type']}", 'data': 'revenue history',
            'analyst': 'analyst-confirmed'}.get(cy['basis'], 'cyclical')


def _earnings_quality(m):
    """[(code, message)]: accounting red flags (Beneish likely manipulator, analysis accruals flag, CFO / net income < 0.5) and weak
    cash conversion (FCF / net income < 0.5 when CFO / net income is unknown and capex is not known to be heavy)."""
    out = []
    if m.get('beneish_zone') == 'likely_manipulator' or m.get('beneish_high'):
        out.append(('accounting', 'Beneish M-score in the likely-manipulator zone: receivables, accruals and revenue recognition need '
                    'checking (fast growers score high by construction: investigate, do not accuse)'))
    if m.get('accruals_high') or (m.get('accruals_ratio') or 0) > .10:
        out.append(('accounting', f"accruals high (net income above operating cash flow by {_fmt(m.get('accruals_ratio'))} of assets)"))
    iq, conv, cx = m.get('income_quality'), m.get('fcf_conversion'), m.get('capex_to_revenue')
    if iq is not None and (m.get('net_margin') or 0) > 0 and iq < .5:
        out.append(('accounting', f'operating cash flow / net income {iq:.2f}: earnings are not converting to cash'))
    elif iq is None and conv is not None and conv < .5 and (cx is None or abs(cx) <= .10):
        out.append(('cash_conversion', f'FCF / net income {conv:.2f}: accruals, working capital, capitalized costs or capex absorb '
                    'earnings; verify before calling it quality'))
    return out


def _risk(c, o, pi):
    m, flags = c['m'], []
    add = lambda code, sev, msg: flags.append({'code': code, 'severity': sev, 'message': msg})
    nde, cov, zone = m.get('net_debt_to_ebitda'), m.get('interest_coverage'), m.get('altman_zone')
    nd, fcf, run = m.get('net_debt'), m.get('fcf_ttm'), m.get('runway_years')
    if cov == 0 and (m.get('operating_margin') or 0) > 0:
        cov = None                      # EBIT / interest = 0 with EBIT > 0 is a placeholder, never a disqualifier
    if nde is not None and nde > 3.5:
        add('leverage', 'high' if nde > 5 else 'medium', f'net debt / EBITDA {nde:.1f}x')
    if nd is not None and nd > 0 and m.get('ebitda') is not None and m['ebitda'] <= 0:
        add('leverage', 'high', 'net debt with negative EBITDA')
    if nd is None and nde is None:
        add('leverage_unknown', 'low', 'net debt unknown (debt tags or EV missing): leverage not verified')
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
    elif d is None:
        add('dilution_unknown', 'low', 'share count change unknown: dilution to current holders not verified')
    if pi.get('class') == 'priced_for_perfection':
        add('priced_for_perfection', 'medium', 'price requires rare growth (crash-risk flag)')
    pc = pi.get('price_check') or {}
    if pi.get('level') == 'unknown' and pc.get('level') == 'weak':
        add('price_demanding', 'medium', f"priced_in not assessable ({pi['class'].replace('_', ' ')}) and the fallback read is demanding: "
                                         f"{pc['basis']} {_fmt(pc['value'], 'x2')}")
    for code, msg in _earnings_quality(m):
        add(code, 'medium', msg)
    v = m.get('volatility_1y')
    if v is not None and v > c['anchors']['volatility_1y'][1]:
        add('volatility', 'medium', f'annualized volatility {v * 100:.0f}%')
    if m.get('acquisition_driven') is True:
        add('acquired_growth', 'low', 'growth may be bought, not organic')
    cy = c.get('cycle')
    if cy and cy['value'] and cy['peak']:
        add('cyclical_peak', 'medium', f'cyclical ({_cyc_label(cy)}) at a possible peak: ' + '; '.join(cy['signs']))
    elif _consensus_downside(m):
        add('consensus_downside', 'low', _consensus_downside(m))
    if cy and cy['value'] and cy['peak_check'] == 'incomplete':
        add('cyclical_unchecked', 'low', f'cyclical ({_cyc_label(cy)}): no consensus or margin history to test for a peak; missing '
                                         'consensus is not evidence of no peak')
    if c.get('support_flag'):
        add(*c['support_flag'])
    known = any(m.get(k) is not None for k in ('net_debt', 'net_debt_to_ebitda', 'interest_coverage'))   # Altman alone is not leverage data
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
    calls = {k: v for k, v in (row.get('errors') or {}).items() if v != 'invalid_value'}
    bad = sorted(k for k, v in (row.get('errors') or {}).items() if v == 'invalid_value')
    if calls:
        reasons.append('vendor calls failed: ' + ', '.join(f'{k} {v}' for k, v in sorted(calls.items())))
    if bad:
        reasons.append('invalid values read as unknown: ' + ', '.join(bad))
    flags = [f for f in row.get('data_flags') or [] if isinstance(f, dict)]
    crit = [f for f in flags if f.get('severity') == 'critical']
    if crit:            # an implausible core value (a component tagged as revenue, units, a lender's P/E): verify before ranking
        lvl = 'weak'
        reasons.insert(0, 'data check failed: ' + '; '.join(f"{f['code']} ({f['message']})" for f in crit))
    elif any(f.get('severity') == 'major' for f in flags):
        lvl = 'adequate' if lvl == 'strong' else lvl
    reasons += [f"data flag {f['code']}: {f['message']}" for f in flags if f.get('severity') != 'critical']
    denied = sorted(k for k, v in calls.items() if v == 'access_denied')
    if row.get('as_of') and scan_as_of:
        age = (_date(scan_as_of) - _date(row['as_of'])).days
        if age > 120:
            lvl = 'weak' if age > 400 else ('adequate' if lvl == 'strong' else lvl)
            reasons.append(f'data {age} days older than the scan date')
    gap = lvl == 'weak' and cov < .5 and bool(denied)
    if gap:      # a plan denial is a coverage gap, not evidence about the company: say so first
        reasons.insert(0, 'coverage gap (vendor denied: ' + ', '.join(denied) + ')')
    return {'level': lvl, 'coverage': round(cov, 3), 'missing': missing, 'reasons': reasons, 'coverage_gap': gap}


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
        s += (f"; the price implies ~{_fmt(pi['implied_growth'])}/yr growth ({pi['multiple_basis'].split(' (')[0]} {pi['multiple']:.0f}x)"
              + (f" vs {pi['reference_basis']} {_fmt(pi['reference_growth'])}" if pi['reference_growth'] is not None else ''))
    pc = pi['price_check']
    if pi['level'] == 'unknown':
        s += (f"; priced_in not assessable ({pi['class'].replace('_', ' ')})"
              + (f": fallback {pc['basis']} {_fmt(pc['value'], 'x2')} reads {pc['level']} (crude)" if pc['source'] == 'fallback'
                 else ': price unassessed'))
    if er['value'] is not None:
        s += f"; return proxy {_fmt(er['value'], 'pct1')}/yr (rough)"
    return s


PI_RANK = {'strong': 0, 'adequate': 1, 'unknown': 2, 'unassessed': 2, 'weak': 3}
BALANCE_CODES = ('leverage', 'interest_coverage', 'funding')
EARNINGS_CODES = ('accounting', 'cash_conversion')


def _price_state(c):
    """(rank, tags) of the price check for lens ordering: 0 assessed and not demanding, 1 unassessed, 2 demanding (priced for
    perfection, or the fallback multiple reads weak)."""
    pi = c['dims']['priced_in']
    pc = pi['price_check']
    if pi['class'] == 'priced_for_perfection':
        return 2, ['priced_for_perfection']
    if pi['level'] != 'unknown':
        return 0, []
    if pc['level'] == 'unassessed':
        return 1, ['price_unassessed']
    return (2, ['price_demanding']) if pc['level'] == 'weak' else (0, ['price_fallback'])


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
    if _recovery(m):
        out.append(('recovery', False, _recovery(m)))
    dd = m.get('drawdown_from_high')
    # the price path never admits a name alone: a drawdown with intact quality lowers the bar from two improvement signals
    # (one of them profit/cash) to one, and is shown as context (policy.price_path_used_for_selection)
    over = (dd is not None and dd <= o['drawdown_overreaction'] and d['business_quality']['level'] in ('strong', 'adequate')
            and d['growth_quality']['level'] in ('strong', 'adequate') and bool(out))
    return out, over


def _losses_narrowing(m, margin, change):
    """True when a loss/burn is not widening in dollars (or the margin is >= 0), False when it widens, None when unknown."""
    v, dv, rev, g = m.get(margin), m.get(change), m.get('revenue_ttm'), m.get('revenue_growth')
    if v is None or v >= 0:
        return True
    if dv is not None and rev and g is not None and 1 + g > 0:
        return v * rev >= (v - dv) * rev / (1 + g)
    if margin == 'operating_margin' and m.get('incremental_operating_margin') is not None:
        return m['incremental_operating_margin'] > 0
    return None


def _lens(name, c, o):
    """-> (in_scope, conditions [(name, ok, detail)], ordering [(label, value, asc|desc)], why, tags)."""
    m, d = c['m'], c['dims']
    A = c['anchors']
    bq, gq, fs, val, pi, er = (d[k]['level'] for k in ('business_quality', 'growth_quality', 'financial_strength', 'valuation', 'priced_in',
                                                         'expected_return_proxy'))
    erv, gl = d['expected_return_proxy']['value'], m.get('growth_level')
    flags = d['risk']['flags']
    fs_c = ('financial_strength', fs != 'weak', f'financial strength weak: {_neg(d["financial_strength"])}')
    hard = [f['message'] for f in flags if f['code'] in BALANCE_CODES and f['severity'] == 'high']
    bs_c = ('balance_sheet', not hard, 'high-severity balance-sheet flag: ' + '; '.join(hard))
    eq_bad = [f['message'] for f in flags if f['code'] in EARNINGS_CODES]
    eq_c = ('earnings_quality', not eq_bad, 'earnings quality: ' + '; '.join(eq_bad))
    cy = c.get('cycle')
    cstate, cyc_t = _cyc_state(c)
    crank = CYC_RANK[cstate]
    cyc_c = ('not_cyclical_peak', cstate not in ('possible_peak', 'incomplete'),
             (f"cyclical ({_cyc_label(cy)}) at a possible peak ({'; '.join(cy['signs'])}): peak margins and FCF make the price look lower "
              'than through-cycle economics' if cstate == 'possible_peak' else
              f"cyclical ({_cyc_label(cy) if cy else ''}): peak check incomplete (no consensus or margin history): cannot show margins are not "
              'at a peak; supply consensus, margin history or an analyst statement (options.cyclical)') if cy else '')
    prank, ptags = _price_state(c)
    dil = m.get('dilution_yoy')
    dil_txt = lambda lim: ('share count change unknown: growth per share and dilution to current holders not verified' if dil is None
                           else f'share count +{_fmt(dil)}/yr (limit {_fmt(lim)}): growth paid for with current holders\' ownership')
    if name == 'quality_compounder':
        scope = gl is not None and gl >= A['growth_level'][0]
        om, roic = m.get('operating_margin'), m.get('roic')
        prof = (om is not None and om >= A['operating_margin'][1]) or (roic is not None and roic >= A['roic'][1])
        dil_hi = any(f['code'] == 'dilution' and f['severity'] == 'high' for f in flags)
        conds = [('business_quality', bq in ('strong', 'adequate'), f'fast growth without demonstrated quality ({bq}): {_neg(d["business_quality"])}'),
                 ('profitable', prof, f"operating margin {_fmt(om)} and ROIC {_fmt(roic)} below their weak anchors: loss-making growth "
                  'belongs in early_commercial_growth'),
                 ('growth_quality', gq == 'strong', f'growth quality {gq}, needs strong: {_neg(d["growth_quality"])}'),
                 ('per_share', dil is not None and dil <= .05 and not dil_hi, dil_txt(.05)), fs_c, bs_c, eq_c]
        order = [('cyclical peak check (0 none or through-cycle, 1 incomplete, 2 possible peak)', crank, 'asc'),
                 ('quality tier (0 strong, 1 adequate)', 0 if bq == 'strong' else 1, 'asc'),
                 ('price check (0 assessed, 1 unassessed, 2 demanding or priced for perfection)', prank, 'asc'),
                 ('expected-return proxy', erv, 'desc')]
        why = (f"Revenue grows {_fmt(gl)}/yr with {gq} growth quality and {bq} business quality ({_quality_phrase(c)}). Price check: {_price_phrase(c)}"
               + ('. Priced for perfection: high growth already assumed.' if 'priced_for_perfection' in ptags else
                  '. Demanding price on the fallback multiple.' if 'price_demanding' in ptags else '.'))
        return scope, conds, order, why, ptags + cyc_t
    if name == 'quality_fair_price':
        traj = gq in ('strong', 'adequate') and (d['growth_quality']['inputs']['growth_level']['signal'] or 0) >= 0
        lev_known = m.get('net_debt') is not None or m.get('net_debt_to_ebitda') is not None
        conds = [('valuation', val in ('strong', 'adequate'), f'quality but expensive: {_neg(d["valuation"])}'),
                 ('priced_in', pi in ('strong', 'adequate'), f"price assumption {d['priced_in']['class'].replace('_', ' ')}: "
                  + '; '.join(d['priced_in']['reasons'][:1])),
                 ('trajectory', traj, f'growth quality {gq}, revenue growth {_fmt(gl)}/yr: {_neg(d["growth_quality"])} (cannot rule out '
                  'deterioration; buybacks do not offset shrinking revenue)'),
                 fs_c, ('leverage_known', lev_known, 'leverage unknown (net debt not in the data: debt tags or EV missing): the balance '
                                                     'sheet is not verified'), bs_c, eq_c,
                 ('expected_return', er in ('strong', 'adequate'), f"return proxy {er}: {'; '.join(d['expected_return_proxy']['reasons'][:1])}"),
                 cyc_c]
        order = [('growth-quality tier (0 strong, 1 adequate)', 0 if gq == 'strong' else 1, 'asc'), ('expected-return proxy', erv, 'desc'),
                 ('business-quality percentile', d['business_quality']['percentile'], 'desc')]
        why = f"Strong business ({_quality_phrase(c)}) with {gq} growth ({_fmt(gl)}/yr) at a price rated {val}: {_price_phrase(c)}."
        return bq == 'strong', conds, order, why, cyc_t
    if name == 'early_commercial_growth':
        om, nm, fm, gm = m.get('operating_margin'), m.get('net_margin'), m.get('fcf_margin'), m.get('gross_margin')
        stage = m.get('stage') or m.get('stage_pit')
        ev_early = [x for x, ok in (('net loss', nm is not None and nm <= 0), ('operating loss', om is not None and om < 0),
                                    ('cash burn', fm is not None and fm < 0), (f'stage {stage}', stage in ('early_commercial', 'turning_profitable')),
                                    (f'operating margin {_fmt(om)} below 10% with gross margin {_fmt(gm)}',
                                     om is not None and om < .10 and (gm is None or gm >= .40))) if ok]
        g = max(x for x in (m.get('revenue_growth'), gl, -1) if x is not None)
        scope = bool(ev_early) and g >= o['early_growth_min']
        proofs = []
        if (gm or 0) >= .40 or (m.get('gross_margin_change') or 0) >= .02:
            proofs.append(f"unit economics: gross margin {_fmt(gm)} ({_fmt(m.get('gross_margin_change'), 'pts')})")
        if ((m.get('operating_margin_trend') or 0) >= .02 or ((m.get('incremental_operating_margin') or -9) > (om or 0) + .05)) \
                and _losses_narrowing(m, 'operating_margin', 'operating_margin_trend'):
            proofs.append(f"operating leverage: operating margin {_fmt(m.get('operating_margin_trend'), 'pts')}")
        if ((m.get('fcf_margin_change') or 0) >= .03 and _losses_narrowing(m, 'fcf_margin', 'fcf_margin_change')) or (fm is not None and fm >= 0):
            proofs.append(f"cash trajectory: FCF margin {_fmt(fm)} ({_fmt(m.get('fcf_margin_change'), 'pts')})")
        ser = [x for x in m.get('revenue_growth_series') or [] if x is not None]
        if _consistency(ser) == 1 or (len(ser) >= 2 and min(ser) > .15):
            proofs.append(f"repeat growth: {_fmt(ser, 'list')}")
        widen = [k for k, mg, ch in (('operating loss', 'operating_margin', 'operating_margin_trend'), ('FCF burn', 'fcf_margin', 'fcf_margin_change'))
                 if _losses_narrowing(m, mg, ch) is False]
        run = m.get('runway_years')
        funded = (fm is not None and fm >= 0) or (run is not None and run >= 2)
        conds = [('commercial_proof', len(proofs) >= 3, f'growth without enough commercial proof ({len(proofs)} of 4: {"; ".join(proofs) or "none"}); '
                  f"gross margin {_fmt(gm)}, operating margin trend {_fmt(m.get('operating_margin_trend'), 'pts')}, FCF margin {_fmt(fm)}"
                  + (f"; {' and '.join(widen)} widening in dollars: margin gains on a small base are not proof" if widen else '')),
                 ('dilution', dil is not None and dil <= .10, dil_txt(.10)),
                 ('no_distress', not d['risk']['disqualifying'] and not any(f['code'] == 'distress' for f in flags), 'distress flag')]
        order = [('commercial-proof count', len(proofs), 'desc'), ('funding (0 funded, 1 conditional)', 0 if funded else 1, 'asc'),
                 ('cyclical peak check (0 none or through-cycle, 1 incomplete, 2 possible peak)', crank, 'asc'),
                 ('price check (0 assessed, 1 unassessed, 2 demanding)', prank, 'asc'), ('per-share growth', m.get('per_share_growth'), 'desc')]
        why = (f"Grows {_fmt(g)}/yr at an earlier stage ({'; '.join(ev_early)}) with commercial proof: {'; '.join(proofs)}. "
               + ('Funded by own cash flow or runway.' if funded else f"Funding-conditional (runway {_fmt(run, 'yrs')}, dilution {_fmt(dil)}/yr).")
               + f' Price check: {_price_phrase(c)}.'
               + ' Paid adoption and cohorts are not in screening data: verify.')
        return scope, conds, order, why, ['funded' if funded else 'funding_conditional'] + ptags + cyc_t
    if name == 'mispriced_turning':
        imp, over = _improvements(c, o)
        dd = m.get('drawdown_from_high')
        scope = bool(imp) or (dd is not None and dd <= o['drawdown_overreaction'])
        evid = (len(imp) >= 2 and any(p for _, p, _ in imp)) or over
        price_ok = pi == 'strong' or (pi == 'unknown' and val == 'strong') or (over and pi != 'weak')
        conds = [('improvement_evidence', evid, f"one improvement signal is not a turn ({'; '.join(t for _, _, t in imp) or 'none'})"
                  + ('' if dd is None else f"; drawdown {_fmt(dd)} with quality {bq}/growth {gq} is context, not evidence: a fundamental "
                     'improvement signal is required')),
                 ('not_priced_in', price_ok, f"the price does not lag the improvement: priced_in {pi} ({d['priced_in']['class'].replace('_', ' ')}), valuation {val}"),
                 ('dilution', dil is not None and dil <= .08, dil_txt(.08)), fs_c, bs_c, cyc_c]
        order = [('improvement-signal count', len(imp) + int(over), 'desc'), ('priced_in rank (0 strong .. 3 weak)', PI_RANK[pi], 'asc'),
                 ('expected-return proxy', erv, 'desc')]
        why = ((f"Improving: {'; '.join(t for _, _, t in imp)}. " if imp else '')
               + (f"Down {_fmt(-dd)} from its high with {bq} quality and {gq} growth: possible overreaction, test temporary vs structural. " if over else '')
               + f"Price check: {_price_phrase(c)}.")
        return scope, conds, order, why, (['overreaction_candidate'] if over else []) + [k for k, _, _ in imp] + cyc_t
    if name == 'structural_tailwind':
        themes = (o['themes'] or {}).get(c['row']['symbol']) or []
        ind = c['row'].get('industry') in (o['structural_industries'] or [])
        sp = c['spct'].get('growth_level')
        footprint = sp is not None and sp >= .75 and (m.get('gross_margin_change') is None or m['gross_margin_change'] >= -.01)
        scope = bool(themes) or ind or footprint
        cg = m.get('revenue_cagr_3y', gl)
        heavy = [f['message'] for f in flags if f['code'] in ('funding', 'dilution') and f['severity'] == 'high']
        pc = d['priced_in']['price_check']
        theme_ok = (bool(themes) or ind) and bool(m.get('market_cap')) and pc['level'] in ('strong', 'adequate')
        unpriced = pi != 'weak' and (pi != 'unknown' or theme_ok)
        pi_why = '; '.join(d['priced_in']['reasons'][:1])
        if pi == 'weak':
            np_txt = 'potential appears already priced: ' + pi_why
        elif not m.get('market_cap'):
            np_txt = 'market value unknown (no market cap or price): no price check at all, so the potential cannot be shown to be unpriced'
        elif themes or ind:
            np_txt = (f"price check unavailable ({pi_why}); fallback {pc['basis']} reads {pc['level']}: a theme is not a price check")
        else:
            np_txt = (f'numbers only and the price check is unavailable ({pi_why}): cannot show the potential is not already priced; '
                      'supply an analyst theme or run expectations.expectation_gap with company drivers')
        conds = [('durable_growth', cg is not None and cg >= .08 and _consistency(m.get('revenue_growth_series')) != -1,
                  f'growth {_fmt(cg)} 3y or repeated declines: no durable footprint yet'),
                 ('business_quality', bq != 'weak', f'economics weak: {_neg(d["business_quality"])}'),
                 ('not_already_priced', unpriced, np_txt),
                 ('per_share_funding', (dil is not None and dil <= .10) and not heavy,
                  (dil_txt(.10) if dil is None or dil > .10 else f'share count {_fmt(dil)}/yr') + (f"; {'; '.join(heavy)}" if heavy else '')
                  + ': the tailwind would accrue to new shareholders, not current ones'), fs_c, bs_c]
        order = [('cyclical peak check (0 none or through-cycle, 1 incomplete, 2 possible peak)', crank, 'asc'),
                 ('price rank (0 strong .. 3 weak; fallback read when priced_in is unknown)', PI_RANK[pi if pi != 'unknown' else pc['level']], 'asc'),
                 ('expected-return proxy', erv, 'desc'), ('growth level', gl, 'desc')]
        org = d['growth_quality']['inputs']['organic']['signal']
        src = ('analyst theme: ' + ', '.join(t if isinstance(t, str) else t.get('theme', '?') for t in themes) if themes else
               ('structural industry (analyst list)' if ind else
                f'growth in the top quartile of its sector (percentile {sp})' + (': an improving competitive position' if org is not None and org >= 0
                                                                                  else '; organic share unknown (acquisitions not in the data): verify before calling it share gain')))
        why = (f"{src} (a hypothesis, not evidence). Footprint: {_fmt(cg)}/yr growth, {bq} business quality. "
               f"Already priced? {_price_phrase(c)}.")
        return scope, conds, order, why, ['analyst_theme' if themes or ind else 'numeric_footprint'] + ptags + cyc_t
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
    for k in ('top_n', 'sector_cap', 'shortlist_sector_cap'):
        if o[k] is not None and (isinstance(o[k], bool) or not isinstance(o[k], int) or o[k] < 1):
            raise ValueError(f'{k} must be a positive integer' + ('' if k == 'top_n' else ' or None'))
    for sym, ov in (o['cyclical'] or {}).items():
        if not isinstance(ov, dict) or not isinstance(ov.get('value'), bool) or not isinstance(ov.get('reason'), str) or not ov['reason'].strip():
            raise ValueError(f'cyclical.{sym} needs a boolean value and a written reason (an analyst statement, kept next to the computed flag)')
    for sym, why in (o['supported_override'] or {}).items():
        if not isinstance(why, str) or not why.strip():
            raise ValueError(f'supported_override.{sym} needs a written reason (e.g. fee business: no loans, deposits or float)')
    if not isinstance(o['unsupported_defaults'], bool):
        raise ValueError('unsupported_defaults must be true or false')
    a = {**ANCHORS, **(options.get('anchors') or {})}
    for k, v in a.items():
        if len(v) != 3 or v[2] not in ('higher', 'lower'):
            raise ValueError(f'anchor {k} must be (strong, weak, higher|lower)')
    o['anchors'] = {k: [v[0], v[1], v[2]] for k, v in sorted(a.items())}
    if o['terminal_growth'] >= o['discount_rate']:
        raise ValueError('terminal_growth must be below discount_rate')
    return o


def _unsupported(row, o):
    """Reason the row's economics are not screened, or None (see _support)."""
    return _support(row, o)[0]


def _support(row, o):
    """(reason or None, low/medium risk flag or None). Balance-sheet economics are not screened: XBRL bank/insurer markers, the
    industry list (UNSUPPORTED_INDUSTRIES plus options.unsupported_industries, compared after clean_text), and model_card's text
    rules typing the industry (the sector when no industry is given) as financials or REIT. options.unsupported_industries ADDS
    labels; options.unsupported_defaults=False is the explicit switch that drops the built-in list and text rules. Labels that list
    fee businesses with balance-sheet firms (model_card.BALANCE_MIXED_RULES) are decided by the row's balance sheet when it has one;
    without it 'asset management' stays screenable with a flag and the others need options.supported_override {symbol: reason}."""
    ind = row.get('industry') or ''
    m, sym = row.get('metrics') or {}, row.get('symbol')
    why, flag = None, None
    over = (o['supported_override'] or {}).get(sym)
    user = {clean_text(x) for x in o['unsupported_industries'] or []}
    defaults = {clean_text(x) for x in UNSUPPORTED_INDUSTRIES} if o['unsupported_defaults'] else set()
    t = text_type({'industry': row.get('industry'), 'sector': row.get('sector')}) if o['unsupported_defaults'] else None
    lta = m.get('liabilities_to_assets')
    lender = lta is not None and lta >= LENDER_LIABILITIES and (m.get('gross_margin') or 0) <= 0
    prof = row.get('sec_profile') or {}
    if m.get('financial_statements'):
        why = 'bank/insurer XBRL markers'
    elif prof.get('balance_sheet_economics'):
        why = f"SEC SIC {prof.get('sic')} {prof.get('sic_description')}: {prof['balance_sheet_economics']}"
    elif prof.get('fee_flag'):
        flag = ('fiduciary_balance_sheet', 'low', f"SEC SIC {prof.get('sic')}: {prof['fee_flag']}")
    elif clean_text(ind) in user:
        why = f"industry '{ind}' in options.unsupported_industries"
    elif clean_text(ind) in defaults:
        why = f"industry '{ind}'"
    elif t and t.get('signature') == 'balance_sheet':
        if lta is not None:
            why = (f"'{t['text']}' lists {t['mixes']}; liabilities {_fmt(lta)} of assets with no gross margin: lender-like" if lender else None)
            if not lender:
                flag = ('balance_sheet_checked', 'low', f"'{t['text']}' lists {t['mixes']}; liabilities {_fmt(lta)} of assets: not lender-like, "
                        'screened as a fee business')
        elif t['keyword'] in SCREEN_MIXED_SUPPORTED:
            flag = ('balance_sheet_unverified', 'medium', f"'{t['text']}' lists {t['mixes']}; no balance sheet in this row: a custody bank or "
                    'lender would be mis-read by FCF/EV metrics; model_card decides from the balance sheet')
        else:
            why = (f"'{t['text']}' lists {t['mixes']} and this row has no balance sheet (conservative); a fee business can be admitted with "
                   'options.supported_override {symbol: reason}')
    elif t and t['type'] in UNSUPPORTED_TYPES:
        why = f"{t['field']} '{t['text']}' matches '{t['keyword']}' -> {t['type']} (model_card text rules)"
    elif t and t['keyword'] == 'insurance broker':
        flag = ('fiduciary_balance_sheet', 'low', 'insurance broker: client funds held may inflate liabilities and cash; exclude fiduciary '
                'balances from net debt and EV')
    if why and over and not m.get('financial_statements') and not lender:
        return None, ('supported_override', 'low', f'admitted by options.supported_override ({over}); computed: {why}')
    if why:
        return (f'bank/insurer/REIT-type economics ({why}): FCF, EV and gross-margin metrics do not describe them; needs a '
                'sector-specific screen (book value/ROE, FFO). Not relabelled to force a generic model.'), None
    return None, flag


def _clean_rows(rows):
    """Validate rows; metric values of the wrong type (a vendor placeholder string, a boolean in a numeric field) become unknown and
    are named in the row's errors ('invalid_value'), so one bad value never aborts the scan."""
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
        m, errors = {}, dict(r.get('errors') or {})
        for k, v in (r.get('metrics') or {}).items():
            if v is None:
                continue
            if k in STR_METRICS:
                ok = isinstance(v, str) and (STR_METRICS[k] is None or v in STR_METRICS[k])
                val = v if ok else None
            elif k in BOOL_METRICS:
                val = v if isinstance(v, bool) else None
            elif k in LIST_METRICS:
                val = [_f(x) for x in v] if isinstance(v, list) and not any(isinstance(x, (bool, str)) for x in v) else None
            else:
                val = None if isinstance(v, (bool, str, list, dict)) else _f(v)
                if val is None and _f(v) is None and not isinstance(v, (bool, str, list, dict)):
                    continue                      # NaN / inf: unknown, as before (not a type error)
            if val is None:
                errors[f'metric {k}'] = 'invalid_value'
            else:
                m[k] = val
        status, reason = r.get('status') or 'ok', r.get('no_data_reason')
        if status == 'ok' and not any(k in m for k in CORE_METRICS):
            status, reason = 'no_data', 'no usable metrics after removing nulls, invalid and non-finite values'
        out.append({**r, 'metrics': m, 'basis': dict(r.get('basis') or {}), 'errors': errors, 'status': status, 'no_data_reason': reason})
    return out


def _dedupe_key(r):
    """Duplicate rows for one symbol: ok before no data, better grade, latest as_of, fewer vendor errors, more metrics, then the row
    digest, so the kept row never depends on input order."""
    day = _date(r['as_of']).toordinal() if r.get('as_of') else 0
    return (r['symbol'], r['status'] != 'ok', GRADE_ORDER.index(r['data_grade']), -day, len(r['errors']), -len(r['metrics']), F.digest(r))


def _value_trap(c):
    d, m = c['dims'], c['m']
    return d['valuation']['level'] == 'strong' and (d['growth_quality']['level'] == 'weak' or (
        (m.get('gross_margin_change') or 0) < -.02 and (m.get('operating_margin_trend') or 0) < -.02))


DATA_TAGS = ('leverage_unknown', 'dilution_unknown', 'balance_sheet_unverified', 'fiduciary_balance_sheet', 'supported_override')


def scan(rows, options=None):
    """Universe rows (screen.rows_from_* adapters) -> dimensions, lens candidates, shortlist, exclusions, notes.

    Deterministic and order-independent (ties break on symbol; duplicate rows resolve on grade, as_of, errors and coverage, never
    on input order). options: see DEFAULTS; anchors override ANCHORS. The output is a research agenda, never a recommendation."""
    o = _options(options)
    rows = _clean_rows(rows)
    best, notes = {}, []
    for r in sorted(rows, key=_dedupe_key):
        k = best.get(r['symbol'])
        if k is not None:
            desc = lambda x: f"{x['data_grade']} as_of {x.get('as_of')} ({x['status']}, {len(x['errors'])} vendor error(s), {len(x['metrics'])} metrics)"
            conflict = (k.get('as_of') == r.get('as_of') and k['data_grade'] == r['data_grade'] and k['status'] == r['status'] == 'ok'
                        and any(k['metrics'].get(x) != r['metrics'].get(x) for x in set(k['metrics']) & set(r['metrics'])))
            notes.append(f"duplicate rows for {r['symbol']}: kept {desc(k)}, dropped {desc(r)}"
                         + ('; CONFLICT: same-date rows disagree on shared metrics, check the source' if conflict else ''))
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
        ctx[s]['cycle'] = _cycle(ctx[s], o)
    unsupported = {}
    for s, c in ctx.items():
        unsupported[s], c['support_flag'] = _support(c['row'], o)
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
        pi['price_check'] = _price_check(c['m'], c['anchors'], pi)
        _peak(c, o, pi)
        c['dims'] = {'business_quality': _business_quality(c), 'growth_quality': _growth_quality(c),
                     'financial_strength': _financial_strength(c), 'valuation': _valuation(c), 'expected_return_proxy': c['er'],
                     'priced_in': pi, 'risk': _risk(c, o, pi), 'data_uncertainty': _data_uncertainty(c, as_of)}
        r = c['row']
        dims[s] = {'profile': {'name': r.get('name'), 'sector': r.get('sector'), 'industry': r.get('industry'), 'market_cap': c['m'].get('market_cap'),
                               'data_grade': r['data_grade'], 'source': r.get('source'), 'as_of': r.get('as_of'), 'stage': c['m'].get('stage'),
                               'cyclical': c['cycle']},
                   **c['dims'], 'metrics': c['m'], 'basis': c['b']}
    disq, gaps = {}, []
    for s, c in ctx.items():
        du = c['dims']['data_uncertainty']
        if unsupported[s]:
            disq[s] = 'not supported: ' + unsupported[s]
        elif du['level'] == 'weak':
            disq[s] = ('coverage gap, not evidence about the company: ' if du['coverage_gap'] else 'unreliable data: ') + '; '.join(du['reasons'][:4])
            if du['coverage_gap']:
                gaps.append(s)
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
                codes = {f['code'] for f in c['dims']['risk']['flags']}
                tags = list(dict.fromkeys(tags + [t for t in DATA_TAGS if t in codes] + (['possible_value_trap'] if _value_trap(c) else [])))
                passed.append((_sortkey(order, s), s, order, why, tags))
            elif len(fails) == 1:
                nm.append({'symbol': s, 'failed': fails[0][0], 'reason': fails[0][2]})
            else:
                scoped.setdefault(s, []).append((ln, fails))
        passed.sort()
        cand, per = [], {}
        for _, s, order, why, tags in passed:
            row = ctx[s]['row']
            sec = row.get('sector') or row.get('industry')       # no sector: industry; neither: no cap (never one 'unknown' bucket)
            if o['sector_cap'] and sec and per.get(sec, 0) >= o['sector_cap']:
                nm.append({'symbol': s, 'failed': 'sector_cap', 'reason': f"sector cap: {per[sec]} higher-ranked {sec} names already in this lens "
                                                                         f"(cap {o['sector_cap']}{'' if row.get('sector') else '; no sector given, industry used'}); "
                                                                         'qualifies otherwise'})
                continue
            if sec:
                per[sec] = per.get(sec, 0) + 1
            c = ctx[s]
            cand.append({'rank': len(cand) + 1, 'symbol': s, 'name': c['row'].get('name'), 'sector': c['row'].get('sector'),
                         'industry': c['row'].get('industry'), 'why': why,
                         'ordering': {'fields': [{'name': lab, 'value': v, 'order': way} for lab, v, way in order], 'meaning': LENS_RULES[ln]['ordering']},
                         'levels': {k: c['dims'][k]['level'] for k in DIMENSIONS}, 'tags': tags, 'data_grade': c['row']['data_grade'],
                         'risk_flags': [f['code'] for f in c['dims']['risk']['flags']]})
        lenses[ln], near[ln] = cand, sorted(nm, key=lambda x: x['symbol'])
    shortlist, seen, skipped = _shortlist(lenses, o)
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
        excluded[s] = _exclusion(s, c, disq, ranks.get(s), nearest.get(s), scoped.get(s), o, skipped.get(s))
    conc = {}
    for e in shortlist:
        k = e['sector'] or e.get('industry') or 'unlabelled'
        conc[k] = conc.get(k, 0) + 1
    return {'screen_version': SCREEN_VERSION, 'as_of': as_of,
            'universe': {'definition': o['universe'], 'rows': len(best), 'with_data': len(ctx), 'no_data': len(no_data),
                         'coverage_gaps': len(gaps), 'not_supported': sum(1 for v in unsupported.values() if v), 'eligible': len(ctx) - len(disq),
                         'data_grades': {g: sum(1 for c in ctx.values() if c['row']['data_grade'] == g) for g in GRADE_ORDER}},
            'dimensions': dims, 'dimension_meanings': MEANINGS, 'lenses': lenses, 'lens_rules': LENS_RULES, 'near_misses': near,
            'shortlist': shortlist, 'shortlist_concentration': dict(sorted(conc.items())),
            'excluded': dict(sorted(excluded.items())), 'no_data': no_data,
            'notes': notes + _notes(o, ctx, no_data, gaps, conc, skipped), 'options': o,
            'policy': {'is_recommendation': False, 'orders_supported': False, 'valuation_gap_used_for_ranking': False,
                       'momentum_used_for_selection': False,
                       'price_path_used_for_selection': ('only as context: a >= 30% drawdown with intact quality lowers mispriced_turning '
                                                         'evidence from two improvement signals to one; never admits a name without a '
                                                         'fundamental improvement signal; volatility never selects'),
                       'size_or_growth_preference': False, 'single_composite_score': False},
            'input_sha256': F.digest({'rows': [best[s] for s in sorted(best)], 'options': o})}


def _shortlist(lenses, o):
    """'distinct' (default): round-robin over lenses, each contributing up to top_n names not already taken, so a name that
    leads several lenses does not crowd out other opportunity types and no lens is privileged by its position.
    'union': the literal top_n of every lens, deduplicated. Every lens rank of a shortlisted name is recorded.
    options.shortlist_sector_cap (None = no cap) limits names per sector on the whole shortlist; a skipped name is recorded."""
    if o['shortlist_mode'] not in ('distinct', 'union'):
        raise ValueError("shortlist_mode must be 'distinct' or 'union'")
    cap, per, skipped = o['shortlist_sector_cap'], {}, {}
    sec_of = lambda cd: cd['sector'] or cd.get('industry')

    def capped(cd, ln):
        k = sec_of(cd)
        if cap and k and per.get(k, 0) >= cap:
            skipped.setdefault(cd['symbol'], (ln, k))
            return True
        return False

    def take(cd, ln):
        taken.add(cd['symbol']); picks.append((cd['symbol'], ln))
        if sec_of(cd):
            per[sec_of(cd)] = per.get(sec_of(cd), 0) + 1
    picks, taken, cursor, count = [], set(), {ln: 0 for ln in LENSES}, {ln: 0 for ln in LENSES}
    if o['shortlist_mode'] == 'union':
        for ln in LENSES:
            for cd in lenses[ln][:o['top_n']]:
                if cd['symbol'] not in taken and not capped(cd, ln):
                    take(cd, ln)
    else:
        progress = True
        while progress:
            progress = False
            for ln in LENSES:
                cands = lenses[ln]
                while count[ln] < o['top_n'] and cursor[ln] < len(cands) and (cands[cursor[ln]]['symbol'] in taken or capped(cands[cursor[ln]], ln)):
                    cursor[ln] += 1
                if count[ln] < o['top_n'] and cursor[ln] < len(cands):
                    take(cands[cursor[ln]], ln); count[ln] += 1; progress = True
    shortlist, seen = [], {}
    for s, ln in picks:
        cd = next(c for c in lenses[ln] if c['symbol'] == s)
        seen[s] = {'symbol': s, 'name': cd['name'], 'sector': cd['sector'], 'industry': cd.get('industry'), 'data_grade': cd['data_grade'],
                   'selected_by': ln, 'lenses': [{'lens': x, 'rank': c['rank'], 'why': c['why']} for x in LENSES for c in lenses[x] if c['symbol'] == s],
                   'levels': cd['levels'], 'risk_flags': cd['risk_flags'], 'tags': cd['tags']}
        shortlist.append(seen[s])
    return shortlist, seen, {s: v for s, v in skipped.items() if s not in seen}


def _check_first(c, lenses):
    m, d = c['m'], c['dims']
    out = []
    if c['row']['data_grade'] != 'verified':
        out.append(f"Reconcile {c['row']['data_grade']}-grade inputs to filings (dataqa: shares, cash, debt, TTM revenue/CFO) before any price conclusion.")
    out += [LENS_CHECKS[x] for x in lenses]
    codes = {f['code'] for f in d['risk']['flags']}
    if 'priced_for_perfection' in codes or 'price_demanding' in codes:
        out.append('The price requires rare growth: run company-driver expectations (expectations.expectation_gap) and the bear case first.')
    if 'dilution' in codes or (m.get('dilution_yoy') or 0) > .04:
        out.append('Share count path: SBC, issuance, converts and warrants (capital.py) and dilution to current holders.')
    if 'dilution_unknown' in codes:
        out.append('Share count change is not in the data: establish dilution to current holders before reading growth per share.')
    if 'funding' in codes:
        out.append('Funding: runway to the next milestone and the financing the plan needs (finance.funding_path).')
    if 'leverage' in codes or 'interest_coverage' in codes:
        out.append('Debt maturities, covenants and refinancing cost.')
    if 'leverage_unknown' in codes:
        out.append('Leverage is not in the data (debt tags or EV missing): establish debt, leases and converts before any price read.')
    if 'distress' in codes:
        out.append('Altman distress zone: confirm or dismiss (retained earnings distorted by buybacks?).')
    org = d['growth_quality']['inputs']['organic']['signal']
    if m.get('acquisition_driven') or (m.get('acquisitions_pct_revenue') or 0) > .10:
        out.append('Organic versus acquired growth.')
    elif org is None and (m.get('growth_level') or 0) >= c['anchors']['growth_level'][0]:
        out.append('Organic versus acquired growth: acquisition spend and goodwill are not in this row, so bought growth cannot be ruled out.')
    if d['business_quality']['inputs']['fcf_conversion']['signal'] == -1 or codes & set(EARNINGS_CODES):
        out.append('Earnings quality: accruals, working capital, capitalized costs' + (' (an accounting red flag is raised: '
                   + '; '.join(f['message'] for f in d['risk']['flags'] if f['code'] in EARNINGS_CODES) + ')' if codes & set(EARNINGS_CODES) else '.'))
    if d['priced_in']['class'] == 'not_assessable_negative_fcf':
        out.append('What the price requires cannot be read from a cash-flow multiple: build the company-driver forecast.')
    cy = c.get('cycle')
    if cy and cy['value']:
        out.append(f"Cyclical ({_cyc_label(cy)}; peak check {cy['peak_check']}): judge margins and FCF through the cycle, not the latest "
                   'year (model_card normalizes when the history shows a cycle, when a flagged industry sits at a margin peak, or when an '
                   'analyst confirms one).')
    if 'balance_sheet_unverified' in codes or 'fiduciary_balance_sheet' in codes:
        out.append('Balance sheet: confirm the liabilities are fee-business (fiduciary) balances, not lending or underwriting funding (model_card).')
    return list(dict.fromkeys(out))


def _exclusion(s, c, disq, ranked, nm, scoped, o, skipped=None):
    if s in disq:
        return disq[s]
    d, m = c['dims'], c['m']
    lv = lambda k: d[k]['level']
    scoped_txt = [f"in scope of {ln} but failed {len(fails)} conditions ({' | '.join(n + ': ' + t.split(';')[0] for n, _, t in fails)})"
                  for ln, fails in scoped or []]
    if ranked:
        return (f"ranked #{ranked[0]} in {ranked[1]} (shortlist takes up to {o['top_n']} names per lens, {o['shortlist_mode']} mode"
                + (f"; skipped by the shortlist sector cap: {skipped[1]} already holds {o['shortlist_sector_cap']} names" if skipped else '') + ')'
                + ('; ' + '; '.join(scoped_txt) if scoped_txt else ''))
    if nm:
        return f"near miss in {nm[0]}: {nm[1]['reason']}" + ('; also ' + '; '.join(scoped_txt) if scoped_txt else '')
    tags = []
    if _value_trap(c):
        tags.append('cheap but deteriorating (possible value trap)')
    if lv('business_quality') == 'strong' and (lv('valuation') == 'weak' or lv('priced_in') == 'weak'):
        tags.append('quality but expensive')
    tags += scoped_txt
    return ('no lens matched: ' + (', '.join(tags) + '; ' if tags else '') +
            f"quality {lv('business_quality')}, growth {lv('growth_quality')} ({_fmt(m.get('growth_level'))}), strength {lv('financial_strength')}, "
            f"valuation {lv('valuation')}, priced-in {lv('priced_in')}, return proxy {_fmt(d['expected_return_proxy']['value'], 'pct1')}")


def _notes(o, ctx, no_data, gaps=(), conc=None, skipped=None):
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
         'priced_in uses a standard fade model on EV / after-tax operating profit (market cap / owner FCF when operating profit is '
         'thin or unknown; not the pre-registered calibrated revenue model and without base rates): '
         'read it as "what the price requires", a crash-risk check. A consensus reference is compared on its own 2-year horizon. '
         'When it cannot be read, a crude fallback (EV/sales per Rule-of-40 point, EV/sales, P/E) orders and flags names; '
         'valuation-gap ranking failed out of sample (docs/GAP_TEST_RESULTS.md).',
         'Cyclical rows (industry word, model_card cyclical type, or a boom after a downturn in the data) are tested for a possible '
         'peak; missing consensus leaves the check incomplete, never "no peak". A possible or unchecked peak fails quality_fair_price '
         'and mispriced_turning (peak earnings make the price look low) and orders names last elsewhere.',
         'Paid adoption, retention, moat, management and capital allocation are not in screening data: lenses use numeric proxies '
         'and say so; the deep analysis must test them.',
         'Structural themes are analyst hypotheses supplied in options; numbers alone cannot identify a structural shift, and a '
         'theme is not a price check.']
    if no_data or gaps:
        kinds = sorted({v for x in no_data for v in x['errors'].values() if v != 'invalid_value'})
        n.append(f'{len(no_data)} symbols without data' + (f" (vendor: {', '.join(kinds)})" if kinds else '') + ': listed under no_data, '
                 'not excluded on merit.' + (f" {len(gaps)} more ({', '.join(sorted(gaps))}) lost most key metrics to vendor denials: "
                                            'excluded as coverage gaps.' if gaps else '')
                 + ' A plan denial is a coverage gap, not evidence about the company.')
    if conc:
        top, k = max(conc.items(), key=lambda x: (x[1], x[0]))[0], max(conc.values())
        tot = sum(conc.values())
        if tot >= 4 and k / tot > .5:
            n.append(f'Shortlist concentration: {k} of {tot} names in {top}' + (f" (shortlist_sector_cap {o['shortlist_sector_cap']})"
                     if o['shortlist_sector_cap'] else '; set options.shortlist_sector_cap to cap names per sector on the shortlist') + '.')
    if skipped:
        n.append('Shortlist sector cap skipped: ' + ', '.join(f'{s} ({v[1]}, from {v[0]})' for s, v in sorted(skipped.items())) + '.')
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
