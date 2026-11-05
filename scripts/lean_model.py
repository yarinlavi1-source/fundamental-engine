"""Lean two-method valuation for the 5-minute ticker check (owner preference 04.11.2026).

Method 1: DCF of free cash flow after SBC. The cost of equity is rf + beta x ERP and fades to a mature beta over
10 years. Growth fades to terminal growth = rf over 2033-2041, with reinvestment = g / terminal ROIC. Cash is added.
Method 2: exit multiple. The end-of-horizon value is P/E x next-year operating earnings (ex cash), plus the PV of the
FCF to the horizon, plus cash today.
Value = average of the two. Year-end values are conditional values on each path, not price forecasts.

Usage: python scripts/lean_model.py params.json [--out result.json]
The params file has keys: ticker, as_of, price, rf, erp, beta, beta_mature, shares_m, net_cash_m (end of the base year;
negative = net debt), rev0_m (base-year revenue), base_year, tax, cash_yield, debt_rate, roic_terminal,
days_to_base_year_end, years_to_horizon, buy_return, probabilities {bear, base, bull}, and scenarios
{bear|base|bull: {g[6], m[6] (operating margin after SBC), conv[6] (FCF / NOPAT), pe (exit P/E on next-year
operating EPS), charge {year index: $M after tax}, optional shares_m}}.
Converts, puts, warrants, leases and patent cliffs are NOT modelled here. Put them into the inputs (net cash, shares,
growth/margin paths, charges) from the filings fine-print lane, or use a custom model.

Two lenses (owner preference 05.11.2026, CLAUDE.md): the result also carries a calibrated lens of the base case. It keeps
the base's revenue, conversion and charges but holds today's forward P/E (price / first forecast year's operating EPS) as
the exit multiple and does not let the margin fall below the first forecast year. Reason: in the 2016-2025 price-path
walk-forward (docs/PRICE_PATH_V2_RESULTS_2026-10-25.md) fading the multiple and cutting margins were the two largest
sources of a low bias. Optional params key "calibrated": {"pe": exit P/E to use when today's multiple is not credible
(e.g. a GAAP P/E inflated by temporarily depressed earnings), "m": [6 margins] when a margin cut is hard and evidenced
(e.g. a quantified depreciation wave), "why": text}. The two lenses are not averaged: both historical analogues erred low
in the same direction (3-year median bias x0.73 and x0.89), so an average cannot remove that shared bias. A buy, sell or
trim call is made only where both lenses fall in the same band ("bands": buy/hold/lagging thresholds in % a year).
"""
import argparse
import json


def _ke_path(p, n):
    k0 = p['rf'] + p['beta'] * p['erp']
    km = p['rf'] + p['beta_mature'] * p['erp']
    return [k0 + (km - k0) * min(i, 10) / 10 for i in range(n)]


def run_scenario(p, d):
    """One scenario. Revenue index 0 = base year; g/m/conv index i = base year + 1 + i (6 years)."""
    tax, sh = p['tax'], d.get('shares_m', p['shares_m'])  # per-scenario shares (e.g. warrants vesting with the deals)
    rev = [p['rev0_m']]
    opi, eps_op, fcf = [], [], []
    charge = {int(k): v for k, v in (d.get('charge') or {}).items()}
    for i, g in enumerate(d['g']):
        r = rev[-1] * (1 + g)
        rev.append(r)
        oi = r * d['m'][i]
        opi.append(oi)
        nopat = oi * (1 - tax)
        eps_op.append(nopat / sh)
        fcf.append(nopat * d['conv'][i] - charge.get(i, 0.0))
    tg, roic = p['rf'], p['roic_terminal']
    g_last, n_last = d['g'][-1], opi[-1] * (1 - tax)
    for j in range(1, 10):
        g = g_last + (tg - g_last) * j / 9
        n_last *= 1 + g
        fcf.append(n_last * (1 - g / roic))
    ke = _ke_path(p, len(fcf) + 1)
    tv = n_last * (1 + tg) * (1 - tg / roic) / (ke[len(fcf)] - tg)
    cash = [p['net_cash_m']]
    for i in range(5):
        c = cash[-1]
        rate = p['cash_yield'] if c >= 0 else p.get('debt_rate', p['cash_yield'])
        cash.append(c + fcf[i] + c * rate * (1 - tax))

    def dcf_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, len(fcf)):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return (v + tv / disc + cash[t]) / sh

    exit_op = d['pe'] * eps_op[5] * sh

    def mult_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, 5):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return (v + exit_op / disc + cash[t]) / sh

    dcf = [dcf_at(t) for t in range(6)]
    mul = [mult_at(t) for t in range(6)]
    val = [max((a + b) / 2, 0.01) for a, b in zip(dcf, mul)]   # equity cannot go below zero: a negative path means the scenario needs a restructuring the model does not show
    today = val[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365)
    ret = (val[5] / p['price']) ** (1 / p['years_to_horizon']) - 1
    y0 = p['base_year']
    return {
        'revenue_m': {y0 + i: round(x) for i, x in enumerate(rev)},
        'op_margin': {y0 + 1 + i: m for i, m in enumerate(d['m'])},
        'eps_operating': {y0 + 1 + i: round(x, 2) for i, x in enumerate(eps_op)},
        'eps_with_interest': {y0 + 1 + i: round((opi[i] + cash[i] * (p['cash_yield'] if cash[i] >= 0 else p.get('debt_rate', p['cash_yield']))) * (1 - tax) / sh, 2) for i in range(6)},
        'dcf_today': round(dcf[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365), 2),
        'multiple_today': round(mul[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365), 2),
        'value_today': round(today, 2),
        'year_end_values': {y0 + t: round(val[t], 2) for t in range(1, 6)},
        'annual_return_pct': round(ret * 100, 1),
        'ke0_pct': round(ke[0] * 100, 2),
    }


BANDS = {'buy': 12.0, 'hold': 6.0, 'lagging': 3.0}   # owner's bands on the base-case yearly return (CLAUDE.md, 05.11.2026)


def band(ret_pct, bands=None):
    b = bands or BANDS
    if ret_pct >= b['buy']:
        return 'buy'
    if ret_pct >= b['hold']:
        return 'hold'
    if ret_pct >= b['lagging']:
        return 'lagging'
    return 'sell'


def calibrated_lens(p, d):
    """The base case with today's forward P/E held and the margin floored at the first forecast year (see the docstring)."""
    cal = p.get('calibrated') or {}
    first = run_scenario(p, d)
    eps0 = first['eps_operating'][min(first['eps_operating'])]
    pe_today = p['price'] / eps0 if eps0 > 0 else None
    pe = cal.get('pe', pe_today)
    m = cal.get('m') or [max(d['m'][0], x) for x in d['m']]
    out = {'pe_today': round(pe_today, 1) if pe_today else None, 'pe_used': round(pe, 1) if pe else None,
           'pe_overridden': 'pe' in cal, 'margin_used': [round(x, 4) for x in m], 'margin_overridden': bool(cal.get('m')),
           'why': cal.get('why', '')}
    if pe is None:
        out['note'] = 'no positive first-year EPS: the calibrated lens needs "calibrated.pe"'
        return out
    r = run_scenario(p, dict(d, pe=pe, m=m))
    out.update(value_today=r['value_today'], year_end_values=r['year_end_values'], annual_return_pct=r['annual_return_pct'])
    if pe_today and pe_today > 40 and 'pe' not in cal:
        out['note'] = 'today\'s multiple is above 40: holding it for five years is not credible; set "calibrated.pe"'
    return out


def run(p):
    out = {k: run_scenario(p, d) for k, d in p['scenarios'].items()}
    pr = p['probabilities']
    last = str(p['base_year'] + 5)
    w_today = sum(pr[k] * out[k]['value_today'] for k in out)
    w_end = sum(pr[k] * out[k]['year_end_values'][int(last)] for k in out)
    base = p['scenarios']['base']
    res = {
        'scenarios': out,
        'weighted_value_today': round(w_today, 2),
        'weighted_end_value': round(w_end, 2),
        'weighted_return_pct': round(((w_end / p['price']) ** (1 / p['years_to_horizon']) - 1) * 100, 1),
        'index_return_pct': round((p['rf'] + p['erp']) * 100, 1),
        'buy_price': round(out['base']['year_end_values'][int(last)] / (1 + p['buy_return']) ** p['years_to_horizon'], 2),
        'index_parity_price': round(out['base']['year_end_values'][int(last)] / (1 + p['rf'] + p['erp']) ** p['years_to_horizon'], 2),
    }
    # reverse DCF: hold the first (guided) year, scale later growth until value today = price
    lo, hi = -1.0, 4.0
    for _ in range(80):
        f = (lo + hi) / 2
        d = dict(base, g=[base['g'][0]] + [g * f for g in base['g'][1:]])
        if run_scenario(p, d)['value_today'] < p['price']:
            lo = f
        else:
            hi = f
    d = dict(base, g=[base['g'][0]] + [g * f for g in base['g'][1:]])
    res['market_implied_growth_pct'] = [round(g * 100, 1) for g in d['g']]
    res['market_implied_end_revenue_m'] = run_scenario(p, d)['revenue_m'][p['base_year'] + 5]
    sens = {}
    for b in (p['beta'] - 0.3, p['beta'] + 0.3):
        sens[f'beta {b:.2f}'] = run_scenario(dict(p, beta=b), base)['value_today']
    for pe in (base['pe'] - 5, base['pe'] + 5):
        r = run_scenario(p, dict(base, pe=pe))
        sens[f'exit P/E {pe:g}'] = [r['value_today'], r['annual_return_pct']]
    res['sensitivity_base'] = sens
    cal = calibrated_lens(p, base)
    bands = p.get('bands')
    ours_ret = out['base']['annual_return_pct']
    lens = {'ours': {'value_today': out['base']['value_today'], 'annual_return_pct': ours_ret, 'band': band(ours_ret, bands),
                     'pe_exit': base['pe']}, 'calibrated': cal}
    if 'annual_return_pct' in cal:
        cal['band'] = band(cal['annual_return_pct'], bands)
        lens['agree'] = cal['band'] == lens['ours']['band']
        lens['rule'] = ('both lenses in the same band: act on it' if lens['agree'] else
                        'lenses disagree: hold, or stage the trade around the next report')
    res['two_lens'] = lens
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('params')
    ap.add_argument('--out')
    a = ap.parse_args()
    p = json.load(open(a.params))
    res = run(p)
    txt = json.dumps(res, indent=1)
    if a.out:
        open(a.out, 'w').write(txt)
    print(txt)


if __name__ == '__main__':
    main()
