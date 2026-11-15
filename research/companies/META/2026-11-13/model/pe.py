"""META earnings power x multiple, as_of 2026-11-05 (deep3, method 'pe').

What it does
  For bear/base/bull it builds GAAP EPS 2027-2032 from: revenue (revenue lane), operating costs split into
  (a) D&A from a capex path through the capex->D&A model fitted on META's own 2018-2025 history (history lane),
  (b) incremental cost of the $355.73bn of leases not yet commenced, (c) Reality Labs loss, (d) all other costs growing
  at their own rate; then interest on the old and new notes and on cash, the 6.50% mandatory convertible preferred
  dividend, tax, and a share count with the forward sale, the preferred conversion, an at-the-market (ATM) program
  drawn only when the cash model needs it, RSU dilution and (late) buybacks. A small cash model keeps interest and
  issuance consistent. End-2031 value = exit P/E x 2032 EPS (forward multiple, the same convention as the realized
  forward P/E history). Value today = that value plus the dividends, discounted at the cost of equity.
  All $ in billions, shares in millions, per-share numbers on today's pre-Class-C-dividend basis (the 1-for-1 Class C
  dividend effective 19.1.2027 halves every per-share number and every share-count-based price; it changes no value).

Labels: FACT (file:line or URL), GUIDANCE, CONSENSUS, ESTIMATE (ours), MEMORY (unverified). Run:
  python3 pe.py            -> prints the tables and writes pe_result.json next to this file.
"""
import json, os, copy

HERE = os.path.dirname(os.path.abspath(__file__))
AS_OF = '2026-11-05'
PRICE = 633.80                       # FACT IBKR 09:15 UTC 2026-11-05 (task brief)
YEARS_TO_END31 = 5.15                # 2026-11-05 -> 2031-12-31
RF, ERP = 0.0494, 0.042              # common inputs (task brief)
BETA = 1.40                          # ESTIMATE, see beta.json: 5y weekly 1.37, 5y monthly 1.26, 2y weekly 1.65 (Blume 1.44), 1y weekly 2.23
KE = RF + BETA * ERP                 # 10.82%
INDEX_RETURN = 0.091                 # rf + ERP (task brief)
BANDS = dict(buy=0.12, hold=0.06, lagging=0.03)

# ---------------- history (FACT, SEC companyfacts via hist.json; capex incl. finance-lease principal) ----------------
CAPEX_HIST = {2018: 13.915, 2019: 15.654, 2020: 15.767, 2021: 19.367, 2022: 32.036, 2023: 28.103, 2024: 39.225, 2025: 72.215}
CAPEX_2026 = 140.0                   # GUIDANCE $135-145bn incl. finance leases (R3Q26:65); 9M FACT 85.82
# capex->D&A model (history lane, analysis.json delayed_grid): D&A_t = c * sum_j w_j(L) capex_{t-d-j}, w = mid-year straight line
DA_MODELS = {'L6_d0': (6, 0, 0.6884618269771555),   # RMSE 18% on 2018-25; over-predicts 2026E by ~8%
             'L6_d1': (6, 1, 0.8958360146189845),   # RMSE 24%; one year in construction; under-predicts 2026E by ~6%
             'L4_d0': (4, 0, 0.5564480423291566),   # RMSE 15%
             'L8_d0': (8, 0, 0.8452320751829109)}   # RMSE 20%
DA_CENTRAL = (('L6_d0', 0.75), ('L6_d1', 0.25))       # 2026 29.7 vs 27.5-28.4 estimated; 2031-32 sits mid of the vintage build (asset_da.py)

# ---------------- 2026E base year (ex the $9.89bn + $2.40bn legal charges and $1.18bn severance) ----------------
REV26 = 257.0          # 9M FACT 181.712 (R3Q26) + Q4 ~75.3 (GUIDANCE $73-76bn, beat record) -- revenue lane
OPINC26 = 93.5         # REV26 - (total-expense GUIDANCE mid 177 - 13.47 charges) ; 36.4% (ex-Q3-charge only: 35.0%)
DA26 = 28.4            # 9M FACT 19.509 + Q4 at ~12% of revenue (ESTIMATE, history lane)
RL_LOSS26 = 18.5       # GUIDANCE $18-19bn (R3Q26:63); 9M FACT 12.775
RL_DA = 1.5            # ESTIMATE: RL's own D&A inside its loss (avoid double count with company D&A)
OTHER26 = REV26 - OPINC26 - DA26 - (RL_LOSS26 - RL_DA)   # all other costs 2026E = 118.1 (was ~81 in 2025: +45%)
SBC26 = 28.5           # 9M FACT 21.053 + Q4 ~7.4
EPS26_EX = (OPINC26 - 1.5) * (1 - 0.16) / 2.57   # ESTIMATE 2026 EPS ex charges and ex the Q1 tax benefit, ~30.1

# ---------------- leases not yet commenced (FACT Q3_10Q:748: $355.73bn, commencing 2026-2036, terms >1 to 30 years) --------
LEASE_TOTAL, LEASE_TERM = 355.73, 17.0                      # ESTIMATE average term 17 years -> ~$20.9bn a year at full run
LEASE_PHASE = {2027: .15, 2028: .35, 2029: .60, 2030: .80, 2031: .92, 2032: 1.0, 2033: 1.0, 2034: 1.0}   # ESTIMATE share commenced (Hyperion 2029, Texas 2028)

# ---------------- balance sheet and financing ----------------
CASH_0930 = 82.449     # FACT cash 11.222 + marketable 71.227 (R3Q26:48); restricted 13.13 excluded
ESCROW = 10.80         # FACT restricted MMF, released 2028-2030 on satisfying purchase obligations (Q3_10Q:1416 area)
DEBT_OLD = 84.0        # FACT face (Q3_10Q:702-720); net interest after capitalization Q3'26 $882m (Q3_10Q:727)
OLD_INT = 0.882 * 4    # $3.53bn a year net of capitalized interest (FACT run-rate)
USD_NOTES = [(3.25, .0585), (3.25, .0625), (4.0, .0655), (1.75, .0735), (2.75, .0750)]   # FACT fwp_1103 (settled 5.11.2026)
EURUSD = 1.1241        # FACT FMP forex quote 2026-11-05
EUR_NOTES = [(1.75, .0430), (1.25, .0465), (1.0, .0490), (1.75, .0540), (1.75, .0605), (1.5, .0640)]  # FACT fwp_1104, EUR bn
NEW_DEBT = sum(a for a, _ in USD_NOTES) + EURUSD * sum(a for a, _ in EUR_NOTES)              # 25.12
NEW_INT = sum(a * c for a, c in USD_NOTES) + EURUSD * sum(a * c for a, c in EUR_NOTES)      # 1.53 a year
CAP_INT = 0.15         # ESTIMATE share of new-note interest capitalized while CIP is large (Q3: ~14% on old notes)
PREF = 12.5            # FACT $12.5bn 6.50% mandatory convertible preferred (final_1028:17-23); dividends $812.5m a year
PREF_DIV = {2027: 0.8125, 2028: 0.8125, 2029: 0.8125 * 0.75}  # payable Feb/May/Aug/Nov 2027 ... Aug 2029 (final_1028:21-23)
PREF_MIN_SH, PREF_MAX_SH = 12.5 * 1.3620, 12.5 * 1.6000       # FACT 17.03-20.00m Class A shares (final_1028:360)
PREF_LO, PREF_HI = 625.0, 734.2135                             # initial price, threshold appreciation price (final_1028:326,388)
FWD_SH, FWD_CASH = 40.0, 40.0 * 0.62291875                    # FACT 40m shares at forward price $622.91875 (fwd_final_1028:59)
ATM_MAX = 60.0         # FACT plan, not yet a contract, from Q1 2027 (fwp_1026:30, 90)
SH_DILUTED_0 = 2573.0  # FACT Q3'26 diluted weighted shares (R3Q26); basic outstanding 2,556.2m at 16.10.2026
DPS26 = 1.361 * 4 / 2.552   # FACT dividends $1.361bn in Q3 / 2,552m basic -> ~$2.13 a year
Q4_OCF, Q4_CAPEX = 37.0, CAPEX_2026 - 85.82     # ESTIMATE (Q4'25 OCF 36.2 FACT); capex GUIDANCE mid minus 9M FACT
CASH_YIELD, NEW_DEBT_RATE = 0.040, 0.060        # ESTIMATE
CASH_FLOOR, CASH_CEIL = 60.0, 110.0             # ESTIMATE: ATM/debt below the floor, buybacks above the ceiling
AG_CASH = 1.27         # FACT ~$12.7bn payable in annual installments over 10 years (Q3_10Q note 10)
WITHHOLD = 0.55        # ESTIMATE cash for net share settlement as share of SBC (9M26 FACT 12.77 / 21.05 = 0.61)

SCEN = {
    # lists run 2027..2034; 2033-34 only feed the forward-growth check behind the exit multiple (exit = end-2031 on 2032 EPS)
    'bear': dict(
        rev=[289.0, 301.5, 329.7, 354.2, 376.8], g_after=[0.055, 0.05, 0.045],      # revenue lane bear (8.0% CAGR 26-31)
        capex=[200, 165, 140, 130, 130, 135, 140, 145],                            # 2027 held by $121.4bn 2027 commitments; 2023-style cut after
        g_other=[0.16, 0.05, 0.06, 0.06, 0.05, 0.05, 0.05, 0.05],                  # efficiency years like 2023; SBC sticky
        rl_loss=[15, 15, 14, 14, 13, 13, 12, 12],                                   # 2027 GUIDANCE ~15, no further progress
        tax=0.18, sbc_pct=[0.115, 0.115, 0.11, 0.11, 0.105, 0.105, 0.10, 0.10],
        one_off={2027: 5.3},                                                        # FACT contingent AG amount not accrued
        pe_exit=15.0, px_issue=520.0, px_growth=0.03, dps_g=0.0),
    'base': dict(
        rev=[309.8, 359.8, 408.5, 455.2, 501.9], g_after=[0.09, 0.08, 0.07],        # revenue lane base (14.3% CAGR 26-31)
        capex=[215, 210, 190, 175, 170, 175, 180, 185],                            # 2027 = Q4'26 implied run-rate ~$217bn/yr; ~34% of revenue by 2031
        g_other=[0.18, 0.13, 0.10, 0.09, 0.08, 0.07, 0.065, 0.06],
        rl_loss=[15, 14, 12.5, 11, 10, 9, 8, 7],
        tax=0.16, sbc_pct=[0.11, 0.1075, 0.105, 0.1025, 0.10, 0.10, 0.0975, 0.095],
        one_off={}, pe_exit=20.0, px_issue=680.0, px_growth=0.08, dps_g=0.05),
    'bull': dict(
        rev=[326.0, 396.3, 470.3, 548.4, 628.4], g_after=[0.125, 0.11, 0.10],       # revenue lane bull (19.6% CAGR 26-31)
        capex=[230, 250, 255, 260, 265, 275, 285, 295],
        g_other=[0.21, 0.16, 0.13, 0.12, 0.11, 0.10, 0.09, 0.085],
        rl_loss=[15, 12, 9, 6, 4, 3, 2, 1],
        tax=0.15, sbc_pct=[0.11, 0.105, 0.10, 0.10, 0.095, 0.095, 0.09, 0.09],
        one_off={}, pe_exit=24.0, px_issue=780.0, px_growth=0.12, dps_g=0.08),
}
PROB = dict(bear=0.25, base=0.50, bull=0.25)    # subjective (Claude's judgement), not engine odds
YEARS = list(range(2027, 2035))


def weights(L):
    w = [1.0 / L] * (L + 1); w[0] = w[-1] = 0.5 / L; return w


def da_model(capex, y, name):
    L, d, c = DA_MODELS[name]
    return c * sum(wj * capex[y - d - j] for j, wj in enumerate(weights(L)))


def da_path(capex_future, blend=DA_CENTRAL):
    cap = dict(CAPEX_HIST); cap[2026] = CAPEX_2026
    cap.update(dict(zip(YEARS, capex_future)))
    return {y: sum(w * da_model(cap, y, n) for n, w in blend) for y in [2026] + YEARS}


def run(name, s, da_blend=DA_CENTRAL, atm=True, pe_exit=None, beta=BETA, margin_floor=None, lease_term=LEASE_TERM, da_scale=1.0):
    ke = RF + beta * ERP
    rev = dict(zip(YEARS[:5], s['rev']))
    for k, g in enumerate(s['g_after']):
        rev[2032 + k] = rev[2031 + k] * (1 + g)
    da = {y: v * (da_scale if y >= 2027 else 1.0) for y, v in da_path(s['capex'], da_blend).items()}
    cap = dict(zip(YEARS, s['capex']))
    # ---- Q4 2026 cash roll-forward (pref closed 29-30.10, USD notes 5.11, EUR notes 12.11) ----
    cash = CASH_0930 + Q4_OCF - Q4_CAPEX - 1.37 - WITHHOLD * 7.4 + (PREF - 0.25) + NEW_DEBT * 0.995
    debt_new_draw = 0.0
    sh = SH_DILUTED_0
    other = OTHER26
    out = {}
    atm_used, atm_sh = 0.0, 0.0
    pref_sh = 0.0
    dps = DPS26
    for i, y in enumerate(YEARS):
        other *= 1 + s['g_other'][i]
        lease = LEASE_TOTAL / lease_term * LEASE_PHASE[y]
        rlx = s['rl_loss'][i] - RL_DA
        opinc = rev[y] - da[y] - other - lease - rlx
        margin = opinc / rev[y]
        if margin_floor is not None and margin < margin_floor:      # calibrated lens: margin not below the first forecast year
            margin = margin_floor; opinc = margin * rev[y]
        one = s['one_off'].get(y, 0.0)
        cap_int = CAP_INT if y <= 2029 else 0.0
        interest = OLD_INT + NEW_INT * (1 - cap_int) + debt_new_draw * NEW_DEBT_RATE
        int_inc = cash * CASH_YIELD
        pretax = opinc - one + int_inc - interest
        ni = pretax * (1 - s['tax'])
        pref_div = PREF_DIV.get(y, 0.0)
        ni_common = ni - pref_div
        sbc = s['sbc_pct'][i] * rev[y]
        px = s['px_issue'] * (1 + s['px_growth']) ** (y - 2027)     # ESTIMATE price at which shares are issued / bought
        # ---- shares (start-of-year count -> end-of-year count; EPS on the average) ----
        sh_start = sh
        sh += (1 - WITHHOLD) * sbc / px * 1000                       # net RSU shares (bn/$ -> m)
        fwd_add = FWD_SH if y == 2027 else 0.0                       # forward settled by 19.11.2027; assume mid-2027
        sh += fwd_add
        conv = 0.0
        if y == 2029:                                                # mandatory conversion ~15.8.2029
            p29 = px
            conv = PREF_MAX_SH if p29 <= PREF_LO else PREF_MIN_SH if p29 >= PREF_HI else PREF / p29 * 1000
            pref_sh = conv; sh += conv
        # ---- cash ----
        ocf = ni + da[y] + sbc                                       # one-off already reduces ni; paid in the same year (bear)
        fcf = ocf - cap[y]
        divs = dps * (sh_start + sh) / 2 / 1000
        flows = fcf - divs - WITHHOLD * sbc - AG_CASH - pref_div
        flows += FWD_CASH if y == 2027 else 0.0
        flows += ESCROW / 3 if y in (2028, 2029, 2030) else 0.0
        cash += flows
        buyback = 0.0
        if cash < CASH_FLOOR:
            need = CASH_FLOOR - cash
            take = min(need, ATM_MAX - atm_used) if atm else 0.0
            if take > 0:
                atm_used += take; new = take / px * 1000; atm_sh += new; sh += new; cash += take
                need -= take
            if need > 0:
                debt_new_draw += need; cash += need
        elif cash > CASH_CEIL:
            if debt_new_draw > 0:                                     # repay the extra debt first
                rep = min(debt_new_draw, cash - CASH_CEIL); debt_new_draw -= rep; cash -= rep
            if cash > CASH_CEIL:
                buyback = cash - CASH_CEIL; sh -= buyback / px * 1000; cash = CASH_CEIL
        w_sh = (sh_start + sh) / 2                                   # forward mid-2027, conversion Aug 2029: ~half-year weights
        eps = ni_common / w_sh * 1000
        out[y] = dict(revenue=rev[y], growth=rev[y] / (rev[y - 1] if y > 2027 else REV26) - 1, capex=cap[y],
                      capex_pct=cap[y] / rev[y], da=da[y], da_pct=da[y] / rev[y], other_costs=other, other_pct=other / rev[y],
                      lease_incr=lease, rl_loss=s['rl_loss'][i], op_income=opinc, op_margin=margin,
                      pre_da_margin=(opinc + da[y]) / rev[y], one_off=one, interest_exp=interest, interest_inc=int_inc,
                      tax=s['tax'], net_income=ni, pref_div=pref_div, ni_common=ni_common, sbc=sbc, shares_end=sh,
                      shares_wavg=w_sh, eps=eps, ocf=ocf, fcf=fcf, dividends=divs, dps=dps, cash_end=cash,
                      extra_debt=debt_new_draw, atm_cum=atm_used, atm_shares_cum=atm_sh, pref_conv_shares=conv,
                      buyback=buyback, issue_px=px)
        dps *= 1 + s['dps_g']
    # ---- value ----
    pe = pe_exit if pe_exit is not None else s['pe_exit']
    v31 = pe * out[2032]['eps']
    # dividends a holder of one share today receives: Q4'26 payment + 2027-2031 DPS
    divs_ps = {2026: DPS26 / 4}
    for y in range(2027, 2032):
        divs_ps[y] = out[y]['dps']
    t_of = {2026: 0.12, 2027: 0.65, 2028: 1.65, 2029: 2.65, 2030: 3.65, 2031: 4.65}   # mid-year timing from as_of
    pv_div = sum(d / (1 + ke) ** t_of[y] for y, d in divs_ps.items())
    v_today = v31 / (1 + ke) ** YEARS_TO_END31 + pv_div
    # year-end conditional values on the path: value at end of year t of (v31 + dividends after t)
    ye = {}
    for t in range(2027, 2032):
        yrs = 2031 - t
        ye[t] = v31 / (1 + ke) ** yrs + sum(divs_ps[y] / (1 + ke) ** (y - t - 0.5) for y in range(t + 1, 2032))
    ye_pe = {t: pe * out[t + 1]['eps'] for t in range(2027, 2032)}   # cross-check: exit multiple on next-year EPS
    fwd_g = (out[2034]['eps'] / out[2032]['eps']) ** 0.5 - 1 if out[2032]['eps'] > 0 and out[2034]['eps'] > 0 else None   # EPS growth after the exit
    total_divs = sum(divs_ps.values())
    gross = (v31 + total_divs) / PRICE
    ret = gross ** (1 / YEARS_TO_END31) - 1 if gross > 0 else -1.0
    if out[2032]['eps'] <= 0 or EPS26_EX <= 0:
        pass
    return dict(scenario=name, ke=ke, pe_exit=pe, path=out, v_end2031=v31, v_today=v_today, year_end_values=ye,
                year_end_values_pe_on_next_eps=ye_pe, dividends_per_share=divs_ps, pv_dividends=pv_div,
                annual_return_to_end2031=ret, eps_cagr_2032_2034=fwd_g if isinstance(fwd_g, float) else None, eps_cagr_2026_2032=(out[2032]['eps'] / EPS26_EX) ** (1 / 6) - 1, implied_pe_today_on_2027_eps=PRICE / out[2027]['eps'],
                implied_pe_today_on_2028_eps=PRICE / out[2028]['eps'])


def band(r):
    return 'buy' if r >= BANDS['buy'] else 'hold' if r >= BANDS['hold'] else 'lagging' if r >= BANDS['lagging'] else 'sell'


def main():
    res = {k: run(k, v) for k, v in SCEN.items()}
    base = SCEN['base']
    # ---- calibrated lens (owner's two-lens rule): today's forward P/E held, margin not below the first forecast year ----
    b0 = res['base']
    pe_today = b0['implied_pe_today_on_2027_eps']
    cal_full = run('base_calibrated', base, pe_exit=pe_today, margin_floor=b0['path'][2027]['op_margin'])
    cal_pe_only = run('base_calibrated_pe_only', base, pe_exit=pe_today)     # evidenced D&A/lease costs kept (CLAUDE.md: own line)
    # ---- sensitivities on the base ----
    sens = {}
    for n in ('L4_d0', 'L6_d0', 'L6_d1', 'L8_d0'):
        r = run('base', base, da_blend=((n, 1.0),)); sens[f'da_model_{n}'] = (r['v_today'], r['annual_return_to_end2031'], r['path'][2031]['op_margin'])
    for pe in (15, 17, 18.4, 23, 25):
        r = run('base', base, pe_exit=pe); sens[f'pe_exit_{pe}'] = (r['v_today'], r['annual_return_to_end2031'], None)
    for bt in (1.25, 1.68):
        r = run('base', base, beta=bt); sens[f'beta_{bt}'] = (r['v_today'], r['annual_return_to_end2031'], None)
    r = run('base', base, atm=False); sens['no_atm_debt_instead'] = (r['v_today'], r['annual_return_to_end2031'], r['path'][2031]['op_margin'])
    for lt in (12.0, 25.0):
        r = run('base', base, lease_term=lt); sens[f'lease_term_{int(lt)}y'] = (r['v_today'], r['annual_return_to_end2031'], r['path'][2031]['op_margin'])
    for dg in (-0.02, 0.02):
        s2 = copy.deepcopy(base); s2['g_other'] = [g + dg for g in s2['g_other']]
        r = run('base', s2); sens[f'other_cost_growth_{dg:+.0%}_per_year'] = (r['v_today'], r['annual_return_to_end2031'], r['path'][2031]['op_margin'])
    s2 = copy.deepcopy(base); s2['capex'] = [200, 180, 155, 140, 135, 140, 145, 150]
    r = run('base', s2); sens['capex_falls_after_2027_(195->145)'] = (r['v_today'], r['annual_return_to_end2031'], r['path'][2031]['op_margin'])
    s2 = copy.deepcopy(base); s2['capex'] = [230, 240, 240, 240, 240, 245, 250, 255]
    r = run('base', s2); sens['capex_stays_240'] = (r['v_today'], r['annual_return_to_end2031'], r['path'][2031]['op_margin'])
    for k in (0.9, 1.1):
        r = run('base', base, da_scale=k); sens[f'da_x{k}'] = (r['v_today'], r['annual_return_to_end2031'], r['path'][2031]['op_margin'])

    pw_today = sum(PROB[k] * res[k]['v_today'] for k in res)
    pw_31 = sum(PROB[k] * res[k]['v_end2031'] for k in res)
    pw_ret = ((pw_31 + sum(PROB[k] * sum(res[k]['dividends_per_share'].values()) for k in res)) / PRICE) ** (1 / YEARS_TO_END31) - 1
    out = dict(
        as_of=AS_OF, price=PRICE, method='earnings power x exit P/E (forward, on 2032 GAAP EPS at end-2031), discounted at ke',
        ke=KE, beta=BETA, rf=RF, erp=ERP, years_to_end2031=YEARS_TO_END31, probabilities=PROB,
        base_year_2026E=dict(revenue=REV26, op_income_ex_charges=OPINC26, op_margin=OPINC26 / REV26, da=DA26,
                             da_model_2026=da_path(base['capex'])[2026], rl_loss=RL_LOSS26, other_costs=OTHER26, sbc=SBC26),
        financing=dict(new_notes_usd_equiv=NEW_DEBT, new_notes_interest=NEW_INT, pref=PREF, forward_shares=FWD_SH,
                       forward_cash=FWD_CASH, atm_max=ATM_MAX, eurusd=EURUSD),
        scenarios={k: dict(v_today=v['v_today'], v_end2031=v['v_end2031'], annual_return=v['annual_return_to_end2031'],
                           band=band(v['annual_return_to_end2031']), pe_exit=v['pe_exit'], ke=v['ke'],
                           year_end_values=v['year_end_values'], year_end_values_pe_on_next_eps=v['year_end_values_pe_on_next_eps'],
                           implied_pe_today_on_2027_eps=v['implied_pe_today_on_2027_eps'], eps_cagr_2026_2032=v['eps_cagr_2026_2032'],
                           eps_cagr_2032_2034=v['eps_cagr_2032_2034'],
                           implied_pe_today_on_2028_eps=v['implied_pe_today_on_2028_eps'],
                           dividends_per_share=v['dividends_per_share'], path=v['path']) for k, v in res.items()},
        probability_weighted=dict(v_today=pw_today, v_end2031=pw_31, annual_return=pw_ret, band=band(pw_ret)),
        calibrated_lens=dict(pe_today_on_our_2027_eps=pe_today,
                             full=dict(v_today=cal_full['v_today'], v_end2031=cal_full['v_end2031'],
                                       annual_return=cal_full['annual_return_to_end2031'], band=band(cal_full['annual_return_to_end2031']),
                                       margins={y: cal_full['path'][y]['op_margin'] for y in YEARS}),
                             pe_only_costs_kept=dict(v_today=cal_pe_only['v_today'], v_end2031=cal_pe_only['v_end2031'],
                                                     annual_return=cal_pe_only['annual_return_to_end2031'],
                                                     band=band(cal_pe_only['annual_return_to_end2031']))),
        sensitivities_base={k: dict(v_today=v[0], annual_return=v[1], op_margin_2031=v[2]) for k, v in sens.items()},
    )
    # ---- what the price implies, and the street ----
    lo, hi = 5.0, 60.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if run('base', base, pe_exit=mid)['v_today'] < PRICE: lo = mid
        else: hi = mid
    pe_for_12 = None
    lo2, hi2 = 5.0, 80.0
    for _ in range(60):
        mid = (lo2 + hi2) / 2
        if run('base', base, pe_exit=mid)['annual_return_to_end2031'] < BANDS['buy']: lo2 = mid
        else: hi2 = mid
    pe_for_12 = hi2
    cons_eps = {2026: 29.12, 2027: 33.93, 2028: 40.03, 2029: 49.14, 2030: 55.82}   # CONSENSUS AV (2026, 2027) / FMP (2028-30), 2026-11-05
    cons_margin = {2026: .374, 2027: .368, 2028: .368, 2029: .362, 2030: .367}      # ESTIMATE from FMP EBIT / revenue
    out['market_and_street'] = dict(
        exit_pe_that_makes_base_value_equal_price=lo, exit_pe_for_12pct_a_year_on_base_costs=pe_for_12,
        consensus_eps=cons_eps, consensus_op_margin=cons_margin,
        our_eps={k: {y: res[k]['path'][y]['eps'] for y in range(2027, 2031)} for k in res},
        our_op_margin={k: {y: res[k]['path'][y]['op_margin'] for y in range(2027, 2031)} for k in res},
        note='Consensus 2030 EPS (55.8) is about our bull path (57.5); it keeps the margin near 36-37% with D&A only 14.5% of '
             'revenue, which needs capex far below $190bn+ (our base D&A 2030 = 24% of revenue at capex 215/210/190/175).')
    b = out['scenarios']
    out['owner_rules'] = dict(
        base_band=b['base']['band'], calibrated_pe_only_band=out['calibrated_lens']['pe_only_costs_kept']['band'],
        lenses_agree=b['base']['band'] == out['calibrated_lens']['pe_only_costs_kept']['band'],
        buy_test=dict(base_ge_12=b['base']['annual_return'] >= 0.12, bull_ge_17=b['bull']['annual_return'] >= 0.17,
                      bear_ge_minus10=b['bear']['annual_return'] >= -0.10),
        atm_in_base='yes: the cash model draws ~$44bn of the $60bn plan in 2027-28 (FCF -$45bn in 2027 at $215bn capex, '
                    'dividends, RSU tax withholding) to keep $60bn cash; the alternative (debt at 6%) gives the same value '
                    '(sensitivity no_atm_debt_instead), so the ATM changes the share count, not the value')
    json.dump(out, open(os.path.join(HERE, 'pe_result.json'), 'w'), indent=1, default=float)
    print('exit P/E that equals the price:', round(lo, 1), ' exit P/E for 12%/yr:', round(pe_for_12, 1))
    # ---- print ----
    for k, v in res.items():
        print(f"\n== {k}: value today {v['v_today']:.0f}  end-2031 {v['v_end2031']:.0f}  return {v['annual_return_to_end2031']*100:.1f}%/yr "
              f"({band(v['annual_return_to_end2031'])})  exit {v['pe_exit']}x  EPS cagr 26-32 {v['eps_cagr_2026_2032']*100:.1f}% 32-34 {v['eps_cagr_2032_2034']*100:.1f}%  P/E today on our 2027 EPS {v['implied_pe_today_on_2027_eps']:.1f}x")
        print('year  rev   g     capex cx%   D&A%  other% lease  RL   margin preDA  NI    EPS    shares  cash  xdebt atm  bb   fcf')
        for y, p in v['path'].items():
            print(f"{y} {p['revenue']:6.1f} {p['growth']*100:4.1f} {p['capex']:5.0f} {p['capex_pct']*100:4.0f} {p['da_pct']*100:5.1f} {p['other_pct']*100:5.1f} "
                  f"{p['lease_incr']:5.1f} {p['rl_loss']:4.1f} {p['op_margin']*100:5.1f} {p['pre_da_margin']*100:5.1f} {p['net_income']:5.1f} "
                  f"{p['eps']:6.2f} {p['shares_wavg']:7.0f} {p['cash_end']:5.0f} {p['extra_debt']:5.0f} {p['atm_cum']:4.0f} {p['buyback']:4.0f} {p['fcf']:5.0f}")
        print('year-end values:', {t: round(x) for t, x in v['year_end_values'].items()}, ' P/E path:', {t: round(x) for t, x in v['year_end_values_pe_on_next_eps'].items()})
    print(f"\nprob-weighted: today {pw_today:.0f}, 2031 {pw_31:.0f}, return {pw_ret*100:.1f}%/yr")
    c = out['calibrated_lens']
    print(f"calibrated lens (P/E {c['pe_today_on_our_2027_eps']:.1f}x held, margin floor): today {c['full']['v_today']:.0f}, 2031 {c['full']['v_end2031']:.0f}, "
          f"{c['full']['annual_return']*100:.1f}%/yr ({c['full']['band']}); P/E only: {c['pe_only_costs_kept']['v_today']:.0f}, {c['pe_only_costs_kept']['annual_return']*100:.1f}%/yr")
    for k, v in out['sensitivities_base'].items():
        print(f"  {k:40s} today {v['v_today']:5.0f}  {v['annual_return']*100:5.1f}%/yr  " + (f"margin31 {v['op_margin_2031']*100:.1f}%" if v['op_margin_2031'] else ''))


if __name__ == '__main__':
    main()
