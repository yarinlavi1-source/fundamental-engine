#!/usr/bin/env python3
"""META reverse valuation (deep3 lane 'reverse'), as_of 2026-11-05, price $633.80 (IBKR 09:15 UTC).

Method: start from the price. Build a bottom-up operating model (revenue -> EBITDA after SBC -> D&A from a capex vintage
model -> operating margin; FCF after SBC = NOPAT + D&A - capex + bonus-depreciation tax deferral), feed it to
scripts/lean_model.py, then solve each lever alone for value today = price (revenue, EBITDA margin, capex, exit P/E, beta,
terminal ROIC), map each requirement to base rates / META's own history, and build bear/base/bull from that judgement.

Labels: FACT (file/line), GUIDANCE, CONSENSUS, ESTIMATE (this lane), MEMORY (unverified). $B unless stated.
Writes reverse_params.json (lean_model params) and reverse_result.json (lean_model output + reverse analysis).
"""
import copy
import json
import sys

ROOT = '/home/user/fundamental-engine'
OUT = ROOT + '/runs/tickers/META_2026-11-05/deep3/'
sys.path.insert(0, ROOT + '/scripts')
sys.path.insert(0, ROOT)
import lean_model as lm                      # noqa: E402
from fundamental_engine import base_rates as br  # noqa: E402

PRICE = 633.80
RF, ERP = 0.0494, 0.042
YEARS = list(range(2027, 2033))

# ---------------- FACTS / GUIDANCE ----------------
# Capex incl. finance-lease principal, $B (FACT SEC companyfacts via hist.json / evidence_history.md; 2026 = GUIDANCE mid
# of $135-145B, runs/tickers/META_2026-11-04/deep/filings.md section 2).
CAPEX_HIST = {2013: 1.4, 2014: 1.8, 2015: 2.5, 2016: 4.5, 2017: 6.7, 2018: 13.9, 2019: 15.7, 2020: 15.8, 2021: 19.4,
              2022: 32.0, 2023: 28.1, 2024: 39.2, 2025: 72.2, 2026: 140.0}
DA_HIST = {2019: 5.7, 2020: 6.9, 2021: 8.0, 2022: 8.7, 2023: 11.2, 2024: 15.5, 2025: 18.6,
           2026: 28.0}   # 2026 = 9M FACT 19.5 + Q4 ESTIMATE 8.5 (Q3 7.15, +~10% q/q)
REV0 = 257.0          # 2026E: 9M FACT 181.7 + Q4 ESTIMATE 75.3 (guide $73-76B, beat record: 4 of 5 at/above top)
# 2026 operating income ex legal charges: 9M FACT 54.635 + charges 9.89 + 2.40 = 66.9; Q4 = 75.3 - expenses 49.9 (guide
# total expenses $175-179B less 9M 127.08, mid) = 25.4 -> 92.3 = 35.9% (ESTIMATE from GUIDANCE). EBITDA after SBC 46.8%.
OPM_2026, EBITDA_2026 = 92.3 / REV0, (92.3 + DA_HIST[2026]) / REV0

# ---------------- D&A vintage model (ESTIMATE, fitted to FACT D&A 2019-2026) ----------------
# Equipment share s_eq of each vintage enters service lag_eq after mid-year and depreciates straight-line over 4.5 years
# (vintages before 2025) or 5.5 years (2025+, FACT 10-K: servers/network extended to 5.5 years from 1.1.2025); the rest
# (buildings, CIP, land) enters service lag_b later, 85% depreciable over 25 years (FACT 10-K: buildings 25-30 years).
def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def da_year(capex, t, s_eq, lag_eq, lag_b=1.5, life_b=25.0):
    tot = 0.0
    for v, c in capex.items():
        life = 4.5 if v < 2025 else 5.5
        st = v + 0.5 + lag_eq
        tot += s_eq * c * _overlap(t, t + 1, st, st + life) / life
        st = v + 0.5 + lag_b
        tot += (1 - s_eq) * c * 0.85 * _overlap(t, t + 1, st, st + life_b) / life_b
    return tot


def da_eq_year(capex, t, s_eq, lag_eq):
    tot = 0.0
    for v, c in capex.items():
        life = 4.5 if v < 2025 else 5.5
        st = v + 0.5 + lag_eq
        tot += s_eq * c * _overlap(t, t + 1, st, st + life) / life
    return tot


def fit_da():
    best = None
    for s in [x / 100 for x in range(45, 76, 1)]:
        for lag in [x / 20 for x in range(0, 21)]:
            errs = [da_year(CAPEX_HIST, t, s, lag) / DA_HIST[t] - 1 for t in range(2021, 2027)]
            w = [0.5, 0.5, 1, 1, 2, 3]       # recent years matter most (life policy changed)
            sse = sum(wi * e * e for wi, e in zip(w, errs))
            if best is None or sse < best[0]:
                best = (sse, s, lag, [round(e, 3) for e in errs])
    return best


_FIT = fit_da()
S_EQ, LAG_EQ = _FIT[1], _FIT[2]

# ---------------- financing and claims (end-2026 net cash, $B) ----------------
NET_CASH_ITEMS = {
    'cash_and_securities_2026-09-30 (FACT filings.md s3, R3Q26:48)': 82.449,
    'notes_face_2026-09-30 (FACT Q3_10Q:702-720)': -84.0,
    'Q4_2026_company_FCF (ESTIMATE: OCF ~37 - capex 54.2 = guide mid 140 - 9M 85.82)': -17.2,
    'Q4_2026_dividends_and_RSU_tax_withholding (ESTIMATE from 9M FACT run-rate)': -5.7,
    'mandatory_convertible_preferred_net_proceeds (FACT 8k_1029: 250M dep. shares x $50; ~2% fees ESTIMATE)': 12.25,
    'USD_15.0B_and_EUR_9.0B_notes (FACT fwp_1103/fwp_1104): cash in = debt in': 0.0,
    'forward_sale_40M_sh_x_$622.92 (FACT 8k_1029), settle by 19.11.2027, PV (ESTIMATE)': 24.6,
    'AG_settlement_payable ~$12.7B over 10y (FACT filings.md s6), after-tax PV (ESTIMATE)': -9.0,
    'non-marketable_equity_investments $21.1B book (FACT filings.md s3) at 70% (ESTIMATE, investment asset)': 14.8,
}
NET_CASH_2026 = round(sum(NET_CASH_ITEMS.values()), 2)
# Excluded on purpose: restricted cash 13.1B (escrow, not spendable, FACT); operating-lease liabilities 31.2B and the
# $355.7B of leases not commenced (their cost is inside the EBITDA margin path below); RVG $41.5B (not probable; bear charge).
DILUTED_SH = 2573.0   # FACT Q3'26 diluted weighted shares (release line 175)
FORWARD_SH = 40.0     # FACT 8k_1029
PREF_SH = {'bear': 20.0, 'base': 18.5, 'bull': 17.0}   # FACT fwp_1027: 17.0M (min rate, >$734.21) to 20.0M (max, <$625)
PREF_DIV = 812.5      # $M a year, FACT 6.50% x $12.5B, to ~15.8.2029; not tax deductible

TAX_BOOK = 0.16       # GUIDANCE Q4 tax rate 15-17%
CASH_TAX_FLOOR = 0.06  # ESTIMATE: cash taxes FACT 8.8% of pretax in 2025 (7.58/85.93), 5.5% in 9M26 (2.93/53.5)
ROIC_T = 0.16          # ESTIMATE terminal return on new capital (see judgement)

# ---------------- scenarios (ESTIMATE unless labelled) ----------------
REV_PATHS = {   # $B 2027-2031 from evidence_revenue.md (revenue lane), 2032 growth from the same file
    'bear': ([289.0, 301.5, 329.7, 354.2, 376.8], 0.055),
    'base': ([309.8, 359.8, 408.5, 455.2, 501.9], 0.09),
    'bull': ([326.0, 396.3, 470.3, 548.4, 628.4], 0.125),
}
SCEN = {
    'bear': dict(ebitda=[0.445, 0.430, 0.435, 0.440, 0.445, 0.445], capex=[195, 165, 145, 135, 135], pe=14.0,
                 charge={0: 812 + 4250, 1: 812 + 10000 + 8400, 2: 510, 3: 10000}),
    'base': dict(ebitda=[0.465, 0.475, 0.480, 0.485, 0.490, 0.490], capex=[205, 220, 210, 200, 195], pe=18.0,
                 charge={0: 812 + 1700, 1: 812 + 3000 + 2520, 2: 510}),
    'bull': dict(ebitda=[0.480, 0.495, 0.510, 0.520, 0.530, 0.530], capex=[215, 245, 260, 265, 270], pe=22.0,
                 charge={0: 812, 1: 812, 2: 510}),
}
PROBS = {'bear': 0.25, 'base': 0.50, 'bull': 0.25}   # SUBJECTIVE (Claude's judgement), see reverse_result.json

# Probability of each requirement for value = price (SUBJECTIVE, Claude's judgement; numbers filled from the solves below)
JUDGEMENT = {
    'label': 'SUBJECTIVE probabilities (Claude judgement), not engine odds',
    'revenue_alone': {'p': 0.12, 'why': '2031 revenue ~$581B (17.7% CAGR) with base margins and capex: >$50B base-rate '
                      'share 3.3% (Base Rate Book, 5y real); raised for META-specific evidence (6 quarters >20%, price per '
                      'ad +12-14% on measured AI conversion gains, consensus 2031 ~$535B) but still above consensus'},
    'ebitda_alone': {'p': 0.10, 'why': 'EBITDA after SBC ~+8.6 pts every year (2031 57.6%): equals META\'s 2017 peak '
                     '(57.1%, FACT hist.json) while $355.7B of leases commence (FACT 10-Q) and 2026 fell to ~46.8%'},
    'capex_alone': {'p': 0.10, 'why': 'capex back to ~$118-133B a year from 2028 (24% of revenue by 2031) with no loss of '
                    'revenue: contradicts the $60B ATM plan, the $454.5B commitments and the $355.7B leases (FACT)'},
    'exit_pe_alone': {'p': 0.05, 'why': '32x forward operating EPS at end-2031 (24x if only the multiple method counts); '
                      'META realized forward P/E median 20.4x 2016-24, >=24x in 2 of 9 year-ends (FACT analysis.json)'},
    'discount_rate_alone': {'p': 0.05, 'why': 'flat beta ~0.72 (cost of equity ~8.0%); every measured window is 1.25-2.23 '
                            '(beta.json)'},
    'market_story_joint': {'p': 0.27, 'why': 'consensus revenue ($312B 2027 -> ~$535B 2031) AND EBITDA after SBC rising '
                           'to ~54-56% by 2031 (Family of Apps holds ~55% while Reality Labs losses go to ~0) with base '
                           'capex: P(revenue at consensus) ~0.4 x P(margin recovery | revenue) ~0.65'},
    'p_value_at_least_price': 0.30,
    'scenario_probabilities': PROBS,
    'scenario_basis': {'bear': 'revenue 8.0% CAGR (base-rate share 25.6%), EBITDA 43-44.5%, capex cut to $135B, '
                       'P/E 14x, legal/tax/RVG charges ~$39B; precedent 2022 (revenue -1%, op margin 24.8%)',
                       'base': 'revenue 14.3% CAGR (share 7.9%; consensus -6% by 2030), EBITDA 46.5 -> 49%, capex '
                       '$195-220B, D&A to ~23% of revenue, P/E 18x',
                       'bull': 'revenue 19.6% CAGR (share 2.5%), EBITDA 48 -> 53%, capex up to $270B, P/E 22x'},
}


def growth_from_levels(levels, g6):
    g, prev = [], REV0
    for x in levels:
        g.append(x / prev - 1)
        prev = x
    return g + [g6]


def build(name, rev_growth=None, ebitda=None, capex=None, pe=None, rev_scale=None):
    """Bottom-up drivers -> lean_model scenario dict + a detail table."""
    s = SCEN[name]
    g = list(rev_growth) if rev_growth else growth_from_levels(*REV_PATHS[name])
    if rev_scale is not None:
        g = [g[0]] + [x * rev_scale for x in g[1:]]
    eb = list(ebitda or s['ebitda'])
    cx = list(capex or s['capex'])
    rev = [REV0]
    for x in g:
        rev.append(rev[-1] * (1 + x))
    cap_all = dict(CAPEX_HIST)
    for i, y in enumerate(range(2027, 2032)):
        cap_all[y] = cx[i]
    m, conv, det = [], [], []
    for i, y in enumerate(YEARS):
        r = rev[i + 1]
        if y < 2032:
            da = da_year(cap_all, y, S_EQ, LAG_EQ)
        else:
            # terminal-consistent 2032: reinvestment = g / ROIC, so 2032 capex is implied (reported, not chosen);
            # iterate because the 2032 vintage adds a little 2032 D&A
            c32 = cx[-1]
            for _ in range(20):
                cap_all[2032] = c32
                da = da_year(cap_all, y, S_EQ, LAG_EQ)
                ebit = r * (eb[i] - da / r)
                nopat = ebit * (1 - TAX_BOOK)
                c32 = da + nopat * g[5] / ROIC_T
        ebit = r * eb[i] - da
        mi = ebit / r
        nopat = ebit * (1 - TAX_BOOK)
        cap = cap_all[y]
        da_eq = da_eq_year(cap_all, y, S_EQ, LAG_EQ)
        deferral = 0.21 * max(0.0, S_EQ * cap - da_eq)            # 100% bonus depreciation on equipment (OBBBA)
        deferral = max(0.0, min(deferral, (TAX_BOOK - CASH_TAX_FLOOR) * ebit))
        fcf = nopat + da - cap + deferral
        if y == 2032:
            fcf = nopat * (1 - g[5] / ROIC_T)                        # exactly terminal-consistent
            deferral = 0.0
        m.append(round(mi, 4))
        conv.append(round(fcf / nopat, 4))
        det.append({'year': y, 'revenue': round(r, 1), 'growth': round(g[i], 4), 'ebitda_margin_after_sbc': eb[i],
                    'da': round(da, 1), 'da_pct_rev': round(da / r, 4), 'op_margin': round(mi, 4),
                    'capex': round(cap, 1), 'capex_pct_rev': round(cap / r, 4), 'tax_deferral': round(deferral, 1),
                    'nopat': round(nopat, 1), 'fcf_after_sbc': round(fcf, 1), 'conv': round(fcf / nopat, 3)})
    d = {'g': [round(x, 5) for x in g], 'm': m, 'conv': conv, 'pe': pe if pe is not None else s['pe'],
         'charge': {str(k): v for k, v in s['charge'].items()},
         'shares_m': DILUTED_SH + FORWARD_SH + PREF_SH[name]}
    return d, det


def params(scen=None):
    sc = scen or {k: build(k)[0] for k in SCEN}
    return {
        'ticker': 'META', 'as_of': '2026-11-05', 'price': PRICE, 'rf': RF, 'erp': ERP,
        'beta': 1.40, 'beta_mature': 1.05,
        'shares_m': DILUTED_SH + FORWARD_SH + PREF_SH['base'],
        'net_cash_m': NET_CASH_2026 * 1000, 'rev0_m': REV0 * 1000, 'base_year': 2026, 'tax': TAX_BOOK,
        'cash_yield': 0.04, 'debt_rate': 0.058, 'roic_terminal': ROIC_T,
        'days_to_base_year_end': 56, 'years_to_horizon': 5.153, 'buy_return': 0.12,
        'probabilities': PROBS, 'bands': {'buy': 12.0, 'hold': 6.0, 'lagging': 3.0},
        'scenarios': sc,
    }


def value_today(p, d):
    return lm.run_scenario(p, d)['value_today']


def solve(fn, lo, hi, target=PRICE, n=60):
    """Bisection on a monotone increasing fn(x) for fn(x) = target; returns None if not bracketed."""
    flo, fhi = fn(lo), fn(hi)
    if (flo - target) * (fhi - target) > 0:
        return None
    for _ in range(n):
        mid = (lo + hi) / 2
        if (fn(mid) - target) * (flo - target) > 0:
            lo, flo = mid, fn(mid)
        else:
            hi = mid
    return (lo + hi) / 2


def bands_share_real(cagr_nominal, years=5):
    label, table = br.reference_class(REV0 * 1e9)
    return label, br.share_at_least(table[years][0], br.real(cagr_nominal))


def main():
    sc, details = {}, {}
    for k in SCEN:
        sc[k], details[k] = build(k)
    p = params(sc)
    json.dump(p, open(OUT + 'reverse_params.json', 'w'), indent=1)
    res = lm.run(p)
    base_d = sc['base']

    # ---------- reverse: each lever alone (others at base) ----------
    rev = {}
    # 1 revenue: scale 2028-2032 growth (2027 held near consensus); capex dollars held
    f = solve(lambda x: value_today(p, build('base', rev_scale=x)[0]), 0.5, 4.0)
    if f:
        d, det = build('base', rev_scale=f)
        r31 = det[4]['revenue']
        cagr = (r31 / REV0) ** (1 / 5) - 1
        rev['revenue_alone'] = {'growth_scale_2028_32': round(f, 3), 'revenue_2031': r31, 'cagr_2026_31': round(cagr, 4),
                                'growth_path': [round(x, 3) for x in d['g']], 'op_margin_2031': det[4]['op_margin'],
                                'base_rate_share_>=cagr_5y_real': round(bands_share_real(cagr)[1], 4)}
    # 2 EBITDA margin: add the same points to every year
    dm = solve(lambda x: value_today(p, build('base', ebitda=[e + x for e in SCEN['base']['ebitda']])[0]), -0.2, 0.4)
    if dm is not None:
        d, det = build('base', ebitda=[e + dm for e in SCEN['base']['ebitda']])
        rev['ebitda_margin_alone'] = {'add_pts': round(dm * 100, 2), 'ebitda_2031': round(SCEN['base']['ebitda'][4] + dm, 4),
                                      'op_margin_2027': det[0]['op_margin'], 'op_margin_2031': det[4]['op_margin']}
    # 3 capex: scale 2028-2031 capex (2027 held: commitments $121.4B due 2027, FACT)
    fc = solve(lambda x: -value_today(p, build('base', capex=[205] + [c * x for c in SCEN['base']['capex'][1:]])[0]),
               0.0, 1.5, target=-PRICE)
    if fc is not None:
        cx = [205] + [c * fc for c in SCEN['base']['capex'][1:]]
        d, det = build('base', capex=cx)
        rev['capex_alone'] = {'capex_scale_2028_31': round(fc, 3), 'capex_path': [round(x, 1) for x in cx],
                              'capex_pct_rev_2031': det[4]['capex_pct_rev'], 'op_margin_2031': det[4]['op_margin'],
                              'fcf_2031': det[4]['fcf_after_sbc']}
    else:
        rev['capex_alone'] = {'note': 'zero capex after 2027 still does not reach the price'}
        d0, det0 = build('base', capex=[205, 0.01, 0.01, 0.01, 0.01])
        rev['capex_alone']['value_at_capex_0_after_2027'] = value_today(p, d0)
    # 4 exit P/E alone (DCF half unchanged)
    pe = solve(lambda x: value_today(p, dict(base_d, pe=x)), 5, 120)
    rev['exit_pe_alone'] = {'pe_on_2032_op_eps': round(pe, 1) if pe else None}
    # multiple method alone
    def mult_today(pe_):
        r = lm.run_scenario(p, dict(base_d, pe=pe_))
        return r['multiple_today']
    pe_m = solve(mult_today, 5, 120)
    rev['exit_pe_multiple_method_alone'] = round(pe_m, 1) if pe_m else None
    # 5 beta alone
    # a flat beta (today and mature alike): the starting beta alone cannot do it because the terminal uses the mature beta
    b = solve(lambda x: -value_today(dict(p, beta=x, beta_mature=x), base_d), 0.3, 3.0, target=-PRICE)
    rev['flat_beta_alone'] = {'beta_today_and_mature': round(b, 3) if b is not None else None,
                              'cost_of_equity': round(RF + b * ERP, 4) if b is not None else None,
                              'note': 'the starting beta alone (mature 1.05 kept) cannot reach the price even at 0'}
    # 6 terminal ROIC alone
    def v_roic(x):
        global ROIC_T
        old = ROIC_T
        ROIC_T = x
        d, _ = build('base')
        v = value_today(dict(p, roic_terminal=x), d)
        ROIC_T = old
        return v
    ro = solve(v_roic, 0.06, 2.0)
    rev['terminal_roic_alone'] = round(ro, 3) if ro else 'not reachable below 200%'

    # ---------- the market's story: consensus revenue and margins ----------
    cons_rev = [312.4, 372.0, 434.2, 486.3, 486.3 * 1.10]      # CONSENSUS AV/FMP 2026-11-05 (2031 = +10% ESTIMATE)
    cons_g = growth_from_levels(cons_rev, 0.09)
    story = {}
    for cx_name, cx in {'base_capex': SCEN['base']['capex'], 'street_capex_flat_225': [225, 225, 225, 225, 225],
                        'capex_falls_to_35pct': [205, 215, 200, 185, 187]}.items():
        # consensus EBIT margin ~35-36.8% (FMP-derived, evidence_consensus.md); solve EBITDA that gives 35% op margin avg
        def v_cons(eb_add):
            d, det = build('base', rev_growth=cons_g, capex=cx, ebitda=[e + eb_add for e in SCEN['base']['ebitda']])
            return value_today(p, d), det
        vals = {}
        for add in (0.0, 0.03, 0.06):
            v, det = v_cons(add)
            vals[f'ebitda_+{int(add*100)}pts'] = {'value_today': v,
                                                   'op_margin_2027_2031': [x['op_margin'] for x in det[:5]],
                                                   'capex_pct_rev_2031': det[4]['capex_pct_rev']}
        story[cx_name] = vals
    for cx_name, cx in {'base_capex': SCEN['base']['capex'], 'capex_falls_to_35pct': [205, 215, 200, 185, 187]}.items():
        add = solve(lambda x: value_today(p, build('base', rev_growth=cons_g, capex=cx,
                                                    ebitda=[e + x for e in SCEN['base']['ebitda']])[0]), -0.1, 0.3)
        d, det = build('base', rev_growth=cons_g, capex=cx, ebitda=[e + add for e in SCEN['base']['ebitda']])
        story['solve_' + cx_name] = {'ebitda_add_pts': round(add * 100, 2),
                                     'ebitda_2027_2031': [round(e + add, 4) for e in SCEN['base']['ebitda'][:5]],
                                     'op_margin_2027_2031': [x['op_margin'] for x in det[:5]],
                                     'revenue_2031': det[4]['revenue']}
    # same story with the EBITDA gain ramped in (2027 at base, linear to 2031): closer to how operating leverage arrives
    ramp = lambda x: [e + x * min(i, 4) / 4 for i, e in enumerate(SCEN['base']['ebitda'])]   # noqa: E731
    add = solve(lambda x: value_today(p, build('base', rev_growth=cons_g, ebitda=ramp(x))[0]), -0.1, 0.5)
    d, det = build('base', rev_growth=cons_g, ebitda=ramp(add))
    story['solve_base_capex_ramped'] = {'ebitda_2031_needed': round(SCEN['base']['ebitda'][4] + add, 4),
                                        'ebitda_2027_2031': [round(x, 4) for x in ramp(add)[:5]],
                                        'op_margin_2027_2031': [x['op_margin'] for x in det[:5]],
                                        'revenue_2031': det[4]['revenue']}
    # consensus EPS x P/E check (evidence_consensus.md): 2030 EPS $55.82 at 18.7x
    story['consensus_eps_check'] = {'eps_2030_consensus': 55.82, 'at_18.7x': round(55.82 * 18.7, 0),
                                    'at_15x': round(55.82 * 15, 0),
                                    'note': 'consensus per-share figures mostly predate the forward, preferred and ATM'}

    # ---------- grid: revenue CAGR x EBITDA margin (base capex) ----------
    grid = []
    for scale in (0.55, 0.8, 1.0, 1.2, 1.4, 1.6):
        for add in (-0.03, 0.0, 0.03, 0.06, 0.09):
            d, det = build('base', rev_scale=scale, ebitda=[e + add for e in SCEN['base']['ebitda']])
            r31 = det[4]['revenue']
            grid.append({'rev_2031': r31, 'cagr': round((r31 / REV0) ** 0.2 - 1, 4),
                         'ebitda_2031': round(SCEN['base']['ebitda'][4] + add, 3), 'op_margin_2031': det[4]['op_margin'],
                         'value_today': value_today(p, d)})

    # ---------- base rates for each scenario's revenue ----------
    brates = {}
    for k in SCEN:
        lv = REV_PATHS[k][0][-1]
        c = (lv / REV0) ** 0.2 - 1
        lab, sh = bands_share_real(c)
        brates[k] = {'rev_2031': lv, 'cagr': round(c, 4), 'class': lab, 'share_reaching_5y_real': round(sh, 4)}

    # ---------- implied returns on the build ----------
    roic = {}
    for k in SCEN:
        det = details[k]
        npe_2026 = 253.2 + 54.2 - 8.5            # net PP&E end-2026 ESTIMATE (FACT 253.2 at 9/30 + Q4 capex - Q4 D&A)
        net_inv = sum(x['capex'] - x['da'] for x in det[:5])
        nopat26 = 92.3 * (1 - TAX_BOOK)
        roic[k] = {'net_ppe_2031': round(npe_2026 + net_inv, 0), 'nopat_2031': det[4]['nopat'],
                   'roic_on_net_ppe_2031': round(det[4]['nopat'] / (npe_2026 + net_inv), 3),
                   'incremental_roic_2026_31': round((det[4]['nopat'] - nopat26) / net_inv, 3) if net_inv > 0 else None,
                   'cum_fcf_after_sbc_2027_31': round(sum(x['fcf_after_sbc'] for x in det[:5]), 0)}

    # ---------- beta / mature-beta sensitivity of the base ----------
    sens = {}
    for b_, bm in ((1.68, 1.05), (1.40, 1.05), (1.25, 1.0), (1.40, 1.0), (1.68, 1.2)):
        r = lm.run_scenario(dict(p, beta=b_, beta_mature=bm), base_d)
        sens[f'beta {b_} -> mature {bm}'] = {'value_today': r['value_today'], 'ret_pct': r['annual_return_pct']}

    # calibrated lens with the D&A wave as its own line (CLAUDE.md 05.11.2026)
    cal_da = lm.calibrated_lens(dict(p, calibrated={'m': base_d['m'], 'why': 'base margins kept: D&A wave from the capex '
                                                    'vintage model (2026 FACT/GUIDANCE capex 140B, 2027 commitments)'}),
                                base_d)

    # ATM ($60B Class C, FACT fwp_1026: planned, no agreement, size/timing undetermined) - not in the base; effect if sold
    # in full at today's price: per-share value (V + 60) / (N + 60 / price)
    atm = {}
    for k in SCEN:
        n = sc[k]['shares_m'] / 1000
        v = res['scenarios'][k]['value_today'] * n
        atm[k] = round((v + 60) / (n + 60 / PRICE), 2)
    res['atm_if_sold_at_price'] = {'value_today_per_share': atm, 'new_shares_m': round(60 / PRICE * 1000, 1),
                                   'why_not_in_base': 'no agreement entered; the funding gap it would cover is already '
                                   'in the base as net debt at 5.8%; sold near $634 it is accretive in bear/base, '
                                   'dilutive in bull, about +-1%'}
    res['judgement'] = JUDGEMENT
    res['drivers'] = {'da_model': {'s_eq': S_EQ, 'lag_eq': LAG_EQ, 'fit_errors_2021_26': _FIT[3]},
                      'net_cash_2026_items_bn': NET_CASH_ITEMS, 'net_cash_2026_bn': NET_CASH_2026,
                      'opm_2026_ex_legal': round(OPM_2026, 4), 'ebitda_2026_ex_legal': round(EBITDA_2026, 4),
                      'detail': details}
    res['reverse'] = {'levers_alone': rev, 'market_story': story, 'grid_base_capex': grid}
    res['base_rates_revenue'] = brates
    res['returns_on_build'] = roic
    res['discount_rate_sensitivity'] = sens
    res['calibrated_lens_with_da_wave'] = cal_da
    json.dump(res, open(OUT + 'reverse_result.json', 'w'), indent=1)
    return res


if __name__ == '__main__':
    r = main()
    s = r['scenarios']
    for k in ('bear', 'base', 'bull'):
        print(k, s[k]['value_today'], s[k]['year_end_values'], s[k]['annual_return_pct'], 'dcf', s[k]['dcf_today'],
              'mult', s[k]['multiple_today'])
    print('weighted', r['weighted_value_today'], r['weighted_return_pct'], 'implied g', r['market_implied_growth_pct'])
    print('two lens', json.dumps(r['two_lens']))
    print('cal with DA', json.dumps(r['calibrated_lens_with_da_wave']))
    print(json.dumps(r['reverse']['levers_alone'], indent=1))
    print(json.dumps(r['reverse']['market_story'], indent=1))
    print(json.dumps(r['base_rates_revenue']))
    print(json.dumps(r['returns_on_build'], indent=1))
    print(json.dumps(r['discount_rate_sensitivity'], indent=1))
    print('fit', r['drivers']['da_model'], 'netcash', r['drivers']['net_cash_2026_bn'])
    for k in ('bear', 'base', 'bull'):
        for x in r['drivers']['detail'][k]:
            print(k, x)
