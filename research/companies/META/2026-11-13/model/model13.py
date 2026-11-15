"""META update 2026-11-13 on the 05.11 deep3 baseline (stable-valuation rule).

Step 0 reruns the three corrected lanes exactly as deep3 (DCF red-team corrected, P/E verifier part 2, reverse + net-cash
correction) and the deep3 synthesis -> must give base ~$508. Then applies, one at a time, only the changes a new fact or a
locked rule justifies, with the $ effect of each:
  C1 beta = locked rule (2y weekly vs SPY, adjusted 0.67*raw+0.33 = 1.434; market.md mkt_beta.json) in all three lanes
  C2 exit P/E = locked rule (own long-run forward P/E median 20.35x, 10y, market.md mkt_fwdpe_adj.json) for the base exit of
     the P/E lane (was 20.0x) and the reverse lane's multiple half (was 18.0x); bear/bull multiples kept (scenario judgement)
  C3 New Mexico UPA judgment (FACT Q3 10-Q Note 10: $25B civil penalty 21.10.2026, 15% post-judgment interest, appeal) as a
     non-deductible one-off claim paid ~mid-2028: base $8B expected, bear $25B*1.15^2 = $33.1B + DC AG $1.7B, bull $2B
  C4 value date 13.11.2026, price $634.68 (Yahoo 14:58 UTC; IBKR 502), 2027 consensus EPS 33.97
Then the two lenses, the depreciation-wave line, and the price ladder. Pure arithmetic; ESTIMATE unless stated.
"""
import copy, json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
# research copy: the two path lines below replace the run folder's absolute paths (nothing else in this file changed)
ROOT = os.environ.get('FE_ROOT') or os.path.abspath(os.path.join(HERE, '..', '..', '..', '..', '..'))
OUT_DIR = os.environ.get('META_RERUN_OUT') or tempfile.mkdtemp(prefix='meta_rerun_')   # never the saved result's folder
sys.path.insert(0, HERE); sys.path.insert(0, ROOT + '/scripts'); sys.path.insert(0, ROOT)
import dcf_redteam_model as d
import pe
import lean_model as lm

YEARS = [2027, 2028, 2029, 2030, 2031]
RF, ERP = 0.0494, 0.042
INDEX = 9.14
CUM_DIV = 12.3

# ---------------- lanes ----------------
DP = json.load(open(os.path.join(HERE, 'dcf_params.json')))
DAM = d.fit_da(DP, 6)
LTAX, JV = 20.521 / 1.05 ** 5, 11.547


def dcf_lane(beta):
    S = copy.deepcopy(DP['scenarios'])
    common = dict(tmode='cash', tv_mid=True, legal_pct=0.005, nonop_add=JV, beta=beta)
    bear = S['bear']; bear['capex_rev_after'] = [0.31 + (0.27 - 0.31) * i / 4 for i in range(5)]
    out = {}
    for k in ('bear', 'base', 'bull'):
        kk = dict(common, **({} if k == 'bear' else {'t0_claim': LTAX}))
        r = d.run(DP, k, S[k], DAM, **kk)
        out[k] = {'today': r['value_today'], 'ye': {y: r['year_end_values'][y] for y in YEARS},
                  'eps32': r['eps_book'][2032], 'eps27': r['eps_book'][2027], 'ke0': r['ke_path'][0]}
    return out


def _jv_extra(y):
    tx = 6.165 / 4 * (0.5 if y == 2028 else (1 if y > 2028 else 0))
    la = 12.311 / 4 * (0.5 if y == 2029 else (1 if y > 2029 else 0))
    return tx + la - 18.476 / 17 * pe.LEASE_PHASE[y]


def pe_corr(name):
    add = dict(bear=0.0, base=0.05, bull=0.02)[name]
    s = copy.deepcopy(pe.SCEN[name]); s['g_other'][0] += add
    oo = dict(s['one_off'])
    s['one_off'] = {y: oo.get(y, 0.0) + _jv_extra(y) - 0.10 * (355.73 - 18.476) / 17 * pe.LEASE_PHASE[y] + 2.0 for y in pe.YEARS}
    return s


def pe_lane(beta, base_exit):
    out = {}
    for k in ('bear', 'base', 'bull'):
        r = pe.run(k, pe_corr(k), beta=beta, pe_exit=(base_exit if k == 'base' else None))
        out[k] = {'today': r['v_today'], 'ye': {y: r['year_end_values'][y] for y in YEARS},
                  'eps32': r['path'][2032]['eps'], 'eps27': r['path'][2027]['eps'], 'm27': r['path'][2027]['op_margin'],
                  'ke0': r['ke']}
    return out


RP = json.load(open(os.path.join(HERE, 'reverse_params.json')))
RV0 = json.load(open(os.path.join(HERE, 'reverse_result.json')))['scenarios']
RV_CORR = {'bear': 205.87 - RV0['bear']['value_today'], 'base': 447.35 - RV0['base']['value_today'],
           'bull': 814.32 - RV0['bull']['value_today']}       # red-team net-cash corrections (escrow, JV stakes), deep3


def rev_lane(beta, base_pe):
    p = copy.deepcopy(RP); p['beta'] = beta
    out = {}
    for k in ('bear', 'base', 'bull'):
        sc = copy.deepcopy(p['scenarios'][k])
        if k == 'base':
            sc['pe'] = base_pe
        r = lm.run_scenario(p, sc)
        ke = r['ke0_pct'] / 100
        dlt = RV_CORR[k]
        out[k] = {'today': r['value_today'] + dlt,
                  'ye': {y: r['year_end_values'][y] + dlt * (1 + ke) ** (1.15 + i) for i, y in enumerate(YEARS)},
                  'eps32': r['eps_with_interest'][2032] if 2032 in r['eps_with_interest'] else r['eps_with_interest']['2032'],
                  'eps27': r['eps_with_interest'][2027] if 2027 in r['eps_with_interest'] else r['eps_with_interest']['2027'],
                  'ke0': ke}
    return out


W = {'dcf': 0.40, 'pe': 0.30, 'rev': 0.30}
PROB = {'bear': 0.25, 'base': 0.50, 'bull': 0.25}      # SUBJECTIVE (Claude's judgement), unchanged from deep3


def synth(cfg):
    lanes = {'dcf': dcf_lane(cfg['beta_dcf']), 'pe': pe_lane(cfg['beta_pe'], cfg['pe_exit_base']),
             'rev': rev_lane(cfg['beta_rev'], cfg['rev_pe_base'])}
    price, T = cfg['price'], cfg['T31']
    roll = cfg.get('roll_days', 0) / 365.0
    shares = 2556.24 + 40.0 + 21.3          # FACT basic + forward-sale shares + preferred conversion (DCF base)
    sc = {}
    for s in ('bear', 'base', 'bull'):
        ke_w = sum(W[k] * lanes[k][s]['ke0'] for k in W)
        today = sum(W[k] * lanes[k][s]['today'] for k in W) * (1 + ke_w) ** roll
        ye = {y: sum(W[k] * lanes[k][s]['ye'][y] for k in W) for y in YEARS}
        claim_b = cfg['claims'].get(s, 0.0)                         # $B nominal paid mid-2028
        t_pay = 1.63 if cfg.get('roll_days') else 1.65
        pv_ps = claim_b / (1 + ke_w) ** t_pay / shares * 1000
        today -= pv_ps
        t_ye = {y: (1.13 if cfg.get('roll_days') else 1.15) + i for i, y in enumerate(YEARS)}
        ye = {y: ye[y] - pv_ps * (1 + ke_w) ** t_ye[y] for y in YEARS}
        ret = (((ye[2031] + CUM_DIV) / price) ** (1 / T) - 1) * 100
        sc[s] = {'today': round(today, 1), 'ye': {y: round(v, 1) for y, v in ye.items()}, 'return_pct': round(ret, 2),
                 'claim_pv_ps': round(pv_ps, 2), 'claim_fv31_ps': round(pv_ps * (1 + ke_w) ** t_ye[2031], 2), 'ke_w': ke_w}
    return lanes, sc


def ret(v31, price, T):
    return (((v31 + CUM_DIV) / price) ** (1 / T) - 1) * 100


def price_at(v31, r, T):
    return (v31 + CUM_DIV) / (1 + r / 100) ** T


def band(r):
    return 'buy/add' if r >= 12 else 'hold' if r >= 6 else 'lagging' if r >= 3 else 'sell'


BASE_CFG = dict(beta_dcf=1.35, beta_pe=1.40, beta_rev=1.40, pe_exit_base=20.0, rev_pe_base=18.0, claims={},
                price=633.80, T31=5.15, cons_eps27=33.93)
BETA_LOCK = 1.434            # market.md: 2y weekly raw 1.648 -> 0.67*1.648+0.33
PE_LOCK = 20.35              # market.md: own 10y forward P/E median (adjusted EPS); deep3 analysis.json 2016-24: 20.4x
NM = {'bear': 25.0 * 1.15 ** 2 + 1.7, 'base': 8.0, 'bull': 2.0}
PRICE_NOW, T_NOW = 634.68, 5.13        # 13.11.2026 -> 31.12.2031 = 1509 days / 365.25 = 4.13 + 1 ... (5.13 years)

steps = [('baseline_rerun_deep3', {}),
         ('C1_beta_locked_1.434', dict(beta_dcf=BETA_LOCK, beta_pe=BETA_LOCK, beta_rev=BETA_LOCK)),
         ('C2a_pe_lane_exit_20.35x', dict(pe_exit_base=PE_LOCK)),
         ('C2b_reverse_multiple_20.35x', dict(rev_pe_base=PE_LOCK)),
         ('C3_new_mexico_judgment', dict(claims=NM)),
         ('C4_value_date_and_price_13.11', dict(price=PRICE_NOW, T31=T_NOW, roll_days=8, cons_eps27=33.97))]
cfg = dict(BASE_CFG); res = {'steps': {}}
prev = None
for name, ch in steps:
    cfg.update(ch)
    lanes, sc = synth(cfg)
    row = {'base_today': sc['base']['today'], 'base_2031': sc['base']['ye'][2031], 'base_return_pct': sc['base']['return_pct'],
           'bear_today': sc['bear']['today'], 'bull_today': sc['bull']['today'],
           'lanes_base_today': {k: round(lanes[k]['base']['today'], 1) for k in lanes}}
    if prev:
        row['effect_base_today'] = round(sc['base']['today'] - prev['base_today'], 1)
        row['effect_base_2031'] = round(sc['base']['ye'][2031] - prev['base_2031'], 1)
        row['effect_bear_today'] = round(sc['bear']['today'] - prev['bear_today'], 1)
        row['effect_bull_today'] = round(sc['bull']['today'] - prev['bull_today'], 1)
    res['steps'][name] = row; prev = row
    print(name, json.dumps(row))

# ---------------- final ----------------
price, T = cfg['price'], cfg['T31']
res['config'] = {k: v for k, v in cfg.items()}
res['lanes'] = {k: {s: {'today': round(lanes[k][s]['today'], 1), 'end_2031': round(lanes[k][s]['ye'][2031], 1),
                        'eps27': round(lanes[k][s]['eps27'], 2), 'eps32': round(lanes[k][s]['eps32'], 2)} for s in lanes[k]}
                for k in lanes}
res['scenarios'] = sc
pw_today = sum(PROB[s] * sc[s]['today'] for s in PROB); pw31 = sum(PROB[s] * sc[s]['ye'][2031] for s in PROB)
res['probability_weighted'] = {'today': round(pw_today, 1), 'end_2031': round(pw31, 1), 'return_pct': round(ret(pw31, price, T), 2)}

# lenses (base). Claim FV at 2031 is subtracted from the lens values too (one-off cash, not in EPS).
pe_today = price / cfg['cons_eps27']
claim31 = sc['base']['claim_fv31_ps']
b = pe.run('cal', pe_corr('base'), beta=BETA_LOCK, pe_exit=PE_LOCK)
cal = pe.run('cal', pe_corr('base'), beta=BETA_LOCK, pe_exit=pe_today, margin_floor=b['path'][2027]['op_margin'])
eps32_floor = cal['path'][2032]['eps']
eps32_w = sum(W[k] * lanes[k]['base']['eps32'] for k in W)
eps27_w = sum(W[k] * lanes[k]['base']['eps27'] for k in W)
# judgement line between the two: only the evidenced (committed) part of the D&A wave, ~+1.5pt of revenue by 2030-31
# (D&A ~16-17% of revenue even with capex cut to ~$90B from 2028: deep3 dcf_result consensus_da_check) -> floor 1.5pt lower
cal_hard = pe.run('cal_h', pe_corr('base'), beta=BETA_LOCK, pe_exit=pe_today, margin_floor=b['path'][2027]['op_margin'] - 0.015)
res_hard_eps32 = cal_hard['path'][2032]['eps']
L = {'fundamental': sc['base']['ye'][2031],
     'calibrated_hard_part_only_judgement': pe_today * res_hard_eps32 - claim31,
     'calibrated': pe_today * eps32_floor - claim31,
     'calibrated_dep_wave_line': pe_today * eps32_w - claim31}
res['lenses'] = {'pe_today_held': round(pe_today, 2), 'eps32_margin_floored': round(eps32_floor, 2),
                 'margin_floor_2027': round(b['path'][2027]['op_margin'], 4), 'eps32_weighted_dep_wave': round(eps32_w, 2),
                 'eps27_weighted': round(eps27_w, 2), 'claim_fv31_ps': claim31}
for k, v31 in L.items():
    r = ret(v31, price, T)
    res['lenses'][k] = {'end_2031': round(v31, 1), 'return_pct': round(r, 2), 'band': band(r),
                        'ladder': {f'{x}%': round(price_at(v31, x, T), 0) for x in (12, INDEX, 6, 3)}}
# combined rule: an action needs BOTH lenses; the price where both are below x = the higher of the two lens prices
def both(a, bb):
    return {f'{x}%': max(res['lenses'][a]['ladder'][f'{x}%'], res['lenses'][bb]['ladder'][f'{x}%']) for x in (12, INDEX, 6, 3)}
def both_add(a, bb):     # add needs both >= 12%: the lower of the two 12% prices
    return min(res['lenses'][a]['ladder']['12%'], res['lenses'][bb]['ladder']['12%'])
res['combined'] = {
    'fundamental_and_calibrated': dict(both('fundamental', 'calibrated'), add_both=both_add('fundamental', 'calibrated')),
    'fundamental_and_dep_wave_line': dict(both('fundamental', 'calibrated_dep_wave_line'),
                                          add_both=both_add('fundamental', 'calibrated_dep_wave_line'))}
p_add = res['lenses']['fundamental']['ladder']['12%']
res['buy_test_at_fundamental_add_price'] = {'price': p_add, 'bear': round(ret(sc['bear']['ye'][2031], p_add, T), 1),
                                            'bull': round(ret(sc['bull']['ye'][2031], p_add, T), 1)}
res['buy_test_today'] = {'base': sc['base']['return_pct'], 'bull': sc['bull']['return_pct'], 'bear': sc['bear']['return_pct']}
# sensitivities on the final base
res['sens'] = {}
res['sens']['non_AG_legal_accruals_7.2B_as_debt_ps'] = round(-7.2 / (2556.24 + 61.3) * 1000, 2)
res['sens']['nm_base_25B_full_face_2028_ps'] = round(-(33.06 - 8.0) / (1 + sc['base']['ke_w']) ** 1.63 / 2617.5 * 1000, 2)
json.dump(res, open(os.path.join(OUT_DIR, 'model_result.json'), 'w'), indent=1, default=str)
print(json.dumps({k: res[k] for k in ('scenarios', 'probability_weighted', 'lenses', 'combined', 'buy_test_at_fundamental_add_price')},
                 indent=1, default=str))
