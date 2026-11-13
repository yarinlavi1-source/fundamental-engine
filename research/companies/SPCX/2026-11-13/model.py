"""SPCX (SpaceX) custom segment model, 13.11.2026. Claude's own build; transparent arithmetic, no engine code.

Why custom and not scripts/lean_model.py: SpaceX is three businesses with different economics (Starlink = subscription
network, Space = launch + government contractor carrying Starship R&D, SI = capital-heavy AI cloud + Cursor + X), and
the value hinges on SI capital intensity (capex ~3x revenue in 2026). lean_model takes one margin and one FCF/NOPAT
conversion for the group, which cannot tie revenue to the compute fleet or D&A to capex vintages (the NBIS lesson).
This model keeps lean_model's conventions (cost of equity fading to a mature beta over 10 years, terminal growth = rf,
reinvestment = g / ROIC, value = average of DCF and exit-P/E methods, calibrated lens = exit P/E held and margin floored
at the first forecast year) so the outputs are comparable with the other saved runs.

Usage: python model.py params.json [--out results.json]
Units: $bn, shares in millions -> per-share $.
"""
import argparse
import json
import copy

YEARS = list(range(2027, 2033))          # explicit years, index 0..5; 2032 is the 'next year' for the exit P/E


def vint_dep(capex_by_year, life, year):
    """Straight-line with half-year convention: vintage v books 0.5/L in v, 1/L in v+1..v+L-1, 0.5/L in v+L."""
    d = 0.0
    for v, c in capex_by_year.items():
        a = year - v
        if a == 0 or a == life:
            d += 0.5 * c / life
        elif 0 < a < life:
            d += c / life
    return d


def in_service_gross(capex_by_year, life, year_end):
    """Gross assets in service at the end of a year (vintages retire after `life` years)."""
    return sum(c for v, c in capex_by_year.items() if year_end - life < v <= year_end)


def run_scenario(p, s, overrides=None):
    s = copy.deepcopy(s)
    p = dict(p, hist_capex={k: {int(y): c for y, c in v.items()} for k, v in p['hist_capex'].items()})
    if overrides:
        s.update(overrides)
    b = p['base_2026']
    out = {y: {} for y in YEARS}

    # ---------- Connectivity (Starlink) ----------
    subs_prev = b['subs_end_m']
    eg = b['ent_gov_rev']
    conn_capex = dict(p['hist_capex']['conn'])
    for i, y in enumerate(YEARS):
        subs = s['conn']['subs_end_m'][i]
        avg = (subs_prev + subs) / 2
        cons = avg * s['conn']['arpu'][i] * 12 * s['conn']['hw_factor'][i] / 1000.0
        eg = eg * (1 + s['conn']['ent_gov_g'][i])
        rev = cons + eg
        capex = rev * s['conn']['capex_pct'][i]
        conn_capex[y] = capex
        out[y]['conn'] = dict(subs_end_m=subs, consumer_rev=cons, ent_gov_rev=eg, rev=rev,
                              ebitda=rev * s['conn']['ebitda_m'][i], capex=capex)
        subs_prev = subs

    # ---------- Space (launch, government, Starship R&D) ----------
    rev = b['space_rev']
    space_capex = dict(p['hist_capex']['space'])
    for i, y in enumerate(YEARS):
        rev = rev * (1 + s['space']['g'][i])
        capex = s['space']['capex'][i]
        space_capex[y] = capex
        out[y]['space'] = dict(rev=rev, starship_rd=s['space']['rd'][i],
                               ebitda=rev * s['space']['core_m'][i] - s['space']['rd'][i], capex=capex)

    # ---------- SI (AI cloud/compute, Grok, Cursor, X) ----------
    si_capex = dict(p['hist_capex']['si'])
    srv_share = p['si_server_share']
    cursor, xads = b['cursor_rev'], b['x_ads_rev']
    for i, y in enumerate(YEARS):
        capex = s['si']['capex'][i]
        si_capex[y] = capex
        srv = {v: c * srv_share for v, c in si_capex.items()}
        start = in_service_gross(srv, p['server_life'], y - 1)
        end = in_service_gross(srv, p['server_life'], y)
        compute_rev = s['si']['yield'][i] * (start + end) / 2
        cursor = s['si']['cursor_rev_2027'] if i == 0 else cursor * (1 + s['si']['cursor_g'][i])
        xads = xads * (1 + p['x_ads_g'])
        rev = compute_rev + cursor + xads
        out[y]['si'] = dict(rev=rev, compute_rev=compute_rev, cursor_rev=cursor, x_ads_rev=xads,
                            servers_in_service_avg=(start + end) / 2, ebitda=rev * s['si']['ebitda_m'][i], capex=capex)

    # ---------- D&A from vintages (legacy vintages scaled so 2026 matches the reported run-rate) ----------
    def da(hist, newc, life, y, scale):
        legacy = {v: c * scale for v, c in hist.items()}
        new = {v: c for v, c in newc.items() if v >= 2027}
        return vint_dep(legacy, life, y) + vint_dep(new, life, y)

    sc = p['legacy_da_scale']
    for y in YEARS:
        out[y]['conn']['da'] = da(p['hist_capex']['conn'], conn_capex, p['conn_life'], y, sc['conn'])
        out[y]['space']['da'] = da(p['hist_capex']['space'], space_capex, p['space_life'], y, sc['space'])
        srv = {v: c * srv_share for v, c in si_capex.items()}
        bld = {v: c * (1 - srv_share) for v, c in si_capex.items()}
        hs = {v: c * srv_share for v, c in p['hist_capex']['si'].items()}
        hb = {v: c * (1 - srv_share) for v, c in p['hist_capex']['si'].items()}
        out[y]['si']['da'] = (da(hs, srv, p['server_life'], y, sc['si']) + da(hb, bld, p['building_life'], y, sc['si']))
        for seg in ('conn', 'space', 'si'):
            o = out[y][seg]
            o['ebit'] = o['ebitda'] - o['da']
            o['ebit_m'] = o['ebit'] / o['rev']

    # ---------- Group ----------
    sh = s.get('shares_m', p['shares_m'])
    cash = p['net_cash_end2026'] + p['investment_assets']
    res = {'years': {}}
    fcf_list, nopat_list, eps_list = [], [], []
    for i, y in enumerate(YEARS):
        o = out[y]
        rev = sum(o[k]['rev'] for k in ('conn', 'space', 'si'))
        ebitda = sum(o[k]['ebitda'] for k in ('conn', 'space', 'si'))
        ebit = sum(o[k]['ebit'] for k in ('conn', 'space', 'si'))
        if s.get('_margin_floor') is not None:          # calibrated lens: group EBIT margin not below the first year
            ebit = max(ebit, rev * s['_margin_floor'])
        capex = sum(o[k]['capex'] for k in ('conn', 'space', 'si'))
        da_ = sum(o[k]['da'] for k in ('conn', 'space', 'si'))
        tax = p['tax'][i] * max(ebit, 0)
        nopat = ebit - tax
        # a margin floor (calibrated lens / reverse reading) lifts EBIT through lower cash costs: FCF follows via NOPAT
        fcf = nopat + da_ - capex - p['one_off_cash'].get(str(y), 0.0)
        interest = cash * (p['cash_yield'] if cash >= 0 else p['debt_rate'])
        eps_op = nopat / sh * 1000
        eps_int = (nopat + interest * (1 - p['tax'][i])) / sh * 1000
        cash = cash + fcf + interest * (1 - p['tax'][i])
        res['years'][y] = dict(rev=rev, ebitda_after_sbc=ebitda, da=da_, ebit=ebit, ebit_m=ebit / rev, capex=capex,
                               fcf=fcf, nopat=nopat, eps_operating=eps_op, eps_with_interest=eps_int, net_cash_end=cash,
                               conn=o['conn'], space=o['space'], si=o['si'])
        fcf_list.append(fcf)
        nopat_list.append(nopat)
        eps_list.append(eps_op)

    # invested capital proxy for terminal ROIC: net PP&E end-2026 + capex - D&A to 2032
    ic = p['net_ppe_end2026'] + sum(res['years'][y]['capex'] - res['years'][y]['da'] for y in YEARS)
    roic_calc = nopat_list[-1] / ic
    roic = min(max(roic_calc, p['roic_floor']), p['roic_cap'])

    # ---------- discounting ----------
    rf, erp = p['rf'], p['erp']
    k0 = rf + s.get('beta', p['beta']) * erp
    km = rf + p['beta_mature'] * erp
    n_tail = 9
    ke = [k0 + (km - k0) * min(i, 10) / 10 for i in range(len(YEARS) + n_tail + 1)]
    g_last = s['g_2033']
    tail, n = [], nopat_list[-1]
    for j in range(1, n_tail + 1):
        g = g_last + (rf - g_last) * j / n_tail
        n *= 1 + g
        tail.append(n * (1 - g / roic))
    flows = fcf_list + tail
    tv = n * (1 + rf) * (1 - rf / roic) / (ke[len(flows)] - rf)

    cash_t = [p['net_cash_end2026'] + p['investment_assets']] + [res['years'][y]['net_cash_end'] for y in YEARS[:5]]

    def dcf_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, len(flows)):
            disc *= 1 + ke[i]
            v += flows[i] / disc
        return (v + tv / disc) * 1000 / sh + cash_t[t] * 1000 / sh

    pe = s['pe']
    exit_v = pe * eps_list[5]                       # $/share at end-2031 on 2032 operating EPS

    def mult_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, 5):
            disc *= 1 + ke[i]
            v += fcf_list[i] / disc
        return v * 1000 / sh + exit_v / disc + cash_t[t] * 1000 / sh

    dcf = [dcf_at(t) for t in range(6)]
    mul = [mult_at(t) for t in range(6)]
    val = [max((a + b2) / 2, 0.01) for a, b2 in zip(dcf, mul)]
    stub = (1 + ke[0]) ** (p['days_to_base_year_end'] / 365)
    today = val[0] / stub
    ret = (val[5] / p['price']) ** (1 / p['years_to_horizon']) - 1

    # segment DCF (sum of parts): same discounting, tax at the group path on each segment's own EBIT
    seg = {}
    for k in ('conn', 'space', 'si'):
        f, nl = [], []
        for i, y in enumerate(YEARS):
            o = res['years'][y][k]
            nop = o['ebit'] * (1 - p['tax'][i])
            nl.append(nop)
            f.append(nop + o['da'] - o['capex'])
        ic_k = p['net_ppe_split'][k] + sum(res['years'][y][k]['capex'] - res['years'][y][k]['da'] for y in YEARS)
        r_k = min(max(nl[-1] / ic_k, p['roic_floor']), p['roic_cap']) if ic_k > 0 else p['roic_cap']
        n_k, t_k = nl[-1], []
        for j in range(1, n_tail + 1):
            g = g_last + (rf - g_last) * j / n_tail
            n_k *= 1 + g
            t_k.append(n_k * (1 - g / r_k))
        fl = f + t_k

        def pv(kpath):
            tv_k = n_k * (1 + rf) * (1 - rf / r_k) / (kpath[len(fl)] - rf)
            v, disc = 0.0, 1.0
            for i in range(len(fl)):
                disc *= 1 + kpath[i]
                v += fl[i] / disc
            return (v + tv_k / disc) / stub
        bk = p['seg_beta'][k]
        k0s, kms = rf + bk * erp, rf + min(bk, p['beta_mature']) * erp
        ke_s = [k0s + (kms - k0s) * min(i, 10) / 10 for i in range(len(fl) + 1)]
        vg, vs = pv(ke), pv(ke_s)
        seg[k] = dict(value_bn=round(vg, 1), per_share=round(vg * 1000 / sh, 2),
                      value_bn_seg_beta=round(vs, 1), per_share_seg_beta=round(vs * 1000 / sh, 2), seg_beta=bk,
                      roic_2032=round(nl[-1] / ic_k, 3) if ic_k > 0 else None)
    seg['net_cash_and_investments_bn'] = round(cash_t[0] / stub, 1)
    seg['per_share_cash'] = round(cash_t[0] / stub * 1000 / sh, 2)
    seg['total_per_share_group_beta'] = round(sum(seg[k]['per_share'] for k in ('conn', 'space', 'si')) + seg['per_share_cash'], 2)
    seg['total_per_share_seg_beta'] = round(sum(seg[k]['per_share_seg_beta'] for k in ('conn', 'space', 'si')) + seg['per_share_cash'], 2)

    return dict(
        value_today=round(today, 2), dcf_today=round(dcf[0] / stub, 2), multiple_today=round(mul[0] / stub, 2),
        year_end_values={2026 + t: round(val[t], 2) for t in range(1, 6)},
        annual_return_pct=round(ret * 100, 1), ke0_pct=round(k0 * 100, 2), roic_terminal_used=round(roic, 3),
        roic_2032_calc=round(roic_calc, 3), exit_pe=pe, sum_of_parts=seg,
        path={y: dict(rev=round(r['rev'], 1), conn_rev=round(r['conn']['rev'], 1), space_rev=round(r['space']['rev'], 1),
                      si_rev=round(r['si']['rev'], 1), ebit=round(r['ebit'], 1), ebit_m=round(r['ebit_m'], 3),
                      conn_ebit_m=round(r['conn']['ebit_m'], 3), space_ebit=round(r['space']['ebit'], 2),
                      si_ebit_m=round(r['si']['ebit_m'], 3), capex=round(r['capex'], 1), da=round(r['da'], 1),
                      fcf=round(r['fcf'], 1), net_cash_end=round(r['net_cash_end'], 1),
                      eps_operating=round(r['eps_operating'], 2), eps_with_interest=round(r['eps_with_interest'], 2),
                      subs_end_m=r['conn']['subs_end_m'], si_servers_avg=round(r['si']['servers_in_service_avg'], 1))
              for y, r in res['years'].items()},
    )


def run(p):
    out = {k: run_scenario(p, s) for k, s in p['scenarios'].items()}
    pr = p['probabilities']
    base = p['scenarios']['base']
    res = {'scenarios': out}
    res['weighted_value_today'] = round(sum(pr[k] * out[k]['value_today'] for k in out), 2)
    w_end = sum(pr[k] * out[k]['year_end_values'][2031] for k in out)
    res['weighted_end_2031'] = round(w_end, 2)
    res['weighted_return_pct'] = round(((w_end / p['price']) ** (1 / p['years_to_horizon']) - 1) * 100, 1)
    res['index_return_pct'] = round((p['rf'] + p['erp']) * 100, 1)
    v31 = out['base']['year_end_values'][2031]
    res['price_for_12pct'] = round(v31 / 1.12 ** p['years_to_horizon'], 2)
    res['index_parity_price'] = round(v31 / (1 + p['rf'] + p['erp']) ** p['years_to_horizon'], 2)

    # calibrated lens: market's forward multiple held (consensus FY27 P/E ~35x; our own forward P/E is not meaningful)
    first = out['base']['path'][2027]
    pe_today_ours = p['price'] / first['eps_operating'] if first['eps_operating'] > 0 else None
    cal = run_scenario(p, base, {'pe': p['calibrated']['pe'], '_margin_floor': first['ebit_m']})
    res['calibrated'] = dict(pe_today_on_our_2027_eps=round(pe_today_ours, 1) if pe_today_ours else None,
                             pe_used=p['calibrated']['pe'], margin_floor=first['ebit_m'], why=p['calibrated']['why'],
                             value_today=cal['value_today'], year_end_values=cal['year_end_values'],
                             annual_return_pct=cal['annual_return_pct'])

    # reverse reading 1: SI yield multiplier (all years) needed for value today = price (base otherwise)
    def solve(fn, lo, hi):
        for _ in range(60):
            mid = (lo + hi) / 2
            if fn(mid) < p['price']:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def with_yield(f):
        s = copy.deepcopy(base)
        s['si']['yield'] = [y * f for y in s['si']['yield']]
        return run_scenario(p, s)['value_today']
    fy = solve(with_yield, 0.5, 6.0)
    sy = copy.deepcopy(base); sy['si']['yield'] = [y * fy for y in sy['si']['yield']]
    ry = run_scenario(p, sy)

    # reverse reading 2: all segments' revenue scaled from 2028 on, capex scaled with it (same capital intensity)
    def with_scale(f):
        s = copy.deepcopy(base)
        for i in range(1, 6):
            s['conn']['subs_end_m'][i] = base['conn']['subs_end_m'][0] + (base['conn']['subs_end_m'][i] - base['conn']['subs_end_m'][0]) * f
        s['conn']['ent_gov_g'] = [base['conn']['ent_gov_g'][0]] + [g * f for g in base['conn']['ent_gov_g'][1:]]
        s['space']['g'] = [base['space']['g'][0]] + [g * f for g in base['space']['g'][1:]]
        s['si']['capex'] = [base['si']['capex'][0]] + [c * f for c in base['si']['capex'][1:]]
        s['si']['cursor_g'] = [base['si']['cursor_g'][0]] + [g * f for g in base['si']['cursor_g'][1:]]
        return run_scenario(p, s)

    fs = solve(lambda f: with_scale(f)['value_today'], 0.2, 8.0)
    rs = with_scale(fs)

    # reverse reading 3: group EBIT margin from 2028 needed at base revenue (no capex change)
    def with_margin(mm):
        s = copy.deepcopy(base)
        s['_margin_floor'] = mm
        return run_scenario(p, s)['value_today']
    mneed = solve(with_margin, 0.0, 0.9)

    res['reverse'] = {
        'si_yield_multiplier_needed': round(fy, 2),
        'si_yield_needed': [round(y * fy, 3) for y in base['si']['yield']],
        'si_rev_2031_needed_bn': ry['path'][2031]['si_rev'], 'group_rev_2031_needed_bn_yield': ry['path'][2031]['rev'],
        'growth_scale_needed': round(fs, 2), 'group_rev_2031_needed_bn_scale': rs['path'][2031]['rev'],
        'group_rev_2027_base_bn': out['base']['path'][2027]['rev'], 'group_rev_2031_base_bn': out['base']['path'][2031]['rev'],
        'ebit_margin_floor_needed_at_base_revenue': round(mneed, 3),
        'base_ebit_margin_2031': out['base']['path'][2031]['ebit_m'],
    }

    sens = {}
    for bta in (1.5, 2.6):
        sens[f'beta {bta}'] = run_scenario(p, base, {'beta': bta})['value_today']
    for pe in (base['pe'] - 6, base['pe'] + 6):
        r = run_scenario(p, base, {'pe': pe})
        sens[f'exit P/E {pe}'] = [r['value_today'], r['annual_return_pct']]
    p4 = dict(p, server_life=4)
    r4 = run_scenario(p4, base)
    sens['GPU/server life 4y (vs 5)'] = [r4['value_today'], r4['annual_return_pct']]
    s6 = copy.deepcopy(base); s6['si']['yield'] = [max(y, base['si']['yield'][0]) for y in base['si']['yield']]
    r6 = run_scenario(p, s6)
    sens['SI yield held at 2027 level'] = [r6['value_today'], r6['annual_return_pct']]
    res['sensitivity_base'] = sens

    bands = {'buy': 12.0, 'hold': 6.0, 'lagging': 3.0}

    def band(x):
        return 'buy' if x >= bands['buy'] else 'hold' if x >= bands['hold'] else 'lagging' if x >= bands['lagging'] else 'sell'
    ours = out['base']['annual_return_pct']
    res['two_lens'] = {'ours': {'value_today': out['base']['value_today'], 'annual_return_pct': ours, 'band': band(ours)},
                       'calibrated': {'value_today': cal['value_today'], 'annual_return_pct': cal['annual_return_pct'],
                                      'band': band(cal['annual_return_pct'])}}
    res['two_lens']['agree'] = res['two_lens']['ours']['band'] == res['two_lens']['calibrated']['band']
    bt = out['bear']['annual_return_pct']
    res['buy_test'] = dict(base_ge_12_both=ours >= 12 and cal['annual_return_pct'] >= 12,
                           bull_ge_17=out['bull']['annual_return_pct'] >= 17, bear_ge_minus10=bt >= -10)
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
