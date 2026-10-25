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

Outcome (21.10.2026, docs/PRICE_PATH_RESULTS_2026-10-21.md): the preregistered walk-forward FAILED. The base path's median log
error was -0.11 / -0.31 / -0.50 at 1 / 3 / 5 years, the random walk's; EPS rose 38% on the median path while the multiple fell
28% toward the justified level the market never converged on. The lane is not shown in the valuation table (TABLE_STATUS);
price_path still runs for research with MEASURED in its band statement. A second version needs a new preregistration.

Spec 2 (docs/prereg_price_path_v2.md, registered 25.10.2026 before its code; selected with spec_version=2, spec 1 above stays the
default and unchanged as the lane_v1 baseline): run rate = mean of the latest FY, latest quarter and 3-year CAGR; the TTM margin
held in the base for every company, the bear / bull margins at the company's own 10-year low / high bounded at half / 1.5x
today's; bear / bull growth fading to the size class p25 / p75 at the same half-life; the multiple target is the market's own
point-in-time trailing P/E by growth x margin bucket (p25 / median / p75 of the universe at the same as_of, CELL_MIN rows with a
growth-row and whole-cross-section fallback), today's log premium over the class median (clipped at +-ln 3) halving at the stage
half-life; bear <= base <= bull in growth, margin, multiple and price every year by construction, asserted (OrderingError).
Section 10 of that document logs every pre-run clarification of this implementation.
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
# Measured walk-forward error of the base path on the common sample (docs/PRICE_PATH_RESULTS_2026-10-21.md, the first and
# only run, 21.10.2026: 490 companies x formations 2016-2025, survivors, run-rate + base-rate variant, no guidance). Written
# AFTER the preregistered run and shown with every live path; it changes no price. Keys: horizon -> {'mdale', 'within25',
# 'bias_median', 'n'}; e = log(pred / actual). The random walk (today's price) had bias -0.120 / -0.311 / -0.501 and MdALE
# 0.195 / 0.362 / 0.557 at the same horizons: the base path's five-year price is, on median, today's price.
MEASURED = {1: {'n': 2600, 'bias_median': -.113, 'mdale': .213, 'within25': .530},
            3: {'n': 2023, 'bias_median': -.311, 'mdale': .399, 'within25': .288},
            5: {'n': 1435, 'bias_median': -.504, 'mdale': .588, 'within25': .176}}
MEASURED_VERDICT = ('FAIL: S2 bias, S1 accuracy and S3 band all failed the preregistered criteria (docs/prereg_price_path.md 6.7); '
                    'the lane is recorded in docs and not shown in the valuation table')
# The section 6.4 baselines on the same common sample (summary.json), kept next to MEASURED so a live output can state the
# measured error of what IS shown in the table instead of this lane: the engine's base IRR at today's price
# (render.hebrew_table's expected-return row; engine_irr = P_0 x (1 + base_irr - dividend yield)^h, price only). Record only:
# nothing here enters a price or the hashed output. engine_irr was measured with the engine at commit ff36ede (v0.12.0, the
# 2026-10-21 walk-forward rows); later engine changes (e.g. terminal growth = risk-free, 005f64b) are not in that measurement.
MEASURED_BASELINES = {
    'random_walk':   {1: {'bias_median': -.120, 'mdale': .195, 'within25': .563}, 3: {'bias_median': -.311, 'mdale': .362, 'within25': .313},
                      5: {'bias_median': -.501, 'mdale': .557, 'within25': .200}},
    'drift_7':       {1: {'bias_median': -.053, 'mdale': .173, 'within25': .609}, 3: {'bias_median': -.108, 'mdale': .278, 'within25': .427},
                      5: {'bias_median': -.162, 'mdale': .363, 'within25': .319}},
    'analyst_const': {1: {'bias_median': -.030, 'mdale': .190, 'within25': .573}, 3: {'bias_median': -.013, 'mdale': .374, 'within25': .315},
                      5: {'bias_median': -.023, 'mdale': .577, 'within25': .210}},
    'engine_irr':    {1: {'bias_median': -.065, 'mdale': .175, 'within25': .600}, 3: {'bias_median': -.163, 'mdale': .286, 'within25': .412},
                      5: {'bias_median': -.270, 'mdale': .408, 'within25': .287}},
}
# section 6.7: PASS -> the lane may appear next to the DCF; PARTIAL -> a draft row labelled with what failed; FAIL -> not shown
TABLE_STATUS = {'PASS': 'shown', 'PARTIAL': 'draft', 'FAIL': 'not_shown'}[MEASURED_VERDICT.split(':')[0]]
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


def anchors(fb, an, qa, card, price, rf, erp, cpi=None, spec_version=SPEC_VERSION):
    """Section 3.1 anchors from a FactBook at as_of with its dataqa / analysis / model card outputs, the traded price, the
    risk-free rate and the ERP. cpi: CPI-U index at as_of (section 4; base_rates.CPI_NOW when None, for a live run).
    -> {'status': 'ok', ...} or {'status': 'excluded', 'reasons': [...]} (NI_0 <= 0 etc.; counted by the backtest).
    spec_version=2 reads the anchors of docs/prereg_price_path_v2.md section 3.1 instead (anchors_v2)."""
    if spec_version == SPEC_VERSION_2:
        return anchors_v2(fb, an, qa, card, price, rf, erp, cpi)
    if spec_version != SPEC_VERSION:
        raise ValueError(f'spec_version {spec_version!r} unknown (registered: {SPEC_VERSIONS})')
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


def irr_path_band_he():
    """The one sentence recommended under the valuation table's expected-return row (docs/PRICE_PATH_2026-10.md, presentation;
    not wired into render.hebrew_table in this build): the measured error of a price path compounding at the engine's base IRR
    less the dividend yield, the only price path measured so far (engine ff36ede, see MEASURED_BASELINES). Plain Hebrew,
    numbers from MEASURED_BASELINES['engine_irr'] (the median shortfall is 1 - exp(median log error)); it is a record of the
    2016-2025 survivor test, not a forecast."""
    m = MEASURED_BASELINES['engine_irr']
    w = ' / '.join(f"{m[h]['within25']:.0%}" for h in (1, 3, 5))
    b = ' / '.join(f"{(1 - math.exp(m[h]['bias_median'])) * 100:.0f}%" for h in (1, 3, 5))
    return (f'במבחן על 2016–2025 (חברות גדולות ששרדו; מחיר בלבד, בלי דיבידנדים) מחיר שעולה כל שנה בקצב התשואה הצפויה פחות הדיבידנד '
            f'היה בטווח של ±25% מהמחיר שהתממש ב־{w} מהמקרים אחרי 1 / 3 / 5 שנים, ובחציון נמוך ממנו בכ־{b}. '
            'התשואה הצפויה היא הערכה מותנית, לא תחזית למחיר המניה.')


def price_path(anch, inputs=None, spec_version=SPEC_VERSION, cross_section=None):
    """Sections 3.2-3.6 on an anchors() dict (pure, deterministic, hashed). inputs: section 5 (live engine only).
    spec_version=2: docs/prereg_price_path_v2.md on anchors(..., spec_version=2) with the point-in-time cross-section table of
    its section 3.4 (cross_section_table); spec 1 (the default) is unchanged and takes no table."""
    if spec_version == SPEC_VERSION_2:
        return price_path_v2(anch, inputs, cross_section)
    if spec_version != SPEC_VERSION or anch.get('spec_version', SPEC_VERSION) != SPEC_VERSION:
        raise ValueError(f'spec 1 needs spec-1 anchors (spec_version {spec_version!r}, anchors {anch.get("spec_version", SPEC_VERSION)!r})')
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
               table_status=TABLE_STATUS, input_sha256=_sha({'anchors': anch, 'inputs': inputs or {}, 'spec_version': SPEC_VERSION}), output_sha256=_sha(core))
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


def _head_to_head(recs, name, actual_key='actual', baselines=BASELINES):
    out = {}
    for b in baselines:
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


# =================================================================== spec_version 2 (docs/prereg_price_path_v2.md)
# Registered 25.10.2026 after spec 1 FAILED and before this code; sections 2-7 of that document are frozen and every reading
# this implementation had to choose is logged in its section 10 (pre-run clarifications). Spec 1 above is unchanged: it is
# the default of anchors / price_path / evaluate and the lane_v1 baseline of v2 section 6.4.

SPEC_VERSION_2 = 2
SPEC_VERSIONS = (SPEC_VERSION, SPEC_VERSION_2)
# ---- constants of docs/prereg_price_path_v2.md section 4 (new or changed; the rest are spec 1's above)
GROWTH_Q = {'bear': .25, 'bull': .75}          # T_bear / T_bull: the size class' p25 / p75 (bins of base_rates.CLASSES); base: median
BEAR_MARGIN_MIN, BULL_MARGIN_MAX = .5, 1.5     # the bear never below half today's margin, the bull never above 1.5x it
MARGIN_HISTORY_YEARS, MARGIN_HISTORY_MIN = 10, 3   # own fiscal-year net margins: last <= 10, at least 3 (else m_TTM -/+ MS)
GROWTH_BUCKETS = ((0., '<0%'), (.05, '0-5%'), (.10, '5-10%'), (.20, '10-20%'), (.30, '20-30%'), (float('inf'), '>30%'))
MARGIN_BUCKETS = ((.05, '<5%'), (.10, '5-10%'), (.20, '10-20%'), (float('inf'), '>20%'))
CS_Q = {'bear': .25, 'base': .50, 'bull': .75}  # cross-section quantile per scenario (interquartile band)
CELL_MIN = 20                                    # rows a cell (else the growth row, else the whole cross-section) needs
CS_PE_RANGE = (1., 500.)                         # raw trailing P/E kept in the table (unit defects, near-zero earnings out)
L0_CLIP = math.log(3)                            # today's premium: one third to three times the class median
PE_FLOOR_V2, PE_CAP_V2 = 5., 120.                # floor as spec 1; cap ~3x the highest class median (39.6x, > 30% growers)
LEVELS = ('cell', 'growth_row', 'cross_section')  # the 3.4 fallback ladder
ORDER_KEYS = ('growth', 'margin', 'multiple', 'price')
# Measured error of spec 2: EMPTY until its single preregistered walk-forward (section 6) is run and recorded. Until then the
# path and the band are untested, the band statement says so and the lane is not shown (section 7 / 9).
MEASURED_V2 = {}
MEASURED_VERDICT_V2 = ('NOT RUN: spec_version 2 is registered (docs/prereg_price_path_v2.md) and implemented; its preregistered '
                       'walk-forward has not been run, so nothing about its accuracy is known')
TABLE_STATUS_V2 = 'not_shown'
S3_TAIL_V2 = .30                                 # section 7 S3: neither tail above 30% (spec 1: 35%)
S4_LIMIT = .10                                   # section 7 S4: |median e_rev|, |median e_margin|, |median e_multiple| at h = 3
BASELINES_V2 = BASELINES + ('lane_v1',)
ATTR_TERMS = ('e_total', 'e_rev', 'e_margin', 'e_shares', 'e_multiple')
MULTIPLE_BUCKETS_V2 = ((10, '<10x'), (15, '10-15x'), (20, '15-20x'), (30, '20-30x'), (60, '30-60x'), (float('inf'), '>60x'))
DEFECT_PE = 1.                                   # section 2 / 6.8: raw M_0 < 1x is a share-unit or revenue-unit defect row


class OrderingError(AssertionError):
    """v2 3.6: bear <= base <= bull in growth, margin, multiple and price every year. A violation is an error, never a flag."""


# ---------------------------------------------------------------- v2 3.1 anchors

def anchors_v2(fb, an, qa, card, price, rf, erp, cpi=None):
    """v2 section 3.1. Rev_0, NI_0 (> 0; facts.ttm for the same period end, spec-1 deviation 2), S_0, dividends, stage and the
    exclusions as spec 1. g_run = mean of the available among the latest FY growth, the latest quarter vs a year earlier and the
    3-year revenue CAGR (a cyclical: spec 1's trend rule unchanged); clipped to G_RUN. m_TTM = NI_0 / Rev_0 is the base margin
    for every company; m_lo / m_hi = lowest / highest net margin of the last <= 10 fiscal years with revenue > 0 (>= 3, else
    m_TTM -/+ MS, flagged margin_history_short); spec 1's mid-cycle median is kept as a diagnostic only.
    -> {'status': 'ok', 'spec_version': 2, ...} or {'status': 'excluded', 'reasons': [...]}."""
    ttm = (an or {}).get('ttm') or {}
    rev0, ni0 = _num(ttm.get('revenue')), _num(ttm.get('net_income'))
    res = ((qa or {}).get('resolved') or {}).get('shares_outstanding') or {}
    s0 = _num(res.get('value'))
    stage = ((card or {}).get('stage') or {}).get('value')
    cyc = (card or {}).get('cyclical') or {}
    reasons, flags, notes = [], [], []
    ni_ref = F.ttm(fb, 'net_income')
    if ni0 is None and rev0 and ni_ref and ttm.get('period_end') and abs((_d(ni_ref['period_end']) - _d(ttm['period_end'])).days) <= F.SAME_PERIOD_DAYS:
        ni0 = _num(ni_ref['value'])                       # the same twelve months, read as spec 1 (its deviation 2)
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
    g_fy, g_3 = _metric(an, 'revenue_growth_last'), _metric(an, 'revenue_cagr_3y')
    g_q, q_ref = None, None
    q = F.series(fb, 'revenue', 'quarter')
    if q:
        end, v, f = q[-1]
        want = _d(end) - timedelta(days=364)
        prior = [x for x in q[:-1] if abs((_d(x[0]) - want).days) <= 10]
        if prior and prior[0][1] and prior[0][1] > 0 and v is not None:
            g_q = v / prior[0][1] - 1
            q_ref = {'quarter_end': end, 'prior_quarter_end': prior[0][0], 'source_id': f['source_id']}
    legs = [(label, x) for label, x in (('latest fiscal year growth', g_fy), ('latest quarter vs a year earlier', g_q),
                                        ('3-year revenue CAGR', g_3)) if x is not None]
    trend = None
    if cyclical:
        trend, basis = _metric(an, 'revenue_cagr_5y'), 'revenue_cagr_5y'
        if trend is None:
            trend, basis = g_3, 'revenue_cagr_3y'
    if trend is not None:
        g_raw, g_basis = _clip(trend, *CYC_G), f'cyclical trend: {basis} clipped to {CYC_G}'
        flags.append('cyclical_growth_trend')
    else:
        if not legs:
            return {'status': 'excluded', 'reasons': ['no growth run rate (no fiscal-year, quarterly or 3-year comparison)']}
        g_raw = sum(x for _, x in legs) / len(legs)
        g_basis = 'mean of ' + ', '.join(label for label, _ in legs)
        if cyclical:
            g_basis = 'cyclical without a multi-year trend: ' + g_basis
            flags.append('cyclical_trend_missing')
    g_run = _clip(g_raw, *G_RUN)
    if g_run != g_raw:
        flags.append('run_rate_clipped')
    m_ttm = ni0 / rev0
    ms = (1 - TAX_STAT) * STAGE[stage]['m_spread']
    rows = sorted((pe, r) for pe, r in ((an or {}).get('annual') or {}).items()
                  if _num(r.get('revenue')) and r['revenue'] > 0 and _num(r.get('net_income')) is not None)[-MARGIN_HISTORY_YEARS:]
    hist = [(pe, r['net_income'] / r['revenue']) for pe, r in rows]
    if len(hist) >= MARGIN_HISTORY_MIN:
        ms_hist = [m for _, m in hist]
        m_lo, m_hi, m_mid = min(ms_hist), max(ms_hist), statistics.median(ms_hist)
        m_basis = f'own fiscal-year net margins, last {len(hist)} years ({hist[0][0]} to {hist[-1][0]})'
    else:
        m_lo, m_hi, m_mid = m_ttm - ms, m_ttm + ms, None
        m_basis = f'fewer than {MARGIN_HISTORY_MIN} fiscal years: TTM margin -/+ the after-tax stage spread {ms:.4f}'
        flags.append('margin_history_short')
    s_cagr = _metric(an, 'share_count_cagr_3y')
    roic = _metric(an, 'roic')
    if roic is None:
        flags.append('roic_missing')
    div = _num(ttm.get('dividends'))
    return {'status': 'ok', 'spec_version': SPEC_VERSION_2, 'as_of': fb['as_of'], 'ticker': (fb.get('company') or {}).get('ticker'),
            'price': float(price), 'risk_free': float(rf), 'erp': float(erp), 'cpi': float(cpi or base_rates.CPI_NOW),
            'cpi_basis': 'supplied (CPI-U at as_of)' if cpi else f'base_rates.CPI_NOW {base_rates.CPI_NOW} ({base_rates.CPI_NOW_DATE})',
            'stage': stage, 'cyclical': cyclical, 'revenue': rev0, 'net_income': ni0, 'shares': s0,
            'g_run': g_run, 'g_raw': g_raw, 'g_basis': g_basis, 'g_fiscal_year': g_fy, 'g_quarter': g_q, 'g_3y': g_3, 'quarter_ref': q_ref,
            'm_ttm': m_ttm, 'm_lo': m_lo, 'm_hi': m_hi, 'm_mid_diagnostic': m_mid, 'margin_years': len(hist), 'm_basis': m_basis,
            'share_cagr': s_cagr, 'roic': roic, 'dividends_ttm': div, 'dividend_yield': (div / (price * s0)) if div is not None else None,
            'ttm_period_end': ttm.get('period_end'), 'ttm_method': ttm.get('method'),
            'references': {'revenue': _facts_ref(F.ttm(fb, 'revenue')), 'net_income': _facts_ref(ni_ref),
                           'shares': {k: res.get(k) for k in ('as_of', 'basis', 'source_id')}},
            'flags': flags, 'notes': notes}


# ---------------------------------------------------------------- v2 3.2 growth (size-class quantile targets, one half-life)

def class_quantile(freqs, q):
    """q-quantile (real CAGR, a fraction) of a Base Rate Book bin distribution: linear inside a 5-point bin, the open tails read
    as 5-point bins -- the inverse of base_rates.share_at_least (share_at_least(freqs, class_quantile(freqs, q)) = 1 - q)."""
    edges, tail = base_rates.EDGES, base_rates.TAIL_WIDTH
    lows, highs = (edges[0] - tail,) + tuple(edges), tuple(edges) + (edges[-1] + tail,)
    target, cum = q * sum(freqs), 0.
    for lo, hi, f in zip(lows, highs, freqs):
        if f > 0 and cum + f >= target:
            return lo + (hi - lo) * (target - cum) / f
        cum += f
    return highs[-1]


_QUANTILE_CACHE = {}


def class_target(table, n, scenario):
    """T_s(n), nominal: base = the book's printed median at horizon n (class_median, as spec 1); bear / bull = the class p25 /
    p75 read from the bins of each tabulated horizon (1 / 3 / 5 / 10), linear between them and flat after the last (the
    median's own rule), + PI; clamped T_bear <= T_base <= T_bull."""
    med = class_median(table, n)
    if scenario == 'base':
        return med
    q = GROWTH_Q[scenario]
    vals = {}
    for h in table:
        key = (tuple(table[h][0]), q)
        if key not in _QUANTILE_CACHE:
            _QUANTILE_CACHE[key] = class_quantile(key[0], q)
        vals[h] = _QUANTILE_CACHE[key]
    hs = sorted(vals)
    if n <= hs[0]:
        x = vals[hs[0]]
    elif n >= hs[-1]:
        x = vals[hs[-1]]
    else:
        a = max(k for k in hs if k <= n)
        b = min(k for k in hs if k >= n)
        x = vals[a] if a == b else vals[a] + (vals[b] - vals[a]) * (n - a) / (b - a)
    x += PI
    return min(x, med) if scenario == 'bear' else max(x, med)


def growth_path_v2(g1, stage, scenario, rev0, cpi, lam=1., years=PATH_YEARS + 1, fixed=None, floor=None, cap=None):
    """v2 3.2: g_h(s) = T_s(h) + lambda x (g_1(s) - T_s(1)) x 0.5^((h-1) / HL), HL = the stage half-life for every scenario,
    T_s = the scenario's class target (class_target), the class re-read each year from this path's revenue deflated at PI.
    Ordering by construction: cap (the base path) bounds the bear from above and floor (the base path) the bull from below,
    year by year; the bounded rate is the one that compounds revenue, so next year's class is read from this path. fixed =
    {year: growth} (section 5, live engine): the fade starts from the last fixed year k0, as spec 1.
    -> (growth list, class label per year, target per year, number of years the bound changed)."""
    hl = STAGE[stage]['half_life']
    fixed = {int(k): float(v) for k, v in (fixed or {}).items()}
    k0 = max(fixed) if fixed else 1
    out, labels, targets, bound, rev, excess = [], [], [], 0, rev0, None
    for h in range(1, years + 1):
        label, table = size_class(rev / (1 + PI) ** (h - 1), cpi)
        t = class_target(table, h, scenario)
        if fixed and h <= k0:
            g = fixed.get(h, g1)
            if h == k0:
                excess = g - t
        else:
            if excess is None:
                excess = g1 - t
            g = t + lam * excess * .5 ** ((h - k0) / hl)
        g_raw = g
        if cap is not None:
            g = min(g, cap[h - 1])
        if floor is not None:
            g = max(g, floor[h - 1])
        bound += g != g_raw
        out.append(g)
        labels.append(label)
        targets.append(t)
        rev *= 1 + g
    return out, labels, targets, bound


def shrink_v2(g1, stage, scenario, rev0, cpi, ceil, fixed=None, floor=None, cap=None):
    """The base-rate ceiling of spec 1 (largest lambda in (0, 1] whose 5-year real CAGR is not rarer in the formation size class
    than ceil; bisection, BISECT_STEPS) on the v2 path, ordering bounds included. -> (lambda, share)."""
    freqs5 = size_class(rev0, cpi)[1][5][0]

    def share(lam):
        g = growth_path_v2(g1, stage, scenario, rev0, cpi, lam, fixed=fixed, floor=floor, cap=cap)[0]
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


# ---------------------------------------------------------------- v2 3.3 margins

def margin_targets(m_ttm, m_lo, m_hi, stage):
    """v2 3.3 targets: base m_TTM; bear max(min(m_lo, m_TTM - MS), 0.5 m_TTM); bull min(max(m_hi, m_TTM + 0.75 MS), 1.5 m_TTM),
    MS = (1 - TAX_STAT) x stage m_spread. -> ({scenario: target}, {scenario: which bound set it})."""
    ms = (1 - TAX_STAT) * STAGE[stage]['m_spread']
    worse, better = min(m_lo, m_ttm - ms), max(m_hi, m_ttm + BULL_MARGIN_SHARE * ms)
    bear, bull = max(worse, BEAR_MARGIN_MIN * m_ttm), min(better, BULL_MARGIN_MAX * m_ttm)
    basis = {'base': 'ttm',
             'bear': 'half_of_ttm' if bear != worse else ('own_low' if m_lo < m_ttm - ms else 'stage_spread'),
             'bull': '1.5x_ttm' if bull != better else ('own_high' if m_hi > m_ttm + BULL_MARGIN_SHARE * ms else 'stage_spread')}
    return {'base': m_ttm, 'bear': bear, 'bull': bull}, basis


def margin_path_v2(m_ttm, target, stage, years=PATH_YEARS, m1=None):
    """m_h = m_TTM + (target - m_TTM) x min(h / CONV, 1), CONV = stage conv (the base: target = m_TTM, held); m1 replaces year 1
    (section 5 guided margin, base only); floored at M_FLOOR (flagged). -> (margins m_1..m_years, floored?)."""
    conv = STAGE[stage]['conv']
    path = [m_ttm + (target - m_ttm) * min(h / conv, 1.) for h in range(1, years + 1)]
    if m1 is not None:
        path[0] = m1
    return [max(M_FLOOR, m) for m in path], any(m < M_FLOOR for m in path)


# ---------------------------------------------------------------- v2 3.4 the market's point-in-time multiple table

def _bucket_of(x, buckets):
    for lim, label in buckets:
        if x < lim:
            return label
    return buckets[-1][1]


def growth_bucket(g):
    return _bucket_of(g, GROWTH_BUCKETS)


def margin_bucket(m):
    return _bucket_of(m, MARGIN_BUCKETS)


def quantile(xs, q):
    """q-quantile of sorted xs, linear between order statistics at (n - 1) q (numpy's default; statistics.median at 0.5)."""
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    i = int(math.floor(pos))
    j = min(i + 1, len(xs) - 1)
    return xs[i] + (xs[j] - xs[i]) * (pos - i)


def cross_section_entry(anch, symbol=None):
    """One company's row of the 3.4 table from its v2 anchors (status ok): growth bucket by g_run, margin bucket by m_TTM and
    the raw trailing P/E P_0 S_0 / NI_0."""
    return {'symbol': symbol or anch.get('ticker'), 'g_run': anch['g_run'], 'm_ttm': anch['m_ttm'],
            'pe': anch['price'] * anch['shares'] / anch['net_income']}


def cross_section_table(entries, as_of, source):
    """v2 3.4: the market's point-in-time trailing P/E table at as_of from every company with usable anchors then (entries of
    cross_section_entry). PE outside CS_PE_RANGE is left out (counted). Point in time by construction (nothing after as_of),
    survivor-biased by construction (today's universe). source names the run (section 5: the live engine names the saved
    universe run and its date). -> {'as_of', 'source', 'n', 'n_outside_pe_range', 'cells': {'g|m': sorted PEs},
    'growth': {g: sorted PEs}, 'all': sorted PEs, 'symbols', 'sha256'}."""
    kept, outside = [], 0
    for e in sorted(entries, key=lambda e: (str(e.get('symbol')), e.get('pe') or 0.)):
        pe = _num(e.get('pe'))
        if pe is None or not CS_PE_RANGE[0] <= pe <= CS_PE_RANGE[1]:
            outside += 1
            continue
        kept.append((str(e.get('symbol')), growth_bucket(e['g_run']), margin_bucket(e['m_ttm']), pe))
    if not kept:
        raise ValueError(f'cross-section at {as_of}: no company with a raw P/E in {CS_PE_RANGE}')
    cells, rows = {}, {}
    for _, gb, mb, pe in kept:
        cells.setdefault(f'{gb}|{mb}', []).append(pe)
        rows.setdefault(gb, []).append(pe)
    t = {'as_of': str(as_of)[:10], 'source': source, 'n': len(kept), 'n_outside_pe_range': outside,
         'cells': {k: sorted(v) for k, v in sorted(cells.items())}, 'growth': {k: sorted(v) for k, v in sorted(rows.items())},
         'all': sorted(pe for *_, pe in kept), 'symbols': [s for s, *_ in kept]}
    t['sha256'] = _sha(t)
    return t


def cs_multiple(table, g, m, q):
    """M_cs(t; bucket(g), bucket(m); q): the q-quantile of the cell's P/E when it has CELL_MIN rows, else of the growth bucket's
    row (>= CELL_MIN), else of the whole cross-section. -> (multiple, {'growth_bucket', 'margin_bucket', 'level', 'n', 'q'})."""
    gb, mb = growth_bucket(g), margin_bucket(m)
    xs, level = table['cells'].get(f'{gb}|{mb}') or [], LEVELS[0]
    if len(xs) < CELL_MIN:
        xs, level = table['growth'].get(gb) or [], LEVELS[1]
        if len(xs) < CELL_MIN:
            xs, level = table['all'], LEVELS[2]
    return quantile(xs, q), {'growth_bucket': gb, 'margin_bucket': mb, 'level': level, 'n': len(xs), 'q': q}


def latest_cross_section(tables, as_of):
    """Section 5 (live engine): the saved table (tables = {formation: cross_section_table}, e.g. a v2 run's tables.json) with
    the latest as_of not after the valuation's as_of; the output names its source and date."""
    ok = [t for t in (tables or {}).values() if t and _d(t['as_of']) <= _d(as_of)]
    if not ok:
        raise ValueError(f'no saved cross-section table at or before {as_of}')
    return max(ok, key=lambda t: t['as_of'])


# ---------------------------------------------------------------- v2 3.5 / 3.6 the path

def check_order(paths):
    """v2 3.6 assertion on unrounded values: paths = {scenario: [{'growth', 'margin', 'multiple', 'price'} per year]}.
    Raises OrderingError on the first violation; True otherwise."""
    for i in range(len(paths['base'])):
        for k in ORDER_KEYS:
            lo, mid, hi = (paths[s][i][k] for s in SCEN)
            if not lo <= mid <= hi:
                raise OrderingError(f'ordering violated in year {i + 1}: {k} bear {lo!r} / base {mid!r} / bull {hi!r}')
    return True


def band_statement_v2():
    if not MEASURED_V2:
        return ("Bear and bull are the method's scenario band (the size class' growth p25 / p75, the company's own margin low / "
                "high bounded at half / 1.5x today's margin, the cross-section's P/E p25 / p75), not a measured confidence "
                'interval. Spec 2 has not been measured: its preregistered walk-forward (docs/prereg_price_path_v2.md section 6) '
                'has not been run in this build, so the path and the band are untested. Dated prices are conditional estimates, '
                'never predicted exchange prices, and never authorize a trade.')
    parts = ', '.join(f"{h}y: within +-25% in {m['within25']:.0%} of rows, median |log error| {m['mdale']:.2f}, median bias "
                      f"{m['bias_median']:+.2f} (n {m['n']})" for h, m in sorted(MEASURED_V2.items()))
    return (f"Bear and bull are the method's scenario band, not a measured confidence interval. Measured on the 2016-2025 "
            f'walk-forward of spec 2 (survivors only; {MEASURED_VERDICT_V2}): {parts}. Guided or consensus years are outside '
            'that measurement. Dated prices are conditional estimates, never predicted exchange prices, and never authorize a trade.')


def _gordon(ni, g, R, ke, rf, h):
    """Spec 1's justified multiple on the v2 base path, diagnostic only (3.6): (raw, clipped to spec 1's [PE_FLOOR, PE_CAP]),
    (None, None) when the formula has no value (it never enters a price)."""
    try:
        return justified_multiple(ni, g, R, ke, rf, h)
    except ValueError:
        return None, None


def price_path_v2(anch, inputs=None, cross_section=None):
    """docs/prereg_price_path_v2.md sections 3.2-3.6 on anchors_v2 output and the point-in-time cross-section table of 3.4 (pure,
    deterministic, hashed; the table's hash is part of the input hash). inputs: section 5 (live engine only, untested)."""
    if anch.get('status') != 'ok':
        raise ValueError('anchors are not usable: ' + '; '.join(anch.get('reasons') or ['unknown']))
    if anch.get('spec_version') != SPEC_VERSION_2:
        raise ValueError('spec 2 needs anchors(..., spec_version=2)')
    tbl = cross_section
    if not tbl or not tbl.get('all'):
        raise ValueError('spec 2 needs the point-in-time cross-section table of its section 3.4 (cross_section_table)')
    if _d(tbl['as_of']) > _d(anch['as_of']):
        raise ValueError(f"cross-section table dated {tbl['as_of']} is after as_of {anch['as_of']} (look-ahead)")
    stage = anch['stage']
    st = STAGE[stage]
    hl = st['half_life']
    live = _check_inputs(inputs, anch['as_of'])
    rf, erp, cpi, rev0, s0, p0, ni0 = (anch[k] for k in ('risk_free', 'erp', 'cpi', 'revenue', 'shares', 'price', 'net_income'))
    ke = rf + erp + st['ke']
    roic = anch.get('roic')
    R = ke if roic is None else _clip(roic, ke, R_MAX)
    m_ttm, g_run = anch['m_ttm'], anch['g_run']
    eps0 = ni0 / s0                                          # reported TTM, not normalized
    m0 = p0 / eps0                                           # the raw trailing P/E; no input cap (the log premium is clipped)
    flags = list(anch.get('flags') or [])
    if len(tbl['all']) < CELL_MIN:
        flags.append('cross_section_below_cell_min')
    shares, s_used = share_path(s0, anch.get('share_cagr'))
    if anch.get('share_cagr') is not None and abs(anch['share_cagr']) > SHARE_CLIP:
        flags.append('share_pace_clipped')
    spread = {'bear': -st['g_spread'], 'base': 0., 'bull': st['g_spread']}
    g1 = {s: g_run + spread[s] for s in SCEN}
    fixed = {s: {k: v + spread[s] for k, v in live['fixed'].items()} for s in SCEN}
    m_target, m_target_basis = margin_targets(m_ttm, anch['m_lo'], anch['m_hi'], stage)
    scen = {}
    for s in ('base', 'bear', 'bull'):                       # the base first: it bounds the bear's and the bull's growth
        bounds = {'cap': scen['base']['g']} if s == 'bear' else ({'floor': scen['base']['g']} if s == 'bull' else {})
        lam, share5 = shrink_v2(g1[s], stage, s, rev0, cpi, live['ceil'][s], fixed=fixed[s] or None, **bounds)
        g, labels, targets, n_bound = growth_path_v2(g1[s], stage, s, rev0, cpi, lam, fixed=fixed[s] or None, **bounds)
        m, floored = margin_path_v2(m_ttm, m_target[s], stage, m1=live['margin_1'] if s == 'base' else None)
        if floored:
            flags.append(f'margin_floored_{s}')
        if lam < 1:
            flags.append(f'lambda_shrunk_{s}')
        if n_bound:
            flags.append(f'growth_ordered_{s}')
        scen[s] = {'lambda': lam, 'share_5y': share5, 'g': g, 'labels': labels, 'targets': targets, 'm': m}
    if live['margin_1'] is not None:                         # section 5 only: a guided m_1 can sit outside the band's year 1
        scen['bear']['m'][0] = min(scen['bear']['m'][0], scen['base']['m'][0])
        scen['bull']['m'][0] = max(scen['bull']['m'][0], scen['base']['m'][0])
    for z in scen.values():
        z['rev'], z['ni'] = [rev0], [ni0]
        for k in range(1, PATH_YEARS + 1):
            z['rev'].append(z['rev'][-1] * (1 + z['g'][k - 1]))
            z['ni'].append(z['rev'][-1] * z['m'][k - 1])
    # 3.5: today's multiple as a log premium over the class median, the same L_0 in every scenario, halving at the stage half-life
    cs0, cell0 = cs_multiple(tbl, g_run, m_ttm, CS_Q['base'])
    l0_raw = math.log(m0) - math.log(cs0)
    l0 = _clip(l0_raw, -L0_CLIP, L0_CLIP)
    if l0 != l0_raw:
        flags.append('premium_clipped')
    paths = {s: [] for s in SCEN}
    years = {s: [] for s in SCEN}
    gordon_years = []
    for h in HORIZONS:
        fade = .5 ** (h / hl)
        raw, cells = {}, {}
        for s in SCEN:
            target, cell = cs_multiple(tbl, scen[s]['g'][h - 1], scen[s]['m'][h - 1], CS_Q[s])
            raw[s] = math.exp(math.log(target) + l0 * fade)
            cells[s] = dict(cell, target=_r(target))
        ordered = {'bear': min(raw['bear'], raw['base']), 'base': raw['base'], 'bull': max(raw['bull'], raw['base'])}
        for s in SCEN:
            if ordered[s] != raw[s]:
                flags.append(f'multiple_ordered_{s}')
            mult = _clip(ordered[s], PE_FLOOR_V2, PE_CAP_V2)
            if mult != ordered[s]:
                flags.append(f"multiple_{'floored' if ordered[s] < PE_FLOOR_V2 else 'capped'}_{s}")
            z = scen[s]
            eps = z['ni'][h] / shares[h]
            price = eps * mult
            paths[s].append({'growth': z['g'][h - 1], 'margin': z['m'][h - 1], 'multiple': mult, 'price': price})
            years[s].append({'year': h, 'revenue': _r(z['rev'][h], 2), 'growth': _r(z['g'][h - 1]), 'growth_target': _r(z['targets'][h - 1]),
                             'margin': _r(z['m'][h - 1]), 'net_income': _r(z['ni'][h], 2), 'shares': _r(shares[h], 2), 'eps': _r(eps),
                             'size_class': z['labels'][h - 1], 'cross_section': cells[s], 'premium_log_left': _r(l0 * fade),
                             'multiple_before_ordering': _r(raw[s]), 'multiple': _r(mult), 'price': _r(price),
                             'basis': live['basis'].get(h) or ('run_rate' if h == 1 else 'base_rate_fade')})
        j_raw, j = _gordon(scen['base']['ni'], scen['base']['g'], R, ke, rf, h)
        cs_base = cells['base']['target']
        gordon_years.append({'year': h, 'raw': _r(j_raw), 'multiple': _r(j), 'cross_section': cs_base,
                             'ratio_to_cross_section': _r(j / cs_base) if j and cs_base else None})
    ordering_checked = check_order(paths)
    j0_raw, j0 = _gordon(scen['base']['ni'], scen['base']['g'], R, ke, rf, 0)
    if j0 is None:
        flags.append('gordon_diagnostic_undefined')
    out_scen = {s: {'lambda': _r(scen[s]['lambda']), 'base_rate_share_5y': _r(scen[s]['share_5y']), 'half_life': hl,
                    'growth_year1': _r(g1[s]), 'growth_target': 'class median' if s == 'base' else f'class p{int(GROWTH_Q[s] * 100)}',
                    'margin_target': _r(m_target[s]), 'margin_target_basis': m_target_basis[s], 'cross_section_quantile': CS_Q[s],
                    'years': years[s]} for s in SCEN}
    ms = (1 - TAX_STAT) * st['m_spread']
    ledger = [
        {'id': 'growth_run_rate', 'value': _r(g_run), 'basis': anch['g_basis'] + (f"; raw {anch['g_raw']:.4f} clipped to {G_RUN}" if 'run_rate_clipped' in flags else ''), 'source': 'facts'},
        {'id': 'growth_spread', 'value': st['g_spread'], 'basis': 'stage g_spread (bear = run rate - spread, bull = + spread in year 1)', 'source': 'engine:forecast_rules'},
        {'id': 'growth_half_life', 'value': hl, 'basis': 'stage half-life, the same for every scenario (Koller et al. growth decay)', 'source': 'engine:forecast_rules'},
        {'id': 'growth_targets', 'value': {'bear': 'p25', 'base': 'median', 'bull': 'p75'}, 'basis': f'size-class growth at each horizon (bins interpolated; open tails as 5-point bins), nominal (+{PI}); class re-read yearly', 'source': base_rates.SOURCE},
        {'id': 'base_rate_ceiling', 'value': live['ceil'], 'basis': 'minimum share of the size class growing at least as fast over 5 years (The Base Rate Book, exhibit 4)', 'source': base_rates.SOURCE},
        {'id': 'size_class_today', 'value': size_class(rev0, cpi)[0], 'basis': f'TTM revenue in 2015 dollars (CPI {cpi})', 'source': base_rates.SOURCE},
        {'id': 'margin_base', 'value': _r(m_ttm), 'basis': 'TTM net margin held (every company, cyclicals too)', 'source': 'facts'},
        {'id': 'margin_bear_target', 'value': _r(m_target['bear']), 'basis': f"max(min(own low {anch['m_lo']:.4f}, TTM - {ms:.4f}), {BEAR_MARGIN_MIN} x TTM) -> {m_target_basis['bear']}; over {st['conv']} years", 'source': RULES},
        {'id': 'margin_bull_target', 'value': _r(m_target['bull']), 'basis': f"min(max(own high {anch['m_hi']:.4f}, TTM + {BULL_MARGIN_SHARE} x {ms:.4f}), {BULL_MARGIN_MAX} x TTM) -> {m_target_basis['bull']}; over {st['conv']} years", 'source': RULES},
        {'id': 'share_path', 'value': _r(s_used), 'basis': f'3-year share-count CAGR clipped to +-{SHARE_CLIP}, same in every scenario', 'source': 'facts'},
        {'id': 'multiple_today', 'value': _r(m0), 'basis': f'price {p0:.4f} / TTM EPS {eps0:.4f} (raw trailing P/E)', 'source': 'market'},
        {'id': 'cross_section_today', 'value': _r(cs0), 'basis': f"median raw trailing P/E of the {cell0['level']} {cell0['growth_bucket']} growth x {cell0['margin_bucket']} margin (n {cell0['n']}) at {tbl['as_of']} ({tbl.get('source')})", 'source': 'market'},
        {'id': 'premium_log', 'value': _r(l0), 'basis': f'ln(multiple today / class median) clipped to +-ln 3; decays at the stage half-life {hl}', 'source': RULES},
        {'id': 'multiple_targets', 'value': CS_Q, 'basis': f'cross-section quantile of the scenario\'s year-h growth x margin cell (>= {CELL_MIN} rows, else growth row, else all); bear <= base <= bull; clipped to [{PE_FLOOR_V2}, {PE_CAP_V2}]', 'source': RULES},
        {'id': 'cost_of_equity', 'value': _r(ke), 'basis': f"diagnostic only (the Gordon multiple): risk-free {rf:.4f} + ERP {erp:.4f} + stage {stage} add-on {st['ke']}", 'source': RULES},
    ] + [{'id': f'live_input_{i + 1}', 'value': None, 'basis': s, 'source': 'inputs'} for i, s in enumerate(live['statements'])]
    core = {'spec_version': SPEC_VERSION_2, 'as_of': anch['as_of'], 'ticker': anch.get('ticker'), 'price': p0, 'eps_ttm': _r(eps0),
            'multiple_today': _r(m0), 'cost_of_equity': _r(ke), 'return_on_capital': _r(R),
            'cross_section': {'as_of': tbl['as_of'], 'source': tbl.get('source'), 'n': len(tbl['all']), 'sha256': tbl.get('sha256') or _sha(tbl)},
            'cross_section_today': dict(cell0, multiple=_r(cs0)), 'premium_log_raw': _r(l0_raw), 'premium_log': _r(l0),
            'gordon': {'today': {'raw': _r(j0_raw), 'multiple': _r(j0), 'cross_section': _r(cs0), 'ratio_to_cross_section': _r(j0 / cs0) if j0 else None},
                       'years': gordon_years, 'basis': f'spec 1 justified multiple on the base path (ROIC {R:.4f}, ke {ke:.4f}), clipped to [{PE_FLOOR}, {PE_CAP}]; diagnostic, never in a price'},
            'margin_history': {'m_ttm': _r(m_ttm), 'm_lo': _r(anch['m_lo']), 'm_hi': _r(anch['m_hi']), 'm_mid_diagnostic': _r(anch.get('m_mid_diagnostic')),
                               'years': anch.get('margin_years'), 'basis': anch.get('m_basis'), 'bear_target': _r(m_target['bear']),
                               'bull_target': _r(m_target['bull']), 'bear_basis': m_target_basis['bear'], 'bull_basis': m_target_basis['bull']},
            'size_class': size_class(rev0, cpi)[0], 'scenarios': out_scen, 'flags': sorted(set(flags)),
            'years_basis': {h: (live['basis'].get(h) or ('run_rate' if h == 1 else 'base_rate_fade')) for h in HORIZONS},
            'live_inputs_used': bool(live['statements']), 'ordering_checked': ordering_checked}
    inp = {'anchors': anch, 'inputs': inputs or {}, 'spec_version': SPEC_VERSION_2, 'cross_section_sha256': core['cross_section']['sha256']}
    return dict(core, assumptions=ledger, band_statement=band_statement_v2(), measured_error=MEASURED_V2, verdict=MEASURED_VERDICT_V2,
                table_status=TABLE_STATUS_V2, input_sha256=_sha(inp), output_sha256=_sha(core))


# ---------------------------------------------------------------- v2 evaluation (docs/prereg_price_path_v2.md sections 6-7)

def predictions_v2(row, h, base_irr=None, v1_row=None):
    """v2 6.4: spec 1's predictors (row = a v2 row) plus lane_v1 = spec 1's base path for the same (symbol, formation)."""
    out = predictions(row, h, base_irr)
    if v1_row and v1_row.get('status') == 'ok':
        out['lane_v1'] = v1_row['scenarios']['base']['years'][h - 1]['price']
    return out


def _cell_label(row):
    c = row.get('cross_section_today') or {}
    return f"{c.get('growth_bucket')} x {c.get('margin_bucket')}"


def attribution_terms(years, h, price0, R, later):
    """v2 6.6 / section 1: log(P_lane / P_actual) = e_rev + e_margin + e_shares + e_multiple (exact) for a lane's year-h values
    (years = a row's scenario years), the realized side from the v2 row at t + h (later: its anchors and traded price).
    f = R x P_0 / P(t+h) is the split factor between t and t + h, so the realized share count in t units is S(t+h) / f and
    M_real = P(t+h) S(t+h) / NI(t+h). None when a term has no logarithm (NI(t+h) <= 0 etc.)."""
    an2 = later.get('anchors') or {}
    ni, s, rev, p2 = (_num(x) for x in (an2.get('net_income'), an2.get('shares'), an2.get('revenue'), later.get('price')))
    y = years[h - 1]
    vals = [ni, s, rev, p2, price0, R] + [_num(y.get(k)) for k in ('price', 'revenue', 'margin', 'shares', 'multiple')]
    if any(v is None or v <= 0 for v in vals):
        return None
    f = R * price0 / p2
    t = {'e_total': math.log(y['price'] / (price0 * R)), 'e_rev': math.log(y['revenue'] / rev),
         'e_margin': math.log(y['margin'] / (ni / rev)), 'e_shares': math.log((s / f) / y['shares']),
         'e_multiple': math.log(y['multiple'] / (p2 * s / ni))}
    t['residual'] = t['e_total'] - (t['e_rev'] + t['e_margin'] + t['e_shares'] + t['e_multiple'])
    return t


def _attr_summary(items):
    out = {'n': len(items)}
    if not items:
        return out
    for k in ATTR_TERMS:
        xs = sorted(x[k] for x in items)
        out[k] = {'median': statistics.median(xs), 'mean': sum(xs) / len(xs), 'p10': quantile(xs, .1), 'p90': quantile(xs, .9)}
    out['residual_max_abs'] = max(abs(x['residual']) for x in items)
    return out


def _attribution(common, later, groups):
    """Attributed subsample of one horizon: the common rows with a usable (status ok) v2 row at t + h. Terms for the lane and,
    on the same rows, for lane_v1 (its n reported separately: a term without a logarithm drops only that lane's row), overall
    and by the 6.5 groups."""
    items, no_later, unusable = [], 0, 0
    for rec in common:
        nxt = later.get((rec['symbol'], int(rec['formation'][:4]) + rec['h']))
        if not nxt:
            no_later += 1
            continue
        a = attribution_terms(rec['row']['scenarios']['base']['years'], rec['h'], rec['price'], rec['R'], nxt)
        if a is None:
            unusable += 1
            continue
        items.append((rec, a, attribution_terms(rec['v1_row']['scenarios']['base']['years'], rec['h'], rec['price'], rec['R'], nxt)))

    def both(xs):
        return {'lane_base': _attr_summary([a for _, a, _ in xs]), 'lane_v1': _attr_summary([b for _, _, b in xs if b is not None])}
    out = dict({'n_common': len(common), 'n_no_usable_row_at_t_plus_h': no_later, 'n_terms_undefined': unusable, 'n': len(items)},
               **both(items), by={})
    for name, key in groups.items():
        by = {}
        for x in items:
            by.setdefault(str(key(x[0])), []).append(x)
        out['by'][name] = {g: both(v) for g, v in sorted(by.items())}
    return out


def _dist(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {'n': 0}
    return {'n': len(xs), 'p10': quantile(xs, .1), 'p25': quantile(xs, .25), 'median': quantile(xs, .5), 'p75': quantile(xs, .75),
            'p90': quantile(xs, .9)}


def _diagnostics_v2(rows, tables):
    """v2 6.7: ordering assertion count (must be 0), flag shares, the L_0 distribution, M_just / M_cs, cell usage, table medians
    per formation year; plus spec 1's lambda by size class and the margin-target bounds (own history, stage spread, 0.5 / 1.5x)."""
    ok = [r for r in rows if r.get('status') == 'ok']
    errors = {}
    for r in rows:
        if r.get('status') == 'error':
            for x in r.get('reasons') or ['unknown']:
                errors[x] = errors.get(x, 0) + 1
    diag = {'n_rows': len(rows), 'n_rows_ok': len(ok),
            'status_counts': {k: sum(1 for r in rows if r.get('status') == k) for k in ('ok', 'excluded', 'error')},
            'ordering_assertions': sum(v for k, v in errors.items() if k.startswith('OrderingError')), 'errors': errors}
    if not ok:
        return diag
    flags = {}
    for r in ok:
        for f in r['flags']:
            flags[f] = flags.get(f, 0) + 1
    diag['flag_shares'] = {k: v / len(ok) for k, v in sorted(flags.items())}
    diag['premium_log'] = dict(_dist(r['premium_log'] for r in ok), share_clipped=sum(1 for r in ok if 'premium_clipped' in r['flags']) / len(ok),
                               raw=_dist(r['premium_log_raw'] for r in ok))
    diag['gordon_over_cross_section'] = {'today': _dist(r['gordon']['today']['ratio_to_cross_section'] for r in ok),
                                         'year_5_base': _dist(r['gordon']['years'][-1]['ratio_to_cross_section'] for r in ok),
                                         'note': 'spec 1 justified multiple / the base cell median: how far the market level sits above the no-value-creation level'}
    use = {'formation': {}, 'paths': {}}
    for r in ok:
        lv = r['cross_section_today']['level']
        use['formation'][lv] = use['formation'].get(lv, 0) + 1
        for z in r['scenarios'].values():
            for y in z['years']:
                lv = y['cross_section']['level']
                use['paths'][lv] = use['paths'].get(lv, 0) + 1
    diag['cell_usage'] = {k: {lv: v.get(lv, 0) / max(1, sum(v.values())) for lv in LEVELS} for k, v in use.items()}
    diag['margin_target_basis'] = {s: {b: sum(1 for r in ok if r['margin_history'][f'{s}_basis'] == b) / len(ok)
                                       for b in sorted({r['margin_history'][f'{s}_basis'] for r in ok})} for s in ('bear', 'bull')}
    diag['multiple_today'] = _dist(r['multiple_today'] for r in ok)
    by_class = {}
    for r in ok:
        by_class.setdefault(r['size_class'], []).append(r)
    diag['by_size_class'] = {k: {'n': len(v), 'median_lambda_base': statistics.median(x['scenarios']['base']['lambda'] for x in v),
                                 'median_share_5y_base': statistics.median(x['scenarios']['base']['base_rate_share_5y'] for x in v),
                                 'share_lambda_shrunk_base': sum(1 for x in v if x['scenarios']['base']['lambda'] < 1) / len(v)}
                             for k, v in sorted(by_class.items())}
    tb = {}
    for fm, t in sorted((tables or {}).items()):
        tb[fm] = {'as_of': t['as_of'], 'n': t['n'], 'n_outside_pe_range': t.get('n_outside_pe_range'),
                  'median': quantile(t['all'], .5), 'p25': quantile(t['all'], .25), 'p75': quantile(t['all'], .75),
                  'by_growth': {g: {'n': len(xs), 'median': quantile(xs, .5)} for g, xs in t['growth'].items()},
                  'cells_with_cell_min': sum(1 for xs in t['cells'].values() if len(xs) >= CELL_MIN), 'cells': len(t['cells'])}
    diag['tables'] = tb
    return diag


def _verdict_v2(hz, attr):
    """docs/prereg_price_path_v2.md section 7 on the raw common sample (S4 on the attributed subsample)."""
    def m(h, name, key='mdale'):
        return (((hz.get(h) or {}).get('common') or {}).get('metrics') or {}).get(name, {}).get(key)
    checks = {}
    s1 = []
    for h in (3, 5):
        lane, ac, ei, v1 = m(h, 'lane_base'), m(h, 'analyst_const'), m(h, 'engine_irr'), m(h, 'lane_v1')
        ok = None if None in (lane, ac, ei, v1) else (lane < ac and lane < ei and lane < v1)
        s1.append(ok)
        checks[f'S1_h{h}_below_analyst_engine_and_v1'] = {'lane': lane, 'analyst_const': ac, 'engine_irr': ei, 'lane_v1': v1, 'pass': ok}
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
        ok = None if not band.get('n') else (band['within_band'] >= .5 and band['below_bear'] <= S3_TAIL_V2 and band['above_bull'] <= S3_TAIL_V2)
        s3.append(ok)
        checks[f'S3_h{h}_band'] = dict(band, **{'pass': ok})
    a3 = ((attr.get(3) or {}).get('lane_base')) or {}
    meds = {k: (a3.get(k) or {}).get('median') for k in ('e_rev', 'e_margin', 'e_multiple')}
    s4 = None if not a3.get('n') or None in meds.values() else all(abs(v) <= S4_LIMIT for v in meds.values())
    checks['S4_h3_attribution_terms_within_0.10'] = dict(meds, n=a3.get('n', 0), **{'pass': s4})
    S1, S2, S3, S4 = all(x is True for x in s1), all(x is True for x in s2), all(x is True for x in s3), s4 is True
    verdict = 'PASS' if S1 and S2 and S3 and S4 else ('PARTIAL' if S2 and S4 else 'FAIL')
    meaning = {'PASS': 'the lane appears next to the DCF as the expected price path with the band sentence of section 9',
               'PARTIAL': 'S2 and S4 hold, S1 or S3 fails: a draft row labelled with what failed',
               'FAIL': 'S2 or S4 fails: recorded in docs; not shown'}[verdict]
    failed = [k for k, v in checks.items() if v.get('pass') is not True]
    return {'verdict': verdict, 'S1_accuracy': S1, 'S2_bias': S2, 'S3_band': S3, 'S4_attribution': S4, 'checks': checks,
            'failed': failed, 'meaning': meaning}


def evaluate_v2(rows, prices, engine_rows=None, v1_rows=None, tables=None, spy='SPY'):
    """docs/prereg_price_path_v2.md sections 6.3-6.8 and the verdict of section 7. rows: v2 lane rows (one per symbol x
    formation); prices: {symbol: backtest.load_prices output} including SPY; engine_rows: scripts/backtest_engine.py rows
    (base_irr); v1_rows: spec-1 rows of the same universe (the lane_v1 baseline); tables: {formation: cross_section_table}."""
    irr = {(r['symbol'], r['formation']): r['base_irr'] for r in (engine_rows or []) if r.get('base_irr') is not None}
    v1 = {(r['symbol'], r['formation']): r for r in (v1_rows or []) if r.get('status') == 'ok'}
    later = {(r['symbol'], int(r['formation'][:4])): r for r in rows if r.get('status') == 'ok'}
    names = ['lane_base'] + list(BASELINES_V2)
    groups = {'formation_year': lambda r: r['formation'][:4], 'stage': lambda r: r['stage'], 'cyclical': lambda r: r['cyclical'],
              'multiple_bucket': lambda r: _bucket_of(r['multiple_today'], MULTIPLE_BUCKETS_V2), 'size_class': lambda r: r['size_class'],
              'cross_section_cell': lambda r: r['cell']}
    hz, attr = {}, {}
    for h in HORIZONS:
        y0, y1 = FORMATION_YEARS[h]
        inrange = [r for r in rows if y0 <= int(r['formation'][:4]) <= y1]
        excl = {'no lane path': 0, 'no realized price': 0, 'no engine base_irr': 0, 'no trailing 3-year EPS CAGR': 0, 'no lane_v1 path': 0}
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
                   'multiple_today': r['multiple_today'], 'cell': _cell_label(r), 'defect': r['multiple_today'] < DEFECT_PE,
                   'preds': predictions_v2(r, h, irr.get(key), v1.get(key)), 'row': r, 'v1_row': v1.get(key)}
            lane_only.append(rec)
            miss = False
            if key not in irr:
                excl['no engine base_irr'] += 1
                miss = True
            if r.get('eps_cagr_3y') is None:
                excl['no trailing 3-year EPS CAGR'] += 1
                miss = True
            if key not in v1:
                excl['no lane_v1 path'] += 1
                miss = True
            if not miss:
                common.append(rec)
        rec_h = {'formations': [y0, y1], 'n_in_range': len(inrange), 'excluded': excl, 'exclusion_reasons': reasons,
                 'common': {'n': len(common)}, 'lane_only': {'n': len(lane_only)}}
        if common:
            c = rec_h['common']
            c['metrics'] = {n: _metrics(common, n) for n in names + ['lane_bear', 'lane_bull', 'engine_irr_total']}
            c['head_to_head'] = _head_to_head(common, 'lane_base', baselines=BASELINES_V2)
            c['band'] = _band(common)
            c['rank'] = {n: _rank_stats(common, n) for n in names}
            c['by'] = {k: _breakdown(common, f, names) for k, f in groups.items()}
            c['deflation_factors'] = _deflate(common, prices, spy)
            ok = [r for r in common if r.get('actual_deflated')]
            c['deflated'] = {'n': len(ok), 'metrics': {n: _metrics(ok, n, 'actual_deflated') for n in names + ['lane_bear', 'lane_bull']},
                             'band': _band(ok, 'actual_deflated'), 'head_to_head': _head_to_head(ok, 'lane_base', 'actual_deflated', BASELINES_V2)}
            clean = [r for r in common if not r['defect']]
            c['defect_rows_removed'] = {'n': len(clean), 'removed': sorted(f"{r['symbol']} {r['formation'][:4]}" for r in common if r['defect']),
                                        'rule': f'raw trailing P/E today < {DEFECT_PE}x (section 2 / 6.8)',
                                        'metrics': {n: _metrics(clean, n) for n in names + ['lane_bear', 'lane_bull']},
                                        'band': _band(clean), 'head_to_head': _head_to_head(clean, 'lane_base', baselines=BASELINES_V2)}
            attr[h] = _attribution(common, later, groups)
        if lane_only:
            lo = rec_h['lane_only']
            lo['metrics'] = {n: _metrics(lane_only, n) for n in ('lane_base', 'lane_bear', 'lane_bull', 'random_walk', 'drift_7')}
            lo['head_to_head'] = {k: v for k, v in _head_to_head(lane_only, 'lane_base').items() if k in ('random_walk', 'drift_7')}
            lo['band'] = _band(lane_only)
        hz[h] = rec_h
    verdict = _verdict_v2(hz, attr)
    return {'spec_version': SPEC_VERSION_2, 'horizons': hz, 'attribution': attr, 'diagnostics': _diagnostics_v2(rows, tables),
            'verdict': verdict,
            'caveats': ["survivorship: the universe is today's largest US companies, so every realized price is a survivor's, and the "
                        'cross-section table is survivor-biased by construction; the deflated check (6.8) rescales each window to SPY '
                        'and is reported next to the raw results, not instead of them',
                        "in-sample design: v2's changes were chosen from v1's 2016-2025 attribution (section 1 / 8 of the "
                        'preregistration); the constants were stated with reasons and fixed before this run, not searched, but the '
                        'period is not out of sample',
                        'no guidance or consensus in the backtest: the run-rate + base-rate variant only; guided paths are untested',
                        'one formation a year: yearly rank statistics with t below about 2 are noise',
                        'the attribution conditions on a usable row (NI > 0) at t + h; it locates the error, it does not validate a fix',
                        'the engine_irr baseline comes from the engine walk-forward rows supplied (scripts/backtest_engine.py); lane_v1 '
                        'from the spec-1 rows supplied (deterministic re-run of the unchanged spec-1 code)']}
