"""Growth quality, acceleration and stage — computed only from data usable at date t.

Weakness #5 (docs/GAP_ANALYSIS_2026-10-19.md): growth was first-vs-last YoY with one margin
delta, acquisitions were counted as organic, and one strong quarter could be extrapolated.

Every output is a component with its value, the periods used and a direction, so a ranking can
show what raised and what lowered a company (weakness #6). No component is a forecast.
"""
from datetime import date
from .pit import available, ttm_at, latest_at


def _sorted(series, t):
    return sorted(available(series, t))


def _yoy_list(q):
    """[(end, yoy)] where a quarter 4 back exists ~1 year earlier."""
    out = []
    for i in range(4, len(q)):
        e, v = q[i]; e0, v0 = q[i - 4]
        days = (date.fromisoformat(e) - date.fromisoformat(e0)).days
        if 340 <= days <= 390 and v0 and v0 > 0:
            out.append((e, v / v0 - 1))
    return out


def features(rec, t):
    rev = _sorted(rec['rev'], t)
    if len(rev) < 8:
        return None
    rt, end = ttm_at(rec['rev'], t)
    rt1, _ = ttm_at(rec['rev'], t, offset=4)
    if not rt or not rt1 or rt1 <= 0 or (t - date.fromisoformat(end)).days > 150:
        return None
    f = {'as_of': t.isoformat(), 'period_end': end, 'rev_ttm': rt, 'g_ttm': rt / rt1 - 1}
    yoy = _yoy_list(rev)
    if len(yoy) >= 3:
        f['yoy_q'] = yoy[-1][1]
        f['accel_2q'] = yoy[-1][1] - yoy[-3][1]
        streak = 0
        for i in range(len(yoy) - 1, 0, -1):
            if yoy[i][1] > yoy[i - 1][1]:
                streak += 1
            else:
                break
        f['accel_streak'] = streak
        f['yoy_periods'] = [e for e, _ in yoy[-3:]]
    if len(rev) >= 2 and rev[-2][1] > 0:
        f['seq_annualized'] = (rev[-1][1] / rev[-2][1]) ** 4 - 1
    for key, src in (('oi', 'oi'), ('gp', 'gp'), ('ocf', 'ocf'), ('capex', 'capex'), ('sbc', 'sbc')):
        a, _ = ttm_at(rec.get(src, {}), t)
        b, _ = ttm_at(rec.get(src, {}), t, offset=4)
        f[key + '_ttm'], f[key + '_ttm_1y'] = a, b
    if f['oi_ttm'] is not None:
        f['op_margin'] = f['oi_ttm'] / rt
        if f['oi_ttm_1y'] is not None:
            f['op_margin_1y'] = f['oi_ttm_1y'] / rt1
            f['op_margin_trend'] = f['op_margin'] - f['op_margin_1y']
            if rt - rt1 > 0.02 * rt1:
                f['incremental_op_margin'] = (f['oi_ttm'] - f['oi_ttm_1y']) / (rt - rt1)
    if f['gp_ttm'] is not None:
        f['gross_margin'] = f['gp_ttm'] / rt
        if f['gp_ttm_1y'] is not None and rt - rt1 > 0.02 * rt1:
            f['incremental_gross_margin'] = (f['gp_ttm'] - f['gp_ttm_1y']) / (rt - rt1)
    if f['ocf_ttm'] is not None:
        f['fcf_margin'] = (f['ocf_ttm'] - (f['capex_ttm'] or 0)) / rt
        if f['ocf_ttm_1y'] is not None:
            f['fcf_margin_1y'] = (f['ocf_ttm_1y'] - (f['capex_ttm_1y'] or 0)) / rt1
        f['capex_to_revenue'] = (f['capex_ttm'] or 0) / rt
    if f['sbc_ttm'] is not None:
        f['sbc_to_revenue'] = f['sbc_ttm'] / rt
    # acquisitions: goodwill jump relative to revenue
    gw = _sorted(rec.get('goodwill', {}), t)
    if len(gw) >= 2:
        latest = gw[-1]
        older = [x for x in gw if (date.fromisoformat(latest[0]) - date.fromisoformat(x[0])).days >= 330]
        if older:
            f['goodwill_jump_to_rev'] = (latest[1] - older[-1][1]) / rt
            f['acquisition_driven_risk'] = f['goodwill_jump_to_rev'] > 0.10
    sh = _sorted(rec.get('shares', {}), t)
    if len(sh) >= 2:
        s1 = sh[-1]; older = [x for x in sh if 330 <= (date.fromisoformat(s1[0]) - date.fromisoformat(x[0])).days <= 420]
        if older and older[-1][1] > 0:
            f['dilution_yoy'] = s1[1] / older[-1][1] - 1
    f['stage'] = stage(f)
    f['growth_character'] = character(f)
    return f


def stage(f):
    m, g = f.get('op_margin'), f['g_ttm']
    if m is None:
        return 'unknown'
    m1 = f.get('op_margin_1y')
    if m < -0.05 and g >= 0.25:
        return 'early_commercial'
    if m1 is not None and m1 <= 0 < m:
        return 'turning_profitable'
    if m > 0.15 and 0.10 <= g <= 0.40:
        return 'compounder'
    if g < 0:
        return 'declining'
    if m > 0 and g < 0.10:
        return 'mature'
    return 'other_growth'


def character(f):
    a, tr = f.get('accel_2q'), f.get('op_margin_trend')
    if a is None or tr is None:
        return 'insufficient'
    if f.get('acquisition_driven_risk'):
        return 'acquisition_driven'
    if a > 0.03 and tr > 0:
        return 'accelerating_and_more_profitable'
    if a > 0.03:
        return 'accelerating_but_costlier'
    if a < -0.03 and tr > 0:
        return 'slowing_but_more_profitable'
    if a < -0.03:
        return 'slowing_and_less_profitable'
    return 'steady'


def scorecard(f):
    """Transparent components: value, direction (+1 helps / -1 hurts / 0), why. No blended score."""
    if not f:
        return []
    comp = []
    def add(name, value, direction, why):
        comp.append({'component': name, 'value': value, 'direction': direction, 'why': why, 'period_end': f['period_end']})
    add('revenue growth TTM', f['g_ttm'], 1 if f['g_ttm'] > 0.15 else (-1 if f['g_ttm'] < 0 else 0), 'size of the demand signal')
    if 'accel_2q' in f:
        add('YoY acceleration (2q)', f['accel_2q'], 1 if f['accel_2q'] > 0.03 and f.get('accel_streak', 0) >= 2 else (-1 if f['accel_2q'] < -0.05 else 0),
            'needs >=2 consecutive improving quarters to count; one quarter is not an inflection')
    if 'incremental_op_margin' in f:
        add('incremental operating margin', f['incremental_op_margin'], 1 if f['incremental_op_margin'] > max(f.get('op_margin', 0), 0) + 0.05 else
            (-1 if f['incremental_op_margin'] < 0 else 0), 'does new revenue bring more profit than old revenue?')
    if 'fcf_margin' in f and 'fcf_margin_1y' in f:
        d = f['fcf_margin'] - f['fcf_margin_1y']
        add('FCF margin change', d, 1 if d > 0.03 else (-1 if d < -0.05 else 0), 'cash conversion trend (capex and working capital included)')
    if 'dilution_yoy' in f:
        add('share dilution YoY', f['dilution_yoy'], -1 if f['dilution_yoy'] > 0.05 else (1 if f['dilution_yoy'] < -0.01 else 0), 'per-share value leaks when shares grow')
    if f.get('acquisition_driven_risk'):
        add('acquired growth risk', f['goodwill_jump_to_rev'], -1, 'goodwill jump >10% of revenue: growth may be bought, not organic')
    if 'sbc_to_revenue' in f:
        add('SBC / revenue', f['sbc_to_revenue'], -1 if f['sbc_to_revenue'] > 0.15 else 0, 'compensation paid in shares is a real cost')
    return comp
