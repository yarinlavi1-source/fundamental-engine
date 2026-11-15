#!/usr/bin/env python3
"""META free-cash-flow DCF with an explicit capex -> D&A vintage build (deep3, as_of 2026-11-05).

Why not scripts/lean_model.py: it takes an operating margin and an FCF/NOPAT conversion as inputs. Here D&A must come
from the capex path (empirical model fitted on META's own 2018-2026 history), the $355.7B of leases not yet commenced
must be costed once, cash taxes must follow bonus depreciation, and the Oct-Nov 2026 financing (forward sale,
mandatory convertible preferred, new notes) must be settled explicitly. So this is a custom script.

Structure (all $B, shares in millions, per-share on today's pre-Class-C-dividend basis):
  FoA cash margin x FoA revenue - RL cash loss - lease-wave cost = pre-D&A operating profit (after SBC, ex one-offs)
  - D&A(capex vintages) = EBIT; cash tax = book rate x (EBIT + 0.85 D&A - 0.60 capex), NOL carried forward
  UFCF = EBIT - cash tax + D&A - capex (SBC stays a cost, so the share count is basic shares + contracted issues)
  Explicit 2027-2036; terminal at end-2036: g = rf, ROIC = the path's 2036 NOPAT / 2035 invested capital,
  FCF_2037 = NOPAT_2037 x (1 - g/ROIC), discounted at the mature cost of equity.
  Equity value at a year-end = PV(remaining UFCF + TV at the cost of equity) + net financial position at that year-end
  (cash rolled with interest, preferred and common dividends, settlement installments; debt at face) - PV of remaining
  preferred dividends and settlement installments + non-operating investments; per share on basic + 40.0M forward
  + preferred conversion shares. Discounting unlevered FCF at the cost of equity and deducting debt at face ignores the
  debt tax shield (conservative, ~$6/share).
Usage: python dcf.py dcf_params.json --out dcf_result.json
"""
import argparse
import copy
import json

YEARS = list(range(2027, 2037))


def weights(L):
    w = [1.0 / L] * (L + 1)
    w[0] = w[-1] = 0.5 / L
    return w


def sl(capex, y, L, d):
    return sum(wj * capex.get(y - d - j, 0.0) for j, wj in enumerate(weights(L)))


def da_of(capex, y, m):
    return m['c'] * (m['alpha'] * sl(capex, y, m['L'], 0) + (1 - m['alpha']) * sl(capex, y, m['L'], 1))


def fit_da(p, L, alphas=None, include_2026=True):
    """Fit c (relative least squares) and alpha on 2018-2025 FACT (+ 2026E)."""
    capex = {int(k): v for k, v in p['capex_hist'].items()}
    capex[2026] = p['base_2026']['capex']
    obs = {int(k): v for k, v in p['da_hist'].items()}
    if include_2026:
        obs[2026] = p['base_2026']['da']
    best = None
    for a in (alphas if alphas is not None else [i / 20 for i in range(21)]):
        xs = [(a * sl(capex, y, L, 0) + (1 - a) * sl(capex, y, L, 1)) / obs[y] for y in obs]
        c = sum(xs) / sum(x * x for x in xs)
        rel = [c * x - 1 for x in xs]
        rmse = (sum(r * r for r in rel) / len(rel)) ** 0.5
        if best is None or rmse < best['rmse_pct'] / 100:
            best = {'L': L, 'alpha': a, 'c': round(c, 4), 'rmse_pct': round(rmse * 100, 1),
                    'resid_pct': {y: round(r * 100, 1) for y, r in zip(obs, rel)}}
    return best


def structural_da(capex, y, s_share=0.60, s_life=5.5, s_delay=0.25, b_life=25.0, b_delay=1.5):
    """Cross-check, not the model used: servers/network 60% of capex (share of gross PP&E additions 2025-26, Q3_10Q:637-650)
    at 5.5 years (10K:2258) placed in service ~3 months after spend; buildings and other 40% at ~25 years after ~1.5 years in
    construction in progress. Reproduces 2024 15.4 (actual 15.5) and 2025 19.4 (actual 18.6)."""
    tot = 0.0
    for v, c in capex.items():
        for share, life, delay in ((s_share, s_life, s_delay), (1 - s_share, b_life, b_delay)):
            start = v + 0.5 + delay
            tot += c * share * max(0.0, min(y + 1, start + life) - max(y, start)) / life
    return tot


def ke_path(p, beta=None, beta_m=None):
    b0 = p['beta'] if beta is None else beta
    bm = p['beta_mature'] if beta_m is None else beta_m
    k0, km = p['rf'] + b0 * p['erp'], p['rf'] + bm * p['erp']
    n = p['beta_fade_years']
    return [k0 + (km - k0) * min(i, n) / n for i in range(len(YEARS))], km


def run(p, name, s, dam, beta=None, beta_m=None, roic_override=None, ke_const=None, tmode='driver', legal_pct=0.0, da_struct=False, t0_claim=0.0, nonop_add=0.0, tv_mid=False):
    b = p['base_2026']
    nf = p['net_financial_end_2026']
    capex = {int(k): v for k, v in p['capex_hist'].items()}
    capex[2026] = b['capex']
    rev = {2026: b['revenue']}
    for i, y in enumerate(YEARS):
        rev[y] = s['revenue'][i] if i < 5 else rev[y - 1] * (1 + s['growth_after'][i - 5])
        capex[y] = s['capex'][i] if i < 5 else rev[y] * s['capex_rev_after'][i - 5]
    rl_rev = {}
    for i, y in enumerate(YEARS):
        rl_rev[y] = s['rl_rev'][i] if i < 5 else rl_rev[y - 1] * (1 + s['rl_rev_growth_after'])
    lw = p['lease_wave']
    fr = {int(k): v for k, v in lw['commenced_frac_end'].items()}
    full_cost = lw['uncommenced'] * lw['operating_share'] / lw['avg_term_years']
    lease = {y: full_cost * (fr[y - 1] + fr[y]) / 2 for y in YEARS}
    tx = p['tax']
    if ke_const is not None:
        ke, km = [ke_const] * len(YEARS), ke_const
    else:
        ke, km = ke_path(p, beta, beta_m)
    g = p['rf']
    rows, ic, nol = {}, {2026: p['ic_2026']}, 0.0
    for i, y in enumerate(YEARS):
        foa_rev = rev[y] - rl_rev[y]
        pre = foa_rev * s['foa_margin'][i] - s['rl_cash_loss'][i] - lease[y] - legal_pct * rev[y]
        da = structural_da(capex, y) if da_struct else da_of(capex, y, dam)
        ebit = pre - da
        if s.get('ebit_margin_floor') is not None:
            ebit = max(ebit, s['ebit_margin_floor'] * rev[y])
        taxable = ebit + tx['equip_share_da'] * da - tx['equip_share_capex'] * capex[y]
        if taxable < 0:
            nol += -taxable
            ctax = 0.0
        else:
            use = min(nol, taxable)
            nol -= use
            ctax = tx['book'] * (taxable - use)
        nopat_cash = ebit - ctax
        ufcf = nopat_cash + da - capex[y]
        ic[y] = ic[y - 1] + capex[y] - da
        rows[y] = dict(revenue=rev[y], capex=capex[y], capex_rev=capex[y] / rev[y], da=da, da_rev=da / rev[y],
                       lease_wave=lease[y], rl_cash_loss=s['rl_cash_loss'][i], pre_da_margin=pre / rev[y],
                       ebit=ebit, ebit_margin=ebit / rev[y], cash_tax=ctax, cash_tax_rate=ctax / ebit if ebit > 0 else None,
                       nopat_cash=nopat_cash, ufcf=ufcf, ic_end=ic[y], roic_on_open_ic=nopat_cash / ic[y - 1])
    # terminal
    roic_t = roic_override if roic_override else rows[2036]['nopat_cash'] / ic[2035]
    roic_t = max(roic_t, g + 0.01)
    nopat37 = rows[2036]['nopat_cash'] * (1 + g)
    fcf37 = nopat37 * (1 - g / roic_t)
    if tmode == 'cash':
        fcf37 = rows[2036]['ufcf'] * (1 + g)
    elif tmode == 'path_ronic':
        rr = (rows[2036]['capex'] - rows[2036]['da']) / rows[2036]['nopat_cash']
        fcf37 = nopat37 * (1 - rr)
    tv = fcf37 / (km - g)
    reinvest_2036 = (rows[2036]['capex'] - rows[2036]['da']) / rows[2036]['nopat_cash']

    def pv_at(t_idx):  # PV at end of YEARS[t_idx-1] (t_idx=0 -> end-2026) of UFCF after it + TV
        v, disc = 0.0, 1.0
        for i in range(t_idx, len(YEARS)):
            k = ke[i]
            half = disc * (1 + k) ** 0.5 if p['mid_year'] else disc * (1 + k)
            disc *= 1 + k
            v += rows[YEARS[i]]['ufcf'] / half
        return v + (tv * (1 + km) ** 0.5 if tv_mid else tv) / disc

    # financial roll
    pref = p['preferred']
    pdiv = {int(k): v for k, v in pref['div_by_year'].items()}
    ag = p['ag_settlement']
    agpay = {y: ag['payable'] / ag['years'] for y in YEARS}
    extra = {int(k): v for k, v in (s.get('extra_claims') or {}).items()}
    debt = nf['debt_face_sep30'] + nf['usd_notes_face'] + nf['eur_notes_face']
    cash = {2026: nf['cash_securities_sep30'] + nf['q4_fcf'] + nf['q4_dividend'] + nf['preferred_net'] + nf['usd_notes_net']
            + nf['eur_notes_net'] + nf['forward_proceeds'] + nf['escrow_pv']}
    sh_basic = p['shares_basic_m']
    conv = s['pref_conv_m']
    shares = sh_basic + p['forward_shares_m'] + conv
    nonop = p['nonop_investments'] * (1 - s['nonop_haircut']) + nonop_add

    def pv_list(d, after, r=0.05):
        return sum(v / (1 + r) ** (y - after - 0.5) for y, v in d.items() if y > after)

    eps, divps, int_net = {}, {}, {}
    for y in YEARS:
        c0 = cash[y - 1]
        interest = (c0 * nf['cash_yield'] if c0 >= 0 else c0 * nf['marginal_funding_rate']) - debt * nf['debt_coupon_blended']
        out_sh = sh_basic + (p['forward_shares_m'] if y >= 2027 else 0) + (conv if y >= 2029 else 0)
        cdiv = p['dps'] * out_sh / 1000
        divps[y] = p['dps']
        cash[y] = (c0 + rows[y]['ufcf'] + interest * (1 - tx['book']) - pdiv.get(y, 0.0) - agpay.get(y, 0.0)
                   - extra.get(y, 0.0) - cdiv)
        int_net[y] = interest
        eps[y] = ((rows[y]['ebit'] + interest) * (1 - tx['book']) - pdiv.get(y, 0.0)) / shares * 1000
    vals = {}
    for t in range(0, 6):
        y = 2026 + t
        claims = pv_list(pdiv, y) + pv_list(agpay, y) + pv_list(extra, y)
        eq = pv_at(t) + cash[y] - debt - claims + nonop - t0_claim * (1.05 ** t)
        vals[y] = eq / shares * 1000
    today = vals[2026] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365)
    div_to_2031 = 0.525 + sum(divps[y] for y in range(2027, 2032))
    ret = ((vals[2031] + div_to_2031) / p['price']) ** (1 / p['years_to_horizon']) - 1
    pe_exit = vals[2031] / eps[2032] if eps[2032] > 0 else None
    return {
        'value_today': round(today, 1),
        'year_end_values': {y: round(vals[y], 1) for y in range(2027, 2032)},
        'annual_return_pct_incl_div': round(ret * 100, 2),
        'implied_exit_pe_2031_on_2032_eps': round(pe_exit, 1) if pe_exit else None,
        'eps_book': {y: round(eps[y], 2) for y in YEARS},
        'terminal': {'roic': round(roic_t, 4), 'g': g, 'ke_mature': round(km, 4), 'fcf_2037': round(fcf37, 1),
                     'tv_end_2036': round(tv, 1), 'tv_over_nopat_2037': round(tv / nopat37, 2),
                     'reinvestment_rate_2036_path': round(reinvest_2036, 3), 'reinvestment_rate_terminal': round(g / roic_t, 3),
                     'pv_tv_share_of_ev_today': round((tv / _disc_total(ke, p)) / pv_at(0), 3)},
        'ke_path': [round(k, 4) for k in ke],
        'shares_m': round(shares, 1),
        'net_cash_end': {y: round(cash[y] - debt, 1) for y in range(2026, 2037)},
        'rows': {y: {k: (round(v, 4) if isinstance(v, float) and abs(v) < 5 else (round(v, 1) if isinstance(v, float) else v))
                     for k, v in r.items()} for y, r in rows.items()},
        'pv_ufcf_2027_2036_at_end_2026': round(pv_at(0) - tv / _disc_total(ke, p), 1),
        'pv_tv_at_end_2026': round(tv / _disc_total(ke, p), 1),
        'equity_bridge_end_2026': {'cash_and_receivables': round(cash[2026], 1), 'debt_face': round(debt, 1),
                                   'claims_pv': round(pv_list(pdiv, 2026) + pv_list(agpay, 2026) + pv_list(extra, 2026), 1),
                                   'nonop': round(nonop, 1)},
    }


def _disc_total(ke, p):
    d = 1.0
    for k in ke:
        d *= 1 + k
    return d


def band(r, b):
    return 'buy' if r >= b['buy'] else 'hold' if r >= b['hold'] else 'lagging' if r >= b['lagging'] else 'sell'


def calibrated_lens(p, s, dam, base_res, floor_margin=True):
    """Two-lens rule (CLAUDE.md 05.11.2026): hold today's forward P/E (price / our 2027 EPS) as the exit multiple on
    2032 EPS at end-2031; optionally floor the FoA cash margin at its 2027 level. D&A and lease waves stay as named
    hard costs (they are quantified), so they are not floored."""
    pe_today = p['price'] / base_res['eps_book'][2027]
    s2 = copy.deepcopy(s)
    if floor_margin:
        s2['foa_margin'] = [max(s['foa_margin'][0], m) for m in s['foa_margin']]
    r = run(p, 'cal', s2, dam)
    v31 = pe_today * r['eps_book'][2032]
    div = 0.525 + 5 * p['dps']
    ret = ((v31 + div) / p['price']) ** (1 / p['years_to_horizon']) - 1
    ke0 = r['ke_path'][0]
    today = (v31 + 0) / (1 + ke0) ** p['years_to_horizon'] + sum(p['dps'] / (1 + ke0) ** (t + 0.5) for t in range(5))
    return {'pe_today_on_our_2027_eps': round(pe_today, 1), 'eps_2032': r['eps_book'][2032], 'value_end_2031': round(v31, 1),
            'value_today_approx': round(today, 1), 'annual_return_pct_incl_div': round(ret * 100, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('params')
    ap.add_argument('--out')
    a = ap.parse_args()
    p = json.load(open(a.params))
    res = {'as_of': p['as_of'], 'price': p['price']}
    fits = {L: fit_da(p, L) for L in (4, 6, 8)}
    fits_no26 = {L: fit_da(p, L, include_2026=False) for L in (4, 6, 8)}
    d0 = {L: fit_da(p, L, alphas=[1.0]) for L in (4, 6, 8)}
    d1 = {L: fit_da(p, L, alphas=[0.0]) for L in (4, 6, 8)}
    res['da_model_fits'] = {'alpha_c_fit_incl_2026E': fits, 'alpha_c_fit_2018_2025_only': fits_no26,
                            'no_delay_only': d0, 'one_year_delay_only': d1}
    dam = fits[p['da_model_base']['L']]
    res['da_model_used'] = dam
    out = {k: run(p, k, s, dam) for k, s in p['scenarios'].items()}
    res['scenarios'] = out
    pr = p['probabilities']
    res['weighted'] = {'value_today': round(sum(pr[k] * out[k]['value_today'] for k in out), 1),
                       'value_end_2031': round(sum(pr[k] * out[k]['year_end_values'][2031] for k in out), 1)}
    res['weighted']['annual_return_pct_incl_div'] = round(
        (((res['weighted']['value_end_2031'] + 0.525 + 5 * p['dps']) / p['price']) ** (1 / p['years_to_horizon']) - 1) * 100, 2)
    res['index_return_pct'] = round((p['rf'] + p['erp']) * 100, 2)
    base = p['scenarios']['base']
    bres = out['base']
    res['band_base'] = band(bres['annual_return_pct_incl_div'], p['bands'])
    # two lenses
    cal = calibrated_lens(p, base, dam, bres, floor_margin=True)
    cal_nofloor = calibrated_lens(p, base, dam, bres, floor_margin=False)
    res['two_lens'] = {'ours': {'value_today': bres['value_today'], 'annual_return_pct_incl_div': bres['annual_return_pct_incl_div'],
                                'band': res['band_base'], 'exit_pe_implied': bres['implied_exit_pe_2031_on_2032_eps']},
                       'calibrated_pe_held_margin_floored': dict(cal, band=band(cal['annual_return_pct_incl_div'], p['bands'])),
                       'calibrated_pe_held_margins_as_path': dict(cal_nofloor, band=band(cal_nofloor['annual_return_pct_incl_div'], p['bands']))}
    capb = {int(k): v for k, v in p['capex_hist'].items()}
    capb[2026] = p['base_2026']['capex']
    for i, y in enumerate(YEARS):
        capb[y] = base['capex'][i] if i < 5 else bres['rows'][y]['capex']
    res['da_structural_check_base'] = {y: {'structural': round(structural_da(capb, y), 1), 'empirical_used': round(da_of(capb, y, dam), 1)}
                                       for y in (2024, 2025, 2026, 2027, 2029, 2031, 2033, 2036)}
    s_floor = copy.deepcopy(base)
    s_floor['ebit_margin_floor'] = bres['rows'][2027]['ebit'] / bres['rows'][2027]['revenue']
    st = run(p, 'b', s_floor, dam)
    pe0 = res['two_lens']['calibrated_pe_held_margin_floored']['pe_today_on_our_2027_eps']
    v31 = pe0 * st['eps_book'][2032]
    res['two_lens']['street_like_no_DA_wave_after_2027'] = {
        'ebit_margin_floor': round(s_floor['ebit_margin_floor'], 4), 'eps_book': st['eps_book'], 'pe_held': pe0,
        'value_end_2031': round(v31, 1),
        'annual_return_pct_incl_div': round((((v31 + 0.525 + 5 * p['dps']) / p['price']) ** (1 / p['years_to_horizon']) - 1) * 100, 2),
        'dcf_value_today_if_cash_costs_also_held': st['value_today'],
        'note': 'what the price needs: the D&A and lease waves fully offset by other costs (as the consensus path implies); shown to name the cost of the wave, not as a forecast'}
    chk = {}
    for path in ([193] * 5, [193, 150, 150, 150, 150], [193, 100, 90, 90, 90]):
        cc = {int(k): v for k, v in p['capex_hist'].items()}
        cc[2026] = p['base_2026']['capex']
        cc.update({2027 + i: v for i, v in enumerate(path)})
        chk['capex 2027-31 ' + '/'.join(str(v) for v in path)] = {
            'da_2028': round(da_of(cc, 2028, dam), 1), 'da_2030': round(da_of(cc, 2030, dam), 1),
            'structural_2028': round(structural_da(cc, 2028), 1), 'structural_2030': round(structural_da(cc, 2030), 1)}
    res['consensus_da_check'] = {'consensus_da_2028': 47.3, 'consensus_da_2030': 70.4,
                                 'consensus_src': 'ESTIMATE from FMP EBITDA minus EBIT means, evidence_consensus.md (pulled 2026-11-05)',
                                 'model_da_under_capex_paths': chk,
                                 'reading': 'even capex cut to ~$90B a year from 2028 gives D&A above the consensus path: the street margin of 36-37% embeds less depreciation than the 2026-27 capex already produces'}
    res['atm_note'] = ('The ~$60B Class C ATM (fwp_1026:29-31, not yet established) is NOT added to the base share count: the funding gap it '
                       'would fill is already charged to today\'s holders as negative free cash flow and net debt (base net debt peaks in 2029). '
                       'Selling it at market above our value would be accretive: at $634 vs base %.0f, about $%.1f per share.' % (
                           bres['value_today'], 60 * (1 - bres['value_today'] / p['price']) / bres['shares_m'] * 1000))
    # sensitivities on base
    sens = {}
    for bb in (1.2, 1.7):
        sens[f'beta {bb}'] = run(p, 'b', base, dam, beta=bb)['value_today']
    sens['beta 1.2 fading to 1.0'] = run(p, 'b', base, dam, beta=1.2, beta_m=1.0)['value_today']
    for L in (4, 8):
        sens[f'D&A fit L={L}'] = run(p, 'b', base, fits[L])['value_today']
    sens['D&A L6 no delay (c=%.3f)' % d0[6]['c']] = run(p, 'b', base, d0[6])['value_today']
    sens['D&A L6 one-year delay (c=%.3f)' % d1[6]['c']] = run(p, 'b', base, d1[6])['value_today']
    for f in (0.85, 1.15):
        s2 = copy.deepcopy(base)
        s2['capex'] = [base['capex'][0]] + [c * f for c in base['capex'][1:]]
        s2['capex_rev_after'] = [c * f for c in base['capex_rev_after']]
        sens[f'capex 2028+ x{f}'] = run(p, 'b', s2, dam)['value_today']
    for dm in (-0.015, 0.015):
        s2 = copy.deepcopy(base)
        s2['foa_margin'] = [m + dm for m in base['foa_margin']]
        sens[f'FoA cash margin {dm:+.3f}'] = run(p, 'b', s2, dam)['value_today']
    for term in (12, 25):
        p2 = copy.deepcopy(p)
        p2['lease_wave']['avg_term_years'] = term
        sens[f'lease term {term}y'] = run(p2, 'b', base, dam)['value_today']
    p2 = copy.deepcopy(p)
    p2['lease_wave']['operating_share'] = 0.0
    sens['no lease wave (as if all in capex)'] = run(p2, 'b', base, dam)['value_today']
    km = p['rf'] + p['beta_mature'] * p['erp']
    sens['terminal ROIC = ke (growth adds no value)'] = run(p, 'b', base, dam, roic_override=km)['value_today']
    sens['terminal ROIC 25%'] = run(p, 'b', base, dam, roic_override=0.25)['value_today']
    p2 = copy.deepcopy(p)
    p2['tax']['equip_share_capex'] = 0.0
    p2['tax']['equip_share_da'] = 0.0
    sens['cash tax = book 16% on EBIT'] = run(p2, 'b', base, dam)['value_today']
    for k in ('bear', 'bull'):
        s2 = copy.deepcopy(base)
        s2['revenue'] = p['scenarios'][k]['revenue']
        s2['growth_after'] = p['scenarios'][k]['growth_after']
        sens[f'{k} revenue, base costs and capex'] = run(p, 'b', s2, dam)['value_today']
    res['sensitivity_base_value_today'] = sens
    # reverse: constant cost of equity that equates base value with the price
    lo, hi = 0.05, 0.15
    for _ in range(60):
        mid = (lo + hi) / 2
        if run(p, 'b', base, dam, ke_const=mid)['value_today'] > p['price']:
            lo = mid
        else:
            hi = mid
    res['implied_constant_cost_of_equity_at_price_pct'] = round(mid * 100, 2)
    # reverse 2: uniform FoA cash-margin shift that equates base value with the price
    lo, hi = -0.05, 0.25
    for _ in range(60):
        mid2 = (lo + hi) / 2
        s2 = copy.deepcopy(base)
        s2['foa_margin'] = [m + mid2 for m in base['foa_margin']]
        if run(p, 'b', s2, dam)['value_today'] < p['price']:
            lo = mid2
        else:
            hi = mid2
    res['implied_foa_margin_shift_at_price_pp'] = round(mid2 * 100, 1)
    # reverse 3: capex scale 2028+ that equates base value with the price
    lo, hi = 0.2, 1.0
    for _ in range(60):
        f = (lo + hi) / 2
        s2 = copy.deepcopy(base)
        s2['capex'] = [base['capex'][0]] + [c * f for c in base['capex'][1:]]
        s2['capex_rev_after'] = [c * f for c in base['capex_rev_after']]
        if run(p, 'b', s2, dam)['value_today'] > p['price']:
            lo = f
        else:
            hi = f
    res['implied_capex_scale_2028_plus_at_price'] = round(f, 3)
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        open(a.out, 'w').write(txt)
    print(json.dumps({k: res[k] for k in ('da_model_used', 'weighted', 'band_base', 'two_lens', 'sensitivity_base_value_today',
                                          'implied_constant_cost_of_equity_at_price_pct', 'implied_foa_margin_shift_at_price_pp',
                                          'implied_capex_scale_2028_plus_at_price')}, indent=1, default=str))
    for k, r in out.items():
        print(k, 'today', r['value_today'], 'YE', r['year_end_values'], 'ret', r['annual_return_pct_incl_div'],
              'exitPE', r['implied_exit_pe_2031_on_2032_eps'], 'term', r['terminal'])


if __name__ == '__main__':
    main()
