"""BNY bank model (custody bank): residual income on tangible common equity + exit P/E path. Usage:
python bank_model.py params.json --out result.json
Both methods value TOTAL common equity (clean surplus), then divide by shares at that date. Year t index 0 = 2026."""
import json, argparse, copy

def ke_path(p, n):
    k0 = p['rf'] + p['beta'] * p['erp']; km = p['rf'] + p['beta_mature'] * p['erp']
    return [k0 + (km - k0) * min(i, 10) / 10 for i in range(n)]

def run_scn(p, s, pe_override=None):
    pe_exit = pe_override or s['pe_exit']
    ke = ke_path(p, 40)
    # 2026 (index 0)
    sh_q3 = p['shares_q3_m']; ni0_ps = s['eps_2026']
    avg26 = 689.0  # diluted average 2026 (Q2 692.2, Q3 686.9 per releases; Q4 ~680)
    ni = [ni0_ps * avg26]
    q4_ni = ni[0] - (2.25 + 2.46 + 2.37) * 689.5   # adjusted 9M EPS 7.08 per AV/releases
    price0 = p['price']
    sh_end = [sh_q3 - p['q4_2026']['buyback_m'] / price0 + 0.0]
    tce = [p['tce_q3_m'] + q4_ni - p['q4_2026']['dividend_ps'] * sh_q3 - p['q4_2026']['buyback_m']]
    eps = [ni0_ps]; dps = [p["q4_2026"]["dividend_ps"] + 1.69]  # 2026: 9M paid 1.69 (10-Q) + 0.63 Q4
    dist = [None]; pe_today = price0 / eps[0]
    for i, g in enumerate(s['ni_g']):
        t = i + 1
        n = ni[-1] * (1 + g); ni.append(n)
        po = s['payout'][min(i, len(s['payout']) - 1)]
        div = p['dividend_payout'] * n; bb = max(po * n - div, 0.0)
        # buyback price: P/E moves linearly from today's trailing P/E to the exit P/E by 2031 (index 5)
        pe_t = pe_today + (pe_exit * (1 + s['ni_g'][min(t, 5)]) - pe_today) * min(t, 5) / 5
        eps_guess = n / (sh_end[-1] + p['dilution_m'])
        px = pe_t * eps_guess
        se = sh_end[-1] - bb / px + p['sbc_issuance_m']; sh_end.append(se)
        avg = (sh_end[-2] + se) / 2 + p['dilution_m']
        eps.append(n / avg); dps.append(div / avg)
        tce.append(tce[-1] + n - div - bb); dist.append(div + bb)
    N = len(ni) - 1  # 6 -> index 6 = 2032 forecast exists for forward EPS at end-2031
    H = 5  # horizon index = 2031
    # residual income beyond the explicit years: ROTCE fades to terminal over fade_years, TCE grows at g
    g = p['terminal_g']
    def ri_value_at(t):
        """total common equity value at end of year t (index), RI method"""
        v = tce[t]; disc = 1.0
        for s_ in range(t + 1, N + 1):
            disc *= 1 + ke[s_]
            v += (ni[s_] - ke[s_] * tce[s_ - 1]) / disc
        # fade from ROTCE of last explicit year
        r_last = ni[N] / tce[N - 1]; tc = tce[N]
        for j in range(1, p['fade_years'] + 1):
            r = r_last + (s['rotce_terminal'] - r_last) * j / p['fade_years']
            k = ke[min(N + j, 39)]
            disc *= 1 + k
            v += (r - k) * tc / disc
            tc *= 1 + g
        km = ke[39]
        tv = (s['rotce_terminal'] - km) * tc / (km - g)
        return v + tv / disc
    def mult_value_at(t):
        v = 0.0; disc = 1.0
        for s_ in range(t + 1, H + 1):
            disc *= 1 + ke[s_]
            v += dist[s_] / disc
        exit_eq = pe_exit * eps[H + 1] * sh_end[H]
        return v + exit_eq / disc
    ri = [ri_value_at(t) / sh_end[t] for t in range(H + 1)]
    mu = [mult_value_at(t) / sh_end[t] for t in range(H + 1)]
    val = [(a + b) / 2 for a, b in zip(ri, mu)]
    stub = (1 + ke[0]) ** (p['days_to_base_year_end'] / 365)
    # dividends per share received 2027..2031 compounded to end-2031 at ke
    fv_div = sum(dps[t] * (1 + ke[t]) ** (H - t) for t in range(1, H + 1)) + p['q4_2026']['dividend_ps'] * (1 + ke[0]) ** H
    yrs = p['years_to_horizon']
    ret_total = ((val[H] + fv_div) / price0) ** (1 / yrs) - 1
    ret_price = (val[H] / price0) ** (1 / yrs) - 1
    def r2(x): return round(x, 2)
    return {
        'eps': {2026 + t: r2(e) for t, e in enumerate(eps)},
        'ni_common_m': {2026 + t: round(x) for t, x in enumerate(ni)},
        'tce_m': {2026 + t: round(x) for t, x in enumerate(tce)},
        'tbvps': {2026 + t: r2(tce[t] / sh_end[t]) for t in range(N + 1)},
        'rotce': {2026 + t: round(ni[t] / ((tce[t - 1] + tce[t]) / 2), 3) for t in range(1, N + 1)},
        'shares_end_m': {2026 + t: round(x, 1) for t, x in enumerate(sh_end)},
        'dps': {2026 + t: r2(x) for t, x in enumerate(dps)},
        'ri_today': r2(ri[0] / stub), 'multiple_today': r2(mu[0] / stub), 'value_today': r2(val[0] / stub),
        'ri_year_end': {2026 + t: r2(ri[t]) for t in range(1, H + 1)},
        'multiple_year_end': {2026 + t: r2(mu[t]) for t in range(1, H + 1)},
        'year_end_values': {2026 + t: r2(val[t]) for t in range(1, H + 1)},
        'fv_dividends_2031': r2(fv_div),
        'annual_return_pct': round(ret_total * 100, 1), 'annual_return_price_only_pct': round(ret_price * 100, 1),
        'pe_exit': pe_exit, 'ke0_pct': round(ke[0] * 100, 2), 'p_tbv_implied_today': r2(val[0] / stub / (tce[0] / sh_end[0])),
    }

def band(x): return 'buy' if x >= 12 else 'hold' if x >= 6 else 'lagging' if x >= 3 else 'sell'

def run(p):
    out = {k: run_scn(p, s) for k, s in p['scenarios'].items()}
    base = p['scenarios']['base']
    pe_today = p['price'] / out['base']['eps'][2027]
    cal = run_scn(p, base, pe_override=pe_today)
    cal_s = {'pe_used': round(pe_today, 1), 'value_today': cal['value_today'], 'year_end_values': cal['year_end_values'],
             'annual_return_pct': cal['annual_return_pct'], 'band': band(cal['annual_return_pct']), 'why': p['calibrated']['why']}
    pr = p['probabilities']
    res = {'scenarios': out, 'calibrated': cal_s,
           'two_lens': {'ours': {'value_today': out['base']['value_today'], 'annual_return_pct': out['base']['annual_return_pct'], 'band': band(out['base']['annual_return_pct'])},
                        'calibrated': {'value_today': cal_s['value_today'], 'annual_return_pct': cal_s['annual_return_pct'], 'band': cal_s['band']}},
           'weighted_value_today': round(sum(pr[k] * out[k]['value_today'] for k in out), 2),
           'index_return_pct': p['index_return'] * 100}
    res['two_lens']['agree'] = res['two_lens']['ours']['band'] == cal_s['band']
    # entry price that passes all tests: base >=12%/yr (both lenses), bull >=17%, bear >=-10%
    yrs = p['years_to_horizon']
    def need(k, r, sc=None):
        sc = sc or out[k]; return (sc['year_end_values'][2031] + sc['fv_dividends_2031']) / (1 + r) ** yrs
    ent = {'base_12': need('base', .12), 'calibrated_12': (cal['year_end_values'][2031] + cal['fv_dividends_2031']) / 1.12 ** yrs,
           'bull_17': need('bull', .17), 'bear_-10': need('bear', -.10)}
    res['entry_prices'] = {k: round(v, 2) for k, v in ent.items()}
    res['entry_price_all_tests'] = round(min(ent.values()), 2)
    # sensitivities on the base
    sens = {}
    for b in (p['beta'] - 0.3, p['beta'] + 0.3):
        sens[f'beta {b:.2f}'] = run_scn(dict(p, beta=b), base)['value_today']
    for pe in (base['pe_exit'] - 2.3, base['pe_exit'] + 2.3):
        r = run_scn(p, base, pe_override=pe); sens[f'exit P/E {pe:.1f}'] = [r['value_today'], r['annual_return_pct']]
    r = run_scn(dict(p, terminal_g=0.03), base); sens['terminal g 3% (RI)'] = [r['ri_today'], r['value_today'], r['annual_return_pct']]
    for rt in (0.20, 0.28):
        r = run_scn(p, dict(base, rotce_terminal=rt)); sens[f'terminal ROTCE {rt:.0%}'] = [r['ri_today'], r['value_today'], r['annual_return_pct']]
    res['sensitivity_base'] = sens
    # reverse: constant NI growth after 2027 that makes the base value today = price
    lo, hi = -0.05, 0.25
    for _ in range(60):
        f = (lo + hi) / 2
        d = dict(base, ni_g=[base['ni_g'][0]] + [f] * 5)
        if run_scn(p, d)['value_today'] < p['price']: lo = f
        else: hi = f
    res['market_implied_ni_growth_2028_2032_pct'] = round(f * 100, 1)
    return res

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('params'); ap.add_argument('--out'); a = ap.parse_args()
    p = json.load(open(a.params)); res = run(p); txt = json.dumps(res, indent=1)
    if a.out: open(a.out, 'w').write(txt)
    for k, s in res['scenarios'].items():
        print(k, 'today', s['value_today'], '(RI', s['ri_today'], 'mult', s['multiple_today'], ') end27', s['year_end_values'][2027], 'end28', s['year_end_values'][2028], 'end31', s['year_end_values'][2031], 'ret', s['annual_return_pct'], 'price-only', s['annual_return_price_only_pct'])
        print('   eps', s['eps']); print('   rotce', s['rotce'], 'tbvps', s['tbvps']); print('   shares', s['shares_end_m'], 'P/TBV implied', s['p_tbv_implied_today'])
    print('calibrated', res['calibrated']); print('two_lens', res['two_lens']); print('entry', res['entry_prices'], res['entry_price_all_tests'])
    print('sens', res['sensitivity_base']); print('implied NI growth', res['market_implied_ni_growth_2028_2032_pct'], 'weighted', res['weighted_value_today'])
