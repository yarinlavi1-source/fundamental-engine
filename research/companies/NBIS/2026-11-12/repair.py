"""12.11.2026 repair pass after the numbers check and the red team: bridge from the first-pass base (75.50) to the final base,
and the sensitivities of the final base. Run from deep/: python repair.py. Uses the val() of sens.py (first-pass params = params_pre_checks.json)."""
import json, copy
src = open('sens.py').read().split("out = {'one_at_a_time_on_the_11_11_base'")[0]
ns = {}; exec(src, ns)
val, PRE, nR, nT, E, lm = ns['val'], ns['NEW'], ns['nR'], ns['nT'], ns['E'], ns['lm']
D = ns['D']
FIN = json.load(open(D + 'params.json')); SUMM = json.load(open(D + 'side_summary.json'))
PRICE = FIN['price']; YRS = FIN['years_to_horizon']
SB_OLD = list(ns['SB']); SB_NEW = [0.06, 0.035, 0.03, 0.025, 0.025, 0.02, 0.02, 0.02]
fR = SUMM['_paths']['base_R']
def kw_for(r1, **over):
    k = dict(d_end26=11000.0, capex_guide={2027: 42500}, inflow_guide={2027: 18500}, cip0=max(5000 - (r1 / 0.44 - 11500 / 0.44), 0.0)); k.update(over); return k
def P_(base, rate=None, price=None):
    p = copy.deepcopy(base)
    if rate: p['debt_rate'] = rate
    p['price'] = price or PRICE
    return p
out = {'price': PRICE, 'bridge_after_checks': [], 'alone_on_first_pass': [], 'final_base_sensitivity': []}
def v_(P, R, SB, E_=None, kwo=None, **k):
    ns['SB'] = SB
    v = val(P, R, nT, E=E_ or E, kw=kw_for(R[1], **(kwo or {})), **k)
    ns['SB'] = SB_OLD
    v['price_for_12pct'] = round(v['y2031'] / 1.12 ** YRS, 2)
    c = lm.calibrated_lens(P, None) if False else None
    return v
def show(tag, name, v, prev=None):
    d = '' if prev is None else f" change {v['today'] - prev:+6.2f}"
    print(f"{tag} {name:88s} today {v['today']:7.2f}{d} %/yr {v['ret']:5.1f} e27 {v['y2027']:7.2f} e28 {v['y2028']:7.2f} e31 {v['y2031']:7.2f} | cal {v['cal_today']} {v['cal_ret']} | 12% at {v['price_for_12pct']:6.2f}")
# ---- sequential bridge from the first-pass base ----
R12 = list(fR)
steps = [('first-pass base at $130.25 (must be 75.50 / 8.2%)', P_(PRE, price=130.25), nR, SB_OLD),
         (f'price ${PRICE} (value unchanged)', P_(PRE), nR, SB_OLD),
         ('2027 revenue at the low end 12,000, later growth rates same (2028 21,551)', P_(PRE), R12, SB_OLD),
         ('SBC 3.5% (2027), 3%, 2.5%, 2.5%, 2%, 2% of revenue (was 2 / 1.7 / 1.5)', P_(PRE), R12, SB_NEW),
         ('debt at 7% (was 6%)  = FINAL base', P_(PRE, rate=0.07), R12, SB_NEW)]
prev = None
for name, P, R, SB in steps:
    v = v_(P, R, SB); show('SEQ', name, v, prev); out['bridge_after_checks'].append((name, v, None if prev is None else round(v['today'] - prev, 2))); prev = v['today']
fin = v_(FIN, R12, SB_NEW); show('CHK', 'final base from params.json (must equal result.json)', fin)
for name, P, R, SB in [('alone: 2027 at 12,000', P_(PRE), R12, SB_OLD), ('alone: SBC path', P_(PRE), nR, SB_NEW), ('alone: debt 7%', P_(PRE, rate=0.07), nR, SB_OLD)]:
    v = v_(P, R, SB); show('ONE', name, v, 75.50); out['alone_on_first_pass'].append((name, v, round(v['today'] - 75.50, 2)))
# ---- sensitivities of the final base ----
def add(name, R=None, SB=None, P=None, **k):
    v = v_(P or FIN, R or R12, SB or SB_NEW, **k); show('SEN', name, v, fin['today']); out['final_base_sensitivity'].append((name, v, round(v['today'] - fin['today'], 2)))
sc = lambda r27, r28: [3000, r27] + [x * r28 / R12[2] for x in R12[2:]]
al = lambda r27: [3000] + [x * r27 / R12[1] for x in R12[1:]]
add('final base')
add('2027 at 12,250 (first-pass choice), later rates same', R=al(12250))
add('2027 at the midpoint 12,500, later rates same', R=al(12500))
add('2027 at the top 13,000, later rates same', R=al(13000))
add('one more month of slip: 2027 11,200, 2028 21,000, later rates same', R=sc(11200, 21000))
add('MIDDLE CASE the 2026 two-month slip repeats, business intact: 2027 10,200, 2028 20,000', R=sc(10200, 20000))
add('2028 with no growth after Dec-2027 (17,500), later rates same', R=sc(12000, 17500))
add('2028 at the 2027 pace of additions (24,500), later rates same', R=sc(12000, 24500))
add('2028 as base, then the 11.11 revenue LEVELS one year early (2029 25.5B ... 2032 31.8B)', R=[3000, 12000, R12[2], 25500, 28000, 30000, 31800, 31800 * 1.06])
add('a quarter of the 2027 prebuild never earns', kwo=dict(cip_usable=0.75))
add('half of the 2027 prebuild never earns', kwo=dict(cip_usable=0.5))
add('none of the 2027 prebuild earns', kwo=dict(cip_usable=0.0))
add('adj. EBITDA 43% in 2027 and 48% in 2028 (was 46 / 50), later same', E_=[0.40, 0.43, 0.48] + E[3:])
add('EBITDA margin -5 points from 2028', E_=E[:2] + [x - 0.05 for x in E[2:]])
add('EBITDA margin +5 points from 2028', E_=E[:2] + [x + 0.05 for x in E[2:]])
add(f'$4B of share sales in 2027 at today\'s ${PRICE} ({4000 / PRICE:.1f}M shares)', extra_sh=4000 / PRICE, extra_cash=4000)
add('$4B of share sales in 2027 at $100 (40M shares)', extra_sh=40.0, extra_cash=4000)
add(f'$8.5B of share sales at today\'s ${PRICE} ({8500 / PRICE:.1f}M shares)', extra_sh=8500 / PRICE, extra_cash=8500)
add('$8.5B of share sales at $100 (85M shares)', extra_sh=85.0, extra_cash=8500)
add('30.9 receivable of ~3.3B never collected', extra_cash=-3340)
add('Q4 other operating outflows repeat Q3 (-365.6)', extra_cash=-365.6)
add('debt at 6% (first pass)', P=P_(FIN, rate=0.06))
add('debt at 7.5%', P=P_(FIN, rate=0.075))
add('SBC as in the first pass (2 / 1.7 / 1.5%)', SB=SB_OLD)
add('server life 4 years', L=4)
add('server life 6 years', L=6)
r = json.load(open(D + 'result.json')); sv = {k: r['scenarios'][k]['value_today'] for k in ('bear', 'base', 'bull')}; se = {k: r['scenarios'][k]['year_end_values']['2031'] for k in sv}
for lab, w in (('new odds 0.30/0.50/0.20', (0.30, 0.50, 0.20)), ('old odds 0.25/0.55/0.20', (0.25, 0.55, 0.20))):
    wt = sum(a * b for a, b in zip(w, (sv['bear'], sv['base'], sv['bull']))); we = sum(a * b for a, b in zip(w, (se['bear'], se['base'], se['bull'])))
    print(f'weighted, {lab}: today {wt:.2f} end-2031 {we:.2f} return {((we / PRICE) ** (1 / YRS) - 1) * 100:.1f}%'); out[lab] = dict(today=round(wt, 2), end_2031=round(we, 2))
b = r['scenarios']['base']; print('final base: dcf', b['dcf_today'], 'multiple', b['multiple_today'])
g = FIN['rf']; ke = FIN['rf'] + FIN['beta_mature'] * FIN['erp']; roic = FIN['roic_terminal']
print('justified steady-state P/E at ROIC %.2f, ke %.4f, g %.4f = %.1f' % (roic, ke, g, (1 - g / roic) / (ke - g)))
for nm in ('first-pass base at $130.25', ):
    pass
# old 11.11 model, price only
ob = json.load(open(D + 'result_baseline_at_today.json')); print('11.11 model at today price:', ob['scenarios']['base']['value_today'], ob['scenarios']['base']['annual_return_pct'], ob['two_lens']['calibrated']['annual_return_pct'])
print('calibrated price for 12%:', round(r['two_lens']['calibrated']['year_end_values']['2031'] / 1.12 ** YRS, 2), 'ours:', round(b['year_end_values']['2031'] / 1.12 ** YRS, 2), 'buy_price', r['buy_price'], 'parity', r['index_parity_price'])
print('two_lens', json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ('why', 'margin_used')} for k, v in r['two_lens'].items() if isinstance(v, dict)}), {k: v for k, v in r['two_lens'].items() if not isinstance(v, dict)})
for f in ('result_life4', 'result_life6'):
    q = json.load(open(D + f + '.json')); print(f, q['scenarios']['base']['value_today'], q['scenarios']['base']['annual_return_pct'], q['two_lens']['calibrated']['value_today'], q['two_lens']['calibrated']['annual_return_pct'])
json.dump(out, open(D + 'repair_output.json', 'w'), indent=1)
