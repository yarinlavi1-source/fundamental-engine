"""Expectation-gap engine: what the price already assumes versus what the evidence supports.

Weakness #3 in docs/GAP_ANALYSIS_2026-10-19.md: the live brain solved implied growth from
TTM revenue with a net-margin path and valued capital-heavy firms on earnings multiples,
so "implied growth" was mis-stated and the gap between price and business never measured.

Here one driver model is used for BOTH directions:
  revenue (forward start) -> EBIT after SBC -> tax -> reinvestment linked to growth
  -> FCFF -> enterprise value -> capital.per_share (converts/warrants/claims) -> per share.

Forward value: evidence-anchored scenarios.
Reverse value: hold every driver at its evidence anchor and solve ONE lever at a time
(growth level, growth duration, mature margin, capital efficiency) so that per-share
value equals the price. The output is a table of "what must be true", each compared with
(a) the company's own demonstrated metric and (b) an empirical base rate when supplied.

Nothing here predicts returns. Whether the gap predicts returns is an empirical question
tested in scripts/ with point-in-time data (see docs/prereg_expectation_gap.md).
"""
from copy import deepcopy
from .finance import number
from .capital import per_share, market_view


def growth_path(g1, g_term, high_years, years):
    """Growth g1 held for `high_years`, then linear fade to g_term by the final year."""
    out = []
    for t in range(1, years + 1):
        if t <= high_years:
            out.append(g1)
        else:
            span = max(years - high_years, 1)
            w = (t - high_years) / span
            out.append(g1 + (g_term - g1) * w)
    return out


def margin_path(m1, m_mature, years_to_mature, years):
    return [m1 + (m_mature - m1) * min(t / max(years_to_mature, 1), 1.0) for t in range(1, years + 1)]


def run_model(d):
    """d: driver dict (see DEFAULT_KEYS). Returns rows, EV, terminal share and implied ROIC."""
    rev = number(d['revenue0'], 'revenue0', 0)
    years = int(d.get('years', 10))
    gp = growth_path(d['g1'], d['g_terminal'], d['high_years'], years)
    mp = margin_path(d['m1'], d['m_mature'], d['years_to_mature'], years)
    tax, wacc, s2c = d['tax'], d['wacc'], d['sales_to_capital']
    g_t = d['g_terminal']
    if wacc <= g_t:
        raise ValueError('wacc must exceed terminal growth')
    ic = number(d.get('invested_capital0', rev / s2c), 'invested_capital0', 0)
    rows, pv = [], 0.0
    prev = rev
    for t in range(1, years + 1):
        r = prev * (1 + gp[t - 1]) if t > 1 else prev            # year 1 revenue = forward revenue0
        ebit = r * mp[t - 1]
        nopat = ebit - max(ebit, 0) * tax
        base = prev if t > 1 else d.get('revenue_ttm', r)           # year 1 = forward revenue; its build-out is reinvested in year 1
        reinv = max(r - base, 0) / s2c                              # net reinvestment; maintenance sits inside EBIT (after D&A)
        fcff = nopat - reinv
        ic += reinv
        pv += fcff / (1 + wacc) ** t
        rows.append({'year': t, 'revenue': r, 'growth': gp[t - 1] if t > 1 else None, 'ebit_margin': mp[t - 1], 'nopat': nopat,
                     'reinvestment': reinv, 'fcff': fcff, 'invested_capital': ic, 'roic': nopat / ic if ic > 0 else None})
        prev = r
    roic_term = d.get('terminal_roic') or max(rows[-1]['roic'] or wacc, wacc)
    nopat_next = rows[-1]['nopat'] * (1 + g_t)
    tv = nopat_next * (1 - g_t / roic_term) / (wacc - g_t) if roic_term > g_t else nopat_next / wacc
    tv_pv = tv / (1 + wacc) ** years
    ev = pv + tv_pv
    rv5 = rows[4]['revenue'] if years >= 5 else None
    return {'rows': rows, 'enterprise_value': ev, 'terminal_share': tv_pv / ev if ev > 0 else None,
            'revenue_y5': rv5, 'revenue_y10': rows[-1]['revenue'],
            'cagr_5y': (rv5 / rows[0]['revenue']) ** (1 / 4) - 1 if rv5 else None,
            'roic_final': rows[-1]['roic'], 'terminal_roic': roic_term}


def value_per_share(d, capital_structure):
    m = run_model(d)
    ps = per_share(capital_structure, m['enterprise_value'])
    return ps['value_per_share'], m, ps


LEVERS = {
    'growth_level': ('g1', -0.5, 3.0, 'Annual revenue growth during the high-growth years'),
    'growth_duration': ('high_years', 0, 10, 'Years the high growth lasts before fading'),
    'mature_margin': ('m_mature', -0.2, 0.7, 'Mature EBIT margin after SBC'),
    'capital_efficiency': ('sales_to_capital', 0.05, 10.0, 'Revenue added per $1 of reinvestment'),
}


def solve_lever(d, capital_structure, price, lever, iters=80):
    key, lo, hi, label = LEVERS[lever]
    def f(x):
        dd = dict(d); dd[key] = round(x) if key == 'high_years' else x
        return value_per_share(dd, capital_structure)[0] - price
    flo, fhi = f(lo), f(hi)
    if flo == fhi or flo * fhi > 0:
        evs = []
        for x in (lo, hi):
            dd = dict(d); dd[key] = round(x) if key == 'high_years' else x
            evs.append(run_model(dd)['enterprise_value'])
        why = ('more of this lever DESTROYS value: returns on new capital are below the cost of capital at the anchor'
               if evs[1] < evs[0] else 'even the extreme of this lever does not reach the price')
        return {'lever': lever, 'label': label, 'status': 'not_reachable', 'range': [lo, hi],
                'value_at_low': flo + price, 'value_at_high': fhi + price, 'ev_at_low': evs[0], 'ev_at_high': evs[1], 'why': why}
    for _ in range(iters):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (fhi > 0):
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
        if key == 'high_years' and hi - lo < 1:
            break
    x = hi if key == 'high_years' else (lo + hi) / 2
    dd = dict(d); dd[key] = round(x) if key == 'high_years' else x
    m = run_model(dd)
    return {'lever': lever, 'label': label, 'status': 'solved', 'required': dd[key], 'anchor': d[key],
            'implied_revenue_y5': m['revenue_y5'], 'implied_revenue_y10': m['revenue_y10'], 'implied_cagr_5y': m['cagr_5y'],
            'implied_roic_final': m['roic_final']}


def classify_gap(prob_beat):
    if prob_beat is None:
        return 'unassessed'
    if prob_beat >= 0.6:
        return 'price_assumes_less_than_demonstrated'      # candidate opportunity
    if prob_beat >= 0.35:
        return 'price_roughly_consistent_with_evidence'
    if prob_beat >= 0.15:
        return 'price_assumes_more_than_evidence'
    return 'price_requires_rare_outcome'


def expectation_gap(case):
    """case: {price, capital_structure, anchor: driver dict, evidence: {...}, base_rate_fn: optional callable}.

    evidence keys (all optional, each with source ids): demonstrated_cagr_3y, recent_yoy_median,
    best_margin_3y, incremental_s2c, consensus_cagr, backlog_coverage.
    """
    d = deepcopy(case['anchor'])
    cs = case['capital_structure']
    price = number(case['price'], 'price', 1e-9)
    v_anchor, m_anchor, ps = value_per_share(d, cs)
    mv = market_view(cs, price)
    levers = [solve_lever(d, cs, price, k) for k in LEVERS]
    g = next(l for l in levers if l['lever'] == 'growth_level')
    ev = case.get('evidence', {})
    comps = []

    def comp(name, required, demonstrated, better_if='higher', source_ids=None, note=''):
        if required is None or demonstrated is None:
            status = 'unassessed'
        else:
            ok = demonstrated >= required if better_if == 'higher' else demonstrated <= required
            status = 'evidence_meets_requirement' if ok else 'evidence_below_requirement'
        comps.append({'component': name, 'price_requires': required, 'demonstrated': demonstrated, 'status': status,
                      'source_ids': source_ids or [], 'note': note})

    comp('5y revenue CAGR', g.get('implied_cagr_5y'), ev.get('demonstrated_cagr_3y', {}).get('value'),
         source_ids=ev.get('demonstrated_cagr_3y', {}).get('source_ids'), note='trailing 3y CAGR as the demonstrated rate')
    mm = next(l for l in levers if l['lever'] == 'mature_margin')
    comp('mature EBIT margin (after SBC)', mm.get('required'), ev.get('best_margin_3y', {}).get('value'),
         source_ids=ev.get('best_margin_3y', {}).get('source_ids'))
    ce = next(l for l in levers if l['lever'] == 'capital_efficiency')
    comp('revenue per $ reinvested', ce.get('required'), ev.get('incremental_s2c', {}).get('value'),
         source_ids=ev.get('incremental_s2c', {}).get('source_ids'))

    prob = None
    br = case.get('base_rate_fn')
    if br and g.get('implied_cagr_5y') is not None:
        prob = br(g['implied_cagr_5y'])
    return {'price': price, 'value_at_anchor': v_anchor, 'anchor_vs_price': v_anchor / price - 1 if price else None,
            'market': mv, 'anchor_model': {k: m_anchor[k] for k in ('enterprise_value', 'terminal_share', 'revenue_y5', 'revenue_y10', 'cagr_5y', 'roic_final')},
            'what_must_be_true': levers, 'components': comps, 'prob_beat_implied_growth': prob, 'gap_class': classify_gap(prob),
            'converted_at_anchor': ps.get('converted'),
            'meaning': 'Each lever is solved alone with the others held at the evidence anchor. Probabilities come only from a supplied empirical base-rate function.'}
