"""Expected price path: a year-by-year bear / base / bull PRICE for the next five years, built the way a careful analyst does
and tested as a price forecast (docs/prereg_price_path.md, written before this module; nothing in sections 2-6 of that
document may change after the first run).

The DCF lane (forecast.py / valuation.py) measures value. This lane forecasts the price from three pieces, each tied to
what the company already shows and to documented base rates, never to today's multiple carried forward:
  growth   the reported run rate (latest fiscal year and latest quarter against a year earlier; a cyclical's 5-year trend
           instead of its last leg), faded toward the median growth of companies of its revenue size (The Base Rate Book,
           base_rates.CLASSES) with the engine's stage half-lives; the size class is re-read every year as the company
           grows; the base path may not be rarer in its class than the top decile (the bull: top 5%) without evidence;
  margin   the structural net margin (a cyclical's mid-cycle median), the bear moving to a lower margin and the bull to a
           higher one over the stage's convergence years -- the bear changes the business, not only the multiple;
  multiple a justified trailing P/E from the scenario's own remaining growth, its return on capital (through the payout the
           sustainable-growth identity leaves) and the cost of equity: with no value creation it is 1 / ke, it rises with
           growth only when ROIC > ke, and it falls year by year as the growth fades. Today's multiple enters only as a
           premium (or discount) over the justified one that decays with the same half-life as the excess growth.
Price_h(s) = EPS_h(s) x multiple_h(s). A dated price is a conditional estimate, not a predicted exchange price, and never
authorizes a trade. The backtest tests the run-rate + base-rate variant only; dated guidance / consensus inputs (section 5
of the preregistration) are accepted here with evidence ids but are NOT covered by the measured error.

Point in time: anchors come from the first three pipeline stages (dataqa, analysis, model_card) on a FactBook whose facts
are usable only when filed by as_of (facts.usable), exactly as the engine walk-forward reads them.
"""
from datetime import date, timedelta
from hashlib import sha256
import json
import math
import statistics

from . import base_rates
from . import facts as F
from .backtest import _at, _stats
from .forecast import STAGE

SPEC_VERSION = 1
SCEN = ('bear', 'base', 'bull')
HORIZONS = (1, 2, 3, 4, 5)
# ---- constants of docs/prereg_price_path.md section 4 (sources there; repeated in short here)
PI = .02                       # long-run inflation to turn the book's real medians nominal and to deflate forecast revenue (Fed target)
G_RUN = (-.30, .60)            # beyond this a year is an event (acquisition, divestiture, collapse), not a run rate
CYC_G = (-.05, .25)            # a cyclical's trend growth bounds (cf. forecast.CYCLE_TREND, wider above: no fitted-trend rule here)
CYC_CONV = 3                   # years for a cyclical's margin to move from TTM to mid-cycle (forecast.CYCLE_CONV base)
CEIL = {'base': .10, 'bull': .05, 'bear': None}   # base-rate ceiling: share of the size class that grew at least as fast over 5 years
# section 5 (live engine only, untested): evidenced backlog (RPO >= one year of revenue) lets the path sit deeper in the tail.
# The preregistration prints the lifted ceilings as {base .25, bull .10}, which would TIGHTEN the constraint (a larger minimum
# share); the only reading consistent with "lift the ceiling" is a smaller minimum share, so the base takes the bull's level and
# the bull half of it. Logged as deviation 1 in docs/prereg_price_path.md section 9.
CEIL_BACKLOG = {'base': .05, 'bull': .025, 'bear': None}
R_MAX = .50                    # ROIC cap: above 50% the reinvestment need is already ~0
TAX_STAT = .21                 # US statutory rate (TCJA): the after-tax version of the stage operating-margin spread
M_FLOOR = .01                  # a P/E path needs positive earnings; a binding floor is flagged, never hidden
PE_FLOOR, PE_CAP, PE_INPUT_CAP = 5., 60., 60.   # Mauboussin & Callahan 2014 exhibits 1 and 5; Damodaran Jan-2026 PE regression
SHARE_CLIP = .06               # buyback / dilution pace beyond 6% a year is an event
N_JUST = 10                    # explicit years after h inside the justified multiple
PATH_YEARS = 15                # explicit growth path (evaluated through year PATH_YEARS + 1: the payout of year 15 needs g_16)
HL_SCALE = {'bear': .8, 'base': 1., 'bull': 1.3}   # the engine's scenario rule for the growth half-life
BULL_MARGIN_SHARE = .75        # the engine's bull margin rule: + 0.75 x the stage spread
BISECT_STEPS = 40
# CPI-U of May of each formation year (published mid-June, before the 30 June as_of): FRED CPIAUCSL, retrieved 2026-10-21.
# The 2015 base is base_rates.CPI_2015 (BLS annual average). Restates TTM revenue in 2015 dollars for the size class.
CPI_MAY = {2016: 239.557, 2017: 244.004, 2018: 250.792, 2019: 255.296, 2020: 255.802, 2021: 268.383, 2022: 291.298,
           2023: 303.334, 2024: 313.175, 2025: 320.620}
# Damodaran, January 2026 US cross-section: PE = 13.12 + 10.52 Beta + 36.47 gEPS + 8.46 Payout (R2 38.7%). A diagnostic only
# (section 6.6): it is a 2026 fit, so it never enters a price and is not point in time.
PE_REG = {'intercept': 13.12, 'beta': 10.52, 'growth': 36.47, 'payout': 8.46, 'date': '2026-01', 'r2': .387}
FORMATION_YEARS = {1: (2016, 2025), 2: (2016, 2024), 3: (2016, 2023), 4: (2016, 2022), 5: (2016, 2021)}   # section 6.2
VERDICT_HORIZONS = (1, 3, 5)
MULTIPLE_BUCKETS = ((10, '<10x'), (15, '10-15x'), (20, '15-20x'), (30, '20-30x'), (60, '30-60x'), (float('inf'), '>60x (capped)'))
WITHIN = math.log(1.25)
# Measured walk-forward error of the base path (docs/PRICE_PATH_RESULTS_2026-10-21.md). Written after the preregistered run
# and shown with every live path; None until the run exists. Keys: horizon -> {'mdale', 'within25', 'bias_median', 'n'}.
MEASURED = None
MEASURED_VERDICT = None
RULES = 'engine:price_path_rules'


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _metric(an, name):
    m = ((an or {}).get('metrics') or {}).get(name)
    return _num(m.get('value') if isinstance(m, dict) else m)


def _sha(obj):
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str).encode()).hexdigest()


def _r(x, k=6):
    return None if x is None else round(float(x), k)


# ---------------------------------------------------------------- 3.2 growth path

def class_median(table, n):
    """Nominal median CAGR of a size class at horizon n: the book's real median, linear between its tabulated horizons
    (1/3/5/10), flat after the last, plus PI."""
    hs = sorted(table)
    if n <= hs[0]:
        med = table[hs[0]][1]
    elif n >= hs[-1]:
        med = table[hs[-1]][1]
    else:
        a = max(x for x in hs if x <= n)
        b = min(x for x in hs if x >= n)
        med = table[a][1] if a == b else table[a][1] + (table[b][1] - table[a][1]) * (n - a) / (b - a)
    return med / 100 + PI


def size_class(revenue, cpi):
    """(label, table) of the Base Rate Book size class for revenue in the CPI's dollars."""
    return base_rates.reference_class(revenue, cpi_now=cpi)


def growth_path(g1, stage, scenario, rev0, cpi, lam=1., years=PATH_YEARS + 1, fixed=None):
    """g_h = c_{class_h}(h) + lambda x (g_1 - c_{class_1}(1)) x 0.5^((h-1) / HL_s), h = 1..years, the class re-read each year
    from the forecast revenue deflated at PI. fixed = {year: growth} (live engine: guided / consensus years, literal) makes the
    fade start from the last fixed year k0: g_h = c_h(h) + lambda x (g_k0 - c_k0(k0)) x 0.5^((h-k0) / HL_s) for h > k0.
    Returns (growth list, class label per year)."""
    st = STAGE[stage]
    hl = st['half_life'] * HL_SCALE[scenario]
    fixed = {int(k): float(v) for k, v in (fixed or {}).items()}
    k0 = max(fixed) if fixed else 1
    out, labels, rev, excess = [], [], rev0, None
    for h in range(1, years + 1):
        label, table = size_class(rev / (1 + PI) ** (h - 1), cpi)
        c = class_median(table, h)
        if fixed and h <= k0:
            g = fixed.get(h, g1)
            if h == k0:
                excess = g - c
        else:
            if excess is None:
                excess = g1 - c
            g = c + lam * excess * .5 ** ((h - k0) / hl)
        out.append(g)
        labels.append(label)
        rev *= 1 + g
    return out, labels


def real_cagr_5y(growth):
    return (1 + base_rates.cagr(growth, 5)) / (1 + PI) - 1


def shrink(g1, stage, scenario, rev0, cpi, ceil, fixed=None):
    """Largest lambda in (0, 1] for which the 5-year real CAGR of the path is not rarer in the formation size class than ceil
    (base_rates.share_at_least >= ceil); 1 when the unshrunk path passes; bisection, BISECT_STEPS. -> (lambda, share)."""
    freqs5 = size_class(rev0, cpi)[1][5][0]

    def share(lam):
        g, _ = growth_path(g1, stage, scenario, rev0, cpi, lam, fixed=fixed)
        return base_rates.share_at_least(freqs5, real_cagr_5y(g))
    s1 = share(1.)
    if ceil is None or s1 >= ceil:
        return 1., s1
    lo, hi = 0., 1.
    for _ in range(BISECT_STEPS):
        mid = (lo + hi) / 2
        if share(mid) >= ceil:
            lo = mid
        else:
            hi = mid
    return lo, share(lo)


# ---------------------------------------------------------------- 3.3 margins, shares

def margin_path(m_ttm, m_anchor, stage, scenario, cyclical, years=PATH_YEARS, m1_override=None):
    """Base: m_anchor (structural) or m_ttm -> m_anchor linearly over CYC_CONV years (cyclical: year h is h / CYC_CONV of the
    way). Bear: from the base's m_1 to m_anchor - MS over the stage's conv years, then holds; bull: to m_anchor + 0.75 MS;
    MS = (1 - TAX_STAT) x stage m_spread. The move is counted from today like the engine's own margin path (forecast.py:
    year t is t / conv of the way) and like the cyclical rule above, so the bear's margin is below the base's from year 1:
    the bear changes the business in every forecast year (section 9, clarification 3). Floored at M_FLOOR.
    -> (margins m_1..m_years, floored?)."""
    st = STAGE[stage]
    base = [(m_ttm + (m_anchor - m_ttm) * min(h / CYC_CONV, 1.)) if cyclical else m_anchor for h in range(1, years + 1)]
    if m1_override is not None:
        base[0] = m1_override
    if scenario == 'base':
        path = base
    else:
        ms = (1 - TAX_STAT) * st['m_spread']
        target = m_anchor - ms if scenario == 'bear' else m_anchor + BULL_MARGIN_SHARE * ms
        m1 = base[0]
        path = [m1 + (target - m1) * min(h / st['conv'], 1.) for h in range(1, years + 1)]
    floored = any(m < M_FLOOR for m in path)
    return [max(M_FLOOR, m) for m in path], floored


def share_path(s0, s_cagr, years=HORIZONS[-1]):
    s = _clip(s_cagr or 0., -SHARE_CLIP, SHARE_CLIP)
    return [s0 * (1 + s) ** h for h in range(0, years + 1)], s


# ---------------------------------------------------------------- 3.4 justified multiple

def justified_multiple(ni, g, R, ke, rf, h, n=N_JUST):
    """Trailing P/E at the end of year h justified by the path after it: ni = [NI_0 .. NI_{>= h+n}] (NI_0 normalized),
    g = [g_1 .. g_{>= h+n+1}]. payout_k = clip(1 - g_{k+1} / R, 0, 1) (sustainable-growth identity); V_h = sum of
    NI_k payout_k / (1+ke)^(k-h), k = h+1..h+n, plus NI_{h+n} (1+g_T) clip(1 - g_T/R, 0, 1) / (ke - g_T) discounted n years,
    g_T = min(rf, g_{h+n}); M = V_h / NI_h. -> (raw, clipped to [PE_FLOOR, PE_CAP]).
    Properties: with R = ke every reinvested dollar earns its cost, so V_h = NI_{h+1} / ke whatever the growth path while
    the payout is unclipped (g <= R): the forward multiple is 1 / ke, the trailing one (1 + g_{h+1}) / ke, and growth adds
    value only when R > ke. When g > R the clip at 0 means the path grows faster than full reinvestment at R allows (the
    identity cannot fund it); the multiple then rises with growth even at R = ke. That is the registered formula; the
    ceiling on lambda and R_MAX keep such paths rare (section 9, clarification 4)."""
    if ni[h] <= 0:
        raise ValueError(f'justified multiple needs positive earnings in year {h}')
    pv = 0.
    for k in range(h + 1, h + n + 1):
        payout = _clip(1 - g[k] / R, 0., 1.)                   # g[k] is g_{k+1} (g is 0-based: g[0] = g_1)
        pv += ni[k] * payout / (1 + ke) ** (k - h)
    gT = min(rf, g[h + n - 1])                                  # g_{h+n}
    if ke - gT <= 1e-9:
        raise ValueError(f'cost of equity {ke:.4f} must exceed the terminal growth {gT:.4f}')
    tv = ni[h + n] * (1 + gT) * _clip(1 - gT / R, 0., 1.) / (ke - gT)
    raw = (pv + tv / (1 + ke) ** n) / ni[h]
    return raw, _clip(raw, PE_FLOOR, PE_CAP)


# ---------------------------------------------------------------- anchors (point in time, from the pipeline's first three stages)

def _facts_ref(t):
    return [{'metric': f['metric'], 'period': f['period'], 'period_end': f['period_end'], 'source_id': f['source_id'],
             'available_at': f['available_at']} for f in (t or {}).get('facts') or []]


def anchors(fb, an, qa, card, price, rf, erp, cpi=None):
    """Section 3.1 anchors from a FactBook at as_of with its dataqa / analysis / model card outputs, the traded price, the
    risk-free rate and the ERP. cpi: CPI-U index at as_of (section 4; base_rates.CPI_NOW when None, for a live run).
    -> {'status': 'ok', ...} or {'status': 'excluded', 'reasons': [...]} (NI_0 <= 0 etc.; counted by the backtest)."""
    ttm = (an or {}).get('ttm') or {}
    rev0, ni0 = _num(ttm.get('revenue')), _num(ttm.get('net_income'))
    res = ((qa or {}).get('resolved') or {}).get('shares_outstanding') or {}
    s0 = _num(res.get('value'))
    stage = ((card or {}).get('stage') or {}).get('value')
    cyc = (card or {}).get('cyclical') or {}
    reasons, flags, notes = [], [], []
    ni_ref = F.ttm(fb, 'net_income')
    if ni0 is None and rev0 and ni_ref and ttm.get('period_end') and abs((_d(ni_ref['period_end']) - _d(ttm['period_end'])).days) <= F.SAME_PERIOD_DAYS:
        # analysis.py keeps only a TTM built by the same method as the revenue TTM (annual + year to date vs four quarters); the
        # facts layer's TTM for the SAME period end is the same twelve months. Deviation 2 of docs/prereg_price_path.md section 9
        # (pre-run clarification, counted through the flag): the exclusion is for NI_0 <= 0, not for a method label.
        ni0 = _num(ni_ref['value'])
        flags.append('ni_ttm_from_facts')
        notes.append(f"TTM net income {ni0:,.0f} read from facts.ttm ({ni_ref['method']}, {ni_ref['period_end']}): analysis excluded it as a "
                     f"different method from the revenue TTM ({ttm.get('method')})")
    if not rev0 or rev0 <= 0:
        reasons.append('no positive TTM revenue')
    if ni0 is None:
        reasons.append('TTM net income missing')
    elif ni0 <= 0:
        reasons.append('TTM net income not positive')
    if not s0 or s0 <= 0:
        reasons.append('no point-in-time share count')
    if not price or price <= 0:
        reasons.append('no price')
    if rf is None or erp is None:
        reasons.append('risk-free rate or ERP missing')
    if stage not in STAGE:
        reasons.append(f'stage {stage!r} unknown')
    if reasons:
        return {'status': 'excluded', 'reasons': reasons}
    cyclical = bool(cyc.get('value'))
    # growth run rate
    g_fy = _metric(an, 'revenue_growth_last')
    g_q, q_ref = None, None
    q = F.series(fb, 'revenue', 'quarter')
    if q:
        end, v, f = q[-1]
        want = _d(end) - timedelta(days=364)
        prior = [x for x in q[:-1] if abs((_d(x[0]) - want).days) <= 10]
        if prior and prior[0][1] and prior[0][1] > 0 and v is not None:
            g_q = v / prior[0][1] - 1
            q_ref = {'quarter_end': end, 'prior_quarter_end': prior[0][0], 'source_id': f['source_id']}
    if cyclical:
        trend, basis = _metric(an, 'revenue_cagr_5y'), 'revenue_cagr_5y'
        if trend is None:
            trend, basis = _metric(an, 'revenue_cagr_3y'), 'revenue_cagr_3y'
        if trend is not None:
            g_raw, g_basis = _clip(trend, *CYC_G), f'cyclical trend: {basis} clipped to {CYC_G}'
            flags.append('cyclical_normalized')
        else:
            parts = [x for x in (g_fy, g_q) if x is not None]
            if not parts:
                return {'status': 'excluded', 'reasons': ['no growth run rate (no fiscal-year or quarterly comparison)']}
            g_raw, g_basis = sum(parts) / len(parts), 'cyclical without a multi-year trend: structural run rate used'
            flags += ['cyclical_normalized', 'cyclical_trend_missing']
    else:
        parts = [x for x in (g_fy, g_q) if x is not None]
        if not parts:
            return {'status': 'excluded', 'reasons': ['no growth run rate (no fiscal-year or quarterly comparison)']}
        g_raw = sum(parts) / len(parts)
        g_basis = 'mean of ' + ' and '.join(k for k, x in (('latest fiscal year growth', g_fy), ('latest quarter vs a year earlier', g_q)) if x is not None)
    g_run = _clip(g_raw, *G_RUN)
    if g_run != g_raw:
        flags.append('run_rate_clipped')
    # margins
    m_ttm = ni0 / rev0
    m_anchor, m_basis = m_ttm, 'TTM net margin (structural)'
    if cyclical:
        rows = sorted((pe, r) for pe, r in ((an or {}).get('annual') or {}).items()
                      if _num(r.get('revenue')) and r['revenue'] > 0 and _num(r.get('net_income')) is not None)[-10:]
        ms = [r['net_income'] / r['revenue'] for _, r in rows]
        if len(ms) >= 3:
            m_anchor, m_basis = statistics.median(ms), f'median fiscal-year net margin of the last {len(ms)} years (mid-cycle)'
        else:
            m_basis = 'TTM net margin (cyclical, fewer than 3 fiscal years for a mid-cycle median)'
            flags.append('cyclical_margin_history_short')
    if m_anchor <= 0:
        return {'status': 'excluded', 'reasons': ['normalized (mid-cycle) net margin not positive']}
    s_cagr = _metric(an, 'share_count_cagr_3y')
    roic = _metric(an, 'roic')
    if roic is None:
        flags.append('roic_missing')
    div = _num(ttm.get('dividends'))
    return {'status': 'ok', 'as_of': fb['as_of'], 'ticker': (fb.get('company') or {}).get('ticker'), 'price': float(price),
            'risk_free': float(rf), 'erp': float(erp), 'cpi': float(cpi or base_rates.CPI_NOW),
            'cpi_basis': 'supplied (CPI-U at as_of)' if cpi else f'base_rates.CPI_NOW {base_rates.CPI_NOW} ({base_rates.CPI_NOW_DATE})',
            'stage': stage, 'cyclical': cyclical, 'revenue': rev0, 'net_income': ni0, 'shares': s0,
            'g_run': g_run, 'g_raw': g_raw, 'g_basis': g_basis, 'g_fiscal_year': g_fy, 'g_quarter': g_q, 'quarter_ref': q_ref,
            'm_ttm': m_ttm, 'm_anchor': m_anchor, 'm_basis': m_basis, 'share_cagr': s_cagr, 'roic': roic,
            'dividends_ttm': div, 'dividend_yield': (div / (price * s0)) if div is not None else None,
            'ttm_period_end': ttm.get('period_end'), 'ttm_method': ttm.get('method'),
            'references': {'revenue': _facts_ref(F.ttm(fb, 'revenue')), 'net_income': _facts_ref(ni_ref),
                           'shares': {k: res.get(k) for k in ('as_of', 'basis', 'source_id')}},
            'flags': flags, 'notes': notes}


def eps_cagr_3y(fb, qa=None):
    """The analyst_const baseline's growth (section 6.4): trailing 3-year diluted-EPS CAGR from the annual eps_diluted series,
    split-consistent: an EPS first filed before a split dataqa established (qa['splits']) is divided by the split factor.
    Without that a split inside the window would mis-state the baseline by the ratio (AAPL 2020: 11.89 -> 3.28). None unless
    the latest fiscal year and the one three years earlier both exist and are positive."""
    ser = F.series(fb, 'eps_diluted', 'annual')
    if not ser:
        return None
    splits = []
    for x in ((qa or {}).get('splits') or []):
        ev = ((x.get('evidence') or {}).get('split_events') or [{}])[0]
        when = ev.get('date') or x.get('to')
        if when and x.get('factor'):
            splits.append((str(when)[:10], float(x['factor'])))

    def adj(f):
        v = f['value']
        for when, factor in splits:
            if str(f['available_at'])[:10] < when:
                v /= factor
        return v
    end, _, last = ser[-1]
    want = _d(end) - timedelta(days=3 * 365)
    back = [x for x in ser if abs((_d(x[0]) - want).days) <= 45]
    if not back:
        return None
    a, b = adj(back[0][2]), adj(last)
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return (b / a) ** (1 / 3) - 1


# ---------------------------------------------------------------- section 5 inputs (live engine; untested by the backtest)

def _check_inputs(inputs, as_of):
    """Dated, evidenced guidance / consensus / backlog -> {'fixed': {year: growth}, 'basis': {year: label}, 'margin_1',
    'ceil', 'statements'}. Every input needs evidence_ids and an issued_at <= as_of (no look-ahead)."""
    out = {'fixed': {}, 'basis': {}, 'margin_1': None, 'ceil': CEIL, 'statements': []}
    if not inputs:
        return out
    if not isinstance(inputs, dict):
        raise ValueError('inputs must be an object')
    extra = sorted(set(inputs) - {'guidance', 'consensus', 'backlog', 'track_record'})
    if extra:
        raise ValueError(f'unknown input keys {extra} (guidance, consensus, backlog, track_record)')

    def dated(x, label):
        if not isinstance(x, dict) or _num(x.get('value')) is None:
            raise ValueError(f'{label}: value required')
        if not isinstance(x.get('evidence_ids'), list) or not x['evidence_ids']:
            raise ValueError(f'{label}: evidence_ids required')
        if not x.get('issued_at') or _d(x['issued_at']) > _d(as_of):
            raise ValueError(f'{label}: issued_at required and not after as_of {as_of} (look-ahead)')
        return float(x['value'])
    gd = inputs.get('guidance') or {}
    tr = inputs.get('track_record') or {}
    if 'revenue_growth_fy1' in gd:
        g = dated(gd['revenue_growth_fy1'], 'guidance.revenue_growth_fy1')
        rows, surprise = int(tr.get('rows') or 0), _num(tr.get('median_revenue_surprise'))
        if rows >= 4 and surprise is not None:
            g = (1 + g) * (1 + surprise) - 1
            out['statements'].append(f'year 1: guidance midpoint adjusted by the median past revenue surprise {surprise:+.1%} ({rows} rows)')
        else:
            out['statements'].append('year 1: guidance midpoint (track record under 4 rows: no adjustment)')
        out['fixed'][1], out['basis'][1] = g, 'guided'
    if 'margin_fy1' in gd:
        out['margin_1'] = dated(gd['margin_fy1'], 'guidance.margin_fy1')
        out['statements'].append('year 1 margin: guided')
    cs = inputs.get('consensus') or {}
    for yr in (1, 2):
        key = f'revenue_growth_y{yr}'
        if key in cs and yr not in out['fixed']:
            if yr == 2 and 1 not in out['fixed']:
                raise ValueError('consensus year 2 needs a year-1 value (guidance or consensus)')
            out['fixed'][yr], out['basis'][yr] = dated(cs[key], f'consensus.{key}'), 'consensus'
            out['statements'].append(f'year {yr}: consensus')
    bl = inputs.get('backlog')
    if bl:
        rpo = dated(bl, 'backlog')
        rev = _num(bl.get('revenue_ttm'))
        if rev and rpo >= rev:
            out['ceil'] = CEIL_BACKLOG
            out['statements'].append(f'backlog {rpo:,.0f} >= TTM revenue {rev:,.0f}: base-rate ceiling lifted to {CEIL_BACKLOG}')
        else:
            out['statements'].append('backlog below one year of revenue: ceiling unchanged')
    return out


# ---------------------------------------------------------------- 3.5 / 3.6 the path

def band_statement():
    if not MEASURED:
        return ('Bear and bull are the method\'s scenario band (slower growth and a lower margin / faster growth and a higher '
                'margin, with today\'s premium over the justified multiple fading faster / slower), not a measured confidence '
                'interval. The walk-forward measurement of this lane has not been recorded in this build: the band is untested. '
                'Dated prices are conditional estimates, never predicted exchange prices, and never authorize a trade.')
    parts = ', '.join(f"{h}y: within +-25% in {m['within25']:.0%} of rows, median |log error| {m['mdale']:.2f}, median bias "
                      f"{m['bias_median']:+.2f} (n {m['n']})" for h, m in sorted(MEASURED.items()))
    return (f'Bear and bull are the method\'s scenario band, not a measured confidence interval. Measured on the 2016-2025 '
            f'walk-forward of the run-rate + base-rate variant (survivors only; {MEASURED_VERDICT}): {parts}. Guided or consensus '
            'years are outside that measurement. Dated prices are conditional estimates, never predicted exchange prices, and '
            'never authorize a trade.')


def price_path(anch, inputs=None):
    """Sections 3.2-3.6 on an anchors() dict (pure, deterministic, hashed). inputs: section 5 (live engine only)."""
    if anch.get('status') != 'ok':
        raise ValueError('anchors are not usable: ' + '; '.join(anch.get('reasons') or ['unknown']))
    st = STAGE[anch['stage']]
    live = _check_inputs(inputs, anch['as_of'])
    rf, erp, cpi, rev0, s0, p0 = anch['risk_free'], anch['erp'], anch['cpi'], anch['revenue'], anch['shares'], anch['price']
    ke = rf + erp + st['ke']
    roic = anch.get('roic')
    R = ke if roic is None else _clip(roic, ke, R_MAX)
    m_anchor, m_ttm, cyclical = anch['m_anchor'], anch['m_ttm'], anch['cyclical']
    ni0 = rev0 * m_anchor                                   # normalized trailing earnings (mid-cycle for a cyclical)
    eps0 = ni0 / s0
    m0 = p0 / eps0
    flags = list(anch.get('flags') or [])
    if m0 > PE_INPUT_CAP:
        flags.append('multiple_capped')
    shares, s_used = share_path(s0, anch.get('share_cagr'))
    if anch.get('share_cagr') is not None and abs(anch['share_cagr']) > SHARE_CLIP:
        flags.append('share_pace_clipped')
    g1 = {'base': anch['g_run'], 'bear': anch['g_run'] - st['g_spread'], 'bull': anch['g_run'] + st['g_spread']}
    fixed = {s: {k: v + (0 if s == 'base' else (-st['g_spread'] if s == 'bear' else st['g_spread'])) for k, v in live['fixed'].items()}
             for s in SCEN}
    scen = {}
    for s in SCEN:
        lam, share5 = shrink(g1[s], anch['stage'], s, rev0, cpi, live['ceil'][s], fixed=fixed[s] or None)
        g, labels = growth_path(g1[s], anch['stage'], s, rev0, cpi, lam, fixed=fixed[s] or None)
        m, floored = margin_path(m_ttm, m_anchor, anch['stage'], s, cyclical, m1_override=live['margin_1'])
        rev, ni = [rev0], [ni0]
        for k in range(1, PATH_YEARS + 1):
            rev.append(rev[-1] * (1 + g[k - 1]))
            ni.append(rev[-1] * m[k - 1])
        if floored:
            flags.append(f'margin_floored_{s}')
        if lam < 1:
            flags.append(f'lambda_shrunk_{s}')
        scen[s] = {'lambda': lam, 'share_5y': share5, 'half_life': st['half_life'] * HL_SCALE[s], 'g1': g1[s], 'g': g, 'labels': labels,
                   'm': m, 'rev': rev, 'ni': ni}
    just0_raw, just0 = justified_multiple(scen['base']['ni'], scen['base']['g'], R, ke, rf, 0)
    d0 = min(m0, PE_INPUT_CAP) - just0
    out_scen = {}
    for s in SCEN:
        z = scen[s]
        years = []
        for h in HORIZONS:
            raw, just = justified_multiple(z['ni'], z['g'], R, ke, rf, h)
            prem = d0 * .5 ** (h / z['half_life'])
            mult = _clip(just + prem, PE_FLOOR, PE_CAP)
            eps = z['ni'][h] / shares[h]
            basis = live['basis'].get(h) or ('run_rate' if h == 1 else 'base_rate_fade')
            years.append({'year': h, 'revenue': _r(z['rev'][h], 2), 'growth': _r(z['g'][h - 1]), 'margin': _r(z['m'][h - 1]),
                          'net_income': _r(z['ni'][h], 2), 'shares': _r(shares[h], 2), 'eps': _r(eps), 'size_class': z['labels'][h - 1],
                          'payout': _r(_clip(1 - z['g'][h] / R, 0., 1.)), 'justified_multiple': _r(just), 'justified_raw': _r(raw),
                          'premium_left': _r(prem), 'multiple': _r(mult), 'price': _r(eps * mult), 'basis': basis})
        out_scen[s] = {'lambda': _r(z['lambda']), 'base_rate_share_5y': _r(z['share_5y']), 'half_life': z['half_life'],
                       'growth_year1': _r(z['g1']), 'years': years}
    e5 = out_scen['base']['years'][4]['eps']
    g5 = (e5 / eps0) ** (1 / 5) - 1 if e5 and e5 > 0 else None
    payout1 = out_scen['base']['years'][0]['payout']
    pe_reg = PE_REG['intercept'] + PE_REG['beta'] + PE_REG['growth'] * g5 + PE_REG['payout'] * payout1 if g5 is not None else None
    ledger = [
        {'id': 'cost_of_equity', 'value': _r(ke), 'basis': f"risk-free {rf:.4f} + ERP {erp:.4f} + stage {anch['stage']} add-on {st['ke']}", 'source': RULES},
        {'id': 'return_on_capital', 'value': _r(R), 'basis': 'latest-FY ROIC clipped to [ke, R_MAX]' if roic is not None else 'ROIC unavailable: R = ke (no value creation assumed)', 'source': RULES},
        {'id': 'growth_run_rate', 'value': _r(anch['g_run']), 'basis': anch['g_basis'] + (f"; raw {anch['g_raw']:.4f} clipped to {G_RUN}" if 'run_rate_clipped' in flags else ''), 'source': 'facts'},
        {'id': 'growth_spread', 'value': st['g_spread'], 'basis': 'stage g_spread (bear = base - spread, bull = base + spread in year 1)', 'source': 'engine:forecast_rules'},
        {'id': 'growth_half_life', 'value': st['half_life'], 'basis': f"stage half-life x {HL_SCALE} per scenario (Koller et al. growth decay)", 'source': 'engine:forecast_rules'},
        {'id': 'base_rate_ceiling', 'value': live['ceil'], 'basis': 'minimum share of the size class growing at least as fast over 5 years (The Base Rate Book, exhibit 4)', 'source': base_rates.SOURCE},
        {'id': 'size_class_today', 'value': size_class(rev0, cpi)[0], 'basis': f"TTM revenue in 2015 dollars (CPI {cpi}); re-read yearly from forecast revenue deflated at {PI}", 'source': base_rates.SOURCE},
        {'id': 'margin_anchor', 'value': _r(m_anchor), 'basis': anch['m_basis'], 'source': 'facts'},
        {'id': 'margin_spread_after_tax', 'value': _r((1 - TAX_STAT) * st['m_spread']), 'basis': f"(1 - {TAX_STAT}) x stage m_spread {st['m_spread']}; bear reaches anchor - spread, bull anchor + {BULL_MARGIN_SHARE} x spread over {st['conv']} years", 'source': 'engine:forecast_rules'},
        {'id': 'share_path', 'value': _r(s_used), 'basis': f"3-year share-count CAGR clipped to +-{SHARE_CLIP}, same in every scenario", 'source': 'facts'},
        {'id': 'multiple_today', 'value': _r(m0), 'basis': f'price {p0:.4f} / normalized trailing EPS {eps0:.4f}' + (f'; capped at {PE_INPUT_CAP} for the premium' if m0 > PE_INPUT_CAP else ''), 'source': 'market'},
        {'id': 'justified_multiple_today', 'value': _r(just0), 'basis': f'base path, {N_JUST} explicit years after today, terminal growth min(rf, g_10), clipped to [{PE_FLOOR}, {PE_CAP}]', 'source': RULES},
        {'id': 'premium_today', 'value': _r(d0), 'basis': 'min(multiple today, cap) - justified multiple today; decays at the scenario growth half-life', 'source': RULES},
    ] + [{'id': f'live_input_{i + 1}', 'value': None, 'basis': s, 'source': 'inputs'} for i, s in enumerate(live['statements'])]
    core = {'spec_version': SPEC_VERSION, 'as_of': anch['as_of'], 'ticker': anch.get('ticker'), 'price': p0, 'cost_of_equity': _r(ke),
            'return_on_capital': _r(R), 'eps_normalized': _r(eps0), 'multiple_today': _r(m0), 'justified_today': _r(just0),
            'justified_today_raw': _r(just0_raw), 'premium_today': _r(d0), 'size_class': size_class(rev0, cpi)[0],
            'scenarios': out_scen, 'flags': sorted(set(flags)), 'eps_cagr_5y_base': _r(g5), 'payout_1': payout1,
            'pe_regression_diagnostic': {'pe_reg': _r(pe_reg), 'ratio_justified_to_reg': _r(just0 / pe_reg) if pe_reg else None, **PE_REG},
            'years_basis': {h: (live['basis'].get(h) or ('run_rate' if h == 1 else 'base_rate_fade')) for h in HORIZONS},
            'live_inputs_used': bool(live['statements'])}
    out = dict(core, assumptions=ledger, band_statement=band_statement(), measured_error=MEASURED, verdict=MEASURED_VERDICT,
               input_sha256=_sha({'anchors': anch, 'inputs': inputs or {}, 'spec_version': SPEC_VERSION}), output_sha256=_sha(core))
    return out


def table(path):
    """Compact {scenario: {year: price}} view of a price_path result."""
    return {s: {y['year']: y['price'] for y in z['years']} for s, z in path['scenarios'].items()}


# ---------------------------------------------------------------- evaluation (section 6)

def realized_ratio(p, as_of, h):
    """close(t + 12h months) / close(t), split-adjusted, no dividends (backtest._at within its two-month tolerance)."""
    if not p:
        return None
    a = _at(p, as_of[:7])
    y, m = int(as_of[:4]) + h, int(as_of[5:7])
    b = _at(p, f'{y:04d}-{m:02d}')
    if not a or not b or a[0] <= 0 or b[0] <= 0:
        return None
    return b[0] / a[0]


def predictions(row, h, base_irr=None):
    """Section 6.4 predictors of actual_h (t-share units): the lane's three paths and the baselines."""
    p0 = row['price']
    out = {'lane_base': row['scenarios']['base']['years'][h - 1]['price'],
           'lane_bear': row['scenarios']['bear']['years'][h - 1]['price'],
           'lane_bull': row['scenarios']['bull']['years'][h - 1]['price'],
           'random_walk': p0, 'drift_7': p0 * 1.07 ** h}
    g = row.get('eps_cagr_3y')
    if g is not None:
        out['analyst_const'] = p0 * (1 + _clip(g, -.10, .30)) ** h
    if base_irr is not None:
        dy = row.get('dividend_yield') or 0.
        out['engine_irr'] = p0 * max(0., 1 + base_irr - dy) ** h
        out['engine_irr_total'] = p0 * max(0., 1 + base_irr) ** h
    return out


BASELINES = ('random_walk', 'drift_7', 'analyst_const', 'engine_irr')


def _err(pred, actual):
    return math.log(pred / actual) if pred and pred > 0 and actual and actual > 0 else None


def _metrics(recs, name, actual_key='actual'):
    es = [_err(r['preds'].get(name), r[actual_key]) for r in recs]
    es = [e for e in es if e is not None]
    if not es:
        return {'n': 0}
    return {'n': len(es), 'bias_median': statistics.median(es), 'bias_mean': sum(es) / len(es),
            'mdale': statistics.median(abs(e) for e in es), 'within25': sum(1 for e in es if abs(e) <= WITHIN) / len(es),
            'share_above': sum(1 for e in es if e > 0) / len(es)}


def _head_to_head(recs, name, actual_key='actual'):
    out = {}
    for b in BASELINES:
        pairs = [(_err(r['preds'].get(name), r[actual_key]), _err(r['preds'].get(b), r[actual_key])) for r in recs]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        out[b] = {'n': len(pairs), 'share_lane_closer': (sum(1 for x, y in pairs if abs(x) < abs(y)) / len(pairs)) if pairs else None}
    return out


def _band(recs, actual_key='actual'):
    n = len(recs)
    if not n:
        return {'n': 0}
    below = sum(1 for r in recs if r[actual_key] < r['preds']['lane_bear'])
    above = sum(1 for r in recs if r[actual_key] > r['preds']['lane_bull'])
    return {'n': n, 'within_band': (n - below - above) / n, 'below_bear': below / n, 'above_bull': above / n}


def _spearman(a, b):
    n = len(a)
    if n < 5:
        return None
    rank = lambda xs: {i: k for k, i in enumerate(sorted(range(n), key=lambda i: xs[i]))}
    ra, rb = rank(a), rank(b)
    return 1 - 6 * sum((ra[i] - rb[i]) ** 2 for i in range(n)) / (n * (n * n - 1))


def _rank_stats(recs, name):
    by = {}
    for r in recs:
        if r['preds'].get(name):
            by.setdefault(r['formation'][:4], []).append((r['preds'][name] / r['price'], r['R']))
    rhos = [_spearman([x for x, _ in v], [y for _, y in v]) for v in by.values()]
    rhos = [x for x in rhos if x is not None]
    return dict(_stats(rhos), by_year={y: _spearman([x for x, _ in v], [y2 for _, y2 in v]) for y, v in sorted(by.items())})


def _bucket(m0):
    for lim, label in MULTIPLE_BUCKETS:
        if m0 < lim:
            return label
    return MULTIPLE_BUCKETS[-1][1]


def _breakdown(recs, key, names, actual_key='actual'):
    groups = {}
    for r in recs:
        groups.setdefault(str(key(r)), []).append(r)
    return {g: {n: {k: v for k, v in _metrics(rs, n, actual_key).items() if k in ('n', 'bias_median', 'mdale', 'within25')} for n in names}
            for g, rs in sorted(groups.items())}


def _deflate(recs, prices, spy):
    """Section 6.8: per formation window the realized ratios are rescaled so the sample's median equals SPY's price-only
    ratio (repA's survivorship rule). Writes rec['actual_deflated']; returns the factors used."""
    factors = {}
    by = {}
    for r in recs:
        by.setdefault(r['formation'], []).append(r)
    for fm, rs in by.items():
        spy_r = realized_ratio(prices.get(spy), rs[0]['as_of'], rs[0]['h'])
        med = statistics.median(x['R'] for x in rs)
        f = spy_r / med if spy_r and med else None
        factors[fm] = {'spy_ratio': spy_r, 'sample_median_ratio': med, 'factor': f, 'n': len(rs)}
        for x in rs:
            x['actual_deflated'] = x['actual'] * f if f else None
    return factors


def _verdict(hz):
    """Section 6.7 on the raw common sample."""
    def m(h, name, key='mdale'):
        return (((hz.get(h) or {}).get('common') or {}).get('metrics') or {}).get(name, {}).get(key)
    checks = {}
    s1 = []
    for h in (3, 5):
        lane, ac, ei = m(h, 'lane_base'), m(h, 'analyst_const'), m(h, 'engine_irr')
        ok = None if None in (lane, ac, ei) else (lane < ac and lane < ei)
        s1.append(ok)
        checks[f'S1_h{h}_below_analyst_and_engine'] = {'lane': lane, 'analyst_const': ac, 'engine_irr': ei, 'pass': ok}
    for h in VERDICT_HORIZONS:
        lane, rw, dr = m(h, 'lane_base'), m(h, 'random_walk'), m(h, 'drift_7')
        ok = None if None in (lane, rw, dr) else lane <= min(rw, dr) + .03
        s1.append(ok)
        checks[f'S1_h{h}_not_worse_than_mechanical_by_0.03'] = {'lane': lane, 'min_mechanical': None if None in (rw, dr) else min(rw, dr), 'pass': ok}
    s2 = []
    for h in VERDICT_HORIZONS:
        b = m(h, 'lane_base', 'bias_median')
        ok = None if b is None else abs(b) <= .10
        s2.append(ok)
        checks[f'S2_h{h}_bias_within_0.10'] = {'bias_median': b, 'pass': ok}
    s3 = []
    for h in (3, 5):
        band = ((hz.get(h) or {}).get('common') or {}).get('band') or {}
        ok = None if not band.get('n') else (band['within_band'] >= .5 and band['below_bear'] <= .35 and band['above_bull'] <= .35)
        s3.append(ok)
        checks[f'S3_h{h}_band'] = dict(band, **{'pass': ok})
    S1, S2, S3 = all(x is True for x in s1), all(x is True for x in s2), all(x is True for x in s3)
    verdict = 'PASS' if S1 and S2 and S3 else ('PARTIAL' if S2 else 'FAIL')
    meaning = {'PASS': 'the lane may appear next to the DCF as the expected price path, carrying its measured error band',
               'PARTIAL': 'S2 holds but S1 or S3 fails: shown only as a draft row labelled with what failed',
               'FAIL': 'S2 fails: recorded in docs; not shown in the table'}[verdict]
    failed = [k for k, v in checks.items() if v.get('pass') is not True]
    return {'verdict': verdict, 'S1_accuracy': S1, 'S2_bias': S2, 'S3_band': S3, 'checks': checks, 'failed': failed, 'meaning': meaning}


def evaluate(rows, prices, engine_rows=None, spy='SPY'):
    """Sections 6.3-6.8 and the verdict of 6.7. rows: lane rows (one per symbol x formation, status 'ok' or not); prices:
    {symbol: backtest.load_prices output} including SPY; engine_rows: scripts/backtest_engine.py rows (base_irr)."""
    irr = {(r['symbol'], r['formation']): r['base_irr'] for r in (engine_rows or []) if r.get('base_irr') is not None}
    names = ['lane_base'] + list(BASELINES)
    hz = {}
    for h in HORIZONS:
        y0, y1 = FORMATION_YEARS[h]
        inrange = [r for r in rows if y0 <= int(r['formation'][:4]) <= y1]
        excl = {'no lane path': 0, 'no realized price': 0, 'no engine base_irr': 0, 'no trailing 3-year EPS CAGR': 0}
        reasons = {}
        lane_only, common = [], []
        for r in inrange:
            if r.get('status') != 'ok':
                excl['no lane path'] += 1
                for x in r.get('reasons') or [r.get('status')]:
                    reasons[x] = reasons.get(x, 0) + 1
                continue
            R = realized_ratio(prices.get(r['symbol']), r['as_of'], h)
            if R is None:
                excl['no realized price'] += 1
                continue
            key = (r['symbol'], r['formation'])
            rec = {'symbol': r['symbol'], 'formation': r['formation'], 'as_of': r['as_of'], 'h': h, 'price': r['price'], 'R': R,
                   'actual': r['price'] * R, 'stage': r['stage'], 'cyclical': r['cyclical'], 'size_class': r['size_class'],
                   'multiple_today': r['multiple_today'], 'preds': predictions(r, h, irr.get(key))}
            lane_only.append(rec)
            miss = False
            if key not in irr:
                excl['no engine base_irr'] += 1
                miss = True
            if r.get('eps_cagr_3y') is None:
                excl['no trailing 3-year EPS CAGR'] += 1
                miss = True
            if not miss:
                common.append(rec)
        rec_h = {'formations': [y0, y1], 'n_in_range': len(inrange), 'excluded': excl, 'exclusion_reasons': reasons,
                 'common': {'n': len(common)}, 'lane_only': {'n': len(lane_only)}}
        if common:
            c = rec_h['common']
            c['metrics'] = {n: _metrics(common, n) for n in names + ['lane_bear', 'lane_bull', 'engine_irr_total']}
            c['head_to_head'] = _head_to_head(common, 'lane_base')
            c['band'] = _band(common)
            c['rank'] = {n: _rank_stats(common, n) for n in names}
            c['by'] = {'formation_year': _breakdown(common, lambda r: r['formation'][:4], names),
                       'stage': _breakdown(common, lambda r: r['stage'], names),
                       'cyclical': _breakdown(common, lambda r: r['cyclical'], names),
                       'multiple_bucket': _breakdown(common, lambda r: _bucket(r['multiple_today']), names),
                       'size_class': _breakdown(common, lambda r: r['size_class'], names)}
            c['deflation_factors'] = _deflate(common, prices, spy)
            ok = [r for r in common if r.get('actual_deflated')]
            c['deflated'] = {'n': len(ok), 'metrics': {n: _metrics(ok, n, 'actual_deflated') for n in names + ['lane_bear', 'lane_bull']},
                             'band': _band(ok, 'actual_deflated'), 'head_to_head': _head_to_head(ok, 'lane_base', 'actual_deflated')}
        if lane_only:
            lo = rec_h['lane_only']
            lo['metrics'] = {n: _metrics(lane_only, n) for n in ('lane_base', 'lane_bear', 'lane_bull', 'random_walk', 'drift_7')}
            lo['head_to_head'] = {k: v for k, v in _head_to_head(lane_only, 'lane_base').items() if k in ('random_walk', 'drift_7')}
            lo['band'] = _band(lane_only)
        hz[h] = rec_h
    ok_rows = [r for r in rows if r.get('status') == 'ok']
    diag = {'n_rows_ok': len(ok_rows), 'n_rows': len(rows)}
    if ok_rows:
        by_class = {}
        for r in ok_rows:
            by_class.setdefault(r['size_class'], []).append(r)
        diag['by_size_class'] = {k: {'n': len(v), 'median_lambda_base': statistics.median(x['scenarios']['base']['lambda'] for x in v),
                                     'median_share_5y_base': statistics.median(x['scenarios']['base']['base_rate_share_5y'] for x in v),
                                     'share_lambda_shrunk_base': sum(1 for x in v if x['scenarios']['base']['lambda'] < 1) / len(v)}
                                 for k, v in sorted(by_class.items())}
        prem = sorted(r['premium_today'] / r['multiple_today'] for r in ok_rows if r['multiple_today'])
        q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
        diag['premium_over_multiple'] = {'n': len(prem), 'p10': q(prem, .1), 'p25': q(prem, .25), 'median': q(prem, .5), 'p75': q(prem, .75), 'p90': q(prem, .9)}
        ratio = sorted(r['pe_regression_diagnostic']['ratio_justified_to_reg'] for r in ok_rows if r['pe_regression_diagnostic'].get('ratio_justified_to_reg'))
        diag['justified_over_damodaran_regression'] = {'n': len(ratio), 'p25': q(ratio, .25), 'median': q(ratio, .5), 'p75': q(ratio, .75),
                                                       'note': 'Damodaran January-2026 fit: diagnostic only, not point in time'}
        flags = {}
        for r in ok_rows:
            for f in r['flags']:
                flags[f] = flags.get(f, 0) + 1
        diag['flag_shares'] = {k: v / len(ok_rows) for k, v in sorted(flags.items())}
        diag['multiple_today'] = {'median': statistics.median(r['multiple_today'] for r in ok_rows),
                                  'median_justified': statistics.median(r['justified_today'] for r in ok_rows)}
    verdict = _verdict(hz)
    return {'spec_version': SPEC_VERSION, 'horizons': hz, 'diagnostics': diag, 'verdict': verdict,
            'caveats': ["survivorship: the universe is today's largest US companies, so every realized price is a survivor's; the deflated "
                        'check (6.8) rescales each window to SPY and is reported next to the raw results, not instead of them',
                        "in-sample design: the stage parameters, base rates and the method were written in 2026 with the period's history known; "
                        'the preregistration fixed them before this run, but they are not out of sample',
                        'no guidance or consensus in the backtest: the run-rate + base-rate variant only; guided paths are untested',
                        'one formation a year: yearly rank statistics with t below about 2 are noise',
                        "the engine_irr baseline comes from the engine walk-forward rows supplied (scripts/backtest_engine.py)"]}
